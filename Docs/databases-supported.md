<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md. -->
# Databases in Mohio: what's supported and how to connect

You declare a connection once, at the top of a file:

```
connect db as <driver> from env.<VAR>
```

`db` is the name you'll use later (`save to db.users`, `retrieve.all from db.users`).
The driver word picks the backend. The `env.<VAR>` holds the connection string,
so secrets stay out of your code. The same `save`, `fetch`, `retrieve`, `update`,
and `remove` keywords work the same across every backend.

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
