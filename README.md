<div align="center">

# MOHIO

### *Code that thinks the way you do.*

**The first programming language where AI reasoning and compliance are native primitives — not libraries, not API calls.**

[![License](https://img.shields.io/badge/license-Apache%202.0-teal.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-design%20phase-orange.svg)]()
[![CLI](https://img.shields.io/badge/CLI-mio-teal.svg)]()
[![Extension](https://img.shields.io/badge/files-.mho-teal.svg)]()

[mohio.io](https://mohio.io) · [Language Design Document](#language-design-document) · [Join the Waitlist](https://mohio.io#waitlist)

</div>

---

## What is Mohio?

Mohio *(Māori: "to know, to understand")* is an AI-native programming language where AI reasoning is a first-class primitive. Every major language was designed for the constraints of its era. Mohio is designed for right now.

In every existing language, AI is bolted on — you import a library, call an API, parse a string, and hope the output matches what you expected. Mohio changes that at the architectural level. A `decide` block is syntax. A confidence score is a type. A fallback is mandatory. The audit trail is automatic.

```mohio
compliance: PCI_DSS

connect postgres as db

route POST /transaction {
  receive body as tx

  set recent = fetch transactions
             where user_id = tx.user_id
             and   created_at > now() - 24h

  decide isFraudulent returns bool confidence 0.85 {
    consider "Amount: ${{tx.amount}} — typical: ${{recent.avg_amount}}"
    consider "Transactions in last 24h: {{recent.count}}"
    consider "Location match: {{tx.location_known}}"
    explain reasoning
    audit "Fraud check for transaction {{tx.id}}"
    fallback { result false }
  }

  if isFraudulent {
    update account set status = "frozen" where id = tx.account_id
    miomail.send(
      to:      tx.user_email,
      subject: "Suspicious activity detected",
      body:    "We have frozen your account. Reply to verify.",
    )
    give back { status: 403, body: "Transaction blocked" }
  }

  save tx to db
  give back { status: 200, body: "Transaction approved" }
}
```

No prompt engineering. No API wiring. No parsing JSON responses. No try/catch around model calls. The language handles all of that — because it understands what `decide` means natively.

---

## The Five Problems Mohio Solves

| Problem | Every other language | Mohio |
|---|---|---|
| AI integration | Library or API call — bolted on | Native language primitive — built in |
| AI output types | Untyped string — you parse it | Declared return type enforced by runtime |
| AI failure handling | Optional try/catch — often skipped | Mandatory `fallback` — compiler rejects omission |
| Audit trail | Custom code — inconsistent or missing | Automatic on every `decide` block |
| Learning from use | Not possible — feedback disappears | `miolearn` closes the loop natively |

---

## Compliance as a Native Primitive

Mohio implements compliance at two levels:

### Level 1 — File Declaration (Regime Activation)
One keyword activates a complete compliance regime for the entire program:

```mohio
compliance: HIPAA    // or PCI_DSS, SOC2, GDPR, FINRA, CCPA
```

The runtime automatically enforces encryption, PII masking in logs, data retention, access controls, and generates compliance-format audit records for every `decide` block — without additional developer work.

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

**The fused audit object:** When a `decide` block runs inside a compliance regime, the AI reasoning and the compliance audit record are the same object. A HIPAA `decide` block generates a HIPAA-format audit record automatically. The developer wrote neither — the runtime generated both.

---

## Core Keywords

Mohio's keywords are natural English, organized into semantic groups:

### Variables & Functions
```mohio
set username = "ronnie"          // mutable variable
hold MAX_RETRIES = 3             // immutable constant
task checkFraud(tx) { ... }      // define a function
result risk_score                // return a value
```

### Control Flow
```mohio
if score > 0.8 { block }
or if score > 0.5 { block }
else { block }

when payment.received { ... }    // event-driven
otherwise { ... }

each tx in transactions { ... }  // iterate
repeat 3 times { ... }           // counted loop
while queue is not empty { ... } // conditional loop
```

### AI-Native Primitives
```mohio
decide isFraudulent returns bool confidence 0.85 {
  consider "context for reasoning"
  explain reasoning
  audit "log entry"
  fallback { result false }      // REQUIRED — compiler enforces this
}

set image = mioai {
  generate image
  prompt "A clean dashboard for {{app.name}}"
}

miolearn.signal(
  input:    ticket,
  predicted: routeTicket,
  correct:   agent.chosen_department,
)
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
connect postgres as db           // declared once at file top
connect redis   as cache

receive POST /transaction as tx
route GET /status { ... }
give back { status: 200, body: result }
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

---

## Design Philosophy

- **Forgiving syntax** — whitespace, semicolons, and brace placement are irrelevant. The language never blocks intent over formatting.
- **Natural English keywords** — `decide`, `consider`, `explain`, `fallback`, `fetch`, `give back`. Code reads like a specification.
- **Secrets enforced** — `env.VARIABLE` syntax, enforced at parse time. Hardcoded secrets are a parse error.
- **Compliance by declaration** — one keyword activates an entire regulatory regime.
- **Model-agnostic** — the runtime selects the appropriate AI model based on declared return type, confidence threshold, and compliance mode. Developers never specify models in application code.
- **`mio` CLI** — three keys, right hand, one motion.

---

## What Mohio Is NOT

- **Not a general-purpose language** — Mohio targets the application layer where AI reasoning, data operations, and service integrations converge. Not systems programming.
- **Not a no-code tool** — Mohio is a programming language with a grammar, type system, runtime, and compiler. It requires developers.
- **Not an AI wrapper** — the `decide` block is a language-level construct with runtime-enforced semantics. Fundamentally different from calling an AI API and parsing the response.
- **Not model-opinionated** — model selection is an infrastructure concern, not an application concern.

---

## Language Design Document

The full Language Design Document (LDD) is the founding specification for Mohio. It covers the complete philosophy, all keywords and semantics, three full example programs, compliance architecture, and the build roadmap.

📄 **[Read the full LDD →](docs/LDD.md)** *(coming soon)*

---

## Build Roadmap

| Phase | What | Timeline |
|---|---|---|
| **1** | Lark grammar + AST — `mio parse` works | Weeks 1–4 |
| **2** | Python interpreter — `mio run` executes non-AI programs | Weeks 5–10 |
| **3** | AI runtime — `decide` blocks execute end-to-end | Weeks 11–16 |
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
- **CLI:** `mio` — parse, run, serve, deploy, test
- **Editor:** VS Code extension (marketplace)
- **Files:** `.mho`

---

## Follow the Build

Mohio is being built in public. Star and watch this repo to follow progress.

- 🌐 [mohio.io](https://mohio.io) — landing page and waitlist
- 💬 Discussions — coming soon
- 📋 Issues — coming soon
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
