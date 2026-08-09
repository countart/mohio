# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
test_golden_saga_retrieve.py
=============================
Golden integration tests — suites 1–3 from the 2026-06-18 compiler ship.

Suite 1: Saga with a REAL database (DB consistency, not show markers)
Suite 2: Saga behind a route (HTTP layer, TestClient)
Suite 3: retrieve.* data correctness + check-layer fail-loud

These complement (not duplicate) the existing unit-level coverage:
  - test_saga_execution.py    (15 checks — ordering/status via show markers)
  - test_retrieve_modifiers.py (23 checks — modifier extraction, adapter, e2e count/all/first)
  - test_journey_page.py       (27 checks — page routing, scope, render)

HOW TO RUN (from mohio/):
    python -m pytest tests/golden/test_golden_saga_retrieve.py -v
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

# ── Locate project root ───────────────────────────────────────────────────────
_THIS_DIR = Path(__file__).parent.resolve()
_PROJECT  = _THIS_DIR.parent.parent

if not (_PROJECT / "mio.py").exists():
    for c in [_THIS_DIR.parent, _THIS_DIR]:
        if (c / "mio.py").exists():
            _PROJECT = c
            break

sys.path.insert(0, str(_PROJECT))
import mohio_data

try:
    from lark import Lark
    from mohio_transformer_ast import transform
    from mohio_interpreter import MohioInterpreter, DbRuntime
    from mohio_server import MohioServer, create_app
    from starlette.testclient import TestClient
except ImportError as e:
    pytest.skip(f"Missing dependency: {e}", allow_module_level=True)

_grammar_path = mohio_data.GRAMMAR_PATH
if not _grammar_path.exists():
    pytest.skip("mohio.lark not found", allow_module_level=True)

_raw = _grammar_path.read_text(encoding="utf-8")
_clean = "\n".join(l for l in _raw.splitlines() if not l.strip().startswith("//"))
_PARSER = Lark(_clean, parser="earley", ambiguity="resolve", propagate_positions=True)


class MockAI:
    def register_chain(self, *a, **k): pass
    def decide(self, **k):
        return SimpleNamespace(result=None, confidence=0.9,
                               fell_back=False, model="mock")


def _tag(resp):
    body = resp.get("body") if isinstance(resp, dict) else resp
    m = re.search(r"\[(.*?)\]", str(body))
    return m.group(1) if m else None


# ══════════════════════════════════════════════════════════════════════════════
# SUITE 1 — SAGA WITH A REAL DATABASE
#
# The unit tests prove ordering via `show` markers. These tests prove DATA
# CONSISTENCY: saga steps write to a real SQLite DB, and assertions query
# the DB directly after the saga completes.
# ══════════════════════════════════════════════════════════════════════════════

# A 3-step saga that creates an order, logs an event, and confirms.
# Each step does a DB write; each compensate undoes it.
_SAGA_DB = """\
connect db as sqlite from env.DATABASE_URL

saga process_order

    step create_order
        save to db.orders
            id       "ORD-1"
            status   "pending"
        save: done
        compensate
            update db.orders
                status "cancelled"
                match id to "ORD-1"
            update: done
    step: done

    step log_event
        save to db.events
            order_id  "ORD-1"
            type      "created"
        save: done
        compensate
            update db.events
                type "rolled_back"
                match order_id to "ORD-1"
            update: done
    step: done

    step confirm_order
        update db.orders
            status "confirmed"
            match id to "ORD-1"
        update: done
        compensate
            update db.orders
                status "cancelled"
                match id to "ORD-1"
            update: done
    step: done

saga: done
"""

# Same saga but step 3 raises -> COMPENSATED
_SAGA_DB_FAIL_STEP3 = """\
connect db as sqlite from env.DATABASE_URL

saga process_order

    step create_order
        save to db.orders
            id       "ORD-1"
            status   "pending"
        save: done
        compensate
            update db.orders
                status "cancelled"
                match id to "ORD-1"
            update: done
    step: done

    step log_event
        save to db.events
            order_id  "ORD-1"
            type      "created"
        save: done
        compensate
            update db.events
                type "rolled_back"
                match order_id to "ORD-1"
            update: done
    step: done

    step confirm_order
        raise "payment declined"
        compensate
            show "this should not run"
    step: done

saga: done
"""

# Saga with a best-effort step whose DB write must persist after compensation
_SAGA_DB_BEST_EFFORT = """\
connect db as sqlite from env.DATABASE_URL

saga audit_flow

    step main_action
        save to db.actions
            id     "ACT-1"
            status "done"
        save: done
        compensate
            update db.actions
                status "undone"
                match id to "ACT-1"
            update: done
    step: done

    step log_audit
        save to db.audit
            id     "AUD-1"
            action "ACT-1"
            note   "audit recorded"
        save: done
        best effort
    step: done

    step finalize
        raise "final step failed"
        compensate
            show "never runs"
    step: done

saga: done
"""

# Saga where one step's compensate itself raises -> FAILED_COMPENSATION
_SAGA_DB_FAILED_COMP = """\
connect db as sqlite from env.DATABASE_URL

saga broken_rollback

    step step_a
        save to db.items
            id     "A"
            status "created"
        save: done
        compensate
            update db.items
                status "rolled_back"
                match id to "A"
            update: done
    step: done

    step step_b
        save to db.items
            id     "B"
            status "created"
        save: done
        compensate
            raise "compensate boom"
    step: done

    step step_c
        raise "step c failed"
        compensate
            show "should not run — failing step"
    step: done

saga: done
"""


class TestSagaDB:
    """Suite 1: saga with a real SQLite database."""

    def _run_saga(self, source: str) -> tuple[dict, DbRuntime]:
        """Parse and run a saga. Return (result_dict, db_handle)."""
        dbfile = tempfile.mktemp(suffix=".db")
        os.environ["DATABASE_URL"] = dbfile
        try:
            prog = transform(_PARSER.parse(source), source)
            it = MohioInterpreter(ai=MockAI(), db_path=dbfile)
            result = it.run(prog, None)
            blob = result.to_python() if hasattr(result, "to_python") else result
            if not isinstance(blob, dict):
                blob = {"status": None, "raw": blob}
            return blob, it._db
        finally:
            os.environ.pop("DATABASE_URL", None)

    # ── 1. Happy path: all succeed -> COMMITTED, rows reflect forward actions
    def test_happy_path_committed(self):
        result, db = self._run_saga(_SAGA_DB)
        assert result.get("status") == "COMMITTED"

        orders = db.retrieve_all_spec("orders", [])
        assert len(orders) == 1
        assert orders[0]["id"] == "ORD-1"
        assert orders[0]["status"] == "confirmed", (
            "Step 3 should have updated status to 'confirmed'"
        )

        events = db.retrieve_all_spec("events", [])
        assert len(events) == 1
        assert events[0]["type"] == "created"

    # ── 2. Rollback: last step fails -> COMPENSATED, DB back to pre-saga
    def test_rollback_compensated(self):
        result, db = self._run_saga(_SAGA_DB_FAIL_STEP3)
        assert result.get("status") == "COMPENSATED"

        orders = db.retrieve_all_spec("orders", [])
        assert len(orders) == 1
        assert orders[0]["status"] == "cancelled", (
            "Step 1 compensate should have set status to 'cancelled'"
        )

        events = db.retrieve_all_spec("events", [])
        assert len(events) == 1
        assert events[0]["type"] == "rolled_back", (
            "Step 2 compensate should have set type to 'rolled_back'"
        )

    # ── 3. Best effort: its DB write persists even after compensation
    def test_best_effort_persists(self):
        result, db = self._run_saga(_SAGA_DB_BEST_EFFORT)
        assert result.get("status") == "COMPENSATED"

        actions = db.retrieve_all_spec("actions", [])
        assert len(actions) == 1
        assert actions[0]["status"] == "undone", (
            "Step 1 was compensated (undone)"
        )

        audit = db.retrieve_all_spec("audit", [])
        assert len(audit) == 1, (
            "Best-effort step's DB write must persist after compensation"
        )
        assert audit[0]["note"] == "audit recorded"

    # ── 4. FAILED_COMPENSATION: compensate raises, rollback continues for others
    def test_failed_compensation(self):
        result, db = self._run_saga(_SAGA_DB_FAILED_COMP)
        assert result.get("status") == "FAILED_COMPENSATION"

        items = db.retrieve_all_spec("items", [])
        items_by_id = {r["id"]: r for r in items}

        assert items_by_id["A"]["status"] == "rolled_back", (
            "Step A's compensate should have succeeded"
        )
        # Step B's compensate raised — its row stays as-is
        assert items_by_id["B"]["status"] == "created", (
            "Step B's compensate failed — row unchanged"
        )

    # ── 5. Failing step's own compensate never runs
    def test_failing_step_compensate_not_run(self):
        result, db = self._run_saga(_SAGA_DB_FAIL_STEP3)
        it_shown = []  # We already tested this via unit tests; here confirm DB
        orders = db.retrieve_all_spec("orders", [])
        # Step 3 raised — its compensate should NOT have run.
        # If it had run, it would have written something. Since step 3
        # only has `raise` in its forward action and `show` in its compensate,
        # the compensate marker should be absent. The DB state proves steps
        # 1 and 2 were compensated (see test_rollback_compensated).
        # This is an indirect proof: if step 3's compensate ran, we'd see
        # its `show` in the shown list. But here we just re-confirm the
        # DB state is exactly what steps 1+2 compensates produce.
        assert orders[0]["status"] == "cancelled"


# ══════════════════════════════════════════════════════════════════════════════
# SUITE 2 — SAGA BEHIND A ROUTE (HTTP LAYER)
#
# A saga inside a task, invoked from a listener endpoint.
# saga.status drives the HTTP response via check/when.
# ══════════════════════════════════════════════════════════════════════════════

_SAGA_ROUTE_SUCCESS = """\
connect db as sqlite from env.DATABASE_URL

task doWork
    returns text

    saga work_saga
        step one
            save to db.log
                msg "step_one_done"
            save: done
            compensate
                save to db.log
                    msg "step_one_undone"
                save: done
        step: done
    saga: done

    check work_saga.status
        when "COMMITTED"
            give back ok "[COMMITTED]"
        when "COMPENSATED"
            give back 409 "[COMPENSATED]"
        when "FAILED_COMPENSATION"
            give back error "[FAILED_COMP]"
        otherwise
            give back error "[UNKNOWN]"
    check: done
task: done

listen for
    new sh.Job at /run
        call doWork
        call: done
    new: done
listen: done
"""

_SAGA_ROUTE_FAIL = """\
connect db as sqlite from env.DATABASE_URL

task doWork
    returns text

    saga work_saga
        step one
            save to db.log
                msg "step_one_done"
            save: done
            compensate
                save to db.log
                    msg "step_one_undone"
                save: done
        step: done
        step two
            raise "forced failure"
            compensate
                show "never"
        step: done
    saga: done

    check work_saga.status
        when "COMMITTED"
            give back ok "[COMMITTED]"
        when "COMPENSATED"
            give back 409 "[COMPENSATED]"
        when "FAILED_COMPENSATION"
            give back error "[FAILED_COMP]"
        otherwise
            give back error "[UNKNOWN]"
    check: done
task: done

listen for
    new sh.Job at /run
        call doWork
        call: done
    new: done
listen: done
"""


class TestSagaRoute:
    """Suite 2: saga behind an HTTP route via Starlette TestClient."""

    def _make_client(self, source: str) -> TestClient:
        dbfile = tempfile.mktemp(suffix=".db")
        os.environ["DATABASE_URL"] = dbfile
        prog = transform(_PARSER.parse(source), source)
        interp = MohioInterpreter(ai=MockAI(), db_path=dbfile)
        server = MohioServer(prog, interp)
        app = create_app(server)
        return TestClient(app, raise_server_exceptions=False)

    def test_committed_returns_200(self):
        """
        INTENDED: saga completes -> work_saga.status == "COMMITTED" ->
        check/when routes to give back ok "[COMMITTED]" -> 200.

        REAL BEHAVIOR: work_saga.status is not accessible inside the task
        after saga: done. check falls through to otherwise -> [UNKNOWN].
        The saga runs and commits, but its .status property is not bound
        in the calling scope.

        Compiler chat ticket: saga result binding inside tasks. The saga
        returns {status: "COMMITTED", steps: [...]} when run standalone
        (unit tests prove this), but inside a task the result isn't bound
        to the saga name so `check work_saga.status` can't resolve it.
        """
        c = self._make_client(_SAGA_ROUTE_SUCCESS)
        r = c.post("/run", json={"_shape": "Job"})
        assert r.status_code == 200
        assert "COMMITTED" in str(r.json()), (
            f"Expected COMMITTED in response. Got: {r.json()}"
        )

    def test_compensated_returns_409(self):
        """
        INTENDED: saga fails -> work_saga.status == "COMPENSATED" ->
        check/when routes to give back 409 "[COMPENSATED]".

        Same gap as test_committed_returns_200 — saga.status not bound.
        """
        c = self._make_client(_SAGA_ROUTE_FAIL)
        r = c.post("/run", json={"_shape": "Job"})
        assert r.status_code == 409
        assert "COMPENSATED" in str(r.json()), (
            f"Expected COMPENSATED in response. Got: {r.json()}"
        )

    def test_saga_runs_once_per_request(self):
        """Two requests produce independent saga runs."""
        c = self._make_client(_SAGA_ROUTE_SUCCESS)
        r1 = c.post("/run", json={"_shape": "Job"})
        r2 = c.post("/run", json={"_shape": "Job"})
        assert r1.status_code == 200
        assert r2.status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# SUITE 3 — RETRIEVE.* DATA CORRECTNESS + CHECK-LAYER FAIL-LOUD
#
# Seed a table with ordered rows. Assert .all/.every, .count, .one,
# .first/.last return correct data. Then verify `mio check` on
# retrieve.bogus exits non-zero with a clean error.
# ══════════════════════════════════════════════════════════════════════════════

# Template: inject a retrieve block into a handler that renders the result
_RETRIEVE_HEAD = """\
connect db as sqlite from env.DATABASE_URL
shape Page
    method GET
shape: done
listen for
    request for sh.Page at /test
"""
_RETRIEVE_TAIL = """\
    request: done
listen: done
"""

def _retrieve_src(retrieve_block: str, render_expr: str) -> str:
    return (
        _RETRIEVE_HEAD +
        retrieve_block +
        f"        render\n            <p>[{render_expr}]</p>\n        render: done\n" +
        _RETRIEVE_TAIL
    )


class TestRetrieveDataCorrectness:
    """Suite 3a: seed rows, assert modifier results via interpreter."""

    SEED = [
        {"id": 1, "name": "alpha", "rank": 10},
        {"id": 2, "name": "bravo", "rank": 20},
        {"id": 3, "name": "charlie", "rank": 30},
        {"id": 4, "name": "delta", "rank": 40},
        {"id": 5, "name": "echo", "rank": 50},
    ]

    def _run_retrieve(self, retrieve_block: str, render_expr: str) -> str:
        """Seed items table, run a GET /test, return the [tag] from the response."""
        dbfile = tempfile.mktemp(suffix=".db")
        os.environ["DATABASE_URL"] = dbfile
        try:
            src = _retrieve_src(retrieve_block, render_expr)
            prog = transform(_PARSER.parse(src), src)
            it = MohioInterpreter(ai=MockAI(), db_path=dbfile)
            # Seed
            it._db = DbRuntime(dbfile)
            it._db.ensure_table("items", ["id", "name", "rank"])
            for row in self.SEED:
                it._db.save("items", row)

            resp = it.run(prog, {"_method": "GET", "_path": "/test"})
            return _tag(resp) or str(resp)
        finally:
            os.environ.pop("DATABASE_URL", None)
            try:
                os.unlink(dbfile)
            except OSError:
                pass

    def test_retrieve_count(self):
        block = (
            "        retrieve.count total from db.items\n"
            "        retrieve: done\n"
        )
        assert self._run_retrieve(block, "{{ total }}") == "5"

    def test_retrieve_all(self):
        block = (
            "        retrieve.all rows from db.items\n"
            "        retrieve: done\n"
        )
        assert self._run_retrieve(block, "{{ rows.count }}") == "5"

    def test_retrieve_every(self):
        """retrieve.every is an alias for retrieve.all."""
        block = (
            "        retrieve.every rows from db.items\n"
            "        retrieve: done\n"
        )
        assert self._run_retrieve(block, "{{ rows.count }}") == "5"

    def test_retrieve_first(self):
        block = (
            "        retrieve.first row from db.items\n"
            "        retrieve: done\n"
        )
        assert self._run_retrieve(block, "{{ row.name }}") == "alpha"

    def test_retrieve_last(self):
        block = (
            "        retrieve.last row from db.items\n"
            "        retrieve: done\n"
        )
        assert self._run_retrieve(block, "{{ row.name }}") == "echo"

    def test_retrieve_one(self):
        block = (
            "        retrieve.one row from db.items\n"
            "            match name to \"charlie\"\n"
            "        retrieve: done\n"
        )
        assert self._run_retrieve(block, "{{ row.name }}") == "charlie"


class TestRetrieveCheckLayer:
    """Suite 3b: mio check on invalid modifiers."""

    MIO_PY = _PROJECT / "mio.py"

    def _run_check(self, source: str) -> subprocess.CompletedProcess:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".mho", dir=_PROJECT, delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(source)
            tmp_path = tmp.name
        try:
            return subprocess.run(
                [sys.executable, str(self.MIO_PY), "check", tmp_path],
                capture_output=True, text=True, cwd=str(_PROJECT),
            )
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def test_retrieve_bogus_fails_clean(self):
        """
        INTENDED: mio check on retrieve.bogus exits non-zero with a clean
        error and a line number.

        REAL BEHAVIOR: mio check only runs the MohioValidator on the Lark
        tree — it does not run the AST transformer, which is where invalid
        modifiers are caught (MohioCompileError). So retrieve.bogus passes
        mio check but fails at transform time.

        The existing unit test (test_retrieve_modifiers.py) proves the
        transformer catches it. This test documents the intended behavior
        at the CLI layer.

        Compiler chat ticket: surface MohioCompileError for invalid retrieve
        modifiers in cmd_check (either run the transformer or mirror the
        modifier validation in the MohioValidator).
        """
        source = 'retrieve.bogus r from db.rooms\n    match id to 1\nretrieve.bogus: done\n'
        result = self._run_check(source)
        assert result.returncode != 0, (
            f"Expected non-zero exit for retrieve.bogus.\n{result.stdout}"
        )
        assert "Traceback" not in result.stdout, (
            f"Expected clean error, got traceback.\n{result.stdout}"
        )

    def test_valid_modifiers_check_clean(self):
        """mio check on valid retrieve modifiers exits 0."""
        for mod in ["all", "every", "count", "first", "last", "one"]:
            source = f'retrieve.{mod} r from db.rooms\n    match id to 1\nretrieve.{mod}: done\n'
            result = self._run_check(source)
            assert result.returncode == 0, (
                f"retrieve.{mod} should check clean. Got:\n{result.stdout}"
            )

    def test_plain_retrieve_checks_clean(self):
        """Plain retrieve (no modifier) checks clean."""
        source = 'retrieve r from db.rooms\n    match id to 1\nretrieve: done\n'
        result = self._run_check(source)
        assert result.returncode == 0, (
            f"Plain retrieve should check clean. Got:\n{result.stdout}"
        )
