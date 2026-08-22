# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""ai.decide audit records input NAMES + CLASSIFICATION, never raw values, never hashes.

THE BUG (verified, real path): `_write_ai_audit` stored `inputs = {name: str(value)}`, so a fraud
or PHI decision persisted the raw SSN/balance/etc. into the audit log's `inputs` column. The audit
trail -- the thing that exists to PROVE compliance -- became a second, unguarded copy of the exact
sensitive data it protects.

THE FIX (design chat ruling, 2026-07-15): store field NAMES + CLASSIFICATION
({"ssn": "[phi]", "balance": "[pci]"}), never raw values, and NEVER per-field plain hashes
(a hash of a low-entropy identifier like an SSN is reversible in milliseconds, so it counts as
storing the value). Reserve a nullable `input_binding` column for the future detailed/mioaudit
tier's keyed record-level binding.

This is the patent-critical audit feature; the base-tier guarantee is: the decision trail names
what a decision was made ABOUT without ever holding the data itself.
"""
import os, sys, json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')

from mohio_interpreter import MohioInterpreter, MohioValue

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)


class Decision:
    def __init__(self, inputs, result=True):
        self.inputs = inputs; self.result = result
        self.confidence = 0.91; self.model = 'm'; self.fell_back = False
        self.explanation = 'reasoning text (should never contain a leaked input value here)'


class OkDB:
    # Finding A (mohio_interpreter.py _seed_chain_head): a test double with no queryable
    # connection must be independently classifiable as a legitimate durable sink, same as the
    # doubles in test_audit_fail_loud.py / test_audit_sink_seam.py / test_degraded_incident.py /
    # test_structural_invariants.py -- an unmarked double is (correctly) refused as unclassifiable
    # now that classify_sink is the enforced authority, instead of silently succeeding via the
    # pre-FIX-3 genesis-fork bug this mock used to depend on without anyone knowing.
    _mohio_grade_verified = 'durable'

    def __init__(self): self.rows = []
    def ensure_table(self, *a): pass
    def save(self, t, r): self.rows.append(r)


class Ctx:
    def __init__(self, db, profile=None):
        self._db = db; self._sector_compliance = ['hipaa']; self._sector_profile = profile
    def get_connection(self, n): return self._db
    def get(self, k): return None


SSN = "SENTINEL-SSN-555-88-9999"
BAL = "SENTINEL-BALANCE-1234567"
REG = "SENTINEL-REGION-atlantis"


def audit_row(phi=None, pci=None, purposes=None, profile=None, inputs=None):
    interp = MohioInterpreter()
    interp._phi_fields = set(phi or [])
    interp._pci_fields = set(pci or [])
    interp._field_purposes = {k: set() for k in (purposes or [])}
    db = OkDB()
    dec = Decision(inputs or {
        'ssn': MohioValue(SSN, 'text'),
        'balance': MohioValue(BAL, 'text'),
        'region': MohioValue(REG, 'text'),
    })
    interp._write_ai_audit('phi_audit_log', 'fraud_check', dec, Ctx(db, profile))
    # T1-AUDIT-COVERAGE-GAPS Part D (2026-08-17): _write_ai_audit now routes through
    # _audit_event (the M2 sink-bypass fix) instead of calling db.save with a flat, columnar
    # row directly -- the physical row is now {audit_id, ts, event, agent, detail, prev_hash,
    # entry_hash}, with the full original entry (decision_name/inputs/result/confidence/model/
    # fell_back/input_binding/sector/session_id/member_id) JSON-encoded inside `detail`. Same
    # data, same guarantees (still never a raw value, still a real audit_id), different shape
    # to read it back through -- so every check below unwraps `detail` first.
    row = db.rows[0]
    return row, json.loads(row['detail'])


# 1. NO raw value anywhere in the persisted row
row, detail = audit_row(phi=['ssn'], pci=['balance'])
blob = json.dumps(row, default=str)
leaked = [s for s in (SSN, BAL, REG) if s in blob]
check("no raw input value persisted anywhere in the audit row", not leaked, f"leaked: {leaked}")

# 2. classification is correct
inp = json.loads(detail['inputs'])
check("phi field classified [phi]", inp.get('ssn') == '[phi]', inp)
check("pci field classified [pci]", inp.get('balance') == '[pci]', inp)
check("unclassified field is '-'", inp.get('region') == '-', inp)

# 3. pii (purpose) field classified [pii]
row2, detail2 = audit_row(purposes=['email'], inputs={'email': MohioValue('SENTINEL@x.com', 'text')})
inp2 = json.loads(detail2['inputs'])
check("pii/purpose field classified [pii]", inp2.get('email') == '[pii]', inp2)
check("no pii value leaked", 'SENTINEL@x.com' not in json.dumps(row2, default=str))

# 4. the reserved input_binding field is present and null at base tier
check("input_binding present and null at base tier",
      'input_binding' in detail and detail['input_binding'] is None, str(detail.get('input_binding')))

# 5. NOT a hash -- classification must be a tag, not a hex digest of the value
import re
vals = list(json.loads(detail['inputs']).values())
looks_hashed = any(re.fullmatch(r'[0-9a-f]{16,}', str(v)) for v in vals)
check("classifications are tags, not hashes of values", not looks_hashed, str(vals))

# 6. audit_id is still deterministic and 16 hex chars (computed over the value-free dict)
check("audit_id present, 16 hex chars", bool(re.fullmatch(r'[0-9a-f]{16}', row['audit_id'])),
      row.get('audit_id'))

# 7. the non-input fields still carry their (non-sensitive) data
check("result/confidence/model still recorded",
      detail.get('result') and detail.get('confidence') and detail.get('model'), str(detail))

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
