# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""One declared storage area registers once, however many times its declaration is executed.

MEASURED through `mio serve <directory>` and a real multipart upload, which is where this was
found and the only place it shows:

    POST /  ->  500 upload_zone_ambiguous: this program declares more than one cloud
                storage area (vault, vault)

`vault` twice. The program declares it once. Durable storage was unreachable entirely: no upload
could succeed, and the message accused the developer of a mistake the source does not contain.

WHY IT HAPPENED, from an instrumented run rather than from reading. The zone was registered twice
on the SAME interpreter, from two places:

    run_declarations          -- directory-mode startup
    _run_with_session_inner   -- the request

THE DOUBLE EXECUTION IS NOT THE BUG, which matters because the obvious fix is to stop it.
Declarations are re-run per request deliberately: a request gets a fresh base context and its
shapes, tasks and connections have to exist in it. Removing that to fix this would break every
other declaration.

THE BUG IS THAT ONE HANDLER ACCUMULATED. Sweeping every declaration executor in the interpreter
for one that grows interpreter-level state found exactly one, `_exec_MiofileDecl`, which appended
to a list. Every other registrar assigns into a dict keyed by name (set_shape, set_task,
set_connection), so running a declaration twice leaves ONE entry. Appending leaves two. The fix
brings the odd one into line with the contract the others already keep.

KEYED ON THE WHOLE SPEC, NOT THE NAME, and there is a real case behind that: a bare `temp` area
can be declared with no name, so keying on name would merge two genuinely different unnamed areas
-- the same wrong answer pointing the other way. That case is asserted below.

WHAT IS DELIBERATELY UNCHANGED: the ambiguity refusal. Two DIFFERENT cloud areas still refuse,
because picking one would put a health record in whichever bucket happened to be declared first.
Only the false trigger is gone, and both halves are asserted here.

Run: PYTHONPATH=$PWD python tests/test_battery_cloud_zone_double_registration.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("DATABASE_URL", ":memory:")
os.environ.setdefault("MOHIO_ENCRYPTION_KEY", "testkey")
os.environ.setdefault("MOHIO_LICENSE", "test-license")

import mohio_data                                                       # noqa: E402
from lark import Lark                                                   # noqa: E402
from mohio_transformer_ast import transform                             # noqa: E402
from mohio_interpreter import MohioInterpreter, MockAiRuntime           # noqa: E402

_g = "\n".join(l for l in mohio_data.GRAMMAR_PATH.read_text(encoding="utf-8-sig").splitlines()
               if not l.strip().startswith("//"))
_P = Lark(_g, parser="earley", ambiguity="resolve", propagate_positions=True)


# HOW THIS BATTERY IS PROVEN ABLE TO FAIL. Each entry is a real change to the
# code under test; the gate applies it, requires this battery to go red, and
# restores the file. A declaration whose `find` text stops existing fails the gate,
# so this cannot quietly drift into a claim about a mutation nobody runs.
CAN_FAIL = [
    {
        'file': 'mohio_interpreter.py',
        'find': '        self._miofile_zones = _zones',
        'replace': "        self._miofile_zones = (getattr(self, '_miofile_zones', None) or []) + specs",
        'note': 'a declared storage area accumulates on every execution again',
    },
]

_p = _f = 0
_failures = []


def check(label, cond, detail=""):
    global _p, _f
    print("  [" + ("PASS" if cond else "FAIL") + "] " + label)
    if not cond and detail:
        print("          " + str(detail).replace("\n", "\n          ")[:600])
    if not cond:
        _failures.append(label)
    _p += bool(cond)
    _f += (not cond)


ONE_CLOUD = ('miofile\n'
             '    cloud vault\n'
             '        bucket "repro-bucket"\n'
             '        region "auto"\n'
             '        endpoint "https://example.invalid"\n'
             '        accept pdf\n'
             '        max size 9mb\n'
             'miofile: done\n'
             'show "ready"\n')

TWO_CLOUDS = ('miofile\n'
              '    cloud vault\n'
              '        bucket "b1"\n'
              '    cloud archive\n'
              '        bucket "b2"\n'
              'miofile: done\n'
              'show "ready"\n')

TWO_UNNAMED_TEMP = ('miofile\n'
                    '    temp "scratch/one" as first\n'
                    '    temp "scratch/two" as second\n'
                    'miofile: done\n'
                    'show "ready"\n')


def declared(src, times=1):
    """Run the declarations `times` times on ONE interpreter, as the server does.

    A LABELLED UNIT TEST OF THE REGISTRATION ITSELF, paired with the real-path HTTP proof that
    lives beside this file's finding (a directory-mode server and a real upload). It exists
    because the count is what it asserts, and the count is not observable over HTTP: the
    symptom is, the number is not. Calling `run_declarations` twice is exactly what startup plus
    a request does, which the instrumented run established.
    """
    prog = transform(_P.parse(src), src)
    it = MohioInterpreter(ai=MockAiRuntime(), verbose=False, db_path=":memory:")
    for _ in range(times):
        it.run_declarations(prog)
    return it


print("\na storage area declared once is registered once")
print("=" * 78)

# ══ 1. THE COUNT, WHICH IS THE WHOLE BUG ════════════════════════════════════════════════
print("\n-- one declaration, executed the way the server executes it ---------------------")
it1 = declared(ONE_CLOUD, times=1)
z1 = [z for z in (it1._miofile_zones or []) if z.get('kind') == 'cloud']
check("one execution registers one area", len(z1) == 1, it1._miofile_zones)

it2 = declared(ONE_CLOUD, times=2)
z2 = [z for z in (it2._miofile_zones or []) if z.get('kind') == 'cloud']
check("TWO executions still register one area (startup plus a request)",
      len(z2) == 1, it2._miofile_zones)
check("...and it is still the area that was declared",
      bool(z2) and z2[0].get('name') == 'vault', z2)
check("...keeping its settings, so the dedupe did not drop the configured copy",
      bool(z2) and (z2[0].get('settings') or {}).get('bucket') == 'repro-bucket', z2)
check("...and its safety policies", bool(z2) and z2[0].get('accept') == ['pdf']
      and z2[0].get('maxsize') == 9 * 1024 * 1024, z2)

it5 = declared(ONE_CLOUD, times=5)
z5 = [z for z in (it5._miofile_zones or []) if z.get('kind') == 'cloud']
check("five executions still register one area, so it cannot creep",
      len(z5) == 1, it5._miofile_zones)

# ══ 2. THE REFUSAL IS INTACT FOR THE REAL CASE ══════════════════════════════════════════
# The check is CORRECT and must stay: two rival areas cannot be guessed between, because
# guessing puts a health record in whichever bucket happened to be declared first.
print("\n-- two DIFFERENT areas are still two, and still refuse --------------------------")
itd = declared(TWO_CLOUDS, times=2)
zd = [z for z in (itd._miofile_zones or []) if z.get('kind') == 'cloud']
check("two declared areas register as two, even across repeated execution",
      len(zd) == 2, itd._miofile_zones)
_msg = ""
try:
    itd._upload_zone()
except Exception as e:                                                  # noqa: BLE001
    _msg = str(getattr(e, 'message', '') or e)
check("...and an upload against them is refused", "more than one cloud storage" in _msg, _msg)
check("...naming both areas", "vault" in _msg and "archive" in _msg, _msg)

# And the single case resolves rather than refusing, which is the whole point of the fix.
_zone, _err = None, ""
try:
    _zone = it2._upload_zone()
except Exception as e:                                                  # noqa: BLE001
    _err = str(getattr(e, 'message', '') or e)
check("a single declared area resolves instead of refusing",
      _zone is not None and not _err, _err or _zone)
check("...to the declared area", (_zone or {}).get('name') == 'vault', _zone)

# ══ 3. THE EDGE THE KEY EXISTS FOR ══════════════════════════════════════════════════════
# Keying on NAME would have been the obvious dedupe and would have been wrong: two different
# areas that differ in path must both survive.
print("\n-- two different areas that differ only in path stay two ------------------------")
itt = declared(TWO_UNNAMED_TEMP, times=3)
zt = [z for z in (itt._miofile_zones or []) if z.get('kind') == 'temp']
check("two distinct temp areas survive repeated execution",
      len(zt) == 2, itt._miofile_zones)
check("...keeping both paths",
      sorted(z.get('path') for z in zt) == ['scratch/one', 'scratch/two'], zt)

print()
print("=" * 78)
print("RESULTS: %d passed, %d failed" % (_p, _f))
for x in _failures:
    print("  FAILED: " + x)
sys.exit(1 if _f else 0)
