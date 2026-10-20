<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Mohio Local Development Setup

## Live-database test engines (for working on the compiler itself)

The test suite's live-database batteries (`tools/run_tests.py --engines`) use THREE DEDICATED
DOCKER CONTAINERS, and only these three -- never a locally installed server, never the Postgres/
MySQL/MariaDB instances described further down this page for running Zork:

| Engine   | Container      | Port | Credential              |
|----------|----------------|------|--------------------------|
| Postgres | `mohio-pg`     | 5433 | `postgres` / `mohiotest` |
| MySQL    | `mohio-mysql`  | 3309 | `root` / `mohiotest`     |
| Oracle   | `mohio-oracle` | 1521 | `mohio` / `mohiotest` (service `FREEPDB1`) |

Start them with (adjust images/tags as needed):
```bash
docker run -d --name mohio-pg    -p 5433:5432 -e POSTGRES_PASSWORD=mohiotest -e POSTGRES_DB=mohio postgres:16-alpine
docker run -d --name mohio-mysql -p 3309:3306 -e MYSQL_ROOT_PASSWORD=mohiotest -e MYSQL_DATABASE=mohio mysql:8.0
docker run -d --name mohio-oracle -p 1521:1521 -e ORACLE_PASSWORD=mohiotest gvenzl/oracle-free:slim
# then create the mohio app user/schema in Oracle and the marker table in each -- see
# tests/_engine_isolation.py's docstring for the exact marker a battery checks for.
```

Every live battery REFUSES to run unless it finds a marker table (`mohio_test_container_marker`)
that only these containers carry -- a port number is not proof of what is listening there. A
battery that cannot find the marker treats that as a hard, loud failure, not a quiet skip,
because something answering on the expected port that is NOT the Mohio container is the one
case that must never be silently trusted.

**The native installs described below in this page (a locally installed Postgres on 5432, or a
locally installed MySQL/MariaDB on 3306/3307) are NO LONGER USED by the test suite.** They
predate the container setup above and the test suite's own hardcoded defaults used to point at
them by accident; that has been fixed, and every live battery's default now points only at the
three containers. If you have one of those native installs for an unrelated reason (your own
Zork database, below, or anything else), the test suite will never touch it, and this page makes
no further changes to it.

---

## Installing Mohio

Install and run your first file with **`start-here/QUICKSTART.md`**, the one canonical install walkthrough
(kept in one place so the steps cannot drift). This document picks up after that, for the database-backed
local setup QUICKSTART does not cover.

---

## Full Setup (for Zork and database-driven apps)

### Step 1 — Install PostgreSQL

**Windows:**
1. Download from https://www.postgresql.org/download/windows/
2. Run installer — remember your password, keep port 5432
3. Add to PATH: `C:\Program Files\PostgreSQL\16\bin`

**Mac:**
```bash
brew install postgresql@16
brew services start postgresql@16
```

**Linux:**
```bash
sudo apt install postgresql
sudo service postgresql start
```

### Step 2 — Create a local database

```bash
# Connect to PostgreSQL
psql -U postgres

# Create database
CREATE DATABASE zork_local;

# Exit
\q
```

### Step 3 — Set your DATABASE_URL

**Windows (PowerShell):**
```powershell
$env:DATABASE_URL = "postgresql://postgres:yourpassword@localhost/zork_local"
```

**Windows (permanent — System Properties > Environment Variables):**
```
DATABASE_URL = postgresql://postgres:yourpassword@localhost/zork_local
```

**Mac/Linux:**
```bash
export DATABASE_URL="postgresql://postgres:yourpassword@localhost/zork_local"
```

### Step 4 — Seed the database

```bash
# Seed Zork data
curl -X POST "http://localhost:8080/mio/seed?secret=your_seed_secret" \
  -H "Content-Type: application/json" \
  -d @your-seed-data.json   # the Zork seed is a private fixture and is not distributed; supply your own JSON
```

Or once the server is running, visit:
`http://localhost:8080/mio/admin`

Set `SEED_SECRET` and `ADMIN_ENABLED` environment variables first:
```powershell
$env:SEED_SECRET = "any_secret_you_choose"
$env:ADMIN_ENABLED = "true"
```

### Step 5 — Run Zork locally

```bash
python mio.py serve tests/zork_demo.mho
```

Open browser: `http://localhost:8080`

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | For DB apps | PostgreSQL connection string |
| `ANTHROPIC_API_KEY` | For AI features | Your Anthropic API key |
| `SEED_SECRET` | For admin UI | Any secret string you choose |
| `ADMIN_ENABLED` | For admin UI | Set to `true` to enable |

---

## Local vs Production

Your local setup mirrors Railway exactly:
- Same `DATABASE_URL` format (just different host/database name)
- Same environment variables
- Same `.mho` files — no changes needed to push live

When you push to Railway, Railway uses its own `DATABASE_URL`
pointing to the Railway PostgreSQL instance. Your code is identical.

---

## Common Commands

```bash
# Check syntax and compliance
python mio.py check myapp.mho

# Run without server
python mio.py run myapp.mho

# Start web server
python mio.py serve myapp.mho

# Serve a whole directory of .mho files
python mio.py serve mysite/

# Translate keywords to another language
python mio.py translate --from en --to pt myapp.mho

# Generate AI training data from usage
python mio.py generate training-data applang

# Check compiler version
python mio.py version
```

---

## VS Code Setup

1. Clone the repo
2. Open in VS Code
3. Install the Mohio extension (in `vscode-extension/` folder)
4. Syntax highlighting and snippets activate automatically for `.mho` files

---

## Getting Help

- Discord: https://discord.gg/TRt25pc8
- GitHub Issues: https://github.com/countart/mohio/issues
- Email: hello@mohio.io
- Documentation: https://mohio.io
