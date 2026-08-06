# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""Hosting hardening: this runtime serves OTHER PEOPLE'S apps on shared infrastructure.

Everything here was a real finding in the 2026-07-18 hosting-readiness review. The server was
written for a single trusted app; these are the guarantees it needs before a public tenant.

  C1  no unauthenticated session listing / clearing, and a session id in a REQUEST HEADER is not
      accepted as identity unless the deployment opts in (it was attacker-suppliable: harvest an
      id from /mio/sessions, replay it in X-Session-ID, become that user)
  C2  static serving is rooted at the APP's directory -- never the compiler's, which handed out
      mohio_server.py / mohio.lark over HTTP, and meant the app's own assets were never searched
  C3  a resolved path must still be inside its root (a symlink in a tenant repo pointed anywhere)
  C4  source, config, data and dotfiles are never served (`.mho`, `.py`, `.env`, `.db`, `.git/`)
  H1  static is revalidated, not cached for a day (a redeploy must be visible immediately)
  H6  no wildcard CORS by default on a multi-tenant runtime
"""
import os, sys, pathlib, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')
os.environ.pop('MOHIO_CORS_ORIGINS', None)
os.environ.pop('MOHIO_ALLOW_SESSION_HEADER', None)

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter
import mohio_server
from mohio_server import create_app, MohioServer
from starlette.testclient import TestClient

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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


# ── build a realistic tenant directory ────────────────────────────────────────────────
tenant = tempfile.mkdtemp(prefix="tenant_")
APP = ('shape H\n    method GET\nshape: done\n'
       'listen for\n    request for sh.H at /hi\n        give back 200 "tenant"\n'
       '    request: done\nlisten: done\n')
open(os.path.join(tenant, 'app.mho'), 'w', encoding='utf-8').write(APP)
open(os.path.join(tenant, 'style.css'), 'w', encoding='utf-8').write('body{color:red}')
open(os.path.join(tenant, '.env'), 'w', encoding='utf-8').write('SECRET=abc')
open(os.path.join(tenant, 'helper.py'), 'w', encoding='utf-8').write('print(1)')
open(os.path.join(tenant, 'data.sqlite'), 'w', encoding='utf-8').write('x')
try:
    os.symlink('/etc/passwd', os.path.join(tenant, 'link.txt'))
    _symlink = True
except Exception:
    _symlink = False

prog = transform(P.parse(APP), APP)
it = MohioInterpreter(); it.run_declarations(prog)
app = create_app(MohioServer(prog, it, app_dir=tenant))
c = TestClient(app)
paths = [r.path for r in app.routes]

# ── C1 ────────────────────────────────────────────────────────────────────────────────
check("C1 no /mio/sessions route is mounted", '/mio/sessions' not in paths)
check("C1 no /mio/sessions/{id} route is mounted",
      not any('sessions' in p for p in paths))
check("C1 GET /mio/sessions does not succeed", c.get('/mio/sessions').status_code != 200)
check("C1 session-id header is opt-in (off by default)",
      getattr(mohio_server, '_ALLOW_SESSION_HEADER', True) is False)

# ── C2 ────────────────────────────────────────────────────────────────────────────────
check("C2 the app's own stylesheet is served", c.get('/style.css').status_code == 200)
for src_file in ('mohio_server.py', 'mohio_interpreter.py', 'mohio.lark'):
    check(f"C2 compiler source /{src_file} is not served",
          c.get('/' + src_file).status_code != 200)

# ── C3 ────────────────────────────────────────────────────────────────────────────────
if _symlink:
    check("C3 a symlink escaping the static root is refused",
          c.get('/link.txt').status_code != 200)
else:
    check("C3 (symlinks unavailable on this platform -- skipped)", True)

# ── C4 ────────────────────────────────────────────────────────────────────────────────
for blocked in ('/app.mho', '/helper.py', '/.env', '/data.sqlite'):
    check(f"C4 {blocked} is never served as static", c.get(blocked).status_code != 200)

# ── H1 ────────────────────────────────────────────────────────────────────────────────
r = c.get('/style.css')
etag = r.headers.get('etag')
cache = r.headers.get('cache-control', '')
check("H1 static carries an ETag", bool(etag))
check("H1 static is revalidated, not cached for a day",
      '86400' not in cache, f"cache-control={cache!r}")
if etag:
    check("H1 a matching If-None-Match returns 304",
          c.get('/style.css', headers={'If-None-Match': etag}).status_code == 304)

# ── H6 ────────────────────────────────────────────────────────────────────────────────
check("H6 no wildcard CORS by default",
      c.get('/hi').headers.get('access-control-allow-origin') != '*')

# ── the app still works ───────────────────────────────────────────────────────────────
check("the app's route still serves", c.get('/hi').status_code == 200)
check("HEAD is supported", c.head('/hi').status_code == 200)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
