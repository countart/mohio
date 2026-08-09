<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Mohio Manual — New Chapters

Six chapters plus a CLI note, covering how a `.mho` file becomes a running web app, how
to call tasks, sagas, retrieving records, and an honest list of what isn't built yet.
Every code example here has been run through `mio check` and compiles.

---

## Your first web page — `render`

This is how a `.mho` file serves a web page.

```mohio
journey App
    page Home at /home
        render
            <h1>Welcome, {{ user.name }}</h1>
        render: done
    page: done
journey: done
```

Serve it with `mio serve site.mho` and open **http://localhost:8080/home**.

`render` is the page container. You write only the *body* — the part a person actually
sees — and the runtime wraps it in a complete HTML5 document for you: the doctype,
`<html lang>`, the charset, the viewport tag. Remove `render` and there's nothing left on
the page but your intent.

A few things `render` does for you:

- **`{{ expr }}` is filled in with your data and auto-escaped.** If `user.name` is
  `<script>…`, it shows up as harmless text, not running code. That HTML-escaping is on
  by default, so the safe thing is also the easy thing.
- It sets the response type to `text/html` and serves the page straight to the browser.
- If you decide to write a *complete* document yourself (starting with `<!doctype>` or
  `<html>`), the runtime steps back and does not double-wrap it.

**Page title and description** come from `title` and `describe` declared in scope:

```mohio
journey App
    title "My Site"
    describe "A friendly demo"
    page Home at /home
        render
            <h1>Hi</h1>
        render: done
    page: done
journey: done
```

Those populate `<title>` and the meta description in the document head.

**Don't confuse `render` with `show`.** They look similar and do different jobs:

- `render` — a web **page** (a full document, `text/html`).
- `show <value>` — emits a **value** (for `mio run` output or building up a result), not
  a page.
- `show / …html… / show: done` — a raw HTML **fragment**: interpolated, but *not* wrapped
  in a document and with no content type. Use it to build one piece of a page; use
  `render` for the page itself.

---

## App structure — journeys and pages

`journey` is your app's root. It holds the shared setup — `connect`, tasks, shapes,
`title` — and every page inside it inherits that setup.

`page N at /path` is one **GET** route: what a visitor sees at that URL. A page body ends
in either `render` (a web page) or `give back` (data or a status code).

```mohio
journey App
    connect db as sqlite from env.DATABASE_URL

    page Home at /home
        render
            <h1>Welcome</h1>
        render: done
    page: done

    page Health at /health
        give back 200 "ok" as json
    page: done
journey: done
```

For endpoints that **create or submit** (a form post, an API write), put a `listen for`
inside the journey, right alongside your pages:

```mohio
journey App
    page Home at /home
        render
            <h1>Sign up</h1>
        render: done
    page: done

    listen for
        new sh.Signup at /signup
            give back 201 "created"
        new: done
    listen: done
journey: done
```

**Routing** is an exact path match. Nothing matches, you get a clean 404. A trailing
slash or a `?query=…` on the end of the URL is tolerated.

**Serving the root `/`:** declare a shape and handle it with `request for sh.X at /` — the
path is written **unquoted** (`at /`, never `at "/"`, which does not register). That handler
serves your own content at `/`; without one, the dev server shows a default home page.

---

## Calling tasks — and taking back what they return

A task is a reusable block: define it once, then `call` it.

There are two ways to call:

```mohio
// no argument, or arguments in a body
call sendWelcome
call: done

// one inline argument
call greet with "Aria"
```

A bare `call greet` — no body, no `with`, no closer — is **not valid** and fails loud. It
used to silently turn into an assignment, which quietly hid bugs.

**Capturing what a task gives back.** If a task is declared with `returns <type>`, its
`give back` is a *return value*. Capture it at the call site with `as`:

```mohio
task greet person as text returns text
    give back ("Hi " & person)
task: done

call greet with "Aria" as greeting
show greeting          // Hi Aria
```

The body form takes `as` in the same place:

```mohio
call greet as result
    person "Zed"
call: done
```

Use `call` for task invocation today. (`run async …` is a separate feature for
background work and is unaffected.)

---

## Sagas and steps — all-or-nothing sequences

A saga is a sequence of steps that either all succeed or get rolled back, so you never
leave things half-done (money charged but no order created, for example).

```mohio
saga process_order
    step charge
        show "charging the card"
        compensate
            show "refunding the card"
    step: done
    step reserve
        show "reserving stock"
    step: done
saga: done
```

- **`compensate`** is a step's rollback block. If a step fails, every *completed* step
  before it is compensated in **reverse** order. The failing step's own `compensate`
  does not run (it didn't finish, so there's nothing to undo).
- **`best effort`** on a step means "if this fails, shrug and carry on." Its failure is
  swallowed and it is never compensated — good for non-critical work like a notification
  email.
- **`on.failure` / `on.success`** are per-step and local; `on.failure` runs before any
  saga-level rollback.
- Use **`compensate`**, not `undo` (`undo` is a warned alias for it).

**Reading the result.** The saga's status binds to its name. Read `<name>.status` and
branch on it:

```mohio
saga process_order
    step charge
        show "charging"
    step: done
saga: done

check process_order.status
    when "COMMITTED"           show "order placed"
    when "COMPENSATED"         show "rolled back; nothing charged"
    when "FAILED_COMPENSATION" show "manual intervention needed"
check: done
```

A saga always resolves to exactly one of `COMMITTED`, `COMPENSATED`, or
`FAILED_COMPENSATION`. A best-effort failure never downgrades a `COMMITTED`.

**Not yet:** parameterized sagas (`saga foo(a, b)` as a reusable callable) and nested
sagas are future work and fail loud today.

---

## Retrieving records — the `retrieve` modifiers

Plain `retrieve` gets a single record. When you want to be specific about how many, or
which, add a dot modifier:

```mohio
retrieve.one recent from db.orders
    match id to 1
retrieve.one: done
```

The valid modifiers are `.one`, `.first`, `.last`, `.all`, `.every`, and `.count`. Plain
`retrieve` means a single record. Anything else fails loud at compile time, so a typo
like `retrieve.slice` is caught before you run.

Both `retrieve: done` and `retrieve.<mod>: done` close the block.

---

## What isn't built yet

An honest list so you're never surprised by a silent gap. Each of these fails loud today
rather than doing nothing quietly:

- `display` block — use `render`.
- `mio.*` view helpers — use literal HTML with `{{ }}` for now.
- Saga invocation with arguments, saga-level handlers, and nested sagas.
- `send` / `broadcast` / `stream` / `notify`.
- `cm.purge` / `cm.retain` / `cm.report`.
- `verify token`.

If you write one of these, the compiler tells you plainly instead of leaving you
guessing.

---

## `mio check` is your pre-flight

`mio check <file>` catches more than typos. It reports transform-time errors too — an
invalid `retrieve` modifier, a closer mismatch, a retired keyword, a mis-ordered
declaration — each with a line number, and it exits non-zero when something is wrong.

So `mio check` is a real gate: if it comes back clean, your file is structurally sound
before you ever run or serve it. Run it early and often.
