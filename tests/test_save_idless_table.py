# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""T1-SAVE-IDLESS-TABLE (2026-08-21): writing to a table that has NO `id` column works.

THE BUG, live on production and caught by real Zork gameplay. `save to db.flags` returned:

    db_error: column "id" does not exist
    LINE 1: ...'window_open') RETURNING id

`flags` is deliberately id-less -- UNIQUE (session_id, flag_name), no id column -- because a flag
IS its (session, name) pair. Postgres's insert path asked for the new key back BY NAME with a
hardcoded `RETURNING id`, so it could not write to that table at all. Every flag-setting site in
Zork went through it.

WHY POSTGRES ONLY, and why it surfaced now. Postgres is the one backend that names the column:
SQLite and MySQL read the new key from the driver (`lastrowid`) and Mongo from `inserted_id`, none
of which care whether an `id` column exists. Swept, all four backends, every write verb -- the
full matrix is in the commit message; only `PostgresRuntime.save` and
`PostgresRuntime.save_if_not_exists` carried the assumption.

It stayed hidden because the assumption was self-fulfilling: when the RUNTIME creates a table,
`_col_defs` always adds `id ... PRIMARY KEY`, so `RETURNING id` always worked. It broke only on a
PRE-EXISTING table with no id -- and the seeder began producing exactly those in
T1-SEEDER-SCHEMA-CORRECTNESS (`be29057`), which gave each table its real identity. The seeder fix
was right; it exposed a latent assumption one layer down. Verified both halves live: a
runtime-created table still gets an id and still returns it; a seeder-created `flags` did not.

THE FIX: ask the table whether it has an `id` column (once, cached) and only request
`RETURNING id` when it does. For an id-less table there is no generated key to hand back -- the
row's identity is the values just written, which the caller already holds -- so `save` returns
None and `save ... unless exists` returns its rowcount, which is all its caller ever tests.

BOUNDARY -- what this does NOT change: the audit record written for an id-less save carries
`record_id=None`, because there is no id to record. That is honest rather than invented, but it
IS a weaker audit record than a keyed table produces, and what should identify such a row in the
audit log (the composite? a rendered key?) is a compliance question left for its own ruling, not
decided here.

COVERAGE: the always-on cases drive the real `PostgresRuntime` methods through a recording cursor,
so CI catches a regression with no Postgres present. The live case runs real `.mho` through
`mio`'s pipeline against a real server when MOHIO_TEST_PG_URL is set (same skip convention as
tests/test_audit_chain_postgres.py).

Verified live on Postgres 18 (2026-08-21), including a real cookieless HTTP request through
`mio serve` writing flags into a seeder-built id-less table.

Run: `python tests/test_save_idless_table.py`.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); os.chdir(ROOT)
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')
os.environ.setdefault('MOHIO_ENCRYPTION_KEY', 'testkey')

from lark import Lark
from mohio_transformer_ast import transform as ast_transform
from mohio_interpreter import MohioInterpreter, PostgresRuntime

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


# -- ALWAYS-ON: drive the real Postgres methods through a recording cursor -------------------
class _Cur:
    """Answers the id-column probe with `has_id`, and records every statement issued."""
    def __init__(self, sink, has_id):
        self.sink = sink; self._has_id = has_id; self.rowcount = 1
        self._last_was_probe = False
    def execute(self, sql, params=None):
        flat = ' '.join(sql.split())
        self._last_was_probe = 'information_schema.columns' in flat
        if not self._last_was_probe:
            self.sink.append(flat)
    def fetchone(self):
        if self._last_was_probe:
            return (1,) if self._has_id else None
        return (42,)              # a real RETURNING id result, when one was asked for
    def close(self): pass

class _Conn:
    def __init__(self, sink, has_id): self.sink = sink; self._has_id = has_id
    def cursor(self): return _Cur(self.sink, self._has_id)
    def commit(self): pass
    def rollback(self): pass

def _pg(has_id):
    rt = object.__new__(PostgresRuntime)        # bypass __init__ (it would open a socket)
    rt.conn = _Conn([], has_id)
    rt._in_transaction = False
    rt.ensure_table = lambda *a, **k: None
    return rt

def _run(method, has_id, *args):
    rt = _pg(has_id)
    sink = rt.conn.sink
    out = getattr(rt, method)(*args)
    return ' | '.join(sink), out

FIELDS = {'session_id': 's1', 'flag_name': 'window_open'}

# save() -- the reported failure
sql, out = _run('save', False, 'flags', FIELDS)
check("id-less save emits NO 'RETURNING id' (the exact statement that 500'd live)",
      'RETURNING' not in sql.upper(), sql)
check("id-less save still performs the INSERT", 'INSERT INTO "flags"' in sql, sql)
check("id-less save returns None -- there is no key to hand back", out is None, out)

sql, out = _run('save', True, 'people', {'name': 'Bo'})
check("keyed save STILL asks for RETURNING id (no regression for normal tables)",
      'RETURNING id' in sql, sql)
check("keyed save still returns the new id", out == 42, out)

# save_if_not_exists() -- the sibling with the same assumption
sql, out = _run('save_if_not_exists', False, 'flags', FIELDS, ['session_id', 'flag_name'])
check("id-less save-unless-exists emits NO 'RETURNING id'",
      'RETURNING' not in sql.upper(), sql)
check("id-less save-unless-exists keeps its WHERE NOT EXISTS guard",
      'WHERE NOT EXISTS' in sql.upper(), sql)
check("id-less save-unless-exists reports the insert via rowcount", out == 1, out)

sql, out = _run('save_if_not_exists', True, 'people', {'id': '1', 'name': 'Bo'}, ['id'])
check("keyed save-unless-exists STILL asks for RETURNING id", 'RETURNING id' in sql, sql)
check("keyed save-unless-exists still returns the id", out == 42, out)

# The probe is asked once per table, not once per row -- a write loop must not pay for it twice.
rt = _pg(False)
rt.save('flags', FIELDS); rt.save('flags', FIELDS); rt.save('flags', FIELDS)
check("the id-column probe is cached per table (asked once, not once per write)",
      rt._id_col_cache == {'flags': False}, getattr(rt, '_id_col_cache', None))


# -- LIVE: real .mho through the full pipeline against a real Postgres ----------------------
_URL = os.environ.get('MOHIO_TEST_PG_URL')
if not _URL:
    print("  [SKIP] live: set MOHIO_TEST_PG_URL to run the real id-less write cases")
else:
    import psycopg2
    conn = psycopg2.connect(_URL); conn.autocommit = True
    c = conn.cursor()
    c.execute('DROP TABLE IF EXISTS t_idless')
    # Mirror the real `flags` shape exactly: composite unique, no id column.
    c.execute('CREATE TABLE t_idless ("session_id" TEXT, "flag_name" TEXT, '
              'UNIQUE ("session_id", "flag_name"))')

    SRC = ('connect db as postgres from env.DATABASE_URL\n'
           'save to db.t_idless\n    session_id "s1"\n    flag_name "window_open"\nsave: done\n'
           'show "saved"\n'
           'save to db.t_idless unless session_id, flag_name exists\n'
           '    session_id "s1"\n    flag_name "window_open"\nsave: done\n'
           'show "unless-exists ok"\n'
           'upsert db.t_idless\n    match session_id to "s1", flag_name to "window_open"\n'
           'upsert: done\nshow "upsert ok"\n'
           'update db.t_idless\n    match session_id to "s1"\n    flag_name "window_shut"\n'
           'update: done\nshow "update ok"\n'
           'retrieve.count n from db.t_idless\nretrieve.count: done\n'
           'show ("rows=" & n)\n'
           'remove from db.t_idless\n    match session_id to "s1"\nremove: done\n'
           'show "remove ok"\n')
    prev = os.environ.get('DATABASE_URL')
    os.environ['DATABASE_URL'] = _URL
    try:
        prog = ast_transform(P.parse(SRC), SRC)
        it = MohioInterpreter(); it.run_declarations(prog); it.run(prog)
        out = it.shown
        check("live: SAVE into an id-less table succeeds (the production 500)",
              'saved' in out, out)
        check("live: save-unless-exists works on an id-less table", 'unless-exists ok' in out, out)
        check("live: upsert works on an id-less table", 'upsert ok' in out, out)
        check("live: update works on an id-less table", 'update ok' in out, out)
        check("live: remove works on an id-less table", 'remove ok' in out, out)
        check("live: exactly one row survived the duplicate attempts", 'rows=1' in out, out)
        c.execute("SELECT count(*) FROM t_idless")
        check("live: the row really is gone after remove", c.fetchone()[0] == 0)
    except Exception as e:
        check("live: id-less write cycle", False, f"{type(e).__name__}: {e}")
    finally:
        if prev is not None: os.environ['DATABASE_URL'] = prev
        c.close(); conn.close()

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
