# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""The general runtime knows nothing about any specific app.

After de-Zorking mohio_server.py, a tenant app served by the general runtime must carry NO
Zork-specific or schema-coupled surface:
  - no /game route (Zork-ism; the catch-all handles any path)
  - no admin/seed/reset/stats endpoints (DB management is a control-plane concern)
  - no bundled demo front end (an app with no index.html gets a neutral page, not Zork's HTML)
  - the session cookie name is a configurable default, not hardcoded to one app's choice

This locks the separation so demo scaffolding cannot creep back into the shared runtime that every
tenant machine runs.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter
import mohio_server
from mohio_server import create_app, MohioServer
from starlette.testclient import TestClient

_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)


def _app(src):
    prog = transform(P.parse(src), src)
    it = MohioInterpreter(); it.run_declarations(prog)
    return create_app(MohioServer(prog, it))


# a routed tenant app with no root route and no index.html
ROUTED = ('shape H\n    method GET\nshape: done\n'
          'listen for\n    request for sh.H at /hello\n'
          '        give back 200 "hello from tenant app"\n    request: done\nlisten: done\n')
app = _app(ROUTED)
paths = [r.path for r in app.routes]
c = TestClient(app)

# ── no Zork / admin routes are mounted ────────────────────────────────────────────────
for bad in ['/game', '/mio/seed', '/mio/stats', '/mio/reset-db', '/mio/admin', '/mio/cache/clear']:
    check(f"no `{bad}` route on the general runtime", bad not in paths)

# ── requesting the removed admin endpoints does not succeed ───────────────────────────
check("GET /mio/seed does not 200 (endpoint gone)", c.get('/mio/seed').status_code != 200)
check("GET /mio/reset-db does not 200 (endpoint gone)", c.get('/mio/reset-db').status_code != 200)

# ── generic admin (health/ping/sessions) still works ──────────────────────────────────
check("GET /health still works (generic)", c.get('/health').status_code == 200)
check("GET /ping still works (generic)", c.get('/ping').status_code == 200)

# ── the app's own routed page serves ──────────────────────────────────────────────────
r = c.get('/hello')
check("routed tenant page serves", r.status_code == 200 and "hello from tenant app" in r.text)

# ── no-index root -> neutral page, zero Zork ──────────────────────────────────────────
root = c.get('/')
check("no-index root returns a neutral page (200)", root.status_code == 200)
check("neutral page shows a generic message", "Your Mohio app is running" in root.text)
check("neutral page mentions nothing app-specific (no 'zork')",
      'zork' not in root.text.lower())

# ── the loader that hunted for zork_frontend.html is gone ──────────────────────────────
check("server no longer has _load_frontend_html (no demo-front-end hunting)",
      not hasattr(mohio_server, '_load_frontend_html'))

# ── session cookie name is configurable, defaulting to mio_session ────────────────────
check("session cookie name is a configurable constant (default mio_session)",
      getattr(mohio_server, '_SESSION_COOKIE', None) == 'mio_session')

# ── the app's home-page filename is configurable, defaulting to index.html ────────────
# This is how an app keeps its own front end without the server hardcoding any app's filename.
# (A demo whose front end is not called index.html sets MOHIO_INDEX_HTML in its own deployment.)
check("index resolution falls back to index.html then home.html",
      getattr(mohio_server, "_INDEX_CANDIDATES", None) == ("index.html", "home.html"))

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
