<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Your first real app: a feedback board

*Follows `start-here/QUICKSTART.md` and `Docs/guides/mohio-mental-model-no-classes.md`. This builds one
small, real, served app, step by step: a shape, a route, a validated form, a database write, a
list-back, and a search box. Every stage below is the complete program at that point, checked and
run against the current compiler, not a fragment.*

What you're building: a page where anyone can leave feedback (name, email, a message), see every
message posted so far, and search them by keyword. Contact-form scale, the shape a first real app
actually takes.

---

## Stage 1: the shape, and a page to show

Everything starts with the **shape** -- what a piece of feedback IS, described once:

```mohio
connect db as sqlite from env.DATABASE_URL

shape Feedback
    name as text required
    email as text required format "email"
    message as text required min 5 max 500
shape: done

listen for
    request for sh.Feedback at /
        render
            <h1>Feedback</h1>
            <p>Tell us what you think.</p>
            {{ form sh.Feedback }}
        render: done
    request: done
listen: done
```

Save this as `feedback.mho` and run it:

```bash
mio serve feedback.mho
```

Open `http://localhost:8080/`. You'll see a real HTML form, generated FROM the shape:
`{{ form sh.Feedback }}` reads the three fields and their rules and builds the inputs, the
`required` attributes, a CSRF token, and a hidden honeypot field, all from one line. You did not
write any HTML for the form itself, and you did not write any validation code yet -- the shape
already declared what a valid `Feedback` looks like:

- `required` -- the field must be present.
- `format "email"` on `email` -- must look like an email address.
- `min 5 max 500` on `message` -- length, in characters, both ends.

(Full list of field rules -- `min`/`max`/`pattern`/`accept`/more -- is
`Docs/guides/form-field-types.md`.)

---

## Stage 2: accepting the submission

A route that only shows a form isn't an app yet. Add a `new` handler for the same shape -- it
fires when the form (or any client) POSTs a `Feedback`:

```mohio
connect db as sqlite from env.DATABASE_URL

shape Feedback
    name as text required
    email as text required format "email"
    message as text required min 5 max 500
shape: done

listen for
    request for sh.Feedback at /
        render
            <h1>Feedback</h1>
            <p>Tell us what you think.</p>
            {{ form sh.Feedback }}
        render: done
    request: done

    new sh.Feedback
        save to db.feedback
            name feedback.name
            email feedback.email
            message feedback.message
        save: done
        give back [200] "Thanks for the feedback."
    new: done
listen: done
```

Check it, then run it and post to it for real:

```bash
mio check feedback.mho
mio serve feedback.mho
```

```bash
curl -X POST http://localhost:8080/ \
  -H "Content-Type: application/json" \
  -d '{"name":"Ada","email":"ada@example.com","message":"Love the search box!"}'
# {"message": "Thanks for the feedback."}

curl -X POST http://localhost:8080/ \
  -H "Content-Type: application/json" \
  -d '{"name":"Cy","email":"not-an-email","message":"hi"}'
# 422 {"errors": {"email": "Enter a valid email address.", "message": "Message must be at
#      least 5 characters."}}
```

**Nothing here checks the email format or the message length by hand.** The shape already
declared the rule; every route that receives a `Feedback` enforces it before your code ever
runs, automatically, on every field, every time -- that's the whole point of describing it once
on the shape instead of validating it inside each handler.

`new sh.Feedback` binds the incoming, now-validated data to a variable named after the shape,
lowercased: `feedback`. `feedback.name`, `feedback.email`, `feedback.message` read its fields.
`save to db.feedback` writes a row; no `create table` anywhere; the first `save` makes the table.

---

## Stage 3: listing it back

A route is just `request for` at a path. Add one that reads every row back:

```mohio
listen for
    request for sh.Feedback at /feedback/list
        find rows in db.feedback
        find: done
        listing ("{{ rows.count }} message(s):")
        repeat each row in rows
            listing (listing & "\n- " & row.name & ": " & row.message)
        repeat: done
        give back [200] listing
    request: done
listen: done
```

(Add this block inside the same `listen for ... listen: done`, alongside the other two.)

```bash
curl http://localhost:8080/feedback/list
# {"message": "2 message(s):\n- Ada: Love the search box!\n- Bo: The docs need work."}
```

`find rows in db.feedback` (no `where`) gets every row. `repeat each row in rows` walks the
result. **One thing worth knowing before you hit it:** inside a served route, only the LAST
`show`/`give back` becomes the response -- earlier `show` calls in the same handler are not
concatenated for you. That's why the loop above builds one text value (`listing`, restated each
pass to grow it) and gives that back once at the end, rather than calling `show` per row.

---

## Stage 4: the search box

A GET query parameter (`?q=...`) is read with `request.q` -- not through the shape, since a
search term isn't a field being submitted, it's part of the request itself:

```mohio
listen for
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
listen: done
```

```bash
curl "http://localhost:8080/feedback/search?q=search"
# {"message": "- Ada: Love the search box!\n"}

curl "http://localhost:8080/feedback/search?q=zzz"
# {"message": "No matches."}
```

`where message contains request.q` is a substring search over the `message` field --
`starts.with` and `ends.with` work the same way for prefix/suffix search (full list:
`Docs/guides/mioql-user-guide.md`). `check hits.count / when 0` is the ordinary way to branch on "did
anything come back" -- a genuine zero-result search is not a failure, it's a normal empty answer,
so it's handled with `check`, not `on.failure` (which means the database operation itself broke,
not "found nothing" -- see `Docs/guides/mioql-user-guide.md`'s `try`/`on.failure` section for the
distinction and when to reach for each).

---

## The complete app

All four stages, together, checked and run as one file:

```mohio
connect db as sqlite from env.DATABASE_URL

shape Feedback
    name as text required
    email as text required format "email"
    message as text required min 5 max 500
shape: done

listen for
    request for sh.Feedback at /
        render
            <h1>Feedback</h1>
            <p>Tell us what you think.</p>
            {{ form sh.Feedback }}
        render: done
    request: done

    new sh.Feedback
        save to db.feedback
            name feedback.name
            email feedback.email
            message feedback.message
        save: done
        give back [200] "Thanks for the feedback."
    new: done

    request for sh.Feedback at /feedback/list
        find rows in db.feedback
        find: done
        listing ("{{ rows.count }} message(s):")
        repeat each row in rows
            listing (listing & "\n- " & row.name & ": " & row.message)
        repeat: done
        give back [200] listing
    request: done

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
listen: done
```

Everything in this walkthrough was proven against the compiler, real requests included, not
copied from a working file elsewhere: `mio check` clean, `mio serve` running, every route hit
with a real HTTP request (`curl`), every response read back and matched against what's shown
above, both the valid and the invalid-input cases.

---

## Where to go from here

- **The mental model** (`shape` describes, `task` acts) -- `Docs/guides/mohio-mental-model-no-classes.md`.
- **Every query verb in depth** (`find`, `retrieve`, `save`, `update`, `remove`, `try`/
  `on.failure`) -- `Docs/guides/mioql-user-guide.md`.
- **Field validation rules in full** (`pattern`, `accept`, file uploads, custom error text) --
  `Docs/guides/form-field-types.md`.
- **Auth**, once you're ready to gate a route -- `Docs/guides/password-login-howto.md` and
  `cookbook/password-login.mho`.
- **The full language reference**, every keyword with a verified example --
  `start-here/LANGUAGE-REFERENCE.md`.
