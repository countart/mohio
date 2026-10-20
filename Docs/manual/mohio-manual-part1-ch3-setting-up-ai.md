<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Part I -- First Programs

## Chapter 3: Setting Up AI

*Rung 3 of the ladder, and the one chapter with nothing to build yet -- this is the setup
chapter 4's reasoning app needs before it can reach a real model. Every step below is verified
by running it, including the two things a reader actually hits: no key at all, and a key that
is set but does not work.*

---

### You do not need a key to start

`ai.decide` runs in mock mode by default -- no key, no cost, no network call:

```mohio
ai.decide isFraudulent returns boolean
    confidence above 0.85
    weigh amount
    ai.audit to fraud_audit_log
    not confident
        give back [202] "Flagged for manual review"
    on.failure
        give back [503] "AI service unavailable"
ai.decide: done

amount 100
ai.decide isFraudulent

check isFraudulent
    when true
        give back [422] "Transaction blocked"
    otherwise
        give back [200] "Approved"
check: done
```

```bash
mio run isfraud.mho --verbose
```

```
AI runtime: mock (use --ai for real Anthropic API)
[ai.decide] isFraudulent: False (conf=0.88, threshold=0.85)
[ai.audit] -> fraud_audit_log [c1ff49226060456f] sector:none

Response  200  Approved
```

Mock mode is deterministic and free -- it is how you write and test an `ai.decide` block
before spending anything on a real model. You can also ask for it explicitly with
`MOHIO_AI=mock`, which is what the error message below points you to if you ever need to force
it.

### The three provider keys, and where they go

Mohio reads three environment variables, checked in this order:

| Variable | Provider | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | Anthropic (Claude) | the default; `--ai` uses this one directly |
| `OPENAI_API_KEY` | OpenAI | reachable today by declaring an `ai.connect` provider chain |
| `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) | Gemini | same, via `ai.connect` |

Set the variable in your shell, the same way you'd set `DATABASE_URL` -- never in the `.mho`
file itself (a hardcoded-looking key is flagged at check time).

Windows (PowerShell):
```
$env:ANTHROPIC_API_KEY="your-key-here"
```
Mac or Linux:
```
export ANTHROPIC_API_KEY=your-key-here
```

### Running with a real provider: `--ai`

Add `--ai` to reach the real API instead of the mock:

```bash
mio run isfraud.mho --ai --verbose
```

**Verified, no key set at all** -- the error names exactly what's missing and every variable
that would satisfy it:

```
Error  No AI provider key found. Set ANTHROPIC_API_KEY, OPENAI_API_KEY, or GEMINI_API_KEY
       (or run with MOHIO_AI=mock for the labeled mock provider).
```

**Verified, a key IS set** (tested with a syntactically-shaped but non-working key, so the
call could be observed without spending anything) -- the runtime switches, builds a real
prompt, and genuinely reaches the provider layer:

```
AI runtime: Anthropic API (claude-sonnet-5)

[ai.decide -> API] isFraudulent
  Model: claude-sonnet-5
  Inputs: ['amount']
  Prompt:
Decision: isFraudulent
...
```

That is the config path working end to end: set the variable, add `--ai`, and Mohio stops
asking for a key and starts talking to the provider. Whether the CALL itself succeeds from
there depends on the key being valid, which this chapter cannot prove without spending real
money against a real account -- what it proves is that Mohio picks the key up correctly and
gets as far as the provider.

**A note on sampling with the current SDK.** The installed `anthropic` package (1.0.0) removed
`temperature` from `messages.create`, so on that SDK a decision runs under the provider's default
sampling rather than a temperature you set. Mohio detects this and omits the argument rather than
raising, and records which sampling control actually applied, so a decision still completes and the
audit says what was used. A real call reaches the provider: an invalid key comes back as a genuine
HTTP 401 that `on.failure` catches, not an SDK error.

### When an AI call fails, `failure` says where

Inside an `ai.` operation's `on.failure`, the `failure` value carries detail so a handler can tell a
bad key from an outage from an unusable answer (RULED 2026-10-05, verified by running):

- `failure.provider` -- who was asked (for example `anthropic`).
- `failure.stage` -- where it failed: `call` (the provider call itself, including an HTTP status such
  as a 401 or 500), `response validation` (the answer came back but was not a decision), or
  `confidence` (the confidence was outside 0..1).
- `failure.error` -- the provider's HTTP status when there was one (`HTTP 500`), otherwise its error.

```mohio
ai.decide is_fraudulent returns boolean
    confidence above 0.85
    weigh request.amount
    not confident
        give back [202] "Flagged for review."
    on.failure
        show "provider=" & failure.provider
        show "stage=" & failure.stage
        show "error=" & failure.error
        give back [503] "AI service unavailable."
ai.decide: done
```

The same three fields are carried by `ai.compare` and `ai.respond`, and alongside the fields every
failure already has (`failure.kind`, `failure.message`, `failure.status`).

### Declaring providers: `ai.connect`

`--ai` with `ANTHROPIC_API_KEY` is the quick path. To choose a provider, chain several for failover, or
reach one Mohio does not ship, declare an `ai.connect` and reference it from an AI verb with `using`.

**A fallback chain.** List providers in priority order, each with a REQUIRED model string. The chain falls
over to the next provider on failure or unavailability (a rate-limit counts); a cost-ceiling breach is a
deliberate refusal and does not switch providers.

```mohio
ai.connect answer
    order
        anthropic model "claude-haiku-4-5-20251001"
        openai    model "gpt-4o"
    order: done
ai.connect: done
```

Then `ai.decide verdict / using answer / ...`.

**One provider, or one Mohio does not ship.** Give the connection an `address` and a `key` directly
(`address` takes a URL string, `env.X`, or `secret.X`; `key` takes `env.X` or `secret.X`):

```mohio
ai.connect mine
    address "https://host/v1"
    key env.MYKEY
ai.connect: done
```

To use a custom provider inside a chain, declare it inline and name it in `order`: `address "https://host/v1"
key env.MYKEY as mine`, then `mine model "m1"` under `order`.

**The built-in providers.** Name any of these in an `order` block with a model and no address is needed;
Mohio already knows the endpoint. The table it ships (provider, endpoint, environment variable):

| Provider | Endpoint (address) | Environment variable |
|---|---|---|
| `anthropic` | (Anthropic SDK, no base URL) | `ANTHROPIC_API_KEY` |
| `openai` | `https://api.openai.com/v1` | `OPENAI_API_KEY` |
| `gemini` | `https://generativelanguage.googleapis.com/v1beta` | `GEMINI_API_KEY` |
| `groq` | `https://api.groq.com/openai/v1` | `GROQ_API_KEY` |
| `kimi` | `https://api.moonshot.ai/v1` | `MOONSHOT_API_KEY` |
| `qwen` | `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` | `DASHSCOPE_API_KEY` |
| `deepseek` | `https://api.deepseek.com` | `DEEPSEEK_API_KEY` |
| `perplexity` | `https://api.perplexity.ai` | `PERPLEXITY_API_KEY` |
| `grok` | `https://api.x.ai/v1` | `XAI_API_KEY` |

Providers move their endpoints, so treat this table as current-as-shipped, not permanent. You can OVERRIDE any
row: give a built-in name your own `address` and it replaces the shipped endpoint (`address "..." as openai`).
The table carries no model strings on purpose, because models change far more often than endpoints.

**Model is required; and the three check refusals.** Mohio keeps NO per-provider default model, so a provider
named with no model is refused at check (models change too often to pin one in the compiler). Run before this
was written, the three refusals an `ai.connect` can raise at check are: an EMPTY `ai.connect` (no address, no
`order`) refuses and names both ways to fill it; an UNRECOGNIZED provider name refuses and prints the known list
plus the declare-your-own form; and a provider with NO MODEL refuses and shows the `model "..."` fix.
(`ai.connect` replaced the retired `ai.chain`.)

### Checking your setup without spending anything

- `echo $ANTHROPIC_API_KEY` (Mac/Linux) or `echo $env:ANTHROPIC_API_KEY` (PowerShell) confirms
  the variable is actually set in your current shell.
- Running with no `--ai` flag always stays in mock mode, free, whatever your keys are set to --
  use this while you're still writing the `ai.decide` block itself.
- Running with `--ai` and NO key set is the fastest way to confirm Mohio would pick a key up
  if you set one -- you'll see the "No AI provider key found" message above.

### AI, documents, and which provider answers

The `ai.*` primitives (`ai.decide`, `ai.create`, `ai.explain`, `ai.agent`) read TEXT. They take the text you
give them -- a value, a field, a string you built -- and reason over it. They do NOT read a document or an image,
so passing an uploaded file to an `ai.*` call is refused at CHECK (verified by running):

```
Line 7: `ai.decide` is given `Claim.photo`, an uploaded file. AI primitives read text; reading a document or image is mioai.extract, which is designed but not built yet. Once the request is bound, `Claim.photo` holds the stored file's name, so the model would answer about the name and not the file.
  To fix it, weigh text instead, such as a field the person typed, and leave the file out of the AI call.
```

So weigh TEXT, never a file. Reading a document or image (pulling text or data out of a PDF, a scan or a photo)
is `mioai.extract`, a locked design (June 12) that is DESIGNED AND NOT YET BUILT; today an upload given to any
`ai.*` primitive refuses at check (above). Until `mioai.extract` lands, get the text out by other means and give
`ai.*` the text.

One separate current-build fact: a dotted `mioai.*` call refuses at check, with a message that calls the word
retired and points to `ai.create` / `ai.decide`. That is the current build's refusal for a verb that is not wired
yet; it is not a statement that the `mioai.extract` design is abandoned.

**`using` reaches only the chain it names.** An AI verb can name a provider chain with `using <name>`, and the
call reaches ONLY that chain. A `using` that names a chain no `ai.connect` declared refuses at RUN with a 503,
`ai.connect chain '<name>' is not registered (no ai.connect block declared it)`, so a missing or mistyped chain
fails loud rather than quietly falling back to a default. (The app still checks clean; the refusal is at run.)

---

**Next: Chapter 4, An App That Reasons** -- a real `ai.decide` block, its audit trail, and what
each outcome (confident, not confident, and unavailable) actually returns.
`Docs/manual/mohio-manual-part1-ch4-app-that-reasons.md`.
