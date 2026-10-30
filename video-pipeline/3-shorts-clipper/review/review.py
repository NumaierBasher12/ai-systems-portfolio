"""Roles 'review' and 'ingest_review': the approval gate.
review: write 'Review YYYY-MM-DD.md' into the vault: one block per candidate with the moment, the clip file, the copy and its Voice Check flags,
and a Decision line the user ticks (approve / change / reject).
ingest_review: read the ticks back into approvals + decisions, set clips.status, and copy APPROVED clips with their copy into outbox/<date>/
for the user to post by hand. Nothing is ever uploaded or posted by this code."""
import re
import shutil
from datetime import datetime
from pathlib import Path

from chief import core

OUTBOX = core.ROOT / "outbox"
BLOCK = re.compile(r"<!-- cand:(\d+) -->\s*\n(.*?)(?=\n## |\Z)", re.S)


def hms(t):
    t = int(t)
    return f"{t // 3600}:{t // 60 % 60:02d}:{t % 60:02d}"


def run_review(conn, cfg, job):
    c = core.config("sources.json")
    out_dir = Path(c["review_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    cands = conn.execute("SELECT c.id, c.start, c.end, c.score, c.reason, c.text, v.ext_id, v.title AS vtitle FROM candidates c "
                         "JOIN videos v ON v.id=c.video_id ORDER BY c.score DESC").fetchall()
    parts = [f"# Review {datetime.now():%Y-%m-%d}", "",
             "Tick ONE box per clip (`[x]`), add a note if you like, save the file. The next run reads your marks: approved clips go to the outbox folder "
             "(`outbox/<date>/`) with their copy, rejected ones are remembered. Nothing is posted by the assistant. Copy marked with Flags "
             "failed the Voice Check and was NOT used in the clip.", ""]
    for n, x in enumerate(cands, 1):
        clip = conn.execute("SELECT path, status FROM clips WHERE candidate_id=? ORDER BY id DESC LIMIT 1", (x["id"],)).fetchone()
        drafts = conn.execute("SELECT kind, text, flags FROM drafts WHERE candidate_id=? ORDER BY id", (x["id"],)).fetchall()
        d = {}
        for r in drafts:
            d.setdefault(r["kind"], []).append(r["text"])
        flags = next((r["flags"] for r in drafts if r["flags"]), "")
        parts += [f"## #{n} {hms(x['start'])}-{hms(x['end'])} (score {x['score']})",
                  f"[Open this moment on YouTube](https://www.youtube.com/watch?v={x['ext_id']}&t={int(x['start'])}s) from *{x['vtitle']}*"
                  + (f" · clip: `{clip['path']}` ({clip['status']})" if clip else " · no clip cut yet"),
                  f"Judge: {x['reason'] or 'n/a'}", "", "> " + x["text"][:420].replace("\n", " ") + ("..." if len(x["text"]) > 420 else ""), ""]
        if d:
            parts += [f"**Label:** {d.get('label', [''])[0]}", f"**Title:** {d.get('title', [''])[0]}",
                      "**Hooks:** " + " | ".join(f"{i}) {h}" for i, h in enumerate(d.get("hook", []), 1)),
                      f"**Caption:** {d.get('caption', [''])[0]}  {d.get('tags', [''])[0]}", f"**Flags:** {flags or 'none'}"]
        else:
            parts += ["*No copy written for this moment yet.*"]
        parts += ["", f"<!-- cand:{x['id']} -->", "Decision: [ ] approve   [ ] change   [ ] reject", "Notes: ", ""]
    path = out_dir / f"Review {datetime.now():%Y-%m-%d}.md"
    if path.exists():  # never overwrite marks the user already made
        path = out_dir / f"Review {datetime.now():%Y-%m-%d %H%M}.md"
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")
    return str(path)


def ingest_marks(conn, text):
    """Returns the number of new marks recorded."""
    new = 0
    for m in BLOCK.finditer(text):
        cand_id, body = int(m.group(1)), m.group(2)
        checked = [v for v in ("approve", "change", "reject") if re.search(rf"\[[xX]\]\s*{v}", body)]
        if len(checked) != 1:
            continue  # nothing ticked, or ambiguous: wait for the user
        verdict = {"approve": "approved", "change": "change", "reject": "rejected"}[checked[0]]
        nm = re.search(r"Notes:(.*)", body)
        note = (nm.group(1) or "").strip() if nm else ""
        clip = conn.execute("SELECT id, path FROM clips WHERE candidate_id=? ORDER BY id DESC LIMIT 1", (cand_id,)).fetchone()
        last = conn.execute("SELECT verdict, note FROM approvals WHERE clip_id IS ? ORDER BY id DESC LIMIT 1", (clip["id"] if clip else None,)).fetchone()
        if last and last["verdict"] == verdict and (last["note"] or "") == note:
            continue  # already recorded
        conn.execute("INSERT INTO approvals(clip_id, verdict, note, at) VALUES (?,?,?,?)", (clip["id"] if clip else None, verdict, note, core.now()))
        conn.execute("INSERT INTO decisions(subject, subject_id, choice, why, at) VALUES ('candidate',?,?,?,?)", (cand_id, verdict, note, core.now()))
        if clip:
            conn.execute("UPDATE clips SET status=? WHERE id=?", (verdict, clip["id"]))
            if verdict == "approved":
                package(conn, cand_id, Path(clip["path"]))
        new += 1
    conn.commit()
    return new


def package(conn, cand_id, clip_path):
    """P6 hand-off: copy the approved clip and its copy to outbox/<date>/ for the user to post by hand."""
    day = OUTBOX / f"{datetime.now():%Y-%m-%d}"
    day.mkdir(parents=True, exist_ok=True)
    if clip_path.exists():
        shutil.copy2(clip_path, day / clip_path.name)
    d = {}
    for r in conn.execute("SELECT kind, text FROM drafts WHERE candidate_id=? AND (flags IS NULL OR flags='') ORDER BY id", (cand_id,)):
        d.setdefault(r["kind"], []).append(r["text"])
    lines = [f"Clip: {clip_path.name}", f"Title: {d.get('title', [''])[0]}", f"Label (pinned in the video): {d.get('label', [''])[0]}",
             f"Caption: {d.get('caption', [''])[0]}", f"Tags: {d.get('tags', [''])[0]}", "Hook options: " + " | ".join(d.get("hook", [])),
             "", "Rights to the source footage are still your open decision. Check platform rules (clipping vault, Research/platform-rules.md) before posting."]
    (day / f"{clip_path.stem}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_ingest_review(conn, cfg, job):
    c = core.config("sources.json")
    total = 0
    for f in sorted(Path(c["review_dir"]).glob("Review *.md")) if Path(c["review_dir"]).exists() else []:
        total += ingest_marks(conn, f.read_text(encoding="utf-8"))
    return f"{total} new mark(s) recorded"
