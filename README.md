<div align="center">

<img src="https://mohio.io/img/mohio-6-mark.png" alt="Mohio" width="110" />

<img src="https://mohio.io/img/mohio-6-wordmark.png" alt="Mohio 6" width="380" />

### Write intent. Execute reason. See everything.

**The first AI-native programming language - where AI reasoning is a compiler-enforced primitive, not a library, not an API, not an afterthought.**

[![BSL 1.1](https://img.shields.io/badge/license-BSL%201.1-teal.svg)](LICENSE)
[![Release](https://img.shields.io/badge/release-6.0.3-0D7377.svg)](https://github.com/countart/mohio/releases)
[![Discord](https://img.shields.io/badge/Discord-Join%20us-5865F2.svg)](https://discord.gg/9tq7tGSNYE)
[![X](https://img.shields.io/badge/X-@mohiolang-000000.svg)](https://x.com/mohiolang)
[![LinkedIn](https://img.shields.io/badge/LinkedIn-Mohio-0077B5.svg)](https://linkedin.com/company/mohiolang)
[![Hugging Face](https://img.shields.io/badge/%F0%9F%A4%97-mohiolang-FFD700.svg)](https://huggingface.co/mohiolang)
[![Zork Demo](https://img.shields.io/badge/Live%20Demo-zork.mohio.io-0D7377.svg)](https://zork.mohio.io)
[![Platform](https://img.shields.io/badge/Platform-getmohio.com-0D7377.svg)](https://getmohio.com)

*Mohio (moh-hee-oh) - from te reo Māori: to understand.*

> **New here? Start with [`start-here/`](start-here/)** for install, your first program, and links to every verified guide, in one place.

*Conceived March 23, 2026. Dunkin drive-thru. Mooresville, NC.*
*Born later that day. Side of I77. Charlotte, NC.*

</div>

---

In March 2026 Mohio was a demo running in a terminal. Today it is the first official public release, going into its first regulated-bank deployment. The handful of tags you see on this repo sit on top of roughly 1,500 commits of private work: the compiler, five execution engines, the governance primitives, the sector profiles, and the test batteries that gate every release.

Here is the thing it exists to make ordinary:

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
    on.failure
        give back false
ai.decide: done
```

A non-developer reads that in three seconds and understands the business intent. That is a design constraint, not a style preference - every keyword decision is measured against it. The AI decision is governed, audited, and has a defined fallback, because the compiler refuses to build it otherwise.

---

## AI-native does not mean AI writes your app

Mohio is a real programming language. You write it. It has rules, a compiler, and a grammar.

AI-native means one thing: `ai.decide`, `ai.audit`, `ai.agent`, and `ai.explain` are built into the language itself - enforced by the compiler, not bolted on as a library, not called through an API. When your Mohio program makes an AI decision, it is governed, auditable, and typed. That is what AI-native means.

If you want a tool that generates an app from a description, other products do that well. Mohio is what you reach for when you need code you can read, own, modify, and trust - with AI reasoning that is part of the program, not a black box beside it.

> **In one line:** Mohio is a programming language. You write it. AI reasons inside it.

---

## Getting it, and somewhere to put it

Two pieces sit either side of the language itself.

**Mohio Home** is the installer and console. It puts Mohio on your machine without a clone or
`pip install`, and gives you a place to see what your programs are doing. Download for your system
(these links always point at the current release):

- Windows: download both into the same folder, then run the `.bat`:
  - [bootstrap_windows.bat](https://github.com/countart/mohio-home-releases/releases/latest/download/bootstrap_windows.bat)
  - [bootstrap_windows.ps1](https://github.com/countart/mohio-home-releases/releases/latest/download/bootstrap_windows.ps1)
- macOS: [bootstrap_macos.command](https://github.com/countart/mohio-home-releases/releases/latest/download/bootstrap_macos.command)
- Linux: [bootstrap_linux.sh](https://github.com/countart/mohio-home-releases/releases/latest/download/bootstrap_linux.sh)
- All releases: [mohio-home-releases](https://github.com/countart/mohio-home-releases/releases)

**[getmohio.com](https://getmohio.com)** is the platform. Serve a Mohio application without
assembling hosting yourself: each app gets its own database, durable file storage and secrets,
deployed from a tagged release. Real applications are running on it now. Access is currently by
invitation, with a free tier for pioneers; open sign-up follows once billing is in place. Ask on
Discord for an invitation.

Neither is required: everything else in this README runs from a clone or `pip install` on your own
machine.

---

## Your first program

Shorter than the demo above, and it shows the thing Mohio is actually for. Save it as
`first.mho` and run it. No API key, no database to install, nothing to configure.

```mohio
// A patient record. The [phi] tag is the whole of the protection.
shape Patient
    chart as text [phi]
    name as text
shape: done

connect db as sqlite from env.DATABASE_URL

save to db.patients
    chart "diagnosis: acute bronchitis"
    name "Ada Lovelace"
save: done

retrieve p from db.patients
    match name to "Ada Lovelace"
    on.failure
        show "no record"
retrieve: done

show ("patient: " & p.name)
show ("chart:   " & p.chart)
```

```bash
MOHIO_ENCRYPTION_KEY=demo-key mio run first.mho
```

```
  patient: Ada Lovelace
  chart:   ****itis
```

One tag did three things. The chart was encrypted before it reached the disk, it came back
masked on the way out, and the sentence around it survived: `chart:` is still there, only the
diagnosis is hidden. The name was never tagged, so nothing happened to it.

Point any SQLite browser at the file and this is what is stored:

```
  name  = Ada Lovelace
  chart = enc:v1:Fuq1/GBeXw85aKoKA5Gzs05r1792x8IrkAt0B...
```

*(The encrypted value is different on every run by design, so yours will not match this one
character for character. The `enc:v1:` marker and the absence of the diagnosis are the point.)*

That protection is bound to the value, not to the column. Copy `p.chart` into a variable and
save it somewhere else and it is still encrypted when it lands, because what is protected
travelled with it.

---

## What makes Mohio different

### 1 - AI reasoning is a language construct, not a function call

`ai.decide` is understood by the compiler, enforced by the runtime, automatically audited.

**The compiler refuses to build without a fallback.** `tests/no_fallback_illustration.mho` is a minimal `ai.decide` with the `not confident` block deliberately removed - run it yourself:

```bash
mio check tests/no_fallback_illustration.mho
```

```
  x ai.decide 'critical_decision' is missing a 'not confident' block.
    Every ai.decide must define what happens when confidence falls below threshold.
    Add 'not confident' inside 'ai.decide critical_decision'.

  x ai.decide 'critical_decision' has no 'ai.audit' declaration.
    Add 'ai.audit to [log_name]' so this AI decision produces an immutable record.
    An unaudited AI decision cannot be reviewed or defended after the fact.

  x ai.decide 'critical_decision' has no 'on.failure' handler.
    Add 'on.failure' inside 'ai.decide critical_decision' so the program says what happens
    when the AI service itself is unreachable.
    Without it a provider outage becomes an unhandled error at runtime.

  x  tests/no_fallback_illustration.mho  13 lines . 1 warning(s) . 4 error(s)
```

Three separate things are missing and the compiler names all three: what happens when the model
is unsure, where the decision is recorded, and what happens when the provider is down. Each one
is a way a governed decision quietly stops being governed.

**The audit trail is not optional.** `ai.audit to escalation_audit_log` writes an immutable record of every decision - inputs, result, confidence, model, timestamp.

**Confidence is a first-class value.** `confidence above 0.85` is enforced by the runtime, not the model.

### 2 - Compliance is a declaration

```mohio
sector: demo_high    // shipped community demo profile -- not certified, not legal compliance
```

One word activates the mechanism. No library installation. No configuration files. No manual wiring - and the mechanism is real, not aspirational. Real `mio check` output on a program with its `ai.decide` declared at `0.85` confidence, one notch under the active sector's floor:

```
  x ai.decide 'critical_decision' confidence 0.85 is below the demo_high sector floor of 0.95.
    Raise confidence to at least 0.95 for demo_high sector compliance, or, if this decision
    is non-regulatory, add sec.non_critical reason "...". See: mohio.io/docs/sectors/demo_high
```

The program does not compile until the declared confidence meets the sector's floor. That is the whole enforcement story for the profiles shipped today: one rule, a confidence floor, genuinely loaded and genuinely gating the build. `mio check` also labels the profile honestly, unprompted:

```
  ! sector: demo_high is a community or unverified profile. Review carefully before production use.
    Use an official Mohio sector profile for production compliance.
```

**What is not true today:** `sector: financial` and `sector: healthcare` - the names a regulated team would reach for first - currently load **zero rules**. Declaring either is accepted by the compiler and does nothing yet. Certified, legally reviewed profiles are a separate commercial tier arriving after formal compliance review.

*Mohio activates technical enforcement controls - not a guarantee of legal compliance. Qualified legal counsel does that.*

### 3 - The code reads like you wrote it in English

See the escalation example at the top of this page. The Walk-By Test: a non-developer reads a Mohio program in three seconds and understands the business intent. This is a design constraint, not a style preference. Every keyword and syntax decision is evaluated against it.

### 4 - Named closers - precision-machined blocks

Every block opens with a verb and closes with its name. Closer mismatches are compile errors:

```
Line 24 - closer mismatch. Expected: ai.decide: done. Found: find: done.
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

### 5 - AI provider failover - `ai.connect`

Named provider groups with ordered fallback - declare once, reference anywhere:

```mohio
connect db from env.DATABASE_URL   // where ai.audit keeps each decision's record

ai.connect fraud_providers, text_gen
    order
        anthropic model "claude-opus-4-8"
        openai    model "gpt-4o"
    order: done
    on.failure give back "Could not connect to LLM provider."
ai.connect: done

// Reference it inside ai.decide
ai.decide isFraudulent returns boolean
    using fraud_providers
    confidence above 0.85
    weigh
        transaction.amount
    ai.audit to fraud_audit_log
    not confident
        give back [202] "Referred for review."
    on.failure
        give back [503] "Fraud screening unavailable."
ai.decide: done
```

### 6 - Agent safety - Deterministic Runtime Boundary Gate

```mohio
ai.agent researchAssistant
    goal "Research this topic and summarize findings"
    limits
        max steps    10
        cost ceiling 1.00
    limits: done
    not confident
        give back "Research incomplete - limits reached"
ai.agent: done
```

When `max steps` is reached, `_AgentLimitExceeded` fires - a hard stop enforced by the interpreter step counter, not a guideline. The agent cannot continue regardless of the model's output. Cost ceiling works the same way. Both are deterministic.

### 7 - mio check - structured compile-time validation

```bash
mio check myapp.mho                 # structural check
mio check myapp.mho --security      # 8 named security checks (one is sector-conditional)
mio check myapp.mho --json          # machine-readable for agents and CI
```

Every error includes line number and actionable hint. Real `--json` output on a program with an `ai.decide` missing its `not confident` block:

```json
{
  "errors": [
    {
      "code": "ai_decide_no_not_confident",
      "line": 3,
      "message": "ai.decide 'critical_decision' is missing a 'not confident' block.",
      "hint": "Every ai.decide must define what happens when confidence falls below threshold.\nAdd 'not confident' inside 'ai.decide critical_decision'."
    }
  ]
}
```

### 8 - Single-row reads refuse an ambiguous match

`retrieve`, `grab`, and `get` read exactly one row. As of 6.0.3 they **fail loud** when more than one row matches, instead of silently binding an arbitrary one:

```
retrieve matched 2 rows, expected exactly one.
Constrain the condition to a key, use 'check exists' for a one-to-many condition,
or 'find' for the many.
```

Exactly one row binds. Zero rows takes the existing empty/none path unchanged. More than one is a developer-facing correctness error, named at `mio check` where the field's uniqueness is knowable statically, at runtime otherwise. "Any one of several" stays explicit: `find` + `order.up/down by` + `.first`.

### 9 - Atomic upsert

```mohio
upsert db.game_state
    match session_id to session_id
    current_room current_room
    score        score
upsert: done
```

INSERT or UPDATE atomically. Native on SQLite (INSERT OR REPLACE), Postgres (ON CONFLICT), MySQL, MongoDB.

### 10 - Money movement that cannot double-spend

A MioScript `send` carries one idempotency key per logical transfer intent, so a transfer against a backend that requires it succeeds with no application-side plumbing. A genuinely new intent gets a fresh key; retries, double-clicks, lost-response recovery, and redirects keep the same one. The backend's requirement for the key is never weakened and never satisfied by ungoverned JavaScript.

### 11 - Write Mohio in your language

The same program, authored in your own language. Structural connectors (`on.`,
`as.`, `ai.`) and the `mio*` service names never translate - only the readable
keywords do, so a program stays portable and the structure stays universal.

Language packs are in progress for Spanish, Portuguese, and Hindi, with the
mechanism designed to extend to any language. (Examples of translated source are
held back pending a provisional patent filing.)

A privacy tag is not a readable keyword and never translates. `[phi]` stays `[phi]` when the
source is written in Spanish and when the program is translated on the way to a deploy target,
so protection does not depend on which language the author was working in.

### 12 - Protection follows the value, not the column

A tag on a field is a statement about the data, so it travels with the data. Copy a `[phi]`
value into a variable, hold it, pass it into a task, join it into a sentence, and write it to a
column nobody tagged: it is still encrypted when it lands.

Masking follows the same rule and stops where the protected part stops:

```mohio
give back 200 ("the ssn is " & p.ssn)
```

returns `the ssn is ****1111`. The number is hidden, the sentence the program wrote is not.
Earlier versions masked the whole string, which protected four characters by destroying the
text around them. As of 6.0.3 a protected value nested in a list is masked on every egress path
at any depth - output, serialization, text conversions, joins, and page loops - with no path left
open.

`[pii]` seals at rest exactly like `[phi]` and `[pci]`, and deliberately does NOT mask on the
way out, because the everyday reason to hold an email is to show a person their own email.
Sealing on disk and hiding on screen are different questions and Mohio answers them separately.

### 13 - One contract, five engines

A source is described once and referenced through its connection. The same program runs against
PostgreSQL, MySQL, MariaDB, SQLite and MongoDB, with the engine named at the connection and
nowhere else. MySQL and MariaDB share one driver, which is what wire compatibility is for.

`save`, `retrieve`, `find`, `upsert` and `remove` mean the same thing on all of them, including
the parts that usually leak through: a document store identifies a record differently from a
relational table, and the language absorbs that rather than handing it to you.

**What is not true today:** `modify` needs the database to say which columns identify a row, so
that it changes one row and not another. SQLite, PostgreSQL and MongoDB can answer; the MySQL
driver cannot yet, so `modify` on MySQL or MariaDB refuses with a message saying so rather than
guessing. `update ... match <field> to <value>` addresses rows by the condition you write and
works on every engine.

### 14 - A refusal leaves a trace

A control that stops something is only as good as the record it leaves. An erasure request
refused because the table is under a legal hold, a partial erasure rolled back, a call stopped
because a compliance profile could not verify its data, a request denied for the wrong role:
each one writes a hash-chained audit entry saying what was asked for and what happened to the
request. Silence and never-having-been-asked look identical in a trail, and that is the one
thing an auditor cannot work with.

---

## Firsts (as far as we know)

- First language where `ai.decide` is a compiler-enforced primitive
- First language where a missing AI fallback is a build error
- First language with automatic immutable AI audit trails as a language construct
- First language where compliance is a single declaration
- First language where sector profiles enforce AI confidence floors
- First language where a shape declaration serves DB, API, validation, compliance, and AI simultaneously
- First language with the Walk-By Test as a formal design constraint
- First language with named block closers as a compile-time safety mechanism
- First language with deterministic runtime boundary gates on AI agents
- A native Right to Be Forgotten verb (`cm.purge`) - syntax, mandatory `reason` audit modifier, and compile-time enforcement ship today; cascade-delete execution is in active development and fails safe
- Zero-drift multilingual syntax - write Mohio in your language; structural connectors (`on.`, `as.`, `ai.`) never translate

---

## Current state

Current release: 6.0.3

Mohio is built in the open and moving fast - the compiler updates multiple times a
day. The core language is solid and tested; the surface around it is filling in.
Status below is honest about where each piece actually stands.

**Legend:** **Working** = wired and tested, build on it today · **Partial** = wired,
actively being completed, fails loud where unbuilt · **Designed** = grammar or spec
in place, implementation pending.

| Component | Status |
|-----------|--------|
| Lark grammar - Earley parser | Working |
| AST (65+ node types) | Working |
| Transformer + MohioValidator | Working |
| Structured errors - `ERROR`/`WARNING` with file, line, message, hint | Working |
| `mio check --security` - 8 named checks (one sector-conditional) | Working |
| Tree-walking interpreter | Working |
| SQLite data layer | Working |
| PostgreSQL data layer | Working |
| db CRUD, match / find / retrieve | Working |
| Single-row reads fail loud on ambiguous match | Working (6.0.3) |
| ai.decide - confidence, fallback, audit | Working |
| Native idempotency on money movement | Working (6.0.3) |
| Anthropic API integration | Working (real Claude reasoning) |
| casts, strings, includes, journey | Working |
| upsert | Working |
| mio check --json | Working |
| mio fmt - canonical formatter | Working |
| mio translate - source-language keyword translation | Working |
| Language Reference - generated from the grammar | Working - [live](https://mohiou.com/reference/) |
| mioschedule (Phase 1) | Working |
| mio warmup - Earley parse cache | Working |
| Zork text adventure (stress test) | Live at zork.mohio.io |
| ai.agent - boundary gate | Partial - wired, testing pending |
| ai.connect - named provider failover | Working |
| Compliance enforcement layer | Partial - mechanism wired, hardening in progress |
| sector profiles (activation, field types, floors) | Partial - wired, **not yet certified** |
| Auth / security built-ins | Partial - early |
| Web built-ins (routes, http, mail, file, cache) | Partial |
| cm.purge - right to be forgotten | Partial - verb + reason audit + compile-time enforcement ship; cascade-delete in dev, fails safe |
| Plugin registry - commercial extension point | Partial |

**On sectors:** the compliance *enforcement layer* exists and is wired - sector
declaration activates field classification, confidence floors, retention rules, and
audit hooks. What does **not** yet exist is a *certified* profile: legally reviewed,
attorney-vetted, with a maintenance commitment behind a certified seal. General
editable sectors are coming; certified **financial** and **healthcare** profiles
require paid compliance review and arrive after that work completes. Mohio activates
technical enforcement controls - it is not, and does not claim to be, a guarantee of
legal compliance.

---

## Documentation

The complete **[Language Reference](https://mohiou.com/reference/)** is generated
directly from the compiler grammar, so it always matches what `mio` actually
accepts. Every keyword is listed with its status - canonical, non-canonical,
retired, reserved, or not-yet-built - and every canonical example is verified to
compile. It is the place to start when writing real programs.

---

## Run it yourself

```bash
git clone https://github.com/countart/mohio
cd mohio
pip install -e .   # installs dependencies and the mio command

mio warmup
mio check tests/support_escalation_demo.mho
mio check tests/support_escalation_demo.mho --security
mio run   tests/support_escalation_demo.mho --seed tests/seed_support_escalation.json --memory --verbose

# Machine-readable output
mio check tests/support_escalation_demo.mho --json
```

The command above runs with no API key - it uses the mock AI runtime and an in-memory SQLite database, so it works on a completely fresh clone with no external setup.

**Real Claude reasoning requires `ANTHROPIC_API_KEY` set to a real key first:**

```bash
export ANTHROPIC_API_KEY=your_key
mio run tests/support_escalation_demo.mho --seed tests/seed_support_escalation.json --memory --ai --verbose
```

**Working on the compiler itself, not just writing Mohio programs?** `pip install -r requirements.txt` installs everything the test suite needs too, then `TESTING.md` has the one command to run it.

---

## The Mohio family

| Property | What it is |
|---|---|
| **[mohio.io](https://mohio.io)** | The language and the project home. |
| **[mohiou.com](https://mohiou.com/reference/)** | Mohio University - the full language reference and learning material. |
| **[getmohio.com](https://getmohio.com)** | The platform: install Mohio and serve applications on it. |
| **[mohioenterprise.com](https://mohioenterprise.com)** | The commercial runtime and certified sector profiles for regulated organizations. |
| **[mohioos.com](https://mohioos.com)** | Mohio OS - a desktop environment that organizes apps, context, and AI into Spaces, with apps you build in Mohio. In development. |

---

## Commercial features

The full language, compiler, standard built-ins, and community sector profiles are open core under BSL 1.1. In process or planned, commercial tier:

- `miochain.*` - blockchain integration, transaction signing via miovault
- `miovault` - HSM sidecar for key isolation
- `mioknow` managed - pgvector AI memory with sector compliance
- Certified sector profiles - legally reviewed, updated as regulations change

The language will never be closed.

---

## Sector Pioneer Program

Early access for financial and healthcare developers before public launch.

**What pioneers get:** Early runtime access, free certified sector profile, direct line to the team, pioneer credits.

**What we ask:** Build something real, tell us what is wrong, let us mention you if it works.

No contract. No commitment. No cost. Email **hello@mohio.io** with your sector and one sentence on the compliance problem that costs you the most time.

---

## License and intellectual property

Open core under the **Mohio Business Source License 1.1 (BSL)** - see [`LICENSE`](LICENSE) and [`LICENSE-SCOPE.md`](LICENSE-SCOPE.md). First public distribution 2026-11-04; Change Date 2030-11-04, Change License AGPL-3.0-or-later. The full language, compiler, standard built-ins, and community sector profiles are free to use; the commercial runtime adds `miochain`, `miovault`, managed `mioknow`, and certified sector profiles.

Compiler-enforced AI-safety primitives, sector-activated compliance enforcement, deterministic runtime boundary gates on AI agents, and agent-native structured compiler output are the subject of provisional patent applications filed with the USPTO.

For trademark and brand usage, see [TRADEMARKS.md](TRADEMARKS.md). For reserved commercial offerings, see [RESERVED-COMMERCIAL-OFFERINGS.md](RESERVED-COMMERCIAL-OFFERINGS.md). For licensing: **hello@mohio.io**

---

## Join the community

| | |
|---|---|
| Website | [mohio.io](https://mohio.io) |
| Language Reference | [mohiou.com/reference](https://mohiou.com/reference/) |
| Live Demo | [zork.mohio.io](https://zork.mohio.io) |
| Discord | [discord.gg/9tq7tGSNYE](https://discord.gg/9tq7tGSNYE) |
| X / Twitter | [@mohiolang](https://x.com/mohiolang) |
| LinkedIn | [Mohio](https://linkedin.com/company/mohiolang) |
| Hugging Face | [mohiolang](https://huggingface.co/mohiolang) |
| Support | [buymeacoffee.com/mohiolang](https://buymeacoffee.com/mohiolang) |
| Email | [hello@mohio.io](mailto:hello@mohio.io) |

---

<div align="center">

**Mohio Language Project · Particular LLC · BSL 1.1**

*6.0.3 · Write intent. Execute reason. See everything.*

</div>