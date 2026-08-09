# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
test_airuntime_parity.py — Mock and real AiRuntime must never drift.

Ronnie's rule: when AiRuntime.decide() (or any method with a real + mock
implementation) gains a parameter, BOTH implementations update in the same commit.
This test fails loud the moment they drift — it's what would have caught the
"read leaflet" 500 (MockAiRuntime.decide() missing persona/model_override).

It does two things:
  1. Compares the decide() signatures of MockAiRuntime and AnthropicAiRuntime —
     the Mock must accept every parameter the real one accepts (no missing kwarg).
  2. Actually CALLS Mock.decide() with the FULL parameter set the interpreter's
     call site passes — so a missing/renamed param is a hard failure, not a silent gap.
     (The real runtime can't be called here — it needs an API key and would bill —
     so its contract is checked by signature; the Mock is exercised live.)

Run: python3 tests/test_airuntime_parity.py   (from the compiler root)
"""
import os, sys, inspect
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from mohio_interpreter import MockAiRuntime
from mohio_ai import AnthropicAiRuntime

passed = failed = 0
def check(name, ok, detail=""):
    global passed, failed
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"   {detail}"))
    passed += bool(ok); failed += (not ok)

def params_of(cls, method):
    return set(inspect.signature(getattr(cls, method)).parameters) - {'self'}

# The exact kwargs the interpreter's _exec ai.decide call site can pass.
# Keep this in sync with mohio_interpreter.py decide_kwargs assembly.
CALL_SITE_KWARGS = {
    'name', 'inputs', 'threshold', 'return_type', 'chain_name',
    'system_prompt', 'persona', 'context', 'temperature', 'model_override',
}

print("\n=== decide() signature parity (Mock vs real) ===")
mock_p = params_of(MockAiRuntime, 'decide')
real_p = params_of(AnthropicAiRuntime, 'decide')

missing_from_mock = sorted(real_p - mock_p)
check("Mock.decide accepts every real-runtime parameter (no drift)",
      not missing_from_mock,
      detail=f"Mock is MISSING: {missing_from_mock} — add them to MockAiRuntime.decide()")

missing_call_site = sorted(CALL_SITE_KWARGS - mock_p)
check("Mock.decide accepts every kwarg the interpreter call site passes",
      not missing_call_site,
      detail=f"Mock cannot receive: {missing_call_site}")

missing_call_site_real = sorted(CALL_SITE_KWARGS - real_p)
check("Real decide accepts every kwarg the interpreter call site passes",
      not missing_call_site_real,
      detail=f"Real runtime cannot receive: {missing_call_site_real}")

print("\n=== Mock.decide LIVE call with the full call-site parameter set ===")
try:
    MockAiRuntime().decide(
        name="parity_probe", inputs={"x": 1}, threshold=0.5,
        return_type="text", chain_name=None, system_prompt="sys",
        persona="a dry narrator", context="room: test",
        temperature=0.2, model_override="claude-sonnet-4-6",
    )
    check("Mock.decide(**full_call_site_kwargs) does not raise", True)
except TypeError as e:
    check("Mock.decide(**full_call_site_kwargs) does not raise", False, detail=str(e))

# If Mock ever implements generate_text, hold it to the same parity rule.
print("\n=== generate_text parity (only if Mock implements it) ===")
if hasattr(MockAiRuntime, 'generate_text') and hasattr(AnthropicAiRuntime, 'generate_text'):
    m = params_of(MockAiRuntime, 'generate_text')
    r = params_of(AnthropicAiRuntime, 'generate_text')
    missing = sorted(r - m)
    check("Mock.generate_text accepts every real parameter", not missing,
          detail=f"Mock missing: {missing}")
else:
    check("Mock does not implement generate_text (hasattr-guarded at call site) — OK", True)

print(f"\nRESULTS: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
