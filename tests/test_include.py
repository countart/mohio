#!/usr/bin/env python3
# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
Lock tests for:
  * include resolution — separate-parse + AST merge, cross-file tasks,
    cycle/dupe guards, missing-include fail-loud.
  * ai.decide hard-failure safety — decide() RAISES AiProviderError on a hard
    failure (revised 2026-08-04: the old "never raises, fell_back AiDecision"
    contract silently conflated a dead provider with a genuine low-confidence
    answer; see tests/test_ai_hard_failure_loud.py for the full ruling and the
    interpreter-side coverage); not confident + on.failure still coexist.
"""
import os, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault('DATABASE_URL', ':memory:')

import mio
from mohio_transformer_ast import transform
from mohio_ast import IncludeDecl, TaskDecl

PASS = FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  PASS  {name}")
    else:    FAIL += 1; print(f"  FAIL  {name}")


def build(path):
    src = open(path, encoding='utf-8').read()
    tree, ctx = mio._parse_and_validate(src, path)
    prog = transform(tree, src)
    return mio._resolve_includes(prog, path)


d = tempfile.mkdtemp(prefix='inc_')
def w(name, text):
    p = os.path.join(d, name)
    open(p, 'w', encoding='utf-8').write(text)
    return p


# 1. include merges the sub-file's statements; no IncludeDecl remains
w('lib.mho', 'hold greeting = "hi"\n')
main = w('main.mho', 'include "lib.mho"\nshow greeting\n')
prog = build(main)
check("include merges sub-file statements",
      not any(isinstance(s, IncludeDecl) for s in prog.statements))
check("merged statements include the sub-file's hold",
      any(type(s).__name__ == 'HoldDecl' for s in prog.statements))

# 2. cross-file task: a task defined in the included file is present after merge
w('spine.mho', 'task greet returns text\n    give back "hi"\ntask: done\n')
m2 = w('uses_task.mho', 'include "spine.mho"\ncall greet\ncall: done\n')
prog2 = build(m2)
check("task from included file is merged (callable across files)",
      any(isinstance(s, TaskDecl) and s.name == 'greet' for s in prog2.statements))

# 3. cycle guard: a <-> b terminates (no infinite recursion)
w('a.mho', 'include "b.mho"\nhold a = "A"\n')
w('b.mho', 'include "a.mho"\nhold b = "B"\n')
m3 = w('cyc.mho', 'include "a.mho"\nshow a\nshow b\n')
try:
    prog3 = build(m3)
    check("cycle a<->b terminates without hang/error", True)
except RecursionError:
    check("cycle a<->b terminates without hang/error", False)

# 4. dupe guard: including the same file twice merges it once
w('dup.mho', 'hold x = "once"\n')
m4 = w('dupmain.mho', 'include "dup.mho"\ninclude "dup.mho"\nshow x\n')
prog4 = build(m4)
holds = [s for s in prog4.statements if type(s).__name__ == 'HoldDecl']
check("duplicate include merged only once", len(holds) == 1)

# 5. missing include fails loud
m5 = w('bad.mho', 'include "nope.mho"\n')
try:
    build(m5)
    check("missing include raises (fail-loud)", False)
except FileNotFoundError:
    check("missing include raises (fail-loud)", True)

# 6. ai.decide hard failure RAISES (2026-08-04 ruling) -- it must not come back as a
#    fell_back AiDecision indistinguishable from a genuine low-confidence answer.
#    Patches _complete (the actual network call), not _decide_impl, so this exercises
#    the real wrapping logic that turns the failure into AiProviderError.
from mohio_ai import AnthropicAiRuntime, AiDecision, AiProviderError
rt = AnthropicAiRuntime.__new__(AnthropicAiRuntime)
rt._model = 'claude-sonnet-4-6'; rt._verbose = False; rt._overrides = {}; rt._chains = {}
rt._calls = 0; rt._call_cap = 0
def _boom(*a, **k):
    raise RuntimeError("network down")
rt._complete = _boom
try:
    rt.decide("narrator", {"x": 1}, threshold=0.9)
    check("ai.decide hard failure -> raises AiProviderError (was: silent fell_back)", False)
except AiProviderError as e:
    check("ai.decide hard failure -> raises AiProviderError (was: silent fell_back)",
          "network down" in str(e))

# 7. grammar: not confident + on.failure coexist in one ai.decide block
from lark import Lark
from pathlib import Path
_raw = Path('mohio.lark').read_text()
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)
# `returns` is required on the ai.decide BLOCK form (`ai.decide NAME returns TYPE`).
# Without it the source is a name-only invoke followed by dangling blocks, which is
# not a declaration at all -- it used to parse only because any `word.word` counted
# as a statement, and that same permissiveness was eating `give back` values.
src = ('ai.decide narrator returns text\n    weigh\n        command\n'
       '    not confident\n        give back 200 "A"\n'
       '    on.failure\n        give back 200 "B"\nai.decide: done\n')
try:
    _P.parse(src)
    check("grammar: not confident + on.failure coexist", True)
except Exception:
    check("grammar: not confident + on.failure coexist", False)

# 8. journey auto-discovery: a journey.mho in the dir auto-merges into a sibling
jd = tempfile.mkdtemp(prefix='jrn_')
def jw(name, text):
    p = os.path.join(jd, name); open(p, 'w', encoding='utf-8').write(text); return p
jw('journey.mho', 'hold spine = "from journey"\n')
page = jw('page.mho', 'show spine\n')
jprog = mio._apply_journey(build(page), page)
check("journey auto-applies to a sibling page (spine merged)",
      any(type(s).__name__ == 'HoldDecl' for s in jprog.statements))

# 9. main wins on conflict (journey prepended, main's def is last)
over = jw('over.mho', 'hold spine = "main wins"\n')
oprog = mio._apply_journey(build(over), over)
holds = [s for s in oprog.statements if type(s).__name__ == 'HoldDecl']
# journey's spine is first, main's spine is last -> last (main) wins at runtime
check("journey prepended so main's declaration is processed last (main wins)",
      len(holds) >= 1 and oprog.statements[-1] is holds[-1])

# 10. no journey present -> unchanged
nd = tempfile.mkdtemp(prefix='noj_')
np = os.path.join(nd, 'x.mho'); open(np, 'w', encoding='utf-8').write('show "clean"\n')
nprog = mio._apply_journey(build(np), np)
check("no journey.mho -> program unchanged",
      not any(type(s).__name__ == 'HoldDecl' for s in nprog.statements))

print(f"\nRESULTS: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
