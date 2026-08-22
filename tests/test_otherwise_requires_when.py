# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""T1-OTHERWISE-HARDENING, Part C (2026-08-19), COMPLETED per Ronnie's FINAL ruling
(2026-08-20): a lone `otherwise` -- nothing preceding it in the same block, not even
on.failure/on.success -- fails loud, everywhere, no verb exempt.

C1 ORIGINAL (RUN 2, 2026-08-19): a lone `otherwise` with zero `when` clauses used to fire
UNCONDITIONALLY. Fixed at check-time in `check_block`'s own transformer and the shared
`result_handlers` transformer (retrieve/grab/save/update/remove/pull/save-or-update/connect/
check_mioql/...).

TWO gaps found completing this, both closed here:

1. **RUN 2's rule was too STRICT, a real bug of its own, found while completing this**: it
   only checked for `when`, so it wrongly rejected the real, useful `on.failure show "broke" /
   otherwise show "empty"` pattern (no `when` at all) on every `result_handlers`-based verb --
   confirmed live: `retrieve item ... / on.failure ... / otherwise ... / retrieve: done` raised
   `MohioCompileError` under the original rule. This went unnoticed because the pre-existing
   `tests/test_otherwise_spec.py`'s own "find, has results -> otherwise fires as the fallback"
   test uses exactly this shape on `find` -- which RUN 2 never reached (see gap 2) -- so nothing
   caught it. Corrected: `on.failure`/`on.success` now also legitimize an otherwise, not just
   `when`. Shared in ONE helper, `MohioTransformer._validate_lone_otherwise` (see its own
   docstring for the full rule), called from every site below instead of duplicated per site.

2. **RUN 2's fix never reached `find`/`compare`**: both embed a bare `result_handler` directly
   in their own body grammar (`find_body`/`compare_body`), never routing through the shared
   `result_handlers` wrapper RUN 2 patched -- confirmed live via `test_otherwise_spec.py`'s
   passing "find, otherwise ALONE is the fallback" test (now corrected to assert the opposite,
   per the new ruling). Fixed by calling the same shared helper from `find_block`'s and
   `compare_block`'s own transformer methods. Along the way, found and fixed a genuinely
   separate, pre-existing bug in `compare_block`'s transformer: `compare_body*`'s repetitions
   were never unwrapped (no dedicated transformer method for `compare_body` itself), so
   `CompareBlock.body` held raw, un-flattened Lark `Tree` objects instead of real statement
   nodes -- `_exec_CompareBlock`'s `for stmt in node.body: self._exec(stmt, ctx)` was iterating
   Trees, not statements, so EVERY `compare_body` item (return_clause/calculate_block/handlers
   alike) was silently inert. Fixed as a necessary side effect of making this check actually
   see compare's real children, not a separate initiative.

C2 (unchanged from RUN 2, still confirmed correct by construction): an `otherwise` cannot
bleed across block boundaries -- every block-shaped grammar rule ends in a MANDATORY `closer`,
so Earley cannot attach a later block's otherwise_clause as a child of an earlier block's node.

Real .mho source through the full pipeline throughout (T1-TEST-REAL-PATH-STANDARD). The real
corpus (cookbook/examples/tests, including Zork's full `index.mho`) was swept for lone-otherwise
usage on find/compare before this landed: zero hits -- nothing was silently made invalid.

Run: `python tests/test_otherwise_requires_when.py`.
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); os.chdir(ROOT)
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')
os.environ.setdefault('MOHIO_ENCRYPTION_KEY', 'testkey')

from lark import Lark
from mohio_transformer_ast import transform as ast_transform, MohioCompileError
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


def transform_only(src):
    return ast_transform(P.parse(src), src)

def run_real(src):
    prog = ast_transform(P.parse(src), src)
    it = MohioInterpreter()
    it.run_declarations(prog)
    return it, it.run(prog)


# ── C1: lone otherwise in a standalone check block -> fails loud at CHECK time ─────────────
try:
    transform_only('x 5\ncheck x\n    otherwise\n        show "always"\ncheck: done\n')
    check("C1: lone otherwise in a check block fails loud at check-time", False,
          "no error was raised")
except MohioCompileError as e:
    check("C1: lone otherwise in a check block fails loud at check-time",
          'otherwise requires a when' in str(e), str(e))

# ── C1: lone otherwise inside a verb block's result handlers -> fails loud at CHECK time ───
try:
    transform_only(
        'connect db as sqlite from env.DATABASE_URL\n'
        'shape Thing\n    name as text\nshape: done\n'
        'retrieve item from db.things\n    match name to "x"\n'
        '    otherwise\n        show "always"\n'
        'retrieve: done\n')
    check("C1: lone otherwise in a retrieve block fails loud at check-time", False,
          "no error was raised")
except MohioCompileError as e:
    check("C1: lone otherwise in a retrieve block fails loud at check-time",
          'otherwise requires a when' in str(e), str(e))

# ── C1 regression guards: legitimate combinations still work, no false positives ───────────
it_a, _ = run_real('x 5\ncheck x\n    when 5\n        show "five"\n    otherwise\n        show "other"\ncheck: done\n')
check("C1-regression: when+otherwise together still works", it_a.shown == ["five"], it_a.shown)

it_b, _ = run_real(
    'connect db as sqlite from env.DATABASE_URL\n'
    'shape Thing2\n    name as text\nshape: done\n'
    'save to db.things2\n    name "widget"\nsave: done\n'
    'retrieve item from db.things2\n    match name to "widget"\n'
    '    when item is empty\n        show "MISS"\n'
    'retrieve: done\n')
check("C1-regression: a when with NO otherwise (no fallback declared) still works",
      it_b.shown == [], it_b.shown)

it_c, _ = run_real(
    'connect db as sqlite from env.DATABASE_URL\n'
    'shape Thing3\n    name as text\nshape: done\n'
    'save to db.things3\n    name "widget"\nsave: done\n'
    'retrieve item from db.things3\n    match name to "widget"\n'
    '    on.success show "ack"\n'
    'retrieve: done\n')
check("C1-regression: on.success alone (no when/otherwise at all) still works",
      it_c.shown == ["ack"], it_c.shown)

# ── C2: a two-block sequence -- block 1 has no otherwise, block 2 has one -- must not bleed ─
it_d, _ = run_real(
    'x 5\n'
    'check x\n    when 999\n        show "block1-should-never-fire"\ncheck: done\n'
    'y 5\n'
    'check y\n    when 5\n        show "block2-when"\n    otherwise\n        show "block2-otherwise"\ncheck: done\n')
check("C2: block1's unmatched when does not borrow block2's otherwise, and block2 fires its own when",
      it_d.shown == ["block2-when"], it_d.shown)

# The mirror case: block 1 HAS otherwise, block 2 does not -- block1's otherwise must not
# leak forward and fire again for block 2.
it_e, _ = run_real(
    'x 999\n'
    'check x\n    when 5\n        show "block1-when"\n    otherwise\n        show "block1-otherwise"\ncheck: done\n'
    'y 999\n'
    'check y\n    when 5\n        show "block2-when-should-never-fire"\ncheck: done\n')
check("C2: block1's otherwise fires once for its own miss, and does not re-fire for block2",
      it_e.shown == ["block1-otherwise"], it_e.shown)


# ── Gap 2: find/compare, RUN 2's original C1 never reached them ────────────────────────────
try:
    transform_only(
        'connect db as sqlite from env.DATABASE_URL\n'
        'shape Thing4\n    name as text\nshape: done\n'
        'find hit in db.things4\n    where name is "x"\n    otherwise show "always"\nfind: done\n')
    check("Gap-2: lone otherwise on find fails loud at check-time", False, "no error was raised")
except MohioCompileError as e:
    check("Gap-2: lone otherwise on find fails loud at check-time",
          'otherwise requires' in str(e), str(e))

try:
    transform_only('hold a 1\nhold b 2\ncompare a to b\n    otherwise show "always"\ncompare: done\n')
    check("Gap-2: lone otherwise on compare fails loud at check-time", False, "no error was raised")
except MohioCompileError as e:
    check("Gap-2: lone otherwise on compare fails loud at check-time",
          'otherwise requires' in str(e), str(e))

# ── Gap 1: on.failure/on.success ALSO legitimize an otherwise (not just when) ──────────────
# This is the exact pattern RUN 2's original, too-strict rule wrongly rejected.
it_f, _ = run_real(
    'connect db as sqlite from env.DATABASE_URL\n'
    'shape Thing5\n    name as text\nshape: done\n'
    'save to db.things5\n    name "widget"\nsave: done\n'
    'retrieve item from db.things5\n    match name to "widget"\n'
    '    on.failure show "BROKE"\n    otherwise show "HIT"\n'
    'retrieve: done\n')
check("Gap-1: on.failure+otherwise (no when) on retrieve now parses AND runs correctly "
      "(was wrongly rejected by RUN 2's first version)", it_f.shown == ["HIT"], it_f.shown)

it_g, _ = run_real(
    'connect db as sqlite from env.DATABASE_URL\n'
    'shape Thing6\n    name as text\nshape: done\n'
    'save to db.things6\n    name "widget"\nsave: done\n'
    'find hit in db.things6\n    where name is "absent"\n'
    '    on.failure show "BROKE"\n    otherwise show "HIT"\nfind: done\n')
check("Gap-1+2 together: on.failure+otherwise (no when) on find (a genuinely empty result "
      "against a REAL, seeded table -- not a missing-table error) now parses and runs correctly",
      it_g.shown == ["HIT"], it_g.shown)

# compare's own runtime dispatch of handler-shaped body items is a separate, pre-existing gap
# (found as a byproduct of unwrapping compare_body correctly -- see this file's own header):
# _exec_CompareBlock blindly executes every body item as an ordinary statement, and there is no
# `_exec_OnSuccess`/`_exec_OtherwiseClause` for a bare handler node run that way (pre-existing,
# not introduced here -- before the unwrap fix this crashed identically, just on a raw Tree
# instead of a named handler type). Only the SYNTAX claim is tested here, not execution.
try:
    transform_only(
        'hold a 1\nhold b 2\ncompare a to b\n'
        '    on.success show "ACK"\n    otherwise show "HIT"\ncompare: done\n')
    check("Gap-1+2 together: on.success+otherwise (no when) on compare parses "
          "(compare's runtime handler-dispatch gap this note used to name is now CLOSED -- "
          "T1-COMPARE-HANDLER-DISPATCH, 2026-08-20; firing is asserted in "
          "tests/test_compare_handlers.py)",
          True)
except MohioCompileError as e:
    check("Gap-1+2 together: on.success+otherwise (no when) on compare parses", False, str(e))


print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
