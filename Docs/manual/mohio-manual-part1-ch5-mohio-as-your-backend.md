<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Part I -- First Programs

## Chapter 5: Mohio as Your Backend

*Rung 4: everything so far served pages. This chapter is Mohio behind a separate frontend --
`framework: api`, endpoint calls only, no page routing. Verified end to end before a word of
this chapter was written: checked, served, hit with real requests, the responses read back.*

---

### Declaring it

```mohio
framework: api

shape Ping
    name as text required
shape: done

listen for
    new sh.Ping at /ping
        give back [200] "pong"
    new: done
listen: done
```

`framework: api` goes once, at the top of the journey spine (or the file, for a single-file
app) -- it is application-level context, decided before the first request, the same way
`sector:` is, but for structure instead of rules. Leave it out and the app is `web`, the
default. `api` and `web` are orthogonal to `sector:`, which still governs compliance.

### Verified: it answers JSON, not HTML

```bash
mio check ping.mho     # clean, no errors
mio serve ping.mho --port 8080
```

```bash
curl -X POST http://localhost:8080/ping -H "Content-Type: application/json" -d '{"name":"a"}'
```
```
HTTP/1.1 200 OK
content-type: application/json

{"message": "pong"}
```

Shape validation answers in the same JSON shape, not a rendered error page:

```bash
curl -X POST http://localhost:8080/ping -H "Content-Type: application/json" -d '{}'
```
```
HTTP/1.1 422 Unprocessable Entity
content-type: application/json

{"errors": {"name": "Name is required."}}
```

### The one thing worth knowing: send `Content-Type: application/json`

A POST sent without a `Content-Type` header is treated as an ordinary form submission, and
Mohio's CSRF protection correctly refuses it -- verified live:

```bash
curl -X POST http://localhost:8080/ping -d '{"name":"a"}'
# 403  Invalid or expired form token. Please reload and try again.
```

This is not a framework:api bug -- it is the same protection a `web` app gets, and it is what
you want: a browser can be tricked into firing a plain form POST at your endpoint from another
site, and that is exactly the request CSRF protection exists to block. A real API client
(another service, a fetch call, a mobile app) sends `Content-Type: application/json` as a
matter of course, and that header is what tells Mohio this is a programmatic caller rather than
a browser form -- verified, the identical request with that header set returns 200. Set it on
every request your client makes.

### Verified: no page routing

`framework: web` will render a `.mho` file placed at its convention URL with no route
declared at all. `framework: api` deliberately does not -- an api app answers only the routes
declared inside `listen for`, confirmed by reading the serve layer's own dispatch (it returns
the 404 immediately for any framework outside `web`/`game`, before ever trying to render a
file). A bare `GET /` on an api app with no declared route for it still shows Mohio's generic
"your app is running" placeholder -- that page is a platform-wide catch-all shown for any app
with nothing mapped to `/`, `web` or `api` alike, not evidence of page routing under `api`.

### What this chapter leaves out

This is the declaration and the shape of the response, not a full JSON API -- error-shape
conventions, pagination, and a route inventory belong with `mioql-user-guide.md`'s write/read
verbs once you're building past a single endpoint; those verbs work identically under
`framework: api` and `framework: web`, since only routing and response shape changed.

---

**A note on route paths:** a multi-segment path works. `/feedback/list` (already in chapter 2),
`/users/profile`, and three further variants all parse, transform, and serve correctly,
including a real HTTP round trip, so the routes in this chapter are written the natural way
rather than flattened to a single segment.

**End of Part I.** Part II picks up MioQL in depth, sectors and compliance, and `ai.agent`.
