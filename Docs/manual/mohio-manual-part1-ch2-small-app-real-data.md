<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Part I -- First Programs

## Chapter 2: A Small App With Real Data

*Rung 2 of the ladder. Chapter 1 was one file, one line, run once. This chapter is a real,
served app: a shape, a route, a database write, and data read back -- multi-part and
data-driven, the shape every small real app actually takes. Every sample is verified against the
current compiler; the full build, staged step by step with the reasoning behind each piece, is
`Docs/guides/mohio-first-app-tutorial.md` -- this chapter walks the same app, framed for the book.*

---

### What you're building

A feedback board: a page where anyone can leave a message (name, email, a message), see every
message posted so far, and search them by keyword. Small enough to read in one sitting, real
enough to show every piece a small app needs.

### The four new constructs this chapter introduces

- **`connect`** -- one line, once, names which database the rest of the file talks to.
- **`shape`** with validation -- the same declaration from chapter 1's mental-model bridge, now
  carrying real rules (`required`, `format "email"`, `min`/`max`) that the compiler enforces on
  every request before your own code ever runs.
- **`listen for`** -- opens the server. Each `request for` (a GET) or `new` (a POST) inside it
  is one route.
- **`save`** / **`find`** -- write a row, read rows back.

### The shape, and the page it renders

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

`{{ form sh.Feedback }}` builds a real HTML form FROM the shape -- the fields, the `required`
markers, a CSRF token -- from one line. Nothing here validates the input by hand; the shape
already declared what a valid `Feedback` looks like, and every route that receives one enforces
it automatically.

### Accepting it

```mohio
listen for
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

`new sh.Feedback` fires on a POST, and binds the now-validated submission to a variable named
after the shape, lowercased: `feedback`. `save to db.feedback` writes it -- no `create table`
anywhere; the first `save` makes the table.

### Reading it back

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

`find rows in db.feedback` (no `where`) gets every row; `repeat each row in rows` walks it.
Inside a served route, only the LAST `show`/`give back` becomes the response -- earlier `show`
calls in the same handler are not concatenated -- which is why the loop above builds one text
value and gives it back once, rather than showing each row as it goes.

### Proof, not a claim

Every route above was hit with a real HTTP request: a valid submission, an invalid
one (bad email, too-short message, both rejected with `422` naming each field), the list-back
read after two real submissions. The full transcript, every `curl` and its real response,
plus the search route this chapter leaves out for space: `Docs/guides/mohio-first-app-tutorial.md`.

---

**Where this chapter's constructs go deeper:**
- Every data verb (`save`, `find`, `retrieve`, `update`, `remove`) and `try`/`on.failure` for
  when something genuinely breaks -- `Docs/guides/mioql-user-guide.md`.
- Every field validation rule (`pattern`, `accept`, file uploads) -- `Docs/guides/form-field-types.md`.
- Gating a route by role, and validating a search term before it reaches the database --
  `Docs/guides/mohio-protected-search-tutorial.md`.

**Next in Part I** (not yet written): Chapter 3, Setting Up AI --
environment variables and provider keys. Chapter 4, An App That Reasons -- `ai.decide` for
real. Chapter 5, Mohio as Your Backend -- `framework: api`, no page routing.
