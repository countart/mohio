<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Part VII -- AI

## Chapters 36 and 37: Generating and Acting -- `ai.create` and `ai.agent`

*Every example below was run, not recalled. `ai.create` and `ai.agent` are both built and both run
against a mock provider when no real AI is configured, so you can follow this chapter on a laptop
with nothing set up. The command and its result are shown for each one.*

`ai.decide` (Chapter 34) answers a question you pose: a typed verdict, weighed against a confidence
floor. The two primitives in this chapter do more than answer.

- **`ai.create`** generates a value: a summary, a welcome line, a description. You give it a source
  and some hints; it hands back generated content.
- **`ai.agent`** runs a bounded task: it may take several steps and call the tools you grant it,
  inside limits you set, and it comes back with a result or hands off when it cannot finish.

Both reach out to a live model at run time, so both plan for the model being unsure or unavailable,
the same discipline `ai.decide` follows.

---

### `ai.create` -- generate a value

The clearest form names what to generate, the source to generate it from, any free-form hints, and
an `on.failure` for a model outage. It closes with `ai.create: done`.

```mohio
report "Q3 revenue rose 12 percent on strong retail."

ai.create text as summary from report
    tone "executive"
    length "brief"
    on.failure
        give back [503] "Summary service unavailable."
ai.create: done

show summary
```

The source is any value you already hold, so a plain prompt is just a value you name first:

```mohio
hold brief "Write a friendly one-line welcome for a new player."

ai.create text as welcome from brief
    tone "warm"
    on.failure
        give back [503] "Generation unavailable."
ai.create: done

show welcome
```

Both check clean:

```
$ mio check summary.mho
  v  summary.mho  8 lines . no errors
```

#### What a developer sees with no AI provider

With no key set, `ai.create` runs against a mock provider rather than failing. The mock echoes the
request it would have sent, so you can see the source and hints reached the model correctly before
you spend anything on a real call:

```
$ mio run summary.mho
  [mock ai.create text] Generate a 'summary'. from this source: Q3 revenue rose 12 percent on
  strong retail.. with these hints: tone=executive; length=brief.
```

That placeholder is the signal that AI is not wired up yet, not an error. Turn a real provider on
with the `--ai` flag or `MOHIO_AI=1`, and confirm it with `mio ai-check` (see
`Docs/guides/FILES-AND-SERVING.md`, "AI: turning it on and checking it works"), which distinguishes
a working provider from a wrong key that quietly degrades.

#### Omitting `on.failure`

`on.failure` is not required, but leaving it out earns a warning, because a generation call depends
on a service outside your program:

```
$ mio check no-fallback.mho
  ! ai.create generates from a source via a live AI call but has no 'on.failure' fallback.
    Add an 'on.failure' block inside ai.create so a model outage is handled gracefully.
  v  no-fallback.mho  7 lines . no errors . 1 warning(s)
```

The file still builds, but treat the warning as the recommendation it is: a model outage without a
fallback surfaces to your caller as a raw error rather than a handled one.

> **A known false-positive warning, so you are not thrown by it.** The one-line form
> `ai.create NAME text prompt "..."` runs (it prints the mock placeholder), but `mio check` currently
> tags it "not executable in this build." That warning is a reachability-scan false positive tracked
> as `T1-SCAN-CHILD-NODE-FALSE-POSITIVE`: the executor is wired (`_exec_AiCreateStmt`, 22 passing
> tests), and the scan is flagging a child node its parent already consumes. The `from`-source block
> form above checks fully clean, so it is the one to reach for; the one-liner works, but its warning
> is noise until the dev lane fixes the scan.

---

### `ai.agent` -- a bounded autonomous task

An agent is given a goal and left to work toward it in steps. Because it can loop and call out to
services, it costs money and time, and an unbounded one can run away. Mohio makes the guard rail
mandatory: **an `ai.agent` without a `limits` block does not compile.**

```mohio
ai.agent researcher
    goal "Summarize the latest three support tickets."
ai.agent: done
```

```
$ mio check agent.mho
  x ai.agent is missing a limits declaration.
    Add 'max steps N' to prevent infinite loops and runaway token spend.
  x  agent.mho  3 lines . 1 warning(s) . 1 error(s)
```

That is an error, not a warning: the file will not build until you say how far the agent may go. Add
the `limits` block and a `not confident` recovery path, and it checks clean:

```mohio
ai.agent researcher
    goal "Summarize the latest three support tickets."
    limits
        max steps 5
        max tokens 2000
    limits: done
    not confident
        give back [202] "Handed to a human analyst."
ai.agent: done
```

```
$ mio check agent.mho
  v  agent.mho  9 lines . no errors
```

The meters you can set inside `limits` are `max steps`, `max tokens`, `max calls` (external calls
through granted tools), and `cost ceiling`. The counters live inside the interpreter, not in your
program's variables, so nothing the agent generates can reset one or talk its way past it. Cross a
ceiling and the agent stops and routes to its `not confident` path.

**Reading what the agent spent.** After the block, read the agent's result fields in dotted form on its name:
`researcher.cost`, and its siblings for tokens, steps and calls. The old underscored spelling
(`_agent_researcher_cost`) is RETIRED and refuses at both `mio check` and `mio run`, naming the dotted form as the
fix (verified):

```mohio
ai.agent researcher
    goal "Summarize the latest three support tickets."
    limits
        max steps 5
    limits: done
    not confident
        give back [202] "Handed to a human analyst."
ai.agent: done
show "cost: " & researcher.cost
```

> **These limits are enforced only on `ai.agent`, and only when the block is on the agent itself.**
> A `limits` block enforces nothing anywhere else, verified by running: a `limits` block written at
> journey level checks clean and then CRASHES at run with "No executor for LimitsBlock"; a `limits`
> block on `ai.create` is accepted and never applied; and `ai.agent` does not inherit a journey-level
> ceiling, so it still needs its own. The one AI-cost control that applies beyond the agent is the
> environment setting `MOHIO_AI_MAX_TOKENS`, capped in the provider call. Do not describe Mohio as
> enforcing an app-wide AI cost ceiling: it does not today. Journey and `ai.create` limits enforcement
> is tracked as `T1-AI-LIMITS-NOT-ENFORCED` and is being built.

`not confident` is warned-but-not-required, the same as on `ai.decide`: without it, `mio check` tells
you to add one so there is a defined answer when the agent cannot finish confidently.

#### What a developer sees with no AI provider

With no provider configured, the mock cannot complete the task confidently, so the agent takes its
recovery path -- which is exactly what that path is for:

```
$ mio run agent.mho
  Response  202  Handed to a human analyst.
```

So the `not confident` block is not a rare edge case you can skip; on an unconfigured machine it is
the ordinary result, and it is what proves your recovery path works before you ever pay for a call.

#### Granting tools

An agent reaches an outside service only through tools you grant it by name, using the connectors
from Chapter 40 (`mioconnect`). A call to a tool you did not grant is refused; there is no ambient
access.

```mohio
mioconnect PyPI
    address "https://pypi.org"
    operation lookup
        path "/pypi/lark/json"
        method GET
    operation: done
mioconnect: done

ai.agent researcher
    goal "Look up the package and summarize it."
    tools
        PyPI.lookup
    tools: done
    limits
        max steps 5
        max calls 3
    limits: done
    not confident
        give back [202] "Could not complete the research."
ai.agent: done
```

```
$ mio check agent-tools.mho
  v  agent-tools.mho  20 lines . no errors
```

Each granted call counts against `max calls`, so the tool grant and the limit work together: the
grant says what the agent may reach, and the limit says how often.

---

### Common mistakes

- **`ai.agent` with no `limits` block.** A compile error, not a warning. Add `limits ... limits: done`
  with at least `max steps N`.
- **Skipping `not confident`.** Warned on both `ai.agent` and `ai.decide`. On an unconfigured machine
  the agent's mock result IS the `not confident` path, so a missing one leaves the ordinary case
  undefined.
- **Skipping `on.failure` on `ai.create`.** Warned. A model outage then reaches your caller as a raw
  error instead of a handled response.
- **Reading the mock placeholder as a bug.** `[mock ai.create text] ...` and a `202` from an agent on
  a machine with no AI configured are the mock provider working as designed. Set a real provider and
  confirm with `mio ai-check`.
- **Expecting a tool the agent was not granted to be callable.** An agent reaches only the operations
  named in its `tools` block; anything else is refused.
