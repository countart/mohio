# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Corpus-parse gate: every shipped, pioneer-facing .mho must parse (mio check exit 0).

Why this exists: two cookbook recipes once shipped with committed git merge-conflict markers
and failed to parse at line 1, col 1 -- broken code on the most pioneer-facing surface in the
repo. Nothing shipped may regress that way again.

Scope -- the dirs a pioneer copies from:
    cookbook/      (recipes; empty today, both recipes quarantined pending gaps)
    examples/      (runnable examples)
    start-here/    (onboarding)

Carved out: drafts/ is the not-yet-wired holding pen -- its files intentionally do NOT parse
(biometric needs the unbuilt mioauth.*; school-checkin needs the `is.in today` grammar gap).
Out of scope: tests/ (deliberately-broken fixtures), bucket/, dirtest/, and repo-root scratch.

Run as a script: `python tests/test_corpus_parses.py` (exit 0 = pass).
"""
import glob
import os
import subprocess
import sys

REPO = os.getcwd()
MIO = os.path.join(REPO, "mio.py")
ENV = dict(os.environ, PYTHONPATH=REPO, DATABASE_URL=":memory:",
           PYTHONIOENCODING="utf-8", PYTHONUTF8="1")

SHIPPED_DIRS = ["cookbook", "examples", "start-here"]

# Intentional non-parsers that live INSIDE a shipped dir, each with a reason. Empty today.
# A file listed here must STILL fail; if it starts passing, remove it (the gate says so),
# so the exemption list can never silently rot into hiding a real regression.
KNOWN_NEGATIVE = {
    # "examples/some_intentional_negative.mho": "why it is an intentional negative",
}

# drafts/ is exempt by being outside SHIPPED_DIRS, never by living in this list.
assert "drafts" not in SHIPPED_DIRS

def check_exit(path):
    r = subprocess.run([sys.executable, MIO, "check", path], cwd=REPO, env=ENV,
                       capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr

files = []
for d in SHIPPED_DIRS:
    files.extend(glob.glob(os.path.join(REPO, d, "**", "*.mho"), recursive=True))
files = sorted(set(files))

_p = _f = 0
for path in files:
    rel = os.path.relpath(path, REPO).replace(os.sep, "/")
    code, out = check_exit(path)
    if rel in KNOWN_NEGATIVE:
        ok = code != 0
        label = f"{rel} -- intentional negative ({KNOWN_NEGATIVE[rel]})"
        detail = "PASSED but is listed as a known negative -- remove it from KNOWN_NEGATIVE"
    else:
        ok = code == 0
        label = rel
        detail = out[-400:]
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{('  :: ' + detail) if (detail and not ok) else ''}")
    _p += ok; _f += (not ok)

scope = ", ".join(SHIPPED_DIRS)
print(f"\nRESULTS: {_p} passed, {_f} failed  (scope: {scope}; {len(files)} file(s); drafts/ carved out)")
sys.exit(1 if _f else 0)
