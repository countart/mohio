<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Mohio Punctuation & Brackets Card

**Consult this every change. Each mark does ONE job.** This is the standing
anti-drift reference for punctuation and brackets.

| Mark | Its one job (and nothing else) |
|---|---|
| `"..."` | Strings. **Double quotes only** — never single quotes. |
| `{{ }}` | Display / interpolate a value or variable. The **only** brace form. Single `{ }` is illegal. |
| `( )` | **Math and expression grouping ONLY.** Never wrap a concatenation. |
| `[ ]` | Data-class tags only: `[phi]`, `[pci]`, `[pii]`. |
| `,` | Separate distinct values — inline or in blocks. |
| `.` | Dot connector: modifiers/additives (`on.failure`, `as.int`), dotted paths (`db.users`), decimals. |
| `&` | Concatenation (join text). Not `+`. |
| `+ - * / %` | Arithmetic ONLY. Not concatenation. |
| `=` | Not for setting a variable — bare is canonical (`x 5`, `hold x 5`). Used only in `lock NAME = value` and comparisons inside `( )`. |
| `: done` | Named block closer (`check: done`, `find: done`). |
| `//`  `/* */` | Comments. `#` is **not** a comment. |

**Never used:** single quotes `'`, single braces `{ }`, `;`, `@`, `$`, backticks.

**Variable tiers:** bare `x 5` = changes freely · `hold x 5` = frozen until `release` ·
`lock x = 5` = permanent.

## Canonical sample (every mark used correctly)

```mohio
// a comment — never use #
lock max = 100                      // = only in lock
hold rate 0.05                      // no = for hold
count 0                             // bare variable, no =
count 5                             // a bare variable changes freely
hold label "user" & "@site.com"     // & joins text, NO parens
hold total (count * max)            // ( ) for math only
show {{ label }}                    // {{ }} displays a value
give back 200 "count is " & count   // & concat, no parens
```
