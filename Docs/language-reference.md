# Mohio Language Reference

**Version:** Phase 1  
**CLI:** `mio`  
**Extension:** `.mho`  
**Pronunciation:** *moh-hee-oh* — from te reo Māori: to understand

---

## File structure

Every Mohio file follows three zones in order:

```
Declarations    — sector, connect, hold, lock, shape, task
Logic           — listen for, ai.decide, retrieve, find, save, if, check, each
Output          — give back, halt, jump to, show
```

Zones are organizational — they are not enforced syntax. They are the convention that makes Mohio files readable at a glance.

---

## Closers

Every named block in Mohio opens with a verb and closes with its name:

```mohio
retrieve member from db.members
    match id is user.id
retrieve: done

ai.decide isFraudulent(transaction) returns boolean
    ...
ai.decide: done
```

**Named closer** — `blockname: done` — strict. The compiler validates the name matches the opening block. Mismatch = compile error with line numbers and a suggested fix.

**Bare closer** — `done` — accepted on any block. Forgiving, but you lose the mismatch detection. `mio fmt` (Phase 2) will normalize bare closers to named closers.

**Phase 1 note:** All blocks — including `if`, `check`, `each`, `repeat`, `while` — require explicit closers. Phase 1.5 (indentation preprocessor) will lift this requirement from flow control blocks.

---

## Declarations

### `sector:`

```mohio
sector: financial
sector: healthcare
```

Activates a sector profile — field type awareness, compliance frameworks, AI decision constraints, retention rules, audit requirements, and security defaults. See [sector-financial.md](sector-financial.md) and [sector-healthcare.md](sector-healthcare.md).

Your declaration wins. Any rule in the profile can be overridden by declaring it explicitly after the `sector:` line.

### `connect`

```mohio
connect db as postgres from env.DATABASE_URL
connect cache as redis from env.REDIS_URL
```

Declares a named data connection. Credentials always via `env.` — never hardcoded. The compiler warns on hardcoded secrets.

### `hold` and `lock`

```mohio
hold FRAUD_THRESHOLD 0.85    // soft constant
hold API_VERSION = "v2"      // = sign optional

lock MAX_VELOCITY 5          // hard constant
```

`hold` — immutable value, runtime enforces it.  
`lock` — hard constant, cannot be released.

### `shape`

```mohio
shape Transaction
    id          as text
    amount      as decimal
    currency    as text
    member_id   as text
    timestamp   as datetime
shape: done
```

A shape is simultaneously the contract for database structure, API format, frontend validation, compliance enforcement, and UI generation. Define it once. Enforce it everywhere.

Field modifiers: `required`, `optional`, `default value`, `min N`, `max N`, `format "pattern"`, `never store`, `never log`.

### `task`

```mohio
task clearTransaction(transaction) returns text
    save to db.cleared_transactions
        id          transaction.id
        cleared_at  now()
    save: done
    give back "Transaction approved"
clearTransaction: done
```

Named, callable block of logic. Parameters are typed. Return type optional but recommended. Tasks are the idiomatic way to encapsulate complex logic — especially any logic that involves multiple named data blocks.

### `compliance:`

```mohio
compliance: HIPAA
compliance: PCI_DSS
compliance: SOC2
compliance: GDPR
```

Activates a compliance framework directly. Usually activated automatically by `sector:` — use standalone when you need frameworks outside a sector profile.

---

## Routing

### `listen for` / `new` / `request`

```mohio
listen for

    new sh.Transaction
        require role "screener" or "system"
        // handles POST / new shape instances
    new: done

    request for sh.Member
        // handles GET / read requests
    request: done

listen: done
```

`new sh.X` — handles incoming shape instances (POST equivalent). Shape fields are available as variables.  
`request for sh.X` — handles read requests (GET equivalent).

---

## Flow control

### `if`

```mohio
if isFraudulent is true
    give back 422 "Transaction blocked"
if: done
```

### `if / or if / otherwise`

```mohio
if score > 90
    give back 200 "excellent"
or if score > 70
    give back 200 "good"
otherwise
    give back 200 "needs review"
if: done
```

### `check / when / otherwise`

Multi-branch routing. Cleaner than chained `if` for 3+ branches.

```mohio
check status
    when "active"
        give back 200 "ok"
    when "pending"
        give back 202 "pending"
    otherwise
        give back 400 "unknown"
check: done
```

### `each`

```mohio
each user in users
    miomail.send to user.email
each: done
```

### `repeat`

```mohio
repeat 3 times
    miohttp.post to env.RETRY_ENDPOINT body payload
repeat: done
```

### `while`

```mohio
while queue.size > 0
    process queue.next()
while: done
```

### `stop`, `skip`, `halt`

```mohio
stop    // exit current loop
skip    // next iteration
halt    // stop all execution
```

---

## Data operations

All data operations require explicit closers.

### `retrieve`

Single record by exact match.

```mohio
retrieve member from db.members
    match id is transaction.member_id
    on.failure
        give back 404 "Member not found"
retrieve: done
```

### `find`

Search with conditions. Returns a collection.

```mohio
find recent in db.transactions
    where member_id is transaction.member_id
    and timestamp since now() - 24 hours
find: done
```

### `save`

```mohio
save to db.cleared_transactions
    id          transaction.id
    amount      transaction.amount
    cleared_at  now()
save: done
```

### `update`

```mohio
update db.orders
    match id is order.id
    status "shipped"
update: done
```

### `remove`

```mohio
remove from db.sessions
    match user_id is user.id
remove: done
```

### `transaction`

Wraps multiple data operations in an all-or-nothing block. Rolls back automatically on error.

```mohio
transaction
    save to db.orders
        user_id user.id
        total   cart.total
    save: done
    remove from db.cart
        match user_id is user.id
    remove: done
transaction: done
```

---

## AI primitives

### `ai.decide`

The core AI reasoning primitive. Not a function call — a language construct.

```mohio
ai.decide isFraudulent(transaction) returns boolean
    check confidence above 0.85
    weigh transaction.amount, transaction.device_id, member.history, recent
    ai.audit to fraud_audit_log
    not confident
        give back 202 "Referred to manual review"
    on.failure
        give back 503 "Fraud check unavailable"
ai.decide: done
```

**`check confidence above N`** — confidence threshold. The model's confidence must exceed this value or the `not confident` block fires.

**`weigh`** — the inputs the AI reasoning uses. Labeled plainly for the model.

**`ai.audit to log_name`** — writes an immutable record: decision name, inputs, result, confidence, model, timestamp. Must appear **before** `not confident`.

**`not confident`** — required. The compiler refuses to build without it. Defines what happens when confidence falls below threshold.

**`on.failure`** — what happens if the AI call itself fails (API error, timeout).

**Return types:** `boolean`, `text`, `number`, `result`

### `ai.explain`

```mohio
set reason = ai.explain decision fraud_check
    audience "compliance officer"
    format "paragraph"
```

Generates a plain-language explanation of an `ai.decide` result. Audience and format specified inline.

### `ai.audit` (standalone)

```mohio
ai.audit decision
    event "loan_approval"
    inputs { application.id, credit_score }
    result approval_decision
    to compliance_log
```

Manual audit entry for any decision — not just AI decisions.

---

## Result handlers

Available on any data block or AI block.

```mohio
on.failure
    give back 404 "Not found"

on.success
    give back 200 "Done"

on.failure
    give back 503 "Service unavailable"
```

---

## Try / catch / always

```mohio
try
    retrieve data from db.records
        match id is record.id
    retrieve: done
catch timeout
    give back 503 "Service temporarily unavailable"
catch any as err
    miolog.error err.message
always
    miolog.info "Attempt completed"
```

---

## Output

### `give back`

```mohio
give back 200 "Transaction approved"
give back 422 "Transaction blocked pending review"
give back 202 "Referred to manual review"
give back member                               // give back a value
give back result                               // give back a variable
```

### `jump to`

```mohio
jump to /dashboard
jump to manual_review
```

---

## Values and expressions

### Environment variables

```mohio
env.DATABASE_URL
env.ANTHROPIC_API_KEY
secret.STRIPE_KEY
```

`env.` — reads from environment. `secret.` — reads from secret store. The compiler warns on hardcoded credentials.

### Dotted names

```mohio
transaction.amount
member.history
user.email
```

### Time expressions

```mohio
now()
now() - 24 hours
now() - 30 days
```

### Math

```mohio
total ((price * quantity))
rate  ((amount / 100))
```

### Template strings

```mohio
"Welcome {{ user.name }}"
"Invoice {{ invoice.number }}"
```

---

## Built-in services (`mio*`)

| Namespace | Purpose |
|-----------|---------|
| `miolog` | Structured logging — `.info`, `.warn`, `.error`, `.alert` |
| `miomail` | Email — `.send`, `.queue` |
| `miohttp` | Outbound HTTP — `.get`, `.post`, `.put`, `.delete` |
| `miofile` | Files and directories |
| `mioauth` | Auth — JWT, OAuth, MFA, API keys, roles |
| `miocache` | Caching |
| `mioschedule` | Task scheduling |
| `miopdf` | PDF generation |
| `mioimage` | Image processing |
| `miosearch` | Full-text search |
| `miostream` | WebSocket, SSE, streaming |

---

## Require role

```mohio
require role "admin"
require role "screener" or "system"
```

Enforces role-based access. If the current request does not carry a matching role, execution stops with a 403.

---

## Modifier prefix system

Nine prefixes. Learn them once, read any Mohio code.

| Prefix | Meaning | Examples |
|--------|---------|---------|
| `by.` | the how | `by.sending`, `by.streaming` |
| `do.` | the rule / constraint | `do.once`, `do.after 5 seconds`, `do.encrypt` |
| `on.` | the reaction | `on.failure`, `on.success`, `on.open`, `on.change` |
| `as.` | the form | `as.json`, `as.csv`, `as.pdf` |
| `to.` | the destination | `to.email`, `to.queue`, `to.log` |
| `in.` | the where or unit | `in.USD`, `in.background` |
| `is.` | the state test | `is.empty`, `is.valid`, `is.overdue` |
| `not.` | the opposite | `not.empty`, `not.found`, `not.authorized` |
| `if.` | the when | `if.exists`, `if.empty` |

---

## Error message format

Mohio errors are written for developers, not compilers. Every error answers three questions: what went wrong, why it is a problem, what to do about it.

```
Line 24 — closer mismatch.
Expected: ai.decide: done
Found:     retrieve: done

The ai.decide block opened on line 18 is not closed.
Add 'ai.decide: done' before 'retrieve: done'.
```

```
Compile error — ai.decide "isFraudulent" is missing a "not confident" block.
Every ai.decide must define what happens when confidence falls below threshold.
Add a "not confident" block inside this ai.decide before building.
```

---

## CLI — Phase 1

```bash
mio run file.mho                                    # execute
mio run file.mho --verbose                          # execute with trace
mio run file.mho --ai                               # use real Anthropic API
mio run file.mho --request-file request.json        # supply request payload
mio run file.mho --seed seed.json                   # supply database seed data
mio run file.mho --param key=value                  # supply individual fields

mio check file.mho                                  # parse and validate only
mio version                                         # version info
mio help                                            # usage
```

**Requires:** `ANTHROPIC_API_KEY` environment variable for `--ai`.

---

*Mohio Language Reference — Phase 1 — Particular LLC — Apache 2.0*
