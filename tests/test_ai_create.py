# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
ai.create generation test suite (text / image / video).

Layers:
  1. DISPATCH + ATTRIBUTES — each modality calls the right generator with the
     right attributes (mocked runtime; runs anywhere).
  2. ALIAS + FALLBACK       — result binds to `as <var>`; not-confident runs on failure.
  3. LIVE                    — real generation per modality, SKIPPED unless keys are set
                              (this is the layer that verifies actual models on Railway).

Run locally:   python3 test_ai_create.py
Run on Railway (keys set): live layer activates automatically.
"""
import io
import os
import sys
import contextlib
from pathlib import Path

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter

_passed = 0
_failed = 0
_skipped = 0

_raw = Path("mohio.lark").read_text(encoding="utf-8")
_GRAMMAR = "\n".join(l for l in _raw.splitlines() if not l.strip().startswith("//"))
_PARSER = Lark(_GRAMMAR, parser="earley", ambiguity="resolve", propagate_positions=True)


def check(label, got, expect):
    global _passed, _failed
    ok = got == expect
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}"
          + ("" if ok else f"  (expected {expect!r})"))
    _passed += ok
    _failed += (not ok)


def _run(src, ai, verbose=False):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        MohioInterpreter(ai=ai, verbose=verbose).run(transform(_PARSER.parse(src), src),
                                                      request={})
    return buf.getvalue()


class RecordAI:
    def __init__(self):
        self.calls = []

    def generate_text(self, **kw):
        self.calls.append(("text", kw)); return "OK-TEXT"

    def generate_image(self, **kw):
        self.calls.append(("image", kw)); return "https://img/out.png"

    def generate_video(self, **kw):
        self.calls.append(("video", kw)); return "https://vid/out.mp4"


def test_dispatch_and_attributes():
    print("\n=== Layer 1: dispatch + attributes ===")

    ai = RecordAI()
    _run('ai.create headline returns text as r\n'
         '    goal "a tagline"\n    persona "witty"\n    temperature 0.9\n'
         '    not confident\n        give back none\nai.create: done\n', ai)
    kind, kw = ai.calls[0]
    check("text -> generate_text", kind, "text")
    check("text goal", kw.get("goal"), "a tagline")
    check("text persona", kw.get("persona"), "witty")

    ai = RecordAI()
    _run('ai.create poster returns image as b\n'
         '    goal "a lake"\n    style "photorealistic"\n    negative "text"\n    size "1024x1024"\n'
         '    not confident\n        give back none\nai.create: done\n', ai)
    kind, kw = ai.calls[0]
    check("image -> generate_image", kind, "image")
    check("image style", kw.get("style"), "photorealistic")
    check("image negative", kw.get("negative"), "text")
    check("image size", kw.get("size"), "1024x1024")

    ai = RecordAI()
    _run('ai.create promo returns video as c\n'
         '    goal "reveal"\n    style "cinematic"\n    duration 8\n'
         '    not confident\n        give back none\nai.create: done\n', ai)
    kind, kw = ai.calls[0]
    check("video -> generate_video", kind, "video")
    check("video duration", kw.get("duration"), 8.0)


def test_alias_and_fallback():
    print("\n=== Layer 2: alias binding + fallback ===")

    class OkAI:
        def generate_text(self, **kw): return "BOUND-VALUE"

    out = _run('ai.create h returns text as r\n    goal "x"\n'
               '    not confident\n        give back none\nai.create: done\n'
               'show {{r}}\n', OkAI(), verbose=True)
    check("alias bound + readable", "BOUND-VALUE" in out, True)

    class FailAI:
        def generate_text(self, **kw): raise RuntimeError("provider down")

    out2 = _run('ai.create h returns text as r\n    goal "x"\n'
                '    not confident\n        show "FELL_BACK"\nai.create: done\n',
                FailAI(), verbose=True)
    check("not-confident runs on failure", "FELL_BACK" in out2, True)


def test_live_generation():
    global _skipped
    print("\n=== Layer 3: live generation (skipped without keys) ===")

    try:
        from mohio_ai import AnthropicAiRuntime
    except Exception as e:
        print(f"  [SKIP] runtime import failed: {e}")
        _skipped += 3
        return
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        print("  [SKIP] no ANTHROPIC_API_KEY — runtime needs it to start; skipping live layer")
        _skipped += 3
        return
    rt = AnthropicAiRuntime()

    # TEXT (any text provider key)
    try:
        out = rt.generate_text(goal="Reply with exactly: ok", temperature=0)
        ok = bool(out and str(out).strip())
        print(f"  [{'PASS' if ok else 'FAIL'}] text live -> {str(out)[:40]!r}")
        globals()['_passed'] += ok; globals()['_failed'] += (not ok)
    except Exception as e:
        print(f"  [FAIL] text live: {type(e).__name__}: {e}")
        globals()['_failed'] += 1

    # IMAGE (needs OpenAI or Google)
    if os.environ.get("OPENAI_API_KEY", "").strip() or os.environ.get("GEMINI_API_KEY", "").strip():
        try:
            url = rt.generate_image(goal="a single red circle on white", size="1024x1024")
            ok = bool(url)
            print(f"  [{'PASS' if ok else 'FAIL'}] image live -> {str(url)[:50]!r}")
            globals()['_passed'] += ok; globals()['_failed'] += (not ok)
        except Exception as e:
            print(f"  [FAIL] image live: {type(e).__name__}: {e}")
            globals()['_failed'] += 1
    else:
        print("  [SKIP] image — no OPENAI_API_KEY / GEMINI_API_KEY")
        _skipped += 1

    # VIDEO (async — long; only if explicitly enabled to avoid long CI)
    if os.environ.get("MOHIO_TEST_VIDEO", "").strip() and os.environ.get("OPENAI_API_KEY", "").strip():
        try:
            url = rt.generate_video(goal="a slow zoom on a glass bottle", duration=4)
            ok = bool(url)
            print(f"  [{'PASS' if ok else 'FAIL'}] video live -> {str(url)[:50]!r}")
            globals()['_passed'] += ok; globals()['_failed'] += (not ok)
        except Exception as e:
            print(f"  [FAIL] video live: {type(e).__name__}: {e}")
            globals()['_failed'] += 1
    else:
        print("  [SKIP] video — set MOHIO_TEST_VIDEO=1 + OPENAI_API_KEY to run (slow)")
        _skipped += 1


if __name__ == "__main__":
    test_dispatch_and_attributes()
    test_alias_and_fallback()
    test_live_generation()
    print(f"\nRESULTS: {_passed} passed, {_failed} failed, {_skipped} skipped")
    sys.exit(1 if _failed else 0)
