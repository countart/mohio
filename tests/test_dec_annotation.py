# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""`dec.N` precision annotation: declare a decimal capped to N places.

Distinct from the CAST `(x as.dec.N)` (a one-shot value transform): the ANNOTATION `x as dec.N`
puts a precision CONTRACT on the name. Every assignment is truncated to at most N places (the
contract's purpose), and non-numbers fail loud (the type contract). Works the same standalone and
in shape fields -- the foundation currency types build on.

`.pad` (dec.N.pad, zero-padded display) parses and precision-enforces, but its display-side padding
(10 -> 10.00) is not yet wired through the render paths, so it FAILS LOUD rather than silently not
padding. The precision form `dec.N` should be used until `.pad` display lands.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')

from lark import Lark
from mohio_transformer_ast import transform
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


def run(src):
    b = MohioInterpreter().run(transform(P.parse(src), src)).get('body')
    return b.to_python() if hasattr(b, 'to_python') else b


def fails(src):
    try:
        run(src); return False
    except Exception:
        return True


# ── standalone dec.N precision (truncation on assignment) ─────────────────────────────
check("dec.2 truncates 5.999 -> 5.99", run('x as dec.2\nx 5.999\ngive back 200 x') == 5.99)
check("dec.2 truncates 10.45676 -> 10.45", run('x as dec.2\nx 10.45676\ngive back 200 x') == 10.45)
check("dec.2 leaves 5.1 as 5.1", run('x as dec.2\nx 5.1\ngive back 200 x') == 5.1)
check("dec.5 caps at 5 places", run('x as dec.5\nx 10.123456789\ngive back 200 x') == 10.12345)
check("dec.0 caps to whole", run('x as dec.0\nx 10.9\ngive back 200 x') == 10.0)
check("dec.2 accepts an int (widening)", run('x as dec.2\nx 5\ngive back 200 x') == 5.0)
check("bare dec keeps full precision (no N cap)",
      run('x as dec\nx 5.999\ngive back 200 x') == 5.999)

# ── dec.N is a type contract too (non-numbers fail) ───────────────────────────────────
check("dec.2 rejects text", fails('x as dec.2\nx "cat"\ngive back 200 x'))
check("dec.2 read-before-assign is 0.0", run('x as dec.2\ngive back 200 x') == 0.0)

# ── dec.N in a shape field (currency foundation) ──────────────────────────────────────
S = 'shape Invoice\n    amount as dec.2\nshape: done\n'
check("shape dec.2 field truncates on create",
      run(S + 'create inv as sh.Invoice\n    amount 19.999\ncreate: done\ngive back 200 inv.amount') == 19.99)
check("shape dec.2 field rejects text",
      fails(S + 'create inv as sh.Invoice\n    amount "cat"\ncreate: done\ngive back 200 inv.amount'))

# ── cast still works and is distinct from the annotation ──────────────────────────────
check("cast (x as.dec.2) still truncates", run('give back 200 ((1/3) as.dec.2)') == 0.33)

# ── .pad now renders zero-filled display (value stays numeric) ────────────────────────
check("dec.2.pad renders 10 as 10.00 in text",
      run('x as dec.2.pad\nx 10\ngive back 200 "{{x}}"') == "10.00")
check("dec.2.pad value stays numeric for math",
      run('x as dec.2.pad\nx 10\ngive back 200 (x + 1)') == 11.0)
check("dec.2.pad in a shape field renders padded",
      run('shape M\n    amount as dec.2.pad\nshape: done\n'
          'create m as sh.M\n    amount 7\ncreate: done\ngive back 200 "{{m.amount}}"') == "7.00")

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
