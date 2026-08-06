# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""validate using RULE resolves in-scope variables when there's no request (A4),
and still fails loud on a genuinely missing field (never silently passes)."""
import os, sys
os.environ.setdefault('DATABASE_URL', ':memory:')
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter

import mohio_data
_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

_passed = _failed = 0
def check(label, cond):
    global _passed, _failed
    if cond: _passed += 1; print(f"  [PASS] {label}")
    else:    _failed += 1; print(f"  [FAIL] {label}")

RULE = 'miovalidate EmailRule\n    check email as text\nmiovalidate: done\n'

def reached(body):
    """True if execution passed the validate (a `show "REACHED"` after it ran)."""
    it = MohioInterpreter()
    try:
        it.run(transform(_P.parse(body), body))
    except Exception:
        pass
    return 'REACHED' in (getattr(it, 'shown', []) or [])

# in-scope held variable validates and execution continues
check("in-scope email passes -> reaches past validate",
      reached(RULE + 'hold email "amy@example.com"\nvalidate using EmailRule\nshow "REACHED"\n'))

# genuinely missing field halts (no silent pass) -> does NOT reach
check("missing field halts -> does not reach past validate",
      not reached(RULE + 'validate using EmailRule\nshow "REACHED"\n'))

# §6: served handler validates a held in-scope variable (not just the request)
import os as _os
_os.environ.setdefault('DATABASE_URL', ':memory:')
try:
    from mohio_server import MohioServer, create_app
    from starlette.testclient import TestClient
    _src = ("shape Q\n    email as text\nshape: done\n"
            "miovalidate R\n    check email as text\nmiovalidate: done\n"
            "listen for\n    request for sh.Q at /q\n"
            "        hold email \"a@b.com\"\n        validate using R\n"
            "        give back ok \"validated\"\n    request: done\nlisten: done\n")
    _c = TestClient(create_app(MohioServer(transform(_P.parse(_src), _src), MohioInterpreter())),
                    raise_server_exceptions=False)
    _r = _c.get('/q')
    _ok = _r.status_code == 200 and 'validated' in _r.text
    print(("  [PASS]" if _ok else "  [FAIL]") + " served handler validates a held in-scope var")
    globals()['_passed'] = _passed + (1 if _ok else 0)
    globals()['_failed'] = _failed + (0 if _ok else 1)
except Exception as _e:
    print(f"  [FAIL] served held-var validate (exception) {_e}")
    globals()['_failed'] = _failed + 1

print(f"\nRESULTS: {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)