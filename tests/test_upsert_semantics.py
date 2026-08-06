# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""`save or update` (upsert) UPDATES an existing row, it does NOT insert a duplicate.

Locks the upsert idempotency invariant explicitly: N upserts on one match key leave ONE row holding
the last value -- exactly the semantic zork's `saved_games` relies on (one row per session,
rewritten each turn). Catches the sqlite fallback path breaking (`count = db.update(...)` -> always
insert). Proven wired: it fails under that mutation.

NOTE (Stage-6 sibling finding, 2026-07-31): the executor has TWO upsert implementations --
the NATIVE `db.upsert(...)` (Postgres/Mongo) and this update-then-insert FALLBACK (sqlite). The
`:memory:` suite only exercises the fallback, so a mutation to the native line is INERT here and the
native path -- the one that actually runs in production, incl. zork's Postgres `saved_games` -- is
NOT covered locally. A Postgres-backed upsert idempotency test is the open item; see the backlog.

Run as a script: `python tests/test_upsert_semantics.py` (exit 0 = pass).
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); os.chdir(ROOT)
import mohio_data
os.environ['DATABASE_URL'] = ':memory:'

from pathlib import Path
from lark import Lark
from mohio_interpreter import MohioInterpreter
from mohio_transformer_ast import transform as ast_transform

_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)

# Two upserts on the SAME match key (handle="neo"): the second must UPDATE the first, not add a row.
SRC = '''connect db as sqlite from env.DATABASE_URL
save or update db.players
    match handle to "neo"
    score 10
save: done
save or update db.players
    match handle to "neo"
    score 20
save: done
'''

interp = MohioInterpreter()
interp.run(ast_transform(P.parse(SRC), SRC))
tenant = interp._db

cur = tenant.conn.cursor()
cur.execute('SELECT COUNT(*) FROM players WHERE handle = ?', ('neo',))
n = cur.fetchone()[0]
check("two upserts on one match key leave exactly ONE row (update, not duplicate insert)",
      n == 1, f"row count for handle=neo is {n} (a duplicate insert would give 2)")

cur.execute('SELECT score FROM players WHERE handle = ?', ('neo',))
row = cur.fetchone()
score = int(row[0]) if row and row[0] is not None else None
check("the surviving row holds the LATEST upserted value (score 20)", score == 20,
      f"score is {score!r}, expected 20")

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
