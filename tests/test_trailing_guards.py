# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Trailing `if` (positive) and `unless` (negative) — a single constraint on one statement.

    this runs IF     the condition is true
    this runs UNLESS the condition is true

`unless` already existed. `if` did NOT: it parsed into junk assignments and SILENTLY DROPPED
the condition, so `show "big" if x is more than 3` printed "big" even when x was 1. Now it is
a real guard, mirroring UnlessGuard.
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
    return list(getattr(it, 'shown', []) or [])

check('if TRUE  -> statement runs',
      shown('x 5\nshow "big" if x is more than 3\nshow "end"\n') == ['big', 'end'])
check('if FALSE -> statement is skipped (was silently ignored)',
      shown('x 1\nshow "big" if x is more than 3\nshow "end"\n') == ['end'])
check('unless TRUE  -> statement is skipped',
      shown('x 5\nshow "small" unless x is more than 3\nshow "end"\n') == ['end'])
check('unless FALSE -> statement runs',
      shown('x 1\nshow "small" unless x is more than 3\nshow "end"\n') == ['small', 'end'])
check('if with a compound condition',
      shown('x 5\ny 2\nshow "yes" if x is more than 3 and y is less than 3\n') == ['yes'])

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
