<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Part V -- Web and delivery

## Pages: the head, and social previews

A page can declare three things about itself at the top level: a `title`, a `describe`, and an `image`. They feed
the page's `<head>`, and from the same three values Mohio writes the social preview tags a link needs to render a
card when it is shared.

```mohio
title "Q3 results"
describe "Our latest numbers are out"
image "q3-preview.png"
render
    <h1>Q3 results</h1>
render: done
```

**What you write.** `title` and `describe` are the page's title and one-line description. `image` (new in 6.0.0)
names the preview image, a page-level declaration beside the other two.

**What happens.** `title` fills `<title>` and `describe` fills `<meta name="description">`, as before. From the
same three values, Mohio also writes the Open Graph and Twitter tags, so a link pasted into a chat or a social
post shows a preview card rather than a bare URL. Serving the page above writes (verified by running through
`mio run --request`):

```
<title>Q3 results</title>
<meta name="description" content="Our latest numbers are out">
<meta property="og:title" content="Q3 results">
<meta property="og:description" content="Our latest numbers are out">
<meta property="og:image" content="q3-preview.png">
<meta name="twitter:title" content="Q3 results">
<meta name="twitter:description" content="Our latest numbers are out">
<meta name="twitter:image" content="q3-preview.png">
<meta name="twitter:card" content="summary_large_image">
```

You declare the three values once; you do not hand-write a single meta tag.

**What can go wrong, and what it does instead.** The tags are written only from what you declare:

- A page that declares none of the three emits NO `og:` or `twitter:` tags (verified). Nothing is invented.
- A page that declares only `title` writes the title-derived tags and `twitter:card`, but no `og:image` or
  `og:description` (verified). One of the three is enough to get a preview card; the rest appear only when you
  declare them.

So the preview grows exactly as far as the values you give it, and a page that says nothing about itself stays a
plain link rather than a card with blank fields.

## Page text: any character, and showing a literal double-brace

Text inside a `render` block, and a fragment a `render` includes, is page content, so it may contain any
character and still check clean: accents, non-Latin scripts, a dash, an emoji (verified by running, inline and in
an included fragment). This is page text, not Mohio code, so the rule that non-ASCII in code fails loud does not
apply to it.

To SHOW a `{{ ... }}` pair in a page instead of evaluating it, for a Mohio example on a page, put a backslash
before it:

```mohio
shape Q
    q as text
shape: done
request for sh.Q at /help
    render
        <p>Write \{{ name }} to show a value.</p>
    render: done
request: done
```

`\{{ name }}` renders the literal text `{{ name }}`, the backslash consumed and nothing inside evaluated. Single
braces need no escape and stay literal on their own: CSS `{ }`, a lone `}}`, an unmatched `{{` all render as
written, because only a complete pair is a template.

## A `render` needs its own `render: done`

A `render` block closes with `render: done`, and the compiler refuses one that is missing it at check, rather than
letting its page text run on and swallow the lines after it. Before, a `render` with no closer took the next route,
and every route after it, as page text, so those routes silently vanished. Now it fails loud at the line that
opened the render. Verified by running `mio check` on a render with no closer of its own:

```
Line 2: this `render` has no `render: done` of its own, so its page text runs on past line 4 (`request: done`) and takes that line, and every route after it, as text: those routes would never exist.
  Close the render before line 4:
    render
        ...
    render: done
```

The fix is the closer:

```mohio
shape Page
    q as text
shape: done
request for sh.Page at /p
    render
        <h1>Hello</h1>
    render: done
request: done
```

## Page code gets the same request and session a handler gets

A page serves itself at its conventional address, and the code on it reads `request.*` and `session.*`
EXACTLY as a handler does, the same request and the same session. Verified by running a single-file page that
reads a GET query field and the session:

```mohio
give back [200] ("person [" & session.person & "] session [" & session.id & "] q [" & request.q & "]")
```

```
GET /?q=hello  ->  200
person [] session [8f48b360f77f4462a92e57130416f762] q [hello]
```

Anonymous, with no prior cookie: `session.person` is the no-person value (it renders empty), `session.id` is a
real minted session id, and `request.q` reads the GET query field. A page is not a lesser context than a handler;
it gets the request and session a handler gets.
