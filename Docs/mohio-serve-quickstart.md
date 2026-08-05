<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md. -->
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

## Install in 4 Steps

**Step 1 -- Clone the repo**
```
git clone https://github.com/countart/mohio
cd mohio
```

**Step 2 -- Install dependencies**
```
pip install -r requirements.txt
```

**Step 3 -- Warm up the compiler** (first run only, takes ~20 seconds)
```
python mio.py warmup
```
You will see a message like:
```
[warmup] Go grab a coffee -- back in about 20 seconds
[warmup] Grammar compiled and cached.
[warmup] Pre-parsing tests/zork_demo.mho...
[warmup] tests/zork_demo.mho -- parsed and cached.
[warmup] Cold-start delay eliminated.
```

**Step 4 -- Run your app**
```
python mio.py serve tests/zork_demo.mho
```
You should see:
```
mio serve  v0.4.6
Loading tests/zork_demo.mho
...
v  Server ready
Listening on  http://127.0.0.1:8080
```

Open your browser to `http://localhost:8080`

---

## Writing Your Own App

Create a file called `app.mho` and run it:
```
python mio.py serve app.mho
```

The simplest possible Mohio app:
```mohio
connect db as sqlite from env.DB_URL

shape Greeting
    name as text
shape: done

listen for
    request for sh.Greeting at /
        give back 200 "Hello from Mohio!" as json
    request: done
listen: done
```

---

## Check Your Code Before Running

```
python mio.py check app.mho
```

Fast check (instant, catches most issues):
```
python mio.py check --fast app.mho
```

Check all .mho files in your project:
```
python mio.py check --all
```

---

## Environment Variables

Create a `.env` file in your project folder:
```
DB_URL=sqlite:///myapp.db
ANTHROPIC_API_KEY=your-key-here
```

Mohio reads this automatically. Never put secrets in your .mho files --
the compiler will warn you if you try.

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
```
pip install lark
```
Or reinstall all dependencies:
```
pip install -r requirements.txt
```

**"python not found" on Windows**
Try `python3` instead of `python`, or install Python from python.org.

**Warmup is taking forever**
Normal on first run. It compiles the grammar and caches it.
Subsequent runs start in under a second.
If it hangs for more than 5 minutes, press Ctrl+C and try again.

**"Syntax error" in my .mho file**
Run `python mio.py check yourfile.mho` for the full error with line numbers
and a suggested fix.

**Port 8080 already in use** (8080 is the default)
```
python mio.py serve app.mho --port 8081
```

**App starts but browser shows nothing**
Make sure your .mho file has a `listen for` block.
Check `http://localhost:8080/mio/health` -- if it returns a running status
(JSON like `{"status":"running", ...}`) the server is up and the issue is in your routes.

---

## Quick Reference

```
python mio.py warmup              # First-time setup
python mio.py serve app.mho       # Run your app
python mio.py check app.mho       # Check for errors
python mio.py check --fast app.mho # Fast check
python mio.py check --all         # Check all files
python mio.py --help              # All commands
```

---

## Need Help?

- Discord: discord.gg/TRt25pc8
- GitHub: github.com/countart/mohio/issues
- Email: hello@mohio.io

---

*Mohio CLI v0.4.6 -- mohio.io*
