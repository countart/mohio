# Two-Word Forms — What Works Today

**Verified 2026-08-11** by running each form against the current compiler, not from memory or a
brief. See `PARSE-COST-MEASUREMENT.md` for the full measurement this list is drawn from, and
`CLAUDE.md`'s "Dot Connector System" section for why the dot exists at all: it is a **grouping
directive**, not "a modifier attached to a verb" — it joins two or more words into one
unambiguous unit. A space leaves the grouping open, so under Earley the parser must explore every
possible grouping, and the wrong one can silently win. Two-word spellings work ONLY where the
grouping is forced some other way, regardless of the dot. Everywhere grouping is genuinely
ambiguous, two-word either hard-fails or silently misparses — see `PARSE-COST-MEASUREMENT.md` for
the measured cost and the corpus-wide blast radius.

**All other two-word forms are coming in the Rust conversion; today the dot form is canonical.**
If a form is not on this list, write it with the dot, and teach the dot form as primary.

---

## 1. `give back` — the return keyword. Two words, always has been; there is no dot form.

Not an "exception" in the sense of "the dot form also exists" — `give back` was never ambiguous
in the first place. `GIVE` and `BACK` are adjacent, fixed keywords with nothing else they could
group with.

```mohio
task greet
    take name as text
    give back ("Hi " & name)
task: done
```
Verified live, `mio run`: `Response 200  Hi Bo`.

## 2. `starts with` — inside a `where` clause only (query context, `find`/`retrieve`).

`where_condition`'s grammar has a genuine, separate two-word alternative
(`dotted_name STARTS _WITH STRING -> wc_starts_with`, alongside the dotted `starts.with`) — this
is not a misparse, it is a real, designed alternative in this one context. **Does NOT extend to
the general `condition` rule** (`while`/`if`/`unless`/`check`) — there, only the dotted
`starts.with` form exists.

```mohio
find x in db.items
    where name starts with "a"
find: done
show x.count
```
Verified live, `mio run`: `1`.

## 3. `is empty` — inside a `where` clause only (query context, `find`/`retrieve`).

Same shape as `starts with`: `where_condition` has both `dotted_name IS_EMPTY -> wc_empty`
(dotted) and `dotted_name IS EMPTY -> wc_is_empty` (two-word) as genuinely separate, working
alternatives. **Does NOT extend to `while`/`if`/`unless`/`check`** — there, `X is empty`
silently misparses as ordinary equality (`X IS empty`, reading "empty" as a bareword variable),
which is exactly the bug `T1-SPACED-MISPARSE-GUARDS` closes with a fail-loud guard in that
context specifically. The `where`-context form below is untouched by that guard.

```mohio
find x in db.items
    where name is empty
find: done
show x.count
```
Verified live, `mio run`: `1`.

## 4. `n as int` — the empty TYPED DECLARATION. This is NOT a cast.

`n as int` declares a new variable `n` with a type and no value yet (`empty_typed_decl: NAME AS
TYPE_NAME`), assigned later with `n 42`. It has always been two-word; it never had a dot form to
begin with, because it isn't converting anything — there is nothing to disambiguate.

**Do not confuse this with the cast.** The real cast is `"5" as.int` (dotted, converts an
existing value) — and the two-word spelling of THAT, `"5" as int`, hard-fails at parse time. Two
different grammar rules, two different jobs, sharing the surface words "as int" only when read
out loud.

```mohio
n as int
n 42
show n
```
Verified live, `mio run`: `42`.

Control, same session, confirming the cast genuinely does NOT have a two-word form:
```mohio
total "5" as int
show total
```
Verified live, `mio check`: `Syntax error ... No terminal matches 'i' in the current parser
context, at line 1 col 14`.

---

## Everything else

Every other two-word spelling of a dot-canonical form — `on failure`, `on success`,
`retrieve all`, `modify as`, `apply in`, and the rest of the 272-site corpus inventory in
`PARSE-COST-MEASUREMENT.md` — either hard-fails or silently misparses today. Write the dot form.
Two-word verb-modifiers return with the Rust port, which can encode real grouping rules (precedence,
lookahead) that Earley's general ambiguity-exploration cannot.
