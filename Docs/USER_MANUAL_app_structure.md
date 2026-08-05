<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md. -->
# Mohio User Manual: Structuring an App (pages, includes, routing, setup)

This section explains how to build a Mohio app out of small pieces instead of one
giant file: a thin skeleton page, logic pages you pull in, a shared spine, and how
the HTML frontend connects to it. Everything below is verified working.

---

## 1. The skeleton page

Your main file is a thin shell. It declares the app, accepts requests, and routes
each one to the right piece of logic. It does NOT hold all the logic itself.

```mohio
sector: game                              // your compliance profile
connect db as postgres from env.DATABASE_URL

include "greetings.mho"                    // pull in a logic page (see below)
include "billing.mho"                      // and another

shape Request
    action as text required
shape: done

listen for
    new sh.Request
        require role "user"
        action request.action
        check action
            when "hello"
                call greet with "friend"
            when "invoice"
                call make_invoice
                call: done
            otherwise
                give back 200 "Unknown action."
        check: done
    new: done
listen: done
```

The `check action / when ... / call ...` block is your router. Each command points
at a task. That is the whole skeleton.

---

## 2. Logic pages (include)

A logic page is just another `.mho` file. You pull it in with `include "file.mho"`.

An include is NOT limited to tasks. It carries whatever is at the top of the file:
tasks you call, and plain setup that runs when the app starts. Example:

```mohio
// greetings.mho
hold SITE_NAME "Acme"                       // setup: runs once at startup
task greet who as text                       // a task: runs when you call it
    give back 200 ("Welcome to " & SITE_NAME & ", " & who & ".")
task: done
```

```mohio
// billing.mho
task make_invoice
    give back 200 "Invoice #1001 created."
task: done
```

Two things to know about `include`:

- It happens at BUILD time, before your app runs. Each included file is read and
  merged in once. This is also why it is fast: each file is parsed on its own, so
  ten small files cost far less than one huge file.
- A task keeps its own scratch space. Only what it `give back`s comes out. If a
  task needs to change something that must STICK (move a player, set a flag), it
  must write the database directly inside the task. Setting a plain variable inside
  a task does not reach the caller. (This is normal function scoping.)

### Calling a task
- No arguments:        `call greet_world` then `call: done`
- One argument:        `call greet with "friend"`
- Several arguments:   `call greet` / `name "friend"` / `lang "en"` / `call: done`

A task returns by `give back`, and that response becomes the app's response.

---

## 3. The shared spine (journey.mho)

Put a file named exactly `journey.mho` in a folder, and it is applied to every
`.mho` in that folder automatically, with NO include line. Think of it like a
header that is always there. It is the right home for declarations every page in
the folder should share.

```mohio
// journey.mho
task audit_hit label as text
    give back 200 ("[spine] logged: " & label)
task: done
```

Your skeleton can call `audit_hit` without including anything. Two rules:

- It is folder-scoped. `journey.mho` only affects files in its own folder, so keep
  one app per folder to keep its spine from touching unrelated files.
- Your main file wins. If the spine and your file both define the same name, your
  file is processed last and overrides the spine.

---

## 4. Conditional routing (the important part)

People coming from PHP expect to choose a file at runtime:
`if (x) include 'a.php'; else include 'b.php';`. Mohio does it differently, and
once you see the shape it is simpler.

Mohio includes are STATIC: every `include` is pulled in at build time, always. You
do not pick a file to include at runtime. Instead you include everything up top,
then choose which logic to RUN at the `call`:

```mohio
include "logic_a.mho"
include "logic_b.mho"
include "logic_c.mho"

// ... inside your handler:
check mode
    when "a"
        call run_a
        call: done
    when "b"
        call run_b
        call: done
    otherwise
        call run_c
        call: done
```

Same result as the PHP version (the right logic runs for the right case), with one
clean difference: loading (include, build time) and choosing (check/call, run time)
are separate steps. Load everything once, branch at the call.

---

## 5. Frontend and backend (the HTML page vs the .mho)

Your `.mho` is the BACKEND: it handles requests and returns responses. An HTML page
is the FRONTEND: it is what the browser shows, and it sends requests to your `.mho`.

How the server wires them when you run `mio serve yourapp.mho`:

- `GET /`            serves the home HTML page to the browser.
- `POST /`           is handled by your `listen for / new` block (this is where the
                     browser sends actions/commands; you `give back` the response).
- `GET /some/path`   renders a `page at /some/path` declared in your `.mho`, or a
                     real static file (css, images) sitting in the app folder.

So a typical app is: one `.mho` (backend logic, routes, pages) plus one HTML page
that POSTs user actions to it and shows what comes back. That is exactly how the
Zork demo is built (an HTML page talking to `zork_demo.mho`).

### Serving your own content at `/`
Handle the root with a route: declare a shape and add `request for sh.X at /` (the path is
written **unquoted** — `at /`, not `at "/"`). That serves your own content at `GET /`;
without such a handler, the server shows a default home page. For other GET routes use a
`page` declaration in your `.mho`; for assets, drop them in the app folder as static files.
