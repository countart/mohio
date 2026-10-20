<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# The `mio` command reference

*The canonical list of `mio` commands, each established by running `mio` (no arguments), `mio version`, and the
command itself, with its actual output. For a gentle first-time introduction to using a terminal at all, read
`Docs/guides/mohio-cli-guide-for-beginners.md`; this is the complete reference that guide is not.*

Every command is `mio <command> [arguments] [flags]`. `mio` with no arguments, and `mio help`, print the usage
summary this reference expands on.

## Read-only or changes something

A developer running an unfamiliar command on a real application wants to know, before they run it, whether it
can change anything. Each command below is marked. The short version:

| Command | Safe to run on a real app? |
|---|---|
| `check`, `fmt` (no `--write`), `writes`, `walk`, `schema show`/`check`, `audit verify`/`head`/`verify-anchors`, `schedule list`, `version`, `help` | **Read-only.** Inspects, never writes. |
| `run`, `serve`, `test` | **Runs your code.** Read-only UNLESS the program itself writes (then it writes exactly what the program says to). |
| `ai-check` | Changes no files, but makes real AI calls (needs a provider key), so it spends AI quota. |
| `fmt --write`, `create`, `new`, `init`, `translate`, `harvest --output`, `schema generate`, `generate` | **Writes files.** New files or a rewritten one. |
| `audit relay`, `schedule run-due` | **Changes state.** Delivers owed audit records / runs due scheduled tasks. |
| `warmup` | Writes a CACHE only, never application data. |
| `install-hooks` | **Changes your git setup** (installs pre-commit hooks). |

## Everyday commands

### `mio check <file.mho>` -- validate without running (read-only)

Validates a file and reports every error and warning without running it. Run it constantly.

```
$ mio check hi.mho
  v  hi.mho  1 lines . no errors
```

`mio check` also takes a FOLDER: it checks every `.mho` under it, and when any fail it names each failed file
and exits nonzero (verified: a folder with one broken file reports `1 file(s) failed:` and lists it by name),
so a broken file in an app is not lost in a clean summary.

Across a folder, each finding NAMES THE FILE it is in, and a finding in SHARED code is reported ONCE. A finding
declared in a `journey` (shared code every page under it inherits) is shown a single time, at the journey, rather
than repeated for every page that inherits it. Verified by running a folder with a broken page and a journey
carrying a shared finding:

```
  journey.mho  (shared: the code is here, and every page under it inherits it)
  ...
  Summary  2 file(s) checked . 0 warning(s) . 1 error(s)
  1 finding(s) declared in a journey and inherited by every page are shown once, at the journey.
  x 2 file(s) failed:
    broken.mho
    journey.mho
```

Flags: `--all` checks every `.mho` file in the tree; `--security` gives the full security and compliance
report. Changes nothing.

**Warning about `--fast`.** `mio check --fast` runs ONLY the ASCII and reserved-word checks and SKIPS the full
parse (verified: it prints "fast check passed (ASCII + reserved words)" and tells you to run without `--fast`
for the full parse). It is a quick pre-filter, NOT a substitute for a normal check: a file can pass `--fast` and
still fail to parse. Use a plain `mio check` before trusting a file.

### `mio run <file.mho>` -- execute once (runs your code)

Runs the program once and prints what it shows.

```
$ mio run hi.mho
  hello
```

Flags: `--verbose` adds trace output; `--ai` uses the real Anthropic API for `ai.decide` (otherwise a labeled
mock). It writes to the database only if the program does; a program that only computes and shows changes
nothing.

**`--ai` makes a REAL provider call and can incur charges.** Without it, `ai.decide` uses a labeled mock; with
it, the run calls the configured provider, which costs money and quota. The same applies to `mio ai-check`
below.

**Passing a request.** A handler reads an incoming request; to drive one from the CLI, supply the request three
ways (all verified by running):

```
mio run app.mho --request-file req.json                 # a JSON file (recommended, all platforms)
mio run app.mho --request '{"_shape":"Ping","note":"hi"}'   # inline JSON (macOS, Linux, PowerShell)
mio run app.mho --param _shape=Ping --param note=hello       # repeated key=value (Windows CMD friendly)
```

Inline JSON suits macOS, Linux and PowerShell; repeated `--param` suits Windows CMD, where quoting inline JSON
is awkward. A request carries the program's own fields plus underscore keys that route it: `_shape` (which
shape the body satisfies), `_method` (the HTTP method), and `_roles` (the caller's roles). An example file:

```
{
  "_shape":  "Transaction",
  "_method": "POST",
  "_roles":  ["screener"],
  "amount":  500
}
```

### `mio serve <file.mho>` or `mio serve <directory>` -- run as a web server (runs your code)

Starts an HTTP server (default port 8080). **The argument decides how much is served, and this has cost people
a session:**

- **`mio serve <directory>` serves the whole application** -- every file at its convention URL, with the
  journey cascade applied (see the Journeys chapter). This is what you want for a real multi-page app.
- **`mio serve <file.mho>` serves ONE PAGE.** It loads only that file and none of the sibling routes, so an
  application looks half-broken for a reason that is not the code. The build lane added a note when a file is
  served beside sibling routable files, so you are told; still, reach for the directory form for an app.

Flags: `--port <n>` sets the port; `--ai` serves with the real Anthropic API. It writes only what the app's
handlers write.

**What `mio check` validated is what `mio serve` routes.** A handler `mio check` passes is a handler `mio serve`
routes, so a declared handler is never silently gone at serve time. A route that could never be reached is caught
at CHECK rather than disappearing at run: two handlers answering the same method and path refuse at check, naming
both lines:

```
Two handlers answer GET /home: one at line 5 and one at line 9. A method and path together name exactly one route, so the second can never be reached. Give one a different path, or merge them.
```

And a top-level line that does not parse as a handler refuses on its own line, rather than being read as page text
that check blesses while serve then 404s. Verified by running: an app's GET, POST and in-journey handlers each
respond under `mio serve` exactly as `mio check` validated them.

### `mio fmt <file.mho> [--write]` -- normalize toward canonical (read-only, or writes with `--write`)

Reports how the file differs from canonical Mohio. Without `--write` it only reports:

```
$ mio fmt hi.mho
mio fmt: hi.mho -- already canonical
```

With `--write` it rewrites the file in place. So `mio fmt <file>` is read-only; `mio fmt <file> --write`
changes the file.

## Testing

### `mio test <file.mho>` -- run the file's `it` test cases (runs your code)

Runs the `it "what this proves" ... it: done` cases in the file and reports each.

```
$ mio test add.mho
  v  adds two numbers
  1 case(s) passed
```

Two defaults are worth stating, because they are the opposite of what most test runners do, and they are the
right way round:

- **A file with NO test case is a FAILURE, not a pass** (verified: `mio test` exits 1 on a file with no `it`
  block). A test run that found nothing to run should never report success.
  ```
  $ mio test hi.mho
    !  no test cases in this file -- `it "what this proves" ... it: done`
  ```
- **An assertion the build cannot evaluate FAILS the case by name**, it does not pass (verified). Nobody gets a
  green from an assertion that was never actually run:
  ```
  $ mio test badassert.mho
    x  checks something odd
          the case stopped with a refusal: ...
    1 of 1 case(s) failed
  ```

Flags: `--db`, `--memory`, `--verbose`, `--ai`, `--api-key`. It runs the cases, so it writes whatever they
write (use `--memory` or a throwaway `--db` to keep a real database untouched). If you have been writing test
suites in another language, this is the built-in runner to reach for first.

### `mio ai-check <file.mho>` -- run AI-decision compliance checks (makes AI calls)

Exercises the file's AI decisions against a provider. It needs an AI provider key
(`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or `GEMINI_API_KEY`), or `MOHIO_AI=mock` for the labeled mock:

```
$ mio ai-check add.mho
Error  No AI provider key found. Set ANTHROPIC_API_KEY, OPENAI_API_KEY, or GEMINI_API_KEY
(or run with MOHIO_AI=mock for the labeled mock provider).
```

It changes no files, but a real provider call **can incur charges** (use `MOHIO_AI=mock` to exercise it without
spending). Same as `mio run --ai`.

## Scaffolding a project (writes files)

- **`mio create <name>`** creates a new project folder (framework `web` by default); `mio create <framework>
  <name>` names the framework (`web`, `api`, `game`).
- **`mio new <name>`** creates a new project folder with a starter page.
- **`mio init`** puts that same starter in the folder you are already in.

All three write files. Run them in an empty or intended directory.

## Data, schema, and writes (mostly read-only)

### `mio schema <generate|check|show> <file.mho | folder>` -- work with `.mhoschema` files

`show` prints the database tables a program uses; `check` validates against the `.mhoschema`; `generate` writes
a `.mhoschema`. `show`/`check` are read-only; `generate` writes.

```
$ mio schema show add.mho
No .mhoschema found for add.mho
```

**A single file or an app folder.** `mio schema` takes one `.mho` file, writing `<file>.mhoschema` beside it, OR
an app folder, the same way `mio check` and `mio serve` do: it resolves the journey and every include and
describes the whole app, writing ONE manifest at `<folder>/.mhoschema` (verified by running). A folder `check`
finds a misspelled field on any page and names the page; a page that does not parse is refused loudly, never
reported as all valid.

### `mio migrate typed-storage <file.mho> [--dry-run]` -- convert text columns to typed (writes)

A database whose tables were created before typed storage holds TEXT columns. This converts them to the types the
shapes declare (see the typed-storage section of `mohio-manual-part9-ch37-sqlite-to-postgres.md`). It CHANGES the
database.

```
$ mio migrate typed-storage app.mho --dry-run
$ mio migrate typed-storage app.mho
```

`--dry-run` lists the plan and every value that will not convert, and changes nothing. Run the dry run first, fix
any value it flags, then migrate.

### `mio writes <file.mho>` -- show what each write means (read-only)

Prints each write in THAT FILE, normalized, so you can see what it will change before you run it.

```
$ mio writes add.mho
  No writes in add.mho.
```

**Two limits to know, because this is the command a compliance reviewer most wants to trust, and
over-trusting it is the failure mode.** It is PER-FILE, not per-program: it does NOT resolve `include`s or
apply the journey, so a file that includes another reports only its OWN writes and omits the included file's
writes entirely (verified by running). And a write inside a CONDITION that may never run is listed flat, with
no marker distinguishing it from an unconditional write. So do not read `mio writes` on one file as the
complete set of writes a running program performs. This gap is tracked as `T1-WRITES-PER-FILE-NOT-PROGRAM`.

### `mio walk <file.mho>` -- inspect a data map / flow (read-only)

Shows a program's data map (`map <name> ... data ... map: done`). On a file with no map it says so:

```
$ mio walk hi.mho
  ! No data map found in this file.
    A map is declared with `map <name> ... data ... map: done`.
```

## Audit (verify is read-only; relay changes state)

The audit chain is a hash-linked, append-only record. These commands read or deliver it.

- **`mio audit verify <db>`** -- check each audit log's hash chain is intact (read-only):
  ```
  $ mio audit verify app.db
    v  fraud_audit_log  0 entries . chain intact
       head 000000...0000
  ```
- **`mio audit head <db>`** -- print each log's chain head, the value you publish as an anchor (read-only).
- **`mio audit verify-anchors <db> --anchors <f.json>`** -- check the live chain against externally published
  anchors, which catches TRUNCATION and wholesale REPLACEMENT that a plain verify cannot, both proven by
  running (read-only). It BUILT and works today. The whole anchoring shape: `mio audit head` prints a head, a
  human anchors it wherever they trust, an external party holds it, and `verify-anchors` checks the live chain
  against the anchors handed back. What is unbuilt is only the automated publish-and-sign step in between (a
  bank would want to control that anyway), so anchoring is NOT "simply unbuilt": the verification half exists.
  One caveat the command prints, worth heeding: it compares heads and TRUSTS the anchors as authentic; it does
  NOT verify their signatures, which is the caller's job upstream.
- **`mio audit relay <db>`** -- deliver audit records a crash left owed. This CHANGES state (it completes
  deliveries), so run it deliberately, not to inspect. It never reports a caught-up trail while a record is still
  owed: it counts every envelope it leaves owed (a delivery that failed, or an owed log name the program does not
  declare), reports `N owed, delivery failed: <reason>` (after any other-app note), and exits 1. A run that
  delivers everything exits 0. Every `mio audit` message shows the database URL with its password masked.

## Operations and tooling

- **`mio warmup`** -- pre-build the parser cache so the first real run is not slow (writes a cache, not app
  data). See the next section for what shipping the cache buys.
- **`mio schedule <run-due|list|watch> [file]`** -- `run-due` runs any scheduled tasks that are due (changes
  state); `list` and `watch` are read-only. `--interval <n>` sets the watch interval. **`watch` is a FOREGROUND
  development ticker** -- it stays running and fires due tasks on the interval, which is right for local
  development but not for production. In production, call `run-due` from an EXTERNAL scheduler (cron, a platform
  scheduler) rather than depending on a long-lived `watch` process.
- **`mio translate <file.mho> --to <lang>`** -- rewrite a program's natural-language keyword layer into another
  language, writing a new file (identifiers, strings and protected namespaces are left unchanged):
  ```
  $ mio translate hi.mho --to klingon
    [translate] written to: hi_klingon.mho
    [translate] identifiers, strings, and protected namespaces unchanged
  ```
- **`mio harvest [--output f.json]`** -- extract the current word inventory from the grammar (for langmaps); an
  internal/advanced tool. Writes the output file when `--output` is given.
- **`mio generate <artifact>`** -- generate artifacts (for example training data); internal/advanced.
- **`mio install-hooks`** -- install git pre-commit hooks. This changes your git setup; run it only when you
  mean to.
- **`mio version`** and **`mio help`** -- print version information and the usage summary (read-only).

## The three things that cost people time

### `mio serve <dir>` vs `mio serve <file>`

Covered above and worth repeating: the directory form serves the application with its cascade; the file form
serves one page. If your app looks half-broken with routes missing, you probably served a file. Serve the
directory.

### `DATABASE_URL` wants a bare file path for SQLite, not a URL form

Two lanes hit this. For SQLite, `DATABASE_URL` is a plain path or `:memory:`, NOT a `sqlite:///...` URL:

```
DATABASE_URL=/data/app.db     # correct
DATABASE_URL=:memory:          # correct (throwaway, for a test)
DATABASE_URL=sqlite:///data/app.db   # WRONG -- fails at runtime with "unable to open database file"
```

A connection declared in a journey is only as good as the value in the environment.

### `mio warmup`, and what shipping a cache buys

The first parse is slow because the grammar has to be compiled; `mio warmup` does that ahead of time and caches
the result, so the next `mio check` or `mio serve` starts warm. Shipping the cache with a deployment turns a long
cold start into a short warm one: the Stage-10 program checks in about **1.6 seconds** with a shipped cache
against about **73 seconds** cold.

**When to run it:** after you change files (to re-cache them), before you serve, and as a step in a deployment or
CI build, so the cost is paid before anyone is waiting on it.

**It moves the parse cost earlier, it does not remove it.** The grammar still has to compile and each file still
has to parse; `mio warmup` just does that work at warmup time instead of on the first real request. So warming
helps only the run that follows it, and a file changed after a warm needs warming again. The full recipe for
building and shipping the cache is `Docs/guides/shipping-the-parse-cache.md`; it is not duplicated here.

## The development loop

A few facts about how the tools work together save real time while you build. Each was established by running.

- **`mio warmup` once, after a cold start.** It compiles the grammar ahead of time so the next `mio check` or
  `mio serve` starts warm. Run it once (or ship its cache), not on every edit.
- **`mio serve` already reads and compiles every file on startup.** Starting a folder server scans the folder,
  compiles each `.mho`, and reports any error per route before it listens (verified: "Scanning ... Routes
  discovered ... [per-file result]"). So a FULL `mio check --all` immediately before every `mio serve` doubles
  the work: serve is about to compile the same files anyway. Just serve, and read the errors it prints.
- **Run `mio check --all` when you are VERIFYING A CHANGE,** not before every start. It compiles every `.mho` in
  the tree, each independently, and is the right tool when you want a clean pass over the whole project after an
  edit, or in CI. For the inner loop, serving and reading its output is enough.
- **The `.mho.cache` files beside your `.mho` files are the parse cache,** written automatically so a reparse of
  an unchanged file is fast. Leave them. Deleting them is only for a DELIBERATE COLD verification, when you want
  to prove a file parses from scratch with no cached tree; the next check or serve simply rebuilds them.

## Governance: `mio sector` (read-only)

`mio sector` answers what governance is in force. It changes nothing; it reads profiles and reports. Two forms.

**`mio sector` (no file)** lists the profiles on the search path, the known certified sectors that have no local
profile here, the search path itself, and the deployment's trust settings, warning loudly when no signing keys
are loaded (because every certified profile then fails closed):

```
$ mio sector
  Sector profiles on the search path
    demo-financial     community   v0.1   .../sector-demo-financial.mho
    ...
  Known certified sectors with no local profile here
    education, financial, government, healthcare, insurance, legal, logistics, science
  Deployment trust settings
    WARNING  Trusted signing keys (MOHIO_SECTOR_TRUSTED_KEYS): 0 loaded -- every .sector fails closed
```

**`mio sector <file.mho>`** reports each sector the program names, with its tier, version, signature result and
licence position:

```
$ mio sector app.mho
  app.mho names: demo-financial
  demo-financial
    Tier:       COMMUNITY
    Version:    0.1
    Signature:  n/a (community profiles are unsigned)
    Licence:    free sector, no entitlement required
    Status:     WOULD APPLY
```

**The boundary it states about itself, which is the honest part:** it reports what WOULD APPLY from the FILE'S
OWN `sector:` line. It does NOT resolve a journey spine in a parent folder, which can TIGHTEN the sector under
the cascade; where such a folder journey exists it says so ("It may tighten the sector under the cascade; this
command does not resolve that") rather than pretending to know the final answer. So `mio sector <file>` tells
you the file's own claim, not the assembled application's.
