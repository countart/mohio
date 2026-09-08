# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Three refusals that fired correctly and left nothing behind.

ONE CONCERN: a refusal must leave a trace. Each of these stopped the wrong thing from happening,
raised a good message, and wrote NOTHING, so the only record was a runtime error in somebody's
terminal. That is invisible to the one question an auditor actually asks, which is not "what
happened" but "what was asked for, and what happened to the request". Silence and never-asked
read identically.

THE FLAGSHIP IS THE LEGAL HOLD, and it is a GDPR Article 17 gap. A right-to-erasure request
refused because the table is under a `cm.lock` legal hold left no record that erasure had been
requested, none that it was refused, and none of why. A refused erasure is precisely the event a
regulator asks to see, and the hold is precisely the defence, so the moment it mattered there was
nothing to show.

THE ROLLBACK is the same shape the saga compensation gap had. A `cm.purge` that partially runs
and then fails rolls everything back, deliberately writes no tombstone, and raised with no record
at all -- so the trail showed an erasure request that simply stops, indistinguishable from a
crash that left rows half-deleted.

THE SCAN FAILURE is an asymmetry inside one guard. When the sector profile CAN read a payload and
a rule forbids the call, the refusal is audited. When the profile CANNOT read the payload it
refuses too, and audited nothing, so the case a reviewer most wants to see -- something stopped
because governance could not be applied at all -- was the one case that vanished.

Each now writes through the seam its neighbours already use: the two `cm.purge` refusals through
`_compliance_audit`, the same one `cm.retain`, `cm.expire` and `cm.lock` use, and the scan
failure through `_audit_event` into the log the data-class rules name, matching the verdict path
directly below it.

READ THE AUDIT, NOT THE OUTPUT. Every assertion below reads the stored trail, because the whole
defect is invisible in what the program prints.

Run: PYTHONPATH=$PWD DATABASE_URL=:memory: python tests/test_battery_refusal_leaves_a_trace.py
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

_p = _f = 0
_failures = []


def check(label, cond, detail=""):
    global _p, _f
    print("  [" + ("PASS" if cond else "FAIL") + "] " + label)
    if not cond:
        if detail:
            print("          " + str(detail)[:400])
        _failures.append(label)
    _p += bool(cond)
    _f += (not cond)


TMP = tempfile.mkdtemp(prefix="mohio_trace_")
_n = [0]


def run(src, db, salt="s3cr3t"):
    _n[0] += 1
    path = os.path.join(TMP, f"f{_n[0]}.mho")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(src)
    env = dict(os.environ, PYTHONPATH=ROOT, DATABASE_URL=db,
               MOHIO_ENCRYPTION_KEY="testkey", MOHIO_AUDIT_SALT=salt)
    r = subprocess.run([sys.executable, "mio.py", "run", path],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    return (r.stdout or "") + (r.stderr or "")


def trail(db, table="compliance_audit"):
    """The stored audit records, decoded, with their chain fields."""
    conn = sqlite3.connect(db)
    try:
        out = []
        for ph, eh, detail in conn.execute(
                f"select prev_hash, entry_hash, detail from {table}"):
            rec = json.loads(detail)
            rec['_prev_hash'] = ph
            rec['_entry_hash'] = eh
            out.append(rec)
        return out
    except Exception:
        return []
    finally:
        conn.close()


def new_db():
    _n[0] += 1
    return os.path.join(TMP, f"db{_n[0]}.db")


SEED = ('shape Member\n    email as text\n    name as text\nshape: done\n'
        'connect db as sqlite from env.DATABASE_URL\n'
        'save to db.members\n    email "ada@x.com"\n    name "Ada"\nsave: done\n')
PURGE = ('cm.purge from db.members\n    match id to 1\n'
         '    reason "erasure request"\ncm.purge: done\nshow "done"\n')


# ── 1. the legal hold, the flagship ──────────────────────────────────────────────────────────
print("\n== a right-to-erasure REFUSED by a legal hold is recorded ==")
db = new_db()
out = run(SEED + 'cm.lock members\n' + PURGE, db)
check("the refusal still fires", "under a cm.lock legal hold" in out, out[-300:])
rows = [r for r in trail(db) if r.get('action') == 'purge_refused']
check("a refusal record is written at all", len(rows) == 1, [r.get('action') for r in trail(db)])
rec = rows[0] if rows else {}
check("...naming the table", "members" in str(rec.get('target', '')), rec)
check("...saying an erasure was REQUESTED", rec.get('requested') == 'erasure', rec)
check("...saying it was REFUSED", rec.get('refused') == 'yes', rec)
check("...and why", rec.get('reason') == 'under_legal_hold', rec)
check("...naming the hold that caused it", rec.get('legal_hold') == 'members', rec)
check("...and carrying the operator's own stated reason for the erasure",
      rec.get('purge_reason') == 'erasure request', rec)

print("\n-- the record is hash-chained onto the lock that caused it --")
all_rows = trail(db)
check("both the lock and the refusal are in one trail",
      [r.get('action') for r in all_rows] == ['lock', 'purge_refused'],
      [r.get('action') for r in all_rows])
check("...and the refusal chains onto the lock",
      len(all_rows) == 2 and all_rows[1]['_prev_hash'] == all_rows[0]['_entry_hash'],
      [(r['_prev_hash'][:8], r['_entry_hash'][:8]) for r in all_rows])

print("\n-- an UNLOCKED table erases, and writes no refusal record --")
# The negative half. A record that appears whether or not anything was refused says nothing.
db = new_db()
out = run(SEED + PURGE, db)
check("the erasure runs", "done" in out, out[-300:])
conn = sqlite3.connect(db)
left = [r[0] for r in conn.execute("select email from members")]
conn.close()
check("...the row is gone", left == [], left)
check("...no refusal record was written",
      [r for r in trail(db) if r.get('action') == 'purge_refused'] == [], trail(db))
check("...and the lawful erasure left its own tombstone instead",
      any(r.get('event') == 'TOMBSTONE' for r in trail(db, 'data_audit_log')),
      trail(db, 'data_audit_log'))


# ── 2. the atomic rollback ───────────────────────────────────────────────────────────────────
print("\n== a partial erasure that fails and rolls back is recorded ==")
# Two clauses: the first names a real column and completes, the second names one that does not
# exist, so the batch fails after part of it ran and everything is undone.
db = new_db()
out = run(SEED + 'cm.purge from db.members\n    match id to 1\n'
                 '    match nosuchcolumn to "x"\n'
                 '    reason "erasure request"\ncm.purge: done\nshow "done"\n', db)
check("the rollback still fires", "rolled back" in out, out[-300:])
conn = sqlite3.connect(db)
left = [r[0] for r in conn.execute("select email from members")]
conn.close()
check("...and nothing was actually erased", left == ["ada@x.com"], left)
rows = [r for r in trail(db) if r.get('action') == 'purge_rolled_back']
check("a rollback record is written", len(rows) == 1, [r.get('action') for r in trail(db)])
rec = rows[0] if rows else {}
check("...naming the table", "members" in str(rec.get('target', '')), rec)
check("...saying an erasure was attempted and undone",
      rec.get('outcome') == 'failed_and_rolled_back', rec)
check("...stating plainly that nothing was erased", rec.get('erased') == 'nothing', rec)
check("...how far it got before failing", rec.get('reached') == 'id', rec)
check("...how many clauses were in the request", rec.get('clauses') == 2, rec)
check("...and the underlying cause", "nosuchcolumn" in str(rec.get('reason', '')), rec)

print("\n-- it names how far it got by FIELD, never by row --")
# Nothing was erased, so naming rows would assert an erasure that did not happen. That is the
# false-evidence class the tombstone rules already guard against, pointed the other way.
check("no row references appear in the rollback record",
      'row_refs' not in rec and 'rows' not in rec, rec)

print("\n-- the record SURVIVES the rollback it describes --")
# The first version of this fix wrote the record inside the suppressed-commit window the purge
# opens, so the rollback discarded the record along with the deletes and the audit table was not
# even created. A rollback record that rolls back with the rollback is worse than none.
check("the record is present in the database after the transaction unwound",
      len(rows) == 1, trail(db))
check("...and is hash-chained", bool(rec.get('_entry_hash')), rec.get('_entry_hash'))

print("\n-- a clean erasure writes no rollback record --")
db = new_db()
run(SEED + PURGE, db)
check("nothing claims a rollback that did not happen",
      [r for r in trail(db) if r.get('action') == 'purge_rolled_back'] == [], trail(db))


# ── 3. the sector scan failure ───────────────────────────────────────────────────────────────
print("\n== a sector refusal that could not read the payload is recorded ==")
sys.argv = ['mio.py']
os.environ.setdefault('DATABASE_URL', ':memory:')
SECTOR = ('sector profile "scantest"\n\n'
          'meta\n    tier free\nmeta: done\n\n'
          'field types\n    ssn as text [pci]\nfield: done\n\n'
          'operation rules for sector "scantest"\n'
          '    any operation touching [pci]\n'
          '        forbidden\n'
          '        reason "pci data may not leave this sector"\n'
          '        ai.audit to operation_audit_log\n'
          'operation: done\n')
PROG = ('sector: scantest\n'
        'shape Member\n    ssn as text\n    name as text\nshape: done\n'
        'connect db as sqlite from env.DATABASE_URL\n'
        'mioconnect Vendor\n    address "https://example.invalid"\n'
        '    operation send\n        path "/s"\n        method POST\n'
        '    operation: done\nmioconnect: done\n'
        'save to db.members\n    ssn "111"\n    name "Ada"\nsave: done\n'
        'retrieve m from db.members\n    match name to "Ada"\n'
        '    on.failure\n        show "none"\nretrieve: done\n'
        'Vendor.send with m as result\nshow "done"\n')

SECDIR = os.path.join(TMP, "sec")
os.makedirs(os.path.join(SECDIR, "sectors"), exist_ok=True)
with open(os.path.join(SECDIR, "sectors", "sector-scantest.sector"), "w", encoding="utf-8") as fh:
    fh.write(SECTOR)
with open(os.path.join(SECDIR, "scan.mho"), "w", encoding="utf-8") as fh:
    fh.write(PROG)


def sector_run(inject):
    """The real program, through the real pipeline, with the profile on the real search path.

    `inject` makes the profile unable to answer what a field is classified as, which is the
    condition this guard exists for. It is a fault at the seam, not a call into an internal
    function: the program still runs end to end and the refusal still comes out of the real
    governance check.
    """
    code = (
        "import sys, os, json\n"
        "sys.argv=['mio.py']\n"
        "sys.path.insert(0, r'" + ROOT + "')\n"
        "import mohio_data, mohio_sector_loader as SL\n"
        "from lark import Lark\n"
        "from mohio_transformer_ast import transform\n"
        "from mohio_interpreter import MohioInterpreter, Context\n"
        "if " + str(bool(inject)) + ":\n"
        "    SL.SectorProfile.get_field_classifications = lambda self, n: (_ for _ in ()).throw(\n"
        "        RuntimeError('classification table unavailable'))\n"
        "g='\\n'.join(l for l in mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8').splitlines()\n"
        "            if not l.strip().startswith('//'))\n"
        "P=Lark(g,parser='earley',ambiguity='resolve',propagate_positions=True)\n"
        "src=open('scan.mho',encoding='utf-8').read()\n"
        "prog=transform(P.parse(src), src)\n"
        "it=MohioInterpreter(verbose=False); ctx=Context()\n"
        "err=''\n"
        "try:\n"
        "    for st in prog.statements: it._exec(st, ctx)\n"
        "except Exception as e:\n"
        "    err=type(e).__name__+': '+str(e)[:200]\n"
        "print('@@'+json.dumps({'err': err, 'logs': {k:[{kk:str(vv) for kk,vv in e.items()}\n"
        "      for e in v] for k,v in (getattr(it,'_audit_logs',{}) or {}).items()}}))\n")
    env = dict(os.environ, PYTHONPATH=ROOT, DATABASE_URL=":memory:",
               MOHIO_ENCRYPTION_KEY="testkey")
    r = subprocess.run([sys.executable, "-c", code], cwd=SECDIR, env=env,
                       capture_output=True, text=True, timeout=300)
    for line in ((r.stdout or "") + (r.stderr or "")).splitlines():
        if line.startswith("@@"):
            return json.loads(line[2:])
    return {"err": (r.stdout or "") + (r.stderr or ""), "logs": {}}


res = sector_run(inject=True)
check("the scan failure still refuses", "sector_scan_failed" in res.get("err", ""),
      res.get("err"))
ops = [e for e in res.get("logs", {}).get("operation_audit_log", [])
       if e.get("verdict") == "scan_failed"]
check("a record is written for the refusal", len(ops) == 1, res.get("logs"))
rec = ops[0] if ops else {}
check("...marked as a refused operation, like the verdict path beside it",
      rec.get("event") == "OPERATION_REFUSED", rec)
check("...naming the connector and operation",
      rec.get("connector") == "Vendor" and rec.get("operation") == "send", rec)
check("...naming the sector whose rules could not be applied",
      rec.get("sector") == "scantest", rec)
check("...and saying it could not verify, with the underlying cause",
      "could not verify" in str(rec.get("reason", ""))
      and "classification table unavailable" in str(rec.get("reason", "")), rec)
check("...stamped and chained like every other governance event",
      bool(rec.get("audit_id")) and bool(rec.get("entry_hash")), sorted(rec))

print("\n-- it goes to the log the data-class rules name, not a generic one --")
# No rule has matched yet when the scan fails, so there is no verdict to take a log from. The
# rules that forced the scan are the honest destination.
check("the record lands in the rules' own audit log",
      "operation_audit_log" in res.get("logs", {}), sorted(res.get("logs", {})))

print("\n-- a scan that SUCCEEDS writes no scan-failure record --")
res = sector_run(inject=False)
ops = [e for e in res.get("logs", {}).get("operation_audit_log", [])
       if e.get("verdict") == "scan_failed"]
check("nothing claims a scan failure that did not happen", ops == [], res.get("logs"))
check("...and the run got past governance to the call itself",
      "sector_scan_failed" not in res.get("err", ""), res.get("err"))

shutil.rmtree(TMP, ignore_errors=True)
if _failures:
    print("\nFAILURES, repeated so the suite runner's tail carries them:")
    for line in _failures:
        print("  * " + line)
print("\nRESULTS: " + str(_p) + " passed, " + str(_f) + " failed")
sys.exit(1 if _f else 0)
