<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# What is not built yet

*An honest, developer-facing list of what Mohio does not do yet, so you can design around it rather than
discover it mid-build. Grouped by what you are trying to DO, not by our internal tags, and established by running
rather than read off a backlog. Almost every item has a way to do the thing today; that "way today" is the part
that makes this page useful rather than discouraging.*

Two lanes have already lost time to gaps that were real but undocumented: one wrote its test suite in Python
because `mio test` was undocumented (it exists, see the CLI reference), and one duplicated logic across pages
because a task in a journey is callable from every page under it (it is, see the Tasks chapter). This page exists
so the next lane does not.

## In the browser (MioScript)

The client can do a great deal already (listen for events, `send`/`request`, `check`, `put`/`inject`, validate,
navigate). What it cannot do yet all sits around ONE problem: a server-rendered fragment arrives and something in
it must be interactive.

- **Bind a listener to an element that a fragment inserted later.** A `listen for` binds only to elements present
  when the page's script initializes; an element a fragment injects afterward is not bound.
- **Read a value the server put on a rendered element** (a transaction reference on a row, say). MioScript reads
  `value`, `key`, `x`, `y` off the listened element, not an arbitrary rendered value.
- **Activate another control from a listener** (a rendered shortcut opening a persistent panel).
- **A dynamic `send`/`request` destination.** Both take a literal URL, not a computed one.

**The way today:** keep the interactive markup Mohio-rendered and present when the page loads, rather than
inserting interactive controls into a fragment after the fact; and use fixed, typed routes for `send`/`request`
(which is usually the better design anyway, not a workaround). In a fully converted banking application the only
remaining hand-written JavaScript was a ~2.4 KB bridge covering exactly this fragment-interactivity gap, so the
gap is small and bounded, not a wall.

## Talking to the database

Mohio's queries work, but two things happen AFTER the rows come back rather than in SQL, which matters only at
scale:

- **Ordering and paging happen after the fetch, not pushed into SQL.** For a modest table this is invisible. The
  measured boundary (langref lane): a sorted page starts to pass roughly 500 ms once about 100,000 rows match,
  because the rows are fetched and then sorted and sliced in Mohio.
- **Range filters fetch, then filter.** A range condition brings rows back before narrowing them, for the same
  reason.

**The way today:** for the row counts most applications actually hit, this is fine and you need do nothing. For a
genuinely large table, keep the matched set small with an indexed equality `match` before you sort or page, and
be aware the sort/slice is in-process. Pushing `LIMIT`/`OFFSET` and ordering into SQL (which needs typed columns
first) is designed and ruled "do it once, do it right," and is the fix when it lands; it is not built yet.

## The mobile surface

`screen`, `layout`, `mioapp` and the device permissions are declared in the grammar and NOT built. They refuse
loud rather than doing nothing, so you cannot ship one by accident. Verified: `screen "Home"` and `layout "main"`
each report "declared but NOT built yet -- it arrives with the mobile surface, so today it does nothing"; `mioapp`
is the native mobile-target service root (Phase 3), parsed and validated but with no interpreter wiring, so a bare
`mioapp` is refused as a service root used as a variable and a `mioapp.*` call reports it is not executable in this
build.

**The way today:** build the web surface, which is fully built: `render` for markup, `journey` for what a folder
of pages shares, MioScript for behavior. The mobile surface arrives later; nothing you build on the web surface
is wasted when it does.

## Two standalone handlers not built yet

The built request handlers are `listen for sh.X` (a POST), `request for sh.X` (a GET), and `connection` (a websocket).
Two more forms parse as standalone handlers but are NOT built, so do not reach for them yet (verified by
running):

- **`change to sh.X`** (a stored-record change) refuses at both `mio check` and `mio run`: "change to sh.X is
  declared in the grammar but not built in this release."
- **`from <connector>`** (a webhook, `from NAME / when <event> / ... / from: done`) now refuses at both `mio
  check` and `mio run` (verified): "from <connector> is declared in the grammar but not built in this release. It
  refuses at run rather than silently do nothing, so this names it before you deploy." For an inbound webhook
  today, use a `listen for sh.X` POST handler with `verify signature using env.YOUR_SECRET`.

## Words that refuse as "declared, not built yet"

A number of forms the grammar knows are not implemented, and they REFUSE with a "declared but not yet built"
message rather than silently doing nothing. That is deliberate, and telling that refusal apart from a mistake is
worth understanding: see [Why Mohio refuses things](mohio-manual-why-mohio-refuses.md), which explains the three
kinds of refusal (you wrote it wrong / declared and not built yet / belongs on a different verb) so you know,
when you hit one, whether to fix it, wait on it, or reach for a different verb. This page does not repeat the
list; the refusal you hit will name itself.

## Audit anchoring

Anchoring is often described as simply unbuilt. That is wrong, and the honest statement is stronger: the VERIFY
half exists and works. `mio audit verify-anchors <db> --anchors f.json` checks the live audit chain against
externally held anchors and detects both TRUNCATION and wholesale REPLACEMENT (proven by running). What is NOT
built is the automated PUBLISH-and-sign step in between, which a bank would want to control anyway.

**The way today:** run `mio audit head <db>` to print each log's chain head, hand that head to an external party
you trust to hold it, and later run `verify-anchors` against the anchors they hand back. The one caveat the
command itself prints: it trusts the anchors as authentic and does not verify their signatures, which is the
caller's job upstream.

## The short version

The web surface, the database verbs, MioScript's behavior layer, the audit chain and its verification, and the
CLI are built and in daily use. The gaps are: fragment-driven client interactivity (small, bounded, with a
Mohio-rendered-markup way around it), SQL-side ordering/paging at large scale (fine below ~100,000 rows), the
mobile surface (declared, refuses, arrives later), a set of not-yet-built words that refuse rather than
mislead, and the automated anchor publish step (the verify half already works). Design with those in mind and
nothing on this page should surprise you mid-build.
