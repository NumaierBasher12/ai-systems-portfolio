"""Channel Watch + Trend Scout (our own versions, not copies of the reel's).
Reads public pages politely: YouTube channel RSS (fallback: yt-dlp flat listing, which also gives the /streams tab = clippable VODs),
Reddit top-of-day RSS with a long gap between requests, YouTube search sorted by date.
A 429 is waited out once then that source is skipped for the night; repeated 403 raises StopRun. No proxies, no tricks."""
import json
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

import truststore

truststore.inject_into_ssl()
import feedparser  # noqa: E402

from chief import core  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (personal research assistant)"}
YTDL = str(Path(__file__).resolve().parent.parent / "chief" / "ytdl.py")


class Skip(Exception):
    """This source cannot be read tonight; carry on with the next one."""


def http(url, retries_429=1, backoff=60):
    forbidden = 0
    for attempt in range(retries_429 + 2):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt <= retries_429:
                time.sleep(backoff)
                continue
            if e.code == 403:
                forbidden += 1
                if forbidden >= 2:
                    raise core.StopRun(f"403 twice from {url[:60]}")
                time.sleep(10)
                continue
            if e.code >= 500 and attempt == 0:
                time.sleep(5)
                continue
            raise Skip(f"HTTP {e.code}")
        except Exception as e:
            raise Skip(type(e).__name__)
    raise Skip("gave up")


def source_id(conn, kind, name, url):
    conn.execute("INSERT OR IGNORE INTO sources(kind, name, url, added) VALUES (?,?,?,?)", (kind, name, url, core.now()))
    return conn.execute("SELECT id FROM sources WHERE kind=? AND url=?", (kind, url)).fetchone()[0]


def upsert(conn, sid, ext_id, title, url, published, views, score, duration, kind):
    """First sight sets `seen`; later runs only refresh views and score."""
    conn.execute(
        "INSERT INTO items(source_id, ext_id, title, url, published, views, score, seen, duration, kind) VALUES (?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(source_id, ext_id) DO UPDATE SET views=excluded.views, score=excluded.score, title=excluded.title",
        (sid, ext_id, title, url, published, views, score, core.now(), duration, kind))


def yt_flat(url, n):
    """yt-dlp flat listing: [{id, title, view_count, duration}], newest first. Raises Skip on failure."""
    r = subprocess.run([sys.executable, "-X", "utf8", YTDL, "--flat-playlist", "--playlist-end", str(n), "--dump-json", url],
                       capture_output=True, text=True, encoding="utf-8", timeout=180)
    rows = [json.loads(line) for line in r.stdout.splitlines() if line.startswith("{")]
    if not rows:
        raise Skip("yt-dlp returned nothing")
    return rows


def watch_channel(conn, ch, c):
    """Uploads (RSS first) and streams (yt-dlp) of one channel. Returns (new_count, notes)."""
    sid = source_id(conn, "yt_channel", ch["name"], f"https://www.youtube.com/channel/{ch['channel_id']}")
    before = conn.execute("SELECT COUNT(*) FROM items WHERE source_id=?", (sid,)).fetchone()[0]
    notes = []
    try:  # uploads with publish time
        feed = feedparser.parse(http(f"https://www.youtube.com/feeds/videos.xml?channel_id={ch['channel_id']}"))
        rows = []
        for e in feed.entries:
            views = int((e.get("media_statistics") or {}).get("views", 0) or 0)
            pub = datetime(*e.published_parsed[:6], tzinfo=timezone.utc)
            age_h = max(1.0, (datetime.now(timezone.utc) - pub).total_seconds() / 3600)
            rows.append((e.yt_videoid, e.title, e.link, pub.isoformat(timespec="seconds"), views, views / age_h))
        med = (statistics.median([r[5] for r in rows]) or 1.0) if rows else 1.0
        for ext, title, link, pub, views, vph in rows:
            upsert(conn, sid, ext, title, link, pub, views, round(vph / med, 2), None, "upload")
    except Skip as e:
        notes.append(f"{ch['name']} RSS: {e}; using yt-dlp")
        rows = yt_flat(f"https://www.youtube.com/channel/{ch['channel_id']}/videos", c["channel_listing"])
        med = statistics.median([r.get("view_count") or 0 for r in rows]) or 1.0
        for r in rows:
            v = r.get("view_count") or 0
            upsert(conn, sid, r["id"], r.get("title"), f"https://www.youtube.com/watch?v={r['id']}", None, v, round(v / med, 2), r.get("duration"), "upload")
    try:  # past streams = the VODs worth clipping
        for r in yt_flat(f"https://www.youtube.com/channel/{ch['channel_id']}/streams", 6):
            v = r.get("view_count") or 0
            upsert(conn, sid, r["id"], r.get("title"), f"https://www.youtube.com/watch?v={r['id']}", None, v, None, r.get("duration"), "stream")
    except Skip as e:
        notes.append(f"{ch['name']} streams: {e}")
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM items WHERE source_id=?", (sid,)).fetchone()[0] - before, notes


def watch_subreddit(conn, sub, c):
    sid = source_id(conn, "reddit", sub, f"https://www.reddit.com/r/{sub}")
    before = conn.execute("SELECT COUNT(*) FROM items WHERE source_id=?", (sid,)).fetchone()[0]
    feed = feedparser.parse(http(f"https://www.reddit.com/r/{sub}/top/.rss?t=day"))
    for e in feed.entries:
        pub = datetime(*e.published_parsed[:6], tzinfo=timezone.utc).isoformat(timespec="seconds") if e.get("published_parsed") else None
        upsert(conn, sid, e.get("id") or e.link, e.title, e.link, pub, None, None, None, "thread")
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM items WHERE source_id=?", (sid,)).fetchone()[0] - before


def watch_search(conn, query, c):
    sid = source_id(conn, "yt_search", query, f"yt-results:{query}")
    before = conn.execute("SELECT COUNT(*) FROM items WHERE source_id=?", (sid,)).fetchone()[0]
    # sp=CAI%3D = sort by upload date (yt-dlp's ytsearchdate prefix no longer works); channel hits have 24-char ids, videos 11
    rows = [r for r in yt_flat(f"https://www.youtube.com/results?search_query={quote_plus(query)}&sp=CAI%253D", c["search_results"])
            if len(r.get("id", "")) == 11]
    for r in rows:
        upsert(conn, sid, r["id"], r.get("title"), f"https://www.youtube.com/watch?v={r['id']}", None, r.get("view_count"), None, r.get("duration"), "search")
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM items WHERE source_id=?", (sid,)).fetchone()[0] - before


def run_watch(conn, cfg, job):
    """Role 'watch': one source at a time, gaps between requests. Returns a one-line summary for the job table."""
    c = core.config("sources.json")
    new = 0
    problems = []

    def attempt(label, func, *args):
        nonlocal new
        try:
            out = func(*args)
            n, notes = out if isinstance(out, tuple) else (out, [])
            new += n
            problems.extend(notes)
        except Skip as e:
            problems.append(f"{label}: {e}")
        except subprocess.TimeoutExpired:
            problems.append(f"{label}: timeout")

    for ch in c["channels"]:
        attempt(ch["name"], watch_channel, conn, ch, c)
        time.sleep(c["rss_gap_s"])
    for sub in c["subreddits"]:
        attempt(f"r/{sub}", watch_subreddit, conn, sub, c)
        time.sleep(c["reddit_gap_s"])
    for q in c["searches"]:
        attempt(f"search '{q}'", watch_search, conn, q, c)
    msg = f"{new} new items" + (f"; problems: {' | '.join(problems)}" if problems else "")
    core.log(f"watch: {msg}")
    return msg
