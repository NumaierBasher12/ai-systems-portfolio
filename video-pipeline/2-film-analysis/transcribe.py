# Transcribe the narration with word timings (faster-whisper large-v3 on GPU).
# usage: py -3.13 transcribe.py <work_dir>
import os, sys, glob, json, site, time
import numpy as np, wave

# shortcut: cuBLAS/cuDNN DLLs come from pip's nvidia-* packages, not a CUDA toolkit
for sp in site.getsitepackages():
    for d in glob.glob(os.path.join(sp, "nvidia", "*", "bin")):
        os.add_dll_directory(d)
        os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]
from faster_whisper import WhisperModel

work = sys.argv[1]
with wave.open(os.path.join(work, "audio16k.wav")) as w:  # read wav ourselves: PyAV 19 breaks the built-in decoder
    audio = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768

t = time.time()
try:
    m = WhisperModel("large-v3", device="cuda", compute_type="float16", local_files_only=True)
    dev = "cuda"
except Exception as e:
    print("GPU failed, using CPU:", e, flush=True)
    m = WhisperModel("large-v3", device="cpu", compute_type="int8", local_files_only=True)
    dev = "cpu"
segs, info = m.transcribe(audio, language="en", word_timestamps=True, beam_size=5,
                          condition_on_previous_text=False)
out = []
with open(os.path.join(work, "transcript.txt"), "w", encoding="utf8") as f:
    for s in segs:
        out.append({"start": s.start, "end": s.end, "text": s.text.strip(),
                    "words": [[w.start, w.end, w.word] for w in s.words]})
        f.write(f"[{int(s.start//60):02d}:{s.start%60:05.2f}] {s.text.strip()}\n")
        if len(out) % 50 == 0:
            print(f"{len(out)} segments, at {s.end:.0f}s", flush=True)
json.dump(out, open(os.path.join(work, "transcript.json"), "w", encoding="utf8"))
print(f"DONE {dev} {len(out)} segments in {time.time()-t:.0f}s", flush=True)
