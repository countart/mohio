# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""The audit chain on real PostgreSQL.

The control plane reads these chains from the tenant's Postgres database in order to anchor them,
so "verified on SQLite" was never sufficient. Running it against a real server found that the
chain was not merely unverified on Postgres, it was BROKEN:

  1. every chain read called `conn.execute(...)`, which is a sqlite3 convenience psycopg2 does not
     have. Reads raised AttributeError, which the readers caught and reported as "log unreadable"
     or an empty list. Verification found nothing to check, discovery returned nothing, and the
     seed returned None -- so a restart began a SECOND chain from genesis, silently.
  2. Postgres aborts the whole transaction on any failed statement. Log discovery probes
     non-audit tables on purpose, so the act of looking for audit logs left the connection
     unusable for reading them.

Both are fixed. This test holds them fixed. It SKIPS cleanly where no Postgres is available, so
it never blocks the suite -- but where a server exists it is the only proof that matters.

Set MOHIO_TEST_PG_URL to point at a database, or it falls back to a local default.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')

URL = os.environ.get('MOHIO_TEST_PG_URL',
                     'postgresql://postgres@127.0.0.1:5433/mohio_audit')

try:
    import psycopg2  # noqa: F401
    from mohio_interpreter import MohioInterpreter, PostgresRuntime, Context
    _probe = PostgresRuntime(URL)
    _c = _probe.conn.cursor(); _c.execute('SELECT 1'); _probe.conn.commit()
    _AVAILABLE = True
except Exception as _e:
    _AVAILABLE = False
    _WHY = str(_e).splitlines()[0][:90]

if not _AVAILABLE:
    print("  [SKIP] no PostgreSQL available -- Postgres chain proof not run.")
    print(f"         ({_WHY})")
    print("         Set MOHIO_TEST_PG_URL to run it. This is the ONLY proof that the chain")
    print("         works where the control plane actually reads it.")
    print("\nRESULTS: 0 passed, 0 failed (skipped)")
    sys.exit(0)

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)


def fresh(table, n=5):
    db = PostgresRuntime(URL)
    it = MohioInterpreter(); it._db = db
    class C(Context):
        def get_connection(self, _x): return db
    cur = db.conn.cursor(); cur.execute(f'DROP TABLE IF EXISTS "{table}"'); db.conn.commit()
    for i in range(n):
        it._audit_event(table, {'event': f'e{i}', 'agent': 'a'}, C())
    return it, db


def sql(db, statement):
    cur = db.conn.cursor(); cur.execute(statement); db.conn.commit()


# ── reads work at all (they did not) ──────────────────────────────────────────────────
T = 'pgt_basic_audit_log'
it, db = fresh(T, 4)
check("audit logs are discoverable on Postgres (information_schema path)",
      T in it.audit_logs(db), f"got {it.audit_logs(db)}")
v = it.verify_audit_chain(db, T)
check("the chain verifies on Postgres", v['ok'] and v['checked'] == 4, str(v))
head = it.audit_chain_head(db, T)
check("the chain head is computable on Postgres",
      len(head['head']) == 64 and head['entries'] == 4, str(head))

# ── discovery must not poison the transaction ─────────────────────────────────────────
# Probing non-audit tables fails on purpose; on Postgres each failure aborts the transaction.
sql(db, 'CREATE TABLE IF NOT EXISTS pgt_ordinary_table (id int, note text)')
_logs = it.audit_logs(db)
check("an ordinary table is not mistaken for an audit log", 'pgt_ordinary_table' not in _logs)
check("verification still works after discovery probed a non-audit table",
      it.verify_audit_chain(db, T)['ok'],
      "a failed probe left the transaction aborted")

# ── tamper detection on Postgres ──────────────────────────────────────────────────────
T2 = 'pgt_alter_audit_log'
it2, db2 = fresh(T2)
sql(db2, f"""UPDATE "{T2}" SET event='TAMPERED'
             WHERE audit_id=(SELECT audit_id FROM "{T2}" LIMIT 1)""")
check("ALTERING a record is detected on Postgres", not it2.verify_audit_chain(db2, T2)['ok'])

T3 = 'pgt_delete_audit_log'
it3, db3 = fresh(T3)
sql(db3, f"""DELETE FROM "{T3}"
             WHERE audit_id=(SELECT audit_id FROM "{T3}" OFFSET 2 LIMIT 1)""")
check("DELETING a record is detected on Postgres", not it3.verify_audit_chain(db3, T3)['ok'])

# ── restart continuity: the failure the rowid seed caused silently ────────────────────
# Postgres has no rowid. The old seed ordered by it, raised, returned None, and a fresh process
# started a second chain from genesis -- with no break reported anywhere.
T4 = 'pgt_restart_audit_log'
fresh(T4, 3)
db4 = PostgresRuntime(URL); it4 = MohioInterpreter(); it4._db = db4
class _C4(Context):
    def get_connection(self, _x): return db4
for i in range(2):
    it4._audit_event(T4, {'event': f'r{i}', 'agent': 'a'}, _C4())
v4 = it4.verify_audit_chain(db4, T4)
check("a restart continues the existing Postgres chain (does not fork at genesis)",
      v4['ok'] and v4['checked'] == 5, str(v4))


# ── item 5: the stringified round-trip, ASSERTED rather than assumed ─────────────────
# Chain consistency depends on `str(written) == str(read_back)` for every content column. That
# holds today only because audit columns are TEXT on both engines -- nothing enforced it. A typed
# column would make a legitimately written record fail verification as TAMPERED, which is the
# worst possible false positive: the system accusing itself because of a schema change.
from mohio_audit_grades import canonical_audit_columns as _cac

T5 = 'pgt_roundtrip_audit_log'
_db5 = PostgresRuntime(URL)
_c5 = _db5.conn.cursor(); _c5.execute(f'DROP TABLE IF EXISTS "{T5}"'); _db5.conn.commit()
_it5 = MohioInterpreter(); _it5._db = _db5
_db5.ensure_table(T5, _cac())
_content = [c for c in _cac() if c not in MohioInterpreter._CHAIN_LINK_COLUMNS]
_written = {c: f'v-{c}' for c in _content}
_saved = _it5._audit_chained_save(_db5, T5, dict(_written))
_rows5 = _it5._audit_rows(_db5, T5)
check("the record round-trips", len(_rows5) == 1, f"rows={len(_rows5)}")
if _rows5:
    _drift = [c for c in _content
              if str(_rows5[0].get(c)) != str(_written[c])]
    check("every content column survives str() round-trip unchanged on Postgres",
          not _drift, f"columns that drifted: {_drift}")
check("a fully-populated record verifies on Postgres",
      _it5.verify_audit_chain(_db5, T5)['ok'])

# ── item 6: paths previously unexecuted on Postgres ───────────────────────────────────
# fork detection
T6 = 'pgt_fork_audit_log'
_it6, _db6 = fresh(T6, 4)
_r6 = _it6._audit_rows(_db6, T6)
_dup = dict(_r6[1]); _dup['audit_id'] = 'dupe000000000000'
_cols = ', '.join(f'"{k}"' for k in _dup)
_ph = ', '.join(['%s'] * len(_dup))
_cur6 = _db6.conn.cursor()
_cur6.execute(f'INSERT INTO "{T6}" ({_cols}) VALUES ({_ph})', list(_dup.values()))
_db6.conn.commit()
_v6 = _it6.verify_audit_chain(_db6, T6)
check("a duplicated record forks the chain and is detected on Postgres",
      not _v6['ok'] and 'fork' in (_v6['reason'] or '').lower(), str(_v6))

# the incident writer
T7 = 'audit_incident_log'
_db7 = PostgresRuntime(URL)
_c7 = _db7.conn.cursor(); _c7.execute(f'DROP TABLE IF EXISTS "{T7}"'); _db7.conn.commit()
_it7 = MohioInterpreter(); _it7._db = _db7
_it7._audit_chained_save(_db7, T7, {
    'audit_id': 'inc0000000000000', 'ts': '2026-07-19T00:00:00',
    'event': 'AUDIT_DEGRADED', 'agent': 'some_log', 'detail': '{"why":"test"}'})
check("the incident writer chains on Postgres",
      _it7.verify_audit_chain(_db7, T7)['ok'] and
      _it7.verify_audit_chain(_db7, T7)['checked'] == 1)

# the compliance writer
T8 = 'compliance_audit'
_db8 = PostgresRuntime(URL)
_c8 = _db8.conn.cursor(); _c8.execute(f'DROP TABLE IF EXISTS "{T8}"'); _db8.conn.commit()
_it8 = MohioInterpreter(); _it8._db = _db8
_it8._audit_chained_save(_db8, T8, {
    'audit_id': 'cmp0000000000000', 'ts': '2026-07-19T00:00:00',
    'event': 'compliance', 'agent': 'purge', 'detail': '{"action":"purge"}'})
check("the compliance writer chains on Postgres",
      _it8.verify_audit_chain(_db8, T8)['ok'])

# end-to-end: a real program, the decision-audit writer, against Postgres
from lark import Lark as _Lark
from mohio_transformer_ast import transform as _tf
from mohio_interpreter import MockAiRuntime as _Mock
_gp = '\n'.join(l for l in mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8').splitlines()
    if not l.strip().startswith('//'))
_Pp = _Lark(_gp, parser='earley', ambiguity='resolve', propagate_positions=True)
T9 = 'phi_audit_log'
_db9 = PostgresRuntime(URL)
_c9 = _db9.conn.cursor(); _c9.execute(f'DROP TABLE IF EXISTS "{T9}"'); _db9.conn.commit()
_PROG = ('amt 100\n'
         'ai.decide d returns boolean\n    confidence above 0.85\n    weigh amt\n'
         f'    ai.audit to {T9}\n    not confident\n        give back false\n'
         'ai.decide: done\ngive back 200 "ok"\n')
_it9 = MohioInterpreter(ai=_Mock()); _it9._db = _db9
_t9 = _tf(_Pp.parse(_PROG), _PROG)
_it9.run_declarations(_t9)


class _C9(Context):
    def get_connection(self, _x): return _db9


_it9._exec_declarations(_t9, _C9())
_it9.run(_t9)
_v9 = _it9.verify_audit_chain(_db9, T9)
check("a real program's decision audit chains and verifies on Postgres",
      _v9['ok'] and _v9['checked'] >= 1, str(_v9))

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
