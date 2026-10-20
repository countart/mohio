<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Part VIII -- The Web Layer

## Chapter 31: Verifying a Webhook Signature

*Every example below was run: `mio check` for the compile-time proofs and `mio serve` hit with real
HTTP for the runtime proofs. The command and its result are shown for each. Behavior was established
by running, not from the design.*

A webhook is an inbound request from another service: a payment succeeded, a message arrived, a build
finished. The caller holds no session, so `require role` does not fit. What it holds is a shared
secret, and it signs each request with it. `verify signature` is how a handler checks that signature
and refuses anything that does not carry a real one.

---

### The clause

```mohio
shape Payment
    amount as int
shape: done

listen for sh.Payment at /webhooks/pay
    verify signature using env.WEBHOOK_SECRET
    save to db.payments
        amount 100
    save: done
    give back [200] "ok"
listen: done
```

```
$ mio check pay.mho
  v  pay.mho  ... no errors
```

The clause runs **first** in the handler, before shape validation and before any statement, so
nothing binds, reads or writes until the signature is proven. It computes HMAC-SHA256 over the raw
request bytes, the exact bytes the caller sent before any parsing, and compares that against the
signature header in constant time. It **fails closed**: a request without a valid signature is
rejected and the handler body never runs.

**It runs on both handler kinds.** `verify signature` works on a `listen for sh.X` handler (a POST, shown above) and on
a `request for sh.X` handler, so a webhook delivered as a GET-style callback is verified the same way (verified):

```mohio
shape Payment
    amount as USD
shape: done
request for sh.Payment at /webhooks/pay
    verify signature using env.WEBHOOK_SECRET
    give back [200] "ok"
request: done
```

The full form carries an optional header name and an optional freshness window, both of which check
clean:

```mohio
shape Payment
    amount as int
shape: done

listen for sh.Payment at /webhooks/pay
    verify signature using env.WEBHOOK_SECRET header "Stripe-Signature" within 5 minutes
    give back [200] "ok"
listen: done
```

`header "..."` names the request header the signature arrives in; the default is `X-Signature`.
`within N minutes` adds a freshness check (below). Both are optional.

### The secret comes from the environment

The secret must be an `env.` value. A literal in the source is refused, because a secret in the code
is committed to git and shipped in the build, where anyone with the code can forge a signature:

```
$ mio check literal.mho
  x Line 6: `verify signature` needs its secret from the environment, not written in the page. A
    secret in source is committed to git and shipped in the build, where anyone with the code can
    forge a signature. Move it to an environment variable and name it here:
        verify signature using env.YOUR_WEBHOOK_SECRET
```

### What a caller sees: valid, wrong, missing, altered

Served with `WEBHOOK_SECRET` set, and hit with real requests:

```
valid signature       -> 200  {"message": "ok"}
wrong signature       -> 401  Signature verification failed.
missing signature     -> 401  Missing signature.
altered body, old sig -> 401  Signature verification failed.
```

Two properties worth knowing, both verified:

- **The raw bytes are what is signed.** A signature computed over a re-serialized copy of the body
  (same JSON, re-encoded) is rejected, because verification never re-encodes; it signs the exact
  bytes received. So awkward whitespace and non-ASCII in the body are fine as long as the sender
  signed the same bytes.
- **Nothing leaks.** A rejection returns `401`, writes nothing, and audits the reason and the handler
  but never the payload, the signature or the secret.

A handler that carries the clause but is invoked where no raw request bytes exist (not a real HTTP
request) fails loud rather than running unverified.

---

### Freshness narrows the replay window; it does not close it

`within N minutes` requires a timestamp, sent in the `X-Timestamp` header, and folds it into the
signature (the signature covers timestamp-then-body, so the timestamp cannot be altered on its own).
The window is symmetric: a timestamp too far in the future is rejected as well as one too far in the
past. Served with `within 5 minutes`:

```
fresh timestamp        -> 200  {"message": "ok"}
stale (10 min old)     -> 401  Signature timestamp outside the allowed window.
missing timestamp      -> 401  Missing signature timestamp.
```

**State it directly: freshness NARROWS the replay window, it does not close it.** Inside the tolerance,
a captured request still verifies and still runs. So do not call `within` replay protection. The real
defense, refusing a repeated event id (idempotency), is **not built yet** and is a named follow-on. A
sector app that omits the window is warned:

```
$ mio check nofresh.mho
  ! `/w` verifies the signature but sets no freshness window, so a captured valid request replayed
    later still verifies. Add a bound matched to how quickly the sender delivers:
  (Freshness narrows the window; full replay defense is idempotency, recording the sender's event
   id and refusing a repeat -- a follow-on.)
```

---

### Secret rotation under a regulated sector

A signing secret must be rotated, and under a regulated sector Mohio holds you to it. It reads two
environment variables **named after the secret**: `<SECRET>_ROTATED`, the date it was last rotated,
and `<SECRET>_ROTATION`, the interval (for example `90 days`). For `env.WEBHOOK_SECRET` those are
`WEBHOOK_SECRET_ROTATED` and `WEBHOOK_SECRET_ROTATION`.

Under a real regulated sector both are required. Expiry is checked at `mio check` and again at
startup, **never mid-request**, so an app refuses to start on an expired secret rather than failing a
live call:

```
$ mio check rot.mho            # WEBHOOK_SECRET_ROTATED unset
  x `/w` uses the secret env.WEBHOOK_SECRET under a regulated sector, so its rotation date must be
    recorded, and env.WEBHOOK_SECRET_ROTATED is not set. Set it to the date the secret was last
    rotated:

$ mio check rot.mho            # rotated 2020-01-01, interval 90 days
  x `/w` uses the secret env.WEBHOOK_SECRET, which is PAST its rotation interval (rotated 2020-01-01,
    interval 90 days). Under a regulated sector an app cannot start on an expired secret. Rotate the
    secret, then update WEBHOOK_SECRET_ROTATED to the new date.
```

With a current `WEBHOOK_SECRET_ROTATED` and `WEBHOOK_SECRET_ROTATION`, the file checks clean.

---

### Which control fits which endpoint

A signature is one of three controls a regulated sector accepts for an inbound request; the other two
are `require role` (a signed-in user) and a CSRF-protected browser form (a page a person submits). The
sector rule infers which kind of endpoint you have from what the handler does: one that `render`s or
`jump to`s is a browser form and is excluded; one that acknowledges with a status or JSON is
webhook-shaped and is flagged if it carries neither a role nor a signature. Full treatment, with the
sector error and the rotation rules, is in `Docs/guides/compliance.md`, "An inbound request must
prove who sent it."

---

### Common mistakes

- **Writing the secret as a literal.** Refused at check. Use `env.WEBHOOK_SECRET`.
- **Signing a re-serialized body.** Rejected. Sign the exact bytes you send; verification never
  re-encodes.
- **Calling `within` replay protection.** It narrows the window, it does not close it. Repeated-event
  idempotency is a separate, not-yet-built defense.
- **Forgetting the rotation variables under a sector.** A check error, and an expired secret stops the
  app at startup. Set `<SECRET>_ROTATED` and `<SECRET>_ROTATION`.
- **Expecting a rendering handler to be treated as a webhook.** It is a browser form; the signature
  rule does not apply to it. The rule keys on behavior, not on the path name.
