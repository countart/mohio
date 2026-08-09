# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Cross-currency math guard + dec.N.pad zero-padded display.

- Two different currencies cannot be combined in one operation (USD + EUR has no fixed rate) --
  fails loud. Same-currency math is fine and keeps the currency; currency + plain number is fine.
- dec.N.pad renders a number zero-filled to exactly N places in text (10 -> 10.00), while the
  stored value stays numeric for math.
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


def errors(src):
    """True if the program raises OR returns an error body (a _Raise surfaces as a 500 body)."""
    try:
        r = MohioInterpreter().run(transform(P.parse(src), src))
    except Exception:
        return True
    if r.get('status') == 500:
        return True
    b = r.get('body')
    b = b.to_python() if hasattr(b, 'to_python') else b
    return isinstance(b, str) and ('mismatch' in b or 'currencies' in b)


def fails(src):
    try:
        run(src); return False
    except Exception:
        return True


# ── cross-currency guard ──────────────────────────────────────────────────────────────
check("USD + EUR fails loud", errors('a as USD\na 10\nb as EUR\nb 5\ngive back 200 (a + b)'))
check("USD - GBP fails loud", errors('a as USD\na 10\nb as GBP\nb 5\ngive back 200 (a - b)'))
check("USD + USD is allowed", run('a as USD\na 10\nb as USD\nb 5\ngive back 200 (a + b)') == 15.0)
check("USD + plain number is allowed", run('a as USD\na 10\ngive back 200 (a + 5)') == 15.0)
check("same-currency sum keeps formatting",
      run('a as USD\na 10\nb as USD\nb 5\nt as USD\nt (a + b)\ngive back 200 ("" & t)') == "$15.00")

# ── dec.N.pad display ─────────────────────────────────────────────────────────────────
check("dec.2.pad renders 10 -> 10.00 (interp)",
      run('x as dec.2.pad\nx 10\ngive back 200 "{{x}}"') == "10.00")
check("dec.2.pad renders in concat",
      run('x as dec.2.pad\nx 3.5\ngive back 200 ("" & x)') == "3.50")
check("dec.3.pad renders 1.5 -> 1.500", run('x as dec.3.pad\nx 1.5\ngive back 200 "{{x}}"') == "1.500")
check("dec.2.pad truncates then pads (5.999 -> 5.99)",
      run('x as dec.2.pad\nx 5.999\ngive back 200 "{{x}}"') == "5.99")
check("dec.2.pad value stays numeric for math",
      run('x as dec.2.pad\nx 10\ngive back 200 (x + 1)') == 11.0)
check("dec.2.pad in a shape field renders padded",
      run('shape A\n  q as dec.2.pad\nshape: done\ncreate a as sh.A\n  q 7\ncreate: done\n'
          'give back 200 "{{a.q}}"') == "7.00")

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
