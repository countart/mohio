# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""The audit trail is hash-chained, and the chain is verifiable.

Before this, every audit record carried `audit_id` -- a digest of its OWN contents. That proves a
record's individual integrity and nothing whatsoever about the sequence: delete a row and no
arithmetic anywhere disagrees. For a compliance language claiming an immutable audit trail, that
gap is the difference between a true claim and a false one.

Now each record carries:

    entry_hash = H(prev_hash || canonical(content))
    prev_hash  = the entry_hash of the record before it (genesis = 64 zeros)

so any alteration, deletion, or reordering invalidates every hash that follows, and
`verify_audit_chain` finds it.

NOT claimed, deliberately: tail truncation. Removing the most recent records leaves a shorter but
internally consistent chain. Detecting that requires external anchoring, which is separate work.
This test asserts that limitation explicitly so nobody later mistakes it for a bug or overstates
what the chain proves.
"""
import os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')

from mohio_interpreter import MohioInterpreter, DbRuntime, Context

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)


def build(n=5, path=':memory:'):
    it = MohioInterpreter(); db = DbRuntime(path); it._db = db
    class C(Context):
        def get_connection(self, _n): return db
    ctx = C()
    for i in range(n):
        it._audit_event('log', {'event': f'e{i}', 'agent': 'a'}, ctx)
    return it, db


# ── the chain is written ──────────────────────────────────────────────────────────────
it, db = build(4)
rows = db.conn.execute(
    'SELECT audit_id, prev_hash, entry_hash FROM log ORDER BY rowid').fetchall()
check("every audit record carries an entry_hash",
      all(r['entry_hash'] for r in rows), f"{len(rows)} rows")
check("the first record's prev_hash is genesis",
      rows[0]['prev_hash'] == it.AUDIT_GENESIS)
check("each record links to the one before it",
      all(rows[i]['prev_hash'] == rows[i-1]['entry_hash'] for i in range(1, len(rows))))
check("entry_hash is a full sha256 (not truncated)", len(rows[0]['entry_hash']) == 64)

# ── verification passes on an intact chain ────────────────────────────────────────────
v = it.verify_audit_chain(db, 'log')
check("verification passes on an intact chain", v['ok'] and v['checked'] == 4, str(v))

# ── the three things a per-entry digest cannot catch ──────────────────────────────────
it, db = build(5)
db.conn.execute("UPDATE log SET event='TAMPERED' WHERE rowid=3"); db.conn.commit()
v = it.verify_audit_chain(db, 'log')
check("ALTERING a record's contents is detected", not v['ok'], str(v))
check("the report names the offending record", v['broken_at'] is not None)

it, db = build(5)
db.conn.execute("DELETE FROM log WHERE rowid=3"); db.conn.commit()
check("DELETING a record is detected", not it.verify_audit_chain(db, 'log')['ok'])

it, db = build(5)
db.conn.execute("UPDATE log SET detail='{\"x\":1}' WHERE rowid=4"); db.conn.commit()
check("EDITING a record's detail payload is detected",
      not it.verify_audit_chain(db, 'log')['ok'])

# ── the documented limitation, asserted so it is not mistaken for a bug ───────────────
it, db = build(5)
last = db.conn.execute('SELECT MAX(rowid) AS m FROM log').fetchone()['m']
db.conn.execute(f"DELETE FROM log WHERE rowid={last}"); db.conn.commit()
check("TAIL truncation is NOT detected (needs external anchoring -- documented, not claimed)",
      it.verify_audit_chain(db, 'log')['ok'])

# ── the chain survives a restart (seeded from what is already durable) ────────────────
path = tempfile.mktemp(suffix='.db')
build(3, path)                      # "process 1"
it2, db2 = build(2, path)           # "process 2": fresh interpreter, same durable log
rows = db2.conn.execute('SELECT prev_hash, entry_hash FROM log ORDER BY rowid').fetchall()
check("a restart continues the existing chain (does not restart at genesis)",
      len(rows) == 5 and all(rows[i]['prev_hash'] == rows[i-1]['entry_hash']
                             for i in range(1, len(rows))))
check("verification passes across the restart boundary",
      it2.verify_audit_chain(db2, 'log')['ok'])

# ── every audit writer chains, not just the one that was built first ──────────────────
src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'mohio_interpreter.py'), encoding='utf-8').read()
for log in ("'compliance_audit'", "'audit_incident_log'"):
    check(f"{log} is written through the chained save",
          f"_audit_chained_save(db, {log}" in src)
# The previous version of this check grepped for two literal table names, so a writer calling
# `db.save(log_name, ...)` with a VARIABLE sailed through it -- which is exactly how the ai.decide
# audit writer stayed outside the chain while a test claimed otherwise. Test the property, not the
# spelling: no `.save(` on any audit destination may remain outside the chaining helper.
# A source grep can only ever guess at this: it keys on how a destination is SPELLED, so a
# variable named `dest` or `tbl` holding an audit table slips past whatever pattern is written.
# The storage layer can refuse it outright, which is a guarantee rather than a heuristic.
from mohio_audit_grades import assert_write_allowed as _awa, chained_write as _cw

_refused = False
try:
    _awa('phi_audit_log')
except PermissionError:
    _refused = True
check("a direct write to an audit relation is refused by the storage layer", _refused)

_allowed = True
try:
    with _cw():
        _awa('phi_audit_log')
except PermissionError:
    _allowed = False
check("the chaining path is permitted to write an audit relation", _allowed)

_ordinary = True
try:
    _awa('members')
except PermissionError:
    _ordinary = False
check("ordinary data tables are unaffected by the restriction", _ordinary)

# and the enforcement is wired into every SQL runtime, not just one
for _cls in ('DbRuntime', 'PostgresRuntime', 'MySQLRuntime'):
    _i = src.index(f'class {_cls}:')
    _seg = src[_i:_i + 12000]
    check(f"{_cls}.save enforces the audit-relation restriction",
          'assert_write_allowed' in _seg)

check("the chaining helper is the only writer of audit rows",
      src.count('_audit_chained_save(') >= 4,
      f"chained-save call sites: {src.count('_audit_chained_save(')}")


# ── the anchoring export: chain head + log discovery ──────────────────────────────────
# Publishing the head somewhere the log's owner does not control is what turns the chain from
# "detects an outsider editing records" into "detects the owner truncating or restarting the
# log" -- neither of which breaks the chain internally.
it, db = build(4)
check("audit logs are discoverable without knowing the app's schema",
      it.audit_logs(db) == ['log'], str(it.audit_logs(db)))
h1 = it.audit_chain_head(db, 'log')
check("chain head is exposed", len(h1['head']) == 64 and h1['entries'] == 4, str(h1))
db.conn.execute("DELETE FROM log WHERE rowid=(SELECT MAX(rowid) FROM log)"); db.conn.commit()
h2 = it.audit_chain_head(db, 'log')
check("tail truncation still passes internal verification", h2['intact'])
check("but the HEAD changes -- which is what an anchor detects",
      h1['head'] != h2['head'])

# ── the grade ladder names WORM for what the storage does, not a promise we cannot keep ──
from mohio_audit_grades import GRADES, satisfies
check("the strongest grade is named `worm` (not `tamper_proof`)", GRADES[-1] == 'worm',
      str(GRADES))
check("no grade identifier claims prevention the compiler cannot deliver",
      'tamper_proof' not in GRADES)
check("worm still satisfies append_only", satisfies('worm', 'append_only'))


# ── portability: the walk follows the CHAIN, not the storage order ────────────────────
# Postgres has no rowid and does not guarantee row order, and the control plane reads these
# chains from the tenant's Postgres database to anchor them. A verifier that trusted the
# database's ordering would also be trusting the thing it is supposed to be checking.
it, db = build(5)
h_before = it.audit_chain_head(db, 'log')['head']
_rows = [dict(r) for r in db.conn.execute("SELECT * FROM log").fetchall()]
import random as _rnd; _rnd.shuffle(_rows)
db.conn.execute("DELETE FROM log")
for _r in _rows:
    _c = ','.join(f'"{k}"' for k in _r); _p2 = ','.join('?' for _ in _r)
    db.conn.execute(f"INSERT INTO log ({_c}) VALUES ({_p2})", list(_r.values()))
db.conn.commit()
_v = it.verify_audit_chain(db, 'log')
check("verification survives scrambled storage order (Postgres-safe)",
      _v['ok'] and _v['checked'] == 5, str(_v))
check("the chain head is the same regardless of storage order",
      it.audit_chain_head(db, 'log')['head'] == h_before)

# ── a duplicated / re-inserted record is now REFUSED by the database ─────────────────
# A lock serializes writers inside one process but cannot reach a second process: both read the
# same predecessor, both hash against it, both write, and the chain forks with nothing tampered.
# Measured on real Postgres before the fix: three processes writing concurrently produced a
# forked chain every time. A unique index on prev_hash makes the database the arbiter, so the
# second writer is refused rather than admitted -- prevention, not just detection.
it, db = build(5)
_r_dup = dict(db.conn.execute("SELECT * FROM log WHERE rowid=3").fetchone())
_r_dup.pop('id', None)
_c_dup = ','.join(f'"{k}"' for k in _r_dup)
_p_dup = ','.join('?' for _ in _r_dup)
_refused_dup = False
try:
    db.conn.execute(f"INSERT INTO log ({_c_dup}) VALUES ({_p_dup})", list(_r_dup.values()))
    db.conn.commit()
except Exception as _e:
    _refused_dup = 'unique' in str(_e).lower()
check("the database refuses a second record claiming the same predecessor", _refused_dup)
check("the chain is still intact after the refused duplicate",
      it.verify_audit_chain(db, 'log')['ok'])

# detection must still work for a log that predates the index (or where it could not be built)
it, db = build(5)
# drop whatever uniqueness index exists, by lookup rather than by guessing its name
for _ix in [r[0] for r in db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='log'")]:
    db.conn.execute(f'DROP INDEX IF EXISTS "{_ix}"')
db.conn.commit()
_r_dup = dict(db.conn.execute("SELECT * FROM log WHERE rowid=3").fetchone())
_r_dup.pop('id', None)
_c_dup = ','.join(f'"{k}"' for k in _r_dup)
_p_dup = ','.join('?' for _ in _r_dup)
db.conn.execute(f"INSERT INTO log ({_c_dup}) VALUES ({_p_dup})", list(_r_dup.values()))
db.conn.commit()
_v_dup = it.verify_audit_chain(db, 'log')
check("a fork in a log without the index is still DETECTED", not _v_dup['ok'], str(_v_dup))



# ── canonical encoding is pinned and versioned ────────────────────────────────────────
# Identical logical entries must hash identically forever. If the encoding drifts without a
# version marker, verification fails and tampering becomes indistinguishable from a formatting
# change -- which destroys the evidentiary value of the whole log.
check("the chain declares its canonical encoding version",
      getattr(MohioInterpreter, 'AUDIT_ENCODING_VERSION', None) == 'mohio-audit-1')
_h_same_a = MohioInterpreter._audit_chain_hash('p', {'a': '1'})
_h_same_b = MohioInterpreter._audit_chain_hash('p', {'a': '1'})
check("identical input hashes identically", _h_same_a == _h_same_b)
_orig_ver = MohioInterpreter.AUDIT_ENCODING_VERSION
try:
    MohioInterpreter.AUDIT_ENCODING_VERSION = 'mohio-audit-99'
    _h_other = MohioInterpreter._audit_chain_hash('p', {'a': '1'})
finally:
    MohioInterpreter.AUDIT_ENCODING_VERSION = _orig_ver
check("an encoding change produces a DIFFERENT hash (detectable, not silently compatible)",
      _h_same_a != _h_other)

# ── the truncated audit_id is never a chain link ──────────────────────────────────────
# audit_id is 64 bits, which is fine for an identifier and not fine for a tamper-evidence
# claim: ~2^32 birthday resistance is reachable. It may be hashed AS CONTENT, never used AS
# a link.
it, db = build(3)
_r = db.conn.execute(
    "SELECT audit_id, prev_hash, entry_hash FROM log ORDER BY rowid").fetchall()
check("audit_id is the short display id (64-bit)", len(_r[0]['audit_id']) == 16)
check("prev_hash and entry_hash are full 256-bit",
      len(_r[0]['prev_hash']) == 64 and len(_r[0]['entry_hash']) == 64)
check("the truncated audit_id is never used as a chain link",
      _r[1]['prev_hash'] == _r[0]['entry_hash'] and _r[1]['prev_hash'] != _r[0]['audit_id'])


# ── KNOWN GAP, asserted so it is not discovered later ─────────────────────────────────
# Chaining makes the trail append-only in evidence, which collides with erasure rights. If a
# record must be removed anyway (a stricter state rule, or a specific order), the deletion is
# indistinguishable from tampering: verification reports a break either way, which falsely
# accuses a lawful act. Recording a compelled erasure IN the chain as a tombstone is the answer.
# It is not built. This asserts the current behaviour so the gap stays visible.
it_a, db_a = build(5)
db_a.conn.execute("DELETE FROM log WHERE rowid=3"); db_a.conn.commit()
_malicious = it_a.verify_audit_chain(db_a, 'log')
it_b, db_b = build(5)
db_b.conn.execute("DELETE FROM log WHERE rowid=3"); db_b.conn.commit()   # same op, lawful
_lawful = it_b.verify_audit_chain(db_b, 'log')
check("a removed record breaks the chain whatever the reason", not _malicious['ok'])
check("KNOWN GAP: a lawful erasure is NOT yet distinguishable from tampering "
      "(needs an in-chain tombstone)",
      _malicious['reason'] == _lawful['reason'])

# ── the retention rationale is recorded in the code, and the wrong one is not ─────────
_src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         'mohio_interpreter.py'), encoding='utf-8').read()
check("the code states the erasure-exception basis for retaining audit records",
      'establish, exercise, or defend legal claims' in _src)
check("the code explicitly marks the 'no personal data to erase' claim as WRONG",
      'no personal data to erase' in _src and 'WRONG:' in _src)


# ── the chain covers EVERY content column, not a convenient subset ────────────────────
# The canonical audit schema carries 17 columns. Hashing only a handful leaves the rest
# unprotected: the ai.decide record holds `result`, `confidence`, `inputs`, and `sector`, and if
# those sit outside the hash then the decision itself can be rewritten without breaking the
# chain -- which is the exact thing the chain exists to prevent.
from mohio_audit_grades import canonical_audit_columns as _cac
_payload = MohioInterpreter._chain_payload({c: 'x' for c in _cac()})
_link_cols = set(MohioInterpreter._CHAIN_LINK_COLUMNS)
_content = [c for c in _cac() if c not in _link_cols]
check("the hashed payload covers every canonical content column",
      set(_payload.keys()) == set(_content),
      f"missing: {set(_content) - set(_payload.keys())}")
check("the chain link columns are excluded from what they protect",
      not (_link_cols & set(_payload.keys())))
check("writer and verifier share one payload definition",
      src.count('self._chain_payload(') >= 2)


# ── the ai.decide record is chained, and its decision columns are covered ─────────────
# This was the false claim: three writers chained, and the FOURTH -- the primary ai.decide
# record, carrying the decision, its confidence, and the sector it was made under -- wrote
# directly with db.save. It had no entry_hash, so verification skipped it entirely. A chain that
# covers three of four writers is not a chain, it is a chain-shaped claim.
from lark import Lark as _Lark
from mohio_transformer_ast import transform as _tf
from mohio_interpreter import MockAiRuntime as _Mock
_g2 = '\n'.join(l for l in mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8').splitlines()
    if not l.strip().startswith('//'))
_P2 = _Lark(_g2, parser='earley', ambiguity='resolve', propagate_positions=True)
_AI = ('connect db as sqlite from env.DATABASE_URL\namt 100\n'
       'ai.decide d returns boolean\n    confidence above 0.85\n    weigh amt\n'
       '    ai.audit to phi_audit_log\n    not confident\n        give back false\n'
       'ai.decide: done\ngive back 200 "ok"\n')


def _run_ai():
    it2 = MohioInterpreter(ai=_Mock()); it2._db = DbRuntime(':memory:')
    t2 = _tf(_P2.parse(_AI), _AI); it2.run_declarations(t2); it2.run(t2)
    return it2, it2._db


_it, _db = _run_ai()
_rows = _db.conn.execute("SELECT entry_hash FROM phi_audit_log").fetchall()
check("the ai.decide audit record is written with a chain link",
      len(_rows) == 1 and _rows[0]['entry_hash'], f"rows={len(_rows)}")
check("verification sees the ai.decide record (it is not skipped)",
      _it.verify_audit_chain(_db, 'phi_audit_log')['checked'] == 1)

for _col, _sql in (('result',     "UPDATE phi_audit_log SET result='False'"),
                   ('confidence', "UPDATE phi_audit_log SET confidence='0.01'"),
                   ('sector',     "UPDATE phi_audit_log SET sector='forged'"),
                   ('inputs',     "UPDATE phi_audit_log SET inputs='{}'")):
    _i, _d = _run_ai(); _d.conn.execute(_sql); _d.conn.commit()
    check(f"tampering with `{_col}` on the decision record is detected",
          not _i.verify_audit_chain(_d, 'phi_audit_log')['ok'])

# ── one seed implementation, and it is the portable one ───────────────────────────────
# Two definitions existed; Python kept the second, which ordered by rowid. Postgres has no
# rowid, so on Postgres the seed raised, returned None, and a restart silently began a second
# chain from genesis -- while the portable link-derived seed sat above it, shadowed and dead.
check("there is exactly one _audit_chain_seed", src.count('def _audit_chain_seed') == 1,
      f"count={src.count('def _audit_chain_seed')}")
check("the chain path contains no rowid ordering (Postgres has none)",
      'ORDER BY rowid' not in src)


# ── concurrent writers must not fork the chain ────────────────────────────────────────
# Reading the head, hashing against it, writing, and advancing is one indivisible operation.
# Without that, two threads read the same head, both hash against it, and both write -- two
# records claiming the same predecessor. Measured before the fix: four threads writing twenty
# records each produced eighty rows and a forked chain every time, with nothing tampered.
import threading as _th
_it_c = MohioInterpreter(); _db_c = DbRuntime(':memory:'); _it_c._db = _db_c
class _Cc(Context):
    def get_connection(self, _x): return _db_c
_ctx_c = _Cc()


def _writer(n):
    for i in range(20):
        _it_c._audit_event('conc_audit_log', {'event': f't{n}-{i}', 'agent': 'a'}, _ctx_c)


_threads = [_th.Thread(target=_writer, args=(n,)) for n in range(4)]
for _t in _threads:
    _t.start()
for _t in _threads:
    _t.join()
_n_rows = _db_c.conn.execute("SELECT COUNT(*) c FROM conc_audit_log").fetchone()['c']
_v_c = _it_c.verify_audit_chain(_db_c, 'conc_audit_log')
check("every concurrent write landed", _n_rows == 80, f"rows={_n_rows}")
check("concurrent writes do not fork the chain",
      _v_c['ok'] and _v_c['checked'] == _n_rows, str(_v_c))
check("the chain advance is serialized by a lock",
      hasattr(MohioInterpreter, '_AUDIT_CHAIN_LOCK'))


# ── the TEXT round-trip invariant, ENFORCED rather than assumed ──────────────────────
# Verification recomputes each record's hash from what the database returns, so the chain is
# only sound while every audit column round-trips as an identical string. That held because
# ensure_table creates audit columns TEXT -- but nothing checked it, and a backend returning a
# native type would make a correctly written record report as TAMPERED. The system accusing
# itself over a schema change, indistinguishable from a real accusation.
from mohio_audit_grades import canonical_audit_columns as _cac2

_it_rt = MohioInterpreter(); _db_rt = DbRuntime(':memory:')
_cols_rt = {c: 'INTEGER' for c in _cac2()}          # ask for the WRONG type on purpose
_db_rt.ensure_table('rt_audit_log', _cols_rt)
_types = {r[1]: r[2] for r in _db_rt.conn.execute('PRAGMA table_info(rt_audit_log)')}
check("audit columns are created TEXT even when another type is requested",
      all(_types.get(c) == 'TEXT' for c in _cac2() if c in _types),
      str({k: v for k, v in _types.items() if v != 'TEXT'}))

# and the backstop catches a backend that hands back a native type regardless
_orig_rows = MohioInterpreter.__dict__['_audit_rows']   # keep the staticmethod wrapper


def _drifting(sink, log):
    _rows = _orig_rows(sink, log)
    for _r in _rows:
        if _r.get('agent') is not None:
            _r['agent'] = 7                          # driver returned an int, not '007'
    return _rows


MohioInterpreter._audit_rows = staticmethod(_drifting)
_caught = False
try:
    MohioInterpreter()._audit_chained_save(
        _db_rt, 'rt_audit_log',
        {'audit_id': 'a' * 16, 'ts': 't', 'event': 'e', 'agent': '007', 'detail': '{}'})
except Exception as _e:
    _caught = 'round-trip' in str(_e) or 'TAMPERED' in str(_e) or 'verifiable chain' in str(_e)
finally:
    MohioInterpreter._audit_rows = _orig_rows            # restored as a staticmethod, not bound
check("a backend whose columns do not round-trip is refused, not silently trusted", _caught)


# ── cross-process ordering: the database enforces one successor per record ────────────
# The in-process lock cannot reach another process. A unique index on prev_hash makes a fork
# physically impossible whichever process the writer is in: the engine refuses the second claim,
# and the loser re-reads the head and links after it. Without that, multi-worker deployment
# silently forks the chain -- so this is the precondition for running more than one worker.
_it_u = MohioInterpreter(); _db_u = DbRuntime(':memory:'); _it_u._db = _db_u
class _Cu(Context):
    def get_connection(self, _x): return _db_u
_it_u._audit_event('ux_audit_log', {'event': 'e', 'agent': 'a'}, _Cu())
_idx = [r[0] for r in _db_u.conn.execute(
    "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='ux_audit_log'")]
check("a unique index on prev_hash is created for an audit log",
      any('prev_hash' in i for i in _idx), str(_idx))

_dup_refused = False
try:
    _db_u.conn.execute(
        'INSERT INTO ux_audit_log (audit_id, prev_hash, entry_hash) VALUES (?,?,?)',
        ('x' * 16, MohioInterpreter.AUDIT_GENESIS, 'forged'))
    _db_u.conn.commit()
except Exception:
    _dup_refused = True
check("the engine refuses a second record claiming the same predecessor", _dup_refused)

# under real contention: every write lands AND the chain stays intact
import threading as _th2
_it_h = MohioInterpreter(); _db_h = DbRuntime(':memory:'); _it_h._db = _db_h
class _Ch(Context):
    def get_connection(self, _x): return _db_h
_ctx_h = _Ch()
_N_T, _N_W = 8, 40


def _hw(n):
    for i in range(_N_W):
        _it_h._audit_event('hc_audit_log', {'event': f't{n}-{i}', 'agent': 'a'}, _ctx_h)


_th_list = [_th2.Thread(target=_hw, args=(n,)) for n in range(_N_T)]
for _t in _th_list:
    _t.start()
for _t in _th_list:
    _t.join()
_got = _db_h.conn.execute("SELECT COUNT(*) c FROM hc_audit_log").fetchone()['c']
_vh = _it_h.verify_audit_chain(_db_h, 'hc_audit_log')
check(f"under contention every write lands ({_N_T}x{_N_W})",
      _got == _N_T * _N_W, f"want {_N_T * _N_W}, got {_got}")
check("under contention the chain stays intact",
      _vh['ok'] and _vh['checked'] == _got, str(_vh))


# ── the chain must not depend on a sqlite3-only connection convenience ────────────────
# `conn.execute(...)` is a sqlite3 affordance. NEITHER psycopg2 NOR pymysql has it -- verified
# directly: pymysql.connections.Connection exposes .cursor but not .execute. Relying on it is
# what made the chain silently non-functional on Postgres: reads raised AttributeError, the
# readers caught it, and verification reported "unreadable" while the seed returned None so a
# restart began a second chain from genesis.
#
# This exercises the reader against a connection shaped like those drivers -- cursor-only, dict
# rows -- so the dependency cannot come back without a live server to catch it.
class _CursorOnlyConn:
    """A connection with .cursor() and no .execute(), like psycopg2 and pymysql."""

    def __init__(self, real): self._real = real

    def cursor(self, *a, **k): return self._real.cursor(*a, **k)

    def commit(self): return self._real.commit()

    def rollback(self): return self._real.rollback()


class _CursorOnlySink:
    def __init__(self):
        self._inner = DbRuntime(':memory:')
        self.conn = _CursorOnlyConn(self._inner.conn)

    def ensure_table(self, *a, **k): return self._inner.ensure_table(*a, **k)

    def save(self, *a, **k): return self._inner.save(*a, **k)


_sink_co = _CursorOnlySink()
_it_co = MohioInterpreter(); _it_co._db = _sink_co
class _Cco(Context):
    def get_connection(self, _x): return _sink_co
for _i in range(4):
    _it_co._audit_event('co_audit_log', {'event': f'e{_i}', 'agent': 'a'}, _Cco())

check("audit logs are discoverable on a cursor-only connection",
      'co_audit_log' in _it_co.audit_logs(_sink_co), str(_it_co.audit_logs(_sink_co)))
_v_co = _it_co.verify_audit_chain(_sink_co, 'co_audit_log')
check("the chain verifies on a cursor-only connection",
      _v_co['ok'] and _v_co['checked'] == 4, str(_v_co))
check("the head is computable on a cursor-only connection",
      len(str(_it_co.audit_chain_head(_sink_co, 'co_audit_log')['head'])) == 64)
_sink_co._inner.conn.execute("UPDATE co_audit_log SET event='X' WHERE rowid=2")
_sink_co._inner.conn.commit()
check("tampering is detected on a cursor-only connection",
      not _it_co.verify_audit_chain(_sink_co, 'co_audit_log')['ok'])

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
