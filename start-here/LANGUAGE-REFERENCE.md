<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md. -->
# Mohio Language Reference

*Generated from the grammar on 2026-07-31. Do not edit by hand -- edit `tools/langref/langref_meta.json` and regenerate.*

## How to use this reference

This page is generated directly from the Mohio grammar, so it always matches what the compiler actually accepts. Each entry shows a construct, its status, its syntax, and -- for canonical forms -- an example that is verified to compile. Start with the **canonical** entries: those are the working language you build with. Avoid **retired** and **non-canonical** forms (`mio fmt` rewrites them). Do not rely on **not-built** entries yet. Use search to find a keyword and the status filters to narrow what you see.

Status legend:

- ✅ **canonical** -- Recommended. Teach this.
- ⚠️ **non-canonical** -- Compiles but avoid; mio fmt rewrites it.
- ⛔ **retired** -- Do not use.
- 🔒 **reserved** -- Held for future use.
- 🚧 **not-built** -- Recognized but not executable yet.


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
A TEMPORARY LOCK, used sparingly -- NOT a way to introduce or capture a normal variable. A held value cannot be restated ("already held"). Change it with `release name` (then restate), or `release.now name = value`; also `clear`, `replace`, `rename`, `forget`.

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
Common types: text, int (or integer), dec (or decimal), boolean, json. `number` and `num` are retired type names -- use int/integer or dec/decimal. In a shape/param, `as TYPE` is a type annotation, not a value cast.

### ✅ `cast (dotted)`  
*canonical*

```
VALUE as.TYPE
```
Example:
```mohio
rounded total as.int
```
Casts are dotted: as.int (rounds), as.decimal.2, as.number, as.text, as.bool, round.up, round.down. The two-word `as int` is not a cast today.


## Text

### ✅ `join (&)`  
*canonical*

```
A & B
```
Example:
```mohio
greeting ("Hi " & name)
```
Concatenate with &.

### ✅ `text tools (no arg)`  
*canonical*

```
VALUE uppercase | lowercase | trim
```
Example:
```mohio
big name uppercase
```
uppercase, lowercase, trim.

### ✅ `text tools (with arg)`  
*canonical*

```
replace "x" with "y" | left N | right N | after "x" | before "x"
```
Example:
```mohio
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
Display a value.

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
show {{ form sh.Signup }}
```
Render a form from a shape. The form engine is open-core.


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
check score
    when score is more than 100
        show "Amazing!"
    otherwise
        show "Keep going"
check: done
```
Body on the next line, indented. Works with every operator. Do not use the inline `when ... -> result` form.

### ✅ `unless`  
*canonical*

```
ACTION unless CONDITION
```
Example:
```mohio
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
check age
    when age is between 10 and 20
        show "mid"
check: done
```
Natural language preferred outside parentheses.

### ⛔ `if / else as block`  
*retired*

Use check/when/otherwise (or `unless`). `if` is an inline qualifier only.


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
repeat each user in users
    show user.name
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
task NAME [returns TYPE]
    take PARAM as TYPE
    ...
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
Inputs are declared with `take` lines in the body -- NOT on the task header (that form is retired). `returns TYPE` (optional) makes `give back` a value the caller can capture.

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
call NAME with VALUE   |   call NAME / input value / ... / call: done
```
Example:
```mohio
call greet with "Bo"
```
call only ever invokes a task. One value: `call greet with "Bo"`. Several values by name (order-free): `call NAME / a 7 / b 9 / call: done`. Bad arguments fail loud.

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
    email as text required unique
    age as int min 0 max 120
shape: done
```
Declared with a bare name. Reference it elsewhere with sh.NAME. Declaring `shape sh.Name` is an error.

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
Modifiers tighten a field. `never store` / `never log` enforce data handling.

### ✅ `sh. reference`  
*canonical*

```
sh.NAME
```
Example:
```mohio
new sh.Signup
```
Reference a declared shape. Reference-only prefix.


## Data (MioQL)

### ✅ `connect`  
*canonical*

```
connect db as postgres from env.DATABASE_URL
```
Example:
```mohio
connect db as postgres from env.DATABASE_URL
```
Connect once at the top. Tables are db.NAME.

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
One row. Stack match lines for AND. `match any` = OR, `no.match` = NOT (closes no.match: done).

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
Many rows. `where` is for ranges/comparisons.

### ✅ `save`  
*canonical*

```
save to db.Y
```
Example:
```mohio
save to db.players
    name player.name
    score player.score
save: done
```
Insert.

### ✅ `update`  
*canonical*

```
update db.Y
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
Bare field values inside update; match selects the row.

### ✅ `upsert`  
*canonical*

```
upsert db.Y match KEY to "..."
```
Example:
```mohio
upsert db.players match id to "1"
```
Insert or update.

### ✅ `remove`  
*canonical*

```
remove from db.Y
```
Example:
```mohio
remove from db.players
    where id is player.id
remove: done
```
Delete a row. `remove` is the deletion verb (not delete).

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
Ascending / descending.


## Web (server listeners)

### ✅ `listen for`  
*canonical*

```
listen for
    new sh.X
        ...
    new: done
listen: done
```
Example:
```mohio
listen for
    new sh.Request
        require role "user"
        give back 200 "ok"
    new: done
listen: done
```
Listens for actions. The method is never declared; the word carries it.

### ✅ `new sh.X (POST)`  
*canonical*

```
new sh.SHAPE
```
Example:
```mohio
new sh.Request
```
A submission arrived (POST), bound to the lowercase instance.

### ✅ `request for sh.X (GET)`  
*canonical*

```
request for sh.SHAPE
```
Example:
```mohio
request for sh.Page
```
A page or data was requested (GET).

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
Send the response; work above must finish first.

### ✅ `require role`  
*canonical*

```
require role "NAME" [or "NAME"]
```
Example:
```mohio
require role "admin" or "system"
```
Role gate.

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
ai.decide is_spam returns boolean
    confidence above 0.85
    weigh
        message.body, sender.history
    not confident
        give back pending "Sent to a human"
ai.decide: done
```
not confident is a REQUIRED fallback; its absence is a compile error. ai. is free.

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
ai.create text|image|video prompt "..."
```
Example:
```mohio
ai.create text prompt "a haiku about rain"
```
Generation. ai. is free.

### 🚧 `ai.connect`  
*not-built*

```
ai.connect ...
```
Chain providers with fallback. Block form; the reference had only the opener, which does not compile on its own. Needs a verified example before it is shown as canonical.

### 🚧 `mioai.* (paid)`  
*not-built*

```
mioai.translate ...
```
Advanced and managed AI. UNDERWAY. The free line is `ai.*` -- ai.decide, ai.agent and the rest work today and are not a trial of anything. Whatever mioai adds sits above that. Plans change and nothing here is a commitment to a date.


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
Activate a sector profile. Hierarchical and additive: sector: education.north_carolina.highschool applies all layers. One per file.

### ✅ `compliance:`  
*canonical*

```
compliance: FRAMEWORK
```
Example:
```mohio
compliance: HIPAA
```
Activate a compliance framework's code enforcement.

### ✅ `cm.* (app-level)`  
*canonical*

```
cm.retain FIELD for DURATION | cm.purge | cm.report | cm.notify | cm.lock | cm.expire
```
Example:
```mohio
cm.retain ssn for 6 years
```
App-level compliance management. cm.purge requires a reason.


## App structure

### ✅ `include`  
*canonical*

```
include "file.mho"
```
Example:
```mohio
include "tasks.mho"
```
Static, build-time merge. Carries tasks and top-level setup.

### ✅ `journey`  
*canonical*

```
journey.mho (folder-scoped spine)
```
A folder-scoped shared spine auto-applied to every .mho in its folder. Main file is processed last and overrides (last-wins).

### ✅ `page`  
*canonical*

```
page at /path
```
Example:
```mohio
page at /about
    show "About"
page: done
```
Render a page at a path under mio serve.


## Modifiers (the dot connector)

### ✅ `dot modifiers`  
*canonical*

```
on.X / as.X / by.X / do.X / to.X / in.X / is.X / not.X / if.X / with.X / from.X / and.X
```
Example:
```mohio
save to db.x on.failure raise "db down"
```
Modifiers attach with a dot, front and back, position carrying the context. on.click (react) is not click.on (perform). Dot is canonical until the Rust rewrite.


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
Browser events: click, typing, leaving, focus, hover, press, submit. Runs on the page after load.

### ✅ `put / inject`  
*canonical*

```
put VALUE into #SELECTOR | inject VALUE into #SELECTOR
```
Example:
```mohio
put value into #out
```
put is durable, inject is transient. Value must be event data, a result.field, or a held variable, never a raw string literal.

### ✅ `mark / unmark`  
*canonical*

```
mark #SELECTOR as STATE | unmark ...
```
Example:
```mohio
mark #email as invalid
```
State for CSS to style. Replaces add-class / set-style.

### ✅ `send (server bridge)`  
*canonical*

```
send #FORM to "/path"
```
Example:
```mohio
send #form to "/signup"
```
Serializes and POSTs; response in result (result.message, result.error) with on.success / on.failure.

### ✅ `notify / go to / scroll to`  
*canonical*

```
notify "..." | go to "/path" | scroll to #SELECTOR
```
Example:
```mohio
notify "saved"
```
Toast / navigate / scroll.


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
Outbound HTTP. Built on stdlib; response bound with `as`.

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
miocache.get|delete|exists|flush KEY
```
Example:
```mohio
miocache.get "k"
```
Cache reads and deletes work. `miocache.set "k" to "v"` does NOT: the `to` argument form is unwired and check says so, so treat set as not built until that is fixed.

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


## Services (mio*) — not built yet

### ✅ `mioconnect`  
*canonical*

```
mioconnect NAME [as ALIAS] / address URL / auth ... / operation OP / path ... / sends ... / operation: done / mioconnect: done   (shorthand: mioconnect NAME from env.X). Call: Connector.operation with PAYLOAD as RESULT.
```
Named external connectors that compile to miohttp. Built and verified: the full block declaration, the shorthand, the dotted call, and the ai.agent `tools`-grant layer (operation-level + bare-connector expansion, grant validated at setup, ungranted calls refused with TOOL_NOT_GRANTED).

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
The list-grow verb: append an element to the end of a list. Lists ONLY -- `add` to a non-list fails loud, pointing to append/prepend for strings. Preserves order and duplicates.

### ✅ `list access (first / last / position / pos)`  
*canonical*

```
LIST.first  |  LIST.last  |  LIST.position.N  |  LIST.pos.N  |  LIST.count
```
Example:
```mohio
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
encode data as base64
```
Encode a value (base64, etc.).

### ✅ `decode`  
*canonical*

```
decode VALUE from FORMAT
```
Example:
```mohio
decode data from base64
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
masked card mask.all except last 4
```
Mask all but the last/first N characters.


## Math and aggregation

### ✅ `absolute`  
*canonical*

```
absolute VALUE as NAME
```
Example:
```mohio
absolute -5 as size
```
Absolute value.

### ✅ `calculate`  
*canonical*

```
calculate
    NAME FUNC
calculate: done
```
Aggregation block over fields.

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

### ✅ `then`  
*canonical*

```
UNIT then UNIT then ...
```
Result threading: chain actions, each feeding the next.

### 🔒 `undo`  
*reserved*

Reserved for saga/compensation rollback.


## Comparisons and time

### ✅ `below / within / since`  
*canonical*

```
below N | within DURATION | since TIME
```
Comparison and time qualifiers used in conditions and ranges.

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
Cursor-based pagination clause.

### ✅ `paginate`  
*canonical*

```
paginate by N
```
Page results by N.

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

### ✅ `broadcast`  
*canonical*

```
broadcast to room VALUE [except WHO]
```
Example:
```mohio
broadcast to room "lobby"
```
Broadcast to a room (WebSocket).

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
A multi-step saga with compensation.

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

### ✅ `rerun`  
*canonical*

```
rerun NAME with VALUE
```
Example:
```mohio
rerun job with 3
```
Re-run a job. rerun.after / rerun.max / rerun.until tune retries.

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

### ✅ `mock`  
*canonical*

```
mock sh.SHAPE
    ...
mock: done
```
Example:
```mohio
mock sh.Order
    show "m"
mock: done
```
Mock a shape or dependency in tests.

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
expect total is 3
```
Test assertion.

### ✅ `compare`  
*canonical*

```
compare A to B
compare: done
```
Example:
```mohio
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

### ✅ `template`  
*canonical*

```
template NAME
template: done
```
Define a reusable view template.

### ✅ `screen`  
*canonical*

```
screen NAME
    title "..."
screen: done
```
Example:
```mohio
screen home
    show "Home"
screen: done
```
A screen declaration.

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
Copy a file; rename inside the block.

### ✅ `export`  
*canonical*

```
export as csv|json|pdf to TARGET
```
Export data to a format/target.

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
Raw SQL escape hatch.

### ✅ `debug`  
*canonical*

```
debug VERBOSE|...
```
Developer debug declaration.

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
    ...
map: done
```
Alias map declaration.

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


## Services (mio*) — managed, commercial, or partial

### 🚧 `mioauth.*`  
*not-built*

```
mioauth.login | logout | verify | refresh | apikey | biometric
```
Auth service is tiered and only partially wired. For working auth today use `require role` and `verify token`. Being finalized.

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
Native mobile targets. PLANNED, not built -- each is declared in the grammar and none is wired, so a call fails loud at check. For environment values use `env.NAME`, which works today and is free. Mohio's line is the same everywhere: the basics a developer needs to build and ship securely are free; advanced and managed tooling is commercial. Plans change and nothing here is a commitment to a date.


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
Modifiers that shape how a modify applies a change.

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

### ✅ `threshold`  
*canonical*

```
threshold N
```
Field/regulatory threshold modifier.

### ✅ `timezone`  
*canonical*

```
timezone "Zone"
```
Timezone setting for time handling.

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
Generation-quality modifier (ai.create).

### ✅ `minimal`  
*canonical*

```
minimal
```
Posture modifier requesting the minimal form.

### ✅ `transaction`  
*canonical*

```
transaction
```
Reserved name usable as a task parameter / db transaction context.

### 🚧 `conflict`  
*not-built*

```
conflict
    ...
conflict: done
```
Conflict rules for miomap; not wired yet.

### 🔒 `deploy`  
*reserved*

Reserved for deployment tooling.

### 🔒 `import`  
*reserved*

Reserved; use `include` (build-time) or `use` today.

### 🔒 `planned / supported`  
*reserved*

Status keywords reserved for roadmap/declaration metadata.


---

## Uncatalogued grammar keywords

These keyword literals exist in the grammar but are not yet described above. They should be categorized:

`pad.left`, `pad.right`
