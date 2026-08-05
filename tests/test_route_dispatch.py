# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
test_route_dispatch.py — endpoints must route by PATH.

Regression guard for the bug where the listen dispatcher matched only by
method (and optional shape), so every GET hit the first `request for`
endpoint regardless of its `at /path` — making every page after the first
unreachable.

Covers: path routing for GET and POST, same-shape disambiguation by path,
single-endpoint fallback, trailing-slash / query-string tolerance, and a
clean 404 for an unmatched route (never a silently-wrong page or a None).

Run: python3 tests/test_route_dispatch.py
"""
import os, sys, re
sys.argv = ['mio.py']
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')

from types import SimpleNamespace
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter

_passed = _failed = 0
def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1; print(f"  ok   {label}")
    else:
        _failed += 1; print(f"  FAIL {label}: got {got!r} want {want!r}")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_raw = open(os.path.join(ROOT, 'mohio.lark'), encoding='utf-8').read()
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

class MockAI:
    def register_chain(self, *a, **k): pass
    def decide(self, **k):
        return SimpleNamespace(result=None, confidence=0.9, fell_back=False, model='mock')

def _tag(resp):
    m = re.search(r'\[(.*?)\]', str(resp.get('body', '')) if isinstance(resp, dict) else '')
    return m.group(1) if m else None

def _run(src, req):
    prog = transform(_P.parse(src), src)
    it = MohioInterpreter(ai=MockAI())
    return it.run(prog, req)


# ── two GET endpoints, UNIQUE shapes -> route by path ─────────
print("GET routing by path (unique shapes)")
UNIQ = ('shape Home\n    method GET\nshape: done\nshape About\n    method GET\nshape: done\n'
        'listen for\n'
        '    request for sh.Home at /home\n        render\n            <p>[HOME]</p>\n        render: done\n    request: done\n'
        '    request for sh.About at /about\n        render\n            <p>[ABOUT]</p>\n        render: done\n    request: done\n'
        'listen: done\n')
check("/home -> HOME", _tag(_run(UNIQ, {'_method': 'GET', '_path': '/home'})), 'HOME')
check("/about -> ABOUT", _tag(_run(UNIQ, {'_method': 'GET', '_path': '/about'})), 'ABOUT')


# ── two GET endpoints, SAME shape -> still route by path ──────
print("GET routing by path (shared shape)")
SAME = ('shape Page\n    method GET\nshape: done\n'
        'listen for\n'
        '    request for sh.Page at /home\n        render\n            <p>[HOME]</p>\n        render: done\n    request: done\n'
        '    request for sh.Page at /about\n        render\n            <p>[ABOUT]</p>\n        render: done\n    request: done\n'
        'listen: done\n')
check("/home -> HOME", _tag(_run(SAME, {'_method': 'GET', '_path': '/home'})), 'HOME')
check("/about -> ABOUT", _tag(_run(SAME, {'_method': 'GET', '_path': '/about'})), 'ABOUT')
check("/about/ (trailing slash) -> ABOUT", _tag(_run(SAME, {'_method': 'GET', '_path': '/about/'})), 'ABOUT')
check("/about?x=1 (query string) -> ABOUT", _tag(_run(SAME, {'_method': 'GET', '_path': '/about?x=1'})), 'ABOUT')

# unmatched route -> clean 404, not a wrong page or None
_r = _run(SAME, {'_method': 'GET', '_path': '/nope'})
check("unknown route -> 404", _r.get('status') if isinstance(_r, dict) else None, 404)


# ── single GET endpoint -> fallback works even without a path ─
print("single-endpoint fallback")
ONE = ('shape Page\n    method GET\nshape: done\n'
       'listen for\n    request for sh.Page at /home\n        render\n            <p>[ONLY]</p>\n        render: done\n    request: done\nlisten: done\n')
check("single route, exact path -> ONLY", _tag(_run(ONE, {'_method': 'GET', '_path': '/home'})), 'ONLY')
check("single route, no path pinned -> ONLY", _tag(_run(ONE, {'_method': 'GET'})), 'ONLY')


# ── POST endpoints route by path too ─────────────────────────
print("POST routing by path")
POSTS = ('shape A\nshape: done\nshape B\nshape: done\n'
         'listen for\n'
         '    new sh.A at /alpha\n        give back 201 "[ALPHA]"\n    new: done\n'
         '    new sh.B at /beta\n        give back 201 "[BETA]"\n    new: done\n'
         'listen: done\n')
def _post_tag(resp):
    m = re.search(r'\[(.*?)\]', str(resp.get('body', '')) if isinstance(resp, dict) else '')
    return m.group(1) if m else None
check("POST /alpha -> ALPHA", _post_tag(_run(POSTS, {'_method': 'POST', '_path': '/alpha', 'a': {}})), 'ALPHA')
check("POST /beta -> BETA", _post_tag(_run(POSTS, {'_method': 'POST', '_path': '/beta', 'b': {}})), 'BETA')

print(f"\nRESULTS: {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
