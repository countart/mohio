<div align="center">

<img src="https://mohio.io/img/mohio-6-mark.png" alt="Mohio" width="120" />

# Mohio

### Write intent. Execute reason. See everything.

**An AI-native programming language where AI reasoning is a compiler-enforced primitive - not a library, not an API, not an afterthought.**

[Live demo](https://zork.mohio.io) · [Language Reference](https://mohiou.com/reference/) · [mohio.io](https://mohio.io)

</div>

---

## What Mohio is

Mohio is a real programming language - a grammar, a compiler, and rules you write against. What makes it AI-native is that `ai.decide`, `ai.audit`, `ai.agent`, and `ai.explain` are built into the language itself. When a Mohio program makes an AI decision, that decision is typed, governed, and recorded automatically. You write the intent; the runtime handles the reasoning, the fallback, and the record.

It is also built to be read. Every keyword is measured against the Walk-By Test: a non-developer should read the business intent in about three seconds.

---

## What you can build with it

**AI decisions that can't ship ungoverned.**
`ai.decide` is understood by the compiler. A missing confidence fallback is a *build error*, not a 2 a.m. surprise. Confidence floors are enforced by the runtime, not left to the model.

```mohio
connect db as sqlite from env.DATABASE_URL default "fraud.db"

shape Txn
    amount as decimal
    device_id as text
shape: done

create txn as sh.Txn
    amount 4200
    device_id "device-9981"
create: done

ai.decide isFraudulent returns boolean
    confidence above 0.85
    weigh txn.amount, txn.device_id
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
```

Leave out `not confident` and `mio check` refuses the file:

```
  11 | ai.decide isFraudulent returns boolean
  x ai.decide 'isFraudulent' is missing a 'not confident' block.
    Every ai.decide must define what happens when confidence falls below threshold.
```

**An audit trail you don't wire.**
The one line `ai.audit to fraud_audit_log` above writes a hash-chained record of every decision - the inputs it weighed (by name, never their values), the result, confidence, model, and timestamp - with no plumbing. `ai.explain` turns a decision into a sentence a person can read; add these two lines right after `ai.decide isFraudulent`:

```mohio
ai.explain isFraudulent as reason
show reason
```

**Audit anchors you can hand to someone else.**
`mio audit publish` signs the head of each audit log with your own Ed25519 key, and `mio audit verify-anchors --verify-sig` checks the trail against those signed anchors. A tampered row, a truncated log, a swapped database, and an anchor changed after signing are each refused by name.

```bash
mio audit publish fraud.db --out anchors.json
mio audit verify-anchors fraud.db --anchors anchors.json --verify-sig anchor.pub
```

**Compliance as a declaration.**
Tag a field `[pii]`, `[phi]`, or `[pci]` and its protection travels with the value: through a copy, a join, a task, a page, or a JSON response. `sector: financial` declares the rules a program runs under, with no config files and no manual wiring.

```mohio
sector: financial

shape Account
    holder as text [pii]
    card as text [pci]
shape: done
```

With no sector profile installed, built-in defaults apply under a declared sector: an over-precise money amount is refused rather than rounded, every data write is recorded in the audit trail, and a default database on a temporary disk refuses at connect. A sector profile adds field classifications, confidence floors, and never-store fields; you can write your own community profile today.
*Mohio activates technical enforcement controls; it is not, and does not claim to be, a guarantee of legal compliance. Certified, legally-reviewed sector profiles are a separate commercial tier.*

**Code that reads like English.**
Named block closers (`ai.decide: done`) make a mismatch a compile error, so the structure can't drift from the intent.

```mohio
score 92
check score
    when score is more than 89
        show "A"
    otherwise
        show "keep going"
repeat: done        // WRONG on purpose: a check closes with check: done
```

```
  7 | repeat: done        // WRONG on purpose: a check closes with check: done
  x Closer mismatch -- 'repeat: done' closes the wrong block.
    The 'check' block (line 2) requires 'check: done'. Found 'repeat: done'.
```

**Provider failover, bounded agents, and CI-ready tooling.**
`ai.connect` gives ordered provider fallback. `ai.agent` runs under deterministic step and cost limits enforced by the interpreter, and `mio check` refuses an agent with no limits. `mio check --json` emits machine-readable validation for CI and agents.

```mohio
ai.connect answer
    order
        anthropic model "claude-haiku-4-5-20251001"
        openai    model "gpt-4o"
    order: done
ai.connect: done

ai.agent helper
    goal "summarize the open tickets"
    limits
        max steps 5
        cost ceiling 0.50
    limits: done
    not confident
        give back none
ai.agent: done
```

---

## A Mohio program

A complete, deployable contact form, from [`examples/contact.mho`](examples/contact.mho):

```mohio
connect db as postgres from env.DATABASE_URL

shape Contact
    name    as text required label "Your name"
    email   as text required format "email"
    message as text required error "Tell us what you need"
shape: done

request for sh.Contact at /contact
    render
        <h1>Get in touch</h1>
        <p>We read every message.</p>
        {{ form sh.Contact }}
    render: done
request: done

listen for sh.Contact at /contact
    save to db.messages
        name    Contact.name
        email   Contact.email
        message Contact.message
    save: done
    render
        <h1>Thanks, {{ Contact.name }}</h1>
        <p>Your message is in. We'll be in touch at {{ Contact.email }}.</p>
    render: done
listen: done
```

`GET /contact` shows a form built from the `Contact` shape; `POST /contact` checks the submission against that same shape, saves it, and thanks the sender by name.

---

## Get it

```bash
git clone https://github.com/countart/mohio
cd mohio
pip install -e .        # installs dependencies and the `mio` command

mio warmup
mio run cookbook/letter-grade.mho

# Save the fraud example above as fraud.mho, then:
mio check fraud.mho
mio run   fraud.mho --verbose

# Real AI reasoning
export ANTHROPIC_API_KEY=your_key
mio run fraud.mho --ai --verbose
```

With no key set, AI runs in mock mode, so everything above works offline. On Windows, set `PYTHONUTF8=1` in your terminal first.

Start with [`start-here/`](https://github.com/countart/mohio/tree/main/start-here) for install, your first program, and links to every verified guide. The [`cookbook/`](cookbook/) holds nine short recipes you can run as they are.

---

## License

Open core under the **Mohio Business Source License 1.1 (BSL)** - see `LICENSE` and `LICENSE-SCOPE.md`. The full language, compiler, standard built-ins, and community sector profiles are free to use. In process or planned for the commercial tier: `miochain`, `miovault`, managed `mioknow`, and certified sector profiles. The language will never be closed.

Compiler-enforced AI-safety primitives, sector-activated compliance enforcement, deterministic runtime boundary gates on AI agents, and agent-native structured compiler output are the subject of provisional patent applications filed with the USPTO.

For trademark and brand usage, see [TRADEMARKS.md](TRADEMARKS.md). For reserved commercial offerings, see [RESERVED-COMMERCIAL-OFFERINGS.md](RESERVED-COMMERCIAL-OFFERINGS.md).

---

## Find us

- **Live demo:** [zork.mohio.io](https://zork.mohio.io) - an AI text adventure with real session state and real reasoning
- **Language Reference:** [mohiou.com/reference](https://mohiou.com/reference/) - generated from the grammar
- **Website:** [mohio.io](https://mohio.io)
- **Community:** [Discord](https://discord.gg/9tq7tGSNYE), [@mohiolang](https://x.com/mohiolang), [LinkedIn](https://linkedin.com/company/mohiolang)
- **Email:** [hello@mohio.io](mailto:hello@mohio.io)

---

*Mohio (moh-hee-oh) - from te reo Māori: to understand.*
*MOHIO™ 6.0.3 · Mohio Language Project · Particular LLC · BSL 1.1*
