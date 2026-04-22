"""
mohio_interpreter.py
Mohio Language — AST Interpreter
Version: 0.1.0 | April 2026 | Particular LLC

Walks the AST produced by mohio_transformer.py and executes it.

Phase 1 scope:
  - Full execution of the fraud detection demo
  - Real SQLite database (test fixture)
  - Mocked AI provider (returns controlled responses)
  - Real control flow: if/check/each/repeat/while
  - Real data ops: retrieve/find/save/update/remove/transaction
  - Real closers, real error handling, real audit logging

Architecture:
  - Context   — the runtime environment (variables, DB, config)
  - MohioValue — runtime value wrapper
  - Interpreter — one exec_* method per AST node type
  - AiRuntime  — pluggable AI backend (mock for Phase 1)
  - DbRuntime  — pluggable database backend (SQLite for Phase 1)
  - AuditLog   — structured audit trail (JSON lines)
"""

from __future__ import annotations
import os, sys, json, sqlite3, datetime, traceback
from dataclasses import dataclass, field
from typing import Any, Optional, Callable
from pathlib import Path

sys.path.insert(0, '/home/claude')

from mohio_ast import (
    Program, Node,
    SectorDecl, ConnectDecl, ShapeDecl, ShapeField,
    TaskDecl, HoldDecl, LockDecl, ReleaseStmt, ComplianceDecl,
    RequireRoleDecl, IncludeDecl, TimespanDecl,
    ListenBlock, NewBlock, RequestBlock, ConnectionBlock,
    WhileActiveBlock,
    IfBlock, OrIfClause, OtherwiseClause,
    CheckBlock, CheckWhen,
    EachBlock, RepeatBlock, WhileBlock,
    RetrieveBlock, FindBlock, SaveBlock, UpdateBlock, RemoveBlock,
    FieldValue, MatchClause, WhereClause, AndClause,
    OnFailure, OnSuccess, OnError,
    AiDecideBlock, ConfidenceCheck, WeighClause,
    NotConfidentBlock, AiAuditStmt, AiExplainStmt,
    AiChainBlock, AiCreateStmt, AiOverrideStmt,
    TryBlock, CatchClause, AlwaysClause, TransactionBlock,
    GiveBackStmt, JumpToStmt, HaltStmt, StopStmt, SkipStmt,
    ShowStmt, RaiseStmt, SendStmt, ServiceCallStmt,
    Assignment,
    Condition, NotCondition, AndCondition, OrCondition, DotStateCheck,
    Literal, DottedName, EnvRef, SecretRef, DbRef, ShRef,
    FuncCall, MathExpr, TemplateString, ListLiteral, MapLiteral,
    TimeExpr, DatetimeExpr, DurationExpr,
)


# ──────────────────────────────────────────────────────────────
# RUNTIME SIGNALS
# These exceptions drive control flow — they are not errors.
# ──────────────────────────────────────────────────────────────

class _GiveBack(Exception):
    """give back — return value from a block."""
    def __init__(self, status=None, value=None, modifier=None):
        self.status = status
        self.value = value
        self.modifier = modifier

class _Halt(Exception):
    """halt — stop all execution."""
    pass

class _Stop(Exception):
    """stop — exit current loop."""
    pass

class _Skip(Exception):
    """skip — next loop iteration."""
    pass

class _Jump(Exception):
    """jump to — navigate to path."""
    def __init__(self, destination):
        self.destination = destination

class _Raise(Exception):
    """raise — throw a named error."""
    def __init__(self, error_name=None, message=None):
        self.error_name = error_name
        self.message = message
    def __str__(self):
        if self.error_name:
            return f"{self.error_name}: {self.message}"
        return str(self.message)


# ──────────────────────────────────────────────────────────────
# MOHIO RUNTIME VALUE
# All values in Mohio are MohioValue instances at runtime.
# ──────────────────────────────────────────────────────────────

class MohioValue:
    """
    Runtime value wrapper. Holds a Python value and its Mohio type.
    Supports attribute access for shapes: member.email → member["email"]
    """

    def __init__(self, value: Any, mohio_type: str = "any"):
        self._value = value
        self._type = mohio_type

    @property
    def value(self):
        return self._value

    @property
    def mohio_type(self):
        return self._type

    def get(self, key: str, default=None):
        """Attribute access — user.email, transaction.amount etc."""
        if isinstance(self._value, dict):
            return MohioValue(self._value.get(key, default))
        if hasattr(self._value, key):
            return MohioValue(getattr(self._value, key))
        return MohioValue(default)

    def __repr__(self):
        return f"MohioValue({self._value!r}, type={self._type!r})"

    def __bool__(self):
        return bool(self._value)

    def __eq__(self, other):
        if isinstance(other, MohioValue):
            return self._value == other._value
        return self._value == other

    def __lt__(self, other):
        v = other._value if isinstance(other, MohioValue) else other
        return self._value < v

    def __gt__(self, other):
        v = other._value if isinstance(other, MohioValue) else other
        return self._value > v

    def __le__(self, other):
        v = other._value if isinstance(other, MohioValue) else other
        return self._value <= v

    def __ge__(self, other):
        v = other._value if isinstance(other, MohioValue) else other
        return self._value >= v

    def __add__(self, other):
        v = other._value if isinstance(other, MohioValue) else other
        return MohioValue(self._value + v)

    def __sub__(self, other):
        v = other._value if isinstance(other, MohioValue) else other
        return MohioValue(self._value - v)

    def __mul__(self, other):
        v = other._value if isinstance(other, MohioValue) else other
        return MohioValue(self._value * v)

    def __truediv__(self, other):
        v = other._value if isinstance(other, MohioValue) else other
        return MohioValue(self._value / v)

    def __mod__(self, other):
        v = other._value if isinstance(other, MohioValue) else other
        return MohioValue(self._value % v)

    def to_python(self):
        """Unwrap to raw Python value."""
        return self._value


# ──────────────────────────────────────────────────────────────
# CONTEXT — Runtime environment
# ──────────────────────────────────────────────────────────────

class Context:
    """
    Runtime environment — variables, constants, shapes, DB connections.
    Scoped: each block gets a child context; names resolve up the chain.
    """

    def __init__(self, parent: Optional[Context] = None):
        self._vars: dict[str, MohioValue] = {}
        self._constants: dict[str, MohioValue] = {}   # hold / lock
        self._shapes: dict[str, ShapeDecl] = {}
        self._tasks: dict[str, TaskDecl] = {}
        self._connections: dict[str, Any] = {}        # name → DbRuntime
        self._sector: Optional[str] = None
        self._compliance: list[str] = []
        self._roles: list[str] = []                   # current request roles
        self._current_request: Optional[dict] = None
        self._parent = parent
        self._audit_log: list[dict] = []

    def child(self) -> Context:
        """Create a child scope."""
        ctx = Context(parent=self)
        return ctx

    # Variable access

    def set(self, name: str, value: Any, immutable: bool = False):
        """Bind a name in the current scope."""
        v = value if isinstance(value, MohioValue) else MohioValue(value)
        if immutable:
            self._constants[name] = v
        else:
            self._vars[name] = v

    def get(self, name: str) -> MohioValue:
        """Resolve a name — walk up scope chain."""
        if name in self._vars:
            return self._vars[name]
        if name in self._constants:
            return self._constants[name]
        if self._parent:
            return self._parent.get(name)
        return MohioValue(None)

    def get_dotted(self, parts: list[str]) -> MohioValue:
        """Resolve dotted name — user.email, transaction.amount."""
        root = self.get(parts[0])
        for part in parts[1:]:
            root = root.get(part)
        return root

    def set_shape(self, name: str, decl: ShapeDecl):
        self._shapes[name] = decl

    def get_shape(self, name: str) -> Optional[ShapeDecl]:
        if name in self._shapes:
            return self._shapes[name]
        if self._parent:
            return self._parent.get_shape(name)
        return None

    def set_task(self, name: str, decl: TaskDecl):
        self._tasks[name] = decl

    def get_task(self, name: str) -> Optional[TaskDecl]:
        if name in self._tasks:
            return self._tasks[name]
        if self._parent:
            return self._parent.get_task(name)
        return None

    def set_connection(self, name: str, conn):
        self._connections[name] = conn

    def get_connection(self, name: str = 'db'):
        if name in self._connections:
            return self._connections[name]
        if self._parent:
            return self._parent.get_connection(name)
        return None

    def get_env(self, key: str) -> MohioValue:
        val = os.environ.get(key)
        return MohioValue(val, 'text')

    def add_audit(self, entry: dict):
        entry['_ts'] = datetime.datetime.utcnow().isoformat()
        self._audit_log.append(entry)
        if self._parent:
            self._parent.add_audit(entry)

    def has_role(self, role: str) -> bool:
        if role in self._roles:
            return True
        if self._parent:
            return self._parent.has_role(role)
        return False

    def set_roles(self, roles: list[str]):
        self._roles = roles


# ──────────────────────────────────────────────────────────────
# DATABASE RUNTIME — SQLite for Phase 1
# ──────────────────────────────────────────────────────────────

class DbRuntime:
    """
    SQLite-backed database runtime for Phase 1.
    Translates Mohio retrieve/find/save/update/remove operations
    into SQL statements against a SQLite database.
    """

    def __init__(self, db_path: str = ':memory:'):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._in_transaction = False

    def ensure_table(self, table: str, columns: list[str]):
        """Create table if not exists — used by test fixture."""
        cols_sql = ', '.join(f'{c} TEXT' for c in columns)
        self.conn.execute(
            f'CREATE TABLE IF NOT EXISTS {table} ({cols_sql})'
        )
        self.conn.commit()

    def retrieve_one(self, table: str, match_field: str,
                     match_value: Any) -> Optional[dict]:
        """Fetch single row by exact match."""
        cur = self.conn.execute(
            f'SELECT * FROM {table} WHERE {match_field} = ? LIMIT 1',
            (match_value,)
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def find_many(self, table: str, where: dict,
                  limit: Optional[int] = None,
                  order_by: Optional[str] = None,
                  order_dir: str = 'asc') -> list[dict]:
        """Search with multiple conditions."""
        clauses = []
        params = []
        for field, value in where.items():
            clauses.append(f'{field} = ?')
            params.append(value)
        sql = f'SELECT * FROM {table}'
        if clauses:
            sql += ' WHERE ' + ' AND '.join(clauses)
        if order_by:
            sql += f' ORDER BY {order_by} {order_dir.upper()}'
        if limit:
            sql += f' LIMIT {limit}'
        cur = self.conn.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]

    def save(self, table: str, fields: dict) -> int:
        """Insert new row."""
        cols = ', '.join(fields.keys())
        placeholders = ', '.join('?' for _ in fields)
        cur = self.conn.execute(
            f'INSERT INTO {table} ({cols}) VALUES ({placeholders})',
            list(fields.values())
        )
        if not self._in_transaction:
            self.conn.commit()
        return cur.lastrowid

    def update(self, table: str, updates: dict, match_field: str,
               match_value: Any) -> int:
        """Update rows matching condition."""
        set_clause = ', '.join(f'{k} = ?' for k in updates)
        params = list(updates.values()) + [match_value]
        cur = self.conn.execute(
            f'UPDATE {table} SET {set_clause} WHERE {match_field} = ?',
            params
        )
        if not self._in_transaction:
            self.conn.commit()
        return cur.rowcount

    def remove(self, table: str, match_field: str, match_value: Any) -> int:
        """Delete rows matching condition."""
        cur = self.conn.execute(
            f'DELETE FROM {table} WHERE {match_field} = ?',
            (match_value,)
        )
        if not self._in_transaction:
            self.conn.commit()
        return cur.rowcount

    def begin_transaction(self):
        self._in_transaction = True

    def commit_transaction(self):
        self.conn.commit()
        self._in_transaction = False

    def rollback_transaction(self):
        self.conn.rollback()
        self._in_transaction = False

    def close(self):
        self.conn.close()


# ──────────────────────────────────────────────────────────────
# AI RUNTIME — Pluggable, Mock for Phase 1
# ──────────────────────────────────────────────────────────────

@dataclass
class AiDecision:
    result: Any
    confidence: float
    model: str
    inputs: dict
    explanation: Optional[str] = None
    fell_back: bool = False


class MockAiRuntime:
    """
    Deterministic mock AI runtime for Phase 1 testing.
    Returns controlled responses based on input patterns.
    """

    def __init__(self):
        self._overrides: dict[str, AiDecision] = {}

    def set_response(self, decision_name: str, result: Any,
                     confidence: float = 0.95):
        """Pre-configure a response for a named ai.decide block."""
        self._overrides[decision_name] = AiDecision(
            result=result,
            confidence=confidence,
            model='mock-v1',
            inputs={},
        )

    def decide(self, name: str, inputs: dict,
               threshold: float = 0.85,
               return_type: str = 'boolean') -> AiDecision:
        """
        Execute an AI decision.
        Phase 1: returns override if set, otherwise deterministic heuristic.
        """
        if name in self._overrides:
            d = self._overrides[name]
            d.inputs = inputs
            return d

        # Default heuristic for fraud detection
        if 'amount' in inputs:
            amount = inputs.get('amount', 0)
            if isinstance(amount, MohioValue):
                amount = amount.to_python()
            amount = float(amount) if amount else 0
            is_fraud = amount > 50000
            confidence = 0.92 if is_fraud else 0.88
            return AiDecision(
                result=is_fraud,
                confidence=confidence,
                model='mock-v1',
                inputs=inputs,
            )

        # Generic fallback — never confident enough to avoid fallback
        return AiDecision(
            result=None,
            confidence=0.5,
            model='mock-v1',
            inputs=inputs,
            fell_back=True,
        )

    def explain(self, decision: AiDecision, audience: str = 'developer',
                fmt: str = 'paragraph') -> str:
        return (
            f"Decision: {decision.result} "
            f"(confidence {decision.confidence:.0%}, model {decision.model}). "
            f"Inputs considered: {list(decision.inputs.keys())}."
        )


# ──────────────────────────────────────────────────────────────
# AUDIT LOG
# ──────────────────────────────────────────────────────────────

class AuditLog:
    """
    Structured audit trail — JSON lines written to a named log.
    In Phase 1 this is an in-memory list + optional file output.
    """

    def __init__(self, name: str, output_path: Optional[str] = None):
        self.name = name
        self.entries: list[dict] = []
        self.output_path = output_path

    def record(self, entry: dict):
        entry['log'] = self.name
        entry['ts'] = datetime.datetime.utcnow().isoformat()
        self.entries.append(entry)
        if self.output_path:
            with open(self.output_path, 'a') as f:
                f.write(json.dumps(entry) + '\n')

    def __len__(self):
        return len(self.entries)


# ──────────────────────────────────────────────────────────────
# INTERPRETER
# ──────────────────────────────────────────────────────────────

class MohioInterpreter:
    """
    Tree-walking interpreter for the Mohio AST.

    One exec_* method per AST node type.
    Returns MohioValue or raises a runtime signal (_GiveBack etc.).

    Usage:
        interp = MohioInterpreter()
        interp.setup_test_db()          # Phase 1 — seed SQLite
        result = interp.run(program)    # execute Program node
    """

    def __init__(self,
                 ai: Optional[MockAiRuntime] = None,
                 db_path: str = ':memory:',
                 verbose: bool = False):
        self.ai = ai or MockAiRuntime()
        self.db_path = db_path
        self.verbose = verbose
        self._audit_logs: dict[str, AuditLog] = {}
        self._db: Optional[DbRuntime] = None

    # ── Public API ───────────────────────────────────────────

    def run(self, program: Program, request: Optional[dict] = None) -> Any:
        """
        Execute a Program node.
        request: simulated inbound request (shape data + roles).
        Returns the final give back value, or None.
        """
        ctx = Context()
        if request:
            ctx._current_request = request
            ctx.set_roles(request.get('_roles', []))

        try:
            return self._exec_program(program, ctx)
        except _Halt:
            return {'status': 200, 'body': 'halted'}
        except _GiveBack as gb:
            return self._format_response(gb)
        except _Raise as r:
            if r.error_name == 'authorization_error':
                return {'status': 403, 'body': str(r.message)}
            return {'status': 500, 'body': str(r)}
        except _Jump as j:
            return {'status': 302, 'body': str(j.destination)}

    def setup_test_db(self, seed_data: Optional[dict] = None):
        """
        Phase 1: initialise SQLite with test tables and optional seed data.
        seed_data = {'members': [...], 'transactions': [...], ...}
        """
        self._db = DbRuntime(self.db_path)

        # Core tables for fraud demo
        self._db.ensure_table('members', [
            'id', 'name', 'email', 'status', 'history', 'created_at'
        ])
        self._db.ensure_table('transactions', [
            'id', 'amount', 'currency', 'member_id', 'merchant',
            'device_id', 'timestamp', 'status'
        ])
        self._db.ensure_table('cleared_transactions', [
            'id', 'amount', 'member_id', 'cleared_at'
        ])
        self._db.ensure_table('fraud_audit_log', [
            'decision_name', 'inputs', 'result', 'confidence',
            'model', 'fell_back', 'ts'
        ])

        if seed_data:
            for table, rows in seed_data.items():
                for row in rows:
                    self._db.save(table, row)

        return self

    # ── Program ──────────────────────────────────────────────

    def _exec_program(self, node: Program, ctx: Context) -> Any:
        """Execute all top-level statements in order."""
        result = None
        for stmt in node.statements:
            r = self._exec(stmt, ctx)
            if r is not None:
                result = r
        return result

    # ── Dispatch ─────────────────────────────────────────────

    def _exec(self, node: Node, ctx: Context) -> Any:
        """Dispatch to the appropriate exec_* method."""
        if node is None:
            return None
        method_name = f'_exec_{type(node).__name__}'
        method = getattr(self, method_name, None)
        if method is None:
            if self.verbose:
                print(f"[interp] no handler for {type(node).__name__}")
            return None
        return method(node, ctx)

    def _exec_block(self, stmts: list, ctx: Context) -> Any:
        """Execute a list of statements in a child scope."""
        child = ctx.child()
        result = None
        for stmt in stmts:
            result = self._exec(stmt, child)
        return result

    # ── Declarations ─────────────────────────────────────────

    def _exec_SectorDecl(self, node: SectorDecl, ctx: Context):
        ctx._sector = node.sector
        if self.verbose:
            print(f"[sector] {node.sector}")

    def _exec_ConnectDecl(self, node: ConnectDecl, ctx: Context):
        # Phase 1: use the pre-initialised SQLite DbRuntime
        # In production this would open a real connection
        if self._db:
            ctx.set_connection(node.name, self._db)
        if self.verbose:
            source = self._eval(node.source, ctx)
            print(f"[connect] {node.name} as {node.driver} from {source}")

    def _exec_ShapeDecl(self, node: ShapeDecl, ctx: Context):
        ctx.set_shape(node.name, node)
        if self.verbose:
            print(f"[shape] {node.name} ({len(node.fields)} fields)")

    def _exec_TaskDecl(self, node: TaskDecl, ctx: Context):
        ctx.set_task(node.name, node)

    def _exec_HoldDecl(self, node: HoldDecl, ctx: Context):
        value = self._eval(node.value, ctx)
        ctx.set(node.name, value, immutable=True)
        if self.verbose:
            print(f"[hold] {node.name} = {value}")

    def _exec_LockDecl(self, node: LockDecl, ctx: Context):
        value = self._eval(node.value, ctx)
        ctx.set(node.name, value, immutable=True)
        if self.verbose:
            print(f"[lock] {node.name} = {value}")

    def _exec_ComplianceDecl(self, node: ComplianceDecl, ctx: Context):
        ctx._compliance.append(node.framework)
        if self.verbose:
            print(f"[compliance] {node.framework} activated")

    def _exec_RequireRoleDecl(self, node: RequireRoleDecl, ctx: Context):
        for role in node.roles:
            if ctx.has_role(role):
                return  # at least one role matches — pass
        raise _Raise(
            error_name='authorization_error',
            message=f"Role required: {' or '.join(node.roles)}"
        )

    def _exec_IncludeDecl(self, node: IncludeDecl, ctx: Context):
        pass  # Phase 1: includes deferred

    def _exec_TimespanDecl(self, node: TimespanDecl, ctx: Context):
        pass  # Phase 1: timespans stored, not yet evaluated

    def _exec_ReleaseStmt(self, node: ReleaseStmt, ctx: Context):
        pass  # Phase 1: release deferred

    # ── Listen / Routing ─────────────────────────────────────

    def _exec_ListenBlock(self, node: ListenBlock, ctx: Context):
        """
        Phase 1: if a current_request is present, find the matching
        new/request block by shape type and execute it.
        """
        req = ctx._current_request
        if not req:
            if self.verbose:
                print("[listen] no request — skipping")
            return None

        shape_name = req.get('_shape')
        method = req.get('_method', 'POST').upper()

        for listener in node.listeners:
            if isinstance(listener, NewBlock) and method in ('POST', 'NEW'):
                if not shape_name or listener.shape == shape_name:
                    return self._exec_new_block(listener, ctx, req)
            elif isinstance(listener, RequestBlock) and method in ('GET', 'REQUEST'):
                if not shape_name or listener.shape == shape_name:
                    return self._exec_request_block(listener, ctx, req)

        return None

    def _exec_new_block(self, node: NewBlock, ctx: Context,
                        req: dict) -> Any:
        """Execute a new sh.X block with request data."""
        child = ctx.child()
        # Expose the shape instance as a variable named after the shape (lowercase)
        shape_var = node.shape[0].lower() + node.shape[1:]  # Transaction → transaction
        child.set(shape_var, MohioValue(req, 'shape'))
        # Also expose individual fields
        for k, v in req.items():
            if not k.startswith('_'):
                child.set(k, MohioValue(v))

        try:
            return self._exec_block(node.body, child)
        except _GiveBack as gb:
            return self._format_response(gb)

    def _exec_request_block(self, node: RequestBlock, ctx: Context,
                            req: dict) -> Any:
        """Execute a request for sh.X block."""
        child = ctx.child()
        shape_var = node.shape[0].lower() + node.shape[1:]
        child.set(shape_var, MohioValue(req, 'shape'))
        for k, v in req.items():
            if not k.startswith('_'):
                child.set(k, MohioValue(v))
        try:
            return self._exec_block(node.body, child)
        except _GiveBack as gb:
            return self._format_response(gb)

    def _exec_NewBlock(self, node: NewBlock, ctx: Context):
        # Called when not inside listen — standalone new block
        return None

    def _exec_RequestBlock(self, node: RequestBlock, ctx: Context):
        return None

    def _exec_ConnectionBlock(self, node: ConnectionBlock, ctx: Context):
        pass  # Phase 1: WebSocket not implemented

    def _exec_WhileActiveBlock(self, node: WhileActiveBlock, ctx: Context):
        pass  # Phase 1: WebSocket not implemented

    # ── Flow Control ─────────────────────────────────────────

    def _exec_IfBlock(self, node: IfBlock, ctx: Context):
        if self._eval_condition(node.condition, ctx):
            return self._exec_block(node.body, ctx)

        for or_if in node.or_if_clauses:
            if self._eval_condition(or_if.condition, ctx):
                return self._exec_block(or_if.body, ctx)

        if node.otherwise:
            return self._exec_block(node.otherwise.body, ctx)

        return None

    def _exec_CheckBlock(self, node: CheckBlock, ctx: Context):
        value = self._eval(node.value, ctx)
        raw = value.to_python() if isinstance(value, MohioValue) else value

        for when in node.when_clauses:
            when_val = self._eval(when.value, ctx)
            when_raw = when_val.to_python() if isinstance(when_val, MohioValue) else when_val
            if raw == when_raw:
                return self._exec_block(when.body, ctx)

        if node.otherwise:
            return self._exec_block(node.otherwise.body, ctx)

        return None

    def _exec_EachBlock(self, node: EachBlock, ctx: Context):
        collection = self._eval(node.collection, ctx)
        items = collection.to_python() if isinstance(collection, MohioValue) else collection

        if not items:
            return None

        if isinstance(items, dict):
            items = list(items.values())

        result = None
        for item in items:
            child = ctx.child()
            child.set(node.item, MohioValue(item) if not isinstance(item, MohioValue) else item)
            try:
                result = self._exec_block(node.body, child)
            except _Stop:
                break
            except _Skip:
                continue
        return result

    def _exec_RepeatBlock(self, node: RepeatBlock, ctx: Context):
        count = self._eval(node.count, ctx)
        n = int(count.to_python() if isinstance(count, MohioValue) else count)
        result = None
        for _ in range(n):
            try:
                result = self._exec_block(node.body, ctx)
            except _Stop:
                break
            except _Skip:
                continue
        return result

    def _exec_WhileBlock(self, node: WhileBlock, ctx: Context):
        result = None
        max_iterations = 10000  # safety cap
        i = 0
        while self._eval_condition(node.condition, ctx) and i < max_iterations:
            try:
                result = self._exec_block(node.body, ctx)
            except _Stop:
                break
            except _Skip:
                pass
            i += 1
        return result

    def _exec_OrIfClause(self, node: OrIfClause, ctx: Context):
        pass  # handled in IfBlock

    def _exec_OtherwiseClause(self, node: OtherwiseClause, ctx: Context):
        pass  # handled in parent block

    # ── Data Operations ───────────────────────────────────────

    def _exec_RetrieveBlock(self, node: RetrieveBlock, ctx: Context):
        """retrieve name from db.table match field is value"""
        db = ctx.get_connection('db')
        table = self._resolve_source(node.source, ctx)

        match = next((b for b in node.body if isinstance(b, MatchClause)), None)
        if not match:
            self._handle_result_failure(node.handlers, ctx,
                                        "retrieve: no match clause")
            return None

        match_val = self._eval(match.value, ctx)
        raw_val = match_val.to_python() if isinstance(match_val, MohioValue) else match_val

        try:
            row = db.retrieve_one(table, match.field, raw_val)
        except Exception as e:
            return self._handle_result_failure(node.handlers, ctx, str(e))

        if row is None:
            return self._handle_result_failure(node.handlers, ctx,
                                               f"retrieve: no record in {table}")

        result = MohioValue(row, 'shape')
        ctx.set(node.name, result)

        for handler in node.handlers:
            if isinstance(handler, OnSuccess):
                self._exec_block(handler.body, ctx)
                break

        if self.verbose:
            print(f"[retrieve] {node.name} from {table} → {row}")
        return result

    def _exec_FindBlock(self, node: FindBlock, ctx: Context):
        """find name in db.table where ... and ..."""
        db = ctx.get_connection('db')
        table = self._resolve_source(node.source, ctx)

        where = {}
        for clause in node.body:
            if isinstance(clause, (WhereClause, AndClause)):
                val = self._eval_simple(clause.value, ctx)
                where[clause.field.split('.')[-1]] = val

        try:
            rows = db.find_many(table, where)
        except Exception as e:
            return self._handle_result_failure(node.handlers, ctx, str(e))

        result = MohioValue(rows, 'list')
        ctx.set(node.name, result)

        if self.verbose:
            print(f"[find] {node.name} in {table} → {len(rows)} rows")
        return result

    def _exec_SaveBlock(self, node: SaveBlock, ctx: Context):
        """save to db.table  field value ..."""
        db = ctx.get_connection('db')
        table = self._resolve_source(node.target, ctx)

        fields = {}
        for fv in node.fields:
            if isinstance(fv, FieldValue):
                val = self._eval(fv.value, ctx)
                raw = val.to_python() if isinstance(val, MohioValue) else val
                # Convert datetime values to string
                if isinstance(raw, datetime.datetime):
                    raw = raw.isoformat()
                fields[fv.name] = raw

        try:
            row_id = db.save(table, fields)
        except Exception as e:
            if self.verbose:
                print(f"[save] ERROR: {e}")
            raise _Raise(error_name='db_error', message=str(e))

        result = MohioValue({'id': row_id, **fields}, 'shape')
        if self.verbose:
            print(f"[save] to {table} → id={row_id}")
        return result

    def _exec_UpdateBlock(self, node: UpdateBlock, ctx: Context):
        db = ctx.get_connection('db')
        table = self._resolve_source(node.source, ctx)

        match = next((b for b in node.body if isinstance(b, MatchClause)), None)
        updates = {}
        for b in node.body:
            if isinstance(b, FieldValue):
                val = self._eval(b.value, ctx)
                updates[b.name] = val.to_python() if isinstance(val, MohioValue) else val

        if match and updates:
            match_val = self._eval(match.value, ctx)
            raw_val = match_val.to_python() if isinstance(match_val, MohioValue) else match_val
            count = db.update(table, updates, match.field, raw_val)
            if self.verbose:
                print(f"[update] {table} — {count} rows")
        return None

    def _exec_RemoveBlock(self, node: RemoveBlock, ctx: Context):
        db = ctx.get_connection('db')
        table = self._resolve_source(node.source, ctx)

        if node.match:
            match_val = self._eval(node.match.value, ctx)
            raw_val = match_val.to_python() if isinstance(match_val, MohioValue) else match_val
            count = db.remove(table, node.match.field, raw_val)
            if self.verbose:
                print(f"[remove] from {table} — {count} rows")
        return None

    def _exec_TransactionBlock(self, node: TransactionBlock, ctx: Context):
        db = ctx.get_connection('db')
        if db:
            db.begin_transaction()
        try:
            result = self._exec_block(node.body, ctx)
            if db:
                db.commit_transaction()
            return result
        except Exception as e:
            if db:
                db.rollback_transaction()
            raise

    def _resolve_source(self, source, ctx: Context) -> str:
        """Extract table name from source_ref node."""
        if isinstance(source, DbRef):
            return source.table
        if isinstance(source, DottedName):
            # db.members → members
            return source.parts[-1]
        if isinstance(source, str):
            return source
        return str(source) if source else 'unknown'

    def _handle_result_failure(self, handlers: list, ctx: Context,
                               reason: str) -> Any:
        """Execute on.failure handlers, or return None if none."""
        for h in handlers:
            if isinstance(h, OnFailure):
                if self.verbose:
                    print(f"[on.failure] {reason}")
                try:
                    return self._exec_block(h.body, ctx)
                except _GiveBack as gb:
                    raise
        return None

    # ── AI Primitives ─────────────────────────────────────────

    def _exec_AiDecideBlock(self, node: AiDecideBlock, ctx: Context):
        """
        ai.decide — the core AI reasoning primitive.

        1. Collect weigh inputs
        2. Call AI runtime
        3. Check confidence threshold
        4. If below threshold → execute not_confident block
        5. Write ai.audit entry
        6. Bind result to decision name in context
        """
        # 1. Collect inputs from weigh clause
        weigh = next((b for b in node.body if isinstance(b, WeighClause)), None)
        inputs = {}
        if weigh:
            for dotted in weigh.inputs:
                key = '.'.join(dotted.parts)
                val = ctx.get_dotted(dotted.parts)
                inputs[key] = val

        # Find confidence threshold
        conf_check = next(
            (b for b in node.body if isinstance(b, ConfidenceCheck)), None
        )
        threshold_node = conf_check.threshold if conf_check else None
        threshold = 0.85  # default
        if threshold_node:
            t = self._eval(threshold_node, ctx)
            threshold = float(t.to_python() if isinstance(t, MohioValue) else t)
        # Also check hold constants
        threshold_from_ctx = ctx.get('FRAUD_THRESHOLD')
        if threshold_from_ctx and threshold_from_ctx.to_python():
            threshold = float(threshold_from_ctx.to_python())

        # 2. Call AI
        decision = self.ai.decide(
            name=node.name,
            inputs=inputs,
            threshold=threshold,
            return_type=node.return_type,
        )

        if self.verbose:
            print(
                f"[ai.decide] {node.name}: result={decision.result} "
                f"confidence={decision.confidence:.2f} threshold={threshold}"
            )

        # 3. Write audit FIRST — before any control flow that might exit early
        # This ensures audit is always written regardless of confidence path
        audit_stmt = next(
            (b for b in node.body if isinstance(b, AiAuditStmt)), None
        )
        if audit_stmt:
            self._write_ai_audit(audit_stmt.log_name, node.name, decision, ctx)

        # 4. Check confidence — execute not_confident if below threshold
        if decision.confidence < threshold:
            decision.fell_back = True
            not_conf = next(
                (b for b in node.body if isinstance(b, NotConfidentBlock)), None
            )
            if not_conf:
                try:
                    self._exec_block(not_conf.body, ctx)
                except _GiveBack:
                    raise  # propagate give back from not_confident

        # Execute on.failure if decision fell back and there's a handler
        if decision.fell_back:
            for b in node.body:
                if isinstance(b, OnFailure):
                    try:
                        self._exec_block(b.body, ctx)
                    except _GiveBack:
                        raise

        # 6. Bind result
        result_val = MohioValue(decision.result, node.return_type)
        ctx.set(node.name, result_val)

        # Execute remaining body (on.success, ai.explain etc.)
        for b in node.body:
            if isinstance(b, (WeighClause, ConfidenceCheck, NotConfidentBlock,
                               AiAuditStmt, OnFailure, OnSuccess)):
                continue  # already handled
            if isinstance(b, AiExplainStmt):
                self._exec_AiExplainStmt(b, ctx, decision)
            elif isinstance(b, OnSuccess) and not decision.fell_back:
                self._exec_block(b.body, ctx)

        return result_val

    def _write_ai_audit(self, log_name: str, decision_name: str,
                        decision: AiDecision, ctx: Context):
        """Write immutable audit record for an AI decision."""
        log = self._get_or_create_audit_log(log_name)
        entry = {
            'event': 'ai.decide',
            'decision': decision_name,
            'result': str(decision.result),
            'confidence': decision.confidence,
            'model': decision.model,
            'fell_back': decision.fell_back,
            'inputs': {
                k: str(v.to_python() if isinstance(v, MohioValue) else v)
                for k, v in decision.inputs.items()
            },
        }
        log.record(entry)

        # Also write to DB table if it exists
        db = ctx.get_connection('db')
        if db:
            try:
                db.save(log_name, {
                    'decision_name': decision_name,
                    'inputs': json.dumps(entry['inputs']),
                    'result': entry['result'],
                    'confidence': str(decision.confidence),
                    'model': decision.model,
                    'fell_back': str(decision.fell_back),
                    'ts': entry['ts'],
                })
            except Exception:
                pass  # log table might not exist for all audit logs

        if self.verbose:
            print(f"[ai.audit] → {log_name}: {entry}")

    def _get_or_create_audit_log(self, name: str) -> AuditLog:
        if name not in self._audit_logs:
            self._audit_logs[name] = AuditLog(name)
        return self._audit_logs[name]

    def _exec_AiAuditStmt(self, node: AiAuditStmt, ctx: Context):
        # Standalone ai.audit — create the log entry manually
        log = self._get_or_create_audit_log(node.log_name)
        log.record({'event': 'manual_audit'})

    def _exec_AiExplainStmt(self, node: AiExplainStmt, ctx: Context,
                             decision: Optional[AiDecision] = None):
        if decision:
            explanation = self.ai.explain(
                decision,
                audience=node.audience or 'developer',
                fmt=node.format or 'paragraph',
            )
            ctx.set('_last_explanation', MohioValue(explanation))
            if self.verbose:
                print(f"[ai.explain] {explanation}")

    def _exec_AiChainBlock(self, node: AiChainBlock, ctx: Context):
        pass  # Phase 1: ai.chain deferred

    def _exec_AiCreateStmt(self, node: AiCreateStmt, ctx: Context):
        pass  # Phase 1: ai.create deferred

    def _exec_AiOverrideStmt(self, node: AiOverrideStmt, ctx: Context):
        pass  # Phase 1: ai.override deferred

    # ── Try / Catch / Always ──────────────────────────────────

    def _exec_TryBlock(self, node: TryBlock, ctx: Context):
        result = None
        error = None
        try:
            result = self._exec_block(node.body, ctx)
        except _GiveBack:
            raise
        except _Halt:
            raise
        except _Raise as r:
            error = r
        except Exception as e:
            error = _Raise(error_name=type(e).__name__, message=str(e))

        if error:
            handled = False
            for catch in node.catch_clauses:
                catch_type = catch.catch_type
                if catch_type is None or catch_type == 'any':
                    handled = True
                elif catch_type == error.error_name:
                    handled = True
                elif catch_type == 'timeout' and 'timeout' in str(error.message).lower():
                    handled = True

                if handled:
                    child = ctx.child()
                    if catch.alias:
                        child.set(catch.alias, MohioValue({'message': str(error.message),
                                                           'name': error.error_name}))
                    result = self._exec_block(catch.body, child)
                    break

            if not handled:
                if node.always:
                    self._exec_block(node.always.body, ctx)
                raise error

        if node.always:
            self._exec_block(node.always.body, ctx)

        return result

    # ── Transaction Block ─────────────────────────────────────

    # (already defined above)

    # ── Action Statements ────────────────────────────────────

    def _exec_GiveBackStmt(self, node: GiveBackStmt, ctx: Context):
        value = self._eval(node.value, ctx) if node.value else None
        status = None
        if node.status is not None:
            s = self._eval(node.status, ctx) if hasattr(node.status, '__class__') and not isinstance(node.status, (int, float)) else node.status
            status = int(s.to_python() if isinstance(s, MohioValue) else s)
        raise _GiveBack(status=status, value=value)

    def _exec_HaltStmt(self, node: HaltStmt, ctx: Context):
        raise _Halt()

    def _exec_StopStmt(self, node: StopStmt, ctx: Context):
        raise _Stop()

    def _exec_SkipStmt(self, node: SkipStmt, ctx: Context):
        raise _Skip()

    def _exec_JumpToStmt(self, node: JumpToStmt, ctx: Context):
        raise _Jump(destination=str(node.destination))

    def _exec_ShowStmt(self, node: ShowStmt, ctx: Context):
        value = self._eval(node.value, ctx)
        if self.verbose:
            print(f"[show] {value}")
        return value

    def _exec_RaiseStmt(self, node: RaiseStmt, ctx: Context):
        message = self._eval(node.message, ctx) if node.message else None
        msg_raw = message.to_python() if isinstance(message, MohioValue) else message
        raise _Raise(error_name=node.error_name, message=msg_raw)

    def _exec_SendStmt(self, node: SendStmt, ctx: Context):
        pass  # Phase 1: WebSocket send deferred

    def _exec_BroadcastStmt(self, node, ctx: Context):
        pass  # Phase 1: deferred

    def _exec_StreamStmt(self, node, ctx: Context):
        pass  # Phase 1: deferred

    def _exec_ServiceCallStmt(self, node: ServiceCallStmt, ctx: Context):
        """
        Handle mio* service calls — Phase 1 routes to built-in handlers.
        Unrecognised services: log and continue (forgiving runtime).
        """
        service = node.service
        method = node.method

        # miolog — structured logging
        if service == 'miolog':
            msg = self._eval(node.args, ctx) if node.args else MohioValue('')
            raw_msg = msg.to_python() if isinstance(msg, MohioValue) else msg
            level = method.split('.')[-1] if '.' in method else method
            log_entry = {'level': level, 'message': raw_msg}
            if self.verbose:
                print(f"[miolog.{level}] {raw_msg}")
            return MohioValue(log_entry)

        # miocache — Phase 1: in-memory dict
        if service == 'miocache':
            pass

        # miomail — Phase 1: log and no-op
        if service == 'miomail':
            if self.verbose:
                print(f"[miomail.{method}] (Phase 1 — no-op)")

        if self.verbose and service not in ('miolog', 'miocache', 'miomail'):
            print(f"[service] {service}.{method} (Phase 1 — no handler)")

        return None

    def _exec_NotifyStmt(self, node, ctx: Context):
        pass  # Phase 1: deferred

    def _exec_Closer(self, node, ctx: Context):
        pass  # Closers consumed by transformer — should not appear in AST body

    # ── Assignment ────────────────────────────────────────────

    def _exec_Assignment(self, node: Assignment, ctx: Context):
        value = self._eval(node.value, ctx)
        ctx.set(node.name, value)
        if self.verbose:
            print(f"[assign] {node.name} = {value}")
        return value

    # ── Value Evaluation ──────────────────────────────────────

    def _eval(self, node, ctx: Context) -> MohioValue:
        """Evaluate a value expression node to a MohioValue."""
        if node is None:
            return MohioValue(None)

        if isinstance(node, MohioValue):
            return node

        if isinstance(node, Literal):
            return MohioValue(node.value, node.literal_type)

        if isinstance(node, DottedName):
            return ctx.get_dotted(node.parts)

        if isinstance(node, EnvRef):
            return ctx.get_env(node.key)

        if isinstance(node, SecretRef):
            val = os.environ.get(node.key, '')
            return MohioValue(val, 'secret')

        if isinstance(node, DbRef):
            return MohioValue(node.table, 'table_ref')

        if isinstance(node, ShRef):
            shape = ctx.get_shape(node.shape_name)
            return MohioValue(shape, 'shape_def')

        if isinstance(node, FuncCall):
            return self._eval_func_call(node, ctx)

        if isinstance(node, MathExpr):
            return self._eval_math(node, ctx)

        if isinstance(node, TemplateString):
            return MohioValue(self._interpolate(node.template, ctx), 'text')

        if isinstance(node, ListLiteral):
            items = [self._eval(i, ctx).to_python() for i in node.items]
            return MohioValue(items, 'list')

        if isinstance(node, MapLiteral):
            d = {k: self._eval(v, ctx).to_python() for k, v in node.entries}
            return MohioValue(d, 'map')

        if isinstance(node, TimeExpr):
            return self._eval_time(node, ctx)

        if isinstance(node, DatetimeExpr):
            return MohioValue(node.date, 'datetime')

        if isinstance(node, DurationExpr):
            return MohioValue({'count': node.count, 'unit': node.unit}, 'duration')

        # Fallback — return as-is wrapped
        return MohioValue(node)

    def _eval_simple(self, node, ctx: Context) -> Any:
        """Eval and unwrap to Python value."""
        v = self._eval(node, ctx)
        return v.to_python() if isinstance(v, MohioValue) else v

    def _eval_func_call(self, node: FuncCall, ctx: Context) -> MohioValue:
        """Evaluate built-in function calls."""
        if not node.name:
            return MohioValue(None)

        name = '.'.join(node.name.parts) if isinstance(node.name, DottedName) else str(node.name)

        # now() — current UTC timestamp
        if name == 'now':
            return MohioValue(datetime.datetime.utcnow().isoformat(), 'datetime')

        # today, yesterday
        if name == 'today':
            return MohioValue(datetime.date.today().isoformat(), 'date')

        # User-defined tasks
        task = ctx.get_task(name)
        if task:
            child = ctx.child()
            args = [self._eval(a, ctx) for a in node.args]
            for i, param in enumerate(task.params):
                if i < len(args):
                    child.set(param.name, args[i])
                elif param.default:
                    child.set(param.name, self._eval(param.default, ctx))
            try:
                self._exec_block(task.body, child)
            except _GiveBack as gb:
                return gb.value if isinstance(gb.value, MohioValue) else MohioValue(gb.value)
            return MohioValue(None)

        return MohioValue(None)

    def _eval_math(self, node: MathExpr, ctx: Context) -> MohioValue:
        """Evaluate a math expression."""
        left = self._eval(node.left, ctx)
        right = self._eval(node.right, ctx)
        op = node.op
        try:
            if op == '+': return left + right
            if op == '-': return left - right
            if op == '*': return left * right
            if op == '/': return left / right
            if op == '%': return left % right
        except Exception as e:
            raise _Raise(error_name='math_error', message=str(e))
        return MohioValue(None)

    def _eval_time(self, node: TimeExpr, ctx: Context) -> MohioValue:
        """Evaluate time expressions."""
        now = datetime.datetime.utcnow()
        if node.base == 'now()':
            if node.offset and node.offset_op:
                dur = node.offset
                count = dur.count or 0
                unit = dur.unit
                delta = self._duration_to_timedelta(count, unit)
                if node.offset_op == '-':
                    return MohioValue((now - delta).isoformat(), 'datetime')
                return MohioValue((now + delta).isoformat(), 'datetime')
            return MohioValue(now.isoformat(), 'datetime')
        if node.base == 'today':
            return MohioValue(datetime.date.today().isoformat(), 'date')
        if node.base == 'yesterday':
            return MohioValue((datetime.date.today() - datetime.timedelta(days=1)).isoformat(), 'date')
        return MohioValue(now.isoformat(), 'datetime')

    def _duration_to_timedelta(self, count: Any, unit: str) -> datetime.timedelta:
        count = int(count) if count else 0
        unit = str(unit).lower().rstrip('s')  # normalise: "hours" → "hour"
        if unit in ('second', 'sec'): return datetime.timedelta(seconds=count)
        if unit in ('minute', 'min'): return datetime.timedelta(minutes=count)
        if unit in ('hour',):         return datetime.timedelta(hours=count)
        if unit in ('day',):          return datetime.timedelta(days=count)
        if unit in ('week',):         return datetime.timedelta(weeks=count)
        if unit in ('month',):        return datetime.timedelta(days=count * 30)
        if unit in ('year',):         return datetime.timedelta(days=count * 365)
        return datetime.timedelta()

    def _interpolate(self, template: str, ctx: Context) -> str:
        """Replace {{ name }} in a template string."""
        import re
        def replace(m):
            expr = m.group(1).strip()
            parts = expr.split('.')
            val = ctx.get_dotted(parts)
            return str(val.to_python() if isinstance(val, MohioValue) else val)
        return re.sub(r'\{\{([^}]+)\}\}', replace, template)

    # ── Condition Evaluation ──────────────────────────────────

    def _eval_condition(self, node, ctx: Context) -> bool:
        """Evaluate a condition node to bool."""
        if node is None:
            return False

        if isinstance(node, Condition):
            left = self._eval(node.left, ctx)
            right = self._eval(node.right, ctx)
            op = node.op

            # Unwrap for comparison
            lv = left.to_python() if isinstance(left, MohioValue) else left
            rv = right.to_python() if isinstance(right, MohioValue) else right

            if op in ('is', '=='): return lv == rv
            if op == 'is not':    return lv != rv
            if op == '!=':        return lv != rv
            if op == '>':         return lv > rv
            if op == '<':         return lv < rv
            if op == '>=':        return lv >= rv
            if op == '<=':        return lv <= rv
            if op == 'above':     return lv > rv
            if op == 'below':     return lv < rv
            return False

        if isinstance(node, NotCondition):
            return not self._eval_condition(node.condition, ctx)

        if isinstance(node, AndCondition):
            return (self._eval_condition(node.left, ctx) and
                    self._eval_condition(node.right, ctx))

        if isinstance(node, OrCondition):
            return (self._eval_condition(node.left, ctx) or
                    self._eval_condition(node.right, ctx))

        if isinstance(node, DotStateCheck):
            val = self._eval(node.value, ctx)
            raw = val.to_python() if isinstance(val, MohioValue) else val
            state = node.state

            if node.prefix == 'is':
                if state == 'empty':  return not bool(raw)
                if state == 'valid':  return raw is not None
                if state == 'active': return raw == 'active'
                return bool(raw)
            if node.prefix == 'not':
                if state == 'empty':  return bool(raw)
                if state == 'found':  return raw is None
                return not bool(raw)
            return False

        # Fallback — truthy eval
        val = self._eval(node, ctx)
        return bool(val)

    # ── Response Formatting ───────────────────────────────────

    def _format_response(self, gb: _GiveBack) -> dict:
        """Format a _GiveBack signal as an HTTP-style response."""
        value = gb.value
        raw = value.to_python() if isinstance(value, MohioValue) else value
        return {
            'status': gb.status or 200,
            'body': raw,
        }


# ──────────────────────────────────────────────────────────────
# PUBLIC RUN FUNCTION
# ──────────────────────────────────────────────────────────────

def run_program(source: str,
                request: Optional[dict] = None,
                ai: Optional[MockAiRuntime] = None,
                seed_data: Optional[dict] = None,
                verbose: bool = False) -> dict:
    """
    Full pipeline: source → parse → transform → execute.

    Args:
        source:    .mho source text
        request:   simulated inbound request dict
                   Keys: _shape (shape name), _method (POST/GET),
                         _roles (list of roles), + shape field values
        ai:        AI runtime (mock by default)
        seed_data: initial DB data {'table': [row, ...]}
        verbose:   print execution trace

    Returns:
        dict with 'status' and 'body' keys
    """
    from lark import Lark
    from mohio_transformer import transform

    with open('/home/claude/mohio.lark') as f:
        grammar = f.read()

    parser = Lark(grammar, parser='earley', ambiguity='resolve',
                  propagate_positions=True)
    tree = parser.parse(source)
    program = transform(tree, source)

    interp = MohioInterpreter(ai=ai, verbose=verbose)
    interp.setup_test_db(seed_data=seed_data)

    try:
        result = interp.run(program, request=request)
        if isinstance(result, dict) and 'status' in result:
            return result
        return {'status': 200, 'body': result}
    except _GiveBack as gb:
        return interp._format_response(gb)
    except _Halt:
        return {'status': 200, 'body': 'halted'}
    except Exception as e:
        return {'status': 500, 'body': str(e)}
