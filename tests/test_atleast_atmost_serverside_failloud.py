# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""T1-CONDITIONAL-VOCAB-SERVER-CLIENT-SPLIT -- `is at least` / `is at most` fail loud server-side.

`is at least N` / `is at most N` are real grammar (`IS _AT _LEAST NUMBER -> cond_atleastnum` /
`IS _AT _MOST NUMBER -> cond_atmostnum`) but belong ONLY to `client_cond` (MioScript's
CLIENT-SIDE `validate`/`check` browser grammar). Before this fix, writing them inside the
SERVER-SIDE `where_condition` rule (used by both `check`/`when` and MioQL `where` clauses) did
not raise a parse error -- the words silently split into an unrelated comparison against a bare
identifier "at" plus a stray "least N" / "most N" statement, and the intended condition never
matched. This test proves, via the real pipeline (parse -> transform), that:

  1. `is at least` / `is at most` inside a server-side `check`/`when` now fails loud with the
     exact required message, instead of silently mismatching.
  2. The same words inside a MioQL `where` clause (the sibling path -- `where_condition` is
     shared by both) also fails loud, found via sibling sweep when building this fix.
  3. `is at least` / `is at most` in their REAL, valid context (`client_cond`, inside a
     `listen for ... check ...` MioScript block) is completely unaffected and still transforms
     to a real `ClientCheck` AST node.
  4. `>=` / `<=` inside `(...)` -- the real working server-side spelling -- still work,
     end-to-end through `mio run`, unaffected by this change.

Run as a script: `python tests/test_atleast_atmost_serverside_failloud.py` (exit 0 = pass).
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); os.chdir(ROOT)
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')
os.environ.setdefault('MOHIO_ENCRYPTION_KEY', 'testkey')

from lark import Lark
from mohio_transformer_ast import transform as ast_transform, MohioError
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

REQUIRED_MSG = ("is at least / is at most are client-side validation only -- use >= or <= "
                "in (...) for server-side comparisons.")


def expect_failloud(src, label):
    try:
        ast_transform(P.parse(src), src)
        check(label, False, "expected MohioError, transform succeeded")
    except MohioError as e:
        check(label, REQUIRED_MSG in str(e), str(e))


# ── 1. server-side check/when: is at least / is at most ─────────────────────────────────────
expect_failloud(
    'x 5\ncheck x\n    when x is at least 5\n        show "big"\ncheck: done\n',
    "server-side check/when: `is at least` fails loud")
expect_failloud(
    'x 5\ncheck x\n    when x is at most 5\n        show "small"\ncheck: done\n',
    "server-side check/when: `is at most` fails loud")

# ── 2. sibling: MioQL where clause (shares where_condition with check/when) ─────────────────
expect_failloud(
    'scores as list 10, 90, 50\n'
    'find hit in scores\n'
    '    where value is at least 60\n'
    'find: done\n'
    'show hit\n',
    "sibling MioQL where clause: `is at least` also fails loud")

# ── 3. real client_cond context: still parses and transforms untouched ──────────────────────
CLIENT_SRC = (
    'listen for submit on #f\n'
    '    check qty\n'
    '        is at least 5\n'
    '            put "big" into #log\n'
    '    check: done\n'
    'listen: done\n'
)
try:
    prog = ast_transform(P.parse(CLIENT_SRC), CLIENT_SRC)
    stmt = prog.statements[0]
    branch_cond = stmt.body[0].branches[0][0]
    check("client_cond `is at least` still transforms (unaffected)",
          branch_cond == ('atleastnum', 5.0), branch_cond)
except Exception as e:
    check("client_cond `is at least` still transforms (unaffected)", False,
          f"{type(e).__name__}: {e}")

# ── 4. >= / <= in (...) still work server-side, end-to-end ──────────────────────────────────
prog = ast_transform(P.parse(
    'x 5\ncheck x\n    when (x >= 5)\n        show "gte-ok"\n'
    '    otherwise\n        show "gte-fail"\ncheck: done\n'
), 'x 5\ncheck x\n    when (x >= 5)\n        show "gte-ok"\n    otherwise\n'
   '        show "gte-fail"\ncheck: done\n')
it = MohioInterpreter()
it.run_declarations(prog)
it.run(prog)
check(">= in (...) server-side still works end-to-end", it.shown == ["gte-ok"], it.shown)

prog = ast_transform(P.parse(
    'y 5\ncheck y\n    when (y <= 5)\n        show "lte-ok"\n'
    '    otherwise\n        show "lte-fail"\ncheck: done\n'
), 'y 5\ncheck y\n    when (y <= 5)\n        show "lte-ok"\n    otherwise\n'
   '        show "lte-fail"\ncheck: done\n')
it = MohioInterpreter()
it.run_declarations(prog)
it.run(prog)
check("<= in (...) server-side still works end-to-end", it.shown == ["lte-ok"], it.shown)


print(f"\n{_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
