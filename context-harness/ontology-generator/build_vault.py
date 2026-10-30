"""Builds the Fable knowledge vault (an ontology of style knowledge + real audience questions) from the raw scrape.
Reads data/youtube, data/reddit, data/x, data/exa. Writes the output vault folder (generated folders only) and
data/tables/idea_bank.csv. Re-run any time: generated folders are rebuilt, 'My Notes' and '_Project' are never touched.
Usage: python build_vault.py"""
import csv, json, os, re, shutil, statistics
from collections import Counter, defaultdict
from pathlib import Path
from config import DATA
from ontology import ONTOLOGY, FORMATS

VAULT = Path(os.environ.get("FABLE_VAULT", Path.home() / "ontology-vault"))
GENERATED = ["Concepts", "Domains", "Question Bank", "Formats", "Home.md", "Hook Library.md", "Creators.md", "Ontology.canvas"]
NOISE = re.compile(r"official (music )?video|\bsong|lyric|full movie|trailer|\baudio\b|\bft\.|feat\.|jukebox|remix|\bdance\b|#bollywood|punjabi songs?", re.I)
QSTART = re.compile(r"^(how|what|why|which|should|is|are|does|do|can|any|where|when|anyone|help|would|will)\b", re.I)
CONCEPTS = [(d, n, re.compile(rx, re.I), definition) for d, items in ONTOLOGY.items() for n, rx, definition in items]
DOMAIN_OF = {n: d for d, n, _, _ in CONCEPTS}
COLORS = [0xE6194B, 0x3CB44B, 0xFFE119, 0x4363D8, 0xF58231, 0x911EB4, 0x46F0F0, 0xF032E6, 0xBCF60C, 0xFABEBE, 0x008080, 0xE6BEFF]


def rows(p):
    return [json.loads(l) for l in p.open(encoding="utf-8") if l.strip()] if p.exists() else []


def clean(t):
    t = re.sub(r"https?://\S+|@\w+|\[\w+\]", "", t or "")
    return re.sub(r"\s+", " ", t).strip()


def concepts_in(text):
    return [n for _, n, rx, _ in CONCEPTS if rx.search(text)]


def is_question(t, src=""):
    # Reddit post titles often skip the "?"; comments need one, or "What I learned: ..." counts as a question
    return 25 <= len(t) <= 300 and ("?" in t or (src == "Reddit" and bool(QSTART.match(t))))


def link(name):
    return f"[[{name}]]"


def md_escape(t):
    return t.replace("|", "/").replace("[", "(").replace("]", ")")


def load():
    yt = DATA / "youtube"
    videos = {}
    for r in rows(yt / "search.jsonl") + rows(yt / "channel_videos.jsonl") + rows(yt / "details.jsonl"):
        if r.get("marker") or not r.get("title"):
            continue
        v = videos.setdefault(r["id"], {"id": r["id"], "title": r["title"], "channel": r.get("channel", ""), "views": 0,
                                        "url": f"https://www.youtube.com/watch?v={r['id']}", "short": r.get("is_short")})
        v["views"] = max(v["views"], r.get("views") or 0)
    for v in videos.values():
        v["concepts"] = [] if NOISE.search(v["title"]) else concepts_in(v["title"])

    items = []  # one row per comment / post: what real people said
    for d in rows(yt / "details.jsonl"):
        v = videos.get(d["id"])
        if not v or not v["concepts"]:
            continue  # comments under music videos etc. are noise
        for c in d.get("comments") or []:
            items.append(("YouTube", clean(c.get("text")), c.get("likes") or 0, v["title"], v["url"]))
    for r in rows(DATA / "reddit" / "raw.jsonl"):
        ctx = f"r/{r['sub']}: {r['title']}"
        items.append(("Reddit", clean(r["title"] + ". " + (r.get("body") or "")), 0, ctx, r["link"]))
        items += [("Reddit", clean(c), 0, ctx, r["link"]) for c in r.get("comments") or []]
    for r in rows(DATA / "x" / "posts.jsonl") + rows(DATA / "x" / "replies.jsonl"):
        if not r.get("text"):
            continue  # marker rows
        items.append(("X reply" if r.get("parent_id") else "X", clean(r["text"]), r.get("likes") or 0, "@" + r["account"], f"https://x.com/{r['account']}/status/{r['id']}"))

    hooks = {d["id"]: clean(d.get("hook")) for d in rows(yt / "details.jsonl") if d.get("hook")}
    for t in rows(yt / "transcripts.jsonl"):
        if t.get("hook"):
            hooks.setdefault(t["id"], clean(t["hook"]))
    return videos, items, hooks, rows(DATA / "exa" / "results.jsonl")


def analyse(videos, items, sources):
    C = {n: {"items": 0, "likes": 0, "questions": [], "videos": [], "x": [], "sources": [], "co": Counter()} for _, n, _, _ in CONCEPTS}
    seen = set()
    for src, text, likes, ctx, url in items:
        if not text:
            continue
        hits = concepts_in(text)
        for n in hits:
            c = C[n]; c["items"] += 1; c["likes"] += likes
            c["co"].update(h for h in hits if h != n)
            if src == "X" and len(text) > 40:
                c["x"].append((likes, text, ctx, url))
        key = text.lower()[:80]
        if hits and src != "X" and is_question(text, src) and key not in seen:  # account posts are brand voice, not audience
            seen.add(key)
            for n in hits:
                C[n]["questions"].append((likes, src, text, ctx, url))
    for v in videos.values():
        for n in v["concepts"]:
            C[n]["videos"].append(v)
    for s in sources:
        for n in concepts_in(f"{s.get('title', '')} {s.get('text', '')[:3000]}"):
            C[n]["sources"].append(s)
    for c in C.values():
        c["questions"].sort(key=lambda q: -q[0]); c["x"].sort(key=lambda q: -q[0])
        c["videos"].sort(key=lambda v: -v["views"])
        c["median_views"] = int(statistics.median([v["views"] for v in c["videos"]])) if c["videos"] else 0
    return C


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def concept_note(d, n, definition, c, total_videos_for):
    fm = (f"---\ntype: concept\ndomain: \"{d}\"\nmentions: {c['items']}\nquestions: {len(c['questions'])}\n"
          f"likes: {c['likes']}\nvideos: {len(c['videos'])}\nmedian_views: {c['median_views']}\ntags: [concept]\n---\n")
    out = [fm, f"# {n}\n", f"> {definition}\n", f"**Domain:** {link(d)}  ·  _generated by build_vault.py; add your own notes in `My Notes/`_\n"]
    related = [f"{link(r)} ({k})" for r, k in c["co"].most_common(8)]
    out.append("## Related concepts\nConcepts people mention in the same comment or post (count of co-mentions):\n\n" + (" · ".join(related) or "_none yet_") + "\n")
    out.append("## Demand\n| Mentions | Questions | Likes on mentions | Videos | Median views |\n|---|---|---|---|---|\n"
               f"| {c['items']} | {len(c['questions'])} | {c['likes']:,} | {len(c['videos'])} | {c['median_views']:,} |\n")
    qs = c["questions"][:25]
    out.append("## What the audience asks\n" + ("\n".join(f"- **{q[0]:,}♥ {q[1]}**: {md_escape(q[2])}  \n  _on: [{md_escape(q[3][:80])}]({q[4]})_" for q in qs) or "_no questions found_")
               + f"\n\nFull list: {link('Question Bank/' + d)}\n")
    if c["videos"]:
        out.append("## Proven videos (most viewed)\n" + "\n".join(f"- [{md_escape(v['title'])}]({v['url']}): {v['channel']}, {v['views']:,} views" for v in c["videos"][:8]) + "\n")
    if c["x"]:
        out.append("## What style voices on X say\n" + "\n".join(f"- **{x[0]:,}♥ {x[2]}**: {md_escape(x[1][:280])}" for x in c["x"][:5]) + "\n")
    if c["sources"]:
        out.append("## Research to cite\n" + "\n".join(f"- [{md_escape(s.get('title', 'source'))}]({s['url']}) ({(s.get('published') or '')[:4]})" for s in c["sources"][:6]) + "\n")
    ideas = [f"- {link('Formats/' + f)}: {t.format(c=n.lower())}" for f, t in FORMATS]
    ideas += [f"- {link('Formats/Answer the Comment')}: \"{md_escape(q[2][:160])}\"" for q in qs[:5]]
    out.append("## Idea seeds\n" + "\n".join(ideas) + "\n")
    return "\n".join(out)


def idea_rows(C):
    for d, n, _, _ in CONCEPTS:
        for f, t in FORMATS:
            yield [d, n, f, t.format(c=n.lower()), "", 0, "", ""]
        for likes, src, text, ctx, url in C[n]["questions"]:
            yield [d, n, "Answer the Comment", text, src, likes, ctx, url]


def canvas(C):
    nodes, edges, cols, gw, gh = [], [], 4, 1500, 1300
    for i, (d, items) in enumerate(ONTOLOGY.items()):
        x0, y0 = (i % cols) * (gw + 200), (i // cols) * (gh + 200)
        gid = f"g{i}"
        nodes.append({"id": gid, "type": "group", "label": d, "x": x0, "y": y0, "width": gw, "height": gh, "color": str(i % 6 + 1)})
        did = f"d{i}"
        nodes.append({"id": did, "type": "file", "file": f"Domains/{d}.md", "x": x0 + 550, "y": y0 + 40, "width": 400, "height": 120, "color": str(i % 6 + 1)})
        for j, (n, _, _) in enumerate(items):
            cid = f"c{i}_{j}"
            nodes.append({"id": cid, "type": "text", "text": f"[[{n}]]\n{len(C[n]['questions'])} questions · {C[n]['items']} mentions",
                          "x": x0 + 40 + (j % 4) * 360, "y": y0 + 260 + (j // 4) * 260, "width": 330, "height": 140})
            edges.append({"id": f"e{i}_{j}", "fromNode": did, "fromSide": "bottom", "toNode": cid, "toSide": "top"})
    return json.dumps({"nodes": nodes, "edges": edges}, indent=1)


def main():
    assert concepts_in("Is genuine leather actually bad?") == ["Leather Types"], concepts_in("Is genuine leather actually bad?")
    assert is_question("What colour shoes go with navy chinos?") and not is_question("What I learned: Brad Pitt is a handsome fella")
    assert NOISE.search("Na Ja - Pav Dharia (Official Video)")

    videos, items, hooks, sources = load()
    C = analyse(videos, items, sources)
    for g in GENERATED:  # only folders this script owns
        p = VAULT / g
        shutil.rmtree(p) if p.is_dir() else p.unlink(missing_ok=True)

    for d, n, _, definition in CONCEPTS:
        write(VAULT / "Concepts" / d / f"{n}.md", concept_note(d, n, definition, C[n], None))

    domain_stats = {}
    for d, members in ONTOLOGY.items():
        names = [n for n, _, _ in members]
        ranked = sorted(names, key=lambda n: -len(C[n]["questions"]))
        domain_stats[d] = (sum(C[n]["items"] for n in names), sum(len(C[n]["questions"]) for n in names), sum(C[n]["likes"] for n in names), sum(len(C[n]["videos"]) for n in names))
        body = [f"---\ntype: domain\ntags: [domain]\n---\n# {d}\n", f"Part of {link('Home')}. Concepts ranked by how many real questions people ask.\n",
                "| Concept | Questions | Mentions | Videos | Median views |\n|---|---|---|---|---|"]
        body += [f"| {link(n)} | {len(C[n]['questions'])} | {C[n]['items']} | {len(C[n]['videos'])} | {C[n]['median_views']:,} |" for n in ranked]
        body.append("\n## What each concept is\n" + "\n".join(f"- **{link(n)}**: {de}" for n, _, de in members))
        write(VAULT / "Domains" / f"{d}.md", "\n".join(body) + "\n")
        qb = [f"# Question Bank: {d}\n", f"Every real question found for {link(d)}, sorted by likes. Each one is a ready video idea ({link('Formats/Answer the Comment')}).\n"]
        for n in ranked:
            qs = C[n]["questions"]
            qb.append(f"\n## {link(n)} ({len(qs)})\n" + "\n".join(f"- {q[0]:,}♥ {q[1]}: {md_escape(q[2])}" for q in qs[:150]))
        write(VAULT / "Question Bank" / f"{d}.md", "\n".join(qb) + "\n")

    for f, t in FORMATS:
        write(VAULT / "Formats" / f"{f}.md", f"---\ntype: format\ntags: [format]\n---\n# {f}\n\nTemplate: {t.format(c='<concept>')}\n\n"
              f"Apply it to any concept in {link('Home')}. Every concept note lists this format under Idea seeds.\n")

    with (DATA / "tables" / "idea_bank.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh); w.writerow(["domain", "concept", "format", "idea", "source", "likes", "context", "url"])
        n_ideas = sum(1 for r in idea_rows(C) if w.writerow(r) or True)

    rel = [v for v in videos.values() if v["concepts"]]
    hk = sorted((v for v in rel if hooks.get(v["id"]) and len(hooks[v["id"]]) > 40), key=lambda v: -v["views"])[:80]
    write(VAULT / "Hook Library.md", "# Hook Library\nOpening words of the most-viewed on-topic videos (from transcripts). Study the first sentence.\n\n"
          + "\n".join(f"- **{v['views']:,} views** [{md_escape(v['title'])}]({v['url']}), {v['channel']}  \n  > {md_escape(' '.join(hooks[v['id']].split()[:45]))}  \n  {' '.join(link(n) for n in v['concepts'])}" for v in hk) + "\n")

    ch = defaultdict(list)
    for v in rel: ch[v["channel"]].append(v)
    top = sorted(ch.items(), key=lambda kv: -statistics.median(x["views"] for x in kv[1]) * min(len(kv[1]), 10))[:50]
    write(VAULT / "Creators.md", "# Creators to study\nRanked by median views x on-topic video count (capped at 10).\n\n| Channel | On-topic videos | Median views | Top concepts |\n|---|---|---|---|\n"
          + "\n".join(f"| {md_escape(c)} | {len(vs)} | {int(statistics.median(x['views'] for x in vs)):,} | {', '.join(link(n) for n, _ in Counter(n for x in vs for n in x['concepts']).most_common(3))} |" for c, vs in top) + "\n")

    allc = [n for _, n, _, _ in CONCEPTS]
    by_q = sorted(allc, key=lambda n: -len(C[n]["questions"]))
    gaps = sorted((n for n in allc if len(C[n]["questions"]) >= 10), key=lambda n: -len(C[n]["questions"]) / (len(C[n]["videos"]) + 1))[:15]
    home = ["# Fable Knowledge Vault\n",
            "An ontology of style knowledge built from the scrape: **domains → concepts → real audience questions → idea seeds**. "
            f"Open the graph view (colour = domain) or {link('Ontology.canvas')}. Raw ideas for a spreadsheet: `data/tables/idea_bank.csv` ({n_ideas:,} rows).\n",
            "## How ideas multiply\nEvery concept × every format (9) × every real question. Pick a concept, pick a format, answer one question = one post. "
            f"{len(allc)} concepts × {len(FORMATS)} formats = {len(allc) * len(FORMATS)} angles before using a single question.\n",
            "## Domains\n| Domain | Mentions | Questions | Likes | Videos |\n|---|---|---|---|---|"]
    home += [f"| {link(d)} | {s[0]:,} | {s[1]:,} | {s[2]:,} | {s[3]:,} |" for d, s in sorted(domain_stats.items(), key=lambda kv: -kv[1][1])]
    home.append("\n## Most-asked concepts (top 20)\n| Concept | Domain | Questions | Videos |\n|---|---|---|---|")
    home += [f"| {link(n)} | {DOMAIN_OF[n]} | {len(C[n]['questions'])} | {len(C[n]['videos'])} |" for n in by_q[:20]]
    home.append("\n## Gaps: many questions, few videos (content opportunities)\n| Concept | Questions | Videos |\n|---|---|---|")
    home += [f"| {link(n)} | {len(C[n]['questions'])} | {len(C[n]['videos'])} |" for n in gaps]
    home.append(f"\n## Also\n- {link('Hook Library')}: openings of top videos\n- {link('Creators')}: who to study\n- Question Bank: one note per domain\n- Formats: {' · '.join(link('Formats/' + f) for f, _ in FORMATS)}\n")
    home.append(f"\n_Data: {len(items):,} comments/posts, {len(rel):,} on-topic videos (of {len(videos):,}), {len(sources)} research sources. Rebuild: `python build_vault.py`._\n")
    write(VAULT / "Home.md", "\n".join(home))
    write(VAULT / "Ontology.canvas", canvas(C))

    gj = VAULT / ".obsidian" / "graph.json"
    gj.parent.mkdir(parents=True, exist_ok=True)
    groups = [{"query": f"path:\"Concepts/{d}\" OR path:\"Domains/{d}\"", "color": {"a": 1, "rgb": COLORS[i]}} for i, d in enumerate(ONTOLOGY)]
    g = json.loads(gj.read_text(encoding="utf-8")) if gj.exists() else {}
    g["colorGroups"] = groups
    write(gj, json.dumps(g, indent=2))
    print(f"items {len(items):,} | on-topic videos {len(rel):,}/{len(videos):,} | questions {sum(len(C[n]['questions']) for n in allc):,} (with overlaps) | ideas {n_ideas:,}")
    for d, s in domain_stats.items():
        print(f"  {d}: mentions {s[0]:,}, questions {s[1]:,}, videos {s[3]:,}")


if __name__ == "__main__":
    main()
