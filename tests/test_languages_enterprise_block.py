#!/usr/bin/env python3
# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Regression guard: `languages_block` is a REAL feature, `enterprise_block` fails loud
honestly (journey-newest-decisions-2026-08-07, languages-declaration-2026-08-07).

BATCH8 (T1-SILENT-SWEEP) wrongly classified `languages_block` as genuinely unbuilt and
routed it through the same fail-loud-as-unbuilt mechanism as truly-never-started features
(mioauth, miosearch, ...). It is not unbuilt -- it is a real journey_body member (a langmap
declaration), and this test locks that in so it can never regress that way again:
  1. `languages: done` (the named closer) and bare `done` both work -- the shared `closer`
     rule already carried a `/languages/ ":" DONE` alternative; languages_block just never
     referenced it.
  2. A real journey with a languages block runs end-to-end (no fail-loud) and the parsed
     declaration (current/supported/deploy/planned) is genuinely captured on ctx, not
     silently discarded.
  3. `enterprise_block` still fails loud (a genuine, unrelated gap) -- but with honest
     wording ("not fully built / under review"), not the generic "not built in this
     release" phrasing that implies a simple, never-started deferral.

Runs real `.mho` source through the full pipeline (parse -> transform -> run), not a direct
call to _exec_LanguagesBlock -- the T1-TEST-REAL-PATH-STANDARD this project requires, given
a unit-level call would have passed the whole time this was broken.
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import mohio_data
os.chdir(ROOT)
os.environ.setdefault('DATABASE_URL', ':memory:')

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter, MohioRuntimeError

_raw = mohio_data.GRAMMAR_PATH.read_text()
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  PASS  {name}")
    else:    FAIL += 1; print(f"  FAIL  {name}  {detail}")


def run_and_capture_languages(src):
    """Run real .mho source, spying on _exec_LanguagesBlock to capture what it stored."""
    it = MohioInterpreter()
    t = transform(P.parse(src), src)
    it.run_declarations(t)
    captured = {}
    orig = MohioInterpreter._exec_LanguagesBlock
    def spy(self, node, ctx):
        r = orig(self, node, ctx)
        captured['languages'] = getattr(ctx, '_languages', None)
        return r
    MohioInterpreter._exec_LanguagesBlock = spy
    try:
        it.run(t)
    finally:
        MohioInterpreter._exec_LanguagesBlock = orig
    return captured.get('languages')


NAMED_SRC = ('journey Test\n'
             '    languages\n'
             '        current EN\n'
             '        supported klingon\n'
             '        deploy EN\n'
             '        planned DE\n'
             '    languages: done\n'
             '    show "started"\n'
             'journey: done\n')

BARE_SRC = ('journey Test\n'
            '    languages\n'
            '        current EN\n'
            '    done\n'
            'journey: done\n')


# 1. named closer parses and reaches the real executor
# `supported klingon` (a real installed pack -- see the pack-enforcement check this
# session added to _exec_LanguagesBlock) and `planned DE` (informational, no pack
# required) -- deliberately NOT `supported FR`/`supported PT`, which have no real pack
# in this repo and would now correctly fail loud instead of silently parsing.
cfg = run_and_capture_languages(NAMED_SRC)
check("languages: done (named closer) reaches the real executor", cfg is not None, str(cfg))
check("declaration correctly captured (named closer)",
      cfg == {'current': 'EN', 'supported': ['klingon'], 'deploy': 'EN', 'planned': ['DE'], 'maps': []},
      str(cfg))

# 2. bare done closer still works (backward compatible)
cfg2 = run_and_capture_languages(BARE_SRC)
check("bare done closer still works", cfg2 == {'current': 'EN', 'supported': [], 'deploy': '', 'planned': [], 'maps': []},
      str(cfg2))

# 3. languages_block must NOT fail loud as unbuilt (the BATCH8 regression)
try:
    it = MohioInterpreter()
    t = transform(P.parse(NAMED_SRC), NAMED_SRC)
    it.run_declarations(t)
    it.run(t)
    check("languages_block does not fail loud as unbuilt", True)
except MohioRuntimeError as e:
    check("languages_block does not fail loud as unbuilt", False, str(e))

# 4. enterprise_block still fails loud, but with the HONEST wording
ENTERPRISE_SRC = ('journey Test\n'
                   '    enterprise\n'
                   '        key env.ENTERPRISE_KEY\n'
                   '        tier professional\n'
                   '    enterprise: done\n'
                   '    show "started"\n'
                   'journey: done\n')
try:
    it = MohioInterpreter()
    t = transform(P.parse(ENTERPRISE_SRC), ENTERPRISE_SRC)
    it.run_declarations(t)
    it.run(t)
    check("enterprise_block fails loud (still a genuine gap)", False, "did not raise")
except MohioRuntimeError as e:
    msg = str(e).lower()
    check("enterprise_block fails loud (still a genuine gap)", True)
    check("enterprise message says 'not fully built / under review'",
          'not fully built' in msg and 'under review' in msg, str(e))
    check("enterprise message does NOT use the generic 'not built in this release' phrasing",
          'not built in this release' not in msg, str(e))

print(f"\nRESULTS: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
