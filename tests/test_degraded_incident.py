# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""Degraded-audit incident: recorded durably AND emitted to the alert sink, never silent (H).

When an audit write degrades (durable but below the required grade, or no sink accepted it), the
runtime must:
  - build a degraded-event incident record to the design-chat schema (SPEC-degraded-events §2),
  - write it durably to `audit_incident_log` (queryable, survives with no alert sink bound),
  - emit it to the registered alert sink (PagerDuty/Slack/webhook) if one is bound.

NEVER SILENT: the alert sink is only the outbound page. Its absence must never suppress the durable
incident record. This test locks all of that.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')

from mohio_interpreter import MohioInterpreter

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)


class Ctx:
    def __init__(self, comp, db, sector='healthcare'):
        self._sector_compliance = comp; self._db = db
        self._sector = sector; self._sector_profile = None
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


class DurableOnlySink:
    """durable, but below hipaa's append_only -> triggers a degraded incident."""
    def __init__(self): self._mohio_grade_verified = 'durable'
    def ensure_table(self, *a): pass
    def save(self, *a): pass


interp = MohioInterpreter()
# audit writes go to a durable-only provider sink (forces degrade under hipaa)
MohioInterpreter.register_audit_sink_provider(lambda ctx: [DurableOnlySink()])


# 1. with an alert sink bound: incident emitted AND recorded
alerts = []
MohioInterpreter.register_alert_sink(lambda ev: alerts.append(ev))
db = DB()
interp._audit_event('phi_audit_log', {'event': 'access'}, Ctx(['hipaa'], db))

check("alert sink received exactly one degraded incident", len(alerts) == 1, f"got {len(alerts)}")

ev = alerts[0] if alerts else {}
for field in ['incident_id', 'raised_ts', 'audit_table', 'orphaned_audit_id', 'required_grade',
              'written_grade', 'reason', 'sector', 'frameworks', 'state']:
    check(f"incident has '{field}'", field in ev, str(sorted(ev.keys())))

check("required_grade is the framework max (append_only for hipaa)",
      ev.get('required_grade') == 'append_only', ev.get('required_grade'))
check("written_grade recorded (durable)", ev.get('written_grade') == 'durable',
      ev.get('written_grade'))
check("state starts at 'raised'", ev.get('state') == 'raised', ev.get('state'))
check("frameworks recorded", ev.get('frameworks') == ['hipaa'], ev.get('frameworks'))
check("audit_table recorded", ev.get('audit_table') == 'phi_audit_log', ev.get('audit_table'))
check("incident_id is a uuid-ish string", isinstance(ev.get('incident_id'), str)
      and len(ev.get('incident_id', '')) >= 16)
check("reconciliation fields present and null at raise",
      ev.get('reconciled_ts') is None and ev.get('reconciliation_audit_id') is None)

check("durable incident record written to audit_incident_log",
      len(db.by_table.get('audit_incident_log', [])) == 1,
      str(list(db.by_table.keys())))

# 2. NEVER SILENT: with NO alert sink, the incident is still recorded durably
MohioInterpreter.unregister_alert_sink()
db2 = DB()
interp._audit_event('phi_audit_log', {'event': 'access'}, Ctx(['hipaa'], db2))
check("no alert sink -> incident STILL recorded durably (never silent)",
      len(db2.by_table.get('audit_incident_log', [])) == 1,
      str(list(db2.by_table.keys())))

# 3. a broken alert sink does not crash the audit or lose the durable record
def boom(ev): raise RuntimeError("pager down")
MohioInterpreter.register_alert_sink(boom)
db3 = DB()
try:
    interp._audit_event('phi_audit_log', {'event': 'access'}, Ctx(['hipaa'], db3))
    check("broken alert sink does not crash the audit", True)
    check("broken alert sink still leaves the durable incident record",
          len(db3.by_table.get('audit_incident_log', [])) == 1)
except Exception as e:
    check("broken alert sink does not crash the audit", False, str(e))
    check("broken alert sink still leaves the durable incident record", False)
MohioInterpreter.unregister_alert_sink()

# 4. no framework -> no degrade -> no incident
db4 = DB()
interp._audit_event('data_audit_log', {'event': 'access'}, Ctx([], db4))
check("no framework -> no degraded incident raised",
      len(db4.by_table.get('audit_incident_log', [])) == 0)

MohioInterpreter.unregister_audit_sink_provider()

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
