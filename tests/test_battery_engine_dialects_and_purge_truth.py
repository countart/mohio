# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Raw sql runs on every engine, and a warning about deletion tells the truth.

TWO BUGS THAT SHARE A SHAPE: code written for one situation and then assumed for all of them.

1. THE RAW SQL BLOCK WAS SQLITE-ONLY, on engines Mohio says it supports. Its executor reached
   for `conn.execute`, which is a sqlite3 convenience and nothing else, so a `sql` block on
   MySQL or MariaDB answered `Connection object has no attribute execute` and ran nothing at
   all. Postgres had its own branch beside it; everything else fell down the sqlite one.

   THE FIX IS ONE PATH, NOT A THIRD BRANCH. Every driver here has `conn.cursor()`, so that is
   the door all of them share, and each runtime states its own dialect: the mark it wants where
   a bound value goes, and how it marks a word as a name rather than a value.

   The same sqlite-only reach was in two more places, and both SWALLOWED the failure, which is
   worse than the loud one: the guard that refuses a match on a field whose older rows were
   never indexed, and the check for whether a sealed field has a searchable index at all. On
   MySQL neither ran, neither complained, and a match on a sealed field answered with nothing
   over rows that do hold the value.

2. A DELETION VERB WAS WARNED ABOUT AS IF IT DID NOTHING. `mio check` told every `cm.purge`
   that no data is deleted and that the block fails loud at runtime. Measured on a real
   database, `cm.purge from db.members / match id to "7"` REMOVES that row and leaves the
   others. A false safe on an erasure verb is worse than no warning: a coder trusts it and runs
   the erasure believing it is a no-op.

   The two forms differ and now say so. The `from` form deletes and gets no warning, because it
   is guarded where guarding belongs, at runtime: no `match` is refused as a table-wide erasure,
   and a match on any field other than the id is refused unless a per-deployment audit salt is
   set. The value form records the request and deletes nothing, which is worth saying because
   the word is the same and the effect is not.

   Its siblings were checked the same way, by running each one: `cm.lock` both records a legal
   hold AND enforces it, so its warning is gone; `cm.retain` and `cm.expire` record a policy and
   never act on it; `cm.report`, `cm.notify` and bare `notify` stop the program rather than
   skipping quietly.

Run: PYTHONPATH=$PWD python tests/test_battery_engine_dialects_and_purge_truth.py
"""
import io
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
            print("          " + str(detail)[:600])
        _failures.append(label)
    _p += bool(cond)
    _f += (not cond)


def skip(label, why):
    print("  [SKIP] " + label + "  (" + why + ")")
    _skipped.append(label + " -- " + why)


TMP = tempfile.mkdtemp(prefix="mohio_dialect_")
_n = [0]


def write(name, src):
    path = os.path.join(TMP, name)
    io.open(path, "w", encoding="utf-8", newline="\n").write(src)
    return path


def run_mho(src, db, extra_env=None):
    """One real program, through `mio run`, the way a coder starts it."""
    _n[0] += 1
    path = write("r%d.mho" % _n[0], src)
    env = dict(os.environ, PYTHONPATH=ROOT, DATABASE_URL=db,
               MOHIO_ENCRYPTION_KEY="testkey", PYTHONIOENCODING="utf-8")
    env.pop("MOHIO_AUDIT_SALT", None)
    env.update(extra_env or {})
    r = subprocess.run([sys.executable, "-m", "mio", "run", path], cwd=ROOT, env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=300)
    return (r.stdout or "") + (r.stderr or "")


def check_mho(src):
    _n[0] += 1
    path = write("c%d.mho" % _n[0], src)
    r = subprocess.run([sys.executable, "-m", "mio", "check", path], cwd=ROOT,
                       env=dict(os.environ, PYTHONPATH=ROOT, DATABASE_URL=":memory:",
                                MOHIO_ENCRYPTION_KEY="testkey", PYTHONIOENCODING="utf-8"),
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=300)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


# ── 1. raw sql, every engine ───────────────────────────────────────────────────────────────────
RAW = """connect db as {eng} from env.DATABASE_URL
sql
    CREATE TABLE IF NOT EXISTS rawprobe (id INT, label VARCHAR(50))
sql: done
sql
    INSERT INTO rawprobe (id, label) VALUES (1, 'one')
sql: done
sql
    INSERT INTO rawprobe (id, label) VALUES (2, 'two')
sql: done
sql
    UPDATE rawprobe SET label = 'ONE' WHERE id = 1
sql: done
sql
    DELETE FROM rawprobe WHERE id = 2
sql: done
sql
    SELECT id, label FROM rawprobe
sql: done
show _sql_result
"""


def sqlite_read(url, sql):
    try:
        return list(sqlite3.connect(url).execute(sql))
    except Exception as e:
        return "ERR %s" % e


def pg_read(url, sql):
    import psycopg2
    c = psycopg2.connect(url)
    c.autocommit = True
    cur = c.cursor()
    try:
        cur.execute(sql)
        return cur.fetchall() if cur.description else []
    except Exception as e:
        return "ERR %s" % str(e).splitlines()[0]
    finally:
        c.close()


def my_read(url, sql):
    import pymysql
    u = _up.urlparse(url)
    c = pymysql.connect(host=u.hostname, port=u.port or 3306, user=u.username,
                        password=u.password, database=u.path.lstrip("/"))
    c.autocommit(True)
    cur = c.cursor()
    try:
        cur.execute(sql)
        return cur.fetchall() if cur.description else []
    except Exception as e:
        return "ERR %s" % str(e).splitlines()[0]
    finally:
        c.close()


ENGINES = [("sqlite", "sqlite", os.path.join(TMP, "raw.db"), sqlite_read)]

PG = os.environ.get("MOHIO_TEST_PG", "postgresql://postgres:postgres@localhost:5432/postgres")
try:
    import psycopg2
    psycopg2.connect(PG, connect_timeout=3).close()
    ENGINES.append(("postgres", "postgres", PG, pg_read))
except Exception as e:
    skip("postgres raw sql", str(e).splitlines()[0][:70])

for _label, _port, _dbname in (("mysql", 3307, "mohio_meta_test"),
                               ("mariadb", 3306, "mohio_meta_maria")):
    _url = os.environ.get("MOHIO_TEST_%s" % _label.upper(),
                          "mysql://mohio:mohio_test_pw@127.0.0.1:%d/%s" % (_port, _dbname))
    try:
        import pymysql
        _u = _up.urlparse(_url)
        pymysql.connect(host=_u.hostname, port=_u.port or 3306, user=_u.username,
                        password=_u.password, database=_u.path.lstrip("/"),
                        connect_timeout=4).close()
        ENGINES.append((_label, "mysql", _url, my_read))
    except Exception as e:
        skip("%s raw sql" % _label, str(e).splitlines()[0][:70])

print("\n== a raw sql block runs on every engine Mohio says it supports ==")
for label, eng, url, read in ENGINES:
    read(url, "DROP TABLE IF EXISTS rawprobe")
    out = run_mho(RAW.format(eng=eng), url)
    check("%s: a create, two inserts, an update, a delete and a select all run"
          % label, "no attribute" not in out and "Error" not in out, out[-400:])
    rows = read(url, "SELECT id, label FROM rawprobe")
    check("%s: ...and the database holds exactly what they said" % label,
          [tuple(r) for r in (rows if isinstance(rows, (list, tuple)) else [])] == [(1, "ONE")],
          rows)
    check("%s: ...and the SELECT came back to the program as named fields" % label,
          "'label': 'ONE'" in out or '"label": "ONE"' in out, out[-300:])
    read(url, "DROP TABLE IF EXISTS rawprobe")

print("\n-- each runtime states its own dialect, rather than inheriting SQLite's --")
from mohio_interpreter import DbRuntime, PostgresRuntime, MySQLRuntime  # noqa: E402

check("SQLite asks for ? and quotes a name with double quotes",
      DbRuntime.sql_placeholder == "?" and DbRuntime.ident_quotes == ('"', '"'))
check("Postgres asks for %s", PostgresRuntime.sql_placeholder == "%s")
check("MySQL asks for %s and quotes a name with backticks, because a double-quoted word "
      "there is a STRING",
      MySQLRuntime.sql_placeholder == "%s" and MySQLRuntime.ident_quotes == ("`", "`"))
_rt = DbRuntime(os.path.join(TMP, "q.db"))
check("...and the quoting is asked of the runtime, not guessed from a table",
      _rt.quote_ident("members") == '"members"', _rt.quote_ident("members"))
check("every runtime can hand out a cursor, which is the door they all share",
      all(hasattr(c, "raw_cursor") for c in (DbRuntime, PostgresRuntime, MySQLRuntime)))

print("\n-- the two guards that DO NOT move --")
out = run_mho('sector: financial\nconnect db as sqlite from env.DATABASE_URL\n'
              'sql\n    SELECT 1\nsql: done\n', os.path.join(TMP, "sec.db"))
check("raw sql is still refused in a certified sector",
      "blocked_in_certified_sector" in out, out[-300:])

_txdb = os.path.join(TMP, "tx.db")
out = run_mho('connect db as sqlite from env.DATABASE_URL\n'
              'sql\n    CREATE TABLE IF NOT EXISTS keeps (id INT)\nsql: done\n'
              'transaction\n'
              '    sql\n        INSERT INTO keeps (id) VALUES (1)\n    sql: done\n'
              '    sql\n        INSERT INTO no_such_table_xyz (id) VALUES (2)\n    sql: done\n'
              'transaction: done\n', _txdb)
check("a raw write inside a failed transaction is still rolled back",
      sqlite_read(_txdb, "select id from keeps") == [], sqlite_read(_txdb, "select id from keeps"))


# ── 2. cm.purge tells the truth ────────────────────────────────────────────────────────────────
print("\n== what a purge warning says matches what a purge does ==")

PURGE_FROM = """shape Member
    id as text
    email as text [pii]
shape: done
connect db as sqlite from env.DATABASE_URL
save to db.members
    id "7"
    email "gone@example.com"
save: done
save to db.members
    id "8"
    email "stays@example.com"
save: done
cm.purge from db.members
    match id to "7"
    reason "right to erasure request"
cm.purge: done
show "purge ran"
"""

PURGE_VALUE = """shape Member
    id as text
    email as text [pii]
shape: done
connect db as sqlite from env.DATABASE_URL
save to db.members
    id "7"
    email "gone@example.com"
save: done
cm.purge member.id
    reason "right to erasure request"
cm.purge: done
show "purge ran"
"""

code, out = check_mho(PURGE_FROM)
check("the deleting form is no longer told that nothing is deleted",
      "NO data is actually deleted" not in out and "not yet executed" not in out, out[-500:])
check("...and checks clean, because it works", code == 0 and "warning" not in out.lower(),
      out[-400:])

_fromdb = os.path.join(TMP, "pfrom.db")
out = run_mho(PURGE_FROM, _fromdb)
left = sqlite_read(_fromdb, "select id from members")
check("...and it really does delete the matched row, and only that one",
      [r[0] for r in (left if isinstance(left, list) else [])] == [8], left)

code, out = check_mho(PURGE_VALUE)
check("the recording form says it RECORDS rather than deletes",
      "RECORDS an erasure request" in out, out[-500:])
check("...and names the form that does delete, so the fix is in the message",
      "cm.purge from db." in out, out[-500:])

_valdb = os.path.join(TMP, "pval.db")
out = run_mho(PURGE_VALUE, _valdb)
left = sqlite_read(_valdb, "select id from members")
check("...and it really does leave the row in place", [r[0] for r in (left or [])] == [7], left)

print("\n-- the runtime guards the deleting form, which is why check does not have to --")
out = run_mho("""shape Member
    id as text
shape: done
connect db as sqlite from env.DATABASE_URL
save to db.members
    id "7"
save: done
cm.purge from db.members
    reason "no scope at all"
cm.purge: done
""", os.path.join(TMP, "pnomatch.db"))
check("a purge with no match is refused as a table-wide erasure",
      "requires a `match`" in out, out[-400:])

_saltdb = os.path.join(TMP, "psalt.db")
out = run_mho("""shape Member
    id as text
    email as text [pii]
shape: done
connect db as sqlite from env.DATABASE_URL
save to db.members
    id "7"
    email "gone@example.com"
save: done
cm.purge from db.members
    match email to "gone@example.com"
    reason "right to erasure request"
cm.purge: done
""", _saltdb)
check("a purge matched on a field other than the id is refused without an audit salt, so the "
      "tombstone cannot name the erased row reversibly",
      "MOHIO_AUDIT_SALT" in out, out[-400:])
check("...and nothing was erased while it was refused",
      [r[0] for r in (sqlite_read(_saltdb, "select id from members") or [])] == [7],
      sqlite_read(_saltdb, "select id from members"))

_salted = os.path.join(TMP, "psalted.db")
out = run_mho("""shape Member
    id as text
    email as text [pii]
shape: done
connect db as sqlite from env.DATABASE_URL
save to db.members
    id "7"
    email "gone@example.com"
save: done
save to db.members
    id "8"
    email "stays@example.com"
save: done
cm.purge from db.members
    match email to "gone@example.com"
    reason "right to erasure request"
cm.purge: done
""", _salted, {"MOHIO_AUDIT_SALT": "a-per-deployment-salt"})
check("with the salt set, an erasure matched on a SEALED field reaches the right row, which is "
      "the encrypt-versus-match pair that silently matched nothing elsewhere",
      [r[0] for r in (sqlite_read(_salted, "select id from members") or [])] == [8],
      sqlite_read(_salted, "select id from members"))


# ── 3. the siblings: every other compliance verb says what it does ─────────────────────────────
print("\n== the sibling warnings, each checked against the verb it describes ==")

_lockdb = os.path.join(TMP, "lock.db")
out = run_mho("""shape Member
    id as text
shape: done
connect db as sqlite from env.DATABASE_URL
save to db.members
    id "7"
save: done
cm.lock members
cm.purge from db.members
    match id to "7"
    reason "right to erasure request"
cm.purge: done
""", _lockdb)
check("cm.lock really does place a hold: the erasure after it is refused",
      "legal hold" in out, out[-400:])
check("...and the row is still there", [r[0] for r in (sqlite_read(_lockdb, "select id from members") or [])] == [7],
      sqlite_read(_lockdb, "select id from members"))
code, out = check_mho('connect db as sqlite from env.DATABASE_URL\ncm.lock legal_case_123\n')
check("...so cm.lock is no longer warned about as if it did nothing",
      "cm.lock" not in out or "not yet executed" not in out, out[-400:])

code, out = check_mho('connect db as sqlite from env.DATABASE_URL\n'
                      'cm.retain user.email for 2 years\n'
                      'cm.expire user.token after 30 days\n')
check("cm.retain says it records a policy and does not act on it",
      "records a retention policy" in out, out[-500:])
check("cm.expire says the same about its own period",
      "records an expiry policy" in out, out[-500:])

code, out = check_mho('connect db as sqlite from env.DATABASE_URL\ncm.report "CTR" for filings\n')
check("cm.report says it will STOP the program, which is what it does",
      "STOP this program" in out, out[-400:])
code, out = check_mho('connect db as sqlite from env.DATABASE_URL\ncm.notify "breach detected"\n')
check("cm.notify says the same", "STOP this program" in out, out[-400:])

shutil.rmtree(TMP, ignore_errors=True)
if _skipped:
    print("\nNOT RUN (no engine reachable here, so these prove nothing today):")
    for line in _skipped:
        print("  ~ " + line)
if _failures:
    print("\nFAILURES, repeated so the suite runner's tail carries them:")
    for line in _failures:
        print("  * " + line)
print("\nRESULTS: " + str(_p) + " passed, " + str(_f) + " failed, "
      + str(len(_skipped)) + " skipped")
sys.exit(1 if _f else 0)
