<div align="center">

<img src="https://mohio.io/img/logo-spiral.png" alt="Mohio" width="120" />

# Mohio

### Write intent. Execute reason. See everything.

**The first AI-native programming language — where AI reasoning is a compiler-enforced primitive, not a library, not an API, not an afterthought.**

[![BSL 1.1](https://img.shields.io/badge/license-BSL%201.1-teal.svg)](LICENSE)
[![Discord](https://img.shields.io/badge/Discord-Join%20us-5865F2.svg)](https://discord.gg/9tq7tGSNYE)
[![X](https://img.shields.io/badge/X-@mohiolang-000000.svg)](https://x.com/mohiolang)
[![LinkedIn](https://img.shields.io/badge/LinkedIn-Mohio-0077B5.svg)](https://linkedin.com/company/mohiolang)
[![Hugging Face](https://img.shields.io/badge/%F0%9F%A4%97-mohiolang-FFD700.svg)](https://huggingface.co/mohiolang)
[![Buy Me a Coffee](https://img.shields.io/badge/Support-Buy%20Me%20a%20Coffee-FFDD00.svg)](https://buymeacoffee.com/mohiolang)
[![Zork Demo](https://img.shields.io/badge/Live%20Demo-zork.mohio.io-0D7377.svg)](https://zork.mohio.io)

*Mohio (moh-hee-oh) — from te reo Māori: to understand.*

> **New here? Start with [`start-here/`](start-here/)** for install, your first program, and links to every verified guide, in one place.

*Conceived March 23, 2026. Dunkin drive-thru. Mooresville, NC.*
*Born later that day. Side of I77. Charlotte, NC.*

</div>

---

---

## AI-native does not mean AI writes your app

Mohio is a real programming language. You write it. It has rules, a compiler, and a grammar.

AI-native means one thing: `ai.decide`, `ai.audit`, `ai.agent`, and `ai.explain` are built into the language itself — enforced by the compiler, not bolted on as a library, not called through an API. When your Mohio program makes an AI decision, it is governed, auditable, and typed. That is what AI-native means.

If you want a tool that generates an app from a description, other products do that well. Mohio is what you reach for when you need code you can read, own, modify, and trust — with AI reasoning that is part of the program, not a black box beside it.

> **In one line:** Mohio is a programming language. You write it. AI reasons inside it.

---

## This just ran.

```
zork.mohio.io — live AI text adventure, session state, real Claude reasoning
```

One more demo. One command. Zero setup beyond an API key — and it runs with no key too, because the fallback path is the whole point.

**Mode A — no `ANTHROPIC_API_KEY` set.** This is the primary "run it yourself" path: a fresh clone, no external database, no key. It proves two things at once — the compiler refuses to ship an `ai.decide` with no fallback (shown below, under "The compiler refuses to build without a fallback"), and the fallback path genuinely fires when confidence doesn't clear the sector's floor.

```bash
mio run tests/support_escalation_demo.mho --seed tests/seed_support_escalation.json --memory --verbose
```

```
  [mohio.sector] note (line 9): using built-in baseline rules for 'demo_high' (no profile file found; field-type classifications inactive).
                 add sector-demo_high.sector (certified) or sector-demo_high.mho (community) on the search path for full enforcement.

  Loading tests/support_escalation_demo.mho (27 lines)
  9 | sector: demo_high
  ! sector: demo_high is a community or unverified profile. Review carefully before production use.
    Use an official Mohio sector profile for production compliance.

  Transformed -- 5 top-level statements
  AI runtime: mock (use --ai for real Anthropic API)
  Seed data: 1 rows across ['tickets']
  [sector] demo_high
  [sector] profile loaded: 0 field types, 1 confidence floors, 0 never-store fields
  [connect] SQLite in memory -- everything written is lost when the app stops. Fine for a test, never for real data.
  [connect] db as sqlite (DbRuntime)
  [retrieve] ticket from tickets
  [ai.decide] critical_decision: True (conf=0.91, threshold=0.95)
  [ai.audit] -> escalation_audit_log [4b20bbf75f5d4b38] sector:demo_high
  [show] Referred to a human agent -- confidence below the sector floor
  [show] critical_decision complete

  Result  MohioValue('critical_decision complete', 'string')
```

*(Trimmed to the load-bearing lines — the sector-profile and connect confirmations each print twice in the real output, once during static analysis and once at execution. The audit hash on the `[ai.audit]` line is different on every run by design.)*

The mock AI runtime returned `confidence=0.91`. The active sector's floor for this decision is `0.95`. `0.91 < 0.95`, so `not confident` fired — for real, deterministically, on every run, with zero API key and zero external database.

**Mode B — real Claude reasoning.** Requires `ANTHROPIC_API_KEY` set to a real key first. This is the one prerequisite this whole demo has, and it's stated here, not buried three sections down:

```bash
export ANTHROPIC_API_KEY=your_key
mio run tests/support_escalation_demo.mho --seed tests/seed_support_escalation.json --memory --ai --verbose
```

Real output, real key, real model call:

```
  AI runtime: Anthropic API (claude-sonnet-5)
  Seed data: 1 rows across ['tickets']
  [retrieve] ticket from tickets

[ai.decide -> API] critical_decision
  Model: claude-sonnet-5
  Inputs: ['ticket.priority_score', 'ticket.sentiment']
  Prompt:
Decision: critical_decision

Inputs:
  priority score: 72
  sentiment: frustrated

Return type expected: boolean

Respond with only the JSON object.
  Raw response: {"result": true, "confidence": 0.62, "explanation": "A priority score of 72
    combined with frustrated sentiment suggests elevated risk warranting critical
    classification, though the threshold for 'critical' isn't explicitly defined."}
  Result: True  Confidence: 0.62  Threshold: 0.95  Fell back: True
  [ai.decide] critical_decision: True (conf=0.62, threshold=0.95)
  [ai.audit] -> escalation_audit_log [e56a51858f8bacbd] sector:demo_high
  [show] Referred to a human agent -- confidence below the sector floor
  [show] critical_decision complete

  Result  MohioValue('critical_decision complete', 'string')
```

Genuine model reasoning over the real ticket data (`72`, `frustrated`) — not a script. Claude's own confidence (0.62) still didn't clear the sector's 0.95 floor, so the same `not confident` path fired again, this time because the model itself wasn't certain enough, not because of a hardcoded mock value.

---

## The support-escalation demo

Replaces the week-1 fraud/patient_intake/invoice_saga demos, which required a live Postgres connection to even start, and declared `sector: financial` / `sector: healthcare` — profiles that, verified by running, load zero rules today. This one declares `sector: demo_high`, one of the shipped, non-certified community sector profiles, and it genuinely loads and enforces one rule: a 0.95 confidence floor.

```mohio
sector: demo_high

connect db as sqlite from env.DATABASE_URL

retrieve ticket from db.tickets
    match id to "T-1042"
retrieve: done

ai.decide critical_decision returns boolean
    check confidence above 0.95
    weigh ticket.priority_score, ticket.sentiment
    ai.audit to escalation_audit_log
    not confident
        show "Referred to a human agent -- confidence below the sector floor"
    on.failure
        show "AI reasoning unavailable"
ai.decide: done

show "critical_decision complete"
```

`check confidence above 0.95` is not a typo — the program declares 0.95 up front because the active sector requires it. Try declaring less and `mio check` refuses to build the program at all (see the compliance section below). Zero API wiring in user code either way. The developer wrote intent. The runtime — and the sector profile — handled the rest.

---

## What makes Mohio different

### 1 — AI reasoning is a language construct, not a function call

`ai.decide` is understood by the compiler, enforced by the runtime, automatically audited.

**The compiler refuses to build without a fallback.** `tests/no_fallback_illustration.mho` is a minimal `ai.decide` with the `not confident` block deliberately removed — run it yourself:

```bash
mio check tests/no_fallback_illustration.mho
```

```
  x ai.decide 'critical_decision' is missing a 'not confident' block.
    Every ai.decide must define what happens when confidence falls below threshold.
    Add 'not confident' inside 'ai.decide critical_decision'.

  x  tests/no_fallback_illustration.mho  13 lines . 2 warning(s) . 2 error(s)
```

*(The real output currently reports this same error twice, in two slightly different wordings, from two separate checks in the compiler — a known duplicate-reporting quirk, not fixed here, not hidden either.)* And the real `--json` output for the same file — no invented error codes, just `"code": "ERROR"`:

```json
{
  "passed": false,
  "errors": [
    {
      "code": "ERROR",
      "line": 10,
      "message": "ai.decide 'critical_decision' is missing a 'not confident' block.",
      "hint": "Every ai.decide must define what happens when confidence falls below threshold.\nAdd 'not confident' inside 'ai.decide critical_decision'."
    }
  ]
}
```

**The audit trail is not optional.** `ai.audit to escalation_audit_log` writes an immutable record of every decision — inputs, result, confidence, model, timestamp.

**Confidence is a first-class value.** `confidence above 0.85` is enforced by the runtime, not the model.

### 2 — Compliance is a declaration

```mohio
sector: demo_high    // shipped community demo profile -- not certified, not legal compliance
```

One word activates the mechanism. No library installation. No configuration files. No manual wiring — and the mechanism is real, not aspirational. Real `mio check` output on `tests/support_escalation_demo.mho` with its `ai.decide` declared at `0.85` confidence, one notch under the active sector's floor:

```
  x ai.decide 'critical_decision' confidence 0.85 is below the demo_high sector floor of 0.95.
    Raise confidence to at least 0.95 for demo_high sector compliance, or, if this decision
    is non-regulatory, add sec.non_critical reason "...". See: mohio.io/docs/sectors/demo_high
```

The program does not compile until the declared confidence meets the sector's floor. That's the whole enforcement story for the profiles shipped today: one rule (a confidence floor), genuinely loaded, genuinely gating the build. `mio check` also labels the profile honestly, unprompted:

```
  ! sector: demo_high is a community or unverified profile. Review carefully before production use.
    Use an official Mohio sector profile for production compliance.
```

**What is not true today:** `sector: financial` and `sector: healthcare` — the names a regulated team would reach for first — currently load **zero rules**. Verified by running: `mio run` on a program declaring `sector: financial` prints `[sector] profile loaded: 0 field types, 0 confidence floors, 0 never-store fields`. Declaring either sector name is accepted by the compiler and does nothing yet.

*Mohio activates technical enforcement controls — not a guarantee of legal compliance. Qualified legal counsel does that.*

#### How this looks with a certified sector profile (commercial tier) — aspirational, does not run today

This is the target shape for `sector: financial` / `sector: healthcare` once a certified, legally-reviewed profile ships. None of the following runs against the current compiler — it is a design illustration, not a transcript:

```mohio
sector: financial    // aspirational: PCI-DSS v4.0.1, SOC2, BSA/AML thresholds, OFAC, AML rules
sector: healthcare   // aspirational: HIPAA, HITECH, PHI fields, 6-year retention, 0.95 AI floor
```

`sector: financial` would know `card_cvv` can never be stored — the compiler would enforce it. Would know cash over $10,000 triggers CTR. Would know SAR decisions require 0.95 confidence and human review.

`sector: healthcare` would know what `mrn`, `npi`, `diagnosis`, and `prescription` are. Would know clinical AI requires 0.95 confidence and human review. Would know PHI is retained 6 years.

The enforcement *mechanism* is wired and demonstrated above with the community profile. **Certified** sector profiles — legally reviewed and maintained, with rules like the ones sketched above — are a separate, commercial tier arriving after formal compliance review. See [Current state](#current-state--v421).

### 3 — The code reads like you wrote it in English

```mohio
find flagged in db.transactions
    where amount is above 10000
    and created_at is.in last 30 days
    order.down by amount
find: done

ai.decide shouldEscalate returns boolean
    confidence above 0.90
    weigh flagged.amount, flagged.velocity_score, flagged.device_fingerprint
    ai.audit to compliance_audit_log
    not confident
        give back false
ai.decide: done

give back flagged as.json
```

Walk-By Test: a non-developer reads that in three seconds and understands the business intent. This is a design constraint, not a style preference. Every keyword and syntax decision is evaluated against it.

### 4 — Named closers — precision-machined blocks

Every block opens with a verb and closes with its name. Closer mismatches are compile errors:

```
Line 24 — closer mismatch. Expected: ai.decide: done. Found: find: done.
The ai.decide block opened on line 18 is not closed.
Add 'ai.decide: done' before 'find: done'.
```

Block results can be bound at the closer:

```mohio
check skill_score as skill_level    // naming goes on the ACTION
    when skill_score contains "100"
        give back "expert"
    otherwise
        give back "novice"
check: done
```

### 5 — AI provider failover — `ai.connect`

Named provider groups with ordered fallback — declare once, reference anywhere:

```mohio
ai.connect fraud_providers, text_gen
    order
        anthropic
        anthropic model "claude-opus-4-8"
        openai    model "gpt-4o"
        azure
    order: done
    on.failure give back "Could not connect to LLM provider."
ai.connect: done

// Reference it inside ai.decide
ai.decide isFraudulent returns boolean
    using fraud_providers
    confidence above 0.85
    weigh
        transaction.amount
    not confident
        give back 202 "Referred for review."
ai.decide: done
```

### 6 — Agent safety — Deterministic Runtime Boundary Gate

```mohio
ai.agent researchAssistant
    goal "Research this topic and summarize findings"
    limits
        max steps    10
        cost ceiling 1.00
    limits: done
    not confident
        give back "Research incomplete — limits reached"
ai.agent: done
```

When `max steps` is reached, `_AgentLimitExceeded` fires — a hard stop enforced by the interpreter step counter, not a guideline. The agent cannot continue regardless of the model's output. Cost ceiling works the same way. Both are deterministic.

### 7 — mio check — structured compile-time validation

```bash
mio check myapp.mho                 # structural check
mio check myapp.mho --security      # 8 named security checks (one is sector-conditional)
mio check myapp.mho --json          # machine-readable for agents and CI
```

Every error includes line number and actionable hint. Real `--json` output on a program with an `ai.decide` missing its `not confident` block:

```json
{
  "errors": [
    {
      "code": "ERROR",
      "line": 3,
      "message": "ai.decide 'critical_decision' is missing a 'not confident' block.",
      "hint": "Every ai.decide must define what happens when confidence falls below threshold.\nAdd 'not confident' inside 'ai.decide critical_decision'."
    }
  ]
}
```

### 8 — Atomic upsert

```mohio
upsert db.game_state
    match session_id to session_id
    current_room current_room
    score        score
upsert: done
```

INSERT or UPDATE atomically. Native on SQLite (INSERT OR REPLACE), Postgres (ON CONFLICT), MySQL, MongoDB.

### 9 — Write Mohio in your language

The same program, authored in your own language. Structural connectors (`on.`,
`as.`, `ai.`) and the `mio*` service names never translate — only the readable
keywords do, so a program stays portable and the structure stays universal.

Language packs are in progress for Spanish, Portuguese, and Hindi, with the
mechanism designed to extend to any language. (Examples of translated source are
held back pending a provisional patent filing.)

---

## Firsts (as far as we know)

- First language where `ai.decide` is a compiler-enforced primitive
- First language where a missing AI fallback is a build error
- First language with automatic immutable AI audit trails as a language construct
- First language where compliance is a single declaration
- First language where sector profiles enforce AI confidence floors
- First language where a shape declaration serves DB, API, validation, compliance, and AI simultaneously
- First language with the Walk-By Test as a formal design constraint
- First language with named block closers as a compile-time safety mechanism
- First language with deterministic runtime boundary gates on AI agents
- A native Right to Be Forgotten verb (`cm.purge`) — syntax, mandatory `reason` audit modifier, and compile-time enforcement ship today; cascade-delete execution is in active development and fails safe
- Zero-drift multilingual syntax — write Mohio in your language; structural connectors (`on.`, `as.`, `ai.`) never translate

---

## Current state — v4.2.1

Mohio is built in the open and moving fast — the compiler updates multiple times a
day. The core language is solid and tested; the surface around it is filling in.
Status below is honest about where each piece actually stands.

**Legend:** **Working** = wired and tested, build on it today · **Partial** = wired,
actively being completed, fails loud where unbuilt · **Designed** = grammar or spec
in place, implementation pending.

| Component | Status |
|-----------|--------|
| Lark grammar — Earley parser | Working |
| AST (65+ node types) | Working |
| Transformer + MohioValidator | Working |
| Structured errors — `ERROR`/`WARNING` with file, line, message, hint | Working |
| `mio check --security` — 8 named checks (one is sector-conditional: a confidence-floor check that only appears when the active sector declares one) | Working |
| Tree-walking interpreter | Working |
| SQLite data layer | Working |
| PostgreSQL data layer | Working |
| db CRUD, match / find / retrieve | Working |
| ai.decide — confidence, fallback, audit | Working |
| Anthropic API integration | Working (real Claude reasoning) |
| casts, strings, includes, journey | Working |
| upsert | Working |
| mio check --json | Working |
| mio fmt — canonical formatter | Working |
| mio translate — source-language keyword translation | Working |
| Language Reference — generated from the grammar | Working — [live](https://mohiou.com/reference/) |
| mioschedule (Phase 1) | Working |
| mio warmup — Earley parse cache | Working |
| Zork text adventure (stress test) | Live at zork.mohio.io |
| ai.agent — boundary gate | Partial — wired, testing pending |
| ai.connect — named provider failover | Working |
| Compliance enforcement layer | Partial — mechanism wired, hardening in progress |
| sector profiles (activation, field types, floors) | Partial — wired, **not yet certified** |
| Auth / security built-ins | Partial — early |
| Web built-ins (routes, http, mail, file, cache) | Partial |
| cm.purge — right to be forgotten | Partial — verb + reason audit + compile-time enforcement ship; cascade-delete in dev, fails safe |
| Plugin registry — commercial extension point | Partial |

**On sectors:** the compliance *enforcement layer* exists and is wired — sector
declaration activates field classification, confidence floors, retention rules, and
audit hooks. What does **not** yet exist is a *certified* profile: legally reviewed,
attorney-vetted, with a maintenance commitment behind a certified seal. General
editable sectors are coming soon; certified **financial** and **healthcare** profiles
require paid compliance review and arrive after that work completes. Mohio activates
technical enforcement controls — it is not, and does not claim to be, a guarantee of
legal compliance.

**Phase 2 — grammar ready** (the grammar rule is live-wired into the parser; the construct parses and fails loud with a specific "not wired" or "commercial tier" message, rather than crashing or silently no-opping):
miostream, miovault (HSM secrets access — the parseable form runs into a deliberate commercial-tier gate today, not a "not built" message)

**Phase 3 — designed, not yet reachable from any parse** (the grammar rule exists in `mohio.lark` but is referenced by nothing else in the grammar, so no input can reach it — verified directly against the grammar, not inferred):
mioapp native mobile (iOS/Android), miochain blockchain, mioknow memory, Rust compiler

`mio translate` is not on either list — it graduated. It's a working CLI feature today (`mio translate --from en --to <lang> file.mho`), not a grammar-only construct; see the status table above.

---

## Documentation

The complete **[Language Reference](https://mohiou.com/reference/)** is generated
directly from the compiler grammar, so it always matches what `mio` actually
accepts. Every keyword is listed with its status — canonical, non-canonical,
retired, reserved, or not-yet-built — and every canonical example is verified to
compile. It is the place to start when writing real programs.

---

## Run it yourself

```bash
git clone https://github.com/countart/mohio
cd mohio
pip install -e .   # installs dependencies and the mio command

mio warmup
mio check tests/support_escalation_demo.mho
mio check tests/support_escalation_demo.mho --security
mio run   tests/support_escalation_demo.mho --seed tests/seed_support_escalation.json --memory --verbose

# Machine-readable output
mio check tests/support_escalation_demo.mho --json
```

The command above runs with no API key — it uses the mock AI runtime and an in-memory SQLite database, so it works on a completely fresh clone with no external setup.

**Real Claude reasoning requires `ANTHROPIC_API_KEY` set to a real key first** — stated here, not three sections later:

```bash
export ANTHROPIC_API_KEY=your_key
mio run tests/support_escalation_demo.mho --seed tests/seed_support_escalation.json --memory --ai --verbose
```

---

## Commercial features

Open core: full language, compiler, standard built-ins, community sector profiles — BSL 1.1.

In process or planned, commercial tier:
- `miochain.*` — blockchain integration, transaction signing via miovault
- `miovault` — HSM sidecar for key isolation
- `mioknow` managed — pgvector AI memory with sector compliance
- Certified sector profiles — legally reviewed, updated as regulations change

These four are not yet in [RESERVED-COMMERCIAL-OFFERINGS.md](RESERVED-COMMERCIAL-OFFERINGS.md) by name — that file covers reserved business-offering *categories* (hosted services, certification, marketplaces), not individual language constructs. Certified sector profiles are the one item above with a real match there. The language will never be closed.

---

## Sector Pioneer Program

Early access for financial and healthcare developers before public launch.

**What pioneers get:** Early runtime access, free certified sector profile, direct line to the team, pioneer credits.

**What we ask:** Build something real, tell us what's wrong, let us mention you if it works.

No contract. No commitment. No cost. Email **hello@mohio.io** with your sector and one sentence on the compliance problem that costs you the most time.

---

## Intellectual Property

Compiler-enforced AI safety primitives, sector-activated regulatory compliance enforcement, deterministic runtime boundary gates on AI agents, and agent-native structured compiler output are the subject of provisional patent applications filed with the USPTO.

For trademark and brand usage, see [TRADEMARKS.md](TRADEMARKS.md). For reserved commercial offerings, see [RESERVED-COMMERCIAL-OFFERINGS.md](RESERVED-COMMERCIAL-OFFERINGS.md).

For licensing: **hello@mohio.io**

---

## Join the community

| | |
|---|---|
| Website | [mohio.io](https://mohio.io) |
| Language Reference | [mohiou.com/reference](https://mohiou.com/reference/) |
| Live Demo | [zork.mohio.io](https://zork.mohio.io) |
| Discord | [discord.gg/9tq7tGSNYE](https://discord.gg/9tq7tGSNYE) |
| X / Twitter | [@mohiolang](https://x.com/mohiolang) |
| LinkedIn | [Mohio](https://linkedin.com/company/mohiolang) |
| Hugging Face | [mohiolang](https://huggingface.co/mohiolang) |
| Support | [buymeacoffee.com/mohiolang](https://buymeacoffee.com/mohiolang) |
| Email | [hello@mohio.io](mailto:hello@mohio.io) |

---

<div align="center">

**Mohio Language Project · Particular LLC · BSL 1.1**

*v4.8 · Write intent. Execute reason. See everything.*

</div>