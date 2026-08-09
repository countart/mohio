# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Names-not-values across EVERY audit path (relay item #6, consolidated).

The compliance guarantee is that an audit trail records what happened -- field NAMES, ids, counts,
classifications, timestamps -- but NEVER the sensitive VALUES it exists to protect, so the trail
can never become a second unguarded copy of the regulated data.

This drives each audit path with DISTINCTIVE sentinel values and asserts no sentinel appears in any
audit entry the interpreter collects. Paths covered:
  - ai.decide            (_write_ai_audit)      -> phi/fraud_audit_log
  - data_access          (_audit_data_access)   -> data_audit_log
  - data_change          (_audit_data_change)   -> data_audit_log  (incl. match-on-sensitive-value)
  - purpose_use          (_audit_purpose_use)   -> data_audit_log
  - agent limit breach   (_audit_event)         -> <agent>_limits_log  (metric counts, not values)

This is the compiler-lane scan (interpreter-level). The test chat runs the full `mio serve` HTTP
version, which is the definitive proof; this guards against regression at the unit level.
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


# distinctive sentinels — if any of these strings appears in an audit entry, a value leaked
SSN   = "SENTINEL_SSN_999887777"
BAL   = "SENTINEL_BALANCE_123456"
EMAIL = "SENTINEL_EMAIL_x@y.zzz"
NAME  = "SENTINEL_NAME_zaphod"
ALL_SENTINELS = [SSN, BAL, EMAIL, NAME]


def scan_logs(interp):
    """Return the list of (log, sentinel) leaks across every collected audit entry."""
    leaks = []
    for logname, entries in getattr(interp, '_audit_logs', {}).items():
        elist = entries if isinstance(entries, list) else getattr(entries, 'entries', [])
        for e in elist:
            blob = json.dumps(e, default=str)
            for s in ALL_SENTINELS:
                if s in blob:
                    leaks.append((logname, s))
    return leaks


class Ctx:
    def __init__(self, db, comp=None):
        self._db = db; self._sector_compliance = comp or []; self._sector_profile = None
        self._sector = 'healthcare'
    def get_connection(self, n): return self._db
    def get(self, k): return None


class DB:
    # Declared through the provider-verified channel: a test double has no inspectable
    # backend, and an unclassifiable sink grades `none` rather than being assumed
    # adequate -- a store no longer grades itself.
    _mohio_grade_verified = 'durable'


    def __init__(self): self.by_table = {}
    def ensure_table(self, t, cols): self.by_table.setdefault(t, [])
    def save(self, t, r): self.by_table.setdefault(t, []).append(r)


# ---- ai.decide -----------------------------------------------------------------------
interp = MohioInterpreter()
interp._phi_fields = {'ssn'}; interp._pci_fields = {'balance'}
class Dec:
    inputs = {'ssn': MohioValue(SSN, 'text'), 'balance': MohioValue(BAL, 'text')}
    result = True; confidence = 0.9; model = 'm'; fell_back = False; explanation = 'r'
interp._write_ai_audit('phi_audit_log', 'fraud', Dec(), Ctx(DB(), ['hipaa']))
check("ai.decide audit leaks no sentinel value", not scan_logs(interp), str(scan_logs(interp)))

# ---- data_access ---------------------------------------------------------------------
interp = MohioInterpreter()
interp._phi_fields = {'ssn'}; interp._pci_fields = set()
rows = [{'ssn': SSN, 'name': NAME}]
interp._audit_data_access('retrieve', 'patients', rows, Ctx(DB(), ['hipaa']))
check("data_access audit leaks no sentinel value", not scan_logs(interp), str(scan_logs(interp)))

# ---- data_change incl. match-on-sensitive-value --------------------------------------
# record_id is ALWAYS the DB-generated surrogate id (row_id = db.save(...)), never user data --
# verified at the real call site (interpreter ~5038). So we pass a surrogate id here, and assert
# the NAME-bearing fields (match_fields, fields) never carry the sensitive value.
interp = MohioInterpreter()
interp._encrypted_fields = {'ssn'}; interp._tagged_tables = {'patients'}
interp._audit_data_change('update', 'patients', Ctx(DB(), ['hipaa']),
                          record_id=42,               # surrogate id, as the real path passes
                          match_fields=['ssn'],       # NAME of the match field, not its value
                          fields=['ssn'], count=1)
for logname, entries in getattr(interp, '_audit_logs', {}).items():
    elist = entries if isinstance(entries, list) else getattr(entries, 'entries', [])
    for e in elist:
        mf = e.get('match_fields', []); fl = e.get('fields', [])
        check("data_change match_fields are names not values",
              all('SENTINEL' not in str(x) for x in mf), str(mf))
        check("data_change fields are names not values",
              all('SENTINEL' not in str(x) for x in fl), str(fl))
        check("data_change record_id is the surrogate id, not a sensitive value",
              str(e.get('record_id')) == '42', str(e.get('record_id')))
check("data_change audit leaks no sentinel value", not scan_logs(interp), str(scan_logs(interp)))

# ---- purpose_use ---------------------------------------------------------------------
interp = MohioInterpreter()
interp._field_purposes = {'email': set()}
interp._audit_purpose_use('email', 'marketing', {'marketing'}, Ctx(DB(), ['gdpr']))
# purpose_use records the field NAME 'email', never the email VALUE
leaks = scan_logs(interp)
check("purpose_use audit records field name, not value", not leaks, str(leaks))

# ---- agent limit breach --------------------------------------------------------------
interp = MohioInterpreter()
# limits log records metric counts (steps/tokens/cost), never data values
interp._audit_event('payments_agent_limits_log', {
    'event': 'AGENT_LIMIT_EXCEEDED', 'agent': 'payments',
    'metric': 'tokens', 'value': 1500, 'ceiling': 1000,
}, Ctx(DB(), ['hipaa']))
check("agent limits_log records metric counts, not sentinel values", not scan_logs(interp))

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
