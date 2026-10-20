# Deploying a Mohio project

Written for somebody who has built a Mohio project and wants it on the internet, and who has
put a website online before by copying files to a server over FTP but has never run a compiler
or a container. You do not need to learn either one to deploy Mohio. This guide says both HOW
and WHY, because the why is what stops a deploy from feeling like guesswork.

**The one thing that is different from FTP.** A PHP or static site is a pile of files a web
server reads on each request, so you upload the files and you are done. A Mohio app is not files
a server reads; it is a PROGRAM that runs and answers requests itself. So "deploying" it does not
mean copying files into a web root. It means giving the program somewhere to RUN, and giving it a
database that survives. That is the whole shift, and once it clicks the three paths below are
just three answers to "where does it run."

---

## Three ways to put it online, and which to pick

1. **getmohio.com (recommended, and the least to learn).** The Mohio platform runs your app for
   you. You hand it your project; it provides the place to run and a database that survives, and
   you do not assemble a server or a container yourself. This is the closest to the old FTP
   feeling: give it the app, it is online. Start here unless you have a specific reason not to.
2. **Your own computer (for building, not for the public).** `mio serve` runs the app on your
   machine so you can see it work and iterate. It is not public hosting; it is the edit-run-look
   loop. Everyone uses this while building, whichever path they later deploy on.
3. **A server you rent and run yourself (a VPS: most control, most work).** A Linux box you
   control, where you install Mohio, run the app, and point it at a database you set up. This is
   the path that most resembles renting a server and doing everything on it, and it is the one
   that asks the most of you. The technical sections further down are this path.

Two Mohio surfaces sit alongside these, and both are worth knowing:

- **Mohio Home** is the installer and console: it puts Mohio on your machine without the clone
  and `pip install` steps, gives you a project to start from, and lets you see what your programs
  are doing. It also has a Deploy screen that packages and sends your project for you.
- **[getmohio.com](https://getmohio.com)** is the platform in path 1: somewhere to run a Mohio
  app once you have written one, rather than assembling hosting for it yourself.

Neither is required (everything here also works from a plain install on your own machine), but
for the audience this guide is written for, getmohio is the easiest place to land a real app and
Mohio Home is the easiest place to build one.

*What each costs is decided on getmohio.com, not here; this guide stays out of pricing so it
never goes stale on a number.*

### Path 1 in practice: getmohio

Because the platform provides the place to run AND the database, the two things that make the
other paths fiddly, path 1 is mostly: build your app (see "start from a starter" below), then
hand it to getmohio.com to run. It uses Postgres underneath, which matters for one concrete
reason covered in the database guide: a host that resets its disk cannot carry a durable
compliance grade, so a platform that offers to run a regulated app uses a database that survives.
You get that by default here without setting it up. The exact steps to connect a project live on
getmohio.com itself, since that is the surface that owns them.

The rest of this guide is the DO-IT-YOURSELF detail behind paths 2 and 3: how to start a
project, run it locally, and stand it up on a server you control.

---

## First: start from a starter, not from a clone

There are two ways to get Mohio, and until recently they did not agree with each other.

**Mohio Home** (the installer) creates a project for you. You give it a name and it writes
a folder with a working hello-world page in it.

**`pip install mohio`** gives you the compiler and the `mio` command. It used to give you
nothing to point them at, so the obvious next move was to clone the Mohio repository and
start editing in there. That repository is not a starting point. It is the compiler's own
workshop, and its deploy files are aimed at Zork, Mohio's own public demo, with real AI
calls switched on. Deploying a clone of it as-is deploys somebody else's demo on somebody
else's bill.

Both paths now write the same starter:

```
pip install mohio
mio new my-app
```

That creates a folder `my-app` containing:

| File | What it is |
|---|---|
| `index.mho` | the page served at `/` |
| `journey.mho` | the project's name |
| `_project.json` | the name and description, so Mohio Home recognizes the folder |
| `Procfile` | the start command a host reads |
| `requirements.txt` | the one dependency: `mohio` |
| `.gitignore` | keeps Mohio's local database and OS clutter out of version control |
| `README.md` | how to run it and how to deploy it |

`mio init` does the same thing in the folder you are already standing in. Neither one will
ever overwrite a file that is already there; if one of those names exists, the command
refuses and says which.

---

## Run it on your own computer first

```
mio serve index.mho
```

Open http://127.0.0.1:8080. That is the whole loop: edit `index.mho`, stop the server with
Ctrl-C, start it again.

Before you deploy anything, run:

```
mio check index.mho
```

It reads the whole program and reports every error and warning it can find, without running
it. It takes a few seconds and it catches most of what a deploy would otherwise catch for
you, slowly, in public.

---

## Deploying it yourself (path 3, and Procfile hosts)

This is the do-it-yourself path: a server or a host you run the app on, rather than handing it to
getmohio.com. If you chose path 1, you can skip to "Settings the host needs to know about" for the
environment values, or stop here. What follows is HOW to stand the app up where you control it.

### Anything that reads a Procfile

Railway, Heroku, Render and Fly all read a `Procfile`. The starter's already says the right
thing:

```
web: mio serve index.mho --port $PORT --host 0.0.0.0
```

Three parts, and each one matters:

- **`index.mho`** is your project's own entry file. If you rename it, change this line.
- **`$PORT`** is the port the host assigns. A host picks the port; it does not ask you.
- **`--host 0.0.0.0`** means answer from outside the container. The default,
  `127.0.0.1`, answers only from inside it, which on a host looks exactly like a
  deploy that succeeded and then does not respond.

Push the folder to the host. It installs from `requirements.txt` and runs the Procfile
line.

### A Dockerfile

You do not need one. The starter does not write one, and neither does Mohio Home, because
a Procfile says the same thing in one line. If your host requires a Dockerfile, the whole
of it is:

```dockerfile
FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mio warmup .
ENTRYPOINT ["/bin/sh", "-c"]
CMD ["mio serve index.mho --port $PORT --host 0.0.0.0"]
```

`mio warmup` compiles the grammar once at build time and stores it in the image, so the first
`mio check` or `mio serve` after the deploy starts warm. Without it the first request after every
deploy waits about twenty seconds while the grammar compiles. It is not required; it is the
difference between a cold start you notice and one you do not.

It moves the parse cost earlier, it does not remove it: the grammar still compiles and files still
parse, but at build time rather than on the first request. Run it after changing files and before
serving (here, as the `RUN mio warmup .` build step above), so the cost is paid before a user waits.

### Mohio Home

If you built the project in Mohio Home, its Deploy screen does all of this for you: it
packs the folder and sends it, and it deliberately leaves a Dockerfile out of what it
sends. You do not need a Procfile there either. The starter carries one anyway, because a
project should be deployable by somebody who never installs Mohio Home.

---

## Settings the host needs to know about

Everything below is set in the host's own environment settings, never written into a file
in your project.

### `DATABASE_URL`

`index.mho` opens with:

```mohio
connect db from env.DATABASE_URL
```

Set `DATABASE_URL` and the project uses that database. Leave it unset and Mohio uses a
local SQLite file under `~/.mohio/data/`, which is fine on your own computer and is usually
wrong on a host, because most hosts throw the filesystem away on every deploy. If your data
needs to survive a deploy, set `DATABASE_URL` to a real database the host gives you.

Mohio speaks SQLite, Postgres, MySQL and MongoDB. For Postgres, install the driver too:

```
pip install "mohio[postgres]"
```

and add `mohio[postgres]` to `requirements.txt` in place of `mohio`.

### `ANTHROPIC_API_KEY`, and why `--ai` is off

An `ai.decide` block is a real decision made by a real AI provider, and a real provider
charges for it. So the starter's Procfile has no `--ai` on it, and that is deliberate
rather than an omission.

Without `--ai`, every `ai.decide` in your program still runs. It runs against a local
stand-in that returns a decision and a confidence, so the program's shape, its audit trail,
its `not confident` fallback and its `on.failure` path all execute and can all be tested.
It costs nothing and it needs no key.

When you want the real thing: add `--ai` to the Procfile line, and set a provider key in
the host's environment. Mohio accepts `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` or
`GEMINI_API_KEY`. Those are two separate steps on purpose. `--ai` with no key at all
refuses at startup, naming all three, rather than quietly running on the stand-in and
letting you believe you had bought real decisions.

`MOHIO_AI=1` in the environment does the same thing as `--ai`, for a host that runs one
command for many applications and cannot rewrite the command line per application.

### `MOHIO_ENCRYPTION_KEY`

Required if any field in your program is tagged `[phi]`, `[pii]` or `[pci]`. Those fields
are encrypted at rest, and the key is what encrypts them. Set it once and do not change it:
a changed key cannot read what the old key wrote.

A read under the wrong key no longer hands back the ciphertext and no longer looks like a moved value: it
refuses loud as `encryption.cannot_decrypt`, naming a rotated or replaced key, and tells you to restore the
original key or re-encrypt the stored rows. A value that is genuinely in the wrong place (a ciphertext copied
from another row, column or table) refuses separately as `encryption.wrong_place`. If the database records no key
check, the refusal names both possible causes and claims neither.

---

## The platform picture today: what is handled, and what you do by hand

Two demo lanes asked for this straight, so here it is without the future tense. What a tenant deploying today can
rely on:

- **A fresh database serves an empty declared table rather than failing.** A read of a table your program
  declares (one it writes anywhere, or governs with `<name> as table`) returns empty on a brand-new deployment
  instead of erroring, because the table is legitimately empty before its first write. A read of a table your
  program never declares still fails loud.
- **Authored data ships in `_seed.json` and loads at serve, automatically.** The underscore keeps it off the web;
  `mio serve` loads it with no flag. See the section below.
- **A journey's `authorize:` and `hidden:` gate static files, not only routes.** A whole folder under a gated path
  is protected, its assets included. See `Docs/guides/FILES-AND-SERVING.md`.

What you must still do BY HAND, stated so nothing surprises you:

- **Point `DATABASE_URL` at a real database.** Left unset, a host throws the filesystem away on each deploy (see
  above).
- **Alter an existing table yourself when a shape changes.** The accumulated half of migration is UNTOUCHED:
  changing a shape does not alter a table that already holds rows (a new column, a changed type, a dropped field).
  A seed sets row VALUES, never a table's structure. This is the real schema-migration gap, tracked as
  `T0-DATA-DEPLOY-NO-MIGRATION`, and today it is manual.
- **Seed any table a security decision reads before the app is reachable.** An empty denylist admits everyone; see
  the security note below.
- **Rename a stale `seed.json` to `_seed.json`.** The old name seeds nothing and is not served; `mio serve` warns
  you. See below.

---

## Getting your data to the deployment

Mohio applications are data-driven: a lot of an app's behavior lives in its ROWS, not only its code (the
scenarios it offers, the policy thresholds it reads, the control states it renders). So a fresh deployment does
not just need your code, it needs the data the code reads, or it comes up empty and does the wrong thing. This
section is what you need to know today; what is still missing is tracked as `T0-DATA-DEPLOY-NO-MIGRATION` and not
repeated here.

**Authored data versus accumulated data, the distinction to hold onto.** AUTHORED data is part of the application
and MUST travel to the deployment: the scenarios, the policy tables, the starting control states, anything the
app's behavior reads. ACCUMULATED data is use, and must NOT travel: real transactions, real users, whatever the
running application piled up. Moving authored data is a deployment step; copying accumulated data into a new
deployment is contaminating it. The convention below keeps the two apart by LOCATION: authored data lives in a
file in your source tree, accumulated data lives only in the database, so the two never meet.

**Ship authored data in `_seed.json` at your project root.** Put a `_seed.json` file (the leading underscore
matters -- see below) beside `index.mho` and `journey.mho`, holding the rows your app reads. `mio serve` loads it
AUTOMATICALLY on startup -- there is no flag to remember, because a convention that needs a flag is not one. It is
versioned with your code, which is exactly what makes it authored: it travels with the deploy because it is part
of the deploy.

**The leading underscore is what keeps it off the web.** The app root is where static files are served from, so a
plain `seed.json` there would be fetchable at `https://your-app.example.com/seed.json` -- and authored data is a
denylist, an allowlist, roles, thresholds: your app's authority configuration, which must never be public. A
leading underscore is Mohio's private-file rule, and the static server never serves such a file, so `_seed.json`
seeds the app but returns 404 to a browser. (This closed `T0-SEED-JSON-SERVED-AS-STATIC`.)

**If you already created a `seed.json` (the old name), rename it to `_seed.json`.** The loader reads only the
underscore name, so a leftover `seed.json` seeds nothing, silently. It is also denied now, at any depth, so it
cannot leak the authority configuration it used to hold, and `mio serve` prints a one-line warning at startup when
one sits at your app root, naming the rename. This is a permanent rule, not a migration window: anyone copying an
older example inherits the old name and gets the same warning.

```json
{
  "scenarios": [
    { "id": 1, "name": "onboarding", "active": true },
    { "id": 2, "name": "renewal", "active": false }
  ]
}
```

Four things follow from the convention, and they are what makes it safe to leave in place:

- **No file, nothing happens.** An app with no authored data ships no `_seed.json`, and startup does not complain.
- **A malformed file fails loud, naming the file.** A broken `_seed.json` stops the deploy rather than starting an
  app with half its authored data missing and no sign of why.
- **It re-runs on every deploy and restart, safely.** Seeding is idempotent, so loading the same `_seed.json`
  again does not double your rows (see the `id` note below).
- **You can override it.** `mio serve <dir> --seed other-file.json` loads that file instead, for the rare case
  where the authored data for a deploy is not the file in the tree.

**Because the file is committed, do not put user data or secrets in it.** A user's row in `_seed.json` means user
data checked into source control, which is the tell that you have put ACCUMULATED data where only AUTHORED data
belongs. Secrets never go in it either: keys and provider credentials come from the environment (see the settings
section above), never a file in the repo.

**What a seed can and cannot do today.** A seed file sets tables, columns and row VALUES against your database.
It does NOT set column TYPES, CONSTRAINTS, INDEXES, or an EMPTY table (a table with no rows). So a seed carries
your authored rows, but it is not a schema migration.

**Give every seed row an `id` where you can.** Seeding is idempotent, so running it again does not double your
data: a row with an `id` is upserted, and an id-less row is skipped when an identical row already exists. The one
gap: an id-less row into a table with an encrypted (`[phi]`/`[pii]`/`[pci]`) column, or a row carrying a null,
may still be re-inserted on a second run, because Mohio cannot content-match it. Give such a row an `id` and the
idempotency is exact.

**Read-before-write: seed at least one row into any table you read before you write it.** A read of a table your
program DECLARES returns empty on a fresh deployment rather than erroring (a missing table on a fresh tenant is
expected), while a read of an UNDECLARED table still fails loud (a real fault). Until the read-tolerant behavior
is fully in place, if your program READS a table before it ever WRITES it, seed that table with at least one row
so the read has something to find.

**A security note about empty tables on a fresh tenant, worth reading before you open the door.** A security
check that READS a table sees whatever is there, and on a fresh deployment that table is EMPTY. An empty
DENYLIST admits everyone; an empty allowlist admits no one. This is inherent to a fresh deployment, not a bug to
work around: SEED any table a security decision reads (the denylist, the allowlist, the roles table) before the
application is reachable, so the first request is judged against real data and not against emptiness.

## The deployment sequence, in order

Put the steps in this order so each one runs against the state the last one left:

1. **Migrations (schema changes).** If a shape changed a table's structure (a new column, a type change), apply
   that structure change FIRST, before any data loads. Today this is done by hand (a seed sets row values, never
   structure, see the migration gap above). `mio migrate typed-storage`, the command that migrates an existing
   text-stored database to typed columns, is NOT yet on origin (tracked under `T1-DB-TYPED-STORAGE`); document it
   here as a step once it lands. Until then, alter the table yourself.
2. **Seed authored data.** `_seed.json` loads at serve automatically; or run with `--seed <file>` to load a
   specific file. Seeding is idempotent when every row has a stable `id` (above), so this step is safe to repeat.
3. **Start the app.** `mio serve .` serves the whole project; `mio serve index.mho` serves one page (see the CLI
   reference). The host's start command is what `mio new` writes into the Procfile.
4. **Health check.** Hit a known route and confirm it answers before sending traffic. A fresh deployment with an
   unseeded security table (above) is the case to check here, not after the door is open.

This order matters: a seed that runs before its table's structure exists writes into the wrong shape, and traffic
that arrives before the seed sees empty security tables.

---

## Caching of served files

A file served from an application root is marked `private`, not publicly cacheable: a browser still caches and
revalidates it, but a shared cache (a CDN or a proxy in front of many tenants) does not hold one tenant's file.
Public edge caching of a genuinely public asset is then a deliberate choice you make at the edge, not a default
that could hand a tenant's file to the wrong viewer.

---

## Checking the deploy actually worked

Every served Mohio project answers at `/mio/health` whether you wrote that route or not.

```
curl https://your-app.example.com/mio/health
```

A reply means the process is up and the compiler is loaded. Point the host's own health
check at that path; the starter's own `/` would work too, but `/mio/health` keeps
answering while you are still rewriting the front page.

---

## When it deploys and then does not answer

In rough order of how often each one is the answer:

1. **`--host 0.0.0.0` is missing.** The server is up and answering only itself.
2. **`$PORT` is hardcoded.** The host assigned a different one.
3. **The entry file was renamed** and the Procfile still names the old one. The logs say
   the file was not found; nothing else does.
4. **`DATABASE_URL` points at a database the host cannot reach.** The failure arrives on
   the first request, not at startup.
5. **`--ai` is on and `ANTHROPIC_API_KEY` is not set.** This one refuses at startup and
   says so.

Read the host's logs first. A Mohio server logs every request it answers, so a log with no
request lines in it means nothing is arriving, which is a different problem from a log full
of 500s.
