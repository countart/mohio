# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""T1-COMPARE-HANDLER-DISPATCH (2026-08-20): `compare` dispatches its result handlers through
the SAME shared mechanism every other verb block uses.

THE GAP THIS CLOSES. `compare_body` (mohio.lark:1634) embeds a bare singular `result_handler`
directly in its own body grammar, never routing through the shared `result_handlers` wrapper --
the same shape `find_body` (mohio.lark:1601) uses, and the ONLY two rules in the grammar that do
(verified by sweep, not assumed). `find` collected those handlers into `node.handlers` and
dispatched them via `_handle_success`; `compare_block`'s transformer left them mixed into
`body` with `handlers=[]`, and `_exec_CompareBlock` blindly `_exec`'d every body item. So a
compare block carrying on.success / on.failure / when / otherwise died at RUNTIME with
"No executor for 'OnSuccess'". Reproduced live before the fix, all four forms.

Always broken, never a regression: before C1-completion (`5eb537e`) unwrapped `compare_body`,
the same loop iterated raw Lark `Tree` objects and every handler was silently INERT. The unwrap
turned a silent no-op into a loud crash -- same pre-existing gap, better error message.

THE MODEL, mirrored not invented (mohio.lark:2609-2615's own STATE-vs-CONDITION comment, and
`_handle_success`'s docstring):
    STATE      on.failure / on.success -- did it break? on.failure fires and EXITS the block.
    CONDITION  when / otherwise        -- what came back? Post-result, non-failure path only.
`compare` is a pure computation: no connection to drop, no driver to raise. So a wired
`on.failure` is correct and simply stays quiet whenever the comparison itself succeeds, exactly
as find's on.failure stays quiet against a healthy database.

ORDERING NOTE: `comparison` is now bound BEFORE the body and handlers run, not after -- a `when`
clause conditions on what came back (`when comparison.equal`), so the result must exist by the
time the conditional set is evaluated. Reading `comparison` after the block is unchanged (guarded
below).

BOUNDARY -- what this does NOT cover. compare_body's other two alternatives are untouched:
`return_clause` inside a compare block still has NO executor ("No executor for 'ReturnClause'",
confirmed identical at clean HEAD `d93c090`, so pre-existing and not caused by this fix), and
`calculate_block` remains a `_stub`. Both are separate gaps, deliberately out of scope here.

Real .mho source through the full pipeline throughout (T1-TEST-REAL-PATH-STANDARD).

Run: `python tests/test_compare_handlers.py`.
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

def run_real(src):
    prog = ast_transform(P.parse(src), src)
    it = MohioInterpreter()
    it.run_declarations(prog)
    return it, it.run(prog)


# -- STATE channel: on.success --------------------------------------------------------------
it, _ = run_real('a 5\nb 5\ncompare a to b\n    on.success\n        show "SUCCESS"\ncompare: done\n')
check("on.success fires on a successful compare (block form)", it.shown == ["SUCCESS"], it.shown)

it, _ = run_real('a 5\nb 5\ncompare a to b\n    on.success show "SUCCESS-INLINE"\ncompare: done\n')
check("on.success fires in the INLINE form too", it.shown == ["SUCCESS-INLINE"], it.shown)

# on.failure is the ERROR path, never the "not equal" path. A compare that computed fine did
# not break, so on.failure must stay silent even when the two values differ.
it, _ = run_real('a 5\nb 9\ncompare a to b\n    on.failure\n        show "FAILURE"\ncompare: done\n')
check("on.failure does NOT fire on a successful compare (values differ != it broke)",
      it.shown == [], it.shown)

# -- CONDITION channel: when / otherwise, both branches --------------------------------------
_WHEN_SRC = ('a {a}\nb {b}\ncompare a to b\n'
             '    when comparison.equal\n        show "EQUAL"\n'
             '    otherwise\n        show "NOT-EQUAL"\n'
             'compare: done\n')
it, _ = run_real(_WHEN_SRC.format(a=5, b=5))
check("when fires on the matching condition (equal)", it.shown == ["EQUAL"], it.shown)

it, _ = run_real(_WHEN_SRC.format(a=5, b=9))
check("otherwise fires when no when matched (not equal)", it.shown == ["NOT-EQUAL"], it.shown)

# -- Both stages, in the ruled order: STATE first, then CONDITION ----------------------------
it, _ = run_real('a 5\nb 9\ncompare a to b\n'
                 '    on.success\n        show "1-STATE"\n'
                 '    when comparison.equal\n        show "2-EQUAL"\n'
                 '    otherwise\n        show "2-NOT-EQUAL"\n'
                 'compare: done\n')
check("STATE runs before CONDITION, and both run (on.success then otherwise)",
      it.shown == ["1-STATE", "2-NOT-EQUAL"], it.shown)

# -- The ordering fix: `comparison` is bound before the conditional set is evaluated ---------
# If the bind happened after the handler DISPATCH, `when comparison.equal` would raise
# undeclared_variable instead of branching. Mutation-proved at exactly that point: moving
# `ctx.set('comparison', rv)` below the `_handle_success` call fails this and the three other
# ordering-dependent cases (8 passed / 4 failed). Note the weaker mutation -- moving the bind
# after the BODY loop but still above dispatch -- correctly does NOT fail, because the body
# carries no handlers and the bind still precedes the conditional set; dispatch order is the
# real invariant here, not body order.
it, _ = run_real('a 7\nb 7\ncompare a to b\n'
                 '    when comparison.equal\n        show "BOUND-IN-TIME"\ncompare: done\n')
check("`comparison` is readable from inside a when clause (bound before dispatch)",
      it.shown == ["BOUND-IN-TIME"], it.shown)

# -- Regressions: everything compare already did must still do it ----------------------------
it, _ = run_real('a 10\nb 4\ncompare a to b\ncompare: done\n'
                 'show ("e=" & comparison.equal & " d=" & comparison.difference '
                 '& " l=" & comparison.larger & " s=" & comparison.smaller)\n')
check("regression: comparison still readable AFTER the block, numeric fields intact",
      it.shown == ["e=false d=6 l=10 s=4"], it.shown)

it, _ = run_real('a 5\nb 5\ncompare a to b\ncompare: done\nshow "no-handlers-ok"\n')
check("regression: a compare with NO handlers at all still runs",
      it.shown == ["no-handlers-ok"], it.shown)

# 5eb537e's lone-otherwise fail-loud must survive the transformer partition (the validator now
# reads the separated handler list rather than the mixed body).
try:
    run_real('a 5\nb 5\ncompare a to b\n    otherwise\n        show "always"\ncompare: done\n')
    check("regression: lone otherwise on compare still fails loud (5eb537e preserved)", False,
          "no error was raised")
except MohioCompileError as e:
    check("regression: lone otherwise on compare still fails loud (5eb537e preserved)",
          'otherwise requires a when' in str(e), str(e))

# on.failure/on.success legitimize an otherwise with no `when` (the widened rule) -- and now
# that otherwise must actually FIRE, not merely parse.
it, _ = run_real('a 5\nb 9\ncompare a to b\n'
                 '    on.success show "ACK"\n    otherwise show "FALLBACK"\ncompare: done\n')
check("on.success + otherwise (no when): both parse AND both fire",
      it.shown == ["ACK", "FALLBACK"], it.shown)

# -- Sibling guard: find, the only other bare-`result_handler` site, must stay working -------
it, _ = run_real('colors as list "red", "blue", "green"\n'
                 'find hit in colors\n    where value is "blue"\n'
                 '    on.success\n        show "FIND-SUCCESS"\nfind: done\n')
check("sibling: find's handler dispatch still works (unchanged by this fix)",
      it.shown == ["FIND-SUCCESS"], it.shown)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
