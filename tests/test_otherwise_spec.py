# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
test_otherwise_spec.py

`otherwise` per the ORIGINAL design spec (2026-03-30, Ronnie):

    Works inside any verb block where two paths exist. Always the last line before the block
    closer. Once per block. Never mandatory. It runs when NEITHER on.failure NOR on.success
    fired.

    In `find`, on.failure means NO RESULTS. So `otherwise` means "there were results".

None of this was implemented. Three separate silent failures were stacked on top of each other:

  1. `otherwise_clause` appeared in exactly ONE grammar rule -- check_block. Every other verb
     block simply had no otherwise.

  2. find_block's transformer used an ALLOWLIST for its body items that did not list OnFailure,
     OnSuccess or OtherwiseClause. So `find` PARSED its handlers, checked clean, and then threw
     them away. on.failure never fired on an empty result. Ever.

  3. `_exec_FindBlock` never consulted node.handlers at all, so even a handler that survived
     would not have run.

Result: you could write on.failure in a find block, get a green check, and it would silently
never execute. Now it does, and `otherwise` is the fallback beside it.
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
env = dict(os.environ, PYTHONPATH=ROOT, DATABASE_URL=":memory:",
           MOHIO_ENCRYPTION_KEY="testkey")

_p = _f = 0

SETUP = '''connect db as sqlite from env.DATABASE_URL

shape T
    title as text
shape: done

save to db.rows
    title "present"
save: done

'''


def run(label, body, want, must_not=None):
    """Run a real program and assert which handler actually FIRED."""
    global _p, _f
    fd, path = tempfile.mkstemp(suffix=".mho")
    os.write(fd, (SETUP + body).encode())
    os.close(fd)
    r = subprocess.run([sys.executable, "mio.py", "run", path],
                       env=env, capture_output=True, text=True)
    os.unlink(path)
    out = r.stdout + r.stderr
    ok = want in out and (must_not is None or must_not not in out)
    _p += ok
    _f += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")


# ── find: on.failure means NO RESULTS ─────────────────────────────────────
# DESIGN (Ronnie): on.failure means IT BROKE. An empty result is NOT a failure -- the query
# ran fine and found nothing. Conflating them makes a dead database render as "All caught up!".
# An empty result is a CONDITION, and conditions belong to when / otherwise.
run("find, no results -> on.failure does NOT fire (the query did not break)",
    'find a in db.rows\n'
    '    where title is "absent"\n'
    '    on.failure show "BROKE"\n'
    'find: done\n'
    'show "RAN_FINE"\n',
    want="RAN_FINE", must_not="BROKE")

def no_conn(label, body, want):
    """A program with NO db connection -- the only way to make find genuinely break."""
    global _p, _f
    fd, path = tempfile.mkstemp(suffix=".mho")
    os.write(fd, body.encode())
    os.close(fd)
    r = subprocess.run([sys.executable, "mio.py", "run", path],
                       env=dict(env, DATABASE_URL=""), capture_output=True, text=True)
    os.unlink(path)
    ok = want in (r.stdout + r.stderr)
    _p += ok
    _f += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")


no_conn("find, a REAL error (no db connection) -> on.failure fires",
        'find a in db.rows\n'
        '    where title is "x"\n'
        '    on.failure show "BROKE"\n'
        'find: done\n',
        want="BROKE")

run("find, has results -> otherwise fires as the fallback",
    'find b in db.rows\n'
    '    where title is "present"\n'
    '    on.failure show "FAILED"\n'
    '    otherwise show "OTHERWISE"\n'
    'find: done\n',
    want="OTHERWISE", must_not="FAILED")

# TWO STAGES, both live. on.success answers "did it break?" (no). The conditional set then runs
# on the result. They are different questions, so they do not compete -- both fire.
run("on.success and the conditional set are two stages; BOTH fire",
    'find c in db.rows\n'
    '    where title is "present"\n'
    '    on.success show "SUCCESS"\n'
    '    otherwise show "OTHERWISE"\n'
    'find: done\n',
    want="SUCCESS")

run("...and the conditional set really did run alongside it",
    'find c2 in db.rows\n'
    '    where title is "present"\n'
    '    on.success show "SUCCESS"\n'
    '    otherwise show "OTHERWISE"\n'
    'find: done\n',
    want="OTHERWISE")

run("find, otherwise ALONE is the fallback when nothing else is declared",
    'find d in db.rows\n'
    '    where title is "absent"\n'
    '    otherwise show "OTHERWISE"\n'
    'find: done\n',
    want="OTHERWISE")

# ── never mandatory ───────────────────────────────────────────────────────
run("find with NO handlers at all is fine (never mandatory)",
    'find e in db.rows\n'
    '    where title is "present"\n'
    'find: done\n'
    'show "NO_HANDLERS_OK"\n',
    want="NO_HANDLERS_OK")

# ── check keeps working (it was the only block that ever had otherwise) ───
run("check block otherwise still fires",
    'hold n 1\n'
    'check n\n'
    '    when n is more than 3\n'
    '        show "WHEN"\n'
    '    otherwise\n'
    '        show "OTHERWISE"\n'
    'check: done\n',
    want="OTHERWISE", must_not="WHEN")


# ── `otherwise` scopes to its CONDITIONAL SET, not to the block ───────────
# A block may hold several conditional sets, and they nest. Each set gets its own otherwise.
run("multiple conditional sets, each with its own otherwise; plus a nested one",
    'hold n 2\n'
    'check n\n'
    '    when n is more than 5\n'
    '        show "SET1_WHEN"\n'
    '    otherwise\n'
    '        show "SET1_OTHERWISE"\n'
    'check: done\n'
    'check n\n'
    '    when n is more than 1\n'
    '        show "SET2_WHEN"\n'
    '        find a in db.rows\n'
    '            where title is "absent"\n'
    '            on.failure show "NESTED_FAILURE"\n'
    '            otherwise show "NESTED_OTHERWISE"\n'
    '        find: done\n'
    '    otherwise\n'
    '        show "SET2_OTHERWISE"\n'
    'check: done\n',
    want="NESTED_OTHERWISE", must_not="NESTED_FAILURE")


# ── once per set, and LAST ────────────────────────────────────────────────
# check_block got this free from the grammar. The other twenty blocks share result_handler*,
# which accepted two otherwise clauses, or one sitting ahead of on.failure -- silently, with one
# of them simply never running.
def loud(label, body):
    global _p, _f
    fd, path = tempfile.mkstemp(suffix=".mho")
    os.write(fd, (SETUP + body).encode())
    os.close(fd)
    r = subprocess.run([sys.executable, "mio.py", "check", path],
                       env=env, capture_output=True, text=True)
    os.unlink(path)
    ok = r.returncode != 0
    _p += ok
    _f += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")


loud("TWO otherwise in one find is LOUD",
     'find a in db.rows\n    otherwise show "one"\n    otherwise show "two"\nfind: done\n')
loud("otherwise BEFORE on.failure is LOUD (it must be last)",
     'find a in db.rows\n    otherwise show "o"\n    on.failure show "f"\nfind: done\n')
loud("TWO otherwise in a check is LOUD",
     'hold n 1\ncheck n\n    when n is more than 3\n        show "w"\n'
     '    otherwise\n        show "a"\n    otherwise\n        show "b"\ncheck: done\n')


# ── THE CONDITIONAL SET: `when` / `otherwise` in every verb block ─────────
# `when` used to exist in exactly ONE block: check. Everywhere else an empty result had no way
# to be handled except by abusing on.failure -- which made a dead database render as
# "All caught up!". Now: on.failure is a STATE (it broke, and it exits the block), and
# when/otherwise are CONDITIONS on the result, running post-result on the non-failure path.

run("find, empty result -> `when x is empty` fires",
    'find a in db.rows\n    where title is "absent"\n'
    '    when a is empty\n        show "EMPTY"\n'
    '    otherwise\n        show "HAS_ROWS"\n'
    'find: done\n',
    want="EMPTY", must_not="HAS_ROWS")

run("find, rows returned -> otherwise fires",
    'find b in db.rows\n    where title is "present"\n'
    '    when b is empty\n        show "EMPTY"\n'
    '    otherwise\n        show "HAS_ROWS"\n'
    'find: done\n',
    want="HAS_ROWS", must_not="EMPTY")

run("a `when` may test ANY variable, not just the result (a simpler if)",
    'hold mode "admin"\n'
    'find c in db.rows\n    where title is "present"\n'
    '    when mode is "guest"\n        show "GUEST"\n'
    '    when mode is "admin"\n        show "ADMIN"\n'
    '    otherwise\n        show "NEITHER"\n'
    'find: done\n',
    want="ADMIN", must_not="GUEST")

run("a `when` may test a property of the result",
    'find d in db.rows\n    where title is "present"\n'
    '    when d.count is more than 5\n        show "MANY"\n'
    '    otherwise\n        show "FEW"\n'
    'find: done\n',
    want="FEW", must_not="MANY")

run("when/otherwise work in save and retrieve too, not just find",
    'hold flag "go"\n'
    'save to db.rows\n    title "s"\n'
    '    when flag is "go"\n        show "SAVE_WHEN"\n'
    '    otherwise\n        show "SAVE_OTHERWISE"\n'
    'save: done\n',
    want="SAVE_WHEN", must_not="SAVE_OTHERWISE")

run("a nested block keeps its OWN conditional set",
    'find e in db.rows\n    where title is "present"\n'
    '    otherwise\n        show "OUTER"\n'
    '        find f in db.rows\n            where title is "absent"\n'
    '            when f is empty\n                show "NESTED_WHEN"\n'
    '            otherwise\n                show "NESTED_OTHERWISE"\n'
    '        find: done\n'
    'find: done\n',
    want="NESTED_WHEN", must_not="NESTED_OTHERWISE")


# on.failure is a GATE: it fires and EXITS the block, so the conditional set never runs on the
# failure path. THIS is why `when` needs no protective wrapper, and why the outer `otherwise`
# in the nested form is redundant.
no_conn("on.failure EXITS the block -- the when set does not run on the failure path",
        'find t in db.rows\n'
        '    on.failure show "BROKE"\n'
        '    when t is empty\n        show "WHEN_RAN"\n'
        '    otherwise\n        show "OTHERWISE_RAN"\n'
        'find: done\n'
        'show "AFTER_BLOCK"\n',
        want="AFTER_BLOCK")

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
