<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Part IX -- Operational Guarantees

## Chapter 42: Testing with two sessions, and how `session.person` works

Many things a regulated app must get right only show up with TWO people signed in at once: maker-checker (one
person cannot approve their own work), per-record access (one driver cannot see another's delivery), and session
isolation (signing out of one session does not sign out the other). You cannot see any of these with a single
browser tab, because one browser is one session.

**The recipe: two independent sessions on one machine.** A session is tied to the browser's cookies, so two
cookie jars are two sessions. Any of these gives you a second one:

- a NORMAL browser window plus an INCOGNITO / private window (the private window has its own cookie jar, so it is
  a separate session; closing it ends that session);
- a SECOND BROWSER PROFILE (for example a second Chrome profile), which also keeps its own cookies;
- a PHONE on the same network, pointed at your machine's address.

Sign in as a DIFFERENT person in each. Now you have two live sessions to test against: approve in one what the
other created, read in one a record the other owns, and sign out of one and confirm the other still works.

**How `session.person` works, and why two sessions matter.** At the authentication boundary, `grant role for
person "alice"` sets `session.person` to `alice` for THAT session. It is read-only and set once per session (a
second `grant ... for person` refuses), it survives session rotation, and it is stamped automatically onto every
audit record (see Chapter 29). Each session has its OWN `session.person`: the normal window is `alice`, the
incognito window is `bob`. That is exactly what lets you test maker-checker for real:

```mohio
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

Signed in as `alice` in the normal window, alice cannot approve a record whose requester is `alice`; signed in as
`bob` in the incognito window, bob can. One tab could never show you both halves. An absent `session.person`
(nobody signed in) refuses a comparison in both directions, so a separation-of-duties check cannot quietly pass
with an empty session, which is itself worth testing in a third, signed-out window.
