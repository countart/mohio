# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
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
import os, sys, re

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
SCAN = ("mohio_interpreter.py", "mio.py", "mohio_ai.py")
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
    for ph in re.findall(r"`([^`]{2,40})`", l):                            # `X with` backticked in the source
        ph2 = re.sub(r"\{[^}]*\}", " ", ph).lower()                        # drop {placeholder} parts
        c.update(re.findall(r"[a-z_][\w.]{2,}", ph2))
    if "export as" in l.lower(): c.add("export")
    return {x for x in c if x and x.lower() not in STOP and len(x) >= 3}

def sites():
    out = []
    for fn in SCAN:
        lines = open(fn, encoding="utf-8").read().split("\n")
        for i, l in enumerate(lines):
            st = l.strip(); low = l.lower()
            if st.startswith("#") or st.startswith("//"): continue
            if not any(p in low for p in PHRASES): continue
            if any(g in low for g in GENERIC): continue
            out.append((f"{fn}:{i+1}", st[:80], candidates(fn, lines, i)))
    return out

# backticked identifiers/phrases in the backlog -> the set of words deliberately referenced.
backlog = open("CLAUDE-CODE-BACKLOG.md", encoding="utf-8").read()
_bt_phrases = re.findall(r"`([^`]+)`", backlog)
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
