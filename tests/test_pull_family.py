#!/usr/bin/env python3
# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
Lock tests for the pull family — the verified db-source paths.

Covers:
  1. `pull NAME up to N from db.table`        -> first N rows, in order, bound to NAME
  2. `pull NAME up to N random from db.table` -> N rows, sampled (varies)
  3. `pull NAME up to BIG random from` a small table -> clamps to table size,
     never errors (the `up to` ceiling, via min(n, len))
  4. The result name is on the OPENER (`pull picks up to N from ...`), mirroring
     `retrieve NAME from ...`. The retired `pull: done as NAME` (naming-on-closer) fails loud.

NOTE: `pull` is canonically a queue/stream extraction verb; queue/stream runtime sources are a
pending build (they currently fail "must be a collection"). Until then, pull runs against the
collections it can see -- db tables and held lists -- which is what these tests exercise. Held-list
and find-result sources also work at runtime; the db-table source is locked here.
"""
import os, sys, collections
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import mohio_data
os.chdir(ROOT)
os.environ.setdefault('DATABASE_URL', ':memory:')

from pathlib import Path
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter, DbRuntime

_raw = mohio_data.GRAMMAR_PATH.read_text()
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

HEAD = 'connect db as sqlite from env.DATABASE_URL\n'


def _seed(db):
    db.conn.execute("CREATE TABLE rooms (id INTEGER PRIMARY KEY, name TEXT)")
    db.conn.executemany(
        "INSERT INTO rooms(name) VALUES (?)",
        [('r1',), ('r2',), ('r3',), ('r4',), ('r5',), ('r6',)])
    db.conn.commit()


def run(body):
    it = MohioInterpreter()
    db = DbRuntime(':memory:'); _seed(db); it._db = db
    tree = transform(P.parse(HEAD + body), HEAD + body)
    it.run_declarations(tree)
    r = it.run(tree)
    v = getattr(r, 'value', r)
    return v.to_python() if hasattr(v, 'to_python') else v


PASS = FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  PASS  {name}")
    else:    FAIL += 1; print(f"  FAIL  {name}")


def _names(rows):
    return [r.get('name') for r in rows] if isinstance(rows, list) else None


# 1. ordered pull — first N rows in order, bound via `as`
out = run('pull picks up to 3 from db.rooms\npull: done\nshow picks\n')
check("pull up to 3 from db.table -> first 3 in order",
      _names(out) == ['r1', 'r2', 'r3'])

# 4. as_name binding actually happened (picks was usable by `show`)
check("pull: done as NAME binds the result", isinstance(out, list) and len(out) == 3)

# 4b. the retired closer form `pull: done as NAME` fails loud (naming goes on the action head)
try:
    _b = HEAD + 'pull up to 3 from db.rooms\npull: done as picks\nshow picks\n'
    transform(P.parse(_b), _b)
    check("retired `pull: done as NAME` fails loud", False)
except Exception:
    check("retired `pull: done as NAME` fails loud", True)

# 2. random pull — N items, and the set varies across runs
seen = set(); sizes = collections.Counter()
for _ in range(12):
    out = run('pull picks up to 3 random from db.rooms\npull: done\nshow picks\n')
    if isinstance(out, list):
        sizes[len(out)] += 1
        seen.add(tuple(sorted(_names(out))))
check("pull up to 3 random -> always 3 items", set(sizes) == {3})
check("pull up to 3 random -> set varies (>1 distinct over 12 runs)", len(seen) > 1)

# 3. clamp — `up to 9` from a 6-row table returns 6, never errors
out = run('pull picks up to 9 random from db.rooms\npull: done\nshow picks\n')
check("pull up to 9 random from 6-row table -> 6 (clamp, no error)",
      isinstance(out, list) and len(out) == 6)

print(f"\nRESULTS: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
