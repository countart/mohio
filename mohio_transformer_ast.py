# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
mohio_transformer.py
Mohio Language -- Parse Tree -> AST Transformer
Version: 0.1.0 | April 2026 | Particular LLC

Walks the Lark parse tree produced by mohio.lark and emits
AST nodes defined in mohio_ast.py.

Three things happen here that can't happen in the grammar:
  1. Closer validation -- strict, Option A. blockname: done must
     match the block it closes. Mismatch = MohioCloserError.
  2. Zone ordering -- Declarations / Logic / Output. Validated
     at the program level. Violation = MohioZoneError.
  3. Type coercion -- lexer tokens become Python values.
     "0.85" -> float, "true" -> True, NUMBER -> int or float.
"""

from __future__ import annotations


# ============================================================================
# RULE CHANGES REQUIRE RONNIE'S APPROVAL. NO EXCEPTIONS.
#
# Enforcement rules and grammar are the language. A wrong rule does not fail
# loud - it BECOMES the truth and everything drifts to match it.
#
# Before you add, change, or retire a rule you must CITE a source: Ronnie's
# explicit ruling, a design decision found via conversation_search, or a working
# .mho in the repo that you RAN. If you cannot cite one, you are writing from
# memory. Stop, say "unverified", and ask Ronnie.
#
# Go through the door: mohio_enforce.enforce(). See TESTING.md and DRIFT.md.
# ============================================================================

# ================================================================================
#   DO NOT CALL THIS FILE DIRECTLY. GO THROUGH THE DOOR: mohio_enforce.enforce()
# ================================================================================
#   This file is transform()
#   LAYER 2 of 3 -- BUILDING THE AST.
#
#   Rules that can only be seen while assembling a construct (a closer that names a result).
#
#   Mohio enforces rules in THREE layers. They cannot be merged -- each needs data the others
#   do not have. But there is exactly ONE DOOR into them:
#
#       from mohio_enforce import enforce
#       ctx, program = enforce(tree, source=src)     # runs ALL THREE, returns every error
#
#   WHY THIS EXISTS: `mio check` ran all three layers. The GATE ran only validate(). So the gate
#   -- the thing we treat as sacred -- was blind to 25 transformer guards and 7 scanners. Real
#   bugs lived in main for months (two `list` fields in one shape; `get ... from cache.settings`;
#   a gate test asserting a RETIRED ai.connect form). The first run through the single door found
#   all three. That was not a bug in any layer. It was a bug in having three front doors.
#
#   IF YOU ARE WRITING A TEST (unit, regression, gate, or in another chat):
#       Call enforce(), or shell out to `mio check`. Never import a single layer and call it --
#       you will be testing a third of the compiler and believing it is the whole thing.
#
#   IF YOU ARE ADDING A RULE:
#       Put it in the layer that has the data you need (see the three above), then make sure a
#       test drives it through enforce(). A rule only one layer knows about is a rule that drifts.
# ================================================================================

import sys
from lark import Transformer, Token, Tree, v_args
from lark.exceptions import VisitError

from mohio_ast import (
    Program, Node,
    ApplangBlock,
    MatchBlock, MatchAnyBlock, NoMatchBlock, MatchPair,
    ViewCallStmt, ViewRender, RespondAsStmt, TitleDecl, DescribeDecl,
    DebugDecl, DebugLogStmt, DebugCheckpoint,
    # Declarations
    SectorDecl, ConnectDecl, ShapeDecl, ShapeField, ShapeFieldModifier,
    TaskDecl, TaskParam, HoldDecl, LockDecl, ReleaseStmt, VarStateStmt, ComplianceDecl, SecurityDecl,
    JourneyDecl, JourneyMeta, PageDecl, SagaDecl, StepBlock, MioconnectDecl, IncludeDecl, RequireRoleDecl, GrantRoleDecl,
    RateLimitDecl, TimespanDecl, TimespanAnchor, TimespanPrecision,
    TimespanTimezone, TimespanRecurring, TimespanExclude,
    # Closers
    Closer, PurposeBlock,
    # Routing blocks
    ListenBlock, NewBlock, RequestBlock, ChangeBlock,
    ConnectionBlock, WhileActiveBlock, OnOpen, OnClose,
    # Flow control
    OrIfClause, OtherwiseClause,
    CheckBlock, CheckWhen,
    EachBlock, RepeatBlock, WhileBlock, SectionBlock,
    # Data operations
    RetrieveBlock, FindBlock, SaveBlock, UpdateBlock, RemoveBlock,
    FieldValue, DynamicFieldValue, MatchClause, WhereClause, AndClause, ReturnClause,
    OrderClause, LimitClause, CacheClause,
    # Result handlers
    OnFailure, OnSuccess, OnError,
    # AI
    AiDecideBlock, AiDecideInvoke, ConfidenceCheck, UsingChain, WeighClause,
    AiRankBlock, RankOption,
    NotConfidentBlock, AiAuditStmt, AiExplainStmt, AiExplainBlock,
    AiCreateStmt, AiOverrideStmt, RunBlock,
    # Try/catch
    TryBlock, CatchClause, AlwaysClause,
    TransactionBlock,
    # Actions
    GiveBackStmt, JumpToStmt, HaltStmt, StopStmt, SkipStmt,
    ShowStmt, RaiseStmt, SendStmt, BroadcastStmt, StreamStmt,
    NotifyStmt, ServiceCallStmt,
    # Assignment
    Assignment,
    # Conditions
    Condition, NotCondition, AndCondition, OrCondition, DotStateCheck,
    # Values
    Literal, DottedName, EnvRef, SecretRef, DbRef, ShRef,
    FuncCall, MathExpr, TemplateString, ListLiteral, MapLiteral,
    # Time
    TimeExpr, DatetimeExpr, DurationExpr, SinceExpr,
    # Design literals (v3.8) -- for mioimage and ai.generate. NEVER IMPORTED, so even
    # though the grammar defines color_lit/percent_lit/dimension_lit and the interpreter
    # has evaluators, nothing could build the nodes and every literal resolved to None.
    ColorLit, PercentLit, DimensionLit,
    # uuid() -- NEVER IMPORTED, which is precisely why nothing could ever build one and
    # `uuid()` silently fell through to TimeExpr and returned the current timestamp.
    UuidCall,
)


# --------------------------------------------------------------
# ERRORS
# --------------------------------------------------------------

class MohioError(Exception):
    """Base for all Mohio compile-time errors."""
    pass


class MohioCloserError(MohioError):
    """Raised when a named closer doesn't match its opening block."""

    def __init__(self, expected: str, found: str, open_line: int, close_line: int):
        self.expected = expected
        self.found = found
        self.open_line = open_line
        self.close_line = close_line

    def __str__(self):
        return (
            f"\nLine {self.close_line} -- closer mismatch.\n"
            f"Expected: {self.expected}: done\n"
            f"Found:     {self.found}: done\n"
            f"\nThe {self.expected} block opened on line {self.open_line} is not closed.\n"
            f"Add '{self.expected}: done' before '{self.found}: done'."
        )


class MohioZoneError(MohioError):
    """Raised when a statement appears in the wrong zone."""

    def __init__(self, stmt_type: str, found_zone: str, expected_zone: str, line: int):
        self.stmt_type = stmt_type
        self.found_zone = found_zone
        self.expected_zone = expected_zone
        self.line = line

    def __str__(self):
        return (
            f"\nLine {self.line} -- zone ordering violation.\n"
            f"'{self.stmt_type}' is a {self.expected_zone} statement.\n"
            f"Found in {self.found_zone} zone.\n"
            f"\nMohio files are structured: Declarations / Logic / Output.\n"
            f"Move this statement to the {self.expected_zone} zone."
        )


class MohioCompileError(MohioError):
    """General compile-time error."""

    def __init__(self, message: str, line: int = 0):
        self.message = message
        self.line = line

    def __str__(self):
        prefix = f"\nLine {self.line} -- " if self.line else "\n"
        return f"{prefix}{self.message}"


# --------------------------------------------------------------
# CLOSER STACK
# Tracks open named blocks and validates closer names match.
# --------------------------------------------------------------

class CloserStack:
    """
    Tracks open named blocks and validates that closer names match.

    Usage:
        stack.push("ai.decide", line=18)
        ...
        stack.pop("retrieve", close_line=24)  # raises MohioCloserError

    Blocks that use optional closers (if, each, repeat, while, check)
    are NOT pushed -- they dedent-close, not name-close.
    """

    OPTIONAL_CLOSER_BLOCKS = {
        'each_block', 'repeat_block', 'while_block',
        'check_block', 'or_if_clause', 'otherwise_clause', 'when_clause',
    }

    def __init__(self):
        self._stack: list[tuple[str, int]] = []  # (block_name, open_line)

    def push(self, block_name: str, line: int = 0):
        self._stack.append((block_name, line))

    def pop(self, closer_name: str, close_line: int = 0) -> str:
        """
        Validate closer_name matches the top of the stack.
        Returns the block_name on success.
        Raises MohioCloserError on mismatch.
        Raises MohioCompileError if stack is empty.
        """
        if not self._stack:
            # Bare 'done' with empty stack -- tolerate (forgiving parser)
            if closer_name is None:
                return None
            raise MohioCompileError(
                f"'{closer_name}: done' found but no open block to close.",
                line=close_line
            )

        expected_name, open_line = self._stack[-1]

        if closer_name is None:
            # Bare 'done' -- pop whatever is on top
            self._stack.pop()
            return expected_name

        if closer_name != expected_name:
            raise MohioCloserError(
                expected=expected_name,
                found=closer_name,
                open_line=open_line,
                close_line=close_line,
            )

        self._stack.pop()
        return expected_name

    def is_empty(self) -> bool:
        return len(self._stack) == 0

    def peek(self) -> tuple[str, int] | None:
        return self._stack[-1] if self._stack else None


# --------------------------------------------------------------
# HELPERS
# --------------------------------------------------------------

def _line(tree_or_token) -> int:
    """Extract line number from a Lark tree or token, return 0 if unavailable."""
    if isinstance(tree_or_token, Token):
        return tree_or_token.line or 0
    if hasattr(tree_or_token, 'meta') and hasattr(tree_or_token.meta, 'line'):
        return tree_or_token.meta.line or 0
    return 0


def _unquote(s: str) -> str:
    """Strip surrounding double quotes from a STRING token's text."""
    s = str(s)
    return s[1:-1] if len(s) >= 2 and s[0] == s[-1] == '"' else s


def _seltext(tok) -> str:
    """Selector text from a SELECTOR (bare #id) or STRING (quoted) token."""
    return str(tok) if getattr(tok, 'type', '') == 'SELECTOR' else _unquote(str(tok))


def _duration_ms(dur) -> int:
    """Convert a DurationExpr to milliseconds. Browser timing floor is ms;
    second/minute/hour also supported. Default unit is seconds."""
    _mult = {'millisecond': 1, 'milliseconds': 1, 'ms': 1,
             'second': 1000, 'seconds': 1000, 'minute': 60000, 'minutes': 60000,
             'hour': 3600000, 'hours': 3600000}
    if dur is None or getattr(dur, 'count', None) is None:
        return 0
    return int(float(dur.count) * _mult.get((dur.unit or '').strip().lower(), 1000))


def _token_str(t) -> str:
    """Safely get string value of a token or tree."""
    if isinstance(t, Token):
        return str(t)
    if isinstance(t, Tree):
        # For single-child trees like type_name, value_expr -- return the child
        if len(t.children) == 1:
            return _token_str(t.children[0])
    return str(t)


def _is_tree(node, rule_name: str) -> bool:
    return isinstance(node, Tree) and node.data == rule_name


def _is_token(node, token_type: str) -> bool:
    return isinstance(node, Token) and node.type == token_type


def _filter_trees(children, rule_name: str) -> list:
    return [c for c in children if _is_tree(c, rule_name)]


def _filter_type(children, cls) -> list:
    return [c for c in children if isinstance(c, cls)]


def _first_tree(children, rule_name: str):
    for c in children:
        if _is_tree(c, rule_name):
            return c
    return None



def _mohio_unescape(s):
    """Decode the supported escape sequences in a string literal body.
    Targeted (not codecs.unicode_escape) so UTF-8 in strings is never corrupted."""
    out = []; i = 0; n = len(s)
    _map = {'n': '\n', 't': '\t', 'r': '\r', '"': '"', '\\': '\\', '/': '/', '0': '\0'}
    while i < n:
        c = s[i]
        if c == '\\' and i + 1 < n:
            nxt = s[i + 1]
            if nxt in _map:
                out.append(_map[nxt]); i += 2; continue
            if nxt == 'u' and i + 6 <= n and all(h in '0123456789abcdefABCDEF' for h in s[i + 2:i + 6]):
                out.append(chr(int(s[i + 2:i + 6], 16))); i += 6; continue
            out.append(c); out.append(nxt); i += 2; continue   # unknown escape: keep literal
        out.append(c); i += 1
    return ''.join(out)


def _mohio_decode_string(raw):
    """Strip the surrounding double quotes from a STRING token and decode escapes."""
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        inner = raw[1:-1]
    else:
        inner = raw.strip('"')
    return _mohio_unescape(inner)


def _coerce_number(token_str: str):
    """'0.85' -> 0.85  |  '10' -> 10"""
    try:
        if '.' in token_str:
            return float(token_str)
        return int(token_str)
    except ValueError:
        return token_str


import re as _re_codes
_LEADING_ZERO_CODE = _re_codes.compile(r'^-?0\d+$')   # 001, 007, 0042 (no decimal point)

def _number_or_code(token_str: str):
    """Decide how a NUMBER literal in value position should be typed.

    A leading-zero integer (001, 007, 0042) is a zero-padded *code*: keep it as a
    string so the zeros survive for storage, display, and equality matching.
    Numeric comparisons (above/below/between) coerce it back to a number, and
    `as int` converts it explicitly when a real number is wanted. Plain numbers
    (5, 10, 0, 0.5, 0.85) stay numeric — a decimal point means it's not a code.

    Returns (value, literal_type).
    """
    s = str(token_str)
    if _LEADING_ZERO_CODE.match(s):
        return (s, 'string')          # preserve leading zeros as a code
    return (_coerce_number(s), 'number')


# --------------------------------------------------------------
# TRANSFORMER
# --------------------------------------------------------------

class MohioTransformer(Transformer):
    """
    Transforms the Lark parse tree into Mohio AST nodes.

    Closer validation is enforced inline as each block is built --
    the closer is the last child of every named block, and we
    validate it matches the block name before returning the node.
    """

    def __init__(self):
        super().__init__()
        # Closer stack is used during transformation of nested blocks
        # We don't use it as a persistent stack across the whole tree
        # because Lark transforms bottom-up. Instead, each block rule
        # validates its own closer inline.
        self._source_lines: list[str] = []

    def set_source(self, source: str):
        self._source_lines = source.splitlines()

    def _call_userfunc(self, tree, *args, **kwargs):
        """Generic line propagation (2026-07-31): after each rule builds its node, stamp the source
        line from the Lark tree meta onto any AST Node that doesn't already carry one. Nodes defaulted
        to line 0, so runtime errors could not point at a line. Single point -- no per-rule change."""
        node = super()._call_userfunc(tree, *args, **kwargs)
        if isinstance(node, Node) and not getattr(node, 'line', 0):
            try:
                _m = getattr(tree, 'meta', None)
                _ln = getattr(_m, 'line', 0) if _m is not None else 0
                if _ln:
                    node.line = _ln
            except Exception:
                pass
        return node

    # -- PROGRAM ROOT ------------------------------------------

    # Blocks that pair their closer as a sibling rather than nesting it
    # (verified empirically: only `connect` today). Allow-listed everywhere.
    _SIBLING_CLOSER_BLOCKS = {'connect'}

    def _scan_stray_closer(self, node):
        """Return the first stray Closer anywhere in the tree, or None.

        Every block transformer strips its closer from its body (via
        _body_without_closer or an explicit Closer filter), so a Closer that
        survives as an element of any body list means the block it should have
        closed did not parse and Earley fell back to loose statements (the
        silent-decomposition class). Walks the whole tree, so nested malformed
        blocks (inside task/journey/check/step) are caught too, not run silently.

        Skips raw Lark Tree nodes: a construct that parsed but has no transformer
        method stays a Tree (an unwired stub). Its closer is not a stray from
        decomposition -- that is a separate 'not wired' concern, surfaced at the
        interpreter, not here.
        """
        if isinstance(node, Tree):
            return None
        d = getattr(node, '__dict__', None)
        if not d:
            return None
        for v in d.values():
            if isinstance(v, list):
                for item in v:
                    if isinstance(item, Closer):
                        bn = getattr(item, 'block_name', None) or ''
                        if bn not in self._SIBLING_CLOSER_BLOCKS:
                            return item
                    elif isinstance(item, Tree):
                        continue
                    elif hasattr(item, '__dict__'):
                        found = self._scan_stray_closer(item)
                        if found is not None:
                            return found
            elif isinstance(v, Tree):
                continue
            elif hasattr(v, '__dict__') and not isinstance(v, (str, int, float, bool)):
                found = self._scan_stray_closer(v)
                if found is not None:
                    return found
        return None

    def start(self, children):
        stmts = [c for c in children if c is not None]
        program = Program(statements=stmts)
        # Fail loud on a stray block closer ANYWHERE -- top level or nested.
        # A closer (`X: done`) is normally consumed by its block; if one leaks
        # into a statement list, the block it should close did not parse and
        # Earley fell back to loose statements. Surface it instead of running
        # the broken decomposition.
        stray = self._scan_stray_closer(program)
        if stray is not None:
            bn = getattr(stray, 'block_name', None) or '?'
            ln = getattr(stray, 'line', None)
            if bn in ('find', 'retrieve', 'grab', 'check'):
                hint = (". The block it should close did not parse -- a condition above is "
                        "likely non-canonical. Valid comparisons: is above / is more than, "
                        "is below / is less than, is between A and B, contains, starts.with, "
                        "is empty, is not.")
            else:
                hint = (". The block it should close did not parse -- check the lines "
                        "above it for a malformed or non-canonical block body.")
            raise MohioCompileError(
                f"Unmatched block closer '{bn}: done'"
                + (f" (line {ln})" if ln else "")
                + hint)
        return program

    def statement(self, children):
        if not children:
            return None
        stmt = children[0]
        # A trailing `unless <condition>` guards the statement.
        unless_idx = next((i for i, c in enumerate(children)
                           if isinstance(c, Token) and c.type == 'UNLESS'), None)
        if unless_idx is not None and unless_idx + 1 < len(children):
            from mohio_ast import UnlessGuard
            return UnlessGuard(stmt=stmt, condition=children[unless_idx + 1])
        # A trailing `if <condition>` guards the statement -- the positive counterpart.
        if_idx = next((i for i, c in enumerate(children)
                       if isinstance(c, Token) and c.type == 'IF_KW'), None)
        if if_idx is not None and if_idx + 1 < len(children):
            from mohio_ast import IfGuard
            if_line   = getattr(children[if_idx], 'line', 0) or 0
            stmt_line = getattr(stmt, 'line', 0) or 0
            if stmt_line and if_line and if_line != stmt_line:
                raise MohioCompileError(
                    "`if` is a trailing guard: it goes on the SAME line as the statement it "
                    "guards, and it never opens a block. "
                    "Trailing: `show \"big\" if x is more than 3`. "
                    "To branch, use check/when: `check x / when x is more than 3 / ... / "
                    "check: done`.")
            return IfGuard(stmt=stmt, condition=children[if_idx + 1])
        return stmt

    # -- CLOSER -----------------------------------------------
    # Returns a Closer node. Block rules extract and validate it.

    def closer(self, children):
        # Grammar: DOTTED_CLOSER ":" DONE ("as" NAME)?
        # "as" is a grammar literal -- not in children as a Token
        # Children: [DOTTED_CLOSER, DONE, NAME(as_name)] or [DOTTED_CLOSER, DONE] or [DONE]
        block_name = None
        as_name    = None
        for tok in children:
            if not isinstance(tok, Token): continue
            if tok.type == 'DONE':          continue
            if tok.type == 'DOTTED_CLOSER': block_name = str(tok); continue
            if tok.type == 'NAME':
                val = str(tok).lower()
                if val not in ('done', 'as'):
                    as_name = str(tok)   # any NAME that isn't 'done' is the as_name
        line = _line(children[0]) if children and isinstance(children[0], Token) else 0
        return Closer(block_name=block_name, as_name=as_name, line=line)

    def _validate_closer(self, block_name: str, children: list, open_line: int = 0) -> Closer:
        """
        Find the Closer node in children, validate it matches block_name.
        Returns the Closer. Raises MohioCloserError on mismatch.

        Closer rule (canonical is 'verb: done'): a block opened by a verb is
        closed by that verb. The optional dot-modifier is sugar -- 'remove: done',
        'remove.all: done', and a bare 'done' all close a 'remove.all' block. Only
        the leading verb segment must match, so a leaked closer from a different
        verb (e.g. 'no.match' inside 'find') is still caught as a mismatch.
        """
        def _verb(name):
            return name.split('.')[0] if name else name

        target_verb = _verb(block_name)

        closer_nodes = [c for c in children if isinstance(c, Closer)]
        if not closer_nodes:
            # No closer found -- grammar required one, so this is a parse gap
            # Treat as a compile error
            raise MohioCompileError(
                f"'{block_name}: done' is missing.\n"
                f"Every {block_name} block must end with '{block_name}: done'.",
                line=open_line
            )

        closer_node = closer_nodes[-1]  # default: last closer in children
        # If a nested block's closer (e.g. 'no.match') leaked up via grammar
        # ambiguity, prefer the closer that actually matches this block's verb
        # (or a bare 'done').
        matching = [c for c in closer_nodes
                    if c.block_name is None or _verb(c.block_name) == target_verb]
        if matching:
            closer_node = matching[-1]

        if closer_node.block_name is None:
            # bare 'done' -- accept it (forgiving) but note the block name
            closer_node.block_name = block_name
            return closer_node

        if _verb(closer_node.block_name) != target_verb:
            raise MohioCloserError(
                expected=block_name,
                found=closer_node.block_name,
                open_line=open_line,
                close_line=closer_node.line,
            )

        return closer_node

    def _body_without_closer(self, children: list) -> list:
        """Return children with Closer nodes removed."""
        return [c for c in children if not isinstance(c, Closer)]

    # -- DECLARATIONS -----------------------------------------

    def declaration(self, children):
        return children[0]

    def respond_as_stmt(self, children):
        # respond as "json" | "xml" | "html" | "application/xml"  -- document-level
        # default response format. A per-response `give back X as Y` overrides it.
        from mohio_ast import RespondAsStmt
        raw = next((str(c).strip('"').strip() for c in children
                    if isinstance(c, Token) and c.type == 'STRING'), 'text/html')
        ct = {'json': 'application/json', 'xml': 'application/xml',
              'html': 'text/html', 'text': 'text/plain', 'csv': 'text/csv'}.get(raw.lower(), raw)
        return RespondAsStmt(content_type=ct)

    def sector_decl(self, children):
        # children: [SECTOR, sector_name]. sector_name is SECTOR_SEG ("." SECTOR_SEG)*
        # -- join the segments into a dotted string (e.g. financial.banking.retail)
        # rather than leaving an unresolved Tree, which broke the warning and any
        # logic that reads the sector name.
        type_node = children[-1]
        if isinstance(type_node, Tree) and str(getattr(type_node, 'data', '')) == 'sector_name':
            segs = [str(t) for t in type_node.children
                    if isinstance(t, Token) and t.type == 'SECTOR_SEG']
            sector = '.'.join(segs)
        elif isinstance(type_node, str):
            sector = type_node
        else:
            sector = _token_str(type_node)
        return SectorDecl(sector=sector)

    def connect_decl(self, children):
        # CONNECT NAME AS conn_access? NAME FROM (ENV_REF | SECRET_REF)
        # The grammar embeds ENV_REF/SECRET_REF as raw terminals directly in this rule
        # rather than routing through the separate env_ref/secret_ref sub-rules (which
        # DO build a real EnvRef/SecretRef below) -- so both arrive here as plain Lark
        # Tokens, not transformed nodes. `value = [c for c in children if not
        # isinstance(c, Token)]` was therefore always empty and `source` was always
        # None, for every connect declaration ever parsed, regardless of driver.
        # Verified live: node.source was None even for `connect db as sqlite from
        # env.DATABASE_URL`, the canonical example. Build the same EnvRef/SecretRef
        # shape env_ref/secret_ref build, from the raw token, so source is finally real.
        tokens = [c for c in children if isinstance(c, Token)]
        name_tokens = [t for t in tokens if t.type == 'NAME']
        alias = str(name_tokens[0]) if len(name_tokens) > 0 else ""
        driver = str(name_tokens[1]) if len(name_tokens) > 1 else ""
        env_tok = next((t for t in tokens if t.type == 'ENV_REF'), None)
        secret_tok = next((t for t in tokens if t.type == 'SECRET_REF'), None)
        if env_tok is not None:
            source = EnvRef(key=str(env_tok).replace('env.', ''))
        elif secret_tok is not None:
            source = SecretRef(key=str(secret_tok).replace('secret.', ''))
        else:
            # Defensive only -- FROM (ENV_REF | SECRET_REF) is not optional in the
            # grammar, so this should be unreachable from any real parse.
            source = None
        return ConnectDecl(name=alias, driver=driver, source=source)

    # ── mioconnect declaration ──────────────────────────────────
    # Each body/op-body alias returns a tagged tuple; the parent decl /
    # operation collects them. Keeps dispatch unambiguous after Lark
    # filters the keyword literals ("address", "auth", "path", ...).
    def mc_address(self, children):
        val = next((c for c in children if not isinstance(c, Token)), None)
        return ('address', val)

    def mc_auth_key(self, children):
        val = next((c for c in children if not isinstance(c, Token)), None)
        return ('auth', 'key', val)

    def mc_auth_bearer(self, children):
        val = next((c for c in children if not isinstance(c, Token)), None)
        return ('auth', 'bearer', val)

    def mc_auth_basic(self, children):
        vals = [c for c in children if not isinstance(c, Token)]
        return ('auth', 'basic', vals[0] if vals else None,
                vals[1] if len(vals) > 1 else None)

    def mc_auth_header(self, children):
        name_tok = next((c for c in children if isinstance(c, Token) and c.type == 'STRING'), None)
        val = next((c for c in children if not isinstance(c, Token)), None)
        hname = str(name_tok).strip('"') if name_tok else ""
        return ('auth', 'header', hname, val)

    def mc_webhook(self, children):
        val = next((c for c in children if not isinstance(c, Token)), None)
        return ('webhook', val)

    def mc_timeout(self, children):
        return ('timeout', list(children))

    def mc_retry(self, children):
        num = next((c for c in children if isinstance(c, Token) and c.type == 'NUMBER'), None)
        return ('retry', int(str(num)) if num else None)

    def mc_op_path(self, children):
        s = next((c for c in children if isinstance(c, Token)), None)
        return ('path', str(s).strip('"') if s else "")

    def mc_op_sends(self, children):
        s = next((c for c in children if isinstance(c, Token)), None)
        return ('sends', str(s) if s else None)

    def mc_op_returns(self, children):
        s = next((c for c in children if isinstance(c, Token)), None)
        return ('returns', str(s) if s else None)

    def mc_op_method(self, children):
        s = next((c for c in children if isinstance(c, Token)), None)
        return ('method', str(s).upper() if s else "POST")

    def mc_op_timeout(self, children):
        return ('timeout', list(children))

    def mioconnect_body(self, children):
        # Unwraps the unaliased alternatives (operation, on_failure_handler)
        # so they reach mioconnect_decl directly.
        return children[0] if children else None

    def mioconnect_operation(self, children):
        from mohio_ast import MioconnectOperation
        name_tok = next((c for c in children if isinstance(c, Token) and c.type == 'NAME'), None)
        op = MioconnectOperation(name=str(name_tok) if name_tok else "")
        for c in children:
            if isinstance(c, tuple):
                tag = c[0]
                if   tag == 'path':    op.path = c[1]
                elif tag == 'method':  op.method = c[1]
                elif tag == 'sends':   op.sends_shape = c[1]
                elif tag == 'returns': op.returns_shape = c[1]
        return op

    def mioconnect_decl(self, children):
        from mohio_ast import MioconnectDecl, MioconnectOperation
        has_as   = any(isinstance(c, Token) and c.type == 'AS'   for c in children)
        has_from = any(isinstance(c, Token) and c.type == 'FROM' for c in children)
        name_toks = [c for c in children if isinstance(c, Token) and c.type == 'NAME']
        name  = str(name_toks[0]) if name_toks else ""
        alias = str(name_toks[1]) if (has_as and len(name_toks) > 1) else None
        decl  = MioconnectDecl(name=name, alias=alias)
        if has_from:
            # shorthand: mioconnect Name from value_expr  (source is the lone value node)
            src = next((c for c in children
                        if not isinstance(c, (Token, Closer, tuple, MioconnectOperation))), None)
            decl.source = src
            return decl
        for c in children:
            if isinstance(c, MioconnectOperation):
                decl.operations.append(c)
            elif isinstance(c, tuple):
                tag = c[0]
                if   tag == 'address':  decl.address = c[1]
                elif tag == 'auth':
                    decl.auth_type = c[1]
                    if c[1] == 'header':
                        decl.auth_header_name = c[2]; decl.auth_value = c[3]
                    elif c[1] == 'basic':
                        decl.auth_value = c[2]; decl.auth_value2 = c[3]
                    else:  # bearer / key
                        decl.auth_value = c[2]
                elif tag == 'timeout':  decl.timeout = c[1]
                # webhook / retry: parsed and accepted; no node field in MVP
        return decl

    def mioconnect_call(self, children):
        # dotted_name WITH value_expr (AS NAME)?
        from mohio_ast import MioconnectCall, DottedName
        dn = next((c for c in children if isinstance(c, DottedName)), None)
        parts = dn.parts if dn else []
        connector = parts[0] if parts else ""
        operation = ".".join(parts[1:]) if len(parts) > 1 else ""
        payload = None
        result = ""
        phase = 'pre'
        for c in children:
            if isinstance(c, Token) and c.type == 'WITH':
                phase = 'payload'; continue
            if isinstance(c, Token) and c.type == 'AS':
                phase = 'result'; continue
            if c is dn:
                continue
            if phase == 'payload' and payload is None:
                payload = c                      # value_expr — Token (bare NAME) or node
            elif phase == 'result' and isinstance(c, Token) and c.type == 'NAME':
                result = str(c)
        return MioconnectCall(connector=connector, operation=operation,
                              payload=payload, result=result)

    def shape_decl(self, children):
        # SHAPE NAME shape_field* closer
        name_token = next((c for c in children if isinstance(c, Token) and c.type == 'NAME'), None)
        open_line = _line(name_token)
        fields = [c for c in children if isinstance(c, ShapeField)]
        zone_tok = next((c for c in children if isinstance(c, Token) and c.type == 'TAG_REF'), None)
        zone_tag = str(zone_tok).strip('[]').strip() if zone_tok is not None else None
        if zone_tag is None and any(isinstance(c, Token) and c.type == 'SEC_ENCRYPT' for c in children):
            zone_tag = 'encrypt'   # generic zone seal: shape X sec.encrypt
        self._validate_closer('shape', children, open_line)
        return ShapeDecl(
            name=str(name_token) if name_token else "",
            fields=fields,
            zone_tag=zone_tag,
            line=open_line,
        )

    def shape_body(self, children):
        # shape_body: shape_field | "retain" FOR duration_expr
        # The grammar wraps every field in a shape_body node. Without this
        # passthrough the ShapeField stays nested one level deep and
        # shape_decl's field filter never sees it (fields come out empty).
        # Bubble the ShapeField up; a non-field body (retain) passes through
        # untouched and is ignored by shape_decl's field filter.
        for c in children:
            if isinstance(c, ShapeField):
                return c
        return children[0] if len(children) == 1 else children

    def shape_field(self, children):
        name_token = next((c for c in children if isinstance(c, Token) and c.type == 'NAME'), None)
        type_node = _first_tree(children, 'type_name')
        type_name = _token_str(type_node) if type_node else None
        mods = [c for c in children if isinstance(c, ShapeFieldModifier)]
        return ShapeField(
            name=str(name_token) if name_token else "",
            type_name=type_name,
            modifiers=mods,
            line=_line(name_token),
        )

    def shape_field_mod(self, children):
        # Identify the modifier by its leading keyword token and capture its
        # value. The earlier version joined the first two tokens into the key,
        # which swallowed the value of single-keyword modifiers like
        # `label "..."`, `format "..."`, and `default ...`.
        def _clean(tok):
            s = str(tok)
            return s[1:-1] if len(s) >= 2 and s[0] == s[-1] == '"' else s

        toks = [c for c in children if isinstance(c, Token)]
        head = str(toks[0]).lower() if toks else ''

        # two-keyword flags: never store / never log
        if head == 'never' and len(toks) >= 2:
            return ShapeFieldModifier(modifier_type=f"never_{str(toks[1]).lower()}", value=None)

        # allowed <list>: the options live in an allowed_list tree
        allowed_tree = _first_tree(children, 'allowed_list')
        if allowed_tree is not None:
            opts = [_clean(t) for t in allowed_tree.children if isinstance(t, Token)]
            return ShapeFieldModifier(modifier_type='allowed', value=opts)

        # bare flags: required / optional / unique / multiline / multiple
        if head in ('required', 'optional', 'unique', 'multiline', 'multiple'):
            return ShapeFieldModifier(modifier_type=head, value=None)

        # keyword + value(s): label, format, default, threshold, range, ...
        key = 'format' if head in ('format', 'format_kw') else head
        rest = [c for i, c in enumerate(children) if not (i == 0 and isinstance(c, Token))]
        vals = []
        for c in rest:
            if isinstance(c, Token):
                vals.append(_clean(c))
            elif isinstance(c, Literal):
                vals.append(c.value)
            else:
                vals.append(c)
        value = vals[0] if len(vals) == 1 else (vals or None)
        return ShapeFieldModifier(modifier_type=key, value=value)

    def _field_num(self, tok, which):
        s = str(tok).strip()
        try:
            return float(s) if ('.' in s or 'e' in s.lower()) else int(s)
        except ValueError:
            raise MohioCompileError(
                f"'{which} {s}' needs a number (for example {which} 5 or {which} 0.01).")

    def field_encrypt_mod(self, children):
        return ShapeFieldModifier(modifier_type='encrypt', value=None)

    def field_tag_mod(self, children):
        tok = next((c for c in children if isinstance(c, Token)), None)
        tag = str(tok).strip('[]').strip() if tok is not None else None
        return ShapeFieldModifier(modifier_type='tag', value=tag)

    def field_purpose_mod(self, children):
        tok = next((c for c in children
                    if isinstance(c, Token) and getattr(c, 'type', None) == 'STRING'), None)
        val = str(tok).strip('"') if tok is not None else None
        return ShapeFieldModifier(modifier_type='purpose', value=val)

    def field_min_mod(self, children):
        n = next(c for c in children if isinstance(c, Token))
        return ShapeFieldModifier(modifier_type='min', value=self._field_num(n, 'min'))

    def field_max_mod(self, children):
        n = next(c for c in children if isinstance(c, Token))
        return ShapeFieldModifier(modifier_type='max', value=self._field_num(n, 'max'))

    def field_minmax_mod(self, children):
        nums = [int(str(c)) for c in children if isinstance(c, Token)]
        return ShapeFieldModifier(modifier_type='minmax', value=tuple(nums[:2]))

    def field_matches_mod(self, children):
        # MATCHES is now a named terminal, so it appears as a token in children;
        # take the field NAME it references, not the keyword itself.
        n = next(c for c in children if isinstance(c, Token) and c.type == 'NAME')
        return ShapeFieldModifier(modifier_type='matches', value=str(n))

    def field_pattern_mod(self, children):
        s = next((c for c in children if isinstance(c, Token) and c.type == 'STRING'), None)
        raw = str(s) if s is not None else '""'
        val = raw[1:-1] if len(raw) >= 2 and raw[0] == raw[-1] == '"' else raw
        return ShapeFieldModifier(modifier_type='pattern', value=val)

    def field_accept_mod(self, children):
        exts = []
        for c in children:
            if hasattr(c, 'children'):  # the allowed_list tree
                for t in c.children:
                    if isinstance(t, Token):
                        s = str(t)
                        s = s[1:-1] if len(s) >= 2 and s[0] == s[-1] == '"' else s
                        exts.append(s.strip().lstrip('.').lower())
        return ShapeFieldModifier(modifier_type='accept', value=exts)

    def field_maxsize_mod(self, children):
        toks = [c for c in children if isinstance(c, Token)]
        num = next((t for t in toks if str(t).replace('.', '', 1).isdigit()), None)
        unit = next((t for t in toks if not str(t).replace('.', '', 1).isdigit()), None)
        mult = {'b': 1, 'kb': 1024, 'mb': 1024**2, 'gb': 1024**3}
        u = str(unit).strip().lower() if unit is not None else 'mb'
        n = float(str(num)) if num is not None else 0
        return ShapeFieldModifier(modifier_type='maxsize',
                                  value=int(n * mult.get(u, 1024**2)))

    def task_decl(self, children):
        name_token = next((c for c in children if isinstance(c, Token) and c.type == 'NAME'), None)
        open_line = _line(name_token)
        name = str(name_token) if name_token else ""
        # Params come from `take` statements. A take_stmt handler returns a LIST of
        # TaskParam; collect and flatten them (also accept bare TaskParam for safety).
        params = []
        for c in children:
            if isinstance(c, TaskParam):
                params.append(c)
            elif isinstance(c, list) and c and all(isinstance(p, TaskParam) for p in c):
                params.extend(c)
        type_node = _first_tree(children, 'type_name')
        return_type = _token_str(type_node) if type_node else None
        # v3.8: canonical task closer is 'task: done' not 'taskName: done'
        # Accept either form -- transformer warns on old form, doesn't error.
        closer_node = next((c for c in children if _is_tree(c, 'closer')), None)
        if closer_node:
            from lark import Token as _Token
            closer_name = next((str(t) for t in closer_node.scan_values(lambda v: True)
                               if isinstance(t, _Token) and t.type == 'DOTTED_CLOSER'), None)
            # Only validate if closer is not 'task' (the canonical v3.8 form)
            if closer_name and closer_name != 'task':
                self._validate_closer(name, children, open_line)
            # else: 'task: done' is always valid -- no check needed
        body = self._body_without_closer(
            [c for c in children if not isinstance(c, Token)
             and not isinstance(c, TaskParam)
             and not (isinstance(c, list) and c and all(isinstance(p, TaskParam) for p in c))
             and not _is_tree(c, 'type_name')]
        )
        return TaskDecl(name=name, params=params, return_type=return_type,
                        body=body, line=open_line)

    def _extract_task_params(self, params_node) -> list:
        if not params_node:
            return []
        param_list = _first_tree(params_node.children, 'param_list')
        if not param_list:
            return []
        return [c for c in param_list.children if isinstance(c, TaskParam)]

    def param(self, children):
        tokens = [c for c in children if isinstance(c, Token) and c.type == 'NAME']
        name = str(tokens[0]) if tokens else ""
        type_node = _first_tree(children, 'type_name')
        type_name = _token_str(type_node) if type_node else "any"
        default = next((c for c in children
                       if not isinstance(c, Token)
                       and not _is_tree(c, 'type_name')), None)
        return TaskParam(name=name, type_name=type_name, default=default)

    def call_body(self, children):
        """A named call argument: NAME value_expr  (e.g. `name "Bo"`)."""
        from mohio_ast import FieldValue
        name_tok = next((c for c in children if isinstance(c, Token) and c.type == 'NAME'), None)
        name = str(name_tok) if name_tok else ""
        value = next((c for c in children if not isinstance(c, Token)), None)
        return FieldValue(name=name, value=value)

    def task_param(self, children):
        """name [as type] [required|optional|default <value>] -- one task parameter.
        Grammar: task_param: (NAME|TRANSACTION) AS? type_name (REQUIRED|OPTIONAL|DEFAULT value_expr)?"""
        name_tok = next((c for c in children
                         if isinstance(c, Token) and c.type in ('NAME', 'TRANSACTION')), None)
        name = str(name_tok) if name_tok else ""
        type_node = _first_tree(children, 'type_name')
        type_name = _token_str(type_node) if type_node else "any"
        default = None
        if any(isinstance(c, Token) and c.type == 'DEFAULT' for c in children):
            default = next((c for c in children
                            if not isinstance(c, Token) and not _is_tree(c, 'type_name')), None)
        return TaskParam(name=name, type_name=type_name, default=default)

    def take_name(self, children):
        """take_name: NAME | TRANSACTION -- one parameter name, as a string."""
        tok = next((c for c in children if isinstance(c, Token)), None)
        return str(tok) if tok is not None else ""

    def take_group(self, children):
        """take_group: take_name ("," take_name)* (AS type_name)? (DEFAULT value_expr)?
        One comma group shares a type and an optional default across ALL its names.
        Returns a list of TaskParam (one per name)."""
        # NOTE: Lark's Token is a str subclass, so filter on `type(c) is str` to pick
        # ONLY the plain-string results of take_name (never the AS/DEFAULT tokens).
        names = [c for c in children if type(c) is str]
        type_node = _first_tree(children, 'type_name')
        type_name = _token_str(type_node) if type_node else "any"
        default = None
        if any(isinstance(c, Token) and c.type == 'DEFAULT' for c in children):
            default = next((c for c in children
                            if type(c) is not str and not isinstance(c, Token)
                            and not _is_tree(c, 'type_name')), None)
        return [TaskParam(name=n, type_name=type_name, default=default) for n in names]

    def take_stmt(self, children):
        """take_stmt: TAKE take_group (_AND take_group)* -- flat list of TaskParam."""
        params = []
        for c in children:
            if isinstance(c, list):
                params.extend(c)
        return params

    def retired_typed_decl(self, children):
        """`x as int 5` -- the type sits BEFORE the value. Backwards (a modifier follows what
        it modifies) and redundant (5 is already an integer). It used to degrade into two junk
        assignments (`x = as`, `int = 5`) and run."""
        name = next((str(c) for c in children
                     if isinstance(c, Token) and c.type == 'NAME'), 'x')
        raise MohioCompileError(
            f"`{name} as <type> <value>` is retired: the type is written before the value, "
            f"and the value already carries its type. Write `{name} <value>`.")

    def empty_typed_decl(self, children):
        """`x as int` (no value) -> declare an empty typed variable. Emits
        Assignment(value=None, type_name=<type>). The executor records the type contract on the
        name and leaves the value at its type-zero (0 / 0.0 / "" / false) until first assignment;
        every later assignment must satisfy the contract or fail loud. This is the standalone
        equivalent of a shape field `age as int`, and it removes the pull toward the retired
        `x as int 5` form (whose error message -- "expected a value" -- used to guide people into
        the backwards syntax)."""
        from mohio_ast import Assignment
        name_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'NAME'), None)
        type_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'TYPE_NAME'), None)
        return Assignment(name=str(name_token) if name_token else 'x',
                          type_name=str(type_token).lower() if type_token else None,
                          value=None,
                          line=_line(name_token) if name_token else 0)

    def retired_if_block(self, children):
        """`if x is more than 3 / ... / if: done` -- the #1 drift. Every other language opens a
        block with `if`; Mohio does not. This parses ONLY so we can say why."""
        raise MohioCompileError(
            "`if` is a trailing guard: it goes on the SAME line as the statement it guards, "
            "and it never opens a block. "
            "Trailing: `show \"big\" if x is more than 3`. "
            "To branch, use check/when: `check x / when x is more than 3 / ... / check: done`.")

    def retired_unless_block(self, children):
        """`unless x is more than 3 / ... / unless: done` -- the same drift as leading `if`.

        This one was worse than the `if` case because it did not fail. The line parsed as an
        UnlessGuard attached to nothing, the body became separate statements, and the guard did
        not guard -- so the body ran whatever the condition said. It only errored when the body
        happened not to form a valid standalone construct, which made it look intermittent
        rather than broken.
        """
        raise MohioCompileError(
            "`unless` is a trailing guard: it goes on the SAME line as the statement it guards, "
            "and it never opens a block. "
            "Trailing: `show \"small\" unless x is more than 3`. "
            "To branch, use check/when: `check x / when x is more than 3 / ... / check: done`.")

    def miotest_block(self, children):
        """`miotest "suite" / ... / miotest: done`

        The grammar rule for this existed and was referenced by NOTHING -- an orphan. So the
        block could never match, and `miotest "suite"` fell through to a declaration, silently
        creating a VARIABLE named miotest. Wired now; the runtime handler is still to come, so
        it fails LOUD at run rather than doing nothing.
        """
        from mohio_ast import MiotestDecl
        open_token = next((c for c in children if isinstance(c, Token)
                           and c.type.startswith('MIOTEST')), None)
        self._validate_closer('miotest', children, _line(open_token))
        name = next((str(c).strip('"') for c in children
                     if isinstance(c, Token) and c.type == 'STRING'), "")
        # The closer is a Closer NODE by now, not a Tree -- filter it by type or it leaks into
        # the body and trips the stray-closer scanner.
        body = [c for c in children
                if not isinstance(c, Token)
                and type(c).__name__ != 'Closer'
                and not _is_tree(c, 'closer')]
        return MiotestDecl(name=name, body=body)

    def miotest_unit(self, children):
        return self.miotest_block(children)

    def miotest_ai(self, children):
        return self.miotest_block(children)

    def retired_on_error(self, children):
        """`on.error` is retired. It said the same thing as `on.failure` -- the operation broke."""
        raise MohioCompileError(
            "`on.error` is retired: it means the same thing as `on.failure` (the operation "
            "broke), and one job takes one word. Use `on.failure`. For a result that came back "
            "empty, that is a CONDITION -- use `when` / `otherwise`.")

    def retired_typed_hold(self, children):
        """`hold x as int 5` -- the same backwards form wearing a `hold`."""
        name = next((str(c) for c in children
                     if isinstance(c, Token) and c.type == 'NAME'), 'x')
        raise MohioCompileError(
            f"`hold {name} as <type> <value>` is retired: the type is written before the "
            f"value, and the value already carries its type. Write `hold {name} <value>` to "
            f"freeze it until released, or just `{name} <value>` for an ordinary variable.")


    def hold_decl(self, children):
        name_token = next((c for c in children if isinstance(c, Token) and c.type == 'NAME'), None)
        name = str(name_token) if name_token else ""
        # `hold x as int 5` -- the declared type was being DISCARDED, so `hold x as banana 5`
        # checked clean and `as int` enforced nothing. Capture it so the type scanner sees it.
        _tn = _first_tree(children, 'type_name')
        _held_type = ""
        if _tn is not None:
            _held_type = ' '.join(str(t) for t in getattr(_tn, 'children', []) or []).strip()
        elif len(children) > 1:
            _t2 = next((c for c in children[1:]
                        if isinstance(c, Token) and c.type == 'TYPE_NAME'), None)
            if _t2 is not None:
                _held_type = str(_t2)
        # Reserved word check -- import here to avoid circular import
        from mohio_transformer import MOHIO_RESERVED_EXACT, MOHIO_RESERVED_WHAT
        if name.lower() in MOHIO_RESERVED_EXACT:
            what = MOHIO_RESERVED_WHAT.get(name.lower(), "a Mohio built-in namespace")
            raise ValueError(f"Reserved word '{name}' used as variable name. "
                           f"'{name}' is {what}.")

        # RETIRED (B6, 2026-08-01): the `hold` LIST form (`hold name / "a" / "b" / hold: done`).
        # `hold` freezes ONE scalar value and nothing else. This form was fully wired but never
        # retired in code (grammar productions + this transformer + _exec_HoldDecl all still ran
        # it); it also parsed ambiguously. Fail loud, pointing at the ratified list replacement.
        # The grammar production is kept so the message is precise instead of a raw parse error.
        list_items = [c for c in children if _is_tree(c, 'hold_list_item')]
        if list_items:
            raise MohioCompileError(
                f"`hold {name}` as a LIST (indented items under it) is retired: `hold` freezes a "
                f"single scalar value, nothing else. Build a list with `create list {name}` "
                f"(items indented, one per line, then `create: done`), or inline with "
                f"`{name} as list \"a\", \"b\", \"c\"`.")

        # RETIRED (B6, 2026-08-01): the `hold` PROFILE/DICT block form (`hold name / field value
        # / ...`). Same story as the list form -- wired, never retired, and worse: it could
        # SILENTLY DROP its last field depending on surrounding statements. Retired in favour of
        # `create`, which builds the same structured value reliably in any program position.
        body_items = [c for c in children if _is_tree(c, 'hold_body')]
        if body_items:
            raise MohioCompileError(
                f"`hold {name}` as a structured value (indented `field value` lines) is retired: "
                f"`hold` freezes a single scalar value, nothing else. Build a structured value "
                f"with `create {name}` (fields indented, then `create: done`), optionally typed "
                f"as `create {name} as sh.YourShape`.")

        # Simple form: hold NAME value [default fallback]
        value_exprs = [c for c in children if not isinstance(c, Token)
                       and not _is_tree(c, 'closer')
                       and not _is_tree(c, 'type_name')]
        has_default = any(isinstance(c, Token) and c.type == 'DEFAULT' for c in children)
        value   = value_exprs[0] if value_exprs else None
        default = value_exprs[1] if (has_default and len(value_exprs) > 1) else None
        return HoldDecl(type_name=_held_type, name=name, value=value, default=default,
                        line=_line(name_token))

    def lock_decl(self, children):
        name_token = next((c for c in children if isinstance(c, Token) and c.type == 'NAME'), None)
        value = next((c for c in children if not isinstance(c, Token)), None)
        return LockDecl(
            name=str(name_token) if name_token else "",
            value=value,
            line=_line(name_token),
        )

    def lock_existing(self, children):
        # `lock x` (no value): seal an already-existing variable in place. LockDecl with value=None
        # tells the executor to lock the current value rather than assign a new one.
        name_token = next((c for c in children if isinstance(c, Token) and c.type == 'NAME'), None)
        return LockDecl(name=str(name_token) if name_token else "",
                        value=None,
                        line=_line(name_token))

    def compliance_decl(self, children):
        name = next((str(c) for c in children if isinstance(c, Token) and c.type == 'NAME'), "")
        return ComplianceDecl(framework=name)

    def sec_level_off(self, children):
        return ("level", "off")

    def sec_level_standard(self, children):
        return ("level", "standard")

    def security_reason(self, children):
        s = next((c for c in children if isinstance(c, (str, Token))), "")
        return ("reason", _mohio_decode_string(str(s)))

    def security_expires(self, children):
        s = next((c for c in children if isinstance(c, (str, Token))), "")
        return ("expires", _mohio_decode_string(str(s)))

    def security_decl(self, children):
        # children: [("level", ...)] then zero+ [("reason"/"expires", ...)]
        level, reason, expires = "standard", "", ""
        for c in children:
            if isinstance(c, tuple) and len(c) == 2:
                key, val = c
                if key == "level":     level = val
                elif key == "reason":  reason = val
                elif key == "expires": expires = val
        return SecurityDecl(level=level, reason=reason, expires=expires)

    def include_decl(self, children):
        path = next((str(c).strip('"') for c in children if isinstance(c, Token)
                     and c.type == 'STRING'), "")
        return IncludeDecl(path=path)

    def require_role_decl(self, children):
        role_list_node = _first_tree(children, 'role_list')
        roles = []
        if role_list_node:
            from lark import Tree as _Tree
            for child in role_list_node.children:
                if isinstance(child, Token) and child.type == 'STRING':
                    roles.append(str(child).strip('"'))
                elif isinstance(child, Token) and child.type == 'NAME':
                    roles.append(str(child))
                elif isinstance(child, _Tree) and child.data == 'role_val':
                    # New grammar wraps roles in role_val subtrees
                    for tok in child.children:
                        if isinstance(tok, Token):
                            roles.append(str(tok).strip('"'))
        return RequireRoleDecl(roles=roles)

    def grant_role_decl(self, children):
        # GRANT ROLE value_expr -- the lone non-token child is the value node.
        value = next((c for c in children if not isinstance(c, Token)), None)
        return GrantRoleDecl(value=value)

    def rate_limit_decl(self, children):
        number = next((c for c in children if isinstance(c, Token)
                       and c.type == 'NUMBER'), None)
        unit_node = _first_tree(children, 'time_unit')
        unit = _token_str(unit_node) if unit_node else ""
        per_name = next((str(c) for c in children
                         if isinstance(c, Token) and c.type == 'NAME'), None)
        count = _coerce_number(str(number)) if number else None
        return RateLimitDecl(count=count, unit=unit, per=per_name)

    def timespan_decl(self, children):
        name_token = next((c for c in children if isinstance(c, Token)
                           and c.type == 'NAME'), None)
        open_line = _line(name_token)
        body = [c for c in children
                if not isinstance(c, Token) and not isinstance(c, Closer)]
        self._validate_closer('timespan', children, open_line)
        return TimespanDecl(
            name=str(name_token) if name_token else "",
            body=body,
            line=open_line,
        )

    def view_call_stmt(self, children):
        # view "home" [param value ...] -- build ViewCallStmt so the (already
        # functional) executor runs; without this the raw tree hit "no executor".
        from mohio_ast import ViewCallStmt
        name_tok = next((c for c in children if isinstance(c, Token)
                         and c.type in ('STRING', 'NAME')), None)
        if name_tok is not None and name_tok.type == 'STRING':
            template_name = _mohio_decode_string(str(name_tok))
        else:
            template_name = str(name_tok) if name_tok is not None else ""
        params = []
        for c in children:
            if _is_tree(c, 'view_call_pair') and len(c.children) >= 2:
                params.append((str(c.children[0]), c.children[1]))
        return ViewCallStmt(template_name=template_name, params=params)

    def compare_block(self, children):
        # compare A to B [return ...] : done -- build CompareBlock so the
        # executor runs (was a raw tree -> "no executor").
        from mohio_ast import CompareBlock
        names = [c for c in children if isinstance(c, Token) and c.type == 'NAME']
        open_line = _line(names[0]) if names else 0
        self._validate_closer('compare', children, open_line)
        body = [c for c in children
                if not isinstance(c, Token) and not isinstance(c, Closer)]
        return CompareBlock(
            name_a=str(names[0]) if len(names) > 0 else "",
            name_b=str(names[1]) if len(names) > 1 else "",
            body=body, handlers=[], line=open_line)

    def timespan_body(self, children):
        keyword = next((str(c) for c in children if isinstance(c, Token)
                        and c.type in ('START', 'END', 'UNTIL', 'PRECISION',
                                       'TIMEZONE', 'EVERY', 'EXCLUDE', 'BETWEEN')), "")
        values = [c for c in children if not isinstance(c, Token)]
        keyword_lower = keyword.lower()
        if keyword_lower in ('start', 'end', 'until'):
            return TimespanAnchor(anchor_type=keyword_lower,
                                  datetime_expr=values[0] if values else None)
        elif keyword_lower == 'precision':
            name = next((str(c) for c in children
                         if isinstance(c, Token) and c.type == 'NAME'), "")
            return TimespanPrecision(precision=name)
        elif keyword_lower == 'timezone':
            tz = next((str(c).strip('"') for c in children
                       if isinstance(c, Token)
                       and c.type in ('STRING', 'NAME')), "")
            return TimespanTimezone(timezone=tz)
        elif keyword_lower == 'every':
            pattern = _token_str(values[0]) if values else ""
            return TimespanRecurring(pattern=pattern)
        elif keyword_lower == 'exclude':
            return TimespanExclude(exclude_type=str(values[0]) if values else "")
        return Node()

    # -- BLOCK STATEMENTS -------------------------------------

    def block_stmt(self, children):
        return children[0]

    def listen_block(self, children):
        self._reject_quoted_path(children, 'listen for')
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'LISTEN'), None)
        open_line = _line(open_token)
        self._validate_closer('listen', children, open_line)
        # Shape-on-listener: `listen for sh.X [at /path]` binds the shape directly
        # and the body IS the handler (no `new` wrapper). A SH_REF token sitting
        # directly on the listen (nested new/request blocks keep their SH_REF
        # inside their own node) means the single-shape form. Synthesize a
        # NewBlock so the existing path/shape dispatch routes it unchanged.
        sh_token = next((c for c in children
                         if isinstance(c, Token) and c.type == 'SH_REF'), None)
        if sh_token is not None:
            shape_name = str(sh_token).replace('sh.', '')
            path_token = next((c for c in children
                               if isinstance(c, Token) and c.type == 'PATH_LIT'), None)
            path = str(path_token) if path_token else None
            body = self._body_without_closer(
                [c for c in children if not isinstance(c, Token)]
            )
            listener = NewBlock(shape=shape_name, path=path, body=body, line=open_line)
            return ListenBlock(listeners=[listener], line=open_line)
        # Multi-handler form: new/request/connection/change/from blocks inside.
        body = self._body_without_closer(
            [c for c in children if not isinstance(c, Token)]
        )
        return ListenBlock(listeners=body, line=open_line)

    def listener_body(self, children):
        return children[0] if children else None

    # ── MioScript: browser-event listener and client statements ──
    def client_listen_block(self, children):
        from mohio_ast import ClientListener, DurationExpr
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'LISTEN'), None)
        open_line = _line(open_token)
        self._validate_closer('listen', children, open_line)
        toks = [c for c in children if isinstance(c, Token)]
        event = next((str(t) for t in toks if t.type == 'NAME'), '')
        sel_tok = next((t for t in toks if t.type in ('STRING', 'SELECTOR')), None)
        selector = _seltext(sel_tok) if sel_tok is not None else ''
        dur = next((c for c in children if isinstance(c, DurationExpr)), None)
        debounce_ms = _duration_ms(dur)
        body = [c for c in children
                if not isinstance(c, Token)
                and type(c).__name__ not in ('Closer', 'DurationExpr')]
        return ClientListener(event=event, selector=selector, body=body,
                              debounce_ms=debounce_ms, line=open_line)

    def client_listen_change(self, children):
        from mohio_ast import ClientListener
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'LISTEN'), None)
        open_line = _line(open_token)
        self._validate_closer('listen', children, open_line)
        sel_tok = next((c for c in children
                        if isinstance(c, Token) and c.type in ('STRING', 'SELECTOR')), None)
        selector = _seltext(sel_tok) if sel_tok is not None else ''
        body = [c for c in children
                if not isinstance(c, Token) and type(c).__name__ != 'Closer']
        return ClientListener(event='change', selector=selector, body=body, line=open_line)

    def client_put(self, children):
        from mohio_ast import ClientPut
        val = next((c for c in children if isinstance(c, tuple)), ('literal', ''))
        sels = [c for c in children if isinstance(c, Token) and c.type in ('STRING', 'SELECTOR')]
        target = _seltext(sels[-1]) if sels else ''
        return ClientPut(source_kind=val[0], source=val[1], target=target)

    def client_toggle(self, children):
        from mohio_ast import ClientToggle
        toks = [c for c in children if isinstance(c, Token)]
        attr = next((str(t) for t in toks if t.type == 'NAME'), 'type')
        sel_tok = next((t for t in toks if t.type in ('STRING', 'SELECTOR')), None)
        selector = _seltext(sel_tok) if sel_tok is not None else ''
        betweens = [t for t in toks if t.type == 'STRING' and t is not sel_tok]
        a = _unquote(str(betweens[0])) if len(betweens) > 0 else ''
        b = _unquote(str(betweens[1])) if len(betweens) > 1 else ''
        return ClientToggle(attr=attr, selector=selector, state_a=a, state_b=b)

    def client_literal(self, children):
        s = next((c for c in children if isinstance(c, Token) and c.type == 'STRING'), None)
        return ('literal', _unquote(str(s)) if s is not None else '')

    def client_the(self, children):
        n = next((c for c in children if isinstance(c, Token) and c.type == 'NAME'), None)
        return ('the', str(n) if n is not None else 'value')

    def client_subject(self, children):
        n = next((c for c in children if isinstance(c, Token) and c.type == 'NAME'), None)
        return ('subject', str(n) if n is not None else 'value')

    def cond_valid(self, children):
        n = next((c for c in children if isinstance(c, Token) and c.type == 'NAME'), None)
        return ('cond', ('valid', str(n) if n is not None else 'email'))

    def cond_matches(self, children):
        s = next((c for c in children if isinstance(c, Token) and c.type in ('STRING', 'SELECTOR')), None)
        return ('cond', ('matches', _seltext(s) if s is not None else ''))

    def cond_empty(self, children):
        return ('cond', ('empty',))

    def cond_notempty(self, children):
        return ('cond', ('notempty',))

    def client_when(self, children):
        cond = next((c[1] for c in children
                     if isinstance(c, tuple) and c and c[0] == 'cond'), ('empty',))
        stmts = [c for c in children if hasattr(c, '__dict__')]
        return ('when', cond, stmts)

    def client_otherwise(self, children):
        return ('otherwise', [c for c in children if hasattr(c, '__dict__')])

    def client_check(self, children):
        from mohio_ast import ClientCheck
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'CHECK'), None)
        open_line = _line(open_token)
        self._validate_closer('check', children, open_line)
        subject = next((c[1] for c in children
                        if isinstance(c, tuple) and c and c[0] == 'subject'), 'value')
        branches = [(c[1], c[2]) for c in children
                    if isinstance(c, tuple) and c and c[0] == 'when']
        otherwise = next((c[1] for c in children
                          if isinstance(c, tuple) and c and c[0] == 'otherwise'), [])
        return ClientCheck(subject=subject, branches=branches,
                           otherwise=otherwise, line=open_line)

    def _client_dom(self, op, children, two=False):
        from mohio_ast import ClientDomOp
        sels = [_seltext(c) for c in children
                if isinstance(c, Token) and c.type in ('STRING', 'SELECTOR')]
        if two:
            return ClientDomOp(op=op, arg=sels[0] if sels else '',
                               selector=sels[1] if len(sels) > 1 else '')
        return ClientDomOp(op=op, selector=sels[0] if sels else '')

    def client_show(self, children):    return self._client_dom('show', children)
    def client_hide(self, children):    return self._client_dom('hide', children)
    def client_enable(self, children):  return self._client_dom('enable', children)
    def client_disable(self, children): return self._client_dom('disable', children)
    def client_clear(self, children):    return self._client_dom('clear', children)
    def client_focus(self, children):    return self._client_dom('focus', children)
    def client_scrollto(self, children): return self._client_dom('scrollto', children)
    def client_removeelem(self, children): return self._client_dom('removeelem', children)
    def client_selecttext(self, children): return self._client_dom('selecttext', children)
    def client_togglevis(self, children):  return self._client_dom('togglevis', children)

    def _client_state(self, children, op):
        from mohio_ast import ClientState
        sel = next((_seltext(c) for c in children
                    if isinstance(c, Token) and c.type in ('STRING', 'SELECTOR')), '')
        state = next((str(c) for c in children
                      if isinstance(c, Token) and c.type == 'NAME'), '')
        return ClientState(selector=sel, state=state, op=op)

    def client_mark(self, children):      return self._client_state(children, 'add')
    def client_unmark(self, children):    return self._client_state(children, 'remove')
    def client_markstate(self, children): return self._client_state(children, 'toggle')

    def client_validate(self, children):
        from mohio_ast import ClientValidate
        vt = next((str(c) for c in children
                   if isinstance(c, Token) and c.type == 'NAME'), '')
        return ClientValidate(vtype=vt)

    def client_append(self, children):
        from mohio_ast import ClientAppend
        val = next((c for c in children if isinstance(c, tuple)), ('literal', ''))
        sels = [c for c in children if isinstance(c, Token) and c.type in ('STRING', 'SELECTOR')]
        target = _seltext(sels[-1]) if sels else ''
        return ClientAppend(source_kind=val[0], source=val[1], target=target)

    def cond_atleastnum(self, children):
        n = next((c for c in children if isinstance(c, Token) and c.type == 'NUMBER'), None)
        return ('cond', ('atleastnum', float(str(n)) if n is not None else 0))

    def cond_atmostnum(self, children):
        n = next((c for c in children if isinstance(c, Token) and c.type == 'NUMBER'), None)
        return ('cond', ('atmostnum', float(str(n)) if n is not None else 0))

    def cond_all(self, children):
        inner = [c[1] for c in children if isinstance(c, tuple) and c and c[0] == 'cond']
        return ('cond', ('all', inner))

    def cond_any(self, children):
        inner = [c[1] for c in children if isinstance(c, tuple) and c and c[0] == 'cond']
        return ('cond', ('any', inner))

    def client_request(self, children):
        from mohio_ast import ClientRequest
        ordered = [c for c in children
                   if isinstance(c, Token) and c.type in ('STRING', 'SELECTOR')]
        url = _unquote(str(ordered[0])) if ordered else ''
        target = _seltext(ordered[1]) if len(ordered) > 1 else ''
        return ClientRequest(url=url, target=target)

    def _cond_str_op(self, children, opname):
        # Two shapes reach here: the explicit-subject form
        # (value_expr OP value_expr) and the implicit form (`OP STRING`, subject
        # is the check value). Explicit -> a real Condition carrying the subject;
        # implicit -> the subject-less tuple applied to the check value.
        non_tokens = [c for c in children if not isinstance(c, Token)]
        if len(non_tokens) >= 2:
            return Condition(left=non_tokens[0], op=opname, right=non_tokens[1])
        s = next((c for c in children
                  if isinstance(c, Token) and c.type == 'STRING'), None)
        return ('cond', (opname, _unquote(str(s)) if s is not None else ''))

    def cond_contains(self, children):
        return self._cond_str_op(children, 'contains')

    def cond_starts(self, children):
        return self._cond_str_op(children, 'starts')

    def cond_ends(self, children):
        return self._cond_str_op(children, 'ends')

    def cond_minlen(self, children):
        n = next((c for c in children if isinstance(c, Token) and c.type == 'NUMBER'), None)
        return ('cond', ('minlen', int(str(n)) if n is not None else 0))

    def cond_maxlen(self, children):
        n = next((c for c in children if isinstance(c, Token) and c.type == 'NUMBER'), None)
        return ('cond', ('maxlen', int(str(n)) if n is not None else 0))

    def cond_checked(self, children):
        return ('cond', ('checked',))

    def cond_morethan(self, children):
        n = next((c for c in children if isinstance(c, Token) and c.type == 'NUMBER'), None)
        return ('cond', ('morethan', float(str(n)) if n is not None else 0))

    def cond_lessthan(self, children):
        n = next((c for c in children if isinstance(c, Token) and c.type == 'NUMBER'), None)
        return ('cond', ('lessthan', float(str(n)) if n is not None else 0))

    def cond_equals(self, children):
        s = next((c for c in children if isinstance(c, Token) and c.type == 'STRING'), None)
        return ('cond', ('equals', _unquote(str(s)) if s is not None else ''))

    def client_notify(self, children):
        from mohio_ast import ClientNotify
        val = next((c for c in children if isinstance(c, tuple)), ('literal', ''))
        return ClientNotify(source_kind=val[0], source=val[1])

    def client_hold(self, children):
        from mohio_ast import ClientHold
        name_tok = next((c for c in children
                         if isinstance(c, Token) and c.type == 'NAME'), None)
        val = next((c for c in children if isinstance(c, tuple)), ('literal', ''))
        return ClientHold(name=str(name_tok) if name_tok is not None else '',
                          source_kind=val[0], source=val[1])

    def client_result(self, children):
        tok = next((c for c in children
                    if isinstance(c, Token) and c.type == 'RESULT_REF'), None)
        return ('result', str(tok) if tok is not None else 'result')

    def client_on_success(self, children):
        body = [c for c in children if not isinstance(c, Token)]
        return ('__on_success__', body)

    def client_on_failure(self, children):
        body = [c for c in children if not isinstance(c, Token)]
        return ('__on_failure__', body)

    def client_send(self, children):
        from mohio_ast import ClientSend
        open_tok = next((c for c in children
                         if isinstance(c, Token) and c.type == 'SEND'), None)
        to_idx = next((i for i, c in enumerate(children)
                       if isinstance(c, Token) and c.type == 'TO'), -1)
        before = children[:to_idx] if to_idx >= 0 else children
        after = children[to_idx + 1:] if to_idx >= 0 else []
        form_tok = next((c for c in before
                         if isinstance(c, Token) and c.type in ('SELECTOR', 'STRING')), None)
        url_tok = next((c for c in after
                        if isinstance(c, Token) and c.type == 'STRING'), None)
        success, failure = [], []
        for c in children:
            if isinstance(c, tuple) and c and c[0] == '__on_success__':
                success = c[1]
            elif isinstance(c, tuple) and c and c[0] == '__on_failure__':
                failure = c[1]
        return ClientSend(
            form_selector=_seltext(form_tok) if form_tok is not None else '',
            url=_unquote(str(url_tok)) if url_tok is not None else '',
            success=success, failure=failure, line=_line(open_tok))

    def client_after(self, children):
        from mohio_ast import ClientAfter
        from mohio_ast import DurationExpr
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'STR_AFTER'), None)
        open_line = _line(open_token)
        self._validate_closer('after', children, open_line)
        dur = next((c for c in children if isinstance(c, DurationExpr)), None)
        ms = _duration_ms(dur)
        body = [c for c in children
                if hasattr(c, '__dict__') and type(c).__name__ not in ('Closer', 'DurationExpr')]
        return ClientAfter(ms=ms, body=body, line=open_line)

    def client_goto(self, children):
        from mohio_ast import ClientNav
        s = next((c for c in children if isinstance(c, Token) and c.type == 'STRING'), None)
        return ClientNav(op='goto', url=_unquote(str(s)) if s is not None else '')

    def client_goback(self, children):
        from mohio_ast import ClientNav
        return ClientNav(op='back')

    def client_reload(self, children):
        from mohio_ast import ClientNav
        return ClientNav(op='reload')

    def new_block(self, children):
        self._reject_quoted_path(children, 'new')
        sh_token = next((c for c in children
                         if isinstance(c, Token) and c.type == 'SH_REF'), None)
        open_line = _line(sh_token)
        shape_name = str(sh_token).replace('sh.', '') if sh_token else ""
        path_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'PATH_LIT'), None)
        path = str(path_token) if path_token else None
        self._validate_closer('new', children, open_line)
        body = self._body_without_closer(
            [c for c in children if not isinstance(c, Token)]
        )
        return NewBlock(shape=shape_name, path=path, body=body, line=open_line)

    def request_inbound_block(self, children):
        self._reject_quoted_path(children, 'request for')
        """v3.8 grammar renamed request_block -> request_inbound_block"""
        return self.request_block(children)

    def request_block(self, children):
        sh_token = next((c for c in children
                         if isinstance(c, Token) and c.type == 'SH_REF'), None)
        open_line = _line(sh_token)
        shape_name = str(sh_token).replace('sh.', '') if sh_token else ""
        path_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'PATH_LIT'), None)
        self._validate_closer('request', children, open_line)
        body = self._body_without_closer(
            [c for c in children if not isinstance(c, Token)]
        )
        return RequestBlock(shape=shape_name,
                            path=str(path_token) if path_token else None,
                            body=body, line=open_line)

    def change_block(self, children):
        sh_token = next((c for c in children
                         if isinstance(c, Token) and c.type == 'SH_REF'), None)
        open_line = _line(sh_token)
        shape_name = str(sh_token).replace('sh.', '') if sh_token else ""
        self._validate_closer('change', children, open_line)
        body = self._body_without_closer(
            [c for c in children if not isinstance(c, Token)]
        )
        return ChangeBlock(shape=shape_name, body=body, line=open_line)

    def connection_block(self, children):
        self._reject_quoted_path(children, 'connection')
        path_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'PATH_LIT'), None)
        open_line = _line(path_token)
        self._validate_closer('connection', children, open_line)
        body = self._body_without_closer(
            [c for c in children if not isinstance(c, Token)]
        )
        return ConnectionBlock(
            path=str(path_token) if path_token else "",
            body=body, line=open_line,
        )

    def connection_body(self, children):
        return children[0] if children else None

    def on_open_stmt(self, children):
        action = next((c for c in children if not isinstance(c, Token)), None)
        return OnOpen(action=action)

    def on_close_stmt(self, children):
        action = next((c for c in children if not isinstance(c, Token)), None)
        return OnClose(action=action)

    def while_active_block(self, children):
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'WHILE_ACTIVE'), None)
        open_line = _line(open_token)
        self._validate_closer('while', children, open_line)
        body = self._body_without_closer(
            [c for c in children if not isinstance(c, Token)]
        )
        return WhileActiveBlock(body=body, line=open_line)

    # -- JOURNEY / PAGE (multi-page model) ---------------------
    # journey = the app's root scope + routing container. Its body holds scope
    # declarations (connect/hold/task/shape/ai.decide/security/...), pages
    # (`page N at /path` = GET routes), an optional `listen for` block, and
    # config/metadata. Pages run in a child of the journey scope, so they inherit
    # everything the journey declared. See Docs/journey-page-design-2026-06-17.md.

    def path_list(self, children):
        # Just the raw path strings. Which keyword (public:/private:/flow:) this
        # list belongs to is now told apart at the journey_body alias level (each
        # of the three got its own -> alias, 2026-08-06) -- path_list itself no
        # longer needs to guess, it only ever builds the list.
        return [str(c) for c in children
                if isinstance(c, Token) and c.type in ('PATH_LIT', 'STRING')]

    # journey-level access-control metadata (2026-08-06). Each of the five aliased
    # journey_body alternatives now produces a correctly-tagged JourneyMeta -- never
    # silently collapsed into the same 'path_list' kind the way all three used to be,
    # and never dropped to None the way serves: used to be. private:/public: are read
    # and enforced by _exec_JourneyDecl (real, server-verified-session-based). flow:
    # is captured but still not interpreted -- no documented source of truth for its
    # intended behavior exists anywhere in this repo (the design doc _exec_JourneyDecl
    # itself cites, Docs/journey-page-design-2026-06-17.md, has never existed in git
    # history), so building a guessed runtime meaning for it was deliberately not done
    # here; only "no longer indistinguishable from public:/private:" was in scope.
    # serves: is captured with its real declared value but likewise not yet enforced --
    # real tenant isolation needs a way to establish a request's tenant identity that
    # does not exist anywhere in the language today (no grant-tenant-shaped primitive),
    # which is new grammar, not a wiring fix, so it stops here pending that ruling.
    def journey_public(self, children):
        paths = next((c for c in children if isinstance(c, list)), [])
        return JourneyMeta(kind='public', value=paths)

    def journey_private(self, children):
        paths = next((c for c in children if isinstance(c, list)), [])
        return JourneyMeta(kind='private', value=paths)

    def journey_flow(self, children):
        paths = next((c for c in children if isinstance(c, list)), [])
        return JourneyMeta(kind='flow', value=paths)

    def journey_serves_single(self, children):
        return JourneyMeta(kind='serves', value='single tenant')

    def journey_serves_multiple(self, children):
        return JourneyMeta(kind='serves', value='multiple tenants')

    def journey_body(self, children):
        # Each journey_body wraps exactly one item (declaration / statement /
        # JourneyMeta) already transformed by the time we get here. Historical note:
        # serves:/public:/private:/flow: used to drop to empty -> None here (serves:)
        # or collapse into an indistinguishable generic 'path_list' kind (the other
        # three) -- fixed 2026-08-06 via the five journey_body aliases above, which
        # now each produce their own correctly-tagged JourneyMeta before reaching
        # this generic wrapper.
        return children[0] if children else None

    def journey_decl(self, children):
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'JOURNEY'), None)
        open_line = _line(open_token)
        name_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'NAME'), None)
        name = str(name_token) if name_token else None
        self._validate_closer('journey', children, open_line)
        body = [c for c in children
                if not isinstance(c, Token)
                and not isinstance(c, Closer)
                and c is not None]
        return JourneyDecl(name=name, body=body, line=open_line)

    def _reject_quoted_path(self, children, construct):
        """The path after `at` is an unquoted literal (`at /about`). A quoted string
        there is a common mistake: it used to parse into a stray assignment and serve
        nothing. It is now captured by the grammar and rejected here with the exact
        fix. A direct STRING child of these path-taking rules can only be that
        mis-quoted path slot (body statements arrive as transformed AST nodes)."""
        str_tok = next((c for c in children
                        if isinstance(c, Token) and c.type == 'STRING'), None)
        if str_tok is not None:
            raw = str(str_tok)
            unq = raw[1:-1] if len(raw) >= 2 and raw[0] in '"\'' else raw
            raise MohioCompileError(
                f"paths after `at` are unquoted -- write `at {unq}`, not `at {raw}`. "
                f"A quoted path in this {construct} does not register and the route "
                f"will not serve. Run `mio fmt --write` to fix it automatically.",
                line=_line(str_tok))

    def page_decl(self, children):
        self._reject_quoted_path(children, 'page')
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'PAGE'), None)
        open_line = _line(open_token)
        name_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'NAME'), None)
        name = str(name_token) if name_token else None
        path_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'PATH_LIT'), None)
        path = str(path_token) if path_token else None
        self._validate_closer('page', children, open_line)
        body = [c for c in children
                if not isinstance(c, Token)
                and not isinstance(c, Closer)
                and c is not None]
        return PageDecl(name=name, path=path, body=body, line=open_line)

    # -- SAGA / STEP -------------------------------------------
    # Mechanical plumbing only: builds distinguishable nodes. The step handler
    # keywords (compensate / undo / best effort) are now named terminals, so a
    # step's compensation body, best-effort flag, and on.failure/on.success
    # handlers are each recovered distinctly (previously the bare-literal keywords
    # lost to NAME and compensation bodies were mis-parsed as assignments).
    # Saga *execution* semantics are intentionally NOT wired here -- see the
    # interpreter (fail-loud) and Docs/saga-step-semantics-for-design-chat.md.

    def step_handler(self, children):
        comp = next((c for c in children if isinstance(c, Token) and c.type == 'COMPENSATE'), None)
        undo = next((c for c in children if isinstance(c, Token) and c.type == 'UNDO'), None)
        best = next((c for c in children if isinstance(c, Token) and c.type == 'BEST_EFFORT'), None)
        stmts = [c for c in children if not isinstance(c, Token)]
        if comp is not None:
            return ('compensate', stmts)
        if undo is not None:
            return ('undo', stmts)
        if best is not None:
            return ('best_effort', None)
        # on_failure_handler / on_success_handler already transformed to OnFailure/OnSuccess
        return children[0] if children else None

    def step_block(self, children):
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'STEP'), None)
        open_line = _line(open_token)
        name_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'NAME'), None)
        name = str(name_token) if name_token else ""
        self._validate_closer('step', children, open_line)
        body, undo_body, best_effort, handlers = [], [], False, []
        for c in children:
            if isinstance(c, (Token, Closer)) or c is None:
                continue
            if isinstance(c, tuple):
                kind, payload = c
                if kind in ('compensate', 'undo'):
                    undo_body = payload or []   # both populate the compensation body
                elif kind == 'best_effort':
                    best_effort = True
                continue
            if isinstance(c, (OnFailure, OnSuccess)):
                handlers.append(c)
                continue
            if isinstance(c, Closer):
                continue   # structural terminator, not a body statement
            body.append(c)   # ordinary step body statement
        return StepBlock(name=name, body=body, undo=undo_body,
                         best_effort=best_effort, handlers=handlers, line=open_line)

    def saga_body(self, children):
        return children[0] if children else None

    def saga_decl(self, children):
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'SAGA'), None)
        open_line = _line(open_token)
        name_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'NAME'), None)
        name = str(name_token) if name_token else ""
        self._validate_closer('saga', children, open_line)
        steps = [c for c in children if isinstance(c, StepBlock)]
        return SagaDecl(name=name, steps=steps, line=open_line)

    # -- FLOW CONTROL ------------------------------------------

    # if_block transformer REMOVED (A6): block-`if` was retired in the No-If canon.
    # `if` is trailing-inline only; multi-branch logic is `check / when / otherwise`.
    # There is no `if_block` grammar rule, so this method was dead drift from v0.1.

    def or_if_clause(self, children):
        non_tokens = [c for c in children if not isinstance(c, Token)]
        cond = non_tokens[0] if non_tokens else None
        body = non_tokens[1:]
        return OrIfClause(condition=cond, body=body)

    def otherwise_clause(self, children):
        body = [c for c in children if not isinstance(c, Token)]
        return OtherwiseClause(body=body)

    def check_block(self, children):
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'CHECK'), None)
        open_line = _line(open_token)
        closer_node = next((c for c in children if isinstance(c, Closer)), None)
        # Naming goes on the ACTION: `check score as grade`. The old `check: done as grade`
        # put the name on the closer, which is drift -- accepted for now, action wins.
        name_toks = [c for c in children if isinstance(c, Token) and c.type == 'NAME']
        as_name = str(name_toks[0]) if name_toks else None
        if not as_name and closer_node:
            as_name = getattr(closer_node, 'as_name', None)
        non_tokens = [c for c in children
                      if not isinstance(c, (Token, Closer))]
        value = non_tokens[0] if non_tokens else None
        whens = [c for c in non_tokens[1:] if isinstance(c, CheckWhen)]
        otherwise = next((c for c in non_tokens if isinstance(c, OtherwiseClause)), None)
        return CheckBlock(value=value, when_clauses=whens,
                          otherwise=otherwise, as_name=as_name, line=open_line)

    def check_when(self, children):
        # Map condition tokens to canonical condition strings
        condition_map = {
            'WHEN':     'when',
            'ABOVE':    'above',
            'BELOW':    'below',
            'CONTAINS': 'contains',
            'IS_IN':    'is_in',
            'NOT':      'not',
        }
        # Find the condition token -- first Token that maps to a condition
        condition = 'when'
        for c in children:
            if isinstance(c, Token) and c.type in condition_map:
                condition = condition_map[c.type]
                break
        non_tokens = [c for c in children if not isinstance(c, Token)]
        value = non_tokens[0] if non_tokens else None
        body  = non_tokens[1:]
        return CheckWhen(value=value, condition=condition, body=body)

    def check_morethan(self, children):
        # `check x / is more than n` -- same comparison as `is above n` (>).
        non_tokens = [c for c in children if not isinstance(c, Token)]
        value = non_tokens[0] if non_tokens else None
        body  = non_tokens[1:]
        return CheckWhen(value=value, condition='above', body=body)

    def check_lessthan(self, children):
        # `check x / is less than n` -- same comparison as `is below n` (<).
        non_tokens = [c for c in children if not isinstance(c, Token)]
        value = non_tokens[0] if non_tokens else None
        body  = non_tokens[1:]
        return CheckWhen(value=value, condition='below', body=body)

    def check_mioql_block(self, children):
        # A1 MioQL: check exists / check count / check unique.
        from mohio_ast import CheckMioqlBlock, MatchClause, WhereClause
        toks = [c for c in children if isinstance(c, Token)]
        variant_tok = toks[0].type if toks else ''
        variant = {'CHECK_EXISTS': 'exists', 'CHECK_COUNT': 'count',
                   'CHECK_UNIQUE': 'unique'}.get(variant_tok, '')
        source = next((c for c in children
                       if type(c).__name__ in ('DbRef', 'DottedName')), None)
        rh = next((c for c in children
                   if isinstance(c, Tree) and getattr(c, 'data', '') == 'result_handlers'),
                  None)
        handlers = list(rh.children) if rh is not None else []
        name = ''
        condition = None
        if variant == 'exists':
            # CHECK_EXISTS NAME IN source match_clause handlers closer. The grammar only lists
            # match_clause here (not where_clause -- unlike CHECK_COUNT below), so this form was
            # never actually reachable with a where. WhereClause is captured anyway to match
            # check_exists_bare_block's pattern and stay correct if the grammar ever adds it.
            name_tok = next((c for c in children
                             if isinstance(c, Token) and c.type == 'NAME'), None)
            name = str(name_tok) if name_tok else ''
            condition = next((c for c in children
                              if isinstance(c, (MatchClause, WhereClause))), None)
        elif variant == 'count':
            # CHECK_COUNT (AS NAME)? IN source (where|match)? handlers closer. The grammar
            # accepts EITHER a where_clause or a match_clause here, but this only ever looked
            # for MatchClause -- a `where` condition parsed fine and was then silently dropped,
            # so `check count as n / where grp is "a"` counted the WHOLE table, not the filtered
            # rows. check_exists_bare_block already had the correct (MatchClause, WhereClause)
            # pattern; mirrored here.
            name_tok = next((c for c in children
                             if isinstance(c, Token) and c.type == 'NAME'), None)
            name = str(name_tok) if name_tok else ''
            condition = next((c for c in children
                              if isinstance(c, (MatchClause, WhereClause))), None)
        elif variant == 'unique':
            # CHECK_UNIQUE IN source MATCH_MOD NAME TO value handlers closer
            field_tok = next((c for c in children
                              if isinstance(c, Token) and c.type == 'NAME'), None)
            val = next((c for c in children
                        if type(c).__name__ in ('Literal', 'DottedName')), None)
            condition = MatchClause(modifier='unique',
                                    field=str(field_tok) if field_tok else '',
                                    value=val)
        return CheckMioqlBlock(variant=variant, name=name, source=source,
                              condition=condition, handlers=handlers)

    def check_exists_bare_block(self, children):
        # `check found in db.users / match email to "x" / check: done`
        # Same node as `check exists` — variant 'exists', binds NAME to a boolean.
        from mohio_ast import CheckMioqlBlock, MatchClause, WhereClause
        name_tok = next((c for c in children
                         if isinstance(c, Token) and c.type == 'NAME'), None)
        name = str(name_tok) if name_tok else ''
        source = next((c for c in children
                       if type(c).__name__ in ('DbRef', 'DottedName')), None)
        condition = next((c for c in children
                          if isinstance(c, (MatchClause, WhereClause))), None)
        rh = next((c for c in children
                   if isinstance(c, Tree) and getattr(c, 'data', '') == 'result_handlers'),
                  None)
        handlers = list(rh.children) if rh is not None else []
        return CheckMioqlBlock(variant='exists', name=name, source=source,
                               condition=condition, handlers=handlers)

    def each_block(self, children):
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'EACH'), None)
        open_line = _line(open_token)
        name_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'NAME'), None)
        non_tokens = [c for c in children
                      if not isinstance(c, (Token, Closer))]
        collection = non_tokens[0] if non_tokens else None
        body = non_tokens[1:]
        return EachBlock(
            item=str(name_token) if name_token else "",
            collection=collection,
            body=body, line=open_line,
        )

    def repeat_block(self, children):
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'REPEAT'), None)
        open_line = _line(open_token)
        non_tokens = [c for c in children
                      if not isinstance(c, (Token, Closer))]
        count = non_tokens[0] if non_tokens else None
        body = non_tokens[1:]
        return RepeatBlock(count=count, body=body, line=open_line)

    def repeat_each(self, children):
        # `repeat each <item> in <collection>` -- canonical collection loop.
        # `repeat` is the verb; `each` is the constraint. Builds the same
        # EachBlock the interpreter already executes, so no interpreter change.
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'REPEAT'), None)
        open_line = _line(open_token)
        name_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'NAME'), None)
        non_tokens = [c for c in children
                      if not isinstance(c, (Token, Closer))]
        collection = non_tokens[0] if non_tokens else None
        body = non_tokens[1:]
        return EachBlock(
            item=str(name_token) if name_token else "",
            collection=collection,
            body=body, line=open_line,
        )

    def while_block(self, children):
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'WHILE'), None)
        open_line = _line(open_token)
        non_tokens = [c for c in children
                      if not isinstance(c, (Token, Closer))]
        cond = non_tokens[0] if non_tokens else None
        body = non_tokens[1:]
        return WhileBlock(condition=cond, body=body, line=open_line)

    def section_block(self, children):
        path_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'PATH_LIT'), None)
        open_line = _line(path_token)
        self._validate_closer('section', children, open_line)
        body = self._body_without_closer(
            [c for c in children if not isinstance(c, Token)]
        )
        return SectionBlock(
            path=str(path_token) if path_token else "",
            body=body, line=open_line,
        )

    # -- DATA OPERATIONS ---------------------------------------

    def retrieve_block(self, children):
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'RETRIEVE'), None)
        open_line = _line(open_token)
        name_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'NAME'), None)
        source = next((c for c in children if isinstance(c, (DbRef, DottedName))), _first_tree(children, 'source_ref'))
        if source is None:
            source = next((c for c in children if isinstance(c, (DottedName, DbRef))), None)
        self._validate_closer('retrieve', children, open_line)
        # Unwrap retrieve_body subtrees -- MatchClause etc. live inside them
        from mohio_ast import MatchBlock, MatchAnyBlock, NoMatchBlock, SqlBlock
        # SqlBlock is a first-class member of the query block: the raw-SQL escape hatch
        # nests inside `retrieve`, and the enclosing retrieve names the result.
        _body_types = (MatchClause, MatchBlock, MatchAnyBlock, NoMatchBlock,
                       WhereClause, AndClause, OrderClause, LimitClause, CacheClause,
                       SqlBlock)
        body_items = []
        for c in children:
            if _is_tree(c, 'retrieve_body'):
                for item in c.children:
                    if isinstance(item, list):          # comma match -> list of MatchClause
                        body_items.extend(x for x in item if isinstance(x, _body_types))
                    elif isinstance(item, _body_types):
                        body_items.append(item)
            elif isinstance(c, list):
                body_items.extend(x for x in c if isinstance(x, _body_types))
            elif isinstance(c, _body_types):
                body_items.append(c)
        handlers_node = next((c for c in children
                              if _is_tree(c, 'result_handlers')), None)
        handlers = []
        if handlers_node:
            handlers = [c for c in handlers_node.children
                        if isinstance(c, (OnFailure, OnSuccess, OnError, CheckWhen, OtherwiseClause))]
        return RetrieveBlock(
            name=str(name_token) if name_token else "",
            source=source,
            body=body_items,
            handlers=handlers,
            line=open_line,
        )

    def retrieve_mod_block(self, children):
        # retrieve.all/.every/.one/.first/.last/.count NAME (as NAME)? from src ...
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'RETRIEVE_MOD'), None)
        open_line = _line(open_token)
        modifier = None
        if open_token is not None:
            _ot = str(open_token)
            modifier = _ot.split('.', 1)[1] if '.' in _ot else None
        # The RETRIEVE_MOD terminal now matches any `retrieve.<word>` (so an invalid
        # modifier is caught HERE as one unambiguous token, instead of silently
        # leaking to the generic dotted service-call path). Validity is a semantic
        # rule, not a grammar enumeration -- reject anything outside the closed set.
        _VALID_RETRIEVE_MODS = {'one', 'first', 'last', 'all', 'every', 'count'}
        if modifier is not None and modifier not in _VALID_RETRIEVE_MODS:
            raise MohioCompileError(
                f"Unknown retrieve modifier 'retrieve.{modifier}'.\n"
                f"Valid modifiers are: "
                f"{', '.join('retrieve.' + m for m in ['one','first','last','all','every','count'])}.\n"
                f"Use plain 'retrieve' for a single record, or one of the modifiers above.",
                line=open_line)
        name_tokens = [c for c in children if isinstance(c, Token) and c.type == 'NAME']
        name_token = name_tokens[0] if name_tokens else None
        alias = str(name_tokens[1]) if len(name_tokens) > 1 else None
        source = next((c for c in children if isinstance(c, (DbRef, DottedName))), _first_tree(children, 'source_ref'))
        if source is None:
            source = next((c for c in children if isinstance(c, (DottedName, DbRef))), None)
        # Forgiving closer: accept either `retrieve: done` or the modifier form
        # `retrieve.all: done` (normalize the latter to the base before validating).
        for _c in children:
            if isinstance(_c, Closer) and getattr(_c, 'block_name', None) \
                    and str(_c.block_name).startswith('retrieve.'):
                _c.block_name = 'retrieve'
        self._validate_closer('retrieve', children, open_line)
        from mohio_ast import MatchBlock, MatchAnyBlock, NoMatchBlock
        _body_types = (MatchClause, MatchBlock, MatchAnyBlock, NoMatchBlock,
                       WhereClause, AndClause, OrderClause, LimitClause, CacheClause)
        body_items = []
        for c in children:
            if _is_tree(c, 'retrieve_body'):
                for item in c.children:
                    if isinstance(item, list):
                        body_items.extend(x for x in item if isinstance(x, _body_types))
                    elif isinstance(item, _body_types):
                        body_items.append(item)
            elif isinstance(c, list):
                body_items.extend(x for x in c if isinstance(x, _body_types))
            elif isinstance(c, _body_types):
                body_items.append(c)
        handlers_node = next((c for c in children
                              if _is_tree(c, 'result_handlers')), None)
        handlers = []
        if handlers_node:
            handlers = [c for c in handlers_node.children
                        if isinstance(c, (OnFailure, OnSuccess, OnError, CheckWhen, OtherwiseClause))]
        return RetrieveBlock(
            name=str(name_token) if name_token else "",
            alias=alias,
            modifier=modifier,
            source=source,
            body=body_items,
            handlers=handlers,
            line=open_line,
        )

    def pull_block(self, children):
        from mohio_ast import PullBlock
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'PULL'), None)
        open_line = _line(open_token)
        # result name — a NAME immediately after the PULL verb: `pull picks up to N from ...`
        # (mirrors `retrieve NAME from ...`). up/to are keyword tokens, so the only bare NAME on
        # the head is the result binding. Absent = an unnamed pull (side-effect / handler-consumed).
        result_name = next((c for c in children
                            if isinstance(c, Token) and c.type == 'NAME'), None)
        # limit — NUMBER after "up to" (absent for the bare `pull from` form)
        num_tok = next((c for c in children
                        if isinstance(c, Token) and c.type == 'NUMBER'), None)
        limit = int(str(num_tok)) if num_tok else None
        # random flag — `pull up to N random from`
        is_random = any(isinstance(c, Token) and c.type == 'RANDOM_KW'
                        for c in children)
        # source — db table (held-list / find-result sources are a pending design item)
        source = next((c for c in children if isinstance(c, (DbRef, DottedName))), None)
        if source is None:
            sr = _first_tree(children, 'source_ref')
            if sr is not None:
                source = next((cc for cc in sr.children
                               if isinstance(cc, (DbRef, DottedName))), None)

        # body (where / order / match) — used when pulling straight from a db table
        _body_types = (MatchClause, WhereClause, AndClause, OrderClause, LimitClause)
        body_items = []
        for c in children:
            if _is_tree(c, 'pull_body'):
                for item in c.children:
                    if isinstance(item, _body_types):
                        body_items.append(item)
            elif isinstance(c, _body_types):
                body_items.append(c)
        # handlers
        handlers_node = next((c for c in children
                              if _is_tree(c, 'result_handlers')), None)
        handlers = []
        if handlers_node:
            handlers = [c for c in handlers_node.children
                        if isinstance(c, (OnFailure, OnSuccess, OnError, CheckWhen, OtherwiseClause))]
        # result binding: the name on the OPENER (`pull picks up to N from ...`), mirroring
        # `retrieve NAME from ...`. The retired closer form `pull: done as NAME` no longer binds
        # (naming goes on the action head, never on a closer).
        as_name = str(result_name) if result_name else None
        return PullBlock(limit=limit, source=source, random=is_random,
                         body=body_items, handlers=handlers,
                         as_name=as_name, line=open_line)

    def _create_field(self, create_body_tree, line):
        """Build a FieldValue from a create_body subtree (NAME + value). Nested
        create fields are not executable yet -> fail loud rather than drop."""
        from mohio_ast import FieldValue
        fname = next((str(k) for k in create_body_tree.children
                      if isinstance(k, Token) and k.type == 'NAME'), None)
        if fname is None:
            return None
        value = None
        for k in create_body_tree.children:
            if isinstance(k, Token):
                continue
            if _is_tree(k, 'create_field_body'):
                for vk in k.children:
                    if _is_tree(vk, 'create_body'):
                        raise MohioCompileError(
                            f"nested 'create' fields are not executable yet "
                            f"(field '{fname}').\nUse flat fields for now: "
                            f"'create Report' then 'name value' lines, one per field.",
                            line=line)
                    value = vk
                    break
            elif _is_tree(k, 'create_body'):
                raise MohioCompileError(
                    f"nested 'create' fields are not executable yet (field '{fname}').\n"
                    f"Use flat fields for now: 'create Report' then 'name value' lines.",
                    line=line)
            else:
                value = k
            if value is not None:
                break
        return FieldValue(name=fname, value=value)

    def then_chain(self, children):
        """head then step then step... -> ThenChain(steps=[...]).
        THEN tokens are separators; each remaining child is a chain step
        (already-transformed action/assignment/value node)."""
        from mohio_ast import ThenChain
        steps = [c for c in children
                 if not (isinstance(c, Token) and c.type == 'THEN')]
        return ThenChain(steps=steps,
                         line=_line(children[0]) if children else 0)

    def chain_unit(self, children):
        # chain_unit wraps a single action/assignment/value node -- unwrap it.
        return children[0] if children else None

    def run_async_block(self, children):
        # 'run async' parses + validates today but has no real async runtime and
        # the executor ignores the task -- a fail-late trap (dies at runtime as a
        # generic "No executor", or silently runs nothing). Fail loud at compile
        # instead, pointing to the working synchronous path. Reverse this (wire a
        # real transformer + executor) when async lands.
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'RUN'), None)
        raise MohioCompileError(
            "'run async' isn't implemented yet -- async execution is a future "
            "release.\n"
            "  Run the task synchronously for now (drop 'async'), or sequence work "
            "with a 'then' pipeline.\n"
            "  (Failing at compile so it can't silently run nothing or mislead you "
            "into thinking it ran concurrently.)",
            line=_line(open_token))

    def wait_for_stmt(self, children):
        # 'wait for' is a pure no-op today (async coordination is Phase 2). Fail
        # loud at compile rather than silently doing nothing.
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'WAIT'), None)
        raise MohioCompileError(
            "'wait for' isn't implemented yet -- async coordination is a future "
            "release.\n"
            "  It currently has no effect; run work in order (synchronously) until "
            "async lands.",
            line=_line(open_token))

    def empty_list_decl(self, children):
        """NAME as list TYPE  (no value) -> empty growable list of TYPE.
        Emits Assignment(value=None, type_name='list <elem>') which the executor
        initializes to an empty list. Distinct from `hold` (fixed)."""
        from mohio_ast import Assignment
        name_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'NAME'), None)
        # element type is the last token (TYPE_NAME / NAME / SH_REF) after LIST_KW
        elem = None
        for c in children:
            if isinstance(c, Token) and c.type in ('TYPE_NAME', 'SH_REF'):
                elem = str(c)
            elif isinstance(c, Token) and c.type == 'NAME' and c is not name_token:
                elem = str(c)
        return Assignment(name=str(name_token) if name_token else "",
                          type_name=f"list {elem}" if elem else "list",
                          value=None,
                          line=_line(name_token))

    def create_list_block(self, children):
        """create list NAME / value / ... / create: done  -> a populated list, block form.
        Same target as the inline form: Assignment(value=ListLiteral(items)). Items are the
        value_expr children; the Closer and the CREATE/LIST/NAME tokens are excluded."""
        from mohio_ast import Assignment, ListLiteral, Closer
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'CREATE'), None)
        self._validate_closer('create', children, _line(open_token))
        name_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'NAME'), None)
        items = [c for c in children
                 if not isinstance(c, Token) and not isinstance(c, Closer)]
        return Assignment(name=str(name_token) if name_token else "",
                          type_name="list",
                          value=ListLiteral(items=items),
                          line=_line(name_token))

    def list_lit_decl(self, children):
        """NAME as list VALUE, VALUE, ...  -> a list declared with its items inline.
        Reuses the proven ListLiteral evaluator: Assignment(value=ListLiteral(items)) which the
        executor evaluates to MohioValue([...], 'list'). The empty-typed form (`as list text`) is
        empty_list_decl; this one carries literal values."""
        from mohio_ast import Assignment, ListLiteral
        name_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'NAME'), None)
        items = [c for c in children if not isinstance(c, Token)]
        return Assignment(name=str(name_token) if name_token else "",
                          type_name="list",
                          value=ListLiteral(items=items),
                          line=_line(name_token))

    def create_block(self, children):
        """create NAME (as sh.Shape)? (from <src>)? / field value... / create: done
        Emits a CreateBlock node matching _exec_CreateBlock (name, shape,
        from_source, body[FieldValue]). (Was 'make' -- retired to 'create'.)"""
        from mohio_ast import CreateBlock
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'CREATE'), None)
        open_line = _line(open_token)
        name_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'NAME'), None)
        name = str(name_token) if name_token else ''
        sh_token = next((c for c in children
                         if isinstance(c, Token) and c.type == 'SH_REF'), None)
        shape = str(sh_token) if sh_token else None
        # from_source: first non-token child following a FROM token
        from_source = None
        from_idx = next((i for i, c in enumerate(children)
                         if isinstance(c, Token) and c.type == 'FROM'), None)
        if from_idx is not None:
            for c in children[from_idx + 1:]:
                if not isinstance(c, Token):
                    from_source = c
                    break
        self._validate_closer('create', children, open_line)
        body = []
        for c in children:
            if _is_tree(c, 'create_body'):
                fv = self._create_field(c, open_line)
                if fv is not None:
                    body.append(fv)
        return CreateBlock(name=name, shape=shape, from_source=from_source,
                           body=body, line=open_line)

    def make_retired_block(self, children):
        """'make' is retired -> 'create'. Fail loud (with a precise pointer)
        instead of letting `make X` silently parse as an assignment."""
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'MAKE'), None)
        raise MohioCompileError(
            "'make' is retired -- use 'create'.\n"
            "  'create' builds an object from pieces (create Report / name value / "
            "create: done).\n"
            "  'ai.create' builds one from nothing using AI.\n"
            "  Rename 'make' to 'create' (and 'make: done' to 'create: done').",
            line=_line(open_token))

    def join_block(self, children):
        # Native JOIN ('with ... from' / 'and retrieve from') parses but is not
        # yet executable. Fail loud here rather than letting find/retrieve silently
        # drop it (which would return UNJOINED rows and quietly mislead). Joins run
        # through the SQL escape hatch today; native relational JOIN is on the roadmap.
        open_token = next((c for c in children if isinstance(c, Token)
                           and c.type in ('WITH', 'WITH_REQUIRED', 'WITH_ALL')), None)
        raise MohioCompileError(
            "native JOIN ('with ... from') is not executable in this build yet.\n"
            "Run joins through the SQL escape hatch for now:\n"
            "    retrieve rows from db.parent\n"
            "        sql\n"
            "            SELECT p.*, child.x FROM parent p LEFT JOIN child ON child.parent_id = p.id\n"
            "        sql: done\n"
            "    retrieve: done\n"
            "Native relational JOIN (envelope of LEFT JOIN + aggregates) is on the roadmap.",
            line=_line(open_token))

    def find_block(self, children):
        from mohio_ast import (SummarizeBlock, CalculateBlock,
                               PaginateClause, CursorClause, SkipClause,
                               MatchBlock, MatchAnyBlock, NoMatchBlock, ExportClause,
                               TimespanRef)
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'FIND'), None)
        open_line = _line(open_token)
        name_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'NAME'), None)
        source = next((c for c in children if isinstance(c, (DbRef, DottedName))), _first_tree(children, 'source_ref'))
        self._validate_closer('find', children, open_line)
        # group_by: find <name> by <groupby_expr> in db.X
        gb_tree = _first_tree(children, 'groupby_expr')
        group_by = None
        if gb_tree is not None:
            group_by = next((str(c) for c in gb_tree.children), None)
        # Unwrap find_body subtrees
        _BODY = (WhereClause, AndClause, MatchClause, OrderClause, LimitClause, CacheClause,
                 ReturnClause, SummarizeBlock, CalculateBlock,
                 PaginateClause, CursorClause, SkipClause,
                 MatchBlock, MatchAnyBlock, NoMatchBlock, ExportClause, TimespanRef)
        body_items = []
        for c in children:
            if _is_tree(c, 'find_body'):
                for item in c.children:
                    if isinstance(item, _BODY):
                        body_items.append(item)
            elif isinstance(c, _BODY):
                body_items.append(c)
        # find ... random.N  — RANDOM_N token lands inside a find_body wrapper;
        # extract the integer (random.3 -> 3) so the executor can sample.
        random_n = None
        for c in children:
            toks = c.children if _is_tree(c, 'find_body') else ([c] if isinstance(c, Token) else [])
            for tk in toks:
                if isinstance(tk, Token) and tk.type == 'RANDOM_N':
                    try:
                        random_n = int(str(tk).split('.', 1)[1])
                    except (IndexError, ValueError):
                        random_n = None
        # find_block has no `result_handlers` in the grammar -- its handlers arrive inside
        # find_body wrappers. The _BODY allowlist above did not list them, so on.failure,
        # on.success and otherwise were SILENTLY DROPPED: they parsed, checked clean, and
        # never ran. Collect them from both places, and include OtherwiseClause (design spec:
        # otherwise is the final fallback of any verb block when no handler fired).
        from mohio_ast import OtherwiseClause as _Otherwise
        _HANDLERS = (OnFailure, OnSuccess, OnError, _Otherwise, CheckWhen)
        handlers = []
        for c in children:
            if _is_tree(c, 'result_handlers') or _is_tree(c, 'find_body'):
                handlers.extend(h for h in c.children if isinstance(h, _HANDLERS))
            elif isinstance(c, _HANDLERS):
                handlers.append(c)
        # `by day` / `by merchant by day` -- the time bucket arrives as a tagged tuple from
        # time_group_clause. It used to be discarded entirely, so every bucket compiled the same.
        time_bucket = None
        for _c in children:
            _cands = (_c.children if _is_tree(_c, 'find_body') else [_c])
            for _it in _cands:
                if isinstance(_it, tuple) and len(_it) == 3 and _it[0] == '__time_group__':
                    if _it[2]:
                        time_bucket = _it[2]
                    if _it[1] and group_by is None:
                        group_by = _it[1]
        return FindBlock(
            name=str(name_token) if name_token else "",
            group_by=group_by,
            time_bucket=time_bucket,
            source=source,
            body=body_items,
            handlers=handlers,
            random_n=random_n,
            line=open_line,
        )

    def save_block(self, children):
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'SAVE'), None)
        open_line = _line(open_token)
        # source_ref is transformed bottom-up to DbRef/DottedName before save_block runs
        source = next((c for c in children
                       if isinstance(c, (DbRef, DottedName, ShRef))), None)
        if source is None:
            source = next((c for c in children if isinstance(c, (DbRef, DottedName))), _first_tree(children, 'source_ref'))
        self._validate_closer('save', children, open_line)
        # DynamicFieldValue too -- the dynamic `name to value` save-field form. Collecting
        # only FieldValue silently DROPPED the whole field (no error, the column just never
        # got written); it is one of the three collection sites that made every
        # DynamicFieldValue executor branch unreachable dead code.
        fields = [c for c in children if isinstance(c, (FieldValue, DynamicFieldValue))]
        handlers_node = next((c for c in children
                              if _is_tree(c, 'result_handlers')), None)
        handlers = []
        if handlers_node:
            handlers = [c for c in handlers_node.children
                        if isinstance(c, (OnFailure, OnSuccess, OnError, CheckWhen, OtherwiseClause))]
        # optional `save ... as NAME` capture: the NAME token right after AS
        alias = None
        dedupe_fields = []
        for i, c in enumerate(children):
            if isinstance(c, Token) and c.type == 'AS':
                alias = next((str(c2) for c2 in children[i + 1:]
                              if isinstance(c2, Token) and c2.type == 'NAME'), None)
            elif isinstance(c, Token) and c.type == 'UNLESS':
                # `unless a, b exists` -> name_list subtree of NAME tokens. Collect ALL of
                # them: together they identify one logical row (composite dedupe key). A
                # single field is just a one-element list, so that path is unchanged.
                nl = next((c2 for c2 in children[i + 1:] if _is_tree(c2, 'name_list')), None)
                if nl is not None:
                    dedupe_fields = [str(t) for t in nl.children if isinstance(t, Token)]
                else:
                    one = next((str(c2) for c2 in children[i + 1:]
                                if isinstance(c2, Token) and c2.type == 'NAME'), None)
                    dedupe_fields = [one] if one else []
        return SaveBlock(target=source, fields=fields, handlers=handlers,
                         alias=alias, dedupe_fields=dedupe_fields, line=open_line)

    def save_all_block(self, children):
        # Build SaveAllBlock so it reaches _exec_SaveAllBlock instead of staying a
        # raw Tree (which made the executor dead code -- same bug class as cm.purge).
        # Grammar: SAVE ALL? TO <target source_ref> FROM <source value_expr>
        #          result_handlers closer  -- TO/FROM tokens survive, so extract by
        #          position (target = child after TO, source = child after FROM).
        from mohio_ast import SaveAllBlock
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'SAVE'), None)
        open_line = _line(open_token)
        self._validate_closer('save', children, open_line)
        target = source = None
        for i, c in enumerate(children):
            if isinstance(c, Token) and c.type == 'TO' and i + 1 < len(children):
                target = children[i + 1]
            elif isinstance(c, Token) and c.type == 'FROM' and i + 1 < len(children):
                source = children[i + 1]
        handlers = []
        rh = _first_tree(children, 'result_handlers')
        if rh is not None:
            # This list said only (OnSuccess, OnFailure). `when` and `otherwise` PARSED,
            # `mio check` reported no errors, and the transformer threw them away without
            # a word -- so a conditional set on a `save all` did nothing at all. Every
            # other verb block was widened for the two-stage model; this one was missed.
            # The allowlist disease: a list that does not name a thing does not fail, it
            # silently substitutes nothing.
            handlers = [h for h in rh.children
                        if isinstance(h, (OnSuccess, OnFailure, CheckWhen, OtherwiseClause))]
        return SaveAllBlock(target=target, source=source, handlers=handlers, line=open_line)

    def remove_all_spaced(self, children):
        # `remove all from db.x` (spaced) -- not valid. Fail loud rather than let
        # it silently no-op. Point to the dotted, destructive-explicit form.
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'REMOVE'), None)
        raise MohioCompileError(
            "'remove all' (with a space) isn't valid -- it would silently delete "
            "nothing.\n"
            "  To clear an entire table, use the dotted form: "
            "'remove.all from db.<table>'.\n"
            "  To delete specific rows, use 'remove from db.<table> where "
            "<field> is <value>'.")

    def remove_all_block(self, children):
        # REMOVE_ALL FROM <source_ref> (result_handlers closer)? -- destructive
        # table clear. Bare one-liner needs NO closer. Optional block form adds
        # on.success / on.failure (which self-delimit -- handlers never take their
        # own closer) and ONE block closer (canonical 'remove: done'; 'remove.all:
        # done' and bare 'done' also accepted via the verb-prefix rule). Build the
        # node so it reaches the real _exec_RemoveAllBlock (was a raw Tree = dead
        # code, and the previously-mandatory closer made the one-liner fall back
        # to a service call).
        from mohio_ast import RemoveAllBlock
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'REMOVE_ALL'), None)
        source = None
        for i, c in enumerate(children):
            if isinstance(c, Token) and c.type == 'FROM' and i + 1 < len(children):
                source = children[i + 1]; break
        if source is None:
            source = next((c for c in children if isinstance(c, (DbRef, DottedName))), None)
        handlers_node = next((c for c in children
                              if _is_tree(c, 'result_handlers')), None)
        handlers = []
        if handlers_node is not None:
            handlers = [c for c in handlers_node.children
                        if isinstance(c, (OnFailure, OnSuccess, OnError, CheckWhen, OtherwiseClause))]
            # block form -> exactly one block closer is present; validate it
            self._validate_closer('remove.all', children, _line(open_token))
        return RemoveAllBlock(source=source, handlers=handlers,
                              line=_line(open_token))

    def miomap_decl(self, children):
        # Phase-2 no-op (Zork uses miomap). Build the node so it reaches the `pass`
        # _exec_MiomapDecl instead of failing loud as a raw Tree. Full field capture
        # (from/to/fields) waits until miomap is actually wired.
        from mohio_ast import MiomapDecl
        name_tok = next((c for c in children if isinstance(c, Token) and c.type == 'NAME'), None)
        return MiomapDecl(name=str(name_tok) if name_tok else "")

    def pattern_decl(self, children):
        # Phase-2 no-op (Zork uses pattern). Build the node so it reaches the `pass`
        # _exec_PatternDecl instead of failing loud as a raw Tree.
        from mohio_ast import PatternDecl
        name_tok = next((c for c in children if isinstance(c, Token) and c.type == 'NAME'), None)
        return PatternDecl(name=str(name_tok) if name_tok else "")

    def update_body(self, children):
        """Unwrap update_body wrapper -- pass through contents."""
        return children[0] if len(children) == 1 else children

    def update_field(self, children):
        """v3.8 grammar: field value inside update block."""
        return self.save_field(children)

    def update_block(self, children):
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'UPDATE'), None)
        open_line = _line(open_token)
        source = next((c for c in children if isinstance(c, (DbRef, DottedName))), None) or _first_tree(children, 'source_ref')
        self._validate_closer('update', children, open_line)
        body = []
        for c in children:
            if isinstance(c, list):                 # comma match -> list of MatchClause
                body.extend(x for x in c
                            if isinstance(x, (MatchClause, FieldValue, DynamicFieldValue)))
            elif isinstance(c, (MatchClause, FieldValue, DynamicFieldValue)):
                body.append(c)
        handlers_node = next((c for c in children
                              if _is_tree(c, 'result_handlers')), None)
        handlers = []
        if handlers_node:
            handlers = [c for c in handlers_node.children
                        if isinstance(c, (OnFailure, OnSuccess, OnError, CheckWhen, OtherwiseClause))]
        return UpdateBlock(source=source, body=body,
                           handlers=handlers, line=open_line)

    def remove_block(self, children):
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'REMOVE'), None)
        open_line = _line(open_token)
        source = next((c for c in children if isinstance(c, (DbRef, DottedName))), None) or _first_tree(children, 'source_ref')
        # remove_condition is a match_clause OR a where_clause. Capture whichever
        # is present as the deletion condition. (Previously this only looked for a
        # MatchClause and never set `condition`, while the executor reads
        # `condition` — so every remove silently deleted nothing.)
        # remove_condition is its own subtree wrapping a match_clause or a
        # where_clause (or two where_clauses joined by AND). Its inner clauses
        # are already transformed to nodes, so look one level inside it.
        cond_items = []
        for c in children:
            if _is_tree(c, 'remove_condition'):
                cond_items.extend(c.children)
            else:
                cond_items.append(c)
        # Same list-vs-single fix as get/grab above: match_clause returns a LIST for a
        # composite match, which `isinstance(c, MatchClause)` alone never matches.
        matches = []
        for c in cond_items:
            if isinstance(c, MatchClause):
                matches.append(c)
            elif isinstance(c, list):
                matches.extend(x for x in c if isinstance(x, MatchClause))
        match  = matches[0] if len(matches) == 1 else (matches if matches else None)
        wheres = [c for c in cond_items if isinstance(c, WhereClause)]
        if len(wheres) > 1:
            raise MohioCompileError(
                "remove with multiple conditions isn't supported yet.\n"
                "  Use a single condition: 'where <field> is <value>' or "
                "'match <field> to <value>'.")
        condition = match or (wheres[0] if wheres else None)
        self._validate_closer('remove', children, open_line)
        handlers_node = next((c for c in children
                              if _is_tree(c, 'result_handlers')), None)
        handlers = []
        if handlers_node:
            handlers = [c for c in handlers_node.children
                        if isinstance(c, (OnFailure, OnSuccess, OnError, CheckWhen, OtherwiseClause))]
        return RemoveBlock(source=source, match=match, condition=condition,
                           handlers=handlers, line=open_line)

    def _single_fetch_block(self, children, open_type, node_cls):
        """Shared builder for get/grab — fetch one record by an optional match.
        Both were dropping to raw Trees (no transformer), leaving their
        executors dead. Grammar: (GET|GRAB) NAME FROM source_ref match_clause?
        result_handlers closer.
        """
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == open_type), None)
        open_line = _line(open_token)
        name_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'NAME'), None)
        source = next((c for c in children if isinstance(c, (DbRef, DottedName))),
                      _first_tree(children, 'source_ref'))
        # match_clause returns a LIST of MatchClause for multiple comma-separated pairs (a
        # composite match) -- collecting only `isinstance(c, MatchClause)` missed that list
        # entirely, so a composite `match a to X, b to Y` on get/grab silently dropped the
        # WHOLE condition to None (worse than "first field only": get/grab then treats "no
        # match" as "bind nothing", so the query silently returned nothing with no error).
        matches = []
        for c in children:
            if isinstance(c, MatchClause):
                matches.append(c)
            elif isinstance(c, list):
                matches.extend(x for x in c if isinstance(x, MatchClause))
        match = matches[0] if len(matches) == 1 else (matches if matches else None)
        self._validate_closer(open_type.lower(), children, open_line)
        handlers_node = next((c for c in children
                              if _is_tree(c, 'result_handlers')), None)
        handlers = []
        if handlers_node:
            handlers = [c for c in handlers_node.children
                        if isinstance(c, (OnFailure, OnSuccess, OnError, CheckWhen, OtherwiseClause))]
        return node_cls(name=str(name_token) if name_token else "",
                        source=source, match=match,
                        handlers=handlers, line=open_line)

    def get_block(self, children):
        from mohio_ast import GetBlock
        return self._single_fetch_block(children, 'GET', GetBlock)

    def grab_block(self, children):
        from mohio_ast import GrabBlock
        return self._single_fetch_block(children, 'GRAB', GrabBlock)

    def grab_inline(self, children):
        # grab x from t where field is value  -> GrabBlock, no handlers/closer.
        # grab is the quick one-liner retrieval verb: it works WITHOUT a closer, which is what
        # makes it a distinct verb rather than an alias of the block-form get. Uses `is` (not `to`)
        # so it reads as plain English -- `grab m from db.members where id is 5` passes the walk-by
        # test. A single equality condition; the runtime binds one record.
        from mohio_ast import GrabBlock
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'GRAB'), None)
        open_line = _line(open_token)
        name_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'NAME'), None)
        source = next((c for c in children if isinstance(c, (DbRef, DottedName))),
                      _first_tree(children, 'source_ref'))
        # the where field is the dotted_name AFTER the source; the value is the trailing expr.
        dotted = [c for c in children if isinstance(c, DottedName)]
        field_node = dotted[-1] if dotted else None
        field = '.'.join(field_node.parts) if field_node else ""
        # value is the last non-token child (the value_expr after IS)
        value = next((c for c in reversed(children)
                      if not isinstance(c, Token) and c is not field_node
                      and c is not source), None)
        match = MatchClause(field=field, value=value) if field else None
        return GrabBlock(
            name=str(name_token) if name_token else "",
            source=source,
            match=match,
            handlers=[],
            line=open_line,
        )

    def transaction_block(self, children):
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'TRANSACTION'), None)
        open_line = _line(open_token)
        self._validate_closer('transaction', children, open_line)
        body = self._body_without_closer(
            [c for c in children if not isinstance(c, Token)]
        )
        return TransactionBlock(body=body, line=open_line)

    # DATA CLAUSES

    def field_value(self, children):
        name_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'NAME'), None)
        value = next((c for c in children if not isinstance(c, Token)), None)
        return FieldValue(
            name=str(name_token) if name_token else "",
            value=value,
        )

    def retrieve_inline(self, children):
        # retrieve x from t where a to 1, b to 2  -> RetrieveBlock with one
        # MatchClause per pair (AND-ed by the interpreter), no handlers/closer.
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'RETRIEVE'), None)
        open_line = _line(open_token)
        name_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'NAME'), None)
        source = next((c for c in children if isinstance(c, (DbRef, DottedName))),
                      _first_tree(children, 'source_ref'))
        pairs = [c for c in children
                 if isinstance(c, tuple) and len(c) == 3 and c[0] == '__pair__']
        body = [MatchClause(field=name, value=value) for (_t, name, value) in pairs]
        return RetrieveBlock(
            name=str(name_token) if name_token else "",
            source=source,
            body=body,
            handlers=[],
            line=open_line,
        )

    def match_clause(self, children):
        from mohio_ast import MatchBlock, MatchAnyBlock, NoMatchBlock
        # Block forms (match / match any / no.match) arrive as a single AST node.
        for c in children:
            if isinstance(c, (MatchBlock, MatchAnyBlock, NoMatchBlock)):
                return c
        # Inline form: one or more comma-separated match_pair tuples.
        # ('__pair__', name, value).  Emit one MatchClause per pair; the
        # interpreter ANDs every MatchClause in the body, so comma-separated
        # and stacked `match` lines produce the identical multi-field WHERE.
        pairs = [c for c in children
                 if isinstance(c, tuple) and len(c) == 3 and c[0] == '__pair__']
        if pairs:
            clauses = [MatchClause(field=name, value=value)
                       for (_tag, name, value) in pairs]
            return clauses if len(clauses) > 1 else clauses[0]
        # Defensive fallback for any raw NAME TO value shape.
        name_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'NAME'), None)
        non_tokens = [c for c in children if not isinstance(c, Token)]
        value = non_tokens[1] if len(non_tokens) > 1 else (non_tokens[0] if non_tokens else None)
        return MatchClause(field=str(name_token) if name_token else "", value=value)

    def match_pair(self, children):
        name = next((str(c) for c in children
                     if isinstance(c, Token) and c.type == 'NAME'), "")
        value = next((c for c in children if not isinstance(c, Token)), None)
        return ('__pair__', name, value)

    def where_is_pair(self, children):
        # `where field is value` in an inline retrieval -> same pair shape as match_pair, but read
        # with `is` (equality/state) because a WHERE filter is a condition, not a correspondence
        # mapping. `match ... to` stays for block-form joins/mappings; `where ... is` is the filter.
        field_node = next((c for c in children if isinstance(c, DottedName)), None)
        name = '.'.join(field_node.parts) if field_node else ""
        value = next((c for c in reversed(children)
                      if not isinstance(c, Token) and c is not field_node), None)
        return ('__pair__', name, value)

    def _collect_pairs(self, children):
        return [(p[1], p[2]) for p in children
                if isinstance(p, tuple) and len(p) == 3 and p[0] == '__pair__']

    def match_block(self, children):
        from mohio_ast import MatchBlock
        return MatchBlock(pairs=self._collect_pairs(children))

    def match_any_block(self, children):
        from mohio_ast import MatchAnyBlock
        return MatchAnyBlock(pairs=self._collect_pairs(children))

    def no_match_block(self, children):
        from mohio_ast import NoMatchBlock
        return NoMatchBlock(pairs=self._collect_pairs(children))

    # Condition aliases (wc_<x>) -> canonical condition string used by the interpreter
    _WC_COND = {
        'is': 'is', 'not_is': 'is_not', 'above': 'above', 'below': 'below',
        'between': 'between', 'not_above': 'not_above', 'not_below': 'not_below',
        'contains': 'contains', 'starts': 'starts', 'starts_with': 'starts',
        'is_name': 'is', 'in_list': 'is_in', 'not_in_list': 'not_in',
        'empty': 'empty', 'not_empty': 'not_empty', 'is_empty': 'empty',
        'is_not_empty': 'not_empty',
        'older': 'older', 'newer': 'newer', 'time_in': 'is_in',
        'after': 'after', 'before': 'before',
        'above_avg': 'above_avg', 'pattern': 'pattern',
    }
    _WC_SKIP = {'IS', 'IS_NOT', 'ABOVE', 'BELOW', 'BETWEEN', 'AND', 'CONTAINS',
                'STARTS', 'STARTS_WITH', 'NOT', 'THAN', 'OLDER', 'NEWER', 'EMPTY',
                'OF', 'SINCE', 'STR_AFTER', 'STR_BEFORE', 'IS_IN'}
    # STR_AFTER/STR_BEFORE are the `after`/`before` tokens; in a `<field> is after <value>`
    # datetime comparison they are OPERATOR keywords (aliases for above/below), not values, so
    # they must be skipped when collecting the comparison value -- exactly like ABOVE/BELOW.
    # (Their string-slice meaning `text after "x"` lives in a different rule, not where_condition.)

    def _build_where(self, children, cls):
        # children: [WHERE token, wc_* subtree]. The wc_* subtree is not itself
        # transformed (no per-alias method), so unwrap it here to get field +
        # condition + value(s). Its dotted_name/value_expr children ARE transformed.
        wc = next((c for c in children
                   if isinstance(c, Tree) and str(c.data).startswith('wc_')), None)
        if wc is None:
            # Fallback: direct children (older grammar shapes)
            non_tokens = [c for c in children if not isinstance(c, Token)]
            fd = next((c for c in non_tokens if isinstance(c, DottedName)), None)
            return cls(field='.'.join(fd.parts) if fd else "",
                       value=non_tokens[1] if len(non_tokens) > 1 else None)
        condition = self._WC_COND.get(str(wc.data)[3:], str(wc.data)[3:])
        field_dotted = next((c for c in wc.children if isinstance(c, DottedName)), None)
        field = '.'.join(field_dotted.parts) if field_dotted else ""
        values = []
        for c in wc.children:
            if c is field_dotted:
                continue
            if isinstance(c, Token):
                if c.type in self._WC_SKIP:
                    continue
                values.append(c)          # STRING / NAME literal etc.
            else:
                values.append(c)          # value_expr / value_list node
        return cls(field=field, condition=condition,
                   value=values[0] if values else None,
                   value2=values[1] if len(values) > 1 else None)

    def where_clause(self, children):
        return self._build_where(children, WhereClause)

    def and_clause(self, children):
        return self._build_where(children, AndClause)

    # dt_point -- the one resolvable time operand. Each alias builds a node the interpreter's
    # _eval_time / datetime resolver already understands (TimeExpr for now()/anchors with an
    # optional offset; DatetimeExpr for a literal date).
    def dt_anchor_word(self, children):
        tok = next((c for c in children if isinstance(c, Token)), None)
        return str(tok).lower() if tok is not None else 'now()'

    def dt_now_offset(self, children):
        dur = next((c for c in children if not isinstance(c, Token)), None)
        return TimeExpr(base='now()', offset_op='-', offset=dur)

    def dt_anchor_offset(self, children):
        # dt_anchor_word already reduced to a lowercase string; the duration is the DurationExpr.
        word = next((c for c in children if isinstance(c, str)), None)
        dur = next((c for c in children if not isinstance(c, (str, Token))), None)
        return TimeExpr(base=word or 'now()', offset_op='-', offset=dur)

    def dt_anchor_bare(self, children):
        word = next((c for c in children if isinstance(c, str)), None)
        return TimeExpr(base=word or 'now()')

    def dt_date_lit(self, children):
        date_tok = next((c for c in children if isinstance(c, Token)), None)
        return DatetimeExpr(date=str(date_tok), time=None)

    def dt_bare_duration(self, children):
        # A bare duration in a datetime comparison means "<duration> ago" == now() - duration.
        dur = next((c for c in children if not isinstance(c, Token)), None)
        return TimeExpr(base='now()', offset_op='-', offset=dur)

    def since_anchor(self, children):
        # since_anchor now just wraps a single dt_point result.
        return children[0] if children else TimeExpr(base='now()')

    def time_constant(self, children):
        """A calendar time_constant token (TODAY/YESTERDAY/LAST_WEEK/THIS_MONTH/...) -> a
        calendar TimePeriod, used by `is.in <period>`. (Section 2, 2026-08-01: before this,
        time_period_expr/time_constant had NO transformer, so `is.in <period>` parsed and then
        failed at runtime -- 'no rule to compute its value'.)"""
        from mohio_ast import TimePeriod
        tok = next((c for c in children if isinstance(c, Token)), None)
        return TimePeriod(calendar=str(tok).lower() if tok else None,
                          line=_line(tok) if tok else 0)

    def time_period_expr(self, children):
        """Resolve a time_period_expr to a TimePeriod. `last N <unit>` -> rolling; a time_constant
        (already a TimePeriod) passes through; `this <name>` / `last <name>` -> calendar with the
        <name> validated -- an unrecognized period FAILS LOUD, never silently guesses."""
        from mohio_ast import TimePeriod, DurationExpr
        tp = next((c for c in children if type(c).__name__ == 'TimePeriod'), None)
        if tp is not None:
            return tp
        dur = next((c for c in children if isinstance(c, DurationExpr)), None)
        if dur is not None:
            return TimePeriod(rolling=dur)
        toks = [c for c in children if isinstance(c, Token)]
        this_tok = next((t for t in toks if t.type == 'THIS' or str(t).lower() == 'this'), None)
        name_tok = next((t for t in toks if t.type == 'NAME'), None)
        name = str(name_tok).lower() if name_tok else ''
        prefix = 'this' if this_tok is not None else 'last'
        if name not in ('week', 'month', 'quarter', 'year'):
            raise MohioCompileError(
                f"`is.in {prefix} {name}` is not a valid time period. After `{prefix}` use one of "
                f"week, month, quarter, year (e.g. `is.in {prefix} month`), or a rolling window "
                f"`is.in last N hours/days/...`.")
        return TimePeriod(calendar=f"{prefix}_{name}",
                          line=_line(name_tok) if name_tok else 0)

    def since_clause(self, children):
        return self._time_clause(children, 'since')

    def from_clause(self, children):
        return self._time_clause(children, 'from')

    def _time_clause(self, children, condition):
        # `<field> since|from <point>` -> WhereClause(condition): keep rows whose field is at or
        # after the resolved point, up to now. Inclusive (>=). Explicit field, no column guessing.
        field_dotted = next((c for c in children if isinstance(c, DottedName)), None)
        field = '.'.join(field_dotted.parts) if field_dotted else ""
        anchor = next((c for c in children
                       if c is not field_dotted and not isinstance(c, Token)), None)
        return WhereClause(field=field, condition=condition, value=anchor, value2=None)

    # Each `and ...` condition is its own aliased rule (and_above, and_is, ...)
    # with no wrapping node, so build AndClause directly from the alias children.
    def _and_cond(self, children, condition):
        fd = next((c for c in children if isinstance(c, DottedName)), None)
        field = '.'.join(fd.parts) if fd else ""
        values = [c for c in children
                  if c is not fd and not (isinstance(c, Token) and c.type in self._WC_SKIP)]
        return AndClause(field=field, condition=condition,
                         value=values[0] if values else None,
                         value2=values[1] if len(values) > 1 else None)

    def and_above(self, c):       return self._and_cond(c, 'above')
    def and_below(self, c):       return self._and_cond(c, 'below')
    def and_between(self, c):     return self._and_cond(c, 'between')
    def and_time_in(self, c):     return self._and_cond(c, 'is_in')
    def and_older(self, c):       return self._and_cond(c, 'older')
    def and_newer(self, c):       return self._and_cond(c, 'newer')
    def and_after(self, c):       return self._and_cond(c, 'after')
    def and_before(self, c):      return self._and_cond(c, 'before')
    def and_empty(self, c):       return self._and_cond(c, 'empty')
    def and_not_empty(self, c):   return self._and_cond(c, 'not_empty')
    def and_contains(self, c):    return self._and_cond(c, 'contains')
    def and_starts(self, c):      return self._and_cond(c, 'starts')
    def and_is(self, c):          return self._and_cond(c, 'is')
    def and_is_not(self, c):      return self._and_cond(c, 'is_not')
    def and_in_list(self, c):     return self._and_cond(c, 'is_in')
    def and_not_in_list(self, c): return self._and_cond(c, 'not_in')
    def and_since(self, c):       return self._and_cond(c, 'since')

    def order_clause(self, children):
        dotted = next((c for c in children if isinstance(c, DottedName)), None)
        direction = 'up'
        for c in children:
            if isinstance(c, Token):
                if c.type == 'ORDER_DOWN':
                    direction = 'down'
                elif c.type == 'ORDER_UP':
                    direction = 'up'
        return OrderClause(
            field='.'.join(dotted.parts) if dotted else "",
            direction=direction,
        )

    def limit_clause(self, children):
        num = next((c for c in children
                    if isinstance(c, Token) and c.type == 'NUMBER'), None)
        return LimitClause(count=_coerce_number(str(num)) if num else None)

    def cache_clause(self, children):
        dur = next((c for c in children if isinstance(c, DurationExpr)), None)
        return CacheClause(duration=dur)

    def paginate_clause(self, children):
        # paginate by NUMBER  — 1-based page number
        from mohio_ast import PaginateClause
        num = next((c for c in children
                    if isinstance(c, Token) and c.type == 'NUMBER'), None)
        return PaginateClause(count=int(str(num)) if num is not None else 1)

    def skip_clause(self, children):
        # skip NUMBER  — leading offset (skip the first N rows)
        from mohio_ast import SkipClause
        num = next((c for c in children
                    if isinstance(c, Token) and c.type == 'NUMBER'), None)
        return SkipClause(count=int(str(num)) if num is not None else 0)

    def export_clause(self, children):
        # export as.csv | as.json | as.pdf | as.xlsx to "path"
        from mohio_ast import ExportClause
        fmt = 'csv'
        for c in children:
            if isinstance(c, Token):
                if   c.type == 'AS_CSV':  fmt = 'csv'
                elif c.type == 'AS_JSON': fmt = 'json'
                elif c.type == 'AS_PDF':  fmt = 'pdf'
                elif str(c).strip() == 'as.xlsx': fmt = 'xlsx'
        target = next((c for c in children if not isinstance(c, Token)), None)
        return ExportClause(format=fmt, target=target)

    def cursor_clause(self, children):
        # cursor from <dotted_name>  — source is an evaluable reference
        from mohio_ast import CursorClause
        src = next((c for c in children if isinstance(c, DottedName)), None)
        return CursorClause(source=src)

    def timespan_ref_clause(self, children):
        from mohio_ast import TimespanRef
        name = next((str(c) for c in children
                     if isinstance(c, Token) and c.type == 'NAME'), "")
        return TimespanRef(name=name)

    def source_ref(self, children):
        token = children[0] if children else None
        if isinstance(token, Token):
            if token.type == 'DB_REF':
                table = str(token).replace('db.', '')
                return DbRef(table=table)
            return DottedName(parts=[str(token)])
        return token  # already transformed

    def result_handlers(self, children):
        # Return as a tree for the parent to extract
        return Tree('result_handlers', children)

    def result_handler(self, children):
        return children[0] if children else None

    def on_failure_handler(self, children):
        # Every non-Token child is part of the handler body: the optional
        # inline_action (give_back/show/jump) AND any statement* that follows
        # (e.g. a nested retrieve). The previous "inline" heuristic mistook the
        # first nested statement for an inline action and silently dropped it,
        # which made a retrieve nested inside on.failure vanish from the AST.
        body = [c for c in children if not isinstance(c, Token)]
        return OnFailure(body=body)

    def on_success_handler(self, children):
        body = [c for c in children if not isinstance(c, Token)]
        return OnSuccess(body=body)

    def on_error_handler(self, children):
        body = [c for c in children if not isinstance(c, Token)]
        return OnError(body=body)

    # -- VALIDATION (miovalidate rule sets + validate application) --------
    def miovalidate_decl(self, children):
        from mohio_ast import MiovalidateDecl, MiovalidateRule
        name_tok = next((c for c in children
                         if isinstance(c, Token) and c.type == 'NAME'), None)
        rules = [c for c in children if isinstance(c, MiovalidateRule)]
        return MiovalidateDecl(name=str(name_tok) if name_tok else "",
                               rules=rules, line=_line(name_tok))

    def miovalidate_rule(self, children):
        from mohio_ast import MiovalidateRule
        field_name = ""
        type_name  = ""
        modifiers  = []
        for c in children:
            if isinstance(c, Token):
                if c.type == 'NAME' and not field_name:
                    field_name = str(c)
            elif isinstance(c, str):
                if not type_name:
                    type_name = c
            elif _is_tree(c, 'type_name'):
                type_name = " ".join(str(t) for t in c.children
                                     if isinstance(t, Token)) or type_name
            elif isinstance(c, dict):
                modifiers.append(c)
        return MiovalidateRule(field_name=field_name,
                               type_name=type_name, modifiers=modifiers)

    def miovalidate_mod(self, children):
        toks = [c for c in children if isinstance(c, Token)]
        types = {t.type for t in toks}
        nums  = [int(str(t)) for t in toks if t.type == 'NUMBER']
        if 'OPTIONAL' in types: return {'kind': 'optional'}
        if 'REQUIRED' in types: return {'kind': 'required'}
        if 'UNIQUE'   in types: return {'kind': 'unique'}
        name_tok = next((t for t in toks if t.type == 'NAME'), None)
        if name_tok and not nums:
            return {'kind': 'scheme', 'name': str(name_tok)}
        if len(nums) >= 2:
            if 'AND' in types:                 # between MIN and MAX  (value range)
                return {'kind': 'between', 'min': nums[0], 'max': nums[1]}
            return {'kind': 'length', 'min': nums[0], 'max': nums[1]}  # length MIN to MAX
        return {'kind': 'unknown'}

    def validate_stmt(self, children):
        from mohio_ast import ValidateStmt
        name_toks = [c for c in children
                     if isinstance(c, Token) and c.type == 'NAME']
        handlers  = [c for c in children if isinstance(c, (OnFailure, OnSuccess))]
        # An 'against' source is any AST node that isn't a handler/closer.
        source_expr = next((c for c in children
                            if not isinstance(c, Token)
                            and not isinstance(c, (OnFailure, OnSuccess, Closer))), None)
        first_line = _line(name_toks[0]) if name_toks else 0
        if source_expr is not None:                       # validate <rules> against <data>
            return ValidateStmt(variant='against',
                                rules_name=str(name_toks[0]) if name_toks else "",
                                source=source_expr, handlers=handlers, line=first_line)
        if len(name_toks) >= 2:                            # validate <data> using <rules>
            return ValidateStmt(variant='using', rules_name=str(name_toks[1]),
                                source=str(name_toks[0]), handlers=handlers, line=first_line)
        return ValidateStmt(variant='using',              # validate using <rules>  (source = request)
                            rules_name=str(name_toks[0]) if name_toks else "",
                            source=None, handlers=handlers, line=first_line)

    def validate_handler(self, children):
        return next((c for c in children
                     if isinstance(c, (OnFailure, OnSuccess))), None)

    # -- RETURN CLAUSE (field projection + aggregates) -------------------
    def return_clause(self, children):
        from mohio_ast import ReturnClause
        fields = [c for c in children if isinstance(c, dict)]
        return ReturnClause(fields=fields)

    def return_field(self, children):
        # dotted_name is already a DottedName; the loose NAME token is the alias.
        dn    = next((c for c in children if isinstance(c, DottedName)), None)
        parts = dn.parts if dn else []
        # 'average' is a reserved terminal (AVERAGE), so it does not fold into the
        # dotted_name like sum/count/max/min do — catch it as a trailing token.
        avg_tok = next((c for c in children
                        if isinstance(c, Token) and c.type == 'AVERAGE'), None)
        alias_tok = next((c for c in children
                          if isinstance(c, Token) and c.type == 'NAME'), None)
        alias = str(alias_tok) if alias_tok else None
        AGG = {'sum', 'count', 'average', 'max', 'min'}
        if avg_tok is not None:                           # field.average
            return {'kind': 'agg', 'func': 'average',
                    'field': ".".join(parts) or None,
                    'alias': alias or ("_".join(parts + ['average']) if parts else 'average')}
        if len(parts) == 1 and parts[0] in AGG:           # bare: count
            return {'kind': 'agg', 'func': parts[0], 'field': None,
                    'alias': alias or parts[0]}
        if len(parts) >= 2 and parts[-1] in AGG:          # field.sum / field.max
            return {'kind': 'agg', 'func': parts[-1], 'field': ".".join(parts[:-1]),
                    'alias': alias or "_".join(parts)}
        return {'kind': 'field', 'field': ".".join(parts),  # plain projection: id, name
                'alias': alias or (parts[-1] if parts else "")}

    # -- AGGREGATE FUNCTIONS (summarize / calculate) --------------------
    def _af(self, children, func):
        names  = [str(c) for c in children if isinstance(c, Token) and c.type == 'NAME']
        nums   = [str(c) for c in children if isinstance(c, Token) and c.type == 'NUMBER']
        dotted = next((c for c in children if isinstance(c, DottedName)), None)
        spec = {'func': func,
                'source': names[0] if names else ('.'.join(dotted.parts) if dotted else None)}
        if func == 'rank' and len(names) >= 2:
            spec['partition'] = names[1]
        if func == 'moving_average':
            spec['window'] = int(nums[0]) if nums else None
            if len(names) >= 2:
                spec['window_unit'] = names[1]
        if func == 'percentile' and nums:
            spec['p'] = float(nums[0])
        if func == 'percentage_of' and dotted:
            spec['source'] = '.'.join(dotted.parts)
        return spec

    def af_sum(self, c):            return self._af(c, 'sum')
    def af_count(self, c):          return self._af(c, 'count')
    def af_average(self, c):        return self._af(c, 'average')
    def af_max(self, c):            return self._af(c, 'max')
    def af_min(self, c):            return self._af(c, 'min')
    def af_running_sum(self, c):    return self._af(c, 'running_sum')
    def af_moving_average(self, c): return self._af(c, 'moving_average')
    def af_rank(self, c):           return self._af(c, 'rank')
    def af_std_deviation(self, c):  return self._af(c, 'std_deviation')
    def af_variance(self, c):       return self._af(c, 'variance')
    def af_percentile(self, c):     return self._af(c, 'percentile')
    def af_p_value(self, c):        return self._af(c, 'p_value')
    def af_cohens_d(self, c):       return self._af(c, 'cohens_d')
    def af_percentage_of(self, c):  return self._af(c, 'percentage_of')

    def agg_field(self, children):
        from mohio_ast import AggField
        result_name = next((str(c) for c in children
                            if isinstance(c, Token) and c.type == 'NAME'), "")
        spec = next((c for c in children if isinstance(c, dict)), {})
        return AggField(name=result_name, function=spec.get('func', ''), arg=spec)

    def summarize_block(self, children):
        from mohio_ast import SummarizeBlock, AggField
        fields = [c for c in children if isinstance(c, AggField)]
        return SummarizeBlock(fields=fields)

    def calc_field(self, children):
        from mohio_ast import AggField
        result_name = next((str(c) for c in children
                            if isinstance(c, Token) and c.type == 'NAME'), "")
        spec = next((c for c in children if isinstance(c, dict)), None)
        if spec is not None:                          # NAME agg_func
            return AggField(name=result_name, function=spec.get('func', ''), arg=spec)
        operands = [c for c in children                # NAME value_expr MINUS value_expr
                    if not isinstance(c, (Token, dict))]
        return AggField(name=result_name, function='difference',
                        arg={'left': operands[0] if operands else None,
                             'right': operands[1] if len(operands) > 1 else None})

    def calculate_block(self, children):
        from mohio_ast import CalculateBlock, AggField
        fields = [c for c in children if isinstance(c, AggField)]
        return CalculateBlock(fields=fields)

    # -- AI PRIMITIVES -----------------------------------------

    def ai_decide_block(self, children):
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'AI_DECIDE'), None)
        open_line = _line(open_token)
        name_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'NAME'), None)
        name = str(name_token) if name_token else ""
        type_node = _first_tree(children, 'type_name')
        return_type = _token_str(type_node) if type_node else "any"
        self._validate_closer('ai.decide', children, open_line)

        # Extract ai_prompt_opts (goal/persona/context/temperature/model)
        # before building body so they don't fall through to statement parsing
        all_body = [c for c in children
                    if not isinstance(c, Token)
                    and not _is_tree(c, 'type_name')]
        ai_opts, remaining = self._extract_ai_opts(all_body)
        body = self._body_without_closer(remaining)

        # `threshold N` was invented by a Jun 30 compiler chat to replace a form it wrongly
        # believed retired. It was never designed. `threshold` is a SHAPE field modifier
        # (like min/max), not a confidence form. It matches no ai.decide rule, so it fell
        # through to `statement` and became a VARIABLE named `threshold` -- while the real
        # threshold silently stayed at the hardcoded 0.85. A fraud model asking for 0.99
        # got 0.85 and was told nothing.
        for b in body:
            if type(b).__name__ == 'Assignment' and str(getattr(b, 'name', '')) in (
                    'threshold', 'confidence'):
                raise MohioCompileError(
                    f'Line {getattr(b, "line", 0) or open_line} -- `{b.name}` is not a '
                    f'confidence form inside ai.decide.\n'
                    f'    It declared a VARIABLE called `{b.name}`, and the real gate '
                    f'silently stayed at its default.\n'
                    f'    The form is:  check confidence above 0.99')

        has_not_confident = any(isinstance(b, NotConfidentBlock) for b in body)
        if not has_not_confident:
            raise MohioCompileError(
                f'ai.decide "{name}" is missing a "not confident" block.\n'
                f'Every ai.decide must define what happens when confidence '
                f'falls below threshold.\n'
                f'Add a "not confident" block inside this ai.decide.',
                line=open_line
            )

        return AiDecideBlock(
            name=name, return_type=return_type, body=body, line=open_line,
            goal=ai_opts['goal'], persona=ai_opts['persona'],
            context=ai_opts['context'], temperature=ai_opts['temperature'],
            model=ai_opts['model'],
        )

    def ai_compare_block(self, children):
        # ai.compare <name> ... ai.compare: done
        # Relational judgment. Shares ai_decide_body, so the same goal/persona/context/
        # temperature/model options apply. Binds { winner, margin, explanation } to <name>.
        from mohio_ast import AiCompareBlock
        return self._ai_decide_shaped_block(children, 'AI_COMPARE', 'ai.compare',
                                            AiCompareBlock)

    def ai_respond_block(self, children):
        # ai.respond <name> ... ai.respond: done
        # Interaction response (support, chat, narration). Shares ai_decide_body.
        from mohio_ast import AiRespondBlock
        return self._ai_decide_shaped_block(children, 'AI_RESPOND', 'ai.respond',
                                            AiRespondBlock)

    def _ai_decide_shaped_block(self, children, token_type, verb, node_cls):
        """Shared build for the ai.* blocks that reuse ai_decide_body (compare, respond).

        Same head shape as ai.decide (NAME + optional return type + prompt options + body),
        without ai.decide's threshold/not-confident requirements: neither compare nor respond
        is a gated decision, so demanding a confidence gate on them would be enforcement
        theatre rather than a real guarantee.
        """
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == token_type), None)
        open_line = _line(open_token)
        name_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'NAME'), None)
        name = str(name_token) if name_token else ""
        type_node = _first_tree(children, 'type_name')
        return_type = _token_str(type_node) if type_node else "text"
        self._validate_closer(verb, children, open_line)

        all_body = [c for c in children
                    if not isinstance(c, Token)
                    and not _is_tree(c, 'type_name')]
        ai_opts, remaining = self._extract_ai_opts(all_body)
        body = self._body_without_closer(remaining)

        return node_cls(
            name=name, return_type=return_type, body=body, line=open_line,
            goal=ai_opts['goal'], persona=ai_opts['persona'],
            context=ai_opts['context'], temperature=ai_opts['temperature'],
            model=ai_opts['model'],
        )

    def rank_weight(self, children):
        vals = [c for c in children if not isinstance(c, Token)]
        return ('__weight__', vals[-1] if vals else None)

    def rank_opt(self, children):
        weight = None
        condition = None
        rest = []
        for c in children:
            if isinstance(c, tuple) and len(c) == 2 and c[0] == '__weight__':
                weight = c[1]
            elif isinstance(c, (Condition, NotCondition, AndCondition, OrCondition)):
                condition = c
            elif isinstance(c, Token):
                continue
            else:
                rest.append(c)
        return RankOption(value=(rest[0] if rest else None),
                          condition=condition, weight=weight, is_default=False)

    def rank_default(self, children):
        weight = None
        rest = []
        for c in children:
            if isinstance(c, tuple) and len(c) == 2 and c[0] == '__weight__':
                weight = c[1]
            elif isinstance(c, Token):
                continue
            else:
                rest.append(c)
        return RankOption(value=(rest[0] if rest else None),
                          weight=weight, is_default=True)

    def rank_confidence(self, children):
        vals = [c for c in children if not isinstance(c, Token)]
        return ('__confidence__', vals[-1] if vals else None)

    def ai_rank_block(self, children):
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'AI_RANK'), None)
        open_line = _line(open_token)
        name_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'NAME'), None)
        name = str(name_token) if name_token else ""
        type_node = _first_tree(children, 'type_name')
        return_type = _token_str(type_node) if type_node else "text"
        self._validate_closer('ai.rank', children, open_line)
        options = [c for c in children if isinstance(c, RankOption)]
        confidence = next((c[1] for c in children
                           if isinstance(c, tuple) and len(c) == 2
                           and c[0] == '__confidence__'), None)
        not_confident = next((c for c in children
                              if isinstance(c, NotConfidentBlock)), None)
        audit = next((c for c in children if isinstance(c, AiAuditStmt)), None)
        explain = next((c for c in children if isinstance(c, AiExplainBlock)), None)
        # The FOR subject is the leftover value node (not a token, not a body piece).
        known = (RankOption, NotConfidentBlock, AiAuditStmt, AiExplainBlock, Closer)
        subject = next((c for c in children
                        if not isinstance(c, Token)
                        and not _is_tree(c, 'type_name')
                        and not isinstance(c, known)
                        and not isinstance(c, tuple)), None)
        return AiRankBlock(name=name, subject=subject, options=options,
                           return_type=return_type,
                           confidence=confidence, not_confident=not_confident,
                           audit=audit, explain=explain, line=open_line)

    def ai_decide_invoke(self, children):
        name_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'NAME'), None)
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'AI_DECIDE'), None)
        return AiDecideInvoke(name=str(name_token) if name_token else "",
                              line=_line(open_token))

    # -- Shared ai.* option extractor ----------------------------

    def _extract_ai_opts(self, children):
        """
        Extract goal/persona/context/temperature/model from ai_opt_* alias nodes.
        Uses Lark tree.data aliases (ai_opt_goal, ai_opt_persona etc) to identify
        which option was matched -- no keyword tokens to search for.
        Returns dict of extracted values and list of remaining body items.
        """
        goal = persona = context = model = ""
        temperature = None
        body = []
        def _is_ai_opt(child):
            # After bottom-up transform, ai_opt_* returns ('ai_opt', KEY, val) tuples
            return isinstance(child, tuple) and len(child) == 3 and child[0] == 'ai_opt'

        for child in children:
            if _is_ai_opt(child):
                _, key, val = child
                if key == 'goal':        goal = str(val)
                elif key == 'persona':   persona = str(val)
                elif key == 'context':   context = str(val)
                elif key == 'model':     model = str(val)
                elif key == 'temperature': temperature = float(val) if val else 1.0
            else:
                body.append(child)
        return dict(goal=goal, persona=persona, context=context,
                    temperature=temperature, model=model), body

    def ai_prompt_opts(self, children):
        """Alias nodes handle extraction -- this is a pass-through stub."""
        return children

    # ai_opt_* transformers return tagged tuples so they survive
    # bottom-up transformation and can be identified in parent blocks.
    # Format: ('ai_opt', 'KEY', value_string)

    def _opt_val(self, children):
        """Extract string value from any child -- Token, Tree, or Mohio AST node."""
        for child in children:
            # Mohio AST node (Literal, DottedName etc)
            if hasattr(child, 'value'):
                return str(child.value).strip('"')
            elif isinstance(child, Token):
                return str(child).strip('"')
            elif hasattr(child, 'children'):
                v = self._opt_val(child.children)
                if v: return v
        return ""

    def ai_opt_goal(self, children):
        return ('ai_opt', 'goal', self._opt_val(children))
    def ai_opt_persona(self, children):
        return ('ai_opt', 'persona', self._opt_val(children))
    def ai_opt_context(self, children):
        return ('ai_opt', 'context', self._opt_val(children))
    def ai_opt_temperature(self, children):
        val = self._opt_val(children)
        try: return ('ai_opt', 'temperature', float(val))
        except: return ('ai_opt', 'temperature', 1.0)
    def ai_opt_max_tokens(self, children):
        val = self._opt_val(children)
        try: return ('ai_opt', 'max_tokens', int(val))
        except: return ('ai_opt', 'max_tokens', 1000)
    def ai_opt_model(self, children):
        return ('ai_opt', 'model', self._opt_val(children).strip('"'))

    def ai_decide_body(self, children):
        return children[0] if children else None

    def confidence_check(self, children):
        value = next((c for c in children if not isinstance(c, Token)), None)
        return ConfidenceCheck(operator='above', threshold=value)

    def using_chain(self, children):
        name = next((str(c) for c in children
                     if isinstance(c, Token) and c.type == 'NAME'), "")
        return UsingChain(chain_name=name)

    def weigh_clause(self, children):
        weigh_list = _first_tree(children, 'weigh_list')
        inputs = []
        if weigh_list:
            inputs = [c for c in weigh_list.children if isinstance(c, DottedName)]
        return WeighClause(inputs=inputs)

    def not_confident_block(self, children):
        body = [c for c in children if not isinstance(c, Token)]
        return NotConfidentBlock(body=body)

    def ai_audit_stmt(self, children):
        name = next((str(c) for c in children
                     if isinstance(c, Token) and c.type == 'NAME'), "")
        return AiAuditStmt(log_name=name)

    def ai_explain_block(self, children):
        ai_opts, remaining = self._extract_ai_opts(children)
        opts = _filter_trees(remaining, 'ai_explain_opt')
        audience = None
        for opt in opts:
            tokens = [c for c in opt.children if isinstance(c, Token)]
            kw = next((str(t) for t in tokens
                       if t.type in ('AUDIENCE', 'FORMAT')), "")
            val = next((str(t).strip('"') for t in tokens
                        if t.type == 'STRING'), "")
            if kw == 'audience':
                audience = val
        # Grammar: AI_EXPLAIN NAME (AS NAME)? -- first NAME is the decision to
        # explain, the optional NAME after AS is where the explanation is bound.
        name_toks = [c for c in remaining
                     if isinstance(c, Token) and c.type == 'NAME']
        decision_name = str(name_toks[0]) if len(name_toks) > 0 else None
        alias = str(name_toks[1]) if len(name_toks) > 1 else None
        return AiExplainBlock(decision_name=decision_name,
                              alias=alias, audience=audience)

    def _ai_chain_block_retired(self, children):  # retired -- ai_connect_block replaces this
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'AI_CONNECT'), None)
        open_line = _line(open_token)
        name_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'NAME'), None)
        name = str(name_token) if name_token else ""
        self._validate_closer('ai.chain', children, open_line)
        steps = []  # retired -- ai.chain replaced by ai.connect
        return AiConnectBlock(names=[name] if name else [], steps=steps, line=open_line)

    def ai_chain_step(self, children):
        name_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'NAME'), None)
        provider = str(name_token) if name_token else ""
        body = [c for c in children if not isinstance(c, Token)]
        return None  # retired

    def ai_chain_step_body(self, children):
        return children[0] if children else None

    def ai_create_stmt(self, children):
        from mohio_ast import AiCreateStmt
        from lark import Token
        # modality: stmt form uses ai_create_type; block form uses `returns type_name`
        type_node = _first_tree(children, 'ai_create_type')
        if type_node:
            create_type = (_token_str(type_node) or "").lower()
        else:
            tn = _first_tree(children, 'type_name')
            create_type = (_token_str(tn) or "").lower() if tn else ""
        # name + alias: top-level NAME tokens are [block name, `as NAME` alias]
        name_toks = [c for c in children if isinstance(c, Token) and c.type == 'NAME']
        name  = str(name_toks[0]) if name_toks else ""
        alias = str(name_toks[1]) if len(name_toks) > 1 else ""
        # flatten ai_create_full_body wrappers so opts/attrs sit at top level
        flat = []
        for c in children:
            if _is_tree(c, 'ai_create_full_body'):
                flat.extend(c.children)
            else:
                flat.append(c)
        opts, remaining = self._extract_ai_opts(flat)

        def _num(s):
            try:
                return float(s)
            except (TypeError, ValueError):
                return None

        style = negative = size = voice = source = ""
        pace = duration = None
        attrs = {}
        body = []
        def _valstr(x):
            # Stringify a hint/source value: a dotted reference like report.key_findings
            # becomes "report.key_findings"; otherwise fall back to the opt value.
            if hasattr(x, 'parts'):
                return '.'.join(str(p) for p in x.parts)
            v = self._opt_val([x])
            return v if v else (str(x) if isinstance(x, (str, Token)) else "")
        for item in remaining:
            if   _is_tree(item, 'create_style'):    style    = self._opt_val(item.children)
            elif _is_tree(item, 'create_negative'): negative = self._opt_val(item.children)
            elif _is_tree(item, 'create_size'):     size     = self._opt_val(item.children)
            elif _is_tree(item, 'create_voice'):    voice    = self._opt_val(item.children)
            elif _is_tree(item, 'create_pace'):     pace     = _num(self._opt_val(item.children))
            elif _is_tree(item, 'create_duration'): duration = _num(self._opt_val(item.children))
            elif _is_tree(item, 'create_source'):   source   = self._opt_val(item.children)
            elif _is_tree(item, 'create_template'): attrs['template'] = self._opt_val(item.children)
            elif _is_tree(item, 'name_value_pair'):
                kv = item.children
                if kv:
                    v = self._opt_val(kv[1:]) if len(kv) > 1 else ""
                    if not v and len(kv) > 1:
                        v = _valstr(kv[1])
                    attrs[str(kv[0])] = v
            elif _is_tree(item, 'ai_create_type') or _is_tree(item, 'type_name'):
                pass
            elif isinstance(item, Token):
                pass
            elif isinstance(item, Closer):
                pass   # block closer -- not a body statement
            elif hasattr(item, 'parts') and not source:
                # A bare dotted reference in the block is the `from SOURCE` object.
                source = '.'.join(str(p) for p in item.parts)
            else:
                body.append(item)
        return AiCreateStmt(
            create_type=create_type, name=name, alias=alias, return_type=create_type,
            goal=opts['goal'], persona=opts['persona'], context=opts['context'],
            temperature=opts['temperature'], model=opts['model'],
            style=style, negative=negative, size=size, voice=voice,
            pace=pace, duration=duration, source=source, attrs=attrs, body=body)

    def ai_create_type(self, children):
        return children[0] if children else Tree('ai_create_type', [])

    def ai_override_stmt(self, children):
        """ai.override decision isFraudulent / by X / value / reason "..." / to log"""
        name_tokens = [c for c in children
                       if isinstance(c, Token) and c.type == 'NAME']
        name = str(name_tokens[0]) if name_tokens else ""
        # Extract body fields from the block
        body_nodes = [c for c in children if not isinstance(c, Token)
                      and not _is_tree(c, 'closer')]
        by_attribution = None
        value = None
        reason = ""
        log_target = ""
        for node in body_nodes:
            # Try to extract by/reason/to/value from field nodes
            if hasattr(node, 'children'):
                field_text = tree_to_str(node) if hasattr(node, 'data') else ""
                if 'by' in str(node).lower():
                    by_attribution = node
                elif 'reason' in str(node).lower():
                    reason = str(node)
                elif 'to' in str(node).lower():
                    log_target = str(node)
                else:
                    value = node
        return AiOverrideStmt(
            name=name, value=value,
            by_attribution=by_attribution,
            reason=reason, log_target=log_target
        )

    # -- TRY / CATCH / ALWAYS ----------------------------------

    def try_block(self, children):
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'TRY'), None)
        open_line = _line(open_token)
        self._validate_closer('try', children, open_line)
        on_failure = next((c for c in children if isinstance(c, OnFailure)), None)
        on_success = next((c for c in children if isinstance(c, OnSuccess)), None)
        # on.failure / on.success arrive wrapped in a result_handlers Tree
        for c in children:
            if _is_tree(c, 'result_handlers'):
                for h in c.children:
                    if isinstance(h, OnFailure): on_failure = h
                    elif isinstance(h, OnSuccess): on_success = h
                    elif isinstance(h, (CheckWhen, OtherwiseClause)):
                        # The grammar lets `try` take result_handlers, so `when` and
                        # `otherwise` PARSE here and check clean -- and this transformer
                        # threw both away without a word. A conditional set on a `try` did
                        # nothing at all, silently.
                        #
                        # TryBlock has no field to hold them, and whether `try` SHOULD take
                        # a conditional set is a design question, not a bug fix. So refuse
                        # loudly rather than guess: a silent no-op is never the answer, and
                        # a loud refusal is one line to remove if it is later designed in.
                        word = 'when' if isinstance(h, CheckWhen) else 'otherwise'
                        raise MohioCompileError(
                            f"Line {open_line} -- `{word}` is not supported inside a `try` "
                            f"block in this build.\n"
                            f"    `try` handles what BROKE (catch / on.failure / always). "
                            f"A conditional set asks what CAME BACK.\n"
                            f"    Put the `{word}` on the verb block whose result you are "
                            f"testing, inside the try.")
        always = next((c for c in children if isinstance(c, AlwaysClause)), None)
        catch = next((c for c in children if isinstance(c, CatchClause)), None)
        # modifiers arrive as ('retry'|'per'|'total'|'backoff', value) tuples
        mods = {k: v for (k, v) in
                (c for c in children if isinstance(c, tuple) and len(c) == 2)}
        body = self._body_without_closer([
            c for c in children
            if not isinstance(c, Token)
            and not isinstance(c, tuple)
            and not isinstance(c, (OnFailure, OnSuccess, OnError, AlwaysClause,
                                   CatchClause, Closer))
            and not _is_tree(c, 'result_handlers')
        ])
        return TryBlock(body=body, catch=catch,
                        on_failure=on_failure, on_success=on_success,
                        always=always, line=open_line,
                        retry_times=mods.get('retry'),
                        per_timeout=mods.get('per'),
                        total_timeout=mods.get('total'),
                        backoff=mods.get('backoff'))

    def purpose_block(self, children):
        str_tok = next((c for c in children
                        if isinstance(c, Token) and c.type == 'STRING'), None)
        purpose = str(str_tok)[1:-1] if str_tok is not None else None
        open_line = _line(str_tok)
        self._validate_closer('purpose', children, open_line)
        body = self._body_without_closer([
            c for c in children
            if not isinstance(c, Token) and not isinstance(c, Closer)
        ])
        return PurposeBlock(purpose=purpose, body=body, line=open_line)

    def for_purpose(self, children):
        tok = next((c for c in children
                    if isinstance(c, Token) and getattr(c, 'type', None) == 'STRING'), None)
        return ('for_purpose', str(tok)[1:-1] if tok is not None else None)

    def _wrap_for_purpose(self, stmt, children):
        """Desugar `<stmt> for.purpose "X"` into a one-statement purpose block, so the
        per-op form reuses the exact same enforcement + audit as the block form."""
        fp = next((c for c in children
                   if isinstance(c, tuple) and len(c) == 2 and c[0] == 'for_purpose'), None)
        if fp is not None and fp[1]:
            return PurposeBlock(purpose=fp[1], body=[stmt], line=getattr(stmt, 'line', 0))
        return stmt

    def catch_clause(self, children):
        body = self._body_without_closer([
            c for c in children if not isinstance(c, Token)])
        return CatchClause(body=body)

    _TRY_UNIT_SECONDS = {
        'second': 1, 'seconds': 1, 'minute': 60, 'minutes': 60,
        'hour': 3600, 'hours': 3600, 'day': 86400, 'days': 86400,
        'week': 604800, 'weeks': 604800,
    }

    def _try_secs(self, children):
        num = next((c for c in children
                    if isinstance(c, Token) and c.type == 'NUMBER'), None)
        unit_node = _first_tree(children, 'try_time_unit')
        unit = (_token_str(unit_node) or 'seconds').strip() if unit_node else 'seconds'
        n = _coerce_number(str(num)) if num is not None else 0
        return float(n) * self._TRY_UNIT_SECONDS.get(unit, 1)

    def try_time_unit(self, children):
        return Tree('try_time_unit', children)

    def try_retry(self, children):
        num = next((c for c in children
                    if isinstance(c, Token) and c.type == 'NUMBER'), None)
        return ('retry', int(_coerce_number(str(num))) if num is not None else 1)

    def try_total_timeout(self, children):
        return ('total', self._try_secs(children))

    def try_per_timeout(self, children):
        return ('per', self._try_secs(children))

    def try_backoff(self, children):
        return ('backoff', self._try_secs(children))

    def always_clause(self, children):
        body = [c for c in children if not isinstance(c, Token)]
        return AlwaysClause(body=body)

    # -- ACTION STATEMENTS -------------------------------------

    def action_stmt(self, children):
        return children[0]

    def give_back_stmt(self, children):
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'GIVE'), None)
        open_line = _line(open_token)
        status_node = _first_tree(children, 'http_status')
        status = None
        if status_node:
            # HTTP_STATUS_CODE terminal (3-digit), STATUS_ALIAS keyword, or NUMBER fallback
            num = next((c for c in status_node.children
                        if isinstance(c, Token) and
                        c.type in ('HTTP_STATUS_CODE', 'NUMBER', 'STATUS_ALIAS')), None)
            if num is not None and num.type == 'STATUS_ALIAS':
                status = {'ok': 200, 'created': 201, 'unauthorized': 401,
                          'missing': 404, 'error': 500, 'pending': 202}[str(num).strip().lower()]
            elif num is not None:
                status = _coerce_number(str(num))
        # value is the first non-token, non-status child
        from mohio_ast import TrailingQualifier as _TQ
        non_tokens = [c for c in children
                      if not isinstance(c, Token) and not _is_tree(c, 'http_status')
                      and not _is_tree(c, 'give_back_mod')
                      and not isinstance(c, _TQ)]
        value = non_tokens[0] if non_tokens else None
        mod_node = _first_tree(children, 'give_back_mod')
        mod = None
        if mod_node:
            mod = mod_node.children[-1] if mod_node.children else None
        qual = next((c for c in children if isinstance(c, _TQ)), None)
        trusted = any(isinstance(c, Token) and c.type == 'TRUSTED' for c in children)
        return self._wrap_for_purpose(GiveBackStmt(status=status, value=value, modifier=mod,
                            qualifier=qual, trusted=trusted, line=open_line), children)

    def give_stmt(self, children):
        """`give <value> as download ["<filename>"]`.

        The filename is kept as a raw string so interpolation runs on it at request
        time, the same way any other Mohio string works -- that is what makes
        `as download "invoice-{{ customer.lastname }}.pdf"` rename in transit for free.
        """
        from mohio_ast import TrailingQualifier as _TQG, GiveStmt
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'GIVE'), None)
        name_node = _first_tree(children, 'give_as_name')
        filename = None
        if name_node is not None and name_node.children:
            filename = str(name_node.children[0])
            if len(filename) >= 2 and filename[0] in '"\'' and filename[-1] == filename[0]:
                filename = filename[1:-1]
        mod_node = _first_tree(children, 'give_back_mod')
        mod = None
        if mod_node is not None and mod_node.children:
            mod = str(mod_node.children[-1])
        value = next((c for c in children
                      if not isinstance(c, Token)
                      and not _is_tree(c, 'give_back_mod')
                      and not _is_tree(c, 'give_as_name')
                      and not isinstance(c, _TQG)), None)
        qual = next((c for c in children if isinstance(c, _TQG)), None)
        return GiveStmt(value=value, modifier=mod, filename=filename,
                        qualifier=qual, line=_line(open_token))

    def jump_to_stmt(self, children):
        dest = next((c for c in children
                     if isinstance(c, Token)
                     and c.type in ('PATH_LIT', 'STRING')), None)
        if dest is None:
            dest = next((c for c in children if isinstance(c, DottedName)), None)
        from mohio_ast import TrailingQualifier as _TQJ
        qual = next((c for c in children if isinstance(c, _TQJ)), None)
        return JumpToStmt(destination=str(dest) if isinstance(dest, Token) else dest,
                          qualifier=qual)

    # ── alternatives that used to collapse (see the aliases in mohio.lark) ────────────
    # Each of these rules was built only from filtered terminals, so every alternative produced
    # the same empty subtree and the choice was lost. `ai.create poster image` and
    # `ai.create poster video` compiled identically; a report grouped `by year` behaved exactly
    # like one grouped `by hour`. The aliases give each branch a handler; these return the value.
    def debug_mode_on(self, children):      return 'on'
    def debug_mode_off(self, children):     return 'off'
    def debug_mode_minimal(self, children): return 'minimal'
    def debug_mode_verbose(self, children): return 'verbose'

    def bucket_hour(self, children):    return 'hour'
    def bucket_day(self, children):     return 'day'
    def bucket_week(self, children):    return 'week'
    def bucket_month(self, children):   return 'month'
    def bucket_quarter(self, children): return 'quarter'
    def bucket_year(self, children):    return 'year'

    def create_type_image(self, children): return 'image'
    def create_type_audio(self, children): return 'audio'
    def create_type_logic(self, children): return 'logic'
    def create_type_text(self, children):  return 'text'
    def create_type_video(self, children): return 'video'
    def create_type_data(self, children):  return 'data'

    # ── map modifiers ─────────────────────────────────────────────────────────────────
    # `map_modifier` is a RULE, so the child arriving at map_alias_entry was a subtree, and the
    # parent selected modifiers with `isinstance(c, Token) and c.type in (...)`. No subtree can
    # satisfy that test, so every modifier was silently discarded and `modifiers` was always [].
    # Two entries with opposite case rules compiled identically. Aliasing each alternative gives
    # it a handler that collapses to a plain value the parent can actually see.
    def contribute_when_all_pass(self, children):   return 'when_all_pass'
    def contribute_opt_in_required(self, children): return 'opt_in_required'

    def mod_ignore_case(self, children):     return 'ignore.case'
    def mod_match_case(self, children):      return 'match.case'
    def mod_keep_whitespace(self, children): return 'keep.whitespace'

    def mod_retired_case_no(self, children):
        raise MohioCompileError(
            "`case.no` is retired. Use `ignore.case` -- it says what it does: matching "
            "disregards letter case.")

    def mod_retired_case_yes(self, children):
        raise MohioCompileError(
            "`case.yes` is retired. Use `match.case` -- it says what it does: matching is "
            "case-sensitive.")

    def debug_decl(self, children):
        """`debug on|off|minimal|verbose`.

        There was no handler, so the whole declaration survived into the finished AST as a raw
        Tree -- which always means some rule was never transformed, whatever the cause.
        """
        from mohio_ast import DebugDecl
        mode = next((c for c in children
                     if isinstance(c, str)
                     and c in ('on', 'off', 'minimal', 'verbose')), 'on')
        return DebugDecl(mode=mode)

    def trailing_qualifier(self, children):
        """`if <condition>` trailing an action statement.

        This method did not exist. The grammar built the condition and the AST had a labelled
        `qualifier` slot waiting for it, so both layers read as finished -- but nothing moved the
        value between them, the field kept its default of None, and the guarded statement ran
        unconditionally. `halt if <cond>` halted every time; `give back ... if <cond>` responded
        every time. A guard that does not guard is worse than no guard, because the code reads as
        though the case is handled.

        Raising on a missing condition rather than returning None: a silent None here is exactly
        what made the original defect invisible.
        """
        from mohio_ast import TrailingQualifier
        cond = next((c for c in children if not isinstance(c, Token)), None)
        if cond is None:
            raise MohioCompileError("trailing `if` has no condition.")
        return TrailingQualifier(condition=cond)

    def halt_stmt(self, children):
        from mohio_ast import TrailingQualifier
        qual = next((c for c in children if isinstance(c, TrailingQualifier)), None)
        return HaltStmt(qualifier=qual)

    def stop_stmt(self, children):
        name_tok = next((c for c in children if isinstance(c, Token) and c.type == 'NAME'), None)
        cond = next((c for c in children if not isinstance(c, Token)), None)
        return StopStmt(target=str(name_tok) if name_tok else None, condition=cond)

    def skip_stmt_action(self, children):
        cond = next((c for c in children if not isinstance(c, Token)), None)
        return SkipStmt(condition=cond)

    def loop_block(self, children):
        """loop [name] ... loop: done -- open-ended loop, break with 'stop' or 'stop name'."""
        from mohio_ast import LoopBlock
        name_tok = next((c for c in children if isinstance(c, Token) and c.type == 'NAME'), None)
        name = str(name_tok) if name_tok else None
        body = self._body_without_closer([c for c in children if not isinstance(c, Token)])
        return LoopBlock(name=name, body=body)

    def show_stmt(self, children):
        value = next((c for c in children if not isinstance(c, Token)
                      and not _is_tree(c, 'show_mod')), None)
        mod_node = _first_tree(children, 'show_mod')
        mod = mod_node.children[-1] if mod_node and mod_node.children else None
        return self._wrap_for_purpose(ShowStmt(value=value, modifier=mod), children)

    def raise_stmt(self, children):
        name_token = next((c for c in children
                           if isinstance(c, Token)
                           and c.type in ('NAME', 'AGAIN')), None)
        error_name = str(name_token) if name_token else None
        value = next((c for c in children if not isinstance(c, Token)), None)
        return RaiseStmt(error_name=error_name, message=value)

    def send_stmt(self, children):
        non_tokens = [c for c in children if not isinstance(c, Token)]
        value = non_tokens[0] if non_tokens else None
        target = non_tokens[1] if len(non_tokens) > 1 else None
        return SendStmt(value=value, target=target)

    def broadcast_stmt(self, children):
        non_tokens = [c for c in children if not isinstance(c, Token)]
        room = non_tokens[0] if non_tokens else None
        excpt = next((c for c in children
                      if isinstance(c, Token) and c.type == 'EXCEPT'), None)
        except_session = non_tokens[1] if excpt and len(non_tokens) > 1 else None
        return BroadcastStmt(room=room, value=None, except_session=except_session)

    def cast_expr(self, children):
        # cast_expr: value_expr type_cast_mod  (postfix). Handles every as.* cast AND round.*.
        from mohio_ast import TypeCastExpr, RoundExpr
        value    = children[0]
        mod_tree = children[1] if len(children) > 1 else None
        toks = list(getattr(mod_tree, 'children', []) or [])
        mod  = str(toks[0]) if toks else str(mod_tree)
        # round family -> RoundExpr
        if mod == 'round.up':   return RoundExpr(value=value, direction='up')
        if mod == 'round.down': return RoundExpr(value=value, direction='down')
        if mod == 'round.to':
            places = int(str(toks[1])) if len(toks) > 1 else 2
            return RoundExpr(value=value, direction='to', places=places)
        # as.* casts -> TypeCastExpr
        cast_map = {
            'as.int':'int', 'as.decimal':'decimal', 'as.string':'string', 'as.text':'string', 'as.boolean':'boolean', 'as.bool':'boolean',
            'as.days':'days', 'as.hours':'hours', 'as.minutes':'minutes', 'as.seconds':'seconds',
            'as.weeks':'weeks', 'as.uc':'uc', 'as.lc':'lc', 'as.uppercase':'uc', 'as.lowercase':'lc', 'as.title':'title',
            'as.sentence':'sentence', 'as.absolute':'absolute', 'as.json':'json',
            'as.csv':'csv', 'as.pdf':'pdf', 'as.html':'html',
        }
        if mod.startswith('as.dec'):        # as.dec / as.decimal / .N
            places = None
            parts = mod.split('.')          # ['as','decimal'] or ['as','decimal','2']
            if len(parts) >= 3 and parts[2].isdigit():
                places = int(parts[2])
            return TypeCastExpr(value=value, cast_type='decimal', places=places)
        if mod in cast_map:
            return TypeCastExpr(value=value, cast_type=cast_map[mod])
        return value

    def stream_stmt(self, children):
        non_tokens = [c for c in children if not isinstance(c, Token)]
        value = non_tokens[0] if non_tokens else None
        target = non_tokens[1] if len(non_tokens) > 1 else None
        return StreamStmt(value=value, target=target)

    def notify_stmt(self, children):
        dotted = next((c for c in children if isinstance(c, DottedName)), None)
        via_token = next((str(c) for c in children
                          if isinstance(c, Token) and c.type == 'NAME'), None)
        body = [c for c in children
                if _is_tree(c, 'notify_body')]
        return NotifyStmt(target=dotted, channel=via_token, body=body)

    def release_stmt(self, children):
        variant_token = next((c for c in children
                              if isinstance(c, Token)
                              and c.type in ('RELEASE', 'RELEASE_NOW',
                                             'RELEASE_LOCK')), None)
        variant = str(variant_token).lower() if variant_token else "release"
        name = next((c for c in children if isinstance(c, DottedName)), None)
        value = next((c for c in children
                      if not isinstance(c, Token)
                      and not isinstance(c, DottedName)), None)
        return ReleaseStmt(variant=variant,
                           name='.'.join(name.parts) if name else "",
                           value=value)

    def clear_stmt(self, children):
        name = next((c for c in children if isinstance(c, DottedName)), None)
        return VarStateStmt(op='clear', name='.'.join(name.parts) if name else "")

    def forget_stmt(self, children):
        name = next((c for c in children if isinstance(c, DottedName)), None)
        return VarStateStmt(op='forget', name='.'.join(name.parts) if name else "")

    def rename_stmt(self, children):
        name = next((c for c in children if isinstance(c, DottedName)), None)
        target = next((c for c in children if isinstance(c, Token) and c.type == 'NAME'), None)
        return VarStateStmt(op='rename',
                            name='.'.join(name.parts) if name else "",
                            target=str(target) if target else "")

    def replace_stmt(self, children):
        name = next((c for c in children if isinstance(c, DottedName)), None)
        value = next((c for c in children
                      if not isinstance(c, Token) and not isinstance(c, DottedName)), None)
        return VarStateStmt(op='replace',
                            name='.'.join(name.parts) if name else "",
                            value=value)

    def service_call_stmt(self, children):
        dotted_names = [c for c in children if isinstance(c, DottedName)]
        dotted = dotted_names[0] if dotted_names else None
        if dotted and len(dotted.parts) >= 2:
            service = dotted.parts[0]
            method = '.'.join(dotted.parts[1:])
        else:
            service = str(dotted) if dotted else ""
            method = ""
        # A MioQL/data verb used as a dotted service (save.do, find.by, retrieve.x)
        # is a mistake: these open blocks, they are not services. The grammar parses
        # any word.word as a service call, so fail loud here instead of silently
        # accepting a no-op. `remove` is excluded (it has real dotted forms like
        # remove.ws); ai./cm./mio* and other real services are unaffected.
        _DATA_BLOCK_VERBS = {'save', 'find', 'retrieve', 'grab',
                             'update', 'upsert', 'create'}
        if service in _DATA_BLOCK_VERBS:
            raise MohioCompileError(
                f"'{service}.{method}' is not a valid form. '{service}' is a data/block "
                f"verb, not a service, and a dotted spelling parses but does nothing. "
                f"Use a '{service}' block instead (for example `{service} ...`).",
                line=getattr(dotted, 'line', 0) or 0)
        # Args are every non-token child EXCEPT the service.method dotted name
        # itself. A value_expr arg can ITSELF be a DottedName (e.g. the decision
        # name in `ai.decide resolve_noun`), so we must not blanket-drop
        # DottedNames here -- doing so silently discarded the invocation target.
        arg_children = [c for c in children
                        if not isinstance(c, Token) and c is not dotted]
        args = arg_children[0] if arg_children else None
        params = arg_children[1:] if len(arg_children) > 1 else []
        return ServiceCallStmt(service=service, method=method,
                               args=args, params=params)

    def dotted_name_with_dot(self, children):
        # MIO_SERVICE_ROOT as well as NAME: the service roots are reserved words now, so the head
        # of `miosearch.index` is NOT a NAME token. Filtering on NAME alone silently DROPPED the
        # head and produced parts=['index'] -- the service name vanished, and the not-built check
        # never saw a service to complain about. Same allowlist-drops-what-it-does-not-recognize
        # bug as everywhere else.
        parts = [str(c) for c in children
                 if isinstance(c, Token) and c.type in ('NAME', 'MIO_SERVICE_ROOT')]
        return DottedName(parts=parts)

    def inline_action(self, children):
        return children[0] if children else None

    # -- ASSIGNMENT --------------------------------------------

    def assignment(self, children):
        # `set` is RETIRED. It used to be accepted as noise and SILENTLY DISCARDED -- exactly
        # how a dead keyword survives in the docs and comes back as canon. It still parses so
        # that this message can be raised instead of a bare parser error.
        _set = next((c for c in children
                     if isinstance(c, Token) and c.type == 'SET'), None)
        if _set is not None:
            _nm = next((str(c) for c in children
                        if isinstance(c, Token) and c.type == 'NAME'), 'x')
            raise MohioCompileError(
                f"`set` is retired. Write the declaration directly: `{_nm} <value>` "
                f"(the `=` is optional sugar). Use `hold {_nm} <value>` to freeze it until "
                f"released, or `lock {_nm} <value>` for a permanent constant.")

        # SET? NAME (AS type_name)? =? value_expr
        name_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'NAME'), None)
        name = str(name_token) if name_token else ""
        # A bare `call X` / `run X` (no closer, no `with`) has no valid call/run rule,
        # so Earley falls back to parsing it as an assignment named `call`/`run`. That
        # is never what the author meant -- fail loud instead of silently creating a
        # variable. (`call`/`run` are reserved invocation keywords.)
        if name in ('call', 'run'):
            value = next((c for c in children
                          if not isinstance(c, Token) and not _is_tree(c, 'type_name')), None)
            target = None
            if value is not None:
                target = (getattr(value, 'name', None)
                          or ('.'.join(str(p) for p in getattr(value, 'parts', []))
                              if getattr(value, 'parts', None) else None))
            target = target or "<task>"
            raise MohioCompileError(
                f"'{name} {target}' is not a valid task invocation.\n"
                f"Use the block form:  call {target}   (then on its own line)  call: done\n"
                f"Or with arguments:  call {target} with <value>",
                line=_line(name_token))
        # Block-opener / connection keywords that mis-parse into an assignment name
        # mean an incomplete statement (no closer, or the wrong form). Earley falls
        # back to an assignment, which would silently create a junk variable (e.g.
        # `find xs in db.nums then show it.count` reads `find` as a variable and
        # `it.count` ends up counting the letters in "nums"). Fail loud at transform
        # time so `mio check` catches it, with the correct form. Only keywords whose
        # VALID form is a proper block are listed -- save/upsert/remove have valid
        # one-line forms that legitimately parse as assignments, so they are excluded.
        _RESERVED_OPENERS = {
            'connect': "`connect` opens a connection and is not a variable name.\n"
                       "Use:  connect db as postgres from env.DATABASE_URL\n"
                       "AI needs no connection (`ai.*` is always available), so "
                       "`connect ai` is never valid -- remove it.",
            'verify':  "`verify` is a keyword, not a variable name.\n"
                       "`verify token` needs a source:  verify token from "
                       "request.header \"Authorization\"\n"
                       "A bare `verify token` verifies nothing and must not pass silently.",
            'find':    "`find` opens a query block and must close with `find: done`.\n"
                       "    find xs in db.nums\n    find: done\n"
                       "then use xs (e.g. xs.count) or chain it:  ... then show it.count",
            'retrieve':"`retrieve` opens a query block and must close with `retrieve: done`.\n"
                       "    retrieve row from db.table\n        match id to 1\n    retrieve: done",
            'update':  "`update` opens a write block and must close with `update: done`.\n"
                       "    update db.table\n        match id to 1\n        field value\n    update: done",
            'grab':    "`grab` opens a quick-read block and must close with `grab: done`.\n"
                       "    grab cfg from db.config\n        match key to \"theme\"\n    grab: done",
            'as':      "Naming goes on the ACTION, never on a closer. "
                       "`<block>: done as NAME` is retired. "
                       "Use `check score as grade`, `retrieve rows from db.t`, "
                       "`find rows in db.t`. A nested `sql` block takes no alias -- the "
                       "enclosing query verb names the result.",
            'task':    "`task` opens a named block and must close with `task: done`.\n"
                       "    task total\n        a as int\n        returns int\n\n"
                       "        give back a\n    task: done",
            'uppercase': "`uppercase` is an adjective and follows the noun, it never leads.\n"
                         "Write it after the value: `name uppercase` (e.g. `show name "
                         "uppercase`, `hold display name uppercase`).",
            'miopublish': "`miopublish` is not a keyword on its own. The guaranteed-delivery "
                          "form is `miopublish.guaranteed \"message\" / to CHANNEL / miopublish: "
                          "done` (a not-built service that fails loud), not a bare `miopublish`.",
            'lowercase': "`lowercase` is an adjective and follows the noun, it never leads.\n"
                         "Write it after the value: `name lowercase` (e.g. `show name "
                         "lowercase`, `hold key name lowercase`).",
        }
        if name in _RESERVED_OPENERS:
            raise MohioCompileError(_RESERVED_OPENERS[name], line=_line(name_token))
        if name == 'validate':
            # `validate email` / `validate required` / `validate min 8` (inline built-in
            # validators) have no grammar rule yet, so they fall here as an assignment
            # named `validate` and would SILENTLY pass invalid input. Fail loud until
            # the inline validators are wired. (The working forms are
            # `validate using <ruleset>` and `validate against <expr>`.)
            value = next((c for c in children
                          if not isinstance(c, Token) and not _is_tree(c, 'type_name')), None)
            target = None
            if value is not None:
                target = (getattr(value, 'name', None)
                          or ('.'.join(str(p) for p in getattr(value, 'parts', []))
                              if getattr(value, 'parts', None) else None))
            target = target or "<rule>"
            raise MohioCompileError(
                f"Inline validator 'validate {target}' is not yet wired (it would "
                f"silently pass invalid input). Use 'validate using <ruleset>' or "
                f"'validate against <expr>' for now.",
                line=_line(name_token))
        type_node = _first_tree(children, 'type_name')
        type_name = _token_str(type_node) if type_node else None
        value_exprs = [c for c in children
                       if not isinstance(c, Token) and not _is_tree(c, 'type_name')]
        has_default = any(isinstance(c, Token) and c.type == 'DEFAULT' for c in children)
        value   = value_exprs[0] if value_exprs else None
        default = value_exprs[1] if (has_default and len(value_exprs) > 1) else None
        return Assignment(
            name=name,
            type_name=type_name,
            value=value,
            default=default,
            line=_line(name_token),
        )

    # -- CONDITIONS --------------------------------------------

    # ── conditions that reached the AST as raw Trees ──────────────────────────────────
    # Each of these had a grammar alias and NO transformer method, so a raw Tree reached the
    # interpreter, which fell back to evaluating the first child. Every one of them reduced to
    # "is the left operand truthy", which meant the guard fired regardless of the condition.
    # `is.not` is the documented spelling, so the wrong one was the one people were told to use.
    def cond_is_not(self, children):
        """`x is.not y` -- the dotted spelling of `x is not y`. Same primitive, one meaning."""
        non_tokens = [c for c in children if not isinstance(c, Token)]
        return Condition(left=non_tokens[0] if non_tokens else None,
                         op='is not',
                         right=non_tokens[1] if len(non_tokens) > 1 else None)

    def cond_is_empty(self, children):
        """`x is.empty` -- true when x is None, "", or an empty collection.

        Distinct from `cond_empty`, which belongs to the client-side condition family and
        returns a tuple. Both grammar rules used to alias the same name, so the condition path
        received a tuple it could not read and fell through to truthiness -- a non-empty tuple
        is truthy, so `is.empty` was true for everything.
        """
        non_tokens = [c for c in children if not isinstance(c, Token)]
        return Condition(left=non_tokens[0] if non_tokens else None,
                         op='is.empty', right=None)

    def cond_not_empty(self, children):
        """`not.empty x` -- true when x is NOT None/""/empty collection.

        Not the same as truthy: 0 is not empty, and the old fallback called it empty.
        """
        non_tokens = [c for c in children if not isinstance(c, Token)]
        return Condition(left=non_tokens[0] if non_tokens else None,
                         op='not.empty', right=None)

    def cond_is(self, children):
        non_tokens = [c for c in children if not isinstance(c, Token)]
        left = non_tokens[0] if non_tokens else None
        right = non_tokens[1] if len(non_tokens) > 1 else None
        negated = any(isinstance(c, Token) and c.type == 'NOT'
                      for c in children)
        op = 'is not' if negated else 'is'
        return Condition(left=left, op=op, right=right)

    def cond_cmp(self, children):
        non_tokens = [c for c in children if not isinstance(c, Token)]
        op_token = next((c for c in children
                         if isinstance(c, Token) and c.type == 'CMP_OP'), None)
        left = non_tokens[0] if non_tokens else None
        right = non_tokens[1] if len(non_tokens) > 1 else None
        return Condition(left=left, op=str(op_token) if op_token else "==",
                         right=right)

    def cond_above(self, children):
        non_tokens = [c for c in children if not isinstance(c, Token)]
        return Condition(left=non_tokens[0] if non_tokens else None,
                         op='above',
                         right=non_tokens[1] if len(non_tokens) > 1 else None)

    def cond_below(self, children):
        non_tokens = [c for c in children if not isinstance(c, Token)]
        return Condition(left=non_tokens[0] if non_tokens else None,
                         op='below',
                         right=non_tokens[1] if len(non_tokens) > 1 else None)

    def cond_is_morethan(self, children):
        non_tokens = [c for c in children if not isinstance(c, Token)]
        return Condition(left=non_tokens[0] if non_tokens else None,
                         op='above',
                         right=non_tokens[1] if len(non_tokens) > 1 else None)

    def cond_is_lessthan(self, children):
        non_tokens = [c for c in children if not isinstance(c, Token)]
        return Condition(left=non_tokens[0] if non_tokens else None,
                         op='below',
                         right=non_tokens[1] if len(non_tokens) > 1 else None)

    def cond_is_even(self, children):
        # `n is even` -- a unary parity predicate; no right operand.
        non_tokens = [c for c in children if not isinstance(c, Token)]
        return Condition(left=non_tokens[0] if non_tokens else None,
                         op='even', right=None)

    def cond_is_odd(self, children):
        non_tokens = [c for c in children if not isinstance(c, Token)]
        return Condition(left=non_tokens[0] if non_tokens else None,
                         op='odd', right=None)

    def cond_is_above(self, children):
        non_tokens = [c for c in children if not isinstance(c, Token)]
        return Condition(left=non_tokens[0] if non_tokens else None,
                         op='above',
                         right=non_tokens[1] if len(non_tokens) > 1 else None)

    def cond_is_below(self, children):
        non_tokens = [c for c in children if not isinstance(c, Token)]
        return Condition(left=non_tokens[0] if non_tokens else None,
                         op='below',
                         right=non_tokens[1] if len(non_tokens) > 1 else None)

    def cond_dot_state(self, children):
        value = next((c for c in children if not isinstance(c, Token)), None)
        state_token = next((c for c in children
                            if isinstance(c, Token) and c.type == 'DOT_STATE'), None)
        if state_token:
            parts = str(state_token).split('.', 1)
            prefix = parts[0]
            state = parts[1] if len(parts) > 1 else ""
        else:
            prefix = state = ""
        return DotStateCheck(value=value, prefix=prefix, state=state)

    def cond_not(self, children):
        cond = next((c for c in children if not isinstance(c, Token)), None)
        return NotCondition(condition=cond)

    def cond_and(self, children):
        non_tokens = [c for c in children if not isinstance(c, Token)]
        return AndCondition(
            left=non_tokens[0] if non_tokens else None,
            right=non_tokens[1] if len(non_tokens) > 1 else None,
        )

    def cond_or(self, children):
        non_tokens = [c for c in children if not isinstance(c, Token)]
        return OrCondition(
            left=non_tokens[0] if non_tokens else None,
            right=non_tokens[1] if len(non_tokens) > 1 else None,
        )

    def cond_bool(self, children):
        # A bare value_expr used as a truthiness condition, e.g. `unless door_open`.
        # Returned as the raw expression; _eval_condition evaluates it for truthiness.
        return children[0] if children else None

    # -- VALUE EXPRESSIONS -------------------------------------

    def value_expr(self, children):
        child = children[0] if children else None
        # Lark inlines single-terminal rules -- namespace ref tokens arrive raw
        if isinstance(child, Token):
            t = child.type
            v = str(child)
            if t == 'ENV_REF':
                return EnvRef(key=v.replace('env.', ''), line=_line(child))
            if t == 'SECRET_REF':
                return SecretRef(key=v.replace('secret.', ''), line=_line(child))
            if t == 'DB_REF':
                return DbRef(table=v.replace('db.', ''), line=_line(child))
            if t == 'SH_REF':
                return ShRef(shape_name=v.replace('sh.', ''), line=_line(child))
            if t in ('TRUE', 'FALSE', 'BOOL'):
                return Literal(value=(v == 'true'), literal_type='bool', line=_line(child))
            if t in ('NULL_KW', 'NONE_KW'):
                return Literal(value=None, literal_type='null', line=_line(child))
            if t == 'NUMBER':
                _v, _lt = _number_or_code(v)
                return Literal(value=_v, literal_type=_lt, line=_line(child))
            if t == 'STRING':
                return Literal(value=_mohio_decode_string(v), literal_type='string', line=_line(child))
            if t == 'TEMPLATE_STR':
                return TemplateString(template=v, line=_line(child))
            if t == 'NOW_CALL':
                return TimeExpr(base='now()', line=_line(child))
            if t in ('TODAY', 'YESTERDAY', 'LAST_WEEK', 'LAST_MONTH',
                     'LAST_QUARTER', 'LAST_YEAR', 'THIS_WEEK', 'THIS_MONTH',
                     'THIS_QUARTER', 'THIS_YEAR'):
                return TimeExpr(base=v, line=_line(child))
            # NAME or anything else -- wrap as dotted name
            return DottedName(parts=[v], line=_line(child))
        return child

    def paragraph_string(self, children):
        # PARA_MARK STRING -> string Literal (marker is a signal; stripped here).
        # The STRING may contain real newlines (authored multi-line / prompt).
        tok = next((c for c in children if isinstance(c, Token) and c.type == 'STRING'), None)
        if tok is None:
            return Literal(value="", literal_type='string')
        return Literal(value=_mohio_decode_string(str(tok)), literal_type='string')

    def literal(self, children):
        token = children[0]
        if not isinstance(token, Token):
            return token   # already-built node (e.g. paragraph_string -> Literal)
        if token.type in ('TRUE', 'FALSE'):
            return Literal(value=(token.type == 'TRUE'), literal_type='bool')
        if token.type in ('NULL_KW', 'NONE_KW'):
            return Literal(value=None, literal_type='null')
        if token.type == 'NUMBER':
            _v, _lt = _number_or_code(str(token))
            return Literal(value=_v, literal_type=_lt)
        if token.type == 'STRING':
            return Literal(value=_mohio_decode_string(str(token)), literal_type='string')
        return Literal(value=str(token), literal_type='unknown')

    # Design literals (v3.8), wired for mioimage and ai.generate. The grammar defines these
    # rules and the interpreter has evaluators (MohioValue(value, 'color'|'percent'|
    # 'dimension')), but no method built the node, so every #ff8800 / 50% / 12px silently
    # evaluated to None. Same shape as uuid(): both ends present, nobody connected them.
    def color_lit(self, children):
        token = children[0]
        return ColorLit(value=str(token), line=_line(token))

    def percent_lit(self, children):
        token = children[0]
        return PercentLit(value=str(token), line=_line(token))

    def dimension_lit(self, children):
        token = children[0]
        return DimensionLit(value=str(token), line=_line(token))

    def dotted_name(self, children):
        # Handle pre-tokenized USERVAR_DOTTED tokens
        for c in children:
            if isinstance(c, Token) and c.type == 'USERVAR_DOTTED':
                # Strip __USERVAR__ prefix and split on dots
                from mohio_pretokenizer import unmark_dotted
                left, rest = unmark_dotted(str(c))
                parts = [left] + rest
                return DottedName(parts=parts)
        # Standard dotted name -- NAME tokens (plus 'average', a reserved terminal still valid as a
        # dotted segment, e.g. score.average), and NUMBER for a numeric index segment
        # (colors.position.2). Dropping the NUMBER made `.position.N` on a plain list resolve to
        # None -- a silent no-op on basic list access.
        parts = [str(c) for c in children
                 if isinstance(c, Token) and c.type in ('NAME', 'AVERAGE', 'NUMBER')]
        return DottedName(parts=parts)

    def sh_ref(self, children):
        token = next((c for c in children
                      if isinstance(c, Token) and c.type == 'SH_REF'), None)
        name = str(token).replace('sh.', '') if token else ""
        return ShRef(shape_name=name)

    def env_ref(self, children):
        token = next((c for c in children
                      if isinstance(c, Token) and c.type == 'ENV_REF'), None)
        key = str(token).replace('env.', '') if token else ""
        return EnvRef(key=key)

    def secret_ref(self, children):
        token = next((c for c in children
                      if isinstance(c, Token) and c.type == 'SECRET_REF'), None)
        key = str(token).replace('secret.', '') if token else ""
        return SecretRef(key=key)

    def db_ref(self, children):
        token = next((c for c in children
                      if isinstance(c, Token) and c.type == 'DB_REF'), None)
        table = str(token).replace('db.', '') if token else ""
        return DbRef(table=table)

    def func_call(self, children):
        dotted = next((c for c in children if isinstance(c, DottedName)), None)
        arg_list = _first_tree(children, 'arg_list')
        args = list(arg_list.children) if arg_list else []
        return FuncCall(name=dotted, args=args)

    def math_expr(self, children):
        # children contains the content between ( ) -- find math_inner or math_binop
        for c in children:
            if isinstance(c, Tree):
                return _build_math(c)
            if isinstance(c, MathExpr):
                return c
        # Fallback -- single value
        non_tokens = [c for c in children if not isinstance(c, Token)]
        return non_tokens[0] if non_tokens else None

    def math_binop(self, children):
        # Handled by _build_math below
        return Tree('math_binop', children)

    def math_val(self, children):
        return children[0] if children else None

    def template_str(self, children):
        token = children[0]
        return TemplateString(template=str(token))

    def list_lit(self, children):
        # RETIRED: the [a, b, c] bracket list literal. Square brackets are reserved for field tags
        # ([phi], [pii]) and facets -- not values. Fail loud at mio check (before deploy) with the
        # canonical fix, rather than parsing a value-bracket into a list. class_tag / _FACETS use
        # their own rules and are untouched. create list / as list build the list node directly.
        items = [c for c in children if not isinstance(c, Token)]
        line = next((getattr(c, 'line', 0) for c in items if getattr(c, 'line', 0)), 0)
        raise MohioCompileError(
            "the [a, b, c] list literal is retired -- square brackets are for field tags "
            "([phi], [pii]) and facets, not values.\n"
            "  Inline:  colors as list \"red\", \"green\", \"blue\"\n"
            "  Block:   create list colors / \"red\" / \"green\" / create: done",
            line=line)

    def map_lit(self, children):
        entries = [c for c in children if _is_tree(c, 'map_entry')]
        pairs = []
        for entry in entries:
            tokens = [t for t in entry.children if isinstance(t, Token)]
            key_token = next((t for t in tokens if t.type == 'NAME'), None)
            key = str(key_token) if key_token else ""
            value = next((c for c in entry.children if not isinstance(c, Token)), None)
            pairs.append((key, value))
        return MapLiteral(entries=pairs)

    def map_entry(self, children):
        return Tree('map_entry', children)

    # -- TIME --------------------------------------------------

    # Every token `time_expr` admits, named explicitly. The grammar rule is:
    #
    #   time_expr: NOW_CALL (("-"|"+") duration_expr)?  |  UUID_CALL
    #            | TODAY | YESTERDAY
    #            | LAST_WEEK | LAST_MONTH | LAST_QUARTER | LAST_YEAR
    #            | THIS_WEEK | THIS_MONTH | THIS_QUARTER | THIS_YEAR
    #            | SINCE time_anchor
    #
    # This list must name every one of them. If it does not, the missing token falls into
    # the fallthrough and becomes SOMETHING ELSE without any error -- which is exactly how
    # uuid() came to return a timestamp.
    _TIME_WORDS = ('TODAY', 'YESTERDAY',
                   'LAST_WEEK', 'LAST_MONTH', 'LAST_QUARTER', 'LAST_YEAR',
                   'THIS_WEEK', 'THIS_MONTH', 'THIS_QUARTER', 'THIS_YEAR')

    def time_expr(self, children):
        # `time_expr` admits BOTH now() and uuid(). This method used to name only NOW_CALL
        # and let everything else fall through to `TimeExpr(base=str(token))` -- so uuid()
        # became a TIME expression and the interpreter evaluated it as the current clock:
        #
        #     hold u uuid()   ->   u = "2026-07-14T20:36:18.842854"
        #
        # It parsed. It checked clean. It ran. It silently returned the WRONG KIND OF VALUE.
        # A "uuid" that is really a timestamp is PREDICTABLE and COLLIDABLE, so anything
        # using it for a token, a reset link, or a key had a security bug and no warning.
        #
        # The bitter part: `UuidCall` exists and the interpreter has always had a correct
        # evaluator for it (`str(uuid.uuid4())`). Both ends of the feature were built.
        # Nothing ever connected them -- exactly like ai.decide's confidence threshold,
        # which sat at a hardcoded 0.85 for the same reason.
        #
        # So: name every token, and REFUSE anything unnamed rather than guessing.
        token = children[0] if children else None
        if isinstance(token, Token):
            if token.type == 'UUID_CALL':
                return UuidCall(line=_line(token))
            # `since <anchor>`: the SINCE token leads, and time_anchor has already built a
            # SinceExpr sitting later in children. Return that built node rather than
            # choking on the leading SINCE token (which would hit the refuse-branch below).
            if token.type == 'SINCE':
                built = next((c for c in children if isinstance(c, SinceExpr)), None)
                if built is not None:
                    return built
                raise MohioCompileError(
                    "`since` with no anchor.", line=_line(token))
            # now() with optional offset
            if token.type == 'NOW_CALL':
                dur = next((c for c in children if isinstance(c, DurationExpr)), None)
                op_token = next((c for c in children
                                 if isinstance(c, Token)
                                 and str(c) in ('+', '-')), None)
                op = str(op_token) if op_token else None
                return TimeExpr(base='now()', offset_op=op, offset=dur)
            if token.type in self._TIME_WORDS:
                return TimeExpr(base=str(token))
            raise MohioCompileError(
                f"'{token}' reached a time expression but this build does not know how to "
                f"evaluate it. Add it to MohioTransformer._TIME_WORDS, or give it its own "
                f"AST node. It must NOT fall through: an unnamed token used to silently "
                f"become a timestamp, which is how uuid() returned the current time.",
                line=_line(token))
        # `since <anchor>` arrives already built by time_anchor.
        if children and not isinstance(children[0], Token):
            return children[0]
        raise MohioCompileError(
            "Empty time expression -- time_expr was reached with nothing to evaluate.")

    def time_anchor(self, children):
        token = children[0] if children else None
        dur = next((c for c in children if isinstance(c, DurationExpr)), None)
        return SinceExpr(anchor=str(token) if token else "")

    def datetime_expr(self, children):
        date_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'DATE_LIT'), None)
        time_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'TIME_LIT'), None)
        tz_token = next((c for c in children
                         if isinstance(c, Token) and c.type == 'NAME'), None)
        if isinstance(children[0], TimeExpr):
            return children[0]
        return DatetimeExpr(
            date=str(date_token) if date_token else "",
            time=str(time_token) if time_token else None,
            timezone=str(tz_token) if tz_token else None,
        )

    def duration_expr(self, children):
        num = next((c for c in children
                    if isinstance(c, Token) and c.type == 'NUMBER'), None)
        unit_node = _first_tree(children, 'time_unit')
        unit = _token_str(unit_node) if unit_node else ""
        return DurationExpr(
            count=_coerce_number(str(num)) if num else None,
            unit=unit,
        )

    def time_unit(self, children):
        return Tree('time_unit', children)

    def type_name(self, children):
        token = children[0]
        return Tree('type_name', [token])

    def miomail_stmt(self, children):
        from mohio_ast import MiomailStmt

        # Determine action from first token
        first_tok = next((c for c in children if isinstance(c, Token)), None)
        tok_type = str(first_tok.type).lower() if first_tok else ""
        if "queue" in tok_type:
            action = "queue"
        elif "template" in tok_type:
            action = "template"
        else:
            action = "send"

        to_ = from_ = from_name = subject = body = template = reply_to = None
        attach = []
        cc = []
        bcc = []

        # Walk body nodes
        for child in children:
            if not _is_tree(child, "miomail_body"):
                continue
            body_toks = [c for c in child.children if isinstance(c, Token)]
            body_vals = [c for c in child.children if not isinstance(c, Token)]
            key = str(body_toks[0]).lower() if body_toks else ""
            val = body_vals[0] if body_vals else (body_toks[1] if len(body_toks) > 1 else None)

            if key == "to":          to_ = val
            elif key == "from":
                from_ = val
                # Check for "as Name" -- from "addr" as "Name"
                as_tok = next((str(t) for t in body_toks if str(t).lower() == "as"), None)
                if as_tok and len(body_vals) > 1:
                    from_name = body_vals[1]
            elif key == "subject":   subject = val
            elif key == "body":      body = val
            elif key == "template":  template = val
            elif key == "attach":    attach.append(val)
            elif key == "cc":        cc.append(val)
            elif key == "bcc":       bcc.append(val)
            elif key == "reply_to":  reply_to = val

        return self._wrap_for_purpose(MiomailStmt(
            action=action, to=to_, from_=from_, from_name=from_name,
            subject=subject, body=body, template=template,
            attach=attach, cc=cc, bcc=bcc, reply_to=reply_to
        ), children)



    def string_op_expr(self, children):
        from mohio_ast import StringOpExpr
        tokens = [c for c in children if isinstance(c, Token)]
        non_tokens = [c for c in children if not isinstance(c, Token)]
        # Detect which op by first token type
        if not tokens:
            return StringOpExpr(operation='unknown', operand=None)
        op_tok = tokens[0]
        op_type = str(op_tok.type).lower()
        # Slice operators: value_expr OP value/number
        if op_type in ('str_after', 'str_before', 'str_left', 'str_right'):
            # Structure: value_expr OP value_expr [STR_DEFAULT value_expr]
            # tokens: [OP_TOKEN, possibly STR_DEFAULT]
            # non_tokens: [source, delimiter/count, optional default_val]
            src  = non_tokens[0] if non_tokens else None
            arg  = non_tokens[1] if len(non_tokens) > 1 else (tokens[1] if len(tokens) > 1 else None)
            # Check for STR_DEFAULT -- present if token list has 'STR_DEFAULT' type
            has_default = any(str(t.type) == 'STR_DEFAULT' for t in tokens)
            default_val = non_tokens[2] if (has_default and len(non_tokens) > 2) else None
            op_name = op_type.replace('str_', '')  # after/before/left/right
            return StringOpExpr(operation=op_name, operand=src, arg=arg, default_val=default_val)
        # mask.all -- source-first: value_expr MASK_ALL "except" (LAST|FIRST) NUMBER
        if op_type == 'mask_all':
            direction = ''
            count = None
            for t in tokens:
                if t.type == 'LAST':
                    direction = 'last'
                elif t.type == 'FIRST':
                    direction = 'first'
                elif t.type == 'NUMBER':
                    count = int(str(t))
            operand = non_tokens[0] if non_tokens else None
            return StringOpExpr(operation='mask.all', operand=operand,
                                arg=count, direction=direction)
        # pad.left/right source-first: value_expr PAD_x TO NUMBER "with" fill
        if op_type in ('pad_left', 'pad_right'):
            operand = non_tokens[0] if non_tokens else None
            num_tok = next((t for t in tokens if t.type == 'NUMBER'), None)
            n = int(str(num_tok)) if num_tok is not None else 0
            fill_tok = next((t for t in tokens if t.type == 'STRING'), None)
            fill = _mohio_decode_string(str(fill_tok)) if fill_tok is not None else ' '
            return StringOpExpr(operation=op_type.replace('_', '.'), operand=operand,
                                arg=n, default_val=fill)
        # truncate.to source-first: value_expr TRUNCATE_TO NUMBER (words|characters|chars)?
        if op_type == 'truncate_to':
            operand = non_tokens[0] if non_tokens else None
            num_tok = next((t for t in tokens if t.type == 'NUMBER'), None)
            n = int(str(num_tok)) if num_tok is not None else 35
            unit = 'words' if any(t.type == 'WORDS_KW' for t in tokens) else 'characters'
            return StringOpExpr(operation='truncate.to', operand=operand,
                                arg=n, direction=unit)
        # Existing ops
        if op_type == 'by_kw':
            src = non_tokens[0] if non_tokens else None
            cnt = non_tokens[1] if len(non_tokens) > 1 else None
            return StringOpExpr(operation='by', operand=src, arg=cnt)
        op_name = {
            'truncate_to': 'truncate.to',
            'mask_all': 'mask.all',
            'trim_kw': 'trim',
            'uppercase_kw': 'uppercase',
            'lowercase_kw': 'lowercase',
            'trim_front': 'trim.front',
            'trim_back': 'trim.back',
            'remove_ws': 'remove.ws',
            'remove_special': 'remove.special',
            'remove_html': 'remove.html',
        }.get(op_type, op_type)
        operand = non_tokens[0] if non_tokens else None
        return StringOpExpr(operation=op_name, operand=operand)




        headers = []
        body = auth = None
        alias = ""
        timeout = 30

        for child in children:
            if not _is_tree(child, "miohttp_body"):
                continue
            toks = [c for c in child.children if isinstance(c, Token)]
            vals = [c for c in child.children if not isinstance(c, Token)]
            key  = str(toks[0]).lower() if toks else ""

            if key == "header":
                # header "Name" value_expr
                hname = str(toks[1]).strip('"') if len(toks) > 1 else ""
                hval  = vals[0] if vals else None
                headers.append((hname, hval))
            elif key == "body":    body    = vals[0] if vals else None
            elif key == "auth":    auth    = vals[0] if vals else None
            elif key == "timeout":
                try: timeout = int(str(toks[1])) if len(toks) > 1 else 30
                except: timeout = 30
            elif key == "as":
                alias = str(toks[1]) if len(toks) > 1 else ""

        return MiohttpStmt(
            method=method, url=url, headers=headers,
            body=body, auth=auth, timeout=timeout, alias=alias
        )

    def concat_expr(self, children):
        from mohio_ast import ConcatExpr
        # Filter out CONCAT_OP tokens -- keep only the value terms
        terms = [c for c in children
                 if not (isinstance(c, Token) and c.type == 'CONCAT_OP')]
        return ConcatExpr(terms=terms)

    # -- miocookie transformer methods (v4.0) ---------------------------------
    def miocookie_set(self, children):
        from mohio_ast import MioCookieSet, Closer
        # Block-form options arrive as ('__ck__', key, value) markers from the aliased clause
        # methods below; fold them onto the node. Formerly the whole body was discarded, so
        # secure / http only / same site / expires / domain / path never reached the runtime.
        opts = {}
        rest = []
        for c in children:
            if isinstance(c, tuple) and len(c) == 3 and c[0] == '__ck__':
                opts[c[1]] = c[2]
            else:
                rest.append(c)
        str_toks = [t for t in rest if isinstance(t, Token) and t.type == 'STRING']
        name = str(str_toks[0]).strip('"').strip("'") if str_toks else None
        # Inline form value: the lone non-token, non-closer node (the block-form `value` clause
        # arrives as a marker, so it is not double-counted here).
        vals = [c for c in rest if not isinstance(c, Token) and not isinstance(c, Closer)]
        inline_value = vals[0] if vals else None
        return MioCookieSet(
            name=name, inline_value=inline_value,
            value=opts.get('value'),
            secure=opts.get('secure'),
            http_only=opts.get('http_only'),
            same_site=opts.get('same_site'),
            expires_seconds=opts.get('expires'),
            domain=opts.get('domain'),
            path=opts.get('path'),
        )

    # Block-form miocookie.set clauses. Each grammar alternative is aliased so it arrives here
    # distinctly (the keyword terminals are filtered, so without the alias `domain "x"` and
    # `path "x"` are indistinguishable). Each returns a ('__ck__', key, value) marker.
    def _ck_string(self, children):
        s = next((c for c in children if isinstance(c, Token) and c.type == 'STRING'), None)
        return str(s).strip('"').strip("'") if s is not None else None

    def cookie_value(self, children):
        return ('__ck__', 'value', next((c for c in children if not isinstance(c, Token)), None))
    def cookie_secure(self, children):
        return ('__ck__', 'secure', True)
    def cookie_http_only(self, children):
        return ('__ck__', 'http_only', True)
    def cookie_same_site(self, children):
        return ('__ck__', 'same_site', self._ck_string(children) or 'lax')
    def cookie_domain(self, children):
        return ('__ck__', 'domain', self._ck_string(children))
    def cookie_path(self, children):
        return ('__ck__', 'path', self._ck_string(children))
    def cookie_expires_never(self, children):
        return ('__ck__', 'expires', 60 * 60 * 24 * 400)   # ~13 months: long-lived persistent cookie
    def cookie_expires(self, children):
        num = next((c for c in children if isinstance(c, Token) and c.type == 'NUMBER'), None)
        unit_node = _first_tree(children, 'time_unit')
        unit = (_token_str(unit_node) if unit_node else 'seconds').strip().lower()
        mult = {'second': 1, 'seconds': 1, 'minute': 60, 'minutes': 60,
                'hour': 3600, 'hours': 3600, 'day': 86400, 'days': 86400,
                'week': 604800, 'weeks': 604800}.get(unit, 1)
        secs = int(float(str(num)) * mult) if num is not None else None
        return ('__ck__', 'expires', secs)

    def miocookie_get_body(self, children): return children

    def limits_block(self, children):
        from mohio_ast import LimitsBlock
        from lark import Token
        max_steps = 0; max_tokens = 0; cost_ceiling = 0.0; timeout = None
        max_calls = 0
        for child in children:
            if not hasattr(child, 'data'): continue
            rule = str(child.data)
            nums = [c for c in child.children
                    if isinstance(c, Token) and c.type in ('NUMBER', 'INT', 'FLOAT')]
            if not nums: continue
            n = str(nums[0])
            if rule == 'limits_max_steps':
                try: max_steps = int(float(n))
                except: pass
            elif rule == 'limits_max_tokens':
                try: max_tokens = int(float(n))
                except: pass
            elif rule == 'limits_max_calls':
                try: max_calls = int(float(n))
                except: pass
            elif rule == 'limits_cost_ceiling':
                try: cost_ceiling = float(n)
                except: pass
            elif rule == 'limits_timeout':
                try: timeout = float(n)
                except: pass
        return LimitsBlock(max_steps=max_steps, max_tokens=max_tokens, max_calls=max_calls,
                           cost_ceiling=cost_ceiling, timeout=timeout)

    # -- sql_block and run_block (Zork critical) -------------------------------
    def raw_sql_content(self, children): return children
    def raw_sql_line(self, children):    return children

    def ai_connect_block(self, children):
        from mohio_ast import AiConnectBlock
        from lark import Token
        names = []
        order_providers = []
        handlers = []
        closer_node = next((c for c in children if isinstance(c, Closer)), None)
        def _extract_order(order_tree):
            provs = []
            for prov in order_tree.children:
                if _is_tree(prov, 'order_provider'):
                    ptoks = [str(t) for t in prov.children if isinstance(t, Token)]
                    pname = ptoks[0] if ptoks else ''
                    pmodel = ptoks[1].strip('"').strip("'") if len(ptoks) > 1 else None
                    provs.append({'provider': pname, 'model': pmodel})
            return provs

        for child in children:
            if _is_tree(child, 'ai_connect_names'):
                names += [str(t) for t in child.children
                          if isinstance(t, Token) and t.type == 'NAME']
            elif _is_tree(child, 'order_block'):
                order_providers.extend(_extract_order(child))
            elif _is_tree(child, 'ai_connect_body'):
                for sub in child.children:
                    if _is_tree(sub, 'order_block'):
                        order_providers.extend(_extract_order(sub))
                    elif not isinstance(sub, (Token, Closer)):
                        handlers.append(sub)
            elif _is_tree(child, 'ai_connect_subgroup'):
                for sub in child.children:
                    if _is_tree(sub, 'ai_connect_names'):
                        names += [str(t) for t in sub.children
                                  if isinstance(t, Token) and t.type == 'NAME']
                    elif _is_tree(sub, 'order_block'):
                        order_providers.extend(_extract_order(sub))
            elif not isinstance(child, (Token, Closer)):
                handlers.append(child)
        return AiConnectBlock(names=names, providers=order_providers, handlers=handlers)



    # -- upsert / save or update (Zork critical) ------------------------------
    def save_or_update_block(self, children):
        from mohio_ast import SaveOrUpdateBlock, FieldValue
        from lark import Token
        source      = None
        matches     = []   # list of MatchClause; multiple are AND-ed -> composite conflict target
        fields      = []
        handlers    = []

        from mohio_ast import DbRef, MatchClause
        for child in children:
            if isinstance(child, Token):
                continue
            if isinstance(child, Closer):
                continue
            if isinstance(child, (FieldValue, DynamicFieldValue)):
                fields.append(child)
                continue
            # match_clause returns a LIST of MatchClause for multiple comma-separated pairs.
            if isinstance(child, list):
                matches.extend(c for c in child if type(c).__name__ == 'MatchClause')
                continue

            # Handle already-transformed AST objects (Lark transforms bottom-up)
            cls_name = type(child).__name__

            if cls_name in ('DbRef', 'SourceRef', 'MioRef'):
                # DbRef has only .table -- prefix "db." to match interpreter expectation
                if hasattr(child, 'table') and child.table:
                    source = f"db.{child.table}"
                elif hasattr(child, 'name'):
                    source = child.name
                else:
                    source = str(child)

            elif cls_name == 'DottedName':
                # Through the real `mio run`/enforce path the pre-tokenizer rewrites
                # `db.saved_games` (db is a known connection name) into a DottedName, not a
                # DbRef -- the same shape `save`/`find` already accept. Without this branch the
                # source stayed None and the table resolved to the invented "unknown", so upsert
                # wrote to a table nobody named (silent on SQLite, ON CONFLICT failure on PG).
                source = '.'.join(str(p) for p in getattr(child, 'parts', []))

            elif cls_name == 'MatchClause':
                matches.append(child)

            elif cls_name in ('OnSuccessHandler', 'OnFailureHandler',
                              'ResultHandler', 'OnResolveHandler'):
                handlers.append(child)

            elif hasattr(child, 'data'):
                # Raw tree (wasn't transformed) -- handle by rule name
                rule = str(child.data)
                if rule == 'source_ref':
                    source = '.'.join(str(t) for t in child.children
                                      if isinstance(t, Token)) or str(child)
                elif rule == 'match_clause':
                    pairs = [c for c in child.children
                             if isinstance(c, tuple) and len(c) == 3 and c[0] == '__pair__']
                    if pairs:
                        for (_t, nm, vv) in pairs:
                            matches.append(MatchClause(field=nm, value=vv))
                    else:
                        toks = [t for t in child.children if isinstance(t, Token) and t.type == 'NAME']
                        vals = [c for c in child.children if not isinstance(c, Token)]
                        if toks:
                            matches.append(MatchClause(field=str(toks[0]),
                                                       value=vals[0] if vals else None))
                elif rule == 'save_field':
                    ftoks = [t for t in child.children if isinstance(t, Token) and t.type == 'NAME']
                    fvals = [c for c in child.children if not isinstance(c, Token)]
                    if ftoks and fvals:
                        fields.append(FieldValue(name=str(ftoks[0]), value=fvals[0]))
                elif rule == 'result_handlers':
                    for h in child.children:
                        if h and not isinstance(h, Token):
                            handlers.append(h)

        # Use the canonical MatchClause dataclass, not an ad-hoc local class. A local class
        # (`<locals>._Match`) is UNPICKLABLE ("Can't get local object ..."), which would make
        # any node carrying it uncacheable, and it is not the same type the rest of the compiler
        # produces for a match. The interpreter reads .field / .value, which MatchClause has.
        # One match -> a single MatchClause (single-column upsert, unchanged). Multiple -> the
        # list, carried through so upsert emits a composite ON CONFLICT. Formerly a multi-field
        # match silently dropped to None here (only a single MatchClause was kept).
        match_obj = matches[0] if len(matches) == 1 else (matches if matches else None)

        return SaveOrUpdateBlock(
            source   = source,
            match    = match_obj,
            fields   = fields,
            handlers = handlers,
        )

    # upsert is an alias for save or update
    def upsert_block(self, children):
        return self.save_or_update_block(children)

    def map_decl(self, children):
        from mohio_ast import MapDecl, MapAliasEntry
        # Detect action form: first non-keyword child is NOT a plain NAME token
        # Action form: MAP_KW Literal/AST-node NAME(through) NAME(alias)
        # Declaration form: MAP_KW NAME(map_name) map_alias_entry* closer
        first_non_kw = next((c for c in children
                             if not (isinstance(c, Token) and c.type == 'MAP_KW')), None)
        # Action form if first child after MAP_KW is NOT a NAME token
        # (it's a transformed value -- Literal, DottedName, etc.)
        is_action = (first_non_kw is not None and
                     not (isinstance(first_non_kw, Token) and
                          first_non_kw.type == 'NAME'))
        if is_action:
            # Action form: map source through map_name as alias
            name_toks = [str(c) for c in children
                         if isinstance(c, Token) and c.type == 'NAME']
            source = first_non_kw  # the value_expr tree
            through = name_toks[0] if name_toks else ""
            alias   = name_toks[1] if len(name_toks) > 1 else ""
            return MapDecl(source=source, through=through, alias=alias)
        else:
            # Declaration form: map name / entries / map: done
            name_tok = next((c for c in children
                             if isinstance(c, Token) and c.type == 'NAME'), None)
            name = str(name_tok) if name_tok else ""
            entries = [c for c in children if isinstance(c, MapAliasEntry)]
            return MapDecl(name=name, entries=entries)

    def map_alias_entry(self, children):
        from mohio_ast import MapAliasEntry
        toks = [c for c in children if isinstance(c, Token)]
        # Arrow token
        arrow = next((str(c) for c in toks
                      if c.type in ('ARROW', 'BIDIR_ARROW', 'BACK_ARROW')), '->')
        modifiers = [c for c in children
                     if isinstance(c, str)
                     and c in ('ignore.case', 'match.case', 'keep.whitespace')]
        # Left and right can be STRING tokens OR transformed value_expr nodes
        non_tok_non_mod = [c for c in children
                           if c not in modifiers
                           and (not isinstance(c, Token) or
                                c.type not in ('ARROW', 'BIDIR_ARROW', 'BACK_ARROW'))]
        # Extract left and right -- could be Token(STRING) or AST node
        def extract_val(c):
            if isinstance(c, Token) and c.type == 'STRING':
                return str(c).strip('"')
            return c  # AST node -- interpreter will eval at runtime
        values = [extract_val(c) for c in non_tok_non_mod
                  if not (isinstance(c, Token) and
                          c.type in ('ARROW', 'BIDIR_ARROW', 'BACK_ARROW', 'MAP_KW'))]
        return MapAliasEntry(
            left=values[0] if values else "",
            right=values[1] if len(values) > 1 else "",
            arrow=arrow,
            modifiers=modifiers
        )

    # -- Moved inside class for Lark transformer dispatch --------------
    def prepend_stmt(self, children):
        from mohio_ast import PrependStmt
        name_tok = next((c for c in children
                         if isinstance(c, Token) and c.type == 'NAME'), None)
        val = next((c for c in children if not isinstance(c, Token)), None)
        return PrependStmt(value=val, target=str(name_tok) if name_tok else "")

    def append_stmt(self, children):
        from mohio_ast import AppendStmt
        name_tok = next((c for c in children
                         if isinstance(c, Token) and c.type == 'NAME'), None)
        val = next((c for c in children if not isinstance(c, Token)), None)
        return AppendStmt(value=val, target=str(name_tok) if name_tok else "")

    def add_stmt(self, children):
        # `add X to LIST` -- the canonical list-grow verb. Same executor as append, but strict:
        # LISTS ONLY (fails loud on a non-list target). append/prepend still handle strings.
        from mohio_ast import AppendStmt
        name_tok = next((c for c in children
                         if isinstance(c, Token) and c.type == 'NAME'), None)
        val = next((c for c in children if not isinstance(c, Token)), None)
        return AppendStmt(value=val, target=str(name_tok) if name_tok else "",
                          strict_list=True)

    def hash_block(self, children):
        from mohio_ast import HashBlock
        from lark import Token, Tree
        # Flatten hash_field results (block form) -- hash_field returns a list
        items = []
        for c in children:
            if isinstance(c, list):
                items.extend(c)
            elif isinstance(c, Tree) and c.data == 'hash_field':
                items.extend(c.children)
            else:
                items.append(c)
        value = None
        alias = None
        algorithm = None
        i = 0
        while i < len(items):
            c = items[i]
            if isinstance(c, Token) and c.type == 'AS' and i + 1 < len(items):
                alias = str(items[i + 1]); i += 2; continue
            if isinstance(c, Token) and c.type == 'USING' and i + 1 < len(items):
                algorithm = str(items[i + 1]); i += 2; continue
            if isinstance(c, Token):
                i += 1; continue
            if _is_tree(c, 'closer'):
                i += 1; continue
            if value is None:
                value = c
            i += 1
        return HashBlock(value=value, alias=alias, algorithm=(algorithm or 'sha256'))

    def replace_block(self, children):
        from mohio_ast import ReplaceBlock
        name_toks = [c for c in children if isinstance(c, Token) and c.type == 'NAME']
        list_entries = [c for c in children if isinstance(c, list)]
        if list_entries:
            # BLOCK form: replace in NAME / "old" with "new" / ... / replace: done
            entries = []
            for child in list_entries:
                toks = [c for c in child if isinstance(c, Token)]
                old_val = str(toks[0]).strip('"').strip("'") if toks else ""
                new_val = next((c for c in child if not isinstance(c, Token)), None)
                if old_val or new_val is not None:
                    entries.append((old_val, new_val))
            return ReplaceBlock(target=str(name_toks[0]) if name_toks else "", entries=entries)
        # INLINE form: replace "old" with "new" in NAME ("as" NAME)?
        string_toks = [c for c in children if isinstance(c, Token) and c.type == 'STRING']
        old_val = str(string_toks[0]).strip('"').strip("'") if string_toks else ""
        new_val = next((c for c in children
                        if not isinstance(c, Token) and not _is_tree(c, 'closer')), None)
        target = str(name_toks[0]) if name_toks else ""
        alias  = str(name_toks[1]) if len(name_toks) > 1 else ""
        return ReplaceBlock(target=target, entries=[(old_val, new_val)], alias=alias)

    def extract_stmt(self, children):
        from mohio_ast import ExtractStmt
        source = next((c for c in children
                       if not isinstance(c, Token)
                       and not _is_tree(c, 'closer')), None)
        name_toks = [str(c) for c in children
                     if isinstance(c, Token) and c.type == 'NAME']
        pattern = name_toks[0] if name_toks else ""
        alias   = name_toks[1] if len(name_toks) > 1 else ""
        return ExtractStmt(source=source, pattern=pattern, alias=alias)

# --------------------------------------------------------------
# MATH HELPER (builds MathExpr from nested math_inner trees)
# --------------------------------------------------------------
# NOTE: _build_math lives at the BOTTOM of this file (module level). It must NOT
# be defined here — a column-0 def mid-class silently ends MohioTransformer and
# orphans every method below it (was the cause of ~110 dead transformer methods).


# --------------------------------------------------------------
# PUBLIC API
# --------------------------------------------------------------

    def random_expr(self, children):
        from mohio_ast import RandomValue
        from lark import Token
        tokens = [c for c in children if isinstance(c, Token)]
        numbers = [c for c in children if isinstance(c, Token) and c.type == 'NUMBER']
        first_type = tokens[0].type if tokens else ''

        if first_type == 'RANDOM_UUID':
            return RandomValue(kind='uuid')
        elif first_type == 'UNIQUE_ID':
            return RandomValue(kind='unique')
        elif first_type == 'RANDOM_COLOR':
            return RandomValue(kind='color')
        elif first_type == 'RANDOM_TOKEN':
            length = int(str(numbers[0])) if numbers else 32
            return RandomValue(kind='token', length=length)
        elif first_type == 'RANDOM_HEX':
            length = int(str(numbers[0])) if numbers else 64
            return RandomValue(kind='hex', length=length)
        elif first_type == 'RANDOM_NUMBER':
            min_v = float(str(numbers[0])) if len(numbers) > 0 else 0
            max_v = float(str(numbers[1])) if len(numbers) > 1 else 100
            return RandomValue(kind='number', min_val=min_v, max_val=max_v)
        elif first_type == 'RANDOM_N':
            n = int(str(tokens[0]).replace('random.', ''))
            return RandomValue(kind='count', count=n)
        else:
            # random_from_source (its own rule) already produced a RandomValue
            from mohio_ast import RandomValue as _RV
            for c in children:
                if isinstance(c, _RV):
                    return c
            # bare `random` -> select (no source)
            from lark import Token as _Tok
            non_tokens = [c for c in children if not isinstance(c, _Tok)]
            source = non_tokens[0] if non_tokens else None
            return RandomValue(kind='select', source=source)

    def random_from_source(self, children):
        """random from <source> — pick one item from a collection."""
        from mohio_ast import RandomValue
        from lark import Token as _Tok
        non_tokens = [c for c in children if not isinstance(c, _Tok)]
        source = non_tokens[0] if non_tokens else None
        return RandomValue(kind='select', source=source)

    def verify_token_stmt(self, children):
        from mohio_ast import VerifyTokenStmt
        from lark import Token, Tree
        source = header = scope = None
        for c in children:
            if isinstance(c, Tree) and c.data == 'verify_source':
                for cc in c.children:
                    if isinstance(cc, Token) and cc.type == 'STRING':
                        header = str(cc).strip('"')
                    elif not isinstance(cc, Token):
                        source = cc
            elif isinstance(c, Tree) and c.data == 'verify_body':
                for cc in c.children:
                    if isinstance(cc, Token) and cc.type == 'STRING':
                        scope = str(cc).strip('"')
        return VerifyTokenStmt(source=source, header=header, scope=scope)

    def give_back_val(self, children):
        from lark import Token
        from mohio_ast import RandomValue
        # Handle give back random / give back random as name / give back random.N as name
        tokens = [c for c in children if isinstance(c, Token)]
        non_tokens = [c for c in children if not isinstance(c, Token)]
        first_type = tokens[0].type if tokens else ''
        if first_type in ('RANDOM_KW', 'RANDOM_N'):
            kind = 'select'
            count = 0
            alias = ""
            if first_type == 'RANDOM_N':
                count = int(str(tokens[0]).replace('random.', ''))
                kind = 'count'
            # Check for AS NAME
            as_idx = next((i for i, t in enumerate(tokens) if t.type == 'AS'), -1)
            if as_idx >= 0 and as_idx + 1 < len(tokens):
                alias = str(tokens[as_idx + 1])
            return RandomValue(kind=kind, count=count, alias=alias)
        # Default -- return first non-token child
        return non_tokens[0] if non_tokens else (tokens[0] if tokens else None)


    # -- run_block transformer method REMOVED 2026-08-01 with the grammar rule (Row 2, Tier 4).
    # `run NAME` task invocation is retired; `call` is the canonical verb. With the rule gone,
    # `run NAME` falls back to an assignment named `run`, which the assignment guard above
    # already refuses (`if name in ('call','run')`) with "not a valid task invocation, use call".

    def call_value(self, children):
        """`total = call add with 2` -- a call used as a VALUE. Reuses CallBlock; the
        interpreter evaluates it and hands back the task's return value."""
        from mohio_ast import CallBlock
        names = [c for c in children if isinstance(c, Token) and c.type == 'NAME']
        arg = next((c for c in children if not isinstance(c, Token)), None)
        return CallBlock(task_name=str(names[0]) if names else "",
                         args=[], inline_arg=arg, alias="",
                         line=_line(names[0]) if names else 0)

    def call_procedure(self, children):
        """`call greet` -- a procedure call: no arguments, no closer."""
        from mohio_ast import CallBlock
        names = [c for c in children if isinstance(c, Token) and c.type == 'NAME']
        return CallBlock(task_name=str(names[0]) if names else "",
                         args=[], inline_arg=None,
                         alias=str(names[1]) if len(names) > 1 else "",
                         line=_line(names[0]) if names else 0)

    def call_block(self, children):
        """call NAME / arg value / call: done -- task invocation.
        call is the canonical verb, explicitly linked to task (locked)."""
        from mohio_ast import RunBlock, FieldValue
        from lark import Token
        name_toks = [c for c in children
                     if isinstance(c, Token) and c.type == 'NAME']
        name_tok = name_toks[0] if name_toks else None
        # A second top-level NAME (after `as`) is the result-capture alias:
        # `call greet with "Aria" as greeting`.
        alias = str(name_toks[1]) if len(name_toks) > 1 else ""
        non_tokens = [c for c in children
                      if not isinstance(c, (Token, Closer))]
        args = [c for c in non_tokens if not isinstance(c, Closer)]
        inline_arg = None
        # `call X with value` -> a single bare value becomes the inline arg.
        # A single named pair (FieldValue) must stay a named arg, not inline.
        if (len(args) == 1 and not isinstance(args[0], FieldValue)
                and not hasattr(args[0], 'children')):
            inline_arg = args[0]
            args = []
        return RunBlock(
            task_name=str(name_tok) if name_tok else "",
            args=args,
            inline_arg=inline_arg,
            alias=alias,
        )

    # NOTE: no hold_list_item transformer method on purpose. The list-form
    # detection in hold_decl() relies on `hold_list_item` subtrees surviving as
    # Lark Trees (via _is_tree). A transformer method here would collapse them to
    # bare tokens before hold_decl runs, breaking list detection (items lost,
    # closer leaks into value, is_list never set). Leave them as Trees.

    def hold_list_decl(self, children):
        """hold name / items / hold: done -- list form"""
        from mohio_ast import HoldDecl
        name_tok = next((c for c in children
                         if isinstance(c, Token) and c.type == 'NAME'), None)
        name = str(name_tok) if name_tok else ""
        items = []
        for child in children:
            if _is_tree(child, 'hold_list_item'):
                val = child.children[0] if child.children else None
                if isinstance(val, Token):
                    raw = str(val)
                    if val.type == 'STRING':
                        raw = raw.strip('"')
                    elif val.type in ('TRUE', 'FALSE'):
                        raw = val.type == 'TRUE'
                    elif val.type == 'NUMBER':
                        raw = float(raw) if '.' in raw else int(raw)
                    items.append(raw)
        return HoldDecl(name=name, is_list=True, items=items)

    def cm_purge_block(self, children):
        # Two forms:
        #   value form: `cm.purge member.id`   -> target (audit/declaration only)
        #   from form:  `cm.purge from db.X / match id to Y` -> source + matches
        #               (the interpreter deletes those matched rows + audits)
        from mohio_ast import CmPurgeBlock, MatchClause
        has_from = any(isinstance(c, Token) and c.type == 'FROM' for c in children)
        subject = None
        for c in children:
            if isinstance(c, Token) and c.type in ('CM_PURGE', 'FROM'):
                continue
            if _is_tree(c, 'cm_purge_body') or _is_tree(c, 'cm_purge_reason'):
                continue
            subject = c          # value_expr (member.id) or source_ref (db.members)
            break
        reason = None
        matches = []
        for c in children:
            if _is_tree(c, 'cm_purge_reason'):
                # `reason <value_expr>` -- the expression is already transformed;
                # keep the node so the interpreter evaluates it at runtime (a
                # literal, a variable, request.field, or a join all work).
                reason = c.children[0] if c.children else None
                continue
            if not _is_tree(c, 'cm_purge_body'):
                continue
            for k in c.children:
                if isinstance(k, MatchClause):
                    matches.append(k)
                elif isinstance(k, list):
                    matches.extend(x for x in k if isinstance(x, MatchClause))
        if has_from:
            return CmPurgeBlock(source=subject, matches=matches, reason=reason)
        return CmPurgeBlock(target=subject, reason=reason)

    def cm_action_stmt(self, children):
        # cm.retain / cm.expire / cm.lock / cm.report / cm.notify all reach here
        # as a flat rule. Dispatch on the leading CM_* token to a real node so
        # each gets its own executor (was a raw Tree -> "no executor").
        from mohio_ast import (CmRetainStmt, CmExpireStmt, CmLockStmt,
                               CmReportStmt, CmNotifyStmt)
        head = next((c for c in children if isinstance(c, Token)
                     and str(c.type).startswith('CM_')), None)
        tok = head.type if head is not None else ''
        non_tokens = [c for c in children if not isinstance(c, Token)]
        if tok == 'CM_RETAIN':
            return CmRetainStmt(value=non_tokens[0] if non_tokens else None,
                                duration=non_tokens[1] if len(non_tokens) > 1 else None)
        if tok == 'CM_EXPIRE':
            return CmExpireStmt(value=non_tokens[0] if non_tokens else None,
                                duration=non_tokens[1] if len(non_tokens) > 1 else None)
        if tok == 'CM_LOCK':
            name = next((c for c in children if isinstance(c, Token)
                         and c.type == 'NAME'), None)
            return CmLockStmt(target=str(name) if name is not None
                              else (non_tokens[0] if non_tokens else None))
        if tok == 'CM_REPORT':
            s = next((c for c in children if isinstance(c, Token)
                      and c.type == 'STRING'), None)
            n = next((c for c in children if isinstance(c, Token)
                      and c.type == 'NAME'), None)
            return CmReportStmt(report_type=_unquote(str(s)) if s is not None else '',
                                target=str(n) if n is not None else None)
        if tok == 'CM_NOTIFY':
            s = next((c for c in children if isinstance(c, Token)
                      and c.type == 'STRING'), None)
            return CmNotifyStmt(event=_unquote(str(s)) if s is not None else '',
                                body=non_tokens)
        return None

    # -- Phase 2 stub transformers ---------------------------------------------

    def resolve_cache(self, children):
        # Tier 1 source. `_CACHE` is a filtered terminal, so the tier is identified by this
        # rule alias rather than by inspecting children -- the previous approach looked for a
        # literal 'cache' token that Lark had already removed, so every tier came back None.
        name_tok = next((c for c in children if isinstance(c, Token)), None)
        return ('__tier__', 'cache', str(name_tok) if name_tok is not None else None)

    def resolve_learned(self, children):
        # Tier 2 source (a db.table / source_ref).
        ref = next((c for c in children if not isinstance(c, Token)), None)
        if ref is None:
            ref = next((c for c in children if isinstance(c, Token)), None)
        return ('__tier__', 'learned', ref)

    def resolve_live(self, children):
        # Tier 3: an invocation of a DECLARED ai.decide block, by name.
        inv = next((c for c in children
                    if type(c).__name__ in ('AiDecideInvoke', 'AiDecideBlock')), None)
        return ('__tier__', 'live', inv)

    def ai_resolve_block(self, children):
        from mohio_ast import AiResolveBlock
        name_tok = next((c for c in children
                         if isinstance(c, Token) and c.type == 'NAME'), None)
        ai_opts, remaining = self._extract_ai_opts(children)
        tiers = {t[1]: t[2] for t in remaining
                 if isinstance(t, tuple) and len(t) == 3 and t[0] == '__tier__'}
        return AiResolveBlock(
            name=str(name_tok) if name_tok else "",
            cache_ref=tiers.get('cache'),
            learned_ref=tiers.get('learned'),
            live_block=tiers.get('live'),
            goal=ai_opts['goal'], persona=ai_opts['persona'],
            context=ai_opts['context'], temperature=ai_opts['temperature'],
            model=ai_opts['model'],
        )

    # RETIRED (2026-08-06): the inline `max steps`/`max cost`/`max time` shorthand inside
    # ai.agent's body. Confirmed empirically before retiring: not a small gap, a whole
    # abandoned syntax family -- none of the three had any transformer handling at all
    # (a raw, untransformed Tree fell through to the generic unwired-construct scan), and
    # mio check's own required-limits validation never recognized the shorthand as
    # satisfying "ai.agent needs a limits declaration" either, so no developer could ever
    # have used it successfully. The `limits` block is the one real, working form.
    # Grammar productions are KEPT (aliased via -> so the transformer can tell the three
    # apart, since the underscore-filtered terminals leave an otherwise-identical bare
    # NUMBER for steps vs cost) so each message is precise, not a raw parse failure --
    # tested directly: removing the grammar alternatives instead produces a bare
    # "No terminal matches" error with no redirect at all, the wrong outcome here.
    def ai_agent_max_steps_shorthand(self, children):
        n = next((str(c) for c in children if isinstance(c, Token)), "N")
        raise MohioCompileError(
            f"`max steps {n}` written directly in an ai.agent body is retired: it was never "
            f"wired to anything. Declare it in the limits block instead:\n"
            f"    limits\n        max steps {n}\n    limits: done")

    def ai_agent_max_cost_shorthand(self, children):
        n = next((str(c) for c in children if isinstance(c, Token)), "N")
        raise MohioCompileError(
            f"`max cost {n}` written directly in an ai.agent body is retired: it was never "
            f"wired to anything. Declare it in the limits block instead:\n"
            f"    limits\n        cost ceiling {n}\n    limits: done")

    def ai_agent_max_time_shorthand(self, children):
        toks = [str(c) for c in children if isinstance(c, Token)]
        n = toks[0] if toks else "N"
        unit_node = _first_tree(children, 'time_unit')
        unit = _token_str(unit_node) if unit_node else (toks[1] if len(toks) > 1 else "seconds")
        raise MohioCompileError(
            f"`max time {n} {unit}` written directly in an ai.agent body is retired: it was "
            f"never wired to anything. The limits block's equivalent is `timeout`, not "
            f"`max time`:\n"
            f"    limits\n        timeout {n} {unit}\n    limits: done")

    def ai_agent_block(self, children):
        from mohio_ast import AiAgentBlock, ToolsBlock
        name_tok = next((c for c in children
                         if isinstance(c, Token) and c.type == 'NAME'), None)
        non_name = [c for c in children if not (isinstance(c, Token) and c.type == 'NAME')]
        # An agent's goal/persona/context/model arrive WRAPPED in ai_agent_body, one per
        # wrapper, while _extract_ai_opts looks for ('ai_opt', key, val) tuples as direct
        # children. So none of them were found and `goal` came out empty -- an agent declared
        # with a goal ran with no instructions at all, silently. Flatten single-item wrappers
        # first so the shared extractor sees what every other ai.* block gives it.
        _flat = []
        for _c in non_name:
            if _is_tree(_c, 'ai_agent_body') and len(getattr(_c, 'children', [])) == 1:
                _inner = _c.children[0]
                if isinstance(_inner, tuple) and len(_inner) == 3 and _inner[0] == 'ai_opt':
                    _flat.append(_inner)
                    continue
            _flat.append(_c)
        ai_opts, remaining = self._extract_ai_opts(_flat)
        # Extract display_name from ai_agent_body children
        display_name = ""
        tools = []
        limits = None
        body = []
        for child in remaining:
            if _is_tree(child, 'ai_agent_body'):
                # A tools grant block rides inside an ai_agent_body wrapper; pull
                # its grants up to the agent's tools field rather than the body.
                inner = [c for c in child.children if not isinstance(c, Token)]
                if len(inner) == 1 and isinstance(inner[0], ToolsBlock):
                    tools = inner[0].grants
                    continue
                # The LIMITS block rides in the same wrapper and must be lifted the same way.
                # It was not, so `node.limits` stayed None and the boundary gate -- the ceilings
                # on steps, tokens, calls and cost that make an agent safe to run -- fell back to
                # defaults. The interpreter now recovers it by digging through the wrapper, but
                # a safety gate should not depend on a rescue: set the field the executor reads.
                from mohio_ast import LimitsBlock as _LimitsBlock, NotConfidentBlock as _NCB, OnFailure as _OF
                if len(inner) == 1 and isinstance(inner[0], _LimitsBlock):
                    limits = inner[0]
                    continue
                # `not confident` is the agent's fallback -- what runs when the model is not
                # sure enough to act. It was left in the wrapper too, so node.not_confident was
                # None and the fallback the developer wrote was never reachable.
                if len(inner) == 1 and isinstance(inner[0], _NCB):
                    # Unwrap it into body so consumers see a NotConfidentBlock rather than a
                    # raw Tree. AiAgentBlock has no dedicated field for it, so body is where it
                    # belongs -- but it must not stay wrapped, or anything scanning body for
                    # the fallback finds a Tree and moves on.
                    body.append(inner[0])
                    continue
                # `on.failure` -- what runs when the agent hard-fails (provider error or a
                # boundary-gate breach). Same disease as not_confident above, left wrapped so
                # _exec_AiAgentBlock's body scan (2026-08-04, Unit 2) could never find it: the
                # executor used to read a `node.handlers` field that does not exist on
                # AiAgentBlock at all, so on.failure never fired for any agent failure.
                if len(inner) == 1 and isinstance(inner[0], _OF):
                    body.append(inner[0])
                    continue
                body_toks = [str(c) for c in child.children if isinstance(c, Token)]
                body_vals = [c for c in child.children if not isinstance(c, Token)]
                if body_toks and body_toks[0].lower() == 'name' and body_vals:
                    display_name = str(body_vals[0]).strip('"')
                elif body_toks and body_toks[0].lower() == 'name' and len(body_toks) > 1:
                    display_name = body_toks[1].strip('"')
                else:
                    body.append(child)
            elif not isinstance(child, Token) and not _is_tree(child, 'closer') and not isinstance(child, Closer):
                body.append(child)
        return AiAgentBlock(
            name=str(name_tok) if name_tok else "",
            display_name=display_name, body=body, tools=tools, limits=limits,
            goal=ai_opts['goal'], persona=ai_opts['persona'],
            context=ai_opts['context'], temperature=ai_opts['temperature'],
            model=ai_opts['model'],
        )

    def tools_entry(self, children):
        # A granted tool is either an ai builtin (mioai.X) or a connector
        # operation reference (Connector.operation -> DottedName).
        from mohio_ast import DottedName
        for c in children:
            if isinstance(c, Token) and c.type == 'MIOAI_REF':
                return str(c)
            if isinstance(c, DottedName):
                return ".".join(c.parts)
        return None

    def tools_block(self, children):
        from mohio_ast import ToolsBlock
        return ToolsBlock(grants=[c for c in children if isinstance(c, str)])
    def languages_block(self, children): return None
    def enterprise_block(self, children): return None

    def view_decl(self, children):
        from mohio_ast import ViewDecl
        name_tok = next((c for c in children
                         if isinstance(c, Token) and c.type == 'NAME'), None)
        return ViewDecl(name=str(name_tok) if name_tok else "")

    def template_decl(self, children):
        from mohio_ast import TemplateDecl
        name_tok = next((c for c in children
                         if isinstance(c, Token) and c.type == 'NAME'), None)
        return TemplateDecl(name=str(name_tok) if name_tok else "")

    def miotest_decl(self, children):
        from mohio_ast import MiotestDecl
        name_tok = next((c for c in children
                         if isinstance(c, Token) and c.type == 'NAME'), None)
        return MiotestDecl(name=str(name_tok) if name_tok else "")

    def it_block(self, children):
        from mohio_ast import ItBlock
        desc_tok = next((c for c in children
                         if isinstance(c, Token) and c.type == 'STRING'), None)
        return ItBlock(description=str(desc_tok).strip('"') if desc_tok else "")

    def replace_entry(self, children):
        return children  # handled in replace_block

    def time_group_clause(self, children):
        """`by day` / `by merchant` / `by merchant by day`.

        This returned None outright, with a comment saying the grouping was handled by the
        find_body context. It was not: the bucket was discarded, so `by hour` and `by year`
        compiled identically and a report grouped by year behaved exactly like one grouped by
        hour. That produces plausible-looking numbers, which is harder to notice than a crash.
        """
        bucket = next((c for c in children
                       if isinstance(c, str)
                       and c in ('hour', 'day', 'week', 'month', 'quarter', 'year')), None)
        field = next((str(c) for c in children
                      if isinstance(c, Token) and c.type == 'NAME'), None)
        return ('__time_group__', field, bucket)

    def ignore_stmt(self, children):
        from mohio_ast import IgnoreStmt
        target = next((str(c) for c in children
                       if isinstance(c, Token)
                       and c.type in ('STRING', 'REL_PATH', 'PATH_LIT', 'NAME')), "")
        return IgnoreStmt(target=target.strip('"'))

    def ai_create_block(self, children):
        # Full block form -- same extraction as ai_create_stmt but richer
        return self.ai_create_stmt(children)

    def give_back_block_body(self, children):
        from mohio_ast import FieldValue
        name_tok = next((c for c in children
                         if isinstance(c, Token) and c.type == 'NAME'), None)
        val = next((c for c in children if not isinstance(c, Token)), None)
        if name_tok and val:
            return FieldValue(name=str(name_tok), value=val)
        return None

    def map_block(self, children): return children
    def custom_block(self, children): return children
    def lang_code(self, children): return str(children[0]) if children else ""


    def string_op_stmt(self, children):
        """uppercase member.name / lowercase product.sku / trim / truncate.to N"""
        from mohio_ast import StringOpExpr
        tokens = [c for c in children if isinstance(c, Token)]
        non_tokens = [c for c in children if not isinstance(c, Token)]
        # Determine operation from first token
        op = str(tokens[0]).lower() if tokens else ""
        operand = non_tokens[0] if non_tokens else None
        # Map token types to operation names
        token_map = {
            'UPPERCASE_KW': 'uppercase',
            'LOWERCASE_KW': 'lowercase', 
            'AS_TITLE': 'as.title',
            'AS_SENTENCE': 'as.sentence',
            'TRIM_FRONT': 'trim.front',
            'TRIM_BACK': 'trim.back',
            'REMOVE_WS': 'remove.ws',
            'REMOVE_SPECIAL': 'remove.special',
            'REMOVE_HTML': 'remove.html',
            'TRUNCATE_TO': 'truncate.to',
            'MASK_ALL': 'mask.all',
        }
        if tokens:
            op = token_map.get(tokens[0].type, str(tokens[0]).lower())
        return StringOpExpr(operation=op, operand=operand)


    def sec_classify_block(self, children): return None
    def sec_classify_body(self, children): return None
    def sec_classify_rule(self, children): return None
    def sec_validate_stmt(self, children): return None
    def sec_threat_list(self, children): return None
    def sec_audit_stmt(self, children): return None
    def sec_nohardcode_stmt(self, children): return None
    def sec_headers_block(self, children): return None
    def sec_header_entry(self, children): return None


    # -- miocookie -- full implementation --------------------------

    def miocookie_get(self, children):
        from mohio_ast import MioCookieGet
        name_tok = next((c for c in children
                         if isinstance(c, Token) and c.type == 'STRING'), None)
        name = str(name_tok).strip('"') if name_tok else ""
        default_val = next((c for c in children
                            if not isinstance(c, Token)), None)
        return MioCookieGet(name=name, default=default_val)

    # `check miocookie.exists "x"` / `player_session miocookie.get "x" default y` -- the
    # EXPRESSION forms. Same nodes as the statement forms; the interpreter's exec handlers
    # already return a MohioValue, so they work in a value slot as-is.
    def miocookie_exists_expr(self, children):
        return self.miocookie_exists(children)

    def miocookie_get_expr(self, children):
        return self.miocookie_get(children)

    def miocookie_delete(self, children):
        from mohio_ast import MioCookieDelete
        name_tok = next((c for c in children
                         if isinstance(c, Token) and c.type == 'STRING'), None)
        return MioCookieDelete(name=str(name_tok).strip('"') if name_tok else "")

    def miocookie_exists(self, children):
        from mohio_ast import MioCookieExists
        name_tok = next((c for c in children
                         if isinstance(c, Token) and c.type == 'STRING'), None)
        return MioCookieExists(name=str(name_tok).strip('"') if name_tok else "")

    # -- miolog ----------------------------------------------------

    def miolog_stmt(self, children):
        from mohio_ast import MioLogStmt
        tok = next((c for c in children if isinstance(c, Token)), None)
        level = str(tok.type).replace('MIOLOG_', '').lower() if tok else 'info'
        val = next((c for c in children if not isinstance(c, Token)), None)
        return MioLogStmt(level=level, value=val)

    # -- miocache --------------------------------------------------

    def miocache_stmt(self, children):
        from mohio_ast import MioCacheStmt
        tok = next((c for c in children if isinstance(c, Token)), None)
        op = str(tok.type).replace('MIOCACHE_', '').lower() if tok else 'get'
        name_tok = next((c for c in children
                         if isinstance(c, Token) and c.type == 'STRING'), None)
        key = str(name_tok).strip('"') if name_tok else ""
        alias_tok = next((c for c in children
                          if isinstance(c, Token) and c.type == 'NAME'), None)
        alias = str(alias_tok) if alias_tok is not None else ""
        vals = [c for c in children if not isinstance(c, Token)]
        return MioCacheStmt(op=op, key=key, values=vals, alias=alias)

    # -- mio* service stubs (not yet implemented) -----------------
    # miomail_stmt and miohttp_stmt are implemented above
    #
    # Not-built dedicated service rules used to `return None`, which dropped the
    # statement from the AST and let it silently no-op. They now route through
    # _not_built_service so the interpreter fails loud at the point of use, one
    # consistent auditable message. tier='commercial' for licensed managed services
    # (miovault/miotranslate/miosecurity...), 'plain' for not-built-yet.

    def _not_built_service(self, children, default_name, tier="plain"):
        from mohio_ast import NotBuiltService
        tok = next((c for c in children if isinstance(c, Token)), None)
        raw = str(tok) if tok is not None else default_name
        line = getattr(tok, 'line', 0) or 0
        service, _, method = raw.partition('.')
        if not service:
            service = default_name
        return NotBuiltService(service=service, method=method, tier=tier, line=line)

    def miofile_stmt(self, children):
        from mohio_ast import MiofileStmt
        toks = [c for c in children if isinstance(c, Token)]
        vals = [c for c in children if not isinstance(c, Token)]
        op_tok = toks[0]
        op = str(op_tok).split('.')[-1]          # "miofile.read" -> "read"
        line = getattr(op_tok, 'line', 0) or 0
        alias = ""
        name_toks = [t for t in toks[1:] if getattr(t, 'type', '') == 'NAME']
        if name_toks:
            alias = str(name_toks[0])
        path    = vals[0] if len(vals) > 0 else None
        content = vals[1] if (op == 'write' and len(vals) > 1) else None
        dest    = vals[1] if (op in ('move', 'copy') and len(vals) > 1) else None
        return MiofileStmt(op=op, path=path, content=content, dest=dest, alias=alias, line=line)
    def mioauth_stmt(self, children):   return self._not_built_service(children, "mioauth")
    def mioauth_body(self, children):   return children
    def mioresponse_stmt(self, children): return self._not_built_service(children, "mioresponse")
    def miostream_stmt(self, children): return self._not_built_service(children, "miostream")
    def mioschedule_run(self, children):
        from mohio_ast import RunSchedule
        from lark import Token
        name = next((str(c) for c in children
                     if isinstance(c, Token) and c.type == 'NAME'), "")
        return RunSchedule(name=name)
    def mioimage_stmt(self, children): return self._not_built_service(children, "mioimage")
    def miopublish_stmt(self, children): return self._not_built_service(children, "miopublish")
    def miopublish_body(self, children): return children
    def miopush_stmt(self, children): return self._not_built_service(children, "miopush")
    def miodata_stmt(self, children): return self._not_built_service(children, "miodata")
    def miosys_stmt(self, children): return self._not_built_service(children, "miosys")
    def mioprint_stmt(self, children): return self._not_built_service(children, "mioprint")
    def miograph_stmt(self, children): return self._not_built_service(children, "miograph")
    def miograph_body(self, children): return children
    def miosecurity_stmt(self, children): return self._not_built_service(children, "miosecurity", tier="commercial")
    def miotranslate_stmt(self, children): return self._not_built_service(children, "miotranslate", tier="commercial")
    def miovault_stmt(self, children): return self._not_built_service(children, "miovault", tier="commercial")


    # -- data primitives ------------------------------------------

    def hash_field(self, children): return children

    def check_against_stmt(self, children):
        from mohio_ast import CheckAgainstStmt
        non_toks = [c for c in children if not isinstance(c, Token)]
        value = non_toks[0] if non_toks else None
        stored = non_toks[1] if len(non_toks) > 1 else None
        # Flatten check_against_body wrappers so on.failure/on.success land directly
        body = []
        for c in non_toks[2:]:
            if isinstance(c, list):
                body.extend(c)
            elif isinstance(c, Closer):
                continue   # block closer -- not a body statement
            else:
                body.append(c)
        return CheckAgainstStmt(value=value, stored=stored, body=body)

    def check_against_body(self, children): return children

    def encode_stmt(self, children):
        from mohio_ast import EncodeStmt
        toks = [c for c in children if isinstance(c, Token) and c.type == 'NAME']
        fmt = str(toks[0]) if toks else "base64"
        alias = str(toks[1]) if len(toks) > 1 else ""
        val = next((c for c in children if not isinstance(c, Token)), None)
        return EncodeStmt(value=val, format=fmt, alias=alias)

    def decode_stmt(self, children):
        from mohio_ast import DecodeStmt
        toks = [c for c in children if isinstance(c, Token) and c.type == 'NAME']
        fmt = str(toks[0]) if toks else "base64"
        alias = str(toks[1]) if len(toks) > 1 else ""
        val = next((c for c in children if not isinstance(c, Token)), None)
        return DecodeStmt(value=val, format=fmt, alias=alias)

    def parse_stmt(self, children):
        from mohio_ast import ParseStmt
        val = next((c for c in children if not isinstance(c, Token)
                    and not _is_tree(c, 'type_name')), None)
        type_node = _first_tree(children, 'type_name')
        type_name = _token_str(type_node) if type_node else "text"
        alias_tok = next((c for c in children if isinstance(c, Token)
                          and c.type == 'NAME'), None)
        return ParseStmt(value=val, type_name=type_name,
                         alias=str(alias_tok) if alias_tok else "")

    def math_func_stmt(self, children):
        from mohio_ast import MathFuncStmt
        func_tok = next((c for c in children if isinstance(c, Token)
                         and c.type in ('ABSOLUTE_KW', 'MINIMUM_KW', 'MAXIMUM_KW',
                                        'AVERAGE', 'SUM_KW', 'PERCENTAGE_KW')), None)
        func = str(func_tok.type).lower().replace('_kw', '') if func_tok else "absolute"
        values = [c for c in children if not isinstance(c, Token)]
        alias_tok = next((c for c in reversed(children) if isinstance(c, Token)
                          and c.type == 'NAME'), None)
        return MathFuncStmt(
            func=func,
            value=values[0] if values else None,
            value2=values[1] if len(values) > 1 else None,
            alias=str(alias_tok) if alias_tok else "")

    def app_config_block(self, children):
        from mohio_ast import AppConfigBlock
        body = [c for c in children if not isinstance(c, Token)]
        return AppConfigBlock(body=body)

    def app_config_body(self, children): return children

    def mioschedule_decl(self, children):
        from mohio_ast import MioScheduleDecl
        name_tok = next((c for c in children if isinstance(c, Token)
                         and c.type == 'NAME'), None)
        body = [c for c in children if not isinstance(c, Token)
                and not _is_tree(c, 'closer') and not isinstance(c, Closer)]
        return MioScheduleDecl(name=str(name_tok) if name_tok else "", body=body)

    def mioschedule_body(self, children): return children
    def script_section(self, children): return None
    def script_body(self, children): return children
    def style_section(self, children): return None
    def style_body(self, children): return children
    def page_script_style(self, children): return None


    # -- service declaration transformers -------------------------

    def miomail_decl(self, children): return None
    def miomail_with_body(self, children): return children
    def miomail_sender_body(self, children): return children
    def miomail_sender_ref(self, children): return children

    def mioauth_decl(self, children): return self._not_built_service(children, "mioauth")
    def mioauth_provider_body(self, children): return children
    def mioauth_password_body(self, children): return children
    def mioauth_mfa_body(self, children): return children
    def mioauth_jwt_body(self, children): return children
    def mioauth_apikey_body(self, children): return children
    def mioauth_apikey_sub_body(self, children): return children
    def mioauth_ldap_body(self, children): return children

    def miofile_decl(self, children):
        from mohio_ast import MiofileDecl
        zones = [c for c in children if isinstance(c, dict) and 'kind' in c]
        line = next((getattr(c, 'line', 0) for c in children if isinstance(c, Token)), 0)
        return MiofileDecl(zones=zones, line=line)

    def miofile_decl_body(self, children):
        _kw = {'local', 'temp', 'cloud'}
        kind = next((str(c) for c in children
                     if isinstance(c, Token) and str(c) in _kw), None)
        names = [str(c) for c in children
                 if isinstance(c, Token) and getattr(c, 'type', '') == 'NAME']
        paths = [str(c) for c in children
                 if isinstance(c, Token) and getattr(c, 'type', '') in ('REL_PATH', 'PATH_LIT', 'STRING')]
        policies = [c for c in children if isinstance(c, dict) and 'policy' in c]
        return {'kind': kind, 'name': (names[0] if names else None),
                'path': (paths[0] if paths else None), 'policies': policies}

    def miofile_policy(self, children):
        _kw = {'accept', 'all', 'except', 'max', 'size', 'expires', 'clean', 'using', 'in', 'after'}
        policy = next((str(c) for c in children
                       if isinstance(c, Token) and str(c) in ('accept', 'max', 'expires', 'clean')), None)
        parts = [c for c in children
                 if not (isinstance(c, Token) and str(c) in _kw)]
        return {'policy': policy, 'parts': parts}

    def miofile_cloud_body(self, children): return children

    def miolog_decl(self, children): return None
    def miolog_decl_body(self, children): return children
    def miolog_alert_body(self, children): return children

    def mioimage_decl(self, children): return self._not_built_service(children, "mioimage")
    def mioimage_decl_body(self, children): return children
    def mioimage_preset_body(self, children): return children

    def miopdf_decl(self, children): return self._not_built_service(children, "miopdf")
    def miopdf_with_body(self, children): return children

    # miosearch had NO transformer method at all (verified live, 2026-08-05): it stayed a
    # raw, untransformed Tree, so it fell into scan_unwired's generic "unwired construct"
    # bucket -- a deliberate WARNING for genuinely-scaffolded features, not the ERROR its
    # actual runtime behavior (a real crash, "No executor for 'miosearch_decl'") deserves.
    # Routing it through _not_built_service, exactly like mioimage_decl/miopdf_decl above,
    # moves it into scan_not_built_services' ERROR-level registry instead, matching its
    # siblings and matching what mio run actually does.
    def miosearch_decl(self, children): return self._not_built_service(children, "miosearch")
    def miosearch_body(self, children): return children
    def miosearch_embed_body(self, children): return children

    def load_pack_stmt(self, children): return None

    def apply_body(self, children): return children

    def modify_block(self, children):
        # modify every X in COLLECTION [where COND] / apply X / field value / apply: done
        from mohio_ast import ModifyBlock, FieldValue
        variant = 'all' if any(isinstance(c, Token) and c.type == 'ALL'
                               for c in children) else 'every'
        noun = next((str(c) for c in children
                     if isinstance(c, Token) and c.type == 'NAME'), None)
        collection = None
        condition = None
        field_changes = []
        cond_types = ('Condition', 'AndCondition', 'OrCondition', 'NotCondition')
        for c in children:
            if isinstance(c, (Token, Closer)):
                continue
            cn = type(c).__name__
            if cn in cond_types and condition is None:
                condition = c
            elif isinstance(c, list):            # modify_body -> list holding apply_block trees
                for item in c:
                    if _is_tree(item, 'apply_block'):
                        for ab in item.children:
                            if isinstance(ab, list) and len(ab) >= 2:
                                nm = next((str(t) for t in ab
                                           if isinstance(t, Token) and t.type == 'NAME'), None)
                                val = next((t for t in ab if not isinstance(t, Token)), None)
                                if nm is not None:
                                    field_changes.append(FieldValue(name=nm, value=val))
            elif _is_tree(c, 'apply_block'):
                for ab in c.children:
                    if isinstance(ab, list) and len(ab) >= 2:
                        nm = next((str(t) for t in ab
                                   if isinstance(t, Token) and t.type == 'NAME'), None)
                        val = next((t for t in ab if not isinstance(t, Token)), None)
                        if nm is not None:
                            field_changes.append(FieldValue(name=nm, value=val))
            elif collection is None and cn not in cond_types:
                collection = c
        return ModifyBlock(variant=variant, noun=noun, collection=collection,
                           condition=condition, body=field_changes)
    def modify_body(self, children): return children

    def inject_stmt(self, children): return None


    def sql_block(self, children):
        from mohio_ast import SqlBlock, Closer
        alias = None
        sql_lines = []
        def gather(node):
            if isinstance(node, Closer):
                return
            if isinstance(node, Token):
                if node.type not in ('SQL_KW', 'SQL', 'DONE', 'DOTTED_CLOSER'):
                    sql_lines.append(str(node))
            elif isinstance(node, list):
                for x in node:
                    gather(x)
            elif hasattr(node, 'children'):
                for x in node.children:
                    gather(x)
        for child in children:
            if isinstance(child, Closer):
                alias = child.as_name
            else:
                gather(child)
        sql_text = "\n".join(l for l in sql_lines if l.strip())
        return SqlBlock(sql=sql_text, alias=alias)

    def show_block(self, children):
        from mohio_ast import ShowBlock
        # Gather raw HTML lines from raw_show_content (mirrors sql_block).
        html_lines = []
        for child in children:
            if _is_tree(child, 'raw_show_content'):
                for line_tree in child.children:
                    if _is_tree(line_tree, 'raw_show_line'):
                        for tok in line_tree.children:
                            html_lines.append(str(tok))
                    elif isinstance(line_tree, Token):
                        html_lines.append(str(line_tree))
            elif isinstance(child, Token) and child.type not in ('SHOW', 'RENDER', 'DONE', 'DOTTED_CLOSER'):
                html_lines.append(str(child))
        html_text = "\n".join(l for l in html_lines if l.strip())
        return ShowBlock(html=html_text)

    def render_block(self, children):
        # `render` is the canonical view container; it captures the HTML view the
        # same way `show ... show: done` does and reuses the same executor. Unlike
        # show, render auto-escapes interpolated {{ }} values (HTML context); the
        # developer's literal markup is left as-is. mio.* helpers come in a later slice.
        block = self.show_block(children)
        block.escape = True

        # `render` defaults to HTML. A <script> in an HTML render is not a style problem --
        # it is a live JS escape hatch that the compiler was silently permitting, in a
        # language whose whole claim is that the compiler enforces the rules. So the
        # container must NAME the content: `render scripts`. Then the hatch is explicit,
        # greppable, and enforceable, instead of an accident of raw-HTML passthrough.
        kind_tok = next((c for c in children
                         if isinstance(c, Token) and c.type == 'RENDER_KIND'), None)
        kind = str(kind_tok) if kind_tok else 'html'
        block.kind = kind
        if kind != 'scripts':
            import re as _r
            body = getattr(block, 'html', '') or ''
            if _r.search(r'<\s*script\b', body, _r.I) or _r.search(r'\bon\w+\s*=', body, _r.I):
                raise MohioCompileError(
                    "Script in an HTML render. `render` (and `render html`) is for markup; "
                    "it does not carry JavaScript. Declare the container instead: "
                    "`render scripts` ... `render: done`. Inline event handlers (onclick=...) "
                    "count as script. For behaviour, prefer MioScript "
                    "(`listen for click on #btn`), which is the supported path.")
        return block

    def title_decl(self, children):
        tok = next((c for c in children if isinstance(c, Token) and c.type == 'STRING'), None)
        return TitleDecl(text=_mohio_decode_string(str(tok)) if tok else "")

    def describe_decl(self, children):
        tok = next((c for c in children if isinstance(c, Token) and c.type == 'STRING'), None)
        return DescribeDecl(text=_mohio_decode_string(str(tok)) if tok else "")

    def save_field(self, children):
        from mohio_ast import FieldValue, DynamicFieldValue
        # Grammar: save_field: NAME value_expr            // static:  troll_dead "true"
        #                    | dotted_name _TO value_expr // dynamic: puzzle.flag_set to "true"
        #
        # Discriminate on STRUCTURE, never on the `to` token. `_TO` is underscore-prefixed,
        # which tells Lark to DISCARD it, so the dynamic form's children are
        # [dotted_name, value_expr] with no `to` anywhere. The old check looked for exactly
        # that discarded token (`any(str(t).lower() == 'to' ...)`), so it was ALWAYS False:
        # every dynamic field fell into the static branch, where the NAME lookup found
        # nothing (name='') and the value slot picked up the FIELD NAME node instead of the
        # value -- writing a column named '' holding the field-name text, and discarding the
        # real value entirely, with no error. Structure is unambiguous and cannot rot the
        # same way (verified: static -> [Token(NAME), ...], dynamic -> [DottedName, ...]).
        tokens = [c for c in children if isinstance(c, Token)]
        non_tokens = [c for c in children if not isinstance(c, Token)]
        name_tok = next((t for t in tokens if t.type == 'NAME'), None)
        if name_tok is None:
            # Dynamic: the field NAME is a node (resolved at runtime), not a NAME token.
            field_name = non_tokens[0] if non_tokens else None
            value = non_tokens[-1] if len(non_tokens) > 1 else None
            return DynamicFieldValue(field_name=field_name, value=value)
        value = non_tokens[0] if non_tokens else None
        return FieldValue(name=str(name_tok), value=value)


    def concat_term(self, children):
        c = children[0] if children else None
        # A bare STRING token still carries its quotes; convert to a clean text
        # Literal so concatenation joins the value, not the quoted token.
        if isinstance(c, Token) and c.type == 'STRING':
            from mohio_ast import Literal
            s = str(c)
            if len(s) >= 2 and s[0] in '"\'' and s[-1] == s[0]:
                s = s[1:-1]
            return Literal(value=s, literal_type='text')
        return c


    def miohttp_stmt(self, children):
        from mohio_ast import MiohttpStmt

        first_tok = next((c for c in children if isinstance(c, Token)), None)
        tok_str = str(first_tok.type).lower() if first_tok else ""
        if "post"   in tok_str: method = "post"
        elif "put"  in tok_str: method = "put"
        elif "delete" in tok_str: method = "delete"
        elif "patch" in tok_str: method = "patch"
        else:                   method = "get"

        # URL is first non-token, non-body child
        url = next((c for c in children
                    if not isinstance(c, Token)
                    and not _is_tree(c, "miohttp_body")
                    and not _is_tree(c, "closer")), None)

        headers = []
        body = auth = None
        alias = ""
        timeout = 30

        for child in children:
            if not _is_tree(child, "miohttp_body"):
                continue
            toks = [c for c in child.children if isinstance(c, Token)]
            vals = [c for c in child.children if not isinstance(c, Token)]
            key  = str(toks[0]).lower() if toks else ""

            if key == "header":
                # header "Name" value_expr
                hname = str(toks[1]).strip('"') if len(toks) > 1 else ""
                hval  = vals[0] if vals else None
                headers.append((hname, hval))
            elif key == "body":    body    = vals[0] if vals else None
            elif key == "auth":    auth    = vals[0] if vals else None
            elif key == "timeout":
                try: timeout = int(str(toks[1])) if len(toks) > 1 else 30
                except: timeout = 30
            elif key == "as":
                alias = str(toks[1]) if len(toks) > 1 else ""

        return MiohttpStmt(
            method=method, url=url, headers=headers,
            body=body, auth=auth, timeout=timeout, alias=alias
        )


def transform(parse_tree, source: str = "") -> Program:
    """
    Transform a Lark parse tree into a Mohio AST Program node.

    Args:
        parse_tree: The tree returned by Lark.parse()
        source:     The original source text (used for error messages)

    Returns:
        Program -- the root AST node

    Raises:
        MohioCloserError   -- closer name mismatch
        MohioZoneError     -- statement in wrong zone
        MohioCompileError  -- other compile-time error
    """
    t = MohioTransformer()
    t.set_source(source)
    try:
        return t.transform(parse_tree)
    except VisitError as ve:
        # Lark wraps any exception raised inside a transformer rule method in a
        # VisitError. Unwrap it so clean compile errors (MohioCompileError,
        # MohioCloserError, ...) surface directly with their own message instead of
        # buried under a VisitError traceback.
        orig = getattr(ve, 'orig_exc', None)
        if orig is not None:
            raise orig
        raise


# --------------------------------------------------------------
# MATH HELPER (module-level — builds MathExpr from math_inner trees)
# Kept at the bottom so it never severs the MohioTransformer class.
# --------------------------------------------------------------
def _build_math(node) -> MathExpr:
    """Recursively build MathExpr from a math_inner tree."""
    if isinstance(node, Tree):
        if node.data == 'math_binop':
            left = _build_math(node.children[0])
            op_token = node.children[1]
            right = _build_math(node.children[2])
            return MathExpr(left=left, op=str(op_token), right=right)
        if node.data == 'math_cmp':
            # A parenthesised comparison: `(score > 100)`. Without this the builder fell through
            # to `return node`, handing a raw Tree to the interpreter, which evaluated the first
            # child -- so `(5 > 2)` became 5 and `(2 > 5)` became 2. A boolean silently turned
            # into a number, and every truthiness test downstream read the wrong thing.
            kids = [c for c in node.children if c is not None]
            op_token = next((c for c in kids if isinstance(c, Token)), None)
            operands = [c for c in kids if not isinstance(c, Token)]
            if op_token is not None and len(operands) >= 2:
                return Condition(left=_build_math(operands[0]),
                                 op=str(op_token),
                                 right=_build_math(operands[1]))
        if node.data == 'math_val':
            return node.children[0] if node.children else None
        if node.data == 'math_inner':
            if len(node.children) == 1:
                return _build_math(node.children[0])
            return _build_math(Tree('math_binop', node.children))
    return node  # already a value node
