# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
test_scan_crash_loud.py

Guards the fix for the confirmed silent-wrongness defect in `mio check`:
a Layer-3 scanner (or a compile-time compliance guard) that CRASHES during
`mio check` was swallowed by `except Exception: pass`. The command then
reported a clean result for a program nobody finished checking -- the exact
silent failure the language exists to refuse.

Design contract (locked): an advisory scan hiccup must NOT break `mio check`
(the run still completes), but it must NEVER be silent. On crash the command
prints a `[enforce] WARNING ... INCOMPLETE` line to stderr naming the scan.

These tests fault-inject a crash into each guarded scan and assert the warning
fires, and assert a clean file produces NO warning (no false positives).

Run:  PYTHONPATH=$PWD DATABASE_URL=:memory: python3 tests/test_scan_crash_loud.py
"""
import io
import os
import sys
import tempfile
from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout

os.environ.setdefault("DATABASE_URL", ":memory:")
os.environ.setdefault("MOHIO_ENCRYPTION_KEY", "testkey")

import mio  # noqa: E402
import mohio_enforce  # noqa: E402

_p = _f = 0

# A minimal, VERIFIED-clean program: declared var returned from a page.
CLEAN_SRC = 'page at /\n    greeting "hello"\n    give back 200 greeting\npage: done\n'


def _run_check_capturing(src):
    """Run cmd_check on a temp file, return captured (stdout, stderr)."""
    fd, path = tempfile.mkstemp(suffix=".mho")
    os.write(fd, src.encode())
    os.close(fd)
    out, err = io.StringIO(), io.StringIO()
    args = Namespace(file=path, security=False, all=False, langmap=False)
    try:
        with redirect_stdout(out), redirect_stderr(err):
            try:
                mio.cmd_check(args)
            except SystemExit:
                pass  # cmd_check calls sys.exit on real errors; not our concern here
    finally:
        os.unlink(path)
    return out.getvalue(), err.getvalue()


def check(label, got, want=True):
    global _p, _f
    ok = bool(got) is want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    _p += ok
    _f += (not ok)


def main():
    # 1. Layer-3 scanner crash -> loud
    _orig = mohio_enforce.enforce_scans
    mohio_enforce.enforce_scans = lambda ctx, program: (_ for _ in ()).throw(
        RuntimeError("injected scanner crash"))
    try:
        _, err = _run_check_capturing(CLEAN_SRC)
    finally:
        mohio_enforce.enforce_scans = _orig
    check("Layer-3 scanner crash warns on stderr",
          "[enforce] WARNING" in err and "did not finish" in err)

    # 2. never-store (PCI/PII) guard crash -> loud
    _orig_ns = mio._check_never_store
    mio._check_never_store = lambda program: (_ for _ in ()).throw(
        RuntimeError("injected never-store crash"))
    try:
        _, err = _run_check_capturing(CLEAN_SRC)
    finally:
        mio._check_never_store = _orig_ns
    check("never-store guard crash warns on stderr",
          "never-store (PCI/PII) guard" in err)

    # 3. clean file, no injected crash -> NO warning (false-positive guard)
    _, err = _run_check_capturing(CLEAN_SRC)
    check("clean file produces no scan-incomplete warning",
          "[enforce] WARNING" in err, want=False)

    print(f"\n  RESULTS: {_p}/{_p + _f} passed")
    sys.exit(1 if _f else 0)


if __name__ == "__main__":
    main()
