# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
Guard: `mio check <dir>` does not report a clean folder while ignoring a broken file.

Include targets (`_name.mho`) are never routed, so they are not checked as pages. They
used to be skipped entirely, which made "All N files passed" a claim about a folder
containing a file nobody had looked at -- a syntax error in one surfaced only when
something included it, or at runtime.

They are now PARSED but not scanned. A fragment legitimately leans on the file that
includes it (a shape, a variable declared there), so running the semantic scans on it
standalone would invent errors that are not real. Parsing is context-free, so a syntax
error is a syntax error wherever the file sits.

The false-positive case is the one worth guarding hardest: an earlier version of this
loop caught every exception, so a NameError inside the loop itself was reported as the
file's fault and a perfectly good include target came back "failed". A checker that
invents failures is worse than one that misses them.
"""
import os, shutil, subprocess, sys, tempfile

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

GOOD_PAGE = 'page at /\n    show "home"\npage: done\n'
GOOD_PRIV = 'page at /cheat\n    show "codes"\npage: done\n'
BROKEN     = 'page at /cheat\n    show "codes"\n'          # never closed
FRAGMENT   = 'hold greeting "hi"\nhold farewell "bye"\n'    # leans on its includer


def run_dir(files):
    d = tempfile.mkdtemp(prefix="mohio_dircheck_")
    try:
        for name, body in files.items():
            with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
                fh.write(body)
        r = subprocess.run([sys.executable, MIO, "check", d],
                           capture_output=True, text=True, env=ENV, timeout=300)
        return r.returncode, (r.stdout + r.stderr)
    finally:
        shutil.rmtree(d, ignore_errors=True)


print("test_check_include_targets")

# The false-positive case first: a good include target must never be blamed.
code, out = run_dir({"index.mho": GOOD_PAGE, "_cheats.mho": GOOD_PRIV})
check("a valid include target does not fail the folder", code, 0)
check("  and the summary says it was looked at",
      "include target" in out, True)

# The gap this closes.
code, out = run_dir({"index.mho": GOOD_PAGE, "_cheats.mho": BROKEN})
check("a broken include target fails the folder", code, 1)
check("  and the broken file is named", "_cheats.mho" in out, True)

# A fragment is parsed, not scanned. Standalone it has no includer, so any semantic
# scan would have nothing to resolve against -- it must still pass.
code, _ = run_dir({"index.mho": GOOD_PAGE, "_bits.mho": FRAGMENT})
check("a fragment include target passes", code, 0)

# Routable files are unaffected either way.
code, _ = run_dir({"index.mho": GOOD_PAGE})
check("a folder with no include targets still passes", code, 0)
code, _ = run_dir({"index.mho": BROKEN, "_cheats.mho": GOOD_PRIV})
check("a broken routable file still fails", code, 1)

print(f"\nRESULTS: {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
