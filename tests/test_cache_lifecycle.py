# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""AST-cache lifecycle: a file that passes Layer 1 but fails a later layer must NEVER create a
reusable clean cache that bypasses the failing layer on the next `mio check` (audit finding #3).

Before the single-door correction, `_parse_and_validate` saved the AST cache right after Layer 1
validation -- so a file that passed Layer 1 but failed AST construction (Layer 2) or a whole-program
scan (Layer 3) could be cached as "clean" and replay clean on the next run, bypassing the failing
layer. The cache save now happens only after the full pipeline confirms the file is clean through
Layer 3.

This test runs a Layer-2-failing specimen TWICE and asserts exit 1 both times, and that no clean
cache is left behind to bypass enforcement.
"""
import os, sys, subprocess, tempfile, glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DATABASE_URL', ':memory:')
os.environ.setdefault('MOHIO_ENCRYPTION_KEY', 'testkey')
os.environ['PYTHONPATH'] = ROOT

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)


def run_check(path):
    r = subprocess.run([sys.executable, os.path.join(ROOT, 'mio.py'), 'check', path],
                       cwd=ROOT, capture_output=True, text=True, timeout=90)
    return r.returncode


# A specimen that PASSES Layer 1 (all tokens valid) but FAILS Layer 2 (AST construction): the
# retired `done as NAME` closer-naming form. Layer 1 sees only valid tokens; the transformer
# rejects it while assembling the block.
L2_FAIL = ('retrieve raw from db.cards\n'
           '    sql\n'
           '        SELECT 1\n'
           '    sql: done as raw\n'
           'retrieve: done\n')

d = tempfile.mkdtemp()
spec = os.path.join(d, 'l2fail.mho')
open(spec, 'w', encoding='utf-8').write(L2_FAIL)

# clear any stale cache
for c in glob.glob(os.path.join(d, '*.cache')):
    os.unlink(c)

first = run_check(spec)
check("first run of a Layer-2-failing file exits non-zero", first != 0, f"exit {first}")

# whatever cache state resulted, the SECOND run must ALSO fail -- no clean-cache bypass
second = run_check(spec)
check("second run still exits non-zero (no clean-cache bypass)", second != 0, f"exit {second}")

# and no clean cache should be sitting there claiming the file is fine
caches = glob.glob(os.path.join(d, '*.cache'))
check("no reusable clean cache was written for the failing file",
      len(caches) == 0, f"found: {caches}")

# sanity: a genuinely clean file still exits 0 (we did not break the happy path)
ok = os.path.join(d, 'ok.mho')
open(ok, 'w', encoding='utf-8').write('shape T\n    age as int\nshape: done\n')
check("a clean file still passes (exit 0)", run_check(ok) == 0)

# cleanup
import shutil
shutil.rmtree(d, ignore_errors=True)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
