# Tag each shot's mid frame with the local vision model (Ollama). Resume-safe.
# usage: py -3.13 tag.py <work_dir> [--shots 3,5,17] [--out tags_eval.jsonl]
import os, sys, json, glob, base64, time, urllib.request, argparse

ap = argparse.ArgumentParser()
ap.add_argument("work"); ap.add_argument("--shots"); ap.add_argument("--out", default="tags.jsonl")
args = ap.parse_args()
work = args.work
MODEL = "huihui_ai/qwen3-vl-abliterated:8b-instruct"
out_path = os.path.join(work, args.out)
done = set()
if os.path.exists(out_path):
    done = {json.loads(l)["shot"] for l in open(out_path, encoding="utf8") if l.strip()}

# v2 (2026-10-24): v1 put news footage, documents and quote cards into "screen_capture"; definitions fix that
PROMPT = """You are a film editor logging one frame from a documentary. First decide "medium" using these definitions:
- 3d_render: a computer-generated 3D scene (CG people/mannequins, CG rooms, CG objects; also a CG phone or monitor shown inside a 3D scene)
- archival_video: real filmed footage of real events: TV news, anchors, protests, hearings, press conferences, interviews
- archival_photo: a real still photograph of a real person or place
- document: paper or article text filling the frame: court filings, forms, news headlines, article pages, often with yellow highlighter
- quote_card: a dark or blurred background with a speaker name and a quote in text, sometimes an audio waveform line
- screen_capture: a flat recording of a real computer or phone interface: web browser, search engine, website, app, login form
- 2d_motion_graphics: flat icons, diagrams, connecting lines, counters on a plain or dark grid background
- map: a geographic map or satellite view
- title_card: a chapter or title in text on a plain background
- stock_footage: generic real live-action filmed b-roll with no news context (servers, hands typing, CCTV camera, prison)
- mixed: two of the above layered equally
Answer ONLY with JSON:
{"medium": "<one of the names above>",
 "shot_size": one of ["extreme_wide","wide","medium","close_up","extreme_close_up","insert_detail","graphic_full_frame"],
 "angle": one of ["eye_level","high","low","overhead_top_down","dutch_tilt","pov","isometric","flat_2d"],
 "subject": "<what the frame shows, max 15 words>",
 "people": "<none | faceless 3d figures | real person | silhouette | other>",
 "lighting": "<key light direction and mood, max 10 words>",
 "depth_of_field": one of ["deep","shallow_bokeh","flat_graphic"],
 "palette_mood": "<max 6 words>",
 "composition": "<centered | rule_of_thirds | symmetric | leading_lines | negative_space | other, plus 5 words>",
 "on_screen_text": "<exact visible text, or empty>",
 "effects": "<grain, glow, chromatic aberration, vignette, blur, glitch, lens flare, scanlines... or none>",
 "how_made": "<your best guess how this was produced, max 15 words>"}"""

frames = sorted(glob.glob(os.path.join(work, "frames", "shot_*.jpg")))
if args.shots:
    want = {int(x) for x in args.shots.split(",")}
    frames = [p for p in frames if int(os.path.basename(p)[5:9]) in want]
with open(out_path, "a", encoding="utf8") as out:
    for p in frames:
        shot = int(os.path.basename(p)[5:9])
        if shot in done:
            continue
        body = json.dumps({"model": MODEL, "prompt": PROMPT, "images": [base64.b64encode(open(p, "rb").read()).decode()],
                           "format": "json", "stream": False,
                           "options": {"temperature": 0, "num_ctx": 2048, "num_gpu": 99}}).encode()  # all layers on GPU; a frame needs ~400 tokens
        t = time.time()
        tag = None
        for attempt in range(2):  # Ollama sometimes answers 500 once; one retry
            try:
                r = json.load(urllib.request.urlopen(urllib.request.Request(
                    "http://127.0.0.1:11434/api/generate", body, {"Content-Type": "application/json"}), timeout=300))
                tag = json.loads(r["response"]); break
            except Exception as e:
                err = e
        if tag is None:  # logged and retried on the next run
            print(f"shot {shot} FAILED: {err}", flush=True)
            continue
        tag["shot"] = shot
        out.write(json.dumps(tag, ensure_ascii=False) + "\n"); out.flush()
        if shot % 25 == 0:
            print(f"shot {shot} {time.time()-t:.1f}s", flush=True)
print("DONE", flush=True)
