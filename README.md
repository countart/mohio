<div align="center">

# MOHIO

### *Code that thinks the way you do.*

**The first programming language where AI reasoning and compliance are native primitives — not libraries, not API calls.**

[![License](https://img.shields.io/badge/license-Apache%202.0-teal.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-design%20phase-orange.svg)]()
[![CLI](https://img.shields.io/badge/CLI-mio-teal.svg)]()
[![Extension](https://img.shields.io/badge/files-.mho-teal.svg)]()

[mohio.io](https://mohio.io) · [Language Design Document](docs/LDD.md) · [Join the Waitlist](https://mohio.io#waitlist)

</div>

---

## What is Mohio?

Mohio *(Māori: "to know, to understand")* is an AI-native programming language where AI reasoning and compliance are first-class primitives. Every major language was designed for the constraints of its era. Mohio is designed for right now.

In every existing language, AI is bolted on — you import a library, call an API, parse a string, and hope the output matches what you expected. Compliance is bolted on too — install a framework, configure encryption, wire audit logging, read 200 pages of documentation. Mohio changes both at the architectural level. `ai.decide` is syntax. A confidence score is a type. A fallback is mandatory. `compliance: HIPAA` is one line. The audit trail is automatic.

```mohio
sector: financial

connect db    as postgres from env.DATABASE_URL
connect cache as redis    from env.REDIS_URL

route POST "/transaction/screen"
    require role "screener"
    validate request.body requires transaction_id, amount, member_id

    set transaction = fetch one from transactions
        where id = request.transaction_id

    set member = fetch one from members
        where id = transaction.member_id
        cache for 5 minutes

    ai.decide is_fraudulent(transaction) returns boolean
        confidence above 0.85
        weigh
            transaction.amount,
            transaction.location,
            member.transaction_history,
            member.typical_spend_pattern,
            transaction.device_fingerprint,
            transaction.velocity_score
        fallback
            give back pending "Referred to manual review"
            miolog.warn "Fraud check below threshold — manual review queued"
        ai.audit to fraud_audit_log

    consider is_fraudulent.result
        when true
            update transactions set status = "blocked" where id = transaction.id
            miomail.send
                to member.email
                subject "Transaction blocked"
                body fraud_alert_template with { transaction = transaction }
            give back 200 "Transaction blocked"

        when false
            update transactions set status = "approved" where id = transaction.id
            give back 200 "Transaction approved"
```

No prompt engineering. No API wiring. No parsing JSON responses. No try/catch around model calls. No compliance library to install. The language handles all of that — because it understands what `ai.decide` and `sector: financial` mean natively.

---

## The Six Problems Mohio Solves

| Problem | Every other language | Mohio |
|---|---|---|
| AI integration | Library or API call — bolted on | Native language primitive — built in |
| AI output types | Untyped string — you parse it | Declared return type enforced by runtime |
| AI failure handling | Optional try/catch — often skipped | Mandatory `fallback` — compiler rejects omission |
| Audit trail | Custom code — inconsistent or missing | Automatic on every `ai.decide` block |
| Compliance | Third-party frameworks, weeks of setup | One declaration activates full enforcement |
| Industry knowledge | Lives in developer heads, not code | `sector:` profiles load it automatically |

---

## Compliance as a Native Primitive

Mohio implements compliance at two levels:

### Level 1 — File Declaration (Regime Activation)
One keyword activates a complete compliance regime for the entire program:

```mohio
compliance: HIPAA    // or PCI_DSS, SOC2, GDPR, FINRA, CCPA
```

The runtime automatically enforces encryption, PII masking in logs, data retention, access controls, and generates compliance-format audit records for every `ai.decide` block — without additional developer work.

### Level 2 — Field Annotation (Granular Sensitivity)
Individual fields carry sensitivity markers that control how data flows through logging, caching, and API responses:

```mohio
set patient_ssn    as pii  = fetch ssn where id = patient.id
set diagnosis      as phi  = fetch diagnosis where id = visit.id
set card_number    as pci  = fetch card where token = tx.token
set internal_score as restricted = calculateRisk(patient)

// A field can carry multiple markers
set patient_card as phi pci = fetch payment where patient_id = patient.id
```

**The fused audit object:** When an `ai.decide` block runs inside a compliance regime, the AI reasoning and the compliance audit record are the same object. A HIPAA `ai.decide` block generates a HIPAA-format audit record automatically. The developer wrote neither — the runtime generated both.

### Explicit Compliance Actions (`cm.` namespace)
For surgical compliance operations beyond the file-level declaration:

```mohio
cm.retain user.records for 7 years
cm.purge(user.id) includes [user_records, audit_logs, session_data]
cm.notify breach affected "user_records" estimated_count 1400 within 72 hours
cm.report SOC2 period "Q1 2026" send to compliance_officer.email
```

---

## Sector Profiles

The first language with institutional knowledge built in. One declaration loads everything your industry requires:

```mohio
sector: healthcare   // activates HIPAA, HITECH — knows mrn, npi, phi, dob
sector: financial    // activates PCI_DSS, SOC2 — knows card_number, routing_number, CTR thresholds
sector: legal        // activates SOC2 — knows case_number, privileged_document
sector: government   // activates FedRAMP, FISMA — knows clearance_level, foil_exempt
sector: education    // activates FERPA, COPPA — knows student_id, minor protections
```

A developer working in healthcare does not need to know what PHI is, which fields carry it, how long records must be retained, or what AI decisions require human review. The language already knows.

Sectors stack. A health-tech fintech app uses both:
```mohio
sector: healthcare
sector: financial
compliance: SOC2
```

---

## Core Keywords

### Variables & Functions
```mohio
set username = "ronnie"          // mutable variable
hold MAX_RETRIES = 3             // immutable constant
task checkFraud(tx) { ... }      // define a function
give back risk_score             // return a value
```

### Control Flow
```mohio
if score > 0.8 { ... }
or if score > 0.5 { ... }
else { ... }

consider status                  // switch/case routing
    when "active" → process
    when "blocked" → reject
    otherwise → review

each tx in transactions { ... }  // iterate
repeat 3 times { ... }           // counted loop
while queue is not empty { ... } // conditional loop
```

### AI-Native Primitives (`ai.` namespace — hard reserved)
```mohio
ai.decide is_fraudulent(transaction) returns boolean
    confidence above 0.85
    weigh
        transaction.amount, member.history,
        device.fingerprint, velocity.pattern
    fallback
        give back false
        miolog.warn "Fell below confidence threshold"
    ai.audit to fraud_audit_log

set explanation = ai.explain decision fraud_check
    audience "compliance officer"
    format "paragraph"
```

### Data Operations
```mohio
fetch user where id = userId
save transaction
update user set status = "blocked"
remove session where expired = true
```

### Connections & I/O
```mohio
connect postgres as db from env.DATABASE_URL
connect redis   as cache from env.REDIS_URL

route POST "/screen" { ... }
receive from "stripe" verify signature env.STRIPE_SECRET
give back 200 "Approved"
```

### Built-In Services (`mio*` prefix)

| Built-in | Purpose |
|---|---|
| `miomail` | Send and receive email |
| `mioschedule` | Cron and event scheduling |
| `miofile` | File system read/write |
| `miohttp` | Outbound HTTP calls |
| `mioauth` | Authentication and session management |
| `miocache` | Redis-compatible caching |
| `miolog` | Structured logging with compliance tagging |
| `miotest` | Integrated unit and integration testing |
| `miopdf` | PDF generation and parsing |
| `miosms` | SMS messaging |
| `mioai` | AI generation — text, images, embeddings, research |
| `miolearn` | Feedback loop for model improvement |

---

## Design Philosophy

- **Forgiving syntax** — whitespace, semicolons, and brace placement are irrelevant. The language never blocks intent over formatting.
- **Natural English keywords** — `ai.decide`, `consider`, `ai.explain`, `fallback`, `fetch`, `give back`. Code reads like a specification.
- **Secrets enforced** — `env.VARIABLE` syntax, enforced at parse time. Hardcoded secrets are a parse error.
- **Compliance by declaration** — one keyword activates an entire regulatory regime.
- **Sector knowledge built in** — the language knows your industry before you write your first line.
- **Model-agnostic** — the runtime selects the appropriate AI model. Developers never specify models in application code.
- **`mio` CLI** — three keys, right hand, one motion.

---

## What Mohio Is NOT

- **Not a general-purpose language** — Mohio targets the application layer where AI reasoning, data operations, and service integrations converge. Not systems programming.
- **Not a no-code tool** — Mohio is a real programming language with a grammar, type system, runtime, and compiler. It requires developers.
- **Not an AI wrapper** — `ai.decide` is a language-level construct with runtime-enforced semantics. Fundamentally different from calling an AI API and parsing the response.
- **Not model-opinionated** — model selection is an infrastructure concern, not an application concern.
- **Not magic** — every decision is auditable. Every behavior is explainable. Nothing is hidden.

---

## Language Design Document

The full Language Design Document (LDD) is the founding specification for Mohio. It covers the complete philosophy, all keywords and semantics, three full example programs, compliance architecture, sector profiles, and the build roadmap.

📄 **[Read the full LDD →](docs/LDD.md)**

---

## Build Roadmap

| Phase | What | Timeline |
|---|---|---|
| **1** | Lark grammar + AST — `mio parse` works | Weeks 1–4 |
| **2** | Python interpreter — `mio run` executes non-AI programs | Weeks 5–10 |
| **3** | AI runtime — `ai.decide` blocks execute end-to-end | Weeks 11–16 |
| **4** | Web server — `mio serve` starts an HTTP service | Weeks 17–20 |
| **5** | VS Code extension — syntax, autocomplete, audit viewer | Weeks 21–24 |
| **6** | mio* built-in library — all services functional | Weeks 25–32 |
| **7** | Open core release — public launch, mohio.io, GitHub | Weeks 33–40 |

---

## The Stack

- **Interpreter:** Python + Lark parser
- **AI runtime:** Model-agnostic, routes to best available model
- **Connections:** PostgreSQL, MongoDB, Redis, REST, gRPC — native syntax
- **Compliance:** HIPAA, PCI_DSS, SOC2, GDPR, FINRA, CCPA
- **Sectors:** healthcare, financial, legal, government, education
- **CLI:** `mio` — parse, run, serve, deploy, test
- **Editor:** VS Code extension (marketplace)
- **Files:** `.mho`

---

## Follow the Build

Mohio is being built in public. Star and watch this repo to follow progress.

- 🌐 [mohio.io](https://mohio.io) — landing page and waitlist
- 💬 [Discord](https://discord.gg/mohio) — community and announcements
- 📋 Issues — bug reports and language feedback
- 🗺️ Project board — coming soon

---

## License

The Mohio language specification and interpreter are open source under the [Apache 2.0 License](LICENSE).

The Mohio Platform (visual builder, compliance dashboard, hosted runtime) is a separate commercial product.

---

<div align="center">

**MOHIO · mohio.io · `mio` CLI · `.mho` files**

*"Code that thinks the way you do."*

</div>
