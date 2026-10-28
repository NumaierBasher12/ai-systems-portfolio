"""Role 'moments': find clip-worthy windows in a transcribed stream and rank them.
Input per video folder: transcript.json (segments with words {w,s,e}) and rms.txt (ffmpeg astats, one RMS level per second).
Steps: windows of 25-62 s that start and end on clean boundaries -> features (loudness spike, reaction words, hook, payoff, density,
dead air, filler) -> greedy pick with a minimum gap so the list is spread over the stream -> local LLM judge on the top 25 ->
final rank -> candidates table + a contact sheet of the top 10 so a human can check them by eye.
Optional signal: heatmap.json (YouTube 'most replayed') when the video has one."""
import json
import re
import shutil
import statistics
import subprocess
from pathlib import Path

from chief import core

MIN_LEN, MAX_LEN, TARGETS = 25.0, 62.0, (30.0, 45.0, 60.0)
MIN_GAP_S = 180  # centres of two picks are at least this far apart
CONTINUATION = re.compile(r"^\s*(and|but|so|because|which|then|or)\b", re.I)
HOOK = re.compile(r"\?|\b(no way|yo+|what the|oh my god|let'?s go|are you kidding|i can'?t|listen|watch this|dude|wait)\b|!", re.I)
REACTION = re.compile(r"\b(no way|yo+|crazy|insane|let'?s go+|oh my god|clutch|ace|wipe|headshot|unreal|sick|nuts|lmao|dead|scream\w*|cooked|"
                      r"what the|are you kidding|i can'?t|holy|bro what|nice|gg|ez|rage|tilt)\b", re.I)
FILLER = {"bro", "yeah", "okay", "ok", "like", "uh", "um", "so", "yo", "right", "just", "i", "you", "the", "to", "and", "it", "a"}
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
JUDGE_MODEL = "huihui_ai/qwen3-abliterated:4b"


def load(folder):
    folder = Path(folder)
    segs = json.loads((folder / "transcript.json").read_text(encoding="utf-8"))["segments"]
    rms, t = {}, None
    for line in (folder / "rms.txt").read_text().splitlines():
        if "pts_time:" in line:
            t = int(float(line.split("pts_time:")[1]))
        elif "RMS_level=" in line and t is not None:
            v = line.split("=")[1]
            rms[t] = -90.0 if v in ("-inf", "inf") else float(v)
    heat = []
    hp = folder / "heatmap.json"
    if hp.exists():
        heat = json.loads(hp.read_text()) or []
    return segs, rms, heat


def clamp(x):
    return max(0.0, min(1.0, x))


def windows(segs, rms, heat):
    med = statistics.median(rms.values())
    heat_max = max((h["value"] for h in heat), default=0) or 1
    out = []
    for i, first in enumerate(segs):
        if CONTINUATION.match(first["text"]):
            continue  # would start mid-thought
        for j in range(i, len(segs)):
            last = segs[j]
            length = last["end"] - first["start"]
            if length > MAX_LEN:
                break
            if length < MIN_LEN or not last["text"].rstrip().endswith((".", "?", "!")):
                continue  # ends must land on a finished sentence
            start, end = first["start"] - 0.15, last["end"] + 0.35
            levels = [rms.get(t, -90.0) for t in range(int(start), int(end) + 1)]
            before = [rms.get(t, -90.0) for t in range(max(0, int(start) - 40), int(start))] or [med]
            words = [w for s in segs[i:j + 1] for w in s["words"]]
            tokens = [re.sub(r"\W", "", w["w"].lower()) for w in words]
            text = " ".join(s["text"].strip() for s in segs[i:j + 1])
            opening = " ".join(s["text"] for s in segs[i:i + 2])
            ending = " ".join(s["text"] for s in segs[max(i, j - 1):j + 1])
            reactions = {m.lower() for m in REACTION.findall(text)}
            f = {
                "burst": clamp((max(levels) - statistics.median(before)) / 15),     # a sudden spike over the previous 40 s
                "peak": clamp((sorted(levels)[int(len(levels) * 0.9)] - med) / 18),  # sustained loudness over the stream median
                "reaction": clamp(len(reactions) / 4),
                "hook": clamp(len(HOOK.findall(opening)) / 2),
                "payoff": 1.0 if (REACTION.search(ending) or ending.rstrip().endswith("!")) else 0.0,
                "density": clamp(len(words) / length / 3.5),
                "length_fit": 1 - min(abs(length - t) for t in TARGETS) / 30,
                "dead_air": sum(1 for x in levels if x < med - 10) / len(levels),
                "filler": sum(1 for t in tokens if t in FILLER) / max(1, len(tokens)),
                "heat": clamp(max((h["value"] for h in heat if start <= h["start_time"] <= end), default=0) / heat_max) if heat else 0.0,
            }
            score = 100 * (0.24 * f["burst"] + 0.14 * f["peak"] + 0.18 * f["reaction"] + 0.10 * f["hook"] + 0.10 * f["payoff"]
                           + 0.10 * f["density"] + 0.06 * f["length_fit"] + 0.08 * f["heat"])
            score -= 25 * max(0.0, f["dead_air"] - 0.25) + 30 * max(0.0, f["filler"] - 0.55)
            out.append({"start": round(start, 2), "end": round(end, 2), "heur": round(score, 1),
                        "features": {k: round(v, 2) for k, v in f.items()}, "text": text})
    return out


def spread(cands, n):
    """Best first, but each pick must be at least MIN_GAP_S away (centre to centre) from every earlier pick."""
    chosen = []
    for c in sorted(cands, key=lambda c: c["heur"], reverse=True):
        mid = (c["start"] + c["end"]) / 2
        if all(abs(mid - (k["start"] + k["end"]) / 2) >= MIN_GAP_S for k in chosen):
            chosen.append(c)
        if len(chosen) == n:
            break
    return chosen


def judge(c):
    """Local model reads the moment's words. Returns (0-10, reason, hook_line) or None when Ollama is unavailable."""
    import urllib.request
    prompt = ("You judge moments cut from a long Rainbow Six Siege stream by the streamer Jynxzi for a 30-60 second vertical clip. "
              "Transcript of one candidate (speech recognition, may contain errors):\n\n" + c["text"][:2500] +
              "\n\nScore 0-10: does it have context, a build-up and a payoff, and can a stranger follow it without the rest of the stream? "
              "Funny, tense or surprising = higher. Pure shouting, filler, or a cut-off thought = lower. "
              'Reply with JSON only: {"score": number, "reason": "one short sentence", "hook_line": "the best opening line from the text"}')
    body = json.dumps({"model": JUDGE_MODEL, "messages": [{"role": "user", "content": prompt}], "stream": False,
                       "think": False, "format": "json", "options": {"temperature": 0.2}}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(OLLAMA_URL, data=body, headers={"Content-Type": "application/json"}), timeout=180) as r:
            d = json.loads(json.loads(r.read())["message"]["content"])
        return float(d["score"]), str(d.get("reason", ""))[:200], str(d.get("hook_line", ""))[:200]
    except Exception as e:
        core.log(f"judge failed: {type(e).__name__}")
        return None


def motion(video, start, end):
    """Mean frame-to-frame change over 8 sampled frames, 0-1 (the facecam corner is ignored). Static lobby, ban-phase, stats and replay
    screens score near 0; gameplay and scene changes score higher. None when frames could not be read."""
    import numpy as np
    ff = shutil.which("ffmpeg") or "ffmpeg"
    step, frames = (end - start) / 8, []
    for k in range(8):
        r = subprocess.run([ff, "-loglevel", "error", "-ss", f"{start + k * step:.2f}", "-i", str(video), "-frames:v", "1",
                            "-vf", "scale=160:90,format=gray", "-f", "rawvideo", "-"], capture_output=True, timeout=60)
        if len(r.stdout) == 160 * 90:
            frames.append(np.frombuffer(r.stdout, dtype=np.uint8).astype(np.float32).reshape(90, 160))
    if len(frames) < 4:
        return None
    diffs = []
    for a, b in zip(frames, frames[1:]):
        d = np.abs(a - b)
        d[48:, :55] = 0  # facecam corner
        diffs.append(d.mean())
    return round(clamp(float(np.mean(diffs)) / 30), 2)


def judge_batch(conn, cfg, picks):
    """One Claude Haiku call scores all picks (0-10 + a reason). Returns True when it worked; the caller falls back to the local model."""
    items = "\n\n".join(f"ID {i}: {c['text'][:600]}" for i, c in enumerate(picks))
    text = core.ask_claude(conn, cfg,
        "You judge moments cut from a long Rainbow Six Siege stream by the streamer Jynxzi for 30-60 s vertical clips. Each moment is a speech-recognition "
        "transcript (errors possible). Score each 0-10 for: self-contained (a stranger can follow), a build-up and a payoff, funny/tense/surprising. "
        "Talk about menus, stats websites, loadouts or admin chatter scores low even if loud. Spread your scores: use the whole range. "
        'Reply JSON only: {"scores":[{"id":0,"score":7,"reason":"one short concrete sentence about THIS moment"}]}\n\n' + items, model="haiku")
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        got = {int(s["id"]): s for s in json.loads(m.group(0))["scores"]}
    except Exception:
        return False
    for i, c in enumerate(picks):
        if i in got:
            c["llm"], c["reason"] = float(got[i]["score"]), str(got[i].get("reason", ""))[:200]
    return all(c.get("llm") is not None for c in picks)


def contact_sheet(video, picks, out_path):
    """3 frames per pick (start+2 s, middle, end-2 s) in one image, one row per pick."""
    from PIL import Image, ImageDraw
    ff = shutil.which("ffmpeg") or "ffmpeg"
    tw, th = 320, 180
    sheet = Image.new("RGB", (3 * tw, len(picks) * (th + 18)), "black")
    draw = ImageDraw.Draw(sheet)
    tmp = out_path.parent / "_frame.jpg"
    for row, c in enumerate(picks):
        draw.text((4, row * (th + 18) + 3), f"#{row + 1}  {c['start']:.0f}s-{c['end']:.0f}s  final {c['final']}  llm {c.get('llm')}  heur {c['heur']}", fill="white")
        for col, t in enumerate((c["start"] + 2, (c["start"] + c["end"]) / 2, c["end"] - 2)):
            subprocess.run([ff, "-y", "-loglevel", "error", "-ss", str(t), "-i", str(video), "-frames:v", "1", "-vf", f"scale={tw}:{th}", str(tmp)],
                           check=False, capture_output=True, timeout=120)
            if tmp.exists():
                sheet.paste(Image.open(tmp).convert("RGB"), (col * tw, row * (th + 18) + 18))
                tmp.unlink()
    sheet.save(out_path, quality=85)


def run_moments(conn, cfg, job):
    """job.input = JSON {"folder": ..., "video_id": ..., "title": ..., "top": 10}. Returns a summary line."""
    arg = json.loads(job["input"])
    folder = Path(arg["folder"])
    segs, rms, heat = load(folder)
    cands = windows(segs, rms, heat)
    picks = spread(cands, 40)
    video = next(folder.glob("*.mp4"), None)
    for c in picks:  # what is on screen: static menus and lobbies are pushed down (x0.6)
        c["motion"] = motion(video, c["start"], c["end"]) if video else None
        c["pre"] = c["heur"] * (0.6 if c["motion"] is not None and c["motion"] < 0.12 else 1.0)
        c["features"]["motion"] = c["motion"]
    picks = sorted(picks, key=lambda c: c["pre"], reverse=True)[:25]
    for c in picks:
        c.update(llm=None, reason="", hook_line="")
    if not judge_batch(conn, cfg, picks):  # Claude unavailable or budget spent: the local model judges one by one
        core.log("moments: Claude judge unavailable, using the local model")
        for c in picks:
            j = judge(c)
            c["llm"], c["reason"], c["hook_line"] = (j if j else (None, "", ""))
    for c in picks:
        c["final"] = round(0.5 * c["pre"] / 10 + 0.5 * c["llm"], 2) if c["llm"] is not None else round(c["pre"] / 10, 2)
    picks.sort(key=lambda c: c["final"], reverse=True)
    top = picks[: arg.get("top", 10)]
    conn.execute("INSERT OR IGNORE INTO videos(platform, ext_id, title, duration, path, status, added) VALUES ('youtube',?,?,?,?,?,?)",
                 (arg["video_id"], arg.get("title"), segs[-1]["end"], str(folder), "moments", core.now()))
    vid = conn.execute("SELECT id FROM videos WHERE platform='youtube' AND ext_id=?", (arg["video_id"],)).fetchone()[0]
    conn.execute("DELETE FROM candidates WHERE video_id=?", (vid,))
    for c in top:
        conn.execute("INSERT INTO candidates(video_id, start, end, score, features, reason, text) VALUES (?,?,?,?,?,?,?)",
                     (vid, c["start"], c["end"], c["final"], json.dumps({**c["features"], "heur": c["heur"], "llm": c["llm"], "hook_line": c["hook_line"]}),
                      c["reason"], c["text"]))
    conn.commit()
    if video:
        (folder / "sheets").mkdir(exist_ok=True)
        contact_sheet(video, top, folder / "sheets" / "top10.jpg")
    return f"{len(cands)} windows, {len(picks)} judged, top {len(top)} saved; sheet {(folder / 'sheets' / 'top10.jpg') if video else 'skipped (no video)'}"
