# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""A batch write goes in one statement where the engine allows it, and it is recorded.

TWO THINGS, and the second is the one that matters most.

1. `save all` IS THE VERB THAT MEANS "WRITE THESE ROWS", AND IT WAS WRITING THEM ONE AT A TIME.
   Two thousand rows meant two thousand round trips. Measured on a real Postgres over loopback:
   about 1,080 rows a second before, about 3,300 after, with every id still returned. The bulk
   path is offered by the runtime and DECLINED by default, so a backend nobody has taught it to
   keeps the loop it always had and loses nothing.

   It is `save all` and not `repeat each ... save` on purpose. The coder has stated the batch, so
   there is no question of one row depending on the row before it. Deciding that for a general
   loop needs analysis this does not attempt.

2. A BULK WRITE OF TAGGED DATA WAS RECORDED NOWHERE. The audit records a change when a sector is
   active, when the table is already known to hold sensitive data, or when the fields being
   written are tagged. `save all` passed no field names, so the third test could not be true, and
   a batch of [phi] rows into a table nothing had written singly before produced NO audit record
   at all. Measured: five sealed rows landed in the table, and the trail held five records for
   the loop that built them, one for the read, and nothing for the write that mattered.

   This was NOT caused by the batching. Confirmed by running the same program against the code
   from before that change and getting the identical empty result.

WHAT IS NOT CHANGED HERE, deliberately. Rows are still prepared one at a time: a tagged field is
sealed per row, a non-record is refused per row, only the writing batches. Transaction semantics
are untouched, and a batch inside a transaction that fails is taken back whole.

STILL OPEN, and recorded in the backlog rather than assumed away: `save all` records ONE entry for
the batch, not one per row. Per-row records are the ruled default for regulated data, so that is a
build with a ruling attached, not a detail.

Run: PYTHONPATH=$PWD python tests/test_battery_bulk_save_and_batch_audit.py
"""
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import urllib.parse as _up

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_p = _f = 0
_failures = []
_skipped = []


def check(label, cond, detail=""):
    global _p, _f
    print("  [" + ("PASS" if cond else "FAIL") + "] " + label)
    if not cond:
        if detail:
            print("          " + str(detail)[:500])
        _failures.append(label)
    _p += bool(cond)
    _f += (not cond)


def skip(label, why):
    print("  [SKIP] " + label + "  (" + why + ")")
    _skipped.append(label + " -- " + why)


TMP = tempfile.mkdtemp(prefix="mohio_bulk_")
_n = [0]

PG = os.environ.get("MOHIO_TEST_PG", "postgresql://postgres:postgres@localhost:5432/postgres")
MY = os.environ.get("MOHIO_TEST_MYSQL",
                    "mysql://mohio:mohio_test_pw@127.0.0.1:3307/mohio_meta_test")
SQ = os.path.join(TMP, "bulk.db")


def sq(sql):
    # CLOSED, because the file is deleted between cases and Windows will not remove a database
    # something still holds open. Leaving that to the garbage collector is what made this
    # battery fail on its own second pass.
    conn = sqlite3.connect(SQ)
    try:
        return [tuple(r) for r in conn.execute(sql)]
    except Exception as e:
        return "ERR %s" % e
    finally:
        conn.close()


def pg(sql):
    import psycopg2
    c = psycopg2.connect(PG)
    c.autocommit = True
    cur = c.cursor()
    try:
        cur.execute(sql)
        return [tuple(r) for r in cur.fetchall()] if cur.description else []
    except Exception as e:
        return "ERR %s" % str(e).splitlines()[0]
    finally:
        c.close()


def my(sql):
    import pymysql
    u = _up.urlparse(MY)
    c = pymysql.connect(host=u.hostname, port=u.port or 3306, user=u.username,
                        password=u.password, database=u.path.lstrip("/"))
    c.autocommit(True)
    cur = c.cursor()
    try:
        cur.execute(sql)
        return [tuple(r) for r in cur.fetchall()] if cur.description else []
    except Exception as e:
        return "ERR %s" % str(e).splitlines()[0]
    finally:
        c.close()


ENGINES = [("sqlite", "sqlite", SQ, sq)]
try:
    import psycopg2
    psycopg2.connect(PG, connect_timeout=3).close()
    ENGINES.append(("postgres", "postgres", PG, pg))
except Exception as e:
    skip("postgres cases", str(e).splitlines()[0][:70])
try:
    import pymysql
    _u = _up.urlparse(MY)
    pymysql.connect(host=_u.hostname, port=_u.port or 3306, user=_u.username,
                    password=_u.password, database=_u.path.lstrip("/"),
                    connect_timeout=4).close()
    ENGINES.append(("mysql", "mysql", MY, my))
except Exception as e:
    skip("mysql cases", str(e).splitlines()[0][:70])


def wipe(label, rd):
    if label == "sqlite":
        for s in ("", "-wal", "-shm"):
            if os.path.exists(SQ + s):
                os.remove(SQ + s)
        return
    cascade = " CASCADE" if label == "postgres" else ""
    for t in ("patients", "src", "data_audit_log", "phi_audit_log"):
        rd("DROP TABLE IF EXISTS %s%s" % (t, cascade))


def run(src, url):
    _n[0] += 1
    path = os.path.join(TMP, "r%d.mho" % _n[0])
    io.open(path, "w", encoding="utf-8", newline="\n").write(src)
    env = dict(os.environ, PYTHONPATH=ROOT, DATABASE_URL=url,
               MOHIO_ENCRYPTION_KEY="testkey", MOHIO_MAX_RUN_SECONDS="0",
               MOHIO_MAX_LOOP_ITERATIONS="0", PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, "-m", "mio", "run", path], cwd=ROOT, env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=600)
    return (r.stdout or "") + (r.stderr or "")


BATCH = """shape Patient
    chart as text [phi]
    name as text
shape: done
connect db as {eng} from env.DATABASE_URL
counter 0
repeat 5 times
    counter (counter + 1)
    save to db.src
        chart "111-11-1111"
        name "Ada"
    save: done
repeat: done
find rows in db.src
find: done
save all to db.patients from rows
save: done
show "batched"
"""

BATCH_IN_FAILED_TX = """shape Patient
    chart as text [phi]
    name as text
shape: done
connect db as {eng} from env.DATABASE_URL
counter 0
repeat 5 times
    counter (counter + 1)
    save to db.src
        chart "111-11-1111"
        name "Ada"
    save: done
repeat: done
find rows in db.src
find: done
transaction
    save all to db.patients from rows
    save: done
    sql
        INSERT INTO no_such_table_xyz (x) VALUES (1)
    sql: done
transaction: done
"""


# -- the runtime contract -----------------------------------------------------------------------
print("\n== the bulk path is offered, and declining it is a real answer ==")
from mohio_interpreter import DbRuntime, PostgresRuntime, MySQLRuntime  # noqa: E402

check("every runtime is asked the same question", all(
    hasattr(c, "save_many") for c in (DbRuntime, PostgresRuntime, MySQLRuntime)))
_rt = DbRuntime(os.path.join(TMP, "decline.db"))
check("a runtime with no bulk form declines, rather than pretending",
      _rt.save_many("anything", [{"a": 1}]) is None)
check("...and an empty batch is an empty answer, not a decline",
      _rt.save_many("anything", []) == [])

# THE CORRECTNESS CASES BELOW PASS ON EITHER PATH, which is the point of a fallback and is also a
# blind spot: switching the bulk path off left every one of them green. So the primitive itself is
# exercised here, directly, and this is the case that goes red if the fast path stops being taken.
print("\n-- and on an engine that offers it, the bulk path really is the one used --")
_pg_live = any(lbl == "postgres" for lbl, _e, _u, _r in ENGINES)
if not _pg_live:
    skip("the bulk primitive itself", "postgres unreachable")
else:
    _prt = PostgresRuntime(PG)
    pg("DROP TABLE IF EXISTS bulk_probe CASCADE")
    _ids = _prt.save_many("bulk_probe", [{"body": "a"}, {"body": "b"}, {"body": "c"}])
    check("postgres writes a uniform batch in one statement and returns an id for every row",
          isinstance(_ids, list) and len(_ids) == 3, _ids)
    check("...and the rows are really there",
          pg("select body from bulk_probe order by id") == [("a",), ("b",), ("c",)],
          pg("select body from bulk_probe order by id"))
    check("...and the ids it returned are the ids that were written",
          sorted(_ids) == [r[0] for r in pg("select id from bulk_probe order by id")],
          (_ids, pg("select id from bulk_probe order by id")))
    _ragged = _prt.save_many("bulk_probe", [{"body": "d"}, {"body": "e", "extra": "x"}])
    check("a ragged batch DECLINES rather than writing a row under a column list that is not its "
          "own", _ragged is None, _ragged)
    check("...and declining wrote nothing",
          pg("select count(*) from bulk_probe") == [(3,)], pg("select count(*) from bulk_probe"))
    pg("DROP TABLE IF EXISTS bulk_probe CASCADE")


# -- every engine writes the batch, and seals what is tagged ------------------------------------
print("\n== the batch lands, and a tagged field is still sealed ==")
for label, eng, url, rd in ENGINES:
    wipe(label, rd)
    out = run(BATCH.format(eng=eng), url)
    check("%s: the batch runs" % label, "batched" in out, out[-300:])
    rows = rd("select chart from patients")
    check("%s: ...and every row landed" % label,
          isinstance(rows, list) and len(rows) == 5, rows)
    sealed = (isinstance(rows, list) and rows
              and all(str(r[0]).startswith("enc:v1:") for r in rows))
    check("%s: ...with the tagged field sealed, which per-row preparation still does" % label,
          sealed, rows[:1] if isinstance(rows, list) else rows)


# -- the audit records the batch ----------------------------------------------------------------
print("\n== a bulk write of tagged data is RECORDED, which it was not before ==")
for label, eng, url, rd in ENGINES:
    wipe(label, rd)
    run(BATCH.format(eng=eng), url)
    recs = rd("select event, detail from data_audit_log")
    entries = []
    for row in (recs if isinstance(recs, list) else []):
        try:
            entries.append(json.loads(row[1]))
        except Exception:
            pass
    batch_rec = [d for d in entries if d.get("operation") == "save_all"]
    # ONE RECORD PER ROW. This asserted a single record carrying a count, which is what the verb
    # used to write and what the per-line ruling replaced: a count answers how many rows were
    # written and the question asked afterwards is always about one of them. The stronger
    # per-row assertions live in their own section below; these keep the point that the write is
    # recorded AT ALL, which is what was missing entirely.
    check("%s: the batch write left records" % label, len(batch_rec) == 5, len(batch_rec))
    if batch_rec:
        d = batch_rec[0]
        check("%s: ...naming the table they went to" % label, d.get("table") == "patients", d)
        check("%s: ...and the FIELD NAMES, which is what makes the record happen at all" % label,
              "chart" in (d.get("fields") or []), d.get("fields"))
        check("%s: ...and never a value" % label,
              "111-11-1111" not in json.dumps(d), d)


# -- one record PER ROW, and the chain still holds ----------------------------------------------
print("\n== the records are per row, and the chain they form still verifies ==")
for label, eng, url, rd in ENGINES:
    wipe(label, rd)
    run(BATCH.format(eng=eng), url)
    recs = rd("select event, detail from data_audit_log")
    entries = []
    for row in (recs if isinstance(recs, list) else []):
        try:
            entries.append(json.loads(row[1]))
        except Exception:
            pass
    per_row = [d for d in entries if d.get("operation") == "save_all"]
    check("%s: five rows leave FIVE records, not one carrying a count" % label,
          len(per_row) == 5, len(per_row))
    check("%s: ...each naming the row it is about, which a count cannot do" % label,
          all(d.get("record_id") for d in per_row),
          [d.get("record_id") for d in per_row])
    check("%s: ...and still never a value" % label,
          "111-11-1111" not in json.dumps(per_row), per_row[:1])
    check("%s: ...while the old single count record is gone" % label,
          not any(d.get("count") == 5 and d.get("operation") == "save_all" for d in entries),
          [d for d in entries if d.get("operation") == "save_all"][:1])

print("\n-- the chain over a batched write, and what happens when one record is altered --")
_pg_live = any(lbl == "postgres" for lbl, _e, _u, _r in ENGINES)
if not _pg_live:
    skip("chain verification over a batch", "postgres unreachable")
else:
    def _verify():
        env = dict(os.environ, PYTHONPATH=ROOT, MOHIO_ENCRYPTION_KEY="testkey",
                   PYTHONIOENCODING="utf-8")
        env.pop("DATABASE_URL", None)
        r = subprocess.run([sys.executable, "-m", "mio", "audit", "verify", PG], cwd=ROOT,
                           env=env, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=600)
        return (r.stdout or "") + (r.stderr or "")

    wipe("postgres", pg)
    run(BATCH.format(eng="postgres"), PG)
    out = _verify()
    check("the chain over a batch written in one statement verifies",
          "data_audit_log" in out and "chain intact" in out, out[-300:])
    victim = pg("select audit_id from data_audit_log where detail like '%save_all%' "
                "order by audit_id limit 1 offset 2")
    if isinstance(victim, list) and victim:
        pg("update data_audit_log set event = 'TAMPERED' where audit_id = '%s'" % victim[0][0])
        out2 = _verify()
        check("...and altering ONE record inside that batch is still caught",
              "BROKEN" in out2, out2[-300:])
    wipe("postgres", pg)


# -- and the audit write really is ONE statement, not N ----------------------------------------
# THE RECORDS ARE IDENTICAL EITHER WAY, which is the design: chained in memory first, so writing
# them singly produces exactly the same rows and the same chain. That also means every case above
# passes whether or not the write batches, so none of them can tell. This one can: it counts what
# the sink was asked to do. Without it, a change that quietly un-batched the audit would be
# invisible, and the cost would come back with nothing to show it had.
print("\n== the audit write is one statement, and that is checked, not assumed ==")
if not _pg_live:
    skip("the batched audit write", "postgres unreachable")
else:
    from mohio_interpreter import MohioInterpreter as _MI

    _sink = PostgresRuntime(PG)
    pg("DROP TABLE IF EXISTS batch_audit_probe CASCADE")
    _calls = {"save_many": 0, "save": 0}
    _orig_many, _orig_save = PostgresRuntime.save_many, PostgresRuntime.save

    def _count_many(self, table, rows, _o=_orig_many):
        _calls["save_many"] += 1
        return _o(self, table, rows)

    def _count_save(self, table, fields, *a, **k):
        _calls["save"] += 1
        return _orig_save(self, table, fields, *a, **k)

    PostgresRuntime.save_many = _count_many
    PostgresRuntime.save = _count_save
    try:
        _rows = [{"audit_id": "a%d" % i, "ts": "t", "event": "E", "agent": "",
                  "detail": "{}"} for i in range(4)]
        _written = _MI()._audit_chained_save_many(_sink, "batch_audit_probe", _rows)
        check("four records reach the sink in ONE call, not four", _calls["save_many"] == 1,
              _calls)
        check("...and none of them went through the one-at-a-time door", _calls["save"] == 0,
              _calls)
        check("...while still producing four chained records",
              len(_written) == 4 and all(r.get("entry_hash") for r in _written), _written[:1])
        check("...each linked to the one before it, which is what makes the chain a chain",
              all(_written[i + 1]["prev_hash"] == _written[i]["entry_hash"]
                  for i in range(len(_written) - 1)),
              [(r["prev_hash"][:8], r["entry_hash"][:8]) for r in _written])
    finally:
        PostgresRuntime.save_many, PostgresRuntime.save = _orig_many, _orig_save
        _rel = getattr(_sink, "release_thread_connection", None)
        if callable(_rel):
            _rel()
        pg("DROP TABLE IF EXISTS batch_audit_probe CASCADE")


# -- a batch inside a failed transaction is taken back whole ------------------------------------
print("\n== a batch inside a transaction that fails is taken back whole ==")
for label, eng, url, rd in ENGINES:
    wipe(label, rd)
    run(BATCH_IN_FAILED_TX.format(eng=eng), url)
    rows = rd("select chart from patients")
    gone = (rows == [] or (isinstance(rows, str) and
                           ("does not exist" in rows or "doesn't exist" in rows)))
    check("%s: nothing from the batch survived" % label, gone, rows)


shutil.rmtree(TMP, ignore_errors=True)
if _skipped:
    print("\nNOT RUN (no engine reachable here):")
    for line in _skipped:
        print("  ~ " + line)
if _failures:
    print("\nFAILURES, repeated so the suite runner's tail carries them:")
    for line in _failures:
        print("  * " + line)
print("\nRESULTS: " + str(_p) + " passed, " + str(_f) + " failed, "
      + str(len(_skipped)) + " skipped")
sys.exit(1 if _f else 0)
