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

### `connect` -- one line, once

```mohio
connect db as sqlite from env.DATABASE_URL
```

Names which database the rest of the file talks to. See `Docs/guides/mioql-user-guide.md` and
`Docs/guides/databases-supported.md` for the other backends and the access qualifiers
(`readonly`/`writeonly`/`readwrite`).

### `save`, `update`, `remove`, `retrieve` -- already verified, see MioQL guide

`Docs/guides/mioql-user-guide.md` covers these four with real, run examples: `save` (write a row),
`update` (change an existing row by `match`), `remove` (delete every row a `match` finds),
`retrieve` (get one row). Not repeated here.

### `save all` -- write a whole collection in one block

```mohio
connect db as sqlite from env.DATABASE_URL

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

---

**Also in this Part, not covered here (see the MioQL guide):** `where`/`match` filtering,
ordering and paging, aggregation (`summarize`/`average`/`maximum`/`minimum`), and
`try`/`on.failure` for a genuine database failure (Chapter 15 covers the same construct's day-two
trap).
