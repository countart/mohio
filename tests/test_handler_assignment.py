# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
test_handler_assignment.py — locks the on.success/on.failure assignment fix.

THE BUG (production "read leaflet" crash): inline_action allowed a bare value_expr,
so the FIRST statement of an on.success/on.failure body that was an assignment
(e.g. `verb alias.canonical`) had its leading NAME peeled off as a no-op inline
expression, and the remainder mis-parsed as a service call. The assignment was
silently destroyed — `verb` never became `examine`, so the verb-alias system was
dead and `read` fell through to the (crashing) AI narrator.

THE FIX: remove bare value_expr from inline_action. give back / show / jump inline
forms remain; assignments are parsed by the handler's statement*.

Run: python3 tests/test_handler_assignment.py   (from the compiler root)
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.chdir(ROOT)
from pathlib import Path
from lark import Lark

_raw = Path('mohio.lark').read_text()
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

passed = failed = 0
def check(name, ok, detail=""):
    global passed, failed
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"   {detail}"))
    passed += bool(ok); failed += (not ok)

def first_handler_stmt_kinds(src, handler='on_success_handler'):
    """Return the rule-names of the direct children of the handler subtree."""
    t = P.parse(src + '\n')
    def find(n):
        if hasattr(n, 'data') and n.data == handler:
            yield n
        for c in getattr(n, 'children', []):
            if hasattr(c, 'data'):
                yield from find(c)
    out = []
    for h in find(t):
        for c in h.children:
            if hasattr(c, 'data'):
                out.append(c.data)
    return out

# The exact production pattern: assignment from a retrieved row field.
SRC_SUCCESS = '''listen for
    new sh.T
        verb "read"
        noun "leaflet"
        retrieve alias from db.verb_aliases
            match alias to verb
            on.success
                verb alias.canonical
                command verb & " " & noun
        retrieve: done
        give back 200 verb'''

print("\n=== assignment is the FIRST statement in on.success (not stolen) ===")
kinds = first_handler_stmt_kinds(SRC_SUCCESS, 'on_success_handler')
# Must NOT contain a bare inline_action; the body must be real statement(s).
# Handler bodies are body_stmt* (closer-free, inlined), so the children are the
# statement nodes directly (e.g. 'assignment'), not a 'statement' wrapper.
check("on.success body has NO inline_action stealing the assignment",
      'inline_action' not in kinds, detail=f"kinds={kinds}")
check("on.success body is real statement(s) (assignment present)",
      'assignment' in kinds, detail=f"kinds={kinds}")

# Drill: the first statement must be an assignment to `verb`.
def first_assignment_name(src, handler='on_success_handler'):
    t = P.parse(src + '\n')
    def find(n):
        if hasattr(n, 'data') and n.data == handler: yield n
        for c in getattr(n, 'children', []):
            if hasattr(c, 'data'): yield from find(c)
    for h in find(t):
        for st in h.children:
            # body_stmt is inlined, so an assignment body statement appears
            # directly as an 'assignment' node (no 'statement' wrapper).
            if hasattr(st, 'data') and st.data == 'assignment':
                return str(st.children[0])
    return None
check("first on.success statement is assignment to 'verb'",
      first_assignment_name(SRC_SUCCESS) == 'verb',
      detail=f"got {first_assignment_name(SRC_SUCCESS)!r}")

# on.failure with a leading assignment, same rule.
SRC_FAIL = '''listen for
    new sh.T
        retrieve x from db.t
            match id to "1"
            on.failure
                status "missing"
                give back 404 "nope"
        retrieve: done
        give back 200 "ok"'''
print("\n=== assignment is the FIRST statement in on.failure (not stolen) ===")
kinds_f = first_handler_stmt_kinds(SRC_FAIL, 'on_failure_handler')
check("on.failure body has NO inline_action stealing the assignment",
      'inline_action' not in kinds_f, detail=f"kinds={kinds_f}")

# The inline same-line give back forms MUST still work (give back stays in inline_action).
print("\n=== inline same-line give back forms still parse ===")
def parses(src):
    try:
        P.parse(src + '\n'); return True
    except Exception:
        return False
check("on.failure give back (same line) parses",
      parses('''listen for
    new sh.T
        retrieve x from db.t
            match id to "1"
            on.failure give back 404 "nope"
        retrieve: done
        give back 200 "ok"'''))
check("on.success give back (same line) parses",
      parses('''listen for
    new sh.T
        retrieve x from db.t
            match id to "1"
            on.success give back 200 x.name
        retrieve: done
        give back 200 "ok"'''))

print(f"\nRESULTS: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
