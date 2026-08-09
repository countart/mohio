# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""T0-4: a `transaction` block must be atomic -- all of it, or none of it.

THE BUG, CORRECTED ROOT CAUSE (verify against current code before trusting the archived
claim -- it was wrong): the archived logs blamed `save`'s error path returning a Response 500
instead of raising. That is not what happens: `save` with NO on.failure handler already raises
`_Raise('db_error', ...)`, which `_exec_TransactionBlock`'s own `except Exception: rollback();
raise` already catches correctly. Reproducing live (mio run, real SQLite, real Postgres) showed
the rollback STILL didn't undo the first write -- so the failure never was in that except clause.

The real mechanism: `DbRuntime.save()` (and Postgres/MySQL's) calls `ensure_table()` on EVERY
save, unconditionally -- table already existing or not. `ensure_table()`, on all three SQL
backends, called `self.conn.commit()` UNCONDITIONALLY at the end, ignoring `_in_transaction`
entirely (every other write method already gates its own commit on that flag; ensure_table alone
did not). So: first save's INSERT lands, held open by `_in_transaction` (save() correctly skips
its own commit) -- then the SECOND save's ensure_table() call runs, sees the table already
exists, does nothing schema-wise, but ITS OWN commit() fires anyway and force-commits the first
save's still-pending INSERT. The second write's failure has nothing left to roll back.

Fixed by making ensure_table's commit follow the same `if not self._in_transaction` rule as
every other write method, on all three SQL backends (SQLite `mohio_interpreter.py` ~line 965,
Postgres ~line 1219 [now ~1226 after this comment shifted it], MySQL ~line 1564).

FORK-8 (ruled), a SEPARATE mechanism: a write with its OWN on.failure handler (`save`/`update`/
`remove`, all 5 call sites) catches its exception locally and RETURNS instead of raising, so
`_exec_TransactionBlock`'s except never fires at all in that case -- with ensure_table alone
fixed, the block would still wrongly commit. Fixed the same way saga already tracks a compensated
step (`_saga_failed`): each write's on.failure branch sets `self._transaction_write_failed = True`
when `db._in_transaction` is true; `_exec_TransactionBlock` checks the flag after its body runs
(in addition to catching a real exception) and forces the same rollback either way. The on.failure
handler still runs -- it just can't rescue the transaction.

Added: a check-time WARNING (`mohio_reachability.scan_transaction_onfailure_futile`) for
on.failure written inside a transaction, naming exactly this -- it cannot rescue a partial
transaction.

Saga sibling (same unit, saga is the pattern source for FORK-8's fix): a compensated saga used to
be reported ONLY behind `--verbose` -- a request could return a plain 200 with `saga.status ==
'COMPENSATED'` bound but nothing in the output naming that a step failed. Fixed with an
unconditional (not --verbose-gated) stderr line whenever a saga does not commit cleanly.

Run: `python tests/test_transaction_rollback.py`.
"""
import io
import os
import sqlite3
import sys
from contextlib import redirect_stderr

os.environ.setdefault('DATABASE_URL', ':memory:')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mohio_data
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter, MohioRuntimeError
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


def run_src(src):
    prog = transform(P.parse(src), src)
    it = MohioInterpreter()
    try:
        result = it.run(prog)
        return result, None, it
    except MohioRuntimeError as e:
        return None, e, it


CONNECT = 'connect db as sqlite from env.DATABASE_URL\n'
SEED = (CONNECT +
        'save to db.members\n    id "seed"\n    email "seed@example.com"\nsave: done\n')


def member_ids(it):
    rows = it._db.conn.execute('select id from members').fetchall()
    return sorted(r['id'] for r in rows)


def failed_with(result, exc, needle):
    """A verb failure surfaces two different ways depending on which exception type carried
    it: `_Raise` (the plain "no on.failure" db_error path) is caught INSIDE it.run() and
    turned into a {'status': 500, 'body': ...} response -- exc stays None. The NEW FORK-8
    MohioRuntimeError (a write's on.failure fired, but the transaction still failed) is NOT
    caught inside it.run() and propagates as a real exception. Check whichever one fired."""
    if exc is not None:
        return needle in str(exc)
    if isinstance(result, dict):
        return needle in str(result.get('body', ''))
    return False


print("=== 1. two-write transaction, second violates UNIQUE -> full rollback ===")

TXN_UNIQUE_VIOLATION = (
    SEED +
    'transaction\n'
    '    save to db.members\n        id "m1"\n        email "first@example.com"\n    save: done\n'
    '    save to db.members\n        id "seed"\n        email "dup@example.com"\n    save: done\n'
    'transaction: done\n')
result, exc, it = run_src(TXN_UNIQUE_VIOLATION)
check("the failing write's db_error surfaced", failed_with(result, exc, 'UNIQUE'), (result, exc))
check("row count UNCHANGED from before the block -- 'm1' did NOT survive (full rollback)",
      member_ids(it) == ['seed'], member_ids(it))


print("\n=== 2. a transaction that SUCCEEDS -> both writes commit (no regression) ===")

TXN_SUCCESS = (
    SEED +
    'transaction\n'
    '    save to db.members\n        id "m1"\n        email "first@example.com"\n    save: done\n'
    '    save to db.members\n        id "m2"\n        email "second@example.com"\n    save: done\n'
    'transaction: done\n')
result, exc, it = run_src(TXN_SUCCESS)
check("no exception on a clean transaction", exc is None, exc)
check("all three rows committed", member_ids(it) == ['m1', 'm2', 'seed'], member_ids(it))


print("\n=== 3. FORK-8: on.failure inside a transaction still rolls back the whole block ===")

TXN_ONFAILURE = (
    SEED +
    'transaction\n'
    '    save to db.members\n        id "m1"\n        email "first@example.com"\n    save: done\n'
    '    save to db.members\n        id "seed"\n        email "dup@example.com"\n'
    '        on.failure\n            show "handler ran"\n'
    '    save: done\n'
    'transaction: done\n')
result, exc, it = run_src(TXN_ONFAILURE)
check("the on.failure handler DID run", 'handler ran' in [str(s) for s in it.shown], it.shown)
check("the transaction still raised (on.failure could not rescue it)",
      exc is not None and 'cannot rescue' in str(exc), exc)
check("row count UNCHANGED -- 'm1' did NOT survive despite the handler running",
      member_ids(it) == ['seed'], member_ids(it))


print("\n=== 4. check-time warning on on.failure inside a transaction ===")

prog_warn = transform(P.parse(TXN_ONFAILURE), TXN_ONFAILURE)
_errs, _warns = run_scans(prog_warn)
_codes = [getattr(w, 'code', None) for w in _warns]
check("mio check warns: on.failure inside a transaction cannot rescue it",
      'transaction_onfailure_futile' in _codes, _codes)

# Adversarial: on.failure OUTSIDE any transaction must NOT trigger this warning.
NO_TXN_ONFAILURE = (
    SEED +
    'save to db.members\n    id "seed"\n    email "dup@example.com"\n'
    '    on.failure\n        show "handled"\n'
    'save: done\n')
prog_ok = transform(P.parse(NO_TXN_ONFAILURE), NO_TXN_ONFAILURE)
_errs2, _warns2 = run_scans(prog_ok)
_codes2 = [getattr(w, 'code', None) for w in _warns2]
check("on.failure OUTSIDE a transaction does NOT trigger the warning (no false positive)",
      'transaction_onfailure_futile' not in _codes2, _codes2)


print("\n=== 5. bare save failure outside any transaction -- unchanged ===")

BARE_NO_HANDLER = (
    SEED + 'save to db.members\n    id "seed"\n    email "dup@example.com"\nsave: done\n'
    'show "unreachable"\n')
result, exc, it = run_src(BARE_NO_HANDLER)
check("still surfaces db_error, unchanged from before this fix",
      failed_with(result, exc, 'UNIQUE'), (result, exc))
check("'unreachable' never ran", 'unreachable' not in [str(s) for s in it.shown], it.shown)

BARE_WITH_HANDLER = (
    SEED + 'save to db.members\n    id "seed"\n    email "dup@example.com"\n'
    '    on.failure\n        show "handled outside txn"\n'
    'save: done\n'
    'show "continues after handled failure"\n')
result, exc, it = run_src(BARE_WITH_HANDLER)
check("on.failure outside a transaction still just handles and continues, unchanged",
      exc is None and [str(s) for s in it.shown] ==
      ['handled outside txn', 'continues after handled failure'], (exc, it.shown))


print("\n=== 6. saga: a compensated failure now surfaces without --verbose ===")

SAGA_COMPENSATES = (
    'saga checkout\n'
    '    step reserve\n        show "reserved"\n'
    '        compensate\n            show "released"\n'
    '    step: done\n'
    '    step ship\n        raise "warehouse unreachable"\n'
    '        compensate\n            show "no-op"\n'
    '    step: done\n'
    'saga: done\n'
    'give back 200 "done"\n')
_buf = io.StringIO()
with redirect_stderr(_buf):
    prog = transform(P.parse(SAGA_COMPENSATES), SAGA_COMPENSATES)
    it = MohioInterpreter()
    it.run(prog)
_stderr_out = _buf.getvalue()
check("a compensated saga prints to stderr WITHOUT --verbose",
      "did not commit" in _stderr_out and "COMPENSATED" in _stderr_out, _stderr_out)
check("names the step that actually failed",
      "'ship' failed" in _stderr_out, _stderr_out)

SAGA_SUCCESS = (
    'saga checkout\n'
    '    step reserve\n        show "reserved"\n'
    '        compensate\n            show "released"\n'
    '    step: done\n'
    'saga: done\n'
    'give back 200 "done"\n')
_buf2 = io.StringIO()
with redirect_stderr(_buf2):
    prog2 = transform(P.parse(SAGA_SUCCESS), SAGA_SUCCESS)
    it2 = MohioInterpreter()
    it2.run(prog2)
check("a COMMITTED saga prints nothing to stderr (no noise on the happy path)",
      _buf2.getvalue() == "", repr(_buf2.getvalue()))


print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
