# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
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


def dequote_paths(src: str, parser, parse=None) -> tuple[str, int]:
    """Rewrite a mistaken quoted path after `at` to the canonical unquoted form:
    `at "/about"` -> `at /about`. The grammar captures the quoted string in the `at`
    slot and the compiler rejects it; fmt fixes it here in a source pass that runs
    BEFORE the AST transform, so a file with quoted paths still formats instead of
    dying on the fail-loud. Returns (src, count_fixed).

    `parse` builds the tree (fmt passes its segmented parse); it must parse the text as written,
    since the rewrite splices by token position. A file with no `at` followed by a quote cannot
    hold a quoted path, so it is not parsed here at all: on a large app this parse was a third of
    fmt's time, spent on files with nothing to fix."""
    from lark import Token
    if not re.search(r'\bat[ \t]+"', src):
        return src, 0
    try:
        tree = (parse or parser.parse)(src)
    except Exception:
        return src, 0   # unparseable for other reasons -- leave to the normal path
    rules = {'listen_block', 'new_block',
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


_BARE_LISTEN_RE = re.compile(r'\s*listen\s+for\s*(//.*)?$')
_LISTEN_CLOSER_RE = re.compile(r'(listen\s*:\s*done|done)\b')


_NEW_HANDLER_RE = re.compile(r'^(\s*)new(\s+sh\.\S.*)$')
_NEW_CLOSER_RE = re.compile(r'^(\s*)new:\s*done\b(.*)$')


def new_to_listen(lines):
    """`new sh.X at /path` ... `new: done` becomes `listen for sh.X at /path` ... `listen: done`.

    RULED by Ron, 2026-10-07: `new` is not a handler. A handler for arriving data is `listen for
    sh.X`, which behaves exactly as `new sh.X` did (method, binding, the 422, the forms guard, field
    reads, the automatic transaction, audit, mio check). This is the mechanical swap: each opener's
    closer is the next `new: done` at the opener's own indentation, so nested blocks pair correctly.
    Returns (lines, changes) in the shape the other line-based passes use."""
    out = list(lines)
    changes = []
    open_indents = []
    for i, raw in enumerate(out):
        m = _NEW_HANDLER_RE.match(raw)
        if m:
            out[i] = f"{m.group(1)}listen for{m.group(2)}"
            open_indents.append(len(m.group(1)))
            changes.append((i + 1, raw.strip(), out[i].strip()))
            continue
        c = _NEW_CLOSER_RE.match(raw)
        if c and open_indents and len(c.group(1)) == open_indents[-1]:
            open_indents.pop()
            out[i] = f"{c.group(1)}listen: done{c.group(2)}"
            changes.append((i + 1, raw.strip(), out[i].strip()))
    return out, changes


_SHAPE_HANDLER_RE = re.compile(r'^(\s*)(?:listen for|request for)\s+sh\.([A-Za-z_]\w*)')
_TEMPLATE_RE = re.compile(r'\{\{.*?\}\}')


def declares_name(lines, name):
    """Does any line of the program declare `name` as a value of its own: an assignment, a hold or
    lock, a typed declaration, a query or call result, a loop item, a create, or a task input? Then
    `name.<field>` reads that value, check does not refuse it, and the binding rewrite leaves it."""
    n = re.escape(name)
    forms = (r'^\s*(?:hold\s+|lock\s+)?' + n + r'(?:\s|=|$)(?!\.)',
             r'\b(?:retrieve|find|grab|create)(?:\.\w+)?\s+' + n + r'\b(?!\.)',
             r'\bas\s+' + n + r'\s*$',
             r'\beach\s+' + n + r'\s+in\b',
             r'^\s*take\s+(?:[\w\s,]*\s)?' + n + r'\b(?!\.)')
    in_shape = False
    fields_at = None     # the indent of an open save/update/create/call block: its lines are fields
    for ln in lines:
        code = ln.split('//')[0] if '"' not in ln else ln
        # A shape's own field lines (`q as text` in `shape Q`) declare a field, not a value.
        if re.match(r'^\s*shape\s+\w', code) and not re.match(r'^\s*shape\s*:', code):
            in_shape = True
            continue
        if in_shape:
            if re.match(r'^\s*shape\s*:\s*done\b', code):
                in_shape = False
            continue
        indent = len(code) - len(code.lstrip())
        if fields_at is not None and code.strip() and indent <= fields_at:
            fields_at = None
        if re.match(r'^\s*(?:save|update|upsert|create|call)\b', code):
            fields_at = indent
        # A field line (`pay kept` in a save block) names a column, not a value of its own.
        check = forms[1:] if fields_at is not None and indent > fields_at else forms
        if any(re.search(f, code) for f in check):
            return True
    return False


def shape_binding_names(lines):
    """In a `listen for sh.X` / `request for sh.X` handler, the request data is read by the shape's
    name exactly as declared (RULED by Ron 2026-10-09). The retired name lowercased the first letter
    (`staffStatus` for sh.StaffStatus); this rewrites it to `StaffStatus` inside that handler's body,
    and a dotted read in another spelling of the same name (`staff_status.note`) the same way.

    It touches code and template insertions only: a quoted string keeps its text except inside
    `{{ }}`, a line in a render body is page text except inside `{{ }}`, and a comment is left as
    written. A handler that also names the old spelling as its own variable is left alone, so fmt
    never turns one name into two meanings; check reports that case."""
    out = list(lines)
    changes = []
    # WHAT IS IN SCOPE in a handler: its own body and the program outside every handler. A view loop
    # `{{ each vehicle in fleet }}` in another handler's page does not declare `vehicle` here, and
    # check refuses `vehicle.make` in sh.Vehicle's handler accordingly (measured on Dispatch: 20 lines
    # left unrewritten because a name was declared in some other handler).
    ranges = []
    for h in range(len(out)):
        mh = _SHAPE_HANDLER_RE.match(out[h])
        if mh:
            e = h + 1
            while e < len(out):
                code = out[e].split('//')[0]
                if code.strip() and len(code) - len(code.lstrip()) <= len(mh.group(1)):
                    break
                e += 1
            ranges.append((h, e))
    inside = set()
    for h, e in ranges:
        inside.update(range(h, e))
    outside = [out[x] for x in range(len(out)) if x not in inside]
    i = 0
    while i < len(out):
        m = _SHAPE_HANDLER_RE.match(out[i])
        if not m:
            i += 1
            continue
        indent, shape = len(m.group(1)), m.group(2)
        old = shape[0].lower() + shape[1:]
        j = i + 1
        while j < len(out):
            code = out[j].split('//')[0]
            if code.strip() and len(code) - len(code.lstrip()) <= indent:
                break
            j += 1
        # THE NAMES TO REWRITE: the retired lowercase-first name, and any other spelling of the shape
        # name (`staff_status` for sh.StaffStatus, RULED by Ron 2026-10-11), matched by the same
        # spelling key check's "Did you mean `StaffStatus.note`" uses. Inside this handler the shape
        # is the only request in scope, so a match names exactly one shape; a spelling the handler
        # or the program declares as its own value is left, and check keeps refusing it.
        from mohio_reachability import spelling_key
        in_scope = outside + out[i + 1:j]
        spellings = {r for r in re.findall(r'(?<![\w.])([A-Za-z_]\w*)(?![\w])', '\n'.join(out[i + 1:j]))
                     if r != shape and spelling_key(r) == spelling_key(shape)}
        names = sorted(r for r in spellings | {old} if r != shape and not declares_name(in_scope, r))
        rewrites = []
        for name in names:
            n = re.escape(name)
            rewrites.append((
                # A DOTTED READ (`back.token`), the form check refuses. A bare word in any position is
                # not: the retired name of sh.Back is `back`, and rewriting every bare `back` turned
                # `give back` into `give Back` (measured on cookbook/session-timeout.mho).
                re.compile(r'(?<![\w.])' + n + r'(?=\.[A-Za-z_])'),
                # ...A WHOLE-VALUE READ ONLY WHERE A VALUE STANDS: after a service op
                # (`miofile.read`), `from`, `with`, a give back status, `show`, `&`, `(`, `,` or
                # `{{`. A line-leading word (a save block's column) is never touched.
                re.compile(r'((?:\bmio\w*\.\w+|\bfrom|\bwith|\]|\bshow|&|\(|,|\{\{)\s*)' + n + r'(?![\w.])'),
                # ...AND A FIELD-VALUE LINE (`txn transaction` in a call or save block).
                re.compile(r'^(\s*[A-Za-z_]\w*\s+)' + n + r'(\s*)$')))

        def fix(text):
            for word, bare, pair in rewrites:
                text = pair.sub(lambda m: m.group(1) + shape + m.group(2),
                                bare.sub(lambda m: m.group(1) + shape, word.sub(shape, text)))
            return text
        if not rewrites:
            i = j
            continue
        in_render, render_indent = False, 0
        for k in range(i + 1, j):
            raw = out[k]
            stripped = raw.strip()
            if not in_render and _RENDER_OPEN_RE.match(raw):
                in_render, render_indent = True, len(raw) - len(raw.lstrip())
                continue
            if in_render and _RENDER_CLOSE_RE.match(raw):
                in_render = False
                continue
            if not stripped or stripped.startswith('//'):
                continue
            if in_render:
                new = _TEMPLATE_RE.sub(lambda t: fix(t.group(0)), raw)
            else:
                # Quoted pieces sit at odd positions; a `//` outside quotes starts a comment, and
                # a `//` inside one (a URL) is text.
                pieces = re.split(r'("(?:[^"\\]|\\.)*")', raw)
                commented = False
                for p in range(len(pieces)):
                    if commented:
                        continue
                    if p % 2:
                        pieces[p] = _TEMPLATE_RE.sub(lambda t: fix(t.group(0)), pieces[p])
                    else:
                        code, sep, rest = pieces[p].partition('//')
                        pieces[p] = fix(code) + sep + rest
                        commented = bool(sep)
                new = ''.join(pieces)
            if new != raw:
                out[k] = new
                changes.append((k + 1, raw.strip(), new.strip()))
        i = j
    return out, changes


_RENDER_OPEN_RE = re.compile(r'^[ \t]*render([ \t]+(as[ \t]+)?[A-Za-z_]\w*)?[ \t]*$')
_RENDER_CLOSE_RE = re.compile(r'^[ \t]*render[ \t]*:[ \t]*done(?![A-Za-z0-9_])[ \t]*$')
_EACH_MARK_RE = re.compile(
    r'\{\{[ \t]*each[ \t]+[A-Za-z_]\w*[ \t]+in[ \t]+[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*(?:[ \t]+take[ \t]+\d+)?[ \t]*\}\}'
    r'|\{\{[ \t]*each[ \t]*:[ \t]*done[ \t]*\}\}')


_DECIDE_OPEN_RE = re.compile(r'^\s*ai\.decide\s+\w')
_DECIDE_CLOSE_RE = re.compile(r'^\s*ai\.decide\s*:\s*done\b')
_GATE_RE = re.compile(r'^(\s*)(?:check\s+confidence\s+(?:is\s+)?above|confidence\s+is\s+above)\b')


def confidence_gate_form(lines):
    """Inside an ai.decide block the confidence gate is `confidence above N`, the only form (RULED by
    Ron 2026-10-10). `check confidence above N`, `check confidence is above N` and `confidence is
    above N` are rewritten to it. Other AI blocks are left alone: a gate there is refused by its own
    rule (123c021), which this does not change."""
    out = list(lines)
    changes = []
    inside = False
    for k, raw in enumerate(out):
        if _DECIDE_OPEN_RE.match(raw) and not _DECIDE_CLOSE_RE.match(raw):
            inside = True
            continue
        if inside and _DECIDE_CLOSE_RE.match(raw):
            inside = False
            continue
        if inside:
            new = _GATE_RE.sub(lambda m: m.group(1) + 'confidence above', raw)
            if new != raw:
                out[k] = new
                changes.append((k + 1, raw.strip(), new.strip()))
    return out, changes


_RENDER_OPEN_RE = re.compile(r'^\s*render\b(?!\s*:)')
_RENDER_CLOSE_RE = re.compile(r'^\s*render\s*:\s*done\b')
_SQL_OPEN_RE = re.compile(r'^\s*sql\s*$')
_SQL_CLOSE_RE = re.compile(r'^\s*sql\s*:\s*done\b')


def _plus_beside_text(line):
    """`line` with every `+` that sits directly beside a string literal turned into `&`. Text
    inside strings and after a `//` comment is left alone. Returns the line unchanged when there is
    nothing to turn."""
    out, k, n, in_str = [], 0, len(line), False
    while k < n:
        c = line[k]
        if in_str:
            out.append(c)
            if c == '\\' and k + 1 < n:
                out.append(line[k + 1])
                k += 2
                continue
            if c == '"':
                in_str = False
            k += 1
            continue
        if line.startswith('//', k):
            out.append(line[k:])
            break
        if c == '"':
            in_str = True
            out.append(c)
            k += 1
            continue
        if c == '+':
            before = ''.join(out).rstrip()
            after = line[k + 1:].lstrip()
            if before.endswith('"') or after.startswith('"'):
                out.append('&')
                k += 1
                continue
        out.append(c)
        k += 1
    return ''.join(out)


def plus_on_text(lines):
    """`+` with a string literal beside it joins text, which Mohio writes `&` (RULED by Ron); check
    refuses it, and this rewrites it. Only a `+` directly beside a string literal changes, which is
    text whatever the other side is. A render's page text and a raw `sql` block are not code here and
    are left alone."""
    out = list(lines)
    changes = []
    in_render = in_sql = False
    for k, raw in enumerate(out):
        if in_render:
            if _RENDER_CLOSE_RE.match(raw):
                in_render = False
            continue
        if in_sql:
            if _SQL_CLOSE_RE.match(raw):
                in_sql = False
            continue
        if _RENDER_OPEN_RE.match(raw):
            in_render = True
            continue
        if _SQL_OPEN_RE.match(raw):
            in_sql = True
            continue
        if '+' not in raw:
            continue
        new = _plus_beside_text(raw)
        if new != raw:
            out[k] = new
            changes.append((k + 1, raw.strip(), new.strip()))
    return out, changes


def inline_each_to_lines(lines):
    """An inline view loop becomes the line-anchored form (RULED by Ron 2026-10-09, under the Apr 3
    rule that fmt converts where it can). `<ul>{{ each r in rows }}<li>{{ r }}</li>{{ each: done }}</ul>`
    on one line never ran: the runtime reads a view loop only when its opener and closer stand alone
    on their lines, and `mio check` refuses the inline form. Inside a render, every line holding an
    `{{ each NAME in SOURCE }}` or `{{ each: done }}` beside other text is split so each marker has its
    own line and the markup between them sits one level deeper. A malformed marker (no `in`) is left
    for `mio check` to name. Returns (lines, changes) like the other line-based passes."""
    from mohio_interpreter import _RENDER_EACH_OPEN, _RENDER_EACH_CLOSE
    out, changes, in_render = [], [], False
    for i, raw in enumerate(lines):
        if not in_render:
            out.append(raw)
            in_render = bool(_RENDER_OPEN_RE.match(raw))
            continue
        if _RENDER_CLOSE_RE.match(raw):
            out.append(raw)
            in_render = False
            continue
        marks = list(_EACH_MARK_RE.finditer(raw))
        if not marks or _RENDER_EACH_OPEN.match(raw) or _RENDER_EACH_CLOSE.match(raw):
            out.append(raw)
            continue
        if len(re.findall(r'\{\{[ \t]*each\b', raw)) != len(marks):
            out.append(raw)        # a malformed marker on the line: left whole, for check to name
            continue
        base = raw[:len(raw) - len(raw.lstrip(' \t'))]
        depth, pos, pieces = 0, 0, []
        for m in marks:
            text = raw[pos:m.start()].strip()
            if text:
                pieces.append(base + '    ' * depth + text)
            closer = 'done' in m.group(0).split(':')[-1] and ':' in m.group(0)
            if closer:
                depth = max(0, depth - 1)
            pieces.append(base + '    ' * depth + m.group(0).strip())
            if not closer:
                depth += 1
            pos = m.end()
        tail = raw[pos:].strip()
        if tail:
            pieces.append(base + '    ' * depth + tail)
        out.extend(pieces)
        changes.append((i + 1, "inline view loop", f"split into {len(pieces)} lines (each opener and closer on its own)"))
    return out, changes


def _remove_listen_wrappers(lines):
    """Remove the retired bare `listen for` server wrapper (no subject) and dedent its handlers to
    the wrapper's own level. A bare wrapper is a `listen for` line with nothing after `for`; its
    closer is the first `listen: done` (or bare `done`) at the SAME indent. Handlers stand alone now,
    so the wrapper and its closer go and everything between them shifts left one level."""
    out = []
    changes = []
    i = 0
    n = len(lines)
    while i < n:
        raw = lines[i]
        if _BARE_LISTEN_RE.match(raw):
            wrap_indent = len(raw) - len(raw.lstrip())
            body = []
            closer_idx = None
            j = i + 1
            while j < n:
                rj = lines[j]
                sj = rj.strip()
                ij = len(rj) - len(rj.lstrip())
                if sj and ij == wrap_indent and _LISTEN_CLOSER_RE.match(sj):
                    closer_idx = j
                    break
                body.append(rj)
                j += 1
            if closer_idx is not None:
                body_indents = [len(b) - len(b.lstrip()) for b in body if b.strip()]
                step = (min(body_indents) - wrap_indent) if body_indents else 0
                step = max(0, step)
                for b in body:
                    if b.strip() and b[:step].strip() == "":
                        out.append(b[step:])
                    else:
                        out.append(b)
                changes.append((i + 1, raw.strip(), "removed retired `listen for` wrapper; handlers dedented"))
                i = closer_idx + 1
                continue
        out.append(raw)
        i += 1
    return out, changes


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

    # Retire the bare `listen for` server wrapper LAST, after the AST-line-number edits above, so
    # removing lines does not shift the line numbers those edits rely on.
    lines, wrap_changes = _remove_listen_wrappers(lines)
    changes.extend(wrap_changes)

    out = "\n".join(lines)
    out, trimmed = _whitespace_pass(out)
    if trimmed:
        changes.append((0, f"trailing whitespace / EOF", f"{trimmed} line(s) cleaned"))
    return out, changes
