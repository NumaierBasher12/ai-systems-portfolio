"""Reel bot: send a reel link (Instagram, YouTube, TikTok) or a video file to your private Telegram bot and it saves the video on this PC.
Stdlib only. Runs on this PC only, while the PC is on (Telegram keeps messages sent meanwhile for up to 24 hours).
Plain long polling: no public web address needed.

Rules it follows (scraping-rules.md): one download at a time, at least 30 s between Instagram downloads, back off 10 min on a 429,
stop Instagram downloads and tell you on a login challenge. No proxies, no fingerprint tricks, no CAPTCHA solving.
Only the paired owner can use it. The bot token and the cookies are never written to the log.

    python reel_bot.py            run the bot
    python reel_bot.py --selftest check the link reading, no network
"""
import json
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
import os
import shutil
from pathlib import Path
REELS = Path(os.environ.get("REELS_ROOT", Path.home() / "reels"))

HERE = Path(__file__).parent
OUT = (REELS / "1-inbox")                      # the folder the reel analysis already reads from
COOKIES = (REELS / "cookies.txt")              # your Instagram session (private, never print it)
YTDLP = Path.home() / ".ai-video-editor" / "venv" / "Scripts" / "python.exe"   # run as `python -m yt_dlp`: the yt-dlp.exe launcher is blocked by Windows Application Control
FFMPEG = os.path.dirname(shutil.which("ffmpeg") or "")  # folder holding ffmpeg, from PATH
TOKEN_FILE, OWNER_FILE, PAIR_FILE, LOG_FILE = HERE / ".telegram-token", HERE / "owner.json", HERE / "pair-code.txt", HERE / "bot.log"
IG_GAP = 30          # seconds between Instagram downloads
IG_BACKOFF = 600     # seconds to pause Instagram after a 429
MAX_MB = 200
PREFIX = {"ig": "ig", "yt": "yt", "tt": "tt"}            # file names: ig_<id>.mp4, yt_<id>.mp4 (yt_ matches the earlier analysed reel)

TOKEN = ""


# ---------- reading links (the only place text from Telegram becomes a command argument, so it is strict) ----------
def parse_link(text: str):
    """First supported link in a message -> (kind, clean_url, id_or_None), or None. Query strings and tracking tokens are dropped."""
    for raw in re.findall(r"https?://[^\s<>\"']+", text):
        u = urllib.parse.urlparse(raw.rstrip(".,)"))
        host = (u.hostname or "").lower()
        if host == "instagram.com" or host.endswith(".instagram.com") or host == "instagr.am":
            m = re.match(r"^/share/(?:(?:reel|p)/)?([A-Za-z0-9_-]+)", u.path)  # first: a share id is NOT the post id, yt-dlp follows the redirect
            if m:
                return "ig", f"https://www.instagram.com/share/{'reel/' if '/share/reel/' in u.path else 'p/' if '/share/p/' in u.path else ''}{m.group(1)}/", None
            m = re.match(r"^/(?:[A-Za-z0-9_.]+/)?(reel|reels|p|tv)/([A-Za-z0-9_-]+)", u.path)
            if m:
                return "ig", f"https://www.instagram.com/{'reel' if m.group(1) == 'reels' else m.group(1)}/{m.group(2)}/", m.group(2)
        elif host == "youtu.be":
            m = re.match(r"^/([A-Za-z0-9_-]{6,})", u.path)
            if m:
                return "yt", f"https://youtu.be/{m.group(1)}", m.group(1)
        elif host == "youtube.com" or host.endswith(".youtube.com"):
            m = re.match(r"^/shorts/([A-Za-z0-9_-]{6,})", u.path)
            v = urllib.parse.parse_qs(u.query).get("v", [""])[0]
            vid = m.group(1) if m else (v if re.fullmatch(r"[A-Za-z0-9_-]{6,}", v) else None)
            if vid:
                return "yt", f"https://www.youtube.com/watch?v={vid}", vid
        elif host == "tiktok.com" or host.endswith(".tiktok.com"):
            if re.fullmatch(r"/[A-Za-z0-9_@./-]+", u.path) and len(u.path) > 3:
                return "tt", f"https://{host}{u.path}", None
    return None


def selftest():
    p = parse_link
    assert p("look https://www.instagram.com/reel/DeL9MAfINkC/?stkn=MTFp==") == ("ig", "https://www.instagram.com/reel/DeL9MAfINkC/", "DeL9MAfINkC")
    assert p("https://instagram.com/reels/AbC_123-x/")[1] == "https://www.instagram.com/reel/AbC_123-x/"
    assert p("https://www.instagram.com/someone.name/reel/XyZ12345/?igsh=abc")[2] == "XyZ12345"
    assert p("https://www.instagram.com/p/Cxyz987/")[0] == "ig"
    assert p("https://www.instagram.com/share/reel/BAbcDef12/")[1] == "https://www.instagram.com/share/reel/BAbcDef12/"
    assert p("https://youtu.be/Y2gLUMjhiBs?si=zzz") == ("yt", "https://youtu.be/Y2gLUMjhiBs", "Y2gLUMjhiBs")
    assert p("https://www.youtube.com/shorts/abcdEFG1234")[2] == "abcdEFG1234"
    assert p("https://www.youtube.com/watch?v=abcdEFG1234&t=5")[1].endswith("v=abcdEFG1234")
    assert p("https://vm.tiktok.com/ZMabc123/")[0] == "tt"
    assert p("https://evil.com/?u=https://www.instagram.com/reel/AAAAAAAA/") is None or p("https://evil.com/?u=x")[0] != "ig"
    assert p("https://www.instagram.com.evil.com/reel/AAAAAAAA/") is None
    assert p("https://www.instagram.com/reel/abc$(calc)/") is None or "$" not in p("https://www.instagram.com/reel/abc$(calc)/")[1]
    assert p("hello, no link") is None and p("https://example.com/video") is None
    print("selftest ok")


# ---------- plumbing ----------
def log(msg: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}".replace(TOKEN, "<token>") if TOKEN else f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


def tg(method: str, **params):
    req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/{method}", data=json.dumps(params).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=params.get("timeout", 0) + 20) as r:
        return json.load(r)["result"]


IDS_FILE = (REELS / "bot" / "chat_ids.json")  # message ids of this chat (yours + mine), so a new batch can clear the old ones


def track(mid: int) -> None:
    try:
        ids = json.loads(IDS_FILE.read_text()) if IDS_FILE.exists() else []
        IDS_FILE.write_text(json.dumps(ids[-500:] + [mid]))
    except Exception:
        pass


def clear_chat(chat: int) -> int:
    """Delete the tracked messages (Telegram allows bots to delete messages younger than 48 h). Returns how many went."""
    try:
        ids = json.loads(IDS_FILE.read_text()) if IDS_FILE.exists() else []
    except Exception:
        ids = []
    n = 0
    for m in ids:
        try:
            tg("deleteMessage", chat_id=chat, message_id=m)
            n += 1
        except Exception:
            pass
    IDS_FILE.write_text("[]")
    return n


def say(chat: int, text: str) -> None:
    try:
        track(tg("sendMessage", chat_id=chat, text=text, disable_web_page_preview=True)["message_id"])
    except Exception as e:  # a failed reply must never stop the bot
        log(f"reply failed: {type(e).__name__}")


def owner_id():
    return json.loads(OWNER_FILE.read_text())["id"] if OWNER_FILE.exists() else None


# ---------- downloading ----------
state = {"last_ig": 0.0, "ig_pause_until": 0.0, "ig_stopped": False}


def download(kind: str, url: str):
    """-> (ok, message). yt-dlp is called with an argument list (no shell), and the URL was rebuilt from a strict pattern."""
    cmd = [str(YTDLP), "-m", "yt_dlp", "--no-playlist", "--write-info-json", "--no-simulate", "--max-filesize", f"{MAX_MB}M", "--ffmpeg-location", FFMPEG,
           "-o", str(OUT / (PREFIX[kind] + "_%(id)s.%(ext)s")), "--print", "after_move:DONE\t%(filepath)s\t%(duration)s\t%(title).70s"]
    if kind == "ig" and COOKIES.exists():
        cmd += ["--cookies", str(COOKIES)]
    cmd.append(url)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, creationflags=0x08000000)  # no console window
    except subprocess.TimeoutExpired:
        return False, "That took more than 10 minutes, so I gave up."
    err = (r.stderr or "").lower()
    for line in r.stdout.splitlines():
        if line.startswith("DONE\t"):
            _, path, dur, title = (line.split("\t") + ["", "", ""])[:4]
            p = Path(path)
            mb = p.stat().st_size / 1e6 if p.exists() else 0
            try:
                secs = f"{float(dur):.0f} s"
            except ValueError:
                secs = "? s"
            return True, f"Saved: {p.name}\n{title} · {secs} · {mb:.1f} MB\nIn 1-inbox on the PC."
    if kind == "ig" and any(w in err for w in ("429", "rate-limit", "rate limit", "too many requests")):
        state["ig_pause_until"] = time.time() + IG_BACKOFF
        return False, f"Instagram asked me to slow down. I'm pausing Instagram until {datetime.now() + timedelta(seconds=IG_BACKOFF):%H:%M}. Send it again after that."
    if kind == "ig" and any(w in err for w in ("login required", "checkpoint", "challenge", "confirm you", "log in")):
        if "login required" not in err or not COOKIES.exists():
            state["ig_stopped"] = True
        return False, ("Instagram wants a login or a check. I've stopped Instagram downloads so I don't push it. "
                       "Open Instagram in Chrome, resolve anything it asks, export the cookies again, then restart the bot.")
    if kind == "ig" and ("http error 400" in err or "not available for the request" in err):  # CDN refusal, not a private post: back off like a 429
        state["ig_pause_until"] = time.time() + IG_BACKOFF
        return False, "Instagram's video server refused (HTTP 400). Pausing 10 minutes, then it can be retried."
    if "private" in err or "unavailable" in err or "removed" in err or "not available to" in err:
        return False, "That post is private, deleted or not available to your account."
    tail = " | ".join(l for l in (r.stderr or "").strip().splitlines()[-2:])[:300]
    log(f"download failed ({kind}): {tail}")
    return False, f"Download failed.\n{tail}"


def save_telegram_file(msg: dict):
    """A video sent as a file (Telegram bots can fetch up to 20 MB). -> (ok, message)"""
    media = msg.get("video") or (msg.get("document") if str(msg.get("document", {}).get("mime_type", "")).startswith("video/") else None)
    if not media:
        return None
    if media.get("file_size", 0) > 20 * 1024 * 1024:
        return False, "Telegram only lets bots fetch files up to 20 MB. Send the link instead."
    info = tg("getFile", file_id=media["file_id"])
    dest = OUT / f"tg_{media['file_unique_id']}.mp4"
    with urllib.request.urlopen(f"https://api.telegram.org/file/bot{TOKEN}/{info['file_path']}", timeout=120) as r, dest.open("wb") as f:
        f.write(r.read())
    return True, f"Saved: {dest.name} ({dest.stat().st_size / 1e6:.1f} MB) in 1-inbox."


# ---------- one message ----------
def handle(msg: dict) -> None:
    chat, uid = msg["chat"]["id"], msg.get("from", {}).get("id")
    text = (msg.get("text") or msg.get("caption") or "").strip()
    owner = owner_id()
    if owner is None:  # not paired yet: the one-time code on the PC is the only way in
        m = re.match(r"^/start\s+(\d{6})$", text)
        if m and PAIR_FILE.exists() and secrets.compare_digest(m.group(1), PAIR_FILE.read_text().strip()):
            OWNER_FILE.write_text(json.dumps({"id": uid}))
            PAIR_FILE.unlink(missing_ok=True)
            log("paired with the owner")
            say(chat, "Paired. Send me an Instagram, YouTube or TikTok link and I'll save the video on your PC. /help for the rest.")
        else:
            say(chat, "Not paired yet. Send /start followed by the 6-digit code in pair-code.txt on the PC.")
        return
    if uid != owner:
        log(f"ignored a message from another user ({uid})")
        return
    track(msg["message_id"])
    if text.startswith("/help") or text.startswith("/start"):
        say(chat, "Send an Instagram reel, YouTube or TikTok link, or a video file under 20 MB, and I save it in the inbox folder on your PC.\n"
                  "/status shows what I can do right now. Instagram downloads are spaced 30 s apart on purpose.")
        return
    if text.startswith("/reel") or text.startswith("/list"):  # the loop's analysis results (loop\report.py)
        sys.path.insert(0, str(REELS / "loop"))
        import report
        arg = text.split(maxsplit=1)[1].strip() if len(text.split()) > 1 else ""
        if text.startswith("/list"):
            ids = sorted((p.name[3:] for p in report.ANALYSED.glob("ig_*") if (p / "timeline.json").exists()),
                         key=lambda i: (report.ANALYSED / f"ig_{i}" / "timeline.json").stat().st_mtime, reverse=True)[:15]
            say(chat, ("Analysed reels (newest first):\n" + "\n".join(f"/reel {i}" for i in ids)) if ids else "Nothing analysed yet.")
        elif re.fullmatch(r"[A-Za-z0-9_-]{5,20}", arg):
            full = report.report(arg, full=True)
            for k in range(0, len(full), 3800):  # Telegram's limit is 4096 characters per message
                say(chat, full[k:k + 3800])
        else:
            say(chat, "Use /reel <id> (see /list).")
        return
    if re.match(r"/(go|skip|keep)\b", text):  # your yes/no per reel; Claude Code reads decisions.txt next session
        verb, args = text.split()[0][1:].replace("keep", "go"), text.split()[1:]
        try:
            nums = json.loads((REELS / "loop" / "digest.json").read_text())  # {"1": reel id, ...} from the latest digest message
        except Exception:
            nums = {}
        if args == ["all"]:
            args = list(nums)
        ids = [nums.get(a.strip(",")) or (a.strip(",") if re.fullmatch(r"[A-Za-z0-9_-]{8,20}", a.strip(",")) else None) for a in args]
        ids = [i for i in ids if i]
        if ids:
            with open((REELS / "decisions.txt"), "a", encoding="utf-8") as f:
                f.writelines(f"{datetime.now():%Y-%m-%d %H:%M} {verb} {i}\n" for i in ids)
            say(chat, f"{verb}: {len(ids)} reel(s) noted." + (" Tell Claude Code \"do the go list\" and it audits and installs them." if verb == "go" else ""))
        else:
            say(chat, "Use e.g. /skip 3 7  or  /go 1 2 4  or  /go all  (numbers from the latest list).")
        return
    if text.startswith("/status"):
        age = f"{(time.time() - COOKIES.stat().st_mtime) / 86400:.0f} days old" if COOKIES.exists() else "missing"
        paused = max(0, int(state["ig_pause_until"] - time.time()))
        say(chat, f"Instagram session file: {age}\nInstagram: {'STOPPED (restart the bot after fixing the login)' if state['ig_stopped'] else ('paused for %d min' % (paused // 60 + 1) if paused else 'ready')}\n"
                  f"Videos in 1-inbox: {len(list(OUT.glob('*.mp4')))}")
        return
    got = save_telegram_file(msg)
    if got:
        say(chat, got[1])
        return
    link = parse_link(text)
    if not link:
        say(chat, "I only understand Instagram, YouTube and TikTok links, or a video file. Send one of those.")
        return
    kind, url, vid = link
    if vid and (OUT / f"{PREFIX[kind]}_{vid}.mp4").exists():
        say(chat, f"Already saved: {PREFIX[kind]}_{vid}.mp4")
        return
    if kind == "ig":
        if state["ig_stopped"]:
            say(chat, "Instagram downloads are stopped until the login is fixed (see /status).")
            return
        if time.time() < state["ig_pause_until"]:
            say(chat, f"Instagram is paused until {datetime.fromtimestamp(state['ig_pause_until']):%H:%M}. Send it again after that.")
            return
        wait = IG_GAP - (time.time() - state["last_ig"])
        if wait > 0:
            if wait > 8:
                say(chat, f"Waiting {wait:.0f} s so I don't hammer Instagram...")
            time.sleep(wait)
    else:
        say(chat, "Downloading...")
    log(f"download {kind} {url}")
    ok, reply = download(kind, url)
    if kind == "ig":
        state["last_ig"] = time.time()
    say(chat, reply if ok else "✗ " + reply)
    log(f"result ok={ok}")


def main() -> None:
    global TOKEN
    if not TOKEN_FILE.exists() or len(TOKEN_FILE.read_text().strip()) < 30:
        sys.exit(f"No bot token yet. Paste the token from @BotFather into {TOKEN_FILE} and save.")
    TOKEN = TOKEN_FILE.read_text().strip()
    OUT.mkdir(parents=True, exist_ok=True)
    me = tg("getMe")
    log(f"bot @{me['username']} started")
    if owner_id() is None:
        PAIR_FILE.write_text(f"{secrets.randbelow(10**6):06d}")
        log(f"not paired: send /start <code> to @{me['username']} (code in {PAIR_FILE.name})")
    offset = None
    while True:
        try:
            for upd in tg("getUpdates", offset=offset, timeout=50, allowed_updates=["message"]):
                offset = upd["update_id"] + 1
                if "message" in upd:
                    try:
                        handle(upd["message"])
                    except Exception as e:  # one bad message must not kill the bot
                        log(f"handler error: {type(e).__name__}: {e}")
        except urllib.error.HTTPError as e:
            log(f"telegram http {e.code}")
            time.sleep(15 if e.code == 409 else 5)  # 409 = a second copy of the bot is running
        except Exception as e:
            log(f"network: {type(e).__name__}")
            time.sleep(5)


if __name__ == "__main__":
    selftest() if "--selftest" in sys.argv else main()
