# Mohio Roadmap

**Current phase:** Phase 1 complete — compiler running, demo live  
**Updated:** April 2026  
**Language version:** v3.6 · LDD complete · 12 service appendices complete

---

## What is built and working today

Everything listed here runs. You can clone the repo and execute it.

### Language Design — Complete

| Document | Status |
|----------|--------|
| Language Design Document (LDD) v3.6 | ✅ Complete — 6,700+ paragraphs |
| Service appendices — 12 volumes | ✅ Complete — mioconnect through AI Primitives |
| MoQL specification + companion | ✅ Complete |
| CLI reference | ✅ Complete |
| Sector profiles — financial, healthcare | ✅ Designed and documented |
| Primitives & modifiers reference | ✅ Complete |

### Compiler

| Component | Detail |
|-----------|--------|
| Lark Earley grammar | ✅ 70/70 tests passing |
| AST — 60+ node types | ✅ Complete |
| Transformer | ✅ Strict closer validation. Named closer mismatches are compile errors with line numbers and suggested fixes. |
| Interpreter | ✅ Tree-walking executor. SQLite data layer. Structured audit logging. |
| Anthropic API runtime | ✅ Real Claude reasoning for `ai.decide`. Confidence scoring. Typed results. |

### CLI — `mio`

| Command | Status |
|---------|--------|
| `mio run [file.mho]` | ✅ Working |
| `mio run --ai` | ✅ Real Anthropic API |
| `mio run --verbose` | ✅ Full execution trace |
| `mio run --request-file` | ✅ JSON request payload |
| `mio run --seed` | ✅ Database seed data |
| `mio run --param key=value` | ✅ Windows CMD compatible |
| `mio check [file.mho]` | ✅ Parse + validate, no execution |
| `mio version` | ✅ Working |
| `mio help` | ✅ Working |
| `mio serve` | 📋 Phase 2 |
| `mio fmt` | 📋 Phase 2 |
| `mio test` | 📋 Phase 2 |
| `mio lint` | 📋 Phase 2 |
| `mio translate` | 📋 Phase 3 |
| `mio expand sector` | 📋 Phase 2 |
| `mio deploy` | 📋 Phase 3 |

### Fraud detection demo

73 lines of Mohio. Runs end to end with real Claude reasoning.

- Member lookup from database
- 24-hour transaction history query
- `ai.decide` with confidence threshold enforcement
- Immutable audit log written on every decision
- 422 on fraud (confidence ≥ 0.85, result = true)
- 202 on uncertainty (confidence < 0.85)
- 200 on approval — record saved to cleared transactions
- 403 on role mismatch

---

## Phase 1.5 — Indentation preprocessor

**Priority: High | Status: In progress**

A ~100-line tokenizer step that converts indented blocks to INDENT/DEDENT tokens before the Earley parser sees the source. After this, flow control reads naturally without explicit closers:

```mohio
check isFraudulent
    when true
        miolog.alert "High-risk transaction flagged"
        give back 422 "Transaction blocked pending review"
    otherwise
        give back clearTransaction(transaction)
check: done
```

Flow control reads naturally. Named data blocks and AI blocks always keep explicit closers — they are precision-machined components where the named closer is the documentation.

---

## Phase 2 — Runtime, server, and full language surface

**Priority: High | Status: Planned**

### `mio serve` — Application server

A Mohio file with a `listen for` block becomes a running application server:

```bash
mio serve journey.mho --port 3000
```

This is the step that turns Mohio from a CLI tool into something you deploy. Full HTTP, WebSocket, SSE, and scheduled task support. The complete language surface becomes operational.

### Real database connections

Production-grade drivers:
- PostgreSQL
- MySQL
- Redis (cache layer)
- SQLite (retained for development)

### `mio fmt` — Formatter

Canonical output. Normalizes all bare `done` closers to named closers. Consistent indentation. `mio fmt --annotate` adds plain-English comments to every block. `mio fmt --check` for CI pipelines.

### `mio test` — Built-in test runner

Mohio test blocks execute against the interpreter with mocked AI, mocked connectors, and database seed data. Full `expect` assertion library. Failure output that answers the four questions.

### `mio lint` — Linter

Three-tier output: compiler errors, runtime warnings, audit notices. Full four-question error messages throughout.

### Sector profile runtime enforcement

Financial and healthcare sector profiles are fully specified. Phase 2 wires them into the runtime — compile-time field type enforcement, retention rule enforcement, AI decision constraint enforcement, breach notification defaults.

### mioimage — Image processing primitives

Lock and implement the mioimage service — resize, crop, compress, convert, watermark. Consistent with Lock Blocks style: modifiers on save and make verbs, no attribute-soup.

### miotranslate — Translation primitive

Lock and implement the translation loop spec — ensuring that a file translated to Spanish and back to English loses zero architectural intent. The prefix system is the guarantee.

---

## Phase 3 — Platform, ecosystem, and scale

**Priority: Medium | Status: On the horizon**

### Visual builder — Mohio platform

Drag-and-drop builder that generates valid `.mho` files. Positioned against Lovable and Emergent — but Mohio generates real deployable production code, not prototypes. The builder outputs files that run through the standard compiler with zero modification.

Target audience: the Natural Language Developer who thinks in business intent, not code. The platform is the commercial layer. The language is always free.

### Vibe coding integration

Mohio is the answer to the vibe coding problem — not a tool that generates code you can't read, but a language where the generated code is the same as the intended code. The visual builder is the interface. Mohio is the output. Integration with AI-assisted development workflows.

### `mio serve` — Production runtime tier

Beyond the Phase 2 development server: managed deployment, compliance dashboard, uptime monitoring, cost reporting, AI decision quality monitoring. The hosted runtime commercial tier.

### Compiler rewrite — Rust

The Python/Lark implementation is the reference compiler. Phase 3 rewrites the compiler in Rust for production performance — faster parse times, lower memory footprint, native concurrency for `mio serve`. Rust's type system and memory safety make it the right choice for a language compiler that needs to be both fast and provably correct.

### Compiler in Mohio — eating our own cooking

The long-term target: the Mohio compiler written in Mohio. This is the proof that the language is complete — the language that can describe its own construction. After the Go rewrite stabilizes, this is the next milestone.

### Fine-tuned Mohio model

A model trained on the LDD, language reference, and working Mohio programs. Provider-agnostic via the Oumi framework. Training data: LDD + Language Reference + Cookbook programs (opt-in only, three tiers). Initial training run estimated at a few thousand dollars once the language is stable.

This is the long-term moat — a model that understands Mohio natively, not one that generates approximate Mohio from pattern matching on similar languages.

### Language packs — Zero-Drift multilingual

`mio translate` converts Mohio keywords to another language. The prefix system is the structural guarantee — it never changes. The parser, runtime, and compiled output are identical across languages.

| Phase | Languages |
|-------|-----------|
| First | Portuguese (Brazil + Portugal), Spanish (Latin America + Spain) |
| Second | Hindi, Filipino, Vietnamese |
| Third | Polish, Czech, French, additional — community-contributed |

### Sector expansion

Beyond financial and healthcare — the two sectors where compliance infrastructure spending is highest. Next sectors in order of market readiness:

| Sector | Key compliance | Target audience |
|--------|---------------|-----------------|
| `sector: legal` | Matter management, privilege, retention | Law firms, legal ops |
| `sector: government` | FedRAMP, FISMA, FOIA, records retention | GovTech, federal contractors |
| `sector: science` | IRB protocols, data provenance, reproducibility | Research institutions, pharma |
| `sector: insurance` | State regulations, claims, actuary data | Insurtech, carriers |
| `sector: education` | FERPA, student records, accessibility | EdTech, universities |

### Healthcare demo

A working demo alongside fraud detection. Clinical decision support — `sector: healthcare`, HIPAA enforcement visible, PHI audit trail written, 0.95 minimum confidence on treatment recommendations, human review gate before any action. **This is the next demo target.**

### RoundTableIQ — Mohio proof of concept

RoundTableIQ (a Particular LLC product) is being built in Mohio — the first real-world application demonstrating the full language surface including multi-persona AI streaming, session management, and the Understanding Arc in production. It is simultaneously a product and a proof of concept for the visual builder.

### Community shape library

`mio install shape [name]` — community-contributed shapes bringing domain knowledge as installable packages. Open registry. Certified shapes reviewed by the Mohio team. Commercial sector shapes (clinical-member, financial-instrument) in the paid tier.

---

## Acquisition horizon

3–7 years. The language is not the product — the language is what you use to build the product. The sophistication is in the runtime. The simplicity is in the code. The business is in the tiers.

**Strongest acquisition targets:**
- **Microsoft** — VS Code integration, Azure compliance layer, GitHub Copilot native Mohio support
- **Salesforce** — Industry cloud platform, Einstein AI layer, compliance-native development
- **ServiceNow** — Workflow automation, enterprise compliance, regulated industries

The acquirer buys the language, the runtime, the fine-tuned model, and the sector profile library. The open source layer is the distribution mechanism. The commercial layer is the asset.

---

## Competitive watch

All three core Mohio market positions verified unoccupied as of April 2026:

1. AI reasoning as a native language primitive — not a library
2. Compliance as a declaration — not middleware
3. Sector profiles as institutional knowledge in the language itself

**Primary threat:** An announcement from a major AI provider of language-level AI primitives. That is the most likely competitive threat — not another language project.

**Secondary threat:** A well-funded vibe coding platform adds a compliance layer and natural language output. Mohio's answer: the compiler validates the output. A vibe coding platform that generates unvalidated code is still unvalidated code.

---

## First-mover window

Estimated 4–9 months before the space gets crowded enough to make first-mover advantage significantly harder to claim.

The compiler works. The demo runs. The LDD is complete. The gap between "working demo in a terminal" and "something developers can find, try, and talk about" is where the window is won or lost.

---

*Mohio Roadmap — Particular LLC — Apache 2.0*
*This document is updated as milestones are hit.*
