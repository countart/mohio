# Mohio Roadmap

**Current phase:** Phase 1 complete  
**Updated:** April 2026

---

## What is built and working today

Everything listed here runs. You can clone the repo and execute it.

### Compiler

| Component | Detail |
|-----------|--------|
| Lark Earley grammar | 70/70 tests passing |
| AST — 60+ node types | Complete |
| Transformer | Strict closer validation (Option A). Named closer mismatches are compile errors with line numbers and suggested fixes. |
| Interpreter | Tree-walking executor. SQLite data layer. Structured audit logging. |
| Anthropic API runtime | Real Claude reasoning for `ai.decide` blocks. Confidence scoring. Typed results. |

### CLI — `mio`

| Command | Status |
|---------|--------|
| `mio run file.mho` | ✅ Working |
| `mio run --ai` | ✅ Real Anthropic API |
| `mio run --verbose` | ✅ Full execution trace |
| `mio run --request-file` | ✅ JSON request payload |
| `mio run --seed` | ✅ Database seed data |
| `mio run --param key=value` | ✅ Windows CMD compatible |
| `mio check file.mho` | ✅ Parse + validate, no execution |
| `mio version` | ✅ Working |
| `mio help` | ✅ Working |

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

### Sector profiles

Financial and healthcare sector profiles are fully documented and the runtime enforcement model is designed. Field type awareness, compliance frameworks, AI decision constraints, audit requirements, retention rules, and security minimums are all specified.

Full profile enforcement in the runtime is Phase 2.

---

## Phase 1.5 — Indentation preprocessor

**Priority: High | Status: In design**

The whitespace-agnostic Earley parser cannot determine block boundaries from indentation alone. This requires explicit closers on flow control blocks (`if`, `check`, `each`, `repeat`, `while`) in Phase 1.

Phase 1.5 adds a ~100-line tokenizer step that converts indented blocks to INDENT/DEDENT tokens before the parser sees the source. After this, the fraud demo looks like natural Mohio:

```mohio
if isFraudulent is true
    miolog.alert "High-risk transaction flagged"
    give back 422 "Transaction blocked pending review"

save to db.cleared_transactions
    id          transaction.id
    amount      transaction.amount
save: done
```

No `if: done`. Flow control reads naturally. Named data blocks still require explicit closers — which is correct, because they are precision-machined components.

---

## Phase 2 — Runtime and server layer

**Priority: High | Status: Planned**

### `mio serve`

HTTP server layer. A Mohio file with a `listen for` block becomes a running API server:

```bash
mio serve journey.mho --port 3000
```

This is the step that turns Mohio from a CLI tool into something you deploy.

### Real database connections

Currently SQLite for testing. Phase 2 adds real connection drivers:
- PostgreSQL
- MySQL
- SQLite (production)
- Redis (cache layer)

### `mio fmt`

Formatter. Canonical output. Normalizes all bare `done` closers to named closers. Consistent indentation. `mio fmt --annotate` adds plain-English comments to every block.

### `mio test`

Built-in test runner. Mohio test blocks execute against the interpreter with mocked AI and database responses.

### Full sector profile enforcement

The financial and healthcare sector profiles are fully specified. Phase 2 wires them into the runtime — compile-time field type enforcement, retention rule enforcement, AI decision constraint enforcement.

---

## Phase 3 — Platform and ecosystem

**Priority: Medium | Status: On the horizon**

### Fine-tuned Mohio model

A model trained on the LDD, the language reference, and working Mohio programs. The long-term moat. Initial training run estimated at a few thousand dollars once the language is stable.

Training data: LDD + Language Reference + Cookbook programs (opt-in only, three tiers).

### Visual platform

Drag-and-drop builder that generates Mohio code. Emergent-style mass-market play for the Natural Language Developer profile. The builder generates valid `.mho` files that run through the standard compiler.

### Healthcare demo

A second working demo alongside fraud detection. Clinical decision support — `sector: healthcare`, HIPAA enforcement visible, PHI audit trail written, 0.95 confidence threshold on treatment recommendations, human review gate before any action.

### Translation (language packs)

The prefix system makes translation possible — the dot separates category from word. Swap the word, the parser and runtime don't change.

Phase 1: Portuguese (Brazil + Portugal), Spanish (Latin America + Spain)  
Phase 2: Hindi, Filipino, Vietnamese

---

## Acquisition horizon

3–7 years. The language is not the product — the language is what you use to build the product. The sophistication is in the runtime. The simplicity is in the code. The business is in the tiers.

Strongest acquisition targets: Microsoft (VS Code integration, Azure compliance layer), Salesforce (industry cloud platform), ServiceNow.

---

## Competitive watch

All three core Mohio market positions verified unoccupied as of April 2026:

1. AI reasoning as a native language primitive (not a library)
2. Compliance as a declaration (not middleware)
3. Sector profiles as institutional knowledge in the language itself

Active watch: any announcement from major AI providers of language-level AI primitives. That is the most likely competitive threat, not another language project.

---

## First-mover window

Estimated 3–6 months before the space gets crowded enough to make first-mover advantage significantly harder to claim.

The compiler works. The demo runs. The gap between "working demo in a terminal" and "something developers can find, try, and talk about" is where the window is won or lost.

---

*Mohio Roadmap — Particular LLC — Apache 2.0*  
*This document is updated as milestones are hit. Check the commit history for the most recent state.*
