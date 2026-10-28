# One compact markdown card with every measured identifier of a film (see vault analysis-protocol.md).
# usage: py -3.13 film_card.py <work_dir> [info_json]
import os, sys, json, csv, collections
import numpy as np

work = sys.argv[1]
root = os.path.dirname(os.path.abspath(__file__))
info_p = sys.argv[2] if len(sys.argv) > 2 else os.path.join(root, "video", os.path.basename(work.rstrip("\\/")) + ".info.json")
info = json.load(open(info_p, encoding="utf8")) if os.path.exists(info_p) else {}
def load(n): p = os.path.join(work, n); return json.load(open(p, encoding="utf8")) if os.path.exists(p) else None
shots = list(csv.DictReader(open(os.path.join(work, "shots.csv"), encoding="utf8")))
A, V, T = load("audio_report.json"), load("voices.json"), load("transcript.json")
tags = {}
if os.path.exists(os.path.join(work, "tags.jsonl")):
    for l in open(os.path.join(work, "tags.jsonl"), encoding="utf8"):
        if l.strip(): t = json.loads(l); tags[t["shot"]] = t
for s in shots:
    s["dur"] = float(s["dur"]); s["start"] = float(s["start"])
total = shots[-1]["start"] + shots[-1]["dur"]
def mmss(t): return f"{int(t//60)}:{t%60:04.1f}"
out = []
P = out.append

P(f"# Film card: {info.get('title', work)}")
P(f"channel {info.get('channel')} | {info.get('duration_string')} | {info.get('width')}x{info.get('height')} @ {info.get('fps')} fps | "
  f"views {info.get('view_count')} | likes {info.get('like_count')} | uploaded {info.get('upload_date')}")
chap = [(c["start_time"], c["title"]) for c in (info.get("chapters") or [])] or [(t, f"block {i+1}") for i, t in enumerate(range(0, int(total), 120))]
P("chapters: " + "; ".join(f"{mmss(a)} {n}" for a, n in chap))

d = np.array([s["dur"] for s in shots])
P(f"\n## edit\nshots {len(d)} | median {np.median(d):.2f}s | mean {d.mean():.2f}s | "
  + " ".join(f"{a}-{b}s:{int(((d>=a)&(d<b)).sum())}" for a, b in [(0,1),(1,2),(2,4),(4,8),(8,12),(12,999)]))
P("long shots (>12 s, check with a strip): " + ", ".join(f"#{s['shot']}@{mmss(s['start'])} {s['dur']:.0f}s" for s in shots if s["dur"] > 12))

words = [w for s in (T or []) for w in s["words"]]
md = np.array(A["music_db_per_s"]) if A else None
bounds = [a for a, _ in chap] + [total]
P("\n| chapter | min | words/min | cuts/min | music dB | moving % | dark % | top media |\n|---|---|---|---|---|---|---|---|")
for (a, n), b in zip(chap, bounds[1:]):
    ss = [s for s in shots if a <= s["start"] < b]; m = (b - a) / 60
    ww = [w for w in words if a <= w[0] < b]
    med = collections.Counter()
    for s in ss:
        if int(s["shot"]) in tags: med[tags[int(s["shot"])].get("medium")] += s["dur"]
    tot = sum(med.values()) or 1
    mus = md[int(a):int(b)] if md is not None else []
    P(f"| {n[:28]} | {m:.1f} | {len(ww)/m:.0f} | {len(ss)/m:.0f} | {np.median(mus) if len(mus) else float('nan'):.1f} | "
      f"{100*np.mean([s['move']!='static' for s in ss]) if ss else 0:.0f} | {100*np.mean([float(s['luma'])<60 for s in ss]) if ss else 0:.0f} | "
      + ", ".join(f"{k} {100*v/tot:.0f}%" for k, v in med.most_common(3)) + " |")

mv = collections.Counter(x for s in shots for x in s["move"].split("+"))
P("\n## camera (optical flow; thresholds unvalidated)\n" + ", ".join(f"{k} {100*v/len(shots):.0f}%" for k, v in mv.most_common()))
P(f"\n## look\nluma median {np.median([float(s['luma']) for s in shots]):.0f}/255 | sat median {np.median([float(s['sat']) for s in shots]):.0f}/255")

if A:
    P("\n## voice + music\n```")
    for k, v in A.items():
        if k.endswith("per_s") or k == "music_dropouts_s": continue
        if k == "pace":
            v = dict(v); lp = v.pop("longest_pauses")
            P(f"pace: {v}")
            for t, g in lp:  # the line spoken right before each long pause = hook lines
                before = [s for s in (T or []) if s["end"] <= t + 0.3]
                P(f"  pause {g:.1f}s at {mmss(t)} after: {before[-1]['text'][-90:] if before else ''}")
        else:
            P(f"{k}: {v}")
    dr = A.get("music_dropouts_s", [])
    P(f"music_dropouts: {len(dr)} (first at {', '.join(mmss(x) for x in dr[:8])})\n```")

if V:
    P("\n## voices (speaker clusters; name them from the lines, merge duplicates)\n| v | sec | share | f0 | wpm | low_vs_mid | high_vs_mid | level | first line |\n|---|---|---|---|---|---|---|---|---|")
    tv = sum(v["seconds"] for v in V["voices"])
    for v in V["voices"][:14]:
        P(f"| {v['voice']} | {v['seconds']:.0f} | {100*v['seconds']/tv:.0f}% | {v['f0_median']} | {v['wpm']:.0f} | {v['low_vs_mid_db']} | {v['high_vs_mid_db']} | {v['level_db']} | {v['samples'][0][:70]} |")
    P(f"(+{max(0, len(V['voices'])-14)} smaller clusters)")

if tags:
    P(f"\n## media (local tags, {len(tags)}/{len(shots)} shots; spot-check by eye)")
    for f in ("medium", "shot_size", "angle", "people", "depth_of_field"):
        c = collections.Counter()
        for s in shots:
            t = tags.get(int(s["shot"]))
            if t: c[str(t.get(f))] += s["dur"]
        tot = sum(c.values()) or 1
        P(f"{f} (by screen time): " + ", ".join(f"{k} {100*v/tot:.0f}%" for k, v in c.most_common(8)))
    seq = [tags[int(s["shot"])].get("medium") for s in shots if int(s["shot"]) in tags]
    pairs = collections.Counter(zip(seq, seq[1:]))
    P("switch pattern (medium A -> B, count): " + ", ".join(f"{a}->{b} {n}" for (a, b), n in pairs.most_common(10)))
    runs, cur, n = collections.defaultdict(list), seq[0], 1
    for x in seq[1:] + [None]:
        if x == cur: n += 1
        else: runs[cur].append(n); cur, n = x, 1
    P("run length (shots in a row): " + ", ".join(f"{k} mean {np.mean(v):.1f} max {max(v)}" for k, v in runs.items() if k))
    eff = collections.Counter(e.strip() for t in tags.values() for e in str(t.get("effects", "")).lower().split(",") if e.strip() and e.strip() != "none")
    P("effects mentioned: " + ", ".join(f"{k} {v}" for k, v in eff.most_common(10)))

open(os.path.join(work, "film_card.md"), "w", encoding="utf8").write("\n".join(out) + "\n")
print(f"film_card.md: {len(out)} lines")
