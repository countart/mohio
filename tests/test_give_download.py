# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
Guard: `give <value> as download` hands a file to the requester.

`give back` answers what was asked for. `give` hands something over. The split matters
because a download is not a response format the way json and xml are -- it is a
different act, and folding it into `give back` would give that word a third job.

Two forms, and which one applies is settled at check time:
  give "reports/q3.pdf" as download          -- a path written in place names itself
  give <anything else> as download "name"    -- everything else carries its own name

The name can only be inferred from a path written in place. A variable could hold a
path or raw bytes, and the compiler sees the shape rather than the value, so that case
is refused at check instead of failing on a live request.

The security rule is containment, not a look-safe check on the string: the path is
resolved and must land inside the app folder or the file area, with symlinks followed
first. That is the same boundary the static server and the file area already enforce,
reused rather than reimplemented, so `give` cannot become the soft way around it.
"""
import os, sys, shutil, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import mohio_data
os.environ.setdefault("DATABASE_URL", ":memory:")
os.environ.setdefault("MOHIO_ENCRYPTION_KEY", "testkey")
os.environ.setdefault("MOHIO_SECRET", "testsecret")

from lark import Lark
from mohio_transformer_ast import transform
from mohio_ast import GiveStmt, GiveBackStmt, Literal
from mohio_reachability import scan_give_destination
from mohio_interpreter import MohioInterpreter
from mohio_server import MohioServer, create_app
from starlette.testclient import TestClient

_passed = _failed = 0
def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1; print(f"  ok   {label}")
    else:
        _failed += 1; print(f"  FAIL {label}: got {got!r} want {want!r}")

_g = mohio_data.GRAMMAR_PATH.read_text(encoding="utf-8")
_g = "\n".join(l for l in _g.splitlines() if not l.strip().startswith("//"))
_P = Lark(_g, parser="earley", ambiguity="resolve", propagate_positions=True)

def page(stmt):
    return f'page at /i\n    {stmt}\npage: done\n'

def node_of(stmt):
    from dataclasses import fields, is_dataclass
    prog = transform(_P.parse(page(stmt)), page(stmt))
    def find(n):
        if not is_dataclass(n):
            return None
        if isinstance(n, (GiveStmt, GiveBackStmt)):
            return n
        for f in fields(n):
            v = getattr(n, f.name, None)
            if is_dataclass(v):
                r = find(v)
                if r is not None:
                    return r
            elif isinstance(v, list):
                for i in v:
                    r = find(i)
                    if r is not None:
                        return r
        return None
    return find(prog.statements[0])

def errs(stmt):
    src = page(stmt)
    return scan_give_destination(transform(_P.parse(src), src))

print("test_give_download")

# ---------------------------------------------------------------- parsing
n = node_of('give "reports/q3.pdf" as download')
check("a literal path parses as give", type(n).__name__, "GiveStmt")
check("  its destination is download", n.modifier, "download")
check("  it needs no filename", n.filename, None)
check("  the value stays a literal", isinstance(n.value, Literal), True)

n = node_of('give doc.contents as download "invoice.pdf"')
check("a field with a filename parses as give", type(n).__name__, "GiveStmt")
check("  the filename is kept", n.filename, "invoice.pdf")

# `give` must not disturb `give back`, which is used everywhere.
check("give back is untouched", type(node_of('give back 200 "x"')).__name__,
      "GiveBackStmt")
check("give back with a format is untouched",
      type(node_of('give back 200 report as json')).__name__, "GiveBackStmt")

# ---------------------------------------------------------------- check-time rules
check("a literal path passes check", len(errs('give "reports/q3.pdf" as download')), 0)
check("a field with a filename passes check",
      len(errs('give doc.contents as download "invoice.pdf"')), 0)

check("bare `give` is refused", len(errs('give invoice')) > 0, True)
check("  and says what to use instead",
      "give back" in errs('give invoice')[0].message, True)

check("a variable with no filename is refused",
      len(errs('give invoice as download')) > 0, True)
check("  and asks for the filename",
      "filename" in errs('give invoice as download')[0].message.lower(), True)

check("another `as` destination is refused", len(errs('give x as email "a"')) > 0, True)
check("  and names the service that owns that job",
      "miomail" in errs('give x as email "a"')[0].message, True)

# ---------------------------------------------------------------- end to end
APP = ('page at /ok\n    give "reports/q3.pdf" as download\npage: done\n\n'
       'page at /rename\n    hold who "Smith"\n'
       '    give "reports/q3.pdf" as download "invoice-{{ who }}.pdf"\npage: done\n\n'
       'page at /bytes\n    hold rows "name,total"\n'
       '    give rows as download "export.csv"\npage: done\n\n'
       'page at /esc\n    give "../../etc/passwd" as download\npage: done\n\n'
       'page at /abs\n    give "/etc/passwd" as download\npage: done\n\n'
       'page at /priv\n    give "_private/seed.json" as download\npage: done\n\n'
       'page at /conf\n    give ".env" as download\npage: done\n\n'
       'page at /src\n    give "thing.py" as download\npage: done\n\n'
       'page at /link\n    give "link.txt" as download\npage: done\n\n'
       'page at /cache\n    give "index.mho.cache" as download\npage: done\n')

_dir = tempfile.mkdtemp(prefix="mohio_give_")
_cwd = os.getcwd()
try:
    os.makedirs(os.path.join(_dir, "reports"))
    os.makedirs(os.path.join(_dir, "_private"))
    open(os.path.join(_dir, "reports", "q3.pdf"), "w").write("Q3 REPORT BODY")
    open(os.path.join(_dir, "_private", "seed.json"), "w").write('{"answers":1}')
    open(os.path.join(_dir, ".env"), "w").write("SECRET=abc")
    open(os.path.join(_dir, "thing.py"), "w").write("print(1)")
    # A build artifact derived from source IS source: a parse-tree cache holds every
    # literal in the file, keys included. Denying `.mho` while handing out
    # `index.mho.cache` is the same leak with a different name on it.
    open(os.path.join(_dir, "index.mho.cache"), "wb").write(b"pickled sk-live-SECRET")
    # A link pointing out of the app. This is the case ONLY the containment check
    # catches: the name looks ordinary, no dots and no leading slash, so every
    # string-level rule passes it. Removing the containment check makes this one
    # serve the file outside -- the others keep failing, which is why the guard has
    # to include it to mean anything.
    _outside = os.path.join(tempfile.gettempdir(), "mohio_give_outside_secret.txt")
    open(_outside, "w").write("OUTSIDE SECRET")
    try:
        os.symlink(_outside, os.path.join(_dir, "link.txt"))
        _linked = True
    except (OSError, NotImplementedError):
        _linked = False       # no symlink support on this platform
    os.chdir(_dir)

    client = TestClient(
        create_app(MohioServer(transform(_P.parse(APP), APP), MohioInterpreter())),
        raise_server_exceptions=False)

    r = client.get("/ok")
    check("a real file downloads", r.status_code, 200)
    check("  the browser is told to save it",
          r.headers.get("content-disposition"), 'attachment; filename="q3.pdf"')
    check("  the content type comes from the name",
          (r.headers.get("content-type") or "").startswith("application/pdf"), True)
    check("  the bytes are the file's own", r.text, "Q3 REPORT BODY")

    r = client.get("/rename")
    check("the filename can be built from values",
          r.headers.get("content-disposition"),
          'attachment; filename="invoice-Smith.pdf"')

    r = client.get("/bytes")
    check("a value in hand downloads under its given name",
          r.headers.get("content-disposition"), 'attachment; filename="export.csv"')
    check("  and carries its own content", r.text, "name,total")

    # Containment. Each of these would hand out something the app must never serve.
    if _linked:
        r = client.get("/link")
        check("a link pointing out of the app is refused", r.status_code != 200, True)
        check("  and its target never reaches the browser",
              "OUTSIDE SECRET" in r.text, False)

    r = client.get("/cache")
    check("a parse cache is refused", r.status_code != 200, True)
    check("  and its contents never reach the browser",
          "sk-live-SECRET" in r.text, False)

    for path, label in (("/esc",  "a path climbing out is refused"),
                        ("/abs",  "an absolute path is refused"),
                        ("/priv", "an underscore-private file is refused"),
                        ("/conf", "a config file is refused"),
                        ("/src",  "a source file is refused")):
        r = client.get(path)
        check(label, r.status_code != 200, True)
        check(f"  {label[2:]} sends no file",
              "content-disposition" in {k.lower() for k in r.headers}, False)
finally:
    os.chdir(_cwd)
    shutil.rmtree(_dir, ignore_errors=True)

print(f"\nRESULTS: {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
