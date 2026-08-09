# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Single enforcement door: `mio check` and `mio run` must AGREE.

Before this was closed, `mio run` enforced only Layer 1 (parse-tree validation) and skipped Layer 3
(whole-program scanners). A program with a Layer-3 error -- e.g. a field typed with an undeclared
shape -- would FAIL `mio check` but RUN anyway. Now `mio run` runs all three layers through the
door (enforce -> enforce_scans on the assembled program) and blocks execution on any error, so the
two commands agree.

This test drives the real CLI (subprocess) so it exercises the actual cmd_run / cmd_check paths,
not an in-process shortcut. It also locks the currency/dec.N fix the closure surfaced: the Layer-3
type scanner must accept USD/CAD/EUR/GBP and dec.N annotations (it did not, so valid currency
programs were being rejected by check).
"""
import os, sys, subprocess, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV = dict(os.environ, PYTHONPATH=ROOT, DATABASE_URL=":memory:",
           MOHIO_ENCRYPTION_KEY="testkey")
MIO = os.path.join(ROOT, "mio.py")

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)


def _write(src):
    fd, path = tempfile.mkstemp(suffix=".mho")
    os.write(fd, src.encode()); os.close(fd)
    return path


def _run(cmd, path):
    r = subprocess.run([sys.executable, MIO, cmd, path], env=ENV,
                       capture_output=True, text=True, timeout=60)
    return r.returncode, (r.stdout + r.stderr)


# ── a Layer-3 error (undeclared type on a field) blocks BOTH check and run ────────────
bad = _write('shape Order\n    widget as Gadget\nshape: done\nshow "ok"\n')
c_code, c_out = _run("check", bad)
r_code, r_out = _run("run", bad)
check("check flags the undeclared-type error", "error" in c_out.lower())
check("run BLOCKS on the same Layer-3 error (nonzero exit)", r_code != 0)
check("run does NOT execute the program (no 'ok' printed)", "\n  ok" not in r_out)
check("check and run agree: both reject the program", (c_code != 0) == (r_code != 0) == True)
os.unlink(bad)

# ── a valid program (currency + dec.N) passes BOTH ────────────────────────────────────
good = _write('total as USD\ntotal 1234.5\nshow ("Total: " & total)\n')
c_code2, c_out2 = _run("check", good)
r_code2, r_out2 = _run("run", good)
check("check passes a valid currency program (Layer-3 knows USD)", "no errors" in c_out2.lower())
check("run executes the valid currency program", "$1,234.50" in r_out2)
check("check and run agree: both accept the program", c_code2 == 0 and r_code2 == 0)
os.unlink(good)

# ── dec.N annotation is accepted by the Layer-3 scanner ───────────────────────────────
dec = _write('shape M\n    amt as dec.2\nshape: done\nshow "ok"\n')
c_code3, c_out3 = _run("check", dec)
check("check accepts a dec.N field type", "no errors" in c_out3.lower())
os.unlink(dec)

# ── serve must also block a Layer-3 error (it is a run-path too) ───────────────────────
def _serve_starts(path):
    """Return True if serve reaches 'Listening', False if it blocks on a build error."""
    import subprocess as _sp
    try:
        r = _sp.run([sys.executable, MIO, "serve", path], env=ENV,
                    capture_output=True, text=True, timeout=6)
        out = r.stdout + r.stderr
    except _sp.TimeoutExpired as e:
        out = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        out += (e.stderr or b"").decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
    return "Listening" in out, out

bad_serve = _write('shape Order\n    widget as Gadget\nshape: done\n'
                    'shape H\n    method GET\nshape: done\n'
                    'listen for\n    request for sh.H at /x\n        give back 200 "hi"\n'
                    '    request: done\nlisten: done\n')
_started, _sout = _serve_starts(bad_serve)
check("serve BLOCKS on a Layer-3 error (does not start)", not _started)
check("serve reports the build failure", "Build failed" in _sout)
os.unlink(bad_serve)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
