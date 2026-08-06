# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""MioScript `send` handler binding.

Root cause of the on.failure bug: `send` had no closer, and its handler bodies
(`client_stmt*`) sat directly against the listener's own `client_stmt*` with no delimiter.
Where a handler's body ended and the listener's next statement resumed was genuinely
ambiguous, so Earley guessed -- and guessed wrong whenever a statement preceded the send,
hoisting on.failure statements out to run on every submit.

Fix: `send` is a verb block, and verb blocks close (`send: done`). The closer bounds the
handlers exactly as the main language bounds them (step_block: body* handler* closer).
Conditions (on.success/on.failure) still take NO closer -- they end at the next condition
or at the block end.
"""
import os, sys
os.environ.setdefault('DATABASE_URL', ':memory:')
from lark import Lark
from mohio_transformer_ast import transform
from mohio_mioscript import compile_listeners
import mohio_data

_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)
_p = _f = 0
def check(label, cond):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    _p += bool(cond); _f += (not cond)

def js_of(src):
    return compile_listeners(transform(_P.parse(src), src).statements) or ''

CLOSED = '''listen for submit on #f
    disable #b
    put "Running..." into #log
    send #f to "/u"
        on.success
            put result into #log
            enable #b
        on.failure
            put "broke" into #log
            enable #b
    send: done
    put "sent" into #s
listen: done
'''
js = js_of(CLOSED)
check("closed send: else branch is not empty", '} else {  }' not in js and '} else {}' not in js)
check("closed send: on.failure body stays inside the send",
      not __import__('re').search(r'\}\);\s*\n\s*_moPutHtml\("#log", "broke"\)', js))
check("closed send: both on.failure statements bind (put + enable)",
      js.count('_moEnable("#b")') >= 2)
check("closed send: sibling after `send: done` still emits", '_moPutHtml("#s", "sent")' in js)

# bare send (no handlers) still valid, no closer needed
check("bare send still parses", bool(js_of('listen for submit on #f\n    send #f to "/u"\nlisten: done\n')))

# handlers with NO closer must fail loud, never silently miscompile
UNCLOSED = CLOSED.replace('    send: done\n', '')
try:
    js_of(UNCLOSED); check("unclosed send with handlers fails loud", False)
except Exception:
    check("unclosed send with handlers fails loud", True)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
