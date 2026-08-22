# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
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
import mohio_data

_passed = 0
_failed = 0
_skipped = 0

_raw = mohio_data.GRAMMAR_PATH.read_text(encoding="utf-8")
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


def test_image_missing_handle_fails_loud():
    """T1-SILENT-SWEEP-BATCH6-10 (2026-08-15): a successful API response carrying neither
    'url'/'b64_json' (OpenAI) nor 'bytesBase64Encoded'/'image' (Google) used to return None,
    silently indistinguishable from a real image handle. Verified offline via a mocked HTTP
    layer -- no real network call, no real API key needed; only the response-parsing logic
    (not provider behavior) is under test."""
    global _passed, _failed
    print("\n=== Layer 1b: image generation with a response missing url/b64_json fails loud ===")
    import json as _json
    import urllib.request
    from mohio_ai import AnthropicAiRuntime

    class _FakeResponse:
        def __init__(self, payload):
            self._payload = _json.dumps(payload).encode()
        def read(self):
            return self._payload
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def _fake_urlopen(payload):
        def _f(req, timeout=None):
            return _FakeResponse(payload)
        return _f

    rt = AnthropicAiRuntime.__new__(AnthropicAiRuntime)
    rt._tick = lambda: None
    orig_urlopen = urllib.request.urlopen
    os.environ.setdefault('OPENAI_API_KEY', 'fake-key-offline-mock-only')
    os.environ.setdefault('GEMINI_API_KEY', 'fake-key-offline-mock-only')
    try:
        urllib.request.urlopen = _fake_urlopen({"data": [{"revised_prompt": "x"}]})
        try:
            rt._image_openai("dall-e-3", "x", "1024x1024", 1)
            print("  [FAIL] OpenAI: no exception on a response missing url/b64_json")
            _failed += 1
        except RuntimeError as e:
            ok = 'neither' in str(e).lower()
            print(f"  [{'PASS' if ok else 'FAIL'}] OpenAI raises RuntimeError on missing handle")
            _passed += ok; _failed += (not ok)

        urllib.request.urlopen = _fake_urlopen({"data": [{"url": "https://x/y.png"}]})
        r = rt._image_openai("dall-e-3", "x", "1024x1024", 1)
        ok = r == "https://x/y.png"
        print(f"  [{'PASS' if ok else 'FAIL'}] OpenAI regression: a real url is unaffected")
        _passed += ok; _failed += (not ok)

        urllib.request.urlopen = _fake_urlopen({"predictions": [{"safetyAttributes": {}}]})
        try:
            rt._image_google("imagen-3.0-generate", "x")
            print("  [FAIL] Google: no exception on a response missing both fields")
            _failed += 1
        except RuntimeError as e:
            ok = 'neither' in str(e).lower()
            print(f"  [{'PASS' if ok else 'FAIL'}] Google raises RuntimeError on missing handle")
            _passed += ok; _failed += (not ok)

        urllib.request.urlopen = _fake_urlopen({"predictions": [{"bytesBase64Encoded": "AAAA"}]})
        r = rt._image_google("imagen-3.0-generate", "x")
        ok = r == "AAAA"
        print(f"  [{'PASS' if ok else 'FAIL'}] Google regression: real bytesBase64Encoded unaffected")
        _passed += ok; _failed += (not ok)
    finally:
        urllib.request.urlopen = orig_urlopen


def test_video_timeout_and_missing_handle_fails_loud():
    """T1-SILENT-SWEEP-BATCH6-10 (2026-08-15): a polling loop that never sees
    completed/succeeded (OpenAI) or done=True (Google) used to fall through with no timeout
    check, then `return job.get("url") or job_id` / `... or op_name` silently returned the
    BARE JOB ID / OPERATION NAME as if it were a finished video. Verified offline via a
    mocked HTTP layer + a stubbed time.sleep (no real 10-minute wait, no real API key)."""
    global _passed, _failed
    print("\n=== Layer 1c: video polling timeout / missing-handle fails loud ===")
    import json as _json
    import time as _time
    import urllib.request
    from mohio_ai import AnthropicAiRuntime

    class _FakeResponse:
        def __init__(self, payload):
            self._payload = _json.dumps(payload).encode()
        def read(self):
            return self._payload
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def _never_finishes(create_payload, poll_payload):
        calls = {'n': 0}
        def _f(req, timeout=None):
            calls['n'] += 1
            return _FakeResponse(create_payload if calls['n'] == 1 else poll_payload)
        return _f

    rt = AnthropicAiRuntime.__new__(AnthropicAiRuntime)
    rt._tick = lambda: None
    orig_urlopen = urllib.request.urlopen
    orig_sleep = _time.sleep
    os.environ.setdefault('OPENAI_API_KEY', 'fake-key-offline-mock-only')
    os.environ.setdefault('GEMINI_API_KEY', 'fake-key-offline-mock-only')
    _time.sleep = lambda *a, **k: None
    try:
        urllib.request.urlopen = _never_finishes(
            {"id": "job-123", "status": "queued"}, {"id": "job-123", "status": "processing"})
        try:
            rt._video_openai("sora-2", "x", 4, None)
            print("  [FAIL] OpenAI: no exception when the job never completes")
            _failed += 1
        except RuntimeError as e:
            ok = 'did not complete' in str(e)
            print(f"  [{'PASS' if ok else 'FAIL'}] OpenAI raises on poll timeout")
            _passed += ok; _failed += (not ok)

        urllib.request.urlopen = _never_finishes(
            {"id": "job-456", "status": "queued"},
            {"id": "job-456", "status": "completed", "url": "https://x/y.mp4"})
        r = rt._video_openai("sora-2", "x", 4, None)
        ok = r == "https://x/y.mp4"
        print(f"  [{'PASS' if ok else 'FAIL'}] OpenAI regression: a real completed url is unaffected")
        _passed += ok; _failed += (not ok)

        urllib.request.urlopen = _never_finishes(
            {"name": "op-789", "done": False}, {"name": "op-789", "done": False})
        try:
            rt._video_google("veo-3.0-generate", "x", 4)
            print("  [FAIL] Google: no exception when the operation never completes")
            _failed += 1
        except RuntimeError as e:
            ok = 'did not complete' in str(e)
            print(f"  [{'PASS' if ok else 'FAIL'}] Google raises on poll timeout")
            _passed += ok; _failed += (not ok)

        urllib.request.urlopen = _never_finishes(
            {"name": "op-xyz", "done": False}, {"name": "op-xyz", "done": True, "response": {}})
        try:
            rt._video_google("veo-3.0-generate", "x", 4)
            print("  [FAIL] Google: no exception when done=True but no uri is present")
            _failed += 1
        except RuntimeError as e:
            ok = 'no video was actually returned' in str(e)
            print(f"  [{'PASS' if ok else 'FAIL'}] Google raises when done but unusable payload")
            _passed += ok; _failed += (not ok)

        urllib.request.urlopen = _never_finishes(
            {"name": "op-abc", "done": False},
            {"name": "op-abc", "done": True,
             "response": {"generatedVideos": [{"video": {"uri": "https://x/y2.mp4"}}]}})
        r = rt._video_google("veo-3.0-generate", "x", 4)
        ok = r == "https://x/y2.mp4"
        print(f"  [{'PASS' if ok else 'FAIL'}] Google regression: a real completed uri is unaffected")
        _passed += ok; _failed += (not ok)
    finally:
        urllib.request.urlopen = orig_urlopen
        _time.sleep = orig_sleep


def test_tts_explicit_model_wins():
    """T1-SILENT-SWEEP-BATCH6-10 (2026-08-15): an explicit TTS model override that didn't
    start with the literal string 'tts' used to be silently replaced with 'tts-1' --
    contradicts the file's own 'an explicit override wins outright' principle. Verified
    offline by inspecting the actual outbound request payload via a mocked HTTP layer."""
    global _passed, _failed
    print("\n=== Layer 1d: TTS explicit model override is respected, not silently replaced ===")
    import json as _json
    import urllib.request
    from mohio_ai import AnthropicAiRuntime

    captured = {}

    class _FakeResponse:
        def read(self):
            return b"FAKE-MP3-BYTES"
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def _fake_urlopen(req, timeout=None):
        captured['payload'] = _json.loads(req.data.decode())
        return _FakeResponse()

    rt = AnthropicAiRuntime.__new__(AnthropicAiRuntime)
    orig_urlopen = urllib.request.urlopen
    os.environ.setdefault('OPENAI_API_KEY', 'fake-key-offline-mock-only')
    urllib.request.urlopen = _fake_urlopen
    try:
        rt._audio_openai("hello", "alloy", "gpt-4o-mini-tts")
        ok = captured['payload']['model'] == "gpt-4o-mini-tts"
        print(f"  [{'PASS' if ok else 'FAIL'}] non-'tts'-prefixed explicit model is preserved")
        _passed += ok; _failed += (not ok)

        rt._audio_openai("hello", "alloy", "tts-1-hd")
        ok = captured['payload']['model'] == "tts-1-hd"
        print(f"  [{'PASS' if ok else 'FAIL'}] regression: 'tts'-prefixed model still works")
        _passed += ok; _failed += (not ok)
    finally:
        urllib.request.urlopen = orig_urlopen


if __name__ == "__main__":
    test_dispatch_and_attributes()
    test_alias_and_fallback()
    test_image_missing_handle_fails_loud()
    test_video_timeout_and_missing_handle_fails_loud()
    test_tts_explicit_model_wins()
    test_live_generation()
    print(f"\nRESULTS: {_passed} passed, {_failed} failed, {_skipped} skipped")
    sys.exit(1 if _failed else 0)
