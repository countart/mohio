# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
test_check_time_silence.py

Two bugs the test-chat hunt found. Both were the same disease: `mio check` said "no errors" for
code that was wrong.

BUG 1 -- `if` as a block opener passed check when the variable was UNDECLARED.

    if x is more than 3          <- x never declared
        show "big"
    if: done

    `NAME` excluded `unless` but not `if`. Someone had patched exactly the one word that bit
    them. So with no valid IF_KW parse available, Earley read every word as a NAME -- and
    `NAME value` is a legal assignment. The line silently became three assignments:
    if=x, is=more, than=3. Exit 0. No errors.

    `if` is now reserved, symmetric with `unless` -- they are the SAME construct (trailing
    guards, DRIFT.md section 1) and the grammar had reserved only one of them. And
    `retired_if_block` exists so the form still PARSES, letting the transformer raise the
    directional "if is a trailing guard" message instead of a bare "Syntax error".

BUG 2 -- not-built services passed check, then blew up at run.

    They always failed loud at RUNTIME. But `mio check` returned exit 0 with no warning, so you
    saw green, deployed, and found out in production. Check-time silence is still silence.

    Every genuinely wired mio* service gets its OWN ast node (MioCookieSet, MiohttpStmt,
    MiomailStmt...). Only miocache and miolog run through the generic dotted catch-all. So a
    `mio*` reaching ServiceCallStmt is not wired, and now fails at CHECK.
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


def check(label, src, want_exit, needle=None):
    global _p, _f
    fd, path = tempfile.mkstemp(suffix=".mho")
    os.write(fd, src.encode())
    os.close(fd)
    r = subprocess.run([sys.executable, "mio.py", "check", path],
                       env=env, capture_output=True, text=True)
    os.unlink(path)
    ok = r.returncode == want_exit
    if ok and needle:
        ok = needle.lower() in (r.stdout + r.stderr).lower()
    _p += ok
    _f += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label} (exit {r.returncode}, want {want_exit})")


# ── BUG 1: `if` never opens a block -- declared or not ────────────────────
check("leading if, variable UNDECLARED (the bug)",
      'if x is more than 3\n    show "big"\nif: done\n', 1, "trailing guard")
check("leading if, variable declared",
      'x 5\nif x is more than 3\n    show "big"\nif: done\n', 1, "trailing guard")

# ...and the CANONICAL forms must stay clean. A trailing guard is correct Mohio.
check("trailing if (canonical)",  'hold x 1\nshow "big" if x is more than 3\n', 0)
check("trailing unless (canonical)", 'hold x 1\nshow "s" unless x is more than 3\n', 0)
check("check / when / otherwise (the way to branch)",
      'hold x 5\ncheck x\n    when x is more than 3\n        show "big"\n'
      '    otherwise\n        show "small"\ncheck: done\n', 0)
check("a name that merely CONTAINS a keyword", 'notify "hi"\nshow notify\n', 0)

# ── BUG 2: a service that fails at RUN must fail at CHECK ─────────────────
for svc in ('miosearch.index "d"',
            'miopush.send "n"',
            'mioimage.resize "p.jpg"',
            'miostream.send "d"',
            'miosecurity.scan',
            'miovault.get "s" as v',
            'miopublish.guaranteed "m"'):
    check(f"not built, fails at CHECK: {svc}", svc + "\n", 1)

# ...and the services that ARE wired must stay clean.
for svc in ('miocache.set "k" to "v"',
            'miolog.info "hi"',
            'miocookie.set "s" to "v"',
            'miocookie.exists "s"',
            'miocookie.delete "s"',
            'miohttp.get "https://example.com" as r'):
    check(f"wired, stays clean: {svc}", svc + "\n", 0)

# ── `otherwise` follows the ORIGINAL design spec ──────────────────────────
# Spec (2026-03-30): otherwise is the final fallback of a VERB BLOCK. Once per block, always
# last. It is not an inline operator.
#
# The compiler had grown an invented inline form, null_coalesce_stmt: `x foo otherwise bar`.
# No .mho file ever used it, and it outranked the check-block otherwise branch -- so Zork's
# session bootstrap parsed as a coalesce expression, the branch keyword was EATEN, and the
# assignment target was silently dropped. player_session was never set.
#
# Removing the rule was not enough: `otherwise` still lexed as a NAME, so
# `b a otherwise "fallback"` simply declared a VARIABLE CALLED otherwise. Same hole that let
# `if x is more than 3` become three declarations. otherwise and when are now reserved.
check("invented inline `x foo otherwise bar` is LOUD",
      'hold a "x"\nb a otherwise "fallback"\nshow b\n', 1)
check("spec: otherwise INLINE in a check block",
      'hold s 85\ncheck s\n    when s is more than 70\n        give back 200 "pass"\n'
      '    otherwise give back 200 "fail"\ncheck: done\n', 0)
check("spec: otherwise MULTI-LINE in a check block",
      'hold s 85\ncheck s\n    when s is more than 70\n        show "pass"\n'
      '    otherwise\n        show "fail"\ncheck: done\n', 0)
check("a dotted value assigned inside an otherwise branch (the Zork bug)",
      'hold n 1\ncheck n\n    when n is more than 3\n        v "A"\n'
      '    otherwise\n        v unique.id\ncheck: done\nshow v\n', 0)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
