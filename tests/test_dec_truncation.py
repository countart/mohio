# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""`as.dec.N` CAPS by TRUNCATION -- it does not round.

Precision limiting (`dec.N`) and rounding (`round.up` / `round.down` / `round.to N`) are separate
jobs. `dec.N` truncates to at most N places, deterministically -- 10.45676 as.dec.2 is 10.45, never
10.46. Rounding is only ever done when explicitly asked. This locks that distinction and guards
against a regression back to `round(float(num), places)`, which rounded (and rounded
inconsistently, because it rounded a float).

Truncation is also the safe foundation for currency types (which declare their OWN rounding
policy on top of dec precision).
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
def check(label, got, want):
    global _p, _f
    ok = str(got) == str(want)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: got {got}, want {want}")
    _p += ok; _f += (not ok)


def run(expr):
    src = f'give back 200 {expr}\n'
    b = MohioInterpreter().run(transform(P.parse(src), src)).get('body')
    return b.to_python() if hasattr(b, 'to_python') else b


# ── truncation, not rounding ──────────────────────────────────────────────────────────
check("10.45676 as.dec.2 truncates to 10.45 (not 10.46)", run('((10.45676) as.dec.2)'), 10.45)
check("10.455 as.dec.2 caps to 10.45 (not round-up 10.46)", run('((10.455) as.dec.2)'), 10.45)
check("10.999 as.dec.2 truncates to 10.99 (not 11.00)", run('((10.999) as.dec.2)'), 10.99)
check("2/3 as.dec.2 truncates to 0.66 (not rounded 0.67)", run('((2/3) as.dec.2)'), 0.66)
check("1/3 as.dec.2 truncates to 0.33", run('((1/3) as.dec.2)'), 0.33)

# ── fewer places than the cap: unchanged, not padded ──────────────────────────────────
check("10.456 as.dec.5 unchanged (fewer places than cap)", run('((10.456) as.dec.5)'), 10.456)
check("10 as.dec.2 stays 10.0 (truncation never pads)", run('(10 as.dec.2)'), 10.0)

# ── deterministic across values that trip float rounding ──────────────────────────────
check("0.1 + 0.2 then as.dec.2 truncates cleanly", run('((0.1 + 0.2) as.dec.2)'), 0.3)
check("2.675 as.dec.2 truncates to 2.67 (classic float-round trap)", run('((2.675) as.dec.2)'), 2.67)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
