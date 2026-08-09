<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Mohio Quick Start Guide

*You know how to code. You've never seen Mohio. This gets you running in 10 minutes.*

---

## What is Mohio?

Mohio (pronounced *moh-hee-oh* — Māori for "to understand") is a programming language where AI reasoning and compliance are built in. Not libraries. Not APIs you bolt on. The language itself knows what a fraud check is, what HIPAA requires, and what happens when an AI isn't confident enough to decide.

You write intent. The compiler enforces the rules.

---

## What you need

Before you install, make sure you have:

- **Python 3.10 or higher** (3.12 or newer recommended). Check with `python --version` (or `python3 --version` on Mac and Linux). If you don't have it, get it from python.org.
- **pip**, which comes with Python.
- **The Mohio pioneer package** (the zip linked below).
- **A text editor.** VS Code is a good choice; any editor works.

Optional, only when you want them:

- **An AI provider key** for real AI decisions (`ai.decide`). You do NOT need one to learn: with no key set, AI runs in mock mode, which is perfect for getting started. For real AI, the simplest path is an Anthropic key (`ANTHROPIC_API_KEY`), which the `--ai` flag uses directly. OpenAI (`OPENAI_API_KEY`) and Gemini (`GEMINI_API_KEY` or `GOOGLE_API_KEY`) are also supported, currently by declaring an `ai.connect` provider chain in your program.
- **A database.** You do NOT need one to start: SQLite is built into Python and Mohio uses it out of the box. Postgres and MySQL are for later; their drivers already install with the package, you would just add the database server yourself.

### Windows, Mac, or Linux

Mohio behaves the same on every operating system, and it brings its own web server and SQLite built in. The only differences are how you install Python and how you type a couple of commands:

- **Windows:** install Python from python.org and check "Add Python to PATH" during setup. The command is usually `python` (sometimes `py`). Set an environment variable in PowerShell with `$env:DATABASE_URL="pioneer.db"`. One Windows-only gotcha: if your program shows an emoji or other non-Latin character, the console can stop with a `UnicodeEncodeError`. Set `$env:PYTHONIOENCODING="utf-8"` once in your PowerShell session and it prints fine.
- **Mac:** install from python.org or Homebrew. The command is often `python3`. Set a variable with `export DATABASE_URL=pioneer.db`.
- **Linux:** install with your package manager. The command is often `python3`. Set a variable with `export DATABASE_URL=pioneer.db`.

This guide writes `python`; if that doesn't work, try `python3`.

---

## 1 — Installation

**Requirements:** Python 3.12 or higher.

Download the Mohio pioneer package (a zip):

  https://drive.google.com/file/d/1mZabIOAuRL8wSlRCBIHlkao9fkBz57MZ/view?usp=sharing

Unzip it somewhere you can find it (for example `C:\mohio`). Then, in the unzipped folder, install the dependencies:

```bash
pip install -r requirements.txt
```

Verify it works:

```bash
python mio.py version
```

Warm up the parse cache (faster runs after this):

```bash
python mio.py warmup
```

> **How to type commands:** the pioneer package runs with `python mio.py`. This guide writes `mio` for brevity, so wherever you see `mio ...`, type `python mio.py ...` instead. For example, `mio run hello.mho` means `python mio.py run hello.mho`.

*Every Mohio program is a `.mho` file.*

---

## 2 — Your First Program

Create a file called `hello.mho`:

```mohio
greeting "Hello from Mohio."
show greeting
```

Run it:

```bash
mio run hello.mho
```

**What just happened:**

Naming a value is all it takes: `greeting` is the name, the text is what it holds.
That is the everyday variable, and you can change it any time by naming it again.
`show` prints it.

(`hold` is a different thing, covered later: it FREEZES a value until you `release`
it. Reach for it when you want a value guarded, not for ordinary variables.)

That's it. No boilerplate. No main function. No imports. The file is the program.

**Add a task** (Mohio's word for a function):

```mohio
task greet
    message "Hello, Mohio."
    give back message
task: done

call greet
```

`task` opens a block. `task: done` closes it — and the closer names what it closes. If you write the wrong closer, the compiler tells you exactly which block is unclosed and on which line.

`give back` is how you return a value. `call` invokes the task.

---

## 3 — Your First Web Route

Create `server.mho`:

```mohio
shape Member
    name as text
shape: done

listen for
    request for sh.Member at /
        name "World"
        give back 200 "Hello, {{ name }}."
    request: done
listen: done
```

Run it:

```bash
python mio.py serve server.mho
```

Open `http://localhost:8080` — you'll get:

```
Hello, World.
```

**What just happened:**

`listen for` opens the server. Every route lives inside it.

`shape Member ... shape: done` declares a data contract, a **shape**, named Member. `request for sh.Member at /` handles a GET request at the path `/`, and `sh.Member` refers to the shape you just declared (`sh.` means shape). There are no magic built-in shapes; you declare the ones you need with `shape`. Note the path is written bare, `at /`, not quoted.

`{{ name }}` interpolates the variable inline. `give back 200` sends the HTTP response with status code 200.

**Branching** — use `check` instead of if/else:

```mohio
score "85"

check score as result
    when "100"
        give back "perfect"
    when "85"
        give back "good"
    otherwise
        give back "keep going"
check: done
```

`check` evaluates a value. `when` handles each case. `otherwise` is the fallback. Naming goes on the opener: `check score as result` binds the outcome to a variable called `result`, and `check: done` closes the block. You can use `result` in the next line.

---

## 4 — Your First AI Decision

This is where Mohio is different from every other language.

Create `fraud.mho`:

```mohio
ai.decide isFraudulent returns boolean
    confidence above 0.85
    weigh transaction.amount, transaction.device_id
    ai.audit to fraud_audit_log
    not confident
        give back 202 "Flagged for manual review"
    on.failure
        give back 503 "AI service unavailable"
ai.decide: done

check isFraudulent
    when true
        give back 422 "Transaction blocked"
    otherwise
        give back 200 "Approved"
check: done
```

Run it without a real AI key (mock mode, free and instant):

```bash
python mio.py run fraud.mho --verbose
```

Run it with real AI (needs a provider key):

```bash
python mio.py run fraud.mho --ai --verbose
```

`--ai` uses your Anthropic key (`ANTHROPIC_API_KEY`) by default. For OpenAI or Gemini, see "What you need."

**What just happened:**

`ai.decide isFraudulent returns boolean` — you're declaring an AI decision block that returns true or false.

`confidence above 0.85` — the AI must be at least 85% confident. If it isn't, the `not confident` block fires automatically. The compiler will refuse to build this file if `not confident` is missing.

`weigh` — what the AI considers when making the decision.

`ai.audit to fraud_audit_log` — every decision is logged automatically. Inputs, result, confidence, model, timestamp. You don't write logging code. This line makes it happen.

`not confident` — required. This is where you decide what happens when the AI isn't sure enough. Route to human review, return a safe default, log the uncertainty — your call, but you must handle it.

`on.failure` — if the AI service is unavailable entirely, this fires.

**The compiler enforces three things you can't forget:**
1. A fallback for low confidence (`not confident`)
2. An audit trail (`ai.audit`)
3. An error handler (`on.failure`)

Leave any one of them out — the build fails with a clear message telling you exactly what's missing and where.

---

## 5 — Your First Sector Declaration

One line changes everything about how the compiler treats your code.

Create `payments.mho`:

```mohio
sector: financial

shape Payment
    amount as decimal
shape: done

listen for
    new sh.Payment at /pay
        require role "processor"
        give back 200 "Payment received"
    new: done
listen: done
```

Check it:

```bash
python mio.py check payments.mho --security
```

**What just happened, and what to expect right now:**

`sector: financial` is how you tell Mohio which rulebook a program lives under. In the full Mohio product, declaring a sector loads a certified compliance profile, and from that point the compiler enforces it. For `financial`, that means things like: card CVVs can never be stored, card numbers must be tokenized before storage, large cash transactions trigger reporting, AI decisions on transactions require a minimum confidence, and access to financial records is audited automatically. One line declares the regulatory world your code lives in; the certified profile does the enforcing.

You do NOT need to use sectors to start, and you are not expected to yet. This evaluation package recognizes the `sector:` declaration but does not ship the certified profiles, so `mio check` will tell you a profile was not found and nothing is being enforced. That is expected. The goal for now is to understand the model, when you eventually build something in a regulated space, this is the mechanism that carries the compliance weight.

`require role "processor"` — only users with the processor role can hit this route. The sector profile enforces that this check exists on routes touching payment data.

`sector: healthcare` works the same way for HIPAA.

You declare the sector. The compiler does the compliance work.

---

## What's Next

**Validate your code:**
```bash
mio check myfile.mho           # structural check
mio check myfile.mho --security # security + compliance check
mio check myfile.mho --json     # machine-readable output
```

**Run it:**
```bash
mio run myfile.mho             # execute a program
mio serve myfile.mho           # start a web server
```

**Connect a database (SQLite, no server needed):**
```mohio
connect db as sqlite from env.DATABASE_URL

save to db.notes
    body "first note"
    author "Ronnie"
save: done

find all in db.notes
find: done

repeat each n in all
    show n.body
repeat: done
```

Mohio never lets you hardcode a connection string (that would bake a credential into your code), so the path always comes from an environment variable. Run it:

Windows (PowerShell):
```
$env:DATABASE_URL="pioneer.db"
python mio.py run notes.mho
```
Mac or Linux:
```
DATABASE_URL=pioneer.db python mio.py run notes.mho
```

That creates `pioneer.db` and stores your note. For Postgres later, change `sqlite` to `postgres` and point `DATABASE_URL` at your Postgres server; the driver is already installed with the package.

**Go deeper:**

The **Mohio Language Reference** (`start-here/LANGUAGE-REFERENCE.md` in this package) lists every keyword with its status and a verified example. When you are unsure whether a form exists or is current, that file is the source of truth.

**Join the community:**
- GitHub: [github.com/countart/mohio](https://github.com/countart/mohio)
- Discord: [discord.gg/9tq7tGSNYE](https://discord.gg/9tq7tGSNYE)
- Docs: [mohio.io](https://mohio.io)

---

*Mohio Evaluation License v1.0 · Particular LLC*
