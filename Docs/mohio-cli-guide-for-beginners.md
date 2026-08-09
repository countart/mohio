<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# The `mio` command line — a starter guide

Written for someone who has never used a command line before. If you already know what a
terminal is, skip to "The commands."

## What is a command line?

Most programs you use have buttons you click. A **command line** is different: instead of
clicking, you **type** a short instruction and press Enter, and the computer does it.

Think of it like texting the computer. You send it a tiny message like "run my program,"
and it runs your program. The window where you type these messages is called a
**terminal** (some people say "console" or "shell" — same thing).

Every Mohio instruction starts with the word `mio`, then what you want it to do, then
usually the name of your file. Like this:

```
mio run game.mho
```

That reads as: "mio, run the file called game.mho." Press Enter, and it runs.

## Opening a terminal

- **Windows:** press the Start button, type `powershell`, open it.
- **Mac:** press Command+Space, type `terminal`, open it.

A window opens with a blinking cursor waiting for you to type. First, go to the folder
your project is in with `cd` (it means "change directory"):

```
cd C:\my-mohio-project      (Windows)
cd ~/my-mohio-project        (Mac)
```

Now you're "inside" your project and can run the `mio` commands below.

## The commands you'll use every day

### `mio run` — run your program once

```
mio run game.mho
```

Runs the program one time and shows the result. Good for scripts and quick tests.

### `mio serve` — run your program as a website

```
mio serve site.mho
```

Turns your program into a running website. After you run it, open your web browser and go
to **http://localhost:8080** to see it. Use this for anything with `listen for` or `page`
blocks. Press Ctrl+C in the terminal to stop it.

Want a different address? `mio serve site.mho --port 3000` serves it at
http://localhost:3000 instead.

### `mio check` — find mistakes without running

```
mio check game.mho
```

Reads your program and tells you about errors and warnings **without running it**. This is
your best friend — run it often. It'll point at the exact line and usually tell you how to
fix it.

- `mio check --security game.mho` gives a full security and compliance report.
- `mio check --all` checks every `.mho` file in your project at once.

### `mio fmt` — tidy your code

```
mio fmt game.mho
```

Shows how to clean your code up to the standard style. Add `--write` to actually apply it:

```
mio fmt game.mho --write
```

It fixes spacing and small things (like turning a quoted path back into the canonical
unquoted form) so your code looks the way everyone else's does.

### `mio version` and `mio help`

```
mio version      // prints which version of Mohio you have
mio help         // shows a summary of the commands
```

## The rest (you'll grow into these)

You won't need these at the start, but here they are so nothing's a mystery:

- `mio translate site.mho --to pt` — rewrites your program into another human language
  (Portuguese here). Mohio can be written in more than one language.
- `mio schema show site.mho` — shows the database tables your program uses.
- `mio schedule run-due jobs.mho` — runs any scheduled jobs that are due right now.
- `mio warmup` — pre-builds the compiler so your very first run is faster.
- `mio generate`, `mio harvest`, `mio install-hooks` — internal/advanced tools; you can
  ignore these for now.

## The three you'll type most

If you remember nothing else:

- **`mio check <file>`** — did I make a mistake?
- **`mio run <file>`** — run it once.
- **`mio serve <file>`** — run it as a website.
