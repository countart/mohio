# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
test_control_flow.py -- regression guard for the `check` / `when` matching fix (B7).

History: `check`'s `when` matched REGARDLESS of value for string equality and for
`is above` / `is below` (the operator + RHS were dropped; the where_condition
subtree resolved to the subject, which always equalled the check value). Only the
bare-value `when "x"` form and `contains` worked. Fixed by evaluating the clause's
own left-hand subject + operator + RHS via _match_where_condition.

Bare-value `when "x"` (the form Zork uses) MUST keep working -- guarded here too.

NOTE: the multi-line `if ... if: done` block and the trailing `if` qualifier
(findings doc sections 1 & 2) are separate DESIGN items (A6) and are intentionally
not asserted here.
"""
import os
os.environ['DATABASE_URL'] = ':memory:'
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter

_raw = open('mohio.lark', encoding='utf-8').read()
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)
_H = 'connect db as sqlite from env.DATABASE_URL\n'

def run(body):
    it = MohioInterpreter()
    it.run(transform(_P.parse(_H + body), _H + body), {})
    return [str(x) for x in it.shown]

PASS = 0; FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL: {name}")

# numeric equality
check("numeric is (match)",
      run('hold n=5\ncheck n\n when n is 5\n  show "five"\ncheck: done\n') == ['five'])
check("numeric is (skip first, match second)",
      run('hold n=5\ncheck n\n when n is 1\n  show "one"\n when n is 5\n  show "five"\ncheck: done\n') == ['five'])
# string equality -- the core bug
check("string is (match)",
      run('hold s="a"\ncheck s\n when s is "a"\n  show "A"\ncheck: done\n') == ['A'])
check("string is (no match -> otherwise)",
      run('hold s="z"\ncheck s\n when s is "a"\n  show "A"\n otherwise\n  show "other"\ncheck: done\n') == ['other'])
# is above / is below -- the other core bug
check("is above (true)",
      run('hold n=20\ncheck n\n when n is above 17\n  show "adult"\ncheck: done\n') == ['adult'])
check("is above (false -> otherwise)",
      run('hold n=5\ncheck n\n when n is above 17\n  show "adult"\n otherwise\n  show "minor"\ncheck: done\n') == ['minor'])
check("is below (false -> otherwise)",
      run('hold n=20\ncheck n\n when n is below 5\n  show "lo"\n otherwise\n  show "other"\ncheck: done\n') == ['other'])
check("is below (true)",
      run('hold n=3\ncheck n\n when n is below 5\n  show "lo"\ncheck: done\n') == ['lo'])
# is not
check("is not (false -> otherwise)",
      run('hold n=5\ncheck n\n when n is not 5\n  show "neq"\n otherwise\n  show "eq"\ncheck: done\n') == ['eq'])
check("is not (true)",
      run('hold n=3\ncheck n\n when n is not 5\n  show "neq"\n otherwise\n  show "eq"\ncheck: done\n') == ['neq'])
# contains
check("contains (match)",
      run('hold s="hello"\ncheck s\n when s contains "ell"\n  show "has"\ncheck: done\n') == ['has'])
check("contains (no match -> otherwise)",
      run('hold s="hello"\ncheck s\n when s contains "zzz"\n  show "has"\n otherwise\n  show "no"\ncheck: done\n') == ['no'])
# bare-value form (Zork relies on this -- must not regress)
check("bare-value (match)",
      run('hold s="go"\ncheck s\n when "go"\n  show "GO"\n otherwise\n  show "NO"\ncheck: done\n') == ['GO'])
check("bare-value (no match -> otherwise)",
      run('hold s="x"\ncheck s\n when "go"\n  show "GO"\n otherwise\n  show "NO"\ncheck: done\n') == ['NO'])
# first-match-wins ordering
check("first match wins",
      run('hold n=5\ncheck n\n when n is above 1\n  show "first"\n when n is above 2\n  show "second"\ncheck: done\n') == ['first'])

print(f"RESULTS: {PASS} passed, {FAIL} failed")
import sys
sys.exit(1 if FAIL else 0)
