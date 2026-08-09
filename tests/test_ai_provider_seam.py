# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""A8 provider seam: the runtime activates on ANY provider key and routes ai.* to it (2026-07-31).

Adversarial proof (no real network -- urllib is mocked, so it runs anywhere):
  - ONLY an OpenAI key  -> runtime starts (no Anthropic key/client), ai.decide + ai.create run
                           end to end and route to api.openai.com.
  - ONLY a Gemini key   -> runtime starts, ai.decide + ai.create route to generativelanguage.
  - NO key (mock)       -> MockAiRuntime runs ai.decide + ai.create with labeled output.
  - the per-session call cap (MOHIO_AI_CALL_CAP) fails loud when exceeded.
  - generate_audio routes to OpenAI TTS by default and to ElevenLabs for an eleven* model.
  - MOHIO_AI=mock forces the mock even when a real key is present.

Run as a script: `python tests/test_ai_provider_seam.py` (exit 0 = pass).
"""
import os, sys, unittest.mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); os.chdir(ROOT)
import mohio_data
os.environ["DATABASE_URL"] = ":memory:"

from pathlib import Path
from lark import Lark
from mohio_interpreter import MohioInterpreter, MockAiRuntime
from mohio_transformer_ast import transform as ast_transform
from mohio_ai import AnthropicAiRuntime

_raw = mohio_data.GRAMMAR_PATH.read_text(encoding="utf-8")
_g = "\n".join(l for l in _raw.splitlines() if not l.strip().startswith("//"))
P = Lark(_g, parser="earley", ambiguity="resolve", propagate_positions=True)

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)

DECIDE = ('amount 100\nai.decide big returns boolean\n    confidence above 0.85\n    weigh\n'
          '        amount\n    not confident\n        give back pending "human"\nai.decide: done\n'
          'ai.decide big\n')
CREATE = 'ai.create haiku returns text\n    prompt "a haiku about rain"\nai.create: done\nshow haiku\n'

def make_urlopen(hits):
    def f(req, *a, **k):
        url = getattr(req, "full_url", str(req)); hits.append(url)
        if "openai.com/v1/audio" in url:      body = None                 # audio bytes
        elif "openai.com" in url:             body = '{"choices":[{"message":{"content":"generated"}}]}'
        elif "generativelanguage" in url:     body = '{"candidates":[{"content":{"parts":[{"text":"generated"}]}}]}'
        elif "elevenlabs.io" in url:          body = None
        else:                                 body = "{}"
        class R:
            def read(self): return b"AUDIOBYTES" if body is None else body.encode()
            def __enter__(self): return self
            def __exit__(self, *a): return False
        return R()
    return f

def run_program(rt, prog, hits):
    interp = MohioInterpreter(ai=rt)
    with unittest.mock.patch("urllib.request.urlopen", make_urlopen(hits)):
        return interp.run(ast_transform(P.parse(prog), prog))

def only(provider_env):
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "MOHIO_AI"):
        os.environ.pop(k, None)
    for k, v in provider_env.items():
        os.environ[k] = v

# ── ONLY an OpenAI key ─────────────────────────────────────────────────────────────────────
only({"OPENAI_API_KEY": "sk-openai-test"})
rt = AnthropicAiRuntime()
check("starts with ONLY an OpenAI key (no ANTHROPIC_API_KEY)", rt._model == "gpt-4o", rt._model)
check("no Anthropic client built at startup", rt._client is None)
hits = []
run_program(rt, DECIDE, hits)
run_program(rt, CREATE, hits)
check("ai.decide + ai.create route to OpenAI end to end",
      any("openai.com" in h for h in hits) and not any("anthropic" in h for h in hits), str(hits))

# ── ONLY a Gemini key ──────────────────────────────────────────────────────────────────────
only({"GEMINI_API_KEY": "gemini-test"})
rt = AnthropicAiRuntime()
check("starts with ONLY a Gemini key", rt._model == "gemini-1.5-pro", rt._model)
hits = []
run_program(rt, DECIDE, hits)
run_program(rt, CREATE, hits)
check("ai.decide + ai.create route to Gemini end to end",
      any("generativelanguage" in h for h in hits), str(hits))

# ── NO key -> mock ─────────────────────────────────────────────────────────────────────────
only({})
mock = MockAiRuntime()
hits = []
run_program(mock, DECIDE, hits)
res = run_program(mock, CREATE, hits)
check("no key -> MockAiRuntime runs ai.decide + ai.create (no network)", not hits, str(hits))

# ── per-session call cap fails loud ────────────────────────────────────────────────────────
only({"OPENAI_API_KEY": "sk-openai-test"})
rt = AnthropicAiRuntime(); rt._call_cap = 2
capped = None
try:
    with unittest.mock.patch("urllib.request.urlopen", make_urlopen([])):
        rt._complete("gpt-4o", "s", "u"); rt._complete("gpt-4o", "s", "u"); rt._complete("gpt-4o", "s", "u")
except RuntimeError as e:
    capped = str(e)
check("the 3rd call under a cap of 2 fails loud", capped is not None and "call cap" in capped.lower(), str(capped))

# ── generate_audio routing ─────────────────────────────────────────────────────────────────
only({"OPENAI_API_KEY": "sk-openai-test"})
rt = AnthropicAiRuntime()
hits = []
with unittest.mock.patch("urllib.request.urlopen", make_urlopen(hits)):
    out = rt.generate_audio(goal="hello world", voice="nova")
check("generate_audio defaults to OpenAI TTS (/v1/audio/speech) and returns bytes",
      any("openai.com/v1/audio/speech" in h for h in hits) and isinstance(out, (bytes, bytearray)), str(hits))
os.environ["ELEVENLABS_API_KEY"] = "el-test"
hits = []
with unittest.mock.patch("urllib.request.urlopen", make_urlopen(hits)):
    rt.generate_audio(goal="hi", model="eleven_multilingual_v2")
check("an eleven* audio model routes to ElevenLabs", any("elevenlabs.io" in h for h in hits), str(hits))

# ── MOHIO_AI=mock forces the mock even with a real key ─────────────────────────────────────
only({"OPENAI_API_KEY": "sk-openai-test", "MOHIO_AI": "mock"})
from mio import _construct_ai_runtime
forced = _construct_ai_runtime(None, False)
check("MOHIO_AI=mock forces MockAiRuntime even with a real key", isinstance(forced, MockAiRuntime),
      type(forced).__name__)

for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "MOHIO_AI", "ELEVENLABS_API_KEY"):
    os.environ.pop(k, None)
print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
