<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Part IX -- Operational Guarantees

## Chapter 40: Connector calls, and what is not yet guaranteed

An outbound integration call goes through `mioconnect` (the connection declaration and the
`Connector.operation with PAYLOAD as RESULT` call). The call itself is built and runs. This chapter is about the
RELIABILITY guarantees around it, because for a payment call the difference between built and planned is money.

**A connector call is an egress boundary.** A `[phi]`, `[pci]` or `[pii]` value in a connector payload (raw or
shaped, including one built with `create`) is refused at check unless it is masked or the record is sent
`stripped`; `miohttp` follows the same rule as an AI call. See the egress gate in `Docs/guides/compliance.md`.

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
