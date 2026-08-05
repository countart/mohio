# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""Currency types: USD / CAD / EUR / GBP.

Each currency is a typed state built on dec.2: the stored VALUE is a number ROUNDED HALF-UP to 2
places (money standard -- currency rounds where bare dec.N truncates), and the DISPLAY renders
symbol + thousands + locale decimal when the value is joined into text. Raw numeric value stays
available for math. Declaration is empty-style: `price as USD`, then `price 19.99`.

Per-currency display:
    USD/CAD/GBP -> $1,234.56 / $1,234.56 / £1,234.56   (comma thousands, dot decimal)
    EUR         -> €1.234,56                             (dot thousands, comma decimal)
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


def run(src):
    b = MohioInterpreter().run(transform(P.parse(src), src)).get('body')
    return b.to_python() if hasattr(b, 'to_python') else b


def fails(src):
    try:
        run(src); return False
    except Exception:
        return True


# ── value: rounds HALF-UP to 2 places (money standard, not truncation) ────────────────
check("USD rounds 19.999 up to 20.0", run('p as USD\np 19.999\ngive back 200 p') == 20.0)
check("USD rounds 19.994 down to 19.99", run('p as USD\np 19.994\ngive back 200 p') == 19.99)
check("USD rounds 19.995 up to 20.0 (half-up)", run('p as USD\np 19.995\ngive back 200 p') == 20.0)
check("USD leaves 19.99 as 19.99", run('p as USD\np 19.99\ngive back 200 p') == 19.99)

# ── contract: non-numbers fail loud; empty seeds 0 ────────────────────────────────────
check("USD rejects text", fails('p as USD\np "cat"\ngive back 200 p'))
check("USD read-before-assign is 0.0", run('p as USD\ngive back 200 p') == 0.0)

# ── raw value stays numeric for math ──────────────────────────────────────────────────
check("USD value is numeric for math (price + 1)", run('p as USD\np 10\ngive back 200 (p + 1)') == 11.0)
check("money math then assign: total (price + tax) rounds",
      run('price as USD\nprice 10.005\ntax as USD\ntax 2\ntotal as USD\ntotal (price + tax)\ngive back 200 total') == 12.01)

# ── display: formatted when joined into text ──────────────────────────────────────────
check("USD displays $1,234.50", run('p as USD\np 1234.5\ngive back 200 ("" & p)') == "$1,234.50")
check("USD displays large $1,000,000.00",
      run('p as USD\np 1000000\ngive back 200 ("" & p)') == "$1,000,000.00")
check("GBP displays £99.00", run('p as GBP\np 99\ngive back 200 ("" & p)') == "£99.00")
check("CAD displays $50.25", run('p as CAD\np 50.25\ngive back 200 ("" & p)') == "$50.25")
check("EUR displays €1.234,50 (comma decimal, dot thousands)",
      run('p as EUR\np 1234.5\ngive back 200 ("" & p)') == "€1.234,50")
check("USD negative displays -$5.00", run('p as USD\np -5\ngive back 200 ("" & p)') == "-$5.00")

# ── state operators work on currency (it is a typed state) ────────────────────────────
check("release drops the currency contract",
      run('p as USD\np 5\nrelease p\np "x"\ngive back 200 p') == "x")
check("forget removes a currency variable", run('p as USD\np 5\nforget p\ngive back 200 p') is None)

# ── shape field currency (asset MVP) ──────────────────────────────────────────────────
S = 'shape Asset\n    name as text\n    price as USD\nshape: done\n'
check("shape USD field rounds on create",
      run(S + 'create a as sh.Asset\n    name "House"\n    price 1999.999\ncreate: done\ngive back 200 a.price') == 2000.0)
check("shape USD field displays formatted",
      run(S + 'create a as sh.Asset\n    name "H"\n    price 1999.999\ncreate: done\ngive back 200 ("" & a.price)') == "$2,000.00")
check("shape USD field rejects text",
      fails(S + 'create a as sh.Asset\n    price "cat"\ncreate: done\ngive back 200 a.price'))

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
