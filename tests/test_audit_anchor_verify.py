# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Anchor verification: detect truncation, wholesale deletion, and repoint against
published heads -- the manipulations the chain cannot catch on its own.

Run: PYTHONPATH=$PWD MOHIO_ENCRYPTION_KEY=testkey python3 tests/test_audit_anchor_verify.py
"""
import os, sys, tempfile, sqlite3
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mohio_interpreter import MohioInterpreter, DbRuntime

def _rm(p):
    # Best-effort temp cleanup. On Windows a file cannot be unlinked while a sqlite connection to
    # it is still open (WinError 32), and these dbs are held by a live DbRuntime. The assertions
    # above are the test; a failed teardown unlink must never fail the run. The OS temp dir
    # reclaims the file regardless.
    try:
        os.remove(p)
    except OSError:
        pass

_passed = _failed = 0
def check(label, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1; print(f"  ok   {label}")
    else:
        _failed += 1; print(f"  FAIL {label}  {detail}")

class Ctx:
    def __init__(self, db): self.db = db
    def get_connection(self, _n): return self.db

def fresh(path):
    it = MohioInterpreter(); db = DbRuntime(path); it._db = db
    return it, db, Ctx(db)

def write(it, ctx, log, tag, n):
    for i in range(n):
        it._audit_event(log, {'event': f'{tag}{i}', 'agent': 'a'}, ctx)

# --- Build a 5-record chain, capturing anchors at length 3 and length 5 --------
path = tempfile.mktemp(suffix='.db')
it, db, ctx = fresh(path)
write(it, ctx, 'alog', 'e', 3)
h3 = it.audit_chain_head(db, 'alog')
write(it, ctx, 'alog', 'e', 2)          # events e3, e4 -> length 5
h5 = it.audit_chain_head(db, 'alog')
check("anchor A3 captured at length 3", h3['entries'] == 3 and h3['head'], str(h3))
check("anchor A5 captured at length 5", h5['entries'] == 5 and h5['head'], str(h5))
anchors = [{'head': h3['head'], 'length': 3}, {'head': h5['head'], 'length': 5}]

# 1. Intact chain: both anchors satisfied
r = it.verify_audit_chain_against_anchors(db, 'alog', anchors)
check("intact -> ok", r['ok'] is True, str(r))
check("intact -> both anchors satisfied", r['anchors_satisfied'] == 2, str(r))
check("intact -> no failures", r['failures'] == [], str(r))

# 2. Tail truncation: delete e3, e4 (chain internally intact, now length 3)
con = sqlite3.connect(path)
walk = list(con.execute("select prev_hash, entry_hash from alog"))
by_prev = {p: e for p, e in walk}
order = []; cur = it.AUDIT_GENESIS
while cur in by_prev:
    order.append(by_prev[cur]); cur = by_prev[cur]
tail_heads = order[3:]           # e3, e4 entry_hashes
con.executemany("delete from alog where entry_hash=?", [(h,) for h in tail_heads]); con.commit()
r = it.verify_audit_chain_against_anchors(db, 'alog', anchors)
check("truncation -> not ok", r['ok'] is False, str(r))
check("truncation -> A3 still satisfied", r['anchors_satisfied'] == 1, str(r))
check("truncation -> A5 reported TRUNCATION",
      any(f['kind'] == 'TRUNCATION' and f['length'] == 5 for f in r['failures']), str(r['failures']))
con.close()

# 3. Wholesale deletion: drop every row (truncation to zero)
con = sqlite3.connect(path); con.execute("delete from alog"); con.commit(); con.close()
r = it.verify_audit_chain_against_anchors(db, 'alog', anchors)
check("wholesale delete -> not ok", r['ok'] is False, str(r))
check("wholesale delete -> both anchors TRUNCATION",
      len([f for f in r['failures'] if f['kind'] == 'TRUNCATION']) == 2, str(r['failures']))
_rm(path)

# 4. Repoint / replacement: a fresh, internally-valid 5-record chain with different events
path2 = tempfile.mktemp(suffix='.db')
it2, db2, ctx2 = fresh(path2)
write(it2, ctx2, 'alog', 'x', 5)        # different events -> different heads
r = it2.verify_audit_chain_against_anchors(db2, 'alog', anchors)
check("repoint -> not ok", r['ok'] is False, str(r))
check("repoint -> reported REPOINT (not satisfied)",
      r['anchors_satisfied'] == 0 and any(f['kind'] == 'REPOINT' for f in r['failures']),
      str(r['failures']))
check("repoint -> internal chain still reads intact",
      r['internal']['ok'] is True, str(r['internal']))
_rm(path2)

# 5. Internal break wins: alter a mid record so the chain itself breaks
path3 = tempfile.mktemp(suffix='.db')
it3, db3, ctx3 = fresh(path3)
write(it3, ctx3, 'alog', 'e', 4)
a4 = it3.audit_chain_head(db3, 'alog')
anch3 = [{'head': a4['head'], 'length': 4}]
con = sqlite3.connect(path3)
mid = list(con.execute("select audit_id from alog limit 1 offset 1"))[0][0]
con.execute("update alog set detail = 'tampered' where audit_id=?", (mid,)); con.commit(); con.close()
r = it3.verify_audit_chain_against_anchors(db3, 'alog', anch3)
check("internal break -> not ok", r['ok'] is False, str(r))
check("internal break -> reason cites integrity",
      r['reason'] and 'integrity' in r['reason'], str(r['reason']))
_rm(path3)

# 6. No anchors -> reflects internal integrity only
path4 = tempfile.mktemp(suffix='.db')
it4, db4, ctx4 = fresh(path4)
write(it4, ctx4, 'alog', 'e', 3)
r = it4.verify_audit_chain_against_anchors(db4, 'alog', [])
check("no anchors on intact chain -> ok", r['ok'] is True and r['anchors_checked'] == 0, str(r))
_rm(path4)

print(f"\nRESULTS: {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
