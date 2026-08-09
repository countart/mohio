# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Two small approved fixes (2026-08-04), same-shape sibling gaps closed in the same
functions their working precedent already lived in:

1. `make` (bare form, `make thing`) is caught by scan_block_opener_as_variable
   (mohio_reachability.py) but had no entry in _OPENER_HINT, so the error never named
   `create` as the replacement -- the block form (`make X ... make: done`) already had
   a precise "'make' is retired -- use 'create'" message (mohio_transformer_ast.py's
   make_retired_block), the bare form just never got the same courtesy.

2. `_eval_condition`'s numeric-comparison branch (`>`/`above`/`<`/`below`/`>=`/`<=`)
   caught a TypeError from comparing two genuinely incomparable values (e.g. non-numeric
   text against a number) and silently returned False -- indistinguishable from the
   condition genuinely being false. The sibling "unknown operator" fallthrough four
   lines below already fails loud instead of guessing false; this now matches it.

Run: `python tests/test_opener_hint_and_comparison_guard.py`.
"""
import os
os.environ.setdefault('DATABASE_URL', ':memory:')
from lark import Lark
from pathlib import Path
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter, Context, MohioValue
from mohio_ast import Condition, DottedName

import mohio_data
_RAW = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_G = '\n'.join(l for l in _RAW.splitlines() if not l.strip().startswith('//'))
_P = Lark(_G, parser='earley', ambiguity='resolve', propagate_positions=True)

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail: print(f"          {detail}")
    _p += bool(cond); _f += (not cond)

# ── 1. bare `make` opener hint points at `create` ───────────────────────────────────
from mohio_reachability import scan_block_opener_as_variable

src = 'make thing\n'
prog = transform(_P.parse(src), src)
errors = scan_block_opener_as_variable(prog)
check("bare `make` is caught as a block-opener-as-variable error",
      len(errors) == 1, str(errors))
msg = str(errors[0].message) if errors else ""
check("the error now names `create` as the replacement (was: no hint at all)",
      'create' in msg.lower(), msg)
check("the error still names `make` and the original silent-block-vanish explanation",
      'make' in msg.lower() and 'variable' in msg.lower(), msg)

# Regression: an unrelated opener (e.g. `new`) is unaffected by this change.
src2 = 'new thing\n'
prog2 = transform(_P.parse(src2), src2)
errors2 = scan_block_opener_as_variable(prog2)
check("regression: an unrelated opener (`new`) still gets its own existing hint",
      len(errors2) == 1 and 'sh.' in str(errors2[0].message), str(errors2))

# ── 2. _eval_condition fails loud on an incomparable numeric comparison ─────────────
it = MohioInterpreter()
ctx = Context()
ctx.set('word', MohioValue('hello', 'text'))
ctx.set('n', MohioValue(5, 'number'))
ctx.set('s', MohioValue('10', 'text'))

for op in ('>', '<', '>=', '<=', 'above', 'below'):
    cond = Condition(left=DottedName(parts=['word']), op=op, right=DottedName(parts=['n']))
    try:
        it._eval_condition(cond, ctx)
        check(f"`{op}` on incomparable values raises (was: silently False)", False)
    except Exception as e:
        check(f"`{op}` on incomparable values raises, naming both sides",
              'hello' in str(e) and '5' in str(e), str(e))

# Regression: numeric-string coercion still works for every comparison operator.
for op, expect in (('>', True), ('<', False), ('>=', True), ('<=', False)):
    cond = Condition(left=DottedName(parts=['s']), op=op, right=DottedName(parts=['n']))
    result = it._eval_condition(cond, ctx)
    check(f"regression: numeric-string `{op}` comparison unaffected ('10' {op} 5 -> {expect})",
          result == expect, result)

print(f"\nRESULTS: {_p} passed, {_f} failed")
import sys
sys.exit(1 if _f else 0)
