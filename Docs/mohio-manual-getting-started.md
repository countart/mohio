<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md. -->
# Mohio Manual — Getting Started

The very beginning. This assumes you have never written code before. If you already have,
you can move quickly, but nothing here expects prior knowledge. Every example here has been
run and works.

---

## 1. What is Mohio?

Mohio is a language for telling a computer what you want. The name is Māori for "to
understand," and that's the whole idea: you write down *what* should happen, in plain
words, and Mohio figures out *how* to make it happen.

Most programming languages make you spell out every tiny step. Mohio is different. You
write your **intent**, and the language handles the machinery underneath. Here is a
complete Mohio program:

```mohio
name "Aria"
show "Hello, {{ name }}!"
```

You can probably read that already, even having never seen Mohio: remember a name, then
show a hello with it. That's not an accident. It's the one test every line of Mohio has to
pass:

> **The Walk-By Test.** If someone walked by your screen and read a line out loud, they
> should understand what it does in about three seconds — even if they don't know Mohio.

If you can read your code out loud and it makes sense, you're writing good Mohio.

A Mohio file ends in `.mho`. That's your program.

---

## 2. The rules that never change

Mohio has a handful of hard rules. Learn these five first, because breaking one is the
one kind of mistake the computer won't try to guess around — it'll just tell you.

**1. Comments start with `//`.** A comment is a note to yourself; the computer ignores it.

```mohio
// This is a note. The computer skips it.
show "But this runs."
```

For a longer note, wrap it in `/* … */`. A `#` is **not** a comment in Mohio.

**2. No curly braces `{ }`.** Ever. Other languages use them to group things; Mohio uses
indentation (spaces at the start of a line) and words instead. The only brace you'll see
is the double `{{ }}`, which means "drop a value in here" — you'll meet it in a moment.

**3. Every block closes with its own name.** When you open a block, you close it by naming
it. If you open a `check`, you close it with `check: done`. This is so the computer can
tell you *exactly* which block you forgot to close, on which line.

**4. Straight quotes only.** Use `"like this"`, not curly quotes. Some editors "helpfully"
change your quotes and dashes — turn that off, or it'll break your code.

**5. One word, one job.** Each Mohio word does exactly one thing. No word means two
different things, so once you learn a word, you know it everywhere.

---

## 3. Your very first program

Make a file called `hello.mho` and put this in it:

```mohio
greeting "Hello from Mohio."
show greeting
```

Then, in your terminal (see the CLI guide if that word is new), run:

```
mio run hello.mho
```

You'll see `Hello from Mohio.` printed out.

Here's what each line did. The first line names a value: `greeting` is the name, and the
text `"Hello from Mohio."` is what it holds. That is the everyday way to make a value in
Mohio — no keyword needed, just a name and what it should be. **`show`** displays a
value. That's a whole program: no setup, no boilerplate. The file *is* the program.

Change the text, run it again, watch it change. That loop — edit, run, look — is how
you'll learn fastest.

---

## 4. Remembering things: values

**A plain variable** is the everyday kind — name it, and change it any time by restating it:

```mohio
score 0
score 10
show score          // 10
```

That's the default: a plain variable is dynamic and changes freely. When you want a value
*protected*, Mohio gives you two stronger options.

**`hold`** freezes a value. Once held, it can't change until you `release` it — good for a
value you want guarded against accidental change but may deliberately update later:

```mohio
hold rate 0.05      // freeze it
release rate        // unfreeze it first
rate 0.07
show rate           // 0.07
```

Trying to change a held value without releasing it fails loud and tells you it's held. You
can unfreeze and set a new value together with `release.now`:

```mohio
hold rate 0.05
release.now rate = 0.07
show rate           // 0.07
```

**`lock`** is the strongest: it makes a value permanent. A locked value can never change,
and it can't be released:

```mohio
lock pi = 3.14159
show pi             // 3.14159
```

Trying to change a locked value fails loud — a lock is permanent by design.

So there are three tiers; reach for the lightest that fits:
- a **plain variable** — changes freely (most values)
- **`hold`** — frozen until you `release` it (protect, but occasionally update)
- **`lock`** — permanent, never changes (true constants)

---

## 5. Working with text

The double braces `{{ }}` drop a value into a piece of text:

```mohio
name "Aria"
show "Welcome back, {{ name }}."
```

You can also join text with `&`:

```mohio
name "Aria"
greeting ("Hi " & name & "!")
show greeting          // Hi Aria!
```

(When you do math or join text like that, wrap it in `( )`.)

To change the case of text, use a cast: `as.uc` makes it UPPERCASE, `as.lc` makes it
lowercase:

```mohio
name "aria"
loud (name as.uc)
show loud          // ARIA
```

---

## 6. Numbers and true-or-false

Math works the way you'd expect: `+  -  *  /`. Wrap a calculation in `( )`:

```mohio
price 4
quantity 3
total (price * quantity)
show total          // 12
```

Sometimes a value is text that you need as a number (or the other way around). Convert it
with a **dot cast**:

```mohio
text_number "5"
real_number (text_number as.int)
show real_number          // 5, as a number you can do math with
```

Useful casts: `as.int` (a whole number), `as.decimal.2` (two decimal places), `as.text`,
`as.number`.

And a value can simply be **true or false** — Mohio calls these booleans:

```mohio
game_over false
has_key true
```

You'll use these constantly to make decisions, which is next.

---

## 7. Making choices

Instead of a pile of if-statements, Mohio uses **`check / when / otherwise`**. You check
a value, list what to do `when` something is true, and give an `otherwise` for everything
else:

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

The conditions read like English. Some you'll use often:

- `is more than 100`
- `is less than 10`
- `is "cash"` (equals)
- `is not "closed"`
- `contains "smith"`
- `starts.with "A"`
- `is empty`

That covers almost every decision you'll need to make. `check / when / otherwise` is the
one tool to reach for whenever your program has to choose between paths.

For a quick one-liner, **`unless`** does something *unless* a condition is true, the
opposite of "if":

```mohio
door_open true
show "The door is locked." unless door_open
```

That shows the message only when `door_open` is false. `unless` works with any condition
you saw above (`unless age is more than 18`, `unless a and b`), and it's a reserved word,
so you can't name a variable `unless`.

---

## Where to go next

You now know enough to write real little programs: remember values, work with text and
numbers, and make choices. From here:

- **Loops** — doing something over and over (see the Loops explainer).
- **Tasks** — bundling steps you reuse and calling them (see the Tasks explainer).
- **Web pages** — turning a `.mho` into a website with `render` (see the web-pages
  chapter).

And whenever you're unsure whether something is a mistake, run `mio check yourfile.mho`.
It reads your program without running it and points at the exact line, usually with a fix.
It's the fastest way to learn what Mohio expects.
