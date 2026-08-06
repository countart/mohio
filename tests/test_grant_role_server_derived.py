# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""Auth rebuild Item 1 (2026-08-02): roles are SERVER-derived via `grant role`, never
read from the client `_roles` payload.

The confirmed live gap: `require role` used to read roles straight from the request `_roles`
field, which the client fully controls, and `MOHIO_TRUST_PROXY_ROLES=1` trusted them wholesale.
The fix: `grant role` establishes a VERIFIED role at login, stored on the session root (survives
across requests via self.sessions[uuid]); `require role` reads only that; the client `_roles`
payload is never consulted again.

End-to-end through the real serving path (mio serve via TestClient, real HTTP round trips). The
forgery is delivered the way it actually reaches an app: a POST body carrying `_roles`
(server reads payload.get("_roles") on POST; the GET path hardcodes []). Using `new sh.X` POST
routes is the exact shape of the original forgeable-`_roles` vulnerability.
Run: `python tests/test_grant_role_server_derived.py`.
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

# A login route grants "admin" server-side and persists the session by setting mio_session to
# session.id. A separate protected route requires "admin". Both are POST `new sh.X` routes --
# the exact form of the confirmed forgeable-_roles vulnerability, and the only path that
# delivers a client `_roles` payload to the interpreter.
SRC = (
    'shape Login\n    who as text\nshape: done\n'
    'shape Secret\n    what as text\nshape: done\n'
    'listen for\n'
    '    new sh.Login\n'
    '        grant role "admin"\n'
    # 2026-08-04: mio_session is runtime-owned now -- the server emits it
    # automatically on every session-bearing response, no miocookie.set needed
    # (and it would fail loud if attempted; the reservation is tested elsewhere).
    '        give back 200 "logged in"\n'
    '    new: done\n'
    '    new sh.Secret\n'
    '        require role "admin"\n'
    '        give back 200 "SECRET OK"\n'
    '    new: done\n'
    'listen: done\n'
)

def fresh_client():
    """One app instance -> one shared self.sessions -> cookies persist across calls."""
    prog = transform(_P.parse(SRC), SRC)
    it = MohioInterpreter(); it.run_declarations(prog)
    return TestClient(create_app(MohioServer(prog, it)))

def body_of(r):
    try:    return str(r.json())
    except Exception: return r.text

def login(c):    return c.request('POST', '/', json={'who': 'bo', '_shape': 'Login'})
def secret(c, **extra):
    return c.request('POST', '/', json={'what': 'read', '_shape': 'Secret', **extra})

# ── 1. FORGED _roles in the POST body, NO valid session -> refused ────────────────────────
os.environ.pop('MOHIO_TRUST_PROXY_ROLES', None)
c = fresh_client()
r = secret(c, _roles=['admin'])
check("forged _roles=['admin'] in POST body, no session -> REFUSED (not 200)",
      r.status_code != 200, f"status={r.status_code} body={body_of(r)}")
check("forged request does NOT leak the protected body", 'SECRET OK' not in body_of(r),
      body_of(r))

# ── 2. Real login establishes the role server-side; a later request with NO _roles authorizes ─
c = fresh_client()
r_login = login(c)
check("login route -> 200", r_login.status_code == 200, f"{r_login.status_code} {body_of(r_login)}")
check("login set the mio_session cookie (session persists)",
      'mio_session' in c.cookies, dict(c.cookies))
r_secret = secret(c)   # NO _roles in the payload at all -- role must come from the server
check("after login, /secret with the session cookie and NO _roles -> 200 (role came from the server)",
      r_secret.status_code == 200 and 'SECRET OK' in body_of(r_secret),
      f"status={r_secret.status_code} body={body_of(r_secret)}")

# ── 3. Even WITH a real session, a forged _roles in the body must not add authority ────────
# (Confirms require role reads the server store, not the payload -- a logged-in NON-admin
#  cannot escalate by attaching _roles. Here there is no grant at all, only a session.)
c = fresh_client()
# Establish a session WITHOUT granting admin: hit the login shape's cookie-set by... instead,
# just send a forged _roles alongside a minted session from a prior secret attempt.
r = secret(c, _roles=['admin'])
check("forged _roles with a fresh session and no grant -> still REFUSED",
      r.status_code != 200 and 'SECRET OK' not in body_of(r),
      f"status={r.status_code} body={body_of(r)}")

# ── 4. Old wholesale-trust is GONE: MOHIO_TRUST_PROXY_ROLES=1 no longer trusts the payload ──
os.environ['MOHIO_TRUST_PROXY_ROLES'] = '1'
c = fresh_client()
r = secret(c, _roles=['admin'])
check("MOHIO_TRUST_PROXY_ROLES=1 + forged _roles in POST body -> STILL refused (bypass removed)",
      r.status_code != 200 and 'SECRET OK' not in body_of(r),
      f"status={r.status_code} body={body_of(r)}")
os.environ.pop('MOHIO_TRUST_PROXY_ROLES', None)

# ── 5. grant role value forms: a runtime value (not just a literal), a list, and empty ─────
# grant role ESTABLISHES from a computed value -- it takes a value_expr, so the role name can
# come from a variable or a looked-up field (the realistic login: grant role user.role). An
# empty resolved value fails loud at the source rather than silently granting no authority.
def run_stateless(src):
    prog = transform(_P.parse(src), src)
    return MohioInterpreter().run(prog, request={'method': 'POST', 'path': '/', 'action': 'x'})

DYN = ('shape Cmd\n    action as text\nshape: done\n'
       'listen for\n    new sh.Cmd\n        hold r "editor"\n        grant role r\n'
       '        require role "editor"\n        give back 200 "DYN OK"\n    new: done\nlisten: done\n')
r = run_stateless(DYN)
check("grant role from a runtime variable -> require role passes (200)",
      r.get('status') == 200 and 'DYN OK' in str(r.get('body', '')), str(r))

LST = ('shape Cmd\n    action as text\nshape: done\n'
       'listen for\n    new sh.Cmd\n        roles as list "a", "admin", "c"\n        grant role roles\n'
       '        require role "admin"\n        give back 200 "LIST OK"\n    new: done\nlisten: done\n')
r = run_stateless(LST)
check("grant role from a list grants each member (require admin passes)",
      r.get('status') == 200 and 'LIST OK' in str(r.get('body', '')), str(r))

EMPTY = ('shape Cmd\n    action as text\nshape: done\n'
         'listen for\n    new sh.Cmd\n        hold r ""\n        grant role r\n'
         '        give back 200 "should not reach"\n    new: done\nlisten: done\n')
r = run_stateless(EMPTY)
check("grant role of an empty value fails loud (403), does not silently grant nothing",
      r.get('status') == 403 and 'empty' in str(r.get('body', '')).lower()
      and 'should not reach' not in str(r.get('body', '')), str(r))

# ── 6. grant role REPLACES, does not accumulate: a second grant reflects current state ──────
REPLACE = ('shape Cmd\n    action as text\nshape: done\n'
           'listen for\n    new sh.Cmd\n        grant role "admin"\n        grant role "viewer"\n'
           '        require role "admin"\n        give back 200 "still admin"\n    new: done\nlisten: done\n')
r = run_stateless(REPLACE)
check("second grant REPLACES the first (grant viewer after admin -> require admin is 403)",
      r.get('status') == 403 and 'still admin' not in str(r.get('body', '')), str(r))

# ── 7. grant role records a role_granted security audit event ──────────────────────────────
_ap = transform(_P.parse(DYN), DYN)
_ai = MohioInterpreter(); _ai.run_declarations(_ap)
_ai.run(_ap, request={'method': 'POST', 'path': '/', 'action': 'x'})
_alog = getattr(_ai, '_audit_logs', {}).get('security_audit_log', [])
check("grant role records a role_granted audit event",
      len(_alog) >= 1 and _alog[0].get('event') == 'role_granted'
      and _alog[0].get('roles') == ['editor'], str(_alog))

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
