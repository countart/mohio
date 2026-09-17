<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Part III -- Control Flow

## Chapter 11: Decisions

*Verified: checked with `mio check`, run with `mio run`, output read back.*

---

### `check` / `when` / `otherwise`

```mohio
score 72

check score
    when score is more than 90
        show "A"
    when score is more than 70
        show "B"
    otherwise
        show "C or below"
check: done
```
```
B
```

`check` reads its subject once, top to bottom; each `when` is tried in order, and the first one
that matches runs. `otherwise` is the fallback when nothing else matched. `is more than` /
`is less than` / `is above` / `is below` are the spoken comparison forms; `<`/`>`/`<=`/`>=` also
work, always inside `(...)`. There is one gap worth knowing: `>=` and `<=` have no spoken
form, so those two must be written as symbols inside `(...)`.

### `unless` -- a trailing guard, not a block

`unless` never opens a block; it attaches to the END of the single statement it guards, the
opposite order from `if`/`unless` in some other languages:

```mohio
age 15
show "not an adult yet" unless age is more than 18
```
```
not an adult yet
```

Trying to write `unless` as a block opener (`unless age is more than 18` / a body / `unless:
done`) is refused at check time, naming the correct trailing form. For anything beyond one guarded
statement, use `check`/`when`/`otherwise` instead.

---

**Next in Part III:** Chapter 12, Loops.
