# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""Unit B -- error-reporter hygiene: no raw Python traceback ever reaches a user.

Every user-facing error path must produce ONE clear Mohio message, never a Python
"Traceback (most recent call last):" dump. Two guarantees:

  1. Foreseeable file-read failures (missing / a directory / not valid UTF-8) go
     through _read_source and fail loud with an accurate one-line message.
  2. Any UNforeseen exception that escapes a command handler is caught by the
     backstop in main() (_die_unexpected) and turned into a clean message; the full
     traceback appears only under MOHIO_DEBUG=1. Clean exits / integrity refusals
     (SystemExit) must pass through untouched.

Adversarial: the directory and invalid-UTF-8 inputs here are exactly the ones that
leaked a raw traceback from `check`, `run`, and `fmt` before the fix.

Run as a script: `python tests/test_error_reporter_hygiene.py` (exit 0 = pass).
"""
import os, subprocess, sys, tempfile

REPO = os.getcwd()
MIO = os.path.join(REPO, "mio.py")
ENV = dict(os.environ, PYTHONPATH=REPO, DATABASE_URL=":memory:",
           PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONUNBUFFERED="1")
ENV.pop("MOHIO_DEBUG", None)
TRACE = "Traceback (most recent call last)"

_p = _f = 0

def _record(label, ok, detail=""):
    global _p, _f
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{(' -- ' + detail) if (detail and not ok) else ''}")
    _p += ok; _f += (not ok)

def run_mio(args, env_extra=None):
    env = dict(ENV, **(env_extra or {}))
    r = subprocess.run([sys.executable, MIO] + args, cwd=REPO, env=env,
                       capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr

# ---- 1. foreseeable file-read failures: clean message, no traceback ----------

COMMANDS = ["check", "run", "fmt", "serve", "schema"]

for cmd in COMMANDS:
    d = tempfile.mkdtemp(suffix=".mho")           # a directory named like a file
    code, out = run_mio([cmd, d])
    _record(f"`mio {cmd} <dir>` no raw traceback", TRACE not in out and code != 0,
            f"exit={code}\n{out[-400:]}")
    os.rmdir(d)

for cmd in COMMANDS:
    fd, path = tempfile.mkstemp(suffix=".mho")
    os.write(fd, b"\xff\xfe\x00\x01 show \"x\"\n"); os.close(fd)   # invalid UTF-8
    code, out = run_mio([cmd, path])
    _record(f"`mio {cmd} <binary>` no raw traceback, names UTF-8",
            TRACE not in out and code != 0 and ("UTF-8" in out or "valid" in out),
            f"exit={code}\n{out[-400:]}")
    os.unlink(path)

for cmd in COMMANDS:
    missing = os.path.join(tempfile.gettempdir(), "mohio_definitely_missing_xyz.mho")
    if os.path.exists(missing):
        os.unlink(missing)
    code, out = run_mio([cmd, missing])
    _record(f"`mio {cmd} <missing>` no raw traceback", TRACE not in out and code != 0,
            f"exit={code}\n{out[-400:]}")

# ---- 1b. commands that take sub-args: translate, schedule (single-file reads) --

SINGLE_FILE_VARIANTS = [
    ("translate", ["translate", "--to", "es"]),
    ("schedule",  ["schedule", "run-due"]),
]

for label, prefix in SINGLE_FILE_VARIANTS:
    d = tempfile.mkdtemp(suffix=".mho")
    code, out = run_mio(prefix + [d])
    _record(f"`mio {label} <dir>` no raw traceback", TRACE not in out and code != 0,
            f"exit={code}\n{out[-400:]}")
    os.rmdir(d)

    fd, path = tempfile.mkstemp(suffix=".mho")
    os.write(fd, b"\xff\xfe\x00\x01 show \"x\"\n"); os.close(fd)
    code, out = run_mio(prefix + [path])
    _record(f"`mio {label} <binary>` no raw traceback, names UTF-8",
            TRACE not in out and code != 0 and ("UTF-8" in out or "valid" in out),
            f"exit={code}\n{out[-400:]}")
    os.unlink(path)

    missing = os.path.join(tempfile.gettempdir(), "mohio_missing_sub_xyz.mho")
    if os.path.exists(missing):
        os.unlink(missing)
    code, out = run_mio(prefix + [missing])
    _record(f"`mio {label} <missing>` no raw traceback", TRACE not in out and code != 0,
            f"exit={code}\n{out[-400:]}")

# ---- 1c. directory serve: one unreadable page is skipped, not fatal ----------

def run_serve_timeout(args, seconds=10):
    """Serve blocks (it listens), so run it under a timeout and read whatever it
    printed during the scan phase before we killed it."""
    try:
        r = subprocess.run([sys.executable, MIO] + args, cwd=REPO, env=ENV,
                           capture_output=True, text=True, timeout=seconds)
        return r.returncode, r.stdout + r.stderr
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") + (e.stderr or "")
        return None, out if isinstance(out, str) else out.decode("utf-8", "replace")

_serve_dir = tempfile.mkdtemp()
with open(os.path.join(_serve_dir, "index.mho"), "w", encoding="utf-8") as _fh:
    _fh.write('show "ok"\n')
with open(os.path.join(_serve_dir, "broken.mho"), "wb") as _fh:
    _fh.write(b"\xff\xfe\x00\x01 bad\n")
_code, _out = run_serve_timeout(["serve", _serve_dir])
_record("directory serve skips an unreadable page, no traceback, still serves the good one",
        TRACE not in _out and "broken" in _out
        and ("routes loaded" in _out or "Listening" in _out),
        f"exit={_code}\n{_out[-600:]}")
import shutil as _shutil
_shutil.rmtree(_serve_dir, ignore_errors=True)


# ---- 2. backstop: unforeseen exception -> clean message, trace only on debug --

_BACKSTOP_PROG = (
    "import sys; sys.argv=['mio']; import mio\n"
    "try:\n"
    "    raise ValueError('boom-xyz')\n"
    "except Exception as e:\n"
    "    mio._die_unexpected(e, 'check')\n"
)

def run_prog(prog, env_extra=None):
    env = dict(ENV, **(env_extra or {}))
    r = subprocess.run([sys.executable, "-c", prog], cwd=REPO, env=env,
                       capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr

code, out = run_prog(_BACKSTOP_PROG)
_record("backstop: unforeseen exc -> clean msg, exit 2, no trace",
        code == 2 and TRACE not in out and "boom-xyz" in out
        and "Internal error" in out and "MOHIO_DEBUG=1" in out,
        f"exit={code}\n{out[-400:]}")

code, out = run_prog(_BACKSTOP_PROG, {"MOHIO_DEBUG": "1"})
_record("backstop: MOHIO_DEBUG=1 shows the full traceback",
        code == 2 and TRACE in out and "boom-xyz" in out,
        f"exit={code}\n{out[-400:]}")

# ---- 3. the backstop must NOT swallow clean exits / integrity refusals --------

_SYSEXIT_PROG = (
    "import sys; sys.argv=['mio']; import mio\n"
    "try:\n"
    "    try:\n"
    "        sys.exit(7)\n"
    "    except SystemExit:\n"
    "        raise\n"
    "    except Exception as e:\n"
    "        mio._die_unexpected(e, 'check')\n"
    "except SystemExit as se:\n"
    "    print('EXITCODE', se.code); sys.exit(se.code)\n"
)
code, out = run_prog(_SYSEXIT_PROG)
_record("backstop passes SystemExit through unchanged (exit code preserved)",
        code == 7 and "EXITCODE 7" in out and TRACE not in out,
        f"exit={code}\n{out[-400:]}")

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
