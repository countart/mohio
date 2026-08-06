# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""Two fail-loud surface fixes:

1. A MohioRuntimeError raised inside a served handler (e.g. re-holding an already-held session
   value) returns a clean 500 response for that request instead of crashing the server / taking
   down the session. The fail-loud behavior is preserved; the surface is graceful.

2. A bare `show missing` on an undefined single variable fails loud, consistent with interpolation
   ({{ missing }} already fails loud). Silently showing None for a typo is the surprising behavior
   the language avoids.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter, _InMemorySessionStore

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


def run_shown(src):
    it = MohioInterpreter(); it.shown = []
    it.run(transform(P.parse(src), src))
    return it.shown


def fails(src):
    try:
        run(src); return False
    except Exception:
        return True


# ── 1. served re-hold returns a clean 500, does not crash ─────────────────────────────
H = 'shape Visit\n    method POST\nshape: done\n'
BODY = ('listen for\n    new sh.Visit\n        hold once = "a"\n        give back once\n    new: done\nlisten: done\n')
t = transform(P.parse(H + BODY), H + BODY)
sessions = _InMemorySessionStore()
r1 = MohioInterpreter().run_with_session(t, {"_method": "POST"}, "e2e", sessions)
try:
    r2 = MohioInterpreter().run_with_session(t, {"_method": "POST"}, "e2e", sessions)
    crashed = False
except Exception:
    r2 = None
    crashed = True
check("served handler: first request succeeds (200)", r1.get('status') == 200)
check("served re-hold does NOT crash the request", not crashed)
check("served re-hold returns a 500 response", r2 is not None and r2.get('status') == 500)
check("served re-hold 500 body carries the fail-loud message",
      r2 is not None and "already held" in str(r2.get('body')))

# ── 2. bare show of an undefined variable fails loud ──────────────────────────────────
check("show of a defined variable works", run_shown('hold x "ok"\nshow x\n') == ["ok"])
check("show of a literal works", run_shown('show "literal"\n') == ["literal"])
check("show of an undefined variable fails loud (not silent None)",
      fails('hold x "ok"\nshow y\n'))
check("show of an undefined variable does NOT print None",
      "None" not in [str(s) for s in (run_shown('hold x "ok"\nshow x\n'))])

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
