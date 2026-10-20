# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Parse a program in segments cut only between two closed blocks, and cache each segment.

WHY. The Earley parse is the whole cost of a cold `mio check` on a large app (Dispatch, 5,447
lines: about 330 seconds, 98% of the check), and its rate is flat per line (EARLEY-PARSER-
MEASUREMENT.md), so splitting alone saves nothing. What a split buys is two things: segments that
did not change are not parsed again after an edit, and segments that did can be parsed on every
core at once.

WHERE A CUT IS ALLOWED, and why nowhere else. Only between two CLOSED BLOCKS: the segment before
ends with a column-0 named closer that closes its own column-0 opener, and the segment after
starts with a column-0 opener whose own named closer is the next column-0 line. A bare top-level
statement is never cut from its neighbours: parsed alone, `show name as.title` grouped differently
than in its file (measured 2026-10-11, the reason the statement-by-statement version was reverted),
so a bare statement always stays in the segment of the block next to it. Which block kinds may sit
on either side of a cut is SEGMENT_KINDS, and every kind in it is one tools/segment_diff.py found
zero tree differences for across every program we have.

THE TREE IS THE SAME. Each segment's tree is shifted to its place in the file and the segments
become the children of one `start` tree, exactly what the whole-file parse builds, every node and
every position (tools/segment_diff.py compares them).

FAILING IS NEVER DIFFERENT. If a segment does not parse alone, or leaves a closer standing on its
own at its top level, `parse_segmented` gives up and the caller parses the whole file as it always
has, so a syntax error is reported exactly as before. A cut in the wrong place can only cost speed.
"""
import hashlib
import os
import pickle
import re

# Every segment's tree is written through the tree pickler, which keeps every token position: plain
# pickle drops a token's end line, end column and end position, so a cached segment was not the tree
# a fresh parse built (found 2026-10-12, mohio_treepickle's docstring).
import mohio_treepickle as treepickle

from mohio_egress import gated_print as print  # noqa: A001,E402 -- every print through the egress gate

# The block kinds a cut may sit between, each proven by tools/segment_diff.py: every kind the harness
# cut, beside every other, across 731 programs (365 .mho files, Dispatch, Zork, the health and
# banking apps, and every mohio fence in a tracked doc), with zero tree differences (2026-10-11). A
# kind the corpus never cut is not here, so it is never cut: it keeps the whole-file parse.
SEGMENT_KINDS = frozenset({
    "ai.agent", "ai.connect", "ai.create", "ai.decide", "call", "check", "cm.purge", "create",
    "find", "it", "journey", "listen", "map", "meq", "mioconnect", "mioschedule", "modify", "muv",
    "new", "on.idle", "pattern", "remove", "remove.all", "render", "repeat", "replace", "request",
    "retrieve", "retrieve.one", "saga", "save", "shape", "sign", "split", "sql", "task", "timespan",
    "transaction", "update", "upsert", "verify",
})

# For tools/segment_diff.py only: cut between ANY two closed blocks, to find which kinds are safe.
ANY_CLOSED = "any closed block"

# A column-0 line that closes a block, and the kind it closes (`listen: done` -> listen).
_CLOSER = re.compile(r"^([A-Za-z_][\w.]*)[ \t]*:[ \t]*done[ \t]*(?://.*)?$")
_ANY_CLOSER = re.compile(r"^[A-Za-z_][\w.]*\s*:\s*done\b|^done\b")
# The kind a column-0 opener opens: its first word, or `ai.decide` and the like as written.
_OPENER = re.compile(r"^([A-Za-z_][\w.]*)")

# Uncached text below this is parsed in this process: starting workers costs more than it saves.
_PARALLEL_MIN_CHARS = 40000
_PARALLEL_MIN_SEGMENTS = 4

_SEGMENT_CACHE_SUFFIX = ".segments.mho.cache"   # *.mho.cache: ignored by git, purged by a deploy build, never served


def _line_end_state(line, state):
    """What a line leaves open for the next one: "code", "string" (a text literal that runs on, as a
    multi-line `give back "<h1>...` does) or "comment" (a /* comment not yet closed). A line comment
    `//` ends at the end of its line; a backslash in a string escapes the next character."""
    k, n = 0, len(line)
    while k < n:
        c = line[k]
        if state == "string":
            if c == "\\":
                k += 2
                continue
            if c == '"':
                state = "code"
        elif state == "comment":
            if line.startswith("*/", k):
                state, k = "code", k + 2
                continue
        else:
            if line.startswith("//", k):
                break
            if line.startswith("/*", k):
                state, k = "comment", k + 2
                continue
            if c == '"':
                state = "string"
        k += 1
    return state


def top_level_chunks(text):
    """[(first_line, last_code_line, opener_line)] for each top-level statement of `text`, by line
    index. A chunk starts at a column-0 code line that is not a closer (and not inside a /* */
    comment or a text literal running on from the line above); its last code line is its last line
    that is not blank or a comment. Blank and comment lines before a chunk belong to the chunk
    before it, or to the first chunk."""
    lines = text.split("\n")
    starts, last_code = [], []
    state = "code"
    for i, line in enumerate(lines):
        if state == "code":
            stripped = line.strip()
            is_code = bool(stripped) and not stripped.startswith("//") and not stripped.startswith("/*")
            if is_code and not line[0].isspace() and not _ANY_CLOSER.match(line):
                starts.append(i)
                last_code.append(i)
            elif is_code and starts:
                last_code[-1] = i
        state = _line_end_state(line, state)
    return [(s, lc) for s, lc in zip(starts, last_code)], lines


def _closed_kind(lines, first, last):
    """The kind of the closed block spanning lines[first..last], or None. Closed means: the opener
    is at column 0, its own named closer is the chunk's last code line at column 0, and no other
    line in between sits at column 0, so the block is delimited by its closer AND its indentation."""
    m = _OPENER.match(lines[first])
    c = _CLOSER.match(lines[last]) if last > first else None
    if not m or not c:
        return None
    kind = c.group(1)
    head = m.group(1)
    if head != kind and not (head.startswith(kind + ".") or kind.startswith(head + ".")):
        return None
    for line in lines[first + 1:last]:
        if line and not line[0].isspace() and line.strip() and not line.strip().startswith("//"):
            return None
    return kind


def closed_kinds(text):
    """The kind of each top-level statement of `text`, None where it is not a closed block."""
    chunks, lines = top_level_chunks(text)
    return [_closed_kind(lines, a, b) for a, b in chunks]


def segments(text, kinds=None):
    """[(line_offset, char_offset, segment_text)] covering `text` exactly, in order, cut only
    between two closed blocks whose kinds are both in `kinds` (default SEGMENT_KINDS). The segment
    texts concatenated are `text`, character for character."""
    kinds = SEGMENT_KINDS if kinds is None else kinds
    chunks, lines = top_level_chunks(text)
    if not chunks or not kinds:
        return [(0, 0, text)]
    closed = [_closed_kind(lines, a, b) for a, b in chunks]
    cut_at = [0]
    for i in range(1, len(chunks)):
        if kinds == ANY_CLOSED:
            allowed = bool(closed[i - 1]) and bool(closed[i])
        else:
            allowed = closed[i - 1] in kinds and closed[i] in kinds
        if allowed:
            # The cut sits right after the closer of the block before: the comment and blank lines
            # between belong to the segment that follows, as they do to the chunk below them.
            cut_at.append(chunks[i - 1][1] + 1)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line) + 1)
    bounds = cut_at + [len(lines)]
    out = []
    for a, b in zip(bounds, bounds[1:]):
        start, end = offsets[a], min(offsets[b], len(text))
        out.append((a, start, text[start:end]))
    return out


def _shift(tree, dl, dc):
    """Move every line and character position in `tree` by (dl, dc). Columns stay: every segment
    starts at the beginning of a line."""
    from lark import Tree, Token
    stack = [tree]
    while stack:
        n = stack.pop()
        if isinstance(n, Token):
            for a in ("line", "end_line"):
                v = getattr(n, a, None)
                if v is not None:
                    setattr(n, a, v + dl)
            for a in ("start_pos", "end_pos"):
                v = getattr(n, a, None)
                if v is not None:
                    setattr(n, a, v + dc)
        elif isinstance(n, Tree):
            m = getattr(n, "_meta", None)
            if m is not None and not getattr(m, "empty", True):
                for a in ("line", "end_line", "container_line", "container_end_line"):
                    v = getattr(m, a, None)
                    if v is not None:
                        setattr(m, a, v + dl)
                for a in ("start_pos", "end_pos", "container_start_pos", "container_end_pos"):
                    v = getattr(m, a, None)
                    if v is not None:
                        setattr(m, a, v + dc)
            stack.extend(n.children)
    return tree


def _merge(roots):
    """One `start` tree whose children are every segment's statements, its span from the first
    segment's start to the last segment's end, as the whole-file parse sets it."""
    from lark import Tree
    children = []
    for r in roots:
        children.extend(r.children)
    merged = Tree(roots[0].data, children)
    first, last = roots[0].meta, roots[-1].meta
    m = merged.meta
    for a in ("line", "column", "start_pos", "container_line", "container_column",
              "container_start_pos"):
        if hasattr(first, a):
            setattr(m, a, getattr(first, a))
    for a in ("end_line", "end_column", "end_pos", "container_end_line", "container_end_column",
              "container_end_pos"):
        if hasattr(last, a):
            setattr(m, a, getattr(last, a))
    m.empty = getattr(first, "empty", False) and getattr(last, "empty", False)
    return merged


def _stands_alone(tree):
    """False when a segment's tree has a closer standing on its own among its top-level statements:
    the cut fell inside a block, and the whole-file parse must decide what that file means."""
    from lark import Tree
    for ch in getattr(tree, "children", []):
        node = ch
        while isinstance(node, Tree) and len(node.children) == 1 and isinstance(node.children[0], Tree) \
                and node.data in ("statement", "_statement", "stmt"):
            node = node.children[0]
        if isinstance(node, Tree) and ("closer" in str(node.data)):
            return False
    return True


# -- the per-segment cache -------------------------------------------------------------------------

def _segment_key(segment_text):
    return hashlib.sha256(segment_text.encode("utf-8")).hexdigest()[:24]


def _cache_path(source_path):
    return os.path.splitext(source_path)[0] + _SEGMENT_CACHE_SUFFIX


def _load_cache(source_path, fingerprint):
    path = _cache_path(source_path)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "rb") as fh:
            cached = pickle.load(fh)
    except Exception:  # SILENT-OK: a corrupt or unreadable cache is a miss; every segment re-parses
        return {}
    if not isinstance(cached, dict) or cached.get("parse") != fingerprint:
        return {}
    return cached.get("segments") or {}


def _save_cache(source_path, fingerprint, kept):
    path = _cache_path(source_path)
    try:
        # EGRESS-EXEMPT: the compiler's own parse cache; no program value
        with open(path, "wb") as fh:
            pickle.dump({"parse": fingerprint, "segments": kept}, fh,
                        protocol=pickle.HIGHEST_PROTOCOL)
    except Exception as e:
        # Not fatal (the next check re-parses), and never silent: say why it did not save.
        print(f"  [cache] could not save the segment parse cache {path}: {e}")


# -- parallel workers --------------------------------------------------------------------------------
# Plain subprocesses running this file, never a multiprocessing pool: a pool on Windows re-runs the
# CALLER's main script in every worker, so a program that called the compiler without a __main__
# guard would start workers that start workers. Each worker reads its segments from a file and
# writes their trees to another, so nothing it or `mio` prints can mix into the result.

def _serve_worker(in_path, out_path):
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import mio
    parser = mio._make_parser_cached(mio._load_grammar())
    with open(in_path, "rb") as fh:
        texts = pickle.load(fh)
    out = []
    for t in texts:
        try:
            out.append(treepickle.dumps(parser.parse(t)))
        except Exception:  # SILENT-OK: a segment that does not parse alone sends the caller to the whole-file parse
            out.append(None)
    # EGRESS-EXEMPT: parse trees of the compiler's own segments, handed back to the parent process
    with open(out_path, "wb") as fh:
        pickle.dump(out, fh, protocol=pickle.HIGHEST_PROTOCOL)


def _workers(n_segments):
    raw = os.environ.get("MOHIO_PARSE_WORKERS", "").strip()
    if raw:
        if not raw.isdigit() or int(raw) < 1:
            raise ValueError(
                f"MOHIO_PARSE_WORKERS must be a whole number, 1 or more; it is {raw!r}. It sets how many "
                f"processes parse a large file at once. Write a number, for example "
                f"MOHIO_PARSE_WORKERS=4, or unset it to use this machine's cores.")
        return min(int(raw), n_segments)
    return max(1, min(n_segments, (os.cpu_count() or 2) - 1, 12))


def _parse_in_workers(texts, n):
    """Parse `texts` across `n` worker processes; a list of pickled trees, None where one failed."""
    import shutil
    import subprocess
    import sys
    import tempfile
    order = sorted(range(len(texts)), key=lambda i: -len(texts[i]))   # largest first, spread evenly
    buckets, loads = [[] for _ in range(n)], [0] * n
    for i in order:
        k = loads.index(min(loads))
        buckets[k].append(i)
        loads[k] += len(texts[i])
    tmp = tempfile.mkdtemp(prefix="mohio_parse_")
    procs = []
    try:
        for k, idx in enumerate(buckets):
            if not idx:
                continue
            ip, op = os.path.join(tmp, f"in{k}.pkl"), os.path.join(tmp, f"out{k}.pkl")
            # EGRESS-EXEMPT: the source segments a worker parses, in a private temporary folder
            with open(ip, "wb") as fh:
                pickle.dump([texts[i] for i in idx], fh, protocol=pickle.HIGHEST_PROTOCOL)
            procs.append((idx, op, subprocess.Popen(
                [sys.executable, os.path.abspath(__file__), "--worker", ip, op],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)))
        results = [None] * len(texts)
        for idx, op, proc in procs:
            proc.wait()
            if proc.returncode == 0 and os.path.exists(op):
                with open(op, "rb") as fh:
                    for i, data in zip(idx, pickle.load(fh)):
                        results[i] = data
        return results
    finally:
        for _idx, _op, proc in procs:
            if proc.poll() is None:
                proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)


def parse_segmented(parser, text, source_path=None, fingerprint=None, kinds=None):
    """The parse tree of `text` (pretokenized source), built segment by segment, with a
    per-segment cache when `source_path` is given, or None. None means: parse the whole file, which
    reports any error exactly as it always has. A file with one segment returns None too, so it
    takes the whole-file path it always took."""
    _workers(1)                      # a bad MOHIO_PARSE_WORKERS refuses on every parse, not only a big one
    segs = segments(text, kinds)
    if len(segs) < 2:
        return None
    cache = _load_cache(source_path, fingerprint) if source_path else {}
    keys = [_segment_key(s[2]) for s in segs]
    misses = [i for i, k in enumerate(keys) if k not in cache]
    fresh = {}
    if misses:
        miss_chars = sum(len(segs[i][2]) for i in misses)
        if len(misses) >= _PARALLEL_MIN_SEGMENTS and miss_chars >= _PARALLEL_MIN_CHARS \
                and _workers(len(misses)) > 1:
            results = _parse_in_workers([segs[i][2] for i in misses], _workers(len(misses)))
            for i, data in zip(misses, results):
                if data is not None:
                    fresh[keys[i]] = data
        else:
            for i in misses:
                try:
                    fresh[keys[i]] = treepickle.dumps(parser.parse(segs[i][2]))
                except Exception:  # SILENT-OK: this segment does not parse alone; the whole-file parse runs
                    pass
    kept = {k: cache[k] for k in keys if k in cache}
    kept.update(fresh)
    if source_path and (fresh or len(kept) != len(cache)):
        _save_cache(source_path, fingerprint, kept)
    if any(k not in kept for k in keys):
        return None
    roots = []
    for (dl, dc, _), k in zip(segs, keys):
        root = pickle.loads(kept[k])
        if not _stands_alone(root):
            return None
        roots.append(_shift(root, dl, dc))
    return _merge(roots)


if __name__ == "__main__":
    import sys as _sys
    if len(_sys.argv) == 4 and _sys.argv[1] == "--worker":
        _serve_worker(_sys.argv[2], _sys.argv[3])
