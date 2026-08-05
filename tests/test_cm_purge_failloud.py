# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""cm.purge must fail loud on a failed delete and never write a false/empty tombstone (triage #1).

Right-to-be-forgotten silently reporting success on a delete that did not happen -- and then writing
a TOMBSTONE claiming a lawful erasure that never occurred -- is affirmative false evidence in the
audit chain, directly contradicting the guarantee. Was the most dangerous live bug. This locks:
  1. a failed `db.remove` RAISES (purge does not report success); the row remains; NO tombstone.
  2. happy path: the row is erased; exactly one tombstone with a real row_ref.
  3. a match that erases 0 rows writes NO tombstone (no delete -> no tombstone).
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')
os.environ['MOHIO_AUDIT_SALT'] = 'test-salt'

from pathlib import Path
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter, DbRuntime, MohioRuntimeError

_raw = Path('mohio.lark').read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)


def setup():
    tenant = DbRuntime(':memory:'); audit = DbRuntime(':memory:')
    tenant.conn.execute("CREATE TABLE members (id TEXT, email TEXT)")
    tenant.conn.execute("INSERT INTO members VALUES ('M001','a@x.com')")
    tenant.conn.commit()
    return tenant, audit

def tombstones(audit):
    try:
        return audit.conn.execute(
            "SELECT detail FROM data_audit_log WHERE event='TOMBSTONE'").fetchall()
    except Exception:
        return []

def members(tenant):
    return tenant.conn.execute("SELECT COUNT(*) FROM members").fetchone()[0]

PURGE = ('connect db as sqlite from env.DATABASE_URL\n'
         'cm.purge from db.members\n    match id to "M001"\n    reason "GDPR Art 17"\ncm.purge: done\n')


print("=== 1. a FAILED delete fails loud, row remains, no tombstone ===")
tenant, audit = setup()
MohioInterpreter.register_audit_sink_provider(lambda ctx: [audit])
try:
    def boom(*a, **k):
        raise Exception("simulated delete failure")
    tenant.remove = boom
    it = MohioInterpreter(); it._db = tenant
    raised = ""
    try:
        it.run(transform(P.parse(PURGE), PURGE))
    except MohioRuntimeError as e:
        raised = str(e)
    check("purge RAISES on a failed delete (no silent success)",
          'could not erase' in raised, raised)
    check("the row REMAINS (erasure did not happen)", members(tenant) == 1)
    check("NO tombstone written (no false erasure evidence)", len(tombstones(audit)) == 0)
finally:
    MohioInterpreter.unregister_audit_sink_provider()

print("\n=== 2. happy path: row erased, exactly one real tombstone ===")
tenant, audit = setup()
MohioInterpreter.register_audit_sink_provider(lambda ctx: [audit])
try:
    it = MohioInterpreter(); it._db = tenant
    it.run(transform(P.parse(PURGE), PURGE))
    check("the row is erased", members(tenant) == 0)
    ts = tombstones(audit)
    check("exactly one tombstone", len(ts) == 1, f"count={len(ts)}")
    check("tombstone carries a real row_ref (M001, id kind)",
          bool(ts) and 'M001' in ts[0][0] and '"kind": "id"' in ts[0][0])
finally:
    MohioInterpreter.unregister_audit_sink_provider()

print("\n=== 3. a 0-row match writes NO tombstone (no delete -> no tombstone) ===")
tenant, audit = setup()
MohioInterpreter.register_audit_sink_provider(lambda ctx: [audit])
try:
    it = MohioInterpreter(); it._db = tenant
    src = ('connect db as sqlite from env.DATABASE_URL\n'
           'cm.purge from db.members\n    match id to "NO_SUCH_ID"\n    reason "x"\ncm.purge: done\n')
    it.run(transform(P.parse(src), src))
    check("nothing matched, so the row is untouched", members(tenant) == 1)
    check("NO tombstone for a purge that erased nothing", len(tombstones(audit)) == 0)
finally:
    MohioInterpreter.unregister_audit_sink_provider()

print("\n=== 4. ATOMIC: a multi-clause purge that fails mid-way rolls back EVERYTHING ===")
tenant, audit = setup()  # M001
tenant.conn.execute("INSERT INTO members VALUES ('M002','b@x.com')"); tenant.conn.commit()
MohioInterpreter.register_audit_sink_provider(lambda ctx: [audit])
try:
    _real = tenant.remove
    def selective(table, f, v):
        if v == 'M002':
            raise Exception("simulated delete failure on M002")
        return _real(table, f, v)
    tenant.remove = selective
    it = MohioInterpreter(); it._db = tenant
    src = ('connect db as sqlite from env.DATABASE_URL\n'
           'cm.purge from db.members\n    match id to "M001"\n    match id to "M002"\n'
           '    reason "erase both"\ncm.purge: done\n')
    raised = ""
    try:
        it.run(transform(P.parse(src), src))
    except MohioRuntimeError as e:
        raised = str(e)
    check("multi-clause mid-failure RAISES", 'rolled back' in raised or 'could not erase' in raised, raised)
    check("NOTHING erased -- M001's delete was rolled back (still present)",
          tenant.conn.execute("SELECT COUNT(*) FROM members WHERE id='M001'").fetchone()[0] == 1)
    check("M002 also present (its delete failed)",
          tenant.conn.execute("SELECT COUNT(*) FROM members WHERE id='M002'").fetchone()[0] == 1)
    check("ZERO tombstones (no half-erasure record)", len(tombstones(audit)) == 0)
    # THE loop-closing check: the rolled-back row must adjudicate PRESENT, not MISSING -- proving the
    # audit system now tells the truth about the rolled-back rows (no phantom tampering signal).
    tenant.remove = _real
    v = it.adjudicate_erasure(audit, tenant, 'members', 'id', 'M001')
    check("rolled-back row adjudicates PRESENT (not MISSING) -- no phantom tampering",
          v['verdict'] == 'PRESENT', str(v))
finally:
    MohioInterpreter.unregister_audit_sink_provider()

print("\n=== 5. happy multi-clause: all erased, tombstone references every erased row ===")
tenant, audit = setup()  # M001
tenant.conn.execute("INSERT INTO members VALUES ('M002','b@x.com')"); tenant.conn.commit()
MohioInterpreter.register_audit_sink_provider(lambda ctx: [audit])
try:
    it = MohioInterpreter(); it._db = tenant
    src = ('connect db as sqlite from env.DATABASE_URL\n'
           'cm.purge from db.members\n    match id to "M001"\n    match id to "M002"\n'
           '    reason "erase both"\ncm.purge: done\n')
    it.run(transform(P.parse(src), src))
    check("both rows erased", members(tenant) == 0)
    ts = tombstones(audit)
    check("a tombstone was written", len(ts) >= 1, f"count={len(ts)}")
    check("the tombstone references both erased rows (M001 and M002)",
          bool(ts) and 'M001' in ts[0][0] and 'M002' in ts[0][0], str(ts))
    check("both adjudicate ERASED",
          it.adjudicate_erasure(audit, tenant, 'members', 'id', 'M001')['verdict'] == 'ERASED'
          and it.adjudicate_erasure(audit, tenant, 'members', 'id', 'M002')['verdict'] == 'ERASED')
finally:
    MohioInterpreter.unregister_audit_sink_provider()

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
