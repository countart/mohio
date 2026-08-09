<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Mohio Local Development Setup

## Quick Start (no database needed)

For simple .mho files that don't use a database:

```bash
# Clone the repo
git clone https://github.com/countart/mohio
cd mohio

# Windows
setup_mohio.bat

# Mac/Linux
chmod +x setup_mohio.sh
./setup_mohio.sh

# Check any .mho file
python mio.py check tests/fraud_demo_simple.mho
```

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
  -d @tests/seed_zork.json
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
