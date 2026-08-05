# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""Runtime errors point at the offending line (2026-07-31, tracker section 4).

Runtime errors used to print `Runtime error <file>` then the message -- no line, near-useless on a
large file. AST nodes defaulted to line 0; the transformer now stamps every node with its source
line (generic `_call_userfunc` hook) and the interpreter tracks the innermost executing node's line,
so a runtime error shows `<file>:<line>` plus the source snippet, matching check-time errors.

Uses the real CLI (`mio run`). Run as a script: `python tests/test_runtime_line_numbers.py`.
"""
import os, subprocess, sys, tempfile

REPO = os.getcwd()
MIO = os.path.join(REPO, "mio.py")
ENV = dict(os.environ, PYTHONPATH=REPO, DATABASE_URL=":memory:",
           PYTHONIOENCODING="utf-8", PYTHONUTF8="1")

_p = _f = 0
def rec(label, ok, detail=""):
    global _p, _f
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{('' if ok else '  -- ' + detail)}")
    _p += ok; _f += (not ok)

def run(src):
    fd, path = tempfile.mkstemp(suffix=".mho"); os.write(fd, src.encode("utf-8")); os.close(fd)
    try:
        r = subprocess.run([sys.executable, MIO, "run", path], cwd=REPO, env=ENV,
                           capture_output=True, text=True, timeout=60)
        return r.returncode, (r.stdout + r.stderr), os.path.basename(path)
    finally:
        os.unlink(path)

# A runtime error (bare miomail.send fails loud) placed on line 3, then on line 5, to prove the line
# is the OFFENDING one -- not a constant, not "last statement" by accident.
c, out, base = run('show "one"\nshow "two"\nmiomail.send\n')
rec("runtime error names the line (:3) and shows the snippet",
    f"{base}:3" in out and "3 |" in out and "miomail.send" in out, out[-300:])

c, out, base = run('show "a"\nshow "b"\nshow "c"\nshow "d"\nmiomail.send\n')
rec("the line tracks the actual error location (:5, not :3)",
    f"{base}:5" in out and "5 |" in out, out[-300:])

# The error still names the problem and the fix (no regression in message quality).
c, out, base = run('miomail.send\n')
rec("still names the problem and the fix", "recipient" in out.lower() and "miomail.send to" in out, out[-200:])

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
