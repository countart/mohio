<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Start Here -- the reading path

New to Mohio and nobody to ask? This is the map. It is ordered on purpose: read the first list in
sequence, reach for the second when a task needs it, and do not read the third list front to back. If you
copy an example from anything linked here, it compiles.

## What Mohio is

Mohio is an AI-native language where AI reasoning and compliance are compiler-enforced primitives, not
libraries you bolt on. One line:

> **Write intent. Execute reason. See everything.**

AI-native does not mean AI writes your app. You write Mohio -- it has rules, a compiler, and a grammar.
AI-native means `ai.decide`, `ai.audit`, `ai.agent`, and `ai.explain` are part of the language itself,
governed and audited by the compiler. Files end in `.mho`. The command is `mio`. Every line passes the
**Walk-By Test**: a non-technical person reads it and gets the intent in about three seconds.

---

## Read first, in order

This is the whole path from nothing to a working, data-backed, served app. Nine steps, each building on
the last.

**Before the path, read [What every program needs](../Docs/manual/mohio-manual-what-every-program-needs.md).**
It shows, in one runnable program, the four things almost every example assumes and leaves out to stay short: a
connection, a shape, a handler, and a role granted at login. Read it once and every short snippet afterward reads
as the one new line on top of those four, instead of a mystery that refuses.

1. **Install and your first program** -- **[QUICKSTART.md](QUICKSTART.md)**. Install Mohio, run a `.mho`
   file, a program, a web route, an AI decision, and a sector declaration, in about ten minutes.
2. **First programs, in book form** -- `../Docs/manual/mohio-manual-part1-ch1-hello-mohio.md` (install,
   the first program, the five rules, values, text and choices, numbers and casts, and the one shift a
   reader from another language needs: Mohio has no classes).
3. **A small app with real data** -- `../Docs/manual/mohio-manual-part1-ch2-small-app-real-data.md` (a
   shape, a route, a validated form, a database write, a list-back: the smallest complete web app).
4. **The core constructs** -- values and output
   (`../Docs/manual/mohio-manual-part2-ch6-variables-and-output.md`), text
   (`../Docs/manual/mohio-manual-part2-ch8-working-with-text.md`), decisions
   (`../Docs/manual/mohio-manual-part3-ch11-decisions.md`), loops
   (`../Docs/manual/mohio-manual-part3-ch12-loops.md`).
5. **Shapes** -- `../Docs/manual/mohio-manual-part5-ch16-shapes.md`, the data contract at the center of
   everything, and the no-classes model: `../Docs/guides/mohio-mental-model-no-classes.md`.
6. **Handlers and data** -- listening for requests
   (`../Docs/manual/mohio-manual-part8-ch29-listening-for-requests.md`), reading and writing data
   (`../Docs/manual/mohio-manual-part7-data-and-persistence.md` and the query language,
   `../Docs/guides/mioql-user-guide.md`).
7. **Tasks and outcomes** -- reusable logic (`../Docs/manual/mohio-manual-part4-ch14-tasks.md`) and what
   happens when something fails (`../Docs/manual/mohio-manual-part4-ch15-errors-and-outcomes.md`).
8. **Structure** -- folders, the spine, and page classification
   (`../Docs/manual/mohio-manual-part5-ch23-journeys.md`), and route path parameters
   (`../Docs/manual/mohio-manual-part5-ch20-route-path-parameters.md`).
9. **When something refuses, and what is not built** -- read these two EARLY, not last. Why Mohio refuses
   things (`../Docs/manual/mohio-manual-why-mohio-refuses.md`) turns a refusal from friction into a
   signal, and **What is not built yet** (`../Docs/manual/mohio-manual-what-is-not-built.md`) is the
   honest boundary: if you meet a word that refuses as "declared, not built," that file lists it. A
   partner who checks there first loses no time to a feature that was never wired.

---

## Read when you need it

Not part of the first path; reach for the chapter when the task calls for it.

- **Money** -- exact decimal, the currency set, safe math, `no.symbol`:
  `../Docs/manual/mohio-manual-part2-money.md`.
- **Files and uploads** -- what is served and what is private
  (`../Docs/guides/FILES-AND-SERVING.md`), durable uploads and signed links
  (`../Docs/manual/mohio-manual-part6-ch26-durable-uploads.md`).
- **Webhooks** -- `../Docs/manual/mohio-manual-part8-ch31-webhook-signatures.md`.
- **MioScript** (the browser layer) -- `../Docs/manual/mohio-manual-part5-ch24-mioscript.md`.
- **AI create and agents** -- `../Docs/manual/mohio-manual-part7-ch36-ai-create-and-agent.md`.
- **Maps, flow, and walk** -- `../Docs/manual/mohio-manual-part7-ch27-maps-flow-and-walk.md`.
- **Databases, and the compliance floor** -- use whatever database you like; compliance sets a
  minimum for what can carry a durable audit trail. See `../Docs/guides/databases-supported.md`.
- **Deploying** -- getmohio.com, your own machine, or a server you run yourself:
  `../Docs/guides/deploying-a-mohio-project.md`.
- **Compliance and sectors** -- `../Docs/guides/compliance.md` and the audit trail
  (`../Docs/guides/audit-trail.md`), when you are in a regulated sector.

---

## Reference, not learning (do not read front to back)

Consult these when you need to look one thing up. They are catalogs, not lessons.

- **[LANGUAGE-REFERENCE.md](LANGUAGE-REFERENCE.md)** -- the complete, always-current keyword catalog,
  every word with its status and a verified example. The source of truth when you are unsure a form
  exists or is current.
- **Casts and coercion** (`../Docs/guides/Mohio-Casts-and-Coercion.md`), the **punctuation card**
  (`../Docs/guides/mohio-punctuation-card.md`), **retired forms**
  (`../Docs/guides/RETIRED-FORMS-REFERENCE.md`), **two-word forms that work today**
  (`../Docs/guides/two-word-forms-what-works-today.md`).
- The **loops**, **tasks**, and **strings** explainers under `../Docs/guides/` cover the same ground as
  the chapters above, in guide form. Prefer the chapters; the explainers are there if you want a second
  pass on one topic.

---

## See it work

Small, standalone examples live in `../examples/`: `emoji_hello.mho` (the smallest program),
`contact.mho` (a web form that saves to a database), `particularllc-skeleton.mho` (a fuller site).
Fuller demos live in `../tests/`: `fraud_demo_simple.mho` (the smallest AI decision, no database),
`fraud_demo.mho` (a full fraud screener with an audit trail), `member_dashboard.mho` (a data-driven read
endpoint), `patient_intake.mho` (registration in a healthcare sector). Run any with
`mio run <file>.mho`. The live text adventure is at **zork.mohio.io**.

## The five rules that never change

1. Comments are `//` and `/* ... */`. `#` is not a comment.
2. No single curly braces `{ }`. The only brace form is `{{ }}` (display a value).
3. Every block closes with its named closer: `check: done`, `ai.decide: done`, `repeat: done`.
4. Straight quotes `"..."` only. Turn off editor auto-correct.
5. One word, one job.

## Community

- Discord: https://discord.gg/9tq7tGSNYE
- GitHub: https://github.com/countart/mohio

Welcome. You are writing in a language almost nobody else has used yet. Break things and tell us what
confused you; that feedback is what makes Mohio better.
