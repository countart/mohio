# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Onboarding: a single leading UTF-8 BOM (U+FEFF) is stripped, not rejected.

Windows tools add a BOM by default (PowerShell `Out-File -Encoding utf8`, Notepad, some
editors), so a newcomer would otherwise get a line 1 col 1 "Non-ASCII character" error for an
invisible byte they never typed. A BOM is file metadata, not code -- strip exactly one leading
U+FEFF before the ASCII gate. Non-ASCII ANYWHERE else must still fail loud.

Run as a script: `python tests/test_bom_strip.py` (exit 0 = pass).
"""
import os, subprocess, sys, tempfile

REPO = os.getcwd()
MIO = os.path.join(REPO, "mio.py")
ENV = dict(os.environ, PYTHONPATH=REPO, DATABASE_URL=":memory:",
           PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
BOM = "﻿"

_p = _f = 0

def _record(label, ok, detail=""):
    global _p, _f
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{(' -- ' + detail) if (detail and not ok) else ''}")
    _p += ok; _f += (not ok)

def _run(cmd, src):
    fd, path = tempfile.mkstemp(suffix=".mho")
    os.write(fd, src.encode("utf-8")); os.close(fd)
    try:
        r = subprocess.run([sys.executable, MIO, cmd, path], cwd=REPO, env=ENV,
                           capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr
    finally:
        os.unlink(path)

# 1. a BOM-prefixed file parses clean and runs.
code, out = _run("check", BOM + 'show "hi"\n')
_record("BOM-prefixed file passes check (no Non-ASCII error)",
        code == 0 and "Non-ASCII" not in out, f"exit={code}\n{out[-300:]}")

rc, rout = _run("run", BOM + 'show "hi"\n')
_record("BOM-prefixed file runs and prints its output", rc == 0 and "hi" in rout,
        f"exit={rc}\n{rout[-300:]}")

# 2. a genuine non-ASCII character in an identifier STILL fails loud.
code, out = _run("check", 'shów "hi"\n')       # 'shów'
_record("non-ASCII in an identifier still fails loud", code == 1 and "Non-ASCII" in out,
        f"exit={code}\n{out[-300:]}")

# 3. a BOM followed by non-ASCII: the BOM is stripped, then the real error still fires.
code, out = _run("check", BOM + 'shów "hi"\n')
_record("BOM stripped, then non-ASCII still fails loud",
        code == 1 and "Non-ASCII" in out and "U+00F3" in out, f"exit={code}\n{out[-300:]}")

# 4. only ONE leading BOM is stripped; a second (or a mid-line U+FEFF) still fails loud.
code, out = _run("check", BOM + BOM + 'show "hi"\n')
_record("a second leading BOM still fails loud (only one stripped)",
        code == 1 and "Non-ASCII" in out, f"exit={code}\n{out[-300:]}")

# 5. a normal ASCII file is unaffected.
rc, rout = _run("run", 'show "plain"\n')
_record("plain ASCII file unaffected", rc == 0 and "plain" in rout, f"exit={rc}")

# 6. the strip is limited to U+FEFF (M10 gap regression). A file starting with a non-BOM
# non-ASCII character (é, U+00E9) must STILL fail the ASCII gate -- the strip must not be widened
# to swallow any leading non-ASCII char. Mutation testing (2026-07-31) found the suite did not
# cover this; widening the strip silently ate a leading é. Correct today; lock it in.
code, out = _run("check", "éshow \"hi\"\n")
_record("a leading non-BOM non-ASCII char still fails the ASCII gate (strip is BOM-only)",
        code == 1 and "Non-ASCII" in out, f"exit={code}\n{out[-300:]}")

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
