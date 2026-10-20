<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Upgrading to 6.0.0

**This is a major release because it changes how existing programs are written, so read this page before
upgrading.** Most 5.1.0 code still runs unchanged on 6.0.0, but a few forms CHANGED and now refuse (above all, a
POST handler is now `listen for sh.X`, not `new sh.X`), so a `mio check` across your app finds them for you. Each
refusal names its fix. For everything new, see `mohio-manual-whats-new-6.0.0.md`.

## Check your app for these

A short upgrade checklist. Each is covered in full below, and a `mio check` across your app finds the ones it can.

1. **Multi-condition lookups.** Every `match` and `where` in a lookup must now hold. A lookup that relied on a
   partial match now returns nothing.
2. **An early `give back`.** The first `give back` reached ends the request, in every branch.
3. **Reading request data.** It is read by the shape's name exactly as declared (`ShapeName.field`); run
   `mio fmt --write` to convert the old lowercase form.
4. **Pre-6.0.0 uploads.** Files uploaded under `[pii]` or `encrypted` before 6.0.0 stay stored unencrypted until
   re-uploaded; new uploads are sealed.
5. **`hold`.** A `hold` keeps its value across a person's requests until `release`; `release` is safe to call when
   nothing is held.

## Behavior that changed

A few 6.0.0 changes are not a renamed form that refuses, they change what a program that still parses
DOES. These are the ones to read closely, because an app can upgrade clean at `mio check` and still
behave differently at run. Each was established by running.

1. **A `give back` ends the request, in every branch.**
   - Before: a `give back` inside a nested block (a named `check`, a typed task, a transaction, an
     `on.failure`) could fall through, so execution carried on past it and the LAST `give back`
     reached was the response.
   - After: the FIRST `give back` reached ENDS the request; nothing after it runs. A refusal inside a
     transaction also rolls the transaction back, so a write made before the refusal does not survive.
   - What to check: any handler that deliberately did work AFTER an early `give back` (a refusal
     followed by a compensating write). That work no longer runs; move it ahead of the `give back`.
     See `mohio-manual-part8-ch29-listening-for-requests.md` and Chapter 43.
   - Before and after, the same handler (verified by running):
     ```mohio
     shape Order
         amount as int
     shape: done
     listen for sh.Order at /order
         check Order.amount
             when Order.amount is more than 100
                 give back [200] "first wins; the request ends here"
             otherwise
                 give back [400] "too small"
         check: done
         give back [200] "never runs now"
     listen: done
     ```
     Before 6.0.0 the trailing `give back [200] "never runs now"` could become the response (the LAST
     `give back` reached won). Now the `when` branch's `give back` ends the request, and the trailing line
     never runs.

2. **Every `match` (and `where`) condition is ANDed.**
   - Before: a lookup could match on only SOME of its conditions.
   - After: all conditions must hold; the lookup returns a row only when every one matches, on
     `find`, `retrieve`, `remove` and `modify`, and on every engine.
   - What to check: multi-condition lookups. One that silently returned extra rows now returns fewer,
     and one that leaned on a dropped condition may now come back empty. See
     `mohio-manual-part7-data-and-persistence.md`.
   - Before and after (verified by running, with Alice stored in NYC):
     ```mohio
     retrieve person from db.people
         match name to "Alice"
         match city to "LA"
         when person is empty
             show "no match: both must hold"
         otherwise
             show ("found " & person.name)
     retrieve: done
     ```
     Before 6.0.0 this could still return Alice, matching on `name` alone. Now both `match` lines must
     hold, so with her city stored as NYC it returns nothing.

3. **The egress gate: protected data does not leave the app.**
   - Before: a `[phi]`/`[pci]`/`[pii]` value could reach an AI call, an outbound HTTP call, or a log,
     and a cookie, a redirect or a file write were not gated at all.
   - After: page responses mask (`[pii]` stays plain, as decided); application logs, the journey log,
     metrics and diagnostics mask all three; and an AI call, an outbound HTTP or connector call, an
     email, a cookie, a redirect (`Location`) or a file write carrying a protected field is REFUSED AT
     CHECK unless the value is masked or the record is sent with `stripped`. A local model at a
     literal loopback address, through `ai.connect`, is the one AI exception.
   - What to check: `ai.decide`/`ai.create`/`ai.agent` inputs and every `miohttp`/`mioconnect`/
     `miomail` call, and any value written to a cookie, a redirect (`Location`), or a file
     (`miofile`). Mask the value or send the record `stripped`. See `Docs/guides/compliance.md`.

4. **A secret reaches only its declared use.**
   - Before: a `secret.X` value could be shown, logged, stored, or sent.
   - After: a secret may reach ONLY a connector's credential or the `auth` of an outside call;
     anywhere else refuses at check, and masking is no way through. Storing one refuses too.
   - What to check: any `secret.X` used outside `auth`/a connector credential. To prove a secret is
     set, check that it exists rather than showing it. See `Docs/guides/compliance.md`.

5. **`unique` on a tagged field enforces on the fingerprint, and an existing table with duplicates is
   reported.**
   - Before: `unique` on an encrypted (`[phi]`/`[pci]`/`[pii]`) field had no plaintext to index, and
     adding `unique` to a table that already held duplicates could pass.
   - After: `unique` enforces on the field's deterministic blind index (composite included), so a
     duplicate protected value is refused; and adding `unique` to a table that already holds
     duplicates refuses to create the rule at all, naming the count, changing nothing.
   - What to check: tables with a tagged `unique` field that already contain duplicates. Remove the
     duplicates, or drop `unique`, before the upgrade. See `mohio-manual-part5-ch16-shapes.md`.

6. **A multi-write handler's response status decides commit or rollback.**
   - Before: a multi-write handler committed on a 2xx and rolled back on anything else.
   - After: a **2xx or a 3xx commits**, a **4xx or a 5xx rolls back** (and a throw rolls back). A handler
     that answered a 3xx (a redirect, say) after its writes now COMMITS them, where a strict non-2xx rule
     would have rolled them back.
   - What to check: multi-write handlers that answer a 3xx. A single write is still not transactional, so
     a lone write followed by a refusal still persists. See `mohio-manual-part8-ch29-listening-for-requests.md`
     and Chapter 43.

7. **A pathless handler inside a journey now refuses.**
   - Before: a handler written inside a `journey` with no `at /path` was accepted.
   - After: it refuses at check and at run, because a journey is not a page and the handler has no address of
     its own. A pathless handler at the top level of a file still binds to that file's page, unchanged.
   - What to check: any `request for`/`listen for` written loose inside a `journey` without `at /path`. Give
     it a path. See `mohio-manual-part8-ch29-listening-for-requests.md`.

8. **A nested `check ... against` must carry its own `check: done`.**
   - Before: a `check ... against` nested inside another block (for example `check password against ...`
     inside a `listen for`) could be left without its own closer.
   - After: it refuses at check, naming the line, because without its own `check: done` the enclosing closer
     is taken as this check's and the outer block runs on past its end:
     ```
     Line 12: `check Login.password against member.hashed_password` needs its own closer, `check: done`, at its own indentation. Its handlers end at line 17; without the closer, the next `check: done` below is taken as this check's, and the block that closer belongs to runs on past its own end and swallows what follows.
     ```
     The refused shape is the `check ... against` with its `on.success` / `on.failure` handlers but no
     `check: done` before the handler's `listen: done`. The corrected shape adds the closer at the check's own
     indentation:
     ```mohio
     check Login.password against member.hashed_password
         on.success
             grant role "member" for person member.id
             give back [200] "signed in"
         on.failure
             give back [401] "invalid email or password"
     check: done
     ```
   - What to check: every `check ... against` nested in a handler or another block. The placement is about
     correctness, not just parsing: the lines that must run ONLY on a match belong INSIDE the match. The
     sign-in, `grant role ... for person ...`, goes inside `on.success`, so a role is granted only when the
     password actually verified, never on the way past a check that was read as ending somewhere else.

## Renamed, now refusing

- **`new sh.X` as a handler is retired; listen for it instead.** A POST handler is now `listen for sh.X at
  /path` (closing with `listen: done`), not `new sh.X at /path` (`new: done`). The two behave identically, so the
  change is mechanical: swap `new sh.X` for `listen for sh.X` and `new: done` for `listen: done`, keeping the
  `at /path`. From 6.0.0 the compiler refuses `new sh.X` as a handler and shows the swap. `new` is not a way to
  declare a handler at all; the word is reserved for a planned condition inside a `listen for` that acts only on a
  genuinely new arrival (not built yet). `mio new` the CLI command, and `request for sh.X` (a GET handler), are
  unaffected.
- **Paging result fields.** `has_more`, `per_page` and `next_cursor` are retired. Use `page.more`, `page.size`
  and `page.cursor` on the result (for example `rows.page.more`). Reading an old name refuses at check and run,
  naming the replacement.
- **Agent result fields.** The underscored spelling `_agent_<name>_cost` (and its siblings) is retired. Read the
  dotted form on the agent's name instead: `researcher.cost`.
- **`ai.chain`** is retired. Declare providers with `ai.connect` (a chain, a single provider, or a custom one with
  an address and key).

## Retired, write it the plain way

- **`release.now` and `release.lock`.** A one-step release-and-reassign is gone, because it reads like a second
  `set`. Write two plain statements: `release name`, then `name value`.
- **The five state operators act only on a HELD variable.** `release`, `clear`, `replace`, `rename` and `forget`
  now refuse on a fluid (naked) variable, naming the fix: to change a fluid variable, just restate it
  (`name value`); to use the operators, `hold` it first. A 5.1.0 program that cleared or forgot a naked variable
  will be caught at check.

## Forms that now need a closer or a different spelling

- **`check ... against ...` takes its own `check: done`.** It used to have no closer; now an unclosed one refuses,
  because a statement written after it would otherwise disappear into its last `on.success` / `on.failure`
  handler. Add `check: done`, and when it is nested inside another `check`, close it before the outer one.
- **On the client, `hold` freezes; use a bare assignment for a value you re-capture.** If a 5.1.0 MioScript
  handler used `hold q = value` to re-capture a value on every keystroke, write the fluid bare form `q value`
  instead. `hold` is now reserved for a value that must not change until you `release` it.

## Rounding and numbers

These behaviours were corrected in 6.0.0. Every example here was run on the current build.

- **`round.to` rounds HALF AWAY FROM ZERO, on the exact decimal.** `2.675 round.to 2` is now `2.68` (it was `2.67`,
  because the old path used Python's `round`, which rounds ties to even and worked on a binary float). `-2.675
  round.to 2` is `-2.68`.
- **`round.to 0` means ZERO decimal places.** It now gives a whole number (`2.5 round.to 0` is `3`); it used to
  mean two places.
- **`round.up` goes AWAY from zero, `round.down` goes TOWARD zero, and both take an OPTIONAL places count.** Bare,
  they round to a whole number: `2.3 round.up` is `3`, `-2.3 round.up` is `-3`, `2.7 round.down` is `2`, `-2.7
  round.down` is `-2` (on a negative, the opposite of ceiling and floor). With `N`, they round to N places in the
  same direction: `2.341 round.up 2` is `2.35`, `2.349 round.down 2` is `2.34`. Any other rounding spelling
  refuses, naming the accepted forms (`round.to N`, `round.up [N]`, `round.down [N]`).
- **Over-precise money rounds half away from zero with a WARNING, and REFUSES under a regulated sector.** Assigning
  `1.005` to a `USD` field stores `$1.01` and warns that the extra precision was dropped; under a regulated sector
  profile the same assignment is refused rather than warned. Write the amount at the currency's precision.
- **Every number is exact.** Arithmetic and storage use exact decimals, never binary floats: `1 / 3` shows
  `0.3333333333333333333333333333`, `"2.50" as.dec` keeps `2.50`, and `as.json` serializes the exact value
  (`(1 / 3) as.json` is the same 28-digit number, not a float approximation).
- **Booleans read back as a boolean on every engine.** A `boolean` field saved `true` reads back as a boolean,
  not a string or a number.

## More changes in 6.0.0

Each was established by running on the current build.

- **A MongoDB multi-write handler refuses at run.** A handler that makes more than one write on MongoDB refuses,
  because a Mongo transaction around several writes is declared but not built (`T1-MONGO-PROGRAM-TRANSACTION`, it
  needs a replica set). It refuses at run, not at check, since the engine is known only from the live connection.
  Make the handler one write, use a `saga` with a `compensate`, or run it on Postgres, MySQL, Oracle or SQLite.
- **A table or column name is never taken from data.** An identifier is a literal you wrote, or it is refused: a
  dynamic table/column name (an interpolated identifier) does not parse, and a bare name after the dot is the name
  itself, never a variable lookup. Nothing to change unless you were attempting a computed identifier.
- **What `mio check` validated is what `mio serve` routes.** A declared handler is never silently gone at serve
  time, and two handlers on the same method and path refuse at check rather than one disappearing at run.
- **A `dec.N` field truncates an over-precise write, toward zero.** `1.239` into a `dec.2` field stores `1.23`,
  dropping the extra place rather than rounding it. A currency type still rounds half-up; a plain declared-scale
  decimal truncates.
- **On SQLite a `dec.N` column is now TEXT (exact), not INTEGER.** A declared-scale decimal is stored as exact
  text at its scale. A database written before this still reads back correctly, so there is nothing to migrate
  for reads; `mio migrate typed-storage` converts the columns when you want the native type.
- **The form guard token is bound to the session.** A CSRF token lifted from one session is refused in another,
  the same way a missing token is. You write nothing for this.
- **A `_`-prefixed upload path is never given a URL.** A folder whose name starts with `_` goes to the private
  store and is served by no address at all, reachable only by the app's own code.
- **`named NAME` as a capture word is retired; use `as NAME`.** `sign url for X as link` replaces `named link`,
  and the old form refuses at check, naming the fix. `as` is the capture word for the whole family.
- **`when not.empty`, `when is.empty` and `when not.found` with NO SUBJECT refuse at check.** The subject comes
  first (`when member not.found`); a test written before its subject is refused, naming the fix, because it has
  nothing to test and would fail at run.
- **A `validate` or `verify token` block with a handler needs its closer.** A `verify token ... as grant` block
  that carries an `on.failure` / `on.success` handler closes with `verify: done`; an unclosed one refuses, the
  same reason `check ... against` now needs its closer.
- **A connection failure names the declared variable.** A failed `connect` says which connection failed and how
  it was declared, for example "Database connection failed for 'db' (declared as postgres from env.BADURL)",
  rather than a bare driver error.
- **An in-list filter works: `where status active, pending`.** Matching a field against a list of values never
  worked in 5.1.0; it does now (it is evaluated correctly, though it does not yet push down as a SQL `IN`, tracked
  as `T1-IN-FILTER-PUSHDOWN`).

## Security note for 5.1.0 users

_Placeholder. The build lane is still establishing the facts for this note; it will be filled in once they are
confirmed by running._

## How to find them

Run `mio check` across your app (`mio check --all`, or `mio check` on the folder). Every item above refuses at
check with a message that names the replacement, so the upgrade is a read-the-error, apply-the-fix pass rather
than a hunt.
