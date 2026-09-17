<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Part VII -- Data and Persistence, MioQL

## Chapter 27: Maps, and the two verbs that drive them -- `map`, `flow`, `walk`

*Verified: every block checked with `mio check`, every program run with `mio run`,
output read back. Exercised against SQLite, Postgres, MySQL and Mongo.*

---

### The idea in one line

A **map** says where values go. **`flow`** makes it happen. **`walk`** stops at every step and
lets you look.

That split is the whole design. A map is a declaration and says nothing about when the moving
happens, so it can be read on its own. One verb drives it quietly; a different verb looks. A
driver that also narrates is a driver you cannot trust to be quick, so the looking lives
somewhere else.

---

### `map` -- the declaration

A map holds one or more **chains**. A chain is a line of **stages** joined by arrows and read
left to right.

```mohio
map Paydata
    data
        order.total -> ledger.amount
map: done
```

A chain is **named by the qualifier of the stage it starts from**. The chain above begins at
`order.total`, so the chain is called `order`. There is no separate name to invent, and that is
deliberate: the name is already on the page.

A stage is one of two things.

| written | kind | what it is |
|---|---|---|
| `order.total` | place | somewhere a value sits |
| `db.ledger.amount` | place | a real database column |
| `tidy` | transform | a shape the value is reformed by |

A place is `owner.field`, as deep as it needs to be. A transform is a bare name, and it is a
shape the value passes **through**:

```mohio
shape tidy
    value as text pad.left to 6 with "0"
shape: done

map Paydata
    data
        order.total -> tidy -> ledger.amount
map: done
```

That is what lets a shape stand in a chain rather than only describe something. It takes the
value, reforms it, and hands it on.

#### More than one chain

```mohio
map Paydata
    data
        order.total -> ledger.amount
        refund.total -> refunds.amount
map: done
```

Two chains, called `order` and `refund`.

Each chain comes to rest in one place. If you point two chains at two different **columns of the
same table**, the second is refused: Mohio will not add a column to an existing table on the fly,
because doing that silently makes the schema whatever the last write happened to say. Give each
chain its own table, or create the table with both columns first.

#### Database stages work on every engine

A `db.` stage is a real write. This was measured, not assumed: the same map with a `db.` stage
was run against **SQLite, Postgres, MySQL and Mongo**, and the hop landed on all four.

---

### `flow` -- run it

```
flow <map>          runs EVERY chain in the map
flow <map>.<chain>  runs ONE chain
```

```mohio
shape tidy
    value as text pad.left to 6 with "0"
shape: done

map Paydata
    data
        order.total -> tidy -> ledger.amount
map: done

create order
    total "42"
create: done

create ledger
    amount ""
create: done

flow Paydata.order
show ("landed: " & ledger.amount)
```
```
landed: 000042
```

The value left `order.total`, was padded by `tidy` on the way past, and came to rest in `ledger`.

A destination has to exist before something lands in it, the same as any other value in Mohio.

#### Asking what came out

`flow` reports nothing while it runs. When you want the end of the chain, ask for it:

```mohio
map Paydata
    data
        order.total -> ledger.amount
map: done

create order
    total "42"
create: done

create ledger
    amount ""
create: done

check flow Paydata.order as landed
show ("the endpoint: " & landed)
```
```
the endpoint: 42
```

The endpoint is the stage after the last forward hop. Any **other** position is `grab` or `walk`,
so `flow` is never overloaded with positional access.

---

### `walk` -- look at every step

`walk` is the opposite number to `flow`. It stops at **every** stage, binds what is sitting there,
and runs whatever the body says.

```
walk <map>[.<chain>]
    at each stage: report      emit the stage
    at each stage: log         record it
    at each stage: <statement> run it, with `stage` bound
walk: done
```

```mohio
shape tidy
    value as text pad.left to 6 with "0"
shape: done

map Paydata
    data
        order.total -> tidy -> ledger.amount
map: done

create order
    total "42"
create: done

create ledger
    amount ""
create: done

flow Paydata.order

walk Paydata.order
    at each stage: report
walk: done
```
```
order [1] order.total (place) = '42'
order [2] tidy (transform) = ''
order [3] ledger.amount (place) = '000042'
```

At each stage the body can read `name`, `qualifier`, `path`, `kind`, `position`, `arrives`,
`departs` and `value`. Positions count from 1, like everywhere else in Mohio.

**A transform stage shows an empty value, and that is not a fault.** A transform is a step, not a
place: nothing rests there. The value it produced turns up at the next place along, which is
exactly what line 3 above shows.

`walk` inspects; it does not drive. Walking a chain nothing has flowed through shows the stages
with nothing in them, which is a perfectly reasonable question to ask.

#### The two ends

```mohio
map Paydata
    data
        order.total -> ledger.amount
map: done

create order
    total "42"
create: done

create ledger
    amount ""
create: done

flow Paydata.order

grab first stage from Paydata.order as entry
grab last stage from Paydata.order as exit
show ("entered at: " & entry.name)
show ("came to rest at: " & exit.name)
```
```
entered at: order.total
came to rest at: ledger.amount
```

`first` and `last` are the two **semantic** ends, found by the arrows rather than by counting.
They are the pair to compare when asking whether a value survived the trip. `grab stage at 2` and
`grab stage at "name"` reach the ones in between, and are convenience only -- `walk` already
reaches every stage.

---

### The Walk-By reading

Read the three aloud and they say what they do:

> **map** Paydata: the order total goes through tidy into the ledger amount.
> **flow** Paydata: run it.
> **walk** Paydata: at each stage, report.

A manager who has never seen Mohio can tell you what the map moves, and that is the test a map
has to pass before anything else.

---

### What is not built yet

`map` has a second and a third written form, and **neither runs in this release**:

```
map FieldNames                       a map of value-to-value entries
    "first_name" -> "firstName"
map: done

map raw through FieldNames as tidy   the action form
```

Both parse, and `mio check` now refuses them by name rather than letting them reach a runtime
that has nothing to run. Use the section form shown throughout this chapter, which is built.

A `wildcard` stage and a route `map` section that mounts files are separate topics; the `data`
section is what `flow` and `walk` drive.

---

### Working examples in the repository

| file | shows |
|---|---|
| `cookbook/map-moving-values.mho` | a map with two chains, a transform, and database stages |
| `cookbook/flow-run-a-map.mho` | one chain, every chain, and the endpoint |
| `cookbook/walk-inspect-a-map.mho` | every stage, and the two ends |

All three run with `mio run` and are covered by the test suite, so they stay working.
