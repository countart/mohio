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

**Path is optional on a page.** The page is the location. Path is required in a journey, where listeners are declared globally.

**`from "Service"` on webhooks activates automatic signature verification.** Two guards run before your logic: signature check, then shape validation.

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

### AI Primitives

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

        make member
            find db.members where id is transaction.member_id
            cache for 5 minutes
        make: done

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
| `mioschedule` | Scheduling — cron, one-time, recurring tasks |
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

Write Mohio in your language. `mio translate` converts keyword-for-keyword between any two mapped languages. A developer in São Paulo reads the same `.mho` file as Portuguese. A developer in Mumbai reads it as Hindi. A developer in Chicago reads it as English. The code is identical. The comprehension is native.

**Portuguese and Spanish ship first.** Hindi follows. Additional language packs are published to the Mohio registry — any developer or team can propose and contribute a mapping. The Mohio team reviews and certifies each pack before it ships.

This is not a documentation feature. It is a compiler feature.

### The Mohio Coding Platform

A visual development environment that generates clean, hand-editable `.mho` code. Not a no-code tool — a productivity layer for developers who want to move faster and for non-developer stakeholders who need to configure compliance rules and review AI decision logic without touching code.

Think Dreamweaver's two-way fidelity: visual and code views always in sync. Every visual action produces real, readable Mohio. You own it. You can modify it. The AI didn't write a black box.

This is also the foundation for AI-assisted Mohio authoring — describe what you want, the platform writes the `.mho`. Not vague generated code. Valid, readable, auditable Mohio that runs.

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
