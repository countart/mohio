# Mohio Design Decisions

This document records the locked design decisions that define Mohio as a language. Every decision here has been through the Walk-By Test, tested against real syntax, and locked. These are not proposals — they are canon.

---

## The Walk-By Test

**The governing design principle for every syntax decision in Mohio.**

A non-technical manager walking by a developer's screen should understand the business intent in three seconds — without knowing Mohio, without brackets, without decoding variable names.

Every keyword, every syntax pattern, every error message is evaluated against this test before it ships. If a construct fails the Walk-By Test, it doesn't make it into the language regardless of how technically elegant it is.

---

## One word, one job

No keyword in Mohio does double duty based on context. A word means exactly one thing everywhere it appears.

Examples of this principle in action:
- `retrieve` — bring back one specific record by match. Never used for searching.
- `find` — search across multiple records. Never used for single lookups.
- `make` — assembly from known shapes or ingredients.
- `create` — builds something new from scratch.
- `retrieve` and `find` are never aliases. They are permanently distinct.

---

## Closers — named, required, strict

Every named block opens with a verb and closes with its name:

```mohio
retrieve member from db.members
    match id is user.id
retrieve: done

ai.decide isFraudulent(transaction) returns boolean
    ...
ai.decide: done
```

**Why named closers:** A bare `done` at the end of a deeply nested block tells you nothing. `ai.decide: done` tells you exactly what just closed — the name is the documentation. When something breaks, the closer is the first clue.

**Option A — strict validation:** A named closer must match the block it closes. Mismatch = compile error, not warning. Line number, expected closer, found closer, suggested fix — all in the message.

**Bare `done` is accepted** on any block. It is forgiving, valid, and executable. It is the div soup of Mohio — you can write it, it works, but you lose the mismatch detection. `mio fmt` (Phase 2) normalizes all bare closers to named closers.

**Phase 1 note:** All flow control blocks (`if`, `check`, `each`, `repeat`, `while`) require explicit closers in Phase 1 due to the whitespace-agnostic Earley parser. Phase 1.5 (indentation preprocessor) lifts this requirement from flow control blocks. Data operation blocks and AI blocks always require named closers.

---

## `ai.audit` before `not confident`

Inside an `ai.decide` block, `ai.audit` must appear before `not confident`.

```mohio
ai.decide isFraudulent(transaction) returns boolean
    check confidence above 0.85
    weigh transaction.amount, member.history
    ai.audit to fraud_audit_log       ← must come first
    not confident
        give back 202 "Referred to manual review"
ai.decide: done
```

**Why:** The `not_confident` block uses `statement*` which is greedy — if `ai.audit` appears after it, the parser swallows the audit line into the fallback body and it never fires. The audit must be declared before the fallback. This is also the right semantic order: declare what you're auditing before you define what happens if you can't decide.

---

## `not confident` is mandatory — compile error without it

Every `ai.decide` block must have a `not confident` block. The compiler refuses to build without one. This is not a warning.

**Why:** An `ai.decide` with no fallback path is undefined behavior. When confidence falls below threshold, there must be a defined action — not silence, not a crash, not a guess. The developer must decide what the program does when the model is uncertain. That decision belongs in the language, not handled at runtime.

```
Compile error — ai.decide "isFraudulent" is missing a "not confident" block.
Every ai.decide must define what happens when confidence falls below threshold.
Add a "not confident" block inside this ai.decide before building.
```

---

## Confidence is a first-class language value

`check confidence above 0.85` is enforced by the runtime — not by application logic, not by the developer checking a return value. The model does not decide what happens when it is uncertain. The developer does, in the language, where it can be reviewed, audited, and changed without touching the AI call.

This is one of the three things that make `ai.decide` a language construct rather than a function call.

---

## The three things that make `ai.decide` a language construct

1. **The compiler refuses to build without a fallback.** No other language enforces this.
2. **The audit trail is not optional.** `ai.audit` writes an immutable record on every execution. HIPAA, SOC2, and financial regulations require this. Mohio writes it automatically.
3. **Confidence is enforced by the runtime.** The threshold is declared in the language, not handled in application code.

---

## ai.chain — Provider Failover and the Loop Problem

**ai.chain** has two distinct failure modes. Both must be understood before using it in any batch or loop operation. Getting this wrong is a billing event at scale, not a performance inconvenience.

**The loop problem.** An unresolved chain inside an each block re-evaluates provider availability on every iteration. 100 records = 100 provider checks. 10,000 records = 10,000 checks. Resolve before the loop. Pay once.

**The mid-loop switch problem.** If a provider fails mid-loop — credits exhausted, rate limit, timeout — a naive implementation switches for that record and then re-checks from scratch on the next one. Record 47 gets GPT-4o. Record 48 checks Claude, fails, switches to GPT-4o again. Every remaining record pays the check-fail-switch cost.

**The correct behavior.** When fallback() fires mid-loop, it updates active_provider in place on the shared chain object. Record 48 reads the already-updated provider. So do 49 through 100. One switch. Zero re-evaluation. The chain never goes backwards.

**on.resolve** is the lock. Declare the chain, call on.resolve before the loop, then process records. Resolution cost: one provider ping. Cost per record: zero.
mio check warns when an ai.chain appears inside an each block without prior on.resolve. It is a warning, not a compile error. The developer can override it. But it is on record.

```mohio
ai.chain fraud_checker
    try "claude-sonnet-4-20250514"
        quality above 0.75
    then "gpt-4o"
        quality above 0.70
    then "claude-haiku-4-5-20251001"
    on.resolve
        miolog.info fraud_checker.active_provider
ai.chain: done
```
---

## Sector profiles — institutional knowledge as a language feature

`sector: financial` and `sector: healthcare` are the first instance of institutional knowledge built directly into a programming language.

The profile knows what field names carry which classifications. It knows the regulatory thresholds. It knows which AI decision types require human review. It activates all of this from a single declaration.

**Transparency about what profiles don't cover is a trust feature, not a weakness.** Every sector profile includes an explicit `profile notes` section listing what it does not handle and what requires legal counsel. A developer should never assume a sector profile replaces qualified legal advice.

---

## Compliance framing — technical enforcement controls

Mohio activates **technical enforcement controls** — it does not guarantee compliance. The distinction matters legally and is stated explicitly in all documentation.

The phrase "a Mohio program that declares HIPAA compliance is HIPAA-compliant" is retired. It is legally inaccurate. Mohio enforces what can be enforced in code. The obligations that live outside code — BAAs, independent audits, staff training, legal counsel — remain the organization's responsibility.

---

## Annotation-Driven Development (ADD)

The formal methodology for writing Mohio:

1. Write standard code first
2. Annotate each line in plain conversational English
3. Rewrite in Mohio
4. Read aloud
5. Apply Walk-By Test
6. Rewrite if anything fails
7. Lock when it passes

This is not a style preference — it is the process that ensures Mohio code remains readable after the original developer leaves the project.

---

## The nine-prefix modifier system

Nine prefixes cover all modifier types in the language. Learn them once, read any Mohio code.

| Prefix | Meaning |
|--------|---------|
| `by.` | the how |
| `do.` | the rule / constraint |
| `on.` | the reaction |
| `as.` | the form |
| `to.` | the destination |
| `in.` | the where or unit |
| `is.` | the state test |
| `not.` | the opposite |
| `if.` | the when |

Plus connectors: `with.`, `from.`, `and.`

The dot separates category from word. Once you know `on.` means reaction, you know `on.failure`, `on.success`, `on.open`, `on.close`, and `on.change` without being told.

---

## Retired keywords

These keywords appeared in early design and are explicitly retired:

| Keyword | Status | Replaced by |
|---------|--------|------------|
| `route` | Retired | `listen for` |
| `set` | Retired (parser accepts as noise) | Assignment without keyword |
| `emit` | Retired, reserved | — |
| `process` | Retired | — |
| `receive body as` | Retired | `sending` blocks |
| `body {}` | Retired | `sending` blocks |
| `as [Shape]` | Retired | `expect sh.[Shape] as [name]` |
| `timeout N seconds` | Retired | `wait up to N seconds` |
| `tenant mode: multi` | Retired | `serves: multiple tenants` |
| `miocron` | Retired | `mioschedule` |
| `miosignal` | Not needed | — |

---

## Three developer profiles

Mohio meets developers where they are.

**Craftsman** — Precise, intentional, explicit. Uses every language feature deliberately. Gets full control with zero boilerplate.

**Collaborator** — Works alongside domain experts: compliance officers, legal teams, clinical staff. Produces code that non-developers can review, verify, and sign off on.

**Natural Language Developer** — Thinks in business intent. Mohio accepts near-natural phrasing and canonicalizes it. The Walk-By Test guarantees the code matches the intent.

---

## Founding story

The core syntax was designed on a phone during a roadside breakdown on I-77. The sector profiles were designed in a Dunkin' Donuts drive-thru. The founding infrastructure was built in three hours.

The name comes from te reo Māori — *mohio* means to understand. That is the only thing the language is trying to do.

---

*Mohio Design Decisions — Particular LLC — Apache 2.0*  
*This document is the public record of locked language decisions. It is derived from the Language Design Document (LDD), which is the internal authoritative specification.*
