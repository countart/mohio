# Mohio

> **Simplify the complicated. Make it stupid easy to build the most involved, insanely complicated programs.**

*The first programming language where AI reasoning is a native primitive, compliance is a declaration, and the code reads like the English sentence you'd use to describe what it does.*

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-teal.svg)](https://opensource.org/licenses/Apache-2.0)
[![Status: Design Phase](https://img.shields.io/badge/Status-Design%20Phase-orange.svg)]()
[![Discord](https://img.shields.io/badge/Discord-Join%20us-5865F2.svg)](https://discord.gg/MF95H3wQdm)
[![Buy Me a Coffee](https://img.shields.io/badge/Support-Buy%20Me%20a%20Coffee-yellow.svg)](https://buymeacoffee.com/mohiolang)

**CLI:** `mio` · **Extension:** `.mho` · **[mohio.io](https://mohio.io)** · **[hello@mohio.io](mailto:hello@mohio.io)**

---

## Read This First

Mohio (pronounced *moh-hee-oh*, Māori for "to understand") is a programming language. You write code. It has structure, form, and rules.

But the structure is English sentence structure. The rules are the rules of clear thought — not the rules of any language family that came before.

**Read this aloud:**

```mohio
sector: financial

listen for
    new sh.Transaction
        require role "screener"
        ai.decide isFraudulent(transaction) returns boolean
            check confidence above 0.85
            weigh
                transaction.amount,
                transaction.device_fingerprint,
                transaction.velocity_score
            not confident
                give back false
            ai.audit to fraud_audit_log
        decide: done
    new: done
listen: done
```

**If that sounded like English — you already understand Mohio.**

That is a complete, working, production-grade fraud screening service. One `sector:` declaration activates PCI_DSS enforcement, field type awareness, CTR threshold detection, and AML rules. The `listen for` block replaces all HTTP routing. The `new sh.Transaction` guard validates incoming data before a single line of logic runs. The `ai.decide` block makes a typed, audited, compliance-enforced AI decision with a mandatory fallback. You wrote none of that infrastructure. The language handled it.

---

## Who Mohio Is For

Mohio is one language for every scale — from a solo developer shipping a landing page to a regulated enterprise running AI in production.

```mohio
// A solo developer — landing page, four lines
listen for
    request for sh.Page
        show "pages/home.mho"
    request: done
listen: done
```

```mohio
// A startup — user identity in six lines
listen for
    new sh.LoginRequest
        check db.users for loginrequest.id
            on.failure give back "Identity not found"
            otherwise give back "Identity Verified"
        check: done
    new: done
listen: done
```

```mohio
// An enterprise team — HIPAA-compliant AI clinical triage
sector: healthcare

listen for
    new sh.PatientRecord
        require role "clinician"
        ai.decide readmissionRisk(patientrecord) returns text
            check confidence above 0.90
            weigh
                patientrecord.age,
                patientrecord.diagnoses,
                patientrecord.encounter_history
            not confident
                give back "moderate"
            ai.audit to phi_audit_log
        decide: done
    new: done
listen: done
```

Same language. Same syntax. Same rules. The `sector:` and `compliance:` declarations are there when you need them. They stay completely out of the way when you don't.

---

## The Six Problems Mohio Solves

### 1. AI is a guest in every other language

In Python, JavaScript, Go — AI is always something you import. A library. An SDK. A REST call wrapped in try/catch. You manage the connection, parse the response, handle failures manually, and hope the output is what you expected. The AI is bolted on, not built in.

In Mohio, `ai.decide` is a language construct. The compiler understands it. The runtime enforces typed returns, confidence thresholds, and mandatory fallbacks. The audit trail is automatic. You don't call AI — you write in it.

### 2. Compliance is always bolted on

Every regulated industry forces developers to spend weeks wiring compliance before writing a single line of business logic. Install the HIPAA library. Configure encryption. Set up audit logging. Wire access controls. Read 200 pages of documentation. Then — finally — write the feature you were hired to build.

In Mohio, `compliance: HIPAA` is one line. The runtime handles the rest.

### 3. Industry knowledge lives nowhere in code

A healthcare developer has to manually know that `mrn` is PHI, that patient records must be retained for 6 years, that AI diagnostic decisions require human review. A fintech developer has to know CTR thresholds, AML rules, what fields can never be logged. None of that knowledge lives in any language — it lives in developer heads and compliance documents.

In Mohio, `sector: healthcare` and `sector: financial` are language declarations that activate pre-built institutional knowledge automatically. Field types, retention rules, compliance frameworks, AI decision constraints — all loaded. The language knows the world you work in before you write your first line.

### 4. AI outputs are untyped

Language models return text. Turning that text into a typed, validated, usable value requires boilerplate that every developer writes differently and tests inconsistently.

In Mohio, `ai.decide` blocks declare their return type and confidence threshold. The runtime enforces them. You get a `boolean`, a `text`, a `decimal` — not a string to parse and hope for the best.

### 5. AI failures are silent

AI reasoning can fail — hallucinate, refuse, time out, return below-threshold confidence. In most systems, handling these cases is optional. Developers forget. Errors surface in production.

In Mohio, every `ai.decide` block requires a `not confident` block. The compiler rejects code that omits one. No exceptions. No silent failures. Failure handling is enforced at build time.

### 6. Modern languages are hostile to speed and beginners

The path from idea to working service runs through package managers, framework choices, boilerplate scaffolding, ORM configuration, and API client setup — before writing a single line of business logic. ColdFusion solved this in 1995. Nobody has solved it since at the same philosophical level.

Mohio solves it. Connections just work. Services are built in. The language is on your side from line one.

---

## How Mohio Works

### Listeners, Not Endpoints

> *"We've effectively removed the 'Web' from Web Development. We aren't building endpoints — we are building Listeners."*

### The Walk-By Test

Every line of Mohio code must pass this test before it is locked into the language:

> *If a manager can walk by a desk, glance at the monitor for three seconds, and understand the business intent without seeing a single bracket or semicolon — it passes.*

This is not a style preference. It is the founding rule that produced the syntax. If a code block requires explanation — it gets rewritten. Every keyword, every example, every primitive in this language passed that test before it was locked. That is why Mohio reads the way it does.

`route` is retired. `listen for` is the universal listener for everything a service receives — HTTP requests, webhooks, WebSocket connections, UI events, data changes, and scheduled triggers. One block per context. One `listen: done` seals the external interface of the entire page or service.

```mohio
listen for
    require role "manager"           // applies to everything this page hears

    new sh.Order                     // POST — something new arrived
        make Invoice from order
            on.failure log "Invoice failed: {{ order.id }}"
            otherwise notify order.assigned_to
        make: done
    new: done

    request for sh.Inventory         // GET — something was requested
        find Stock where item_id is inventory.id
            on.failure give back "Item not found"
            otherwise give back stock
        find: done
    request: done

    new sh.Payment from "Stripe" at /webhooks/stripe   // webhook + auto signature verify
        check payment.status
            on.failure log "Payment failed: {{ payment.id }}"
            otherwise start ProcessOrder(payment.metadata.order_id)
        check: done
    new: done

    connection at /chat/stream       // WebSocket — persistent connection
        on.open show "Connected"
        while.active
            new sh.Message
                show message in chat_window
                start ArchiveMessage(message)
            new: done
        while: done
        on.close show "Connection Lost"
    connection: done

listen: done
```

**HTTP methods are never declared explicitly.** `new` implies POST. `request for` implies GET. The method is an infrastructure detail — not your problem.

**Path is optional on a page — the page IS the location.** No path needed. Path is required in a journey, where listeners are declared globally across the entire application.

**`from "Service"` on webhooks activates automatic signature verification.** Two guards run before your logic: signature check, then shape validation.

### `otherwise` — The Parser Rule

`otherwise` is the universal final fallback inside any verb block. Execute the block top to bottom — if `on.failure` fires, exit immediately. If execution reaches `otherwise`, run it as the final fallback. Once per block, always the last line before the block closer.

```mohio
find Tasks where status is "backlog"
    on.failure show "You're all caught up!"
    otherwise show results as list
find: done
```

### `find` — The Search Verb

`find` searches with matching criteria and returns a collection or single record. Distinct from `retrieve` (general DB query), `pull` (queue extraction), and `get` (property access):

```mohio
find Tasks where title is.matching search_input
    on.failure show "No tasks found"
    otherwise show results as list
find: done
```

### `jump to` — Navigation Primitive

Moves flow physically from one file to the next. More active than `redirect`. Used naturally with `on.success` and `on.failure`:

```mohio
make Task from task_form
    on.failure show "Fix your errors"
    otherwise jump to "pages/board.mho"
make: done
```

### Standard SQL — Always Valid

Standard SQL is valid anywhere inside a `.mho` file alongside native Mohio data calls. `mio fmt` suggests the Mohio-native equivalent but never forces conversion. Both styles are permanently valid and can coexist in the same file.

The native Mohio data query pattern uses named blocks:

```mohio
// Simple — named block retrieval
retrieve from db.members
    match id to request.id
    return id, name, email
    up to 20
db.members: done

// With join — and retrieve from replaces LEFT JOIN ON
retrieve from db.members
    match id to transaction.member_id
    and retrieve from db.transactions
        match member_id to member.id
        where created_at > now() - 30 days
    return member.name, transaction.amount, transaction.created_at
    order by transaction.created_at descending
    up to 20
db.members: done

// Export directly from a query
retrieve from db.members
    match status to "active"
    return id, name, email
    export as.csv to "members.csv"
db.members: done

give back "members.csv"

// Raw SQL escape hatch — always valid
retrieve from db
    sql
        SELECT m.id, m.name, COUNT(t.id) as transaction_count
        FROM members m
        LEFT JOIN transactions t ON t.member_id = m.id
        GROUP BY m.id, m.name
        HAVING COUNT(t.id) > 5
    sql: done
db: done
```

| Keyword | Use for | Example |
|---------|---------|---------|
| `match` | Equality — field to value or field to field | `match id to request.id` |
| `where` | Conditions — ranges, comparisons, complex logic | `where amount > 10000` |
| `and retrieve from` | Join to another table | `and retrieve from db.transactions` |
| `return [fields]` | Field selection — specific fields only | `return id, name, email` |
| `return all` | All fields | `return all` |
| `up to N` | Bounded result — no more than N records | `up to 20` |
| `sql { }` | Raw SQL escape hatch | Full SQL inside the block |

`limit N` is accepted by the parser — `mio fmt` suggests `up to N`. `limit` inside a `sql` block is correct SQL syntax and stays as-is.


### Scheduling — mioschedule

Mohio replaces server cron jobs with named schedule declarations in the journey file. Declared in code. Version controlled. No SSH access needed. The runtime manages execution — the developer declares intent.

```mohio
// Named scheduled task — journey level
mioschedule weekly_digest
    every monday at 9am
    run SendWeeklyDigest
    on.failure alert ops_team
mioschedule: done

mioschedule fraud_queue_check
    every 5 minutes between 8am and 6pm
    on weekdays only
    run CheckFraudQueue
    timeout after 2 minutes
mioschedule: done

// Relative — one-time future execution
mioschedule payment_reminder
    in 3 days from order.created_at
    run SendPaymentReminder(order.id)
    do.once for order.id
mioschedule: done

// On-demand trigger from a page
run mioschedule.weekly_digest immediately
    on.success show "Digest queued"
    on.failure show "Failed to queue"
```

Standard cron expressions are also accepted — `mio fmt` suggests the natural language equivalent:

```mohio
mioschedule legacy_job
    cron "0 2 * * 1"    // mio fmt suggests: every monday at 2am
    run LegacyCleanup
mioschedule: done
```

| Time pattern | Example |
|-------------|---------|
| `every [time]` | `every monday at 9am`, `every 5 minutes`, `every day at 2am` |
| `every [time] between [t] and [t]` | `every 5 minutes between 8am and 6pm` |
| `on weekdays only` | Day scope modifier |
| `on [date] at [time]` | `on december 31 at 11:59pm` |
| `in [duration] from [datetime]` | `in 3 days from order.created_at` |
| `unless today is holiday` | Exception condition |
| `cron "[expression]"` | Standard cron — always accepted |

### Reactive Triggers and Persistent Connections

**`on.change`** fires when a shape field value changes — reactive triggers without polling:

```mohio
change to sh.Task
    on.success notify task.assigned_to
change: done
```

**`while.active`** is the persistent loop inside a WebSocket `connection` block. Runs until the connection closes. `on.open` and `on.close` handle the lifecycle.

**`vr.`** is RETIRED. The parser resolves `sh.Transaction` (shape) vs `transaction` (instance) automatically — `sh.` on the shape reference solves the ambiguity from the correct direction. `vr.` is no longer in canonical Mohio.

### The Atomic Guard — `new sh.[Shape]`

When a listener declares `new sh.Transaction`, it is not naming a variable. It is applying a contract.

The `sh.` prefix marks a shape reference — unambiguous to the parser, unambiguous to any developer reading the code. The `Transaction` shape is defined separately and already describes every field, type, and constraint. The Mohio runtime acts as a proxy at the network layer. Bad data never reaches your code. You write zero validation code. Shape mismatch returns an automatic, descriptive error to the sender.

```mohio
shape Transaction
    id                  as uuid     required    default uuid()
    amount              as decimal  required    min 0.01        in.USD
    device_fingerprint  as text     required
    velocity_score      as decimal  required    min 0.0  max 1.0
    status              as text     default "pending"
                                    allowed "pending" "approved" "blocked"
    created_at          as datetime default now()
shape: done
```

Inside the block, the instance is always lowercase — `transaction.amount`, `transaction.velocity_score`. The shape is `sh.Transaction`. The instance is `transaction`. Never ambiguous.


### The Legal Passport — Data Carries Its Own Rules

Retention and purge rules declared on a shape travel with the data everywhere it goes. The shape is the single source of truth for what data is, how long it must be kept, and what happens when it is deleted.

```mohio
shape UserAccount
    id    as uuid required
    email as text required
    status as text default "active"

    retain transactions for 7 years
    retain audit_logs   for 7 years
    retain all [phi]    for 6 years
        unless status is "test_account"

    on.purge
        cm.purge user.id
            includes "user_records" "audit_logs" "session_data"
            preserve "legal_hold_records"
        cm.purge: done
    on.purge: done
shape: done
```

`retain` is a standalone verb — `do.retain` is accepted but silently discarded. `purge` is a standalone verb — `do.purge` is accepted but silently discarded.

The full conditional retention pattern:

```mohio
retain transactions
    when status is "active"    for 7 years
    when status is "suspended" for 1 year
    unless status is "test_account"
```

> *"A compliance officer looking at Python is looking at how a developer decided to solve a problem. A compliance officer looking at Mohio is looking at the Rule of Law itself."* — External validator, unprompted

### The No-If Revolution

`on.failure` and `otherwise` handle the two-path case on any block — without a single `if`:

```mohio
check db.users for provided_id
    on.failure give back "Identity not found"
    otherwise give back "Identity Verified"
check: done

make Task from task_form
    on.failure show "Fix your errors"
    otherwise jump to "pages/board.mho"
make: done

find Tasks where status is "backlog"
    on.failure show "You're all caught up!"
    otherwise show results as list
find: done
```

> *"By using `on.failure` and `otherwise`, you can write 90% of a SaaS application without a single if statement."*

The parser rule: execute the block top to bottom. If `on.failure` is reached — run it and exit. If `otherwise` is reached — run it as the final fallback. `otherwise` appears once, always last. `if` is still fully supported for pure conditional logic — it just isn't required for the common binary case.

### The Nine Prefix System

Learn nine prefixes once. Read any Mohio code immediately. The dot connects a category of intent to a plain English word.

| Prefix | Meaning | Examples |
|--------|---------|---------|
| `by.` | How | `by.sending`, `by.joining`, `by.appending` |
| `do.` | Constraint | `do.once`, `do.after`, `do.lock`, `do.unless` |
| `on.` | Event | `on.failure`, `on.success`, `on.open`, `on.change` |
| `as.` | Form | `as.json`, `as.csv`, `as.pdf`, `as.excel` |
| `to.` | Direction | `to.email`, `to.queue`, `to.log`, `to.screen` |
| `in.` | Scope/Unit | `in.USD`, `in.kilometers`, `in.background` |
| `is.` | State | `is.empty`, `is.valid`, `is.overdue`, `is.pending` |
| `not.` | Negation | `not.empty`, `not.found`, `not.authorized` |
| `if.` | Condition | `if.exists`, `if: done` |

> *"Once you know `by.` means how — you know it everywhere. Once you know `do.` means constraint — you know it everywhere. This solves the Keyword Explosion problem. In most languages, you memorize hundreds of unique keywords. In Mohio, you learn nine prefixes and the rest is just English."*

### Write Mohio in Your Language

Translation is not a documentation feature. It is a compiler feature.

The prefix system is what makes translation possible. The dot separates the category of intent from the plain English word. Swap the word — the prefix stays. The parser doesn't change. The runtime doesn't change. Only the vocabulary changes.

```bash
mio translate file.mho --to pt   # every keyword converts to Portuguese
mio translate file.mho --to es   # every keyword converts to Spanish
```

A developer in São Paulo reads the same `.mho` file as Portuguese. A developer in Mumbai reads it as Hindi. A developer in Chicago reads it as English. One codebase. Native comprehension everywhere. The logic is identical. The structure is identical. The parser is identical.

**Phase 1:** Portuguese (Brazil + Portugal), Spanish (Latin America + Spain)
**Phase 2:** Hindi, Filipino, Vietnamese
**Phase 3:** Polish, Czech, additional languages

Language packs follow the same distribution model as community connectors — published to the Mohio registry, reviewed and certified by the Mohio team before shipping. Any developer or team can propose and contribute a mapping.


### `unless` and `when` — Natural Language Conditionals

`unless` replaces `else` and `if not` in rule and block context. `when` applies a positive condition. Both read as natural language policy:

```mohio
// Instead of:
if status is not "test_account"
    retain transactions for 7 years

// Natural language:
retain transactions for 7 years unless status is "test_account"

// Positive condition:
retain transactions for 7 years when status is "active"

// Combined:
retain transactions
    when status is "active"    for 7 years
    when status is "suspended" for 1 year
    unless status is "test_account"
```

`if` is permanently valid — every developer can write Mohio the way they always have. `unless` and `when` are better alternatives, not replacements.

### Three-Tier Mutability

```mohio
// Tier 0 — mutable, no keyword
count = 0
name = "Ronnie"

// Tier 1 — soft constant, releasable
hold FRAUD_THRESHOLD = 0.85
hold CTR_THRESHOLD = 10000

// Tier 2 — hard constant, compiler error if reassigned
lock MAX_RETRIES = 3

// Type annotation — name first, type second
phone as text = "5555555555"
amount as decimal = 0.00
```

`set` is retired — the parser accepts it as noise and silently discards it for compatibility.

### `secret.` vs `env.`

Mohio has two ways to access external configuration — both use the same syntax, resolved differently at runtime:

```mohio
// env. — development default, reads from environment variables
connect postgres as db from env.DATABASE_URL

// secret. — production companion, resolves from a declared provider
// (Vault, AWS Secrets Manager, Azure Key Vault)
connect postgres as db from secret.DATABASE_URL
```

`env.` works anywhere environment variables are set. `secret.` resolves from a secrets provider declared in your journey's `app config`. Both are enforced at parse time — hardcoded credentials are a compiler error. In production, use `secret.`. In development, `env.` is fine. `mio secrets check` verifies all secrets are resolvable before deployment.

### `make` vs `create` — Not Aliases

Two distinct verbs with different runtime behavior:

| Verb | Intent | Requires | Example |
|------|--------|----------|---------|
| `make` | Assembly from known ingredients | Existing shape fields | `make Invoice from order` |
| `create` | Build new structure, accepts unknowns | Nothing predefined | `create Workspace for "Project Alpha"` |

```mohio
// make — checks required shape fields exist before executing
make Invoice from order
    on.failure log "Invoice creation failed"
    otherwise notify order.assigned_to
make: done

// create — builds new structure, allocates space, accepts unknowns
create Workspace for "Project Alpha"
    on.failure give back "Workspace creation failed"
    otherwise jump to "pages/workspace.mho"
create: done
```

> *"A junior developer makes. A senior architect creates."*


### Saga — Distributed Operations with Compensation

A `saga` is a distributed operation where each step has an explicit rollback (`undo`). Absence of `undo` signals best effort — failure does not trigger compensation. `step: done` is the canonical closer.

```mohio
saga process_order(order, payment)

    step reserve_inventory
        update db.inventory
            reserved = reserved + order.quantity
        update: done
        undo
            update db.inventory
                reserved = reserved - order.quantity
            update: done
        undo: done
    step: done

    step charge_payment
        check payment with PaymentService
            by.sending
                amount = order.total
                token = payment.token
            sending: done
            expect sh.ChargeResult
            wait up to 5 seconds
            if no answer give back error "Payment timed out"
        check: done
        undo
            check payment with PaymentService
                by.sending action = "refund", charge_id = charge.id
                sending: done
                do.once for "refund-{{ order.id }}"
            check: done
        undo: done
    step: done

    step notify_customer
        // no undo = best effort
        // fires only when all prior steps succeeded
        miomail.send
            to = order.customer_email
            subject = "Order confirmed"
        miomail: done
    step: done

saga: done
```

Use `pause short` between steps to avoid flooding external services. `epic` is reserved alongside `saga` for a future collection-of-sagas pattern.

### Block Structure — Universal Closers

Every block that opens must close. Two forms, both always valid:

```mohio
done              // universal — always valid
blockname: done   // named — optional, canonical for nested containers
```

The named closer tells you exactly what just ended. No counting indents. No ambiguity. Works exactly like HTML div tags.

```mohio
ai.decide isFraudulent(transaction) returns boolean
    check confidence above 0.85
    weigh
        transaction.amount,
        transaction.device_fingerprint
    not confident
        give back false
    ai.audit to fraud_audit_log
decide: done        // unambiguous — the decide block just closed

shape Transaction
    id      as uuid     required
    amount  as decimal  required
shape: done
```

### Block Scope — Three Levels

Any Mohio verb can be an inline block without a name — for one-off logic right where it's needed. Named blocks are only needed when the same logic is called from multiple places:

```mohio
// Inline — no name, used once, right here
check db.users for provided_id
    on.failure give back "Identity not found"
    otherwise give back "Identity Verified"
check: done

// Named action block — callable from anywhere
VerifyUser(provided_id)
    check db.users for provided_id
        on.failure give back "Identity not found"
    give back "Identity Verified"
VerifyUser: done
```

Three scope levels: **inline block** (one-off, no name) → **page action** (top of `.mho` file, used on that page) → **journey action** (journey file, application-wide).

`ai.decide` — makes a typed, confidence-gated AI decision with mandatory fallback and automatic audit. Hard reserved. The compiler will not build without a `not confident` block.

`ai.explain` — generates a plain-language explanation of any decision. Audience and format specified inline.

`ai.audit` — permanent, immutable decision record. Fires at the `ai.decide` level regardless of which path executed. Inputs, outputs, confidence score, model, timestamp — all recorded.

`ai.chain` — fallback sequences across providers with quality gates:

```mohio
hold image_chain = ai.chain
    try imagen evaluate quality above 0.75
    then dalle evaluate quality above 0.70
    on all fail give back error "Image generation failed"
```

`mioai` — AI generation and processing: text, images, research, classification, embedding, translation. Provider-agnostic — swap models in the journey file, not in application code.

### The Prompt System

`.prompt` files are first-class Mohio files. Prompts follow the same three-scope model as CSS: external library (shared), file-scoped (page), inline (one-off).

```mohio
// prompts/fraud_analysis.prompt
prompt fraud_analysis
    version "1.0"
    system
        You are a financial fraud analyst.
        Return only: fraudulent or legitimate.
    user
        Amount: {{ transaction.amount }}
        Device: {{ transaction.device_fingerprint }}
        Velocity: {{ transaction.velocity_score }}
    constraints
        response "fraudulent" or "legitimate" only
```

Prompt inputs are sanitized automatically when flagged — injection patterns stripped before interpolation.

### Sector Profiles

```mohio
sector: financial   // PCI_DSS, SOC2, BSA/AML — card CVV can never be stored,
                    // CTR threshold built in, credit decisions require ai.explain
sector: healthcare  // HIPAA, HITECH — PHI retention 6 years, clinical AI
                    // requires confidence > 0.95 and human review before action
```

Sectors are transparent. See exactly what a declaration adds:

```bash
mio expand sector healthcare   # outputs every rule, field type, and constraint added
mio check sector healthcare    # checks version, shows what changed, flags urgent updates
```

Sectors stack. A health-tech fintech uses both:

```mohio
sector: healthcare
sector: financial
compliance: SOC2
```

---

## A Complete Production Example

Financial fraud detection. Compliance enforced. AI-powered. Fully audited. Every line readable.

```mohio
sector: financial

connect postgres as db from secret.DATABASE_URL
connect redis as cache from secret.REDIS_URL

hold FRAUD_THRESHOLD = 0.85

on error
    miolog.error "Unhandled error: {{ error.message }}"
    give back 500 "Internal error"

listen for
    new sh.Transaction
        require role "screener" or "system"

        find member in db.members
            where id is transaction.member_id
            cache for 5 minutes
        find: done

        ai.decide isFraudulent(transaction) returns boolean
            check confidence above FRAUD_THRESHOLD
            weigh
                transaction.amount,
                transaction.device_fingerprint,
                transaction.velocity_score,
                member.transaction_history
            not confident
                give back pending "Referred to manual review"
            ai.audit to fraud_audit_log
        decide: done

        consider isFraudulent
            when true
                update db.accounts
                    status = "frozen"
                    where id is transaction.account_id
                update: done
                miomail.send
                    to = transaction.user_email
                    subject = "Suspicious activity detected"
                miomail: done
                give back 403 "Transaction blocked"

            when false
                save transaction to db
                give back 200 "Transaction approved"
        consider: done

    new: done
listen: done
```

---

## The Back to Front Bridge

Mohio is the first language to seamlessly connect backend logic and frontend output from a single shape declaration — no separate API layer, no contract negotiation between teams, no client-side schema to keep in sync.

When you declare a shape, that shape is the contract everywhere: the listener validates against it, the database stores to it, the page renders from it, and the AI reasons about it. One source of truth. No translation required.

```mohio
// Define the shape once
shape Task
    id          as uuid     required    default uuid()
    title       as text     required    max 200
    status      as text     default "pending"
                            allowed "pending" "active" "complete"
    assigned_to as text
    due_date    as datetime
shape: done

// The listener validates against it (backend)
listen for
    new sh.Task
        require role "member"
        save task to db
        otherwise jump to "pages/board.mho"
    new: done
listen: done

// The page renders from it (frontend)
page board
fetch
    tasks = retrieve set of sh.Task from db where assigned_to is session.user_id
show
    <ul>
        each task in tasks
            <li class="{{ task.status }}">{{ task.title }}</li>
        each: done
    </ul>
```

The same `sh.Task` contract governs the POST, the database query, and the HTML output. Change the shape — the compiler tells you everywhere that needs to update. This is what platforms like Lovable and Emergent cannot do: their visual layers and their backend logic are separate systems that have to be kept in sync manually.

**Mohio doesn't have a frontend and a backend. It has one language.**

---

## What Other Platforms Hide

Every vibe coding platform and visual builder makes the same tradeoff: move fast early, pay the complexity bill later — usually when you try to go to production. Mohio surfaces what other platforms hide, at the point where it matters most.

| What gets hidden | What Mohio does |
|-----------------|-----------------|
| When dummy/mock data is being shown | `fetch` section is explicit — if you're not fetching real data, there's nothing to show |
| Which endpoints still need to be built | `listen for` declares every listener explicitly — gaps are visible in the file |
| Which database and whether it's structured or unstructured | `connect` is declared at journey level — type, location, and credentials are explicit |
| Dev to prod connection status | `secret.` vs `env.` makes the environment explicit. `mio secrets check` validates before deploy |
| Which libraries are needed or called | `assets` block in journey declares every external dependency — nothing implicit |
| Security status per listener | `require role` inside `new sh.[Shape]` — missing security is visible as missing code |
| AI decisions without audit trails | `ai.audit` is mandatory — the compiler rejects `ai.decide` blocks without it |
| Compliance gaps | `mio check compliance` lists every technical and non-technical gap with actionable next steps |

None of these are dashboard features or post-hoc audit tools. They are language features. The gaps show up in the code — before you deploy, not after.

---

## How Pages Work — Front End & Output

Not everything needs a listener. Pages handle output — HTML, CSS, JavaScript — structured in six optional sections in fixed order.

```mohio
page dashboard

needs
    member as sh.Member from session.user_id   // declare what the page needs

fetch
    stats = retrieve stats from db where user_id is member.id  // get data first

think
    summary = mioai.summarize stats             // logic and AI after fetch

show
    // standard HTML — {{ variable }} outputs auto-escaped values
    <main class="dashboard">
        <h1>Welcome back, {{ member.name }}</h1>
        <p>{{ summary }}</p>
        <div class="stats">
            <span>{{ stats.total_orders }}</span> orders
        </div>
    </main>

script
    // page-specific JavaScript — {{ }} interpolation works inside
    <script>
    document.querySelector('.stats').addEventListener('click', function() {
        fetch('/api/stats/{{ member.id }}/detail')
            .then(r => r.json())
            .then(data => console.log(data))
    })
    </script>

style
    // page-specific CSS — scoped to this page
    <style>
    .dashboard { max-width: 960px; margin: 0 auto; }
    </style>
```

| Section | Purpose | When |
|---------|---------|------|
| `needs` | Declare shapes, models, prompts from journey | 1st — declare dependencies |
| `fetch` | All data retrieval | 2nd — get the data |
| `think` | Logic and AI operations | 3rd — do stuff with it |
| `show` | HTML output with Mohio control flow | 4th — display the result |
| `script` | Page-specific JavaScript | 5th — add interactivity |
| `style` | Page-specific CSS | 6th — make it look right |

Include only the sections the page needs. A landing page might only need `show`. A data dashboard needs all six.

**Partials** are reusable page components — defined once, used anywhere:

```mohio
// partials/header.mho
partial header(title as text, user as sh.Member)
    display
    <header>
        <nav>
            <a href="/">Mohio</a>
            <span>{{ user.name }}</span>
        </nav>
    </header>

// Used in any show section
show header with title = "Dashboard", user = member
```

**External libraries** — JavaScript libraries, Google Fonts, external APIs — declared once in the journey `assets` block, available to every page:

```mohio
journey my_app
    assets
        font "https://fonts.googleapis.com/css2?family=Inter"
        style "/css/global.css"
        script "https://cdn.jsdelivr.net/npm/chart.js"
        favicon "/img/favicon.ico"
```

---

## Built-In Services — Zero Configuration

Every `mio*` service uses `secret.` variables — never hardcoded credentials, never SDK setup.

| Service | What it does |
|---------|-------------|
| `miomail` | Send, queue, receive email — templates, attachments, multipart |
| `miohttp` | Outbound HTTP — all methods, retry, timeout, streaming |
| `miofile` | Files and directories — read, write, upload, move, zip |
| `mioauth` | Auth — login, logout, JWT, OAuth, MFA, API keys, rate limiting |
| `miocache` | Redis-compatible caching — get, set, flush, patterns |
| `miolog` | Structured JSON logging — metrics, spans, alerts, observability hooks |
| `mioschedule` | Scheduling — named declarations in journey + programmatic inside tasks |
| `miopdf` | PDF — generate from HTML, merge, split, protect, fill forms |
| `mioimage` | Images — resize, crop, convert, watermark |
| `mioai` | AI generation — text, images, research, classify, embed, translate |
| `miosearch` | Full-text search — index, query, facet |
| `miostream` | Streaming and SSE — open, send, close, subscribe |
| `miodata` | Data transformation — XML, CSV, JSON, YAML |
| `miotest` | Testing — describe, expect, mock, snapshot |

Community connectors extend this list. Published to the Mohio registry, installed with `mio install [connector]`, used identically to built-in services.

---

## The CLI

> **`mio fmt --annotate`** deserves a special mention. Run it on any `.mho` file and the formatter adds a plain English interpretation comment to every line — exactly what the compiler reads it as. It's the fastest way to understand someone else's code, verify your own, and teach Mohio to a new team member. If the annotation doesn't match what you intended — rewrite the code until it does.

```bash
mio run file.mho               # Execute a program
mio serve journey.mho          # Start HTTP server
mio build journey.mho          # Compile without serving
mio test file.mho              # Run test suite
mio fmt file.mho               # Format — canonical output
mio fmt --annotate             # Add plain English interpretation comments to every line
mio fmt --clean                # Remove annotation comments
mio fmt --check                # CI mode — fail if not formatted
mio lint file.mho              # Lint for errors and warnings
mio lint --strict              # Errors and warnings both fail build
mio expand sector healthcare   # Show exactly what sector: healthcare adds
mio check sector healthcare    # Version, updates, urgent regulatory flags
mio check compliance           # Full compliance posture report
mio secrets check journey.mho  # Verify all secrets are resolvable
mio translate --to pt          # Translate keywords to Portuguese
mio translate --to es          # Translate keywords to Spanish
mio diagram journey.mho        # Visualize dependency flow
mio deploy journey.mho         # Deploy application
mio rollback journey.mho       # Rollback to previous version
mio cost report                # AI decision cost analysis
mio quality report             # AI decision quality monitoring
mio training export            # Export fine-tuning dataset
mio contribute submit file.mho # Submit program to community training set
```

---

## What's Coming

### Language Packs

See [Write Mohio in Your Language](#write-mohio-in-your-language) above for the full translation roadmap. Portuguese and Spanish ship first, Hindi and Filipino next, Polish and Czech after that. Language packs are community-contributed and Mohio-team certified.

### The Mohio Coding Platform

A visual development environment that generates clean, hand-editable `.mho` code. Not a no-code tool — a productivity layer for developers who want to move faster and for non-developer stakeholders who need to configure compliance rules and review AI decision logic without touching code.

Think Dreamweaver's two-way fidelity: visual and code views always in sync. Every visual action produces real, readable Mohio. You own it. You can modify it. The AI didn't write a black box.

This is also the foundation for AI-assisted Mohio authoring — describe what you want, the platform writes the `.mho`. Not vague generated code. Valid, readable, auditable Mohio that runs.

### Named Scheduling

`mioschedule [name]` declarations replace server cron jobs entirely — see the [Scheduling](#scheduling--mioschedule) section above for the full pattern. The runtime manages execution state. No SSH access. No crontab editing.

### The Mohio Cookbook

A separate GitHub repo (`github.com/countart/mohio-cookbook`) of complete, runnable `.mho` programs. Not snippets. Not pseudocode. Full working programs covering common patterns — fraud detection, HIPAA patient records, ticket routing, multi-tenant SaaS, content moderation, document intelligence, API gateways, and more. Community-contributed, Mohio-team curated.

### Certified Sector Profiles

Professionally maintained, legally reviewed, jurisdiction-aware sector profiles updated quarterly as regulations change. A hospital using certified profiles never has to worry about a regulation change breaking their compliance posture. Delivered via the commercial runtime subscription.

### The Commercial Runtime

Hosted, managed execution for enterprises that need guaranteed uptime, SLA support, managed compliance enforcement, and audit aggregation across multiple applications. Includes breach detection, regulatory export reports, auditor read-only portal, retention management, and the Mohio Certified compliance brand.

### The Fine-Tuned Mohio Model

A domain-specific reasoning model trained on production Mohio programs. Gets smarter as the language is used in production. The data accumulates. The model improves. This is the moat that cannot be forked.

---

## Sector Profile Coverage

| Sector | Activates | Key Field Intelligence | AI Constraints |
|--------|-----------|----------------------|----------------|
| `sector: financial` | PCI_DSS v4, SOC2, BSA/AML | `account_number`, `card_number`, `ssn`, `ein`, `transaction_id` | Fraud/credit decisions: audit + explain required |
| `sector: healthcare` | HIPAA, HITECH | `mrn`, `npi`, `diagnosis`, `dob`, `prescription`, `lab_result` | Clinical AI: confidence > 0.95, human review before action |
| `sector: legal` | SOC2 | `case_number`, `client_id`, `privileged_document` | Attorney-client privilege enforcement |
| `sector: education` | FERPA, COPPA | `student_id`, `gpa`, `disciplinary_record`, `parent_id` | Minor protection, parental consent enforcement |
| `sector: government` | FedRAMP, FISMA | `clearance_level`, `case_number`, `foil_exempt` | Classification enforcement, mandatory access logging |

All profiles are transparent — `mio expand sector [name]` shows every rule injected into your file. The profile notes section documents explicitly what each profile does not cover and what requires additional legal counsel. The language being honest about its own limitations is a trust feature, not a weakness.

---

## What Mohio Is Not

- **Not a no-code tool.** You write code. Real code with structure and form — that happens to read like English.
- **Not an AI wrapper.** `ai.decide` is a compiled language construct with typed returns, confidence thresholds, mandatory fallback, and automatic audit trails. Fundamentally different from calling an API and parsing a string.
- **Not a framework on top of another language.** Mohio is its own language — grammar, runtime, compiler, CLI.
- **Not only for enterprise.** A landing page and a regulated hospital system use the same language. Sector profiles and compliance declarations are there when you need them and out of the way when you don't.
- **Not opinionated about which AI model runs it.** Model-agnostic. The runtime routes to the right model based on return type, confidence threshold, and compliance mode. Swap providers in the journey file, not in application code.
- **Not finished.** The interpreter is being built against this locked specification. The syntax is stable.

---

## The Build Roadmap

| Stage | What | Status |
|-------|------|--------|
| 1 | Language Design Document v2.0 | 🔄 In progress |
| 2 | Lark grammar + parser | Upcoming |
| 3 | Tree-walking interpreter — core keywords, data operations | Upcoming |
| 4 | AI runtime — `ai.decide`, `ai.audit`, `ai.explain`, `ai.chain` | Upcoming |
| 5 | Built-in services — full `mio*` namespace | Upcoming |
| 6 | Sector profiles — financial and healthcare certified first | Upcoming |
| 7 | VS Code extension — highlighting, errors, autocomplete, audit viewer | Upcoming |
| 8 | Language packs — Portuguese and Spanish first | Upcoming |
| 9 | Public launch + Sector Pioneer Program open | Upcoming |
| 10 | Mohio Cookbook — separate repo, community contributions open | Upcoming |
| 11 | Mohio Coding Platform — visual builder | Future |
| 12 | Certified sector profiles — commercial tier | Future |
| 13 | Fine-tuned Mohio model | Future |

---

## The Sector Pioneer Program

Invite-only early access for financial and healthcare developers — the ones who feel the compliance pain most acutely.

**Pioneers get:**
- Early runtime access before public launch
- Free certified sector profile for their industry — professionally maintained, updated as regulations change
- Direct line to the Mohio team — your feedback shapes the language
- Recognition as a Mohio Sector Pioneer in the project credits and launch materials
- First access when certified profiles update for regulatory changes

**What we ask:**
- Use it. Build something real.
- Tell us what's wrong. Honest feedback via GitHub.
- If it works for you, let us mention you as an early user.

No contract. No commitment. No cost.

**Interested?** Email [hello@mohio.io](mailto:hello@mohio.io) — your name, what you build, and one sentence on the compliance problem that costs you the most time. First cohort: 10–20 developers per sector.

---

## The Business Model

The language is free. Forever.

| Layer | What | Model |
|-------|------|-------|
| Language core | Interpreter, CLI, VS Code extension, `mio*` built-ins, `.mho` spec | Open source — Apache 2.0 |
| Community | Cookbook, community connectors, language packs | Free — community-maintained |
| Commercial runtime | Hosted execution, SLA, audit aggregation, breach detection, reporting | Paid — SaaS + enterprise |
| Certified profiles | Professionally maintained sector profiles | Commercial runtime subscription |
| Mohio Platform | Visual builder, compliance dashboard, auditor portal | Paid — closed source |
| Fine-tuned model | Domain-specific reasoning model | Commercial runtime |

The business model never taxes usage of the language itself. Open core drives adoption at zero cost. The commercial runtime and platform convert serious users to revenue. The proprietary model, compliance brand, and enterprise contracts create the defensibility that makes Mohio acquisition-worthy in a 3–7 year horizon.

---

## Community

- **Discord:** [discord.gg/MF95H3wQdm](https://discord.gg/MF95H3wQdm) — design discussion, syntax feedback, use cases, showcase
- **GitHub Discussions:** Open — questions, syntax ideas, community connectors
- **Email:** [hello@mohio.io](mailto:hello@mohio.io)
- **Support the project:** [buymeacoffee.com/mohiolang](https://buymeacoffee.com/mohiolang)

---

## The Founding Story

The core syntax was designed on a phone while waiting for a tow truck on I-77 in North Carolina. The sector profile system was designed in a Dunkin' Donuts drive-thru line. The founding infrastructure — domain, GitHub repo, landing page, Discord server, email — was built in three hours. The Language Design Document reached v1.3 within 48 hours of the first session.

**Founder:** Ronnie Smith · **Owner:** Particular LLC · Built in the margins between day job, family, and other products.

---

## License

Apache 2.0 — free forever at the language and interpreter layer.

See [LICENSE](LICENSE) for full terms.

---

*Mohio · [mohio.io](https://mohio.io) · "Write intent. Execute reason. See everything."*
