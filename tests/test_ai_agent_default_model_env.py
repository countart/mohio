# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""ai.agent's tool-enabled path (agent_turn) now shares mohio_ai.DEFAULT_ANTHROPIC_MODEL as its
undeclared-model fallback instead of a second, independently hardcoded copy
("claude-sonnet-4-20250514", confirmed dead against the live API 2026-08-05 -- a real
mio run --ai call returned a 404 not_found_error for that exact model string).

mohio_interpreter.py's _exec_AiAgentBlock used to compute
`model = node.model or "claude-sonnet-4-20250514"` unconditionally and pass it straight to
agent_turn() -- a tool-enabled ai.agent with no declared `model` line always sent this dead
string, regardless of MOHIO_AI_MODEL, even though ai.decide's own default already correctly
respected that env var. One source of truth now: the interpreter imports
DEFAULT_ANTHROPIC_MODEL from mohio_ai instead of hardcoding a second copy.

Adversarial proof:
  (a) MOHIO_AI_MODEL set, no declared model on a tool-enabled ai.agent -> the env-configured
      value reaches agent_turn(), and the old dead literal never appears.
  (b) a declared `model "..."` on the block still wins outright (regression, unaffected).

Run: `python tests/test_ai_agent_default_model_env.py`.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')
os.environ['MOHIO_AI_MODEL'] = 'env-configured-test-model'   # set BEFORE mohio_ai is ever imported

from pathlib import Path
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter, AgentTurn
import mohio_ai

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail: print(f"          {detail}")
    _p += bool(cond); _f += (not cond)

check("precondition: DEFAULT_ANTHROPIC_MODEL picked up MOHIO_AI_MODEL",
      mohio_ai.DEFAULT_ANTHROPIC_MODEL == 'env-configured-test-model', mohio_ai.DEFAULT_ANTHROPIC_MODEL)

import mohio_data
_RAW = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_G = '\n'.join(l for l in _RAW.splitlines() if not l.strip().startswith('//'))
_P = Lark(_G, parser='earley', ambiguity='resolve', propagate_positions=True)

class _TrackingAi:
    def __init__(self):
        self.agent_turn_calls = []
    def resolve_chain(self, *a, **k): return None
    def decide(self, **kw):
        import types
        return types.SimpleNamespace(result='DONE', confidence=0.99, fell_back=False,
                                      model='unused', tokens=1, cost=0.0)
    def agent_turn(self, **kw):
        self.agent_turn_calls.append(kw)
        return AgentTurn(kind='text', text='DONE')

CONNECTOR = ('mioconnect Stripe\n    address "https://api.stripe.com"\n'
             '    operation charge\n        path "/charge"\n    operation: done\n'
             'mioconnect: done\n')

# ── (a) no declared model on a tool-enabled ai.agent -> env-configured default reaches agent_turn() ──
SRC_NO_MODEL = (CONNECTOR +
                'ai.agent helper\n    goal "help"\n'
                '    tools\n        Stripe.charge\n    tools: done\nai.agent: done\n')
ai_a = _TrackingAi()
prog_a = transform(_P.parse(SRC_NO_MODEL), SRC_NO_MODEL)
it_a = MohioInterpreter(ai=ai_a)
it_a.run_declarations(prog_a); it_a.shown = []
it_a.run(prog_a)
check("(a) no declared model -> MOHIO_AI_MODEL-configured default reaches agent_turn()",
      len(ai_a.agent_turn_calls) >= 1 and ai_a.agent_turn_calls[0].get('model') == 'env-configured-test-model',
      str(ai_a.agent_turn_calls))
check("sibling: the old dead hardcoded model string never appears",
      len(ai_a.agent_turn_calls) >= 1 and ai_a.agent_turn_calls[0].get('model') != 'claude-sonnet-4-20250514',
      str(ai_a.agent_turn_calls))

# ── (b) regression: a declared model still wins outright, unaffected by this fix ──────────────────
SRC_DECLARED = (CONNECTOR +
                'ai.agent helper\n    goal "help"\n    model "gpt-4o-declared"\n'
                '    tools\n        Stripe.charge\n    tools: done\nai.agent: done\n')
ai_b = _TrackingAi()
prog_b = transform(_P.parse(SRC_DECLARED), SRC_DECLARED)
it_b = MohioInterpreter(ai=ai_b)
it_b.run_declarations(prog_b); it_b.shown = []
it_b.run(prog_b)
check("(b) regression: a declared model on the block still wins outright",
      len(ai_b.agent_turn_calls) >= 1 and ai_b.agent_turn_calls[0].get('model') == 'gpt-4o-declared',
      str(ai_b.agent_turn_calls))

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
