<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Part II -- Values, Types, and Text

## Chapter 8: Working with Text (case transforms)

*This chapter covers the case-transform family only -- the stable, verified slice this manual is
thinnest on. Join (`&`), encode/decode, and the other text tools get their own pass later; see
`Docs/guides/mioql-user-guide.md` for what already runs today. Every
form below was run, not carried over from an earlier claim.*

---

### The six case transforms -- all of them work

```mohio
name "bo smith"

show (name as.uppercase)
show (name as.uc)
show (name as.lowercase)
show (name as.lc)
show (name as.title)
show (name as.sentence)
```
```
BO SMITH
BO SMITH
bo smith
bo smith
Bo Smith
Bo smith
```

`as.uppercase`/`as.uc` and `as.lowercase`/`as.lc` are pairs -- the long and short spelling of the
same transform, both canonical, pick whichever reads better in context. `as.title` capitalizes
each word; `as.sentence` capitalizes only the first letter. All six are dotted casts, so they read
as one unit -- `name as.title`, never a two-word split (see Chapter 7 for why the dot exists at
all).

### A near-miss spelling, and a stale message worth knowing about

`as.title` and `as.sentence` are what work, verified above. A reasonable guess at "title case" --
**`as.titlecase`, one compound word** -- is NOT one of the six forms above. Written as a bare
statement, it checks clean with a WARNING and fails loud at runtime, never silently:

```mohio
name "bo smith"
name as.titlecase
show name
```
```
! `type_cast_mod` parsed and validated, but is not executable in this build -- it has no
  interpreter wiring, so it would fail at run with 'no executor'.

Runtime error
  internal: the construct 'type_cast_mod' reached the value evaluator with no rule to compute
  its value, so any result would be a guess. This is a compiler gap (a grammar rule with no
  transformer method), not your code -- please report it with the line that triggered it.
```

Written the more natural way, as part of a `show` expression -- `show (name as.titlecase)` --
it does not even get that far: `mio check` refuses it outright, at parse time, naming `as.title`
as the nearest real word:

```
Syntax error
  x as.titlecase is not a Mohio word.
    The nearest Mohio word is as.title, and it is ACCEPTED BUT NOT WIRED -- it would check
    clean and stop at runtime.
```

**The one thing worth knowing: the syntax-error message itself is stale.** It currently reads
"`as.title`... is ACCEPTED BUT NOT WIRED -- it would check clean and stop at runtime" and "`as.title`
and `as.sentence`... PARSE AND DO NOTHING." That was true once; it is not true today -- both run
correctly, as the example above shows by running them. Trust the run, not this message, until it
is corrected. The message is out of date; the feature is not.

---

**Next in Part II:** the rest of Chapter 8 (join, encode/decode, extract/truncate) and Chapter 9,
Lists.
