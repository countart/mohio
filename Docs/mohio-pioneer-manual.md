<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Mohio Pioneer Program — Developer Manual
**For Pioneers only · Pre-launch · mohio.io**

---

## Welcome

You are one of the first people to write Mohio in the real world. That matters. This manual covers everything you need to go from quick start to production-ready programs. If something is missing, email hello@mohio.io — your feedback builds this document.

The quick start is at **mohio.io** (if you haven't run your first program yet, start there).

---

## The language in one page

Mohio programs are `.mho` files. The CLI is `mio`. Every block has a named closer. Every AI decision requires a fallback. Everything reads like what it does.

```mohio
// This is a comment. Only // — never ##.

connect db as postgres from env.DATABASE_URL   // one connect at the top

shape Screening                                // declare the shape you handle
    member_id as int
shape: done

listen for
    new sh.Screening at /screen
        require role "screener"                    // role values always quoted

        retrieve member from db.members
            match id to request.member_id
            on.failure
                give back 404 "Member not found."
        retrieve: done

        ai.decide is_fraudulent returns boolean
            confidence above 0.85
            weigh
                transaction.amount,
                member.history,
                device.fingerprint
            ai.audit to fraud_audit_log   // ai.audit goes BEFORE not confident
            not confident
                give back 202 "Flagged for review."
            on.failure
                give back 503 "AI service unavailable."
        ai.decide: done

        check is_fraudulent
            when true
                give back 200 "Blocked." as json
            otherwise
                give back 200 "Approved." as json
        check: done
    new: done
listen: done
```

---

## File structure

```
myproject/
  app.mho          // main file
  .env             // DATABASE_URL and other secrets — never committed
```

Secrets live in `.env` and are accessed with `env.VARIABLE_NAME`. If you hardcode a secret, the compiler will tell you. That's not a warning. It's an error.

---

## Running your program

```bash
mio run app.mho          # run the program
mio check app.mho        # check for errors without running
mio serve app.mho        # serve it (for listen for / page apps)
```

---

## Language reference

### Variables

```mohio
name "Ron"           // scalar — one value
count 0               // also scalar

name request.name default "guest"   // with fallback
```

> **A variable holds one value (a scalar).** `hold` is a scalar *temporary lock*, not a list builder. **Lists** are being finalized — see the Language Reference for their current status; to assemble a structured value today, use a `create ShapeName … create: done` block.

### Routes

```mohio
// `listen for` is the entry point. The WORD carries the method -- you never
// declare GET/POST. Each listener closes with its own named closer.
listen for
    request for sh.Page at /path       // GET  -- read a page or data
        give back 200 "Here you go."
    request: done
    new sh.Form at /submit             // POST -- a submission arrived
        give back 201 "Created."
    new: done
    request for sh.User at /users/:id  // URL parameter -> request.id
        give back 200 "User {{ request.id }}"
    request: done
listen: done
```

### Responses

```mohio
give back 200 "Success."
give back 200 data as json
give back 201 "Created."
give back 400 "Bad request."
give back 404 "Not found."
give back 503 "Service unavailable."
```

### Decisions

```mohio
check value
    when value is more than 100
        // do something
    when value is more than 50
        // do something else
    otherwise
        // default
check: done
```

### Database operations

```mohio
connect db as postgres from env.DATABASE_URL    // once, at top

// Save
save to db.table
    field_name   value
save: done

// Retrieve one record
retrieve thing from db.table
    match id to request.id
    on.failure
        give back 404 "Not found."
retrieve: done

// Find multiple records
find things in db.table
    where status is "active"
    order.down by created_at
    limit 20
    on.failure
        give back 500 "Could not load."
find: done

// Update
update db.table
    match id to thing.id
    field_name   new_value
update: done

// Upsert (compound key)
upsert db.preferences
    match user_id to request.user_id
    match setting to request.setting
    value    request.value
upsert: done

// Remove
remove from db.table
    match id to request.id
remove: done

// Create (assemble an object from parts — not a DB write, use save for that)
create invoice
    customer    member.name
    items       cart.items
    total       cart.total
    issued      today
create: done
```

> **Note:** `find` inside `create` is invalid. `create` assembles; `find` retrieves. Always separate blocks. If you need data to build the object, retrieve it first, then create.

### AI decisions

```mohio
ai.decide name returns type
    confidence above 0.85       // threshold — must be between 0 and 1
    weigh
        field_one,              // inputs the model weighs
        field_two
    ai.audit to audit_log       // logs the decision; goes BEFORE not confident
    not confident               // REQUIRED — what if below threshold?
        give back 202 "Needs review."
    on.failure                  // optional — handle the AI being unavailable
        give back 503 "AI offline."
ai.decide: done
```

`ai.decide` returns the named value (`name` in the example above). Use it like any boolean or typed value after the block.

### The `ai.` namespace

`ai.` is a hard-reserved **namespace** for AI primitives, the same way `cm.` is the namespace for compliance. It groups the free, basic AI primitives: `ai.decide` (a typed judgment call), `ai.create` (generation), `ai.explain` (a plain-language reason for a decision), and `ai.audit` (a decision trail). (Down the line `ai.` may also attach to standard verbs to add AI reasoning to them, but that is not in the immediate plans.)

```mohio
// ai.create — generate from a prompt (simplest form)
ai.create text prompt "A friendly one-line welcome for a new player."

// ai.create — generate FROM a source object, with free-form hints
ai.create summary from report
    tone "executive"
    length "brief"
    on.failure
        give back 503 "Summary service unavailable."
ai.create: done

// ai.decide — a typed judgment call with a required fallback
ai.decide is_fraudulent returns boolean
    confidence above 0.85
    weigh
        transaction.amount,
        member.history
    ai.audit to fraud_audit_log
    not confident
        give back 202 "Referred for review."
    on.failure
        give back 503 "AI unavailable."
ai.decide: done

// ai.explain — a plain-language reason for a decision (optional)
ai.explain is_fraudulent as reason
```

`not confident` is required on **`ai.decide`** — the compiler refuses to build a decision without a low-confidence fallback. `on.failure` (what happens if the AI is unavailable) is optional but recommended, and `ai.audit` is recommended too — a sector profile such as `financial` or `healthcare` requires it. The other `ai.` primitives don't take `not confident`: `ai.create` just generates, and `ai.explain` is optional — add it when you want a human-readable reason for a decision.

**Why `ai.` is special.** The rest of Mohio computes a result from things you already have. The `ai.` primitives are different: they reach out to a live AI model to *do* the work. `ai.decide` sends your inputs to a model and reads back a judgment; `ai.create` asks it to generate content; `ai.explain` asks for a reason in plain words. So an `ai.` block is a hybrid — it reads like ordinary Mohio, but at runtime it makes a real call to an AI service and waits for an answer. That is exactly why every `ai.decide` must say what happens when the model is unsure (`not confident`, required), and should plan for it being unavailable (`on.failure`, recommended): you are depending on something outside your own code, so you have to plan for it.

### Caching

```mohio
retrieve rates from db.rates
    where product is request.product
    cache for 5 minutes
    on.failure
        give back 500 "Rate service unavailable."
retrieve: done
```

Add `cache for N minutes` (or hours, days) to any retrieve or find block. Cache is keyed automatically on the query parameters.

### Error handling

```mohio
try
    // risky operation
    on.failure
        give back 500 "Something went wrong."
    always
        // runs whether try succeeded or failed
try: done
```

### Sector profiles

```mohio
sector: financial           // activates PCI-DSS v4, SOC2, BSA/AML
sector: healthcare          // activates HIPAA, HITECH
```

One `sector:` per file. Put it at the top, before `connect`. Sectors are hierarchical and additive — adding subsectors layers on more rules, and a deeper declaration applies every layer above it:

```mohio
sector: financial.banking.retail   // financial + banking + retail rules, all active
```

Your own declarations can tighten a profile's defaults, but a broader layer's security baselines are non-overridable no matter what you declare (hardcoded secrets, CVV storage, PHI encryption).

---

## What the compiler catches

- Missing `not confident` path in any `ai.decide` block (the required low-confidence fallback)
- Hardcoded secrets (passwords, keys, tokens in source)
- `card_cvv` storage attempts (financial sector)
- PHI fields accessed without authentication (healthcare sector)
- Malformed `sector:` declarations
- `{` or `}` brackets anywhere (compile error — not a warning)
- `make` keyword — retired; it's a compile error (use `create` instead)
- Unicode arrow `→` (use `->`)

---

## Named closers — the full list

Every block closes with its own name followed by `: done`.

| Block | Closer |
|---|---|
| `ai.decide name` | `ai.decide: done` |
| `create name` | `create: done` |
| `retrieve x from` | `retrieve: done` |
| `find things in` | `find: done` |
| `save to` | `save: done` |
| `update` | `update: done` |
| `upsert` | `upsert: done` |
| `remove from` | `remove: done` |
| `check value` | `check: done` |
| `listen for` | `listen: done` |

---

## Common patterns

### Require authentication

```mohio
listen for
    request for sh.Dashboard at /dashboard
        require role "member"
        // ... rest of the handler
    request: done
listen: done
```

### Paginated list endpoint

```mohio
listen for
    request for sh.Transactions at /transactions
        require role "analyst"

        find transactions in db.transactions
            where account_id is request.account_id
            order.down by created_at
            paginate by 25
            on.failure
                give back 500 "Could not load transactions."
        find: done

        give back 200 transactions as json
    request: done
listen: done
```

### Conditional sector threshold

```mohio
// In sector: financial — CTR threshold is already known to the compiler
// This is how you act on it explicitly:

check transaction
    when transaction.amount is more than 10000 and transaction.type is "cash"
        cm.report "CTR" for transaction
check: done
```

### AI with human review gate

```mohio
ai.decide is_approved returns boolean
    confidence above 0.90
    weigh
        application.income,
        application.history,
        application.dti_ratio
    ai.audit to credit_audit_log   // ai.audit goes BEFORE not confident
    not confident
        give back 202 "Referred to underwriter."
    on.failure
        give back 503 "Decisioning unavailable."
ai.decide: done

// ECOA -- if not approved, refer to a human. (Generate a plain-language reason
// with a standalone `ai.explain is_approved as reason` block; see AI decisions.)
// `unless` is a trailing one-liner guard -- it does NOT open a block.
give back 202 "Referred to a human underwriter." unless is_approved
```

---

## Pioneer responsibilities

You signed up to build something real with this. We ask three things:

1. **Use it.** Even a small project — a personal tool, a demo, a weekend experiment. Real usage finds what tests don't.
2. **Report honestly.** GitHub issues are the mechanism. Bugs, awkward syntax, missing features, things that confused you. All of it is useful. Especially the things that confused you.
3. **If it works for you, say so.** A quote, a mention, a GitHub star. No pressure — but if Mohio helped you, letting people know helps us keep building.

**GitHub:** github.com/countart/mohio  
**Discord:** discord.gg/MF95H3wQdm  
**Email:** hello@mohio.io

---

## What's coming

- `mioagent` — agent primitive (ai.agent) for multi-step AI workflows
- `miotranslate` — translate .mho programs to other languages
- PT/ES native langmaps — language keywords in Portuguese and Spanish
- Rust rewrite — Q4 2026, same syntax, significantly faster runtime
- Certified sector profiles — legally reviewed, regulation-tracked

You'll be first to know. That's the deal.

---

*MOHIO™ · Patent pending · Open core (BSL 1.1 base compiler) · Particular LLC · Mooresville, NC*
