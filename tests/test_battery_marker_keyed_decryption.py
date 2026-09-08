# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""A value that says it is encrypted is decrypted wherever it is found.

THE CIPHERTEXT SELF-DESCRIBES. `enc:v1:` is a marker on the VALUE, and whether a value can be
read back is a property of that value, never of the column it happens to be sitting in. Two of
the three layers already agreed: `_encrypt_field` skips a value that already carries the marker,
and `_decrypt_field` returns anything without it untouched. The row-level walk between them did
not. It asked the COLUMN whether it was declared encrypted, and only then looked at the value,
so a real ciphertext in a column nobody tagged was walked straight past.

MEASURED THROUGH `mio run` BEFORE THE FIX, with a real ciphertext lifted out of a [phi] column
and planted in an untagged one:

    show ("body is: " & n.body)   ->   body is: enc:v1:vWx3JOW5/HIJSVcaFoxMgSBvGdrJeJuR...

That is the exact outcome the fail-loud a few lines below it exists to prevent, in its own
words: "Refusing to hand back the ciphertext as if it were the value." The refusal was real and
the column gate meant it never ran. Handing back ciphertext is not a cosmetic wrong answer: the
program can compare it, show it, or SAVE IT AGAIN, and saving it again double-encrypts the field
beyond recovery.

TWO GATES, both column-bound, both widened to ask the value as well:

  * the whole-program early return, which skipped every row when the READING program declared no
    encrypted field of its own. A program that declares no tag can still be handed a classified
    value that travelled into one of its columns.
  * the per-column loop, which decided field by field.

NO INFERENCE. Nothing here inspects a plaintext value to guess what it might be. The only
question asked is whether the value already carries the marker this runtime wrote, which is a
fact about the bytes, not a guess about their meaning. A genuinely plaintext value is untouched,
and that is asserted below.

Run: PYTHONPATH=$PWD DATABASE_URL=:memory: python tests/test_battery_marker_keyed_decryption.py
"""
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


TMP = tempfile.mkdtemp(prefix="mohio_mkd_")
_n = [0]
SECRET = "111-11-1111"


def run(src, db, key="testkey"):
    _n[0] += 1
    path = os.path.join(TMP, f"f{_n[0]}.mho")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(src)
    env = dict(os.environ, PYTHONPATH=ROOT, DATABASE_URL=db)
    if key is None:
        env.pop("MOHIO_ENCRYPTION_KEY", None)
    else:
        env["MOHIO_ENCRYPTION_KEY"] = key
    r = subprocess.run([sys.executable, "mio.py", "run", path],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    return (r.stdout or "") + (r.stderr or "")


SEED = ('shape Patient\n    ssn as text [phi]\n    name as text\nshape: done\n'
        'connect db as sqlite from env.DATABASE_URL\n'
        'save to db.patients\n    ssn "' + SECRET + '"\n    name "Ada"\nsave: done\n'
        'show "seeded"\n')


def fresh_db(plant_untagged=True, plant_value=None):
    """A real encrypted value, and optionally that SAME ciphertext planted in an untagged column.

    The ciphertext is produced by a real `mio run`, never hand-built, so what the untagged column
    holds is exactly what this runtime writes.
    """
    _n[0] += 1
    db = os.path.join(TMP, f"db{_n[0]}.db")
    run(SEED, db)
    conn = sqlite3.connect(db)
    ct = conn.execute("select ssn from patients").fetchone()[0]
    conn.execute("create table if not exists notes "
                 "(id integer primary key autoincrement, body text, who text)")
    if plant_untagged:
        conn.execute("insert into notes (body, who) values (?,?)",
                     (plant_value if plant_value is not None else ct, "Ada"))
    conn.commit()
    conn.close()
    return db, ct


READ_TAGGED_DECLARED = (
    'shape Patient\n    ssn as text [phi]\n    name as text\nshape: done\n'
    'shape Note\n    body as text\n    who as text\nshape: done\n'
    'connect db as sqlite from env.DATABASE_URL\n'
    'retrieve n from db.notes\n    match who to "Ada"\n'
    '    on.failure\n        show "none"\nretrieve: done\n'
    'show ("body is: " & n.body)\n')

READ_NO_TAG_ANYWHERE = (
    'shape Note\n    body as text\n    who as text\nshape: done\n'
    'connect db as sqlite from env.DATABASE_URL\n'
    'retrieve n from db.notes\n    match who to "Ada"\n'
    '    on.failure\n        show "none"\nretrieve: done\n'
    'show ("body is: " & n.body)\n')


print("\n== a ciphertext in an UNTAGGED column is read back as its value ==")
db, ct = fresh_db()
out = run(READ_TAGGED_DECLARED, db)
check("the value comes back decrypted", SECRET in out, out[-300:])
check("...not as the raw ciphertext it used to return", "enc:v1:" not in out, out[-300:])

print("\n-- even when the reading program declares no tagged field at all --")
# The whole-program early return was the second gate. A program that declares no encrypted field
# skipped the walk entirely, so this case did not even reach the per-column decision.
db, ct = fresh_db()
out = run(READ_NO_TAG_ANYWHERE, db)
check("a program with no tag of its own still reads the value", SECRET in out, out[-300:])
check("...and never sees `enc:v1:`", "enc:v1:" not in out, out[-300:])

print("\n== NO INFERENCE: a genuinely plaintext value is untouched ==")
# Nothing here looks at a plaintext value and guesses what it is. The only question asked is
# whether the bytes carry the marker this runtime wrote.
db, _ = fresh_db(plant_value="just an ordinary note")
out = run(READ_NO_TAG_ANYWHERE, db)
check("plain text passes through exactly as written",
      "just an ordinary note" in out, out[-300:])

print("\n-- a value that merely LOOKS like a marker is not treated as one --")
db, _ = fresh_db(plant_value="enc:v0:not-really-ours")
out = run(READ_NO_TAG_ANYWHERE, db)
check("a different prefix is left alone", "enc:v0:not-really-ours" in out, out[-300:])

print("\n== the tagged column still behaves exactly as before ==")
db, ct = fresh_db(plant_untagged=False)
out = run('shape Patient\n    ssn as text [phi]\n    name as text\nshape: done\n'
          'connect db as sqlite from env.DATABASE_URL\n'
          'retrieve p from db.patients\n    match name to "Ada"\n'
          '    on.failure\n        show "none"\nretrieve: done\n'
          'show ("ssn is: " & p.ssn)\n', db)
check("a tagged column still decrypts, then masks as it always has",
      "****1111" in out and "enc:v1:" not in out, out[-300:])
check("the stored value is still ciphertext at rest", ct.startswith("enc:v1:"), ct[:40])

print("\n-- THE BOUNDARY: this restores READABILITY, it does not move the mask --")
# Stated as an assertion rather than left to be discovered. Decryption is now a property of the
# value; MASKING is still a property of the field NAME. So the same secret read out of the
# column that declares it comes back `****1111`, and read out of a column that does not comes
# back in full. Closing that gap is the value-bound egress work, which is blocked on a separate
# ruling (see WRITE-PATH-VALUE-BOUND-FORK.md). Before this change the second case returned an
# unusable `enc:v1:` blob, which the runtime's own fail-loud calls the outcome it must never
# produce, so this is not a step back from a working mask -- there was never a mask here.
db, _ = fresh_db()
out = run(READ_NO_TAG_ANYWHERE, db)
check("the travelled value is readable", SECRET in out, out[-300:])
check("...and is NOT masked, because no field name here is classified",
      "****1111" not in out, out[-300:])

print("\n== the fail-loud still fires, and now reaches the case it was written for ==")
# With no key, a ciphertext cannot be read back. Before this change an UNTAGGED column holding
# ciphertext skipped the check entirely and handed the blob to the program.
db, _ = fresh_db()
out = run(READ_NO_TAG_ANYWHERE, db, key=None)
check("no key plus a ciphertext value refuses", "key_missing" in out or "cannot be read back" in out,
      out[-400:])
check("...rather than handing back the ciphertext", "enc:v1:" not in out.split("body is:")[-1],
      out[-400:])

print("\n== the shadow index columns are still stripped from a row ==")
db, _ = fresh_db(plant_untagged=False)
out = run('shape Patient\n    ssn as text [phi]\n    name as text\nshape: done\n'
          'connect db as sqlite from env.DATABASE_URL\n'
          'retrieve p from db.patients\n    match name to "Ada"\n'
          '    on.failure\n        show "none"\nretrieve: done\n'
          'show p\n', db)
check("no __bidx column surfaces in a whole-row show", "__bidx" not in out, out[-300:])

print("\n== over real HTTP, the path a served handler actually takes ==")
sys.argv = ['mio.py']
os.environ.setdefault('DATABASE_URL', ':memory:')
os.environ.setdefault('MOHIO_ENCRYPTION_KEY', 'testkey')
import mohio_data  # noqa: E402
from lark import Lark  # noqa: E402
from mohio_transformer_ast import transform as _tf  # noqa: E402
from mohio_interpreter import MohioInterpreter  # noqa: E402
from mohio_server import MohioServer, create_app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_g = '\n'.join(l for l in mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8').splitlines()
               if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

db, ct = fresh_db()
_src = ('shape Req\n    q as text\nshape: done\n'
        'shape Note\n    body as text\n    who as text\nshape: done\n'
        'connect db as sqlite from env.DATABASE_URL\n'
        'listen for\n    new sh.Req at /n\n'
        '        retrieve n from db.notes\n            match who to "Ada"\n'
        '            on.failure\n                give back [404] "none"\n'
        '        retrieve: done\n'
        '        give back [200] n.body\n    new: done\nlisten: done\n')
_prev = os.environ.get('DATABASE_URL')
os.environ['DATABASE_URL'] = db
try:
    _client = TestClient(create_app(MohioServer(_tf(_P.parse(_src), _src), MohioInterpreter())),
                         raise_server_exceptions=False)
    body = _client.post("/n", json={"q": "x"}).text
finally:
    if _prev is not None:
        os.environ['DATABASE_URL'] = _prev
check("a served handler does not receive ciphertext", "enc:v1:" not in body, body[:200])

shutil.rmtree(TMP, ignore_errors=True)
if _failures:
    print("\nFAILURES, repeated so the suite runner's tail carries them:")
    for line in _failures:
        print("  * " + line)
print("\nRESULTS: " + str(_p) + " passed, " + str(_f) + " failed")
sys.exit(1 if _f else 0)
