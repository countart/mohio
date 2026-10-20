# Shipping the parse cache

`mio check` and `mio serve` parse a `.mho` file once and keep the parse tree in a `<name>.mho.cache`
file beside the source. A warm check reads that tree instead of re-parsing, which is why Stage 10
checks in about 1.5 seconds warm but takes over a minute cold. The cache is portable: build it once,
ship it beside the source, and a deploy, a CI job, a fresh clone or a demo machine gets the warm
time on its first check instead of the cold one.

## What a `.mho.cache` holds

A small pickle: the parse tree, plus three keys that must all match for the tree to be used.

- `hash` -- a hash of the source file's own content. Edit the source and the cache is ignored.
- `parse` -- a fingerprint of everything that decides the parse tree: the grammar, the pretokenizer,
  the symbol-table extractor, the langmap files, the reserved-word set, AND the Lark version and the
  Python major.minor that pickled the tree. Change any of these and the cache is ignored.
- `app` -- a fingerprint of the whole app's declared names (the page, its `include` targets, and
  `journey.mho` in the same folder). Change a declaration in an include and the page re-parses.

None of these is a machine path. A cache is not tied to the directory it was built in, so it travels.

## Does it travel?

Yes, to a machine that runs the SAME Mohio compiler, the SAME Lark version and the SAME Python
minor version -- which is what a pinned `requirements` file gives a build server and a deploy target.
Ship the WHOLE app folder (the page, every include, `journey.mho`, and their `.cache` files); the
`app` key is computed from the app's other files, so a page shipped without its includes re-parses.

If the target differs in Lark, Python or any compiler file, its `parse` fingerprint differs, the
cache is a clean MISS, and the file re-parses. It is never loaded against the wrong compiler.

## Building the cache: `mio warmup`

    mio warmup <folder>

warms every `.mho` under `<folder>`, writing a `<name>.mho.cache` beside each. Run it at build time
(a Dockerfile step, a CI job, a release script) after the source is in place. It is the cold parse
paid once, on the build machine, instead of on every target.

    mio warmup <file>      # a single file
    mio warmup <folder>    # every .mho in the tree

`mio check <file>` and `mio serve <file>` also write the cache as a side effect of a normal run, so a
warm-by-first-use also works; `mio warmup` just does it deliberately, ahead of the first request.

## Shipping layout

Keep each `.cache` beside its source and carry both:

    app/
      index.mho          index.mho.cache
      journey.mho        journey.mho.cache
      _brand.mho         _brand.mho.cache
      risk.mho           risk.mho.cache

A CI or deploy step is two lines: build the source into place, then `mio warmup app/`. The `.cache`
files travel with the source in the same artifact. Nothing else is needed, and nothing is tied to a
platform.

## Safety

A cache is used ONLY when its `hash`, `parse` and `app` keys all match. A stale source, a different
compiler, a different Lark or Python, or a changed include each make it a clean miss that re-parses.
A corrupted `.cache` file is ignored and the file re-parses; it can never yield a wrong tree or hide
an error. The worst a bad cache can do is cost a re-parse, never a wrong answer.
