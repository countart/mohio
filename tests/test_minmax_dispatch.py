# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""min/max shape-field enforcement (OQ-006) -- type-dependent dispatch guard.

OQ-006 locked "one word, one job, different context" for min/max: the SAME two words mean whatever
is natural for the thing being constrained.

    number field   ->  VALUE bound      (min 13 => value >= 13)
    text field     ->  LENGTH bound     (min 3  => char length >= 3)
    standalone max ->  ceiling only     (max 100, no min)
    standalone min ->  floor only       (min 5, no max)

This behavior was verified working on the real `_validate_against_shape` path; it had no dedicated
regression guard. This locks it so a change to the validation path cannot silently regress the
dispatch (e.g. back to checking digit count on numbers, the old bug).
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter

_raw = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         'mohio.lark'), encoding='utf-8').read()
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)


def shape_obj(prog, name):
    for st in prog.statements:
        if type(st).__name__ == 'ShapeDecl' and st.name == name:
            return st
    return None


def validate(shape_src, shape_name, field, value):
    """Return True if the value is REJECTED (fails validation), False if it passes."""
    interp = MohioInterpreter()
    prog = transform(P.parse(shape_src), shape_src)
    interp.run(prog)
    obj = shape_obj(prog, shape_name)
    errs = interp._validate_against_shape(obj, lambda fn, v=value: v if fn == field else None)
    return bool(errs)


# ── number field: min/max are VALUE bounds ────────────────────────────────────────────
NUM = 'shape Person\n    age as number min 13 max 120\nshape: done\n'
check("number: value below min rejected (age=10 < 13)", validate(NUM, 'Person', 'age', '10'))
check("number: value in range passes (age=25)", not validate(NUM, 'Person', 'age', '25'))
check("number: value above max rejected (age=150 > 120)", validate(NUM, 'Person', 'age', '150'))
check("number: min boundary passes (age=13)", not validate(NUM, 'Person', 'age', '13'))
check("number: max boundary passes (age=120)", not validate(NUM, 'Person', 'age', '120'))
check("number: just below min rejected (age=12)", validate(NUM, 'Person', 'age', '12'))
check("number: just above max rejected (age=121)", validate(NUM, 'Person', 'age', '121'))
# the OLD BUG was digit-count: a 3-digit in-range value like 100 must PASS (not be rejected as
# "too many digits"). This is the specific regression this guard exists to prevent.
check("number: 3-digit in-range value passes (age=100, not digit-count)",
      not validate(NUM, 'Person', 'age', '100'))


# ── text field: min/max are LENGTH bounds ─────────────────────────────────────────────
TXT = 'shape U\n    code as text min 3 max 6\nshape: done\n'
check("text: too short rejected (len 2 < 3)", validate(TXT, 'U', 'code', 'ab'))
check("text: in-length passes (len 4)", not validate(TXT, 'U', 'code', 'abcd'))
check("text: too long rejected (len 7 > 6)", validate(TXT, 'U', 'code', 'abcdefg'))
# a numeric-looking string is still length-checked, not value-checked, on a TEXT field
check("text: value is length-checked not value-checked ('99999' len 5 passes)",
      not validate(TXT, 'U', 'code', '99999'))


# ── standalone max (no min) ───────────────────────────────────────────────────────────
MAXONLY = 'shape T\n    score as number max 100\nshape: done\n'
check("standalone max: below passes (50)", not validate(MAXONLY, 'T', 'score', '50'))
check("standalone max: boundary passes (100)", not validate(MAXONLY, 'T', 'score', '100'))
check("standalone max: above rejected (101)", validate(MAXONLY, 'T', 'score', '101'))


# ── standalone min (no max) ───────────────────────────────────────────────────────────
MINONLY = 'shape M\n    qty as number min 5\nshape: done\n'
check("standalone min: below rejected (3)", validate(MINONLY, 'M', 'qty', '3'))
check("standalone min: boundary passes (5)", not validate(MINONLY, 'M', 'qty', '5'))
check("standalone min: above passes (99)", not validate(MINONLY, 'M', 'qty', '99'))

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
