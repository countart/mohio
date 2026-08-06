# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""`<field> since <anchor>` time filter (OQ-004).

Walk-By: "created since last_month" -- keep rows whose <field> is at or after the anchor, up to
now. Inclusive of the anchor moment (>=). Explicit field only (no column guessing). The range end
is always now(); a bounded A-to-B range is `between`, a different clause.

This proves the full chain at the layers it owns:
  1. grammar     -- `created since <anchor>` parses for every anchor kind
  2. transformer -- produces WhereClause(condition='since', field, anchor)
  3. resolution  -- the anchor resolves to a concrete datetime via the same path post_filters uses
  4. semantics   -- _row_matches('since') keeps >= anchor, inclusive, datetime-aware, excludes
                    unparseable/missing
Filtering is verified at the _row_matches level (the proven-reliable pattern used by
test_retrieve_modifiers for retrieve behavior), not through a single-program save+retrieve round
trip (SQLite :memory: gives each connection its own db, so that harness shape is unreliable for ALL
retrieves, not just since).
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter, Context

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


def where_of(anchor):
    src = (f'connect db as sqlite from env.DATABASE_URL\n'
           f'retrieve r from db.events\n    created since {anchor}\n'
           f'retrieve: done\ngive back 200 r\n')
    prog = transform(P.parse(src), src)
    def find(n, d=0):
        if d > 14:
            return None
        if type(n).__name__ == 'WhereClause':
            return n
        for v in (vars(n).values() if hasattr(n, '__dict__') else []):
            for it in (v if isinstance(v, list) else [v]):
                r = find(it, d + 1)
                if r:
                    return r
        return None
    return find(prog)


# ── 1. grammar: every anchor kind parses ──────────────────────────────────────────────
for anchor in ['last_month', 'last_week', 'last_year', 'yesterday', 'this_year',
               'now() - 30 days', '2026-01-01']:
    try:
        where_of(anchor)
        check(f"grammar: `created since {anchor}` parses", True)
    except Exception as e:
        check(f"grammar: `created since {anchor}` parses", False, str(e)[:60])


# ── 2. transformer: WhereClause(condition='since', field, anchor) ──────────────────────
w = where_of('last_month')
check("transformer: condition is 'since'", w.condition == 'since', w.condition)
check("transformer: field is 'created'", w.field == 'created', w.field)
check("transformer: anchor node carried in value", w.value is not None)


# ── 3. resolution: anchor -> concrete datetime via post_filters' own path ─────────────
it = MohioInterpreter()
resolved = it._eval_filter_value(w.value, Context())
check("resolution: last_month resolves to an ISO date string",
      isinstance(resolved, str) and resolved.count('-') == 2, repr(resolved))


# ── 4. semantics: _row_matches('since') is inclusive, datetime-aware ──────────────────
anchor = '2026-06-30'
check("row AFTER anchor is kept (2026-07-10 >= 2026-06-30)",
      it._row_matches({'created': '2026-07-10'}, 'created', 'since', anchor) is True)
check("row BEFORE anchor is dropped (2026-01-01 < 2026-06-30)",
      it._row_matches({'created': '2026-01-01'}, 'created', 'since', anchor) is False)
check("row ON anchor is kept (inclusive >=)",
      it._row_matches({'created': '2026-06-30'}, 'created', 'since', anchor) is True)
check("row with datetime (not just date) compares correctly",
      it._row_matches({'created': '2026-06-30T12:00:00'}, 'created', 'since', anchor) is True)
check("missing field value is excluded (cannot be shown in range)",
      it._row_matches({'name': 'x'}, 'created', 'since', anchor) is False)
check("unparseable field value is excluded",
      it._row_matches({'created': 'not-a-date'}, 'created', 'since', anchor) is False)
# since must NOT do a float comparison (the old above-path trap): a date must not be coerced to num
check("since does NOT float-coerce (real datetime path, not numeric)",
      it._row_matches({'created': '2026-07-10'}, 'created', 'since', '2026-06-30') is True)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
