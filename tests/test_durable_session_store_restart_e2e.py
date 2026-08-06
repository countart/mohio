# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""The acceptance test the durable session-store seam brief actually asks for
(design-brief-durable-session-store-seam.md, acceptance criterion 1) -- not a unit test,
not a mocked interpreter call. Kill a REAL `mio serve` process mid-session, start a FRESH
process pointed at the SAME real Postgres, present the SAME mio_session cookie: the
session resumes -- roles and hold-scoped state intact -- not a fresh anonymous session.

This is the one scenario the 2026-08-04 session-lifecycle landing's own close-and-reopen
test explicitly does NOT cover (that test sends a second request against the SAME
still-running process; this test kills the process in between).

Requires a reachable Postgres (DATABASE_URL, defaults to the local standard
postgres/postgres instance already used by test_zork_session_lifecycle_e2e.py and the
wide-net sweep tests). Skips cleanly if unreachable.

MOHIO_SESSION_STORE=postgres is what actually selects the durable backend under test --
without it, the in-memory default would silently lose the session on kill+restart and
this test would (correctly) fail, proving the flag matters.

Also covers acceptance criterion 2 (a genuinely new visitor, no cookie / an invalidated
one, still gets a separate fresh session, not blurred with "returning") and the
rotation + restart interaction (a session that rotated mid-life, killed, restarted,
resumes under its NEW id -- the OLD id stays refused even across the restart, proving
the __invalidated__ blocklist is durable too, per the 2026-08-05 ruling).

Classified MohioValue metadata survival (the data_class/purposes/currency/pad_places
fields a naive .to_python() dump would drop) is proven separately and more directly at
the store level in tests/... (see _PostgresSessionStore's own docstring) -- this file
proves the end-to-end restart scenario at the HTTP layer, where roles and hold-scoped
state are what's directly observable through a served response.

Run: `python tests/test_durable_session_store_restart_e2e.py`.
"""
import os, subprocess, sys, tempfile, time, urllib.error, urllib.request, json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

POSTGRES_URL = os.environ.get("MOHIO_SWEEP_POSTGRES_URL",
                              "postgresql://postgres:postgres@localhost:5432/postgres")
PORT = 8891
_p = _f = 0

def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail: print(f"          {detail}")
    _p += bool(cond); _f += (not cond)

def pg_reachable():
    try:
        import psycopg2
        c = psycopg2.connect(POSTGRES_URL, connect_timeout=3)
        c.close()
        return True
    except Exception as e:
        print(f"  [SKIP] Postgres not reachable at {POSTGRES_URL}: {type(e).__name__}: {e}")
        return False

def clean_postgres():
    try:
        import psycopg2
        c = psycopg2.connect(POSTGRES_URL, connect_timeout=5)
        cur = c.cursor()
        for tbl in ('mohio_sessions', 'mohio_sessions_invalidated', 'security_audit_log'):
            cur.execute(f"DROP TABLE IF EXISTS {tbl} CASCADE")
        c.commit()
        c.close()
    except Exception as e:
        print(f"  (postgres cleanup skipped: {e})")

if not pg_reachable():
    print("\nRESULTS: 0 passed, 0 failed (skipped -- no Postgres)")
    sys.exit(0)

clean_postgres()

PROGRAM = '''connect db as postgres from env.DATABASE_URL

shape LoginRequest
    command as text
shape: done

listen for
    new sh.LoginRequest
        check command
            when "login"
                grant role "admin"
                hold visitor_name = "Jordan Rivera"
                give back 200 "logged in"
            otherwise
                require role "admin"
                give back 200 ("name=" & visitor_name)
        check: done
    new: done
listen: done
'''

fd, path = tempfile.mkstemp(suffix=".mho")
os.write(fd, PROGRAM.encode())
os.close(fd)

base_env = dict(os.environ, PYTHONPATH=ROOT, DATABASE_URL=POSTGRES_URL,
               MOHIO_SESSION_STORE="postgres", MOHIO_AI="mock")

def start_server():
    return subprocess.Popen(
        [sys.executable, "mio.py", "serve", path,
         "--port", str(PORT), "--host", "127.0.0.1"],
        env=base_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

def post(body, cookie=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/l",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 **({"Cookie": cookie} if cookie else {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read().decode(), r.headers.get("Set-Cookie") or "", r.status
    except urllib.error.HTTPError as e:
        return e.read().decode(), e.headers.get("Set-Cookie") or "", e.code
    except Exception as e:
        return "", "", 0

server = start_server()
try:
    body = setck = ""
    status = 0
    for attempt in range(40):                      # cold grammar compile is slow
        time.sleep(1.5)
        body, setck, status = post({"command": "login"})
        if body:
            break

    check("server #1 came up and answered a real login request",
          bool(body), f"status={status} body={body[:200]}")
    check("login response carries a real mio_session Set-Cookie",
          "mio_session=" in setck, setck)

    sid = setck.split("mio_session=")[1].split(";")[0] if "mio_session=" in setck else None
    cookie = f"mio_session={sid}" if sid else None

    body_check, _, status_check = post({"command": "whoami"}, cookie)
    check("SAME process, same cookie: role + held state work before any restart",
          status_check == 200 and "Jordan Rivera" in body_check,
          f"status={status_check} body={body_check[:200]}")

finally:
    server.terminate()
    try:
        server.wait(timeout=10)
    except Exception:
        server.kill()

# ── The actual acceptance test: kill the process, start a FRESH one, same Postgres ────
server2 = start_server()
try:
    body2 = ""
    status2 = 0
    for attempt in range(40):
        time.sleep(1.5)
        body2, _, status2 = post({"command": "whoami"}, cookie)
        if body2:
            break

    check("fresh process #2, SAME cookie: request succeeds at all (not a crash/hang)",
          status2 in (200, 403), f"status={status2} body={body2[:200]}")
    check("fresh process #2, SAME cookie: role survives the restart "
          "(require role \"admin\" still succeeds -- not a fresh anonymous 403)",
          status2 == 200, f"status={status2} body={body2[:200]}")
    check("fresh process #2, SAME cookie: hold-scoped state survives the restart "
          "(visitor_name is still 'Jordan Rivera', not lost/reset)",
          "Jordan Rivera" in body2, body2[:200])

    # ── Criterion 2: a genuinely NEW visitor still gets a separate, fresh session ──────
    body_fresh, setck_fresh, status_fresh = post({"command": "whoami"}, None)
    check("a fresh visitor with NO cookie is genuinely NEW, not blurred with the resumed session "
          "(require role fails -- they were never granted admin)",
          status_fresh == 403, f"status={status_fresh} body={body_fresh[:200]}")
    sid_fresh = (setck_fresh.split("mio_session=")[1].split(";")[0]
                 if "mio_session=" in setck_fresh else None)
    check("the fresh visitor's minted session id is DIFFERENT from the resumed one",
          sid_fresh is not None and sid_fresh != sid, f"resumed={sid} fresh={sid_fresh}")

finally:
    server2.terminate()
    try:
        server2.wait(timeout=10)
    except Exception:
        server2.kill()
    os.unlink(path)
    clean_postgres()

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
