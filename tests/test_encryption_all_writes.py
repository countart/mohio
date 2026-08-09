# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Every write path must encrypt tagged fields at rest -- not just `save`.

`save` encrypted [phi]/[pii]/[pci] fields. `update`, `upsert` (save or update), and
`save all` did NOT: they handed fields straight to the database driver, so a tagged field
first written through any of those paths went in as PLAINTEXT, and the fail-loud never
fired because the encrypt code was never entered.

That is the public encryption claim silently having an exception nobody wrote down: true
for save, false for the other three writes. These tests exist so every write path is held
to the same guarantee -- ciphertext at rest, and refuse (not store plaintext) with no key.

Harness mirrors tests/test_encryption.py: the `retrieve raw from db.* / SELECT` trick reads
the value straight from the table so we see whether it is `enc:v1:` ciphertext at rest.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter

_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

SHAPE = ('shape Person\n    ssn as text [pii] sec.encrypt required\n'
         '    name as text\nshape: done\n')
DB = 'connect db as sqlite from env.DATABASE_URL\n'


def run(src):
    return MohioInterpreter().run(transform(_P.parse(src), src)).get('body')


def _val(body):
    return body.to_python() if hasattr(body, 'to_python') else body


def raw_ssn(where_name):
    """Read ssn straight from the table (bypassing decrypt) to see what is AT REST."""
    return (f'retrieve raw from db.*\n    sql\n        SELECT ssn FROM people\n'
            f'    sql: done\nretrieve: done\ngive back 200 raw.first.ssn\n')


_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)


# ---------------------------------------------------------------------------------------
# UPDATE. Seed a row with a plain save (encrypted), then UPDATE the tagged field and
# confirm the NEW value is ciphertext at rest -- i.e. the update path encrypted it.
# ---------------------------------------------------------------------------------------
os.environ['MOHIO_ENCRYPTION_KEY'] = 'test-secret-key-123'

SEED = 'save to db.people\n    ssn "000-00-0000"\n    name "Ada"\nsave: done\n'
UPDATE = ('update db.people\n    match name to "Ada"\n    ssn "123-45-6789"\nupdate: done\n')

at_rest = _val(run(SHAPE + DB + SEED + UPDATE + raw_ssn("Ada")))
check("update: tagged field is ciphertext at rest",
      isinstance(at_rest, str) and at_rest.startswith('enc:v1:'),
      f"got {at_rest!r} -- if this is a bare SSN, update stored PHI in plaintext")

# and it must still round-trip back to plaintext on read
back = _val(run(SHAPE + DB + SEED + UPDATE +
                'retrieve p from db.people\n    match name to "Ada"\nretrieve: done\n'
                'give back 200 p.ssn\n'))
check("update: value round-trips to plaintext on read",
      str(back) == '123-45-6789', f"got {back!r}")


# ---------------------------------------------------------------------------------------
# UPSERT (save or update). Write a tagged field via upsert and confirm ciphertext at rest.
# ---------------------------------------------------------------------------------------
UPSERT = ('save or update db.people\n    match name to "Bea"\n    ssn "555-66-7777"\n'
          'save: done\n')

at_rest = _val(run(SHAPE + DB + UPSERT +
                   'retrieve raw from db.*\n    sql\n        SELECT ssn FROM people\n'
                   '    sql: done\nretrieve: done\ngive back 200 raw.first.ssn\n'))
check("upsert: tagged field is ciphertext at rest",
      isinstance(at_rest, str) and at_rest.startswith('enc:v1:'),
      f"got {at_rest!r} -- if this is a bare SSN, upsert stored PHI in plaintext")

back = _val(run(SHAPE + DB + UPSERT +
                'retrieve p from db.people\n    match name to "Bea"\nretrieve: done\n'
                'give back 200 p.ssn\n'))
check("upsert: value round-trips to plaintext on read",
      str(back) == '555-66-7777', f"got {back!r}")


# ---------------------------------------------------------------------------------------
# The fail-loud must hold on the NEW paths too: no key -> refuse, do not store plaintext.
# ---------------------------------------------------------------------------------------
os.environ.pop('MOHIO_ENCRYPTION_KEY', None)

body = run(SHAPE + DB + 'save to db.people\n    ssn "1"\n    name "Cy"\nsave: done\n'
           + 'update db.people\n    match name to "Cy"\n    ssn "9-9-9"\nupdate: done\n'
           + 'give back 200 "done"\n')
txt = str(_val(body))
check("update: fails loud without a key (does not store plaintext)",
      'key_missing' in txt or 'encryption' in txt,
      f"expected a key_missing error, got {txt!r}")

body = run(SHAPE + DB +
           'save or update db.people\n    match name to "Di"\n    ssn "8-8-8"\nsave: done\n'
           + 'give back 200 "done"\n')
txt = str(_val(body))
check("upsert: fails loud without a key (does not store plaintext)",
      'key_missing' in txt or 'encryption' in txt,
      f"expected a key_missing error, got {txt!r}")

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
