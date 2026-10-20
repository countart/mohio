<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Why Mohio refuses things

*Every refusal quoted below is real output, captured by running `mio check`, `mio run` or `mio serve`, not
invented. If you have hit two or three refusals in an hour and started to think the language is fussy, read this
once. The refusals are not fussiness; they are the one habit that makes a Mohio program trustworthy.*

## The one principle: refuse rather than guess

Mohio refuses what it cannot do correctly, rather than guessing and producing an answer that looks right. In an
ordinary script a wrong-but-plausible result is an annoyance. In a regulated application it is the WORST possible
outcome: a payment that looked approved, an export that looked masked, a rule that looked enforced, with nothing
on screen and nothing in a diff to show it was wrong. A crash is recoverable and a refusal is cheap; a silent
wrong answer is the one failure you find out about from someone else, later, in production.

So when Mohio meets something it would have to guess about, it stops and says so. That is the trade the language
makes on your behalf: a little friction now, in exchange for never shipping a confident wrong answer.

A real example of the outcome this exists to prevent, found and fixed in 2026-09: `export as.xlsx` used to check
clean, run clean, and write CSV bytes into the `.xlsx` file. A grammar literal was dropped before it reached the
executor, so the export quietly fell through to the CSV writer, and a developer opened a file named `.xlsx` full
of comma-separated text. Nothing errored; the artifact was simply wrong. That is exactly the failure a refusal
prevents: `export as.xlsx` now fails loud (and refuses at check) rather than producing a mislabelled file. A
missing refusal is bad; a wrong artifact produced in silence is worse, and it is the case that most justifies the
friction of the refusals below.

## Three kinds of refusal, and telling them apart

A developer who reads every refusal as "the language said no" learns nothing from them. There are THREE kinds,
and they mean three different things you should do.

### 1. "You wrote it wrong" -- fix it

The form is a mistake. Fix the line and move on. A predicate written before its subject:

```
# check n / when is even n / ...
x `is even n` puts the predicate before its subject, so `check`/`when` reads `is` as the value to test
  and this branch never matches.
```

An inline view marker where the multiline form is required:

```
# render: <ul>{{ each c in colors }}<li>{{ c }}</li>{{ end }}</ul>
x {{ each c in colors }} is not a valid view loop. A view loop stands alone on its line as
  `{{ each NAME in SOURCE }}`, closed by `{{ each: done }}` on its own line, with the row markup between.
```

A word that is not a verb, which would otherwise become a variable without your noticing:

```
# read "hello"
x `read` is not a Mohio verb; written on its own it silently becomes a variable named `read`.
```

That last one is the principle in miniature: `read` as a bare word would have quietly become a variable and done
nothing, so Mohio refuses it instead of letting the silence stand.

The mirror of it is a word that IS a verb, written as `VERB value`, which would otherwise become a variable named
after the verb and do nothing:

```
# stop 5
x Line 1: `stop` is an action verb. It is not a variable name.
    This line declared a VARIABLE called `stop`, so the `stop` you wrote never happens -- and nothing would have told you.
    The form is:  stop (on its own, or stop when <condition>)
    To keep a value, give it a name that says what it holds.
```

Written that way the verb never ran: `stop 5` stopped nothing, `add 5` added nothing, `check 5` checked nothing,
`send email to ...` sent nothing. Mohio refuses each at check and names the real form. A word that is not a verb
(`log hello`, `x 50`) still assigns as before, and a field named after a verb inside a block is untouched.

A comparison between two different KINDS of value, which another language would answer by silently coercing one
side:

```
# when balance is more than true
x Line 3: `balance` is a number and true is a true-or-false, so the comparison has no answer, and at run it would refuse. Use a value of the same kind.
    Compare it with a number; values of different kinds never compare.
```

That is the principle again: `5 above true` would once have coerced `true` to a number and answered yes, and
`true plus 5` would have given `6`. Both now refuse rather than guess a shared meaning, on every surface: a
comparison, a `where` or `match` filter, and arithmetic. What still works is defined, not guessed: two numbers
written as text still meet a number, and a true-or-false still answers against a number at run.

### 2. "This is declared and not built yet" -- wait on it, do not work around it

The form is real Mohio the grammar knows, but the runtime does not implement it yet. This is a DEFERRAL, not a
mistake: you did not write it wrong, the feature is not there. An unbuilt clause on `retrieve`:

```
# retrieve r from db.people / match id to 1 / export as.csv to "out.csv"
x retrieve: `export` is declared but not yet built on `retrieve` -- retrieve applies only `match`, so it would
  fetch the rows and hand them back without applying `export`, and a filter or export silently dropped from a
  read is wrong data. `find` is the collection verb that applies these clauses: rename `retrieve` to `find`.
  For a single-row export, use `find` with a `match` and `up to 1`.
```

The message says "declared but not yet built" precisely so you can tell it apart from a mistake, and it also
NAMES THE VERB THAT DOES THE JOB (`find`) and the one-row form, so the refusal is help rather than a wall. It
would have been easy to run the query and drop the clause silently; the whole point is that it refuses rather
than returning rows that look exported and are not.

### 3. "This belongs on a different verb or construct" -- use the right one

The thing you asked for is real and built, just not HERE. The `retrieve` refusal above is also one of these:
`retrieve` returns one record and `find` is the collection verb that exports, so the answer is usually to reach
for `find`. A write attempted on a page served by convention (a GET):

```
x a convention-served page is read-only; move state changes into a listen-for handler. `/` answers GET `/`
  by being run, and it changes state: `save` (line 2). A GET is safe and repeatable -- a browser, a link
  preview or a crawler may fetch this page any number of times, and each fetch would run that again. Move
  the state change into a `listen for ... request: done` handler and leave the page to render what it reads.
```

Nothing is broken here; the write simply belongs in a handler, not on a page that a crawler might fetch a hundred
times.

## A refusal names the fix

Every refusal above tells you what to do next, not just that something is wrong. That is a standing rule in
Mohio: a message carries the line, what is wrong in plain words, why it matters, and the fix. You should be able
to act on a refusal without opening the documentation. The `retrieve` refusal above is the rule at its best: it
does not just say no, it names the verb that does the job (`find`), tells you to rename `retrieve` to `find`, and
gives the one-row form (`find` with a `match` and `up to 1`). A refusal that named a problem without naming the
fix would be half a message; the standing rule is that it names both.

## A refusal is sometimes the language pointing at a better design

The most useful refusals are the ones that stop you building the wrong thing. `send` and `request` take a
compile-time destination, not a computed URL. An application lane converting a payment flow met that constraint
with two typed routes, one per currency, and could have weakened them into one dynamic route to satisfy the
tool. It did the opposite: it kept the typed routes and the server contract intact, and its own developer agreed
that the result was the better architecture, unprompted. The constraint did not force a workaround; it pointed at
the stronger design. A subfolder that tries to loosen a parent's compliance sector meets the same kind of
refusal:

```
Error  The journey at app/loosen/journey.mho sets sector `healthcare`, which would LOOSEN the sector
`financial` a parent journey already requires. A subfolder journey may only TIGHTEN security, never
relax it ... but it cannot drop it or switch to a sector that does not include it.
```

That is not the language getting in the way. It is the language refusing to let one folder quietly step outside
the rules the application declared.

## The worst failure is a stated protection that does not exist

Every refusal above is the language telling the truth: it stops, and it says why. There is a failure one class
worse than any of them, and `unique` was it. A field declared `unique` on a shape that governs no table enforced
NOTHING: two writes of the same value both succeeded, two duplicate rows, no index. That alone is an absent
protection, the same kind as an unlogged read or an unencrypted field. What made it worse is that `mio check`
printed that the constraint WAS enforced, once per declaration, twenty-five times in one demo.

That is the difference that matters. The export leak and the upload gap this week were protections that were
ABSENT. This was a protection that was STATED and did not exist, and the statement was in check output, which is
the exact surface a developer points at to say Mohio tells the truth about what a program does. A false refusal
wastes an afternoon; a false assurance ships a duplicate payment under retry and reports success. An absent
protection is a gap; a falsely-stated one poisons the instrument you use to find gaps.

The sweep for siblings was the reassuring half: `unique` was alone. Tags still encrypt, `never store` still
refuses, and the boundary rules still enforce. It was singular for a structural reason, which is worth keeping:
`unique` is the only modifier that needs a TABLE to live on, so it was the only one that could be declared in a
place with nowhere to take effect and no write yet to notice.

The ruling was to build the enforcement, not to fix the message, and the instance is now CLOSED: this is the
class, not a live bug in the language. A `unique` field takes effect from the WRITE INTENT, so Mohio resolves the
table the shape is saved to and lands a real database index there, identical to a `<table> as table` block, and a
duplicate is refused on every write verb. A shape saved to a table chosen at runtime has nowhere to put the index
and is refused at check and at boot, naming the field; a shape that is never written is named inert once, rather
than silently doing nothing. An honest message alone was declined as a workaround for something that could be
closed the same day: the point of the check surface is that it states what is true, so the fix was to make the
statement true, not to soften it. The section stays because the class outlives the instance, and a stated
protection that does not exist is the failure to watch for anywhere it could recur.

## Careful, not fussy

Put together, the refusals say one thing: Mohio would rather stop and tell you than guess and be wrong. A
developer who has read this page hits their next refusal and reads it as the language being careful with their
program, which is what it is. Read the message, tell which of the three kinds it is, do what it names, and the
friction turns into the thing you were promised: a program that does not lie to you about what it did.
