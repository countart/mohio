<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Part IX -- Operational Guarantees

## Chapter 40: Outbound HTTP -- connectors and miohttp, and what is not yet guaranteed

Mohio makes an outbound HTTP call two ways. `mioconnect` declares a connector once (the connection and its
`Connector.operation with PAYLOAD as RESULT` calls); `miohttp.get` / `.post` / `.put` / `.delete` / `.patch`
makes a direct one-off call. Both are built and run. This chapter covers how each carries HEADERS and
AUTHENTICATION, and the RELIABILITY guarantees around a connector call, because for a payment call the difference
between built and planned is money.

**A connector call is an egress boundary.** A `[phi]`, `[pci]` or `[pii]` value in a connector payload (raw or
shaped, including one built with `create`) is refused at check unless it is masked or the record is sent
`stripped`; `miohttp` follows the same rule as an AI call. See the egress gate in `Docs/guides/compliance.md`.

## Headers and authentication

An outbound call carries two different things on its way out: HEADERS, which are DATA, and a CREDENTIAL, which is
a SECRET. Mohio keeps them on separate lines because they cross the egress boundary differently, and the compiler
enforces the split.

**The secret-egress rule, enforced at `mio check`.** A secret may leave your app only as a credential: through
`auth` on a `miohttp` call, or a connector's own `auth` line. A `secret.X` placed in a `header` line is refused
at check, because a header is data and a secret is not data to hand around. Writing
`header "X-Key" secret.API_KEY` on a `miohttp.get` is refused:

    Line N: `secret.API_KEY`, or a value made from it, would reach the "X-Key" header of miohttp.get. A secret
    is used only where it is declared for, a connector's credential or the `auth` of an outside call, and is
    never shown, logged, sent elsewhere or stored, masked or not.
    To fix it, pass it only as a credential (`auth secret.API_KEY` on the call, or `auth bearer secret.API_KEY`
    in the connector's block); to show that it is set, check that it exists rather than showing it.

That is why `header` and `auth` are separate statements: `header` is the data channel, and `auth` is the egress
channel for a secret.

### miohttp headers and authentication

On a `miohttp` call, `header "Name" value` adds any header you name, and `auth value` authenticates the call.
Two separate lines, two separate jobs:

```mohio
miohttp.get "https://api.example.com/orders"
    header "Accept" "application/json"
    auth secret.API_TOKEN
miohttp.get: done
```

`header` takes a non-secret value (a content type, a trace or request id); a secret belongs on `auth`, per the
rule above.

### mioconnect authentication

A `mioconnect` connector declares its credential with `auth`, which has four forms today, each verified by
running:

```mohio
mioconnect Keyed
    address env.A_URL
    auth key env.A_KEY
mioconnect: done
```

```mohio
mioconnect Bearer
    address env.B_URL
    auth bearer secret.B_TOKEN
mioconnect: done
```

```mohio
mioconnect Basic
    address env.C_URL
    auth basic env.C_USER env.C_PASS
mioconnect: done
```

```mohio
mioconnect CustomHeader
    address env.D_URL
    auth header "X-Api-Key" secret.D_KEY
mioconnect: done
```

`auth key`, `auth bearer` and `auth basic` name a scheme; `auth header "Name" value` is the custom-header AUTH
form, for a provider that authenticates through a header it names, such as `X-Api-Key`. A secret is allowed on
any of them, because `auth` is the egress channel.

**mioconnect has no standalone `header` line today.** A non-auth custom header on a connector goes through
`auth header "Name" value` for now (as the `Idempotency-Key` example later in this chapter does).

**The timeout, built (verified by running).** A `timeout` on the connector bounds every call it makes, and an
operation's own `timeout` overrides the connector's. Write it in seconds:

```mohio
mioconnect Payments
    address secret.PAYMENTS_URL
    timeout 10 seconds
    operation charge
        path "/charge"
        method POST
        timeout 5 seconds
    operation: done
    on.failure
        show "provider=" & failure.provider
        show "stage=" & failure.stage
        show "error=" & failure.error
mioconnect: done
```

A call that exceeds its timeout fails into `on.failure`, where `failure` carries `failure.provider` (the
connector), `failure.stage` (`call`) and `failure.error` (naming the timeout). A zero timeout refuses at check,
and a `webhook` setting, accepted but not executable, refuses at check with a teaching message.

**The connectivity retry, built.** `retry N times` on the connector retries a call N more times, but only on a
genuine connectivity failure, where the request never reached the other side, and never on an HTTP error
response. That retry is safe because a connection that never opened could not have acted.

**Setting your own Idempotency-Key.** Every CHANGING outbound call (`POST` and the other write methods) is sent
with an `Idempotency-Key` header. Mohio generates one per call when you do not set your own, and a `GET` carries
none. To set YOUR OWN key, use the header form on the connector:

```
mioconnect Pay
    address env.PAY_URL
    auth header "Idempotency-Key" payment.business_key
mioconnect: done
```

Set it to a stable BUSINESS KEY (an order id, a payment id), so the provider can find the same payment on a
retry or a status lookup instead of treating it as a new charge. Mohio's auto-generated key is a fresh value per
call, so it protects a retry of a request that never left; a business-level duplicate is what your own key
guards against.

**What is NOT built yet.** The retry above does NOT make a payment call idempotent. For an outbound call today,
Mohio still does NOT provide:

- AUTOMATIC DEDUPLICATION (Mohio deduplicating a retried or duplicated call that DID reach the other side FOR
  YOU; you can set your own `Idempotency-Key` as above so the PROVIDER can, but Mohio does not yet dedup for you);
- INDETERMINATE-RESULT handling (a call that times out after the other side may or may not have acted);
- A RETRY POLICY for that indeterminate case (the connectivity retry above does not cover it);
- PROVIDER EVENT CORRELATION (matching an inbound webhook back to the outbound call it answers).

These are the reliability layer, tracked as `T1-CONNECTOR-RELIABILITY`. The inbound provider-webhook handler
(`from <connector>`) that event correlation needs refuses until its executor lands, tracked as
`T1-FROM-CONNECTOR-EXECUTOR`.

**What this means for you now.** The timeout bounds the call and the connectivity retry covers a call that never
left, but once a call has reached the other side there is no idempotency key to make a retry safe. Until the
reliability layer lands, treat a payment or transfer that may have been received as needing the recovery your
regulator requires built in the application, and expect to replace it with the built layer when
`T1-CONNECTOR-RELIABILITY` ships.
