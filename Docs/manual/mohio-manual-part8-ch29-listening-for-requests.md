<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Part VIII -- The Web Layer

## Listening for Requests, and Responding

*Spans this Part's Chapter 29 (Listening for Requests) and Chapter 30 (Responding) in one pass.
Every example below is reused, verbatim, from already-verified content -- `Docs/mohio-first-app-
tutorial.md`'s search-box stage, hit with real `curl` requests against a real served route -- not
re-derived. Reuse per the standing instruction to build on verified work rather than repeat it.*

---

### `listen for sh.X` and `request for sh.X` -- each handler stands on its own

```mohio
connect db from env.DATABASE_URL

shape Feedback
    name as text required
    email as text required format "email"
    message as text required min 5 max 500
shape: done

request for sh.Feedback at /feedback/search
    find hits in db.feedback
        where message contains request.q
    find: done
    check hits.count
        when 0
            give back [200] "No matches."
        otherwise
            results ""
            repeat each hit in hits
                results (results & "- " & hit.name & ": " & hit.message & "\n")
            repeat: done
            give back [200] results
    check: done
request: done
```

`request for sh.X at <path>` answers a GET; `listen for sh.X at <path>` (seen in earlier chapters)
answers a POST. Both bind the request to the named shape, so every field rule from Chapter 16
already applies before your own code runs.

**`new` is not how you declare a handler.** To receive a submission, a form post or an API write, you
LISTEN FOR it: `listen for sh.Signup at /signup`. The word `new` is reserved for a condition planned
INSIDE a `listen for`, one that acts only on the genuinely new arrival when many of the same shape
arrive at once (a double-clicked submit, a game); that condition is not built yet. From 6.0.0 the
compiler refuses `new sh.X` as a handler and shows you the swap to `listen for sh.X`. If you have
older code, change `new sh.X at /path` to `listen for sh.X at /path` and its `new: done` to
`listen: done`; the two behave identically.

**A handler with no path binds to its page.** The `at /path` is optional on a single page. A
`request for sh.X` or `listen for sh.X` written with NO `at` answers at the file's own conventional
address, the page it sits on. Verified by running a single file whose only handler is pathless: a GET to
`/` returns the page, and the shape-named paths 404.

```mohio
shape Q
    q as text
shape: done
request for sh.Q
    give back [200] "pathless home"
request: done
```

```
GET /   -> 200  pathless home
GET /Q  -> 404
```

**Inside a journey, a path is REQUIRED.** A journey is not a page, so a handler written loose inside one
has no page to bind to and must say where it answers. A pathless handler inside a journey refuses, at
check AND at run, naming the line and the journey (verified by running):

```
Line 6: `request for sh.Q` inside journey `Shop` has no `at /path`. A handler on a page answers at that page's own address, but a journey is not a page, so this handler would have no address of its own. Say where it answers, for example `request for sh.Q at /orders`.
```

### The day-two trap: only the LAST `show` becomes the response

Inside a served route, calling `show` several times does not build up a response the way it does
in a bare `mio run` script -- only the final `show` in the handler is what the caller actually
receives. (A `give back` is different: it ENDS the request the moment it runs, so the FIRST one
reached wins, not the last. See the next section.) This is why the block above builds one text
value (`results`, restated each pass through the loop to grow it) and gives it back once at the
end, instead of calling `show` per row. A handler that calls `show` per item and expects them all
to reach the caller will see only the last one.

### A `give back` ends the request, the moment it runs

A `give back` with a status does not just set the response, it ENDS the request right there. The
first `give back` reached is the one the caller gets, and nothing written after it runs, wherever
that `give back` sits: inside a `check ... when` branch, inside a typed task the handler calls,
inside a `transaction`, or inside an `on.failure` handler. Verified by running, through a real POST:

```mohio
shape Order
    amount as int
shape: done
listen for sh.Order at /order
    check Order.amount
        when Order.amount is more than 100
            give back [200] "FIRST wins, the request ends here"
        otherwise
            give back [400] "too small"
    check: done
    give back [200] "this line after the check never runs"
listen: done
```

```
POST /order {"amount":150}
{"message": "FIRST wins, the request ends here"}
```

The `give back` inside the `when` branch answered and ended the request; the `give back` written
after the `check` block never ran.

**What changed in 6.0.0, and what to check.** Before, a `give back` inside a nested block (a named
`check`, a typed task, a transaction, an `on.failure`) could fall through, so execution carried on
to whatever came after the block and the LAST `give back` reached was the response. Now the FIRST
one ends the request. A program behaves identically wherever there was only one `give back` on the
path; the behaviour changes only where code relied on running PAST a refusal. If a handler
deliberately did work after an early `give back` (a refusal followed by a compensating write, say),
that work no longer runs, so move it ahead of the `give back`. A refusal inside a transaction now
also rolls the transaction back (Chapter 43), so a write made before the refusal does not survive.

In an `on.failure` handler the same rule holds: a `give back` there ends the request on a genuinely
broken lookup, such as a lost connection or a missing table. `on.failure` does NOT fire when a
lookup simply finds no row, because that is a normal empty result. Refuse the empty result with
`when <var> is empty` inside the lookup block, and keep `on.failure` for the broken-lookup case (for
example `give back [503]`). Writing the not-found refusal in `on.failure` is refused at check, with a
message that names the fix.

### The other day-two trap: a GET query parameter reads as `request.q`, not through the shape

```bash
curl "http://localhost:8080/feedback/search?q=search"
# {"message": "- Ada: Love the search box!\n"}

curl "http://localhost:8080/feedback/search?q=zzz"
# {"message": "No matches."}
```

A search term typed into a URL (`?q=...`) is not a field the shape declares -- it is read directly
off the request with `request.q`, never through `sh.Feedback`'s own fields. `where message
contains request.q` reads naturally, but the field-versus-request-property distinction is easy to
miss coming from a framework where a query parameter and a form field are handled the same way.

### A handler that writes more than once is all-or-nothing

If a handler makes MORE THAN ONE database write, those writes succeed together or not at all. The
handler runs in one transaction, and the RESPONSE STATUS decides its fate: a **2xx or a 3xx commits**,
a **4xx or a 5xx rolls back**, and anything that throws rolls back. So the client is never told one
thing while the database holds another: the status the caller receives and the rows that were kept
always agree. Verified by running a two-write handler and reading the table back after each answer:

```
decision=ok    -> 201  both rows persist
decision=redir -> 302  both rows persist   (3xx commits)
decision=bad   -> 400  rows rolled back    (table unchanged)
decision=boom  -> 500  rows rolled back    (table unchanged)
```

```mohio
listen for sh.Order at /order
    save to db.orders
        ref request.ref
    save: done
    save to db.audit
        event "order-created"
    save: done
    check request.ref
        when request.ref is empty
            give back [400] "missing ref"    // both writes above roll back
        otherwise
            give back [201] "created"         // both writes commit together
    check: done
listen: done
```

Three things worth knowing:

- **A single write is NOT transactional.** One write is atomic on its own, but outside a transaction it
  is not rolled back by a later refusal: a handler that makes one write and then answers `[400]` KEEPS
  that row (verified by running). Decide before you write, or wrap the write in a `transaction`. The full
  treatment is in Chapter 43.
- **Your own `transaction` or `saga` keeps its own boundaries.** A handler that opens one is not
  double-wrapped; you stay in control of where it begins and ends.
- **On MongoDB a multi-write handler REFUSES, at run.** A multi-write handler must be atomic, and a
  MongoDB transaction around several writes is declared but NOT BUILT in this release
  (`T1-MONGO-PROGRAM-TRANSACTION`): Mongo multi-document transactions need a replica set (they are built
  on the oplog, which a standalone node lacks). The engine is known only from the live connection, so
  this refuses at RUN, not at check, and nothing in the handler runs:

  ```
  this handler makes more than one write, so it must be atomic -- all of its writes or none -- and a MongoDB `transaction` around several writes is declared but not built in this release (T1-MONGO-PROGRAM-TRANSACTION). Nothing in the handler was run.
    To proceed: make the handler one write (a single write is atomic on its own), use a `saga` with a `compensate` for each step, or run it on Postgres, MySQL, Oracle or SQLite until the MongoDB transaction lands.
  ```

  SQLite, PostgreSQL, MySQL and Oracle need nothing extra.

What this changes for you: you no longer have to reach for an explicit `transaction` block just to keep
a handler's writes consistent under a mid-way refusal. That block is still there for finer control and
for writing outside a handler (see the query guide's "all-or-nothing" section), but the ordinary case,
a handler that writes and might refuse, is now safe by default.

### When a handler fails, the client gets a reference, not the detail

A deliberate `give back` is your message to the caller. A SERVER FAILURE is different: when a handler
throws, Mohio answers 500 with ONE plain sentence and a REFERENCE ID, and nothing else. The cause, the
table name, the driver text, the stack, none of it reaches the client; it goes to the server log under
that same reference, where you can find it. Verified by running, a handler that hits a runtime error:

```
GET /broken  ->  500
{"message": "Something went wrong on our side, and it has been recorded.", "reference": "ed3b104f393b"}
```

```
[mohio.error] reference=ed3b104f393b status=500 :: db_error: no such table: never_declared_anywhere
```

The caller sees the sentence and the reference `ed3b104f393b`; the log carries the detail under the
identical reference. So a user can quote the reference in a support ticket, and you can find the one log
line that explains it, without ever leaking the internals to the response. (A `give back [500]` you write
yourself is your own sentence and is not replaced; this is about an unhandled failure.)

### Who is acting: the durable person at the auth boundary

`require role` says a caller MAY act; the durable person says WHO is acting, and keeps it for the whole session.
At the authentication boundary, `grant role` takes a `for person` clause:

```mohio
shape Login
    who as text
shape: done
listen for sh.Login at /login
    grant role "manager" for person "alice"
    give back [200] "ok"
listen: done
```

That sets `session.person` to "alice" for the session. It is read-only and set once: a second
`grant ... for person` refuses, and setting `session.person` outside the boundary refuses, so the acting person
cannot be rewritten mid-session. It survives session rotation, and it is stamped automatically onto every audit
record, so the trail says who did each thing without you passing it around. When an `ai.agent` acts,
`session.agent` is set alongside and the person is kept, since the agent acts on the person's behalf.

Read them as `session.person` and `session.agent`. An ABSENT person is one representation everywhere, and a
comparison involving an absent person refuses loudly in BOTH directions, so a separation-of-duties check cannot
quietly pass with nobody there. To test for presence, use the sanctioned `when session.person is empty`.

```mohio
shape Approve
    requester as text
shape: done
listen for sh.Approve at /approve
    require role "manager"
    check session.person
        when session.person is Approve.requester
            give back [403] "cannot approve your own work"
        otherwise
            give back [200] "approved"
    check: done
listen: done
```

That is maker-checker end to end: the same person cannot approve their own work, a different person can.
`grant role` with no `for person` behaves exactly as before. The free runtime carries an ASSERTED person; a
sector that requires a VERIFIED one waits on the profile rules and is not built yet.

### `require person`, and the two refusals a gate can give

`require role "x"` says the caller must hold a role; `require person` says only that SOMEONE is signed in, whoever
they are. Both are gates, and both can refuse in two DIFFERENT ways, with two different statuses, so the caller can
tell "you are not signed in" apart from "you are signed in but may not do this".

```mohio
listen for sh.Mine at /mine
    require person              // anyone signed in, whoever they are
    give back [200] "hello"
listen: done
```

- **Not signed in, 401.** A request with nobody signed in is refused 401, and the message asks them to sign in and
  names no role (verbatim from a running server):
  ```
  401 {"message": "authentication_required: Please sign in. This page needs someone signed in, and nobody is (a handler signs a person in with `grant role \"<role>\" for person <who>`)."}
  ```
  A role-gated route gives the SAME 401 to an unauthenticated caller: someone not signed in gets 401, never 403,
  because the first question is whether anyone is there at all.

- **Signed in, wrong role, 403.** A caller who IS signed in but lacks the role gets 403, and the message names BOTH
  the role needed and the roles held (verbatim from a running server):
  ```
  403 {"message": "Role required: admin. You are signed in with viewer, and this needs admin. Roles are never read from the client request; they are established at sign-in with `grant role`."}
  ```

The split matters for the caller's own logic: 401 means "sign in and try again", 403 means "signing in will not
help, you lack the role". Roles are never read from the request; they are established at the authentication
boundary with `grant role`.

### Ending every session a person holds

`end session` logs out the current caller. `end every session for person <who>` is the administrative version:
it ends EVERY session that person holds, across every session store, each ending audited. It is THE pattern for
disabling or banning an account and for signing someone out everywhere -- a stolen laptop, a leaver, a reset
password, an account you are disabling -- where ending the one request that asked is not enough:

```mohio
shape Revoke
    who as text
shape: done
listen for sh.Revoke at /revoke
    require role "admin"
    end every session for person Revoke.who
    give back [200] "every session that person holds is ended"
listen: done
```

Verified by a served run. The same person (`M10045`) signs in on TWO sessions, an admin calls the revoke route,
and both sessions are signed out on their next request:

```
login member session A:  200 signed in
login member session B:  200 signed in
admin login:             200 admin in
A GET /mine (before):    200 hello
B GET /mine (before):    200 hello
admin revoke M10045:     200 revoked
A GET /mine (after):     401 authentication_required: Please sign in. ...
B GET /mine (after):     401 authentication_required: Please sign in. ...
```

The audit records ONE ending per session, each `session_invalidated` with reason `ended_for_person`, both under
the admin's single revoke request (read back from `security_audit_log`):

```
{"event": "session_invalidated", "reason": "ended_for_person", "for_person": "M10045", "person": "admin-boss", "outcome": "committed", "request_id": "0bfe5f7dd02e", ...}
{"event": "session_invalidated", "reason": "ended_for_person", "for_person": "M10045", "person": "admin-boss", "outcome": "committed", "request_id": "0bfe5f7dd02e", ...}
```

It is not a logout of the caller and never counts as an authentication boundary; it is an act on someone else's
sessions, so it is its own statement. Each session it ends is invalidated through the same primitive that expiry
and rotation use, and recorded in the audit trail (`session_invalidated`, reason `ended_for_person`). The same
shared code reaches every session store; it is verified on SQLite and the in-memory store, and the Postgres leg
is pending an engine run. If the person holds no sessions, nothing is ended; an empty `for person` refuses at run,
naming the fix, because there is no person to act on; and it needs a session store, so it runs from a handler
under `mio serve`.

**The person is server-decided.** Pass a literal (`for person "alice@example.com"`), a value the server looked up
(a field off a record you retrieved, `for person target.email`), or a validated shape field an authorized admin
sent (`for person Revoke.who`, with `require role` on the handler, as above). The raw request accessor
`for person request.who` is refused, the same guard that stops `grant role ... for person` from trusting the
request: who to revoke is decided by your code, never read straight off an unauthenticated request. Under a
sector, a handler that ends someone's sessions with nothing proving who sent it is refused at check, naming
`require role` as the fix.

**Disabling an account: call it mid-handler, then carry on.** "Account disabled" is application-defined, so the
compiler cannot do it for you, and cannot flag its absence at check -- you decide what disabling means and call
the revocation yourself, as one step in the handler that does the rest:

```mohio
shape Disable
    user_id as int
shape: done
listen for sh.Disable at /admin/disable-account
    require role "admin"
    retrieve target from db.users match id to Disable.user_id
        when target is empty
            give back [404] "no such user"
    retrieve: done
    update db.users
        status "disabled"
        match id to Disable.user_id
    update: done
    end every session for person target.email
    give back [200] "account disabled and signed out everywhere"
listen: done
```

The handler sets the account's own `disabled` state, ends every session the person holds so they are logged out
on their next request, and continues to its response. The revocation is one statement in the middle of the
handler, not a special terminal step.

**Two honest boundaries.** The cross-store behaviour is verified on SQLite and the in-memory store; the Postgres
leg shares the same code but is pending an engine run. And the caller's OWN session is left untouched by design
(this is an act on someone else's sessions, never a logout of the admin running it), though that specific case
is reasoned from the design and was not separately run with a logged-in admin revoking a third party.

---

**Next in Part VIII:** Chapter 31, Security and What Runs Automatically (`require role`).
