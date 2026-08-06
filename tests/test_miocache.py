# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""miocache set/get with `as NAME` capture and `default` on miss (A3)."""
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
def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1; print(f"  [PASS] {label}")
    else:
        _failed += 1; print(f"  [FAIL] {label}: got {got!r} want {want!r}")

def shown(body):
    it = MohioInterpreter()
    it.run(transform(_P.parse(body), body))
    return it.shown[-1] if getattr(it, 'shown', None) else None

check("get after set captures value via `as`",
      shown('miocache.set "greeting" "hello"\nmiocache.get "greeting" as out\nshow out\n'),
      "hello")
check("default used on cache miss",
      shown('miocache.get "nope" default "fallback" as out\nshow out\n'),
      "fallback")
check("hit preferred over default",
      shown('miocache.set "k" "real"\nmiocache.get "k" default "fallback" as out\nshow out\n'),
      "real")

# A typo'd cache method must FAIL LOUD, not silently return None. Before the fix, the generic
# miocache handler returned None for any unrecognized method, so `miocache.gett` executed clean
# and the caller saw a phantom cache miss forever (same class as the miomail.queue silent no-op).
def failed_reason(body):
    it = MohioInterpreter()
    try:
        it.run(transform(_P.parse(body), body)); return None
    except Exception as e:
        return str(e)
_err = failed_reason('miocache.gett "k" as r\nshow "after"\n')
check("typo miocache.gett fails loud (not a silent no-op)", _err is not None, True)
check("the fail-loud names the real methods", bool(_err) and 'miocache.get' in _err and 'miocache.set' in _err, True)

print(f"\nRESULTS: {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
