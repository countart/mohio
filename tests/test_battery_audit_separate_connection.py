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
  5. an audit connection restricted to INSERT can still write (the append-only role);
  6. a unique index an administrator built on prev_hash and audit_id, under any name, is the
     chain's protection: the first regulated write lands and Mohio sends no index statement, on
     SQLite, Postgres and MySQL with an INSERT-and-SELECT-only audit role, and on Oracle as the
     owner, watched by a schema DDL trigger (T1-AUDIT-RESTRICTED-ROLE-INDEX).

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

CAN_FAIL = [
    {"file": "mohio_interpreter.py",
     "find": "            if enriched.get('_audit_owed'):\n                self._relay_still_owed += 1\n",
     "replace": "            if enriched.get('_audit_owed'):\n                pass  # MUTATED: a still-owed record is counted nowhere\n",
     "note": "the relay says Nothing owed and exits 0 while the envelope stays owed"},
    # T1-AUDIT-RESTRICTED-ROLE-INDEX: the catalogue is asked, by column, before an index is created.
    {"file": "mohio_interpreter.py",
     "find": "            if callable(_has) and _has(log_name, col):\n",
     "replace": "            if False and callable(_has) and _has(log_name, col):  # MUTATED: never asks\n",
     "note": "the sink never asks for an existing index: the restricted roles cannot deliver, and "
             "an owner adds its own beside the administrator's"},
    {"file": "mohio_interpreter.py",
     "find": "        if unique:\n            _has = getattr(sink, 'has_unique_index_on', None)\n",
     "replace": "        if unique and col == 'prev_hash':  # MUTATED: audit_id is never asked\n"
                "            _has = getattr(sink, 'has_unique_index_on', None)\n",
     "note": "the guard covers prev_hash and not audit_id: the audit_id index is still created"},
    {"file": "mohio_interpreter.py",
     "find": "            if len(cols) == 1 and cols[0][2] is not None and str(cols[0][2]).lower() == "
             "str(col).lower():\n",
     "replace": "            if any(c[2] is not None and str(c[2]).lower() == str(col).lower() "
                "for c in cols):  # MUTATED: a wider index counts\n",
     "note": "sqlite takes a unique index on prev_hash with another column as the chain's own"},
    {"file": "mohio_interpreter.py",
     "find": "AND i.indnkeyatts = 1 AND i.indpred IS NULL",
     "replace": "AND i.indpred IS NULL",
     "note": "postgres takes a unique index on prev_hash with another column as the chain's own"},
    {"file": "mohio_interpreter.py",
     "find": "            groups.setdefault(str(ix), []).append(None if c is None else str(c).lower())\n"
             "        return any(cols == [str(col).lower()] for cols in groups.values())\n",
     "replace": "            groups.setdefault(str(ix), []).append(None if c is None else str(c).lower())\n"
                "        return any(str(col).lower() in cols for cols in groups.values())  # MUTATED\n",
     "note": "mysql takes a unique index on prev_hash with another column as the chain's own"},
    {"file": "mohio_interpreter.py",
     "find": "                groups.setdefault(str(ix), []).append(str(c).lower())\n        finally:\n"
             "            cur.close()\n        return any(cols == [str(col).lower()] for cols in groups.values())\n",
     "replace": "                groups.setdefault(str(ix), []).append(str(c).lower())\n        finally:\n"
                "            cur.close()\n        return any(str(col).lower() in cols for cols in groups.values())"
                "  # MUTATED\n",
     "note": "oracle takes a unique index on prev_hash with another column as the chain's own"},
    {"file": "mohio_interpreter.py",
     "find": "                if not unique or not callable(_has) or _has(log_name, col):\n"
             "                    return 'exists'\n",
     "replace": "                return 'exists'  # MUTATED: an index in the way counts as done\n",
     "note": "a non-unique index in the way of the chain's unique index counts as done, on sqlite "
             "by its name and on oracle by its column list"},
]

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
# A first write creates its table, so a rolled-back first write leaves no table at all (no table is
# created at connect, T1-DECLARED-TABLE-READ-TOLERANT): no table is no row too.
_rolled = sq("select name from patients")
check("a [phi] write in a FAILED transaction leaves no row",
      _rolled == [] or str(_rolled).startswith("ERR no such table"), _rolled)
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


# -- indexes an administrator built, under names of their own (T1-AUDIT-RESTRICTED-ROLE-INDEX) ----
print("\n== sqlite: the administrator's own unique indexes are the chain's, and none is added ==")
ADMIN_DB = os.path.join(TMP, "admin_indexed.db")
AUDIT_TABLES_ALL = ("phi_audit_log", "data_audit_log", "operation_audit_log")
_adm = sqlite3.connect(ADMIN_DB)
for t in AUDIT_TABLES_ALL:
    _adm.execute("CREATE TABLE %s (id INTEGER PRIMARY KEY, %s)"
                 % (t, ", ".join('"%s" TEXT' % c for c in canonical_audit_columns())))
    _adm.execute("CREATE UNIQUE INDEX adm_%s_p ON %s (prev_hash)" % (t, t))
    _adm.execute("CREATE UNIQUE INDEX adm_%s_a ON %s (audit_id)" % (t, t))
_adm.commit()
_adm.close()


def sq_admin(sql):
    try:
        c = sqlite3.connect(ADMIN_DB)
        try:
            out = list(c.execute(sql))
            c.commit()
            return out
        finally:
            c.close()
    except Exception as e:
        return "ERR %s" % e


_ix_sql = ("select name from sqlite_master where type = 'index' and tbl_name in "
           "('phi_audit_log', 'data_audit_log', 'operation_audit_log') order by name")
out = run(APPEND_ONLY_WRITE, ADMIN_DB, "sqlite")
check("sqlite: the first regulated write lands its record", "saved" in out
      and sq_admin("select count(*) from data_audit_log") == [(1,)],
      (sq_admin("select count(*) from data_audit_log"), out[-300:]))
check("sqlite: ...and no index was added beside the administrator's",
      [r[0] for r in (sq_admin(_ix_sql) or [])]
      == sorted("adm_%s_%s" % (t, k) for t in AUDIT_TABLES_ALL for k in ("a", "p")),
      sq_admin(_ix_sql))
_dup = sq_admin("insert into data_audit_log (prev_hash, audit_id, event) "
                "select prev_hash, 'another-id', 'forged' from data_audit_log")
check("sqlite: ...and a second record on the same predecessor is refused",
      isinstance(_dup, str) and "UNIQUE constraint failed" in _dup, _dup)

# A NON-EQUIVALENT index is not taken for one. A unique index on prev_hash together with another
# column allows two records on one predecessor, so Mohio still builds its own.
WIDE_DB = os.path.join(TMP, "wide_indexed.db")
_adm = sqlite3.connect(WIDE_DB)
for t in AUDIT_TABLES_ALL:
    _adm.execute("CREATE TABLE %s (id INTEGER PRIMARY KEY, %s)"
                 % (t, ", ".join('"%s" TEXT' % c for c in canonical_audit_columns())))
    _adm.execute("CREATE UNIQUE INDEX wide_%s_p ON %s (prev_hash, event)" % (t, t))
    _adm.execute("CREATE UNIQUE INDEX part_%s_a ON %s (audit_id) WHERE event = 'x'" % (t, t))
_adm.commit()
_adm.close()
run(APPEND_ONLY_WRITE, WIDE_DB, "sqlite")
_wide = [r[0] for r in sqlite3.connect(WIDE_DB).execute(
    "select name from sqlite_master where type = 'index' and tbl_name = 'data_audit_log'")]
check("sqlite: a unique index on prev_hash WITH another column is not taken as the chain's, so "
      "Mohio builds its own", "ux_data_audit_log_prev_hash" in _wide, _wide)
check("sqlite: ...nor is a PARTIAL unique index on audit_id",
      "ux_data_audit_log_audit_id" in _wide, _wide)

# AN INDEX IN THE WAY IS NOT THE ONE ASKED FOR. A non-unique index under the very name Mohio gives
# its unique one used to count as done, and the chain then ran with no predecessor uniqueness.
SQUAT_DB = os.path.join(TMP, "squat_indexed.db")
_adm = sqlite3.connect(SQUAT_DB)
for t in AUDIT_TABLES_ALL:
    _adm.execute("CREATE TABLE %s (id INTEGER PRIMARY KEY, %s)"
                 % (t, ", ".join('"%s" TEXT' % c for c in canonical_audit_columns())))
    _adm.execute("CREATE INDEX ux_%s_prev_hash ON %s (prev_hash)" % (t, t))
_adm.commit()
_adm.close()
out = run(APPEND_ONLY_WRITE, SQUAT_DB, "sqlite")
check("sqlite: a NON-unique index under the chain index's own name is not taken as done: the record "
      "is not written to an unprotected chain",
      list(sqlite3.connect(SQUAT_DB).execute("select count(*) from data_audit_log")) == [(0,)],
      list(sqlite3.connect(SQUAT_DB).execute("select count(*) from data_audit_log")))
check("sqlite: ...and the reason is said, with the fix",
      "does not hold it to one row" in out and "Make that index unique on prev_hash alone" in out,
      out[-600:])


# -- real Postgres ------------------------------------------------------------------------------
print("\n== postgres ==")
PG = os.environ.get("MOHIO_TEST_PG", "postgresql://nobody:none@127.0.0.1:1/none")
import sys as _sys_guard
_sys_guard.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
# OUR TEST CONTAINERS ONLY. _engine_isolation refuses any server that is not this project's own test
# container; it is not shipped. A licensee runs this test against their own engine through the env
# var above (a throwaway database: the test drops and creates its own tables), or it skips.
try:
    import _engine_isolation as _iso_guard
except ImportError:
    _iso_guard = None
if _iso_guard is not None:
    _iso_guard.require_live_url(PG, "postgres")

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

    print("\n  -- a unique index that is not the chain's is not taken for it --")
    # On prev_hash WITH another column, or on audit_id only WHERE a condition holds: each allows two
    # records the chain must refuse, so the owner builds its own (T1-AUDIT-RESTRICTED-ROLE-INDEX).
    for t in ("patients", "mohio_audit_envelope", "mohio_audit_envelope_ack") + AUDIT_TABLES_ALL:
        pg("DROP TABLE IF EXISTS %s CASCADE" % t)
    for t in AUDIT_TABLES_ALL:
        pg("CREATE TABLE %s (id SERIAL PRIMARY KEY, %s)"
           % (t, ", ".join('"%s" TEXT' % c for c in canonical_audit_columns())))
        pg("CREATE UNIQUE INDEX wide_%s_p ON %s (prev_hash, event)" % (t, t))
        pg("CREATE UNIQUE INDEX part_%s_a ON %s (audit_id) WHERE event = 'x'" % (t, t))
    run(APPEND_ONLY_WRITE, PG, "postgres")
    _wide = [r[0] for r in (pg("select indexname from pg_indexes where tablename = 'data_audit_log'")
                            or [])]
    check("postgres: a unique index on prev_hash WITH another column is not taken as the chain's, "
          "so Mohio builds its own", "ux_data_audit_log_prev_hash" in _wide, _wide)
    check("postgres: ...nor is a PARTIAL unique index on audit_id",
          "ux_data_audit_log_audit_id" in _wide, _wide)
    check("postgres: ...and the record still lands",
          pg("select count(*) from data_audit_log") == [(1,)], pg("select count(*) from data_audit_log"))


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
    def _swap_creds(url, user, pw):
        # A literal string .replace("postgres:postgres@", ...) broke SILENTLY the moment PG's
        # own superuser password changed (343318a moved PG off the forbidden native port onto
        # the mohio-pg container, whose password is "mohiotest", not "postgres") -- the
        # replace then matched nothing, APP_URL/AUDIT_URL stayed IDENTICAL to the superuser PG,
        # and every "the restricted role cannot ..." check below passed only because it was
        # secretly still running as the superuser. Parsing the URL properly and replacing just
        # the userinfo survives ANY future credential change, including a user-supplied
        # MOHIO_TEST_PG with its own password.
        import urllib.parse as _up
        p = _up.urlsplit(url)
        host = p.hostname or ""
        if p.port:
            host += ":" + str(p.port)
        netloc = f"{user}:{pw}@{host}"
        return _up.urlunsplit((p.scheme, netloc, p.path, p.query, p.fragment))

    APP_URL = _swap_creds(PG, "mohio_app_r", "app_pw")
    AUDIT_URL = _swap_creds(PG, "mohio_audit_r", "audit_pw")
    AUDIT_TABLES = ("phi_audit_log", "data_audit_log", "operation_audit_log")

    def _teardown():
        # THE ENVELOPE RELATIONS TOO. They live on the tenant database beside the data, so an
        # earlier part of this battery leaves them owned by the superuser that ran it, and the
        # restricted application role below cannot then alter what it does not own. That is a
        # fixture leftover rather than a finding, but the ownership it exposes is real and is
        # asserted a few lines down.
        # mohio_key_check too (HC-110, 2026-10-12): the first encrypted write proves its key against
        # it, and one an earlier superuser leg created is not the application role's to use.
        for t in AUDIT_TABLES + ("patients", "mohio_audit_envelope",
                                 "mohio_audit_envelope_ack", "mohio_key_check"):
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
        # ...and its indexes, as the administrator must: the chain needs a unique index on
        # prev_hash and audit_id, and a role that may only INSERT cannot create one on a table it
        # does not own. Mohio asks the catalogue, by column, for one already there before it
        # creates its own (T1-AUDIT-RESTRICTED-ROLE-INDEX, built 2026-11-08).
        pg("CREATE UNIQUE INDEX admin_%s_prev ON %s (prev_hash)" % (t, t))
        pg("CREATE UNIQUE INDEX admin_%s_aid ON %s (audit_id)" % (t, t))
        # mohio_app_r is granted NOTHING on the audit tables.

    out = run(APPEND_ONLY_WRITE, APP_URL, "postgres", {"MOHIO_AUDIT_DATABASE_URL": AUDIT_URL})
    check("the program runs with the audit on its own restricted credential",
          "saved" in out and "permission denied" not in out, out[-400:])
    check("...the application's write landed",
          [r[0] for r in (pg("select name from patients") or [])] == ["Eve"],
          pg("select name from patients"))
    # T1-AUDIT-RESTRICTED-ROLE-INDEX, BUILT 2026-11-08. A role that may only INSERT and SELECT on audit
    # tables an administrator owns could not deliver: the sink created its chain indexes first and
    # Postgres answered `must be owner` before it looked for the administrator's own. The sink now asks
    # the catalogue, by column, for a unique index already holding prev_hash and audit_id, and sends no
    # statement when one is there. This role can run no DDL at all, so a record in the trail is itself
    # the proof that none was sent: any attempt would have refused the delivery and left it owed.
    check("...and the audit record is IN THE TRAIL on the first write, with no index created by a role "
          "that cannot create one",
          pg("select count(*) from data_audit_log") == [(1,)],
          pg("select count(*) from data_audit_log"))
    check("...and the only indexes on the audit tables are the administrator's",
          sorted(r[0] for r in (pg("select indexname from pg_indexes where tablename in "
                                   "('phi_audit_log','data_audit_log','operation_audit_log')") or []))
          == sorted("admin_%s_%s" % (t, k) for t in AUDIT_TABLES for k in ("prev", "aid")),
          pg("select indexname from pg_indexes where tablename in "
             "('phi_audit_log','data_audit_log','operation_audit_log')"))
    _dup = pg("insert into data_audit_log (prev_hash, audit_id, event) "
              "select prev_hash, 'another-id', 'forged' from data_audit_log")
    check("...and chain uniqueness holds: a second record claiming the same predecessor is refused "
          "by the administrator's index", isinstance(_dup, str) and "duplicate key" in _dup, _dup)
    _dup = pg("insert into data_audit_log (prev_hash, audit_id, event) "
              "select 'another-prev', audit_id, 'forged' from data_audit_log")
    check("...and one record per audit id holds the same way",
          isinstance(_dup, str) and "duplicate key" in _dup, _dup)

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
    check("...and it is acknowledged, because the record was delivered",
          pg("select count(*) from mohio_audit_envelope_ack") == [(1,)],
          pg("select count(*) from mohio_audit_envelope_ack"))
    # RELAY HONESTY (RULED by Ron 2026-10-17): the relay, run as the same roles, says what is owed from
    # what it finds. Nothing is owed now, so it says so and exits 0, and it never prints a password.
    _renv = dict(os.environ, PYTHONPATH=ROOT, MOHIO_ENCRYPTION_KEY="testkey", PYTHONIOENCODING="utf-8",
                 MOHIO_AUDIT_DATABASE_URL=AUDIT_URL)
    _renv.pop("DATABASE_URL", None)
    _rp = os.path.join(TMP, "relay_prog.mho")
    io.open(_rp, "w", encoding="utf-8", newline="\n").write(APPEND_ONLY_WRITE.format(eng="postgres"))
    _rr = subprocess.run([sys.executable, "-m", "mio", "audit", "relay", APP_URL, "--program", _rp],
                         cwd=ROOT, env=_renv, capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=300)
    _rout = (_rr.stdout or "") + (_rr.stderr or "")
    check("the relay finds nothing owed and exits 0",
          _rr.returncode == 0 and "Nothing owed" in _rout and "owed, delivery failed" not in _rout,
          (_rr.returncode, _rout[-500:]))
    check("...and it names the store without its password",
          "app_pw" not in _rout and "audit_pw" not in _rout, _rout[-400:])
    check("...and the trail still holds exactly the one record",
          pg("select count(*) from data_audit_log") == [(1,)],
          pg("select count(*) from data_audit_log"))
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

    print("\n  -- with no administrator index, the restricted role still cannot index, and says so --")
    # The other side of the same question: the catalogue holds no unique index on the column, so the
    # sink must create one, and this role cannot. The record is owed, never lost and never faked.
    pg("DROP INDEX admin_data_audit_log_prev")
    pg("DROP INDEX admin_data_audit_log_aid")
    out = run(APPEND_ONLY_WRITE, APP_URL, "postgres", {"MOHIO_AUDIT_DATABASE_URL": AUDIT_URL})
    check("a second write with the administrator's indexes gone commits its row",
          sorted(r[0] for r in (pg("select name from patients") or [])) == ["Eve", "Eve"],
          pg("select name from patients"))
    check("...and its record is OWED, not in the trail",
          pg("select count(*) from data_audit_log") == [(1,)]
          and pg("select count(*) from mohio_audit_envelope") == [(2,)]
          and pg("select count(*) from mohio_audit_envelope_ack") == [(1,)],
          (pg("select count(*) from data_audit_log"), pg("select count(*) from mohio_audit_envelope"),
           pg("select count(*) from mohio_audit_envelope_ack")))
    # RELAY HONESTY (RULED by Ron 2026-10-17): it tries the owed record, cannot deliver it, and says
    # so: "1 owed, delivery failed: <reason>", exit non-zero. Never "Nothing owed", never a password.
    _rr = subprocess.run([sys.executable, "-m", "mio", "audit", "relay", APP_URL, "--program", _rp],
                         cwd=ROOT, env=_renv, capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=300)
    _rout = (_rr.stdout or "") + (_rr.stderr or "")
    check("the relay reports the record still owed, with the reason, and exits non-zero",
          _rr.returncode != 0 and "1 owed, delivery failed:" in _rout and "must be owner" in _rout,
          (_rr.returncode, _rout[-500:]))
    check("...never `Nothing owed` while an envelope is owed",
          "Nothing owed" not in _rout, _rout[-400:])
    check("...and it names the store without its password",
          "app_pw" not in _rout and "audit_pw" not in _rout, _rout[-400:])
    check("...and the record is still owed after it, not acknowledged by the attempt",
          pg("select count(*) from mohio_audit_envelope_ack") == [(1,)],
          pg("select count(*) from mohio_audit_envelope_ack"))

    _teardown()


# -- real MySQL ---------------------------------------------------------------------------------
print("\n== mysql: its audit reader was built with the wrong quoting and wrote nothing ==")
MY = os.environ.get("MOHIO_TEST_MYSQL",
                    "mysql://nobody:none@127.0.0.1:1/none")
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



# -- MySQL: the audit connection as an INSERT-and-SELECT-only user ------------------------------
print("\n== mysql: an audit user that may only INSERT and SELECT, on indexes an administrator built ==")


def _url_with_user(url, user, pw):
    """The same server and database, signed in as another user (the userinfo replaced, nothing else)."""
    import urllib.parse as _up
    p = _up.urlsplit(url)
    host = p.hostname or ""
    if p.port:
        host += ":" + str(p.port)
    return _up.urlunsplit((p.scheme, "%s:%s@%s" % (user, pw, host), p.path, p.query, p.fragment))


def _mysql_as(url, sql):
    """One statement, signed in as the user the URL names."""
    import pymysql
    import urllib.parse as _up
    u = _up.urlparse(url)
    try:
        c = pymysql.connect(host=u.hostname, port=u.port or 3306, user=u.username,
                            password=u.password, database=u.path.lstrip("/"))
    except Exception as e:
        return "ERR %s" % str(e).splitlines()[0]
    try:
        cur = c.cursor()
        cur.execute(sql)
        return cur.fetchall() if cur.description else []
    except Exception as e:
        return "ERR %s" % str(e).splitlines()[0]
    finally:
        c.close()


MY_SCRATCH = ("patients", "mohio_audit_envelope", "mohio_audit_envelope_ack", "mohio_key_check")

if my_ok:
    import uuid as _uuid
    import urllib.parse as _up
    MY_AUDIT_USER = "mohio_aud_" + _uuid.uuid4().hex[:8]     # server-wide, so never a fixed name
    MY_AUDIT_URL = _url_with_user(MY, MY_AUDIT_USER, "audit_pw")
    MY_DB = _up.urlparse(MY).path.lstrip("/")
    for t in MY_SCRATCH + AUDIT_TABLES_ALL:
        my("DROP TABLE IF EXISTS %s" % t)
    for t in AUDIT_TABLES_ALL:
        # Built once, by an administrator, as on Postgres: TEXT columns, indexed by a key prefix.
        my("CREATE TABLE %s (%s)" % (t, ", ".join("`%s` TEXT" % c for c in canonical_audit_columns())))
        my("CREATE UNIQUE INDEX adm_%s_p ON %s (prev_hash(255))" % (t, t))
        my("CREATE UNIQUE INDEX adm_%s_a ON %s (audit_id(255))" % (t, t))
    my("CREATE USER '%s'@'%%' IDENTIFIED BY 'audit_pw'" % MY_AUDIT_USER)
    try:
        for t in AUDIT_TABLES_ALL:
            my("GRANT INSERT, SELECT ON `%s`.`%s` TO '%s'@'%%'" % (MY_DB, t, MY_AUDIT_USER))
        out = run(APPEND_ONLY_WRITE, MY, "mysql", {"MOHIO_AUDIT_DATABASE_URL": MY_AUDIT_URL})
        check("mysql: the program runs with the audit on its own restricted user",
              "saved" in out and "denied" not in out, out[-400:])
        _n_rec = my("select count(*) from data_audit_log")
        check("mysql: ...and the record is in the trail on the first write (this user cannot create "
              "an index, so none was sent)",
              not isinstance(_n_rec, str) and [tuple(r) for r in _n_rec] == [(1,)], _n_rec)
        _ix = my("select distinct index_name from information_schema.statistics where table_schema = "
                 "DATABASE() and table_name in ('phi_audit_log','data_audit_log','operation_audit_log') "
                 "order by index_name")
        check("mysql: ...and the only indexes on the audit tables are the administrator's",
              not isinstance(_ix, str) and sorted(r[0] for r in _ix)
              == sorted("adm_%s_%s" % (t, k) for t in AUDIT_TABLES_ALL for k in ("a", "p")), _ix)
        _dup = my("insert into data_audit_log (prev_hash, audit_id, event) "
                  "select prev_hash, 'another-id', 'forged' from data_audit_log")
        check("mysql: ...and a second record on the same predecessor is refused",
              isinstance(_dup, str) and "Duplicate entry" in _dup, _dup)
        _probe = _mysql_as(MY_AUDIT_URL, "CREATE INDEX probe_ix ON data_audit_log (event(10))")
        check("mysql: ...and the audit user itself cannot build an index, so the record above is the "
              "proof that it never tried", isinstance(_probe, str) and "denied" in _probe.lower(), _probe)
    finally:
        my("DROP USER IF EXISTS '%s'@'%%'" % MY_AUDIT_USER)
        for t in MY_SCRATCH + AUDIT_TABLES_ALL:
            my("DROP TABLE IF EXISTS %s" % t)
    # A unique index on prev_hash WITH another column allows two records on one predecessor, so the
    # owner builds its own.
    for t in AUDIT_TABLES_ALL:
        my("CREATE TABLE %s (%s)" % (t, ", ".join("`%s` TEXT" % c for c in canonical_audit_columns())))
        my("CREATE UNIQUE INDEX wide_%s_p ON %s (prev_hash(255), event(50))" % (t, t))
    run(APPEND_ONLY_WRITE, MY, "mysql")
    _wide = my("select distinct index_name from information_schema.statistics where table_schema = "
               "DATABASE() and table_name = 'data_audit_log'")
    _wide = [r[0] for r in _wide] if not isinstance(_wide, str) else _wide
    check("mysql: a unique index on prev_hash WITH another column is not taken as the chain's, so "
          "Mohio builds its own", "ux_data_audit_log_prev_hash" in _wide, _wide)
    for t in MY_SCRATCH + AUDIT_TABLES_ALL:
        my("DROP TABLE IF EXISTS %s" % t)
else:
    skip("mysql restricted audit user", "mysql unreachable")


# -- Oracle: indexes an administrator built, and not one statement from Mohio --------------------
print("\n== oracle: the administrator's own unique indexes are the chain's, and Mohio sends no DDL ==")
# The Oracle test user cannot create another user, so the INSERT-only role is shown on Postgres and
# MySQL. What Oracle shows is the catalogue question itself, through `mio run` as the owner: the
# indexes are already there under other names, and a schema DDL trigger writes down every DDL
# statement this schema receives, so "no index statement" is read off the database, not inferred.
ORA = os.environ.get("MOHIO_TEST_ORACLE", "oracle://nobody:none@127.0.0.1:1/none")
ora_ok = False
try:
    import oracledb
    from mohio_interpreter import OracleRuntime  # noqa: E402
    _ou, _opw, _odsn = OracleRuntime._parse_url(ORA)
    oracledb.connect(user=_ou, password=_opw, dsn=_odsn).close()
    ora_ok = True
except Exception as e:
    skip("oracle cases", str(e).splitlines()[0][:70])


def ora(sql):
    c = oracledb.connect(user=_ou, password=_opw, dsn=_odsn)
    try:
        cur = c.cursor()
        cur.execute(sql)
        out = [tuple(r) for r in cur.fetchall()] if cur.description else []
        c.commit()
        return out
    except Exception as e:
        return "ERR %s" % str(e).splitlines()[0]
    finally:
        c.close()


ORA_TABLES = MY_SCRATCH + ("mohio_ddl_seen",) + AUDIT_TABLES_ALL

if ora_ok:
    if _iso_guard is not None:
        _c = oracledb.connect(user=_ou, password=_opw, dsn=_odsn)
        try:
            _iso_guard.require_live(_c, "oracle")
        finally:
            _c.close()
    ora("DROP TRIGGER mohio_ddl_watch")
    for t in ORA_TABLES:
        ora("DROP TABLE %s PURGE" % t)
    # The audit tables exactly as Mohio builds them, then the administrator's indexes under names of
    # their own.
    _ort = OracleRuntime(ORA)
    for t in AUDIT_TABLES_ALL:
        _ort.ensure_table(t, canonical_audit_columns(), allow_new_columns=True)
    _ort.conn.close()
    for t in AUDIT_TABLES_ALL:
        ora("CREATE UNIQUE INDEX adm_%s_p ON %s (prev_hash)" % (t[:12], t))
        ora("CREATE UNIQUE INDEX adm_%s_a ON %s (audit_id)" % (t[:12], t))
    _ora_ix = ("SELECT index_name FROM user_indexes WHERE table_name IN "
               "('PHI_AUDIT_LOG', 'DATA_AUDIT_LOG', 'OPERATION_AUDIT_LOG') "
               "AND index_name NOT IN (SELECT constraint_name FROM user_constraints "
               "WHERE constraint_type = 'P') ORDER BY index_name")
    _before = ora(_ora_ix)
    ora("CREATE TABLE mohio_ddl_seen (ev VARCHAR2(30), otype VARCHAR2(30), oname VARCHAR2(128))")
    ora("CREATE OR REPLACE TRIGGER mohio_ddl_watch BEFORE DDL ON SCHEMA\n"
        "DECLARE PRAGMA AUTONOMOUS_TRANSACTION;\n"
        "BEGIN\n"
        "  INSERT INTO mohio_ddl_seen VALUES (ora_sysevent, ora_dict_obj_type, ora_dict_obj_name);\n"
        "  COMMIT;\n"
        "END;")
    try:
        out = run(APPEND_ONLY_WRITE, ORA, "oracle")
        _seen = ora("SELECT ev, otype, oname FROM mohio_ddl_seen")
    finally:
        ora("DROP TRIGGER mohio_ddl_watch")
    check("oracle: the first regulated write lands its record",
          "saved" in out and ora("SELECT count(*) FROM data_audit_log") == [(1,)],
          (ora("SELECT count(*) FROM data_audit_log"), out[-300:]))
    check("oracle: the DDL watch saw this run's own work (it is live, so its silence below counts)",
          isinstance(_seen, list) and any(r[1] == "TABLE" for r in _seen), _seen)
    _audit_ddl = [r for r in (_seen if isinstance(_seen, list) else [])
                  if r[1] == "INDEX" and "AUDIT_LOG" in str(r[2]).upper()]
    check("oracle: ...and not one index statement reached an audit log", _audit_ddl == [], _audit_ddl)
    check("oracle: ...and the audit tables hold only the administrator's indexes",
          isinstance(_before, list) and len(_before) == 6 and ora(_ora_ix) == _before,
          (ora(_ora_ix), _before))
    _dup = ora("INSERT INTO data_audit_log (prev_hash, audit_id, event) "
               "SELECT prev_hash, 'another-id', 'forged' FROM data_audit_log")
    check("oracle: ...and a second record on the same predecessor is refused",
          isinstance(_dup, str) and "ORA-00001" in _dup, _dup)
    # A unique index on prev_hash WITH another column allows two records on one predecessor, so the
    # owner builds its own.
    for t in ORA_TABLES:
        ora("DROP TABLE %s PURGE" % t)
    _ort = OracleRuntime(ORA)
    for t in AUDIT_TABLES_ALL:
        _ort.ensure_table(t, canonical_audit_columns(), allow_new_columns=True)
    _ort.conn.close()
    for t in AUDIT_TABLES_ALL:
        ora("CREATE UNIQUE INDEX wide_%s_p ON %s (prev_hash, event)" % (t[:12], t))
    run(APPEND_ONLY_WRITE, ORA, "oracle")
    _wide = ora("SELECT index_name FROM user_indexes WHERE table_name = 'DATA_AUDIT_LOG'")
    _wide = [str(r[0]).lower() for r in _wide] if not isinstance(_wide, str) else _wide
    check("oracle: a unique index on prev_hash WITH another column is not taken as the chain's, so "
          "Mohio builds its own", "ux_data_audit_log_prev_hash" in _wide, _wide)
    # A NON-unique index on prev_hash alone: Oracle refuses a second index on the same column list
    # (ORA-01408), which used to count as done, so the chain ran with no predecessor uniqueness and a
    # second record on one predecessor was accepted (measured 2026-11-08 through `mio run`).
    for t in ORA_TABLES:
        ora("DROP TABLE %s PURGE" % t)
    _ort = OracleRuntime(ORA)
    for t in AUDIT_TABLES_ALL:
        _ort.ensure_table(t, canonical_audit_columns(), allow_new_columns=True)
    _ort.conn.close()
    for t in AUDIT_TABLES_ALL:
        ora("CREATE INDEX plain_%s_p ON %s (prev_hash)" % (t[:12], t))
    out = run(APPEND_ONLY_WRITE, ORA, "oracle")
    check("oracle: a NON-unique index on prev_hash is not taken as the chain's: the record is not "
          "written to an unprotected chain", ora("SELECT count(*) FROM data_audit_log") == [(0,)],
          ora("SELECT count(*) FROM data_audit_log"))
    check("oracle: ...and the reason is said, with the fix",
          "does not hold it to one row" in out and "Make that index unique on prev_hash alone" in out,
          out[-600:])
    for t in ORA_TABLES:
        ora("DROP TABLE %s PURGE" % t)

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
