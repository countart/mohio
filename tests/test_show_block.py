#!/usr/bin/env python3
# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
Lock tests for the `show` HTML-block render form.

    show
        <h1>Welcome {{ member.name }}</h1>
    show: done

Models the sql_block "forgiveness" principle (raw HTML inside, parser does not
restrict syntax). Disambiguates from `show <value>` structurally -- raw HTML is
not a value_expr, so only the block form matches it (no newline gate needed,
which matters because the grammar %ignores newlines).

Covers:
  1. The block parses to a real ShowBlock node (not a raw Tree).
  2. {{ }} interpolates from context (dotted names resolve).
  3. `show "value"` single form still works (no regression).
  4. The block does not swallow the following statement.
  5. Raw HTML is preserved verbatim (tags untouched).
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
from mohio_interpreter import MohioInterpreter, Context, MohioValue
from mohio_ast import ShowBlock

_raw = mohio_data.GRAMMAR_PATH.read_text()
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

PASS = FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  PASS  {name}")
    else:    FAIL += 1; print(f"  FAIL  {name}")

BLOCK = ('show\n'
         '    <h1>Welcome {{ member.name }}</h1>\n'
         '    <p>You have {{ stats.count }} messages</p>\n'
         'show: done\n')

prog = transform(P.parse(BLOCK), BLOCK)
node = prog.statements[0]
check("show block builds a real ShowBlock (not a raw Tree)", type(node).__name__ == 'ShowBlock')

ctx = Context()
ctx.set('member', MohioValue({'name': 'Ronnie'}, 'shape'))
ctx.set('stats', MohioValue({'count': 3}, 'shape'))
it = MohioInterpreter(); it.shown = []
it._exec(node, ctx)
out = it.shown[0] if it.shown else ""
check("{{ member.name }} interpolates", 'Welcome Ronnie' in out)
check("{{ stats.count }} interpolates", 'You have 3 messages' in out)
check("raw HTML tags preserved", '<h1>' in out and '</p>' in out)

# show value still works
it.shown = []
v = transform(P.parse('show "Hi {{ member.name }}"\n'), 'x').statements[0]
it._exec(v, ctx)
check("show <value> single form still works", it.shown == ['Hi Ronnie'])

# block does not swallow the following statement
multi = transform(P.parse('show\n    <h1>Hi</h1>\nshow: done\nhold x = 5\n'), 'x')
kinds = [type(s).__name__ for s in multi.statements]
check("block does not swallow the next statement", 'ShowBlock' in kinds and len(multi.statements) >= 2)

print(f"\nRESULTS: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
