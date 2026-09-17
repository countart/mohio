<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Real password login

Three already-built primitives are all a real login needs: `hash`, `check ...
against ...`, and `grant role`. No `mioauth` declaration, no new syntax. This
page shows the full flow. For a runnable, self-contained version see
`cookbook/password-login.mho`.

## The three pieces

**Hash a password before storing it** (see `password-min-howto.md` for the
minimum-length rule on the field itself):

```
hash signup.password as hashed using bcrypt
save to db.members
    email    signup.email
    password hashed
save: done
```

**Check a login attempt against the stored hash.** `check ... against ...`
detects the hash format itself (bcrypt, pbkdf2, or a plain sha256 checksum)
and verifies accordingly — real `bcrypt.checkpw` under the hood, not a string
comparison:

```
check request.password against member.hashed_password
    on.success
        show "password matches"
    on.failure
        show "password does not match"
```

**Grant a role on success.** `grant role` is the same server-verified
mechanism every other role-gated route already uses — it establishes the role
on the session, not on anything the client can forge:

```
grant role member.role
```

## The full route

A real login endpoint composes all three, plus a `retrieve.one` to find the
account by email:

```
shape Member
    email as text required
    hashed_password as text required
    role as text required
shape: done

shape LoginRequest
    method POST
shape: done

listen for
    new sh.LoginRequest at /login
        retrieve.one member from db.members
            match email to request.email
            on.failure
                give back [401] "invalid credentials"
        retrieve.one: done
        check request.password against member.hashed_password
            on.success
                grant role member.role
                give back [200] "ok"
            on.failure
                give back [401] "invalid credentials"
    new: done
listen: done
```

Verified end to end: a correct password returns `200`, a wrong password
returns `401`, and an unknown email returns `401` from `retrieve.one`'s own
`on.failure` — never a generic route-not-found.

## A current ordering constraint — read this before nesting `check`

`check ... against ...` has no closer of its own (no `check: done`). Two
consequences, both real and both currently true:

- **`check ... against ...` must be the last statement in its enclosing
  block.** Anything placed after it as a sibling — another statement, another
  `check` block — is silently dropped from execution. In the route above,
  notice `check` is the very last thing before `new: done`.
- **Do not nest `check ... against ...` inside another block's `on.success`
  when that outer block also has a sibling `on.failure`.** The outer
  `on.failure` can be mis-attached and never run. This is why the route above
  keeps `retrieve.one` and `check` as siblings (each with its own `on.failure`)
  rather than nesting the check inside `retrieve.one`'s `on.success`.

Both are logged as a known compiler gap in the project backlog, not
something to work around forever — but until it's fixed, write `check`
exactly as shown above: last statement, sibling to (not nested inside)
whatever finds the record it's checking against.

## What `grant role` does to the session, and the trap in it

Two behaviours, both deliberate, and together they catch people out. This bit one project twice.

**It REPLACES the role set, it does not add to it.** A grant states what the session's roles are
now. That is the safe direction: merging would let somebody who logs back in as a lower role keep
a privilege from an earlier login on the same session. To hold several roles at once, grant them
together as a list, not one at a time.

```mohio
grant role member.roles
```

**It rotates the session id whenever the role set actually changes**, and only then. Granting the
same roles again is an idempotent re-assertion and rotates nothing, which is why a route that
grants `"player"` on every request is not churning sessions. A rotation issues a new session id,
and a request still carrying the previous cookie is treated as invalidated.

**The trap.** A flow that alternates role sets across requests rotates on every alternation. If
one route grants `member.role` and another grants `member.role` plus something extra, the session
id changes each time the caller moves between them, and anything holding the older cookie, a
parallel request or a retried one, lands on a fresh session instead of the one it expected. It
shows up as intermittent lost state rather than as an error, which is what makes it expensive to
find.

Keep the role set stable across requests unless you mean to rotate. Grant the full set the caller
should have, once, and let `require role` do the checking after that.

## Why not `mioauth`

`mioauth`'s declared grammar (`with local`/`with google`, `password` policy,
`mfa`, `jwt`, `apikey`, `ldap`) is a separate, larger surface — OAuth
delegation, JWT issuance, MFA, API-key lifecycle. It is not built, and it was
never the intended home for a plain password check: its `mioauth.login`
statement has no field anywhere for a submitted email or password, so it
cannot express a login attempt at all. Real password login does not wait on
`mioauth` — the three primitives above are already shipped and already
correct.
