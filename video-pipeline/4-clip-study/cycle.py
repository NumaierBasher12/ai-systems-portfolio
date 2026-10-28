"""Resumable study cycle: for every queued short -> download (+top comments) -> frame analysis -> framing
measurements -> contact sheet -> delete the video. One clip at a time, polite delays, stops on a block.
Usage: python cycle.py            (reads queue.tsv; appends to features.jsonl; skips ids in done.txt)
queue.tsv rows: id|tier|views|channel|title
Outputs per clip in data/<id>/: meta.json, comments.json (text+likes only, no usernames), features.json,
sheet.jpg, analysis/ (frames, timeline.json, timeline.md). The mp4 is deleted once analysed.
"""
import json
import os
import random
import shutil
import subprocess
import sys
import time
import wave
from pathlib import Path

import cv2
import numpy as np
import psutil

ROOT = Path(r"C:\dev\clipping")
STUDY = ROOT / "study"
DATA = STUDY / "data"
REELS_OUT = Path(r"C:\dev\reels\3-analysed")  # analyze_reel.py writes here; we move the result
ANALYZE = r"C:\Users\yusha\OneDrive\Desktop\Web Scraping\analyze_reel.py"
FFMPEG = r"C:\Users\yusha\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin\ffmpeg.exe"
MIN_FREE_RAM_GB = 2.5
MAX_COMMENTS = 60
FACE = cv2.CascadeClassifier(str(STUDY / "haarcascade_frontalface_default.xml"))  # this OpenCV build ships no cascades; file fetched from opencv/opencv
BLOCK_MARKERS = ("HTTP Error 429", "Sign in to confirm", "Too Many Requests")

ANALYZE_WRAPPER = (
    "import sys, runpy, av\n"
    "_o = av.open\n"
    "av.open = lambda *a, **k: _o(*a, **{x: y for x, y in k.items() if x != 'metadata_errors'})\n"  # PyAV 19 dropped this kwarg
    "sys.argv = ['analyze_reel.py', sys.argv[1], '--model', 'large-v3-turbo']\n"
    f"runpy.run_path(r'{ANALYZE}', run_name='__main__')\n"
)


def wait_for_ram():
    for _ in range(40):
        if psutil.virtual_memory().available / 1e9 >= MIN_FREE_RAM_GB:
            return
        time.sleep(30)


def download(clip_id, folder):
    cmd = [sys.executable, "-X", "utf8", str(ROOT / "yt.py"), "--no-warnings", "--cookies", str(ROOT / "yt-cookies.txt"),
           "--js-runtimes", "node", "-f", "bv*[height<=720]+ba/b", "--merge-output-format", "mp4",
           "--write-info-json", "--write-comments", "--match-filter", "duration<=150",
           "--extractor-args", f"youtube:comment_sort=top;max_comments={MAX_COMMENTS},{MAX_COMMENTS},0,0",
           "--sleep-interval", "0.5", "--max-sleep-interval", "1",
           "-o", str(folder / "%(id)s.%(ext)s"), f"https://www.youtube.com/shorts/{clip_id}"]
    run = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if "confirm your age" in run.stderr:
        raise RuntimeError("age-restricted video, skipped")  # one gated clip is not a bot block
    if any(m in run.stderr + run.stdout for m in BLOCK_MARKERS):
        (STUDY / "STOP").write_text(run.stderr[-500:])
        sys.exit("blocked by YouTube; stopped (see study/STOP)")
    return run.returncode == 0 and (folder / f"{clip_id}.mp4").exists()


def trim_meta(clip_id, folder, row):
    info = json.loads((folder / f"{clip_id}.info.json").read_text(encoding="utf-8"))
    meta = {k: info.get(k) for k in ("id", "title", "channel", "duration", "view_count", "like_count", "comment_count",
                                     "upload_date", "width", "height", "fps", "heatmap", "tags")}
    meta["description"] = (info.get("description") or "")[:300]
    meta["tier"], meta["queue_views"] = row["tier"], row["views"]
    (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    comments = [{"text": c.get("text", ""), "likes": c.get("like_count", 0)} for c in (info.get("comments") or [])]  # no usernames kept
    (folder / "comments.json").write_text(json.dumps(comments, ensure_ascii=False), encoding="utf-8")
    (folder / f"{clip_id}.info.json").unlink()
    return meta


def analyse(clip_id, folder):
    run = subprocess.run([sys.executable, "-X", "utf8", "-c", ANALYZE_WRAPPER, str(folder / f"{clip_id}.mp4")],
                         capture_output=True, text=True, timeout=1200)
    produced = REELS_OUT / clip_id
    if run.returncode != 0 or not produced.exists():
        raise RuntimeError("analysis failed: " + run.stderr[-300:])
    target = folder / "analysis"
    if target.exists():
        shutil.rmtree(target)
    shutil.move(str(produced), str(target))
    return target


def sharpness(gray):
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def framing(frames_dir):
    """Layout measurements from up to 12 evenly spaced 1 fps frames (approximate, Haar face detector)."""
    files = sorted(frames_dir.glob("[0-9][0-9][0-9][0-9].jpg"))
    pick = [files[int(i)] for i in np.linspace(0, len(files) - 1, min(12, len(files)))]
    blurred, split_rows, face_area, face_y, face_seen = 0, [], [], [], 0
    for path in pick:
        img = cv2.imread(str(path))
        if img is None:
            continue
        height, width = img.shape[:2]
        scale = 360 / width
        gray = cv2.cvtColor(cv2.resize(img, (360, int(height * scale))), cv2.COLOR_BGR2GRAY)
        h = gray.shape[0]
        mid = sharpness(gray[int(h * .4):int(h * .6)]) + 1e-6
        if sharpness(gray[:h // 8]) < .3 * mid and sharpness(gray[-h // 8:]) < .3 * mid:
            blurred += 1  # sharp video in the middle, soft bars above and below
        grad = np.abs(np.diff(gray.astype(np.float32), axis=0)).mean(axis=1)
        band = grad[int(h * .2):int(h * .8)]
        if band.max() > 6 * (np.median(grad) + 1e-6):
            split_rows.append((int(h * .2) + int(band.argmax())) / h)
        faces = FACE.detectMultiScale(gray, 1.1, 5, minSize=(30, 30))
        if len(faces):
            x, y, fw, fh = max(faces, key=lambda f: f[2] * f[3])
            face_seen += 1
            face_area.append(fw * fh / (gray.shape[0] * gray.shape[1]))
            face_y.append((y + fh / 2) / h)
    n = max(len(pick), 1)
    return {"blurred_bars_share": round(blurred / n, 2),
            "split_screen_share": round(len(split_rows) / n, 2),
            "split_y_median": round(float(np.median(split_rows)), 2) if split_rows else None,
            "face_seen_share": round(face_seen / n, 2),
            "face_area_pct": round(100 * float(np.mean(face_area)), 1) if face_area else 0.0,
            "face_y_mean": round(float(np.mean(face_y)), 2) if face_y else None}


def speech_features(timeline):
    words = timeline.get("words") or []
    secs = max(timeline.get("seconds", 1), 1)
    cov = timeline.get("coverage", {})
    first = words[0].get("start") if words else None
    text = " ".join(w.get("word", "").strip() for w in words)
    cuts = timeline.get("cuts") or []  # list of cut times in seconds
    return {"cuts": len(cuts), "cut_times": cuts,
            "voiced_share": round(cov.get("voiced_seconds", 0) / secs, 2),
            "words_per_sec": round(len(words) / secs, 2), "first_word_s": first,
            "first_words": " ".join(text.split()[:14]), "last_words": " ".join(text.split()[-10:])}


def contact_sheet(analysis, out):
    subprocess.run([FFMPEG, "-y", "-v", "error", "-framerate", "1", "-i", str(analysis / "frames" / "%04d.jpg"),
                    "-vf", "scale=240:-2,tile=8x4", "-frames:v", "1", str(out)], check=False)


def audio_features(wav_path):
    """Loudness and brightness of the soundtrack from the 16 kHz mono wav (per-second dB series + summary)."""
    with wave.open(str(wav_path)) as w:
        rate = w.getframerate()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    secs = len(data) // rate
    frames = data[:secs * rate].reshape(secs, rate)
    db = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1) + 1e-12) + 1e-9)
    spec = np.abs(np.fft.rfft(frames, axis=1))
    centroid = (spec * np.fft.rfftfreq(rate, 1 / rate)).sum(axis=1) / (spec.sum(axis=1) + 1e-9)  # higher = brighter / screamier
    median = float(np.median(db))
    summary = {"audio_db_median": round(median, 1), "audio_db_max": round(float(db.max()), 1),
               "audio_peak_seconds": int((db > median + 8).sum()),  # seconds 8 dB louder than the clip's typical level
               "audio_first3_vs_median": round(float(db[:3].mean() - median), 1),
               "audio_silence_share": round(float((db < -50).mean()), 2),
               "audio_dynamic_range_db": round(float(np.percentile(db, 95) - np.percentile(db, 10)), 1),
               "audio_centroid_hz_mean": int(centroid.mean()), "audio_loudest_second": int(db.argmax())}
    return summary, [round(float(x), 1) for x in db]


def process(row):
    clip_id = row["id"]
    folder = DATA / clip_id
    folder.mkdir(parents=True, exist_ok=True)
    wait_for_ram()
    if not download(clip_id, folder):
        raise RuntimeError("download failed or filtered (longer than 150 s)")
    meta = trim_meta(clip_id, folder, row)
    analysis = analyse(clip_id, folder)
    timeline = json.loads((analysis / "timeline.json").read_text(encoding="utf-8"))
    features = {"id": clip_id, "tier": row["tier"], "views": meta["view_count"], "seconds": meta["duration"],
                "w": meta["width"], "h": meta["height"], **speech_features(timeline), **framing(analysis / "frames")}
    audio_summary, audio_series = audio_features(analysis / "audio.wav")
    features.update(audio_summary)
    (folder / "audio.json").write_text(json.dumps(audio_series), encoding="utf-8")
    (folder / "features.json").write_text(json.dumps(features), encoding="utf-8")
    contact_sheet(analysis, folder / "sheet.jpg")
    (folder / f"{clip_id}.mp4").unlink(missing_ok=True)  # video no longer needed; frames + sheet stay
    (analysis / "audio.wav").unlink(missing_ok=True)
    with open(STUDY / "features.jsonl", "a", encoding="utf-8") as out:
        out.write(json.dumps(features) + "\n")


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    done = set((STUDY / "done.txt").read_text().split()) if (STUDY / "done.txt").exists() else set()
    rows = []
    # CLIP_QUEUE lets a second worker run a different queue (streamer study) next to the Jynxzi one
    for line in (STUDY / os.environ.get("CLIP_QUEUE", "queue.tsv")).read_text(encoding="utf-8").splitlines():
        parts = line.split("|", 4)
        if len(parts) == 5 and parts[0] not in done:
            rows.append(dict(id=parts[0], tier=parts[1], views=parts[2], channel=parts[3], title=parts[4]))
    if os.environ.get("CLIP_REVERSE"):  # second worker walks the queue from the end; both skip what the other finished
        rows.reverse()
    print(f"{len(rows)} clips to do", flush=True)
    for n, row in enumerate(rows, 1):
        if (STUDY / "STOP").exists():
            sys.exit("STOP file present")
        if row["id"] in set((STUDY / "done.txt").read_text().split()):
            continue
        try:
            process(row)
            with open(STUDY / "done.txt", "a") as f:
                f.write(row["id"] + "\n")
            print(f"[{n}/{len(rows)}] ok {row['id']} {row['tier']}", flush=True)
        except Exception as err:  # keep the cycle alive; log and move on
            with open(STUDY / "failed.tsv", "a", encoding="utf-8") as f:
                f.write(f"{row['id']}\t{str(err)[:200]!r}\n")
            print(f"[{n}/{len(rows)}] FAIL {row['id']} {str(err)[:100]}", flush=True)
        time.sleep(random.uniform(0.3, 0.8))  # cut from 1.5-3 s (user, 2026-10-22); STOP on 429 still applies


if __name__ == "__main__":
    main()
