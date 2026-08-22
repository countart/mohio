# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""T1-RAWSQL-FAILOPEN, Part D (2026-08-19): raw `sql` on a genuine driver error.

STATUS: verified ALREADY FIXED, no code change made. _exec_SqlBlock's execution loop
(mohio_interpreter.py) already wraps the driver call in `try: ... except Exception as e:
raise _Raise(error_name='sql.error', ...)`, so a nonexistent table or genuinely malformed SQL
already produces a clean 500 db_error, not a silent no-op.

The earlier belief that this silently no-op'd traces to a real blind spot in this session's
own T1-AUDIT-COVERAGE-GAPS Part F test (test_audit_rawsql_opaque.py): its `run_real` helper
called `it.run(prog)` and discarded the return value, so it only ever asserted "no audit entry
was fabricated" -- true whether the call raised OR silently no-op'd, so it never actually
distinguished the two. This file closes that blind spot directly by asserting on the real
{'status', 'body'} result.

Real .mho source through the full pipeline throughout (T1-TEST-REAL-PATH-STANDARD).

Run: `python tests/test_rawsql_failopen.py`.
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
    r = it.run(prog)
    return it, r


def raw_sql_entries(it):
    return [e for e in it._audit_logs.get('data_audit_log', []) if e.get('event') == 'RAW_SQL_EXECUTED']


SEED = 'connect db as sqlite from env.DATABASE_URL\n'

# ── raw sql UPDATE against a NONEXISTENT table -> fails loud, no fabricated audit record ───
it1, r1 = run_real(SEED + "sql\n    UPDATE ghost_table SET x = 1\nsql: done\n" + 'show "unreachable"\n')
check("raw sql on a nonexistent table fails loud (real status/body, not a silent no-op)",
      r1.get('status') == 500 and 'ghost_table' in str(r1.get('body', '')), r1)
check("the failure never reaches past the sql block (unreachable show never ran)",
      it1.shown == [], it1.shown)
check("no fabricated RAW_SQL_EXECUTED audit entry for a statement that never really ran",
      raw_sql_entries(it1) == [], raw_sql_entries(it1))

# ── genuinely malformed SQL -> fails loud ───────────────────────────────────────────────────
it2, r2 = run_real(SEED + "sql\n    THIS IS NOT VALID SQL AT ALL\nsql: done\n" + 'show "unreachable"\n')
check("malformed SQL fails loud (real status/body, not a silent no-op)",
      r2.get('status') == 500 and 'syntax error' in str(r2.get('body', '')).lower(), r2)
check("no fabricated audit entry for malformed SQL", raw_sql_entries(it2) == [], raw_sql_entries(it2))

# ── a VALID raw sql statement still runs and still leaves its opaque audit record (Part F) ─
SEED3 = SEED + 'shape Thing\n    name as text\nshape: done\nsave to db.things\n    name "widget"\nsave: done\n'
it3, r3 = run_real(SEED3 + "sql\n    UPDATE things SET name = 'renamed'\nsql: done\n")
_row3 = it3._db.conn.execute("SELECT name FROM things").fetchone()
check("a valid raw sql statement still runs (data actually changed)",
      _row3[0] == 'renamed', _row3)
_ent3 = raw_sql_entries(it3)
check("a valid raw sql statement still leaves its RAW_SQL_EXECUTED audit record",
      len(_ent3) == 1 and 'UPDATE things' in _ent3[0].get('statement', ''), _ent3)


print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
