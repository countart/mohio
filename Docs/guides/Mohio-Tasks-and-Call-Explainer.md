<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Tasks and `call` in Mohio

A **task** is a named, reusable piece of work — define it once, run it from anywhere with **`call`**. `call` and `task` are a pair: `task` makes the thing, `call` runs it. (`run` is reserved for async jobs and schedules, never for tasks.)

Every runnable example here is verified against the current compiler. Inputs are declared with `take`, capturing a task's return works when the task declares a return type, and you can pass several inputs by name — all shown below.

---

## Defining a task

```
task greet
    take name as text
    returns text
    give back ("Hello, " & name & "!")
task: done
```

- `task greet` opens it; `task: done` closes it.
- **Inputs are declared with `take`** — one `take` line per input (or several names on one line). Give a type with `as <type>` (optional); a `take` line with no `default` is **required**, and a `default` makes it optional:
  ```
  task bill
      take who as text
      take amt as int default 0
      returns text
      give back (who & " owes " & amt)
  task: done
  ```
  `take amt as int default 0` gives that input a fallback, used when the caller doesn't supply it (e.g. `call bill with "Bo"` supplies `who` and lets `amt` default to `0`). Inputs that share a type go on one line: `take a, b as int`.
- `returns text` declares what kind of value comes back (optional).
- `give back <value>` hands a result back to whoever called the task. Math and joined text need parentheses: `give back (who & " owes " & amt)`.

---

## Calling a task

**No arguments — needs the closer:**
```
call generateReport
call: done
```
> A no-argument call must include `call: done`. Writing `call generateReport` alone on a line is currently read as an *assignment* (a variable named `call` set to `generateReport`), so it silently does nothing. Always close a no-arg call.

**One argument, inline:**
```
call greet with "Aria"
```

**Several inputs — one per line, by name:**
```
call bill
    who "Bo"
    amt 50
call: done
```
Each value binds to the input of the **same name** (`who` → `who`, `amt` → `amt`), so the order doesn't matter, and any input you leave out falls back to its `default`. (Use this per-line body form for *several* values — the inline `call bill with "Bo" and 50` is only for a single value.)

> Bad arguments now fail loud, never silently: a missing required input (*"task bill requires who"*), a wrong-typed value for a typed input, an unknown input name, or a value passed to a task that takes none.

---

## Tasks and loops together

Tasks and loops compose cleanly — call a task on each pass of a loop. Collections come from a `find`/`retrieve` result (Mohio has no list literals):

```
find guests in db.guests
find: done

repeat each g in guests
    call greet with g.name
repeat: done
```

---

## Capturing the return

When a task declares a **return type** (`returns text`, `returns int`, …), you can catch its result into a variable at the call site with **`as`**:

```
call greet with "Aria" as greeting
show greeting          // Hi Aria
```

The `returns <type>` declaration is what makes this work — it turns the task's `give back` into a *value* for the caller. Without `returns`, the compiler tells you plainly:

> a task returns a value only when it declares a return type: `task greet <param> as <type> returns <type>`. Without `returns`, its `give back` is a response, not a value for the caller.

A no-argument task is captured the same way, with the block form:

```
call report as summary
call: done
show summary
```

---

## Quick reference

| You want to… | Use |
|---|---|
| Define reusable work | `task name / take inputs / returns type / … / give back result / task: done` |
| An input | `take name as text` (add `default <value>` to make it optional) |
| Return a value | `give back (<value>)` |
| Run a task, no args | `call name` (the `call name / call: done` closer form also works) |
| Run with one value | `call name with <value>` |
| Run with several values | `call name / input value / … / call: done` (order-free; omitted inputs use their `default`) |
| Catch the return into a variable | `call name with <value> as result` (task must declare `returns <type>`) |

---

*No-args note: bare `call name` runs the task — `call` leads the line and is invoked, so it is not misread as a `name value` assignment (verified by running). The `call name / call: done` closer form runs the same task and is also accepted; use whichever reads better.*
