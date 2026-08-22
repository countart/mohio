# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""T1-MYSQL-DICTCURSOR-INDEX (2026-08-20): the MySQL backend can create, introspect and write
tables again.

THE BUG. `MySQLRuntime` connects with `cursorclass=pymysql.cursors.DictCursor`
(mohio_interpreter.py:1788), so every fetched row is a dict. `ensure_table` read its
information_schema result POSITIONALLY -- `existing = {r[0] for r in cur.fetchall()}` -- which on
a dict raises `KeyError: 0`. `ensure_table` runs before EVERY save / update / upsert / retrieve,
so this was not an edge case: the entire MySQL backend could not create or introspect a table at
all. Through `mio run` it surfaced as the uniquely unhelpful `500 db_error: 0`.

Pre-existing, NOT caused by the upsert work that found it -- confirmed by running the identical
program at clean HEAD `4b55369` with the fix absent (`500 db_error: 0`) and with it present (full
lifecycle passes).

THE FIX. The spelling this file already uses for exactly this situation, a few methods below in
the same class (`count_rows`, mohio_interpreter.py:1898 -- "Connection uses DictCursor, so
fetchone() is a dict; read by alias"): alias the column in SQL, read it by that alias, keep a
positional fallback. `SELECT column_name AS col ...` + `r['col'] if isinstance(r, dict) else r[0]`.
The explicit alias is load-bearing rather than cosmetic: MySQL 8 reports information_schema column
labels in UPPER case, so an unaliased `r['column_name']` would work on some servers and KeyError
on others -- the alias makes the key deterministic across versions.

SIBLING SWEEP (Ron's question, answered explicitly):

  1. Does an EXTERNAL MySQL reached through `mioconnect` hit the same bug? **No -- mioconnect has
     no MySQL path at all.** `_exec_MioconnectDecl` (mohio_interpreter.py:4563) states it in its
     own docstring: "mioconnect compiles to miohttp at call time." A connector record holds an
     address, auth, and operations, and each operation carries `method` and `path` -- it is an
     HTTP layer, not a database driver. Asserted below rather than asserted-by-prose.

  2. Then how is an external MySQL reached? Through the SAME code as any other MySQL:
     `connect db as mysql` -> `_make_db_runtime` (mohio_interpreter.py:2414) -> `MySQLRuntime(url)`.
     There is no embedded/"built-in" MySQL to contrast with a remote one -- EVERY MySQL Mohio talks
     to is an external server, and the host in the connection string is the only thing that
     differs. So this is ONE path, not two, and one fix covers local and remote alike. Asserted
     below via the runtime class actually handed to a live remote-host connection.

  3. Other positional-row reads in `MySQLRuntime`? Swept lines 1767-2145: this was the ONLY
     unguarded one. Every other read goes through `dict(row)` / `dict(r)`, and `count_rows`
     already used the guarded alias form. The remaining `[0]` occurrences index an EXCEPTION's
     `.args`, not a row.

COVERAGE. The always-on section reproduces the exact dict-row regression with no server needed,
so CI catches it. The live section runs real `.mho` through `mio`'s full pipeline against a real
MySQL when MOHIO_TEST_MYSQL_URL is set (same skip convention as tests/test_audit_chain_postgres.py)
-- table create, column-widening introspection, write, read, update, and the upsert-on-a-
non-unique-column case. That last one also CLOSES the gap recorded in
tests/test_upsert_no_constraint.py's mutation log, which could only be covered at SQL-shape level
while MySQL was unable to execute anything at all.

Verified live on MySQL 8.4 (2026-08-20).

Run: `python tests/test_mysql_dictcursor_index.py`.
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); os.chdir(ROOT)
import mohio_data
os.environ.setdefault('MOHIO_ENCRYPTION_KEY', 'testkey')

from lark import Lark
from mohio_transformer_ast import transform as ast_transform
from mohio_interpreter import MohioInterpreter, MySQLRuntime, MariaDBRuntime

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


# ── ALWAYS-ON: the exact regression, reproduced with dict rows and no server ────────────────
class _DictCursor:
    """Stands in for pymysql's DictCursor: fetchall() yields DICTS, never tuples."""
    def __init__(self, sink, existing_cols): self.sink = sink; self._cols = existing_cols
    def execute(self, sql, params=None): self.sink.append(sql)
    # The real query is `SELECT column_name AS col ...`, so the dict is keyed by the alias.
    def fetchall(self): return [{'col': c} for c in self._cols]
    def fetchone(self): return None
    def close(self): pass

class _DictConn:
    def __init__(self, sink, existing_cols): self.sink = sink; self._cols = existing_cols
    def cursor(self): return _DictCursor(self.sink, self._cols)
    def commit(self): pass
    def rollback(self): pass

def _ensure_table(existing_cols, want_cols):
    sink = []
    rt = object.__new__(MySQLRuntime)          # bypass __init__ (it would open a real socket)
    rt.conn = _DictConn(sink, existing_cols)
    rt._in_transaction = False
    rt.ensure_table('players', want_cols)
    return sink

# THE regression: before the fix this raised KeyError: 0 on the dict row.
try:
    stmts = _ensure_table(['id', 'handle'], ['id', 'handle'])
    check("ensure_table survives DICT rows (the KeyError: 0 regression)", True)
except Exception as e:
    check("ensure_table survives DICT rows (the KeyError: 0 regression)", False,
          f"{type(e).__name__}: {e}")
    stmts = []

joined = ' | '.join(stmts)
check("ensure_table reads the introspection column by ALIAS (deterministic across MySQL versions)",
      'AS col' in joined, joined)
check("a column that ALREADY exists is not re-added (the introspection result is really used)",
      'ADD COLUMN' not in joined.upper(), joined)

# The widening branch: a genuinely NEW column must still be added. If the fix had silently
# produced an empty `existing` set, the case above would ALSO emit ADD COLUMN and look fine --
# this pair is what distinguishes "read the rows correctly" from "read nothing at all".
stmts2 = _ensure_table(['id', 'handle'], ['id', 'handle', 'score'])
j2 = ' | '.join(stmts2)
check("a genuinely NEW column IS added (widening still works)",
      'ADD COLUMN' in j2.upper() and 'score' in j2, j2)
check("...and only the new column, not the existing ones",
      j2.upper().count('ADD COLUMN') == 1, j2)


# ── ALWAYS-ON: mioconnect is an HTTP layer, so it has no MySQL path to share this bug ───────
MIOCONNECT_SRC = (
    'mioconnect Billing as Bill\n'
    '    address "https://api.example.com"\n'
    '    operation charge\n'
    '        path "/v1/charge"\n'
    '        method POST\n'
    '    operation: done\n'
    'mioconnect: done\n')
try:
    prog = ast_transform(P.parse(MIOCONNECT_SRC), MIOCONNECT_SRC)
    it = MohioInterpreter(); it.run_declarations(prog); it.run(prog)
    rec = (getattr(it, '_connectors', {}) or {}).get('Billing') or {}
    ops = rec.get('operations') or {}
    check("mioconnect registers an ADDRESS-based connector (not a database runtime)",
          'address' in rec and not any(k in rec for k in ('runtime', 'db', 'conn')), sorted(rec))
    check("mioconnect operations are HTTP (method + path) -- no MySQL path exists to share the bug",
          bool(ops) and all('method' in o and 'path' in o for o in ops.values()), ops)
except Exception as e:
    check("mioconnect declaration parses and registers", False, f"{type(e).__name__}: {e}")

# One class serves mysql and mariadb, so a fix here covers both declarations.
check("MariaDB is the SAME class as MySQL (one fix covers both declarations)",
      MariaDBRuntime is MySQLRuntime)


# ── LIVE MySQL: real .mho through the full pipeline, skipped when no server is configured ───
_URL = os.environ.get('MOHIO_TEST_MYSQL_URL')
if not _URL:
    print("  [SKIP] live MySQL: set MOHIO_TEST_MYSQL_URL to run the real-path cases")
else:
    LIVE = (
        'connect db as mysql from env.MYSQL_URL\n'
        'shape Player\n    handle as text\n    score as text\nshape: done\n'
        'save to db.players\n    handle "neo"\n    score "10"\nsave: done\n'
        'show "created"\n'
        'retrieve p from db.players\n    match handle to "neo"\nretrieve: done\n'
        'show ("read=" & p.score)\n'
        'update db.players\n    match handle to "neo"\n    score "42"\nupdate: done\n'
        'retrieve p2 from db.players\n    match handle to "neo"\nretrieve: done\n'
        'show ("updated=" & p2.score)\n'
        # A column the table does not have yet -> exercises the introspect/widen branch, which
        # is the exact code that was broken.
        'save to db.players\n    handle "trin"\n    score "7"\n    rank "captain"\nsave: done\n'
        'retrieve p3 from db.players\n    match handle to "trin"\nretrieve: done\n'
        'show ("widened=" & p3.rank)\n'
        # upsert on a NON-unique column -- shape-only until MySQL could execute; live now.
        'upsert db.players\n    match handle to "neo"\n    score "99"\nupsert: done\n'
        'upsert db.players\n    match handle to "neo"\n    score "99"\nupsert: done\n'
        'find neos in db.players\n    where handle is "neo"\nfind: done\n'
        'show ("neo_rows=" & neos.count)\n'
        'retrieve p4 from db.players\n    match handle to "neo"\nretrieve: done\n'
        'show ("upserted=" & p4.score)\n')
    prev_my, prev_db = os.environ.get('MYSQL_URL'), os.environ.get('DATABASE_URL')
    os.environ['MYSQL_URL'] = _URL
    os.environ.pop('DATABASE_URL', None)
    try:
        import pymysql
        _c = pymysql.connect(host='127.0.0.1', port=int(_URL.rsplit(':', 1)[1].split('/')[0]),
                             user=_URL.split('//')[1].split(':')[0],
                             password=_URL.split(':')[2].split('@')[0],
                             database=_URL.rsplit('/', 1)[1])
        _cur = _c.cursor(); _cur.execute('DROP TABLE IF EXISTS players'); _c.commit(); _c.close()
    except Exception:
        pass    # a clean slate is preferable but not required for the assertions below
    try:
        prog = ast_transform(P.parse(LIVE), LIVE)
        it = MohioInterpreter(); it.run_declarations(prog); it.run(prog)
        out = it.shown
        check("live MySQL: CREATE + WRITE works (the whole backend was dead before)",
              "created" in out, out)
        check("live MySQL: read back the written value", "read=10" in out, out)
        check("live MySQL: update works", "updated=42" in out, out)
        check("live MySQL: INTROSPECT + WIDEN works (the exact broken code path)",
              "widened=captain" in out, out)
        check("live MySQL: upsert on a NON-unique column leaves ONE row (was shape-only coverage)",
              "neo_rows=1" in out, out)
        check("live MySQL: the upsert applied its value", "upserted=99" in out, out)
        # The external question, settled on the object the factory actually hands back for a
        # remote TCP URL: it is the same class local MySQL gets -- ONE path, not two. Asserted
        # through `_make_db_runtime`, the single factory every `connect ... as mysql` goes
        # through, rather than by digging for a connection attribute (an earlier version of this
        # check read a name that does not exist on the interpreter, so it silently never ran --
        # exactly the silent no-op this codebase refuses; assert something that cannot skip).
        from mohio_interpreter import _make_db_runtime
        _rt = _make_db_runtime('mysql')
        check("live MySQL: a remote-host mysql URL is served by MySQLRuntime (ONE path, not two)",
              isinstance(_rt, MySQLRuntime), type(_rt).__name__)
        try: _rt.close()
        except Exception: pass
    except Exception as e:
        check("live MySQL: full lifecycle", False, f"{type(e).__name__}: {e}")
    finally:
        if prev_my is not None: os.environ['MYSQL_URL'] = prev_my
        else: os.environ.pop('MYSQL_URL', None)
        if prev_db is not None: os.environ['DATABASE_URL'] = prev_db

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
