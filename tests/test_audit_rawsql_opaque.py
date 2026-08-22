# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""T1-AUDIT-COVERAGE-GAPS Part F (2026-08-17): raw `sql` bypasses every verb-level audit call
(_audit_data_access/_audit_data_change), so a raw sql UPDATE changed data with ZERO audit
trail, and a raw sql SELECT over a [phi]-tagged table left none either. Fixed with a
deliberately OPAQUE record (_audit_raw_sql, mohio_interpreter.py): that raw sql executed, by
whom, when, and the parameterized statement text -- WITHOUT parsing which tables/fields were
touched. RULED explicitly (PRODUCTION-BUILD-PLAN.md, T1-AUDIT-RAWSQL-FULL-RECORD): this is a
stopgap, not the destination -- a full data-access/data-change record (matching what
retrieve/save already produce) is deferred, its own design beat.

Real .mho source through the full pipeline throughout (T1-TEST-REAL-PATH-STANDARD).

Run: `python tests/test_audit_rawsql_opaque.py`.
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); os.chdir(ROOT)
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')
os.environ.setdefault('MOHIO_ENCRYPTION_KEY', 'testkey')

from lark import Lark
from mohio_transformer_ast import transform as ast_transform
from mohio_interpreter import MohioInterpreter

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


def run_real(src):
    prog = ast_transform(P.parse(src), src)
    it = MohioInterpreter()
    it.run_declarations(prog)
    it.run(prog)
    return it


def raw_sql_entries(it):
    return [e for e in it._audit_logs.get('data_audit_log', []) if e.get('event') == 'RAW_SQL_EXECUTED']


SEED = ('connect db as sqlite from env.DATABASE_URL\n'
        'save to db.patients\n    diagnosis "flu"\nsave: done\n')


# ── a raw sql UPDATE that actually changes data now leaves an opaque trail ─────────────────────
it = run_real(SEED + "sql\n    UPDATE patients SET diagnosis = 'cold'\nsql: done\n")
_row = it._db.conn.execute("SELECT diagnosis FROM patients").fetchone()
check("the raw sql UPDATE really did change the data (proving this isn't a no-op test)",
      _row[0] == 'cold', _row)
_ent = raw_sql_entries(it)
check("the UPDATE produced a RAW_SQL_EXECUTED audit entry",
      len(_ent) == 1, _ent)
check("the entry carries the statement text",
      _ent and 'UPDATE patients' in _ent[0].get('statement', ''), _ent)


# ── a raw sql SELECT also leaves the opaque trail (the read-side gap) ──────────────────────────
it2 = run_real(SEED + "sql\n    SELECT * FROM patients\nsql: done\n")
_ent2 = raw_sql_entries(it2)
check("a raw sql SELECT also produces a RAW_SQL_EXECUTED audit entry",
      len(_ent2) == 1, _ent2)


# ── interpolated VALUES never leak into the audit trail -- only the parameterized form does ────
it3 = run_real(
    SEED + 'x "SENTINEL-SECRET-VALUE"\n'
    "sql\n    UPDATE patients SET diagnosis = {{ x }}\nsql: done\n")
_ent3 = raw_sql_entries(it3)
check("the interpolated value is a real, working substitution (not a no-op)",
      it3._db.conn.execute("SELECT diagnosis FROM patients").fetchone()[0] == 'SENTINEL-SECRET-VALUE')
check("the audit entry's statement text carries the placeholder, NOT the interpolated value",
      _ent3 and '?' in _ent3[0].get('statement', '')
      and 'SENTINEL-SECRET-VALUE' not in str(_ent3), _ent3)


# ── a raw sql block whose statement genuinely fails (malformed SQL) must FAIL LOUD, not
# silently no-op, and must not FABRICATE a misleading "it ran" audit record for something
# that never ran. CORRECTED 2026-08-19 (T1-RAWSQL-FAILOPEN investigation): the comment here
# used to claim this was a pre-existing "silent no-op in _split_sql_statements" -- that was
# never actually verified; `run_real` above discards `it.run()`'s return value, so the
# original check here could only ever prove "no audit entry," which is equally true whether
# the call raised OR silently no-op'd. It does NOT silently no-op: _exec_SqlBlock's execution
# loop already wraps the driver call and raises `_Raise(error_name='sql.error', ...)` on any
# exception, producing a clean 500. Asserting on the real result here closes that blind spot
# directly instead of leaving it undetected. Full matrix (nonexistent table too) lives in the
# dedicated tests/test_rawsql_failopen.py.
prog4 = ast_transform(P.parse(SEED + "sql\n    THIS IS NOT VALID SQL AT ALL\nsql: done\n"),
                       SEED + "sql\n    THIS IS NOT VALID SQL AT ALL\nsql: done\n")
it4 = MohioInterpreter(); it4.run_declarations(prog4)
r4 = it4.run(prog4)
check("malformed SQL genuinely fails loud (real 500 db_error, not a silent no-op)",
      r4.get('status') == 500 and 'sql.error' in str(r4.get('body', '')), r4)
check("a raw sql block that never actually executes a statement produces NO audit entry "
      "(no fabricated record)", raw_sql_entries(it4) == [], raw_sql_entries(it4))


# Test-strength check (content-safety review, 2026-08-19): the RAW_SQL_EXECUTED write must
# go through _audit_event, not a hand-rolled call straight to _audit_chained_save (the M2/M3
# bypass pattern the architectural rule forbids repeating). Spy on the real bound method.
_calls = []
_orig_audit_event = MohioInterpreter._audit_event
def _spy_audit_event(self, log_name, entry, ctx):
    _calls.append((log_name, entry.get('event')))
    return _orig_audit_event(self, log_name, entry, ctx)
MohioInterpreter._audit_event = _spy_audit_event
try:
    run_real(SEED + "sql\n    UPDATE patients SET diagnosis = 'cold'\nsql: done\n")
finally:
    MohioInterpreter._audit_event = _orig_audit_event
check("the raw-sql audit goes through _audit_event (not a bypass)",
      ('data_audit_log', 'RAW_SQL_EXECUTED') in _calls, _calls)

print(f"\n{_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
