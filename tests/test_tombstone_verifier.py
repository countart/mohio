# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Tombstone verifier (D3, reading (b)) -- prove what the tombstones can prove, nothing wider.

Two capabilities, both grounded by running a REAL cm.purge and then verifying:

  verify_tombstones(audit, data): every TOMBSTONE must name a row that is actually ABSENT. It walks
    the TOMBSTONES (not the data rows -- no unprompted hunt for unaccounted deletions; that is the
    enterprise INSERT-log tier), and flags a tombstoned row that is still present (INCONSISTENT) or
    a hash ref it cannot check without the salt (UNVERIFIABLE).

  adjudicate_erasure(audit, data, table, field, value): answer ONE row on request --
    PRESENT / ERASED / MISSING / INCONSISTENT / UNVERIFIABLE.

(b) is honest about its edge: MISSING cannot tell 'deleted without a tombstone' from 'never
existed'. That distinction needs the creation-log tier and is deliberately not claimed.
"""
import os, sys, sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); os.chdir(ROOT)
import mohio_data
os.environ['DATABASE_URL'] = ':memory:'

from pathlib import Path
from lark import Lark
from mohio_interpreter import MohioInterpreter, DbRuntime
from mohio_transformer_ast import transform as ast_transform

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

SALT = 'deployment-secret-xyz'
os.environ['MOHIO_AUDIT_SALT'] = SALT

audit = DbRuntime(':memory:')
MohioInterpreter.register_audit_sink_provider(lambda ctx: [audit])

# Seed three rows; lawfully erase M001 (by id) and M002 (by email). M003 stays.
SRC = '''connect db as sqlite from env.DATABASE_URL
save to db.members
    id "M001"
    email "a@x.com"
save: done
save to db.members
    id "M002"
    email "b@x.com"
save: done
save to db.members
    id "M003"
    email "c@x.com"
save: done
cm.purge from db.members
    match id to "M001"
    reason "erasure request A"
cm.purge: done
cm.purge from db.members
    match email to "b@x.com"
    reason "erasure request B"
cm.purge: done
'''

try:
    interp = MohioInterpreter()
    interp.run(ast_transform(P.parse(SRC), SRC))
    tenant = interp._db

    # --- consistency sweep: both tombstoned rows are absent -> ok ------------------------
    rep = interp.verify_tombstones(audit, tenant)
    check("sweep: two tombstones examined", rep['tombstones'] == 2, str(rep))
    check("sweep: ok=True (every tombstoned row is absent, chain intact)", rep['ok'] is True, str(rep))
    check("sweep: nothing inconsistent, nothing unverifiable",
          not rep['inconsistent'] and not rep['unverifiable'], str(rep))

    # --- adjudicate one row on request --------------------------------------------------
    v = interp.adjudicate_erasure(audit, tenant, 'members', 'id', 'M003')
    check("adjudicate M003 -> PRESENT", v['verdict'] == 'PRESENT', str(v))

    v = interp.adjudicate_erasure(audit, tenant, 'members', 'id', 'M001')
    check("adjudicate M001 (id purge) -> ERASED", v['verdict'] == 'ERASED', str(v))

    v = interp.adjudicate_erasure(audit, tenant, 'members', 'email', 'b@x.com')
    check("adjudicate b@x.com (email/hash purge) -> ERASED (hash recomputed)",
          v['verdict'] == 'ERASED', str(v))

    v = interp.adjudicate_erasure(audit, tenant, 'members', 'id', 'M999')
    check("adjudicate M999 (never existed / no tombstone) -> MISSING", v['verdict'] == 'MISSING', str(v))

    # --- tampering caught on request: a delete with NO tombstone ------------------------
    tenant.save('members', {'id': 'M004', 'email': 'd@x.com'})
    tenant.remove('members', 'id', 'M004')          # erased WITHOUT a cm.purge -> no tombstone
    v = interp.adjudicate_erasure(audit, tenant, 'members', 'id', 'M004')
    check("adjudicate M004 (deleted with no tombstone) -> MISSING (possible tampering)",
          v['verdict'] == 'MISSING', str(v))

    # --- unverifiable: a hash tombstone with no salt ------------------------------------
    os.environ.pop('MOHIO_AUDIT_SALT', None)
    rep2 = interp.verify_tombstones(audit, tenant)
    check("no salt: the email (hash) tombstone is UNVERIFIABLE, ok=False",
          rep2['ok'] is False and len(rep2['unverifiable']) == 1, str(rep2))
    check("no salt: the id tombstone is still checkable (no false inconsistency)",
          not rep2['inconsistent'], str(rep2))
    os.environ['MOHIO_AUDIT_SALT'] = SALT

    # --- inconsistent: a tombstoned row reappears ---------------------------------------
    tenant.save('members', {'id': 'M001', 'email': 'a@x.com'})   # the erased row returns
    v = interp.adjudicate_erasure(audit, tenant, 'members', 'id', 'M001')
    check("reappeared M001 -> INCONSISTENT (present but tombstoned)",
          v['verdict'] == 'INCONSISTENT', str(v))
    rep3 = interp.verify_tombstones(audit, tenant)
    check("sweep now flags the inconsistency, ok=False",
          rep3['ok'] is False and any(i['ref'] == 'M001' for i in rep3['inconsistent']), str(rep3))

    # --- inconsistent via the SALTED-HASH path: a NON-ID tombstoned row reappears --------
    # GAP-1 regression (mutation S5-M1b, 2026-07-31). The id path above is mirrored here for the
    # salted-hash path in `_tombstone_ref_present`. Erasing by email tombstones a HASH ref; if that
    # row returns, the SWEEP must flag it present. `verify_tombstones` is the only caller that
    # routes through `_tombstone_ref_present` (adjudicate_erasure uses `_row_present` directly), so
    # only the sweep can catch it. Flipping the hash path `return True, True` -> `return False, True`
    # reports a still-present erased row as gone: a false HIPAA/GDPR attestation. Before this case NO
    # compliance test exercised the hash present-branch -- the id sibling was covered, this was not.
    tenant.save('members', {'id': 'M002', 'email': 'b@x.com'})   # the email(hash)-erased row returns
    rep4 = interp.verify_tombstones(audit, tenant)
    check("sweep flags the reappeared HASH-tombstoned row (email), mirroring the id path",
          rep4['ok'] is False
          and any(i.get('kind') == 'hash' and i.get('field') == 'email'
                  for i in rep4['inconsistent']), str(rep4))

finally:
    MohioInterpreter.unregister_audit_sink_provider()
    os.environ.pop('MOHIO_AUDIT_SALT', None)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
