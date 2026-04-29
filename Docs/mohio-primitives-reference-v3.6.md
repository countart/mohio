# Mohio Language — Primitives & Modifiers Reference
## Version 3.6 · April 2026 · Particular LLC

*The authoritative quick reference for the Mohio language surface. For full specifications, design decisions, and compiler internals — see the Language Design Document (LDD). This document is derived from it.*

---

## The Mohio Arc

Every significant Mohio program follows three stages:

```
GATHER                  UNDERSTAND                  ACT
retrieve / find      ai.decide / ai.create      give back / export
```

MoQL feeds the arc. AI works on what MoQL returned. Always sequential — never nested. A `find` block never contains `ai.decide`. An `ai.decide` block never contains `find`.

---

## Nine-Prefix Modifier System

Learn nine prefixes. Read any Mohio code immediately.

| Prefix | Meaning | Examples |
|--------|---------|---------|
| `by.` | the how | `by.token` `by.sentence` `by.streaming` |
| `do.` | the rule / constraint | `do.once for id` `do.after 5 seconds` `do.encrypt` |
| `on.` | the reaction | `on.failure` `on.success` `on.open` `on.close` `on.reconnect` `on.change` |
| `as.` | the form | `as.json` `as.csv` `as.pdf` `as.boolean` `as.uc` `as.lc` `as.decimal` |
| `to.` | the destination | `to.email` `to.queue` `to.log` |
| `in.` | the where or unit | `in.USD` `in.kilometers` `in.background` `in.EUR` |
| `is.` | the state test | `is.empty` `is.valid` `is.matching` `is.overdue` `is.active` `is.not` `is.in` |
| `not.` | the opposite | `not.empty` `not.found` `not.authorized` |
| `if.` | the when | `if.exists` `if.empty` `if.above` |

**Connector prefixes:** `with.` `from.` `and.`

The dot separates category from word. Once you know `on.` means reaction, you know `on.failure`, `on.success`, `on.open`, `on.close`, and `on.change` without being told.

---

## Hard-Reserved Namespaces

| Namespace | Coverage | Notes |
|-----------|---------|-------|
| `ai.` | All AI reasoning primitives | Hard reserved — zero ambiguity. Cannot be variable names. |
| `sh.` | Shape references | Strongly encouraged. Parser unambiguous. |
| `cm.` | Compliance actions | cm.retain, cm.purge, cm.report, cm.notify, cm.lock, cm.expire… |
| `env.` | Environment variables | Safe credentials. Compiler warns on hardcoded secrets. |
| `secret.` | Secrets / credentials | Never hardcoded. |
| `db.` | Database table references | db.members, db.orders, db.transactions… |
| `mioai.` | AI generation library | Generation, not reasoning. |

---

## Foundation Blocks

Structural declarations that run before logic. They define the world. They are contracts, not code.

| Block | What It Declares |
|-------|-----------------|
| `journey` | Application entry. Orchestrates all pages and shared declarations. |
| `saga` | Distributed operation. Steps with rollback. |
| `shape [Name]` | Data structure contract — DB, API, UI, and compliance simultaneously. |
| `pattern [Name]` | Named text pattern for validation, extraction, and replacement. |
| `miomap [Name]` | Field mapping between two shapes. Transformation contract. |
| `connect [name] as [type]` | Database or cache connection. Credentials always via env. |
| `mioconnect [Name]` | Named external service connector. |
| `sector:` | Activates sector profile — compliance, field types, AI constraints. |
| `compliance:` | Activates a specific compliance framework runtime. |
| `include "[file]"` | Pull another .mho file inline. |

---

## Core Verbs

### Flow Control

| Verb | Notes |
|------|-------|
| `listen for` | Multiplexing container. Universal entry point. |
| `connection at [path]` | Persistent WebSocket. Inside listen for. |
| `while.active` | WebSocket loop. |
| `new sh.[Shape]` | Inbound data listener. Implies POST. |
| `request for sh.[Shape]` | Read request listener. Implies GET. |
| `if / otherwise` | Standard conditional. otherwise = final fallback. |
| `check [value]` | Multi-branch examination. `when` modifiers below. |
| `unless` | Exception condition modifier. |
| `when` | Positive condition modifier inside check. |
| `each [item] in [collection]` | Iterate over collection. |
| `repeat [n] times` | Count-based loop. |
| `stop` | Exit current loop. |
| `skip` | Next iteration. |
| `halt` | Stop all execution. Nothing after runs. |
| `jump to [path]` | Navigation / redirect. |

### Data Operations (MoQL)

| Verb | Notes |
|------|-------|
| `retrieve from db.[table] as [name]` | Bring back specific known record by identity. |
| `retrieve.one / .first / .last / .all / .every` | Retrieve modifiers. |
| `find [name] in db.[table]` | Search — scan and filter with where conditions. |
| `grab` | Lightweight single read — config values, constants. |
| `check exists / count` | Verify presence or count. Returns boolean or number. |
| `compare [a] to [b]` | Side-by-side analysis — period-over-period, A/B. |
| `save to db.[table]` | Insert new record. |
| `save or update db.[table]` | Upsert — match clause is the uniqueness key. |
| `save all from [collection]` | Bulk insert. |
| `update db.[table]` | Modify existing. `match` required — never `where`. |
| `remove from db.[table]` | Delete. `match` or `where` required. |
| `remove.all from db.[table]` | Full table delete — explicit modifier required. |
| `make` | Assemble from known shapes or ingredients. |
| `create` | Build something new from scratch. |
| `write` | Output structured data to storage. |
| `copy` | Copy source to destination, leaves original. |
| `read` | Read file or storage content. |
| `ping` | Periodic lightweight pull. |

### MoQL — match, where, order, group

```mohio
// match — record identity
match id to request.id
match.unique email to request.email   // error if not exactly one
match.any member_id to member.id       // one or more must exist
match.none email to request.email      // conflict check
match.all status to "active"           // every matching record

// where — conditions
where amount is above 1000
where amount is below 100
where score is between 700 and 850
where created_at is.in last 30 days
where created_at is.in this month
where created_at is older than 90 days
where created_at is newer than 7 days
where deleted_at is empty
where deleted_at is.not empty
where name contains "Smith"
where email starts "info@"
where status active, pending, review    // IN list
where status is.not banned, deleted     // NOT IN list

// order
order.up by field           // ascending — canonical
order.down by field         // descending — canonical
order.{{ direction }} by field  // dynamic

// group
by merchant                 // group by field
by day / week / month / quarter / year  // time series

// pagination
up to 25                    // limit
skip 40                     // offset
paginate by 25              // cursor-based — result carries next_cursor, has_more
cursor from request.cursor

// aggregation (inside summarize block)
amount.sum          amount.count        amount.average
amount.max          amount.min          amount.running_sum
amount.moving_average by N              amount.rank within field
value.std_deviation value.variance      value.percentile N
difference.percentage_of field

// export
return id, name, email
return id as record_id
export as.csv to "filename.csv"
export as.xlsx to "filename.xlsx"
export as.json to "filename.json"

// SQL escape hatch
sql
    SELECT * FROM members WHERE ...
sql: done
```

### Output

| Verb | Notes |
|------|-------|
| `give back` | Return value and exit. Universal return. |
| `give back [status] "[message]"` | HTTP response with status. |
| `give back [variable]` | Return a named value. |
| `give back [variable] as.json` | Return with format cast. |
| `with [key] [value]` | Named payload pair on give back. |
| `send [data] to [session]` | Push to WebSocket/SSE session. |
| `broadcast to room "[name]"` | Send to all in a room. |
| `except member.session` | Exclude sender from broadcast. |
| `stream [data] to [session]` | SSE or file stream. |
| `stream manifest [name]` | HLS manifest for audio/video. |
| `show` | Render HTML. |
| `close stream` | Finalize active stream. |

---

## Expressions

### Three Contexts

```mohio
(subtotal / 100 + tax)          // math — standard operators + - * / %
"Hello {{ member.name }}"       // string with template injection
{{ (amount / 100) as.decimal }} // template with math expression
```

### Type Cast Modifiers (as.)

| Modifier | Returns |
|----------|---------|
| `as.int` | Integer — floor |
| `as.decimal` | 2 decimal places |
| `as.decimal.00` | Explicit 2-place pattern form |
| `as.decimal.0000` | 4 decimal places |
| `as.string` | String |
| `as.boolean` | true / false |
| `as.days / .hours / .minutes / .seconds / .weeks` | Date arithmetic result unit |
| `as.uc` | Uppercase — canonical (`as.upper` accepted) |
| `as.lc` | Lowercase — canonical (`as.lower` accepted) |
| `as.title` | Title Case |
| `as.sentence` | Sentence case |
| `as.absolute` | Absolute value |
| `as.json / .csv / .pdf / .html` | Format cast on give back |

**Date arithmetic — as. goes OUTSIDE parens:**
```mohio
age_days  (today() - member.created_at) as.days    // correct
overdue   (now() - invoice.due_date) as.hours
```

### Rounding

```mohio
total (price * quantity) round.up       // ceiling
total (price * quantity) round.down     // floor
total (price * quantity) round.to 2     // nearest, 2 decimal places
total (price * quantity) round.up as.decimal.00
```

### join inside {{ }}

```mohio
"{{ join first_name, last_name }}"              // space — default
"{{ join first_name, last_name with space }}"
"{{ join items with comma }}"
"{{ join parts with none }}"                    // concatenate
```

### Null Coalescing

```mohio
display_name member.nickname otherwise member.full_name
```

### = and == inside ()

Both mean equality comparison inside `(...)`. `=` is canonical. `==` accepted — mio fmt suggests `=`.

---

## String Operations

```mohio
// Case
as.uc           as.lc           as.title        as.sentence

// Trim
trim            trim.front      trim.back

// Remove
remove.special  remove.html     remove.ws

// Truncate
truncate.to 35          truncate.to 40 words

// Pad
pad.left to 12 with 0   pad.right to 8 with " "

// Mask
mask.all except last 4  // ****1234
as pattern ****-####    // pattern mask form

// Split
split full_name by " "
    first_name  first part
    last_name   last part
split: done

// Replace
replace in raw_text
    "WM" with "W.M."
replace: done

// Prepend / Append
prepend "TXN-" to reference_number
append ".pdf" to filename
```

---

## Pattern Blocks

```mohio
pattern TransactionRef
    starts.with "TXN-"
    then digits exactly 8
    then "-"
    then letters exactly 3
    ends
pattern: done

// Named parts
pattern Email
    any characters as local_part
    then "@"
    then any characters as domain
pattern: done

// Operations
find all pattern.Email in document.body as emails    find: done
extract from member.email using pattern.Email
replace pattern.Date in document.body
    with "{{ date.month }}/{{ date.day }}/{{ date.year }}"
replace: done
```

**Built-in patterns:** `pattern.email` `pattern.phone_us` `pattern.url` `pattern.zip_us` `pattern.date_iso` `pattern.currency_usd` `pattern.ip_v4`

---

## AI Primitives

### `ai.` namespace — Hard Reserved

| Primitive | Notes |
|-----------|-------|
| `ai.decide [name](args) returns [type]` | AI reasoning. Compiler refuses to build without `not confident`. Automatic audit. |
| `confidence above [n]` | Threshold declaration — no `check` prefix. |
| `weigh [field list]` | Inputs the AI reasons over. Comma-separated. |
| `ai.audit to [log]` | Permanent immutable record. Must appear BEFORE `not confident`. |
| `not confident` | Required fallback path. Compiler error without it. |
| `on.failure` | Service failed — connection error, timeout, provider down. |
| `on.error` | Unexpected runtime error. Distinct from on.failure and not confident. |
| `using [chain_name]` | Route through a declared ai.chain. |
| `ai.explain [decision] as [name]` | Plain-language explanation. Audience and format specified. |
| `ai.audit decision` | Standalone audit entry — manual override records. |
| `ai.create image / audio / logic / text` | Generate content inline. Four type modifiers. |
| `ai.chain [name]` | Provider fallback sequence with quality gates. Resolve before loops — never inside. |
| `ai.override [decision] with [value]` | Human correction. Auto-audited. Signals miolearn. |

**Canonical ai.decide block:**
```mohio
ai.decide isFraudulent(transaction) returns boolean
    confidence above 0.85
    weigh transaction.amount, transaction.device_id, member.history
    ai.audit to fraud_audit_log
    not confident
        give back 202 "Referred to manual review"
    on.failure
        give back 503 "Fraud check unavailable"
    on.error
        miolog.error "Unexpected fraud check error"
            code    error.code
            message error.message
            at      error.location
        give back 500 "Internal error"
ai.decide: done
```

**ai.explain — standalone form:**
```mohio
ai.explain fraud_check as explanation
    audience "compliance officer"    // applicant / clinician / developer / regulator
    format "bullet list"             // plain paragraph / formal letter paragraph / technical summary
ai.explain: done
give back explanation
```

**ai.chain — runtime values:**

| Term | Notes |
|------|-------|
| `on.resolve` | Fires once when chain establishes active provider. Lock before loops. |
| `[chain].active_provider` | Currently resolved provider name. |
| `fallback()` | Advances active_provider in place on mid-loop failure. Never goes backwards. |

### `mioai.` namespace — Generation Library

| Method | Notes |
|--------|-------|
| `mioai.generate` | Text generation with full streaming. |
| `mioai.summarize` | Document and text summarization. |
| `mioai.classify` | Classify text into named categories with fallback. |
| `mioai.embed` | Vector embeddings for semantic search. |
| `mioai.research` | AI-powered research with depth and citation control. |

**mioai. Runtime Values:**
```
mioai.last_response     mioai.last_tokens       mioai.last_cost
mioai.last_model        mioai.last_confidence   mioai.streaming
mioai.tokens_so_far
```

**AI Streaming Modes:**

| Mode | Best for |
|------|---------|
| `stream by token` | Chat interfaces — most responsive |
| `stream by sentence` | Compliance — complete thoughts |
| `stream by paragraph` | Long-form structured content |
| `complete` | Need full response before acting |

### Sector AI Confidence Minimums

| Sector | Decision Type | Minimum |
|--------|--------------|---------|
| financial | Transaction approval / denial | 0.85 |
| financial | Credit approval | 0.90 |
| financial | SAR filing | 0.95 |
| financial | Account closure | 0.95 |
| healthcare | Diagnosis / treatment | 0.95 |
| healthcare | Medication / prescription | 0.98 |
| healthcare | Billing / procedure code | 0.90 |

---

## Block Event Modifiers

| Modifier | When it fires |
|----------|--------------|
| `on.failure` | Expected failure — service down, 404, timeout |
| `on.error` | Unexpected exception — something broke |
| `on.success` | Block completed successfully |
| `on.open` | WebSocket connection established |
| `on.reconnect` | After automatic reconnection |
| `on.close` | WebSocket connection closed |
| `on.mfa required` | MFA required in login flow |
| `on.change([field])` | Named field value changed |
| `on.progress` | File stream progress |
| `on.complete` | Stream or generation completed |
| `on.rollback` | Saga step reversal |
| `on.resolve` | ai.chain provider established |
| `on.purge` | Shape retention — triggers cm.purge |

---

## The error Object

Available inside `on.error` handlers. All fields always present.

| Field | Contents |
|-------|---------|
| `error.message` | Human-readable description. Plain English. |
| `error.code` | HTTP status or Mohio internal code (PARSE_ERROR, MISSING_FALLBACK, CONFIDENCE_FAIL…) |
| `error.type` | timeout / auth / validation / service / runtime / parse / schema / compliance |
| `error.source` | Block or service that generated the error |
| `error.location` | File and line: `fraud_check.mho:42` |
| `error.trace` | Stack trace. Empty string in production unless `--verbose`. |
| `error.context` | Runtime state at time of error |
| `error.count` | Cumulative error count for this block |
| `error.previous` | Previous error if chained. null if first. |

**on.failure object:**

| Field | Contents |
|-------|---------|
| `on.failure.message` | Short description: 'Service returned 503' / 'Record not found' |
| `on.failure.code` | HTTP status or Mohio failure code |
| `on.failure.source` | Block or service that triggered the failure |
| `on.failure.at` | File and line |

---

## Stream Modifiers

| Modifier | Notes |
|----------|-------|
| `max every [duration]` | Rate limit SSE push. |
| `merge latest` | Most recent value only — dashboards, prices. |
| `queue events` | All events held — activity feeds. |
| `discard overflow` | Events dropped — metrics. |
| `buffer for [duration]` | Time-based batching. |
| `buffer [n] events` | Count-based batching. |
| `live true` | Keep stream open, push new events. |
| `resumable` | Client can resume if connection drops. |
| `record play_position` | Runtime tracks playback position. |
| `resume from [position]` | Start from last known position. |
| `add chunk to [stream] for [id]` | Receive and add media chunk. |
| `close stream [name] for [id]` | Finalize stream, clean up segments. |
| `max: done` | Closes max block. |

---

## Closers

```mohio
done                // universal closer — always valid
blockname: done     // named closer — explicit, strongly recommended
```

Named closer examples: `listen: done` `new: done` `retrieve: done` `find: done` `save: done` `update: done` `remove: done` `ai.decide: done` `ai.explain: done` `ai.chain: done` `check: done` `each: done` `miomail: done` `miotest: done` `stream: done` `pattern: done` `split: done` `replace: done`

A named closer mismatch is a compile error — not a warning. The error message gives line number, expected closer, found closer, and suggested fix.

---

## Compliance Primitives (cm.)

```mohio
cm.retain [data] for [period]
cm.retain all [phi] for 6 years
cm.expire [data] after [period]

cm.purge member.id
    reason "GDPR right to erasure — {{ request.ticket_id }}"   // REQUIRED
    includes "user_records" "session_data"
    preserve "legal_hold_records"
cm.purge: done

cm.report "CTR" for transaction
cm.notify breach
cm.lock [data]
cm.unlock [data]
```

`cm.purge` requires `reason` — compiler error without it. A purge without a documented reason is not legally defensible.

---

## Auth & Access

```mohio
require role "admin"
require role "admin" or "system"
verify token from request.header "Authorization"
    scope "read:members"
    on.failure give back 401 "Unauthorized"
verify: done
sign url for cloud_storage/{{ file.path }}
    expires in 30 minutes
    named download_url
sign: done
```

---

## Built-In Services (mio*)

| Service | Purpose | Full Reference |
|---------|---------|---------------|
| `mioauth` | Auth — JWT, OAuth, MFA, API keys, RBAC | Pioneer Program |
| `miocache` | Redis-compatible caching | Pioneer Program |
| `mioconnect` | External service connectors | Pioneer Program |
| `miofile` | Files, directories, cloud storage | Pioneer Program |
| `mioform` | Form declaration and rendering | Pioneer Program |
| `miolog` | Structured JSON logging | Pioneer Program |
| `miomail` | Email — send, queue, receive | Pioneer Program |
| `miomap` | Field mapping — connectors to shapes | Pioneer Program |
| `miopdf` | PDF generation and processing | Tier 2 |
| `mioschedule` | Task scheduling | Pioneer Program |
| `miosearch` | Full-text search | Tier 2 |
| `miostream` | WebSocket, SSE, AI streaming, live media | Pioneer Program |
| `miotest` | Testing framework | Pioneer Program |
| `miovalidate` | Named reusable validation rules | LDD |
| `mioimage` | Image processing | Tier 2 — Phase 2 |
| `miopush` | Real-time push to active sessions | Tier 2 |
| `mioaccess` | Accessibility — TTS narration, captions | Tier 2 |

---

## Built-In Shapes

| Shape | Available when |
|-------|---------------|
| `sh.Member` | Always — `current.user` resolves to sh.Member. |
| `sh.MediaChunk` | Live streaming — browser media segments. |
| `sh.SignalMessage` | WebRTC signaling — ICE candidates and SDP. |
| `sh.SensorReading` | IoT device listener pattern. |

---

## miotest-Scoped Keywords

Only valid inside miotest blocks. Compiler error if used in production code.

| Keyword | Meaning |
|---------|---------|
| `it "[description]"` | Test case declaration |
| `mock [target]` | Replace with controlled fake |
| `seed db.[table]` | Populate test database |
| `force confidence [n]` | Override AI confidence score |
| `force db.[table] to fail` | Force database error |
| `force ai.decide [name] to fail` | Force AI service failure |
| `force mioconnect [name] to fail` | Force connector failure |
| `record [value]` | Capture value across iterations |
| `expect` | Test assertion — fail if not true |
| `expect [x] is [y]` | Equality assertion |
| `expect [x] above / below [n]` | Numeric assertion |
| `expect [x] contains "[text]"` | Text assertion |
| `expect [x] matches sh.[Shape]` | Shape conformance |
| `expect db.[table] has record` | Database assertion |
| `expect miomail sent` | Email assertion |
| `expect [x] matches snapshot "[name]"` | Snapshot assertion |
| `contribute to training` | Training pipeline opt-in |
| `mode [types]` on it | Test mode filter |
| `auto` | Automation triggers |

---

## Multilingual Syntax — mio translate

`mio translate [file.mho] --to [lang]`

The prefix system never translates — it is the structural guarantee. Vocabulary translates. Grammar does not.

| Phase | Languages |
|-------|-----------|
| 1 | Portuguese (pt), Spanish (es) |
| 2 | Hindi (hi), Filipino (fil), Vietnamese (vi) |
| 3 | Polish, Czech, French — community |

---

## Retired Keywords — Do Not Use

| Keyword / Pattern | Replacement |
|-------------------|------------|
| `route` | `listen for` |
| `emit` | RESERVED |
| `process` | RESERVED |
| `consider` | RESERVED — use `check` |
| `includes` (condition modifier) | RESERVED — use `when contains` |
| `or if` | `check / when / otherwise` |
| `area` | `section` |
| `{ }` single braces | `with [key] [value]` pairs or shapes |
| `notify via email` | `miomail send` — miomail is the only email primitive |
| `run async [name] = task()` | `run async task() as [name]` |
| `retrieve X in db.Y` | `retrieve from db.Y as X` |
| `find` nested inside `retrieve` | Separate blocks |
| `retrieve explanation from ai.explain` | `ai.explain [decision] as [name]` |
| `at most every` | `max every` |
| `track play_position` | `record play_position` |
| `append chunk to` | `add chunk to [stream] for [id]` |
| `finalize [stream]` | `close stream [name] for [id]` |
| `persist when empty` | `keep open when empty` |
| `direction in/out/both` | Protocol inference |
| `warm cache` | `retrieve` with `cache for` |
| `simulate` (miotest) | `on.failure` inside mock |
| `miotest.set confidence to` | `force confidence [n]` |
| `miocron` | `mioschedule` |
| `tenant mode: multi` | `serves: multiple tenants` |
| `timeout N seconds` | `wait up to N seconds` |
| `body {}` | Bare field values in new sh. blocks |
| `where` in update blocks | `match [field] to [value]` |
| `status to X` in update blocks | Bare `status X` |
| `set [field] = [value]` in update | Bare field values |
| `order by X ascending` | `order.up by X` |
| `order by X descending` | `order.down by X` |
| `divide by N` | `divide.by N` |
| `check confidence above` | `confidence above` (no check prefix inside ai.decide) |

---

*Mohio Language — Primitives & Modifiers Reference*
*Version 3.6 · April 2026 · Particular LLC · Apache 2.0*
*LDD v3.6 is the authoritative source. When this document and the LDD conflict, the LDD wins.*
*Full service appendices and design decisions available via the Pioneer Program — mohio.io*
