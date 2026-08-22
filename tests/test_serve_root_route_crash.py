#!/usr/bin/env python3
# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Regression guard for FIX-B9-6 (T1-SILENT-SWEEP-BATCH9, finding #17).

`serve_frontend`'s GET `/` handler used to catch ANY exception escaping
`_with_timeout(server.dispatch, ...)` and fall through to the SAME "no root route" code path
used for a genuinely-undefined `/` -- both landed on the neutral placeholder at HTTP 200, so a
real crash was indistinguishable from a healthy app that simply has no root route. This is the
finding that made "GET works" a false signal during the Zork custom-domain diagnosis.

`MohioServer.dispatch` already catches every exception from running the program itself
(format_runtime_error/log_runtime_error) and returns a real {status, body} dict through the
NORMAL (non-exception) path -- that path is unaffected by this fix and is exercised by other
tests. This file covers the two cases specific to this fix:
  1. An exception that escapes `_with_timeout`'s own wrapping (rarer, deeper than an ordinary
     program crash) must now surface as a real error response, not the 200 placeholder.
     Reached here via a deliberate, labeled monkeypatch of `server.dispatch` -- dispatch's own
     exception handling is a separate, already-correct path this test does not need to re-prove.
  2. A genuinely undefined root route (no `/` anywhere in the program) must still show the
     placeholder at 200 -- the legitimate case, confirmed intact.
"""
import os, sys
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import mohio_data
os.chdir(ROOT)
os.environ.setdefault('DATABASE_URL', ':memory:')

try:
    from lark import Lark
    from mohio_transformer_ast import transform
    from mohio_interpreter import MohioInterpreter
    from mohio_server import MohioServer, create_app
    from starlette.testclient import TestClient
except ImportError as e:
    print(f"SKIP: missing dependency ({e})")
    sys.exit(0)

_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

PASS = FAIL = 0
def check(label, cond, detail=""):
    global PASS, FAIL
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    PASS += bool(cond); FAIL += (not cond)


class MockAI:
    def register_chain(self, *a, **k): pass
    def decide(self, **k):
        return SimpleNamespace(result=None, confidence=0.9, fell_back=False, model="mock")


def make_client(source):
    prog = transform(P.parse(source), source)
    interp = MohioInterpreter(ai=MockAI())
    server = MohioServer(prog, interp)
    app = create_app(server)
    return server, TestClient(app, raise_server_exceptions=False)


print("=== 1. genuinely no root route -- placeholder at 200, legitimate case intact ===")
NO_ROOT_SRC = (
    'listen for\n'
    '    new sh.Request\n'
    '        require role "user"\n'
    '        give back 200 "ok"\n'
    '    new: done\n'
    'listen: done\n'
)
_, client = make_client(NO_ROOT_SRC)
resp = client.get("/")
check("no root route -> still 200 (placeholder)", resp.status_code == 200,
      f"got {resp.status_code}: {resp.text[:200]}")

print()
print("=== 2. root route exists and its dispatch call fails outside dispatch's own catch ===")
print("    (server.dispatch monkeypatched to raise directly -- deliberately isolated, see")
print("    module docstring for why this is the right level to test this specific fix at)")
ANY_SRC = 'show "unused -- monkeypatch bypasses the real program"\n'
server, client = make_client(ANY_SRC)

def _boom(payload, session_id=None):
    raise RuntimeError("simulated failure outside dispatch's own exception handling")
server.dispatch = _boom

resp = client.get("/")
check("crash surfaces as a real error status, not 200", resp.status_code >= 500,
      f"got {resp.status_code}: {resp.text[:300]}")
check("crash response is NOT the neutral placeholder HTML", "RuntimeError" in resp.text or resp.headers.get("content-type", "").startswith("application/json"),
      f"got: {resp.text[:300]}")
try:
    body = resp.json()
    check("error body carries a real code/message (format_runtime_error shape)",
          isinstance(body, dict) and body.get("message") and "simulated failure" in body.get("message", ""),
          f"got: {body}")
except Exception as e:
    check("error body carries a real code/message (format_runtime_error shape)", False, str(e))

print()
print(f"RESULTS: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
