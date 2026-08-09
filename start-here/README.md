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
- **Loops** — `repeat`, `while`, `each`, `stop`, `skip`. See `../Docs/Mohio-Loops-Explainer.md`.
- **Tasks and call** — reusable logic with `task`, `give back`, `call`. See
  `../Docs/Mohio-Tasks-and-Call-Explainer.md`.
- **Strings and status codes** — text, multi-line prose, and HTTP responses. See
  `../Docs/Mohio-Strings-and-Status.md`.
- **Casts and coercion** — `as.int`, `as.decimal.2`, and friends. See
  `../Docs/Mohio-Casts-and-Coercion.md`.
- **MioQL (the query language)** — `find`, `retrieve`, `save`, `match`, `where`. See
  `../Docs/mioql-user-guide.md`.
- **App structure** — pages, includes, journeys, and serving. See
  `../Docs/USER_MANUAL_app_structure.md`.
- **Databases** — what is supported and how to connect. See `../Docs/databases-supported.md`.

The complete, always-current keyword catalog, every word with its status and a verified
example, is the generated **[LANGUAGE-REFERENCE.md](LANGUAGE-REFERENCE.md)**. When you are
unsure whether a form exists or is current, that file is the source of truth.

## 3. See it work

Runnable demos live in `../tests/`:

- `fraud_demo_simple.mho` — the smallest AI decision, no database.
- `fraud_demo.mho` — a full fraud screener with a database and audit trail.
- `member_dashboard.mho` — a data-driven read endpoint.
- `patient_intake.mho` — registration and triage in a healthcare sector.

Run any of them with `mio run tests/<file>.mho` (add `--request-file` and `--seed` as the
QUICKSTART shows). The live text adventure is at **zork.mohio.io**.

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
