# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""T1-GUARD-FAILOPEN, Part A (2026-08-19): retrieve fails loud on a genuine driver error,
the same way find already does. The removed carve-out lived in DbRuntime.retrieve_one/
retrieve_one_multi/retrieve_one_spec/retrieve_all_spec (SQLite), and the equivalent methods
on PostgresRuntime/MySQLRuntime: each caught a driver exception, string/code-matched "table
doesn't exist", and returned None/[] -- silently indistinguishable from a genuine "no rows".
retrieve_one/retrieve_one_multi are ALSO the `save ... unless X exists` dedupe check, which
runs BEFORE that table is created -- that ONE caller now catches its own "missing table"
condition (mohio_interpreter.py, _exec_SaveBlock), so the shared retrieve functions no longer
need to guess which caller they're serving.

Real .mho source through the full pipeline throughout (T1-TEST-REAL-PATH-STANDARD). Postgres/
MySQL backends are verified separately via mock driver exceptions (no live server in this
environment -- same constraint test_postgres_backend.py already documents) in
test_guard_failopen_pg_mysql.py.

Run: `python tests/test_guard_failopen_retrieve.py`.
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
from mohio_interpreter import MohioInterpreter

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


SEED = ('connect db as sqlite from env.DATABASE_URL\n'
        'shape Thing\n    name as text\nshape: done\n'
        'save to db.things\n    name "widget"\nsave: done\n')


# ── retrieve.one (default) on a NONEXISTENT table -> fails loud, clean db_error, same shape
# find already produces ─────────────────────────────────────────────────────────────────────
it, r = run_real('connect db as sqlite from env.DATABASE_URL\n'
                  'retrieve item from db.ghost_table\n    match id to 1\nretrieve: done\n'
                  'show "unreachable"\n')
check("retrieve on a nonexistent table fails loud (was silent 200/empty)",
      r.get('status') == 500 and 'ghost_table' in str(r.get('body', '')), r)
check("the failure never reaches past the retrieve (unreachable show never ran)",
      it.shown == [], it.shown)

# ── retrieve.all on a NONEXISTENT table -> fails loud ──────────────────────────────────────
it2, r2 = run_real('connect db as sqlite from env.DATABASE_URL\n'
                    'retrieve.all items from db.ghost_table_2\nretrieve.all: done\n'
                    'show "unreachable"\n')
check("retrieve.all on a nonexistent table fails loud",
      r2.get('status') == 500 and 'ghost_table_2' in str(r2.get('body', '')), r2)

# ── real not-found (table EXISTS, zero rows) -> still binds empty, when/otherwise fires ────
it3, _ = run_real(SEED +
    'retrieve item from db.things\n    match name to "nonexistent-widget"\n'
    '    when item is empty\n        show "MISS"\n    otherwise\n        show "HIT"\n'
    'retrieve: done\n')
check("real not-found (table exists, no row) still binds empty and fires when-empty",
      it3.shown == ["MISS"], it3.shown)

it3b, _ = run_real(SEED +
    'retrieve item from db.things\n    match name to "widget"\n'
    '    when item is empty\n        show "MISS"\n    otherwise\n        show "HIT"\n'
    'retrieve: done\n')
check("real found still fires otherwise/HIT (regression guard)",
      it3b.shown == ["HIT"], it3b.shown)

# ── find is unchanged: still fails loud on a nonexistent table, exactly as before ──────────
it4, r4 = run_real('connect db as sqlite from env.DATABASE_URL\n'
                    'find hit in db.ghost_table_3\n    where id is 1\nfind: done\n'
                    'show "unreachable"\n')
check("find on a nonexistent table still fails loud (unchanged)",
      r4.get('status') == 500 and 'ghost_table_3' in str(r4.get('body', '')), r4)

# ── save ... unless X exists on a FRESH table still works (the collateral case this fix
# had to protect -- the dedupe check runs BEFORE the table exists) ─────────────────────────
it5, _ = run_real(
    'connect db as sqlite from env.DATABASE_URL\n'
    'shape Flag\n    session_id as text\n    flag_name as text\nshape: done\n'
    'save to db.brand_new_flags unless session_id, flag_name exists\n'
    '    session_id "s1"\n    flag_name "seen_intro"\n'
    'save: done\n'
    'save to db.brand_new_flags unless session_id, flag_name exists\n'
    '    session_id "s1"\n    flag_name "seen_intro"\n'
    'save: done\n')
_rows5 = it5._db.conn.execute("SELECT * FROM brand_new_flags").fetchall()
check("save ... unless exists on a brand-new table still dedupes correctly (1 row, not 2)",
      len(_rows5) == 1, _rows5)

# ── a genuine BAD FIELD reference (not a missing table) also fails loud ────────────────────
it6, r6 = run_real(SEED +
    'retrieve item from db.things\n    match this_field_does_not_exist to "x"\nretrieve: done\n'
    'show "unreachable"\n')
check("retrieve on a genuinely bad field name fails loud",
      r6.get('status') == 500, r6)


print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
