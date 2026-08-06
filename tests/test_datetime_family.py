# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""The datetime word family: before / after / older than / newer than / since / from.

One coherent family for time comparison. Every operator resolves its operand to a concrete point
in time (duration -> now-duration; anchor word -> that period; date literal -> itself; anchor with
a modifier like `last_month - 1 day`), then compares. English-meaning inclusivity:

    before / older  ->  strictly earlier  (<)    "before Tuesday" is not Tuesday
    after  / newer   ->  strictly later    (>)    "after Tuesday" is not Tuesday
    since  / from    ->  at-or-after       (>=)   "since/from Monday" includes Monday

Dropped for dates: is above / is below (those are numeric only; a date is never "above" a date).

This is the guard that replaces the old test_is_after_before, which asserted the OLD buggy
behavior (is after aliased to is above, doing a FLOAT comparison that silently returned False for
every date). This locks the corrected datetime semantics end to end.
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

it = MohioInterpreter()
ctx = Context()


def where_of(clause):
    src = (f'connect db as sqlite from env.DATABASE_URL\n'
           f'retrieve r from db.events\n    {clause}\nretrieve: done\ngive back 200 r\n')
    prog = transform(P.parse(src), src)
    def find(n, d=0):
        if d > 14:
            return None
        if type(n).__name__ == 'WhereClause':
            return n
        for v in (vars(n).values() if hasattr(n, '__dict__') else []):
            for x in (v if isinstance(v, list) else [v]):
                r = find(x, d + 1)
                if r:
                    return r
        return None
    return find(prog)


def cond_and_anchor(clause):
    w = where_of(clause)
    return w.condition, it._eval_filter_value(w.value, ctx)


# ── 1. every operator parses and maps to the right condition ──────────────────────────
cases = [
    ('where created is before 2026-06-01', 'before'),
    ('where created is after 2026-06-01', 'after'),
    ('where created is older than 90 days', 'older'),
    ('where created is newer than 30 days', 'newer'),
    ('created since last_month', 'since'),
    ('created from today', 'from'),
]
for clause, want in cases:
    c, _ = cond_and_anchor(clause)
    check(f"`{clause}` -> condition '{want}'", c == want, c)


# ── 2. operands resolve: duration, anchor word, date literal, modifier ────────────────
_, dur = cond_and_anchor('where created is newer than 30 days')
check("duration operand resolves to a datetime (now - 30 days)", isinstance(dur, str) and 'T' in dur)
_, anchor = cond_and_anchor('created since last_month')
check("anchor word resolves to a date", isinstance(anchor, str) and anchor.count('-') == 2)
_, datev = cond_and_anchor('where created is after 2026-01-01')
check("date literal resolves to itself", datev == '2026-01-01', datev)
_, modv = cond_and_anchor('created since last_month - 1 day')
_, basev = cond_and_anchor('created since last_month')
check("anchor modifier shifts the date (last_month - 1 day < last_month)", modv < basev,
      f"{modv} vs {basev}")


# ── 3. boundary inclusivity per the English-meaning rule ──────────────────────────────
A = '2026-06-01'
def m(cond, day):
    return it._row_matches({'created': day}, 'created', cond, A)

# strictly earlier / later exclude the boundary day
check("before excludes the boundary day", m('before', A) is False)
check("before keeps an earlier day", m('before', '2026-05-01') is True)
check("after excludes the boundary day", m('after', A) is False)
check("after keeps a later day", m('after', '2026-07-01') is True)
check("older == before (excludes boundary)", m('older', A) is False and m('older', '2026-05-01') is True)
check("newer == after (excludes boundary)", m('newer', A) is False and m('newer', '2026-07-01') is True)
# inclusive forward include the boundary day
check("since includes the boundary day", m('since', A) is True)
check("from includes the boundary day", m('from', A) is True)
check("since/from exclude an earlier day", m('since', '2026-05-01') is False)


# ── 4. datetime path, not numeric: a date must NOT be float-coerced ───────────────────
# (the old bug: is after -> above -> float('2026-...') fails -> everything filtered out)
check("after does a real datetime compare, not float (2026-07-10 after 2026-06-01)",
      it._row_matches({'created': '2026-07-10'}, 'created', 'after', '2026-06-01') is True)


# ── 5. compound: a datetime op in an `and` continuation ───────────────────────────────
w = where_of('where score is above 5\n    and created is after 2026-01-01')
def find_and(clause):
    prog = transform(P.parse(
        f'connect db as sqlite from env.DATABASE_URL\nretrieve r from db.events\n    {clause}\n'
        f'retrieve: done\ngive back 200 r\n'), 'x')
    out = []
    def find(n, d=0):
        if d > 14:
            return
        if type(n).__name__ == 'AndClause':
            out.append(n)
        for v in (vars(n).values() if hasattr(n, '__dict__') else []):
            for x in (v if isinstance(v, list) else [v]):
                find(x, d + 1)
    find(prog)
    return out[0] if out else None

a = find_and('where score is above 5\n    and created is after 2026-01-01')
check("compound `and ... is after` -> condition 'after'", a is not None and a.condition == 'after',
      a.condition if a else 'NONE')


# ── 6. string after/before still slice (no collision regression) ──────────────────────
def run(src):
    b = MohioInterpreter().run(transform(P.parse(src), src)).get('body')
    return b.to_python() if hasattr(b, 'to_python') else b
check("string `after` still slices", run('hold d "u@x.com" after "@"\ngive back 200 d\n') == 'x.com')

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
