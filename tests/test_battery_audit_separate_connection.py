# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""The audit trail keeps its own connection, and says what became of what it recorded.

THE COLLISION. The audit and the application shared one database connection, and that made two
promises contradict each other. An audit record commits, because a record of what happened must
survive whatever happens next. A transaction rolls back, because that is the whole of what a
transaction promises. On one connection those are the same commit, so the audit's commit ended the
user's transaction and KEPT a write the user was about to undo. Measured on Postgres, MySQL and
SQLite alike: a [phi] field written inside a transaction that then failed was still in the table.
On Postgres it went further and crashed outright, because the audit left a transaction open and
the next `transaction` block cannot set its isolation inside one.

WHAT THIS ASSERTS, each case here because it was a real measured failure:
  1. a failed transaction leaves NO row, and an audit record marked rolled_back;
  2. a committed one leaves the row, and a record marked committed;
  3. a `transaction` opened right after an audited write does not crash (Postgres);
  4. MySQL writes an audit trail at all (its reader was built with the wrong quoting entirely);
  5. an audit connection restricted to INSERT can still write (the append-only role).

THE OUTCOME IS WHY THE RECORD IS HELD. Whether an operation committed is not knowable at the
moment it happens, so a record written then cannot state it. Holding the record until the
transaction resolves is also what dissolves the lock: SQLite allows one writer, so a separate
audit connection is refused while the application holds a write transaction, and waiting would
deadlock, since the transaction cannot end while the code blocks inside it.

REAL ENGINES OR NOTHING. Postgres, MySQL and the restricted-role case are skipped, loudly, when
the server is not reachable, never quietly passed. SQLite runs everywhere and is a FILE here, not
':memory:', because a second connection to ':memory:' is a different database.

Run: PYTHONPATH=$PWD python tests/test_battery_audit_separate_connection.py
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

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


TMP = tempfile.mkdtemp(prefix="mohio_auditconn_")
_n = [0]

PHI_IN_FAILED_TX = """shape Patient
    chart as text [phi]
    name as text
shape: done
connect db as {eng} from env.DATABASE_URL
transaction
    save to db.patients
        chart "111-11-1111"
        name "Ada"
    save: done
    sql
        INSERT INTO no_such_table_xyz (x) VALUES (1)
    sql: done
transaction: done
"""

PHI_IN_GOOD_TX = """shape Patient
    chart as text [phi]
    name as text
shape: done
connect db as {eng} from env.DATABASE_URL
transaction
    save to db.patients
        chart "222-22-2222"
        name "Bo"
    save: done
transaction: done
show "committed"
"""

TX_AFTER_AUDITED_WRITE = """shape Patient
    chart as text [phi]
    name as text
shape: done
connect db as {eng} from env.DATABASE_URL
save to db.patients
    chart "333-33-3333"
    name "Cy"
save: done
transaction
    save to db.patients
        chart "444-44-4444"
        name "Di"
    save: done
transaction: done
show "no crash"
"""

APPEND_ONLY_WRITE = """shape Patient
    chart as text [phi]
    name as text
shape: done
connect db as {eng} from env.DATABASE_URL
save to db.patients
    chart "555-55-5555"
    name "Eve"
save: done
show "saved"
"""


def run(src, url, eng, extra_env=None):
    """One real program, through `mio run`, the way a coder starts it."""
    _n[0] += 1
    path = os.path.join(TMP, "p%d.mho" % _n[0])
    io.open(path, "w", encoding="utf-8", newline="\n").write(src.format(eng=eng))
    env = dict(os.environ, PYTHONPATH=ROOT, DATABASE_URL=url,
               MOHIO_ENCRYPTION_KEY="testkey", PYTHONIOENCODING="utf-8")
    env.pop("MOHIO_AUDIT_DATABASE_URL", None)
    env.update(extra_env or {})
    r = subprocess.run([sys.executable, "-m", "mio", "run", path], cwd=ROOT, env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=300)
    return (r.stdout or "") + (r.stderr or "")


def outcomes(rows):
    """The `outcome` of every audit record read back, in order. An unreadable log yields NOTHING
    rather than an error string that a length check would then count as a record."""
    out = []
    for r in (rows if isinstance(rows, (list, tuple)) else []):
        try:
            out.append(json.loads(r[0]).get("outcome"))
        except Exception:
            pass
    return out


# -- SQLite, a FILE, and therefore everywhere ---------------------------------------------------
print("\n== sqlite: the transaction keeps its promise and the trail keeps its record ==")
import sqlite3  # noqa: E402

SQLITE_DB = os.path.join(TMP, "engine.db")


def sq(sql):
    try:
        return list(sqlite3.connect(SQLITE_DB).execute(sql))
    except Exception as e:
        return "ERR %s" % e


run(PHI_IN_FAILED_TX, SQLITE_DB, "sqlite")
check("a [phi] write in a FAILED transaction leaves no row",
      sq("select name from patients") == [], sq("select name from patients"))
check("...and leaves an audit record saying it rolled back",
      outcomes(sq("select detail from data_audit_log")) == ["rolled_back"],
      sq("select detail from data_audit_log"))

run(PHI_IN_GOOD_TX, SQLITE_DB, "sqlite")
check("a committed transaction keeps its row",
      [r[0] for r in (sq("select name from patients") or [])] == ["Bo"],
      sq("select name from patients"))
check("...and its record says committed",
      outcomes(sq("select detail from data_audit_log")) == ["rolled_back", "committed"],
      outcomes(sq("select detail from data_audit_log")))

out = run(TX_AFTER_AUDITED_WRITE, SQLITE_DB, "sqlite")
check("a transaction opened right after an audited write runs", "no crash" in out, out[-300:])

print("\n-- the record names the field, never its value, which the separation does not change --")
detail = " ".join(str(r[0]) for r in (sq("select detail from data_audit_log") or []))
check("no [phi] VALUE reached the trail",
      "111-11-1111" not in detail and "222-22-2222" not in detail, detail[:300])
check("...though the trail does name the operation it recorded", "patients" in detail, detail[:300])


# -- which connection the audit gets ------------------------------------------------------------
print("\n== which connection the audit gets ==")
from mohio_interpreter import DbRuntime  # noqa: E402

file_rt = DbRuntime(os.path.join(TMP, "twin.db"))
check("a real database gets a SECOND connection for the audit",
      file_rt.audit_sibling() is not file_rt)
check("...and the same twin every time, not one per write",
      file_rt.audit_sibling() is file_rt.audit_sibling())
check("...marked, so nothing mistakes it for the application's connection",
      getattr(file_rt.audit_sibling(), "_is_audit_twin", False))

mem_rt = DbRuntime(":memory:")
check("an in-memory database does NOT get one, because a second connection to ':memory:' is a "
      "different, empty database", mem_rt.audit_sibling() is mem_rt)

print("\n-- a held record is never dropped in silence --")
# A UNIT CASE, labelled as one, beside the real-program cases above. Every ordinary exit from a
# transaction flushes with a real outcome, and those paths are covered by the runs above. The one
# path that cannot be reached from a program is a commit or rollback that ITSELF throws, leaving
# the block while records are still held. What is asserted here is that the flush left behind for
# that case writes the record rather than discarding it.
from mohio_interpreter import MohioInterpreter  # noqa: E402


class HoldCtx(object):
    def __init__(self, db):
        self._db = db
        self._sector_compliance = None
        self._sector_profile = None

    def get_connection(self, name):
        return self._db

    def get(self, key):
        return None


held_db = DbRuntime(os.path.join(TMP, "held.db"))
held_it = MohioInterpreter()
held_it._pending_audit = [("operation_audit_log",
                           {"event": "DATA_CHANGE", "table": "patients"}, HoldCtx(held_db))]
held_it._flush_pending_audit("unknown")
held_rows = list(sqlite3.connect(os.path.join(TMP, "held.db")).execute(
    "select detail from operation_audit_log"))
check("a record held when the transaction itself failed to end is still written",
      len(held_rows) == 1, held_rows)
check("...and says 'unknown', because the transaction never said which",
      outcomes(held_rows) == ["unknown"], outcomes(held_rows))
check("...and nothing is left held afterwards", not held_it._pending_audit, held_it._pending_audit)

print("\n-- an audit connection can be pointed somewhere else entirely --")
os.environ["MOHIO_AUDIT_DATABASE_URL"] = os.path.join(TMP, "audit_elsewhere.db")
try:
    named = DbRuntime(":memory:")
    check("MOHIO_AUDIT_DATABASE_URL is honoured even for an in-memory application database",
          named.audit_sibling() is not named)
finally:
    os.environ.pop("MOHIO_AUDIT_DATABASE_URL", None)


# -- the append-only role's precondition: a complete table is never rebuilt ----------------------
print("\n== a table that is already complete is not created again ==")
from mohio_audit_grades import canonical_audit_columns  # noqa: E402

counting = DbRuntime(os.path.join(TMP, "count.db"))
ddl = []


class WatchedConnection(object):
    """The real connection, with every schema statement written down on the way through.
    A wrapper because sqlite3's own `execute` cannot be replaced on the object."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, *a, **k):
        up = str(sql).upper()
        if "CREATE TABLE" in up or "ALTER TABLE" in up:
            ddl.append(sql)
        return self._conn.execute(sql, *a, **k)

    def __getattr__(self, name):
        return getattr(self._conn, name)


counting.conn = WatchedConnection(counting.conn)

counting.ensure_table("widgets", ["name", "colour"])
first = len(ddl)
check("the first call builds the table", first >= 1, ddl)
counting.ensure_table("widgets", ["name", "colour"])
check("...a second call for the SAME columns issues no schema statement at all",
      len(ddl) == first, ddl[first:])
counting.ensure_table("widgets", ["name", "colour", "weight"], allow_new_columns=True)
check("...and a genuinely new column is still added",
      len(ddl) > first and "weight" in counting.table_columns("widgets"), ddl[first:])
check("the predicate says so directly",
      counting.schema_already_has("widgets", ["name", "colour"])
      and not counting.schema_already_has("widgets", ["name", "unheard_of"])
      and not counting.schema_already_has("no_such_table", ["name"]))
check("the audit log's own column list is what that check is made against for an audit table "
      "(17 named columns, plus the id every table gets, which is the canonical 18)",
      len(canonical_audit_columns()) == 17 and "entry_hash" in canonical_audit_columns(),
      canonical_audit_columns())


# -- real Postgres ------------------------------------------------------------------------------
print("\n== postgres ==")
PG = os.environ.get("MOHIO_TEST_PG", "postgresql://postgres:postgres@localhost:5432/postgres")
pg_ok = False
try:
    import psycopg2
    psycopg2.connect(PG, connect_timeout=3).close()
    pg_ok = True
except Exception as e:
    skip("postgres cases", str(e).splitlines()[0][:70])


def pg(sql, url=PG):
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


if pg_ok:
    for t in ("patients", "data_audit_log", "phi_audit_log", "operation_audit_log"):
        pg("DROP TABLE IF EXISTS %s CASCADE" % t)
    run(PHI_IN_FAILED_TX, PG, "postgres")
    rows = pg("select name from patients")
    check("postgres: a [phi] write in a failed transaction leaves no row",
          rows == [] or (isinstance(rows, str) and "does not exist" in rows), rows)
    check("postgres: ...and an audit record saying it rolled back",
          outcomes(pg("select detail from data_audit_log")) == ["rolled_back"],
          pg("select detail from data_audit_log"))

    run(PHI_IN_GOOD_TX, PG, "postgres")
    check("postgres: a committed transaction keeps its row",
          [r[0] for r in (pg("select name from patients") or [])] == ["Bo"],
          pg("select name from patients"))
    check("postgres: ...and its record says committed",
          outcomes(pg("select detail from data_audit_log")) == ["rolled_back", "committed"],
          outcomes(pg("select detail from data_audit_log")))

    out = run(TX_AFTER_AUDITED_WRITE, PG, "postgres")
    check("postgres: a transaction right after an audited write does not crash (it used to set "
          "the session's isolation inside a transaction the audit had left open)",
          "no crash" in out and "set_session" not in out, out[-400:])

    print("\n  -- a widened table is seen as widened, which SQLite already did and this did not --")
    # The column set is remembered so a field reference can be checked against the real schema.
    # SQLite forgot the entry whenever it touched the schema; Postgres and MySQL never did, and
    # it did not show because their own ensure_table read the schema directly every time. It
    # shows now, because ensure_table asks the remembered set whether there is anything to build.
    from mohio_interpreter import PostgresRuntime  # noqa: E402
    pg("DROP TABLE IF EXISTS widening_probe CASCADE")
    widened = PostgresRuntime(PG)
    widened.ensure_table("widening_probe", ["name"])
    check("postgres: the narrow table reads as narrow",
          widened.table_columns("widening_probe") == {"id", "name"},
          widened.table_columns("widening_probe"))
    widened.ensure_table("widening_probe", ["name", "weight"], allow_new_columns=True)
    check("postgres: ...and the widened one reads as widened, so a field that now exists is not "
          "refused", "weight" in (widened.table_columns("widening_probe") or set()),
          widened.table_columns("widening_probe"))
    pg("DROP TABLE IF EXISTS widening_probe CASCADE")


# -- the append-only audit role, on real Postgres -----------------------------------------------
print("\n== postgres: the audit connection as an append-only role ==")
role_ok = False
if pg_ok:
    probe = pg("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
    role_ok = bool(probe) and not isinstance(probe, str) and probe[0][0]
    if not role_ok:
        skip("append-only role case", "the test connection cannot create roles")
else:
    skip("append-only role case", "postgres unreachable")

if role_ok:
    APP_URL = PG.replace("postgres:postgres@", "mohio_app_r:app_pw@")
    AUDIT_URL = PG.replace("postgres:postgres@", "mohio_audit_r:audit_pw@")
    AUDIT_TABLES = ("phi_audit_log", "data_audit_log", "operation_audit_log")

    def _teardown():
        # THE ENVELOPE RELATIONS TOO. They live on the tenant database beside the data, so an
        # earlier part of this battery leaves them owned by the superuser that ran it, and the
        # restricted application role below cannot then alter what it does not own. That is a
        # fixture leftover rather than a finding, but the ownership it exposes is real and is
        # asserted a few lines down.
        for t in AUDIT_TABLES + ("patients", "mohio_audit_envelope",
                                 "mohio_audit_envelope_ack"):
            pg("DROP TABLE IF EXISTS %s CASCADE" % t)
        for r in ("mohio_app_r", "mohio_audit_r"):
            pg("DROP OWNED BY %s" % r)
            pg("DROP ROLE IF EXISTS %s" % r)

    _teardown()
    pg("CREATE ROLE mohio_app_r LOGIN PASSWORD 'app_pw'")
    pg("CREATE ROLE mohio_audit_r LOGIN PASSWORD 'audit_pw'")
    pg("GRANT USAGE ON SCHEMA public TO mohio_app_r, mohio_audit_r")
    pg("GRANT CREATE ON SCHEMA public TO mohio_app_r")
    cols = ", ".join('"%s" TEXT' % c for c in canonical_audit_columns())
    for t in AUDIT_TABLES:
        # The audit tables are built ONCE, by an administrator. The append-only role never
        # creates anything, which is the whole point of it.
        pg("CREATE TABLE %s (%s)" % (t, cols))
        pg("GRANT INSERT, SELECT ON %s TO mohio_audit_r" % t)
        # mohio_app_r is granted NOTHING on the audit tables.

    out = run(APPEND_ONLY_WRITE, APP_URL, "postgres", {"MOHIO_AUDIT_DATABASE_URL": AUDIT_URL})
    check("the program runs with the audit on its own restricted credential",
          "saved" in out and "permission denied" not in out, out[-400:])
    check("...the application's write landed",
          [r[0] for r in (pg("select name from patients") or [])] == ["Eve"],
          pg("select name from patients"))
    check("...and the audit record landed, written by a role that may only INSERT",
          pg("select count(*) from data_audit_log") == [(1,)],
          pg("select count(*) from data_audit_log"))

    # THE EVIDENCE BELONGS TO THE APPLICATION, NOT TO THE AUDIT. It commits in the same
    # transaction as the data, on the same connection, so it must be reachable by the role that
    # writes the data and it is deliberately not granted to the append-only audit role at all.
    # This says so out loud, because getting it the other way round would mean a regulated write
    # could not commit its own evidence.
    check("the application's own role created the evidence relation",
          pg("select tableowner from pg_tables where tablename = 'mohio_audit_envelope'")
          == [("mohio_app_r",)],
          pg("select tableowner from pg_tables where tablename = 'mohio_audit_envelope'"))
    check("...and the evidence for that write is in it",
          pg("select count(*) from mohio_audit_envelope") == [(1,)],
          pg("select count(*) from mohio_audit_envelope"))
    check("...and it was acknowledged, so nothing is owed",
          pg("select count(*) from mohio_audit_envelope_ack") == [(1,)],
          pg("select count(*) from mohio_audit_envelope_ack"))
    _got_env = pg("select count(*) from mohio_audit_envelope", AUDIT_URL)
    check("the audit's role cannot read the evidence either",
          isinstance(_got_env, str) and "permission denied" in _got_env, _got_env)

    print("\n  -- the isolation the separate credential buys, enforced by the database --")
    for verb, sql in (("read", "select count(*) from phi_audit_log"),
                      ("rewrite", "update phi_audit_log set event = 'forged'"),
                      ("scrub", "delete from phi_audit_log"),
                      ("drop", "drop table phi_audit_log")):
        got = pg(sql, APP_URL)
        check("the application's role cannot %s the audit trail" % verb,
              isinstance(got, str) and ("permission denied" in got or "must be owner" in got), got)
    got = pg("select name from patients", AUDIT_URL)
    check("the audit's role cannot read the application's data",
          isinstance(got, str) and "permission denied" in got, got)
    for verb, sql in (("rewrite", "update phi_audit_log set event = 'forged'"),
                      ("scrub", "delete from phi_audit_log")):
        got = pg(sql, AUDIT_URL)
        check("the audit's own role cannot %s what it wrote" % verb,
              isinstance(got, str) and "permission denied" in got, got)

    _teardown()


# -- real MySQL ---------------------------------------------------------------------------------
print("\n== mysql: its audit reader was built with the wrong quoting and wrote nothing ==")
MY = os.environ.get("MOHIO_TEST_MYSQL",
                    "mysql://mohio:mohio_test_pw@127.0.0.1:3307/mohio_meta_test")
my_ok = False
try:
    import pymysql
    import urllib.parse as _up
    _u = _up.urlparse(MY)
    pymysql.connect(host=_u.hostname, port=_u.port or 3306, user=_u.username,
                    password=_u.password, database=_u.path.lstrip("/"),
                    connect_timeout=4).close()
    my_ok = True
except Exception as e:
    skip("mysql cases", str(e).splitlines()[0][:70])


def my(sql):
    import pymysql
    import urllib.parse as _up
    u = _up.urlparse(MY)
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


if my_ok:
    for t in ("patients", "data_audit_log", "phi_audit_log", "operation_audit_log"):
        my("DROP TABLE IF EXISTS %s" % t)
    run(PHI_IN_FAILED_TX, MY, "mysql")
    check("mysql: a [phi] write in a failed transaction leaves no row",
          list(my("select name from patients") or []) == [], my("select name from patients"))
    check("mysql: ...and an audit record exists at all, which it did not before the quoting was "
          "made per-engine", len(outcomes(my("select detail from data_audit_log"))) == 1,
          my("select detail from data_audit_log"))
    check("mysql: ...and it says rolled back",
          outcomes(my("select detail from data_audit_log")) == ["rolled_back"],
          outcomes(my("select detail from data_audit_log")))

    run(PHI_IN_GOOD_TX, MY, "mysql")
    check("mysql: a committed transaction keeps its row",
          [r[0] for r in (my("select name from patients") or [])] == ["Bo"],
          my("select name from patients"))
    check("mysql: ...and its record says committed",
          outcomes(my("select detail from data_audit_log")) == ["rolled_back", "committed"],
          outcomes(my("select detail from data_audit_log")))


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
