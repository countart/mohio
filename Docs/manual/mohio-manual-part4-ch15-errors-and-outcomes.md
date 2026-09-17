<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Part IV -- Tasks and Errors

## Chapter 15: Errors and Outcomes

*Verified: checked with `mio check`, run with `mio run`, real errors triggered against
a real database, output read back. `Docs/guides/mioql-user-guide.md`'s own try/on.failure section covers
the same construct against a real `sql` failure; this chapter's trap is the piece it doesn't cover.*

---

### `try` / `on.failure` -- catching a real failure

```mohio
connect db as sqlite from env.DATABASE_URL

try
    retrieve one from db.nobody_here
        match id to 1
    retrieve: done
    on.failure
        show "caught it -- the table does not exist yet"
try: done

show "after the try"
```
```
caught it -- the table does not exist yet
after the try
```

`on.failure` runs when something inside `try` genuinely errors -- an unreachable database, a
rejected write, a table that does not exist. It does NOT run for an ordinary empty result:
`retrieve`/`find` finding nothing is a normal, successful outcome, checked with `is empty`, never
a failure (see `Docs/guides/mioql-user-guide.md`).

### The day-two trap: a bare `try` with no `on.failure` catches nothing

Same program, `on.failure` removed:

```mohio
connect db as sqlite from env.DATABASE_URL

try
    retrieve one from db.nobody_here
        match id to 1
    retrieve: done
try: done

show "after the try"
```

`mio check` warns at the `try` line itself:

```
3 | try
! This `try` has no `on.failure`, so it does not catch anything -- an error inside it still
  leaves the block and stops the program.
```

Run it against a real database with no such table, and the warning is exactly right -- the error
walks straight out as a raw response, `"after the try"` never prints:

```
Response  500  db_error: no such table: nobody_here
  The database rejected the operation. Check the table and field names, and that the
  connection is configured.
```

`try` on its own is a scope, not a guard. Someone arriving from a language where `try` itself is
the protection will write exactly this and get no error, no warning at check time they notice, and
a program that looks like it handles failure but does not. A block inside the `try` may carry its
own `on.failure` instead of one at the top level, and that protects just as well -- what matters is
that SOMETHING inside the `try` actually catches, not that the word `try` is present.

---

**Next in Part IV:** end of Part IV. Part V picks up Shapes, Forms, and Validation.
