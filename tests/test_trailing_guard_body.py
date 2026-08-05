# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
test_trailing_guard_body.py

Guards the fix that catches a silent trap: a trailing `if` / `unless` guard with a
line indented BENEATH it. A trailing guard never opens a block, so the indented
line runs unconditionally even though it looks guarded by the condition (verified
at runtime: with the condition false, the guarded line is suppressed but the
indented line still runs). The AST flattens the indented line into a sibling, so
the check runs on the parse tree where indentation is still visible.

  * `show "x" if c` then an INDENTED line          -> ERROR (the trap)
  * `show "x" unless c` then an INDENTED line       -> ERROR
  * `show "x" if c` then a same-indent line         -> OK (valid trailing guard)
  * two same-indent trailing guards                 -> OK

Run:  PYTHONPATH=$PWD DATABASE_URL=:memory: python3 tests/test_trailing_guard_body.py
"""
import os
import subprocess
import sys
import tempfile

env = dict(os.environ, PYTHONPATH=os.getcwd(), DATABASE_URL=":memory:",
           MOHIO_ENCRYPTION_KEY="testkey")
_p = _f = 0


def case(label, src, want_exit, want_msg=None):
    global _p, _f
    fd, path = tempfile.mkstemp(suffix=".mho")
    os.write(fd, src.encode())
    os.close(fd)
    r = subprocess.run([sys.executable, "mio.py", "check", path], env=env,
                       capture_output=True, text=True)
    os.unlink(path)
    ok = (r.returncode == want_exit)
    if want_msg is not None:
        ok = ok and (want_msg in (r.stdout + r.stderr))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label} (exit {r.returncode}, want {want_exit})")
    _p += ok
    _f += (not ok)


def main():
    MSG = "runs unconditionally"

    case("trailing if + indented body fails loud",
         'hold x = 5\nshow "big" if x is more than 3\n    show "extra"\n',
         want_exit=1, want_msg=MSG)

    case("trailing unless + indented body fails loud",
         'hold x = 5\nshow "big" unless x is more than 3\n    show "extra"\n',
         want_exit=1, want_msg=MSG)

    case("valid trailing if, same-indent sibling passes",
         'hold x = 5\nshow "big" if x is more than 3\nshow "small"\n',
         want_exit=0)

    case("two same-indent trailing guards pass",
         'hold x = 5\nshow "big" if x is more than 3\n'
         'show "small" unless x is more than 3\n',
         want_exit=0)

    print(f"\n  RESULTS: {_p}/{_p + _f} passed")
    sys.exit(1 if _f else 0)


if __name__ == "__main__":
    main()
