# Contact sheets for reading the film by eye.
# usage: py -3.13 sheets.py <work_dir> sheets            -> sheets/sheet_NN.jpg (48 shots each)
#        py -3.13 sheets.py <work_dir> strip 12 40 133   -> sheets/strip_0012.jpg (8 frames across each shot)
import os, sys, csv
import cv2
from PIL import Image, ImageDraw

work, mode = sys.argv[1], sys.argv[2]
rows = list(csv.DictReader(open(os.path.join(work, "shots.csv"), encoding="utf8")))
os.makedirs(os.path.join(work, "sheets"), exist_ok=True)
TW, TH, COLS, PER = 320, 136, 6, 48

def mmss(t): t = float(t); return f"{int(t//60):02d}:{t%60:04.1f}"

if mode == "sheets":
    for n in range(0, len(rows), PER):
        chunk = rows[n:n + PER]
        nrows = (len(chunk) + COLS - 1) // COLS
        sheet = Image.new("RGB", (COLS * TW, nrows * (TH + 18)), (20, 20, 20))
        d = ImageDraw.Draw(sheet)
        for j, r in enumerate(chunk):
            x, y = (j % COLS) * TW, (j // COLS) * (TH + 18)
            p = os.path.join(work, "frames", f"shot_{int(r['shot']):04d}.jpg")
            if os.path.exists(p):
                sheet.paste(Image.open(p).resize((TW - 4, TH)), (x + 2, y))
            d.text((x + 3, y + TH + 2), f"#{r['shot']} {mmss(r['start'])} {float(r['dur']):.1f}s {r['move'][:22]}", fill=(235, 235, 120))
        sheet.save(os.path.join(work, "sheets", f"sheet_{n // PER:02d}.jpg"), quality=85)
    print("sheets:", (len(rows) + PER - 1) // PER)
else:
    cap = cv2.VideoCapture(os.path.join(work, "proxy.mp4"))
    fps = cap.get(cv2.CAP_PROP_FPS)
    for s in map(int, sys.argv[3:]):
        r = rows[s]; a, b = float(r["start"]) * fps, float(r["end"]) * fps - 1
        strip = Image.new("RGB", (4 * TW, 2 * (TH + 16)), (20, 20, 20)); d = ImageDraw.Draw(strip)
        for k in range(8):
            f = int(a + (b - a) * k / 7)
            cap.set(cv2.CAP_PROP_POS_FRAMES, f); ok, fr = cap.read()
            if ok:
                x, y = (k % 4) * TW, (k // 4) * (TH + 16)
                strip.paste(Image.fromarray(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB)).resize((TW - 4, TH)), (x + 2, y))
                d.text((x + 3, y + TH + 1), f"{mmss(f / fps)}", fill=(235, 235, 120))
        strip.save(os.path.join(work, "sheets", f"strip_{s:04d}.jpg"), quality=85)
    print("strips done")
