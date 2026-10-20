# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
mohio_symbol_table.py -- Pre-pass symbol table for Mohio compiler

Collects all declared names BEFORE Earley parsing so the lexer
can distinguish user variables from built-in namespaces without
ambiguity. This eliminates the need for terminal priority hacks
and makes dotted name resolution deterministic.

Flow:
    source -> extract_symbols() -> SymbolTable
                                      ?
    source -> Lark(earley) with symbol context -> Tree -> AST

Performance impact:
    - Pre-pass is O(n) regex scan -- near instant
    - Earley gets unambiguous token stream -- no exponential branching
    - Parse time drops ~80% for files with many dotted names
"""

import re
from dataclasses import dataclass, field
from typing import Set, Dict, Optional
from mohio_transformer import MOHIO_RESERVED_EXACT


@dataclass
class SymbolTable:
    """All declared names in a Mohio source file."""
    
    # User-declared names (set, hold, task, shape, connect, journey)
    variables:  Set[str] = field(default_factory=set)
    tasks:      Set[str] = field(default_factory=set)
    shapes:     Set[str] = field(default_factory=set)
    journeys:   Set[str] = field(default_factory=set)
    connects:   Set[str] = field(default_factory=set)
    
    # Warnings generated during pre-pass
    warnings:   list = field(default_factory=list)

    def all_user_names(self) -> Set[str]:
        return self.variables | self.tasks | self.shapes | self.journeys | self.connects

    # REMOVED 2026-09-01: `builtins`, `__post_init__`, `is_builtin`, `is_user_symbol`.
    # The two methods had NO caller anywhere in the tree, tests included, and `builtins`
    # existed only to feed `is_builtin` -- so `__post_init__` copied the whole
    # MOHIO_RESERVED_EXACT set on EVERY parse to populate a field nothing ever read. The
    # three places that genuinely need the reserved set (`extract_symbols`'s warning check
    # and `check_reserved_violations`) read MOHIO_RESERVED_EXACT directly.


# -- Patterns for pre-pass extraction ----------------------------------

_HOLD_PATTERN    = re.compile(r'^\s*hold\s+([a-zA-Z_][a-zA-Z0-9_]*)\b', re.MULTILINE)
# NOTE (2026-09-01): there is deliberately no `set` pattern here. `set` is RETIRED and
# hard-errors -- verified by running, not assumed: `set x 5` -> "`set` is retired. Write the
# declaration directly". So a file containing `set x 5` is REJECTED, and registering `x` from
# it could only ever help a program that cannot compile. The retirement is pinned by
# tests/test_unknown_types.py and tests/test_drift_loud_rules.py, so if `set` is ever brought
# back those go red first, and this note points at what was removed on that assumption.
_TASK_PATTERN    = re.compile(r'^\s*task\s+([a-zA-Z_][a-zA-Z0-9_]*)\b', re.MULTILINE)
_SHAPE_PATTERN   = re.compile(r'^\s*shape\s+([a-zA-Z][a-zA-Z0-9_]*)\b', re.MULTILINE)
_JOURNEY_PATTERN = re.compile(r'^\s*journey\s+([a-zA-Z_][a-zA-Z0-9_]*)\b', re.MULTILINE)
_CONNECT_PATTERN = re.compile(
    r'^\s*connect\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+as\b', re.MULTILINE)
_MIOCONNECT_PATTERN = re.compile(
    r'^\s*mioconnect\s+([a-zA-Z_][a-zA-Z0-9_]*)(?:\s+as\s+([a-zA-Z_][a-zA-Z0-9_]*))?', re.MULTILINE)
_ASSIGN_PATTERN  = re.compile(
    r'^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s+(?!:)[\w\"\'\(]', re.MULTILINE)
# A loop VARIABLE is a user name too. `repeat` and `each` are both skip-words, so the assignment
# pattern above never looked at this line and the loop variable was invisible to the symbol table.
# That matters because the pretokenizer is what lets a dotted access carry a type-word FIELD
# (`e.map`, `row.text`): it can only mark `e.map` if it knows `e` is a user name. Without this,
# any type-word field on a loop variable silently resolved to the whole owner instead of the
# field -- found via the flow/walk report's own `e.map`.
_LOOP_VAR_PATTERN = re.compile(
    r'^\s*(?:repeat\s+)?each\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+in\b', re.MULTILINE)

# The name a QUERY binds its result to: `retrieve r from db.x`, `find rows in db.x`,
# `grab one from db.x`, `pull p up to 5 from db.x`.
#
# Q59-sibling (2026-09-01). These were missing, and the consequence was worse than "the name
# is unknown". The pretokenizer marks a dotted chain by its ROOT, so with `r` unknown it
# matched from the MIDDLE instead: `show r.phone.length` became
# `show r.__USERVAR__phone.length` -- a mangled token that then failed to parse with the
# zero-information "Unexpected end-of-input. Expected one of:" (no line, no caret). A
# developer writing the natural `r.phone.length` got an error naming nothing at all.
#
# Same class as the loop-variable pattern directly above, added for the same reason: a
# binding form the symbol table did not know about.
_QUERY_ALIAS_PATTERN = re.compile(
    r'^\s*(?:retrieve(?:\.\w+)?|find(?:\.\w+)?|grab|pull)\s+'
    r'([a-zA-Z_][a-zA-Z0-9_]*)\b', re.MULTILINE)

# A RESULT captured with `as NAME`: a connector/service call (`Svc.op with body as res`,
# `Svc.op as res`), an HTTP call (`miohttp.get URL as res`), a task call (`call t as res`),
# `apply`, `run async`, a query rename (`retrieve r as renamed`), `save ... as rec`,
# `check ... as grade`, `check count ... as n`, `ai.create ... as art`, and the rows-affected
# capture on `update`/`remove ... as changed`. The name after a bare `as` is a NEW variable the
# program reads, often dotted -- so the pretokenizer must know it, exactly as it must know a query
# subject or a loop variable. This one binding position was captured only for four query verbs
# (above), so every OTHER `as NAME` form (the connector call among them -- Ron's ruling) went
# unregistered: `Svc.call as jev_result` then `jev_result.status` marked from the middle and failed
# to parse, while `result` worked only because it is a runtime shape, not because the alias was
# understood.
#
# The word after `as` is a NEW NAME only when it is NOT a type or a connection driver: `n as int`,
# `amt as BHD`, `card as text` and `connect db as sqlite` name the variable BEFORE `as` and put a
# TYPE or a driver after it. Those are excluded below so a type word is never registered as a
# variable. The dotted cast `as.int` is not matched at all (it has no space after `as`).
_RESULT_ALIAS_PATTERN = re.compile(r'(?<![.\w])as\s+([a-zA-Z_][a-zA-Z0-9_]*)', re.MULTILINE)

# Words that follow `as` in an ANNOTATION or a CONNECTION, never as a captured variable.
_NON_ALIAS_AFTER_AS = {
    # scalar / structured types
    'text', 'string', 'int', 'integer', 'dec', 'decimal', 'number', 'num',
    'bool', 'boolean', 'date', 'datetime', 'time', 'uuid', 'email', 'url',
    'json', 'list', 'map', 'any', 'void', 'base64', 'image', 'audio', 'video',
    'pdf', 'file', 'table',
    # currency types
    'usd', 'eur', 'gbp', 'jpy', 'chf', 'cad', 'aud', 'cny', 'hkd', 'sgd', 'inr',
    'nzd', 'sek', 'nok', 'mxn', 'brl', 'zar', 'krw',
    'bhd', 'kwd', 'omr', 'jod', 'tnd', 'iqd', 'lyd',
    # connection drivers (`connect db as sqlite`) -- the connection name is captured elsewhere
    'sqlite', 'postgres', 'postgresql', 'mysql', 'mariadb', 'mongo', 'mongodb',
}

# Lines to skip (comments, keywords that aren't assignments)
_SKIP_WORDS = {
    'sector', 'compliance', 'listen', 'when', 'otherwise', 'check',
    'if', 'else', 'repeat', 'while', 'each', 'try', 'catch',
    'give', 'find', 'save', 'update', 'remove', 'upsert',
    'retrieve', 'redirect', 'forward', 'include', 'use',
    'run', 'wait', 'lock', 'unlock', 'require', 'new',
    'ai', 'sh', 'env', 'mio',
}


def _strip_line_comments(source: str) -> str:
    """Drop `//` line comments without touching a `//` inside a double-quoted string.
    A per-line string toggle is enough for this fast pre-pass: Mohio has only `"..."` strings, and
    the goal is to not lose a URL's tail (and any alias after it), not to fully tokenize."""
    out = []
    for line in source.split('\n'):
        in_str = False
        cut = len(line)
        i = 0
        n = len(line)
        while i < n:
            c = line[i]
            if c == '"':
                in_str = not in_str
            elif c == '/' and not in_str and i + 1 < n and line[i + 1] == '/':
                cut = i
                break
            i += 1
        out.append(line[:cut])
    return '\n'.join(out)


def extract_symbols(source: str) -> SymbolTable:
    """
    Fast O(n) pre-pass to extract all declared symbols from source.
    Does NOT do full parsing -- just pattern matching on declarations.
    Runs in milliseconds even on large files.
    """
    st = SymbolTable()

    # Remove line comments, but NOT a `//` inside a string. The naive `re.sub(r'//[^\n]*', '')`
    # ate the `//` in a URL (`miohttp.get "https://api/x" as res`) and everything after it on the
    # line, so an alias written after a URL on the same line was deleted before it could be seen --
    # the miohttp sibling of the connector-alias miss. Mohio strings are double-quoted only.
    clean = _strip_line_comments(source)
    
    # Extract each declaration type
    for m in _HOLD_PATTERN.finditer(clean):
        name = m.group(1)
        if name.lower() not in _SKIP_WORDS:
            st.variables.add(name)
            if name.lower() in MOHIO_RESERVED_EXACT:
                st.warnings.append(f"Reserved word '{name}' used as variable name")
    
    for m in _LOOP_VAR_PATTERN.finditer(clean):
        name = m.group(1)
        if name.lower() not in _SKIP_WORDS:
            st.variables.add(name)

    for m in _QUERY_ALIAS_PATTERN.finditer(clean):
        name = m.group(1)
        if name.lower() not in _SKIP_WORDS:
            st.variables.add(name)

    # Every `as NAME` result capture (connector call, miohttp, call, apply, run async, query
    # rename, save/update/remove, check, check count, ai.create ...), so a dotted read of the
    # alias is marked. A type or driver word after `as` is an annotation, not a capture.
    for m in _RESULT_ALIAS_PATTERN.finditer(clean):
        name = m.group(1)
        low = name.lower()
        if low not in _SKIP_WORDS and low not in _NON_ALIAS_AFTER_AS:
            st.variables.add(name)

    for m in _TASK_PATTERN.finditer(clean):
        st.tasks.add(m.group(1))
    
    for m in _SHAPE_PATTERN.finditer(clean):
        st.shapes.add(m.group(1))
    
    for m in _JOURNEY_PATTERN.finditer(clean):
        st.journeys.add(m.group(1))
    
    for m in _CONNECT_PATTERN.finditer(clean):
        st.connects.add(m.group(1))

    for m in _MIOCONNECT_PATTERN.finditer(clean):
        st.connects.add(m.group(1))          # connector name (used as Name.operation)
        if m.group(2):
            st.connects.add(m.group(2))      # alias
    
    # Bare assignments (name value without set/hold keyword).
    # A reserved keyword in first position is a statement (mioconnect Stripe,
    # render ..., listen for ...), never a user variable -- never extract it.
    for m in _ASSIGN_PATTERN.finditer(clean):
        name = m.group(1)
        if (name.lower() not in _SKIP_WORDS
                and name.lower() not in MOHIO_RESERVED_EXACT
                and name not in st.all_user_names()):
            st.variables.add(name)
    
    return st


# -- Whole-app declaration set (the global symbol index) -------------------
#
# The single-file `extract_symbols` above sees ONLY the string handed to it: it cannot see a name
# declared in an included file or the journey spine. When the page then references that name in a
# DOTTED position (`settings.timeout`), the pretokenizer -- which only marks a dotted access whose
# root it knows -- leaves it unmarked, and Earley must branch on it. Feeding the pretokenizer the
# WHOLE-APP declaration set (page + its includes + journey) removes that branching. It NEVER changes
# the resulting tree: marking only picks the USERVAR_DOTTED terminal instead of making Earley explore
# groupings, so the AST is identical either way (proven by the hostile pass and the file-by-file
# equivalence check). This is the ONE implementation of the app-set logic; `tools/symbol_index.py`
# calls it, never a copy.

_INCLUDE_RE = re.compile(r'^\s*include\s+"([^"]+)"', re.MULTILINE)


def discover_app_files(page_path) -> list:
    """The page, its DIRECT includes (regex-found, never parsed), and journey.mho in the app dir --
    in file order, each existing path once. Comments are stripped before the include scan so a
    commented-out `include` is not counted. This is the file set whose declarations the pretokenizer
    must see to mark the page's names; it does not compose or parse anything."""
    import os
    page_path = os.path.abspath(page_path)
    base = os.path.dirname(page_path)
    files = [page_path]
    seen = {page_path}
    try:
        src = open(page_path, encoding='utf-8-sig').read()
    except OSError:
        return files
    clean = re.sub(r'//[^\n]*', '', src)
    for m in _INCLUDE_RE.finditer(clean):
        inc = os.path.join(base, m.group(1))
        if os.path.isfile(inc) and inc not in seen:
            files.append(inc)
            seen.add(inc)
    journey = os.path.join(base, 'journey.mho')
    if os.path.isfile(journey) and journey not in seen and journey != page_path:
        files.append(journey)
    return files


def _iter_app_symbol_tables(page_path):
    """Yield the SymbolTable of each app file (page, includes, journey). The file reads live HERE, in
    one place, so the best-effort skip of an unreadable file exists once rather than at every caller.

    The skip is genuinely correct, not a swallowed error: a file `discover_app_files` returned but
    that cannot be read now can only mean a delete/permission race in the moment between its `isfile`
    check and here. Marking is an OPTIMIZATION -- a missed name only costs a missed mark, never a
    wrong parse (the tree is identical with or without it) -- and a genuinely bad include/journey path
    is reported LOUD, at the right place, by `_resolve_includes` when the app is actually assembled.
    So skipping here loses nothing and hides nothing."""
    for f in discover_app_files(page_path):
        try:
            text = open(f, encoding='utf-8-sig').read()
        except OSError:
            continue   # best-effort marking scan: unreadable app file skipped; bad paths fail loud in _resolve_includes
        yield extract_symbols(text)


def app_user_names(page_path) -> Set[str]:
    """Union of ALL declared user names across the page, its includes and its journey (variables,
    tasks, shapes, journeys AND connects). This is the reporting view (`tools/symbol_index.py`); for
    what the compiler feeds the pretokenizer see `app_mark_names`, which excludes connects. A page
    with no includes and no journey returns exactly `extract_symbols(page).all_user_names()`."""
    names: Set[str] = set()
    for st in _iter_app_symbol_tables(page_path):
        names |= st.all_user_names()
    return names


def app_mark_names(page_path) -> Set[str]:
    """The cross-file names the pretokenizer may SAFELY mark from other files in the app: variables,
    tasks, shapes and journeys -- but NOT connect names.

    Why connects are excluded. A connect root has a dedicated dotted grammar: unmarked, `db.orders`
    parses as a DbRef (a database table); marked, it becomes a plain DottedName. Within one file this
    already happens (the file's own `connect db` is in its single-file table, `db.orders` is marked,
    and the interpreter treats DbRef and a db-rooted DottedName identically). But a `connect db` that
    lives in an INCLUDE must not change how the PAGE parses `db.orders`: doing so flips the page's AST
    (DbRef -> DottedName) versus today for zero runtime gain, and the point of this index is a faster
    parse with an IDENTICAL tree. So the cross-file mark set omits connects; the page's own connect
    names still come from its single-file symbol table, so in-file behaviour is unchanged. Measured on
    Stage 10: without this, `index.mho`'s `db.<table>` sources reparse DbRef -> DottedName because the
    journey/include's `connect db` enters the set.

    A page with no includes and no journey adds nothing here (its own names already cover it)."""
    names: Set[str] = set()
    for st in _iter_app_symbol_tables(page_path):
        names |= st.variables | st.tasks | st.shapes | st.journeys   # deliberately not st.connects
    return names


def check_reserved_violations(st: SymbolTable) -> list:
    """
    Return list of (name, context, what_its_reserved_for) tuples
    for any reserved words used as user symbols.
    """
    from mohio_transformer import MOHIO_RESERVED_WHAT
    violations = []
    for name in st.all_user_names():
        if name.lower() in MOHIO_RESERVED_EXACT:
            what = MOHIO_RESERVED_WHAT.get(name.lower(), "a Mohio built-in")
            violations.append((name, what))
    return violations
