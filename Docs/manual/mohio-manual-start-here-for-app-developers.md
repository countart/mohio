<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Start here: building an application on Mohio

This page is for a developer building a real application. It says where to look first, how the manual and the
language reference relate, and what to do when you cannot find something.

## Read the manual first, not the compiler source

The manual is where features are explained with runnable, verified examples. If you are learning how to do
something, start here. Reading the compiler source to work out a feature is a sign the manual is behind, and that
is a bug in the manual we want to fix, not a workflow you should adopt. See the last section on reporting a gap.

## Where to look first

- **Getting a first app running:** `mohio-manual-getting-started.md`, then Part I
  (`mohio-manual-part1-ch1-hello-mohio.md` onward).
- **Data: connecting a database, writing and reading, joins, paging:**
  `mohio-manual-part7-data-and-persistence.md`, with the architect/DBA view in
  `Docs/guides/running-mohio-on-your-database.md` and the engine list in `Docs/guides/databases-supported.md`.
- **Dates and times in queries:** `mohio-manual-part2-dates-and-times.md`.
- **Pages, routing and the browser:** Part V, including `mohio-manual-part5-ch24-mioscript.md` (the client side)
  and `mohio-manual-part5-pages-and-previews.md` (the page head and social previews).
- **AI:** `mohio-manual-part1-ch3-setting-up-ai.md` (providers) and `mohio-manual-part1-ch4-app-that-reasons.md`
  (a real decision and its audit trail).
- **The `mio` command line:** `mohio-manual-cli-reference.md`.

**Serve the WHOLE app, not one page.** This catches people on day one: `mio serve .` serves the whole project
(every file answers at its own URL, with the journey cascade and shared session). `mio serve index.mho` serves
only THAT one page and none of its sibling routes. If your app looks half-broken with routes missing or a login
that does not carry between pages, you probably served a single file: serve the directory. Full detail is in the
CLI reference.

- **What changed recently:** `mohio-manual-whats-new-6.0.0.md`.
- **What is NOT built yet:** `mohio-manual-what-is-not-built.md`, so you do not build on a declared-but-unbuilt
  form.

## How the manual and the language reference relate

- The **manual** teaches: how to do a thing, what happens, and what can go wrong, with complete examples. Read it
  to learn.
- The **language reference** (`tools/langref/out/LANGUAGE-REFERENCE.md`, generated) is the exhaustive, per-keyword
  list, each with its status (canonical, non-canonical, retired, reserved, or not-built) and a verified example.
  Read it to confirm a single keyword's exact form and status.

Use the manual to learn a feature and the reference to check a keyword. When the two ever disagree, the grammar
(`mohio.lark`) is the final authority, and the disagreement is itself a doc bug to report.

## If you cannot find it here

If a feature you need is not in the manual, do not reverse-engineer it from the compiler source. Report it as a
doc gap, so the next developer finds it documented. A doc gap is a real, tracked item: name the feature and what
you were trying to do, and it goes into the backlog the same as any other work. Reading source to fill the gap
yourself leaves the gap in place for everyone after you.
