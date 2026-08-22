# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""T1-SILENT-SWEEP-BATCH7 (2026-08-15): a shape field's unrecognized type name (a typo, e.g.
`age as itn`) used to be indistinguishable from "no type declared at all" -- both silently
became "text" in the generated schema. `age as itn` is real, reachable Mohio syntax
(`type_name` accepts any identifier at parse time), so this was not a parse-time-caught typo.

Fixed two things, in the same unit:
1. mohio_schema.py normalize_type() now raises on a genuinely unrecognized scalar type name.
   Unaffected: no type declared at all (still "text", the real default), a real recognized
   type, and a namespaced shape-type reference like "sh.Order" (not a scalar TYPE_MAP entry
   by design, not a typo).
2. mio.py cmd_schema's 'generate' action -- discovered while verifying #1 through the REAL
   `mio schema generate` CLI path: generate_schema() already carried a genuine failure in its
   returned dict's 'error' key (its own graceful-degrade design), but cmd_schema never
   checked it -- so the fail-loud from #1 was silently swallowed, reporting "Schema
   generated" with 0 tables/fields, exit 0, and writing a broken .mhoschema file. Fixed to
   check the 'error' key and fail loud instead.

Run: `python tests/test_schema_type_typo_failloud.py`.
"""
import os, sys, io, subprocess, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mohio_data
from lark import Lark
from mohio_transformer_ast import transform
from mohio_schema import extract_schema_from_program, generate_schema

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

def build(src):
    prog = transform(P.parse(src), src)
    return extract_schema_from_program(prog, "test.mho")

# ── normalize_type() itself ─────────────────────────────────────────────────────
try:
    build("shape Foo\n    age as itn\nshape: done\n")
    check("a typo'd type name raises (was: silently 'text')", False)
except ValueError as e:
    check("a typo'd type name raises ValueError naming the bad value", "itn" in str(e), str(e))

s = build("shape Foo\n    age\nshape: done\n")
check("no type declared at all is still 'text' (regression: the real default)",
      s['tables']['foos']['fields']['age']['type'] == 'text')

s = build("shape Foo\n    age as int\nshape: done\n")
check("a real recognized type still maps correctly (regression)",
      s['tables']['foos']['fields']['age']['type'] == 'number')

s = build("shape Foo\n    order as sh.Order\nshape: done\n")
check("a namespaced shape-type reference is unaffected (not a typo, not a TYPE_MAP scalar)",
      s['tables']['foos']['fields']['order']['type'] == 'text')

# ── generate_schema()'s error propagation ───────────────────────────────────────
schema = generate_schema("shape Foo\n    age as itn\nshape: done\n", "test.mho")
check("generate_schema() carries the failure in its 'error' key (its own design)",
      bool(schema.get('error')) and schema.get('tables') == {}, schema)

# ── the REAL CLI path: mio schema generate ──────────────────────────────────────
def run_cli(src):
    fd, path = tempfile.mkstemp(suffix=".mho")
    os.write(fd, src.encode())
    os.close(fd)
    try:
        env = dict(os.environ, DATABASE_URL=':memory:', MOHIO_ENCRYPTION_KEY='testkey')
        result = subprocess.run(
            [sys.executable, 'mio.py', 'schema', 'generate', path],
            capture_output=True, text=True, env=env, timeout=60)
        schema_path = path[:-4] + '.mhoschema'
        wrote_schema = os.path.exists(schema_path)
        if wrote_schema:
            os.unlink(schema_path)
        return result.returncode, result.stdout + result.stderr, wrote_schema
    finally:
        os.unlink(path)

rc, out, wrote = run_cli("shape Foo\n    age as itn\nshape: done\n")
check("real CLI: a typo'd type exits non-zero (was: exit 0, 'Schema generated', 0 fields)",
      rc != 0, f"rc={rc} out={out[:200]}")
check("real CLI: the error message reaches the user", "itn" in out, out[:300])

rc2, out2, wrote2 = run_cli("shape Foo\n    age as int\n    name as text\nshape: done\n")
check("real CLI: a legitimate shape still generates successfully (regression)",
      rc2 == 0 and "Schema generated" in out2, f"rc={rc2} out={out2[:300]}")
check("real CLI: a legitimate shape's .mhoschema file is actually written (regression)", wrote2)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
