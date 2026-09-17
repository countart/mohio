<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Start Here

New to Mohio? This folder is your front door. Everything linked here is current and verified,
if you copy an example, it compiles. (The wider `Docs/` folder holds internal notes and older
material, you do not need it to get going.)

## What Mohio is

Mohio is an AI-native language where AI reasoning and compliance are compiler-enforced
primitives, not libraries you bolt on. One line:

> **Write intent. Execute reason. See everything.**

AI-native does not mean AI writes your app. You write Mohio — it has rules, a compiler, and a
grammar. AI-native means `ai.decide`, `ai.audit`, `ai.agent`, and `ai.explain` are part of the
language itself, governed and audited by the compiler. **Mohio is a programming language. You
write it. AI reasons inside it.**

Files end in `.mho`. The command is `mio`. Every line passes the **Walk-By Test**: a
non-technical person reads it and gets the intent in about three seconds.

## 1. Install and run your first program

Start with **[QUICKSTART.md](QUICKSTART.md)**. You will clone the repo, run
`pip install -e .` (which also installs the `mio` command), and run your first `.mho` file,
a program, a web route, an AI decision, and a sector declaration, in about ten minutes.

## 2. Learn the language

Short, example-driven guides. Every example in these has been run:

- **Decisions (the No-IF way)** — `check / when / otherwise`, `unless`, and reacting to
  outcomes with `on.failure` / `on.success`. See `../docs/conditionals.md`.
- **Loops** -- `repeat`, `while`, `each`, `stop`, `skip`. See `../Docs/guides/Mohio-Loops-Explainer.md`.
- **Tasks and call** — reusable logic with `task`, `give back`, `call`. See
  `../Docs/guides/Mohio-Tasks-and-Call-Explainer.md`.
- **Strings and status codes** — text, multi-line prose, and HTTP responses. See
  `../Docs/guides/Mohio-Strings-and-Status.md`.
- **Casts and coercion** — `as.int`, `as.decimal.2`, and friends. See
  `../Docs/guides/Mohio-Casts-and-Coercion.md`.
- **MioQL (the query language)** — `find`, `retrieve`, `save`, `match`, `where`. See
  `../Docs/guides/mioql-user-guide.md`.
- **App structure** — pages, includes, journeys, and serving. See
  `../Docs/guides/USER_MANUAL_app_structure.md`.
- **Databases** -- what is supported and how to connect. See `../Docs/guides/databases-supported.md`.
- **Getting started, from zero** -- every foundational form (values, `hold`/`lock`, text,
  numbers, `check`/`when`/`otherwise`, `unless`), for someone who has never coded before.
  See `../Docs/manual/mohio-manual-getting-started.md`.
- **Serving an app, one-card reference** -- install, run, check, environment variables, and
  troubleshooting, on one page. See `../Docs/guides/mohio-serve-quickstart.md`.
- **The no-classes mental model** -- for anyone coming from Java, C#, PHP, or Python:
  `shape` describes, `task` acts, and the closest thing to a method. See
  `../Docs/guides/mohio-mental-model-no-classes.md`.
- **Your first real app** -- a shape, a route, a validated form, a database write, a
  list-back, and a search box, built step by step. See
  `../Docs/guides/mohio-first-app-tutorial.md`.
- **A protected feature, end to end** -- search, validation, and `require role` wired
  together on one route, the real shape of a gated feature. See
  `../Docs/guides/mohio-protected-search-tutorial.md`.

The complete, always-current keyword catalog, every word with its status and a verified
example, is the generated **[LANGUAGE-REFERENCE.md](LANGUAGE-REFERENCE.md)**. When you are
unsure whether a form exists or is current, that file is the source of truth.

**The Mohio Manual, Part I: First Programs** -- the same ground as this list, in book form, all
five chapters written and verified, read in order:
`../Docs/manual/mohio-manual-part1-ch1-hello-mohio.md` (install through the no-classes mental model),
`../Docs/manual/mohio-manual-part1-ch2-small-app-real-data.md` (a real, served, data-backed app),
`../Docs/manual/mohio-manual-part1-ch3-setting-up-ai.md` (provider keys, configuring `ai.decide`),
`../Docs/manual/mohio-manual-part1-ch4-app-that-reasons.md` (a real AI decision and its audit trail),
`../Docs/manual/mohio-manual-part1-ch5-mohio-as-your-backend.md` (`framework: api`, no page routing).
Part II onward is not a finished book yet, but a first pass of core-construct chapters is
written and verified out of order, wherever the manual was thinnest: variables and output
(`../Docs/manual/mohio-manual-part2-ch6-variables-and-output.md`), case transforms
(`../Docs/manual/mohio-manual-part2-ch8-working-with-text.md`), decisions
(`../Docs/manual/mohio-manual-part3-ch11-decisions.md`), loops
(`../Docs/manual/mohio-manual-part3-ch12-loops.md`), tasks
(`../Docs/manual/mohio-manual-part4-ch14-tasks.md`), errors and outcomes
(`../Docs/manual/mohio-manual-part4-ch15-errors-and-outcomes.md`), shapes
(`../Docs/manual/mohio-manual-part5-ch16-shapes.md`), data and persistence
(`../Docs/manual/mohio-manual-part7-data-and-persistence.md`), listening for requests
(`../Docs/manual/mohio-manual-part8-ch29-listening-for-requests.md`), and money
(`../Docs/manual/mohio-manual-part2-money.md`) -- exact decimal math, the full built currency set with
its formatting, safe math across money and plain numbers, cross-currency refusal, and a real
database round trip. Sector governance, `flow`/`walk`, and `map` are deliberately not covered
yet -- each is still being verified elsewhere in this repo, and gets its own chapter once that
lands.

## 3. See it work

Small, standalone examples live in `../examples/`:

- `emoji_hello.mho` -- the smallest program, and a first taste of langmaps (Mohio written
  in more than one language).
- `klingon_hello.mho` -- the same idea through another language pack.
- `contact.mho` -- a web form that saves to a database.
- `particularllc-skeleton.mho` -- a fuller site skeleton.

Fuller, real-world-shaped demos live in `../tests/`:

- `fraud_demo_simple.mho` — the smallest AI decision, no database.
- `fraud_demo.mho` — a full fraud screener with a database and audit trail.
- `member_dashboard.mho` — a data-driven read endpoint.
- `patient_intake.mho` — registration and triage in a healthcare sector.

Run any of them with `mio run examples/<file>.mho` or `mio run tests/<file>.mho` (add
`--request-file` and `--seed` as the QUICKSTART shows). The live text adventure is at
**zork.mohio.io**.

## The five rules that never change

1. Comments are `//` and `/* ... */`. `#` is not a comment.
2. No single curly braces `{ }`. The only brace form is `{{ }}` (display a value).
3. Every block closes with its named closer: `check: done`, `ai.decide: done`, `repeat: done`.
4. Straight quotes `"..."` only. Turn off editor auto-correct.
5. One word, one job.

## Community

- Discord: https://discord.gg/9tq7tGSNYE
- GitHub: https://github.com/countart/mohio

Welcome. You are writing in a language almost nobody else has used yet. Break things and tell
us what confused you, that feedback is what makes Mohio better.
