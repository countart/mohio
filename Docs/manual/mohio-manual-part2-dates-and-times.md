<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Part II -- Values, Types, and Text

## Dates and times

A `datetime` field holds a moment. You write a date as `YYYY-MM-DD`. In a `save`, the value is a string the
column coerces (`at "2020-01-01"`); in a query's `where` clause, you write the date bare (`is after 2024-01-01`).
`now()` is the current moment, and bare `now` names the current moment inside a temporal clause.

### Filtering by date in a `where` clause

Every form below was run against three events dated 2020-01-01, 2023-06-15, and 2025-12-31.

```mohio
connect db from env.DATABASE_URL
shape Event
    events as table
        title as text
        at as datetime
shape: done
save to db.events
    title "old"
    at "2020-01-01"
save: done
save to db.events
    title "mid"
    at "2023-06-15"
save: done
save to db.events
    title "new"
    at "2025-12-31"
save: done
find rows in db.events
    where at is between 2023-01-01 and 2024-01-01
find: done
repeat each e in rows
    show e.title
repeat: done
```

That prints `mid`. The dates are read as WHOLE DAYS: `is between 2023-01-01 and 2024-01-01` covers every moment
from the start of 2023-01-01 to the end of 2024-01-01, so a time-of-day on the boundary day is not accidentally
excluded.

**`is after` and `is before`.** Strictly later or earlier than the given moment.

```mohio
find rows in db.events
    where at is after 2024-01-01
find: done
```

`is after 2024-01-01` keeps `new`; `is before 2024-01-01` keeps `old` and `mid`. The right side can also be the
bare word `now`: `where at is after now` keeps rows dated in the future, `where at is before now` keeps past rows.

**`is not after` and `is not before` -- the INCLUSIVE forms.** `is not after X` means "on or before X" (X and
everything earlier); `is not before X` means "on or after X" (X and everything later). They reuse the existing
`not` / `after` / `before` words rather than adding `<=` / `>=` spellings for dates.

```mohio
find rows in db.events
    where at is not after 2023-06-15
find: done
```

Against the three events, `is not after 2023-06-15` keeps `old` and `mid` (2023-06-15 is included), and
`is not before 2023-06-15` keeps `mid` and `new` (again including 2023-06-15).

**`is.in` a period.** `is.in` (dotted) matches a moment inside a named period, rather than against a fixed date:

```mohio
find rows in db.events
    where at is.in last 30 days
find: done
```

The period after `is.in` is one of: `last N days` / `weeks` / `months` / `years`; `last week` / `last month` /
`last quarter` / `last year`; `this week` / `this month` / `this quarter` / `this year`; or `today` / `yesterday`.
The window is computed from the moment the query runs, not from a fixed date, so `is.in last 30 days` keeps only
rows dated within the thirty days before now (verified live: a row dated ten days ago matched, a row dated years
ago did not).

**`since` and `until now`.** A `since` clause keeps rows from an anchor onward, and the anchor can be `now` or a
date. It reads as a continuation of a `where`, after `and`:

```mohio
find rows in db.events
    where kind is "signup"
    and created_at since now
find: done
```

`and created_at since now` keeps rows dated at or after the present moment; `and created_at since 2020-01-01`
keeps rows from that date onward (verified by running). On a `retrieve.all` block the `since` anchor is admitted
but still blocked by the same not-yet-built retrieve-filtering deferral as `since yesterday`, so use it on a
`find` today.

`until now` closes a `timespan` at the present, so a window that starts at a fixed date and runs up to the
moment the query executes needs no second date:

```mohio
timespan window
    start 2020-01-01
    until now
timespan: done
```

### `now()` never repeats, and never goes backward

`now()` reads the current moment, and within a single run two reads are never identical and never out of order. If
the system clock has not advanced between two reads, the second comes back one microsecond after the first, so
program order is always clock order. Verified by running:

```mohio
a now()
b now()
show ("first:  " & a)
show ("second: " & b)
```

```
first:  2026-10-08T14:33:10.157096
second: 2026-10-08T14:33:10.157097
```

This matters for a query window that starts at `now()`: a row saved a moment earlier in the same run cannot land
on the exact same instant as the window's start and slip in, because the window's `now()` reads strictly later
than the write did. On Windows especially, where the system clock advances in coarse steps of about 16 ms, two
reads close together would otherwise collide on one value.

### What runs where

A declared datetime is NORMALIZED to UTC when it is written (verified by running: `2026-01-01T12:00:00+05:00` is
stored and read back as `2026-01-01T07:00:00`), so two datetimes from different zones DO compare correctly as
stored. What is still true is that date filters run inside Mohio today, not pushed down to the database:
datetime pushdown is not built yet, so these filters are evaluated after the rows are fetched, while the numeric,
text, match-any, ordering and paging filters do push down. For the full what-runs-where split, see the database
guide's "How Mohio reads and joins your data" section (`Docs/guides/running-mohio-on-your-database.md`).
