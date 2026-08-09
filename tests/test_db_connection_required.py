# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Guard: a missing database connection is a FAILURE, never a quiet nothing.

12 of 15 database verbs used to degrade silently when no connection was open:

    save / save.or.update / update / remove   ->  `if not db: return None`
                                                  The write was DISCARDED. The program
                                                  ran to completion and reported success.
    pull                                      ->  returned an empty list -- it LIED and
                                                  said the table had no rows
    sql                                       ->  returned an empty list; the only trace
                                                  was a --verbose print
    grab                                      ->  bound None
    retrieve / find                           ->  fell through _handle_failure to nothing
                                                  when the block had no handlers
    transaction                               ->  ran the body with NO transaction around
                                                  it -- silently non-atomic
    modify                                    ->  skipped the db write, changed nothing
    cm.purge                                  ->  (already raised -- correct)

Meanwhile remove.all, save.all and check.mioql RAISED. Same situation, opposite answer.
That inconsistency is drift, not design.

    save to db.audit
        trace "t1"
    save: done
    show "reached the end"

printed "reached the end". `mio check` said no errors. Nothing was written. Nothing said
so. In an app that is silent data loss.

THE RULE, which the design already settled: in the two-stage verb block, `on.failure`
means IT BROKE -- an error, NO CONNECTION, a timeout. So a missing connection routes to
on.failure if the block has one, and otherwise fails loud. It never continues as though
the work was done.
"""
import os, sys
os.environ.setdefault('DATABASE_URL', ':memory:')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mohio_data
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

_p = _f = 0
def check(label, cond):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    _p += bool(cond); _f += (not cond)

def run(src):
    """Returns (result, interpreter). run() RETURNS a response dict; it does not raise --
    in a web runtime an error IS a response. Throwing that return value away is how an
    earlier version of this file reported green while testing nothing."""
    it = MohioInterpreter()
    res = it.run(transform(_P.parse(src), src))
    return res, it

def refuses(src):
    """The verb REFUSED with the real error.

    NOT "did anything raise". `run()` catches _Raise and RETURNS a 500 response -- an
    error IS a response in a web runtime -- so catching exceptions here would miss the
    refusal entirely. Worse, it would count a SYNTAX error as a pass. An earlier version
    of this file did exactly that and reported green on invalid Mohio.

    So: assert the specific outcome, by name.
    """
    try:
        res, _ = run(src)
    except Exception:
        # A compile/syntax error is NOT the refusal we are testing for.
        return False
    if not isinstance(res, dict):
        return False
    return res.get('status') == 500 and 'no_db_connection' in str(res.get('body', ''))

def shown(src):
    _, it = run(src)
    return str(it.shown[-1]) if getattr(it, 'shown', None) else None

CONNECT = 'connect db as sqlite from env.DATABASE_URL\n'

print("no database connection is a FAILURE, not a quiet nothing")

# Every db verb, with NO connect declared. Every one must refuse.
VERBS = {
    'save':           'save to db.t\n    a "1"\nsave: done\n',
    'save or update': 'save or update db.t\n    match id to "1"\n    a "1"\nsave: done\n',
    'update':         'update db.t\n    match id to "1"\n    a "2"\nupdate: done\n',
    'remove':         'remove from db.t\n    match id to "1"\nremove: done\n',
    'find':           'find rows in db.t\nfind: done\n',
    'retrieve':       'retrieve r from db.t\n    match id to "1"\nretrieve: done\n',
    'pull':           'pull up to 3 from db.t\npull: done\n',
    'sql':            'sql\n    SELECT * FROM t\nsql: done\n',
    'transaction':    'transaction\n    show "inside"\ntransaction: done\n',
}
for verb, src in VERBS.items():
    check(f"{verb}: refuses when no connection is open", refuses(src))

# THE ORIGINAL BUG, named. The write must not vanish while the program reports success.
check("a save with no connect does NOT run to completion",
      refuses('save to db.audit\n    trace "t1"\nsave: done\nshow "reached the end"\n'))

# pull must not claim the table was empty. "I could not look" is not "there is nothing".
check("pull does not return an empty list when it never looked",
      refuses('pull up to 3 from db.t\npull: done\n'))

# A transaction that cannot be atomic must refuse, not run the body unprotected.
check("transaction does not run its body without a transaction",
      refuses('transaction\n    show "inside"\ntransaction: done\n'))

# on.failure is the DESIGNED route: "it broke" covers a missing connection.
check("on.failure catches the missing connection instead of raising",
      shown('find rows in db.t\n    on.failure\n        show "no db"\nfind: done\n') == "no db")

# And it must still WORK normally with a connection.
check("save + find still work with a connect",
      shown(CONNECT + 'save to db.t\n    a "1"\nsave: done\n'
                      'find rows in db.t\nfind: done\nshow rows\n') is not None)

check("an in-memory program with no db at all is unaffected",
      not refuses('create list xs\n    1\n    2\ncreate: done\nshow xs\n'))

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
