# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""upsert supports a COMPOSITE match target (2026-08-02).

upsert() used to take a single match field and emit ON CONFLICT("{col}") -- so any table with a
multi-column unique constraint (e.g. Zork's items, UNIQUE(session_id, id)) could not be upserted:
Postgres rejects a single-column conflict target that is not itself unique. The transformer also
silently dropped a multi-field match to None. Now:
  - the transformer captures ALL match fields (a list of MatchClause),
  - _exec passes the full list to db.upsert,
  - Postgres emits ON CONFLICT ("c1","c2",...) and DO UPDATE SET excludes EVERY conflict column,
  - single-column upsert is unchanged.

Proven three ways: the AST carries both fields; the Postgres SQL is generated correctly (fake
cursor, no live PG); and a composite upsert runs end-to-end on SQLite (the fallback path).
Run: `python tests/test_upsert_composite_match.py`.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')

from pathlib import Path
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter, PostgresRuntime

import mohio_data
_RAW = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_G = '\n'.join(l for l in _RAW.splitlines() if not l.strip().startswith('//'))
_P = Lark(_G, parser='earley', ambiguity='resolve', propagate_positions=True)

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail: print(f"          {detail}")
    _p += bool(cond); _f += (not cond)

# ── 1. Transformer/AST: composite match is captured as a list; single stays single ─────────
def match_fields(matchline):
    src = 'connect db as sqlite from env.DATABASE_URL\nupsert db.items\n    ' + matchline + '\n    name "x"\n    upsert: done\n'
    m = transform(_P.parse(src), src).statements[-1].match
    if m is None: return None
    return [c.field for c in m] if isinstance(m, list) else [m.field]

check("single match -> one field (unchanged)", match_fields('match session_id to "s"') == ['session_id'])
check("composite match (comma pairs) -> BOTH fields (was silently dropped to None)",
      match_fields('match session_id to "s", id to "i"') == ['session_id', 'id'])

# ── 2. Postgres SQL generation: composite ON CONFLICT, DO UPDATE excludes every key col ─────
def pg_sql(fields, match):
    pg = PostgresRuntime.__new__(PostgresRuntime)
    pg._in_transaction = False
    pg.ensure_table = lambda *a, **k: None
    cap = {}
    class _Cur:
        def execute(self, sql, params): cap['sql'] = sql
        def close(self): pass
    class _Conn:
        def cursor(self): return _Cur()
        def commit(self): pass
        def rollback(self): pass
    pg.conn = _Conn()
    pg.upsert('items', fields, match)
    return cap['sql']

comp = pg_sql({'id': 'i', 'session_id': 's', 'name': 'lamp'}, ['session_id', 'id'])
check('composite: ON CONFLICT names BOTH columns', 'ON CONFLICT("session_id", "id")' in comp, comp)
check('composite: DO UPDATE SET updates the non-key column', 'EXCLUDED."name"' in comp, comp)
check('composite: DO UPDATE SET excludes EVERY conflict column (not session_id, not id)',
      'EXCLUDED."session_id"' not in comp and 'EXCLUDED."id"' not in comp, comp)

one = pg_sql({'id': 'i', 'session_id': 's', 'name': 'lamp'}, 'session_id')
check('single-column upsert unaffected: ON CONFLICT("session_id")', 'ON CONFLICT("session_id")' in one, one)
check('single-column: non-conflict columns still updated (id, name)',
      'EXCLUDED."name"' in one and 'EXCLUDED."id"' in one, one)

# ── 3. End-to-end on SQLite (fallback path): a composite upsert updates the matching row only ─
def run(src):
    prog = transform(_P.parse(src), src); it = MohioInterpreter(); it.run_declarations(prog)
    it.shown = []; it.run(prog); return it.shown

E2E = (
    'connect db as sqlite from env.DATABASE_URL\n'
    'upsert db.items\n    match session_id to "s1", id to "A"\n    name "apple"\nupsert: done\n'
    'upsert db.items\n    match session_id to "s1", id to "B"\n    name "banana"\nupsert: done\n'
    'upsert db.items\n    match session_id to "s1", id to "B"\n    name "BANANA2"\nupsert: done\n'
    'retrieve a from db.items\n    match session_id to "s1", id to "A"\n    on.success show a.name\nretrieve: done\n'
    'retrieve b from db.items\n    match session_id to "s1", id to "B"\n    on.success show b.name\nretrieve: done\n'
    'find allrows in db.items\n    where session_id is "s1"\nfind: done\n'
    'show "count {{ allrows.count }}"\n'
)
out = run(E2E)
check("e2e: the third upsert UPDATED row B (banana -> BANANA2)", 'BANANA2' in out, str(out))
check("e2e: row A (same session, different id) is untouched", 'apple' in out, str(out))
check("e2e: no duplicate row -- composite match updated in place, count stays 2",
      'count 2' in out, str(out))

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
