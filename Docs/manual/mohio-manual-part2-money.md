<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Part II -- Values, Types, and Text

## Money

*Every example below was checked with `mio check` and run with `mio run`; the storage example is
a real database round trip, read back both through Mohio and directly off the database file. The
currency list is read off `mohio_interpreter.py`'s own `_CURRENCIES` table, not carried from a
plan -- if a currency is not in that table, it is not in this chapter.*

---

### Why exact

Money in Mohio is a real type, not a float wearing a dollar sign. This removes the oldest bug in
financial software: ordinary binary floating point cannot represent most decimal fractions
exactly, so `0.1 + 0.2` does not equal `0.3` -- it equals `0.30000000000000004`. Verified, in
Mohio, with plain numbers and no currency type involved:

```mohio
x 0.1
y 0.2
show (x + y)
```
```
0.30000000000000004
```

The same two numbers, typed as money, are exact:

```mohio
a as USD
a 0.10
b as USD
b 0.20
show (a + b)
```
```
$0.30
```

Ten dimes make exactly a dollar, not `$0.9999999999999999`:

```mohio
dime as USD
dime 0.10
total as USD
total 0
repeat 10 times
    total (total + dime)
repeat: done
show total
```
```
$1.00
```

Money is held as an exact decimal internally, never a binary float, so no amount of repeated
addition drifts away from the value it started as. A value that genuinely cannot be held exactly
is refused rather than silently rounded into approximate arithmetic -- money either computes
exactly or the program is told why not.

### Declaration and the currency set

Declare a currency-typed value with `<name> as <CODE>`, then assign it the ordinary way:

```mohio
price as USD
price 5.00
```

A shape field works the same way -- `amount as USD` -- and that is the form the rest of this
chapter uses once storage is involved.

**The full currency set, exactly as built** (`mohio_interpreter.py`'s `_CURRENCIES` table):

| Code | Symbol | Places | Thousands | Decimal | Symbol position |
|---|---|---|---|---|---|
| USD | `$` | 2 | `,` | `.` | before |
| EUR | `€` | 2 | `.` | `,` | before |
| GBP | `£` | 2 | `,` | `.` | before |
| JPY | `¥` | **0** | `,` | `.` | before |
| CHF | `CHF ` | 2 | `'` | `.` | before |
| CAD | `$` | 2 | `,` | `.` | before |
| AUD | `$` | 2 | `,` | `.` | before |
| CNY | `¥` | 2 | `,` | `.` | before |
| HKD | `HK$` | 2 | `,` | `.` | before |
| SGD | `S$` | 2 | `,` | `.` | before |
| INR | `₹` | 2 | `,` | `.` | before |
| NZD | `NZ$` | 2 | `,` | `.` | before |
| SEK | `kr` | 2 | (space) | `,` | **after** |
| NOK | `kr` | 2 | (space) | `,` | **after** |
| MXN | `$` | 2 | `,` | `.` | before |
| BRL | `R$` | 2 | `.` | `,` | before |
| ZAR | `R` | 2 | (space) | `.` | before |
| KRW | `₩` | **0** | `,` | `.` | before |

Eighteen currencies, verified together, the same amount (`1234.5`) through each one:

```mohio
shape Prices
    usd as USD
    eur as EUR
    gbp as GBP
    jpy as JPY
    chf as CHF
    cad as CAD
    aud as AUD
    cny as CNY
    hkd as HKD
    sgd as SGD
    inr as INR
    nzd as NZD
    sek as SEK
    nok as NOK
    mxn as MXN
    brl as BRL
    zar as ZAR
    krw as KRW
shape: done

create p as sh.Prices
    usd 1234.5
    eur 1234.5
    gbp 1234.5
    jpy 1234.5
    chf 1234.5
    cad 1234.5
    aud 1234.5
    cny 1234.5
    hkd 1234.5
    sgd 1234.5
    inr 1234.5
    nzd 1234.5
    sek 1234.5
    nok 1234.5
    mxn 1234.5
    brl 1234.5
    zar 1234.5
    krw 1234.5
create: done

show ("USD: " & p.usd)
show ("EUR: " & p.eur)
show ("GBP: " & p.gbp)
show ("JPY: " & p.jpy)
show ("CHF: " & p.chf)
show ("CAD: " & p.cad)
show ("AUD: " & p.aud)
show ("CNY: " & p.cny)
show ("HKD: " & p.hkd)
show ("SGD: " & p.sgd)
show ("INR: " & p.inr)
show ("NZD: " & p.nzd)
show ("SEK: " & p.sek)
show ("NOK: " & p.nok)
show ("MXN: " & p.mxn)
show ("BRL: " & p.brl)
show ("ZAR: " & p.zar)
show ("KRW: " & p.krw)
```
```
USD: $1,234.50
EUR: €1.234,50
GBP: £1,234.50
JPY: ¥1,235
CHF: CHF 1'234.50
CAD: $1,234.50
AUD: $1,234.50
CNY: ¥1,234.50
HKD: HK$1,234.50
SGD: S$1,234.50
INR: ₹1,234.50
NZD: NZ$1,234.50
SEK: 1 234,50 kr
NOK: 1 234,50 kr
MXN: $1,234.50
BRL: R$1.234,50
ZAR: R1 234.50
KRW: ₩1,235
```

JPY and KRW round to whole units, no decimal places -- `1234.5` rounds up to `1,235`, half-up, the
same rounding every currency uses at its own precision. EUR and BRL swap the thousands and decimal
separators from USD's convention; CHF uses an apostrophe for thousands; SEK/NOK put the symbol
after the amount with a space before it. Each currency formats correctly on its own terms, not
forced through one Western convention.

### Safe math

Addition and subtraction between two amounts of the same currency are exact, shown above. Multiply
or divide an amount by a plain number and the result is still money -- a line-item total on an
invoice is not supposed to stop being money the moment it is multiplied by a quantity:

```mohio
price as USD
price 5.00
quantity 10
total (price * quantity)
show total
```
```
$50.00
```

```mohio
full as USD
full 100.00
share (full / 4)
show share
```
```
$25.00
```

Divide money BY money and the currency drops -- the result is a ratio, a plain number, because
"how many dollars per dollar" is not itself a dollar amount:

```mohio
discounted as USD
discounted 80.00
full as USD
full 100.00
ratio (discounted / full)
show ratio
```
```
0.80
```

The rule is which SIDES are money, not which operator is used: money against a plain number stays
money (a quantity, a split, a rate applied); money against money is always a ratio.

### Cross-currency refusal

Adding two different currencies together is refused by name, because there is no fixed rate
between them for the compiler to assume -- mixing currencies without a real conversion is a bug,
not an operation with an obvious answer:

```mohio
dollars as USD
dollars 10.00
euros as EUR
euros 10.00
total (dollars + euros)
show total
```
```
Response  500  currency_mismatch: Cannot do math across currencies (USD + EUR). Convert one to
the other first -- there is no fixed rate between them.
```

### Storage and read-back

Money written to a database is stored as its exact decimal text -- `0.10`, not the float `0.1` --
and reads back both as that exact amount and with its currency restored, because the governing
shape carries the currency, not the column.

```mohio
connect db as sqlite from env.DATABASE_URL

shape Store
    prices as table
        label as text required
        amount as USD
shape: done

save to db.prices
    label "coffee"
    amount 0.10
save: done

find rows in db.prices
find: done
show ("from the query: " & rows.first.amount)
```
```
from the query: $0.10
```

Reading the same database file directly, outside Mohio entirely, confirms what is actually on
disk:

```
sqlite3.connect(db).execute("SELECT amount FROM prices").fetchall()
[('0.10',)]
```

Not `0.1`, not a binary float artifact -- the literal text `0.10`, exactly what was written, with
the currency restored on the way back out through Mohio because the shape above names `prices` as
a table it governs (see Context, below, for why that line is what makes this work).

**Scope, stated precisely:** this exact round trip -- write, store, read back, currency restored --
is verified on SQLite and Postgres, both real servers. MySQL and a live MongoDB were not reachable
when this was verified and are not claimed. A separate, earlier pass ran the underlying exact-math
and quantization guarantee (not the currency-restore-on-read piece specifically) against SQLite,
Postgres, and MySQL live, and MongoDB via `mongomock` -- a real driver path and real type handling,
but not a live `mongod`, and that distinction is kept rather than blurred. Do not repeat "verified
on every engine" from this chapter; repeat exactly the two claims above, with their own scope.

### Boundaries -- what money does not do, stated so this section stays trustworthy

- **Currency conversion is a separate, paid service, not part of the free type.** Converting USD
  to EUR needs a live exchange rate from somewhere, and that rate needs to be sourced and audited
  the way any other external fact a compliance-relevant decision depends on does. The type you have
  today holds an amount in ONE currency exactly; it does not convert between currencies. That is
  coming as its own capability, not silently bundled into `+`.
- **`%` on money drops the currency.** `%` computes a remainder (modulo), not "N percent of," and
  it is outside the money-math ruling above -- verified: `price % 8` on a `USD` value returns a
  bare number, not money. Do the percentage arithmetic on the plain number and re-tag the result as
  money deliberately if that is what you mean, rather than relying on `%` to carry the currency for
  you.
- **A `flow` hop does not currently fill in a shape's defaults on the row it creates.** `save`,
  `upsert`, and `save all` against a shape-governed table all fill in declared defaults, because
  each is a WHOLE-ROW write. `update` deliberately never fills a default -- verified: updating one
  field of an existing row leaves every other field exactly as it was, never overwritten by a
  fallback, because `update` changes a subset of a row that already exists, and pouring a default
  over an existing value would be data loss dressed as helpfulness. A `flow` hop writes through the
  same `save` path but inserts only the one column it names, and is deliberately NOT treated as a
  whole-row write yet -- doing so would also turn on required-field validation for every existing
  single-column hop into a shaped table, which is a larger, separate design question, not a small
  fix. Queued, not silently dropped.

### Context: the shape governs the write

Money persisting correctly -- quantized to the currency's exact places, defaults filled in,
currency restored on read -- is not a special case built for money. It is one instance of a
general rule: **a shape that names the table it governs (`shape Store / prices as table / ...`)
has that description hold on every write to that table, not only on a form.** The write path
already resolved this binding for validation and classification; defaults and money treatment now
run through the same single choke point (`_guard_write`) every write verb passes through, so the
rule cannot be added for `save` and quietly forgotten for `update` or `flow`.

Defaults fill in BEFORE validation runs, so a field with a default is never wrongly refused as
missing. Defaults only apply on a WHOLE-ROW write -- `update`, which changes a subset of an
existing row, never overwrites what is already there with a declaration's fallback; that would be
data loss dressed as helpfulness.

**One precise distinction worth carrying forward: this is specifically about a shape that declares
which table it governs (the `<name> as table` form above), not any `shape` used only for request
binding.** A flat shape referenced only as `sh.X` on a route does not, on its own, tell the write
path which table it governs, so a direct `save`/`update` against that kind of shape still does not
fill in defaults -- documented in the Shapes chapter. The two forms look similar and answer a
different question; which one you have determines whether this section's guarantee applies.

---

**Next in Part II:** Chapter 9, Lists (already written elsewhere in this manual's build order);
Chapter 10, Numbers, Precision, and Randomness.
