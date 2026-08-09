# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Single-door enforcement: prove `mio check` goes THROUGH mohio_enforce.enforce() (audit #2).

The audit's point: a test that only shells out to `mio.py check` proves nothing about the
architecture, because mio.py could (and did) reconstruct the pipeline manually and pass the same
specimens. So this asserts BOTH:

  (A) structurally -- mio.py imports enforce and its check path does not hand-roll the three layers,
  (B) behaviorally -- when cmd_check runs, mohio_enforce.enforce() is actually CALLED.

If a future change reverts cmd_check to calling validate()/transform()/run_scans() directly, (B)
goes red even if the specimens still pass.
"""
import os, sys, re, subprocess, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DATABASE_URL', ':memory:')
os.environ.setdefault('MOHIO_ENCRYPTION_KEY', 'testkey')
os.environ['PYTHONPATH'] = ROOT

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)


# ── (A) structural: mio.py goes through the door ──────────────────────────────────────
mio_src = open(os.path.join(ROOT, 'mio.py'), encoding='utf-8').read()

check("mio.py imports the enforce door", 'from mohio_enforce import enforce' in mio_src)

# The check path must NOT hand-roll the layers. We allow direct transform() in SERVE/RUN paths
# (they build the AST to execute, after enforcement), but the CHECK path and its Layer-1 helper
# must go through enforce(). Assert cmd_check's body references enforce, not a bare run_scans.
_cmd_check = mio_src[mio_src.find('def cmd_check('):]
_cmd_check = _cmd_check[:_cmd_check.find('\ndef ', 1)]
check("cmd_check calls enforce (not a hand-rolled pipeline)",
      re.search(r'\benforce\w*\s*\(', _cmd_check) is not None)
check("cmd_check does not import run_scans directly",
      'from mohio_reachability import run_scans' not in _cmd_check,
      "cmd_check should call enforce_scans, which owns the canonical scanner list")

# _parse_and_validate (Layer 1 helper) must go through the door, not call validate() directly.
_pv = mio_src[mio_src.find('def _parse_and_validate('):]
_pv = _pv[:_pv.find('\ndef ', 1)]
check("_parse_and_validate goes through enforce for Layer 1",
      re.search(r'\benforce\s*\(', _pv) is not None
      and 'from mohio_transformer import validate' not in _pv,
      "Layer 1 must run through enforce(build_ast=False), not a direct validate() import")


# ── (B) behavioral: enforce() is actually called when cmd_check runs ──────────────────
# Instrument mohio_enforce.enforce, then invoke cmd_check in-process and assert it fired.
import mohio_enforce
_calls = {'n': 0}
_orig = mohio_enforce.enforce
def _counting_enforce(*a, **k):
    _calls['n'] += 1
    return _orig(*a, **k)
mohio_enforce.enforce = _counting_enforce

# also patch the name already imported into mio's namespace if it grabbed a direct ref
import mio as _mio
if hasattr(_mio, 'enforce'):
    _mio.enforce = _counting_enforce

with tempfile.NamedTemporaryFile('w', suffix='.mho', dir='/tmp', delete=False) as fh:
    fh.write('shape T\n    age as int\nshape: done\n')
    spec = fh.name

class _Args:
    file = spec
    all = False
    json = False
    langmap = False
    strict = False
    verbose = False
    command = 'check'
    ai = False
    def __getattr__(self, _name):
        return False  # any flag cmd_check checks defaults to off

try:
    try:
        _mio.cmd_check(_Args())
    except SystemExit:
        pass  # cmd_check calls sys.exit on completion; that is expected
    check("enforce() was called during cmd_check", _calls['n'] >= 1,
          f"enforce call count = {_calls['n']}")
finally:
    mohio_enforce.enforce = _orig
    os.unlink(spec)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
