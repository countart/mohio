<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Upgrading to 6.0.2

**Most 6.0.1 programs run unchanged on 6.0.2.** A few forms now refuse, and a few behaviours changed for an app
that runs under a sector or deploys with protected data. Each refusal below names its fix, and `mio check`
across your app finds all but one of them (the text-measure item says which). Every item was established by
running the same program on 6.0.1 and on 6.0.2.

## Breaking changes

1. **A sector profile carrying an `operation rules` block now refuses at load.**
   - Before: a profile could hold `operation rules for sector "x"` with `any operation "..."` rules, and a
     program under that sector loaded it.
   - After: `mio check` and `mio run` refuse the profile, naming the profile file and the line the block starts
     on. The block was removed from sector profiles: `operation` belongs to a `mioconnect` connector only.
   - The fix: delete the `operation rules ... operation: done` block from the profile. To govern what a field may
     do, classify it under the profile's `field types`; protected data is kept from leaving by its tags
     (`[pii]`, `[phi]`, `[pci]`) on every mail, HTTP and connector call, with or without a profile.

2. **`idempotent by` is retired; write `once per`.**
   - Before: `idempotent by request.header "Idempotency-Key" keep for 24 hours`.
   - After: the old spelling refuses at check, naming each line and its swap. Only the words changed; the route
     still answers a repeated request with the same key from its first answer.
   - The fix: `mio fmt --write <file>` makes the swap for you. Written by hand:

     ```mohio
     connect db from env.DATABASE_URL
     shape Pay
         amount as int
     shape: done
     listen for sh.Pay at /pay
         once per request.header "Idempotency-Key" keep for 24 hours
         give back [201] "ok"
     listen: done
     ```

3. **Under any named sector, a route that moves value or calls out needs an `Idempotency-Key`.**
   - Before: a write route with no idempotent line earned a check warning and ran every request.
   - After: under any `sector:`, a route that writes an amount (directly, through a task, by adding to one, or
     by raw `sql`) or calls an outside service is idempotent with no line written. A request with no
     `Idempotency-Key` header answers 400, naming the header and the sector; a repeat with the same key gets
     the first answer; the same key with a different body answers 422. Keys are kept 24 hours by default.
   - The fix: have every caller send an `Idempotency-Key` header. A route that genuinely cannot take one (the
     provider deduplicates by its own reference, say) opts out with `not idempotent reason "<why>"` inside the
     route. With no sector, nothing is automatic.

4. **Protected data on SQLite refuses to boot on a deploy.**
   - Before: an app with a tagged field, or under a sector, booted and served on SQLite anywhere.
   - After: when the deploy marker is set (`MOHIO_BUILD_SHA` or `MOHIO_LOCAL_DISK_EPHEMERAL`, which a deploy
     pipeline sets), `mio serve` refuses to start and names the tagged fields. A local `mio run` and `mio serve`
     are unchanged; `mio serve --dev` says once that SQLite is the development tier.
   - The fix: connect the deployed app to a server database (`connect db as postgres from env.DATABASE_URL`, or
     MySQL, MariaDB, Oracle, MongoDB).

5. **A shape `unique` on an existing table is a reviewed step on a deploy.**
   - Before: `unique` on a shape field over a table that already existed was not enforced.
   - After: in development the UNIQUE is added once the declarations load, or refused naming the repeated values
     and how many; on a deploy, `mio serve` refuses to start and names the step. A table that does not exist yet
     is created with the UNIQUE either way.
   - The fix: run `mio migrate add-unique <field> on <table>` against the deployed database, then start the app.

6. **`env`, `secret` and `hide` are reserved; none can be a variable name.**
   - Before: `env 5` or `secret "x"` checked clean and ran, and `hide` was an ordinary name.
   - After: each refuses at check and at run. `env.X` reads the environment and `secret.X` the secret store, so
     the bare words cannot also be names; `hide` is the `[hide]` tag.
   - The fix: rename the value for what it holds (`env_value`, `api_secret`, `hidden_count`).

7. **Lists are measured with `.count`, text with `.length`.**
   - Before: `.length` on a list failed only at run, and `.size` or `.count` on text answered.
   - After: `.length` or `.size` on a list refuses at check, naming `.count`. `.size` or `.count` on text refuses,
     naming `.length`; **`mio check` does not catch this one yet**, it refuses when the line runs.
   - The fix: `items.count` for the number of items in a list, `name.length` for the length of text:

     ```mohio
     items as list "a", "b"
     show items.count
     name "Aria"
     show name.length
     ```

8. **A text operation that starts its own line refuses.**
   - Before: a line beginning with a text operation (`uppercase`, `trim`, `before`, `after` and the rest) was
     silently joined to the value on the line above, so `y x` followed by `uppercase` checked clean and read as
     one assignment.
   - After: check refuses it, naming the operation and the line it would have joined.
   - The fix: write the operation on the line of the value it works on, and give a new value a name first.

9. **A quoted template name in a send refuses.**
   - Before: `template "welcome"` inside `miomail.send` parsed and was dropped. The mail went out with no subject
     and an empty body, and nothing said why.
   - After: check refuses it and shows the working form.
   - The fix: declare the template once and name it without quotes in the send, filling its slots with
     `inject <value> into <slot>`. The template sees only what the send injects:

     ```mohio
     template welcome
         subject "Welcome, {{ who }}"
         body "Glad you came, {{ who }}."
     template: done
     member_name "Bo"
     miomail.send
         to "bo@example.test"
         template welcome
         inject member_name into who
     miomail.send: done
     ```

## Behaviour that changed

- **`notify` sends by email.** `notify ... via email` with a `to` line and a `template` (or a `subject` and a
  `message`) sends exactly as `miomail.send` would. A notify on any other channel, or with no `via` line, now
  refuses at check; in 6.0.1 it checked with a warning and stopped the program when reached.
- **`[hide]` hides a value in every output.** A field tagged `[hide]` shows `[hidden]` on a page, in a response, a
  log and an error, the owner included. Tags stack as a union: `[pii, hide]` is sealed as `[pii]` and hidden too.
- **A backlog of owed audit records stops regulated writes.** When 1000 audit records are owed (set with
  `MOHIO_AUDIT_OWED_REFUSE_COUNT`), a regulated write refuses until the trail catches up; a warning starts at 80
  percent. This sits beside the existing age limit.

## How to find them

Run `mio check` across your app (`mio check --all`, or `mio check` on the folder) and `mio fmt --write` for the
`once per` swap. Every breaking change above refuses at check with its fix, except text measured with `.size` or
`.count` (item 7), which refuses when the line runs; a search for `.size` and `.count` on text values finds it.
Items 3, 4 and 5 are about how the app is served and deployed rather than how it is written: check that your
callers send `Idempotency-Key`, that a deployed app with protected data uses a server database, and that a new
`unique` on an existing table has had its `mio migrate add-unique` step.
