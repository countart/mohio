# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""One canonical audit schema for every audit table (Group A + Group B reconciled).

Before this, fraud_audit_log and phi_audit_log used a decision-shaped schema
(decision_name/inputs/result/confidence/model/fell_back/ts) while data_audit_log,
operation_audit_log, and the per-agent <name>_limits_log used an event-shaped schema
(audit_id/ts/event/agent/detail). Two shapes for overlapping purposes = drift, and it meant a
single append-only grant and a single hash chain could not cleanly cover every audit table.

Now every audit table is created with `canonical_audit_columns()`. This test locks:
  - the canonical schema contains every column both old groups needed,
  - it contains the reserved columns (prev_hash/entry_hash for the gated chain; input_binding for
    the detailed tier) so adding them later is not a migration of an append-only log,
  - every ensure_table for an audit table in the interpreter uses the canonical list (no stray
    hardcoded audit schema survives).
"""
import os, re, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')

from mohio_audit_grades import canonical_audit_columns

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)


cols = canonical_audit_columns()

# 1. contains the Group B (event) columns
for c in ['audit_id', 'ts', 'event', 'agent', 'detail']:
    check(f"canonical schema has Group-B column '{c}'", c in cols)

# 2. contains the Group A (decision) columns
for c in ['decision_name', 'inputs', 'result', 'confidence', 'model', 'fell_back']:
    check(f"canonical schema has Group-A column '{c}'", c in cols)

# 3. contains the reserved forward-compat columns
check("reserved chain column prev_hash present", 'prev_hash' in cols)
check("reserved chain column entry_hash present", 'entry_hash' in cols)
check("reserved input_binding column present", 'input_binding' in cols)

# 4. context columns
for c in ['sector', 'session_id', 'member_id']:
    check(f"context column '{c}' present", c in cols)

# 5. returns a fresh list each call (no shared-mutable-state footgun)
a = canonical_audit_columns(); a.append('__mutated__')
check("canonical_audit_columns returns a fresh list (caller can't mutate the source)",
      '__mutated__' not in canonical_audit_columns())

# 6. no stray hardcoded audit schema left in the interpreter's ensure_table calls.
# Every ensure_table that targets an audit log/table must use canonical_audit_columns(), not a
# literal decision_name/event column list.
src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'mohio_interpreter.py'), encoding='utf-8').read()
# the two old literal schemas, as regexes (whitespace-tolerant)
old_group_a = re.search(r"ensure_table\(\s*'(?:fraud|phi)_audit_log'\s*,\s*\[\s*'decision_name'", src)
old_group_b = re.search(r"ensure_table\(\s*log_name\s*,\s*\[\s*'audit_id'\s*,\s*'ts'\s*,\s*'event'", src)
check("no literal Group-A audit schema remains in interpreter", old_group_a is None,
      "found a hardcoded fraud/phi_audit_log decision-column ensure_table")
check("no literal Group-B audit schema remains in interpreter", old_group_b is None,
      "found a hardcoded log_name event-column ensure_table")

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
