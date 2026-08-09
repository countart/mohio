# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
Guard: the parse cache covers include targets, and is keyed on content.

A warm start that skips the include is the worst kind of wrong: it works, it looks
cached, and it quietly pays most of the cost the cache was supposed to remove. On the
real zork tree that was ~7.3s of every boot spent re-parsing `_cheats.mho`, against
~0.4s for the same app with no include.

Two separate defects produced it, and both are guarded here:
  * `warmup` swept the directory for pages and skipped `_name.mho`, so no cache was
    ever written for an include target.
  * `_resolve_includes` called the parser directly and never looked for a cache, so
    even a cache that existed went unread.

Also guarded: the cache is validated on file CONTENT (sha256) plus a fingerprint of the
compiler, never on modification time. That is what lets a cache be built once and
shipped in the bundle -- a host that re-injects the app files gives every one a fresh
mtime, which would throw away an mtime-keyed cache on every boot.
"""
import os, shutil, subprocess, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIO = os.path.join(ROOT, "mio.py")
ENV = dict(os.environ, PYTHONPATH=ROOT, DATABASE_URL=":memory:",
           MOHIO_ENCRYPTION_KEY="testkey")

_passed = _failed = 0
def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1; print(f"  ok   {label}")
    else:
        _failed += 1; print(f"  FAIL {label}: got {got!r} want {want!r}")

# Small enough to run fast, real enough to exercise include resolution.
PAGE = ('include "_bits.mho"\n\n'
        'page at /\n    show "home"\npage: done\n')
BITS = 'page at /extra\n    show "extra"\npage: done\n'


def run(args, cwd, timeout=400):
    r = subprocess.run([sys.executable, MIO] + args, cwd=cwd, env=ENV,
                       capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr)


print("test_include_cache")

d = tempfile.mkdtemp(prefix="mohio_inccache_")
try:
    open(os.path.join(d, "index.mho"), "w").write(PAGE)
    open(os.path.join(d, "_bits.mho"), "w").write(BITS)

    code, out = run(["warmup", "."], d)
    check("warmup succeeds on a folder with an include target", code, 0)

    inc_cache = os.path.join(d, "_bits.mho.cache")
    page_cache = os.path.join(d, "index.mho.cache")
    check("the page is cached", os.path.exists(page_cache), True)
    check("the INCLUDE TARGET is cached too", os.path.exists(inc_cache), True)
    check("warmup said it cached the include target", "_bits.mho -- AST cached" in out,
          True)

    # The include cache must actually be READ. Removing only that file, while leaving
    # the page cache in place, must change the work done -- if resolution ignored the
    # cache, deleting it would change nothing.
    t0 = time.monotonic()
    code, _ = run(["check", "index.mho"], d)
    warm_with_include = time.monotonic() - t0
    check("checking the page with everything cached passes", code, 0)

    os.remove(inc_cache)
    t0 = time.monotonic()
    code, _ = run(["check", "index.mho"], d)
    warm_without_include = time.monotonic() - t0
    check("it still passes without the include cache", code, 0)
    check("removing the include cache costs real time (so the cache was being read)",
          warm_without_include > warm_with_include, True)

    # Content keying, not mtime. Shipping a cache to a new folder with fresh
    # timestamps must still hit.
    run(["warmup", "."], d)
    d2 = tempfile.mkdtemp(prefix="mohio_inccache_ship_")
    try:
        for f in os.listdir(d):
            shutil.copy2(os.path.join(d, f), os.path.join(d2, f))
        for f in os.listdir(d2):
            os.utime(os.path.join(d2, f), None)   # what re-injection does
        t0 = time.monotonic()
        code, _ = run(["check", "index.mho"], d2)
        shipped = time.monotonic() - t0
        check("a shipped cache still passes after fresh mtimes", code, 0)
        check("  and is still fast, so mtime is not the key",
              shipped < warm_without_include, True)
    finally:
        shutil.rmtree(d2, ignore_errors=True)

    # A changed include must never be replayed from a stale cache.
    with open(os.path.join(d, "_bits.mho"), "a") as fh:
        fh.write('\npage at /more\n    show "more"\npage: done\n')
    code, _ = run(["check", "index.mho"], d)
    check("an edited include target still checks clean", code, 0)
finally:
    shutil.rmtree(d, ignore_errors=True)

print(f"\nRESULTS: {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
