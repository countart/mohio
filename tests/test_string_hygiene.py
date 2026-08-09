# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Unit C -- string hygiene: the silent/unhelpful quote and interpolation bugs.

Four cases, all verified by running:
  1. An unescaped double quote inside a string used to SILENTLY TRUNCATE
     (`show "she said "hi""` printed `she said ` and exited clean). Now fails loud.
  2. An unclosed {{ }} interpolation used to SILENTLY print literal braces
     (`show "hi {{ name "` printed `hi {{ name `). Now fails loud.
  3. Curly/smart quotes errored only as generic non-ASCII. Now named.
  4. Single-quoted strings errored as an empty "Unexpected end-of-input". Now named.

The check runs on the pre-parse source, so it must NOT fire inside raw-content blocks
(sql / show / render) where ', ", and {{ }} are legitimate non-Mohio content -- the
raw-block guards below are the adversarial proof that masking holds.

Run as a script: `python tests/test_string_hygiene.py` (exit 0 = pass).
"""
import os, subprocess, sys, tempfile

REPO = os.getcwd()
MIO = os.path.join(REPO, "mio.py")
ENV = dict(os.environ, PYTHONPATH=REPO, DATABASE_URL=":memory:",
           PYTHONIOENCODING="utf-8", PYTHONUTF8="1")

UNIT_C_MSGS = (
    "curly quotes are not string quotes",
    "Mohio strings use double quotes",
    "unescaped double quote inside a string",
    "unclosed interpolation",
)

_p = _f = 0

def _record(label, ok, detail=""):
    global _p, _f
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{(' -- ' + detail) if (detail and not ok) else ''}")
    _p += ok; _f += (not ok)

def _run(cmd, src):
    """Write src (exact bytes) to a temp .mho and run `mio <cmd>` on it."""
    fd, path = tempfile.mkstemp(suffix=".mho")
    os.write(fd, src.encode("utf-8")); os.close(fd)
    try:
        r = subprocess.run([sys.executable, MIO, cmd, path], cwd=REPO, env=ENV,
                           capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr
    finally:
        os.unlink(path)

def expect_fail(label, src, needle):
    code, out = _run("check", src)
    _record(label, code == 1 and needle in out, f"exit={code}\n{out[-300:]}")

def expect_run(label, src, needle, want_exit=0):
    code, out = _run("run", src)
    _record(label, code == want_exit and needle in out, f"exit={code}\n{out[-300:]}")

def expect_no_unitc(label, src):
    """The valuable guard: this source must never trip a Unit C message (false positive)."""
    _, out = _run("check", src)
    tripped = [m for m in UNIT_C_MSGS if m in out]
    _record(label, not tripped, f"tripped {tripped}\n{out[-300:]}")

# ---- the four bugs: now fail loud -------------------------------------------

expect_fail("case 1: unescaped inner double quote fails loud (check)",
            'show "she said "hi""\n', "unescaped double quote inside a string")
expect_fail("case 2: unclosed {{ interpolation fails loud (check)",
            'show "hi {{ name "\n', "unclosed interpolation")
expect_fail("case 3: curly quotes named",
            'show \u201cHello\u201d\n', "curly quotes are not string quotes")
expect_fail("case 4: single quotes named",
            "show 'Hello'\n", "Mohio strings use double quotes")

# cases 1 and 2 are the valuable ones -- they must fail at RUN too (no wrong output)
c1_code, c1_out = _run("run", 'show "she said "hi""\n')
_record("case 1: fails at run, exits 1", c1_code == 1 and "unescaped double quote" in c1_out,
        f"exit={c1_code}")
c2_code, c2_out = _run("run", 'show "hi {{ name "\n')
_record("case 2: fails at run, exits 1", c2_code == 1 and "unclosed interpolation" in c2_out,
        f"exit={c2_code}")

# ---- baselines that MUST stay working ---------------------------------------

expect_run("b1: escaped inner quote prints the quote", 'show "she said \\"hi\\""\n', 'she said "hi"')
expect_run("b2: apostrophe inside a string", 'show "it\'s fine"\n', "it's fine")
expect_run("b3: single quotes inside a string", 'show "the \'best\' one"\n', "the 'best' one")
expect_run("b4: closed interpolation resolves", 'name "Bo"\nshow "hi {{ name }}"\n', "hi Bo")
expect_run("b5: undefined interpolation -> unknown_variable (unchanged)",
           'show "{{ undefined }}"\n', "unknown_variable")
expect_run("b7: empty string still empty, no error", 'show ""\n', "", want_exit=0)
expect_run("log-only: unknown escape passes through literally, no error",
           'show "\\qescape"\n', "\\qescape")

# b6: an unterminated string is STILL a plain syntax error, not claimed as a Unit C bug
b6_code, b6_out = _run("check", 'show "hello\n')
_record("b6: unterminated string stays a syntax error (not a Unit C message)",
        b6_code == 1 and "Unexpected end-of-input" in b6_out
        and not any(m in b6_out for m in UNIT_C_MSGS),
        f"exit={b6_code}\n{b6_out[-300:]}")

# ---- adversarial raw-block guards: NO false positives -----------------------
# render (HTML) block: apostrophe in text + a quote-adjacent attribute would both trip
# case 4 and case 1 if the raw interior were scanned as Mohio.
expect_no_unitc("render block: apostrophe + adjacent-quote HTML is not scanned",
    'listen for\n'
    '    new sh.X at /x\n'
    '        render\n'
    '            <p>We\'ll be in touch. <input value="a"required></p>\n'
    '        render: done\n'
    '    new: done\n'
    'listen: done\n'
    'shape X\n'
    '    a as text\n'
    'shape: done\n')

# sql block: a single-quoted SQL literal + a quote-adjacent token must not trip.
expect_no_unitc("sql block: single-quoted SQL literal is not scanned",
    'connect db as sqlite from env.X\n'
    'find rows in db.t\n'
    '    sql\n'
    "        SELECT * FROM t WHERE name = 'abc' AND note = \"q\"extra\n"
    '    sql: done\n'
    'find: done\n')

# bare `show` raw block: GAP-3 regression (mutation, 2026-07-31). `_mask_noncode` masks sql, show,
# AND render interiors, but only sql and render had a false-positive guard here; dropping `show`
# from the mask set passed this whole suite. A bare `show` block's raw interior legitimately holds
# apostrophes, quote-adjacent tokens, and an open {{ -- none may be scanned as Mohio.
expect_no_unitc("show block: apostrophe + adjacent-quote + open interpolation is not scanned",
    'show\n'
    '    We\'ll be in touch. <input value="a"required> and {{ x\n'
    'show: done\n')

# a Unit C bug OUTSIDE any raw block, in a file that also has a clean render block,
# must still be caught (masking must not blind the real code around a block).
rb_code, rb_out = _run("check",
    'show "she said "hi""\n'
    'render\n'
    '    <p>fine</p>\n'
    'render: done\n')
_record("real bug next to a render block is still caught",
        rb_code == 1 and "unescaped double quote" in rb_out, f"exit={rb_code}")

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
