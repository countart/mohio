<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Part IX -- Operational Guarantees

## Chapter 44: Sectors -- opting in to governance

One line at the top of a program, `sector: financial` (or `healthcare`, `logistics`, `education`, or a
sector of your own), is how an application says "hold me to the rules". It is an opt-in. With no
sector, nothing in this chapter applies: an unregulated write pays nothing, and every governed
behavior below is something you can still choose for one route by writing it yourself.

This chapter is about what the declaration turns on, in particular for the routes that move money,
and about the one way to leave a single route out. Every claim below was established by running.

### 1. What declaring a sector turns on

Declaring a sector turns on Mohio's built-in governance defaults for the whole application, whether
or not a certified profile for that sector is installed:

- an over-precise money amount is refused, never rounded;
- stored rows that break a declared `required` refuse at first touch;
- a default database on a temporary disk refuses at connect;
- every data write is recorded in the audit trail;
- **every route that moves money is idempotent, and requires an `Idempotency-Key` from its caller.**

A certified sector profile adds that sector's own rules on top (its classifications, its confidence
floors, its never-store fields). Without one, `mio check` says the certified rules are not installed,
and the defaults above still hold.

### 2. Money routes are idempotent

A request can arrive twice: a double click, a network retry, a client that timed out and tried again.
For a route that moves money, doing the work twice means paying twice. So under a sector, a
`listen for` route that moves value behaves as if it carried this line, with nothing written:

```mohio
sector: financial

connect db as sqlite from env.DATABASE_URL

shape Ledger
    payments as table
        amount as USD
        memo as text
shape: done

shape PayIn
    amount as USD
    memo as text
shape: done

listen for sh.PayIn at /pay
    require role "teller"
    save to db.payments
        amount request.amount
        memo request.memo
    save: done
    give back [201] "paid"
listen: done
```

Served, the `/pay` route answers like this (the first four verified over real HTTP on a sector route;
the fifth is the same gate `once per` uses, verified there):

| The caller sends | The route answers |
|---|---|
| no `Idempotency-Key` header | `400`, naming the header and the sector |
| a new key | runs the route (`201`) |
| the same key again, the same body | the first answer again, and the work is NOT done again (one row) |
| the same key with a different body | `422`: a key names one request |
| the same key while the first is still running | `409`: retry in a moment with the same key |

The answer is kept for 24 hours. To keep it for another length, write the line yourself; a route that
says `once per` is governed by its own line:

```mohio
sector: financial

connect db as sqlite from env.DATABASE_URL

shape Ledger
    payments as table
        amount as USD
shape: done

shape PayIn
    amount as USD
shape: done

listen for sh.PayIn at /pay
    require role "teller"
    once per request.header "Idempotency-Key" keep for 7 days
    save to db.payments
        amount request.amount
    save: done
    give back [201] "paid"
listen: done
```

### 3. What counts as moving money

A route moves money when it writes an amount, or sends one out. An amount is a value typed in a
currency (`amount as USD`), which is how Mohio's money contract knows a value is money. Mohio traces
where a typed amount can flow, so a route that moves one through any of the paths below is governed:

- a currency field written by `save`, `update` (adding to an amount included), `modify`,
  `save or update`, `save all`, or a `flow` into a database stage declared in a currency;
- a currency field written by raw `sql` (`UPDATE payments SET amount = ...`);
- an outbound call that carries an amount: a `miohttp` call whose body or header holds one, a
  connector operation that `sends` a shape declaring one, or a record looked up from a table that
  declares one;
- any of the above inside a task the route calls, including a task the route hands an amount to.

A route that changes something without moving money (saving a note, sending a message with no amount)
is NOT made idempotent. `mio check` names those routes in one grouped advisory, and you may add
`once per` to any of them that should answer a repeat from the first answer. A route that only
reads is safe to repeat, and earns nothing.

### 4. The client contract: tell your callers

Requiring a key changes what every caller must send, so `mio check` says it, loudly, in one notice
that names each money route. For the first example above, under a sector whose profile is installed
(here a community profile, `sectors/sector-demo-financial.mho`, declared as `sector: demo-financial`):

```
! Line 16: Declaring `sector: demo-financial` makes 1 route that moves value idempotent, and it now
  REQUIRES an `Idempotency-Key` header: a request without one is refused 400, and a repeat with the
  same key gets the first answer. Update your callers to send a new key for each new request and the
  same key to retry one.
    /pay (line 16): writes `amount`, an amount in USD, to db.payments
    To leave a route out, put `not idempotent reason "<why>"` at its top. Once the callers send the
    key, record that in the journey, which quiets this notice (the key stays required):
        suppress sector_idempotency_key_required reason "callers send Idempotency-Key"
```

A caller sends a new key (a UUID is ideal) for each new request, and the SAME key when it retries one.
Once your callers do, record that in the application's journey, which quiets the notice (Chapter 23,
"Quieting an advisory"). The key stays required; only the reminder goes quiet:

```mohio
journey Payments
    timezone "UTC"
    suppress sector_idempotency_key_required reason "every caller sends Idempotency-Key since 6.0.2"
journey: done
```

### 5. Leaving one route out: `not idempotent reason "..."`

The only way to leave a money route out is on the route itself, with the reason on the line. There is
no switch that turns the default off for a whole sector or a whole application: a sector that does not
want it for a route says so at that route, where the next reader sees it.

```mohio
sector: financial

connect db as sqlite from env.DATABASE_URL

shape Ledger
    payments as table
        amount as USD
        memo as text
shape: done

shape Refund
    amount as USD
shape: done

listen for sh.Refund at /refund
    require role "teller"
    not idempotent reason "the provider deduplicates refunds by its own reference"
    save to db.payments
        amount request.amount
        memo "refund"
    save: done
    give back [201] "refunded"
listen: done
```

`mio check` refuses the line when it cannot mean what it says: with no reason, with an empty reason,
anywhere but the top of a `listen for`, written twice, written beside `once per`, or in a program
that declares no sector (where there is nothing to leave).

### 6. A sector with no profile still governs

A sector name that does not resolve to a profile (a certified profile that is not installed, or a
name that is not a profile at all) loads no sector rules. The declaration is still the opt-in to
governance, so money routes still require a key, and `mio check` says exactly what happened and both
ways forward:

For the first example above, as written (`sector: financial`, whose certified profile is not installed
on this machine):

```
! Line 16: `sector: financial` did not resolve to a sector profile (not installed, or not the name of
  one), so its rules are not loaded. Declaring it is still the opt-in to governance, so money routes
  require an `Idempotency-Key` by default. This makes 1 route that moves value idempotent, and it now
  REQUIRES an `Idempotency-Key` header: ...
    /pay (line 16): writes `amount`, an amount in USD, to db.payments
    For the sector's full rules, add the `financial` profile (a `sectors/` folder beside the program,
    or ~/.mohio/sectors). To leave a route out, put `not idempotent reason "<why>"` at its top. ...
```

### 7. Where the route's work is kept

An idempotent route keeps each key and its answer in the database, in the same transaction as the
route's own work, so the two can never disagree. That needs a database connection that runs a request
transaction: SQLite, Postgres, MySQL or Oracle. A money route under a sector in a program with no
database connection, or on MongoDB (whose request transaction is not built in this release), refuses
at `mio check` with the way forward.

### Common mistakes

- **A caller that never sends the key.** Under a sector it gets `400` on every money route. Send a new
  key per request.
- **A caller that sends a new key on a retry.** The retry is then a new request and does the work
  again. Retry with the SAME key.
- **Reaching for a global switch.** There is none, by design. Leave a route out with
  `not idempotent reason "..."` on that route.
- **Suppressing the notice before the callers are updated.** The notice is the reminder; suppress it
  when it is true that the callers send the key.
