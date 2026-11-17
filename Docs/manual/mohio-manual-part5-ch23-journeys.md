<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Part V -- Web and delivery

## Chapter 23: Journeys -- what a folder of pages shares

*Every example below was run: `mio check` and `mio serve` hit with real `curl` requests, with the
command and result shown. The cascade landed in build Batch 36 (2026-09-25); anything written before
that describes a journey that did not reach subfolders, so this chapter is written against the current
build, not the older design.*

A journey is now the **security boundary** of a Mohio application: the sector, the database connection,
the shapes and the AI ceiling all flow from it to the pages beneath it. A developer who misreads the
cascade can leave a subfolder outside a rule they believe applies, so this chapter is worth reading
closely, not skimming.

---

### 1. What a journey is

A journey is **where an application says what is true for a whole folder of pages**. It is not a page, not
a route, and not a controller. It answers no request and renders nothing; it holds the things every page in
its folder needs -- the database connection, the shapes, shared values and tasks, the sector, the AI ceiling
-- so no page has to repeat them. A page in the folder starts with its journey already applied, then runs its
own code.

Two application lanes read the whole cascade below and still could not answer the question they actually had:
does my connection, my shape, my task belong in the journey or in the page? So before the mechanism, the
plain answer.

**What you gain by putting something in a journey:** you declare it once and every page in the folder has it,
with no repetition and no chance of two pages drifting apart. It is also the security boundary, so a rule
declared here covers the whole folder rather than depending on each page to remember it.

**What you lose, and it is the same fact from the other side:** the cascade reaches FURTHER than you may
intend. A declaration in a journey applies to every page in the folder AND every page in every subfolder
beneath it (section 4). That is a broad commitment: a second database connection put in the journey is opened
for pages that never touch it, and a shape put there is in scope for pages that do not use it. Put something
in a journey when the breadth is what you want, not merely because more than one page happens to need it right
now. The next section is the rule for deciding.

---

### What belongs in a journey, and what belongs in a page (the rule to apply)

Two tests, and something belongs in the journey when BOTH lean yes:

1. **Does more than one page need it?** One page's need belongs in that page.
2. **Does it describe the APPLICATION rather than the SCREEN?** The connection, the sector and the shared
   data model describe the application. A form's layout, one screen's helper and one listing's page size
   describe the screen.

Applied to the demo applications (the banking/payment demo and the scenario demo), here is each side.

**Belongs in the journey:**

- The **database connection** every page reads and writes. One app, one connection, declared once.
- **`sector: financial`**. The whole application is regulated; the sector is a property of the application,
  not of any one screen, and it is the security boundary.
- The **AI ceiling** (`limits`). An application-wide budget, not a per-screen one.
- A **shape used across pages** -- the payment `shape` shared by the USD and BHD pages.
- The **shared validation task** (`amount_ok`), called by both payment pages.
- The **shared read helper** that every page uses to format money or read the scenario catalog.

**Belongs in a page:**

- The **route or handler** that answers one screen's request.
- The **render / markup** for one screen.
- A **helper task only that page calls**. It is file-local anyway: a sibling page cannot see it (Chapter 14).
- A **value specific to one screen**, such as that screen's heading or its own page size.
- A **second database connection used by one page only** (see the ambiguous cases).

**The genuinely ambiguous cases, and which way they go:**

- **A shape used by two pages -> journey.** Two pages need it and it describes the application's data, not one
  screen. Both tests lean yes, so it goes up.
- **A task used by one page today, maybe two tomorrow -> page, today.** Do not hoist on speculation. Because a
  nearer declaration overrides a farther one (nearest-wins, Chapter 14), moving the task up to the journey the
  moment a second page needs it is a safe, non-breaking change. Hoisting early only puts logic in scope for
  pages that do not use it.
- **A connection to a second database used by one page only -> page.** It fails the first test: only one page
  needs it. Put it in the journey and the cascade opens that second connection for every page in the folder
  that never touches it. Keep it in the page, and move it up only if a second page needs the same second
  database.

**Cross-reference:** a task declared in a journey is callable from every page under it, INCLUDING pages in
subfolders since Batch 36. That is the mechanism that makes "shared task -> journey" work, and it is covered in
Chapter 14, Tasks, under "Where a task can live." Neither application lane knew it, which is why they
duplicated logic across pages instead of sharing one task.

**Source or data: the compiler enforces shapes, not values (ruled).** The data-driven principle says
information should be data whenever it reasonably can be, and a journey declares a sector, a connection, an AI
ceiling, classifications, shapes and tasks. Which of those should be data? The rule Ron set is general:

> The compiler enforces SHAPES, not VALUES. A rule stays in source, where a reviewer sees it in a diff and the
> compiler enforces it; the numbers it uses may be data.

So a rule that "above a threshold means director approval" is source, and the threshold NUMBER can come from a
table, with the task taking it as a parameter. That is one application across many deployments without giving
away the guarantee. And it is the coder's choice: hold your values in source, in logic, or in a database with
strict write controls, as you decide; Mohio does not police where a value lives.

A SECTOR is the exception that proves the rule, because a sector is not a value, it is which rules apply, so it
is a shape and it MUST be source. A sector held in a table is a sector anyone who can write that table can
remove, and the compliance enforcement everyone believed in would be gone with nothing in a diff to show it.

**Three parts of the principle that a real split makes concrete** (an application lane implemented the
authority-threshold split and added these; each belongs in the principle, not just in their app):

- **A rule whose values come from data records WHICH VERSION of those values it used.** Values held in data can
  change, so the decision stamps the policy version it ran under, and the evidence record inherits that stamp,
  including a later step such as a director approval. Without the version, the audit record cannot say which
  numbers were in force when the decision was made, which is the one hole "numbers may be data" would otherwise
  open.
- **The policy FAILS CLOSED on every bad shape, not only a missing one.** An unsupported currency, a nonpositive
  threshold, and reversed or equal thresholds all refuse (a 503), so a policy table containing nonsense stops the
  application rather than producing a wrong decision. Data supplying the numbers does not mean data can supply
  garbage: the shape is still enforced.
- **The rule structure stays in source; the task takes the numbers as inputs.** Their task takes three decision
  inputs, so what a reviewer reads in a diff is the SHAPE of the decision and never the numbers.

**Worked example, end to end.** The lane moved its authority thresholds into a task in the journey, verified at
the exact boundaries, and made the thresholds database-backed. Under the ruling it splits cleanly: the rule
stays in the task in source, the numbers come from data, the task takes them as parameters, the decision stamps
the policy version and the evidence inherits it, and a malformed policy row fails closed rather than deciding
wrong. Same guarantee, one application across many deployments.

(The follow-on build work from the sector half is tracked as `T1-SECTOR-ABSENT-MUST-NOT-COMPILE`,
`T1-SECTOR-FALLBACK-AND-LICENCE-EXPIRY` and `T1-SECTOR-CHANGE-RECORD`, all sector-tier.)

---

### Access control -- what the journey gates, and what it does not

Governance (the sector and the connection, sections 3-6) decides which rules a page runs under. **Access
control decides who may load a page at its URL at all**, and it is declared the same way: by path, in the
journey. A folder `journey.mho` that classifies four paths:

```mohio
timezone "UTC"
public: /ok
unlisted: /docs
authorize: /admin
hidden: /secret
```

Served with `mio serve app` and hit anonymously (no signed-in session):

```
$ curl ... /ok      -> 200     # public:    served and listed
$ curl ... /docs    -> 200     # unlisted:  served, UNLISTED, NOT gated
$ curl ... /admin   -> 403     # authorize: sign-in required
$ curl ... /secret  -> 404     # hidden:    not served to anyone
```

**Two things the journey does NOT do, and both are mistakes that have shipped:**

- **It does not gate the `.mho` source.** Source is never served; it stays on the server. A request for the
  file itself is a plain 404, classified or not:

```
$ curl ... /journey.mho  -> 404
$ curl ... /index.mho    -> 404
```

  What a visitor can reach is the rendered page at its route, and that is what a classification gates.

- **A hard-to-guess URL is not access control.** `unlisted:` keeps a path out of any listing, but still serves
  it 200 to anyone who requests it. If a page must be protected, gate it; do not rely on nobody finding the link.

**The gates are `authorize:` and `hidden:`.** `authorize:` withholds the body with a 403 until the visitor
holds a server-verified role (a path is matched at its segment boundary, so `authorize: /admin` covers
`/admin/panel` but not `/administration`). `hidden:` returns 404 to everyone, and a path segment starting with
`_` is hidden whether declared or not. `public:` and `unlisted:` are about *listing*, not access: both serve the
body to anyone who asks.

**`private:` is the former spelling of `unlisted:`, and still works.** It is a deprecated alias: a journey or
page that writes `private:` checks clean but warns -- `private: is now unlisted:; note neither restricts access
- use authorize: or hidden: to gate` -- and behaves exactly as `unlisted:` (served, unlisted, not gated). Write
`unlisted:` in new code; the rename lands in 6.0.4.

**The gate runs before the page renders.** Every journey's gate fires before any handler dispatches, so a page
under `authorize:` returns 403 with its body never reached -- a handler cannot leak its content ahead of the
gate, whatever order it is written in:

```
[mohio.error] status=403 :: Please sign in. This page needs someone signed in, and nobody is.
GET /admin   403      # the "admin page" body was never produced
```

**When you do NOT need any classification.** Public is the default, so a page meant for everyone needs none.
Reach for a classification only to restrict: `authorize:` to require sign-in, `hidden:` to take a path off the
server, `unlisted:` to keep a served path out of listings. This is htaccess/config-style access control --
declared once, for a whole folder, in version control -- and on this build it is enforced at the serve layer,
not advisory.

---

### 2. The two forms

**The canonical form is a file named `journey.mho`, with no wrapper**, sitting in a folder beside the
pages it applies to. Its declarations are written at the top level of the file:

```mohio
greeting "from-root"
```

**The wrapper form, `journey NAME ... journey: done`**, groups the same declarations (and can hold
routes) inside one named block:

```mohio
journey Dashboard
    hold greeting "hi"
    request for sh.Q at /home
        give back [200] "ok"
    request: done
journey: done
shape Q
    q as text
shape: done
```

**Reach for the folder `journey.mho` form.** It is the one the folder convention is built around and the
one the cascade (below) walks. The wrapper is the older form and is still accepted.

**Classification is declared by path, and a folder `journey.mho` is the canonical place for it.** The four
classification lines (`public:` / `unlisted:` / `authorize:` / `hidden:`, the access control above) parse at the
top level of a folder `journey.mho`, exactly as they do inside the wrapper:

```
$ mio check journey.mho     # a folder journey.mho whose lines are public:/unlisted:/authorize:/hidden:
  v  journey.mho  5 lines . no errors
```

(An earlier build accepted these only inside the `journey NAME ... journey: done` wrapper. That gap,
`T1-CLASSIFICATION-WRAPPER-ONLY`, is **closed** on this build: a bare folder `journey.mho` classifies.)

**Classification cascades, tighten-only, like the sector (section 5).** A nearer journey may make a path
STRICTER, never looser, in the order `public` < `unlisted` < `authorize` < `hidden` (`hidden` is the strongest).
A subfolder that tightens is accepted; one that would loosen a parent's class for the same path is refused when
the app is assembled (the `mio run` / `mio serve` / `mio check app` path):

```
$ mio check app     # root  authorize: /y     subfolder  public: /y
Error  The journey at app/sub/journey.mho sets `public: /y`, which would LOOSEN `authorize: /y` the
journey at app/journey.mho already requires. A subfolder journey may only TIGHTEN a page's
classification, never relax it ...
```

A narrower path may still be carved out looser (`authorize: /admin`, then `public: /admin/open`). Before this
rule (build 2026-10-04), a subfolder could silently make a hidden path public.

**Do you need a tag on each page? No.** A page is governed by its own folder's journey and every ancestor
journey up to the app root purely by WHERE IT SITS -- nothing tags a page into a journey, and classification is
declared by path in the journey, so pages are not tagged either. A page MAY restate a classification for its own
path inline, but that only helps to TIGHTEN its own path (match or raise the class). A page cannot loosen itself:
the strictest class any journey declares for the path is what serves, and a looser inline line is inert and only
warns.

```
$ mio check app     # journey  authorize: /p     page p.mho  public: /p
! Line 1: this page declares `public: /p`, but the journey ... classifies `/p` as `authorize:`, which
is stricter ... this page is served as `authorize:` and its own line does nothing.
```

**The one real journey-line tag is `[important]`.** It sits on a journey classification line (not on a page) and
makes that class a FLOOR: a page that declares a class BELOW it for the path is REFUSED at check rather than
merely warned -- the tool for a gate a page may not negotiate down:

```
$ mio check app     # journey  authorize: /p [important]     page p.mho  public: /p
Error  Line 1: this page declares `public: /p`, below the `[important]` floor `authorize: /p` set at
line 2 of app/journey.mho. A page may match or tighten an `[important]` journey class, never fall below it ...
```

At or above the floor the page checks clean. `[important]` is also valid on a `timezone` line (the journey's
zone wins); on a `sector:` line it is refused, as is any label other than `[important]` in that slot.

---

### 3. What a journey can declare, and what cascades

| Declaration | What it does | Cascades to subfolders? |
|---|---|---|
| `connect db as ... from env...` | the database connection every page uses | Yes (run-verified) |
| `shape X ... shape: done` | a shape the pages share | Yes (run-verified) |
| a value (`name value`) | a shared value | Yes (run-verified) |
| `task X ... task: done` | a shared task | Yes (same assembly) |
| `sector: ...` | the compliance floor | Yes, tighten-only (section 5) |
| `limits ... limits: done` | the AI ceiling | Yes (same assembly) |
| an `include` | pulls a shared file in | Yes (part of the journey's statements) |
| `suppress <code> reason "..."` | quiets one advisory diagnostic (section 8) | Yes: a folder journey quiets its folder (check-verified) |
| `public:` / `unlisted:` / `hidden:` / `authorize:` | page classification (access control) | Yes, tighten-only (section 2); declarable in a folder `journey.mho` |

"Run-verified" means a subfolder page was served and shown to inherit it this pass; "same assembly"
means it rides the identical cascade mechanism as the verified ones and is not separately re-run here.

---

### 4. The cascade

**Every ancestor journey from the page's own folder up to the application root applies, root first, each
nearer journey after it, and the page last.** The nearest declaration wins on restatement: a subfolder
**replaces** what it restates, while a name it does not restate stays the root's. Depth is unlimited
within the application root and never reaches an ancestor above it.

Take this tree, served with `mio serve app` (the folder is the application root):

```
app/
    journey.mho          greeting "from-root"
                         rootonly "root-value"
    index.mho            renders {{ greeting }}
    api/
        journey.mho      greeting "from-api"
        status.mho       renders {{ greeting }} and {{ rootonly }}
```

Served and hit with real requests:

```
$ curl http://localhost:8843/
from-root
$ curl http://localhost:8843/api/status
<p>from-api / root-value</p>
```

`/` reads the root journey (`from-root`). `/api/status` gets **both** journeys: `api/journey.mho`
restates `greeting`, so the nearest value wins (`from-api`), while `rootonly` -- which the api journey
never mentions -- **stays the root's** (`root-value`). That is nearest-wins and inherit-what-you-do-not-
restate in one page.

**The value cascade travels with the file, even under `mio run`.** Running a single page still applies
every ancestor journey from the root down, exactly as serving does, so a value declared only in the
root journey resolves normally rather than erroring:

```
$ mio run app/api/status.mho
<p>from-api / root-value</p>
```

`rootonly` reads as `root-value` here: the file inherits the whole chain. Governance travels with the
file.

**The page-classification gates travel with the file too, and `mio run` honors them.** `mio run` has
no sign-in, so a classified page is not run without its gate. Running an `authorize:` or `hidden:` page
directly does not print its body: `mio run` refuses it, names the journey line that classifies it,
points you at `mio serve`, and exits non-zero. A page's gate cannot be shed by running it standalone.
With `authorize: /admin` in the root journey and an `admin.mho` page:

```
$ mio run app/admin.mho

  Refused  app/admin.mho

  admin.mho answers at /admin, and journey.mho line 3 classifies /admin as authorize: (sign in required).
  mio run has no sign-in, so running it here would show the page to anyone who can run the file, with nothing checking who is asking.
  See it the way a visitor does:  mio serve app  (then sign in)
  Run it here anyway, on your own machine:  mio run app/admin.mho --ungated
```

A `hidden:` page refuses the same way, noting that serving it answers `404` to everyone. To see a
classified page the way a visitor does, serve the application -- `mio serve app` -- and make real
requests: `/admin` returns `403` until you sign in, a `hidden:` `/secret` returns `404`. During
development `--ungated` runs the page anyway, with a loud warning that the gate is off; it is a local
override, never a way to serve an ungated page.

**A `hold` value cannot be restated by a nearer journey.** A held value is frozen (see Chapter 5), so a
root `hold greeting` and an `api/journey.mho` that also writes `hold greeting` collide with an
"already held" error and the page fails. For a value you intend a subfolder to override, declare it as a
naked value (`greeting "from-root"`), as the worked example does, not with `hold`.

---

### 5. Security only tightens

This one has its own section because it is the security boundary. **A subfolder may tighten the sector a
parent declared, and may not loosen it.** Three cases, all proven to four levels this pass:

- **Silent inherits.** A subfolder journey that says nothing about the sector runs under the parent's
  sector. It cannot escape a rule by omission.
- **Tightening is accepted.** A subfolder may add levels: root `sector: financial`, subfolder
  `sector: financial, banking` is fine.
- **Loosening is refused, at the moment the application is assembled.** A subfolder that switches to a
  sector not containing the parent's is rejected with a message naming the floor it would have dropped:

```
$ mio serve app          # app/journey.mho: sector: financial ; app/loosen/journey.mho: sector: healthcare
Error  The journey at app/loosen/journey.mho sets sector `healthcare`, which would LOOSEN the sector
`financial` a parent journey already requires. A subfolder journey may only TIGHTEN security, never
relax it: it can add levels under `financial` ... but it cannot drop it or switch to a sector that does
not include it.
```

What this protects against, in plain words: a subfolder quietly stepping outside the rules the
application declared. Before the cascade, a subfolder simply did not get the parent's sector at all,
which is the more dangerous version of the same thing (section 6).

**`mio check` catches this too, since Batch 37.** The cascade's application root is taken from the file
being checked rather than the working directory, so `mio check app` assembles the same cascade the server
does and raises the loosen refusal above -- verified this pass, `mio check` on a loosening app reports the
same "would LOOSEN the sector" error. (An earlier build checked each file in isolation and let a loosening
subfolder pass `mio check`; Batch 37 closed that, so a `mio check` pass now does exercise the sector floor
across the application.)

---

### 6. What changed, and when

**Before build Batch 36 (2026-09-25), nothing a root journey declared reached a subfolder** -- not the
connection, not the shapes, not the sector. A page under a regulated application ran **without its
sector, warning where it should have errored**: a real security hole, not a convenience gap. If you are
reading older material, or an older application, that is the behavior it describes. And if you worked
around the old behavior by copying the `connect` and the shapes into every folder, you can stop: the
cascade carries them now, and the duplication was hiding the seam this chapter makes visible.

---

### 7. Two traps that look like a journey problem

Both were hit by real application lanes, and both look like the journey is broken when it is not.

- **`mio serve <file>` serves one page; `mio serve <dir>` serves the application.** Serving your entry
  file alone loads only that one page and none of the routes, and today it says nothing about the ones
  it did not load, so an application looks half-broken for a reason that is not the journey. Serve the
  folder. (Tracked as `T1-SERVE-INDEX-ONE-PAGE`; Batch 35 adds a note when a file is served and sibling
  routable files exist.)
- **SQLite wants a bare file path in `DATABASE_URL`, not a URL form.** `DATABASE_URL=/data/app.db` (or
  `:memory:`) works; a `sqlite:///...` URL form fails at runtime with "unable to open database file". A
  connection declared in a journey is only as good as the value in the environment. (Tracked as
  `T1-SQLITE-URL-FORM-MISMATCH`.)

---

### 8. Quieting an advisory: `suppress`

Some warnings are advice: a known pattern you have decided to keep, a nudge that does not apply to your
application. Left in the report, a warning you have chosen to ignore is noise, and noise is how a new,
important warning gets missed. So a journey may quiet one advisory, by its code, with the reason on the
line:

```mohio
journey App
    timezone "UTC"
    suppress release_never_held reason "the teaching pages release on purpose"
journey: done
```

The code is the one the warning prints (its `see getmohio.com/codes/<code>` line). The line lives in the
journey, so it is in version control, it travels with the repository, and a reviewer or an auditor reads
it there.

**It cascades like the rest of the journey.** The root `journey.mho` quiets the advisory for the whole
project; a folder's `journey.mho` quiets it for that folder and the folders below it, and not for a page
above it (each verified with `mio check`).

**The silence is never opaque.** `mio check` still says how many advisories it suppressed:

```
  v  page.mho  3 lines . no errors

  1 advisory suppressed by a journey, run `mio check page.mho --show-suppressed` to see it
```

and `--show-suppressed` lists each one, with what it said, where it was suppressed and the reason:

```
  Suppressed  1 advisory, each by a journey line

    release_never_held  Line 2: release does nothing here because x is never held; hold it first if you meant to protect it.
      suppressed at journey.mho:3, reason: the teaching pages release on purpose
```

`mio check --json` carries them too, in a `suppressed` list beside `warnings`, each with its reason.

**Advisory codes only.** Every code has a class, and the class decides. An **advisory** is a heuristic, a
nudge or a matter of style, and may be suppressed. A **governance** code is the enforcement itself
(security, classification, money, a sector's rules) and is never silenced by configuration; an **error**
is never suppressed at all. The published code reference marks each code's class. `mio check` refuses a
`suppress` line:

- with no reason, or an empty one;
- naming an error, or a governance code;
- naming a code Mohio does not have;
- naming a code the same journey already suppresses.

**The sector notice is an advisory.** The notice that a sector makes money routes require an
`Idempotency-Key` (Chapter 44) prints until a journey suppresses it, and the `suppress` line, with its
reason, IS the record that your callers were told. The key itself stays required: suppressing the notice
never turns the `400` off.

```mohio
journey Payments
    timezone "UTC"
    suppress sector_idempotency_key_required reason "every caller sends Idempotency-Key since 6.0.2"
journey: done
```

---

### Common mistakes

- **`unlisted:` (formerly `private:`) is not a gate -- `authorize:`/`hidden:` gate.** `unlisted:` only UNLISTS a
  served path; it returns 200 to anyone who requests the URL. A page that must be protected needs `authorize:`
  (403 until sign-in) or `hidden:` (404). Relying on `unlisted:`, or on an unguessable URL, leaves the page open
  -- the single most common way a restricted page leaks. (`private:` still works as a deprecated alias and warns
  to `unlisted:`; it is the same not-a-gate behaviour under the old name.)
- **Leaving a sensitive page unclassified.** Public is the default, so a page with no classification is served
  to everyone. Restricting a page is something you declare; it never happens by omission.
- **Testing a multi-folder app with `mio run onefile.mho`.** No cascade; use `mio serve app`.
- **Overriding a shared value declared with `hold`.** A nearer journey cannot restate a held value; use
  a naked value for anything a subfolder should override.
- **Assuming `mio serve` is the only place the sector floor is enforced.** Since Batch 37 `mio check app`
  assembles the cascade and catches a loosening subfolder too, so check and serve agree.
