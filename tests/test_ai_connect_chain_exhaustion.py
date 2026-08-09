# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""ai.connect: a chain that produces no usable provider must never be silently
discarded in favor of the runtime's own default model (2026-08-04, Unit 1).

Found during the AI-failure wide-net sweep, same disease as the ai.decide fix
earlier that day, different shape. `resolve_chain()` already correctly detected
total failure (every declared provider unreachable) and returned None -- but
`_decide_impl` never checked that return value, or `chain.resolved`, before
deciding which model to call. It silently fell through to `self._model` (the
runtime's OWN default, never named in the ai.connect declaration), and if THAT
happened to work, the decision came back as an ordinary, high-confidence,
fell_back=False success with nothing in the record indicating the declared
chain was bypassed -- an unauthorized model silently answered a decision the
developer explicitly tried to route to a specific provider list (e.g. for cost,
data-residency, or compliance reasons).

Ruling: propagate a real failure (AiProviderError, same pattern as the ai.decide
fix) whenever a chain_name is named for a decision and produces no usable
provider -- never registered, never resolved (every provider's ping failed), or
exhausted mid-loop -- UNLESS the developer also gave an explicit model_override,
which is itself an explicit instruction, not a silent default.

Covers:
  - every provider's pre-loop ping fails -> raises, self._model's own _complete
    is NEVER attempted (the old bug: it silently succeeded on this call)
  - chain_name refers to a chain that was never registered at all (typo/missing
    ai.connect) -> raises, names the chain
  - a chain that resolved successfully once, then gets exhausted mid-loop (every
    provider fails on later calls) -> a subsequent decide() on the same chain
    still raises, never falls through to an unrelated default
  - a resolved chain still wins normally (regression guard, unchanged behavior)
  - chain_name absent entirely -> self._model is used as before (regression
    guard, this fix must not touch the no-chain-declared case)
  - an explicit model_override alongside an unresolved chain is honored, not
    silently discarded either (it is itself an explicit instruction)

Run: `python tests/test_ai_connect_chain_exhaustion.py`.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')

from mohio_ai import AnthropicAiRuntime, AiProviderError, ResolvedChain, CompletionResult

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail: print(f"          {detail}")
    _p += bool(cond); _f += (not cond)

def make_rt():
    rt = AnthropicAiRuntime.__new__(AnthropicAiRuntime)
    rt._overrides = {}; rt._verbose = False; rt._model = "claude-sonnet-4-6"
    rt._calls = 0; rt._call_cap = 0; rt._chains = {}
    return rt

# ── 1. Every provider's ping fails -> raises; the default model is NEVER contacted ──
rt = make_rt()
calls = []
def tracking_complete(model, system, user, temperature=None, max_tokens=None):
    calls.append((model, user))
    if user == "ping":
        raise ConnectionError(f"{model} unreachable")
    return CompletionResult(text='{"result": true, "confidence": 0.93, "explanation": "should never be reached"}')
rt._complete = tracking_complete
chain = rt.register_chain("fraud_providers", ["claude-a", "claude-b", "claude-c"])
rt.resolve_chain("fraud_providers")
check("resolve_chain still correctly reports failure (chain.resolved False)", not chain.resolved)
try:
    rt.decide(name="is_fraud", inputs={"amount": 90000}, threshold=0.85,
              return_type="boolean", chain_name="fraud_providers")
    check("decide() raises when the whole chain fails to resolve (was: silent success)", False)
except AiProviderError as e:
    check("decide() raises AiProviderError when the whole chain fails to resolve",
          "fraud_providers" in str(e), str(e))
check("the runtime's OWN default model was NEVER contacted for a real decision "
      "(only the 3 pings happened -- the old bug's silent fallback call is gone)",
      all(u == "ping" for _, u in calls), str(calls))

# ── 2. chain_name refers to a chain that was never registered -> raises, names it ──
rt2 = make_rt()
rt2._complete = lambda *a, **k: CompletionResult(text='{"result": true, "confidence": 0.9, "explanation": "x"}')
try:
    rt2.decide(name="x", inputs={}, threshold=0.85, return_type="boolean",
              chain_name="typo_chain_name")
    check("an unregistered chain_name raises (was: silently used self._model)", False)
except AiProviderError as e:
    check("an unregistered chain_name raises AiProviderError naming the chain",
          "typo_chain_name" in str(e) and "not registered" in str(e).lower(), str(e))

# ── 3. Chain resolves once, then gets exhausted mid-loop -> a LATER decide() call
#       on the same chain still raises, never falls through silently ──────────────
rt3 = make_rt()
calls3 = []
def flaky_complete(model, system, user, temperature=None, max_tokens=None):
    calls3.append((model, user))
    if user == "ping":
        return "ok"  # every provider resolves fine initially
    raise ConnectionError(f"{model} down mid-loop")  # but every real call fails
rt3._complete = flaky_complete
chain3 = rt3.register_chain("providers3", ["claude-x", "claude-y"])
rt3.resolve_chain("providers3")
check("chain resolves successfully at first (both providers ping OK)",
      chain3.resolved and chain3.active_provider == "claude-x")
# First decide() call: primary fails, retries the 2nd provider, that fails too ->
# chain is now exhausted (today's earlier fix already raises here).
try:
    rt3.decide(name="d1", inputs={}, threshold=0.85, return_type="boolean", chain_name="providers3")
    check("first call on a chain whose providers all fail mid-loop raises", False)
except AiProviderError:
    check("first call on a chain whose providers all fail mid-loop raises", True)
# Second decide() call on the SAME (now exhausted) chain: must ALSO raise, never
# silently fall through to self._model just because chain.active_provider is a
# non-None (but stale, already-failed) string.
try:
    rt3.decide(name="d2", inputs={}, threshold=0.85, return_type="boolean", chain_name="providers3")
    check("a SECOND call on an already-exhausted chain also raises (was: could silently "
          "fall through)", False)
except AiProviderError:
    check("a SECOND call on an already-exhausted chain also raises", True)
check("the default model claude-sonnet-4-6 was never contacted across either call",
      not any(m == "claude-sonnet-4-6" for m, _ in calls3), str(calls3))

# ── 4. Regression: a chain that DOES resolve still wins normally ───────────────────
rt4 = make_rt()
rt4._complete = lambda model, s, u, temperature=None, max_tokens=None: (
    "ok" if u == "ping" else CompletionResult(text='{"result": true, "confidence": 0.9, "explanation": "fine"}'))
chain4 = rt4.register_chain("good_chain", ["claude-good"])
rt4.resolve_chain("good_chain")
d4 = rt4.decide(name="x", inputs={}, threshold=0.85, return_type="boolean", chain_name="good_chain")
check("a chain that resolves successfully is used normally (regression guard, unchanged)",
      d4.model == "claude-good" and d4.fell_back is False, str(d4))

# ── 5. Regression: no chain_name at all -> self._model used exactly as before ──────
rt5 = make_rt()
rt5._complete = lambda model, s, u, temperature=None, max_tokens=None: CompletionResult(text='{"result": true, "confidence": 0.9, "explanation": "fine"}')
d5 = rt5.decide(name="x", inputs={}, threshold=0.85, return_type="boolean")
check("no chain_name declared -> self._model used, no raise (this fix must not "
      "touch the ordinary no-chain case)", d5.model == "claude-sonnet-4-6", str(d5))

# ── 6. An explicit model_override alongside an unresolved chain IS honored -- it is
#       itself an explicit instruction, not a silent default. ─────────────────────
rt6 = make_rt()
calls6 = []
def override_complete(model, system, user, temperature=None, max_tokens=None):
    calls6.append((model, user))
    if user == "ping":
        raise ConnectionError("unreachable")
    return CompletionResult(text='{"result": true, "confidence": 0.9, "explanation": "used the override"}')
rt6._complete = override_complete
rt6.register_chain("dead_chain", ["claude-dead"])
rt6.resolve_chain("dead_chain")
d6 = rt6.decide(name="x", inputs={}, threshold=0.85, return_type="boolean",
               chain_name="dead_chain", model_override="claude-explicit-override")
check("an explicit model_override is honored even when the named chain failed to "
      "resolve (an explicit instruction, not a silent default)",
      d6.model == "claude-explicit-override" and d6.fell_back is False, str(d6))

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
