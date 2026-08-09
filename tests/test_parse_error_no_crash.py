# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
test_parse_error_no_crash.py

Guards the fix for a fail-loud-integrity defect in the parse-error printer:
`_bare_service_root` indexed `lines[line - 1]` guarding only the UPPER bound
(`line > len(lines)`). The parser can report a NON-POSITIVE line (-1 / 0) when
it cannot localize an error (e.g. end-of-input on a file whose last line has no
trailing newline). A negative line wrapped to a bogus index and crashed the
error printer with a Python IndexError traceback -- a pioneer got a stack trace
instead of a legible "Syntax error", the opposite of the language's promise.

Repro that used to crash:  a single line `ai.create text "x"` with NO trailing
newline -> parser reports line == -1 -> lines[-2] on a 1-element list -> IndexError.

Contract: `_bare_service_root` returns None for any non-locatable line and never
raises; the happy path (a bare service root on a real line) still detects.

Run:  PYTHONPATH=$PWD DATABASE_URL=:memory: python tests/test_parse_error_no_crash.py
"""
import os
import sys

os.environ.setdefault("DATABASE_URL", ":memory:")
os.environ.setdefault("MOHIO_ENCRYPTION_KEY", "testkey")

import mio  # noqa: E402

_p = _f = 0


def check(label, got, want=True):
    global _p, _f
    ok = bool(got) is want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    _p += ok
    _f += (not ok)


def _no_raise(source, line):
    """Return ('ok', result) or ('raised', exc)."""
    try:
        return ("ok", mio._bare_service_root(source, line))
    except Exception as e:  # the bug raised IndexError here
        return ("raised", e)


def main():
    src1 = 'ai.create text "x"'          # 1 line, no trailing newline (the repro)

    # 1-3. Non-positive / out-of-range lines must return None, never raise.
    for lbl, ln in [("negative line (-1, the repro)", -1),
                     ("zero line (0)", 0),
                     ("line past end (99)", 99)]:
        status, res = _no_raise(src1, ln)
        check(f"{lbl} does not crash", status == "ok")
        check(f"{lbl} returns None", status == "ok" and res is None)

    # 4. Happy path intact: a bare service root on a real line is still detected.
    status, res = _no_raise('miohttp "x"', 1)
    check("bare service root on line 1 still detected",
          status == "ok" and res is not None and res[0] == "miohttp")

    # 5. A normal variable line returns None (no false positive), no crash.
    status, res = _no_raise('greeting "hello"', 1)
    check("ordinary line returns None", status == "ok" and res is None)

    print(f"\n  RESULTS: {_p}/{_p + _f} passed")
    sys.exit(1 if _f else 0)


if __name__ == "__main__":
    main()
