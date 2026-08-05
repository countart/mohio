# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""Postgres NATIVE upsert idempotency -- the path sqlite never exercises (Stage-6 sibling, 2026-07-31).

`_exec_SaveOrUpdateBlock` uses `db.upsert(...)` when the backend has it (Postgres/Mongo) and an
update-then-insert FALLBACK otherwise (sqlite). The `:memory:` suite only runs the fallback, so the
NATIVE path -- the one zork's Postgres `saved_games` actually uses -- was untested. This exercises
it against a REAL Postgres.

It also pins the known limitation the native path inherits (the zork clean-DB schema item): Postgres
`ON CONFLICT("field")` needs a UNIQUE constraint on that field. Upsert on the `id` PK is idempotent;
upsert on a NON-id field with no unique index must FAIL LOUD (never silently duplicate).

Needs a real Postgres. If none is reachable it SKIPS (exit 0) -- it does NOT fall back to sqlite,
because approximating with sqlite is exactly what hid this gap.

Run as a script: `python tests/test_upsert_semantics_postgres.py` (exit 0 = pass or skip).
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); os.chdir(ROOT)

def _reachable(dsn):
    try:
        import psycopg2
        c = psycopg2.connect(dsn, connect_timeout=3); c.close(); return True
    except Exception:
        return False

# Prefer an explicit Postgres DATABASE_URL; else try the common local default.
_env = os.environ.get('DATABASE_URL', '')
CANDIDATES = ([_env] if _env.startswith('postgres') else []) + [
    'postgresql://postgres:postgres@localhost:5432/postgres',
    'postgresql://postgres@localhost:5432/postgres',
]
DSN = next((d for d in CANDIDATES if d and _reachable(d)), None)
if not DSN:
    print("  [SKIP] no reachable Postgres -- native upsert path needs one; NOT approximating with sqlite")
    sys.exit(0)

os.environ['DATABASE_URL'] = DSN
from pathlib import Path
from lark import Lark
from mohio_interpreter import MohioInterpreter
from mohio_transformer_ast import transform as ast_transform

_raw = Path('mohio.lark').read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)

T1 = "mtest_upsert_id"
T2 = "mtest_upsert_nonid"

def _drop():
    import psycopg2
    c = psycopg2.connect(DSN); cur = c.cursor()
    for t in (T1, T2):
        cur.execute(f'DROP TABLE IF EXISTS "{t}"')
    c.commit(); cur.close(); c.close()

def run(src):
    interp = MohioInterpreter()
    return interp.run(ast_transform(P.parse(src), src))

try:
    _drop()

    # --- native upsert on the id PK: two upserts on one key -> ONE row, latest value ----------
    # A broken native upsert (e.g. degraded to a plain insert) raises a duplicate-key violation on
    # the second write against the id PK -- catch it and report a clean FAIL rather than crashing.
    import psycopg2
    id_err = None
    try:
        run(f'connect db as postgres from env.DATABASE_URL\n'
            f'save or update db.{T1}\n    match id to "K1"\n    score 10\nsave: done\n'
            f'save or update db.{T1}\n    match id to "K1"\n    score 20\nsave: done\n')
    except Exception as e:
        id_err = f"{type(e).__name__}: {e}"
    check("native upsert on id: two idempotent writes complete without error",
          id_err is None, f"upsert path errored: {id_err}")
    if id_err is None:
        c = psycopg2.connect(DSN); cur = c.cursor()
        cur.execute(f'SELECT COUNT(*) FROM "{T1}" WHERE id = %s', ('K1',))
        n = cur.fetchone()[0]
        cur.execute(f'SELECT score FROM "{T1}" WHERE id = %s', ('K1',))
        row = cur.fetchone(); cur.close(); c.close()
        check("native upsert on id: two upserts on one key leave ONE row", n == 1, f"row count = {n}")
        check("native upsert on id: the row holds the LATEST value (20)",
              row is not None and str(row[0]) == "20", f"score = {row!r}")

    # --- native upsert on a NON-id field with no unique index: must FAIL LOUD (zork limitation) ---
    err = None
    try:
        run(f'connect db as postgres from env.DATABASE_URL\n'
            f'save or update db.{T2}\n    match handle to "neo"\n    score 10\nsave: done\n')
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    # surfaced either as a raised exception or a 500 result -- both are fail-loud, neither is silent.
    check("native upsert on a non-id field with no unique index FAILS LOUD (no silent duplicate)",
          err is not None, "expected a failure (ON CONFLICT needs a unique constraint) but the upsert succeeded")
    if err:
        check("the failure points at the missing unique/ON CONFLICT constraint",
              'conflict' in err.lower() or 'unique' in err.lower() or 'constraint' in err.lower(),
              err[:200])
finally:
    try: _drop()
    except Exception: pass

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
