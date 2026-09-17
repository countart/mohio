<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Part IV -- Tasks and Errors

## Chapter 14: Tasks

*Verified: checked with `mio check`, run with `mio run`, output read back. For the
no-classes mental model that `task` fits into (shape describes, task acts, the closest thing to a
method), see `Docs/guides/mohio-mental-model-no-classes.md` -- not repeated here.*

---

### `task` / `call` -- reusable logic

```mohio
task double
    take n as int
    returns int
    give back (n * 2)
task: done

call double as result
    n 5
call: done

show result
```
```
10
```

`task` declares reusable logic; `take` names its inputs. `call <name>` runs it -- pass an inline
argument (`call greet with "Aria"`) or a body of named fields, as above.

### The day-two trap: `returns` decides what `give back` means, and an uncaptured result is gone

This is the single most important thing to know about tasks, because it is silent unless you know
to look for it. Run the SAME task, called two ways:

```mohio
task double
    take n as int
    returns int
    give back (n * 2)
task: done

call double
    n 5
call: done

call double as result
    n 5
call: done

show result
```

`mio check` on this file does not stay quiet about the first call -- it warns at the exact line:

```
7 | call double
! `double` returns int, and this call does not keep the answer.
  The task runs and its result is dropped, so anything after this line decides on its own rather
  than on what `double` worked out.
  Did you mean:  call double as result   -- then read `result`?
  (A `give back` inside a task sets the task's return value; it does not answer for the handler
  around it, so an uncaptured answer is simply gone.)
```
It still runs (`result` ends up `10`, from the SECOND, captured call) -- the first call's answer
was computed and thrown away, silently, at runtime; only `mio check` tells you at the source line.
**A `give back` inside a task with `returns` declared sets a VALUE. It does not exit or answer for
whatever called it.** Capture it: `call <name> as <var>`, then read `<var>`.

### The other half of the trap: no `returns` means `give back` is a RESPONSE, not a value

```mohio
task announce
    take n as int
    give back [200] ("Got " & n)
task: done

call announce
    n 5
call: done

show "after the call"
```
```
Response  200  Got 5
```

`"after the call"` never shows. Without a declared `returns` type, `give back` inside a task
behaves the way it does everywhere else in Mohio: it is the RESPONSE, and it ends the handler it
runs in right there. The rule, stated once, both directions:

```
returns declared     ->  give back sets the task's VALUE. Capture it: call X as result.
returns NOT declared  ->  give back is a RESPONSE. It ends the call right there.
```

Decide up front which one a task is for -- a value-computing helper always declares `returns`; a
task meant to answer a request directly never does.

---

**Next in Part IV:** Chapter 15, Errors and Outcomes.
