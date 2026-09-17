<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# A protected feature: search, validate, and gate a route together

*Search (`Docs/guides/mioql-user-guide.md`'s `contains`/`starts.with`/`ends.with`), validation
(`Docs/guides/form-field-types.md`'s `min`/`max`), and auth (`Docs/guides/password-login-howto.md`,
`cookbook/password-login.mho`) each already have their own doc. None of them show what a real
protected feature actually looks like: a route gated by role, taking a submitted, validated
value, and searching with it. This is that route, built and proven end to end.*

What you're building: a member directory search. Only a logged-in member may search it. The
search term itself is validated (at least 2 characters) before it ever reaches the database.

---

## The three pieces, and where each one already lives

- **Auth**: `require role "member"` on the route; `grant role` at login, once a password
  genuinely checks out. Full mechanics: `Docs/guides/password-login-howto.md`.
- **Validation**: a `min` rule on the shape field carrying the search term. Full list of field
  rules: `Docs/guides/form-field-types.md`.
- **Search**: `where <field> contains <value>`. Full predicate list (`starts.with`,
  `ends.with`, `is empty`, and more): `Docs/guides/mioql-user-guide.md`.

This doc's only job is showing the three wired together, verified, because that combination is
what a real protected feature needs and none of the three docs above build it alone.

---

## The complete app

```mohio
connect db as sqlite from env.DATABASE_URL

shape Member
    email as text required format "email"
    hashed_password as text required
    role as text required
shape: done

shape Login
    email as text required format "email"
    password as text required
shape: done

shape SearchQuery
    term as text required min 2
shape: done

listen for
    new sh.Login at /login
        retrieve.one member from db.members
            match email to login.email
        retrieve.one: done

        check member
            when member is empty
                give back [401] "Wrong email or password."
            otherwise
                check login.password against member.hashed_password
                    on.success
                        grant role member.role
                        give back [200] "Logged in."
                    on.failure
                        give back [401] "Wrong email or password."
        check: done
    new: done

    new sh.SearchQuery at /directory/search
        require role "member"
        find hits in db.members
            where email contains searchQuery.term
        find: done
        check hits.count
            when 0
                give back [200] "No matches."
            otherwise
                results ""
                repeat each hit in hits
                    results (results & "- " & hit.email & "\n")
                repeat: done
                give back [200] results
        check: done
    new: done
listen: done
```

Run it:

```bash
mio check directory.mho
mio serve directory.mho
```

---

## Every case, proven with a real request

Seed two members first (`hash "correct-horse-battery-staple" as stored_hash using bcrypt`, then
`save` each with that hash, as `cookbook/password-login.mho` shows), then:

**Search before logging in -- refused, not a 404, a real auth refusal:**
```bash
curl -X POST http://localhost:8080/directory/search -d '{"term":"al"}'
# 403 {"message": "Role required: member. No server-verified role is present for this
#      session -- roles are never read from the client request; establish one at login
#      with `grant role`."}
```

**Wrong password:**
```bash
curl -X POST http://localhost:8080/login \
  -d '{"email":"alice@example.com","password":"wrong"}'
# 401 {"message": "Wrong email or password."}
```

**Correct login, session cookie saved:**
```bash
curl -c cookies.txt -X POST http://localhost:8080/login \
  -d '{"email":"alice@example.com","password":"correct-horse-battery-staple"}'
# 200 {"message": "Logged in."}
```

**Search, logged in, a valid term -- finds the match, not the other member:**
```bash
curl -b cookies.txt -X POST http://localhost:8080/directory/search -d '{"term":"al"}'
# 200 {"message": "- alice@example.com\n"}
```

**Search, logged in, a term too short -- refused before it ever reaches the database:**
```bash
curl -b cookies.txt -X POST http://localhost:8080/directory/search -d '{"term":"a"}'
# 422 {"errors": {"term": "Term must be at least 2 characters."}}
```

All five run exactly as shown above, against the current compiler, real HTTP requests, a real
SQLite file read back to confirm which member matched.

---

## What each piece is actually doing

**The gate never trusts the client.** `require role "member"` checks a role the SERVER
established (`grant role`, only reachable after a real password check succeeds) -- never
anything the request itself claims. That's why the unauthenticated case above is a real 403
naming the reason, not a silent pass-through.

**The validation runs before the route body does.** `term as text required min 2` on
`SearchQuery` means a too-short term never reaches the `find` at all -- the 422 above happens
before a single line of the route's own code runs, the same guarantee `Docs/guides/form-field-types.md`
describes for any shape field.

**The search is a normal `find ... where ... contains ...`,** reading the ALREADY-validated
`searchQuery.term` -- no separate sanitizing step, because the value reaching this line has
already passed the shape's own rule.

---

## Two things worth knowing before you build this yourself

**A multi-word shape name binds to lower-camelCase, not all-lowercase.** `shape Feedback`
binds to `feedback` (one word lowercases to itself, easy to miss the actual rule). `shape
SearchQuery` binds to `searchQuery` -- only the FIRST letter is lowercased, the rest of the
name is untouched. Verified live: reading `searchquery.term` or `search_query.term` both fail
with `undeclared_variable`; `searchQuery.term` (matching the shape name with just its first
letter lowered) is the one that resolves.

**`check <value> against <value>` does not take its own closer when nested inside another
`check`'s `otherwise` branch.** Writing `on.success`/`on.failure` under it, then adding a
`check: done` for it specifically, breaks the OUTER check's own closer matching (verified: it
produces a "closer mismatch" pointing at the wrong block). The outer `check member / when /
otherwise / check ... against ... / on.success / on.failure` needs exactly ONE closing
`check: done`, for the outer block only -- `cookbook/password-login.mho` already gets this
right; it's easy to get wrong copying the pattern by eye instead of from the file.

---

## Where to go deeper

- **Auth mechanics in full** (hashing, sessions, magic-link as an alternative to a password) --
  `Docs/guides/password-login-howto.md`, `cookbook/password-login.mho`, `cookbook/session-timeout.mho`.
- **Every field validation rule** (`pattern`, `accept`, file uploads, custom error text) --
  `Docs/guides/form-field-types.md`.
- **Every search predicate and query form** -- `Docs/guides/mioql-user-guide.md`.
- **The mental model and first app**, if this is early in your reading order --
  `Docs/guides/mohio-mental-model-no-classes.md`, `Docs/guides/mohio-first-app-tutorial.md`.
