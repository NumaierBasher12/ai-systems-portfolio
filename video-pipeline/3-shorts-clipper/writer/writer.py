"""Role 'writer': hook writer + Voice Check for the top candidates of one video.
One Claude call (Sonnet, counted against the nightly budget) writes, per moment, a pinned label, 3 hook options, a title, a caption and tags,
using what the Jynxzi study found (hook scoreboard from clips.db, the playbook, format F01). Then every text goes through the Voice Check:
cheap rules (length, blocked topics, quotes that are not in the clip) plus a local-model read against the user's Brand Voice note.
A draft with a flag is kept but marked, never used in a clip, and shows up in the review file for the user."""
import json
import re
import sqlite3
import urllib.request
from pathlib import Path

from chief import core
from moments.finder import JUDGE_MODEL, OLLAMA_URL

CLIPS_DB = Path(r"C:\dev\clipping\db\clips.db")  # read-only knowledge base from the study


def hook_context(streamer):
    """Top hook types by average views (own channel and outside clips) from the 364 by-eye reads."""
    try:
        db = sqlite3.connect(f"file:{CLIPS_DB}?mode=ro", uri=True)
        rows = db.execute("SELECT hook_type, side, n, avg_views FROM hook_scoreboard WHERE streamer=? ORDER BY avg_views DESC LIMIT 8",
                          (streamer,)).fetchall()
        return "\n".join(f"- {h} ({s}, {n} clips, avg {v:,} views)" for h, s, n, v in rows)
    except Exception:
        return "- (hook scoreboard unavailable)"


def read_note(path, limit):
    try:
        return Path(path).read_text(encoding="utf-8")[:limit]
    except OSError:
        return ""


def ask_json(conn, cfg, prompt, model="sonnet"):
    text = core.ask_claude(conn, cfg, prompt, model=model, timeout=600)
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        return None


def rule_flags(d, clip_text, voice):
    flags = []
    if len(d["label"].split()) > voice["max_label_words"]:
        flags.append("label too long")
    if len(d["title"]) > voice["max_title_chars"]:
        flags.append("title too long")
    blob = " ".join([d["label"], d["title"], d["caption"], *d["hooks"]]).lower()
    flags += [f"blocked topic: {w}" for w in voice["blocked_words"] if re.search(rf"\b{re.escape(w)}\b", blob)]
    norm = re.sub(r"\W+", " ", clip_text.lower())
    for q in re.findall(r"[\"“]([^\"”]{6,})[\"”]", " ".join([d["title"], d["caption"], *d["hooks"]])):
        if re.sub(r"\W+", " ", q.lower()).strip() not in norm:
            flags.append(f"quote not in the clip: {q[:40]}")
    return flags


def llm_voice_check(voice_note, d, clip_text):
    prompt = ("You check short-video copy against a brand voice note.\n\nBRAND VOICE NOTE:\n" + voice_note[:3000] +
              "\n\nCLIP TRANSCRIPT (may contain recognition errors):\n" + clip_text[:1500] +
              f"\n\nCOPY: label={d['label']!r}; title={d['title']!r}; hooks={d['hooks']!r}; caption={d['caption']!r}\n\n"
              'Does the copy break any rule in the note, or promise something the clip text does not show? '
              'Reply JSON only: {"ok": true or false, "reason": "one short sentence"}')
    body = json.dumps({"model": JUDGE_MODEL, "messages": [{"role": "user", "content": prompt}], "stream": False,
                       "think": False, "format": "json", "options": {"temperature": 0.1}}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(OLLAMA_URL, data=body, headers={"Content-Type": "application/json"}), timeout=180) as r:
            out = json.loads(json.loads(r.read())["message"]["content"])
        return [] if out.get("ok", True) else [f"voice check: {str(out.get('reason', ''))[:120]}"]
    except Exception:
        return ["voice check unavailable (local model down)"]


def run_writer(conn, cfg, job):
    """job.input = JSON {"video_id": ext id, "streamer": "jynxzi", "top": 5}."""
    arg = json.loads(job["input"])
    c = core.config("sources.json")
    voice = core.config("voice.json")
    v = conn.execute("SELECT id FROM videos WHERE ext_id=?", (arg["video_id"],)).fetchone()
    cands = conn.execute("SELECT id, start, end, score, reason, text FROM candidates WHERE video_id=? ORDER BY score DESC LIMIT ?",
                         (v["id"], arg.get("top", 5))).fetchall()
    voice_note = read_note(c["brand_voice_note"], 4000)
    moments = "\n\n".join(f"MOMENT id={x['id']} ({x['end'] - x['start']:.0f} s, judge: {x['reason']})\n{x['text'][:900]}" for x in cands)
    prompt = (f"You write copy for vertical clips of the streamer {arg.get('streamer', 'Jynxzi')} (Rainbow Six Siege, big clip ecosystem).\n"
              "WHAT WORKED (hook types ranked by average views in our study):\n" + hook_context(arg.get("streamer", "jynxzi")) +
              "\n\nPLAYBOOK EXCERPT:\n" + read_note(c["playbook_note"], 2500) + "\n\nBRAND VOICE NOTE (rules):\n" + voice_note +
              "\n\nFormat: split-screen, face on top, gameplay below, ONE pinned premise label for the whole clip, the premise stated in the first frame.\n"
              "For each moment below write: label (max 8 words, states the premise), 3 hook options (the opening line or on-screen first words), "
              "a curiosity title (max 80 characters) that the clip really pays off, a caption, 4 tags. Use only what the moment text shows; "
              "do not invent events or quotes. Speech recognition can be wrong; if a moment is not understandable, set \"skip\": true.\n\n" + moments +
              '\n\nReply JSON only: {"clips":[{"id":<moment id>,"skip":false,"label":"","hooks":["","",""],"title":"","caption":"","tags":["","","",""]}]}')
    data = ask_json(conn, cfg, prompt)
    if not data or "clips" not in data:
        return "no copy written (Claude budget spent or the reply was not JSON); nothing changed"
    by_id = {x["id"]: x for x in cands}
    conn.execute("DELETE FROM drafts WHERE candidate_id IN (%s)" % ",".join(str(i) for i in by_id))
    written = flagged = 0
    for d in data["clips"]:
        cand = by_id.get(d.get("id"))
        if not cand or d.get("skip"):
            continue
        d["hooks"] = [h for h in d.get("hooks", []) if h][:3]
        for k in ("label", "title", "caption"):
            d[k] = str(d.get(k, ""))
        flags = rule_flags(d, cand["text"], voice) + llm_voice_check(voice_note, d, cand["text"])
        fl = "; ".join(flags)
        flagged += bool(flags)
        rows = [("label", d["label"]), ("title", d["title"]), ("caption", d["caption"]), ("tags", " ".join("#" + t.lstrip("#") for t in d.get("tags", [])))]
        rows += [("hook", h) for h in d["hooks"]]
        for kind, text in rows:
            conn.execute("INSERT INTO drafts(candidate_id, kind, text, flags, made_by, made) VALUES (?,?,?,?,?,?)",
                         (cand["id"], kind, text, fl, "sonnet+voicecheck", core.now()))
        written += 1
    conn.commit()
    return f"copy for {written} moment(s), {flagged} with Voice Check flags"
