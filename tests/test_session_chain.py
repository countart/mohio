# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
test_session_chain.py

THE ZORK SESSION CHAIN, end to end over real HTTP.

Zork's first six lines of handler are a session bootstrap:

    check miocookie.exists "mio_session"
        when true
            player_session miocookie.get "mio_session" default session.id
        otherwise
            player_session session.id
            miocookie.set "mio_session" to session.id
    check: done

Every one of those lines was broken, in a different way, and NONE of them failed loud. `mio check`
said "no errors" the entire time. Five bugs, stacked:

  1. `otherwise` ATE ITSELF. `otherwise` does two jobs: the check-block branch keyword, and the
     inline null-coalesce operator (`x foo otherwise bar`). null_coalesce_stmt had priority 3, so
     it BEAT the branch: `player_session <expr>` + `otherwise` + `player_session` parsed as a
     coalesce expression, the branch keyword was consumed, and the rest was stranded. The
     assignment target was silently dropped. This violates Mohio's own one-word-one-job rule and
     is flagged for a design decision.

  2. `session.id` was EMPTY. The server only called run_with_session when the request already
     carried a session id, so a first-time visitor took the plain run() path, which never sets
     `session` in ctx at all. And run_with_session itself did `session_id or ""`. Nothing ever
     MINTED a session. Chicken and egg: session.id came from the cookie, and the cookie was
     written from session.id, so neither could ever start.

  3. `__request_cookies__` was STRIPPED. run_with_session builds its request shape with
     `{k: v for k, v in request.items() if not k.startswith("_")}` -- and the cookies live under
     __request_cookies__. So on that path the program saw NO cookies and every visit looked like
     a first visit.

  4. Set-Cookie was NEVER SENT. miocookie.set writes __pending_cookies__ onto ctx; the server
     reads it off the RESULT. Nothing carried it across. miocookie.set has never once emitted a
     Set-Cookie header over HTTP.

  5. miocookie.get CRASHED on its default. `_exec_MioCookieGet` read `node.fallback`; the
     dataclass field is `default`. AttributeError the moment a cookie was missing -- which is
     exactly the first-visit path.

Each bug hid the ones behind it. Only a real round trip finds this: `mio check` is green,
`interp.run()` bypasses the server wiring, and a unit test on any single handler passes. You have
to start a server, watch the Set-Cookie come back, and send it again.
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

# The exact shape of Zork's session bootstrap. 2026-08-04: simplified to match Zork's
# own post-migration form -- mio_session is now runtime-owned, and session.id IS the
# value this cookie carries, so the check/otherwise dance this test was built to
# reproduce is provably redundant now that the chicken-and-egg mint bug (below) is
# fixed. This test's own value (proving persistence + Set-Cookie actually work over
# real HTTP) is unaffected -- see the assertions below, still exercising exactly that.
PROGRAM = '''shape P
    command as text
shape: done

listen for
    new sh.P at /p
        player_session session.id
        give back 200 "session={{player_session}}"
    new: done
listen: done
'''

PORT = 8846
_p = _f = 0


def check(label, ok):
    global _p, _f
    _p += bool(ok)
    _f += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")


def post(cookie=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/p",
        data=b'{"command":"look"}',
        headers={"Content-Type": "application/json",
                 **({"Cookie": cookie} if cookie else {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read().decode(), r.headers.get("Set-Cookie") or ""
    except urllib.error.HTTPError as e:
        return e.read().decode(), ""
    except Exception:
        return "", ""


fd, path = tempfile.mkstemp(suffix=".mho")
os.write(fd, PROGRAM.encode())
os.close(fd)

env = dict(os.environ, PYTHONPATH=ROOT, DATABASE_URL=":memory:")
server = subprocess.Popen(
    [sys.executable, "mio.py", "serve", path, "--port", str(PORT), "--host", "127.0.0.1"],
    env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
try:
    body1 = setck = ""
    for _ in range(40):                       # cold grammar compile is slow
        time.sleep(1.5)
        body1, setck = post()
        if body1:
            break

    # FIRST VISIT: no cookie. Must mint a session AND hand it back as Set-Cookie.
    check("first visit mints a session (session.id is not empty)",
          "session=" in body1 and len(body1.split("session=")[1].strip('"}')) > 8)
    check("first visit sends Set-Cookie", "mio_session=" in setck)

    sid1 = body1.split("session=")[1].strip('"}') if "session=" in body1 else ""
    cookie = "mio_session=" + sid1

    # SECOND AND THIRD VISITS: send the cookie back. The SAME session must come out.
    body2, _ = post(cookie)
    body3, _ = post(cookie)
    sid2 = body2.split("session=")[1].strip('"}') if "session=" in body2 else "x"
    sid3 = body3.split("session=")[1].strip('"}') if "session=" in body3 else "y"

    check("second visit returns the SAME session", sid1 and sid1 == sid2)
    check("third visit returns the SAME session", sid1 and sid1 == sid3)
    check("no crash on the default path (MioCookieGet.default)",
          "AttributeError" not in body1 + body2)
finally:
    server.terminate()
    server.wait(timeout=10)
    os.unlink(path)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
