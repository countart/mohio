# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
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

**SUPERSESSION (2026-08-20, T1-UPSERT-NO-CONSTRAINT, ruled Option A).** The Postgres SQL-shape
assertions in section 2 originally required `ON CONFLICT ("c1","c2")` + `DO UPDATE SET`. Postgres
no longer emits that form at all: ON CONFLICT hard-requires a UNIQUE constraint on the conflict
target, which Mohio's own auto-created tables never have on non-`id` columns, so upsert 500'd on
exactly the tables Mohio makes. It now emits UPDATE, then an INSERT guarded by `WHERE NOT EXISTS`
naming every key column -- correct with or without a constraint. Recorded as a documented
supersession, not a red test quietly edited to pass.

What this file EXISTS to prove is unchanged and still proven: a composite match must reach the
generated SQL as BOTH key columns (it used to be silently dropped to None in the transformer),
and must update only the matching row. Section 2's assertions were re-pointed at the new shape;
section 3's end-to-end SQLite block is UNTOUCHED -- it never depended on the Postgres SQL form,
it passed before and after, and it is the strongest evidence here that the real intent holds.
The composite case was additionally re-verified on a live Postgres 18 the day of the change
(3 upserts across two composite keys -> 2 rows, the repeated key updated in place).

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
    cap = {'all': []}
    class _Cur:
        # rowcount added 2026-08-20: the constraint-free upsert reads it to decide whether the
        # UPDATE matched (the old ON CONFLICT one-shot never needed it). 0 = nothing matched, so
        # the guarded INSERT branch is the one captured below.
        rowcount = 0
        def execute(self, sql, params): cap['sql'] = sql; cap['all'].append(sql)
        def close(self): pass
    class _Conn:
        def cursor(self): return _Cur()
        def commit(self): pass
        def rollback(self): pass
    pg.conn = _Conn()
    pg.upsert('items', fields, match)
    return ' | '.join(cap['all'])

# SUPERSEDED 2026-08-02 -> 2026-08-20 (T1-UPSERT-NO-CONSTRAINT, ruled Option A): these assertions
# used to require `ON CONFLICT("session_id", "id")` + `DO UPDATE SET ... EXCLUDED."name"`. That
# form is gone -- it hard-required a unique constraint the target table need not have. What this
# block still proves is UNCHANGED and is the whole point of the file: a composite match reaches
# the SQL as BOTH key columns, and the non-key column is the one carried as data. See the
# docstring for why the mechanism changed.
comp = pg_sql({'id': 'i', 'session_id': 's', 'name': 'lamp'}, ['session_id', 'id'])
check('composite: the guarded form replaced ON CONFLICT entirely',
      'ON CONFLICT' not in comp.upper(), comp)
check('composite: the insert is guarded by WHERE NOT EXISTS', 'WHERE NOT EXISTS' in comp.upper(), comp)
check('composite: the guard names BOTH key columns',
      '"session_id" = %s' in comp and '"id" = %s' in comp, comp)
check('composite: the non-key column is still carried as data', '"name"' in comp, comp)

one = pg_sql({'id': 'i', 'session_id': 's', 'name': 'lamp'}, 'session_id')
check('single-column upsert unaffected: guarded, no ON CONFLICT',
      'ON CONFLICT' not in one.upper() and 'WHERE NOT EXISTS' in one.upper(), one)
# Assert on the GUARD clause specifically, not the whole statement: in the single-column case
# `id` is an ordinary DATA column, so `SET "id" = %s` legitimately appears in the UPDATE. Only
# the WHERE NOT EXISTS predicate should be restricted to the key column.
# Cut at the guard's own closing paren: everything after it is the NEXT statement (step 3's
# compensating UPDATE), which legitimately mentions the data columns again.
_one_guard = (one.upper().split('WHERE NOT EXISTS', 1)[1].split(')', 1)[0]
              if 'WHERE NOT EXISTS' in one.upper() else '')
check('single-column: the GUARD predicate names ONLY the single key column',
      '"SESSION_ID" = %S' in _one_guard and '"ID" = %S' not in _one_guard, _one_guard or one)

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
