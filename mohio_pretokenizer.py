# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
mohio_pretokenizer.py -- Pre-tokenizer for Mohio source code

Runs between symbol table extraction and Lark parsing.
Marks user variable dotted accesses so Earley never branches on them.

Performance impact:
    Before: O(n) parse time per dotted expression (linear but slow)
    After:  O(1) per marked dotted expression (constant, no branching)
    
    100 dotted expressions: 5.4s -> ~0.2s
    Zork (200 dotted expressions): ~10s -> ~0.5s
"""

import re
from typing import Optional, Set

# Marker prefix -- chosen to be invalid Mohio syntax so no collision
USERVAR_MARKER = "__USERVAR__"

# Pattern: word followed by dot followed by word (and optionally more)
# Captures: group(1) = left side, group(2) = rest
_DOTTED_PATTERN = re.compile(
    # A segment is a word OR a bare number, so `colors.position.2` / `colors.pos.2` keep the
    # numeric index instead of dropping it (which made numeric position a silent no-op on plain
    # lists). Word segments still start with a letter/underscore; number segments are all digits.
    r'(?<!["\'\w])([a-z_][a-zA-Z0-9_]*)((?:\.(?:[a-zA-Z_][a-zA-Z0-9_]*|[0-9]+))+)'
)

# Runtime variable shapes -- always field access even if not explicitly declared
_RUNTIME_SHAPES = {
    'session', 'request', 'response', 'member', 'user',
    'item', 'room', 'player', 'transaction', 'record',
    'result', 'data', 'event', 'ctx', 'config',
}


def pretokenize(source: str, user_symbols: Set[str], 
                builtin_names: Set[str]) -> str:
    """
    Mark user variable dotted accesses in source before Lark parses it.
    
    "session.id"     -> "__USERVAR__session.id"     (user var)
    "miocookie.set"  -> "miocookie.set"              (builtin, unchanged)
    "player.score"   -> "__USERVAR__player.score"    (user var)
    "ai.decide"      -> "ai.decide"                  (builtin, unchanged)
    
    Lark sees USERVAR_DOTTED terminal for user vars -- no branching.
    Built-in terminals match as before -- no change.
    """
    all_user = {s.lower() for s in user_symbols} | _RUNTIME_SHAPES
    all_builtins = {b.lower() for b in builtin_names}
    
    # Remove comments before processing (don't mark inside comments)
    lines = []
    for line in source.split('\n'):
        comment_pos = line.find('/' + '/')
        if comment_pos >= 0:
            code_part = line[:comment_pos]
            comment_part = line[comment_pos:]
            lines.append(_mark_line(code_part, all_user, all_builtins) + comment_part)
        else:
            lines.append(_mark_line(line, all_user, all_builtins))
    
    return '\n'.join(lines)


def _mark_line(line: str, all_user: Set[str], all_builtins: Set[str]) -> str:
    """Mark user variable dotted accesses in a single line."""
    # Determine if this line starts with a dotted name (LHS position)
    # LHS dotted names (field assignments in upsert/save/update) must NOT be marked
    # Only RHS dotted names (values) should be marked
    stripped = line.lstrip()
    first_token_match = _DOTTED_PATTERN.match(stripped)
    lhs_end = 0
    if first_token_match:
        # This dotted name is at line start -- it's an LHS field name
        # Don't mark it. Record where it ends so we don't re-process it.
        lhs_end = len(line) - len(stripped) + first_token_match.end()
    
    result = []
    i = 0
    in_string = False
    string_char = None
    
    while i < len(line):
        char = line[i]
        
        # Skip LHS dotted name -- copy verbatim
        if i < lhs_end:
            result.append(char)
            i += 1
            continue
        
        # Track string boundaries
        if not in_string and char in ('"', "'"):
            in_string = True
            string_char = char
            result.append(char)
            i += 1
            continue
        
        if in_string:
            if char == string_char:
                in_string = False
            result.append(char)
            i += 1
            continue
        
        # Check for dotted expression starting here (RHS position)
        m = _DOTTED_PATTERN.match(line, i)
        if m:
            left = m.group(1).lower()
            rest = m.group(2)
            
            if left in all_user and left not in all_builtins:
                # User variable in RHS position -- mark it
                result.append(USERVAR_MARKER + m.group(1) + rest)
                i = m.end()
                continue
        
        result.append(char)
        i += 1
    
    return ''.join(result)


def unmark(token_value: str) -> str:
    """Remove USERVAR_MARKER prefix from a token value."""
    if token_value.startswith(USERVAR_MARKER):
        return token_value[len(USERVAR_MARKER):]
    return token_value


def unmark_dotted(token_value: str):
    """
    Convert a marked token back to (left, right) parts.
    "__USERVAR__session.id" -> ("session", ["id"])
    """
    clean = unmark(token_value)
    parts = clean.split('.')
    return parts[0], parts[1:]
