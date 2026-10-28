# Who speaks when, and how each voice is treated (phone filter, pitch, pace, level).
# usage: <voicestudio python> voices.py <work_dir>
# reads stems/htdemucs/audio44k/vocals.wav + transcript.json, model in ../models/ecapa; writes voices.json
import os, sys, json
import numpy as np, soundfile as sf, torch
from scipy import signal
from scipy.cluster.hierarchy import linkage, fcluster
from speechbrain.inference.speaker import EncoderClassifier
from speechbrain.utils.fetching import LocalStrategy

work = sys.argv[1]
mdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "ecapa")
enc = EncoderClassifier.from_hparams(source=mdir, savedir=mdir, run_opts={"device": "cpu"},
                                     local_strategy=LocalStrategy.NO_LINK)
voc, sr = sf.read(os.path.join(work, "stems", "htdemucs", "audio44k", "vocals.wav"))
voc = voc.mean(1)
v16 = signal.resample_poly(voc, 160, 441).astype(np.float32)
tr = json.load(open(os.path.join(work, "transcript.json"), encoding="utf8"))

def f0_median(x, fs):
    fl, out = int(0.04 * fs), []
    thr = np.sqrt(np.mean(x ** 2)) * 0.7
    for s in range(0, len(x) - fl, fl):
        fr = x[s:s + fl] * np.hanning(fl)
        if np.sqrt(np.mean(fr ** 2)) < thr: continue
        ac = np.correlate(fr, fr, "full")[fl - 1:]
        lo, hi = int(fs / 400), int(fs / 60)
        k = lo + np.argmax(ac[lo:hi])
        if ac[k] > 0.4 * ac[0]: out.append(fs / k)
    return float(np.median(out)) if out else None

segs, embs = [], []
for s in tr:
    a, b = s["start"], s["end"]
    if b - a < 0.8: continue
    x16 = v16[int(a * 16000):int(b * 16000)]
    x = voc[int(a * sr):int(b * sr)]
    with torch.no_grad():
        e = enc.encode_batch(torch.from_numpy(x16)[None]).squeeze().numpy()
    f, P = signal.welch(x, sr, nperseg=2048)
    def band(lo, hi): return 10 * np.log10(P[(f >= lo) & (f < hi)].mean() + 1e-20)
    segs.append({"start": round(a, 2), "end": round(b, 2), "text": s["text"], "words": len(s["words"]),
                 "low_vs_mid_db": round(band(80, 300) - band(300, 3400), 1),     # phone line kills < 300 Hz
                 "high_vs_mid_db": round(band(3400, 8000) - band(300, 3400), 1),  # ... and > 3.4 kHz
                 "level_db": round(float(20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-9)), 1),
                 "f0": f0_median(signal.resample_poly(x, 1, 4), sr / 4)})
    embs.append(e / np.linalg.norm(e))
embs = np.array(embs)
lab = fcluster(linkage(embs, "average", metric="cosine"), t=0.55, criterion="distance")
for s, l in zip(segs, lab): s["voice"] = int(l)

voices = []
for l in sorted(set(lab), key=lambda l: -sum(s["end"] - s["start"] for s in segs if s["voice"] == l)):
    ss = [s for s in segs if s["voice"] == l]
    dur = sum(s["end"] - s["start"] for s in ss)
    f0s = [s["f0"] for s in ss if s["f0"]]
    voices.append({"voice": int(l), "seconds": round(dur, 1), "segments": len(ss),
                   "wpm": round(sum(s["words"] for s in ss) / dur * 60, 0),
                   "f0_median": round(float(np.median(f0s)), 0) if f0s else None,
                   "low_vs_mid_db": round(float(np.median([s["low_vs_mid_db"] for s in ss])), 1),
                   "high_vs_mid_db": round(float(np.median([s["high_vs_mid_db"] for s in ss])), 1),
                   "level_db": round(float(np.median([s["level_db"] for s in ss])), 1),
                   "first": ss[0]["start"], "last": ss[-1]["end"],
                   "samples": [f"[{int(s['start']//60)}:{s['start']%60:04.1f}] {s['text'][:80]}" for s in ss[:4]]})
json.dump({"voices": voices, "segments": segs}, open(os.path.join(work, "voices.json"), "w", encoding="utf8"), indent=1)
for v in voices:
    print({k: v[k] for k in v if k != "samples"})
    for t in v["samples"]: print("    ", t)
