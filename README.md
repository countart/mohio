<div align="center">

<img src="https://mohio.io/img/logo-spiral.png" alt="Mohio" width="120" />

# Mohio

### Write intent. Execute reason. See everything.

**The first programming language where AI reasoning is a native primitive — not a library, not an API, not an afterthought.**

[![Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-teal.svg)](LICENSE)
[![Discord](https://img.shields.io/badge/Discord-Join%20us-5865F2.svg)](https://discord.gg/MF95H3wQdm)
[![Buy Me a Coffee](https://img.shields.io/badge/Support-Buy%20Me%20a%20Coffee-FFDD00.svg)](https://buymeacoffee.com/mohiolang)
[![Status](https://img.shields.io/badge/Status-Phase%201%20Complete-teal.svg)](https://mohio.io)

*Mohio (moh-hee-oh) — from te reo Māori: to understand.*

</div>

---

## This just ran.

```
mio run fraud_demo.mho --request-file request.json --seed seed.json --ai --verbose
```

```
[retrieve] member from members → {'name': 'Alice Chen', 'history': '3 years, clean history, average spend $200/month'}
[find] recent in transactions → 3 rows

[ai.decide → API] isFraudulent
  Model: claude-sonnet-4-20250514
  Inputs: ['transaction.amount', 'transaction.device_id', 'member.history', 'recent']

  Raw response: {"result": true, "confidence": 0.95,
    "explanation": "Transaction amount of $74,500 is over 300 times the
    average monthly spend from an unrecognized device with no recent
    transaction history."}

  Result: True  Confidence: 0.95  Threshold: 0.85  Fell back: False

[ai.audit] → fraud_audit_log: {
  'decision': 'isFraudulent', 'result': 'True',
  'confidence': 0.95, 'model': 'claude-sonnet-4-20250514',
  'inputs': {'amount': '74500', 'device_id': 'D_NEW_UNRECOGNIZED', ...},
  'ts': '2026-04-22T16:34:49'
}
[miolog.alert] High-risk transaction flagged

  Response  422  Transaction blocked pending review
```

That is real output. Real Claude reasoning. Real audit trail. Real block.

The code that produced it is 73 lines of Mohio.

---

## The code

```mohio
sector: financial

connect db as postgres from env.DATABASE_URL

hold FRAUD_THRESHOLD 0.85

shape Transaction
    id          as text
    amount      as decimal
    currency    as text
    member_id   as text
    merchant    as text
    device_id   as text
    timestamp   as datetime
shape: done

task clearTransaction(transaction) returns text
    save to db.cleared_transactions
        id          transaction.id
        amount      transaction.amount
        member_id   transaction.member_id
        cleared_at  now()
    save: done
    give back "Transaction approved"
clearTransaction: done

listen for

    new sh.Transaction
        require role "screener" or "system"

        retrieve member from db.members
            match id is transaction.member_id
        retrieve: done

        find recent in db.transactions
            where member_id is transaction.member_id
            and timestamp since now() - 24 hours
        find: done

        ai.decide isFraudulent(transaction) returns boolean
            check confidence above FRAUD_THRESHOLD
            weigh transaction.amount, transaction.device_id, member.history, recent
            ai.audit to fraud_audit_log
            not confident
                give back 202 "Referred to manual review"
            on.failure
                give back 503 "Fraud check unavailable"
        ai.decide: done

        if isFraudulent is true
            miolog.alert "High-risk transaction flagged"
            give back 422 "Transaction blocked pending review"
        if: done

        result clearTransaction(transaction)
        give back 200 result

    new: done

listen: done
```

A non-technical manager can read this. A compliance officer can audit it. A new developer understands it on day one. That's the Walk-By Test — and every line of Mohio has to pass it.

---

## What makes Mohio different

### AI reasoning is a language construct, not a function call

`ai.decide` is understood by the compiler, enforced by the runtime, and automatically audited. Three things the language enforces that no other language does:

**The compiler refuses to build without a fallback.** Every `ai.decide` must have a `not confident` block. If confidence falls below the threshold and there is no defined fallback, that is not a warning — it is a build error.

```
Compile error — ai.decide "isFraudulent" is missing a "not confident" block.
Every ai.decide must define what happens when confidence falls below threshold.
Add a "not confident" block inside this ai.decide before building.
```

**The audit trail is not optional.** `ai.audit to fraud_audit_log` writes an immutable record of every decision — inputs, result, confidence score, model used, timestamp. HIPAA, SOC2, and financial regulations require this when AI decisions affect real outcomes. Mohio writes it automatically.

**Confidence is a first-class value.** `check confidence above 0.85` is enforced by the runtime. The model doesn't decide what happens when it's uncertain — the developer does, in the language, where it can be reviewed and audited.

### Compliance is a declaration

```mohio
sector: financial    // activates PCI_DSS, SOC2, CTR thresholds, AML rules
sector: healthcare   // activates HIPAA, HITECH, PHI field types, 6-year retention
```

One word. The sector profile activates field type awareness, encryption rules, retention policies, audit requirements, breach notification defaults, and AI decision constraints. No library installation. No configuration files. No manual wiring.

`sector: financial` already knows that `card_cvv` can never be stored. The compiler enforces it. It knows that transactions over $10,000 in cash trigger CTR requirements. It knows that AI credit decisions require ECOA-compliant adverse action explanations.

`sector: healthcare` already knows what `mrn`, `npi`, `diagnosis`, and `prescription` are. It knows PHI must be retained for 6 years. It knows that clinical AI decisions require 0.95 minimum confidence and human review before action.

### Named closers — precision-machined blocks

Every block in Mohio opens with a verb and closes with its name:

```mohio
retrieve member from db.members
    match id is transaction.member_id
retrieve: done

ai.decide isFraudulent(transaction) returns boolean
    ...
ai.decide: done
```

A closer mismatch is a compile error — not a warning, not a runtime surprise:

```
Line 24 — closer mismatch.
Expected: ai.decide: done
Found:     retrieve: done

The ai.decide block opened on line 18 is not closed.
Add 'ai.decide: done' before 'retrieve: done'.
```

You never lose your place. The structure is self-documenting.

---

## Current state — Phase 1 complete

Mohio is in active development. The compiler is real. The demo runs. Here is the honest state of the project.

**Working today:**

| Component | Status |
|-----------|--------|
| Lark grammar | ✅ 70/70 tests passing |
| AST (60+ node types) | ✅ Complete |
| Transformer — strict closer validation | ✅ Complete |
| Tree-walking interpreter | ✅ Complete |
| SQLite data layer | ✅ Working |
| Audit log | ✅ Written on every ai.decide |
| Anthropic API wiring for ai.decide | ✅ Real Claude reasoning |
| `mio run` | ✅ Complete |
| `mio check` | ✅ Complete |
| Fraud detection demo | ✅ Running end to end |
| Sector profiles — financial, healthcare | ✅ Designed and documented |

**Coming next:**

| Component | Status |
|-----------|--------|
| Indentation preprocessor (Phase 1.5) | 🔧 In progress — lifts explicit closer requirement from flow control |
| `mio fmt` | 📋 Planned |
| `mio serve` — HTTP server layer | 📋 Planned |
| Real database connections (PostgreSQL, MySQL) | 📋 Planned |
| `mio test` | 📋 Planned |
| Healthcare demo | 📋 Next demo target |
| Fine-tuned Mohio model | 🔭 On the horizon |
| Visual platform | 🔭 On the horizon |

---

## Run it yourself

**Requirements:** Python 3.10+, pip

```bash
git clone https://github.com/countart/mohio
cd mohio/compiler

pip install lark anthropic

# Check the fraud demo parses clean
python mio.py check tests/fraud_demo.mho

# Run with mock AI (no API key needed)
python mio.py run tests/fraud_demo.mho --request-file tests/request.json --seed tests/seed.json --verbose

# Run with real Claude reasoning (requires ANTHROPIC_API_KEY)
python mio.py run tests/fraud_demo.mho --request-file tests/request_fraud.json --seed tests/seed.json --ai --verbose
```

**Windows CMD note:** Use `--request-file` or `--param` instead of `--request` for JSON input:

```cmd
python mio.py run tests\fraud_demo.mho --param _shape=Transaction --param amount=74500 --param _roles=screener --seed tests\seed.json --ai
```

---

## The Walk-By Test

Every line of Mohio must pass this: a non-technical manager should glance at a screen for three seconds and understand the business intent — without a bracket, semicolon, or variable name to decode.

This is not a style preference. It is a design constraint that every keyword, syntax decision, and error message is evaluated against. If it doesn't read like the intent, it doesn't ship.

---

## Sector Pioneer Program

We are inviting a small group of financial and healthcare developers to early access before public launch.

**What pioneers get:**
- Early access to the runtime before public launch
- Free access to the certified sector profile for your industry — maintained, legally reviewed, updated as regulations change
- Direct line to the Mohio team — your feedback shapes the language
- Recognition as a Mohio Sector Pioneer in the project credits

**What we ask:**
- Use it. Build something real with it, even small.
- Tell us what's wrong — bugs, missing rules, awkward syntax, compliance gaps.

No contract. No commitment. No cost.

Email **hello@mohio.io** with your name, your sector, and one sentence on the compliance problem that costs you the most time.

---

## Business model

Mohio is open core. The language and base runtime are free and open source under Apache 2.0.

| Tier | What's included | Cost |
|------|----------------|------|
| Language + Runtime | Full language, base interpreter, standard built-ins | Free / Apache 2.0 |
| Certified Sector Profiles | Professionally maintained, legally reviewed, regulatory-updated | Commercial |
| Visual Platform | Builder generating Mohio code | Commercial |
| Hosted Runtime | Managed deployment, compliance dashboard | Commercial |
| Fine-Tuned Mohio Model | Trained on your codebase and domain | Enterprise |

The language will never be closed. The sophistication is in the runtime. The simplicity is in the code. The business is in the tiers.

---

## Join the community

- 🌐 [mohio.io](https://mohio.io) — Homepage and waitlist
- 💬 [Discord](https://discord.gg/MF95H3wQdm) — Community
- 🐙 [GitHub](https://github.com/countart/mohio) — Source
- ☕ [Buy Me a Coffee](https://buymeacoffee.com/mohiolang) — Support the project
- 📧 [hello@mohio.io](mailto:hello@mohio.io) — Sector Pioneer Program

---

<div align="center">

**Mohio Language Project · Particular LLC · Apache 2.0**

*"Write intent. Execute reason. See everything."*

</div>
