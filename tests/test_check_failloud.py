# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Check-time fail-loud guards: orphan `it` (§3) and plain `miopublish` (§1) must make
`mio check` exit 1, not pass silently or only fail at runtime."""
import subprocess, sys, os, tempfile
env = dict(os.environ, PYTHONPATH=os.getcwd(), DATABASE_URL=':memory:')
_p = _f = 0
def check(label, src, want_exit):
    global _p, _f
    fd, path = tempfile.mkstemp(suffix='.mho'); os.write(fd, src.encode()); os.close(fd)
    r = subprocess.run([sys.executable, 'mio.py', 'check', path], env=env,
                       capture_output=True, text=True)
    os.unlink(path)
    ok = (r.returncode == want_exit)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label} (exit {r.returncode}, want {want_exit})")
    _p += ok; _f += (not ok)

check("orphan `show it` fails check", "show it\n", 1)
check("give back it after a chain passes", "r \"x\"\nthen\n    show r\ngive back it\n", 0)
check("plain miopublish fails check", 'miopublish "event" to "channel"\n', 1)
# A not-built service used to PARSE CLEAN and only blow up at run. You would see a green
# check, deploy, and find out in production. Check-time silence is still silence, so a service
# that will fail at RUN now fails at CHECK.
check("miopublish.guaranteed is not built -- fails at CHECK, not at run",
      'miopublish.guaranteed "m"\nshow "ok"\n', 1)
check("leading `if` block fails check",
      'x 5\nif x is more than 3\n    show "big"\nif: done\n', 1)
check("trailing `if` (same line) passes",
      'x 5\nshow "big" if x is more than 3\n', 0)
check("unclosed `task` fails check",
      'task t\n    returns int\n\n    give back 1\n', 1)
check("closed `task` passes",
      'task t\n    returns int\n\n    give back 1\ntask: done\n', 0)
check("call to an undeclared task fails check",
      'call nonexistentTask\n', 1)
check("call to a declared task passes",
      'task greet\n    show "Hello"\ntask: done\n\ncall greet\n', 0)

check("check <value> as NAME (naming on the action) passes",
      'score 85\ncheck score as grade\n    when score is more than 80\n        give back "A"\n'
      '    otherwise\n        give back "B"\ncheck: done\nshow grade\n', 0)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
