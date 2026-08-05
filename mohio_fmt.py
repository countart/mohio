# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""mio fmt -- auto-format Mohio source toward canonical form.

v1 scope: strip the legacy `set` keyword from assignments. `=` is kept.

    set age = 15   ->  age = 15        (drop `set`, keep `=`)
    set age 15     ->  age 15          (drop `set`)
    set count 3    ->  count 3         (drop `set`)
    age = 15       ->  age = 15        (unchanged -- `=` is readable sugar, kept)
    hold age = 15  ->  hold age = 15   (unchanged)

Canonical assignment is `name value` (mutable) and `hold name value`
(immutable). `set` is retired legacy -- the parser accepts it as noise and
discards it. `=` is optional sugar; fmt keeps it for readability rather than
forcing the bare form (design decision, 2026-06-29). fmt removes only the
clearly-noise `set` keyword and leaves the rest of the line verbatim.

This is AST-driven on purpose. It parses the file, finds the exact source
lines that are confirmed `Assignment` or simple `HoldDecl` nodes, and rewrites
only those. A line that merely contains `=` -- a config block like the sector
profiles' `ctr_threshold = 10000`, or a comparison -- is never touched,
because it is not an assignment node. Blind regex over the whole file would
corrupt those; walking the AST is the safe path.
"""
from __future__ import annotations
import re


def _walk(node):
    """Yield every AST node in the tree, depth-first."""
    yield node
    d = getattr(node, "__dict__", None)
    if not d:
        return
    for v in d.values():
        if isinstance(v, list):
            for it in v:
                if hasattr(it, "__dict__"):
                    yield from _walk(it)
        elif hasattr(v, "__dict__") and not isinstance(v, (str, int, float, bool)):
            yield from _walk(v)


def _whitespace_pass(text: str) -> tuple[str, int]:
    """Safe, AST-independent cleanups: trim trailing whitespace on every line,
    and normalize the end of file to a single trailing newline. Neither is ever
    meaningful in Mohio, so this can run over any parseable file without risk.
    Returns (text, lines_trimmed).
    """
    lines = text.split("\n")
    trimmed = 0
    for i, ln in enumerate(lines):
        stripped = ln.rstrip()
        if stripped != ln:
            lines[i] = stripped
            trimmed += 1
    out = "\n".join(lines)
    # Collapse trailing blank lines to exactly one terminating newline.
    out = out.rstrip("\n") + "\n"
    return out, trimmed


def dequote_paths(src: str, parser) -> tuple[str, int]:
    """Rewrite a mistaken quoted path after `at` to the canonical unquoted form:
    `at "/about"` -> `at /about`. The grammar captures the quoted string in the `at`
    slot and the compiler rejects it; fmt fixes it here in a source pass that runs
    BEFORE the AST transform, so a file with quoted paths still formats instead of
    dying on the fail-loud. Returns (src, count_fixed)."""
    from lark import Token
    try:
        tree = parser.parse(src)
    except Exception:
        return src, 0   # unparseable for other reasons -- leave to the normal path
    rules = {'page_decl', 'listen_block', 'new_block',
             'request_inbound_block', 'connection_block'}
    spans = []  # (start_pos, end_pos, replacement)
    for st in tree.iter_subtrees():
        if getattr(st, 'data', None) in rules:
            toks = [c for c in st.children if isinstance(c, Token)]
            if any(t.type == 'AT' for t in toks):
                for t in toks:
                    if t.type == 'STRING' and getattr(t, 'start_pos', None) is not None:
                        raw = str(t)
                        inner = raw[1:-1] if len(raw) >= 2 else raw
                        spans.append((t.start_pos, t.end_pos, inner))
    if not spans:
        return src, 0
    # Apply right-to-left so earlier source positions stay valid as we splice.
    spans.sort(key=lambda s: s[0], reverse=True)
    out = src
    for start, end, repl in spans:
        out = out[:start] + repl + out[end:]
    return out, len(spans)


def format_source(src: str, ast) -> tuple[str, list[tuple[int, str, str]]]:
    """Return (formatted_source, changes).

    changes is a list of (line_number, old_stripped, new_stripped).
    Line 0 is used to report file-level whitespace cleanups.
    """
    lines = src.split("\n")
    # line_number(1-based) -> ('assign'|'hold', name)
    targets: dict[int, tuple[str, str]] = {}
    for n in _walk(ast):
        ln = getattr(n, "line", None)
        if not ln:
            continue
        kind = type(n).__name__
        if kind == "Assignment":
            targets[ln] = ("assign", n.name)
        # HoldDecl needs no normalization: `hold` is canonical and `=` is kept.

    changes: list[tuple[int, str, str]] = []
    for ln, (kind, name) in targets.items():
        idx = ln - 1
        if idx < 0 or idx >= len(lines):
            continue
        old = lines[idx]
        esc = re.escape(name)
        new = None
        m = None
        if kind == "assign":
            # Strip a leading `set` keyword (non-canonical noise); preserve `=`
            # and the rest of the line verbatim. `=` is readable sugar, kept per
            # design decision (2026-06-29). A plain `name = value` or `name value`
            # with no `set` is left untouched -- only the `set` word is removed.
            m = re.match(r"^(\s*)set\s+(" + esc + r"\b.*)$", old)
            if m:
                new = f"{m.group(1)}{m.group(2)}".rstrip()
        if m and new is not None and new != old:
            lines[idx] = new
            changes.append((ln, old.strip(), new.strip()))

    out = "\n".join(lines)
    out, trimmed = _whitespace_pass(out)
    if trimmed:
        changes.append((0, f"trailing whitespace / EOF", f"{trimmed} line(s) cleaned"))
    return out, changes
