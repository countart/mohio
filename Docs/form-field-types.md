<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md. -->
# Mohio form field types

Every field is declared on a shape. The renderer turns each into the right HTML
control, validates it, keeps its value on a failed submit, and shows a per-field
error. This is the full set as of 2026-06-23.

## Text and text-like

```
fullname as text required
email    as text required format "email"        // type=email
secret   as text required format "password"     // masked, never echoed
website  as text format "url"                   // type=url
phone    as text format "tel"                   // type=tel
bio      as text multiline                      // <textarea>
```

- `text` is a single-line input.
- `multiline` makes it a `<textarea>` (messages, descriptions, bios).
- `format` switches the input type: `email`, `password`, `url`, `tel`. `phone`
  is accepted as an alias of `tel`.

## Numbers, dates, times

```
age   as number      // type=number  (also integer, decimal)
born  as date        // type=date
start as time        // type=time
when  as datetime    // type=datetime-local
```

## Choices

```
plan as text allowed "Free", "Pro", "Team"                 // <select> dropdown
size as text allowed "S", "M", "L" format "radio"          // radio buttons
```

- `allowed` lists the options. It both renders the control and validates that
  the submitted value is one of them.
- A dropdown by default; add `format "radio"` to render radio buttons instead.

## Multiple choices

```
interests as text allowed "Music", "Sports", "Art" multiple                // checkbox group
langs     as text allowed "EN", "ES", "PT" multiple format "select"        // multi-select
```

- `multiple` lets the field hold more than one value. A checkbox group by
  default; `format "select"` makes it a multi-select box.
- Validates that every chosen value is in the `allowed` list. With `required`,
  at least one must be chosen. Submitted choices stay checked on re-render.

## Boolean

```
agree as boolean      // a single checkbox
```

## Rules you can add to any field

```
required                         // must be present
min 8        max 64              // character length (min, max, or both)
range 18 120                     // numeric bounds (for number fields)
pattern "\d{5}(-\d{4})?"         // must match this regular expression
matches password                 // must equal another field (confirm-password)
default "Free"                   // pre-filled value
label "Your full name"           // the visible label
error "Custom message here"      // overrides the default error for this field
```

## File uploads

A field with several rules reads better stacked. Both forms parse identically;
use whichever is clearer. Simple fields stay on one line; dense ones (uploads
especially) read best multi-line:

```
avatar as image
    required
    accept png, jpg
    max size 2mb

resume as pdf
    required
    accept pdf
    max size 5mb
```

The one-line form still works (`avatar as image required accept png, jpg max size
2mb`), but stacking each rule on its own line passes the walk-by test: a reader
takes it in top to bottom without hunting for where one rule ends and the next
begins. Extensions can be bare (`png`) or quoted (`"png"`).

Upload field types: `image`, `pdf`, `audio`, `video`, and the generic `file`.

- `accept` lists the file extensions you allow. It is required; there is no
  default. The browser uses it as a hint and the server enforces it.
- `max size` sets the size ceiling (`kb`, `mb`, `gb`). Also required, no default.
- Executable and script types (`exe`, `bat`, `sh`, `js`, and similar) are always
  rejected, even if you list them.
- `mio check` errors if `accept` or `max size` is missing on an upload field, and
  warns when a max size is unusually large.
- A stored file lands in `MOHIO_UPLOAD_DIR` (default `./uploads`) under a random,
  sanitized name, and the field is handed to your handler as that stored path, so
  `save` writes the path. The form is sent as `multipart/form-data` automatically
  when it contains an upload field.

## What is not in yet

A `pattern` covers formats like zip codes, SSNs, or custom IDs and is enforced on
the server (`mio check` errors if the expression does not compile). What remains is
the live, in-browser behaviour: the show/hide password eye, validate-as-you-type,
and confirm-password match-on-blur. Those are client behaviour and belong to
MioScript, not the form gate.

Everything else a normal form needs, single and multi-line text, email,
password with confirm, numbers with bounds, dates and times, url and tel,
dropdowns, radios, checkboxes, and multi-select, can be written today,
validated, and kept on re-render.
