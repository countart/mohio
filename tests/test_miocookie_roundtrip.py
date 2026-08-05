# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
test_miocookie_roundtrip.py

END TO END, over real HTTP. Not a unit test - a unit test is exactly what would have missed this.

`check miocookie.exists "probe_cookie"` was broken in production for months. It parsed, it checked
clean (one warning), and it ALWAYS returned false. Every request to the live Zork demo took the
`otherwise` branch and reset the player's session. Sessions never persisted.

Two bugs stacked, and each one hid the other:

  1. The grammar has had `miocookie_exists_expr` and `miocookie_get_expr` forever, but the
     transformer had no method for either. They stayed raw Lark Trees, and a raw Tree has no
     executor - `_eval` fell into its generic Tree branch and returned something meaningless.

  2. `_exec_MioCookieGet` falls back to `ctx.get('__request_cookies__')` when the request dict
     does not carry the cookies. `_exec_MioCookieExists` did not. That matters because a
     `new sh.X` route coerces the request to the SHAPE, which strips `__request_cookies__` from
     the request dict - so the cookies only survive on ctx. get() looked there. exists() did not.

So: exists() and get() disagreed about where cookies live, and the expression form never reached
either of them. Only a real round-trip - set a cookie, send it back, see if the server notices -
could catch that. This test does exactly that.
"""
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

PROGRAM = '''shape Probe
    command as text
shape: done

listen for
    new sh.Probe
        check miocookie.exists "probe_cookie"
            when true
                give back 200 "COOKIE SEEN"
            otherwise
                miocookie.set "probe_cookie" to "abc123"
                give back 200 "NO COOKIE"
        check: done
    new: done
listen: done
'''

PORT = 8797
_p = _f = 0


def check(label, got, want):
    global _p, _f
    ok = want in got
    _p += ok
    _f += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label} -- got {got!r}, want {want!r}")


def post(cookie=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/probe",
        data=b'{"command":"look"}',
        headers={"Content-Type": "application/json",
                 **({"Cookie": cookie} if cookie else {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read().decode()
    except urllib.error.HTTPError as e:
        return e.read().decode()
    except Exception:
        return ""          # server still warming up (the grammar compile is slow cold)


fd, path = tempfile.mkstemp(suffix=".mho")
os.write(fd, PROGRAM.encode())
os.close(fd)

env = dict(os.environ, PYTHONPATH=ROOT, DATABASE_URL=":memory:")
server = subprocess.Popen(
    [sys.executable, "mio.py", "serve", path, "--port", str(PORT), "--host", "127.0.0.1"],
    env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
try:
    body = ""
    for _ in range(40):                       # the grammar compile is slow on a cold cache
        time.sleep(1.5)
        body = post()
        if body:
            break

    # No cookie -> the server must NOT think one exists.
    check("no cookie sent -> server sets one", body, "NO COOKIE")

    # Send the cookie back. THIS is the case that was broken: it always said NO COOKIE.
    check("cookie sent back -> server sees it", post("probe_cookie=abc123"), "COOKIE SEEN")
finally:
    server.terminate()
    server.wait(timeout=10)
    os.unlink(path)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
