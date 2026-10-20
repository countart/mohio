<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Databases in Mohio: what's supported and how to connect

You declare a connection once, at the top of a file:

```
connect db from env.<VAR>
```

`db` is the name you'll use later (`save to db.users`, `retrieve.all rows from db.users`).
With no `as`, Mohio reads the engine FROM THE ADDRESS the `env.<VAR>` holds (a `postgres://`
URL connects Postgres, a file path connects SQLite), so one line moves from development to
production unchanged. This adaptive form is the default to teach and write. The `env.<VAR>` holds
the connection string, so secrets stay out of your code, and the same `save`, `retrieve`,
`update`, and `remove` keywords work the same across every backend.

**Pinning the engine with `as`, and when it is the point.** You can also name the engine explicitly,
`connect db as <driver> from env.<VAR>`. An `as` that contradicts the address refuses at run rather
than connecting to the wrong database. Reach for the explicit form only when NAMING the engine is the
point (documentation that shows a specific backend, or a file that must only ever run on one). For an
ordinary app, prefer the adaptive form above. Each engine section below uses the explicit form
deliberately, to show that engine.

**Deciding whether to let Mohio onto your database?** For the architect/DBA view, how many sessions it
opens, what privileges the account needs, what tables it creates, how data is stored, and the Oracle
specifics, see **`running-mohio-on-your-database.md`**.

## Use any database you like, and compliance has a floor

Pick the database that suits you. SQLite to start, Postgres, MySQL, or MongoDB in production: the same code
runs on all of them, and Mohio does not push you toward one. That freedom has one boundary, and it is worth
meeting before you build rather than at a refusal: **compliance has a floor.** When your program runs under a
regulated sector, some of what compliance guarantees, above all a durable, tamper-evident audit trail, cannot
be delivered by a database that can lose what it wrote. So a sector sets a minimum: below the floor, a
regulated write is refused rather than accepted into storage that cannot keep the promise the sector makes.

This is a requirement with a reason, not a restriction for its own sake. An audit trail that a disk-reset can
erase is not an audit trail, and a language that let a regulated write land there would be lying about what it
guaranteed. The floor is how Mohio keeps that honest: use whatever database you like, and if you take on a
sector's obligations, the storage under them has to be able to keep them.

The same sentence, applied to a specific place, is why **getmohio.com runs on Postgres.** A host that resets
its disk on every deploy cannot carry a durable grade, so the platform that offers to run a regulated Mohio
app for you uses a database that survives, rather than one that looks fine until the first redeploy takes the
audit trail with it.

### The two axes, kept apart

The floor is really two separate questions, and merging them is how a reader wrongly concludes that switching
to Postgres makes them compliant. It does not. One axis is what your STORE PROVIDES; the other is what your
FRAMEWORK REQUIRES. You are compliant on this front only when the grade your store provides meets the grade your
framework requires. Postgres provides `durable` and no more by inspection, which meets some frameworks and not
others. Read the two axes as two columns that have to line up, never as one.

**Axis 1 -- what a sink provides** (ascending):

| Grade | What it means |
|---|---|
| `none` | No sink bound, an in-memory database with nothing that survives the process, a sink exposing no connection, or an unrecognized sink. It FAILS CLOSED: an unclassified sink is `none`, never `durable`. |
| `durable` | Records survive a restart or a sleep: a SQLite file on disk, a Postgres server, a MySQL or MariaDB server. A networked engine is `durable` and no higher. |
| `append_only` | `durable` plus rows that cannot be updated or deleted in place. |
| `worm` | `append_only` plus a storage provider that itself refuses rewrite or deletion until retention ends. |

**`durable` is the ceiling of what Mohio can observe.** Append-only and WORM cannot be reached by inspection,
because whether a bucket is in compliance mode or a role has insert-only grants is invisible from a connection
object. They arrive through a verified channel, and A STORE CANNOT GRADE ITSELF (a store asserting its own grade
has the same author for the claim and the thing claimed). So the honest answer to "how do I reach append-only"
is not a database choice alone: it is the database, PLUS the grants, PLUS verification.

**Axis 2 -- what a framework requires.** The compiler enforces the highest grade any activated framework
demands, and refuses at check or deploy when no bound sink meets it.

| Required grade | Frameworks |
|---|---|
| `durable` | glba*, gdpr, ccpa, cpra, soc2 |
| `append_only` | hipaa, hitech, pci-dss, pci, fedramp, cmmc, nist-800-53 |
| `worm` | sox, sec-17a-4, finra, bsa*, aml* |

Composing several frameworks takes the HIGHEST grade among them. An unknown framework is REPORTED, never
silently treated as "no requirement."

**\* Three of these are UNRATIFIED DEFAULTS**, stated here at the table rather than hidden in a footnote because
a reader treats a grade table as settled unless told otherwise: **glba, bsa, and aml** are a considered reading,
not a settled legal one. An environment override (`MOHIO_FRAMEWORK_GRADES`) exists precisely because they are
not ratified, so a deployment can set its own reading. The module says it in its own words, carried verbatim:
"Nothing about this module is legal advice; it is research-grade organisation."

**Tier and audit grade are independent.** They are different axes again: a community-tier profile that activates
HIPAA gets HIPAA's `append_only`, and a licensed-tier profile that activates nothing audit-bearing gets `none`.
Paying for a tier does not raise your audit grade, and running under a free tier does not lower it; the grade is
set by the frameworks you activate and the store you bind, not by what you are paying for.

## Supported backends

### SQLite (built in)
Nothing to install. Good for development and small apps.

```
connect db as sqlite from env.DATABASE_URL
```

The value can be a file path (`/data/app.db`) or `:memory:` for a throwaway
in-memory database. In-memory does not persist between restarts.

### PostgreSQL
Railway's default database. Driver: `psycopg2-binary` (in requirements).

```
connect db as postgres from env.DATABASE_URL
```

Verified backend. `postgres` and `postgresql` both work.

### Supabase
Supabase is hosted PostgreSQL, so there is nothing special to do. Use the
`postgres` driver with your Supabase connection string.

```
connect db as postgres from env.DATABASE_URL
```

Set `DATABASE_URL` to the Supabase connection string (Project settings,
Database, Connection string). No extra driver.

### MySQL / MariaDB
Driver: `pymysql` (in requirements). MariaDB is fully compatible.

```
connect db as mysql from env.MYSQL_URL
```

`mysql` and `mariadb` both work. The connection string format is
`mysql://user:pass@host:port/dbname`. Verified backend.

### MongoDB (experimental, not yet verified)
Driver: `pymongo` (in requirements). Collections act as tables, documents as
rows, and `_id` becomes `id`.

```
connect db as mongodb from env.MONGO_URL
```

`mongodb` and `mongo` both work. Treat this as experimental: it has no test
coverage yet, and its operations currently return empty on error rather than
failing loud, which can hide a problem. Do not rely on it in production until
it is tested and the error handling is brought in line with the rest of Mohio.

### Oracle (supported)
Oracle is supported, verified on Oracle AI Database 26ai; 19c verification follows after the tag. Connect with
`connect db as oracle from env.X`. The data verbs, the connection pool and the audit trail all run on Oracle with
the same guarantees as the other shipped engines.

## What `connect` checks at build time

Two things are enforced at `mio check`, not left to fail at run:

- **The engine must be one Mohio knows.** An unknown engine name is refused at check, naming the
  supported ones (verified: `connect db as banana ...` gives "'banana' is not a database Mohio knows.
  To proceed: use sqlite, postgres, mysql, mongodb or oracle."). A misspelled real engine is caught the
  same way, rather than silently becoming SQLite.
- **The source must be `env.X` or `secret.X`, for every engine.** The declared source governs the
  connection; a literal string is refused at check (verified: `connect db as sqlite from "x.db"` gives
  "Hardcoded connection source. A connect source must be env.X or secret.X, never a literal string",
  `HARDCODED_CREDENTIAL`), because a literal hardcodes a credential and silently fails to open the
  connection. Put the connection string in the environment and name it with `env.`.

## Which environment variable

| Driver            | Env var it reads                          |
|-------------------|-------------------------------------------|
| sqlite            | the path you give (or `DATABASE_URL`)     |
| postgres          | `DATABASE_URL`                            |
| mysql / mariadb   | `MYSQL_URL`, falling back to `DATABASE_URL` |
| mongodb           | `MONGO_URL`, falling back to `MONGODB_URL` |

## Notes

- Drivers are imported only when that backend is actually used, so a missing
  driver only errors if you connect to that backend.
- Tables are created automatically on first save when they don't exist.
- For a deployed app you also want `MOHIO_SECRET` set (it signs form CSRF
  tokens). Without it, a per-process key is used and tokens won't survive a
  restart.
