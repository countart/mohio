<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md. -->
# Talking to the Outside World: mioconnect

Most real programs need to reach an external service at some point. Charge a card,
look up an address, send a text, ask another API a question. In Mohio you do this
with **mioconnect**, in two clear steps:

1. **Declare** the connector once: where it lives, how it authenticates, and what
   operations it offers.
2. **Call** an operation wherever you need it, and bind the response to a name.

That is the whole model. A connector is a named door to an outside service, and an
operation is one thing you can do through that door.

---

## Declaring a connector

A connector has an address and one or more operations. Each operation has a path
and an HTTP method.

```mohio
mioconnect PyPI
    address "https://pypi.org"
    operation lookup
        path "/pypi/lark/json"
        method GET
    operation: done
mioconnect: done
```

That declares a connector named `PyPI` with one operation, `lookup`. The address is
the base; the operation path is added onto it, so `lookup` calls
`https://pypi.org/pypi/lark/json`. The block opens with `mioconnect` and closes with
`mioconnect: done`, and each operation opens with `operation` and closes with
`operation: done`. Same rule as everywhere in Mohio: every block you open, you close.

A connector can hold as many operations as you like:

```mohio
mioconnect Stripe
    address "https://api.stripe.com/v1"
    operation charge
        path "/charges"
        method POST
    operation: done
    operation refund
        path "/refunds"
        method POST
    operation: done
mioconnect: done
```

---

## Authentication

Credentials never go in your source. They come from the environment, and Mohio
resolves them at the moment a call is made. There are three forms.

**Bearer token** is the most common, used by most modern APIs:

```mohio
mioconnect Stripe
    address "https://api.stripe.com/v1"
    auth bearer env.STRIPE_KEY
    operation charge
        path "/charges"
        method POST
    operation: done
mioconnect: done
```

Every call to `Stripe` then sends `Authorization: Bearer <your key>`.

**A custom header** is for services that want the key in a named header:

```mohio
mioconnect Weather
    address "https://api.weather.example"
    auth header "X-API-Key" env.WEATHER_KEY
    operation forecast
        path "/forecast"
        method GET
    operation: done
mioconnect: done
```

**Basic auth** takes a username and a password, both from the environment:

```mohio
mioconnect Legacy
    address "https://api.legacy.example"
    auth basic env.LEGACY_USER env.LEGACY_PASS
    operation status
        path "/status"
        method GET
    operation: done
mioconnect: done
```

If a credential resolves to nothing at call time (a missing environment variable),
the call fails loud and tells you which connector. It never sends an empty
credential and hopes.

---

## Calling an operation

To call an operation, name it as `Connector.operation`, hand it a payload with
`with`, and bind the response with `as`:

```mohio
Stripe.charge with payment as receipt
```

That sends `payment` as the JSON body of a POST to the charge endpoint and binds the
response to `receipt`.

For an operation that takes no body, such as most GET requests, leave off `with`:

```mohio
PyPI.lookup as package
```

The body is never sent on a GET, so you do not need a placeholder payload.

---

## The response

Whatever you bind with `as` comes back as a shape with five fields:

| Field | What it holds |
|---|---|
| `status` | the HTTP status code, e.g. `200` |
| `ok` | `true` when the status is 2xx, otherwise `false` |
| `json` | the response parsed as JSON, ready to read with dotted names |
| `body` | the raw response text |
| `headers` | the response headers |

So after a call you can read straight into the result:

```mohio
mioconnect PyPI
    address "https://pypi.org"
    operation lookup
        path "/pypi/lark/json"
        method GET
    operation: done
mioconnect: done

PyPI.lookup as package
show package.status
show package.json.info.name
```

---

## Branching on the result

The honest pattern is to check `ok` and branch:

```mohio
Stripe.charge with payment as receipt
check receipt.ok
    when true   give back 201 "Order confirmed"
    otherwise   give back 502 "Payment failed"
check: done
```

`receipt.ok` is `true` only on a 2xx response, so a declined card or a network
failure both land in `otherwise`, where you decide what the caller sees.

---

## When something goes wrong

mioconnect fails loud, never silently. Three cases tell you exactly what to fix:

- Calling a connector you never declared names it and reminds you to declare it.
- Calling an operation the connector does not have lists the operations it does
  have, so you can spot the typo.
- A credential that resolves empty names the connector so you know which
  environment variable to set.

A failed HTTP call is different from a broken program. If the service returns a 404
or a 500, that is a real response: `status` carries the code, `ok` is `false`, and
your `check receipt.ok` handles it. The program keeps its footing.

---

## Inside an agent: the boundary gate

When a connector call happens inside an `ai.agent` block, it counts against that
agent's budget. Alongside `max steps`, `max tokens`, and `cost ceiling`, you can set
`max calls` to cap how many external calls the agent may make:

```mohio
ai.agent researcher
    goal "Look up the package and summarize it"
    limits
        max steps  5
        max calls  3
    limits: done
    not confident
        give back "Could not complete the research"
ai.agent: done
```

The counter lives inside the interpreter, not in your program's variables, so nothing
the agent generates can reset it or talk its way past it. Cross the ceiling and the
boundary gate stops the agent and routes it to its `not confident` recovery path. This
is the same gate that bounds steps and cost: external calls are simply another
metered resource.

---

## A complete example

A small charge endpoint, end to end. It declares the connector, takes a payment,
calls Stripe, and answers based on what came back.

```mohio
mioconnect Stripe
    address "https://api.stripe.com/v1"
    auth bearer env.STRIPE_KEY
    operation charge
        path "/charges"
        method POST
    operation: done
mioconnect: done

hold payment = "tok_visa"

Stripe.charge with payment as receipt
check receipt.ok
    when true   give back 201 "Order confirmed"
    otherwise   give back 502 "Payment failed"
check: done
```

Declare the door, name the operation, call it, read the answer. A non-technical
reader can follow the intent in a few seconds, which is the point.

---

## What is not here yet

mioconnect is built on plain HTTP, which covers the overwhelming majority of services
you will reach. A few things are deliberately still ahead:

- **Shape checking on `sends` and `returns`.** An operation can declare the shapes it
  sends and returns, and those are recorded, but they are not yet enforced. For now
  the payload you pass and the response you read are up to you.
- **MCP transport.** Reaching Model Context Protocol servers is planned as a second
  transport after HTTP.

Neither blocks building a real application today. Declare a connector, call its
operations, branch on the result, and you have a program that talks to the world.
