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

The `202` is the language doing its job: the AI fell below the confidence floor on a regulated decision and handed off to a person, because the code required it. Nothing was bolted on. The reasoning, the audit, and the fallback are part of the program.

---

## Where to go next

- **[mohio.io](https://mohio.io)** — the home for docs, guides, and access.
- **[Discord](https://discord.gg/9tq7tGSNYE)** — the community, and where early access is handed out.
- **[zork.mohio.io](https://zork.mohio.io)** — a live demo you can play right now.

---

<div align="center">

**MOHIO™** is a trademark of Particular LLC, Mooresville, NC. Patent pending.

Questions or early-access requests: **rsmith@getmohio.com**

</div>
