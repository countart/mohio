# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""An upload declared in a cloud area goes to the bucket, sealed, and comes back only when allowed.

A CLOUD AREA USED TO THROW ITS OWN DECLARATION AWAY. `miofile_cloud_body` returned its children
unread, and because `_BUCKET`, `_REGION`, `_KEY`, `_SECRET` and `_ENDPOINT` are filtered
terminals, nothing in those children said which setting was which: `bucket "b"` and
`region "auto"` arrived identical. So the whole body was dropped. Verified on an area declaring
five things:

    {'kind': 'cloud', 'name': 'vault', 'path': None, 'policies': []}

The bucket, the region and the endpoint went, and `accept` and `max size` went with them, so a
cloud area enforced none of the rules the same words enforce on a local one. That is the
accept-and-ignore disease `test_miofile_zones.py` was written to kill, surviving in the branch
that test never reached.

THE ONLY NEW THING HERE IS WHICH STORE ANSWERS. The content is sealed before it is handed over,
the handler's own access check runs before any read, and the bytes are unsealed by their marker
afterwards, all exactly as they already do for a local area. A bucket is a different place to put
an object, not a different set of rules about it. That is why this battery asserts the dispatch
and the configuration, and leans on the local batteries for the rules themselves.

THE REAL BUCKET ROUND TRIP runs only when the store is actually configured, and says so LOUDLY
when it is not. A cloud test that quietly passes with no bucket is how a backend ends up with no
coverage, which is exactly how the Mongo `modify` break survived.

Run: PYTHONPATH=$PWD DATABASE_URL=:memory: python tests/test_battery_cloud_upload_zone.py
"""
import os
import shutil
import sys
import tempfile

sys.argv = ['mio.py']
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault('DATABASE_URL', ':memory:')
os.environ.setdefault('MOHIO_ENCRYPTION_KEY', 'testkey')

import mohio_data  # noqa: E402
from lark import Lark  # noqa: E402
from mohio_transformer_ast import transform  # noqa: E402
from mohio_interpreter import MohioInterpreter, Context  # noqa: E402

_p = _f = 0
_failures = []


def check(label, cond, detail=""):
    global _p, _f
    print("  [" + ("PASS" if cond else "FAIL") + "] " + label)
    if not cond:
        if detail:
            print("          " + str(detail)[:400])
        _failures.append(label)
    _p += bool(cond)
    _f += (not cond)


_g = '\n'.join(l for l in mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8').splitlines()
               if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

# The env names the store actually reads. Two are AWS-standard and three are Mohio-prefixed, and
# that split is asserted below so it cannot drift without someone noticing.
CRED_ENV = ('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY')
STORE_ENV = ('MOHIO_STORE_ENDPOINT', 'MOHIO_STORE_BUCKET', 'MOHIO_STORE_REGION')


def with_env(src, env):
    """Run a program's declarations with `env` in force, and hand back the interpreter."""
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        it = MohioInterpreter(verbose=False)
        ctx = Context()
        for st in transform(_P.parse(src), src).statements:
            it._exec(st, ctx)
        return it, dict(env)
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def store_of(src, env):
    """The store a program's uploads would use, resolved inside the env window.

    A value of None UNSETS that name for the window. That is not a convenience: the
    unconfigured-area check below only ever passed because the machine running it happened to
    have no bucket configured, and the moment real Tigris credentials were present it asserted
    the opposite of what it meant. A test about absence has to create the absence.
    """
    old = {k: os.environ.get(k) for k in env}
    for _k, _v in env.items():
        if _v is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _v
    try:
        # BOTH MOMENTS ARE CAUGHT, and returned rather than raised. The commercial gate refuses
        # while the area is being declared and a missing bucket refuses when the store is asked
        # for, so a helper that guarded only the second would let the first end the run.
        try:
            it = MohioInterpreter(verbose=False)
            ctx = Context()
            for st in transform(_P.parse(src), src).statements:
                it._exec(st, ctx)
            return it._upload_store()
        except Exception as e:
            return e
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def refusal(src, env):
    """The refusal a program produces, whether it is raised while the area is DECLARED or when
    the store is first ASKED FOR.

    Two different moments refuse here and both are real: the commercial gate refuses at the
    declaration, a missing bucket refuses at the dispatch. A helper that understood only one of
    them would report the other as silence, which is the shape this whole file is about.
    """
    r = store_of(src, env)
    return str(r) if isinstance(r, Exception) else ""


def cfg(store):
    """(kind, endpoint, bucket, region), or the failure.

    `store_of` hands back an exception rather than raising it, so a broken dispatch produces a
    complete list of failed assertions instead of ending the run at the first one.
    """
    if isinstance(store, Exception):
        return ('raised: ' + str(store)[:80], None, None, None)
    return (type(store).__name__, getattr(store, 'endpoint', None),
            getattr(store, 'bucket', None), getattr(store, 'region', None))


LIC = {'MOHIO_LICENSE': 'x'}
FAKE_CREDS = dict(LIC, AWS_ACCESS_KEY_ID='k', AWS_SECRET_ACCESS_KEY='s')
CLOUD = ('miofile\n    cloud vault\n        bucket "declared-bucket"\n        region "auto"\n'
         '        endpoint "https://t3.storage.dev"\n'
         '        accept pdf\n        max size 9mb\n'
         'miofile: done\nshow "x"\n')


# ── the declaration survives at all ──────────────────────────────────────────────────────────
print("\n== a cloud area keeps what was declared on it ==")
it, _ = with_env(CLOUD, FAKE_CREDS)
zones = [z for z in (it._miofile_zones or []) if z.get('kind') == 'cloud']
check("the area is registered", len(zones) == 1, it._miofile_zones)
z = zones[0] if zones else {}
st = z.get('settings') or {}
check("...keeping its bucket", st.get('bucket') == 'declared-bucket', st)
check("...its region", st.get('region') == 'auto', st)
check("...and its endpoint", st.get('endpoint') == 'https://t3.storage.dev', st)

print("\n-- and the SAFETY policies, which a local area has always enforced --")
check("`accept` survives on a cloud area", z.get('accept') == ['pdf'], z)
check("`max size` survives on a cloud area", z.get('maxsize') == 9 * 1024 * 1024, z)


# ── the dispatch ─────────────────────────────────────────────────────────────────────────────
print("\n== uploads dispatch to the bucket for a cloud area, and to disk otherwise ==")
kind, ep, bk, rg = cfg(store_of(CLOUD, FAKE_CREDS))
check("a cloud area routes uploads to the object store", kind == 'S3ObjectStore', kind)
check("...configured from the DECLARATION, which is the more specific answer",
      (ep, bk, rg) == ('https://t3.storage.dev', 'declared-bucket', 'auto'), (kind, ep, bk, rg))

kind, ep, bk, _r = cfg(store_of('miofile\n    cloud vault\nmiofile: done\nshow "x"\n',
                                dict(FAKE_CREDS, MOHIO_STORE_ENDPOINT='https://env.example',
                                     MOHIO_STORE_BUCKET='env-bucket',
                                     MOHIO_STORE_REGION='auto')))
check("the environment answers for whatever the declaration left unsaid",
      (ep, bk) == ('https://env.example', 'env-bucket'), (kind, ep, bk))

kind, _e, _b, _r = cfg(store_of('show "x"\n', {}))
check("a program with no cloud area still stores on disk", kind == 'LocalObjectStore', kind)

print("\n-- the env names are a MIX, and that is asserted so it cannot drift silently --")
import inspect  # noqa: E402
from mohio_metasource_store import S3ObjectStore  # noqa: E402
src_init = inspect.getsource(S3ObjectStore.__init__)
for n in STORE_ENV + CRED_ENV:
    check(f"the store reads {n}", n in src_init)
check("...and reads no other AWS endpoint spelling, so the names are exactly these",
      'AWS_ENDPOINT_URL' not in src_init, src_init[:200])


# ── the refusals ─────────────────────────────────────────────────────────────────────────────
print("\n== a cloud area is refused in open core, and reached only when licensed ==")
msg = refusal(CLOUD, {'MOHIO_ENFORCE_LICENSE': '1'})
check("open core refuses a cloud area", "commercial" in msg.lower(), msg[:160])
check("...naming what to do instead", "local or temp" in msg.lower(), msg[:200])

print("\n-- an unconfigured cloud area fails loud, naming every missing setting --")
UNSET_STORE = dict(LIC, **{n: None for n in STORE_ENV + CRED_ENV})
msg = refusal('miofile\n    cloud vault\nmiofile: done\nshow "x"\n', UNSET_STORE)
check("it refuses rather than storing somewhere nobody chose",
      "not configured" in msg, msg[:160])
check("...listing the names that are missing",
      "AWS_ACCESS_KEY_ID" in msg and "MOHIO_STORE_REGION" in msg, msg[:250])

print("\n-- two cloud areas refuse rather than picking one --")
# Guessing here would put a health record in whichever bucket happened to be declared first.
msg = refusal('miofile\n    cloud a\n        bucket "b1"\n    cloud b\n        bucket "b2"\n'
             'miofile: done\nshow "x"\n', FAKE_CREDS)
check("it refuses", "upload_zone_ambiguous" in msg, msg[:160])
check("...naming both areas", "a, b" in msg, msg[:200])


# ── the real bucket ──────────────────────────────────────────────────────────────────────────
print("\n== the real round trip, against a live S3-compatible bucket ==")
_configured = all(os.environ.get(n) for n in STORE_ENV + CRED_ENV)
if not _configured:
    _missing = [n for n in STORE_ENV + CRED_ENV if not os.environ.get(n)]
    # SAID OUT LOUD. A cloud test that passes quietly with no bucket is how a backend ends up
    # with no coverage at all, which is exactly how the Mongo modify break survived.
    print("  NOT RUN: no bucket is configured, so the cloud path has NO live coverage in this")
    print("           run. Set " + ", ".join(_missing) + " to exercise it.")
    check("the dispatch and configuration were still proved above", True)
else:
    import uuid  # noqa: E402
    store = S3ObjectStore()
    key = "mohio-battery/" + uuid.uuid4().hex + ".bin"
    it = MohioInterpreter(verbose=False)
    SECRET = b"SENSITIVE MEDICAL CONTENT"
    sealed = it._seal_upload_body(SECRET)
    try:
        store.put(key, sealed, content_type="application/octet-stream")
        body, _etag = store.get(key)
        check("the object came back from the bucket", body is not None, None)
        check("...and what is STORED there is ciphertext, not the file",
              body is not None and body.startswith(b"enc:v1:"), (body or b"")[:40])
        check("...with the secret nowhere in the stored bytes",
              body is not None and SECRET not in body, (body or b"")[:60])
        check("...and it unseals back to exactly the file",
              it._unseal_upload_body(body) == SECRET, None)

        PNG = b'\x89PNG\r\n\x1a\n' + bytes(range(256)) * 3 + b'\x00\xff\xfe\xfd'
        bkey = "mohio-battery/" + uuid.uuid4().hex + ".png"
        store.put(bkey, it._seal_upload_body(PNG), content_type="image/png")
        bback, _ = store.get(bkey)
        check("a binary file round-trips through the bucket byte-identical",
              it._unseal_upload_body(bback) == PNG, None)
        store.delete(bkey)

        pkey = "mohio-battery/" + uuid.uuid4().hex + ".txt"
        PLAIN = b"ordinary notes"
        store.put(pkey, PLAIN, content_type="text/plain")
        pback, _ = store.get(pkey)
        check("an untagged upload is stored in the bucket as written, with no inference",
              pback == PLAIN, pback)
        store.delete(pkey)

        check("the key is listed while it exists", key in (store.list("mohio-battery/") or []),
              None)
        store.delete(key)
        gone, _ = store.get(key)
        check("delete removes the object from the bucket", gone is None, gone)
    finally:
        for k in (key,):
            try:
                store.delete(k)
            except Exception:
                pass

if _failures:
    print("\nFAILURES, repeated so the suite runner's tail carries them:")
    for line in _failures:
        print("  * " + line)
print("\nRESULTS: " + str(_p) + " passed, " + str(_f) + " failed")
sys.exit(1 if _f else 0)
