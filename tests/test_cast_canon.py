# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
test_cast_canon.py — Locks the numeric-cast canon (Build 3).

Canon (for now): the DOTTED modifier `as.int` / `as.number` / `as.decimal`
is the cast. The SPACE form `as int` has no grammar rule — it silently
mis-parses (cast vanishes, numeric data stays a string, math then crashes).
Until the Rust rewrite (which will accept both), the space form must FAIL
LOUD at `mio check`, never run wrong.

This locks two things against regression:
  1. `as.int` correctly coerces null / None / "none" / "" / "0" -> 0,
     "5" -> 5, "564.987" -> 565 (rounds), and works inside math.
     (This is the Zork bug we spent a long time on — it must stay fixed.)
  2. Space-form `as int` (cast position) is a hard compile error with a
     "use as.int" hint — while the legit `NAME as integer` shape-field /
     assignment-target form is NOT flagged.

Run: python3 tests/test_cast_canon.py   (from the compiler root)
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import mohio_data
os.chdir(ROOT)
os.environ.setdefault('DATABASE_URL', ':memory:')
from pathlib import Path
from lark import Lark
from mohio_transformer_ast import transform
from mohio_transformer import validate
from mohio_interpreter import MohioInterpreter, MohioValue, Context

_raw = mohio_data.GRAMMAR_PATH.read_text()
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

passed = failed = 0
def check(name, got, want):
    global passed, failed
    ok = (got == want)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"   got={got!r} want={want!r}"))
    passed += ok; failed += (not ok)

def eval_expr(stmts, var='result'):
    """Run top-level stmts, return python value of `var`."""
    prog = transform(P.parse(stmts + '\n'), stmts + '\n')
    interp = MohioInterpreter(verbose=False)
    ctx = Context()
    for st in prog.statements:
        interp._exec(st, ctx)
    v = ctx.get(var)
    return v.to_python() if isinstance(v, MohioValue) else v

def space_cast_fails_loud(src):
    """
    True if a space-form cast never silently succeeds: either the parser
    rejects it (math context -> hard parse error) OR validate flags it with
    the 'Space-form cast' error (statement context -> silent-misparse caught).
    """
    from lark.exceptions import UnexpectedInput
    try:
        tree = P.parse(src + '\n')
    except UnexpectedInput:
        return True   # hard parse error — fails loud
    ctx = validate(tree, source=src + '\n', filename="<test>")
    return any('Space-form cast' in str(e) for e in (ctx.errors or []))

def validate_clean_of_cast_error(src):
    """True if validate produces NO space-form-cast error (legit form)."""
    tree = P.parse(src + '\n')
    ctx = validate(tree, source=src + '\n', filename="<test>")
    return not any('Space-form cast' in str(e) for e in (ctx.errors or []))

# ── 1. as.int coercion — every case Ronnie listed must hold ──────────────
print("\n=== as.int coercion (the Zork int bug — locked) ===")
check("none      -> 0",  eval_expr('v none\nresult = v as.int'), 0)
check('"none"    -> 0',  eval_expr('result = ("none") as.int'), 0)
check('"null"    -> 0',  eval_expr('result = ("null") as.int'), 0)
check('""        -> 0',  eval_expr('result = ("") as.int'), 0)
check('"0"       -> 0',  eval_expr('result = ("0") as.int'), 0)
check('"5"       -> 5',  eval_expr('result = ("5") as.int'), 5)
check('"564.987" -> 565', eval_expr('result = ("564.987") as.int'), 565)
check('"  7 "    -> 7',  eval_expr('result = ("  7 ") as.int'), 7)
check("in math ((v as.int)+1), v=none -> 1",
      eval_expr('v none\nresult = ((v as.int) + 1)'), 1)
check('in math ((v as.int)+1), v="7" -> 8',
      eval_expr('v "7"\nresult = ((v as.int) + 1)'), 8)

# ── 2. space-form cast must FAIL LOUD (cast position only) ───────────────
print("\n=== space-form 'as int' fails loud (cast position) ===")
check("'(v) as int' fails loud",
      space_cast_fails_loud('listen for GET "/x"\n    r (v as int)\n    give back 200 "ok"\nlisten: done'), True)
check("'v default \"0\" as int' (zork form) fails loud",
      space_cast_fails_loud('listen for GET "/x"\n    mi m default "0" as int\n    give back 200 "ok"\nlisten: done'), True)
check("'m val as number' fails loud",
      space_cast_fails_loud('mi val as number'), True)
check("'m val as decimal' fails loud",
      space_cast_fails_loud('mi val as decimal'), True)

# ── 3. legit `NAME as type` must NOT be flagged ──────────────────────────
print("\n=== legit declarations not flagged ===")
check("shape field 'x as integer' clean",
      validate_clean_of_cast_error('shape Patient\n    triage_level as integer\nshape: done'), True)
# NOTE: `x as int = "5"` was previously asserted clean -- wrong. It is the type-before-value trap
# (a type written BEFORE the value), which fails loud by design (cast PLACEMENT, not the `=`). The
# correct cast form is the dotted `as.int`, tested below.
check("dotted 'as.int' clean",
      validate_clean_of_cast_error('r (v as.int)'), True)

print(f"\nRESULTS: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
