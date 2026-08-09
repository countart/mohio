# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
Guard: a declared `miofile` area must GOVERN the file operations that land in it.

Before this was wired, a zone declaration validated and then did nothing -- this
program compiled clean and wrote the .exe anyway:

    miofile
        local "uploads" as media accept jpg, png max size 5mb
    miofile: done
    miofile.write "uploads/evil.exe" "MZ"

That is accept-and-ignore: the same `accept` / `max size` words enforced on a shape
field and were decorative on an area. This guard proves they now mean the same thing
in both places, that a path in NO declared area keeps working (so declaring areas
tightens rather than breaks), and that the rules hold on the real serve path -- which
only works because MiofileDecl runs as a startup declaration.
"""
import os, sys, json, shutil, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import mohio_data
os.environ.setdefault("DATABASE_URL", ":memory:")
os.environ.setdefault("MOHIO_ENCRYPTION_KEY", "testkey")
_AREA = tempfile.mkdtemp(prefix="miofile_zones_")
os.environ["MIOFILE_ROOT"] = _AREA

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter
from mohio_server import MohioServer, create_app
from starlette.testclient import TestClient
from mohio_reachability import (scan_miofile_zone_coverage, scan_upload_accept_groups,
                                 scan_miofile_dangerous_accept)

_passed = _failed = 0
def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1; print(f"  ok   {label}")
    else:
        _failed += 1; print(f"  FAIL {label}: got {got!r} want {want!r}")

def check_true(label, val):
    global _passed, _failed
    if val:
        _passed += 1; print(f"  ok   {label}")
    else:
        _failed += 1; print(f"  FAIL {label}: expected truthy, got {val!r}")

_raw = mohio_data.GRAMMAR_PATH.read_text(encoding="utf-8")
_g = "\n".join(l for l in _raw.splitlines() if not l.strip().startswith("//"))
_P = Lark(_g, parser="earley", ambiguity="resolve", propagate_positions=True)

ZONE = ('miofile\n'
        '    local "uploads" as media accept jpg, png max size 5mb\n'
        'miofile: done\n')

def run_prog(src):
    """Run a program the way the runtime does: declarations first, then statements.
    Returns (ok, message)."""
    prog = transform(_P.parse(src), src)
    interp = MohioInterpreter()
    try:
        interp.run_declarations(prog)
        ctx = interp.base_context()
    except Exception:
        ctx = None
    try:
        from mohio_ast import MiofileStmt
        for st in prog.statements:
            if isinstance(st, MiofileStmt):
                interp._exec(st, ctx)
        return True, ""
    except Exception as e:
        return False, str(e)

print("test_miofile_zones")

# 1. The exact case that used to slip through.
ok, msg = run_prog(ZONE + 'miofile.write "uploads/evil.exe" "MZ"\n')
check("write .exe into a jpg/png area is refused", ok, False)
check_true("the .exe refusal names it as executable", "executable" in msg)
check("the .exe never reached disk",
      os.path.exists(os.path.join(_AREA, "uploads", "evil.exe")), False)

# 1b. A harmless type that simply is not on the list gets the accept-list message.
ok, msg = run_prog(ZONE + 'miofile.write "uploads/notes.txt" "hi"\n')
check("write .txt into a jpg/png area is refused", ok, False)
check_true("that refusal names the accepted types", "jpg" in msg and "png" in msg)

# 2. An accepted type in the same area still works.
ok, msg = run_prog(ZONE + 'miofile.write "uploads/photo.jpg" "jpegdata"\n')
check("write .jpg into the same area succeeds", ok, True)
check("the .jpg reached disk",
      os.path.isfile(os.path.join(_AREA, "uploads", "photo.jpg")), True)

# 3. max size is enforced, in bytes, MB-style message.
ok, msg = run_prog(ZONE + 'miofile.write "uploads/big.jpg" "' + ("A" * (6 * 1024 * 1024)) + '"\n')
check("write over max size is refused", ok, False)
check_true("refusal names the limit", "5 MB" in msg or "5MB" in msg)

# 4. A path in NO declared area keeps working (declaring areas tightens, never breaks).
ok, msg = run_prog(ZONE + 'miofile.write "staging/free.txt" "ok"\n')
check("write outside every declared area still works", ok, True)

# 5. copy / move cannot smuggle a refused type into a governed area.
run_prog('miofile.write "staging/x.exe" "MZ"\n')
ok, _ = run_prog(ZONE + 'miofile.copy "staging/x.exe" to "uploads/x.exe"\n')
check("copy of .exe into the area is refused", ok, False)
ok, _ = run_prog(ZONE + 'miofile.move "staging/x.exe" to "uploads/x.exe"\n')
check("move of .exe into the area is refused", ok, False)
check("no .exe reached the area by copy or move",
      os.path.exists(os.path.join(_AREA, "uploads", "x.exe")), False)

# 6. The check-time coverage warning: fires outside, silent inside, silent with no areas.
def warns(src):
    return len(scan_miofile_zone_coverage(transform(_P.parse(src), src)))
check("warns when an op is outside every declared area",
      warns(ZONE + 'miofile.write "staging/x.txt" "hi"\n') > 0, True)
check("silent when the op is inside a declared area",
      warns(ZONE + 'miofile.write "uploads/a.jpg" "hi"\n'), 0)
check("silent when no areas are declared at all",
      warns('miofile.write "anywhere/x.txt" "hi"\n'), 0)

# 6b. `accept` can never opt in to an executable type. An upload field refuses .exe
#     ahead of its own allowlist, so an area must refuse it too -- otherwise the same
#     word means two different things depending on where it is written.
def errs(src):
    return scan_miofile_dangerous_accept(transform(_P.parse(src), src))
bad_accept = ('miofile\n'
              '    local "uploads" as media accept jpg, exe max size 5mb\n'
              'miofile: done\n')
e = errs(bad_accept)
check("`accept exe` is refused at check", len(e) > 0, True)
check_true("the refusal says it is an executable type",
           bool(e) and "executable" in str(e[0].message))
check("a clean accept list raises nothing", len(errs(ZONE)), 0)

# 6b-ii. The blocklist policy itself, so a future edit cannot quietly change it.
from mohio_interpreter import MohioInterpreter as _MI
_BLOCKED = _MI._DANGEROUS_UPLOAD_EXT
for _e in ("pdf", "jpg", "png", "docx", "xlsx", "pptx", "csv", "txt", "zip"):
    check(f"{_e} stays accepted", _e in _BLOCKED, False)
for _e in ("exe", "docm", "xlsm", "pptm", "xll", "php", "jar", "apk", "hta", "dmg"):
    check(f"{_e} is refused", _e in _BLOCKED, True)
check("`accept docm` is refused at check",
      len(errs('miofile\n    local "d" as docs accept pdf, docm max size 5mb\nmiofile: done\n')) > 0,
      True)
check("`accept pdf` alone raises nothing",
      len(errs('miofile\n    local "d" as docs accept pdf max size 5mb\nmiofile: done\n')), 0)

# 6b-iii. The nine accept groups (catalog ruling 2026-07-22) and the forms that must
#         keep failing. Groups resolve to extensions; `all` never does.
from mohio_interpreter import MohioInterpreter as _MI2
_r = _MI2.resolve_accept_list
check("images resolves", _r(['images']),
      ['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'tiff'])
check("documents resolves", _r(['documents']), ['pdf', 'docx', 'txt', 'rtf', 'odt'])
check("spreadsheets resolves", _r(['spreadsheets']), ['xlsx', 'csv', 'tsv', 'ods'])
check("presentations resolves", _r(['presentations']), ['pptx', 'odp'])
check("archives resolves", _r(['archives']), ['zip', 'tar', 'gz', '7z'])
check("audio resolves", _r(['audio']), ['mp3', 'wav', 'ogg', 'm4a', 'flac'])
check("video resolves", _r(['video']), ['mp4', 'webm', 'mov', 'avi', 'mkv'])
check("media is images + audio + video", len(_r(['media'])), 17)
check("office is documents + spreadsheets + presentations", _r(['office']),
      ['pdf', 'docx', 'txt', 'rtf', 'odt', 'xlsx', 'csv', 'tsv', 'ods', 'pptx', 'odp'])
check("groups union without duplicates", _r(['images', 'pdf', 'csv']),
      ['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'tiff', 'pdf', 'csv'])
check("explicit extensions pass through", _r(['jpg', 'png']), ['jpg', 'png'])
check("`all` is not a group", _r(['all']), ['all'])

def field_errs(accept):
    src = (f'shape D\n    f as file accept {accept} max size 5mb\nshape: done\n')
    return scan_upload_accept_groups(transform(_P.parse(src), src))

for _ok in ("images", "images, documents", "images, pdf, csv", "media", "office",
            "jpg, png", "html", "zip", "mp4"):
    check(f"accept {_ok} passes check", len(field_errs(_ok)), 0)
for _bad in ("all", "any", "image", "photos", "svg", "doc", "xls", "ppt", "py", "rb"):
    check(f"accept {_bad} fails check", len(field_errs(_bad)) > 0, True)

ok, msg = run_prog('miofile\n    local "uploads" as media accept jpg\nmiofile: done\n'
                   'miofile.write "uploads/payload.exe" "MZ"\n')
check("runtime refuses an executable in a declared area", ok, False)
check_true("runtime refusal names it as executable", "executable" in msg)

# 6c. Not over-blocked: the same extensions stay writable OUTSIDE any declared area,
#     so generating a .js asset server-side keeps working.
ok, _ = run_prog('miofile.write "assets/app.js" "console.log(1)"\n')
check("a .js write outside every declared area still works", ok, True)

# 7. The rules hold on the real serve path (requires MiofileDecl to run at startup).
SERVE = (ZONE + '\nshape Up\n    note as text\nshape: done\n\n'
         'listen for\n'
         '    new sh.Up at /bad\n'
         '        miofile.write "uploads/evil2.exe" "MZ"\n'
         '        give back ok "wrote it"\n'
         '    new: done\n'
         '    new sh.Up at /good\n'
         '        miofile.write "uploads/good2.jpg" "jpegdata"\n'
         '        give back ok "wrote it"\n'
         '    new: done\n'
         'listen: done\n')
prog = transform(_P.parse(SERVE), SERVE)
server = MohioServer(prog, MohioInterpreter())
c = TestClient(create_app(server), raise_server_exceptions=False)
bad = c.post("/bad", json={"note": "n"}).text
good = c.post("/good", json={"note": "n"}).text
check_true("serve refuses the .exe and says why", "executable" in bad)
check_true("serve allows the .jpg", "wrote it" in good)
check("serve wrote only the .jpg",
      (os.path.isfile(os.path.join(_AREA, "uploads", "good2.jpg")),
       os.path.exists(os.path.join(_AREA, "uploads", "evil2.exe"))), (True, False))

shutil.rmtree(_AREA, ignore_errors=True)
print(f"\nRESULTS: {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
