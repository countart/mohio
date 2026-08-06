# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
Guard: `give back ... as xml` produces XML on BOTH response paths.

There are two response builders -- one for POST, one for GET and pages. The XML
serializer lived only in the POST one, so a GET set an `application/xml` content type
and then sent `str(body)`: a Python dict repr under an XML header. Every feed and every
sitemap is fetched by GET, so the path that mattered was the one without it.

This is the second time a feature has been half-wired across those two builders (the
download header was the first), which is why the serializer is now a single shared
function rather than a copy in each.
"""
import os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import mohio_data
os.environ.setdefault("DATABASE_URL", ":memory:")
os.environ.setdefault("MOHIO_ENCRYPTION_KEY", "testkey")
os.environ.setdefault("MOHIO_SECRET", "testsecret")

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter
from mohio_server import MohioServer, create_app, _xml_body
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

print("test_xml_output")

# The serializer itself.
check("a plain value is wrapped", _xml_body("hello"),
      '<?xml version="1.0" encoding="UTF-8"?><response>hello</response>')
# A string that already looks like markup is a document the program assembled itself
# (a feed, a sitemap) and passes through untouched -- escaping it would turn a working
# feed into visible angle brackets. Escaping applies to VALUES inside structured data.
check("a pre-assembled document passes through untouched",
      _xml_body("<rss><channel></channel></rss>"), "<rss><channel></channel></rss>")
check("markup inside a value IS escaped",
      "&lt;script&gt;" in _xml_body({"body": "<script>x</script>"}), True)
check("  and is never emitted raw",
      "<script>" in _xml_body({"body": "<script>x</script>"}), False)
check("an ampersand in a value is escaped", "&amp;" in _xml_body({"t": "a & b"}), True)
check("a dict becomes elements",
      "<title>First</title>" in _xml_body({"title": "First"}), True)
check("a list nests under a singular tag",
      "<items><item><title>a</title></item>" in
      _xml_body({"items": [{"title": "a"}]}), True)

# GET -- the path a feed is actually fetched on, and the one that was broken.
GET_APP = ('page at /feed\n'
           '    create posts\n'
           '        title "First post"\n'
           '        link "https://example.com/1"\n'
           '    create: done\n'
           '    give back posts as xml\n'
           'page: done\n')
gi = MohioInterpreter()
gc = TestClient(create_app(MohioServer(transform(_P.parse(GET_APP), GET_APP), gi)),
                raise_server_exceptions=False)
r = gc.get("/feed")
check("GET returns an xml content type",
      (r.headers.get("content-type") or "").startswith("application/xml"), True)
check("GET body is really XML, not a python repr",
      r.text.startswith('<?xml version="1.0"'), True)
check("  the fields are elements", "<title>First post</title>" in r.text, True)
check("  no python dict punctuation leaked", "{'" in r.text, False)

# POST -- must keep working.
POST_APP = ('shape Ping\n    q as text required\nshape: done\n\n'
            'listen for\n'
            '    new sh.Ping at /send\n'
            '        give back 200 "pong" as xml\n'
            '    new: done\n'
            'listen: done\n')
pi = MohioInterpreter()
pc = TestClient(create_app(MohioServer(transform(_P.parse(POST_APP), POST_APP), pi)),
                raise_server_exceptions=False)
pr = pc.post("/send", data={"_csrf": pi._issue_csrf(), "q": "hi"})
check("POST body is XML too", pr.text.startswith('<?xml version="1.0"'), True)
check("  with the value inside", "<response>pong</response>" in pr.text, True)

# json and html must be untouched by any of this.
JSON_APP = ('page at /d\n    create x\n        a "1"\n    create: done\n'
            '    give back x as json\npage: done\n')
ji = MohioInterpreter()
jc = TestClient(create_app(MohioServer(transform(_P.parse(JSON_APP), JSON_APP), ji)),
                raise_server_exceptions=False)
jr = jc.get("/d")
check("json still answers as json",
      (jr.headers.get("content-type") or "").startswith("application/json"), True)
check("  and is not wrapped in xml", jr.text.startswith("<?xml"), False)

print(f"\nRESULTS: {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
