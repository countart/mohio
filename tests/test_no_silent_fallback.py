# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
Guard: two silent failures that made an app look healthy while being wrong.

1. A program saying `connect db as postgres` with no DATABASE_URL used to start on
   SQLite and answer normally. The fallback target was `:memory:`, so on a host that
   sleeps machines the data did not merely go to the wrong database, it disappeared at
   the next restart while every request still returned 200. A declared backend now
   refuses to start without its connection string.

2. A file sitting beside the app was downloadable by anyone who guessed the name --
   for a game, the seed with the answers in it. The `_name` convention already meant
   "private, never routed" for .mho files and meant nothing for anything else. It now
   means the same thing for every file type.
"""
import os, sys, subprocess, tempfile, shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("MOHIO_ENCRYPTION_KEY", "testkey")

_passed = _failed = 0
def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1; print(f"  ok   {label}")
    else:
        _failed += 1; print(f"  FAIL {label}: got {got!r} want {want!r}")

print("test_no_silent_fallback")

# ---------------------------------------------------------------- database
from mohio_interpreter import _make_db_runtime, DbRuntime

def make(driver, env):
    """Build a runtime with a controlled environment. Returns (ok, detail)."""
    saved = {k: os.environ.get(k) for k in
             ("DATABASE_URL", "MYSQL_URL", "MONGO_URL", "MONGODB_URL")}
    try:
        for k in saved:
            os.environ.pop(k, None)
        for k, v in env.items():
            os.environ[k] = v
        try:
            return True, type(_make_db_runtime(driver)).__name__
        except RuntimeError as e:
            return False, str(e)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

ok, detail = make("postgres", {})
check("postgres with no DATABASE_URL refuses", ok, False)
check("the refusal names the variable", "DATABASE_URL" in detail, True)
check("the refusal explains the wrong-database risk",
      "wrong database" in detail.lower(), True)
check("the refusal offers the real alternative", "as sqlite" in detail, True)

ok, _ = make("postgres", {"DATABASE_URL": "   "})
check("a blank DATABASE_URL is still missing", ok, False)

ok, _ = make("mysql", {})
check("mysql with no url refuses", ok, False)
ok, _ = make("mongodb", {})
check("mongodb with no url refuses", ok, False)

ok, name = make("sqlite", {})
check("sqlite asked for by name still works", (ok, name), (True, "DbRuntime"))
ok, name = make("", {})
check("no backend named still defaults to sqlite", (ok, name), (True, "DbRuntime"))

ok, detail = make("postgress", {})
check("a misspelled backend refuses instead of becoming sqlite", ok, False)
check("the refusal names the typo", "postgress" in detail, True)

# ---------------------------------------------------------------- static files
PAGE = 'page at /\n    show "home"\npage: done\n'

def serve_and_get(paths):
    """Serve a directory and GET each path. Returns {path: status}."""
    import socket, time, urllib.request, urllib.error
    d = tempfile.mkdtemp(prefix="mohio_static_")
    out = {}
    proc = None
    try:
        open(os.path.join(d, "index.mho"), "w").write(PAGE)
        open(os.path.join(d, "_seed.json"), "w").write('{"answers":"secret"}')
        open(os.path.join(d, "manifest.json"), "w").write('{"name":"app"}')
        open(os.path.join(d, "style.css"), "w").write("body{}")
        # The source is denied as `.mho`; its parse cache must be denied too, or the
        # front door is closed while the same content leaves by the side one.
        open(os.path.join(d, "index.mho.cache"), "wb").write(b"pickled SECRET")
        os.makedirs(os.path.join(d, "_data"), exist_ok=True)
        open(os.path.join(d, "_data", "rooms.json"), "w").write('{"x":1}')
        # A DOT-prefixed folder is private, and that is the way to hide a directory.
        # The underscore had to be freed up for `_next/`, so the two markers now mean
        # different things: `.folder/` is hidden, `_folder/` is not, `_file` is.
        os.makedirs(os.path.join(d, ".private"), exist_ok=True)
        open(os.path.join(d, ".private", "seed.json"), "w").write('{"secret":1}')
        s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
        env = dict(os.environ, PYTHONPATH=ROOT, DATABASE_URL=":memory:")
        proc = subprocess.Popen(
            [sys.executable, os.path.join(ROOT, "mio.py"), "serve",
             os.path.join(d, "index.mho"), "--port", str(port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env, cwd=d)
        base = f"http://127.0.0.1:{port}"
        for _ in range(120):
            try:
                urllib.request.urlopen(base + "/", timeout=1); break
            except urllib.error.HTTPError:
                break
            except Exception:
                time.sleep(0.5)
        for p in paths:
            try:
                out[p] = urllib.request.urlopen(f"{base}/{p}", timeout=5).status
            except urllib.error.HTTPError as e:
                out[p] = e.code
            except Exception:
                out[p] = 0
        return out
    finally:
        if proc:
            proc.terminate()
            try: proc.wait(timeout=10)
            except Exception: proc.kill()
        shutil.rmtree(d, ignore_errors=True)

st = serve_and_get(["_seed.json", "_data/rooms.json", ".private/seed.json",
                    "manifest.json", "style.css", "index.mho.cache", "index.mho"])
check("the source is not served", st.get("index.mho"), 404)
check("its parse cache is not served either", st.get("index.mho.cache"), 404)

# Four families that each got past the old check, which looked only at the LAST
# extension of a name.
from mohio_server import _is_denied_static_name as _denied
for _n in ("app.db-wal", "app.db-shm", "app.db-journal", "app.sqlite-journal"):
    check(f"sqlite sidecar {_n} is refused", _denied(_n), True)   # holds live writes
for _n in ("backup.sql.gz", "dump.sql.zip", "app.log.1", "secret.env.gz"):
    check(f"wrapped {_n} is refused", _denied(_n), True)          # compound suffix
for _n in ("id_rsa", "id_ed25519", "authorized_keys", "server.p8", "app.jks"):
    check(f"key material {_n} is refused", _denied(_n), True)     # often no extension
for _n in ("app.mho~", "secrets.yaml.old", "core.dump", "notes.tmp"):
    check(f"leftover {_n} is refused", _denied(_n), True)         # editor and backups
for _n in ("manifest.json", "style.css", "app.js", "notes.md", "data.csv", "logo.png"):
    check(f"ordinary {_n} still serves", _denied(_n), False)
check("an underscore file is not served", st.get("_seed.json"), 404)
# Underscore-private governs FILES, not folders. A whole-directory rule would deny
# `_next/`, where every Next.js asset lives, and break a built front end served
# through Mohio with no message. The tradeoff is deliberate and worth stating: a
# secret inside an underscore FOLDER is no longer hidden by the folder name alone.
# The extension denylist still refuses source, config and data wherever they sit, so
# name the file itself `_seed.json` rather than relying on `_data/` to cover it.
check("an underscore FOLDER no longer hides its contents",
      st.get("_data/rooms.json"), 200)
check("a DOT folder is private -- this is how to hide a directory",
      st.get(".private/seed.json"), 404)
check("an ordinary json still serves", st.get("manifest.json"), 200)
check("an ordinary asset still serves", st.get("style.css"), 200)

print(f"\nRESULTS: {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
