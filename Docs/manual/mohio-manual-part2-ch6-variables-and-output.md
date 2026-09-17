<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Part II -- Values, Types, and Text

## Chapter 6: Variables and Output

*Every construct below is verified against the current compiler: checked with
`mio check`, run with `mio run`, output read back. Money -- a currency-typed value -- is its own
chapter, coming after the currency build finishes; this chapter covers plain values only.*

---

### `show` -- output a value

```mohio
name "Bo"
show name
```
```
Bo
```

`show` is how a bare `mio run` program produces output. Give it a value, a variable, or a joined
string built with `&`.

### `name value` -- the standard variable

A standard variable is fluid: restate it to change its value, and its type can change too.

```mohio
name "Bo"
show name

name "Zed"
show name

age 15
show ("age: " & age)
```
```
Bo
Zed
age: 15
```

No `=` is needed -- `name value` is the canonical, bare form. `=` is accepted as sugar
(`age = 15` reads the same as `age 15`), but the bare form is what to write.

### `hold` -- a value that resists accidental change

```mohio
hold pin "1234"
show pin
```
```
1234
```

`hold` freezes a value until you deliberately `release` it -- restating a held value the ordinary
way is refused. Reach for it when a value should not change by accident partway through a
process; most variables never need this and stay naked.

### `lock` -- permanent

```mohio
lock pi 3.14159
show pi
```
```
3.14159
```

`lock` is stronger than `hold`: it is never meant to change again, and restating it is refused
permanently, with no `release` to undo it. Use it for a real constant.

---

**Next in Part II:** Chapter 7, Types, Casts, and the Dot; Chapter 8, Working with Text (this
manual's next chapter, already written); Chapter 9, Lists; Chapter 10, Numbers, Precision, and
Randomness. Money -- a dedicated currency type -- gets its own chapter once the currency build
this repo is currently verifying is confirmed stable; it is deliberately not covered here.
