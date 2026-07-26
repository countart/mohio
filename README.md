<div align="center">

<img src="https://mohio.io/img/logo-spiral.png" alt="Mohio" width="120" />

# Mohio™

### Write intent. Execute reason. See everything.

**The first AI-native programming language, where AI reasoning is a compiler-enforced primitive, not a library, not an API, not an afterthought.**

[![Discord](https://img.shields.io/badge/Discord-Join%20us-5865F2.svg)](https://discord.gg/9tq7tGSNYE)
[![X](https://img.shields.io/badge/X-@mohiolang-000000.svg)](https://x.com/mohiolang)
[![LinkedIn](https://img.shields.io/badge/LinkedIn-Mohio-0077B5.svg)](https://linkedin.com/company/mohiolang)
[![Hugging Face](https://img.shields.io/badge/%F0%9F%A4%97-mohiolang-FFD700.svg)](https://huggingface.co/mohiolang)
[![Buy Me a Coffee](https://img.shields.io/badge/Support-Buy%20Me%20a%20Coffee-FFDD00.svg)](https://buymeacoffee.com/mohiolang)
[![Live Demo](https://img.shields.io/badge/Live%20Demo-zork.mohio.io-0D7377.svg)](https://zork.mohio.io)

*Mohio (moh-hee-oh), from te reo Māori: to understand.*

*Conceived March 23, 2026. Dunkin drive-thru, Mooresville, NC. Born later that day, side of I-77, Charlotte, NC.*

</div>

---

> ### The compiler and the full guides are being prepared for release under a new license.
> Access is rolling out to early testers now. To get in, join the **[Discord](https://discord.gg/9tq7tGSNYE)** or visit **[mohio.io](https://mohio.io)**. The public release lands here soon.

---

## AI-native does not mean AI writes your app

Mohio is a real programming language. You write it. It has rules, a compiler, and a grammar.

AI-native means one thing: `ai.decide`, `ai.audit`, `ai.agent`, and `ai.explain` are built into the language itself, enforced by the compiler, not bolted on as a library, not called through an API. When your Mohio program makes an AI decision, it is governed, auditable, and typed. That is what AI-native means.

If you want a tool that generates an app from a description, other products do that well. Mohio is what you reach for when you need code you can read, own, modify, and trust, with AI reasoning that is part of the program, not a black box beside it.

> **In one line:** Mohio is a programming language. You write it. AI reasons inside it.

---

## See it running

**[zork.mohio.io](https://zork.mohio.io)** is a live AI text adventure built in Mohio, with real session state and real reasoning. Go play it. That whole thing is a Mohio program.

<!-- PLAYGROUND: paste your playground URL here when ready, e.g. https://playground.mohio.io -->

---

## What Mohio looks like

A fraud check. Declare the sector, gather the data, let the AI decide with a confidence floor and an audit trail, and route to a human automatically when the model is not sure enough.

```mohio
sector: financial

connect db as postgres from env.DATABASE_URL

shape Transaction
    id          as text
    amount      as decimal
    member_id   as text
    device_id   as text
    timestamp   as datetime
shape: done

listen for
    new sh.Transaction
        require role "screener" or "system"

        find member in db.members
            where id is transaction.member_id
        find: done

        ai.decide isFraudulent returns boolean
            confidence above 0.85
            weigh transaction.amount, transaction.device_id, member.history
            ai.audit to fraud_audit_log
            not confident
                give back 202 "Referred to manual review"
            on.failure
                give back 503 "Fraud check unavailable"
        ai.decide: done

        check isFraudulent
            when true
                update db.transactions
                    status blocked
                    match id to transaction.id
                update: done
                give back 422 "Transaction blocked pending review"
            otherwise
                give back 200 "Transaction approved"
        check: done

    new: done
listen: done
```

**What the runtime produced** with a $74,500 transaction from an unrecognized device:

```
[find] member -> Alice Chen, 3 years clean history, avg $200/month
[find] recent transactions -> 3 rows

[ai.decide] isFraudulent
  Model: claude-sonnet-4-6
  Response: {"result": true, "confidence": 0.95,
    "explanation": "Amount $74,500 is 300x average monthly spend
    from an unrecognized device with no recent history."}
  Result: True  Confidence: 0.95  Threshold: 0.85  Fell back: False

[ai.audit] -> fraud_audit_log
  decision: isFraudulent  result: True  confidence: 0.95
  model: claude-sonnet-4-6  ts: 2026-04-22T16:34:49

Response  422  Transaction blocked pending review
```

Zero API wiring in user code. The developer wrote intent. The runtime handled the rest.

---

## What makes Mohio different

### 1. AI reasoning is a language construct, not a function call

`ai.decide` is understood by the compiler, enforced by the runtime, automatically audited.

The compiler refuses to build without a fallback:

```
CompileError E002. ai.decide "isFraudulent" is missing a "not confident" block.
Every ai.decide must define what happens when confidence falls below threshold.
Hint: Add a "not confident" block inside this ai.decide before building.
```

The audit trail is not optional. `ai.audit to fraud_audit_log` writes a record of every decision: inputs, result, confidence, model, timestamp. Confidence is a first-class value. `confidence above 0.85` is enforced by the runtime, not the model.

### 2. Compliance is a declaration

```mohio
sector: financial    // PCI-DSS v4.0.1, SOC2, BSA/AML thresholds, OFAC, AML rules
sector: healthcare   // HIPAA, HITECH, PHI fields, 6-year retention, 0.95 AI floor
```

One word. No library installation. No configuration files. No manual wiring.

`sector: financial` knows `card_cvv` can never be stored, and the compiler enforces it. It knows cash over $10,000 triggers CTR. It knows SAR decisions require 0.95 confidence and human review. `sector: healthcare` knows what `mrn`, `npi`, `diagnosis`, and `prescription` are, that clinical AI requires 0.95 confidence and human review, that PHI is retained 6 years, and that MFA is mandatory under the January 2025 HIPAA Security Rule update.

*Mohio activates technical enforcement controls. It is not a guarantee of legal compliance; qualified legal counsel does that.* The enforcement layer is wired today. Certified sector profiles, legally reviewed and maintained, arrive after formal compliance review.

### 3. The code reads like you wrote it in English

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

The Walk-By Test: a non-developer reads that in three seconds and understands the business intent. It is a design constraint, not a style preference. Every keyword and syntax decision is evaluated against it.

### 4. Named closers, precision-machined blocks

Every block opens with a verb and closes with its name. Closer mismatches are compile errors:

```
Line 24. Closer mismatch. Expected: ai.decide: done. Found: find: done.
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

### 5. AI provider failover with `ai.connect`

Named provider groups with ordered fallback. Declare once, reference anywhere:

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

### 6. Agent safety, the Deterministic Runtime Boundary Gate

```mohio
ai.agent researchAssistant
    goal "Research this topic and summarize findings"
    limits
        max steps    10
        cost ceiling 1.00
    limits: done
    not confident
        give back "Research incomplete, limits reached"
ai.agent: done
```

When `max steps` is reached, a hard stop fires, enforced by the interpreter step counter, not a guideline. The agent cannot continue regardless of the model's output. The cost ceiling works the same way. Both are deterministic.

### 7. mio check, structured compile-time validation

```bash
mio check myapp.mho                 # structural check
mio check myapp.mho --security      # security checks
mio check myapp.mho --json          # machine-readable for agents and CI
```

Every error includes a line number and an actionable hint. `--json` outputs pure JSON to stdout:

```json
{
  "errors": [
    {
      "code": "E002",
      "message": "ai.decide 'isFraudulent' is missing a 'not confident' block.",
      "line": 18,
      "hint": "Add a 'not confident' block inside this ai.decide before building."
    }
  ]
}
```

### 8. Atomic upsert

```mohio
upsert db.game_state
    match session_id to session_id
    current_room current_room
    score        score
upsert: done
```

INSERT or UPDATE atomically. Native on SQLite (INSERT OR REPLACE), Postgres (ON CONFLICT), MySQL, and MongoDB.

### 9. Write Mohio in your language

The same program, authored in your own language. Structural connectors (`on.`, `as.`, `ai.`) and the `mio*` service names never translate, only the readable keywords do, so a program stays portable and the structure stays universal.

Language packs are in progress for Spanish, Portuguese, and Hindi, with the mechanism designed to extend to any language. (Examples of translated source are held back pending a provisional patent filing.)

---

## Firsts (as far as we know)

- First language where `ai.decide` is a compiler-enforced primitive
- First language where a missing AI fallback is a build error
- First language with automatic AI audit trails as a language construct
- First language where compliance is a single declaration
- First language where sector profiles enforce AI confidence floors
- First language where a shape declaration serves DB, API, validation, compliance, and AI at once
- First language with the Walk-By Test as a formal design constraint
- First language with named block closers as a compile-time safety mechanism
- First language with deterministic runtime boundary gates on AI agents
- A native Right to Be Forgotten verb (`cm.purge`): syntax, mandatory `reason` audit modifier, and compile-time enforcement ship today; cascade-delete execution is in active development and fails safe
- Zero-drift multilingual syntax: write Mohio in your language, and the structural connectors never translate

---

## Sector Pioneer Program

Early access for financial and healthcare developers before public launch.

**What pioneers get:** early runtime access, a reviewed sector profile, a direct line to the team, and pioneer credits.

**What we ask:** build something real, tell us what is wrong, and let us mention you if it works.

No contract, no commitment, no cost. Email **hello@mohio.io** with your sector and one sentence on the compliance problem that costs you the most time.

---

## Intellectual property

Compiler-enforced AI safety primitives, sector-activated regulatory compliance enforcement, deterministic runtime boundary gates on AI agents, and agent-native structured compiler output are the subject of provisional patent applications filed with the USPTO. Patent pending.

For licensing inquiries: **hello@mohio.io**

---

## Join the community

| | |
|---|---|
| Website | [mohio.io](https://mohio.io) |
| Live Demo | [zork.mohio.io](https://zork.mohio.io) |
| Discord | [discord.gg/9tq7tGSNYE](https://discord.gg/9tq7tGSNYE) |
| X / Twitter | [@mohiolang](https://x.com/mohiolang) |
| LinkedIn | [Mohio](https://linkedin.com/company/mohiolang) |
| Hugging Face | [mohiolang](https://huggingface.co/mohiolang) |
| Support | [buymeacoffee.com/mohiolang](https://buymeacoffee.com/mohiolang) |
| Email | [hello@mohio.io](mailto:hello@mohio.io) |

---

<div align="center">

**MOHIO™** is a trademark of Particular LLC, Mooresville, NC. Patent pending.

*Write intent. Execute reason. See everything.*

</div>
