# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
test_journey_access_control.py -- private:/public:/flow:/serves: journey-level access
control (2026-08-06).

History: all four were the same disease. public:/private:/flow: parsed into an identical,
indistinguishable generic `path_list`-shaped JourneyMeta (the leading keyword is a bare
underscore-filtered terminal Lark drops -- the transformer's own old comment admitted "cannot
be told apart after parse"). serves: was worse -- BOTH its values ("single tenant" and
"multiple tenants") were filtered to an empty tree and dropped to None in journey_body,
before ever becoming a node. _exec_journey_scope has always explicitly skipped JourneyMeta,
and no commit since the journey feature's introduction ever added real enforcement -- every
path in every journey was silently open regardless of what was declared. Confirmed by real
HTTP reproduction (tenant_isolation_probe.py, this session): a `serves: multiple tenants`
journey let tenant B read tenant A's data verbatim over a plain, unauthenticated GET.

Fixed:
  - Grammar: each of the five journey_body alternatives now has its own -> alias
    (journey_public/journey_private/journey_flow/journey_serves_single/journey_serves_multiple)
    so the transformer can tell them apart.
  - private:/public: are now genuinely enforced in _exec_JourneyDecl, reusing require role's
    exact server-verified-session mechanism (ctx.has_any_roles()/roles_verified()) and 403
    shape. Matching is segment-boundary prefix (private: /admin also covers /admin/users);
    public: is an explicit override for a path that would otherwise fall under a private:
    entry.
  - flow: and serves: multiple tenants cannot be genuinely built yet (flow's intended runtime
    behavior has no documented source of truth anywhere in this repo; serves: needs a
    request-scoped tenant-identity primitive that does not exist in the language). Converted
    from silent no-op to fail-loud (501, "not yet ..."), matching the house pattern already
    used by rate limit / miopdf / miotest -- see CLAUDE-CODE-BACKLOG.md. serves: single tenant
    is unaffected (nothing to isolate, safe as a no-op).

Real HTTP throughout (Starlette TestClient), matching test_golden_journey_page.py's harness.
Run: `python tests/test_journey_access_control.py`.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')

from types import SimpleNamespace
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter
from mohio_server import MohioServer, create_app
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

class MockAI:
    def register_chain(self, *a, **k): pass
    def decide(self, **k):
        return SimpleNamespace(result=None, confidence=0.9, fell_back=False, model='mock')

def make_client(source):
    prog = transform(_P.parse(source), source)
    interp = MohioInterpreter(ai=MockAI())
    server = MohioServer(prog, interp)
    return TestClient(create_app(server), raise_server_exceptions=False)


# ══════════════════════════════════════════════════════════════════════
# GROUP A -- private:/public: real enforcement
# ══════════════════════════════════════════════════════════════════════
_ACCESS = """\
journey AccessApp
    private: /admin
    public: /admin/public-notice

    shape Login
        who as text
    shape: done

    page Home at /home
        render
            <p>[HOME]</p>
        render: done
    page: done
    page Admin at /admin
        render
            <p>[ADMIN]</p>
        render: done
    page: done
    page AdminUsers at /admin/users
        render
            <p>[ADMIN_USERS]</p>
        render: done
    page: done
    page AdminNotice at /admin/public-notice
        render
            <p>[NOTICE]</p>
        render: done
    page: done
    listen for
        new sh.Login at /login
            grant role "staff"
            give back 200 "[LOGGED_IN]"
        new: done
    listen: done
journey: done
"""

c = make_client(_ACCESS)

r = c.get("/home")
check("unlisted path (/home) stays default-open, no auth needed",
      r.status_code == 200 and "[HOME]" in r.text, f"status={r.status_code} body={r.text[:150]}")

r = c.get("/admin")
check("private: /admin genuinely REFUSES an unauthenticated request (403)",
      r.status_code == 403, f"status={r.status_code} body={r.text[:150]}")
check("the refused request does NOT leak the protected page content",
      "[ADMIN]" not in r.text, r.text[:150])

r = c.get("/admin/users")
check("private: /admin covers a deeper path (/admin/users) via segment-boundary prefix -> 403",
      r.status_code == 403, f"status={r.status_code} body={r.text[:150]}")

r = c.get("/admin/public-notice")
check("public: /admin/public-notice OVERRIDES the private: /admin prefix -> stays open (200)",
      r.status_code == 200 and "[NOTICE]" in r.text, f"status={r.status_code} body={r.text[:150]}")

r_login = c.post("/login", json={"who": "bo", "_shape": "Login"})
check("login (grant role) -> 200, session cookie set",
      r_login.status_code == 200 and "mio_session" in c.cookies,
      f"status={r_login.status_code} cookies={dict(c.cookies)}")

r = c.get("/admin")
check("after login (any server-verified role), private: /admin -> 200",
      r.status_code == 200 and "[ADMIN]" in r.text, f"status={r.status_code} body={r.text[:150]}")

r = c.get("/admin/users")
check("after login, the covered subpath /admin/users -> 200 too",
      r.status_code == 200 and "[ADMIN_USERS]" in r.text, f"status={r.status_code} body={r.text[:150]}")


# ══════════════════════════════════════════════════════════════════════
# GROUP B -- flow: converted from silent no-op to fail-loud (501)
# ══════════════════════════════════════════════════════════════════════
_FLOW = """\
journey FlowApp
    flow: /wizard
    page Wizard at /wizard
        render
            <p>[WIZARD]</p>
        render: done
    page: done
journey: done
"""

c2 = make_client(_FLOW)
r = c2.get("/wizard")
# 500, not 501: verified by running -- run_with_session (the real mio-serve path) catches
# MohioRuntimeError locally and always returns 500, never reaching format_runtime_error's
# "not yet" -> 501 mapping. That is a PRE-EXISTING gap (affects the whole existing
# miopdf/rate-limit/miotest fail-loud family too, not something introduced here) -- named,
# not fixed, out of scope for this unit. See CLAUDE-CODE-BACKLOG.md.
check("flow: no longer silently does nothing -- fails loud (500) instead of serving [WIZARD]",
      r.status_code == 500 and "[WIZARD]" not in r.text, f"status={r.status_code} body={r.text[:200]}")
check("the flow: fail-loud names the feature and reads as a deferral, not a mistake",
      "flow" in r.text.lower() and any(s in r.text.lower() for s in ("not yet", "tracked")),
      r.text[:300])


# ══════════════════════════════════════════════════════════════════════
# GROUP C -- serves: multiple tenants: the exact tenant-A/B leak reproduction,
# now confirmed CLOSED (real HTTP, same shape as the original reproduction).
# ══════════════════════════════════════════════════════════════════════
_TENANTS = """\
journey MultiTenantApp
    serves: multiple tenants
    connect db as sqlite from env.DATABASE_URL

    shape Note
        content as text required
    shape: done
    shape NoteRequest
        method POST
    shape: done
    shape ListRequest
        method GET
    shape: done

    listen for
        new sh.NoteRequest at /notes
            save to db.notes
                content request.content
            save: done
            give back 201 "saved"
        new: done
        request for sh.ListRequest at /notes
            retrieve.all notes from db.notes
                on.success
                    show "ok"
            retrieve.all: done
            render
                <p>{{ notes.count }} notes: {{ notes }}</p>
            render: done
        request: done
    listen: done
journey: done
"""

c3 = make_client(_TENANTS)
r_write = c3.post("/notes", json={"content": "TENANT-A-SECRET-CONFIDENTIAL-DATA"})
check("serves: multiple tenants -- the write no longer silently succeeds (not 201)",
      r_write.status_code != 201, f"status={r_write.status_code} body={r_write.text[:150]}")

r_read = c3.get("/notes")
check("serves: multiple tenants -- the ORIGINAL leak reproduction is now CLOSED: "
      "no 200, and tenant A's data never appears in the response",
      r_read.status_code != 200 and "TENANT-A-SECRET-CONFIDENTIAL-DATA" not in r_read.text,
      f"status={r_read.status_code} body={r_read.text[:200]}")
check("the closure is a real fail-loud (500), not an accidental different kind of leak",
      r_read.status_code == 500, f"status={r_read.status_code} body={r_read.text[:200]}")


# ══════════════════════════════════════════════════════════════════════
# GROUP D -- serves: single tenant remains a genuine no-op (nothing to isolate,
# must NOT fail loud -- regression guard against over-firing the new check).
# ══════════════════════════════════════════════════════════════════════
_SINGLE = """\
journey SingleApp
    serves: single tenant
    page Home at /home
        render
            <p>[OK]</p>
        render: done
    page: done
journey: done
"""

c4 = make_client(_SINGLE)
r = c4.get("/home")
check("serves: single tenant is unaffected -- still serves normally (200), no fail-loud",
      r.status_code == 200 and "[OK]" in r.text, f"status={r.status_code} body={r.text[:150]}")


# ══════════════════════════════════════════════════════════════════════
# GROUP E -- pathless request (no `_path` on the request at all, e.g. `mio run
# --request-file` with no `_path` key) against a journey declaring private:.
# _serve_pages's single-page fallback would otherwise serve whatever one page exists with
# no path to check against private:/public:, bypassing the check above entirely. Deny by
# default when the journey declares ANY private: entry and no verified role is present.
# Driven through run_with_session directly (not the HTTP TestClient) because real HTTP
# always sets `_path` from the URL -- the only way to construct a genuinely pathless
# request is the same stateless/session entry point `mio run --request-file` itself uses.
# ══════════════════════════════════════════════════════════════════════
from mohio_interpreter import MohioInterpreter, _InMemorySessionStore

_PRIVATE_SINGLE = """\
journey PrivateSingle
    private: /admin

    shape Login
        who as text
    shape: done

    page Admin at /admin
        render
            <p>[SECRET_ADMIN]</p>
        render: done
    page: done
    listen for
        new sh.Login at /login
            grant role "staff"
            give back 200 "[LOGGED_IN]"
        new: done
    listen: done
journey: done
"""

_NO_PRIVATE_SINGLE = """\
journey NoPrivateSingle
    page Home at /home
        render
            <p>[HOME_NO_PRIVATE]</p>
        render: done
    page: done
journey: done
"""

def _pathless(source, requests):
    """Run each request through run_with_session, following the session cookie across
    calls the way a real client would (grant role ROTATES the session id on privilege
    change, 2026-08-04 ruling -- reusing a fixed session_id across calls would silently
    talk to a different, anonymous session after login, exactly the trap this avoids).
    A request dict with no '_path' key reproduces the bypass exactly as `mio run
    --request-file` (no _path in the JSON) does -- no HTTP layer involved, so nothing
    injects a path."""
    prog = transform(_P.parse(source), source)
    sessions = _InMemorySessionStore()
    session_id = "e2e-pathless"
    results = []
    for req in requests:
        r = MohioInterpreter().run_with_session(prog, req, session_id, sessions)
        results.append(r)
        cookies = r.get('__pending_cookies__') if isinstance(r, dict) else None
        new_sid = (cookies or {}).get('mio_session', {}).get('value')
        if new_sid:
            session_id = new_sid
    return results

# (a) _path absent + private: declared + no role -> denied
r_a = _pathless(_PRIVATE_SINGLE, [{"_method": "GET"}])[0]
check("(a) pathless request, private: declared, no role -> denied (403)",
      r_a.get('status') == 403, str(r_a))
check("(a) the denial names the real reason (no path + journey declares private:)",
      'no path' in str(r_a.get('body', '')).lower()
      and 'private' in str(r_a.get('body', '')).lower(), str(r_a))
check("(a) the denial does NOT leak the protected page content",
      '[SECRET_ADMIN]' not in str(r_a.get('body', '')), str(r_a))

# (b) _path absent + private: declared + verified role -> allowed
r_login, r_b = _pathless(_PRIVATE_SINGLE, [
    {"_method": "POST", "_path": "/login", "_shape": "Login", "who": "bo"},
    {"_method": "GET"},
])
check("(b) setup: login established a server-verified role",
      r_login.get('status') == 200 and '[LOGGED_IN]' in str(r_login.get('body', '')),
      str(r_login))
check("(b) pathless request, private: declared, verified role -> allowed (not 403)",
      r_b.get('status') != 403, str(r_b))
check("(b) the single page is actually served, not silently swallowed",
      '[SECRET_ADMIN]' in str(r_b.get('body', '')), str(r_b))

# (c) _path absent + NO private: declared -> allowed, unchanged behavior
r_c = _pathless(_NO_PRIVATE_SINGLE, [{"_method": "GET"}])[0]
check("(c) pathless request, journey has NO private: entries -> unaffected (200)",
      r_c.get('status') == 200 and '[HOME_NO_PRIVATE]' in str(r_c.get('body', '')),
      str(r_c))

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
