<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Part VI -- Services (mio*)

## Chapter 26 (continued): Durable Uploads and Signed Links

*Every example below was run: `mio check` for the checks, `mio run` for the signed URLs, with the
command and its result shown. Nothing here is derived from the design.*

**How access to an uploaded file actually works, since it is easy to assume it is simpler than it is.** There is
NO public-or-private "zone" on a `miofile` area: a `miofile` block declares a `local`, a `temp` or a `cloud`
area and nothing else (verified by running -- `public` and `private` both fail to parse in a `miofile` block;
`public:` and `private:` are keywords for PAGE paths in a journey, not for files). Access is not a property
stamped on the file; it is the HANDLER GATE in front of the read:

- An upload is NOT in the served directory, so there is no URL that serves it directly from your app.
- A read goes through `miofile.read` INSIDE a handler, and that handler's `require role` (or `authorize:`)
  refuses FIRST, with an audited denial. Measured: 403 unauthenticated, 403 wrong role, 200 authorized.
- A file tagged `[phi]`, `[pci]` or `[pii]`, or carrying the `encrypted` classification, is encrypted at rest: an
  uploaded file is sealed by the same classes that seal a written value.

THE ONE LIMIT, stated so nobody over-claims it: for a CLOUD area, Mohio sets no ACL on the object, so whether
that object is fetchable by its raw storage URL is the BUCKET'S own policy and the provider's configuration, NOT
a Mohio guarantee. So the distinction to carry through this chapter is: THE HANDLER GATE IS OURS TO CLAIM, AND
THE STORAGE URL IS NOT. Mohio guarantees the gate in front of `miofile.read`; it does not guarantee that a
cloud object cannot be reached around that gate by its storage URL, because that is the bucket's setting.

The `accept` rule on an upload field, and handing a file back through a handler, are covered in
`Docs/guides/FILES-AND-SERVING.md` (checked this pass: it does NOT carry the public/private-zone wording this
chapter's intro used to, so it needs no correction on that point). This chapter covers the one thing that guide
does not: getting an uploaded file to a durable home, and letting a browser upload straight to that home with a
signed link.

---

### Where a plain upload goes, and why that is a warning

An `accept` field (see `FILES-AND-SERVING.md`) stores the uploaded file on the server's local disk
and keeps only its path in the database. Mohio warns you about this at check time, because the row
and the file then live in different places with different lifetimes:

```mohio
shape Application
    resume as file accept pdf max size 5mb
shape: done

listen for sh.Application at /apply
    give back [201] "received"
listen: done
```

```
$ mio check apply.mho
  ! `resume` accepts an upload, and uploaded files are written to local disk (MOHIO_UPLOAD_DIR,
    default ~/.mohio/uploads, outside the project folder) while the database stores only the path.
    The row survives a restart and the FILE does not, unless that directory is durable and outside
    the tree a deploy replaces.
```

The default upload directory is `~/.mohio/uploads` (under the user's home, verified by running), NOT a
project-local `./uploads`, so it is deliberately outside the folder a deploy replaces. `MOHIO_UPLOAD_DIR`
overrides it (an explicit value is resolved to an absolute path and honoured even when relative).

It is a warning, not an error: local uploads are fine while you build. For production the file needs
a durable home, and the most common one is a cloud bucket that a deploy never touches.

The warning fires at `mio check` whatever your deploy looks like, because a static check cannot see
the environment a deploy will run in (verified: the warning is identical with and without the store
variables set). What actually happens to a plain upload at RUNTIME is the next section.

---

### The platform's default durable store

On a deployment that configures an object store, a plain upload with no declared area is durable with
nothing for you to declare. When the environment provides `MOHIO_STORE_ENDPOINT`, `MOHIO_STORE_BUCKET`,
`MOHIO_STORE_REGION`, `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`, that bucket is the store, and an
upload field with no `miofile` area of its own goes straight to it.

```bash
MOHIO_STORE_ENDPOINT=https://s3.example.com
MOHIO_STORE_BUCKET=mohio-uploads
MOHIO_STORE_REGION=us-east-1
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
```

What a plain upload (no declared area) does at runtime, each case verified by running:

- **The store is configured:** the file goes to that bucket, nothing is written to local disk, and a
  new server reads it back after a restart. This is `getmohio.com`: durable uploads with nothing to
  declare.
- **A laptop, no store configured:** the file goes to local disk as before, and the check-time warning
  above is the one that matters.
- **A host whose disk is temporary** (`MOHIO_LOCAL_DISK_EPHEMERAL=1`) **with no store:** the upload is
  refused loud, naming the fix, and nothing is written, because a file written to a disk that resets
  would look saved and be lost at the next restart.
- **A half-configured store** (some of the five variables missing) is a loud error that names each
  missing one, never a quiet fall back to disk.

Two boundaries to hold on to. A DECLARED `miofile` area still wins: if a field or a program names its
own area, that area is where its files go, and the default store does not override it. And the default
store catches an UPLOAD with no area of its own; a `miofile` verb that names a `local` area is not
routed to the default bucket, because it carries its own area and goes where that area says. So "durable
by default" is about the undeclared upload on the platform, not a redirect of everything to the cloud.

An uploaded object whose database row never lands is removed, and the removal is audited (`FILE_CLEANUP`),
so a failed write or a rolled-back transaction leaves no orphaned file. That guarantee is covered in the
transactions chapter (`mohio-manual-part9-ch38-transactions-and-audit.md`).

### Public and private, by folder

The default store has two buckets, and the FOLDER in the path chooses between them (verified by running).
A `public/` path goes to the public bucket; everything else goes to the private bucket:

- A path under `public/` is served by a plain, unsigned, permanent address: `sign upload url for
  "public/logo.png"` signs a `PUT` into the public bucket, and `sign url for "public/logo.png"` is a
  permanent unsigned address that fetches it. The deployment sets `MOHIO_STORE_PUBLIC_BUCKET` for this
  (public-read), beside the private `MOHIO_STORE_BUCKET`.
- Any other folder goes to the private bucket. A private object is read only through a signed, expiring
  link (`sign url for`), never a plain address.
- A folder whose name starts with `_` goes to the private bucket too and is given NO address at all: it is
  never served by any URL, and is reachable only by the app's own code.
- A `public/` path with no `MOHIO_STORE_PUBLIC_BUCKET` set REFUSES, naming the variable, and sends nothing
  to the private bucket in its place. With no store configured at all, signing still refuses as before
  (a local disk cannot sign).

This is the file half of the folder-based access model being built across the framework (tracked as
`T1-FILE-CLASSIFICATION`): `public/` is public, everything else is private, and `_` is strictest. The
platform provides and locks the two buckets; Mohio decides every access.

### Reading an uploaded file: read, hash, save, reveal

An upload field reads as a FILE. Inside a handler, `miofile.read` opens the uploaded file by its stored name;
you can hash it, save its name and digest, and later reveal it to an authorized reader by that stored name.
Verified end to end through a real multipart upload round-trip:

- An upload FIELD must declare what it accepts and a size cap, or check refuses, naming each: for example
  `photo as image accept png max size 1mb`. A field with no `accept` or no `max size` fails at check.
- `miofile.read Upload.photo as photo_bytes` reads the uploaded bytes; `hash photo_bytes as digest using sha256`
  is its SHA-256; the handler saves `photo Upload.photo` (the stored name) and `digest`.
- `Upload.photo` is that same stored name, so reading it again reads and hashes the same file; a value that is
  not a file name refuses, naming what it is; and no response or log line carries the file's bytes.
- The reveal is GOVERNED: a reader without the role is refused; signed in with it, `miofile.read r.photo as img`
  then `give img as image "receipt.png"` returns the saved file, and the saved digest is the SHA-256 of what was
  uploaded.

### The folder decides visibility, never the filename

Visibility is read off the PATH the program wrote, never off the uploaded file's own name. A caller cannot make a
file public or private by naming it a certain way; only the folder it is written to decides (`public/` public, a
`_`-prefixed folder never served, everything else private, as above). So the filename a person uploads never
changes where the file goes or who can reach it.

And a standing caution, this one ADVICE rather than an enforced check: never put a customer identifier in a path.
A path is chosen by the program and can appear in a signed URL or a bucket listing, so a customer id in it leaks
who the file belongs to. There is no compiler guard against this today, so keeping identifiers out of the path,
and in the row instead, is on you.

### `copy`, and why the file area is not served

`copy` moves a file through the `miofile` file area, and it cannot carry a file around the area's zone
rules. Both ends are governed: neither path may leave the area, and the destination's zone refuses an
executable, a type its `accept` does not list, or a file over its `max size`, judged on the name the file
ends with after a `rename`. An existing file is replaced only with `allow overwrite`.

The file area itself is NOT served to the web. A file written with `miofile.write "secret.txt"` is not
reachable at a URL like `/mio_files/secret.txt`; static serving never serves the file area, so a
restricted file cannot be read around the handler gate just by guessing its path. Access to a stored file
is always the handler gate in front of `miofile.read`, as described at the top of this chapter.

---

### A durable home: a cloud vault

Declare where files live with a `miofile` block. A `cloud vault` names the bucket, its region and
endpoint, and what it accepts, exactly as an `accept` field does. Credentials come from the
environment, never the source.

```mohio
miofile
    cloud vault
        bucket "mohio-uploads"
        region "us-east-1"
        endpoint "https://s3.example.com"
        accept pdf
        max size 9mb
miofile: done
```

---

### Uploading straight to the vault: `sign upload url for`

Routing a large file through your app twice -- browser to app, app to bucket -- is slow and doubles
your bandwidth. Instead, hand the browser a **signed upload URL**: a one-time, time-limited address
that lets it `PUT` the file directly into the bucket, with nothing else it can do.

```mohio
hold target "receipts/invoice-001.pdf"

sign upload url for target as link
    expires in 15 minutes
sign: done

show link
```

`as link` names the generated URL `link`, and `expires in 15 minutes` sets how long it
is valid. `as` is the capture word for the whole family (the old `named link` line is retired and refuses,
naming this form). Run it with credentials set (a live bucket is not needed to generate the URL):

```
$ mio run upload.mho
  https://s3.example.com/mohio-uploads/receipts/invoice-001.pdf?X-Amz-Algorithm=AWS4-HMAC-SHA256
  &X-Amz-Credential=...%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=...&X-Amz-Expires=900
  &X-Amz-SignedHeaders=host&X-Amz-Signature=...
```

`X-Amz-Expires=900` is the fifteen minutes, in seconds. Hand `link` to your front end; the browser
uploads to it and the file lands in the bucket without passing through your app.

### Reading it back: `sign url for`

The same construct, without `upload`, signs a **read**. `sign upload url for` signs permission to
WRITE (a `PUT`); `sign url for` signs permission to READ (a `GET`). They are deliberately different
verbs for different permissions, so a link that can only upload cannot also be used to download, and
the reverse.

```mohio
hold target "receipts/invoice-001.pdf"

sign url for target as link
    expires in 15 minutes
sign: done

show link
```

Use `sign url for` to hand someone a TIME-LIMITED signed link that reads one object from the cloud vault: it
expires, so it is not a permanent address, and it never exposes your credentials. What it is NOT is a "link to a
private file," because there is no private-file zone (see the access model at the top of this chapter): the
signed URL is a capability to read one object for a short window, issued against the cloud store. The access
guarantee you control is the HANDLER GATE in front of `miofile.read`; the signed URL is a separate,
time-limited grant you choose to hand out, and once handed out anyone who has it can read that object until it
expires. Use it when you deliberately want to give a short-lived read to a specific object, not as a substitute
for the handler gate.

---

### Handing a file back to display: `give as image` (beside `as download`)

The files guide (`Docs/guides/FILES-AND-SERVING.md`) covers `give <value> as download`, which hands a file over
as an ATTACHMENT the browser saves. Its sibling is **`give <value> as image`**, which DISPLAYS the file inline
with its image content-type. Showing a delivery photo is the normal case; saving it is the exception.

```mohio
give "proof.png" as image
```

Both run INSIDE the handler, AFTER `require role` and any record checks, and both record the SAME egress (the
audit distinguishes 'display' from 'download'), because displaying a protected file crosses the boundary exactly
as saving it does. And `give back [200] <raw bytes>` on its own is refused, naming `as image` / `as download`, so
raw bytes can never fall through to the JSON path and come back as a corrupt body.

**An SVG is deliberately served with `as download`, never inline.** `as image` refuses an SVG at run: an SVG can
carry script, so displaying one inline is stored cross-site scripting. So `as image` is for raster images (png,
jpg and the like); an SVG goes out through `as download`, where the browser saves it rather than executing it.
That is the same reason SVG is refused as an upload extension elsewhere in Mohio.

---

### The vault is required

A signed URL only means something against real cloud storage, so asking for one without a declared
vault fails loud rather than handing back something that cannot work:

```
$ mio run no-vault.mho
  Response  500  sign_needs_cloud_storage: A signed URL can only be issued for cloud storage, and
    this program declares none, so there is nothing to sign against. Declare a cloud area for the
    program's files, and set the store's endpoint, bucket, region and key pair in the environment.
    A local area cannot issue one: the signature is what makes a direct upload safe, and a directory
    has no signature to give.
```

A local directory has no signature to give, which is the whole point: the signature is what makes a
direct upload safe. So durable, signed uploads and the cloud vault go together -- one is the home,
the other is the safe door to it.

---

### Common mistakes

- **Shipping local uploads to production.** The check-time warning is telling you the file will not
  survive a deploy. Move it to a cloud vault.
- **Confusing the two sign forms.** `sign upload url for` signs a `PUT` (upload); `sign url for` signs
  a `GET` (download). Using the read form where you meant to let someone upload hands back a link the
  bucket will reject.
- **Asking for a signed URL with no cloud vault declared.** A `500` (`sign_needs_cloud_storage`): a
  local directory cannot sign. Declare a `cloud vault` and set its credentials in the environment.
- **Putting credentials in the source.** They come from the environment; the vault block names only
  the bucket, region and endpoint.
