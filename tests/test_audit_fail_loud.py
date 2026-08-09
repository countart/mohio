# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Audit persistence: framework-driven grade, and NEVER halt the institution on a blip.

The audit guarantee is keyed on the activated compliance FRAMEWORKS (profile.compliance), not the
sector's license tier. Each framework demands a minimum audit grade; the highest wins.

Behavior (the halt-safe split):
  - framework requires durable+ audit, runtime write fails, sink(s) present
        -> PROCEED, mark _audit_degraded, fire a loud ALERT. NEVER abort a live operation on a
           transient blip (a bank must not stop mid-transaction on a network hiccup). The record
           is reconciled via the redundant/WAL path.
  - framework requires durable+ audit, NO sink configured at all (structural absence)
        -> RAISE audit.no_durable_store (meant to be caught at check/deploy, halts nothing live).
  - no framework requiring durable audit, write fails -> loud warning, proceed.
  - no framework, no sink -> proceed (open core, no false refusal).

The old version keyed on license tier and ABORTED on certified write-failure. Corrected: license
tier != audit requirement, and aborting a live op on a blip is the wrong failure mode.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')

from mohio_interpreter import MohioInterpreter, _Raise
from mohio_audit_grades import required_grade, satisfies

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)


class Ctx:
    def __init__(self, compliance, db):
        self._sector_compliance = compliance; self._db = db
    def get_connection(self, name): return self._db
    def get(self, k): return None


class FailDB:
    # A DURABLE store whose write fails transiently -- a network blip, not a misconfiguration.
    # It declares its grade through the provider-verified channel because a test double has no
    # inspectable backend, and an unclassifiable sink now (correctly) grades `none`: a store
    # that cannot be shown to hold a record must not be assumed to.
    _mohio_grade_verified = 'durable'

    def ensure_table(self, *a): pass
    def save(self, *a): raise RuntimeError("network blip")


class OkDB:
    _mohio_grade_verified = 'durable'      # see FailDB: test doubles assert, backends are read

    def __init__(self): self.rows = []; self.by_table = {}
    def ensure_table(self, *a): pass
    def save(self, t, r):
        self.by_table.setdefault(t, []).append(r)
        if not str(t).endswith('_incident_log'):
            self.rows.append(r)   # audit rows only; incident records tracked separately


interp = MohioInterpreter()


def run(compliance, db):
    try:
        e = interp._audit_event('phi_log', {'event': 'access'}, Ctx(compliance, db))
        return ('proceeded', bool(e.get('_audit_degraded', False)))
    except _Raise as ex:
        return ('raised', ex.error_name)


# --- the mapping ----------------------------------------------------------------------
check("HIPAA requires append_only", required_grade(['hipaa'])[0] == 'append_only')
check("GDPR requires durable", required_grade(['gdpr'])[0] == 'durable')
check("SOX requires worm", required_grade(['sox'])[0] == 'worm')
check("highest framework wins", required_grade(['gdpr', 'sox', 'hipaa'])[0] == 'worm')
check("no framework -> none", required_grade([])[0] == 'none')
check("unknown framework surfaced", required_grade(['hipaa', 'zzz'])[1] == ['zzz'])
check("case/separator normalized", required_grade(['PCI-DSS'])[0] == 'append_only')
check("satisfies: durable !>= append_only", not satisfies('durable', 'append_only'))
check("satisfies: worm >= append_only", satisfies('worm', 'append_only'))

# --- the halt-safe behavior -----------------------------------------------------------
# 1. framework + failing sink -> PROCEED, degraded (NOT raise). The halt-the-bank guard.
check("framework + runtime write failure PROCEEDS (does not halt the institution)",
      run(['hipaa'], FailDB()) == ('proceeded', True),
      str(run(['hipaa'], FailDB())))

# 2. framework + no sink at all -> structural refusal
check("framework + no durable store raises audit.no_durable_store",
      run(['hipaa'], None) == ('raised', 'audit.no_durable_store'),
      str(run(['hipaa'], None)))

# 3. framework + working sink AT THE REQUIRED GRADE -> clean proceed, persisted, not degraded.
# hipaa requires append_only, so the sink must declare append_only to be "at grade".
ok = OkDB(); ok._mohio_grade_verified = 'append_only'
res = interp._audit_event('phi_log', {'event': 'access'}, Ctx(['hipaa'], ok))
check("framework + at-grade sink persists, not degraded",
      len(ok.rows) == 1 and not res.get('_audit_degraded', False),
      f"rows={len(ok.rows)} degraded={res.get('_audit_degraded')}")

# 3b. framework + working sink BELOW the required grade -> durable but DEGRADED (pending upgrade).
# A plain durable sink under an append_only framework is not lost, but must be reconciled up.
below = OkDB()  # verified `durable` only -- below hipaa's append_only
res_b = interp._audit_event('phi_log', {'event': 'access'}, Ctx(['hipaa'], below))
check("framework + below-grade sink persists but is degraded",
      len(below.rows) == 1 and res_b.get('_audit_degraded') is True
      and res_b.get('_audit_required_grade') == 'append_only',
      f"rows={len(below.rows)} degraded={res_b.get('_audit_degraded')} "
      f"req={res_b.get('_audit_required_grade')}")

# 4. no framework + failing sink -> warn, proceed
check("no framework + write failure proceeds (warn only)",
      run([], FailDB()) == ('proceeded', False),
      str(run([], FailDB())))

# 5. no framework + no sink -> proceed (open core, no false refusal)
check("no framework + no sink proceeds (no false refusal)",
      run([], None) == ('proceeded', False),
      str(run([], None)))

# 6. entry still stamped (unchanged contract)
ent = interp._audit_event('log', {'event': 'x'}, Ctx([], OkDB()))
check("entry still stamped with ts + audit_id",
      'ts' in ent and 'audit_id' in ent and len(ent['audit_id']) == 16, str(ent))

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
