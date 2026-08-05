# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""DotStateCheck short form (OQ-003): `when order.shipped` sugar for `when order.shipped is true`.

People prompt in near-English (`when mfa.verified is true`) and shorten as they adopt
(`when mfa.verified`). Both must mean the same thing: fire the branch when the dotted boolean state
flag is true. The verbose form already worked via cond_is; the short form was the sugar to wire.

The bug (verified real path): `when mfa.verified` in a check-with-subject block was comparing the
SUBJECT (mfa, a shape) against the resolved value (True) for equality -- never matching. Now a bare
dotted-name that resolves to a boolean is a truthiness check on that flag.

Guard: short form fires on true / falls through on false; verbose form still works; and ordinary
`when <value>` equality (Zork session patterns) is NOT disturbed.
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


def val(b):
    return b.to_python() if hasattr(b, 'to_python') else b


def run(src):
    return val(MohioInterpreter().run(transform(P.parse(src), src)).get('body'))


def short(flag, v):
    return run(f'''create obj
    {flag} {str(v).lower()}
create: done
check obj
    when obj.{flag}
        give back 200 "fired"
    otherwise
        give back 200 "fell_through"
check: done
''')


# short form fires on true, falls through on false
check("short form `when obj.verified` fires when true", short('verified', True) == 'fired',
      short('verified', True))
check("short form falls through when false", short('verified', False) == 'fell_through',
      short('verified', False))
# a different flag name, to be sure it isn't hardcoded
check("short form works for `when obj.shipped`", short('shipped', True) == 'fired')


def verbose(v):
    return run(f'''create obj
    verified {str(v).lower()}
create: done
check obj
    when obj.verified is true
        give back 200 "fired"
    otherwise
        give back 200 "fell_through"
check: done
''')

check("verbose form `is true` still fires when true", verbose(True) == 'fired')
check("verbose form still falls through when false", verbose(False) == 'fell_through')


# ordinary `when <value>` equality must be UNDISTURBED: a non-boolean dotted value still compares
def eq_case(status_value, when_value):
    return run(f'''create order
    status "{status_value}"
create: done
check order.status
    when "{when_value}"
        give back 200 "matched"
    otherwise
        give back 200 "no_match"
check: done
''')

check("ordinary value equality still matches when equal", eq_case('shipped', 'shipped') == 'matched',
      eq_case('shipped', 'shipped'))
check("ordinary value equality still fails when unequal",
      eq_case('pending', 'shipped') == 'no_match', eq_case('pending', 'shipped'))

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
