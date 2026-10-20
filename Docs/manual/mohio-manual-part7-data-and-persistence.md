<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Part VII -- Data and Persistence, MioQL

## Data and Persistence -- the write and read verbs

*Spans this Part's Chapter 22 (Connecting), 23 (Finding and Retrieving), and 24 (Saving,
Updating, Removing) together; a future split into three files is a refinement, not a rewrite.
Every example below ran against a real SQLite database: checked with `mio check`, run
with `mio run`, and read back from the database afterward, not assumed from the response alone.
`retrieve`, plain `save`, `update`, and `remove` are already fully covered with verified examples
in `Docs/guides/mioql-user-guide.md`; reused by reference below rather than re-derived. This chapter adds
the verbs that guide is missing.*

---

## The data-driven principle

A Mohio application should be data-driven whenever the information can reasonably be data. Code describes how
data is governed and presented; it does not become an alternate datastore. When a list of things lives in the
code as branches or repeated markup, ask whether it is really data wearing code's clothes, and if it is, move
it to a table and let the code describe how it is read, ordered and shown. This principle was reached by an
application lane and endorsed by Ron; it is recorded here because it is about how the language should be used,
not only how one demo was built.

The principle reads as advice, so here is the worked example that produced it and what it bought. An
application lane had its demo scenarios in code. It moved them into a table: MioQL reads and orders them, the
page renders however many rows exist, ONE MioScript listener on a class selector serves every scenario
control, the chosen id is posted, the server retrieves that row, and its fields populate the form. Adding a
fourth scenario is now one database row and NO code change at all. An unknown scenario returns 404 through the
ordinary empty-query conditional path (`check ... when ... is empty`), not a special case written for it. The
code shrank to the part that is genuinely logic (how a scenario is read, ordered and shown), and the part that
was really data (which scenarios exist) became data.

The move proved five seams, each untested until this demo and all of which held:

- A top-level page `render` can consume a MioQL result.
- A server route can return a retrieved structured record.
- MioScript can read that response's dotted fields (`result.field`).
- A single class-selector listener (`listen for click on .scenario`) can serve repeated, database-rendered
  controls, however many rows exist.
- One signed form can represent a variable-length catalog, because the catalog is rows, not fixed fields.

No scenario value and no banking decision is duplicated in browser code. That is the payoff the principle
names: the browser reflects data and posts a choice, the server governs and decides, and the catalog that
drives both is a table. When the next feature tempts you to add a branch per case, check whether the cases are
data first.

**A parse-time corollary, recorded as an observation that still needs one more measurement.** Parse cost is
roughly flat per line, observed at about 42 to 46 milliseconds a line whatever the line contains, so a line of
data held in code costs about the same to parse as a line of logic. That means moving data out of code and into
a table does not just read better, it removes those lines from the parse entirely, which makes the data-driven
principle a parse-time lever as well as a design one. This is an observation, not a claim. The magnitude of the
saving is deliberately left unstated, because the measurement nobody has taken yet is how much of a real
program is data rather than logic: without that proportion, the per-line cost cannot be turned into a figure
for what the principle saves on a whole program. Until that measurement exists, this chapter should not quote a
number for the saving; the direction is sound, the size is unmeasured.

---

### `connect` -- choose and connect an engine, one line, once

```mohio
connect db from env.DATABASE_URL
```

`connect` names which database the rest of the file talks to. `db` is the name you use afterward (`save to
db.orders`, `find rows in db.orders`). The source is ALWAYS an environment variable (`env.X`) or a secret
(`secret.X`), never a literal connection string, so a credential never sits in your source. A literal is refused
at check with `Hardcoded connection source. A connect source must be env.X or secret.X, never a literal string`.

**The engines.** SQLite (built in), PostgreSQL, MySQL/MariaDB, MongoDB (experimental), and Oracle (supported).
The same `save`, `find`, `retrieve`, `update` and `remove` work the same across all of them.

**Two ways to name the engine.** Give it explicitly with `as`, or leave `as` out and let the ADDRESS decide:

```mohio
connect db as postgres from env.DATABASE_URL
```

```mohio
connect db from env.DATABASE_URL
```

With no `as`, Mohio reads the engine from the address: a `postgres://` URL connects Postgres, a `mysql://` URL
MySQL, a file path or `:memory:` SQLite (verified by running).

**A mismatch refuses, loud.** If you write `as mysql` but the address is a `postgres://` URL, Mohio refuses at
RUN rather than connect to the wrong database, and names both ways to fix it (verified):

```
connect db as mysql from env.DATABASE_URL, but that address is a postgres connection (postgres://...).
  The `as mysql` and the address name different kinds of database, so Mohio refuses rather than connect to the wrong one.
  To fix, either leave out the `as mysql` and let the address decide the engine:
      connect db from env.DATABASE_URL
  or point DATABASE_URL at a SQLite file if that is what you meant.
```

**An unknown engine is refused at CHECK**, naming the ones Mohio knows (verified):

```
'banana' is not a database Mohio knows. To proceed: use sqlite, postgres, mysql, mongodb or oracle.
```

**Oracle is supported,** verified on Oracle AI Database 26ai; 19c verification follows after the tag. Connect with
`connect db as oracle from env.X`; the data verbs, the connection pool and the audit trail all run on Oracle.

For the full engine list, the environment variable each reads, and the access qualifiers
(`readonly`/`writeonly`/`readwrite`), see `Docs/guides/databases-supported.md`. For the architect or DBA view,
how many sessions Mohio opens, the privileges the account needs, what it creates in your schema, how data is
stored, and the Oracle specifics, see `Docs/guides/running-mohio-on-your-database.md`.

### A table with no shape: columns from what you write, all text, empty until written

A table that no shape describes takes its columns from what the program WRITES to it: every field named across
its write sites becomes a column, each stored as TEXT, plus an auto `id` integer key. It needs no `create table`
and no schema declaration; writing to it is what defines it. Before any row is inserted, a read of that table is
EMPTY (count 0), not an error. Verified by running:

```mohio
connect db as sqlite from env.DATABASE_URL
find rows in db.notes
find: done
show ("notes before any write: " & rows.count)
save to db.notes
    title "hello"
    body "world"
save: done
find more in db.notes
    where title is "hello"
find: done
show ("notes after one write: " & more.count)
```

```
notes before any write: 0
notes after one write: 1
```

The `notes` table reads `0` before the save and `1` after it, and the columns it was given are exactly the ones
the program writes, `title` and `body`, both TEXT, beside the auto `id`. Two edges, both verified: a read of a
table the program NEVER writes errors (`db_error: no such table: ...`), because nothing ever defined it; and a
`match` or `where` on a column no write names fails loud, naming the columns that exist (`Field 'kind' does not
exist in db.notes ... The columns on db.notes are: title, id`). So a shapeless table is shaped by its writes,
and a read before the first row is a clean empty, not a missing-table error, as long as the program writes to it
somewhere. A table a shape describes (`<name> as table` with typed fields) gets real typed columns instead; see
the typed-storage note in `mohio-manual-part9-ch37-sqlite-to-postgres.md`.

### `save`, `update`, `remove`, `retrieve` -- already verified, see MioQL guide

`Docs/guides/mioql-user-guide.md` covers these four with real, run examples: `save` (write a row),
`update` (change an existing row by `match`), `remove` (delete every row a `match` finds),
`retrieve` (get one row). Not repeated here.

### `save all` -- write a whole collection in one block

```mohio
connect db from env.DATABASE_URL

shape Product
    name as text required
    category as text required
shape: done

create alpha as sh.Product
    name "Alpha"
    category "widgets"
create: done

create beta as sh.Product
    name "Beta"
    category "gadgets"
create: done

items as list alpha, beta

save all to db.products from items
save: done

find rows in db.products
find: done
show ("count: " & rows.count)
```
```
count: 2
```

`save all to <table> from <collection>` writes every item in a list as its own row. Build the
list first -- from `create`d shape instances, as above, or from anywhere else a list comes from.
The closer is `save: done`, not `save all: done` -- `all` is a modifier on the opener, not part of
the closer's name.

### `save ... unless <field> exists` -- skip if it's already there

```mohio
save to db.products unless name exists
    name "Alpha"
    category "widgets"
save: done

save to db.products unless name exists
    name "Alpha"
    category "should not appear again"
save: done

find rows in db.products
find: done
show ("count: " & rows.count)
```
```
count: 1
```

The second `save` is skipped -- a row with `name "Alpha"` already exists. Name one or more fields
after `unless`; the check runs before the write, not as a database-level constraint.

### `save or update` -- write it, or change it if it's already there

```mohio
save to db.products
    name "Alpha"
    category "widgets"
save: done

save or update db.products
    match name to "Alpha"
    category "updated-widgets"
save: done

save or update db.products
    match name to "Nobody-yet"
    category "brand-new"
save: done

find rows in db.products
find: done
repeat each row in rows
    show (row.name & " -- " & row.category)
repeat: done
```
```
Alpha -- updated-widgets
Nobody-yet -- brand-new
```

`match` decides which row, the same way it does for a plain `update`. If a row matches, its named
fields change; if none matches, a new row is written instead -- verified both ways above: `Alpha`
was updated in place, `Nobody-yet` was inserted fresh.

### `remove.all` -- clear an entire table

**`remove all` with a space is refused, on purpose** -- verified: `mio check` names it directly,
*"'remove all' (with a space) isn't valid -- it would silently delete nothing."* Deleting rows
that match a condition is plain `remove` with `match` (see the MioQL guide), which already deletes
every matching row, not just one. Clearing an ENTIRE table with no condition at all is the dotted
form, `remove.all`:

```mohio
save to db.products
    name "Alpha"
    category "discontinued"
save: done

save to db.products
    name "Beta"
    category "discontinued"
save: done

save to db.products
    name "Gamma"
    category "active"
save: done

remove from db.products
    match category to "discontinued"
remove: done

find rows in db.products
find: done
show ("count after remove: " & rows.count)

remove.all from db.products
remove.all: done

find rows in db.products
find: done
show ("count after remove.all: " & rows.count)
```
```
count after remove: 1
count after remove.all: 0
```

### `modify` -- change every row matching a condition, in bulk

```mohio
save to db.products
    name "Alpha"
    category "widgets"
save: done

save to db.products
    name "Beta"
    category "gadgets"
save: done

modify every product in db.products where category is "widgets"
    apply product
        category "premium-widgets"
    apply: done
modify: done

find rows in db.products
find: done
repeat each row in rows
    show (row.name & " -- " & row.category)
repeat: done
```

`modify every <name> in <table> where <condition>` opens the block; the `apply <name>` /
field-value lines / `apply: done` inside it names what actually changes. `modify` with no `apply`
body naming a field is refused -- verified: *"modify names no field to change, so there is
nothing to write."* Run against a table with an `Alpha` (category `widgets`) and a `Beta`
(category `gadgets`), only `Alpha`'s category changes:
```
Alpha -- premium-widgets
Beta -- gadgets
```

### Finding rows: filters, ordering, and paging

`find` reads rows; `retrieve` reads one. You filter with `where` / `match`, order with `order.up by` /
`order.down by`, and page with `up to` and `paginate by`. The filtering and ordering forms have run examples in
`Docs/guides/mioql-user-guide.md`; this section covers paging and what-runs-where, both of which changed for 6.0.0.

**Paging: `up to N` is the page size, `paginate by PAGE` is which page.**

```mohio
connect db from env.DATABASE_URL
shape Item
    items as table
        name as text
shape: done
save to db.items
    name "a"
save: done
save to db.items
    name "b"
save: done
save to db.items
    name "c"
save: done
find rows in db.items
    order.up by name
    up to 2
    paginate by 1
find: done
show "rows on this page: " & rows.count
show "total rows: " & rows.page.count
show "this page: " & rows.page.current
show "total pages: " & rows.page.total
show "a next page?: " & rows.page.more
show "page size: " & rows.page.size
```

`up to 2` sets the page size to two, and `paginate by 1` serves page one. Output (verified by running):

```
rows on this page: 2
total rows: 3
this page: 1
total pages: 2
a next page?: true
page size: 2
```

The page facts live on `<result>.page`: `count` (total matching rows), `current` (this page, 1-based), `total`
(total pages), `more` (is there a next page), `size` (the page size), plus `next` and `prev`. `cursor` is
reserved; keyset pagination is a later unit and reads empty today.

**`paginate by` takes a value, not just a literal.** The page number can come from the request, so a real
endpoint pages on `?page=`:

```mohio
find rows in db.items
    up to 20
    paginate by request.page default 1
find: done
```

`paginate by request.page` reads the page from the request; `default 1` serves page one when the request omits
it. A MISSING value takes the `default`; an INVALID value (not a whole number, or below one) FAILS LOUD and is
never clamped (verified: `?page=banana` is refused by the shape's `int`, `?page=0` refuses, neither silently
becomes page one). A literal still works unchanged (`paginate by 1`).

**The old names are retired and refuse, by name.** `has_more`, `per_page`, and `next_cursor` were renamed;
reading one refuses at check and at run, naming its replacement (verified):

```
'rows.page.has_more' is retired -- it was renamed to 'rows.page.more'. Read 'rows.page.more' instead.
```

An unrecognized page field (a typo) refuses with a did-you-mean rather than handing back a silent empty value.

**What runs in the database, and what still runs in Mohio memory.** An equality filter and native joins run as
one query in the database. Today non-equality filters (ranges), text filters, datetime filters, match-any,
no-match, ordering, and paging whenever one of those is present still run in Mohio's process after the rows are
fetched. The pushdown project is moving each of these into the engine. The full, current split is in the database
guide's "How Mohio reads and joins your data" section (`Docs/guides/running-mohio-on-your-database.md`).

### Joins -- pulling related rows with `with`

A `find` can pull a parent row AND its related rows together, with a `with` block. The related rows
come back as a nested list on each parent, and Mohio fetches them in ONE database query, never one
query per parent.

```mohio
connect db from env.DATABASE_URL

shape Member
    members as table
        name as text
shape: done

shape Order
    orders as table
        member_id as int
        amount as USD
shape: done

save to db.members
    name "Aria"
save: done
save to db.members
    name "Bo"
save: done
save to db.orders
    member_id 1
    amount "10.00"
save: done
save to db.orders
    member_id 1
    amount "20.00"
save: done

find rows in db.members
    with purchases from db.orders
        match member_id to rows.id
    with: done
find: done

repeat each member in rows
    show member.name & " has " & member.purchases.count & " orders"
    repeat each order in member.purchases
        show "  order: " & order.amount
    repeat: done
repeat: done
```

The `with purchases from db.orders` block names the related table, and its own `match` line says how
a child joins its parent: `match member_id to rows.id` reads "the order's `member_id` equals the
member's `id`." The children arrive under the name you gave the block, here `purchases`, as a nested
list you walk with `repeat each`. Run against Aria (two orders) and Bo (none):

```
Aria has 2 orders
  order: 10.00
  order: 20.00
Bo has 0 orders
```

Bo has no orders and is STILL returned, with an empty `purchases` list. That is what `with` means: a
LEFT join, every parent returned whether it has children or not.

**`with.required` -- only parents that have a match.** Swap `with` for `with.required` and a parent
with no related rows is left out entirely (an INNER join):

```mohio
find rows in db.members
    with.required purchases from db.orders
        match member_id to rows.id
    with: done
find: done
```

Against the same data that returns Aria alone; Bo, with no orders, is dropped.

**`up to N` bounds the children, per parent.** Put `up to N` INSIDE the `with` block to cap how many
related rows each parent carries. Each member below carries at most one order, and a member with none
is still returned:

```mohio
find rows in db.members
    with purchases from db.orders
        match member_id to rows.id
        up to 1
    with: done
    order.up by name
find: done
```

**Ordering and paging count PARENTS, not joined rows.** `order.up by` and `up to N` written at the
find level (outside the `with` block) act on parents. `up to 2` returns two members, each with ALL of
its orders, not two joined rows:

```mohio
find rows in db.members
    with purchases from db.orders
        match member_id to rows.id
    with: done
    order.up by name
    up to 2
find: done
```

That returns Aria, with both her orders, and Bo: two parents. `paginate by N` pages the same way, a
page of N is N parents with their children, and the total it reports is the parent count, so a parent
is never split across a page boundary. A page and its count read inside one consistent snapshot, so
the count cannot drift from the page under a concurrent write.

**Two or more lists on one parent (side-by-side).** A find can carry several `with` blocks. Each independent
list is fetched by its own query, all inside one consistent read, and nested under its own name:

```mohio
find rows in db.members
    with purchases from db.orders
        match member_id to rows.id
    with: done
    with contacts from db.contacts
        match member_id to rows.id
    with: done
find: done
```

Each member then carries both a `purchases` list and a `contacts` list (verified).

**Nested lists (a chain).** A `with` block can hold its own `with`, so a parent carries children that carry
grandchildren, still one pushed-down query:

```mohio
find rows in db.members
    with purchases from db.orders
        match member_id to rows.id
        with lines from db.items
            match order_id to purchases.id
        with: done
    with: done
find: done
```

`rows.first.purchases.first.lines` reaches an order's line items (verified). A compiler notice appears at
nesting depth 3 and beyond, not a refusal.

**Joins on `retrieve`.** `retrieve` takes a `with` the same way `find` does, for one record and its children:

```mohio
retrieve one from db.members
    with purchases from db.orders
        match member_id to one.id
    with: done
retrieve: done
```

`one.purchases` holds the retrieved member's orders (verified).

**`with.all` -- include the unmatched rows, as a second list.** `with` (LEFT) returns every parent;
`with.required` (INNER) drops a parent with no match; `with.all` returns the matched parents AND the orphan
children (rows whose parent is missing), as TWO SEPARATE lists, never merged through a pseudo-parent:

```mohio
find rows in db.members
    with.all purchases from db.orders
        match member_id to rows.id
    with: done
find: done
show "matched parents: " & rows.count
show "unmatched orphans: " & rows.unmatched.count
```

`rows` is the matched parents, each with its children; `rows.unmatched` is this page's orphan child rows. Under
paging the counts span both lists: `rows.page.count` is the combined total, `rows.page.unmatched.count` is the
total orphan count across the whole sequence, and `rows.page.unmatched.start` is the 1-based page the orphans
first appear on. Matched records page first, then the orphans fill what is left, so a page can be all-matched,
all-unmatched, or a mix. Verified by running: two matched parents and one orphan give a combined total of 3.
`with.all` is top-level only.

**Across engines.** Joins run on SQLite, PostgreSQL and MySQL; on MongoDB, `with` and `with.required` push down
to a `$lookup`. On MySQL `with.all` is emulated, and MongoDB refuses `with.all`.

### What goes in `match`, what goes in `where`, and what is not a join at all

A join answers one question: given this record, WHICH RECORDS BELONG TO IT. The `match` line says HOW the two
connect, and that connection can be ONE FIELD OR SEVERAL. Every `match` pair connects to a FIELD of the record
being found (`rows.<field>`); a FIXED VALUE goes in a `where` inside the `with`, never in `match`:

```mohio
find rows in db.members
    with purchases from db.orders
        match member_id to rows.id, tenant_id to rows.tenant_id
    with: done
find: done
```

Two fields connect here, written as comma pairs on one line (or as stacked `match` lines); BOTH must match.
Verified by running. Keep `match` to field-to-field connections.

**Other narrowing goes in `where`.** To keep only SOME of the connected records, by a fixed condition, add a
`where` inside the `with` block. The connection stays in `match`:

```mohio
find rows in db.members
    with purchases from db.orders
        match member_id to rows.id
        where amount is above 15
    with: done
find: done
```

Verified by running: a member with a 10 and a 20 order comes back with ONE purchase, the 20.

**Only the records that have a matching row in another table.** To filter a list to just those, reach for
`with.required`: it keeps only the parents that HAVE a matching child, and a `where` inside it narrows WHICH
child counts. "Only the documents the current person may access," where access lives in a separate table:

```mohio
find rows in db.documents
    with.required grant from db.access
        match document_id to rows.id
        where person_id is session.person
    with: done
find: done
```

Verified by running: with an access row for one of two documents, only that document comes back. The `match` is
the connection (an access row points at the document), the `where` says whose access, and it runs as ONE
pushed-down query, not a fetch-then-filter in Mohio. `with.required` brings the matching access rows along under
`grant`; if you only want the documents, ignore that list.

**A yes-or-no guard for ONE record is `check exists`,** separate from any join:

```mohio
check exists allowed in db.access
    match document_id to request.doc
    when empty
        give back [403] "no access"
    otherwise
        give back [200] "allowed"
check: done
```

Verified by running. Use it to gate an action on one record, not to filter a list.

**The escape hatch is raw `sql`.** A `find` or `retrieve` block may hold a raw `sql` statement for anything the
word-based forms do not express. It is written for ONE engine, so unlike the forms above it does not move across
backends unchanged.

A cleaner, non-join way to filter by existence WITHOUT bringing the related rows along (a `where exists in db.X
with match ...` form) is designed but post-tag, tracked as `T1-EXISTENCE-FILTER`; `with.required` covers the
need today.

### Every `match` condition is ANDed, on every verb and engine

A lookup with more than one `match` (or more than one `where`) returns a row only when EVERY condition holds. The
conditions are ANDed: never ORed, and never applied in part. That is true on `find`, `retrieve`, `remove` and
`modify`, and on every engine. Verified by running, with two people seeded (Alice in NYC, Bob in LA):

```mohio
retrieve person from db.people
    match name to "Alice"
    match city to "LA"
    when person is empty
        show "no row: both conditions must hold"
    otherwise
        show ("found " & person.name)
retrieve: done
```

```
no row: both conditions must hold
```

Alice exists and an LA row exists, but no single row is both, so the retrieve comes back empty. **What changed in
6.0.0, and what to check:** a lookup that in an older build matched on only SOME of its conditions now applies all
of them. A query that silently returned extra rows returns fewer, and one that leaned on a condition the old path
dropped may now come back empty. Run your tests over any lookup that carries more than one `match` or `where`.

### Joins and `with.required` work on a protected key

A `match` or a `with.required` may connect on a field tagged `[phi]` or `[pci]`. The connection runs on the
field's deterministic blind index, not its plaintext, so an encrypted key still joins correctly. Verified by
running: patients and labs each keyed by a `[phi]` `patient_key`, joined with `with.required`, returned only the
patient that has matching labs, with its lab rows, and excluded the patient with none, although the key is stored
as randomized ciphertext.

Each joined table keeps its own classification: an untagged column is not masked because a same-named column on
the other side of the join is tagged. See `Docs/guides/compliance.md` (the egress gate).

```mohio
find rows in db.patients
    with.required labs from db.labs
        match patient_key to rows.patient_key
    with: done
find: done
```

### A required join holds regardless of the joins beside it

`with.required` keeps only the parents that HAVE a matching child, and it does so no matter what other
`with` joins sit beside it on the same find. A sibling ordinary join cannot widen or weaken a required
one. This matters for isolation: a required join on an access or tenancy table keeps one organization's
rows out of another's, and a second join alongside it does not open a crack. Verified by running: two
organizations seeded, a find with a `with.required` membership join filtered to one org AND an ordinary
`with runs` join beside it returned only the one org's member; the other org's row was excluded.

```mohio
find techs in db.members
    match role to "technician"
    with.required clinic from db.memberships
        match person_id to techs.person_id
        where organization_id is org
    with: done
    with runs from db.runs
        match assigned_person_id to techs.person_id
        where status is "due"
    with: done
find: done
```

**Paging that shape refuses today.** Adding paging (`up to`, `skip`, or `paginate by`) to a find that has
a `with.required` join BESIDE another `with` join refuses at run, because each join would page its own
rows and the pages would disagree. It names the gap and the workaround (verified by running):

```
join_form_unavailable: `techs` pages or caps its rows and has a `with.required` beside another `with`. Paging that shape is not built yet (`T1-JOIN-REQUIRED-SIBLING-PAGING`), because each join would page its own rows and the pages would disagree.
  Read it without `up to`, `skip` or `paginate by` for now, or make the required join the only `with` on this find.
```

This is a known gap, tracked as `T1-JOIN-REQUIRED-SIBLING-PAGING`; the independence guarantee above is
unaffected, and paging works on a find whose only join is the required one.

### A table or column name is a name, never data

A table or column name is a LITERAL identifier, fixed in the source. It is never read from a variable, a request
field or a stored value: data flows through values, never into the shape of the query. Trying to build an
identifier from data does not quietly work (verified by running):

- `save to db.{{request.t}}` and `save to db.(request.t)` do NOT parse: there is no interpolation in an identifier
  position, so the attempt fails at check, not silently.
- `save to db.tname`, where `tname "players"` is a variable, saves to a table literally named `tname`, NOT to
  `players`. The word after the dot is the name itself, never a lookup.

So an identifier is a name you wrote, or it is refused; it is never computed from input. This is the data-is-data
principle at the schema level, and it is why a query cannot be bent by a value a caller sends. For anything the
fixed forms cannot express, the escape hatch is raw `sql` (written for one engine), not a dynamic name.

---

**Also in this Part, not covered here (see the MioQL guide):** `where`/`match` filtering,
ordering and paging, aggregation (`summarize`/`average`/`maximum`/`minimum`), and
`try`/`on.failure` for a genuine database failure (Chapter 15 covers the same construct's day-two
trap).
