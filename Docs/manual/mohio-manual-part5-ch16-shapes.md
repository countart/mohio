<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Part V -- Shapes, Forms, and Validation

## Chapter 16: Shapes

*Verified: checked with `mio check`, run and served with `mio run`/`mio serve`, real
HTTP requests, output read back. For the no-classes mental model shapes fit into (shape describes,
task acts), see `Docs/guides/mohio-mental-model-no-classes.md`.*

---

### `shape` -- describing data

```mohio
shape Product
    name as text required
    category as text required
shape: done
```

A shape is a declaration, not a table and not a class. It describes what a piece of data is
allowed to look like -- a form's fields, an API response, a database record, an AI decision's
result -- declared once and referenced everywhere via `sh.<Name>`. A shape never acts; it only
describes.

### Field rules: `required`, `min`, `max`, `default`

```mohio
shape Order
    item as text required
    quantity as int default 1
    price as decimal required
shape: done
```

`required` refuses a missing field. `min`/`max` bound a text field's length (characters, both
ends) or a number's range. `default` fills a field in when it is absent, rather than treating it
as missing.

### Where these rules actually enforce, verified precisely

**On the request path -- a `new`/`request for sh.X` route -- every rule enforces, for real,**
already shown end to end in `Docs/guides/mohio-first-app-tutorial.md` (a real 422 naming each bad
field). Confirmed again with `Order` above, served for real:

```bash
curl -X POST http://localhost:8080/order -d '{"item":"widget"}'
# 422 {"errors": {"price": "Price is required."}}
```

**A field with a default is never wrongly flagged missing, because defaults apply BEFORE the
rules run.** Same shape, `price` supplied, `quantity` left out entirely:

```bash
curl -X POST http://localhost:8080/order -d '{"item":"widget","price":"9.99"}'
# 200 {"message": "saved 1 x widget at 9.99"}
```

`quantity` defaulted to `1` with no complaint, and `price` -- sent as the JSON string `"9.99"` --
arrived as a real decimal the response could do math with, because the shape declares the type and
the request-binding boundary is where text becomes a number. A value that cannot convert is a 422,
never a crash.

**None of this applies to a direct `save`/`update` against a FLAT shape -- one referenced only as
`sh.X`, that never names which table it governs.** Verified: declaring `quantity as int default
1` in a flat `shape Order` and then writing `save to db.orders / item "x" / price 5 / save: done`
(no `quantity` line at all) does not fill in `1`. A flat shape's rules are a request-boundary
contract, not a table-level one -- `mio check` says so at that exact line: *"`default` on field
`quantity` is applied when a request is bound to this shape... It is NOT applied on a direct
`save`/`update`, because a write names a table and a table carries no shape."*

**That message is now only half true, and it is worth knowing precisely where the line falls.** A
shape that names the table it governs -- `shape Store / orders as table / item as text required /
quantity as int default 1 / price as decimal required` -- is a different, stronger declaration:
the shape IS the table's description, not just a request-side contract next to it. Verified live,
same fields, this form:

```mohio
connect db as sqlite from env.DATABASE_URL

shape Store
    orders as table
        item as text required
        quantity as int default 1
        price as decimal required
shape: done

save to db.orders
    item "widget"
    price 9.99
save: done

find rows in db.orders
find: done
show ("item: " & rows.first.item & " quantity: " & rows.first.quantity)
```
```
item: widget quantity: 1
```

`quantity` defaulted to `1` on a direct `save`, no request involved -- the check-time warning
above is stale for this case and still claims it would not. `save`, `upsert`, and `save all`
against a table-bound shape all fill in declared defaults, because each is a WHOLE-ROW write.
`update` is the one exception, deliberately: it changes a subset of a row that already exists, and
pouring a default over an existing value would overwrite real data with a fallback, so `update`
never fills one in, on either shape form. See the money chapter (`Docs/manual/mohio-manual-part2-money.md`)
for the same rule applied to currency fields, and why it exists (a write-time choke point every
write verb shares, so the rule cannot be added for `save` and quietly forgotten elsewhere).

**A script-level `create` of a shape instance (`create note as sh.Feedback ... create: done`) is
its OWN, narrower case, checked directly.** Type-checking on a `create` is real -- a
field declared `age as int` refuses a text value there too. `min`/`max` are a different story:
verified live, `create note as sh.Feedback / message "hi" / create: done` against a shape
declaring `message as text required min 5 max 500` runs to completion with no warning and no
error, `message` staying at 2 characters. Do not rely on `min`/`max` catching anything outside a
served request today. It is a real, currently-unguarded gap: no warning or refusal for this
case exists in the compiler today.

---

**Next in Part V:** Chapter 17, Shapes as Forms; Chapter 18, Validation.
