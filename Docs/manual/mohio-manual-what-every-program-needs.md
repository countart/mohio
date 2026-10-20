<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# What every program needs

Read this first. Almost every Mohio example you meet assumes four things are in place, and leaves them out to
stay short. This page puts them all in one runnable program, so that when a snippet elsewhere shows only a
`save` or a `find`, you know what has to be around it. The four, in the order they appear in a file:

1. a **connection** to a database,
2. a **shape** that describes the data,
3. a **handler** that answers a request,
4. a **role**, granted at login and required on the routes that need it.

Here they are together. This program checks clean and runs; every piece below points back to it.

```mohio
connect db from env.DATABASE_URL

shape Note
    body as text required
shape: done

shape Login
    who as text
shape: done

listen for sh.Login at /login
    grant role "member"
    give back [200] "signed in"
listen: done

listen for sh.Note at /notes
    require role "member"
    save to db.notes
        body Note.body
    save: done
    give back [201] "saved"
listen: done

request for sh.Note at /notes/list
    require role "member"
    find rows in db.notes
    find: done
    give back [200] rows as json
request: done
```

## 1. A connection

```mohio
connect db from env.DATABASE_URL
```

Any data verb (`save`, `find`, `retrieve`, `update`, `remove`) works against a connection you opened. `db` is the
default connection's name; you open it once, at the top, and use it as `db.<table>` everywhere below.

**What happens if you forget it, and why it used to be confusing.** A data verb against a connection you never
opened is refused at `mio check`, at the line, naming the fix: "the default connection `db` is used here but never
opened ... Open it first, for example `connect db from env.DATABASE_URL`." This is worth calling out
because it did not always work this way: a missing connection used to slip through the check and fail only at run,
where an `on.failure` handler would catch it, so a forgotten line looked like the database being down rather than
a line you left out. Now the mistake is named before the program runs. Which databases are supported, and the
compliance floor on what a database must be able to guarantee, are in `Docs/guides/databases-supported.md`.

## 2. A shape

```mohio
shape Note
    body as text required
shape: done
```

A shape describes a piece of data: what fields it has, their types, and the rules on them (`required`, `unique`, a
`format`, a default). It is the contract an incoming request is held to, and the description a table is governed
by. A handler names a shape with `sh.<Name>`, and every field rule runs before your own code does. Leave the shape
out and the handler is refused: `sh.Note is used but no shape named Note is declared`. Full chapter:
`Docs/manual/mohio-manual-part5-ch16-shapes.md`.

## 3. A handler

```mohio
listen for sh.Note at /notes
    require role "member"
    save to db.notes
        body Note.body
    save: done
    give back [201] "saved"
listen: done
```

A handler answers one request at one path. `listen for sh.X at <path>` answers a POST (a write); `request for sh.X at
<path>` answers a GET (a read). A change to data belongs in a handler, not on a bare page, because a page runs on
every view and a handler runs only for the request it declares. The handlers chapter covers this, and the
guarantee that a handler making more than one write is all-or-nothing:
`Docs/manual/mohio-manual-part8-ch29-listening-for-requests.md`.

## 4. A role, granted at login and required where it matters

```mohio
listen for sh.Login at /login
    grant role "member"
    give back [200] "signed in"
listen: done
```

`grant role "member"` in a login handler puts a role on the caller's session. `require role "member"` at the top
of another handler then refuses anyone who does not hold it: an anonymous request to `/notes` or `/notes/list`
gets a `403` (verified by running), and a signed-in member gets through. Granting happens once, at login;
requiring happens on each route that needs protecting. This is the ordinary shape of a gated feature, and it is
also how a journey's `authorize:` gates static files (see `Docs/guides/FILES-AND-SERVING.md`).

## Why this page exists

Most examples show one of these four and assume the other three, which is fine once you know they are there and
confusing before you do. A snippet that just says `save to db.notes` is not wrong; it is standing on a
connection, a shape, a handler, and, if the data is protected, a role, all declared elsewhere in the file. When
an example refuses with "no such connection," "no shape named X," or a `403`, it is one of these four missing.
Keep this program open beside the reference, and the short snippets there read as what they are: the one new line,
on top of the four that every program needs.
