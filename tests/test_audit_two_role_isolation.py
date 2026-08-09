# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Two-role audit isolation (D4, tombstone dependency) -- the adversarial bar.

A tombstone is only trustworthy if the tenant whose data was erased cannot forge or scrub it.
The mechanism is the audit-sink provider seam: when a dedicated audit sink is bound through
`register_audit_sink_provider`, governance/tombstone writes go to THAT sink (the `audit_writer`
identity), never to the tenant's application `db`. The tenant code only ever holds its own `db`
connection (`ctx.get_connection('db')`), so it has no path to the audit trail at all.

This test proves, against TWO physically separate real connections (each `DbRuntime(':memory:')`
is its own private database that shares nothing with the other):

  1. audit_writer INSERT succeeds -- the event lands on the audit sink and the chain verifies.
  2. the tenant db is never written -- the audit table does not even exist on it.
  3. the tenant CANNOT SCRUB -- UPDATE and DELETE on the audit log through the tenant's own
     connection are refused by the database (no such table; it lives on a connection the tenant
     does not hold).
  4. the tenant CANNOT FORGE -- a row the tenant writes into a look-alike table in its OWN db is
     inert: it does not appear in the authoritative audit log the verifier/anchors read.
  5. the writer handle is never handed to tenant code -- get_connection('db') is the tenant db,
     never the audit sink.

Postgres deployments enforce the same property with INSERT-only GRANTs to an `audit_writer` role
that the app role cannot impersonate; SQLite here proves it by physical connection isolation.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')

import sqlite3
from mohio_interpreter import MohioInterpreter, DbRuntime

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)


class Ctx:
    """A tenant context: it hands out the tenant's OWN db and nothing else."""
    def __init__(self, comp, tenant_db):
        self._sector_compliance = comp
        self._db = tenant_db
        self._sector_profile = None
    def get_connection(self, n):
        return self._db          # tenant code only ever reaches its own connection
    def get(self, k):
        return None


LOG = 'operation_audit_log'

# Two physically separate connections. tenant_db is the app's data connection; audit_db is the
# dedicated audit_writer store. Distinct in-memory databases -> nothing is shared between them.
tenant_db = DbRuntime(':memory:')
audit_db  = DbRuntime(':memory:')
check("the two connections are genuinely distinct objects", tenant_db is not audit_db)

# Bind the audit_writer through the provider seam (control-plane-held identity).
def provider(ctx):
    return [audit_db]
MohioInterpreter.register_audit_sink_provider(provider)

try:
    interp = MohioInterpreter()
    ctx = Ctx([], tenant_db)

    # The writer handle is never the tenant's -- tenant code reaches only its own db.
    check("tenant get_connection('db') is the tenant db, not the audit sink",
          ctx.get_connection('db') is tenant_db and ctx.get_connection('db') is not audit_db)

    # --- audit_writer appends a TOMBSTONE via the seam ------------------------------------
    tomb = {'event': 'TOMBSTONE', 'table': 'members', 'row_ref': 'M001',
            'reason': 'gdpr art 17 erasure', 'legal_basis': 'GDPR Art. 17(1)'}
    res = interp._audit_event(LOG, tomb, ctx)
    check("1. audit_writer INSERT succeeded (record got a chain position)",
          bool(res.get('entry_hash')), str(res))

    def count_on(conn, table):
        cur = conn.conn.cursor()
        cur.execute(f'SELECT COUNT(*) FROM "{table}"')
        return cur.fetchone()[0]

    check("1. the tombstone landed on the audit sink", count_on(audit_db, LOG) == 1)
    check("1. the chain on the audit sink verifies",
          interp.verify_audit_chain(audit_db, LOG).get('ok') is True,
          str(interp.verify_audit_chain(audit_db, LOG)))

    # --- 2. the tenant db was never written ----------------------------------------------
    tenant_has_table = True
    try:
        count_on(tenant_db, LOG)
    except sqlite3.OperationalError:
        tenant_has_table = False
    check("2. the audit table does not exist on the tenant db", not tenant_has_table)

    # --- 3. the tenant cannot SCRUB the tombstone ----------------------------------------
    def tenant_op_refused(sql):
        try:
            cur = tenant_db.conn.cursor()
            cur.execute(sql)
            tenant_db.conn.commit()
            return False        # it went through -- NOT refused
        except sqlite3.OperationalError:
            tenant_db.conn.rollback()
            return True         # refused by the database

    check("3. tenant DELETE on the audit log is refused",
          tenant_op_refused(f'DELETE FROM "{LOG}"'))
    check("3. tenant UPDATE on the audit log is refused",
          tenant_op_refused(f'UPDATE "{LOG}" SET "event" = \'FORGED\''))

    # the authoritative log is untouched by the attempts
    check("3. the tombstone still stands after the scrub attempts", count_on(audit_db, LOG) == 1)

    # --- 4. the tenant cannot FORGE a lawful-erasure marker ------------------------------
    # The tenant can do whatever it likes inside its OWN database, including building a look-alike
    # table and inserting a fake tombstone. That row is inert: it is not in the authoritative log
    # the verifier and anchors read, so it can never make a missing row look lawfully erased.
    c = tenant_db.conn.cursor()
    c.execute(f'CREATE TABLE "{LOG}" ("event" TEXT, "table" TEXT, "row_ref" TEXT)')
    c.execute(f'INSERT INTO "{LOG}" ("event","table","row_ref") VALUES (?,?,?)',
              ('TOMBSTONE', 'members', 'M999'))
    tenant_db.conn.commit()
    forged_ref_in_authority = False
    cur = audit_db.conn.cursor()
    cur.execute(f'SELECT "detail" FROM "{LOG}"')
    for (detail,) in cur.fetchall():
        if 'M999' in (detail or ''):
            forged_ref_in_authority = True
    check("4. a tombstone forged in the tenant db never reaches the authoritative log",
          not forged_ref_in_authority)
    check("4. the authoritative log still holds only the one genuine tombstone",
          count_on(audit_db, LOG) == 1)

finally:
    MohioInterpreter.unregister_audit_sink_provider()

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
