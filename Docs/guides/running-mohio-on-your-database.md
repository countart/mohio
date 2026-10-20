<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
<!-- Every number and name below is cited to the source file and line it comes from, in a comment beside it. -->
# Running Mohio on your database

Written for the person deciding whether to let a Mohio application connect to a database your institution
runs: a CTO, an architect, or the DBA who will grant the account and watch the sessions. It answers the
questions that decision turns on, with the real numbers from the compiler, not estimates. Mohio runs on
SQLite, PostgreSQL, MySQL/MariaDB, MongoDB, and Oracle (supported); SQL Server is on the near-term
roadmap. Oracle is covered in the most detail here, because that is where the questions are sharpest.

A Mohio application is a program that runs and answers requests; it is not files copied onto a server. So
what reaches your database is a running process holding database connections, creating a small set of
tables, reading and writing rows, and writing an audit trail. The sections below are exactly those things.

---

## 1. Connections and sessions

A served Mohio app uses a connection POOL: one connection is leased per request and returned at the end of
that request, so the number of connections open at once is bounded by the pool size, not by traffic.

<!-- pool defaults: mohio_interpreter.py:2727-2731 (_pool_settings) -->
| Setting | Environment variable | Default |
|---|---|---|
| Minimum pooled connections | `MOHIO_DB_POOL_MIN` | 1 |
| Maximum pooled connections | `MOHIO_DB_POOL_MAX` | 10 |
| Wait for a free connection (seconds) | `MOHIO_DB_POOL_TIMEOUT` | 10 |

When every connection is in use and the wait times out, Mohio does not queue forever or open an
unbounded number of connections; it refuses the request and names the knob to raise:
<!-- exhaustion refusal: mohio_interpreter.py:2805-2810 -->

> database connection pool exhausted: all N connections [in use] ... Raise `MOHIO_DB_POOL_MAX` (currently
> N), or reduce [concurrency].

So the most connections one app instance will hold against your database is `MOHIO_DB_POOL_MAX` (default
10). The pool backs PostgreSQL and MySQL today.

**Oracle (now pooled).** The Oracle backend is pooled, with the same lease discipline as PostgreSQL and
MySQL: each worker thread leases a connection from an `oracledb` session pool, holds it for the request so a
transaction stays on one connection, and returns it at the request boundary. The pool holds up to
`MOHIO_DB_POOL_MAX` connections per process (times the number of machines you run).
<!-- Oracle pool, per-thread lease, MOHIO_DB_POOL_MAX per process: mohio_interpreter.py:4866-4871 -->
Two Oracle-specific sessions sit beside those: at most ONE relay-lock session per process (the dedicated
audit-relay claim, section 7), and one DDL session per THREAD, used only when that thread runs DDL (creating
a table) inside an open transaction, which is rare (Oracle commits DDL implicitly, so that work is isolated
on its own connection, section 6). Both are closed at the request boundary or at close().
<!-- relay-lock session per process + DDL session per thread: mohio_interpreter.py:4871, 4926, 5043 -->
<!-- DDL sibling connection: mohio_interpreter.py:4973-4986 -->

**A worked example a DBA can size against (SQL engines today):**

    connections a DBA should allow
      = (app instances) x MOHIO_DB_POOL_MAX
      + (per app instance, Oracle only) 1 relay-lock session + up to 1 DDL session per thread (rare)

So four instances of an app at the default pool size need room for 4 x 10 = 40 pooled connections. On
Oracle, add one relay-lock session per instance, and allow for a brief DDL session on a thread that creates
a table inside a transaction. The pool math itself is the same on Oracle as on PostgreSQL and MySQL.

---

## 2. Privileges the database account needs

The account Mohio connects with needs ORDINARY table privileges: create its own tables, and read and write
rows in them. It does not need anything beyond that.

- **No lock-package grant.** On Oracle, Mohio does not use `DBMS_LOCK`, so no grant on it is required.
- **No access to `v$` views.** The one thing Mohio needs to learn at connect, Oracle's effective identifier
  length limit, it probes with an ordinary statement (a column alias) rather than reading `v$parameter` or
  `v$version`, because those system views are often not granted to an application account.
  <!-- v$ not needed, alias probe: mohio_interpreter.py:4937-4951 -->

Give the account a schema of its own and the ability to create and use tables in it; that is the whole
requirement.

---

## 3. What Mohio creates in your schema

Mohio creates only what it needs, and only when it first needs it (tables are created on first use, not up
front):

- **Application tables** -- one per table your program writes to (`save to db.orders` creates `orders` on
  first write). These hold your data.
- **Audit-log tables** -- one per named audit log your program declares (`ai.audit to fraud_audit_log`
  creates `fraud_audit_log`). <!-- named audit-log tables: mohio_interpreter.py:6499-6500 -->
  These hold the hash-chained audit trail (section 7).
- **Audit envelope tables** -- the delivery record that lets an owed audit record be recovered and
  delivered exactly once (section 7). <!-- envelope machinery: mohio_interpreter.py:4870, 13424 -->
- **On Oracle, a relay-lock table** -- `mohio_audit_relay_lock`, a single-row table -- so one process at a
  time runs the audit relay: a process takes its row `FOR UPDATE NOWAIT`, and a process that cannot get the
  lock skips rather than waits, so owed audit records are delivered once and never duplicated across
  processes. <!-- mohio_audit_relay_lock + FOR UPDATE NOWAIT: mohio_interpreter.py:5092, 5074 -->

Mohio does not touch tables it did not create, and it does not require any shared or system schema.

---

## 4. How data is stored today

Be aware of this before you connect a reporting tool: **a table a shape declares is stored with real typed
columns; a table no shape describes is stored as TEXT.** When a shape declares a table (a `<name> as table`
block of typed fields), Mohio creates native columns, so a reporting tool's `SUM()` sees a number. When a
program saves to a table no shape declares, Mohio has no types to use and stores TEXT, casting back on read.

On SQLite (verified by running): `int` and `boolean` are INTEGER, a currency field is a scaled INTEGER, and a
declared-scale decimal (`dec.N`) and a `datetime` are exact TEXT holding the value at its scale. On the server
engines the column is the engine's native type: on Postgres `int` is INTEGER, `boolean` is BOOLEAN, and currency
and any decimal are an exact NUMERIC.

**Migrating existing text tables:** a database created before typed storage holds TEXT columns. Convert them with
`mio migrate typed-storage <file.mho>` (`--dry-run` lists the plan and every value that will not convert, and
changes nothing). See the typed-storage section of `Docs/manual/mohio-manual-part9-ch37-sqlite-to-postgres.md`.

**Money is exact, and the scale is the currency's.** Amounts are stored and quantized to each currency's
decimal places, so no floating-point drift. The Gulf and North-African dinars are THREE-decimal
currencies, and Mohio stores them at three places:
<!-- three-decimal currencies: mohio_interpreter.py:22371-22378 -->

| Currency | Places |
|---|---|
| BHD, KWD, OMR (and the other ISO-4217 three-minor-unit currencies) | 3 |
| USD, EUR, GBP and the rest | 2 |

The full currency set and its formatting are in the money chapter
(`../Docs/manual/mohio-manual-part2-money.md`).

---

## 5. How Mohio reads and joins your data

This is what a DBA watching the query log will see Mohio do, and what it does NOT do.

**A join runs as ONE query, not one per row.** When a `find` pulls a parent and its related rows (a `with` or
`with.required` block), Mohio pushes the join down as a SINGLE query and regroups the result in memory into
nested records: the parent once, its children as a nested list, and a LEFT-join miss as an empty child list. It
does not issue a query per parent row (no N+1). Verified by running on SQLite, PostgreSQL and MySQL; MongoDB
pushes the join down to `$lookup`. <!-- join_select: mohio_interpreter.py:1976; tests/test_battery_native_join.py -->

**A paged read and its count share ONE consistent snapshot.** A page of results and the total count are two
reads, so Mohio runs them inside one read-only snapshot per engine, which means the count cannot drift from the
page under a concurrent write. The mechanism is the engine's own, named here so a DBA can recognize it in the
log: <!-- read_snapshot: mohio_interpreter.py:1723; per-engine _begin_read_snapshot below -->

| Engine | Snapshot mechanism |
|---|---|
| SQLite | `BEGIN DEFERRED` read transaction <!-- mohio_interpreter.py:2908 --> |
| PostgreSQL | `SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY` <!-- mohio_interpreter.py:4011 --> |
| MySQL / MariaDB | `START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY` (InnoDB) <!-- mohio_interpreter.py:4706 --> |
| Oracle | `SET TRANSACTION READ ONLY` <!-- mohio_interpreter.py:6100 --> |
| MongoDB | no cross-statement snapshot; Mohio says so rather than pretending it held one <!-- base: mohio_interpreter.py:1737 --> |

**Paging is stable.** A paged query orders by your sort column and then by the record key as the FINAL
tie-breaker, so two rows that tie on the sort column do not swap between pages and no row is skipped or repeated
at a page boundary. With no sort column, the key alone gives the page a stable order.
<!-- STABLE PAGING tie-breaker: mohio_interpreter.py:2024 -->

**What runs in the database now, and what still runs in Mohio memory.** On the SQL engines (SQLite, PostgreSQL,
MySQL, Oracle) the pushdown project has landed for most of a query: <!-- PUSHABLE_OPS per engine: mohio_interpreter.py:2952,4029,4928,6306 -->

- equality filters;
- numeric RANGE and compare filters: greater-than, less-than, between, and `is not after` / `is not before` on
  numbers; <!-- _SQL_NUMERIC_PUSHABLE: mohio_interpreter.py:281 -->
- text filters: contains, starts-with, ends-with, empty, not-empty, is-not; <!-- _SQL_TEXT_PUSHABLE: mohio_interpreter.py:292 -->
- match-any and no-match; <!-- _SQL_GROUP_PUSHABLE: mohio_interpreter.py:304 -->
- ordering, paging, and projection (selecting only the fields a `return` clause names).

All of those now run in the database and no longer drag the full table into Mohio's process. What STILL runs in
Mohio memory:

- DATETIME filters, which wait on write-time UTC normalization before they can push down (`T1-DB-TYPED-STORAGE`);
- EVERYTHING on MongoDB, whose adapter pushes nothing yet, so a Mongo query is filtered and ordered in Mohio's
  process.

Where a filter still runs in memory, Mohio fetches the matching rows and does the work itself, and the fallback is
visible rather than silent. `summarize` aggregating in the database from the start is the next build after the
pushdown project.

**A comparison value is bound as the column stores it, on every query path.** A value compared with a typed column
is encoded to that column's stored form before the query runs: on the plain find, the pushed-down filter, a
`match any` or `no.match`, a join-level equality, and a join range bound. A boolean, whole number, decimal or
currency compares natively; text columns still compare as text. Where an engine stores a currency as a scaled
integer, a range bound is scaled to the minor unit. This is why a `match any` on a true-or-false column matches a
live-true row, and a currency `where is` finds its row even behind a pushed-down join, rather than silently
matching nothing (`T1-TYPED-MATCH-PUSHDOWN`).

**Operational limits you can set.** Two environment knobs, both OFF by default:
`MOHIO_FIND_MAX_ROWS` caps how many rows a find may return and refuses loud past the cap rather than streaming an
unbounded result; `MOHIO_DB_STATEMENT_TIMEOUT_MS` sets a per-statement timeout on PostgreSQL, MySQL and Oracle.
<!-- MOHIO_FIND_MAX_ROWS / MOHIO_DB_STATEMENT_TIMEOUT_MS: mohio_interpreter.py:3796 -->

**Join-field indexes.** When Mohio creates a table, it creates an index on each field used as a join target, on
every engine, so a `with` join has an index to use; the index is created only with a NEW table, never added to one
that already exists. <!-- join-field index created only with a new table, every engine -->

---

## 6. Oracle, in detail

The Oracle backend is proven against Oracle Database Free and runs in the driver's Thin mode (no Oracle
Client libraries to install).
<!-- Oracle backend, Thin mode: mohio_interpreter.py:4850-4851, 4896-4904 -->

- **Version floor: 12.1.** The Thin driver cannot talk to a server below 12.1 and raises on connect; Mohio
  turns that into a named refusal ("supports Oracle Database 12.1 and later. An older server (11g) needs
  the Oracle Client libraries") rather than a raw driver code.
  <!-- version floor message: mohio_interpreter.py:4919-4926 -->
- **Identifier length limit, detected at connect: 128, or 30 on older servers.** The real limit depends on
  the server version and the `COMPATIBLE` parameter, neither reliably readable from an ordinary account, so
  Mohio probes it with a 128-character column alias: if it parses, the modern 128 limit applies; `ORA-00972`
  means the old 30-character cap. A too-long name is then named at check, before any write.
  <!-- identifier-limit probe 128/30: mohio_interpreter.py:4937-4951 -->
- **Identifiers are unquoted (Ron's ruling).** Oracle folds unquoted names to upper case in the catalogue,
  and Mohio maps the upper-case catalogue name back to the name your program wrote, so `record.firstName`
  still works AND a DBA can query the table without quoting identifiers. A check-time refusal rejects two
  names in one shape that differ only by case (the mapping would be ambiguous) or a name over the limit.
  <!-- unquoted upper-case + case-collision refusal: mohio_interpreter.py:4855-4859 -->
- **Empty string is NULL, and Mohio keeps results identical.** Oracle stores `''` as `NULL`, and a unique
  index allows many `NULL`s, so Oracle cannot enforce uniqueness on an empty value the way the other engines
  do. Mohio refuses that case loud rather than letting duplicates through silently.
  <!-- empty string NULL + unique refusal: mohio_interpreter.py:5138-5148 -->
- **DDL commits implicitly, and transactions still stay all-or-nothing.** Oracle commits DDL (CREATE/ALTER)
  implicitly, which would commit an open transaction's pending writes. Mohio builds a needed table on a
  SEPARATE connection when a transaction is open, so the implicit commit cannot reach the main
  transaction, and the data write stays all-or-nothing (tracked as `T1-DB-ORACLE-DDL-ATOMICITY`).
  <!-- DDL on separate connection for atomicity: mohio_interpreter.py:4868-4871, 4973-4986 -->
- **It refuses rather than hangs when a transaction alters a table it is writing.** If an `ALTER` (a new
  column) were needed on the very table the open transaction holds a lock on, the separate DDL connection
  would wait on that lock forever. Mohio detects this and refuses, rather than hanging the request.
  <!-- alter-while-writing would hang, so refuse: mohio_interpreter.py:5095-5096 -->
- **The 4000-character text limit, and `text.long`.** An Oracle `VARCHAR2` value column caps at 4000
  characters; plain `text` over that refuses loud, pointing at `text.long`. `text.long` is now BUILT: unlimited
  text on every engine, stored as CLOB on Oracle, LONGTEXT on MySQL, and TEXT on SQLite and PostgreSQL. It is
  refused as a unique, exact-match, or sort key (those need a bounded value). Declare it as a field type,
  `body as text.long`. <!-- text.long built: CLOB/LONGTEXT; VARCHAR2(4000) cap on plain text: mohio_interpreter.py:4861 -->

---

## 7. The audit trail

When a program declares an audit log and writes to it, the records go into a table of that name in your own
database (section 3), not to any external service.

- **A hash chain.** Each record's hash is computed over the encoding version, the previous record's hash,
  and the record's canonical payload, so removing or altering any record breaks every hash after it, which
  is what makes the trail tamper-evident. <!-- chain hash formula: mohio_interpreter.py:14216 -->
- **Verification.** `mio audit verify` recomputes the chain and reports whether it is intact; a separate
  anchor check compares the live chain against externally held chain heads to detect truncation or wholesale
  replacement. <!-- verify_audit_chain / against_anchors: mohio_interpreter.py:13267, 13186 -->
- **The encoding version travels inside the hash.** The current version is `mohio-audit-2`, under which an
  EMPTY value and a MISSING value collapse to one canonical form and hash the SAME, so a present-but-empty
  field and an absent one cannot produce two different chain hashes for what is the same state. The version
  is carried in the hashed input, and verification checks each record under its OWN version, so records
  written under the earlier `mohio-audit-1` keep verifying.
  <!-- mohio-audit-2, empty==missing, prior-version verify: mohio_interpreter.py:13938-13947, 14484 -->
- **A relay delivers owed records.** If a record is owed after a commit, `mio audit relay` delivers it; an
  acked envelope is treated as done, so a record is delivered exactly once and not re-sent.
  <!-- relay delivery + exactly-once: mohio_interpreter.py:13355, 13424 -->

---

## 8. What happens when something goes wrong

- **No silent fallback to a local file.** If you declare a real database and its address is unset or
  unreachable, Mohio refuses to run against a throwaway SQLite instead; it does not quietly swap in a local
  file that would lose your data. <!-- no SQLite fallback: mohio_interpreter.py:7943 -->
- **An unknown engine is refused at check.** A database name Mohio does not know is caught at `mio check`,
  naming the supported engines, rather than failing at run (see `databases-supported.md`).
- **The declared environment variable is the only source.** A connection's source must be `env.X` or
  `secret.X`, for every engine; a literal connection string is refused at check as a hardcoded credential,
  so credentials live in your environment, never in the source.
- **Outbound reach to internal hosts is closed by default.** A Mohio app cannot call an internal address
  unless that host is named in `MOHIO_HTTP_ALLOW_INTERNAL_HOSTS`, so it cannot be turned against your
  internal network by a crafted request. <!-- internal-host allow-list: mohio_interpreter.py:339, 358 -->

---

## 9. Questions a decision-maker asks

**How many sessions will it open against my database?** At most `MOHIO_DB_POOL_MAX` (default 10) per app
instance, one leased per request and returned at the end, on PostgreSQL, MySQL AND Oracle (Oracle is now
pooled on the same discipline). Multiply by your instance count. On Oracle, add one relay-lock session per
instance and, rarely, one brief DDL session on a thread that creates a table inside a transaction.

**What privileges does the account need?** Ordinary table privileges in a schema of its own, nothing more.
No `DBMS_LOCK` grant, no access to `v$` views.

**Will it lock or alter my existing tables?** No. Mohio creates and uses only its own tables. It never
touches a table it did not create, and when it must alter one of its own inside a transaction it refuses
rather than risk a hang.

**What happens if the database goes down?** A write that cannot reach the database fails loud; Mohio does
not fall back to a local file and pretend it succeeded. Under a regulated sector a run of failed audit
writes is treated as an audit outage (`T1-AUDIT-WRITE-FAILED-REGULATED`), not swallowed.

**Can my reporting tools read the data?** Yes, with a native-column caveat: a table a shape declares is stored
with real typed columns, so a direct `SUM()` works; a table no shape describes is stored as text and cast on
read, so a `SUM()` on it will not see a number. Declare the table in a shape, or run `mio migrate typed-storage`
on an older text database (section 4).

**Which engines are actually supported?** SQLite, PostgreSQL, MySQL/MariaDB are shipped; MongoDB is
experimental; Oracle is supported, verified on Oracle AI Database 26ai (19c verification follows after the tag);
SQL Server is on the roadmap. See `databases-supported.md` for the current line.
