# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
test_enforce_gaps.py

The three bugs that were living in main, invisible, because the gate ran only validate() and
never built an AST. Wiring the gate to the single door (mohio_enforce.enforce) exposed all
three on the first run. These lock them shut.

  1. TWO `list` fields in one shape -> "Unmatched block closer".
     A shape body of list fields ALSO matched empty_list_decl as a statement sequence, so
     `shape M` degraded into an assignment and the shape stopped being a shape. One list field
     resolved by luck; two multiplied the ambiguity. Fixed by making the type words reserved
     (NAME no longer claims them) and giving shape_decl priority over the statement reading.

  2. `get X from cache.settings` -> "Unmatched block closer".
     source_ref's `dotted_name` and `NAME` alternatives were written INSIDE a `//` comment, so
     they were never alternatives. Only db.* ever parsed.

  3. `ai.connect` with a `try` block -> closer mismatch.
     Not a compiler bug: ai.chain is retired, `ai.connect` + `order` is canonical, and `try`
     inside a provider block went with it. The GATE TEST was a fossil asserting the retired
     form, and it survived only because no AST was ever built.
"""
import subprocess, sys, os, tempfile

env = dict(os.environ, PYTHONPATH=os.getcwd(), DATABASE_URL=':memory:')
_p = _f = 0


def case(label, src, want_exit=0):
    """Run through the REAL path -- `mio check` -- which goes through the single door."""
    global _p, _f
    fd, path = tempfile.mkstemp(suffix='.mho'); os.write(fd, src.encode()); os.close(fd)
    r = subprocess.run([sys.executable, 'mio.py', 'check', path], env=env,
                       capture_output=True, text=True)
    os.unlink(path)
    ok = (r.returncode == want_exit)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label} (exit {r.returncode}, want {want_exit})")
    _p += ok; _f += (not ok)


# ── Bug 1: repeated list fields in one shape ──────────────────────────────
case("shape: one list field", """
shape Member
    roles as list text
shape: done
""")

case("shape: TWO list fields", """
shape Member
    roles as list text
    tags  as list NAME
shape: done
""")

case("shape: THREE list fields", """
shape Member
    a as list text
    b as list text
    c as list dec
shape: done
""")

case("shape: list of a bare shape name", """
shape Member
    a as list Order
    b as text
shape: done
""")

case("shape: plain repeated types still fine", """
shape Member
    a as int
    b as int
    c as text
shape: done
""")

case("shape: a name CONTAINING a type word", """
shape Member
    intro   as text
    context as text
shape: done
""")

# ── Bug 2: source_ref accepts a dotted source, not just db.* ──────────────
case("get: from a dotted source (cache.settings)", """
get config from cache.settings
    match key to "app_config"
get: done
""")

case("get: from db.settings", """
get config from db.settings
    match key to "app_config"
get: done
""")

# ── Bug 3: ai.connect is `order`; `try` in a provider block is retired ────
case("ai.connect: canonical order block", """
ai.connect fraud_chain
    order
        anthropic model "claude-haiku-4-5-20251001"
        anthropic model "claude-sonnet-4-6"
    order: done
ai.connect: done
""")

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
