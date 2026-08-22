# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""T1-EVAL-SIMPLE-FAILLOUD Piece 2, FINAL redesign (2026-08-17, Ronnie's ruling after the first
attempt broke 52 files).

The first attempt put the never-declared fail-loud in `Context.get()` itself -- too fundamental a
primitive, since it also serves internal `__`-prefixed bookkeeping (read via direct Python calls)
and the `forget`/`rename` contract. That broke `__pending_cookies__`-style internal state and the
tested "forget leaves the name readable as empty" contract across ~40 unrelated test files.

The fix instead: `Context.get()` STAYS lenient. The fail-loud lives at the single USER-FACING
variable-read path -- `_eval`'s `DottedName` root resolution (mohio_interpreter.py) -- which is
only reached when evaluating a parsed AST node built from real `.mho` source. Internal `__`
bookkeeping is read via direct `ctx.get(...)` Python calls in interpreter code and never builds a
`DottedName` node, so it structurally never reaches this check at all -- no exemption logic
needed, just the right location.

Ruled states, no tombstone:
  - never-declared (typo)              -> FAIL LOUD (the bug)
  - declared-empty (`name as text`)    -> empty/zero, no error (a real entry exists in _vars)
  - forgotten / renamed-away           -> FAIL LOUD, same treatment as never-declared: `forget`/
                                           `rename` call `delete_var`, which leaves NO trace, so
                                           the two states are genuinely identical at the read site
  - cleared (`clear x`)                -> empty, no error (`clear` calls `ctx.set(...)`, never
                                           `delete_var` -- the variable stays declared)
  - internal `__` bookkeeping          -> unaffected (never reaches this path at all)

Run as a script: `python tests/test_undeclared_variable_failloud.py` (exit 0 = pass).
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); os.chdir(ROOT)
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')
os.environ.setdefault('MOHIO_ENCRYPTION_KEY', 'testkey')

from lark import Lark
from mohio_transformer_ast import transform as ast_transform
from mohio_interpreter import MohioInterpreter

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


def run_real(src):
    """Real path: parse -> transform -> run_declarations -> run. Returns (it, result).

    Two distinct error shapes exist, both meaning "this program failed," matching real `mio run`
    behavior (confirmed live: a served program returns a clean 500, the process doesn't crash --
    the CLI's own top-level handler normalizes this the same way). A `_Raise` reaching `give
    back`'s VALUE is caught by `run()` itself and formatted into a {'status', 'body'} response.
    A `MohioRuntimeError` raised directly by a statement's own guard (e.g. `show`'s pre-existing
    unknown-variable check) propagates as a raw Python exception instead -- normalize both here
    so every check below can just ask `failed(result)`.
    """
    prog = ast_transform(P.parse(src), src)
    it = MohioInterpreter()
    it.run_declarations(prog)
    try:
        result = it.run(prog)
    except Exception as e:
        result = {'status': 500, 'body': str(e)}
    return it, result


def failed(result):
    return isinstance(result, dict) and result.get('status', 200) >= 400


# ── never-declared: bare name, exercised outside `show`'s own separate pre-check ───────────────
it, result = run_real('n (undeclared_bare_math_var + 1)\nshow n\n')
check("never-declared bare name (in a math expr) fails loud",
      failed(result) and 'undeclared_variable' in str(result.get('body', '')), result)
check("message names the undeclared variable",
      'undeclared_bare_math_var' in str(result.get('body', '')), result)

# ── never-declared: the original repro, a dotted root the transformer never bound ──────────────
it, result = run_real('show totally_undeclared_root_xyz.users\n')
check("never-declared dotted root fails loud",
      failed(result) and 'undeclared_variable' in str(result.get('body', '')), result)


# ── declared-empty: a real entry exists in _vars, must stay silent ─────────────────────────────
it, result = run_real('name as text\nshow name\n')
check("declared-empty (`name as text`) shows empty, no error",
      it.shown == [""] and not failed(result), (it.shown, result))

it, result = run_real('n as int\nshow n\n')
check("declared-empty (`n as int`) shows its zero value, no error",
      it.shown == [0] and not failed(result), (it.shown, result))


# ── forgotten: delete_var leaves no trace -- same state as never-declared, fails loud ──────────
# `show`'s own pre-existing unknown-variable guard fires here before this fix's DottedName-layer
# check ever gets a chance to (both are "fails loud", so which one fires first is not the point --
# whichever it is, `forget` must no longer read back silently empty).
it, result = run_real('x 5\nforget x\nshow x\n')
check("forgotten variable fails loud when read afterward (same treatment as never-declared)",
      failed(result), result)


# ── renamed-away: old name is gone (delete_var), new name works ────────────────────────────────
it, result = run_real('x 5\nrename x to y\nshow x\n')
check("renamed-away old name fails loud when read", failed(result), result)

it, result = run_real('x 5\nrename x to y\nshow y\n')
check("renamed new name still works", it.shown == [5] and not failed(result), (it.shown, result))


# ── cleared: clear calls ctx.set(), never delete_var -- variable stays declared, empty ─────────
it, result = run_real('x 5\nclear x\nshow x\n')
check("cleared variable stays declared and reads as empty, no error",
      not failed(result), result)


# ── {{ }} interpolation of a declared-empty field must stay unaffected ─────────────────────────
it, result = run_real('name as text\nshow "hello {{ name }}!"\n')
check("{{ }} interpolation of a declared-empty field still works, no error",
      it.shown == ["hello !"] and not failed(result), (it.shown, result))


# ── regression: internal __ bookkeeping never reaches this check (structurally exempt) ─────────
sys.path.insert(0, HERE)
from test_find import cursor_fails
check("request.cursor (legitimately unbound `request` outside a real HTTP call) still raises "
      "the specific cursor_pagination_unavailable, unaffected by this change",
      cursor_fails())


print(f"\n{_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
