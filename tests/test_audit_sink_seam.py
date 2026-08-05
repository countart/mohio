# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""Audit-sink provider seam + audit-table identification (F).

The compiler names LOGICAL audit tables and stays backend-agnostic; physical placement (a Postgres
`audit` schema with append-only grants, WORM object storage) lives in a platform-bound audit-sink
PROVIDER, mirroring the key-provider seam. This test locks:

  - is_audit_table() correctly identifies the four static names + both dynamic families
    (<agent>_limits_log, *_audit_log) and rejects ordinary data tables,
  - a registered audit-sink provider is used instead of the app db, and receives ctx,
  - the provider's sink grade is honored: a provider sink meeting the required grade is at-grade
    (not degraded); the app-db fallback (durable-only) under an append_only framework is degraded,
  - unregister restores the app-db fallback.

The compiler does NOT hardcode Postgres schema syntax (that would break SQLite/MySQL/Mongo); the
seam is where a graded, schema-qualified sink is bound.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')

from mohio_interpreter import MohioInterpreter
from mohio_audit_grades import is_audit_table

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)


# --- audit-table identification -------------------------------------------------------
for t in ['fraud_audit_log', 'phi_audit_log', 'data_audit_log', 'operation_audit_log']:
    check(f"static audit table '{t}' identified", is_audit_table(t))
check("per-agent '<name>_limits_log' identified", is_audit_table('payments_agent_limits_log'))
check("profile-custom '*_audit_log' identified", is_audit_table('myproc_audit_log'))
check("ordinary data table NOT identified as audit", not is_audit_table('patients'))
check("ordinary data table 'orders' NOT audit", not is_audit_table('orders'))


# --- provider seam --------------------------------------------------------------------
class Ctx:
    def __init__(self, comp, db):
        self._sector_compliance = comp; self._db = db; self._sector_profile = None
    def get_connection(self, n): return self._db
    def get(self, k): return None


class Sink:
    def __init__(self, grade='durable'):
        # Declared through the provider-verified channel: a test double has no inspectable
        # backend, and an unclassifiable sink now grades `none` rather than being assumed
        # adequate. Setting `_mohio_grade` on a store is no longer believed -- a store does not
        # grade itself.
        self._mohio_grade_verified = grade
        self.rows = []; self.by_table = {}
    def ensure_table(self, *a): pass
    def save(self, t, r):
        self.by_table.setdefault(t, []).append(r)
        if not str(t).endswith('_incident_log'):
            self.rows.append(r)   # audit rows only; incident records tracked separately


interp = MohioInterpreter()

# provider is called, receives ctx, and its sink is used
calls = {'n': 0, 'got_ctx': False}
def provider(ctx):
    calls['n'] += 1
    calls['got_ctx'] = ctx is not None
    return [Sink('worm')]

MohioInterpreter.register_audit_sink_provider(provider)
appdb = Sink('durable')   # app db is only durable
res = interp._audit_event('phi_log', {'event': 'x'}, Ctx(['hipaa'], appdb))
check("registered provider is invoked", calls['n'] == 1)
check("provider receives ctx", calls['got_ctx'])
check("provider's at-grade sink means NOT degraded (worm >= append_only)",
      not res.get('_audit_degraded', False), str(res.get('_audit_degraded')))
check("app db was NOT used when provider is registered", len(appdb.rows) == 0)

# unregister -> falls back to app db, which is durable-only -> degraded under hipaa
MohioInterpreter.unregister_audit_sink_provider()
appdb2 = Sink('durable')
res2 = interp._audit_event('phi_log', {'event': 'x'}, Ctx(['hipaa'], appdb2))
check("after unregister, app db is used", len(appdb2.rows) == 1)
check("app-db fallback below grade is degraded",
      res2.get('_audit_degraded') is True, str(res2.get('_audit_degraded')))

# a broken provider falls back to app db, never crashes the audit
def bad_provider(ctx):
    raise RuntimeError("provider boom")
MohioInterpreter.register_audit_sink_provider(bad_provider)
appdb3 = Sink('durable')
try:
    interp._audit_event('phi_log', {'event': 'x'}, Ctx([], appdb3))
    check("broken provider falls back to app db without crashing", len(appdb3.rows) == 1)
except Exception as e:
    check("broken provider falls back to app db without crashing", False, str(e))
MohioInterpreter.unregister_audit_sink_provider()

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
