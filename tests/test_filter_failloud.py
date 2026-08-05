# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""An unknown where/find filter must FAIL LOUD, never silently match every row (_row_matches).

A non-equality filter condition that `_row_matches` did not recognize fell through to `return True`
-- so the filter matched EVERY row. That is a data-correctness AND exposure bug: a filter meant to
EXCLUDE rows would instead return all of them, leaking rows it was supposed to keep out. This was
live for `ends with` (it returned every row instead of the suffix match). This locks:
  1. `ends with` / `starts with` / `contains` filter to the correct SUBSET (they exclude non-matches).
  2. an unrecognized condition RAISES a clear error (never match-all).
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


def find_count(where_line):
    body = ('connect db as sqlite from env.DATABASE_URL\n'
            'save to db.t\n    name "alpha"\nsave: done\n'
            'save to db.t\n    name "omega"\nsave: done\n'
            'find r in db.t\n' + where_line + '\nfind: done\nshow r.count\n')
    fd, path = tempfile.mkstemp(suffix='.mho')
    os.write(fd, body.encode('utf-8')); os.close(fd)
    env = dict(os.environ, DATABASE_URL=':memory:', PYTHONIOENCODING='utf-8')
    r = subprocess.run([sys.executable, 'mio.py', 'run', path], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=120)
    try: os.remove(path)
    except OSError: pass
    lines = [l.strip() for l in (r.stdout + r.stderr).splitlines()
             if l.strip() and '[connect]' not in l and 'compiling' not in l]
    return lines[-1] if lines else ''


print("=== filters exclude non-matches (2 rows: alpha, omega) ===")
check("ends with 'ega' -> 1 (omega only; alpha excluded, not match-all)",
      find_count('    where name ends with "ega"') == '1',
      find_count('    where name ends with "ega"'))
check("starts with 'alp' -> 1 (alpha only)", find_count('    where name starts with "alp"') == '1')
check("contains 'meg' -> 1 (omega only)", find_count('    where name contains "meg"') == '1')

print("\n=== an unrecognized filter condition FAILS LOUD (never match-all) ===")
# The grammar restricts filter WORDS in source, so an unknown condition is exercised by calling
# _row_matches directly -- the guard that used to `return True` (match every row).
from mohio_interpreter import MohioInterpreter, MohioRuntimeError
it = MohioInterpreter()
raised = False
msg = ""
try:
    it._row_matches({'name': 'alpha'}, 'name', 'frobnicate', 'x')
except MohioRuntimeError as e:
    raised, msg = True, str(e)
check("unknown condition raises (did not return / match-all)", raised, "returned without raising")
check("the error names the unknown condition", 'unknown filter condition' in msg and 'frobnicate' in msg, msg)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
