# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Every unbuilt fail-loud MUST have a backlog entry (family-enumeration gate, 2026-07-31).

A fail-loud for a not-yet-built feature is a DEFERRAL, not a resolution (CLAUDE.md standing rule).
This gate DERIVES the set of unbuilt-feature fail-louds from the code -- every non-comment line in
the interpreter / CLI carrying an "unbuilt" phrasing -- and fails the build if the feature it names
is not tracked in CLAUDE-CODE-BACKLOG.md. A feature is "tracked" when an identifier that names it
appears BACKTICKED in the backlog (a deliberate `feature` reference, so common words like `stream`
or `load` are enforced reliably, not matched incidentally in prose).

So a new fail-loud cannot be added silently: adding one without a backlog entry breaks this test and
names the site. Retiring the name (removing the fail-loud) also makes it pass; so does building it.

Run as a script: `python tests/test_unbuilt_failloud_backlog.py` (exit 0 = pass).
"""
import glob, os, sys, re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); os.chdir(ROOT)

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)

PHRASES = ("not yet executable", "not yet built", "would silently do nothing",
           "is declared but not", "not wired", "not available yet", "no generate_audio runtime")
# generic dispatch mechanisms that format the feature name at runtime -- not a static feature
GENERIC = ("has no handler in this build", "if this should work", "{service}", "{m}.", "{method}",
           "{name} is declared", "and validated, but would silently")
# EVERY compiler file, derived -- not a hand-written list (T1-BACKLOG-GATE-SCANS-TRANSFORMER,
# 2026-09-01). This scanned three files: mohio_interpreter.py, mio.py, mohio_ai.py. Compile-time
# refusal is the standing rule wherever the compiler can see the construct, so new deferrals land
# in the TRANSFORMER by design -- exactly where the gate could not look. `T1-HASH-MULTI-FIELD`
# proved it: a compile-time deferral whose backlog entry had to be written by hand, because
# nothing would have failed if it had been skipped.
#
# DERIVED, not enumerated, for the same reason the rule exists at all: a hand-written list goes
# blind the moment someone adds a compiler file, and no one would notice. Widening it surfaced
# 46 sites where 35 were seen before, of which 6 were untracked -- 3 real deferrals (`framework`,
# `expect`, the `cm.*` compliance actions) and 3 false positives that forced the docstring fix
# below.
SCAN = tuple(sorted(set(glob.glob("mohio_*.py")) - {"mohio_test_grammar.py"} | {"mio.py"}))


def _docstring_lines(lines):
    """Line numbers that sit inside a triple-quoted block of PROSE.

    The scan skipped `#` comments but not DOCSTRINGS, so prose merely DESCRIBING an unbuilt
    feature registered as a fail-loud site. All the false positives from widening the scan were
    this: `mohio_server.py`'s "Raises loudly when the framework is declared but not built" (a
    docstring explaining the raise, not the raise), `mohio_layer3.py`'s "NOT wired into the live
    preprocess pipeline" STATUS note, and `mohio_transformer_ast.py`'s note about a separate
    'not wired' concern.

    Fixed WITH the widening, not after. A gate that reports things which are not findings gets
    ignored, and an ignored gate is the decay it exists to prevent.

    A triple-quoted region that is part of a raise/warn/error call is NOT prose -- a message can
    legitimately be written that way, and skipping it would LOSE a real site. Being wrong in the
    skip direction costs a finding, which is worse for a safety gate than a little noise, so the
    call tokens win. Caught by mutation: the first version skipped a one-line docstring by not
    marking it at all, which meant a one-line `raise X('''...''')` and a one-line docstring were
    treated identically -- both unmarked, so the prose case still reported.
    """
    CALLS = ("raise ", ".warn(", ".error(", "return (")
    inside = set()
    open_tok = None
    for i, line in enumerate(lines):
        if open_tok is not None:
            inside.add(i)
            if open_tok in line:
                open_tok = None
            continue
        hits = [(line.find(t), t) for t in ('"""', "'''") if line.find(t) != -1]
        if not hits:
            continue
        pos, tok = min(hits)
        is_prose = not any(c in line for c in CALLS)
        closed_same_line = tok in line[pos + 3:]
        if is_prose:
            inside.add(i)
            if not closed_same_line:
                open_tok = tok
        elif not closed_same_line:
            # A multi-line message inside a call: its continuation lines are message text,
            # which is exactly what the scan wants to read, so they are deliberately not
            # marked. The opening line is not marked either.
            pass
    return inside
STOP = {"self", "node", "ctx", "raise", "def", "return", "mohioruntimeerror", "message", "this",
        "build", "not", "yet", "the", "and", "but", "would", "silently", "nothing",
        "declared", "executable", "wired", "hint", "use", "via", "does", "two", "word"}

def candidates(fn, lines, i):
    """Identifiers that could name the feature at this fail-loud site."""
    l = lines[i]
    ctx = "\n".join(lines[max(0, i - 3):i + 1])
    c = set()
    c.update(re.findall(r"mio[a-z]+\b|ai\.[a-z_]+|generate_[a-z]+", ctx))   # distinctive tokens
    m = re.search(r"'([a-z_]+)':\s*[\(\"]", l)                              # _service_hints dict key
    if m: c.add(m.group(1))
    m = re.search(r"def _exec_([A-Za-z]+?)(?:Stmt|Decl|Block)\b", ctx)      # construct name
    if m: c.add(m.group(1).lower())
    m = re.search(r'MohioRuntimeError\(\s*[f]?"([A-Za-z_][\w.]*)', l)       # message-leading word
    if m: c.add(m.group(1).lower())
    m = re.search(r'"([a-z_][\w.]*)\s+is\s+(?:declared|not)', l)            # "<feature> is declared"
    if m: c.add(m.group(1).lower())
    for ph in re.findall(r"'([a-z][\w. ]{2,40})'", l):                      # 'change to sh.X', 'cursor pagination'
        c.update(re.findall(r"[a-z_][\w.]{2,}", ph.lower()))
    # project backlog tag convention (T1-QUERY-HELD, T1-CHECK-UNIQUE-REDESIGN, ...). BT_WORDS
    # below splits a backticked `T1-QUERY-HELD` on the hyphen (its regex uses \w, which excludes
    # "-") into separate words {"t1","query","held"} -- mirror that same split here so the two
    # sides compare on identical tokens instead of one whole tag vs three fragments.
    for tag in re.findall(r"T1-[A-Z0-9]+(?:-[A-Z0-9]+)*", ctx):
        c.update(w.lower() for w in re.findall(r"[A-Za-z0-9]+", tag))
    # `ctx` (this line + the 3 lines above), not just `l` -- a wrapped f-string message often
    # carries its backtick-quoted identifier on an ADJACENT physical line from the "NOT YET
    # BUILT" phrase that triggered the site match (T1-QUERY-HELD's own sites are exactly this
    # shape: the phrase is on one wrapped line, `save`/`update`/etc. backticked on another).
    for ph in re.findall(r"`([^`]{2,40})`", ctx):                          # `X with` backticked in the source
        ph2 = re.sub(r"\{[^}]*\}", " ", ph).lower()                        # drop {placeholder} parts
        c.update(re.findall(r"[a-z_][\w.]{2,}", ph2))
    if "export as" in l.lower(): c.add("export")
    return {x for x in c if x and x.lower() not in STOP and len(x) >= 3}

def sites():
    out = []
    for fn in SCAN:
        lines = open(fn, encoding="utf-8").read().split("\n")
        doc = _docstring_lines(lines)
        for i, l in enumerate(lines):
            st = l.strip(); low = l.lower()
            if st.startswith("#") or st.startswith("//"): continue
            if i in doc: continue          # prose ABOUT a fail-loud is not one
            if not any(p in low for p in PHRASES): continue
            if any(g in low for g in GENERIC): continue
            out.append((f"{fn}:{i+1}", st[:80], candidates(fn, lines, i)))
    return out

# backticked identifiers/phrases in the backlog -> the set of words deliberately referenced.
# Triple-backtick fenced code blocks must be stripped FIRST: the inline-span regex below
# pairs backticks strictly left-to-right with no fence awareness, so a ``` fence (an odd
# number of backtick characters on its own) desyncs every single-backtick pairing for the
# rest of the file, silently corrupting real inline `word` references into one giant garbage
# span. Found 2026-08-06: this made `miosearch` (a real, backtick-quoted, covered candidate)
# register as untracked purely because of where a fenced example happened to fall relative to
# it -- a false failure with nothing wrong in the backlog content itself.
backlog = open("CLAUDE-CODE-BACKLOG.md", encoding="utf-8").read()
backlog_no_fences = re.sub(r"```.*?```", " ", backlog, flags=re.DOTALL)
_bt_phrases = re.findall(r"`([^`]+)`", backlog_no_fences)
BT_WORDS = set()
for ph in _bt_phrases:
    for w in re.findall(r"[A-Za-z_][\w.]*", ph):
        BT_WORDS.add(w.lower())

all_sites = sites()
check("derived a non-empty unbuilt-fail-loud set from the code", len(all_sites) >= 5, str(len(all_sites)))
print(f"    {len(all_sites)} unbuilt fail-loud sites scanned")

untracked = []
for loc, msg, cands in all_sites:
    if not cands:
        untracked.append(f"{loc}: {msg}  (no identifier extracted -- refine the gate or name it)")
        continue
    if not (cands & BT_WORDS):
        untracked.append(f"{loc}: {msg}  candidates={sorted(cands)}")

check("every unbuilt fail-loud is tracked in the backlog (a new one cannot be added silently)",
      not untracked,
      "these unbuilt fail-louds have NO backticked backlog entry -- add one (or retire the fail-loud):\n          "
      + "\n          ".join(untracked))

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
