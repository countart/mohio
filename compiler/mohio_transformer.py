"""
mohio_transformer.py
Mohio Language — Parse Tree → AST Transformer
Version: 0.1.0 | April 2026 | Particular LLC

Walks the Lark parse tree produced by mohio.lark and emits
AST nodes defined in mohio_ast.py.

Three things happen here that can't happen in the grammar:
  1. Closer validation — strict, Option A. blockname: done must
     match the block it closes. Mismatch = MohioCloserError.
  2. Zone ordering — Declarations / Logic / Output. Validated
     at the program level. Violation = MohioZoneError.
  3. Type coercion — lexer tokens become Python values.
     "0.85" → float, "true" → True, NUMBER → int or float.
"""

from __future__ import annotations
import sys
from lark import Transformer, Token, Tree, v_args

from mohio_ast import (
    Program, Node,
    # Declarations
    SectorDecl, ConnectDecl, ShapeDecl, ShapeField, ShapeFieldModifier,
    TaskDecl, TaskParam, HoldDecl, LockDecl, ReleaseStmt, ComplianceDecl,
    JourneyDecl, MioconnectDecl, IncludeDecl, RequireRoleDecl,
    RateLimitDecl, TimespanDecl, TimespanAnchor, TimespanPrecision,
    TimespanTimezone, TimespanRecurring, TimespanExclude,
    # Closers
    Closer,
    # Routing blocks
    ListenBlock, NewBlock, RequestBlock, ChangeBlock,
    ConnectionBlock, WhileActiveBlock, OnOpen, OnClose,
    # Flow control
    IfBlock, OrIfClause, OtherwiseClause,
    CheckBlock, CheckWhen,
    EachBlock, RepeatBlock, WhileBlock, SectionBlock,
    # Data operations
    RetrieveBlock, FindBlock, SaveBlock, UpdateBlock, RemoveBlock,
    FieldValue, MatchClause, WhereClause, AndClause,
    OrderClause, LimitClause, CacheClause,
    # Result handlers
    OnFailure, OnSuccess, OnError,
    # AI
    AiDecideBlock, ConfidenceCheck, UsingChain, WeighClause,
    NotConfidentBlock, AiAuditStmt, AiExplainStmt,
    AiChainBlock, AiChainStep, AiCreateStmt, AiOverrideStmt,
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
)


# ──────────────────────────────────────────────────────────────
# ERRORS
# ──────────────────────────────────────────────────────────────

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
            f"\nLine {self.close_line} — closer mismatch.\n"
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
            f"\nLine {self.line} — zone ordering violation.\n"
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
        prefix = f"\nLine {self.line} — " if self.line else "\n"
        return f"{prefix}{self.message}"


# ──────────────────────────────────────────────────────────────
# CLOSER STACK
# Tracks open named blocks and validates closer names match.
# ──────────────────────────────────────────────────────────────

class CloserStack:
    """
    Tracks open named blocks and validates that closer names match.

    Usage:
        stack.push("ai.decide", line=18)
        ...
        stack.pop("retrieve", close_line=24)  # raises MohioCloserError

    Blocks that use optional closers (if, each, repeat, while, check)
    are NOT pushed — they dedent-close, not name-close.
    """

    OPTIONAL_CLOSER_BLOCKS = {
        'if_block', 'each_block', 'repeat_block', 'while_block',
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
            # Bare 'done' with empty stack — tolerate (forgiving parser)
            if closer_name is None:
                return None
            raise MohioCompileError(
                f"'{closer_name}: done' found but no open block to close.",
                line=close_line
            )

        expected_name, open_line = self._stack[-1]

        if closer_name is None:
            # Bare 'done' — pop whatever is on top
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


# ──────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────

def _line(tree_or_token) -> int:
    """Extract line number from a Lark tree or token, return 0 if unavailable."""
    if isinstance(tree_or_token, Token):
        return tree_or_token.line or 0
    if hasattr(tree_or_token, 'meta') and hasattr(tree_or_token.meta, 'line'):
        return tree_or_token.meta.line or 0
    return 0


def _token_str(t) -> str:
    """Safely get string value of a token or tree."""
    if isinstance(t, Token):
        return str(t)
    if isinstance(t, Tree):
        # For single-child trees like type_name, value_expr — return the child
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


def _coerce_number(token_str: str):
    """'0.85' → 0.85  |  '10' → 10"""
    try:
        if '.' in token_str:
            return float(token_str)
        return int(token_str)
    except ValueError:
        return token_str


# ──────────────────────────────────────────────────────────────
# TRANSFORMER
# ──────────────────────────────────────────────────────────────

class MohioTransformer(Transformer):
    """
    Transforms the Lark parse tree into Mohio AST nodes.

    Closer validation is enforced inline as each block is built —
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

    # ── PROGRAM ROOT ──────────────────────────────────────────

    def start(self, children):
        stmts = [c for c in children if c is not None]
        return Program(statements=stmts)

    def statement(self, children):
        # Pass through — unwrap the single child
        return children[0] if children else None

    # ── CLOSER ───────────────────────────────────────────────
    # Returns a Closer node. Block rules extract and validate it.

    def closer(self, children):
        # children: [DOTTED_CLOSER, DONE] or just [DONE]
        if len(children) >= 2:
            name_token = children[0]
            return Closer(block_name=str(name_token), line=_line(name_token))
        else:
            # bare done
            done_token = children[0] if children else None
            return Closer(block_name=None, line=_line(done_token) if done_token else 0)

    def _validate_closer(self, block_name: str, children: list, open_line: int = 0) -> Closer:
        """
        Find the Closer node in children, validate it matches block_name.
        Returns the Closer. Raises MohioCloserError on mismatch.
        """
        closer_nodes = [c for c in children if isinstance(c, Closer)]
        if not closer_nodes:
            # No closer found — grammar required one, so this is a parse gap
            # Treat as a compile error
            raise MohioCompileError(
                f"'{block_name}: done' is missing.\n"
                f"Every {block_name} block must end with '{block_name}: done'.",
                line=open_line
            )

        closer_node = closer_nodes[-1]  # last closer in children

        if closer_node.block_name is None:
            # bare 'done' — accept it (forgiving) but note the block name
            closer_node.block_name = block_name
            return closer_node

        if closer_node.block_name != block_name:
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

    # ── DECLARATIONS ─────────────────────────────────────────

    def declaration(self, children):
        return children[0]

    def sector_decl(self, children):
        # children: [SECTOR, type_name]
        type_node = children[-1]
        sector = _token_str(type_node) if not isinstance(type_node, str) else type_node
        return SectorDecl(sector=sector)

    def connect_decl(self, children):
        # CONNECT NAME AS NAME FROM value_expr
        tokens = [c for c in children if isinstance(c, Token)]
        name_tokens = [t for t in tokens if t.type == 'NAME']
        value = [c for c in children if not isinstance(c, Token)]
        alias = str(name_tokens[0]) if len(name_tokens) > 0 else ""
        driver = str(name_tokens[1]) if len(name_tokens) > 1 else ""
        source = value[0] if value else None
        return ConnectDecl(name=alias, driver=driver, source=source)

    def shape_decl(self, children):
        # SHAPE NAME shape_field* closer
        name_token = next((c for c in children if isinstance(c, Token) and c.type == 'NAME'), None)
        open_line = _line(name_token)
        fields = [c for c in children if isinstance(c, ShapeField)]
        self._validate_closer('shape', children, open_line)
        return ShapeDecl(
            name=str(name_token) if name_token else "",
            fields=fields,
            line=open_line,
        )

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
        tokens = [str(c) for c in children if isinstance(c, Token)]
        key = '_'.join(tokens[:2]).lower()  # "never_store", "never_log", etc.
        value = str(children[-1]) if len(children) > 2 else None
        return ShapeFieldModifier(modifier_type=key, value=value)

    def task_decl(self, children):
        name_token = next((c for c in children if isinstance(c, Token) and c.type == 'NAME'), None)
        open_line = _line(name_token)
        name = str(name_token) if name_token else ""
        params_node = _first_tree(children, 'task_params')
        params = self._extract_task_params(params_node) if params_node else []
        type_node = _first_tree(children, 'type_name')
        return_type = _token_str(type_node) if type_node else None
        self._validate_closer(name, children, open_line)
        body = self._body_without_closer(
            [c for c in children if not isinstance(c, Token)
             and not _is_tree(c, 'task_params')
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

    def hold_decl(self, children):
        name_token = next((c for c in children if isinstance(c, Token) and c.type == 'NAME'), None)
        value = next((c for c in children if not isinstance(c, Token)), None)
        return HoldDecl(
            name=str(name_token) if name_token else "",
            value=value,
            line=_line(name_token),
        )

    def lock_decl(self, children):
        name_token = next((c for c in children if isinstance(c, Token) and c.type == 'NAME'), None)
        value = next((c for c in children if not isinstance(c, Token)), None)
        return LockDecl(
            name=str(name_token) if name_token else "",
            value=value,
            line=_line(name_token),
        )

    def compliance_decl(self, children):
        name = next((str(c) for c in children if isinstance(c, Token) and c.type == 'NAME'), "")
        return ComplianceDecl(framework=name)

    def include_decl(self, children):
        path = next((str(c).strip('"') for c in children if isinstance(c, Token)
                     and c.type == 'STRING'), "")
        return IncludeDecl(path=path)

    def require_role_decl(self, children):
        role_list_node = _first_tree(children, 'role_list')
        roles = []
        if role_list_node:
            roles = [str(c).strip('"') for c in role_list_node.children
                     if isinstance(c, Token) and c.type == 'STRING']
        return RequireRoleDecl(roles=roles)

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

    # ── BLOCK STATEMENTS ─────────────────────────────────────

    def block_stmt(self, children):
        return children[0]

    def listen_block(self, children):
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'LISTEN'), None)
        open_line = _line(open_token)
        self._validate_closer('listen', children, open_line)
        listeners = [c for c in children
                     if isinstance(c, (NewBlock, RequestBlock, ConnectionBlock,
                                       ChangeBlock))
                     or (not isinstance(c, (Token, Closer)))]
        body = self._body_without_closer(
            [c for c in children if not isinstance(c, Token)]
        )
        return ListenBlock(listeners=body, line=open_line)

    def listener_body(self, children):
        return children[0] if children else None

    def new_block(self, children):
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

    # ── FLOW CONTROL ──────────────────────────────────────────

    def if_block(self, children):
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'IF'), None)
        open_line = _line(open_token)
        condition = _first_tree(children, None)  # first non-token, non-statement
        cond = next((c for c in children
                     if not isinstance(c, Token)
                     and isinstance(c, (Condition, NotCondition,
                                        AndCondition, OrCondition,
                                        DotStateCheck))
                     or (not isinstance(c, Token) and hasattr(c, '__class__')
                         and 'cond' in getattr(c, '__class__', type).__name__.lower())),
                    None)
        # condition is always the first non-token child
        non_tokens = [c for c in children if not isinstance(c, Token)
                      and not isinstance(c, Closer)]
        cond = non_tokens[0] if non_tokens else None
        or_ifs = [c for c in non_tokens[1:] if isinstance(c, OrIfClause)]
        otherwise = next((c for c in non_tokens if isinstance(c, OtherwiseClause)), None)
        body = [c for c in non_tokens[1:]
                if not isinstance(c, (OrIfClause, OtherwiseClause))]
        return IfBlock(condition=cond, body=body, or_if_clauses=or_ifs,
                       otherwise=otherwise, line=open_line)

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
        non_tokens = [c for c in children
                      if not isinstance(c, (Token, Closer))]
        value = non_tokens[0] if non_tokens else None
        whens = [c for c in non_tokens[1:] if isinstance(c, CheckWhen)]
        otherwise = next((c for c in non_tokens if isinstance(c, OtherwiseClause)), None)
        return CheckBlock(value=value, when_clauses=whens,
                          otherwise=otherwise, line=open_line)

    def check_when(self, children):
        non_tokens = [c for c in children if not isinstance(c, Token)]
        value = non_tokens[0] if non_tokens else None
        body = non_tokens[1:]
        return CheckWhen(value=value, body=body)

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

    # ── DATA OPERATIONS ───────────────────────────────────────

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
        # Unwrap retrieve_body subtrees — MatchClause etc. live inside them
        body_items = []
        for c in children:
            if _is_tree(c, 'retrieve_body'):
                for item in c.children:
                    if isinstance(item, (MatchClause, WhereClause, AndClause,
                                         OrderClause, LimitClause, CacheClause)):
                        body_items.append(item)
            elif isinstance(c, (MatchClause, WhereClause, AndClause,
                                 OrderClause, LimitClause, CacheClause)):
                body_items.append(c)
        handlers_node = next((c for c in children
                              if _is_tree(c, 'result_handlers')), None)
        handlers = []
        if handlers_node:
            handlers = [c for c in handlers_node.children
                        if isinstance(c, (OnFailure, OnSuccess, OnError))]
        return RetrieveBlock(
            name=str(name_token) if name_token else "",
            source=source,
            body=body_items,
            handlers=handlers,
            line=open_line,
        )

    def find_block(self, children):
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'FIND'), None)
        open_line = _line(open_token)
        name_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'NAME'), None)
        source = next((c for c in children if isinstance(c, (DbRef, DottedName))), _first_tree(children, 'source_ref'))
        self._validate_closer('find', children, open_line)
        # Unwrap find_body subtrees
        body_items = []
        for c in children:
            if _is_tree(c, 'find_body'):
                for item in c.children:
                    if isinstance(item, (WhereClause, AndClause,
                                         OrderClause, LimitClause, CacheClause)):
                        body_items.append(item)
            elif isinstance(c, (WhereClause, AndClause,
                                 OrderClause, LimitClause, CacheClause)):
                body_items.append(c)
        handlers_node = next((c for c in children
                              if _is_tree(c, 'result_handlers')), None)
        handlers = []
        if handlers_node:
            handlers = [c for c in handlers_node.children
                        if isinstance(c, (OnFailure, OnSuccess, OnError))]
        return FindBlock(
            name=str(name_token) if name_token else "",
            source=source,
            body=body_items,
            handlers=handlers,
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
        fields = [c for c in children if isinstance(c, FieldValue)]
        return SaveBlock(target=source, fields=fields, line=open_line)

    def update_block(self, children):
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'UPDATE'), None)
        open_line = _line(open_token)
        source = next((c for c in children if isinstance(c, (DbRef, DottedName))), None) or _first_tree(children, 'source_ref')
        self._validate_closer('update', children, open_line)
        body = [c for c in children
                if isinstance(c, (MatchClause, FieldValue))]
        return UpdateBlock(source=source, body=body, line=open_line)

    def remove_block(self, children):
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'REMOVE'), None)
        open_line = _line(open_token)
        source = next((c for c in children if isinstance(c, (DbRef, DottedName))), None) or _first_tree(children, 'source_ref')
        match = next((c for c in children if isinstance(c, MatchClause)), None)
        self._validate_closer('remove', children, open_line)
        handlers_node = next((c for c in children
                              if _is_tree(c, 'result_handlers')), None)
        handlers = []
        if handlers_node:
            handlers = [c for c in handlers_node.children
                        if isinstance(c, (OnFailure, OnSuccess, OnError))]
        return RemoveBlock(source=source, match=match,
                           handlers=handlers, line=open_line)

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

    def match_clause(self, children):
        name_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'NAME'), None)
        value = next((c for c in children if not isinstance(c, Token)), None)
        return MatchClause(field=str(name_token) if name_token else "", value=value)

    def where_clause(self, children):
        dotted = next((c for c in children if isinstance(c, DottedName)), None)
        value = next((c for c in children
                      if not isinstance(c, Token) and not isinstance(c, DottedName)), None)
        return WhereClause(
            field='.'.join(dotted.parts) if dotted else "",
            value=value,
        )

    def and_clause(self, children):
        dotted = next((c for c in children if isinstance(c, DottedName)), None)
        value = next((c for c in children
                      if not isinstance(c, Token) and not isinstance(c, DottedName)), None)
        return AndClause(
            field='.'.join(dotted.parts) if dotted else "",
            value=value,
        )

    def order_clause(self, children):
        dotted = next((c for c in children if isinstance(c, DottedName)), None)
        direction = next(
            (str(c) for c in children
             if isinstance(c, Token) and str(c) in ('asc', 'desc')), 'asc'
        )
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

    def timespan_ref_clause(self, children):
        name = next((str(c) for c in children
                     if isinstance(c, Token) and c.type == 'NAME'), "")
        return Node()  # placeholder — timespan refs resolved in interpreter

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
        inline = next((c for c in children
                       if not isinstance(c, Token)
                       and not isinstance(c, (OnFailure, OnSuccess, OnError,
                                              GiveBackStmt, HaltStmt))), None)
        body = [c for c in children
                if not isinstance(c, Token) and c is not inline]
        return OnFailure(body=body)

    def on_success_handler(self, children):
        body = [c for c in children if not isinstance(c, Token)]
        return OnSuccess(body=body)

    def on_error_handler(self, children):
        body = [c for c in children if not isinstance(c, Token)]
        return OnError(body=body)

    # ── AI PRIMITIVES ─────────────────────────────────────────

    def ai_decide_block(self, children):
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'AI_DECIDE'), None)
        open_line = _line(open_token)
        name_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'NAME'), None)
        name = str(name_token) if name_token else ""
        arg_list_node = _first_tree(children, 'arg_list')
        args = list(arg_list_node.children) if arg_list_node else []
        type_node = _first_tree(children, 'type_name')
        return_type = _token_str(type_node) if type_node else "any"
        self._validate_closer('ai.decide', children, open_line)
        body = self._body_without_closer(
            [c for c in children
             if not isinstance(c, Token)
             and not _is_tree(c, 'arg_list')
             and not _is_tree(c, 'type_name')]
        )
        # Semantic check: every ai.decide must have a not confident block.
        # If confidence falls below threshold and there is no fallback,
        # the program has no defined behaviour — that is a compile error.
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
            name=name, args=args, return_type=return_type,
            body=body, line=open_line,
        )

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

    def ai_explain_stmt(self, children):
        opts = _filter_trees(children, 'ai_explain_opt')
        audience = None
        fmt = None
        for opt in opts:
            tokens = [c for c in opt.children if isinstance(c, Token)]
            kw = next((str(t) for t in tokens
                       if t.type in ('AUDIENCE', 'FORMAT')), "")
            val = next((str(t).strip('"') for t in tokens
                        if t.type == 'STRING'), "")
            if kw == 'audience':
                audience = val
            elif kw == 'format':
                fmt = val
        decision_name = next((str(c) for c in children
                               if isinstance(c, Token) and c.type == 'NAME'), None)
        return AiExplainStmt(decision_name=decision_name,
                             audience=audience, format=fmt)

    def ai_chain_block(self, children):
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'AI_CHAIN'), None)
        open_line = _line(open_token)
        name_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'NAME'), None)
        name = str(name_token) if name_token else ""
        self._validate_closer('ai.chain', children, open_line)
        steps = [c for c in children if isinstance(c, AiChainStep)]
        return AiChainBlock(name=name, steps=steps, line=open_line)

    def ai_chain_step(self, children):
        name_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'NAME'), None)
        provider = str(name_token) if name_token else ""
        body = [c for c in children if not isinstance(c, Token)]
        return AiChainStep(provider=provider, body=body)

    def ai_chain_step_body(self, children):
        return children[0] if children else None

    def ai_create_stmt(self, children):
        type_node = _first_tree(children, 'ai_create_type')
        create_type = _token_str(type_node) if type_node else ""
        value = next((c for c in children
                      if not isinstance(c, Token)
                      and not _is_tree(c, 'ai_create_type')), None)
        return AiCreateStmt(create_type=create_type, prompt=value)

    def ai_create_type(self, children):
        return children[0] if children else Tree('ai_create_type', [])

    def ai_override_stmt(self, children):
        name = next((str(c) for c in children
                     if isinstance(c, Token) and c.type == 'NAME'), "")
        value = next((c for c in children if not isinstance(c, Token)), None)
        return AiOverrideStmt(name=name, value=value)

    # ── TRY / CATCH / ALWAYS ──────────────────────────────────

    def try_block(self, children):
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'TRY'), None)
        open_line = _line(open_token)
        catches = [c for c in children if isinstance(c, CatchClause)]
        always = next((c for c in children if isinstance(c, AlwaysClause)), None)
        body = [c for c in children
                if not isinstance(c, Token)
                and not isinstance(c, (CatchClause, AlwaysClause))]
        return TryBlock(body=body, catch_clauses=catches, always=always,
                        line=open_line)

    def catch_clause(self, children):
        type_node = _first_tree(children, 'catch_type')
        catch_type = _token_str(type_node) if type_node else None
        alias_token = next((c for c in children
                            if isinstance(c, Token) and c.type == 'NAME'), None)
        body = [c for c in children
                if not isinstance(c, Token) and not _is_tree(c, 'catch_type')]
        return CatchClause(
            catch_type=catch_type,
            alias=str(alias_token) if alias_token else None,
            body=body,
        )

    def always_clause(self, children):
        body = [c for c in children if not isinstance(c, Token)]
        return AlwaysClause(body=body)

    # ── ACTION STATEMENTS ─────────────────────────────────────

    def action_stmt(self, children):
        return children[0]

    def give_back_stmt(self, children):
        open_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'GIVE'), None)
        open_line = _line(open_token)
        status_node = _first_tree(children, 'http_status')
        status = None
        if status_node:
            num = next((c for c in status_node.children
                        if isinstance(c, Token) and c.type == 'NUMBER'), None)
            status = _coerce_number(str(num)) if num else None
        # value is the first non-token, non-status child
        non_tokens = [c for c in children
                      if not isinstance(c, Token) and not _is_tree(c, 'http_status')
                      and not _is_tree(c, 'give_back_mod')]
        value = non_tokens[0] if non_tokens else None
        mod_node = _first_tree(children, 'give_back_mod')
        mod = None
        if mod_node:
            mod = mod_node.children[-1] if mod_node.children else None
        return GiveBackStmt(status=status, value=value, modifier=mod,
                            line=open_line)

    def jump_to_stmt(self, children):
        dest = next((c for c in children
                     if isinstance(c, Token)
                     and c.type in ('PATH_LIT', 'STRING')), None)
        if dest is None:
            dest = next((c for c in children if isinstance(c, DottedName)), None)
        return JumpToStmt(destination=str(dest) if isinstance(dest, Token) else dest)

    def halt_stmt(self, children):
        return HaltStmt()

    def stop_stmt(self, children):
        return StopStmt()

    def skip_stmt(self, children):
        return SkipStmt()

    def show_stmt(self, children):
        value = next((c for c in children if not isinstance(c, Token)
                      and not _is_tree(c, 'show_mod')), None)
        mod_node = _first_tree(children, 'show_mod')
        mod = mod_node.children[-1] if mod_node and mod_node.children else None
        return ShowStmt(value=value, modifier=mod)

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

    def service_call_stmt(self, children):
        dotted = next((c for c in children if isinstance(c, DottedName)), None)
        if dotted and len(dotted.parts) >= 2:
            service = dotted.parts[0]
            method = '.'.join(dotted.parts[1:])
        else:
            service = str(dotted) if dotted else ""
            method = ""
        non_dotted = [c for c in children
                      if not isinstance(c, (Token, DottedName))]
        args = non_dotted[0] if non_dotted else None
        params = non_dotted[1:] if len(non_dotted) > 1 else []
        return ServiceCallStmt(service=service, method=method,
                               args=args, params=params)

    def dotted_name_with_dot(self, children):
        parts = [str(c) for c in children if isinstance(c, Token) and c.type == 'NAME']
        return DottedName(parts=parts)

    def inline_action(self, children):
        return children[0] if children else None

    # ── ASSIGNMENT ────────────────────────────────────────────

    def assignment(self, children):
        # SET? NAME (AS type_name)? =? value_expr
        name_token = next((c for c in children
                           if isinstance(c, Token) and c.type == 'NAME'), None)
        type_node = _first_tree(children, 'type_name')
        type_name = _token_str(type_node) if type_node else None
        value = next((c for c in children
                      if not isinstance(c, Token)
                      and not _is_tree(c, 'type_name')), None)
        return Assignment(
            name=str(name_token) if name_token else "",
            type_name=type_name,
            value=value,
            line=_line(name_token),
        )

    # ── CONDITIONS ────────────────────────────────────────────

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

    # ── VALUE EXPRESSIONS ─────────────────────────────────────

    def value_expr(self, children):
        child = children[0] if children else None
        # Lark inlines single-terminal rules — namespace ref tokens arrive raw
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
                return Literal(value=_coerce_number(v), literal_type='number', line=_line(child))
            if t == 'STRING':
                return Literal(value=v.strip('"'), literal_type='string', line=_line(child))
            if t == 'TEMPLATE_STR':
                return TemplateString(template=v, line=_line(child))
            if t == 'NOW_CALL':
                return TimeExpr(base='now()', line=_line(child))
            if t in ('TODAY', 'YESTERDAY', 'LAST_WEEK', 'LAST_MONTH',
                     'LAST_QUARTER', 'LAST_YEAR', 'THIS_WEEK', 'THIS_MONTH',
                     'THIS_QUARTER', 'THIS_YEAR'):
                return TimeExpr(base=v, line=_line(child))
            # NAME or anything else — wrap as dotted name
            return DottedName(parts=[v], line=_line(child))
        return child

    def literal(self, children):
        token = children[0]
        if token.type in ('TRUE', 'FALSE'):
            return Literal(value=(token.type == 'TRUE'), literal_type='bool')
        if token.type in ('NULL_KW', 'NONE_KW'):
            return Literal(value=None, literal_type='null')
        if token.type == 'NUMBER':
            return Literal(value=_coerce_number(str(token)), literal_type='number')
        if token.type == 'STRING':
            return Literal(value=str(token).strip('"'), literal_type='string')
        return Literal(value=str(token), literal_type='unknown')

    def dotted_name(self, children):
        parts = [str(c) for c in children if isinstance(c, Token) and c.type == 'NAME']
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
        # children contains the content between ( ) — find math_inner or math_binop
        for c in children:
            if isinstance(c, Tree):
                return _build_math(c)
            if isinstance(c, MathExpr):
                return c
        # Fallback — single value
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
        items = [c for c in children if not isinstance(c, Token)]
        return ListLiteral(items=items)

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

    # ── TIME ──────────────────────────────────────────────────

    def time_expr(self, children):
        token = children[0] if children else None
        if isinstance(token, Token):
            base = str(token)
            # now() with optional offset
            if token.type == 'NOW_CALL':
                dur = next((c for c in children if isinstance(c, DurationExpr)), None)
                op_token = next((c for c in children
                                 if isinstance(c, Token)
                                 and str(c) in ('+', '-')), None)
                op = str(op_token) if op_token else None
                return TimeExpr(base='now()', offset_op=op, offset=dur)
            return TimeExpr(base=base)
        return TimeExpr(base='')

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


# ──────────────────────────────────────────────────────────────
# MATH HELPER (builds MathExpr from nested math_inner trees)
# ──────────────────────────────────────────────────────────────

def _build_math(node) -> MathExpr:
    """Recursively build MathExpr from a math_inner tree."""
    if isinstance(node, Tree):
        if node.data == 'math_binop':
            left = _build_math(node.children[0])
            op_token = node.children[1]
            right = _build_math(node.children[2])
            return MathExpr(left=left, op=str(op_token), right=right)
        if node.data == 'math_val':
            return node.children[0] if node.children else None
        if node.data == 'math_inner':
            if len(node.children) == 1:
                return _build_math(node.children[0])
            return _build_math(Tree('math_binop', node.children))
    return node  # already a value node


# ──────────────────────────────────────────────────────────────
# PUBLIC API
# ──────────────────────────────────────────────────────────────

def transform(parse_tree, source: str = "") -> Program:
    """
    Transform a Lark parse tree into a Mohio AST Program node.

    Args:
        parse_tree: The tree returned by Lark.parse()
        source:     The original source text (used for error messages)

    Returns:
        Program — the root AST node

    Raises:
        MohioCloserError   — closer name mismatch
        MohioZoneError     — statement in wrong zone
        MohioCompileError  — other compile-time error
    """
    t = MohioTransformer()
    t.set_source(source)
    return t.transform(parse_tree)
