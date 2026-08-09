# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
test_security_checks.py
=======================
pytest suite for `mio check --security`

Covers five security checks:
  1. HARDCODED_CREDENTIAL        — detected by _scan_source (comments/strings outside connect)
                                   NOTE: connect_decl grammar only accepts ENV_REF|SECRET_REF,
                                   so a literal in connect is a parse error. The transformer's
                                   _scan_source catches hardcoded credentials in other contexts
                                   (comments, variable assignments, etc).
  2. MISSING_AGENT_LIMITS        — ai.agent block with no limits/max steps/max cost/max time
  3. SECTOR_VIOLATION            — ai.decide confidence below sector floor without sec.non_critical
  4. SECURITY_DEBT_UNDOCUMENTED  — security: off without reason / expires inline
  5. sec.non_critical            — audit notice (not an error) when sector floor bypass present

SYNTAX REFERENCE (from live mohio.lark + tests/zork_demo.mho):
  - listen for ... listen: done          (closer is "listen: done")
  - ai.decide NAME returns type ... ai.decide: done
  - confidence above 0.90                (no parens on value)
  - weigh name, other_name               (dotted names OK: txn.amount)
  - ai.audit to log_name
  - not confident \n    statement
  - security: off reason "..." expires "..."   (inline, no sub-indented block)
  - ai.agent NAME ... ai.agent: done
  - connect NAME as TYPE from env.VAR    (grammar ONLY accepts ENV_REF — literal = parse error)
  - sector: demo_low

HOW TO RUN (from mohio/ — the directory containing mio.py and the mohio_data/ package):
    python -m pytest tests/test_security_checks.py -v
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import tempfile
from pathlib import Path

import pytest

# ── Locate mio.py ──────────────────────────────────────────────────────────────
_THIS_DIR = Path(__file__).parent.resolve()

MIO_PY: Path | None = None
for _candidate in [_THIS_DIR.parent, _THIS_DIR.parent.parent]:
    if (_candidate / "mio.py").exists():
        MIO_PY = _candidate / "mio.py"
        break

if MIO_PY is None:
    pytest.exit(
        "Cannot locate mio.py. Run from mohio/ or repo root.",
        returncode=2,
    )

MIO_DIR = MIO_PY.parent   # temp .mho files are written next to mio.py so a subprocess run from
                          # here resolves imports normally; the grammar itself now loads via
                          # mohio_data.GRAMMAR_PATH regardless of this directory


# ── Helper ─────────────────────────────────────────────────────────────────────

def _run_check(source: str) -> subprocess.CompletedProcess:
    """Write source to a temp .mho in MIO_DIR, run mio check --security."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".mho", dir=MIO_DIR, delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(textwrap.dedent(source))
        tmp_path = tmp.name
    try:
        result = subprocess.run(
            [sys.executable, str(MIO_PY), "check", tmp_path, "--security"],
            capture_output=True, text=True, cwd=str(MIO_DIR),
        )
        result.stdout = result.stdout + result.stderr
        return result
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _assert_present(result, code):
    assert code in result.stdout, (
        f"\nExpected '{code}' in output — not found.\n"
        f"Exit: {result.returncode}\nOutput:\n{result.stdout}"
    )

def _assert_absent(result, code):
    assert code not in result.stdout, (
        f"\nExpected '{code}' to be absent — found it.\n"
        f"Exit: {result.returncode}\nOutput:\n{result.stdout}"
    )

def _assert_parses(result):
    """Fail fast with a clear message if the snippet itself has a syntax error."""
    assert "Syntax error" not in result.stdout, (
        f"\nSnippet has a SYNTAX ERROR — fix the test fixture, not the compiler.\n"
        f"Output:\n{result.stdout}"
    )


# ── Canonical valid snippets ────────────────────────────────────────────────────
# These are building blocks confirmed against the live grammar and zork_demo.mho.
# All indentation is LEFT-FLUSH so textwrap.dedent works cleanly.

# Minimal valid ai.decide (financial floor: 0.85 — confidence 0.90 passes)
_DECIDE_VALID_FINANCIAL = """\
ai.decide screen_transaction returns boolean
    confidence above 0.90
    weigh amount, member_id
    ai.audit to fraud_audit_log
    not confident
        give back 200 "Referred to manual review"
ai.decide: done"""

# ai.decide below financial floor (0.60 < 0.85) — triggers SECTOR_VIOLATION
_DECIDE_BELOW_FINANCIAL = """\
ai.decide screen_transaction returns boolean
    confidence above 0.60
    weigh amount, member_id
    ai.audit to fraud_audit_log
    not confident
        give back 200 "Referred to manual review"
ai.decide: done"""

# ai.decide below financial floor with sec.non_critical + reason — fully suppresses floor
_DECIDE_NON_CRITICAL = """\
ai.decide screen_transaction returns boolean
    confidence above 0.60
    sec.non_critical reason "Non-regulatory UI preference decision"
    weigh amount, member_id
    ai.audit to hint_audit_log
    not confident
        give back 200 "Referred to manual review"
ai.decide: done"""

# ai.decide valid for healthcare floor (0.95 — confidence 0.96 passes)
_DECIDE_VALID_HEALTHCARE = """\
ai.decide suggest_diagnosis returns text
    confidence above 0.96
    weigh symptoms, history
    ai.audit to phi_audit_log
    not confident
        give back 200 "Referred to clinician"
ai.decide: done"""

# ai.decide below healthcare floor (0.85 < 0.95) — triggers SECTOR_VIOLATION
_DECIDE_BELOW_HEALTHCARE = """\
ai.decide suggest_diagnosis returns text
    confidence above 0.85
    weigh symptoms, history
    ai.audit to phi_audit_log
    not confident
        give back 200 "Referred to clinician"
ai.decide: done"""

# ai.agent WITH limits (limits block + closer) — valid
_AGENT_WITH_LIMITS = """\
ai.agent triage_agent
    goal "Pre-screen the transaction"
    limits
        max steps 10
        cost ceiling 0.50
    limits: done
    not confident
        give back 200 "Agent could not complete"
ai.agent: done"""

# ai.agent WITHOUT limits — triggers MISSING_AGENT_LIMITS
_AGENT_NO_LIMITS = """\
ai.agent triage_agent
    goal "Pre-screen the transaction"
    not confident
        give back 200 "Agent could not complete"
ai.agent: done"""


def _wrap_listen(body: str, sector: str = "", connect: str = "connect db as postgres from env.DATABASE_URL") -> str:
    """
    Wrap a body snippet in a minimal valid .mho file structure.
    sector: pass "financial" or "healthcare" or "" for none.
    Indents body by 4 spaces inside listen for.
    """
    sector_line = f"sector: {sector}\n" if sector else ""
    indented = "\n".join("    " + line if line.strip() else line for line in body.splitlines())
    return f"""{sector_line}{connect}

listen for
{indented}
    give back 200 "ok"
listen: done
"""


# ══════════════════════════════════════════════════════════════════════════════
# CHECK 1 — HARDCODED_CREDENTIAL
#
# The grammar rejects a literal string in `connect from "..."` outright (parse
# error). The _scan_source check catches hardcoded credentials appearing in
# other contexts: variable assignments that look like connection strings, or
# comments containing password patterns.
#
# These tests exercise _scan_source — the regex pattern that fires on lines
# matching /password|secret|api_key|token/ with a string value.
# ══════════════════════════════════════════════════════════════════════════════

class TestHardcodedCredential:

    def test_pass_env_only(self):
        """
        PASS: env.VAR reference — no hardcoded credential pattern.
        Expect exit 0, HARDCODED_CREDENTIAL absent.
        """
        source = _wrap_listen("", connect="connect db as postgres from env.DATABASE_URL")
        result = _run_check(source)
        _assert_parses(result)
        _assert_absent(result, "HARDCODED_CREDENTIAL")
        assert result.returncode == 0, f"Expected exit 0.\n{result.stdout}"

    def test_fail_connect_literal_should_be_caught(self):
        """
        INTENDED: a hardcoded connection string in connect from MUST trigger
        HARDCODED_CREDENTIAL. The grammar currently accepts it and the scanner
        misses it — this test will fail until the compiler chat patches the gap.

        Compiler chat ticket: Earley parser accepts STRING in connect_decl
        where only ENV_REF|SECRET_REF should be valid. Either tighten the
        grammar or add a _scan_source / _v_connect_decl regex to catch
        connect ... from "literal" patterns.
        """
        source = 'connect db as postgres from "postgresql://admin:secret@db/prod"\n\nlisten for\n    give back 200 "ok"\nlisten: done\n'
        result = _run_check(source)
        _assert_present(result, "HARDCODED_CREDENTIAL")
        assert result.returncode == 1, (
            f"Expected exit 1 — hardcoded credential in connect.\n{result.stdout}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# CHECK 2 — MISSING_AGENT_LIMITS
# ai.agent must have limits / max steps / max cost / max time.
# Detected by transformer _v_ai_agent_block (pass 1) and security report (pass 2).
# ══════════════════════════════════════════════════════════════════════════════

class TestMissingAgentLimits:

    def test_pass_agent_with_max_steps(self):
        """
        PASS: ai.agent has max steps — limits satisfied.
        Expect exit 0, MISSING_AGENT_LIMITS absent.
        """
        source = _wrap_listen(_AGENT_WITH_LIMITS, sector="demo_low")
        result = _run_check(source)
        _assert_parses(result)
        _assert_absent(result, "MISSING_AGENT_LIMITS")
        assert result.returncode == 0, f"Expected exit 0.\n{result.stdout}"

    def test_fail_agent_no_limits(self):
        """
        FAIL: ai.agent with no limits, max steps, max cost, or max time.
        Expect exit 1, MISSING_AGENT_LIMITS present.
        """
        source = _wrap_listen(_AGENT_NO_LIMITS, sector="demo_low")
        result = _run_check(source)
        _assert_parses(result)
        _assert_present(result, "MISSING_AGENT_LIMITS")
        assert result.returncode == 1, f"Expected exit 1.\n{result.stdout}"

    def test_fail_two_agents_one_missing(self):
        """
        FAIL: two ai.agent blocks — one valid, one without limits.
        MISSING_AGENT_LIMITS fires for the bad one.
        """
        body = _AGENT_WITH_LIMITS + "\n" + _AGENT_NO_LIMITS
        source = _wrap_listen(body, sector="demo_low")
        result = _run_check(source)
        _assert_parses(result)
        _assert_present(result, "MISSING_AGENT_LIMITS")
        assert result.returncode == 1, f"Expected exit 1.\n{result.stdout}"


# ══════════════════════════════════════════════════════════════════════════════
# CHECK 3 — SECTOR_VIOLATION
# Floors: financial → 0.85, healthcare → 0.95.
# sec.non_critical suppresses the error (audit notice only).
# ══════════════════════════════════════════════════════════════════════════════

class TestSectorViolation:

    def test_pass_financial_above_floor(self):
        """PASS: confidence 0.90 >= 0.85 financial floor. No error."""
        source = _wrap_listen(_DECIDE_VALID_FINANCIAL, sector="demo_low")
        result = _run_check(source)
        _assert_parses(result)
        _assert_absent(result, "SECTOR_VIOLATION")
        assert result.returncode == 0, f"Expected exit 0.\n{result.stdout}"

    def test_fail_financial_below_floor(self):
        """FAIL: confidence 0.60 < 0.85 financial floor. SECTOR_VIOLATION."""
        source = _wrap_listen(_DECIDE_BELOW_FINANCIAL, sector="demo_low")
        result = _run_check(source)
        _assert_parses(result)
        _assert_present(result, "SECTOR_VIOLATION")
        assert result.returncode == 1, f"Expected exit 1.\n{result.stdout}"

    def test_pass_non_critical_suppresses_violation(self):
        """
        INTENDED: sec.non_critical with reason fully suppresses the
        confidence floor for this specific non-regulatory decision.
        Both the transformer check and the security report's SECTOR_VIOLATION
        are suppressed. Exit 0. Audit notice logged.

        Will fail until the compiler chat lands the consolidation fix
        (validator taught to honor sec.non_critical, two checks folded to one).
        """
        source = _wrap_listen(_DECIDE_NON_CRITICAL, sector="demo_low")
        result = _run_check(source)
        _assert_parses(result)
        _assert_absent(result, "SECTOR_VIOLATION")
        assert result.returncode == 0, (
            f"sec.non_critical with reason should fully suppress floor. Exit 0.\n{result.stdout}"
        )

    def test_fail_healthcare_below_floor(self):
        """FAIL: confidence 0.85 < 0.95 healthcare floor. SECTOR_VIOLATION."""
        source = _wrap_listen(_DECIDE_BELOW_HEALTHCARE, sector="demo_high")
        result = _run_check(source)
        _assert_parses(result)
        _assert_present(result, "SECTOR_VIOLATION")
        assert result.returncode == 1, f"Expected exit 1.\n{result.stdout}"

    def test_pass_healthcare_above_floor(self):
        """PASS: confidence 0.96 >= 0.95 healthcare floor. No error."""
        source = _wrap_listen(_DECIDE_VALID_HEALTHCARE, sector="demo_high")
        result = _run_check(source)
        _assert_parses(result)
        _assert_absent(result, "SECTOR_VIOLATION")
        assert result.returncode == 0, f"Expected exit 0.\n{result.stdout}"

    def test_pass_no_sector_no_floor(self):
        """PASS: no sector declared — no floor enforced. Any confidence is valid."""
        body = """\
ai.decide is_relevant returns boolean
    confidence above 0.50
    weigh score
    ai.audit to general_audit_log
    not confident
        give back 200 "Manual review"
ai.decide: done"""
        source = _wrap_listen(body)
        result = _run_check(source)
        _assert_parses(result)
        _assert_absent(result, "SECTOR_VIOLATION")
        assert result.returncode == 0, f"Expected exit 0.\n{result.stdout}"


# ══════════════════════════════════════════════════════════════════════════════
# CHECK 4 — SECURITY_DEBT_UNDOCUMENTED
# security: off requires both reason "..." and expires "..." inline.
# ══════════════════════════════════════════════════════════════════════════════

class TestSecurityDebtUndocumented:

    def test_pass_fully_documented(self):
        """
        PASS: security: off with reason and expires on following lines.
        The security report regex looks ahead from the NEXT line after
        'security: off', so reason/expires must be on subsequent lines.
        Expect exit 0, SECURITY_DEBT_UNDOCUMENTED absent.
        """
        body = 'security: off\n    reason "Dev-only, TLS at load balancer"\n    expires "2026-12-31"'
        source = _wrap_listen(body)
        result = _run_check(source)
        _assert_parses(result)
        _assert_absent(result, "SECURITY_DEBT_UNDOCUMENTED")
        assert result.returncode == 0, f"Expected exit 0.\n{result.stdout}"

    def test_fail_bare_security_off(self):
        """
        FAIL: bare security: off with no reason or expires.
        Expect exit 1, SECURITY_DEBT_UNDOCUMENTED present.
        """
        body = "security: off"
        source = _wrap_listen(body)
        result = _run_check(source)
        _assert_parses(result)
        _assert_present(result, "SECURITY_DEBT_UNDOCUMENTED")
        assert result.returncode == 1, f"Expected exit 1.\n{result.stdout}"

    def test_fail_reason_only(self):
        """FAIL: reason present but expires missing — still incomplete."""
        body = 'security: off reason "Dev-only endpoint"'
        source = _wrap_listen(body)
        result = _run_check(source)
        _assert_parses(result)
        _assert_present(result, "SECURITY_DEBT_UNDOCUMENTED")
        assert result.returncode == 1, f"Expected exit 1.\n{result.stdout}"

    def test_fail_expires_only(self):
        """FAIL: expires present but reason missing — still incomplete."""
        body = 'security: off expires "2026-12-31"'
        source = _wrap_listen(body)
        result = _run_check(source)
        _assert_parses(result)
        _assert_present(result, "SECURITY_DEBT_UNDOCUMENTED")
        assert result.returncode == 1, f"Expected exit 1.\n{result.stdout}"


# ══════════════════════════════════════════════════════════════════════════════
# CHECK 5 — sec.non_critical audit notice
# Must appear in output when present. Must NOT cause exit 1.
# ══════════════════════════════════════════════════════════════════════════════

class TestSecNonCriticalNotice:

    def test_no_notice_when_absent(self):
        """
        PASS: no sec.non_critical in file.
        The report summary always mentions 'sec.non_critical overrides' as a
        line item, so we check for the specific notice format 'sec.non_critical
        at line' which only appears when the keyword is actually in the source.
        """
        source = _wrap_listen(_DECIDE_VALID_FINANCIAL, sector="demo_low")
        result = _run_check(source)
        _assert_parses(result)
        assert "sec.non_critical at line" not in result.stdout, (
            f"Expected no sec.non_critical notice.\n{result.stdout}"
        )

    def test_notice_present_when_used(self):
        """
        INTENDED: sec.non_critical with reason produces an audit notice
        ('sec.non_critical at line N') but fully suppresses the floor error.
        SECTOR_VIOLATION absent. Exit 0.

        Will fail until the compiler chat lands the consolidation fix.
        """
        source = _wrap_listen(_DECIDE_NON_CRITICAL, sector="demo_low")
        result = _run_check(source)
        _assert_parses(result)
        assert "sec.non_critical at line" in result.stdout, (
            f"Expected sec.non_critical audit notice.\n{result.stdout}"
        )
        _assert_absent(result, "SECTOR_VIOLATION")
        assert result.returncode == 0, (
            f"sec.non_critical with reason should exit 0.\n{result.stdout}"
        )

    def test_fail_non_critical_without_reason(self):
        """
        INTENDED: sec.non_critical without a reason is an error.
        Mirrors security: off requiring reason. A casual bypass and a
        justified exemption must not look identical in the source.

        Will fail until the compiler chat adds the reason requirement.
        """
        body = """\
ai.decide screen_transaction returns boolean
    confidence above 0.60
    sec.non_critical
    weigh amount, member_id
    ai.audit to hint_audit_log
    not confident
        give back 200 "Referred to manual review"
ai.decide: done"""
        source = _wrap_listen(body, sector="demo_low")
        result = _run_check(source)
        _assert_parses(result)
        # sec.non_critical without reason should be an error
        assert result.returncode == 1, (
            f"Expected exit 1 — sec.non_critical requires a reason.\n{result.stdout}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# INTEGRATION — multiple violations in one file
# ══════════════════════════════════════════════════════════════════════════════

class TestIntegration:

    def test_three_violations_in_one_file(self):
        """
        INTEGRATION: one file with three violations that survive parse:
          1. MISSING_AGENT_LIMITS   — ai.agent with no limits
          2. SECTOR_VIOLATION       — confidence 0.60 < 0.85 financial floor
          3. SECURITY_DEBT_UNDOCUMENTED — bare security: off

        (HARDCODED_CREDENTIAL is a separate class — connect literal is a parse
        error in the grammar; credential patterns in assignments are tested above.)

        All three codes must appear; exit 1.
        """
        body = (
            _AGENT_NO_LIMITS + "\n" +
            _DECIDE_BELOW_FINANCIAL + "\n" +
            "security: off"
        )
        source = _wrap_listen(body, sector="demo_low")
        result = _run_check(source)
        _assert_parses(result)

        missing = [
            code for code in [
                "MISSING_AGENT_LIMITS",
                "SECTOR_VIOLATION",
                "SECURITY_DEBT_UNDOCUMENTED",
            ]
            if code not in result.stdout
        ]
        assert not missing, (
            f"Expected all three codes. Missing: {missing}\n{result.stdout}"
        )
        assert result.returncode == 1, f"Expected exit 1.\n{result.stdout}"

    def test_clean_file_exit_zero(self):
        """
        INTEGRATION BASELINE: clean financial sector file.
        No error codes in output, exit 0.
        """
        body = _AGENT_WITH_LIMITS + "\n" + _DECIDE_VALID_FINANCIAL
        source = _wrap_listen(body, sector="demo_low")
        result = _run_check(source)
        _assert_parses(result)

        found = [
            code for code in [
                "HARDCODED_CREDENTIAL",
                "MISSING_AGENT_LIMITS",
                "SECTOR_VIOLATION",
                "SECURITY_DEBT_UNDOCUMENTED",
            ]
            if code in result.stdout
        ]
        assert not found, (
            f"Expected no error codes. Found: {found}\n{result.stdout}"
        )
        assert result.returncode == 0, f"Expected exit 0.\n{result.stdout}"
