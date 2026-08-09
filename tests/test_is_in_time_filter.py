# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Section 2: `is.in <time period>` filters rows by a half-open date RANGE (end-to-end via mio run).

Before this build the whole `is.in <period>` where-clause filter parsed clean and died at runtime
("'time_period_expr' reached the value evaluator with no rule"). Now it resolves the period to a
[start, end) range (Section 1) and keeps rows whose field is in it. Seed dates are computed from
the SAME clock the interpreter anchors to (UTC by default), so the assertions are deterministic
regardless of the wall clock. Covers calendar members, the rolling `last N` window (the form
fraud_demo.mho uses), and the fail-louds. Run: `python tests/test_is_in_time_filter.py`.
"""
import os, sys, subprocess, tempfile, datetime
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV = dict(os.environ, PYTHONPATH=ROOT, DATABASE_URL=':memory:',
           PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
ENV.pop('MOHIO_APP_TIMEZONE', None)   # anchor to UTC, matching the seed clock below

UTC = datetime.timezone.utc
now = datetime.datetime.now(UTC)
today = now.date()
def iso(d): return d.isoformat()

# labeled rows at known instants (UTC), spanning the periods under test
ROWS = {
    'T':   datetime.datetime(today.year, today.month, today.day, 12, 0, tzinfo=UTC),          # today noon
    'Y':   datetime.datetime(today.year, today.month, today.day, 12, 0, tzinfo=UTC) - datetime.timedelta(days=1),  # yesterday noon
    'H1':  now - datetime.timedelta(hours=1),                                                 # 1h ago (rolling 24h)
    'H30': now - datetime.timedelta(hours=30),                                                # 30h ago (outside 24h)
    'LY':  datetime.datetime(today.year - 1, 1, 1, 12, 0, tzinfo=UTC),                        # last year
}
SEED = 'connect db as sqlite from env.DATABASE_URL\n' + ''.join(
    f'save to db.events\n    label "{k}"\n    created_at "{iso(v)}"\nsave: done\n'
    for k, v in ROWS.items())

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

def matched(period_src):
    """Return the set of labels a `where created_at is.in <period>` returns."""
    out = run(SEED + f'find rows in db.events\n    where created_at {period_src}\nfind: done\n'
                     'repeat each e in rows\n    show "ROW:" & e.label\nrepeat: done\n')
    if 'error' in out.lower() and 'ROW:' not in out:
        return f"<error: {[l.strip() for l in out.splitlines() if l.strip()][-1]}>"
    return {l.split('ROW:')[1].strip() for l in out.splitlines() if 'ROW:' in l}

# ── calendar members ────────────────────────────────────────────────────────────────────
m = matched('is.in today')
check("is.in today -> today's row, NOT yesterday/last-year",
      isinstance(m, set) and 'T' in m and 'Y' not in m and 'LY' not in m, str(m))
m = matched('is.in yesterday')
check("is.in yesterday -> yesterday's row, NOT today's",
      isinstance(m, set) and 'Y' in m and 'T' not in m, str(m))
m = matched('is.in this year')
check("is.in this year -> today's row, NOT last year's",
      isinstance(m, set) and 'T' in m and 'LY' not in m, str(m))
m = matched('is.in last year')
check("is.in last year -> last year's row, NOT today's",
      isinstance(m, set) and 'LY' in m and 'T' not in m, str(m))

# ── rolling window (the fraud_demo.mho form) ────────────────────────────────────────────
m = matched('is.in last 24 hours')
check("is.in last 24 hours -> 1h-ago row, NOT 30h-ago row (rolling, half-open)",
      isinstance(m, set) and 'H1' in m and 'H30' not in m, str(m))

# ── fraud_demo's exact pattern: an equality match AND the rolling window together ────────
h1  = (now - datetime.timedelta(hours=1)).isoformat()
h30 = (now - datetime.timedelta(hours=30)).isoformat()
FSEED = ('connect db as sqlite from env.DATABASE_URL\n'
         f'save to db.txns\n    member_id "1"\n    label "IN"\n    created_at "{h1}"\nsave: done\n'
         f'save to db.txns\n    member_id "1"\n    label "OLD"\n    created_at "{h30}"\nsave: done\n'
         f'save to db.txns\n    member_id "2"\n    label "OTHER"\n    created_at "{h1}"\nsave: done\n')
out = run(FSEED + 'find recent in db.txns\n    where member_id is "1"\n'
                  '    and created_at is.in last 24 hours\nfind: done\n'
                  'repeat each e in recent\n    show "ROW:" & e.label\nrepeat: done\n')
labels = {l.split('ROW:')[1].strip() for l in out.splitlines() if 'ROW:' in l}
check("fraud_demo pattern (member match AND is.in last 24 hours) -> only the in-window match",
      labels == {'IN'}, str(labels) + ' ' + out[-120:])

# ── fail-louds (mutation targets) ───────────────────────────────────────────────────────
_e = run(SEED + 'find rows in db.events\n    where created_at is.in this fortnight\nfind: done\n')
check("is.in this <unknown> fails loud in the transformer (never guesses)",
      'not a valid time period' in _e, _e[-160:])

# _eval_time must never silently resolve an unknown anchor to `now` (in-process unit).
from mohio_interpreter import MohioInterpreter, MohioRuntimeError
from mohio_ast import TimeExpr
_ok = False
try:
    MohioInterpreter()._eval_time(TimeExpr(base='bogus_anchor'), None)
except MohioRuntimeError as ex:
    _ok = 'not a time anchor' in str(ex)
check("_eval_time fails loud on an unknown anchor (never silently 'now')", _ok)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
