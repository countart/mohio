# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""Not-found / empty-result on a `find` -- the four paths of the canonical shape must be distinct.

Canonical (Ronnie-ruled):
    find carrying in db.T
        match ...
        on.failure          -> operational failure
        otherwise           -> success
            check carrying
                when empty   -> zero rows (a real empty result)
                otherwise    -> results
            check: done
    find: done

The subtle, non-negotiable part: a MISSING TABLE and a ZERO-ROW result look identical at the result
level (both empty) but must NEVER share a branch -- a missing table is an operational FAILURE
(on.failure), a zero-row result is a real empty (when empty). Verified by running:
  1. genuine failure (no db)      -> on.failure
  2. missing table                -> on.failure   (was silently collapsed into empty-success)
  3. success with rows            -> results branch
  4. success with zero rows       -> when empty    (was the silent no-op: silently took results)
"""
import os, sys, subprocess, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)

TAIL = ('    on.failure\n        show "MARK_FAIL"\n'
        '    otherwise\n        show "MARK_SUCCESS"\n'
        '        check carrying\n            when empty\n                show "MARK_EMPTY"\n'
        '            otherwise\n                show "MARK_RESULTS"\n        check: done\nfind: done\n')
SEED = ('connect db as sqlite from env.DATABASE_URL\n'
        'save to db.items\n    name "lantern"\n    location "inventory"\nsave: done\n')

def run_mho(body):
    fd, path = tempfile.mkstemp(suffix='.mho')
    os.write(fd, body.encode('utf-8')); os.close(fd)
    env = dict(os.environ, DATABASE_URL=':memory:', PYTHONIOENCODING='utf-8')
    r = subprocess.run([sys.executable, 'mio.py', 'run', path], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=120)
    try: os.remove(path)
    except OSError: pass
    return r.stdout + r.stderr

def marks(out):
    return {m for m in ('MARK_FAIL', 'MARK_SUCCESS', 'MARK_EMPTY', 'MARK_RESULTS') if m in out}


print("=== 1. genuine failure (no db connection) -> on.failure ===")
m = marks(run_mho('find carrying in db.items\n    match location to "inventory"\n' + TAIL))
check("on.failure fires", 'MARK_FAIL' in m, str(m))
check("success branch does NOT fire", 'MARK_SUCCESS' not in m, str(m))

print("\n=== 2. MISSING TABLE -> on.failure (NOT empty-success) ===")
m = marks(run_mho('connect db as sqlite from env.DATABASE_URL\n'
                  'find carrying in db.ghost_table\n    match location to "inventory"\n' + TAIL))
check("on.failure fires for a missing table", 'MARK_FAIL' in m, str(m))
check("it did NOT take the success/empty path", 'MARK_SUCCESS' not in m and 'MARK_EMPTY' not in m, str(m))

print("\n=== 3. success WITH rows -> results branch ===")
m = marks(run_mho(SEED + 'find carrying in db.items\n    match location to "inventory"\n' + TAIL))
check("success fires", 'MARK_SUCCESS' in m, str(m))
check("results branch fires (not when empty)", 'MARK_RESULTS' in m and 'MARK_EMPTY' not in m, str(m))

print("\n=== 4. success with ZERO rows -> when empty (the silent no-op we killed) ===")
m = marks(run_mho(SEED + 'find carrying in db.items\n    match location to "warehouse"\n' + TAIL))
check("success fires", 'MARK_SUCCESS' in m, str(m))
check("WHEN EMPTY fires on zero rows (not silently the results otherwise)",
      'MARK_EMPTY' in m and 'MARK_RESULTS' not in m, str(m))

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
