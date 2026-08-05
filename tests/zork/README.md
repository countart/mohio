# Zork demo

The Zork text adventure, the flagship reference implementation for Mohio. Lives in its own folder so the auto-applied `journey.mho` spine scopes to Zork only.

## Files

| File | What it is |
|---|---|
| `index.mho` | The router. Declares the database, the listener, and every game command. |
| `index.html` | The web UI. Served automatically as this app's home page (see below). |
| `journey.mho` | The shared spine, auto-applied to every `.mho` in this folder with no include line. Holds the `narrator` and `resolve_noun` `ai.decide` blocks. |
| `_cheats.mho` | Easter eggs and cheats, pulled in by an `include` from the router. |
| `seed_zork.json` | Seed data for the game world. |

## How it is served

The Mohio runtime serves static files from **the directory of the `.mho` it was given**, and looks there for `index.html` (or `home.html`) as the home page. So `index.html` must live in this folder, next to `index.mho`.

The runtime does not special-case any demo filename. An app with no root route and no `index.html` gets a neutral placeholder page instead.

Run it locally:

    python mio.py serve tests/zork/index.mho --port 8080

`index.mho` declares `connect db as postgres from env.DATABASE_URL`, so a `DATABASE_URL` must be set. Mohio will not quietly fall back to SQLite.

## Deployment

Railway builds from the repo-root `Dockerfile`, and **its `CMD` is authoritative**. The `Procfile` is ignored whenever a Dockerfile is present, but keep the two in sync so either path works:

    Dockerfile CMD: python mio.py serve tests/zork/index.mho --port $PORT --host 0.0.0.0 --ai
    Procfile:  web: python mio.py serve tests/zork/index.mho --port $PORT --host 0.0.0.0 --ai

Session state persists between commands via the `X-Session-ID` header.

## What the journey spine proves

The router invokes `ai.decide narrator` and `ai.decide resolve_noun`, which are defined **only** in `journey.mho`. The build prepends the spine before the listener runs, so those invocations resolve against the auto-applied definitions with no include line. Zork serving correctly is the live proof that journey auto-apply works on a real app.

## Extraction rule (what can move to an include page)

A handler that only `give back`s a response, or that persists via direct database writes, extracts cleanly into a task in an include page.

A handler that mutates a **router-scope variable** does not. A task has its own scope and only its `give back` returns, so the state change would not persist. Those stay in the router.