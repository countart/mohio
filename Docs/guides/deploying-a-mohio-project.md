# Deploying a Mohio project

Written for somebody who has a Mohio project and wants it on the internet. It assumes
nothing about Docker, and it does not need you to read the compiler.

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
| `_project.json` | the name and description, so Mohio Home recognises the folder |
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

## Deploying

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

`mio warmup` compiles the grammar once at build time and stores it in the image. Without
it the first request after every deploy waits about twenty seconds while the grammar
compiles. It is not required; it is the difference between a cold start you notice and one
you do not.

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
connect db as sqlite from env.DATABASE_URL
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
