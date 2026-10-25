<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Casts and Coercion in Mohio

Turning one kind of value into another — a string into a number, a number into
text, a stored timestamp into "days ago" — is a **cast**. Mohio writes casts as a
**dotted modifier** that trails the value: `value as.int`. Every example here is
verified against the current compiler.

---

## Where the name goes: the two orders of `as`

`as` reads two ways, told apart by what it is doing:

- A DECLARATION names FIRST: a shape field or a `take` is `NAME as TYPE` -- `name as text`,
  `amount as USD`, `take qty as int`. The name you are declaring comes first, its type second.
- An ALIAS, CAST or RESULT binds the name LAST: the thing comes first and `as NAME` (or the dotted
  `as.KIND`) trails it -- a cast `("5") as.int`, a captured result `call total as sum` or
  `Service.pay as receipt`, a changed record `update ... as changed`, and a produced kind
  `ai.create image as pic` (the kind first, the name last). Here `as` binds a name to the thing on its
  left.

Rule of thumb: when you are DECLARING what a value is, the name leads (`name as text`); when you are
NAMING the result of something, the name trails (`... as result`).

---

## The cast is dotted: `as.int`, not `as int`

The canonical cast form is the **dotted modifier**:

```
result   ("5") as.int          // 5
amount   (price) as.decimal    // 19.99
label    (count) as.text       // "42"
```

> **`as int` with a space is not a cast.** For now the space form has no grammar
> rule — it silently mis-parses (the cast quietly vanishes, the value stays a
> string, and the next bit of math crashes). The compiler now catches this and
> **fails loud** at `mio check` with a "use `as.int`" hint, so it can never run
> wrong. Both forms (`as.int` and `as int`) will be accepted after the Rust
> rewrite; until then, **always use the dotted modifier.**

The space form `NAME as type` is still valid in its *declaration* slots — shape
fields (`triage_level as integer`), assignment targets (`x as int = "5"`), and
task parameters (`amt as int default 0`). Those are type *declarations*, not
casts, and are untouched.

---

## `as.int` — string to whole number

`as.int` turns a value into a whole number. It is deliberately forgiving about
the messy values a database hands back, and strict about genuine garbage.

| You give it | You get | Why |
|---|---|---|
| `none` / `null` (SQL NULL → None) | `0` | missing → zero |
| `""` (empty string) | `0` | empty → zero |
| `"none"`, `"null"`, `"n/a"`, `"-"` | `0` | null-ish text → zero |
| `"0"` | `0` | numeric string |
| `"5"` | `5` | numeric string |
| `"564.987"` | `565` | rounds to the nearest whole number |
| `"  7 "` | `7` | surrounding whitespace ignored |
| `"abc"` | **fails loud** | genuine garbage is never silently `0` |

The rounding is on purpose: `as.int` rounds to nearest (`564.987 → 565`). For an
explicit direction use `round.up` / `round.down`; to keep the fraction use
`as.number` or `as.decimal`.

Because `as.int` already maps null / None / empty / null-ish text to `0`, you do
**not** need a `default "0"` in front of it for integer work — the cast covers
those cases on its own.

---

## The safe integer-from-database pattern

Numeric fields come back from the database as **strings or `None`**, never as
integers. Cast before you do math:

```
// reading a stored counter, then incrementing it
moves_int   moves as.int          // none/""/"0"/"7" all handled -> 0,0,0,7
moves       ((moves_int as.int) + 1)
```

The cast belongs **inside its own parentheses**, on its own value, before the
math:

```
score   ((score as.int) + (puzzle_score as.int))    // both cast, then added
```

Do not write the cast loose inside a math expression (`(score + 1 as.int)`) — cast
each value first, in its own parens, then combine.

---

## The other casts

The same dotted modifier covers the rest of the family. All are verified.

| Cast | Turns value into | Example |
|---|---|---|
| `as.int` | nearest whole number | `("564.987") as.int` → `565` |
| `as.number` / `as.num` | number, fraction kept | `("564.987") as.number` → `564.987` |
| `as.decimal` / `as.dec` | decimal (optional places: `as.decimal.2`) | `(price) as.decimal.2` |
| `as.text` | text | `(42) as.text` → `"42"` |
| `as.boolean` / `as.bool` | true / false | `(flag) as.boolean` |
| `as.uc` / `as.lc` / `as.title` / `as.sentence` | cased text | `(name) as.title` |
| `as.absolute` | magnitude (drops sign) | `(-5) as.absolute` → `5` |
| `as.days` / `as.hours` / `as.minutes` / `as.seconds` / `as.weeks` | elapsed time from an ISO datetime | `(created_at) as.days` |
| `as.json` | JSON text | `(data) as.json` |

`as.csv`, `as.pdf`, and `as.html` are reserved and **fail loud** ("not yet
implemented") rather than pretending to work — serialize in application logic for
now.

An unknown cast (`as.frob`) also fails loud. Mohio never silently ignores a cast.

---

## Rounding, separately

When you only want to round (no type change), use the `round.*` modifiers:

```
total   (price) round.up        // to a whole number, AWAY from zero
total   (price) round.down      // to a whole number, TOWARD zero
total   (price) round.up 2      // to 2 places, away from zero
total   (price) round.down 2    // to 2 places, toward zero
total   (price) round.to 2      // to 2 places, half away from zero
```

`round.up` and `round.down` take an OPTIONAL number of places: bare, they round to a whole number;
with `N`, to N places. `round.up` goes away from zero and `round.down` goes toward zero, so on a
negative they are the opposite of ceiling and floor (`-2.3 round.up` is `-3`, `-2.7 round.down` is
`-2`; verified by running). `round.to N` rounds to N places, ties going half away from zero. Any
other rounding spelling refuses, naming the accepted forms.

---

## Money is not plain decimal

`as.decimal` and `as decimal` keep whatever precision you give them and apply no rounding and no
currency, which is right for a measurement or a rate but wrong for money. For money, declare a
currency type (`amount as USD`): it holds exactly the currency's decimal places, rounds half-up, and
carries the currency so cross-currency math is caught. See the money chapter,
`Docs/manual/mohio-manual-part2-money.md`, "Over-precision, and `as decimal` versus a currency type".

---

## Quick reference

| You want to… | Write |
|---|---|
| String → whole number (DB-safe) | `(field) as.int` |
| Increment a stored counter | `count ((count as.int) + 1)` |
| Keep the fraction | `(field) as.number` |
| Fixed decimal places | `(field) as.decimal.2` |
| Number → text | `(value) as.text` |
| Round only (no type change) | `(value) round.up [N]` / `round.down [N]` / `round.to N` |
| Declare a field/param type | `name as integer` *(declaration, not a cast)* |

**One rule to remember:** the cast is the **dotted** modifier — `as.int`. The
space form `as int` in cast position is reserved for the Rust rewrite and fails
loud until then.
