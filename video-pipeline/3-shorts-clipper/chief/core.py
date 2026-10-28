"""Chief of staff core: paths, log, SQLite job table, run lock, STOP file, resource check, Telegram notify, health.
Plain stdlib + psutil, no Windows-only calls, so the same code runs on the new PC (maybe Linux)."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parent.parent
DATA, LOGS, CONFIG = ROOT / "data", ROOT / "logs", ROOT / "config"
DB_PATH = DATA / "assistant.db"
LOCK = DATA / "run.lock"
STOP = ROOT / "STOP"  # a hard block (403, CAPTCHA, ban) writes this; read why, then delete it by hand
MAX_TRIES = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY, role TEXT NOT NULL, input TEXT, status TEXT NOT NULL DEFAULT 'todo',
  result TEXT, tries INTEGER NOT NULL DEFAULT 0, created TEXT, started TEXT, finished TEXT);
CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY, started TEXT, finished TEXT, status TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS claude_usage(day TEXT PRIMARY KEY, calls INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS sources(id INTEGER PRIMARY KEY, kind TEXT, name TEXT, url TEXT, added TEXT, UNIQUE(kind, url));
CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY, source_id INTEGER, ext_id TEXT, title TEXT, url TEXT,
  published TEXT, views INTEGER, score REAL, seen TEXT, duration REAL, kind TEXT, UNIQUE(source_id, ext_id));
CREATE TABLE IF NOT EXISTS videos(id INTEGER PRIMARY KEY, platform TEXT, ext_id TEXT, title TEXT, duration REAL,
  path TEXT, status TEXT, added TEXT, UNIQUE(platform, ext_id));
CREATE TABLE IF NOT EXISTS transcripts(video_id INTEGER, start REAL, end REAL, text TEXT);
CREATE TABLE IF NOT EXISTS candidates(id INTEGER PRIMARY KEY, video_id INTEGER, start REAL, end REAL, score REAL,
  features TEXT, reason TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS clips(id INTEGER PRIMARY KEY, candidate_id INTEGER, path TEXT, status TEXT, made TEXT);
CREATE TABLE IF NOT EXISTS drafts(id INTEGER PRIMARY KEY, clip_id INTEGER, kind TEXT, text TEXT, flags TEXT,
  made_by TEXT, made TEXT, candidate_id INTEGER);
CREATE TABLE IF NOT EXISTS approvals(id INTEGER PRIMARY KEY, clip_id INTEGER, verdict TEXT, note TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS decisions(id INTEGER PRIMARY KEY, subject TEXT, subject_id INTEGER, choice TEXT, why TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS outcomes(id INTEGER PRIMARY KEY, clip_id INTEGER, platform TEXT, views INTEGER,
  retention REAL, comments INTEGER, measured TEXT);
"""


class StopRun(Exception):
    """Raised by a role on a hard block. The runner writes STOP and ends; we never try to get around a block."""


def now():
    return datetime.now().isoformat(timespec="seconds")


def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    LOGS.mkdir(exist_ok=True)
    with (LOGS / "chief.log").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def config(name="budget.json"):
    return json.loads((CONFIG / name).read_text(encoding="utf-8-sig"))


def db(path=None):
    path = path or DB_PATH
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    if "candidate_id" not in [r[1] for r in conn.execute("PRAGMA table_info(drafts)")]:  # migrate a database made before drafts had it
        conn.execute("ALTER TABLE drafts ADD COLUMN candidate_id INTEGER")
    return conn


# ---------- job table ----------
def enqueue(conn, role, input=""):
    cur = conn.execute("INSERT INTO jobs(role, input, created) VALUES (?,?,?)", (role, input, now()))
    conn.commit()
    return cur.lastrowid


def recover(conn):
    """A killed run leaves jobs 'doing'. Put them back in the queue (their try count stays)."""
    n = conn.execute("UPDATE jobs SET status='todo' WHERE status='doing'").rowcount
    conn.commit()
    return n


def claim(conn):
    row = conn.execute("SELECT * FROM jobs WHERE status='todo' AND tries<? ORDER BY id LIMIT 1", (MAX_TRIES,)).fetchone()
    if row:
        conn.execute("UPDATE jobs SET status='doing', tries=tries+1, started=? WHERE id=?", (now(), row["id"]))
        conn.commit()
    return row


def finish(conn, job_id, status, result=""):
    conn.execute("UPDATE jobs SET status=?, result=?, finished=? WHERE id=?", (status, str(result)[:2000], now(), job_id))
    conn.commit()


# ---------- one run at a time, and the STOP file ----------
def acquire_lock():
    """False when another run is alive. A lock left by a dead process is removed."""
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(str(LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            try:
                pid = int(LOCK.read_text() or 0)
            except (ValueError, OSError):
                pid = 0
            if pid and psutil.pid_exists(pid):
                return False
            LOCK.unlink(missing_ok=True)
    return False


def release_lock():
    LOCK.unlink(missing_ok=True)


def write_stop(reason):
    STOP.write_text(f"{now()} {reason}\n", encoding="utf-8")


# ---------- resources ----------
def vram_free_gb():
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=10)
        return int(r.stdout.split()[0]) / 1024
    except Exception:
        return 0.0


def resources_ok(cfg, need_gpu):
    """(ok, why). Heavy steps (Whisper, vision, LLM) wait until RAM and VRAM are free; disk below the floor stops everything."""
    ram = psutil.virtual_memory().available / 2**30
    disk = shutil.disk_usage(ROOT).free / 2**30
    vram = vram_free_gb() if need_gpu else 99.0
    if disk < cfg["min_free_disk_gb"]:
        return False, f"disk {disk:.0f} GB free, floor {cfg['min_free_disk_gb']}"
    if ram < cfg["min_free_ram_gb"]:
        return False, f"RAM {ram:.1f} GB free, need {cfg['min_free_ram_gb']}"
    if vram < cfg["min_free_vram_gb"]:
        return False, f"VRAM {vram:.1f} GB free, need {cfg['min_free_vram_gb']}"
    return True, "ok"


# ---------- Claude budget (counted per call, per day) ----------
def claude_used(conn):
    row = conn.execute("SELECT calls FROM claude_usage WHERE day=?", (str(date.today()),)).fetchone()
    return row["calls"] if row else 0


def claude_left(conn, cfg):
    return cfg["claude_calls_per_night"] - claude_used(conn)


def record_claude_call(conn):
    conn.execute("INSERT INTO claude_usage(day, calls) VALUES (?,1) ON CONFLICT(day) DO UPDATE SET calls=calls+1", (str(date.today()),))
    conn.commit()


def ask_claude(conn, cfg, prompt, model="haiku", timeout=300):
    """One headless Claude call on the user's login. Returns the text, or None when the nightly budget is spent or the call fails.
    The call is counted before it runs, so a crash never gives free calls."""
    if claude_left(conn, cfg) <= 0:
        return None
    record_claude_call(conn)
    try:
        r = subprocess.run(["claude", "-p", "--model", model, "--no-session-persistence"], input=prompt,
                           capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    except Exception as e:
        log(f"claude call failed: {type(e).__name__}")
        return None
    out = (r.stdout or "").strip()
    return out if r.returncode == 0 and out else None


# ---------- Telegram (reuses the reel bot's token and owner; never raises) ----------
def notify(text, cfg):
    if not cfg.get("notify_telegram"):
        return
    try:
        sys.path.insert(0, r"C:\dev\reels\bot")
        import reel_bot as rb  # ponytail: borrows the existing bot; copy its two small functions when the bot moves
        if not rb.TOKEN:
            rb.TOKEN = rb.TOKEN_FILE.read_text().strip()
        chat = rb.owner_id()
        if chat:
            rb.say(chat, text)
    except Exception as e:
        log(f"telegram notify failed: {type(e).__name__}")


# ---------- health ----------
def health(conn, cfg):
    ram = psutil.virtual_memory().available / 2**30
    disk = shutil.disk_usage(ROOT).free / 2**30
    jobs = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status")}
    last = conn.execute("SELECT started, status, note FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    lines = [
        f"disk {disk:.0f} GB free (floor {cfg['min_free_disk_gb']}), RAM {ram:.1f} GB free, VRAM {vram_free_gb():.1f} GB free",
        f"Claude calls today {claude_used(conn)} of {cfg['claude_calls_per_night']}",
        "jobs: " + (", ".join(f"{k} {v}" for k, v in sorted(jobs.items())) or "none"),
        "last run: " + (f"{last['started']} {last['status']} {last['note'] or ''}".strip() if last else "never"),
        "STOP file present: " + (STOP.read_text().strip() if STOP.exists() else "no"),
    ]
    return "\n".join(lines)
