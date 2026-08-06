# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""Currency renders formatted across every display path, not just `&` concat.

A currency-tagged value formats as money whenever it is turned into display text: string concat
(`&`), output interpolation (`{{ }}`), and the show/display path. Its raw numeric value stays a
number for math and for a bare JSON give-back (currency is a number at the data layer, formatted at
the presentation layer). One helper, `_display_text` / `_display_value`, decides formatting so all
paths stay consistent.
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


check("concat formats USD", run('p as USD\np 1234.5\ngive back 200 ("" & p)') == "$1,234.50")
check("interpolation formats USD",
      run('p as USD\np 1234.5\ngive back 200 "Total {{p}}"') == "Total $1,234.50")
check("interpolation formats EUR (comma decimal)",
      run('p as EUR\np 1234.5\ngive back 200 "Cost {{p}}"') == "Cost €1.234,50")
check("interpolation mid-sentence",
      run('p as GBP\np 99\ngive back 200 "Price {{p}} each"') == "Price £99.00 each")
check("shape currency field formats in concat",
      run('shape A\n  price as USD\nshape: done\ncreate a as sh.A\n  price 99.999\ncreate: done\n'
          'give back 200 ("" & a.price)') == "$100.00")
check("shape currency field formats in interpolation",
      run('shape A\n  price as USD\nshape: done\ncreate a as sh.A\n  price 2500\ncreate: done\n'
          'give back 200 "MSRP {{a.price}}"') == "MSRP $2,500.00")
# raw value stays numeric (data layer) — bare give-back is the number, math works
check("bare give-back of a currency is the raw number (data layer)",
      run('p as USD\np 1234.5\ngive back 200 p') == 1234.5)
check("currency math uses the raw number", run('p as USD\np 10\ngive back 200 (p + 5)') == 15.0)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
