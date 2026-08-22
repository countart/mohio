# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""T1-COMPARE-OPERAND-FAILLOUD (2026-08-20): a never-declared operand on `compare` fails loud,
on the same read path as every other user-facing variable read.

THE GAP THIS CLOSES. `_exec_CompareBlock`'s operand resolver called `ctx.get(name)` directly --
the LENIENT internal lookup, which returns None for a name that was never declared. So:

    a 5
    compare a to ghostoperand
    compare: done
    show ("equal=" & comparison.equal)      ->  equal=false        (silent, confident, WRONG)

A missing operand produced a plausible answer instead of naming the mistake. That is the exact
silent-default shape `c023363` (T1-EVAL-SIMPLE-FAILLOUD, diary 2026-08-19-04) was built to kill;
compare was simply a site that fix never reached, because it never went through `_eval` at all.

THE FIX. Compare's operands are ordinary user-facing reads from real .mho source, so they now
build the DottedName the grammar would have produced for a bare name and evaluate it through
`_eval` -- inheriting the existing fail-loud verbatim: same message, same `it`/`random`
exemptions, and any future change to that rule follows automatically. Deliberately NOT a copied
`ctx.exists()` guard at the compare site: a second spelling of the same rule is how two copies
drift apart.

THE DISTINCTION THAT MUST HOLD (2026-08-19-04's own ruling): variable-doesn't-exist fails loud;
value-doesn't-exist is normal. A DECLARED-but-empty operand (`a as text`) still compares like any
other empty value and must NOT fail loud. Guarded below -- that case is the one a careless fix
would break.

A never-declared operand is a developer mistake, not an operational failure, so it fails loud
rather than being converted into `on.failure` (the query-BROKE channel, T1-OUTCOME-STRUCTURE).
Guarded below with an on.failure handler declared: it must NOT swallow the fail-loud.

BOUNDARY: this changes operand RESOLUTION only. compare's handler dispatch is a separate,
already-landed unit (`666ac0a`, T1-COMPARE-HANDLER-DISPATCH, tests/test_compare_handlers.py).
`return_clause` inside a compare block still has no executor and `calculate_block` is still a
`_stub` -- both pre-existing, both untouched here.

Real .mho source through the full pipeline throughout (T1-TEST-REAL-PATH-STANDARD).

Run: `python tests/test_compare_operand_failloud.py`.
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

def failed_with(src, needle):
    """Run real source; return (failed_loud, detail). A runtime fail-loud surfaces as a
    returned 500 response carrying the message -- the same shape
    tests/test_guard_failopen_retrieve.py asserts on -- not as a raised Python exception."""
    try:
        _it, r = run_real(src)
    except Exception as e:                       # a transform-time refusal also counts
        return (needle in str(e)), str(e)
    body = str((r or {}).get('body', ''))
    return ((r or {}).get('status') == 500 and needle in body), r


# -- THE FIX: a never-declared operand fails loud, either side ------------------------------
ok, msg = failed_with('a 5\ncompare a to ghostoperand\ncompare: done\n', "undeclared_variable")
check("undeclared RIGHT operand fails loud", ok, msg)
check("...and the message names the offending operand", "ghostoperand" in str(msg), msg)

ok, msg = failed_with('b 5\ncompare ghostleft to b\ncompare: done\n', "undeclared_variable")
check("undeclared LEFT operand fails loud", ok, msg)
check("...and the message names the offending operand", "ghostleft" in str(msg), msg)

ok, msg = failed_with('compare ghosta to ghostb\ncompare: done\n', "undeclared_variable")
check("both operands undeclared fails loud", ok, msg)

# The precise regression this closes: it used to yield a confident equal=false.
ok, msg = failed_with(
    'a 5\ncompare a to ghostoperand\ncompare: done\nshow ("equal=" & comparison.equal)\n',
    "undeclared_variable")
check("the old silent 'equal=false' answer is gone (fails loud instead)", ok, msg)

# A developer mistake is not an operational failure: a declared on.failure must NOT swallow it.
ok, msg = failed_with(
    'a 5\ncompare a to ghostoperand\n    on.failure\n        show "SWALLOWED"\ncompare: done\n',
    "undeclared_variable")
check("a declared on.failure does NOT swallow the fail-loud (mistake != operational failure)",
      ok, msg)

# -- THE DISTINCTION: value-doesn't-exist stays normal --------------------------------------
# 2026-08-19-04: variable-doesn't-exist fails loud, value-doesn't-exist is ordinary. An empty
# TYPED declaration is declared -- it must still compare, not fail loud.
it, _ = run_real('a as text\nb as text\ncompare a to b\ncompare: done\n'
                 'show ("equal=" & comparison.equal)\n')
check("regression: DECLARED-but-empty operands still compare (empty == empty -> true)",
      it.shown == ["equal=true"], it.shown)

it, _ = run_real('a as text\nb 5\ncompare a to b\ncompare: done\n'
                 'show ("equal=" & comparison.equal)\n')
check("regression: one declared-empty vs one valued still compares (not equal)",
      it.shown == ["equal=false"], it.shown)

# `clear` calls ctx.set, never delete_var -- a cleared variable stays DECLARED, so it must
# compare rather than fail loud. Asserted on the fail-loud invariant only, deliberately not on
# the resulting equal value: a cleared operand and an empty typed declaration both DISPLAY as
# empty yet compare unequal (observed live, 2026-08-20) -- a pre-existing representation nuance
# that has nothing to do with this fix and is not being locked in either direction here.
_it, _r = run_real('a 5\nclear a\nb as text\ncompare a to b\ncompare: done\n'
                   'show ("equal=" & comparison.equal)\n')
check("regression: a CLEARED operand does NOT fail loud (clear leaves it declared)",
      (_r or {}).get('status') != 500 and _it.shown and _it.shown[0].startswith("equal="),
      (_r, _it.shown))

# -- Ordinary compare is untouched -----------------------------------------------------------
it, _ = run_real('a 10\nb 4\ncompare a to b\ncompare: done\n'
                 'show ("e=" & comparison.equal & " d=" & comparison.difference)\n')
check("regression: a normal compare still produces its full result",
      it.shown == ["e=false d=6"], it.shown)

it, _ = run_real('a 5\nb 5\ncompare a to b\n    on.success\n        show "OK"\ncompare: done\n')
check("regression: handler dispatch (666ac0a) still works alongside this fix",
      it.shown == ["OK"], it.shown)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
