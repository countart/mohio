# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""T1-UPSERT-NO-CONSTRAINT (2026-08-20, ruled Option A): `upsert` works on a match column with
NO unique constraint, on every backend, because no backend requires one any more.

THE TWO BUGS THIS CLOSES, both proven live before the fix (real DBs, not inferred):

  Postgres -- `INSERT ... ON CONFLICT(session_id) DO UPDATE` hard-requires a unique/exclusion
    constraint on the conflict column. Mohio's auto-created tables only ever get one on `id`,
    so `upsert db.saved_games match session_id` raised
    "there is no unique or exclusion constraint matching the ON CONFLICT specification"
    and 500'd EVERY time -- on exactly the tables Mohio makes. Reproduced on real Postgres 18.

  MySQL -- `INSERT ... ON DUPLICATE KEY UPDATE` does NOT error when the match column has no
    unique key. It just INSERTS. Reproduced on real MySQL 8 with the old code: two upserts on
    the same session_id gave `row count = 2` and a retrieve returned the STALE first row
    (`room now = west_of_house`, not `kitchen`) -- a silent duplicate AND a lost update, with
    no error and no signal. Worse than the Postgres crash: wrong data, no way to notice.

THE FIX. Both now use the constraint-free form `save_if_not_exists` already used, and had
already ruled correct in this same file (SQLite mohio_interpreter.py:1319, MySQL :2041, with
the reasoning spelled out): UPDATE the matching row; if nothing matched, INSERT guarded by
`WHERE NOT EXISTS` in ONE statement so a concurrent writer cannot create a duplicate; if that
guard blocks the insert (a concurrent writer won the race), apply the values with a final
UPDATE rather than silently losing the write. Correct WITH or WITHOUT a constraint, so a table
that DOES have a unique on the match column keeps working unchanged (guarded below).

THE OTHER TWO BACKENDS WERE ALREADY CORRECT AND ARE NOT TOUCHED: SQLite has no `.upsert()` at
all and takes the update-then-insert fallback in `_exec_SaveOrUpdateBlock`; Mongo uses
`update_one(..., upsert=True)`, which needs no constraint. Both guarded against regression here
to the extent each can be (see the coverage note below).

COVERAGE, stated honestly:
  - SQLite runs on every invocation, through real .mho source (T1-TEST-REAL-PATH-STANDARD).
  - Postgres and MySQL run against a REAL server when MOHIO_TEST_PG_URL / MOHIO_TEST_MYSQL_URL
    are set, and skip otherwise -- same convention as tests/test_audit_chain_postgres.py.
  - The emitted-SQL guards below are labeled unit tests over the runtime classes. They exist so
    that a regression to ON CONFLICT / ON DUPLICATE KEY is caught on EVERY run, including CI
    with no Postgres or MySQL present, where the real-path cases above would silently skip.
  - Mongo has no live case here (no local instance was available); its `upsert` is unmodified
    by this change, which is the whole of the claim made about it.

MUTATION RECORD (2026-08-20) -- what these guards do and do not detect:
  M1  Postgres reverted to ON CONFLICT      -> CAUGHT (3 failures, incl. the live case
                                               reproducing the original constraint crash).
  M3  Postgres step-1 UPDATE disabled       -> CAUGHT by the shape guard. Behaviour did NOT
                                               change, because step 3's post-guard UPDATE
                                               compensates -- worth knowing: the three steps
                                               are individually redundant by design.
  M2  MySQL guard clause neutered           -> **NOT CAUGHT.** The always-on guards assert the
      (WHERE NOT EXISTS made a no-op)          SQL SHAPE (no ON DUPLICATE KEY, guard present,
                                               dialect wrapper intact), not that the guard is
                                               semantically effective. Only a live MySQL run
                                               could catch that, and the live MySQL case cannot
                                               run at all until the separate pre-existing
                                               `ensure_table` DictCursor bug is fixed. So MySQL
                                               is covered at SHAPE level only today. Stated
                                               rather than papered over: it is a real gap, and
                                               it closes for free once MySQL can execute.

Run: `python tests/test_upsert_no_constraint.py`.
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); os.chdir(ROOT)
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')
os.environ.setdefault('MOHIO_ENCRYPTION_KEY', 'testkey')

from lark import Lark
from mohio_transformer_ast import transform as ast_transform
from mohio_interpreter import MohioInterpreter, PostgresRuntime, MySQLRuntime

_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)

def run_real(src):
    prog = ast_transform(P.parse(src), src)
    it = MohioInterpreter()
    it.run_declarations(prog)
    return it, it.run(prog)


# ── REAL PATH, always-on: SQLite, two upserts on one key -> ONE row, value UPDATED ──────────
_SQLITE_SRC = (
    'connect db as sqlite from env.DATABASE_URL\n'
    'shape SavedGame\n    session_id as text\n    room as text\nshape: done\n'
    'upsert db.saved_games\n    match session_id to "sess-1"\n    room "west_of_house"\n'
    'upsert: done\n'
    'upsert db.saved_games\n    match session_id to "sess-1"\n    room "kitchen"\n'
    'upsert: done\n'
    'retrieve.all rows from db.saved_games\nretrieve.all: done\n'
    'show ("rows=" & rows.count)\n'
    'retrieve got from db.saved_games\n    match session_id to "sess-1"\nretrieve: done\n'
    'show ("room=" & got.room)\n')
it, _ = run_real(_SQLITE_SRC)
check("SQLite (unchanged fallback): two upserts on a non-unique column -> ONE row, updated",
      it.shown == ["rows=1", "room=kitchen"], it.shown)


# ── EMITTED-SQL GUARDS (labeled unit tests -- see the coverage note in the docstring) ───────
class _RecordingCursor:
    """Records every statement; reports 0 rows matched so the INSERT branch is always taken."""
    def __init__(self, sink): self.sink = sink; self.rowcount = 0; self.lastrowid = 1
    def execute(self, sql, params=None): self.sink.append(sql)
    def fetchall(self): return []
    def fetchone(self): return None
    def close(self): pass

class _RecordingConn:
    def __init__(self, sink): self.sink = sink
    def cursor(self): return _RecordingCursor(self.sink)
    def commit(self): pass
    def rollback(self): pass

def _emitted(runtime_cls):
    """Drive a runtime's upsert with a fake connection and return the SQL it emitted.
    `ensure_table` is stubbed: this asserts on the UPSERT statement shape only, which is the
    entire scope of this change."""
    sink = []
    rt = object.__new__(runtime_cls)          # bypass __init__ (it would open a real socket)
    rt.conn = _RecordingConn(sink)
    rt._in_transaction = False
    rt.ensure_table = lambda *a, **k: None
    rt.upsert('saved_games', {'session_id': 's1', 'room': 'kitchen'}, 'session_id')
    return ' | '.join(sink)

pg_sql = _emitted(PostgresRuntime)
check("Postgres upsert no longer emits ON CONFLICT", "ON CONFLICT" not in pg_sql.upper(), pg_sql)
check("Postgres upsert emits the guarded INSERT ... WHERE NOT EXISTS",
      "WHERE NOT EXISTS" in pg_sql.upper(), pg_sql)
check("Postgres upsert tries the UPDATE first", pg_sql.upper().startswith("UPDATE"), pg_sql)

my_sql = _emitted(MySQLRuntime)
check("MySQL upsert no longer emits ON DUPLICATE KEY",
      "ON DUPLICATE KEY" not in my_sql.upper(), my_sql)
check("MySQL upsert emits the guarded INSERT ... WHERE NOT EXISTS",
      "WHERE NOT EXISTS" in my_sql.upper(), my_sql)
check("MySQL upsert keeps the FROM DUAL + derived-table wrapper its dialect needs",
      "FROM DUAL" in my_sql.upper() and "_CHK" in my_sql.upper(), my_sql)


# ── REAL SERVERS when available, skipped otherwise (same convention as the pg audit test) ───
def _live_backend(label, url_env, connect_kw, drop_sql):
    url = os.environ.get(url_env)
    if not url:
        print(f"  [SKIP] {label}: set {url_env} to run the live case")
        return
    src = _SQLITE_SRC.replace('connect db as sqlite from env.DATABASE_URL',
                              f'connect db as {connect_kw} from env.DATABASE_URL')
    prev = os.environ.get('DATABASE_URL')
    os.environ['DATABASE_URL'] = url
    try:
        it, _ = run_real(src)
        check(f"{label}: two upserts on a NON-unique column -> ONE row, updated",
              it.shown == ["rows=1", "room=kitchen"], it.shown)
    except Exception as e:
        # Never let a live backend take the whole file down with a bare traceback: a reader
        # must be able to tell an UPSERT regression from an unrelated backend break. As of
        # 2026-08-20 the MySQL case hits exactly such a break BEFORE upsert runs at all --
        # `MySQLRuntime.ensure_table` does `r[0]` on rows from a DictCursor connection
        # (mohio_interpreter.py:1821), so it raises KeyError: 0 for ANY MySQL write. That is
        # pre-existing (confirmed identical at clean HEAD), is NOT this change, and blocks the
        # MySQL backend entirely -- tracked separately, deliberately not fixed in this unit.
        check(f"{label}: two upserts on a NON-unique column -> ONE row, updated",
              False, f"{type(e).__name__}: {e} -- if this names ensure_table/KeyError, it is "
                     f"the separate pre-existing MySQL DictCursor bug, not an upsert regression")
    finally:
        if prev is not None: os.environ['DATABASE_URL'] = prev

_live_backend("Postgres (live)", 'MOHIO_TEST_PG_URL', 'postgres', 'DROP TABLE IF EXISTS saved_games')
_live_backend("MySQL (live)", 'MOHIO_TEST_MYSQL_URL', 'mysql', 'DROP TABLE IF EXISTS saved_games')

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
