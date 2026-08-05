#!/usr/bin/env python3
# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
Lock tests for `find` — querying, offset pagination, skip, and accessors.

`find` is the multi-row query path (lists, feeds, tables). Verified here:

  Querying
  1. .count over all / filtered rows
  2. where <field> is <value>  filtering
  3. order.up by <field> / order.down by <field>
  4. up to N  (limit)
  5. .first / .last / .first.<field>  accessors

  Offset pagination  (paginate by PAGE, with up to N as page size)
  6. .page.current / .page.total / .page.has_more / .page.count
  7. .page.next / .page.prev
  8. page slice is correct (page 2 of an ordered set)
  9. partial last page returns the remainder

  Skip
  10. skip N applies a leading offset (after ordering)

  Cursor
  11. cursor pagination fails loud (designed, not yet wired) — never a silent
      half-result

  Backward compatibility
  12. a plain find (no pagination) is unchanged
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault('DATABASE_URL', ':memory:')

from pathlib import Path
from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter, Context, DbRuntime, _Raise

_raw = Path('mohio.lark').read_text()
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)
H = 'connect db as sqlite from env.DATABASE_URL\n'

PASS = FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  PASS  {name}")
    else:    FAIL += 1; print(f"  FAIL  {name}")

def fresh():
    db = DbRuntime(':memory:')
    db.conn.execute("CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT, category TEXT, price INTEGER)")
    db.conn.executemany("INSERT INTO products(name,category,price) VALUES (?,?,?)",
                        [(f"P{i:02d}", "books" if i % 2 else "toys", i * 5) for i in range(1, 26)])
    db.conn.commit()
    it = MohioInterpreter(); it._db = db
    return it

def run(prog):
    it = fresh(); it.shown = []
    t = transform(P.parse(H + prog), H + prog); it.run_declarations(t); it.run(t)
    return it.shown

# ── Querying ──────────────────────────────────────────────────
check("count over all rows", run('find x in db.products\nfind: done\nshow x.count\n') == [25])
check("where filter narrows the set",
      run('find x in db.products\n    where category is "books"\nfind: done\nshow x.count\n') == [13])
check("order.up sorts ascending",
      run('find x in db.products\n    order.up by price\nfind: done\nshow x.first.price\n') == [5])
check("order.down sorts descending",
      run('find x in db.products\n    order.down by price\nfind: done\nshow x.first.price\n') == [125])
check("up to N limits the result",
      run('find x in db.products\n    up to 10\nfind: done\nshow x.count\n') == [10])
check(".last accessor",
      run('find x in db.products\n    order.up by price\nfind: done\nshow x.last.price\n') == [125])

# ── Offset pagination ─────────────────────────────────────────
PG = 'find x in db.products\n    order.up by price\n    up to 10\n    paginate by {n}\nfind: done\nshow x.page.{k}\n'
check("page.current reflects the page", run(PG.format(n=2, k='current')) == [2])
check("page.total counts all pages", run(PG.format(n=1, k='total')) == [3])     # 25 / 10 -> 3
check("page.has_more true on page 1", run(PG.format(n=1, k='has_more')) == [True])
check("page.has_more false on last page", run(PG.format(n=3, k='has_more')) == [False])
check("page.count is the full total", run(PG.format(n=1, k='count')) == [25])
check("page.next points to the next page", run(PG.format(n=2, k='next')) == [3])
check("page.prev points to the previous page", run(PG.format(n=2, k='prev')) == [1])
check("page 2 slice is correct (ordered)",
      run('find x in db.products\n    order.up by price\n    up to 10\n    paginate by 2\nfind: done\nshow x.first.price\n') == [55])
check("partial last page returns remainder",
      run('find x in db.products\n    up to 10\n    paginate by 3\nfind: done\nshow x.count\n') == [5])

# ── Skip ──────────────────────────────────────────────────────
check("skip N applies a leading offset (after order)",
      run('find x in db.products\n    order.up by price\n    skip 5\nfind: done\nshow x.first.price\n') == [30])

# ── Cursor fails loud (direct exec so the raise propagates) ───
def cursor_fails():
    it = fresh()
    src = 'find x in db.products\n    up to 5\n    cursor from request.cursor\nfind: done\n'
    node = transform(P.parse(src), src).statements[0]
    ctx = Context(); ctx.set_connection('db', it._db)
    try:
        it._exec(node, ctx); return False
    except _Raise as e:
        return e.error_name == 'cursor_pagination_unavailable'
check("cursor pagination fails loud (not yet wired)", cursor_fails())

# ── Backward compatibility ────────────────────────────────────
check("plain find is unchanged", run('find x in db.products\nfind: done\nshow x.count\n') == [25])
check("where + order + limit still composes",
      run('find x in db.products\n    where category is "toys"\n    order.down by price\n    up to 3\nfind: done\nshow x.first.price\n') == [120])

print(f"\nRESULTS: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
