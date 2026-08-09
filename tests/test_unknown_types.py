# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""A type slot takes a known type or a declared shape. Nothing else.

THE DRIFT GENERATOR: `type_name` accepts a bare NAME (it must -- `sh.Order` arrives that
way), so ANY word in a type slot was silently accepted. `n as banana` checked clean. So did
every typo and every retired type. Silent acceptance is how wrong syntax survives, gets
copied into docs, and comes back next session as "canonical".
"""
import subprocess, sys, os, tempfile
env = dict(os.environ, PYTHONPATH=os.getcwd(), DATABASE_URL=':memory:')
_p = _f = 0
def check(label, src, want_exit):
    global _p, _f
    fd, path = tempfile.mkstemp(suffix='.mho'); os.write(fd, src.encode()); os.close(fd)
    r = subprocess.run([sys.executable, 'mio.py', 'check', path], env=env,
                       capture_output=True, text=True, timeout=180)
    os.unlink(path)
    ok = (r.returncode == want_exit)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label} (exit {r.returncode}, want {want_exit})")
    _p += ok; _f += (not ok)

T = 'task t\n    take n as %s\n    returns int\n\n    give back 1\ntask: done\n'
check("unknown type `banana` fails",  T % 'banana', 1)
check("retired `num` fails",          T % 'num',    1)
check("retired `number` fails",       T % 'number', 1)
check("`int` passes",                 T % 'int',    0)
check("`dec` passes",                 T % 'dec',    0)
check("`text` passes",                T % 'text',   0)
check("declared shape passes",
      'shape Order\n    total as dec\nshape: done\n\n'
      'task t\n    take o as sh.Order\n    returns text\n\n    give back "ok"\ntask: done\n', 0)
check("`list text` field passes",
      'shape Order\n    items as list text\nshape: done\n', 0)

# The type-before-value form is RETIRED entirely -- backwards (a modifier follows what it
# modifies) and redundant (5 already carries its type). It used to degrade into two junk
# assignments (`x = as`, `int = 5`) and run.
check("`x as int 5` is retired",        'x as int 5\nshow x\n', 1)
check("`hold x as int 5` is retired",   'hold x as int 5\nshow x\n', 1)
check("`hold x as banana 5` is retired",'hold x as banana 5\nshow x\n', 1)
check("`x 5` passes",                   'x 5\nshow x\n', 0)
check("`x = 5` passes (sugar)",         'x = 5\nshow x\n', 0)
check("`hold x 5` passes",              'hold x 5\nshow x\n', 0)
check("`lock k 3.14` passes",           'lock k 3.14\nshow k\n', 0)

# `set` is RETIRED. It used to be accepted as noise and silently discarded -- the exact
# pattern that keeps a dead keyword alive in docs until it comes back as canon.
check("`set x = 5` fails loud",         'set x = 5\nshow x\n', 1)
check("`set x 5` fails loud",           'set x 5\nshow x\n', 1)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
