<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Part IX -- Operational Guarantees

This part is written for a developer building a regulated application, from what the runtime actually does. Every
claim here was established by running. Where something is planned but not yet built, it says so and names the
backlog item, so you can tell a guarantee from a roadmap.

## Chapter 37: From SQLite to Postgres, without rewriting the app

**The guarantee: the same program runs on every shipped engine.** The data verbs (`save`, `find`, `retrieve`,
`update`, `remove`, joins) are identical across SQLite, PostgreSQL, MySQL/MariaDB and Oracle. Moving from one to
another is a change of the connection address, not of the code.

**Let the address pick the engine.** Write the connection with no `as`, and Mohio reads the engine from the
address the environment variable holds:

```mohio
connect db from env.DATABASE_URL
```

Point `DATABASE_URL` at a file path in development and SQLite opens; point it at a `postgres://` URL in production
and Postgres opens. Nothing in the program changes:

```
# development: a local SQLite file
DATABASE_URL=/data/app.db

# production: a Postgres server
DATABASE_URL=postgres://user:pass@db.internal:5432/app
```

Verified by running: a file path opens SQLite, a `postgres://` value opens Postgres, and an `as` that contradicts
the address refuses at run rather than connecting to the wrong database (see the databases section of
`mohio-manual-part7-data-and-persistence.md`).

**A SQLite file on a temporary host disk is NOT durable.** Many hosts give a container a disk that is wiped on
every redeploy or restart. A SQLite file there looks fine until the first redeploy, then the database, including
the audit trail, is gone. SQLite is right for development and for a host with a real persistent volume; for a
production app on an ephemeral host, use a networked engine (Postgres or MySQL) whose storage survives. Under a
regulated sector this is not optional: the compliance floor refuses a regulated write to storage that cannot keep
it (see `Docs/guides/databases-supported.md`).

An operator can make this refusal explicit on any host by setting `MOHIO_LOCAL_DISK_EPHEMERAL=1`: a `connect` to
a local SQLite file then refuses loudly rather than writing a row that the next restart would erase (verified by
running: "this host's disk is temporary ... so every row would be lost at the next restart. Nothing was written.
To proceed: set the connection's address to a durable database, or run where the disk persists and unset
MOHIO_LOCAL_DISK_EPHEMERAL"). The same flag refuses a local-disk upload, so the database and the uploads fail the
same way on a disk that resets, instead of one of them being a silent data-loss trap.

**Seeding.** A `_seed.json` beside your app is loaded on serve, so a fresh database starts with the rows you ship.
The leading underscore keeps the file off the web. Seeding is idempotent: a restart does not double the data. The
full convention is in the deployment guide (`Docs/guides/deploying-a-mohio-project.md`).

**Typed storage landed (`T1-DB-TYPED-STORAGE`).** A table a shape declares with a `<name> as table` block of
typed fields is now created with real typed columns, so a reporting tool querying the table sees native values,
not text. The table name matches the block name:

```mohio
shape Ledger
    accounts as table
        balance as dec.2
        count as int
        active as boolean
        opened as datetime
shape: done
save to db.accounts
    balance 12.50
    count 3
    active true
    opened now()
save: done
```

Verified by running on SQLite: `count` is a real INTEGER column holding `3`, `active` (boolean) is an INTEGER,
`balance` (a declared-scale decimal) is an exact-text column holding `12.50` at its scale, and `opened`
(datetime) holds the ISO value. A declared currency field is stored as a scaled integer. On the server engines
each value is stored in the engine's native type (on Postgres: `int` as INTEGER, `boolean` as BOOLEAN, currency
and any decimal as an exact NUMERIC); the per-engine mapping is in the database guide,
`Docs/guides/running-mohio-on-your-database.md`.

One thing to know: typed columns come from the shape-declared table. A bare `save` to a table no shape describes
still creates TEXT columns, because nothing tells Mohio the types. Declare the table in a shape to get native
columns.

**A `dec.N` field truncates an over-precise write, toward zero.** A value written with more than the declared
places keeps exactly `N` places, dropping the rest rather than rounding them: `1.239` into a `dec.2` field is
stored as `1.23` (verified by running). This is different from a currency type, which rounds half-up to the
currency's places (see `mohio-manual-part2-money.md`); a plain declared-scale decimal truncates. On SQLite the
`dec.N` column is exact text holding the value at its scale; an older database written before typed storage keeps
its TEXT column and still reads back correctly.

**A value compared with a `dec.N` field is placed at the field's scale first, the same truncation a write
applies.** Before any engine sees it, a value compared with a declared `dec.N` field is truncated to `N` places
toward zero, exactly as a write to that field would be, so a comparison answers as a written value does. A `dec.2`
field holding `3.10` is matched by `3.1`, `3.105` and `3.106576543`, and not by `3.199` (which is `3.19`). This
holds across every comparison form (`is`, `is not`, `above`, `below`, `at most`, `at least`, `between`, `in`,
`not in`, `match`, `match any`, `no.match`), every verb (`find`, `retrieve`, `update`, `remove`, including a
`with` join's parent match, child `where` and child range), and every engine, verified on SQLite, PostgreSQL,
MySQL and MongoDB, with the Oracle leg owed. A currency keeps its own never-round rule, a bare decimal has no
scale, and a held list has no declared type.

**Migrating an existing database: `mio migrate typed-storage`.** A database whose tables were created before
typed storage holds TEXT columns. Convert them to the types their shapes declare with:

```bash
mio migrate typed-storage app.mho
mio migrate typed-storage app.mho --dry-run
```

`--dry-run` lists the plan and every value that will not convert, and changes nothing. Without it, the command
changes the database. Run the dry run first, fix any value it flags, then migrate.
