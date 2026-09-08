# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""A tag belongs to the VALUE, so copying a classified value does not strip its protection.

THE LEAK. The egress mask matched a field NAME, so once a classified value was copied into an
ordinary variable there was no name left to match and it went out whole. Copying a value to a
variable is the most ordinary operation there is, which is what made this a silent leak on an
everyday line.

MEASURED FIRST, the same program with each tag, and the measurement is what made the fix one
line rather than a rewrite:

                                        [pci]     [phi]
    give back p.ssn                     masked    masked
    x p.ssn then give back x            masked    PLAINTEXT
    hold s = p.ssn then give back s     masked    PLAINTEXT
    concatenated into a string          masked    PLAINTEXT
    passed into a task, returned        masked    PLAINTEXT

The value-bound mechanism ALREADY EXISTED and already worked: `data_class` travels with the
value through assignment, hold, concatenation and a task boundary, which is why pci survived all
four. phi simply never set it. So the metadata was neither dropped on assignment nor read
wrongly at egress, which were the two possibilities the brief asked to distinguish between: it
was never attached in the first place.

WHAT THIS FIXES is plain egress of a copied value. It does not touch export, and it forces no
purpose declaration on anyone.

Run: PYTHONPATH=$PWD DATABASE_URL=:memory: python tests/test_battery_value_bound_classification.py
"""
import os
import re
import sys

sys.argv = ['mio.py']
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault('DATABASE_URL', ':memory:')
os.environ.setdefault('MOHIO_ENCRYPTION_KEY', 'testkey')

import mohio_data  # noqa: E402
from lark import Lark  # noqa: E402
from mohio_transformer_ast import transform  # noqa: E402
from mohio_interpreter import MohioInterpreter  # noqa: E402
from mohio_server import MohioServer, create_app  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

_g = '\n'.join(l for l in mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8').splitlines()
               if not l.strip().startswith('//'))
P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

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


SECRET = "111-11-1111"
MASK = "****1111"
TASK = ('task echo\n    take v as text\n    returns text\n    give back v\ntask: done\n')


def serve(tail, tag='phi'):
    src = ('shape Patient\n    ssn as text [' + tag + ']\n    name as text\nshape: done\n'
           'connect db as sqlite from env.DATABASE_URL\n' + TASK +
           'listen for\n    new sh.Patient at /p\n'
           '        save to db.patients\n            ssn "' + SECRET + '"\n'
           '            name "Ada"\n        save: done\n'
           '        retrieve p from db.patients\n            match name to "Ada"\n'
           '            on.failure\n                give back [404] "none"\n'
           '        retrieve: done\n'
           + tail + '\n    new: done\nlisten: done\n')
    prog = transform(P.parse(src), src)
    client = TestClient(create_app(MohioServer(prog, MohioInterpreter())),
                        raise_server_exceptions=False)
    r = client.post("/p", json={})
    return r.status_code, r.text


COPY_PATHS = (
    ("copied to a plain variable", '        x p.ssn\n        give back 200 x'),
    ("held in a hold",             '        hold s = p.ssn\n        give back 200 s'),
    ("concatenated into a string", '        give back 200 ("SSN " & p.ssn)'),
    ("passed into a task and returned",
     '        call echo as got\n            v p.ssn\n        call: done\n'
     '        give back 200 got'),
    ("copied twice",               '        x p.ssn\n        y x\n        give back 200 y'),
)

# ── the leak, on every path a value can be copied then emitted ───────────────────────────────
for tag in ('phi', 'pci'):
    print(f"\n== [{tag}] survives being copied ==")
    for label, tail in COPY_PATHS:
        status, body = serve(tail, tag)
        check(f"[{tag}] {label}: the secret is not in the response",
              SECRET not in body, body[:140])
        check(f"...[{tag}] {label}: and the masked form is", MASK in body, body[:140])

# ── the paths that already worked must keep working ──────────────────────────────────────────
print("\n== the direct and whole-object paths are unchanged ==")
for tag in ('phi', 'pci'):
    for label, tail in (("direct give back", '        give back 200 p.ssn'),
                        ("whole object",     '        give back 200 p'),
                        ("whole object as.json", '        give back p as.json')):
        status, body = serve(tail, tag)
        check(f"[{tag}] {label} still masks", MASK in body and SECRET not in body, body[:140])

print("\n-- render still masks, and is NOT loosened by this build --")
status, body = serve('        render\n            <p>MARK {{ p.ssn }}</p>\n        render: done')
m = re.search(r'MARK ([^<]*)', body)
check("a rendered classified field is masked", m is not None and MASK in m.group(1),
      m.group(1) if m else body[:150])
check("...and the secret is nowhere in the page", SECRET not in body, body[:200])

# ── THE FALSE-POSITIVE GUARD: only tagged values mask ────────────────────────────────────────
print("\n== an untagged value is untouched on every one of those paths ==")
for label, tail in (
        ("copied to a variable", '        x p.name\n        give back 200 x'),
        ("held",                 '        hold s = p.name\n        give back 200 s'),
        ("concatenated",         '        give back 200 ("NAME " & p.name)'),
        ("passed into a task",
         '        call echo as got\n            v p.name\n        call: done\n'
         '        give back 200 got'),
):
    status, body = serve(tail)
    check(f"untagged {label}: comes back whole", "Ada" in body, body[:140])
    check(f"...untagged {label}: nothing was masked", "****" not in body, body[:140])

# ── ciphertext is still never masked ─────────────────────────────────────────────────────────
print("\n== a value still at rest is never masked, which would destroy it ==")
status, body = serve('        retrieve raw from db.*\n            sql\n'
                     '                SELECT ssn FROM patients\n            sql: done\n'
                     '        retrieve: done\n'
                     '        x raw.first.ssn\n'
                     '        give back 200 x')
check("copied ciphertext comes back as ciphertext, not as a mangled mask",
      "enc:v1:" in body, body[:160])
check("...and is not masked", "****" not in body, body[:160])

if _failures:
    print("\nFAILURES, repeated so the suite runner's tail carries them:")
    for line in _failures:
        print("  * " + line)
print("\nRESULTS: " + str(_p) + " passed, " + str(_f) + " failed")
sys.exit(1 if _f else 0)
