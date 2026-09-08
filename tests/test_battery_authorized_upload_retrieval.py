# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""A stored upload can be read back, and only from a handler that already checks who is asking.

THE FEATURE WAS WRITE-ONLY. Uploads moved out of the working directory to close a live exposure:
the store used to sit inside the served folder, so a `[phi]` file answered a plain unauthenticated
GET with its full contents. Closing that removed the only route to the bytes and nothing replaced
it, so a program could store a file and never read it again. Storage without retrieval is not a
feature with a gap; it is a feature that does not work.

ACCESS CONTROL IS INHERITED, NOT INVENTED. There is no file permission system here, and there
should not be: the read happens inside a handler, and Mohio already decides who may reach a
handler. `require role` is server-verified and cannot be forged from the request; `authorize:`
gates a journey path on an authenticated session. Both raise before the body runs, so an
unauthorized request never reaches the read at all, and both already write an audited
`access_denied`. A second, file-shaped permission model would be a second thing to keep in step
with the first, and the first is the one that is already forgery-proof.

Measured end to end, one program, three requests:

    UNAUTHENTICATED   403
    WRONG ROLE        403
    AUTHORIZED        200  SENSITIVE MEDICAL CONTENT

with the file on disk reading `enc:v1:uP/U3JPjowija7JDx...` throughout.

TWO PLACES, IN ORDER, AND THE FIRST IS UNCHANGED. `miofile.read` still resolves the file area
exactly as it always did, so every existing program reads what it read before; the object store
is tried second, because that is where an upload lives. A name in neither place fails loud and
says so. 116 existing assertions depend on the first half and are untouched.

UNSEALED BY THE MARKER, like a stored field value: a tagged upload is written as ciphertext, so a
read that returned the stored bytes would return `enc:v1:...` rather than the file. An untagged
upload carries no marker and comes back as stored, so nothing has to remember which it was.

NOT THIS BUILD: a governed records transfer (purpose, audit, and encryption on both ends) is its
own design and is not claimed here.

Run: PYTHONPATH=$PWD DATABASE_URL=:memory: python tests/test_battery_authorized_upload_retrieval.py
"""
import os
import shutil
import sys
import tempfile

sys.argv = ['mio.py']
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault('DATABASE_URL', ':memory:')
os.environ.setdefault('MOHIO_ENCRYPTION_KEY', 'testkey')

import mohio_data  # noqa: E402
from lark import Lark  # noqa: E402
from mohio_transformer_ast import transform  # noqa: E402
from mohio_interpreter import MohioInterpreter, MohioValue, Context  # noqa: E402
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


TMP = tempfile.mkdtemp(prefix="mohio_ret_")
STORE = os.path.join(TMP, "store")
os.makedirs(STORE, exist_ok=True)
os.environ['MOHIO_UPLOAD_DIR'] = STORE
_n = [0]

_g = '\n'.join(l for l in mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8').splitlines()
               if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

SECRET = b"SENSITIVE MEDICAL CONTENT"


def app(tag=" [phi]", accept="txt", ftype="file"):
    """One program that stores an upload and reads it back from a role-gated handler."""
    _n[0] += 1
    db = os.path.join(TMP, f"db{_n[0]}.db")
    src = ('shape Chart\n    scan as ' + ftype + tag + '\n        accept ' + accept + '\n'
           '        max size 5 mb\n    name as text\nshape: done\n'
           'shape Login\n    who as text\nshape: done\n'
           'connect db as sqlite from env.DATABASE_URL\n'
           'listen for\n'
           '    new sh.Chart at /c\n'
           '        save to db.charts\n            scan scan\n            name name\n'
           '        save: done\n        give back [200] "stored"\n    new: done\n'
           '    new sh.Login at /login\n        grant role "clinician"\n'
           '        give back [200] "in"\n    new: done\n'
           '    new sh.Login at /badlogin\n        grant role "visitor"\n'
           '        give back [200] "in"\n    new: done\n'
           '    request for sh.Chart at /read\n'
           '        require role "clinician"\n'
           '        retrieve c from db.charts\n            match name to "Ada"\n'
           '            on.failure\n                give back [404] "none"\n'
           '        retrieve: done\n'
           '        miofile.read c.scan as content\n'
           '        give back [200] content\n'
           '    request: done\n'
           'listen: done\n')
    prev = os.environ.get('DATABASE_URL')
    os.environ['DATABASE_URL'] = db
    try:
        it = MohioInterpreter()
        client = TestClient(create_app(MohioServer(transform(_P.parse(src), src), it)),
                            raise_server_exceptions=False)
        return it, client
    finally:
        if prev is not None:
            os.environ['DATABASE_URL'] = prev


def store(it, client, blob, filename="chart.txt", ctype="text/plain"):
    before = set(os.listdir(STORE))
    r = client.post("/c", data={"_csrf": it._issue_csrf(), "name": "Ada"},
                    files={"scan": (filename, blob, ctype)})
    return r, [n for n in os.listdir(STORE) if n not in before]


def denials(it):
    return [e for e in (it._audit_logs.get('security_audit_log') or [])
            if e.get('event') == 'access_denied']


# ── the three cases ──────────────────────────────────────────────────────────────────────────
print("\n== an authorized request gets the file; nobody else does ==")
it, client = app()
r, keys = store(it, client, SECRET)
check("the upload is stored", r.status_code == 200 and len(keys) == 1, r.text[:120])
raw = open(os.path.join(STORE, keys[0]), 'rb').read() if keys else b""
check("...as ciphertext on disk", raw.startswith(b"enc:v1:"), raw[:30])

print("\n-- UNAUTHENTICATED --")
before = len(denials(it))
resp = client.get("/read")
check("refused", resp.status_code == 403, (resp.status_code, resp.text[:120]))
check("...and the file content is nowhere in the response",
      SECRET.decode() not in resp.text, resp.text[:120])
check("...and the refusal is audited", len(denials(it)) == before + 1, denials(it))

print("\n-- AUTHENTICATED BUT THE WRONG ROLE --")
client.post("/badlogin", json={"who": "x"})
before = len(denials(it))
resp = client.get("/read")
check("refused", resp.status_code == 403, (resp.status_code, resp.text[:120]))
check("...and the file content still does not leak",
      SECRET.decode() not in resp.text, resp.text[:120])
check("...and this refusal is audited too", len(denials(it)) == before + 1, denials(it))
check("...naming why, not just that",
      any(d.get('reason') for d in denials(it)), denials(it))

print("\n-- AUTHORIZED --")
client.post("/login", json={"who": "x"})
resp = client.get("/read")
check("the request succeeds", resp.status_code == 200, (resp.status_code, resp.text[:120]))
check("...and returns the real file, decrypted", SECRET.decode() in resp.text, resp.text[:160])
check("...never the stored ciphertext", "enc:v1:" not in resp.text, resp.text[:160])

print("\n-- the read never ran for the refused requests --")
# The governance raises before the handler body, so this is not a matter of the read returning
# nothing: the read is not reached. An audited denial with no egress record is what that looks
# like from the trail.
reads = [e for e in (it._audit_logs.get('egress_audit_log') or [])
         if str(e.get('channel', '')) == 'miofile']
check("there are fewer file reads than there were requests for the file",
      len(reads) <= 1, len(reads))


# ── an untagged upload ───────────────────────────────────────────────────────────────────────
print("\n== an untagged upload reads back as stored, still only when authorized ==")
PLAIN = b"ordinary notes, nothing sensitive"
it2, client2 = app(tag="")
r2, keys2 = store(it2, client2, PLAIN)
raw2 = open(os.path.join(STORE, keys2[0]), 'rb').read() if keys2 else b""
check("it is stored in the clear, as it always was", raw2 == PLAIN, raw2[:40])
check("an unauthenticated read is still refused", client2.get("/read").status_code == 403)
client2.post("/login", json={"who": "x"})
resp2 = client2.get("/read")
check("an authorized read returns it", PLAIN.decode() in resp2.text, resp2.text[:120])


# ── the closed door stays closed ─────────────────────────────────────────────────────────────
print("\n== the old unauthenticated URL is still gone ==")
# This is the exposure the storage change closed, asserted here so retrieval cannot quietly
# reopen it: the way back in is the governed handler, not a path.
g = client.get("/uploads/" + keys[0]) if keys else None
check("a plain GET for the stored object does not serve it",
      g is not None and g.status_code != 200, g.status_code if g else None)
check("...and returns none of its contents",
      g is not None and SECRET.decode() not in g.text, g.text[:80] if g else None)


# ── binary integrity ─────────────────────────────────────────────────────────────────────────
print("\n== a binary file reads back byte-identical ==")
# Asserted at the value rather than over the wire: a binary HTTP response body is a separate
# feature. What is proved here is that the read itself does not corrupt the bytes, which is what
# the old text-only read did.
PNG = b'\x89PNG\r\n\x1a\n' + bytes(range(256)) * 3 + b'\x00\xff\xfe\xfd'
for tag, label in ((" [phi]", "tagged"), ("", "untagged")):
    itb, clientb = app(tag=tag, accept="images", ftype="image")
    rb, kb = store(itb, clientb, PNG, filename="pic.png", ctype="image/png")
    check(f"a {label} PNG stores", rb.status_code == 200 and len(kb) == 1, rb.text[:120])
    src = ('miofile.read "' + kb[0] + '" as blob\nshow "read"\n')
    prog = transform(_P.parse(src), src)
    ctx = Context()
    for st in prog.statements:
        itb._exec(st, ctx)
    got = ctx.get('blob')
    got = got.to_python() if isinstance(got, MohioValue) else got
    check(f"...and reads back byte-identical ({label})", got == PNG,
          (type(got).__name__, len(got or b''), len(PNG)))

print("\n-- a text file still reads back as text, which is what the 116 assertions rely on --")
itt, clientt = app(tag="")
rt, kt = store(itt, clientt, b"just some words")
src = ('miofile.read "' + kt[0] + '" as words\nshow "read"\n')
prog = transform(_P.parse(src), src)
ctx = Context()
for st in prog.statements:
    itt._exec(st, ctx)
got = ctx.get('words')
check("text comes back as text, not as bytes",
      isinstance(got, MohioValue) and got.to_python() == "just some words", got)


# ── a name in neither place ──────────────────────────────────────────────────────────────────
print("\n== a name that is stored nowhere fails loud, naming both places ==")
itx, _cx = app()
src = 'miofile.read "no-such-key.txt" as x\nshow "read"\n'
prog = transform(_P.parse(src), src)
ctx = Context()
try:
    for st in prog.statements:
        itx._exec(st, ctx)
    msg = ""
except Exception as e:
    msg = str(e)
check("it refuses rather than returning empty", "no file" in msg.lower(), msg[:200])
check("...and says both the file area and the upload store were looked in",
      "file area" in msg and "upload store" in msg, msg[:220])

shutil.rmtree(TMP, ignore_errors=True)
if _failures:
    print("\nFAILURES, repeated so the suite runner's tail carries them:")
    for line in _failures:
        print("  * " + line)
print("\nRESULTS: " + str(_p) + " passed, " + str(_f) + " failed")
sys.exit(1 if _f else 0)
