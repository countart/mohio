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
                give back 401 "invalid credentials"
        retrieve.one: done
        check request.password against member.hashed_password
            on.success
                grant role member.role
                give back 200 "ok"
            on.failure
                give back 401 "invalid credentials"
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

Both are logged as a known compiler gap (`CLAUDE-CODE-BACKLOG.md`), not
something to work around forever — but until it's fixed, write `check`
exactly as shown above: last statement, sibling to (not nested inside)
whatever finds the record it's checking against.

## Why not `mioauth`

`mioauth`'s declared grammar (`with local`/`with google`, `password` policy,
`mfa`, `jwt`, `apikey`, `ldap`) is a separate, larger surface — OAuth
delegation, JWT issuance, MFA, API-key lifecycle. It is not built, and it was
never the intended home for a plain password check: its `mioauth.login`
statement has no field anywhere for a submitted email or password, so it
cannot express a login attempt at all. Real password login does not wait on
`mioauth` — the three primitives above are already shipped and already
correct.
