# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
test_ai_rank.py — ai.rank + natural comparison operators

Written to spec (2026-06-28). Failing test = compiler bug report.

Run: PYTHONPATH=$PWD DATABASE_URL=:memory: python3 tests/test_ai_rank.py
"""
import os, sys, re
sys.argv = ['mio.py']
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('DATABASE_URL', ':memory:')

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter, AiDecision
from mohio_server import MohioServer, create_app
from starlette.testclient import TestClient

_passed = _failed = 0
def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1; print(f"  ok   {label}")
    else:
        _failed += 1; print(f"  FAIL {label}: got {got!r} want {want!r}")

def check_true(label, val):
    global _passed, _failed
    if val:
        _passed += 1; print(f"  ok   {label}")
    else:
        _failed += 1; print(f"  FAIL {label}: expected truthy, got {val!r}")

_raw = open(os.path.join(ROOT, 'mohio.lark'), encoding='utf-8').read()
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

class MockAI:
    def register_chain(self, *a, **k): pass
    def decide(self, name='', inputs=None, **k):
        return AiDecision(result=None, confidence=0.9, fell_back=False,
                          model='mock', inputs=inputs or {})

def unwrap(r):
    try:
        data = r.json()
    except Exception:
        return r.text.strip()
    if isinstance(data, list): return data
    msg = data.get("message", data.get("body", ""))
    if isinstance(msg, str):
        m = re.match(r"MohioValue\('(.+?)',", msg)
        if m: return m.group(1)
    return msg

def run_top(rank_src, result_name="result"):
    """Top-level ai.rank → give back the bound name via /q."""
    full = f'connect db as sqlite from env.DATABASE_URL\n\n{rank_src}\n\nlisten for\n    request for sh.Q at /q\n        give back ok {result_name}\n    request: done\nlisten: done\n'
    try:
        prog = transform(_P.parse(full), full)
    except Exception as e:
        return -1, f"PARSE: {str(e).splitlines()[0][:80]}"
    try:
        interp = MohioInterpreter(ai=MockAI())
        server = MohioServer(prog, interp)
        app = create_app(server)
        c = TestClient(app, raise_server_exceptions=False)
        r = c.get("/q")
        return r.status_code, unwrap(r)
    except Exception as e:
        return -2, f"RUNTIME: {str(e).splitlines()[0][:80]}"


# ══════════════════════════════════════════════════════════════════════════════
# PART 1 — ai.rank (simple cases that work today)
# ══════════════════════════════════════════════════════════════════════════════
print("\n── ai.rank: simple cases ──")

status, val = run_top('''\
ai.rank result returns text
    option "a"
    option "b" weight 2
ai.rank: done''')
check("bare vs weighted → b", str(val), "b")

status, val = run_top('''\
ai.rank result returns text
    option "x"
ai.rank: done''')
check("for omitted", str(val), "x")

status, val = run_top('''\
ai.rank result returns text
    confidence above 0.5
    option "only-one"
ai.rank: done''')
check("single option conf 1.0", str(val), "only-one")

status, val = run_top('''\
ai.rank result returns text
    option "only"
    ai.audit to rank_log
ai.rank: done''')
check("ai.audit in rank", str(val), "only")

status, val = run_top('''\
ai.rank result
    option "solo"
ai.rank: done''')
check("returns omitted", str(val), "solo")


# ══════════════════════════════════════════════════════════════════════════════
# PART 1 — ai.rank (conditional cases — spec, may be RED)
# ══════════════════════════════════════════════════════════════════════════════
print("\n── ai.rank: conditional (written to spec) ──")

# ARR 50000 → growth (only growth condition holds)
status, val = run_top('''\
hold arr = 50000
ai.rank result returns text for user
    confidence above 0.7
    option "enterprise" if arr is more than 100000 weight 0.6
    option "growth" if arr is more than 10000 weight 0.3
    default "starter" weight 0.1
    not confident
        give back "needs-review"
ai.rank: done''')
check("ARR 50000 → growth", str(val), "growth")

# ARR 5000 → starter (default)
status, val = run_top('''\
hold arr = 5000
ai.rank result returns text for user
    confidence above 0.7
    option "enterprise" if arr is more than 100000 weight 0.6
    option "growth" if arr is more than 10000 weight 0.3
    default "starter" weight 0.1
    not confident
        give back "needs-review"
ai.rank: done''')
check("ARR 5000 → starter", str(val), "starter")

# ARR 150000 → needs-review (conf 0.667 < 0.7)
status, val = run_top('''\
hold arr = 150000
ai.rank result returns text for user
    confidence above 0.7
    option "enterprise" if arr is more than 100000 weight 0.6
    option "growth" if arr is more than 10000 weight 0.3
    default "starter" weight 0.1
    not confident
        give back "needs-review"
ai.rank: done''')
check("ARR 150000 → needs-review", str(val), "needs-review")

# Default fallback
status, val = run_top('''\
hold x = 1
ai.rank result returns text
    option "big" if x is more than 1000
    default "fallback"
ai.rank: done''')
check("default when no condition holds", str(val), "fallback")

# No floor → not confident never fires
status, val = run_top('''\
hold x = 500
ai.rank result returns text
    option "a" if x is more than 100 weight 0.5
    option "b" if x is more than 10 weight 0.3
    not confident
        give back "should-not-fire"
ai.rank: done''')
check("no floor → a wins", str(val), "a")

# Comparison conditions → AiRankBlock (regression pin)
status, val = run_top('''\
hold score = 75
ai.rank result returns text
    option "A" if score is more than 90 weight 0.5
    option "B" if score is more than 70 weight 0.3
    option "C" if score is more than 50 weight 0.1
    default "F"
ai.rank: done''')
check("score=75 → B (regression pin)", str(val), "B")


# ══════════════════════════════════════════════════════════════════════════════
# PART 2 — Natural comparison operators in check/when
# (top-level set → check → give back via handler)
# ══════════════════════════════════════════════════════════════════════════════
print("\n── comparisons in check/when ──")

for form, x_val, threshold, expected in [
    ("is above", 50, 10, "yes"),
    ("is below", 3, 10, "yes"),
    ("above", 50, 10, "yes"),
    ("below", 3, 10, "yes"),
    ("is more than", 50, 10, "yes"),
    ("is less than", 3, 10, "yes"),
]:
    src = f'''\
hold x = {x_val}

listen for
    request for sh.Q at /q
        check x
            {form} {threshold}
                answer = "yes"
            otherwise
                answer = "no"
        check: done
        give back ok answer
    request: done
listen: done
'''
    full = 'connect db as sqlite from env.DATABASE_URL\n\n' + src
    try:
        prog = transform(_P.parse(full), full)
        interp = MohioInterpreter(ai=MockAI())
        server = MohioServer(prog, interp)
        app = create_app(server)
        c = TestClient(app, raise_server_exceptions=False)
        r = c.get("/q")
        check(f"{form} {threshold} (x={x_val})", unwrap(r), expected)
    except Exception as e:
        check(f"{form} {threshold} (x={x_val})", f"ERROR: {e}", expected)

# Boundary: strict greater-than
src = '''\
hold x = 100

listen for
    request for sh.Q at /q
        check x
            is more than 100
                answer = "above"
            otherwise
                answer = "not above"
        check: done
        give back ok answer
    request: done
listen: done
'''
full = 'connect db as sqlite from env.DATABASE_URL\n\n' + src
prog = transform(_P.parse(full), full)
interp = MohioInterpreter(ai=MockAI())
server = MohioServer(prog, interp)
app = create_app(server)
c = TestClient(app, raise_server_exceptions=False)
r = c.get("/q")
check("is more than 100 (x=100) strict", unwrap(r), "not above")


# ══════════════════════════════════════════════════════════════════════════════
# PART 3 — Langmap (SKIP if maps/ not available)
# ══════════════════════════════════════════════════════════════════════════════
print("\n── langmap ──")
maps_dir = os.path.join(ROOT, 'maps')
if not os.path.isdir(maps_dir):
    print("  SKIP maps/ not found")
else:
    try:
        from mohio_langmap import preprocess_source
        print("  langmap available — would test round-trips here")
    except ImportError:
        print("  SKIP mohio_langmap not available")


# ══════════════════════════════════════════════════════════════════════════════
print(f"\n{'=' * 60}")
print(f"  {_passed} passed, {_failed} failed")
if _failed:
    print(f"  *** {_failed} FAILURE(S) ***")
print(f"{'=' * 60}")
sys.exit(1 if _failed else 0)
