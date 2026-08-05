# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
Guard: `_journey.mho` fails loud instead of silently not being a spine.

The journey is found by exact filename, and a leading underscore keeps a file out of
routing. Put together, `_journey.mho` is not a journey at all -- the sector floor,
shared connections and compliance it declares simply stop applying, and nothing is
printed. A silent loss of the compliance floor is the worst shape a failure can take,
so it stops the build.

Verified by running the CLI rather than calling the function, because the exit code is
the thing a deploy actually reads.
"""
import os, sys, shutil, subprocess, tempfile

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

PAGE = 'page at /\n    show "home"\npage: done\n'
SPINE = 'page at /fromjourney\n    show "spine"\npage: done\n'


def run_check(files):
    """Write `files` into a fresh directory and run `mio check` on index.mho."""
    d = tempfile.mkdtemp(prefix="mohio_journey_")
    try:
        for name, body in files.items():
            with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
                fh.write(body)
        r = subprocess.run([sys.executable, MIO, "check", os.path.join(d, "index.mho")],
                           capture_output=True, text=True, env=ENV, timeout=180)
        return r.returncode, (r.stdout + r.stderr)
    finally:
        shutil.rmtree(d, ignore_errors=True)


print("test_journey_underscore")

code, out = run_check({"index.mho": PAGE, "_journey.mho": SPINE})
check("`_journey.mho` alone fails the build", code, 1)
check("the message names the file", "_journey.mho" in out, True)
check("the message says what to rename it to", "journey.mho" in out, True)
check("the message says why it matters",
      any(w in out.lower() for w in ("sector", "compliance")), True)

code, _ = run_check({"index.mho": PAGE, "journey.mho": SPINE, "_journey.mho": SPINE})
check("a real journey.mho alongside it is fine", code, 0)

code, _ = run_check({"index.mho": PAGE, "journey.mho": SPINE})
check("journey.mho alone is fine", code, 0)

code, _ = run_check({"index.mho": PAGE})
check("no journey at all is fine", code, 0)

code, _ = run_check({"index.mho": PAGE, "_partial.mho": SPINE})
check("an ordinary underscore file is untouched", code, 0)

print(f"\nRESULTS: {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
