# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
test_docs_compile.py

Every Mohio snippet in the docs gets compiled. If a doc shows code that no longer works, this
goes red.

Docs rot silently. That is the whole problem: a README written in April still LOOKS right in
July, and the next person copies it. The README was carrying `check: done as skill_level` (a form
retired weeks ago) and `connect fraud_providers` (never a real form -- it is `using`). Both read
perfectly fine to a human. Only the compiler knew.

So the compiler checks the docs now. A doc claim that the compiler refuses is a bug in the doc.
"""
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
env = dict(os.environ, PYTHONPATH=ROOT, DATABASE_URL=":memory:")

DOCS = ["README.md", "CLAUDE.md", "DRIFT.md", "TESTING.md", "start-here/README.md"]

# Some blocks are deliberately WRONG -- they show the drift we are warning against. A block is
# skipped only if it says so out loud, right there in the code.
WRONG_ON_PURPOSE = ("// WRONG", "// RETIRED", "// LOUD", "# WRONG", "// this is the",
                    "// REDUNDANT")

_p = _f = 0

for doc in DOCS:
    if not os.path.exists(doc):
        continue
    text = open(doc, encoding="utf-8").read()
    blocks = re.findall(r"```(?:mohio|mio)\n(.*?)```", text, re.S)
    for i, block in enumerate(blocks, 1):
        if not block.strip():
            continue
        if any(marker in block for marker in WRONG_ON_PURPOSE):
            continue                      # a counter-example, on purpose
        fd, path = tempfile.mkstemp(suffix=".mho")
        os.write(fd, block.encode())
        os.close(fd)
        r = subprocess.run([sys.executable, "mio.py", "check", path],
                           env=env, capture_output=True, text=True)
        os.unlink(path)
        ok = r.returncode == 0
        _p += ok
        _f += not ok
        if not ok:
            first = block.strip().splitlines()[0][:48]
            err = [l.strip()[2:] for l in (r.stdout + r.stderr).splitlines()
                   if l.strip().startswith("x ")]
            print(f"  [FAIL] {doc} block {i}: {first}")
            if err:
                print(f"         {err[0][:96]}")
        else:
            print(f"  [PASS] {doc} block {i}")

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
