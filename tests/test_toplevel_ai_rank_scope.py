# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
test_toplevel_ai_rank_scope.py

Guards the fix that makes a top-level `ai.rank` a declaration: it computes once
when the base context is built and binds its winner into app scope, so a handler
can `give back ok <name>`. Before the fix, a top-level ai.rank was neither a
declaration nor a listener, so it never ran on the serve path and the handler saw
the name as undefined -- which silently became "(no response was generated)".

  * top-level ai.rank result, give back in handler  -> the ranked winner
  * ai.rank INSIDE a handler still works (per-request, unchanged)
  * top-level `hold` still visible in a handler (unchanged)

Run:  PYTHONPATH=$PWD DATABASE_URL=:memory: python3 -m pytest tests/test_toplevel_ai_rank_scope.py -q
"""
from __future__ import annotations

import os
import sys
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
    from mohio_server import MohioServer, create_app
    from starlette.testclient import TestClient
except ImportError as e:
    pytest.skip(f"Missing dependency: {e}", allow_module_level=True)

_clean = "\n".join(
    l for l in mohio_data.GRAMMAR_PATH.read_text(encoding="utf-8").splitlines()
    if not l.strip().startswith("//")
)
_PARSER = Lark(_clean, parser="earley", ambiguity="resolve", propagate_positions=True)


def _get(src, path="/q"):
    prog = transform(_PARSER.parse(src), src)
    app = create_app(MohioServer(prog, MohioInterpreter()))
    r = TestClient(app, raise_server_exceptions=False).get(path)
    try:
        data = r.json()
        return data.get("message", data.get("body", ""))
    except Exception:
        return r.text.strip()


_RANK = ('option "a"\n    option "b" weight 2\n')


def test_toplevel_ai_rank_visible_in_handler():
    src = (
        "shape Q\nshape: done\n\n"
        "ai.rank result returns text\n    " + _RANK + "ai.rank: done\n\n"
        "listen for\n    request for sh.Q at /q\n"
        "        give back ok result\n    request: done\nlisten: done\n"
    )
    assert _get(src) == "b"


def test_ai_rank_inside_handler_still_works():
    src = (
        "shape Q\nshape: done\n"
        "listen for\n    request for sh.Q at /q\n"
        "        ai.rank result returns text\n            " + _RANK +
        "        ai.rank: done\n"
        "        give back ok result\n    request: done\nlisten: done\n"
    )
    assert _get(src) == "b"


def test_toplevel_hold_still_visible_in_handler():
    src = (
        'hold config "production"\n'
        "shape Q\nshape: done\n"
        "listen for\n    request for sh.Q at /q\n"
        "        give back ok config\n    request: done\nlisten: done\n"
    )
    assert _get(src) == "production"


def test_bare_toplevel_assignment_does_NOT_run_at_startup():
    # Rule B (locked): only declarations run at startup. A bare top-level assignment
    # is not a declaration, so it does NOT run on the serve path and is not visible
    # to handlers -- the canonical way to introduce a setup value is `hold`. This
    # guards against re-introducing the general top-level-execution shortcut.
    src = (
        'result "b"\n'
        "shape Q\nshape: done\n"
        "listen for\n    request for sh.Q at /q\n"
        "        give back ok result\n    request: done\nlisten: done\n"
    )
    # 'result' never runs at startup, so the handler produces an empty body.
    assert _get(src) != "b"


def test_hold_is_the_canonical_startup_value():
    # The taught, canonical way to introduce a top-level setup value is `hold`,
    # which IS a declaration and runs at startup -- visible to every handler.
    src = (
        'hold result "b"\n'
        "shape Q\nshape: done\n"
        "listen for\n    request for sh.Q at /q\n"
        "        give back ok result\n    request: done\nlisten: done\n"
    )
    assert _get(src) == "b"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
