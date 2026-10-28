"""Role 'cutter': turn the top candidates into 1080x1920 draft clips (never posted, never uploaded).
Layout A 'split' (streamer has a facecam box in config): stream scaled to full width near the top, facecam crop enlarged below it,
word-timed karaoke captions on the lower part, blurred copy of the stream as the background.
Layout B 'blur' (no facecam box): sharp 16:9 stream in the middle of a blurred background, captions below.
Audio is loudness-normalised. Source = <folder>/*.mp4 (video only) + *.webm/*.m4a (audio) as downloaded by the ingest step."""
import json
import re
import shutil
import subprocess
from pathlib import Path

from chief import core

WORDS_PER_CAPTION = 3
HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Cap,Arial,82,&H0000FFFF,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,7,2,2,50,50,{margin},1
Style: Pin,Arial,64,&H00000000,&H00000000,&H00FFFFFF,&H00FFFFFF,1,0,0,0,100,100,0,0,3,14,0,8,60,60,110,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""


def ass_time(seconds):
    cs = int(round(max(0.0, seconds) * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def captions(words, start, end, margin, label=""):
    """Karaoke ASS: groups of 3 words; the word being spoken turns yellow (SecondaryColour white -> PrimaryColour yellow).
    `label` = the pinned premise label (white box, top of the frame, whole clip), as in the F01 format of the Format Catalog."""
    ws = [w for w in words if start <= w["s"] < end]
    lines = [HEADER.format(margin=margin)]
    if label:
        lines.append(f"Dialogue: 1,{ass_time(0)},{ass_time(end - start)},Pin,,0,0,0,,{re.sub(r'[{}\\\\]', '', label).upper()}\n")
    for k in range(0, len(ws), WORDS_PER_CAPTION):
        group = ws[k:k + WORDS_PER_CAPTION]
        t0, t1 = group[0]["s"] - start, min(group[-1]["e"], end) - start
        text = ""
        for n, w in enumerate(group):
            nxt = group[n + 1]["s"] if n + 1 < len(group) else w["e"]
            cs = max(1, int(round((nxt - w["s"]) * 100)))
            text += "{\\k%d}%s " % (cs, re.sub(r"[{}\\]", "", w["w"].strip()).upper())
        lines.append(f"Dialogue: 0,{ass_time(t0)},{ass_time(t1 + 0.15)},Cap,,0,0,0,,{text.strip()}\n")
    return "".join(lines)


def build_filter(face, margin):
    if face:
        return (f"[0:v]split=3[bg][g][c];"
                f"[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=30:5[bgb];"
                f"[g]scale=1080:-2[gs];"
                f"[c]crop={face['w']}:{face['h']}:{face['x']}:{face['y']},scale=1080:-2[cs];"
                f"[bgb][cs]overlay=0:200[t1];[t1][gs]overlay=0:1050[t2];[t2]ass=clip.ass[v]")  # face on top, gameplay below (format F01)
    return (f"[0:v]split=2[bg][fg];"
            f"[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=30:5[bgb];"
            f"[fg]scale=1080:-2[fgs];[bgb][fgs]overlay=0:(H-h)/2-120[t];[t]ass=clip.ass[v]")


def cut_clip(folder, words, start, end, out_name, face, label=""):
    """Writes <folder>/clips/<out_name>.mp4. Returns the path. Runs ffmpeg from the clips folder so the filter can use a relative ass name."""
    folder = Path(folder).resolve()
    video = next(folder.glob("*.mp4"))
    audio = next((p for ext in ("*.webm", "*.m4a", "*.opus") for p in folder.glob(ext)), None)
    out_dir = folder / "clips"
    out_dir.mkdir(exist_ok=True)
    margin = 880 if face else 560  # split layout: captions sit on the seam between face and gameplay
    (out_dir / "clip.ass").write_text(captions(words, start, end, margin, label), encoding="utf-8")
    ff = shutil.which("ffmpeg") or "ffmpeg"
    dur = end - start
    cmd = [ff, "-y", "-loglevel", "error", "-ss", f"{start:.2f}", "-t", f"{dur:.2f}", "-i", str(video)]
    if audio:
        cmd += ["-ss", f"{start:.2f}", "-t", f"{dur:.2f}", "-i", str(audio)]
    cmd += ["-filter_complex", build_filter(face, margin), "-map", "[v]", "-map", "1:a:0" if audio else "0:a:0?",
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", f"{out_name}.mp4"]
    r = subprocess.run(cmd, cwd=out_dir, capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg: " + (r.stderr or "")[-300:])
    return out_dir / f"{out_name}.mp4"


def run_cutter(conn, cfg, job):
    """job.input = JSON {"video_id": ext id, "streamer": "jynxzi", "top": 3, "min_score": 0}. Draft clips only."""
    arg = json.loads(job["input"])
    v = conn.execute("SELECT id, path FROM videos WHERE ext_id=?", (arg["video_id"],)).fetchone()
    if not v:
        raise RuntimeError("video not registered; run the moments role first")
    folder = Path(v["path"])
    segs = json.loads((folder / "transcript.json").read_text(encoding="utf-8"))["segments"]
    words = [{"w": w["w"], "s": w["s"], "e": w["e"]} for s in segs for w in s["words"]]
    face = core.config("sources.json").get("facecam", {}).get(arg.get("streamer", ""))
    rows = conn.execute("SELECT id, start, end, score FROM candidates WHERE video_id=? AND score>=? ORDER BY score DESC LIMIT ?",
                        (v["id"], arg.get("min_score", 0), arg.get("top", 3))).fetchall()
    made = []
    for n, c in enumerate(rows, 1):
        name = f"{n:02d}_{int(c['start'])}"
        lab = conn.execute("SELECT text FROM drafts WHERE candidate_id=? AND kind='label' AND (flags IS NULL OR flags='') ORDER BY id DESC LIMIT 1",
                           (c["id"],)).fetchone()  # a label that failed the Voice Check is never burned in
        path = cut_clip(folder, words, c["start"], c["end"], name, face, lab["text"] if lab else "")
        conn.execute("INSERT INTO clips(candidate_id, path, status, made) VALUES (?,?,?,?)", (c["id"], str(path), "draft", core.now()))
        made.append(path.name)
    conn.commit()
    return f"{len(made)} draft clip(s): {', '.join(made)} (layout {'split' if face else 'blur'})"
