<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Files, folders, and what reaches the browser

How Mohio decides which files are part of your app, which are private, and which are
never handed out. Written for the manual; every rule here is enforced by the compiler
and covered by a test.

---

## Naming: what is public, what is private

Two markers, and they mean different things. This is the tree structure to learn first,
because it decides what strangers can download from your site.

| You write | What happens |
|---|---|
| `about.mho` | A page. Served at `/about`. |
| `index.mho` | The front page. Served at `/`. |
| `journey.mho` | The spine. Applied to every page in its folder. Never a page itself. |
| `_cheats.mho` | Private. Pulled into other files with `include`. Never has a web address. |
| `_seed.json` | Private. Never handed out, whatever it holds. |
| `.private/` | Private folder. Nothing inside it is ever handed out. |
| `_next/` | **Not** private. A folder starting with an underscore is served normally. |

**To hide a folder, start its name with a dot.** To hide a single file, an underscore is
enough. The underscore is deliberately not a folder rule: `_next/` is where a built
front end keeps its assets, and denying that whole tree by name would break the site
with no message explaining why.

So: name the file, or use a dot folder. Do not rely on `_data/` to keep something out
of reach, because it will not.

```
myapp/
    index.mho              ->  /
    about.mho              ->  /about
    journey.mho                the spine, applied to both pages
    _cheats.mho                included by index.mho, no web address
    _seed.json                 private, never served
    .private/                  private folder
        answers.json           never served
    assets/
        style.css          ->  /assets/style.css
```

### The one trap

The spine must be named `journey.mho` exactly. Naming it `_journey.mho` used to make it
silently stop applying, taking your sector and compliance rules with it and printing
nothing. That now stops the build and tells you to rename it.

---

## What is never handed out

Some files are refused no matter where they sit, because handing them over would give
away the app itself or the data inside it.

- **Your source and its build artifacts.** `.mho`, and also `.cache`, `.pkl`,
  `.pickle`. A parse cache holds every value written in the file, keys included, so it
  is source in a different shape.
- **Configuration.** `.env`, `.ini`, `.cfg`, `.conf`, `.toml`, `.yaml`, `.yml`.
- **Databases, including their side files.** `.db`, `.sqlite`, `.sql`, and the sidecars
  `.db-wal`, `.db-shm`, `.db-journal`, `.sqlite-journal`. SQLite writes recent changes
  to those sidecars, so refusing the database and serving its journal would leak the
  same data by another name.
- **Keys and certificates.** `.pem`, `.key`, `.crt`, `.p12`, `.pfx`, `.p8`, `.der`,
  `.jks`, `.ppk`, and files named `id_rsa`, `authorized_keys`, `known_hosts` even
  though they have no extension at all.
- **Leftovers.** `.bak`, `.old`, `.tmp`, `.orig`, `.swp`, `.log`, `.dump`, and anything
  ending in `~`, which is how editors name a backup of the file they are editing.

**Every part of the name is checked, not just the end.** `backup.sql.gz` is refused
because of the `.sql` in the middle, so compressing a database dump does not get it
past. Same for `app.log.1`.

Ordinary things are unaffected: images, stylesheets, scripts, `manifest.json`,
markdown, CSV.

---

## Handing a file to someone: `give`

`give back` answers what was asked for. `give` hands something over as a file, so the
browser saves it instead of displaying it.

```mohio
give "reports/q3.pdf" as download
```

A path written in place names the file from the end of the path, so this saves as
`q3.pdf`. The file is read from your app folder or your file area, never from anywhere
else on the machine.

When the value is not a path written in place, name the file yourself:

```mohio
give doc.contents as download "invoice.pdf"
```

The filename is an ordinary string, so you can build it:

```mohio
give doc.contents as download "invoice-{{ customer.lastname }}.pdf"
```

That form is also how you rename a file on the way out.

**Why the filename is required there.** A path carries a name; a database field or a
generated document does not. The compiler cannot know at check time what a variable
will hold when the page runs, so it asks for the name up front rather than failing on a
real request.

`give` on its own is refused. It always needs `as download`, or you meant `give back`.

---

## Uploads: `accept`

Every upload field must say what it accepts. There is no default.

```mohio
shape Application
    resume as file accept pdf, docx max size 5mb
shape: done
```

You can name a group instead of listing types, and combine groups with individual types:

```mohio
accept images
accept images, documents
accept images, pdf, csv
```

The nine groups:

| Group | Types |
|---|---|
| `images` | jpg, jpeg, png, gif, webp, bmp, tiff |
| `documents` | pdf, docx, txt, rtf, odt |
| `spreadsheets` | xlsx, csv, tsv, ods |
| `presentations` | pptx, odp |
| `archives` | zip, tar, gz, 7z |
| `audio` | mp3, wav, ogg, m4a, flac |
| `video` | mp4, webm, mov, avi, mkv |
| `media` | images + audio + video |
| `office` | documents + spreadsheets + presentations |

`accept all` is refused on purpose. So is a near miss like `accept image`, because it
would be read as a file extension and refuse every real image, which reads correct and
behaves backwards.

Some types are never accepted even if you name them: executables and installers, macro
enabled Office files (`.docm`, `.xlsm`, `.pptm` and friends, while the plain `.docx`,
`.xlsx`, `.pptx` are fine), server side scripts, `svg` because it can carry script, and
the legacy `doc`, `xls`, `ppt` which can carry macros.

Uploaded web pages (`html`, `htm`, `xhtml`) are allowed and are **cleaned before they
are stored**. Script, event handlers, frames and anything else that could run against a
later viewer is stripped, and only the cleaned copy is kept. Headings, links, images
and tables survive.

---

## Databases: say which one you mean

```mohio
connect db as postgres from env.DATABASE_URL
```

If you name a real database and its address is not set, the app **refuses to start**.
It used to fall back to a throwaway database that emptied itself on every restart,
while every page kept answering normally. That is the reason people build something,
believe their data is saved, and find it gone.

To use the simple local database, say so:

```mohio
connect db as sqlite from env.DATABASE_URL
```

You will get a note reminding you that SQLite in memory keeps nothing after the app
stops, and a SQLite file lives or dies with the disk it sits on.

A misspelled name (`as postgress`) is refused rather than quietly becoming SQLite.

---

## AI: turning it on and checking it works

AI can be enabled with the `--ai` flag or by setting `MOHIO_AI=1`, which is easier when
one host runs many apps.

A **missing** key stops the app at startup. A **wrong** key is more dangerous: the app
starts, every page answers, and every AI decision quietly falls back, so the app looks
healthy while giving no real AI answers.

```
mio ai-check
```

makes one real decision against the provider and tells you which of the three you have.
It exits `0` when AI works, `1` when it is reachable but degraded, and `2` when it is
not configured, so a deploy can stop on anything but `0`.

---

## Fast starts: `mio warmup`

A large app takes a long time to read the first time. `mio warmup` does that reading
ahead of time and saves the result next to the source.

```
mio warmup .                 # a whole folder
mio warmup app.mho           # one file
```

This is a build step, not something to run in production. `mio serve` and `mio check`
read the saved copies but never write them.

- The saved copy is matched to the **contents** of the file, not the date on it, so it
  keeps working after a host copies your files over fresh.
- Editing a file makes its saved copy invalid automatically. You cannot serve a stale
  one by accident.
- Upgrading the compiler invalidates every saved copy, for the same reason.
- Include targets are covered too. They were skipped once, which produced a start that
  looked warm and re-read the include on every boot.
- Only a file that passes `mio check` cleanly can be saved this way. If warmup says it
  could not save one, believe it and run `mio check` on that file.

Ship the `.cache` files with your app. They are refused as downloads, so they cannot be
read by anyone visiting the site.

---

## Checking a folder

```
mio check .
```

reports the pages it checked, and how many include targets it parsed. Include targets
are parsed for spelling and structure but not checked for meaning on their own, because
a file meant to be pulled into another leans on things that file provides.

---

## Advanced: calling a Mohio app from a front end on another domain

A browser refuses a cross-domain request unless the server says that origin is allowed.
Mohio says nothing by default, which is the safe position on a host running many apps.

```
MOHIO_CORS_ORIGINS=https://app.example.com
MOHIO_CORS_ORIGINS=https://a.example.com,https://b.example.com
MOHIO_CORS_ORIGINS=*
```

Comma separated, no spaces needed, no trailing slash. With several listed, a caller from
one of them gets back exactly its own address, not the whole list.

**`*` and logins cannot be used together.** A browser refuses to send cookies to a server
that allows any origin, so a session-based app with `*` will appear to work and then fail
the moment a request depends on being logged in. If your app has logins, name the origins
explicitly. `*` is for an API that does not use sessions at all.

Reading the headers by hand is misleading here: a request from an address that is NOT on
the list still gets a header naming the allowed address. The browser refuses it because it
does not match, so nothing is exposed, but do not read that header as proof that any
origin is accepted.

**Being allowed to call is not the same as being allowed in.** CORS only decides which
web pages a browser will let talk to you. It proves nothing about who the caller is. A
front end hosted elsewhere still needs something that proves it is permitted, and that is
a separate question from this setting.

