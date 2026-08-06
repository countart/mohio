# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""`miocookie.set` emits Set-Cookie on EVERY serving path, identically (2026-08-01 fix).

It worked on the `new sh.X` listener path but SILENTLY DROPPED the cookie on the
`request for sh.X at "/path"` route path: the handler runs in a child ctx, and `_attach_cookies`
reads the session ctx, so the pending cookie written by miocookie.set never reached the response.
`_exec_request_listener` now bubbles the child's pending cookies up to the session ctx. This
locks the header on BOTH paths so they never diverge again. Run: `python tests/test_miocookie_serving.py`.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter
from mohio_server import create_app, MohioServer
from starlette.testclient import TestClient

_RAW = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_G = '\n'.join(l for l in _RAW.splitlines() if not l.strip().startswith('//'))
_P = Lark(_G, parser='earley', ambiguity='resolve', propagate_positions=True)

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail: print(f"          {detail}")
    _p += bool(cond); _f += (not cond)

def serve(src, method, path, **kw):
    prog = transform(_P.parse(src), src)
    it = MohioInterpreter(); it.run_declarations(prog)
    client = TestClient(create_app(MohioServer(prog, it)))
    r = getattr(client, method)(path, **kw)
    set_cookie = [v.decode() for k, v in r.headers.raw if k.lower() == b'set-cookie']
    return r.status_code, dict(r.cookies), set_cookie

# ── `request for sh.X at "/path"` route -- the path that used to silently drop the cookie ──
REQ = ('shape Q\n    method GET\nshape: done\n'
       'listen for\n    request for sh.Q at /setc\n'
       '        miocookie.set "sid" to "abc123"\n'
       '        give back 200 "ok"\n'
       '    request: done\nlisten: done\n')
st, cookies, sc = serve(REQ, 'get', '/setc')
check("request-for route: 200 OK", st == 200, str(st))
check("request-for route: Set-Cookie present with sid=abc123",
      cookies.get('sid') == 'abc123', f"cookies={cookies} raw={sc}")
check("request-for route: cookie carries the secure defaults (HttpOnly)",
      any('HttpOnly' in v for v in sc), str(sc))

# ── `new sh.X` listener -- was already working, must NOT regress ──────────────────────────
NEW = ('shape Cmd\n    command as text\nshape: done\n'
       'listen for\n    new sh.Cmd\n'
       '        miocookie.set "sid" to "abc123"\n'
       '        give back 200 "ok"\n'
       '    new: done\nlisten: done\n')
st, cookies, sc = serve(NEW, 'post', '/', json={'command': 'x'})
check("new sh.X route: 200 OK", st == 200, str(st))
check("new sh.X route: Set-Cookie still present with sid=abc123 (no regression)",
      cookies.get('sid') == 'abc123', f"cookies={cookies} raw={sc}")

# ── Secure flag honors the server scheme default (auth sweep item 3, 2026-08-02) ───────────
# miocookie.set used to hardcode secure=False, which overrode the server default and left the
# session cookie non-Secure even on https. Now it omits secure so _secure_default applies:
# MOHIO_COOKIE_SECURE=1 forces it on regardless of the (http) test transport.
os.environ['MOHIO_COOKIE_SECURE'] = '1'
st, cookies, sc = serve(NEW, 'post', '/', json={'command': 'x'})
check("MOHIO_COOKIE_SECURE=1 -> Set-Cookie carries Secure (was hardcoded off before)",
      any('Secure' in v for v in sc), str(sc))
os.environ['MOHIO_COOKIE_SECURE'] = '0'
st, cookies, sc = serve(NEW, 'post', '/', json={'command': 'x'})
check("MOHIO_COOKIE_SECURE=0 -> no Secure (plain-http dev preserved)",
      not any('Secure' in v for v in sc), str(sc))
os.environ.pop('MOHIO_COOKIE_SECURE', None)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
