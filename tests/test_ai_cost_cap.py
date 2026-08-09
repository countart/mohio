# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""A8's declared cost ceiling (`ai.agent`'s `limits / cost ceiling N / limits: done`) now
genuinely enforces (2026-08-06). Found scaffolding-only, not enforcing: the boundary-gate
check in `_exec_AiAgentBlock` was always in the right place, but nothing anywhere ever
populated `.cost` on the objects it reads -- `AgentTurn.cost` defaulted to 0.0 and was never
overridden, `AiDecision` had no `cost`/`tokens` field at all. `accumulated_cost` was
permanently 0.0 in real operation, so the check could never trip. Full finding logged in
CLAUDE-CODE-BACKLOG.md's A8 entry.

Fixed in two layers, both covered here:
  1. Real per-call USD cost, computed from real provider token usage (mohio_ai.py:
     MODEL_PRICING_PER_1M / _price_for_model / _cost_for_tokens / CompletionResult),
     wired into every AgentTurn and AiDecision construction.
  2. A cost-breach fall back to not confident / on.failure now carries an unambiguous,
     self-describing explanation -- never the bare "Agent limit exceeded: cost v > c" text,
     never confusable with a steps/tokens/timeout breach or a genuine provider failure.
     Same distinct reason lands in the ai.audit log entry, not just the structured
     metric/value/ceiling fields.

Also covers item C's first slice (2026-08-06): pre-call estimated-cost refusal, real
per-provider token counting (never a heuristic -- see requirements.txt for why one was
rejected). Claude via the Anthropic SDK's own messages.count_tokens, Gemini via its own
:countTokens REST endpoint (confirmed to exist independently of GPT's situation, not
assumed into the same bucket), GPT via tiktoken (neither Claude's nor Gemini's kind of
provider-native method exists for it). Proves the pre-call refusal genuinely stops the
over-budget call BEFORE it fires -- one call earlier than post-call-only tracking would.

Run: python tests/test_ai_cost_cap.py
"""
import os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')

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
    r = it.run(prog)
    return r, it, prog

# ── 1. Pricing math (pure functions, no interpreter needed) ────────────────────────
from mohio_ai import _price_for_model, _cost_for_tokens, MODEL_PRICING_PER_1M

check("known model: exact published rate used",
      _price_for_model("claude-sonnet-5") == MODEL_PRICING_PER_1M["claude-sonnet"])
check("longest-prefix match: claude-opus beats a shorter claude- entry if one existed",
      _price_for_model("claude-opus-4-1") == MODEL_PRICING_PER_1M["claude-opus"])
check("unknown model in a known vendor family falls back to that family's worst case",
      _price_for_model("claude-totally-new-model") == MODEL_PRICING_PER_1M["claude-opus"])
check("totally unknown model never prices as free",
      _price_for_model("some-made-up-model") != (0.0, 0.0))
check("cost math: 1M input + 1M output tokens on claude-sonnet costs its published rates exactly",
      _cost_for_tokens("claude-sonnet-5", 1_000_000, 1_000_000) == 3.00 + 15.00,
      str(_cost_for_tokens("claude-sonnet-5", 1_000_000, 1_000_000)))
check("cost math: zero tokens costs exactly zero",
      _cost_for_tokens("claude-sonnet-5", 0, 0) == 0.0)

# ── 2. _complete()'s Claude path really returns real usage, not just text ──────────
from mohio_ai import AnthropicAiRuntime, CompletionResult

_rt = AnthropicAiRuntime.__new__(AnthropicAiRuntime)
_rt._overrides = {}; _rt._verbose = False; _rt._model = "claude-sonnet-5"
_rt._calls = 0; _rt._call_cap = 0; _rt._chains = {}; _rt._anthropic_key = "sk-fake"

class _FakeUsage:
    input_tokens = 1000
    output_tokens = 500

class _FakeBlock:
    text = "the answer"

class _FakeMsg:
    content = [_FakeBlock()]
    usage = _FakeUsage()

class _FakeClient:
    class messages:
        @staticmethod
        def create(**kw): return _FakeMsg()

_rt._client = _FakeClient()
res = _rt._complete("claude-sonnet-5", "sys", "user")
check("_complete() returns a CompletionResult, not bare text",
      isinstance(res, CompletionResult), type(res).__name__)
check("_complete() carries the real input token count from the provider response",
      res.input_tokens == 1000, res.input_tokens)
check("_complete() carries the real output token count from the provider response",
      res.output_tokens == 500, res.output_tokens)
check("_complete() still carries the real text",
      res.text == "the answer", res.text)

# ── 2b. agent_turn()'s Claude path converts real usage into a real, nonzero
#       AgentTurn.cost -- this is the EXACT wiring gap the original A8 bug had
#       (tokens were captured, cost never was). Direct, not through _exec_AiAgentBlock,
#       so a regression here is caught even if the boundary-gate consumer tests above
#       are satisfied by a mock that bypasses this layer entirely. ─────────────────────
class _FakeToolMsg:
    content = [_FakeBlock()]
    usage = _FakeUsage()
    stop_reason = "end_turn"

class _FakeToolClient:
    class messages:
        @staticmethod
        def create(**kw): return _FakeToolMsg()

_rt3 = AnthropicAiRuntime.__new__(AnthropicAiRuntime)
_rt3._overrides = {}; _rt3._verbose = False; _rt3._model = "claude-sonnet-5"
_rt3._calls = 0; _rt3._call_cap = 0; _rt3._chains = {}; _rt3._anthropic_key = "sk-fake"
_rt3._client = _FakeToolClient()
turn = _rt3.agent_turn(messages=[{"role": "user", "content": "hi"}], tools=None, model="claude-sonnet-5")
check("agent_turn()'s Claude path returns a nonzero cost for a real (1000 in / 500 out) call",
      turn.cost > 0.0, turn.cost)
check("agent_turn()'s Claude-path cost matches the pricing table exactly "
      "(1000 input + 500 output tokens on claude-sonnet)",
      abs(turn.cost - _cost_for_tokens("claude-sonnet-5", 1000, 500)) < 1e-9, turn.cost)

# Same check for the non-Claude (gpt/gemini) text path, the other branch touched by
# this fix -- monkey-patch _complete() directly since that path is provider-agnostic.
_rt4 = AnthropicAiRuntime.__new__(AnthropicAiRuntime)
_rt4._overrides = {}; _rt4._verbose = False; _rt4._model = "gpt-4o"
_rt4._calls = 0; _rt4._call_cap = 0; _rt4._chains = {}
_rt4._complete = lambda *a, **k: CompletionResult(text="ok", input_tokens=1000, output_tokens=500)
turn_gpt = _rt4.agent_turn(messages=[{"role": "user", "content": "hi"}], tools=None, model="gpt-4o")
check("agent_turn()'s non-Claude (gpt/gemini) path also returns a nonzero cost",
      turn_gpt.cost > 0.0, turn_gpt.cost)
check("agent_turn()'s non-Claude-path cost matches the pricing table exactly",
      abs(turn_gpt.cost - _cost_for_tokens("gpt-4o", 1000, 500)) < 1e-9, turn_gpt.cost)

# ── 3. End to end: a real cost breach genuinely stops the agent BEFORE the over-budget
#      call, and routes to not confident with the distinct explanation ────────────────
class _CostlyAi:
    """Every call reports a real $2.00 cost -- two calls cross a $3.00 ceiling on the
    THIRD call's pre-check, proving the gate is checked before that call fires, not
    after it silently completes."""
    def __init__(self):
        self.calls_made = 0
    def decide(self, **kw):
        self.calls_made += 1
        return types.SimpleNamespace(result='keep going', confidence=0.99,
                                     fell_back=False, model='mock', tokens=100, cost=2.00)
    def resolve_chain(self, *a, **k): return None

SRC_COST = ('ai.agent helper\n    goal "help"\n'
            '    limits\n        max steps 10\n        cost ceiling 3.00\n    limits: done\n'
            '    not confident\n        show "NOT-CONFIDENT-RAN"\nai.agent: done\n')
ai = _CostlyAi()
r, it, prog = run(SRC_COST, ai)
check("a real cost breach routes to not confident",
      "NOT-CONFIDENT-RAN" in it.shown, str(it.shown))
check("the gate stops BEFORE the call that would push further over budget "
      "(2 calls -> $4.00 total would exceed 3.00, so at most 2 calls run)",
      ai.calls_made <= 2, ai.calls_made)

# ── 4. Distinguishability: the cost explanation is self-describing and never collides
#      with a steps/timeout breach or a raw provider failure string ───────────────────
class _NeverDoneAi:
    def decide(self, **kw):
        return types.SimpleNamespace(result='keep going', confidence=0.99,
                                     fell_back=False, model='mock', tokens=1, cost=0.0)
    def resolve_chain(self, *a, **k): return None

class _HardFailAi:
    def decide(self, **kw): raise ConnectionError("simulated: provider down")
    def resolve_chain(self, *a, **k): return None

def _capture_explanation(src, ai):
    prog = transform(_P.parse(src), src)
    it2 = MohioInterpreter(ai=ai)
    it2.run_declarations(prog)
    it2.shown = []
    it2.run(prog)
    joined = "\n".join(str(s) for s in it2.shown)
    expl = joined.split("EXPL=", 1)[1].split("\n", 1)[0] if "EXPL=" in joined else ""
    err = joined.split("ERR=", 1)[1].split("\n", 1)[0] if "ERR=" in joined else ""
    return expl, err

TAIL = ('show "EXPL={{ _agent_helper_explanation }}"\n'
        'show "ERR={{ _agent_helper_error }}"\n')

SRC_STEPS = ('ai.agent helper\n    goal "help"\n'
             '    limits\n        max steps 1\n    limits: done\n'
             '    not confident\n        show "x"\nai.agent: done\n' + TAIL)
steps_expl, steps_err = _capture_explanation(SRC_STEPS, _NeverDoneAi())

SRC_COST2 = ('ai.agent helper\n    goal "help"\n'
             '    limits\n        max steps 10\n        cost ceiling 0.01\n    limits: done\n'
             '    not confident\n        show "x"\nai.agent: done\n' + TAIL)
cost_expl, cost_err = _capture_explanation(SRC_COST2, _CostlyAi())

SRC_FAIL = ('ai.agent helper\n    goal "help"\n'
            '    not confident\n        show "x"\nai.agent: done\n' + TAIL)
fail_expl, fail_err = _capture_explanation(SRC_FAIL, _HardFailAi())

check("cost-breach explanation is self-describing (names cost specifically)",
      cost_expl is not None and "COST" in str(cost_expl).upper(), cost_expl)
check("cost-breach explanation is never the same string as a steps-breach explanation",
      cost_expl != steps_expl, (cost_expl, steps_expl))
check("cost-breach explanation is never the same string as a raw provider-failure error",
      cost_expl != fail_err and str(cost_expl) not in str(fail_err), (cost_expl, fail_err))
check("steps-breach explanation is self-describing too (names steps, not cost)",
      steps_expl is not None and "STEP" in str(steps_expl).upper()
      and "COST" not in str(steps_expl).upper(), steps_expl)
check("a genuine provider failure gets no cost/step explanation at all (stays empty)",
      not fail_expl, fail_expl)

# ── 5. Regression: a real $0.00 (mock) run never spuriously breaches a real ceiling ──
SRC_FREE = ('ai.agent helper\n    goal "help"\n'
            '    limits\n        max steps 3\n        cost ceiling 1.00\n    limits: done\n'
            '    not confident\n        show "SHOULD-NOT-RUN"\nai.agent: done\n'
            'show helper\n')
class _FreeAi:
    def decide(self, **kw):
        return types.SimpleNamespace(result='DONE', confidence=0.99,
                                     fell_back=False, model='mock', tokens=1, cost=0.0)
    def resolve_chain(self, *a, **k): return None
r5, it5, _ = run(SRC_FREE, _FreeAi())
check("a genuinely free (mock) run under a real cost ceiling completes normally, "
      "not falsely flagged as a breach",
      "SHOULD-NOT-RUN" not in it5.shown and "DONE" in it5.shown, str(it5.shown))

# ── 6. Pre-call estimation (C, item 1): real per-provider token counting ───────────
from mohio_ai import AnthropicAiRuntime as _AAR

# 6a. Claude: real messages.count_tokens integration, monkey-patched SDK client.
_rt5 = _AAR.__new__(_AAR)
_rt5._overrides = {}; _rt5._verbose = False; _rt5._model = "claude-sonnet-5"
_rt5._calls = 0; _rt5._call_cap = 0; _rt5._chains = {}; _rt5._anthropic_key = "sk-fake"
class _FakeCountResult:
    input_tokens = 12345
class _FakeCountClient:
    class messages:
        @staticmethod
        def count_tokens(**kw): return _FakeCountResult()
_rt5._client = _FakeCountClient()
n = _rt5.estimate_input_tokens("claude-sonnet-5", [{"role": "user", "content": "hi"}])
check("Claude pre-call estimate uses the real messages.count_tokens result",
      n == 12345, n)

# 6b. GPT: real tiktoken integration, no mocking needed (offline, real library).
n_gpt = _rt5.estimate_input_tokens("gpt-4o", [{"role": "user", "content": "hello world, this is a test"}])
check("GPT pre-call estimate returns a real, positive tiktoken count",
      isinstance(n_gpt, int) and n_gpt > 0, n_gpt)
check("GPT pre-call estimate for a known short phrase matches tiktoken directly (no heuristic math)",
      n_gpt == 7, n_gpt)  # "hello world, this is a test" == 7 tokens, verified directly with tiktoken earlier

# 6c. Gemini: real :countTokens integration, monkey-patched HTTP layer.
import urllib.request as _ur, json as _json_mod
class _FakeGeminiResp:
    def __init__(self, payload): self._payload = payload
    def read(self): return _json_mod.dumps(self._payload).encode()
    def __enter__(self): return self
    def __exit__(self, *a): return False
_orig_urlopen = _ur.urlopen
_captured_url = {}
def _fake_urlopen(req, timeout=30):
    _captured_url['url'] = req.full_url
    return _FakeGeminiResp({"totalTokens": 999})
_ur.urlopen = _fake_urlopen
try:
    os.environ['GEMINI_API_KEY'] = 'fake-key-for-test'
    n_gem = _rt5.estimate_input_tokens("gemini-1.5-pro", [{"role": "user", "content": "hi"}])
finally:
    _ur.urlopen = _orig_urlopen
    os.environ.pop('GEMINI_API_KEY', None)
check("Gemini pre-call estimate uses its own real :countTokens response (totalTokens field)",
      n_gem == 999, n_gem)
check("Gemini pre-call estimate hits the countTokens endpoint specifically, not generateContent",
      ':countTokens' in _captured_url.get('url', ''), _captured_url.get('url'))

# ── 7. End to end: pre-call refusal stops the over-budget call ONE CALL EARLIER than
#      post-call-only tracking would -- the actual point of building C at all. ────────
class _PrecallAwareAi:
    """estimate_input_tokens returns a small, known value (pre-call estimate stays well
    under ceiling); the REAL post-call cost is large ($2.90). First call's pre-call check
    passes (nothing spent yet), first call actually runs and reports $2.90. Second call's
    PRE-call check now sees accumulated ($2.90) + estimate (~$0.30) > ceiling ($3.00) and
    must refuse BEFORE that second call ever fires -- proving calls_made stops at 1, not 2."""
    def __init__(self):
        self.calls_made = 0
    def estimate_input_tokens(self, model, messages, system=None, tools=None):
        return 100_000  # -> ~$0.30 on claude-sonnet at (100000/1e6)*3.00 + small output
    def decide(self, **kw):
        self.calls_made += 1
        return types.SimpleNamespace(result='keep going', confidence=0.99,
                                     fell_back=False, model='claude-sonnet-5', tokens=100, cost=2.90)
    def resolve_chain(self, *a, **k): return None

SRC_PRECALL = ('ai.agent helper\n    goal "help"\n'
               '    limits\n        max steps 10\n        max tokens 200\n'
               '        cost ceiling 3.00\n    limits: done\n'
               '    not confident\n        show "PRECALL-STOPPED"\nai.agent: done\n')
ai7 = _PrecallAwareAi()
r7, it7, _ = run(SRC_PRECALL, ai7)
check("pre-call refusal routes to not confident",
      "PRECALL-STOPPED" in it7.shown, str(it7.shown))
check("pre-call refusal stops the SECOND call from ever firing -- only 1 real call happened "
      "(post-call-only tracking would have let a 2nd $2.90 call through before catching it)",
      ai7.calls_made == 1, ai7.calls_made)

# ── 8. Distinguishability: cost_precall gets its own explanation, distinct from a
#      post-call cost breach, steps breach, or provider failure. ───────────────────────
def _capture_explanation2(src, ai):
    prog = transform(_P.parse(src), src)
    it2 = MohioInterpreter(ai=ai)
    it2.run_declarations(prog)
    it2.shown = []
    it2.run(prog)
    joined = "\n".join(str(s) for s in it2.shown)
    return joined.split("EXPL=", 1)[1].split("\n", 1)[0] if "EXPL=" in joined else ""

precall_expl = _capture_explanation2(SRC_PRECALL + TAIL, _PrecallAwareAi())
check("pre-call-refusal explanation is self-describing (names PRECALL specifically)",
      "PRECALL" in precall_expl.upper(), precall_expl)
check("pre-call-refusal explanation is never the same string as a post-call cost-breach explanation",
      precall_expl != cost_expl, (precall_expl, cost_expl))
check("pre-call-refusal explanation is never the same string as a steps-breach explanation",
      precall_expl != steps_expl, (precall_expl, steps_expl))

# ── 9. Regression: MockAiRuntime's own estimate works offline, no network/tiktoken/key ──
from mohio_interpreter import MockAiRuntime as _MockRT
_mock = _MockRT()
n_mock = _mock.estimate_input_tokens("claude-sonnet-5", [{"role": "user", "content": "a short message"}])
check("MockAiRuntime's estimate_input_tokens returns a real positive number offline",
      isinstance(n_mock, int) and n_mock > 0, n_mock)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
