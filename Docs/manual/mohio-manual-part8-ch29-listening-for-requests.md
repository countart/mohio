<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Part VIII -- The Web Layer

## Listening for Requests, and Responding

*Spans this Part's Chapter 29 (Listening for Requests) and Chapter 30 (Responding) in one pass.
Every example below is reused, verbatim, from already-verified content -- `Docs/mohio-first-app-
tutorial.md`'s search-box stage, hit with real `curl` requests against a real served route -- not
re-derived. Reuse per the standing instruction to build on verified work rather than repeat it.*

---

### `listen for` -- opens the server; each route inside it is one handler

```mohio
connect db as sqlite from env.DATABASE_URL

shape Feedback
    name as text required
    email as text required format "email"
    message as text required min 5 max 500
shape: done

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

`request for sh.X at <path>` answers a GET; `new sh.X at <path>` (seen in earlier chapters)
answers a POST. Both bind the request to the named shape, so every field rule from Chapter 16
already applies before your own code runs.

### The day-two trap: only the LAST `show`/`give back` becomes the response

Inside a served route, calling `show` several times does not build up a response the way it does
in a bare `mio run` script -- only the final `show` or `give back` in the handler is what the
caller actually receives. This is why the block above builds one text value (`results`, restated
each pass through the loop to grow it) and gives it back once at the end, instead of calling
`show` per row. A handler that calls `show` per item and expects them all to reach the caller will
see only the last one.

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

---

**Next in Part VIII:** Chapter 31, Security and What Runs Automatically (`require role`).
