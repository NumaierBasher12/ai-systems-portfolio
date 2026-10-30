r"""Reel loop: every interval_min minutes: collect saved links -> download -> analyse -> delete old videos.
All settings live in config.json (one place; change it, the next tick picks it up).
  python loop.py            run forever (started by Windows Startup: reel-loop.cmd)
  python loop.py --once     one tick, then exit
Rules: reads Instagram only; never deletes anything on Instagram (only the local video file, N days after its analysis);
RAM/VRAM are checked before every heavy step; one download / one analysis at a time. No proxies or tricks.
"""
import csv, json, os, re, subprocess, sys, time
from datetime import datetime
from pathlib import Path
import psutil

HERE = Path(__file__).parent
ROOT = Path(r"C:\dev\reels")
INBOX, ANALYSED, LEDGER, QUEUE = ROOT / "1-inbox", ROOT / "3-analysed", ROOT / "ledger.csv", ROOT / "queue" / "links.txt"
ANALYZE = Path(os.environ.get("ANALYZE_REEL", "analyze_reel.py"))  # path to the analyser script, see SETUP.md
COLLECT = ROOT / "collector" / "collect_links.py"
LOG, STATUS, LOCK = HERE / "loop.log", HERE / "status.json", HERE / "loop.lock"
FIELDS = ["id", "link", "collection", "status", "added", "note"]
NEW = []  # reels analysed in this tick, sent as one digest
sys.path.insert(0, str(ROOT / "bot"))
import reel_bot as rb  # same download() the Telegram bot uses (cookies, 429 back-off, login-challenge stop)
sys.path.insert(0, str(HERE))
import report  # Telegram text for one analysed reel
import screentext  # on-screen text, 4 fps


def notify(text):
    """Message the owner through the Telegram bot (bot -> your chat). Never raises; the loop must not stop for a failed message."""
    try:
        if not rb.TOKEN:
            rb.TOKEN = rb.TOKEN_FILE.read_text().strip()
        chat = rb.owner_id()
        if chat and cfg().get("notify_telegram", True):
            rb.say(chat, text)
    except Exception as e:
        log(f"telegram notify failed: {type(e).__name__}")


def cfg():
    return json.loads((HERE / "config.json").read_text(encoding="utf-8-sig"))


def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def vram_free_gb():
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=10)
        return int(r.stdout.split()[0]) / 1024
    except Exception:
        return 0.0


def resources_ok(c, need_gpu):
    ram = psutil.virtual_memory().available / 2**30
    vram = vram_free_gb() if need_gpu else 99
    need_ram = c["min_free_ram_gb"] if need_gpu else c.get("min_free_ram_gb_download", 1.0)  # a download is light; analysis loads Whisper
    ok = ram >= need_ram and vram >= c["min_free_vram_gb"]
    if not ok:
        log(f"waiting: free RAM {ram:.1f} GB (need {need_ram}), free VRAM {vram:.1f} GB (need {c['min_free_vram_gb']})")
    return ok


def read_ledger():
    with LEDGER.open(newline="", encoding="utf-8-sig") as f:
        return {r["id"]: r for r in csv.DictReader(f)}


def write_ledger(rows):
    with LEDGER.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        w.writerows(rows.values())


def collect(c):
    for folder in c["folders"]:
        try:
            r = subprocess.run([sys.executable, str(COLLECT), folder], capture_output=True, text=True, timeout=900)
            out = (r.stdout + r.stderr).strip().splitlines()
            stops = [l for l in out if l.startswith("STOP")]
            if stops:
                log(f"collect '{folder}' stopped: {stops[0][:200]}")
            else:
                log(f"collect '{folder}': " + next((l for l in reversed(out) if "found" in l), "done"))
        except Exception as e:
            log(f"collect '{folder}' failed: {type(e).__name__}")


def sync_files(led):
    """The bot may have downloaded files by hand: make the ledger match the disk."""
    for i, r in led.items():
        have = (INBOX / f"ig_{i}.mp4").exists()
        done = (ANALYSED / f"ig_{i}" / "timeline.json").exists()
        if r["status"] in ("queued", "failed") and have:
            r["status"] = "downloaded"
        if r["status"] in ("queued", "downloaded") and done:
            r["status"] = "analysed"


def fails(r):
    m = re.match(r"fail(\d+)", r["note"])  # notes written by this loop start with failN
    return int(m.group(1)) if m else 0


def download(c, led):
    n = login_fails = 0
    for i, r in led.items():
        if r["status"] != "queued" or fails(r) >= c["max_fail_attempts"] or n >= c["max_downloads_per_tick"]:
            continue
        if rb.state["ig_stopped"] or time.time() < rb.state["ig_pause_until"]:
            log("downloads paused (Instagram asked to slow down or wants a login)")
            break
        if not resources_ok(c, need_gpu=False):
            break
        ok, msg = rb.download("ig", r["link"])
        n += 1
        lines = msg.splitlines()
        if ok:
            r["status"] = "downloaded"
            r["note"] = ""
            log(f"downloaded {i}: {lines[1] if len(lines) > 1 else msg}")
            login_fails = 0
        else:
            r["note"] = lines[0][:80] if "refused" in lines[0] else f"fail{fails(r) + 1} {lines[0][:80]}"  # CDN refusals are not counted as failures
            log(f"download failed {i}: {r['note']}")
            if "wants a login" in lines[0]:
                login_fails += 1
                if login_fails < 2:  # one reel can need a login (private/restricted); two in a row = the account, stay stopped
                    rb.state["ig_stopped"] = False
        write_ledger(led)
        time.sleep(c["ig_gap_s"])


def close_chrome():
    """Ask Chrome to close (no /F: it saves its session, so Ctrl+Shift+T / 'continue where you left off' brings the tabs back).
    Only called between steps, when no collector is running, and only when free RAM is below the analysis threshold."""
    before = psutil.virtual_memory().available / 2**30
    subprocess.run(["taskkill", "/IM", "chrome.exe"], capture_output=True, timeout=60)
    time.sleep(20)
    after = psutil.virtual_memory().available / 2**30
    log(f"closed Chrome for RAM: free {before:.1f} -> {after:.1f} GB")
    notify(f"Closed Chrome to free RAM for analysis (free {before:.1f} -> {after:.1f} GB). Reopen it any time; Ctrl+Shift+T restores the tabs.")


def analyse(c, led):
    n = 0
    for i, r in led.items():
        if r["status"] != "downloaded" or n >= c["max_analyses_per_tick"]:
            continue
        mp4 = INBOX / f"ig_{i}.mp4"
        if not mp4.exists():
            continue
        for k in range(6):  # RAM is shared with Chrome and Ollama: re-check every 5 min (up to 30 min) before giving up this tick
            if resources_ok(c, need_gpu=True):
                break
            if k == 0 and c.get("close_chrome_when_low_ram") and psutil.virtual_memory().available / 2**30 < c["min_free_ram_gb"]:
                close_chrome()
                continue
            time.sleep(300)
        else:
            break
        t0 = time.time()
        p = subprocess.run([sys.executable, "-X", "utf8", str(ANALYZE), str(mp4), "--model", c["whisper_model"]],
                           capture_output=True, text=True, timeout=1800)
        n += 1
        if (ANALYSED / f"ig_{i}" / "timeline.json").exists():
            r["status"] = "analysed"
            log(f"analysed {i} in {time.time() - t0:.0f} s")
            try:  # on-screen text at 4 frames/s (Windows OCR); a failure here must not lose the analysis
                n_lines = len(screentext.read(i))
                log(f"screen text {i}: {n_lines} lines")
            except Exception as e:
                log(f"screen text failed {i}: {type(e).__name__}: {e}")
            NEW.append(i)  # reported together in one digest at the end of the tick
        else:
            tail = (p.stderr or p.stdout).strip().splitlines()
            r["note"] = "analysis failed: " + (tail[-1][:100] if tail else "?")
            log(f"analysis failed {i}: {r['note']}")
        write_ledger(led)


def cleanup(c, led):
    """Delete the LOCAL video N days after its analysis. The analysis folder, info.json and Instagram stay untouched."""
    cutoff = time.time() - c["delete_video_after_days"] * 86400
    for i, r in led.items():
        tl, mp4 = ANALYSED / f"ig_{i}" / "timeline.json", INBOX / f"ig_{i}.mp4"
        if r["status"] == "analysed" and tl.exists() and tl.stat().st_mtime < cutoff and mp4.exists():
            size = mp4.stat().st_size / 1e6
            mp4.unlink()
            r["note"] = f"video deleted {datetime.now():%Y-%m-%d}"
            log(f"deleted local video ig_{i}.mp4 ({size:.1f} MB), analysed {c['delete_video_after_days']}+ days ago")
    write_ledger(led)


def tick():
    c = cfg()
    log(f"tick start (folders: {c['folders']})")
    collect(c)
    led = read_ledger()
    sync_files(led)
    write_ledger(led)
    download(c, led)
    analyse(c, led)
    cleanup(c, led)
    QUEUE.write_text("".join(r["link"] + "\n" for r in led.values() if r["status"] == "queued"), encoding="utf-8")
    counts = {}
    for r in led.values():
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    STATUS.write_text(json.dumps({"last_tick": f"{datetime.now():%Y-%m-%d %H:%M:%S}", "counts": counts,
                                  "free_ram_gb": round(psutil.virtual_memory().available / 2**30, 1),
                                  "free_vram_gb": round(vram_free_gb(), 1)}, indent=1), encoding="utf-8")
    log(f"tick done: {counts}")
    digest(counts, led)


DIGESTED = HERE / "digested.json"
VAULT_DIGESTS = Path(r"C:\OBSIDIAN VAULT\Codebase and learning\Projects\Web Scraping\Reel digests")


def digest(counts, led):
    """Every analysed test-reels reel not yet reported goes in ONE digest with a full briefing each (Telegram caps a message at
    4096 characters, so it is sent as consecutive parts). Old chat cleared first; a permanent copy goes to the Obsidian vault."""
    done = set(json.loads(DIGESTED.read_text())) if DIGESTED.exists() else set()
    new = [i for i, r in led.items() if r["status"] == "analysed" and "test reels" in r["collection"] and i not in done]
    if not new:
        return
    for i in new:  # reels analysed before on-screen text existed
        if not (ANALYSED / f"ig_{i}" / "screen_text.json").exists() and (INBOX / f"ig_{i}.mp4").exists():
            try:
                screentext.read(i)
            except Exception as e:
                log(f"screen text failed {i}: {type(e).__name__}")
    blocks = [report.digest_block(i, n) for n, i in enumerate(new, 1)]
    head = (f"{len(new)} new reel(s) analysed ({counts.get('queued', 0)} still queued).\n"
            f"Reply /skip 3 7   or   /go 1 2 4   or   /go all\n")
    (HERE / "digest.json").write_text(json.dumps({str(n): i for n, i in enumerate(new, 1)}), encoding="utf-8")
    parts, cur = [], head
    for b in blocks:
        if len(cur) + len(b) > 3800:
            parts.append(cur)
            cur = ""
        cur += "\n" + b + "\n"
    parts.append(cur)
    try:  # permanent, searchable copy
        VAULT_DIGESTS.mkdir(parents=True, exist_ok=True)
        (VAULT_DIGESTS / f"{datetime.now():%Y-%m-%d %H%M} digest.md").write_text(
            f"# Reel digest {datetime.now():%Y-%m-%d %H:%M}\n\n" + "\n\n---\n\n".join(blocks), encoding="utf-8")
    except Exception as e:
        log(f"vault note failed: {type(e).__name__}")
    try:
        if not rb.TOKEN:
            rb.TOKEN = rb.TOKEN_FILE.read_text().strip()
        chat = rb.owner_id()
        if chat and cfg().get("notify_telegram", True):
            log(f"cleared {rb.clear_chat(chat)} old messages")
            for p in parts:
                rb.say(chat, p)
            DIGESTED.write_text(json.dumps(sorted(done | set(new))))
    except Exception as e:
        log(f"digest failed: {type(e).__name__}")


def main():
    if "--once" not in sys.argv:
        if LOCK.exists() and LOCK.read_text().strip().isdigit() and psutil.pid_exists(int(LOCK.read_text())):
            sys.exit("loop already running")
        LOCK.write_text(str(os.getpid()))
    while True:
        try:
            tick()
        except Exception as e:
            log(f"tick crashed: {type(e).__name__}: {e}")
        try:
            import reellog
            log(f"reel log: {reellog.build()} reels")
        except Exception as e:
            log(f"reel log failed: {type(e).__name__}: {e}")
        if "--once" in sys.argv:
            break
        time.sleep(cfg()["interval_min"] * 60)


if __name__ == "__main__":
    main()
