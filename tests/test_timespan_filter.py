# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""A declared `timespan NAME` filters a find by its half-open [start, end) window (2026-08-01).

A declared timespan (`timespan window / start <date> / end <date> / timespan: done`) used to be
stored and NEVER read (inert). Now referencing it in a find (`find ... / timespan window`) range-
filters the result. Ruling: it defaults to the `created_at` column and FAILS LOUD if the table
has no `created_at` -- never silently filters on nothing. The explicit-field form
(`timespan NAME on <field>`) is a deferred follow-on. Run: `python tests/test_timespan_filter.py`.
"""
import os, sys, subprocess, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV = dict(os.environ, PYTHONPATH=ROOT, DATABASE_URL=':memory:',
           PYTHONIOENCODING='utf-8', PYTHONUTF8='1')

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail: print(f"          {detail}")
    _p += bool(cond); _f += (not cond)

def run(src):
    fd, p = tempfile.mkstemp(suffix='.mho'); os.write(fd, src.encode()); os.close(fd)
    try:
        r = subprocess.run([sys.executable, os.path.join(ROOT, 'mio.py'), 'run', p],
                           cwd=ROOT, env=ENV, capture_output=True, text=True, timeout=45)
        return r.stdout + r.stderr
    finally:
        os.unlink(p)

WIN = ('connect db as sqlite from env.DATABASE_URL\n'
       'timespan window\n    start 2026-01-01\n    end 2026-07-01\ntimespan: done\n')

# ── filter correctness: rows inside [start, end) returned, outside excluded ──────────────
seed = (WIN +
        'save to db.events\n    label "IN"\n    created_at "2026-03-15"\nsave: done\n'
        'save to db.events\n    label "BEFORE"\n    created_at "2025-12-31"\nsave: done\n'
        'save to db.events\n    label "AFTER"\n    created_at "2026-09-01"\nsave: done\n'
        'save to db.events\n    label "START"\n    created_at "2026-01-01"\nsave: done\n'   # inclusive
        'save to db.events\n    label "END"\n    created_at "2026-07-01"\nsave: done\n')     # exclusive
out = run(seed + 'find rows in db.events\n    timespan window\nfind: done\n'
                 'repeat each e in rows\n    show "ROW:" & e.label\nrepeat: done\n')
labels = {l.split('ROW:')[1].strip() for l in out.splitlines() if 'ROW:' in l}
check("timespan filters to the in-window rows",
      'IN' in labels and 'BEFORE' not in labels and 'AFTER' not in labels, str(labels))
check("half-open: start date INCLUDED, end date EXCLUDED",
      'START' in labels and 'END' not in labels, str(labels))

# ── created_at fail-loud: a table without the column refuses, naming it ──────────────────
out = run(WIN + 'save to db.notes\n    label "X"\n    body "hi"\nsave: done\n'
                'find rows in db.notes\n    timespan window\nfind: done\n')
check("table without created_at fails loud, naming the field",
      'no `created_at`' in out and 'notes' in out, out[-160:])

# ── undeclared timespan fails loud ──────────────────────────────────────────────────────
out = run('connect db as sqlite from env.DATABASE_URL\n'
          'save to db.events\n    created_at "2026-03-15"\nsave: done\n'
          'find rows in db.events\n    timespan ghost\nfind: done\n')
check("undeclared timespan fails loud", "timespan 'ghost' is not declared" in out, out[-160:])

# ── a timespan with end on/before start fails loud (empty window) ────────────────────────
out = run('connect db as sqlite from env.DATABASE_URL\n'
          'timespan bad\n    start 2026-07-01\n    end 2026-01-01\ntimespan: done\n'
          'save to db.events\n    created_at "2026-03-15"\nsave: done\n'
          'find rows in db.events\n    timespan bad\nfind: done\n')
check("timespan with end <= start fails loud", 'window would be empty' in out, out[-160:])

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
