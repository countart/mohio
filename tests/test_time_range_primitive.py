# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Section 1: the time-range primitive (`_period_range` / `_rolling_range`) -- standalone proof.

A calendar/rolling period resolves to a half-open interval [start, end) of tz-aware datetimes,
anchored in the app's timezone. Rulings (Ronnie, 2026-08-01): week starts MONDAY; quarters are
REAL calendar quarters (last_quarter = the previous calendar quarter, NOT 90 rolling days);
month/quarter/year are calendar-bound; `last N <unit>` is rolling from now; an unknown period
fails loud (never guesses). This test pins a fixed anchor and checks every member + tz anchoring.

Run: `python tests/test_time_range_primitive.py`.
"""
import os, sys, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')
from mohio_interpreter import MohioInterpreter, MohioRuntimeError

IT = MohioInterpreter()
UTC = datetime.timezone.utc
def dt(y, m, d, tz=UTC): return datetime.datetime(y, m, d, tzinfo=tz)

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail: print(f"          {detail}")
    _p += bool(cond); _f += (not cond)

def rng(period, now): return IT._period_range(period, now=now)

# Anchor: Sat 2026-08-01 12:00 UTC (weekday=5, Q3, August).
NOW = datetime.datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
CASES = {
    'today':        (dt(2026, 8, 1),  dt(2026, 8, 2)),
    'yesterday':    (dt(2026, 7, 31), dt(2026, 8, 1)),
    'this_week':    (dt(2026, 7, 27), dt(2026, 8, 3)),   # Monday-start; 08-01 is Sat
    'last_week':    (dt(2026, 7, 20), dt(2026, 7, 27)),
    'this_month':   (dt(2026, 8, 1),  dt(2026, 9, 1)),
    'last_month':   (dt(2026, 7, 1),  dt(2026, 8, 1)),
    'this_quarter': (dt(2026, 7, 1),  dt(2026, 10, 1)),  # Q3 Jul-Sep
    'last_quarter': (dt(2026, 4, 1),  dt(2026, 7, 1)),   # Ruling 1: REAL Q2, not 90 days back
    'this_year':    (dt(2026, 1, 1),  dt(2027, 1, 1)),
    'last_year':    (dt(2025, 1, 1),  dt(2026, 1, 1)),
}
for period, (exp_s, exp_e) in CASES.items():
    s, e = rng(period, NOW)
    check(f"{period}: [{exp_s.date()}, {exp_e.date()})",
          s == exp_s and e == exp_e, f"got [{s.isoformat()}, {e.isoformat()})")

# Half-open: end is EXCLUSIVE (start of next period), and start <= end.
for period in CASES:
    s, e = rng(period, NOW)
    check(f"{period}: half-open (start < end)", s < e)

# Ruling 1 hard case: last_quarter from Q1 crosses the year boundary to the PREVIOUS year's Q4.
s, e = rng('last_quarter', dt(2026, 2, 15))
check("last_quarter from Q1 -> previous year's Q4 [2025-10-01, 2026-01-01)",
      s == dt(2025, 10, 1) and e == dt(2026, 1, 1), f"got [{s.isoformat()}, {e.isoformat()})")
# and it is NOT the rolling-90-days answer (which would be ~2025-11-17)
check("last_quarter is calendar-bound, NOT 90 rolling days",
      rng('last_quarter', NOW)[0] == dt(2026, 4, 1))

# Timezone anchoring: a fixed -05:00 offset shifts the day boundaries to that zone's midnight.
EST = datetime.timezone(datetime.timedelta(hours=-5))
s, e = rng('today', datetime.datetime(2026, 8, 1, 12, 0, tzinfo=EST))
check("today anchors to the app tz midnight (-05:00)",
      s == dt(2026, 8, 1, EST) and e == dt(2026, 8, 2, EST), f"got [{s.isoformat()}, {e.isoformat()})")

# Rolling window: `last 24 hours` = [now - 24h, now), independent of the calendar.
s, e = IT._rolling_range(24, 'hours', now=NOW)
check("rolling last 24 hours = [now-24h, now)",
      e == NOW and s == NOW - datetime.timedelta(hours=24), f"got [{s.isoformat()}, {e.isoformat()})")
s, e = IT._rolling_range(7, 'days', now=NOW)
check("rolling last 7 days = [now-7d, now)", s == NOW - datetime.timedelta(days=7) and e == NOW)

# An unknown period NEVER guesses -- it fails loud.
_raised = False
try:
    IT._period_range('fortnight', now=NOW)
except MohioRuntimeError as ex:
    _raised = 'not a recognized time period' in str(ex)
check("unknown period fails loud (never guesses)", _raised)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
