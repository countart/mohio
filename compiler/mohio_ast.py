# Mohio AST Node Types — Phase 1
# Version: 0.1.0 | April 2026 | Particular LLC
#
# Every grammar rule that produces a meaningful semantic unit
# maps to exactly one AST node class. The transformer emits
# these nodes. The interpreter walks them.
#
# Convention:
#   - Node names are PascalCase
#   - Field names are snake_case
#   - Optional fields default to None
#   - List fields default to []
#
# This file is the source of truth for node types.
# The lark transformer and Python interpreter import from here.

from dataclasses import dataclass, field
from typing import Any, Optional


# ============================================================
# BASE
# ============================================================

@dataclass
class Node:
    """Base class for all AST nodes."""
    line: int = 0
    col: int = 0


# ============================================================
# PROGRAM ROOT
# ============================================================

@dataclass
class Program(Node):
    """Root node. Contains all top-level statements."""
    statements: list = field(default_factory=list)


# ============================================================
# DECLARATIONS
# ============================================================

@dataclass
class SectorDecl(Node):
    """sector: financial"""
    sector: str = ""

@dataclass
class ConnectDecl(Node):
    """connect db as postgres from env.DATABASE_URL"""
    name: str = ""           # alias (db)
    driver: str = ""         # postgres, redis, etc.
    source: Any = None       # value_expr — usually an env ref

@dataclass
class ShapeDecl(Node):
    """shape Transaction ... shape: done"""
    name: str = ""
    fields: list = field(default_factory=list)   # list[ShapeField]

@dataclass
class ShapeField(Node):
    """id  as  text"""
    name: str = ""
    type_name: Optional[str] = None
    modifiers: list = field(default_factory=list)  # list[ShapeFieldModifier]

@dataclass
class ShapeFieldModifier(Node):
    """never store | format "###" | label "..." | range N N | threshold N"""
    modifier_type: str = ""   # "never_store", "format", "label", "range", "threshold"
    value: Any = None

@dataclass
class TaskDecl(Node):
    """task greet(name text) returns text ... task: done"""
    name: str = ""
    params: list = field(default_factory=list)     # list[TaskParam]
    return_type: Optional[str] = None
    body: list = field(default_factory=list)        # list[Node]

@dataclass
class TaskParam(Node):
    """name as type [default value]"""
    name: str = ""
    type_name: str = ""
    default: Any = None

@dataclass
class HoldDecl(Node):
    """hold FRAUD_THRESHOLD 0.85"""
    name: str = ""
    value: Any = None

@dataclass
class LockDecl(Node):
    """lock MAX_RETRIES 3"""
    name: str = ""
    value: Any = None

@dataclass
class ReleaseStmt(Node):
    """release var | release.now var value | release.lock var value"""
    variant: str = ""          # "release" | "release.now" | "release.lock"
    name: str = ""
    value: Any = None

@dataclass
class ComplianceDecl(Node):
    """compliance: HIPAA"""
    framework: str = ""

@dataclass
class JourneyDecl(Node):
    """journey AppName"""
    name: str = ""

@dataclass
class MioconnectDecl(Node):
    """mioconnect Stripe from env.STRIPE_KEY"""
    name: str = ""
    source: Any = None

@dataclass
class IncludeDecl(Node):
    """include "config/app.mho" """
    path: str = ""

@dataclass
class RequireRoleDecl(Node):
    """require role "admin" or "screener" """
    roles: list = field(default_factory=list)    # list[str]

@dataclass
class RateLimitDecl(Node):
    """rate limit 3 per minute per ip"""
    count: Any = None
    unit: str = ""
    per: Optional[str] = None

@dataclass
class TimespanDecl(Node):
    """timespan last_quarter ... timespan: done"""
    name: str = ""
    body: list = field(default_factory=list)

@dataclass
class TimespanAnchor(Node):
    """start / end / until"""
    anchor_type: str = ""
    datetime_expr: Any = None

@dataclass
class TimespanPrecision(Node):
    precision: str = ""

@dataclass
class TimespanTimezone(Node):
    timezone: str = ""

@dataclass
class TimespanRecurring(Node):
    pattern: str = ""

@dataclass
class TimespanExclude(Node):
    exclude_type: str = ""   # "weekends", "holidays", "date"
    value: Any = None


# ============================================================
# BLOCK CLOSERS
# ============================================================

@dataclass
class Closer(Node):
    """blockname: done | done"""
    block_name: Optional[str] = None    # None for bare done


# ============================================================
# LISTEN / ROUTING BLOCKS
# ============================================================

@dataclass
class ListenBlock(Node):
    """listen for ... listen: done"""
    listeners: list = field(default_factory=list)   # list[Node]

@dataclass
class NewBlock(Node):
    """new sh.Transaction [at /path] ... new: done"""
    shape: str = ""                     # shape name (Transaction)
    path: Optional[str] = None
    body: list = field(default_factory=list)

@dataclass
class RequestBlock(Node):
    """request for sh.Inventory [at /path] ... request: done"""
    shape: str = ""
    path: Optional[str] = None
    body: list = field(default_factory=list)

@dataclass
class ChangeBlock(Node):
    """change to sh.Task ... change: done"""
    shape: str = ""
    body: list = field(default_factory=list)

@dataclass
class ConnectionBlock(Node):
    """connection at /chat/stream ... connection: done"""
    path: str = ""
    body: list = field(default_factory=list)

@dataclass
class WhileActiveBlock(Node):
    """while.active ... while: done"""
    body: list = field(default_factory=list)

@dataclass
class OnOpen(Node):
    """on.open show "Connected" """
    action: Any = None

@dataclass
class OnClose(Node):
    """on.close show "Connection Lost" """
    action: Any = None


# ============================================================
# FLOW CONTROL
# ============================================================

@dataclass
class IfBlock(Node):
    """if condition ... or if ... otherwise"""
    condition: Any = None
    body: list = field(default_factory=list)
    or_if_clauses: list = field(default_factory=list)   # list[OrIfClause]
    otherwise: Optional["OtherwiseClause"] = None

@dataclass
class OrIfClause(Node):
    condition: Any = None
    body: list = field(default_factory=list)

@dataclass
class OtherwiseClause(Node):
    body: list = field(default_factory=list)

@dataclass
class CheckBlock(Node):
    """check status ... when "active" → ..."""
    value: Any = None
    when_clauses: list = field(default_factory=list)   # list[CheckWhen]
    otherwise: Optional[OtherwiseClause] = None

@dataclass
class CheckWhen(Node):
    value: Any = None
    body: list = field(default_factory=list)

@dataclass
class EachBlock(Node):
    """each user in users"""
    item: str = ""
    collection: Any = None
    body: list = field(default_factory=list)

@dataclass
class RepeatBlock(Node):
    """repeat 3 times"""
    count: Any = None
    body: list = field(default_factory=list)

@dataclass
class WhileBlock(Node):
    """while queue.size > 0"""
    condition: Any = None
    body: list = field(default_factory=list)

@dataclass
class SectionBlock(Node):
    """section /admin"""
    path: str = ""
    body: list = field(default_factory=list)


# ============================================================
# DATA OPERATIONS
# ============================================================

@dataclass
class RetrieveBlock(Node):
    """retrieve member from db.members match id is ..."""
    name: str = ""
    source: Any = None
    body: list = field(default_factory=list)
    handlers: list = field(default_factory=list)   # list[ResultHandler]

@dataclass
class FindBlock(Node):
    """find recent in db.transactions where ..."""
    name: str = ""
    source: Any = None
    body: list = field(default_factory=list)
    handlers: list = field(default_factory=list)

@dataclass
class SaveBlock(Node):
    """save to db.cleared_transactions  id value ..."""
    target: Any = None
    fields: list = field(default_factory=list)   # list[FieldValue]

@dataclass
class UpdateBlock(Node):
    """update source  match ... field value ..."""
    source: Any = None
    body: list = field(default_factory=list)

@dataclass
class RemoveBlock(Node):
    """remove from source  match ..."""
    source: Any = None
    match: Any = None
    handlers: list = field(default_factory=list)

@dataclass
class FieldValue(Node):
    """field_name  value"""
    name: str = ""
    value: Any = None

@dataclass
class MatchClause(Node):
    """match id is value"""
    field: str = ""
    value: Any = None

@dataclass
class WhereClause(Node):
    """where status is "active" """
    field: str = ""
    value: Any = None

@dataclass
class AndClause(Node):
    """and timestamp since now() - 24 hours"""
    field: str = ""
    value: Any = None

@dataclass
class OrderClause(Node):
    field: str = ""
    direction: str = "asc"

@dataclass
class LimitClause(Node):
    count: Any = None
    source: Any = None

@dataclass
class CacheClause(Node):
    duration: Any = None   # DurationExpr


# ============================================================
# RESULT HANDLERS
# ============================================================

@dataclass
class OnFailure(Node):
    inline: Any = None
    body: list = field(default_factory=list)

@dataclass
class OnSuccess(Node):
    inline: Any = None
    body: list = field(default_factory=list)

@dataclass
class OnError(Node):
    body: list = field(default_factory=list)


# ============================================================
# AI PRIMITIVES
# ============================================================

@dataclass
class AiDecideBlock(Node):
    """ai.decide isFraudulent(tx) returns boolean ... ai.decide: done"""
    name: str = ""
    args: list = field(default_factory=list)
    return_type: str = ""
    body: list = field(default_factory=list)

@dataclass
class ConfidenceCheck(Node):
    """check confidence above 0.85"""
    operator: str = "above"
    threshold: Any = None

@dataclass
class UsingChain(Node):
    """using fraud_chain"""
    chain_name: str = ""

@dataclass
class WeighClause(Node):
    """weigh transaction.amount, member.history, ..."""
    inputs: list = field(default_factory=list)   # list[dotted names]

@dataclass
class NotConfidentBlock(Node):
    """not confident ... (fallback pattern)"""
    body: list = field(default_factory=list)

@dataclass
class AiAuditStmt(Node):
    """ai.audit to fraud_audit_log"""
    log_name: str = ""

@dataclass
class AiExplainStmt(Node):
    """ai.explain decision name audience "..." format "..." """
    decision_name: Optional[str] = None
    audience: Optional[str] = None
    format: Optional[str] = None

@dataclass
class AiChainBlock(Node):
    """ai.chain fraud_chain ... ai.chain: done"""
    name: str = ""
    steps: list = field(default_factory=list)   # list[AiChainStep]

@dataclass
class AiChainStep(Node):
    provider: str = ""
    quality_threshold: Any = None
    body: list = field(default_factory=list)

@dataclass
class AiCreateStmt(Node):
    """ai.create image "..." """
    create_type: str = ""   # image | audio | logic | text
    prompt: Any = None

@dataclass
class AiOverrideStmt(Node):
    """ai.override decision_name with value"""
    name: str = ""
    value: Any = None


# ============================================================
# TRY / CATCH / ALWAYS
# ============================================================

@dataclass
class TryBlock(Node):
    body: list = field(default_factory=list)
    catch_clauses: list = field(default_factory=list)   # list[CatchClause]
    always: Optional["AlwaysClause"] = None

@dataclass
class CatchClause(Node):
    catch_type: Optional[str] = None   # "timeout", "any", or named error
    alias: Optional[str] = None
    body: list = field(default_factory=list)

@dataclass
class AlwaysClause(Node):
    body: list = field(default_factory=list)


# ============================================================
# TRANSACTION
# ============================================================

@dataclass
class TransactionBlock(Node):
    body: list = field(default_factory=list)


# ============================================================
# ACTION STATEMENTS
# ============================================================

@dataclass
class GiveBackStmt(Node):
    """give back 200 "OK"  |  give back value  |  give back pending "..." """
    status: Optional[Any] = None      # http status number or named status
    value: Any = None
    modifier: Any = None              # "as json" or "with {...}"

@dataclass
class JumpToStmt(Node):
    destination: Any = None

@dataclass
class HaltStmt(Node):
    pass

@dataclass
class StopStmt(Node):
    pass

@dataclass
class SkipStmt(Node):
    pass

@dataclass
class ShowStmt(Node):
    value: Any = None
    modifier: Any = None

@dataclass
class RaiseStmt(Node):
    error_name: Optional[str] = None  # named error type or "again"
    message: Any = None

@dataclass
class SendStmt(Node):
    value: Any = None
    target: Any = None

@dataclass
class BroadcastStmt(Node):
    room: Any = None
    value: Any = None
    except_session: Optional[Any] = None

@dataclass
class StreamStmt(Node):
    value: Any = None
    target: Any = None

@dataclass
class NotifyStmt(Node):
    target: Any = None
    channel: Optional[str] = None
    body: list = field(default_factory=list)

@dataclass
class ServiceCallStmt(Node):
    """miolog.alert "msg" / miomail.send ..."""
    service: str = ""     # miolog, miomail, etc.
    method: str = ""      # alert, send, etc.
    args: Any = None
    params: list = field(default_factory=list)


# ============================================================
# ASSIGNMENT
# ============================================================

@dataclass
class Assignment(Node):
    """name [as type] value"""
    name: str = ""
    type_name: Optional[str] = None
    value: Any = None


# ============================================================
# CONDITIONS
# ============================================================

@dataclass
class Condition(Node):
    left: Any = None
    op: str = ""
    right: Any = None

@dataclass
class NotCondition(Node):
    condition: Any = None

@dataclass
class AndCondition(Node):
    left: Any = None
    right: Any = None

@dataclass
class OrCondition(Node):
    left: Any = None
    right: Any = None

@dataclass
class DotStateCheck(Node):
    """is.empty | not.valid etc."""
    value: Any = None
    prefix: str = ""    # "is" or "not"
    state: str = ""


# ============================================================
# VALUE EXPRESSIONS
# ============================================================

@dataclass
class Literal(Node):
    value: Any = None
    literal_type: str = ""   # string | number | bool | null

@dataclass
class DottedName(Node):
    """user.email | transaction.amount"""
    parts: list = field(default_factory=list)   # list[str]

@dataclass
class EnvRef(Node):
    """env.DATABASE_URL"""
    key: str = ""

@dataclass
class SecretRef(Node):
    """secret.STRIPE_KEY"""
    key: str = ""

@dataclass
class DbRef(Node):
    """db.members"""
    table: str = ""

@dataclass
class ShRef(Node):
    """sh.Transaction"""
    shape_name: str = ""

@dataclass
class FuncCall(Node):
    """now() | request.city"""
    name: Any = None    # DottedName
    args: list = field(default_factory=list)

@dataclass
class MathExpr(Node):
    """((subtotal / 100) + tax_rate)"""
    left: Any = None
    op: str = ""
    right: Any = None

@dataclass
class TemplateString(Node):
    """{{ user.name }} or "Hello {{ name }}" """
    template: str = ""

@dataclass
class ListLiteral(Node):
    items: list = field(default_factory=list)

@dataclass
class MapLiteral(Node):
    entries: list = field(default_factory=list)   # list[(key, value)]


# ============================================================
# TIME / DATE EXPRESSIONS
# ============================================================

@dataclass
class TimeExpr(Node):
    base: str = ""          # "now()", "today", "last_month", etc.
    offset_op: Optional[str] = None
    offset: Optional["DurationExpr"] = None

@dataclass
class DatetimeExpr(Node):
    date: str = ""
    time: Optional[str] = None
    timezone: Optional[str] = None

@dataclass
class DurationExpr(Node):
    count: Any = None
    unit: str = ""

@dataclass
class SinceExpr(Node):
    anchor: Any = None
