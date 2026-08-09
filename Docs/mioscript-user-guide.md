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
