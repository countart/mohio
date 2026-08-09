# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""`check count` (and `check exists`) honor a `where` filter, not just `match` (2026-08-03).

Found during the overnight cross-dialect sweep: `check count as n in db.t / where grp is "a"`
counted the WHOLE table (3), not the filtered rows (2). The grammar accepts `where_clause` for
CHECK_COUNT, but the transformer only ever looked for `MatchClause` -- a `where` condition parsed
fine and was silently dropped. `check_exists_bare_block` (the `check found in db.x / where ...`
shorthand) already had the correct pattern; mirrored here for the explicit CHECK_COUNT form.
`db.count()` only understands equality filters, so a comparison condition (`where x is above N`)
fails loud rather than silently degrading to "no filter".

Run: `python tests/test_check_count_where_filter.py`.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')

from pathlib import Path
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter

import mohio_data
_RAW = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_G = '\n'.join(l for l in _RAW.splitlines() if not l.strip().startswith('//'))
_P = Lark(_G, parser='earley', ambiguity='resolve', propagate_positions=True)

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail: print(f"          {detail}")
    _p += bool(cond); _f += (not cond)

SETUP = (
    'connect db as sqlite from env.DATABASE_URL\n'
    'save to db.t\n    grp "a"\n    name "one"\nsave: done\n'
    'save to db.t\n    grp "a"\n    name "two"\nsave: done\n'
    'save to db.t\n    grp "b"\n    name "three"\nsave: done\n'
)

def run(src):
    prog = transform(_P.parse(src), src)
    it = MohioInterpreter(); it.run_declarations(prog)
    it.shown = []
    it.run(prog)
    return it.shown

def run_raises(src):
    prog = transform(_P.parse(src), src)
    it = MohioInterpreter(); it.run_declarations(prog)
    try:
        it.run(prog)
        return None
    except Exception as e:
        return str(e)

WHERE_COUNT = SETUP + 'check count as n in db.t\n    where grp is "a"\ncheck: done\nshow "n={{ n }}"\n'
NOFILTER_COUNT = SETUP + 'check count as n in db.t\ncheck: done\nshow "n={{ n }}"\n'
MATCH_COUNT = SETUP + 'check count as n in db.t\n    match grp to "a"\ncheck: done\nshow "n={{ n }}"\n'
COMPARISON_COUNT = SETUP + 'check count as n in db.t\n    where grp is "a"\n    where name is "one"\ncheck: done\nshow "n={{ n }}"\n'

check("check count with where -> honors the filter (n=2, was silently 3)",
      run(WHERE_COUNT) == ['n=2'], str(run(WHERE_COUNT)))
check("check count with no filter -> whole table (n=3, regression guard)",
      run(NOFILTER_COUNT) == ['n=3'], str(run(NOFILTER_COUNT)))
check("check count with match -> unaffected (n=2, was already correct)",
      run(MATCH_COUNT) == ['n=2'], str(run(MATCH_COUNT)))

# A comparison operator inside a `where` -- count() has no way to express it; must fail loud,
# never silently ignore (the exact class of bug this fix closes).
AMOUNT_SETUP = ('connect db as sqlite from env.DATABASE_URL\n'
               'save to db.t\n    amount 5\nsave: done\n'
               'save to db.t\n    amount 50\nsave: done\n')
COMPARISON = AMOUNT_SETUP + 'check count as n in db.t\n    where amount is more than 10\ncheck: done\nshow "n={{ n }}"\n'
err = run_raises(COMPARISON)
check("comparison operator in check count where -> fails loud (not a silently wrong count)",
      err is not None and 'above' in err.lower() and 'not supported' in err.lower(), str(err))

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
