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

```mohio
hash Signup.password as hashed using bcrypt
save to db.members
    email    Signup.email
    password hashed
save: done
```

**Check a login attempt against the stored hash.** `check ... against ...`
detects the hash format itself (bcrypt, pbkdf2, or a plain sha256 checksum)
and verifies accordingly — real `bcrypt.checkpw` under the hood, not a string
comparison:

```mohio
check request.password against member.hashed_password
    on.success
        show "password matches"
    on.failure
        show "password does not match"
check: done
```

Every `check ... against ...` is now AUDITED: each password check writes an audit record (built
2026-10-04), so a login attempt, successful or failed, leaves a trail rather than passing silently.

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
    email as text required
    password as text required
shape: done

listen for sh.LoginRequest at /login
    retrieve.one member from db.members
        match email to LoginRequest.email
        when member is empty
            give back [401] "invalid credentials"
    retrieve.one: done
    check LoginRequest.password against member.hashed_password
        on.success
            grant role member.role
            give back [200] "ok"
        on.failure
            give back [401] "invalid credentials"
    check: done
listen: done
```

Verified end to end: a correct password returns `200`, a wrong password
returns `401`, and an unknown email returns `401` from `retrieve.one`'s own
`on.failure` — never a generic route-not-found.

## What `grant role` does to the session, and the trap in it

Two behaviors, both deliberate, and together they catch people out. This bit one project twice.

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

## Secure one-time links

A one-time link, for a confirm-by-email or a magic sign-in, is three primitives, all shipped and audited at every
step (verified by running, on SQLite, Postgres, MySQL, Oracle and MongoDB):

```mohio
shape Link
    member as text
    scope as text
    t as text
shape: done

// Issue a link. Only the token's SHA-256 is stored, with its purpose, scope and expiry.
listen for sh.Link at /make
    mioauth.token.create as approve_token
        for Link.member
        scope Link.scope
        expires in 1 hour
    mioauth.token.create: done
    give back [200] approve_token
listen: done

// A GET checks the token WITHOUT using it, so an email scanner that opens the link does not spend it.
request for sh.Link at /confirm
    verify token from Link.t as grant
        scope "approve:payment-*"
        on.failure
            give back [403] failure.message
        on.success
            give back [200] "confirm for " & grant.for
    verify: done
request: done

// A POST REDEEMS it once, in one conditional update, so two simultaneous redemptions cannot both succeed.
listen for sh.Link at /redeem
    verify token from Link.t as grant
        scope "approve:payment-*"
        on.failure
            give back [403] failure.message
        on.success
            give back [200] "redeemed for " & grant.for
    verify: done
listen: done
```

`mioauth.token.create as NAME` issues a 32-byte token and stores only its hash, with `for`, `scope`, an expiry
and used/revoked marks. `verify token from X as grant` checks the token on a GET (`request for`) and redeems it
once on a POST (`new`); `grant.for` is the subject it was issued for. `mioauth.token.revoke` revokes outstanding
tokens by `for` and `scope`:

```mohio
shape Link
    member as text
shape: done
listen for sh.Link at /revoke
    mioauth.token.revoke
        for Link.member
        scope "approve:*"
    mioauth.token.revoke: done
    give back [200] "revoked"
listen: done
```

## The forms guard is bound to the session

Mohio's form CSRF token is bound to the session it was issued to, so a token lifted from one session cannot be
replayed in another (verified by running). You write nothing for this: a form rendered through Mohio carries the
guard, and a POST is accepted only when the guard matches the posting session. A token that does not belong to
the session is refused the same way a missing one is.

## Why not `mioauth`

`mioauth`'s declared grammar (`with local`/`with google`, `password` policy,
`mfa`, `jwt`, `apikey`, `ldap`) is a separate, larger surface — OAuth
delegation, JWT issuance, MFA, API-key lifecycle. It is not built, and it was
never the intended home for a plain password check: its `mioauth.login`
statement has no field anywhere for a submitted email or password, so it
cannot express a login attempt at all. Real password login does not wait on
`mioauth` — the three primitives above are already shipped and already
correct.
