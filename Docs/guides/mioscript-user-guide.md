<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# MioScript -- moved to the manual chapter

**This short guide has been retired as a source of truth.** It drifted from what runs three times (a
selector-only listener's inferred event, whether `checked` is read directly, and the network-failure shape), and
each time the manual chapter was the correct one. So the guide is now a pointer, not a second description to keep
in step.

**Read the MioScript chapter instead:** `Docs/manual/mohio-manual-part5-ch24-mioscript.md`. It is the complete,
current reference, every example `mio check`-verified, covering `listen for` and its events, `check`/`when`,
`hold`, `put`/`inject`/`append`, `request` vs `send` (with the normalized failure shape), `validate`,
navigation, `on.pause`/`on.idle` and the full session-timeout recipe, and what is not built.

The one piece of content that lived only here, the two-halves session-timeout recipe (`on.idle` on the browser
plus the server relogin flow with `random.token`, `miomail.send` and `grant role`), has been folded into that
chapter's `on.idle` section, so nothing was lost in retiring this guide.

For the philosophy behind the refusals MioScript emits, see `Docs/manual/mohio-manual-why-mohio-refuses.md`.
