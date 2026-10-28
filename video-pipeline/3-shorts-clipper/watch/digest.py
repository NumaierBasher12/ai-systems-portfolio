"""Role 'digest': turn the watched items into one dated Obsidian note.
Sections are plain SQL (outperformers, VODs worth clipping, Reddit today, search hits); Claude Haiku adds a short 'what matters' read
only while the nightly budget lasts. Without Claude the note is still complete."""
from datetime import datetime, timedelta
from pathlib import Path

from chief import core


def hms(seconds):
    s = int(seconds or 0)
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}"


def line(r, extra=""):
    views = f"{r['views']:,} views" if r["views"] else "views n/a"
    return f"- [{r['title']}]({r['url']}) ({r['source']}, {views}{extra})"


def rows(conn, where, args, order, limit):
    return conn.execute(
        "SELECT i.title, i.url, i.views, i.score, i.duration, i.kind, s.name AS source FROM items i "
        f"JOIN sources s ON s.id=i.source_id WHERE {where} ORDER BY {order} LIMIT {limit}", args).fetchall()


def run_digest(conn, cfg, job):
    c = core.config("sources.json")
    since = (datetime.now() - timedelta(hours=36)).isoformat(timespec="seconds")
    week = (datetime.now() - timedelta(days=7)).isoformat(timespec="seconds")
    out = rows(conn, "i.kind='upload' AND i.score>=2 AND i.seen>=?", (week,), "i.score DESC", 8)
    vods = rows(conn, "i.kind='stream' AND i.duration>=?", (c["vod_min_seconds"],), "i.views DESC", 8)
    kw = [k.lower() for k in c["keywords"]]
    threads = [r for r in rows(conn, "i.kind='thread' AND i.seen>=?", (since,), "i.seen DESC", 200)
               if r["source"] in c["core_subreddits"] or any(k in (r["title"] or "").lower() for k in kw)][:15]  # broad subs only when on topic
    hits = rows(conn, "i.kind='search' AND i.seen>=?", (since,), "i.views DESC", 10)
    new_total = conn.execute("SELECT COUNT(*) FROM items WHERE seen>=?", (since,)).fetchone()[0]

    parts = [f"# Digest {datetime.now():%Y-%m-%d}", "",
             f"{new_total} items first seen in the last 36 hours. Source: Assistant Watch (YouTube RSS/yt-dlp, Reddit top of day, YouTube search).", ""]
    parts += ["## Outperformers (views per hour vs the channel's own median, 2x or more)"]
    parts += [line(r, f", {r['score']}x") for r in out] or ["- none this week"]
    parts += ["", "## VODs worth clipping (streams of 1 h or more)"]
    parts += [line(r, f", {hms(r['duration'])}") for r in vods] or ["- none listed"]
    parts += ["", "## Reddit, top of day"]
    parts += [f"- [{r['title']}]({r['url']}) (r/{r['source']})" for r in threads] or ["- nothing today (or rate-limited, see the run log)"]
    parts += ["", "## YouTube search, newest by query"]
    parts += [line(r, f", {hms(r['duration'])}" if r["duration"] else "") for r in hits] or ["- none"]

    facts = "\n".join(p for p in parts if p.startswith("- "))[:6000]
    read = core.ask_claude(conn, cfg,
        "You help a clipper who turns Rainbow Six Siege streamer Jynxzi's long streams into short vertical clips. "
        "From the list below, write at most 5 short bullets: what matters today, which VOD to cut first and why, any topic or "
        "outperformer worth a clip. Use only the list; say 'not enough data' rather than guess. No preamble.\n\n" + facts)
    parts[3:3] = ["## Read of the day (Claude Haiku)", read or "- skipped (Claude budget spent or call failed); the sections below are complete", ""]

    d = Path(c["digest_dir"])
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{datetime.now():%Y-%m-%d}.md"
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")
    core.log(f"digest written: {path}")
    return str(path)
