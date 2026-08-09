# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
Guard: a fresh audit log always gets the full canonical schema.

The bug this pins: the "table already prepared" marker was a process-wide set keyed on
id(sink) -- a memory address. Python reuses addresses after garbage collection, so a
brand-new database whose sink landed where an earlier, unrelated sink had been was
treated as already prepared. `_ensure_audit_table` returned without creating anything,
another path then made the table from a single record's own keys, and the result was an
audit log with 8 columns instead of the canonical 18 -- a log that accepted writes and
could never be read back or verified.

It failed about one run in twenty, entirely dependent on whether the allocator reused an
address, which is why it read as a flaky test for weeks while actually being a
correctness failure in audit-table creation.

The fix ties the marker to the sink's own lifetime, so a reused address cannot be
confused for a prepared table. This test forces garbage collection between builds to
provoke address reuse on purpose.
"""
import gc
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("DATABASE_URL", ":memory:")
os.environ.setdefault("MOHIO_ENCRYPTION_KEY", "audit-schema-test")

from mohio_interpreter import MohioInterpreter, DbRuntime, Context
from mohio_audit_grades import canonical_audit_columns

_passed = _failed = 0
def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1; print(f"  ok   {label}")
    else:
        _failed += 1; print(f"  FAIL {label}: got {got!r} want {want!r}")


def build(n=5):
    it = MohioInterpreter()
    db = DbRuntime(":memory:")
    it._db = db

    class C(Context):
        def get_connection(self, _n):
            return db

    ctx = C()
    for i in range(n):
        it._audit_event("log", {"event": f"e{i}", "agent": "a"}, ctx)
    return it, db


print("test_audit_table_schema")

CANON = len(canonical_audit_columns())  # field columns; the table also has "id"

# Many fresh databases, forcing GC to reuse addresses -- the exact condition that used
# to produce the short table.
counts = {}
for trial in range(150):
    _it, db = build(3)
    ncol = len([d[0] for d in db.conn.execute("SELECT * FROM log LIMIT 1").description])
    counts[ncol] = counts.get(ncol, 0) + 1
    del _it, db
    if trial % 25 == 0:
        gc.collect()

check("every fresh audit table has exactly one schema", len(counts), 1)
only = next(iter(counts))
check("that schema carries all canonical columns (plus id)", only >= CANON, True)
check("no short-schema table ever appeared", 8 in counts, False)

# The verifier must read every one of them back without a key error.
read_failures = 0
for _ in range(40):
    it, db = build(5)
    db.conn.execute("DELETE FROM log WHERE rowid=3")
    db.conn.commit()
    reason = it.verify_audit_chain(db, "log").get("reason", "")
    if "could not be read" in reason:
        read_failures += 1
    del it, db
    gc.collect()
check("no audit log is unreadable after a fresh create", read_failures, 0)

# The ready-marker lives on the sink, not in a process-wide address-keyed set.
it, db = build(2)
check("the sink carries its own ready marker",
      hasattr(db, "_mohio_audit_ready"), True)
check("the old process-wide address-keyed set is gone",
      hasattr(MohioInterpreter, "_AUDIT_TABLE_READY"), False)

print(f"\nRESULTS: {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
