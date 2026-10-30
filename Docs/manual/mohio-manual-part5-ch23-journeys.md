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

**One thing only the wrapper can declare today: page classification.** `public:`, `private:`, `hidden:`
and `authorize:` are wrapper alternatives and do **not** parse in a bare folder `journey.mho`:

```
$ mio check journey.mho          # a folder journey.mho containing only:  private: /docs
Syntax error
  1 | private: /docs
```

So a folder `journey.mho` cannot classify a path today. This is a known gap
(`T1-CLASSIFICATION-WRAPPER-ONLY`), still open (verified: a bare `private: /docs` still fails to parse).

**Classification now follows the tighten-only rule across the cascade.** Where classification IS declared (in a
wrapper journey), a nearer journey can only make a path STRICTER, never looser, in the strictness order
`public` < `private` < `authorize` < `hidden` (`hidden` is the strongest). A subfolder journey that would weaken a
parent's classification for the same path, or a broader path that would swallow a narrower stricter one beneath
it, is refused. This is the same tighten-only rule section 5 states for the sector, now extended to classification
(build 2026-10-04), enforced when the app is assembled (the path `mio run` and `mio serve` use). Before this, a
subfolder could silently make a hidden path public.

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
| `public:` / `private:` / `hidden:` / `authorize:` | page classification | Wrapper-only; not declarable in a folder `journey.mho` today |

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

**The cascade is a `mio serve` feature, keyed on the application root.** Serving the folder
(`mio serve app`) is what walks the ancestors. Running a single file with `mio run app/api/status.mho`
does **not** cascade -- it applies only that file's own-directory journey, so a value declared only in
the root journey reads as an unknown variable:

```
$ mio run app/api/status.mho
Runtime error: show refers to an unknown variable 'rootonly'.
```

So test a multi-folder application with `mio serve app`, not by running one file.

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

- **Testing a multi-folder app with `mio run onefile.mho`.** No cascade; use `mio serve app`.
- **Overriding a shared value declared with `hold`.** A nearer journey cannot restate a held value; use
  a naked value for anything a subfolder should override.
- **Assuming `mio serve` is the only place the sector floor is enforced.** Since Batch 37 `mio check app`
  assembles the cascade and catches a loosening subfolder too, so check and serve agree.
- **Classifying from a folder `journey.mho`.** `private:` and its siblings are wrapper-only today.
