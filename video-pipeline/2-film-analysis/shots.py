# Shot table + per-frame camera motion and colour, from the 960px proxy.
# usage: py -3.13 shots.py <work_dir>
# writes: shots.csv (one row per shot), frames.npz (per-frame features), frames/shot_NNNN.jpg (mid frame)
import os, sys, csv
import numpy as np, cv2
from scenedetect import detect, AdaptiveDetector

work = sys.argv[1]
proxy = os.path.join(work, "proxy.mp4")
os.makedirs(os.path.join(work, "frames"), exist_ok=True)

# 1) cuts
scenes = detect(proxy, AdaptiveDetector(adaptive_threshold=3.0, min_scene_len=6))
cuts = [s[0].get_frames() for s in scenes] or [0]
print(f"cuts: {len(cuts)} shots", flush=True)

# 2) per-frame features
cap = cv2.VideoCapture(proxy)
fps = cap.get(cv2.CAP_PROP_FPS)
W, H = 240, 102
yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
rx, ry = xx - W / 2, yy - H / 2
rn = np.sqrt(rx**2 + ry**2) + 1e-6
feat, prev, i = [], None, 0
while True:
    ok, fr = cap.read()
    if not ok:
        break
    small = cv2.resize(fr, (W, H), interpolation=cv2.INTER_AREA)
    g = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    if prev is None:
        dx = dy = zoom = rot = resid = 0.0
    else:
        f = cv2.calcOpticalFlowFarneback(prev, g, None, 0.5, 3, 15, 3, 5, 1.2, 0)
        fx, fy = f[..., 0], f[..., 1]
        dx, dy = float(np.median(fx)), float(np.median(fy))
        zoom = float(np.median((fx * rx + fy * ry) / rn))   # >0 = expanding = push-in
        rot = float(np.median((fy * rx - fx * ry) / rn))    # rotation around the centre
        # what a single pan+zoom cannot explain: parallax (3D camera / layers) or moving subjects
        pred_x = dx + zoom * rx / rn
        pred_y = dy + zoom * ry / rn
        resid = float(np.median(np.hypot(fx - pred_x, fy - pred_y)))
    feat.append([i, g.mean(), hsv[..., 1].mean(), hsv[..., 2].std(), dx, dy, zoom, rot, resid])
    prev = g
    i += 1
cap.release()
feat = np.array(feat, np.float32)
np.savez_compressed(os.path.join(work, "frames.npz"), feat=feat, fps=fps,
                    cols="i,luma,sat,contrast,dx,dy,zoom,rot,resid")
n = len(feat)
print(f"frames: {n} at {fps} fps", flush=True)

# 3) per-shot table + mid frames + palette
def label(dx, dy, z, r, res, shake):
    moves = []
    if abs(z) > 0.04: moves.append("push-in" if z > 0 else "pull-out")
    if abs(dx) > 0.15: moves.append("pan-" + ("left" if dx > 0 else "right"))  # content moves right = camera pans left
    if abs(dy) > 0.15: moves.append("tilt-" + ("up" if dy > 0 else "down"))
    if abs(r) > 0.03: moves.append("roll")
    if shake > 0.25: moves.append("shake")
    if res > 0.12: moves.append("parallax/subject-motion")
    return "+".join(moves) or "static"

cap = cv2.VideoCapture(proxy)
bounds = cuts + [n]
rows = []
for k in range(len(cuts)):
    a, b = bounds[k], bounds[k + 1]
    seg = feat[a + 1:b] if b - a > 2 else feat[a:b]
    med = np.median(seg[:, 4:9], axis=0)
    shake = float(np.std(seg[:, 4]) + np.std(seg[:, 5])) if len(seg) > 3 else 0.0
    cap.set(cv2.CAP_PROP_POS_FRAMES, (a + b) // 2)
    ok, fr = cap.read()
    pal = ""
    if ok:
        cv2.imwrite(os.path.join(work, "frames", f"shot_{k:04d}.jpg"), fr, [cv2.IMWRITE_JPEG_QUALITY, 88])
        px = cv2.resize(fr, (96, 41)).reshape(-1, 3).astype(np.float32)
        _, lab, cen = cv2.kmeans(px, 5, None, (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0), 3, cv2.KMEANS_PP_CENTERS)
        order = np.argsort(-np.bincount(lab.ravel(), minlength=5))
        pal = " ".join("#%02x%02x%02x" % tuple(int(c) for c in cen[j][::-1]) for j in order)
    rows.append({"shot": k, "start": round(a / fps, 2), "end": round(b / fps, 2), "dur": round((b - a) / fps, 2),
                 "luma": round(float(seg[:, 1].mean()), 1), "sat": round(float(seg[:, 2].mean()), 1),
                 "dx": round(float(med[0]), 3), "dy": round(float(med[1]), 3), "zoom": round(float(med[2]), 4),
                 "rot": round(float(med[3]), 4), "resid": round(float(med[4]), 3), "shake": round(shake, 3),
                 "move": label(*med, shake), "palette": pal})
cap.release()
with open(os.path.join(work, "shots.csv"), "w", newline="", encoding="utf8") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader(); w.writerows(rows)
d = np.array([r["dur"] for r in rows])
print(f"DONE {len(rows)} shots, median {np.median(d):.2f}s, mean {d.mean():.2f}s", flush=True)
