r"""On-screen text for one reel, read at 4 frames per second with Windows' built-in OCR (no AI, no install).
  python screentext.py <reel id>      -> 3-analysed/ig_<id>/screen_text.json  [{"text", "from", "to"}, ...]
Every line of text is merged across frames, so a title that stays 3 s is one entry with from/to; text visible for
only 0.25 s still gets read. Frames are temporary (deleted afterwards).
"""
import json, re, shutil, subprocess, sys, tempfile
from difflib import SequenceMatcher
from pathlib import Path

HERE = Path(__file__).parent
ROOT = Path(r"C:\dev\reels")
FFMPEG = Path(r"C:\dev\documentary\ffmpeg\bin\ffmpeg.exe")
FPS = 4


def read(rid):
    video = ROOT / "1-inbox" / f"ig_{rid}.mp4"
    out = ROOT / "3-analysed" / f"ig_{rid}" / "screen_text.json"
    tmp = Path(tempfile.mkdtemp(prefix="ocr_"))
    try:
        subprocess.run([str(FFMPEG), "-y", "-loglevel", "error", "-i", str(video), "-vf", f"fps={FPS}", "-q:v", "3", str(tmp / "%05d.jpg")],
                       check=True, timeout=600, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # pythonw has no console handles
        res_file = tmp / "ocr.jsonl"  # results go through a file: powershell's stdout is lost when the parent is pythonw
        r = subprocess.run(["powershell", "-NoProfile", "-File", str(HERE / "ocr_dir.ps1"), "-Dir", str(tmp), "-Out", str(res_file)],
                           capture_output=True, text=True, encoding="utf-8", timeout=1800, stdin=subprocess.DEVNULL, creationflags=0x08000000)
        seen = []  # entries: {"text","key","from","to","n"}; OCR garbles the same line differently per frame, so merge near-duplicates
        if not res_file.exists() or res_file.stat().st_size == 0:  # say why instead of silently saving an empty result
            raise RuntimeError(f"OCR gave no output (exit {r.returncode}): {(r.stderr or '')[-200:]}")
        for ln in res_file.read_text(encoding="utf-8").splitlines():
            try:
                j = json.loads(ln)
            except ValueError:
                continue
            t = (int(Path(j["f"]).stem) - 1) / FPS
            for line in re.split(r"(?<=[.!?])\s+|\s{2,}", j["t"]):
                line = line.strip()[:140]
                key = re.sub(r"[^a-z0-9]", "", line.lower())
                if len(key) < 4 or sum(c.isalpha() for c in line) < 0.6 * len(line):
                    continue
                for e in seen:
                    if abs(len(e["key"]) - len(key)) <= max(4, len(key) // 4) and SequenceMatcher(None, e["key"], key).ratio() > 0.8:
                        e["to"], e["n"] = t, e["n"] + 1
                        if len(line) > len(e["text"]) and key.isascii():
                            e["text"], e["key"] = line, key
                        break
                else:
                    seen.append({"text": line, "key": key, "from": t, "to": t, "n": 1})
        res = sorted(({k: e[k] for k in ("text", "from", "to", "n")} for e in seen), key=lambda e: e["from"])
        out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        return res
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    for e in read(sys.argv[1]):
        print(f"{e['from']:6.2f}-{e['to']:6.2f}  {e['text']}")
