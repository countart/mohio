# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""ai.agent tool-grant capability boundary (RECONSTRUCTED 2026-07-31).

The original test_agent_tools.py (chat da7118b0, Jul 20) was written and verified but never pushed;
this reconstructs it from the ratified behaviors in `_agent_tool_schemas` + the agent loop's grant
enforcement. It guards an autonomous agent's capability boundary -- a refactor that drops the check
would otherwise be silent. Five ratified behaviors (Jul 18, chat 70314f78):

  1. operation-level grant      -- `tools / Stripe.refund` -> only that operation is a tool
  2. bare-connector expansion   -- `tools / Stripe` -> every operation on the connector
  3. empty-list-means-no-tools  -- no tools grant -> empty tool list (reason, no reach)
  4. grant validation at setup  -- a grant to an unknown connector/operation fails loud before run
  5. TOOL_NOT_GRANTED enforcement -- the agent cannot call an operation it was not granted

Run: `python tests/test_agent_tools.py`.
"""
import os, sys, unittest.mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mohio_data
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['DATABASE_URL'] = ':memory:'

from pathlib import Path
from lark import Lark
from mohio_interpreter import MohioInterpreter, MockAiRuntime, MohioRuntimeError, Context, AgentTurn, AiDecision
from mohio_transformer_ast import transform as ast_transform

_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail: print(f"          {detail}")
    _p += bool(cond); _f += (not cond)

CONNECTOR = ('mioconnect Stripe\n    address "https://api.stripe.com"\n'
             '    operation refund\n        path "/refund"\n    operation: done\n'
             '    operation charge\n        path "/charge"\n    operation: done\n'
             'mioconnect: done\n')

def agent_src(tools_block):
    return (CONNECTOR +
            'ai.agent helper\n    goal "Help"\n' + tools_block +
            '    limits\n        max steps 2\n    limits: done\n'
            '    not confident\n        give back pending "human"\nai.agent: done\n')

def schemas_for(tools_block):
    """Set up connectors + shapes, then call _agent_tool_schemas directly (setup-time behavior)."""
    interp = MohioInterpreter(ai=MockAiRuntime())
    prog = ast_transform(P.parse(agent_src(tools_block)), agent_src(tools_block))
    ctx = Context(); interp._exec_declarations(prog, ctx)
    agent = next(s for s in prog.statements if type(s).__name__ == 'AiAgentBlock')
    schemas, routing = interp._agent_tool_schemas(agent, ctx)
    return sorted(s['name'] for s in schemas), routing

# 1. operation-level grant -> only that operation
names, routing = schemas_for('    tools\n        Stripe.refund\n    tools: done\n')
check("operation-level grant yields ONLY that operation as a tool",
      names == ['Stripe_refund'] and routing == {'Stripe_refund': ('Stripe', 'refund')}, str((names, routing)))

# 2. bare-connector expansion -> every operation
names, routing = schemas_for('    tools\n        Stripe\n    tools: done\n')
check("bare-connector grant expands to EVERY operation",
      names == ['Stripe_charge', 'Stripe_refund'] and set(routing) == {'Stripe_charge', 'Stripe_refund'}, str(names))

# 3. no tools grant -> empty
names, routing = schemas_for('')
check("no tools grant -> empty tool list (reason, no reach)", names == [] and routing == {}, str((names, routing)))

# 4a. grant to an unknown connector fails loud at setup (broad except so a mutation that removes the
# raise fails cleanly with the wrong/no message rather than crashing the run).
try:
    schemas_for('    tools\n        Nope.refund\n    tools: done\n'); _ok = False; _msg = "no exception raised"
except Exception as e:
    _ok = isinstance(e, MohioRuntimeError) and 'not declared' in str(e); _msg = f"{type(e).__name__}: {str(e)[:80]}"
check("grant to an UNKNOWN CONNECTOR fails loud at setup (named)", _ok, _msg)

# 4b. grant to an unknown operation fails loud at setup
try:
    schemas_for('    tools\n        Stripe.teleport\n    tools: done\n'); _ok = False; _msg = "no exception raised"
except Exception as e:
    _ok = isinstance(e, MohioRuntimeError) and 'does not have' in str(e); _msg = f"{type(e).__name__}: {str(e)[:80]}"
check("grant to an UNKNOWN OPERATION fails loud at setup (named)", _ok, _msg)

# 5. TOOL_NOT_GRANTED enforcement -- drive the agent to request granted vs ungranted tools.
class DrivenMock(MockAiRuntime):
    def __init__(self, tool): super().__init__(); self._tool = tool; self._turns = 0
    def agent_turn(self, *, messages, tools=None, model=None, temperature=None, max_tokens=None):
        self._turns += 1
        if self._turns == 1:
            return AgentTurn(kind='tool', tool_name=self._tool, tool_input={'amount': 1}, tool_id='t1')
        return AgentTurn(kind='text', text='done')

def drive(requested_tool):
    src = agent_src('    tools\n        Stripe.refund\n    tools: done\n')  # only refund granted
    interp = MohioInterpreter(ai=DrivenMock(requested_tool))
    prog = ast_transform(P.parse(src), src)
    interp.run_declarations(prog)
    hits = []
    def fake_urlopen(req, *a, **k):
        hits.append(getattr(req, 'full_url', str(req)))
        class R:
            status = 200
            def read(self): return b'{"ok":true}'
            def __enter__(self): return self
            def __exit__(self, *a): return False
        return R()
    with unittest.mock.patch('urllib.request.urlopen', fake_urlopen):
        try: interp.run(prog)
        except Exception: pass
    return hits, interp

# granted tool IS callable (positive control): the connector operation is actually invoked
hits, _ = drive('Stripe_refund')
check("a GRANTED tool is callable (its connector operation runs)", any('/refund' in h for h in hits), str(hits))

# ungranted tool is BLOCKED: TOOL_NOT_GRANTED, the operation is never called
hits, interp = drive('Stripe_charge')   # charge NOT granted (only refund is)
called = any('/charge' in h for h in hits)
logged = any('TOOL_NOT_GRANTED' in str(e) or 'tool_not_granted' in str(e)
             for log in getattr(interp, '_audit_logs', {}).values() for e in log)
check("an UNGRANTED tool is BLOCKED (operation never called)", not called, str(hits))
# Strict: the block must fire the ENFORCEMENT (TOOL_NOT_GRANTED audit event), not merely error out
# some other way. `logged or not called` here would pass even with enforcement disabled -- proven by
# mutation, that weaker check did not catch `if False`. Require the audit event.
check("the block fires the TOOL_NOT_GRANTED enforcement (audit event present)", logged,
      "no tool_not_granted audit event -- enforcement may be disabled")

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
