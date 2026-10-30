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
from mohio_egress import gated_print as print  # noqa: A001,E402 -- every print through the egress gate
import builtins as _builtins  # noqa: E402


def _print_program_output(line):
    """The PROGRAM'S OWN OUTPUT (`show`, a give back) to the terminal. It was masked by the display
    rule inside the interpreter (the egress gate's `display` boundary: [phi]/[pci] hidden, [pii] in
    full, the standing ruling), so it is printed as it is; the trace scrub every other print takes
    would hide a [pii] value the display rule deliberately shows."""
    # EGRESS-EXEMPT: program output, already masked by the gate's display rule in the interpreter
    _builtins.print(line)

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
from mohio_version import VERSION, BUILD_SHA

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
            # EGRESS-EXEMPT: the compiler's own parser cache; no program value
            with os.fdopen(fd, "wb") as f:
                pickle.dump({"parser": parser,
                             "re_module": getattr(saved_re, "__name__", "re")},
                            f, protocol=pickle.HIGHEST_PROTOCOL)
                f.flush()
                os.fsync(f.fileno())          # bytes are on disk before the rename
            os.replace(tmp_path, cache_file)  # atomic on POSIX and on Windows
            tmp_path = None
            # PRUNE STALE HASHES (Batch 34). One pickle per historical grammar hash used to
            # accumulate forever in each of the three cache dirs -- the suite measurement found 44,
            # about 88 MB. The current grammar has exactly one valid pickle; every other
            # `mohio_parser_*.pkl` in THIS directory is a dead hash from an older grammar and can
            # never be loaded again (the hash is over grammar content). Remove them right after the
            # current one lands, so the set stays at one and cannot grow. Best-effort: a pickle a
            # concurrent process still holds open on Windows simply survives to the next run.
            for _old in cache_file.parent.glob("mohio_parser_*.pkl"):
                if _old.name != cache_file.name:
                    try:
                        _old.unlink()
                    except OSError:
                        pass
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
_AST_CACHE_VERSION = 3   # 3: trees keep every token position (mohio_treepickle); older caches dropped end positions

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


_PARSE_FINGERPRINT = None
def _parse_fingerprint() -> str:
    """Hash of ONLY the inputs that determine the PARSE TREE: the grammar, the pretokenizer, the
    symbol-table extractor that feeds it, the langmap preprocessor and its data files, and the
    reserved-word set the pretokenizer uses. A scanner, interpreter, AST-builder or CLI change
    touches none of these, so the parse cache survives it -- which is the point: Batch 9 changed
    only `mohio_reachability.py` and the blunt `_compiler_fingerprint` discarded every cached parse.

    Splitting the fingerprint is SAFE here, and cannot bring back the bug `_compiler_fingerprint`
    guards against (a cached clean verdict replayed after a new scanner), because ONLY THE PARSE IS
    CACHED, NEVER THE VERDICT. Layer 1, the AST build and every scan re-run on every check, so a new
    rule always runs -- there is no cached verdict to replay.
    """
    global _PARSE_FINGERPRINT
    if _PARSE_FINGERPRINT is not None:
        return _PARSE_FINGERPRINT
    import hashlib, os, glob
    here = os.path.dirname(os.path.abspath(__file__))
    h = hashlib.sha256()
    h.update(f"parse-v{_AST_CACHE_VERSION}|".encode())
    # No try/except swallowing anywhere below: a fingerprint that silently omits one of its
    # inputs on a read error is the very stale-cache bug this fingerprint exists to prevent -- a
    # grammar or pretokenizer edit that coincided with a transient read failure (a locked file on
    # Windows) would leave the omitted input out of the key and a stale parse would be served. On a
    # working install every read below succeeds, so this fails loud only when the install is
    # already broken, where a clear traceback beats a quietly-wrong cache key.
    # mohio_segment.py shifts and joins the segments of a segmented parse into the tree this caches;
    # mohio_treepickle.py writes every cached tree and decides which token fields survive the cache.
    for fn in ('mohio_pretokenizer.py', 'mohio_symbol_table.py', 'mohio_langmap.py', 'mohio_segment.py',
               'mohio_treepickle.py'):
        with open(os.path.join(here, fn), 'rb') as f:
            h.update(fn.encode())
            h.update(f.read())
    h.update(b'grammar|')
    h.update(mohio_data.GRAMMAR_PATH.read_bytes())
    from mohio_transformer import MOHIO_RESERVED_EXACT
    h.update(b'reserved|')
    h.update(repr(sorted(MOHIO_RESERVED_EXACT)).encode())
    # THE TREE IS A PICKLE OF LARK OBJECTS, so its validity is bound to the Lark version that built
    # it and the Python that pickled it, neither of which the file hashes above capture. A cache
    # built on one machine and shipped to another with the same Mohio compiler but a different Lark
    # or Python would otherwise match this key and be loaded -- an unpickle that at best raises (a
    # silent reparse) and at worst yields a structurally different tree. Binding both in makes such a
    # cache a CLEAN MISS that reparses, never a wrong tree. A deploy that pins Lark and Python (a
    # requirements file) keeps an identical fingerprint, so the cache still travels for the real case.
    import sys as _sys
    import lark as _lark
    h.update(b'lark|')
    h.update(str(getattr(_lark, '__version__', 'unknown')).encode())
    h.update(b'py|')
    h.update(('%d.%d' % _sys.version_info[:2]).encode())
    # Langmap DATA files translate a non-English source before it is parsed, so a langmap edit
    # changes the tree for any file using it. Hashing all of them is safe (a langmap edit is rare)
    # and keeps this a single global fingerprint rather than a per-file one.
    maps_dir = os.path.join(here, 'mohio_data', 'maps')
    for lm in sorted(glob.glob(os.path.join(maps_dir, '*.langmap'))):
        with open(lm, 'rb') as f:
            h.update(os.path.basename(lm).encode())
            h.update(f.read())
    _PARSE_FINGERPRINT = h.hexdigest()[:16]
    return _PARSE_FINGERPRINT


def _ctx_has_errors(ctx) -> bool:
    return bool(ctx is not None and getattr(ctx, 'errors', None))


def _app_cache_key(source_path: str):
    """A short hash of the WHOLE-APP declaration set (page + includes + journey) for `source_path`.
    The page's parse tree now depends on what its includes and journey DECLARE (those names are fed
    to the pretokenizer), so the parse cache must invalidate when an included file's declarations
    change, not only when the page itself changes. Hashing the declaration SET is the tightest
    correct dependency: an include edit that changes no declared name (a string value, a comment)
    legitimately keeps the cache; adding, renaming or removing a declaration reparses the page.
    Returns None for a non-file source (`<string>`) or on any read failure, which reproduces the
    pre-integration key exactly (a file with no includes/journey hashes only its own names)."""
    if not source_path or source_path == "<string>":
        return None
    import hashlib
    from mohio_symbol_table import app_mark_names
    # app_mark_names is internally robust (it skips an unreadable app file, never raises on one), so
    # there is no failure to swallow here. A genuine error (a real bug) is left to surface loudly
    # rather than silently disabling cache invalidation, and the two cache callers already treat an
    # exception from this as a miss (reparse), never a false hit.
    return hashlib.sha256(repr(sorted(app_mark_names(source_path))).encode()).hexdigest()[:16]


def _load_ast_cache(source_path: str, source: str):
    """Load a cached PARSE TREE, keyed on the source, the files that affect PARSING
    (`_parse_fingerprint`), and the whole-app declaration set (`_app_cache_key`). Returns the tree,
    or None on a miss.

    ONLY THE PARSE IS CACHED, NEVER THE VERDICT. Layer 1, the AST build and every whole-program scan
    re-run on every check, so a new scanner rule can never be skipped by a stale cache. That is why
    this no longer keys on the whole-compiler fingerprint and no longer stores the ctx: a scanner or
    interpreter change keeps the parse (the 87s on a large app), and a re-worded or added rule always
    runs, because there is no cached verdict to replay.
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
        if cached.get('parse') != _parse_fingerprint():
            return None  # a parse-affecting file changed -- reparse
        if cached.get('app') != _app_cache_key(source_path):
            return None  # an included file's or the journey's declarations changed -- reparse
        if _cache_debug():
            # Opt-in, like the parser cache's own line: a test can ask whether this file's parse was
            # READ from its cache, instead of inferring it from a timing difference that load can invert.
            print(f"  [ast] cache hit: {cache_path}", file=sys.stderr)
        return cached.get('tree')
    except Exception:
        pass  # Corrupted cache -- re-parse
    return None


def _save_ast_cache(source_path: str, source: str, tree, ctx=None):
    """Save the PARSE TREE, whenever the parse SUCCEEDED. A file whose parse succeeds but whose
    scans find errors still caches its tree: the parse is reusable, and the errors are re-produced
    fresh on the next check. `ctx` is accepted for call-site compatibility and is NOT stored -- the
    verdict is never cached.
    """
    if tree is None:
        return  # nothing parsed -- nothing to cache
    # Through the tree pickler: a tree read back from this cache is the tree a fresh parse built,
    # every token position included (plain pickle dropped every end position).
    import mohio_treepickle as _treepickle
    cache_path = _ast_cache_path(source_path)
    try:
        # EGRESS-EXEMPT: the compiler's own parse cache; no program value
        with open(cache_path, 'wb') as f:
            _treepickle.dump({
                'hash':  _source_hash(source),
                'parse': _parse_fingerprint(),
                'app':   _app_cache_key(source_path),
                'tree':  tree,
            }, f)
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


def _declared_ai_keys(path):
    """The env names a program's `ai.connect` blocks read their key from (`key env.MYKEY`, or the
    inline `address .. key env.MYKEY as NAME`), across one .mho file or every .mho under a folder.
    It decides only whether `--ai` may start: the key itself is resolved through the program when
    the connection registers."""
    import os as _os
    import re as _re
    files = []
    if path and _os.path.isdir(path):
        for _root, _dirs, _names in _os.walk(path):
            files += [_os.path.join(_root, n) for n in _names if n.endswith('.mho')]
    elif path and _os.path.isfile(path):
        files = [path]
    keys = set()
    for f in files:
        src = open(f, encoding='utf-8-sig').read()   # unreadable is loud, as it is when serve loads it
        if 'ai.connect' in src:
            keys.update(_re.findall(r'\bkey\s+env\.([A-Za-z_][A-Za-z0-9_]*)', src))
    return sorted(keys)


def _construct_ai_runtime(api_key, verbose, path=None):
    """A8: build the AI runtime, honoring MOHIO_AI=mock to force the labeled mock provider
    (classroom weeks 1-3 need mock mode; the real runtime needs a provider key). Any real provider
    key (Anthropic / OpenAI / Gemini) activates AnthropicAiRuntime, which routes by model prefix."""
    import os as _os
    from mohio_interpreter import MockAiRuntime
    if _os.environ.get("MOHIO_AI", "").strip().strip('"').strip("'").lower() == "mock":
        return MockAiRuntime()
    from mohio_ai import AnthropicAiRuntime
    return AnthropicAiRuntime(api_key=api_key, verbose=verbose, declared_keys=_declared_ai_keys(path))


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
     "A web address is a handler on its own, and the address rides on it:\n"
     "    request for sh.Thing at /home\n"
     "        ...\n"
     "    request: done\n"
     "  (or `listen for sh.Thing at /home` ... `listen: done` for a POST). No wrapper block is needed."),
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
     "To serve several addresses from one file, write each handler on its own:\n"
     "    request for sh.Home at /about\n"
     "        render\n            <p>About</p>\n"
     "        render: done\n"
     "    request: done\n"
     "Page CLASSIFICATION (`private:` / `hidden:`) is a different thing and is unchanged."),
)


_RENDER_INCLUDE_RE = re.compile(r'\{\{\s*include\s+"([^"]+)"\s*\}\}')


def _render_only_fragments(paths):
    """Absolute paths of the files a render includes (`{{ include "x" }}`). Those are page text and
    are not parsed as Mohio on their own. A file that is ALSO included as a statement is still
    checked as Mohio, through the page that includes it. Resolved the way the runtime resolves an
    include: relative to the including file, then the current directory."""
    import os as _os
    found = set()
    for p in paths:
        try:
            src = Path(p).read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            continue          # an unreadable file is reported by its own parse, not here
        base = _os.path.dirname(_os.path.abspath(p))
        for m in _RENDER_INCLUDE_RE.finditer(src):
            for cand in (_os.path.join(base, m.group(1)), _os.path.abspath(m.group(1))):
                if _os.path.isfile(cand):
                    found.add(_os.path.abspath(cand))
                    break
    return found


def _find_take_after_returns(source):
    """(line, task name) of the first `take` line written after a `returns` in the same task, or
    (None, None). Asked only after a parse has failed. `returns` may sit on the task line itself
    (`task echo returns text`) or on its own line before the takes."""
    import re as _r
    task, seen_returns = None, False
    for i, raw in enumerate(_mask_noncode(source).split("\n")):
        line = _code_part(raw).strip()
        m = _r.match(r'task\s+([A-Za-z_][A-Za-z0-9_]*)(\s+returns\b)?', line)
        if m and not line.startswith('task:'):
            task, seen_returns = m.group(1), bool(m.group(2))
            continue
        if task is None:
            continue
        if line.startswith('task: done') or line == 'done':
            task, seen_returns = None, False
        elif _r.match(r'returns\b', line):
            seen_returns = True
        elif seen_returns and _r.match(r'take\b', line):
            return i + 1, task
    return None, None


def _find_rounding_misuse(source):
    """The line of a rounding word written in a form Mohio does not have, or None. Asked only after
    a parse has failed. The accepted forms follow a value: `x round.to N`, `x round.up [N]`,
    `x round.down [N]`; this finds `round.to` with no whole-number count, a line led by a rounding
    verb, and a bare `round` after a value."""
    import re as _r
    for i, raw in enumerate(_mask_noncode(source).split("\n")):
        line = _code_part(raw).strip()
        if not line:
            continue
        if _r.search(r'(?<![A-Za-z0-9_.])round\.to(?![A-Za-z0-9_])(?!\s+\d+(?![\d.]))', line):
            return i + 1
        if _r.match(r'round\.(to|up|down)(?![A-Za-z0-9_])', line):
            return i + 1
        if _r.search(r'[A-Za-z0-9_)\]]\s+round\s*(\)|$)', line):
            return i + 1
    return None


def _find_misplaced_no_symbol(source):
    """The line of a `no.symbol` written anywhere but on a currency field declaration, or None.

    The field form is `<name> as <CODE> ... no.symbol`; every other use follows a value, which is
    not a form Mohio has. Text inside quotes and comments is masked, so a page that mentions the
    word is not read as using it."""
    import re as _r
    for i, raw in enumerate(_mask_noncode(source).split("\n")):
        line = _code_part(raw).strip()
        if not _r.search(r'(?<![A-Za-z0-9_.])no\.symbol(?![A-Za-z0-9_])', line):
            continue
        if _r.match(r'^[A-Za-z_][A-Za-z0-9_]*\s+as\s+[A-Za-z]{3}\b', line):
            continue
        return i + 1
    return None


def _find_retired_form(source, err_line=None):
    """Find a retired keyword at the head of a statement that can be the cause of a parse failure
    reported at `err_line` (see _at_reported_position). Returns (word, line_no, guidance, header)."""
    import re as _r
    for i, raw in enumerate(_mask_noncode(source).split("\n")):
        line = _code_part(raw).strip()
        if not line or not _at_reported_position(source, i + 1, err_line):
            continue
        for pattern, word, guidance in _RETIRED_FORMS:
            m = _r.search(pattern, line)
            if not m:
                continue
            w = word or m.group(1)
            if not _retired_word_is_a_name(line, m):
                return (w, i + 1, guidance, 'is not a Mohio word any more.')
            if w in _RESERVED_NOT_NAMES:
                return (w, i + 1, _RESERVED_NOT_NAMES[w], 'is reserved, so it cannot be a name.')
    return (None, None, None, None)


# A RETIRED WORD IS ONLY RETIRED WHERE IT WOULD BE READ AS ITS OLD STATEMENT (RULED by Ron 2026-10-09).
# As a field, a column, a variable or after a dot it is an ordinary name. The explainer for a syntax
# error used to blame the first line STARTING with a retired word anywhere in the file, so a shape
# field `make as text required` (a vehicle's make) was reported as the error while the real mistake
# sat 600 lines further down.
# One retired word is still a live word, so it is never a name: `if` is the trailing qualifier
# (`option X if ...`). `catch` was released as a name 2026-10-09; only its statement refuses.
_RESERVED_NOT_NAMES = {
    'if': "Choose another name for this field or variable, for example `condition`.\n"
          "`if` is still a word in Mohio (the trailing qualifier `option X if ...`); decisions use "
          "`check` / `when` / `otherwise`.",
}
_NAME_FOLLOWS = re.compile(r'^\s*(?:as\b|"|\d|=|\.|:|\[|\(|to\b|is\b|[A-Za-z_]\w*\.)')


def _retired_word_is_a_name(line, m):
    """True when the retired word at the head of `line` is used as a NAME: a field declaration
    (`make as text`), a value line (`make "Volvo"`, `make 3`), an assignment or a dotted name. Only
    the bare head word is judged; a two-word form such as `request outbound` is its own statement."""
    head = m.group(0)
    if ' ' in head.strip() or '.' in head:
        return False
    return bool(_NAME_FOLLOWS.match(line[m.end():]))


# The two-bareword where shorthand `where status active` (field value, no `is`) was DELETED
# (Ron, 2026-09-25) -- it read as two nouns, not a sentence, and the ordinary `where status is
# active` already says it. Deleting it also removed the only place a bareword could sit in the
# FIELD position, which is why `when is even` (predicate before its subject) misparsed. Both now
# fail at the grammar on some unrelated character further down; name the sentence form the reader
# meant, the same way the retired-word table already does.
_COND_PREDS = frozenset((
    'is', 'contains', 'starts', 'ends', 'matches', 'between', 'older', 'newer',
    'above', 'below', 'more', 'less', 'greater', 'in', 'not', 'empty', 'odd', 'even'))

def _find_condition_shorthand(source):
    """The removed `where X Y` shorthand, or a subject-less `when is <pred>`. Returns
    (kind, line_no, guidance) where kind is 'where-shorthand' or 'when-subjectless'."""
    import re as _r
    for i, raw in enumerate(_mask_noncode(source).split("\n")):
        line = _code_part(raw).strip()
        if not line:
            continue
        # A condition with the predicate BEFORE its subject: `when is even`, `when is empty`.
        if _r.match(r'^when\s+(?:is|is\.|not\.|starts|ends|contains|matches)\b', line):
            return ("when-subjectless", i + 1,
                    "A condition names its subject first, then the test:\n"
                    "    check total\n"
                    "        when total is even\n"
                    "            ...\n"
                    "    check: done\n"
                    "`when is even` puts the test before its subject, so there is nothing to test.")
        # The two-bareword shorthand: `where status active`, a field and a value with no `is`.
        m = _r.match(r'^where\s+([A-Za-z_][\w.]*)\s+([A-Za-z_]\w*)\s*$', line)
        if m and m.group(2).lower() not in _COND_PREDS:
            fld, val = m.group(1), m.group(2)
            return ("where-shorthand", i + 1,
                    "A where clause reads as a sentence, with `is` between the field and the value:\n"
                    f"    where {fld} is {val}\n"
                    f"The two-word shorthand `where {fld} {val}` was removed.")
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


_WHEN_IS_FIELD = re.compile(r'^(\s*)when\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*?)\.is(?![A-Za-z0-9_.])\s*(.*?)\s*(?:->)?\s*$')


def _find_when_is_field(source, err_line):
    """A `when <owner>.is ...` condition that can be the cause of a parse failure reported at
    `err_line`. Returns (line, owner, enclosing check subject or None, value written or None)."""
    lines = _mask_noncode(source).split("\n")
    raw = source.split("\n")
    for i, text in enumerate(lines):
        m = _WHEN_IS_FIELD.match(text)
        if not m or not _at_reported_position(source, i + 1, err_line):
            continue
        indent = len(m.group(1))
        subject = None
        for j in range(i - 1, -1, -1):
            above = lines[j]
            if not above.strip():
                continue
            depth = len(above) - len(above.lstrip())
            if depth < indent:
                cm = re.match(r'\s*check\s+([A-Za-z_][\w.]*)\s*$', above)
                subject = cm.group(1) if cm else None
                break
        value = None
        if m.group(3) and i < len(raw):
            # The value as written, quotes included, read from the unmasked line.
            vm = re.search(r'\.is\s+(.+?)\s*(?:->)?\s*$', raw[i])
            value = vm.group(1) if vm else None
        return (i + 1, m.group(2), subject, value)
    return None


def _find_case_near_miss(source, err_line=None):
    """A case transform spelled the way another language spells it, on a line that can be the
    cause of a parse failure reported at `err_line`. Returns (written, meant)."""
    import re as _re
    for i, text in enumerate(_mask_noncode(source).split("\n")):
        if not _at_reported_position(source, i + 1, err_line):
            continue
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
                    "NOT WIRED YET -- `mio check` refuses them rather than let them do nothing, "
                    "so use one of the forms above until they are."
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


_CLOSER_LINE = re.compile(r'^\s*(?:[A-Za-z_][\w.]*\s*:\s*done|done)\s*$')


def _at_reported_position(source, found_line, err_line):
    """True when a hint found on `found_line` can be the cause of a parse failure reported at
    `err_line` (RULED by Ron 2026-10-09, from the Dispatch app: a syntax error at line 748 was
    explained as a retired word on line 81, a valid shape field 667 lines earlier).

    The parser reads every line before the reported one, so a cause above it must be part of the
    statement that was still open there: the reported line itself, the code line just before it, or
    a line whose block the reported line sits inside (deeper indented, or that block's own closer).
    Anything else was read successfully and is not what failed. A failure with no line (the end of
    the file) has no position to hold the hint to, so every hint stays admissible there."""
    if not err_line or not found_line:
        return True
    if found_line == err_line:
        return True
    if found_line > err_line:
        return False
    lines = _mask_noncode(source).split("\n")
    code = [n for n in range(found_line + 1, err_line) if n - 1 < len(lines) and lines[n - 1].strip()]
    if not code:
        return True                      # nothing but blank lines and comments between them
    opener = lines[found_line - 1] if found_line - 1 < len(lines) else ""
    indent = len(opener) - len(opener.lstrip())
    for n in code + [err_line]:
        text = lines[n - 1] if n - 1 < len(lines) else ""
        depth = len(text) - len(text.lstrip())
        if depth > indent:
            continue
        # The reported line is the closer of the found line's own block, or of a block around it
        # (every line between is deeper): the parser often fails on the outer closer of the statement
        # that did not parse (`when is even` reported at the check's `check: done`).
        return n == err_line and bool(_CLOSER_LINE.match(text))
    return True


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
    (r'^\s*require\s+role\s+"[^"]*"\s*,',
     "a comma between roles",
     "Several roles are joined with `or`:\n    require role \"user\" or \"admin\""),
    (r'^\s*verify\s+token\s+from\s+"',
     "a quoted value after `verify token from`",
     "`verify token from` reads a name, so give the token a name first:\n"
     "    presented request.t\n    verify token from presented as grant"),
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
    # `give back [409]` is a status code, never an index: the hint used to fire on any handler
    # that answered with a status near the real mistake (a role list, a verify source).
    (r'\b(?!back\b)[A-Za-z_]\w*\s*\[\s*\d+\s*\]',
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


def _locate_unfinished(source):
    """A position-less end-of-input error (Earley raises `UnexpectedEOF` with no line/col when a token
    lexes but the parse can only fail at the end) points at the LAST line of the file, not the line the
    mistake is on. Find the real line: blank each code line from the BOTTOM up, and the deepest line
    whose removal lets the whole file parse is where the unfinished statement sits. Runs ONLY on the
    error path, and returns (line_no, snippet) or (None, None) -- so an ambiguous case falls back to the
    honest "on or above line N" message rather than pointing confidently at the wrong place."""
    try:
        from mohio_symbol_table import extract_symbols
        from mohio_pretokenizer import pretokenize
        from mohio_transformer import MOHIO_RESERVED_EXACT
        parser = _make_parser_cached(_load_grammar())
    except Exception:
        return None, None

    def _parses(text):
        try:
            st = extract_symbols(text)
            parser.parse(pretokenize(text, st.all_user_names(), MOHIO_RESERVED_EXACT))
            return True
        except Exception:
            return False

    lines = source.split("\n")
    idxs = [i for i, l in enumerate(lines) if l.strip()]
    if not idxs or len(idxs) > 150:          # keep the error path cheap on large files
        return None, None
    for i in reversed(idxs):
        trial = lines[:]
        trial[i] = ""                         # blank, preserving line numbers
        if _parses("\n".join(trial)):
            return i + 1, _source_snippet(source, i + 1)
    return None, None


def _find_hyphenated_name(source, line, col):
    """A hyphen inside what reads as a name -- `balance-hidden`, `is-active` -- fails at the `-` with
    `No terminal matches '-'`, naming a character and never the name. A hyphen is only ever minus, and
    minus lives inside parentheses; between two name fragments it is invalid. Recover the whole
    hyphenated word so the message names what was written. Returns (word, line) or (None, None)."""
    import re as _re
    if not (line and col):
        return None, None
    lines = source.split("\n")
    if line > len(lines):
        return None, None
    text = lines[line - 1]
    idx = col - 1                       # 0-based position of the offending character
    if idx < 0 or idx >= len(text) or text[idx] != '-':
        return None, None
    for mm in _re.finditer(r'[A-Za-z_][A-Za-z0-9_]*(?:-[A-Za-z0-9_]+)+', text):
        if mm.start() <= idx < mm.end():
            return mm.group(0), line
    return None, None


def _find_type_after_value(source, line, col):
    """`code 5 as text` with a reserved type word stops the parse ON the type word. Returns the
    shared teaching message (mohio_ast.type_after_value_message) when the caret is there, else None."""
    import re as _re
    if not (line and col):
        return None
    lines = source.split("\n")
    if line > len(lines):
        return None
    m = _re.match(r'\s*[A-Za-z_]\w*\s+\S.*?\s+as\s+([A-Za-z_][\w.]*)', lines[line - 1])
    if not m or not (m.start(1) <= col - 1 < m.end(1)):
        return None
    from mohio_ast import type_after_value_message
    return type_after_value_message(lines, line)


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
    # The position a hint must explain (see _at_reported_position). A failure at the end of the
    # file carries wherever the file ran out, which is no position at all, so it holds none.
    _at_eof = any(w in msg.lower() for w in ("end-of-input", "end of input"))
    at = None if _at_eof else line
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
    # A `mioai.<method>` call written with a value after it (`mioai.text "hello"`) fails to parse,
    # so it never reaches the check-time refusal and arrived here as a bare character. Say what the
    # method is: retired, designed and not built, or not a mioai operation (RULED 2026-10-09).
    _mio_m = re.match(r'\s*mioai\.([A-Za-z_]\w*)', _source_snippet(source, line) or "") if line else None
    if _mio_m:
        from mohio_ast import mioai_refusal
        print(f"  {red('x')} {mioai_refusal(_mio_m.group(1))}\n")
        return
    # A `config` block never parses (`config: done` closes nothing the grammar knows), so it reached
    # here as a bare character. It is the same refusal every other form of the block gives (Ron,
    # 2026-10-13): application-level settings are not built yet, at the line that opened it.
    if line and re.match(r'\s*config\s*:\s*done\s*$', _source_snippet(source, line) or ""):
        _src_lines = (source or "").splitlines()
        _open = next((i + 1 for i in range(min(line, len(_src_lines)) - 1, -1, -1)
                      if re.match(r'\s*config\s*$', _src_lines[i])), line)
        from mohio_reachability import app_config_refusal, APP_CONFIG_FIX
        print(f"  {red('x')} {app_config_refusal(_open, 'config')}.")
        print(f"    {dim(APP_CONFIG_FIX)}\n")
        return
    # `shape -Order` never parses, so it reaches here, not the check that refuses `shape _Order`.
    # Both say the same thing: a shape name begins with a letter (RULED by Ron 2026-10-10).
    _sn_m = re.match(r'\s*shape\s+([-_][\w-]*)', _source_snippet(source, line) or "") if line else None
    if _sn_m:
        from mohio_ast import shape_name_start_message
        print(f"  {red('x')} {shape_name_start_message(line, _sn_m.group(1))}\n")
        return
    # `connect db as json` never parses, because json is a Mohio word, so it never reaches the
    # check that refuses `connect db as csv`. The parse fails on that line: give the same refusal
    # (RULED by Ron 2026-10-11). Any Mohio word in that slot is the same mistake. The caret is not
    # consulted: after `readonly` the parse fails on `from`, and an engine word Mohio does not know
    # is refused whatever else the line holds.
    _cn_m = re.match(r'\s*connect\s+\w+\s+as\s+(?:(?:readonly|writeonly|readwrite)\s+)?([A-Za-z_]\w*)\b',
                     _source_snippet(source, line) or "") if line else None
    if _cn_m:
        from mohio_db_drivers import is_supported_driver, unknown_driver_message
        if not is_supported_driver(_cn_m.group(1)):
            print(f"  {red('x')} {unknown_driver_message(_cn_m.group(1))}\n")
            return
    _tv = _find_type_after_value(source, line, col)
    if _tv:
        print(f"  {red('x')} {_tv}\n")
        return
    # `when actor.is "yes"` (RULED by Ron 2026-10-09, the Dispatch app's line 747): `is` dotted onto a
    # name is read as a field, the value after it fits nowhere, and the parse fails on the NEXT line.
    # Describe the condition, at its own line, naming the enclosing check's subject.
    _wi = _find_when_is_field(source, at)
    if _wi:
        from mohio_ast import when_is_field_message
        _wi_line, _wi_owner, _wi_subject, _wi_value = _wi
        print(f"  {red('x')} {when_is_field_message(_wi_line, _wi_owner, _wi_subject, _wi_value)}")
        _echo_line_if_new(source, _wi_line, line)
        print()
        return
    # A quote that is never closed swallows the rest of the file and kills the parse at
    # end-of-input, so it arrives here with no line, no caret, and an empty expected-list.
    # It is the commonest typo there is, so it is recovered before anything else.
    q_line, q_col = _find_unterminated_string(source)
    if q_line and not (at and q_line > at):
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
    word, w_line, guidance, w_head = _find_retired_form(source, at)
    if word:
        print(f"  {red('x')} {bold(word)} {w_head}")
        _echo_line_if_new(source, w_line, line)
        print()
        for g in guidance.splitlines():
            print(f"    {g}")
        print()
        return

    # F5 (RULED by Ron, 2026-10-06): `no.symbol` is a SHAPE FIELD modifier. Written after a value
    # (`give back [200] r.amount no.symbol`, `show amt no.symbol`) the parse failed further down and
    # borrowed the square-bracket hint, naming a problem the line did not have. Name the real one.
    _ns_line = _find_misplaced_no_symbol(source)
    if _ns_line and _at_reported_position(source, _ns_line, at):
        print(f"  {red('x')} {bold('no.symbol')} is a field modifier, and here it follows a value.")
        _echo_line_if_new(source, _ns_line, line)
        print(f"\n    It belongs on the money field itself, in its shape, and then every place that")
        print(f"    shows the field drops the symbol (show and render):")
        print(f"        amount as USD no.symbol")
        print(f"    A give back already sends money as its plain number, so it needs nothing here.\n")
        return

    # F1 (RULED by Ron, 2026-10-06): a `take` written after `returns` in a task is not a parameter
    # any more, and `take v as text` there stops the parse with nothing said about the order.
    _tk_line, _tk_task = _find_take_after_returns(source)
    if _tk_line and _at_reported_position(source, _tk_line, at):
        print(f"  {red('x')} This {bold('take')} comes after {bold('returns')} in task "
              f"{bold(_tk_task)}, so it is not read as a parameter.")
        _echo_line_if_new(source, _tk_line, line)
        print(f"\n    Write the take lines first, then returns:")
        print(f"        task {_tk_task}\n            take <param> as <type>\n"
              f"            returns <type>\n            give back <value>\n        task: done\n")
        return

    # A rounding form that is not one of the three (RULED by Ron, 2026-10-06): name the accepted
    # forms rather than leave a generic parse error.
    _rd_line = _find_rounding_misuse(source)
    if _rd_line and _at_reported_position(source, _rd_line, at):
        from mohio_ast import ROUNDING_FORMS
        print(f"  {red('x')} This line uses a rounding word in a form Mohio does not have.")
        _echo_line_if_new(source, _rd_line, line)
        print()
        for _part in ROUNDING_FORMS.split(". "):
            print(f"    {_part.rstrip('.')}.")
        print()
        return

    # A hyphen inside a name (`toggle "#box" as balance-hidden`) fails at the `-` with a bare
    # `No terminal matches '-'`. Name the whole word, at its line, instead of the punctuation.
    _hy, _hy_line = _find_hyphenated_name(source, line, col)
    if _hy:
        _under = _hy.replace('-', '_')
        _parts = _hy.split('-')
        _camel = _parts[0] + ''.join(p[:1].upper() + p[1:] for p in _parts[1:])
        print(f"  {red('x')} {bold(_hy)} has a hyphen, and a name here cannot contain one.")
        _echo_line_if_new(source, _hy_line, line)
        print(f"\n    A hyphen is only ever minus, and minus lives inside parentheses. In a name -- a "
              f"state, a variable, a field -- join the words with an underscore or run them together:")
        print(f"        {bold(_under)}   or   {bold(_camel)}\n")
        return

    # The removed two-bareword where shorthand, or a subject-less `when is <pred>`. Both fail at
    # the grammar on a later character; name the sentence form the reader meant.
    _ck, _ck_line, _ck_guidance = _find_condition_shorthand(source)
    if _ck and _at_reported_position(source, _ck_line, at):
        if _ck == "where-shorthand":
            print(f"  {red('x')} This where clause is missing its {bold('is')}.")
        else:
            print(f"  {red('x')} This condition puts the test before its subject.")
        _echo_line_if_new(source, _ck_line, line)
        print()
        for g in _ck_guidance.splitlines():
            print(f"    {g}")
        print()
        return

    # A case transform spelled the way another language spells it. The vocabulary here is richer
    # than the one most people arrive with, which is why it gets missed: the word they know is
    # not the word, and what they got back was a character.
    _wrong, _meant = _find_case_near_miss(source, at)
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
    if uns and _at_reported_position(source, u_line, at):
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
    if resv and _at_reported_position(source, r_line, at):
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
    if h_line and _at_reported_position(source, h_line, at):
        snippet = _source_snippet(source, h_line)
        print(f"  {red('x')} A comment in Mohio starts with {bold('//')}, not with #.")
        _echo_line_if_new(source, h_line, line)
        print(f"\n    Write it as:  // {snippet.strip().lstrip('#').strip() if snippet else 'your note'}")
        print(f"    For several lines at once, wrap them in  /* ... */\n")
        return

    cap, c_line = _find_capitalised_opener(source)
    if cap and _at_reported_position(source, c_line, at):
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
        # FIRST, try to point AT the offending line rather than the file's last line. A bad token that
        # lexed but the parser could only reject at end-of-input (common in a check/when or request
        # handler body) arrives here with no position; `_locate_unfinished` recovers the actual line.
        _u_line, _u_snip = _locate_unfinished(source)
        if _u_line is not None:
            print(f"  {red('x')} Line {_u_line} has something the parser cannot read, and it was "
                  f"only caught at the end of the file.")
            if _u_snip is not None:
                print(f"\n  {dim(str(_u_line) + ' |')} {_u_snip}")
            print(f"\n    A token here fits no statement in this block, so the parser read on to the "
                  f"end still")
            print(f"    waiting for the rest of something. Check line {_u_line} for a stray symbol, a "
                  f"misspelled")
            print(f"    word, or a value where a name belongs.")
            print()
            return
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


def _wrapped_finding(marker_colored, marker_plain, message, indent="  ", width=None):
    """A finding line, WORD-WRAPPED in code with a hanging indent, so a long message on an interactive
    terminal never relies on the terminal's own soft-wrap -- which eats the space at the wrap column
    and renders `It is` as `Itis`. Continuations align under the message text.

    ONLY WHEN A REAL TERMINAL IS WRITING (or an explicit `width` is given, for tests). Piped or
    captured output is not terminal-soft-wrapped, so there is no `Itis` there and the single-line form
    is what tooling and tests read; wrapping it would only split a message a grep expects whole. So the
    wrap tracks where the bug actually happens."""
    import textwrap
    if width is None:
        _isatty = getattr(sys.stdout, 'isatty', None)   # missing on some wrapped streams; guard it
        if not (_isatty and _isatty()):
            return f"{indent}{marker_colored} {message}"     # piped/captured: single line, unchanged
        import shutil
        width = shutil.get_terminal_size((100, 24)).columns
    width = max(40, min(int(width), 120))
    lead = f"{indent}{marker_plain} "                 # visible leading, e.g. "  ! "
    body_width = max(20, width - len(lead))
    lines = textwrap.wrap(str(message), width=body_width,
                          break_long_words=False, break_on_hyphens=False) or [str(message)]
    pad = " " * len(lead)
    out = [f"{indent}{marker_colored} {lines[0]}"]
    out.extend(pad + ln for ln in lines[1:])
    return "\n".join(out)


_INCLUDED_SOURCE = {}


def _finding_where(finding, source, filename):
    """(source, label) for a finding's excerpt. A finding on a statement merged in from an
    included file carries that file on its line (mohio_ast.SourceLine, HC-137), and is shown with
    that file's own text and named `file:line`; anything else is this file's line as before."""
    ln = getattr(finding, 'line', None)
    path = getattr(ln, 'file', None)
    if not path or (filename and os.path.realpath(path) == os.path.realpath(filename)):
        return source, str(ln)
    if path not in _INCLUDED_SOURCE:
        try:
            _INCLUDED_SOURCE[path] = open(path, encoding='utf-8-sig').read()
        except OSError:  # SILENT-OK: an unreadable include prints no excerpt; its file and line are still named
            _INCLUDED_SOURCE[path] = ''
    base = os.path.dirname(os.path.abspath(filename)) if filename else os.getcwd()
    try:
        shown = os.path.relpath(path, base)
    except ValueError:      # another drive on Windows: the full path is the honest name
        shown = path
    return _INCLUDED_SOURCE[path], f"{shown.replace(os.sep, '/')}:{int(ln)}"


def _finding_file(finding):
    """The included file a finding's line belongs to, as a path relative to the working directory
    (the way the checked file itself is named), or None for a line of the checked file."""
    path = getattr(getattr(finding, 'line', None), 'file', None)
    if not path:
        return None
    try:
        return os.path.relpath(path).replace(os.sep, '/')
    except ValueError:      # another drive on Windows: the full path is the honest name
        return path


def _print_compile_error(err, source="", filename=""):
    """Print a single CompileError from the new transformer."""
    header = bold(red("Error")) + (f"  {dim(filename)}" if filename else "")
    source, where = _finding_where(err, source, filename)
    snippet = _source_snippet(source, err.line)
    if snippet is not None:
        print(f"  {dim(where + ' |')} {snippet}")
    print(_wrapped_finding(red('x'), 'x', err.message))
    if err.hint:
        for hint_line in err.hint.splitlines():
            print(f"    {dim(hint_line)}")
    _print_code_link(err)
    print()


def _code_link(finding):
    """The getmohio page of a finding's code, from the diagnostic-code registry; nothing for a finding
    whose code is not registered."""
    from mohio_transformer import diagnostic_link
    return diagnostic_link(getattr(finding, 'code', None))


def _journey_suppressions(program):
    """{code: (reason, line)} for every `suppress <code> reason "..."` line in the journeys this page is
    checked with (RULED by Ron 2026-10-25). The cascade is already merged into the program, root first,
    so a root journey's line reaches the whole project and a folder journey's line the folder below it.
    Only an advisory code with a reason is honoured; scan_suppress_lines refuses any other line."""
    from mohio_ast import JourneyMeta
    from mohio_transformer import diagnostic_entry
    from mohio_reachability import _dataclass_nodes
    out = {}
    for n in _dataclass_nodes(program):
        if not isinstance(n, JourneyMeta) or n.kind != 'suppress':
            continue
        code, reason = n.value
        entry = diagnostic_entry(code)
        if entry is not None and entry.get('suppressible') and reason and str(reason).strip():
            out[code] = (str(reason), n.line)
    return out


def _apply_suppressions(ctx, program):
    """Move each warning whose code a journey suppresses out of ctx.warnings, into ctx.suppressed as
    (warning, reason, line declared). Errors are never touched: no error is suppressible."""
    rules = _journey_suppressions(program) if program is not None else {}
    kept, quiet = [], []
    for w in list(ctx.warnings or []):
        rule = rules.get(_warning_code(w))
        if rule is None:
            kept.append(w)
        else:
            quiet.append((w, rule[0], rule[1]))
    ctx.warnings = kept
    ctx.suppressed = quiet
    return quiet


def _distinct_suppressed(collected):
    """The suppressed advisories of a folder check, each once: a journey's own finding that every page
    under it inherits is one finding, not one per page."""
    seen, out = set(), []
    for page, info in (collected or {}).items():
        for w, reason, line in (info or {}).get('suppressed', ()):
            # A finding on a journey's line names that journey's file and is one finding however many
            # pages inherit it; a page's own finding is that page's.
            src = getattr(getattr(w, 'line', None), 'file', None) or page
            k = (_warning_code(w), str(getattr(w, 'message', w)), str(getattr(w, 'line', '')), str(src))
            if k not in seen:
                seen.add(k)
                out.append((w, reason, line, src))
    return out


def _advisory_counts(warnings):
    """(advisories, of them suppressible) among `warnings`, each warning counted once per instance, the
    way the warning count beside it is. The class and the suppressible flag are the registry's."""
    from mohio_transformer import diagnostic_entry
    adv = sup = 0
    for w in warnings:
        entry = diagnostic_entry(_warning_code(w))
        if entry is not None and entry.get('class') == 'advisory':
            adv += 1
            sup += bool(entry.get('suppressible'))
    return adv, sup


def _print_advisory_count(counts, n_warn):
    """`5 of the 7 warnings are advisories ...` under the summary (RULED by Ron 2026-10-26 #5): an
    advisory is an ergonomic finding, so the rest of the count reads as the ones that matter, and the
    line says how many a journey may silence by code. Nothing when no warning is an advisory."""
    adv, sup = counts
    if not adv:
        return
    lead = ((f"All {n_warn} warning(s) " if adv == n_warn else f"{adv} of the {n_warn} warning(s) ")
            + ("is an advisory" if adv == 1 else "are advisories") + ", ergonomic rather than governance")
    if sup:
        lead += (f"; a journey may silence {sup} of " + ("it" if adv == 1 else "them") + " by code"
                 f" (`suppress <code> reason \"...\"`)")
    print(f"  {dim(lead + '.')}")


def _print_suppressed(quiet, target, show):
    """`N advisories suppressed, run mio check <target> --show-suppressed to see them` (the silence is
    never opaque), or with --show-suppressed every one: its code, what it said and the reason given."""
    if not quiet:
        return
    n = len(quiet)
    if not show:
        print(f"  {dim(f'{n} advisor' + ('y' if n == 1 else 'ies') + ' suppressed by a journey, run '
                       f'`mio check {target} --show-suppressed` to see ' + ('it' if n == 1 else 'them'))}")
        return
    print(f"\n  {bold('Suppressed')}  {dim(f'{n} advisor' + ('y' if n == 1 else 'ies') + ', each by a journey line')}\n")
    for item in quiet:
        w, reason, line = item[:3]
        first = str(getattr(w, 'message', w)).splitlines()[0]
        src = getattr(line, 'file', None)
        where = f"{os.path.basename(str(src))}:{line}" if src else f"journey line {line}"
        page = f"{str(item[3]).replace(os.sep, '/')}  " if len(item) > 3 else ""
        print(f"    {yellow(_warning_code(w))}  {dim(page)}{first}")
        print(f"      {dim(f'suppressed at {where}, reason: {reason}')}")
    print()


def _print_code_link(finding):
    """Close a finding with its code's page, `see getmohio.com/codes/<code>`, where the reader can read
    what the code means and, for an advisory, how to suppress it."""
    see = _code_link(finding)
    if see:
        print(f"    {dim('see ' + see)}")


def _print_compile_warning(warn, source="", filename="", prefix=""):
    """Print a single CompileWarning from the new transformer. `prefix` leads the message (a grouped
    warning's `N x <code>: `), so the one site shown still carries its whole teaching message."""
    source, where = _finding_where(warn, source, filename)
    snippet = _source_snippet(source, warn.line)
    if snippet is not None:
        print(f"  {dim(where + ' |')} {snippet}")
    print(_wrapped_finding(yellow('!'), '!', prefix + warn.message))
    if warn.hint:
        for hint_line in warn.hint.splitlines():
            print(f"    {dim(hint_line)}")
    _print_code_link(warn)
    print()


# A warning's code names its class. These two say only "a warning", so they never group one class with
# another: a warning carrying one is grouped by what it says instead.
_GENERIC_WARNING_CODES = ('', 'WARNING')

# `mio check --locations [code]` (Ron 2026-10-22): list every site of a grouped warning instead of one.
# Set by cmd_check from its arguments; `_CHECK_TARGET` is what the drill-down line tells the reader to
# run again.
_CHECK_LOCATIONS = {'on': False, 'code': None}
_CHECK_TARGET = ['.']


def _warning_code(warn):
    code = str(getattr(warn, 'code', '') or '')
    return '' if code in _GENERIC_WARNING_CODES else code


def _warning_key(warn):
    """Warnings of one CLASS group together (RULED by Ron 2026-10-22): two warnings with the same stable
    code are the same warning, whatever name or path each message carries. A warning with no code of
    its own is the same as another when it says the same thing: the same message and the same fix."""
    code = _warning_code(warn)
    if code:
        return ('code', code)
    return (str(getattr(warn, 'message', warn)), str(getattr(warn, 'hint', '') or ''))


def _site_label(warn, source, filename, first_file):
    _src, label = _finding_where(warn, source, filename)
    if filename and filename != first_file and ':' not in label:
        try:
            page = os.path.relpath(filename)
        except ValueError:      # another drive on Windows: the full path is the honest name
            page = filename
        return f"{page.replace(os.sep, '/')}:{label}"
    return label if ':' in label else f"line {label}"


def _print_warning_groups(items, heading=None):
    """Print warnings with each class said ONCE (RULED by Ron 2026-10-22, grouping by stable code).
    `items` is [(warning, source, filename)] in order. A class seen once prints as before. A class seen
    N times prints ONE entry, `N x <code>: <message>`, with the first site's excerpt and its whole
    teaching message (what, why, the fix), and one line naming the drill-down: `mio check <target>
    --locations` lists every site. With --locations (all classes, or the one named) every site is
    listed instead. The order is the order the classes first appear, which is the files' and lines'
    order, so two runs on the same code print the same thing. Errors are never grouped: each is
    printed where it is. `--json` is not affected: it lists every finding. `heading`, when given,
    prints before the first warning, and only when there is one. Returns how many printed in full."""
    groups, order = {}, []
    for warn, source, filename in items:
        k = _warning_key(warn)
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append((warn, source, filename))
    if order and heading:
        print(heading)
    for k in order:
        group = groups[k]
        first = group[0]
        if len(group) == 1:
            _print_compile_warning(*first)
            continue
        code = _warning_code(first[0])
        _print_compile_warning(*first, prefix=f"{len(group)} x {code or 'the same warning'}: ")  # SILENT-SHAPE-OK: wording for a class with no code
        want = _CHECK_LOCATIONS['on'] and _CHECK_LOCATIONS['code'] in (None, code)
        if want:
            print(f"    {dim(f'All {len(group)} sites:')}")
            for warn, source, filename in group:
                print(f"      {dim(_site_label(warn, source, filename, first[2]))}")
            print()
        else:
            _drill = f"mio check {_CHECK_TARGET[0]} --locations" + (f" {code}" if code else "")
            print(f"    {dim(f'{len(group)} sites in all, the first shown. List every one: {_drill}')}\n")
    return len(order)


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


def _display_target(target):
    """A database address as it may be printed: the password of a url is never shown. An audit
    command names the store it read in its messages, and the store is often a url carrying its
    credentials, which went to the terminal and to any log of it in full."""
    t = str(target)
    if '://' not in t or '@' not in t:
        return t
    from urllib.parse import urlsplit, urlunsplit
    try:
        u = urlsplit(t)
        if u.password is None:
            return t
        host = u.hostname or ''
        if u.port:
            host += ':%d' % u.port
        return urlunsplit(u._replace(netloc='%s:***@%s' % (u.username or '', host)))
    except ValueError:  # SILENT-OK: not a parseable url; show nothing that might be a secret
        return '<the database address>'


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
    Raw blocks -- `sql`, a bare `show`, and `render` in ANY kind or spelling (`render`,
    `render html`/`scripts`/`code`/`fragment`, and the `render as <kind>` form) -- capture non-Mohio
    lines that legitimately contain ', ", {{ }} and, for `render code`, whole lines of Mohio SOURCE
    like `task: done`; masking them keeps the string-hygiene scan (Unit C) and every other line-level
    check from mistaking that content for code. The render kind is DERIVED from the opener, not
    enumerated, so a kind added later cannot silently escape the mask (it did twice: fragment, `as`)."""
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
        # DERIVE the render kind, never enumerate it. Every `render`, `render <kind>` and
        # `render as <kind>` opens a raw block that closes on `render: done`, so matching the
        # `render` opener itself covers all of them. Enumerating the kinds (html/scripts/code) meant
        # each kind added later escaped the mask and reintroduced FAC-15 in that kind: `fragment`
        # (Batch 30) and the `as` spelling (Batch 40) both failed here until this derived instead.
        if code == "render" or code.startswith("render "):
            return "render"
        if code in ("sql", "show"):
            return code
        return None
    import re as _re

    def _closes(raw, code):
        # The closer as the GRAMMAR reads it. A render ends only on its own `render: done` (spaces
        # around the colon allowed), never on a bare `done` or any other closer: the text a page
        # displays is never program code (RULED by Ron 2026-10-09). `show` and `sql` also close on a
        # bare `done`, as their grammar rules do.
        if raw == "render":
            return bool(_re.match(r'^render\s*:\s*done(?![A-Za-z0-9_])\s*$', code))
        return code == f"{raw}: done" or code == "done"

    out, raw = [], None
    for line in masked.split(NL):
        code = line.strip()
        if raw is None:
            out.append(line)                       # opener (or ordinary) line stays
            raw = _opener(code)
        elif _closes(raw, code):
            out.append(line); raw = None           # closer stays
        else:
            out.append(' ' * len(line))            # raw interior -> spaces
    return NL.join(out)


# -- Result formatter -----------------------------------------------------------

def _list_text(value, interp):
    """A list or a record as the program shows it: each item as `show` shows it alone, joined by
    ", " (T1-LIST-DISPLAY-FORM and T1-RECORD-DISPLAY-FORM, RULED 2026-10-15), through the
    interpreter's one display rule, which also masks a protected field."""
    if interp is None or not hasattr(interp, '_display_str'):
        if isinstance(value, dict):
            return ", ".join(f"{k}: {v}" for k, v in value.items())
        return ", ".join(str(v.to_python() if type(v).__name__ == "MohioValue" else v) for v in value)
    from mohio_interpreter import MohioValue
    return interp._display_str(MohioValue(value, 'shape' if isinstance(value, dict) else 'list'))


def _print_result(result, verbose=False, interp=None):
    # THE WRAPPER IS NOT THE VALUE. Every runtime value is a MohioValue, and formatting one with
    # `%s` prints its repr: `print "hi"` reported `Result MohioValue('hi', 'string')`, showing a
    # pioneer the interpreter's own bookkeeping where their value belonged. Unwrapped here
    # because this is the display boundary; the double-wrapping that caused the other routes is
    # fixed at construction, and this is the remaining one where a bare wrapper is handed
    # straight to a format string.
    # A PROGRAM VALUE IS NEVER A RESPONSE. `give back` and a refusal reach here as the interpreter's
    # response envelope, a plain dict; a program's last value reaches here wrapped. A saved row with
    # a `status` field used to print as `Response  due`, the row itself dropped, because the test
    # below looked at the dict's keys rather than at where the value came from.
    is_value = type(result).__name__ == "MohioValue"
    if is_value:
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
    if not is_value and isinstance(result, dict) and ("status" in result or "body" in result):
        status = result.get("status", "")
        body   = result.get("body", "")
        if isinstance(body, (list, dict)) and "json" in str(result.get("content_type") or ""):
            # `give back ... as json` IS JSON: show the JSON it sends, exact numbers and all
            from mohio_interpreter import _json_dumps_exact
            body = _json_dumps_exact(body)
        elif isinstance(body, (list, dict)):
            body = _list_text(body, interp)
        if isinstance(status, int):
            status_str = green(str(status)) if status < 300 else \
                         yellow(str(status)) if status < 400 else red(str(status))
        else:
            status_str = str(status)
        _print_program_output(f"\n  {bold('Response')}  {status_str}  {body}")
        # The HOW, when the envelope carries one. Printed under the response rather than folded
        # into the body, so the body stays exactly the string several tests assert on.
        _hint = result.get("hint") or ""
        if _hint:
            for _hint_line in str(_hint).splitlines():
                print(f"    {dim(_hint_line)}")
    else:
        if isinstance(result, (list, dict)):
            result = _list_text(result, interp)
        print(f"\n  {bold('Result')}  {result}")
    print()


# -- Shared parse + validate step ----------------------------------------------

def _check_new_handlers(source, filename):
    """`new sh.X` is not a handler (RULED by Ron, 2026-10-07), and every one in the file is named.

    A handler for arriving data is `listen for sh.X at /path` ... `listen: done`, which behaves
    exactly as `new` did. Each `new sh.` line is listed with its replacement, so a file with forty
    of them is fixed in one pass rather than one error at a time, and `mio fmt --write` makes every
    swap. `new` stays reserved for an arrival condition that is not built yet
    (T1-NEW-ARRIVAL-CONDITION). Comments and raw-text bodies are masked first, so a page that shows
    the old spelling as text is not refused."""
    import re as _re
    masked = _mask_noncode(source).split('\n')
    original = source.split('\n')
    hits = []
    for i, l in enumerate(masked):
        m = _re.match(r'^(\s*)new(\s+sh\.\S.*)$', l)
        if m:
            shown = original[i].strip() if i < len(original) else l.strip()
            hits.append((i + 1, shown, 'listen for' + shown[len('new'):]))
    if not hits:
        return
    lines = '\n'.join(f"  Line {n}: {old}   ->   {new}" for n, old, new in hits)
    _die(f"`new` is not a handler, so {len(hits)} line(s) here are refused:\n{lines}\n"
         f"  A handler for arriving data is written `listen for sh.<Shape> at /path` and closed "
         f"with `listen: done`; it behaves exactly as `new` did. Run `mio fmt --write {filename}` "
         f"to make every swap. (`new` is reserved for an arrival condition that is not "
         f"built yet: T1-NEW-ARRIVAL-CONDITION.)", exit_code=1)


def _check_idempotent_by(source, filename):
    """`idempotent by` is now written `once per` (RENAMED by Ron, 2026-10-28, D7), and every old line
    in the file is named with its replacement, so a file is fixed in one pass, and `mio fmt --write`
    makes every swap. Only the words moved: a route keeps its answers under the key exactly as before.
    Comments and raw-text bodies are masked first, so a page that shows the old spelling is not refused."""
    import re as _re
    masked = _mask_noncode(source).split('\n')
    original = source.split('\n')
    hits = []
    for i, l in enumerate(masked):
        if _re.match(r'^\s*idempotent\s+by\b', l):
            shown = original[i].strip() if i < len(original) else l.strip()
            hits.append((i + 1, shown, 'once per' + _re.sub(r'^idempotent\s+by', '', shown)))
    if not hits:
        return
    lines = '\n'.join(f"  Line {n}: {old}   ->   {new}" for n, old, new in hits)
    _die(f"`idempotent by` is now written `once per`, so {len(hits)} line(s) here are refused:\n{lines}\n"
         f"  Only the words changed: the route still answers a repeated request with the same key from "
         f"its first answer. Run `mio fmt --write {filename}` to make every swap.", exit_code=1)


def _check_against_closers(source, filename):
    """Every `check ... against` needs its own `check: done`, at its own indentation (HC-013).

    THE CLOSER IS THE SAME WORD AS ITS PARENT'S. A `check ... against` nested under a `when` or an
    `otherwise`, written without its closer, took the enclosing `check`'s `check: done` as its own.
    The enclosing check was then left open, the handler ran on past `new: done` and swallowed every
    route after it, and the errors pointed at closers that were correct (reproduced from the health
    app's report: the Patient page's `request.id` checked against LoginRequest). The parse that
    steals the closer is a valid parse, which is why the grammar's trap for the unclosed form never
    fired. The layout says which block a closer belongs to, so it is read from the layout, before
    the parse, and nothing about how anything parses changes. Raw-text bodies and comments are
    blanked first (`_mask_noncode`, the same masking the ASCII gate uses), because a render body may
    sit at any indentation, HTML at column 0 included."""
    import re as _re
    masked = _mask_noncode(source).split('\n')
    original = source.split('\n')

    def _ind(l):
        return len(l) - len(l.lstrip(' '))

    for i, l in enumerate(masked):
        st = l.strip()
        if not _re.match(r'^check\s+\S.*\s+against\s+\S', st):
            continue
        k = _ind(l)
        closed, end = False, i
        for j in range(i + 1, len(masked)):
            t = masked[j]
            ts = t.strip()
            if not ts:
                continue
            if _ind(t) > k:
                end = j
                continue
            closed = _ind(t) == k and bool(_re.match(r'^check:\s*done\b', ts))
            break
        if not closed:
            pad = ' ' * k
            shown = original[i].strip() if i < len(original) else st
            _die(f"Line {i + 1}: `{shown}` needs its own closer, `check: done`, at its own "
                 f"indentation. Its handlers end at line {end + 1}; without the closer, the next "
                 f"`check: done` below is taken as this check's, and the block that closer belongs "
                 f"to runs on past its own end and swallows what follows.\n"
                 f"  Add `check: done` under it, at the same indentation:\n"
                 f"{pad}{shown}\n{pad}    on.failure\n{pad}        ...\n{pad}check: done",
                 exit_code=1)


def _check_render_closers(source, filename):
    """A `render` that runs past its handler refuses, naming both lines (HC-006).

    A render body is raw text, so a render missing its own `render: done` took the NEXT one in the
    file: everything between -- the handler's `request: done`, whole routes after it -- became page
    text, those routes vanished, and `mio check` reported no errors. Reduced to 16 lines: `/a`'s
    render without its closer swallowed the `/b` route entire. Indentation cannot bound a render
    (its HTML may sit at column 0).

    ONLY `render: done` CLOSES A RENDER, AND EVERY OTHER LINE IS PAGE TEXT (RULED by Ron 2026-10-09,
    from the Volarix summit app). This used to treat any handler opener or closer line in the body
    as proof the render had run on, so a page showing Mohio source in `<pre><code>` with a
    `check: done` line of its own was refused. What is certain instead: a render that took a later
    render's `render: done` holds that later render's OPENER in its body, and a render with no
    `render: done` anywhere after it has none to close on. Either one refuses, naming the line."""
    import re as _re
    lines = source.split('\n')
    _open = _re.compile(r'^render(\s+(as\s+)?[A-Za-z_]\w*)?\s*$')
    _close = _re.compile(r'^render\s*:\s*done(?![A-Za-z0-9_])\s*$')
    i = 0
    while i < len(lines):
        st = lines[i].strip()
        if _open.match(st):
            pad = ' ' * (len(lines[i]) - len(lines[i].lstrip(' ')))
            for j in range(i + 1, len(lines)):
                tj = lines[j].strip()
                if _close.match(tj):
                    i = j
                    break
                if _open.match(tj):
                    _die(f"Line {i + 1}: this `render` has no `render: done` of its own, so its page "
                         f"text runs on to line {j + 1}, where another `render` opens, and takes "
                         f"everything between as text: any route in between would never exist.\n"
                         f"  Close the render before line {j + 1}:\n"
                         f"{pad}render\n{pad}    ...\n{pad}render: done", exit_code=1)
            else:
                _die(f"Line {i + 1}: this `render` has no `render: done`, so its page text runs to "
                     f"the end of the file and takes every line after it as text.\n"
                     f"  Close it where its page ends:\n"
                     f"{pad}render\n{pad}    ...\n{pad}render: done", exit_code=1)
        i += 1


def _check_orphan_handlers(source, filename):
    """A handler line after its block's closer refuses on its own line (HC-013, on the real file).

    The health app's login wrote a `check ... against` with its handlers and its `check: done`, and
    then the same `on.failure` / `give back` / `check: done` tail a second time. The repeated tail
    belonged to no block; the parse could not place it, and the error surfaced a hundred lines
    later as "`journey: done` closes the wrong block", pointing at a line that was correct. A
    handler line directly after a closer at the SAME indentation is that orphan, so it is named
    where it is, before the parse."""
    import re as _re
    masked = _mask_noncode(source).split('\n')
    original = source.split('\n')

    def _ind(l):
        return len(l) - len(l.lstrip(' '))

    prev = None   # (index, stripped) of the previous non-blank line
    for i, l in enumerate(masked):
        st = l.strip()
        if not st:
            continue
        if prev is not None and _re.match(r'^(on\.failure|on\.success|otherwise)\s*$', st):
            pi, pst = prev
            # The ENCLOSING block decides: a handler after a nested block's closer belongs to the
            # block around it (a find's on.failure after its `with: done`), unless that block is one
            # that never takes handlers -- a route, a journey, a task, a loop, a branch, a render.
            enc = next((masked[k].strip() for k in range(i - 1, -1, -1)
                        if masked[k].strip() and _ind(masked[k]) < _ind(l)), '')
            _no_handlers = _re.match(r'^(listen\s+for|request\s+for|journey|task|repeat|loop|while|'
                                     r'each|when|shape|render)\b', enc)
            # DEEPER than the closer above it, the handler sits inside the block that closer just
            # ended (the health app's repeated tail): always an orphan. LEVEL with it, it belongs to
            # the enclosing block, an orphan only when that block takes no handlers.
            _closer = _re.match(r'^[\w.]+\s*:\s*done\s*$', pst)
            if _closer and (_ind(l) > _ind(masked[pi])
                            or (_ind(l) == _ind(masked[pi]) and _no_handlers)):
                shown = original[i].strip()
                _die(f"Line {i + 1}: this `{shown}` comes after `{pst}` on line {pi + 1}, "
                     f"so it belongs to no block: the block it was written for is "
                     f"already closed, and the error would otherwise surface far below, at a closer "
                     f"that is correct.\n"
                     f"  To fix it, remove this `{shown}` and what it runs (a repeated tail), or move "
                     f"it above line {pi + 1} so it sits inside the block.", exit_code=1)
        prev = (i, st)


def _check_or_in_where(source, filename):
    """`or` inside a `where` / `and` line refuses on its own line (the or-in-where message).

    A where has no `or`. Written anyway, the parse had nowhere to put it, so the WHOLE block was read
    as something else: `find rows in db.items / where status is "a" or status is "b"` checked clean
    with warnings that `find`, `in` and `where` were "set but never used" -- the query silently
    became assignments. Read from the line, before the parse, with strings and raw text blanked so
    an `or` inside a value never trips it."""
    import re as _re
    masked = _mask_noncode(source).split('\n')
    original = source.split('\n')
    for i, l in enumerate(masked):
        st = _re.sub(r'"(?:\\.|[^"\\])*"', '""', l.strip())
        if not _re.match(r'^(where|and)\s', st) or not _re.search(r'\bor\b', st):
            continue
        shown = original[i].strip() if i < len(original) else st
        pad = ' ' * (len(l) - len(l.lstrip(' ')))
        _die(f"Line {i + 1}: `{shown}` uses `or`, and a `where` has no `or`, so the whole block "
             f"around it would be read as something else and the filter would never run.\n"
             f"  To keep rows that match either value of one field, list them in the where:\n"
             f"{pad}where status \"a\", \"b\"\n"
             f"  For either of several conditions, use a `match any` block:\n"
             f"{pad}match any\n{pad}    status to \"a\"\n{pad}    kind to \"b\"\n{pad}match any: done",
             exit_code=1)


def _check_download_name(source, filename):
    """A download's file name is text in quotes or a plain name; anything else refuses on its own line.

    The name after `download` is a string (with `{{ }}` naming the file from a value) or, RULED
    2026-10-12 (OR-7), a bare name read exactly as `"{{ name }}"`. An expression there still does not
    parse, and the parse failed two lines further on, at the handler's `listen: done`, which pointed
    at a line with nothing wrong on it (found with HC-124). Read from the line before the parse, with
    comments and raw text blanked."""
    import re as _re
    masked = _mask_noncode(source).split('\n')
    original = source.split('\n')
    for i, l in enumerate(masked):
        m = _re.match(r'^\s*give\b.*\bas[ \t]+download[ \t]+([^"\s].*?)\s*$', l)
        if not m:
            continue
        name = m.group(1)
        if _re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*', name):
            continue          # a plain or dotted name: the grammar reads it as "{{ name }}"
        shown = original[i].strip() if i < len(original) else l.strip()
        _inner = name[1:-1].strip() if name.startswith('(') and name.endswith(')') else name
        _die(f"Line {i + 1}: `{shown}` names the download with `{name}`, and a download's file name "
             f"is text in quotes or a plain name, so this line cannot be read.\n"
             f"  To name the file from a value, put the value inside the quotes:\n"
             f"    {shown[:shown.rfind(name)]}\"{{{{ {_inner} }}}}\"",
             exit_code=1)


def _parse_and_validate(source, filename, verbose=False):
    """Parse source and run compile-time validation.

    Returns (tree, ctx) or exits on hard failure. ctx.errors is populated if validation
    fails; ctx.warnings are always printed.

    This text used to sit further down, below the first executable lines, where Python
    reads it as a statement that evaluates a string and discards it rather than as the
    function's documentation. It was moved up so `help(_parse_and_validate)` shows it.
    """
    # THE PROGRAM'S FOLDER FOR SECTOR PROFILES (T1-SECTOR-APP-FOLDER-PROFILE): the first program file
    # this command parses names it; an include or a journey parsed after it is in the same app.
    import mohio_sector_loader as _SLp
    import os as _os_p     # this function imports `os` again further down, which makes the name local
    if _SLp._program_folder is None and filename and _os_p.path.isfile(str(filename)):
        _SLp.set_program_folder(_os_p.path.dirname(_os_p.path.abspath(str(filename))))
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
        lines = src.split(chr(10))          # ORIGINAL lines, for the error snippet
        # SCAN THE MASKED SOURCE, so the ASCII rule applies only to CODE. `_mask_noncode` blanks
        # comment and raw-block interiors (`sql`, a bare `show`, `render`/`render html`/`render
        # scripts`) to spaces, preserving offsets -- so ordinary typographic punctuation, an
        # accented name or a currency symbol in a render's visible TEXT or an ATTRIBUTE is content,
        # not code, and is no longer refused as "Non-ASCII in executable code" (FAC-15). Strings are
        # left intact by the mask, so the string-tracking below still skips their content. A non-ASCII
        # identifier, keyword or operator sits in code and is still flagged.
        flat = _mask_noncode(src)
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
            print(f"    For a request handler, write it on its own: "
                  f"`request for sh.X at /path ... request: done`.")
            print(f"    The page CLASSIFICATION model (public:/private:/hidden:/authorize:) is")
            print(f"    unchanged -- that is access control, and separate from this.")
            sys.exit(1)

    # The comma is the separator (Ron, 2026-10-13: keep the comma, d8260e9 stands). A trailing comment
    # is allowed on the line, or the dotted form reached the parser and got a raw syntax error.
    _sector_dotted = _re_sector.compile(
        r'^\s*sector\s*:\s*([A-Za-z][A-Za-z0-9_-]*(?:\s*\.\s*[A-Za-z][A-Za-z0-9_-]*)+)\s*(?://.*|/\*.*)?$')
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
    _check_against_closers(source, filename)
    _check_or_in_where(source, filename)
    _check_render_closers(source, filename)
    _check_download_name(source, filename)
    _check_orphan_handlers(source, filename)
    _check_new_handlers(source, filename)
    _check_idempotent_by(source, filename)

    # -1. PARSE CACHE. The cache holds the TREE only, keyed on the source and the parse-affecting
    # files (`_parse_fingerprint`). A hit skips the Earley parse (the 87s on a large app) but never
    # the verdict: the symbol-table pass below, Layer 1, the AST build and every scan still run, so
    # a new or re-worded rule always fires. This is why splitting the cache is safe -- there is no
    # cached verdict to replay.
    tree = None
    if filename and filename != "<string>":
        tree = _load_ast_cache(filename, source)
        if verbose:
            print(f"  [cache] parse cache {'hit -- skipping parse' if tree is not None else 'miss -- parsing fresh'}")

    # 0. Symbol table pre-pass -- O(n), ALWAYS run (its reserved-word warnings are part of the
    # verdict, and on a cache hit it is the only place they come from). It also feeds the
    # pretokenizer when a parse is actually needed.
    try:
        from mohio_symbol_table import extract_symbols
        from mohio_transformer import MOHIO_RESERVED_EXACT
        symbol_table = extract_symbols(source)
        for w in symbol_table.warnings:
            print(f"  !  {w}")
        _reserved = MOHIO_RESERVED_EXACT
    except ImportError:
        symbol_table = None
        _reserved = None

    # 1. Parse (only on a cache miss). The pretokenizer marks dotted user-var accesses as a single
    # USERVAR_DOTTED token so Earley never branches on them. Central parse path -- check, run, serve.
    if tree is None:
        try:
            if symbol_table is not None:
                from mohio_pretokenizer import pretokenize
                # Mark against the WHOLE-APP declaration set, not just this file's names: a name
                # declared in an include or the journey is then marked here too, so Earley never
                # branches on it (the measured cold-parse win). The tree is IDENTICAL either way --
                # marking picks a terminal, it does not change the grammar -- proven by the hostile
                # pass and the file-by-file equivalence check. `app_mark_names` is internally robust
                # (it skips an unreadable app file rather than raising), so no fallback guard is
                # needed here; a real error surfaces through the enclosing parse handler below.
                _names = symbol_table.all_user_names()
                if filename and filename != "<string>":
                    from mohio_symbol_table import app_mark_names
                    _names = _names | app_mark_names(filename)
                parse_source = pretokenize(source, _names, _reserved)
            else:
                parse_source = source  # graceful degradation
            grammar = _load_grammar()
            parser  = _make_parser_cached(grammar)
            # SEGMENTED FIRST (mohio_segment.py): a file is cut only between two closed blocks of
            # kinds proven to parse identically alone, the segments parse on every core and are
            # cached one by one, so an edit re-parses only the segment it touched. The tree is the
            # whole-file tree, every node and position. Anything that does not parse alone gives
            # None, and the whole file parses as it always has, reporting any error exactly as before.
            tree = None
            if filename and filename != "<string>":
                from mohio_segment import parse_segmented
                tree = parse_segmented(parser, parse_source, filename, _parse_fingerprint())
                if verbose and tree is not None:
                    print("  [cache] parsed in segments")
            if tree is None:
                tree = parser.parse(parse_source)
        except Exception as e:
            from lark.exceptions import UnexpectedInput
            if isinstance(e, UnexpectedInput):
                _print_parse_error(e, source, filename)
                sys.exit(1)
            _die(f"Parse failed: {e}", exit_code=1)
        # A successful parse is cached here (the tree only). A file that fails to parse never
        # reaches this line, so a parse error is never cached.
        if filename and filename != "<string>":
            _save_ast_cache(filename, source, tree)

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
                _nf = FileNotFoundError(
                    f"include: cannot find '{stmt.path}' (looked relative to "
                    f"{base_dir} and the current directory)")
                # Carried for `cmd_check`'s own catch (mio.py) to report the real source line
                # rather than line 0 -- IncludeDecl.line is already populated by the generic
                # line-propagation in MohioTransformer._call_userfunc.
                _nf.mohio_line = getattr(stmt, 'line', 0) or 0
                raise _nf
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
            # `_parse_and_validate` now owns the parse cache (the tree only, keyed on the
            # parse-affecting files), so an included file is parsed once and cached, and every check
            # after reads it. Batch 11's separate lookup-and-save here is no longer needed.
            sub_tree, _sub_ctx = _parse_and_validate(sub_src, target, verbose)
            sub_prog = _t(sub_tree, sub_src)
            _resolve_includes(sub_prog, target, _seen, _depth + 1, verbose)
            # Every line in the included statements names its own file, so a finding on one is
            # reported at that file and line, not at the including file's line of the same number.
            from mohio_ast import tag_source_file
            tag_source_file(sub_prog.statements, target)
            new_stmts.extend(sub_prog.statements)
        else:
            new_stmts.append(stmt)
    program.statements = new_stmts
    return program


def _app_root_for_file(page_dir):
    """The application root for a SINGLE file: the HIGHEST ancestor directory (including the file's
    own) that holds a `journey.mho`, with none above it. A file with no `journey.mho` anywhere above
    it is standalone -- its own directory, no cascade.

    WHY THE HIGHEST, NOT THE NEAREST, ANCESTOR (Batch 37 item 4): the security floor is declared at
    the TOP of an application; a nearer subfolder journey is a tightening UNDER it, never the floor
    itself, so the nearest journey would miss the root sector and let a loosening subfolder pass.

    WHY NOT THE WORKING DIRECTORY: every single-file path used to default the cascade root to the
    working directory, so the cascade -- and the sector tighten-only refusal riding on it --
    assembled only when the developer happened to be standing in the app root. A security floor that
    depends on which directory a command was run from is the gap this closes: a developer checking
    one page of an application from ANYWHERE now gets the same answer as checking it from the app
    root, because the root is found from the file, not from the cwd."""
    page_dir = os.path.abspath(page_dir)
    highest, cur = None, page_dir
    while True:
        if os.path.isfile(os.path.join(cur, 'journey.mho')):
            highest = cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return highest or page_dir


def _journey_chain_dirs(page_dir, app_root):
    """The directories from the application ROOT down to the page's own directory (root-first),
    while the page is actually under the root. A page outside the root cascades only within its
    own directory. This is the walk T1-JOURNEY-CASCADE needs: a page inherits every ancestor
    journey up to the root, not only the one beside it."""
    page_dir = os.path.abspath(page_dir)
    root = os.path.abspath(app_root) if app_root else page_dir
    def _within(child, parent):
        try:
            return os.path.commonpath([os.path.realpath(child), os.path.realpath(parent)]) \
                == os.path.realpath(parent)
        except ValueError:
            return False
    if not _within(page_dir, root):
        return [page_dir]
    chain, cur = [], page_dir
    while True:
        chain.append(cur)
        if os.path.realpath(cur) == os.path.realpath(root):
            break
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    chain.reverse()          # root first, page's own directory last
    return chain


_JOURNEY_PROGRAMS = {}


def _journey_program(journey_path, j_src, verbose=False):
    """A journey's program, parsed, validated, transformed and with its includes resolved ONCE per
    process, and handed to each page as its own deep copy.

    Every page in a folder inherits the journey, so a 95-page check or serve used to repeat this
    work 95 times: the parse came from the disk cache, but the cache load, the validation and the
    transform ran again for each page (measured on the banking app: 10.9s of an 11.9s warm check).
    The copy is what makes the reuse safe: a page's assembly and its run may annotate or rewrite
    the nodes it was handed, and no page may see another page's changes. Keyed on the file's real
    path and its exact text, so an edited journey is never served from the memo."""
    import copy
    key = (os.path.realpath(journey_path), j_src)
    prog = _JOURNEY_PROGRAMS.get(key)
    if prog is None:
        from mohio_transformer_ast import transform as _t
        # Same content-keyed AST cache the include path uses, so the shared spine is parsed once.
        j_tree, _j_ctx = _parse_and_validate(j_src, journey_path, verbose)
        prog = _t(j_tree, j_src)
        _resolve_includes(prog, journey_path, verbose=verbose)  # a journey may include too
        _JOURNEY_PROGRAMS[key] = prog
    return copy.deepcopy(prog)


# PAGE CLASSIFICATION STRICTNESS, loosest to strictest: the ONE order. The journey cascade uses it
# (a subfolder journey may only tighten a class an ancestor set, 6d44d83) and so does `[important]`
# (a journey line marked [important] may only tighten what a page declares, RULED by Ron 2026-10-22).
# Module level so both read the same object and a change moves both; test_battery_important_tighten_
# only asserts it is defined once. private: is unlisted but reachable direct, one notch tighter than
# public:; authorize: requires login; hidden: is never served, "the strongest tier".
_CLASS_RANK = {'public': 0, 'private': 1, 'authorize': 2, 'hidden': 3}


def _apply_journey(program, file_path, verbose=False, app_root=None):
    """htaccess-style auto-discovery, now CASCADING (T1-JOURNEY-CASCADE, Batch 36).

    A page inherits the `journey.mho` in its own directory AND every `journey.mho` in an ancestor
    directory up to the application root, applied ROOT-FIRST. Registration processes the root
    journey first, each nearer journey after it, and the page LAST, so the NEAREST declaration
    wins on restatement and the page still overrides them all -- exactly the resolution Ron ruled:
    the root applies first and reaches subfolders, and a nearer journey overrides the root for
    what it restates. A subfolder journey REPLACES rather than adds for what it declares, because
    a re-registration by the same name (a shape, a task, a connection, the sector) is the later
    one winning, not a merge.

    The application root bounds the upward walk: `mio serve <dir>` / `mio check <dir>` pass the
    served directory, and a single-file run passes the working directory, so a page run from the
    app root (`mio run api/accounts.mho`) reaches the root journey. Before this, `_apply_journey`
    read only the page's OWN directory, so a root journey's connection, shapes, sector and limits
    never reached a page under `api/` -- the demo blocker: every such page ran with no database
    connection while the root journey declared one.

    SECURITY ONLY TIGHTENS is enforced separately, in the transformer/enforce layer, not here:
    this assembles the cascade; a subfolder that would LOOSEN the root's sector or classification
    is refused at check. This function never lets a nearer journey drop a security floor -- it
    only orders the statements; the floor is enforced on the assembled program.
    """
    from mohio_ast import SectorDecl
    if not file_path:
        return program
    page_path = os.path.abspath(file_path)
    page_dir = os.path.dirname(page_path)
    # A directory serve/check passes its directory as the root; a single file finds its app root
    # from the file itself (the highest ancestor with a journey), so the cascade -- and the sector
    # floor it carries -- is the same wherever the command was run from (Batch 37 item 4).
    chain = _journey_chain_dirs(page_dir, app_root if app_root else _app_root_for_file(page_dir))
    prepend = []
    floor_sectors = []   # SECURITY ONLY TIGHTENS: the sector floor set by nearer-to-root journeys
    def _tightens(nearer, floor):
        # `nearer` (a dotted sector string) covers `floor` when it IS floor or adds levels under it
        return nearer == floor or nearer.startswith(floor + '.')
    # PAGE CLASSIFICATION ONLY TIGHTENS (T1-PAGE-CLASSIFICATION-MODEL, the same ruling extended
    # from sector to the other journey-level security floor): a nearer journey may not weaken the
    # classification an ancestor already set for a path. The strictness order is the module-level
    # _CLASS_RANK, shared with `[important]` below.
    floor_classifications = []   # accumulates (kind, norm_path, source_journey_path) root-first
    journey_class_groups = []    # per journey.mho: (journey_path, [(kind, norm_path, line, important)])
    _journey_dirs = []           # (folder, journey.mho) applied to this page, root-first
    def _norm_jpath(p):
        p = str(p or '')
        return p[:-1] if len(p) > 1 and p.endswith('/') else p
    for d in chain:
        journey_path = os.path.join(d, 'journey.mho')
        is_page_dir = (os.path.realpath(d) == os.path.realpath(page_dir))
        if not os.path.isfile(journey_path):
            # A leading underscore marks a file private to routing, and the spine is found by
            # exact name -- so `_journey.mho` is silently not a journey at all. The sector floor,
            # shared connections and compliance settings just stop applying, with nothing printed.
            # That is the one combination worth stopping on, because the failure looks like nothing
            # happening. Kept at the page's own directory, exactly as before the cascade.
            if is_page_dir:
                stray = os.path.join(d, '_journey.mho')
                if os.path.isfile(stray):
                    _die(f"Found `_journey.mho` in {d}, but no `journey.mho`.\n\n"
                         f"  The spine must be named `journey.mho` exactly. A leading "
                         f"underscore keeps a file out of routing, so `_journey.mho` is never "
                         f"applied to anything -- the sector, shared connections and compliance "
                         f"it declares would silently not be in force.\n\n"
                         f"  To proceed: rename it to `journey.mho`. It is never routable and "
                         f"never served, so it does not need the underscore. If it is not meant "
                         f"to be the spine, give it another name.", exit_code=1)
            continue
        if os.path.realpath(journey_path) == os.path.realpath(page_path):
            continue  # don't apply a journey.mho to itself
        j_src = open(journey_path, encoding='utf-8-sig').read()
        j_prog = _journey_program(journey_path, j_src, verbose)
        # SECURITY ONLY TIGHTENS (Ron's ruling, the gap that prompted T1-JOURNEY-CASCADE): a
        # nearer journey may add sector levels or a new sector, but it may NEVER drop or weaken a
        # sector the root established. A nearer journey that restates the sector must COVER every
        # floor sector -- with the same sector or a tighter one under it. If it does not, it is
        # loosening a security floor from a subfolder, and it is refused at check, naming the root
        # declaration it would have loosened. A nearer journey SILENT on the sector inherits the
        # floor unchanged (its statements carry no SectorDecl, so nothing is dropped).
        own_sectors = [s.sector for s in j_prog.statements if isinstance(s, SectorDecl) and s.sector]
        if own_sectors and floor_sectors:
            for f in floor_sectors:
                if not any(_tightens(o, f) for o in own_sectors):
                    # Levels are held joined by dots internally; a message shows them the way a
                    # `sector:` line is written, with commas. The dotted form is retired and refuses,
                    # so a message that showed it taught the reader a line that fails (17c).
                    _shown = lambda s: ', '.join(str(s).split('.'))
                    _die(
                        f"The journey at {journey_path} sets sector "
                        f"`{'; '.join(_shown(o) for o in own_sectors)}`, "
                        f"which would LOOSEN the sector `{_shown(f)}` a parent journey already requires.\n\n"
                        f"  A subfolder journey may only TIGHTEN security, never relax it: it can "
                        f"add levels under `{_shown(f)}` (for example `sector: {_shown(f)}, <deeper>`) "
                        f"or keep it as is, but it cannot drop it or switch to a sector that does not "
                        f"include it.\n\n"
                        f"  To proceed: include `{_shown(f)}` (or a level under it) in this journey's "
                        f"`sector:` line, or remove the `sector:` line here to inherit `{_shown(f)}` "
                        f"from the parent.", exit_code=1)
        if own_sectors:
            floor_sectors = own_sectors   # nearer replaces the floor, having covered it

        # PAGE CLASSIFICATION ONLY TIGHTENS: this journey's own public:/private:/hidden:/
        # authorize: entries, checked against every floor entry an ancestor already set.
        # public:/private:/hidden:/authorize: are journey-BODY declarations (`journey NAME
        # ... journey: done`), not bare top-level statements the way `sector:` is -- so they
        # sit on JourneyDecl.body, and collecting them needs one level of unwrapping sector:
        # never needed.
        from mohio_ast import JourneyMeta as _JM2, JourneyDecl as _JD2
        def _journey_metas(stmts):
            for s in stmts:
                if isinstance(s, _JM2):
                    yield s
                elif isinstance(s, _JD2):
                    for s2 in _journey_metas(s.body):
                        yield s2
        own_class = [(m.kind, _norm_jpath(p), journey_path)
                     for m in _journey_metas(j_prog.statements) if m.kind in _CLASS_RANK
                     for p in (m.value or [])]
        for (kind2, path2, _own_journey) in own_class:
            for (kind, path, floor_journey) in floor_classifications:
                if path2 == path:
                    # Restating the SAME path: allowed only at the same strictness or tighter.
                    if _CLASS_RANK[kind2] < _CLASS_RANK[kind]:
                        _die(
                            f"The journey at {journey_path} sets `{kind2}: {path2 or '/'}`, which "
                            f"would LOOSEN `{kind}: {path or '/'}` the journey at {floor_journey} "
                            f"already requires.\n\n"
                            f"  A subfolder journey may only TIGHTEN a page's classification, never "
                            f"relax it: `{path or '/'}` must stay `{kind}` or something stricter "
                            f"here, not drop to `{kind2}`.\n\n"
                            f"  To proceed: restate it as `{kind}: {path or '/'}` (or a stricter "
                            f"kind), or remove this line to inherit `{kind}` from the parent "
                            f"journey. A narrower path under `{path or '/'}` (for example "
                            f"`{(path or '').rstrip('/')}/exception`) may still be carved out as "
                            f"`public:`, the same exception mechanism a single journey already "
                            f"supports.", exit_code=1)
                elif path.startswith(path2 + '/'):
                    # path2 is a BROADER ancestor of the floor path -- a weaker kind here would
                    # swallow the stricter, narrower floor entry under its own wider coverage.
                    if _CLASS_RANK[kind2] < _CLASS_RANK[kind]:
                        _die(
                            f"The journey at {journey_path} sets `{kind2}: {path2 or '/'}`, which "
                            f"covers `{path}` too and would LOOSEN `{kind}: {path}` the journey at "
                            f"{floor_journey} already requires there.\n\n"
                            f"  A subfolder journey may only TIGHTEN a page's classification, never "
                            f"relax it, even through a broader path that happens to cover a "
                            f"narrower one a parent already protected.\n\n"
                            f"  To proceed: narrow `{kind2}: {path2 or '/'}` so it does not cover "
                            f"`{path}`, or raise it to `{kind}` (or stricter).", exit_code=1)
                # elif path2.startswith(path + '/'): a NARROWER path under the floor -- the
                # existing public:-overrides exception mechanism; allowed at any strictness.
        floor_classifications.extend(own_class)
        journey_class_groups.append((journey_path, [
            (m.kind, _norm_jpath(p), getattr(m, 'line', 0) or 0, bool(getattr(m, 'important', False)))
            for m in _journey_metas(j_prog.statements) if m.kind in _CLASS_RANK for p in (m.value or [])]))

        # A JOURNEY'S LINES ARE THE JOURNEY'S (Zork, 6.0.1). Prepended as plain numbers, a finding a
        # page's check made on journey code was printed against the PAGE's text at the same number
        # (right number, wrong file). Stamped with the journey's file, as an included file's lines
        # are (HC-137), it names `journey.mho:N` and shows the journey's own line.
        # DEFENSIVE, and unreachable given the fix: the reachability scan now leaves journey code to
        # the journey's own check (`from_journey`), so no finding on a journey line reaches a page's
        # output today (measured: removing this stamp leaves test_battery_spine_findings_attribution
        # green). It mirrors the include stamping so a future scan that does report one still names
        # the right file.
        from mohio_ast import tag_source_file as _tag_source_file
        _tag_source_file(j_prog.statements, journey_path)
        _journey_dirs.append((d, journey_path))
        prepend.extend(j_prog.statements)
    # A PAGE BELOW ITS FOLDER'S CLASS (RULED by Ron 2026-10-22: `[important]` is a FLOOR). At serve time
    # every journey's gate runs, so the stricter class always holds; that is unchanged. What a page
    # declares below the class a journey.mho sets for its path is a line that does nothing, so the check
    # says so: a WARNING when the journey's line is plain, a REFUSAL when it is marked `[important]`. A
    # page at or above the journey's class is silent either way: `[important]` never drags a stricter
    # page down. Judged on the shared _CLASS_RANK, per journey, at the page's path, the way the serve
    # gate decides it (a `public:` in that journey overrides its other classes there, so a journey's own
    # carve-out is never mistaken for a floor).
    program.cascade_warnings = []
    if journey_class_groups and os.path.basename(page_path) != 'journey.mho':
        from mohio_ast import JourneyMeta as _JM4, JourneyDecl as _JD4
        def _page_metas(stmts):
            for s in stmts:
                if isinstance(s, _JM4):
                    yield s
                elif isinstance(s, _JD4):
                    yield from _page_metas(s.body)
        def _covers(entry, path):
            return path == entry or path.startswith(entry.rstrip('/') + '/')
        def _journey_class_at(entries, path):
            """This journey's effective class at `path`, as its serve gate decides it: (kind, path,
            line, important) of the deciding line, or None when the journey says nothing there."""
            covering = [e for e in entries if _covers(e[1], path)]
            if not covering:
                return None
            public = [e for e in covering if e[0] == 'public']
            if public:
                return public[0]
            top = max(_CLASS_RANK[e[0]] for e in covering)
            same = [e for e in covering if _CLASS_RANK[e[0]] == top]
            return next((e for e in same if e[3]), same[0])
        _order = ' < '.join(sorted(_CLASS_RANK, key=_CLASS_RANK.get))
        for m in _page_metas(program.statements):
            if m.kind not in _CLASS_RANK:
                continue
            pkind, pline = m.kind, getattr(m, 'line', 0) or 0
            for ppath in (_norm_jpath(p) for p in (m.value or [])):
                for (fjourney, entries) in journey_class_groups:
                    # the page's own path, and any narrower path this journey classifies under it
                    points = [ppath] + [e[1] for e in entries if e[1] != ppath and _covers(ppath, e[1])]
                    for q in points:
                        floor = _journey_class_at(entries, q)
                        if floor is None or _CLASS_RANK[pkind] >= _CLASS_RANK[floor[0]]:
                            continue
                        fkind, fpath, fline, fimportant = floor
                        if fimportant:
                            _die(
                                f"Line {pline}: this page declares `{pkind}: {ppath}`, below the "
                                f"`[important]` floor `{fkind}: {fpath}` set at line {fline} of {fjourney}. "
                                f"A page may match or tighten an `[important]` journey class, never fall "
                                f"below it ({_order}). Raise this line to `{fkind}:` or stricter, or remove "
                                f"it to keep the journey's `{fkind}:`.", exit_code=1)
                        program.cascade_warnings.append((pline, (
                            f"Line {pline}: this page declares `{pkind}: {ppath}`, but the journey at "
                            f"{fjourney} (line {fline}) classifies `{fpath}` as `{fkind}:`, which is "
                            f"stricter ({_order}). Every journey's gate runs, so this page is served as "
                            f"`{fkind}:` and its own line does nothing. Remove the line, or change the "
                            f"journey's line if this page should be looser. (A warning: the stricter class "
                            f"is what serves. Marking the journey line `[important]` makes this a "
                            f"refusal.)")))
                        break
    # Prepend root-first; the page's own statements register last, so the page wins over every
    # journey and a nearer journey wins over the root.
    program.statements = prepend + program.statements
    # A JOURNEY IS SHARED, so what it declares is used by the pages under it (the Zork lane's false
    # warning, 2026-10-10): an `ai.decide` declared here and invoked from index.mho was reported
    # "declared but never invoked", because a journey checked on its own is a program of one file.
    # The names every inheriting page writes travel with it; scan_ai_decide_never_invoked counts
    # them. Only names, from the text with comments removed, so a check never parses the pages.
    if os.path.basename(page_path) == 'journey.mho':
        program.spine_names = _names_in_pages_under(page_dir, page_path)
    elif _journey_dirs:
        # A PAGE checked with its journeys sees the journey's decisions as its own statements, so a
        # decision declared there and invoked from ANOTHER page under it was reported "declared but
        # never invoked" against this page (Zork's _cheats.mho under `mio check --all`). The pages
        # under each journey are what use it, so their names travel here too, but only for the
        # names the journeys declare: a decision this page declares itself is judged on this page.
        _spine = set()
        for _jd, _jp in _journey_dirs:
            _spine |= _names_in_pages_under(_jd, _jp)
        program.spine_names = _spine
        program.journey_declared = {
            str(getattr(s, 'name', '') or '') for s in prepend
            if type(s).__name__ == 'AiDecideBlock'}
    # IS THIS PROGRAM SERVED AS CHECKED? A journey checked on its own, and an underscore partial
    # (kept out of routing, reached only through a page that includes it), are never served as
    # they stand: a page that serves them supplies what they lean on, such as the store an
    # `ai.audit` writes to. Every served page is checked with its journeys prepended, so a page
    # that truly lacks the store is still refused there (T1-CHECK-ALL-SPINE-NO-STORE).
    _base = os.path.basename(page_path)
    program.served_as_checked = not (_base == 'journey.mho' or _base.startswith('_'))
    return program


def _names_in_pages_under(folder, journey_path):
    """Every word written in the .mho pages under `folder` (the pages a journey there reaches),
    comments and quoted text removed. Used only to tell whether a journey's declaration is used."""
    names = set()
    for root, _dirs, files in os.walk(folder):
        for fn in files:
            p = os.path.join(root, fn)
            if not fn.endswith('.mho') or os.path.realpath(p) == os.path.realpath(journey_path):
                continue
            try:
                src = open(p, encoding='utf-8-sig').read()
            except OSError:
                continue   # SILENT-OK: an unreadable page only means its names are not counted (a possible warning), never a passed check
            src = re.sub(r'/\*.*?\*/', ' ', src, flags=re.S)
            src = re.sub(r'//[^\n]*', ' ', src)
            src = re.sub(r'"(?:[^"\\\n]|\\.)*"', ' ', src)
            names.update(re.findall(r'[A-Za-z_]\w*', src))
    return names

def _webhook_startup_rotation_gate(program, interp=None):
    """STARTUP half of the secret-rotation rule (item 7). Under a regulated sector, an app must not
    START on a webhook secret that is missing its rotation record or is past its interval -- the same
    condition `mio check` refuses, enforced again at boot so a deploy that skipped check cannot run
    expired. Never mid-request: this runs once, here, at startup. Outside a sector the scan returns
    nothing and this is a no-op. On the way through, it records each rotation to the audit chain the
    first time a new rotation date is seen (once per rotation, not per request)."""
    # Imported directly, not guarded: mohio_reachability is a core module. If it cannot import, the
    # compiler is already broken, and a loud ImportError beats silently skipping a security gate. The
    # CLI calls `webhook_rotation_problems` (the shared core), never a `scan_` function by hand -- that
    # is a structural invariant; scanners run through run_scans, this gate reuses their logic.
    from mohio_reachability import (webhook_rotation_problems, _rotation_error_message,
                                    _webhook_secrets_under_sector, _rotation_status)
    problems = webhook_rotation_problems(program)
    if problems:
        msg = "\n\n".join(_rotation_error_message(where, secret_env, status, detail)
                          for (_vdecl, where, secret_env, status, detail) in problems)
        _die(f"Cannot start under a regulated sector -- webhook secret rotation:\n\n{msg}", exit_code=1)
    # Durable record: every secret here is 'ok' (the gate above refused otherwise). Record each
    # rotation once, deduped by (secret, date), to the hash-chained audit sink.
    if interp is not None:
        for secret_env, (_vdecl, where) in _webhook_secrets_under_sector(program).items():
            status, detail = _rotation_status(secret_env)
            if status == 'ok':
                try:
                    interp.record_secret_rotation(secret_env, where)
                except Exception as _e:
                    print(f"  [rotation-audit] could not record rotation for env.{secret_env}: {_e}",
                          file=sys.stderr)


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


def _supplied_db_address(args):
    """The address `--db` or `--memory` supplies for this run, or None when neither was given. The
    CLI's own persistent file is not one: it is where an undeclared program's data lives, never a
    database a declared `connect` lands on when its variable is unset (the connect flip)."""
    if getattr(args, 'memory', False):
        return ':memory:'
    return getattr(args, 'db', None) or None


def _app_identity(mho_file):
    """Which app this is, as its audit envelopes record it (the relay-identifier blocker, RULED by Ron
    2026-10-08): MOHIO_APP_ID when it is set, which a deployment whose paths differ from development
    sets; otherwise a stable hash of the app root, the topmost folder above the program that holds a
    `journey.mho` (the program's own folder when none does), so `mio serve app/` and `mio run
    app/page.mho` are one app. None when there is neither a program nor the variable."""
    import hashlib
    explicit = (os.environ.get('MOHIO_APP_ID') or '').strip()
    if explicit:
        return explicit
    if not mho_file:
        return None
    here = os.path.realpath(mho_file)
    folder = here if os.path.isdir(here) else os.path.dirname(here)
    root, probe = folder, folder
    while True:
        if os.path.isfile(os.path.join(probe, 'journey.mho')):
            root = probe
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    return 'app:' + hashlib.sha256(os.path.normcase(root).encode('utf-8')).hexdigest()[:16]


def _cli_interpreter(ai, verbose, mho_file, args, serving=False):
    """An interpreter for a CLI run/serve/test: the resolved SQLite path as before, and only the
    flags as the supplied address."""
    from mohio_interpreter import MohioInterpreter
    interp = MohioInterpreter(ai=ai, verbose=verbose, db_path=_resolve_sqlite_db_path(mho_file, args))
    interp.supplied_address = _supplied_db_address(args)
    interp.app_id = _app_identity(mho_file)
    # Handed to the protected-data-on-SQLite startup guard: whether this is a serve, and the
    # platform variable that marks a deploy (_PLATFORM_VARS, the one `serve --dev` refuses on).
    interp.serving = bool(serving)
    interp.deploy_marker = next((v for v in _PLATFORM_VARS if (os.environ.get(v) or '').strip()), None)
    return interp


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
            # FAILS LOUD (ruled by Ron, 2026-10-03): a scanner that dies partway has checked
            # some rules and not others, so running anyway proceeds with an unknown amount of
            # enforcement actually applied. This now blocks exactly like a Layer-1 error, through
            # the SAME ctx.errors the "Build failed" check right below already acts on.
            _scan_incomplete_warn(ctx, "Layer 3 whole-program scanners", _scan_err)
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
                ai = _construct_ai_runtime(args.api_key, verbose, path=filename)
                if verbose:
                    print(dim(f"  AI runtime: Anthropic API ({ai._model})"))
            except RuntimeError as e:
                _die(str(e))
        else:
            ai = MockAiRuntime()
            if verbose:
                print(dim("  AI runtime: mock (use --ai for real Anthropic API)"))

        interp = _cli_interpreter(ai, verbose, filename, args)

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
        # THE OUTPUT BEFORE THE FAILURE IS SHOWN FIRST. A runtime error used to discard every
        # `show` the program had already made, so a program that printed three lines and failed
        # on the fourth looked as though nothing ran at all, which sends debugging to the wrong
        # line. `interp` may not exist when construction itself failed.
        for _shown_line in (getattr(locals().get('interp'), 'shown', None) or []):
            _print_program_output(f"  {_shown_line}")
        _print_runtime_error(e, filename, source=source, line=_line)
        if verbose:
            traceback.print_exc()
        sys.exit(2)

    # Surface `show` output — the program's visible output, in order. Without
    # this, only the final statement's value appears (so a `show` inside a loop
    # looked like it only ran the last iteration).
    shown = getattr(interp, 'shown', None) or []
    for line in shown:
        _print_program_output(f"  {line}")
    # Echo the Result/Response only when it adds information beyond the show
    # output: a real give-back (dict), or a value-returning program with no show.
    if isinstance(result, dict) or not shown:
        _print_result(result, verbose, interp)
    elif verbose:
        _print_result(result, verbose, interp)

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
    from mohio_db_drivers import sqlite_path_from_address
    try:
        db_path = sqlite_path_from_address(db_path)    # `sqlite:///app.db` names the file app.db
    except RuntimeError as e:
        _die(str(e))

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
        # EGRESS-EXEMPT: the learned-language store's word pairs; no tagged field is stored there
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
        # EGRESS-EXEMPT: the learned-language store's word pairs; no tagged field is stored there
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

    to_lang = getattr(args, 'to_lang', None)
    # THE FILE SAYS WHAT IT IS WRITTEN IN (T1-TRANSLATE-REVERSE-SILENT-NOOP). `--from` defaulted to
    # English, so a Klingon file translated `--to english` skipped the step out of Klingon, was written
    # back unchanged, and the command said "keywords translated to english". The file's own
    # `// language:` line names its language; `--from` that disagrees with it is refused, not obeyed.
    import re as _re_lang
    try:
        _head = Path(args.file).read_text(encoding='utf-8-sig')[:2000]
    except (OSError, ValueError):
        _head = ''                      # an unreadable file is refused by the read below, by name
    _declared = _re_lang.search(r'^\s*//\s*language:\s*([\w-]+)', _head, _re_lang.M | _re_lang.I)
    _declared = _declared.group(1).lower() if _declared else None
    def _lang_key(lang):
        lang = str(lang).strip().lower()
        return 'en' if lang in ('en', 'english', 'canonical') else lang
    from_lang = getattr(args, 'from_lang', None)
    if from_lang and _declared and _lang_key(from_lang) != _lang_key(_declared):
        print(f"  {red('Error:')} --from {from_lang} disagrees with the file, which declares "
              f"`// language: {_declared}`.")
        print(f"  Leave --from out and the file's own line is used: "
              f"mio translate {args.file} --to <lang>")
        sys.exit(1)
    from_lang = (from_lang or _declared or 'en')  # SILENT-OK: no line and no --from is the base language, as every file is unless it says otherwise

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

        if _lang_key(from_lang) == _lang_key(to_lang):
            print(f"  {red('Error:')} {source_file.name} is already written in {from_lang}, so there is "
                  f"nothing to translate to {to_lang}. Nothing was written.")
            sys.exit(1)

        # Step 1: translate from source lang to canonical (English)
        if from_lang not in ('en', 'english'):
            from_map_path = find_langmap(from_lang, maps_dir)
            if not from_map_path:
                # It used to assume English and write the file back unchanged, saying it had translated.
                print(f"  {red('Error:')} no language pack for '{from_lang}', the language this file is "
                      f"written in, so it cannot be read back into the base language. Nothing was written.")
                print(f"  Available langmaps in {maps_dir}:")
                for lm in glob.glob(os.path.join(maps_dir, '*.langmap')):
                    print(f"    {os.path.basename(lm)}")
                sys.exit(1)
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

        # THE RECEIPT IS THE OUTCOME. Code that comes out word for word as it went in was not
        # translated, so it is never written under a name that says it was.
        def _code_only(text):
            return '\n'.join(l for l in text.splitlines()
                             if l.strip() and not _re_lang.match(r'\s*//', l)).strip()
        if _code_only(translated) == _code_only(source):
            print(f"  {red('Error:')} translating {source_file.name} from {from_lang} to {to_lang} "
                  f"changed no word, so it was not written. Check the file's `// language:` line, or "
                  f"name its language with --from.")
            sys.exit(1)

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

        # EGRESS-EXEMPT: program SOURCE text the developer asked to write; no runtime value
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
            if _load_ast_cache(target, source) is not None:
                print(f"  [warmup] {target} -- parse cache valid.")
                continue
            print(f"  [warmup] Pre-parsing {target} (build-time only)...")
            try:
                # `_parse_and_validate` caches the PARSE (the tree only) for any file that parses.
                # The old full-pipeline-clean gate is gone: the cache no longer holds a verdict, so
                # there is no clean/dirty verdict to guard -- every check re-runs Layer 1, the AST
                # build and every scan against the cached tree.
                tree, ctx = _parse_and_validate(source, target, verbose=False)
                if tree is not None:
                    print(f"  [warmup] {target} -- parse cached.")
                else:
                    print(f"  [warmup] {target} -- NOT cached: it did not parse. Run mio check.")
            except Exception as e:
                print(f"  [warmup] {target} -- parse error: {e}")
        except Exception as e:
            print(f"  [warmup] {target} -- could not read: {e}")

    print("  [warmup] Cold-start delay eliminated.")

def _cached_parse_tree(source, filename):
    """The parse tree for `source`: from the parse cache when it holds one, else parsed exactly as
    `mio check` parses (pretokenized against the app's declared names) and saved to that cache.
    Raises the parser's own error and prints nothing.

    WHY IT IS SHARED (item 10, measured on the health app's folder): directory discovery parsed
    every file with the raw parser, no cache and no pretokenizing, 281 seconds for 48 files before
    the server could answer, and then compiled each file again. Discovery through this helper fills
    the cache the compile reads, so a file is parsed once per run."""
    is_file = bool(filename) and filename != "<string>"
    tree = _load_ast_cache(filename, source) if is_file else None
    if tree is not None:
        return tree
    from mohio_symbol_table import extract_symbols, app_mark_names
    from mohio_transformer import MOHIO_RESERVED_EXACT
    from mohio_pretokenizer import pretokenize
    names = extract_symbols(source).all_user_names()
    if is_file:
        names = names | app_mark_names(filename)
    tree = _make_parser_cached(_load_grammar()).parse(pretokenize(source, names, MOHIO_RESERVED_EXACT))
    if is_file:
        _save_ast_cache(filename, source, tree)
    return tree


def _declared_route_paths(abs_path):
    """The route paths a file's top-level handlers DECLARE via `at`. Directory discovery uses these
    instead of the file's name (Batch 37 item 2): a handler that writes `new sh.X at /api/pay`
    answers at /api/pay, not at the filename-derived path. A file with no declared handler path
    routes by filename as before. Returns [] if the file cannot be parsed here -- it then falls back
    to filename routing and its parse error surfaces when the page is served, exactly as before."""
    try:
        source = Path(abs_path).read_text(encoding='utf-8-sig')
    except (OSError, ValueError):
        # ValueError covers UnicodeDecodeError -- a non-UTF-8 file cannot declare a path here. It
        # falls back to filename routing and is skipped, with its error reported, by the serve loop.
        return []
    try:
        from mohio_transformer_ast import transform as _t
        from mohio_ast import top_handlers
        prog = _t(_cached_parse_tree(source, abs_path), source)
    except Exception:
        # A file that will not parse cannot declare a path here; leave it to filename routing, where
        # its error is reported when the page is served. This is a routing HINT, never the place a
        # parse error is first raised, so falling back is correct rather than a swallowed failure.
        # (`except Exception` deliberately does not catch SystemExit -- a `_die` still propagates.)
        return []
    # EVERY HANDLER THE RUNTIME ROUTES, not only the top-level ones (HC-008). A handler written
    # loose inside a `journey` body is routed by the program when its file is served
    # (_exec_JourneyDecl's `journey_loose`; a ListenBlock's listeners are read the same way, for an
    # older program that still holds one), but this read only the top level. Its path never entered the directory route table, so under
    # `mio serve .` the request 404'd before reaching the file -- while serving that file alone
    # answered it. Measured on the health app: /documents and /documents/reconcile 404'd. Routes
    # whose path happened to equal the file name (/referrals in referrals.mho) only LOOKED fine,
    # because the file-name fallback produced the same URL.
    from mohio_ast import JourneyDecl, ListenBlock
    handlers = list(top_handlers(prog.statements))
    for stmt in prog.statements:
        if isinstance(stmt, JourneyDecl):
            handlers += top_handlers(stmt.body)
            for b in (stmt.body or []):
                if isinstance(b, ListenBlock):
                    handlers += top_handlers(b.listeners)
        elif isinstance(stmt, ListenBlock):
            handlers += top_handlers(stmt.listeners)
    paths = []
    for h in handlers:
        p = getattr(h, 'path', None)
        if p and str(p).startswith('/'):
            paths.append(str(p))
    return paths


def _load_authored_seed(directory, args, interps):
    """Seed the application's AUTHORED data at startup, from `_seed.json` at the application root.

    AUTHORED data -- the lookup and configuration rows that ARE the application, versioned with its
    code -- ships in `_seed.json` (a leading underscore, so the static server never serves it) beside the pages and journey. `mio serve <dir>` loads it AUTOMATICALLY,
    no flag, because a convention that needs a flag is not one; an explicit `--seed` overrides it.
    Absent, nothing happens (no complaint): an app with no authored data ships no file. Malformed, it
    fails loud naming the file, because a broken authored-data file must not deploy silently. Seeding
    is idempotent (a row with an id upserts; a row without one is skipped when an identical row
    exists), so re-running on every deploy and restart is safe.

    AUTHORED, NOT ACCUMULATED: the file lives in the source tree and travels with the code, which is
    what makes it authored. ACCUMULATED data -- the rows users create in use -- lives only in the
    database, never in a repo file; putting a user's row here means committing user data to source
    control, which is the visibly-odd thing this convention makes odd. `interps` is the per-page
    interpreter map; all pages share one application database, so one interpreter seeds it once."""
    if not interps:
        return
    # A stale `seed.json` (the OLD name) is now DEAD on both counts and silent on both: the loader
    # reads only `_seed.json`, so an old file at this name seeds NOTHING, and the static server now
    # denies the old name, so it no longer LEAKS either. Neither the dead load nor the closed leak
    # announces itself, and someone copying an older example or project inherits exactly this file.
    # Say so once, here, at serve: name the file, say it does nothing, and name the rename that fixes
    # it (what / why / how, one line).
    _stale = Path(str(directory)) / 'seed.json'
    if _stale.is_file():
        print("  [mohio.serve] WARNING: seed.json at the app root is no longer loaded or served -- "
              "authored data now ships in _seed.json (a leading underscore keeps it off the web). "
              "Rename it: seed.json -> _seed.json.")
    _seed_path = None
    if getattr(args, 'seed', None):
        _seed_path = Path(args.seed)
        if not _seed_path.exists():
            _die(f"Seed file not found: {args.seed}", exit_code=3)
    else:
        # `_seed.json` -- a LEADING UNDERSCORE, on purpose. Authored data is authority configuration
        # (a denylist, an allowlist, roles, thresholds), so it must never be served. A leading
        # underscore is Mohio's private-file convention and the static server never serves such a file
        # (verified: `/_seed.json` -> 404), so the file that seeds the app is not one a browser can
        # fetch. The earlier name `seed.json` had no underscore and WAS served (T0-SEED-JSON-SERVED-AS-
        # STATIC): the guide already documented the underscore name as the safe one, so this aligns the
        # loader with the documented, served-safe name.
        _conv = Path(str(directory)) / '_seed.json'
        if _conv.is_file():
            _seed_path = _conv
    if _seed_path is None:
        return
    try:
        import json as _json
        _seed_data = _json.loads(_seed_path.read_text(encoding='utf-8'))
    except Exception as _e:
        _die(f"Cannot read authored-data seed `{_seed_path}`: {_e}. It must be JSON of the form "
             f'{{"table": [{{"id": 1, ...}}]}}. Fix the file or remove it.', exit_code=1)
    if not isinstance(_seed_data, dict):
        _die(f"Authored-data seed `{_seed_path}` must be a JSON object mapping each table to its "
             f'rows, like {{"table": [{{"id": 1, ...}}]}}.', exit_code=1)
    # All pages share one application database; seed it once, through a page that HAS a database. If no
    # page connects one, an explicit --seed is a mistake (there is nothing to seed into) while the
    # convention file is simply not applicable and is left alone.
    _seed_it = next((_it for _it in interps.values() if getattr(_it, '_db', None) is not None), None)
    if _seed_it is None:
        if getattr(args, 'seed', None):
            _die("--seed was given, but no page declares a `connect` -- there is no database to seed "
                 "into. Add `connect db as sqlite` (or postgres / mysql / mongodb), or drop --seed.")
        return
    # SEED DATA NEVER RE-APPLIES OVER A LIVE DATABASE AT START (Ron, 2026-10-13). It ran on every start
    # and upserted each row by its id, so a row the app had changed since (a checker's authority
    # binding, in the Volarix checkpoint) went back to its seeded value on the next restart, with
    # nothing said. The convention file now seeds only when every table it names is absent or empty;
    # an explicit `--seed` is the explicit command and still applies.
    if not getattr(args, 'seed', None) and _seeded_tables_hold_rows(_seed_it._db, _seed_data):
        _held = [t for t in _seed_data if _table_holds_rows(_seed_it._db, t)]
        print(f"  {dim(f'authored data in {_seed_path.name} was not re-applied: the database already holds rows in ' + ', '.join(_held) + '. Seeding runs only on an empty database, so a restart never puts back what the app has changed. To apply it again on purpose: mio serve ... --seed ' + _seed_path.name)}")
        return
    try:
        _seed_it.seed_db(_seed_data)
    except Exception as _e:
        _die(f"Authored-data seed `{_seed_path}` failed to load: {_e}", exit_code=1)
    _rows = sum(len(v) for v in _seed_data.values() if isinstance(v, list))
    print(f"  {dim(f'authored data: {_rows} row(s) across {list(_seed_data.keys())} from '
                  f'{_seed_path.name} (applied to an empty database)')}")


def _table_holds_rows(db, table):
    """True when `table` exists and has at least one row. An absent table holds none; a table that
    exists and cannot be counted is an error, raised, never read as empty."""
    cols = db.table_columns(table) if hasattr(db, 'table_columns') else None
    if not cols:
        return False
    return int(db.count(table) or 0) > 0


def _seeded_tables_hold_rows(db, seed_data):
    return any(_table_holds_rows(db, t) for t in (seed_data or {}) if isinstance(seed_data.get(t), list))


_LOOPBACK_HOSTS = ('127.0.0.1', 'localhost', '::1')

# What the platform sets on EVERY deployed app, read from its deploy job (getmohio-control,
# app/Jobs/DeployApp.php): MOHIO_BUILD_SHA (line 267, the runtime image a tenant is built from, the
# variable mohio_version.py documents as stamped by the deploy pipeline) and
# MOHIO_LOCAL_DISK_EPHEMERAL (line 304, "a platform fact" set unconditionally). A developer's
# machine sets neither.
_PLATFORM_VARS = ('MOHIO_BUILD_SHA', 'MOHIO_LOCAL_DISK_EPHEMERAL')


def _dev_serve_refusal(args, root):
    """Why `mio serve --dev` may not run here, or None (T1-DEVELOPMENT-SERVE-MODE, RULED by Ron:
    production is the default, and development is refused under a sector and on a deployment).

    Development mode answers a refused page with its compiler error, so it is for one developer's
    machine (RULED by Ron 2026-10-11): it binds a loopback host only, and it refuses on a platform
    deploy, which it knows by the variables the platform sets on every deployed app (_PLATFORM_VARS).
    A sector or compliance profile in force means a regulated app, which is served whole or not
    at all."""
    import re as _re
    host = str(getattr(args, 'host', '') or '').strip().lower()
    if host not in _LOOPBACK_HOSTS:
        return (f"development mode binds this machine only, because a page that did not load "
                f"answers with its compiler error, and --host is {host!r}. Leave --host out (it "
                f"defaults to 127.0.0.1) or write `--host 127.0.0.1`; to serve other machines, "
                f"serve without --dev.")
    on_platform = [v for v in _PLATFORM_VARS if (os.environ.get(v) or '').strip()]
    if on_platform:
        return (f"{on_platform[0]} is set, which the platform sets on a deployed app, and development "
                f"mode is for a developer's machine. Serve without --dev; a deployed app is served "
                f"whole or not at all.")
    files = []
    if os.path.isdir(str(root)):
        for d, dirs, fs in os.walk(str(root)):
            dirs[:] = [x for x in dirs if not x.startswith('.')]
            files += [os.path.join(d, f) for f in fs if f.endswith('.mho')]
    else:
        files = [str(root)]
    for fp in sorted(files):
        try:
            text = Path(fp).read_text(encoding='utf-8-sig')
        except (OSError, ValueError):
            continue        # an unreadable file is refused as a load failure, named on its own
        for n, line in enumerate(text.splitlines(), 1):
            if _re.match(r'\s*(sector|compliance)\s*:', line):
                return (f"a regulated profile is in force ({os.path.relpath(fp, str(root)) if os.path.isdir(str(root)) else fp} "
                        f"line {n}: `{line.strip()}`), and a regulated app is served whole or not at "
                        f"all. Serve without --dev, and fix every file that does not load.")
    return None


def _dev_shared_failures(directory, journeys):
    """The shared files under `directory` that do not load: every journey, and every file a page or
    journey includes as a statement (`include "_part.mho"`). A markup fragment reached only through
    `{{ include }}` in a render is text, not a program, and is not checked here."""
    import re as _re
    import contextlib as _ctxl
    import io as _io
    targets = set(os.path.realpath(j) for j in journeys)
    for d, dirs, fs in os.walk(str(directory)):
        dirs[:] = [x for x in dirs if not x.startswith('.')]
        for f in fs:
            if not f.endswith('.mho'):
                continue
            try:
                text = Path(os.path.join(d, f)).read_text(encoding='utf-8-sig')
            except (OSError, ValueError):
                continue    # reported as a load failure of its own
            for m in _re.finditer(r'^\s*include\s+"([^"]+)"', text, _re.M):
                p = _find_include_path(m.group(1), d)
                if p:
                    targets.add(os.path.realpath(p))
    out = []
    for fp in sorted(targets):
        rel = os.path.relpath(fp, str(directory))
        try:
            src = Path(fp).read_text(encoding='utf-8-sig')
            # The same three steps a page's compile runs: parse and validate, build, whole-program scans.
            with _ctxl.redirect_stdout(_io.StringIO()):
                _tree, _ctx = _parse_and_validate(src, fp, verbose=False)
                if not _ctx.errors:
                    from mohio_transformer_ast import transform as _t
                    from mohio_enforce import enforce_scans as _es
                    _es(_ctx, _t(_tree, src))
            if _ctx.errors:
                out.append(f"{rel} -- {len(_ctx.errors)} error(s)")
        except SystemExit:
            out.append(f"{rel} -- does not parse")
        except Exception as e:      # a build error (a closer mismatch, say) is this file not loading
            out.append(f"{rel} -- {_first_line(e)}")
    return out


def _first_line(err):
    """The first line of an error's text that says something; some begin with a blank line."""
    return next((ln.strip() for ln in str(err).splitlines() if ln.strip()), type(err).__name__)


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
    if getattr(args, 'dev', False):
        _no_dev = _dev_serve_refusal(args, directory)
        if _no_dev:
            _die(f"`mio serve --dev` does not start: {_no_dev}", exit_code=1)
        print(f"  {yellow('!')}  development mode: a file that does not load answers 503 instead of "
              f"stopping the server. Never deploy this way.")

    # Discover all .mho files
    #   _name.mho  -- private: include target only, never routed
    #   journey.mho -- the spine, auto-applied to every page in its folder the way an
    #                  .htaccess applies to a directory. It is not a page, so it is not
    #                  a route. Listing it as one advertised a URL that answers 404.
    #                  _apply_journey finds it on disk by name, so skipping it here
    #                  does not stop it being applied.
    mho_files = []
    journeys = []
    pages = []
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
            pages.append(abs_path)
    # A PROGRESS LINE PER FILE (item 10). Reading the routes parses each file, and on a first run
    # (no parse cache yet) a 48-file app took minutes with nothing printed, which looks like a hang.
    if pages:
        print(f"  {dim(f'Reading {len(pages)} file(s); a first run parses each one, later runs reuse the cache.')}")
    for i, abs_path in enumerate(pages, 1):
        rel_path = os.path.relpath(abs_path, str(directory))
        print(f"  {dim(f'[{i}/{len(pages)}]')} {rel_path.replace(os.sep, '/')}", flush=True)
        # The filename-derived URL, the fallback for a file that declares no route path.
        filename_url = '/' + rel_path.replace(os.sep, '/')
        filename_url = filename_url[:-4]  # remove .mho
        if filename_url.endswith('/index'):
            filename_url = filename_url[:-6] or '/'  # /blog/index -> /blog/
        if not filename_url:
            filename_url = '/'
        # THE DECLARED PATH WINS (Batch 37 item 2). A handler that writes `at /some/path` is
        # registered at that path, not at the file's name -- a developer who declares a route and
        # gets a different one is the same silent surprise as an unmatched POST. A file with no
        # declared handler path routes by filename, as before.
        declared = _declared_route_paths(abs_path)
        for u in (declared or [filename_url]):
            mho_files.append((u, abs_path))

    if not mho_files:
        _die(f"No .mho files found in {directory}")

    # A route may be claimed by exactly one file. Two files answering the same method+path is a
    # collision the router cannot resolve, and silently letting the second SHADOW the first is the
    # same silent-surprise shape as the declared-path bug itself (Batch 37 item 2): a developer
    # would deploy with one route dead and no word. Refuse at startup, naming both files. (A file
    # legitimately declaring the same path more than once collapses to one route -- only DIFFERENT
    # files colliding is refused.)
    _by_url = {}
    for _u, _fp in mho_files:
        if _u in _by_url and os.path.realpath(_by_url[_u]) != os.path.realpath(_fp):
            _die(f"Two files claim the route {bold(_u)}:\n"
                 f"    {os.path.relpath(_by_url[_u], str(directory))}\n"
                 f"    {os.path.relpath(_fp, str(directory))}\n\n"
                 f"  A route answers from one file. This happens when a handler's `at` path (which "
                 f"wins over the filename) lands on a path another file already owns. Change one of "
                 f"the `at` paths, or rename a file, so each route has a single owner.", exit_code=1)
        _by_url.setdefault(_u, _fp)
    # Collapse a file's repeated identical path to a single route entry.
    mho_files = list(dict.fromkeys(mho_files))

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
    # A FILE THAT DOES NOT LOAD STOPS THE SERVER FROM STARTING (RULED by Ron 2026-10-08, replacing
    # hc014). hc014 had a failed file's routes answer 500 while the rest of the app served; before
    # that they were skipped and 404'd. Both are a partial app: the routes the files declare are not
    # the routes being served. Every failure is collected, with its lines, and serve refuses to
    # start naming each one, exactly as `mio serve file.mho` refuses a file that does not load.
    failures = {}

    def _load_failed(filepath, lines, reason):
        failures.setdefault(os.path.realpath(filepath), (filepath, [], reason))[1].extend(
            ln for ln in lines if ln)

    # Set up shared AI runtime
    from mohio_interpreter import MohioInterpreter, MockAiRuntime
    if args.ai:
        try:
            from mohio_ai import AnthropicAiRuntime
            ai = _construct_ai_runtime(args.api_key, verbose, path=directory)
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

    # ONE COMPILE PER FILE (item 10). A file declaring several routes is one program: it was
    # checked, assembled and had its declarations run once PER ROUTE, so a file with six routes
    # paid six times at startup. Its later routes now share the first compile -- the same one
    # interpreter for all of a file's routes that single-file serve has always used.
    _compiled = {}
    for url_path, filepath in mho_files:
        _prior = _compiled.get(os.path.realpath(filepath))
        if _prior is not None:
            if _prior[0] == 'failed':
                pass          # already recorded with its lines when the file was compiled
            else:
                programs[url_path], interps[url_path] = _prior
                print(f"  {green('v')}  {url_path}")
            continue
        try:
            # Read inside the try so an unreadable page (not UTF-8, vanished, etc.)
            # is reported per-file and skipped like a parse error -- one bad page must
            # not crash the whole directory server with a raw traceback (Unit B).
            source = Path(filepath).read_text(encoding='utf-8-sig')
            # A syntax error ends `_parse_and_validate` with its own printed report and an exit. It is
            # caught here only so the NEXT file is checked too and every failure is named at once;
            # the line comes from the report it printed.
            import contextlib as _ctxl
            import io as _io
            _buf = _io.StringIO()
            try:
                with _ctxl.redirect_stdout(_buf):
                    tree, ctx = _parse_and_validate(source, filepath, verbose=False)
            except SystemExit:
                _said = _buf.getvalue()
                print(_said, end='')
                import re as _re_ln
                _m = _re_ln.search(r'^\s*(\d+) \|', _said, _re_ln.M)
                _load_failed(filepath, [int(_m.group(1))] if _m else [], 'does not parse')
                _compiled[os.path.realpath(filepath)] = ('failed', 1)
                continue
            print(_buf.getvalue(), end='')
            if ctx.errors:
                for _e in ctx.errors:
                    _print_compile_error(_e, source, filepath)
                _load_failed(filepath, [getattr(_e, 'line', None) for _e in ctx.errors],
                             f"{len(ctx.errors)} error(s)")
                _compiled[os.path.realpath(filepath)] = ('failed', len(ctx.errors))
                continue
            from mohio_transformer_ast import transform
            program = transform(tree, source)
            program = _resolve_includes(program, filepath, verbose=False)
            # The served directory IS the application root, so the cascade walks from a page's
            # folder up to it (T1-JOURNEY-CASCADE): a page under api/ inherits the root journey.
            program = _apply_journey(program, filepath, verbose=False, app_root=str(directory))
            # Single door: Layer-3 scan on the assembled program before this route goes live.
            # A file that fails check/run must not be served just because it is in a directory.
            try:
                from mohio_enforce import enforce_scans as _enforce_scans
                _enforce_scans(ctx, program)
            except Exception as _scan_err:
                # FAILS LOUD (ruled by Ron, 2026-10-03), through the same ctx.errors the
                # "skipped" check right below already acts on -- see _scan_incomplete_warn.
                _scan_incomplete_warn(ctx, "Layer 3 whole-program scanners", _scan_err)
            if ctx.errors:
                for _e in ctx.errors:
                    _print_compile_error(_e, source, filepath)
                _load_failed(filepath, [getattr(_e, 'line', None) for _e in ctx.errors],
                             f"{len(ctx.errors)} error(s)")
                _compiled[os.path.realpath(filepath)] = ('failed', len(ctx.errors))
                continue
            interp = _cli_interpreter(ai, verbose, filepath, args, serving=True)
            interp.run_declarations(program)
            programs[url_path] = program
            interps[url_path] = interp
            _compiled[os.path.realpath(filepath)] = (program, interp)
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
                              f"refuse the GET. Move the write into a handler that owns it (`listen for sh.X`, `change to sh.X`).")
            except Exception:
                pass          # a reporting aid must never stop the server from starting
            print(f"  {green('v')}  {url_path}")
        except Exception as e:
            print(f"  {red('x')}  {url_path} -- {e}")
            _load_failed(filepath, [getattr(e, 'line', None)], _first_line(e))
            _compiled[os.path.realpath(filepath)] = ('failed', 1)

    refused_routes = {}
    if failures:
        _listed = []
        for _fp, _lines, _why in failures.values():
            _rel = os.path.relpath(_fp, str(directory))
            _at = (", line " + ", ".join(str(n) for n in sorted(set(_lines)))) if _lines else ""
            _listed.append(f"    {_rel}{_at} -- {_why}")
        # DEVELOPMENT STARTS THE FILES THAT LOAD (T1-DEVELOPMENT-SERVE-MODE), unless what failed is
        # shared: a journey or an included file carries shapes, tasks and gates every page using it
        # depends on, so those pages would be served wrong rather than refused.
        _shared = _dev_shared_failures(directory, journeys) if getattr(args, 'dev', False) else []
        if not getattr(args, 'dev', False) or _shared:
            _also = ""
            if _shared:
                _also = ("\n  `--dev` does not start it either: a shared file did not load, and every "
                         "page that uses it would run without what it declares:\n"
                         + "\n".join(f"    {_s}" for _s in _shared))
            _die(f"{len(failures)} file(s) in {directory} did not load, so the app is not started: serving "
                 f"the rest would answer for routes these files declare with something else.\n"
                 + "\n".join(_listed)
                 + "\n  Run `mio check` on each, correct what it reports, and start `mio serve` again."
                 + _also, exit_code=1)
        for _u, _fp in mho_files:
            _f = failures.get(os.path.realpath(_fp))
            if _f:
                refused_routes[_u] = {"file": os.path.relpath(_f[0], str(directory)).replace(os.sep, "/"),
                                      "lines": sorted(set(_f[1])), "reason": _f[2]}
        print(f"\n  {red('DEGRADED, NOT DEPLOYABLE')} -- {len(failures)} of "
              f"{len({os.path.realpath(fp) for _u, fp in mho_files})} file(s) did not load. In development "
              f"each of their routes answers 503 naming the file and line:")
        print("\n".join(_listed))
        print(f"  {dim('This clears only when every file loads. Run `mio check` on each.')}\n")

    if not programs:
        _die("No files compiled successfully.")

    # AUTHORED-DATA SEED CONVENTION (T0-DATA-DEPLOY, authored half). An application ships its AUTHORED
    # data -- the lookup and configuration rows that ARE the application, versioned with its code --
    # in `_seed.json` at the application root (the directory being served; the leading underscore keeps it unserved). `mio serve <dir>` loads it
    # automatically, no flag, because a convention that needs a flag is not one; an explicit `--seed`
    # overrides it. Absent, nothing happens (no complaint): an app with no authored data ships no file.
    # Malformed, it fails loud naming the file, because a broken authored-data file must not deploy
    # silently. Seeding is idempotent (a row with an id upserts; a row without one is skipped when an
    # identical row exists), so re-running it on every deploy and restart is safe.
    #
    # AUTHORED, NOT ACCUMULATED: the file lives in the source tree and travels with the code, which is
    # what makes it authored; ACCUMULATED data -- the rows users create in use -- lives only in the
    # database and never in a repo file. Putting a user's row here means committing user data to source
    # control, which is the visibly-odd thing this convention makes odd.
    _load_authored_seed(directory, args, interps)

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
                               redirects=redirects, status_responses=status_responses,
                               refused_routes=refused_routes)
    except (ImportError, AttributeError) as e:
        # NO SECOND, DEGRADED SERVER. This fell back to an inline FastAPI app that registered only
        # exact URLs, dropped redirects and status responses, and answered every request through a
        # `handle_request` the interpreter does not have, while printing "N routes loaded". An error
        # building the real server is a fault to report, never a reason to serve something else.
        _die(f"The directory server failed to start ({type(e).__name__}: {e}), so nothing is "
             f"served. If a package is missing, install it (`pip install fastapi uvicorn`); "
             f"otherwise this is a Mohio fault worth reporting with the line above.", exit_code=1)
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
        from mohio_server import install_log_timestamps
        install_log_timestamps()     # every log line from here starts with its UTC time (unit D)
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
    if getattr(args, 'dev', False):
        # One file either loads or does not, so --dev changes nothing here but its own refusals.
        _no_dev = _dev_serve_refusal(args, path)
        if _no_dev:
            _die(f"`mio serve --dev` does not start: {_no_dev}", exit_code=1)

    source = _read_source(path)

    print(f"\n  {bold('mio serve')}  {dim(f'v{VERSION}')}")
    print(f"  {dim('Loading')} {bold(filename)}")

    # `mio serve <file>` serves ONE page, on purpose (the one-file-per-page mode). But a developer
    # who means their whole application and points at their entry file gets one page and no word --
    # the same silent surprise that let an application lane's checks pass while the app did not work
    # (Batch 36 carry B). The MODE is unchanged; this only adds the word. When there are OTHER
    # routable pages beside this file, say how many were not loaded and name the directory form.
    try:
        _sib_dir = os.path.dirname(os.path.abspath(filename))
        _siblings = [f for f in os.listdir(_sib_dir)
                     if f.endswith('.mho') and not f.startswith('_')
                     and f != 'journey.mho'
                     and os.path.realpath(os.path.join(_sib_dir, f)) != os.path.realpath(os.path.abspath(filename))]
        if _siblings:
            print(f"  {yellow('Note')} serving ONE page. {len(_siblings)} other page(s) in this "
                  f"folder are not loaded -- run {bold('mio serve .')} to serve the whole "
                  f"application.")
    except OSError:
        pass

    # -- Parse: AST cache first ------------------------------------
    # If this is the first run or cache is stale, Earley parses the file.
    # This is slow on large files. We start uvicorn first so Railway
    # healthcheck passes, then serve requests once parsing completes.
    # `_parse_and_validate` reuses the cached PARSE (the tree) and always re-runs the verdict, so a
    # served file is validated by the current compiler, never by a cached verdict.
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
            # FAILS LOUD (ruled by Ron, 2026-10-03) -- see _scan_incomplete_warn.
            _scan_incomplete_warn(ctx, "Layer 3 whole-program scanners", _scan_err)
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
                ai = _construct_ai_runtime(args.api_key, verbose, path=filename)
                print(f"  {dim('AI runtime:')} Anthropic API ({ai._model})")
            except ImportError:
                _die("Anthropic SDK not installed. Run: pip install anthropic")
            except RuntimeError as e:
                _die(str(e))
        else:
            ai = MockAiRuntime()
            print(f"  {dim('AI runtime:')} mock {dim('(use --ai for real Anthropic API)')}")

        interp = _cli_interpreter(ai, verbose, filename, args, serving=True)

        # Run declarations first to establish the real db connection, THEN load authored data.
        interp.run_declarations(program)
        # AUTHORED-DATA SEED CONVENTION for a single-file app: `_seed.json` beside the file is picked up
        # with no flag, the same convention `mio serve <dir>` uses; an explicit --seed overrides it,
        # absent does nothing, malformed fails loud. The application root of a single file is its own
        # directory. (See `_load_authored_seed` for the authored-vs-accumulated rationale.)
        _load_authored_seed(Path(filename).resolve().parent, args, {filename: interp})

        # Under a regulated sector, refuse to start on an expired/unrecorded webhook secret, and
        # record each rotation to the audit chain once. Runs at startup only, never mid-request.
        _webhook_startup_rotation_gate(program, interp)

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
    # `map route as redirect` in a single served file: collect the redirects (and declared status
    # responses) the same way directory mode does, and hand them to the server, so a moved URL
    # actually redirects. Without this the single-file path registered no redirects and every one
    # answered 404 while `mio check` reported clean.
    from mohio_framework import resolve_map, resolve_map_responses, MapError
    try:
        _s_mounts, _s_redirects = resolve_map(program)
        server.redirects = _s_redirects
        server.status_responses = resolve_map_responses(program)
        for _src, (_dst, _code) in sorted(_s_redirects.items()):
            print(f"  {green('v')}  {_src} {dim(f'(map: -> {_dst}, {_code})')}")
    except MapError as _map_e:
        _die(f"map: {_map_e}")
    app    = create_app(server)

    # -- Start -------------------------------------------------
    print(f"\n  {green('v')}  Server ready")
    print(f"  {bold('Listening on')}  http://{host}:{port}")
    # No `API docs` line: the banner advertised /mio/docs, a route the server has never had, so
    # every start promised a page that answered 404 (found 2026-10-09). Only real routes are listed.
    print(f"  {bold('Health')}        http://{host}:{port}/mio/health")
    print(f"\n  {dim('Press Ctrl+C to stop')}\n")

    try:
        import uvicorn
        from mohio_server import install_log_timestamps
        install_log_timestamps()     # every log line from here starts with its UTC time (unit D)
        uvicorn.run(app, host=host, port=port, log_level="warning")
    except ImportError:
        _die("uvicorn not installed. Run: pip install uvicorn")
    except KeyboardInterrupt:
        print(f"\n\n  {dim('Server stopped.')}\n")

def _run_security_report(source, filename, ctx, program=None):
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
            # The program check already built is passed in, so a manifest beside the file costs no
            # second parse (it re-parsed the whole file, uncached, on every `check --security`).
            schema_errors = validate_field_references(source, schema, filename, program=program)
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


def _scan_incomplete_warn(ctx, scan_name, err):
    """A Layer-3 scan or compile-time guard crashed during `mio check`.

    FAILS LOUD (ruled by Ron, 2026-10-03; was previously "warn on stderr and keep going", which
    reported a program CLEAN when a scanner died before finishing it -- the exact silent-wrongness
    this language exists to refuse, generalized from a specific keyword to the enforcement
    MACHINERY itself. The rest of `mio check`'s other scanners still run (this does not raise),
    so the report is not cut short -- but the file now reports as failed, never clean, when any
    scan or check module did not finish.
    """
    from mohio_transformer_ast import MohioCompileError as _ScanCrashErr
    # A scanner reaching into mohio_sector_loader can raise a DELIBERATE, expected refusal --
    # a sector profile declaring a rule nothing enforces (SectorUnenforceableRuleError), or a
    # certified profile failing signature verification (SectorVerificationError) -- neither is
    # "the compiler crashed"; both are the program's own content being refused on purpose. Report
    # them as the plain refusal they are, not as a bug to file.
    from mohio_sector_loader import SectorUnenforceableRuleError, SectorVerificationError
    if isinstance(err, (SectorUnenforceableRuleError, SectorVerificationError)):
        ctx.errors.append(_ScanCrashErr(str(err), 0))
        return
    ctx.errors.append(_ScanCrashErr(
        f"{scan_name} crashed ({type(err).__name__}: {err}) and did not finish -- this is a "
        f"compiler bug, not a mistake in your program. Enforcement cannot be trusted when a "
        f"scan dies partway through, so this reports as failed rather than an unknown amount of "
        f"checking passing as clean. Report this bug, naming {scan_name!r}.", 0))
    print(f"  [enforce] {scan_name} did not finish "
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
    interp = _cli_interpreter(MockAiRuntime(), False, str(path), args)
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

_CHECK_NOTES = [
    "Checking your code... this is thorough, not fast. Go grab a coffee. :-)",
    "mio check is doing real work here. Earley parsers don't rush.",
    "Analyzing {n} lines of Mohio... might be a good time to stretch.",
    "Running full compliance and security analysis. Back in a moment.",
    "The compiler is reading every word. Give it a second.",
]

# A journey's own check, run once per process and reused by every page that inherits it.
_SHARED_FINDINGS = {}


def _finding_key(f):
    return (str(getattr(f, 'message', '') or f), getattr(f, 'line', 0) or 0)


def _inherited_journey_files(page_path):
    """The journey files a page inherits, root first -- the same cascade `_apply_journey` merges."""
    page_path = os.path.abspath(page_path)
    page_dir = os.path.dirname(page_path)
    out = []
    for d in _journey_chain_dirs(page_dir, _app_root_for_file(page_dir)):
        jp = os.path.join(d, 'journey.mho')
        if os.path.isfile(jp) and os.path.realpath(jp) != os.path.realpath(page_path):
            # Shown relative to where the command runs, unless it is on another drive (Windows),
            # where no relative path exists and the absolute one is the honest name.
            if os.path.splitdrive(jp)[0].lower() == os.path.splitdrive(os.getcwd())[0].lower():
                jp = os.path.relpath(jp)
            out.append(jp)
    return out


def _shared_findings(journey_path, args):
    """The findings of a journey checked on its own, once per process. Its output is swallowed: a
    problem that stops the journey stops the page that merges it too, and is reported there."""
    key = os.path.realpath(journey_path)
    if key in _SHARED_FINDINGS:
        return _SHARED_FINDINGS[key]
    import io as _io
    saved = (args.file, getattr(args, '_collect_into', None), getattr(args, 'json', False),
             getattr(args, 'security', False), sys.stdout)
    box = {}
    args.file, args._collect_into, args.json, args.security = journey_path, box, False, False
    sys.stdout = _io.StringIO()
    try:
        cmd_check(args)
    except SystemExit:
        # RECORDED IN silent_shape_baseline.txt AS A DELIBERATE ONE. A check always ends in an exit;
        # the journey's findings are already in `box`, and a journey that stopped before collecting
        # leaves `box` empty, which is recorded below as "no findings to attribute". The same stop
        # reaches the page that merges the journey and is reported there, so nothing is lost here.
        pass
    finally:
        args.file, args._collect_into, args.json, args.security, sys.stdout = saved
    _SHARED_FINDINGS[key] = box.get(journey_path)
    return _SHARED_FINDINGS[key]


def _split_inherited_findings(filename, ctx, args):
    """Split a page's findings into its OWN and those whose code is in a journey it inherits.

    Returns (own_warnings, own_errors, inherited), where inherited is a list of
    (journey_path, 'w' or 'e', finding). A finding belongs to the SHALLOWEST journey whose own check
    produced the same message on the same line: a subfolder journey's check includes its parent's
    findings, so root-first is what puts each one at the file that declares it."""
    ws, es = list(ctx.warnings or []), list(ctx.errors or [])
    if not (ws or es) or not filename:
        return ws, es, []
    owners = {}
    for jp in _inherited_journey_files(filename):
        info = _shared_findings(jp, args)
        if not info:
            continue
        for kind, lst in (('w', info['warnings']), ('e', info['errors'])):
            for f in lst:
                owners.setdefault((kind,) + _finding_key(f), jp)
    own_w, own_e, inherited = [], [], []
    for kind, lst, own in (('w', ws, own_w), ('e', es, own_e)):
        for f in lst:
            jp = owners.get((kind,) + _finding_key(f))
            if jp is None:
                own.append(f)
            else:
                inherited.append((jp, kind, f))
    return own_w, own_e, inherited


def _check_many(files, args, rel):
    """Check many files in one run and report each finding ONCE, at the file its code is in.

    Shared by `mio check <folder>` and `mio check --all`. One note at the start, then one progress
    line per file, so a long cold run shows where it is instead of a rotating note per file. Journeys
    are checked first, so every page that inherits one reuses its result. Findings print after the
    last file: each journey's own findings once, under the journey, then each page's own.

    Returns (collected, failed, n_warn, n_err, n_shared, advisory), `advisory` being
    _advisory_counts over exactly the warnings counted into n_warn."""
    import random
    print(f"  [mio check] {random.choice([m for m in _CHECK_NOTES if '{n}' not in m])}")
    def _depth(f):
        return os.path.abspath(f).replace('\\', '/').count('/')
    journeys = sorted((f for f in files if os.path.basename(f) == 'journey.mho'), key=lambda f: (_depth(f), f))
    ordered = journeys + sorted(f for f in files if os.path.basename(f) != 'journey.mho')
    collected, failed = {}, []
    saved_all = getattr(args, 'all', False)
    args.all = False
    for i, f in enumerate(ordered, 1):
        print(f"  [{i}/{len(ordered)}] {rel(f).replace(chr(92), '/')}", flush=True)
        memo = _SHARED_FINDINGS.get(os.path.realpath(f))
        if memo:
            collected[f] = memo
            continue
        args.file = f
        args._collect_into = collected
        try:
            cmd_check(args)
        except SystemExit as e:
            if e.code not in (0, None):
                failed.append(f)
        if f in collected and os.path.basename(f) == 'journey.mho':
            _SHARED_FINDINGS[os.path.realpath(f)] = collected[f]
    args._collect_into = None
    args.all = saved_all

    n_warn = n_err = n_shared = 0
    counted = []          # the warnings n_warn counts, so the advisory line counts the same ones
    # 1) Shared code: each journey's own findings, once, under the journey, with its own excerpts. A
    #    journey above the checked folder that a page inherits is reported here too.
    shared = list(journeys)
    for info in collected.values():
        for (jp, _k, _f) in info.get('inherited', ()):
            if not any(os.path.realpath(jp) == os.path.realpath(s) for s in shared):
                shared.append(jp)
    for jf in shared:
        info = collected.get(jf) or _SHARED_FINDINGS.get(os.path.realpath(jf))
        if not info or not (info['own_warnings'] or info['own_errors']):
            continue
        print(f"\n  {bold(dim(rel(jf)))}  "
              f"{dim('(shared: the code is here, and every page under it inherits it)')}\n")
        _print_warning_groups([(w, info['source'], jf) for w in info['own_warnings']])
        for e in info['own_errors']:
            _print_compile_error(e, info['source'], jf)
        n_warn += len(info['own_warnings'])
        counted += info['own_warnings']
        n_err += len(info['own_errors'])
        n_shared += len(info['own_warnings']) + len(info['own_errors'])
    # 2) Each page's own findings, with that page's excerpt. A warning repeated across pages is said
    #    ONCE, under the first page it appears on, naming every other page and line it applies to.
    _by_key, _first_at = {}, {}
    for f in ordered:
        info = collected.get(f)
        if not info or os.path.basename(f) == 'journey.mho':
            continue
        for w in info['own_warnings']:
            k = _warning_key(w)
            _by_key.setdefault(k, []).append((w, info['source'], f))
            _first_at.setdefault(k, f)
    for f in ordered:
        info = collected.get(f)
        if not info:
            continue
        if os.path.basename(f) != 'journey.mho':
            n_warn += len(info['own_warnings'])
            counted += info['own_warnings']
            _leads = [k for k in dict.fromkeys(_warning_key(w) for w in info['own_warnings'])
                      if _first_at.get(k) == f]
        else:
            _leads = []
        if os.path.basename(f) != 'journey.mho' and (_leads or info['own_errors']):
            print(f"\n  {bold(dim(rel(f)))}  {yellow('Warnings')}\n"
                  if not info['own_errors'] else f"\n  {bold(dim(rel(f)))}\n")
            for k in _leads:
                _print_warning_groups(_by_key[k])
            for e in info['own_errors']:
                _print_compile_error(e, info['source'], f)
                n_err += 1
        if info['errors']:
            failed.append(f)
    # A FILE THAT FAILED IS AT LEAST ONE ERROR (HC-121). A file that could not be read at all, a
    # syntax error or a load failure, stops before any finding is collected, so it was listed as
    # failed while the summary beside it said "0 errors" and the exit status said 1. It printed its
    # own message already; here it is counted.
    for f in set(failed):
        info = collected.get(f)
        if not info or not (info.get('own_errors') or info.get('errors')):
            n_err += 1
    return collected, sorted(set(failed)), n_warn, n_err, n_shared, _advisory_counts(counted)


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

    # --locations [CODE]: every site of a repeated warning, or of the one class named. Written before the
    # file (`mio check --locations app.mho`), argparse hands the file to CODE; a CODE that is a path on
    # disk with no file given is the file.
    _loc = getattr(args, 'locations', None)
    if _loc and not getattr(args, 'file', None) and os.path.exists(_loc):
        args.file, _loc = _loc, ""
    _CHECK_LOCATIONS['on'] = _loc is not None
    _CHECK_LOCATIONS['code'] = _loc or None
    if not isinstance(getattr(args, '_collect_into', None), dict):   # the outer call names the target
        _CHECK_TARGET[0] = getattr(args, 'file', None) or '.'  # SILENT-SHAPE-OK: the target a drill-down line names; a check of no file checks .

    # --all mode: check every .mho file in directory tree
    if getattr(args, 'all', False):
        import glob
        files = sorted(glob.glob('**/*.mho', recursive=True))
        # Sector PROFILES now carry the `.mho` extension for a community profile (Batch 14), but they
        # are profile DATA parsed by mohio_sector_loader, not app programs -- checking one as an app
        # fails on its `field types` / `meta` blocks. A profile is a `sector-*.mho` file inside a
        # `sectors/` directory; skip those, keep every real app.
        def _is_sector_profile(path):
            u = path.replace('\\', '/')
            return '/sectors/' in ('/' + u) and os.path.basename(u).startswith('sector-')
        files = [f for f in files if not _is_sector_profile(f)]
        if not files:
            print("  No .mho files found.")
            return
        fast = getattr(args, 'fast', False)
        mode = "--fast" if fast else "full"
        print(f"  [mio check] Checking {len(files)} .mho file(s) [{mode}]...")
        if fast:
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
        else:
            # The same aggregation as a folder check (`_check_many` clears `args.all` while it
            # recurses, for the reason above): one progress line per file, and a finding in a
            # shared journey reported once, at the journey, not under every page.
            _c, failed, n_w, n_e, n_sh, _adv = _check_many(files, args, lambda f: f)
            print(f"\n  {bold('Summary')}  {dim(str(len(files)) + ' file(s) checked')} . "
                  f"{yellow(str(n_w) + ' warning(s)')} . "
                  f"{red(str(n_e) + ' error(s)') if n_e else dim('0 errors')}")
            if n_sh:
                print(f"  {dim(f'{n_sh} finding(s) in shared code are shown once, at the file the code is in.')}")
            _print_advisory_count(_adv, n_w)
            _print_suppressed(_distinct_suppressed(_c), '--all', getattr(args, 'show_suppressed', False))
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
        # A FRAGMENT A RENDER INCLUDES IS PAGE TEXT, NOT MOHIO (RULED by Ron, 2026-10-06). A file
        # reached only through `{{ include "_head.mho" }}` inside a render is emitted as markup,
        # so literal text in it may hold any character: a middle dot in a title, Arabic, an emoji.
        # Parsing it as Mohio stopped the whole folder check at the first such character, before
        # the app was analysed at all. Character rules apply to Mohio keywords and identifiers; a
        # file that is also `include`d as a statement is still Mohio and is still parsed.
        _markup = _render_only_fragments(mho + private)
        markup_fragments = [p for p in private if _os.path.abspath(p) in _markup]
        private = [p for p in private if _os.path.abspath(p) not in _markup]
        if not mho and not private and not markup_fragments:
            _die(f"No .mho files found in {filename}", exit_code=3)
        if not mho:
            _die(f"Only include targets (_name.mho) found in {filename}. "
                 f"Nothing here can be served.", exit_code=3)
        print(f"  [mio check] Checking {len(mho)} .mho file(s) in {bold(str(path))}...")
        def _rel(fp):
            try:
                return _os.path.relpath(fp, str(path))
            except Exception:
                return fp
        # AGGREGATE (`_check_many`): a finding whose code is in a journey is reported ONCE, at the
        # journey, with its real excerpt; each page shows only its OWN findings, with its own excerpt.
        _c, failed, n_distinct_warn, n_distinct_err, _decl, _adv = _check_many(sorted(mho), args, _rel)

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
        # A summary, because 59 files with a handful of distinct findings should say so once, not
        # leave the reader to count. `n_distinct_*` counts each finding once (a journey-declared one
        # counted at the journey, not per page).
        n_distinct_err += len(private_failed)   # an include target that will not parse is an error too
        print(f"\n  {bold('Summary')}  {dim(str(len(mho)) + ' file(s) checked')} . "
              f"{yellow(str(n_distinct_warn) + ' warning(s)')} . "
              f"{red(str(n_distinct_err) + ' error(s)') if n_distinct_err else dim('0 errors')}")
        if _decl:
            print(f"  {dim(f'{_decl} finding(s) declared in a journey and inherited by every page '
                          f'are shown once, at the journey.')}")
        _print_advisory_count(_adv, n_distinct_warn)
        _print_suppressed(_distinct_suppressed(_c), _CHECK_TARGET[0], getattr(args, 'show_suppressed', False))
        if failed or private_failed:
            _n = len(failed) + len(private_failed)
            print(f"  {red('x')} {_n} file(s) failed:")
            for f in failed:
                print(f"    {_rel(f)}")
            for f in private_failed:
                print(f"    {_rel(f)}  (include target -- syntax)")
            sys.exit(1)
        print(f"  {green('v')} All {len(mho)} files passed"
              + (f" ({n_distinct_warn} warning(s) to review)" if n_distinct_warn else "") + ".")
        if private:
            print(f"  {dim(f'  plus {len(private)} include target(s) parsed. They are '
                          f'checked in full through the files that include them.')}")
        if markup_fragments:
            print(f"  {dim(f'  plus {len(markup_fragments)} fragment(s) a render includes, read as '
                          f'page text rather than Mohio.')}")
        return

    source = _read_source(path)
    n_lines = len(source.splitlines())

    # Fun message for large files -- mio check is thorough, not fast
    import random
    msg = random.choice(_CHECK_NOTES)
    _in_collect = isinstance(getattr(args, "_collect_into", None), dict)
    if '{n}' in msg:
        n_lines = len(source.splitlines())
        msg = msg.replace('{n}', str(n_lines))
    if not _in_collect:
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

    # `_parse_and_validate` reuses the cached PARSE (the tree, keyed on the parse-affecting files)
    # and re-runs the verdict every time, so a scanner change is picked up on a warm check while the
    # 87s parse is still skipped. The whole point of splitting the cache: the parse survives, the
    # verdict never does.
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
        _err = MohioCompileError(_msg, _ln, getattr(_e, 'code', '') or '')
        _err.hint = ""
        ctx.errors.append(_err)
        _program = None
    except FileNotFoundError as _e:
        # A missing include/journey file is a mistake in the PROGRAM, not a compiler bug --
        # it used to fall into the generic backstop below, which frames every failure as
        # "report this bug", with no line number (_resolve_includes raises a plain
        # FileNotFoundError, not a MohioError). Reported here as a real check error instead,
        # at the include statement's own line (threaded through via `.mohio_line`, set where
        # _resolve_includes raises, in mio.py).
        if getattr(ctx, 'errors', None) is None:
            ctx.errors = []
        _err = MohioCompileError(str(_e), getattr(_e, 'mohio_line', 0) or 0, '')
        _err.hint = "Check the path and spelling; it is resolved relative to the including file, then the current directory."
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
        _scan_incomplete_warn(ctx, "Layer 2 AST construction", _e)
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
            _scan_incomplete_warn(ctx, "Layer 3 whole-program scanners", _scan_err)

        # PCI/PII compile-time guard: a save must never persist a `never store`
        # field (e.g. a card CVV). Surface as errors before the program runs.
        try:
            from mohio_transformer_ast import MohioCompileError as _NsErr
            for _msg, _ln, _hint in _check_never_store(_program):
                _e = _NsErr(_msg, _ln)
                _e.hint = _hint
                ctx.errors.append(_e)
        except Exception as _scan_err:
            _scan_incomplete_warn(ctx, "never-store (PCI/PII) guard", _scan_err)

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
            _scan_incomplete_warn(ctx, "upload-size lint", _scan_err)

        # MioScript compile-time validation (T1-MIOSCRIPT-CHECK-RUN-PARITY): `compile_listeners`
        # already refuses an unrecognized client value (a typo'd `put <name> into ...`), a
        # `stop <name>` naming no declared `every`, and two `every` blocks sharing one name --
        # but until now it only ran the first time a program was actually run or served, never
        # at `mio check`. A program with one of these mistakes checked clean and only failed on
        # its first real request, which is exactly the "check and run must agree" gap this
        # project names and refuses to ship twice. Any FUTURE refusal added to
        # `compile_listeners` (e.g. an unknown `share ... on <destination>`) gets this same
        # check-time coverage for free, since this call site runs every listener through it.
        # The walk lives in mohio_mioscript beside the compiler it feeds, so `mio check` reads it
        # without loading the interpreter.
        try:
            from mohio_mioscript import compile_listeners as _compile_listeners
            from mohio_mioscript import collect_client_listeners as _collect_client_listeners
            from mohio_transformer_ast import MohioCompileError as _MsErr
            _listeners = _collect_client_listeners(_program)
            if _listeners:
                _compile_listeners(_listeners)
        except ValueError as _ms_err:
            _e = _MsErr(str(_ms_err), 0)
            _e.hint = ""
            ctx.errors.append(_e)
        except Exception as _scan_err:
            _scan_incomplete_warn(ctx, "MioScript compile-time validation", _scan_err)

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
        _scan_incomplete_warn(ctx, "handler-closer lint", _scan_err)

    # Security report
    if security_mode:
        if json_mode:
            import io as _io
            _saved = sys.stdout
            sys.stdout = _io.StringIO()
            try:
                _run_security_report(source, filename, ctx, program=_program)
            finally:
                sys.stdout = _saved
        else:
            _run_security_report(source, filename, ctx, program=_program)

    # A FINDING NAMES THE FILE ITS CODE IS IN. A page is checked with its journeys merged in, so a
    # finding on a journey's line arrives here carrying the journey's line number and was printed
    # against THIS file, with this file's unrelated excerpt (the banking app showed "Line 344" under
    # all 95 pages, and 344 is a line of the root journey). Each finding the page inherited is
    # matched, by message and line, against the journey's own check (run once per process) and
    # reported at the journey instead.
    # SUPPRESSED ADVISORIES (RULED by Ron 2026-10-25): a journey's `suppress <code> reason "..."` takes
    # that advisory out of the report for every page the journey reaches; the count, and with
    # --show-suppressed each one and its reason, is still said, so the silence is never opaque.
    _apply_suppressions(ctx, _program)
    own_w, own_e, inherited = _split_inherited_findings(filename, ctx, args)

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
            code = getattr(e, 'code', '') or (cm.group(1) if cm else "ERROR")
            from mohio_transformer import HINT_TABLE
            return {
                "code":    code,
                "line":    int(lm.group(1)) if lm else 0,
                "message": msg,
                "hint":    HINT_TABLE.get(code, ""),
            }

        def _registered(d):
            # A registered code carries its class and its page, from the diagnostic-code registry.
            from mohio_transformer import diagnostic_entry, diagnostic_link
            entry = diagnostic_entry(d.get("code"))
            if entry is None:
                return d
            return dict(d, **{"class": entry["class"], "link": diagnostic_link(d["code"])})
        # Each finding names the file its code is in: this file, or the journey it came from.
        _from = {id(f): jp for (jp, _kind, f) in inherited}
        for e in (ctx.errors or []):
            output["errors"].append(dict(_registered(_err_to_dict(e)), file=_finding_file(e) or _from.get(id(e), filename)))
        for w in (ctx.warnings or []):
            output["warnings"].append(dict(_registered(_err_to_dict(w)), file=_finding_file(w) or _from.get(id(w), filename)))
        output["suppressed"] = [dict(_registered(_err_to_dict(w)), file=_finding_file(w) or filename,
                                     reason=reason, suppressed_at=int(line or 0))
                                for w, reason, line in getattr(ctx, 'suppressed', [])]
        print(_json.dumps(output, indent=2))
        sys.exit(0 if output["passed"] else 1)

    # FOLDER AND --all COLLECT: hand this file's findings back to the aggregator instead of printing
    # them per file. The aggregator reports a finding in shared code ONCE, at the file it is in,
    # rather than once per page that inherits it -- otherwise 95 pages re-print every inherited
    # finding with an unrelated excerpt, and a real per-page finding drowns in the repetition.
    _collect = getattr(args, '_collect_into', None)
    if isinstance(_collect, dict):
        _collect[filename] = {
            'source': source, 'n_lines': n_lines,
            'warnings': list(ctx.warnings or []),
            'errors': list(ctx.errors or []),
            'own_warnings': own_w, 'own_errors': own_e, 'inherited': inherited,
            'suppressed': list(getattr(ctx, 'suppressed', [])),
        }
        return

    # Findings in a journey this page inherits: printed under that journey, with its own excerpt.
    for jp in dict.fromkeys(jp for (jp, _k, _f) in inherited):
        j_src = _SHARED_FINDINGS[os.path.realpath(jp)]['source']
        print(f"\n  {bold(dim(jp))}  {dim('(a journey this page inherits; the code is here)')}\n")
        _print_warning_groups([(f, j_src, jp) for (jp2, kind, f) in inherited if jp2 == jp and kind == 'w'])
        for (jp2, kind, f) in inherited:
            if jp2 == jp and kind != 'w':
                _print_compile_error(f, j_src, jp)

    # Print warnings, a repeated one once with where else it applies
    if own_w:
        _print_warning_groups([(w, source, filename) for w in own_w],
                              heading=f"\n  {bold(dim(filename))}  {yellow('Warnings')}\n")

    # Print errors
    if own_e:
        print(f"\n  {bold(filename)}  {red('Errors')}\n")
        for e in own_e:
            _print_compile_error(e, source, filename)

    # Summary line
    n_err  = len(ctx.errors)
    n_warn = len(ctx.warnings)

    _quiet = getattr(ctx, 'suppressed', [])
    if n_err:
        warn_part = f" . {yellow(f'{n_warn} warning(s)')}" if n_warn else ""
        print(f"  {red('x')}  {bold(filename)}  "
              f"{dim(f'{n_lines} lines')}"
              f"{warn_part} . {red(f'{n_err} error(s)')}\n")
        _print_advisory_count(_advisory_counts(ctx.warnings), n_warn)
        _print_suppressed(_quiet, filename, getattr(args, 'show_suppressed', False))
        sys.exit(1)
    else:
        warn_part = f" . {yellow(f'{n_warn} warning(s)')}" if n_warn else ""
        print(f"\n  {green('v')}  {bold(filename)}  "
              f"{dim(f'{n_lines} lines . no errors')}"
              f"{warn_part}\n")
        _print_advisory_count(_advisory_counts(ctx.warnings), n_warn)
        _print_suppressed(_quiet, filename, getattr(args, 'show_suppressed', False))
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
    # EGRESS-EXEMPT: git hook and config files the developer asked for; no program value
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
        # EGRESS-EXEMPT: git hook and config files the developer asked for; no program value
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
    # EGRESS-EXEMPT: git hook and config files the developer asked for; no program value
    config_path.write_text(_json.dumps(mio_config, indent=2), encoding="utf-8")
    print(f"  {green('v')}  Config written: {bold('.mioconfig')}")
    print(f"  {dim('Edit to customize check behavior')}\n")


def _schema_pages(path):
    """The files `mio schema` reads: the one file named, or every page of an app folder, found the
    way `mio check` finds them (a `_name.mho` include target is not a page; it reaches the schema
    through the pages that include it, as the journey does)."""
    import os as _os
    if not path.is_dir():
        return [path]
    pages = []
    for _root, _dnames, _fnames in _os.walk(str(path)):
        _dnames[:] = sorted(d for d in _dnames if not d.startswith('.'))
        for f in sorted(_fnames):
            if f.endswith('.mho') and not f.startswith('_'):
                pages.append(Path(_root) / f)
    if not pages:
        _die(f"No .mho pages found in {path}. A folder for mio schema is an app folder: "
             f"its pages, with their includes and journey.mho.", exit_code=3)
    return pages


def _schema_program(page):
    """(source, program) for one page, built EXACTLY as `mio check` builds it: the cached parse,
    the same gates, then the page's includes and its journey merged in. One path, so the schema
    reads the program check and run read, and a warm parse cache makes it seconds, not minutes."""
    from mohio_enforce import enforce as _enforce
    source = _read_source(page)
    tree, ctx = _parse_and_validate(source, str(page), verbose=False)
    _l12_ctx, program = _enforce(tree, source=source, filename=str(page), scan=False)
    if program is None:
        _errs = list(ctx.errors or []) + list(_l12_ctx.errors or [])
        _first = str(_errs[0]) if _errs else "its declarations could not be read"
        _die(f"Could not read the shapes in {page}: {_first}. Run: mio check {page}",
             exit_code=1)
    program = _resolve_includes(program, str(page), verbose=False)
    program = _apply_journey(program, str(page), verbose=False)
    return source, program


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

    # AN APP FOLDER IS THE WHOLE APP (RULED by Ron, 2026-10-05, Part B 18b), the same way `mio
    # check` and `mio serve` take one: every page, each with its includes and its journey merged in,
    # so the schema describes and checks the app rather than one file of it. Its one manifest is
    # `<folder>/.mhoschema`, the name `find_schema_file` already looks for beside every page, so a
    # later `mio check --security` on any page of the app finds it. A single file is unchanged:
    # `<file>.mhoschema` beside it.
    is_app = path.is_dir()
    pages = _schema_pages(path)

    from mohio_schema import (generate_schema, write_schema, read_schema, merge_schemas,
                               find_schema_file, validate_field_references)

    if action == 'generate':
        print(f"\n  {bold('mio schema generate')}  {dim(filename)}\n")
        per_page = []
        for page in pages:
            source, program = _schema_program(page)
            schema = generate_schema(source, str(page), program=program)
            # T1-SILENT-SWEEP-BATCH7 (2026-08-15): generate_schema() carries a real extraction
            # failure (an unrecognized field type, say) in the returned dict's 'error' key. It is
            # surfaced here instead of writing a broken manifest and claiming success.
            if schema.get('error'):
                _die(f"Could not generate a schema for {page}: {schema['error']}", exit_code=1)
            per_page.append((str(page), schema))
        if is_app:
            try:
                schema = merge_schemas(per_page, filename)
            except ValueError as e:
                _die(f"Could not generate a schema for {filename}: {e}", exit_code=1)
            schema_path = path / '.mhoschema'
        else:
            schema = per_page[0][1]
            schema_path = path.with_suffix('.mhoschema')
        n_tables = len(schema.get('tables', {}))
        n_fields = sum(len(t['fields']) for t in schema.get('tables', {}).values())

        write_schema(schema, schema_path)

        print(f"  {green('v')}  Schema generated: {bold(str(schema_path))}")
        if is_app:
            print(f"  {dim(f'{len(pages)} page(s) read, each with its includes and journey')}")
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
        if is_app:
            schema_path = path / '.mhoschema'
            schema_path = schema_path if schema_path.exists() else None
        else:
            schema_path = find_schema_file(filename)
        if not schema_path:
            print(f"  {yellow('!')}  No .mhoschema found. Run: mio schema generate {filename}")
            sys.exit(1)

        schema = read_schema(schema_path)
        unchecked = {}
        errors = []
        for page in pages:
            source, program = _schema_program(page)
            for code, msg, lineno in validate_field_references(
                    source, schema, str(page), unchecked=unchecked, program=program):
                errors.append((page, code, msg, lineno))

        # RULED by Ron, 2026-10-03 (Order item 8): names what it checked and what it does not
        # cover, every run, pass or fail -- a check that stays silent about its own
        # scope reads as covering everything, which is exactly how a narrow tool earns
        # unwarranted trust. SCHEMA_CHECK_COVERAGE is the one place this list lives.
        from mohio_schema import SCHEMA_CHECK_COVERAGE

        if errors:
            for page, code, msg, lineno in errors:
                _where = f"{page.name} line {lineno}" if is_app else f"line {lineno}"
                print(f"  {red('x')}  {_where}: {red(code)}")
                print(f"     {msg}\n")
            print(f"  {red('x')}  {len(errors)} schema error(s) found\n")
        else:
            print(f"  {green('v')}  All field references valid")
            if is_app:
                print(f"  {dim(f'{len(pages)} page(s) checked, each with its includes and journey')}")
            print(f"  {dim(f'Schema: {schema_path}')}\n")

        # NAMED, every run: what could not be checked, and why. A shape with no declared table
        # is skipped (a table name is never guessed from a shape name); a table the program uses
        # with no backing shape has no schema to check against.
        _no_table = (schema or {}).get('loose') or []
        if _no_table:
            print(f"  {dim('Shapes with no declared table (skipped; add `<name> as table` to check one):')}")
            for _sn in _no_table:
                print(f"    {dim('-')} {dim(_sn)}")
        if unchecked:
            print(f"  {dim('Tables used with no backing shape (not checked, not an error):')}")
            for _tn, _ln in sorted(unchecked.items(), key=lambda kv: kv[1]):
                print(f"    {dim('-')} {dim(f'db.{_tn} (first used at line {_ln})')}")
        print(f"  {dim('Checked:')}")
        for item in SCHEMA_CHECK_COVERAGE['checked']:
            print(f"    {dim('-')} {dim(item)}")
        print(f"  {dim('Not covered (not an error if your program uses one of these):')}")
        for item in SCHEMA_CHECK_COVERAGE['not_covered']:
            print(f"    {dim('-')} {dim(item)}")
        print()

        if errors:
            sys.exit(1)

    elif action == 'show':
        if is_app:
            schema_path = (path / '.mhoschema') if (path / '.mhoschema').exists() else None
        else:
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
    # EGRESS-EXEMPT: a generated example file; no runtime value
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

    # Resolve includes and apply the journey cascade FIRST, with the SAME two resolvers `mio run`
    # uses (single-file run semantics: the file's own-directory-and-up journey, no wider cascade).
    # Without this the command reported only the writes written literally in THIS file, reading as a
    # whole-program answer while an included file's writes and a journey task's writes were invisible
    # -- and a journey can hold write-capable tasks. A missing include fails loud here, the same as
    # it would at run, rather than silently planning an incomplete program.
    try:
        program = _resolve_includes(program, filename, verbose=False)
        program = _apply_journey(program, filename, verbose=False)
    except FileNotFoundError as e:
        _die(str(e), exit_code=1)

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
        # A conditional write may never run. Say so, because a flat list reads a `check`-guarded
        # write as identical to one that always runs -- exactly the distinction a compliance
        # reviewer would most want to trust.
        if w.conditional:
            print(f"            {red('CONDITIONAL')}  "
                  f"{dim('runs only when its check / unless / on.failure branch is taken')}")
        _c = w.compliance
        _mark = red("REGULATED") if _c.regulated == "yes" else dim("regulated: unknown")
        print(f"            {_mark}   {dim(_c.why[:78])}")
        print()
    print(f"  {dim('Every dependency question reads unknown: no def-use analysis exists yet,')}")
    print(f"  {dim('and unknown forbids, which is the conservative rule the roadmap specifies.')}")
    print()


def _sector_licence_position(name, paid, entitled):
    """The licence line for one sector: free sectors need no entitlement; a certified sector reads
    against MOHIO_ENFORCE_LICENSE and MOHIO_OWNER / MOHIO_LICENSE."""
    if not paid:
        return "free sector, no entitlement required"
    if not os.environ.get("MOHIO_ENFORCE_LICENSE"):
        return ("certified sector; licence enforcement is off "
                "(MOHIO_ENFORCE_LICENSE not set), so it is allowed here")
    if entitled:
        return "certified sector; entitled via MOHIO_OWNER or MOHIO_LICENSE"
    return ("certified sector; licence enforcement is ON and no entitlement was found "
            "-- set MOHIO_OWNER or MOHIO_LICENSE")


def _sector_facts(name):
    """The full, read-only position of ONE named sector, as a plain dict. Text and JSON both render
    from this same dict, so the two forms cannot report different facts (Ron's condition). Every
    branch fills the same keys: case, resolved_path, tier, version, signature, licence, status,
    explanation."""
    import mohio_sector_loader as _SL
    from mohio_sector_sign import SectorVerificationError as _SVE
    path = _SL.find_sector_profile(name)
    paid = _SL.sector_requires_license(name)
    entitled = _SL.sector_entitled(name)
    f = {"name": name, "resolved_path": path, "paid_base": paid,
         "tier": None, "version": None, "signature": "n/a",
         "licence": _sector_licence_position(name, paid, entitled),
         "status": "not_in_force", "explanation": ""}
    if path is None:
        if paid:
            # A known certified sector whose signed profile is simply not on this machine. This is NOT
            # the absent-sector mistake: the name is real, the profile ships separately.
            f["case"] = "known_certified_no_local_profile"
            f["explanation"] = (
                f"`{name}` is a known certified sector, but no local profile is present on this "
                f"search path. It resolves only once its signed .sector is placed on the search path "
                f"or in MOHIO_CERTIFIED_SECTORS_DIR.")
        else:
            # The item-2 refusal message, reused verbatim so `mio sector` and `mio check` cannot diverge.
            f["case"] = "absent"
            f["explanation"] = _SL.sector_not_found_message(name)
        return f
    ext = os.path.splitext(path)[1]
    if ext == ".sector":
        try:
            prof = _SL.load_sector_profile(name)
            f["case"] = "certified_verified"
            f["tier"] = "certified"
            f["version"] = (prof.version or None) if prof else None
            f["signature"] = "verified against a trusted key"
            f["status"] = "would_apply"
        except _SVE as e:
            # A .sector always wins over a same-named .mho, so a failing signature is a hard stop, not
            # a downgrade. Report it refused, with the fail-closed reason.
            f["case"] = "certified_refused"
            f["tier"] = "certified"
            f["signature"] = "REFUSED"
            f["explanation"] = str(e)
    else:
        f["tier"] = "community"
        f["signature"] = "n/a (community profiles are unsigned)"
        try:
            prof = _SL.load_sector_profile(name)
        except _SL.SectorUnenforceableRuleError as e:
            # A community profile that declares a rule nothing enforces refuses to load (it would claim
            # protection nobody checks). The inventory reports it refused, with the loader's reason,
            # rather than stopping on it: one bad profile on the search path stopped the whole listing.
            f["case"] = "community_refused"
            f["explanation"] = str(e)
            return f
        f["case"] = "community"
        f["version"] = (prof.version or None) if prof else None
        f["status"] = "would_apply"
    return f


def _journey_spines_in_tree(filepath):
    """Any journey.mho sitting in this file's folder or an ancestor (up to a few levels). Cheap
    existence check only -- `mio sector` does NOT resolve the cascade, so a spine here is reported
    as a heads-up, never applied. (`mio writes` DOES resolve includes and apply the journey, via
    _resolve_includes / _apply_journey; this heads-up is specific to the sector reader.)"""
    found = []
    d = os.path.dirname(os.path.abspath(filepath))
    self_abs = os.path.abspath(filepath)
    for _ in range(6):
        jp = os.path.join(d, "journey.mho")
        if os.path.isfile(jp) and os.path.abspath(jp) != self_abs:
            found.append(os.path.relpath(jp))
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    return found


def _sector_inventory():
    """The deployment-wide, read-only picture: profiles present, known certified sectors with no
    local profile, the search path, and the deployment trust settings. A plain dict, so text and
    JSON render from the same facts."""
    import mohio_sector_loader as _SL
    from mohio_sector_sign import trusted_public_keys as _tpk
    names = _SL.available_sector_names()
    profiles = [_sector_facts(n) for n in names]
    present_bases = {n.split(".")[0].lower() for n in names}
    known_no_profile = sorted(
        b for b in _SL.PAID_SECTORS
        if b not in present_bases and _SL.find_sector_profile(b) is None)
    trusted = len(_tpk())
    return {
        "profiles": profiles,
        "known_certified_no_local_profile": known_no_profile,
        "search_paths": _SL._default_search_paths(),
        "certified_sectors_dir": os.environ.get("MOHIO_CERTIFIED_SECTORS_DIR", "") or None,
        "trusted_signing_keys": trusted,
        "require_certified": os.environ.get(_SL.REQUIRE_CERTIFIED_ENV, "") or None,
        "enforce_licence": bool(os.environ.get("MOHIO_ENFORCE_LICENSE")),
        # A deployment that means to run certified profiles and trusts no keys refuses every one of
        # them. That is correct fail-closed behaviour and almost always a misconfiguration, so it is
        # a warning, not a quiet line.
        "trust_warning": (trusted == 0),
    }


_SECTOR_CASE_LABEL = {
    "community": ("COMMUNITY", green),
    "community_refused": ("COMMUNITY but REFUSED", red),
    "certified_verified": ("CERTIFIED", green),
    "certified_refused": ("CERTIFIED but REFUSED", red),
    "known_certified_no_local_profile": ("KNOWN CERTIFIED, no local profile", yellow),
    "absent": ("NOT FOUND", red),
}


def cmd_sector(args):
    """mio sector [FILE.mho] [--json] -- report what sector rules are in force, read-only.

    Runs nothing and changes nothing. With no file it reports the deployment: which profiles are on
    the search path, which known certified sectors have no local profile here, and what this
    deployment trusts (signing keys, required-certified, licence enforcement). With a file it reports
    each sector the program NAMES and where each one stands: community or certified, its version, its
    signature result, its licence position, and -- for a name that does not resolve -- the same
    explanation `mio check` gives.

    It reports what WOULD APPLY from the file's own `sector:` line. It does not resolve a journey
    spine in a parent folder, which can tighten the sector under the cascade; where one exists it says
    so rather than pretending to have applied it.
    """
    import json as _json
    want_json = getattr(args, "json", False)
    filename = getattr(args, "file", None)
    if filename and os.path.isfile(str(filename)):
        # The program's own `sectors/` folder is on the search path for its report too.
        import mohio_sector_loader as _SLf
        _SLf.set_program_folder(os.path.dirname(os.path.abspath(str(filename))))

    if not filename:
        inv = _sector_inventory()
        if want_json:
            print(_json.dumps(inv, indent=2))
            return
        print()
        print(f"  {bold('Sector profiles on the search path')}")
        if inv["profiles"]:
            for p in inv["profiles"]:
                label, colour = _SECTOR_CASE_LABEL.get(p["case"], (p["case"], dim))
                ver = p["version"] if p["version"] else "-"
                loc = os.path.relpath(p["resolved_path"]) if p["resolved_path"] else "-"
                # A profile that refuses to load says REFUSED here; its tier alone read as one in force.
                word = "refused" if str(p["case"]).endswith("_refused") else label.split()[0].lower()
                print(f"    {p['name']:<18} {colour(word):<20} "
                      f"v{ver:<6} {dim(loc)}")
                if word == "refused" and p.get("explanation"):
                    print(f"      {dim(str(p['explanation']).splitlines()[0])}")
            print(f"    {dim('(' + str(len(inv['profiles'])) + ' found)')}")
        else:
            print(f"    {dim('none found')}")
        print()
        if inv["known_certified_no_local_profile"]:
            print(f"  {bold('Known certified sectors with no local profile here')}")
            print(f"    {', '.join(inv['known_certified_no_local_profile'])}")
            print(f"    {dim('name one and it resolves only if its signed .sector is on the search path')}")
            print()
        print(f"  {bold('Search path')} {dim('(first match wins)')}")
        print(f"    {dim('  •  '.join(inv['search_paths']))}")
        print(f"    MOHIO_CERTIFIED_SECTORS_DIR: {inv['certified_sectors_dir'] or dim('(not set)')}")
        print()
        print(f"  {bold('Deployment trust settings')}")
        keyline = f"Trusted signing keys (MOHIO_SECTOR_TRUSTED_KEYS): {inv['trusted_signing_keys']} loaded"
        if inv["trust_warning"]:
            print(f"    {yellow(bold('WARNING'))}  {yellow(keyline + ' -- every .sector fails closed')}")
            print(f"             {dim('add a public key path to trust a certified profile, or this deployment can run none')}")
        else:
            print(f"    {keyline}")
        print(f"    Require certified (MOHIO_REQUIRE_CERTIFIED_SECTORS): "
              f"{inv['require_certified'] or dim('(not set)')}")
        print(f"    Enforce licence (MOHIO_ENFORCE_LICENSE): "
              f"{'on' if inv['enforce_licence'] else dim('off')}")
        print()
        return

    # File form -- report each sector the program names.
    path = Path(filename)
    if not path.exists():
        _die(f"File not found: {filename}", exit_code=3)
    source = _read_source(path)
    # Parse and AST-transform WITHOUT the validate layer, so a file that names an absent sector is
    # EXPLAINED here rather than dying on the item-2 refusal -- explaining that is this command's job.
    try:
        from mohio_transformer_ast import transform as _ast_transform
        from mohio_ast import SectorDecl as _SectorDecl
        parser = _make_parser_cached(_load_grammar())
        program = _ast_transform(parser.parse(source), source)
    except Exception as e:
        from lark.exceptions import UnexpectedInput
        if isinstance(e, UnexpectedInput):
            _print_parse_error(e, source, filename)
            sys.exit(1)
        _die(f"Parse failed: {e}", exit_code=1)

    names = [n.sector for n in (getattr(program, "statements", None) or [])
             if isinstance(n, _SectorDecl)]
    facts = [_sector_facts(n) for n in names]
    spines = _journey_spines_in_tree(filename)

    if want_json:
        print(_json.dumps({"file": str(filename), "sectors": facts,
                           "journey_spines_not_resolved": spines}, indent=2))
        return

    print()
    if not names:
        print(f"  {dim(str(filename) + ' names no sector. No compliance profile would apply.')}\n")
        return
    print(f"  {bold(str(filename))} names: {', '.join(names)}")
    print()
    for f in facts:
        label, colour = _SECTOR_CASE_LABEL.get(f["case"], (f["case"], dim))
        print(f"  {bold(f['name'])}")
        print(f"    Resolved:   {os.path.relpath(f['resolved_path']) if f['resolved_path'] else dim('no profile file found')}")
        print(f"    Tier:       {colour(label)}")
        print(f"    Version:    {f['version'] or dim('-')}")
        print(f"    Signature:  {f['signature']}")
        print(f"    Licence:    {f['licence']}")
        status = {"would_apply": green("WOULD APPLY"),
                  "not_in_force": red("NOT IN FORCE")}.get(f["status"], f["status"])
        print(f"    Status:     {status}")
        if f["explanation"]:
            print(f"    {dim(f['explanation'])}")
        print()
    if spines:
        print(f"  {yellow('note')}  a journey spine ({', '.join(spines)}) sits in this folder tree.")
        print(f"        {dim('It may tighten the sector under the cascade; this command does not resolve that.')}")
        print()


AUDIT_ENVELOPE_LABEL = "mohio_audit_envelope + ack"


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
    shown_target = _display_target(target)   # every message names the store without its password
    driver = _sniff_driver(target) if '://' in str(target) else (
        'sqlite' if explicit_file else _sniff_driver(target))
    try:
        sink = _make_db_runtime(driver, target,
                                url=target if '://' in str(target) else None)
    except Exception as e:
        _die(f"Could not open the audit store at {shown_target}: {e}", exit_code=3)

    it = MohioInterpreter()

    if action == "relay" and getattr(args, "program", None):
        # THE RELAY DELIVERS ONLY TO LOGS THE PROGRAM DECLARES (the relay-identifier blocker): given
        # the program, its own `ai.audit to NAME` logs and `<name>_limits_log` join the runtime's
        # fixed names. It is read, never run.
        _pp = args.program
        if not os.path.isfile(_pp):
            _die(f"--program {_pp}: no such file. Give the .mho program whose audit logs the relay "
                 f"may deliver to.", exit_code=2)
        from mohio_transformer_ast import transform as _t
        _src = open(_pp, encoding="utf-8-sig").read()
        _tree, _ctx = _parse_and_validate(_src, _pp, False)
        _prog = _t(_tree, _src)
        _apply_journey(_prog, _pp)
        it._index_log_names(_prog)
        it.app_id = _app_identity(_pp)
    elif action == "relay":
        it.app_id = _app_identity(None)        # MOHIO_APP_ID, or None: only unattributed envelopes

    if action == "relay":
        # BEFORE THE LOG CHECK, DELIBERATELY. A process killed between committing a regulated
        # write and delivering its record may have left no audit log at all -- if it was the
        # first write, the log was never created. Refusing to relay because there is no log is
        # refusing exactly when the relay is most needed.
        # The relay is a drain, so it takes the same claim the automatic first-use drain does, to
        # the same end: two drains must not deliver one owed record twice. Oracle is the only engine
        # that claims per drain (and so must release here); the session-lock engines hold their claim
        # for the connection's life, so claiming in this short-lived CLI process would make the relay
        # refuse whenever an app is running -- hence the Oracle-only guard. If another process holds
        # it, the owed records are being delivered right now, so there is nothing for this run to do.
        _oracle = type(sink).__name__ == 'OracleRuntime'
        if _oracle and not it._claim_relay(sink):
            print(f"\n  {dim('Another process is draining the trail right now; nothing to do.')}\n")
            return
        try:
            delivered, already = it.audit_relay_drain(sink)
        except Exception as e:
            _die(f"Could not deliver the owed audit records: {e}", exit_code=3)
        finally:
            if _oracle:
                it._release_relay(sink)
        _other = getattr(it, '_relay_other_app', 0)
        _owed = getattr(it, '_relay_still_owed', 0)
        if delivered or already:
            print(f"\n  delivered {delivered} record(s) that were owed; "
                  f"{already} were already in the trail.\n")
        elif not _other and not _owed:
            # ONLY WHEN NOTHING IS LEFT OWED (relay honesty, RULED 2026-10-17): this line used to print
            # after a delivery that failed, while the envelope stayed owed.
            print(f"\n  {dim('Nothing owed: every regulated write in ' + str(shown_target) + ' has its record.')}\n")
        if _other and getattr(it, 'app_id', None) is None:
            # NO IDENTITY, SO NOTHING HERE IS THIS RELAY'S TO DELIVER, and exiting 0 would tell a
            # scheduled relay job the trail is caught up while every app's records stay owed. The
            # relay delivers only its own app's records (the relay-identifier blocker), so it has
            # to be told which app it is.
            _die(f"{_other} owed audit record(s) in {shown_target} belong to an app, and this relay was "
                 f"not told which app it is, so it delivered none of them.\n"
                 f"  Run it as the app that wrote them: `mio audit relay {shown_target} --program app.mho` "
                 f"(the app's own program, or any page in it), or set MOHIO_APP_ID to that app's id.",
                 exit_code=1)
        if _other:
            # Said, not drained: another app's records are its own relay's to deliver.
            print(f"  {_other} owed record(s) belong to another app and were left for it. Run the "
                  f"relay with that app's program (`--program app.mho`) or its MOHIO_APP_ID.\n")
        if _owed:
            # LAST, after the other-app note above, so both are said. STILL OWED IS A FAILURE, said and
            # exited as one, so a scheduled relay never reads it as a caught-up trail. The records stay
            # owed and unacknowledged; the next relay retries them.
            _why = '; '.join(dict.fromkeys(getattr(it, '_relay_owed_reasons', []) or ['unknown']))
            _die(f"{_owed} owed, delivery failed: {_why}\n"
                 f"  {_owed} audit record(s) in {shown_target} are still owed: their evidence is kept and "
                 f"nothing is lost, but the trail is behind until they land.\n"
                 f"  Fix the cause above (the audit store is reachable and this role may write to it), "
                 f"then run `mio audit relay {shown_target}` again.",
                 exit_code=1)
        return

    logs = it.audit_logs(sink)
    # verify-anchors goes on with no logs at all: a published anchor for a log that is gone is the
    # wholesale deletion anchoring exists to catch, never "nothing to do".
    # THE ENVELOPE AND ACK RELATIONS VOUCH FOR THE TRAIL (audit append-only, step 4): an
    # acknowledgement names the record that delivered its envelope, so a record removed from the end
    # of a log, or a whole log removed, is visible here even when every remaining chain is intact.
    _env = it.verify_audit_envelopes(sink) if action == "verify" else None
    if not logs and action != "verify-anchors":
        if _env and _env['problems']:
            # No log is left, and acknowledgements say records were delivered into one: the trail
            # was removed. This used to print "No audit logs found" and exit 0.
            print(f"\n  {red('x')}  No audit logs found in {shown_target}, and "
                  f"{AUDIT_ENVELOPE_LABEL} says records were delivered:")
            for _p in _env['problems']:
                print(f"     {_p}")
            print()
            sys.exit(1)
        print(f"\n  {dim('No audit logs found in ' + str(shown_target) + '.')}\n")
        return

    if action == "read":
        # THE AUTHORIZED READ (OR-3, RULED by Ron 2026-10-12). Who acted is sealed in the trail under
        # a key per subject; this opens it with the database's encryption key, and once a person has
        # been erased their records read "erased". Records written before identities were sealed are
        # counted and shown as they are: historical records are never rewritten.
        import json as _json
        it._db = sink
        plain = 0
        for log in logs:
            rows = sink.find_many(log) or []
            print(f"\n  {bold(log)}  {dim(str(len(rows)) + ' record(s)')}")
            for r in rows:
                r = {str(k).lower(): v for k, v in dict(r).items()}
                raw_detail = r.get('detail')
                d = {}
                if raw_detail:
                    try:
                        d = _json.loads(raw_detail)
                    except (TypeError, ValueError):
                        # Said, not skipped: a record whose detail does not parse is shown as such.
                        print(f"    (record {r.get('audit_id')}: its detail is not readable JSON)")
                shown = []
                for field in ('person', 'for_person', 'member_id'):
                    v = d.get(field) if d.get(field) not in (None, '') else r.get(field)
                    if v in (None, '') or (field == 'member_id' and r.get('session_id') == 'system'):
                        continue
                    try:
                        text, state = it.open_identity(v, sink)
                    except Exception as e:
                        _die(f"Could not open an identity in {log}: {e}", exit_code=3)
                    if state == 'plain':
                        plain += 1
                        text = text + "  (plain: written before identities were sealed)"
                    shown.append(f"{field}={text}")
                event = r.get('event') if r.get('event') else d.get('event', '(no event)')
                subject = d.get('table') if d.get('table') else d.get('action', '')
                print(f"    {event:<14} {subject:<24} {' '.join(shown)}")
        if plain:
            print(f"\n  {plain} identity field(s) in this trail were written before identities were "
                  f"sealed and are readable as they stand. They are not rewritten (historical records "
                  f"never are).")
        print()
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
        # EVERY LOG'S ANCHORS ARE A LIST (HC-134). Only the top level was checked, so a log mapped to
        # a bare head, a number or anything else reached the verifier and stopped it on an internal
        # AttributeError or TypeError. A malformed file is refused whole, naming the log.
        for _log, _val in anchor_map.items():
            if not isinstance(_val, list):
                _kind = ("text" if isinstance(_val, str) else "a number"
                         if isinstance(_val, (int, float)) and not isinstance(_val, bool)
                         else "null" if _val is None else type(_val).__name__)
                _die(f"Anchors for `{_log}` in {anchors_path} must be a list of {{head, length}} "
                     f"anchors, and this one is {_kind}. Write it as:\n"
                     f'    "{_log}": [{{"head": "<64 hex characters>", "length": <records>}}]',
                     exit_code=3)
        print()
        failed = False
        checked_any = False
        uncovered = []
        for log in logs:
            entries = anchor_map.get(log)
            if not entries:
                uncovered.append(log)
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
                    _at = f" at length {f['length']}" if f.get('length') is not None else ""
                    print(f"     {red(f['kind'])}{_at}: {f['detail']}")
        # AN ANCHORED LOG THAT IS GONE IS A FAILURE (HC-135). Its anchors were published, so the log
        # existed; a database with no log by that name lost all of it.
        missing = sorted(l for l, v in anchor_map.items() if v and l not in logs)
        for log in missing:
            failed = True
            print(f"  {red('x')}  {log}  MISSING: {anchors_path} holds anchors for this log and "
                  f"{shown_target} has no log by that name. Either the whole log was removed, or these "
                  f"anchors were published for another database; find out which before trusting it.")
        # A log no anchor covers was not verified, and is said so by name.
        for log in uncovered:
            print(f"  {yellow('!')}  {log}  NOT VERIFIED: {anchors_path} holds no anchor for it.")
        print()
        print(f"  {dim('This compares heads and trusts the anchors as authentic. Verify each')}")
        print(f"  {dim('anchor signature upstream; this check does not verify signatures.')}")
        print()
        if failed:
            sys.exit(1)
        # NOTHING CHECKED IS NOT A PASS (HC-135). An empty file, an empty list or anchors for logs
        # this database does not hold matched nothing; exit 0 there told release automation the
        # trail was verified when not one record was compared. Exit 2 is "no coverage".
        if not checked_any:
            _have = ", ".join(logs) if logs else "none"
            _die(f"Nothing was verified: no anchor in {anchors_path} names a log in {shown_target} "
                 f"(its logs: {_have}).\n"
                 f"  Point --anchors at the file published for this database, or publish anchors "
                 f"from its current heads with `mio audit head {shown_target}`. Exit status 2 means no "
                 f"coverage.", exit_code=2)
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
            # The known duplicate-delivery signature (RULED 2026-10-12): the same record delivered
            # twice before the fix, identical content, each copy linked. Named, never rewritten.
            for _dup in info.get('duplicates') or []:
                print(f"     {yellow('!')} known duplicate delivery: audit_id {_dup} appears twice with "
                      f"identical content (delivered twice before the fix; the chain is intact)")
        else:
            failed = True
            print(f"  {red('x')}  {log}  chain BROKEN")
            print(f"     {info['reason']}")
            if info['broken_at']:
                print(f"     {dim('at audit_id ' + str(info['broken_at']))}")
    if _env and _env['present']:
        if _env['problems']:
            failed = True
            print(f"  {red('x')}  {AUDIT_ENVELOPE_LABEL}  INCONSISTENT with the trail")
            for _p in _env['problems']:
                print(f"     {_p}")
        else:
            print(f"  {green('v')}  {AUDIT_ENVELOPE_LABEL}  {_env['envelopes']} envelope(s) . "
                  f"{_env['acked']} delivered . {_env['owed']} owed . consistent with the trail")
    if action != "head":
        print()
        print(f"  {dim('An intact chain still cannot prove nothing was removed from the END of')}")
        print(f"  {dim('the log. A removed DELIVERED record is caught by the envelope check above;')}")
        print(f"  {dim('for the rest, compare the head against a previously published anchor.')}")
    print()
    if failed:
        sys.exit(1)


def cmd_version(args):
    # THE PRODUCT VERSION AND THE BUILD (unit 5h, RULED 2026-10-12). This printed a separate
    # language line, `v3.8 -- Phase 1`, beside the 6.0.0 release, so the one command a reader asks
    # "what is this" answered with two versions; and it never said which commit it was. The build
    # stamp is the same one /ping and /mio/health report (mohio_version.BUILD_SHA).
    print(f"\n  {bold('mio')}  Mohio Language CLI")
    print(f"  Mohio:            {VERSION} (build {BUILD_SHA})")
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

_STARTER_JOURNEY = (
    "framework: {framework}\n"
    "\n"
    'lock site_name "{name}"\n'
    "\n"
    "// journey.mho is this app's spine: every page in this folder inherits what it declares,\n"
    "// so shared things -- the framework, a database connection, shapes, page mappings -- belong\n"
    "// here, declared once and reached everywhere (best practice, not required). `framework:\n"
    "// {framework}` names how this app is served; the default, `web`, serves HTML pages by\n"
    "// convention. More in the manual. Delete this comment when you start.\n"
)

# BARE render, nothing else. `render` builds HTML and, with no routing around it, serves itself
# at / by convention -- so a single render block is a whole running page, with no database, no
# shape and no listen-for. The old starter opened with `connect db as sqlite` a heading page
# never touched, which created a real SQLite file and printed a disk-ephemerality warning for a
# static page, and taught -- before a word of comment -- that a page needs a database to exist.
_STARTER_INDEX = (
    "// This is the home page. `render` writes HTML, and on its own it serves at / with no\n"
    "// routing code -- that is why one render block is a running page. {{site_name}} comes from\n"
    "// journey.mho. Edit the HTML below; more patterns are in the cookbook. Delete this comment\n"
    "// when you start.\n"
    "render\n"
    "    <h1>{{site_name}}</h1>\n"
    "    <p>Your new project is running.</p>\n"
    "render: done\n"
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


def _write_starter(project_dir, display_name, description="", framework="web"):
    """Writes the starter into an existing, empty-enough directory. Returns the list of
    files written, in the order written, so the caller can show the pioneer what it did.

    `framework` is written into journey.mho explicitly, even for the `web` default, because the
    scaffold's own spine should show a pioneer the declaration rather than leave it implicit."""
    written = []

    def put(name, content):
        # EGRESS-EXEMPT: a scaffold file `mio new` writes; no runtime value
        (project_dir / name).write_text(content, encoding="utf-8")
        written.append(name)

    # A display name with a double quote in it would close the string on the `lock` line and
    # hand the pioneer a parse error in a file they never typed. Mohio Home settles this the
    # same way: the quote becomes an apostrophe rather than an escape nobody asked for.
    put("journey.mho", _STARTER_JOURNEY.format(
        framework=framework, name=display_name.replace('"', "'")))
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


def _new_repurpose_notice():
    """Printed after every `mio new <name>`. `mio new` still makes a project today, but the word
    is being repurposed: `new` is already a Mohio keyword (`new sh.Member at /signup` makes a new
    instance of a shape), and `mio new` at the CLI collides with that one-word-one-job rule. The
    notice says the split out loud, and names the release after which `mio new <name>` stops
    making a project, because a silent repurpose is what makes ignoring it risky."""
    print()
    print(f"  {yellow('Heads up:')} `mio new` is being repurposed.")
    print( "  Your project was created above -- `mio new` still makes one today. But `mio create`")
    print( "  is the verb for making a project now. `mio new` is moving to making things INSIDE a")
    print( "  project (a page, a shape). Inside Mohio, `new` is reserved for a condition on")
    print( "  arriving data (handlers are `listen for sh.X`). Those are not built yet.")
    # The release that ends the window is not set: the notice named v5.3, which never shipped (5.1.0
    # went to 6.0.0), and a named release that has already passed is a false statement. When one is
    # ruled, it goes here; test_battery_mio_create refuses any release at or below this build's.
    print(f"  {dim('A later release will stop `mio new <name>` creating a project -- use `mio create <name>`.')}")
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
    _new_repurpose_notice()


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


def cmd_create(args):
    """The generative verb: create a project, optionally naming its framework.

        mio create <name>              framework defaults to web
        mio create <framework> <name>  explicit framework

    A word beyond the framework and the name is the SCAFFOLD -- the page structure a framework
    is filled with -- which is a real, designed layer (T1-SCAFFOLD-DECLARATION) that is not
    built yet. It is refused by name rather than accepted-and-ignored, so a half-built argument
    is never shipped and never has to be built twice."""
    import mohio_framework as _fw
    positionals = list(getattr(args, "args", None) or [])

    if not positionals:
        _die("mio create needs a project name. Try `mio create my-site`, or "
             f"`mio create <framework> my-site` to name a framework "
             f"({', '.join('`' + v + '`' for v in sorted(_fw.BUILT))}).", exit_code=3)

    if len(positionals) == 1:
        framework, display_name = _fw.DEFAULT, positionals[0]
    elif len(positionals) == 2:
        framework, display_name = positionals[0], positionals[1]
        if not _fw.is_known(framework):
            _die(_fw.unknown_value_message(framework), exit_code=3)
        if _fw.canonical_base(framework) in _fw.DECLARED_NOT_BUILT:
            _die(_fw.not_built_message(framework), exit_code=3)
    else:
        _die(f"`mio create` takes a framework and a name, not a third word "
             f"({positionals[2]!r}). A scaffold -- the page structure a framework is filled "
             f"with -- is a real layer that is not built yet (it has no grammar and no CLI "
             f"argument), so it is refused rather than quietly ignored. Today: "
             f"`mio create {positionals[0]} {positionals[1]}`.", exit_code=3)

    framework = _fw.canonical_base(framework)
    # A built framework whose STARTER is not built yet refuses here, distinct from a
    # declared-but-unbuilt framework above: the framework serves, but the only project a starter can
    # write is the `web` page, and writing that for `api` or `game` is the wrong shape.
    if framework not in _fw.STARTER_BUILT:
        _die(_fw.starter_not_built_message(framework), exit_code=3)
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

    written = _write_starter(project_dir, display_name, args.description or "",
                             framework=framework)
    _report_starter(project_dir, display_name, written, folder)


# ── mio help: a GENERATED command list inside HAND-WRITTEN groups ────────────────────────────────
#
# The command LIST is generated from the parser (`_subparsers_action`), so a registered command can
# never be omitted -- there is no second list to fall out of step with (that omission is what hid
# `mio test` from two lanes, who wrote their suites in Python). The GROUPS and the WORDING stay
# hand-written, because a flat alphabetical dump reads worse than a list grouped by what a person is
# trying to do. A command placed in no group appears under "other" rather than vanishing, so wording
# stays human and completeness stays automatic. `tests/test_cli_help_complete.py` gates both halves:
# every registered command is grouped (or "other") AND carries a description worth reading.
#
# Each description says three things, because a complete list of terse strings satisfies a gate and
# fails the reader: WHAT it does in a newcomer's sentence, WHETHER it changes anything (writes / reads),
# and a WORKING EXAMPLE where the shape is not obvious from the name.
_COMMAND_GROUPS = [
    ("Start a project",        ["create", "new", "init"]),
    ("Run it",                 ["run", "serve"]),
    ("Check it before you ship", ["check", "test", "fmt", "walk", "writes", "sector", "ai-check"]),
    ("The audit trail",        ["audit"]),
    ("Scheduled work",         ["schedule"]),
    ("Language tooling",       ["generate", "translate", "schema", "harvest"]),
    ("Housekeeping",           ["warmup", "install-hooks", "version", "help"]),
]

# changes: True if the command can change something (files, a database, the audit store, git, a cache);
# False if it only reads and prints. `what`: a newcomer sentence. `example`: shown when the shape is not
# obvious from the name (None otherwise).
_COMMAND_HELP = {
    "create":  (True,  "Create a new project folder, optionally naming the framework (web, api, game). Writes new files.",
                "mio create my-site   (or: mio create api my-service)"),
    "new":     (True,  "Create a new project folder with a starter page. Writes new files.",
                "mio new my-site"),
    "init":    (True,  "Put that same starter into the folder you are already in. Writes new files.",
                "mio init"),
    "run":     (True,  "Run a program once and print what it produces. It changes data only if the program itself writes.",
                "mio run index.mho"),
    "serve":   (True,  "Start an HTTP server. Give it ONE FILE to serve that file, or a DIRECTORY to serve a whole "
                       "project (each file answers at its own URL). Your source is read-only; the app may write to its database.",
                "mio serve .        serves the project in this folder\n"
                "mio serve index.mho    serves just that one page"),
    "check":   (False, "Read a whole program and report every error and warning, without running it. Read-only, about a second.",
                "mio check index.mho     (or: mio check --all for the whole tree)"),
    "test":    (False, "Run the file's `it` cases and fail if any case fails, or if the file has none. Read-only (a scratch database).",
                "mio test orders.mho"),
    "fmt":     (True,  "Rewrite a file toward canonical Mohio. Prints the result by default; changes the file only with --write.",
                "mio fmt index.mho --write"),
    "walk":    (False, "Trace a program's data maps stage by stage, to see where a value comes from. Read-only.",
                "mio walk pipeline.mho"),
    "writes":  (False, "Show what each write in a program means, normalized, without running it. Read-only.",
                "mio writes orders.mho"),
    "sector":  (False, "Report which sector (compliance) rules are in force: for one file, or across the whole deployment. Read-only.",
                "mio sector index.mho     (or bare `mio sector` for the deployment inventory)"),
    "ai-check": (False, "Ask a real AI provider to review a program for problems. Read-only to your files, but it makes a billed API call.",
                "mio ai-check index.mho"),
    "audit":   (True,  "Inspect and complete the audit trail. `relay` CHANGES the audit store -- it writes the records a crash "
                       "left owed. `verify`, `head` and `verify-anchors` only read.",
                "mio audit verify app.db      reads and checks the chain\n"
                "mio audit relay app.db       WRITES owed records to the store"),
    "migrate": (True, "Convert the text columns of tables created before typed storage into the types their shapes "
                      "declare. CHANGES the database; --dry-run only reads and lists every value that will not convert.",
                "mio migrate typed-storage app.mho --dry-run"),
    "schedule": (True, "Fire the scheduled tasks that are due (`run-due`), or `list` them, or `watch` and fire on a timer. "
                       "run-due and watch CHANGE data by running tasks; list only reads.",
                "mio schedule run-due jobs.mho"),
    "generate": (True, "Generate an artifact, such as training data, and write it out.",
                "mio generate training-data"),
    "translate": (False, "Translate a program's natural-language layer into another language and print it. Read-only.",
                "mio translate index.mho --to es"),
    "schema":  (True,  "Work with .mhoschema files: `generate` writes one, `check` and `show` only read.",
                "mio schema show orders.mhoschema"),
    "harvest": (False, "Extract the current word inventory from the grammar, for building langmaps. Read-only (unless you write it out).",
                "mio harvest --output words.json"),
    "warmup":  (True,  "Pre-warm the parser cache so the next command starts fast. Changes only a local cache file.",
                None),
    "install-hooks": (True, "Install this repo's git pre-commit hooks. Changes your .git/hooks.",
                None),
    "version": (False, "Print version information. Read-only.", None),
    "help":    (False, "Print this overview. Read-only.", None),
}


def _subparsers_action(parser):
    for a in parser._actions:                         # noqa: SLF001 -- argparse has no public accessor
        if a.__class__.__name__ == '_SubParsersAction':
            return a
    return None


def _registered_commands(parser=None):
    """Every subcommand the parser knows, from the parser itself -- never a hand-kept list."""
    sub = _subparsers_action(parser or build_arg_parser())
    return sorted(sub.choices.keys()) if sub else []


def _command_options(parser, name):
    """The options and positionals a subcommand accepts, read from its own subparser."""
    sub = _subparsers_action(parser)
    sp = sub.choices.get(name) if sub else None
    if sp is None:
        return []
    out = []
    for act in sp._actions:                           # noqa: SLF001
        flags = list(act.option_strings) or [f"<{act.dest}>"]
        label = "/".join(flags)
        if act.option_strings and act.nargs != 0 and act.__class__.__name__ not in (
                '_StoreTrueAction', '_StoreFalseAction', '_HelpAction'):
            label += " " + (act.metavar or act.dest.upper())
        out.append((label, (act.help or "").strip()))
    return out


def _render_command_overview():
    """The grouped, generated command list. A command in no hand-written group lands in `other`, so
    nothing a developer could type is ever missing from this list."""
    parser = build_arg_parser()
    known = set(_registered_commands(parser))
    grouped = set()
    lines = []
    groups = list(_COMMAND_GROUPS)
    # Any registered command not placed in a group is surfaced under "other" -- completeness is
    # automatic even when the hand-written groups fall behind.
    placed = {c for _, cmds in groups for c in cmds}
    leftover = sorted(known - placed)
    if leftover:
        groups = groups + [("Other", leftover)]
    for title, cmds in groups:
        shown = [c for c in cmds if c in known]
        if not shown:
            continue
        lines.append(f"  {bold(title)}")
        for c in shown:
            grouped.add(c)
            changes, what, _ex = _COMMAND_HELP.get(c, (False, "", None))
            tag = red("writes") if changes else dim("reads")
            lines.append(f"    {bold('mio ' + c):<22}  {dim('[' + '')}{tag}{dim(']')}  {what}")
        lines.append("")
    return "\n".join(lines)


def _print_command_help(name):
    """`mio <cmd> --help` / `mio help <cmd>`: that one command's description, options and example --
    the per-command help argparse cannot give here, because every subparser is add_help=False."""
    parser = build_arg_parser()
    if name not in _registered_commands(parser):
        print(f"\n  {red('x')} There is no `mio {name}` command. Run `mio help` for the list.\n")
        return
    changes, what, example = _COMMAND_HELP.get(name, (False, "", None))
    tag = red("writes") if changes else dim("reads only")
    print(f"\n  {bold('mio ' + name)}  {dim('[')}{tag}{dim(']')}\n")
    if what:
        print(f"    {what}\n")
    opts = _command_options(parser, name)
    real = [(lbl, h) for lbl, h in opts if not lbl.startswith("<")] + \
           [(lbl, h) for lbl, h in opts if lbl.startswith("<")]
    if real:
        print(f"  {bold('OPTIONS')}")
        for label, helptext in real:
            print(f"    {label:<26}  {dim(helptext)}" if helptext else f"    {label}")
        print()
    if example:
        print(f"  {bold('EXAMPLE')}")
        for ln in example.split("\n"):
            print(f"    {ln}")
        print()


def cmd_help(args):
    # A specific command's own help: `mio <cmd> --help` (or `mio help <cmd>`).
    _topic = getattr(args, "help_topic", None)
    if _topic:
        _print_command_help(_topic)
        return
    print(f"""
  {bold('mio')} -- Mohio Language CLI  {dim(f'v{VERSION}')}

  {bold('COMMANDS')}  {dim('(mio <command> --help for a command`s options)')}

{_render_command_overview()}
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
    s.add_argument("--dev", action="store_true", default=False,
                   help="development: serve the files that load; a file that does not answers 503")

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
    c.add_argument("--locations", nargs="?", const="", default=None, metavar="CODE",
                   help="List every site of a repeated warning, not just the first (name a CODE to list one class)")
    c.add_argument("--show-suppressed", action="store_true", dest="show_suppressed",
                   help="List each advisory a journey's `suppress` line quiets, with the reason it gives")
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

    # create / new / init -- the starter project
    cr = sub.add_parser("create", add_help=False)
    cr.add_argument("args", nargs="*",
                    help="<name>, or <framework> <name> (framework: web api game)")
    cr.add_argument("--in", dest="folder", default=None,
                    metavar="FOLDER", help="Folder to create (default: the name, lowercased)")
    cr.add_argument("--description", default=None)
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
    tr.add_argument("--from", dest="from_lang", default=None,
                    help="source human language (default: the file's `// language:` line, else en)")
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
    mig = sub.add_parser("migrate", add_help=False)
    mig.add_argument("migrate_action", nargs="?", default=None,
                     choices=["typed-storage", "protect", "add-unique", "unprotect"])
    mig.add_argument("file", nargs="?", default=None)
    # `mio migrate add-unique code,region on claims app.mho`: the key, `on`, the table, the program.
    mig.add_argument("more", nargs="*", default=[])
    mig.add_argument("--dry-run", dest="dry_run", action="store_true",
                     help="List the plan and every value that will not convert; change nothing")
    wr = sub.add_parser("writes", add_help=False)
    wr.add_argument("file", nargs="?", default=None)
    aud = sub.add_parser("audit", add_help=False)
    aud.add_argument("audit_action", nargs="?", default="verify",
                     choices=["verify", "head", "verify-anchors", "relay", "read"])
    aud.add_argument("file", nargs="?", default=None)
    aud.add_argument("--anchors", default=None,
                     help="Path to a JSON file of published anchors (for verify-anchors)")
    aud.add_argument("--program", default=None,
                     help="The .mho program whose own audit logs the relay may deliver to (for relay)")
    sec = sub.add_parser("sector", add_help=False)
    sec.add_argument("file", nargs="?", default=None,
                     help="A .mho file whose sectors to report (omit for the deployment inventory)")
    sec.add_argument("--json", action="store_true",
                     help="Output structured JSON (same facts as the text form)")
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
            # FAILS LOUD (ruled by Ron, 2026-10-03) -- see _scan_incomplete_warn.
            _scan_incomplete_warn(ctx, "Layer 3 whole-program scanners", _scan_err)
        if ctx.errors:
            for e in ctx.errors:
                _print_compile_error(e, source, filename)
            sys.exit(1)

    from mohio_interpreter import MohioInterpreter, MockAiRuntime
    ai = _construct_ai_runtime(getattr(args, "api_key", None), verbose) \
        if getattr(args, "ai", False) else MockAiRuntime()
    interp = _cli_interpreter(ai, verbose, filename, args)
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



# ── mio migrate typed-storage ────────────────────────────────────────────────────────────────
# T1-DB-TYPED-STORAGE (RULED by Ron, 2026-10-05). A table created before typed storage keeps its
# declared int, boolean, decimal and currency fields in TEXT columns; a new table gets real types.
# This converts an existing table's columns to the types its shape declares, through the SAME
# encoder a write uses (`_encode_typed_value`), so a migrated value is stored exactly as a new
# write of it would be: SQLite's scaled integer for a currency, an exact NUMERIC/DECIMAL/NUMBER
# elsewhere, never a rounded one. NOTHING MOVES UNLESS EVERY VALUE CONVERTS: a value that will
# not convert is listed (table, column, row id, the value, why) and the column is left as it is.
# --dry-run lists the plan and every such value and changes nothing. Each converted column leaves
# one audit record naming the table, the column, the two types and the row count.

_MIGRATE_TEXT_TYPES = ('TEXT', 'VARCHAR', 'VARCHAR2', 'CHARACTER VARYING', 'CHAR', 'NVARCHAR2',
                       'LONGTEXT', 'MEDIUMTEXT', 'TINYTEXT', 'CLOB', 'NCLOB', '')
_MIGRATE_TYPED_ROOTS = ('int', 'integer', 'boolean', 'bool', 'dec', 'decimal')


def _migrate_engine(db):
    name = type(db).__name__
    if name == 'PostgresRuntime':
        return 'postgres'
    if name == 'MySQLRuntime':
        return 'mysql'
    if name == 'OracleRuntime':
        return 'oracle'
    if name == 'DbRuntime':
        return 'sqlite'
    return None


def _migrate_q(engine, ident):
    if engine == 'mysql':
        return f'`{ident}`'
    if engine == 'oracle':
        return str(ident)
    return f'"{ident}"'


def _migrate_sql(engine, sql):
    """Write SQL with `?` placeholders; renumber for the connected engine."""
    if engine in ('postgres', 'mysql'):
        return sql.replace('?', '%s')
    if engine == 'oracle':
        n = [0]

        def _r(_m):
            n[0] += 1
            return f':{n[0]}'
        import re as _re
        return _re.sub(r'\?', _r, sql)
    return sql


def _migrate_current_type(engine, cur, table, col):
    """The column's stored type from the engine's own catalogue, upper case, or None when the
    table or column does not exist."""
    if engine == 'sqlite':
        cur.execute(f'PRAGMA table_info("{table}")')
        for r in cur.fetchall():
            if str(r[1]) == col:
                return str(r[2] or '').upper()
        return None
    if engine == 'postgres':
        cur.execute("SELECT data_type AS dt FROM information_schema.columns "
                    "WHERE table_schema = current_schema() AND table_name = %s AND column_name = %s",
                    (table, col))
    elif engine == 'mysql':
        cur.execute("SELECT data_type AS dt FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND table_name = %s AND column_name = %s",
                    (table, col))
    else:
        cur.execute("SELECT data_type FROM user_tab_columns WHERE table_name = :1 AND column_name = :2",
                    [table.upper(), col.upper()])
    row = cur.fetchone()
    if not row:
        return None
    v = (row.get('dt') or row.get('DT')) if isinstance(row, dict) else row[0]
    return str(v or '').upper()


def _migrate_convert(db, value, declared):
    """(converted, None) or (None, reason). The same rules a write applies."""
    from decimal import Decimal, InvalidOperation
    if value is None:
        return None, None
    s = str(value).strip()
    if s == '':
        return None, None
    root = declared.split('.')[0]
    if root in ('boolean', 'bool'):
        if s.lower() in ('true', '1'):
            return True, None
        if s.lower() in ('false', '0'):
            return False, None
        return None, 'not a boolean (true, false, 1 or 0)'
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None, 'not a number'
    if root in ('int', 'integer'):
        if d != d.to_integral_value():
            return None, 'has a fractional part, and the field is an int'
        return int(d), None
    try:
        enc = getattr(db, '_encode_typed_value', None)
        from mohio_interpreter import _encode_typed_value as _base_enc
        return (enc or _base_enc)(d, declared), None
    except Exception as e:  # noqa: BLE001 -- the encoder's own refusal IS the reason reported
        return None, str(e).split('\n')[0]


def cmd_migrate_protect(path, dry):
    """mio migrate protect: encrypt in place the stored values of every field the program declares
    protected that were written unprotected, and build their search index (RULED by Ron 2026-10-12).
    The program's own declarations say which fields are protected; one table at a time, in a
    transaction where the engine has one, with the counts reported."""
    if path.is_dir():
        _die(f"mio migrate protect takes one .mho file, not a folder ({path}). Use the program in "
             f"that folder that declares the protected fields, for example "
             f"`mio migrate protect {path}/app.mho`.", exit_code=3)
    filename = str(path)
    source = _read_source(path, exit_code=3)
    tree, pctx = _parse_and_validate(source, filename, False)
    if pctx.errors:
        for e in pctx.errors:
            _print_compile_error(e, source, filename)
        sys.exit(1)
    from mohio_transformer_ast import transform as ast_transform
    program = ast_transform(tree, source)
    program = _resolve_includes(program, filename, verbose=False)
    program = _apply_journey(program, filename, verbose=False)
    from mohio_interpreter import MohioInterpreter
    it = MohioInterpreter(verbose=False)
    # This command IS the sealing the field register asks for, so the register lets it raise a
    # column's protection over plain values (OR-2); everything else still refuses that.
    it._migrating_protection = True
    it.run_declarations(program)
    title = 'mio migrate protect' + (' --dry-run' if dry else '')
    print(f"\n  {bold(title)}  {dim(filename)}\n")
    try:
        report = it.migrate_protect(dry_run=dry)
    except Exception as e:
        _die(f"mio migrate protect stopped, and the table it was working on was left as it was: {e}\n"
             f"  To fix it, correct what the line above names, then use the same command again; "
             f"tables already protected are skipped.", exit_code=1)
    if not report:
        print(f"  {dim('This program declares no protected field, so there is nothing to protect.')}\n")
        return
    verb = "would encrypt" if dry else "encrypted"
    index_words = {'present': 'search index {} in place', 'built': 'search index {} built',
                   'would build': 'would build search index {}'}
    failed = []
    for row in report:
        where = f"db.{row['table']}.{row['field']}"
        if row['problem']:
            failed.append(row)
            print(f"  {red('x')}  {where}: {row['problem']}")
        elif row['note']:
            print(f"  {where}: {row['note']}")
        else:
            bidx = f"`{row['field']}__bidx`"
            print(f"  {where}: {verb} {row['sealed']} value(s), indexed {row['indexed']} already "
                  f"encrypted value(s), {index_words[row['index']].format(bidx)}")
    print()
    if failed:
        # BS-003: a migration reports success only when every protected field it covers has its
        # search index, read back from the database; anything else exits non-zero.
        first = failed[0]
        print(red(f"  mio migrate protect did not finish: {len(failed)} protected field(s) are not "
                  f"fully protected, first db.{first['table']}.{first['field']}.") + "\n"
              f"  Until it finishes, a program that declares `{first['field']}` protected refuses "
              f"to read or write db.{first['table']}.\n"
              f"  Fix what each line above names, then run it again:\n"
              f"      mio migrate protect {filename}\n")
        sys.exit(1)


def cmd_migrate_add_unique(key_text, more):
    """mio migrate add-unique code,region on <table> <program.mho>: the reviewed step that adds a
    table's declared key, as a UNIQUE, to a table that already exists without it (T1-DECLARED-UNIQUE-
    ENFORCE, RULED by Ron 2026-10-27). A deployed app refuses to start on such a table and names this
    command; development adds the key itself when no key repeats. Only the key the program declares
    for that table (`save ... unless code, region exists`), and never over a repeated key: the
    repeats are named and nothing changes. One audit record names the table and the key."""
    usage = ("Usage: mio migrate add-unique <key> on <table> <program.mho>\n"
             "  Adds the key a program declares for a table (`save to db.<table> unless <key> exists`)\n"
             "  as a UNIQUE on a table that already exists without it, for example\n"
             "      mio migrate add-unique code,region on claims app.mho")
    if not key_text or len(more) != 3 or more[0] != 'on':
        _die(usage, exit_code=3)
    table, filename = more[1], more[2]
    cols = [c.strip() for c in key_text.split(',') if c.strip()]
    path = Path(filename)
    if path.is_dir():
        _die(f"mio migrate add-unique takes one .mho file, not a folder ({filename}).", exit_code=3)
    source = _read_source(path, exit_code=3)
    tree, pctx = _parse_and_validate(source, filename, False)
    if pctx.errors:
        for e in pctx.errors:
            _print_compile_error(e, source, filename)
        sys.exit(1)
    from mohio_transformer_ast import transform as ast_transform
    program = ast_transform(tree, source)
    program = _resolve_includes(program, filename, verbose=False)
    program = _apply_journey(program, filename, verbose=False)
    from mohio_interpreter import MohioInterpreter, Context
    it = MohioInterpreter(verbose=False)
    it._migrating_unique = True          # this command is the step the connect-time review names
    from mohio_interpreter import MohioRuntimeError
    try:
        it.run_declarations(program)
    except MohioRuntimeError as e:
        _die(f"{filename} could not start, so nothing was changed: {e}", exit_code=1)
    db = it._db
    print(f"\n  {bold('mio migrate add-unique')}  {dim(filename)}\n")
    declared = (getattr(it, '_declared_unique', None) or {}).get(table)
    # A field the program's shape declares `unique` on this table is the other key this adds (RULED
    # by Ron 2026-10-28): a deployed app names this command for it too.
    shaped = [str(c) for c in ((getattr(it, '_shaped_unique_cols', None) or {}).get(table) or [])]
    by_shape = len(cols) == 1 and cols[0] in shaped and not (declared and set(cols) == set(declared))
    if not declared and not by_shape:
        _die(f"{filename} declares no key for db.{table}: no `save to db.{table} unless ... exists` and no "
             f"shape field `{', '.join(cols)}` marked `unique` for it. This command adds only a key the "
             f"program declares.", exit_code=1)
    if not by_shape and set(cols) != set(declared):
        _die(f"{filename} declares db.{table}'s key as {', '.join(declared)}, not {', '.join(cols)}. "
             f"This command adds only the key the program declares:\n"
             f"      mio migrate add-unique {','.join(declared)} on {table} {filename}", exit_code=1)
    if db is None or not hasattr(db, 'unique_index_sets'):
        _die(f"{filename} connects to no SQL database Mohio can migrate (sqlite, postgres, mysql or "
             f"oracle).", exit_code=1)
    if by_shape:
        declared = cols
        _phys = list(db._shaped_unique_cols.get(table) or shaped)
        physical = [_phys[shaped.index(cols[0])]]
    else:
        physical = list(db._declared_unique.get(table) or declared)
    sets = db.unique_index_sets(table)
    if sets is None:
        _die(f"db.{table} does not exist yet, so there is nothing to add: the program creates it with "
             f"the UNIQUE on {', '.join(declared)} at its first save.", exit_code=1)
    if frozenset(c.lower() for c in physical) in sets:
        print(f"  db.{table} already has the UNIQUE on {', '.join(declared)}. Nothing to do.\n")
        return
    repeats, total = db.repeated_keys(table, physical)
    if repeats:
        _die(f"db.{table} holds a key more than once: "
             f"{it._repeated_key_text(repeats, total, physical)}. Nothing was changed.\n"
             f"  Remove or merge the repeated rows, then run this command again.", exit_code=1)
    db.add_unique_key(table, physical)
    it._audit_event('security_audit_log', {
        'event': 'unique_key_added', 'table': table, 'field': ', '.join(declared),
        'detail': f'{table}: UNIQUE on {", ".join(declared)} added by mio migrate add-unique',
    }, Context())
    print(f"  {green('v')}  db.{table}: added the UNIQUE on {', '.join(declared)}\n")


def _protection_words(prot):
    """`pii` or `phi, pii`, or `plain` for a column with no protection."""
    named = sorted(p for p in prot if p)
    return ', '.join(named) if named else 'plain'


def _tag_words(prot):
    """The class tags a protected column is declared with: its tags, `pii` when it is only `encrypted`."""
    named = sorted(p for p in prot if p and p != 'encrypted')
    return ', '.join(named) if named else 'pii'


def cmd_migrate_unprotect(target, more):
    """mio migrate unprotect <table>.<column> <program.mho>: correct a field register that records a
    PLAIN column as protected (T1-MIGRATE-UNPROTECT, RULED by Ron 2026-10-27, D1 option B). The old
    name-wide tag binding registered unrelated plain columns, and the register's rule that protection
    never goes down then refused the program that declares the column plain, at every start. One
    column at a time, never on boot: the program must declare it plain, and the column must hold NO
    sealed value; any sealed value is a real downgrade, and the refusal stands. The register entry is
    rewritten as an audited write, and one audit record names the change."""
    usage = ("Usage: mio migrate unprotect <table>.<column> <program.mho>\n"
             "  Records as plain one column the field register over-protected, when the program\n"
             "  declares it plain and it holds no sealed value, for example\n"
             "      mio migrate unprotect staff_decisions.reason app.mho")
    if not target or len(more) != 1 or target.count('.') != 1:
        _die(usage, exit_code=3)
    table, col = target.split('.')
    filename = more[0]
    path = Path(filename)
    if path.is_dir():
        _die(f"mio migrate unprotect takes one .mho file, not a folder ({filename}).", exit_code=3)
    source = _read_source(path, exit_code=3)
    tree, pctx = _parse_and_validate(source, filename, False)
    if pctx.errors:
        for e in pctx.errors:
            _print_compile_error(e, source, filename)
        sys.exit(1)
    from mohio_transformer_ast import transform as ast_transform
    program = ast_transform(tree, source)
    program = _resolve_includes(program, filename, verbose=False)
    program = _apply_journey(program, filename, verbose=False)
    from mohio_interpreter import MohioInterpreter
    it = MohioInterpreter(verbose=False)
    # The register's refusal for THIS column is the one this command answers, so it is held for it
    # alone; every other column is checked as at any start.
    it._migrating_unprotect = (table, col)
    from mohio_interpreter import MohioRuntimeError
    try:
        it.run_declarations(program)
    except MohioRuntimeError as e:
        _die(f"{filename} could not start, so nothing was changed: {e}", exit_code=1)
    db = it._db
    print(f"\n  {bold('mio migrate unprotect')}  {dim(filename)}\n")
    pending = getattr(it, '_unprotect_pending', None)
    if pending is None:
        reg = it._field_register_read(db) if db is not None else {}
        entry = reg.get((table, col))
        if entry is None:
            _die(f"db.{table}.{col} has no field-register entry in this database, so there is nothing "
                 f"to unprotect.", exit_code=1)
        _die(f"{filename} declares db.{table}.{col} with the protection the register records "
             f"({_protection_words(str(entry.get('protection') or '').split(','))}), so there is nothing to unprotect. This command "
             f"records a column plain only for a program that declares it plain.", exit_code=1)
    sealed = db.sealed_count(table, col) if db.table_columns(table) else 0
    if sealed:
        _die(f"db.{table}.{col} holds {sealed} sealed value(s), so recording it plain would leave them "
             f"unreadable to every program reading it as plain. Nothing was changed, and the register "
             f"keeps it protected ({', '.join(sorted(pending['was']))}).\n"
             f"  A column holding sealed values stays protected: declare it `{col} as "
             f"{pending['declared_type'] if pending['declared_type'] else 'text'} [{_tag_words(pending['was'])}]`.",
             exit_code=1)
    ctx = pending['ctx']
    it._field_register_write(db, ctx, table, col, pending['declared_type'], pending['protection'],
                             pending['before'])
    it._audit_event('security_audit_log', {
        'event': 'field_unprotected', 'table': table, 'field': col,
        'detail': (f"{table}.{col}: the field register recorded {', '.join(sorted(pending['was']))} and "
                   f"the program declares {_protection_words(pending['protection'])}; "
                   f"no sealed value held; recorded by mio migrate unprotect"),
    }, ctx)
    print(f"  {green('v')}  db.{table}.{col}: the field register records it as "
          f"{_protection_words(pending['protection'])} (was {_protection_words(pending['was'])}); "
          f"it held no sealed value\n")


def cmd_migrate(args):
    action = getattr(args, 'migrate_action', None)
    filename = getattr(args, 'file', None)
    dry = bool(getattr(args, 'dry_run', False))
    if action == 'protect' and filename:
        return cmd_migrate_protect(Path(filename), dry)
    if action == 'add-unique':
        return cmd_migrate_add_unique(filename, list(getattr(args, 'more', None) or []))
    if action == 'unprotect':
        return cmd_migrate_unprotect(filename, list(getattr(args, 'more', None) or []))
    if action != 'typed-storage' or not filename:
        _die("Usage: mio migrate typed-storage <file.mho> [--dry-run]\n"
             "  Converts the text columns of tables created before typed storage into the types\n"
             "  their shapes declare. --dry-run lists the plan and every value that will not\n"
             "  convert, and changes nothing.\n"
             "       mio migrate protect <file.mho> [--dry-run]\n"
             "  Encrypts, in place, the stored values of every field the program declares\n"
             "  protected ([pii], [phi], [pci], sec.encrypt) that another program wrote\n"
             "  unprotected, and builds their search index. --dry-run counts and changes nothing.\n"
             "       mio migrate add-unique <key> on <table> <file.mho>\n"
             "  Adds the key the program declares for a table (`unless <key> exists`) as a UNIQUE\n"
             "  on a table that already exists without it.\n"
             "       mio migrate unprotect <table>.<column> <file.mho>\n"
             "  Records as plain one column the field register over-protected, when it holds no\n"
             "  sealed value and the program declares it plain.",
             exit_code=3)
    path = Path(filename)
    if path.is_dir():
        _die(f"mio migrate typed-storage takes one .mho file, not a folder ({filename}).", exit_code=3)
    source = _read_source(path, exit_code=3)
    tree, pctx = _parse_and_validate(source, filename, False)
    if pctx.errors:
        for e in pctx.errors:
            _print_compile_error(e, source, filename)
        sys.exit(1)
    from mohio_transformer_ast import transform as ast_transform
    program = ast_transform(tree, source)
    program = _resolve_includes(program, filename, verbose=False)
    program = _apply_journey(program, filename, verbose=False)
    from mohio_interpreter import MohioInterpreter, Context
    it = MohioInterpreter(verbose=False)
    # A DECLARED TYPE THAT DIFFERS FROM THE FIELD REGISTER is what this command exists to make true
    # (BS-008): the register's own refusal ("registered as `dec`, declared `usd`") is held while it
    # runs, and the new type is recorded only for a column whose values converted.
    it._migrating_types = True
    it.run_declarations(program)
    db = it._db
    engine = _migrate_engine(db) if db is not None else None
    if engine is None:
        _die(f"{filename} connects to no SQL database Mohio can migrate (sqlite, postgres, mysql "
             f"or oracle). Declare one with `connect db as <engine> from env.YOUR_VAR`.", exit_code=1)
    title = 'mio migrate typed-storage' + (' --dry-run' if dry else '')
    print(f"\n  {bold(title)}  {dim(filename)}  ({engine})\n")
    col_types = getattr(db, '_shaped_col_types', None) or getattr(it, '_shaped_col_types', {}) or {}
    plan, refusals = [], []
    cur = db.conn.cursor()
    # An audit relation is append-only (Ron, 2026-10-13): this command would UPDATE its rows and
    # drop and rename a column, so a shape that declares one is refused before anything changes.
    from mohio_audit_grades import audit_append_only_refusal, AUDIT_APPEND_ONLY_FIX
    _audit_tables = [t for t in sorted(col_types)
                     if audit_append_only_refusal(t, 'mio migrate typed-storage')]
    if _audit_tables:
        for _t in _audit_tables:
            print(f"  {red('x')}  " + audit_append_only_refusal(_t, 'mio migrate typed-storage'))
        print(f"     {AUDIT_APPEND_ONLY_FIX}\n")
        sys.exit(1)
    for table in sorted(col_types):
        for col, declared in sorted(col_types[table].items()):
            declared = str(declared or '').lower()
            is_money = it._is_currency(declared)
            if declared.split('.')[0] not in _MIGRATE_TYPED_ROOTS and not is_money:
                continue
            current = _migrate_current_type(engine, cur, table, col)
            if current is None:
                continue                    # no table or column yet: it is created typed
            base = current.split('(')[0].strip()
            target = db._declared_column_sql_type(declared)
            if str(target).split('(')[0].strip().upper() in _MIGRATE_TEXT_TYPES:
                continue                    # the declared type is stored as text on this engine
            # THE SAME QUESTION THE WRITE ASKS (BS-006/008, Summit R35): is the live column the
            # storage kind Mohio creates for the declaration? Only a TEXT column used to count as
            # owed a migration, so a legacy NUMERIC or REAL money column was "already typed", the
            # command reported nothing to migrate, and the next write refused that same column as
            # a divergence. A column of the right kind is left alone, whatever its spelling.
            _kinds = db.column_kinds(table) if hasattr(db, 'column_kinds') else None
            _have = (_kinds or {}).get(str(col).lower())
            if _have is not None and hasattr(db, '_storage_kind'):
                if _have[0] == db._storage_kind(target):
                    continue                # already the kind Mohio creates
            elif base not in _MIGRATE_TEXT_TYPES:
                continue                    # no kind reader on this engine: the text-only rule
            cur.execute(_migrate_sql(engine, f'SELECT id, {_migrate_q(engine, col)} FROM '
                                             f'{_migrate_q(engine, table)}'))
            rows = cur.fetchall()
            converted, bad = [], []
            for r in rows:
                rid = r['id'] if isinstance(r, dict) else r[0]
                val = (r.get(col) if isinstance(r, dict) else r[1])
                if isinstance(r, dict) and val is None and col.upper() in r:
                    val = r[col.upper()]
                new, why = _migrate_convert(db, val, declared)
                if why:
                    bad.append((rid, val, why))
                else:
                    converted.append((rid, new))
            plan.append((table, col, declared, current, target, converted, bad))
            refusals.extend((table, col, rid, val, why) for rid, val, why in bad)
    if not plan:
        _type_register_unmet(it, set())
        print(f"  {green('v')}  Nothing to migrate: every declared typed column is already typed.\n")
        return
    for table, col, declared, current, target, converted, bad in plan:
        mark = red('x') if bad else green('v')
        print(f"  {mark}  {table}.{col}  {dim(current)} -> {target}  "
              f"({len(converted)} value(s) convert" + (f", {len(bad)} do not" if bad else "") + ")")
    if refusals:
        print(f"\n  {red('These values will not convert. Nothing was changed.')}\n")
        for table, col, rid, val, why in refusals:
            print(f"    {table}.{col}  id {rid}: {val!r} -- {why}")
        print(f"\n  Fix or clear these values, then run it again.\n")
        sys.exit(0 if dry else 1)
    if dry:
        _type_register_unmet(it, {(t, c) for t, c, *_r in plan}, dry=True)
        print(f"\n  {dim('Dry run: every value converts. Run without --dry-run to migrate.')}\n")
        return
    ctx = Context()
    ctx.set_connection('db', db)
    _held = getattr(it, '_type_register_pending', None) or {}
    for table, col, declared, current, target, converted, bad in plan:
        tmp = f'{col}_mtyp'
        qt, qc, qtmp = _migrate_q(engine, table), _migrate_q(engine, col), _migrate_q(engine, tmp)
        add = (f'ALTER TABLE {qt} ADD ({qtmp} {target})' if engine == 'oracle'
               else f'ALTER TABLE {qt} ADD COLUMN {qtmp} {target}')
        cur.execute(add)
        for rid, new in converted:
            cur.execute(_migrate_sql(engine, f'UPDATE {qt} SET {qtmp} = ? WHERE id = ?'), [new, rid])
        cur.execute(f'ALTER TABLE {qt} DROP COLUMN {qc}')
        cur.execute(f'ALTER TABLE {qt} RENAME COLUMN {qtmp} TO {qc}')
        db.conn.commit()
        it._audit_event('security_audit_log', {
            'event': 'typed_storage_migrated', 'table': table, 'field': col,
            'detail': f'{table}.{col}: {current} -> {target}, {len(converted)} value(s), declared {declared}',
        }, ctx)
        print(f"  {green('v')}  migrated {table}.{col} to {target}")
        # THE REGISTER FOLLOWS THE DATA (BS-008): its new type is written only now, after this
        # column's values converted, never at setup.
        if (table, col) in _held:
            _nt, _prot, _before, _rctx, _ot = _held.pop((table, col))
            it._field_register_write(db, _rctx, table, col, _nt, _prot, _before)
            print(f"  {green('v')}  field register: {table}.{col} is now `{_nt}` (was `{_ot}`)")
    _type_register_unmet(it, set())
    print()


def _type_register_unmet(it, planned, dry=False):
    """A declared type the field register disagrees with, that this run did not make true: its
    column needed no conversion, or is not one this command converts. Said loud, because the
    program will still refuse that column at startup. Exits 1 (0 on a dry run, which reports)."""
    held = {k: v for k, v in (getattr(it, '_type_register_pending', None) or {}).items()
            if k not in planned}
    if not held:
        return
    print(f"\n  {red('The field register disagrees with these declarations, and no conversion makes it true:')}\n")
    for (table, col), (nt, _p, _b, _c, ot) in sorted(held.items()):
        print(f"    db.{table}.{col} is registered `{ot}` and declared `{nt}`, and its column needed no "
              f"conversion here, so the register was not changed.")
    print(f"\n  Declare these as the register says, or convert the column's values deliberately, "
          f"then run it again.\n")
    if not dry:
        sys.exit(1)


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
            # FAILS LOUD (ruled by Ron, 2026-10-03) -- see _scan_incomplete_warn.
            _scan_incomplete_warn(ctx, "Layer 3 whole-program scanners", _scan_err)
        if ctx.errors:
            for e in ctx.errors:
                _print_compile_error(e, source, filename)
            print(red(f"  {len(ctx.errors)} error(s) -- run `mio check {filename}`.\n"))
            sys.exit(1)

    from mohio_interpreter import MohioInterpreter, Context
    it = MohioInterpreter(verbose=verbose)
    # A scheduled job writes as its app, so its owed audit records are its own app's to deliver.
    it.app_id = _app_identity(filename)
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
                        _print_program_output(f"    {line}")
                _time.sleep(interval)
        except KeyboardInterrupt:
            print("\n  Stopped.")
        return

    fired = it.run_due_schedules(exec_ctx)
    failed = getattr(it, 'schedule_failures', None) or []
    if fired:
        print(f"  Fired {len(fired)} schedule(s): {', '.join(fired)}")
    elif not failed:
        print("  No schedules due.")
    for line in it.shown:
        _print_program_output(f"    {line}")
    if failed:
        # A driver (cron, a worker) reads the exit code; a run where a job failed is not a success.
        print(f"  x {len(failed)} schedule(s) failed: {', '.join(n for n, _ in failed)}")
        sys.exit(1)


def cmd_fmt(args):
    """Auto-format a .mho file toward canonical form.

    v1 normalizes legacy assignment spellings (`set` / `=`) to canonical
    `name value`. Dry-run by default; --write applies in place.
    """
    from pathlib import Path
    path = Path(args.file)
    src = _read_source(path)
    _original_src = src   # kept verbatim so the reported count reflects lines actually changed,
                          # not edits applied -- a single wrapper removal dedents its whole body
    grammar = _load_grammar()
    parser  = _make_parser_cached(grammar)
    from mohio_fmt import dequote_paths, format_source
    # Fix a mistaken quoted path (`at "/x"` -> `at /x`) before transforming, so a
    # file the compiler would reject for a quoted path still formats and gets fixed
    # here rather than dying on the fail-loud.
    src, _path_fixes = dequote_paths(src, parser, parse=lambda text: _fmt_parse(parser, text, None))
    # Strip the retired bare `listen for` wrapper BEFORE transforming, for the same reason as the
    # quoted path above: the compiler now rejects the wrapper (Batch 16), so remove it here -- a
    # line-based edit that needs no parse -- rather than dying on the fail-loud. Everything
    # downstream (pretokenize, transform, format_source) then sees the wrapper-free source, so the
    # AST line numbers the Assignment edits use line up with the lines format_source rewrites.
    from mohio_fmt import _remove_listen_wrappers, new_to_listen
    _wrap_lines, _wrap_changes = _remove_listen_wrappers(src.split("\n"))
    # `new sh.X` is not a handler (ruled 2026-10-07): the compiler refuses it before the parse, so
    # the swap to `listen for sh.X` happens here, line by line, like the wrapper removal above.
    _wrap_lines, _new_changes = new_to_listen(_wrap_lines)
    _wrap_changes = _wrap_changes + _new_changes
    # `idempotent by` is now `once per` (ruled 2026-10-28): refused before the parse, swapped here.
    from mohio_fmt import idempotent_by_to_once_per
    _wrap_lines, _once_changes = idempotent_by_to_once_per(_wrap_lines)
    _wrap_changes = _wrap_changes + _once_changes
    # A handler's request data is read by the shape's name exactly as declared (RULED 2026-10-09);
    # check refuses the retired lowercase-first name, so the rewrite happens here, before the parse.
    from mohio_fmt import shape_binding_names
    _wrap_lines, _bind_changes = shape_binding_names(_wrap_lines)
    _wrap_changes = _wrap_changes + _bind_changes
    # An inline `{{ each }}` view loop never ran and check refuses it; it splits into the
    # line-anchored form here, line by line, before the parse (RULED 2026-10-09).
    from mohio_fmt import inline_each_to_lines
    _wrap_lines, _each_changes = inline_each_to_lines(_wrap_lines)
    _wrap_changes = _wrap_changes + _each_changes
    # The ai.decide gate is `confidence above N` only (RULED 2026-10-10); check refuses the `check`
    # and `is` spellings, so they are rewritten here, before the parse.
    from mohio_fmt import confidence_gate_form
    _wrap_lines, _gate_changes = confidence_gate_form(_wrap_lines)
    _wrap_changes = _wrap_changes + _gate_changes
    # `+` beside text is `&` (RULED by Ron); check refuses the `+` spelling, so it is rewritten here.
    from mohio_fmt import plus_on_text
    _wrap_lines, _plus_changes = plus_on_text(_wrap_lines)
    _wrap_changes = _wrap_changes + _plus_changes
    src = "\n".join(_wrap_lines)
    from mohio_transformer_ast import transform
    # Pretokenize dotted user-var accesses (same as _parse_and_validate) so a file
    # using a type-word field like `x.text` formats instead of failing to parse.
    try:
        ast = transform(_fmt_parse(parser, src, path, pretokenized=True), src)
    except Exception as e:
        _die(f"Cannot format -- file does not parse:\n{e}", exit_code=1)
    out, changes = format_source(src, ast)
    changes = _wrap_changes + changes
    if _path_fixes:
        changes.append((0, "quoted path", f"{_path_fixes} unquoted (at \"/x\" -> at /x)"))

    if getattr(args, "stdout", False):
        # EGRESS-EXEMPT: program SOURCE text the developer asked to write; no runtime value
        sys.stdout.write(out)
        return

    # FMT NEVER CALLS A FILE CANONICAL WHILE CHECK REFUSES IT (RULED by Ron 2026-10-09). The formatted
    # text is checked the way `mio check` checks it, and what fmt could not convert is named.
    _refusals = _fmt_remaining_refusals(path, out)

    if not changes:
        if _refusals:
            print(f"mio fmt: {path} -- nothing fmt can convert, and mio check refuses it; these need a "
                  f"change by hand:")
            for _r in _refusals:
                print(f"  {_r}")
            sys.exit(1)
        print(f"mio fmt: {path} -- already canonical")
        return

    # THE HONEST COUNT is lines actually changed, not the number of edit-rules applied. One edit
    # like "remove the retired `listen for` wrapper" dedents every handler under it, so counting
    # edits reported "1 line normalized" against a whole-file rewrite -- the exact number a
    # developer reads to decide whether to review the diff. Compute it from the real before/after.
    import difflib as _difflib
    _before = _original_src.split("\n")
    _after = out.split("\n")
    _added = _removed = 0
    for _d in _difflib.unified_diff(_before, _after, lineterm=""):
        if _d.startswith('+') and not _d.startswith('+++'):
            _added += 1
        elif _d.startswith('-') and not _d.startswith('---'):
            _removed += 1
    _touched = _added + _removed
    _summary = (f"{_touched} line(s) changed ({_added} added, {_removed} removed), "
                f"{len(changes)} normalization(s)")

    if getattr(args, "write", False):
        # EGRESS-EXEMPT: program SOURCE text the developer asked to write; no runtime value
        path.write_text(out, encoding="utf-8", newline="\n")   # keep LF; write_text would CRLF on Windows
        print(f"mio fmt: {path} -- {_summary}")
    else:
        print(f"mio fmt: {path} -- {_summary} (run with --write to apply):")
    for ln, old, new in changes:
        print(f"  line {ln}: {old}  ->  {new}")
    if _refusals:
        print(f"mio fmt: mio check still refuses {path} after formatting; these need a change by hand:")
        for _r in _refusals:
            print(f"  {_r}")
        sys.exit(1)


def _fmt_parse(parser, text, path, pretokenized=False):
    """The parse tree fmt formats from, built the way `mio check` builds its own: marked against
    the whole app's declarations, and parsed in segments (mohio_segment) with check's own segment
    cache when the file has one, so a large app formats in about the time check takes rather than
    in three whole-file parses (Dispatch: about 1,120 seconds). Anything that does not parse in
    segments parses whole, as before, so an error reads exactly as it always has.

    `pretokenized` marks dotted user-variable reads first, as check does (a type-word field like
    `x.text` then parses). Without it the text parses as written, which a caller that splices the
    source by token position needs, since marking moves every position after it."""
    from mohio_segment import parse_segmented
    parse_src = text
    if pretokenized:
        try:
            from mohio_symbol_table import extract_symbols, app_mark_names
            from mohio_transformer import MOHIO_RESERVED_EXACT
            from mohio_pretokenizer import pretokenize
        except ImportError:
            pretokenize = None    # the same graceful degradation check has
        if pretokenize is not None:
            _names = extract_symbols(text).all_user_names()
            if path is not None:
                _names = _names | app_mark_names(str(path))
            parse_src = pretokenize(text, _names, MOHIO_RESERVED_EXACT)
    tree = parse_segmented(parser, parse_src, str(path) if path is not None else None,
                           _parse_fingerprint())
    return tree if tree is not None else parser.parse(parse_src)


def _fmt_remaining_refusals(path, text):
    """What `mio check` refuses in `text`, the formatted source of `path`, as one line per refusal.

    Checked exactly as a user's `mio check` would check it: the text is written beside the original,
    so a folder's journey still applies, under a hidden name that is removed afterwards, and `mio
    check` runs on it as its own process. Only the refusal lines (the `x` lines) are kept."""
    import shutil as _shutil
    import subprocess as _sp
    import uuid as _uuid
    _tag = _uuid.uuid4().hex[:8]
    probe_dir = None
    if path.name == "journey.mho":
        # A JOURNEY'S COPY MUST NOT SIT UNDER THE JOURNEY: beside it, the copy inherits the original
        # and every declaration loads twice. In a hidden folder beside the journey's own folder, named
        # journey.mho, it inherits exactly what the original inherits from the folders above.
        probe_dir = path.resolve().parent.parent / f".mio-fmt-check-{_tag}"
        probe_dir.mkdir()
        probe = probe_dir / "journey.mho"
    else:
        probe = path.parent / f".mio-fmt-check-{_tag}.mho"
    try:
        # EGRESS-EXEMPT: program SOURCE text, written beside itself for `mio check` and removed after
        probe.write_text(text, encoding="utf-8", newline="\n")
        # The original's segment cache goes with it: every segment fmt left alone is already parsed,
        # so the check of the formatted copy re-parses only what fmt changed. A segment is cached by
        # its own text, so a changed one is never mistaken for the old.
        from mohio_segment import _cache_path as _seg_cache
        _seg_src = Path(_seg_cache(str(path)))
        if _seg_src.exists():
            # EGRESS-EXEMPT: the parse cache, copied beside the check copy and removed after
            _shutil.copyfile(str(_seg_src), _seg_cache(str(probe)))
        # The probe by its ABSOLUTE path: run from its own folder, a relative path given on the command
        # line (`mio fmt examples/contact.mho`) named a file that is not there ("File not found").
        r = _sp.run([sys.executable, os.path.abspath(__file__), "check", str(probe.resolve())],
                    cwd=str(probe.parent),
                    capture_output=True, text=True, timeout=1800)
    finally:
        if probe_dir is not None:
            _shutil.rmtree(probe_dir, ignore_errors=True)
        # Check caches its parse beside the file, whole and in segments (mohio_segment).
        from mohio_segment import _cache_path as _segment_cache_path
        for _left in (probe, probe.with_name(probe.name + ".cache"), Path(_segment_cache_path(str(probe)))):
            if not _left.exists():
                continue
            try:
                _left.unlink()
            except OSError as _e:
                print(f"  [mio fmt] WARNING: could not remove the check copy {_left} ({_e}); "
                      f"delete it by hand.", file=sys.stderr)
    if r.returncode == 0:
        return []
    ansi = re.compile(r'\x1b\[[0-9;]*m')
    found, at = [], None
    for line in ((r.stdout or "") + "\n" + (r.stderr or "")).split("\n"):
        s = ansi.sub('', line).strip()
        _ln = re.match(r'^(\d+) \|', s)
        if _ln:
            at = _ln.group(1)           # check prints the source line above each finding
            continue
        if s.startswith("x ") and probe.name not in s:
            msg = s[2:].strip()
            found.append(msg if msg.startswith("Line ") or not at else f"Line {at}: {msg}")
        elif s.startswith(("Syntax error", "Error ")) and not found:
            found.append(s.replace(str(probe), path.name).replace(probe.name, path.name))
    return found or [f"mio check exits {r.returncode} on this file; run `mio check {path}` for the detail."]


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
    # LINE-BUFFERED, for the same reason, decided once here: a served 500 tells the client only its
    # reference, so the cause has to be in the log, under that reference, by the time the response
    # leaves. Written to a file or a pipe, Python buffers output in blocks, so the cause sat in
    # memory while the 500 went out and was lost when the process stopped (measured 2026-10-10: an
    # Oracle upsert failure and a runaway task each answered a reference the log never showed).
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
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

    # HELP IS INTERCEPTED HERE, because every parser is add_help=False (so argparse would otherwise
    # answer `--help` with "unrecognized arguments: --help"). `mio --help` / `mio -h` -> the overview;
    # `mio <cmd> --help` / `mio help <cmd>` -> that command's own options and example.
    _argv = sys.argv[1:]
    _known = set(_registered_commands())
    if "--help" in _argv or "-h" in _argv:
        _cmd = next((a for a in _argv if a in _known), None)
        if _cmd and _cmd != "help":
            _print_command_help(_cmd)
        else:
            cmd_help(argparse.Namespace(help_topic=None))
        return
    if len(_argv) >= 2 and _argv[0] == "help" and _argv[1] in _known:
        _print_command_help(_argv[1])
        return

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
        "create":  cmd_create,
        "new":     cmd_new,
        "init":    cmd_init,
        "serve":   cmd_serve,
        "check":   cmd_check,
        "test":    cmd_test,
        "warmup":  cmd_warmup,
        "translate": cmd_translate,
        "generate":  cmd_generate,
        "schema":        cmd_schema,
        "migrate":       cmd_migrate,
        "schedule":      cmd_schedule,
        "audit":         cmd_audit,
        "sector":        cmd_sector,
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
