<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md. -->
# Setting and changing a password minimum

The password rule lives on the field in the shape. There is no hidden global
default; each field declares its own.

## The rule

```
password as text required format "password" min 8
```

- `format "password"` makes it a masked input and stops the value being echoed
  back if the form re-renders after an error.
- `min 8` is the minimum length. `max 64` adds a ceiling. `min 8 max 64` sets both.
- `required` means it cannot be left empty.

## Changing the minimum

Edit the number. To require twelve characters:

```
password as text required format "password" min 12
```

That is the whole change. The rule is per field, so different forms can use
different minimums.

## Changing the message

The default message reads `Password must be at least 8 characters.` (it uses
the field's label if it has one, otherwise the field name). Override it for
that field with `error`:

```
password as text required format "password" min 12 error "Use at least 12 characters"
```

The `error` message replaces the default for any failure on that field.

## Scope and limits

- `min` and `max` here are character length. They apply to text fields.
- A numeric range (a number between two values) uses the separate `range`
  modifier, not `min`/`max`.
- Complexity rules, such as no more than two of the same character in a row, or
  requiring a mix of letters and digits, are deliberately not built into the
  field. That kind of policy belongs in a sector profile, where a regulated
  domain can set and enforce its own password standard, rather than being
  hardcoded into every form. Until that lands, a single `min` length is the
  baseline.

## A note on hashing

`min` validates the password the user types. It has nothing to do with storage.
Always hash before saving:

```
hash signup.password as hashed using bcrypt
save to db.users
    email    signup.email
    password hashed
save: done
```

`bcrypt` is in requirements for production. `pbkdf2` is also supported and uses
only the standard library.
