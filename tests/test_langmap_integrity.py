# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""Language pack integrity: refuse a pack that can change what a program MEANS.

A language pack rewrites source before the parser sees it. A wrong pack does not produce a clumsy
translation, it produces a DIFFERENT PROGRAM, and it does so silently, because every substitution
it makes is textually valid.

THE DISTINCTION THIS TEST PROTECTS
  REFUSED: a pack that can change meaning -- a collision, an accent-only duplicate, a retired
           keyword, a failed round-trip, an incompatible version.
  ALLOWED: a pack that is merely INCOMPLETE. Unmapped keywords fall back to English, which is how
           a community pack grows. A partial pack must stay shippable on day one.

Getting that boundary wrong in either direction is expensive: refuse too much and nobody can build
a pack, refuse too little and a shared file means different things to different readers.

WHAT WAS ACTUALLY BROKEN
A file declaring `// language: spanish` with no pack installed compiled AS ENGLISH and reported
`No terminal matches 'h'` -- a syntax error about the developer's own words, with no indication
the pack was missing. The declared intent was silently discarded. In mio.py this was explicit:
validation was "informational -- never block compilation", and a missing pack was swallowed by
`except (ImportError, FileNotFoundError): pass`.
"""
import os, subprocess, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MAPS = os.path.join(_ROOT, 'maps')
_ENV = dict(os.environ, PYTHONPATH=_ROOT, DATABASE_URL=':memory:',
            MOHIO_ENCRYPTION_KEY='testkey')

from mohio_langmap import LangmapLoader, verify_pack, RETIRED_KEYWORDS

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)


def pack(body):
    fd, path = tempfile.mkstemp(suffix='.langmap')
    os.write(fd, body.encode('utf-8')); os.close(fd)
    return path


def codes(path):
    try:
        return sorted({c for sev, c, _ in verify_pack(LangmapLoader(path)) if sev == 'refuse'})
    finally:
        os.unlink(path)


# ── each meaning-changing failure is caught ───────────────────────────────────────────
check("two keywords mapping to one word is refused (collision)",
      'PACK_COLLISION' in codes(pack('version = "1.0"\nsave <-> guardar\nremove <-> guardar\n')))
check("entries differing only by accent are refused",
      'PACK_ACCENT_COLLISION' in codes(
          pack('version = "1.0"\nnumber <-> n\u00famero\ncount <-> numero\n')))
check("a pack teaching retired keywords is refused",
      'PACK_RETIRED_KEYWORD' in codes(
          pack('version = "1.0"\nif <-> si\ncatch <-> capturar\n')))
check("a failed round-trip is refused",
      'PACK_ROUNDTRIP' in codes(pack('version = "1.0"\nsave <-> guardar\nremove <-> guardar\n')))
check("retired set includes the keywords the Spanish pack actually mapped",
      {'if', 'catch', 'set'} <= RETIRED_KEYWORDS)

# ── a correct pack is clean ───────────────────────────────────────────────────────────
check("a well-formed pack has no findings",
      codes(pack('version = "1.0"\nsave <-> guardar\nremove <-> eliminar\n')) == [])

# ── the shipped packs are clean (no false positives on real packs) ────────────────────
for shipped in ('en-emoji.langmap', 'en-klingon.langmap'):
    path = os.path.join(_MAPS, shipped)
    if os.path.exists(path):
        found = [c for sev, c, _ in verify_pack(LangmapLoader(path)) if sev == 'refuse']
        check(f"shipped pack {shipped} passes integrity", not found, str(found))


def run_check(source, lang_pack=None, pack_name=None):
    """Write a program (optionally installing a pack) and run `mio check`."""
    installed = None
    if lang_pack is not None:
        installed = os.path.join(_MAPS, f'en-{pack_name}.langmap')
        open(installed, 'w', encoding='utf-8').write(lang_pack)
    fd, src = tempfile.mkstemp(suffix='.mho')
    os.write(fd, source.encode('utf-8')); os.close(fd)
    try:
        r = subprocess.run([sys.executable, os.path.join(_ROOT, 'mio.py'), 'check', src],
                           env=_ENV, capture_output=True, text=True, timeout=180)
        return r.returncode, r.stdout + r.stderr
    finally:
        os.unlink(src)
        if installed and os.path.exists(installed):
            os.unlink(installed)


# ── a declared pack that is not installed is REFUSED, not compiled as English ─────────
# English fallback is the design: a missing pack must NOT refuse the build, because a file
# whose body is already English -- a stale header, a partly translated file -- still compiles,
# and refusing it would break working code over a comment. What it must not do is stay silent.
_code_stale, _out_stale = run_check(
    '// language: nosuchlang\nshape Order\n    method POST\nshape: done\ngive back 200 "ok"\n')
check("an English body with a stale language header still COMPILES", _code_stale == 0,
      _out_stale[-220:])
check("a missing pack is announced rather than passed over silently",
      'LANGMAP_MISSING' in _out_stale, _out_stale[-220:])

# And when the body really is foreign, the failure is attributed to the pack rather than blamed
# on the developer's code.
_code_foreign, _out_foreign = run_check(
    '// language: nosuchlang\nforma Pedido\n    metodo POST\nforma: hecho\n')
check("a foreign body without its pack fails", _code_foreign != 0)
check("the failure is attributed to the missing pack, not just 'No terminal matches'",
      'NOT INSTALLED' in _out_foreign, _out_foreign[-260:])

# ── a meaning-changing pack is refused at compile time ────────────────────────────────
_code_bad, _out_bad = run_check(
    '// language: badp\nguardar x to db.t\n',
    lang_pack='version = "1.0"\nsave <-> guardar\nremove <-> guardar\n', pack_name='badp')
check("a colliding pack is refused when a program declares it", _code_bad != 0)
check("the refusal names the specific integrity failure",
      'PACK_COLLISION' in _out_bad, _out_bad[-220:])

# ── an INCOMPLETE pack still compiles (the growth path stays open) ────────────────────
_code_inc, _out_inc = run_check(
    '// language: partialp\ngive back 200 "ok"\n',
    lang_pack='version = "1.0"\nsave <-> guardar\n', pack_name='partialp')
check("an incomplete pack is NOT refused (unmapped keywords fall back to English)",
      'cannot be used' not in _out_inc, _out_inc[-220:])


# ── accents: a developer types their language as they actually type it ────────────────
# A pack mapping `method <-> método` used to leave `metodo` (typed without the accent)
# UNTRANSLATED. The file still compiled, because the untranslated word became an ordinary
# identifier -- so `metodo GET` silently declared a FIELD CALLED "metodo" instead of setting
# the HTTP method. Same file, different program, no warning.
#
# Folding is only safe because verify_pack refuses any pack with two entries differing only by
# accent. The refusal and the fold are a pair: neither is correct alone.
import io as _io, contextlib as _ctx
import mio as _mio
from mohio_transformer_ast import transform as _tf2

_ACC = os.path.join(_MAPS, 'en-accenttest.langmap')
open(_ACC, 'w', encoding='utf-8').write(
    'version = "1.0"\nshape   <-> forma\nmethod  <-> m\u00e9todo\n')
try:
    def _ast(body):
        fd, path = tempfile.mkstemp(suffix='.mho')
        os.write(fd, body.encode('utf-8')); os.close(fd)
        try:
            _buf = _io.StringIO()
            with _ctx.redirect_stdout(_buf):
                _tree, _c = _mio._parse_and_validate(open(path, encoding='utf-8').read(),
                                                     path, False)
                return repr(_tf2(_tree, open(path, encoding='utf-8').read()).statements)
        finally:
            os.unlink(path)

    _with = _ast('// language: accenttest\nforma S\n    m\u00e9todo GET\nshape: done\n'
                 'give back 200 "ok"\n')
    _without = _ast('// language: accenttest\nforma S\n    metodo GET\nshape: done\n'
                    'give back 200 "ok"\n')
    check("a keyword typed WITH its accent translates", "name='method'" in _with, _with[:120])
    check("the same keyword typed WITHOUT the accent also translates",
          "name='method'" in _without, _without[:120])
    check("accented and unaccented spellings produce the SAME program",
          _with == _without)
finally:
    if os.path.exists(_ACC):
        os.unlink(_ACC)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
