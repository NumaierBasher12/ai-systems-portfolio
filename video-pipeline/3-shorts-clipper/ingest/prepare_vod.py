"""Prepare one long video for the clipper: download (video + audio apart, as the cutter expects), word-timed transcript,
loudness per second, then queue moments -> writer -> cutter for chief/run_all.py.
usage: python ingest/prepare_vod.py <youtube-url> [--cookies cookies.txt] [--model large-v3] [--top 3]
writes work/<video_id>/: <id>.mp4 (video only), <id>.webm|m4a (audio), audio16k.wav, transcript.json, rms.txt"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from chief import core  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("url"); ap.add_argument("--cookies"); ap.add_argument("--model", default="large-v3"); ap.add_argument("--top", type=int, default=3)
a = ap.parse_args()
ff = shutil.which("ffmpeg") or sys.exit("ffmpeg not on PATH")
ytdl = [sys.executable, str(core.ROOT / "chief" / "ytdl.py"), "--no-warnings", "--js-runtimes", "node", "--no-playlist"]
if a.cookies:
    ytdl += ["--cookies", a.cookies]

info = json.loads(subprocess.run(ytdl + ["-J", a.url], capture_output=True, text=True, check=True).stdout)
vid, title = info["id"], info.get("title", "")
folder = core.ROOT / "work" / vid
folder.mkdir(parents=True, exist_ok=True)
out = str(folder / f"{vid}.%(ext)s")
subprocess.run(ytdl + ["-f", "bv*[height<=1080][ext=mp4]/bv*[height<=1080]", "--remux-video", "mp4", "-o", out, a.url], check=True)
subprocess.run(ytdl + ["-f", "ba[ext=webm]/ba", "-o", out, a.url], check=True)
audio = next(p for ext in ("webm", "m4a", "opus") for p in folder.glob(f"*.{ext}"))
print("downloaded", vid, title, flush=True)

wav = folder / "audio16k.wav"
subprocess.run([ff, "-v", "error", "-y", "-i", str(audio), "-ac", "1", "-ar", "16000", str(wav)], check=True)
# one RMS level per second, in the text form moments/finder.py parses (pts_time: ... / ...RMS_level=...)
subprocess.run([ff, "-v", "error", "-i", str(wav), "-af",
                "asetnsamples=n=16000,astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level:file=rms.txt",
                "-f", "null", "-"], cwd=folder, check=True)

import glob, os, site  # noqa: E401,E402
for sp in site.getsitepackages():  # shortcut: cuBLAS/cuDNN DLLs come from pip's nvidia-* packages, not a CUDA toolkit
    for d in glob.glob(os.path.join(sp, "nvidia", "*", "bin")):
        os.add_dll_directory(d)
        os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]
from faster_whisper import WhisperModel  # noqa: E402  (imported late: the download steps do not need it)
try:
    m = WhisperModel(a.model, device="cuda", compute_type="float16")
except Exception as e:
    print("GPU failed, using CPU:", e, flush=True)
    m = WhisperModel(a.model, device="cpu", compute_type="int8")
import wave, numpy as np  # noqa: E401,E402  read the wav ourselves: PyAV 19 breaks faster-whisper's own decoder
with wave.open(str(wav)) as w:
    samples = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
segs, _ = m.transcribe(samples, word_timestamps=True, vad_filter=True, condition_on_previous_text=False)
segments = [{"start": s.start, "end": s.end, "text": s.text.strip(),
             "words": [{"w": w.word, "s": w.start, "e": w.end} for w in s.words]} for s in segs]
(folder / "transcript.json").write_text(json.dumps({"segments": segments}), encoding="utf-8")
print(f"transcript: {len(segments)} segments", flush=True)

conn = core.db()
job = {"folder": str(folder), "video_id": vid, "title": title, "top": 10}
core.enqueue(conn, "moments", json.dumps(job))
core.enqueue(conn, "writer", json.dumps({"video_id": vid, "top": a.top}))
core.enqueue(conn, "cutter", json.dumps({"video_id": vid, "top": a.top}))
print("queued moments -> writer -> cutter; now run: python chief/run_all.py", flush=True)
