<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Part VII -- Data and Persistence, MioQL

## Chapter 28: Money

*Verified: every block checked with `mio check`, every program run with `mio run`,
output read back. Storage verified against SQLite, Postgres and MySQL live, and Mongo through
mongomock.*

---

### Money is not a float, and that is the point

```mohio
price as USD
price 1234.5
show price
```
```
$1,234.50
```

`as USD` declares a money value. Underneath it is an exact decimal, not a floating-point number.
That distinction is the reason this type exists: `0.1 + 0.2` in floating point is not `0.3`, and a
ledger built on floats drifts by a cent at a time until somebody has to explain it.

```mohio
a as USD
a 0.10
b as USD
b 0.20
total (a + b)
expected as USD
expected 0.30
check total
    when total is expected
        show "exact"
    otherwise
        show "drifted"
check: done
```
```
exact
```

---

### The currencies Mohio knows

Eighteen, and this is the whole list. Each one carries how it is written and how many places it
rounds to.

| | | | |
|---|---|---|---|
| USD `$1,234.50` | EUR `€1.234,50` | GBP `£1,234.50` | JPY `¥1,235` |
| CHF `CHF 1'234.50` | CAD `$1,234.50` | AUD `$1,234.50` | CNY `¥1,234.50` |
| HKD `HK$1,234.50` | SGD `S$1,234.50` | INR `₹1,234.50` | NZD `NZ$1,234.50` |
| SEK `1 234,50 kr` | NOK `1 234,50 kr` | MXN `$1,234.50` | BRL `R$1.234,50` |
| ZAR `R1 234.50` | KRW `₩1,235` | | |

**The number of places is correctness, not style.** Yen and won have no minor unit, so they round
to whole units. Writing a yen amount with two decimals is not a formatting preference, it is a
different number.

```mohio
fee as JPY
fee 1234.5
show fee
```
```
¥1,235
```

One known simplification, said here rather than hidden: the rupee groups in the Indian system
(`1,23,456`) and Mohio groups it in threes like the others. The amount is exact either way; only
the separators differ.

---

### Doing arithmetic with money

Money times a plain number is money. That is the case that actually occurs: ten items at five
dollars is fifty dollars, and a line-item total that stopped rendering as money would be wrong on
an invoice in a way nobody has to explain.

```mohio
price as USD
price 5
qty 10
total (price * qty)
show ("line total: " & total)
half (price / 2)
show ("half: " & half)
```
```
line total: $50.00
half: $2.50
```

One amount measured against another is a **ratio**, which is a plain number:

```mohio
a as USD
a 5
b as USD
b 2
r (a / b)
show ("ratio: " & r)
```
```
ratio: 2.50
```

The rule is whether BOTH sides are money, not which operator was used. One side money and one side
a number is a quantity, a split or a rate applied, and the answer is still money.

---

### Two currencies never mix silently

```mohio
a as USD
a 5
b as EUR
b 2
c (a + b)
show c
```

That refuses, with `currency_mismatch`. Mohio has no exchange rate anywhere in it, so adding
dollars to euros has no answer it could give you that would be true. A wrong rate is a wrong
number that looks right, which is the worst shape a money bug takes.

**Conversion is a separate service, deliberately.** Rates change by the second, they come from a
provider, and they carry a cost and an audit question of their own. That belongs somewhere it can
be versioned and charged for, not silently inside an addition.

---

### Money in the database

A money field stores its exact decimal text, so it reads back as the number you wrote.

```mohio
connect db as sqlite from env.DATABASE_URL
shape Store
    prices as table
        label as text required
        amount as USD
shape: done
save to db.prices
    label "a"
    amount 0.10
save: done
find rows in db.prices
find: done
show ("read back: " & rows.first.amount)
```
```
read back: $0.10
```

Two things are worth noticing in that output.

**The trailing place survives.** The column holds `0.10`, not `0.1`. A float would not keep it and
neither would a plain number.

**It comes back as money.** The currency is something the SHAPE knows and a column cannot carry,
so it is restored on the way out through the same table binding the write used. An amount does not
render as money only once it happens to meet another money value later.

Verified on SQLite, Postgres and MySQL against live servers, and on Mongo through mongomock, which
is what this repository's own Mongo tests use. Same value in, same value out, on all four.

---

### What money does NOT do

| | |
|---|---|
| convert between currencies | a separate service; no rate lives in the runtime |
| pick a currency for you | a money field declares its own, and mixing refuses |
| use Indian-style grouping for INR | grouped in threes; the amount is still exact |
| carry a currency through a ratio | money divided by money is a plain number |
