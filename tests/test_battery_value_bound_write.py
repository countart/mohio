# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""A classified value is sealed wherever it is written, not only in the column that declares it.

THE LEAK, measured by reading the database file directly. A `[phi]` field copied into a variable
and saved to a column nobody tagged landed in PLAINTEXT, while the same value in its own column
was sealed:

    patients  ssn  = enc:v1:UyqvAy6HhaUxfVjBMwSdkJZsRjKaPSi/...
    notes     body = 111-11-1111

The protection belonged to the value and was being decided by its destination. A column tag
answers "what is this column for"; it cannot answer "what is this particular value".

WHERE THE CLASS WAS LOST. `data_class` already travelled through copy, hold, concatenation and a
task boundary, which is how masking follows a value to egress. It never reached a WRITE, because
every write verb unwraps its MohioValue to a plain Python value before building its field dict.
Measured at the seam:

    [WRITE SEAM] table=notes
        body     type=str      data_class=None

The class was alive one line earlier, in `_eval_simple`, and died on `.to_python()`. Seven write
verbs build that dict and the interpreter unwraps a MohioValue in 124 places, so a parallel map
threaded from all seven is the hand-listed-family shape `_guard_write` exists to end. A value
that carries its own class needs no list: `ClassifiedText` is a `str` subclass, so every one of
those places sees the same characters and one of them now also sees the class.

ENCRYPTION STAYS AT THE WRITE, and that is the whole reason this works. Sealing the value earlier,
at the read, would remove the plaintext from the write seam and leave nothing to index, and the
blind index is an HMAC over the PLAINTEXT: AES-GCM here is randomized, so the same value encrypts
differently every time and an index taken over ciphertext differs on every save. That would make
a travelled value unfindable and un-erasable, which is the erasure bug, not a stricter safety.
Keeping encryption at the write keeps the plaintext present, so the index is computed exactly as
before and a travelled value stays findable and erasable. Asserted below by matching the index of
the travelled copy against the index of the original.

NO INFERENCE. Nothing looks at a plaintext value to guess what it is. The only question asked is
what class the value already carries, put there by the coder's own tag.

Run: PYTHONPATH=$PWD DATABASE_URL=:memory: python tests/test_battery_value_bound_write.py
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


TMP = tempfile.mkdtemp(prefix="mohio_vbw_")
_n = [0]

HEAD = ('shape Patient\n    ssn as text [phi]\n    name as text\nshape: done\n'
        'shape Note\n    body as text\n    who as text\nshape: done\n'
        'connect db as sqlite from env.DATABASE_URL\n')


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


def raw(db, table):
    conn = sqlite3.connect(db)
    try:
        cur = conn.execute("select * from " + table)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur]
    except Exception as e:
        return [{"error": str(e)}]
    finally:
        conn.close()


def new_db():
    _n[0] += 1
    return os.path.join(TMP, f"db{_n[0]}.db")


TRAVEL = (HEAD +
          'save to db.patients\n    ssn "111-11-1111"\n    name "Ada"\nsave: done\n'
          'retrieve p from db.patients\n    match name to "Ada"\n'
          '    on.failure\n        show "none"\nretrieve: done\n'
          'x p.ssn\n'
          'save to db.notes\n    body x\n    who "Ada"\nsave: done\n'
          'show "written"\n')

# ── the leak itself, read off the disk ───────────────────────────────────────────────────────
print("\n== a [phi] value copied into an UNTAGGED column is sealed on disk ==")
db = new_db()
run(TRAVEL, db)
note = (raw(db, "notes") or [{}])[0]
pat = (raw(db, "patients") or [{}])[0]
check("the untagged column holds ciphertext, not the secret",
      str(note.get("body", "")).startswith("enc:v1:"), note.get("body"))
check("...and the plaintext is nowhere in that row",
      "111-11-1111" not in str(note), note)
check("the tagged column is still sealed as it always was",
      str(pat.get("ssn", "")).startswith("enc:v1:"), pat.get("ssn"))

print("\n-- and it is still FINDABLE and ERASABLE, which is what keeps this honest --")
# The index is an HMAC over the PLAINTEXT. Encryption stays at the write, so the plaintext is
# present at that seam and the index is computed exactly as it is for a declared column. Equal
# indexes for the same secret is the proof that no index was taken over ciphertext.
check("the travelled copy is indexed", note.get("body__bidx"), note)
check("...with the SAME index as the original, so the same value still matches itself",
      note.get("body__bidx") == pat.get("ssn__bidx"),
      (note.get("body__bidx"), pat.get("ssn__bidx")))
check("...while the two ciphertexts differ, because encryption is randomized",
      note.get("body") != pat.get("ssn"), (note.get("body"), pat.get("ssn")))

# ── no inference: an untagged value is left exactly alone ────────────────────────────────────
print("\n== NO INFERENCE: a genuinely untagged value stays plaintext ==")
db = new_db()
run(HEAD + 'note "just an ordinary note"\n'
           'save to db.notes\n    body note\n    who "Ada"\nsave: done\nshow "written"\n', db)
note = (raw(db, "notes") or [{}])[0]
check("ordinary text is written as written", note.get("body") == "just an ordinary note", note)
check("...and gets no shadow index, because nothing asked for one",
      "body__bidx" not in note, note)

print("\n-- a value that merely LOOKS sensitive is still the coder's call, not ours --")
db = new_db()
run(HEAD + 'note "555-12-3456"\n'
           'save to db.notes\n    body note\n    who "Ada"\nsave: done\nshow "written"\n', db)
note = (raw(db, "notes") or [{}])[0]
check("a number shaped like an SSN in an untagged variable stays plaintext",
      note.get("body") == "555-12-3456", note)

# ── the round trip ───────────────────────────────────────────────────────────────────────────
print("\n== the travelled value reads back correctly ==")
db = new_db()
run(TRAVEL, db)
out = run(HEAD + 'retrieve n from db.notes\n    match who to "Ada"\n'
                 '    on.failure\n        show "none"\nretrieve: done\n'
                 'show ("body is: " & n.body)\n', db)
check("it decrypts on the way out", "111-11-1111" in out, out[-300:])
check("...and never surfaces as ciphertext", "enc:v1:" not in out, out[-300:])

print("\n-- no double encryption: one layer, not a blob inside a blob --")
db = new_db()
run(TRAVEL, db)
# Re-reading and re-saving the same travelled value must not wrap it twice. The already-encrypted
# guard is what holds here, and a second layer would be unrecoverable.
run(HEAD + 'retrieve n from db.notes\n    match who to "Ada"\n'
           '    on.failure\n        show "none"\nretrieve: done\n'
           'y n.body\n'
           'save to db.notes\n    body y\n    who "Bob"\nsave: done\nshow "again"\n', db)
rows = {r.get("who"): r for r in raw(db, "notes")}
bob = rows.get("Bob", {})
check("the re-saved copy is ciphertext", str(bob.get("body", "")).startswith("enc:v1:"), bob)
check("...with exactly one layer", str(bob.get("body", "")).count("enc:v1:") == 1, bob.get("body"))
out = run(HEAD + 'retrieve n from db.notes\n    match who to "Bob"\n'
                 '    on.failure\n        show "none"\nretrieve: done\n'
                 'show ("body is: " & n.body)\n', db)
check("...and it still reads back as the value", "111-11-1111" in out, out[-300:])

# ── matching, counting and erasing a travelled value ─────────────────────────────────────────
print("\n== a travelled value stays matchable, countable and erasable BY ITS VALUE ==")


def seeded():
    """Two distinct secrets travelled into an untagged column, one of them twice."""
    db = new_db()
    run(HEAD +
        'save to db.patients\n    ssn "aaa"\n    name "one"\nsave: done\n'
        'save to db.patients\n    ssn "zzz"\n    name "two"\nsave: done\n'
        'retrieve p1 from db.patients\n    match name to "one"\n'
        '    on.failure\n        show "none"\nretrieve: done\n'
        'retrieve p2 from db.patients\n    match name to "two"\n'
        '    on.failure\n        show "none"\nretrieve: done\n'
        'a p1.ssn\nb p2.ssn\n'
        'save to db.notes\n    body a\n    who "one"\nsave: done\n'
        'save to db.notes\n    body b\n    who "two"\nsave: done\n'
        'save to db.notes\n    body b\n    who "dup"\nsave: done\nshow "seeded"\n', db)
    return db


def whos(out):
    """Just the rows the program printed.

    Asserting against the whole run output matched the connect banner, which contains the phrase
    "data lives in this one file" and therefore the word `one`. A test that reads its own noise
    reports on the wrong thing.
    """
    return [l.strip() for l in out.splitlines() if l.strip() in ("one", "two", "dup")]


FIND = (HEAD + 'find rows in db.notes\n    where body is "{v}"\nfind: done\n'
                'repeat each r in rows\n    show r.who\nrepeat: done\n')

db = seeded()
got = whos(run(FIND.format(v="zzz"), db))
check("both rows holding the same secret are found", sorted(got) == ["dup", "two"], got)
got = whos(run(FIND.format(v="aaa"), db))
check("the other secret finds its own row, and only its own", got == ["one"], got)
got = whos(run(FIND.format(v="nothere"), db))
check("a value that is genuinely absent still finds nothing", got == [], got)

out = run(HEAD + 'find rows in db.notes\n    where body is "zzz"\nfind: done\n'
                 'show rows.count\n', db)
check("counting by value counts both", "2" in out, out[-300:])

print("\n-- erasure, the property the blind index exists for --")
db = seeded()
run(HEAD + 'remove from db.notes\n    where body is "zzz"\nremove: done\nshow "removed"\n', db)
left = sorted(r.get("who") for r in raw(db, "notes"))
check("both rows holding that secret are erased by its value", left == ["one"], left)

print("\n-- check unique and check exists answer truthfully about a travelled value --")
db = seeded()
UNIQ = (HEAD + 'check unique in db.notes\n    match body to "{v}"\n'
                '    when empty\n        show "AVAILABLE"\n'
                '    otherwise\n        show "TAKEN"\ncheck: done\n')
out = run(UNIQ.format(v="zzz"), db)
check("a secret already stored reports TAKEN", "TAKEN" in out, out[-300:])
out = run(UNIQ.format(v="nothere"), db)
check("one that is not reports AVAILABLE", "AVAILABLE" in out, out[-300:])

# ── the cousins that had to learn the same lesson ────────────────────────────────────────────
print("\n== ordering and range REFUSE on a column that actually holds encrypted data ==")
# A blind index preserves "same value", never order. The declared column has refused this for
# exactly that reason; a travelled value used to slip past because the refusal asked the
# declaration instead of the data. Measured before: ordering sorted the ciphertext, and a range
# comparison returned an empty list over rows that hold the value.
db = seeded()
out = run(HEAD + 'find rows in db.notes\n    order.up by body\nfind: done\nshow "sorted"\n', db)
check("ordering refuses instead of sorting ciphertext",
      "encrypted_field_not_rangeable" in out, out[-300:])
check("...and says why, in the words the declared case already uses",
      "only be matched for equality" in out, out[-300:])
out = run(HEAD + 'find rows in db.notes\n    where body is above "m"\nfind: done\nshow "ranged"\n', db)
check("a range comparison refuses instead of returning an empty list",
      "encrypted_field_not_rangeable" in out, out[-300:])

print("\n-- an ORDINARY column is untouched by all of this --")
db = seeded()
out = run(HEAD + 'find rows in db.notes\n    order.up by who\nfind: done\n'
                 'repeat each r in rows\n    show r.who\nrepeat: done\n', db)
check("ordering a plain column still works", "dup" in out and "one" in out, out[-300:])
check("...and is not refused", "encrypted_field_not_rangeable" not in out, out[-300:])

# ── over real HTTP ───────────────────────────────────────────────────────────────────────────
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

db = new_db()
_src = ('shape Patient\n    ssn as text [phi]\n    name as text\nshape: done\n'
        'shape Note\n    body as text\n    who as text\nshape: done\n'
        'shape Req\n    q as text\nshape: done\n'
        'connect db as sqlite from env.DATABASE_URL\n'
        'listen for\n    new sh.Req at /w\n'
        '        save to db.patients\n            ssn "111-11-1111"\n'
        '            name "Ada"\n        save: done\n'
        '        retrieve p from db.patients\n            match name to "Ada"\n'
        '            on.failure\n                give back [404] "none"\n'
        '        retrieve: done\n'
        '        x p.ssn\n'
        '        save to db.notes\n            body x\n            who "Ada"\n        save: done\n'
        '        give back [200] "stored"\n    new: done\nlisten: done\n')
_prev = os.environ.get('DATABASE_URL')
os.environ['DATABASE_URL'] = db
try:
    _client = TestClient(create_app(MohioServer(_tf(_P.parse(_src), _src), MohioInterpreter())),
                         raise_server_exceptions=False)
    body = _client.post("/w", json={"q": "x"}).text
finally:
    if _prev is not None:
        os.environ['DATABASE_URL'] = _prev
check("the served write reports success", "stored" in body, body[:200])
note = (raw(db, "notes") or [{}])[0]
check("and the untagged column on disk holds ciphertext, not the secret",
      str(note.get("body", "")).startswith("enc:v1:"), note.get("body"))
check("...with the plaintext nowhere in the row", "111-11-1111" not in str(note), note)

shutil.rmtree(TMP, ignore_errors=True)
if _failures:
    print("\nFAILURES, repeated so the suite runner's tail carries them:")
    for line in _failures:
        print("  * " + line)
print("\nRESULTS: " + str(_p) + " passed, " + str(_f) + " failed")
sys.exit(1 if _f else 0)
