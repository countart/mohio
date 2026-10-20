<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Part IV -- Tasks and Errors

## Chapter 14: Tasks

*Verified by running. The complete programs below were checked with `mio check` and run with `mio run`, output
read back; the cross-file behavior (the cascade and nearest-wins) was verified with `mio serve` and real HTTP
requests, and the results are quoted in the text. A few fences are FRAGMENTS that show one call form or one
page of a multi-page pattern; each fragment is a piece of a whole that was run (for example the payment
before/after uses request context that only runs inside a served app). For the no-classes mental model that
`task` fits into (shape describes, task acts), see `Docs/guides/mohio-mental-model-no-classes.md`.*

---

### What a task is

A task is a named piece of work that other code can call. You declare it once, and any code that can see it
calls it by name, optionally passing values in and optionally reading a value back. That is the whole idea:
write the logic once, call it from wherever you need it.

A task is NOT a page, NOT a route, and NOT a handler. A page is a file that serves itself; a route
(`request for ...`, `listen for sh.X ...`) answers an incoming HTTP request; a handler is that request-answering
block. A task answers nobody on its own. It runs only when something calls it, and it hands its answer back to
that caller. If you find the same logic copied across two pages, that logic wants to be a task.

This chapter exists because two application lanes built substantial applications without using tasks at all,
copying logic across pages instead. Nobody complained, which is what happens when people do not know a feature
exists rather than finding it awkward. The most useful part of the chapter is "Where a task can live," below,
because that is the part that turns a task from a per-file convenience into shared application logic.

---

### Declaring a task, and calling one

Declare with `task NAME`, name the inputs with `take`, name the output type with `returns`, and hand the
answer back with `give back`. Close with `task: done`.

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

`take` names the inputs; types are optional, and `default` makes an input optional:

```mohio
task greet
    take name as text default "friend"
    returns text
    give back ("Hi " & name)
task: done

call greet as g
call: done
show g
```
```
Hi friend
```

Several inputs join with `and`: `take x as int and y as int`. There are two call forms, and both were
verified:

- The **block form** names each argument on its own line and closes with `call: done`:
  ```mohio
  call add as s
      x 3
      y 4
  call: done
  ```
- The **inline form** passes one value with `with` and takes NO closer:
  ```mohio
  call greet with "Aria" as a
  ```

Either way, `as NAME` is how the caller keeps the answer: after the call, read `NAME`. Leave the `as` off and
the answer is dropped (see the trap below).

**Ordering.** Inside a handler or route, a task is available no matter where in the file it is declared, because
every top-level declaration loads before any request is answered (verified: a route called a task declared in
another file entirely). In a plain script run top to bottom with `mio run`, declare the task before the line
that calls it; a call above the declaration leaves the captured variable unset, and `mio check` does not catch
that one, only the run does.

---

### The day-two trap: `returns` decides what `give back` means

This is the single most important thing to know about a task, because it is silent unless you know to look.
When `returns` is declared, `give back` sets the task's VALUE, and an UNCAPTURED call throws that value away.
`mio check` warns at the exact line:

```
! `double` returns int, and this call does not keep the answer.
  The task runs and its result is dropped, so anything after this line decides on its own rather
  than on what `double` worked out.
  Did you mean:  call double as result   -- then read `result`?
```

The other half: with NO `returns`, `give back` is a RESPONSE, and it ends the handler right there, exactly as
`give back` does everywhere else in Mohio.

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

`"after the call"` never shows. The rule, both directions:

```
returns declared      ->  give back sets the task's VALUE. Capture it: call X as result.
returns NOT declared  ->  give back is a RESPONSE. It ends the call right there.
```

Decide up front which a task is for: a value-computing helper always declares `returns`; a task meant to
answer a request directly never does.

---

### Where a task can live

This is the part nobody knew, and the reason for the chapter. A task can be declared in four places, and each
was verified by running. The rule for all four is the same as the journey cascade: a task is visible to the
code below and inside where it is declared.

1. **In the page that calls it.** The task is FILE-LOCAL: only that file can call it. A sibling page in the
   same folder cannot (verified: a page calling a task declared in a neighbour file was refused at check with
   "No task named `only_in_a` is declared"). This is the right home for logic one page needs and no other.

2. **In a journey.** A task in a folder's `journey.mho` is callable by every page in that folder.

3. **In a journey ABOVE.** Since Batch 36 made a root `journey.mho` cascade to pages in subfolders, a task
   declared at the root is callable from ANY page in the application, however deep. This is how shared
   application logic is done: one task at the root, called everywhere.

4. **In an include.** A `_helpers.mho` file that declares a task, pulled in with `include "_helpers.mho"`,
   makes that task callable by the file that included it (verified: `call greet` after the include returned
   `Hi Aria`).

Here is a worked tree. Read it the way you read the journey chapter's cascade tree: find a page, look up and
out, and every task at or above it is reachable.

```
app/
  journey.mho          task fmt_money      <- reachable from EVERY page below
  index.mho            (/)                 can call fmt_money
  _helpers.mho         task slugify        <- only files that `include "_helpers.mho"`
  admin/
    journey.mho        task audit_line     <- reachable from admin/* pages, not from /
    panel.mho          (/admin/panel)      can call fmt_money AND audit_line
    reports/
      monthly.mho      (/admin/reports/monthly)  can call fmt_money AND audit_line
```

Verified against a running server: with `task wrap` in the root `journey.mho`, `GET /` (the same-folder
`index.mho`) and `GET /admin/panel` (a subfolder page) BOTH called it and returned its result, threading the
caller's argument through. So the root task really does reach a subfolder page, which is the newest and most
useful of the four.

---

### When two tasks share a name: nearest wins

Because a task is visible from where it is declared and downward, two levels can declare the same name. The
rule is the cascade's rule, NEAREST WINS, and it was verified end to end against a running server with a task
named `label` at three levels:

```
call from /            -> the ROOT journey's label      ("root")
call from /admin/panel -> the ADMIN journey's label      ("admin-journey")   nearer journey beats root
call from /admin/own   -> that PAGE's own label          ("page")            page beats every journey
```

So a page's own task overrides a journey's, and a nearer journey overrides the root. You override shared logic
for one page or one folder by declaring a same-named task closer to it, and everything else keeps the shared
one. Nothing is silently merged; the nearest declaration is the one that runs.

---

### What a task can and cannot do

Verified by running:

- **Write.** A task can `save`, `update`, `remove`, all the write verbs. A task that adds a row and a second
  task that counts rows worked, and the count was read back from the database.
- **Query.** A task can `retrieve`, `find`, `grab`. The counting task above read `rows.count` from a
  `retrieve.all`.
- **Call another task.** A task can call a task, including deeper chains, and a task can call itself:
  `countdown(3)` returned `3 2 1 done` by recursion.
- **Fail, and be handled INSIDE.** A write, connector or AI call inside a task can fail, and you handle it with
  `on.failure` INSIDE the task, where the typed failure value from Batch 38 (`failure.kind`,
  `failure.message`, `failure.status`) is available, the same as in any `on.failure`.

The one thing a caller CANNOT do is put `on.failure` on the `call` itself, and the refusal now says so as a rule
(verified this pass):

```
x a `call` cannot carry `on.failure` -- a task owns what failure means for it, so failure is handled INSIDE
  the task and the caller reads the outcome (ruled 2026-09-26, deliberate, not a missing feature). Handle it in
  the task, `give back` a value that says what happened, and branch on it at the call.
```

A task therefore handles its own failure and gives back an outcome the caller reads. In practice: catch the
failure inside the task, `give back` a value that says what happened, and let the caller branch on that value.

```mohio
task safe_add
    returns text
    save to db.widgets
        name "x"
        on.failure
            give back ("failed: " & failure.message)
    save: done
    give back "ok"
task: done
```

---

### Why you would use one: shared logic across pages

Here is the case that made this chapter necessary. An application lane converting a payment flow had the SAME
validation in several places. Copied into each page, it drifts: one copy gets a fix the others do not, and now
two pages disagree about what a valid payment is.

Before, the same check pasted into two pages:

```mohio
// pay-usd.mho
listen for sh.Payment at /pay/usd
    check Payment.amount
        when (Payment.amount <= 0)
            give back [400] "Amount must be positive"
    check: done
    // ... the rest of the USD flow
listen: done

// pay-bhd.mho -- the SAME check, pasted again
listen for sh.Payment at /pay/bhd
    check Payment.amount
        when (Payment.amount <= 0)
            give back [400] "Amount must be positive"
    check: done
    // ... the rest of the BHD flow
listen: done
```

After, one task in the journey, called by both:

```mohio
// journey.mho -- declared once, reachable by every page in the app
task amount_ok
    take amount as int
    returns boolean
    give back (amount > 0)
task: done

// pay-usd.mho
listen for sh.Payment at /pay/usd
    call amount_ok with Payment.amount as ok
    check ok
        when ok is false
            give back [400] "Amount must be positive"
    check: done
    // ... the rest of the USD flow
listen: done
```

`pay-bhd.mho` calls the same `amount_ok`. Now the rule lives in one place: fix it once, both pages get the fix,
and they cannot drift apart. That is the whole return on knowing tasks exist.

---

### What is not built

Established by running, not read from the backlog:

- **`on.failure` on a `call`.** The call site does not accept it, and that is a DELIBERATE rule (ruled
  2026-09-26), not a missing feature: failure is managed IN the task. Handle it inside the task with
  `on.failure`, `give back` an outcome, and branch on that value at the call. The refusal states this rule
  directly (see the quoted message above); it no longer reads as a bare closer-mismatch syntax error.
- **Sharing a page's task with a sibling page.** A page task is file-local by design. To share, move it up into
  a journey or an include; there is no cross-page-in-the-same-folder visibility without one of those.

Both of these are the design working as intended rather than gaps: failure is owned by the task, and sharing is
done by placing the task where it can be seen, not by reaching across files.

---

**Next in Part IV:** Chapter 15, Errors and Outcomes.
