#!/usr/bin/env python3
# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
mio -- Mohio Language CLI
Version: see mohio_version.VERSION | Language: v3.8 | August 2026 | Particular LLC

Usage:
    mio new <name>
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
            f"Make sure mohio_data/mohio.lark exists alongside the mohio_data package.",
            exit_code=3,
        )
    raw = GRAMMAR_FILE.read_text(encoding="utf-8-sig")
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


def _code_part(raw):
    """The part of a line the compiler reads: everything before a `//` comment.

    Quote-aware, because a `//` inside a string is text, not a comment, and treating it as one
    would make `show "http://x"` look like an unterminated string.
    """
    out, in_str, i = [], False, 0
    while i < len(raw):
        c = raw[i]
        if c == '\\' and in_str and i + 1 < len(raw):
            out.append(raw[i:i + 2])
            i += 2
            continue
        if c == '"':
            in_str = not in_str
        elif not in_str and c == '/' and raw[i:i + 2] == '//':
            break
        out.append(c)
        i += 1
    return "".join(out)


def _find_unterminated_string(source):
    """Find a line that opens a double quote and never closes it.

    THE MOST COMMON BEGINNER TYPO, and it produced the worst message in the compiler: an
    unclosed quote swallows the rest of the file, the parser dies at end-of-input, and the
    report was `Unexpected end-of-input. Expected one of:` with an EMPTY list, no line and no
    caret. Nothing in it mentioned a quote. Eight different mistakes shared that one message.

    A Mohio string does not span lines, so an odd number of unescaped quotes in the code part
    of a line is the open one. Returns (line_no, column_of_the_opening_quote).

    Runs only on an already-failing parse, so a heuristic is safe here: the worst case is a
    hint that does not apply, never a false error on a working file.
    """
    for i, raw in enumerate(_mask_noncode(source).split("\n")):
        code = _code_part(raw)
        quotes = [n for n, c in enumerate(code)
                  if c == '"' and (n == 0 or code[n - 1] != '\\')]
        if len(quotes) % 2 == 1:
            return (i + 1, quotes[-1] + 1)
    return (None, None)


# Words that used to be Mohio and are not any more. A pioneer meets these by copying an old
# blog post or an out-of-date answer, and until now they died on a character further down the
# line ("No terminal matches '/'") that had nothing to do with the retired word they typed.
# Each entry says WHAT they wrote, and WHAT to write instead, in the same shape as the message
# `set` already gets.
_RETIRED_FORMS = (
    (r'^route\b', 'route',
     "A web address is declared with `listen for`, and the address rides on the request:\n"
     "    listen for\n"
     "        request for sh.Thing at /home\n"
     "            ...\n"
     "        request: done\n"
     "    listen: done"),
    (r'^make\b', 'make',
     "Use `create`.  create list colors  ...  create: done"),
    (r'^consider\b', 'consider',
     "Use `check`.  check score / when ... / otherwise ... / check: done"),
    (r'^delete\b', 'delete',
     "Use `remove`.  remove it  /  remove from db.items ... remove: done"),
    (r'^catch\b', 'catch',
     "Use `on.failure` for the failure branch, and `on.success` for the other one."),
    (r'^(if|else)\b', None,
     "Mohio decides with `check` / `when` / `otherwise`, or with `unless`:\n"
     "    check score\n"
     "        when score is more than 100\n"
     "            show \"big\"\n"
     "        otherwise\n"
     "            show \"small\"\n"
     "    check: done"),
    (r'\bas\.string\b', 'as.string',
     "Use `as.text`. Mohio has one name for a string type and it is `text`."),
    (r'^request\s+outbound\b', 'request outbound',
     "Use `miohttp.get` / `miohttp.post` for an outbound call, or `mioconnect` for a "
     "declared integration."),
    # `page` IS THE EXACT CASE THIS TABLE WAS BUILT FOR, and it was missing from it. The word is
    # kept RESERVED so old code gets told what replaced it; instead `page /home` died on
    # "No terminal matches '/'", which names a slash and explains nothing. Measured 2026-09-15.
    # The transformer also refuses the word, which catches `page 5` -- but that runs too late for
    # the routing form, because `page /home` never parses.
    # ONLY THE ROUTING SHAPE. `page` is also a perfectly ordinary column name -- a save
    # block writing `page "bump"` is a real program, and matching the bare word would
    # hand that program a retirement notice about something it never wrote. The retired
    # form always put a PATH after the word, and that is what is matched.
    (r'^page\s+/', 'page',
     "The page block was removed. A file serves itself now -- whatever it renders or gives "
     "back IS the response, with no routing code:\n"
     "    // index.mho, served at /\n"
     "    give back [200] \"<h1>Hello</h1>\"\n"
     "To serve several addresses from one file, name them:\n"
     "    listen for\n"
     "        request for sh.Home at /about\n"
     "            render\n                <p>About</p>\n"
     "            render: done\n"
     "        request: done\n"
     "    listen: done\n"
     "Page CLASSIFICATION (`private:` / `hidden:`) is a different thing and is unchanged."),
)


def _find_retired_form(source):
    """Find a retired keyword at the head of a statement. Returns (word, line_no, guidance)."""
    import re as _r
    for i, raw in enumerate(_mask_noncode(source).split("\n")):
        line = _code_part(raw).strip()
        if not line:
            continue
        for pattern, word, guidance in _RETIRED_FORMS:
            m = _r.search(pattern, line)
            if m:
                return (word or m.group(1), i + 1, guidance)
    return (None, None, None)


# Words the grammar reserves and no rule ever accepts. They are defined as terminals, which is
# enough to stop them being read as an ordinary name, and nothing consumes them, so writing one
# is a parse error on some unrelated character further down. `do.once` is NOT here: it is wired,
# as an idempotency key on a scheduled run.
_RESERVED_UNBUILT = {
    'do.every':   "a repeating schedule",
    'do.after':   "a delayed action",
    'do.unless':  "a guarded action",
    'do.encrypt': "an encryption step",
}


# ── case-transform near misses ─────────────────────────────────────────────────────────
#
# Mohio's case vocabulary is richer than the one most newcomers arrive with, and that is exactly
# why they miss it: they type the word their old language used and land on nothing. MEASURED --
# `as.upper`, `as.lower`, `as.capitalize`, `as.caps`, `as.upcase` and `as.downcase` all dead-end
# with a bare parse error naming a character.
#
# WHICH FORMS THIS OFFERS IS MEASURED, NOT READ OFF THE GRAMMAR. Six are defined; four run.
# `as.title` and `as.sentence` parse and have no executor, so they are named as accepted-and-not-
# working rather than offered as answers.
_CASE_NEAR_MISS = {
    'as.upper': 'as.uppercase', 'as.upcase': 'as.uppercase', 'as.caps': 'as.uppercase',
    'as.lower': 'as.lowercase', 'as.downcase': 'as.lowercase',
    'as.capitalize': 'as.title', 'as.titlecase': 'as.title', 'as.ucfirst': 'as.title',
    's.upper': 'as.uppercase', 's.lower': 'as.lowercase',
}

# The two that parse and have no executor. Measured, not read off the grammar.
_CASE_UNWIRED = frozenset({'as.title', 'as.sentence'})

_CASE_GUIDANCE = (
    "Mohio changes case with a dotted form, and these four run today:\n"
    "    show name as.uppercase        // or the short form  as.uc\n"
    "    show name as.lowercase        // or the short form  as.lc\n"
    "`as.title` and `as.sentence` are also written down, and they PARSE AND DO NOTHING -- there "
    "is no executor for them yet, so a program using one checks clean and stops at runtime.")


def _find_case_near_miss(source):
    """A case transform spelled the way another language spells it. Returns (written, meant)."""
    import re as _re
    text = _mask_noncode(source)
    for wrong, right in _CASE_NEAR_MISS.items():
        if _re.search(r'(?<![A-Za-z0-9_.])%s(?![A-Za-z0-9_])' % _re.escape(wrong), text):
            return (wrong, right)
    return (None, None)


def _find_unspellable_construct(source):
    """A construct that IS built, written in a form nothing accepts. Returns (word, line, how).

    THE STANDING PRINCIPLE THIS SERVES: the error is the only teacher. A construct whose error
    does not teach its spelling is invisible, however completely it is implemented, because the
    only way to find the working form is to already know it. `mioschedule` was exactly that: a
    full declaration with timing, timezone, windows and idempotency, reachable only by someone
    who had read the grammar.

    Detected at SOURCE level for the same reason the retired-word scan is: by the time the
    parser fails there is no tree to ask, and the failure has already moved to another line.
    """
    import re as _r
    for i, raw in enumerate(_mask_noncode(source).split("\n")):
        line = _code_part(raw).strip()
        if not line:
            continue
        # `mioschedule` leading a line, not the dotted runtime forms (mioschedule.every / .at /
        # .in / .cancel), which are a different construct and parse on their own.
        m = _r.match(r'^mioschedule(?!\s*[.:])(?:\s+([A-Za-z_]\w*))?\s*$', line)
        if m:
            named = m.group(1)
            return ('mioschedule', i + 1,
                    # EVERY FORM BELOW WAS RUN BEFORE IT WAS WRITTEN HERE. The first draft of
                    # this message taught `at "02:00"` and that does not parse: a time is
                    # written bare, not quoted. A refusal that teaches a form nobody can run is
                    # the same failure it exists to fix, one level up.
                    "A schedule is a NAMED declaration with a body and its own closer:\n"
                    "    mioschedule nightly_cleanup\n"
                    "        at 09:00\n"
                    "        run cleanup\n"
                    "    mioschedule: done\n"
                    "The body says WHEN and `run <task>` says WHAT. These run today:\n"
                    "    at 09:00   /   every 5 minutes   /   in 30 minutes   /   "
                    "on december 31 at 11:59pm\n"
                    "A time is written bare and never quoted: `at 09:00`, not `at \"09:00\"`.\n"
                    "`timezone \"EST\"` and `timeout after 10 minutes` are also body lines.\n"
                    "The WEEKDAY forms (`every monday at 9am`, `every day at 09:00`, "
                    "`on monday`) parse and are\n"
                    "NOT WIRED YET -- they are accepted and do nothing, so use one of the "
                    "forms above until they are."
                    + ("" if named else
                       "\nA schedule needs a name of its own -- that is how it is run later "
                       "(`run mioschedule.<name> now`)."))
    return (None, None, None)


def _find_reserved_unbuilt(source):
    """Find a word the grammar reserves but nothing implements. Returns (word, line, what)."""
    import re as _r
    for i, raw in enumerate(_mask_noncode(source).split("\n")):
        line = _code_part(raw).strip()
        if not line:
            continue
        m = _r.match(r'^([a-z]+\.[a-z_]+)', line)
        if m and m.group(1) in _RESERVED_UNBUILT:
            return (m.group(1), i + 1, _RESERVED_UNBUILT[m.group(1)])
    return (None, None, None)


def _find_hash_comment(source):
    """Find a line commented with `#`, which is not a comment in Mohio.

    It failed as `No terminal matches '#'`, which never once said what a comment looks like.
    """
    for i, raw in enumerate(_mask_noncode(source).split("\n")):
        if raw.strip().startswith("#"):
            return i + 1
    return None


# Block openers and everyday verbs, for the capitalised-opener check below.
_LOWERCASE_WORDS = (
    'check', 'show', 'save', 'find', 'retrieve', 'grab', 'repeat', 'task', 'shape', 'listen',
    'journey', 'try', 'loop', 'while', 'hold', 'lock', 'call', 'remove', 'create', 'add',
    'connect', 'render', 'give', 'replace', 'rename', 'forget', 'clear', 'release', 'unless',
    'when', 'otherwise', 'modify', 'update', 'upsert', 'saga', 'flow',
)


def _find_capitalised_opener(source):
    """Find a Mohio word written with a capital letter. Returns (word, line_no).

    `Check x` breaks the parse before any validator runs, and the caret lands on a `(` two
    lines further down, inside a `when` that is perfectly correct. The person is told about a
    bracket when what they typed wrong was a capital C. `Show` already had a named message for
    exactly this; the block openers did not.
    """
    import re as _r
    for i, raw in enumerate(_mask_noncode(source).split("\n")):
        line = _code_part(raw).strip()
        m = _r.match(r'^([A-Z][a-z.]+)\b', line)
        if m and m.group(1).lower() in _LOWERCASE_WORDS:
            return (m.group(1), i + 1)
    return (None, None)


def _echo_line_if_new(source, want_line, already_shown):
    """Show the offending line, unless the report already showed that exact line.

    The header prints the line the PARSER died on, which is often not the line the mistake is
    on. When an explanation names a DIFFERENT line, showing it is the whole point; when it
    names the same one, printing it twice only makes the message look confused.
    """
    if want_line and want_line != already_shown:
        snippet = _source_snippet(source, want_line)
        if snippet is not None:
            print(f"\n  {dim(str(want_line) + ' |')} {snippet}")


# ── what the parser knew, said in words ─────────────────────────────────────────────────
#
# Every form taught below was RUN before it was written here. A message that teaches a form
# nobody can run is the same failure it exists to fix, one level up, and this lane has already
# made that mistake twice.
_FOREIGN_PUNCTUATION = (
    # (pattern, what the reader wrote, what Mohio does instead)
    (r'\{\s*$|^\s*\}',
     "a curly brace opening or closing a block",
     "Mohio blocks are indentation and a named closer, never braces:\n"
     "    check score\n        when score is more than 100\n            show \"big\"\n"
     "    check: done\n"
     "The only brace form in Mohio is the double brace, which shows a value inside text:\n"
     "    show \"Hi {{ name }}\""),
    (r'\$[A-Za-z_]\w*',
     "a dollar sign in front of a name",
     "A Mohio value is just its name:  count 5  /  show count\n"
     "Request data arrives through a shape, never through a global:\n"
     "    shape Signup\n        name as text required\n    shape: done\n"
     "then read it as  request.name  inside the listener."),
    (r';\s*$',
     "a semicolon ending the line",
     "Mohio lines end at the end of the line. There is no semicolon."),
    (r'[A-Za-z_]\w*\s*\[\s*\d+\s*\]',
     "square brackets to reach an item by number",
     "Mohio counts from 1 and reaches an item by position:\n"
     "    show colors.position.2      // or colors.pos.2\n"
     "    show colors.first           // and colors.last\n"
     "Square brackets are for field tags, as in  ssn as text [pii]."),
    (r'[A-Za-z_]\w*\.[A-Za-z_]\w*\s*\(\s*\)',
     "a method call with parentheses",
     "Mohio calls a task by name, in a block:\n"
     "    call greet\n        name \"Ada\"\n    call: done\n"
     "Parentheses group arithmetic and comparisons, nothing else."),
    (r'\[[^\]]*\bfor\b[^\]]*\bin\b[^\]]*\]',
     "a list comprehension",
     "Mohio builds a list by asking for what it wants:\n"
     "    find cheap in items\n        where price is below 5\n    find: done\n"
     "or by walking one:\n"
     "    repeat each item in items\n        show item.name\n    repeat: done"),
    (r'^\s*for\b.*\bin\b.*:\s*$',
     "a for-loop header ending in a colon",
     "Mohio walks a collection with `repeat each`, and counts with `repeat N times`:\n"
     "    repeat each item in items\n        show item\n    repeat: done\n"
     "    repeat 3 times\n        show \"hi\"\n    repeat: done"),
    (r'^\s*with\b.*\bas\b.*:\s*$',
     "a with-block opening a file",
     "Mohio reads a file in one line, and names the result:\n"
     "    miofile.read \"notes.txt\" as notes\n    show notes"),
    (r'\bf"',
     "an f-string",
     "Mohio puts a value into text with double braces:\n"
     "    name \"Bo\"\n    show \"Hi {{ name }}\""),
    (r'=>',
     "an arrow function",
     "Mohio names its work:  task double\n        take n as int\n"
     "        give back (n * 2)\n    task: done"),
)


def _humanise_allowed(allowed):
    """Turn the parser's terminal set into the few categories a reader can act on.

    NOT a dump. `allowed` holds hundreds of names like MIOHTTP_DELETE and AS_UC, and printing
    them is the mistake the end-of-input message already refused to make: the compiler's insides
    handed to somebody who did not write a compiler. What is useful is the SHAPE of what could
    come next, so the set is asked a few yes/no questions instead of being listed.
    """
    if not allowed:
        return []
    names = {str(a) for a in allowed}
    out = []
    if names & {'STRING', 'NUMBER', 'SIGNED_NUMBER', 'TRUE', 'FALSE'}:
        out.append("a value, such as a number or a piece of text in double quotes")
    if names & {'NAME', 'DOTTED_NAME'}:
        out.append("a name")
    if names & {'DONE', 'CLOSER'}:
        out.append("the block's closer, written `<verb>: done`")
    if names & {'NEWLINE', '_NL'}:
        out.append("the end of the line")
    return out


def _explain_unexpected(e, source, line, col):
    """The teacher-less message, given something to teach.

    Returns (what_was_written, guidance) or None. Reads the offending LINE first, because a
    familiar habit is worth naming directly, and falls back to what the parser would have
    accepted there.
    """
    import re as _re
    # THE REPORTED LINE FIRST, THEN ITS NEIGHBOURS -- AND NOTHING FURTHER. A statement does not
    # end at the line break, so looking only where the error points finds nothing when a wrapped
    # line carries the habit. That is why this looks past the reported line at all.
    #
    # IT USED TO LOOK AT EVERY LINE IN THE FILE, and that made it lie. The guidance opens with
    # "This line uses square brackets", so a `show colors[0]` on line 7 attached that sentence to
    # a parse error on line 1 that has no bracket in it -- measured, and reproduced twice before
    # this. For a reader whose only teacher is the error message, a confidently wrong hint is
    # worse than no hint: it sends them to fix something that is not there while the real mistake
    # stays on screen.
    #
    # A WINDOW, because the justification only ever reached as far as a wrapped statement. Two
    # lines either side covers that and cannot reach across a file.
    _NEAR = 2
    snippet = _source_snippet(source, line) if line else None
    _masked = _mask_noncode(source).split("\n")
    if line:
        _lo = max(0, line - 1 - _NEAR)
        _near_lines = _masked[_lo:line + _NEAR]
    else:
        # No line to anchor to. Naming a habit from an arbitrary line would be the same false
        # statement, so this falls through to the parser's own account of what it expected.
        _near_lines = []
    for candidate in ([snippet] if snippet else []) + _near_lines:
        if not candidate or not candidate.strip():
            continue
        for pattern, wrote, guidance in _FOREIGN_PUNCTUATION:
            if _re.search(pattern, candidate):
                return (wrote, guidance)
    cats = _humanise_allowed(getattr(e, 'allowed', None))
    if cats:
        if len(cats) == 1:
            return (None, "At this point Mohio was expecting " + cats[0] + ".")
        return (None, "At this point Mohio was expecting one of: "
                + "; ".join(cats) + ".")
    return None


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
    # A quote that is never closed swallows the rest of the file and kills the parse at
    # end-of-input, so it arrives here with no line, no caret, and an empty expected-list.
    # It is the commonest typo there is, so it is recovered before anything else.
    q_line, q_col = _find_unterminated_string(source)
    if q_line:
        snippet = _source_snippet(source, q_line)
        print(f"  {red('x')} This text is opened with a quote that is never closed.")
        if snippet is not None:
            print(f"\n  {dim(str(q_line) + ' |')} {snippet}")
            print(f"  {dim('  |')} {' ' * (q_col - 1)}{red('^')}")
        print(f"\n    The quote marked above opens a piece of text, and nothing closes it, so "
              f"everything")
        print(f"    after it is read as part of that text and the file runs out before it ends.")
        print(f"    Close it on the same line: text in Mohio never runs past the end of its "
              f"line.\n")
        return

    # A word that used to be Mohio dies on a character further along the line that had nothing
    # to do with it. Name the word the person actually typed, and what replaced it.
    word, w_line, guidance = _find_retired_form(source)
    if word:
        print(f"  {red('x')} {bold(word)} is not a Mohio word any more.")
        _echo_line_if_new(source, w_line, line)
        print()
        for g in guidance.splitlines():
            print(f"    {g}")
        print()
        return

    # A case transform spelled the way another language spells it. The vocabulary here is richer
    # than the one most people arrive with, which is why it gets missed: the word they know is
    # not the word, and what they got back was a character.
    _wrong, _meant = _find_case_near_miss(source)
    if _wrong:
        print(f"  {red('x')} {bold(_wrong)} is not a Mohio word.")
        # NEVER ANSWER `did you mean` WITH A FORM THAT DOES NOT RUN. `as.title` is the nearest
        # spelling to `as.capitalize` and it has no executor, so suggesting it plainly would send
        # the reader into a runtime error -- and the guidance below already says it does not work,
        # which would make one message say two opposite things.
        if _meant in _CASE_UNWIRED:
            print(f"\n    The nearest Mohio word is {bold(_meant)}, and it is ACCEPTED BUT NOT "
                  f"WIRED -- it would check clean and stop at runtime.")
            print(f"    For something that works today, use {bold('as.uppercase')} or "
                  f"{bold('as.lowercase')}.")
        else:
            print(f"\n    Did you mean {bold(_meant)}?")
        for _g in _CASE_GUIDANCE.splitlines():
            print(f"    {dim(_g)}")
        print()
        return

    # A construct that IS built, written in a form nothing accepts. The error it produced named
    # a character on a later line and never mentioned the construct, so a working feature was
    # unreachable to anyone who had not read the grammar.
    uns, u_line, how = _find_unspellable_construct(source)
    if uns:
        print(f"  {red('x')} {bold(uns)} is built, but this is not how it is written.")
        _echo_line_if_new(source, u_line, line)
        print()
        for g in how.splitlines():
            print(f"    {g}")
        print()
        return

    # A word the grammar reserves and nothing implements. It fails on some unrelated character
    # further down (`do.every` reports "No terminal matches 'd'" against its own closer two
    # lines later), which reads as a mistake in the program. It is not one: there is no way to
    # write this today, and saying so is different from saying it is wrong.
    resv, r_line, what = _find_reserved_unbuilt(source)
    if resv:
        # The four words are `do.every`, `do.after`, `do.unless` and `do.encrypt`, each tracked
        # in the living backlog. Named here in full because the message interpolates
        # whichever one was written, so the words themselves appear nowhere else in this file.
        print(f"  {red('x')} {bold(resv)} is declared but not yet built, so nothing can run it.")
        _echo_line_if_new(source, r_line, line)
        print(f"\n    The word is reserved in the grammar for {what} and no part of the "
              f"compiler")
        print(f"    consumes it yet, so this is not a mistake in your program: there is no "
              f"spelling")
        print(f"    of it that works in this build.")
        print(f"    A repeating job can be DECLARED today with a `mioschedule` block, which "
              f"registers")
        print(f"    it for an external driver to run; it does not start a timer by itself.\n")
        return

    h_line = _find_hash_comment(source)
    if h_line:
        snippet = _source_snippet(source, h_line)
        print(f"  {red('x')} A comment in Mohio starts with {bold('//')}, not with #.")
        _echo_line_if_new(source, h_line, line)
        print(f"\n    Write it as:  // {snippet.strip().lstrip('#').strip() if snippet else 'your note'}")
        print(f"    For several lines at once, wrap them in  /* ... */\n")
        return

    cap, c_line = _find_capitalised_opener(source)
    if cap:
        print(f"  {red('x')} Mohio words are written in lower case, so {bold(cap)} is not read "
              f"as {bold(cap.lower())}.")
        _echo_line_if_new(source, c_line, line)
        print(f"\n    Write {bold(cap.lower())} on line {c_line} and the rest of the block will "
              f"be understood.")
        print(f"    The error may have been reported further down: a capitalised word stops the "
              f"line")
        print(f"    being recognised, and the first thing that cannot be read comes later.\n")
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

    # THE EMPTY MESSAGE, AT ITS ROOT. Everything above recovers a SPECIFIC end-of-input cause.
    # What was left when none of them matched was the worst message the compiler produced:
    #
    #     Unexpected end-of-input. Expected one of:
    #
    # and nothing after the colon. The parser's own text runs to about fifty lines, with the
    # word "Expected" on the first and the expected tokens on all the rest, and the line above
    # keeps only the first. So the list was not missing, it was cut off, and the reader was
    # shown the promise of an explanation with the explanation removed.
    #
    # The list itself is not the fix. It is fifty entries of CONCAT_OP and MASK_ALL, which
    # names the compiler's insides to somebody who did not write a compiler. What is useful is
    # the one thing the parser does know: the file ended in the middle of something. So say
    # that, and point at the last line there was, which is where the reader has to look.
    if "end-of-input" in msg.lower() or "end of input" in msg.lower():
        code_lines = [(n, raw) for n, raw in enumerate(_mask_noncode(source).split("\n"), 1)
                      if raw.strip()]
        print(f"  {red('x')} The file ended while this was still unfinished.")
        if code_lines:
            last_no = code_lines[-1][0]
            snippet = _source_snippet(source, last_no)
            if snippet is not None:
                print(f"\n  {dim(str(last_no) + ' |')} {snippet}")
            print(f"\n    The compiler read to the end of the file still waiting for the rest "
                  f"of something.")
            print(f"    Whatever is unfinished is on or above line {last_no}: a statement that "
                  f"stops early,")
            print(f"    a block with no closer, or a piece of text with no closing quote.")
        print()
        return

    print(f"  {msg}\n")
    hint = _beginner_parse_hint(e, source, line)
    if hint:
        for hint_line in hint.splitlines():
            print(f"    {dim(hint_line)}")
        print()
        return
    # THE MESSAGE THAT TAUGHT NOTHING, GIVEN SOMETHING TO TEACH. `No terminal matches 'X'` was
    # 73 of 90 ordinary newcomer lines: it names a character and stops. The parser knew the
    # terminal set at that point the whole time, and the line itself usually shows which habit
    # the reader brought with them.
    _explained = _explain_unexpected(e, source, line, col)
    if _explained:
        _wrote, _guidance = _explained
        if _wrote:
            print(f"    {dim('This line uses ' + _wrote + '.')}")
        for _g in _guidance.splitlines():
            print(f"    {dim(_g)}")
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
    print(f"  {str(e)}")
    # THE HINT WAS BEING THROWN AWAY HERE. Every runtime refusal carries three things by
    # design -- the project's own standard a few lines below `_Raise` says so: WHERE it is,
    # WHAT it is, and HOW to fix it. This printed only the first two. `str(e)` on a `_Raise`
    # is `error_name: message`, so the HOW never reached the person running the program, and
    # `format_runtime_error` had been carefully assembling a hint that nothing displayed.
    #
    # ONE PLACE, RATHER THAN REWORDING EVERY REFUSAL. The alternative was to fold each
    # refusal's guidance into its message, which would have fixed the ones anybody thought to
    # look at and left the rest exactly as they are: the raw-sql refusals were merely where
    # this was noticed, and the same silence applied to every `_Raise` in the compiler,
    # including the whole `_RUNTIME_HINT_TABLE` of per-category advice which no CLI user has
    # ever seen. Printing it here reaches all of them and keeps the structured split intact,
    # which the HTTP response payload already relies on.
    hint = getattr(e, 'hint', '') or ''
    if not hint:
        from mohio_interpreter import _RUNTIME_HINT_TABLE
        hint = _RUNTIME_HINT_TABLE.get(getattr(e, 'error_name', None) or '', '')
    if hint:
        for hint_line in str(hint).splitlines():
            print(f"    {dim(hint_line)}")
    print()


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
        return p.read_text(encoding="utf-8-sig")
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
    # THE WRAPPER IS NOT THE VALUE. Every runtime value is a MohioValue, and formatting one with
    # `%s` prints its repr: `print "hi"` reported `Result MohioValue('hi', 'string')`, showing a
    # pioneer the interpreter's own bookkeeping where their value belonged. Unwrapped here
    # because this is the display boundary; the double-wrapping that caused the other routes is
    # fixed at construction, and this is the remaining one where a bare wrapper is handed
    # straight to a format string.
    if type(result).__name__ == "MohioValue":
        try:
            result = result.to_python()
        except Exception:                                       # noqa: BLE001
            result = result.value
    if result is None:
        if verbose:
            print(dim("  (no response -- program completed without give back)"))
        return
    # A RESPONSE ENVELOPE, NOT MERELY A DICT. Unwrapping the value above means a saved ROW
    # now arrives here as a plain dict too, and printing one through the response branch
    # showed `Response` with an empty status and an empty body -- a row is not an
    # envelope. The envelope is the thing that carries a status or a body, so that is what
    # is tested rather than the container type.
    if isinstance(result, dict) and ("status" in result or "body" in result):
        status = result.get("status", "")
        body   = result.get("body", "")
        if isinstance(status, int):
            status_str = green(str(status)) if status < 300 else \
                         yellow(str(status)) if status < 400 else red(str(status))
        else:
            status_str = str(status)
        print(f"\n  {bold('Response')}  {status_str}  {body}")
        # The HOW, when the envelope carries one. Printed under the response rather than folded
        # into the body, so the body stays exactly the string several tests assert on.
        _hint = result.get("hint") or ""
        if _hint:
            for _hint_line in str(_hint).splitlines():
                print(f"    {dim(_hint_line)}")
    else:
        print(f"\n  {bold('Result')}  {result}")
    print()


# -- Shared parse + validate step ----------------------------------------------

def _parse_and_validate(source, filename, verbose=False):
    """Parse source and run compile-time validation.

    Returns (tree, ctx) or exits on hard failure. ctx.errors is populated if validation
    fails; ctx.warnings are always printed.

    This text used to sit further down, below the first executable lines, where Python
    reads it as a statement that evaluates a string and discards it rather than as the
    function's documentation. It was moved up so `help(_parse_and_validate)` shows it.
    """
    # Per-file state. The missing-pack hint is set during THIS compile and must not survive it:
    # a stale hint would attach "that pack is NOT INSTALLED" to an unrelated error in a later
    # file, which is a confident wrong answer -- the worst kind of diagnostic.
    globals().pop('_LANGMAP_MISSING_HINT', None)

    # THE LEADING BOM IS STRIPPED AT THE READ, not here. A single leading U+FEFF is file
    # metadata, not code: Windows tools add one by default (PowerShell `Out-File -Encoding
    # utf8`, Notepad, some editors), so a newcomer would otherwise get a line 1 col 1 non-ASCII
    # error for an invisible character they never typed. Every read of a .mho file opens it as
    # utf-8-sig, which removes exactly one leading BOM and is plain utf-8 otherwise.
    #
    # It used to be stripped HERE, and that was the bug. This function strips its own local
    # copy, so the caller kept the marked string and handed it to the snippet printer, which
    # then had to encode a U+FEFF onto a console that has no code for one.
    #
    # Stripping in BOTH places would be worse than either: two leading BOMs would both vanish,
    # when the rule is exactly one. The second is a character the author actually has, and it
    # still fails loud below, which `tests/test_bom_strip.py` requires and caught when this
    # briefly stripped twice.
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

    # -1a''. Sector hierarchy moved from dots to commas (2026-08-25). Checked BEFORE the parser
    # runs, because the parser's own answer is "No terminal matches '.'", which tells a coder
    # nothing about what changed or what to write instead. The levels and their order are
    # unchanged; only the separator moved, because a dot is a plausible character inside a
    # sector NAME and would silently split one into levels nobody asked for.
    import re as _re_sector

    # The `page` BLOCK was removed (2026-08-25). Checked before the parser, because the parser's
    # answer is a bare "no terminal matches" that says nothing about what changed. The WORD stays
    # reserved, so this is a migration message rather than an unknown-word error.
    _page_block = _re_sector.compile(
        r'^\s*page\b(?!\s*:)\s*(?:[A-Za-z_][A-Za-z0-9_]*)?\s*(?:at\b|$)')
    for _pln, _praw in enumerate(source.split(chr(10)), 1):
        if _page_block.match(_praw):
            print(f"  Syntax error  {filename}")
            print(f"  {_pln} | {_praw.rstrip()}")
            print(f"  x the `page` block was removed. A file serves itself now.")
            print(f"    Drop the `page ... page: done` wrapper: a file in a served folder answers")
            print(f"    at its own name with whatever it renders, shows, or gives back.")
            print(f"    For a different address, mount it in a `map route` block.")
            print(f"    For a request handler, use `listen for ... request for ... at /path`.")
            print(f"    The page CLASSIFICATION model (public:/private:/hidden:/authorize:) is")
            print(f"    unchanged -- that is access control, and separate from this.")
            sys.exit(1)

    _sector_dotted = _re_sector.compile(
        r'^\s*sector\s*:\s*([A-Za-z][A-Za-z0-9_-]*(?:\s*\.\s*[A-Za-z][A-Za-z0-9_-]*)+)\s*$')
    for _ln, _raw in enumerate(source.split(chr(10)), 1):
        _m = _sector_dotted.match(_raw)
        if _m:
            _levels = [p.strip() for p in _m.group(1).split('.')]
            print(f"  Syntax error  {filename}")
            print(f"  {_ln} | {_raw.rstrip()}")
            print(f"  x sector levels are separated by commas, not dots.")
            print(f"    Write: sector: {', '.join(_levels)}")
            print(f"    The levels and their order are unchanged -- only the separator moved, so")
            print(f"    a dot inside a sector name can never be mistaken for a level boundary.")
            sys.exit(1)

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
            sub_src = open(target, encoding='utf-8-sig').read()
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
    j_src = open(journey_path, encoding='utf-8-sig').read()
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
        if not interp._db:
            # No `connect` declaration succeeded, which for a program with none at all is
            # simply "this app is database-free." It must stay that way regardless of what
            # DATABASE_URL happens to be set to -- Railway/Fly/Heroku set it project-wide,
            # so the simplest connect-less app would otherwise inherit a Postgres DSN and
            # setup_test_db()/seed_db()'s raw sqlite3.connect() on that value crash-loops
            # with SQLite's own "unable to open database file", on an app that never asked
            # for a database at all (T0-2). A later save/find in a genuinely database-free
            # program already fails loud on its own ("no database connected... declare one
            # first") -- nothing here needs to pre-empt that.
            if seed_data:
                _die("--seed was given, but this program declares no `connect` -- there is "
                     "no database to seed into. Add `connect db as sqlite` (or postgres / "
                     "mysql / mongodb) to the program, or drop --seed.")
        elif seed_data:
            interp.seed_db(seed_data)
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

    # A RUNTIME FAILURE MUST NOT EXIT 0. Measured 2026-09-02: `mio run` exited 0 for every
    # runtime failure -- an unhandled `raise`, a missing database connection, and the
    # compliance `audit.no_durable_store` refusal alike. So a script, a CI step or a pioneer
    # running `mio run` in a pipeline could not tell a refused program from a clean one, which
    # made every loud runtime refusal in the language silent at the process level.
    #
    # Keyed on "did the RUNTIME fail", not on the status number. A first version of this keyed
    # on `status >= 500` and was wrong in a way the cross-lane sweep caught: a program that
    # deliberately answers `give back 503 "AI unavailable"` from an `on.failure` handler has
    # WORKED -- it handled an outage exactly as its author wrote it -- and it was exiting 1.
    # That contradicted this change's own rationale for leaving 4xx alone.
    # The discriminator is the FAILURE ENVELOPE, which `format_runtime_error` builds and nothing
    # else does: it carries `code` and `trace`. A raise, a refusal and an internal error all
    # produce it; a deliberate `give back` of any status never does.
    # `sys.exit`, not `return`: the dispatcher calls `fn(args)` and DISCARDS the return value,
    # so returning a code here would have been its own silent no-op. It re-raises SystemExit
    # untouched, which is the path every other command already uses to signal a refusal.
    if isinstance(result, dict) and result.get('_mohio_runtime_error'):
        sys.exit(1)


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

    # THE EXPORT READS SQLITE AND ONLY SQLITE, and it has to say so for every other engine
    # rather than one of them. A postgres url was named and refused; a mysql one fell past this
    # to the file-exists check below and came back as `database not found at mysql://...`, which
    # sends somebody looking for a missing file when the real answer is that this command cannot
    # read their database at all. The learned-language store itself is SQLite-only, which is the
    # reason underneath both and is tracked separately.
    if db_path and '://' in db_path and not db_path.startswith('sqlite'):
        _scheme = db_path.split('://', 1)[0]
        _die(f"the applang export reads a SQLite database, and this is {_scheme}. "
             f"Point --db at the SQLite file that holds applang_map. "
             f"Exporting from {_scheme} is not built: the learned-language store is "
             f"SQLite-only today.")

    if not os.path.exists(db_path) and db_path != ':memory:':
        _die(f"database not found at {db_path}")

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        # Check table exists
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='applang_map'"
        )
        if not cur.fetchone():
            _die("applang_map table not found.\n"
                 "  Run your Mohio app with an applang block first to build the corpus.")

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
        with open(meta_path, 'w', encoding='utf-8') as f:
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
        _die(str(e))


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

        # THE HEADER DESCRIBES THE FILE IN HAND, so translating BACK to the base language
        # must take it off rather than leave the old one standing. A round trip
        # (en -> klingon -> en) produced English source still declaring
        # `// language: klingon`: the file said it was one thing while being another,
        # checked clean, and would have been translated a second time by anything that
        # trusted the declaration.
        import re as _re_hdr
        _to_base = str(to_lang).strip().lower() in ('en', 'english', 'canonical')
        if _to_base:
            translated = _re_hdr.sub(
                r'^//\s*(?:language|langmap|Translated from)\s*:.*\n', '', translated, flags=_re_hdr.M)
            translated = translated.lstrip(chr(10))
        elif '// language:' not in translated[:200]:
            header = (f'// language: {to_lang}\n'
                      f'// langmap: mohio_data/maps/en-{to_lang}.langmap\n'
                      f'// Translated from: {source_file.name}\n\n')
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
            source = _Path(target).read_text(encoding='utf-8-sig')
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
            source = Path(filepath).read_text(encoding='utf-8-sig')
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
            # STATIC detection, at startup, before a single request. A convention-served GET
            # is read-only; a page that changes state on a bare page view is reported HERE so
            # the developer sees it while starting the server, not when a crawler finds it.
            # The serve layer refuses it too -- this is the early warning, that is the
            # guarantee.
            try:
                from mohio_framework import (unsafe_on_get, ai_cost_on_get,
                                             ai_cost_warning, resolve as _rfw,
                                             SERVES_BY_CONVENTION)
                if _rfw(program) in SERVES_BY_CONVENTION:
                    # AI on a page view is ALLOWED (ruled 2026-08-24) -- it is
                    # corruption-safe, so re-running it on a repeat view cannot leave
                    # the app wrong. It is expensive, though, and that is the coder's
                    # call to make, so this warns and never refuses.
                    _ai = ai_cost_on_get(program)
                    if _ai:
                        print(f"  {yellow('!')}  {ai_cost_warning(url_path, _ai)}")
                    _bad = unsafe_on_get(program)
                    if _bad:
                        _verbs = ", ".join(f"{v} (line {ln})" if ln else v for v, ln in _bad)
                        print(f"  {yellow('!')}  {url_path} -- a convention-served page is "
                              f"read-only, and this one changes state: {_verbs}. It will "
                              f"refuse the GET. Move the change into a `listen for` handler.")
            except Exception:
                pass          # a reporting aid must never stop the server from starting
            print(f"  {green('v')}  {url_path}")
        except Exception as e:
            print(f"  {red('x')}  {url_path} -- {e}")

    if not programs:
        _die("No files compiled successfully.")

    # T1-MAP-EXTRACTION (2026-08-24): apply the `map` route classifier.
    #
    # Convention gets a file to its own name; `map` is where a developer says otherwise. A
    # MOUNT re-points an existing compiled file at a different address; a REDIRECT answers
    # instead of any file. Both are settled HERE, before the server is built, for the same
    # reason the framework is: routing is decided before a request arrives, never during one.
    #
    # A mount names the file the way the coder wrote it (`about.mho`, or `"blog/post.mho"`),
    # which is matched against the discovered files by relative path. A mount naming a file
    # that is not there is REFUSED, not skipped: it is a typo, and a silently-ignored mount
    # leaves the page answering at its convention address while the coder is looking for it at
    # the one they wrote.
    redirects = {}
    status_responses = {}
    try:
        from mohio_framework import (resolve_map, resolve_map_responses, MapError,
                                     _normalise_path)
        _mounts, redirects = {}, {}
        # Declared status responses are APP-level: a `[404] /page` in any map section answers
        # for the whole app, which is the point -- an unmatched route and a static file deleted
        # after deploy are the same class of miss, and only one of the two is visible to
        # `mio check` at compile time.
        status_responses = {}
        for _u, _prog in list(programs.items()):
            _m, _r = resolve_map(_prog)
            _mounts.update(_m); redirects.update(_r)
            status_responses.update(resolve_map_responses(_prog))
        if _mounts:
            _by_file = {}
            for _u, _fp in mho_files:
                _rel = os.path.relpath(_fp, str(directory)).replace(os.sep, "/")
                _by_file[_rel] = _u
                _by_file[os.path.basename(_rel)] = _u
            for _path, _src in _mounts.items():
                _key = str(_src).replace(os.sep, "/")
                _from = _by_file.get(_key)
                if _from is None:
                    _die(f"map: `{_src}` is mounted at {_path} but there is no such file in "
                         f"{directory}. Check the spelling, or drop the mount and let the file "
                         f"answer at its own name.")
                if _from in programs:
                    programs[_path] = programs[_from]
                    interps[_path]  = interps[_from]
                    if _path != _from:
                        programs.pop(_from, None); interps.pop(_from, None)
                    print(f"  {green('v')}  {_path} {dim(f'(map: mounted {_src})')}")
        for _src, (_dst, _code) in sorted(redirects.items()):
            print(f"  {green('v')}  {_src} {dim(f'(map: -> {_dst}, {_code})')}")
    except MapError as _me:
        _die(str(_me))

    # Build multi-route FastAPI app
    try:
        from mohio_server import create_multi_app
        app = create_multi_app(programs, interps, verbose=verbose, app_dir=directory,
                               redirects=redirects, status_responses=status_responses)
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
        # FIX-B9-2 (T1-SILENT-SWEEP-BATCH9): `serve` has no `--json` flag (confirmed against
        # its own subparser -- only `check` has one), so this suppression was always
        # unconditionally true here -- copy-pasted from `check`'s identical warning line
        # (commit 8627173 added the --json suppression to a block shared with `serve` without
        # checking which command it landed in). Removed the dead condition; serve always
        # prints its own startup warning count.
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
        if not interp._db:
            # See the identical guard + rationale in cmd_run (T0-2): no `connect` declared
            # means database-free, unconditionally -- DATABASE_URL must not change that.
            if seed_data:
                _die("--seed was given, but this program declares no `connect` -- there is "
                     "no database to seed into. Add `connect db as sqlite` (or postgres / "
                     "mysql / mongodb) to the program, or drop --seed.")
        elif seed_data:
            interp.seed_db(seed_data)

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
            # EVERY field, loose or table-owned. A `never store` field declared under a
            # `<name> as table` scope is still `never store`; walking the flat list alone
            # skipped it entirely once the Phase 2 hierarchy existed.
            for fld in (node.every_field() if hasattr(node, 'every_field')
                        else (getattr(node, 'fields', None) or [])):
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


def cmd_walk(args):
    """`mio walk <file.mho> [map]` -- walk every data map in a file and print each stage.

    A standalone terminal diagnostic: something is wrong, you run `mio walk paydata.mho`, and
    you see where it breaks -- without writing a program to look. A CLI subcommand, like
    `mio serve` and `mio check`, so it adds no language vocabulary.

    It is a RAW DUMP on purpose. It prints what is at every stage and flags a hop that broke;
    it does not decide which stage looks suspicious or what the fix is. Interpreting is a tool,
    and tools belong in the paid tier, built on this. The free capability is seeing clearly.
    """
    from pathlib import Path as _Path

    path = _Path(args.file)
    if not path.exists():
        _die(f"File not found: {args.file}")
    source = path.read_text(encoding='utf-8-sig')

    print(f"\n  {bold('mio walk')} {dim(f'v{VERSION}')} -- {bold(str(path))}")

    tree, ctx = _parse_and_validate(source, str(path), verbose=getattr(args, 'verbose', False))
    if ctx.errors:
        for e in ctx.errors:
            print(f"  {red('x')} {e}")
        sys.exit(1)
    from mohio_transformer_ast import transform as _transform
    program = _transform(tree, source)

    from mohio_interpreter import MohioInterpreter, MockAiRuntime
    interp = MohioInterpreter(ai=MockAiRuntime(), verbose=False,
                              db_path=_resolve_sqlite_db_path(str(path), args))
    interp.run_declarations(program)
    interp.shown = []

    # Run the file first so the stages hold real values. A walk of a program that has never run
    # shows empty everywhere, which is true but not useful -- the point is what is actually
    # sitting at each stage. A failure here is REPORTED and the walk continues, because seeing
    # the stages is exactly what you came for when something is broken.
    run_error = None
    try:
        interp.run(program)
    except Exception as e:
        run_error = f"{type(e).__name__}: {e}"

    chains = interp.walk_all_maps()

    if not chains:
        print(f"  {yellow('!')} No data map found in this file.")
        print(f"    A map is declared with `map <name> ... data ... map: done`.")
        if run_error:
            print(f"  {red('x')} while running: {run_error}")
        sys.exit(1)

    wanted = getattr(args, 'map_name', None)
    total = 0
    for map_name, by_chain in sorted(chains.items()):
        if wanted and map_name != wanted:
            continue
        print(f"\n  {bold('map ' + (map_name or '(unnamed)'))}")
        for chain_key, stages in by_chain.items():
            print(f"    {dim('chain')} {chain_key}  ({len(stages)} stages)")
            for st in stages:
                arrow = {'forward': '->', 'bidirectional': '<->', 'reverse': '<-'}.get(
                    st.get('arrives', ''), '  ')
                value = st.get('value', '')
                kind = st.get('kind', '')
                mark = green('v') if (value != '' or kind == 'transform') else yellow('!')
                print(f"      {mark} {arrow:3} {st.get('position')}. {st.get('name'):28} "
                      f"{dim('[' + kind + ']'):18} = {value!r}")
                total += 1
    print(f"\n  {total} stage(s) walked.")
    if run_error:
        # A broken hop names itself in the message flow already produced; the walk above shows
        # exactly how far the data got before it stopped.
        print(f"  {red('x')} the run stopped: {run_error}")
        sys.exit(1)
    print()

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
        # `args.all` MUST be cleared before recursing. Leaving it set meant every recursive
        # call re-entered this same branch, re-globbed all the files and looped again -- so
        # `mio check --all` never checked anything: it recursed until the stack ran out and
        # printed "Internal error ... maximum recursion depth exceeded". Found 2026-09-01 by
        # running the flag CLAUDE.md documents ("--all checks every file"); it had never
        # worked. A one-file check is what each iteration was always meant to be.
        args.all = False
        for f in files:
            args.file = f
            try:
                cmd_check(args)
            except SystemExit as e:
                if e.code != 0:
                    failed.append(f)
        args.all = True
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
                _parse_and_validate(Path(f).read_text(encoding="utf-8-sig"), f,
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
            cfg = _json.loads(config_path.read_text(encoding="utf-8-sig"))
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
    except Exception as _e:
        # T1-SILENT-SWEEP-BATCH6-10 (2026-08-15): used to `pass` here (advisory only) with
        # no warning at all -- unlike every OTHER advisory-hiccup site in this same
        # function (Layer 3 scanners, the never-store/PCI guard, the upload lint), which all
        # already call _scan_incomplete_warn. A genuine compiler bug in Layer 2 (not a real
        # MohioError -- those are handled above and already surface a real check error) used
        # to silently skip EVERY Layer-3 check that follows (never-store/PCI guard,
        # upload-size lint, handler-closer lint) with `mio check`'s summary still reporting
        # clean. Still never breaks `mio check` itself (advisory, unchanged) -- just no
        # longer invisible.
        _scan_incomplete_warn("Layer 2 AST construction", _e)
        _program = None
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
                    for fld in (node.every_field() if hasattr(node, 'every_field')
                                else (getattr(node, 'fields', None) or [])):
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

    # FIX-B9-4 (T1-SILENT-SWEEP-BATCH9): `install-hooks` is meant to run inside a USER'S OWN
    # app repo, which never contains a copy of the mohio compiler -- `mio` is a real pip
    # console-script (pyproject.toml: `mio = "mio:main"`), so a hardcoded repo-relative
    # `compiler/mio.py` (leftover from some other layout) could never resolve there. Bake in
    # the exact interpreter and mio.py path currently running `install-hooks` itself -- correct
    # whether this is a dev checkout or a pip-installed environment, and needs nothing present
    # in the target repo at all. Kept as two separately-quoted variables (not one combined
    # MIO_CMD string) so each is independently double-quoted AT THE POINT OF USE below --
    # POSIX `sh` word-splits an unquoted variable expansion on whitespace regardless of quote
    # characters embedded in its value, so a single combined string would break the moment
    # either path contains a space (e.g. Windows "Program Files").
    mio_python = str(sys.executable)
    mio_script = str(Path(__file__).resolve())

    # -- pre-push hook ----------------------------------------
    pre_push_content = f"""#!/bin/sh
# Mohio pre-push hook -- installed by mio install-hooks
# Runs mio check on all .mho files before pushing.
# Remove this file to disable: rm .git/hooks/pre-push

echo ""
echo "  mio pre-push check..."

FAILED=0
MIO_PYTHON="{mio_python}"
MIO_SCRIPT="{mio_script}"

# Find all .mho files in the repo
for f in $(find . -name "*.mho" -not -path "./.git/*" 2>/dev/null); do
    result=$("$MIO_PYTHON" "$MIO_SCRIPT" check "$f" {security_flag} 2>&1)
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
    pre_push_path.write_text(pre_push_content, encoding="utf-8")
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
MIO_PYTHON="{mio_python}"
MIO_SCRIPT="{mio_script}"

# Only check staged .mho files
for f in $(git diff --cached --name-only --diff-filter=ACM | grep ".mho$"); do
    if [ -f "$f" ]; then
        result=$("$MIO_PYTHON" "$MIO_SCRIPT" check "$f" {security_flag} 2>&1)
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
        pre_commit_path.write_text(pre_commit_content, encoding="utf-8")
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
    config_path.write_text(_json.dumps(mio_config, indent=2), encoding="utf-8")
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

    # FIX-B9-5 (T1-SILENT-SWEEP-BATCH9): this used to call `_cmd_serve_directory(args, path,
    # verbose)` -- `verbose` was never defined anywhere in this function (no local assignment,
    # `schema`'s own subparser has no --verbose flag), a guaranteed NameError -- and even a
    # defined `verbose` wouldn't have helped, because `_cmd_serve_directory` is the HTTP
    # SERVER's directory-mode route scanner, copy-pasted in from cmd_serve/cmd_check without
    # adapting the call target. Directory-mode schema generation is a genuine, non-trivial
    # design decision (one manifest per file? one merged manifest for the whole app? how are
    # outputs named?) that hasn't been made, not a typo to patch -- fail loud as not-yet-built
    # instead of routing into an unrelated command. Single-file `mio schema <action> file.mho`
    # is unaffected; it never reaches this branch.
    if path.is_dir():
        _die(f"mio schema {action} does not support a directory yet ({filename}) -- "
             f"run it against one .mho file at a time. Left silent it would have crashed "
             f"with an internal NameError, or routed into the http server's directory "
             f"scanner and started serving instead of generating a schema. Tracked for a "
             f"future release.")

    source = _read_source(path)

    from mohio_schema import (generate_schema, write_schema, read_schema,
                               find_schema_file, validate_field_references)

    if action == 'generate':
        print(f"\n  {bold('mio schema generate')}  {dim(filename)}\n")
        schema = generate_schema(source, filename)
        # T1-SILENT-SWEEP-BATCH7 (2026-08-15): generate_schema() already carries a real
        # parse/transform/extraction failure in the returned dict's 'error' key (its own
        # design, a deliberate graceful-degrade shape) -- but this call site never checked
        # it, so a genuine failure (e.g. normalize_type's new fail-loud on an unrecognized
        # field type) silently produced an empty schema, "Schema generated" with 0 tables,
        # and a written .mhoschema file, exit 0. Surfaced here instead of writing a broken
        # manifest and claiming success.
        if schema.get('error'):
            _die(f"Could not generate a schema for {filename}: {schema['error']}",
                 exit_code=1)
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
    text  = GRAMMAR_FILE.read_text(encoding="utf-8-sig")
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


def cmd_writes(args):
    """mio writes FILE.mho -- show what each write in a program MEANS, normalized.

    Phase 1 of the write planner. Eight write node types call the destination `target` on two of
    them and `source` on four, and insert / update / delete / upsert are normalized nowhere, so a
    planner would be reading eight divergent shapes. WriteIntent is the first normal form, and
    this is the command that makes it visible.

    It plans nothing and changes nothing. A write that cannot be faithfully represented is
    printed as NOT NORMALIZED with the reason, rather than approximated -- an IR that guesses is
    worse than none, because it reads as knowledge.
    """
    filename = args.file
    if not filename:
        _die("Which file? Usage: mio writes <file.mho>", exit_code=3)
    path = Path(filename)
    if not path.exists():
        _die(f"File not found: {filename}", exit_code=3)
    source = _read_source(path)

    tree, ctx = _parse_and_validate(source, filename, False)
    if ctx.errors:
        print(f"\n  {bold(red('Build failed'))}  {dim(filename)}\n")
        for e in ctx.errors:
            _print_compile_error(e, source, filename)
        sys.exit(1)

    from mohio_transformer_ast import transform as ast_transform
    from mohio_write_intent import lower_program, classify_program, program_activates_sector
    program = ast_transform(tree, source)

    classifier = classify_program(program)
    intents = lower_program(program, classifier)

    print()
    if not intents:
        print(f"  {dim('No writes in ' + str(filename) + '.')}\n")
        return
    _tagged = sorted(classifier.fields_with('encrypted'))
    print(f"  {bold('WriteIntent')}  {dim(str(filename))}   "
          f"{len(intents)} write(s)")
    if _tagged:
        print(f"  {dim('regulated fields declared: ' + ', '.join(_tagged))}")
    if program_activates_sector(program):
        print(f"  {dim('a compliance sector is active: every data write is regulated')}")
    print()
    for w in intents:
        head = f"  line {w.line:>4}  {bold(w.verb)}"
        if not w.normalized:
            print(f"{head}  {red('NOT NORMALIZED')}")
            print(f"            {dim(w.not_normalized_reason)}")
            print()
            continue
        print(f"{head}  {w.operation} -> {w.target.datasource}.{w.target.relation}")
        print(f"            rows {w.row_source}"
              + (f" (cardinality {w.cardinality})" if w.cardinality is not None else ""))
        if w.field_names:
            print(f"            fields {', '.join(w.field_names)}")
        if w.predicate_fields:
            print(f"            matched on {', '.join(w.predicate_fields)}")
        print(f"            result {w.result}"
              + (f" bound to `{w.result_bound_to}`" if w.result_bound_to else ""))
        print(f"            ordering {w.ordering}   failure {w.failure}   "
              f"transaction {w.transaction}")
        _c = w.compliance
        _mark = red("REGULATED") if _c.regulated == "yes" else dim("regulated: unknown")
        print(f"            {_mark}   {dim(_c.why[:78])}")
        print()
    print(f"  {dim('Every dependency question reads unknown: no def-use analysis exists yet,')}")
    print(f"  {dim('and unknown forbids, which is the conservative rule the roadmap specifies.')}")
    print()


def cmd_audit(args):
    """mio audit verify|head|verify-anchors|relay [db] -- inspect and complete the audit trail.

    Reads the durable store directly (DATABASE_URL, or a path given as the argument). It does NOT
    run the program: an auditor should be able to check the records without executing the code
    that produced them, and requiring a run would mean the thing under inspection gets to act
    first.

    `verify` walks each audit log and reports whether the chain is intact -- an altered, deleted,
    or reordered record breaks it and is named. `head` prints each log's current chain head, which
    is the value an anchoring scheme publishes: a head that no longer matches a previously
    published one is how truncation and genesis-restart become visible, since neither of those
    breaks the chain internally.

    `relay` finishes what a crash interrupted. A regulated write commits its data and a durable
    envelope describing it in one transaction, and then delivers the record to the trail; a
    process killed in between leaves the row, the evidence, and no record. This reads the
    evidence and writes the records, and a record already delivered is recognised and skipped,
    so running it twice changes nothing the first run did not already do.
    """
    action = getattr(args, "audit_action", "verify") or "verify"
    explicit_file = getattr(args, "file", None)
    target = explicit_file or os.environ.get("DATABASE_URL", "")
    if not target:
        _die("No database given. Pass a path (`mio audit verify app.db`) or set DATABASE_URL.",
             exit_code=3)

    from mohio_interpreter import MohioInterpreter, _make_db_runtime, _sniff_driver
    # A positional argument is USUALLY a literal sqlite path (the documented form,
    # `mio audit verify app.db`), and it was treated as one unconditionally (T0-2: before that,
    # every target was opened with a raw sqlite3.connect() regardless of scheme, which is the
    # same "unable to open database file" crash the interpreter's own setup fallback had).
    #
    # Treating it unconditionally left the same crash on the most natural thing an auditor
    # types: `mio audit verify postgresql://...`. That forced the sqlite driver at a Postgres
    # url and answered `unable to open database file`, which reads as a missing file and is
    # really a refusal to look at the database that was named. A url carries its own driver, so
    # it is read either way now, and a plain path still means sqlite exactly as before.
    driver = _sniff_driver(target) if '://' in str(target) else (
        'sqlite' if explicit_file else _sniff_driver(target))
    try:
        sink = _make_db_runtime(driver, target,
                                url=target if '://' in str(target) else None)
    except Exception as e:
        _die(f"Could not open the audit store at {target}: {e}", exit_code=3)

    it = MohioInterpreter()

    if action == "relay":
        # BEFORE THE LOG CHECK, DELIBERATELY. A process killed between committing a regulated
        # write and delivering its record may have left no audit log at all -- if it was the
        # first write, the log was never created. Refusing to relay because there is no log is
        # refusing exactly when the relay is most needed.
        try:
            delivered, already = it.audit_relay_drain(sink)
        except Exception as e:
            _die(f"Could not deliver the owed audit records: {e}", exit_code=3)
        if delivered or already:
            print(f"\n  delivered {delivered} record(s) that were owed; "
                  f"{already} were already in the trail.\n")
        else:
            print(f"\n  {dim('Nothing owed: every regulated write in ' + str(target) + ' has its record.')}\n")
        return

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
            with open(anchors_path, encoding="utf-8-sig") as fh:
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

# -- mio new / mio init: the starter project -------------------------------------------
#
# THE SAME STARTER MOHIO HOME WRITES. Until now the two ways into Mohio started from
# different places. The installer scaffolds a clean hello-world. A pioneer who ran
# `pip install mohio` got a compiler and nothing to point it at, so the natural next move
# was to clone the project and start from whatever files were lying in it -- which in this
# repository means a deploy configuration aimed at the Zork demo with a billed AI switch
# turned on. That is this project's own platform demo, not anybody's starting point.
#
# Mirrored from Mohio Home's `_write_standard_scaffold` (read from app/app.py, not
# remembered): journey.mho and index.mho with the same content, _project.json with the same
# three fields, and the same .gitignore.
#
# ONE DELIBERATE ADDITION, which is why this is not a byte-for-byte copy. The installer
# writes no deploy files at all, because deploying is the platform's job there: Mohio Home
# zips the project folder and uploads it, and its own exclusion list deliberately keeps a
# Dockerfile out of that zip. A pioneer who installed from pip has no platform behind them,
# so this starter carries its own Procfile and its own README, both pointing at THIS
# project's index.mho, and neither one mentioning --ai.

_STARTER_JOURNEY = 'lock site_name "{}"\n'

_STARTER_INDEX = (
    "connect db as sqlite from env.DATABASE_URL\n"
    "\n"
    "shape Home\n"
    "shape: done\n"
    "\n"
    "listen for\n"
    "    request for sh.Home at /\n"
    "        render\n"
    "            <h1>{{site_name}}</h1>\n"
    "            <p>Your new project is running.</p>\n"
    "        render: done\n"
    "    request: done\n"
    "listen: done\n"
)

# The same file Mohio Home writes beside a project's repository. .mohio/ is Mohio's own
# local database and run state, not source, and the other two are operating-system clutter.
_STARTER_GITIGNORE = ".mohio/\n.DS_Store\nThumbs.db\n"

# NO --ai. A real AI decision is a billed call to a provider, so it is something a pioneer
# turns on deliberately, never something a starter turns on for them. The file says so where
# somebody editing it will read it, rather than only in a document.
_STARTER_PROCFILE = (
    "# How this project starts when a host runs it. It serves THIS project's index.mho.\n"
    "#\n"
    "# There is no --ai here on purpose. A real ai.decide call is billed by the provider, so\n"
    "# it is turned on deliberately: add --ai to the line below, and set ANTHROPIC_API_KEY.\n"
    "# Without it every ai.decide still runs, against a local stand-in, and costs nothing.\n"
    "web: mio serve index.mho --port $PORT --host 0.0.0.0\n"
)

_STARTER_README = """# {display}

A Mohio project. Two files hold it: `journey.mho` names it, and `index.mho` is the page
served at `/`.

## Run it on this computer

    mio serve index.mho

Then open http://127.0.0.1:8080 in a browser. Change `index.mho`, stop the server with
Ctrl-C, and start it again to see the change.

To run a program once instead of serving it:

    mio run index.mho

## Check it without running it

    mio check index.mho

This reads the whole program and reports every error and warning it can find, and it runs
in about a second. It is the fastest way to find out whether something is wrong.

## Deploy it

`Procfile` is the start command a host reads. It already points at this project's
`index.mho`, on whatever port the host hands it.

Anything that reads a Procfile (Railway, Heroku, Render, Fly) needs no further setup:
push this folder, and the host installs `mohio` from `requirements.txt` and runs the
Procfile line. Set `DATABASE_URL` in the host's environment if you want a database that
survives a restart; without it the project uses a local SQLite file.

**AI decisions are off.** A real `ai.decide` call is billed by the provider. Every
`ai.decide` in this project still runs without it, against a local stand-in, and costs
nothing. When you want the real thing, add `--ai` to the Procfile line and set
`ANTHROPIC_API_KEY` in the host's environment.
"""

_STARTER_REQUIREMENTS = "mohio>={}\n"


def _starter_slug(display_name):
    """Lowercase, spaces to hyphens, anything else dropped. The same shape Mohio Home's own
    slugify produces, so a project created either way lands in a folder with the same name.

    RETURNS EMPTY rather than falling back to a name, and that is the one place this
    deliberately parts company with the installer. Mohio Home can fall back to "project"
    because the folder name is invisible there: it is derived, never typed, and never
    something a pioneer has to find again. Here it is a folder somebody is about to `cd`
    into, and a name made entirely of characters a folder cannot hold is a mistake worth
    saying out loud rather than answering with a word they did not choose."""
    lowered = display_name.strip().lower()
    spaced = re.sub(r"\s+", "-", lowered)
    stripped = re.sub(r"[^a-z0-9_-]", "", spaced)
    collapsed = re.sub(r"-{2,}", "-", stripped).strip("-")
    return collapsed[:40].strip("-")


def _starter_folder_for(display_name):
    """The folder a display name earns, or a refusal naming why it earned none."""
    slug = _starter_slug(display_name)
    if not slug:
        _die(f'There is nothing in "{display_name}" that a folder name can be built from. '
             f'A folder name keeps letters, digits, hyphens and underscores, and spaces '
             f'become hyphens. Give the project a name with at least one of those in it, '
             f'or name the folder yourself with --in.', exit_code=3)
    return slug


def _write_starter(project_dir, display_name, description=""):
    """Writes the starter into an existing, empty-enough directory. Returns the list of
    files written, in the order written, so the caller can show the pioneer what it did."""
    written = []

    def put(name, content):
        (project_dir / name).write_text(content, encoding="utf-8")
        written.append(name)

    # A display name with a double quote in it would close the string on the `lock` line and
    # hand the pioneer a parse error in a file they never typed. Mohio Home settles this the
    # same way: the quote becomes an apostrophe rather than an escape nobody asked for.
    put("journey.mho", _STARTER_JOURNEY.format(display_name.replace('"', "'")))
    put("index.mho", _STARTER_INDEX)
    put("_project.json", json.dumps(
        {"display_name": display_name, "description": description, "hidden": False},
        indent=4) + "\n")
    put(".gitignore", _STARTER_GITIGNORE)
    put("Procfile", _STARTER_PROCFILE)
    put("requirements.txt", _STARTER_REQUIREMENTS.format(VERSION))
    put("README.md", _STARTER_README.format(display=display_name))
    return written


def _starter_occupied(project_dir):
    """The names this scaffold would write, that are already there. Never overwrite a
    pioneer's own file: the whole point of a starter is the first thing in a folder, and a
    starter that silently replaced an index.mho somebody had been editing would be the worst
    possible way to learn that."""
    names = ["journey.mho", "index.mho", "_project.json", ".gitignore", "Procfile",
             "requirements.txt", "README.md"]
    return [n for n in names if (project_dir / n).exists()]


def _report_starter(project_dir, display_name, written, cd_hint):
    print()
    print(f"  {bold(display_name)}  {dim(str(project_dir))}")
    print()
    for name in written:
        print(f"    {name}")
    print()
    if cd_hint:
        print(f"  Next:   cd {cd_hint}")
        print(f"          mio serve index.mho")
    else:
        print(f"  Next:   mio serve index.mho")
    print(f"  Then open http://127.0.0.1:8080")
    print()
    print(dim("  Version history is not set up. `git init` here if you want it."))
    print()


def cmd_new(args):
    """Create a folder and put the starter in it."""
    display_name = args.name
    folder = args.folder if args.folder else _starter_folder_for(display_name)
    project_dir = Path(folder).resolve()

    if project_dir.exists():
        occupied = _starter_occupied(project_dir) if project_dir.is_dir() else ["(a file)"]
        if not project_dir.is_dir():
            _die(f"{project_dir} is a file, not a folder. Pick another name.", exit_code=3)
        if occupied:
            _die(f"{project_dir} already has {', '.join(occupied)} in it. "
                 f"A starter never overwrites a file that is already there. "
                 f"Pick another name, or run `mio init` in an empty folder.", exit_code=3)
    else:
        try:
            project_dir.mkdir(parents=True)
        except OSError as e:
            _die(f"Could not create {project_dir}: {e}", exit_code=3)

    written = _write_starter(project_dir, display_name, args.description or "")
    _report_starter(project_dir, display_name, written, folder)


def cmd_init(args):
    """Put the starter in the folder you are standing in."""
    project_dir = Path.cwd()
    display_name = args.name or project_dir.name
    occupied = _starter_occupied(project_dir)
    if occupied:
        _die(f"This folder already has {', '.join(occupied)} in it. "
             f"A starter never overwrites a file that is already there. "
             f"Run `mio init` in an empty folder, or `mio new <name>` to create one.",
             exit_code=3)
    written = _write_starter(project_dir, display_name, args.description or "")
    _report_starter(project_dir, display_name, written, None)


def cmd_help(args):
    print(f"""
  {bold('mio')} -- Mohio Language CLI  {dim(f'v{VERSION}')}

  {bold('USAGE')}

    mio new <name>                      Create a new project folder with a starter page
    mio init                            Put that same starter in the folder you are in
    mio run <file.mho>                  Execute a Mohio program
    mio run <file.mho> --verbose        Execute with trace output
    mio run <file.mho> --ai             Use real Anthropic API for ai.decide
    mio serve <file.mho>                Start HTTP server on port 8080
    mio serve <file.mho> --port 9000    Start on custom port
    mio serve <file.mho> --ai           Serve with real Anthropic API
    mio check <file.mho>                Validate -- all errors and warnings, no run
    mio check --all                     Validate every .mho file in the tree
    mio fmt <file.mho> [--write]        Normalize toward canonical Mohio
    mio generate <artifact>             Generate artifacts (e.g. training data)
    mio translate <file.mho> --to <lang>  Translate a program's natural-language layer
    mio schema <generate|check|show>    Work with .mhoschema files
    mio harvest [--output f.json]       Extract the current word inventory from the grammar (langmaps)
    mio schedule <file.mho>             Run any scheduled tasks that are due
    mio audit verify <db>               Check each audit log's hash chain is intact
    mio audit head <db>                 Print each log's chain head (the value you anchor)
    mio audit verify-anchors <db> --anchors f.json   Check the chain against published anchors
    mio audit relay <db>                Deliver audit records a crash left owed
    mio writes <file.mho>               Show what each write means, normalized
    mio warmup                          Pre-warm the parser cache
    mio install-hooks                   Install git pre-commit hooks
    mio version                         Print version information
    mio help                            Print this message

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

    # test
    ts = sub.add_parser("test", add_help=False)
    ts.add_argument("file")
    ts.add_argument("--db", dest="db", default=None)
    ts.add_argument("--memory", dest="memory", action="store_true")
    ts.add_argument("--verbose", "-v", action="store_true")
    ts.add_argument("--ai", action="store_true", default=False)
    ts.add_argument("--api-key", default=None, dest="api_key")

    # check
    c = sub.add_parser("check", add_help=False)
    c.add_argument("--security", action="store_true", help="Run full security compliance report")
    c.add_argument("--json",     action="store_true", help="Output structured JSON for agent consumption")
    c.add_argument("--fast",     action="store_true", help="Fast check: ASCII + reserved words only, skip full parse")
    c.add_argument("--all",      action="store_true", help="Check all .mho files in current directory tree")
    c.add_argument("--langmap",  action="store_true", help="List every keyword this file's langmap does not map (unmapped words fall back to English)")
    c.add_argument("file",       nargs="?",           help="File to check (omit with --all)")

    # walk -- terminal diagnostic for data maps
    wk = sub.add_parser("walk", add_help=False)
    wk.add_argument("file", help="The .mho file whose data maps to walk")
    wk.add_argument("map_name", nargs="?", default=None, help="Only this map")
    wk.add_argument("--memory", action="store_true", dest="memory",
                    help="Use a throwaway in-memory database")
    wk.add_argument("--verbose", "-v", action="store_true")

    # fmt
    fp = sub.add_parser("fmt", add_help=False)
    fp.add_argument("file")
    fp.add_argument("--write", "-w", action="store_true", help="Apply changes in place")
    fp.add_argument("--stdout", action="store_true", help="Print formatted source to stdout")

    # new / init -- the starter project
    nw = sub.add_parser("new", add_help=False)
    nw.add_argument("name")
    nw.add_argument("--in", dest="folder", default=None,
                    metavar="FOLDER", help="Folder to create (default: the name, lowercased)")
    nw.add_argument("--description", default=None)
    ini = sub.add_parser("init", add_help=False)
    ini.add_argument("name", nargs="?", default=None)
    ini.add_argument("--description", default=None)

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
    wr = sub.add_parser("writes", add_help=False)
    wr.add_argument("file", nargs="?", default=None)
    aud = sub.add_parser("audit", add_help=False)
    aud.add_argument("audit_action", nargs="?", default="verify",
                     choices=["verify", "head", "verify-anchors", "relay"])
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

def cmd_test(args):
    """Run every `it` case in a file and report. Exits 1 if any case fails.

    WHAT THIS EXISTS TO STOP. `it` blocks parsed and check-passed for an entire build while
    nothing executed them, so a governance control that requires tests for AI programs was asking
    for tests the language could not run. The assertions are real now, and so is the exit code.
    """
    filename = args.file
    verbose = getattr(args, "verbose", False)
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

    if program is not None:
        try:
            from mohio_enforce import enforce_scans as _enforce_scans
            _enforce_scans(ctx, program)
        except Exception as _scan_err:
            print(f"  [enforce] WARNING: a Layer 3 scanner failed "
                  f"({type(_scan_err).__name__}: {_scan_err}). Enforcement is INCOMPLETE.",
                  file=sys.stderr)
        if ctx.errors:
            for e in ctx.errors:
                _print_compile_error(e, source, filename)
            sys.exit(1)

    from mohio_interpreter import MohioInterpreter, MockAiRuntime
    ai = _construct_ai_runtime(getattr(args, "api_key", None), verbose) \
        if getattr(args, "ai", False) else MockAiRuntime()
    interp = MohioInterpreter(ai=ai, verbose=verbose,
                              db_path=_resolve_sqlite_db_path(filename, args))
    print(f"\n  mio test  {dim(filename)}\n")
    try:
        interp.run(program)
    except Exception as e:                                  # noqa: BLE001
        # A program that cannot finish cannot have proven anything, so this is a failure of the
        # run and is reported as one rather than as "no tests found".
        print(f"  {red('x')}  the program stopped before its cases finished: {e}\n")
        sys.exit(1)

    cases = getattr(interp, "_test_results", None) or []
    if not cases:
        # NOT A PASS. A file with no cases proves nothing, and calling that green is the exact
        # hole the `ai.test.mho` control had.
        print(f"  {yellow('!')}  no test cases in this file -- `it \"what this proves\" ... "
              f"it: done`\n")
        sys.exit(1)

    failed = 0
    for c in cases:
        # NAMED WHERE IT IS KNOWN. A case is written `it "what this proves"`, so an empty
        # description means the author left the quotes empty, which is worth saying rather than
        # papering over with a stand-in that reads like a name the runner chose.
        desc = c.get("description")
        label = desc if desc else "a case with no description (it \"\")"
        if c.get("passed"):
            print(f"  {green('v')}  {label}")
        else:
            failed += 1
            print(f"  {red('x')}  {label}")
            for why in c.get("failures") or []:
                print(f"        {why}")
    total = len(cases)
    print()
    if failed:
        print(f"  {red(bold(str(failed) + ' of ' + str(total) + ' case(s) failed'))}\n")
        sys.exit(1)
    print(f"  {green(bold(str(total) + ' case(s) passed'))}\n")


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


def _use_utf8_console():
    """Make mio's own output survive a console that is not UTF-8.

    Windows hands Python a cp1252 console by default, and mio prints characters cp1252
    has no code for: the box and arrow characters in its diagnostics, a source snippet
    from a file with any non-ASCII in it, a BOM that reached a snippet. Printing one of
    those raised UnicodeEncodeError from inside print(), which surfaced as an internal
    error and STOPPED the command -- so `mio check --all` died partway and reported no
    summary at all. That is a Windows newcomer's first command failing on the encoding
    of the message rather than on anything in their program.

    Done once here, at the entry point, rather than at each print: there are hundreds of
    print sites and a rule applied per-print is a rule that decays. errors='replace' is
    the backstop, so a character the console genuinely cannot show degrades to a
    placeholder instead of ending the run -- a diagnostic with one odd glyph in it is
    still a diagnostic, and a crash is not.

    Only main() calls this, so importing mio as a library leaves the process's streams
    alone. A stream that is not a real text file (a test harness swapping in StringIO,
    a closed stream) has no reconfigure and is left as it is.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            # RECORDED IN silent_shape_baseline.txt AS A DELIBERATE ONE. Both exceptions mean
            # the stream is detached, closed, or refuses reconfiguration -- which is to say the
            # stream this failure would have to be REPORTED ON is the broken one. There is no
            # louder option available: raising would stop mio at startup over the encoding of
            # its output rather than over anything the coder asked for, and printing a warning
            # needs the stream that just proved unusable. So the streams stay exactly as the
            # process was given them and mio runs, which is what it did before this function
            # existed.
            pass


def main():
    # Before the parser, because argparse prints usage and --help through these streams too.
    _use_utf8_console()

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
        "new":     cmd_new,
        "init":    cmd_init,
        "serve":   cmd_serve,
        "check":   cmd_check,
        "test":    cmd_test,
        "warmup":  cmd_warmup,
        "translate": cmd_translate,
        "generate":  cmd_generate,
        "schema":        cmd_schema,
        "schedule":      cmd_schedule,
        "audit":         cmd_audit,
        "writes":        cmd_writes,
        "install-hooks": cmd_install_hooks,
        "harvest":       cmd_harvest,
        "walk":          cmd_walk,
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
