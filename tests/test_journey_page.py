# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
test_journey_page.py — the journey / page multi-page model, wired end to end.

Guards the build that turned `journey` and `page` from raw-Tree no-ops (stateless
run() failed loud; run_with_session() silently returned None) into a real model:

  - journey = app root scope + routing container; pages inherit its declarations.
  - page = one GET route; body ends in `render` (HTML) or `give back` (data).
  - path routing mirrors _exec_ListenBlock: exact match, single-page fallback,
    clean 404 (never a silently-wrong page, never a bare None).
  - journey + nested `listen for` (POST) coexist with GET pages (continue-on-404).
  - implicit default journey: top-level bare pages route the same way.
  - parity across BOTH run() (stateless) and run_with_session() (stateful).

Run: python3 tests/test_journey_page.py
"""
import os, sys, re
sys.argv = ['mio.py']
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')

from types import SimpleNamespace
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter, _InMemorySessionStore
from mohio_ast import JourneyDecl, JourneyMeta, ListenBlock

_passed = _failed = 0
def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1; print(f"  ok   {label}")
    else:
        _failed += 1; print(f"  FAIL {label}: got {got!r} want {want!r}")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

class MockAI:
    def register_chain(self, *a, **k): pass
    def decide(self, **k):
        return SimpleNamespace(result=None, confidence=0.9, fell_back=False, model='mock')

def _status(resp):
    return resp.get('status') if isinstance(resp, dict) else None

def _tag(resp):
    body = resp.get('body') if isinstance(resp, dict) else resp
    m = re.search(r'\[(.*?)\]', str(body))
    return m.group(1) if m else None

def _run(src, req, seed=None):
    prog = transform(_P.parse(src), src)
    it = MohioInterpreter(ai=MockAI())
    if seed is not None:
        it.setup_test_db(seed_data=seed)
    return it.run(prog, req)

def _run_session(src, req, seed=None):
    prog = transform(_P.parse(src), src)
    it = MohioInterpreter(ai=MockAI())
    if seed is not None:
        it.setup_test_db(seed_data=seed)
    return it.run_with_session(prog, req, 's1', _InMemorySessionStore())


# ── 0. transformer builds real nodes (not raw Trees) ──────────
print("transformer builds JourneyDecl / ListenBlock / JourneyMeta")
J = ('shape Q\n    q as text\nshape: done\njourney RatesApp\n    public: /home, /about\n    hold site = "Mohio"\n    listen for\n        request for sh.Q at /home\n                render\n                    <p>[HOME]</p>\n                render: done\n        request: done\n    listen: done\n    listen for\n        request for sh.Q at /about\n                render\n                    <p>[ABOUT]</p>\n                render: done\n        request: done\n    listen: done\njourney: done\n')
prog = transform(_P.parse(J), J)
# The routed source declares its request shape first, so the journey is not statement 0.
j = next(s for s in prog.statements if isinstance(s, JourneyDecl))
check("top-level is JourneyDecl", isinstance(j, JourneyDecl), True)
check("journey name", j.name, 'RatesApp')
check("two routed units built", sum(isinstance(b, ListenBlock) for b in j.body), 2)
check("path metadata carried (not raw Tree)",
      any(isinstance(b, JourneyMeta) for b in j.body), True)
check("no raw Lark Trees in journey body",
      any(type(b).__name__ == 'Tree' for b in j.body), False)


# ── 1. journey page routing — stateless run() ─────────────────
print("journey page routing (stateless run)")
check("/home -> 200 HOME", (_status(_run(J, {'_method':'GET','_path':'/home'})),
                            _tag(_run(J, {'_method':'GET','_path':'/home'}))), (200, 'HOME'))
check("/about -> 200 ABOUT", _tag(_run(J, {'_method':'GET','_path':'/about'})), 'ABOUT')
check("/missing -> 404", _status(_run(J, {'_method':'GET','_path':'/missing'})), 404)
check("trailing slash tolerated", _tag(_run(J, {'_method':'GET','_path':'/home/'})), 'HOME')
check("query string tolerated", _tag(_run(J, {'_method':'GET','_path':'/home?x=1'})), 'HOME')


# ── 2. journey page routing — stateful run_with_session() ─────
print("journey page routing (stateful session)")
check("/home -> 200 HOME", _tag(_run_session(J, {'_method':'GET','_path':'/home'})), 'HOME')
check("/about -> 200 ABOUT", _tag(_run_session(J, {'_method':'GET','_path':'/about'})), 'ABOUT')
check("/missing -> 404", _status(_run_session(J, {'_method':'GET','_path':'/missing'})), 404)


# ── 3. scope inheritance — page reads a journey hold ──────────
print("scope inheritance (page reads journey declaration)")
SCOPE = ('shape Q\n    q as text\nshape: done\njourney App\n    hold greeting = "WELCOME"\n    listen for\n        request for sh.Q at /home\n                render\n                    <p>[{{ greeting }}]</p>\n                render: done\n        request: done\n    listen: done\njourney: done\n')
check("page sees journey hold (run)", _tag(_run(SCOPE, {'_method':'GET','_path':'/home'})), 'WELCOME')
check("page sees journey hold (session)", _tag(_run_session(SCOPE, {'_method':'GET','_path':'/home'})), 'WELCOME')


# ── 4. journey connect inherited — page does a DB find ────────
print("scope inheritance (page uses journey connect)")
DB = ('shape Q\n    q as text\nshape: done\nconnect db as sqlite from env.DATABASE_URL\njourney App\n    listen for\n        request for sh.Q at /list\n                find w in db.widgets\n                find: done\n                render\n                    <p>[GOT {{ w.count }}]</p>\n                render: done\n        request: done\n    listen: done\njourney: done\n')
seed = {'widgets': [{'id':1,'name':'a'},{'id':2,'name':'b'},{'id':3,'name':'c'}]}
check("page find over journey db -> 3 rows", _tag(_run(DB, {'_method':'GET','_path':'/list'}, seed=seed)), 'GOT 3')


# ── 5. page give back (data, not render) ──────────────────────
print("page give back (data endpoint)")
GB = ('shape Q\n    q as text\nshape: done\njourney App\n    listen for\n        request for sh.Q at /api\n                give back ok "[DATA_OK]"\n        request: done\n    listen: done\njourney: done\n')
check("/api give back", (_status(_run(GB, {'_method':'GET','_path':'/api'})),
                         _tag(_run(GB, {'_method':'GET','_path':'/api'}))), (200, 'DATA_OK'))


# ── 6. nested listen — POST endpoint coexists with GET pages ──
print("nested listen for (POST) coexists with GET pages")
MIX = ('shape Q\n    q as text\nshape: done\njourney App\n    listen for\n        request for sh.Q at /home\n                render\n                    <p>[HOME]</p>\n                render: done\n        request: done\n    listen: done\n    listen for\n        new sh.Signup at /signup\n            give back created "[SIGNED_UP]"\n        new: done\n    listen: done\njourney: done\n')
check("GET /home (page)", _tag(_run_session(MIX, {'_method':'GET','_path':'/home'})), 'HOME')
check("POST /signup (nested listener)", (_status(_run_session(MIX, {'_method':'POST','_path':'/signup','_shape':'Signup'})),
                                         _tag(_run_session(MIX, {'_method':'POST','_path':'/signup','_shape':'Signup'}))), (201, 'SIGNED_UP'))
check("GET /missing -> 404 (page miss not shadowed by listener)",
      _status(_run_session(MIX, {'_method':'GET','_path':'/missing'})), 404)
check("POST /missing -> 404", _status(_run_session(MIX, {'_method':'POST','_path':'/missing','_shape':'Signup'})), 404)


# ── 7. implicit default journey — top-level bare pages ────────
print("implicit default journey (top-level bare pages)")
BARE = ('shape Q\n    q as text\nshape: done\nlisten for\n    request for sh.Q at /home\n        render\n            <p>[BARE_HOME]</p>\n        render: done\n    request: done\nlisten: done\nlisten for\n    request for sh.Q at /about\n        render\n            <p>[BARE_ABOUT]</p>\n        render: done\n    request: done\nlisten: done\n')
check("/home", _tag(_run(BARE, {'_method':'GET','_path':'/home'})), 'BARE_HOME')
check("/about", _tag(_run(BARE, {'_method':'GET','_path':'/about'})), 'BARE_ABOUT')
check("/nope -> 404 (no first-match shadow)", _status(_run(BARE, {'_method':'GET','_path':'/nope'})), 404)
check("bare pages route in session too", _tag(_run_session(BARE, {'_method':'GET','_path':'/about'})), 'BARE_ABOUT')


# ── 8. render produces a full HTML page response ──────────────
print("render serves text/html page shell")
r = _run(J, {'_method':'GET','_path':'/home'})
check("content_type text/html", r.get('content_type'), 'text/html')
check("body is an HTML document", str(r.get('body')).lstrip().startswith('<!DOCTYPE html>'), True)


print(f"\nRESULTS: {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
