# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""T0-2: a connection string was being consumed as a filesystem path in three real sites, not
the two originally reported.

THE BUG (all three sites shared it): `mio run`/`mio serve` on a program with no `connect`
declaration at all fell back to `interp.setup_test_db()` (or, with `--seed`, `interp.seed_db()`),
both of which do a raw, scheme-blind `sqlite3.connect(self.db_path)`. `self.db_path` came from
`DATABASE_URL` with zero scheme check. `mio audit verify` did the identical thing directly
(`DbRuntime(target)` on a raw `DATABASE_URL`). Since Railway/Fly/Heroku set `DATABASE_URL`
project-wide, the simplest possible database-free Mohio app crash-looped on exactly the hosts
most likely to run it -- SQLite's own "unable to open database file", on an app that never asked
for a database. Live in production on getmohio.

THE FIX: a connect-less program must not open ANY database, regardless of DATABASE_URL --
`cmd_run`/`cmd_serve` (mio.py) now skip the auto-fallback entirely when the program declared no
`connect` (and fail loud, not silently, if `--seed` was also given with nothing to seed into).
`mio audit verify` (mio.py `cmd_audit`) now dispatches through the SAME resolver `connect`
already uses (`_make_db_runtime`, mohio_interpreter.py) via a new `_sniff_driver` that reads the
scheme off the connection string itself -- the one case with no explicit `connect ... as X` to
read a driver from. A bare `--file`/positional path is always sqlite (the documented form),
never scheme-sniffed.

A separate, unrelated bug in the same `mio audit verify` output: `audit_logs()` identified an
audit table purely by probing for three column names (`audit_id`/`prev_hash`/`entry_hash`), so
ANY ordinary table that happened to define columns with those names (seen with a table named
`recs`) was reported as a BROKEN hash chain -- it was never a chain at all. Fixed by requiring
BOTH the column probe AND `is_audit_table()` (mohio_audit_grades.py), the same name-convention
predicate every real audit writer in the interpreter is already held to.

Run: `python tests/test_database_url_resolver.py`. Needs no live Postgres -- the connect-less
cases use an unreachable-but-scheme-recognizable DATABASE_URL specifically so the test proves
"never even attempted a connection", not "the connection happened to fail". mio serve/audit
subprocess cases use a short timeout, not a real listener wait, so they stay fast and portable.
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.chdir(ROOT)

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)

# A DATABASE_URL with a real, scheme-recognizable Postgres form but an unreachable host --
# on purpose. If the connect-less guard ever regressed to "try to open whatever this is",
# this fails one way or another (a raw sqlite3.connect attempt on it, or a psycopg2 timeout
# trying to actually reach it) without needing a live Postgres server in this environment.
UNREACHABLE_PG_URL = "postgresql://nouser:nopass@203.0.113.1:5432/nodb?connect_timeout=3"

NOCONNECT_MHO = os.path.join(tempfile.gettempdir(), "t0_2_noconnect.mho")
with open(NOCONNECT_MHO, "w", encoding="utf-8") as f:
    f.write('show "hello from a database-free app"\n')

SEED_JSON = os.path.join(tempfile.gettempdir(), "t0_2_seed.json")
with open(SEED_JSON, "w", encoding="utf-8") as f:
    json.dump({"items": [{"id": "1"}]}, f)


def run_cli(args, env_extra=None, timeout=20):
    env = dict(os.environ)
    env.pop("DATABASE_URL", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "mio.py"] + args,
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout,
    )


print("=== 1. no-connect app ignores DATABASE_URL entirely (mio run) ===")

r = run_cli(["run", NOCONNECT_MHO])
check("control: DATABASE_URL unset -> runs fine",
      r.returncode == 0 and "hello from a database-free app" in r.stdout, r.stdout + r.stderr)

r = run_cli(["run", NOCONNECT_MHO], {"DATABASE_URL": UNREACHABLE_PG_URL})
check("connect-less + unreachable Postgres-shaped DATABASE_URL -> still runs fine, no attempt made",
      r.returncode == 0 and "hello from a database-free app" in r.stdout, r.stdout + r.stderr)

r2 = run_cli(["run", NOCONNECT_MHO, "--seed", SEED_JSON], {"DATABASE_URL": UNREACHABLE_PG_URL})
check("connect-less + --seed -> fails loud (no database declared to seed into), not silently sqlite",
      r2.returncode != 0 and "no `connect`" in (r2.stdout + r2.stderr), r2.stdout + r2.stderr)


print("\n=== 2. no-connect app ignores DATABASE_URL entirely (mio serve) ===")

proc = subprocess.Popen(
    [sys.executable, "mio.py", "serve", NOCONNECT_MHO, "--port", "8897"],
    cwd=ROOT, env={**os.environ, "DATABASE_URL": UNREACHABLE_PG_URL},
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
)
time.sleep(2.5)
alive = proc.poll() is None
if alive:
    proc.terminate()
    try:
        out = proc.communicate(timeout=5)[0]
    except subprocess.TimeoutExpired:
        proc.kill()
        out = ""
else:
    out = proc.communicate()[0]
check("connect-less + unreachable Postgres-shaped DATABASE_URL -> server boots and stays up",
      alive, out)


print("\n=== 3. mio audit verify dispatches by scheme (_sniff_driver), not raw sqlite3.connect ===")
from mohio_interpreter import _sniff_driver

check("postgres:// -> postgres", _sniff_driver("postgres://h/d") == "postgres")
check("postgresql:// -> postgres", _sniff_driver("postgresql://h/d") == "postgres")
check("mysql:// -> mysql", _sniff_driver("mysql://h/d") == "mysql")
check("mariadb:// -> mysql", _sniff_driver("mariadb://h/d") == "mysql")
check("mongodb:// -> mongo", _sniff_driver("mongodb://h/d") == "mongo")
check("mongodb+srv:// -> mongo", _sniff_driver("mongodb+srv://h/d") == "mongo")
check("bare filesystem path -> sqlite", _sniff_driver("/some/app.db") == "sqlite")
check("bare :memory: -> sqlite", _sniff_driver(":memory:") == "sqlite")
check("empty/None -> sqlite (unchanged default)", _sniff_driver(None) == "sqlite")

r = run_cli(["audit", "verify"], {"DATABASE_URL": UNREACHABLE_PG_URL})
check("mio audit verify against an unreachable Postgres DSN fails as a POSTGRES error, "
      "never as sqlite's 'unable to open database file'",
      "unable to open database file" not in (r.stdout + r.stderr), r.stdout + r.stderr)


print("\n=== 4. audit_logs() distinguishes a real audit table from an ordinary one (the `recs` bug) ===")
from mohio_interpreter import MohioInterpreter, DbRuntime
from mohio_audit_grades import canonical_audit_columns

RECS_DB = os.path.join(tempfile.gettempdir(), "t0_2_recs_test.db")
if os.path.exists(RECS_DB):
    os.remove(RECS_DB)
conn = sqlite3.connect(RECS_DB)
cur = conn.cursor()
# An ordinary table, never written by any audit writer, that happens to define columns
# with the same names the chain-column probe looks for -- name collision only, not a chain.
cur.execute('CREATE TABLE recs (id INTEGER PRIMARY KEY, audit_id TEXT, prev_hash TEXT, entry_hash TEXT)')
cur.execute("INSERT INTO recs (audit_id, prev_hash, entry_hash) VALUES ('r1','not-a-hash-1','not-a-hash-2')")
conn.commit()
conn.close()

sink = DbRuntime(RECS_DB)
it = MohioInterpreter()
sink.ensure_table('fraud_audit_log', canonical_audit_columns())
logs = it.audit_logs(sink)
check("ordinary `recs` table excluded from audit_logs()", 'recs' not in logs, logs)
check("a real audit table (fraud_audit_log) still found", 'fraud_audit_log' in logs, logs)


for _f_path in (NOCONNECT_MHO, SEED_JSON, RECS_DB):
    try:
        os.remove(_f_path)
    except OSError:
        pass

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
