"""Measure which automatically measured features go with views, inside each streamer and side.

Views are not comparable across channels, so every clip gets a percentile rank inside its own
(streamer, side) group; each feature is then correlated (Spearman) with that percentile.
Run after build_training.py:  python -X utf8 measure_hooks.py
Reads:  table measures (clip features), by_streamer/*.csv (streamer, side)
Writes: vault note Formats/Hook Measurements (auto).md
"""
import csv, glob, os, sqlite3

DB = 'C:/dev/clipping/db/clips.db'
NOTE = 'C:/CLIPPING VAULT/Formats/Hook Measurements (auto).md'
MIN_GROUP = 8  # groups smaller than this have meaningless percentiles
FEATURES = ['cuts_first3', 'first_word_s', 'first_words_n', 'hook_question', 'hook_digit', 'words_per_sec',
            'voiced_share', 'face_area_pct', 'face_y_mean', 'audio_first3_vs_median', 'audio_silence_share',
            'audio_dynamic_range_db', 'seconds', 'cuts']


def ranks(v):
    order = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(x, y):
    n = len(x)
    if n < 10:
        return None
    rx, ry = ranks(x), ranks(y)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx) ** 0.5
    vy = sum((b - my) ** 2 for b in ry) ** 0.5
    return cov / (vx * vy) if vx and vy else None


group = {}
for p in glob.glob('C:/dev/clipping/by_streamer/*.csv'):
    s = os.path.basename(p)[:-4]
    if s.startswith('_'):
        continue
    for r in csv.DictReader(open(p, encoding='utf-8')):
        group[r['clip_id']] = (s, r['side'])

c = sqlite3.connect(DB)
cur = c.execute('SELECT * FROM measures')
cols = [x[0] for x in cur.description]
rows = [dict(zip(cols, r)) for r in cur.fetchall() if r[0] in group and r[1] is not None]

by = {}
for r in rows:
    by.setdefault(group[r['clip_id']], []).append(r)
usable = {k: v for k, v in by.items() if len(v) >= MIN_GROUP}
for v in usable.values():
    rk = ranks([x['views'] for x in v])
    for x, q in zip(v, rk):
        x['pct'] = (q - 1) / (len(v) - 1)

pool = [x for v in usable.values() for x in v]
lines = ['---', 'tags: [formats, auto, measurements]', 'up: "[[Format Library (auto)]]"', '---',
         '# Hook and clip measurements vs views (auto-generated)', '',
         f'Clips measured: {len(rows)}; in groups of at least {MIN_GROUP} (same streamer, same side): {len(pool)} in {len(usable)} groups. '
         'Method: each clip gets a view percentile inside its own (streamer, side) group; Spearman correlation of each feature with that percentile. '
         'FACT = measured. Correlation is not cause; the clips are mostly top-of-channel, so the range of views is narrow. '
         'Rebuild: `python -X utf8 C:\\dev\\clipping\\db\\measure_hooks.py`.', '',
         '## All groups pooled', '', '| feature | n | Spearman rho vs within-group view percentile |', '|---|---|---|']
res = []
for f in FEATURES:
    pairs = [(x[f], x['pct']) for x in pool if x.get(f) is not None]
    rho = spearman([a for a, _ in pairs], [b for _, b in pairs])
    res.append((f, len(pairs), rho))
for f, n, rho in sorted(res, key=lambda t: -abs(t[2] or 0)):
    lines.append(f'| {f} | {n} | {"" if rho is None else f"{rho:+.2f}"} |')

lines += ['', '## Own channel vs outside clips (same method, split by side)', '',
          '| feature | own n | own rho | outside n | outside rho |', '|---|---|---|---|---|']
for f in FEATURES:
    cells = []
    for side in ('own', 'outside'):
        pairs = [(x[f], x['pct']) for k, v in usable.items() if k[1] == side for x in v if x.get(f) is not None]
        rho = spearman([a for a, _ in pairs], [b for _, b in pairs])
        cells += [str(len(pairs)), '' if rho is None else f'{rho:+.2f}']
    lines.append(f'| {f} | ' + ' | '.join(cells) + ' |')

lines += ['', '## Reading guide', '',
          '- |rho| under about 0.15 is noise at these sample sizes; do not build rules on it.',
          '- Hook features: first_word_s (when the first word is spoken), cuts_first3 (cuts in the first 3 s), '
          'audio_first3_vs_median (loudness of the first 3 s against the clip typical level, dB), hook_question / hook_digit '
          '(question mark or digit in the first words).',
          '- Known measurement limits: cut counts and framing numbers are unreliable on letterboxed or split clips; '
          'audio is measured as signal, not listened to (see Lessons).']
os.makedirs(os.path.dirname(NOTE), exist_ok=True)
open(NOTE, 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
print('groups', len(usable), 'clips', len(pool))
for f, n, rho in sorted(res, key=lambda t: -abs(t[2] or 0))[:8]:
    print(f, n, None if rho is None else round(rho, 2))
