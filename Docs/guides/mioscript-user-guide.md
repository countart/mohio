<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# MioScript — Mohio in the browser

MioScript is Mohio that runs on the page after it loads. It is the behaviour
layer: it listens for what the person does and changes the live page in response.
It is not JavaScript with a skin, and it is not where you author the look. That
stays in the display layer and CSS.

The test for every word is simple: would you say it to a person doing the task?
"Mark the email invalid." "Put the result in the box." "When they click." Those
are the words MioScript uses.

---

## Listening

You react to events with `listen for`, naming the thing that happens and the
element it happens on. The block closes with `listen: done`.

```
listen for click on #buy
    notify "added to cart"
listen: done
```

The `#id` selector is a bare token, no quotes. Element ids are the one place `#`
appears in Mohio.

The intent words map to browser events so you do not have to: `click`, `typing`
(input), `leaving` (blur), `focus`, `hover`, `press`, `submit`. A bare
`listen for #search` infers the natural event for that element.

Event data reads are bare, no `the`:

```
listen for typing #search
    hold q = value
listen: done
```

`value`, `key`, `checked`, `x`, `y` are read directly. Write `value`, not
`the value`.

To wait for a pause in typing before acting (debounce), add `on.pause`:

```
listen for typing #search
    on.pause 300 ms
    send #search-form to "/suggest"
        on.success
            put result.html into #suggestions
listen: done
```

---

## Waiting for someone to leave: on.idle

`on.pause` waits for one kind of activity, on one element, to stop. `on.idle` is
its whole-page cousin: it waits for no sign of a person anywhere on the page,
of any kind, for a stretch of time. Session timeout, auto-logout, "are you
still there," a compliance auto-lock, all come from the same construct.

```
on.idle 25 minutes
    show #relogin
on.idle: done
```

One clock is shared across every kind of activity, mouse movement, a key
press, a scroll, a click, a touch. Any of them resets it. That matters: a
timer per event would log someone out mid-sentence the moment they stopped
moving the mouse to read, even though they never left. `on.idle` only fires
once *everything* has been quiet together for the whole stretch. It is a
top-level declaration, written once outside `listen for`, not attached to any
one element, because it is watching the whole document.

`#relogin` is the same `#id` selector used everywhere else in MioScript (see
"Listening," above) and needs the matching id somewhere in the page:

```
<div id="relogin" hidden>Still there?</div>
```

**Duration format.** A number and a full word, with a space: `25 minutes`,
`30 seconds`, `2 hours`. Not `25min`, that fails to parse. The full list:
milliseconds, seconds, minutes, hours, days, weeks, months, and years, each
singular or plural. A month and a year are not exact lengths, so they are
read as an ordinary timer would read them, 30 days and 365 days, which is
long enough for this and not meant to stand in for calendar arithmetic.

**`on.idle` only covers the browser half.** It shows or hides something on
the page; it does not by itself decide who gets let back in. That is the
server's job, and it uses no new construct, only the ones Mohio already has:
`random.token` to generate a real, unguessable token, `miomail.send` to
deliver it, and `grant role` once a real one comes back. The full recipe,
both halves together, lives at `cookbook/session-timeout.mho` and runs as
written:

```
// The browser half. Nobody has moved, typed, scrolled, clicked or touched
// anywhere on this page for 25 minutes, so cover it and ask.
on.idle 25 minutes
    show #relogin
on.idle: done

listen for
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

    // Ask for a link. The token is what proves it was really them, so it is
    // generated here and never chosen by the person asking.
    new sh.Relogin at /relogin
        link_token random.token length 32
        save to db.relogin_links
            email relogin.email
            token link_token
        save: done
        miomail.send
            to relogin.email
            subject "Your link back to the ledger"
            body ("Open this to carry on: /back with the token " & link_token)
        give back [200] "Check your email for the link back."
    new: done

    // Come back. An unknown token is refused rather than ignored, so a guessed
    // one fails loudly instead of quietly doing nothing.
    new sh.Back at /back
        retrieve.one link from db.relogin_links
            match token to back.token
        retrieve.one: done
        // A token nobody was sent finds NOTHING, and finding nothing is a normal
        // empty result, not an error. So the test is `is empty`, not `on.failure`:
        // on.failure means the database itself went wrong, and it would never fire
        // for a wrong token, which would let one straight through.
        check link
            when link is empty
                give back [401] "That link is not one we sent."
            otherwise
                grant role "member"
                give back [200] "Welcome back."
        check: done
    new: done
listen: done
```

Run it with `mio serve cookbook/session-timeout.mho`, no email account needed:
with no provider configured, `miomail` prints instead of sending, so the whole
flow runs end to end on a laptop with nothing set up.

---

## Changing state — mark

`mark <selector> as <state>` puts a symbolic state on an element. It is a flag
with no value that CSS reads. The behaviour names the state, the stylesheet owns
what the state looks like.

```
mark #tab as active
mark #email as invalid
mark #drawer as open
```

`unmark` removes a state, `toggle ... as <state>` flips it on and off. This is how
all motion happens too: open a drawer, shake an invalid field, highlight a row, by
marking a state and letting a CSS transition do the movement. No timing curves or
keyframes ever live in Mohio.

`mark` replaces the old idea of adding a class or setting a style by hand.

---

## Placing values — put and inject

Both write a value to a destination. The destination is read by context: a
selector targets a page element, a name or dotted path targets a variable or
state.

- `put <value> into <dest>` is durable. Place it and leave it.
- `inject <value> into <dest>` is transient. A flash that will be replaced or
  vanish, like ghost text or a streaming token.

```
put response into #note
put 100 into player.health
inject suggestion into #search
```

The rule of thumb: surprised it stayed means it was `inject`. Surprised it
vanished means it was `put`.

Safety is built in. Markup you write as an author may render as HTML, but anything
that comes from the runtime or an event is always forced to plain text and can
never become markup.

---

## Validation — validate as

You name the type or the shape, and the engine applies the rules it already owns,
then marks the field valid or invalid for you.

```
listen for leaving #email
    validate as email
listen: done
```

Built-in types carry their own defaults: `validate as password` brings min length
8 and masking, including the show/hide eye, with nothing to wire. You can also
validate against a shape field you already declared:

```
validate as sh.signup.email
```

Same `as` connector either way. For custom logic, the escape hatch is
`check ... mark`.

---

## Client variables — hold

`hold` keeps a value on the client so you can capture it now and recall it later.

```
listen for typing #search
    hold q = value
listen: done

listen for click on #recall
    put q into #results
    notify q
listen: done
```

Client variables capture and recall only. There is no arithmetic on the client by
design. Anything that needs a calculation goes to the server, where Mohio owns the
logic. This keeps one source of truth and stops business rules leaking into the
browser.

---

## Talking to the server — send

`send` is the bridge. It serializes a form and POSTs it, then gives you the parsed
response in `result`.

```
listen for submit on #signup
    send #signup to "/signup"
        on.success
            put result.message into #status
            go to "/welcome"
        on.failure
            mark #signup as error
            put result.error into #status
listen: done
```

`on.success` and `on.failure` are the same lifecycle words the server uses.
`result` is the parsed response inside the branches, and `result.message`,
`result.error`, and so on read its fields. All of it renders as text, never as
markup, so a hostile response cannot inject anything.

You do not block the browser's native submit yourself. A submit handler cancels
the default reload automatically, because letting both fire would double-submit.
This is submit-only. Reset and every other event keep their native behaviour.

For the form events, listen for `submit` and `reset`, never a click on the button.
The form events fire for both mouse and keyboard.

---

## Toasts — notify

`notify` shows a transient message that dismisses itself.

```
listen for submit on #contact
    send #contact to "/contact"
        on.success
            notify result.message
        on.failure
            notify "something went wrong"
listen: done
```

It creates an element with the class `mio-notify`, so CSS owns the look, the same
rule as `mark`. The text is always safe.

---

## Navigation

```
go to "/welcome"
scroll to #section
```

---

## What MioScript does not do

It does not author the look (CSS does), it does not do client-side arithmetic (the
server does), and it does not expose browser APIs by name. If a word sounds like
`addEventListener` or `innerHTML` or `set style`, it is not a MioScript word. If it
sounds like something you would say to a person, it is.
