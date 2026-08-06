#!/usr/bin/env python3
# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
Lock tests for `find ... random.N` — return N random matching records.

This is the clean "filter then sample" path (the RSS use case): a find with a
where/match filter plus random.N returns N random rows *from the matches*,
binding to the find's own name (no bare-variable `from` ambiguity).

Covers:
  1. find ... random.N           -> N random rows (varies), limited to N
  2. find ... where X random.N   -> N random rows from the filtered set only
  3. find ... random.BIG         -> clamps to the match count, never errors
  4. find ... (no random)        -> unchanged: all matches, in order
"""
import os, sys, json, collections
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
    db.conn.execute("CREATE TABLE rooms (id INTEGER PRIMARY KEY, name TEXT, kind TEXT)")
    db.conn.executemany(
        "INSERT INTO rooms(name, kind) VALUES (?, ?)",
        [('r1', 'cave'), ('r2', 'cave'), ('r3', 'cave'),
         ('r4', 'field'), ('r5', 'field'), ('r6', 'cave')])
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


def names(rows):
    return [r.get('name') for r in rows] if isinstance(rows, list) else None


# 1. find ... random.3 -> 3 random rows, varies
sizes = collections.Counter(); seen = set()
for _ in range(12):
    out = run('find picks in db.rooms random.3\nfind: done\nshow picks\n')
    if isinstance(out, list):
        sizes[len(out)] += 1; seen.add(tuple(sorted(names(out))))
check("find ... random.3 -> always 3 rows", set(sizes) == {3})
check("find ... random.3 -> varies (>1 distinct set over 12 runs)", len(seen) > 1)

# 2. find ... where kind is "cave" random.2 -> 2 rows, all caves
allcave = True; sizes2 = collections.Counter()
for _ in range(12):
    out = run('find picks in db.rooms where kind is "cave" random.2\nfind: done\nshow picks\n')
    if isinstance(out, list):
        sizes2[len(out)] += 1
        allcave &= all(r.get('kind') == 'cave' for r in out)
check("find ... where kind=cave random.2 -> always 2 rows", set(sizes2) == {2})
check("find ... where kind=cave random.2 -> only cave rows (filter then sample)", allcave)

# 3. clamp: random.99 from 6 rows -> 6, no error
out = run('find picks in db.rooms random.99\nfind: done\nshow picks\n')
check("find ... random.99 from 6 rows -> 6 (clamp, no error)",
      isinstance(out, list) and len(out) == 6)

# 4. no random -> all matches in order (unchanged behavior)
out = run('find picks in db.rooms\nfind: done\nshow picks\n')
check("find ... (no random) -> all 6 rows, in order",
      names(out) == ['r1', 'r2', 'r3', 'r4', 'r5', 'r6'])

print(f"\nRESULTS: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
