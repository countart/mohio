<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Part III -- Control Flow

## Chapter 12: Loops

*Verified: checked with `mio check`, run with `mio run`, output read back.*

---

### `repeat N times` -- a fixed count

```mohio
repeat 3 times
    show "hi"
repeat: done
```
```
hi
hi
hi
```

### `repeat each` -- walk a collection

```mohio
colors as list "red", "green", "blue"
repeat each c in colors
    show c
repeat: done
```
```
red
green
blue
```

`repeat each` is the canonical, verb-first spelling. A bare `each x in ...` (no `repeat`) also
runs, identically -- non-canonical but not an error, `mio check` reports no warning for it and
`mio fmt` does not rewrite it. Write `repeat each`; do not be surprised if you see bare `each` in
older code.

### `loop` / `stop` -- open-ended, until you break out

```mohio
loop
    show "once"
    stop
loop: done
```
```
once
```

`loop` runs until something inside it calls `stop`. Without a `stop` reachable, it runs until the
runtime's own guard (`MOHIO_MAX_LOOP_ITERATIONS`, 100k by default) refuses it -- the guard exists
precisely so a forgotten `stop` fails loud instead of hanging.

`while` (a condition checked before each pass) also works; it is not covered in this
chapter.

---

**Next in Part III:** Chapter 13, More Control Flow (`skip`, `then`).
