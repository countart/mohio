<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Part V -- Web and delivery

## Chapter 20 (continued): Route Path Parameters -- `{{id}}` in a path

*Every example below was served with `mio serve` and hit with a real `curl` request; the request and
the response it returned are shown. Nothing here is derived from the design.*

Chapter 20 covered fixed routes: `request for sh.User at /users`. Often the address itself carries a
value -- `/users/42`, `/orders/A-19` -- and the handler needs that value. You write the value's name
in double braces inside the path:

```mohio
shape User
    id as int
shape: done

request for sh.User at /users/{{id}}
    give back [200] ("Bare " & id)
request: done
```

The segment that fills `{{id}}` in the address becomes a bare variable named `id` inside the handler.

```
$ curl http://localhost:8080/users/99
{"message": "Bare 99"}
```

---

### Read it by its bare name, not through `request`

This is the trap to learn first, because it reads plausible and fails at run time. The path value is
**not** a field on `request`. Reading it as `request.id` fails loud:

```mohio
request for sh.User at /users/{{id}}
    give back [200] ("via request.id: " & request.id)
request: done
```

```
$ curl http://localhost:8080/users/42
{"message": "'id' is not a field on 'request' -- known fields: (none). A field that exists but is
empty stays None; a field that was never there fails loud instead of silently returning None."}
[http 500]
```

The name inside `{{ }}` in the path is the name you read: bare `id`. In a `render` block, display it
the same way you display any value, with `{{ id }}`:

```mohio
request for sh.User at /show/{{id}}
    render
        <p>Viewing user {{ id }}</p>
    render: done
request: done
```

```
$ curl http://localhost:8080/show/7
<p>Viewing user 7</p>
```

---

### When the value is missing, the route does not match

A path parameter is part of the address. If the segment is not there, the address is a different one,
so the request simply does not reach this handler and Mohio answers `404`:

```
$ curl -o /dev/null -w "%{http_code}" http://localhost:8080/users/
404
$ curl -o /dev/null -w "%{http_code}" http://localhost:8080/users
404
```

So there is no "missing path parameter" case to handle inside the handler: if your handler runs, the
value was there. A blank or absent segment is a routing miss, not an empty variable.

---

### How it interacts with shapes: it does not

This surprises people coming from frameworks where a path value is validated by the route's schema.
In Mohio the path parameter is a bare variable lifted straight from the URL. **The shape's fields do
not validate it, and it does not have to be a field on the shape at all.**

The shape here declares `id as int`, yet a non-numeric segment binds and runs anyway:

```
$ curl http://localhost:8080/users/abc
{"message": "via bare id: abc"}  [http 200]
```

And a shape that never mentions `id` still receives the path value:

```mohio
shape Lookup
    q as text
shape: done

request for sh.Lookup at /item/{{id}}
    give back [200] ("item id is " & id)
request: done
```

```
$ curl http://localhost:8080/item/500
{"message": "item id is 500"}  [http 200]
```

The shape validates the request body and its declared fields (Chapter 16); the path parameter is a
separate thing that comes from the address. If you need it as a number, cast it yourself and it is
yours to trust from there:

```mohio
shape User
    id as int
shape: done

request for sh.User at /users/{{id}}
    user_id id as.int
    next_id (user_id + 1)
    give back [200] ("next id is " & next_id)
request: done
```

```
$ curl http://localhost:8080/users/42
{"message": "next id is 43"}  [http 200]
```

The cast (`id as.int`) is the boundary where a bad value would fail loud, so put it right at the top
of the handler and work with the cast value below it.

---

### Common mistakes

- **Reading `request.id`.** A `500`: the path value is not a field on `request`. Read the bare name
  from the braces, `id`.
- **Expecting the shape's `id as int` to reject a non-numeric segment.** It does not. The path
  parameter binds unvalidated; cast it (`id as.int`) if you need a number.
- **Handling a missing path parameter inside the handler.** You never reach the handler without it --
  a missing segment is a `404` at routing time, not an empty variable.
- **Nesting parentheses in one expression**, as in `("x " & (n + 1))`. Compute the math on its own
  line first (`next (n + 1)`), then concatenate (`"x " & next`).
