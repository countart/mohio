# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""AI hard failure vs genuine low confidence must never share a behavior (2026-08-04).

Ruling: `ai.decide`'s "no failure ever escapes" guarantee conflated two different
situations. (1) AI never enabled (no key, dev/no-AI case) -- silent fallback to mock
is correct, untouched here. (2) AI enabled, then a real call hard-fails at runtime
(dead model, network failure, malformed response) -- this used to come back as a
fell_back=True AiDecision structurally IDENTICAL to a genuine low-confidence answer,
so both silently ran the SAME `not confident` path. That is wrong: a hard failure
means there is no model answer at all, and must surface loud (on.failure / a real
500), not be swallowed into a normal-looking `not confident` result.

Covers, per the ruling:
  - mohio_ai.py: all 3 swallow sites (`_parse_response`'s two early returns,
    `_decide_impl`'s API-call except including chain exhaustion) now RAISE
    AiProviderError instead of faking an AiDecision.
  - A genuine low-confidence answer (real parsed JSON, confidence below threshold)
    is UNCHANGED: decide() still returns normally, fell_back=True.
  - All 4 `self.ai.decide()` call sites in the interpreter (ai.decide, ai.agent,
    ai.compare, ai.respond) convert a hard failure into on.failure + a loud _Raise
    (never a crash, never a silent 200), and NEVER run `not confident` for it.
  - `mio ai-check` (mio.py cmd_ai_check) still reports the same way on the new
    exception type.

Run: `python tests/test_ai_hard_failure_loud.py`.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')

from pathlib import Path
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter
from mohio_ai import AnthropicAiRuntime, AiProviderError, _parse_response

import mohio_data
_RAW = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_G = '\n'.join(l for l in _RAW.splitlines() if not l.strip().startswith('//'))
_P = Lark(_G, parser='earley', ambiguity='resolve', propagate_positions=True)

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail: print(f"          {detail}")
    _p += bool(cond); _f += (not cond)

# ── Part A: mohio_ai.py -- the 3 swallow sites now raise, the real low-confidence
#            path is untouched ────────────────────────────────────────────────────

try:
    _parse_response("this is not json at all", "text")
    check("_parse_response raises on no-JSON-found (was: silent fell_back)", False)
except AiProviderError as e:
    check("_parse_response raises AiProviderError on no-JSON-found", "No JSON found" in str(e), str(e))

try:
    _parse_response("prefix {broken json here} suffix", "text")
    check("_parse_response raises on unparseable JSON (was: silent fell_back)", False)
except AiProviderError as e:
    check("_parse_response raises AiProviderError on unparseable JSON",
          "unparseable" in str(e).lower(), str(e))

# _decide_impl: API call itself fails (network/auth/timeout), no chain -> raises,
# does not return a fake decision.
rt = AnthropicAiRuntime.__new__(AnthropicAiRuntime)
rt._overrides = {}; rt._chains = {}; rt._verbose = False; rt._model = "claude-sonnet-4-6"
rt._calls = 0; rt._call_cap = 0
def _boom(*a, **k): raise ConnectionError("simulated network failure")
rt._complete = _boom
try:
    rt.decide(name="x", inputs={}, threshold=0.85, return_type="boolean")
    check("decide() raises on a hard API failure (no chain)", False)
except AiProviderError as e:
    check("decide() raises AiProviderError on a hard API failure (no chain)",
          "network failure" in str(e).lower() or "connectionerror" in str(e).lower(), str(e))

# _decide_impl: a chain is configured, primary AND retry both fail -> still raises
# (exhaustion), never falls through to a fake decision.
from mohio_ai import ResolvedChain
rt2 = AnthropicAiRuntime.__new__(AnthropicAiRuntime)
rt2._overrides = {}; rt2._verbose = False; rt2._model = "claude-sonnet-4-6"
rt2._calls = 0; rt2._call_cap = 0
chain = ResolvedChain("providers", ["claude-a", "claude-b"])
chain.active_provider = "claude-a"; chain._resolved = True
rt2._chains = {"providers": chain}
rt2._complete = _boom   # every provider fails
try:
    rt2.decide(name="x", inputs={}, threshold=0.85, return_type="boolean", chain_name="providers")
    check("decide() raises when a chain is EXHAUSTED (primary + retry both fail)", False)
except AiProviderError:
    check("decide() raises when a chain is EXHAUSTED (primary + retry both fail)", True)

# Regression guard: a REAL parsed response with genuinely low confidence still
# returns normally -- fell_back=True, but NOT raised. This is the one case the
# ruling says must NOT change.
rt3 = AnthropicAiRuntime.__new__(AnthropicAiRuntime)
rt3._overrides = {}; rt3._chains = {}; rt3._verbose = False; rt3._model = "claude-sonnet-4-6"
rt3._calls = 0; rt3._call_cap = 0
rt3._complete = lambda *a, **k: '{"result": true, "confidence": 0.40, "explanation": "unsure"}'
decision = rt3.decide(name="x", inputs={}, threshold=0.85, return_type="boolean")
check("a genuine low-confidence answer returns normally (unchanged, does not raise)",
      decision.fell_back is True and decision.result is True and decision.confidence == 0.40,
      str(decision))

# ── Part B: interpreter call sites convert a hard failure into on.failure / a loud
#            _Raise, and NEVER run `not confident` for it (the core of the ruling) ──

class _HardFailAi:
    """Simulates a real hard provider failure: decide() raises, exactly like the
    fixed AnthropicAiRuntime does now (was: silently returned a fell_back decision)."""
    def decide(self, **kw):
        raise AiProviderError("simulated: provider unreachable")
    def resolve_chain(self, *a, **k): return None

# `give back` inside `not confident` does NOT short-circuit the request -- it sets the
# decision's FALLBACK RESULT (bound to the ai.decide name), and execution continues to
# whatever the handler gives back next (see _exec_AiDecideBlock's own comment on this).
# So the final give-back must thread the decision's own name through to observe it.
DECIDE_NO_ONFAILURE = (
    'ai.decide risky returns text\n'
    '    confidence above 0.85\n'
    '    weigh\n        amount\n'
    '    not confident\n'
    '        give back 200 "FELLBACK-NOTCONFIDENT"\n'
    'ai.decide: done\n'
    'shape Cmd\nshape: done\n'
    'listen for\n    new sh.Cmd at /go\n'
    '        hold amount = 100\n'
    '        ai.decide risky\n'
    '        give back 200 risky\n'
    '    new: done\nlisten: done\n')
_prog_decide = transform(_P.parse(DECIDE_NO_ONFAILURE), DECIDE_NO_ONFAILURE)

resp = MohioInterpreter(ai=_HardFailAi()).run(_prog_decide, {'_method': 'POST', '_path': '/go', 'cmd': {}})
check("ai.decide: a hard failure does NOT run `not confident` (never reaches the final give-back)",
      "FELLBACK-NOTCONFIDENT" not in str(resp.get('body', '')), str(resp))
check("ai.decide: a hard failure surfaces as a real, visible failure (status 500), "
      "not a normal 200 dressed as a result",
      resp.get('status') == 500, str(resp))

DECIDE_WITH_ONFAILURE = (
    'ai.decide risky returns boolean\n'
    '    confidence above 0.85\n'
    '    weigh\n        amount\n'
    '    not confident\n'
    '        give back 200 "FELLBACK-NOTCONFIDENT"\n'
    '    on.failure\n'
    '        give back 200 "ONFAILURE-RAN"\n'
    'ai.decide: done\n'
    'shape Cmd\nshape: done\n'
    'listen for\n    new sh.Cmd at /go\n'
    '        hold amount = 100\n'
    '        ai.decide risky\n'
    '        give back 200 "MAIN-PATH-RAN"\n'
    '    new: done\nlisten: done\n')
_prog_of = transform(_P.parse(DECIDE_WITH_ONFAILURE), DECIDE_WITH_ONFAILURE)
resp2 = MohioInterpreter(ai=_HardFailAi()).run(_prog_of, {'_method': 'POST', '_path': '/go', 'cmd': {}})
check("ai.decide: with an on.failure handler present, IT runs (not not_confident)",
      "ONFAILURE-RAN" in str(resp2), str(resp2))

COMPARE_SRC = (
    'ai.compare pick\n'
    '    weigh\n        a, b\n'
    '    on.failure\n'
    '        give back 200 "COMPARE-ONFAILURE-RAN"\n'
    'ai.compare: done\n'
    'shape Cmd\nshape: done\n'
    'listen for\n    new sh.Cmd at /go\n'
    '        hold a = "x"\n        hold b = "y"\n'
    '        ai.compare pick\n'
    '        give back 200 "COMPARE-MAIN-RAN"\n'
    '    new: done\nlisten: done\n')
_prog_cmp = transform(_P.parse(COMPARE_SRC), COMPARE_SRC)
resp3 = MohioInterpreter(ai=_HardFailAi()).run(_prog_cmp, {'_method': 'POST', '_path': '/go', 'cmd': {}})
check("ai.compare: a hard failure runs on.failure, not the main path (new guard)",
      "COMPARE-ONFAILURE-RAN" in str(resp3) and "COMPARE-MAIN-RAN" not in str(resp3), str(resp3))

RESPOND_SRC = (
    'ai.respond reply\n'
    '    weigh\n        msg\n'
    '    on.failure\n'
    '        give back 200 "RESPOND-ONFAILURE-RAN"\n'
    'ai.respond: done\n'
    'shape Cmd\nshape: done\n'
    'listen for\n    new sh.Cmd at /go\n'
    '        hold msg = "hi"\n'
    '        ai.respond reply\n'
    '        give back 200 "RESPOND-MAIN-RAN"\n'
    '    new: done\nlisten: done\n')
_prog_resp = transform(_P.parse(RESPOND_SRC), RESPOND_SRC)
resp4 = MohioInterpreter(ai=_HardFailAi()).run(_prog_resp, {'_method': 'POST', '_path': '/go', 'cmd': {}})
check("ai.respond: a hard failure runs on.failure, not the main path (new guard)",
      "RESPOND-ONFAILURE-RAN" in str(resp4) and "RESPOND-MAIN-RAN" not in str(resp4), str(resp4))

# ── Regression guard: a genuine low-confidence decision (decide() returns, does not
#    raise) still runs `not confident` exactly as before -- the fix must not make
#    every ai.decide loud, only the hard-failure case.
class _GenuineLowConfidenceAi:
    def decide(self, **kw):
        import types
        return types.SimpleNamespace(result=True, confidence=0.40, fell_back=True,
                                      model='mock', explanation='real answer, just unsure')
    def resolve_chain(self, *a, **k): return None

resp5 = MohioInterpreter(ai=_GenuineLowConfidenceAi()).run(_prog_decide, {'_method': 'POST', '_path': '/go', 'cmd': {}})
check("ai.decide: a GENUINE low-confidence answer still runs `not confident` (unchanged)",
      "FELLBACK-NOTCONFIDENT" in str(resp5.get('body', '')), str(resp5))
check("ai.decide: a genuine low-confidence answer is a normal 200, not a 500",
      resp5.get('status', 200) == 200, str(resp5))

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
