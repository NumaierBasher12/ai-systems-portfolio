r"""Master log of every reel: one Obsidian note, rebuilt from ledger + analysis + your decisions + install outcomes.
  python reellog.py
Inputs: ledger.csv, 3-analysed\ig_<id>\{timeline.json,explain.md}, decisions.txt ("<date> <time> go|skip|keep <id>"),
        outcomes.txt ("<date> <time> <id> installed|rejected|asked|needs-you | free note"; written by Claude after an audit).
Output: Projects\Web Scraping\Reel Log.md (table, newest first, plus a dated event history).
"""
import csv, json, re
import os
from pathlib import Path
REELS = Path(os.environ.get("REELS_ROOT", Path.home() / "reels"))

ROOT = REELS
OUT = Path(os.environ.get("REEL_LOG", ROOT / "Reel Log.md"))


def lines(p):
    return p.read_text(encoding="utf-8-sig").splitlines() if p.exists() else []


def build():
    decisions, outcomes, events = {}, {}, []
    for l in lines(ROOT / "decisions.txt"):
        m = re.match(r"(\S+ \S+) (go|skip|keep) (\S+)", l)
        if m:
            decisions[m[3]] = m[2]
            events.append((m[1], m[3], f"you said /{m[2]}"))
    for l in lines(ROOT / "outcomes.txt"):
        m = re.match(r"(\S+ \S+) (\S+) (\S+)\s*\|?\s*(.*)", l)
        if m:
            outcomes[m[2]] = f"{m[3]}" + (f" ({m[4]})" if m[4] else "")
            events.append((m[1], m[2], f"{m[3]} {m[4]}".strip()))
    rows = []
    for r in csv.DictReader((ROOT / "ledger.csv").open(encoding="utf-8-sig")):
        rid, st = r["id"], r["status"]
        if st == "removed":
            continue
        d = {}
        tl = ROOT / "3-analysed" / f"ig_{rid}" / "timeline.json"
        if tl.exists():
            d = json.loads(tl.read_text(encoding="utf-8"))
        post = d.get("post", {})
        ex = ROOT / "3-analysed" / f"ig_{rid}" / "explain.md"
        what = ""
        if ex.exists():
            m = re.search(r"WHAT IT IS:\s*(.+)", ex.read_text(encoding="utf-8"))
            what = (m[1] if m else "").replace("|", "/")[:160]
        step = outcomes.get(rid) or (f"you: {decisions[rid]}, waiting for audit" if rid in decisions else
                                     "briefed, waiting for your /go or /skip" if ex.exists() else st)
        rows.append((r["added"], rid, post.get("uploader") or "?", r["collection"], what, step))
    rows.sort(reverse=True)
    out = ["---", "tags: [reel-log, web-scraping]", "---", "# Reel log",
           f"Every reel sent to the pipeline, newest first. Rebuilt automatically ({len(rows)} reels). "
           "Skills/tools found: [[_index]] (Resources/reel-skills). Digests: `Reel digests/`.", "",
           "| Added | Reel | By | Folder | What it is | Where it stands |", "|---|---|---|---|---|---|"]
    out += [f"| {a} | [{i}](https://www.instagram.com/reel/{i}/) | {u} | {c} | {w} | {s} |" for a, i, u, c, w, s in rows]
    out += ["", "## History (decisions and outcomes)"]
    out += [f"- {t} · {i} · {e}" for t, i, e in sorted(events, reverse=True)] or ["- none yet"]
    OUT.write_text("\n".join(out) + "\n", encoding="utf-8")
    return len(rows)


if __name__ == "__main__":
    print(build(), "reels written to", OUT)
