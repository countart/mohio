# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
test_ai_create_from_source.py

Spec for `ai.create NAME from SOURCE` with free-form hints:
  1. generates from the source, folds the source + hints into the prompt, binds NAME
  2. on.failure fallback runs when the model fails
  3. a from-source ai.create without on.failure produces a (non-fatal) warning

Run: PYTHONPATH=$PWD DATABASE_URL=:memory: python3 tests/test_ai_create_from_source.py
"""
import os, sys, subprocess
os.environ.setdefault("DATABASE_URL", ":memory:")

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter
import mohio_data

_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = "\n".join(l for l in _raw.splitlines() if not l.strip().startswith("//"))
_P = Lark(_g, parser="earley", ambiguity="resolve", propagate_positions=True)


class _MockAI:
    def __init__(self, fail=False):
        self.fail = fail
        self.last_goal = ""

    def generate_text(self, goal="", **k):
        self.last_goal = goal
        if self.fail:
            raise RuntimeError("model unavailable")
        return "GENERATED"


def _run(src, ai=None):
    interp = MohioInterpreter()
    if ai:
        interp.ai = ai
    return interp, interp.run(transform(_P.parse(src), src))


def test_from_source_generates_binds_and_prompts():
    ai = _MockAI()
    _, res = _run(
        'hold report "Q4 revenue up 20 percent"\n'
        'ai.create summary from report\n'
        '    tone "executive"\n'
        '    length "brief"\n'
        'ai.create: done\n'
        'give back 200 summary\n', ai)
    assert res.get("body") == "GENERATED", res            # bound the result
    assert "Q4 revenue up 20 percent" in ai.last_goal, ai.last_goal   # source in prompt
    assert "tone=executive" in ai.last_goal, ai.last_goal            # hint in prompt
    assert "length=brief" in ai.last_goal, ai.last_goal


def test_on_failure_fallback_runs():
    ai = _MockAI(fail=True)
    _, res = _run(
        'hold report "x"\n'
        'ai.create summary from report\n'
        '    tone "brief"\n'
        '    on.failure\n'
        '        give back 503 "AI unavailable"\n'
        'ai.create: done\n'
        'give back 200 summary\n', ai)
    assert res.get("status") == 503, res                  # fallback fired on failure


def test_missing_on_failure_warns():
    env = dict(os.environ, PYTHONPATH=os.getcwd())
    src = ('hold report "x"\nai.create summary from report\n    tone "brief"\n'
           'ai.create: done\n')
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".mho", delete=False) as f:
        f.write(src); path = f.name
    try:
        r = subprocess.run([sys.executable, "mio.py", "check", path],
                           env=env, capture_output=True, text=True, timeout=120)
        out = (r.stdout + r.stderr).lower()
        assert "on.failure" in out and "warn" in out, out[-300:]
    finally:
        os.unlink(path)


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"  PASS {name}")
            except Exception as e:
                failed += 1; print(f"  FAIL {name}: {e}")
    print(f"\n{'ALL PASS' if not failed else str(failed) + ' FAILED'}")
    sys.exit(1 if failed else 0)
