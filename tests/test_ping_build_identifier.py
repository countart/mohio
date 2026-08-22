# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""T1-PING-BUILD-IDENTIFIER (2026-08-20): `/ping` reports the COMMIT this build was made from,
not just the release number.

WHY. `mohio_version.py`'s own docstring already argues that a version disagreeing with itself
makes a deploy unprovable. This is the same failure one level down: VERSION had not moved in six
commits, so `/ping` answered `4.8.2` for BOTH a production container and a local checkout six
commits ahead of it. During a live investigation on 2026-08-20 the question "is the fix I am
looking at actually deployed?" could not be answered from `/ping` at all -- the field existed,
looked authoritative, and discriminated nothing. VERSION answers "which release"; BUILD_SHA
answers "which build", and a deploy question needs the second.

RESOLUTION ORDER (mohio_version._resolve_build_sha), most authoritative first:
  1. `MOHIO_BUILD_SHA` env var -- stamped by the deploy pipeline. The only tier that survives into
     a container built without a .git directory, which is precisely the production case.
  2. `_build_sha.txt` beside the module -- written at package time, same reason.
  3. `git rev-parse --short HEAD` -- a developer running from a checkout.
  4. `"unknown"` -- stated plainly. Never fabricated and never silently blank, so a reader can
     tell "this build is X" apart from "this build did not record which commit it is." The second
     is actionable on its own: it means the pipeline is not stamping.

Resolved ONCE at import, not per request: a server answers /ping constantly, and the commit
cannot change under a running process.

The single-source rule from `mohio_version.py` still holds and is still enforced by
tests/test_structural_invariants.py -- BUILD_SHA lives in that same one module, and
`mohio_server` imports it rather than computing its own.

Run: `python tests/test_ping_build_identifier.py`.
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); os.chdir(ROOT)
os.environ.setdefault('DATABASE_URL', ':memory:')
os.environ.setdefault('MOHIO_ENCRYPTION_KEY', 'testkey')

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)


def _load_version_module(directory, env=None):
    """Import mohio_version.py from `directory` in a SUBPROCESS, so each resolution tier is
    exercised with a genuinely fresh import and a genuinely different environment. Importing it
    in-process would just hand back the already-resolved BUILD_SHA from this process."""
    code = (
        "import importlib.util, sys\n"
        "spec = importlib.util.spec_from_file_location('mv', 'mohio_version.py')\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "sys.stdout.write(m.BUILD_SHA)\n"
    )
    e = dict(os.environ)
    e.pop('MOHIO_BUILD_SHA', None)
    if env:
        e.update(env)
    out = subprocess.run([sys.executable, '-c', code], cwd=directory,
                         capture_output=True, text=True, timeout=60, env=e)
    return out.stdout.strip()


# -- the module exposes a build identifier at all ------------------------------------------
from mohio_version import VERSION, BUILD_SHA
check("mohio_version exposes BUILD_SHA", bool(BUILD_SHA), BUILD_SHA)
check("BUILD_SHA is a DIFFERENT field from VERSION (it must discriminate what VERSION cannot)",
      BUILD_SHA != VERSION, f"{BUILD_SHA!r} vs {VERSION!r}")

# -- tier 3: a developer running from the checkout gets the real commit ---------------------
try:
    real = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=ROOT,
                          capture_output=True, text=True, timeout=30)
    real_sha = real.stdout.strip() if real.returncode == 0 else ''
except Exception:
    real_sha = ''
if real_sha:
    check("tier 3: from a git checkout, BUILD_SHA is the actual HEAD commit",
          _load_version_module(ROOT) == real_sha, f"got {_load_version_module(ROOT)!r}, git says {real_sha!r}")
else:
    print("  [SKIP] tier 3: git not available here")

# -- tier 1: the deploy pipeline's env var wins over everything else ------------------------
check("tier 1: MOHIO_BUILD_SHA overrides (the container case, no .git present)",
      _load_version_module(ROOT, {'MOHIO_BUILD_SHA': 'deadbee'}) == 'deadbee')

# -- tiers 2 and 4, exercised outside any git checkout --------------------------------------
tmp = tempfile.mkdtemp(prefix='mohio_buildsha_')
try:
    shutil.copy(os.path.join(ROOT, 'mohio_version.py'), tmp)

    # tier 4 first: no env, no stamp, no git -> says so, plainly, rather than guessing.
    check("tier 4: outside a checkout with nothing stamped, BUILD_SHA is the literal 'unknown'",
          _load_version_module(tmp) == 'unknown', _load_version_module(tmp))

    # tier 2: the packaged stamp file.
    with open(os.path.join(tmp, '_build_sha.txt'), 'w', encoding='utf-8') as fh:
        fh.write('abc1234\n')
    check("tier 2: a packaged _build_sha.txt is used when there is no env var and no git",
          _load_version_module(tmp) == 'abc1234', _load_version_module(tmp))

    # priority: env beats the stamp file.
    check("priority: MOHIO_BUILD_SHA beats a packaged _build_sha.txt",
          _load_version_module(tmp, {'MOHIO_BUILD_SHA': 'envwins'}) == 'envwins')
finally:
    shutil.rmtree(tmp, ignore_errors=True)


# -- the server actually SERVES it, on both endpoints ---------------------------------------
# Real HTTP through `mio serve`, not a unit call on the route function
# (T1-TEST-REAL-PATH-STANDARD).
import json
import time
import urllib.request

PORT = 8858
srcfile = os.path.join(tempfile.mkdtemp(prefix='mohio_ping_'), 'ping_probe.mho')
with open(srcfile, 'w', encoding='utf-8') as fh:
    fh.write('show "ok"\n')

env = dict(os.environ, PYTHONPATH=ROOT, DATABASE_URL=':memory:', MOHIO_ENCRYPTION_KEY='testkey')
env.pop('MOHIO_BUILD_SHA', None)
proc = subprocess.Popen([sys.executable, 'mio.py', 'serve', srcfile, '--port', str(PORT), '--memory'],
                        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    payload = None
    health = None
    for _ in range(90):
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/ping', timeout=3) as r:
                payload = json.loads(r.read().decode())
            with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/health', timeout=3) as r:
                health = json.loads(r.read().decode())
            break
        except Exception:
            time.sleep(1)

    check("/ping answers over real HTTP", isinstance(payload, dict), payload)
    if isinstance(payload, dict):
        check("/ping carries a `build` field", 'build' in payload, payload)
        check("/ping still carries `version` (BUILD_SHA adds to it, never replaces it)",
              payload.get('version') == VERSION, payload)
        check("/ping's build is a real identifier, not empty",
              bool(str(payload.get('build', '')).strip()), payload)
        if real_sha:
            check("/ping's build matches the checkout's HEAD commit",
                  payload.get('build') == real_sha, f"{payload.get('build')!r} vs {real_sha!r}")
    check("/health carries the same build identifier (one answer, not two)",
          isinstance(health, dict) and health.get('build') == (payload or {}).get('build'),
          health)
finally:
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except Exception:
        proc.kill()

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
