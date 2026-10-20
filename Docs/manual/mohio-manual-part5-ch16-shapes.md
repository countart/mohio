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

### Field rules: `required`, `min`, `max`, `default`, `unique`

```mohio
shape Order
    item as text required
    quantity as int default 1
    price as decimal required
shape: done
```

`required` refuses a missing field. `min`/`max` bound a text field's length (characters, both
ends) or a number's range.

**`default` fills a field only when a request binds to the shape, or the shape governs a table. It does NOT fill
on an unconstrained direct `save`.** This is the rule to carry, not a footnote: on the request path a default
fills for any shape (fixed 2026-10-05, after a spell when a plain shape's default did not fill), and a
table-governing shape (`as table`, below) fills on a direct `save`/`update`; but a direct `save` against a flat
shape that names no table does NOT fill the default, because a flat shape's rules are a request-boundary contract,
not a table-level one. So if you `save` straight to a table through a flat shape and expect the default to appear,
it will not, and that is by design, not a bug.

**`unique`** refuses a duplicate value. It is enforced by a real unique index on the table the
shape is saved to, so a second row with the same value is refused rather than quietly written:

```mohio
connect db from env.DATABASE_URL
shape Acct
    account_name as text required unique
shape: done
listen for sh.Acct at /a
    save to db.accts
        account_name account_name
    save: done
    give back [201] "ok"
listen: done
```

The shape must be saved to a table the compiler can resolve, or `unique` refuses at check rather
than pretending to protect a table it cannot find. A `unique` field on a shape that is never
written is inert and warns once, so a decorative `unique` cannot look like protection it is not
giving. This holds for a plain shape, not only for a shape declared `as table`.

**`unique` on a protected field is enforced on its fingerprint.** A field tagged `[phi]`, `[pci]`
or `[pii]` is stored encrypted, so there is no plaintext column to index. `unique` still holds: it
is enforced on the field's deterministic blind index, so two rows with the same protected value are
refused even though the stored ciphertext differs. Verified by running, a `[pii]` email declared
`unique`, saving the same address twice:

```
409 conflict: a record with this email already exists; `email` is unique.
  This is a duplicate, not a server error. Let it answer the caller as a 409 conflict, catch it with `on.failure`, or use `save or update` to write-or-replace instead of insert.
```

Composite uniqueness enforces on the COMBINATION through the fingerprint the same way. Verified with
a `save ... unless email, org exists` guard over a `[pii]` email: the same email under a DIFFERENT
org both save, and only the repeated PAIR is refused. (The refusal text today names a single field;
the enforcement is on the combination.)

**An existing table that already holds duplicates reports them, and changes nothing.** If you add
`unique` to a field whose table already contains duplicate values, Mohio refuses to create the rule
rather than enforce it partially, at the first write of the run, naming the count:

```
cannot enforce `unique` on accounts.email: 1 value(s) of `email` appear more than once, across 3 row(s). Nothing was changed: the rule is not created partially, and it is not skipped.
  To fix it, remove the duplicate rows, or drop `unique` from `email`.
```

### Where these rules actually enforce, verified precisely

**On the request path -- a `listen for sh.X`/`request for sh.X` route -- every rule enforces, for real,**
already shown end to end in `Docs/guides/mohio-first-app-tutorial.md` (a real 422 naming each bad
field). Confirmed again with `Order` above, served for real:

```bash
curl -X POST http://localhost:8080/order -d '{"item":"widget"}'
# 422 {"errors": {"price": "Price is required."}}
```

**Type coercion and `required` enforce on the request path.** Served for real, a value sent as the JSON
string `"9.99"` arrives as a real decimal the response can do math with, because the shape declares the
type and the request-binding boundary is where text becomes a number; a value that cannot convert is a
422, never a crash. A missing `required` field is a 422 that names it.

**`mio check` sees only the shape, not the database's own constraints.** `mio check` validates a request
against the SHAPE, statically, without opening the database (verified by running: a program checks with no
database connected). So it cannot know that a column is `NOT NULL`. If a database column must hold a value,
declare the matching shape field `required` as well: otherwise a request that omits it passes check and the
shape's validation, then fails at the write. Declaring `required` turns that write-time failure into a 422
that names the field, caught on the request path instead.

**Declared types bind at every binding point, and money stays exact.** A field's declared type is applied
wherever a request binds, not only on a direct save. A `USD` or `dec.N` field arrives as an EXACT decimal,
never a binary float: verified by running, `10.10 + 0.20` sums to exactly `$10.30` (not `10.299...`), and
`0.10 + 0.20` to exactly `$0.30`. A `dec.2` caps extra precision by truncation (`3.14159` stores `3.14`).

**Extra money precision rounds half away from zero, with a warning.** Sending more decimals than the
currency holds rounds half away from zero and warns (under a regulated sector it refuses instead). Verified:
`1.005` into a `USD` field stores `$1.01`, `-2.675` stores `-$2.68`:

```
[mohio.money] WARNING: amount arrived as 1.005 and USD holds 2 decimal places, so it is stored as 1.01, rounded half away from zero. Send the amount at USD's precision to keep exactly what was sent.
```

**An empty optional number stays empty, never 0.** An optional number sent empty (`""` or JSON `null`)
binds as EMPTY, not `0`; a sent `0` is kept as its own distinct value (verified by running). An OMITTED key
is a different case: reading a field the request never carried fails loud (`count` is not a field on
`thing`), rather than silently becoming None or 0. A field that exists but is empty stays None; one that was
never there fails loud.

**A whole-record save refuses an undeclared field, naming it.** A `save all to db.X from <record>` write
carries only what its shape describes: an extra field the shape does not declare is refused and NAMED, never
silently written as a stray column or a mass-assignment. Verified by running, a POST carrying `is_admin` to a
handler whose shape declares only `name` and `plan`:

```
undeclared_field: `save all` carries field(s) `is_admin` that shape `Member` does not declare. A whole-object write may only carry what its shape describes.
```

A shapeless target refuses the same way (`schema_from_input: ... A table's columns come from the program,
never from the data written to it`). The refusal is loud and names the field; today it answers 500, where a
client error of this kind should be a 4xx, a status still to tighten. The security guarantee, no silent
mass-assignment and no stray column, holds either way.

**A browser 422 re-render CLEARS protected fields, and warns about a password field.** When a browser
form fails validation, Mohio re-renders it with the submitted values filled back in, so the person
fixes one field without retyping the rest. A protected field is the exception: a field tagged `[phi]`,
`[pci]` or `[pii]` (and an `encrypted` field, and any secret) comes back EMPTY, never written back into
the page, so a failed submit cannot echo a protected value in the HTML. Verified end to end through a
real POST, with one field of each kind plus an untagged `nickname`:

```
email:    <input ... required><small class="mohio-error">Email is required.</small>
phone:    <input type="text" name="phone" id="phone">              (pii, blanked)
card:     <input type="text" name="card" id="card">                (pci, blanked)
ssn:      <input type="text" name="ssn" id="ssn">                  (phi, blanked)
nickname: <input type="text" name="nickname" id="nickname" value="Bosslady">   (untagged, kept)
```

The tagged fields come back blank; the untagged `nickname` keeps what was typed. This is the one place
`[pii]` is protected on OUTPUT, unlike a page response where `[pii]` stays plain.

A submitted PASSWORD carries no tag, so nothing above protects it unless you say so. A field that reads
as a password (its name is `password`, `passwd`, `passphrase`, `passcode` or `pwd`) earns a check-time
WARNING when it has no `format "password"`:

```
! Line 3: `password` reads as a password, but it has no `format "password"`, so a form that fails validation is sent back with the submitted password filled in.
  To fix it, declare the format:
      password as text format "password"
```

Declaring `password as text format "password"` clears the warning and keeps the password out of the
re-rendered form, the same way a tagged field is kept out.

**`default` on a PLAIN shape fills on the request path (fixed 2026-10-05).** With a plain `shape Prefs`
declaring `theme as text default "light"`, a POST that omits `theme` now fills `Prefs.theme` with `"light"`
rather than leaving it absent (verified by running), the same as `required` and type coercion above. Between
2026-10-03 and this fix a plain-shape default was silently dropped; it now fills. What a plain shape's default
does NOT reach is a DIRECT `save`/`update`, where the write names a table and no shape is bound, so there a flat
shape's rules stay a request-boundary contract only. Where `default` fills on a direct write is a shape that names
its table: the shape IS the table's description, not a request-side contract beside it. Verified live, this
table-bound form:

```mohio
connect db from env.DATABASE_URL

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

`quantity` defaulted to `1` on a direct `save`, no request involved. `save`, `upsert`, and `save all`
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
