# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
test_give_back_no_value.py

Guards the fix for the `give back` (no value) misparse.

`give back` written with nothing after it used to have no value to bind, so Earley
fell to `assignment: NAME value_expr` -- a variable named `give` set to the bare name
`back`. The return silently never happened and the route answered with an empty body
and no error.

That root cause is now gone: `give` is a verb in its own right (`give <value> as
download`), so it can no longer be claimed as a variable name. The misparse is
unreachable rather than merely detected. Both forms below still fail loud, and the
message now names both ways out -- `give <value> as download`, or `give back <value>`.

UPDATED when `give` became a verb. `give 5` used to be legal, a variable that happened
to be called `give`; it is now refused, which is the deliberate cost of the split. The
value of the check is unchanged: a return that silently does not return must never
compile.
  * `give back`            -> ERROR  (no value, and no destination either)
  * `give back 200 x`      -> OK     (a real GiveBackStmt)
  * `give back x`          -> OK     (a real GiveBackStmt, no status)
  * `give 5`               -> ERROR  (`give` is a verb now, not a variable name)

Run:  PYTHONPATH=$PWD DATABASE_URL=:memory: python3 tests/test_give_back_no_value.py
"""
import os
import subprocess
import sys
import tempfile

env = dict(os.environ, PYTHONPATH=os.getcwd(), DATABASE_URL=":memory:",
           MOHIO_ENCRYPTION_KEY="testkey")
_p = _f = 0


def case(label, body, want_exit, want_msg=None):
    """Run through the REAL door -- `mio check` -- and assert exit code (and
    optionally that the give-back message is present)."""
    global _p, _f
    src = "page at /\n" + body + "\npage: done\n"
    fd, path = tempfile.mkstemp(suffix=".mho")
    os.write(fd, src.encode())
    os.close(fd)
    r = subprocess.run([sys.executable, "mio.py", "check", path], env=env,
                       capture_output=True, text=True)
    os.unlink(path)
    ok = (r.returncode == want_exit)
    if want_msg is not None:
        ok = ok and (want_msg in (r.stdout + r.stderr))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label} "
          f"(exit {r.returncode}, want {want_exit})")
    _p += ok
    _f += (not ok)


def main():
    # The misparse -- must fail loud with the give-back message.
    case("give back (nothing) fails loud",
         "    give back", want_exit=1, want_msg="needs a destination")

    # Real returns -- must still pass.
    case("give back 200 greeting passes",
         '    greeting "hi"\n    give back 200 greeting', want_exit=0)
    case("give back x passes",
         '    x "hi"\n    give back x', want_exit=0)

    # `give` is a verb now, so it can no longer be a variable name. This used to
    # pass. Refusing it is what makes the old misparse unreachable instead of merely
    # detected, and the message points at both real forms.
    case("give 5 (give is a verb, not a name) fails loud",
         "    give 5", want_exit=1, want_msg="needs a destination")

    print(f"\n  RESULTS: {_p}/{_p + _f} passed")
    sys.exit(1 if _f else 0)


if __name__ == "__main__":
    main()
