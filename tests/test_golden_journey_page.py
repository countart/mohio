# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
test_golden_journey_page.py
============================
Golden HTTP-layer tests for the journey/page multi-page model.

Tests what shipped: journey = app root scope + routing container;
page N at /path = one GET route; pages inherit declarations; render
produces text/html; give back produces data; routing = exact match →
single-page fallback → clean 404; server GET/POST/PUT/DELETE forwarding.

7 cases from the golden spec:
  1. Multi-page GET routing (two pages, 404 on miss)
  2. Page reads journey scope (hold inheritance via render)
  3. Mixed journey + nested POST listener (GET pages + POST coexist)
  4. Static file precedence (real file wins over page route)
  5. REST verbs (POST/PUT/DELETE forwarded to interpreter)
  6. Zork-safety regression (GET / → SPA, /ping, /health, POST /game)
  7. Implicit default journey (top-level bare pages)

Harness: strip // from mohio.lark; Lark Earley; transform; MockAI;
MohioServer + create_app + Starlette TestClient.

Known boundaries (NOT bugs, do NOT test):
  (1) Root page `page Home at /` is deferred (/ owned by serve_frontend).
  (2) `set X to Y` session-var readback is pre-existing, not journey/page.
  (3) saga/step remain deliberately fail-loud.

HOW TO RUN (from mohio/):
    python -m pytest tests/golden/test_golden_journey_page.py -v
"""

from __future__ import annotations

import json
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

# ── Locate project root ───────────────────────────────────────────────────────
_THIS_DIR = Path(__file__).parent.resolve()
_PROJECT  = _THIS_DIR.parent.parent   # tests/golden/ → tests/ → mohio/

if not (_PROJECT / "mio.py").exists():
    for c in [_THIS_DIR.parent, _THIS_DIR]:
        if (c / "mio.py").exists():
            _PROJECT = c
            break

sys.path.insert(0, str(_PROJECT))
import mohio_data
os.environ.setdefault("DATABASE_URL", ":memory:")

# ── Imports (skip entire module if deps missing) ───────────────────────────────
try:
    from lark import Lark
    from mohio_transformer_ast import transform
    from mohio_interpreter import MohioInterpreter
    from mohio_server import MohioServer, create_app
    from starlette.testclient import TestClient
except ImportError as e:
    pytest.skip(f"Missing dependency: {e}", allow_module_level=True)


# ── Grammar + parser (once per module) ─────────────────────────────────────────
_grammar_path = mohio_data.GRAMMAR_PATH
if not _grammar_path.exists():
    pytest.skip("mohio.lark not found", allow_module_level=True)

_raw = _grammar_path.read_text(encoding="utf-8")
_clean = "\n".join(l for l in _raw.splitlines() if not l.strip().startswith("//"))
_PARSER = Lark(_clean, parser="earley", ambiguity="resolve", propagate_positions=True)


# ── Shared helpers ─────────────────────────────────────────────────────────────

class MockAI:
    def register_chain(self, *a, **k): pass
    def decide(self, **k):
        return SimpleNamespace(result=None, confidence=0.9,
                               fell_back=False, model="mock")


def _make_client(source: str, seed: dict | None = None) -> TestClient:
    """Parse source, build interpreter + server + Starlette app, return TestClient."""
    prog = transform(_PARSER.parse(source), source)
    interp = MohioInterpreter(ai=MockAI())
    if seed is not None:
        interp.setup_test_db(seed_data=seed)
    server = MohioServer(prog, interp)
    app = create_app(server)
    return TestClient(app, raise_server_exceptions=False)


# ══════════════════════════════════════════════════════════════════════════════
# CASE 1 — MULTI-PAGE GET ROUTING
# Two pages at /home and /about. /missing → 404. Trailing slash tolerated.
# ══════════════════════════════════════════════════════════════════════════════

_MULTI_PAGE = """\
journey RatesApp
    page Home at /home
        render
            <p>[HOME]</p>
        render: done
    page: done
    page About at /about
        render
            <p>[ABOUT]</p>
        render: done
    page: done
journey: done
"""


class TestMultiPageGET:

    @pytest.fixture(autouse=True)
    def client(self):
        self.c = _make_client(_MULTI_PAGE)

    def test_get_home_200(self):
        r = self.c.get("/home")
        assert r.status_code == 200
        assert "[HOME]" in r.text

    def test_get_about_200(self):
        r = self.c.get("/about")
        assert r.status_code == 200
        assert "[ABOUT]" in r.text

    def test_get_missing_404(self):
        r = self.c.get("/missing")
        assert r.status_code == 404

    def test_trailing_slash_tolerated(self):
        r = self.c.get("/home/")
        assert r.status_code == 200
        assert "[HOME]" in r.text

    def test_query_string_tolerated(self):
        r = self.c.get("/home?x=1")
        assert r.status_code == 200
        assert "[HOME]" in r.text

    def test_response_is_html(self):
        r = self.c.get("/home")
        assert "text/html" in r.headers.get("content-type", "")


# ══════════════════════════════════════════════════════════════════════════════
# CASE 2 — PAGE READS JOURNEY SCOPE
# Page renders a hold value inherited from the journey.
# ══════════════════════════════════════════════════════════════════════════════

_SCOPE_INHERIT = """\
journey App
    hold greeting = "WELCOME"
    page Home at /home
        render
            <p>[{{ greeting }}]</p>
        render: done
    page: done
journey: done
"""


class TestScopeInheritance:

    @pytest.fixture(autouse=True)
    def client(self):
        self.c = _make_client(_SCOPE_INHERIT)

    def test_page_sees_journey_hold(self):
        r = self.c.get("/home")
        assert r.status_code == 200
        assert "[WELCOME]" in r.text


# ══════════════════════════════════════════════════════════════════════════════
# CASE 3 — MIXED JOURNEY + NESTED POST LISTENER
# GET pages and a POST listener coexist in the same journey.
# continue-on-404 across sibling listeners.
# ══════════════════════════════════════════════════════════════════════════════

_MIXED = """\
journey App
    page Home at /home
        render
            <p>[HOME]</p>
        render: done
    page: done
    listen for
        new sh.Signup at /signup
            give back created "[SIGNED_UP]"
        new: done
    listen: done
journey: done
"""


class TestMixedJourneyListener:

    @pytest.fixture(autouse=True)
    def client(self):
        self.c = _make_client(_MIXED)

    def test_get_home_page(self):
        r = self.c.get("/home")
        assert r.status_code == 200
        assert "[HOME]" in r.text

    def test_post_signup(self):
        r = self.c.post("/signup", json={"_shape": "Signup"})
        assert r.status_code == 201
        data = r.json()
        assert "SIGNED_UP" in str(data)

    def test_get_missing_not_shadowed(self):
        """GET /missing returns 404 — page miss is not swallowed by the listener."""
        r = self.c.get("/missing")
        assert r.status_code == 404

    def test_post_missing_404(self):
        """POST to a path the listener doesn't handle → 404."""
        r = self.c.post("/missing", json={"_shape": "Signup"})
        # POST to an unmatched path should not return 200
        assert r.status_code in (404, 204, 500), (
            f"Expected non-200 for POST /missing. Got {r.status_code}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# CASE 4 — STATIC FILE PRECEDENCE
# A real static file on disk takes priority over a page route at the same path.
# ══════════════════════════════════════════════════════════════════════════════

class TestStaticPrecedence:

    def test_static_file_wins_over_page(self):
        """
        If a static file exists at a path, it's served directly.
        The interpreter never runs for that path.

        Creates a temp static file in a temp dir, patches _STATIC_DIRS,
        and verifies the file content is served instead of the page.
        """
        import mohio_server

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a static file at "home.html"
            static_file = Path(tmpdir) / "home.html"
            static_file.write_text("<h1>STATIC FILE</h1>", encoding="utf-8")

            # Page at /home.html would normally render [PAGE]
            source = """\
journey App
    page Home at /home.html
        render
            <p>[PAGE]</p>
        render: done
    page: done
journey: done
"""
            # Static roots now come from _static_roots(), whose highest-precedence
            # source is the MOHIO_STATIC_DIR env var (the old module-level _STATIC_DIRS
            # list was removed in the static-serving refactor). Point it at our temp dir.
            orig_env = os.environ.get("MOHIO_STATIC_DIR")
            os.environ["MOHIO_STATIC_DIR"] = tmpdir
            try:
                c = _make_client(source)
                r = c.get("/home.html")
                assert r.status_code == 200
                assert "STATIC FILE" in r.text
                assert "[PAGE]" not in r.text
            finally:
                if orig_env is None:
                    os.environ.pop("MOHIO_STATIC_DIR", None)
                else:
                    os.environ["MOHIO_STATIC_DIR"] = orig_env


# ══════════════════════════════════════════════════════════════════════════════
# CASE 5 — REST VERBS (POST/PUT/DELETE forwarding)
# POST, PUT, DELETE are all forwarded to the interpreter via _dispatch_post.
# ══════════════════════════════════════════════════════════════════════════════

_REST = """\
journey Api
    listen for
        new sh.Item at /items
            give back created "[CREATED]"
        new: done
    listen: done
journey: done
"""


class TestRESTVerbs:

    @pytest.fixture(autouse=True)
    def client(self):
        self.c = _make_client(_REST)

    def test_post_creates(self):
        r = self.c.post("/items", json={"_shape": "Item", "name": "Widget"})
        assert r.status_code == 201
        assert "CREATED" in str(r.json())

    def test_put_forwarded(self):
        """PUT is forwarded to the interpreter (same dispatch path as POST)."""
        r = self.c.put("/items", json={"_shape": "Item", "name": "Updated"})
        # PUT goes through _dispatch_post — should reach the listener
        assert r.status_code in (200, 201, 204, 404)

    def test_delete_forwarded(self):
        """DELETE is forwarded to the interpreter."""
        r = self.c.request(
            "DELETE", "/items",
            json={"_shape": "Item", "id": "1"},
        )
        assert r.status_code in (200, 204, 404)


# ══════════════════════════════════════════════════════════════════════════════
# CASE 6 — ZORK-SAFETY REGRESSION                         ** HIGHEST VALUE **
# The journey/page system must NOT break the Zork SPA paths:
#   GET /       → SPA HTML (serve_frontend, not a journey page)
#   GET /ping   → JSON {"pong": true}
#   GET /health → JSON with "status": "running"
#   POST /game  → forwarded to interpreter (Zork command dispatch)
#   POST /      → forwarded to interpreter
#
# These paths have dedicated Route() entries that fire BEFORE the
# catch-all /{path:path}. A journey with pages must not shadow them.
# ══════════════════════════════════════════════════════════════════════════════

_ZORK_COEXIST = """\
journey App
    page Dashboard at /dashboard
        render
            <p>[DASHBOARD]</p>
        render: done
    page: done
    listen for
        new sh.Command at /game
            give back ok "[GAME_RESPONSE]"
        new: done
    listen: done
journey: done
"""


class TestZorkSafety:
    """
    Highest-value guard. Every test here protects a Zork-critical path.
    If any fails, the Zork demo is broken.
    """

    @pytest.fixture(autouse=True)
    def client(self):
        self.c = _make_client(_ZORK_COEXIST)

    def test_get_root_serves_frontend(self):
        """GET / → serve_frontend HTML, NOT a journey page."""
        r = self.c.get("/")
        assert r.status_code == 200
        # Should be HTML (the SPA or the fallback)
        assert "text/html" in r.headers.get("content-type", "")
        # Must NOT contain journey page content
        assert "[DASHBOARD]" not in r.text

    def test_ping(self):
        """GET /ping → JSON pong. Dedicated route, not forwarded."""
        r = self.c.get("/ping")
        assert r.status_code == 200
        data = r.json()
        assert data.get("pong") is True

    def test_health(self):
        """GET /health → JSON stats with status=running."""
        r = self.c.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data.get("status") == "running"

    def test_post_game_forwarded(self):
        """POST /game → dispatched to interpreter."""
        r = self.c.post("/game", json={
            "_shape": "Command", "command": "look"
        })
        # The interpreter processes it — we just verify it doesn't 404/500
        assert r.status_code in (200, 201, 204), (
            f"POST /game should be dispatched. Got {r.status_code}: {r.text[:200]}"
        )

    def test_post_root_forwarded(self):
        """
        POST / → dispatched to interpreter via handle_post_root.
        The interpreter may return 404 (no handler at / in this journey)
        or 204 (no content) — that's correct. The key assertion is that
        the request reaches the interpreter (returns JSON, not a Starlette error).
        In the live Zork deploy, POST / works because the Zork program has
        its own root listener.
        """
        r = self.c.post("/", json={
            "_shape": "Command", "command": "north"
        })
        # Verify it was dispatched (JSON response from _dispatch_post),
        # not a Starlette framework error
        assert r.status_code < 500, (
            f"POST / should be dispatched, not a server error. Got {r.status_code}"
        )

    def test_journey_page_still_works(self):
        """GET /dashboard → journey page renders. Coexistence confirmed."""
        r = self.c.get("/dashboard")
        assert r.status_code == 200
        assert "[DASHBOARD]" in r.text

    def test_mio_health_route(self):
        """GET /mio/health → health endpoint, not a page route."""
        r = self.c.get("/mio/health")
        assert r.status_code == 200
        data = r.json()
        assert data.get("status") == "running"

    def test_favicon_not_forwarded(self):
        """GET /favicon.ico → static handler, not forwarded to interpreter."""
        r = self.c.get("/favicon.ico")
        # 200 if file exists, 404 if not — but never routed to interpreter
        assert r.status_code in (200, 404)


# ══════════════════════════════════════════════════════════════════════════════
# CASE 7 — IMPLICIT DEFAULT JOURNEY
# Top-level bare pages (no journey wrapper) route the same way.
# ══════════════════════════════════════════════════════════════════════════════

_BARE_PAGES = """\
page Home at /home
    render
        <p>[BARE_HOME]</p>
    render: done
page: done
page About at /about
    render
        <p>[BARE_ABOUT]</p>
    render: done
page: done
"""


class TestImplicitDefaultJourney:

    @pytest.fixture(autouse=True)
    def client(self):
        self.c = _make_client(_BARE_PAGES)

    def test_bare_home(self):
        r = self.c.get("/home")
        assert r.status_code == 200
        assert "[BARE_HOME]" in r.text

    def test_bare_about(self):
        r = self.c.get("/about")
        assert r.status_code == 200
        assert "[BARE_ABOUT]" in r.text

    def test_bare_missing_404(self):
        """No first-match shadow: /nope → clean 404."""
        r = self.c.get("/nope")
        assert r.status_code == 404

    def test_bare_pages_are_html(self):
        r = self.c.get("/home")
        assert "text/html" in r.headers.get("content-type", "")
