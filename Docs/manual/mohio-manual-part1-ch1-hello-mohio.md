<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Part I -- First Programs

## Chapter 1: Hello, MOHIO™

*Rung 1 of the ladder: install, your first line, and the shift every reader coming from another
language needs before chapter 2. Every sample in this chapter is verified against the current
compiler (see the two source docs this chapter is built from, both independently re-checked this
pass).*

---

### Get Mohio running

Clone the repository and install it -- the same three commands on Mac, Windows, or Linux:

```bash
git clone https://github.com/countart/mohio
cd mohio
pip install -e .
```

`pip install -e .` installs Mohio's dependencies AND the `mio` command itself. Verify it:

```bash
mio version
mio warmup      # first run only, caches the grammar
```

Full install walkthrough, with the Windows-specific notes (PowerShell environment variables, a
console encoding gotcha) and every step proven by running it: `start-here/QUICKSTART.md`.

---

### Your first program

Make a file called `hello.mho`:

```mohio
greeting "Hello from Mohio."
show greeting
```

Run it:

```bash
mio run hello.mho
```

```
Hello from Mohio.
```

That's the whole program. No boilerplate, no imports, no main function. Naming a value
(`greeting "..."`) is the everyday way to make one -- no keyword needed. `show` prints it.

### The five rules that never change

1. Comments are `//` and `/* ... */`. `#` is not a comment.
2. No single curly braces `{ }`. The only brace form is `{{ }}` (drop a value into text).
3. Every block closes by naming what it closes: `check: done`, `shape: done`, `task: done`.
4. Straight quotes `"..."` only -- turn off editor auto-correct, which "helpfully" swaps them.
5. One word, one job. A word never means two different things depending on context.

Breaking one of these is the one kind of mistake the compiler will not try to guess around --
it names exactly which rule and where.

### Values: naked, held, and locked

A plain variable is the everyday kind -- name it, and change it any time by restating it:

```mohio
score 0
score 10
show score          // 10
```

Two stronger forms exist for when a value needs protecting. `hold` freezes it until you
`release` it:

```mohio
hold rate 0.05
release rate
rate 0.07
show rate            // 0.07
```

`lock` is permanent -- a locked value can never change again:

```mohio
lock pi = 3.14159
show pi               // 3.14159
```

Reach for the lightest of the three that fits: a plain variable for most values, `hold` for
one you want guarded but may deliberately update later, `lock` for a true constant.

### Text and choices

Join text with `&`, drop a value into a string with `{{ }}`:

```mohio
name "Aria"
greeting ("Hi " & name & "!")
show "Welcome back, {{ name }}."
```

Instead of if/else, Mohio uses `check` / `when` / `otherwise`:

```mohio
score 80
check score
    when score is more than 100
        show "Amazing!"
    when score is more than 50
        show "Nice work"
    otherwise
        show "Keep going"
check: done
```

The full walkthrough this chapter draws from -- numbers, casts, and every comparison word
(`contains`, `starts.with`, `is empty`, and more) -- lives in
`Docs/manual/mohio-manual-getting-started.md`, written for someone who has never coded before and
verified line by line (all 15 samples, `mio check` and `mio run`, output matched).

---

### Before chapter 2: the one shift that matters most

If you already know how to code, one habit will trip you more than any syntax difference: Mohio
has no classes.

A `Player` class in Java or C# owns a `health` field AND a `heal()` method together, one thing,
two jobs. Mohio splits those jobs and never puts them back together:

- **`shape`** describes data. What a `Player` IS.
- **`task`** does something. What HAPPENS to a `Player`.

There is no `class` keyword. Reaching for one -- or writing behavior onto a `shape` -- is the
single most common first mistake, and the compiler names the fix directly when you try. The
closest thing to a method is a `task` that TAKES a shape instance and RETURNS one:

```mohio
shape Player
    name as text
    health as int
shape: done

task heal
    take player as sh.Player
    take amount as int
    returns sh.Player
    create healed as sh.Player
        name player.name
        health (player.health + amount)
    create: done
    give back healed
task: done
```

Run it and `hero.health` comes back `65` from a starting `40 + 25` -- but notice the task did
not change `player` in place; it built and returned a NEW `Player`. There is no `this`, and
nothing is ever mutated behind your back.

Full chapter, with the worked example run and its output confirmed, and the exact limit (a task
cannot reach into a shape instance and change one of its fields directly -- verified, it fails
loud): `Docs/guides/mohio-mental-model-no-classes.md`.

---

**Next: Chapter 2, A Small App With Real Data** -- a shape, a route, a validated form, a
database write, and a list-back, built step by step.
`Docs/manual/mohio-manual-part1-ch2-small-app-real-data.md`.
