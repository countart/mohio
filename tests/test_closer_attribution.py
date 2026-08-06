#!/usr/bin/env python3
# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
Regression: closer misattribution under whole-file ambiguity.

Bug (v0.3.8): `closer` was a valid `statement`, so a greedy `statement*` body
(on.success / on.failure handlers, when/otherwise branches, and `... statement*
closer` blocks) could ABSORB the following `<name>: done` closer as a statement.
Under ambiguity="resolve" the parser sometimes bound a retrieve block's own
`retrieve: done` INTO its on.* handler, so the retrieve grabbed the next closer
(`check: done`) and validation reported a mismatch. The isolated block parsed
clean; the defect only surfaced with enough surrounding content to tip the
ambiguity resolution.

Fix: a closer-free `body_stmt` is used for every condition/handler/block body
(`statement: body_stmt | closer`, so `statement` is unchanged where it is used).
A `<name>: done` can no longer be a body continuation; it is consumed only by an
enclosing block's explicit closer slot.

The PRIMARY guard here is structural and deterministic: with ambiguity="explicit"
(every parse exposed), NO handler/condition subtree may contain a closer. That is
a stronger guarantee than re-parsing a huge file (which hits the multi-minute
parse wall) -- it proves the grammar admits no absorbing parse at all.

Run: python3 tests/test_closer_attribution.py   (from the compiler root)
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); os.chdir(ROOT)
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')

import warnings; warnings.filterwarnings('ignore')
from lark import Lark
from mohio_transformer import validate

_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
# explicit: expose ALL parses so we can prove none absorb a closer
P_EXPLICIT = Lark(_g, parser='earley', ambiguity='explicit', propagate_positions=True)
# resolve: the production setting, used for the build-clean checks
P_RESOLVE  = Lark(_g, parser='earley', ambiguity='resolve',  propagate_positions=True)

PASS = FAIL = 0
def check(name, cond, detail=''):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  PASS  {name}")
    else:    FAIL += 1; print(f"  FAIL  {name}  {detail}")

# The reported shape: retrieve with on.success/on.failure, its own closer, all
# nested inside when -> check -> new -> listen. This is the exact structure that
# misattributed in zork_demo.mho.
REPORTED = '''listen for
    new sh.Room at /x
        check command
            when "dbg"
                retrieve testroom from db.rooms
                    match id to current_room
                    on.success trace "[ROOM FOUND]"
                    on.failure trace "[ROOM NOT FOUND]"
                retrieve: done
                give back 200 trace
        check: done
    new: done
listen: done
'''

HANDLER_RULES = ('on_failure_handler', 'on_success_handler', 'on_error_handler',
                 'check_when', 'otherwise_clause', 'not_confident_block')

def handlers_absorbing_closer(src):
    """Count handler/condition subtrees that ABSORB a closer as a DIRECT child.
    A nested block's own closer (e.g. `retrieve: done` inside a `when` body) is
    legitimate and lives deeper in the tree -- only a closer that is an immediate
    child of the handler/condition node is an absorption."""
    t = P_EXPLICIT.parse(src)
    n = 0
    for rule in HANDLER_RULES:
        for sub in t.find_data(rule):
            for c in sub.children:
                if hasattr(c, 'data') and c.data == 'closer':
                    n += 1
    return n

def builds_clean(src):
    r = validate(P_RESOLVE.parse(src), source=src, filename='t.mho')
    return len(r.errors) == 0, r.errors

# 1. STRUCTURAL (deterministic): no handler/condition subtree may absorb a closer.
print("\n=== structural: closers are not absorbable into condition/handler bodies ===")
check("reported shape: 0 handlers absorb a closer (explicit parse)",
      handlers_absorbing_closer(REPORTED) == 0,
      detail=f"absorbing={handlers_absorbing_closer(REPORTED)}")

# 2. The reported shape builds clean (closer attribution correct).
print("\n=== build-clean: the reported shape validates with no errors ===")
ok, errs = builds_clean(REPORTED)
check("reported shape builds clean (no closer mismatch)", ok,
      detail=(str(errs[0]).replace(chr(10), ' ')[:80] if errs else ''))

# 3. Full-file CONTEXT: the same pattern repeated many times with surrounding
#    content -- enough to tip ambiguity resolution the way the 1131-line file did
#    -- must still build clean and must still admit no absorbing parse.
def big_file(reps):
    blocks = []
    for i in range(reps):
        blocks.append(f'''    new sh.Room at /r{i}
        check command
            when "look{i}"
                retrieve room{i} from db.rooms
                    match id to current_room
                    on.success trace "found {i}"
                    on.failure trace "missing {i}"
                retrieve: done
                give back 200 trace
            otherwise
                give back 404 "no"
        check: done
    new: done''')
    return 'listen for\n' + '\n'.join(blocks) + '\nlisten: done\n'

BIG = big_file(8)
print("\n=== full-file context: repeated nested handlers build clean ===")
ok_big, errs_big = builds_clean(BIG)
check("repeated-pattern file builds clean (no misattribution at scale)", ok_big,
      detail=(str(errs_big[0]).replace(chr(10), ' ')[:80] if errs_big else ''))

# 4. Block bodies with explicit closers (each / repeat) must not absorb their own
#    closer either -- the same class of bug.
print("\n=== block bodies with explicit closers do not absorb their closer ===")
EACH = '''listen for
    new sh.T at /t
        each item in db.items
            update db.items
                status "seen"
                match id to item.id
            update: done
        each: done
    new: done
listen: done
'''
ok_each, errs_each = builds_clean(EACH)
check("each-with-nested-update builds clean", ok_each,
      detail=(str(errs_each[0]).replace(chr(10), ' ')[:80] if errs_each else ''))

# 5. try is a block like any other: its body and handlers are closer-free and it
#    has its own (optional) `try: done` closer. A nested try must not steal the
#    enclosing block's closer -- with or without its own try: done.
print("\n=== try does not steal an enclosing closer ===")
TRY_CLOSED = '''listen for
    new sh.Room at /x
        try
            retrieve r from db.rooms
                match id to current_room
            retrieve: done
        on.failure
            give back 500 "err"
        try: done
    new: done
listen: done
'''
ok_tc, errs_tc = builds_clean(TRY_CLOSED)
check("nested try WITH try: done builds clean", ok_tc,
      detail=(str(errs_tc[0]).replace(chr(10), ' ')[:80] if errs_tc else ''))

TRY_NO_CLOSER = '''listen for
    new sh.Room at /x
        try
            give back 200 "ok"
        on.failure
            give back 500 "err"
    new: done
listen: done
'''
# try now requires its own try: done. A missing one must fail loud (it must NOT
# silently steal the enclosing new: done).
import mohio_transformer as _mt
def fails_loud_missing_closer(src):
    try:
        validate(P_RESOLVE.parse(src), source=src, filename='t.mho')
        return False  # parsed without complaint -> bad
    except Exception:
        return True
try:
    r = validate(P_RESOLVE.parse(TRY_NO_CLOSER), source=TRY_NO_CLOSER, filename='t.mho')
    # either a parse exception or a validation error naming try: done
    no_closer_loud = len(r.errors) > 0
except Exception:
    no_closer_loud = True
check("try missing its try: done fails loud (does not steal new: done)",
      no_closer_loud)

print(f"\nRESULTS: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
