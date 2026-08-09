# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""`modify every X in Y where <condition> ...` must filter by the declared condition -- a compound
condition (and/or/not) must never be silently dropped and treated as "no filter."

T0-1: `modify_block`'s transformer (`mohio_transformer_ast.py`) checked
`cond_types = ('Condition', 'And', 'Or', 'Not')` against the real AST class names
`Condition`/`AndCondition`/`OrCondition`/`NotCondition`. None of the compound names matched, so
`node.condition` silently became `None` for any `and`/`or`/`not` WHERE, and `_exec_ModifyBlock`
treats `condition is None` as "no filter" -- every row in the table was modified, not just the ones
the WHERE clause named. A single-comparison WHERE (no connector) was unaffected; only compound was
broken, which is exactly why it went unnoticed -- the common case looked fine.

Existing modify coverage (test_record_id.py, test_sql_battery.py, test_mongo_backend.py) only ever
exercises a single-comparison WHERE (`where id is 1`, `where tag is "a"`). None of it would have
caught this. This file is the compound-WHERE-specific coverage that was missing.

Cross-cutting note, not this file's bug to fix: `modify`'s WHERE routes through the exact same
`_eval_condition` (`mohio_interpreter.py`) that `while`/`if`/`unless`/`check when` use, and a mixed
`a and b or c` chain resolves to `a AND (b OR c)` here exactly as it does everywhere else --
confirmed by AST dump and by the case below.

UPDATE (CR built): the connector ruling shipped as `mohio_reachability.scan_mixed_connector_chain`
-- a CHECK-TIME error only, the RUNTIME grouping is deliberately untouched (the ruling's whole
point is that a mixed chain has no defined meaning to silently execute; `mio check` refusing it
before it ever runs is the fix, not a different runtime resolution). `modify`'s WHERE inherited the
rule automatically through the same AST-shape-based scan, with no `modify`-specific code written
for it -- confirmed below. The case that used to be a DOCUMENTATION-only test (locking in today's
silent grouping so a future change would show as a diff) is exactly that future change: it now
asserts the check-time error fires, which is the signal the ruling reached `modify` too. The
runtime-grouping assertion stays as its own, separate check -- still true, still unchanged, and
now doubly meaningful: it proves the check-time refusal isn't hiding a SECOND, silent runtime
change underneath it.

Run: `python tests/test_modify_where.py`.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mohio_data
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter
from mohio_reachability import run_scans

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


SEED = ('connect db as sqlite from env.DATABASE_URL\n'
        'save to db.items\n    id "1"\n    price 10\nsave: done\n'
        'save to db.items\n    id "2"\n    price 500\nsave: done\n'
        'save to db.items\n    id "3"\n    price 999\nsave: done\n')


def rows_after(modify_clause):
    """Seed the standard 3-row fixture, run one modify clause, return the resulting
    (id, price) pairs ordered by id. Each call gets a fresh in-memory database."""
    src = (SEED + modify_clause +
           'find r in db.items\n    order.up by id\nfind: done\n'
           'give back 200 r\n')
    it = MohioInterpreter()
    result = it.run(transform(P.parse(src), src))
    body = result.get('body') if isinstance(result, dict) else result
    return sorted(((int(r['id']), int(r['price'])) for r in body), key=lambda t: t[0])


# ── 1. Compound AND -- the exact T0-1 reproduction, only the matching row moves ───────────────
MOD_AND = ('modify every row in db.items\n'
           '    where price is above 5 and price is below 100\n'
           '    apply row\n        price 1\n    apply: done\nmodify: done\n')
_r = rows_after(MOD_AND)
check("compound AND: only id=1 (price 10, in range 5-100) is modified",
      _r == [(1, 1), (2, 500), (3, 999)], _r)

# ── 2. Compound OR -- rows matching EITHER side move, the row matching NEITHER does not ───────
MOD_OR = ('modify every row in db.items\n'
          '    where price is below 20 or price is above 900\n'
          '    apply row\n        price 1\n    apply: done\nmodify: done\n')
_r = rows_after(MOD_OR)
check("compound OR: id=1 (below 20) and id=3 (above 900) modified, id=2 untouched",
      _r == [(1, 1), (2, 500), (3, 1)], _r)

# ── 3. Compound NOT -- the negated condition, not the bare one, controls the match ─────────────
MOD_NOT = ('modify every row in db.items\n'
           '    where not price is above 100\n'
           '    apply row\n        price 1\n    apply: done\nmodify: done\n')
_r = rows_after(MOD_NOT)
check("compound NOT: only id=1 (not above 100) is modified",
      _r == [(1, 1), (2, 500), (3, 999)], _r)

# ── 4. No WHERE at all -- must still modify every row (regression guard, not this bug's shape) ─
MOD_ALL = ('modify every row in db.items\n'
           '    apply row\n        price 1\n    apply: done\nmodify: done\n')
_r = rows_after(MOD_ALL)
check("no WHERE: every row is modified (unchanged correct behavior)",
      _r == [(1, 1), (2, 1), (3, 1)], _r)


# ── Adversarial: boundary, zero-match, all-match, and >2-term chains ──────────────────────────
print("\n=== adversarial: boundary values and degenerate match sets ===")

# Strict inequality at the exact boundary -- "above 10"/"below 999" must exclude the boundary
# values themselves, not just the interior. If the filter were ever accidentally inclusive,
# id=1 (price exactly 10) or id=3 (price exactly 999) would move and this would catch it.
MOD_BOUNDARY = ('modify every row in db.items\n'
                '    where price is above 10 and price is below 999\n'
                '    apply row\n        price 1\n    apply: done\nmodify: done\n')
_r = rows_after(MOD_BOUNDARY)
check("boundary: strict above/below excludes the boundary rows themselves (only id=2 moves)",
      _r == [(1, 10), (2, 1), (3, 999)], _r)

# Compound condition that matches ZERO rows -- must modify nothing, not error, not fall back to
# "no filter" (which is exactly the T0-1 failure mode: an unrecognized/mishandled condition
# silently behaving as if no WHERE were given at all).
MOD_ZERO = ('modify every row in db.items\n'
            '    where price is above 10000 and price is below 20000\n'
            '    apply row\n        price 1\n    apply: done\nmodify: done\n')
_r = rows_after(MOD_ZERO)
check("compound AND matching zero rows: nothing modified (not silently 'all rows')",
      _r == [(1, 10), (2, 500), (3, 999)], _r)

# Compound OR that happens to match every seeded row -- must modify all three, same observable
# result as MOD_ALL (no WHERE) but for a genuinely different reason (every row happens to satisfy
# the OR), so a filter that silently degraded to "no filter" would pass this one alone; it only
# means something paired with MOD_ZERO and MOD_AND above.
MOD_ALL_MATCH = ('modify every row in db.items\n'
                 '    where price is above 0 or price is below 0\n'
                 '    apply row\n        price 1\n    apply: done\nmodify: done\n')
_r = rows_after(MOD_ALL_MATCH)
check("compound OR matching every row: all three modified",
      _r == [(1, 1), (2, 1), (3, 1)], _r)

# Three-term chain, not just two -- confirms the fix isn't accidentally special-cased to a single
# AndCondition/OrCondition pair and actually recurses through nested condition nodes.
MOD_THREE = ('modify every row in db.items\n'
             '    where price is above 5 and price is below 1000 and price is not 500\n'
             '    apply row\n        price 1\n    apply: done\nmodify: done\n')
_r = rows_after(MOD_THREE)
check("three-term AND chain: id=1 and id=3 modified, id=2 excluded by the third term",
      _r == [(1, 1), (2, 500), (3, 1)], _r)


# ── CR built: modify's WHERE now check-time-refuses a mixed chain, same as while/if/unless ────
print("\n=== mixed and/or: CR built -- modify's WHERE now check-time-refuses it too ===")

# a = (price is above 100) = False for price=10; b = (price is below 5) = False for price=10;
# c = (price is above 5) = True for price=10.
#   (a and b) or c = (False and False) or True = True   -> row WOULD be modified
#   a and (b or c) = False and (False or True) = False   -> row is NOT modified
MOD_MIXED = ('modify every row in db.items\n'
             '    where price is above 100 and price is below 5 or price is above 5\n'
             '    apply row\n        price 999\n    apply: done\nmodify: done\n')
_mixed_seed = ('connect db as sqlite from env.DATABASE_URL\n'
               'save to db.items\n    id "1"\n    price 10\nsave: done\n')
_mixed_src = _mixed_seed + MOD_MIXED + 'find r in db.items\nfind: done\ngive back 200 r\n'
_it = MohioInterpreter()
_mixed_prog = transform(P.parse(_mixed_src), _mixed_src)
_result = _it.run(_mixed_prog)
_body = _result.get('body') if isinstance(_result, dict) else _result
_mixed_price = int(_body[0]['price'])
# CR is a CHECK-TIME refusal only -- the ruling's point is that a mixed chain should never run at
# all, not that it should run differently. This assertion is deliberately UNCHANGED: it proves the
# check-time fix (below) isn't quietly hiding a second, different runtime change underneath it.
check("mixed a-and-b-or-c groups as a-AND-(b-OR-c), matching while/if/unless -- NOT (a-and-b)-OR-c "
      "(runtime grouping is unchanged by CR -- it is a check-time-only refusal)",
      _mixed_price == 10, f"price={_mixed_price} (10 = ungrouped/not-matched, 999 = matched-and-modified)")
# T0-1's own note predicted this exact update: "this test will then need to assert a check-time
# error instead, and that update is the signal the ruling actually reached modify too." No
# modify-specific scanner code exists -- scan_mixed_connector_chain walks the whole program for
# the AndCondition/OrCondition node SHAPE, so modify's WHERE inherits the rule automatically
# through the same AST, exactly as `_eval_condition` already shares its runtime evaluation.
_mixed_errors, _mixed_warnings = run_scans(_mixed_prog)
check("mio check now REFUSES the mixed chain in modify's WHERE (CR reached modify automatically)",
      any('mixed and/or chain has no defined grouping' in str(e) for e in _mixed_errors),
      [str(e) for e in _mixed_errors])


print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
