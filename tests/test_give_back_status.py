# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
test_give_back_status.py

Guards the give-back status fix end-to-end through a real ASGI round-trip.

A bare status code after `give back` is the STATUS, with an empty body:
    give back 404          -> HTTP 404, empty body
    give back 500          -> HTTP 500, empty body
    give back ok           -> HTTP 200, empty body   (alias)
    give back 200 greeting -> HTTP 200, body "hello"  (status + value)
    give back 404 "gone"   -> HTTP 404, body "gone"   (status + value)

Two regressions this locks down:
  * Grammar/transformer: a lone status used to fall into the value slot, so
    `give back 404` returned HTTP 200 with the text "404" as the page body.
  * Runtime: the root GET handler used to swallow every empty-body / 404
    give-back into the neutral placeholder at 200, dropping the status. It must
    still fall through to that placeholder ONLY when there is genuinely no root
    route (the interpreter's `_no_route`), never for an explicit give-back.

Run:  PYTHONPATH=$PWD DATABASE_URL=:memory: python3 -m pytest tests/test_give_back_status.py -q
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


def _client(body: str) -> TestClient:
    source = "page at /\n" + body + "\npage: done\n"
    prog = transform(_PARSER.parse(source), source)
    app = create_app(MohioServer(prog, MohioInterpreter()))
    return TestClient(app, raise_server_exceptions=False)


def test_status_only_404_returns_404_empty():
    r = _client("    give back 404").get("/")
    assert r.status_code == 404
    assert r.text == ""


def test_status_only_500_returns_500_empty():
    r = _client("    give back 500").get("/")
    assert r.status_code == 500
    assert r.text == ""


def test_alias_ok_returns_200_empty():
    r = _client("    give back ok").get("/")
    assert r.status_code == 200
    assert r.text == ""


def test_alias_pending_returns_202():
    # `pending` is a status alias = 202 Accepted (added for the canonical
    # `give back pending "..."` form used in ai.decide / not confident).
    r = _client('    give back pending "Sent to a human"').get("/")
    assert r.status_code == 202
    assert "Sent to a human" in r.text


def test_status_plus_value_returns_status_and_body():
    r = _client('    greeting "hello"\n    give back 200 greeting').get("/")
    assert r.status_code == 200
    assert "hello" in r.text


def test_status_404_with_body():
    r = _client('    give back 404 "gone"').get("/")
    assert r.status_code == 404
    assert "gone" in r.text


def test_no_root_route_still_falls_through_to_placeholder():
    # A program with no `/` route must still show the neutral placeholder at 200,
    # never a bare 404 -- the explicit-give-back fix must not break this.
    source = ('journey App\n    page Other at /other\n        render\n'
              '            <p>elsewhere</p>\n        render: done\n    page: done\njourney: done\n')
    prog = transform(_PARSER.parse(source), source)
    app = create_app(MohioServer(prog, MohioInterpreter()))
    r = TestClient(app, raise_server_exceptions=False).get("/")
    assert r.status_code == 200
    assert "no home page yet" in r.text


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
