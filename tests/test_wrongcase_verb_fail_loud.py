# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""Workstream A / Unit A4 -- a leading word that is a CASE-VARIANT of a real Mohio verb
fails loud, instead of being silently absorbed as a bare variable.

Mohio keywords are case-sensitive: `show` runs, but `Show`/`SHOW`/`Save` parse as a bare
`NAME value` assignment and used to check clean and do nothing. A4 escalates ONLY that narrow
case -- a case-variant of an actual verb -- to a hard error. It does NOT blanket-error
capitalized names: a foreign keyword (`print`) stays a warning, a read variable gets no
diagnostic, and a capitalized SHAPE name is untouched.

Run as a script: `python tests/test_wrongcase_verb_fail_loud.py` (exit 0 = pass).
"""
import os, subprocess, sys, tempfile

REPO = os.getcwd()
MIO = os.path.join(REPO, "mio.py")
ENV = dict(os.environ, PYTHONPATH=REPO, DATABASE_URL=":memory:",
           PYTHONIOENCODING="utf-8", PYTHONUTF8="1")

_p = _f = 0
def _record(label, ok, detail=""):
    global _p, _f
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{(' -- ' + detail) if (detail and not ok) else ''}")
    _p += ok; _f += (not ok)

def _check(src):
    # mkstemp gives a UNIQUE name -- important on case-insensitive filesystems, where
    # Show.mho / show.mho would otherwise collide.
    fd, path = tempfile.mkstemp(suffix=".mho"); os.write(fd, src.encode()); os.close(fd)
    try:
        r = subprocess.run([sys.executable, MIO, "check", path], cwd=REPO, env=ENV,
                           capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr
    finally:
        os.unlink(path)
        cache = path + ".cache"
        if os.path.exists(cache):
            os.unlink(cache)

def fails(label, src, verb):
    c, o = _check(src)
    _record(label, c == 1 and "is not a Mohio word" in o and f"`{verb}`" in o, f"exit={c}\n{o[-200:]}")

def clean(label, src):
    c, o = _check(src)
    _record(label, c == 0 and "is not a Mohio word" not in o, f"exit={c}\n{o[-200:]}")

# tier 1: case-variants of real verbs FAIL LOUD, naming the verb + did-you-mean
fails("`Show \"hi\"` fails loud (wrong-case verb)", 'Show "hi"\n', "show")
fails("`SHOW \"hi\"` fails loud (all-caps verb)", 'SHOW "hi"\n', "show")
fails("`Save \"hi\"` fails loud", 'Save "hi"\n', "save")
fails("`Check \"hi\"` fails loud", 'Check "hi"\n', "check")
# regardless of read: a wrong-case verb that IS read is still a mis-cased keyword, not a var
fails("a wrong-case verb read later still fails loud", 'Show "hi"\nshow Show\n', "show")

# tier boundaries that must NOT escalate:
clean("`show \"hi\"` (canonical lowercase) runs clean", 'show "hi"\n')
# print stays a WARNING (exit 0), not an error
_c, _o = _check('print "hi"\n')
_record("`print \"hi\"` stays a warning, not an error (foreign keyword)",
        _c == 0 and "is not a Mohio word" not in _o and "set but never used" in _o, f"exit={_c}\n{_o[-200:]}")
# a read variable gets no diagnostic
clean("`greeting \"hi\"` read by `show greeting` -> no diagnostic", 'greeting "hi"\nshow greeting\n')
# a capitalized SHAPE name is untouched (capitalized identifiers already mean shapes)
clean("a capitalized shape name is unaffected",
      'shape Order\n    id as text\nshape: done\n'
      'listen for\n    new sh.Order\n        give back 200 "ok"\n    new: done\nlisten: done\n')
# an ordinary capitalized non-verb bare variable is NOT blanket-errored (narrow, not broad):
# it is only a dead-store warning if unread.
_c2, _o2 = _check('Widget "x"\n')
_record("a capitalized NON-verb bare variable is not errored (narrow, not broad)",
        _c2 == 0 and "is not a Mohio word" not in _o2, f"exit={_c2}\n{_o2[-200:]}")

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
