# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Dead-store warning: suppressed for files with an include or a co-located spine.

The read-scan sees only a single file's Lark tree, so a value declared in one file and read in
an `include` target -- or in the auto-applied `journey.mho` spine -- looked unread and warned
FALSELY (`greeting is set but never used`). A false warning is worse than a missed one, so the
WARNING is suppressed for any file that has an include or a spine. NARROW: never global. The A4
error (a mis-cased verb) is NOT suppressed -- it is read-independent, never a cross-file false
positive.

Accepted trade: a genuinely-unused top-level var in an include/spine file no longer warns
(tracked as a known limitation; real fix is AST-layer read collection after assembly).

Run as a script: `python tests/test_deadstore_crossfile_suppression.py` (exit 0 = pass).
"""
import os, subprocess, sys, tempfile

REPO = os.getcwd()
MIO = os.path.join(REPO, "mio.py")
ENV = dict(os.environ, PYTHONPATH=REPO, DATABASE_URL=":memory:",
           PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
NEVER = "is set but never used"

_p = _f = 0
def _record(label, ok, detail=""):
    global _p, _f
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{(' -- ' + detail) if (detail and not ok) else ''}")
    _p += ok; _f += (not ok)

def _check_file(path):
    r = subprocess.run([sys.executable, MIO, "check", path], cwd=REPO, env=ENV,
                       capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr

def _check_single(src):
    fd, path = tempfile.mkstemp(suffix=".mho"); os.write(fd, src.encode()); os.close(fd)
    try:
        return _check_file(path)
    finally:
        os.unlink(path)
        if os.path.exists(path + ".cache"):
            os.unlink(path + ".cache")

# ---- the false positive is gone: declare in main, read in an include -> NO warning ----
d = tempfile.mkdtemp()
with open(os.path.join(d, "other.mho"), "w") as fh: fh.write("show greeting\n")
with open(os.path.join(d, "main.mho"), "w") as fh: fh.write('greeting "hello"\ninclude "other.mho"\n')
c, o = _check_file(os.path.join(d, "main.mho"))
_record("include file: a value read in the include does NOT warn (false positive fixed)",
        NEVER not in o, f"exit={c}\n{o[-200:]}")

# ---- the A4 error is NOT suppressed even in an include file ----
with open(os.path.join(d, "a4.mho"), "w") as fh: fh.write('Show "hi"\ninclude "other.mho"\n')
c, o = _check_file(os.path.join(d, "a4.mho"))
_record("include file: a mis-cased verb still fails loud (A4 error not suppressed)",
        c == 1 and "not a Mohio word" in o, f"exit={c}\n{o[-200:]}")

# ---- spine-bearing file: a genuinely-unused var is suppressed (accepted trade) ----
sd = tempfile.mkdtemp()
with open(os.path.join(sd, "journey.mho"), "w") as fh: fh.write('hold shared "hello"\n')
with open(os.path.join(sd, "page.mho"), "w") as fh: fh.write('foo "x"\nshow "hi"\n')
c, o = _check_file(os.path.join(sd, "page.mho"))
_record("spine file: dead-store warning is suppressed (co-located journey.mho)",
        NEVER not in o, f"exit={c}\n{o[-200:]}")

# ---- NARROW: a single file with NO include/spine still warns normally ----
c, o = _check_single('unused "x"\nshow "hi"\n')
_record("single file: a genuinely unused var still warns", NEVER in o, f"exit={c}\n{o[-200:]}")
c, o = _check_single('print "hi"\n')
_record("single file: `print \"hi\"` alone still warns", NEVER in o, f"exit={c}\n{o[-200:]}")
# and the A4 error still fires in a plain single file
c, o = _check_single('Show "hi"\n')
_record("single file: mis-cased verb still fails loud", c == 1 and "not a Mohio word" in o, f"exit={c}")

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
