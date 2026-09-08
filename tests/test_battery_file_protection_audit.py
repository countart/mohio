# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Every file stored or read leaves a record saying whether it was protected.

THE LINE AN AUDITOR HAS TO SEE is between a file that was encrypted and one that was not.
Everything else in the trail already recorded that data moved. Nothing recorded which files were
protected when they moved, so proving a health record was sealed, and showing plainly which files
were not, meant reading storage rather than reading the trail. Those are different jobs: a trail
answers what happened at the time, and storage only shows what survives now.

ALWAYS ON, NEVER GATED ON A SECTOR. A compliance profile decides what a regulated deployment must
additionally do; it does not decide whether a file operation happened, and a developer with no
profile active has the same right to see unprotected content moving. Gating it would make the
record appear exactly when someone thought to switch it on, which is not what a trail is for.

THE WARNING IS NARROWER THAN THE RECORD, deliberately. Both plaintext cases are recorded; only
one warns. A plaintext file on the machine the app runs on is reachable by whoever already has
that machine. A plaintext file in a bucket is reachable by anyone holding the bucket credential,
and that credential travels: into build systems, into deployment settings, to whoever needed to
run something once. Warning about both would make the one that matters ordinary.

It is a warning and not a refusal. Storing unencrypted content in a bucket is a legitimate thing
to do, and the coder chose it by not tagging the field. What they may not have chosen is knowing
where it lands.

THE KEY IS RECORDED, NEVER THE CONTENT, and never the resolved filesystem path, which would
publish the store's layout in the very record meant to be shown to an outsider.

Run: PYTHONPATH=$PWD DATABASE_URL=:memory: python tests/test_battery_file_protection_audit.py
"""
import contextlib
import io as _io
import json
import os
import shutil
import sqlite3
import sys
import tempfile

sys.argv = ['mio.py']
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault('MOHIO_ENCRYPTION_KEY', 'testkey')

import mohio_data  # noqa: E402
from lark import Lark  # noqa: E402
from mohio_transformer_ast import transform  # noqa: E402
from mohio_interpreter import MohioInterpreter  # noqa: E402
from mohio_server import MohioServer, create_app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

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


TMP = tempfile.mkdtemp(prefix="mohio_fa_")
STORE = os.path.join(TMP, "store")
os.makedirs(STORE, exist_ok=True)
os.environ['MOHIO_UPLOAD_DIR'] = STORE
_n = [0]

_g = '\n'.join(l for l in mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8').splitlines()
               if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)


def program(tag, read_key=None, db=False):
    return (('connect db as sqlite from env.DATABASE_URL\n' if db else '') +
            'shape Doc\n    f as file' + tag + '\n        accept txt\n        max size 5 mb\n'
            '    name as text\nshape: done\n'
            'shape Login\n    who as text\nshape: done\n'
            'listen for\n'
            '    new sh.Doc at /d\n        give back [200] "stored"\n    new: done\n'
            '    new sh.Login at /login\n        grant role "clinician"\n'
            '        give back [200] "in"\n    new: done\n'
            + ('    request for sh.Doc at /read\n        require role "clinician"\n'
               '        miofile.read "' + read_key + '" as c\n'
               '        give back [200] c\n    request: done\n' if read_key else '') +
            'listen: done\n')


def serve(src, db=None):
    """Build a served app. The database path stays set AFTER this returns.

    Restoring it here would have put it back before any request ran, so the connect inside the
    handler opened a different database than the one the assertions then read -- which is how
    the chain check first reported that the audit table did not exist.
    """
    os.environ['DATABASE_URL'] = db or ':memory:'
    it = MohioInterpreter()
    buf = _io.StringIO()
    with contextlib.redirect_stdout(buf):
        client = TestClient(create_app(MohioServer(transform(_P.parse(src), src), it)),
                            raise_server_exceptions=False)
    return it, client, buf


def file_rows(it):
    return [e for e in (it._audit_logs.get('data_audit_log') or [])
            if str(e.get('event', '')).startswith('FILE_')]


def store_one(tag, blob=b"SENSITIVE MEDICAL CONTENT", db=None):
    """One real upload. Returns (interp, client, printed output, new key, audit rows)."""
    before = set(os.listdir(STORE))
    it, client, buf = serve(program(tag, db=bool(db)), db=db)
    with contextlib.redirect_stdout(buf):
        client.post("/d", data={"_csrf": it._issue_csrf(), "name": "Ada"},
                    files={"f": ("doc.txt", blob, "text/plain")})
    keys = [n for n in os.listdir(STORE) if n not in before]
    return it, client, buf.getvalue(), (keys[0] if keys else None), file_rows(it)


# ── the store record ─────────────────────────────────────────────────────────────────────────
print("\n== storing a file records whether it was protected ==")
it, _c, _out, key, rows = store_one(" [phi]")
check("exactly one store record", len(rows) == 1, rows)
r = rows[0] if rows else {}
check("...marked as a store", r.get('event') == 'FILE_STORE' and r.get('op') == 'store', r)
check("...saying the content was encrypted", r.get('protection') == 'encrypted', r)
check("...naming the key it was stored under", r.get('key') == key, (r.get('key'), key))
check("...and carrying a timestamp", bool(r.get('ts')), r)

print("\n-- an untagged file is recorded too, as plaintext --")
it2, _c2, _o2, key2, rows2 = store_one("", blob=b"ordinary notes")
r2 = rows2[0] if rows2 else {}
check("the untagged store is recorded", len(rows2) == 1, rows2)
check("...as plaintext, which is the line an auditor is looking for",
      r2.get('protection') == 'plaintext', r2)

print("\n-- the record names the key, never the content and never the path --")
check("the content is nowhere in the record",
      "SENSITIVE" not in json.dumps(r) and "ordinary notes" not in json.dumps(r2), (r, r2))
check("...and neither is the store's location on disk",
      STORE.replace('\\', '/') not in json.dumps(r).replace('\\', '/'), r)


# ── the access record ────────────────────────────────────────────────────────────────────────
print("\n== reading a file back records the access, and its protection ==")
it3, client3, buf3 = serve(program(" [phi]", read_key=key))
client3.post("/login", json={"who": "x"})
with contextlib.redirect_stdout(buf3):
    resp = client3.get("/read")
rows3 = file_rows(it3)
check("the authorized read succeeds", resp.status_code == 200, resp.status_code)
check("an access record is written", len(rows3) == 1, rows3)
r3 = rows3[0] if rows3 else {}
check("...marked as an access", r3.get('event') == 'FILE_ACCESS' and r3.get('op') == 'access', r3)
check("...saying what was STORED was encrypted", r3.get('protection') == 'encrypted', r3)
check("...naming the key", r3.get('key') == key, (r3.get('key'), key))

print("\n-- an unauthorized read writes NO access record, because no read happened --")
it4, client4, buf4 = serve(program(" [phi]", read_key=key))
with contextlib.redirect_stdout(buf4):
    resp4 = client4.get("/read")
check("it is refused", resp4.status_code == 403, resp4.status_code)
check("...and nothing claims a file was read", file_rows(it4) == [], file_rows(it4))

print("\n-- reading an untagged file records it as plaintext --")
it5, client5, buf5 = serve(program("", read_key=key2))
client5.post("/login", json={"who": "x"})
with contextlib.redirect_stdout(buf5):
    client5.get("/read")
rows5 = file_rows(it5)
check("the access is recorded as plaintext",
      rows5 and rows5[0].get('protection') == 'plaintext', rows5)


# ── the chain ────────────────────────────────────────────────────────────────────────────────
print("\n== the records are hash-chained in the durable trail ==")
_db = os.path.join(TMP, "trail.db")
before = set(os.listdir(STORE))
it6, client6, buf6 = serve(program(" [phi]", db=True), db=_db)
with contextlib.redirect_stdout(buf6):
    for blob in (b"SECRET ONE", b"SECRET TWO"):
        client6.post("/d", data={"_csrf": it6._issue_csrf(), "name": "Ada"},
                     files={"f": ("doc.txt", blob, "text/plain")})
conn = sqlite3.connect(_db)
try:
    stored = [(ph, eh, json.loads(d)) for ph, eh, d
              in conn.execute("select prev_hash, entry_hash, detail from data_audit_log")]
finally:
    conn.close()
chain = [(ph, eh) for ph, eh, d in stored if str(d.get('event', '')).startswith('FILE_')]
check("both stores are in the durable trail", len(chain) == 2, len(chain))
check("...and the second chains onto the first",
      len(chain) == 2 and chain[1][0] == chain[0][1], chain)


# ── the warning ──────────────────────────────────────────────────────────────────────────────
print("\n== plaintext to a CLOUD area warns; the same file to LOCAL does not ==")
# The store itself is not reached here: the warning is decided before the object is handed over,
# so this needs no bucket. Whether the bucket round-trip works is proved in its own battery.
CLOUD_ZONE = 'miofile\n    cloud vault\n        bucket "b"\n        region "auto"\n' \
             '        endpoint "https://example.invalid"\nmiofile: done\n'


def cloud_attempt(tag):
    """Store into a declared cloud area and return what was printed plus the audit rows."""
    src = CLOUD_ZONE + program(tag)
    env = {'MOHIO_LICENSE': 'x', 'AWS_ACCESS_KEY_ID': 'k', 'AWS_SECRET_ACCESS_KEY': 's'}
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        it, client, buf = serve(src)
        with contextlib.redirect_stdout(buf):
            try:
                client.post("/d", data={"_csrf": it._issue_csrf(), "name": "Ada"},
                            files={"f": ("n.txt", b"plain notes", "text/plain")})
            except Exception:
                pass
        return buf.getvalue(), file_rows(it)
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


out, rows = cloud_attempt("")
check("an unencrypted file bound for a bucket warns",
      "WARNING: unencrypted content" in out, out[-300:])
check("...saying who could then read it",
      "bucket credential" in out or "bucket key" in out, out[-300:])

out, rows = cloud_attempt(" [phi]")
check("a tagged file bound for the same bucket does NOT warn",
      "WARNING: unencrypted content" not in out, out[-300:])

print("\n-- and the record says the destination was a bucket, against a real one --")
# The warning above needs no bucket, because it is decided before the object is handed over.
# The RECORD is written only after the write succeeds, since a record of a store that did not
# happen is the same false statement a tombstone for an erasure that did not happen would be.
# So this half needs somewhere real to write to.
_LIVE = all(os.environ.get(n) for n in
            ('MOHIO_STORE_ENDPOINT', 'MOHIO_STORE_BUCKET', 'MOHIO_STORE_REGION',
             'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY'))
if not _LIVE:
    print("  NOT RUN: no bucket is configured, so the cloud RECORD has no live coverage here.")
    print("           The warning above was still proved; set the store variables to cover it.")
else:
    from mohio_metasource_store import S3ObjectStore  # noqa: E402

    def live_cloud(tag):
        src = ('miofile\n    cloud vault\nmiofile: done\n') + program(tag)
        old = {'MOHIO_LICENSE': os.environ.get('MOHIO_LICENSE')}
        os.environ['MOHIO_LICENSE'] = 'x'
        try:
            it, client, buf = serve(src)
            with contextlib.redirect_stdout(buf):
                client.post("/d", data={"_csrf": it._issue_csrf(), "name": "Ada"},
                            files={"f": ("n.txt", b"plain notes", "text/plain")})
            return buf.getvalue(), file_rows(it)
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    _out, _rows = live_cloud("")
    check("a plaintext store into a real bucket is recorded as plaintext, bound for the cloud",
          _rows and _rows[0].get('protection') == 'plaintext'
          and _rows[0].get('destination') == 'cloud', _rows)
    _out, _rows = live_cloud(" [phi]")
    check("...and a tagged one as encrypted, bound for the cloud",
          _rows and _rows[0].get('protection') == 'encrypted'
          and _rows[0].get('destination') == 'cloud', _rows)
    _store = S3ObjectStore()
    for _k in (_store.list("") or []):
        _store.delete(_k)

_it, _c, out, _k, rows = store_one("", blob=b"plain notes")
check("the same unencrypted file stored LOCALLY does not warn",
      "WARNING: unencrypted content" not in out, out[-300:])
check("...but is still recorded, as plaintext on this machine",
      rows and rows[0].get('protection') == 'plaintext'
      and rows[0].get('destination') == 'local', rows)


# ── the other file verbs ─────────────────────────────────────────────────────────────────────
print("\n== write, delete, move and copy record protection too ==")
# These recorded a boundary crossing and said nothing about whether the file was protected, so
# the trail could show that something was deleted and never whether a sealed file was.
AREA = os.path.join(TMP, "area")
os.makedirs(AREA, exist_ok=True)
os.environ['MIOFILE_ROOT'] = AREA

from mohio_interpreter import Context  # noqa: E402


def verbs(body, seed=None):
    """Run file verbs through the real pipeline. Returns (protection records, printed output)."""
    for name, blob in (seed or {}).items():
        with open(os.path.join(AREA, name), 'wb') as fh:
            fh.write(blob)
    it = MohioInterpreter(verbose=False)
    ctx = Context()
    buf = _io.StringIO()
    with contextlib.redirect_stdout(buf):
        for st in transform(_P.parse(body), body).statements:
            it._exec(st, ctx)
    return file_rows(it), buf.getvalue()


rows, _o = verbs('miofile.write "notes.txt" "ordinary notes"\nshow "d"\n')
check("a write is recorded", len(rows) == 1 and rows[0].get('op') == 'write', rows)
check("...as plaintext, since that is what was written",
      rows and rows[0].get('protection') == 'plaintext', rows)
check("...naming the key", rows and rows[0].get('key') == 'notes.txt', rows)

print("\n-- a file that IS sealed is recognised as such by every verb --")
SEALED = MohioInterpreter(verbose=False)._seal_upload_body(b"SENSITIVE MEDICAL CONTENT")
rows, _o = verbs('miofile.copy "sealed.bin" to "c.bin"\n'
                 'miofile.move "c.bin" to "m.bin"\n'
                 'miofile.delete "m.bin"\nshow "d"\n',
                 seed={'sealed.bin': SEALED})
by_op = {r.get('op'): r for r in rows}
check("a copy is recorded as encrypted",
      by_op.get('copy', {}).get('protection') == 'encrypted', rows)
check("...naming where it came from", by_op.get('copy', {}).get('source') == 'sealed.bin', rows)
check("a move is recorded as encrypted",
      by_op.get('move', {}).get('protection') == 'encrypted', rows)
check("...naming its source too", by_op.get('move', {}).get('source') == 'c.bin', rows)
check("a delete is recorded", by_op.get('delete', {}).get('op') == 'delete', rows)
check("...saying a SEALED file was the one removed",
      by_op.get('delete', {}).get('protection') == 'encrypted', rows)

print("\n-- the delete reads the file BEFORE unlinking it, or it could not know --")
rows, _o = verbs('miofile.delete "plain.txt"\nshow "d"\n', seed={'plain.txt': b"ordinary"})
check("deleting an unsealed file records plaintext",
      rows and rows[0].get('protection') == 'plaintext', rows)

print("\n-- the record names the area that actually governs the path --")
rows, _o = verbs('miofile\n    local "archive" as arch\nmiofile: done\n'
                 'miofile.write "archive/n.txt" "notes"\nshow "d"\n')
check("the governing area is named", rows and rows[0].get('zone') == 'arch', rows)
check("...and the destination is where the bytes really went",
      rows and rows[0].get('destination') == 'local', rows)

print("\n-- a declared CLOUD area governs no path, so these verbs stay local --")
# `cloud NAME` takes settings and no location, so such an area matches no path here and these
# verbs always write to the local file area. Asserted so a warning is never added where it could
# not fire, and so this stops being a surprise when someone wires the store to these verbs.
rows, out = verbs('miofile\n    cloud vault\n        bucket "b"\nmiofile: done\n'
                  'miofile.write "n.txt" "notes"\nshow "d"\n')
check("the write is still recorded as local",
      rows and rows[0].get('destination') == 'local', rows)
check("...and nothing warns about a bucket it never reached",
      "WARNING: unencrypted content" not in out, out[-200:])

print("\n-- WHAT THIS RECORD MAKES VISIBLE: a classified value written in the clear --")
# `miofile.write` does NOT seal a classified value the way an upload is sealed, so a [phi] field
# written to a file lands readable on disk. That is a real exposure, and closing it is not this
# commit's job; the point here is that the trail now SAYS so rather than being silent about it.
_dbp = os.path.join(TMP, "phi.db")
os.environ['DATABASE_URL'] = _dbp
rows, _o = verbs('shape Patient\n    ssn as text [phi]\n    name as text\nshape: done\n'
                 'connect db as sqlite from env.DATABASE_URL\n'
                 'save to db.patients\n    ssn "111-11-1111"\n    name "Ada"\nsave: done\n'
                 'retrieve p from db.patients\n    match name to "Ada"\n'
                 '    on.failure\n        show "none"\nretrieve: done\n'
                 'miofile.write "chart.txt" p.ssn\nshow "d"\n')
check("the write of a classified value is recorded", len(rows) == 1, rows)
check("...as PLAINTEXT, which is the truth and the finding",
      rows and rows[0].get('protection') == 'plaintext', rows)
with open(os.path.join(AREA, "chart.txt"), 'rb') as fh:
    _on_disk = fh.read()
check("...and the file on disk confirms the record is not flattering it",
      _on_disk == b"111-11-1111", _on_disk[:40])


# ── always on ────────────────────────────────────────────────────────────────────────────────
print("\n== the record is written with no sector active ==")
# Every case above ran with no compliance profile. Stated as its own assertion because the value
# of this record is that it is there before anyone thought to ask for it.
_it7, _c7, _o7, _k7, rows7 = store_one(" [phi]")
check("no profile was needed for the trail to exist", len(rows7) == 1, rows7)
import inspect  # noqa: E402
_src = inspect.getsource(MohioInterpreter._audit_file_op)
_body = _src.split(chr(34) * 3)[-1]     # the code, not the docstring that explains the rule
check("...and nothing in the recorder consults a sector or a profile",
      'sector' not in _body.lower() and 'profile' not in _body.lower(), _body[:200])

shutil.rmtree(TMP, ignore_errors=True)
if _failures:
    print("\nFAILURES, repeated so the suite runner's tail carries them:")
    for line in _failures:
        print("  * " + line)
print("\nRESULTS: " + str(_p) + " passed, " + str(_f) + " failed")
sys.exit(1 if _f else 0)
