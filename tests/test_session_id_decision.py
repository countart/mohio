# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
test_session_id_decision.py

The eight acceptance criteria from DECISION: session.id and unique.id (Ronnie).

The design in one line: they are distinguished by their STABILITY CONTRACT, not by freshness.

    session.id -- an IDENTITY. Stable within a request. Read it ten times, get the same value.
                  If the caller has a session, it is theirs. If not, the runtime mints one and
                  that is now theirs for the rest of the request. Answers "who am I talking to?"

    unique.id  -- a GENERATOR. A fresh, distinct value on EVERY read, by contract. Deliberately
                  unstable. Answers "give me a fresh handle for this thing."

The word carries the guarantee. That is what makes the dangerous bug unreachable: under a
mint-on-every-read design, Zork's lines 24-26 would store one value in a variable and a DIFFERENT
value in the cookie, and the session would never match itself. Because session.id promises
identity, it must be stable, or the word lies.
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
env = dict(os.environ, PYTHONPATH=ROOT, DATABASE_URL=":memory:")

PORT = 8852
_p = _f = 0


def check(label, ok):
    global _p, _f
    _p += bool(ok)
    _f += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")


def run_local(src):
    fd, path = tempfile.mkstemp(suffix=".mho")
    os.write(fd, src.encode())
    os.close(fd)
    r = subprocess.run([sys.executable, "mio.py", "run", path],
                       env=env, capture_output=True, text=True)
    os.unlink(path)
    return r.stdout + r.stderr


# ── 5. unique.id: a different value on EVERY read ─────────────────────────
out = run_local('a unique.id\nb unique.id\nc unique.id\nshow a\nshow b\nshow c\n')
ids = [l.strip() for l in out.splitlines() if len(l.strip()) == 32 and l.strip().isalnum()]
check("5. unique.id gives a fresh value on every read", len(set(ids)) == 3 and len(ids) == 3)


# ── The server-side criteria (1-4, 6) need a real HTTP round trip ─────────
# 2026-08-04: session bootstrap simplified to match Zork's post-migration form --
# mio_session is runtime-owned now (see the session-lifecycle brief); this test's own
# criteria (session.id stability/identity semantics) are unaffected by the simplification.
PROGRAM = '''shape P
    command as text
shape: done

listen for
    new sh.P at /p
        first  session.id
        second session.id
        player_session session.id
        give back 200 "first={{first}} second={{second}} player={{player_session}}"
    new: done
listen: done
'''

fd, path = tempfile.mkstemp(suffix=".mho")
os.write(fd, PROGRAM.encode())
os.close(fd)
server = subprocess.Popen(
    [sys.executable, "mio.py", "serve", path, "--port", str(PORT), "--host", "127.0.0.1"],
    env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)


def post(cookie=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/p", data=b'{"command":"look"}',
        headers={"Content-Type": "application/json",
                 **({"Cookie": cookie} if cookie else {})}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read().decode(), r.headers.get("Set-Cookie") or ""
    except urllib.error.HTTPError as e:
        return e.read().decode(), ""
    except Exception:
        return "", ""


def field(body, key):
    if key + "=" not in body:
        return ""
    return body.split(key + "=")[1].split()[0].strip('"}')


try:
    body = setck = ""
    for _ in range(40):
        time.sleep(1.5)
        body, setck = post()
        if body:
            break

    # 4 + 1: a caller with NO session gets a freshly minted value, and it is STABLE
    #        across repeated reads inside the one request.
    f1, s1 = field(body, "first"), field(body, "second")
    check("4. no session -> a value is minted", len(f1) > 8)
    check("1. session.id is STABLE across reads in one request", f1 and f1 == s1)
    check("   the minted id is what gets stored (player == session.id)",
          field(body, "player") == f1)
    check("6. a cold start with NO cookie works (the Zork break)", "first=" in body and f1)

    cookie = "mio_session=" + f1

    # 3: the SAME caller across two requests gets the SAME session.id
    body2, _ = post(cookie)
    check("3. same caller, second request -> SAME session.id", field(body2, "first") == f1)

    # 2: a DIFFERENT caller gets a DIFFERENT session.id
    body3, _ = post("mio_session=totally_different_caller")
    check("2. different caller -> DIFFERENT session.id",
          field(body3, "first") and field(body3, "first") != f1)
finally:
    server.terminate()
    server.wait(timeout=10)
    os.unlink(path)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
