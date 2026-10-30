"""Chief of staff entry point (Task Scheduler / cron starts this).
  python chief/run_all.py            run queued jobs, one at a time
  python chief/run_all.py --dry      show what would run, change nothing
  python chief/run_all.py --health   print the health report
  python chief/run_all.py --selftest checks on a throwaway database and lock
Roles are added to ROLES as each phase is built: name -> (function(conn, cfg, job), needs_gpu)."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from chief import core  # noqa: E402
from cutter import cutter  # noqa: E402
from moments import finder  # noqa: E402
from review import review  # noqa: E402
from watch import channels, digest  # noqa: E402
from writer import writer  # noqa: E402

ROLES = {"watch": (channels.run_watch, False), "digest": (digest.run_digest, False),
         "moments": (finder.run_moments, False), "cutter": (cutter.run_cutter, False),  # Ollama loads/unloads its own model, so no VRAM gate
         "writer": (writer.run_writer, False), "review": (review.run_review, False),
         "ingest_review": (review.run_ingest_review, False)}  # later phases register here


def run(dry=False):
    cfg = core.config()
    if core.STOP.exists():
        core.log(f"STOP file present, not running: {core.STOP.read_text().strip()}")
        return 2
    conn = core.db()
    if dry:
        todo = conn.execute("SELECT id, role, tries FROM jobs WHERE status='todo' AND tries<?", (core.MAX_TRIES,)).fetchall()
        print(f"dry run: {len(todo)} job(s) would run, roles known: {sorted(ROLES) or 'none yet'}")
        for j in todo:
            print(f"  job {j['id']} role={j['role']} tries={j['tries']}")
        print(core.health(conn, cfg))
        return 0
    if not core.acquire_lock():
        core.log("another run is alive, exiting")
        return 3
    run_id = conn.execute("INSERT INTO runs(started, status) VALUES (?, 'running')", (core.now(),)).lastrowid
    conn.commit()
    done = failed = 0
    note = ""
    try:
        recovered = core.recover(conn)
        if recovered:
            core.log(f"recovered {recovered} job(s) left by a killed run")
        while True:
            if core.STOP.exists():
                note = "STOP appeared"
                break
            job = core.claim(conn)
            if not job:
                break
            handler = ROLES.get(job["role"])
            if not handler:
                core.finish(conn, job["id"], "failed", "unknown role")
                failed += 1
                continue
            func, needs_gpu = handler
            ok, why = core.resources_ok(cfg, needs_gpu)
            if not ok:  # leave it for the next run; no busy waiting
                core.finish(conn, job["id"], "todo", f"waiting: {why}")
                conn.execute("UPDATE jobs SET tries=tries-1 WHERE id=?", (job["id"],))
                conn.commit()
                note = f"waiting: {why}"
                break
            try:
                core.finish(conn, job["id"], "done", func(conn, cfg, job) or "")
                done += 1
            except core.StopRun as e:
                core.write_stop(str(e))
                core.finish(conn, job["id"], "todo", f"stopped: {e}")
                note = f"STOP: {e}"
                core.notify(f"Assistant stopped on a block: {e}", cfg)
                break
            except Exception as e:
                core.finish(conn, job["id"], "failed", f"{type(e).__name__}: {e}")
                core.log(f"job {job['id']} ({job['role']}) failed: {type(e).__name__}: {e}")
                failed += 1
    finally:
        core.release_lock()
        status = "ok" if not failed else "partial"
        conn.execute("UPDATE runs SET finished=?, status=?, note=? WHERE id=?",
                     (core.now(), status, f"{done} done, {failed} failed. {note}".strip(), run_id))
        conn.commit()
    core.log(f"run finished: {done} done, {failed} failed {note}")
    return 0


def brief():
    """7 AM: send the digest's short 'read of the day' to Telegram (or say plainly that the night run did not produce one)."""
    import re
    from datetime import date
    cfg, c = core.config(), core.config("sources.json")
    p = Path(c["digest_dir"]) / f"{date.today()}.md"
    if not p.exists():
        core.notify("Assistant: no digest this morning, the night run did not finish. See logs/chief.log", cfg)
        return
    text = p.read_text(encoding="utf-8")
    m = re.search(r"## Read of the day.*?\n(.*?)\n\n## ", text, re.S)
    body = (m.group(1) if m else text)[:900]
    if len(body) == 900:
        body = body.rsplit("\n", 1)[0]  # cut at a line end, never mid-sentence
    core.notify("Assistant morning digest:\n" + body + f"\n\nFull note: {p}", cfg)


def selftest():
    cfg = {"min_free_disk_gb": 1, "min_free_ram_gb": 0.1, "min_free_vram_gb": 0, "claude_calls_per_night": 2, "notify_telegram": False}
    tmp = Path(tempfile.mkdtemp())
    core.LOCK, core.STOP = tmp / "run.lock", tmp / "STOP"
    conn = core.db(":memory:")
    a = core.enqueue(conn, "x")
    assert core.claim(conn)["id"] == a and core.claim(conn) is None, "claim must hand a job out once"
    assert core.recover(conn) == 1 and core.claim(conn)["id"] == a, "a killed run's job must come back"
    core.finish(conn, a, "done", "ok")
    assert conn.execute("SELECT status FROM jobs WHERE id=?", (a,)).fetchone()[0] == "done"
    core.enqueue(conn, "y")
    for _ in range(core.MAX_TRIES):
        core.claim(conn)
        core.recover(conn)
    assert core.claim(conn) is None, "a job that keeps dying must stop after MAX_TRIES"
    assert core.acquire_lock() and not core.acquire_lock(), "second run must be refused"
    core.release_lock()
    core.LOCK.write_text("999999999")
    assert core.acquire_lock(), "a lock left by a dead process must be taken over"
    core.release_lock()
    core.write_stop("test")
    assert core.STOP.exists() and "test" in core.STOP.read_text()
    assert core.claude_left(conn, cfg) == 2
    core.record_claude_call(conn)
    assert core.claude_left(conn, cfg) == 1
    ok, why = core.resources_ok({**cfg, "min_free_disk_gb": 10**9}, False)
    assert not ok and "disk" in why
    # Voice Check rules
    voice = {"max_label_words": 8, "max_title_chars": 80, "blocked_words": ["nazi"]}
    d = {"label": "a b c d e f g h i", "title": 'He says "this exact line" now', "caption": "a nazi joke", "hooks": []}
    flags = writer.rule_flags(d, "he said something else entirely here", voice)
    assert "label too long" in flags and any("blocked topic: nazi" in f for f in flags) and any("quote not in the clip" in f for f in flags), flags
    assert writer.rule_flags({"label": "ok", "title": "t", "caption": "", "hooks": []}, "x", voice) == []
    # review file marks: one tick = recorded once, nothing ticked = ignored, two ticks = ignored, approved clip lands in the outbox
    review.OUTBOX = tmp / "outbox"
    conn.execute("INSERT INTO videos(id, platform, ext_id) VALUES (1,'youtube','abc')")
    conn.execute("INSERT INTO candidates(id, video_id, start, end, score, text) VALUES (7,1,0,30,5,'t'), (8,1,40,70,5,'t'), (9,1,80,110,5,'t')")
    clip = tmp / "01_0.mp4"
    clip.write_bytes(b"x")
    conn.execute("INSERT INTO clips(candidate_id, path, status) VALUES (7, ?, 'draft')", (str(clip),))
    conn.execute("INSERT INTO drafts(candidate_id, kind, text, flags) VALUES (7,'title','A title','')")
    marks = ("<!-- cand:7 -->\nDecision: [x] approve   [ ] change   [ ] reject\nNotes: nice\n\n## next\n"
             "<!-- cand:8 -->\nDecision: [ ] approve   [ ] change   [ ] reject\nNotes: \n\n## next\n"
             "<!-- cand:9 -->\nDecision: [x] approve   [x] change   [ ] reject\nNotes: \n")
    assert review.ingest_marks(conn, marks) == 1 and review.ingest_marks(conn, marks) == 0, "marks must be recorded exactly once"
    assert conn.execute("SELECT status FROM clips WHERE candidate_id=7").fetchone()[0] == "approved"
    assert list((tmp / "outbox").rglob("01_0.mp4")) and list((tmp / "outbox").rglob("01_0.txt")), "approved clip must reach the outbox"
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    elif "--brief" in sys.argv:
        brief()
    elif "--health" in sys.argv:
        print(core.health(core.db(), core.config()))
    else:
        if "--nightly" in sys.argv:  # queue tonight's sweep once, then run everything queued
            conn = core.db()
            today = core.now()[:10]
            from datetime import date
            tonight = ["ingest_review", "watch", "digest"] + (["review"] if date.today().weekday() == 6 else [])  # Sunday: new review file
            for role in tonight:
                if not conn.execute("SELECT 1 FROM jobs WHERE role=? AND created>=?", (role, today)).fetchone():
                    core.enqueue(conn, role)
        sys.exit(run(dry="--dry" in sys.argv))
