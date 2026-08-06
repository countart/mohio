# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""Math operators never touch text.

`+ - * / %` used to silently coerce: "5" + 2 -> "52" (concatenation) and "5" * 2 -> "55"
(Python's str*int repetition). The interpreter comment admitted why: "prevents math_error".
That is the drift pattern -- a workaround that suppressed a fail-loud. It is the worst
failure class we ship, because every value off a request is text, so `(price + tax)` on form
fields quietly produced "1020" instead of 30.

Now: `&` joins, math is math, and a numeric-looking string must be cast explicitly.
"""
import os, sys
os.environ.setdefault('DATABASE_URL', ':memory:')
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter
import mohio_data

_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)
_p = _f = 0
def check(label, cond):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    _p += bool(cond); _f += (not cond)

def shown(src):
    it = MohioInterpreter(); it.run(transform(_P.parse(src), src))
    return it.shown[-1] if getattr(it, 'shown', None) else None

def raises(src):
    """Math on text must not silently produce a value. It raises, or it surfaces as a
    math_error response -- either way `show` never gets the concatenated/repeated string."""
    try:
        it = MohioInterpreter(); it.run(transform(_P.parse(src), src))
        out = it.shown[-1] if getattr(it, 'shown', None) else None
        return out is None          # nothing shown -> the math_error stopped it
    except Exception:
        return True

check('("5" + 2) fails loud',        raises('hold s "5"\nshow (s + 2)\n'))
check('("5" * 2) fails loud',        raises('hold s "5"\nshow (s * 2)\n'))
check('("ab" * 3) fails loud',       raises('hold s "ab"\nshow (s * 3)\n'))
check('(5 + 2) is still 7',          str(shown('hold a 5\nshow (a + 2)\n')) == '7')
check('& still joins',               shown('hold a "Hi"\nshow (a & " there")\n') == "Hi there")
check('explicit cast then math',     str(shown('hold s "5" as.int\nshow (s * 2)\n')) == '10')

# `by` is how text repeats now -- `*` is math only ("one word, one job, different context")
check('("ab" by 3) -> ababab',       shown('hold s "ab"\nshow (s by 3)\n') == "ababab")
check('by with a count of 1',        shown('hold s "hi"\nshow (s by 1)\n') == "hi")

# The CAST is the coercion point (math stays strict). Empties become 0 there, so a form
# field that arrives empty is 0 after `as.int`, not a silent "" flowing into arithmetic.
check('none as.int -> 0',       str(shown('hold s none\nhold n s as.int\nshow n\n')) == '0')
check('"" as.int -> 0',         str(shown('hold s ""\nhold n s as.int\nshow n\n')) == '0')
check('"null" as.int -> 0',     str(shown('hold s "null"\nhold n s as.int\nshow n\n')) == '0')
check('"0" as.int -> 0',        str(shown('hold s "0"\nhold n s as.int\nshow n\n')) == '0')
check('"5" as.int -> 5',        str(shown('hold s "5"\nhold n s as.int\nshow n\n')) == '5')
check('"3.14" as.number',       str(shown('hold s "3.14"\nhold n s as.number\nshow n\n')) == '3.14')
check('"car" as.int fails loud', raises('hold s "car"\nhold n s as.int\nshow n\n'))
check('cast then math: ("" as.int) + 10 -> 10',
      str(shown('hold raw ""\nhold qty raw as.int\nhold price 10\nshow (qty + price)\n')) == '10')

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
