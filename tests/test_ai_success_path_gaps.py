# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""Two confirmed success-path gaps closed (2026-08-04), found during the AI success-path
rigor audit -- both had zero test coverage of any kind before this file, confirmed by a
live mutation against the real code that nothing in the 219-file suite caught.

1. _parse_response's string-to-boolean coercion (mohio_ai.py). A model answering a
   boolean decision with a STRING ("yes", "true", "1", "approved") instead of a real
   JSON boolean is a legitimate, expected response shape (the system prompt cannot force
   strict typing from every model). The coercion for RECOGNIZED strings was already
   correct but untested; an UNRECOGNIZED string ("maybe", "N/A", "unsure") used to fall
   through to a plain `in (...)` membership test and silently become False -- the exact
   same disease as the ai.decide hard-failure bug fixed earlier the same day, just one
   level deeper: the model answered, but not with a real boolean, and the caller could
   never tell that from a genuine "no". Fixed alongside this test: unrecognized strings
   now raise AiProviderError instead of silently coercing to False.

2. agent_turn's tool-use extraction (mohio_ai.py). The mechanism the entire ai.agent
   tool-calling feature depends on -- reading a real Anthropic Messages API response's
   `stop_reason` and `content` blocks into an AgentTurn(kind='tool', ...) -- had NEVER
   been exercised by any test with a real-shaped response object. Every existing test
   either scripted agent_turn() directly (MockAiRuntime subclasses) or never triggered
   stop_reason == 'tool_use' at all. Proven by mutation: breaking the stop_reason check
   entirely, the full 219-file suite passed unchanged.

Run: `python tests/test_ai_success_path_gaps.py`.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')

from mohio_ai import AnthropicAiRuntime, AiProviderError, _parse_response

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail: print(f"          {detail}")
    _p += bool(cond); _f += (not cond)

# ── Gap 1: _parse_response string-to-boolean coercion ──────────────────────────────

import json as _json

def parse_bool(result_str):
    raw = _json.dumps({"result": result_str, "confidence": 0.9, "explanation": "x"})
    return _parse_response(raw, "boolean")

for s in ("yes", "true", "1", "approved", "YES", "True"):
    result, confidence, _ = parse_bool(s)
    check(f'_parse_response coerces "{s}" -> True', result is True, f"got {result!r}")

for s in ("no", "false", "0", "denied", "NO", "False"):
    result, confidence, _ = parse_bool(s)
    check(f'_parse_response coerces "{s}" -> False', result is False, f"got {result!r}")

for s in ("maybe", "unsure", "N/A", "banana"):
    try:
        parse_bool(s)
        check(f'_parse_response raises on unrecognized boolean string "{s}" '
              f'(was: silently False)', False)
    except AiProviderError as e:
        check(f'_parse_response raises AiProviderError on unrecognized boolean string "{s}"',
              s.lower() in str(e).lower() or 'unrecognized' in str(e).lower(), str(e))

# Regression: a real JSON boolean is untouched (never goes through string coercion)
r, c, _ = _parse_response('{"result": true, "confidence": 0.95, "explanation": "x"}', "boolean")
check("a real JSON boolean result is unaffected (regression guard)", r is True, str(r))

# ── Gap 2: agent_turn's real tool-use extraction ────────────────────────────────────

class _Block:
    def __init__(self, type_, **kw):
        self.type = type_
        for k, v in kw.items(): setattr(self, k, v)

class _Usage:
    def __init__(self, input_tokens, output_tokens):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens

class _Message:
    def __init__(self, stop_reason, content, usage=None):
        self.stop_reason = stop_reason
        self.content = content
        self.usage = usage

def make_rt(fake_client):
    rt = AnthropicAiRuntime.__new__(AnthropicAiRuntime)
    rt._overrides = {}; rt._verbose = False; rt._model = "claude-sonnet-4-6"
    rt._calls = 0; rt._call_cap = 0; rt._chains = {}
    rt._anthropic_key = "sk-fake"
    rt._client = fake_client
    return rt

# A real Claude tool-use response has the tool_use block AFTER a leading text block
# (the model "thinks out loud" before calling the tool) -- deliberately NOT content[0],
# so a mutation that only checks the first block cannot pass by accident.
class _ToolClient:
    class messages:
        @staticmethod
        def create(**kw):
            return _Message(
                stop_reason='tool_use',
                content=[
                    _Block('text', text="I'll issue the refund."),
                    _Block('tool_use', name='Stripe_refund',
                          input={'amount': 50, 'reason': 'duplicate charge'},
                          id='toolu_01abc'),
                ],
                usage=_Usage(input_tokens=120, output_tokens=30),
            )

rt = make_rt(_ToolClient())
turn = rt.agent_turn(messages=[{"role": "user", "content": "refund the duplicate charge"}],
                     tools=[{"name": "Stripe_refund"}], model="claude-sonnet-4-6")
check("a real tool_use response yields kind='tool' (was: could silently fall through to text)",
      turn.kind == 'tool', f"got kind={turn.kind!r}")
check("tool_name is extracted correctly", turn.tool_name == 'Stripe_refund', turn.tool_name)
check("tool_input is extracted correctly (full dict, not dropped)",
      turn.tool_input == {'amount': 50, 'reason': 'duplicate charge'}, str(turn.tool_input))
check("tool_id is extracted correctly", turn.tool_id == 'toolu_01abc', turn.tool_id)
check("tokens are summed from usage (input + output)", turn.tokens == 150, turn.tokens)

# Regression: a genuine text-only completion (stop_reason != 'tool_use') still yields
# kind='text' -- this fix must not make every response look like a tool call.
class _TextClient:
    class messages:
        @staticmethod
        def create(**kw):
            return _Message(
                stop_reason='end_turn',
                content=[_Block('text', text='The refund is complete.')],
                usage=_Usage(input_tokens=80, output_tokens=12),
            )

rt2 = make_rt(_TextClient())
turn2 = rt2.agent_turn(messages=[{"role": "user", "content": "status?"}], tools=None,
                       model="claude-sonnet-4-6")
check("a genuine text completion still yields kind='text' (regression guard)",
      turn2.kind == 'text' and turn2.text == 'The refund is complete.', str(turn2))

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
