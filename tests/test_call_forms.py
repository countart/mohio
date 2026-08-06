# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""All five `call` forms.

Two root causes fixed here:

1. Bare `call greet` was rejected. Root cause: `statement: | closer` makes a lone closer a
   valid statement (deliberate -- it powers the "unmatched closer" error). So `call greet` +
   `call: done` had TWO legal parses: call_block-consuming-its-closer, or bare-call + a stray
   closer statement. Both grammatical, so Earley picked arbitrarily. Fixed by PRIORITY:
   call_block.3 (consumes the closer) outranks call_procedure.1 (bare).

2. `total = call add with 2` hit "mioconnect: no connector named 'add'". Root cause: `call`
   was a statement only, never a VALUE. On an assignment RHS it degraded to a bare variable
   named 'call', and the leftover `add with 2` matched mioconnect's `NAME with payload` form.
   Fixed by making `call` a first-class value expression.
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

DOUBLE = 'task double\n    take n as int\n    returns int\n    give back (n * 2)\ntask: done\n'
GREET  = 'task greet\n    show "Hello"\ntask: done\n'
PARAM  = 'task greet\n    take who as text\n    show ("Hi " & who)\ntask: done\n'

check('value form: total = call double with 5',
      str(shown(DOUBLE + 'total = call double with 5\nshow total\n')) == '10')
check('alias form: call double with 5 as r',
      str(shown(DOUBLE + 'call double with 5 as r\nshow r\n')) == '10')
check('procedure, bare: call greet',        shown(GREET + 'call greet\n') == 'Hello')
check('procedure with closer: call: done',  shown(GREET + 'call greet\ncall: done\n') == 'Hello')
check('block args: call greet / who "Aria"',
      shown(PARAM + 'call greet\n    who "Aria"\ncall: done\n') == 'Hi Aria')

# a procedure has no value to give -- asking for one is loud, never a silent None
try:
    shown('task p\n    show "x"\ntask: done\ntotal = call p\nshow total\n')
    check('procedure used as a value fails loud', False)
except Exception:
    check('procedure used as a value fails loud', True)

# ── Unit-2 arg-binding fail-louds: each replaces a verified silent no-op ──
def fails_loud(label, src, phrase):
    try:
        shown(src)
        check(label + " (expected an error, got none)", False)
    except Exception as e:
        check(label, phrase in str(e))

REQ = 'task t\n    take a as int\n    returns int\n    give back a\ntask: done\n'
fails_loud('(a) missing required arg fails loud',
           REQ + 'call t\ncall: done\n', "requires 'a'")
fails_loud('(b) typed mismatch "cat" -> int fails loud',
           REQ + 'call t with "cat"\n', "expected a number")
fails_loud('(c) unknown arg name fails loud',
           REQ + 'call t\n    a 7\n    z 99\ncall: done\n', "has no parameter 'z'")
fails_loud('(d) extra arg to a 0-param task fails loud',
           'task t\n    give back "hi"\ntask: done\ncall t with 7\n', "takes no arguments")

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
