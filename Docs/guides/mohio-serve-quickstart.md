<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Running a Mohio App Locally
## One-Card Developer Guide
### mohio.io | github.com/countart/mohio

---

## What You Need

- Python 3.10 or higher
- Git
- A terminal (PowerShell on Windows, Terminal on Mac/Linux)

Check your Python version:
```
python --version
```

---

## Install in 3 Steps

**Step 1 -- Clone the repo**
```
git clone https://github.com/countart/mohio
cd mohio
```

**Step 2 -- Install it**
```
pip install -e .
```
This installs Mohio's dependencies AND the `mio` command itself. After this, `mio` works
directly, from any folder, the same on Mac, Windows, and Linux -- no `python` prefix needed.

**Step 3 -- Warm up the compiler** (first run only, takes a few seconds)
```
mio warmup
```
You will see a message like:
```
[warmup] Go grab a coffee -- back in about 20 seconds
[warmup] Grammar compiled and cached.
[warmup] Cold-start delay eliminated.
```

**Run a real demo:**
```
mio serve tests/support_escalation_demo.mho
```
You should see:
```
v  Server ready
Listening on  http://127.0.0.1:8080
```

Open your browser to `http://localhost:8080/mio/health` -- you'll get back a small JSON status
block confirming the server is up.

---

## Writing Your Own App

Create a file called `app.mho` and run it:
```
mio serve app.mho
```

The simplest possible Mohio app:
```mohio
connect db as sqlite from env.DATABASE_URL

shape Greeting
    name as text
shape: done

listen for
    request for sh.Greeting at /
        give back [200] "Hello from Mohio!"
    request: done
listen: done
```

---

## Check Your Code Before Running

```
mio check app.mho
```

Fast check (instant, catches most issues):
```
mio check --fast app.mho
```

Check all .mho files in your project:
```
mio check --all
```

---

## Environment Variables

Mohio does not read a `.env` file on its own -- set the variable directly, in your shell,
before running:

Windows (PowerShell):
```
$env:DATABASE_URL="sqlite:///myapp.db"
$env:ANTHROPIC_API_KEY="your-key-here"
```

Mac or Linux:
```
export DATABASE_URL=sqlite:///myapp.db
export ANTHROPIC_API_KEY=your-key-here
```

Never put secrets in your .mho files -- a hardcoded connection string is refused at check
time, and a hardcoded-looking key is flagged; a variable is how the same program runs against
a different database or key in each environment without ever naming either in the code.

---

## What to Expect

| Situation | What You See |
|-----------|-------------|
| First warmup | ~20 second wait, then cached forever |
| App starts clean | `Listening on http://127.0.0.1:8080` |
| Syntax error | Exact line and column with a fix suggestion |
| Missing env var | Clear error telling you which variable |
| Cached app | Starts in under 1 second |

---

## Troubleshooting

**"No module named lark"**
Reinstall from the repo root:
```
pip install -e .
```

**"python not found" on Windows**
Try `python3` instead of `python`, or install Python from python.org.

**Warmup is taking forever**
Normal on first run. It compiles the grammar and caches it.
Subsequent runs start in under a second.
If it hangs for more than 5 minutes, press Ctrl+C and try again.

**"Syntax error" in my .mho file**
Run `mio check yourfile.mho` for the full error with line numbers
and a suggested fix.

**Port 8080 already in use** (8080 is the default)
```
mio serve app.mho --port 8081
```

**App starts but browser shows nothing**
Make sure your .mho file has a `listen for` block.
Check `http://localhost:8080/mio/health` -- if it returns a running status
(JSON like `{"status":"running", ...}`) the server is up and the issue is in your routes.

---

## Quick Reference

```
mio warmup              # First-time setup
mio serve app.mho       # Run your app
mio check app.mho       # Check for errors
mio check --fast app.mho # Fast check
mio check --all         # Check all files
mio help                # All commands
```

---

## Need Help?

- Discord: discord.gg/9tq7tGSNYE
- GitHub: github.com/countart/mohio/issues
- Docs: mohio.io

---

*mohio.io*
