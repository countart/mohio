# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""Outbound HTTP does not chase 3xx redirects into internal hosts (SSRF, S9, 2026-08-01).

`urllib.urlopen` follows redirects by default, so an allowlisted public URL that returns
`302 Location: http://169.254.169.254/...` used to silently fetch the cloud-metadata endpoint
(confirmed by running miohttp.get). Fix, shared by miohttp AND mioconnect via `_http_open`:

  * HOP 0: the INITIAL target is vetted with the same classifier -- a directly-configured or
    interpolated metadata/private/loopback address is refused before the first connection.
    MOHIO_HTTP_ALLOW_INTERNAL=1 opts a deployment in to calling internal services / localhost.
  * DEFAULT: redirects are NOT followed -- a 3xx fails loud, naming the target.
  * OPT-IN (MOHIO_HTTP_FOLLOW_REDIRECTS=1): redirects are followed, but EVERY hop is re-vetted,
    so a chain public -> public -> internal is refused at the internal hop, not just hop 1. A
    redirect hop to internal stays refused even under MOHIO_HTTP_ALLOW_INTERNAL (that opt-in is
    for developer-typed hop-0 targets; a remote-controlled redirect to internal is the attack).

This locks the classifier, hop-0 vetting, per-hop redirect vetting, and the e2e refusal via `mio run`.
Run: `python tests/test_ssrf_redirect.py`.
"""
import os, sys, http.server, socketserver, threading, subprocess, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')

from mohio_interpreter import _ssrf_internal_reason, _VettedRedirect, MohioRuntimeError

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)


# ── 1. the internal-target classifier ─────────────────────────────────────────────────
for url, should_block in (
    ("http://169.254.169.254/latest/meta-data/", True),   # AWS/GCP metadata (link-local)
    ("http://127.0.0.1/x",                       True),    # loopback
    ("http://10.1.2.3/x",                        True),    # private
    ("http://192.168.0.5/x",                     True),    # private
    ("http://172.16.9.9/x",                      True),    # private
    ("http://localhost/x",                       True),    # loopback name
    ("http://foo.internal/x",                    True),    # internal name
    ("http://metadata.google.internal/x",        True),    # metadata name
    ("http://[::1]/x",                           True),    # IPv6 loopback
    ("https://example.com/x",                    False),   # public host -> allowed
    ("https://api.stripe.com/v1/charges",        False),   # public host -> allowed
    ("http://8.8.8.8/x",                         False),   # public IP -> allowed
):
    reason = _ssrf_internal_reason(url)
    check(f"classify {'BLOCK' if should_block else 'ALLOW'}: {url}",
          (reason is not None) == should_block, f"reason={reason!r}")


# ── 2. per-hop vetting: a redirect to an internal target raises; to a public one follows ─
import urllib.request as _ur

class _Hdrs(dict):
    def get_all(self, k, default=None): return default

def _orig():
    # a real Request so HTTPRedirectHandler.redirect_request has origin_req_host etc.
    return _ur.Request("https://start.example.com/", method="GET")

_vr = _VettedRedirect()
_raised = False
try:
    _vr.redirect_request(_orig(), None, 302, "Found", _Hdrs(),
                         "http://169.254.169.254/latest/meta-data/")
except MohioRuntimeError as e:
    _raised = 'internal address' in str(e)
check("VettedRedirect refuses an internal hop (loudly)", _raised)

# a public target must NOT raise -- redirect_request returns a Request (follow proceeds)
_public_ok = False
try:
    _req = _vr.redirect_request(_orig(), None, 302, "Found", _Hdrs(),
                                "https://cdn.example.com/asset")
    _public_ok = _req is not None
except MohioRuntimeError:
    _public_ok = False
check("VettedRedirect FOLLOWS a public hop (returns a Request, does not raise)", _public_ok)


# ── 3. end-to-end through `mio run`: the confirmed repro is refused ─────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = dict(os.environ, PYTHONPATH=ROOT, DATABASE_URL=':memory:',
            PYTHONIOENCODING='utf-8', PYTHONUTF8='1')

class _Internal(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"INTERNAL-SECRET")
    def log_message(self, *a): pass
class _Redirect(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(302); self.send_header("Location", _TARGET); self.end_headers()
    def log_message(self, *a): pass
class _Plain(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"PLAIN-OK")
    def log_message(self, *a): pass

_si = socketserver.TCPServer(("127.0.0.1", 0), _Internal); _PI = _si.server_address[1]
_TARGET = f"http://127.0.0.1:{_PI}/latest/meta-data/"
_sr = socketserver.TCPServer(("127.0.0.1", 0), _Redirect); _PR = _sr.server_address[1]
_sp = socketserver.TCPServer(("127.0.0.1", 0), _Plain);    _PP = _sp.server_address[1]
for _s in (_si, _sr, _sp):
    threading.Thread(target=_s.serve_forever, daemon=True).start()

def _run(prog, env):
    fd, p = tempfile.mkstemp(suffix='.mho'); os.write(fd, prog.encode()); os.close(fd)
    try:
        r = subprocess.run([sys.executable, os.path.join(ROOT, 'mio.py'), 'run', p],
                           cwd=ROOT, env=env, capture_output=True, text=True, timeout=45)
        return r.stdout + r.stderr
    finally:
        os.unlink(p)

# The loopback test servers stand in for TRUSTED hosts, so they declare MOHIO_HTTP_ALLOW_INTERNAL
# (otherwise hop-0 vetting -- see the hop-0 block below -- would refuse the loopback address before
# any redirect). Redirect hops stay refused regardless of that opt-in.
SRV = dict(BASE, MOHIO_HTTP_ALLOW_INTERNAL='1')
_pub = f"http://127.0.0.1:{_PR}/"
try:
    # ── hop 0: the INITIAL target is vetted too, not just redirect hops ────────────────
    _meta = "http://169.254.169.254/latest/meta-data/"
    out = _run(f'miohttp.get "{_meta}" as r\nshow r.body\n', BASE)
    check("e2e hop-0: a directly-configured metadata URL is REFUSED at hop 0 (default)",
          "targets an internal address" in out, out[-160:])
    out = _run(f'miohttp.get "http://127.0.0.1:1/x" as r\nshow r.body\n', BASE)
    check("e2e hop-0: a directly-configured loopback URL is REFUSED at hop 0 (default)",
          "targets an internal address" in out, out[-160:])
    # opt-in lets a legit internal call PAST the hop-0 gate (it then fails to connect, NOT with
    # the SSRF refusal) -- proving the gate is exactly the internal-address check, nothing more.
    out = _run(f'miohttp.get "http://127.0.0.1:1/x" as r\nshow r.status\n',
               dict(BASE, MOHIO_HTTP_ALLOW_INTERNAL='1'))
    check("e2e hop-0: MOHIO_HTTP_ALLOW_INTERNAL=1 lets an internal URL past the gate",
          "targets an internal address" not in out, out[-160:])

    # ── redirect behaviour (loopback servers stand in for trusted hosts via SRV) ───────
    out = _run(f'miohttp.get "{_pub}" as r\nshow r.body\n', SRV)
    check("e2e default: 302->internal is REFUSED (no leak)", "INTERNAL-SECRET" not in out, out[-160:])
    check("e2e default: the refusal names the redirect and is loud",
          "does NOT follow" in out and "302" in out, out[-160:])

    out = _run(f'miohttp.get "http://127.0.0.1:{_PP}/" as r\nshow r.status\nshow r.body\n', SRV)
    check("e2e default: a NO-redirect request still works", "PLAIN-OK" in out and "200" in out, out[-160:])

    env = dict(SRV, MOHIO_HTTP_FOLLOW_REDIRECTS='1')
    out = _run(f'miohttp.get "{_pub}" as r\nshow r.body\n', env)
    check("e2e opt-in: internal REDIRECT hop STILL refused (even with ALLOW_INTERNAL)",
          "INTERNAL-SECRET" not in out and ("internal address" in out or "refused to follow" in out),
          out[-160:])
finally:
    for _s in (_si, _sr, _sp):
        _s.shutdown()

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
