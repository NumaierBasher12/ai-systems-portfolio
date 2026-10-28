"""Build the training tables and the auto format library from clips.db.

Run after adding rows to `reads`:  python -X utf8 build_training.py
Reads:  study/data/*/features.json (measured), table reads (by-eye labels)
Writes: table measures, views training / format_scoreboard, db/training.csv (one row per read clip,
        label + measurements), vault note Formats/Format Library (auto).md
"""
import csv, glob, json, os, sqlite3, statistics as st

DB = 'C:/dev/clipping/db/clips.db'
DATA = 'C:/dev/clipping/study/data'
OUT_CSV = 'C:/dev/clipping/db/training.csv'
NOTE = 'C:/CLIPPING VAULT/Formats/Format Library (auto).md'

c = sqlite3.connect(DB)
c.executescript('''
CREATE TABLE IF NOT EXISTS measures(
 clip_id TEXT PRIMARY KEY, views INTEGER, seconds REAL, cuts INTEGER, cuts_first3 INTEGER,
 first_word_s REAL, words_per_sec REAL, voiced_share REAL, face_area_pct REAL, face_y_mean REAL,
 split_screen_share REAL, split_y_median REAL, audio_first3_vs_median REAL, audio_peak_seconds INTEGER,
 audio_loudest_second INTEGER, audio_silence_share REAL, audio_dynamic_range_db REAL,
 first_words TEXT, first_words_n INTEGER, hook_question INTEGER, hook_digit INTEGER);
DROP VIEW IF EXISTS training;
CREATE VIEW training AS
 SELECT r.*, m.cuts, m.cuts_first3, m.first_word_s, m.words_per_sec, m.voiced_share, m.face_area_pct,
        m.face_y_mean, m.split_screen_share, m.split_y_median, m.audio_first3_vs_median,
        m.audio_loudest_second, m.audio_silence_share, m.audio_dynamic_range_db,
        m.first_words, m.first_words_n, m.hook_question, m.hook_digit
 FROM reads r LEFT JOIN measures m ON m.clip_id = r.clip_id;
DROP VIEW IF EXISTS format_scoreboard;
CREATE VIEW format_scoreboard AS
 SELECT format_id, side, COUNT(*) n, CAST(AVG(views) AS INT) avg_views, MAX(views) best
 FROM reads GROUP BY format_id, side;
''')

n = 0
for f in glob.glob(f'{DATA}/*/features.json'):
    d = json.load(open(f, encoding='utf-8'))
    cuts = d.get('cut_times') or []
    fw = d.get('first_words') or ''
    c.execute('INSERT OR REPLACE INTO measures VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (
        d['id'], d.get('views'), d.get('seconds'), d.get('cuts'), sum(1 for t in cuts if t < 3),
        d.get('first_word_s'), d.get('words_per_sec'), d.get('voiced_share'), d.get('face_area_pct'), d.get('face_y_mean'),
        d.get('split_screen_share'), d.get('split_y_median'), d.get('audio_first3_vs_median'), d.get('audio_peak_seconds'),
        d.get('audio_loudest_second'), d.get('audio_silence_share'), d.get('audio_dynamic_range_db'),
        fw, len(fw.split()), int('?' in fw), int(any(ch.isdigit() for ch in fw))))
    n += 1
c.commit()
print('measures rows', n)

cur = c.execute('SELECT * FROM training ORDER BY views DESC')
cols = [x[0] for x in cur.description]
rows = cur.fetchall()
with open(OUT_CSV, 'w', newline='', encoding='utf-8') as fh:
    w = csv.writer(fh)
    w.writerow(cols)
    w.writerows(rows)
print('training.csv rows', len(rows))


def med(v):
    v = [x for x in v if x is not None]
    return round(st.median(v), 2) if v else ''


def table(groups, head, row_fn):
    out = list(head)
    for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        out.append(row_fn(k, v))
    return out


R = [dict(zip(cols, r)) for r in rows]
fmt, hook, lay = {}, {}, {}
for r in R:
    fmt.setdefault(r['format_id'], []).append(r)
    hook.setdefault(r['hook_type'], []).append(r)
    lay.setdefault((r['layout'] or '').split(',')[0][:70], []).append(r)

lines = ['---', 'tags: [formats, auto]', 'up: "[[Format Catalog]]"', '---',
         '# Format Library (auto-generated, do not edit by hand)', '',
         'Source: `C:\\dev\\clipping\\db\\clips.db` tables `reads` (by-eye labels) and `measures` (from features.json). '
         'Rebuild: `python -X utf8 C:\\dev\\clipping\\db\\build_training.py`. Labels are SEEN by Claude; numbers are MEASURED. '
         'Views are lifetime views, not normalised for channel size or age (see Caveats).', '',
         f'Clips read so far: {len(R)}. Streamers covered: {len({r["streamer"] for r in R})}.', '',
         '## Formats (by-eye label, with measured medians)', '']
lines += table(fmt, [
    '| format | n | own | outside | med views | med sec | med cuts | med cuts in first 3 s | med first word (s) | med audio first 3 s vs typical | examples |',
    '|---|---|---|---|---|---|---|---|---|---|---|'],
    lambda k, v: f"| {k} | {len(v)} | {sum(1 for x in v if x['side'] == 'own')} | {sum(1 for x in v if x['side'] == 'outside')} | "
                 f"{int(st.median([x['views'] or 0 for x in v])):,} | {med([x['seconds'] for x in v])} | {med([x['cuts'] for x in v])} | "
                 f"{med([x['cuts_first3'] for x in v])} | {med([x['first_word_s'] for x in v])} | "
                 f"{med([x['audio_first3_vs_median'] for x in v])} | {', '.join(x['clip_id'] for x in v[:2])} |")
lines += ['', '## Hook types (label) with measured hook numbers', '']
lines += table(hook, [
    '| hook type | n | med views | med first word (s) | med cuts in first 3 s | med audio first 3 s vs typical | question in first words | digit in first words |',
    '|---|---|---|---|---|---|---|---|'],
    lambda k, v: f"| {k} | {len(v)} | {int(st.median([x['views'] or 0 for x in v])):,} | {med([x['first_word_s'] for x in v])} | "
                 f"{med([x['cuts_first3'] for x in v])} | {med([x['audio_first3_vs_median'] for x in v])} | "
                 f"{sum(1 for x in v if x['hook_question'])} | {sum(1 for x in v if x['hook_digit'])} |")
lines += ['', '## Layouts (first clause of the label, counts)', '', '| layout | n |', '|---|---|']
for k, v in sorted(lay.items(), key=lambda kv: -len(kv[1]))[:12]:
    lines.append(f'| {k} | {len(v)} |')
lines += ['', '## Caveats', '',
          '- Views are not normalised: a 10M clip from a huge channel and a 300K clip from a niche channel are not comparable one to one. '
          'Compare inside the same streamer (view `training` has `streamer` and `side`) before comparing formats across streamers.',
          '- The sample is the TOP clips by views, so this shows what the best clips look like, not what separates hits from flops. '
          'The flop and mid clips of the study queue are the control group still to be read.',
          '- Data gaps: xqc own channel returned CS2 guides (wrong channel); payo and buddha outside rows hold unrelated videos (alias matches).']
os.makedirs(os.path.dirname(NOTE), exist_ok=True)
open(NOTE, 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
print('note written')
