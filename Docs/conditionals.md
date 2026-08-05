<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md. -->
# Decisions: the No-IF way

Mohio has no `if` as a block opener. That is deliberate. Nested `if/else` is where business
logic goes to hide: three levels deep and nobody can read what the code actually decides.
Mohio splits the job into three plain tools, each for a different kind of decision:

- **`check` / `when` / `otherwise`** when you are choosing between cases based on a value.
- **`unless`** when you have one action and one exception.
- **`on.failure` / `on.success` / `always`** when you are reacting to how something turned out.

You never ask "did this fail?" with an `if`. You say what to do `on.failure`. The decision
and the reaction sit right next to the thing they are about.

## check / when / otherwise

The body goes on the next line, indented. `otherwise` is the catch-all. The block closes
with `check: done`.

```
check score
    when score is more than 100
        show "Amazing!"
    when score is more than 50
        show "Nice work"
    otherwise
        show "Keep going"
check: done
```

The `when` lines are read top to bottom; the first that matches wins, so put the tightest
band first. A `score` of 60 above prints "Nice work", not "Amazing!".

### Conditions read like English

Inside `when`, write the test the way you would say it:

```
when age is more than 21
when price is less than 100
when x is between 10 and 20
when status is not "closed"
when name contains "world"
when email starts with "info@"
when notes is empty
```

These are the canonical forms and they compile with every comparison. (Symbol operators
like `>` exist for inside `( ... )` math, but in a `when` the words read better and are
preferred.)

## unless: one action, one exception

When there is a single action and a single condition that should suppress it, `unless` is
the whole sentence:

```
show "Door is locked" unless door_is_open
```

That shows the message only when `door_is_open` is false. `unless` is the negative of a
one-line `when`; reach for it instead of a two-line check when there is nothing else to
decide.

## Reacting to an outcome: on.failure / on.success / always

This is the other half of the No-IF idea. When an operation can fail, you do not run it and
then `if`-check a result. You attach the reactions to the operation itself:

```
try
    save to db.orders
    on.failure
        show "could not save"
    on.success
        show "saved"
    always
        show "done either way"
try: done
```

- **`on.failure`** runs when the operation fails (a save that could not write, a service
  that returned an error).
- **`on.success`** runs when it worked.
- **`always`** runs after either path, for the cleanup or the log that has to happen
  regardless.

The same handlers attach inline to a data read, so the not-found case lives right where the
read is:

```
retrieve member from db.members
    match id to request.member_id
    on.failure
        give back 404 "Member not found."
retrieve: done
```

You are still making a decision, "what happens if this does not work", but it reads as a
reaction next to the action, not as a separate `if` three lines later.

> **Scope of the not-found reaction.** `on.failure` on a single-row `retrieve` is the
> not-found pattern to use (verified: an absent row triggers `on.failure`). Detecting an
> **empty result from a multi-row `find`** — a `when empty` / `when found` condition on the
> result — does **not** reliably fire yet; treat that as under review.

## Why it is built this way

Every one of these passes the Walk-By Test: a person with no coding background can read
`when score is more than 100` or `on.failure give back 404` and know exactly what happens.
There are no nested pyramids, no `else if` ladders, and no gap between an operation and the
handling of its result. The structure of the decision is the structure of the sentence.

| You are... | Use |
|---|---|
| choosing between cases on a value | `check / when / otherwise` |
| doing one thing except in one case | `unless` |
| reacting to whether something worked | `on.failure` / `on.success` / `always` |
