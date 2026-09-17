<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Punctuation in Mohio — what each mark means and where

Mohio leans on words over symbols, so the punctuation set is small and each mark has
one job. This is the complete set the language actually uses.

## In active use

| Mark | Where it's used |
|---|---|
| `"..."` | String / text literals, and quoted selectors (`.class`, complex). Double quotes only. |
| `{{ ... }}` | Display interpolation — drop a value into rendered output. The only brace form. |
| `#id` | Bare CSS id selector in MioScript. The only place `#` appears (ids are page anchors). `.class`/complex selectors go inside `"..."`. |
| `[ ... ]` | Data-classification tags on field types: `[phi]`, `[pci]`, `[pii]`. |
| `,` | Separators — list items, allowed values, task parameters. |
| `( ... )` | Task parameters, and grouping / precedence in expressions. |
| `//` and `/* ... */` | Comments. (`#` and `##` are no longer comments.) |
| `:` | Colon — closes blocks (`shape: done`, `block: done`) and opens declarations (`sector:`, `compliance:`, `cm:`). |
| `.` | Dot — connectors and dotted paths (`player.health`, `transaction.amount`), and decimals (`3.14`). |
| `=` | Assignment (`age = 15`, `hold total = 0`). |
| `+ - * / %` | Arithmetic. |
| `&` | String concatenation. |
| `> < >= <= == !=` | Comparison operators, in general expressions and sector thresholds (`amount >= 10000`). |
| `-> <- <->` | Directional arrows, only in language / translation map blocks. |

## Not used

Single quotes `'`, single braces `{ }`, semicolons `;`, `@`, `$`, backticks. If a
construct seems to want one of these, it's a signal to reach for a word or an
existing mark instead.

## Reconcile note (symbol vs word)

The comparison operators (`>`, `>=`, ...) are a split worth resolving before public
release. MioScript conditions deliberately read as words — `is more than`,
`is at least N`, `is at most N` — but general expressions and sector thresholds
still use the symbol forms. Same meaning, two surfaces. Decide whether the symbol
forms should stay (terse math/threshold contexts) or whether the word forms should
reach into expressions too. Logged here, not urgent. Arithmetic (`+ - * /`) is fine
as symbols; "add 3 and 4" would be worse than `3 + 4`.
