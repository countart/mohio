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

**A real gap, named here so it is not mistaken for a credentials
problem:** even a syntactically well-formed key does not complete a real
Anthropic call today -- the SDK call itself raises
`TypeError: Messages.create() got an unexpected keyword argument 'temperature'` before any
network response comes back, and `on.failure` correctly catches it (`503 AI service
unavailable`). This reproduces with any key, valid or not, so it is not something you can fix
by rechecking your key -- it is a version mismatch between the installed `anthropic` package
(`pip install -e .` currently pulls `1.0.0`) and code written for an older shape of that SDK's
`Messages.create()` call. It is a packaging mismatch rather than anything to fix in your
own program, and pinning the older SDK is the workaround until the call is updated.

### Checking your setup without spending anything

- `echo $ANTHROPIC_API_KEY` (Mac/Linux) or `echo $env:ANTHROPIC_API_KEY` (PowerShell) confirms
  the variable is actually set in your current shell.
- Running with no `--ai` flag always stays in mock mode, free, whatever your keys are set to --
  use this while you're still writing the `ai.decide` block itself.
- Running with `--ai` and NO key set is the fastest way to confirm Mohio would pick a key up
  if you set one -- you'll see the "No AI provider key found" message above.

---

**Next: Chapter 4, An App That Reasons** -- a real `ai.decide` block, its audit trail, and what
each outcome (confident, not confident, and unavailable) actually returns.
`Docs/manual/mohio-manual-part1-ch4-app-that-reasons.md`.
