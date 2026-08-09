<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Retired Mohio forms — quick reference

**Derived fresh from the current source (grammar + interpreter + transformer + reachability), 2026-08-01.**
Each row is a form the compiler actively FAILS LOUD on, with the canonical replacement its own message
names, and the source location of that message. Do not write any of the left-column forms — reach for
the right column. (This is the list to check when authoring, so a retired form never leaks into a first
draft. Regenerate it from source rather than trusting a copy; that is the whole point.)

## Variables & assignment

| Retired form | Use instead | Source |
|---|---|---|
| `set x = v` / `set x v` | `x v` (declare directly; `=` is optional sugar) | transformer `assignment` ~4136; grammar 2500-2510 |
| `hold x as <type> <value>` (type before value) | `hold x <value>` (the value carries its type) | transformer `retired_typed_hold` ~1110 |
| `x as int 5` (type-before-value, any decl) | `x 5` | transformer ~1025 |
| `hold NAME` + indented items (LIST block) | `create list NAME / ... / create: done`, or `NAME as list "a","b"` | transformer `hold_decl` ~1144 |
| `hold NAME` + indented `field value` (PROFILE block) | `create NAME / field value / create: done` (opt. `as sh.Shape`) | transformer `hold_decl` ~1156 |
| `[a, b, c]` list literal | `create list …` / `NAME as list …` (brackets are for field tags only) | transformer ~4530 |

## Blocks, closers & control flow

| Retired form | Use instead | Source |
|---|---|---|
| `<block>: done as NAME` (naming on a closer) | name on the ACTION: `check score as grade`, `find rows in db.t`, `call greet with x as g` | transformer ~4189 |
| block-opening `if` / `else` / `or if` | `check` / `when` / `otherwise` (and `unless`); `if` only as a trailing qualifier | interp `_exec_IfBlock` removed ~3772, ~4125; grammar 45-48 |
| `while.active` (loop form) | `loop` | interp ~3761 |
| `undo` (compensate alias) | `compensate` (warns) | grammar 1019 |
| `on.error` | `on.failure` (the operation broke) | transformer `on_error` ~1099; interp ~7880 |
| `check confidence above 0.85` inside `ai.decide` | `check <name>` on the opener | reachability ~660 |
| header task params (`task t a as int b as int`) | a `take` line in the body: `take a as int` | grammar 898 |

## Verbs

| Retired form | Use instead | Source |
|---|---|---|
| `make NAME` | `create NAME` | transformer `make_*` ~2375 |
| `run NAME` (task invocation) | `call NAME` (`run` is only for `run async` / `run mioschedule.X now`) | transformer assignment guard (`run`→"use call"); interp note ~5244 |
| `request outbound` | `miohttp.get` / `miohttp.post` (or `mioconnect`) | interp ~2440, ~7613 |

## Casts & modifiers

| Retired form | Use instead | Source |
|---|---|---|
| `case.no` | `ignore.case` | transformer ~3906 |
| `case.yes` | `match.case` | transformer ~3911 |

## Services

| Retired form | Use instead | Source |
|---|---|---|
| `ai.chain` | `ai.connect` (provider fallback chains) | interp ~10224 |
| `mioai.text` / `.image` / `.audio` (dotted generative) | `ai.create` (or `ai.decide` for reasoning) | interp ~10231; reachability ~522 |
| `miohttp.<verb>` beyond the wired set | `miohttp.get / .post / .put / .delete / .patch` | interp ~10239; reachability ~527 |

## Type names

| Retired form | Use instead | Source |
|---|---|---|
| `number` / `num` | `int` (or `integer`) for whole numbers, `dec` (or `decimal`) for fractions | reachability `_RETIRED_TYPES` ~347 |
| `string` (as a type name) | `text` | grammar/CLAUDE.md (annotation type is `text`) |

## NOT retired (common near-misses — these WORK, do not "correct" them)

- `starts with` / `ends with` as a **condition** (`when name starts with "A"`) — works; the fail-loud only fires when they misparse into a connector call, and it's a disambiguation hint pointing at the dotted `starts.with`, not a retirement (verified by running 2026-08-01).
- A value task's `give back` needs a `returns <type>` declaration to hand a value to the caller — this is a REQUIRED form, not a retirement (a task without `returns` is a procedure; its `give back` is a response).

## Anomalies found while deriving (flag, not resolved here — outside the doc's scope)

- **`as.string`:** CLAUDE.md lists it as retired → `as.text`, but NO fail-loud for it was found in the source, and a live hint (interp ~11830) actually RECOMMENDS "convert with as.string". Either the hint is stale or `as.string` is not enforcement-retired. Verify.
- **`miomail.with`:** the grammar comment at ~3213 reads "miomail.with is RETIRED — use miomail.with" (circular/typo). The real canonical mail-provider config is `miomail.with` + env keys per the backlog; the comment needs fixing.
