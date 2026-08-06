# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""`since <anchor>` must fail loud, not crash, and SinceExpr must stay its own class.

`since last_month` used to raise an INTERNAL TypeError:
    TimeExpr.__init__() got an unexpected keyword argument 'anchor'
because mohio_ast had `SinceExpr = TimeExpr` (a half-done "merge") while the transformer's
time_anchor still built `SinceExpr(anchor=...)`, and TimeExpr has no `anchor` field.

Per the timespan spec, `since <anchor>` is a RANGE ("from a point until now"), semantically
distinct from a point-in-time TimeExpr, so SinceExpr is kept as its own class. The
consumption path (retrieve reading a since-range) is not built yet, so `since` now FAILS
LOUD with an actionable message instead of crashing internally or silently evaluating to
nothing.

These tests lock: (1) SinceExpr is not TimeExpr, (2) since fails loud cleanly, (3) the
regular time forms still work.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter
from mohio_ast import SinceExpr, TimeExpr

_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)


def val(b):
    return b.to_python() if hasattr(b, 'to_python') else b


def run_msg(src):
    """Run and return the value (or the error string) as text."""
    try:
        return str(val(MohioInterpreter().run(transform(_P.parse(src), src)).get('body')))
    except Exception as e:
        return f"{type(e).__name__}: {e}"


_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)


# 1. The classes are distinct again.
check("SinceExpr is NOT TimeExpr (the alias is gone)", SinceExpr is not TimeExpr)
check("SinceExpr has its own `anchor` field",
      'anchor' in getattr(SinceExpr, '__dataclass_fields__', {}))

# 2. since builds a SinceExpr node, not a TimeExpr.
node = transform(_P.parse('hold x (since last_month)\n'),
                 'hold x (since last_month)\n').statements[0].value
check("since builds a SinceExpr node",
      type(node).__name__ == 'SinceExpr', f"built {type(node).__name__}")
check("the SinceExpr carries its anchor",
      getattr(node, 'anchor', None) == 'last_month', f"anchor={getattr(node,'anchor',None)!r}")

# 3. since fails LOUD (no internal TypeError, no silent None).
msg = run_msg('give back 200 (since last_month)\n')
check("since fails loud, not an internal TypeError",
      'TypeError' not in msg and 'unexpected keyword' not in msg, msg)
check("the failure names `since` and points to a working alternative",
      'since' in msg.lower() and ('now()' in msg or 'not yet' in msg.lower() or 'unwired' in msg),
      msg)

# 4. the regular time forms still work (no regression from un-merging).
check("now() still works", 'T' in run_msg('give back 200 (now())\n'))
check("now() - 30 days still works", 'T' in run_msg('give back 200 (now() - 30 days)\n'))
check("today still works", run_msg('hold x today\ngive back 200 x\n').count('-') == 2)
check("uuid() still works (shared time_expr path)",
      len(run_msg('give back 200 (uuid())\n')) == 36)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
