<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Part I -- First Programs

## Chapter 4: An App That Reasons

*Rung 3, built on chapter 3's setup: a real, served app where `ai.decide` makes the call, and
the audit trail records it -- the differentiator gets its full chapter later, this is "build
one small thing" with it. Every claim below is either verified working, verified failing, or
named as not yet checked -- none of the three is guessed.*

---

### The app: screening a transaction

```mohio
connect db as sqlite from env.DATABASE_URL

shape Transaction
    amount as decimal required
    device_id as text required
shape: done

listen for
    new sh.Transaction at /screen
        ai.decide isFraudulent returns boolean
            confidence above 0.85
            weigh transaction.amount, transaction.device_id
            ai.audit to fraud_audit_log
            not confident
                give back [202] "Flagged for manual review"
            on.failure
                give back [503] "AI service unavailable"
        ai.decide: done

        ai.decide isFraudulent

        check isFraudulent
            when true
                give back [422] "Transaction blocked"
            otherwise
                give back [200] "Approved"
        check: done
    new: done
listen: done
```

Run it:

```bash
mio serve screen.mho
```

`ai.decide isFraudulent returns boolean` declares the decision; `weigh` names what it
considers. `confidence above 0.85` is the floor -- below it, `not confident` fires instead of
a real answer. `ai.audit to fraud_audit_log` is the one line that makes every decision durably
recorded; nothing else in this chapter writes to that table.

### Verified working: a confident decision, and its audit trail

```bash
curl -X POST http://localhost:8080/screen \
  -d '{"amount":4200,"device_id":"device-9981"}'
# 200 {"message": "Approved"}
```

Reading `fraud_audit_log` back afterward, in a fresh connection, not trusting the response:

```json
{
  "event": "ai.decide",
  "decision_name": "isFraudulent",
  "result": "False",
  "confidence": "0.88",
  "model": "mock-v1",
  "fell_back": "False",
  "request_id": "d1976845d9e8"
}
```

Field NAMES are recorded for what the decision weighed (`transaction.amount`,
`transaction.device_id`), never the values themselves -- the same value-never-logged discipline
the rest of the audit trail keeps. This record is what makes an AI decision something you can
show a regulator afterward: which decision, what it returned, how confident, which model, and
a request id tying it back to the exact call.

### Verified working: no provider available

Without a real key (`mio serve screen.mho --ai`, no `ANTHROPIC_API_KEY` set):

```bash
curl -X POST http://localhost:8080/screen -d '{"amount":4200,"device_id":"device-9981"}'
```
Fails at startup with the same message chapter 3 showed:
```
Error  No AI provider key found. Set ANTHROPIC_API_KEY, OPENAI_API_KEY, or GEMINI_API_KEY...
```

With a key set but the call itself failing (chapter 3's SDK-version gap, or any genuine
provider-side failure), `on.failure` catches it correctly -- verified with a real served
request:

```bash
curl -X POST http://localhost:8080/screen -d '{"amount":4200,"device_id":"device-9981"}'
# 503 {"message": "AI service unavailable"}
```

### NOT working, verified and named directly: `not confident` inside a served route

`not confident` is meant to fire when the decision cannot clear its own confidence floor, and
it DOES fire -- confirmed directly, forcing the mock provider's fixed 0.88 below a 0.95 floor
and watching a marker inside the block print. What does NOT work: `give back [202]` inside that
block does not end the request the way the identical line inside `on.failure` does two lines
below it. Watched over a real served request, same app, threshold raised to `0.95`:

```bash
curl -X POST http://localhost:8080/screen -d '{"amount":4200,"device_id":"device-9981"}'
# 200 {"message": "Approved"}
```

**Not 202. Approved.** The `not confident` handler ran, its `give back` bound a text value to
`isFraudulent` instead of ending the request, and the `check isFraudulent / when true /
otherwise` below it fell through to the same response a clean approval gives. The audit record
for that exact call also shows `"fell_back": "False"` -- the trail does not know this was a
fallback either. A transaction the AI was genuinely unsure about is indistinguishable, in both
the response and the compliance record, from one it approved outright.

**Do not build against this today.** If your confidence floor can realistically not be met,
`not confident`'s `give back` is not yet the way to answer the caller inside a served route --
`check <name>.confidence` after the decision, read directly, is the workaround until this is
fixed, and that route has not itself been verified end to end. What is described above is a
silent divergence between what happened and what both the caller and the audit trail are told
happened, which is the class of bug Mohio treats as its highest severity.

---

**Next:** Chapter 5, Mohio as Your Backend -- `framework: api`, endpoint calls only, no page
routing.
