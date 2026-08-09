# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
ai.connect multi-provider test suite.

Three layers:
  1. FALLBACK HIERARCHY  — ResolvedChain resolve/fallback logic (mocked, runs anywhere)
  2. PROVIDER DISPATCH    — _complete routes each model to the right vendor (mocked)
  3. LIVE CONNECTIONS     — real API call per provider, SKIPPED unless its key is set
                            (this is the layer that verifies the actual models on Railway)

Run locally:   python3 test_ai_connect.py
Run on Railway (with ANTHROPIC_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY set):
               python3 test_ai_connect.py   -> live layer activates automatically
"""
import os
import sys
from pathlib import Path

from mohio_ai import ResolvedChain, AnthropicAiRuntime
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter
import mohio_data

_passed = 0
_failed = 0
_skipped = 0


def check(label, got, expect):
    global _passed, _failed
    ok = got == expect
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}"
          + ("" if ok else f"  (expected {expect!r})"))
    _passed += ok
    _failed += (not ok)


# ── Layer 1: fallback hierarchy ────────────────────────────────────────────
def test_fallback_hierarchy():
    print("\n=== Layer 1: fallback hierarchy ===")

    # Primary down, secondary up -> resolves to secondary
    chain = ResolvedChain("fraud", ["claude-sonnet-4-6", "gpt-4o", "gemini-1.5-pro"])
    up = {"claude-sonnet-4-6": False, "gpt-4o": True, "gemini-1.5-pro": True}
    chain.resolve(lambda p: up[p])
    check("primary down -> active provider", chain.active_provider, "gpt-4o")

    # Mid-loop failure advances forward
    nxt = chain.fallback(reason="rate limited")
    check("mid-loop fallback -> next provider", nxt, "gemini-1.5-pro")

    # Never goes backward
    chain2 = ResolvedChain("c2", ["a", "b", "c"])
    chain2.resolve(lambda p: True)
    check("all up -> first provider", chain2.active_provider, "a")
    chain2.fallback()
    check("after 1 fallback", chain2.active_provider, "b")
    chain2.fallback()
    check("after 2 fallbacks", chain2.active_provider, "c")

    # Exhaustion returns None (no provider left)
    exhausted = chain2.fallback()
    check("exhausted -> None", exhausted, None)


# ── Layer 2: provider dispatch ─────────────────────────────────────────────
def test_provider_dispatch():
    print("\n=== Layer 2: provider dispatch routing ===")

    class FakeAnthropic:
        class messages:
            @staticmethod
            def create(**k):
                class M:
                    content = [type("x", (), {"text": '{"result": true, "confidence": 0.9}'})()]
                return M()

    rt = AnthropicAiRuntime.__new__(AnthropicAiRuntime)
    rt._client = FakeAnthropic()
    rt._model = "claude-sonnet-4-6"
    rt._verbose = False
    rt._overrides = {}
    rt._chains = {}

    routed = {}
    rt._complete_openai = lambda model, *a, **k: routed.update(openai=model) or "openai-resp"
    rt._complete_gemini = lambda model, *a, **k: routed.update(gemini=model) or "gemini-resp"

    a = rt._complete("claude-sonnet-4-6", "s", "u")
    check("claude-* -> anthropic", a.text.startswith("{"), True)
    rt._complete("gpt-4o", "s", "u")
    check("gpt-4o -> openai", routed.get("openai"), "gpt-4o")
    rt._complete("gemini-1.5-pro", "s", "u")
    check("gemini-* -> gemini", routed.get("gemini"), "gemini-1.5-pro")

    # Unknown provider fails loud
    try:
        rt._complete("mystery-model", "s", "u")
        check("unknown model raises", False, True)
    except RuntimeError:
        check("unknown model raises", True, True)


# ── Layer 2b: ai.connect declaration registers chains ──────────────────────
def test_ai_connect_registration():
    print("\n=== Layer 2b: ai.connect registers chains ===")

    raw = mohio_data.GRAMMAR_PATH.read_text(encoding="utf-8")
    grammar = "\n".join(l for l in raw.splitlines() if not l.strip().startswith("//"))
    parser = Lark(grammar, parser="earley", ambiguity="resolve", propagate_positions=True)

    class MockAI:
        def __init__(self):
            self.registered = {}

        def register_chain(self, name, providers):
            self.registered[name] = providers

    mock = MockAI()
    src = (
        'ai.connect fraud_providers, text_gen\n'
        '    order\n'
        '        anthropic\n'
        '        openai model "gpt-4o"\n'
        '        gemini model "gemini-1.5-pro"\n'
        '    order: done\n'
        'ai.connect: done\n'
    )
    MohioInterpreter(ai=mock).run(transform(parser.parse(src), src), request={})
    expect = ["claude-sonnet-4-6", "gpt-4o", "gemini-1.5-pro"]
    check("fraud_providers order", mock.registered.get("fraud_providers"), expect)
    check("text_gen order", mock.registered.get("text_gen"), expect)


# ── Layer 3: live provider connections (env-gated) ─────────────────────────
def test_live_connections():
    global _skipped
    print("\n=== Layer 3: live provider connections (skipped without keys) ===")

    providers = [
        ("anthropic", "claude-sonnet-4-6", "ANTHROPIC_API_KEY"),
        ("openai", "gpt-4o", "OPENAI_API_KEY"),
        ("gemini", "gemini-1.5-pro", ("GEMINI_API_KEY", "GOOGLE_API_KEY")),
    ]

    # Build a runtime only if at least the Anthropic key exists (its __init__ needs it).
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        print("  [SKIP] no ANTHROPIC_API_KEY — live layer needs the runtime; skipping all live tests")
        _skipped += len(providers)
        return
    rt = AnthropicAiRuntime()

    for vendor, model, keyname in providers:
        keys = (keyname,) if isinstance(keyname, str) else keyname
        if not any(os.environ.get(k, "").strip() for k in keys):
            print(f"  [SKIP] {vendor} ({model}) — no {' or '.join(keys)}")
            _skipped += 1
            continue
        try:
            out = rt._complete(model, "Reply with exactly: ok", "ping",
                               temperature=0, max_tokens=10)
            ok = bool(out and str(out).strip())
            print(f"  [{'PASS' if ok else 'FAIL'}] {vendor} ({model}) live -> {str(out)[:40]!r}")
            globals()['_passed'] += ok
            globals()['_failed'] += (not ok)
        except Exception as e:
            print(f"  [FAIL] {vendor} ({model}) live raised {type(e).__name__}: {e}")
            globals()['_failed'] += 1


def test_using_chain_end_to_end():
    print("\n=== Layer 2c: ai.decide `using <chain>` end-to-end ===")
    raw = mohio_data.GRAMMAR_PATH.read_text(encoding="utf-8")
    grammar = "\n".join(l for l in raw.splitlines() if not l.strip().startswith("//"))
    parser = Lark(grammar, parser="earley", ambiguity="resolve", propagate_positions=True)

    class MockAI:
        def __init__(self):
            self.registered = {}
            self.resolved = []
            self.decided_with = "UNSET"

        def register_chain(self, n, p):
            self.registered[n] = p

        def resolve_chain(self, n):
            self.resolved.append(n)
            return (self.registered.get(n) or [None])[0]

        def decide(self, **kw):
            self.decided_with = kw.get("chain_name")
            class D:
                result = True; confidence = 0.9; explanation = "ok"
                model = "mock"; fell_back = False; inputs = {}
            return D()

    mock = MockAI()
    src = (
        'ai.connect fraud_providers\n'
        '    order\n'
        '        anthropic\n'
        '        openai model "gpt-4o"\n'
        '    order: done\n'
        'ai.connect: done\n\n'
        'ai.decide isFraudulent returns boolean\n'
        '    using fraud_providers\n'
        '    weigh transaction.amount, member.history\n'
        '    confidence above 0.85\n'
        '    not confident\n'
        '        give back false\n'
        '    not confident: done\n'
        'ai.decide: done\n'
    )
    MohioInterpreter(ai=mock).run(
        transform(parser.parse(src), src),
        request={"transaction": {"amount": 5000}, "member": {"history": "clean"}})
    check("chain registered", mock.registered.get("fraud_providers"),
          ["claude-sonnet-4-6", "gpt-4o"])
    check("chain resolved once", mock.resolved, ["fraud_providers"])
    check("decide() got chain_name", mock.decided_with, "fraud_providers")


if __name__ == "__main__":
    test_fallback_hierarchy()
    test_provider_dispatch()
    test_ai_connect_registration()
    test_using_chain_end_to_end()
    test_live_connections()
    print(f"\nRESULTS: {_passed} passed, {_failed} failed, {_skipped} skipped")
    sys.exit(1 if _failed else 0)
