# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""CR (the connector ruling): a mixed `and`/`or` chain in the general `condition` rule is a
check-time error, never silently resolved.

THE BUG: `condition: ... | NOT condition -> cond_not | condition AND condition -> cond_and |
condition OR condition -> cond_or | value_expr -> cond_bool` (mohio_data/mohio.lark:2727-2730) is
one self-recursive rule with no precedence declared between AND and OR. `a and b or c` has more
than one valid derivation, and Earley's ambiguity resolution silently picks ONE -- confirmed by
direct AST dump this session: `a AND (b OR c)`, the OPPOSITE of the C-family convention every
language this reads like uses ((a AND b) OR c). Mohio has no developer-writable grouping for
conditions (parens are math-only), so there was no way to WRITE the grouping you meant, and no
error told you the compiler picked one for you.

THE FIX: a new check-time scanner, `mohio_reachability.scan_mixed_connector_chain` (ERROR_SCANS,
same pattern as `scan_transaction_onfailure_futile`/`scan_bare_random_intrinsic`). It walks the
whole program for the AndCondition/OrCondition node SHAPE -- not for any specific statement type --
so it uniformly reaches every context the general `condition` rule is reachable from: `if`/
`unless`/`while` guards, `check ... when` guards, trailing `IF condition` qualifiers, and modify's
`WHERE condition` (T0-1's fix site -- confirmed the SAME `_eval_condition` evaluator at runtime,
and confirmed here that CR's check-time scan reaches it too, with zero modify-specific code).

MioQL's `where`/`match` clauses (`find`/`retrieve`/`grab`/`update`/`remove`) are a COMPLETELY
SEPARATE grammar path -- block form (`match`/`match any`/`no.match`) or repeated `where` lines --
that never produces an AndCondition/OrCondition node at all. A legitimate query is structurally
unreachable by this scanner, not merely untested; proven below with a real multi-clause query.

Only the CHECK-TIME behavior changed. Runtime grouping (`_eval_condition`) is deliberately
untouched -- the ruling's point is that a mixed chain should never run at all, not that it should
run differently once refused at check time. `tests/test_modify_where.py`'s former DOCUMENTATION
test (locking in today's silent grouping so a future change would show as a diff) is updated
there, not here, to assert the new check-time error -- exactly the "signal the ruling actually
reached modify too" its own comment predicted.

Run: `python tests/test_mixed_connector_failloud.py`.
"""
import os, sys
os.environ.setdefault('DATABASE_URL', ':memory:')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mohio_data
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lark import Lark
from mohio_transformer_ast import transform
from mohio_reachability import run_scans

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


def errors_for(src):
    prog = transform(P.parse(src), src)
    errs, _warns = run_scans(prog)
    return errs

def mixed_error(src):
    return any('mixed and/or chain has no defined grouping' in str(e) for e in errors_for(src))


SEED = 'a true\nb false\nc true\n'


print("=== 1-2. mixed chains fail loud, every control-flow context that reaches `condition` ===")

check("while a and b or c (mixed) -> check-time error",
      mixed_error(SEED + 'while a and b or c\n    show "loop"\n    a false\nwhile: done\n'),
      errors_for(SEED + 'while a and b or c\n    show "loop"\n    a false\nwhile: done\n'))

check("trailing `if a and b or c` (mixed) -> check-time error",
      mixed_error(SEED + 'show "matched" if a and b or c\n'),
      errors_for(SEED + 'show "matched" if a and b or c\n'))

check("trailing `unless a and b or c` (mixed) -> check-time error",
      mixed_error(SEED + 'show "skip" unless a and b or c\n'),
      errors_for(SEED + 'show "skip" unless a and b or c\n'))

check("check/when `a and b or c` (mixed) -> check-time error",
      mixed_error(SEED + 'check a\n    when a and b or c\n        show "yes"\n    '
                  'otherwise\n        show "no"\ncheck: done\n'),
      errors_for(SEED + 'check a\n    when a and b or c\n        show "yes"\n    '
                 'otherwise\n        show "no"\ncheck: done\n'))

# A longer chain, still mixed, connector further from the start -- proves the walk isn't just
# checking the first two terms.
check("a longer mixed chain (a and b and c or a) -> check-time error",
      mixed_error(SEED + 'show "x" if a and b and c or a\n'),
      errors_for(SEED + 'show "x" if a and b and c or a\n'))


print("\n=== 3. pure chains (all-and, all-or) still pass clean ===")

check("pure while a and b and c -> no mixed-connector error",
      not mixed_error(SEED + 'while a and b and c\n    show "loop"\n    a false\nwhile: done\n'))

check("pure while a or b or c -> no mixed-connector error",
      not mixed_error(SEED + 'while a or b or c\n    show "loop"\n    a false\nwhile: done\n'))

check("a longer pure and-chain (a and b and c and a) -> no mixed-connector error",
      not mixed_error(SEED + 'show "x" if a and b and c and a\n'))


print("\n=== 4. a single condition, and a NOT, still pass clean ===")

check("a single bare condition -> no mixed-connector error",
      not mixed_error(SEED + 'show "yes" if a\n'))

check("a NOT condition -> no mixed-connector error",
      not mixed_error(SEED + 'show "yes" unless not a\n'))

check("NOT combined with a PURE and (not a and b) -> no mixed-connector error "
      "(NOT is transparent, not itself a connector)",
      not mixed_error(SEED + 'show "yes" if not a and b\n'))


print("\n=== 5. modify's WHERE inherits the rule through the same AST shape, zero modify-specific code ===")

MODIFY_MIXED = (
    'connect db as sqlite from env.DATABASE_URL\n'
    'modify every row in db.items\n'
    '    where price is above 100 and price is below 5 or price is above 5\n'
    '    apply row\n        price 999\n    apply: done\nmodify: done\n')
check("mixed and/or in modify's WHERE -> check-time error (CR reaches it automatically)",
      mixed_error(MODIFY_MIXED), errors_for(MODIFY_MIXED))

MODIFY_PURE = (
    'connect db as sqlite from env.DATABASE_URL\n'
    'modify every row in db.items\n'
    '    where price is above 5 and price is below 1000\n'
    '    apply row\n        price 999\n    apply: done\nmodify: done\n')
check("a pure and/and modify WHERE -> no mixed-connector error",
      not mixed_error(MODIFY_PURE))


print("\n=== 6. a legitimate MioQL query (match / match any / no.match / where) is UNAFFECTED ===")

MIOQL_QUERY = (
    'connect db as sqlite from env.DATABASE_URL\n'
    'find rows in db.items\n'
    '    match category to "toys"\n'
    '    match any\n        price to 100\n        price to 5\n    match any: done\n'
    '    no.match\n        status to "hidden"\n    no.match: done\n'
    '    where price is above 5\n'
    'find: done\nshow rows\n')
check("a real multi-clause MioQL query is not flagged by the mixed-connector scanner",
      not mixed_error(MIOQL_QUERY), errors_for(MIOQL_QUERY))
check("...and produces zero errors from ANY scanner (a genuinely clean program)",
      errors_for(MIOQL_QUERY) == [], errors_for(MIOQL_QUERY))


print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
