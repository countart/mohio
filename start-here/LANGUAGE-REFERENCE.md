<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Mohio Language Reference

*Generated from the grammar on 2026-10-11. Do not edit by hand -- edit `tools/langref/langref_meta.json` and regenerate.*

## How to use this reference

This page is generated directly from the Mohio grammar, so it always matches what the compiler actually accepts. Each entry shows a construct, its status, its syntax, and -- for canonical forms -- an example that is verified to compile. Start with the **canonical** entries: those are the working language you build with. Avoid **retired** and **non-canonical** forms (`mio fmt` rewrites them). Do not rely on **not-built** entries yet. Use search to find a keyword and the status filters to narrow what you see.

Status legend:

- ✅ **canonical** -- Recommended. Teach this.
- ⚠️ **non-canonical** -- Compiles but avoid; mio fmt rewrites it.
- ⛔ **retired** -- Do not use.
- 🔒 **reserved** -- Held for future use.
- 🚧 **not-built** -- Recognized but not executable yet.
- 💳 **commercial** -- Built and working, but a paid service -- not part of the free runtime.


## Comments and layout

### ✅ `// line comment`  
*canonical*

```
// text
```
Example:
```mohio
// this is a comment
```
Line comment. `#` and `##` are NOT comments.

### ✅ `/* block comment */`  
*canonical*

```
/* text */
```
Example:
```mohio
/* a block
   comment */
```
Block comment.


## Variables and values

### ✅ `variable (standard)`  
*canonical*

```
NAME VALUE
```
Example:
```mohio
score 10
```
The everyday variable. Fluid: restate it to change the value, and the type can change too. `=` is optional readability sugar (`age = 15` equals `age 15`); the bare form is what we teach. `set` is retired.

### ✅ `change a value`  
*canonical*

```
NAME VALUE
```
Example:
```mohio
score 20
```
Restate the variable with its new value to change it. A standard variable is mutable; no keyword is needed.

### ✅ `empty typed declaration`  
*canonical*

```
NAME as TYPE
```
Example:
```mohio
count as int
```
Declare an empty typed variable, then assign later by naming it (`count 5`).

### ✅ `default value`  
*canonical*

```
NAME SOURCE default VALUE
```
Example:
```mohio
mode request.mode default "easy"
```
Bare and unparenthesized. Do NOT wrap a default in parentheses; parentheses are math grouping only.

### ✅ `hold (temporary lock)`  
*canonical*

```
hold NAME VALUE
```
Example:
```mohio
hold name "Aria"
```
A TEMPORARY LOCK, used sparingly -- NOT a way to introduce or capture a normal variable. `hold NAME VALUE` FREEZES the value until it is released: a held value cannot be restated (`already held`), and it changes only through the five state operators. The FIVE OPERATORS act ONLY on a HELD variable (verified by running): `release NAME` unfreezes it (restate it afterwards), `clear NAME` empties it, `replace NAME with VALUE` swaps the whole value, `rename NAME to NEW` renames it, `forget NAME` removes it. On a FLUID (bare) variable each refuses at BOTH check and run, naming it: `clear` / `rename` / `forget` / `replace` give `'<name>' is a fluid variable, not held`, and `release` gives `release: '<name>' has no protection to release -- it is neither held nor under a type contract` (verified: `release` on a fluid variable now refuses at check too, closing the earlier run-only gap). A SECOND replace form, `replace "a" with "b" in NAME`, swaps text INSIDE the held value (verified: `hold x "abc"` then `replace "a" with "Z" in x` gives `Zbc`) -- its own form, also held-only. A fluid variable is changed by simply restating it, never by these operators. `release.now` and `release.lock` are RETIRED (see `release.now / release.lock`).

### ✅ `lock (constant)`  
*canonical*

```
lock NAME = VALUE
```
Example:
```mohio
lock pi = 3.14159
```
Permanent immutability. Restating a locked value errors. Unlike `hold`, a lock is not meant to be released.

### ⚠️ `= in assignment`  
*non-canonical*

```
NAME = VALUE
```
Example:
```mohio
age = 15
```
Accepted and kept by mio fmt as readability sugar (`age = 15` equals `age 15`), but the bare form is what we teach.

### ⛔ `set`  
*retired*

```
set NAME VALUE
```
`set` is retired and hard-errors. Write the bare declaration (`age 15`); use `hold` to freeze until released, or `lock` for a permanent constant.

### ✅ `style (freed variable)`  
*canonical*

```
style VALUE
```
Example:
```mohio
style "blue"
show style
```
`style` is an ordinary variable name (FREED in Batch 38: the orphan `style_section` block that used to reserve it was removed, so a bare `style` no longer silently became a nothing-block). Distinct and NOT in conflict with it: the LIVE `style` BODY WORD inside an `ai.create` image / audio / video block (`ai.create image as poster` ... `style "photorealistic"`) is a DIFFERENT terminal and still works. A program may use a variable named `style` and an ai.create `style` line in the same file -- verified, both check clean.


## Types and casts

### ✅ `types`  
*canonical*

```
as TYPE (in a shape field or task param)
```
Example:
```mohio
shape Item
    qty as int
shape: done
```
Common types: text, int (or integer), dec (or decimal), boolean, json. `number` and `num` are retired type names -- use int/integer or dec/decimal. In a shape/param, `as TYPE` is a type annotation, not a value cast. A currency type (an ISO code such as `USD` or `BHD`) carries that currency's decimal scale. Assigning an over-precise literal to a currency variable (`amt as BHD` then `amt 0.0001`) or to a create-block field warns at run that precision is lost (BHD holds 3 places, so 0.0001 rounds); a correctly-scaled or computed value is silent. A `save`, `update` or `upsert` to a shape-governed currency COLUMN (a shape that declares `<table> as table`) DOES warn and round to scale the same way (verified: `amount 0.0001` into a governed USD column warns `stored as $0.00` and stores $0.00, with the currency restored on read-back). A plain `save` to a table the shape does NOT govern carries no currency behaviour at all -- the raw value is stored, unrounded and unformatted -- which is the ungoverned path, not a currency column. The full currency list and its decimal places are under `currency types (money)`.

### ✅ `currency types (money)`  
*canonical*

```
FIELD as <CURRENCY>    // e.g. amount as USD, price as BHD
```
Example:
```mohio
shape Ledger
    entries as table
        amount as BHD
shape: done
```
A field or variable typed with an ISO currency code carries that currency's decimal SCALE; the scale is kept on write and restored on read. The 25 built-in codes, grouped by decimal places. ZERO places: JPY, KRW. TWO places: USD, EUR, GBP, CHF, CAD, AUD, CNY, HKD, SGD, INR, NZD, SEK, NOK, MXN, BRL, ZAR. THREE places (the Gulf and North-African dinars, ISO minor unit 1000): BHD, KWD, OMR, JOD, TND, IQD, LYD. The three-place group is the one most often lost to a two-place assumption, so it is verified by running a governed save and read-back: `amount as BHD` then `amount 1.234` reads back `BD 1.234`; KWD `2.345` reads back `KD 2.345`; OMR `3.456` reads back `OMR 3.456`; JOD `4.567` reads back `JD 4.567` -- all three places kept. An over-precise write warns and rounds to the currency's scale (`1.2345` into a BHD column warns `BHD holds 3 decimal places, so 1.2345 is stored as BD 1.235`), and under a regulated sector that warning becomes a refusal. See `types` for where the scale warning fires (governed column vs plain variable) and `no.symbol (money field)` to render the bare number without the symbol. Verified by running: a currency-typed REQUEST field arrives as an exact decimal, not a binary float -- a POST of `price` 19.99 reads back `$19.99`, and `0.1 + 0.2` on two USD fields is `$0.30`, never `0.30000000000000004` -- and the value saves and reads back exactly on every engine.

### ✅ `text.long (unlimited text)`  
*canonical*

```
FIELD as text.long
```
Example:
```mohio
connect db as sqlite from env.DATABASE_URL
shape Doc
    docs as table
        body as text.long
shape: done
give back [200] "ok"
```
Unlimited-length text, on every engine: a CLOB on Oracle, LONGTEXT on MySQL, and the native unlimited TEXT on SQLite and Postgres. Use it for a field that can grow without a ceiling (an article body, a long note). Because an unlimited column cannot do what a bounded `text` column does, three uses are REFUSED AT CHECK, each naming the field (verified by running): as `unique` (`an unlimited-length field cannot carry a uniqueness index`), in an exact-match comparison -- a `where ... is` or a `match` (`an exact-match comparison (is) on an unlimited-length field is refused`), and as a sort key (`it cannot be used as a sort key`). Separately, a PLAIN `text` value longer than Oracle's 4000-character blanket column width refuses loud at the write, naming `text.long` as the fix, instead of a raw ORA-12899 (that Oracle write-path message is from the dev battery `tests/test_battery_text_long.py`; this lane verified the type and the three check refusals on SQLite, not the live Oracle write).

### ✅ `cast (dotted)`  
*canonical*

```
VALUE as.TYPE
```
Example:
```mohio
total 5.7
rounded total as.int
show rounded
```
Casts are dotted: `as.int` (rounds), `as.decimal.N` / `as.dec.N`, `as.number`, `as.text`, `as.bool`, `round.up`, `round.down`. `as.decimal.N` (and `as.dec.N`) TRUNCATES to N places -- it drops the extra digits toward zero, it does NOT round (verified by running: `3.146 as.decimal.2` is 3.14, and `-3.146 as.decimal.2` is -3.14), which is the difference from `round.to N` (ties half away from zero, so `3.146 round.to 2` would round). The two-word `as int` is not a cast today.


## Text

### ✅ `join (&)`  
*canonical*

```
A & B
```
Example:
```mohio
name "Bo"
show ("Hi " & name)
```
Concatenate with &.

### ✅ `text tools (no arg)`  
*canonical*

```
VALUE uppercase | lowercase | trim
```
Example:
```mohio
name "ada"
big name uppercase
show big
```
The no-argument text tools, written as an adjective after the value: `name uppercase`, `name lowercase`, `name trim`. The adjective form is canonical because it reads as English. The dotted spellings `as.uppercase` / `as.uc` and `as.lowercase` / `as.lc` are ALIASES of `uppercase` / `lowercase`; prefer the adjective. There is no `title` or `sentence` adjective, so those two cases are written dotted (`as.title` / `as.sentence`), which are canonical casts.

### ✅ `text tools (with arg)`  
*canonical*

```
replace "x" with "y" | left N | right N | after "x" | before "x"
```
Example:
```mohio
card "4111111111111111"
last4 card right 4
```
replace/with, left N, right N, after, before.


## Show and render

### ✅ `show`  
*canonical*

```
show VALUE
```
Example:
```mohio
show "hello"
```
Display a value (`show "hi"`), or, as a block, raw HTML lines between `show` and `show: done` with `{{ }}` interpolation. Both behave as before after the render changes. Unlike `render`, `show` keeps the generic closer and has no closer-escape, so a `show` block cannot display a bare `show: done`; it was left this way deliberately because giving `show` a dedicated closer shifted an unrelated saga-step ambiguity.

### ✅ `{{ value }}`  
*canonical*

```
{{ VALUE }}
```
Example:
```mohio
show {{ name }}
```
Drop a value into rendered output and render inline Mohio. The only brace form; single braces are illegal.

### ✅ `{{ form sh.X }}`  
*canonical*

```
{{ form sh.SHAPE }}
```
Example:
```mohio
shape Signup
    email as text
shape: done
render
    {{ form sh.Signup }}
render: done
```
Render a form from a shape. The form engine is open-core.

### ✅ `render`  
*canonical*

```
render [html|fragment|scripts|code]   (or `render as <kind>`)
    ...body...
render: done
```
Example:
```mohio
render
    <h1>Hi</h1>
render: done
```
Emit a page body. `render` and `render html` both carry HTML markup and behave identically (html is the default, a wrapped document). The FOUR kinds the grammar recognises are `html`, `fragment` (an unwrapped piece of a page -- what `inject` places when the response carries `X-Mohio-Rendered: fragment`), `scripts` (a JavaScript container, wrapped in <script>), and `code` (source shown as text, wrapped in <pre><code>, HTML-escaped). A BARE unknown kind is REFUSED at check, naming the four: `render text` gives `names a render kind that does not exist. The kinds are html, fragment, scripts, code`. `render as <kind>` works for all four kinds identically to the bare spelling (Batch 40), and an unknown kind is refused in BOTH spellings (`render as bogus` gives the same `names a render kind that does not exist` message) -- the earlier leak, where `render as text` checked clean and put the words into the page, is closed. A render block closes on its OWN dedicated closer `render: done`, so every other closer-shaped line inside it (`task: done`, `check: done`) is content -- that is what lets `render code` show real Mohio source. `render` is not a legal variable name; `render <kind>` can only open a render block. The one line it cannot show bare is its own closer, so a leading backslash escapes it: `ender: done` displays as `render: done`. `render` refuses a <script> tag or an inline `onclick=` in markup (see `no script in a render`). A `{{ each NAME in SOURCE }}` view loop is LINE-ANCHORED: the opener alone on its line, the closer `{{ each: done }}` alone on its line, row markup on the lines between. An inline `each` (markup on the same line) or one missing `in` is REFUSED at check, pointing at the multiline form -- it used to check clean and then 500 at run, the whole marker read as an unknown variable. UNICODE (ruled 2026-10-06): the ASCII-only rule is for Mohio KEYWORDS and IDENTIFIERS, not content -- literal render text and string literals may hold ANY character (a middle dot, right-to-left Arabic, an emoji), verified at check and in the served page. It holds inside a render-included fragment too (`{{ include "_head.mho" }}`): a `_name.mho` pulled in as page text is not parsed as code. But a file `include`d as a STATEMENT is code, so a non-ASCII IDENTIFIER in it is still refused, naming the file.


## Decisions

### ✅ `check / when / otherwise`  
*canonical*

```
check VALUE
    when CONDITION
        ...
    otherwise
        ...
check: done
```
Example:
```mohio
score 120
check score
    when score is more than 100
        show "Amazing!"
    otherwise
        show "Keep going"
check: done
```
Body on the next line, indented. Works with every operator. Do not use the inline `when ... -> result` form.

### ✅ `check ... against`  
*canonical*

```
check VALUE against VALUE
    on.success
        ...
    on.failure
        ...
check: done
```
Example:
```mohio
amount 50
limit 100
check amount against limit
    on.success
        show "ok"
    on.failure
        show "over"
check: done
```
Compare a value against another and branch with `on.success` / `on.failure`. It now CLOSES with `check: done`, the same closer every other check block uses (ruled 2026-10-04). The UNCLOSED form is refused at check (verified): `check ... against needs its own closer now, the same check: done every other check block ...`, with the fix shown. It had been the one block sharing the result-handler grammar with no closer, so a statement written after it was silently swallowed into the last handler that ran rather than standing on its own; the closer ends that. Verified by running: the closed form checks clean, the unclosed form refuses.

### ✅ `unless`  
*canonical*

```
ACTION unless CONDITION
```
Example:
```mohio
door_is_open false
show "Locked" unless door_is_open
```
Negative one-liner.

### ✅ `natural conditions`  
*canonical*

```
is more than | is less than | is between A and B | is not | contains | starts with | is empty
```
Example:
```mohio
age 15
check age
    when age is between 10 and 20
        show "mid"
check: done
```
Natural language preferred outside parentheses.

### ⛔ `if / else as block`  
*retired*

Use check/when/otherwise (or `unless`). `if` is an inline qualifier only.

### ✅ `dotted conditions (starts.with / ends.with / is.empty / is.in / is.not)`  
*canonical*

```
when VALUE starts.with "..."   // ends.with, is.empty (prefix or postfix), is.not VALUE; is.in takes a TIME PERIOD
```
Example:
```mohio
name "Aria"
check name
    when name starts.with "A"
        show "match"
check: done
```
Dotted test operators for a check/when branch. `starts.with` / `ends.with` "text"; `is.empty` (no value; works PREFIX or POSTFIX since Batch 31 -- `is.empty VALUE` or `VALUE is.empty`); `is.not VALUE` (inequality). `is.in` tests TIME-PERIOD membership, NOT list membership: it takes a period, not a list, and is used on a date field in a where-clause -- `where created_at is.in today` (also `is.in last 24 hours`, `is.in this year`). A plain `VALUE is.in LIST` does NOT test list membership, and there is no dotted list-membership operator today. They read as the condition they test.

### ✅ `not.empty / not.found`  
*canonical*

```
when not.empty VALUE   // or: when VALUE not.empty
```
Example:
```mohio
msg "hi"
check msg
    when not.empty msg
        show "has text"
check: done
```
`not.empty` is built and works in BOTH positions in a check/when value position since Batch 31: `not.empty VALUE` and `VALUE not.empty` both test correctly, and `is.empty` likewise works either way. In a WHERE clause it is DIFFERENT: the dotted `not.empty` does NOT work over a table OR a held list -- `where not.empty X` 500s (the clause reads the next word as a field) and `where X is not.empty` 500s (reads `not` as a variable). The where-clause emptiness test is the SPOKEN `where X is not empty` / `where X is empty`, verified over both a table and a held list. So dotted `not.empty` is a check/when predicate, not a where-clause form. `not.found` is now BUILT as a SUBJECT-FIRST check/when predicate (verified): `check member / when member not.found` tests that a lookup came back empty (`member not.found`, subject first, like `not.empty`). For a negated emptiness test use `not.empty VALUE` in a check/when, or `is not empty` in a where; for an empty lookup use `member not.found`.

### ✅ `is even / is odd`  
*canonical*

```
when NUMBER is even   // or: is odd
```
Example:
```mohio
n 4
check n
    when n is even
        show "even"
    otherwise
        show "odd"
check: done
```
Parity predicates for a whole number, written subject-first (POSTFIX) inside a `check`/`when` branch: `when NUMBER is even` (verified). They are `check`/`when` ONLY: inside a `where` clause `is even` / `is odd` fail loud (`unknown_filter_value: where refers to 'even'`), so a query cannot filter on parity today. Since Batch 33 the wrong-order (subject-less) form `when is even` -- the predicate before its subject -- is REFUSED at check, in either half of a compound condition, with a teaching message showing the subject-first form; it previously checked clean and silently never matched. Both the subject-less form (`when is even`) and the trailing-subject form (`when is even n`) are now REFUSED at check, so a predicate written before its subject never reaches run -- write the subject first, `when n is even`. `even` and `odd` are reserved words, so `is even` can never silently read `even` as an undefined variable.

### ✅ `is not empty (spoken)`  
*canonical*

```
when SUBJECT is not empty   |   where FIELD is not empty
```
Example:
```mohio
n "x"
check n
    when n is not empty
        show "has a value"
check: done
```
The spoken negated-emptiness test, now working in BOTH positions like its partner `is empty` (built, verified): subject-first inside a `check`/`when` (`when name is not empty`) AND in a `where` clause (`where name is not empty`). It used to fail loud in `check`/`when` (`check/when does not yet support the wc_is_not_empty comparison form`); that limitation is gone. The predicate must FOLLOW its subject: the wrong-order forms are still refused at check, teaching the fix -- subject-less (`when is not empty`) and trailing-subject (`when is not empty n`) both fail because the test is written before its subject. `is empty`, `starts with` and `ends with` read the same subject-first way.


## Loops

### ✅ `repeat each (collection)`  
*canonical*

```
repeat each ITEM in COLLECTION
    ...
repeat: done
```
Example:
```mohio
colors as list "red", "green"
repeat each color in colors
    show color
repeat: done
```
repeat is the verb, each is the constraint. The canonical collection loop.

### ✅ `repeat N times (count)`  
*canonical*

```
repeat N times
    ...
repeat: done
```
Example:
```mohio
repeat 3 times
    show "hi"
repeat: done
```
Count loop.

### ✅ `while`  
*canonical*

```
while CONDITION
    ...
while: done
```
Example:
```mohio
count 0
while count is less than 3
    count (count + 1)
while: done
```
Loop while a condition holds.

### ⚠️ `each (opener)`  
*non-canonical*

```
each ITEM in COLLECTION
```
Example:
```mohio
each user in users
    show user.name
each: done
```
Compiles, but `each` is a constraint not a verb. Use `repeat each`. mio fmt rewrite target.


## Tasks

### ✅ `task`  
*canonical*

```
task NAME
    take PARAM as TYPE
    returns TYPE
    give back VALUE
task: done
```
Example:
```mohio
task greet
    take name as text
    give back ("Hi " & name)
task: done
```
Inputs are declared with `take` lines in the body -- NOT on the task header (that form is retired). `returns TYPE` is optional and goes on its OWN line AFTER the `take` lines (a `returns` on the header line fails to parse once a typed `take` follows it); it makes `give back` a value the caller can capture with `call NAME as RESULT`.

### ✅ `take`  
*canonical*

```
take NAME (, NAME)* [as TYPE] [default VALUE]
```
Example:
```mohio
task total
    take a, b as int and c as text
    give back a
task: done
```
Declares a task's inputs. `as TYPE` is optional (bare = untyped); a `take` with no `default` is required, a `default` makes it optional. `and` joins type-groups on one line; several `take` lines are equivalent.

### ✅ `give back`  
*canonical*

```
give back VALUE
```
Example:
```mohio
give back "pong"
```
Return a value. Always two words.

### ✅ `call`  
*canonical*

```
call NAME with VALUE   |   call NAME as RESULT / input value / ... / call: done
```
Example:
```mohio
call greet with "Bo"
```
call only ever invokes a task. One value: `call greet with "Bo"`. Several values by name (order-free): `call NAME / a 7 / b 9 / call: done`. Capture a task that `returns` a value with `call NAME as RESULT` (then read `RESULT`). Bad arguments fail loud. A `call` CANNOT carry `on.failure`: a task owns what failure means for it (ruled 2026-09-26), so failure is handled INSIDE the task and the caller reads the outcome -- `give back` a value that says what happened and branch on it at the call site.

### 🚧 `run / wait for`  
*not-built*

```
run async ... | wait for ...
```
Async work is being finalized; the transformer currently steers task invocation to call. Use call to invoke a task. run/async/wait are not stable yet.


## Error handling

### ✅ `try / on.failure / on.success / always`  
*canonical*

```
try
    ...
    on.failure
        ...
    on.success
        ...
    always
        ...
try: done
```
Example:
```mohio
try
    save to db.users
        name "Bo"
    save: done
    on.failure
        show "failed"
    always
        show "done"
try: done
```
on.failure / on.success / always. `catch` is retired in favor of on.failure.

### ✅ `raise`  
*canonical*

```
raise "message"
```
Example:
```mohio
raise "not allowed"
```
Raise an error.

### ✅ `failure (typed value in on.failure)`  
*canonical*

```
failure.kind | failure.message | failure.status   (inside on.failure)
```
Example:
```mohio
connect db as sqlite from env.DATABASE_URL
save to db.t
    id 1
    on.failure
        show failure.kind
save: done
```
Inside an `on.failure` body, `failure` carries what MOHIO DETERMINED about the failure, read the way `result.status` / `result.ok` are: `failure.kind`, `failure.message`, `failure.status`. A program branches with an ordinary `when failure.kind is "conflict"` -- no new construct (Batch 38). The KIND is Mohio's determination (the raised error's name, or Mohio's own duplicate-key signatures), never a string parsed out of the message; an unclassifiable failure is an explicit `unknown`. Kinds and their HTTP status: conflict 409, db_error 500, connection_error 503, not_found 404, validation_error 400, authorization_error 403, timeout 504, provider_error 502, any raised error name, and `unknown`. It is bound at every `on.failure` site: a write, a connection-level `on.failure`, a connector call, and every AI call (an AI call defaults to `provider_error`). Verified: a duplicate save gives `conflict` / 409 with the driver detail in `failure.message`; a query with no connection gives `connection_error` / 503. HTTP is the one exception: `miohttp` surfaces failure through `result.ok` / `result.status` (an unreachable host gives `ok=false`, `status=0`), not a `failure` value. A retrieve MISS is a conditional outcome (`otherwise`), not an operational failure, so it does not bind `failure`. The CLIENT reads the same vocabulary: a `send` failure's `result.kind` (see `send`) uses these same names, so a `conflict` reads `conflict` on both sides -- with two client-only caveats, a 500 stays `unknown` and the 400 family collapses to `validation_error`, and a network exception carries the client-only kind `network`. An AI operation's failure ALSO carries three AI-specific fields (ruled 2026-10-05, verified at check): `failure.provider` (which provider failed), `failure.stage` (`call`, `response validation`, or `confidence`), and `failure.error` (the provider's HTTP status when there was one, otherwise its error) -- so an `ai.decide` / `ai.compare` / `ai.respond` `on.failure` can tell a bad key (a 401 at the `call` stage) from a non-decision (`response validation`) from a low-confidence answer (`confidence`).


## Shapes

### ✅ `shape`  
*canonical*

```
shape NAME
    FIELD as TYPE [modifiers]
shape: done
```
Example:
```mohio
shape Signup
    email as text required
    age as int min 0 max 120
shape: done
```
Declared with a bare name. Reference it elsewhere with sh.NAME. Declaring `shape sh.Name` is an error. A shape can GOVERN a table with `<name> as table`, binding its field types to that table -- the TYPE-SAFE path. A governed column reads back at its DECLARED type: an `as text` column keeps its stored STRING (verified: `5.50` stays `5.50`, `+1` keeps its sign, `1e3` and `007` are unchanged), while an `as int` column coerces to int. A LOOSE-FIELD shape (fields declared WITHOUT `<name> as table`) does NOT bind its types to the table it writes, so a read GUESSES the type from the value and coerces (`5.50` comes back `5.5`) -- the same guess a read with no shape at all makes. Choose `as table` for data you read back and then compare or display.

### ✅ `field modifiers`  
*canonical*

```
required | optional | unique | multiline | multiple | min N | max N | format "..." | label "..." | default VALUE | matches NAME | pattern "..."
```
Example:
```mohio
shape Card
    cvv as text never store
shape: done
```
Modifiers tighten a field. `required`, `min N`, `max N`, `range N M`, `pattern "..."` and `allowed ...` are enforced on an incoming request and refuse with a 422 naming the field -- on BOTH a POST body and, since this landed, a GET QUERY VALUE, validated against its shape the same way WHEN A QUERY IS PRESENT (verified: a GET carrying `age=5` against `min 18` answers 422, and a GET carrying any parameter with a `required` field missing answers 422). A bare GET with NO parameters at all is deliberately not validated -- `required` does not fire on an empty query, so a plain page load is not a 422; `never store` / `never log` enforce data handling; the classification tags (`[pii]`/`[phi]`/`[pci]`) seal and mask. `unique` is ENFORCED wherever the shape's write target resolves: a plain shape saved to a table gets a real unique index and a duplicate answers 409 `conflict` (verified), identical to an `as table` shape. A shape saved to a RUNTIME-CHOSEN table refuses at check (there is nowhere to land the index), and a `unique` field on a shape that is NEVER written is INERT and warns once (`unique on <field> in shape <X> is inert: this shape is never written to a table`). So `unique` is always enforced, refused, or named inert -- never a silent no-op, including on an ENCRYPTED tagged field (`[phi]`/`[pci]`): the unique index lands on the field's blind index (`<field>__bidx`, a deterministic hash), so two identical plaintext values collide and the duplicate is refused with 409 `conflict` (verified by running, on every engine). `default VALUE` on a field of a PLAIN shape (not only an `as table` one) now fills that field when a request omits it, and the filled value is visible every way a handler reads it -- `shape.field`, `request.field`, and the bare `field` (verified by running); a value the request DID send is never overridden by the default. A request field arrives already coerced to the type its shape declares (verified by running: an `int` field arrives as a number, a currency field as an exact decimal); a value that cannot be coerced to the declared type is refused with a 422 naming the field.

### ✅ `no.symbol (money field)`  
*canonical*

```
FIELD as <CURRENCY> no.symbol   (a shape field modifier)
```
Example:
```mohio
shape S
    prices as table
        p as USD no.symbol
shape: done
```
A money field declared `no.symbol` RENDERS the bare number -- it drops the currency symbol while keeping the locale separators, the SCALE, and the currency CODE on the value, so a locale service can re-render it later. Verified across all three symbol styles in a render: a leading symbol (`USD` renders `8.50`), a trailing one (`SEK` renders `8,50`), and a code-style currency (`BHD` renders `8.500`, its 3-place scale kept). JSON and `give back` are UNCHANGED -- a currency serialises to its plain number there regardless of this modifier. It applies on every display path: `show` and a render now agree, both emitting the bare number (the earlier divergence where `show` still printed the symbol is closed).

### ✅ `sh. reference`  
*canonical*

```
sh.NAME
```
Example:
```mohio
listen for sh.Signup at /signup
    require role "user"
    give back [201] "created"
listen: done
```
Reference a declared shape with `sh.NAME`. The prefix names the shape wherever a shape is expected -- here inside a `listen for` handler block. A shape reference is never a statement on its own: a bare `sh.Signup` does not stand alone, so it appears inside a handler (`listen for sh.Signup at /path`) or another construct that expects a shape.


## Data (MioQL)

### ✅ `connect`  
*canonical*

```
connect db from env.DATABASE_URL          // adaptive: engine comes from the address
connect db as <engine> from env.X          // pin an engine: sqlite | postgres | mysql | mongodb | oracle
```
Example:
```mohio
connect db from env.DATABASE_URL
```
Open a database connection. THE ADAPTIVE FORM LEADS: `connect db from env.DATABASE_URL` names no engine, so the engine is inferred FROM THE ADDRESS the variable holds (verified by running) -- a plain file path opens SQLite (`[connect] SQLite file ...`), and a scheme address names its own engine (a `postgresql://...` value connects as Postgres). This is the form to reach for: one line that follows the deployment's own connection string. TO PIN AN ENGINE, name it: `connect db as <engine> from env.X`, where `<engine>` is `sqlite`, `postgres`, `mysql`, `mongodb` or `oracle` (`mariadb` routes through the mysql driver; `postgresql` and `mongo` are accepted aliases). `oracle` is SUPPORTED (ruled by Ron, verified on Oracle AI Database 26ai; 19c verification follows after the tag). Either way Mohio does NOT fall back to SQLite when the address names a networked database -- a bad one fails loud (`declared as postgres ... Mohio does not fall back to SQLite`) rather than silently opening SQLite. SOURCE: the connection string comes from `from env.X` or `from secret.X` only; a literal string is refused at check (`A connect source must be env.X or secret.X, never a literal string`), and the DECLARED source governs the connection for every engine, not a fixed env name, so `from env.MY_PG` opens exactly `MY_PG` and an unset declared var is refused naming that variable. An engine Mohio does not support is refused AT CHECK (verified: exit 1), with the same message the runtime gives -- check and run read one shared driver list, so they cannot drift: `'<engine>' is not a database Mohio knows. To proceed: use sqlite, postgres, mysql, mongodb or oracle.` (SQL Server is not supported yet and refuses this way today.) Connect once at the top; tables are `db.NAME`, and a table materialises on its FIRST WRITE. A read of a table that has not materialised yet depends on whether the program DECLARES it. A table the program WRITES anywhere (a `save`/`update`/... targets `db.<name>`) or GOVERNS with `<name> as table` returns EMPTY on a fresh tenant (verified), because before its first write it is legitimately empty. A read of an UNDECLARED table -- one only ever read, never written, never shaped -- still FAILS LOUD (`db_error: no such table: <name>`), because an unknown table is a typo or the wrong database, not an empty one. THE EDGE: a table read on one handler and written on another is DECLARED (a write ANYWHERE in the program declares it) and returns empty; a table named ONLY in a read is undeclared and fails. SECURITY IMPLICATION, deliberate: a check reading an empty declared table on a fresh tenant sees EMPTY, so an empty denylist admits everyone and an empty allowlist admits no one. That is inherent to a fresh deployment, not this rule; SEED such a table so the check has data to read.

### ✅ `typed storage (how values are stored)`  
*canonical*

```
(a shape field's declared type governs its stored column type)
```
Example:
```mohio
connect db as sqlite from env.DATABASE_URL
shape R
    recs as table
        n as int
        ok as boolean
        price as dec.2
shape: done
save to db.recs
    n 5
    ok true
    price 3.10
save: done
give back [200] "ok"
```
A shape field is stored as its DECLARED type, not as text (T1-DB-TYPED-STORAGE, ruled 2026-10-04/05), the same across SQLite, Postgres, MySQL and Oracle. Verified by running (save then read back through a fresh find): `int` stores as an integer, `boolean` as an integer, a CURRENCY at the currency's own exact scale, and a declared-scale decimal (`dec.N` / `decimal.N`) at that scale -- on SQLite, which has no native fixed-point type, both the currency and the declared-scale decimal are stored as a SCALED INTEGER (the minor unit, e.g. cents, exact, zero float risk). A BARE decimal with NO declared scale (not a currency) is never given an invented scale: it is stored EXACTLY as text. A value with MORE decimal places than its declared scale holds REFUSES LOUD at the write (verified: `3.105` into a `dec.2` field -- `3.105 has more decimal places than this field holds (2). Round it explicitly first`), never rounding silently. See `exact numbers`, `currency types (money)`, and `mio migrate typed-storage` to convert a table created before typed storage.

### ✅ `retrieve (one row)`  
*canonical*

```
retrieve X from db.Y
    match FIELD to VALUE
retrieve: done
```
Example:
```mohio
retrieve player from db.players
    match id to 1
retrieve: done
```
One row. Stack match lines for AND. `match any` = OR, `no.match` = NOT (closes no.match: done). A POST-FETCH CLAUSE IS REFUSED on `retrieve` at check: `return`, `export`, `summarize`, `calculate`, `inject` and the time clause (`timespan`) each give `retrieve: <clause> is declared but not yet built on retrieve` and stop the build (they used to be silently dropped; `inject` was the last to slip through and is now caught too). `join` (`with ... from`) is refused as well, but by its own build-wide message (native JOIN is not executable yet -- use the SQL escape hatch), not the retrieve-specific one. Ron's ruling: `retrieve` returns ONE record while those clauses shape or write from a RESULT SET, so they belong on `find`, the collection verb that already exports; a one-row export is `find` with a `match` and a limit. (Fresh-tenant read: a DECLARED-but-unmaterialised table returns empty, an UNDECLARED table fails loud -- see `connect`.)

### ✅ `find (many rows)`  
*canonical*

```
find X in db.Y
    where CONDITION
find: done
```
Example:
```mohio
find tops in db.players
    where active is true
find: done
```
Many rows. `where` is for ranges/comparisons. A read of a DECLARED table (written or `as table`-governed somewhere) that has not materialised returns EMPTY on a fresh tenant; a read of an UNDECLARED (read-only) table fails loud. See `connect`.

### ✅ `with (left join)`  
*canonical*

```
find PARENTS in db.A
    with CHILDREN from db.B
        match INNER_FIELD to PARENTS.KEY
    with: done
find: done
```
Example:
```mohio
connect db as sqlite from env.DATABASE_URL
shape Member
    members as table
        name as text
shape: done
shape Order
    orders as table
        member_id as int
shape: done
find people in db.members
    with purchases from db.orders
        match member_id to people.id
    with: done
find: done
show people.first.purchases.count
```
A native LEFT join on `find`. EVERY parent is returned exactly once, and its matched children are nested as a list under the join name (`people.first.purchases`); a parent with no match gets an EMPTY child list, never a phantom all-null child. Verified by running: two members, only Aria has orders -- `people.count` is 2, `people.first.purchases.count` is 2, and Bo's is 0. The one inner clause is `match INNER_FIELD to PARENTS.KEY`, which MUST name the find's own outer record (the name after `find`); any other inner clause, or a match not naming the outer record, is refused at check. The block always closes `with: done`. A per-parent `up to N` INSIDE the `with` caps children per parent (an empty parent is still returned). An outer bound AFTER `with: done` counts PARENTS, not joined rows: `up to 2` returns two parents and keeps all their children, and `paginate by` pages parents with `rows.page.total` = the parent count. CHAINED (verified): a `with` nests inside another `with` to any depth, each level carrying its own `match`, `where` (comparing a child column to a literal OR to any ANCESTOR level's column, e.g. `where amount_held is above c.payment_amount`), sort and per-level `up to`; the whole chain runs as ONE pushed-down query, and `mio run --verbose` prints a one-line `[join] notice` on a 3-level-or-deeper chain (it is a run --verbose notice, not a check error). SIDE-BY-SIDE SIBLINGS (verified): more than one top-level `with` on the same find each runs as its own independent list, one query each under one consistent read -- the old one-with-per-find limit is gone. On RETRIEVE (verified): a `with` runs on `retrieve` too, including the dotted `retrieve.all` / `.first` / `.last` / `.count`, where the retrieve modifier decides what the join returns. ENGINES: verified on sqlite, and the same shape on postgres/mysql/oracle; on Mongo `with` and `with.required` run via a $lookup (with.all is refused on Mongo at check). See `with.required (inner join)` to drop unmatched parents, and `with.all (full outer join)` for orphan children.

### ✅ `with.required (inner join)`  
*canonical*

```
find PARENTS in db.A
    with.required CHILDREN from db.B
        match INNER_FIELD to PARENTS.KEY
    with: done
find: done
```
Example:
```mohio
connect db as sqlite from env.DATABASE_URL
shape Member
    members as table
        name as text
shape: done
shape Order
    orders as table
        member_id as int
shape: done
find people in db.members
    with.required purchases from db.orders
        match member_id to people.id
    with: done
find: done
show people.count
```
A native INNER join on `find`: identical to `with (left join)` except a parent with NO matching child is EXCLUDED from the result. Verified by running: two members, only Aria has orders -- `people.count` is 1 (Bo is dropped), where `with` would return 2. Everything else matches `with`: the match names the find's outer record, it closes `with: done`, children nest under the join name, and the same per-parent `up to`, parent-counting outer bounds, CHAINING (nested `with`), SIDE-BY-SIDE SIBLINGS, and use on `retrieve` (incl. `retrieve.all` / `.first` / `.last` / `.count`) all apply exactly as for `with` -- see that entry for the detail. On Mongo it runs via a $lookup, like `with`. Use `with` when you want every parent with a possibly-empty child list; use `with.required` when a parent is only meaningful with at least one child. A required join keeps filtering the root no matter what other joins sit beside it (tenant isolation, HC-132): a root row survives only when EVERY required sibling kept it, and a plain `with` beside a `with.required` cannot put back a row the required join dropped. Verified by running: a member with no matching required `perm` row stays out even though a plain `with log` sibling matches it -- the result is 1 row, not 2.

### ✅ `with.all (full outer join)`  
*canonical*

```
find PARENTS in db.A
    with.all CHILDREN from db.B
        match INNER_FIELD to PARENTS.KEY
    with: done
find: done
```
Example:
```mohio
connect db as sqlite from env.DATABASE_URL
shape M
    jm as table
        name as text
shape: done
shape O
    jo as table
        mid as int
shape: done
find rows in db.jm
    up to 1
    paginate by 1
    with.all purchases from db.jo
        match mid to rows.id
    with: done
find: done
show rows.page.unmatched.count
```
Full-outer semantics on `find`, built as TWO SEPARATE LISTS (a LEFT join plus an anti-join), never a SQL FULL OUTER JOIN and never a pseudo-parent wrapper, so it runs unchanged on sqlite, postgres, mysql and oracle. The bound name (`rows`) is the MATCHED parents, exactly like `with` (LEFT); `rows.unmatched` is the list of orphan CHILD rows that match no parent. When an outer `up to` / `paginate` / `skip` is applied, ONE page size runs across both lists (matched rows fill a page first, unmatched fill what is left), and the page fields are, EXACTLY as the compiler produces them (verified by running, 2 matched + 1 orphan, page size 1, page 1): `rows.count` = matched on this page (1), `rows.unmatched.count` = orphans on this page (0), `rows.page.count` = combined total across both lists (3), `rows.page.unmatched.count` = total orphans across the WHOLE sequence, not just this page (1), `rows.page.unmatched.start` = the 1-based page number orphans first appear on, or null when there are none (3). REFUSED at check: `with.all` on the Mongo backend (a $lookup produces only the LEFT shape), and `with.all` alongside a sibling `with` on the same find. Below SQLite 3.39 it refuses, naming the version and the workaround.

### ✅ `save`  
*canonical*

```
save to db.Y
```
Example:
```mohio
save to db.players
    name "Ada"
    score 100
save: done
```
Insert. A duplicate key (an id or unique field that already exists) answers 409 `conflict`, naming the colliding key (e.g. `UNIQUE constraint failed: things.id`), distinct from a 500 `db_error`. Catch it with `on.failure`, or use `save or update` to write-or-replace instead of insert. The detector is by message signature, so it holds on SQLite, Postgres and MySQL alike. A WRITE OVER A HELD LIST is declared but NOT built and refuses at check -- `save`, `save or update`, `update`, `remove` over an in-memory list (`colors as list ...`), and `modify every` over a list of PLAIN values, each say `<verb> over a held list is declared but not built` and point at a real database (`save to db.<table>`). `modify` over a list of RECORDS already works; reads over a held list (`find`/`retrieve`/`grab`) work too. `save all` needs a `db.<table>` source, so it has no held-list form.

### ✅ `update`  
*canonical*

```
update db.Y [as NAME]
    match id to VALUE
    FIELD VALUE
update: done
```
Example:
```mohio
update db.players
    match id to 1
    score 200
update: done
```
Bare field values inside update; match selects the row. Optional `as NAME` binds the COUNT of rows the match selected (0 when a conditional update matched nothing) -- the discriminator that tells which of two concurrent writers won a compare-and-set.

### ✅ `upsert`  
*canonical*

```
upsert db.Y match KEY to "..."
```
Example:
```mohio
connect db as sqlite from env.DATABASE_URL
upsert db.players
    match id to 1
    name "Ada"
upsert: done
```
Insert or update. Unlike `update` and `remove`, `upsert` does NOT take `as NAME`: it always writes exactly one logical row, so there is no matched-row count to bind.

### ✅ `remove`  
*canonical*

```
remove from db.Y [as NAME]
    match FIELD to VALUE
remove: done
```
Example:
```mohio
remove from db.players
    where id is player.id
remove: done
```
Delete a row. `remove` is the deletion verb (not delete). Optional `as NAME` binds the COUNT of rows removed, the same compare-and-set discriminator as on `update`.

### ✅ `order`  
*canonical*

```
order.up by FIELD | order.down by FIELD
```
Example:
```mohio
find tops in db.players
    order.down by score
find: done
```
Ascending / descending, by a real database column. A column named in a `return` projection can be ordered by (`return name, age` then `order.up by age` -- verified): a plain projection where the projected name IS the column is not treated as computed. A RENAME (`return amount as amt`) or a genuinely computed value stays computed, and ordering by that alias is refused (`order_by_computed_column`), because the database orders before a computed value exists -- order by the real column instead.

### ✅ `check count`  
*canonical*

```
check count as NAME in db.TABLE
    ...
check: done
```
Example:
```mohio
check count as n in db.users
check: done
```
Count the rows in a table, bound to a name. A read that answers "how many"; pair it with a where/match clause to count a subset.

### ✅ `check exists`  
*canonical*

```
check exists NAME in db.TABLE
    match FIELD to VALUE
    when empty
        ...
    otherwise
        ...
check: done
```
Example:
```mohio
check exists found in db.users
    match email to "a@b.com"
    when empty
        show "available"
    otherwise
        show "taken"
check: done
```
Ask whether a matching row exists. It branches with `when empty` (nothing matched) and `otherwise` (a match). `on.success` / `on.failure` are refused here: existence is a question, not a success-or-error.

### ✅ `check unique`  
*canonical*

```
check unique in db.TABLE
    match FIELD to VALUE
    when empty
        ...
    otherwise
        ...
check: done
```
Example:
```mohio
check unique in db.users
    match email to "a@b.com"
    when empty
        show "available"
    otherwise
        show "taken"
check: done
```
Ask whether a value is free to use. `when empty` means it is not taken (available); `otherwise` means a row already has it. Same handler rule as `check exists`.

### ✅ `match modifiers (match.all / match.any / match.none / match.unique)`  
*canonical*

```
match.MOD FIELD to VALUE, FIELD to VALUE, ...   (inside a retrieve/find/check query)
```
Example:
```mohio
retrieve u from db.users
    match.any email to "a@b.com", name to "Bo"
    when u is empty
        give back [404] "not found"
retrieve: done
```
How several match pairs combine in a query. `match.all` requires every pair (AND); `match.any` any one pair (OR); `match.none` requires that no pair matches (NOT); `match.unique` that exactly one row matches. Plain `match` (no modifier) is AND. Verified: with a row matching only one pair, `match.all` finds nothing while `match.any` returns it. A not-found is `when <var> is empty` inside the block (shown here), never `on.failure`, which fires only when the lookup itself breaks (a lost connection or missing table) and is refused if it gives back a 404.

### ✅ `remove.all`  
*canonical*

```
remove.all from db.TABLE
```
Example:
```mohio
remove.all from db.users
```
Delete every row in a table. `remove all` with a space is a different, guarded form; the dotted `remove.all` is the one that clears a table. There is no undo, so scope it with care.

### ✅ `identifier safety (the relay-identifier blocker)`  
*canonical*

A SQL identifier Mohio emits -- a table, column or index name -- is always a name read from the program's own declarations, matching the grammar's NAME rule (a letter or underscore, then letters, digits or underscores, at most 63 characters). A name that fails that is REFUSED, never altered into one that passes (`quote_ident` refuses rather than strips): `the name '...' is not a name Mohio will put in SQL ... Nothing was run.` A name read from a STORED ROW is never used as an identifier -- it is data, placed through a `?` parameter, so a value can never become a table or column name. This is the relay-identifier blocker (ruled 2026-10-08), a run-time guarantee on every SQL engine; the audit relay likewise delivers only to log names the program declared, never to a name taken from data. Most programs never see the refusal, because declared names already pass; it is the floor under them.


## Web (server listeners)

### ✅ `listen for`  
*canonical*

```
listen for sh.SHAPE at /path
    ...
listen: done   (server submission handler)   |   listen for EVENT on #SELECTOR   (client, MioScript)
```
Example:
```mohio
shape Signup
    who as text
shape: done
listen for sh.Signup at /signup
    require role "user"
    give back [201] "created"
listen: done
```
THE SUBMISSION HANDLER (ruled by Ron 2026-10-07): `listen for sh.X at /path` is the handler for a submission. It answers a POST, binds the body to the lowercase instance, validates it against the shape (a 422 names the offending field), runs the forms guard, and reads fields as `request.field` or the bare `field` -- and its body is ATOMIC: a handler making more than one write commits on a 2xx `give back` and ROLLS BACK on a non-2xx or an exception, with no explicit transaction (a handler with its own `try`/transaction or a `saga` keeps its own boundaries). It stands on its own at the top level, closes `listen: done`, and `at /path` names the route. A pathless `listen for sh.X` BINDS TO ITS PAGE'S OWN LOCATION (ruled 2026-03-30 / 2026-04-03, locked 2026-07-18), which a single-file run answers at `/` (verified: `POST /` returns the body, `POST /Order` is 404). `at /path` is OPTIONAL on a page and REQUIRED in a journey, where each handler names its own route: `journey Shop / listen for sh.Order at /order / give back [201] "made" / listen: done / journey: done` (verified to answer at `/order`). PENDING (6.0.0, handler-path item, build lane in progress): `mio check` does not yet refuse a pathless handler inside a journey -- today it checks clean and serves at the page location (verified by running); the refusal is being added. `new sh.X at /path` was the older spelling for this handler and is now RETIRED: the compiler refuses it and points here (ruled 2026-10-07), so `listen for` is the one submission-handler form. THE CLIENT FORM is different: `listen for EVENT on #SELECTOR` is a browser event listener in MioScript (`listen for click on #buy`, see the MioScript section). The bare server wrapper `listen for` with NO subject is REMOVED -- it registers no route and is refused; handlers stand on their own. Migrate old wrappers with `mio fmt --write <file>`. What `mio check` validates is what `mio serve` routes (ruled 2026-10-08): a declared handler is never silently gone, a check ERROR stops serve from starting (`Build failed -- fix errors before serving`) rather than serving a subset, and two handlers answering the same method and path are refused at check naming both lines (`Two handlers answer GET /p: one at line 4 and one at line 7`).

### ⛔ `new sh.X`  
*retired*

```
new sh.SHAPE at /path ... new: done   -- RETIRED as a handler. Write: listen for sh.SHAPE at /path ... listen: done
```
RETIRED as a handler (ruled 2026-10-07). The compiler REFUSES `new sh.X at /path` and points to the replacement -- `new is not a handler ... -> listen for sh.<Shape> at /path`, with `mio fmt --write` to convert every line. Write `listen for sh.X at /path ... listen: done`; it behaves exactly as `new` did (POST, shape binding, 422, forms guard, atomic multi-write) -- see `listen for`. The word `new` is RESERVED for a future arrival condition inside a handler that acts only on a genuinely new arrival (a double-clicked submit, a game), not built yet (T1-NEW-ARRIVAL-CONDITION). `mio new`, the CLI command that scaffolds an app, is unrelated and unaffected.

### ✅ `request for sh.X`  
*canonical*

```
request for sh.SHAPE [at /path]
    ...
request: done
```
Example:
```mohio
request for sh.Signup
    give back [200] "ok"
request: done
```
A page or data was requested (GET). It is a block with a named closer, standing on its own at the top level (no `listen for` wrapper) -- a bare one-line `request for sh.Signup` is not a route; the block and `request: done` are. A pathless handler BINDS TO ITS PAGE'S OWN LOCATION (ruled 2026-03-30 / 2026-04-03, locked 2026-07-18): the pathless form above answers at the page's address, which a single-file `mio run` / `mio serve` serves at `/` (verified by running -- `GET /` returns the body, `GET /Signup` is 404). `at /path` is OPTIONAL on a page and REQUIRED in a journey, where each handler names its own route -- `request for sh.Acct at /balance`. PENDING (6.0.0, handler-path item, build lane in progress): `mio check` does not yet refuse a pathless handler inside a journey -- today it checks clean and serves at the page location (verified by running); the refusal is being added.

### ✅ `request.field`  
*canonical*

```
request.FIELD
```
Example:
```mohio
action request.action
```
Read inbound request data.

### ✅ `give back (response)`  
*canonical*

```
give back STATUS "..."
```
Example:
```mohio
give back 200 "Unknown action."
```
Send the response. `give back` is a HARD RETURN: it ends the request immediately, so each branch of a `check`/`when` can give back its own response and nothing after the branch that runs executes (verified by running: a handler that gives back inside `when` and `otherwise` never reaches a trailing `give back`, which `mio check` flags as an unreachable statement). Work above the `give back` must finish first. An UNEXPECTED runtime failure (a status of 500 or more that the program did not write) answers the client one plain sentence and a reference -- `Something went wrong on our side, and it has been recorded.` with a `reference` -- and no database text, table name or internal detail; the full detail is logged server-side under that same reference (verified by running). A program's OWN `give back [500] "..."` is unchanged: its message reaches the client as written.

### ✅ `grant role`  
*canonical*

```
grant role "NAME" [for person <who>]
```
Example:
```mohio
grant role "user"
```
Establish a role on the current session, server-side -- the login primitive and the inverse of `end session`. A handler that grants a role (typically a `listen for sh.X` POST at a login route) authenticates the session; `require role "NAME"` then gates other routes and `end session` clears it. The role is set on the server session and is NEVER trusted from a client-supplied value (a client role is refused -- see `grant role from a client value`). Verified over real HTTP: grant at login, a `require role` route returns 200; after `end session` the same session returns 403. OPTIONAL `for person <who>` sets `session.person` at this authentication boundary -- the human the APPLICATION asserts is here (Mohio does not verify the identity; it carries what the app asserts, and a sector requiring a verified subject is unbuilt). Set once per session and read-only: a second `for person` refuses (`session.person is already set and is read-only`), and it survives a session rotation, so a later `grant role` that changes only the ROLE keeps the person. `for person` resolving to empty refuses (`refusing to assert an absent person`). Granting a role with no `for person` behaves exactly as before -- the person is an addition, never a requirement.

### ✅ `session.person / session.agent`  
*canonical*

```
session.person   |   session.agent   (read-only dotted reads)
```
Example:
```mohio
check session.person
    when session.person is empty
        give back [401] "sign in"
check: done
```
The durable principal the session carries. `session.person` is set once by `grant role <role> for person <who>` (see `grant role`) and is READ-ONLY thereafter -- assigning `session.person <value>` outside that boundary refuses (`read-only`). It is readable in the same request and on every later request, survives a session rotation, and is stamped on every audit record automatically with NO syntax. It is what the APPLICATION asserts, not something Mohio verified. THE ABSENT-PERSON RULE, both halves: when no person is set, a COMPARISON on it refuses loudly in BOTH directions (`session.person is not "x"` and `"x" is not session.person` both 500 with `a comparison here involves an absent person ... an absent person must not pass or fail a separation check with nobody there`) -- so a maker-checker test cannot silently pass with nobody there. The SANCTIONED presence check is `when session.person is empty`, which does NOT refuse; check it first and handle the no-person case. `session.agent` is set while an `ai.agent` acts and KEEPS the person (delegation): a record then stamps BOTH the agent and the person it acts for; with no agent, only the person is stamped.

### ✅ `require role`  
*canonical*

```
require role "NAME" [or "NAME"]
```
Example:
```mohio
require role "admin" or "system"
```
Gate a handler on a server-verified role. Signed out -> 401 `authentication_required` (sign in first); signed in WITHOUT a listed role -> 403 that names what is missing (`Role required: admin. You are signed in with user ...`); with a listed role the handler runs. `require role "A" or "B"` passes on either. The role is the server session's, never a client-supplied value. Verified over real HTTP.

### ✅ `require person`  
*canonical*

```
require person
```
Example:
```mohio
require person
```
Gate a handler on someone being signed in -- any `session.person`, whoever they are (ruled 2026-10-08). A request with no signed-in person is answered 401 `authentication_required`, asking them to sign in; with a person present the handler runs (200). It checks only that a person is there, not which role they hold -- for a role use `require role`. Verified over real HTTP: signed out -> 401, and after a `grant role ... for person` login -> 200.

### ⛔ `route`  
*retired*

Use `listen for`.

### ✅ `give ... as download`  
*canonical*

```
give <value> as download ["<filename>"]
```
Example:
```mohio
give "reports/q3.pdf" as download
```
Hands a value to the requester as a file, so the browser saves it instead of displaying it. `give back` answers what was asked for; `give` hands something over. A path written in place names the file from its tail, so no filename is needed. Anything else (a variable, a database field, generated content) has no name of its own and must be given one: give doc.contents as download "invoice.pdf". The filename is an ordinary string, so {{ }} renames in transit. Paths are read from the app folder and the file area only; source, configuration, private (_) files and anything outside are refused.

### ✅ `give ... as image`  
*canonical*

```
give <value> as image ["<filename>"]
```
Example:
```mohio
give "proof.png" as image
```
Hands a value to the requester DISPLAYED INLINE with a checked image content-type -- the sibling of `as download`, which saves the file instead. Displaying a delivery photo is the common case; saving it the exception. BOTH run inside the handler, after `require role` and any record checks, and both record the SAME egress audit (the op distinguishes `display` from `download`), because displaying a protected file crosses the boundary exactly as saving it does. Verified over real HTTP: unauthenticated 403 (audited access_denied, no display egress), authorized 200 with `Content-Type: image/png` and an INLINE (not attachment) disposition, the exact file bytes. The content type is read from the filename first, then by sniffing the leading bytes, so `give bytes as image` with no name still serves a PNG/JPEG/GIF/WEBP. A NON-IMAGE refuses loud pointing at `as download` ("The name ... is not an image type. To hand a non-image file over for the browser to save, use `as download`"); SVG is deliberately `as download`, never inline, because an inline SVG can carry script. RELATED FIX: `give back [200] <raw bytes>` no longer falls through to the JSON path (which serialized the Python bytes repr into a corrupt body) -- it now refuses loud, naming `as image` / `as download`. This landed as a LANGREF-EXEMPT commit: the grammar did not change (`as NAME` already parsed), which means the behaviour and this entry did, not that the documentation was already done.

### 🚧 `require biometric / require camera / require location / require push`  
*not-built*

Device-permission gates: `require biometric`, `require camera`, `require location`, `require push`. Ruled real and planned for the mobile surface, declared but NOT built. Since Batch 35 P2 each REFUSES at check (`require biometric is declared but NOT built yet -- arrives with the mobile surface`) rather than parsing as `require <variable>` and doing nothing. Distinct from `require role`, which is built.

### ✅ `verify signature (webhook)`  
*canonical*

```
verify signature using env.SECRET [header "NAME"] [within N minutes]   (a handler clause)
```
Example:
```mohio
shape Event
    kind as text
shape: done
listen for sh.Event at /webhook
    verify signature using env.WEBHOOK_SECRET header "Stripe-Signature" within 5 minutes
    give back [200] "ok"
listen: done
```
Prove an inbound webhook came from who it claims. It computes HMAC-SHA256 over the RAW request bytes and compares it constant-time against a signature header, failing CLOSED: a bad or missing signature is a 401 before anything binds, reads or writes. The secret must come from the environment (a literal in the page is refused). `header "..."` names the header the signature arrives in (default `X-Signature`); `within N minutes` adds a freshness bound that requires an `X-Timestamp` header folded into the signature. It works on BOTH handler kinds (verified): a `request for sh.X` (GET) webhook and a `new sh.X` (POST) webhook, not only `new`. REPLAY PROTECTION (verified end to end, scoped to a declared `within N minutes`): a validly-signed delivery that is RESENT identically is answered 200 WITHOUT reprocessing the handler, and the repeat is RECORDED to the audit trail as its own event (a replay is an event, not a silent no-op); a resend carrying a DIFFERENT but still-fresh timestamp is a genuinely new delivery and runs. `within N minutes` is the one configurable acceptance window this grammar has, and replay dedup rides on it: a webhook with NO `within` clause has signature verification but no replay dedup. An inbound handler WITHOUT any `verify signature` clause earns the `webhook_unverified` warning.

### ✅ `one-time links (mioauth.token.create / verify token / mioauth.token.revoke)`  
*canonical*

```
mioauth.token.create as NAME
    for VALUE
    scope "..."
    expires in DURATION
    one time
mioauth.token.create: done   |   verify token from SOURCE as NAME
    scope "..."
verify: done   |   mioauth.token.revoke
    for VALUE
    scope "..."
mioauth.token.revoke: done
```
Example:
```mohio
connect db as sqlite from env.DATABASE_URL
shape P
    id as int
shape: done
listen for sh.P at /issue
    require role "user"
    mioauth.token.create as approve_token
        for request.id
        scope "approve:1"
        expires in 1 hour
        one time
    mioauth.token.create: done
    give back [200] approve_token
listen: done
```
A link that acts ONCE, for one purpose, for a while, with only its hash stored (Q774, ruled 2026-10-06; verified at check, and end to end on SQLite in the dev battery). `mioauth.token.create as NAME` issues a 32-byte token and stores ONLY its SHA-256, with `for` (whom/what it acts for), `scope` (its one job), `expires in DURATION` (required) and `one time` (required); the raw token lives only in the `as` name. THE CAPTURE WORD IS `as`: `as` replaced `named`, and `named <name>` as the capture is REFUSED (`named <name> is retired as a capture word. as names what a block captures`). `verify token from SOURCE as grant` (closes `verify: done`, or a bare closer) checks the token on a GET WITHOUT spending it and redeems it ONCE on any other method, in one conditional UPDATE so two simultaneous redemptions cannot both win. `mioauth.token.revoke` revokes every unused matching token by `for` and `scope`. This trio is built; the rest of `mioauth.*` is not (see `mioauth.*`).

### 🚧 `from <connector> (webhook handler)`  
*not-built*

```
from NAME
    when EVENT
        ...
from: done
```
A declared inbound-webhook handler: react to an event a named connector delivers. Declared in the grammar but NOT built in this release, and verified by running it now REFUSES AT CHECK (exit 1), not merely at run: `from <connector> is declared in the grammar but not built in this release. It refuses at run rather than silently do nothing, so this names it before you deploy. To proceed: remove from <connector> for now, or use a built alternative.` (The message still reads `refuses at run`, but the error fires at check, lined up with the AST `from_connector_block` node -- dev-lane test `tests/test_battery_from_connector_check_refusal.py`.) Its sibling standalone handler `change to sh.X` is the same class and also errors at check. To receive webhooks today, use a built `new sh.X` / `request for sh.X` handler and authenticate the sender with `verify signature (webhook)`.

### 🚧 `change to sh.X (stored-record change handler)`  
*not-built*

```
change to sh.X
    ...
change: done
```
A declared standalone handler meant to react to a stored-record change. Declared in the grammar but NOT built in this release, and verified by running it REFUSES AT CHECK (exit 1): `change to sh.X is declared in the grammar but not built in this release. It refuses at run rather than silently do nothing, so this names it before you deploy. To proceed: remove change to sh.X for now, or use a built alternative.` Same class and same treatment as its sibling `from <connector> (webhook handler)`: the AST names the node, so the declared-not-built error fires at check rather than only at run. To act on a record change today, do the work in the `new sh.X` / `request for sh.X` handler that performs the write.

### ✅ `end session`  
*canonical*

```
end session
```
Example:
```mohio
end session
```
Log out: end the whole current session, the inverse of `grant role`. THE LIFECYCLE, verified over real HTTP: an authenticated request returns 200; the logout (`end session`) returns 200 and now CLEARS the cookie -- it sends `mio_session=` with `Max-Age=0`, an empty value that expires at once (verified: the old behaviour re-emitted the same id and is gone). The next request presenting the old id is unauthenticated -- a `require role` route returns 403 -- and the server mints a fresh, ANONYMOUS session with a new cookie value. The old id is also invalidated server-side (the same primitive session expiry uses), so it cannot be resurrected even if replayed. Roles and session contents are gone; a handler that ends a session is an authentication boundary.


## AI primitives

### ✅ `ai.decide`  
*canonical*

```
ai.decide NAME returns TYPE
    confidence above N
    weigh
        ...
    not confident
        ...
ai.decide: done
```
Example:
```mohio
connect db from env.DATABASE_URL
ai.decide is_spam returns boolean
    confidence above 0.85
    weigh
        message.body, sender.history
    ai.audit to spam_audit_log
    not confident
        give back pending "Sent to a human"
    on.failure
        give back [503] "Spam scoring unavailable"
ai.decide: done
```
A governed AI decision. THREE parts are required and `mio check` refuses without them: the `not confident` fallback, an `ai.audit to <log>` line so the decision is recorded, and an `on.failure` handler for when the AI service is unreachable. `ai.` is free. The block also warns until it is INVOKED (put its name on a line) and until it has compliance tests. An upload given to any AI primitive refuses at CHECK: `ai.decide is given request.<field>, an uploaded file. AI primitives read text; to read a document or image use mioai.extract` (mioai.extract is designed, not built). A protected field, or a record carrying one, is refused before the request leaves (the egress gate); send `<record> stripped` to de-identify it first.

### ✅ `ai.rank`  
*canonical*

```
ai.rank NAME [returns TYPE] [for VALUE]
    option VALUE if CONDITION
    default VALUE
    confidence above N
ai.rank: done
```
Example:
```mohio
ai.rank tier returns text
    option "gold" if score is more than 80
    default "bronze"
ai.rank: done
```
Weighted multi-option ranking. Distinct from ai.decide (one typed question).

### ✅ `ai.explain`  
*canonical*

```
ai.explain DECISION as NAME
```
Example:
```mohio
ai.explain is_spam as reason
```
Human-readable explanation of a decision.

### ✅ `ai.create`  
*canonical*

```
ai.create KIND as NAME
    prompt "..."
    ...
ai.create: done   (KIND: text | image | video | audio)
```
Example:
```mohio
ai.create image as poster
    prompt "a red barn at dusk"
ai.create: done
```
Generation. The KIND comes FIRST and `as NAME` binds the result last, the way `as` names a thing everywhere else in Mohio. KIND is text, image, video or audio, and it selects the provider call -- verified with the mock's distinct markers that each modality routes correctly. The old `ai.create NAME returns KIND` and the inline `ai.create NAME KIND` are RETIRED and refuse with a migration message showing `ai.create KIND as NAME` (the inline form silently generated text whatever the kind word said, which is why it was retired). `returns` is UNTOUCHED elsewhere -- a task, `ai.decide` and `ai.rank` still declare their return with it; only ai.create stopped borrowing it. A `limits` block is consumed: `max tokens` reaches the provider and overrides `MOHIO_AI_MAX_TOKENS`, and the call is charged against any `cost ceiling` before it fires; with no `limits` it inherits the journey's ambient ceiling. A ceiling refusal (a pre-flight `cost ceiling` breach or a truncation refusal) reaches the block's OWN `on.failure` and returns what the developer wrote; with none it fails loud with a 500 naming the ceiling. NOT BUILT: a KIND that is not one of the four -- `ai.create story` was designed for a news application under mioai, a kind taking attributes such as length, style and attribution and returning several things (the piece plus a title and a blurb); today it refuses with a generic parse error rather than a declared-and-not-built message, a known gap.

### 🚧 `ai.create story (a KIND, not built)`  
*not-built*

```
ai.create story as NAME   (not built -- arrives with mioai)
```
`ai.create story` is a KIND, not a bare name, and it is NOT built: it refuses as declared-and-not-built, arriving with mioai. It was designed for a news application -- a kind taking attributes such as length, style and attribution and returning several things (the piece plus a title and a blurb). Any word after `ai.create` that is not one of the built kinds (text, image, video, audio) refuses the same way, naming the kinds that exist. Until then use `ai.create text as NAME` and assemble the title and blurb yourself.

### ✅ `ai.connect`  
*canonical*

```
ai.connect NAME
    address "https://host/v1"          // ONE provider, its own endpoint
    key env.KEY
ai.connect: done
--- or a fallback chain of providers ---
ai.connect NAME
    order
        PROVIDER model "..."           // model is REQUIRED on every provider
        PROVIDER model "..."
    order: done
ai.connect: done   (then reference it from an AI verb with `using NAME`)
```
Example:
```mohio
connect db from env.DATABASE_URL
shape Ask
    amount as decimal
shape: done

ai.connect answer
    order
        anthropic model "claude-haiku-4-5-20251001"
        openai    model "gpt-4o"
    order: done
ai.connect: done

listen for sh.Ask at /check
    require role "screener"
    ai.decide verdict returns boolean
        using answer
        confidence above 0.85
        weigh request.amount
        ai.audit to chain_test_audit_log
        not confident
            give back [202] "below threshold"
        on.failure
            give back [503] "no provider answered"
    ai.decide: done
    ai.decide verdict
    give back [200] "verdict=" & verdict
listen: done
```
Declare an AI connection, then reference it from an AI verb (`ai.decide` / `ai.rank` / ...) with `using NAME`. Two shapes: one provider, or a FALLBACK CHAIN. CHAIN (automatic failover): `order` lists providers in priority order, each with a REQUIRED `model "..."`. The chain falls back to the NEXT provider on FAILURE or UNAVAILABILITY of the current one (a raised rate-limit counts); a cost-ceiling breach is a deliberate refusal and does NOT switch providers. Proven end to end over a keyed run: both keys good, the first answers; the first key invalid, it fails over to the second; both invalid, exhaustion reaches the block's `on.failure`. SINGLE PROVIDER, or any provider Mohio does not ship: give the connection an `address` and a `key` directly. `address` takes a string URL, `env.X`, or `secret.X`; `key` takes `env.X` or `secret.X`. Standalone (the connection IS the provider): `ai.connect mine` / `address "https://host/v1"` / `key env.MYKEY` / `ai.connect: done`. Inline in a chain, declare a named custom provider on one line and then name it in `order`: `address "https://host/v1" key env.MYKEY as mine`, then `mine model "m1"` under `order`. BUILT-IN PROVIDERS ship in a table: anthropic, openai, gemini, groq, kimi, qwen, deepseek, perplexity, grok. Name any of them in `order` with a model and no address is needed; give a built-in name your OWN `address` and it OVERRIDES the shipped endpoint (verified: `address ... as openai` checks clean). MODEL IS REQUIRED: Mohio keeps NO per-provider default model, because models change too often to pin one in the compiler, so a provider named with no model is refused at check. THREE CHECK REFUSALS, each run before writing this: an empty `ai.connect` (no address, no order) refuses and names both ways to fill it; an unrecognized provider name refuses and prints the known list plus the declare-your-own form; a provider named with no model refuses and shows the `model "..."` fix. `ai.connect` replaced `ai.chain` (retired). Verified by running: `using NAME` reaches ONLY that connection's provider. An UNREGISTERED chain name is not a silent fallback to the default -- it REFUSES at run, sending nothing: `ai.connect chain 'X' is not registered (no ai.connect block declared it) -- '<verb>' named it with 'using X'`. (The refusal is at run; `mio check` does not yet flag an undeclared chain name.)

### 🚧 `mioai.* (paid)`  
*not-built*

```
mioai.translate ...
```
Advanced and managed AI. UNDERWAY. The free line is `ai.*` -- ai.decide, ai.agent and the rest work today and are not a trial of anything. Whatever mioai adds sits above that. Plans change and nothing here is a commitment to a date.

### 🚧 `ai.compare / ai.override / ai.resolve / ai.respond`  
*not-built*

Declared AI primitives with no handler in this build: each fails loud at run with "has no handler in this build". Use `ai.decide`, `ai.rank`, `ai.explain` or `ai.create`, which are built. Do not rely on these yet.

### ✅ `limits`  
*canonical*

```
limits
    max steps N
    max tokens N
    max cost N
    max time N UNIT
limits: done
```
Example:
```mohio
ai.agent helper
    goal "help"
    limits
        max steps 5
        max tokens 800
    limits: done
    not confident
        give back none
ai.agent: done
```
A resource-ceiling block that sets the AI ceiling in scope. It is valid at TOP LEVEL (Batch 34), inside a `journey ... journey: done` wrapper, or inside `ai.agent` (which requires its own). It holds the AMBIENT ceiling: an AI call with no `limits` of its own INHERITS it, and a call's own `limits` MERGE over it field by field, so no declared ceiling is dropped (verified). A declared `max tokens` reaches the provider and OVERRIDES the `MOHIO_AI_MAX_TOKENS` environment setting rather than being overridden by it (verified: a source ceiling of 40 beats an env of 999). A breach refuses LOUDLY and is audited (`ai_ceiling_refused` on the security trail): a `cost ceiling` is checked BEFORE the call fires, so a call that would exceed it never happens, and a truncated answer is refused rather than returned as complete. Every AI verb inherits the token ceiling: `ai.create`, `ai.agent`, `ai.decide`, `ai.compare`, `ai.respond`, `ai.rank`. DECLARE ONCE FOR THE APPLICATION: a top-level `limits` block in a folder-scoped `journey.mho` sets the ceiling for every page in the folder, because `journey.mho` is prepended to each page, so a page's `ai.create` under it inherits the ceiling (verified). (Before Batch 34 a top-level `limits` failed check with `Unmatched block closer 'limits: done'` and this needed the explicit `journey` wrapper; before Batch 31 a journey `limits` crashed at run and `ai.create` ignored its `limits`; all fixed.)

### ✅ `ai.agent`  
*canonical*

```
ai.agent NAME
    goal "..."
    limits
        max steps N
    limits: done
    not confident
        ...
ai.agent: done
```
Example:
```mohio
ai.agent helper
    goal "help"
    limits
        max steps 5
        max tokens 800
    limits: done
    not confident
        give back none
ai.agent: done
```
Open-core single AI agent. It REQUIRES a `limits` block: `mio check` refuses without one (`ai.agent is missing a limits declaration`). The block is enforced at run, and the agent ALSO inherits a journey-level ceiling, its own `limits` merging over the journey's field by field (Batch 31). THE HYBRID, which a regulated customer asks about precisely: the `cost ceiling` is a genuine PRE-CALL refusal (a call that would exceed it never fires), while the `max tokens` ceiling is a POST-CALL running total across the loop's turns. Give it a `goal`, optional `tools` (granted names) and `memory`. Like `ai.decide` and `ai.create`, `confidence above` is NOT available. `ai.` is free; multi-agent coordination is the commercial `mioagent`. Its run metadata is read through the result words below (`<name>.cost` and siblings).

### ✅ `ai.agent result words (<name>.cost / .steps / .tokens / .error / .explanation)`  
*canonical*

```
<agent-name>.cost | .steps | .tokens | .error | .explanation
```
After an `ai.agent NAME ... ai.agent: done` block runs, its own run metadata is read as registered dotted words on the agent's name: `<name>.cost` (the spend), plus `<name>.steps`, `<name>.tokens`, `<name>.error` and `<name>.explanation`. They are registered the same PAGE_FIELDS-style way `page.*` is, so a typo (`helper.coost`) refuses at check with a teaching message naming the real words, rather than silently reading nothing. REPLACES the internal underscored spelling `_agent_<name>_cost` (and `_steps` / `_tokens` / `_error` / `_explanation`), which is now RETIRED: reading it is refused at check (verified), `'_agent_<name>_cost' is the retired internal spelling for an ai.agent result field -- use '<name>.cost'`, and the same refusal fires inside `{{ }}` interpolation (verified), so a render cannot slip the old spelling through either. Write `<name>.cost`.


## Validation

### ✅ `validate against`  
*canonical*

```
validate NAME against EXPR
```
Example:
```mohio
validate email against "@"
```
Validate a value against an expression.

### ✅ `validate using`  
*canonical*

```
validate using RULE
```
Example:
```mohio
validate using EmailRule
```
Apply a named miovalidate rule. (Also `validate connect RULE`.)

### ✅ `miovalidate (named rules)`  
*canonical*

```
miovalidate NAME
    check FIELD as TYPE [mods]
miovalidate: done
```
Example:
```mohio
miovalidate EmailRule
    check email as text
miovalidate: done
```
Define reusable validation rules.


## Sectors and compliance

### ✅ `sector:`  
*canonical*

```
sector: NAME
```
Example:
```mohio
sector: financial
```
Activate a sector profile. Hierarchical and additive, levels separated by COMMAS: `sector: education, north_carolina, highschool` applies every layer, each level only ADDING rules. The dotted form (`education.north_carolina.highschool`) is RETIRED and fails loud. One per file. A NAME THAT DOES NOT RESOLVE now REFUSES at check and at run, naming three fixes (remove the line, name a sector that exists, or fix the spelling) plus a did-you-mean on a near miss -- it used to warn and run unenforced. A known paid/licensed sector whose profile is not bundled (`financial`, `healthcare`, and siblings) still WARNS and runs unenforced, pointing at the commercial profile or runtime; a bundled community profile loads and runs. Compliance is opt-in by ANY single marker -- a sector like this, a sensitive field tag (`card as text [pci]`), or a `.cm` verb -- and the compliance minimum (an audit chain and a tamper-evident envelope on writes) applies from that one marker on.

### ✅ `security requirements (sector profile block)`  
*canonical*

A `.sector` profile may carry a `security requirements` block naming rules such as `mfa required for ...`, `session timeout maximum ...`, `failed login lockout after N attempts`, `password minimum length ...`, encrypted at rest / in transit, and `api keys rotate every ...`. The loader does NOT yet enforce any of them, so rather than parse a profile clean and silently drop the rule (which it used to -- a profile claiming a 3-attempt lockout gave no lockout at all), a profile naming ANY such rule now REFUSES TO LOAD, at both check and run, naming the exact rule (verified by running): `sector profile '<file>' declares a 'security requirements' rule this loader does not enforce ... <the rule text> ... Remove these lines until enforcement exists, or drop the 'security requirements' block entirely. Use 'require role' for ...`. A profile with no `security requirements` block, or an empty one, is unaffected. This is a profile-authoring block, not something a `.mho` program writes; it is named here so a developer who hits the refusal knows why. Enforcement of these rules is a future build.

### ✅ `compliance:`  
*canonical*

```
compliance: FRAMEWORK
```
Example:
```mohio
connect db from env.DATABASE_URL
compliance: HIPAA
```
Activate a compliance framework's code enforcement. A framework requires append_only-grade audit storage, so the program must connect a durable store (the `connect db from env.DATABASE_URL` line); without one, every audited action refuses at run with `audit.no_durable_store`.

### ✅ `cm.* (app-level)`  
*canonical*

```
cm.retain FIELD for DURATION   |   cm.expire FIELD after DURATION   |   cm.lock NAME   |   cm.purge VALUE (or: cm.purge from db.TABLE)
    match ... / includes ... / preserve ...
    reason "..."
cm.purge: done   |   cm.report "REPORT" for AUTHORITY   |   cm.notify "EVENT"
    to ... / subject ... / message ... / template ... / via ...
```
Example:
```mohio
connect db as sqlite from env.DATABASE_URL
shape Person
    users as table
        ssn as text [phi]
shape: done
cm.retain ssn for 6 years
cm.expire ssn after 7 years
cm.lock users
cm.purge from db.users
    match ssn to "000-00-0000"
    reason "data subject erasure request"
cm.purge: done
```
App-level compliance management, one verb per compliance action. A `cm.` verb is itself a compliance marker: on its own it puts the program inside the guarantee, so its ordinary writes become regulated (audit chain + envelope), the same as a field tag or a `sector:` does. FOUR verbs run on the open runtime. `cm.purge VALUE` (or `cm.purge from db.TABLE`, narrowed by `match` / `includes` / `preserve`) ERASES the matched data and REQUIRES a `reason`, closing with `cm.purge: done`. `cm.retain FIELD for DURATION` records a retention policy and `cm.expire FIELD after DURATION` records an expiry policy: both are DECLARATIVE, each records the policy in the compliance trail and performs no deletion itself, and each earns a check-time warning saying so. `cm.expire` ESPECIALLY deletes nothing when the period ends, it only records the intent, so perform the deletion yourself with `cm.purge` when the period is up. `cm.lock NAME` records a legal hold on the named data. TWO verbs are commercial and REFUSE at check on the open compiler, because they would not actually file or send: `cm.report "..." for AUTHORITY` (managed regulatory filing, e.g. CTR/SAR) and `cm.notify "..."` (managed breach notification, with `to` / `subject` / `message` / `template` / `via`); each names itself at check and points at the commercial runtime rather than appearing to work. A compliance-shaped program that carries NO marker (a write to an audit/evidence-named table, or an audit-named shape field) earns ONE check-time warning that Mohio makes no compliance claim, listing the three markers that opt in.

### ✅ `egress masking (what leaves the app)`  
*canonical*

What may leave the app, verified by running on the egress gate. CLIENT RESPONSE (a `give back` or a `render` to the requester): a field tagged `[phi]` or `[pci]` is masked to its last 4 (`****6789`), and `[pii]` is sent in plaintext. THIRD-PARTY EGRESS (an AI `weigh`, a `miohttp` / `mioconnect` body, an outbound call's arguments): sending a record or field that holds protected data is REFUSED at check -- `the body of miohttp.post would send a record holding protected fields ... Protected data does not leave the app` -- and a loose shape the check cannot place is caught at run before any request leaves. The sanctioned way out is `<record> stripped` (or `weigh <record> stripped`), which sends the record WITHOUT its `[phi]`/`[pci]`/`[pii]` fields (verified: a stripped record reaches a mock endpoint and the AI as `{id, name}`, no tagged field); an untagged or already-masked value passes as itself. SECRETS: a `secret.X` value (or anything made from it) is REFUSED from reaching ANY egress -- a response, a log, a store, or an outbound body -- masked or not, at both check and run (`secret_egress`). A secret is used only as a credential: `auth secret.X` on a call, or `auth bearer secret.X` in a connector's block. To show a secret is set, check that it exists, never show it.

### ✅ `stripped`  
*canonical*

```
<record> stripped        (a value)
weigh <record> stripped   (inside an ai.decide)
```
`stripped` trails a record (an adjective, never a verb) and yields the record WITHOUT its `[phi]`, `[pci]` and `[pii]` fields, keeping the rest. It is the one sanctioned way to send a governed record to an outside call -- an AI `weigh`, a `miohttp`/`mioconnect` body -- which the egress gate otherwise refuses (see `egress masking`). Verified by running: `weigh patient stripped` reaches the AI as `{id, name}` and `body patient stripped` reaches a mock endpoint as `{id, name}`, with the `[phi]`/`[pci]` fields gone. RULED by Ron 2026-10-08.


## App structure

### ✅ `include`  
*canonical*

```
include "file.mho"
```
Static, build-time merge. Carries tasks and top-level setup. (No runnable example here: `mio check` now resolves the included file as it walks the AST, so a standalone `include "tasks.mho"` with no such file beside it cannot be verified in isolation; write it next to the file it pulls in.)

### ✅ `journey`  
*canonical*

```
journey.mho (folder-scoped spine)  |  journey NAME ... journey: done (wrapper)
```
A shared spine: the folder-scoped file `journey.mho` (auto-applied to every page in its folder), or a `journey NAME ... journey: done` wrapper. CASCADE (Batch 36): a page inherits every ancestor `journey.mho` from its own directory UP TO the application root, applied ROOT-FIRST; the nearest declaration wins on restatement and the page overrides them all (last-wins). Everything a journey declares cascades to a subfolder page: the `connect`, the `shape`s, the `task`s, the `sector`, the AI `limits` ceiling, and any file the journey `include`s (verified: a subfolder page runs on the root's connection, shape and task). The application root bounds the upward walk -- `mio serve <dir>` / `mio check <dir>` pass the served directory, and a single-file run uses the working directory, so `mio run api/page.mho` from the app root reaches the root journey. SECTOR TIGHTENS ONLY (a security rule): a subfolder journey may TIGHTEN the sector a parent set (keep it, or add levels under it) and may NOT loosen it; a loosening attempt is REFUSED at check, naming the floor (`... would LOOSEN the sector 'financial' a parent journey already requires`). A subfolder silent on the sector INHERITS it unchanged.

### ✅ `page`  
*canonical*

```
page VALUE   (an ordinary variable/field now)
```
`page` is a FREE ordinary variable and field name (Batch 40): `page 5`, `page as int` (a typed declaration) and `where page is 5` all check clean. FOUR states to keep straight: (1) as above, the bare word is a free variable/field; (2) its dotted namespace `page.header` / `page.footer` is unchanged (a shape field `page.header as text` still works); (3) the page CLASSIFICATIONS `public:` / `private:` / `hidden:` / `authorize:` inside a `journey` wrapper still work (see page classification); (4) the RETIRED `page /home` routing block still gives its migration message (`page is not a Mohio word any more. The page block was removed. A file serves itself now`). So the word is free for your own data without the namespace, the classifications, or the retirement notice going away.

### ✅ `page classification (public / private / hidden / authorize)`  
*canonical*

```
journey NAME
    public: /path
    private: /path
journey: done
```
Example:
```mohio
journey App
    public: /notice
    private: /admin
journey: done
```
Journey-level access classifications for routes (`public:` is the implicit default). They parse ONLY inside a `journey NAME ... journey: done` wrapper. In a bare folder-scoped `journey.mho` (no wrapper) they do NOT parse: `public: /x` fails at check with `Syntax error ... No terminal matches '/'` (verified Batch 36). So the canonical folder `journey.mho`, which is what a developer reaches for, cannot carry them bare today -- use the wrapper form where you need them. Access control today is enforced by `require role` on a route. CASCADE, TIGHTEN-ONLY (verified by running the cascade assembly): a root `journey.mho` cascades its classifications to pages in its subfolders, and a subfolder `journey.mho` may only TIGHTEN a page's classification, NEVER loosen it. Strictness runs public < private < authorize < hidden. A subfolder tightening (`hidden:` under a parent's `authorize:`) is accepted; a loosening (`public:` under a parent's `hidden:`) is REFUSED at assembly, naming the path and the floor: `A subfolder journey may only TIGHTEN a page's classification, never relax it: /secret must stay hidden or something stricter here, not drop to public.` This holds even when a broader subfolder path (`public: /x`) would cover a narrower path a parent already protected, and a subfolder silent on classification inherits the parent's floor unchanged. The same tighten-only rule governs the sector cascade: a subfolder may ADD sector layers (`financial` -> `financial, banking`), never swap to a looser profile (`financial` -> `healthcare` is refused).

### ✅ `social preview (title / describe / image)`  
*canonical*

```
title "..."
describe "..."
image "preview.png"
```
Example:
```mohio
title "Autumn Rates"
describe "Cabin rates for the season"
image "q3-preview.png"
give back [200] "ok"
```
Three top-level declarations that set a page's SOCIAL PREVIEW (verified at check): `title` is the page title, `describe` its one-line description, and `image` the preview image, declared beside the title and description. The runtime writes all three into the `og:` and `twitter:` meta tags the render shell emits, so a link to the page unfurls with a title, summary and image. Emission into the tags is verified end to end by the dev battery `tests/test_battery_social_preview_tags.py`. `image` names the preview image only; it is not an inline `<img>`.


## Modifiers (the dot connector)

### ✅ `dot modifiers`  
*canonical*

```
on.X / as.X / by.X / do.X / to.X / in.X / is.X / not.X / if.X / with.X / from.X / and.X
```
Example:
```mohio
show ("5" as.int)
```
Modifiers attach with a dot, front and back, position carrying the context: `on.failure`, `as.int` (a cast), `db.users` (scope), `on.click` (react) vs `click.on` (perform). The dot joins two words into one unambiguous unit.

### 🚧 `ignore.case / match.case / keep.whitespace`  
*not-built*

Map modifiers on a `map` or miomap alias entry: whether letter case matters when the entry matches an input. By DESIGN a map matches case-insensitively by default (a map entry mostly matches human input, where North and north are the same intent), and `match.case` is the opt-in to case sensitivity; `ignore.case` names the default explicitly. In the SAME breath: none of this is consumed at runtime yet. The modifiers are captured by the transformer but not read by the executor, so today every map entry matches case-sensitively whatever it declares. Do not rely on either yet. `keep.whitespace` is the third modifier in the same slot: it makes leading and trailing whitespace significant when the alias entry matches. It shares the same status, captured but not consumed, so it does nothing at run either.


## MioScript (browser)

### ✅ `listen for EVENT on #id`  
*canonical*

```
listen for EVENT on #ID
    ...
listen: done
```
Example:
```mohio
listen for click on #buy
    notify "added to cart"
listen: done
```
A browser event listener. Name the event and the selector: `listen for click on #btn`. Exactly SIX human-intent words are mapped to DOM events: `typing` -> input, `leaving` -> blur, `entering` -> focus, `hover` -> pointerover, `press` -> pointerdown, `release` -> pointerup. Every OTHER name binds as the RAW DOM event of that name -- an escape hatch, so `click`, `submit`, `change`, `keydown` and the rest work directly. WARNING: a MISSPELLED intent or event name is NOT caught -- `listen for clik on #btn` checks clean and binds a listener for an event that never fires (a silent dead listener), so spell the event exactly. The selector-only form `listen for #sel` (no event named) infers the change event. A `submit` handler AUTOMATICALLY cancels the browser's native submit, so you do not write `prevent default`. Add `on.pause N <unit>` after the selector to debounce. Event data is read implicitly from the listened element: `value`, `key`, `x`, `y`. A dedicated variant, `listen for end of #media` (verified at check), fires when an audio/video element finishes playing.

### ✅ `put / inject`  
*canonical*

```
put LITERAL|VALUE into #SELECTOR   (author literal = markup, runtime value = text)   |   inject result into #SELECTOR   (server-rendered fragment only)
```
Example:
```mohio
put value into #out
```
The boundary: an author's OWN literal is trusted, but data that arrives at RUNTIME never becomes markup. `put` -- an author STRING LITERAL renders as MARKUP (compiles to `_moPutHtml`, innerHTML), because the developer wrote it; a RUNTIME value (a `result` field, or an event datum like `value`) is forced to TEXT (`_moPut`, textContent), so runtime data can never become markup. `inject` is the ONE path where server-rendered markup enters the page: it places markup (`_moInject`, innerHTML) ONLY from a `send` response that carries Mohio's rendered-fragment marker (`X-Mohio-Rendered: fragment`, set by a `render fragment` handler). An author string literal for `inject` is REFUSED at check (its source must be `result`), and a response WITHOUT the marker is refused at RUN TIME (logged to the console, the region left unchanged). So `inject` is not a placement style -- it is server-rendered-markup-in, gated by the marker, and data assembled anywhere but a Mohio render never becomes markup.

### ✅ `mark / unmark`  
*canonical*

```
mark #SEL as STATE | unmark #SEL as STATE | toggle #SEL as STATE
```
Example:
```mohio
mark #email as invalid
```
Toggle a CSS class on the matched elements for a stylesheet to style. `mark` adds the class, `unmark` removes it, `toggle` flips it -- each emits `element.classList.add/remove/toggle("STATE")`, so the STATE name IS the class name. Because of that the name may contain a HYPHEN (`mark #box as is-active` emits `class="is-active"`), the idiomatic CSS spelling, which was impossible until a dedicated terminal was added; `is_active` and `isActive` work too. The hyphen is allowed ONLY here: a variable or alias name with a hyphen is still refused (`a name here cannot contain one`), and `-` is still subtraction inside `(...)`.

### ✅ `send (server bridge)`  
*canonical*

```
send #FORM to "/path"
```
Example:
```mohio
send #form to "/signup"
```
Serialize a form and POST it, with the parsed response in `result` inside `on.success` / `on.failure`; a `send` with branches closes with `send: done`. Step by step (from the emitter): it serializes the named form with the browser's `FormData` and POSTs; it reads the body as TEXT then tries `JSON.parse`, so `result` is the parsed object (`result.field`) when the body is JSON and the raw text otherwise; an HTTP-ok status runs `on.success`, a non-ok status (4xx/5xx) runs `on.failure`, and a NETWORK exception also runs `on.failure` with `result.error` set to the message; when the listener's event is `submit` the native form submit is suppressed automatically (no `prevent default`); and the in-flight `mio-swirl` overlay is cleared on BOTH success and failure, never left spinning. `result` fields render as TEXT through `put`, never markup; to place a server-rendered fragment use `inject result` (see put / inject). ONE FAILURE SHAPE (Batch 40): every failure path -- an HTTP error OR a network exception -- reaches `on.failure` as one normalized `result` carrying `ok` (false), `status`, `message`, `error`, `json` and `kind`, with documented empties (status null when there was no response, message/error "" when none, json {}). The `kind` matches the server's `failure.kind` vocabulary EXACTLY where a status maps 1:1 -- `conflict` reads `conflict` on both sides, likewise `authorization_error` (403), `not_found` (404), `provider_error` (502), `connection_error` (503), `timeout` (504). Two caveats a developer needs: a 500 stays `unknown` because the server maps both a database error and a generic runtime error onto 500, so the client cannot tell them apart from the status alone; and the 400 family collapses to `validation_error` for the same reason -- unless the server names the exact kind in the response body's `code`, which the client then uses. A NETWORK exception carries kind `network` with a null status (a client-only kind); an undeterminable kind is an explicit `unknown`, never blank. NESTED SEND: a `send` may be nested inside an `on.failure` (or `on.success`) branch -- send, and on failure send again to a deterministic fallback -- and it compiles and emits correctly. The inner send has its OWN `on.success` / `on.failure`, and inside the inner branches `result` refers to the INNER send's response (each inner branch re-declares it). The OUTER branch's `result` is SHADOWED inside the inner branches (the inner callback re-declares `result`), so it is NOT reachable there by name -- capture what you need from the outer failure first (for example into a `hold`) before the inner send.

### ✅ `notify / go to / go back / reload / scroll to`  
*canonical*

```
notify ".." | go to "/path" | go back | reload | scroll to #SEL
```
Example:
```mohio
notify "saved"
```
Toast (`notify ".."`), browser navigation (`go to "/path"`, `go back`, `reload`) and `scroll to #sel`. These are the BUILT browser-side navigation forms (verified: each compiles to `window.location` / `history`). The dotted `nav.*` family (`nav to`, `nav.back`, `nav.forward`, `nav.root`) is the SEPARATE mobile navigation, declared but NOT built -- see `nav.*`.

### 🚧 `nav.* (navigation)`  
*not-built*

Client navigation: `nav.back`, `nav.forward`, `nav.root`, and the `navigate.back` / `navigate.root` / `navigate to` / `nav to` spellings, plus `redirect`. Declared but NOT built: `nav.back` fails loud with no handler, and `redirect` parses as an ordinary variable rather than a navigation action. Do not rely on any of these yet.

### ✅ `on.idle`  
*canonical*

```
on.idle DURATION
    ...
on.idle: done
```
Example:
```mohio
on.idle 5 minutes
    notify "still there?"
on.idle: done
```
Fire once the WHOLE PAGE has been quiet for the duration: no clicks, keys or moves. For session timeout, auto-logout, are-you-still-there. Contrast `on.pause`, which debounces one selector on one event.

### ✅ `on.pause`  
*canonical*

```
listen for EVENT on #SEL on.pause DURATION
    ...
listen: done
```
Example:
```mohio
listen for input on #search on.pause 300 ms
    notify "searching"
listen: done
```
A debounce on a client `listen for`: wait until the event has stopped for the duration, then fire once. The classic search-as-you-type pause. Whole-page inactivity is `on.idle` instead.

### 🚧 `on.change / on.complete`  
*not-built*

`on.change` REFUSES at check (Batch 35 P2): it is not a Mohio form, and the change listener needs no event-modifier word, so write the change listener directly. It used to parse silently as an ordinary variable. `on.complete` exists only inside the `invoke` block, which is Phase 3 reserved.

### ✅ `request (GET into a target)`  
*canonical*

```
request "/path" into #SELECTOR
```
Example:
```mohio
listen for click on #load
    request "/items" into "#list"
listen: done
```
A browser GET from inside a listener: it fetches the URL and places the response in the target. It honours the SAME fragment marker as `send` (Batch): a response the Mohio server marked `X-Mohio-Rendered: fragment` is placed as MARKUP (`_moInject`, innerHTML), and anything else -- ordinary text, or any unmarked response -- is placed as TEXT (`_moPut`, `textContent`), exactly as before. So a GET can now receive a server-rendered fragment, which it could not until this. TRUST BOUNDARY: `request` trusts the marker exactly as `send` does; the protection is that it is normally SAME-ORIGIN -- only the application's own Mohio server sets that header, and only on a `render fragment` handler, so an unmarked response can never become markup. A cross-origin server would have to expose the header through CORS to forge it, which is a trust decision the developer makes when they choose that URL. It has NO success/failure branches -- for those, or to POST a form, use `send`.

### ✅ `check / when / otherwise (client)`  
*canonical*

```
check SUBJECT
    when CONDITION
        ...
    otherwise
        ...
check: done
```
Example:
```mohio
listen for input on #email
    check value
        when is not empty
            show "#hint"
        otherwise
            hide "#hint"
    check: done
listen: done
```
Branch inside a listener, the same shape as the server `check` (Batch 27). `when <condition>` runs its block when the condition holds; an optional `otherwise` runs when none did. The subject is a recognized CLIENT VALUE -- `value`, `key`, `x`, `y`, or `result` (inside a send branch) -- not the element id, so `check value` (the listened element's value), not `check email`; an unrecognized subject is refused at check naming the set. The subject may be repeated for readability, and conditions combine with `and` / `or`. A misleading error once made a lane conclude this was impossible; it works.

### ✅ `client conditions (inside check/when)`  
*canonical*

```
is a valid email|url|phone | matches #sel | is empty | is not empty | contains ".." | starts with ".." | ends with ".." | is at least N characters | is at most N characters | is at least N | is at most N | is more than N | is less than N | is ".." | is checked
```
Example:
```mohio
listen for input on #f
    check value
        when is at least 3 characters
            show "#ok"
    check: done
listen: done
```
The conditions a client `check`/`when` accepts, all verified: validity (`is a valid email` / `url` / `phone` / `number` / `password` / `required`), `matches #sel`, `is empty` / `is not empty`, text (`contains` / `starts with` / `ends with` ".."), length (`is at least N characters` / `is at most N characters`), numeric (`is at least N` / `is at most N` / `is more than N` / `is less than N`), equality (`is ".."`), and `is checked`. COMBINING: state the SUBJECT ONCE, then chain further predicates with `and` / `or` -- `when value contains "@" and is at least 6 characters` checks clean, while REPEATING the subject (`... and value is at least 6 characters`) is a SYNTAX ERROR. `is at least` / `is at most` work BOTH here (client-side MioScript validation) AND server-side in a `check`/`when`, where they are the spoken forms for `>=` / `<=` (built on the current tip, verified 2026-10-11); the `N characters` length form works in both places too.

### ✅ `hold (client variable)`  
*canonical*

```
hold NAME = VALUE
```
Example:
```mohio
listen for click on #btn
    hold count = "1"
listen: done
```
A client-side variable held for the page (in `_moState`). Read it where a client value is accepted (`put` / `inject` / `notify` / `append`). There is deliberately NO client arithmetic: MioScript holds and moves values, it does not compute on them -- do the math server-side. `hold` FREEZES the value on the client exactly as it does on the server: a second `hold` for the same name, with nothing having released it, refuses, and the value changes only through the five held-variable operators (see `client held-variable operations`). The value is a client value (a string, or an event datum like `value`), never a bare number. For a value you just reassign, use the fluid bare form (see `client variable (bare assignment)`).

### ✅ `client variable (bare assignment)`  
*canonical*

```
NAME VALUE   (inside a listener)
```
Example:
```mohio
listen for input on #f
    draft value
listen: done
```
A FLUID client variable, set by plain assignment inside a listener (verified at check) -- the bare form the held-variable ruling introduced (it is what `hold` meant before `hold` became a freeze). A fluid client value is changed by REASSIGNING it, never by the five held-variable operators: `release` / `clear` / `forget` / `rename` / `replace` on a fluid value refuse at check (`'<name>' is a fluid client value, not held`). Reach for `hold` instead when you want the value frozen until you deliberately change it. Read it, like any client value, in `put` / `inject` / `notify` / `append`.

### ✅ `client held-variable operations (release / clear / forget / rename / replace)`  
*canonical*

```
release NAME | clear NAME | forget NAME | rename NAME to NEW | replace NAME with VALUE
```
Example:
```mohio
listen for input on #f
    hold saved = value
    replace saved with "x"
listen: done
```
The five state operators, on the client, acting ONLY on a value a `hold` froze somewhere on the page (verified at check): `release NAME` unfreezes it (fluid again), `clear NAME` empties it, `forget NAME` removes it, `rename NAME to NEW` renames it, `replace NAME with VALUE` changes the value in one step. The client mirror of the server rule, and held-ONLY: applied to a FLUID client value each refuses at check (`'<name>' is a fluid client value, not held, so release does not apply`), so a bare client variable is only ever changed by reassignment. DISTINCT from `clear #selector`, which is a DOM clear by CSS selector (a different, pre-existing op): `clear NAME` (a bare held name) empties a held variable, `clear #sel` clears a form field.

### ✅ `page-change actions (client)`  
*canonical*

```
show|hide|enable|disable|toggle|clear|focus|scroll to|remove|select|reset #SEL  |  append VALUE to #SEL
```
Example:
```mohio
listen for click on #btn
    show "#panel"
    hide "#other"
    enable "#save"
listen: done
```
Direct DOM changes a listener can make: `show` / `hide` / `enable` / `disable` a selector; bare `toggle #sel` flips visibility; `clear`, `focus`, `scroll to`, `remove`, `select`, `reset` a selector (`reset #form` clears a form back to its initial values, verified at check); and `toggle the X of #sel between "a" and "b"`. `append VALUE to #sel` adds to what is there (an input's value, or an element's text) and always appends as TEXT -- an author literal and a runtime value both take the text path, so `append` never introduces markup. To set element STATE by name use `mark #sel as NAME` / `unmark #sel as NAME` / `toggle #sel as NAME` -- the intent-refactor replacement for the RETIRED `set attribute` / `set style` / `set html` / `add class` / `remove class`, which are gone (they fail to parse).

### ✅ `validate as (client)`  
*canonical*

```
validate as TYPE
```
Example:
```mohio
listen for blur on #email
    validate as email
listen: done
```
Validate the listened element's value against a named type in the browser. Pairs with the `is a valid <type>` conditions and the client length/number checks. Client-side only. `validate as strict` (verified at check) runs the strict validation mode.

### ✅ `after (one-shot delay)`  
*canonical*

```
after N <unit>
    ...
after: done   (inside a listener)
```
Example:
```mohio
listen for click on #go
    after 2 seconds
        reload
    after: done
listen: done
```
A one-shot browser timeout: run the nested statements once, after the delay, closing with `after: done` (compiles to a single `setTimeout`). Distinct from the two waits: `on.pause` DEBOUNCES an event (it resets every time the event fires) and `on.idle` watches the WHOLE PAGE for inactivity; `after` does neither -- no event, no resetting. Duration is a number and a full unit word with a space (`2 seconds`, `300 ms`), the same format on.pause/on.idle use.

### ✅ `every ... as NAME / stop NAME (repeat timer)`  
*canonical*

```
every N <unit> as NAME
    ...
every: done
...
stop NAME
```
Example:
```mohio
listen for click on #start
    every 5 seconds as ticker
        reload
    every: done
listen: done
```
A repeating browser timer, the counterpart to the one-shot `after`: `every N <unit> as NAME` runs its nested body on an interval (compiles to `setInterval`) and is NAMED so it can be cancelled. `stop NAME` stops that interval (verified at check). Same duration format as `after` / `on.pause`. Distinct from `stop #selector`, which stops MEDIA playback (see `play / pause / stop (media)`). POLLING (ruled 2026-10-06): when an `every` body runs a `request ... into`, it polls POLITELY with NO new syntax -- it pauses while the page is hidden and resumes after, pauses while offline and resumes after, never starts a request while its last one is still running, updates the target only when the content actually changed, and announces a change through an accessible live region (`aria-live="polite"`). A `request ... into` that is NOT inside an `every` is unchanged (no live region, written on every answer).

### ✅ `alert / confirm (browser dialogs)`  
*canonical*

```
alert VALUE   |   confirm VALUE
```
Example:
```mohio
listen for click on #del
    confirm "Delete this item?"
listen: done
```
Native browser dialogs from a listener (verified at check): `alert VALUE` shows a message box; `confirm VALUE` shows an OK/Cancel box. Each takes a value (a string literal or a runtime value). Client-side only.

### ✅ `copy (to clipboard)`  
*canonical*

```
copy #SELECTOR   |   copy VALUE
```
Example:
```mohio
listen for click on #share
    copy "https://example.test/x"
listen: done
```
Copy to the browser clipboard from a listener (verified at check): `copy #selector` copies that element's text/value, and `copy VALUE` copies a string literal or a runtime value. Client-side only.

### ✅ `play / pause / stop (media)`  
*canonical*

```
play #SELECTOR | pause #SELECTOR | stop #SELECTOR
```
Example:
```mohio
listen for click on #go
    play #song
listen: done
```
Control an audio/video element from a listener (verified at check): `play #sel` starts it, `pause #sel` pauses it, and `stop #sel` stops it (pause and rewind). `stop #selector` is MEDIA; `stop NAME` (a bare name, no selector) stops a repeat timer instead (see `every ... as NAME / stop NAME`). Pair with `listen for end of #sel` to react when playback finishes. Client-side only.

### ✅ `share (Web Share / destination links)`  
*canonical*

```
share  |  share #SELECTOR  |  share VALUE   (each optionally `on DESTINATION`)
```
Example:
```mohio
listen for click on #share
    share
listen: done
```
Share from a listener (verified at check). THREE things to share: bare `share` shares the CURRENT PAGE; `share #selector` shares THAT ELEMENT's own link; `share VALUE` shares a given address/string. Each form optionally adds `on DESTINATION` to target one channel: the recognized destinations are `email`, `facebook`, `linkedin`, `reddit`, `telegram`, `twitter`, `whatsapp`, `x` -- an unrecognized one is refused at check, naming the known list. With no `on`, it uses the browser's native Web Share. Client-side only.

### ✅ `row identity (clicked-row key rides on send)`  
*canonical*

```
(automatic) -- a render `{{ each row in rows }}` loop stamps each row; read server-side as request.<loop-variable>
```
When a render loops a list (`{{ each row in rows }}`), each rendered row is stamped with its record KEY (or its 1-based POSITION when the record has no key) and the loop-variable name -- nothing else from the record. When a row is clicked, that identity rides AUTOMATICALLY on any `send` in the handler, the way an event's own data rides along, and the server reads it as `request.<loop-variable>` (marked positional when it is a position). The client is given ONLY the identity, never the record's other fields, so a list row cannot leak data it merely displayed. Verified end to end against a real DOM (jsdom) by the dev battery `tests/test_battery_row_identity.py`.


## Services (mio*) — working today

### ✅ `miohttp.*`  
*canonical*

```
miohttp.get|post|put|delete|patch URL ... [as NAME]
```
Example:
```mohio
miohttp.get "https://api.example.com/x"
```
Outbound HTTP. Built on stdlib; response bound with `as NAME`. The response exposes `result.ok` (true only on 2xx), `result.status`, `result.body` (raw text) and `result.json` (parsed body). `result.json` is parsed on an HTTP error too (4xx/5xx), so a provider's structured error reads as `result.json.error` the same way as on success, while `result.ok` stays false; a non-JSON error body leaves `result.json` as the raw string. A connectivity failure gives `result.ok` false and `result.status` 0.

### ✅ `miomail.send`  
*canonical*

```
miomail.send to X subject Y body Z
```
Example:
```mohio
miomail.send to "a@b.com" subject "Hi" body "Yo"
```
Send email via your configured provider -- SendGrid / Brevo / SMTP, selected by env key (SENDGRID_API_KEY / BREVO_API_KEY / SMTP_HOST), or a labeled mock when none is set. `to` is required (a bare miomail.send fails loud). Declare a named provider with `miomail.with <provider> / key secret.X / miomail.with: done`. miomail.queue and miomail.template are commercial-tier.

### ✅ `miofile.*`  
*canonical*

```
miofile.read|write|delete|exists|move|copy|list PATH
```
Example:
```mohio
miofile.write "notes/hello.txt" "hello mohio"
```
File operations. Every path is confined to the file area (MIOFILE_ROOT, else ./mio_files); an absolute path or one climbing out fails loud.

### ✅ `miofile (storage areas)`  
*canonical*

```
miofile
    local PATH as NAME accept EXT, EXT max size N
miofile: done
```
Example:
```mohio
miofile
    local "uploads" as media accept jpg, png max size 5mb
miofile: done
```
Declares a named storage area. accept and max size govern writes into it, and the destination of copy and move, exactly as they do on a shape field. local and temp areas are open core; cloud areas and the managed lifecycle policies (expires, clean) are commercial.

### ✅ `miocache.*`  
*canonical*

```
miocache.get|set|delete|exists|flush KEY
```
Example:
```mohio
miocache.get "k"
```
Cache reads and deletes work. `miocache.set "k" to "v"` does NOT: the `to` argument form is unwired and check says so, so treat set as not built until that is fixed. `miocache.set KEY VALUE [for N <unit>]` is the WRITE (verified: set then get round-trips the value); `get KEY [default V] [as NAME]` reads it. `set` was the write operation, so a cache you could only read from was the gap.

### ✅ `miolog.*`  
*canonical*

```
miolog.info|warn|error|alert|metric
```
Example:
```mohio
miolog.info "started"
```
Structured logging.

### ✅ `mioconnect`  
*canonical*

```
mioconnect NAME [as ALIAS] / address URL / auth ... / operation OP / path "..." / method VERB [/ sends sh.PAYLOAD] / operation: done / mioconnect: done   (shorthand: mioconnect NAME from env.X). Call: Connector.operation with PAYLOAD as RESULT.
```
BUILT and tested (`tests/test_mioconnect_patterns.py`, `tests/test_agent_tools.py`): the connection declaration, the `Connector.operation with PAYLOAD as RESULT` call, and the agent-tools grant on `ai.agent` all run. Inside an `operation`, `method` is the HTTP verb (`method POST`) and `sends sh.PAYLOAD` names the payload shape -- two different lines, both verified. It also has the field-mapping and middleware layer described under the flow arrows. `timeout N seconds` (ruled 2026-10-05, verified at check) is the real timeout on every call the connector makes: the duration is converted to seconds at check, an operation's OWN `timeout` overrides the connector's, and a call that exceeds it fails into `on.failure` with the failure detail (`failure.provider` = the connector, `failure.stage` = call, `failure.error` naming the timeout). `retry N times` IS executed, on a connectivity failure only. A `webhook` setting is accepted by the grammar but NOT executed, so it refuses at check with a teaching message.

### ✅ `retry (mioconnect)`  
*canonical*

```
retry N times   (a mioconnect declaration clause; total attempts = 1 + N)
```
Example:
```mohio
shape Lookup
    id as text
shape: done
mioconnect catalog
    address env.CATALOG_URL
    timeout 5 seconds
    retry 2 times
    operation fetch
        path "/items"
        method GET
        returns sh.Lookup
    operation: done
    on.failure
        give back [503] "catalog unavailable, try again shortly"
mioconnect: done
```
`retry N times` is a `mioconnect` declaration clause and IS executed (battery-proven); total attempts are 1 + N. It retries ONLY a connectivity failure that PROVES the request never left: a refused connection, or a host name that did not resolve. A request that was DELIVERED and then timed out, was reset, or came back partial is NEVER retried, because the service may already have acted on it and a second attempt could deliver it twice (ruled 2026-10-08); that case goes straight to `on.failure` (or fails loud) with the failure detail. So `retry` is safe on an idempotent read; on a mutating operation (a POST that changes something on the other side) it earns a check-time warning, and the right answer there is to handle the timeout in `on.failure` and look the result up before trying again. Each attempt is bounded by the operation's own `timeout`, or the connector's.


## Services (mio*) — not built yet

### 🚧 `miopdf / mioimage / miosearch / miofile (cloud)`  
*not-built*

Parse but do not execute yet (fail loud or no-op). Do not rely on them.

### 🔒 `mioagent`  
*reserved*

Multi-agent harness; reserved, separate commercial track.

### 🚧 `miotest.*`  
*not-built*

```
miotest.unit ...
```
Built-in testing. UNDERWAY. Parses and passes check today, but there is no executor yet, so it fails at run. Do not write tests against it. Plans change and nothing here is a commitment to a date.

### 🚧 `miosms / miostream / miosys / mioenv`  
*not-built*

```
varies
```
Sms, streaming, system and environment helpers. PLANNED, not built -- each is declared in the grammar and none is wired, so a call fails loud at check. For environment values use `env.NAME`, which works today and is free. Mohio's line is the same everywhere: the basics a developer needs to build and ship securely are free; advanced and managed tooling is commercial. Plans change and nothing here is a commitment to a date.

### 🚧 `mioschedule.*`  
*not-built*

```
mioschedule.at|every|in|cancel
```
Scheduled work. UNDERWAY. Named schedule declarations parse and validate; the verbs are not wired, so `mioschedule.every` fails loud at check. Plans change and nothing here is a commitment to a date.

### 🚧 `mioimage.*`  
*not-built*

Image operations (compress, convert, crop, resize, watermark). Declared in the grammar but NOT built in this release: a call fails loud with a not-built message. Do not rely on it yet.

### 🚧 `miostream.*`  
*not-built*

Streaming (open, close, send). Declared in the grammar but NOT built in this release: a call fails loud with a not-built message. Do not rely on it yet.

### 🚧 `miosys.*`  
*not-built*

System calls (run, java). Declared in the grammar but NOT built in this release: a call fails loud with a not-built message. Do not rely on it yet.

### 🚧 `miopublish.*`  
*not-built*

Guaranteed publish/delivery. Declared in the grammar but NOT built in this release: a call fails loud with a not-built message. Do not rely on it yet.

### 🚧 `sec.*`  
*not-built*

Security operations (audit, classify, encrypt, headers, nohardcode, validate). Declared in the grammar but NOT built in this release: a call fails loud with a not-built message. Do not rely on it yet.

### ✅ `run mioschedule`  
*canonical*

```
run mioschedule.<name> now
```
Example:
```mohio
task ping
    give back "pong"
task: done

mioschedule cleanup
    every 1 day
    run ping
mioschedule: done

run mioschedule.cleanup now
```
Run a named schedule immediately: `run mioschedule.<name> now`. Verified by running: with the schedule declared as a block (`mioschedule <name>` / `every <duration>` / `run <task>` / `mioschedule: done`), `run mioschedule.<name> now` registers the schedule and invokes its tasks -- a `run ping` task returning "pong" was registered and returned `pong`. The recurring firing over time is a background concern a one-shot `mio run` cannot exercise; the immediate `now` trigger is what this form does and it works. Separate and still NOT built: the dotted verbs `mioschedule.every` / `.at` / `.in` / `.cancel`, which fail loud at check (see `mioschedule.*`).

### 🚧 `connect chain / on chain / sign via`  
*not-built*

These words exist only inside the `miochain` blockchain block (`chain`, the `on chain` ai.audit modifier, and `sign via miovault`). The miochain block rules are defined in the grammar but referenced by no other rule, so no program can reach them: each fails loud as a syntax error or falls through to an ordinary variable in any position a developer can actually write. Not usable today.


## Retired — do not use

### ⛔ `make`  
*retired*

Use `create`.

### ⛔ `consider`  
*retired*

Use `check`.

### ⛔ `catch`  
*retired*

Use `on.failure`.

### ⛔ `delete`  
*retired*

Use `remove`.

### ⛔ `area`  
*retired*

Use `section`.

### 🔒 `emit / process`  
*reserved*

Reserved.

### ⛔ `request outbound`  
*retired*

Use miohttp.* or mioconnect.

### ⛔ `on.error`  
*retired*

Retired. It meant what `on.success` used to, back when success meant returned values rather than errors, and it is superseded. For a broken operation use `on.failure`; for a result that came back empty use `when` / `otherwise`. The compiler already refuses `on.error` with this pointer.

### ⛔ `case.no / case.yes`  
*retired*

Retired earlier names for the map case modifiers, renamed 2026-08-04: `case.no` is now `ignore.case`, `case.yes` is now `match.case`. One job under two names, not two jobs. Use the current names.


## Text and data operations

### ✅ `as list`  
*canonical*

```
NAME as list A, B, C
```
Example:
```mohio
colors as list "red", "green", "blue"
```
Declare a list. Inline populated: `colors as list "red", "green"` (comma-separated values). Empty and growable: `errors as list text` (a bare type word gives an empty typed list). The old `[a, b, c]` bracket literal is RETIRED -- brackets are for field tags ([phi]) and facets. Grow with `add`.

### ✅ `create list`  
*canonical*

```
create list NAME
    ITEM
create: done
```
Example:
```mohio
create list colors
    "red"
    "blue"
create: done
```
Build a populated list from pieces (block form) -- for many items or readability. `create list` makes a list; `create NAME` (no `list`) makes an object. Iterate with `repeat each`, grow with `add`.

### ✅ `add`  
*canonical*

```
add VALUE to LIST
```
Example:
```mohio
add "blue" to colors
```
The list-grow verb: append an element to the end of a list. Lists ONLY -- `add` to a non-list fails loud, pointing to append/prepend for strings. Preserves order and duplicates. There is no in-place removal counterpart yet: the inline `remove X from <list>` is refused (`remove` opens a block, not a variable name), and the block form (`remove from <list>` / `where ...` / `remove: done`) checks clean but fails loud at run (`remove over a list isn't built yet`, tracked as the T1-QUERY-HELD follow-on). To drop an element today, FILTER into a new list -- `find kept in LIST` / `where value is not X` / `find: done` -- or connect a database to remove rows.

### ✅ `list access (first / last / position / pos)`  
*canonical*

```
LIST.first  |  LIST.last  |  LIST.position.N  |  LIST.pos.N  |  LIST.count
```
Example:
```mohio
colors as list "red", "green", "blue"
show colors.position.2
```
Read a list element. `position` is canonical, `pos` the shorthand; both are 1-BASED (`colors.position.1` is the first element). `.first` and `.last` are the ends; `.count` is the length.

### ✅ `append`  
*canonical*

```
append VALUE to NAME
```
Example:
```mohio
append ".pdf" to filename
```
Append to the END of a string (concatenate) or a list (add an element). For growing a list, `add` is the canonical verb; `append`/`prepend` also build strings.

### ✅ `prepend`  
*canonical*

```
prepend VALUE to NAME
```
Example:
```mohio
prepend "TXN-" to reference_number
```
Add to the FRONT of a string (concatenate) or a list (add an element at the front).

### ✅ `encode`  
*canonical*

```
encode VALUE as FORMAT
```
Example:
```mohio
data "hello"
encode data as base64
show data
```
Encode a value (base64, etc.).

### ✅ `decode`  
*canonical*

```
decode VALUE from FORMAT
```
Example:
```mohio
data "aGVsbG8="
decode data from base64 as plain
show plain
```
Decode a value.

### ✅ `hash`  
*canonical*

```
hash
    FIELD ...
hash: done
```
Hash block. Status fields configure the hash.

### ✅ `extract`  
*canonical*

```
extract from SOURCE using RULE
```
Extract structured data from a source via a named rule.

### ✅ `truncate.to`  
*canonical*

```
truncate.to N words
```
Truncate text to N words.

### ✅ `mask.all`  
*canonical*

```
VALUE mask.all except last N
```
Example:
```mohio
card "4111111111111111"
masked card mask.all except last 4
show masked
```
Mask all but the last/first N characters.

### ✅ `trim (trim.front / trim.back)`  
*canonical*

```
(VALUE trim.front)   |   (VALUE trim.back)   |   (VALUE trim)
```
Example:
```mohio
name " Bo "
show (name trim.front)
```
Trim whitespace from one edge: `trim.front` (leading), `trim.back` (trailing). Bare `trim` removes both.

### ✅ `pad (pad.left / pad.right)`  
*canonical*

```
(VALUE pad.left to N with "C")   |   (VALUE pad.right to N with "C")
```
Example:
```mohio
ref "5"
show (ref pad.left to 3 with "0")
```
Pad a value to width N with a fill character. `pad.left` for leading fill (`005`), `pad.right` for trailing.

### ✅ `remove (remove.html / remove.ws / remove.special)`  
*canonical*

```
(VALUE remove.html)   |   (VALUE remove.ws)   |   (VALUE remove.special)
```
Example:
```mohio
raw "<b>hi</b>"
show (raw remove.html)
```
Strip characters from text: `remove.html` (tags), `remove.ws` (all whitespace), `remove.special` (non-alphanumeric). Each returns the cleaned value.

### ✅ `as.json`  
*canonical*

```
(VALUE as.json)
```
Example:
```mohio
count "5"
show (count as.json)
```
Serialize a value to its JSON form. A quoted string comes back quoted (`"5"`).

### ✅ `unique.id`  
*canonical*

```
NAME unique.id
```
Example:
```mohio
ref unique.id
show ref
```
Generate a short unique id (hex, no dashes). For the full dashed form use `uuid()`.

### ✅ `as.title / as.sentence`  
*canonical*

```
(VALUE as.title)   |   (VALUE as.sentence)
```
Example:
```mohio
name "hello world"
show (name as.title)
```
String case casts. `as.title` is Title Case, every word capitalized ("Hello World"); `as.sentence` is Sentence case, the first word only ("Hello world"). The cast form `value as.title` is the working form; the standalone `as.title value` is not wired.

### 🚧 `in.* (unit / currency stage transform)`  
*not-built*

```
source.field -> dest.field in.UNIT   (inside a map data chain)
```
Meant to convert a value to a unit or currency: `in.USD`, `in.km`, `in.celsius`, and the rest. Not built, and the failure differs by context. In a value or cast position (`converted distance in.miles`) it fails loud at run: `in.miles has no handler in this build`. In a MAP DATA CHAIN (`source -> dest in.miles`) it is worse and SILENT: the transform is dropped, the run succeeds, and the value passes through unconverted (100 km stored as 100). Do not rely on it yet.

### 🚧 `apply.* / show.* (not built)`  
*not-built*

Declared string/data modifiers with no executor in this build: `apply.as`, `apply.in`, `show.as`, `show.to`, `show.with`. Each parses and checks but does nothing at run. Do not rely on them yet. (`with`, `with.required` and `with.all` are NOT in this list -- they are the native JOIN forms and are built; see `with (left join)`, `with.required (inner join)` and `with.all (full outer join)` in the Data section.)

### 🚧 `as.csv / as.pdf / as.html (not built)`  
*not-built*

Serialization casts declared in the grammar but NOT built: each checks clean, then at run says to serialize in application logic for now. Do not rely on them yet.

### ⛔ `release.now / release.lock (retired)`  
*retired*

Both RETIRED, and both refuse at CHECK and at RUN (verified by running): `` `release.now` is retired -- a one-step release-and-reassign reads like a second `set`, the same drift the five variable-state operators exist to remove `` (and `release.lock` gives the matching message). A one-step release-and-reassign is the exact drift `set`'s retirement closed, so it is gone. To change a held value, do it in the open: `release NAME`, then restate the variable. (Earlier `release.lock` was partly wired and silently dropped its value; that is gone now -- it simply refuses.)


## Math and aggregation

### ✅ `math and operators`  
*canonical*

```
math inside ( ):  + - * / %   |   date offset outside ( ):  now - 2 days   |   join text:  &   |   flow:  -> <- <->
```
Example:
```mohio
a 7
b 3
show (a + b)
show (a * b - 1)
show (a / b)
show (a % b)
greeting "Bo"
show ("hi " & greeting & ", total " & a)
cutoff now - 2 days
show cutoff
```
Arithmetic lives INSIDE comparison/math grouping parentheses: `+`, `-`, `*`, `/`, and `%` (remainder). The ONE exception written OUTSIDE parentheses is a DATE OFFSET -- a number and a time unit applied to a temporal anchor, `now - 2 days` or `last_month - 3 days` (verified by running: `now - 2 days` yields the datetime two days back). To JOIN TEXT, `&` is the only operator (`"hi " & name`), and it coerces a number to its text form; `+` never joins text. `->`, `<-` and `<->` are FLOW arrows only (mapping, language flow, the cosmetic check/otherwise separator) and are never math. EQUALITY inside `( )` is a single `=`, arriving with the build that makes it evaluate; until it lands, compare with the word form `is`.

### ✅ `absolute`  
*canonical*

```
absolute VALUE as NAME
```
Example:
```mohio
absolute -5 as size
```
Absolute value. `absolute -5` is 5. The dotted form `-5 as.absolute` is an alias; prefer the word `absolute`.

### ✅ `calculate`  
*canonical*

```
calculate
    NAME FUNC
calculate: done
```
Aggregation over a query's rows. BUILT: `running_sum`, `moving_average`, `rank`, `std_deviation`, `variance`, `percentile` (`calc NAME field.running_sum`). The analytic DIFFERENCE family is declared but NOT built and refuses at check: `difference` (the `a - b` form), `p_value`, `cohens_d`, `percentage_of` each say `calculate cannot compute <fn> yet ... declared but NOT built`, rather than 500ing at run.

### ✅ `summarize`  
*canonical*

```
summarize
    NAME FUNC
summarize: done
```
Summary aggregation block.

### ✅ `average / maximum / minimum`  
*canonical*

```
FIELD.average | FIELD.maximum | FIELD.minimum
```
Aggregate functions on a field in a return/calc context.

### ✅ `precision`  
*canonical*

```
precision N
```
Decimal precision modifier.

### ✅ `% (modulo)`  
*canonical*

```
(A % B)
```
Example:
```mohio
show (10 % 3)
```
Remainder after division, inside math grouping parentheses. `(10 % 3)` is 1. A readable way to test parity or cycle a value.

### ✅ `round.to / round.up / round.down`  
*canonical*

```
(VALUE round.to N) | (VALUE round.up [N]) | (VALUE round.down [N])
```
Example:
```mohio
show (10.555 round.to 2)
```
Round a number, ruled 2026-10-05, computed EXACTLY on a Decimal (not a float). `round.to N` rounds to N decimal places with ties HALF AWAY FROM ZERO (verified by running: `10.555 round.to 2` is 10.56, `2.675 round.to 2` is 2.68, and the negatives mirror -- `-2.675 round.to 2` is -2.68; the old float path gave 2.67). `round.to 0` rounds to ZERO decimal places, a whole number (`5.5 round.to 0` is 6). `round.up` goes AWAY from zero and `round.down` goes TOWARD zero (verified: `-2.3 round.up` is -3 and `-2.3 round.down` is -2; `2.3 round.up` is 3 and `2.3 round.down` is 2) -- so on a NEGATIVE they are not floor/ceiling, they mirror. `round.up` and `round.down` ALSO take a places count (ruled 2026-10-06, day 2): `round.up N` / `round.down N` round to N decimal places in the same direction (verified: `2.341 round.up 2` is 2.35, `2.349 round.down 2` is 2.34, `-2.341 round.up 2` is -2.35); with no number they round to zero places. For a fixed print width use `precision`.

### ✅ `exact numbers`  
*canonical*

```
(any arithmetic)
```
Example:
```mohio
show (0.1 + 0.2)
```
Every number is EXACT, on every engine (the Decimal root fix, ruled 2026-10-05). Numbers become exact where they enter -- program literals, form and query values, JSON bodies, AI and connector responses, and database reads -- and stay exact, with no float drift; outgoing JSON writes them as exact numbers. Verified by running: `0.1 + 0.2` is `0.3` (not 0.30000000000000004), a thousand additions of `0.01` is exactly `10.00`, and `10.555 round.to 2` is `10.56` -- each computed, SAVED to a declared column, and read back unchanged. A value saved to a declared-scale column keeps that scale exactly (see `typed storage`); money follows the currency's scale (see `currency types (money)`).


## Control flow (more)

### ✅ `stop`  
*canonical*

```
stop [NAME] [when CONDITION]
```
Example:
```mohio
stop
```
Exit a loop.

### ✅ `skip`  
*canonical*

```
skip [when CONDITION]
```
Example:
```mohio
skip
```
Skip to the next iteration.

### ✅ `halt`  
*canonical*

```
halt
```
Example:
```mohio
halt
```
Stop execution of the page.

### ✅ `jump`  
*canonical*

```
jump to PATH
```
Example:
```mohio
jump to "/home"
```
Jump to a path or anchor.

### 🚧 `redirect (not built)`  
*not-built*

```
redirect "/path"   (refused -- use jump to)
```
`redirect` is declared but NOT BUILT and REFUSES at check: `redirect is declared but NOT BUILT, so it would do nothing at all -- refusing rather than letting a redirect silently not happen. Use jump to for a redirect after a POST` (backlog T1-REDIRECT-UNBUILT). Use `jump to "/path"` instead, which is built and answers a 303. This is a DIFFERENT thing from the map-level `route as redirect` inside a `map` block, which IS built (Batch 18) and declares its redirect type (`"/old" to "/new" as permanent`) -- that is unaffected.

### ✅ `then`  
*canonical*

```
UNIT then UNIT then ...
```
Result threading: chain actions, each feeding the next.

### ⚠️ `undo`  
*non-canonical*

```
step NAME
    ...
    undo
        ...
step: done
```
Currently ACCEPTED as an alias for `compensate` inside a saga step: it parses, checks and runs, `mio fmt` does not rewrite it, and no warning fires today. Whether `undo` or `compensate` is the canonical word is an open design question, because they are not the same idea: `compensate` records a new counteracting action and keeps the history, while `undo` reads as erasure. Prefer `compensate` until this is ruled.

### 🚧 `do.* (do.once / do.every / do.after / do.unless / do.encrypt)`  
*not-built*

Scheduling and conditional action modifiers. Declared in the grammar but NOT built: `do.every` / `do.after` name work for an external driver that does not run here, and `do.once` / `do.unless` / `do.encrypt` fail loud with no executor. Do not rely on them yet.


## Comparisons and time

### ✅ `below / within / since / is not after / is not before`  
*canonical*

```
below N | within DURATION | since TIME | FIELD is not after DATE | FIELD is not before DATE
```
Comparison and time qualifiers used in conditions and ranges. The inclusive date-boundary forms, verified at check in a `where` clause: `FIELD is not after DATE` matches on-or-before (through the end of that day), and `FIELD is not before DATE` matches on-or-after. They are the inclusive siblings of `is after` / `is before`, and read as the plain-English negation a reviewer expects (`is not after 2020-01-01` keeps everything up to and including that day).

### ✅ `newer / older`  
*canonical*

```
newer than TIME | older than TIME
```
Time comparison qualifiers.

### ✅ `time constants`  
*canonical*

```
today | yesterday | this_week | this_month | this_quarter | this_year | last_week | last_month | last_quarter | last_year
```
Built-in relative time values for date conditions.

### ✅ `comparison operators (!= <= >= ==)`  
*canonical*

```
symbols inside ( ):  when (A != B)   // <= >= == < > too   |   spoken in check/when:  when X is at least N  /  is at most N   |   length:  when TEXT is at least N characters  /  is at most N characters
```
Example:
```mohio
score 7
check score
    when score is at least 5
        show "pass"
    otherwise
        show "low"
check: done
name "Aria"
check name
    when name is at least 3 characters
        show "ok name"
check: done
```
The symbolic comparisons live inside comparison grouping parentheses: `!=` (not equal), `<=`, `>=`, `==`, `<`, `>`. In a `check`/`when` the SPOKEN forms are: `is less than` / `is more than` (and `is below` / `is above`) for `<` / `>`, and `is at least N` / `is at most N` for `>=` / `<=`. The `is at least` / `is at most` server-side forms WORK on the current tip (verified by running 2026-10-11: `score 7` matches `is at least 5` and not `is at most 5`); the earlier refusal that sent them to client-side-only is gone. A LENGTH form compares a text's character count: `is at least N characters` / `is at most N characters` (verified: a four-letter name matches `is at least 3 characters`, not `is at most 3 characters`). The same spoken forms also exist client-side inside a MioScript `validate` block.

### ✅ `now() / uuid()`  
*canonical*

```
now()   // current datetime
uuid()  // new unique id
```
Example:
```mohio
created_at now()
id uuid()
show created_at
show id
```
The producing call forms: `now()` builds the current datetime, `uuid()` builds a new unique id. Both belong in a value position (a field, a default). Bare `now` names the moment only in a temporal context (`now - 2 days`). Bare `now` as a VALUE is now refused AT CHECK (verified: `x now` no longer merely 500s at run, it fails `mio check` with `` `now` on its own is not a value. Write `now()` to produce the current moment.``); bare `uuid` alone is refused the same way. In a TEMPORAL position bare `now` is the live anchor (verified at check): a timespan may end `until now`, and a query may filter `where <field> is after now` or `since now` -- `now` resolves to the current moment there, so these need no `now()`.

### ✅ `as.days / as.hours / as.minutes / as.seconds / as.weeks`  
*canonical*

```
(ISO_DATETIME as.days)   // also as.hours / as.minutes / as.seconds / as.weeks
```
Example:
```mohio
t "2020-01-01T00:00:00"
show (t as.days)
```
Elapsed time since a stored ISO datetime, in the named unit. `"2020-01-01T00:00:00" as.days` is the whole number of days from that moment to now. The value must be an ISO-8601 datetime string; a bare number is refused with a pointer to that form.


## Literals

### ✅ `null`  
*canonical*

```
null
```
Example:
```mohio
nickname null
```
Null literal.

### ✅ `none`  
*canonical*

```
none
```
Example:
```mohio
nickname none
```
None literal.

### ✅ `true / false`  
*canonical*

```
NAME true   |   NAME false
```
Example:
```mohio
flag false
check flag
    when flag
        show "on"
    otherwise
        show "off"
check: done
```
The boolean literals. A bare `when flag` is true when the value is true. Store one with `flag true` or `flag false`.


## Data (more)

### ✅ `grab`  
*canonical*

```
grab NAME from db.X
    match KEY to VALUE
grab: done
```
Example:
```mohio
grab cfg from db.config
    match key to "theme"
grab: done
```
Quick single-key read.

### ✅ `pull`  
*canonical*

```
pull up to N [random] from db.X
pull: done
```
Example:
```mohio
pull up to 3 from db.cards
pull: done
```
Pull up to N rows, optionally random.

### ✅ `cursor`  
*canonical*

```
cursor from FIELD
```
Cursor-based pagination is declared but NOT wired: `cursor from ...` refuses at check (`cursor pagination ... is declared but not wired, so it 500s at run`) and points at offset pagination. Use `paginate by N` today.

### ✅ `paginate`  
*canonical*

```
paginate by N | paginate by VALUE | paginate by VALUE default N
```
Example:
```mohio
connect db as sqlite from env.DATABASE_URL
find r in db.items
    paginate by 2
find: done
```
Page a query's results. `paginate by N` takes a whole number of 1 or more, and since Batch 38 also a VARIABLE or expression (`paginate by page_number`). An INVALID value -- not a whole number, or below 1 -- FAILS LOUD at run (`400 validation_error: ... paginate by needs a whole number of 1 or more`), naming the line; the old silent page-one fallback is gone. A MISSING value takes the clause's own `default`: `paginate by request.page default 1` (ruled 2026-09-22, grammar landed 2026-10-05, verified at check) uses 1 when `?page=` is absent, while an invalid page is still refused, never clamped. Pairs with `limit`, `skip` and `cursor`. The paged RESULT is read through `rows.page.*`: `current`, `total`, `more`, `count`, `size`, `next`, `prev`, `cursor` (plus `rows.page.unmatched.count` / `.start` on a `with.all` join). THREE snake_case spellings are RETIRED and refuse at check with a teaching message naming the replacement (verified): `page.has_more` -> `page.more`, `page.per_page` -> `page.size`, `page.next_cursor` -> `page.cursor`.

### ✅ `rate limit`  
*canonical*

```
rate limit N per UNIT
```
Rate limiting as a language primitive.

### ✅ `random.*`  
*canonical*

```
random.uuid | random.number | random.token | random.hex | random.color
```
Example:
```mohio
id random.uuid
```
Random generators.

### ✅ `readonly / writeonly / readwrite`  
*canonical*

```
connect ... readonly | writeonly | readwrite
```
Connection access modes on connect.


## Real-time and messaging

### 🚧 `broadcast`  
*not-built*

```
broadcast to room VALUE [except WHO]
```
Broadcast to a room (WebSocket). Declared in the grammar but NOT built in this release: `mio check` refuses it with a not-built message rather than letting it silently do nothing. Do not rely on it yet.

### ✅ `connection`  
*canonical*

```
connection at /path
    on.open ...
connection: done
```
WebSocket listener. on.open / on.close / while.active.

### ✅ `room / except / waiting`  
*canonical*

```
room | except | waiting
```
Real-time qualifiers used with broadcast and connections.


## Workflow and reliability

### ✅ `saga`  
*canonical*

```
saga NAME
    step NAME
        ...
    step: done
saga: done
```
Example:
```mohio
saga checkout
    step pay
        show "pay"
    step: done
saga: done
```
A multi-step saga with developer-defined per-step compensation, rolled back in reverse order, resolving to one of COMMITTED, COMPENSATED or FAILED_COMPENSATION. Saga is WITHIN-PROCESS compensation only: it does not survive a crash, a deploy, or process death, and a saga interrupted mid-flight leaves its completed steps applied and does not resume. A step's RESPONSE decides what is kept (HC-127, the saga half, ruled 2026-10-08): a 2xx or 3xx completes the step and keeps its work; a 4xx or 5xx -- including a `give back [409]` -- FAILS the step, runs that step's own `on.failure`, compensates the completed steps before it in reverse, and returns the refusal the author wrote. Verified by running: a saga whose second step answers `[409]` leaves the first step's reservation table empty (its compensation ran) and the caller gets the 409.

### ✅ `step`  
*canonical*

```
step NAME
    ...
step: done
```
Example:
```mohio
step pay
    show "pay"
step: done
```
A step within a saga; may carry a compensate handler.

### ✅ `compensate`  
*canonical*

```
compensate
    ...
```
Compensation handler inside a step (rollback).

### 🚧 `rerun`  
*not-built*

```
rerun NAME with VALUE
```
Re-run a job with a delay, a count, or a condition (rerun.after / rerun.max / rerun.until). Declared in the grammar but NOT built in this release: it parses and checks, then fails loud at run with a no-executor message. Backlogged to build later. Do not rely on it yet.

### ✅ `checkpoint`  
*canonical*

```
debug.checkpoint "label"
    ...
```
Debug checkpoint with logging.

### ✅ `timespan`  
*canonical*

```
timespan NAME
timespan: done
```
Example:
```mohio
timespan window
timespan: done
```
Declare a named time window.


## Testing (more)

### ✅ `it`  
*canonical*

```
it "what this proves"
    expect ...
it: done
```
Example:
```mohio
total 3
it "totals add up"
    expect total is 3
it: done
```
The named test case Mohio runs. `mio test FILE.mho` runs every `it` case in the file IN PROCESS, each in its own child scope so one case cannot leak a value into the next, and reports a pass or the failing diff per case; a file with NO `it` case is a FAILURE, not a pass, and any failure exits non-zero. It runs in ANY `.mho` file, not only a `.test.mho` one. Assert with `expect` (see its entry). To test a task, capture its return with `call NAME as X` (the task must declare `returns TYPE`) then `expect X is ...`. There is no served-request assertion today: `expect page` and `expect response status` are among the unwired forms. Separately, `.test.mho` is the naming a GOVERNANCE control looks for -- an `ai.decide` program warns at check time unless a companion `<name>.test.mho` or `ai.test.mho` carries real assertions.

### 🚧 `mock`  
*not-built*

```
mock sh.SHAPE
    ...
mock: done
```
Mock a shape or dependency in a test case. Declared in the grammar but NOT built in this release: it parses and checks (with a not-executable note), then fails at run with a no-executor message and fails under mio test as well, so it executes nowhere. Backlogged to build later, and it flips back to canonical when the executor lands. Do not rely on it yet.

### 🚧 `seed`  
*not-built*

```
seed from db.X
```
Seeds test data. It is only valid inside a `miotest.unit` block, and miotest has no executor in this release, so it cannot run. The reference showed the line on its own, which does not compile.

### ✅ `expect`  
*canonical*

```
expect VALUE is VALUE
```
Example:
```mohio
total 3
expect total is 3
```
A test assertion inside an `it` case, evaluated by `mio test` (it runs in any `.mho` file, not only a `.test.mho` one). Wired forms this build EVALUATES: `is`, `is not`, `above`, `below`, `contains`; `fallback was used` / `human review is required` (the ai.decide governance assertions); `log entry in NAME` / `no log entry in NAME`; and `db.TABLE contains VALUE`. Every other grammar form (`page`, `response status`, `email sent`, `file`, `database queried`, `cache hit`, ...) is real syntax this build does not yet evaluate: it FAILS the case by name rather than passing, so an unproven assertion is never counted as green.

### ✅ `compare`  
*canonical*

```
compare A to B
compare: done
```
Example:
```mohio
price 10
budget 20
compare price to budget
compare: done
```
Compare two values in a test/diff context.


## Declarations and tooling

### ✅ `describe`  
*canonical*

```
describe "text"
```
Example:
```mohio
describe "rates for cabins"
```
Human-readable description for a block.

### ✅ `view`  
*canonical*

```
view "NAME"
```
Example:
```mohio
view "home"
```
Render a named view/template.

### 🚧 `template`  
*not-built*

```
template NAME
template: done
```
Define a reusable view template. Parses and passes `mio check`, but has no interpreter executor: at run it refuses with `No executor for TemplateDecl -- this construct is not executable in this build`. Declared, not built.

### 🚧 `screen`  
*not-built*

```
screen NAME
    title "..."
screen: done
```
A mobile screen declaration, declared but NOT built -- it arrives with the mobile surface. Verified by running: `screen NAME` is REFUSED at BOTH check and run, naming it -- `` `screen` is declared but NOT built yet -- it arrives with the mobile surface, so today it does nothing. `` The dead `screen_block` grammar rule (defined but referenced by no other rule, unreachable like `miochain`) was REMOVED 2026-10-05; the refusal is now on the bare word via the reachability layer, a clean fail-loud rather than the old unmatched-closer mis-parse. Its sibling `layout` refuses the same way; `mioapp` refuses as a service used bare.

### 🚧 `layout`  
*not-built*

```
layout NAME
    ...
layout: done
```
A mobile layout declaration, the sibling of `screen`, declared but NOT built -- it arrives with the mobile surface. Verified by running: `layout NAME` is REFUSED at BOTH check and run, naming it -- `` `layout` is declared but NOT built yet -- it arrives with the mobile surface, so today it does nothing. `` Its dead grammar rule was removed alongside `screen`'s (2026-10-05); the refusal is on the bare word via the reachability layer.

### ✅ `copy`  
*canonical*

```
copy SRC to DST
copy: done
```
Example:
```mohio
copy "a.txt" to "b.txt"
copy: done
```
Copy a file from a source to a destination, with `rename` inside the block. BUILT (T1-COPY-THROUGH-ZONES, 2026-10-06; verified by running, `copy "a.txt" to "b.txt" / copy: done` checks clean). It runs THROUGH the miofile file area, so both ends are governed by the zone rules: neither path may leave the area; the destination's zone refuses an executable, a type its `accept` does not list, or a file over its `max size`, judged on the name the file ends with AFTER `rename`; an existing file is replaced only with `allow overwrite`. Related hardening in the same unit: the file area is no longer served statically, so a restricted file can no longer be reached at `/mio_files/<name>` without a handler -- a copy was never needed to leak it.

### ✅ `export`  
*canonical*

```
export as.csv | as.json to TARGET
```
Example:
```mohio
find rows in db.people
    export as.csv to "people.csv"
find: done
```
Write a query's rows to a file, as a clause on a `find` query (verified). `as.csv` and `as.json` are wired; `as.pdf` and `as.xlsx` refuse AT CHECK (a document library is not wired) -- before this they 500ed at run, and `as.xlsx` silently wrote CSV bytes into the .xlsx file, which is the fail this closed. EGRESS MASKING, verified by running: a file export is BULK egress, so every enforced classifier -- `[phi]`, `[pii]`, `[pci]` -- is masked to its last four characters on the way out (`****1111`), while an untagged or `[public]` field passes through in full. A `return` projection runs BEFORE the export, so a field projected out never reaches the file, and the classifier mask is the second guard, so a forgotten projection no longer leaks. This DIFFERS from `give back`: the display path deliberately keeps `[pii]` VISIBLE (a page serving a visitor their own value), because a give-back is a response to the person whose data it is while a file is bulk egress.

### ✅ `sql`  
*canonical*

```
sql
    RAW SQL
sql: done
```
Example:
```mohio
sql
    SELECT 1
sql: done
```
Raw SQL escape hatch: the body is passed VERBATIM to the connected engine and its rows are returned (verified by running: `SELECT COUNT(*) FROM t` on sqlite returns a `COUNT(*)` column -- the engine's own dialect, untranslated). Because the SQL is hand-written in ONE engine's dialect, a program that uses it may NOT run unchanged on another engine; now that `connect` accepts sqlite, postgres, mysql, mongodb and oracle, that gap is real (column-name casing, functions, and quoting differ per engine). Mohio does NOT warn about this: verified by running, a `sql` block checks clean and runs with no portability or dialect warning, so keeping the SQL portable -- or confining engine-specific SQL to a single-engine deployment -- is the developer's responsibility. Prefer the MioQL verbs (`retrieve` / `find` / `save` / `update` / ...), which Mohio renders into each engine's dialect for you; reach for `sql` only for what they cannot express.

### ✅ `debug`  
*canonical*

```
debug on | debug off | debug minimal | debug verbose
```
Example:
```mohio
debug on
show "hi"
```
Turn on execution tracing. `debug on` writes plain logs to `mohiolog/<journey>/<timestamp>/journey.log` (the folder is auto-gitignored); `debug off` writes nothing. `debug verbose` adds automatic `[trace]` lines for checks, branches and AI steps. `debug minimal` is accepted but today behaves like `debug on`: the designed light-set filter (journey start and end, errors, AI confidence and result only) is NOT built yet. Logged values currently print in their internal form (`x = MohioValue(5, 'number')`), not yet plain English.

### ✅ `ignore`  
*canonical*

```
ignore TARGET
ignore: done
```
Ignore a target during processing.

### ✅ `custom`  
*canonical*

```
custom
    ...
custom: done
```
Custom block for extension entries.

### ✅ `map`  
*canonical*

```
map NAME
    data
        source.field -> [transform ->] dest.field
    map: done
```
Example:
```mohio
map Ledger
    data
        order.total -> db.entries.amount
    map: done
create order
    total "42"
create: done
flow Ledger
```
A map declares a data route: one or more chains, each a line of stages joined by `->`, read left to right. It only describes the route; `flow` runs it and `walk` inspects it. The value-to-value alias form (`map N / "a" -> "b" / map: done`) is a different, not-built construct and fails loud, so use the `data` chain form shown here. PENDING (HC-099, 6.0.0, build lane in progress): three map-access gaps are slated to become `mio check` refusals -- a chain-less `grab first stage from <map>` with no `.chain` (today it misparses as a quick-read `grab` block and errors asking for `grab: done`), a map name colliding with a variable, task or shape (today allowed with no warning), and a SILENT DOTTED READ of `<map>.<chain>` after such a collision (today it returns empty with no error once a variable shadows the map name). The dotted form itself, `grab first stage from <map>.<chain> as NAME`, does not change. Exact refusal messages will be filled in here when the build lands.

### ✅ `pack`  
*canonical*

```
load pack NAME [version N]
```
Load a language/feature pack.

### ✅ `applang`  
*canonical*

```
applang [NAME]
    ...
applang: done
```
App-language localization declaration (managed translation surface).

### ✅ `enterprise`  
*canonical*

```
enterprise
    ...
enterprise: done
```
Enterprise configuration block (commercial features).

### ✅ `walk`  
*canonical*

```
walk MAP[.CHAIN]
    at each stage: report
walk: done
```
Example:
```mohio
map Ledger
    data
        order.total -> db.entries.amount
    map: done
create order
    total "42"
create: done
flow Ledger
walk Ledger
    at each stage: report
walk: done
```
Drive a map like `flow`, but stop at every stage and look at what is there. `at each stage: report` emits each stage in order; `flow` runs a map, `walk` inspects it. Name one chain with `walk MAP.chain`, or the whole map with `walk MAP`.

### ✅ `debug.log`  
*canonical*

```
debug.log NAME   (or a dotted path: debug.log user.email)
```
Example:
```mohio
debug on
x 5
debug.log x
```
Write one variable to the debug log when debug is active: `debug.log user.email` records the value under `mohiolog/`. Under an ACTIVE SECTOR a classified field ([phi] / [pci] / [pii]) is redacted to `[omitted -- <sector> sector field]`, verified, so a protected value does not reach the log. With `debug off` the line runs and produces nothing: it is not removed from the program, it returns early.

### ✅ `debug.checkpoint`  
*canonical*

```
debug.checkpoint "LABEL"
    debug.log NAME
debug.checkpoint: done
```
Example:
```mohio
debug on
x 5
debug.checkpoint "after set"
    debug.log x
debug.checkpoint: done
```
A named snapshot in the debug log: writes the label and each `debug.log` inside it to `mohiolog/` when debug is active. Both `debug.log` and `debug.checkpoint` really write to disk (not only the inline verbose trace). Like `debug.log`, it does nothing under `debug off`.

### ✅ `mio schema (generate / check)`  
*canonical*

```
mio schema generate <file.mho | app-folder>   |   mio schema check <file.mho | app-folder>   (CLI)
```
A CLI command. `mio schema generate` describes an app's tables and `mio schema check` validates field references against them. Ruled 2026-10-05: both take an APP FOLDER as well as a single `.mho` file, resolving the journey and every include the same way `mio check` and `mio serve` do, so they describe and check the WHOLE app (a folder generate writes one manifest, `<folder>/.mhoschema`). They build each page through the same cached parse `mio check` uses, not a second uncached parse (which took a 3,820-line app's `schema generate` from 186 seconds to under one). The check reads the real compiled AST, so a write targeting a dotted `db.customers` is understood and a misspelled field in a real program is caught (it was reported valid before, when the walker only read a `.table` attribute the real path never produces).

### ✅ `mio migrate typed-storage (CLI)`  
*canonical*

```
mio migrate typed-storage <file.mho> [--dry-run]   (CLI)
```
A CLI command (verified: `mio migrate typed-storage <file.mho> [--dry-run]`). It converts a table created BEFORE typed storage -- whose declared int / boolean / decimal / currency fields sit in TEXT columns -- into the types its shape now declares, through the same encoder a write uses (see `typed storage`). ALL-OR-NOTHING: nothing moves unless every value converts. `--dry-run` lists the plan and every value that would not convert (table, column, row id, value, reason) and changes nothing; a real run with an unconvertible value refuses the same way. Each converted column leaves one audit record. Verified on SQLite (always) and on Postgres / MySQL / Oracle through the shared test isolation helper.


## Services (mio*) — managed, commercial, or partial

### 🚧 `mioauth.*`  
*not-built*

```
mioauth.login | logout | verify | refresh | apikey | biometric
```
Auth service is tiered and mostly not wired: providers, JWT, session-with-identity and the `mioauth.*` calls are declared but not built and fail loud. For working auth today, guard a route with `require role` and establish the server-verified role at login with `grant role`. There is no `verify token` (it is not a working statement), and no built-in durable identity for a person across sessions -- `session.id` is stable within a request but rotates on a privilege change by design, so it cannot identify the same human across a role switch.

### ✅ `miocookie.*`  
*canonical*

```
miocookie.set | get | exists | delete | clear
```
Example:
```mohio
miocookie.set "theme" to "dark"
```
Cookie read/write.

### 🚧 `miodata.*`  
*not-built*

```
miodata.json | csv | xml | yaml | validate
```
Data format transforms. Declared but not built; fails loud at the point of use.

### 🚧 `mioresponse.*`  
*not-built*

```
mioresponse.header NAME VALUE | mioresponse.status N
```
Set response headers and status. Declared but not built; fails loud at the point of use.

### 🚧 `miosecurity.*`  
*not-built*

```
miosecurity.scan | miosecurity.report
```
Not built; a commercial-tier managed service. Structural security-by-construction is free and always on; explicit scan/report is licensed. Fails loud at the point of use.

### 🚧 `miotranslate.*`  
*not-built*

```
miotranslate.text VALUE to LANG | miotranslate.page
```
Managed/paid translation product (separate from free ai.translate). Commercial tier.

### 🚧 `miovault.*`  
*not-built*

```
miovault.get | set | delete
```
Managed secrets/HSM. Commercial tier.

### 🚧 `mioknow.*`  
*not-built*

```
mioknow.remember | recall | reinforce | forget
```
Managed AI memory (pgvector). Commercial tier.

### 🚧 `miochain.*`  
*not-built*

```
miochain.contract | execute | tx | wallet
```
Blockchain integration. Commercial/future.

### 🚧 `miograph.*`  
*not-built*

```
miograph.endpoint | find
```
GraphQL/knowledge-graph. Future.

### 🚧 `miomap`  
*not-built*

```
miomap NAME
    ...
miomap: done
```
Data mapping service. Not wired yet.

### 🚧 `miopush.* / miopublish / mioprint.send`  
*not-built*

```
miopush.send | broadcast | miopublish | mioprint.send
```
Push/publish/print messaging. Not wired yet.

### 🚧 `mioapp`  
*not-built*

```
mioapp NAME
    ...
mioapp: done
```
Native mobile target, PLANNED and NOT built -- it arrives with the mobile surface. Verified by running: `mioapp NAME` is REFUSED at BOTH check and run -- `mioapp is a Mohio service, not a variable, and it is never used bare.` (It is reserved as a service root, so the refusal differs from `screen` / `layout`'s mobile-surface message, but the effect is the same: not usable today.) Its dead `mioapp_decl` grammar rule was removed 2026-10-05 alongside `screen`'s and `layout`'s. Mohio's line is the same everywhere: the basics a developer needs to build and ship securely are free; advanced and managed tooling is commercial. Plans change and nothing here is a commitment to a date.


## Qualifiers, modifiers, and reserved words

### ✅ `modify (bulk update)`  
*canonical*

```
modify every X in COLLECTION [where COND]
    apply ...
modify: done
```
Update many rows in one block via an apply body.

### ✅ `modify.as / modify.by / modify.in`  
*canonical*

```
modify.as | modify.by | modify.in
```
`modify.as` and `modify.in` shape how a `modify` applies a change: `as` names the result, `in` places it. `modify.by` is NOT built and REFUSES at check (`by` has no use case yet); change records with `modify` and an `apply` block instead.

### ✅ `audience`  
*canonical*

```
audience "role"
```
Modifier inside ai.explain naming who the explanation is for.

### ✅ `best effort`  
*canonical*

```
best effort
```
Reliability qualifier: try but do not fail the whole flow.

### ✅ `take`  
*canonical*

```
take N
```
Take the first N items.

### ✅ `primary`  
*canonical*

```
primary
```
Marks a primary key on a field.

### 🚧 `threshold`  
*not-built*

```
threshold N
```
A field/regulatory threshold modifier. It parses but enforces nothing today, so it is marked not-built in this release rather than canonical. A build-or-mark ruling is owed.

### ✅ `timezone / in.timezone`  
*canonical*

```
timezone "Zone"   (declaration)   |   VALUE in.timezone "Zone" | VALUE in.timezone FIELD   (display)
```
Ruled 2026-10-05. The `timezone` keyword works in FOUR contexts (each verified at check): (1) a JOURNEY DEFAULT -- `timezone "Asia/Bahrain"` inside a journey sets the zone for the app; (2) a FIELD OVERRIDE -- `created as datetime timezone "Asia/Bahrain"` on a shape field; (3) the `timezone` TYPE -- `z as timezone`, a field that holds a zone name; and (4) DISPLAY conversion via `in.timezone` -- `show payment.created in.timezone "Asia/Bahrain"` or `... in.timezone customer.zone` (a dotted field), which renders a stored datetime in that zone. ZONE NAMES are standard only: a real IANA `Area/Location` name or the bare word `UTC`; a fixed offset and an `Etc/GMT...` name BOTH refuse (even though `Etc/GMT+3` is a loadable zoneinfo key, it is explicitly excluded). STORAGE: datetimes are stored in UTC; a datetime that arrives WITHOUT an offset is read in the field's declared zone, otherwise UTC -- never guessed, never refused.

### ✅ `toggle`  
*canonical*

```
toggle #SELECTOR as STATE
```
MioScript: toggle a CSS state on an element.

### ✅ `similar`  
*canonical*

```
similar to "text"
```
Semantic-similarity clause (mioknow recall).

### ✅ `overwrite`  
*canonical*

```
overwrite
```
Modifier allowing an operation to overwrite existing data.

### ✅ `exclude`  
*canonical*

```
exclude TARGET
```
Exclude a target (e.g. broadcast except / exclude).

### ✅ `quality`  
*canonical*

```
quality LEVEL
```
Generation-quality modifier INSIDE `ai.create` (verified: `quality "high"` in an ai.create body checks clean). Written on its own as a statement it REFUSES at check (Batch 35 P2: `quality is NOT built yet -- planned`), because a bare `quality` would otherwise become a silent variable. Only the standalone use refuses; the ai.create modifier works.

### ✅ `minimal`  
*canonical*

```
minimal
```
Posture modifier inside `ai.create` (verified: works in an ai.create body). Written on its own as a statement it REFUSES at check (Batch 35 P2: `minimal on its own is not a Mohio word`), because a bare `minimal` would otherwise become a silent variable. Only the standalone use refuses.

### ✅ `transaction`  
*canonical*

```
transaction
    <writes>
transaction: done
```
Example:
```mohio
connect db as sqlite from env.DATABASE_URL
transaction
    save to db.orders
        id 1
    save: done
    save to db.receipts
        order_id 1
    save: done
transaction: done
```
Run the body atomically: every write inside commits together, or none does. This is how a program makes its own row and its own evidence row a single unit -- both land or neither does. If any write fails (a duplicate key, a constraint, any error) the whole block rolls back, completed writes included, and the failure surfaces (a duplicate answers 409 `conflict`); it never silently commits a partial. A write's OWN `on.failure` inside the block still runs but CANNOT rescue the transaction -- the block rolls back anyway, and `mio check` warns when it sees on.failure inside a transaction. The RESPONSE decides what is kept (HC-127, ruled 2026-10-08): a `give back` inside the block commits on a 2xx or 3xx status and rolls back on a 4xx or 5xx -- even with no failed write -- so a `check exists ... when empty` -> save -> `give back [409]` refusal keeps nothing and a `[201]` success keeps all of it (verified by running: `save` + `give back [500]` inside the block leaves 0 rows, `give back [201]` leaves 1). A write that actually fails still forces a rollback and surfaces the real failure rather than a false success. A regulated write inside a transaction carries its audit envelope and hash chain, flushed with the transaction's outcome (committed or rolled_back). A transaction with no database connection is refused rather than run non-atomically. On MongoDB a multi-write `transaction` is refused at RUN (not at check, since the engine is known only from the connection): Mongo sessions/transactions are not wired yet (T1-MONGO-PROGRAM-TRANSACTION), so the block cannot be made atomic and it refuses naming the fix -- one write, a `saga` with a `compensate` per step, or a SQL engine (Postgres, MySQL, Oracle, SQLite) -- and runs nothing. Verified atomic on SQLite through `mio run` and a served handler under per-request connection leasing; the executor and the duplicate-key detector are shared across SQLite, Postgres and MySQL. (`transaction` is also usable as a task parameter name.)

### 🚧 `conflict`  
*not-built*

```
conflict
    ...
conflict: done
```
Conflict rules for miomap; not wired yet. Unrelated to the 409 `conflict` HTTP response a duplicate key produces on a write (see `save`).

### ✅ `deploy`  
*canonical*

```
languages
    deploy LANG
languages: done
```
Example:
```mohio
journey J
    languages
        current EN
        deploy EN
    languages: done
journey: done
```
The deploy target inside a `languages` block: which language the app deploys in. It is a sub-keyword of `languages`, not a free-standing verb, and the grammar locks it as a real feature. Checks and runs today.

### 🔒 `import`  
*reserved*

Reserved; use `include` (build-time) or `use` today. Enforcement is pending in the dev lane: until it lands, `import` is still accepted as an ordinary identifier, but it is held for future use and should not be relied on as a variable name.

### 🔒 `planned / supported`  
*reserved*

Status keywords reserved for roadmap/declaration metadata.

### 🚧 `parse-as-variable words (config / layout / percentage / purpose / chars / app config / trusted / for.purpose)`  
*not-built*

Batch 35 Part Two split these grammar words. NOW REFUSED at check, each teaching the fix: `layout` and `decision` (declared but NOT built -- `layout` arrives with the mobile surface, `decision` is planned), and the wrong-word orphans `read` (use `retrieve` for one record or `find` for many), bare `verbose`, `on.change` (the change listener needs no event word), `not.found` and `modify.by` (use `modify` with an `apply` block), and dotted `not.empty` written alone (write `when x is not empty`). STILL silently become a variable, the remaining gap: `config`, `percentage`, `purpose`, `chars`, `app config`, `trusted`, `for.purpose`. Detection is by AST NODE, not by word: a shape FIELD named `read` in a save block is untouched, and only the variable-declaration use refuses -- so these words are not banned outright. (Batch 38 freed `style` for ordinary variable use, since it was an orphan, not a dead duplicate.)

### 🚧 `<- / <-> (flow arrows)`  
*not-built*

The back arrow `<-` and bidirectional arrow `<->`. `<->` is reserved and refused ("reserved and not supported here"); `<-` and the value-to-value alias map that would use it are declared but not built. The forward arrow `->` works, but only inside a map data chain (see the `map` entry). Do not rely on `<-` or `<->` yet.


## Security refusals (checked at compile time)

### ✅ `regulated field must state its type`  
*canonical*

```
FIELD as TYPE [classifier]   (never a classified field with no type)
```
Refused at check time (this is rejected, and here is why):
```mohio
shape P
    ssn [phi]
    other as text
shape: done
```
A field carrying a regulatory classifier (`[phi]`, `[pci]`, `[pii]`) MUST declare its type. Regulated data says its own type; nothing guesses it, because a wrong guess is a compliance fault that looks like nothing. Fix: `ssn as text [phi]`.

### ✅ `a shape may not loosen its sector`  
*canonical*

```
a shape may TIGHTEN a sector's classifiers, never drop one
```
Refused at check time (this is rejected, and here is why):
```mohio
sector: demo_regulated
shape P
    ssn as text [pii]
shape: done
```
The sector is the floor. A shape may raise it (add protections) and may not lower it. Here `demo_regulated` requires `ssn` to carry `[phi]`; the shape drops it, and check refuses. (The demo profile is unverified, and `ssn` is never-store there, so those advisories ride along.)

### ✅ `an upload area may not accept an executable`  
*canonical*

```
accept <safe types>   (never accept exe / an executable type)
```
Refused at check time (this is rejected, and here is why):
```mohio
miofile
    local "uploads" as media accept exe max size 5mb
miofile: done
```
An upload field refuses executables ahead of any allowlist, so `accept exe` can never be honoured. Left to runtime it reads as a working permission that is silently not one, so it is refused where it is written.

### ✅ `file op must land inside a declared zone`  
*canonical*

```
a miofile.* path outside every declared zone loses that zone's policy
```
Refused at check time (this is rejected, and here is why):
```mohio
miofile
    local "uploads" as media accept jpg, png max size 5mb
miofile: done
miofile.write "other/x.txt" "hi"
```
Once zones are declared, a file operation on a path no zone covers silently skips the `accept` / `max size` policy the author thought they had set. Check warns so it is said out loud. (Silent when NO zones are declared, so unzoned programs are not nagged.)

### ✅ `unauthenticated route in a regulated sector`  
*canonical*

```
add `require role ...` to a route that reads governed data
```
Refused at check time (this is rejected, and here is why):
```mohio
sector: financial
shape Acct
    id as int
shape: done
connect db as sqlite from env.DATABASE_URL
request for sh.Acct at /balance
    retrieve acct from db.accounts
        match id to 1
    retrieve: done
request: done
```
A route in a declared sector that reads the database with no `require role` anywhere in it is an unauthenticated path to governed data. Check warns so it is a decision someone made, not one nobody noticed. If a public read is intended, say so in a comment; otherwise add `require role`.

### ✅ `grant role from a client value`  
*canonical*

```
grant role a SERVER decides (a literal, or a looked-up record's field)
```
Refused at check time (this is rejected, and here is why):
```mohio
shape Login
    role as text required
shape: done
listen for sh.Login at /login
    grant role request.role
    give back [200] "ok"
listen: done
```
`grant role` writes a VERIFIED server-side role. Taking its value straight from the request lets the caller pick their own role, re-opening the forgery `grant role` exists to close. Grant a literal, or a field of a record you looked up.

### ✅ `SameSite=None cookie without secure`  
*canonical*

```
same site "none" requires a `secure` line
```
Refused at check time (this is rejected, and here is why):
```mohio
shape Cmd
    command as text
shape: done
listen for sh.Cmd at /cmd
    miocookie.set "sid"
        value "x"
        same site "none"
    miocookie.set: done
    give back [200] "ok"
listen: done
```
A browser silently rejects a SameSite=None cookie that is not Secure, so the cookie is never set with nothing saying why. Check surfaces it before it ships. Add a `secure` line, or use `same site "lax"` / `"strict"`.

### ✅ `no script in a render`  
*canonical*

```
render scripts
    ...
render: done   (the named JS container)
```
Refused at check time (this is rejected, and here is why):
```mohio
render
    <button onclick="go()">Go</button>
render: done
```
`render` (and `render html`) carries markup, never JavaScript: a `<script>` tag and an inline handler such as `onclick=` are both refused at check time. Declare the JS container by name with `render scripts`, or, for behaviour, prefer MioScript (`listen for click on #btn`). Keeping script off the default markup surface is what makes a render auditable.


---

## Coverage against the grammar

The word list comes from `mio harvest`, the compiler's own sweep of the grammar, so it sees every keyword including the ones defined as regex terminals. Every word lands in exactly ONE of the four columns below, in this priority order, so each row sums to its Total. **Entry**: the word appears in an entry's own name, syntax or example -- it is actually taught. **Mentioned**: it appears only in passing inside another entry's note. **Family-covered**: it has no entry of its own and is not mentioned, but a documented `<prefix>.*` entry already teaches the whole family it belongs to (`miofile.copy` under the documented `miofile.*`) -- not a real gap. **No entry**: none of the above -- genuinely undocumented, the real gap to triage.

| Group | Entry | Mentioned only | Family-covered | No entry | Total |
|---|---:|---:|---:|---:|---:|
| operators and symbols | 16 | 0 | 0 | 0 | 16 |
| unit and currency families | 1 | 4 | 6 | 0 | 11 |
| string, cast and condition words | 54 | 13 | 0 | 0 | 67 |
| service calls | 31 | 6 | 58 | 0 | 95 |
| everything else | 309 | 16 | 0 | 0 | 325 |
| **all words** | **411** | **39** | **64** | **0** | **514** |

### unit and currency families

Mentioned in a note but with no entry of its own (4): `in.USD`, `in.celsius`, `in.km`, `in.miles`

Family-covered, no entry of its own but a `<prefix>.*` entry already teaches it (6 words, under `in.*`): `in.EUR`, `in.GBP`, `in.fahrenheit`, `in.kelvin`, `in.kg`, `in.lbs`


### string, cast and condition words

Mentioned in a note but with no entry of its own (13): `apply.as`, `apply.in`, `as.absolute`, `as.lc`, `as.lowercase`, `as.number`, `as.uc`, `as.uppercase`, `as.xlsx`, `no.match`, `show.as`, `show.to`, `show.with`


### service calls

Mentioned in a note but with no entry of its own (6): `miocache.set`, `miohttp.post`, `miomail.queue`, `miomail.template`, `miomail.with`, `mioschedule.every`

Family-covered, no entry of its own but a `<prefix>.*` entry already teaches it (58 words, under `mioauth.*`, `miocache.*`, `miochain.*`, `miocookie.*`, `miodata.*`, `miofile.*`, `miograph.*`, `miohttp.*`, `mioimage.*`, `mioknow.*`, `miolog.*`, `miopublish.*`, `miopush.*`, `mioresponse.*`, `mioschedule.*`, `miostream.*`, `miosys.*`, `miotest.*`, `miovault.*`, `sec.*`): `mioauth.apikey`, `mioauth.logout`, `mioauth.refresh`, `mioauth.verify`, `miocache.delete`, `miocache.exists`, `miocache.flush`, `miochain.execute`, `miochain.tx`, `miochain.wallet`, `miocookie.clear`, `miocookie.delete`, `miocookie.exists`, `miocookie.get`, `miodata.csv`, `miodata.validate`, `miodata.xml`, `miodata.yaml`, `miofile.copy`, `miofile.delete`, `miofile.exists`, `miofile.list`, `miofile.move`, `miograph.find`, `miohttp.delete`, `miohttp.patch`, `miohttp.put`, `mioimage.compress`, `mioimage.convert`, `mioimage.crop`, `mioimage.resize`, `mioimage.watermark`, `mioknow.forget`, `mioknow.recall`, `mioknow.reinforce`, `miolog.alert`, `miolog.error`, `miolog.metric`, `miolog.warn`, `miopublish.guaranteed`, `miopush.broadcast`, `mioresponse.content-type`, `mioschedule.cancel`, `mioschedule.in`, `miostream.close`, `miostream.open`, `miostream.send`, `miosys.java`, `miosys.run`, `miotest.unit.ai`, `miovault.delete`, `miovault.set`, `sec.audit`, `sec.classify`, `sec.encrypt`, `sec.headers`, `sec.nohardcode`, `sec.validate`


### everything else

Mentioned in a note but with no entry of its own (16): `context`, `define`, `fields`, `nav to`, `nav.back`, `nav.forward`, `nav.root`, `on.close`, `permanent`, `range`, `rerun.after`, `rerun.max`, `rerun.until`, `section`, `until`, `while.active`


## Status disagreements (harvest vs this catalog)

Harvest is authoritative for whether a word EXISTS. This catalog is authoritative for its STATUS and content. Where they differ the difference is reported here and never resolved automatically. A curated `reserved` or `retired` sitting against a harvested `canonical` is usually correct: the word is still in the grammar but is not for use.

- broadcast: meta says not-built, harvest says canonical
- catch: meta says retired, harvest says canonical
- conflict: meta says not-built, harvest says canonical
- import: meta says reserved, harvest says canonical
- miomap: meta says not-built, harvest says canonical
- mock: meta says not-built, harvest says canonical
- on.error: meta says retired, harvest says canonical
- rerun: meta says not-built, harvest says canonical
- seed: meta says not-built, harvest says canonical
- set: meta says retired, harvest says canonical
- template: meta says not-built, harvest says canonical
- threshold: meta says not-built, harvest says canonical
- undo: meta says non-canonical, harvest says canonical
