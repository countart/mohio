<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Part V -- Web and delivery

## Styling your app

One stylesheet, shared by every page. You write ordinary CSS in a file under `public/`, and each page links it
from its `render`. There is no `style` block in Mohio (the word is an ordinary variable), so styling is the
plain web thing: a stylesheet and a `<link>`. Every line below was run.

### The shape of it

```
myapp/
  public/
    style.css        // your stylesheet, one file, shared by every page
  index.mho
  about.mho
```

`public/style.css` is ordinary CSS:

```css
body { background: #111; color: #eee; font-family: sans-serif }
h1 { color: #6cf }
```

Each page links it in its `render`:

```mohio
shape Home
    ok as text
shape: done
request for sh.Home at /
    render
        <html>
            <head><link rel="stylesheet" href="/public/style.css"></head>
            <body><h1>Home</h1></body>
        </html>
    render: done
request: done
```

```mohio
shape About
    ok as text
shape: done
request for sh.About at /about
    render
        <html>
            <head><link rel="stylesheet" href="/public/style.css"></head>
            <body><h1>About</h1></body>
        </html>
    render: done
request: done
```

### Running it

Serve the folder:

```bash
mio serve myapp
```

Verified by running: `GET /` returns the page (200), and `GET /public/style.css` returns the stylesheet with
`Content-Type: text/css` (200). The file under `public/` is served at the path `/public/<name>`, which is the
address you link. Both pages share the one `public/style.css`, so a change to it restyles the whole app.

### Two things to know

- **The link lives in each page's `render`.** There is no page-CSS `style` section and no journey-level asset
  block that injects a stylesheet across pages: an `assets` block checks clean but refuses at serve, so it
  injects nothing (tracked as `T1-SHARED-PAGE-ASSETS`). Until that lands, the shared stylesheet is one file and
  each page carries the one `<link>` line.
- **`public/` is the web-reachable folder.** A stylesheet, a logo, a client script you want the browser to fetch
  go under `public/` and are served by their path. A file a page should NOT expose does not go there (see the
  files guide, `Docs/guides/FILES-AND-SERVING.md`).
