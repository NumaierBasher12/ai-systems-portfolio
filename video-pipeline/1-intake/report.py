r"""Build the Telegram text for one analysed reel (no AI, plain extraction from timeline.json).
  python report.py <reel id> [full]
Used by loop.py (after each analysis) and reel_bot.py (/reel <id>, /list).
"""
import json, re, sys
import os
from pathlib import Path
REELS = Path(os.environ.get("REELS_ROOT", Path.home() / "reels"))

ANALYSED = (REELS / "3-analysed")
URL = re.compile(r"https?://[^\s)>\]]+|(?:github\.com|www\.[a-z0-9.-]+\.[a-z]{2,})/[^\s)>\]]+", re.I)
CTA = re.compile(r"""comment\s+["'“]?([A-Za-z0-9_ -]{2,20}?)["'”]?\s+(?:for|and|to|below)""", re.I)


def load(rid):
    p = ANALYSED / f"ig_{rid}" / "timeline.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def transcript(d):
    return " ".join(r["voice"].strip() for r in d["rows"] if r.get("voice")).replace(" -", "-").strip()


def report(rid, full=False):
    d = load(rid)
    if not d:
        return f"No analysis found for {rid}."
    post, cov = d.get("post", {}), d.get("coverage", {})
    cap = (post.get("description") or "").strip()
    tr = transcript(d)
    both = cap + "\n" + tr
    links = list(dict.fromkeys(URL.findall(both)))[:6]
    cta = CTA.search(cap)
    bullets = [l.strip(" *-•\t") for l in cap.splitlines() if re.match(r"^\s*(\*|-|•|\d+[.)])\s+\S", l)][:6]
    out = [f"REEL {rid}  ·  {post.get('uploader') or '?'}",
           f"{d.get('seconds')} s · {len(d.get('cuts', []))} cuts · language {cov.get('language')} · coverage "
           f"{cov.get('voiced_seconds')}/{cov.get('seconds')} s voiced"
           + (f" · FLAGS: {'; '.join(cov['flags'])}" if cov.get("flags") else ""),
           "", "WHAT IT SAYS" + (" (full)" if full else ""), tr if full else (tr[:900] + ("..." if len(tr) > 900 else ""))]
    if bullets:
        out += ["", "POINTS IN THE CAPTION"] + [f"- {b[:160]}" for b in bullets]
    if cta:
        out += ["", f'CALL TO ACTION: comment "{cta.group(1).strip()}" on the reel to get the link (I do not comment from your account without asking)']
    if links:
        out += ["", "LINKS MENTIONED"] + links
    out += ["", f"Reply /reel {rid} for the full transcript. For actions to take, tell Claude Code: \"actions for {rid}\"."]
    return "\n".join(out)


def tools(text):
    """Exact tools/skills/repos named in the reel, via the local Qwen3 (Ollama). None if Ollama is not reachable."""
    import urllib.request
    body = {"model": "huihui_ai/qwen3-abliterated:4b", "stream": False, "think": False, "options": {"temperature": 0, "num_ctx": 4096},
            "prompt": "List ONLY the exact tools, skills, plugins, apps, GitHub repos (owner/name), models or commands named below "
                      "(spoken, in the caption, or shown on screen; on-screen text is OCR so fix obvious typos). One per line, "
                      "format 'name - what it does (max 8 words)'. No intro, no numbering, nothing invented.\n\n" + text[:7000]}
    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/generate", json.dumps(body).encode(), {"Content-Type": "application/json"})
        out = json.loads(urllib.request.urlopen(req, timeout=180).read())["response"]
    except Exception:
        return None
    out = re.sub(r"<think>.*?</think>", "", out, flags=re.S)
    seen, res = set(), []
    for l in (l.strip(" *-•\t") for l in out.splitlines()):
        k = re.sub(r"[^a-z0-9/]", "", l.split(" - ")[0].lower().replace("clawed", "claude"))
        if l and k not in seen:  # the small model repeats itself
            seen.add(k)
            res.append(l)
    return res[:12]


def screen(rid, limit=3000):
    """On-screen text (screentext.py output) as plain lines; lines seen in 2+ frames or reasonably long first."""
    p = ANALYSED / f"ig_{rid}" / "screen_text.json"
    if not p.exists():
        return ""
    es = json.loads(p.read_text(encoding="utf-8"))
    es = [e for e in es if e["n"] >= 2 or len(e["text"]) >= 12]
    return "\n".join(e["text"] for e in es)[:limit]


BRIEFING = ("You brief a web developer who builds AI video/animation and automation (knows JS, Node, React; uses Claude Code, ComfyUI, "
            "Whisper; 6 GB GPU laptop). Using ONLY the material below (caption, speech transcript, text shown on screen = OCR, fix obvious typos), "
            "write the briefing in EXACTLY this format, plain text, no markdown, no extra sections:\n"
            "WHAT IT IS: 2-3 sentences, concrete.\n"
            "WHAT IT DOES:\n- 4 to 6 bullets, each with a concrete feature, number, command or tool name from the material\n"
            "HOW TO GET IT: exact repo (owner/name), link, command or call to action (e.g. 'comment X'), or 'not stated'\n"
            "NEEDS / COST: requirements, price, accounts, or 'not stated'\n"
            "CAUTION: always state that claims come from the creator's own post and are unverified; add if it installs hooks, scripts, "
            "agents or MCP servers into the user's setup, runs commands, or needs API keys/tokens/accounts\n"
            "VERDICT: one sentence: worth trying for this user or not, and why.\n"
            "Write 'not stated' for anything the material does not say. Never invent facts.\n\nMATERIAL:\n")


def explain(rid):
    """Proper briefing for one reel from caption + transcript + on-screen text, by the local Qwen3 8B. Cached in explain.md."""
    import urllib.request
    p = ANALYSED / f"ig_{rid}" / "explain.md"
    if p.exists():
        return p.read_text(encoding="utf-8")
    d = load(rid)
    if not d:
        return "no analysis"
    cap = (d.get("post", {}).get("description") or "").strip()
    mat = f"CAPTION:\n{cap}\n\nSPOKEN:\n{transcript(d)}\n\nSHOWN ON SCREEN:\n{screen(rid, 3500)}"
    body = {"model": "huihui_ai/qwen3-abliterated:8b", "stream": False, "think": False, "options": {"temperature": 0.2, "num_ctx": 8192},
            "prompt": BRIEFING + mat[:9000]}
    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/generate", json.dumps(body).encode(), {"Content-Type": "application/json"})
        out = json.loads(urllib.request.urlopen(req, timeout=900).read())["response"]
        out = re.sub(r"<think>.*?</think>", "", out, flags=re.S).strip()
    except Exception:
        out = ""
    if len(out) < 80:  # model unreachable/too little RAM: fall back to the plain extraction, and do not cache it
        items = tools(mat) or [l.strip(" *-•\t") for l in cap.splitlines() if l.strip()][:6]
        return "TOOLS / SKILLS MENTIONED:\n" + "\n".join(f"- {i[:110]}" for i in items)
    p.write_text(out, encoding="utf-8")
    return out


def digest_block(rid, n):
    """One reel as a numbered block with the full briefing, for the digest message."""
    d = load(rid)
    if not d:
        return f"{n}. {rid}: no analysis"
    post = d.get("post", {})
    return (f"{n}. {post.get('uploader') or '?'} · {d.get('seconds')} s · {d.get('cuts') and len(d['cuts'])} cuts\n"
            f"{explain(rid)[:1800]}\nhttps://www.instagram.com/reel/{rid}/")


def brief(rid):
    """Short Telegram text: the exact tools in bullets + a yes/no. This is what the loop sends after each analysis."""
    d = load(rid)
    if not d:
        return f"No analysis found for {rid}."
    post = d.get("post", {})
    cap = (post.get("description") or "").strip()
    items = tools(cap + "\n" + transcript(d)) or [l.strip(" *-•\t") for l in cap.splitlines() if l.strip()][:6]
    links = list(dict.fromkeys(URL.findall(cap + "\n" + transcript(d))))[:3]
    out = [f"{post.get('uploader') or '?'} · {d.get('seconds')} s · {rid}", "", "TOOLS / SKILLS IN IT"] + [f"- {i[:110]}" for i in items]
    if links:
        out += ["", "LINKS"] + links
    out += ["", f"Use it?  /go {rid}   or   /skip {rid}", f"(full text: /reel {rid})"]
    return "\n".join(out)


if __name__ == "__main__":
    print((brief if "brief" in sys.argv[2:] else report)(sys.argv[1], *(["full"] if "full" in sys.argv[2:] else [])))
