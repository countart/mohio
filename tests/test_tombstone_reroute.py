# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Tombstone reroute (D1, load-bearing) -- prove the erasure marker rides the ISOLATED path.

D4 proved the audit-sink seam isolates a bound audit_writer from the tenant db. That isolation is
decorative until the tombstone actually rides it. Before this change `cm.purge` recorded the
erasure through `_compliance_audit`, straight to the TENANT db -- the very connection the erased
row lived in, and one the tenant can freely UPDATE/DELETE. This test runs a REAL `cm.purge`
program end-to-end (parse -> transform -> interp.run) with a dedicated audit sink bound through the
provider seam, and proves, to the D4 adversarial standard:

  1. the row is really erased from the tenant db (the purge did its job),
  2. the TOMBSTONE lands on the ISOLATED audit sink (event='TOMBSTONE', on data_audit_log),
  3. the tenant db has NO path to the tombstone -- data_audit_log does not exist on it,
  4. the tombstone carries the erasure FACT (table, reason, legal_basis) and match-field NAMES
     only -- never the matched values (no re-created PII in the trail),
  5. the chain on the audit sink verifies.

Row_ref precision (PK id / salted hash) is D2; the ERASED-vs-MISSING verifier is D3. This locks the
routing they both depend on.
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


SRC = '''connect db as sqlite from env.DATABASE_URL
save to db.members
    id "M001"
    email "alice@example.com"
save: done
cm.purge from db.members
    match id to "M001"
    reason "GDPR Article 17 erasure request"
cm.purge: done
'''

# A dedicated audit_writer sink, physically separate from the tenant db, bound through the seam.
audit = DbRuntime(':memory:')
MohioInterpreter.register_audit_sink_provider(lambda ctx: [audit])
try:
    program = ast_transform(P.parse(SRC), SRC)
    interp = MohioInterpreter()
    interp.run(program)

    tenant = interp._db          # the connection the purge deleted from

    def count(conn, sql):
        cur = conn.conn.cursor(); cur.execute(sql); return cur.fetchone()[0]

    # 1. the row is really gone from the tenant db
    check("1. the erased row is gone from the tenant db",
          count(tenant, 'SELECT COUNT(*) FROM members WHERE id = "M001"') == 0)

    # 2. the tombstone landed on the ISOLATED audit sink
    acur = audit.conn.cursor()
    acur.execute('SELECT "event", "detail" FROM data_audit_log')
    audit_rows = acur.fetchall()
    tombstones = [d for (e, d) in audit_rows if e == 'TOMBSTONE']
    check("2. exactly one TOMBSTONE is on the isolated audit sink", len(tombstones) == 1,
          f"rows on sink: {audit_rows}")

    # 3. the tenant db has NO path to the tombstone
    tenant_has_audit = True
    try:
        count(tenant, 'SELECT COUNT(*) FROM data_audit_log')
    except sqlite3.OperationalError:
        tenant_has_audit = False
    check("3. data_audit_log does not exist on the tenant db (no path to the tombstone)",
          not tenant_has_audit)

    # 4. the tombstone records the FACT + field NAMES, never the matched values
    detail = tombstones[0] if tombstones else ""
    check("4. tombstone names the table and the legal basis",
          '"table": "members"' in detail and 'GDPR Art. 17(1)' in detail, detail)
    check("4. tombstone records the match FIELD name ('id')",
          '"match_fields": ["id"]' in detail, detail)
    check("4. the PK id is the row_ref, in the clear (a surrogate key -- ratified safe; D2)",
          '"kind": "id"' in detail and 'M001' in detail, detail)
    check("4. the row's real PII ('alice@example.com') is NOT in the tombstone",
          'alice@example.com' not in detail, detail)

    # 5. the chain on the audit sink verifies
    v = interp.verify_audit_chain(audit, 'data_audit_log')
    check("5. the audit sink's chain verifies", v.get('ok') is True, str(v))

finally:
    MohioInterpreter.unregister_audit_sink_provider()

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
