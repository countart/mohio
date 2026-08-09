# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Retire + miomail-gate fail-louds (2026-07-31). Locks five behaviors changed this session:

  1. `miomail.send to X subject Y body Z` (the free-tier form) still SENDS (regression guard).
  2. bare `miomail.send` (no recipient) FAILS LOUD -- was a silent exit-0 send-nothing.
  3. `miomail.queue` / `miomail.template` FAIL LOUD as commercial-tier -- were silently sending.
  4. `mioai.*` fail-loud NAMES `ai.create` (generation) alongside `ai.decide`.
  5. `ai.chain` is retired: fail-loud NAMES `ai.connect`.

All via the real CLI (`mio run` / `mio check`).
Run as a script: `python tests/test_retire_and_miomail_gate.py` (exit 0 = pass).
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

def run(cmd, src):
    fd, path = tempfile.mkstemp(suffix=".mho"); os.write(fd, src.encode("utf-8")); os.close(fd)
    try:
        r = subprocess.run([sys.executable, MIO, cmd, path], cwd=REPO, env=ENV,
                           capture_output=True, text=True, timeout=60)
        return r.returncode, (r.stdout + r.stderr)
    finally:
        os.unlink(path)

# 1. free-tier send still works (regression guard)
c, out = run("run", 'miomail.send to "a@b.com" subject "Hi" body "Yo"\n')
rec("miomail.send inline still sends (free tier)", c == 0 and "miomail" in out.lower(), f"exit={c}\n{out[-200:]}")

# 2. bare miomail.send fails loud (was silent exit 0)
c, out = run("run", 'miomail.send\n')
rec("bare miomail.send fails loud (requires a recipient)",
    c != 0 and "recipient" in out.lower() and "miomail.send to" in out, f"exit={c}\n{out[-200:]}")

# 3. queue / template fail loud as commercial (no longer silently send)
for form, src in (("miomail.queue", 'miomail.queue to "a@b.com" subject "Hi" body "Yo"\n'),
                  ("miomail.template", 'miomail.template "welcome" to "a@b.com"\n')):
    c, out = run("run", src)
    ok = c != 0 and "commercial runtime" in out.lower() and "miomail.send to x subject y body z" in out.lower()
    rec(f"{form} fails loud as commercial-tier (names the free alternative)", ok, f"exit={c}\n{out[-200:]}")

# 4. mioai.* names ai.create
c, out = run("check", 'mioai.generate "p"\n')
rec("mioai.* fail-loud names ai.create", c != 0 and "ai.create" in out and "ai.decide" in out, f"exit={c}\n{out[-220:]}")

# 5. ai.chain retired, names ai.connect
c, out = run("run", 'ai.chain first then second\n')
rec("ai.chain is retired and names ai.connect",
    "ai.chain is retired" in out.lower() and "ai.connect" in out, f"exit={c}\n{out[-220:]}")

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
