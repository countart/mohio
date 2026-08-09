<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Loops in Mohio

A loop repeats a piece of work. Mohio has four loop shapes, each named for the thing that controls it, plus a flexible `stop` for breaking out. Every example here is verified against the current compiler.

---

## The four loops

| You have… | Use | Controlled by |
|---|---|---|
| a count | `repeat N times` | the number |
| a condition | `while <condition>` | the condition |
| a collection | `each X in list` | the list |
| none of those — just keep going | `loop` | a `stop` inside |

### 1. `repeat N times` — a fixed number of times

```
repeat 3 times
    show "hello"
repeat: done
```

Prints `hello` three times. The count can be any number or expression.

### 2. `while <condition>` — for as long as something is true

```
n = 0
while n < 3
    n = (n + 1)
while: done
show n          // 3
```

Runs while `n < 3`; ends the moment that turns false.

### 3. `each item in list` — once for every item

```
names = [ "Aria", "Bo", "Cy" ]
each name in names
    show name
each: done
```

The loop variable (`name`) holds one item per pass.

### 4. `loop` — open-ended, until you say stop

Use it when nothing external decides the count — a game loop, reading until input ends, polling until a signal. It runs until a `stop` inside it fires.

```
i = 0
loop
    i = (i + 1)
    stop when i > 4
loop: done
show i          // 5
```

A `loop` is protected: if it never reaches a `stop`, it fails loud at the iteration limit instead of hanging or crashing the program.

---

## Breaking out: `stop`

`stop` ends a loop. It comes in four shapes, from simplest to most precise.

**Plain `stop`** — break the loop you're in, right now:

```
repeat 10 times
    show "once"
    stop
repeat: done            // runs one pass, then stops
```

**`stop when <condition>`** — break only when the condition is true. This is how most real loops end:

```
n = 0
repeat 100 times
    n = (n + 1)
    stop when n > 2
repeat: done
show n                  // 3
```

`stop when` works in *every* loop — `repeat`, `while`, `each`, and `loop`.

**`stop <name>`** — in nested loops, break a specific *named* loop from inside an inner one. Only `loop` can be named:

```
loop outer
    each row in rows
        stop outer          // breaks the outer loop, not just the each
    each: done
loop: done
```

**`stop <name> when <condition>`** — both at once: break the named loop, but only when the condition holds:

```
total = 0
loop outer
    each x in [ 1, 2, 3 ]
        total = (total + x)
        stop outer when total > 5
    each: done
loop: done
show total              // 6
```

This is what keeps nested loops clean — you break exactly the loop you mean, exactly when you mean to, without tangled flags.

---

## Runaway protection — the iteration cap

Every counting loop is protected against running forever. If a loop spins past a safety limit — a `stop` that's never reached, a `while` condition that never turns false, or an absurd `repeat` count — Mohio halts it and **fails loud** with a clear `loop_limit_exceeded` error and a hint, instead of freezing or crashing the program.

- The default limit is **100,000 iterations**.
- Change it with the `MOHIO_MAX_LOOP_ITERATIONS` environment variable — raise it for genuinely long jobs, lower it while debugging.
- It applies to `repeat` (the count is checked *before* the loop even starts), `while`, and `loop`.
- `each` doesn't need it — it's naturally bounded by the length of the list.

This is what makes an open-ended `loop` safe to write: the worst case is a clear error pointing you at the missing `stop`, never a hung server.

```
loop
    show "working"
loop: done
// if nothing inside ever triggers a stop:
//   loop_limit_exceeded: loop exceeded 100000 iterations -- it never reached a 'stop'.
```

---

## Accumulating a result across passes

The everyday loop job: build up a total, count, or string. Set a variable **before** the loop, update it **inside**.

```
total = 0
nums = [ 10, 20, 30 ]
each n in nums
    total = (total + n)
each: done
show total          // 60
```

### Two rules that make accumulators work

**Math needs parentheses.** Write `total = (total + n)`, not `total = total + n`.

**Use `=`, not `hold`, for anything you'll change.** `hold` freezes a value until you `release` it — right for something you want guarded, wrong for an accumulator. `lock` is the one that can never change again. A plain assignment (`total = 0`) creates a value you can update.

### Variables you change in a loop stick around

An assignment inside a loop updates the variable in the surrounding code — that's why `total` still holds 60 after the loop ends. There's no separate "loop scope" that throws your changes away.

---

## A note on `while.active`

`while.active` has been retired in favor of `loop` — `loop … loop: done` is clearer and is protected against runaway iteration. If you have old `while.active` code, change it to `loop`.

## Skipping a pass: `skip`

`skip` jumps to the next pass without finishing the current one — handy for ignoring items you don't care about. Like `stop`, it's most useful with a condition:

```
total = 0
each x in [ 1, 2, 3, 4 ]
    skip when x < 3
    total = (total + x)
each: done
show total          // 7 — 1 and 2 were skipped, 3 + 4 added
```

`skip when <condition>` skips the rest of the current pass only when the condition is true. A plain `skip` (no condition) skips every time. `skip` works in `repeat`, `while`, `each`, and `loop`.

---

## Quick reference

| You want to… | Use |
|---|---|
| Repeat a fixed number of times | `repeat N times / … / repeat: done` |
| Loop while a condition holds | `while <condition> / … / while: done` |
| Do something for every item | `each item in list / … / each: done` |
| Keep going until told to stop | `loop / … / loop: done` |
| Name a loop (for nested breaks) | `loop outer / … / loop: done` |
| Break the current loop | `stop` |
| Break when a condition is true | `stop when <condition>` |
| Break a specific named loop | `stop outer` |
| Break a named loop on a condition | `stop outer when <condition>` |
| Skip the rest of the current pass | `skip` |
| Skip a pass when a condition is true | `skip when <condition>` |
| Build up a total/count/text | `hold x 0` before the loop, `x = (x + …)` inside |
| A value that never changes | `lock name = value` |
| A value you'll update | `name = value` |
