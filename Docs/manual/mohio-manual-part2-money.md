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

Because it is exact, two money values that should be equal ARE equal, so you can branch on it
directly:

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

In floating point that same comparison would take the "drifted" branch.

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
| BHD | `BD ` | **3** | `,` | `.` | before |
| KWD | `KD ` | **3** | `,` | `.` | before |
| OMR | `OMR ` | **3** | `,` | `.` | before |
| JOD | `JD ` | **3** | `,` | `.` | before |
| TND | `DT ` | **3** | `,` | `.` | before |
| IQD | `IQD ` | **3** | `,` | `.` | before |
| LYD | `LD ` | **3** | `,` | `.` | before |

Twenty-five currencies. JPY and KRW carry zero decimal places; the seven dinars at the bottom
(BHD, KWD, OMR, JOD, TND, IQD, LYD) carry three, because they divide into 1000, not 100; every other
currency carries two. The worked example below runs the same amount (`1234.5`) through each of the
eighteen two-and-zero-place currencies; the three-decimal dinars are shown on their own terms just
below it.

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
forced through one Western convention. INR is grouped in threes here (`1,234.50`), not the
Indian lakh/crore grouping; either way the stored amount is exact, only the display grouping
differs.

### The three-decimal dinars

The seven dinar currencies divide into 1000, so they carry three decimal places, not two. An amount
at three places is exact; a fourth place rounds away:

```mohio
a as BHD
a 0.001
show a
b as BHD
b 1.2345
show b
c as BHD
c 1234.5
show c
```
```
BD 0.001
BD 1.235
BD 1,234.500
```

`0.001` is exact at three places, `1.2345` rounds half-up to `BD 1.235`, and `1234.5` formats as
`BD 1,234.500` with the symbol, the thousands separator and all three places. The same holds for
KWD, OMR, JOD, TND, IQD and LYD.

### Over-precision, and `as decimal` versus a currency type

A currency-typed value holds exactly its currency's number of decimal places, and a value written with
more precision is quantized to that many places, rounded half-up. When an over-precise LITERAL is
written directly, Mohio warns at run so the loss on every write does not go unseen (the assignment
path was made to warn in build Batch 26, matching the create-field path that already did):

```mohio
price as USD
price 1.005
show price
```
```
[mohio.money] WARNING: Line 2: USD holds 2 decimal places, so 1.005 is stored as $1.01 -- the extra precision is dropped, silently, on every write. Write the amount at USD's precision so the value you see is the value you keep:
    price 1.01        // stored as $1.01
(A warning now; under a regulated sector profile this becomes a refusal.)
$1.01
```

`1.005` becomes `$1.01`, `0.0001` becomes `$0.00`, `2.675` becomes `$2.68`, half-up at two places, and
the warning shows the value AS STORED so you can see exactly what you keep. It fires wherever an
over-precise literal is written directly, a typed-variable assignment and a create field alike. Two
cases stay SILENT on purpose: a correctly-scaled literal (`0.500` in a BHD field), and a COMPUTED value
that happens to round (only a literal you wrote warns, not the result of a division). As the message
says, under a regulated sector profile this is meant to become a refusal rather than a warning.

**Money is a currency type, not plain `as decimal`.** A value declared `as decimal` keeps whatever
precision you give it and applies no rounding and no currency:

```mohio
amount as decimal
amount 1.005
show amount
```
```
1.005
```

`1.005` stays `1.005`. Plain decimal is right for a measurement or a rate, where the exact figure is
what you want; it is the wrong tool for money, because it will not hold the currency's places, will
not round half-up, and does not carry a currency, so cross-currency math is not caught. Declare money
with a currency type (`amount as USD`) and let the type carry the precision, the rounding and the
currency, rather than reaching for plain decimal and writing your own precision check.

**Money is a currency type, not plain `as decimal`.** A value declared `as decimal` keeps whatever
precision you give it and applies no rounding and no currency:

```mohio
amount as decimal
amount 1.005
show amount
```
```
1.005
```

`1.005` stays `1.005`. Plain decimal is right for a measurement or a rate, where the exact figure is
what you want; it is the wrong tool for money, because it will not hold the currency's places, will
not round half-up, and does not carry a currency, so cross-currency math is not caught. Declare money
with a currency type (`amount as USD`) and let the type carry the precision, the rounding and the
currency, rather than reaching for plain decimal and writing your own precision check.

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

### Money against another kind

Money compares and combines only with money of the same currency, or with a plain number where the rules
above apply. Money against a value of any other kind refuses rather than coercing it. `when amount is true`
does not read `true` as a number; `when amount is more than true` does not coerce the boolean and answer
yes; `when amount is ""` and `match amount to true` do not quietly compare or find nothing. A true-or-false,
a written empty text, null, a time word (`today`, `now`), a list, and a name or column declared boolean,
date, datetime, uuid, list, text or email all refuse against money, on `when is`, `is more than`,
`is less than`, the paren forms, `where is`, `where is more than`, `where between` and `match`. Asking
whether an amount has no value is `is empty`, which is defined and is not a cross-kind comparison.

### Storage and read-back

Money written to a database is stored as its exact decimal text -- `0.10`, not the float `0.1` --
and reads back both as that exact amount and with its currency restored, because the governing
shape carries the currency, not the column.

```mohio
connect db from env.DATABASE_URL

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

- **Converting between currencies: the conversion is free, the live rate feed is the paid part.**
  Converting USD to EUR needs an exchange rate, and a rate that changes by the second has to be
  sourced and audited like any other external fact a compliance-relevant decision depends on. Ron's
  ruling (`T1-PAYGATE-PRINCIPLE`): the governed conversion operation is FREE, the language primitive;
  what is paid is Mohio operating a LIVE RATE FEED for you. The type you have today holds an amount in
  ONE currency exactly and does not convert yet; conversion is coming as its own capability, never
  silently bundled into `+`. Adding two currencies without a rate stays refused.
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

### Rendering the bare number: `no.symbol`

Sometimes you want the number without the currency symbol: a table column that a front end will
align, or a value a locale service will re-render for the reader in its own convention. Declare the
field `no.symbol`:

```mohio
shape Prices
    catalog as table
        price as USD no.symbol
shape: done
```

A `no.symbol` money field renders the BARE number and keeps everything true about the money: the
currency CODE and the exact SCALE stay on the value, only the symbol is dropped. Verified by running:
a `USD` field renders `8.50` where the ordinary form renders `$8.50`, and the scale is the currency's,
so a `BHD` field keeps its three places. It changes only the RENDER path. JSON and `give back` are
unchanged, because a currency already serializes to its plain number there regardless of this
modifier. (One current wrinkle: the CLI `show` echo still prints the symbol, so read the formatting on
the rendered page, not the run log.)

**Why this is one word and not a family of locale options.** The boundary Ron ruled is that the
compiler owns what is true about the MONEY, and a service owns what is true about the READER. The code
and the scale are properties of the money: `8.500` BHD is three-place whoever looks at it. How that
same amount appears, `$8.50`, `8,50 kr`, or a bare `8.50` for a reader whose locale is decided
elsewhere, is a property of the READER, not the currency, and the same amount renders three ways
depending on who is looking. So locale-aware rendering belongs to Mio Currency when it exists, not to
the compiler, and `no.symbol` is the one thing the compiler can offer today: hand back the
true number and its code, and let the locale layer format it later. A pile of locale flags on the
compiler would be the compiler claiming to own something it does not.

---

**Next in Part II:** Chapter 9, Lists (already written elsewhere in this manual's build order);
Chapter 10, Numbers, Precision, and Randomness.
