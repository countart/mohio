<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md. -->
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

Start with **[QUICKSTART.md](QUICKSTART.md)**. It walks you through downloading the package, installing the dependencies with `pip install -r requirements.txt`, and running your first `.mho` file with `python mio.py`, a program, a web route, an AI decision, and a look at how sectors work, in about ten minutes.

## 2. Learn the language

The guides in this package live in the `docs/` folder:

- **Getting-started manual** — `docs/mohio-manual-getting-started.md`
- **Pioneer manual** — `docs/mohio-pioneer-manual.md`
- **Serving apps** — `docs/mohio-serve-quickstart.md`
- **Primitives reference** — `docs/mohio-primitives-reference-v3.6.md`

The complete keyword catalog, every word with its status and a verified example, is **[LANGUAGE-REFERENCE.md](LANGUAGE-REFERENCE.md)**. When you are unsure whether a form exists or is current, that file is the source of truth.

## 3. See it work

Runnable examples live in the `examples/` folder:

- `examples/emoji_hello.mho` — the smallest program, and a first taste of langmaps.
- `examples/klingon_hello.mho` — the same idea through another language pack.
- `examples/contact.mho` — a web form that saves to a database.
- `examples/particularllc-skeleton.mho` — a fuller site skeleton.

Run a program with `python mio.py run examples/emoji_hello.mho`, or serve a web app with `python mio.py serve examples/contact.mho`. The live text adventure is at **zork.mohio.io**.

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
