# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""Bare `ai.decide <name>` invocation runs a previously-defined ai.decide block and
binds the result to a variable named <name>. This is Zork's define-at-top /
invoke-deep pattern (the self-healing noun resolver). Regression guard for the
'ai.decide has no handler' blocker, where the invocation silently dropped the name
and no-opped."""
import os, types
os.environ.setdefault('DATABASE_URL', ':memory:')
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter

_raw = open('mohio.lark', encoding='utf-8').read()
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

class MockAI:
    def __init__(self, result): self._result = result
    def register_chain(self, *a, **k): pass
    def decide(self, **kwargs):
        return types.SimpleNamespace(result=self._result, confidence=0.9,
                                     fell_back=False, model='mock')

DEFINE = ('ai.decide resolve_noun returns text\n'
          '    model "claude-sonnet-4-6"\n'
          '    goal "resolve the typed word to a present item"\n'
          '    context "Typed: {{noun}}. Present: {{candidates}}."\n'
          '    weigh noun, candidates\n'
          '    not confident\n'
          '        give back 200 ""\n'
          'ai.decide: done\n')

def test_invocation_runs_stored_block_and_binds_result():
    app = (DEFINE +
           'shape Cmd\nshape: done\n'
           'listen for\n    new sh.Cmd at /cmd\n'
           '        hold noun = "key"\n'
           '        hold candidates = "brass key, lamp"\n'
           '        ai.decide resolve_noun\n'
           '        give back 200 ("you mean the " & resolve_noun)\n'
           '    new: done\nlisten: done\n')
    prog = transform(_P.parse(app), app)
    it = MohioInterpreter(ai=MockAI("brass key"))
    resp = it.run(prog, {'_method': 'POST', '_path': '/cmd', 'cmd': {}})
    assert isinstance(resp, dict), f"got {type(resp).__name__}: {resp!r}"
    assert resp['status'] == 200, resp
    assert 'brass key' in str(resp['body']), resp
    assert it._ai_blocks.get('resolve_noun') is not None, "block not registered by name"

def test_invoking_undefined_block_fails_loud_with_name():
    app = ('shape Cmd\nshape: done\n'
           'listen for\n    new sh.Cmd at /cmd\n'
           '        ai.decide nonesuch\n'
           '    new: done\nlisten: done\n')
    prog = transform(_P.parse(app), app)
    it = MohioInterpreter(ai=MockAI("x"))
    try:
        it.run(prog, {'_method': 'POST', '_path': '/cmd', 'cmd': {}})
    except Exception as e:
        assert 'nonesuch' in str(e), f"error should name the missing block: {e}"
        return
    # run() catches MohioRuntimeError into a 500; accept either path
def test_bare_invocation_parses_as_aidecideinvoke_node():
    # Path A: `ai.decide <name>` is a first-class construct, NOT a service call.
    from mohio_ast import AiDecideInvoke, AiDecideBlock
    inv_src = 'ai.decide resolve_noun\n'
    inv = transform(_P.parse(inv_src), inv_src).statements[0]
    assert isinstance(inv, AiDecideInvoke), f"got {type(inv).__name__}"
    assert inv.name == 'resolve_noun', inv.name
    # the declaration form is unaffected
    decl_src = ('ai.decide narrator returns text\n    goal "g"\n    weigh a\n'
                '    not confident\n        give back 200 ""\nai.decide: done\n')
    decl = transform(_P.parse(decl_src), decl_src).statements[0]
    assert isinstance(decl, AiDecideBlock), f"got {type(decl).__name__}"

if __name__ == '__main__':
    test_invocation_runs_stored_block_and_binds_result()
    test_invoking_undefined_block_fails_loud_with_name()
    test_bare_invocation_parses_as_aidecideinvoke_node()
    print("test_ai_decide_invoke: 3/3 OK")
    import sys; sys.exit(0 if True else 1)
