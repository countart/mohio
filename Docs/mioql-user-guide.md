<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md. -->
# MioQL — the Mohio query language

MioQL is how Mohio talks to your database. It is four words: `find`, `check`,
`retrieve`, and `save`. Each one reads like a sentence and does exactly one job.
You declare the connection once at the top of the file, and every query uses it.

```
connect db as sqlite from env.DATABASE_URL
```

After that, `db.<table>` is how you name a table: `db.products`, `db.users`,
`db.orders`.

---

## find — get many rows

`find` returns a list. You name the list, say which table, and add any filters.
The block closes with `find: done`.

```
find items in db.products
    match category to "widgets"
find: done
```

`items` is now the list of matching rows. Read it directly, or use an accessor:

```
items              // the whole list
items.count        // how many rows (a number)
items.first        // the first row
items.last         // the last row
items.first.name   // a field on the first row
items.position.2   // the 2nd row (1-based)
```

A bare `find` with no filter returns the whole table:

```
find everyone in db.users
find: done
```

### Filtering with where

`where` takes one condition. The field comes first, then the test:

```
find adults in db.members where age is above 21
find: done
```

The conditions you can write:

- `where age is above 21`
- `where price is below 100`
- `where age is between 18 and 65`
- `where status is "active"`
- `where status is not "closed"`
- `where name contains "smith"`
- `where name starts "A"`
- `where notes is empty`
- `where notes is not empty`

Quote text values. A bare word is read as a reference, so `where status is active`
(no quotes) looks for a variable named `active` and tells you to quote it if there
is none. That is on purpose: it fails loud instead of matching nothing.

### Filtering with match

`match` is the equality filter, one `field to value` pair per line. Use it when
you are matching exact values:

```
find widgets in db.products
    match category to "widgets"
    match in_stock to "yes"
find: done
```

Multiple `match` lines are ANDed together (all must hold).

### Grouping and paging

Group with `by`:

```
find totals by status in db.orders
find: done
```

Page through large results. The page accessors come back on the list:

```
find orders in db.orders
    up to 20
    page request.page default 1
find: done

orders.page.current    // 1
orders.page.total      // 47
orders.page.has_more   // true
orders.count           // total across all pages
```

Cursor paging works the same way with `cursor from`:

```
find txns in db.transactions
    up to 50
    cursor from request.cursor
find: done

txns.page.next_cursor  // "abc123"
```

---

## check — answer a yes/no or a count

`check` does not return rows. It answers a question. There are three, each with
its own keyword, and each closes with `check: done`.

### check exists — is there a match?

```
check exists found in db.users
    match email to "taken@x.com"
    on.success
        show "taken"
    on.failure
        show "available"
check: done
```

`on.success` fires when a row exists, `on.failure` when none does.

### check count — how many?

Bind the number with `as`:

```
check count as total in db.users
    on.success
        show total
check: done
```

Add a `where` or `match` to count a subset.

### check unique — is this value free?

Signup polarity: `on.success` means the value is available (count is zero).

```
check unique in db.users
    match.unique email to "new@x.com"
    on.success
        show "available"
    on.failure
        show "taken"
check: done
```

Note: existence and count use a keyword (`check exists`, `check count`,
`check unique`). A bare `check found in db.users` is read as a value check
(`check <expression>`), not a database check, so it will not run a query. Always
write the keyword.

---

## retrieve — get one row

`retrieve` returns a single record. Name it, say the table with `from`, and match
on `field to value`. It closes with `retrieve: done`.

```
retrieve member from db.members where email to "amy@x.com"
    on.success
        show member.name
    on.failure
        show "not found"
retrieve: done
```

There is a short one-liner for the simple case, with `to` pairs and no closer:

```
retrieve member from db.members where email to "amy@x.com"
```

---

## save — write a row

```
save to db.products
    name "Alpha"
    category "widgets"
save: done
```

---

## Using MioQL inside a request

All of these work directly inside a `request for` handler. Find a list, then
respond with it:

```
connect db as sqlite from env.DATABASE_URL
listen for
    request for sh.Catalog at /catalog
        find items in db.products
            match category to "widgets"
        find: done
        give back ok items.count
    request: done
listen: done
```

`give back ok items` returns the rows. `give back ok items.count` returns the
number. Inside the handler, the list and its accessors are yours to use.
