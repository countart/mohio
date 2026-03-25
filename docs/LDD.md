# Mohio Language Design Document
## The Founding Specification
**Version:** 1.3 | **Status:** Active Design | **Date:** 2026-03-25  
**CLI:** `mio` | **Extension:** `.mho` | **Domain:** mohio.io

---

## Founding Philosophy

Mohio (pronounced *moh-hee-oh*, Māori for "to understand") is the first programming language where AI reasoning is a native primitive — not a library, not an API call, not an afterthought. It is also the first language with institutional knowledge built in: a developer working in healthcare, finance, or government can sit down on day one and write domain logic, not infrastructure.

The guiding principle: **write intent, execute reason, see everything.**

Mohio is forgiving in syntax and uncompromising in safety. It reads like English but executes with precision. It knows the world you work in before you write your first line.

---

## The Five Problems Mohio Solves

### 1. AI is a guest in every other language
In Python, JavaScript, Go — AI is always something you import. A library. An SDK. A REST call wrapped in try/catch. You manage the connection, parse the response, handle failures manually, and hope the output is what you expected. AI is never part of the language itself.

In Mohio, `ai.decide` is a language construct. The compiler understands it. The runtime enforces its rules. The audit trail is automatic. You don't call AI — you write in it.

### 2. Compliance is always bolted on
Every regulated industry — healthcare, finance, legal, government — forces developers to spend weeks wiring up compliance before writing a single line of business logic. Install the HIPAA library. Configure the encryption. Set up audit logging. Wire the access controls. Read 200 pages of documentation.

In Mohio, `compliance: HIPAA` is one line. The compiler and runtime handle the rest.

### 3. Industry knowledge lives nowhere in code
A healthcare developer has to manually know that `mrn` is PHI, that patient records must be retained for 6 years, that AI diagnostic decisions require human review. A fintech developer has to know CTR thresholds, AML rules, what fields can never be logged. None of that knowledge lives in any language — it lives in developer heads and compliance documents.

In Mohio, `sector: healthcare` and `sector: financial` are language declarations that activate pre-built institutional knowledge. Field types, retention rules, compliance frameworks, AI decision constraints — all loaded automatically.

### 4. Errors are written for compilers, not developers
Every language produces error messages that require translation. Stack traces, cryptic codes, line numbers without context. Junior developers google errors. Senior developers eventually memorize them.

In Mohio, every error is a sentence. It tells you what went wrong, why it's a problem, and exactly what to do. Calm, specific, actionable. Always.

### 5. Safety is opt-in everywhere else
In other languages, you add security, you add validation, you add sanitization, you remember to encrypt. Mohio inverts this. Unsafe code is what you have to opt into. Validation is built into the type system. Secrets must use `env.`. SQL injection is structurally impossible. AI decisions cannot ship without a fallback.

---

## Core Design Principles

1. **Natural language wins every time.** If you can say it in plain English, that's the syntax.
2. **One way to do the obvious thing.** No ten ways to query a database.
3. **Explicit beats implicit — but not verbose.** You say what you mean. The runtime does the rest.
4. **Declarations at the top. Logic in the middle. Output at the end.**
5. **Security and compliance are declarations, not libraries.**
6. **AI reasoning is native — not imported.**
7. **Every piece of data knows what it is.**
8. **Errors are written for humans.**
9. **The language knows the world you work in.**

---

## Keyword Design Rules

### Contextual Keywords
Most Mohio keywords are **contextual** — they are only treated as language keywords when they appear in the correct syntactic position. This prevents conflicts with natural variable naming.

```mohio
set consider = "option A"    ✅ variable — assignment position
consider status              ✅ keyword — block-opening position
    when "active" → ...
```

### Hard Reserved Keywords
AI primitives are **hard reserved** and cannot be used as variable names. The `ai.` prefix makes this self-evident and enforces it syntactically.

```
ai.decide   ai.audit   ai.explain   ai.fallback
```

### One Word, One Job
Every keyword in Mohio does exactly one thing in exactly one context. There are no overloaded keywords that mean different things depending on where they appear.

| Keyword | Exclusive role |
|---|---|
| `consider` | Switch/case routing — pure logic only |
| `weigh` | Input list inside `ai.decide` only |
| `fallback` | Safety path — inside `ai.decide` only |
| `sector:` | Industry profile declaration only |
| `compliance:` | Compliance framework declaration only |

---

## Language Structure

A Mohio file has three zones, in order:

```mohio
─────────────────────────────────────
ZONE 1: DECLARATIONS
sector:, compliance:, connect, use, include, app config
─────────────────────────────────────
ZONE 2: LOGIC
tasks, routes, modules, event handlers, AI blocks
─────────────────────────────────────
ZONE 3: OUTPUT
display, give back, redirect, stream
─────────────────────────────────────
```

---

## Namespace Architecture

Three namespaces, each with a clear lane:

| Prefix | Lane | Examples |
|---|---|---|
| `ai.` | Native AI reasoning primitives | `ai.decide`, `ai.audit`, `ai.explain` |
| `cm.` | Explicit compliance actions | `cm.retain`, `cm.purge`, `cm.notify`, `cm.report` |
| `mio*` | Built-in libraries | `miomail`, `miofile`, `mioauth`, `miohttp` |

---

## Core Keywords

### Variables & Values
| Keyword | Role | What it does |
|---|---|---|
| `set` | Action | Mutable variable |
| `hold` | Declaration | Immutable constant |
| `set ... default` | Action | Variable with fallback value |
| `env.` | Expression | Environment variable — only safe way to access secrets |

### Flow Control
| Keyword | Role | What it does |
|---|---|---|
| `if / or if / else` | Block | Conditional branching |
| `consider / when / otherwise` | Block | Multi-branch routing (switch/case) |
| `each` | Block | Iterate over collection |
| `repeat N times` | Block | Count-based loop |
| `while` | Block | Condition-based loop |
| `stop` | Action | Exit current loop |
| `skip` | Action | Skip to next iteration |
| `halt` | Action | Stop all execution |

### Tasks & Modules
| Keyword | Role | What it does |
|---|---|---|
| `task` | Declaration | Define a reusable function |
| `give back` | Action | Return a value from a task |
| `call` | Action | Invoke a task without assigning result |
| `module` | Declaration | Group related tasks into a named unit |
| `contract` | Declaration | Define an interface a module must implement |
| `use` | Declaration | Import a module or built-in |
| `include` | Declaration | Include another `.mho` file inline |

### Error Handling
| Keyword | Role | What it does |
|---|---|---|
| `try / catch / always` | Block | Error handling — `always` runs regardless of outcome |
| `raise` | Action | Throw a named error |
| `raise again` | Action | Re-throw a caught error |
| `on error` | Declaration | Application-level error handler |

### Data Operations
| Keyword | Role | What it does |
|---|---|---|
| `connect` | Declaration | Declare a named data source connection |
| `fetch` | Action | Read data from a connection |
| `save` | Action | Insert a new record |
| `update` | Action | Modify existing records |
| `remove` | Action | Delete records (requires `where`) |
| `transaction` | Block | Atomic multi-operation block |
| `cache for` | Modifier | Cache a result for a duration |

### HTTP & Routing
| Keyword | Role | What it does |
|---|---|---|
| `route` | Declaration | Define an HTTP endpoint inline |
| `receive` | Block | Handle inbound webhook |
| `redirect to` | Output | HTTP redirect |
| `forward to` | Action | Internal route forward |

### Security
| Keyword | Role | What it does |
|---|---|---|
| `require role` | Declaration | Role-based access enforcement |
| `sanitize` | Action | Input sanitization |
| `validate` | Action | Input validation with rules |
| `lock` | Block | Thread synchronization |

### Output
| Keyword | Role | What it does |
|---|---|---|
| `display` | Output | Render template or HTML block |
| `{{ variable }}` | Expression | Inline variable output — auto-escaped |
| `flush` | Action | Flush output buffer immediately |

### Events & Async
| Keyword | Role | What it does |
|---|---|---|
| `emit` | Action | Fire a named event |
| `when event` | Block | Handle a named event |
| `run async` | Action | Spawn background task |
| `wait for` | Action | Await async task result |

---

## AI Primitives (ai. namespace)

All AI reasoning primitives are hard reserved and prefixed with `ai.`.

### `ai.decide`
The core AI-native construct. Makes a reasoned decision with typed return, confidence threshold, mandatory fallback, and automatic audit trail. The compiler will not build without a `fallback` block.

```mohio
ai.decide is_fraudulent(transaction) returns boolean
    confidence above 0.85
    weigh
        transaction.amount, user.history, device.fingerprint,
        location.delta, velocity.pattern
    fallback
        give back false
        miolog.warn "Fraud check fell below confidence threshold"
    ai.audit to fraud_audit_log
```

**Compiler rule:** Missing `fallback` = build error. Non-negotiable.

### `ai.explain`
Generates plain-language explanation of a decision result. Target audience and format are specified inline.

```mohio
set reason = ai.explain decision fraud_check
    audience "compliance officer"
    format "paragraph"
```

### `ai.audit`
Records a decision permanently — inputs, outputs, confidence, model, timestamp. Used inline inside `ai.decide` via `ai.audit to [log]` or standalone.

### `weigh`
Sub-parameter inside `ai.decide` only. Lists the inputs the AI reasoning considers. Not a standalone keyword.

---

## Compliance Primitives (cm. namespace)

### Declaration
```mohio
compliance: HIPAA
compliance: SOC2
compliance: PCI_DSS
compliance: GDPR
```

When declared, the compiler and runtime enforce the framework automatically. No additional code required for standard enforcement.

### Explicit Actions

**`cm.retain`** — Data retention rule
```mohio
cm.retain user.records for 7 years
cm.expire session.data after 90 days
```

**`cm.purge`** — Right to delete
```mohio
cm.purge(user.id)
    includes [user_records, audit_logs, session_data]
    preserve [legal_hold_records]
```

**`cm.notify`** — Breach notification
```mohio
cm.notify breach
    affected "user_records"
    estimated_count 1400
    frameworks [HIPAA, GDPR]
    to authorities
    within 72 hours
```

**`cm.report`** — Compliance reporting
```mohio
cm.report SOC2
    period "Q1 2026"
    save to "reports/soc2-q1-2026.pdf"
    send to compliance_officer.email
```

### Field Tags
Fields are tagged inline at the data layer. Tags activate automatic enforcement based on declared compliance frameworks.

```mohio
save to patients values
    mrn         = form.mrn         [phi]
    diagnosis   = form.diagnosis   [phi]
    ssn         = form.ssn         [phi, pii]
    email       = form.email       [pii]
    card_number = form.card        [pci]
```

| Tag | Meaning |
|---|---|
| `[phi]` | Protected Health Information — HIPAA |
| `[pii]` | Personally Identifiable Information — GDPR, CCPA |
| `[pci]` | Payment Card Data — PCI_DSS |
| `[confidential]` | Business confidential — SOC2 |
| `[classified]` | Government classified — FedRAMP |

---

## Sector Profiles (sector: declaration)

### What They Are

Sector profiles are the first instance of **institutional knowledge built into a programming language.** When a developer declares a sector, the compiler loads a pre-built profile that activates compliance frameworks, defines industry-standard field types, sets retention rules, and applies AI decision constraints — automatically.

A developer working in healthcare does not need to know what PHI is, which fields carry it, how long records must be retained, or what AI decisions require human review. The language already knows.

### How They Work — The Macro Model

Sector profiles are `.mho` files that ship with the Mohio runtime. When the compiler encounters `sector: healthcare`, it expands the profile inline before compiling — generating real, readable Mohio code. Transparent, auditable, overridable.

A developer can inspect exactly what their sector declaration expanded to:
```
mio expand sector healthcare > expanded.mho
```

### The Declaration

```mohio
sector: healthcare
sector: financial
sector: legal
sector: education
sector: government
```

Sectors stack with compliance declarations and each other:

```mohio
sector: healthcare
sector: financial       ← a health-tech fintech app uses both
compliance: SOC2        ← add SOC2 on top
```

### Distribution & Monetization

Core sector profiles (healthcare, financial, legal, education, government) ship with the Mohio runtime as part of the open core. 

**Certified Sector Profiles** are a commercial tier — professionally maintained, legally reviewed, jurisdiction-aware, and updated as regulations change. Delivered via the Mohio commercial runtime subscription. This is the enterprise value proposition: compliance infrastructure that stays current without developer effort.

**Community Sector Profiles** — any developer or organization can publish a sector profile:
```mohio
sector: "mohio-registry/cannabis-us"
sector: "my-company/internal-healthcare"
```

### v1 Sector Profiles

| Sector | Frameworks Activated | Key Field Types | AI Constraints |
|---|---|---|---|
| `healthcare` | HIPAA, HITECH | mrn, npi, diagnosis, dob, prescription, insurance_id | Diagnostic decisions require human review, confidence > 0.95 |
| `financial` | PCI_DSS, SOC2 | account_number, routing_number, ssn, ein, transaction_id | Credit/fraud decisions require audit, confidence > 0.85 |
| `legal` | SOC2 | case_number, client_id, privileged_document | Attorney-client privilege enforcement, access logging |
| `education` | FERPA, COPPA | student_id, gpa, disciplinary_record, parent_id | Minor protection rules, parental consent enforcement |
| `government` | FedRAMP, FISMA | clearance_level, case_number, foil_exempt | Classification enforcement, mandatory access logging |

---

## Error Message Standard

Every Mohio error answers three questions: what went wrong, why it's a problem, what to do about it.

### Compiler Errors (won't build)
```
Line 14 — ai.decide "is_fraudulent" is missing a fallback block.
AI reasoning blocks must define what happens when confidence falls below threshold.
Add a fallback block inside this ai.decide before building.
```

```
Line 23 — Hardcoded secret detected: "sk_live_..."
Secrets must be stored in environment variables, not in source code.
Replace with: env.STRIPE_KEY
```

```
Line 31 — sector: healthcare is active.
Field "diagnosis" is tagged [phi] but this route has no require role declaration.
Add "require role" before accessing phi-tagged fields.
```

### Runtime Warnings (runs, but flagged)
```
Runtime — ai.decide "is_fraudulent" used fallback path. Line 42.
Confidence 0.71 fell below required threshold 0.85.
Fallback executed. Decision logged to fraud_audit_log.
```

### Audit Notices (transparency, not errors)
```
Audit — ai.decide "approve_loan" executed.
Inputs: credit_score=742, debt_ratio=0.31, employment_history=8yr
Result: approved | Confidence: 0.93 | Model: claude-sonnet-4
Logged: compliance_log | 2026-03-24T14:32:01Z
```

---

## Built-in Namespace Summary

| Namespace | Purpose | Key Methods |
|---|---|---|
| `miohttp` | Outbound HTTP | `.get .post .put .delete .patch` |
| `miomail` | Email | `.send .queue .receive .template` |
| `miofile` | Files & directories | `.read .write .upload .move .delete .dir.* .zip` |
| `mioauth` | Auth & access control | `.login .logout .oauth .jwt .mfa .apikey .roles .ratelimit` |
| `miocache` | Caching | `.get .set .delete .flush` |
| `miolog` | Logging & observability | `.info .warn .error .metric .span .time .alert` |
| `mioschedule` | Scheduling | `.every .at .on .cancel` |
| `miopdf` | PDF | `.from .merge .split .protect .fill` |
| `miotest` | Testing | `.describe .it .expect .mock .snapshot` |
| `miosearch` | Full-text search | `.index .query .facet .collection` |
| `mioimage` | Image processing | `.resize .crop .convert .watermark` |
| `mioai` | AI generation & processing | `.generate .summarize .embed .research .classify .translate` |
| `miosms` | SMS | `.send .receive` |
| `miostream` | Streaming / SSE | `.open .send .close .subscribe` |
| `miodata` | Data transformation | `.xml .csv .json .yaml .validate` |

---

## Complete Example: Financial Fraud Detection

```mohio
sector: financial

app config
    name    "TransactionScreener"
    version "1.0.0"
    timeout request after 30 seconds

connect db    as postgres from env.DATABASE_URL
connect cache as redis    from env.REDIS_URL

use mioauth
use miolog

on error
    miolog.error "Unhandled error: {{ error.message }}"
    give back 500 "Internal error"

route POST "/screen"
    require role "screener" or "system"
    validate request.body requires transaction_id, amount, member_id

    set transaction = fetch one from transactions
        where id = request.transaction_id

    set member = fetch one from members
        where id = transaction.member_id
        cache for 5 minutes

    if transaction.amount > 10000
        miolog.info "CTR threshold reached" with { amount = transaction.amount }
        cm.report "CTR" for transaction

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
            miolog.alert "Transaction blocked" via slack env.FRAUD_CHANNEL
                with { id = transaction.id, amount = transaction.amount }
            give back 200 "Transaction blocked"

        when false
            update transactions set status = "approved" where id = transaction.id
            give back 200 "Transaction approved"
```

---

## Complete Example: Healthcare Patient Records

```mohio
sector: healthcare

app config
    name    "PatientRecords"
    version "1.0.0"
    session expires in 8 hours

connect db as postgres from env.DATABASE_URL

use mioauth
use miolog

route POST "/patients"
    require role "clinician" or "admin"
    validate request.body requires mrn, name, dob, diagnosis

    transaction
        save to patients values
            mrn       = form.mrn       [phi]
            name      = form.name      [pii]
            dob       = form.dob       [phi, pii]
            diagnosis = form.diagnosis [phi]
            npi       = form.npi       [identifier]
            created   = now()

        miolog.info "Patient record created"
            with { mrn = form.mrn, clinician = session.user.id }

    give back 201 "Patient record created"

route DELETE "/patients/:id"
    require role "admin"
    cm.purge(request.id)
        includes [patient_records, appointment_history, billing_records]
        preserve [legal_hold_records]
    give back 200 "Patient data purged per right-to-delete request"
```

---

## Sector Profile Delivery

### How Profiles Are Distributed

**Bundled profiles** ship with the Mohio runtime — no network call required, works offline. Stored in the runtime directory:

```
/mohio/runtime/sectors/
    healthcare.mho
    financial.mho
    legal.mho
    education.mho
    government.mho
```

When the compiler encounters `sector: healthcare` it reads the local file and expands it inline before compiling. Fast, auditable, no external dependency.

**Registry profiles** — community and certified profiles — are fetched by identifier, cached locally, and version-locked:

```mohio
sector: "mohio-registry/cannabis-us-california"
sector: "my-company/internal-fintech"
```

This follows the same pattern developers know from npm and pip. Fetch once, lock version, verify hash.

### Transparency Requirements

Every sector profile must include a prominent header block:

```
## IMPORTANT — READ BEFORE DEPLOYING
## This profile reflects regulations as of [date].
## Regulations change. This profile is a starting point, not legal advice.
## Always verify current requirements with qualified legal counsel.
## Run "mio check sector [name]" to see if updates are available.
```

The `profile notes` section at the bottom of every profile explicitly lists what it does not cover. The language being honest about its own limitations is a trust feature, not a weakness.

### The `mio check` CLI Command

```bash
mio check sector healthcare
```

Returns:
- Profile version currently installed
- Whether a newer version is available
- What changed in the latest version
- Any urgent regulatory updates flagged by the Mohio team
- Date of last legal review

```bash
mio expand sector healthcare
```

Outputs the full expanded Mohio code that the profile injects into your file. Developers can see exactly what `sector: healthcare` added — nothing hidden, everything auditable.

### Tier Structure

| Tier | What It Is | Cost | Update Cadence |
|---|---|---|---|
| **Bundled** | Core 5 sectors ship with runtime | Free | Community maintained |
| **Certified** | Professionally maintained, legally reviewed, jurisdiction-aware | Commercial runtime subscription | Quarterly review minimum |
| **Community** | Developer/organization published via registry | Free | Maintainer-dependent |
| **Enterprise custom** | Mohio team builds and maintains a sector profile for your organization | Enterprise contract | As contracted |

The certified tier is the enterprise value proposition: compliance infrastructure that stays current without developer effort. A hospital paying for certified sector profiles never has to worry about a regulation change breaking their compliance posture.

---

## Go-To-Market — Sector Pioneer Program

### Concept

Rather than launching to everyone and hoping the right people find it, Mohio seeds adoption with sector specialists who already feel the compliance pain most acutely. These are the developers most likely to immediately understand the value, evangelize authentically, and provide the real-world feedback that makes the language better.

### How It Works

**Mohio Sector Pioneer Program** — invite-only, pre-launch access to certified sector profiles for Financial/Fintech and Healthcare developers.

Pioneers get:
- Early access to the Mohio runtime before public launch
- Free access to the certified sector profile for their industry
- Direct line to the Mohio team for feedback
- Name in the credits and "Mohio Sector Pioneer" recognition
- First access to certified profile updates
- Input on language features affecting their sector

In exchange:
- Real usage and honest feedback
- Bug reports via GitHub
- Permission to be referenced as an early user
- Optional: a brief case study or quote for the launch

### Target Pioneers — Phase 1

**Financial / Fintech** — 10-20 developers  
Target profile: Fintech developers at small-to-mid financial institutions, credit unions, payment processors, lending platforms. Anyone who has manually wired PCI compliance, CTR reporting, or AML logic and knows exactly how painful it is.

**Healthcare** — 10-20 developers  
Target profile: Developers at digital health startups, EHR integrators, telehealth platforms, healthcare middleware companies. Anyone who has spent weeks on HIPAA before writing a single line of clinical logic.

### Phase 2 Sectors (post-launch)
Legal, Government/FedRAMP, Education/FERPA

### Community Platform

**Primary: GitHub Discussions** — attached to the public repo, keeps feedback searchable and public, builds visible community activity, developers trust it for language projects. Conversations are permanent and indexable.

**Secondary: Discord** — for real-time conversation, announcements, and more informal support. Optional for pioneers, useful as the community grows.

Decision on platform timing: when the GitHub repo goes public.

### Waitlist Intelligence

The landing page waitlist form should include one optional field: **"What industry do you work in?"**

This gives sector demand intelligence on every signup before launch — which sectors have the most interest, where to focus certified profile development, which pioneers to prioritize for outreach. One field, high signal.

---

## Build Roadmap

### Stage 1 — Specification (current)
Language Design Document. Primitives reference. Sector profile definitions. Grammar definition. This is the founding artifact.

### Stage 2 — Interpreter
Python-based tree-walking interpreter using Lark parser. Handles core keywords, data operations, task definitions, basic routing.

### Stage 3 — AI Runtime Layer
Wire `ai.decide`, `ai.explain`, `ai.audit` to real model calls. Confidence handling. Fallback enforcement. Audit log output.

### Stage 4 — Built-ins
Implement `mio*` namespaces. Start with `miohttp`, `miomail`, `miofile`, `mioauth`. Each as a Python module behind the Mohio interface.

### Stage 5 — Sector Profiles
Implement profile expansion. Ship healthcare and financial as first certified profiles. Build the `mio expand` inspection command.

### Stage 6 — Compliance Runtime
`cm.` namespace implementation. Field tag enforcement. Compliance framework rules as runtime behavior.

### Stage 7 — Tooling
VS Code extension (syntax highlighting, error underlining). CLI (`mio run`, `mio test`, `mio expand`). Documentation site.

### Stage 8 — Demo & Launch
Fraud detection demo. Healthcare records demo. Landing page. GitHub repo public. Waitlist open.

---

## What Mohio Is Not

- **Not a ColdFusion replacement.** No lineage, no nostalgia, no compatibility layer.
- **Not a no-code tool.** Developers write Mohio. It is a real programming language.
- **Not a wrapper around AI APIs.** AI reasoning is native to the language, not a library bolted on.
- **Not opinionated about your stack.** Mohio talks to any database, any API, any AI provider.
- **Not magic.** Every decision is auditable. Every behavior is explainable. Nothing is hidden.

---

## Competitive Intelligence & Reality Checks

This section is a standing record of the competitive landscape. It is reviewed on a rolling basis — any time a significant language, platform, or compliance tool launches, an entry is added. If a threat encroaches on any of Mohio's three core differentiators, a response strategy is documented immediately.

**Mohio's three core differentiators — the lines to defend:**
1. AI as a language primitive (`ai.decide`, `ai.audit`, `ai.explain`)
2. Compliance as a declaration (`compliance:`, `cm.`)
3. Sector knowledge built into the language (`sector:`)

---

### Competitive Map — Current

| What They Do | Who | Threat Level | Notes |
|---|---|---|---|
| AI performance — Python at C++ speed | Mojo / Modular | ✅ None | Different problem entirely — speed, not reasoning |
| AI libraries / orchestration | LangChain, LlamaIndex, DSPy | ✅ None | Libraries bolted onto existing languages, not native primitives |
| Compliance dashboards / audit tools | Vanta, Drata, Secureframe, Cynomi | ✅ None | Post-hoc tooling, not language-level enforcement |
| Compliance at infrastructure layer | Red Hat OpenShift AI | ⚠️ Watch | Platform-level HIPAA/PCI enforcement — different layer but same developer pain |
| AI-native language primitives | Nobody | 🟢 Open space | Verified March 2026 |
| Sector profiles in a language | Nobody | 🟢 Open space | Verified March 2026 |

---

### Watch List

**Red Hat OpenShift AI** — *Added 2026-03-25*  
Red Hat is providing HIPAA, PCI DSS, and FedRAMP enforcement at the infrastructure/platform layer — encrypted storage, role-based access, audit trails, and compliance scanning baked into their Kubernetes platform. This is not a language play — it's infrastructure. But they are attacking the same developer pain: "I don't want to wire compliance myself."  

**Threat assessment:** Low currently, worth watching. Their approach requires enterprises to run OpenShift, which is a major infrastructure commitment. Mohio's approach works anywhere Python runs — no platform lock-in, no vendor commitment. If Red Hat moves toward a language-level DSL or developer SDK that abstracts compliance into code constructs, that changes the assessment.

**Response if escalated:** Emphasize Mohio's portability advantage. OpenShift is enterprise infrastructure. Mohio is a developer's language that runs on a laptop, a Docker container, a cloud function, or an edge node. Same compliance result, zero platform dependency.

**Mojo / Modular** — *Noted 2026-03-25*  
Mojo is solving the AI performance problem — Python syntax at C++ speed, with native GPU access. They target ML engineers building training pipelines and inference engines. Mohio targets application developers building AI-integrated services. No overlap on core value proposition. Mojo is reaching 1.0 in H1 2026 — watch for any pivot toward application-layer AI features that might drift into Mohio's space.

---

### Reality Check Schedule

| Cadence | Trigger |
|---|---|
| Every 2 weeks | Routine check — new language releases, compliance tool launches |
| Immediate | Any new language announces AI-native primitives |
| Immediate | Any platform announces compliance-as-code or sector profiles |
| Immediate | Major regulatory change affecting any active sector profile |

---

### Regulatory Watch — Active

| Regulation | Change | Status | Mohio Impact |
|---|---|---|---|
| HIPAA Security Rule | Jan 2025 NPRM eliminates "addressable" specs — MFA and encryption now mandatory | ✅ Applied in sector-healthcare.mho v1.1 | MFA elevated from recommended to required |
| PCI DSS | v4.0.1 published — limited revision to v4.0 | Monitor | No change to sector-financial.mho required yet |
| EU AI Act | Phased enforcement beginning 2025-2026 | Monitor | May require `sector: ai-research` or `compliance: EU_AI_ACT` |

---

## Version History

| Version | Date | Changes |
|---|---|---|
| 1.0 | 2026-03-24 | Initial LDD — founding philosophy, five problems, core keywords, examples, roadmap |
| 1.1 | 2026-03-24 | Added: `ai.` prefix for all AI primitives. `cm.` namespace. `sector:` declaration and profile system. Field tag syntax. Keyword reservation rules. Error message standard. `weigh` inside `ai.decide`. `consider` locked to switch/case only. |
| 1.2 | 2026-03-24 | Added: Sector profile delivery mechanism. `mio check` and `mio expand` CLI commands. Transparency requirements. Pioneer Program — Financial and Healthcare phase 1. Waitlist sector intelligence field. |
| 1.3 | 2026-03-25 | Added: Competitive intelligence section with watch list and reality check schedule. Regulatory watch table. EU AI Act flagged for monitoring. Red Hat OpenShift AI added to watch list. `sector-healthcare.mho` updated to v1.1 — MFA now mandatory per Jan 2025 HIPAA rule update. |

---

*Mohio Language Design Document — mohio.io*  
*"Write intent. Execute reason. See everything."*
