# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""T1-SILENT-SWEEP-BATCH7 (2026-08-15): mohio_mioscript.py's _name_expr() -- resolving a bare
name read in a client-value expression (`put NAME into ...`, etc.) -- used to silently fall
back to 'event.target.value' for ANY name that wasn't a known event datum (value/key/x/y), a
declared `hold` var, or `result`. A typo (e.g. `put wdth into #out`, meant to read some other
element property) silently compiled to the WRONG JavaScript and shipped to real browsers with
no error at compile or run time.

Fixed: _name_expr() now raises ValueError naming the unrecognized value. Verified real,
declared vars (compile_listeners() always collects every `hold`-declared var across all
listeners BEFORE compiling any of them, so a legitimately declared var is never mistaken for
an unknown one) and the real event data (value/key/x/y) and `result` remain unaffected.
Verified through the REAL runtime path (`mio run`), not just the isolated compile_listeners()
call -- `mio check` does NOT catch this (MioScript only compiles at run/serve time, per the
module's own docstring), so `mio run`/`mio serve` is the real entry point for this class of
error.

Run: `python tests/test_mioscript_unknown_value_failloud.py`.
"""
import os, sys, subprocess
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mohio_data
from lark import Lark
from mohio_transformer_ast import transform
from mohio_mioscript import compile_listeners
from mohio_ast import ClientListener

_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)

def listeners_from(src):
    prog = transform(P.parse(src), src)
    out = []
    for stmt in prog.statements:
        body = getattr(stmt, 'listeners', None) or getattr(stmt, 'body', None) or []
        out.extend(s for s in body if isinstance(s, ClientListener))
    return out

# ── the isolated compiler ────────────────────────────────────────────────────
TYPO_SRC = ('listen for\n    listen for change on #inp\n        put wdth into #out\n'
            '    listen: done\nlisten: done')
try:
    compile_listeners(listeners_from(TYPO_SRC))
    check("an unrecognized client-value name raises (was: silently 'event.target.value')", False)
except ValueError as e:
    check("an unrecognized client-value name raises ValueError naming it", 'wdth' in str(e), str(e))

VALUE_SRC = ('listen for\n    listen for change on #inp\n        put value into #out\n'
             '    listen: done\nlisten: done')
js = compile_listeners(listeners_from(VALUE_SRC))
check("the real 'value' event datum is unaffected (regression)", 'event.target.value' in js, js)

HOLD_SRC = ('listen for\n    listen for change on #inp\n        hold saved = value\n'
            '    listen: done\n    listen for click on #btn\n        put saved into #out\n'
            '    listen: done\nlisten: done')
js2 = compile_listeners(listeners_from(HOLD_SRC))
check("a declared `hold` var is unaffected (regression)", '_moState' in js2, js2)

# ── the REAL runtime path: mio run ───────────────────────────────────────────
def run_cli(src, path_arg='/home'):
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".mho")
    os.write(fd, src.encode())
    os.close(fd)
    try:
        env = dict(os.environ, DATABASE_URL=':memory:', MOHIO_ENCRYPTION_KEY='testkey')
        result = subprocess.run(
            [sys.executable, 'mio.py', 'run', path,
             '--request', f'{{"_path": "{path_arg}", "_method": "GET"}}'],
            capture_output=True, text=True, env=env, timeout=60)
        return result.returncode, result.stdout + result.stderr
    finally:
        os.unlink(path)

JOURNEY_TYPO = (
    'journey MioScriptApp\n'
    '    page Home at /home\n'
    '        render\n'
    '            <input id="inp">\n'
    '            <div id="out"></div>\n'
    '        render: done\n'
    '    page: done\n'
    '    listen for\n'
    '        listen for change on "#inp"\n'
    '            put wdth into "#out"\n'
    '        listen: done\n'
    '    listen: done\n'
    'journey: done\n')
rc, out = run_cli(JOURNEY_TYPO)
check("real `mio run`: a typo'd client-value exits non-zero", rc != 0, f"rc={rc} out={out[:300]}")
check("real `mio run`: the error names the bad value", 'wdth' in out, out[:300])

JOURNEY_VALID = (
    'journey MioScriptApp2\n'
    '    page Home at /home\n'
    '        render\n'
    '            <input id="inp">\n'
    '            <div id="out"></div>\n'
    '        render: done\n'
    '    page: done\n'
    '    listen for\n'
    '        listen for change on "#inp"\n'
    '            put value into "#out"\n'
    '        listen: done\n'
    '    listen: done\n'
    'journey: done\n')
rc2, out2 = run_cli(JOURNEY_VALID)
check("real `mio run`: a legitimate mioscript listener still runs (regression)",
      rc2 == 0, f"rc={rc2} out={out2[:300]}")
check("real `mio run`: the compiled JS is actually injected (regression)",
      'event.target.value' in out2, out2[:500])

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
