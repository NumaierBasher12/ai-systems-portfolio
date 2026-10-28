# Voice + music measurements from Demucs stems.
# usage: <voicestudio python> audio.py <work_dir>
# reads stems/htdemucs/audio44k/{vocals,no_vocals}.wav, transcript.json, shots.csv; writes audio_report.json
import os, sys, json, csv
import numpy as np, soundfile as sf
from scipy import signal

work = sys.argv[1]
stem = os.path.join(work, "stems", "htdemucs", "audio44k")
voc, sr = sf.read(os.path.join(stem, "vocals.wav"))
mus, _ = sf.read(os.path.join(stem, "no_vocals.wav"))
voc, mus = voc.mean(1), mus.mean(1)
rep = {}

def db(x): return 20 * np.log10(np.maximum(x, 1e-9))

# 1) level per 100 ms: is the music ducked under the voice?
hop = sr // 10
nwin = len(voc) // hop
rv = np.sqrt(np.mean(voc[:nwin * hop].reshape(nwin, hop) ** 2, 1))
rm = np.sqrt(np.mean(mus[:nwin * hop].reshape(nwin, hop) ** 2, 1))
talk = db(rv) > np.percentile(db(rv), 95) - 25
rep["level"] = {
    "voice_active_pct": round(100 * talk.mean(), 1),
    "voice_rms_db_when_talking": round(float(np.median(db(rv[talk]))), 1),
    "music_rms_db_under_voice": round(float(np.median(db(rm[talk]))), 1),
    "music_rms_db_in_gaps": round(float(np.median(db(rm[~talk]))), 1) if (~talk).any() else None,
    "voice_over_music_db": round(float(np.median(db(rv[talk]) - db(rm[talk]))), 1),
}
# music loudness per second for the timeline (downsampled)
rep["music_db_per_s"] = [round(float(v), 1) for v in db(rm[: nwin // 10 * 10]).reshape(-1, 10).mean(1)]
rep["voice_db_per_s"] = [round(float(v), 1) for v in db(rv[: nwin // 10 * 10]).reshape(-1, 10).mean(1)]

# 2) voice timbre: long-term spectrum bands, crest factor (compression), noise floor
vmask = np.zeros(len(voc), bool); vmask[:nwin * hop] = np.repeat(talk, hop)
f, P = signal.welch(voc[vmask], sr, nperseg=4096)
def band(a, b): return float(10 * np.log10(P[(f >= a) & (f < b)].mean()))
ref = band(300, 1000)
rep["voice_spectrum_db_rel_300_1k"] = {k: round(band(*v) - ref, 1) for k, v in {
    "sub_<80": (20, 80), "low_80_200": (80, 200), "lowmid_200_500": (200, 500), "presence_2k_5k": (2000, 5000),
    "air_8k_12k": (8000, 12000), "top_12k_16k": (12000, 16000)}.items()}
seg = voc[vmask]
rep["voice_crest_db"] = round(float(db(np.max(np.abs(seg))) - db(np.sqrt(np.mean(seg ** 2)))), 1)
rep["voice_short_term_spread_db"] = round(float(np.percentile(db(rv[talk]), 90) - np.percentile(db(rv[talk]), 10)), 1)
rep["voice_noise_floor_db"] = round(float(np.percentile(db(rv), 5)), 1)

# 3) pitch (simple autocorrelation f0 on 40 ms frames where voice is loud)
x = signal.resample_poly(voc, 1, 4); fs = sr / 4
fl = int(0.04 * fs); f0 = []
for s in range(0, len(x) - fl, fl):
    fr = x[s:s + fl] * np.hanning(fl)
    if np.sqrt(np.mean(fr ** 2)) < 10 ** ((rep["level"]["voice_rms_db_when_talking"] - 6) / 20):
        continue
    ac = np.correlate(fr, fr, "full")[fl - 1:]
    lo, hi = int(fs / 400), int(fs / 60)
    k = lo + np.argmax(ac[lo:hi])
    if ac[k] > 0.4 * ac[0]:
        f0.append(fs / k)
f0 = np.array(f0)
rep["voice_f0_hz"] = {"median": round(float(np.median(f0)), 1), "p10": round(float(np.percentile(f0, 10)), 1),
                      "p90": round(float(np.percentile(f0, 90)), 1), "frames": int(len(f0))} if len(f0) else None

# 4) pace and pauses from Whisper word timings
tr = json.load(open(os.path.join(work, "transcript.json"), encoding="utf8"))
words = [w for s in tr for w in s["words"]]
dur_talk = sum(w[1] - w[0] for w in words)
gaps = np.array([b[0] - a[1] for a, b in zip(words, words[1:])])
total_min = (words[-1][1] - words[0][0]) / 60
rep["pace"] = {"words": len(words), "wpm_overall": round(len(words) / total_min, 1),
               "wpm_while_speaking": round(len(words) / (dur_talk / 60), 1),
               "pauses_over_0.5s": int((gaps > 0.5).sum()), "pauses_over_1.5s": int((gaps > 1.5).sum()),
               "longest_pauses": [[round(words[i][1], 1), round(float(gaps[i]), 1)] for i in np.argsort(-gaps)[:12]]}

# 5) music sections: big changes in the music stem's spectrum (novelty), plus music-only stretches
f2, t2, S = signal.spectrogram(signal.resample_poly(mus, 1, 4), fs, nperseg=4096, noverlap=2048)
L = np.log(S + 1e-10)
bands = np.array([L[(f2 >= a) & (f2 < b)].mean(0) for a, b in zip(np.geomspace(40, 5000, 13)[:-1], np.geomspace(40, 5000, 13)[1:])])
# 4-second windows before/after each point
step = t2[1] - t2[0]; w = int(4 / step)
nov = np.array([np.linalg.norm(bands[:, i - w:i].mean(1) - bands[:, i:i + w].mean(1)) if w <= i < bands.shape[1] - w else 0
                for i in range(bands.shape[1])])
peaks, _ = signal.find_peaks(nov, distance=int(15 / step), prominence=np.percentile(nov, 90) * 0.5)
rep["music_change_points_s"] = [round(float(t2[p]), 1) for p in peaks]
silent = db(rm) < np.percentile(db(rm), 50) - 20
rep["music_dropouts_s"] = [round(i / 10, 1) for i in np.flatnonzero(np.diff(silent.astype(int)) == 1)][:60]

# 6) sound hits on cuts: onset strength of the music/SFX stem around each cut
env = np.abs(signal.hilbert(signal.resample_poly(mus, 1, 40)))  # ~1.1 kHz envelope
fe = sr / 40
on = np.maximum(np.diff(signal.savgol_filter(env, 41, 2)), 0)
thr = np.percentile(on, 98)
hits = on > thr
shots = list(csv.DictReader(open(os.path.join(work, "shots.csv"), encoding="utf8")))
cut_t = [float(r["start"]) for r in shots[1:]]
near = [hits[max(0, int((t - 0.12) * fe)):int((t + 0.12) * fe)].any() for t in cut_t]
rep["cuts_with_sound_hit_pct"] = round(100 * np.mean(near), 1) if near else None
rep["cuts_total"] = len(cut_t)
rnd = np.random.default_rng(0).uniform(1, len(mus) / sr - 1, 2000)  # chance rate: same test at random times
rep["random_times_with_sound_hit_pct"] = round(100 * np.mean([hits[max(0, int((t - 0.12) * fe)):int((t + 0.12) * fe)].any() for t in rnd]), 1)

json.dump(rep, open(os.path.join(work, "audio_report.json"), "w"), indent=1)
print(json.dumps({k: v for k, v in rep.items() if not k.endswith("per_s")}, indent=1))
