# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""RUN 1 -- the Zork empty-check matrix (T1-EVAL-SIMPLE-FAILLOUD FORK 1, build-diary
Entry 2026-08-19-08).

THE BUG (pre-fix, verified against commit c023363~1, the commit immediately before FORK 1
landed): `retrieve`'s single-record paths (the `.one` default, and `.first`/`.last` with no
match) called `_handle_failure(node.handlers, ctx, ...)` on a genuine "no record found".
`_handle_failure` -- with no `on.failure` handler declared, which is the ordinary case for a
`when`/`otherwise` conditional set -- falls straight through to `_handle_otherwise`, which
looks ONLY for an `OtherwiseClause` and runs it. It never calls `_run_conditional_set`, so
every `when` clause in the block (including `when item is empty`/`when item is.empty`) was
never even evaluated on a genuine miss -- `otherwise` fired unconditionally, regardless of
the emptiness operator's spelling (dotted vs spaced) or whether the check lived inside the
`retrieve` block or in a separate `check` block afterward.

THE ROOT CAUSE, precisely: this was never about emptiness-PREDICATE evaluation being wrong.
`_match_where_condition` (mohio_interpreter.py) handles `wc_empty` (dotted `is.empty`) and
`wc_is_empty` (spaced `is empty`) via the exact same code path (`if data in ('wc_empty',
'wc_not_empty', 'wc_is_empty')`) -- dotted vs spaced makes no difference there and never did.
The bug was earlier: a genuine miss on `.one`/`.first`/`.last` never reached that evaluator at
all, because it never got bound and routed through `_bind_and_succeed` -> `_handle_success` ->
`_run_conditional_set` (the function that actually walks `when` clauses). It took the
`_handle_failure` exit instead, which skips straight to `otherwise`.

Two of the four forms (#1 dotted-separate, #2 spaced-separate) happened to still produce the
correct MISS output even pre-fix -- NOT because the emptiness check worked differently there,
but because `_exec_CheckBlock`'s subject read is deliberately exempt from the undeclared-
variable fail-loud (FORK 2 / A3): a never-bound `item` read leniently as empty, and `check
item / when item is empty` correctly detected THAT emptiness. It was never the intended
mechanism -- it was the pre-fail-loud leniency accidentally producing the right answer on an
unbound name. Forms #3/#4 (in-block) had no such accident to fall back on: `_handle_failure`
short-circuited before `item` was ever bound to anything, in-block `when`/`otherwise` never
even got a chance to run.

THE FIX (already landed, commit c023363, "compiler: undeclared/forgotten variable reads fail
loud (T1-EVAL-SIMPLE-FAILLOUD, FORK-1/2)"): `.one`/`.first`/`.last`'s no-match branches
(held-list and DB-backed, both) now call `_bind_and_succeed(MohioValue(None, 'null'))` instead
of `_handle_failure`. This binds a REAL, declared null and runs the normal success path
(`_handle_success` -> `_run_conditional_set`), so `when`/`otherwise` genuinely evaluate against
a real (empty) value -- for BOTH the in-block and separate-check placements, and for BOTH the
dotted and spaced emptiness operator, uniformly. This is a root fix, not a funnel onto the
form #1 mechanism: `_match_where_condition` was never touched, and never needed to be -- the
fix is entirely in getting a real value bound before evaluation ever starts.

NOT fixed here, a different bug shape, explicitly out of scope (found during the sibling
sweep for this fix, not touched):
  - `grab`/`get` (_exec_GrabBlock): binds `ctx.set(node.name, result)` unconditionally
    (unlike the OLD retrieve), but on a miss with no `on.failure` handler declared, it never
    calls `_handle_success`/`_run_conditional_set` at all -- `when`/`otherwise` never fire,
    on EITHER branch, not just the miss branch. Different mechanism, different fix, not part
    of the Zork empty-check matrix's finding.
  - `check_exists`/`.exists` variant: same absence -- `_run_success()` there is a local
    closure that only checks for `OnSuccess`, never reaches `_run_conditional_set` on any
    branch.

Real .mho source through the full pipeline throughout (T1-TEST-REAL-PATH-STANDARD): parse ->
transform -> run_declarations -> run, asserting on real `show` output, exactly the four forms
from the Zork census, DB-backed AND held-list sourced.

Run: `python tests/test_retrieve_emptiness_matrix.py`.
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
    it.run(prog)
    return it


DB_SEED = ('connect db as sqlite from env.DATABASE_URL\n'
           'shape Thing\n    name as text\nshape: done\n'
           'save to db.things\n    name "widget"\nsave: done\n')
HELD_SEED = 'things as list "widget", "gadget"\n'

WHEN_BODY_DOTTED = '    when item is.empty\n        show "MISS"\n    otherwise\n        show "HIT"\n'
WHEN_BODY_SPACED = '    when item is empty\n        show "MISS"\n    otherwise\n        show "HIT"\n'


def db_form(when_body, inblock):
    if inblock:
        return ('retrieve item from db.things\n    match name to {MATCH}\n' + when_body
                + 'retrieve: done\n')
    return ('retrieve item from db.things\n    match name to {MATCH}\nretrieve: done\n'
            'check item\n' + when_body + 'check: done\n')


def held_form(when_body, inblock):
    if inblock:
        return ('retrieve item from things\n    match value to {MATCH}\n' + when_body
                + 'retrieve: done\n')
    return ('retrieve item from things\n    match value to {MATCH}\nretrieve: done\n'
            'check item\n' + when_body + 'check: done\n')


FORMS = [
    ("1_dotted_separate", DB_SEED + db_form(WHEN_BODY_DOTTED, inblock=False)),
    ("2_spaced_separate", DB_SEED + db_form(WHEN_BODY_SPACED, inblock=False)),
    ("3_spaced_inblock",  DB_SEED + db_form(WHEN_BODY_SPACED, inblock=True)),   # the migration target
    ("4_dotted_inblock",  DB_SEED + db_form(WHEN_BODY_DOTTED, inblock=True)),
]

HELD_FORMS = [
    ("1_dotted_separate_held", HELD_SEED + held_form(WHEN_BODY_DOTTED, inblock=False)),
    ("2_spaced_separate_held", HELD_SEED + held_form(WHEN_BODY_SPACED, inblock=False)),
    ("3_spaced_inblock_held",  HELD_SEED + held_form(WHEN_BODY_SPACED, inblock=True)),
    ("4_dotted_inblock_held",  HELD_SEED + held_form(WHEN_BODY_DOTTED, inblock=True)),
]

for label, tmpl in FORMS + HELD_FORMS:
    empty_src = tmpl.format(MATCH='"nonexistent"')
    found_src = tmpl.format(MATCH='"widget"')
    it_empty = run_real(empty_src)
    it_found = run_real(found_src)
    check(f"{label}: genuine empty result -> MISS branch fires",
          it_empty.shown == ["MISS"], it_empty.shown)
    check(f"{label}: found result -> otherwise/HIT branch fires",
          it_found.shown == ["HIT"], it_found.shown)


# The natural, migration-target form gets its own explicit, named assertion (not just buried
# in the loop above) -- this is the exact form the Zork migration is blocked on.
_natural_empty = run_real(DB_SEED + db_form(WHEN_BODY_SPACED, inblock=True).format(MATCH='"nonexistent"'))
_natural_found = run_real(DB_SEED + db_form(WHEN_BODY_SPACED, inblock=True).format(MATCH='"widget"'))
check("NATURAL FORM (subjected, in-block, spaced -- `when item is empty`): empty -> MISS",
      _natural_empty.shown == ["MISS"], _natural_empty.shown)
check("NATURAL FORM (subjected, in-block, spaced -- `when item is empty`): found -> HIT",
      _natural_found.shown == ["HIT"], _natural_found.shown)


print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
