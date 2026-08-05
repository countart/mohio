# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
test_quoted_path_failloud.py

Spec for the quoted-path fix. The path after `at` is an unquoted literal
(`at /about`). A quoted path (`at "/about"`) used to parse into a stray
assignment and silently serve nothing. Now:

  1. every path-taking construct fails loud on a quoted path, with the exact fix
  2. the unquoted form still parses and serves
  3. a quoted slash-string in the BODY is NOT a false positive
  4. `mio fmt` rewrites `at "/x"` -> `at /x` (and the fixed file then checks clean)

Run: PYTHONPATH=$PWD DATABASE_URL=:memory: python3 tests/test_quoted_path_failloud.py
 or: PYTHONPATH=$PWD python3 -m pytest tests/test_quoted_path_failloud.py -q
"""
import os, subprocess, sys, tempfile
os.environ.setdefault("DATABASE_URL", ":memory:")

from lark import Lark
from mohio_transformer_ast import transform, MohioCompileError
from mohio_interpreter import MohioInterpreter
from mohio_server import MohioServer, create_app
from starlette.testclient import TestClient

_raw = open("mohio.lark", encoding='utf-8').read()
_g = "\n".join(l for l in _raw.splitlines() if not l.strip().startswith("//"))
_P = Lark(_g, parser="earley", ambiguity="resolve", propagate_positions=True)


def _compile(src):
    return transform(_P.parse(src), src)


# ── 1. quoted paths fail loud, across every construct ────────────────────────

QUOTED_CASES = {
    "page": 'page at "/about"\n    give back 200 "hi"\npage: done\n',
    "request for": ('listen for\n    request for sh.X at "/y"\n'
                    '        give back 200 "ok"\n    request: done\nlisten: done\n'),
    "new": ('listen for\n    new sh.X at "/z"\n'
            '        give back 200 "ok"\n    new: done\nlisten: done\n'),
    "connection": ('listen for\n    connection at "/ws"\n'
                   '        give back 200 "ok"\n    connection: done\nlisten: done\n'),
}


def test_quoted_path_fails_loud_everywhere():
    for construct, src in QUOTED_CASES.items():
        try:
            _compile(src)
        except MohioCompileError as e:
            msg = str(e)
            assert "unquoted" in msg, f"{construct}: message missing 'unquoted': {msg}"
            assert "mio fmt" in msg, f"{construct}: message should point at mio fmt: {msg}"
        else:
            raise AssertionError(f"{construct}: quoted path did NOT fail loud")


# ── 2. unquoted paths still serve ────────────────────────────────────────────

def test_unquoted_path_serves():
    prog = _compile('page at /about\n    give back 200 "<h1>About</h1>"\npage: done\n')
    c = TestClient(create_app(MohioServer(prog, MohioInterpreter())),
                   raise_server_exceptions=False)
    r = c.get("/about")
    assert r.status_code == 200 and "About" in r.text, (r.status_code, r.text[:80])


# ── 3. a quoted slash-string in the BODY is not a false positive ─────────────

def test_body_slash_string_is_not_a_path():
    # The "/redirect" here is a give-back value, not an `at` path. Must compile+serve.
    prog = _compile('page at /go\n    give back 200 "/redirect"\npage: done\n')
    c = TestClient(create_app(MohioServer(prog, MohioInterpreter())),
                   raise_server_exceptions=False)
    assert c.get("/go").status_code == 200


# ── 4. mio fmt rewrites the quoted path, and the result checks clean ─────────

def test_fmt_rewrites_quoted_path():
    src = 'page at "/about"\n    give back 200 "<h1>About</h1>"\npage: done\n'
    with tempfile.NamedTemporaryFile("w", suffix=".mho", delete=False) as f:
        f.write(src)
        path = f.name
    try:
        env = dict(os.environ, PYTHONPATH=os.getcwd())
        subprocess.run([sys.executable, "mio.py", "fmt", path, "--write"],
                       env=env, capture_output=True, text=True, timeout=120)
        fixed = open(path, encoding='utf-8').read()
        assert 'page at /about' in fixed, f"fmt did not unquote: {fixed!r}"
        assert '"/about"' not in fixed, f"quote remained: {fixed!r}"
        chk = subprocess.run([sys.executable, "mio.py", "check", path],
                             env=env, capture_output=True, text=True, timeout=120)
        assert "no errors" in (chk.stdout + chk.stderr).lower(), chk.stdout[-200:]
    finally:
        os.unlink(path)


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS {name}")
            except Exception as e:
                failed += 1
                print(f"  FAIL {name}: {e}")
    print(f"\n{'ALL PASS' if not failed else str(failed) + ' FAILED'}")
    sys.exit(1 if failed else 0)
