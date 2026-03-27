# Mohio

**Simplify the complicated.**  
**Make it stupid easy to build the most involved, insanely complicated programs.**

Mohio (pronounced *moh-hee-oh*, Māori for "to understand") is the first programming language where AI reasoning is a native primitive — not a library, not an API call, not an afterthought. It is also the first language with institutional knowledge built in: a developer working in healthcare, finance, or government can sit down on day one and write domain logic, not infrastructure.

```
CLI:        mio
Extension:  .mho
Domain:     mohio.io
GitHub:     github.com/countart/mohio
Discord:    discord.gg/MF95H3wQdm
```

> *"Write intent. Execute reason. See everything."*

---

## The Problem

Every developer building AI-powered applications is manually reinventing the same painful infrastructure:

- Call an AI API → parse a string → hope the format is right → write your own error handling → build an audit trail from scratch → wire compliance manually
- Frontend and backend are separate problems with no unifying language
- Compliance is always bolted on — weeks of work before you write a single line of business logic
- Vibe coding generates code you can't read, debug, or own
- Deployment is whoever figures it out last

Mohio eliminates all of it at the language level.

---

## What Makes Mohio Different

```mohio
sector: financial

check transaction with FraudService
    sending
        transaction_id  = transaction.id
        amount          = transaction.amount
        member_id       = member.id
    expect      FraudDecision
    wait up to  2 seconds
    if no answer    give back pending "Under review"
    if fails        give back error   "Service unavailable"

consider FraudDecision.result
    when true
        update transactions
            status = "blocked"
            where id = transaction.id
        miomail.send
            to      = member.email
            subject = "Transaction blocked"
            body    = fraud_alert_template
        give back 403 "Transaction blocked"
    when false
        update transactions
            status = "approved"
            where id = transaction.id
        give back 200 "Transaction approved"
```

A basic developer reads that and knows exactly what it does. A compliance officer reads it and sees PCI enforcement. A security engineer reads it and sees no hardcoded keys, typed responses, and mandatory fallbacks. That is Mohio.

---

## The Five Problems Mohio Solves

**1. AI is a guest in every other language.**  
In Python, JavaScript, Go — AI is always something you import. In Mohio, `ai.decide` is a language construct. The compiler understands it. The runtime enforces its rules. The audit trail is automatic.

**2. Compliance is always bolted on.**  
`sector: healthcare` activates HIPAA/HITECH enforcement automatically. `sector: financial` activates PCI DSS and SOC2. One declaration. No framework. No configuration files. No manual wiring.

**3. Industry knowledge lives nowhere in code.**  
A healthcare developer has to manually know that `mrn` is PHI, that patient records must be retained for 6 years, that AI diagnostic decisions require human review. In Mohio the language already knows.

**4. AI failures are silent.**  
Every `ai.decide` block requires a fallback. The compiler rejects code that omits one. Failure handling is not optional.

**5. Safety is opt-in everywhere else.**  
In Mohio, unsafe code is what you have to opt into. Validation is built into the type system. Secrets must use `env.` or `secret.`. SQL injection is structurally impossible. AI decisions cannot ship without a fallback.

---

## Core Syntax

### Variables and Constants
```mohio
name            = "Ronnie"          // mutable — can change
hold THRESHOLD  = 0.85              // soft constant — fixed during execution
lock MAX_RETRIES = 3                // hard constant — compiler enforced
phone as text   = "5555555555"      // typed variable — name first, type second
```

### AI-Native Decisions
```mohio
ai.decide is_fraudulent(transaction) returns boolean
    confidence above 0.85
    weigh
        transaction.amount,
        transaction.location,
        member.transaction_history,
        device.fingerprint
    not confident enough
        give back false
        miolog.warn "Fell below confidence threshold"
    ai.audit to fraud_audit_log
```

### Compliance as a Declaration
```mohio
sector: healthcare
compliance: HIPAA

save to patients values
    mrn         = form.mrn         [phi]
    name        = form.name        [pii]
    diagnosis   = form.diagnosis   [phi]
    dob         = form.dob         [phi, pii]
```

### Data Operations
```mohio
// Retrieve with guard
member = retrieve member from db
    where id = session.user_id
    if not exists give back 404 "Member not found"

// Update — plain assignment, no set keyword
update credits
    balance    = balance - charge
    updated_at = now()
    where user_id = current.user_id

// Safe to retry
miohttp.post to env.PAYMENT_API
    sending
        amount = order.total
        token  = payment.token
    expect PaymentResult
    unique by order.id
    wait up to 10 seconds
    if no answer    give back pending "Payment pending"
    if fails        give back error   "Payment unavailable"
```

### Distributed Operations
```mohio
saga process_order(order, payment)

    step reserve_inventory
        update inventory
            reserved = reserved + order.quantity
            where product_id = order.product_id
        compensate
            update inventory
                reserved = reserved - order.quantity
                where product_id = order.product_id

    step charge_payment
        charge = miohttp.post to env.PAYMENT_API
            sending amount = order.total, token = payment.token
            unique by order.id
        compensate
            miohttp.post to env.PAYMENT_API
                sending action = "refund", charge_id = charge.id
                unique by "refund-{{ order.id }}"

    step notify_customer best effort
        on saga committed
            miomail.send to order.customer_email
                subject = "Order confirmed"
                body    = order_confirmation_template
```

### Typed Shapes
```mohio
shape Transaction
    id              as text         required
    amount          as decimal      required    min 0.01
    merchant        as text         required    max 200
    status          as text         default "pending"
        allowed "pending" "approved" "blocked" "flagged"
    device_known    as boolean      default false
    created_at      as datetime     default now()
```

### Journey — Multi-File Applications
```mohio
// journey.mho — the application orchestrator
journey fraud_platform

    include "routes/transactions.mho"
    include "decisions/fraud_check.mho"
    include "notifications/alerts.mho"

    shared
        connect postgres as db from secret.DATABASE_URL
        connect redis    as cache from secret.REDIS_URL
        compliance: PCI_DSS
        sector: financial

    hold claude_creative = ai.provider "anthropic"
        model   "claude-sonnet-4"
        key     secret.ANTHROPIC_KEY

    assets
        style   "/css/global.css"
        font    "https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700"
```

### Page Structure
```mohio
page dashboard_page

    fetch
        member       = retrieve member from db where id = session.user_id
        transactions = retrieve set of transactions from db
            where member_id = member.id
            order by created_at desc
            limit 10

    think
        risk_summary = mioai.summarize transactions
            max_words 50
            audience  "member"

    show
        show header with title = "Dashboard", user = member
        <main class="dashboard">
            <h1>Welcome back, {{ member.name }}</h1>
            <p class="summary">{{ risk_summary }}</p>
            each tx in transactions
                <div class="transaction">
                    <span>{{ tx.merchant }}</span>
                    <span>{{ tx.amount }}</span>
                    <span class="status {{ tx.status }}">{{ tx.status }}</span>
                </div>
        </main>
        show footer

    style
        <style>
        .dashboard { max-width: 960px; margin: 0 auto; padding: 2rem; }
        .transaction { display: flex; justify-content: space-between; padding: 0.75rem; }
        </style>
```

---

## Sector Profiles — Institutional Knowledge Built In

```mohio
sector: healthcare    // Activates HIPAA + HITECH enforcement
sector: financial     // Activates PCI_DSS + SOC2 enforcement
sector: legal         // Activates SOC2 + privilege enforcement
sector: education     // Activates FERPA + COPPA enforcement
sector: government    // Activates FedRAMP + FISMA enforcement
```

When you declare a sector, the compiler loads a pre-built profile that:
- Activates compliance frameworks automatically
- Defines industry-standard field types and their sensitivity tags
- Sets retention rules
- Applies AI decision constraints
- Enforces access control requirements

Run `mio expand sector healthcare` to see exactly what the profile adds to your file — transparent, auditable, overridable.

---

## Built-In Services

```
miohttp      Outbound HTTP — .get .post .put .delete .patch
miomail      Email — .send .queue .receive .template
miofile      Files — .read .write .upload .move .delete .zip
mioauth      Auth — .login .logout .oauth .jwt .mfa .apikey .ratelimit
miocache     Caching — .get .set .delete .flush
miolog       Logging — .info .warn .error .metric .span .alert
mioschedule  Scheduling — .every .at .on .cancel
miopdf       PDF — .from .merge .split .protect .fill
miotest      Testing — .describe .it .expect .mock .snapshot
miosearch    Search — .index .query .facet
mioimage     Images — .resize .crop .convert .watermark
mioai        AI generation — .generate .summarize .embed .research .classify
miosms       SMS — .send .receive
miostream    Streaming — .open .send .close .subscribe
miodata      Data — .xml .csv .json .yaml .validate
```

---

## CLI Reference

```bash
# Core
mio run    [file.mho]         # Execute a program
mio serve  [journey.mho]      # Start HTTP server
mio build  [journey.mho]      # Compile without serving
mio test   [file.mho]         # Run test suite

# Code quality
mio fmt    [file.mho]         # Format code — canonical output
mio fmt    --annotate         # Add interpretation comments
mio fmt    --check            # CI mode — fail if not formatted
mio lint   [file.mho]         # Lint with compliance awareness
mio lint   --strict           # Errors and warnings both fail

# Sector and compliance
mio check  sector [sector]    # Check sector profile version
mio expand sector [sector]    # Show what profile adds to your file
mio check  compliance [sector] # Full posture report
mio check  baa [sector]       # Scan for BAA requirements

# Secrets
mio secrets check [journey]   # Verify all secrets resolvable
mio secrets list  [journey]   # List all secret references

# AI and performance
mio cost   report             # AI cost analysis
mio quality report            # Decision quality monitoring
mio load   test [journey]     # Load test with AI-specific metrics
mio benchmark run             # Official benchmark suite

# Deployment
mio deploy  [journey]         # Deploy application
mio rollback [journey]        # Rollback to previous version
mio status  [journey]         # Deployment status and health
```

---

## What Mohio Is Not

- **Not a ColdFusion replacement.** No lineage, no nostalgia, no compatibility layer.
- **Not a no-code tool.** Developers write Mohio. It is a real programming language.
- **Not a wrapper around AI APIs.** AI reasoning is native to the language, not a library bolted on.
- **Not opinionated about your stack.** Mohio talks to any database, any API, any AI provider.
- **Not magic.** Every decision is auditable. Every behavior is explainable. Nothing is hidden.
- **Not a box.** Mohio gives you the primitives. How you architect your systems is your decision.

---

## Architecture

Mohio is a domain-specific language with a Python-based interpreter. It runs anywhere Python runs — laptop, Docker container, cloud function, edge node. No license server. No vendor commitment.

```
.mho files        ← what developers write
Mohio interpreter ← what we build (Python)
Python runtime    ← what runs the interpreter
Your infrastructure ← unchanged
```

The runtime is stateless. The database is yours. The audit trail is yours. The sector profiles are transparent and overridable. Every decision the runtime makes is visible and auditable.

---

## The Three Layers

| Layer | What It Is | Cost |
|---|---|---|
| **Language core** | Interpreter, CLI, VS Code extension, mio* built-ins, .mho spec | **Free forever** |
| **Commercial runtime** | Compliance dashboard, audit portal, regulatory exports, managed hosting | **Paid — SaaS + Enterprise** |
| **Mohio Platform** | Visual builder that generates clean, readable, hand-editable Mohio | **Paid — Closed source** |

The language is free. The platform is the business. Native compliance and AI-as-primitive are the first moat. The fine-tuned sector model is the last one anyone can cross.

---

## Current Status

Mohio is in **Language Design** phase. The interpreter is not yet built. This is the right time to get involved.

- [x] Language Design Document v1.4
- [x] Sector profiles: healthcare v1.1, financial v1.0
- [x] Landing page — mohio.io
- [x] Discord community
- [ ] Python interpreter (Lark-based) — Phase 2
- [ ] AI runtime layer — Phase 3
- [ ] VS Code extension — Phase 5
- [ ] Built-in services — Phase 6
- [ ] Open core release — Phase 7

---

## Get Involved

**Join the waitlist:** [mohio.io](https://mohio.io)  
**Discord:** [discord.gg/MF95H3wQdm](https://discord.gg/MF95H3wQdm)  
**GitHub:** [github.com/countart/mohio](https://github.com/countart/mohio)  
**Email:** hello@mohio.io  
**Support:** [buymeacoffee.com/mohiolang](https://buymeacoffee.com/mohiolang)

### Sector Pioneer Program

We are inviting Financial/Fintech and Healthcare developers to use Mohio before public launch. You get early access to certified sector profiles. We get real-world feedback that makes the language better.

Email **hello@mohio.io** with your name, sector, and one sentence on the compliance problem that costs you the most time.

---

## License

Apache 2.0 — see [LICENSE](LICENSE)

The language core is free forever. Open source. Community-extensible. No license server. No per-CPU cost. No server edition.

---

*© 2026 Particular LLC · mohio.io*  
*"Write intent. Execute reason. See everything."*
