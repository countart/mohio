<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Mohio Language Reference — Pioneer Edition

Welcome to Mohio. This is everything you need to start writing real programs: the words,
the syntax, the rules, and working examples you can copy. Mohio's whole idea is in one line:

> **Write intent, execute reason, see everything.**

The test every line of Mohio has to pass is the **Walk-By Test**: someone with no coding
background should be able to read a line and understand it in about 3 seconds. If you can read
it out loud and it makes sense, you're writing good Mohio.

A Mohio file ends in `.mho`.

---

## 1. Running your code

You use the `mio` command in your terminal (PowerShell on Windows):

- `mio check yourfile.mho` — checks your file for mistakes **without running it**. Run this often.
- `mio run yourfile.mho` — actually runs your program.

Start every new feature with `mio check`. It catches errors early and tells you the line and a fix.

---

## 2. The five rules that never change

These are hard rules. Breaking them is a compile error, so learn them first:

1. **Comments use `//` (or `/* ... */` for multiple lines).** A `#` is **not** a comment in Mohio.
   ```
   // this is a comment
   ```
2. **No curly braces `{ }`. Ever.** Mohio uses indentation and words instead.
3. **Every block ends with a named closer.** When you open a block, you close it with its name:
   `check: done`, `retrieve: done`, `new: done`, `ai.decide: done`. (`done` by itself works but
   always use the named one — it's clearer and safer.)
4. **Plain text only.** Use straight quotes `"like this"`, not curly ones. Use `->` (a dash and a
   greater-than), never a fancy arrow symbol. Word and some editors "auto-correct" quotes and dashes —
   turn that off, or it'll break your code.
5. **One word, one job.** Each Mohio word does exactly one thing. No word means two different things.

---

## 3. Variables — holding values

Name a value to create it, and name it again to change it. No keyword is needed:

```
name "Aria"
score 0
greeting "Hello"
```

`hold` and `lock` are the guarded forms, not the everyday one. `hold` freezes a value
until you `release` it; `lock` freezes it permanently.

```
hold rate 0.05        // frozen until released
lock pi = 3.14159     // frozen for good
```

You can give a default in case a value is missing (bare, no parentheses):

```
mode request.mode default "easy"
```

---

## 4. Showing things

`show` displays a value:

```
show "Welcome!"
show name
show score
```

---

## 5. Numbers and converting types

Math works the way you'd expect: `+  -  *  /`. To convert a value to a specific type, use the
**dot form** of `as`:

```
count field default "0" as.int      // text "0" -> the number 0
price total as.decimal.2             // round to 2 decimal places
label score as.text                  // number -> text
```

Useful ones: `as.int` (rounds to nearest), `as.decimal.2`, `as.number` (keeps the fraction),
`as.text`, `as.bool`. Also `round.up` and `round.down`.

---

## 6. Text (strings)

Join text with `&`:

```
message ("Hi " & name & "!")
```

Other handy text tools: `replace`, `uppercase`, `lowercase`, `trim` (remove spaces from the ends),
and slices like `left`, `right`, `after`, `before`:

```
last4 card right 4          // last 4 characters
shout message uppercase
```

---

## 7. Making decisions (the "No-If" way)

Mohio uses `check / when / otherwise` instead of long if-chains:

```
check score
    when score is above 100
        show "Amazing!"
    when score is above 50
        show "Nice work"
    otherwise
        show "Keep going"
check: done
```

- `is above`, `is below`, `is between 10 and 20`, `is not`, `contains`, `starts with`, `is empty`.
- `unless` is the opposite of a condition (it replaces the old "else"):
  ```
  show "Door is locked" unless door_is_open
  ```

---

## 8. Repeating

Repeat a fixed number of times:

```
repeat 3 times
    show "Knock"
repeat: done
```

Keep going while a condition holds:

```
attempts 0
while attempts is less than 3
    show "Trying..."
    attempts (attempts + 1)
while: done
```

---

## 9. The database — MioQL (Mohio Query Language)

This is one of Mohio's superpowers: queries that read like English. First, connect once at the top:

```
connect db as postgres from env.DATABASE_URL
```

Then refer to tables as `db.players`, `db.puzzles`, and so on.

**Get one row** with `retrieve`:

```
retrieve player from db.players
    match id to current_user
retrieve: done
```

**`match` means "is exactly equal to."** Stack several `match` lines to require *all* of them — that's
how you do a compound key. You don't need an "and":

```
retrieve puzzle from db.puzzles
    match room to current_room
    match verb to command_verb
retrieve: done
// finds the row where room = current_room AND verb = command_verb
```

**`match any` means OR** — any one of them can match:

```
retrieve ticket from db.tickets
    match any
        label to "bug"
        label to "feature"
    match any: done
retrieve: done
// label = "bug" OR label = "feature"
```

**`no.match` means "none of these"** (NOT). It closes with its own `no.match: done`:

```
retrieve task from db.tasks
    match owner to current_user
    no.match
        status to "done"
        status to "cancelled"
    no.match: done
retrieve: done
// owner = current_user, and status is NOT "done" and NOT "cancelled"
```

You can mix all three — they all combine with AND at the top level.

> **Not-found handling — both forms work (verified by running, 2026-08-01).** For a single-row `retrieve`, attach **`on.failure`** to react when the row isn't found (e.g. `on.failure / give back 404 "Not found."`). For a multi-row `find`, the canonical shape distinguishes three outcomes: `find X in db.T / match ... / on.failure (operational failure — a missing table or no DB) / otherwise ( check X / when empty (zero rows) / otherwise (rows present) / check: done ) / find: done`. `when empty` fires on a genuinely empty result, and a missing table routes to `on.failure` (not silently empty-success). (`no.match` above is different — a compound-match filter inside the query, not a result-emptiness handler.)

**`where` is for ranges and comparisons** (not plain equality). Use it mostly with `find`, which gets
*many* rows:

```
find members in db.members
    where score is above 100
    and score is below 500
find: done
```

Other verbs:
- `save to db.players` — add a new row.
- `upsert db.settings match key to "mode"` — update if it exists, otherwise create it.
- `update db.players match id to current_user` + the fields to change.
- `remove ...` — delete rows.
- `grab` — a quick single-key cache read.

---

## 10. AI — the part no other language has built in

**`ai.decide`** asks the AI for a yes/no verdict, with a confidence threshold and a required backup plan:

```
ai.decide is_spam returns boolean
    confidence above 0.85
    weigh
        message.text, sender.history, link.count
    not confident
        give back pending "Sent to a human to review"
ai.decide: done
```

- `confidence above 0.85` — the AI must be at least 85% sure.
- `weigh` — the things the AI should consider.
- `not confident` is **required** — it's your safety net for when the AI isn't sure enough.

**`ai.create`** generates content:

```
ai.create welcome_note text prompt ("Write a friendly one-line welcome for a new player named " & name)
```

`ai.create text` uses Claude by default. `ai.create image` and `ai.create video` also exist (they need
an OpenAI key set). You can add `ai.connect` to set up a chain of AI providers that fall back to each
other, but you don't need that to start.

---

## 11. Web pages and APIs

Use `listen for` to handle web requests, with a `new` endpoint for each address:

```
shape ScreenCheck
    student_name as text
shape: done

listen for
    new sh.ScreenCheck at /checkin
        require role "staff"
        student request.student_name
        save to db.checkins
            name student
            at today
        save: done
        give back 200 ("Checked in: " & student)
    new: done
listen: done
```

- `request.something` reads data that came in with the request.
- `require role "staff"` locks an endpoint to certain users.
- `give back` sends the response back (and the work above it must finish first).

---

## 12. Tasks — reusable steps

Bundle steps you reuse into a `task`, then run it by name with `call`:

```
task welcome
    show "Welcome to the game!"
    show "Good luck."
task: done

// later in your program:
call welcome
```

Tasks can take inputs and return a value. Declare each input with a `take` line:

```
task greet
    take name as text
    returns text
    give back ("Hello, " & name & "!")
task: done

// call it with an input:
call greet with "Aria"
```

Pass **several** inputs by name, one per line — the order doesn't matter, and any input with a `default` can be omitted:

```
task total
    take a, b as int
    returns int
    give back (a + b)
task: done

call total
    a 7
    b 9
call: done
```

Bad arguments fail loud, never silently: a missing required input, a wrong-typed value, an unknown input name, or a value passed to a task that takes none.

(Note: `call` and `task` go together — `call` is how you invoke a task. `run` is reserved for async jobs like `run async ...`.)

---

## 13. Sectors — instant industry knowledge

Declaring a sector turns on built-in rules for that industry (field types, security defaults,
AI confidence floors). One sector per file:

```
sector: financial
```

or a more specific one:

```
sector: financial.banking.retail
```

A sector adds **technical enforcement** — it is not a guarantee of legal compliance, and `require role`
values are always written in quotes.

---

## 14. A complete little program

```
// guess.mho — a tiny number game

connect db as postgres from env.DATABASE_URL

shape Guess
    number as int
shape: done

listen for
    new sh.Guess at /guess
        tries request.tries default "0" as.int
        answer 7
        guess request.number as.int
        total_tries (tries + 1)

        check guess
            when guess is answer
                give back 200 ("You got it in " & total_tries & " tries!")
            when guess is above answer
                give back 200 "Too high — try again"
            otherwise
                give back 200 "Too low — try again"
        check: done
    new: done
listen: done
```

---

## 15. Not built yet — don't use these

These are planned but **fail loud** if you try them today (they never silently do nothing):
`pattern`, `miomap`, `miotranslate`, `mioagent`, `miotest`, `miosms`, `miostream`, `miosys`,
`mioenv`, and the auth/pdf/search/image helpers (`mioauth`, `miopdf`, `miosearch`, `mioimage`).

`mioauth` specifically only became true here as of 2026-08-06 — before that fix, a
`mioauth` declaration or `mioauth.login` call silently produced no AST node at all and
vanished from the program with no error, the opposite of what this line claimed. See
`CLAUDE-CODE-BACKLOG.md`'s mioauth entry for the finding and the fix.

Working `mio*` helpers you *can* use: `miohttp`, `miomail`, `miocache`, `miolog`, `miofile`, and
`mioschedule`. For `mioschedule`, the working form is the **declaration** —
`mioschedule NAME / every 1 days / run task_name / mioschedule: done`, fired on demand with
`run mioschedule.NAME now` (stateless compute can't self-wake); the programmatic
`mioschedule.every N units` statement is not wired yet and fails loud. (`mask.all` also works —
e.g. `card mask.all except last 4`.)

*(All statuses in this section verified by running each helper, 2026-08-01. Previously this list
named `miotest`, `miosms`, `miostream`, `miosys`, `mioenv` as working — none has an executor —
and put `miofile` under "not built" though write/read round-trip correctly. Corrected.)*

---

## 16. Getting help

- Ask in the Pioneer Discord.
- The repo is on GitHub at `github.com/countart/mohio`.
- Run `mio check` constantly — it's the fastest way to learn what the language expects.

You're building with a language almost nobody else has used yet. Have fun, break things, and tell us
what's confusing — that feedback is exactly what makes Mohio better.
