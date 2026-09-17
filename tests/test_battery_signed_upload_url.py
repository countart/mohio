# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""`sign upload url for` issues a real signed URL, and it signs the right permission.

WHAT THIS EXISTS TO STOP, and it is not the 403. `_UPLOAD` and `_URL` are Lark terminals
whose names begin with an underscore, which means Lark FILTERS them out of the parse tree.
So `sign url for x` and `sign upload url for x` reached the transformer as the same children
with nothing in them saying which had been written, the transformer settled on the download
form, and the executor signed a GET. A program asking for permission to WRITE was handed
permission to READ.

The visible symptom was a bucket returning 403, because the signature had been computed for
GET and the request was a PUT. That symptom is the lucky one. The method is the only part of
the canonical request that does NOT appear in the URL, so the two forms produced URLs that
were byte-identical in every visible field -- same path, same query, same parameter order,
same length -- and comparing them found nothing. Had the methods happened to line up, the
program would have received a working URL that does the opposite of what it asked for, with
nothing anywhere saying so.

HOW IT WAS FOUND, because the method matters more than the fix. Comparing signing INPUTS
found nothing: endpoint, bucket, region, key, and a fingerprint of the secret all matched
between a path that worked and one that did not. What settled it was recomputing the
signature from the URL's own fields with the known secret and finding it did not match, while
the same recomputation against a URL that DID work matched exactly. That narrowed it to a
signed element not present in the URL, and there is only one.

THE ASSERTIONS BELOW NEED NO BUCKET. Which method a URL was signed for is decidable by
recomputing the signature both ways and seeing which one it matches, so the property this
battery exists to protect is checked deterministically and offline. A live round trip against
a real bucket runs too, but only when one is configured, and it SAYS when it did not run
rather than passing quietly -- a cloud test that goes green with no bucket is how a backend
ends up with no backend.

Run: PYTHONPATH=$PWD python tests/test_battery_signed_upload_url.py
Live round trip as well:
    MOHIO_TEST_S3_ENDPOINT=http://127.0.0.1:9000 MOHIO_TEST_S3_BUCKET=mohio-uploads \
    AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... PYTHONPATH=$PWD python tests/...
"""
import hashlib
import hmac
import io
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("MOHIO_ENCRYPTION_KEY", "testkey")


CAN_FAIL = [
    {
        'file': 'mohio_transformer_ast.py',
        'find': "        has_upload = any(isinstance(c, Token) and c.type == 'UPLOAD' for c in children)",
        'replace': "        has_upload = False",
        'note': 'the upload form is read as the download form again, signing a read',
    },
    {
        'file': 'mohio_interpreter.py',
        'find': '        method = "PUT" if getattr(node, \'sign_type\', \'url\') == "upload url" else "GET"',
        'replace': '        method = "GET"',
        'note': 'every signed URL authorises a read, whatever the program asked for',
    },
]

_p = _f = 0
_failures = []


def check(label, cond, detail=""):
    global _p, _f
    print("  [" + ("PASS" if cond else "FAIL") + "] " + label)
    if not cond and detail:
        print("          " + str(detail).replace("\n", "\n          ")[:700])
    if not cond:
        _failures.append(label)
    _p += bool(cond)
    _f += (not cond)


TMP = tempfile.mkdtemp(prefix="mohio_signurl_")

ENDPOINT = os.environ.get("MOHIO_TEST_S3_ENDPOINT", "https://s3.example.com")
BUCKET = os.environ.get("MOHIO_TEST_S3_BUCKET", "mohio-test-bucket")
AK = os.environ.get("AWS_ACCESS_KEY_ID") or "AKIAIOSFODNN7EXAMPLE"
SK = os.environ.get("AWS_SECRET_ACCESS_KEY") or "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
LIVE = bool(os.environ.get("MOHIO_TEST_S3_ENDPOINT"))

PROGRAM = '''connect db as sqlite from env.DATABASE_URL

miofile
    cloud vault
        bucket "%s"
        region "us-east-1"
        endpoint "%s"
        accept pdf
        max size 9mb
miofile: done

hold target "receipts/invoice-001.pdf"

sign %%s for target
    expires in %%s
    named link
sign: done

show link
''' % (BUCKET, ENDPOINT)


def run_program(spelling, expires="15 minutes"):
    """Run a real program through `mio run` and hand back the URL it printed."""
    path = os.path.join(TMP, "p_%s.mho" % spelling.replace(" ", "_"))
    io.open(path, "w", encoding="utf-8").write(PROGRAM % (spelling, expires))
    env = dict(os.environ, PYTHONPATH=ROOT, DATABASE_URL=":memory:",
               AWS_ACCESS_KEY_ID=AK, AWS_SECRET_ACCESS_KEY=SK)
    r = subprocess.run([sys.executable, os.path.join(ROOT, "mio.py"), "run", path],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=900, env=env, cwd=TMP)
    out = (r.stdout or "") + (r.stderr or "")
    for token in out.split():
        if "X-Amz-Signature=" in token:
            return token, out
    return None, out


def _sign(key, msg):
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def signature_for(url, method, secret=SK, region="us-east-1"):
    """Recompute the signature this URL would carry if it had been signed for `method`.

    Everything the canonical request needs is in the URL except the method and the secret,
    which is exactly why a URL comparison could not see this bug and this can.
    """
    split = urllib.parse.urlsplit(url)
    params = dict(p.split("=", 1) for p in split.query.split("&"))
    params.pop("X-Amz-Signature", None)
    amzdate = params["X-Amz-Date"]
    canonical_query = "&".join(
        "%s=%s" % (urllib.parse.quote(k, safe="-_.~"),
                   urllib.parse.quote(urllib.parse.unquote(params[k]), safe="-_.~"))
        for k in sorted(params))
    canonical = (method + "\n" + split.path + "\n" + canonical_query + "\n"
                 + "host:" + split.netloc + "\n\nhost\nUNSIGNED-PAYLOAD")
    scope = "%s/%s/s3/aws4_request" % (amzdate[:8], region)
    to_sign = ("AWS4-HMAC-SHA256\n" + amzdate + "\n" + scope + "\n"
               + hashlib.sha256(canonical.encode("utf-8")).hexdigest())
    k = _sign(("AWS4" + secret).encode("utf-8"), amzdate[:8])
    k = _sign(k, region)
    k = _sign(k, "s3")
    k = _sign(k, "aws4_request")
    return hmac.new(k, to_sign.encode("utf-8"), hashlib.sha256).hexdigest()


def signature_in(url):
    return dict(p.split("=", 1) for p in urllib.parse.urlsplit(url).query.split("&"))[
        "X-Amz-Signature"]


# ══ 1. THE UPLOAD FORM SIGNS A WRITE ════════════════════════════════════════════════════
print("-- `sign upload url for` signs permission to WRITE -------------------------------")

up_url, up_out = run_program("upload url")
check("a program using `sign upload url for` runs and prints a URL",
      bool(up_url), up_out[-600:])

if up_url:
    check("...and the URL is signed for PUT",
          signature_in(up_url) == signature_for(up_url, "PUT"),
          "signature in URL: %s" % signature_in(up_url))
    # THE ASSERTION THAT WOULD HAVE CAUGHT THE BUG. Before the fix this was true instead.
    check("...and NOT for GET, which is what it silently signed before",
          signature_in(up_url) != signature_for(up_url, "GET"),
          "the upload form signed a read, which is the wrong permission")


# ══ 2. THE DOWNLOAD FORM SIGNS A READ, AND IS A DIFFERENT URL ═══════════════════════════
print("\n-- `sign url for` signs permission to READ, and the two differ -------------------")

dn_url, dn_out = run_program("url")
check("a program using `sign url for` runs and prints a URL", bool(dn_url), dn_out[-600:])

if dn_url:
    check("...and the URL is signed for GET",
          signature_in(dn_url) == signature_for(dn_url, "GET"),
          "signature in URL: %s" % signature_in(dn_url))
    check("...and NOT for PUT", signature_in(dn_url) != signature_for(dn_url, "PUT"))

if up_url and dn_url:
    # The two carry the same visible fields, which is why this is asserted on the signature
    # and not on the URL: every other part of them matches by design.
    up_split, dn_split = urllib.parse.urlsplit(up_url), urllib.parse.urlsplit(dn_url)
    check("the two forms agree on everything visible in the URL",
          up_split.path == dn_split.path and up_split.netloc == dn_split.netloc,
          (up_split.path, dn_split.path))
    check("...and still carry different signatures, because the METHOD is signed too",
          signature_in(up_url) != signature_in(dn_url),
          "the two spellings produced the same signature, so they mean the same thing")


# ══ 3. `expires in` REACHES THE SIGNATURE ═══════════════════════════════════════════════
print("\n-- the lifetime the program asks for is the lifetime the URL carries -------------")

for _spell, _asked, _seconds in (("upload url", "15 minutes", "900"),
                                 ("upload url", "1 hours", "3600"),
                                 ("upload url", "2 days", "172800")):
    _u, _o = run_program(_spell, _asked)
    _got = (dict(p.split("=", 1) for p in urllib.parse.urlsplit(_u).query.split("&"))
            ["X-Amz-Expires"]) if _u else None
    check("`expires in %s` becomes %s seconds" % (_asked, _seconds),
          _got == _seconds, "got %r" % _got)


# ══ 4. WHAT CANNOT BE SIGNED IS REFUSED, NOT GUESSED ════════════════════════════════════
print("\n-- a URL that cannot be signed is refused by name --------------------------------")

_nocloud = os.path.join(TMP, "nocloud.mho")
io.open(_nocloud, "w", encoding="utf-8").write(
    'connect db as sqlite from env.DATABASE_URL\n\nhold t "a/b.pdf"\n\n'
    'sign upload url for t\nsign: done\n\nshow "done"\n')
_env = dict(os.environ, PYTHONPATH=ROOT, DATABASE_URL=":memory:")
_r = subprocess.run([sys.executable, os.path.join(ROOT, "mio.py"), "run", _nocloud],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=900, env=_env, cwd=TMP)
_out = (_r.stdout or "") + (_r.stderr or "")
check("a program with no cloud storage is refused, by name",
      "sign_needs_cloud_storage" in _out, _out[-500:])
check("...and the refusal says a local area cannot sign",
      "directory has no signature" in _out, _out[-500:])

_nocreds = os.path.join(TMP, "nocreds.mho")
io.open(_nocreds, "w", encoding="utf-8").write(PROGRAM % ("upload url", "15 minutes"))
_env2 = dict(os.environ, PYTHONPATH=ROOT, DATABASE_URL=":memory:")
_env2.pop("AWS_ACCESS_KEY_ID", None)
_env2.pop("AWS_SECRET_ACCESS_KEY", None)
_r2 = subprocess.run([sys.executable, os.path.join(ROOT, "mio.py"), "run", _nocreds],
                     capture_output=True, text=True, encoding="utf-8", errors="replace",
                     timeout=900, env=_env2, cwd=TMP)
_out2 = (_r2.stdout or "") + (_r2.stderr or "")
check("a deployment with no credentials is refused, naming what is missing",
      "sign_store_unavailable" in _out2 and "AWS_ACCESS_KEY_ID" in _out2, _out2[-500:])


# ══ 5. A REAL BUCKET, WHEN ONE IS CONFIGURED ════════════════════════════════════════════
print("\n-- against a real bucket ---------------------------------------------------------")

if not LIVE:
    print("  [not run] no MOHIO_TEST_S3_ENDPOINT -- the live round trip was SKIPPED.")
    print("            Every assertion above is offline and still ran. This one needs a")
    print("            bucket: start one and set MOHIO_TEST_S3_ENDPOINT to include it.")
else:
    import urllib.error
    import urllib.request

    def send(method, url, data=None):
        try:
            with urllib.request.urlopen(
                    urllib.request.Request(url, data=data, method=method), timeout=30) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, b""

    _body = b"%PDF-1.4 uploaded straight to the bucket by the browser"
    _st, _ = send("PUT", up_url, _body)
    check("the upload URL a real program printed uploads to a real bucket", _st == 200,
          "HTTP %s" % _st)
    _st2, _got = send("GET", dn_url)
    check("...and the download URL reads the object back", _st2 == 200, "HTTP %s" % _st2)
    check("...with the bytes that were sent", _got == _body, "%d bytes" % len(_got))
    # THE PERMISSION IS REAL, not merely different. Each URL is refused for the other verb.
    check("the upload URL is refused as a read", send("GET", up_url)[0] == 403)
    check("the download URL is refused as a write",
          send("PUT", dn_url, b"not allowed")[0] == 403)

print()
print("=" * 78)
print("RESULTS: %d passed, %d failed" % (_p, _f))
for x in _failures:
    print("  FAILED: " + x)
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _f else 0)
