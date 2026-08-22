# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""RUN 3 Part B2 (2026-08-19): mioconnect's retry/on.failure/on.success facade fixed.

CONFIRMED (I3 investigation, cited): mioconnect_decl's old transformer comment literally said
"webhook / retry: parsed and accepted; no node field in MVP" (mohio_transformer_ast.py) --
MioconnectDecl had no field for retry, on.failure, or on.success, so all three parsed clean at
`mio check` and were silently discarded at transform time. _exec_MioconnectCall's generic
`except Exception` branch built a checkable {"ok": False, "error": ...} result and NEVER
raised, retried, or consulted a handler -- every genuine connectivity failure (DNS, refused,
timeout) looked exactly like an ordinary result, indistinguishable from a real 4xx/5xx.

FIXED, precisely scoped: an HTTPError (a real response came back, just not 2xx) and a
successful response are BOTH legitimate CONDITION-style outcomes, unchanged -- still a
checkable {"status", "ok", ...} shape, never raises, never retries. This is locked by the
existing test_mioconnect_patterns.py's "ERROR RESPONSE (4xx/5xx)" case, confirmed still
passing. Only a genuine connectivity exception (never an HTTPError) is a STATE break: it now
retries up to the declared `retry N times` count, then either runs a declared on.failure or
fails loud -- matching every other verb's Part A/B baseline. on.success fires whenever the
call actually completed (2xx or a real HTTP error status both count -- the op ran).

Real .mho source through the full pipeline throughout (T1-TEST-REAL-PATH-STANDARD), with
urllib.request.urlopen mocked (the same seam test_mioconnect_patterns.py already uses) so this
runs offline and deterministically.

Run: `python tests/test_run3_mioconnect_facade.py`.
"""
import os, sys, unittest.mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); os.chdir(ROOT)
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')
os.environ.setdefault('MOHIO_ENCRYPTION_KEY', 'testkey')

from lark import Lark
from mohio_transformer_ast import transform as ast_transform
from mohio_interpreter import MohioInterpreter

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


def run_real(src):
    prog = ast_transform(P.parse(src), src)
    it = MohioInterpreter()
    it.run_declarations(prog)
    r = it.run(prog)
    return it, r


SEED = ('connect db as sqlite from env.DATABASE_URL\n'
        'mioconnect Svc\n    address "https://api.example.com"\n')
OP = '    operation ping\n        path "/ping"\n    operation: done\nmioconnect: done\n'


# ── genuine connectivity failure, no on.failure declared -> fails loud (was silently ok=False)
with unittest.mock.patch('urllib.request.urlopen',
                          side_effect=OSError("Name or service not known")):
    it, r = run_real(SEED + OP + 'Svc.ping as result\nshow "unreachable"\n')
check("genuine connectivity failure, no on.failure -> fails loud (was silent ok=False)",
      r.get('status') == 500 and 'connection failed' in str(r.get('body', '')), r)
check("the failure never reaches past the call (unreachable show never ran)",
      it.shown == [], it.shown)

# ── genuine connectivity failure WITH on.failure declared -> caught, not raised ────────────
with unittest.mock.patch('urllib.request.urlopen',
                          side_effect=OSError("Name or service not known")):
    it2, r2 = run_real(
        SEED + '    on.failure\n        show "caught"\n' + OP +
        'Svc.ping as result\nshow "after"\n')
check("genuine connectivity failure with on.failure declared -> caught",
      it2.shown == ["caught", "after"], it2.shown)

# ── retry: fails N-1 times then succeeds -> succeeds, on.failure never fires ───────────────
_calls = [0]
def _flaky(req, **kwargs):
    _calls[0] += 1
    if _calls[0] < 3:
        raise OSError("Connection refused")
    resp = unittest.mock.MagicMock()
    resp.status = 200; resp.read.return_value = b'{"ok":true}'; resp.headers = {}
    resp.__enter__ = lambda s: s; resp.__exit__ = lambda s, *a: None
    return resp

with unittest.mock.patch('urllib.request.urlopen', side_effect=_flaky):
    it3, r3 = run_real(
        SEED + '    retry 3 times\n    on.success\n        show "connector succeeded"\n' + OP +
        'Svc.ping as result\nshow ("status=" & result.status)\n')
check(f"retry 3 times recovers from 2 failures ({_calls[0]} real attempts made)",
      _calls[0] == 3, _calls[0])
check("on.success fires once the retried call succeeds",
      it3.shown == ["connector succeeded", "status=200"], it3.shown)

# ── a real HTTP error response (422) is UNCHANGED: checkable result, never raises, never
# retries, on.success STILL fires (the op completed, it just wasn't 2xx) ───────────────────
def _http_422(req, **kwargs):
    import urllib.error
    raise urllib.error.HTTPError(req.full_url, 422, "Unprocessable", {}, None)

with unittest.mock.patch('urllib.request.urlopen', side_effect=_http_422):
    it4, r4 = run_real(
        SEED + '    retry 5 times\n    on.success\n        show "op completed"\n'
        '    on.failure\n        show "should NOT fire for a 422"\n' + OP +
        'Svc.ping as result\nshow ("status=" & result.status)\n')
check("a real HTTP 422 stays a checkable result (unchanged, locked by test_mioconnect_patterns.py)",
      it4.shown == ["op completed", "status=422"], it4.shown)

# ── B4: connectivity failures are audited ───────────────────────────────────────────────────
_log2 = it2._audit_logs.get('security_audit_log', [])
check("a caught connectivity failure is audited via _audit_event",
      any(e.get('event') == 'connection_failed' and e.get('connector') == 'Svc' for e in _log2),
      _log2)


print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
