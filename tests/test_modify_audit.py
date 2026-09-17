# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""A `modify` must audit like its fail-loud siblings, and must never pass unrecorded.

`modify` was once the only data-change verb that wrapped its audit in `try: ... except
Exception: pass`, so a change that could not be audited passed in silence. That is what this
file was written to hold closed, and it still is.

WHAT CHANGED, and why the assertions moved (Phase 0 regulated-write atomicity ruling,
2026-09-12). The old version spied on an internal method and asked whether it had been CALLED.
Two things were wrong with that. It passed for a modify of ordinary untagged data, where the
call happened and then correctly recorded nothing, so the test could not tell a real record from
a call that did nothing. And it asserted that an audit-write failure RAISES, which the ruling
rejects by name: by the time the audit can fail the data has already committed, so raising
reports a failure for a change that happened and leaves the rows behind anyway.

So the claim is now held where it can be seen: real Mohio, a real file, real tagged data, and
the trail read back afterwards.

  1. a regulated modify leaves a real record in the trail, naming the table and how many rows.
  2. the rows and the durable evidence for them commit together.
  3. with the trail unreachable, the modify does NOT pass unrecorded: the evidence is there, the
     record is owed, it says so, and the relay delivers it. The silence this file exists to
     prevent is still prevented; what it cannot do any more is lose the change to prevent it.

Run: PYTHONPATH=$PWD python tests/test_modify_audit.py
"""
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("MOHIO_ENCRYPTION_KEY", "testkey")

_p = _f = 0
_failures = []


def check(label, cond, detail=""):
    global _p, _f
    print("  [" + ("PASS" if cond else "FAIL") + "] " + label)
    if not cond and detail:
        print("          " + str(detail)[:600])
    if not cond:
        _failures.append(label)
    _p += bool(cond)
    _f += (not cond)


TMP = tempfile.mkdtemp(prefix="mohio_modify_audit_")
DB = os.path.join(TMP, "app.db")

# A TAGGED FIELD, so the modify is a REGULATED change. Ordinary untagged data is deliberately
# not audited at all, and asserting against it would be asserting nothing.
PROGRAM = (
    'shape Item\n'
    '    chart as text [phi]\n'
    '    status as text\n'
    'shape: done\n'
    'connect db as sqlite from env.DATABASE_URL\n'
    'save to db.items\n    chart "111-11-1111"\n    status "old"\nsave: done\n'
    'save to db.items\n    chart "222-22-2222"\n    status "old"\nsave: done\n'
    'modify every item in db.items\n'
    '    apply item\n'
    '        status "new"\n'
    '    apply: done\n'
    'modify: done\n'
    'show "modified"\n')

PATH = os.path.join(TMP, "modify.mho")
io.open(PATH, "w", encoding="utf-8", newline="\n").write(PROGRAM)


def env(extra=None):
    e = dict(os.environ, PYTHONPATH=ROOT, DATABASE_URL=DB,
             MOHIO_ENCRYPTION_KEY="testkey", PYTHONIOENCODING="utf-8")
    e.pop("MOHIO_AUDIT_DATABASE_URL", None)
    e.update(extra or {})
    return e


def run(args, extra=None):
    r = subprocess.run([sys.executable, "-m", "mio"] + args, cwd=ROOT, env=env(extra),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=300)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def rows(sql):
    try:
        conn = sqlite3.connect(DB)
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql)]
    except Exception as e:                          # noqa: BLE001
        return "ERR " + str(e).splitlines()[0]


print("=== 1. a regulated modify leaves a real record in the trail ===")
rc, out = run(["run", PATH])
check("the program ran", rc == 0, out[-600:])
records = rows("select detail from data_audit_log")
mods = [json.loads(r["detail"]) for r in records
        if isinstance(records, list) and '"modify"' in r["detail"]]
check("the trail holds a modify record", len(mods) == 1, records)
check("it names the table", bool(mods) and mods[0].get("table") == "items", mods)
check("it names how many rows changed", bool(mods) and mods[0].get("count") == 2, mods)
check("it names the field, never the value",
      bool(mods) and mods[0].get("fields") == ["status"], mods)
check("the rows really changed",
      [r["status"] for r in rows("select status from items")] == ["new", "new"],
      rows("select status from items"))

print("\n=== 2. the rows and the evidence for them commit together ===")
envelopes = rows("select operation, record_id, entry_json from mohio_audit_envelope "
                 "where operation = 'modify'")
check("durable evidence exists for the modify", len(envelopes) == 1, envelopes)
check("the evidence carries no written value",
      bool(envelopes) and "111-11-1111" not in envelopes[0]["entry_json"], envelopes)
owed = rows("select e.envelope_id from mohio_audit_envelope e "
            "left join mohio_audit_envelope_ack a on a.envelope_id = e.envelope_id "
            "where a.envelope_id is null")
check("nothing is owed after an ordinary run", owed == [], owed)

print("\n=== 3. with the trail unreachable, the change is NOT unrecorded ===")
# The audit's own database is pointed at a path that cannot be opened, so the record cannot
# reach the trail. This is the case the old `try/except pass` made silent.
unreachable = os.path.join(TMP, "no_such_dir", "audit.db")
before_records = len(rows("select 1 from data_audit_log"))
rc, out = run(["run", PATH], {"MOHIO_AUDIT_DATABASE_URL": unreachable})
after_records = len(rows("select 1 from data_audit_log"))
owed = rows("select e.envelope_id from mohio_audit_envelope e "
            "left join mohio_audit_envelope_ack a on a.envelope_id = e.envelope_id "
            "where a.envelope_id is null")
check("the record did not reach the trail", after_records == before_records,
      "before=%d after=%d" % (before_records, after_records))
check("durable evidence for the change exists anyway", len(owed) >= 1, owed)
check("it says the record is OWED rather than passing in silence",
      "OWED" in out or "owed" in out, out[-700:])
rc, out = run(["audit", "relay", DB])
check("the relay delivers what was owed",
      len(rows("select 1 from data_audit_log")) > after_records,
      "rc=%s out=%s" % (rc, out[-400:]))
check("nothing is owed afterwards",
      rows("select e.envelope_id from mohio_audit_envelope e "
           "left join mohio_audit_envelope_ack a on a.envelope_id = e.envelope_id "
           "where a.envelope_id is null") == [], owed)

print()
print("RESULTS: %d passed, %d failed" % (_p, _f))
for x in _failures:
    print("  FAILED: " + x)
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _f else 0)
