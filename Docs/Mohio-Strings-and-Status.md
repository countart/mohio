<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md. -->
# Strings and Status Codes in Mohio

How to write text — including multi-line text and prompts — and how to set HTTP
status on a response. Every example here is verified against the current compiler.

---

## Single-line strings — the default

A normal string is one physical line, in double quotes:

```
greeting   "Welcome back."
give back 200 "Saved."
```

### `\n` for a short inline break

For a line break inside an otherwise short string, use the `\n` escape — it
renders as a real newline in the output:

```
give back 200 "Line one.\nLine two."
```

`\n`, `\t`, `\r`, `\"`, `\\`, and `\uXXXX` are all supported. Use `\n` for the
occasional break in a short message; for genuinely multi-paragraph text, use the
paragraph marker below.

---

## Multi-line strings — the `as.paragraph` marker

A string that spans physical lines in the source — a paragraph, a block of
narrative, an **AI prompt** — must be marked with **`as.paragraph`** so Mohio
knows the line breaks are intentional:

```
give back as.paragraph "
    You hold the marble up.

    Then the floor is gone.
"
```

```
ai.decide narrator
    goal as.paragraph "
        Respond as a sardonic narrator.

        Rules:
        - Keep it under two sentences.
        - Never break character.
    "
```

The marker goes **in front of** the opening quote (it tells the reader, and the
compiler, that a multi-line block follows). It works **anywhere a string is
authored** — `give back`, `ai.decide` / `ai.create` prompts, assignments, `show`,
shape-field defaults, `check` comparisons, route bodies.

### Aliases

`as.paragraph` is canonical. These all do the same thing and are accepted:

| Form | Status |
|---|---|
| `as.paragraph` | canonical |
| `as.para` | accepted alias (short) |
| `paragraph` | accepted alias (bare) |
| `para` | accepted alias (bare, short) |

### Naked multi-line strings still work — `as.paragraph` is optional clarity

A string with real line breaks parses fine with or without the marker (it always
has). `as.paragraph` is an **optional authoring-clarity marker** — it signals to a
reader "this is intentionally a multi-paragraph block." It does not change how the
string is parsed or stored:

```
give back 200 "Line one.
Line two."                       // works

give back as.paragraph "Line one.
Line two."                       // same result, clearer intent
```

Prefer `as.paragraph` for long authored prose and prompts (it reads well and marks
intent); use `\n` for a short inline break in an otherwise one-line string.

### Long prose can live in the database instead

Newlines in **data** — database fields, API bodies, user input — always work;
they never pass through the parser. Long narrative or prompt text can be stored
as a field and given back directly:

```
find scene in db.scenes where id matches request.scene_id
find: done
give back 200 scene.text          // scene.text may contain newlines — fine
```

This is often the cleaner pattern for large blocks of content: logic in the
`.mho`, prose in the data.

---

## HTTP status codes

`give back` takes an optional status before the value. Numbers always work:

```
give back 200 "Saved."
give back 404 "Not found."
give back 202 "Queued for review."
```

### Five English aliases

For the most common codes, an English word reads more clearly — especially in
business-facing code. Both the number and the alias are canonical; pick whichever
suits the reader. (`mio fmt`, when available, will leave whichever you wrote
as-is — it does not convert between them.)

| Number | Alias | Meaning |
|---|---|---|
| `200` | `ok` | normal success with a body |
| `201` | `created` | a record was just created |
| `401` | `unauthorized` | not logged in |
| `404` | `missing` | the requested thing doesn't exist |
| `500` | `error` | server-side failure |

```
give back ok "Your changes are saved."
give back created member
give back unauthorized "Please log in."
give back missing "Member not found."
give back error "Something went wrong."
```

Only these five aliases exist for now; everything else uses its number
(`403`, `409`, `301`, …). The human-readable detail goes in the body string —
`give back missing "We couldn't find that account."`

### The default is 200

When you omit the status entirely, it defaults to **200**. These three are
identical:

```
give back "Done."
give back 200 "Done."
give back ok "Done."
```

A `give back as.paragraph "..."` with no status also defaults to 200.

---

## Quick reference

| You want to… | Write |
|---|---|
| A short break in a string | `"Line one.\nLine two."` |
| A multi-paragraph block / prompt | `as.paragraph "..."` (front marker) |
| Long prose | store in the DB, `give back 200 field` |
| Success with a body | `give back ok "..."` or `give back 200 "..."` |
| Just created something | `give back created record` |
| Not-logged-in | `give back unauthorized "..."` |
| Not found | `give back missing "..."` |
| Server error | `give back error "..."` |
| A status with no alias | use the number: `give back 409 "..."` |

**Two rules to remember:** a real line break in a source string needs the
`as.paragraph` marker (or use `\n`), and an omitted status means `200`.
