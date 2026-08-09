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
    
    # Built-in namespaces (always populated from MOHIO_RESERVED_EXACT)
    builtins:   Set[str] = field(default_factory=set)
    
    # Warnings generated during pre-pass
    warnings:   list = field(default_factory=list)
    
    def __post_init__(self):
        self.builtins = set(MOHIO_RESERVED_EXACT)
    
    def all_user_names(self) -> Set[str]:
        return self.variables | self.tasks | self.shapes | self.journeys | self.connects
    
    def is_builtin(self, name: str) -> bool:
        return name.lower() in self.builtins
    
    def is_user_symbol(self, name: str) -> bool:
        return name.lower() in {n.lower() for n in self.all_user_names()}
    
    def resolve_dotted(self, left: str, right: str) -> str:
        """
        Determine what kind of dotted expression left.right is.
        Returns: 'builtin', 'field_access', 'unknown'
        """
        left_lower = left.lower()
        if left_lower in self.builtins:
            return 'builtin'
        if self.is_user_symbol(left):
            return 'field_access'
        # Runtime variables (session, request, env at runtime) 
        runtime_shapes = {'session', 'request', 'save', 'result', 
                         'member', 'user', 'item', 'room', 'player'}
        if left_lower in runtime_shapes:
            return 'field_access'
        return 'unknown'


# -- Patterns for pre-pass extraction ----------------------------------

_HOLD_PATTERN    = re.compile(r'^\s*hold\s+([a-zA-Z_][a-zA-Z0-9_]*)\b', re.MULTILINE)
_SET_PATTERN     = re.compile(r'^\s*set\s+([a-zA-Z_][a-zA-Z0-9_]*)\b', re.MULTILINE)
_TASK_PATTERN    = re.compile(r'^\s*task\s+([a-zA-Z_][a-zA-Z0-9_]*)\b', re.MULTILINE)
_SHAPE_PATTERN   = re.compile(r'^\s*shape\s+([a-zA-Z][a-zA-Z0-9_]*)\b', re.MULTILINE)
_JOURNEY_PATTERN = re.compile(r'^\s*journey\s+([a-zA-Z_][a-zA-Z0-9_]*)\b', re.MULTILINE)
_CONNECT_PATTERN = re.compile(
    r'^\s*connect\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+as\b', re.MULTILINE)
_MIOCONNECT_PATTERN = re.compile(
    r'^\s*mioconnect\s+([a-zA-Z_][a-zA-Z0-9_]*)(?:\s+as\s+([a-zA-Z_][a-zA-Z0-9_]*))?', re.MULTILINE)
_ASSIGN_PATTERN  = re.compile(
    r'^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s+(?!:)[\w\"\'\(]', re.MULTILINE)

# Lines to skip (comments, keywords that aren't assignments)
_SKIP_WORDS = {
    'sector', 'compliance', 'listen', 'when', 'otherwise', 'check',
    'if', 'else', 'repeat', 'while', 'each', 'try', 'catch',
    'give', 'find', 'save', 'update', 'remove', 'upsert',
    'retrieve', 'redirect', 'forward', 'include', 'use',
    'run', 'wait', 'lock', 'unlock', 'require', 'new',
    'ai', 'sh', 'env', 'mio',
}


def extract_symbols(source: str) -> SymbolTable:
    """
    Fast O(n) pre-pass to extract all declared symbols from source.
    Does NOT do full parsing -- just pattern matching on declarations.
    Runs in milliseconds even on large files.
    """
    st = SymbolTable()
    
    # Remove comments
    clean = re.sub(r'//[^\n]*', '', source)
    
    # Extract each declaration type
    for m in _HOLD_PATTERN.finditer(clean):
        name = m.group(1)
        if name.lower() not in _SKIP_WORDS:
            st.variables.add(name)
            if name.lower() in MOHIO_RESERVED_EXACT:
                st.warnings.append(f"Reserved word '{name}' used as variable name")
    
    for m in _SET_PATTERN.finditer(clean):
        name = m.group(1)
        if name.lower() not in _SKIP_WORDS:
            st.variables.add(name)
            if name.lower() in MOHIO_RESERVED_EXACT:
                st.warnings.append(f"Reserved word '{name}' used as variable name")
    
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
