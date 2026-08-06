#!/usr/bin/env python3
# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
mio -- Mohio Language CLI
Version: 0.3.8 | Language: v3.8 | May 2026 | Particular LLC

Usage:
    mio run <file.mho> [options]
    mio check <file.mho>
    mio version
    mio help

Exit codes:
    0  success / clean
    1  compile error (parse error, validation error)
    2  runtime error
    3  file not found or unreadable
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import traceback
from pathlib import Path

# -- Path setup ----------------------------------------------------------------
_HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(_HERE))

import mohio_data
GRAMMAR_FILE = mohio_data.GRAMMAR_PATH

# One process compiles the grammar at most once. `mio serve` was compiling it THREE times
# in a single startup: nothing held the compiled parser, so each caller paid ~20s again.
_PARSER_MEMO = {}

# Set once we learn the parser cannot be pickled, so we stop trying on every call.
_PARSER_UNCACHEABLE = False

def _cache_debug():
    """The parser cache used to narrate itself on every single run -- cache hits, cache
    misses, and a directory listing -- straight to stderr, in normal operation. It buried
    real output. Diagnostics are opt-in now."""
    return os.environ.get("MOHIO_PARSER_DEBUG", "").lower() in ("1", "true", "yes")

# ONE source. mio serve bannered 0.3.8 while /mio/health reported 0.4.4 -- two hardcoded
# strings in one binary. A version that disagrees with itself makes a deploy unprovable.
from mohio_version import VERSION, LANGUAGE_VERSION

# -- Colour helpers -------------------------------------------------------------
_USE_COLOUR = sys.stdout.isatty() and os.name != "nt"

def _c(code, text):
    return f"\033[{code}m{text}\033[0m" if _USE_COLOUR else text

def red(t):    return _c("31", t)
def yellow(t): return _c("33", t)
def green(t):  return _c("32", t)
def bold(t):   return _c("1",  t)
def dim(t):    return _c("2",  t)
def cyan(t):   return _c("36", t)


# -- Grammar loader -------------------------------------------------------------

def _load_grammar():
    if not GRAMMAR_FILE.exists():
        _die(
            f"Grammar file not found: {GRAMMAR_FILE}\n"
            f"Make sure mohio.lark is in the same directory as mio.py.",
            exit_code=3,
        )
    raw = GRAMMAR_FILE.read_text(encoding="utf-8")
    return "\n".join(l for l in raw.splitlines() if not l.strip().startswith("//"))


def _make_parser(grammar):
    from lark import Lark
    return Lark(grammar, parser="earley", ambiguity="resolve",
                propagate_positions=True)


def _detach_re_module(parser):
    """Detach the ONE thing that makes a Lark parser unpicklable, and say what it was.

    A Lark Earley parser is unpicklable for exactly one reason: `lexer_conf.re_module`
    holds the `re` MODULE itself, and pickle cannot serialize a module. That is the whole
    obstacle. Nothing else in the object graph is a problem.

    Nobody ever asked WHAT the unpicklable object was, so the conclusion was "Earley
    cannot be cached" -- and pickle.dump, which writes INCREMENTALLY, kept raising
    halfway and leaving a TRUNCATED file that then failed to load forever ("Ran out of
    input"). The grammar recompiled on every boot for want of one attribute.

    Detach it, pickle, put it back. Returns the module so the caller can restore it.
    """
    saved = getattr(parser.lexer_conf, 're_module', None)
    parser.lexer_conf.re_module = None
    return saved


def _reattach_re_module(parser, module_name):
    """Put the regex module back after a load. Lark uses `re` by default and `regex` if
    the grammar asked for it, so restore the SAME one rather than assuming."""
    import importlib
    parser.lexer_conf.re_module = importlib.import_module(module_name or 're')


def _make_parser_cached(grammar):
    global _PARSER_UNCACHEABLE
    """
    Fast parser for serve mode -- tries LALR first (instant), falls back to Earley.
    LALR is 100x faster than Earley. If the grammar has ambiguities LALR can't
    handle, it falls back to Earley with pickle cache.
    """
    import pickle, hashlib, os, tempfile

    # NOTE: LALR attempt removed -- our grammar has 35,000+ conflicts
    # and LALR takes 30 seconds just to fail. Earley with pickle cache
    # is the only viable option until the Rust rewrite.
    grammar_hash = hashlib.md5(grammar.encode()).hexdigest()[:12]

    # (2) IN-PROCESS MEMOIZATION.
    # A single `mio serve` compiled the grammar THREE TIMES. Nothing held the result, so
    # every caller that wanted a parser paid the full ~20s compile again. One process
    # compiles at most once, per grammar.
    cached = _PARSER_MEMO.get(grammar_hash)
    if cached is not None:
        return cached

    # The hash is over the grammar CONTENT only, never over a path. That is what makes a
    # cache baked at Docker BUILD time (in /app/compiler) loadable at BOOT time from a
    # different working directory: same grammar, same filename, no recompile.
    cache_dirs = [
        GRAMMAR_FILE.parent,          # next to mio.py -- the Docker layer preserves this
        GRAMMAR_FILE.parent / ".parser_cache",
        Path(tempfile.gettempdir()),
    ]
    cache_file = None
    for cache_dir in cache_dirs:
        try:
            cache_dir.mkdir(exist_ok=True, parents=True)
            candidate = cache_dir / f"mohio_parser_{grammar_hash}.pkl"
            test_file = cache_dir / ".write_test"
            test_file.touch()
            test_file.unlink(missing_ok=True)
            cache_file = candidate
            break
        except OSError:
            continue

    if cache_file and cache_file.exists():
        try:
            with open(cache_file, "rb") as f:
                payload = pickle.load(f)
            parser, re_module_name = payload["parser"], payload["re_module"]
            _reattach_re_module(parser, re_module_name)
            if _cache_debug():
                print(f"  [parser] cache hit: {cache_file}", file=sys.stderr)
            _PARSER_MEMO[grammar_hash] = parser
            return parser
        except Exception as e:
            # A truncated pickle ("Ran out of input") is a CORRUPT cache. It used to stay
            # corrupt forever, because the file was written non-atomically. Remove it and
            # let the atomic rewrite below replace it cleanly.
            print(f"  [parser] cache unreadable ({e}) -- rebuilding", file=sys.stderr)
            try:
                cache_file.unlink(missing_ok=True)
            except OSError:
                pass
    elif _cache_debug():
        # (3) This message used to say "listing /app:" -- a hardcoded path -- while
        # actually listing the grammar's directory. It printed "/app" on Windows.
        # It also printed on every single run, which is why it drowned real output.
        here = GRAMMAR_FILE.parent
        print(f"  [parser] no cache at {cache_file} -- listing {here}:", file=sys.stderr)
        try:
            for f in os.listdir(str(here)):
                if 'pkl' in f or 'parser' in f or 'cache' in f:
                    print(f"    found: {f}", file=sys.stderr)
        except OSError:
            pass

    print("  [parser] compiling grammar (first run -- this takes a moment)...", file=sys.stderr)
    from lark import Lark
    parser = Lark(grammar, parser="earley", ambiguity="resolve",
                  propagate_positions=True)
    _PARSER_MEMO[grammar_hash] = parser

    if cache_file and not _PARSER_UNCACHEABLE:
        # (1) ATOMIC WRITE.
        # This used to pickle.dump straight into the final path. pickle.dump writes
        # INCREMENTALLY, so when it hit the unpicklable object it raised HALFWAY THROUGH
        # and left a truncated file -- which the old `except Exception: pass` swallowed.
        # Next start: "cache load failed: Ran out of input". Delete, recompile, write
        # another truncated file. Forever. That loop is why the grammar recompiled on
        # every single boot.
        #
        # Write to a temp file in the SAME directory (os.replace is only atomic within a
        # filesystem), then rename into place. A reader sees the old complete file or the
        # new complete file -- never a half-written one. No partial file can ever land.
        tmp_path = None
        saved_re = _detach_re_module(parser)
        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=str(cache_file.parent), prefix=f".{cache_file.name}.", suffix=".tmp")
            with os.fdopen(fd, "wb") as f:
                pickle.dump({"parser": parser,
                             "re_module": getattr(saved_re, "__name__", "re")},
                            f, protocol=pickle.HIGHEST_PROTOCOL)
                f.flush()
                os.fsync(f.fileno())          # bytes are on disk before the rename
            os.replace(tmp_path, cache_file)  # atomic on POSIX and on Windows
            tmp_path = None
            if _cache_debug():
                print("  [parser] cached for future startups", file=sys.stderr)
        except (TypeError, pickle.PicklingError, AttributeError) as e:
            # Something ELSE became unpicklable (a Lark upgrade, most likely). Say so once
            # and stop trying, rather than failing noisily on every call.
            _PARSER_UNCACHEABLE = True
            print(f"  [parser] not cacheable on disk ({e}); compiling per process",
                  file=sys.stderr)
        except Exception as e:
            print(f"  [parser] could not write cache: {e}", file=sys.stderr)
        finally:
            _reattach_re_module(parser, getattr(saved_re, "__name__", "re"))
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)       # never leave a partial file behind
                except OSError:
                    pass
    return parser


# -- Error and warning printers -------------------------------------------------

def _ast_cache_path(source_path: str) -> str:
    """Return path to AST cache file for a given source file."""
    import os
    base = os.path.splitext(source_path)[0]
    return base + ".mho.cache"


def _source_hash(source: str) -> str:
    """Fast hash of source content for cache invalidation."""
    import hashlib
    return hashlib.sha256(source.encode()).hexdigest()[:16]


# Cache format version. Bump only if the cache *structure* changes.
_AST_CACHE_VERSION = 2

_COMPILER_FINGERPRINT = None
def _compiler_fingerprint() -> str:
    """
    Hash of the compiler's own source files. ANY change to the grammar, transformer,
    interpreter, scanners, or CLI invalidates every AST cache -- so a compiler upgrade can
    never be overridden by a cache written by an older compiler.

    THIS USED TO BE A HAND-WRITTEN LIST, and the list did not name `mohio_reachability.py`
    -- the file where EVERY SCANNER LIVES. So changing a scanner did not invalidate the
    cache: `mio check` replayed a cached "clean" result and the new rule silently never
    ran. It also missed `mohio_enforce.py` (the enforcement door) and
    `mohio_sector_loader.py` (the compliance floors), which means a tightened SECTOR FLOOR
    would not take effect on an already-checked file. `mio check` would report "no errors"
    on a program that violates it.

    It is the same disease as everything else this week: A LIST THAT DOES NOT NAME A THING
    DOES NOT FAIL -- IT SILENTLY DOES NOTHING. So this no longer keeps a list. It hashes
    the grammar plus EVERY compiler .py file that sits beside it, and a new compiler module
    is covered the day it is created rather than the day someone remembers to add it here.
    """
    global _COMPILER_FINGERPRINT
    if _COMPILER_FINGERPRINT is not None:
        return _COMPILER_FINGERPRINT
    import hashlib, os, glob
    h = hashlib.sha256()
    h.update(f"v{_AST_CACHE_VERSION}|".encode())
    here = os.path.dirname(os.path.abspath(__file__))

    files = [os.path.basename(p) for p in glob.glob(os.path.join(here, '*.py'))]
    for fn in sorted(set(files)):
        try:
            with open(os.path.join(here, fn), 'rb') as f:
                h.update(fn.encode())
                h.update(h.digest())  # order-sensitive
                h.update(f.read())
        except Exception:
            pass  # missing file -> just contributes nothing
    try:
        h.update(mohio_data.GRAMMAR_PATH.name.encode())
        h.update(h.digest())
        h.update(mohio_data.GRAMMAR_PATH.read_bytes())
    except Exception:
        pass  # missing grammar -> just contributes nothing
    _COMPILER_FINGERPRINT = h.hexdigest()[:16]
    return _COMPILER_FINGERPRINT


def _ctx_has_errors(ctx) -> bool:
    return bool(ctx is not None and getattr(ctx, 'errors', None))


def _load_ast_cache(source_path: str, source: str):
    """
    Load cached AST only if BOTH the source AND the compiler are unchanged,
    and the cached build was clean. Returns (tree, ctx) or None.

    A cache that was written by a different compiler version, or that recorded
    a failed build, is treated as a miss -- never replayed. This prevents an
    old compiler from poisoning serve/check with errors the current compiler
    would not produce.
    """
    import os, pickle
    cache_path = _ast_cache_path(source_path)
    if not os.path.exists(cache_path):
        return None
    try:
        with open(cache_path, 'rb') as f:
            cached = pickle.load(f)
        if cached.get('hash') != _source_hash(source):
            return None  # source changed
        if cached.get('compiler') != _compiler_fingerprint():
            return None  # compiler changed -- never trust an old build
        ctx = cached.get('ctx')
        if _ctx_has_errors(ctx):
            return None  # never replay a cached failure
        return cached.get('tree'), ctx
    except Exception:
        pass  # Corrupted cache -- re-parse
    return None


def _save_ast_cache(source_path: str, source: str, tree, ctx):
    """
    Save parsed AST to cache file -- but ONLY for clean builds. A ctx carrying
    errors is never cached, so a failure can never be persisted and replayed.
    """
    import pickle
    if _ctx_has_errors(ctx):
        return  # never cache an error-state build
    cache_path = _ast_cache_path(source_path)
    try:
        with open(cache_path, 'wb') as f:
            pickle.dump({
                'hash':     _source_hash(source),
                'compiler': _compiler_fingerprint(),
                'tree':     tree,
                'ctx':      ctx,
            }, f, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception as e:
        # A cache-write failure is non-fatal (the program still runs; it just re-parses next
        # time), but it must not be SILENT. The old bare `except: pass` meant an unpicklable
        # node would make every run quietly re-parse forever with no clue why -- the exact
        # silent-no-op class this project is eradicating. Say so once (never raise). The parser
        # cache already reports its write failures the same way.
        print(f"  [ast-cache] could not write cache for {source_path}: {e}", file=sys.stderr)



def _source_snippet(source, line_no):
    """Return a formatted source line for display."""
    if not source or not line_no:
        return None
    lines = source.splitlines()
    if 1 <= line_no <= len(lines):
        return lines[line_no - 1]
    return None


def _construct_ai_runtime(api_key, verbose):
    """A8: build the AI runtime, honoring MOHIO_AI=mock to force the labeled mock provider
    (classroom weeks 1-3 need mock mode; the real runtime needs a provider key). Any real provider
    key (Anthropic / OpenAI / Gemini) activates AnthropicAiRuntime, which routes by model prefix."""
    import os as _os
    from mohio_interpreter import MockAiRuntime
    if _os.environ.get("MOHIO_AI", "").strip().strip('"').strip("'").lower() == "mock":
        return MockAiRuntime()
    from mohio_ai import AnthropicAiRuntime
    return AnthropicAiRuntime(api_key=api_key, verbose=verbose)


def _beginner_parse_hint(e, source, line):
    """Recognize a few common traps from the offending line and return a clear
    fix hint, so a cryptic parser message ('No terminal matches +') becomes
    actionable guidance. Returns None when no trap is recognized."""
    import re as _re
    # 'loop' used as a counted loop:  loop 3 times.  The parser mis-reads the
    # loop header and the error often lands on a LATER line, so scan the whole
    # source rather than just the offending line. `loop <number>` is always the
    # misuse (loop is the conditional loop), so this is safe.
    for _ln in (source or "").splitlines():
        if _re.match(r'\s*loop\s+\d', _ln.lower()):
            return ("'loop' is for conditional loops (loop while ... / loop until ...).\n"
                    "For a counted loop, use:  repeat 3 times -> ...  then  repeat: done")
    # An unknown `ai.<verb>`: the AI namespace fails cryptically ('No terminal matches ...') because
    # only the real verbs are terminals. Name the valid ones instead of failing blankly. `generate`
    # is the common trap -- generation lives on `ai.create`. These are INVALID verbs (not a
    # deferral); the declared-but-unbuilt verbs (ai.override) fail loud at runtime, not here.
    _AI_VERBS = ('decide', 'rank', 'audit', 'explain', 'create', 'connect',
                 'override', 'resolve', 'agent', 'compare', 'respond')
    for _ln in (source or "").splitlines():
        _am = _re.search(r'\bai\.([a-z_]+)', _ln.lower())
        if _am and _am.group(1) not in _AI_VERBS:
            _v = _am.group(1)
            if _v == 'generate':
                return ("`ai.generate` is not a Mohio verb. Use `ai.create` to generate text, "
                        "data, an image, or video.")
            return (f"`ai.{_v}` is not a Mohio verb. The AI verbs are: ai.decide, ai.rank, "
                    "ai.compare, ai.respond, ai.explain, ai.create, ai.agent, ai.resolve, "
                    "ai.audit, ai.connect, ai.override.")
    snippet = _source_snippet(source, line) if line else None
    if not snippet:
        return None
    s   = snippet.strip()
    low = s.lower()
    msg = str(e)
    # task parameters in parentheses:  task greet(name)
    if _re.match(r'task\s+\w+\s*\(', s):
        return ("Tasks don't take parameters in parentheses.\n"
                "Use:  task greet name as text   — or put  receive name  inside the task body.")
    # 'when' combined with a comparison:  when above 12
    m = _re.search(r'\bwhen\s+(above|below|contains|not|is\s+in)\b', low)
    if m:
        word = m.group(1)
        return ("'when' matches a value (e.g.  when \"active\" -> ...).\n"
                f"For a comparison, drop 'when' and use the bare form:  {word} 12 -> ...")
    op_unmatched = bool(_re.search(r"No terminal matches '[-+*/]'", msg))
    # '+' next to a string -> a text-join attempt
    if op_unmatched and '"' in s and '+' in s:
        return ("Mohio doesn't use + to join text.\n"
                "Insert a value with double braces:  show \"Hi {{ name }}\".")
    # arithmetic operator outside parentheses:  hold x = a + b
    if op_unmatched and _re.search(r'=\s*[^()\n]*[-+*/]', s):
        return ("Math must be wrapped in parentheses.\n"
                "Use:  hold total = (a + b)   — parentheses are required around calculations.")
    return None


_BLOCK_OPENERS = (
    'shape', 'task', 'listen', 'check', 'find', 'save', 'retrieve', 'update', 'remove',
    'replace', 'repeat', 'loop', 'each', 'run', 'try', 'transaction', 'saga', 'step',
    'send', 'new', 'request', 'sql', 'mioconnect', 'miovalidate', 'miocache', 'mioscript',
    'ai.decide', 'ai.explain', 'ai.agent', 'ai.resolve', 'ai.compare', 'ai.respond',
)


def _find_retired_set(source):
    """`set` is retired. It was accepted as noise and SILENTLY DISCARDED -- exactly how a dead
    keyword survives in docs and comes back as canon.

    Detected at SOURCE level, not in the grammar: a `SET NAME ...` grammar rule makes Earley's
    dynamic lexer match `set` INSIDE identifiers (`rset_skip` in the Zork demo split into
    `r` + `set` + `_skip`). A word-boundary scan on the statement head has no such hazard.
    """
    import re as _r
    for i, raw in enumerate(source.split('\n')):
        line = raw.strip()
        if not line or line.startswith('//'):
            continue
        m = _r.match(r'set\s+([A-Za-z_]\w*)\b', line)
        if m:
            return (m.group(1), i + 1)
    return (None, None)


def _find_unclosed_block(source):
    """Find a block that opens a body but never closes.

    Every verb block closes with `<kind>: done`. An unclosed one dies at end-of-input
    inside the parser with no line and no fix, so recover it here: an opener that is
    followed by a more-indented line has a body, and that body needs a closer at the
    opener's own indent. Returns (keyword, line_no) for the innermost offender.

    Heuristic, and that is fine: it only runs on an already-failing parse, so the worst
    case is a hint that does not apply -- never a false error.
    """
    import re as _r
    lines = source.split("\n")
    def _code(i):
        s = lines[i].strip()
        return s and not s.startswith("//")
    stack = []
    for i, raw in enumerate(lines):
        if not _code(i):
            continue
        stripped = raw.strip()
        indent = len(raw) - len(raw.lstrip())
        # a closer: `kw: done` or bare `done`
        m = _r.match(r'^([\w.]+)\s*:\s*done\b', stripped)
        if stripped == "done" or m:
            kind = m.group(1) if m else None
            if kind is None:
                # bare `done` closes the innermost open block
                if stack:
                    stack.pop()
                continue
            # A named closer closes its own kind. If the innermost open block is a
            # DIFFERENT kind, that inner block was never closed -- that is the offender.
            if stack and stack[-1][0] != kind:
                return (stack[-1][0], stack[-1][1])
            while stack and stack[-1][0] != kind:
                stack.pop()
            if stack:
                stack.pop()
            continue
        first = stripped.split()[0].rstrip(':')
        if first in _BLOCK_OPENERS:
            # does it actually open a body? (next code line is more indented)
            nxt = next((j for j in range(i + 1, len(lines)) if _code(j)), None)
            if nxt is not None:
                nxt_indent = len(lines[nxt]) - len(lines[nxt].lstrip())
                if nxt_indent > indent:
                    stack.append((first, i + 1, indent))
    return (stack[-1][0], stack[-1][1]) if stack else (None, None)


def _bare_service_root(source, line):
    """Is the offending line a service root used bare, with no `.operation`?

    Returns (root, is_planned) or None. Reads the ONE canonical service list, so a new
    service reserved in mohio_services.py gets this message for free.
    """
    if not source or not line:
        return None
    try:
        from mohio_services import SERVICE_ROOTS, SERVICE_ROOTS_PLANNED
    except Exception:
        return None
    lines = source.splitlines()
    # The parser can report a non-positive line (-1 / 0) when it cannot localize the
    # error (e.g. end-of-input). Guard BOTH bounds: a negative line made `lines[line-1]`
    # wrap to a bogus index and crash the error printer with a Python traceback -- the
    # opposite of a legible failure.
    if line < 1 or line > len(lines):
        return None
    stripped = lines[line - 1].strip()
    if not stripped:
        return None
    first = stripped.split()[0]
    # `miopdf.from` is a DOTTED op -- it parses, and the not-built check owns its message.
    # Only the bare root is our business here.
    if "." in first:
        return None
    if first in SERVICE_ROOTS:
        return (first, first in SERVICE_ROOTS_PLANNED)
    return None


def _print_parse_error(e, source="", filename=""):
    # Wrapper: whatever branch the printer takes, a missing language pack gets attributed
    # afterwards. Patching each return path individually is how one gets missed.
    try:
        _print_parse_error_body(e, source, filename)
    finally:
        _langmap_missing_note()


def _print_parse_error_body(e, source="", filename=""):
    header = bold(red("Syntax error")) + (f"  {dim(filename)}" if filename else "")
    print(f"\n{header}\n")
    line = getattr(e, "line", None)
    col  = getattr(e, "column", None)
    if line:
        snippet = _source_snippet(source, line)
        if snippet is not None:
            print(f"  {dim(str(line) + ' |')} {snippet}")
            if col:
                print(f"  {dim('  |')} {' ' * (col - 1)}{red('^')}")
            print()
    msg = str(e).split("\n")[0][:120]
    # A BARE service root (`miopdf "x"`) is not a form -- every service is a dotted op.
    # It used to die as a generic "Syntax error" that never mentioned miopdf was a
    # reserved service, while the DOTTED form (`miopdf.from ...`) gave a clear, named,
    # directional message. Same failure, two wildly different messages. Recover the line.
    hit = _bare_service_root(source, line)
    if hit:
        root, planned = hit
        print(f"  {red('x')} {bold(root)} is a Mohio service, not a variable, and it is "
              f"never used bare.")
        print(f"\n    Services are always called as an operation: {bold(root + '.<operation>')}.")
        if planned:
            print(f"    {root} is a RESERVED, PLANNED service -- the name is claimed, but")
            print(f"    nothing is built behind it yet.")
        print(f"    If you meant a variable, pick a name that is not a service root.\n")
        return
    # An unclosed block dies at end-of-input with no line and no fix. Recover both.
    if "end-of-input" in msg.lower() or "end of input" in msg.lower():
        kw, kw_line = _find_unclosed_block(source)
        if kw:
            snippet = _source_snippet(source, kw_line)
            print(f"  {red('x')} The {bold(kw)} block opened on line {kw_line} is never closed.")
            if snippet is not None:
                print(f"  {dim(str(kw_line) + ' |')} {snippet}")
            print(f"\n    Every verb block closes with its own closer.")
            print(f"    Add '{kw}: done' at the same indent as the '{kw}' on line {kw_line}.\n")
            return
    print(f"  {msg}\n")
    hint = _beginner_parse_hint(e, source, line)
    if hint:
        for hint_line in hint.splitlines():
            print(f"    {dim(hint_line)}")
        print()


def _langmap_missing_note():
    """If a declared language pack was missing, say so alongside the error it caused.

    A file written in Spanish without the Spanish pack fails with a message about Spanish words
    -- `No terminal matches 'h'` -- which reads as though the developer wrote something wrong.
    They did not. The compiler was handed a file it had no way to read and blamed the file.
    """
    hint = globals().get('_LANGMAP_MISSING_HINT')
    if not hint:
        return
    print()
    print(f"  {yellow('!')} This file declares `// language: {hint}` and that pack is "
          f"NOT INSTALLED.")
    print(f"    The error above is very likely a consequence of that: without the pack, "
          f"{hint} keywords")
    print(f"    are not recognised and are read as ordinary names. Install the pack, or "
          f"remove the header")
    print(f"    if this file is written in English.")


def _print_compile_error(err, source="", filename=""):
    """Print a single CompileError from the new transformer."""
    header = bold(red("Error")) + (f"  {dim(filename)}" if filename else "")
    snippet = _source_snippet(source, err.line)
    if snippet is not None:
        print(f"  {dim(str(err.line) + ' |')} {snippet}")
    print(f"  {red('x')} {err.message}")
    if err.hint:
        for hint_line in err.hint.splitlines():
            print(f"    {dim(hint_line)}")
    print()


def _print_compile_warning(warn, source="", filename=""):
    """Print a single CompileWarning from the new transformer."""
    snippet = _source_snippet(source, warn.line)
    if snippet is not None:
        print(f"  {dim(str(warn.line) + ' |')} {snippet}")
    print(f"  {yellow('!')} {warn.message}")
    if warn.hint:
        for hint_line in warn.hint.splitlines():
            print(f"    {dim(hint_line)}")
    print()


def _print_runtime_error(e, filename="", source="", line=0):
    line = line or getattr(e, "line", 0) or 0
    loc  = f"{filename}:{line}" if (filename and line) else filename
    header = bold(red("Runtime error")) + (f"  {dim(loc)}" if loc else "")
    print(f"\n{header}\n")
    snippet = _source_snippet(source, line) if (source and line) else None
    if snippet:
        print(f"  {dim(str(line) + ' |')} {snippet}")
    print(f"  {str(e)}\n")


def _die(message, exit_code=1):
    print(f"\n{bold(red('Error'))}  {message}\n", file=sys.stderr)
    sys.exit(exit_code)


def _read_source(path, exit_code=2):
    """Read a .mho source file, failing loud with ONE clear message instead of a
    raw Python traceback (Unit B). Handles the foreseeable file problems -- missing,
    a directory, not valid UTF-8, unreadable -- so none of them fall through to the
    generic backstop in main(). Returns the source text."""
    from pathlib import Path as _P
    p = path if isinstance(path, _P) else _P(path)
    if not p.exists():
        _die(f"File not found: {p}", exit_code=exit_code)
    if p.is_dir():
        _die(f"Expected a .mho file but that is a directory: {p}", exit_code=exit_code)
    try:
        return p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        _die(f"Cannot read {p}: it is not valid UTF-8 text (is it a binary file?). "
             f"Mohio source must be UTF-8.", exit_code=exit_code)
    except PermissionError:
        _die(f"Cannot read {p}: permission denied.", exit_code=exit_code)
    except OSError as e:
        _die(f"Cannot read {p}: {e.strerror or e}.", exit_code=exit_code)


def _die_unexpected(exc, command):
    """Last-resort backstop (Unit B): no raw Python traceback ever reaches a user.
    Any exception that escapes a command handler lands here and becomes ONE clear
    message. The full trace is shown only under MOHIO_DEBUG=1, for compiler devs."""
    import os as _os
    etype = type(exc).__name__
    print(f"\n{bold(red('Internal error'))}  mio {command} stopped on an unexpected "
          f"{etype}.", file=sys.stderr)
    msg = str(exc).strip()
    if msg:
        print(f"  {msg}", file=sys.stderr)
    if _os.environ.get("MOHIO_DEBUG", "").strip().lower() in ("1", "true", "yes", "on"):
        traceback.print_exception(type(exc), exc, exc.__traceback__)
    else:
        print("  Re-run with MOHIO_DEBUG=1 to see the full trace.", file=sys.stderr)
    sys.exit(2)


def _mask_noncode(src):
    """Return src with comment and raw-block interiors replaced by spaces, preserving
    every newline and character offset (so error line/col still line up with the source).
    Raw blocks -- `sql`, a bare `show`, and `render`/`render html`/`render scripts` --
    capture non-Mohio lines that legitimately contain ', ", and {{ }}; masking them keeps
    the string-hygiene scan (Unit C) from mistaking raw SQL/HTML for Mohio code."""
    NL = chr(10)
    # Pass 1: blank // and /* */ comments, string-aware, offsets preserved.
    buf = list(src); i = 0; n = len(src); in_str = False
    while i < n:
        c = src[i]
        if in_str:
            if c == '\\':
                i += 2; continue
            if c == '"':
                in_str = False
            i += 1; continue
        if c == '"':
            in_str = True; i += 1; continue
        if c == '/' and i + 1 < n and src[i+1] == '/':
            while i < n and src[i] != NL:
                buf[i] = ' '; i += 1
            continue
        if c == '/' and i + 1 < n and src[i+1] == '*':
            e = src.find('*/', i + 2); e = e + 2 if e >= 0 else n
            while i < e:
                if src[i] != NL:
                    buf[i] = ' '
                i += 1
            continue
        i += 1
    masked = ''.join(buf)
    # Pass 2: blank raw-content block interiors, line based (runs on the comment-masked
    # text, so an opener word sitting inside a former comment is already gone).
    def _opener(code):
        if code in ("sql", "show", "render", "render html", "render scripts"):
            return "render" if code.startswith("render") else code
        return None
    out, raw = [], None
    for line in masked.split(NL):
        code = line.strip()
        if raw is None:
            out.append(line)                       # opener (or ordinary) line stays
            raw = _opener(code)
        elif code == f"{raw}: done" or code == "done":
            out.append(line); raw = None           # closer stays
        else:
            out.append(' ' * len(line))            # raw interior -> spaces
    return NL.join(out)


# -- Result formatter -----------------------------------------------------------

def _print_result(result, verbose=False):
    if result is None:
        if verbose:
            print(dim("  (no response -- program completed without give back)"))
        return
    if isinstance(result, dict):
        status = result.get("status", "")
        body   = result.get("body", "")
        if isinstance(status, int):
            status_str = green(str(status)) if status < 300 else \
                         yellow(str(status)) if status < 400 else red(str(status))
        else:
            status_str = str(status)
        print(f"\n  {bold('Response')}  {status_str}  {body}")
    else:
        print(f"\n  {bold('Result')}  {result}")
    print()


# -- Shared parse + validate step ----------------------------------------------

def _parse_and_validate(source, filename, verbose=False):
    # Per-file state. The missing-pack hint is set during THIS compile and must not survive it:
    # a stale hint would attach "that pack is NOT INSTALLED" to an unrelated error in a later
    # file, which is a confident wrong answer -- the worst kind of diagnostic.
    globals().pop('_LANGMAP_MISSING_HINT', None)

    # A single leading UTF-8 BOM (U+FEFF) is file metadata, not code. Windows tools add it
    # by default (PowerShell `Out-File -Encoding utf8`, Notepad, some editors), so a newcomer
    # would otherwise get a line 1 col 1 non-ASCII error for an invisible character they never
    # typed. Strip exactly one leading BOM; non-ASCII anywhere else still fails loud below.
    if source and source[0] == '\ufeff':
        source = source[1:]
    """
    Parse source and run compile-time validation.
    Returns (tree, ctx) or exits on hard failure.
    ctx.errors is populated if validation fails.
    ctx.warnings always printed.
    """
    # -1a. ASCII enforcement -- Mohio source must be ASCII only in executable positions
    # Comments (//) and string literals are excluded -- those are for humans.
    # Non-ASCII in keywords, identifiers, operators: rejected with clear error.
    def _check_ascii(src, fname):
        in_string = False
        string_char = None
        i = 0
        lines = src.split(chr(10))
        flat = src
        while i < len(flat):
            char = flat[i]
            # Skip line comments
            if not in_string and i + 1 < len(flat) and flat[i:i+2] == '//':
                # Skip to end of line
                end = flat.find(chr(10), i)
                i = end if end >= 0 else len(flat)
                continue
            # Track string boundaries
            if not in_string and char in ('"', "'"):
                in_string = True
                string_char = char
                i += 1
                continue
            if in_string:
                if char == string_char:
                    in_string = False
                i += 1
                continue
            # Executable position -- enforce ASCII
            if ord(char) > 127:
                line_num = flat[:i].count(chr(10)) + 1
                col_num  = i - flat[:i].rfind(chr(10))
                snippet  = lines[line_num - 1] if line_num <= len(lines) else ""
                print(f"  Syntax error  {fname}")
                print(f"  {line_num} | {snippet}")
                print(f"  {' ' * col_num}^")
                print(f"  x Non-ASCII character in executable code.")
                print(f"    Found: {repr(char)} (U+{ord(char):04X}) at line {line_num} col {col_num}.")
                print(f"    Hint: Non-ASCII is allowed in comments (//) and string literals.")
                print(f"          Keywords, identifiers, and operators must be ASCII.")
                sys.exit(1)
            i += 1

    # -1a'. String hygiene (Unit C) -- fail loud on the silent/unhelpful string bugs:
    #   (1) an unescaped " inside a string (which silently TRUNCATES the rest),
    #   (2) an unclosed {{ }} interpolation (which silently prints literal braces),
    #   (3) curly/smart quotes used as string quotes, (4) single-quoted strings.
    # Runs on the canonical source next to the ASCII gate so BOTH `mio check` and `mio run`
    # catch it -- (1) and (2) used to pass check clean and produce wrong output at run.
    # Comment and string INTERIORS are skipped (they are for humans); a nested string inside
    # {{ }} is skipped; a genuinely unterminated string is left to the parser, not claimed here.
    def _check_string_hygiene(src, fname):
        CURLY = "“”‘’"
        orig_lines = src.split(chr(10))
        scan = _mask_noncode(src)              # comments + raw sql/show/render blanked; offsets preserved
        def _fail(pos, msg):
            before = scan[:pos]
            line_no = before.count(chr(10)) + 1
            col0 = pos - (before.rfind(chr(10)) + 1)     # 0-based column on the line
            snippet = orig_lines[line_no - 1] if line_no - 1 < len(orig_lines) else ""
            prefix = f"  {line_no} | "
            print(f"\n  Syntax error  {fname}")
            print(f"{prefix}{snippet}")
            print(f"  {' ' * (len(prefix) - 2 + col0)}^")
            print(f"  x {msg}")
            sys.exit(1)
        i, n = 0, len(scan)
        while i < n:
            c = scan[i]
            if c in CURLY:
                _fail(i, "curly quotes are not string quotes in Mohio, use straight double quotes.")
            if c == "'":
                _fail(i, "Mohio strings use double quotes.")
            if c == '"':
                j = i + 1; depth = 0; interp_pos = None; closed = False
                while j < n:
                    cj = scan[j]
                    if cj == '\\':                                  # escape: next char is literal
                        j += 2; continue
                    if cj == '{' and j + 1 < n and scan[j+1] == '{':
                        if depth == 0: interp_pos = j
                        depth += 1; j += 2; continue
                    if cj == '}' and j + 1 < n and scan[j+1] == '}':
                        if depth > 0: depth -= 1
                        j += 2; continue
                    if cj == '"':                                   # a " always closes: the lexer has no nested strings
                        closed = True; break
                    j += 1
                if not closed:
                    return                                          # unterminated -- the parser owns this error
                if depth > 0:
                    _fail(interp_pos, "unclosed interpolation, `{{` needs a matching `}}`.")
                nxt = scan[j + 1] if j + 1 < n else ''
                if nxt.isalnum() or nxt == '_' or nxt == '"':
                    _fail(j, "unescaped double quote inside a string, use \\\" for a quote inside text.")
                i = j + 1
                continue
            i += 1

    # NOTE: the ASCII gate runs AFTER the langmap pre-pass (below), not before.
    # The locked pipeline is: Layer 3 reorder -> Layer 1 substitution -> parser, and the
    # parser only ever sees canonical English. Gating the *untranslated* source would
    # reject every non-Latin pack (Devanagari, Cyrillic, Greek) before Layer 1 could
    # translate it, which is what happened. The gate belongs on the canonical output.

    # -1b. Langmap pre-pass -- translate non-English keywords to canonical
    # Detect language from source file header comment or declaration
    # e.g: // language: klingon  OR  yoS: Huch (sector: financial in Klingon)
    _translated_source = source
    try:
        from mohio_langmap import preprocess_source
        import os
        # Detect language hint from file header
        _lang_hint = None
        for _line in source.split(chr(10))[:10]:
            _line = _line.strip()
            # Explicit: // language: klingon
            _lm = re.search(r'//\s*language:\s*(\w+)', _line, re.IGNORECASE)
            if _lm:
                _lang_hint = _lm.group(1)
                break
            # Explicit: // langmap: maps/en-klingon.langmap
            _lm2 = re.search(r'//\s*langmap:\s*(\S+\.langmap)', _line, re.IGNORECASE)
            if _lm2:
                _lang_hint = _lm2.group(1)
                break
        
        if _lang_hint:
            # Find maps directory relative to source file
            _maps_dir = os.path.join(os.path.dirname(os.path.abspath(filename)),
                                      '..', 'maps')
            if not os.path.isdir(_maps_dir):
                _maps_dir = str(mohio_data.MAPS_DIR)
            # Validate langmap before translating
            try:
                from mohio_langmap import LangmapLoader, LANGMAP_VERSION
                import glob as _glob
                # Find the langmap file
                _lmap_path = None
                if _lang_hint.endswith('.langmap'):
                    _lmap_path = _lang_hint
                else:
                    # Prefer an EXACT match (`en-spanish.langmap` for hint `spanish`) before
                    # falling back to a substring glob. The glob alone took _candidates[0] in
                    # arbitrary filesystem order, so a hint of `en` matched every en-* pack and
                    # silently picked one.
                    _exact = [os.path.join(_maps_dir, f'en-{_lang_hint}.langmap'),
                              os.path.join(_maps_dir, f'{_lang_hint}.langmap')]
                    _lmap_path = next((c for c in _exact if os.path.exists(c)), None)
                    if _lmap_path is None:
                        _candidates = sorted(_glob.glob(
                            os.path.join(_maps_dir, f'*{_lang_hint}*.langmap')))
                        if len(_candidates) > 1:
                            print(f"  {yellow('!')} LANGMAP_AMBIGUOUS: '{_lang_hint}' matches "
                                  f"{len(_candidates)} packs; using {_candidates[0]}.")
                            for _c in _candidates:
                                print(f"    candidate: {_c}")
                        if _candidates:
                            _lmap_path = _candidates[0]

                if _lmap_path is None:
                    # A declared pack that is not installed does NOT refuse the build. English
                    # fallback is the design: unmapped keywords fall back to English, and a
                    # whole missing pack is just that taken to its limit. A file whose body is
                    # already English -- a stale header, a partially translated file -- still
                    # compiles, and refusing it would break working code for a header comment.
                    #
                    # What it must not do is stay SILENT. Without this warning the developer
                    # gets a syntax error about words in their own language and no hint that a
                    # pack is missing, which is the one outcome that helps nobody. The warning
                    # is recorded so that if the compile then fails, the failure can be
                    # attributed to the missing pack rather than blamed on their code.
                    print(f"  {yellow('!')} LANGMAP_MISSING: this file declares "
                          f"`// language: {_lang_hint}` but no pack for it is installed.")
                    print(f"    Looked in: {_maps_dir}")
                    print(f"    Compiling as English. Keywords written in {_lang_hint} will not "
                          f"be recognised.")
                    globals()['_LANGMAP_MISSING_HINT'] = _lang_hint
                
                if _lmap_path:
                    _loader = LangmapLoader(_lmap_path)
                    # INTEGRITY: refuse a pack that can change what a program MEANS.
                    # Incompleteness is fine and stays a warning -- unmapped keywords fall back
                    # to English, which is how a pack grows. A collision, an accent-only
                    # duplicate, a retired keyword, or a failed round-trip is different: each
                    # one lets the same source read as two different programs, silently, and a
                    # translated file is meant to be exchangeable.
                    from mohio_langmap import verify_pack as _verify_pack
                    _bad = [f for f in _verify_pack(_loader) if f[0] == 'refuse']
                    if _bad:
                        _lines = [f"Language pack {_lmap_path} cannot be used: "
                                  f"{len(_bad)} integrity failure(s).", ""]
                        for _sev, _code, _msg in _bad[:6]:
                            _lines.append(f"  {_code}: {_msg}")
                        if len(_bad) > 6:
                            _lines.append(f"  ... and {len(_bad) - 6} more.")
                        _lines.append("")
                        _lines.append("  Each of these lets the same source compile to a "
                                      "different program depending on how it is read.")
                        _lines.append("  An INCOMPLETE pack is fine -- unmapped keywords fall "
                                      "back to English. A pack that changes meaning is not.")
                        _die("\n".join(_lines), exit_code=3)
                    # Version check
                    if not _loader.validate_version(LANGMAP_VERSION):
                        print(f"  {yellow('!')} LANGMAP_VERSION: {_lmap_path} "
                              f"version {_loader.get_version()} incompatible "
                              f"with compiler version {LANGMAP_VERSION}")
                        print(f"    Update your langmap file or use a compatible version.")
                    # Coverage report -- informational only. Unmapped keywords fall back to
                    # English by design ("English fallback is the feature"); this is never
                    # an error and never halts compilation.
                    _missing = _loader.validate_completeness()
                    if _missing:
                        _full = bool(globals().get('_LANGMAP_FULL_LIST'))
                        print(f"  {yellow('!')} LANGMAP_COVERAGE: {_lmap_path} "
                              f"does not map {len(_missing)} keyword(s):")
                        _show = _missing if _full else _missing[:5]
                        for _kw in _show:
                            print(f"    unmapped: '{_kw}'")
                        if not _full and len(_missing) > 5:
                            print(f"    ... and {len(_missing)-5} more. "
                                  f"Run 'mio check --langmap {filename}' for the full list.")
                        print(f"    Unmapped keywords fall back to English. Not an error.")
            except SystemExit:
                raise      # an integrity refusal is a decision, not a diagnostic
            except Exception:
                pass  # Coverage/version reporting is informational -- never blocks

            # Check if it's an explicit langmap path
            if _lang_hint.endswith('.langmap'):
                _translated_source = preprocess_source(source, None, 
                                                         langmap_path=_lang_hint)
            else:
                _translated_source = preprocess_source(source, _lang_hint, 
                                                         maps_dir=_maps_dir)
    except (ImportError, FileNotFoundError):
        pass  # No langmap -- use source as-is
    source = _translated_source

    # ASCII gate -- runs on the CANONICAL source (post-langmap), which is what the parser
    # sees. A non-Latin pack (Hindi/Devanagari, emoji) translates to ASCII English first and
    # passes; leftover non-ASCII in executable position is a genuine error (unmapped token in
    # a keyword slot). English files are unaffected: no langmap means source is unchanged.
    _check_string_hygiene(source, filename)
    _check_ascii(source, filename)

    # -1. AST cache check -- fastest path (cache hit = skip parse entirely)
    # filename must be provided for cache to work
    if filename and filename != "<string>":
        cached = _load_ast_cache(filename, source)
        if cached:
            tree, ctx = cached
            if tree and ctx:
                if verbose:
                    print(f"  [cache] [ok] AST cache hit -- skipping parse")
                return tree, ctx
        elif verbose:
            print(f"  [cache] AST cache miss -- parsing fresh")

    # 0. Symbol table pre-pass -- O(n) scan before Earley runs
    # Collects all declared names so dotted name resolution is
    # unambiguous. Also catches reserved word violations early.
    try:
        from mohio_symbol_table import extract_symbols
        from mohio_transformer import MOHIO_RESERVED_EXACT
        symbol_table = extract_symbols(source)
        # Symbol table warnings always shown (reserved word violations etc)
        for w in symbol_table.warnings:
            print(f"  !  {w}")
        # Pretokenizer marks dotted user-var accesses (e.g. x.text) as a single
        # USERVAR_DOTTED token, so Earley never branches on them (big speedup) and
        # a type-word field name like `.text`/`.int` is preserved instead of losing
        # to the type terminal. Re-enabled after validating: zork_demo.mho (1100
        # lines) checks clean, the gate is 155/155, and a corpus-wide `mio check`
        # adds zero new failures. Central parse path -- fixes check, run, and serve.
        from mohio_pretokenizer import pretokenize
        parse_source = pretokenize(source, symbol_table.all_user_names(), MOHIO_RESERVED_EXACT)
    except ImportError:
        symbol_table = None
        parse_source = source  # graceful degradation

    # 1. Parse pre-tokenized source (or original if pre-tokenizer unavailable)
    try:
        grammar = _load_grammar()
        parser  = _make_parser_cached(grammar)
        tree    = parser.parse(parse_source)
    except Exception as e:
        from lark.exceptions import UnexpectedInput
        if isinstance(e, UnexpectedInput):
            _print_parse_error(e, source, filename)
            sys.exit(1)
        _die(f"Parse failed: {e}", exit_code=1)

    if verbose:
        from mio_utils import tree_depth
        print(dim(f"  Parsed -- tree depth {tree_depth(tree)}"))

    # 2. Validate (Layer 1) -- through the single enforcement door, not a direct validate()
    # call. build_ast=False runs ONLY Layer 1 (parse-tree validation), preserving this helper's
    # contract (it returns tree + validation ctx; AST/scan happen later, interleaved with
    # includes/journey by the caller). enforce() is the sole owner of what Layer 1 enforces.
    from mohio_enforce import enforce as _enforce
    ctx, _ = _enforce(tree, source=source, filename=filename, build_ast=False)
    # AST cache is intentionally NOT written here. This helper runs only Layer 1; a file can pass
    # Layer 1 yet fail AST construction or a whole-program scan, and caching it as "clean" after
    # Layer 1 alone would let the next `mio check` replay a clean result and bypass the failing
    # layer (audit finding #3). The cache is written by the caller AFTER the full pipeline
    # (assemble includes/journey, then enforce_scans) confirms the file is clean through Layer 3.

    # Always show warnings
    if ctx.warnings and verbose:
        for w in ctx.warnings:
            _print_compile_warning(w, source, filename)
    elif ctx.warnings:
        if '--json' not in sys.argv:
            print(yellow(f"  {len(ctx.warnings)} warning(s) -- run mio check for details"))

    return tree, ctx


def _find_include_path(path, base_dir):
    """Resolve an include path: as given (if absolute), then relative to the
    including file's directory, then relative to the current directory.
    Returns an existing file path, or None if not found."""
    cands = []
    if os.path.isabs(path):
        cands.append(path)
    else:
        cands.append(os.path.join(base_dir, path))
        cands.append(os.path.join(os.getcwd(), path))
        cands.append(path)
    for c in cands:
        if os.path.isfile(c):
            return c
    return None


def _resolve_includes(program, including_path, _seen=None, _depth=0, verbose=False):
    """Replace top-level `include "..."` nodes with the parsed + transformed
    statements of the referenced files.

    Each file is parsed as its OWN independent Earley tree (a separate
    _parse_and_validate call) and its AST is merged in -- never source-text
    concatenation -- so total parse cost stays near-linear instead of quadratic
    in the combined length. Include-once and cycle-safe via a shared `_seen` set
    of real paths (the root file is seeded so A->B->A terminates and never
    double-includes). Programs with no includes pay zero extra cost.
    """
    from mohio_ast import IncludeDecl
    from mohio_transformer_ast import transform as _t
    if _seen is None:
        _seen = set()
    if including_path:
        _seen.add(os.path.realpath(os.path.abspath(including_path)))
    if _depth > 40:
        raise RecursionError("include nesting too deep (possible include cycle)")
    if not any(isinstance(s, IncludeDecl) for s in program.statements):
        return program
    base_dir = (os.path.dirname(os.path.abspath(including_path))
                if including_path else os.getcwd())
    new_stmts = []
    for stmt in program.statements:
        if isinstance(stmt, IncludeDecl):
            target = _find_include_path(stmt.path, base_dir)
            if target is None:
                raise FileNotFoundError(
                    f"include: cannot find '{stmt.path}' (looked relative to "
                    f"{base_dir} and the current directory)")
            real = os.path.realpath(target)
            if real in _seen:
                continue  # already included once (duplicate or cycle) -- skip
            _seen.add(real)
            sub_src = open(target, encoding='utf-8').read()
            # An included file was re-parsed on every boot even when a cache existed
            # for it, because this path never looked. On zork that left ~7s of the
            # warm start still being spent parsing the include, against ~0.4s for the
            # same app with no include -- a warm start that looked correct and was
            # eighteen times slower than the cache promised.
            _sub_cached = _load_ast_cache(target, sub_src)
            if _sub_cached and _sub_cached[0] is not None:
                sub_tree, _sub_ctx = _sub_cached
            else:
                sub_tree, _sub_ctx = _parse_and_validate(sub_src, target, verbose)
            sub_prog = _t(sub_tree, sub_src)
            _resolve_includes(sub_prog, target, _seen, _depth + 1, verbose)
            new_stmts.extend(sub_prog.statements)
        else:
            new_stmts.append(stmt)
    program.statements = new_stmts
    return program


def _apply_journey(program, file_path, verbose=False):
    """htaccess-style auto-discovery: if a `journey.mho` exists in the same
    directory as file_path, merge it as the shared 'spine' for every .mho in
    that directory -- no explicit include needed.

    The journey is PREPENDED, so on a name conflict (same task/shape/ai.decide)
    the main file's declaration is processed last and WINS. The journey is the
    default; main always overrides. The journey file is never applied to itself.

    Security note: the journey is purely additive -- it can add shared
    declarations but does not (and must not) disable a page's `sector:`
    enforcement, which is applied independently. A future "block journey on this
    page" control (mirroring htaccess) is deliberately NOT implemented yet so it
    can never become a way to slip past sector security; for now the journey
    applies to every page in the directory.
    """
    from mohio_transformer_ast import transform as _t
    if not file_path:
        return program
    base_dir = os.path.dirname(os.path.abspath(file_path))
    journey_path = os.path.join(base_dir, 'journey.mho')
    if not os.path.isfile(journey_path):
        # A leading underscore marks a file private to routing, and the spine is found
        # by exact name -- so `_journey.mho` is silently not a journey at all. The
        # sector floor, shared connections and compliance settings just stop applying,
        # with nothing printed. That is the one combination worth stopping on, because
        # the failure looks like nothing happening.
        stray = os.path.join(base_dir, '_journey.mho')
        if os.path.isfile(stray):
            _die(f"Found `_journey.mho` in {base_dir}, but no `journey.mho`.\n\n"
                 f"  The spine must be named `journey.mho` exactly. A leading "
                 f"underscore keeps a file out of routing, so `_journey.mho` is never "
                 f"applied to anything -- the sector, shared connections and compliance "
                 f"it declares would silently not be in force.\n\n"
                 f"  To proceed: rename it to `journey.mho`. It is never routable and "
                 f"never served, so it does not need the underscore. If it is not meant "
                 f"to be the spine, give it another name.", exit_code=1)
        return program
    if os.path.realpath(journey_path) == os.path.realpath(os.path.abspath(file_path)):
        return program  # don't apply the journey to itself
    j_src = open(journey_path, encoding='utf-8').read()
    j_tree, _j_ctx = _parse_and_validate(j_src, journey_path, verbose)
    j_prog = _t(j_tree, j_src)
    _resolve_includes(j_prog, journey_path, verbose=verbose)  # journey may include too
    # Prepend: main statements run/register after the journey, so main wins.
    program.statements = j_prog.statements + program.statements
    return program

def _resolve_sqlite_db_path(mho_file, args):
    """Resolve the SQLite path for a CLI run/serve so data does not vanish on exit.

    The pioneer trap: for SQLite the `from` clause never set the path and the CLI never
    set one either, so every run landed in :memory: and was gone the moment the process
    stopped, no matter what the program said. Here the CLI picks a persistent file by
    default, keyed to the program and kept outside the project folder so moving or deleting
    the project does not erase the data. :memory: stays reachable, but only on purpose.

    Order: --memory (explicit throwaway) -> --db PATH -> DATABASE_URL (honors an explicit
    :memory: or a path) -> a persistent file under ~/.mohio/data/. Postgres and other named
    backends are unaffected; they resolve their own URL and ignore this path.
    """
    if getattr(args, 'memory', False):
        return ':memory:'
    explicit = getattr(args, 'db', None) or os.environ.get('DATABASE_URL')
    if explicit:
        return explicit
    import hashlib
    abspath = os.path.abspath(mho_file) if mho_file else 'mio-default'
    stem = os.path.splitext(os.path.basename(abspath))[0] or 'app'
    key = hashlib.sha256(abspath.encode()).hexdigest()[:8]
    data_dir = os.path.join(os.path.expanduser('~'), '.mohio', 'data')
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, f"{stem}-{key}.db")


def cmd_run(args):
    filename = args.file
    verbose  = args.verbose

    # Load request
    request = None
    if args.request_file:
        rpath = Path(args.request_file)
        if not rpath.exists():
            _die(f"Request file not found: {args.request_file}", exit_code=3)
        try:
            request = json.loads(rpath.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            _die(f"Request file is not valid JSON: {e}")
    elif args.request:
        try:
            request = json.loads(args.request)
        except json.JSONDecodeError as e:
            _die(
                f"--request must be valid JSON: {e}\n\n"
                f"  On Windows CMD, use --request-file instead:\n"
                f"    mio run file.mho --request-file request.json\n\n"
                f"  Or use --param for individual fields:\n"
                f"    mio run file.mho --param _shape=Transaction --param amount=500\n"
            )
    elif args.param:
        request = {}
        for p in args.param:
            if "=" not in p:
                _die(f"--param must be key=value, got: {p!r}")
            k, _, v = p.partition("=")
            if v.lstrip("-").isdigit():
                v = int(v)
            elif v.replace(".", "", 1).lstrip("-").isdigit():
                v = float(v)
            elif v.lower() == "true":
                v = True
            elif v.lower() == "false":
                v = False
            elif v.startswith("[") or v.startswith("{"):
                try:
                    v = json.loads(v)
                except json.JSONDecodeError:
                    pass
            request[k] = v

    # Load source
    path = Path(filename)
    if not path.exists():
        _die(f"File not found: {filename}", exit_code=3)
    if path.suffix not in (".mho", ""):
        print(yellow(f"  Warning: expected .mho file, got {path.suffix}"))

    source = _read_source(path, exit_code=3)
    if verbose:
        print(dim(f"\n  Loading {filename} ({len(source.splitlines())} lines)"))

    # -- Parse + validate --------------------------------------
    tree, ctx = _parse_and_validate(source, filename, verbose)

    # Compile errors block execution
    if ctx.errors:
        print(f"\n  {bold(red('Build failed'))}  {dim(filename)}\n")
        for e in ctx.errors:
            _print_compile_error(e, source, filename)
        print(red(f"  {len(ctx.errors)} error(s) -- fix before running.\n"))
        sys.exit(1)

    # -- AST transform (old transformer -- feeds interpreter) ---
    try:
        from mohio_transformer_ast import transform as ast_transform
        program = ast_transform(tree, source)
        program = _resolve_includes(program, filename, verbose=verbose)
        program = _apply_journey(program, filename, verbose=verbose)
        if verbose:
            print(dim(f"  Transformed -- {len(program.statements)} top-level statements"))
    except ImportError:
        # mohio_transformer_ast not yet available -- skip AST step
        # The interpreter will be updated to work directly with the tree
        program = None
        if verbose:
            print(dim("  AST transform skipped (mohio_transformer_ast not found)"))
    except Exception as e:
        _die(f"AST transform failed: {e}", exit_code=1)

    # -- Layer 3: whole-program scanners on the ASSEMBLED program --------------
    # The single enforcement door. `mio check` runs all three layers; without this, `mio run`
    # stopped at Layer 1 and a Layer-3 error (e.g. a field typed with an undeclared shape) would
    # RUN anyway -- check and run could disagree. Layer 3 must see the assembled program (after
    # includes + journey) so it has every declaration, so it runs here, not in _parse_and_validate.
    # Errors block execution exactly as a Layer-1 error does; warnings stay advisory.
    if program is not None:
        try:
            from mohio_enforce import enforce_scans as _enforce_scans
            _enforce_scans(ctx, program)
        except Exception as _scan_err:
            # A scanner hiccup must not crash an otherwise-valid run -- but it must not be
            # SILENT either. A scanner that dies partway has checked some rules and not others,
            # so the program proceeds with an unknown amount of enforcement actually applied.
            # Swallowing that reports "clean" for a program nobody finished checking.
            import sys as _sys
            print(f"  [enforce] WARNING: a Layer 3 scanner failed ({type(_scan_err).__name__}: "
                  f"{_scan_err}). Enforcement for this run is INCOMPLETE -- some checks did not "
                  f"execute. Run `mio check` to see the full result.", file=_sys.stderr)
        if ctx.errors:
            print(f"\n  {bold(red('Build failed'))}  {dim(filename)}\n")
            for e in ctx.errors:
                _print_compile_error(e, source, filename)
            print(red(f"  {len(ctx.errors)} error(s) -- fix before running "
                      f"(run `mio check {filename}` for the full report).\n"))
            sys.exit(1)

    # -- Execute -----------------------------------------------
    if program is None:
        print(yellow("  Execution skipped -- AST transformer not yet wired."))
        print(yellow("  Run  mio check  to validate only.\n"))
        return

    try:
        from mohio_interpreter import MohioInterpreter, MockAiRuntime

        if args.ai or args.api_key:
            try:
                from mohio_ai import AnthropicAiRuntime
            except ImportError:
                _die(
                    "The Anthropic SDK is not installed.\n\n"
                    "  Run:  pip install anthropic\n\n"
                    "  Then retry:  mio run <file> --ai"
                )
            try:
                ai = _construct_ai_runtime(args.api_key, verbose)
                if verbose:
                    print(dim(f"  AI runtime: Anthropic API ({ai._model})"))
            except RuntimeError as e:
                _die(str(e))
        else:
            ai = MockAiRuntime()
            if verbose:
                print(dim("  AI runtime: mock (use --ai for real Anthropic API)"))

        interp = MohioInterpreter(ai=ai, verbose=verbose,
                                  db_path=_resolve_sqlite_db_path(filename, args))

        seed_data = None
        if args.seed:
            seed_path = Path(args.seed)
            if not seed_path.exists():
                _die(f"Seed file not found: {args.seed}", exit_code=3)
            try:
                seed_data = json.loads(seed_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                _die(f"Seed file is not valid JSON: {e}")
            if verbose:
                rows = sum(len(v) for v in seed_data.values())
                print(dim(f"  Seed data: {rows} rows across {list(seed_data.keys())}"))

        # Run declarations first to establish real db connection
        interp.run_declarations(program)
        if seed_data:
            interp.seed_db(seed_data)
        elif not interp._db:
            interp.setup_test_db()
        result = interp.run(program, request=request)

    except Exception as e:
        _cur = 0
        try: _cur = interp._current_line
        except Exception: pass
        _line = getattr(e, "line", 0) or _cur
        _print_runtime_error(e, filename, source=source, line=_line)
        if verbose:
            traceback.print_exc()
        sys.exit(2)

    # Surface `show` output — the program's visible output, in order. Without
    # this, only the final statement's value appears (so a `show` inside a loop
    # looked like it only ran the last iteration).
    shown = getattr(interp, 'shown', None) or []
    for line in shown:
        print(f"  {line}")
    # Echo the Result/Response only when it adds information beyond the show
    # output: a real give-back (dict), or a value-returning program with no show.
    if isinstance(result, dict) or not shown:
        _print_result(result, verbose)
    elif verbose:
        _print_result(result, verbose)


# -- mio serve -----------------------------------------------------------------

def cmd_generate(args=None):
    """
    Generate artifacts from Mohio runtime data.

    Usage:
        mio generate training-data applang [--db path] [--output file.jsonl]
        mio generate training-data applang [--min-hits 2]
    """
    if not args or not hasattr(args, 'artifact'):
        print("  Usage: mio generate training-data applang")
        print("  Exports the applang_map corpus as weighted JSONL for fine-tuning.")
        return

    artifact = getattr(args, 'artifact', '')
    source = getattr(args, 'source', '')

    if artifact == 'training-data' and source == 'applang':
        _generate_applang_training_data(args)
    else:
        print(f"  Unknown artifact: {artifact} {source}")
        print("  Available: mio generate training-data applang")


def _generate_applang_training_data(args):
    """
    Export applang_map as weighted JSONL training data.
    weight = min(1.0, hit_count / max_hit_count)
    Output: { "prompt": input, "completion": canonical,
              "lang": lang_header, "context": context_id,
              "weight": float, "source": "applang_map" }
    """
    import sqlite3, json, os
    from pathlib import Path

    db_path = getattr(args, 'db', None) or os.environ.get('DATABASE_URL', ':memory:')
    output = getattr(args, 'output', None) or 'applang_training_data.jsonl'
    min_hits = getattr(args, 'min_hits', 1)

    # Handle postgres:// style URLs -- use sqlite for now
    if db_path and db_path.startswith('postgres'):
        print("  ! Postgres export not yet supported. Use sqlite DATABASE_URL.")
        return

    if not os.path.exists(db_path) and db_path != ':memory:':
        print(f"  Error: database not found at {db_path}")
        return

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        # Check table exists
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='applang_map'"
        )
        if not cur.fetchone():
            print("  Error: applang_map table not found.")
            print("  Run your Mohio app with an applang block first to build the corpus.")
            return

        # Get max hit count for normalization
        cur = conn.execute("SELECT MAX(hit_count) as max_hits FROM applang_map")
        row = cur.fetchone()
        max_hits = row['max_hits'] if row and row['max_hits'] else 1

        # Export all entries above min_hits
        cur = conn.execute(
            "SELECT input, canonical, lang_header, context_id, "
            "context_category, hit_count, app_version_hash "
            "FROM applang_map WHERE hit_count >= ? "
            "ORDER BY hit_count DESC",
            (min_hits,)
        )
        rows = cur.fetchall()

        if not rows:
            print(f"  No entries found with hit_count >= {min_hits}")
            return

        # Write JSONL
        out_path = Path(output)
        count = 0
        with open(out_path, 'w', encoding='utf-8') as f:
            for row in rows:
                weight = min(1.0, row['hit_count'] / max_hits)
                record = {
                    "prompt":     row['input'],
                    "completion": row['canonical'],
                    "lang":       row['lang_header'] or 'en',
                    "context":    row['context_id'] or '',
                    "weight":     round(weight, 4),
                    "source":     "applang_map",
                    "hit_count":  row['hit_count']
                }
                f.write(json.dumps(record) + '\n')
                count += 1

        # Write metadata
        meta_path = out_path.with_suffix('.meta.json')
        with open(meta_path, 'w') as f:
            json.dump({
                "total_entries": count,
                "max_hit_count": max_hits,
                "min_hits_filter": min_hits,
                "format": "openai-jsonl-v1",
                "source_table": "applang_map",
                "note": "weight = min(1.0, hit_count / max_hit_count)"
            }, f, indent=2)

        print(f"  [generate] {count} training records written to {out_path}")
        print(f"  [generate] metadata written to {meta_path}")
        print(f"  [generate] max hit count: {max_hits} -- weight normalized 0.0-1.0")
        print(f"  [generate] ready for OpenAI, Anthropic, or HuggingFace fine-tuning")

    except Exception as e:
        print(f"  Error: {e}")


def cmd_translate(args=None):
    """
    Translate a .mho source file between human languages.
    
    Usage:
        mio translate --from en --to pt source.mho
        mio translate --from en --to klingon source.mho --output translated.mho
    
    Translates programming language keywords while leaving:
    - String literals unchanged
    - Comments unchanged  
    - Protected namespaces (ai., env., mio., sh., secret.) unchanged
    - Variable names and identifiers unchanged
    """
    import os
    from pathlib import Path

    if not args or not hasattr(args, 'file') or not args.file:
        print("  Usage: mio translate --from <lang> --to <lang> <file.mho>")
        print("  Example: mio translate --from en --to pt tests/fraud_demo.mho")
        return

    source_file = Path(args.file)
    if not source_file.exists():
        print(f"  {red('Error:')} file not found: {args.file}")
        sys.exit(3)

    from_lang = getattr(args, 'from_lang', 'en')
    to_lang = getattr(args, 'to_lang', None)

    if not to_lang:
        print(f"  {red('Error:')} --to language required")
        print("  Example: mio translate --from en --to pt source.mho")
        sys.exit(1)

    # Find maps directory
    maps_dir = os.path.join(os.path.dirname(os.path.abspath(str(source_file))),
                            '..', 'maps')
    if not os.path.isdir(maps_dir):
        maps_dir = str(mohio_data.MAPS_DIR)

    # Load source langmap (for from_lang -> canonical)
    # Load target langmap (for canonical -> to_lang)
    try:
        from mohio_langmap import LangmapLoader
        import glob

        def find_langmap(lang, maps_dir):
            if lang == 'en' or lang == 'english':
                return None  # English IS canonical
            candidates = glob.glob(os.path.join(maps_dir, f'*{lang}*.langmap'))
            if candidates:
                return candidates[0]
            # Try exact match
            exact = os.path.join(maps_dir, f'en-{lang}.langmap')
            if os.path.exists(exact):
                return exact
            return None

        source = _read_source(source_file)
        
        # Step 1: translate from source lang to canonical (English)
        if from_lang not in ('en', 'english'):
            from_map_path = find_langmap(from_lang, maps_dir)
            if not from_map_path:
                print(f"  {yellow('!')} No langmap found for '{from_lang}' -- assuming canonical English")
                canonical = source
            else:
                loader = LangmapLoader(from_map_path)
                canonical = loader.translate(source, direction='backward')
                print(f"  [translate] {from_lang} -> canonical: {from_map_path}")
        else:
            canonical = source

        # Step 2: translate from canonical to target lang
        if to_lang in ('en', 'english'):
            translated = canonical
        else:
            to_map_path = find_langmap(to_lang, maps_dir)
            if not to_map_path:
                print(f"  {red('Error:')} No langmap found for '{to_lang}'")
                print(f"  Available langmaps in {maps_dir}:")
                for lm in glob.glob(os.path.join(maps_dir, '*.langmap')):
                    print(f"    {os.path.basename(lm)}")
                sys.exit(1)
            loader = LangmapLoader(to_map_path)
            translated = loader.translate(canonical, direction='forward')
            print(f"  [translate] canonical -> {to_lang}: {to_map_path}")

        # Add language header if not present
        if '// language:' not in translated[:200]:
            header = f'// language: {to_lang}\n// langmap: maps/en-{to_lang}.langmap\n// Translated from: {source_file.name}\n\n'
            translated = header + translated

        # Write output
        output_path = getattr(args, 'output', None)
        if output_path:
            out = Path(output_path)
        else:
            stem = source_file.stem
            suffix = source_file.suffix
            out = source_file.parent / f'{stem}_{to_lang}{suffix}'

        out.write_text(translated, encoding='utf-8')
        print(f"  [translate] written to: {out}")
        print(f"  [translate] done -- keywords translated to {to_lang}")
        print(f"  [translate] identifiers, strings, and protected namespaces unchanged")

    except ImportError:
        print(f"  {red('Error:')} mohio_langmap module not found")
        sys.exit(1)
    except FileNotFoundError as e:
        print(f"  {red('Error:')} {e}")
        sys.exit(1)



def cmd_warmup(args=None):
    """
    Pre-compile grammar and pre-parse the serve target.
    Run during Docker build to eliminate cold-start delay.
    Usage: python mio.py warmup
    """
    import random
    warmup_messages = [
        "Go grab a coffee -- back in about 20 seconds ?",
        "Teaching the compiler to understand you...",
        "He aha te mea nui? He tangata. (What is the greatest thing? It is people.)",
        "Compiling your intent into reason...",
        "First run takes a moment. Worth it.",
        "The language is waking up. This only happens once.",
        "Mohio: moh-hee-oh. Now you know how to say it.",
        "Warming up the Earley parser. It's worth the wait.",
        "Built at a Dunkin' Donuts drive-thru. Compiled with care.",
        "Understanding is the goal. Compilation is the path.",
    ]
    print(f"  [warmup] {random.choice(warmup_messages)}")
    grammar = _load_grammar()
    parser = _make_parser_cached(grammar)
    print("  [warmup] Grammar compiled and cached.")

    # Pre-parse the serve target during Docker build to create AST cache.
    # This runs during build (no time limit) so the container starts instantly.
    # Build may take 10-20 minutes but that is acceptable.
    # Container startup will be instant on cache hit.
    from pathlib import Path as _Path
    serve_targets = []
    # An explicit target, so a host can warm the app it is actually about to serve.
    # Without one this fell back to four hardcoded names, which meant any app not
    # called app.mho or main.mho got no cache at all and paid the full cold parse on
    # every boot -- the exact case a hosting platform is made of.
    _explicit = getattr(args, 'target', None) if args is not None else None
    if _explicit:
        _t = _Path(_explicit)
        if _t.is_dir():
            # Include targets are warmed too. Skipping them produced a partial cache:
            # the pages were instant and the include was parsed fresh on every boot,
            # which reads as a working warm start while costing most of the time the
            # cache was meant to save.
            serve_targets = [str(f) for f in sorted(_t.rglob('*.mho'))]
            if not serve_targets:
                _die(f"No .mho files found in {_explicit}")
        elif _t.exists():
            serve_targets = [str(_t)]
        else:
            _die(f"File not found: {_explicit}")
    else:
        for candidate in ['tests/zork_demo.mho', 'tests/fraud_demo.mho',
                          'app.mho', 'main.mho']:
            if _Path(candidate).exists():
                serve_targets.append(candidate)

    for target in serve_targets:
        try:
            source = _Path(target).read_text(encoding='utf-8')
            cached = _load_ast_cache(target, source)
            if cached and cached[0] is not None:
                print(f"  [warmup] {target} -- AST cache valid.")
                continue
            print(f"  [warmup] Pre-parsing {target} (build-time only)...")
            try:
                tree, ctx = _parse_and_validate(source, target, verbose=False)
                # Cache only if clean through the FULL pipeline, not just Layer 1. warmup is now
                # the one legitimate cache-writer, so it owns the "clean means clean-through-L3"
                # guarantee: run Layers 2+3 through the door (assembling includes/journey as the
                # check path does) and cache only if nothing failed. Otherwise a warmup could
                # persist an L1-clean/L2-dirty file as "clean" and a later check would replay it.
                from mohio_enforce import enforce as _wenf, enforce_scans as _wenf_scans
                _wctx, _wprog = _wenf(tree, source=source, filename=target, scan=False)
                if _wprog is not None:
                    try:
                        _wprog = _resolve_includes(_wprog, target, verbose=False)
                        _wprog = _apply_journey(_wprog, target, verbose=False)
                    except Exception:
                        _wprog = None
                if _wprog is not None:
                    _wenf_scans(_wctx, _wprog)
                _full_clean = (_wprog is not None
                               and not _ctx_has_errors(ctx)
                               and not _ctx_has_errors(_wctx))
                if _full_clean:
                    _save_ast_cache(target, source, tree, ctx)
                # The message must report what actually happened. It used to be decided
                # by the Layer 1 error count while the WRITE was gated on _full_clean,
                # so a file that failed at Layer 2 or 3 -- or whose includes could not
                # be resolved -- printed "AST cached" having written nothing. A build
                # step that reports success without producing the artifact is worse
                # than one that fails: the slow start shows up in production instead.
                errs = len(ctx.errors) if ctx else 0
                if _full_clean:
                    print(f"  [warmup] {target} -- AST cached.")
                elif errs:
                    print(f"  [warmup] {target} -- NOT cached: {errs} error(s). "
                          f"Run mio check.")
                else:
                    print(f"  [warmup] {target} -- NOT cached: it does not pass a full "
                          f"check (includes, journey, or a later scan). Run mio check "
                          f"on it; only a file that passes cleanly can be cached.")
            except Exception as e:
                print(f"  [warmup] {target} -- parse error: {e}")
        except Exception as e:
            print(f"  [warmup] {target} -- could not read: {e}")

    print("  [warmup] Cold-start delay eliminated.")

def _cmd_serve_directory(args, directory, verbose=False):
    """
    Multi-file directory serve mode.
    Maps .mho files to URL paths automatically:
        index.mho  -> /
        rates.mho  -> /rates
        terms.mho  -> /terms
        about.mho  -> /about
    Files starting with _ are excluded (private/included components).
    Files in subdirectories map to sub-paths:
        blog/index.mho -> /blog/
        blog/post.mho  -> /blog/post
    """
    import os

    print(f"\n  {bold('mio serve')} {dim(f'v{VERSION}')} -- {bold('directory mode')}")
    print(f"  {dim('Scanning')} {bold(str(directory))}")

    # Discover all .mho files
    #   _name.mho  -- private: include target only, never routed
    #   journey.mho -- the spine, auto-applied to every page in its folder the way an
    #                  .htaccess applies to a directory. It is not a page, so it is not
    #                  a route. Listing it as one advertised a URL that answers 404.
    #                  _apply_journey finds it on disk by name, so skipping it here
    #                  does not stop it being applied.
    mho_files = []
    journeys = []
    for root, dirs, files in os.walk(str(directory)):
        # Skip hidden folders
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for f in files:
            if not f.endswith('.mho') or f.startswith('_'):
                continue
            abs_path = os.path.join(root, f)
            if f == 'journey.mho':
                journeys.append(abs_path)
                continue
            rel_path = os.path.relpath(abs_path, str(directory))
            # Build URL path
            url_path = '/' + rel_path.replace(os.sep, '/')
            url_path = url_path[:-4]  # remove .mho
            if url_path.endswith('/index'):
                url_path = url_path[:-6] or '/'  # /blog/index -> /blog/
            if not url_path:
                url_path = '/'
            mho_files.append((url_path, abs_path))

    if not mho_files:
        _die(f"No .mho files found in {directory}")

    print(f"  {dim('Routes discovered:')}")
    for url, filepath in sorted(mho_files):
        rel = os.path.relpath(filepath, str(directory))
        print(f"    {url:<30} {dim(rel)}")
    for jpath in sorted(journeys):
        rel = os.path.relpath(jpath, str(directory))
        folder = os.path.dirname(rel) or '.'
        print(f"    {dim('(spine)'):<39} {dim(rel)} -- applied to every page in "
              f"{folder}")

    # Parse and compile all files
    programs = {}
    interps = {}

    # Set up shared AI runtime
    from mohio_interpreter import MohioInterpreter, MockAiRuntime
    if args.ai:
        try:
            from mohio_ai import AnthropicAiRuntime
            ai = _construct_ai_runtime(args.api_key, verbose)
        except ImportError:
            _die("Anthropic SDK not installed. Run: pip install anthropic")
        except RuntimeError as e:
            # A missing key raises RuntimeError. Without this the traceback escapes
            # and directory mode looks like a compiler crash, while single-file serve
            # prints a clean message for the same mistake. Asking for --ai and getting
            # the mock silently would be worse than not starting: the app would serve
            # invented AI answers as if they were real.
            _die(str(e))
    else:
        ai = MockAiRuntime()

    for url_path, filepath in mho_files:
        try:
            # Read inside the try so an unreadable page (not UTF-8, vanished, etc.)
            # is reported per-file and skipped like a parse error -- one bad page must
            # not crash the whole directory server with a raw traceback (Unit B).
            source = Path(filepath).read_text(encoding='utf-8')
            tree, ctx = _parse_and_validate(
                source, filepath, verbose=False)
            if ctx.errors:
                print(f"  {yellow('!')} Skipping {filepath} -- parse errors")
                continue
            from mohio_transformer_ast import transform
            program = transform(tree, source)
            program = _resolve_includes(program, filepath, verbose=False)
            program = _apply_journey(program, filepath, verbose=False)
            # Single door: Layer-3 scan on the assembled program before this route goes live.
            # A file that fails check/run must not be served just because it is in a directory.
            try:
                from mohio_enforce import enforce_scans as _enforce_scans
                _enforce_scans(ctx, program)
            except Exception as _scan_err:
                # A crashing scanner must not kill an otherwise-valid command, but it must not be
                # silent: enforcement for this run is INCOMPLETE and the program proceeds anyway.
                import sys as _sys
                print(f"  [enforce] WARNING: a Layer 3 scanner failed "
                      f"({type(_scan_err).__name__}: {_scan_err}). Enforcement is INCOMPLETE -- "
                      f"some checks did not execute. Run `mio check` for the full result.",
                      file=_sys.stderr)
            if ctx.errors:
                print(f"  {red('x')}  {url_path} -- {len(ctx.errors)} error(s), skipped "
                      f"(run `mio check {filepath}`)")
                continue
            interp = MohioInterpreter(ai=ai, verbose=verbose,
                                      db_path=_resolve_sqlite_db_path(filepath, args))
            interp.run_declarations(program)
            programs[url_path] = program
            interps[url_path] = interp
            print(f"  {green('v')}  {url_path}")
        except Exception as e:
            print(f"  {red('x')}  {url_path} -- {e}")

    if not programs:
        _die("No files compiled successfully.")

    # Build multi-route FastAPI app
    try:
        from mohio_server import create_multi_app
        app = create_multi_app(programs, interps, verbose=verbose, app_dir=directory)
    except (ImportError, AttributeError):
        # Fallback: build basic multi-route app inline
        try:
            from fastapi import FastAPI, Request
            from fastapi.responses import JSONResponse, HTMLResponse, PlainTextResponse, Response
            import uvicorn

            app = FastAPI(title="Mohio Directory Server")

            @app.get("/mio/health")
            async def health():
                return {"status": "ok", "mode": "directory",
                        "routes": list(programs.keys())}

            # Register a handler for each .mho file
            for url_path, program in programs.items():
                interp = interps[url_path]

                # Create closure to capture url_path, program, interp
                def make_handler(p, prog, interpr):
                    async def handler(request: Request):
                        body = {}
                        try:
                            body = await request.json()
                        except Exception:
                            pass
                        result = interpr.handle_request(prog, {
                            "method":  request.method,
                            "path":    str(request.url.path),
                            "headers": dict(request.headers),
                            "body":    body,
                            "query":   dict(request.query_params),
                        })
                        status = result.get("status", 200) if result else 200
                        body_out = result.get("body", "") if result else ""
                        content_type = result.get("content_type",
                                                   "application/json") if result else "text/plain"
                        def _to_xml(data, root="response"):
                            import xml.sax.saxutils as _sx
                            def _el(tag, val):
                                tag = str(tag)
                                if isinstance(val, dict):
                                    return "<%s>%s</%s>" % (tag, "".join(_el(k, v) for k, v in val.items()), tag)
                                if isinstance(val, (list, tuple)):
                                    it = tag[:-1] if (tag.endswith("s") and len(tag) > 1) else "item"
                                    return "<%s>%s</%s>" % (tag, "".join(_el(it, v) for v in val), tag)
                                return "<%s>%s</%s>" % (tag, _sx.escape("" if val is None else str(val)), tag)
                            return '<?xml version="1.0" encoding="UTF-8"?>' + _el(root, data)
                        if "html" in content_type:
                            return HTMLResponse(str(body_out), status_code=status)
                        elif "xml" in content_type:
                            return Response(_to_xml(body_out),
                                            status_code=status,
                                            media_type=content_type,
                                            headers={"Cache-Control": "no-store"})
                        elif "plain" in content_type:
                            return PlainTextResponse(str(body_out),
                                                     status_code=status,
                                                     media_type=content_type)
                        # JSON path: never emit None/empty (a client calling
                        # response.json() throws on it) and coerce any
                        # non-serializable value (datetime, MohioValue) via str().
                        if body_out is None:
                            body_out = ""
                        import json as _json
                        payload = body_out if isinstance(body_out, dict) else {"message": str(body_out)}
                        return Response(
                            content=_json.dumps(payload, ensure_ascii=False,
                                                allow_nan=False, default=str),
                            status_code=status,
                            media_type="application/json",
                            headers={"Cache-Control": "no-store"},
                        )
                    return handler

                # Map exact URL
                route = url_path if url_path != '/' else '/'
                app.add_api_route(route, make_handler(url_path, program, interp),
                                  methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
                # Also handle trailing slash variant
                if route != '/' and not route.endswith('/'):
                    app.add_api_route(route + '/', make_handler(url_path, program, interp),
                                      methods=["GET", "POST", "PUT", "DELETE", "PATCH"])

            print(f"\n  {green('v')}  {len(programs)} routes loaded")
            print(f"  {bold('Listening on')}  http://{args.host}:{args.port}")
            print(f"  {bold('Health')}        "
                  f"http://{args.host}:{args.port}/mio/health")
            print(f"\n  {dim('Press Ctrl+C to stop')}\n")

            uvicorn.run(app, host=args.host, port=args.port,
                        log_level="warning")
        except ImportError as e:
            _die(f"FastAPI/uvicorn required for serve: {e}")
    else:
        # create_multi_app built the app but nothing launched it -- the directory
        # branch fell through and the process exited, so `mio serve <dir>` never
        # served (single-file mode was unaffected because it launches separately).
        # Launch the built app exactly like the single-file / fallback paths do.
        try:
            import uvicorn
        except ImportError:
            _die("uvicorn not installed. Run: pip install uvicorn")
        print(f"\n  {green('v')}  {len(programs)} routes loaded")
        print(f"  {bold('Listening on')}  http://{args.host}:{args.port}")
        print(f"  {bold('Health')}        http://{args.host}:{args.port}/mio/health")
        print(f"\n  {dim('Press Ctrl+C to stop')}\n")
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")



def cmd_serve(args):
    """
    Start an HTTP server for a Mohio program.
    Parses, validates, and transforms once at startup.
    Handles all requests with a persistent interpreter instance.
    """
    filename = args.file
    port     = args.port
    host     = args.host
    verbose  = args.verbose

    path = Path(filename)
    if not path.exists():
        _die(f"File not found: {filename}", exit_code=3)

    # ── Multi-file directory mode ──────────────────────────────
    # mio serve myapp/ maps .mho files to URLs automatically:
    #   index.mho -> /
    #   rates.mho -> /rates
    #   terms.mho -> /terms
    # This is the ColdFusion/PHP mental model -- one file per page.
    if path.is_dir():
        _cmd_serve_directory(args, path, verbose)
        return

    source = _read_source(path)

    print(f"\n  {bold('mio serve')}  {dim(f'v{VERSION}')}")
    print(f"  {dim('Loading')} {bold(filename)}")

    # -- Fast mode check ------------------------------------------
    fast_mode = getattr(args, 'fast', False)
    if fast_mode:
        from mohio_symbol_table import extract_symbols, check_reserved_violations
        from mohio_transformer import MOHIO_RESERVED_EXACT, MOHIO_RESERVED_WHAT
        st = extract_symbols(source)
        violations = check_reserved_violations(st)
        if violations:
            for name, what in violations:
                print(f"  x '{name}' is reserved -- it is {what}.")
            print(f"  x {filename} -- {len(violations)} reserved word violation(s)")
            sys.exit(1)
        print(f"  v {filename} -- fast check passed (ASCII + reserved words)")
        return

    # -- Parse: AST cache first ------------------------------------
    # If this is the first run or cache is stale, Earley parses the file.
    # This is slow on large files. We start uvicorn first so Railway
    # healthcheck passes, then serve requests once parsing completes.
    _cached = _load_ast_cache(filename, source)
    if _cached and _cached[0] is not None and _cached[1] is not None:
        tree, ctx = _cached
        print(f"  [parser] AST cache hit -- loaded instantly.")
    else:
        tree, ctx = _parse_and_validate(source, filename, verbose=False)

    if ctx.errors:
        print(f"\n  {red('x')} Build failed -- fix errors before serving:\n")
        for e in ctx.errors:
            _print_compile_error(e, source, filename)
        sys.exit(1)

    if ctx.warnings:
        if '--json' not in sys.argv:
            print(yellow(f"  {len(ctx.warnings)} warning(s) -- run mio check for details"))

    # -- AST transform -----------------------------------------
    try:
        from mohio_transformer_ast import transform
        program = transform(tree, source)
        program = _resolve_includes(program, filename, verbose=verbose)
        program = _apply_journey(program, filename, verbose=verbose)
        print(f"  {dim('Transformed --')} {len(program.statements)} top-level statements")
    except Exception as e:
        _die(f"Transform failed: {e}")

    # -- Layer 3: whole-program scanners on the assembled program ---------------
    # Same single door as run/check. Without this, a Layer-3 error (e.g. a field typed with an
    # undeclared shape) that blocks `mio run` and `mio check` would still SERVE -- a
    # compliance-violating program going live over HTTP. Serve must not enforce fewer rules than
    # run. Errors block startup; a scanner hiccup must never take down an otherwise-valid serve.
    if program is not None:
        try:
            from mohio_enforce import enforce_scans as _enforce_scans
            _enforce_scans(ctx, program)
        except Exception as _scan_err:
            # A crashing scanner must not kill an otherwise-valid command, but it must not be
            # silent: enforcement for this run is INCOMPLETE and the program proceeds anyway.
            import sys as _sys
            print(f"  [enforce] WARNING: a Layer 3 scanner failed "
                  f"({type(_scan_err).__name__}: {_scan_err}). Enforcement is INCOMPLETE -- "
                  f"some checks did not execute. Run `mio check` for the full result.",
                  file=_sys.stderr)
        if ctx.errors:
            print(f"\n  {red('x')} Build failed -- fix errors before serving:\n")
            for e in ctx.errors:
                _print_compile_error(e, source, filename)
            print(red(f"  {len(ctx.errors)} error(s) -- run `mio check {filename}` "
                      f"for the full report.\n"))
            sys.exit(1)

    # -- Set up interpreter ------------------------------------
    try:
        from mohio_interpreter import MohioInterpreter, MockAiRuntime

        if args.ai:
            try:
                from mohio_ai import AnthropicAiRuntime
                ai = _construct_ai_runtime(args.api_key, verbose)
                print(f"  {dim('AI runtime:')} Anthropic API ({ai._model})")
            except ImportError:
                _die("Anthropic SDK not installed. Run: pip install anthropic")
            except RuntimeError as e:
                _die(str(e))
        else:
            ai = MockAiRuntime()
            print(f"  {dim('AI runtime:')} mock {dim('(use --ai for real Anthropic API)')}")

        interp = MohioInterpreter(ai=ai, verbose=verbose,
                                  db_path=_resolve_sqlite_db_path(filename, args))

        # Seed data
        seed_data = None
        if args.seed:
            seed_path = Path(args.seed)
            if not seed_path.exists():
                _die(f"Seed file not found: {args.seed}", exit_code=3)
            seed_data = json.loads(seed_path.read_text(encoding="utf-8"))
            rows = sum(len(v) for v in seed_data.values() if isinstance(v, list))
            print(f"  {dim('Seed data:')} {rows} rows across {list(seed_data.keys())}")

        # Run declarations first to establish real db connection
        interp.run_declarations(program)
        if seed_data:
            interp.seed_db(seed_data)
        elif not interp._db:
            interp.setup_test_db()

    except Exception as e:
        _die(f"Interpreter setup failed: {e}")

    # -- Build server ------------------------------------------
    try:
        from mohio_server import MohioServer, create_app
    except ImportError:
        _die("mohio_server.py not found in compiler directory.")

    # Static files are served from the APP's directory, never the compiler's.
    server = MohioServer(program, interp, verbose=verbose,
                         app_dir=Path(filename).resolve().parent)
    app    = create_app(server)

    # -- Start -------------------------------------------------
    print(f"\n  {green('v')}  Server ready")
    print(f"  {bold('Listening on')}  http://{host}:{port}")
    print(f"  {bold('API docs')}      http://{host}:{port}/mio/docs")
    print(f"  {bold('Health')}        http://{host}:{port}/mio/health")
    print(f"\n  {dim('Press Ctrl+C to stop')}\n")

    try:
        import uvicorn
        # timeout_graceful_shutdown=0 ensures Railway health checks pass quickly
        uvicorn.run(app, host=host, port=port, log_level="warning")
    except ImportError:
        _die("uvicorn not installed. Run: pip install uvicorn")
    except KeyboardInterrupt:
        print(f"\n\n  {dim('Server stopped.')}\n")

def _run_security_report(source, filename, ctx):
    """
    Full security and compliance report for mio check --security.
    Covers: hardcoded credentials, sector floors, agent limits,
    security: off debt, taint flows, visibility conflicts.
    Each check adds to ctx.errors or ctx.warnings.
    """
    import re
    lines = source.splitlines()

    print(f"\n  {bold('mio check --security')}  {dim(filename)}\n")

    checks_run   = []
    notices      = []

    # -- Check 1: HARDCODED_CREDENTIAL (already in _scan_source / _v_connect_decl)
    hc_errors = [e for e in (ctx.errors or []) if 'HARDCODED_CREDENTIAL' in str(e)]
    checks_run.append(("Hardcoded credentials", len(hc_errors) == 0,
                       f"{len(hc_errors)} found" if hc_errors else "none found"))

    # -- Check 2: security: off without reason/expires
    sec_off_lines = [(i+1, l.strip()) for i,l in enumerate(lines)
                     if re.match(r"\s*security\s*:\s*off", l)]
    for lineno, line in sec_off_lines:
        # Look ahead for reason and expires within 5 lines
        block = "\n".join(lines[lineno:min(lineno+5, len(lines))])
        has_reason  = 'reason' in block
        has_expires = 'expires' in block
        if not has_reason or not has_expires:
            missing = []
            if not has_reason:  missing.append('reason')
            if not has_expires: missing.append('expires')
            ctx.error(
                f"SECURITY_DEBT_UNDOCUMENTED: security: off at line {lineno} "
                f"missing {' and '.join(missing)}. "
                f"security: off requires both reason and expires.",
                lineno,
                hint="Add: reason \"Why this is off\" and expires \"YYYY-MM-DD\""
            )
            notices.append(f"  {red('x')} security: off at line {lineno} -- missing {', '.join(missing)}")
        else:
            notices.append(f"  {yellow('!')} security: off at line {lineno} -- documented, check expiry")
    checks_run.append(("security: off documented", len(sec_off_lines) == 0 or
                       not any('SECURITY_DEBT' in str(e) for e in ctx.errors),
                       f"{len(sec_off_lines)} declaration(s)"))

    # -- Check 3: ai.agent without limits (MISSING_AGENT_LIMITS)
    agent_blocks = [(i+1, l) for i,l in enumerate(lines)
                    if re.match(r"\s*ai\.agent\s+", l)]
    for lineno, line in agent_blocks:
        # Look for limits: done within 20 lines
        block = "\n".join(lines[lineno:min(lineno+20, len(lines))])
        if 'limits' not in block:
            ctx.error(
                f"MISSING_AGENT_LIMITS: ai.agent block at line {lineno} "
                f"has no limits block. Every ai.agent must declare "
                f"max steps, max cost, or timeout.",
                lineno,
                hint="Add: limits\n    max steps 10\n    max cost 0.50\nlimits: done"
            )
    checks_run.append(("ai.agent resource limits", not any(
        'MISSING_AGENT_LIMITS' in str(e) for e in ctx.errors),
        f"{len(agent_blocks)} agent(s) found"))

    # -- Check 4: sec.non_critical audit notices -- valid (reasoned) exemptions only.
    # A reason is required; this notice enumerates the exemptions actually in effect so
    # an auditor can list them with one command. Bare sec.non_critical is an error
    # (SEC_NONCRITICAL_NO_REASON, raised in Check 5), not an exemption, so it is not
    # listed here. Shares the noncritical_status rule with the validator (no drift).
    from mohio_transformer import noncritical_status as _nc_status
    non_critical = []
    for i, l in enumerate(lines):
        if 'sec.non_critical' not in l:
            continue
        _, has_reason = _nc_status("\n".join(lines[i:i+3]))
        if has_reason:
            non_critical.append(i+1)
    for lineno in non_critical:
        notices.append(f"  {yellow('!')} sec.non_critical at line {lineno} "
                       f"-- non-regulatory exemption; sector floor bypassed, reason logged (audit notice)")
    checks_run.append(("sec.non_critical overrides",
                       True,  # not an error -- audit notice only
                       f"{len(non_critical)} reasoned exemption(s)"))

    # -- Check 5: sector floor check (basic)
    # Floor comes from the loaded profile (one source of truth), so ANY sector
    # with a confidence floor -- financial, healthcare, or a custom/licensed
    # profile -- emits SECTOR_VIOLATION consistently. No hardcoded sector list.
    sector = getattr(ctx, 'sector', None)
    floor = None
    if sector:
        try:
            from mohio_sector_loader import get_sector_profile
            floor = getattr(get_sector_profile(sector), 'default_confidence_floor', None)
        except Exception:
            floor = None
    if sector and floor:
        from mohio_transformer import noncritical_status
        # Look for ai.decide blocks with confidence below sector minimum
        for i, line in enumerate(lines):
            m = re.search(r"confidence\s+above\s+([0-9.]+)", line)
            if m:
                val = float(m.group(1))
                if val < floor:
                    # Same exemption rule the validator uses (shared helper, no drift):
                    # sec.non_critical is a valid exemption ONLY with a reason; the
                    # reason is logged for audit. Bare sec.non_critical is an error.
                    block_ctx = "\n".join(lines[max(0,i-5):i+10])
                    present, has_reason = noncritical_status(block_ctx)
                    if present and has_reason:
                        pass  # justified non-regulatory exemption -- logged as an audit notice
                    elif present:
                        ctx.error(
                            f"SEC_NONCRITICAL_NO_REASON: sec.non_critical near line {i+1} "
                            f"requires a reason. Every override must be justified and is logged.",
                            i+1,
                            hint='Add: sec.non_critical reason "why this decision is non-regulatory"'
                        )
                    else:
                        ctx.error(
                            f"SECTOR_VIOLATION: confidence {val} at line {i+1} is below "
                            f"sector:{sector} minimum {floor}. "
                            f'If this decision is non-regulatory, add sec.non_critical reason "...".',
                            i+1,
                            hint=f'Raise confidence to {floor} or add sec.non_critical reason "..."'
                        )
        _floor_clean = not any(('SECTOR_VIOLATION' in str(e) or 'SEC_NONCRITICAL_NO_REASON' in str(e))
                               for e in ctx.errors)
        checks_run.append((f"sector:{sector} confidence floors",
                          _floor_clean,
                          f"floor: {floor}"))

    # -- Check 6 REMOVED (2026-08-01): it warned that `ai.agent NAME` and `mioschedule NAME`
    # blocks "will not run -- executor is Phase 2". Both are now BUILT and tested: the
    # mioschedule declaration registers and fires (`run mioschedule.NAME now` -> the task runs;
    # test_mioschedule.py 5/5), and ai.agent runs with the tool-grant layer (test_agent_tools.py
    # 8/8; this very report also enforces ai.agent's limits/not-confident contract, which it would
    # not do for an unbuilt block). The warning told developers working code was broken. A
    # GENUINELY unbuilt construct is still caught accurately elsewhere -- the interpreter's
    # "parsed and validated, but is not executable in this build" check-time warning (e.g. the
    # `mioschedule.every` statement) and the runtime "no executor for X" error -- so removing this
    # hardcoded, now-false check loses no real coverage.

    # -- Check 7: Cursor pagination without order_by -- ambiguous
    cursor_lines = [(i+1, l.strip()) for i, l in enumerate(lines)
                    if re.match(r'\s*cursor\s+from\s+', l)]
    for lineno, cline in cursor_lines:
        # Look back up to 10 lines for order clause
        block_lines = lines[max(0, lineno-10):lineno]
        has_order = any(re.search(r'order\.(up|down)\s+by\s+\w+', bl) for bl in block_lines)
        if not has_order:
            notices.append(f"  {yellow('!')} cursor pagination at line {lineno} has no order.up/order.down -- "
                           f"cursor field defaults to 'id'. Add order.up by [field] for explicit cursor ordering.")
    checks_run.append(("Cursor pagination order",
                       True,  # warning only
                       f"{len(cursor_lines)} cursor block(s)" if cursor_lines else "none"))

    # -- Check 8: Schema field validation (if .mhoschema exists)
    try:
        from mohio_schema import find_schema_file, read_schema, validate_field_references
        schema_path = find_schema_file(filename)
        if schema_path:
            schema = read_schema(schema_path)
            schema_errors = validate_field_references(source, schema, filename)
            for code, msg, lineno in schema_errors:
                ctx.error(f"{code}: {msg}", lineno, hint="Check your shape declaration and field names.")
            checks_run.append(("Schema field references",
                               len(schema_errors) == 0,
                               f"{schema_path.name}"))
        else:
            checks_run.append(("Schema field references",
                               True,
                               "no .mhoschema found -- run: mio schema generate"))
    except ImportError:
        pass

    # Print security report
    # -- Check 9: Debug declaration warning
    # If a journey block has no debug declaration, suggest adding one.
    # This is purely informational -- never an error.
    journey_lines = [(i+1, l.strip()) for i, l in enumerate(lines)
                     if re.match(r'journey\s+\w+', l.strip())]
    no_debug_journeys = []
    for lineno, jline in journey_lines:
        # Look ahead up to 30 lines for a debug declaration
        ahead = lines[lineno:min(lineno+30, len(lines))]
        has_debug = any(re.match(r'debug\s+(on|off|minimal|verbose)', al.strip())
                        for al in ahead)
        has_closer = any(re.match(r'journey:\s*done', al.strip()) for al in ahead)
        if not has_debug and has_closer:
            name_match = re.match(r'journey\s+(\w+)', jline)
            jname = name_match.group(1) if name_match else 'unknown'
            no_debug_journeys.append((lineno, jname))
    if no_debug_journeys:
        for lineno, jname in no_debug_journeys:
            notices.append(
                f"  {yellow('!')} journey '{jname.replace(chr(95), chr(32))}' has no debug declaration.\n"
                f"    Add {yellow('debug on')} inside the journey for execution traces in mohiolog/\n"
                f"    or {yellow('debug off')} to silence this notice."
            )
    checks_run.append(("Debug declarations",
                       True,  # always warning only
                       f"{len(no_debug_journeys)} journey(s) without debug declaration"
                       if no_debug_journeys else "all journeys declared"))

    for check_name, passed, detail in checks_run:
        icon = green("v") if passed else red("x")
        print(f"  {icon}  {check_name:<40} {dim(detail)}")

    if notices:
        print(f"\n  {yellow('Notices:')}")
        for n in notices:
            print(n)

    print()


def _scan_incomplete_warn(scan_name, err):
    """An advisory Layer-3 scan or compile-time guard crashed during `mio check`.

    Design: a scanner hiccup must never break `mio check` (these passes stay
    advisory) -- but it must never be SILENT either. A swallowed crash reports a
    clean result for a program nobody finished checking, which is the exact
    silent-wrongness the language is built to refuse. Warn on stderr, name the
    scan that died, and let check continue so the rest of the report still runs.
    """
    print(f"  [enforce] WARNING: {scan_name} did not finish "
          f"({type(err).__name__}: {err}). Enforcement for this check is "
          f"INCOMPLETE -- some rules did not execute. This is a compiler bug; "
          f"please report it.", file=sys.stderr)


def _check_never_store(program):
    """Compile-time PCI/PII guard: a `save` must never persist a field declared
    `never store` (e.g. a card CVV). Returns a list of (message, line, hint) tuples
    so it can be surfaced as check errors before the program ever runs."""
    never = set()
    seen = set()
    def collect(node):
        if id(node) in seen:
            return
        seen.add(id(node))
        if node.__class__.__name__ == 'ShapeDecl':
            for fld in (getattr(node, 'fields', None) or []):
                mods = getattr(fld, 'modifiers', []) or []
                if any(getattr(m, 'modifier_type', None) == 'never_store' for m in mods):
                    never.add(fld.name)
        for a in getattr(node, '__dict__', {}).values():
            if isinstance(a, list):
                for x in a:
                    if hasattr(x, '__dict__'):
                        collect(x)
            elif hasattr(a, '__dict__'):
                collect(a)
    collect(program)
    errors = []
    seen2 = set()
    def scan(node):
        if id(node) in seen2:
            return
        seen2.add(id(node))
        if node.__class__.__name__ == 'SaveBlock':
            for fv in (getattr(node, 'fields', None) or []):
                if getattr(fv, 'name', None) in never:
                    errors.append((
                        f"'{fv.name}' is declared `never store` and cannot be saved. "
                        f"PCI/PII rules forbid persisting it (e.g. a card CVV).",
                        getattr(node, 'line', 0) or 0,
                        f"Use {fv.name} where it's needed, but remove it from the save block."))
        for a in getattr(node, '__dict__', {}).values():
            if isinstance(a, list):
                for x in a:
                    if hasattr(x, '__dict__'):
                        scan(x)
            elif hasattr(a, '__dict__'):
                scan(a)
    scan(program)
    return errors


def cmd_check(args):
    """
    Parse and validate -- no execution.
    Shows all errors and warnings with line numbers and hints.
    Run with --security for full security and compliance report.
    Run with --all to check every .mho file in the project.
    Run with --langmap to list every keyword the file's langmap does not map.
    Exit 0 = clean (may have warnings). Exit 1 = errors found.
    """
    # --langmap: print the full unmapped-keyword list instead of the first five.
    globals()['_LANGMAP_FULL_LIST'] = bool(getattr(args, 'langmap', False))

    # --all mode: check every .mho file in directory tree
    if getattr(args, 'all', False):
        import glob
        files = sorted(glob.glob('**/*.mho', recursive=True))
        if not files:
            print("  No .mho files found.")
            return
        fast = getattr(args, 'fast', False)
        mode = "--fast" if fast else "full"
        print(f"  [mio check] Checking {len(files)} .mho file(s) [{mode}]...")
        failed = []
        for f in files:
            args.file = f
            try:
                cmd_check(args)
            except SystemExit as e:
                if e.code != 0:
                    failed.append(f)
        if failed:
            print(f"  x {len(failed)} file(s) failed:")
            for f in failed:
                print(f"    {f}")
            sys.exit(1)
        print(f"  v All {len(files)} files passed.")
        return

    filename = args.file
    if not filename:
        _die("Specify a file or use --all to check all .mho files.", exit_code=3)
    path = Path(filename)

    if not path.exists():
        _die(f"File not found: {filename}", exit_code=3)

    # Directory mode: `mio check myapp/` validates every .mho in the tree -- the same
    # set `mio serve myapp/` serves (files starting with _ are components, excluded).
    # The deploy validation step checks the directory, so this must match the serve
    # scan. Without this branch a directory path fell through to read_text() and
    # crashed with IsADirectoryError.
    if path.is_dir():
        import os as _os
        mho = []
        for _root, _dnames, _fnames in _os.walk(str(path)):
            _dnames[:] = [d for d in _dnames if not d.startswith('.')]
            for f in _fnames:
                if f.endswith('.mho') and not f.startswith('_'):
                    mho.append(_os.path.join(_root, f))
        # Include targets (_name.mho) are never routed, so they are not checked as
        # pages. They were skipped ENTIRELY, which made "All N files passed" a claim
        # about a folder containing a file nobody had looked at: a syntax error in one
        # only surfaced when something included it, or at runtime.
        # They are parsed here but not scanned. A fragment legitimately leans on the
        # file that includes it -- a shape, a variable declared there -- so running the
        # semantic scans standalone would invent errors that are not real. Parsing is
        # context-free, so a syntax error is a syntax error wherever the file sits.
        private = []
        for _root, _dnames, _fnames in _os.walk(str(path)):
            _dnames[:] = [d for d in _dnames if not d.startswith('.')]
            for f in _fnames:
                if f.endswith('.mho') and f.startswith('_'):
                    private.append(_os.path.join(_root, f))
        if not mho and not private:
            _die(f"No .mho files found in {filename}", exit_code=3)
        if not mho:
            _die(f"Only include targets (_name.mho) found in {filename}. "
                 f"Nothing here can be served.", exit_code=3)
        print(f"  [mio check] Checking {len(mho)} .mho file(s) in {bold(str(path))}...")
        failed = []
        for f in sorted(mho):
            args.file = f
            try:
                cmd_check(args)
            except SystemExit as e:
                if e.code not in (0, None):
                    failed.append(f)
        private_failed = []
        for f in sorted(private):
            # Only a genuine parse failure counts. A bare `except Exception` here
            # turned a NameError in this very loop into a "failed" verdict on a
            # perfectly good file -- a checker that invents failures is worse than one
            # that misses them, so anything unexpected is re-raised rather than
            # reported as the file's fault.
            try:
                _parse_and_validate(Path(f).read_text(encoding="utf-8"), f,
                                    verbose=False)
            except SystemExit as e:
                if e.code not in (0, None):
                    private_failed.append(f)
            except (SyntaxError, UnicodeDecodeError, OSError):
                private_failed.append(f)
            except Exception as e:
                if e.__class__.__module__.startswith('lark'):
                    private_failed.append(f)
                else:
                    raise
        if failed or private_failed:
            _n = len(failed) + len(private_failed)
            print(f"  {red('x')} {_n} file(s) failed:")
            for f in failed:
                print(f"    {f}")
            for f in private_failed:
                print(f"    {f}  (include target -- syntax)")
            sys.exit(1)
        print(f"  {green('v')} All {len(mho)} files passed.")
        if private:
            print(f"  {dim(f'  plus {len(private)} include target(s) parsed. They are '
                          f'checked in full through the files that include them.')}")
        return

    source = _read_source(path)
    n_lines = len(source.splitlines())

    # Fun message for large files -- mio check is thorough, not fast
    import random
    check_messages = [
        "Checking your code... this is thorough, not fast. Go grab a coffee. :-)",
        "mio check is doing real work here. Earley parsers don't rush.",
        "Analyzing {n} lines of Mohio... might be a good time to stretch.",
        "Running full compliance and security analysis. Back in a moment.",
        "The compiler is reading every word. Give it a second.",
    ]
    msg = random.choice(check_messages)
    if '{n}' in msg:
        n_lines = len(source.splitlines())
        msg = msg.replace('{n}', str(n_lines))
    print(f"  [mio check] {msg}")

    # Load .mioconfig if present -- args override config
    security_mode = getattr(args, 'security', False)
    try:
        import json as _json
        config_path = Path(".mioconfig")
        if config_path.exists():
            cfg = _json.loads(config_path.read_text())
            if not security_mode:
                security_mode = cfg.get("check", {}).get("security", False)
    except Exception:
        pass  # Config load failure is non-fatal

    json_mode = getattr(args, 'json', False)

    # Parse + validate
    # --fast mode: ASCII + reserved words only, skip full Earley parse
    fast_mode = getattr(args, 'fast', False)
    if fast_mode:
        from mohio_symbol_table import extract_symbols, check_reserved_violations
        from mohio_transformer import MOHIO_RESERVED_EXACT, MOHIO_RESERVED_WHAT
        st = extract_symbols(source)
        violations = check_reserved_violations(st)
        if violations:
            for name, what in violations:
                print(f"  x '{name}' is reserved -- it is {what}.")
            print(f"  x {filename} -- {len(violations)} reserved word violation(s)")
            sys.exit(1)
        print(f"  v {filename} -- fast check passed (ASCII + reserved words)")
        print(f"    Run without --fast for full parse and compliance check.")
        return

    # Check AST cache first -- if file unchanged, skip the full parse
    # First check is slow. Every subsequent check on unchanged file is instant.
    _cached = _load_ast_cache(filename, source)
    if _cached and _cached[0] is not None and _cached[1] is not None:
        tree, ctx = _cached
        print(f"  [mio check] Cache hit -- loaded instantly.")
    else:
        tree, ctx = _parse_and_validate(source, filename, verbose=False)

    # Unreachable-code check -- needs the AST (clean statement lists), so it runs
    # here rather than in the Lark-tree validator. Appended after parse/validate so
    # it is never written into the AST cache (runs exactly once per check, no dupes).
    # Guarded: a hiccup in this advisory pass must never break `mio check`.
    # AST transform -- surfaces real compile-time errors the Lark-tree validator does
    # NOT catch (closer mismatch, invalid retrieve modifier, retired keyword, etc.).
    # These must become check errors, not be silently swallowed. The reachability /
    # typo scans below stay advisory (a hiccup there must never break `mio check`).
    _program = None
    try:
        from mohio_transformer_ast import MohioError, MohioCompileError
        from mohio_enforce import enforce as _enforce, enforce_scans as _enforce_scans
        # Layer 2 (AST construction) THROUGH THE DOOR. scan=False so we can assemble the program
        # (includes + journey spine) before Layer 3 sees it -- the scanners need every
        # declaration across files. A fresh enforce() with build_ast rebuilds Layer 1 into a new
        # ctx too; we take its AST and fold its errors into the existing ctx so nothing is lost or
        # double-counted.
        _l12_ctx, _program = _enforce(tree, source=source, filename=filename, scan=False)
        # fold any Layer-2 errors/warnings the fresh ctx found that ours does not already have
        _seen = {str(e) for e in (ctx.errors or [])}
        for _e in (_l12_ctx.errors or []):
            if str(_e) not in _seen:
                ctx.errors.append(_e)
        if _program is not None:
            _program = _resolve_includes(_program, filename, verbose=False)
            _program = _apply_journey(_program, filename, verbose=False)
    except MohioError as _e:
        if getattr(ctx, 'errors', None) is None:
            ctx.errors = []
        _msg = getattr(_e, 'message', None) or str(_e).strip()
        _ln = getattr(_e, 'line', 0) or getattr(_e, 'close_line', 0) or 0
        _err = MohioCompileError(_msg, _ln)
        _err.hint = ""
        ctx.errors.append(_err)
        _program = None
    except Exception:
        _program = None   # non-Mohio transform hiccup: advisory only, don't break check
    if _program is not None:
        try:
            # Layer 3 (whole-program scanners) THROUGH THE DOOR, now that the program is fully
            # assembled (includes + journey merged). enforce_scans owns the canonical scanner
            # list -- this block no longer hand-copies it, so a scanner added to the door is
            # seen HERE too. That was the exact drift the single-door design eliminates.
            from mohio_enforce import enforce_scans as _enforce_scans
            _enforce_scans(ctx, _program)

        except Exception as _scan_err:
            _scan_incomplete_warn("Layer 3 whole-program scanners", _scan_err)

        # PCI/PII compile-time guard: a save must never persist a `never store`
        # field (e.g. a card CVV). Surface as errors before the program runs.
        try:
            from mohio_transformer_ast import MohioCompileError as _NsErr
            for _msg, _ln, _hint in _check_never_store(_program):
                _e = _NsErr(_msg, _ln)
                _e.hint = _hint
                ctx.errors.append(_e)
        except Exception as _scan_err:
            _scan_incomplete_warn("never-store (PCI/PII) guard", _scan_err)

        # Lint: file-upload fields must declare both accepted types and a max
        # size (no defaults). Missing either is an error; an unusually large
        # limit is a warning. Walk every shape in the program.
        try:
            from mohio_transformer_ast import MohioCompileError as _UpErr
            from mohio_transformer import CompileWarning as _UpWarn
            _UPLOAD_TYPES = {'file', 'image', 'audio', 'video', 'pdf'}
            _WARN_BYTES = 25 * 1024 * 1024
            _seen = set()
            def _walk_uploads(node):
                if id(node) in _seen:
                    return
                _seen.add(id(node))
                if node.__class__.__name__ == 'ShapeDecl':
                    for fld in (getattr(node, 'fields', None) or []):
                        ln = getattr(fld, 'line', 0) or 0
                        # pattern (any field): the regex must compile
                        pat = next((getattr(m, 'value', None) for m in (getattr(fld, 'modifiers', None) or [])
                                    if getattr(m, 'modifier_type', '') == 'pattern'), None)
                        if pat is not None:
                            import re as _re_pat
                            try:
                                _re_pat.compile(pat)
                            except _re_pat.error as _pe:
                                e = _UpErr(f"field '{fld.name}' has an invalid pattern: {_pe}", ln)
                                e.hint = "Fix the regular expression in this field's pattern rule."
                                ctx.errors.append(e)
                        if (getattr(fld, 'type_name', None) or '') not in _UPLOAD_TYPES:
                            continue
                        mods = {getattr(m, 'modifier_type', '') for m in (getattr(fld, 'modifiers', None) or [])}
                        if 'accept' not in mods:
                            e = _UpErr(f"upload field '{fld.name}' must declare accepted "
                                       f"types, e.g. accept png, jpg.", ln)
                            e.hint = "No default is assumed for uploads; list the types you accept."
                            ctx.errors.append(e)
                        if 'maxsize' not in mods:
                            e = _UpErr(f"upload field '{fld.name}' must declare a max size, "
                                       f"e.g. max size 5mb.", ln)
                            e.hint = "No default is assumed for uploads; set a max size."
                            ctx.errors.append(e)
                        else:
                            mv = next((getattr(m, 'value', None) for m in fld.modifiers
                                       if getattr(m, 'modifier_type', '') == 'maxsize'), None)
                            if isinstance(mv, int) and mv > _WARN_BYTES:
                                ctx.warnings.append(_UpWarn(
                                    message=(f"upload field '{fld.name}' allows "
                                             f"{mv / (1024 * 1024):g} MB, which is large."),
                                    line=ln,
                                    hint="Confirm the limit is intended; big uploads strain storage and memory.",
                                    code="UPLOAD_SIZE"))
                for v in (vars(node).values() if hasattr(node, '__dict__') else []):
                    for it in (v if isinstance(v, list) else [v]):
                        if hasattr(it, '__dict__'):
                            _walk_uploads(it)
            _walk_uploads(_program)
        except Exception as _scan_err:
            _scan_incomplete_warn("upload-size lint", _scan_err)

    # Lint: handler clauses (on.success / on.failure / on.error) are NOT block
    # verbs -- 'on' is not a verb, so they take no closer. A stray 'on.x: done'
    # parses (tolerated) but is incorrect style. Warn so it gets cleaned up.
    # (mio fmt will strip it once fmt exists.) Advisory: never break check.
    try:
        import re as _re_hc
        from mohio_transformer import CompileWarning as _CW
        for _i, _ln in enumerate(source.splitlines(), 1):
            _m = _re_hc.match(r'\s*(on\.(?:success|failure|error))\s*:\s*done\b', _ln)
            if _m:
                ctx.warnings.append(_CW(
                    message=(f"'{_m.group(1)}' doesn't take a closer -- 'on' is not "
                             f"a block verb."),
                    line=_i,
                    hint=("Remove this line. A handler ends at the next handler or "
                          "the housing block's own closer (e.g. 'find: done')."),
                    code="HANDLER_CLOSER"))
    except Exception as _scan_err:
        _scan_incomplete_warn("handler-closer lint", _scan_err)

    # Security report
    if security_mode:
        if json_mode:
            import io as _io
            _saved = sys.stdout
            sys.stdout = _io.StringIO()
            try:
                _run_security_report(source, filename, ctx)
            finally:
                sys.stdout = _saved
        else:
            _run_security_report(source, filename, ctx)

    # JSON output mode -- for AI coding agents
    if json_mode:
        import json as _json
        output = {
            "file":    filename,
            "passed":  len(ctx.errors or []) == 0,
            "lines":   n_lines,
            "errors":  [],
            "warnings": [],
            "notices": [],
        }
        # Use structured to_dict() from CompileError/CompileWarning
        # Falls back to text parsing for any non-structured errors
        _re = __import__('re')
        def _err_to_dict(e):
            if hasattr(e, 'to_dict'):
                return e.to_dict()
            text = str(e).strip()
            lm = _re.search(r'[Ll]ine ([0-9]+)', text)
            cm = _re.search(r'([A-Z][A-Z_]{2,}):', text)
            msg = _re.sub('Line [0-9]+ . ', '', text).strip()
            code = cm.group(1) if cm else "ERROR"
            from mohio_transformer import HINT_TABLE
            return {
                "code":    code,
                "line":    int(lm.group(1)) if lm else 0,
                "message": msg,
                "hint":    HINT_TABLE.get(code, ""),
            }
        for e in (ctx.errors or []):
            output["errors"].append(_err_to_dict(e))
        for w in (ctx.warnings or []):
            output["warnings"].append(_err_to_dict(w))
        print(_json.dumps(output, indent=2))
        sys.exit(0 if output["passed"] else 1)

    # Print warnings
    if ctx.warnings:
        print(f"\n  {bold(dim(filename))}  {yellow('Warnings')}\n")
        for w in ctx.warnings:
            _print_compile_warning(w, source, filename)

    # Print errors
    if ctx.errors:
        print(f"\n  {bold(filename)}  {red('Errors')}\n")
        for e in ctx.errors:
            _print_compile_error(e, source, filename)

    # Summary line
    n_err  = len(ctx.errors)
    n_warn = len(ctx.warnings)

    if n_err:
        warn_part = f" . {yellow(f'{n_warn} warning(s)')}" if n_warn else ""
        print(f"  {red('x')}  {bold(filename)}  "
              f"{dim(f'{n_lines} lines')}"
              f"{warn_part} . {red(f'{n_err} error(s)')}\n")
        sys.exit(1)
    else:
        warn_part = f" . {yellow(f'{n_warn} warning(s)')}" if n_warn else ""
        print(f"\n  {green('v')}  {bold(filename)}  "
              f"{dim(f'{n_lines} lines . no errors')}"
              f"{warn_part}\n")
        sys.exit(0)


# -- mio version ----------------------------------------------------------------

def cmd_install_hooks(args):
    """
    mio install-hooks -- Install git hooks for automatic mio check on push/commit.

    Installs:
      .git/hooks/pre-push    -- runs mio check on all .mho files before push
      .git/hooks/pre-commit  -- runs mio check on staged .mho files before commit

    Both hooks block the git operation if mio check finds errors.
    Use --security to also run the full security compliance report.
    Use --pre-commit to also install the pre-commit hook (stricter).
    """
    import stat

    security_flag = "--security" if getattr(args, 'security', False) else ""
    install_precommit = getattr(args, 'pre_commit', False)

    git_dir = Path(".git")
    if not git_dir.exists():
        _die("No .git directory found. Run from the root of a git repository.", exit_code=1)

    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(exist_ok=True)

    # -- pre-push hook ----------------------------------------
    pre_push_content = f"""#!/bin/sh
# Mohio pre-push hook -- installed by mio install-hooks
# Runs mio check on all .mho files before pushing.
# Remove this file to disable: rm .git/hooks/pre-push

echo ""
echo "  mio pre-push check..."

FAILED=0
MIO_CMD="python compiler/mio.py"

# Find all .mho files in the repo
for f in $(find . -name "*.mho" -not -path "./.git/*" 2>/dev/null); do
    result=$($MIO_CMD check "$f" {security_flag} 2>&1)
    exit_code=$?
    if [ $exit_code -ne 0 ]; then
        echo ""
        echo "  x  $f -- errors found:"
        echo "$result" | grep -E "x|ERROR|error" | head -10
        FAILED=1
    fi
done

if [ $FAILED -ne 0 ]; then
    echo ""
    echo "  Push blocked -- fix mio check errors before pushing."
    echo "  Run: mio check <file.mho> for details."
    echo ""
    exit 1
fi

echo "  v  All .mho files pass mio check"
echo ""
exit 0
"""

    pre_push_path = hooks_dir / "pre-push"
    pre_push_path.write_text(pre_push_content)
    pre_push_path.chmod(pre_push_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    print(f"\n  {green('v')}  Installed: {bold('.git/hooks/pre-push')}")
    print(f"  {dim('Runs mio check on all .mho files before git push')}")

    # -- pre-commit hook --------------------------------------
    if install_precommit:
        pre_commit_content = f"""#!/bin/sh
# Mohio pre-commit hook -- installed by mio install-hooks --pre-commit
# Runs mio check on staged .mho files before committing.

echo ""
echo "  mio pre-commit check..."

FAILED=0
MIO_CMD="python compiler/mio.py"

# Only check staged .mho files
for f in $(git diff --cached --name-only --diff-filter=ACM | grep ".mho$"); do
    if [ -f "$f" ]; then
        result=$($MIO_CMD check "$f" {security_flag} 2>&1)
        exit_code=$?
        if [ $exit_code -ne 0 ]; then
            echo ""
            echo "  x  $f -- errors found:"
            echo "$result" | grep -E "x|ERROR|error" | head -10
            FAILED=1
        fi
    fi
done

if [ $FAILED -ne 0 ]; then
    echo ""
    echo "  Commit blocked -- fix mio check errors before committing."
    echo "  Run: mio check <file.mho> for details."
    echo ""
    exit 1
fi

echo "  v  Staged .mho files pass mio check"
echo ""
exit 0
"""
        pre_commit_path = hooks_dir / "pre-commit"
        pre_commit_path.write_text(pre_commit_content)
        pre_commit_path.chmod(pre_commit_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        print(f"  {green('v')}  Installed: {bold('.git/hooks/pre-commit')}")
        print(f"  {dim('Runs mio check on staged .mho files before git commit')}")

    print(f"\n  {dim('To uninstall: rm .git/hooks/pre-push')}\n")

    # -- also generate mio check config file -----------------
    mio_config = {
        "check": {
            "security": bool(security_flag),
            "auto_schema": True,
            "fail_on_warnings": False,
        },
        "hooks": {
            "pre_push": True,
            "pre_commit": install_precommit,
        }
    }
    config_path = Path(".mioconfig")
    import json as _json
    config_path.write_text(_json.dumps(mio_config, indent=2))
    print(f"  {green('v')}  Config written: {bold('.mioconfig')}")
    print(f"  {dim('Edit to customize check behavior')}\n")


def cmd_schema(args):
    """
    mio schema generate <file.mho>  -- Generate .mhoschema manifest from shapes
    mio schema check <file.mho>     -- Validate field references against manifest
    mio schema show <file.mho>      -- Print current schema manifest
    """
    action   = getattr(args, 'schema_action', 'generate')
    filename = getattr(args, 'file', None)

    if not filename:
        _die("Usage: mio schema generate <file.mho>", exit_code=1)

    path = Path(filename)
    if not path.exists():
        _die(f"File not found: {filename}", exit_code=3)

    # ── Multi-file directory mode ──────────────────────────────
    # mio serve myapp/ maps .mho files to URLs automatically:
    #   index.mho -> /
    #   rates.mho -> /rates
    #   terms.mho -> /terms
    # This is the ColdFusion/PHP mental model -- one file per page.
    if path.is_dir():
        _cmd_serve_directory(args, path, verbose)
        return

    source = _read_source(path)

    from mohio_schema import (generate_schema, write_schema, read_schema,
                               find_schema_file, validate_field_references)

    if action == 'generate':
        print(f"\n  {bold('mio schema generate')}  {dim(filename)}\n")
        schema = generate_schema(source, filename)
        n_tables = len(schema.get('tables', {}))
        n_fields = sum(len(t['fields']) for t in schema.get('tables', {}).values())

        schema_path = path.with_suffix('.mhoschema')
        write_schema(schema, schema_path)

        print(f"  {green('v')}  Schema generated: {bold(str(schema_path))}")
        print(f"  {dim(f'{n_tables} table(s), {n_fields} field(s) total')}\n")

        for tname, tdata in schema.get('tables', {}).items():
            print(f"  {bold(tname)}  {dim(f"(from shape {tdata['shape']})")}")
            for fname, fdata in tdata['fields'].items():
                req = yellow(' required') if fdata.get('required') else ''
                ns  = red(' never-stored') if fdata.get('never_stored') else ''
                print(f"    {fname:<20} {dim(fdata['type'])}{req}{ns}")
            print()

    elif action == 'check':
        print(f"\n  {bold('mio schema check')}  {dim(filename)}\n")
        schema_path = find_schema_file(filename)
        if not schema_path:
            print(f"  {yellow('!')}  No .mhoschema found. Run: mio schema generate {filename}")
            sys.exit(1)

        schema = read_schema(schema_path)
        errors = validate_field_references(source, schema, filename)

        if errors:
            for code, msg, lineno in errors:
                print(f"  {red('x')}  line {lineno}: {red(code)}")
                print(f"     {msg}\n")
            print(f"  {red('x')}  {len(errors)} schema error(s) found\n")
            sys.exit(1)
        else:
            print(f"  {green('v')}  All field references valid")
            print(f"  {dim(f'Schema: {schema_path}')}\n")

    elif action == 'show':
        schema_path = find_schema_file(filename)
        if not schema_path:
            print(f"No .mhoschema found for {filename}")
            sys.exit(1)
        import json
        schema = read_schema(schema_path)
        print(json.dumps(schema, indent=2))

    else:
        _die(f"Unknown schema action: {action}. Use: generate, check, show", exit_code=1)


def cmd_harvest(args):
    """Harvest the current word inventory from the .lark grammar.

    ONE job: read the authoritative grammar and emit every reserved word with
    its terminal, category, definition line, an example rule, and status. This
    feeds the langmap / translation tooling -- and, as downstream consumers of
    the same list, the reserved-word table, editor highlighting, and glossaries.

    It only reads mohio.lark. It is NOT a document scraper: for external sources
    use mioai.research; for reshaping data use transform. Keeping it grammar-only
    keeps the job singular.
    """
    import json, re
    text  = GRAMMAR_FILE.read_text(encoding="utf-8")
    lines = text.splitlines()

    cat_re  = re.compile(r'^//\s*─+\s*(.+?)\s*─+\s*$')         # // ── Category ──
    term_re = re.compile(r'^([A-Z_][A-Z0-9_]*)(?:\.\d+)?\s*:\s*(.+?)\s*$')
    lit_re  = re.compile(r'"((?:[^"\\]|\\.)*)"')               # "literal" (w/ escapes)
    rule_re = re.compile(r'^([a-z_][a-z0-9_]*)(?:\.\d+)?\s*:')  # lowercase rule

    # Pre-scan rule lines so we can attach an example rule to each terminal.
    rule_lines = [l.strip() for l in lines if rule_re.match(l.strip())]
    def example_rule_for(term):
        pat = re.compile(r'\b' + re.escape(term) + r'\b')
        return next((r for r in rule_lines if pat.search(r)), "")

    entries     = []
    current_cat = ""
    for i, raw in enumerate(lines, 1):
        s = raw.strip()
        cm = cat_re.match(s)
        if cm and re.search(r'[A-Za-z]', cm.group(1)):
            current_cat = cm.group(1).strip()
            continue
        if s.startswith("//"):
            continue
        tm = term_re.match(s)
        if not tm:
            continue
        term, body = tm.group(1), tm.group(2)
        lits = lit_re.findall(body)          # string-literal terminals ("ai.create")
        if not lits:
            # Regex terminal (e.g. GRAB.2: /grab(?![A-Za-z0-9_])/). The bare keywords are defined
            # this way and must be harvested too -- skipping them dropped grab/make/get/find/etc.
            # Only extract genuine keyword terminals: an all-caps terminal name whose regex is a
            # word with a trailing word-boundary guard. This excludes operators (+, ->), internal
            # tokens (__USERVAR__), and multi-branch regexes that are not single keywords.
            kw = re.match(r'/([a-z][a-z0-9_.]*)\(\?\!', body)
            if kw and term.isupper() and not term.startswith('_'):
                lits = [kw.group(1)]
            else:
                continue
        ex = example_rule_for(term)
        # Retired if a comment/category says so, OR the terminal is only used by a
        # rule explicitly named *_retired* (e.g. MAKE -> make_retired_block).
        ex_rule_name = ex.split(":", 1)[0].strip() if ex else ""
        is_retired = ("retired" in (s + " " + current_cat).lower()
                      or "retired" in ex_rule_name.lower())
        # Alias if the def line marks it: `// alias of <word>`.
        alias_m  = re.search(r'alias of ([A-Za-z_][\w.]*)', s)
        alias_of = alias_m.group(1) if alias_m else ""
        if is_retired:
            status = "retired"
        elif alias_of:
            status = "alias"
        else:
            status = "canonical"
        for lit in lits:
            word   = lit.replace('\\"', '"').replace("\\\\", "\\")
            prefix = word[:word.index(".") + 1] if "." in word else ""
            entries.append({
                "word":             word,
                "terminal":         term,
                "category":         current_cat,
                "def_line":         i,
                "invariant_prefix": prefix,
                "example_rule":     ex,
                "status":           status,
                "alias":            alias_of,
                "map_label":        "",
                "note":             "",
            })

    out = json.dumps(entries, indent=2, ensure_ascii=False)
    if getattr(args, "stdout", False) or getattr(args, "output", None) == "-":
        print(out)
        return
    path = Path(getattr(args, "output", None) or "mohio_words.json")
    path.write_text(out + "\n", encoding="utf-8")
    from collections import Counter
    cats    = Counter(e["category"] for e in entries)
    retired = sum(1 for e in entries if e["status"] == "retired")
    aliases = sum(1 for e in entries if e["status"] == "alias")
    print(f"  [mio harvest] {len(entries)} words from {GRAMMAR_FILE.name} -> {path}")
    print(f"    {len(cats)} categories, {retired} retired, {aliases} alias(es). "
          f"Editorial fields (alias/map_label/note) left blank for the langmap pass.")


def cmd_audit(args):
    """mio audit verify|head|verify-anchors [db] -- inspect the audit trail's hash chain.

    Reads the durable store directly (DATABASE_URL, or a path given as the argument). It does NOT
    run the program: an auditor should be able to check the records without executing the code
    that produced them, and requiring a run would mean the thing under inspection gets to act
    first.

    `verify` walks each audit log and reports whether the chain is intact -- an altered, deleted,
    or reordered record breaks it and is named. `head` prints each log's current chain head, which
    is the value an anchoring scheme publishes: a head that no longer matches a previously
    published one is how truncation and genesis-restart become visible, since neither of those
    breaks the chain internally.
    """
    action = getattr(args, "audit_action", "verify") or "verify"
    target = getattr(args, "file", None) or os.environ.get("DATABASE_URL", "")
    if not target:
        _die("No database given. Pass a path (`mio audit verify app.db`) or set DATABASE_URL.",
             exit_code=3)

    from mohio_interpreter import MohioInterpreter, DbRuntime
    try:
        sink = DbRuntime(target)
    except Exception as e:
        _die(f"Could not open the audit store at {target}: {e}", exit_code=3)

    it = MohioInterpreter()
    logs = it.audit_logs(sink)
    if not logs:
        print(f"\n  {dim('No audit logs found in ' + str(target) + '.')}\n")
        return

    if action == "verify-anchors":
        anchors_path = getattr(args, "anchors", None)
        if not anchors_path:
            _die("verify-anchors needs an anchors file: "
                 "mio audit verify-anchors app.db --anchors anchors.json", exit_code=3)
        import json
        try:
            with open(anchors_path) as fh:
                anchor_map = json.load(fh)
        except Exception as e:
            _die(f"Could not read anchors file {anchors_path}: {e}", exit_code=3)
        if not isinstance(anchor_map, dict):
            _die("Anchors file must be a JSON object mapping log name to a list of "
                 "{head, length} anchors.", exit_code=3)
        print()
        failed = False
        checked_any = False
        for log in logs:
            entries = anchor_map.get(log)
            if not entries:
                continue
            checked_any = True
            r = it.verify_audit_chain_against_anchors(sink, log, entries)
            if r['ok']:
                print(f"  {green('v')}  {log}  {r['anchors_satisfied']}/{r['anchors_checked']} "
                      f"anchors hold . chain matches published history")
            else:
                failed = True
                print(f"  {red('x')}  {log}  {r['reason']}")
                for f in r['failures']:
                    print(f"     {red(f['kind'])} at length {f['length']}: {f['detail']}")
        if not checked_any:
            print(f"  {dim('No anchors in ' + anchors_path + ' matched any log in ' + str(target) + '.')}")
        print()
        print(f"  {dim('This compares heads and trusts the anchors as authentic. Verify each')}")
        print(f"  {dim('anchor signature upstream; this check does not verify signatures.')}")
        print()
        if failed:
            sys.exit(1)
        return

    print()
    failed = False
    for log in logs:
        info = it.audit_chain_head(sink, log)
        if action == "head":
            print(f"  {bold(log)}")
            print(f"    head     {info['head']}")
            print(f"    entries  {info['entries']}")
        elif info['intact']:
            print(f"  {green('v')}  {log}  {info['entries']} entries . chain intact")
            print(f"     {dim('head ' + info['head'])}")
        else:
            failed = True
            print(f"  {red('x')}  {log}  chain BROKEN")
            print(f"     {info['reason']}")
            if info['broken_at']:
                print(f"     {dim('at audit_id ' + str(info['broken_at']))}")
    if action != "head":
        print()
        print(f"  {dim('An intact chain still cannot prove nothing was removed from the END of')}")
        print(f"  {dim('the log. Comparing the head above against a previously published anchor')}")
        print(f"  {dim('is what detects that.')}")
    print()
    if failed:
        sys.exit(1)


def cmd_version(args):
    print(f"\n  {bold('mio')}  Mohio Language CLI")
    print(f"  CLI version:      {VERSION}")
    print(f"  Language:         {LANGUAGE_VERSION}")
    print(f"  Grammar:          {GRAMMAR_FILE}")
    print()


# -- mio help -------------------------------------------------------------------

def cmd_help(args):
    print(f"""
  {bold('mio')} -- Mohio Language CLI  {dim(f'v{VERSION}')}

  {bold('USAGE')}

    mio run <file.mho>                  Execute a Mohio program
    mio run <file.mho> --verbose        Execute with trace output
    mio run <file.mho> --ai             Use real Anthropic API for ai.decide
    mio serve <file.mho>                Start HTTP server on port 8080
    mio serve <file.mho> --port 9000    Start on custom port
    mio serve <file.mho> --ai           Serve with real Anthropic API
    mio check <file.mho>                Validate -- all errors and warnings, no run
    mio check --all                     Validate every .mho file in the tree
    mio generate <artifact>             Generate artifacts (e.g. training data)
    mio translate <file.mho> --to <lang>  Translate a program's natural-language layer
    mio schema <generate|check|show>    Work with .mhoschema files
    mio harvest [--output f.json]       Extract the current word inventory from the grammar (langmaps)
    mio schedule <file.mho>             Run any scheduled tasks that are due
    mio audit verify <db>               Check each audit log's hash chain is intact
    mio audit head <db>                 Print each log's chain head (the value you anchor)
    mio audit verify-anchors <db> --anchors f.json   Check the chain against published anchors
    mio warmup                          Pre-warm the parser cache
    mio install-hooks                   Install git pre-commit hooks
    mio version                         Print version information
    mio help                            Print this message

  {bold('COMING SOON')}

    mio fmt <file.mho>                  Auto-format to canonical Mohio
                                          (e.g. 'verb: done'; strips stray handler closers)
    {dim('(more tooling commands are on the roadmap)')}

  {bold('PASSING A REQUEST')}

    {bold('--request-file')} (all platforms, recommended):
      mio run fraud_demo.mho --request-file tests/request.json

    {bold('--param')} key=value (Windows CMD friendly):
      mio run fraud_demo.mho --param _shape=Transaction --param amount=500

    {bold('--request')} JSON (Mac/Linux/PowerShell):
      mio run fraud_demo.mho --request '{{"amount": 500, "member_id": "M001"}}'

  {bold('SAMPLE request.json')}

    {{
      "_shape":    "Transaction",
      "_method":   "POST",
      "_roles":    ["screener"],
      "id":        "T1",
      "amount":    500,
      "member_id": "M001"
    }}

  {bold('EXIT CODES')}

    0   success / clean
    1   compile error (syntax or validation)
    2   runtime error
    3   file not found

  {bold('WHAT MIO CHECK CATCHES')}

    Hard errors (build refused):
      . ai.decide missing not confident block
      . ai.audit appearing after not confident (wrong order)
      . cm.purge without reason
      . define used (reserved)
      . invoke / recall / remember used (Phase 3)
      . PCI violations (sector: financial)
      . Closer mismatches

    Warnings (builds, mio fmt will fix):
      . set keyword used (retired)
      . or if used (retired)
      . check confidence above (retired form)
      . Hardcoded credentials detected
      . Task named closer (e.g. taskName: done -> task: done)
""")


# -- Argument parser ------------------------------------------------------------

def build_arg_parser():
    p = argparse.ArgumentParser(prog="mio", description="Mohio Language CLI",
                                add_help=False)
    sub = p.add_subparsers(dest="command")

    # serve
    s = sub.add_parser("serve", add_help=False)
    s.add_argument("file")
    s.add_argument("--db", dest="db", default=None,
                   help="Explicit database path or URL (overrides the persistent default)")
    s.add_argument("--memory", dest="memory", action="store_true",
                   help="Use a throwaway in-memory database (data is lost when the server stops)")
    s.add_argument("--port", "-p", type=int, default=8080)
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--verbose", "-v", action="store_true")
    s.add_argument("--ai", action="store_true", default=False)
    s.add_argument("--api-key", default=None, dest="api_key")
    s.add_argument("--seed", default=None, metavar="seed.json")

    # run
    r = sub.add_parser("run", add_help=False)
    r.add_argument("file")
    r.add_argument("--db", dest="db", default=None,
                   help="Explicit database path or URL (overrides the persistent default)")
    r.add_argument("--memory", dest="memory", action="store_true",
                   help="Use a throwaway in-memory database (data is lost when the app stops)")
    r.add_argument("--verbose", "-v", action="store_true")
    r.add_argument("--request", "-r", default=None)
    r.add_argument("--request-file", "-f", default=None, dest="request_file")
    r.add_argument("--param", "-p", action="append", default=[], metavar="key=value")
    r.add_argument("--ai", action="store_true", default=False)
    r.add_argument("--api-key", default=None, dest="api_key")
    r.add_argument("--seed", default=None, metavar="seed.json")

    # check
    c = sub.add_parser("check", add_help=False)
    c.add_argument("--security", action="store_true", help="Run full security compliance report")
    c.add_argument("--json",     action="store_true", help="Output structured JSON for agent consumption")
    c.add_argument("--fast",     action="store_true", help="Fast check: ASCII + reserved words only, skip full parse")
    c.add_argument("--all",      action="store_true", help="Check all .mho files in current directory tree")
    c.add_argument("--langmap",  action="store_true", help="List every keyword this file's langmap does not map (unmapped words fall back to English)")
    c.add_argument("file",       nargs="?",           help="File to check (omit with --all)")

    # fmt
    fp = sub.add_parser("fmt", add_help=False)
    fp.add_argument("file")
    fp.add_argument("--write", "-w", action="store_true", help="Apply changes in place")
    fp.add_argument("--stdout", action="store_true", help="Print formatted source to stdout")

    # version / help
    sub.add_parser("version", add_help=False)
    wu = sub.add_parser("warmup", add_help=False)
    wu.add_argument("target", nargs="?", default=None)
    gen = sub.add_parser("generate", add_help=False)
    gen.add_argument("artifact", nargs="?", default="training-data")
    gen.add_argument("source", nargs="?", default="applang")
    gen.add_argument("--db", dest="db", default=None,
                     help="path to sqlite db (default: DATABASE_URL env)")
    gen.add_argument("--output", dest="output", default=None,
                     help="output JSONL file (default: applang_training_data.jsonl)")
    gen.add_argument("--min-hits", dest="min_hits", type=int, default=1,
                     help="minimum hit count to include (default: 1)")
    tr = sub.add_parser("translate", add_help=False)
    tr.add_argument("file", nargs="?", help=".mho source file to translate")
    tr.add_argument("--from", dest="from_lang", default="en",
                    help="source human language (default: en)")
    tr.add_argument("--to", dest="to_lang", default=None,
                    help="target human language (e.g. pt, klingon)")
    tr.add_argument("--output", dest="output", default=None,
                    help="output file path (default: source_<lang>.mho)")
    ih = sub.add_parser("install-hooks", add_help=False)
    ih.add_argument("--security",    action="store_true")
    ih.add_argument("--pre-commit",  action="store_true", dest="pre_commit")
    sc = sub.add_parser("schema", add_help=False)
    sc.add_argument("schema_action", nargs="?", default="generate",
                    choices=["generate", "check", "show"])
    sc.add_argument("file", nargs="?", default=None)
    aud = sub.add_parser("audit", add_help=False)
    aud.add_argument("audit_action", nargs="?", default="verify",
                     choices=["verify", "head", "verify-anchors"])
    aud.add_argument("file", nargs="?", default=None)
    aud.add_argument("--anchors", default=None,
                     help="Path to a JSON file of published anchors (for verify-anchors)")
    sched = sub.add_parser("schedule", add_help=False)
    sched.add_argument("schedule_action", nargs="?", default="run-due",
                       choices=["run-due", "list", "watch"])
    sched.add_argument("file", nargs="?", default=None)
    sched.add_argument("--interval", type=int, default=60,
                       help="watch tick interval in seconds (default 60)")

    hv = sub.add_parser("harvest", add_help=False)
    hv.add_argument("--output", "-o", default=None,
                    help="Output file (default: mohio_words.json; '-' for stdout)")
    hv.add_argument("--stdout", action="store_true", help="Print to stdout")
    ac = sub.add_parser("ai-check", add_help=False)
    ac.add_argument("--api-key", dest="api_key", default=None)
    ac.add_argument("--verbose", "-v", action="store_true")

    sub.add_parser("help", add_help=False)

    return p


# -- Entry point ----------------------------------------------------------------

def cmd_schedule(args):
    """`mio schedule run-due <file>` — fire schedules that are due (call this
    from an external cron/worker; see the deploy note). `mio schedule list
    <file>` — show what's registered."""
    action   = getattr(args, "schedule_action", "run-due")
    filename = args.file
    verbose  = getattr(args, "verbose", False)
    if not filename:
        _die("Usage: mio schedule run-due <file.mho>  (or: mio schedule list <file.mho>)",
             exit_code=3)
    path = Path(filename)
    source = _read_source(path, exit_code=3)

    tree, ctx = _parse_and_validate(source, filename, verbose)
    if ctx.errors:
        for e in ctx.errors:
            _print_compile_error(e, source, filename)
        sys.exit(1)

    from mohio_transformer_ast import transform as ast_transform
    program = ast_transform(tree, source)
    program = _resolve_includes(program, filename, verbose=verbose)
    program = _apply_journey(program, filename, verbose=verbose)

    # Single door: Layer-3 scan on the assembled program before any scheduled work fires.
    if program is not None:
        try:
            from mohio_enforce import enforce_scans as _enforce_scans
            _enforce_scans(ctx, program)
        except Exception as _scan_err:
            # A crashing scanner must not kill an otherwise-valid command, but it must not be
            # silent: enforcement for this run is INCOMPLETE and the program proceeds anyway.
            import sys as _sys
            print(f"  [enforce] WARNING: a Layer 3 scanner failed "
                  f"({type(_scan_err).__name__}: {_scan_err}). Enforcement is INCOMPLETE -- "
                  f"some checks did not execute. Run `mio check` for the full result.",
                  file=_sys.stderr)
        if ctx.errors:
            for e in ctx.errors:
                _print_compile_error(e, source, filename)
            print(red(f"  {len(ctx.errors)} error(s) -- run `mio check {filename}`.\n"))
            sys.exit(1)

    from mohio_interpreter import MohioInterpreter, Context
    it = MohioInterpreter(verbose=verbose)
    it.run_declarations(program)                 # db/connect/audit setup
    exec_ctx = Context()
    it._exec_declarations(program, exec_ctx)     # register tasks + schedules for firing

    if action == "list":
        if not it._schedules:
            print("  No schedules registered.")
        for name, s in it._schedules.items():
            print(f"  {name}  ->  tasks: {', '.join(s['tasks']) or '(none)'}")
        return

    if action == "watch":
        # Dev ticker (model B): a foreground loop that fires due schedules every
        # --interval seconds. Good for local dev / a single always-on instance.
        # In production prefer an external driver calling `run-due` (model A).
        import time as _time
        interval = getattr(args, "interval", 60) or 60
        print(f"  Watching {filename} -- firing due schedules every {interval}s "
              f"(Ctrl-C to stop).")
        try:
            while True:
                it.shown = []
                fired = it.run_due_schedules(exec_ctx)
                if fired:
                    print(f"  [{_time.strftime('%H:%M:%S')}] fired: {', '.join(fired)}")
                    for line in it.shown:
                        print(f"    {line}")
                _time.sleep(interval)
        except KeyboardInterrupt:
            print("\n  Stopped.")
        return

    fired = it.run_due_schedules(exec_ctx)
    if fired:
        print(f"  Fired {len(fired)} schedule(s): {', '.join(fired)}")
    else:
        print("  No schedules due.")
    for line in it.shown:
        print(f"    {line}")


def cmd_fmt(args):
    """Auto-format a .mho file toward canonical form.

    v1 normalizes legacy assignment spellings (`set` / `=`) to canonical
    `name value`. Dry-run by default; --write applies in place.
    """
    from pathlib import Path
    path = Path(args.file)
    src = _read_source(path)
    grammar = _load_grammar()
    parser  = _make_parser_cached(grammar)
    from mohio_fmt import dequote_paths, format_source
    # Fix a mistaken quoted path (`at "/x"` -> `at /x`) before transforming, so a
    # file the compiler would reject for a quoted path still formats and gets fixed
    # here rather than dying on the fail-loud.
    src, _path_fixes = dequote_paths(src, parser)
    from mohio_transformer_ast import transform
    # Pretokenize dotted user-var accesses (same as _parse_and_validate) so a file
    # using a type-word field like `x.text` formats instead of failing to parse.
    try:
        from mohio_symbol_table import extract_symbols
        from mohio_transformer import MOHIO_RESERVED_EXACT
        from mohio_pretokenizer import pretokenize
        _fmt_symbols = extract_symbols(src)
        _fmt_parse_src = pretokenize(src, _fmt_symbols.all_user_names(), MOHIO_RESERVED_EXACT)
    except Exception:
        _fmt_parse_src = src
    try:
        ast = transform(parser.parse(_fmt_parse_src), src)
    except Exception as e:
        _die(f"Cannot format -- file does not parse:\n{e}", exit_code=1)
    out, changes = format_source(src, ast)
    if _path_fixes:
        changes.append((0, "quoted path", f"{_path_fixes} unquoted (at \"/x\" -> at /x)"))

    if getattr(args, "stdout", False):
        sys.stdout.write(out)
        return

    if not changes:
        print(f"mio fmt: {path} -- already canonical")
        return

    if getattr(args, "write", False):
        path.write_text(out, encoding="utf-8")
        print(f"mio fmt: {path} -- {len(changes)} line(s) normalized to canonical")
    else:
        print(f"mio fmt: {path} -- {len(changes)} line(s) would change (run with --write to apply):")
    for ln, old, new in changes:
        print(f"  line {ln}: {old}  ->  {new}")


def cmd_ai_check(args):
    """`mio ai-check` -- prove the AI path actually works before users depend on it.

    A missing key already fails loudly at startup. A WRONG key used to not: the client
    built fine, and `ai.decide` used to guarantee no failure escapes, so every decision
    quietly fell back and the app looked healthy from outside. As of 2026-08-04 a hard
    provider failure raises AiProviderError instead of faking a result -- this command
    still exists because that failure is now visible at RUNTIME too (a real request
    500s / on.failure fires), but a host validating a freshly pasted key before any
    traffic hits it still wants a single, deliberate, reported check like this one.

    Exit codes: 0 working, 1 reachable but degraded, 2 not configured.
    """
    verbose = getattr(args, "verbose", False)
    try:
        from mohio_ai import AnthropicAiRuntime, AiProviderError
    except ImportError:
        _die("The Anthropic SDK is not installed.\n\n  Run:  pip install anthropic",
             exit_code=2)
    try:
        ai = _construct_ai_runtime(getattr(args, "api_key", None), verbose)
    except RuntimeError as e:
        _die(str(e), exit_code=2)

    print(f"  {dim('model:')} {ai._model}")
    try:
        ai.decide(
            name="ai_check",
            inputs={"question": "Reply with the single word yes."},
            threshold=0.0,
            return_type="text",
        )
    except AiProviderError as e:
        # The message carries the provider's real error -- an auth failure reads
        # very differently from a timeout, and that difference is the whole point.
        print()
        print(f"  {red('x')}  AI is configured but not working.")
        print(f"     {e}")
        print()
        print("     Every ai.decide would raise the same way at runtime now (loud, not")
        print("     silent), but catching it here means a host finds out before real")
        print("     traffic does. Check the API key.")
        sys.exit(1)
    print()
    print(f"  {green('v')}  AI is working. A real decision came back from the provider.")
    sys.exit(0)


def main():
    p    = build_arg_parser()
    args = p.parse_args()

    # A host that runs `mio serve` for many apps cannot rewrite the command line per
    # app, so MOHIO_AI=1 turns AI on the same way --ai does. Set once, centrally, so
    # every command that reads args.ai sees it.
    if not getattr(args, "ai", False):
        import os as _os
        if _os.environ.get("MOHIO_AI", "").strip().lower() in ("1", "true", "yes", "on"):
            args.ai = True

    dispatch = {
        "run":     cmd_run,
        "serve":   cmd_serve,
        "check":   cmd_check,
        "warmup":  cmd_warmup,
        "translate": cmd_translate,
        "generate":  cmd_generate,
        "schema":        cmd_schema,
        "schedule":      cmd_schedule,
        "audit":         cmd_audit,
        "install-hooks": cmd_install_hooks,
        "harvest":       cmd_harvest,
        "fmt":           cmd_fmt,
        "ai-check":      cmd_ai_check,
        "version": cmd_version,
        "help":    cmd_help,
    }

    fn = dispatch.get(args.command, cmd_help)
    try:
        fn(args)
    except SystemExit:
        raise                      # a command's own clean exit / integrity refusal -- not an error to wrap
    except KeyboardInterrupt:
        print(file=sys.stderr)     # user hit Ctrl-C; exit quietly, no traceback
        sys.exit(130)
    except Exception as e:
        _die_unexpected(e, args.command)


if __name__ == "__main__":
    main()
