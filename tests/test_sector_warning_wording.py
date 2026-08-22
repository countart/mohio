# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""T1-SECTOR-DECLARATION-BEHAVIOR -- accurate per-case sector warning wording (2026-08-17).

`_exec_SectorDecl` (mohio_interpreter.py) used to print one generic "no profile found" warning
for BOTH an unknown sector name and an unlicensed private/paid sector name, and a soft "note"
(not a warning) for the private-sector case. The ruling in PRODUCTION-BUILD-PLAN.md
(T1-SECTOR-DECLARATION-BEHAVIOR, RULED 2026-08-16) requires each case to say precisely what
happened. Nothing halts in any case -- the program always runs -- only the disclosed message
differs. This test runs REAL `.mho` source through the full pipeline (parse -> transform ->
interpreter) for each reachable case and asserts on the real stderr text, per
T1-TEST-REAL-PATH-STANDARD.

The fourth case in the ruling's table (a syntactically nameless `sector:`) is NOT reachable here:
the grammar's `sector_name: SECTOR_SEG ("." SECTOR_SEG)*` requires at least one SECTOR_SEG, so
`sector:` with nothing after the colon is a hard parse-time syntax error, not a runtime warning --
confirmed live via `mio check` before this test was written. That case is covered instead by
asserting the parse error, not a warning message.

Run as a script: `python tests/test_sector_warning_wording.py` (exit 0 = pass).
"""
import os, sys, io, contextlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); os.chdir(ROOT)
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')
os.environ.setdefault('MOHIO_ENCRYPTION_KEY', 'testkey')

from lark import Lark
from lark.exceptions import UnexpectedInput
from mohio_interpreter import MohioInterpreter
from mohio_transformer_ast import transform as ast_transform
import mohio_interpreter as MI

_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)


def run_capture_stderr(src):
    """Real path: parse -> transform -> run_declarations -> run. Returns (interp, stderr_text)."""
    prog = ast_transform(P.parse(src), src)
    it = MohioInterpreter()
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        it.run_declarations(prog)
        it.run(prog)
    return it, buf.getvalue()


# ── Case: nameless sector: is a hard parse error, never reaches the runtime warning ────────────
try:
    P.parse("sector:\nshow \"hi\"\n")
    check("sector: with no name is a parse error", False, "expected UnexpectedInput, got a parse")
except UnexpectedInput:
    check("sector: with no name is a parse error (unreachable at runtime)", True)


# ── Case: wrong / nonexistent sector name ───────────────────────────────────────────────────────
MI.__dict__.pop('_SECTOR_WARN_SEEN', None)
it, err = run_capture_stderr(
    'sector: totally-fake-xyz-nonexistent-wording-test\nshow "hi"\n'
)
check("wrong-name case fires exactly one [mohio.sector] WARNING",
      err.count('[mohio.sector]') == 1, err)
check("wrong-name case uses the required wording",
      "sector name incorrect or not found -- no compliance, governance, or tools applied." in err,
      err)
check("wrong-name case still runs (show fires)", it.shown == ["hi"], it.shown)
# T1-AUDIT-COVERAGE-GAPS Part G (2026-08-17): the stderr warning above vanishes the moment
# nobody is watching the console -- an app running unenforced needs a DURABLE record too.
_log = it._audit_logs.get('security_audit_log', [])
check("wrong-name case writes a durable sector_unenforced audit entry",
      any(e.get('event') == 'sector_unenforced' and e.get('reason') == 'sector_not_found'
          for e in _log), _log)


# ── Case: private/paid sector name with no bundled profile (no key) ────────────────────────────
MI.__dict__.pop('_SECTOR_WARN_SEEN', None)
it, err = run_capture_stderr('sector: financial\nshow "hi"\n')
check("private-sector case fires exactly one [mohio.sector] WARNING",
      err.count('[mohio.sector]') == 1, err)
check("private-sector case uses the required wording",
      "private sector requires a commercial key -- no compliance, governance, or tools applied." in err,
      err)
check("private-sector case still runs (show fires)", it.shown == ["hi"], it.shown)
_log2 = it._audit_logs.get('security_audit_log', [])
check("private-sector case writes a durable sector_unenforced audit entry",
      any(e.get('event') == 'sector_unenforced' and e.get('reason') == 'private_sector_no_key'
          for e in _log2), _log2)


# ── Case: valid sector (bundled demo profile) -- no warning at all ─────────────────────────────
MI.__dict__.pop('_SECTOR_WARN_SEEN', None)
it, err = run_capture_stderr('sector: demo-regulated\nshow "hi"\n')
check("valid sector produces no [mohio.sector] WARNING", '[mohio.sector] WARNING' not in err, err)
check("valid sector still runs (show fires)", it.shown == ["hi"], it.shown)
check("valid sector writes NO sector_unenforced audit entry (nothing wrong to record)",
      it._audit_logs.get('security_audit_log') is None, it._audit_logs.get('security_audit_log'))


# Test-strength check (content-safety review, 2026-08-19): both sector_unenforced writes must
# go through _audit_event, not a hand-rolled call straight to _audit_chained_save (the M2/M3
# bypass pattern the architectural rule forbids repeating). Spy on the real bound method.
_calls = []
_orig_audit_event = MI.MohioInterpreter._audit_event
def _spy_audit_event(self, log_name, entry, ctx):
    _calls.append((log_name, entry.get('event'), entry.get('reason')))
    return _orig_audit_event(self, log_name, entry, ctx)
MI.MohioInterpreter._audit_event = _spy_audit_event
try:
    MI.__dict__.pop('_SECTOR_WARN_SEEN', None)
    run_capture_stderr('sector: totally-fake-xyz-nonexistent-wording-test\nshow "hi"\n')
    MI.__dict__.pop('_SECTOR_WARN_SEEN', None)
    run_capture_stderr('sector: financial\nshow "hi"\n')
finally:
    MI.MohioInterpreter._audit_event = _orig_audit_event
check("wrong-name sector_unenforced audit goes through _audit_event (not a bypass)",
      ('security_audit_log', 'sector_unenforced', 'sector_not_found') in _calls, _calls)
check("private-no-key sector_unenforced audit goes through _audit_event (not a bypass)",
      ('security_audit_log', 'sector_unenforced', 'private_sector_no_key') in _calls, _calls)

print(f"\n{_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
