# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
test_directory_mode.py

Guards `mio serve <dir>` and `mio check <dir>` -- the whole-directory feature the
getmohio platform deploys against.

Three regressions this locks down:
  * create_multi_app routes each mapped URL to the right per-file app (/ vs /about).
  * Static assets beside the .mho files are served, because the served directory is
    threaded through as app_dir. Without it, `GET /style.css` returned empty (static
    resolved against the process cwd, not the app dir).
  * `mio serve <dir>` actually launches and STAYS UP (the directory branch used to
    build the app and fall through, so the process exited and nothing listened).
  * `mio check <dir>` validates every .mho in the tree instead of crashing with
    IsADirectoryError, and its exit code reflects pass/fail.

Run:  PYTHONPATH=$PWD DATABASE_URL=:memory: python3 -m pytest tests/test_directory_mode.py -q
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

_THIS_DIR = Path(__file__).parent.resolve()
_PROJECT = _THIS_DIR.parent
if not (_PROJECT / "mio.py").exists():
    _PROJECT = _THIS_DIR
sys.path.insert(0, str(_PROJECT))
import mohio_data
os.environ.setdefault("DATABASE_URL", ":memory:")

try:
    from lark import Lark
    from mohio_transformer_ast import transform
    from mohio_interpreter import MohioInterpreter
    from mohio_server import create_multi_app
    from starlette.testclient import TestClient
except ImportError as e:
    pytest.skip(f"Missing dependency: {e}", allow_module_level=True)

_clean = "\n".join(
    l for l in mohio_data.GRAMMAR_PATH.read_text(encoding="utf-8").splitlines()
    if not l.strip().startswith("//")
)
_PARSER = Lark(_clean, parser="earley", ambiguity="resolve", propagate_positions=True)

_INDEX = 'page at /\n    give back 200 "<h1>Index Page</h1>"\npage: done\n'
_ABOUT = 'page at /about\n    give back 200 "<h1>About Page</h1>"\npage: done\n'
_CSS = "body { color: rebeccapurple; }\n"


def _prog(src):
    program = transform(_PARSER.parse(src), src)
    interp = MohioInterpreter()
    interp.run_declarations(program)
    return program, interp


def _multi_client(app_dir=None):
    pi = _prog(_INDEX)
    pa = _prog(_ABOUT)
    programs = {"/": pi[0], "/about": pa[0]}
    interps = {"/": pi[1], "/about": pa[1]}
    app = create_multi_app(programs, interps, app_dir=app_dir)
    return TestClient(app, raise_server_exceptions=False)


def test_multi_app_routes_index_and_about():
    c = _multi_client()
    assert "Index Page" in c.get("/").text
    assert "About Page" in c.get("/about").text


def test_multi_app_serves_static_from_app_dir(tmp_path):
    (tmp_path / "style.css").write_text(_CSS, encoding="utf-8")
    c = _multi_client(app_dir=str(tmp_path))
    r = c.get("/style.css")
    assert r.status_code == 200
    assert "rebeccapurple" in r.text
    assert "text/css" in r.headers.get("content-type", "")


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _write_app(dirpath):
    (dirpath / "index.mho").write_text(_INDEX, encoding="utf-8")
    (dirpath / "about.mho").write_text(_ABOUT, encoding="utf-8")
    (dirpath / "style.css").write_text(_CSS, encoding="utf-8")


def test_serve_directory_stays_up_and_serves_all_three(tmp_path):
    _write_app(tmp_path)
    port = _free_port()
    env = dict(os.environ, PYTHONPATH=str(_PROJECT), DATABASE_URL=":memory:",
               MOHIO_ENCRYPTION_KEY="testkey")
    proc = subprocess.Popen(
        [sys.executable, "mio.py", "serve", str(tmp_path), "--port", str(port)],
        cwd=str(_PROJECT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        base = f"http://127.0.0.1:{port}"
        up = False
        for _ in range(60):  # up to ~30s for first-run Earley compile
            if proc.poll() is not None:
                pytest.fail("serve process exited before binding (the launch bug)")
            try:
                urllib.request.urlopen(base + "/", timeout=1).read()
                up = True
                break
            except Exception:
                time.sleep(0.5)
        assert up, "server never came up"

        assert "Index Page" in urllib.request.urlopen(base + "/", timeout=5).read().decode()
        assert "About Page" in urllib.request.urlopen(base + "/about", timeout=5).read().decode()
        css = urllib.request.urlopen(base + "/style.css", timeout=5).read().decode()
        assert "rebeccapurple" in css
        # Still listening after serving requests (did not exit).
        assert proc.poll() is None
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


def _run_check(target):
    env = dict(os.environ, PYTHONPATH=str(_PROJECT), DATABASE_URL=":memory:",
               MOHIO_ENCRYPTION_KEY="testkey")
    return subprocess.run([sys.executable, "mio.py", "check", str(target)],
                          cwd=str(_PROJECT), env=env,
                          capture_output=True, text=True)


def test_check_directory_passes_clean_tree(tmp_path):
    _write_app(tmp_path)
    r = _run_check(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "files passed" in (r.stdout + r.stderr)


def test_check_directory_fails_on_broken_file(tmp_path):
    _write_app(tmp_path)
    # `give back` with no value is a hard error -- the whole-dir check must catch it.
    (tmp_path / "bad.mho").write_text("page at /bad\n    give back\npage: done\n",
                                      encoding="utf-8")
    r = _run_check(tmp_path)
    assert r.returncode == 1
    assert "bad.mho" in (r.stdout + r.stderr)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
