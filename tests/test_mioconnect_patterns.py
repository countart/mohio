# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
test_mioconnect_patterns.py — real-world connector patterns

Covers: POST with payload, response shape, connector in route handler,
error responses, auth headers on the wire, chained calls, multiple
connectors, connector reuse.

All tests mock HTTP via unittest.mock to avoid network dependency.

Run: PYTHONPATH=$PWD DATABASE_URL=:memory: python3 tests/test_mioconnect_patterns.py
"""
import os, sys, re, json, base64, unittest.mock
sys.argv = ['mio.py']
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('DATABASE_URL', ':memory:')

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter, AiDecision, Context
from mohio_server import MohioServer, create_app
from starlette.testclient import TestClient

_passed = _failed = 0
def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1; print(f"  ok   {label}")
    else:
        _failed += 1; print(f"  FAIL {label}: got {got!r} want {want!r}")

def check_true(label, val):
    global _passed, _failed
    if val:
        _passed += 1; print(f"  ok   {label}")
    else:
        _failed += 1; print(f"  FAIL {label}: expected truthy, got {val!r}")

def check_in(label, haystack, needle):
    global _passed, _failed
    if needle.lower() in str(haystack).lower():
        _passed += 1; print(f"  ok   {label}")
    else:
        _failed += 1; print(f"  FAIL {label}: {needle!r} not in output")

_raw = open(os.path.join(ROOT, 'mohio.lark'), encoding='utf-8').read()
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

class MockAI:
    def register_chain(self, *a, **k): pass
    def decide(self, name='', inputs=None, **k):
        return AiDecision(result=None, confidence=0.9, fell_back=False,
                          model='mock', inputs=inputs or {})

def unwrap(r):
    try:
        data = r.json()
    except Exception:
        return r.text.strip()
    if isinstance(data, list): return data
    msg = data.get("message", data.get("body", ""))
    if isinstance(msg, str):
        m = re.match(r"MohioValue\('(.+?)',", msg)
        if m: return m.group(1)
    return msg


# ── HTTP mock helper ──────────────────────────────────────────────────────────

_captured_requests = []

def mock_urlopen_factory(status=200, body='{"ok":true}', headers=None):
    """Create a mock urlopen that captures requests and returns canned responses."""
    def mock_urlopen(req, **kwargs):
        _captured_requests.append({
            'url': req.full_url if hasattr(req, 'full_url') else str(req),
            'method': req.get_method() if hasattr(req, 'get_method') else 'GET',
            'headers': dict(req.headers) if hasattr(req, 'headers') else {},
            'data': req.data if hasattr(req, 'data') else None,
        })
        resp = unittest.mock.MagicMock()
        resp.status = status
        resp.getcode.return_value = status
        body_bytes = body.encode() if isinstance(body, str) else body
        resp.read.return_value = body_bytes
        resp.headers = headers or {}
        resp.getheader = lambda name, default=None: (headers or {}).get(name, default)
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda s, *a: None
        return resp
    return mock_urlopen


# ══════════════════════════════════════════════════════════════════════════════
# 1. POST SENDS PAYLOAD
# ══════════════════════════════════════════════════════════════════════════════
print("\n── POST sends payload ──")

os.environ['API_KEY'] = 'sk_test_xxx'

src = '''\
connect db as sqlite from env.DATABASE_URL

mioconnect Stripe
    address "https://api.stripe.com/v1"
    auth bearer env.API_KEY
    operation charge
        path "/charges"
        method POST
    operation: done
mioconnect: done

listen for
    request for sh.Q at /q
        hold payload = "amount=1000&currency=usd"
        Stripe.charge with payload as result
        give back ok result.status
    request: done
listen: done
'''

_captured_requests.clear()
with unittest.mock.patch('urllib.request.urlopen', mock_urlopen_factory(
        status=200, body='{"id":"ch_123","amount":1000}')):
    prog = transform(_P.parse(src), src)
    interp = MohioInterpreter(ai=MockAI())
    server = MohioServer(prog, interp)
    app = create_app(server)
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/q")

check("POST status via handler", r.status_code, 200)
check_true("request captured", len(_captured_requests) > 0)
if _captured_requests:
    req = _captured_requests[0]
    check("POST method", req['method'], 'POST')
    check_true("POST has body", req['data'] is not None)
    check_in("POST URL has /charges", req['url'], '/charges')


# ══════════════════════════════════════════════════════════════════════════════
# 2. AUTH HEADERS ON THE WIRE
# ══════════════════════════════════════════════════════════════════════════════
print("\n── Auth headers on the wire ──")

# Bearer
os.environ['BEARER_KEY'] = 'my_bearer_token'
src_bearer = '''\
connect db as sqlite from env.DATABASE_URL
mioconnect Svc
    address "https://api.example.com"
    auth bearer env.BEARER_KEY
    operation ping
        path "/ping"
        method GET
    operation: done
mioconnect: done
listen for
    request for sh.Q at /q
        Svc.ping as result
        give back ok result.status
    request: done
listen: done
'''

_captured_requests.clear()
with unittest.mock.patch('urllib.request.urlopen', mock_urlopen_factory()):
    prog = transform(_P.parse(src_bearer), src_bearer)
    interp = MohioInterpreter(ai=MockAI())
    server = MohioServer(prog, interp)
    app = create_app(server)
    c = TestClient(app, raise_server_exceptions=False)
    c.get("/q")

if _captured_requests:
    auth_header = _captured_requests[0]['headers'].get('Authorization', '')
    check("bearer header", auth_header, 'Bearer my_bearer_token')
else:
    check_true("bearer request captured", False)

# Basic auth
os.environ['BASIC_USER'] = 'myuser'
os.environ['BASIC_PASS'] = 'mypass'
src_basic = '''\
connect db as sqlite from env.DATABASE_URL
mioconnect Svc2
    address "https://api.example.com"
    auth basic env.BASIC_USER env.BASIC_PASS
    operation ping
        path "/ping"
        method GET
    operation: done
mioconnect: done
listen for
    request for sh.Q at /q
        Svc2.ping as result
        give back ok result.status
    request: done
listen: done
'''

_captured_requests.clear()
with unittest.mock.patch('urllib.request.urlopen', mock_urlopen_factory()):
    prog = transform(_P.parse(src_basic), src_basic)
    interp = MohioInterpreter(ai=MockAI())
    server = MohioServer(prog, interp)
    app = create_app(server)
    c = TestClient(app, raise_server_exceptions=False)
    c.get("/q")

if _captured_requests:
    auth_header = _captured_requests[0]['headers'].get('Authorization', '')
    expected_basic = 'Basic ' + base64.b64encode(b'myuser:mypass').decode()
    check("basic header", auth_header, expected_basic)
else:
    check_true("basic request captured", False)

# Custom header auth
os.environ['CUSTOM_KEY'] = 'custom_value'
src_header = '''\
connect db as sqlite from env.DATABASE_URL
mioconnect Svc3
    address "https://api.example.com"
    auth header "X-API-Key" env.CUSTOM_KEY
    operation ping
        path "/ping"
        method GET
    operation: done
mioconnect: done
listen for
    request for sh.Q at /q
        Svc3.ping as result
        give back ok result.status
    request: done
listen: done
'''

_captured_requests.clear()
with unittest.mock.patch('urllib.request.urlopen', mock_urlopen_factory()):
    prog = transform(_P.parse(src_header), src_header)
    interp = MohioInterpreter(ai=MockAI())
    server = MohioServer(prog, interp)
    app = create_app(server)
    c = TestClient(app, raise_server_exceptions=False)
    c.get("/q")

if _captured_requests:
    custom = _captured_requests[0]['headers'].get('X-api-key',
             _captured_requests[0]['headers'].get('X-API-Key', ''))
    check("custom header value", custom, 'custom_value')
else:
    check_true("custom header request captured", False)


# ══════════════════════════════════════════════════════════════════════════════
# 3. RESPONSE SHAPE CONTRACT
# ══════════════════════════════════════════════════════════════════════════════
print("\n── Response shape contract ──")

src_shape = '''\
connect db as sqlite from env.DATABASE_URL
mioconnect API
    address "https://api.example.com"
    auth bearer env.API_KEY
    operation getData
        path "/data"
        method GET
    operation: done
mioconnect: done
listen for
    request for sh.Q at /status
        API.getData as result
        give back ok result.status
    request: done
    request for sh.Q at /ok
        API.getData as result
        give back ok result.ok
    request: done
    request for sh.Q at /field
        API.getData as result
        give back ok result.json.name
    request: done
listen: done
'''

_captured_requests.clear()
with unittest.mock.patch('urllib.request.urlopen', mock_urlopen_factory(
        status=200, body='{"name":"Alice","age":30}')):
    prog = transform(_P.parse(src_shape), src_shape)
    interp = MohioInterpreter(ai=MockAI())
    server = MohioServer(prog, interp)
    app = create_app(server)
    c = TestClient(app, raise_server_exceptions=False)

    r = c.get("/status")
    check("response.status = 200", str(unwrap(r)), "200")

    r = c.get("/ok")
    val = str(unwrap(r)).lower()
    check_true("response.ok is truthy", val in ("true", "1", "yes"))

    r = c.get("/field")
    check("response.json.name = Alice", str(unwrap(r)), "Alice")


# ══════════════════════════════════════════════════════════════════════════════
# 4. ERROR RESPONSE (4xx/5xx)
# ══════════════════════════════════════════════════════════════════════════════
print("\n── Error response handling ──")

src_err = '''\
connect db as sqlite from env.DATABASE_URL
mioconnect API
    address "https://api.example.com"
    auth bearer env.API_KEY
    operation fail
        path "/fail"
        method GET
    operation: done
mioconnect: done
listen for
    request for sh.Q at /q
        API.fail as result
        give back ok result.status
    request: done
listen: done
'''

_captured_requests.clear()
with unittest.mock.patch('urllib.request.urlopen', mock_urlopen_factory(
        status=422, body='{"error":"validation failed"}')):
    prog = transform(_P.parse(src_err), src_err)
    interp = MohioInterpreter(ai=MockAI())
    server = MohioServer(prog, interp)
    app = create_app(server)
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/q")
    check("error response status", str(unwrap(r)), "422")


# ══════════════════════════════════════════════════════════════════════════════
# 5. MULTIPLE CONNECTORS
# ══════════════════════════════════════════════════════════════════════════════
print("\n── Multiple connectors ──")

src_multi = '''\
connect db as sqlite from env.DATABASE_URL
mioconnect Stripe
    address "https://api.stripe.com"
    auth bearer env.API_KEY
    operation charge
        path "/charges"
        method POST
    operation: done
mioconnect: done
mioconnect Twilio
    address "https://api.twilio.com"
    auth bearer env.API_KEY
    operation send
        path "/messages"
        method POST
    operation: done
mioconnect: done
listen for
    request for sh.Q at /q
        Stripe.charge with "amount=100" as payment
        Twilio.send with "body=hello" as sms
        give back ok (payment.status & "+" & sms.status)
    request: done
listen: done
'''

_captured_requests.clear()
with unittest.mock.patch('urllib.request.urlopen', mock_urlopen_factory(
        status=200, body='{"ok":true}')):
    prog = transform(_P.parse(src_multi), src_multi)
    interp = MohioInterpreter(ai=MockAI())
    server = MohioServer(prog, interp)
    app = create_app(server)
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/q")

check_true("multi-connector: 2 requests made", len(_captured_requests) >= 2)
if len(_captured_requests) >= 2:
    urls = [req['url'] for req in _captured_requests]
    check_true("multi: stripe called", any('stripe' in u for u in urls))
    check_true("multi: twilio called", any('twilio' in u for u in urls))


# ══════════════════════════════════════════════════════════════════════════════
# 6. GET SENDS NO BODY
# ══════════════════════════════════════════════════════════════════════════════
print("\n── GET sends no body ──")

src_get = '''\
connect db as sqlite from env.DATABASE_URL
mioconnect API
    address "https://api.example.com"
    auth bearer env.API_KEY
    operation lookup
        path "/lookup"
        method GET
    operation: done
mioconnect: done
listen for
    request for sh.Q at /q
        API.lookup with "should_be_ignored" as result
        give back ok result.status
    request: done
listen: done
'''

_captured_requests.clear()
with unittest.mock.patch('urllib.request.urlopen', mock_urlopen_factory()):
    prog = transform(_P.parse(src_get), src_get)
    interp = MohioInterpreter(ai=MockAI())
    server = MohioServer(prog, interp)
    app = create_app(server)
    c = TestClient(app, raise_server_exceptions=False)
    c.get("/q")

if _captured_requests:
    check("GET has no body", _captured_requests[0]['data'], None)
else:
    check_true("GET request captured", False)


# ══════════════════════════════════════════════════════════════════════════════
# 7. CONNECTOR REUSE (same op, different payloads)
# ══════════════════════════════════════════════════════════════════════════════
print("\n── Connector reuse ──")

src_reuse = '''\
connect db as sqlite from env.DATABASE_URL
mioconnect API
    address "https://api.example.com"
    auth bearer env.API_KEY
    operation post
        path "/items"
        method POST
    operation: done
mioconnect: done
listen for
    request for sh.Q at /q
        API.post with "item=a" as r1
        API.post with "item=b" as r2
        API.post with "item=c" as r3
        give back ok "done"
    request: done
listen: done
'''

_captured_requests.clear()
with unittest.mock.patch('urllib.request.urlopen', mock_urlopen_factory()):
    prog = transform(_P.parse(src_reuse), src_reuse)
    interp = MohioInterpreter(ai=MockAI())
    server = MohioServer(prog, interp)
    app = create_app(server)
    c = TestClient(app, raise_server_exceptions=False)
    c.get("/q")

check("reuse: 3 requests made", len(_captured_requests), 3)


# ══════════════════════════════════════════════════════════════════════════════
# Cleanup
for k in ['API_KEY', 'BEARER_KEY', 'BASIC_USER', 'BASIC_PASS', 'CUSTOM_KEY']:
    os.environ.pop(k, None)

print(f"\n{'=' * 60}")
print(f"  {_passed} passed, {_failed} failed")
if _failed:
    print(f"  *** {_failed} FAILURE(S) ***")
print(f"{'=' * 60}")
sys.exit(1 if _failed else 0)
