<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Upgrading to 6.0.4

**One breaking change: every datetime a program shows or sends is now UTC, written `+00:00`.** Read it if your app declares a
`timezone`, or if a client of yours parses a datetime. A second change, to what a zoned app stores, and a third, to what an outbound send
refuses, are under it. Every item was established by running it; the batteries that hold each one are named in
`Docs/design/BACKLOG-COMPLETED.md`.

**Most 6.0.3 programs upgrade without source changes, but three behaviours changed; review the breaking changes above before you upgrade.** This release adds three advisory notices at
`mio check`, a serve-startup cache, a keyring for anchor verification, a per-request deadline with a
clear `504`, and two served-runtime fixes. Nothing here refuses a program that 6.0.3 accepted, with one
narrow exception called out below (`debug.log "text"`). Every item was established by running on the
6.0.4 build.

## Breaking changes

1. **Every datetime leaves a program as UTC with `+00:00`, in one form, on every surface.** It used to go out three ways for one
   moment: the app zone with its offset (`show`, `&`, `{{ }}`, `render`), UTC with `+00:00` (JSON), and the stored UTC clock reading with
   nothing to say so (`give back [200] stamp`), which a reader could not tell from a local time.
   - Before, under `timezone "Pacific/Kiritimati"`: `show stamp` printed `2026-10-15T12:30:00+14:00`, `give back [200] stamp` answered
     `2026-10-14T22:30:00`, and `give back [200] stamp as json` answered `2026-10-14T22:30:00+00:00`.
   - After, for all three, in `mio run` and through `mio serve`: `2026-10-14T22:30:00+00:00`. The moment is the same.
   - Also moved: `show`, `&`, `{{ }}` and `render` in any app that declares a zone, a list of datetimes (`stamps as list e.ts`, `.first`,
     `.last`, `.position.N`, `repeat each`), a task's result, the casts `as.text`, `as.uc` and `as.json`, an outbound `header` and body.
     This supersedes the app-zone display ruled 2026-10-15.
   - What the journey's `timezone` still does: it reads text with no offset, on a write and in a match, and it sets the days of `today`,
     `this_month` and `is.in`. It no longer changes how a datetime reads on the way out.
   - The fix: a client that parses with an offset-aware parser needs nothing. A page that showed people their local time should say so:
     `show stamp in.timezone "Pacific/Kiritimati"` still prints `2026-10-15T12:30:00+14:00`, and `in.timezone` is the one way to show one.

2. **In an app with a `timezone`, a write now stores the moment the program holds.** Three silent wrong answers are gone.
   - Before: `ts now()` stored the wrong moment, moved by the zone's offset, and so did copying a stored datetime
     (`ts other.ts`), again on every copy. `update` and `modify` read text with no offset as UTC, while `save` read it in the
     field's zone.
   - After: a datetime value keeps its moment on every write verb, and text with no offset is read in the field's zone on
     every write verb.
   - The fix: new writes need nothing. Rows an older build wrote stay as they were written, and nothing can tell a moved row
     from a correct one, so no migration is offered. An app whose journey declares no timezone, or declares UTC, was never moved. If
     yours declares another zone and wrote `now()` or copied datetimes, compare a recent row with when it was really written, and
     correct what you can from another record of it.

3. **An untagged datetime for the same moment as a protected one refuses an outbound send.** The outbound guard looks for the text of
   any protected value the request has read. A datetime now reads one way everywhere, so a `[pii]` or `[phi]` datetime and an untagged
   datetime for the same moment are the same text.
   - What it does: once a request has read `person.seen_at` (`[pii]`, `2026-10-14T22:30:00+00:00`), a `miohttp.post` whose body holds an untagged
     `event.at` for that same moment refuses with `would send protected data`, on its own or inside a longer text. An `event.at` one second
     later is sent.
   - **Datetimes are where you should expect this.** Two unrelated events sharing a timestamp is ordinary, so this guard will refuse
     legitimate sends more often for datetimes than for any other value. It fails loud and on the safe side, and nothing protected is sent.
     Watch for it on any route that reads a protected datetime and then sends one of its own. It is tracked as
     `T2-OUTBOUND-TAINT-DATETIME-FALSE-REFUSAL`, to be measured on a real outbound money path and narrowed if it blocks a legitimate send.
   - The fix today: send the record's own field in a request that did not read the protected one, or leave the time out of what you send.

## New advisories at `mio check`

Each is an advisory warning, ergonomic rather than governance, and each is suppressible by code in the
journey with `suppress <code> reason "..."`. None of them refuses a program; they name a cost or a
latent refusal before it bites.

### 1. A single-row read whose match is not a key (`single_read_not_unique`)

`retrieve`, `grab` and `get` each read exactly one record and refuse at run when their match finds more
than one row (the 6.0.3 change). `mio check` now names every such read whose `match` is not held to one
row by a declared key, so you see the latent refusal before a duplicate triggers it:

```mohio
connect db from env.DATABASE_URL
shape Player
    players as table
        team as text
        name as text
shape: done
retrieve p from db.players
    match team to "red"
retrieve: done
show p.name
```

```
! `retrieve p from db.players` matches `team`, and no key this program declares for db.players is held
  to one row by that match. A single-row read refuses at run when its match finds more than one row, so
  the first duplicate stops this request. Keys declared for db.players: id.
    Match a key so only one row can match (`match id to ...`), or declare the field unique where the
    table is described [...]
    To ask whether such a row exists, use `check exists p in db.players` with the same match. To read
    every row it matches, use `find p in db.players`.
    see getmohio.com/codes/single_read_not_unique
```

The fix is one of: match a key, declare the field `unique` on its `as table` field, or switch verb,
`check exists` for a yes-or-no and `find` for the group.

### 2. A find whose filter runs in Mohio, not the database (`find_filter_runs_in_app`)

The pushdown invariant says a relational query must not grow server memory with the table when the
database can run it. `mio check` now names each `find` whose filter the database cannot run, so every row
is read into the server to answer it and the time grows with the table. The notice names the filter, why
the database cannot run it, and the fix:

```mohio
connect db from env.DATABASE_URL
shape Doc
    docs as table
        title as text
shape: done
find hits in db.docs
    where title is at least 3 characters
find: done
show hits.count
```

```
! Line 6: `find hits in db.docs` runs this filter in Mohio, not in the database, so every row of
  db.docs is read to answer it and the time grows with the table. Declare the field's type where the
  table is described, or narrow the find with a filter the database runs:
    `title is at least N characters`: `title` is declared `text`, and the database runs
    `is at least N characters` only on no declared type.
    see getmohio.com/codes/find_filter_runs_in_app
```

The fix is to declare the field's type in an `as table` shape, or to narrow the find with a filter the
database runs (an equality match, or a range on a declared number).

Its runtime companion needs no change on your part: an unordered `find` with a filter the database CAN
run now asks the database for only the first N rows when you write `up to N`, rather than reading the
whole table and slicing in the server. A `find ... up to 20` on a declared column stays flat as the
table grows.

## The serve-startup verdict cache

Starting `mio serve` re-runs the whole verdict for every page, which is nearly all of a warm start once
the parse is cached. 6.0.4 keeps a CLEAN verdict (no errors) beside each page, as `<page>.mho.verdict`,
and the next start reuses it when nothing it was built from has changed. The serve banner names how many
pages reused their verdict.

A verdict is keyed on everything a stale clean verdict could hide a change in: every compiler module, the
grammar, the files under `mohio_data`, and the Python and Lark versions; every `.mho` under the
application root, and the `journey.mho` in the page's folder and in every folder above it; every sector
profile on the search path and every langmap pack a page resolves against; and any other file the
compiler read and any environment variable it read (kept as a hash). Two scanners read the moment rather
than the source, so they run again on every start, cached verdict or not: the webhook secret rotation
check and the certified-sector requirement. Declarations run again too, because they open the database.

Turn the cache off with the environment:

```bash
MOHIO_VERDICT_CACHE=off mio serve app.mho
```

## The anchor-verification keyring (`--verify-sig`)

A tenant that rotates its anchor signing key ends up with one anchors file whose early anchors were
signed by the old key and whose later ones were signed by the new key. `mio audit verify-anchors`
`--verify-sig` now takes a keyring, so such a chain verifies in one run:

```bash
mio audit verify-anchors app.db --anchors anchors.json \
  --verify-sig old.pub --verify-sig new.pub
```

`--verify-sig` may be given more than once, and a single key file may hold several keys (PEM blocks one
after another, or one raw key per line). Each anchor is verified only under the key its `key_id` names,
and only that one, so an anchor is never blessed by a key that happens to verify it but is not the key it
names. An anchor whose `key_id` names no key in the ring, or whose signature does not hold under its own
key, is reported `MALFORMED-SIG`. Truncation and a repointed head keep their own labels through a
keyring. A keyring holding a private key, a line that is not a key, or nothing at all is refused with exit
code 3.

## A per-request deadline and a clear `504`

`mio serve` runs every handler under the request's own deadline, counted from when the request arrived.
The limit is `MOHIO_REQUEST_TIMEOUT`, in seconds, default 30; `0` means no limit:

```bash
MOHIO_REQUEST_TIMEOUT=10 mio serve app.mho
```

Past the deadline the request answers `504` and the message says what was kept, never guessing: a write
that committed before the deadline stands and the `504` does not claim it was undone; a write after a
blocked call never happens; a two-write handler whose last line is the blocked call commits nothing and
its `504` says the request's transaction is rolled back; and a request still waiting behind another at its
deadline never runs, logged as never-run rather than under the other request's reference. The two
situations read differently in the message: one waited behind another request, the other passed its limit
on a call it made.

A database statement has the same limit. Before 6.0.4, `MOHIO_DB_STATEMENT_TIMEOUT_MS` was off unless you set it, so a
statement could run or wait on a lock for as long as it liked, and every other request queued behind it. Under
`mio serve` it now defaults to `MOHIO_REQUEST_TIMEOUT`. A statement past the limit is cancelled by the database. On a
regulated route with an `Idempotency-Key`, the retry does the work once. `mio run` and everything else stay off unless
you set it.

If something of yours legitimately runs longer, set the setting. It is an ordinary environment value.

```bash
MOHIO_DB_STATEMENT_TIMEOUT_MS=120000   # two minutes, for every statement in this process
MOHIO_DB_STATEMENT_TIMEOUT_MS=0        # no limit, as before 6.0.4
```

What to look for: a large index built while `mio serve` starts, and a long scheduled job. Both run under `mio serve`
and both are subject to the limit. A migration is better run outside `mio serve`. What the limit covers depends on the
engine, and SQLite has none: see "Operational limits you can set" in `Docs/guides/running-mohio-on-your-database.md`.

## `debug.log` under `mio serve`

A served program's top-level `debug on` now runs, and `debug.log` in a handler writes its trace to one
`journey.log`, in order, exactly as `mio run` already did. Before 6.0.4 a served handler's `debug.log`
answered the request and wrote nothing, with no warning:

```mohio
debug on

connect db from env.DATABASE_URL

shape Note
    body as text required
shape: done

listen for sh.Note at /note
    require role "viewer"
    seen request.body
    debug.log seen
    give back [200] "ok"
listen: done
```

A sibling found in the same fix is the one narrow refusal in this release: `debug.log "text"`, a value
written in place, now refuses at check rather than writing an empty entry:

```
x `debug.log` records one variable or a dotted field (`debug.log total`, `debug.log user.email`), so it
  has nothing to record from a value written in place, and the line would write an empty entry to the
  debug log.
    see getmohio.com/codes/debug_log_needs_a_name
```

Write `debug.log <name>` with the variable or dotted field whose value you want in the trace.

## The native form now reaches an idempotent route

Mohio's own `{{ form sh.X }}` now posts under a governed `Idempotency-Key` on its own, so a plain
rendered form reaches an idempotent money route without hand-written MioScript. This replaces the 6.0.3
known limitation. See Chapter 44, "A plain browser form reaches an idempotent route", for the full
account and the scripts-off fallback.

## Behaviour changes

1. **A value of `MOHIO_DB_STATEMENT_TIMEOUT_MS` that is not a whole number refuses.**
   - Before: `MOHIO_DB_STATEMENT_TIMEOUT_MS=30s` read as no limit, so a typo turned off the guard you meant to set.
   - After: `mio serve` refuses to start, on every engine, and `mio run` refuses when it connects to Postgres, MySQL or
     Oracle, naming the setting and showing the form to write.

2. **A cancelled write answers 504, not 500.** A caller or a monitor that treats any 5xx alike sees no difference. One
   that matched the old 500 on a database timeout now sees `{"error": "statement_timeout", "message": ..., "reference": ...}`
   with status 504.

3. **A sector app on Oracle no longer logs false audit alarms.** Before this each audit record on Oracle was written and
   then reported as "NOT in the trail", because the grade check did not know the Oracle driver. The records were there.
   If you run a sector app on Oracle and saw those lines, the rows are in `data_audit_log` and `security_audit_log`,
   and the log lines `[audit] DEGRADED` now appear in place of those failures, as they do on Postgres and MySQL.

4. **A protected datetime reads back as its value.** A `[pii]` or `[phi]` field declared `datetime` read back as
   `enc:v2:...` since 6.0.0, and a program showed that, or saved it again sealed twice.
   - After: a `[pii]` one reads as UTC with `+00:00`, and a `[phi]` one is masked. It reads as text with its offset, because a masked
     value cannot be a date, so comparing it with a date refuses, as it refused before. It stays sealed at rest with its blind index,
     and a range on it refuses (`encrypted_field_not_rangeable`), as designed.
   - A sealed value an older build wrote with no offset reads as UTC, and may be off by the zone's offset. One written with an
     offset, or in an app with no zone, is right.

5. **The block form of `give back` answers a record of its lines.** Before, `give back` with `name value` lines under it
   answered the host language's text for its first line (`FieldValue(line=26, ...)`) with a 200, and dropped the rest.

6. **`as.text` on a list or a record reads as `show` reads it**, never the host language's form of it
   (`{'id': 1, 'ts': MohioValue(...)}`).

7. **An equality on a datetime column finds the moment, however it is written.** `match ts to "2026-10-14T22:30:00Z"` found nothing
   against the row stored from that very text, and neither did `+00:00` or another offset, with no error, on a plain column and on a
   protected one. Handing a program its own answer back found nothing either. Now every form of the moment matches, through `retrieve`,
   `find`, `grab`, `check count`, `update`, `save or update`, `remove` and `cm.purge`. Text with no offset is read in the field's zone, as a
   write reads it. A `text` column is still compared as text. A date alone is left as it was.

8. **`mio audit read` says when.** It printed each record's event and left the time out. Each line now starts with it. The time in the
   trail itself was never sealed: a `[phi]` datetime is encrypted in its own table, and the audit record of the write stays plain UTC text.

9. **A MongoDB operation now has the request's limit under `mio serve`.** It is the same setting, through the driver's `timeoutMS`. A stuck
    operation answers a 504 and the next request runs. When the database never answered, the answer says the write's outcome is not known,
    because it can still land afterwards: measured, a write held in the network landed after its caller had the 504. Check before sending
    it again. MongoDB has no `once per` yet to make a resend safe.

10. **On MongoDB, a declared key is in place before a collection's first document.** Before, only the process that made the collection
    built its key. A second server writing in the gap stored a document with no key, and a deployed app's request in the gap answered a
    500 naming a migration nobody needed. Now every writer builds the key on a collection still empty, in a deployed app too: an empty
    collection has nothing to review. A collection that already holds documents and no key still refuses and names `mio migrate add-unique`.

11. **`failure.message` no longer carries the database's words for a duplicate or a stopped statement.** A program answering with
    `give back [409] failure.message` sent the driver's sentence to the caller: the table and the index, and on MongoDB the value. It now
    reads as the uncaught path does (``a record with this code already exists; `code` is unique.``). `failure.kind` is unchanged. A program
    that matched on the driver's text should match on `failure.kind`.

12. **A handler's `respond as` sets its own answer only, and a top-level one now applies under `mio serve`.** Before, one request to a
    handler with `respond as "text/plain"` changed every later answer of every other handler to text/plain, with no error. Now it
    stays with its request. A `respond as`, `title`, `describe` or `image` at the top of a served file did nothing at all; each now
    applies to every answer. **Check a served app with one of those four at the top of the file**: the type of its answers, or the
    head of its pages, changes with this release. A journey's own `title` is unchanged.

13. **Reading a decision where it did not run names the decision.** `riskCheck.confidence` read on a path where `ai.decide riskCheck`
    did not run used to refuse as an undeclared variable, with "define riskCheck first" as the fix. That fix would hide the decision.
    It is still a 500, and the server log now says `ai.decide riskCheck` did not run on this request.

14. **A served app gives back the database connections its startup used.** Before, on Postgres, MySQL and Oracle, each
    served page kept one connection from startup for as long as the server ran, so its first request and its background
    audit relay each opened another. With `MOHIO_DB_POOL_MAX=1` every page answered 500 once the pool timeout passed,
    and at the default a large regulated directory could use every connection its database allows. 6.0.3 has the same
    fault. Now a served directory rests at about one connection per page. **Size the database for a directory app**:
    each page file still has a pool of its own, so under load the total can reach the number of pages times
    `MOHIO_DB_POOL_MAX`. Allow for that, or lower `MOHIO_DB_POOL_MAX`, until pages share one pool.

## What did not change

**Power-loss durability did not change on any SQLite program.** A program that declares a sector fsyncs every commit,
and under `mio serve` its checkpoint now runs on a background thread, so no request waits for it. A program with no
sector does not fsync its commits, so its checkpoint is what makes them survive a power loss: it stays in the commit,
and such a program keeps its occasional 200 to 700 ms stall. There is no setting to defer it, on purpose.
