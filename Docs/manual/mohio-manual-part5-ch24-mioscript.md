<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Part V -- Web and delivery

## Chapter 24: MioScript -- Mohio in the browser

*Every example below was checked with `mio check` (shown where it matters). MioScript runs in a browser,
which the doc lane cannot open, so RUNTIME behavior here is what the langref lane reconciled by running
the client through a jsdom harness (`tests/test_mioscript_jsdom.py`), not something re-run in a real
browser this pass. Syntax and refusals are `mio check`-verified; browser effects are cited, not re-run.*

MioScript is Mohio that runs on the page after it loads: the behavior layer. It listens for what a
person does and changes the live page in response. It is not JavaScript with a skin, and it is not where
you author the look -- that stays in the display layer and CSS. The test for every word: would you say it
to a person doing the task?

---

### Listening: `listen for`

You react to an event with `listen for EVENT on #SELECTOR`, closing with `listen: done`. The `#id`
selector is a bare token, no quotes -- element ids are the one place `#` appears in Mohio.

```mohio
listen for click on #buy
    notify "added to cart"
listen: done
```

**Six event names are human intent, mapped to a DOM event for you** (verified against the emitter's
`_EVENT_INTENTS` map): `typing` (input), `leaving` (blur), `entering` (focus), `hover` (pointerover),
`press` (pointerdown), `release` (pointerup). **Every other name passes through as the raw DOM event of
that name** -- `click`, `submit`, `change`, `focus`, `keydown` and the rest all work because they are
already DOM events; nothing rewrites them. That pass-through is a deliberate escape hatch, covered in its
own section below. A selector-only `listen for #search` (no event named) resolves to the `change` event.

**A `submit` handler automatically cancels the browser's native submit**, so you never write a
prevent-default statement -- that explicit statement is gone. Listen for `submit` (and `reset`), never a
click on the button; the form events fire for both mouse and keyboard. The cancel is `submit`-only:
`reset` and every other event keep their native default.

**Event data is read implicitly off the listened element, bare, no `the`:** `value`, `key`, `x`, `y`
(the four names in the emitter's `_DATUM` map). Checked-state is not a bare read; it is a condition,
`when value is checked` (see the condition table below). A name that is none of these, none of `result`,
and no page variable (held or fluid) fails loud at compile rather than shipping the wrong reference to a browser.

```mohio
listen for typing on #search
    q value
listen: done
```

---

### Debounce and idle: `on.pause` and `on.idle`

`on.pause N <unit>` after the selector waits until the event stops for that long, then fires once -- the
search-as-you-type pause:

```mohio
listen for typing on #search
    on.pause 300 ms
    request "/suggest" into #suggestions
listen: done
```

`on.idle` is its whole-page cousin, a top-level declaration watching the entire document for no activity
of any kind (mouse, key, scroll, touch) for a stretch -- session timeout, auto-logout, "are you still
there":

```mohio
on.idle 25 minutes
    show #relogin
on.idle: done
```

One shared clock; any activity resets it, so a person reading without moving the mouse is not logged out
mid-sentence. Duration is a number and a full word with a space: `25 minutes`, `300 ms`, not `25min`.

**`on.idle` covers only the browser half; the full session-timeout recipe.** `on.idle` shows or hides something
on the page; it does not decide who gets let back in. That is the server's job, and it uses no new construct,
only ones Mohio already has: `random.token` for an unguessable token, `miomail.send` to deliver it, and `grant
role` once a real one comes back. The full recipe, both halves together, is `cookbook/session-timeout.mho` and
runs as written (folded here from the older guide, which is now a pointer to this chapter):

```mohio
// The browser half. Nobody has moved, typed, scrolled, clicked or touched
// anywhere on this page for 25 minutes, so cover it and ask.
on.idle 25 minutes
    show #relogin
on.idle: done

request for sh.Relogin at /
    render
        <h1>The ledger</h1>
        <p>Your work is here.</p>
        <div id="relogin" hidden>
          <h2>Still there?</h2>
          <p>For your security this page locked itself. Send yourself a link to carry on.</p>
          {{ form sh.Relogin }}
        </div>
    render: done
request: done

// Ask for a link. The token proves it was really them, so it is generated
// here and never chosen by the person asking.
listen for sh.Relogin at /relogin
    link_token random.token length 32
    save to db.relogin_links
        email Relogin.email
        token link_token
    save: done
    miomail.send
        to Relogin.email
        subject "Your link back to the ledger"
        body ("Open this to carry on: /back with the token " & link_token)
    give back [200] "Check your email for the link back."
listen: done

// Come back. An unknown token is refused rather than ignored, so a guessed
// one fails loudly instead of quietly doing nothing.
listen for sh.Back at /back
    retrieve.one link from db.relogin_links
        match token to back.token
    retrieve.one: done
    // A token nobody was sent finds NOTHING, and finding nothing is a normal
    // empty result, not an error. So the test is `is empty`, not `on.failure`.
    check link
        when link is empty
            give back [401] "That link is not one we sent."
        otherwise
            grant role "member"
            give back [200] "Welcome back."
    check: done
listen: done
```

Run it with `mio serve cookbook/session-timeout.mho`: with no email provider configured, `miomail` prints
instead of sending, so the whole flow runs end to end on a laptop with nothing set up.

**NOT SAFE FOR REAL USE as written.** This recipe teaches the shape of the flow, but the composed
`random.token` + `save` + `grant role` link is NOT a secure one-time link, and must not be used for real access,
approvals or payment links:

- the token is stored in PLAINTEXT (anyone who can read the table can use it); a real one is stored as a HASH;
- the token NEVER EXPIRES;
- the token can be REUSED (nothing deletes or marks it used after `/back` consumes it), so a captured link works
  forever.

A secure one-time link also has to survive email and chat scanners, which OPEN links automatically: the GET that
opens the link must only SHOW the action, and a confirming POST must perform it, or a scanner triggers the action
by previewing the email. **This is now built** (`T1-SECURE-ONE-TIME-LINKS` shipped): `mioauth.token.create` issues
an unguessable token stored only as its hash, with a purpose, an expiry and single use; `verify token` checks it
on a GET without using it and redeems it once on a POST; `mioauth.token.revoke` revokes it; every step is audited.
Build a real secure link with those, not from this insecure recipe. The full forms are in
`Docs/guides/password-login-howto.md`.

---

### A plain delay: `after`

`after N <unit>` runs the statements nested under it once, after that delay, and closes with `after: done`.
It compiles to a single browser timeout. It is distinct from the two waits above: `on.pause` debounces an
event (it resets every time the event fires), and `on.idle` watches the whole page for inactivity. `after`
does neither -- it is a one-shot "do this in a moment," with no event and no resetting. Verified clean this
pass:

```mohio
listen for click on #go
    after 2 seconds
        notify "done"
    after: done
listen: done
```

The duration format is the same one `on.pause` and `on.idle` use: a number and a full unit word with a
space.

---

### Changing the page: `mark`, `put`, `append`, `inject`

**`mark #sel as STATE`** puts a symbolic state on an element for CSS to style; `unmark` removes it,
`toggle ... as STATE` flips it. This is how motion happens too -- mark a state, let a CSS transition move
it. No class-adding or inline styles.

```mohio
listen for leaving on #email
    mark #email as invalid
listen: done
```

#### `put`, and the one canonical rule for author literals vs runtime data

**`put VALUE into #sel`** writes a value to a target. Established by running the emitter, the rule that
governs it is a single line, and it is a security boundary, not a convenience:

- An **author string literal** you type in the source (`put "<b>Saved</b>" into #box`) may render as
  markup. On an ordinary element it is written with `innerHTML`; on an input, textarea or select it is
  written to `value`.
- **Any runtime value** -- an event datum (`value`, `key`), a `result.field` from a `send`, a `hold`
  variable -- is forced to plain text. It is written with `textContent` (or `value` on a field) and can
  never become markup.

So the author, who wrote the source, is trusted with a literal; data that arrived at runtime is not. Both
were verified by `mio check` this pass -- a `put` with an author literal and a `put value` both check
clean:

```mohio
listen for typing on #search
    put value into #echo
listen: done
```

This resolves a documentation contradiction. The language reference states that `put` / `inject` values
must not be raw string literals. That is correct for `inject` and **wrong for `put`**: the emitter
explicitly supports an author literal for `put` (it routes it through `_moPutHtml`), with runtime data
forced to text. The canonical rule is the two bullets above. The language reference is the langref lane's
to correct for `put`; recorded for them in the report.

#### `append`

**`append VALUE to #sel`** adds to what is already there rather than replacing it -- to an input's `value`,
or to an element's text content. It always appends as text (both an author literal and a runtime value go
through the same text path), so `append` never introduces markup. Verified clean this pass:

```mohio
listen for click on #more
    append "one more line" to #log
listen: done
```

#### `inject`, and the rendered-fragment marker contract

**`inject result into #sel` is not generic HTML insertion.** Establishing the real contract by reading and
running the emitter: `inject` places markup **only when the enclosing `send`'s response carried the
server's fragment marker, the HTTP header `X-Mohio-Rendered: fragment`.** The server sets that header on,
and only on, a `render fragment` handler's response. If the response is not a marked fragment, `inject`
refuses at runtime, logs `inject refused: the response is not a Mohio-rendered fragment` to the console,
and leaves the region exactly as it was. So markup is inserted only when it was assembled by a Mohio
render on the server, never from data built anywhere else.

That is one of two gates. The other is at check time: the source of `inject` must be `result`, so a raw
string literal is refused before it ever runs, teaching `put`:

```
$ mio check inject-literal.mho     # inject "<b>x</b>" into #r
  x inject places a Mohio-rendered fragment as markup, so its source must be `result` -- the response of
    the enclosing `send`, which the server marks as rendered. `"..."` is a value, not a fragment, and a
    value never becomes markup. To place text use `put "..." into #box`; to place markup, return it from a
    `render fragment` handler and inject that.
```

The older `Docs/guides/mioscript-user-guide.md` calls `inject` "transient." That understates it and is
being read as the whole story: `inject` is specifically the marker-gated rendered-fragment path described
here, not a general "flash something in" verb. Treat this section as the contract; the thin guide's line
is superseded.

#### The everyday element operations, in one place

Beyond `mark` / `put` / `append` / `inject`, the ordinary page verbs are one family and belong together.
All of the following check clean (verified this pass in one file):

| Statement | What it does |
|---|---|
| `show #sel` / `hide #sel` | reveal or hide the element |
| `enable #sel` / `disable #sel` | set or clear the `disabled` state |
| `clear #sel` | empty an input's value, or an element's text |
| `focus #sel` | move keyboard focus to it |
| `scroll to #sel` | bring it into view (`scrollIntoView`) |
| `remove #sel` | take the element out of the page |
| `select #sel` | select an input's text |
| `toggle #sel` | flip its visibility |

```mohio
listen for click on #open
    show #panel
    disable #open
    scroll to #panel
listen: done
```

---

### Talking to the server: `request` and `send`

These are two different bridges, and the thin older guide never mentioned the first one.

**`request "/path" into #sel`** is a browser GET that drops the response into a target. No success or failure
branches -- it is the simple "fetch this and show it" case:

```mohio
listen for click on #load
    request "/data" into #panel
listen: done
```

By default the response is placed as TEXT. Since 2026-09-26, `request` honors the SAME fragment marker as
`send`: if the response carries `X-Mohio-Rendered: fragment` (which the server sets on, and only on, a `render
fragment` handler), it is placed as server-rendered MARKUP; anything else stays text. So a GET endpoint that
returns a `render fragment` can be injected without forcing a POST or a handwritten fetch. The security rule is
the same as `send` and `inject`, not relaxed: the only markup is server-rendered markup carrying the marker,
the protection is same-origin, and a cross-origin server would have to expose the header through CORS, which is
the developer's own choice in picking that URL.

**`send #form to "/path"`** serializes a form and POSTs it, then gives you the parsed response in
`result` inside `on.success` / `on.failure` branches. **A `send` with branches closes with `send: done`:**

```mohio
listen for submit on #signup
    send #signup to "/signup"
        on.success
            put result.message into #status
            go to "/welcome"
        on.failure
            mark #signup as error
    send: done
listen: done
```

`result.message`, `result.error` and the rest read the parsed response, and all of it renders as text
through `put`, never as markup, so a hostile response cannot inject anything. To place a rendered fragment
the server returned, use `inject result into #sel` inside `on.success`. (Note: examples in the older
`Docs/guides/mioscript-user-guide.md` show a `send` block WITHOUT the `send: done` closer; that form does
not check clean -- `send` with branches is a block and needs its closer, verified this pass.)

**What `send` does, step by step** (read from the emitter, so a reader can drop handwritten `fetch` logic
and know exactly what replaces it):

- It serializes the named form with the browser's `FormData` and POSTs it.
- It reads the response body as text, then tries to parse it as JSON. On `on.success`, `result` is the parsed
  object (and `result.field` reads its fields), or the raw text if the body is not JSON.
- An HTTP-ok response runs `on.success`; a non-ok status (4xx, 5xx) runs `on.failure`; and a **network
  exception** (the request never completing at all) ALSO runs `on.failure`.
- On `on.failure`, whichever way it failed, `result` is ONE normalized failure object with the SAME fields on
  every path, so a single `on.failure` branch handles both an HTTP error and a network exception (read from the
  emitter's `_moFailure`): `result.ok` is false; `result.status` is the HTTP status, or null on a network
  exception where there was no response; `result.message` is a human reason (empty when none); `result.error`
  is the error text, carrying the exception message on a network failure; `result.json` is the parsed body
  (empty object when none); and `result.kind` is the failure kind, matching the server's `failure.kind`
  vocabulary, with `network` on a network exception. So a dropped connection and a rejected request land in the
  same branch with the same shape, and `result.kind` tells them apart.
- When the listener's event is `submit`, the native form submission is suppressed automatically (no
  prevent-default statement to write).
- While the request is in flight past a short threshold, Mohio shows a built-in overlay indicator, the
  `mio-swirl` (an inline SVG served from the page's own bundle, never fetched from another host). It clears
  on success and on failure alike, so it is never left spinning. A stylesheet can restyle it via the
  `mio-swirl` class, or switch it off with `.mio-swirl { display: none }`.

So: `request` for a GET that shows text (or a server-rendered fragment when the response is marked one),
`send` for a form POST with success and failure handling.

**Falling back: a `send` nested in `on.failure`.** A `send` can go inside another `send`'s `on.failure`, so a
call that fails can try a second route. An application lane uses this on a live AI route: it sends to the AI
endpoint, and on failure sends again to a deterministic database fallback. This checks clean (verified this
pass):

```mohio
listen for submit on #ask
    send #ask to "/ai/answer"
        on.success
            put result.text into #out
        on.failure
            send #ask to "/ai/fallback"
                on.success
                    put result.text into #out
                on.failure
                    notify "both routes failed"
            send: done
    send: done
listen: done
```

The important part is a SECURITY property, not just the syntax: **the fallback keeps the same authority
boundary as the primary.** Because both routes are ordinary server handlers, each enforces its own
`require role`, so the fallback cannot become a way around auth. The lane verified this live: an authenticated
treasury caller gets 200 from the fallback, and an anonymous one gets 403 from it, the same as the primary.
That answers how a client falls back without loosening anything. (The syntax and the nesting are `mio check`-
verified here; the 200-versus-403 authority behavior was verified by that lane against a running server with
real sessions, which the doc lane cannot reproduce without that auth setup.)

---

### Ending a session: logout

Login is a `send` to a route that grants a role; logout is the mirror. From the browser you end a session by
sending (or navigating) to a route that runs the server statement `end session`, which drops the session's
roles and contents and invalidates its id. Both halves check clean (verified this pass):

```mohio
// the browser half: a click sends to the logout route
listen for click on #logout
    send #logoutform to "/logout"
        on.success
            go to "/"
    send: done
listen: done
```

```mohio
// the server half: the route ends the session
listen for sh.LogoutForm at /logout
    end session
    give back [200] "signed out"
listen: done
```

The lifecycle is the point: after logout, the SAME session is no longer authenticated, so the next guarded
request is refused. An application lane verified the full cycle live: an authenticated request returns 200,
the logout returns 200, and the same session immediately returns 403 on a guarded route afterward. (The
`end session` statement and the client `send` to the logout route are `mio check`-verified here; the
200 / 200 / 403 lifecycle was verified by that lane against a running server with real sessions, which needs an
auth setup the doc lane cannot reproduce.) `end session` is the only thing the browser needs to reach; it does
not manage the session itself, it asks the server to end it.

---

### Deciding on the client: `check / when / otherwise`

A client listener can branch with the same `check / when / otherwise` the server uses:

```mohio
listen for click on #go
    check count
        when count is more than 5
            notify "big"
        otherwise
            notify "small"
    check: done
listen: done
```

A general client `check` is built and checks clean (this reverses an older report that a listener could
branch only on validation predicates). The full condition vocabulary, each form verified with `mio check`
this pass, is:

| Condition | Tests |
|---|---|
| `is "exact"` / `is not "exact"` | exact string equality |
| `is empty` / `is not empty` | the value is blank / non-blank |
| `contains "text"` | the value contains the substring |
| `starts with "text"` / `ends with "text"` | prefix / suffix match |
| `is a valid email` (or `url`, `phone`, `number`, `password`, `required`) | the built-in validators |
| `matches #other` | equals the current value of another element |
| `is checked` | the listened element is a checked checkbox/radio |
| `is more than N` / `is less than N` | numeric comparison |
| `is at least N` / `is at most N` | numeric `>=` / `<=` |
| `is at least N characters` / `is at most N characters` | length `>=` / `<=` |

Two things to know about combining them. State the subject once, then chain further predicates with `and`
/ `or`: `when value contains "@" and is at least 6 characters` checks clean, while repeating the subject
(`... and value is at least 6 characters`) is a syntax error. And `is at least` / `is at most` are
**client-side only** here -- they are real in MioScript's validation grammar but are refused in a
server-side `check`; on the server use `>=` / `<=` inside `(...)`.

```mohio
listen for leaving on #email
    check value
        when value is a valid email and is at most 40 characters
            mark #email as valid
        when value is empty
            unmark #email as valid
        otherwise
            mark #email as invalid
    check: done
listen: done
```

---

### Client variables: a fluid value, `hold` to freeze, and the five operations

Give a value a name right in a handler. A bare assignment is the fluid form, for a value the page keeps
recapturing (the last keystroke, the last selection):

```mohio
listen for typing on #search
    draft value
listen: done
```

`draft value` names a plain, fluid page value, re-captured each time the handler runs. Naming a value is all it
takes; this is the client's everyday variable.

**`hold` freezes a value until you release it, and is never a way to set one.** This is Ron's design from the
start, the same on the client as on the server. Reach for `hold` only when a captured value must not change again
until you deliberately change it:

```mohio
listen for change on #plan
    hold chosen = value
listen: done
```

A held client value changes only through the five operations, mirroring the server's held-variable operations:
`release chosen` unfreezes it, `clear chosen` empties it, `replace chosen with value` changes it (and
`replace "a" with "b" in chosen` replaces within it), `rename chosen to picked` renames it keeping the value, and
`forget chosen` removes it. `release` and `forget` refuse a name that was never frozen, so the fluid-versus-frozen
distinction is taught at the point of the mistake. All of these check clean (verified).

**There is no arithmetic on the client, and that is a ruling, not a gap.** Anything that needs a calculation goes
to the server, where Mohio owns the logic. This keeps one source of truth and stops business rules leaking into
the browser. Other client-variable wish-list items (string building, counting matching elements, client storage)
are unbuilt rather than declined; see the MioScript plan items in the backlog.

---

### Validation, toasts, navigation

**`validate as TYPE`** applies the rules Mohio already owns and marks the field valid or invalid:

```mohio
listen for leaving on #email
    validate as email
listen: done
```

Built-in types carry their defaults (`validate as password` brings min length and masking). You can also
validate against a shape field you declared, `validate as sh.signup.email`.

**`notify "..."`** shows a self-dismissing toast (CSS owns the look via the `mio-notify` class). **Browser
navigation** is `go to "/path"`, `go back`, `reload`, and `scroll to #sel` -- all built (they compile to
`window.location` / `history`). The dotted `nav.*` family (`nav to`, `nav.back`, `nav.forward`,
`nav.root`) is the SEPARATE mobile navigation and is NOT built; do not reach for it yet.

```mohio
listen for click on #home
    go to "/"
listen: done
```

---

### New in 6.0.0: reset, dialogs, media, clipboard, timers, strict validation, sharing

Every form below is verified: each compiles under `mio check` (which now runs MioScript validation), and the
runtime behaviour is proven in the jsdom client battery.

**`reset #form`** clears a form back to its initial values.

```mohio
listen for click on #startover
    reset #signup
listen: done
```

**`alert "..."`** is a real blocking browser alert. It is not the self-dismissing `notify` toast, and it writes
nothing to the console.

```mohio
listen for click on #help
    alert "Saved drafts are kept for 30 days."
listen: done
```

**`confirm "..."`** is a yes/no GATE. The statements after it run only if the user confirms; on cancel the
handler stops there.

```mohio
listen for click on #delete
    confirm "Delete this payment?"
    send #form to "/delete"
listen: done
```

**`copy #element`** copies an element's value to the clipboard. A denied copy ALERTS ("Could not copy...") rather
than failing silently.

```mohio
listen for click on #copykey
    copy #apikey
listen: done
```

**Media: `play`, `pause`, `stop`, and `listen for end of`.** `stop` pauses and rewinds to the start. There is no
toggle verb; `play` and `pause` stay separate by design.

```mohio
listen for click on #listen
    play #episode
listen: done
listen for end of #episode
    show #nextepisode
listen: done
```

**Timers: `every ... as NAME`, and `stop NAME`.** `every` repeats on a schedule under a name, and `stop` cancels
it by that name. A `stop` naming an `every` that was never declared, and two `every` blocks sharing one name,
both refuse at COMPILE time.

```mohio
listen for click on #startpolling
    every 1 seconds as refresh
        request "/tick" into #clock
    every: done
listen: done
listen for click on #stoppolling
    stop refresh
listen: done
```

**A `request ... into` inside an `every` polls politely, with no extra syntax** (verified by running). When a
`request ... into` runs inside an `every`, Mohio makes the poll considerate on its own: it PAUSES while the page
is hidden and resumes when it is shown again, PAUSES while the browser is offline and resumes when it is back,
never starts a new request while the last one is still running, updates the target ONLY when the content actually
changed, and announces each change through an accessible live region (`aria-live="polite"`). A `request ... into`
that is NOT inside an `every` is unchanged: no live region, written on every answer.

**Markup placed after the page loads gets the listeners the page declared** (verified by running). A `listen for`
used to bind only the elements present at load, so a button that arrived later did nothing when clicked, with no
error. Now every binding is remembered and applied to markup as it is placed, once per element, however it
arrives: a fragment answered by `request ... into`, a fragment placed by `send` with `inject result into`, or a
render fragment placed by either. An element that was already there is never bound twice.

**`validate as strict`** runs a whole-form check on submit. On failure it moves focus to the first invalid field
and stops before the next statement, so it gates the submit; it uses the same per-field rules as
`validate as TYPE`.

```mohio
listen for submit on #signup
    validate as strict
    send #signup to "/save"
listen: done
```

**`share`** opens the device share sheet, or hands off to a named destination. The forms are a bare `share`
(share the current page), `share #element` (share that element's own link), `share "address"`, and
`share <held-value>`, each optionally with `on <destination>`. Destinations are kept in a maintained data file
(`mohio_data/share_destinations.json`: linkedin, twitter, x, facebook, reddit, whatsapp, telegram, email).

```mohio
listen for click on #sharepage
    share
listen: done
listen for click on #sharecard
    share #card
listen: done
listen for change on #seed
    link value
listen: done
listen for click on #tweet
    share "https://example.com/r/1" on twitter
listen: done
```

MioScript reads no fields off a held value by design, so `share report.link` (a dotted read off a held value) is
not a form; share the held value itself (`share link`) or the element (`share #card`). All of these compile and
share for real (verified).

### Acting on the right row: row identity

When you render a list and listen on the container, a click has to tell the server WHICH row it hit. Mohio stamps
each rendered row with its identity automatically, so you never pass an id by hand:

- a record row carries its RECORD KEY, under the loop variable's name;
- a loop over plain values carries the VALUE itself as the identity;
- a row with neither carries its 1-based POSITION, flagged positional.

You listen on the container and `send` or `request` as usual; the clicked row's identity rides along, and the
client is given ONLY the identity, never the row's other fields.

```mohio
listen for click on #list
    send #form to "/pick"
listen: done
```

On the server, read it as `request.<loop variable>` (the name you looped with when you rendered the list), and
`request.positional` for a position. So a list rendered by looping `txn` over the rows is read back on the server
as `request.txn`.

### Raw DOM events: the escape hatch

The six intent words above are the mapped set. **Any other event name you write is used as-is, as the raw
DOM event of that name.** `listen for keydown on #box` binds a `keydown` listener; so would `pointermove`,
`wheel`, `dragstart`, or any DOM event the browser knows. This is deliberate -- the emitter's own comment
calls it the escape hatch -- so a rare event the intent words do not cover is still reachable without
leaving MioScript. Verified clean this pass:

```mohio
listen for keydown on #box
    notify "a key went down"
listen: done
```

The trade-off is that the escape hatch is unchecked: a misspelled intent word (`listen for hoverr ...`) is
not caught, because it is indistinguishable from an intentional raw event name, and it binds a listener
that never fires. Reach for a raw event only when no intent word fits.

**Open question for Ron, recorded in the report:** the pass-through is currently public and unrestricted --
any string becomes an event name. It could stay a documented public escape hatch (its value: unmapped
events remain reachable), or be narrowed to a known DOM-event allowlist so a typo fails loud instead of
silently binding a dead listener. This is a design call, flagged, not decided here.

---

### What is genuinely not built

- **Client arithmetic** -- deliberately declined (compute on the server), not a gap.
- **The mobile `nav.*` navigation family** -- declared, fails loud with no handler.
- **`on.change`** -- not built.
- **An explicit prevent-default statement** -- gone; a `submit` handler cancels the native submit for you.
- The rest of the 2026-06-25 client plan (client storage, `for each` over elements, more event/element reads,
  form helpers, geolocation, counts and loops over groups of elements) is unbuilt and tracked as its own
  backlog items. Dialogs, timers, media, clipboard, strict validation and sharing, once on that list, landed
  in 6.0.0 and are covered above.

---

### Common mistakes

- **A `send` block with no `send: done`.** With `on.success` / `on.failure` it is a block and needs its
  closer.
- **Injecting a raw string.** `inject` takes only `result` from a `send`; a literal is refused. Use `put`
  for text.
- **Reaching for arithmetic on the client.** It is not there by design; send the work to the server.
- **`nav.*` for navigation.** Use `go to` / `go back` / `reload`; the `nav.*` mobile family is not built.
