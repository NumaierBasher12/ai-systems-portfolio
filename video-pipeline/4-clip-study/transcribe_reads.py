"""Full timestamped transcripts for every clip in table `reads` (resume-safe, one clip at a time).

For each clip id without a row in `transcript_status`: download the audio only (yt-dlp, same flags as
study/audio_backfill.py), transcribe with faster-whisper large-v3-turbo on the GPU, store one row per
segment in `transcripts` (clip_id, start, end, text), delete the audio. Stops on a YouTube block
(429 / bot check); never retries around it. Log: C:/dev/clipping/db/transcribe.log
Run:  python -X utf8 transcribe_reads.py [max_clips]
"""
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import av

_open = av.open  # PyAV 19 dropped the metadata_errors kwarg that faster-whisper 1.2.1 still passes
av.open = lambda *a, **k: _open(*a, **{x: y for x, y in k.items() if x != "metadata_errors"})

from faster_whisper import WhisperModel  # noqa: E402

ROOT = Path(r"C:\dev\clipping")
DB = ROOT / "db" / "clips.db"
TMP = ROOT / "db" / "audio_tmp"
LOG = ROOT / "db" / "transcribe.log"
TMP.mkdir(exist_ok=True)
BLOCK_MARKERS = ("HTTP Error 429", "Sign in to confirm you", "Too Many Requests")
limit = int(sys.argv[1]) if len(sys.argv) > 1 else 10**9


def log(msg):
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(time.strftime("%H:%M:%S ") + msg + "\n")


db = sqlite3.connect(DB)
db.execute("CREATE TABLE IF NOT EXISTS transcripts(clip_id TEXT, start REAL, end REAL, text TEXT)")
db.execute("CREATE TABLE IF NOT EXISTS transcript_status(clip_id TEXT PRIMARY KEY, status TEXT, at TEXT)")
todo = [r[0] for r in db.execute(
    "SELECT clip_id FROM reads WHERE clip_id NOT IN (SELECT clip_id FROM transcript_status) ORDER BY views DESC")][:limit]
log(f"start, {len(todo)} clips to do")
model = WhisperModel("small", device="cpu", compute_type="int8")

for n, clip_id in enumerate(todo, 1):
    raw = TMP / f"{clip_id}.%(ext)s"
    run = subprocess.run(
        [sys.executable, "-X", "utf8", str(ROOT / "yt.py"), "--no-warnings", "--cookies", str(ROOT / "yt-cookies.txt"),
         "--js-runtimes", "node", "-f", "bestaudio", "--sleep-interval", "1", "--max-sleep-interval", "3",
         "-o", str(raw), f"https://www.youtube.com/shorts/{clip_id}"], capture_output=True, text=True, timeout=300)
    if any(m in run.stderr for m in BLOCK_MARKERS):
        log("blocked by YouTube, stopping (no evasion)")
        sys.exit("blocked by YouTube; stopped")
    source = next(TMP.glob(f"{clip_id}.*"), None)
    if source is None:
        db.execute("INSERT OR REPLACE INTO transcript_status VALUES(?,?,datetime('now'))", (clip_id, "download-failed"))
        db.commit()
        log(f"{n} {clip_id} download failed")
        continue
    try:
        segs, _info = model.transcribe(str(source), vad_filter=True, word_timestamps=False)
        rows = [(clip_id, s.start, s.end, s.text.strip()) for s in segs]
        db.executemany("INSERT INTO transcripts VALUES(?,?,?,?)", rows)
        db.execute("INSERT OR REPLACE INTO transcript_status VALUES(?,?,datetime('now'))",
                   (clip_id, "ok" if rows else "no-speech"))
        log(f"{n} {clip_id} {len(rows)} segments")
    except Exception as exc:  # keep going, record the reason
        db.execute("INSERT OR REPLACE INTO transcript_status VALUES(?,?,datetime('now'))", (clip_id, f"error: {exc}"[:200]))
        log(f"{n} {clip_id} error {exc}")
    db.commit()
    source.unlink(missing_ok=True)
log("done")
