# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""ai.agent: on.failure and not confident actually run, and a hard provider
failure never masquerades as a real completion (2026-08-04, Unit 2).

Found during the AI-failure wide-net sweep, two compounding bugs in the SAME
block type:

  1. WIRING: AiAgentBlock has no `handlers` field at all. The executor read
     `getattr(node, "handlers", None) or []` -- always an empty list -- so
     neither `on.failure` nor `not confident` EVER fired for any agent
     failure (a provider error, OR a boundary-gate breach: max steps, token
     ceiling, cost ceiling, timeout -- all of these converge on the same
     agent_error path). The transformer also never unwrapped an OnFailure
     node from its ai_agent_body wrapper the way it already did for
     NotConfidentBlock, so even a body-scan would have found a raw Tree, not
     an OnFailure instance. Both are fixed: the transformer now unwraps
     OnFailure into node.body exactly like NotConfidentBlock already was, and
     the executor scans node.body for both, mirroring _exec_AiDecideBlock's
     own pattern precisely, rather than reading a field that never existed.

  2. agent_turn(): had its own separate swallow logic (distinct from
     decide()'s, fixed earlier the same day) -- a provider failure returned
     AgentTurn(kind='text', text=f"[provider error: {e}]") instead of
     raising. The loop's `if turn.kind == 'text': result = turn.text; break`
     treated that identically to a genuine completion -- the failure text got
     bound as the agent's real answer, and (because of bug 1) never even
     reached the failure-handling path at all. Now raises AiProviderError,
     same as decide().

Covers, per the ruling:
  - on.failure fires for a hard provider failure on the no-tools (decide) path
  - not confident fires as the fallback when on.failure is absent
  - on.failure fires for the tool-enabled (agent_turn) path -- the bug this
    unit specifically targets: no fake "[provider error: ...]" success
  - a boundary-gate breach (max steps) ALSO reaches on.failure, not just a
    provider error -- both failure sources converge on the same handling
  - neither handler declared -> correctly hard-fails loud (matches ai.create's
    established pattern), never silently resolves to None
  - a genuinely successful agent run is unaffected (regression guard)

Run: `python tests/test_ai_agent_failure_handling.py`.
"""
import os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')

from pathlib import Path
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter

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

def run(src, ai):
    prog = transform(_P.parse(src), src)
    it = MohioInterpreter(ai=ai)
    it.run_declarations(prog)
    it.shown = []
    return it.run(prog), it.shown

class _HardFailAi:
    """No tools -> the decide-path is used."""
    def decide(self, **kw): raise ConnectionError("simulated: provider down")
    def agent_turn(self, **kw): raise ConnectionError("simulated: provider down (tool path)")
    def resolve_chain(self, *a, **k): return None

CONNECTOR = ('mioconnect Stripe\n    address "https://api.stripe.com"\n'
             '    operation charge\n        path "/charge"\n    operation: done\n'
             'mioconnect: done\n')

# ── 1. no-tools (decide-path), WITH on.failure -> it fires ─────────────────────────
SRC1 = ('ai.agent helper\n    goal "help"\n'
        '    on.failure\n        show "AGENT-ONFAILURE-RAN"\nai.agent: done\n')
r1, shown1 = run(SRC1, _HardFailAi())
check("decide-path agent, hard failure, WITH on.failure -> it runs",
      "AGENT-ONFAILURE-RAN" in shown1, str(shown1))

# ── 2. no-tools, WITH not confident only -> it fires as the fallback ───────────────
SRC2 = ('ai.agent helper\n    goal "help"\n'
        '    not confident\n        show "AGENT-NOTCONFIDENT-RAN"\nai.agent: done\n')
r2, shown2 = run(SRC2, _HardFailAi())
check("decide-path agent, hard failure, WITH not confident (no on.failure) -> it runs",
      "AGENT-NOTCONFIDENT-RAN" in shown2, str(shown2))

# ── 3. neither declared -> correctly hard-fails loud (was: silent None) ────────────
SRC3 = 'ai.agent helper\n    goal "help"\nai.agent: done\n'
r3, shown3 = run(SRC3, _HardFailAi())
check("decide-path agent, hard failure, NO handlers -> a real 500 (was: silent None)",
      isinstance(r3, dict) and r3.get('status') == 500, str(r3))
check("the 500 names the agent and the real failure reason",
      isinstance(r3, dict) and 'helper' in str(r3.get('body', '')) and
      'provider down' in str(r3.get('body', '')), str(r3))

# ── 3b. Direct runtime-level check on the REAL AnthropicAiRuntime.agent_turn() --
#        the interpreter-level checks above use a hand-rolled mock, which cannot
#        prove agent_turn() itself raises rather than faking a text turn. This is
#        the specific method the bug lived in. ──────────────────────────────────────
from mohio_ai import AnthropicAiRuntime, AiProviderError
_rt = AnthropicAiRuntime.__new__(AnthropicAiRuntime)
_rt._overrides = {}; _rt._verbose = False; _rt._model = "gpt-4o"
_rt._calls = 0; _rt._call_cap = 0; _rt._chains = {}
def _boom(*a, **k): raise ConnectionError("simulated: provider down")
_rt._complete = _boom
try:
    turn = _rt.agent_turn(messages=[{"role": "user", "content": "hi"}], tools=None, model="gpt-4o")
    check("AnthropicAiRuntime.agent_turn() raises on a hard failure (non-Claude path) "
          "(was: returned a fake successful text turn)", False, f"got: {turn}")
except AiProviderError as e:
    check("AnthropicAiRuntime.agent_turn() raises AiProviderError on a hard failure "
          "(non-Claude path)", "provider down" in str(e), str(e))

_rt2 = AnthropicAiRuntime.__new__(AnthropicAiRuntime)
_rt2._overrides = {}; _rt2._verbose = False; _rt2._model = "claude-sonnet-4-6"
_rt2._calls = 0; _rt2._call_cap = 0; _rt2._chains = {}; _rt2._anthropic_key = "sk-fake"
class _BoomClient:
    class messages:
        @staticmethod
        def create(**kw): raise ConnectionError("simulated: claude down")
_rt2._client = _BoomClient()
try:
    turn2 = _rt2.agent_turn(messages=[{"role": "user", "content": "hi"}], tools=None, model="claude-sonnet-4-6")
    check("AnthropicAiRuntime.agent_turn() raises on a hard failure (Claude path) "
          "(was: returned a fake successful text turn)", False, f"got: {turn2}")
except AiProviderError as e:
    check("AnthropicAiRuntime.agent_turn() raises AiProviderError on a hard failure "
          "(Claude path)", "claude down" in str(e), str(e))

# ── 4. tool-enabled (agent_turn path), WITH on.failure -> it fires ─────────────────
#    This is the specific bug: agent_turn() used to fake a successful text turn.
SRC4 = (CONNECTOR +
        'ai.agent helper\n    goal "help"\n'
        '    tools\n        Stripe.charge\n    tools: done\n'
        '    on.failure\n        show "TOOL-AGENT-ONFAILURE-RAN"\nai.agent: done\n')
r4, shown4 = run(SRC4, _HardFailAi())
check("tool-path agent (agent_turn), hard failure, WITH on.failure -> it runs",
      "TOOL-AGENT-ONFAILURE-RAN" in shown4, str(shown4))

# ── 5. tool-enabled, NO handler -> real 500, and CRITICALLY no fake success text ───
SRC5 = (CONNECTOR +
        'ai.agent helper\n    goal "help"\n'
        '    tools\n        Stripe.charge\n    tools: done\nai.agent: done\n')
r5, shown5 = run(SRC5, _HardFailAi())
check("tool-path agent, hard failure, NO handler -> a real 500 (was: silent None)",
      isinstance(r5, dict) and r5.get('status') == 500, str(r5))
check("the old fake-success marker never appears anywhere in the response",
      "[provider error" not in str(r5), str(r5))

# ── 6. a boundary-gate breach (max steps) ALSO reaches on.failure, not just a
#       provider error -- both failure sources converge on the same handling ───────
class _NeverDoneAi:
    def decide(self, **kw):
        return types.SimpleNamespace(result='keep going', confidence=0.99,
                                     fell_back=False, model='mock', tokens=1, cost=0.0)
    def resolve_chain(self, *a, **k): return None

SRC6 = ('ai.agent helper\n    goal "help"\n'
        '    limits\n        max steps 1\n    limits: done\n'
        '    on.failure\n        show "BOUNDARY-ONFAILURE-RAN"\nai.agent: done\n')
r6, shown6 = run(SRC6, _NeverDoneAi())
check("a boundary-gate breach (max steps) also reaches on.failure (not just provider errors)",
      "BOUNDARY-ONFAILURE-RAN" in shown6, str(shown6))

# ── 7. Regression guard: a genuinely successful agent run is unaffected ────────────
class _SucceedsAi:
    def decide(self, **kw):
        return types.SimpleNamespace(result='DONE', confidence=0.99,
                                     fell_back=False, model='mock', tokens=1, cost=0.0)
    def resolve_chain(self, *a, **k): return None

SRC7 = 'ai.agent helper\n    goal "help"\nai.agent: done\nshow helper\n'
r7, shown7 = run(SRC7, _SucceedsAi())
check("a genuinely successful agent run is unaffected by this fix (regression guard)",
      "DONE" in shown7, str(shown7))

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
