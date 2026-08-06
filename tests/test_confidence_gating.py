# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""Runtime confidence gating (patent P1 'confidence threshold' + not_confident).
Proves the visible behavior: when an ai.decide falls back (confidence below
threshold), the `not confident` block fires and its give-back is the response;
when it does not fall back, the main path runs with the result. Hand to testing chat.
"""
import os, types
os.environ.setdefault('DATABASE_URL', ':memory:')
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter
import mohio_data

_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

class GatedAI:
    """Mock that reports a confidence and whether it fell back, like the runtime."""
    def __init__(self, result, fell_back): self._r, self._fb = result, fell_back
    def register_chain(self, *a, **k): pass
    def decide(self, **kwargs):
        return types.SimpleNamespace(result=self._r, confidence=(0.40 if self._fb else 0.97),
                                     fell_back=self._fb, model='mock')

DEFINE = ('ai.decide resolve_noun returns text\n'
          '    model "claude-sonnet-4-6"\n'
          '    goal "resolve the typed word"\n'
          '    confidence above 0.85\n'
          '    weigh noun, candidates\n'
          '    not confident\n'
          '        give back 200 "FELLBACK"\n'
          'ai.decide: done\n')
APP = (DEFINE +
       'shape Cmd\nshape: done\n'
       'listen for\n    new sh.Cmd at /cmd\n'
       '        hold noun = "key"\n'
       '        hold candidates = "brass key, lamp"\n'
       '        ai.decide resolve_noun\n'
       '        give back 200 ("you mean the " & resolve_noun)\n'
       '    new: done\nlisten: done\n')
_prog = transform(_P.parse(APP), APP)

def _run(fell_back, result):
    it = MohioInterpreter(ai=GatedAI(result, fell_back))
    return it.run(_prog, {'_method': 'POST', '_path': '/cmd', 'cmd': {}})

def test_below_threshold_fires_not_confident():
    resp = _run(fell_back=True, result="brass key")
    assert "FELLBACK" in str(resp['body']), f"not_confident should fire: {resp}"

def test_above_threshold_runs_main_path():
    resp = _run(fell_back=False, result="brass key")
    assert "brass key" in str(resp['body']) and "FELLBACK" not in str(resp['body']), f"main path should run: {resp}"

if __name__ == '__main__':
    p = f = 0
    for fn in (test_below_threshold_fires_not_confident, test_above_threshold_runs_main_path):
        try: fn(); print(f"  PASS  {fn.__name__}"); p += 1
        except AssertionError as e: print(f"  FAIL  {fn.__name__}: {e}"); f += 1
    print(f"RESULTS: {p} passed, {f} failed")
    import sys; sys.exit(0 if f == 0 else 1)
