<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# What's new in 6.0.0

Every user-facing change since 5.1.0, in one place, each linking to the chapter that documents it in full. Each
item below was verified by running before it was written up.

## Check your app for these

A short upgrade checklist (the full upgrade guide is `mohio-manual-upgrading-to-6.0.0.md`):

1. **Multi-condition lookups.** Every `match` and `where` in a lookup must now hold. A lookup that relied on a
   partial match now returns nothing.
2. **An early `give back`.** The first `give back` reached ends the request, in every branch.
3. **Reading request data.** It is read by the shape's name exactly as declared (`ShapeName.field`); run
   `mio fmt --write` to convert the old lowercase form.
4. **Pre-6.0.0 uploads.** Files uploaded under `[pii]` or `encrypted` before 6.0.0 stay stored unencrypted until
   re-uploaded; new uploads are sealed.
5. **`hold`.** A `hold` keeps its value across a person's requests until `release`; `release` is safe to call when
   nothing is held.

## Databases and querying

- **Pick the engine from the address.** `connect db from env.X` with no `as` reads the engine from the address
  (`postgres://` connects Postgres, a file path connects SQLite). An `as` that conflicts with the address refuses
  at run rather than connecting to the wrong database. See the databases section of
  `mohio-manual-part7-data-and-persistence.md`.
- **Oracle is supported,** verified on Oracle AI Database 26ai; 19c verification follows after the tag. Connect
  with `connect db as oracle`; the data verbs, the connection pool and the audit trail all run on Oracle. Same
  chapter, and `Docs/guides/running-mohio-on-your-database.md`.
- **Native joins, in full.** `with` (left join) and `with.required` (inner join) pull a parent and its related
  rows in one query, with a per-parent `up to` and paging that counts parents. Now also: side-by-side lists
  (several `with` blocks on one find), nested chains (a `with` inside a `with`), joins on `retrieve`, joins on
  MongoDB (`$lookup`), and `with.all`, which returns the matched parents and the unmatched orphans as two separate
  lists (`rows` and `rows.unmatched`, with `rows.page.unmatched.count` / `.start` under paging). The boundaries,
  verified: a nested `with.all` is refused, `with.all` alongside a sibling `with` is refused, and MongoDB supports
  `with` and `with.required` but not `with.all`. See the joins section of
  `mohio-manual-part7-data-and-persistence.md`.
- **Paging result words.** A paged query's facts live on `<result>.page`: `count`, `current`, `total`, `more`,
  `size`, `next`, `prev`. See the querying section of the same chapter.
- **Pushdown landed.** On the SQL engines, numeric range and compare filters, text filters, match-any / no-match,
  ordering, paging and projection now run IN the database. Datetime filters and all of MongoDB still run in Mohio
  memory. Two operational knobs, off by default: `MOHIO_FIND_MAX_ROWS` and `MOHIO_DB_STATEMENT_TIMEOUT_MS`. Join
  fields get an index when their table is created. The full split is in `Docs/guides/running-mohio-on-your-database.md`.
- **`text.long`.** Unlimited text on every engine (CLOB on Oracle, LONGTEXT on MySQL), refused as a unique,
  exact-match or sort key. See the database guide.
- **Typed storage landed.** A table a shape declares (`<name> as table` with typed fields) is created with real
  typed columns, so a reporting tool sees native values, not text. A table no shape describes still stores TEXT.
  `mio migrate typed-storage <file.mho>` converts an older text-stored database to the declared types
  (`--dry-run` lists the plan first). See `mohio-manual-part9-ch37-sqlite-to-postgres.md` and the database guide.
- **`paginate by` takes a value.** `paginate by request.page default N` reads the page from the request and falls
  back to N when it is missing; an invalid page (not a whole number, or below one) fails loud and is never
  clamped. See the querying section of `mohio-manual-part7-data-and-persistence.md`.

## Dates and times

- **Whole-day date filters:** `is between A and B`, `is after`, `is before`, the inclusive `is not after` /
  `is not before`, and `is.in` a period (`last N days`, `this month`, `today`, and the rest). See
  `mohio-manual-part2-dates-and-times.md`.

## MioScript (the browser)

- **New client verbs:** `reset`, `alert`, `confirm` (a gate), `copy`, media `play` / `pause` / `stop` and
  `listen for end of`, timers `every ... as NAME` / `stop NAME`, `validate as strict` (auto-focus on failure),
  and `share` in all its forms: bare (current page), `share #element`, `share "address"`, `share <held-value>`,
  each with an optional `on <destination>`. See the "New in 6.0.0" section of `mohio-manual-part5-ch24-mioscript.md`.
- **Client variables: fluid, held, and the five operations.** A bare assignment (`draft value`) is the fluid
  client variable; `hold` freezes a value; and `release` / `clear` / `replace` / `rename` / `forget` act on a held
  value, mirroring the server. See the Client variables section of the MioScript chapter.
- **Row identity.** A rendered list stamps each row with its identity, so a click tells the server which row it
  hit; read it on the server as `request.<loop variable>`. Same chapter, "Acting on the right row".

## Pages, requests and security

- **Social previews.** A page's `title`, `describe`, and the new `image` declaration write the Open Graph and
  Twitter preview tags, so a shared link renders a card. See `mohio-manual-part5-pages-and-previews.md`.
- **`verify signature` on `request for` handlers.** Webhook signature verification runs on a `request for sh.X`
  handler as well as a `listen for sh.X` one. See `mohio-manual-part8-ch31-webhook-signatures.md`.
- **Page classification tightens only.** Across a folder cascade a nearer journey can only make a path stricter
  (`public` < `private` < `authorize` < `hidden`), never looser, enforced at assembly. See the journeys chapter.
- **Sector profiles refuse unenforceable rules.** A profile naming a `security requirements` rule nothing enforces
  refuses to load, rather than claiming a control it does not deliver. See `Docs/guides/compliance.md`.
- **Every password check is audited,** and a shape field's `default` now fills on the request path. See
  `Docs/guides/password-login-howto.md` and the shapes chapter.

## AI

- **`ai.connect` providers.** Declare a provider chain, a single provider, or one Mohio does not ship, with an
  address and key; built-in providers need only a name and an exact model string. See
  `mohio-manual-part1-ch3-setting-up-ai.md`.
- **Audit records carry tokens and cost,** each with a basis sentence, priced from the model that actually
  answered. See `mohio-manual-part1-ch4-app-that-reasons.md`.
- **`ai.decide` enforces a journey cost ceiling** before the provider call; a breach routes to `on.failure` and
  audits `ai_ceiling_refused`. `ai.create` the same. Same chapter.
- **Agent result fields are dotted:** read `researcher.cost` and its siblings; the old underscored spelling is
  retired. See `mohio-manual-part7-ch36-ai-create-and-agent.md`.
- **An AI failure says where it failed.** In an `ai.` operation's `on.failure`, `failure` carries
  `failure.provider` (who was asked), `failure.stage` (`call`, `response validation`, or `confidence`) and
  `failure.error` (the provider's HTTP status when there was one), so a handler can tell a bad key from an outage
  from an unusable answer. See `mohio-manual-part1-ch3-setting-up-ai.md`.
- **AI and documents.** The `ai.*` primitives read TEXT; they do not read a document or an image, so passing an
  uploaded file to an `ai.*` call refuses at check. Reading a document or image is `mioai.extract`, a locked
  design (June 12) that is designed and not yet built; weigh text and extract it by other means until it lands.
  Separately, a dotted `mioai.*` call refuses in the current build (its message calls the word retired, pointing
  to `ai.create` / `ai.decide`). See `mohio-manual-part1-ch3-setting-up-ai.md`.
- **`using` reaches only the chain it names.** An AI verb's `using <chain>` reaches only that `ai.connect` chain;
  a `using` that names a chain nothing declared refuses at run with a 503 (not registered), never a silent
  fallback. Same chapter.

## Integrations and files

- **`mioconnect` timeout and retry.** A connector `timeout N seconds` bounds every call, and an operation's own
  `timeout` overrides the connector's; a timeout fails into `on.failure` with the failure detail. `retry N times`
  retries on a genuine connectivity failure only, never on an HTTP error response. The idempotency and
  indeterminate-result layer is still planned (`T1-CONNECTOR-RELIABILITY`). See
  `mohio-manual-part9-ch40-connector-calls.md`.
- **An uploaded file whose row never lands is removed, and the removal is audited.** A write that fails, or a
  transaction that rolls back, cleans up the object it would have orphaned (`FILE_CLEANUP`), and a refused removal
  is recorded as `FILE_CLEANUP_FAILED` rather than left silent. A committed row keeps its file. See
  `mohio-manual-part9-ch38-transactions-and-audit.md`.
- **`mio schema` takes an app folder.** `mio schema generate|check|show` now accept an app folder as well as a
  single file, resolving the journey and every include and writing one manifest at `<folder>/.mhoschema`. See
  `mohio-manual-cli-reference.md`.

## Security, protected data and the request lifecycle

Each was verified by running.

- **A `give back` ends the request.** The first `give back` reached ends the request in every branch (a named
  `check`, a typed task, a transaction, an `on.failure`); nothing after it runs, and a refusal inside a
  transaction rolls it back. See `mohio-manual-part8-ch29-listening-for-requests.md` and Chapter 43.
- **Every `match` and `where` condition is ANDed,** on every verb and engine: a lookup returns a row only when
  all of its conditions hold. See `mohio-manual-part7-data-and-persistence.md`.
- **The egress gate: protected data does not leave the app.** Page responses mask (`[pii]` stays plain, as
  decided); application logs, the journey log, metrics and traces mask all three tags; and an AI call, an outbound
  HTTP or connector call, an email, a cookie, a redirect or a file write carrying a protected field refuses at
  check unless the value is masked or the record is sent with `stripped`. One gate (`mohio_egress`) decides every
  boundary, and a build-time boundary test fails if any outbound path skips it. A local model at a loopback
  address is the one AI exception. See `Docs/guides/compliance.md`.
- **Secrets reach only their declared use.** A `secret.X` may be a connector's credential or an outside call's
  `auth` and nothing else; every other boundary refuses at check, masking included, and storing one refuses too.
  Same guide.
- **`unique` on a tagged field enforces on the fingerprint,** composite included, and adding `unique` to a table
  that already holds duplicates reports them and changes nothing. See `mohio-manual-part5-ch16-shapes.md`.
- **Joins and `with.required` work on a protected key** through the blind index, so an encrypted key still joins
  correctly. See `mohio-manual-part7-data-and-persistence.md`.
- **A protected value is bound to its place.** Every `[phi]`, `[pii]` or `[pci]` write is sealed to its table,
  column and row id (associated data). A ciphertext moved to another row, column or table refuses loud as
  `encryption.wrong_place` instead of reading back as the new row's value, and a read under the wrong key refuses
  as `encryption.cannot_decrypt` rather than looking like a moved value. Values written before 6.0.0 (`enc:v1:`)
  still open unchanged. See `Docs/guides/pci-masking.md`.
- **Values of different kinds never compare or combine silently.** A comparison, a `where`/`match` filter, or
  arithmetic between two different kinds (a number and a true-or-false, a date and a number, money and anything
  that is not money of the same currency) refuses rather than coercing one side. See
  `mohio-manual-part3-ch11-decisions.md` and `mohio-manual-part2-money.md`.
- **An action verb written as `VERB value` refuses at check,** instead of silently becoming a variable named
  after the verb that never runs (`stop 5`, `add 5`, `check 5`). A word that is not a verb still assigns. See
  `mohio-manual-why-mohio-refuses.md`.
- **Auditing covers the empty case and the system case.** A protected search that returns nothing is still
  audited; `flow` and `walk` audit and mask protected values; scheduled work records a system actor. See
  `mohio-manual-part9-ch38-transactions-and-audit.md`.
- **`require person`, and separate 401 and 403 refusals.** `require person` gates on anyone signed in; an
  unauthenticated caller gets 401, and a signed-in caller lacking the role gets 403 naming the role needed and the
  roles held. See `mohio-manual-part8-ch29-listening-for-requests.md`.
- **A browser 422 re-render clears protected fields,** and a password field with no `format "password"` earns a
  check-time warning. See `mohio-manual-part5-ch16-shapes.md`.
- **`now()` never repeats or goes backward** within a run. See `mohio-manual-part2-dates-and-times.md`.
- **Audit sink grade.** A hash chain is tamper-evident, not tamper-proof; a regulated deployment must bind an
  append-only or WORM sink, and a local SQLite sink is flagged below grade when a row is altered. See Chapter 39.
- **The audit relay backs off and alerts once.** While the audit sink is unreachable the automatic drain doubles
  its wait up to a cap, and one alert and one incident are raised per outage, not one per refused record. See
  Chapter 39.

## Transactions, routing and identifiers

Each was established by running.

- **A multi-write handler's response status decides its fate.** Inside the one transaction a handler runs in, a
  2xx or a 3xx commits and a 4xx or a 5xx rolls back, so the status the caller receives and the rows that were
  kept always agree. A single write is not transactional: outside a transaction a lone write followed by a refusal
  persists. See `mohio-manual-part8-ch29-listening-for-requests.md` and Chapter 43.
- **A MongoDB multi-write handler refuses at run.** A Mongo transaction around several writes is declared but not
  built (`T1-MONGO-PROGRAM-TRANSACTION`, it needs a replica set); the handler refuses at run, naming the fix (one
  write, a `saga`, or another engine). Same chapter.
- **What `mio check` validated is what `mio serve` routes.** A declared handler is never silently gone at serve
  time; two handlers on the same method and path refuse at check. See `mohio-manual-cli-reference.md`.
- **A table or column name is never taken from data.** An identifier is a literal or it is refused: a dynamic
  (interpolated) identifier does not parse, and a bare name after the dot is the name itself. The relay enforces
  the same rule, and a relay not told which app it is fails rather than reporting a caught-up trail. See
  `mohio-manual-part7-data-and-persistence.md` and Chapter 39.
- **A pathless handler binds to its page; a journey requires a path.** A `request for`/`listen for` with no
  `at /path` answers at its file's own page; written loose inside a `journey` it refuses, because a journey is not
  a page. See `mohio-manual-part8-ch29-listening-for-requests.md`.

## Request binding, failures, uploads and joins

Each was established by running.

- **Declared types bind at every binding point, and money is exact.** A request field arrives as its shape's
  declared type wherever it binds; `USD`/`dec.N` are exact decimals (no float drift), and extra money precision
  rounds half away from zero with a warning (refuses under a regulated sector). See
  `mohio-manual-part5-ch16-shapes.md`.
- **An empty optional number stays empty, never 0.** A sent-empty optional number binds as empty, a sent `0`
  stays `0`, and an omitted key fails loud rather than becoming 0. Same chapter.
- **A whole-record save refuses an undeclared field.** `save all ... from <record>` carries only what the shape
  describes; an extra field is refused and named, with no silent mass-assignment (today it answers 500, where a
  4xx is the intended tightening). Same chapter.
- **A server failure answers a reference, not the detail.** An unhandled 500 returns one plain sentence and a
  reference id; the cause is only in the log under that id. See `mohio-manual-part8-ch29-listening-for-requests.md`.
- **Page code gets the same request and session a handler gets.** A page reads `request.*` and `session.*`
  exactly as a handler does. See `mohio-manual-part5-pages-and-previews.md`.
- **Uploads: read, hash, save, reveal.** An upload field reads as a file through `miofile.read`, has a SHA-256,
  saves by its stored name, and is revealed to an authorized reader by that name; an upload field must declare
  `accept` and `max size`. The FOLDER decides visibility (`public/` public, a `_`-prefixed folder never served,
  else private), never the filename; keeping customer identifiers out of a path is advice, not an enforced check.
  Uploads default to `~/.mohio/uploads` (under the user home, outside the project tree), overridable by
  `MOHIO_UPLOAD_DIR`. See `mohio-manual-part6-ch26-durable-uploads.md`.
- **A saga's refusing step undoes the earlier steps.** Inside a saga, a step that refuses fails the saga and
  compensates the completed steps in reverse order; the caller gets the refusal, not a false success. See the
  sagas section of `mohio-manual-new-chapters.md`.
- **A required join holds regardless of the joins beside it.** `with.required` keeps one tenant's rows out of
  another's no matter what sibling joins sit beside it; paging that shape (a required join beside another join)
  refuses today (`T1-JOIN-REQUIRED-SIBLING-PAGING`). See `mohio-manual-part7-data-and-persistence.md`.
- **MongoDB and the audit trail.** A regulated write on MongoDB refuses at run today, because the trail is
  SQL-only and MongoDB support is declared but not built (`T1-MONGO-AUDIT-TRAIL`); untagged apps are unaffected.
  Pending (ruled 2026-10-09, not yet landed): a tagged or sector app will refuse at CHECK too. Run a regulated
  app on Postgres, MySQL, Oracle or SQLite. See Chapter 39.

## Changed or retired

- **Paging field names renamed.** `has_more`, `per_page`, and `next_cursor` are retired in favour of
  `page.more`, `page.size`, and `page.cursor`. Reading an old name refuses at check and run, naming its
  replacement.
- **`release.now` and `release.lock` are retired,** each refusing with a teaching message: write `release name`
  then `name value`.
- **The underscored agent result spelling** (`_agent_<name>_cost` and siblings) is retired in favour of the dotted
  `<name>.cost`.
- **`ai.chain` is retired,** replaced by `ai.connect` (same chapter as above).

## Known limitations in 6.0.0

These are known gaps in 6.0.0, each with the release that closes it, so you can plan around them:

- **No per-account login rate limit yet.** 6.0.0 does not throttle repeated login attempts per account; that
  backstop arrives in 6.0.1.
- **Datetime filters run in Mohio, not pushed to the database.** A declared `datetime` is stored (normalized to
  UTC) and a `dec.N` decimal is stored and compared at its scale, so typed columns reach the column in 6.0.0. What
  remains is PUSHDOWN: a date filter is still evaluated in Mohio after the rows are fetched, not pushed to the
  database (`T1-DB-TYPED-STORAGE`); numeric, text, ordering and paging filters do push down.
- **Protected fields need an `id`-keyed table.** A `[phi]`, `[pii]` or `[pci]` field can only be written to a
  table keyed by `id`; a composite-key or other non-`id` table refuses as `protected_field_needs_id_key` and keeps
  nothing. Composite-key and non-id tables gain protected fields in 6.0.1.
- **SQLite audit loss under heavy contention.** The audit path is fail-closed (no governed row is committed
  without its audit envelope), but under heavy single-file contention a SQLite run can lose a security-plane
  record that sits outside the envelope; the loss is alerted loudly, never silent. Hardening arrives in early
  6.0.1; for a regulated trail, bind an append-only or WORM sink.
- **Bulk write performance.** Writing many rows in one go is not yet optimized; that work is its own later
  release, after 6.0.1.

## What comes next

The near-term order is 6.0.1 for `mioauth` (the per-account login rate limit and the auth surface), then the
write-performance release, then BridgeMap.
