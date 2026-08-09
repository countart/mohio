# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Model resolution order: explicit call > active chain > app default (2026-08-04 ruling,
Stage 3 of the model-selection sequence).

Two compounding defects, fixed as one unit per the ruling (not two patches):

1. mohio_ai.py's _decide_impl had the precedence BACKWARDS: a resolved ai.connect chain
   silently overruled an explicit model_override, even though the override is the
   developer's own instruction for THIS specific call. Now the override wins outright
   and chain resolution is not even consulted when one is present.

2. ai.agent's no-tools path (mohio_interpreter.py, the plain self.ai.decide(...) call
   inside the reasoning loop) never passed a model at all -- a declared `model "..."`
   on a no-tools ai.agent was silently ignored, while its tool-enabled sibling
   (agent_turn(model=model, ...)) already passed it correctly. Fixed to pass
   model_override only when the developer actually declared one, so an undeclared
   model still falls through to decide()'s own chain/app-default resolution rather
   than forcing the agent's local dated-string default in as a fake "explicit"
   instruction that would now outrank an active chain.

Adversarial proof, exactly the three cases named in the ruling:
  (a) explicit override wins over an active (resolved) chain
  (b) a chain still works normally when no override is given
  (c) ai.agent's no-tools path now honors an explicit model the same way its
      tool-enabled sibling already does

Run: `python tests/test_model_resolution_order.py`.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')

from pathlib import Path
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter
from mohio_ai import AnthropicAiRuntime, CompletionResult

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

def make_rt():
    rt = AnthropicAiRuntime.__new__(AnthropicAiRuntime)
    rt._overrides = {}; rt._verbose = False; rt._model = "claude-sonnet-4-6"
    rt._calls = 0; rt._call_cap = 0; rt._chains = {}
    return rt

# ── (a) explicit override wins over an active, RESOLVED chain ──────────────────────
rt_a = make_rt()
rt_a._complete = lambda model, s, u, temperature=None, max_tokens=None: (
    "ok" if u == "ping" else CompletionResult(text='{"result": true, "confidence": 0.9, "explanation": "x"}'))
chain_a = rt_a.register_chain("chain_a", ["gpt-4o"])
rt_a.resolve_chain("chain_a")
check("chain_a resolved successfully (precondition)",
      chain_a.resolved and chain_a.active_provider == "gpt-4o")
d_a = rt_a.decide(name="x", inputs={}, threshold=0.85, return_type="boolean",
                  chain_name="chain_a", model_override="claude-explicit")
check("(a) explicit model_override WINS over a resolved chain (was: chain won, backwards)",
      d_a.model == "claude-explicit", d_a.model)

# ── (b) chain still works normally when no override is given (regression) ──────────
rt_b = make_rt()
rt_b._complete = lambda model, s, u, temperature=None, max_tokens=None: (
    "ok" if u == "ping" else CompletionResult(text='{"result": true, "confidence": 0.9, "explanation": "x"}'))
chain_b = rt_b.register_chain("chain_b", ["gpt-4o"])
rt_b.resolve_chain("chain_b")
d_b = rt_b.decide(name="x", inputs={}, threshold=0.85, return_type="boolean",
                  chain_name="chain_b")
check("(b) a resolved chain is still used normally when NO override is given",
      d_b.model == "gpt-4o", d_b.model)

# Regression: no override, no chain -> app default (self._model) used, unaffected.
rt_b2 = make_rt()
rt_b2._complete = lambda model, s, u, temperature=None, max_tokens=None: (
    CompletionResult(text='{"result": true, "confidence": 0.9, "explanation": "x"}'))
d_b2 = rt_b2.decide(name="x", inputs={}, threshold=0.85, return_type="boolean")
check("regression: no override, no chain -> app default (self._model) used",
      d_b2.model == "claude-sonnet-4-6", d_b2.model)

# Regression: no override, chain EXHAUSTED -> still raises (today's earlier fix, unaffected).
from mohio_ai import AiProviderError
rt_b3 = make_rt()
rt_b3._complete = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("down"))
rt_b3.register_chain("dead", ["gpt-4o"])
rt_b3.resolve_chain("dead")
try:
    rt_b3.decide(name="x", inputs={}, threshold=0.85, return_type="boolean", chain_name="dead")
    check("regression: an exhausted chain with no override still raises", False)
except AiProviderError:
    check("regression: an exhausted chain with no override still raises", True)

# ── (c) ai.agent's no-tools path now honors an explicit model ──────────────────────
class _TrackingAi:
    def __init__(self):
        self.decide_calls = []
        self.agent_turn_calls = []
    def resolve_chain(self, *a, **k): return None
    def decide(self, **kw):
        self.decide_calls.append(kw)
        import types
        return types.SimpleNamespace(result='DONE', confidence=0.99, fell_back=False,
                                     model=kw.get('model_override', 'unset'),
                                     tokens=1, cost=0.0)
    def agent_turn(self, **kw):
        self.agent_turn_calls.append(kw)
        from mohio_interpreter import AgentTurn
        return AgentTurn(kind='text', text='DONE')

SRC_NO_TOOLS = ('ai.agent helper\n    goal "help"\n    model "gpt-4o-declared"\n'
               'ai.agent: done\n')
ai1 = _TrackingAi()
prog1 = transform(_P.parse(SRC_NO_TOOLS), SRC_NO_TOOLS)
it1 = MohioInterpreter(ai=ai1)
it1.run_declarations(prog1); it1.shown = []
it1.run(prog1)
check("(c) ai.agent no-tools path passes the declared model_override to decide()",
      len(ai1.decide_calls) >= 1 and ai1.decide_calls[0].get('model_override') == 'gpt-4o-declared',
      str(ai1.decide_calls))

# Regression: no-tools path with NO declared model -> model_override is NOT forced in at all
# (must fall through to decide()'s own resolution, not the agent's local dated default).
SRC_NO_TOOLS_NO_MODEL = 'ai.agent helper\n    goal "help"\nai.agent: done\n'
ai2 = _TrackingAi()
prog2 = transform(_P.parse(SRC_NO_TOOLS_NO_MODEL), SRC_NO_TOOLS_NO_MODEL)
it2 = MohioInterpreter(ai=ai2)
it2.run_declarations(prog2); it2.shown = []
it2.run(prog2)
check("regression: no-tools path with NO declared model never passes model_override at all",
      len(ai2.decide_calls) >= 1 and 'model_override' not in ai2.decide_calls[0],
      str(ai2.decide_calls))

# Regression: the tool-enabled sibling path still passes model correctly (unchanged).
CONNECTOR = ('mioconnect Stripe\n    address "https://api.stripe.com"\n'
             '    operation charge\n        path "/charge"\n    operation: done\n'
             'mioconnect: done\n')
SRC_TOOLS = (CONNECTOR +
             'ai.agent helper\n    goal "help"\n    model "gpt-4o-declared"\n'
             '    tools\n        Stripe.charge\n    tools: done\nai.agent: done\n')
ai3 = _TrackingAi()
prog3 = transform(_P.parse(SRC_TOOLS), SRC_TOOLS)
it3 = MohioInterpreter(ai=ai3)
it3.run_declarations(prog3); it3.shown = []
it3.run(prog3)
check("regression: the tool-enabled sibling still passes the declared model to agent_turn()",
      len(ai3.agent_turn_calls) >= 1 and ai3.agent_turn_calls[0].get('model') == 'gpt-4o-declared',
      str(ai3.agent_turn_calls))

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
