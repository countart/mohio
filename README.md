<div align="center">

<img src="https://mohio.io/img/logo-spiral.png" alt="Mohio" width="120" />

# Mohio

### Write intent. Execute reason. See everything.

**The first programming language where AI reasoning is a native primitive — not a library, not an API, not an afterthought.**

[![Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-teal.svg)](LICENSE)
[![Discord](https://img.shields.io/badge/Discord-Join%20us-5865F2.svg)](https://discord.gg/MF95H3wQdm)
[![Buy Me a Coffee](https://img.shields.io/badge/Support-Buy%20Me%20a%20Coffee-FFDD00.svg)](https://buymeacoffee.com/mohiolang)
[![Status](https://img.shields.io/badge/Status-Language%20Design-orange.svg)](https://mohio.io)

*Mohio (moh-hee-oh) — from te reo Māori: to understand.*

</div>

---

## Make the difficult stupid easy.

Mohio exists to close the gap between what a developer means and what the code says.

Before you write a single line of business logic in a healthcare app, you spend weeks configuring HIPAA compliance, wiring PHI encryption, building access control, setting up audit logging, and reading 200 pages of regulation. In a fintech app, it's PCI setup, AML rules, CTR threshold detection, OFAC checks, and audit trails before you've touched the feature you were hired to build.

Mohio eliminates that grind. Compliance is a declaration. AI reasoning is a primitive. Security is built in. The boilerplate disappears — and what's left is exactly what you meant to write.

---

## The Walk-By Test

Every line of Mohio must pass this: a non-technical manager should glance at a screen for three seconds and understand the business intent — without a single bracket, semicolon, or variable name to decode.

Here's a fraud detection endpoint:

```mohio
sector: financial

listen for
    new sh.Transaction
        require role "screener" or "system"

        ai.decide isFraudulent(transaction) returns boolean
            check confidence above 0.85
            weigh
                transaction.amount,
                transaction.device_fingerprint,
                transaction.velocity_score
            not confident
                give back pending "Referred to manual review"
            ai.audit to fraud_audit_log
        decide: done

        give back fraud_decision
    new: done
listen: done
```

That's a production-grade, PCI-DSS-compliant fraud screening endpoint with AI decision-making, confidence thresholds, human fallback, and a permanent audit trail — in 22 lines. The `sector: financial` declaration at the top did the rest.

A manager walking by can read it. A compliance officer can audit it. A new developer can understand it on day one.

---

## What Mohio Is

Mohio is an AI-native programming language. The CLI is `mio`. Files use the `.mho` extension.

**Three things make it different from every other language:**

### 1. AI reasoning is native

`ai.decide` is not a function call. It is a language construct — understood by the compiler, enforced by the runtime, and automatically audited. The compiler will refuse to build if you forget the fallback. The runtime logs every decision. The audit trail is not optional.

```mohio
ai.decide approve_loan(application) returns result
    check confidence above 0.90
    weigh
        application.credit_score,
        application.debt_ratio,
        application.employment_history
    not confident
        give back pending "Referred to manual underwriting"
        notify underwriter_team
    ai.audit to compliance_log
decide: done
```

### 2. Compliance is a declaration

One line activates an entire compliance framework — field type awareness, encryption rules, retention policies, audit requirements, breach notification defaults, and AI decision constraints.

```mohio
sector: healthcare    // HIPAA, HITECH, PHI field types, 6-year retention
sector: financial     // PCI_DSS, SOC2, CTR thresholds, AML rules
```

You can inspect exactly what any sector declaration activates:

```bash
mio expand sector healthcare
mio check compliance healthcare
```

### 3. The shape is the contract

A Mohio shape is simultaneously the contract for database structure, API format, frontend validation, compliance enforcement, and UI component generation. Define it once. Enforce it everywhere.

```mohio
shape Transaction
    id              as uuid     required    default uuid()
    amount          as decimal  required    min 0.01        in.USD
    device_fingerprint as text  required
    velocity_score  as decimal  required    min 0.0 max 1.0
    status          as text     default "pending"
                    allowed "pending" "approved" "blocked"
    created_at      as datetime default now()
    retain for 5 years
shape: done
```

---

## Lock Blocks Architecture

Every piece of logic in Mohio is a block. Blocks lock together — verb opens, named closer seals. Like precision-machined components that can only connect one way, Mohio blocks compose without drift.

```mohio
// A block opens with its verb
find members in db.members
    where status is "active"
    order by created_at descending
    up to 50
    on.failure give back "No members found"
find: done

// Blocks nest. The nesting is always explicit.
listen for
    new sh.Order
        require role "customer" or "system"
        make Invoice from order
            on.failure notify ops_team about "Invoice creation failed"
            otherwise notify order.assigned_to
        make: done
    new: done
listen: done
```

The `done` tells you exactly what just ended. You never lose your place.

---

## The Modifier Prefix System

Learn nine prefixes. Read any Mohio code immediately.

| Prefix | Meaning | Examples |
|--------|---------|---------|
| `by.` | the how | `by.sending`, `by.streaming`, `by.joining` |
| `do.` | the rule / constraint | `do.once for id`, `do.after 5 seconds`, `do.encrypt` |
| `on.` | the reaction | `on.failure`, `on.success`, `on.open`, `on.change` |
| `as.` | the form | `as.json`, `as.csv`, `as.pdf`, `as.boolean` |
| `to.` | the destination | `to.email`, `to.queue`, `to.log` |
| `in.` | the where or unit | `in.USD`, `in.kilometers`, `in.background` |
| `is.` | the state test | `is.empty`, `is.valid`, `is.matching`, `is.overdue` |
| `not.` | the opposite | `not.empty`, `not.found`, `not.authorized` |
| `if.` | the when | `if.exists`, `if.empty` |

Once you know `on.` means reaction — you know it everywhere. `on.failure`, `on.success`, `on.open`, `on.close`, `on.change` all follow the same pattern.

---

## No-If Revolution

By using `on.failure` and `otherwise`, you can write 90% of a SaaS application without a single `if` statement. Every block has a straight-line success path and a named exit ramp.

```mohio
// The straight line: if it works, keep going.
// The exit ramp: on.failure handles the problem and exits.
// The fallback: otherwise catches everything else.

check db.members for provided_id
    on.failure give back 404 "Member not found"
    otherwise give back "Identity verified"
check: done
```

`if` is still valid — always. Mohio meets developers where they are.

---

## Sector Profiles

Sector profiles are the first instance of institutional knowledge built directly into a programming language.

When you declare `sector: financial`, Mohio already knows:
- What `routing_number`, `card_number`, `card_cvv`, `ssn`, and `ein` are
- That card CVV can never be stored (compiler enforces this)
- That transactions over $10,000 in cash trigger CTR requirements
- That AI credit decisions require ECOA-compliant adverse action explanations
- The AML flagging rules and SAR thresholds

When you declare `sector: healthcare`, Mohio already knows:
- What `mrn`, `npi`, `diagnosis`, `dob`, `prescription`, and `lab_result` are
- That PHI must be retained for 6 years (enforced automatically)
- That clinical AI decisions require 0.95 minimum confidence and human review
- That medication decisions require 0.98 confidence
- The HHS breach notification requirements

No configuration. No library installation. One word.

Available sectors: `financial` · `healthcare` · `legal` · `education` · `government`

---

## AI Primitives

Mohio ships with a full set of AI primitives — native to the language, not bolted on.

| Primitive | What it does |
|-----------|-------------|
| `ai.decide` | AI reasoning with typed return, confidence threshold, mandatory fallback, automatic audit |
| `ai.explain` | Plain-language explanation of a decision — audience and format specified inline |
| `ai.audit` | Permanent immutable decision record — inputs, outputs, confidence, model, timestamp |
| `ai.override` | Human correction of AI decision — auto-audited, auto-signals miolearn |
| `ai.chain` | Fallback sequence across AI providers with quality gates |
| `ai.create image` | Generate images inline |
| `ai.create audio` | Render text to audio |
| `ai.create logic` | Generate Mohio shapes and pages from natural language |
| `ai.create text` | Generate text content — subject lines, summaries, drafts |

The `mioai` namespace handles generation tasks: `.generate`, `.summarize`, `.embed`, `.research`, `.classify`, `.translate`.

---

## Built-In Services

Everything a modern application needs — declared, not configured.

```mohio
// Email — provider-agnostic, one word to switch providers
connect email as sender
    from brevo
    key secret.BREVO_API_KEY
connect: done

// Send
miomail.send
    to member.email
    from "hello@mohio.io" as "Mohio Support"
    subject "Your Invoice — {{ invoice.number }}"
    template "invoice_email"
    inject invoice into invoice inject: done
    attach invoice_pdf as "Invoice-{{ invoice.number }}.pdf"
    do.once for invoice.id
    do.encrypt
miomail: done
```

| Service | Purpose |
|---------|---------|
| `miomail` | Email — send, queue, receive. Provider-agnostic. |
| `miohttp` | Outbound HTTP — all methods |
| `miofile` | Files, directories, uploads with compression |
| `mioauth` | Auth — JWT, OAuth, MFA, API keys, role enforcement |
| `miocache` | Redis-compatible caching |
| `miolog` | Structured JSON logging and observability |
| `mioschedule` | Task scheduling — version controlled, no SSH required |
| `miopdf` | PDF generation and processing |
| `mioimage` | Image processing with compression |
| `miosearch` | Full-text search — Meilisearch, Elastic, Typesense |
| `miomap` | Geolocation, proximity search, geocoding, mapping |
| `miostream` | Streaming and SSE |
| `miopush` | Real-time push to active sessions |
| `mioaccess` | Accessibility — TTV narration, captions |

---

## Scheduling Without the Chaos

No cron strings. No SSH. No crontab.

```mohio
mioschedule weekly_digest
    every monday at 9am
    run SendWeeklyDigest
    on.failure alert ops_team
mioschedule: done

mioschedule payment_reminder
    in 3 days from order.created_at
    run SendPaymentReminder(order.id)
    do.once for order.id
mioschedule: done
```

Declared in code. Version controlled. Visible to the team. Auditable.

---

## Distributed Transactions — Visible Rollback

Sagas in Mohio show their rollback logic. A compliance officer can read exactly what the business does when things go wrong.

```mohio
saga process_order(order, payment)
    step charge_card
        check stripe with payment
            by.charging order.total
        on.rollback
            refund stripe.charge
    step: done

    step reserve_inventory
        update warehouse.inventory
            status = "reserved"
        on.rollback
            update warehouse.inventory
                status = "available"
    step: done
saga: done
```

The code is the policy.

---

## Language Packs

Translation is a compiler feature, not a documentation feature. The prefix system makes it possible — the dot separates category from word. Swap the word. The parser and runtime don't change.

```bash
mio translate file.mho --to pt   # Portuguese
mio translate file.mho --to es   # Spanish
```

**Phase 1:** Portuguese (Brazil + Portugal), Spanish (Latin America + Spain)  
**Phase 2:** Hindi, Filipino, Vietnamese  
**Phase 3:** Polish, Czech, and more

---

## Business Model

Mohio is open core.

| Tier | What's included | Cost |
|------|----------------|------|
| **Language + Runtime** | Full language, base interpreter, standard built-ins | Free / Apache 2.0 |
| **Certified Sector Profiles** | Professionally maintained, legally reviewed, regulatory-updated profiles | Commercial |
| **Visual Platform** | Drag-and-drop builder generating Mohio code | Commercial |
| **Hosted Runtime** | Managed deployment, compliance dashboard | Commercial |
| **Fine-Tuned Model** | Mohio-specific AI model trained on your codebase | Enterprise |

The language will never be closed. The sophistication is in the runtime. The simplicity is in the code. The business is in the tiers.

---

## The CLI

```bash
mio run file.mho           # Execute
mio serve journey.mho      # Start HTTP server
mio fmt file.mho           # Format — canonical output
mio fmt --annotate         # Add plain English comments to every line
mio lint file.mho          # Lint
mio test file.mho          # Run tests
mio expand sector financial # See exactly what sector: financial activates
mio check compliance healthcare  # Full compliance posture report
mio translate file.mho --to pt   # Translate to Portuguese
mio update                 # Update runtime drivers
```

---

## Three Developer Profiles

Mohio meets developers where they are.

**Craftsman** — Precise, intentional, explicit. Gets full control with zero boilerplate.

**Collaborator** — Works with domain experts — compliance, legal, clinical. Produces code that non-developers can review and verify.

**Natural Language Developer** — Thinks in business intent. Mohio accepts near-natural phrasing and canonicalizes it. No judgement.

---

## Get Started

```bash
# Install
npm install -g @mohio/cli

# Create a new project
mio new my-project
cd my-project

# Run
mio serve journey.mho
```

**Join the community:**

- 🌐 [mohio.io](https://mohio.io) — Homepage and waitlist
- 💬 [Discord](https://discord.gg/MF95H3wQdm) — Community
- 🐙 [GitHub](https://github.com/countart/mohio) — Source
- ☕ [Buy Me a Coffee](https://buymeacoffee.com/mohiolang) — Support the project

**Sector Pioneer Program** — We're inviting financial and healthcare developers to early access. [hello@mohio.io](mailto:hello@mohio.io)

---

## Status

Mohio is in active language design. The LDD (Language Definition Document) is the authoritative specification. The first-mover window is 6–12 months. The compiler is the next milestone.

Contributions, feedback, and sector-specific expertise are welcome. If you work in fintech, healthcare, legal, or government and see gaps in the sector profiles — open an issue or reach out directly.

---

<div align="center">

**Mohio Language Project · Particular LLC · Apache 2.0**

*"Write intent. Execute reason. See everything."*

</div>
