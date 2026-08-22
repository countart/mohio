# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""T1-GUARD-FAILOPEN, Part B (2026-08-19): reconcile get/grab and check_exists's .exists
variant with retrieve's RUN-1 fix.

THE GAP (a DIFFERENT shape than retrieve's old bug -- there, `otherwise` fired WRONGLY on a
genuine miss; here, on a miss with no `on.failure` declared, NEITHER `when` nor `otherwise`
ever ran at all):
  - _exec_GrabBlock (get/grab): bound `ctx.set(node.name, result)` unconditionally, but only
    called _handle_success (which walks when/otherwise) on a HIT. A miss with no on.failure
    handler fell through to a bare `return result` -- when/otherwise silently never evaluated.
  - check_mioql_block's .exists variant: `_run_success()` there is a local closure that only
    ever ran an OnSuccess handler, on the HIT path only. when/otherwise were wired into the
    grammar (result_handlers allows CheckWhen/OtherwiseClause there) but the interpreter never
    reached _run_conditional_set on EITHER outcome.

THE FIX: both now bind a real value and call _handle_success on every legitimate outcome
(found AND not-found), exactly mirroring retrieve's _bind_and_succeed. A genuine driver error
(missing table/column) is caught separately and routed through on.failure/fail loud, same as
Part A gives retrieve/find.

Real .mho source through the full pipeline throughout (T1-TEST-REAL-PATH-STANDARD).

Run: `python tests/test_guard_failopen_grab_checkexists.py`.
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

WHEN_OTHERWISE = '    when item is empty\n        show "MISS"\n    otherwise\n        show "HIT"\n'

# ── grab: real miss -> when-empty fires (was completely silent, neither branch) ────────────
it1, _ = run_real(SEED + 'grab item from db.things\n    match name to "nonexistent"\n'
                   + WHEN_OTHERWISE + 'grab: done\n')
check("grab real miss now fires when-empty (was silent)", it1.shown == ["MISS"], it1.shown)

it2, _ = run_real(SEED + 'grab item from db.things\n    match name to "widget"\n'
                   + WHEN_OTHERWISE + 'grab: done\n')
check("grab found still fires otherwise/HIT (regression guard)", it2.shown == ["HIT"], it2.shown)

# ── get: shares grab's runtime -- same fix applies ──────────────────────────────────────────
it3, _ = run_real(SEED + 'get item from db.things\n    match name to "nonexistent"\n'
                   + WHEN_OTHERWISE + 'get: done\n')
check("get real miss now fires when-empty (was silent)", it3.shown == ["MISS"], it3.shown)

# ── grab: genuine driver error -> fails loud, clean db_error (Part A parity) ───────────────
_, r4 = run_real('connect db as sqlite from env.DATABASE_URL\n'
                  'grab item from db.ghost_table\n    match id to 1\ngrab: done\n'
                  'show "unreachable"\n')
check("grab on a genuine error (missing table) fails loud",
      r4.get('status') == 500 and 'ghost_table' in str(r4.get('body', '')), r4)

# ── check exists: found -> when/otherwise fires (was completely inert on BOTH outcomes) ────
it5, _ = run_real(SEED + 'check exists hit in db.things\n    match name to "widget"\n'
                   '    when hit is true\n        show "FOUND"\n    otherwise\n        show "NOTFOUND"\n'
                   'check: done\n')
check("check exists found now fires when (was inert)", it5.shown == ["FOUND"], it5.shown)

it6, _ = run_real(SEED + 'check exists hit in db.things\n    match name to "ghost-xyz"\n'
                   '    when hit is true\n        show "FOUND"\n    otherwise\n        show "NOTFOUND"\n'
                   'check: done\n')
check("check exists not-found now fires otherwise (was inert)", it6.shown == ["NOTFOUND"], it6.shown)

# ── check exists: genuine driver error -> fails loud ────────────────────────────────────────
_, r7 = run_real('connect db as sqlite from env.DATABASE_URL\n'
                  'check exists hit in db.ghost_table_2\n    match id to 1\ncheck: done\n'
                  'show "unreachable"\n')
check("check exists on a genuine error (missing table) fails loud",
      r7.get('status') == 500 and 'ghost_table_2' in str(r7.get('body', '')), r7)


print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
