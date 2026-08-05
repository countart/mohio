<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md. -->
# Masking card data ([pci])

Mark a field `[pci]` and Mohio treats it as cardholder data: it is encrypted at rest
and shown only as its last four digits. You never write masking code, and the full
number can never appear on a screen, in a response, or in a log by accident.

```
shape Card
    ref as text
    number as text [pci]
shape: done
```

## What you see

- **At rest:** stored encrypted. The raw column is a sealed `enc:v1:...` blob.
- **On display:** masked to the last four. `show card.number` prints `****1234`;
  `give back card.number` returns `****1234`.
- **For real use:** the value is full internally. Reading it into a variable, comparing
  it, hashing it, or handing it to a payment processor uses the full number.

## The mask follows the value (taint)

The important part: masking is not tied to the field name, it rides the value. If you
weave a card number into another string, the result is still masked on display:

```
show ("Card: " & card.number)      // ****1234  — the full PAN cannot leak here
give back 200 ("ref " & card.number)   // ****1234
```

So a full card number cannot escape by being concatenated into a message, a log line,
or a response. This is deliberate: string-building is treated as heading for display.

### Using the full number

Because any built string is masked, do not build a display string when you actually
need the full number. Pass the **raw value** to the thing that needs it:

```
miohttp.post "https://processor.example/charge"
    body card.number        // full number goes to the processor, not a built string
miohttp: done
```

And if you *want* a label next to the masked number, mask explicitly so the intent is
on the page:

```
show ("Card ending " & (card.number right 4))   // Card ending 1234
```

## Note

The current rule masks a `[pci]` value on **any** display, including give-back, so a
concatenated value is masked there too. This keeps the guarantee simple and safe. It
may be revisited later (for example, letting `give back` to a trusted service stay full
while `show` always masks). If that changes, this page and the behavior change together.
