# Mohio

**Simplify the complicated. Make it stupid easy to build the most involved, insanely complicated programs.**

*The first programming language where AI reasoning is a native primitive and compliance is a declaration — not a library, not an API call, not an afterthought.*

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-teal.svg)](https://opensource.org/licenses/Apache-2.0)
[![Status: Design Phase](https://img.shields.io/badge/Status-Design%20Phase-orange.svg)]()
[![Discord](https://img.shields.io/badge/Discord-Join%20us-5865F2.svg)](https://discord.gg/MF95H3wQdm)

**CLI:** `mio` · **Extension:** `.mho` · **Domain:** [mohio.io](https://mohio.io) · **[Buy Me a Coffee](https://buymeacoffee.com/mohiolang)**

---

## Read This First

Mohio (pronounced *moh-hee-oh*, Māori for "to understand") is a programming language. You write code. It has structure, form, and rules. But the structure is English sentence structure. The rules are the rules of clear thought — not the rules of any language family that came before.

Read this aloud:

```mohio
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

If that sounded like English — you already understand Mohio. That is a complete, working, AI-powered fraud screening listener with mandatory fallback and automatic audit trail. No boilerplate. No framework configuration. No glue code.

---

## What Mohio Is

Mohio is for every developer — from a solo builder shipping a landing page to an enterprise team running regulated AI in production. Same language. Same syntax. Same rules.

```mohio
// A solo developer — landing page
listen for
    request for sh.Page
        show "pages/home.mho"
    request: done
listen: done
```

```mohio
// A startup — user identity
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
// An enterprise team — HIPAA-compliant AI triage
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

One language. Three scales. The Walk-By Test applies to every line: *if a manager glancing at a monitor for three seconds understands the business intent — it passes.*

---

## The Six Problems Mohio Solves

**1. AI is a guest in every other language.**
In Python, JavaScript, Go — AI is always something you import. A library. An SDK. A REST call. In Mohio, `ai.decide` is a language construct. The compiler understands it. The audit trail is automatic. You don't call AI — you write in it.

**2. Compliance is always bolted on.**
Every regulated industry forces developers to spend weeks wiring compliance before writing a single line of business logic. In Mohio, `compliance: HIPAA` is one line. The runtime handles the rest.

**3. Industry knowledge lives nowhere in code.**
A healthcare developer has to manually know that `mrn` is PHI, that records must be retained for 6 years, that AI diagnostic decisions require human review. In Mohio, `sector: healthcare` activates all of that automatically. The language knows the world you work in.

**4. AI outputs are untyped.**
Language models return text. In Mohio, `ai.decide` blocks declare their return type and confidence threshold. The runtime enforces them. You get a typed, validated result — not a string to parse.

**5. AI failures are silent.**
AI reasoning can fail — hallucinate, time out, return below-threshold confidence. In Mohio, every `ai.decide` block requires a `not confident` block. The compiler rejects code that omits one. Failure handling is not optional.

**6. Modern languages are hostile to speed and beginners.**
The path from idea to working service runs through package managers, framework choices, boilerplate scaffolding, and ORM configuration — before writing a single line of business logic. Mohio fixes this. Connections just work. Services are built in. The language is on your side from line one.

---

## How It Works

### The Nine Prefix System

Learn nine prefixes. Read any Mohio code immediately.

| Prefix | Meaning | Examples |
|--------|---------|---------|
| `by.` | How | `by.sending`, `by.joining` |
| `do.` | Constraint | `do.once`, `do.after`, `do.lock` |
| `on.` | Event | `on.failure`, `on.success`, `on.open` |
| `as.` | Form | `as.json`, `as.csv`, `as.pdf` |
| `to.` | Direction | `to.email`, `to.queue`, `to.log` |
| `in.` | Scope/Unit | `in.USD`, `in.background` |
| `is.` | State | `is.empty`, `is.valid`, `is.overdue` |
| `not.` | Negation | `not.empty`, `not.found` |
| `if.` | Condition | `if.exists`, `if: done` |

### Listeners, Not Endpoints

We've effectively removed the "Web" from Web Development. We aren't building endpoints — we are building Listeners.

`listen for` is a Multiplexing Container. One block handles everything a page or service receives. `new sh.[Shape]` is an Atomic Guard — incoming data is validated against the shape before a single line of your logic runs. Bad data never reaches your code.

```mohio
listen for
    require role "manager"

    new sh.Order
        make Invoice from order
            on.failure log "Invoice failed: {{ order.id }}"
            otherwise notify order.assigned_to
        make: done
    new: done

    request for sh.Inventory
        find Stock where item_id is inventory.id
            on.failure give back "Item not found"
            otherwise give back stock
        find: done
    request: done

listen: done
```

### The No-If Revolution

`on.failure` and `otherwise` handle the two-path case on any block — without a single `if`:

```mohio
check db.users for provided_id
    on.failure give back "Identity not found"
    otherwise give back "Identity Verified"
check: done
```

> *"By using `on.failure` and `otherwise`, you can write 90% of a SaaS application without a single if statement."*

### Sector Profiles — Institutional Knowledge Built In

```mohio
sector: financial   // Activates PCI_DSS, SOC2, field type awareness,
                    // CTR thresholds, AML rules, audit trail
sector: healthcare  // Activates HIPAA, HITECH, PHI retention,
                    // clinical AI constraints, breach notification
```

One declaration. The language already knows the world you work in.

### The Translation Factor

Mohio reads like English because it *is* English — structured, but English. A developer in São Paulo reads the same `.mho` file as Portuguese. A developer in Mumbai reads it as Hindi. The code is identical. The comprehension is native.

`mio translate --to pt` outputs a valid, runnable `.mho` file with localized keywords. Portuguese and Spanish ship first. Hindi follows.

---

## Quick Start

> **Note:** The Mohio interpreter is currently in development. The examples below represent final syntax — locked and stable. The runtime is being built against this spec.

```bash
# Coming soon
npm install -g mohio
mio run my_app.mho
mio serve my_journey.mho
```

---

## A Complete Example

Financial fraud detection. Compliance enforced. AI-powered. Fully audited. 

```mohio
sector: financial

connect postgres as db from secret.DATABASE_URL
connect redis as cache from secret.REDIS_URL

hold FRAUD_THRESHOLD = 0.85

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

Walk-By Test: **passes.** Read it aloud. Every line tells you exactly what it does.

---

## Sector Profiles

| Sector | Activates | Key AI Constraints |
|--------|-----------|-------------------|
| `sector: financial` | PCI_DSS, SOC2, BSA/AML | Fraud/credit decisions require audit + explain |
| `sector: healthcare` | HIPAA, HITECH | Clinical AI requires confidence > 0.95 + human review |
| `sector: legal` | SOC2 | Attorney-client privilege enforcement |
| `sector: education` | FERPA, COPPA | Minor protection, parental consent |
| `sector: government` | FedRAMP, FISMA | Classification enforcement |

---

## What Mohio Is Not

- **Not a no-code tool.** You write code. Real code, with structure and form — that happens to read like English.
- **Not an AI wrapper.** `ai.decide` is a compiled language construct with typed returns, confidence thresholds, mandatory fallback, and automatic audit trails. This is fundamentally different from calling an API and parsing a string.
- **Not a framework on top of another language.** Mohio is its own language — its own grammar, its own runtime, its own compiler.
- **Not only for enterprise.** A landing page and a regulated hospital system use the same language. The sector profiles and compliance declarations are there when you need them. They don't get in the way when you don't.
- **Not finished.** The interpreter is being built. This is the founding specification — locked, stable, and the basis for everything being built.

---

## The Build Roadmap

| Stage | What | Status |
|-------|------|--------|
| 1 | Language Design Document v2.0 | 🔄 In progress |
| 2 | Lark grammar + parser | Upcoming |
| 3 | Tree-walking interpreter (core keywords, data ops) | Upcoming |
| 4 | AI runtime layer (`ai.decide`, `ai.audit`, `ai.explain`) | Upcoming |
| 5 | Built-in services (`mio*` namespace) | Upcoming |
| 6 | Sector profiles (financial + healthcare first) | Upcoming |
| 7 | VS Code extension | Upcoming |
| 8 | Public launch + Sector Pioneer Program | Upcoming |

---

## The Sector Pioneer Program

Invite-only early access for financial and healthcare developers — the ones who feel the compliance pain most acutely. Pioneers get:

- Early runtime access before public launch
- Free certified sector profile for their industry
- Direct line to the Mohio team
- Recognition as a Mohio Sector Pioneer

**Interested?** Email [hello@mohio.io](mailto:hello@mohio.io) — one sentence on what you build and what compliance problem costs you the most time.

---

## Community

- **Discord:** [discord.gg/MF95H3wQdm](https://discord.gg/MF95H3wQdm)
- **GitHub Discussions:** Open — use it for questions, syntax feedback, and ideas
- **Email:** [hello@mohio.io](mailto:hello@mohio.io)
- **Support the project:** [buymeacoffee.com/mohiolang](https://buymeacoffee.com/mohiolang)

---

## The Founding Story

The core syntax was designed on a phone while waiting for a tow truck on I-77 in North Carolina. The sector profile system was designed in a Dunkin' Donuts drive-thru. The founding infrastructure — domain, GitHub, landing page, Discord — was built in three hours. The Language Design Document reached v1.3 within 48 hours of the first session.

**Founder:** Ronnie Smith · **Owner:** Particular LLC · Built in the margins between day job, family, and other products.

---

## License

Apache 2.0 — free forever at the language and interpreter layer.

The language is the open core. The platform, compliance dashboard, and certified sector profiles are the commercial tier. The business model never taxes usage of the language itself.

---

*Mohio — mohio.io · "Write intent. Execute reason. See everything."*
