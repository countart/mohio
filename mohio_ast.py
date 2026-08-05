# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
# mohio_ast.py
# Mohio AST Node Types -- v3.8
# Version: 0.3.8 | May 2026 | Particular LLC
#
# Every grammar rule that produces a meaningful semantic unit
# maps to exactly one AST node class. The transformer emits
# these; the interpreter walks them.
#
# Conventions:
#   - Node names: PascalCase
#   - Field names: snake_case
#   - Optional fields: default None
#   - List fields: default []
#   - Marked with NEW where added in v3.8
#   - Marked with CHANGED where updated in v3.8

from dataclasses import dataclass, field
from typing import Any, Optional


# ==============================================================
# BASE
# ==============================================================

@dataclass
class Node:
    """Base class. All nodes carry line/col for error reporting."""
    line: int = 0
    col:  int = 0


# ==============================================================
# PROGRAM ROOT
# ==============================================================

@dataclass
class Program(Node):
    """Root node. Contains all top-level statements."""
    statements: list = field(default_factory=list)


# ==============================================================
# DECLARATIONS
# ==============================================================

@dataclass
class SectorDecl(Node):
    """sector: financial"""
    sector: str = ""

@dataclass
class ConnectDecl(Node):
    """connect db as postgres from env.DATABASE_URL"""
    name:   str = ""      # alias (db)
    driver: str = ""      # postgres, redis, etc.
    source: Any = None    # value_expr -- usually env ref

@dataclass
class ComplianceDecl(Node):
    """compliance: HIPAA"""
    framework: str = ""

@dataclass
class SecurityDecl(Node):
    """
    security: off / reason "..." / expires "YYYY-MM-DD"

    Locked June 8 (levels ladder). Built rung: `off` (+ standard no-op).
    `off` is the honest security-debt escape hatch: mio check --security
    requires reason + expires (SECURITY_DEBT_UNDOCUMENTED otherwise) and
    suppresses the EXISTING checks (agent limits, sector floor) in scope.
    Hardcoded secrets / eval are NEVER suppressible. strict/relaxed deferred
    to the sec.* runtime phase. Placement: journey + section (+ top-level).
    """
    level:   str = "standard"   # "off" | "standard"
    reason:  str = ""           # justification string (required for off)
    expires: str = ""           # YYYY-MM-DD; required for long-term off

@dataclass
class IncludeDecl(Node):
    """include "config/app.mho" """
    path: str = ""

@dataclass
class RequireRoleDecl(Node):
    """require role "admin" or "screener" """
    roles: list = field(default_factory=list)   # list[str]

@dataclass
class GrantRoleDecl(Node):
    """grant role "admin"  /  grant role user.role -- establish a SERVER-verified
    role in the current session. The value_expr resolves at runtime to the role
    name(s); the grant is stored on the session root and marked verified, and is
    the ONLY source `require role` trusts (client `_roles` is never consulted)."""
    value: object = None   # value_expr node, resolved at runtime

@dataclass
class RateLimitDecl(Node):
    """rate limit 100 per hour"""
    count: Any  = None
    unit:  str  = ""
    per:   Optional[str] = None

@dataclass
class LoadPackDecl(Node):           # NEW v3.8
    """load pack miogscreen  |  load pack miogscreen version 1.2"""
    pack_name: str  = ""
    version:   Any  = None          # number or string, optional

@dataclass
class HoldDecl(Node):
    """hold THRESHOLD 0.85  |  hold list / items / hold: done  |  hold block / params"""
    name:      str  = ""
    type_name: str  = ""            # `hold x as int 5` -- was silently dropped
    value:    Any  = None           # simple form
    body:     list = field(default_factory=list)  # block form
    is_block: bool = False          # True = profile block form
    is_list:  bool = False          # True = list form
    items:    list = field(default_factory=list)  # list items
    default:  Any  = None           # value-level fallback: hold x source default Y

@dataclass
class LockDecl(Node):
    """lock MAX_RETRIES 3"""
    name:  str = ""
    value: Any = None

@dataclass
class ReleaseStmt(Node):
    """release var | release.now var value | release.lock var value"""
    variant: str = ""     # "release" | "release.now" | "release.lock"
    name:    str = ""
    value:   Any = None


@dataclass
class VarStateStmt(Node):
    """A variable state-change operator: clear / forget / rename / replace.
    op       -- 'clear' | 'forget' | 'rename' | 'replace'
    name     -- the target variable
    target   -- new name (rename) ; unused for clear/forget
    value    -- replacement value (replace) ; unused for clear/forget/rename
    """
    op:      str = ""
    name:    str = ""
    target:  str = ""
    value:   Any = None


# -- Shape ------------------------------------------------------

@dataclass
class ShapeDecl(Node):
    """shape Transaction ... shape: done"""
    name:          str  = ""
    fields:        list = field(default_factory=list)   # list[ShapeField]
    retain_years:  Any  = None     # NEW v3.8 -- shape-level retain for N years
    zone_tag:      Any  = None     # shape Intake [phi] -- seals every field in the zone

@dataclass
class ClientListener(Node):
    """MioScript: listen for <event> on "<selector>" ... listen: done.
    Mohio words that compile to JS and run in the browser."""
    event:       str  = ""         # input / click / blur / change / ...
    selector:    str  = ""         # CSS selector the listener binds to
    body:        list = field(default_factory=list)   # list of client statements
    debounce_ms: int  = 0          # on.pause <duration> — 0 means fire immediately

@dataclass
class ClientPut(Node):
    """put <the value | "literal"> into "<selector>" """
    source_kind: str = "literal"   # "literal" or "the"
    source:      str = ""          # the literal text, or the datum name (value/key)
    target:      str = ""          # CSS selector to write into

@dataclass
class ClientToggle(Node):
    """toggle the <attr> of "<selector>" between "<a>" and "<b>" """
    attr:     str = "type"
    selector: str = ""
    state_a:  str = ""
    state_b:  str = ""

@dataclass
class ClientCheck(Node):
    """check the value / <condition> ... / otherwise ... / check: done.
    Branches are (condition, [statements]); condition is a tuple like
    ('valid','email'), ('matches','#password'), ('empty',), ('notempty',)."""
    subject:   str  = "value"      # which datum is being checked (the value, the key)
    branches:  list = field(default_factory=list)   # list[(cond_tuple, [stmts])]
    otherwise: list = field(default_factory=list)    # [stmts] for the final fallback

@dataclass
class ClientDomOp(Node):
    """An everyday DOM verb: show/hide/enable/disable/add class/remove class/
    toggle class/set attribute."""
    op:       str = ""             # show|hide|enable|disable|addclass|removeclass|toggleclass|setattr
    selector: str = ""
    arg:      str = ""             # class name, or attribute name for setattr
    arg2:     str = ""             # attribute value for setattr

@dataclass
class ClientRequest(Node):
    """request "/path" into "#target" — browser GET, drop response into target."""
    url:    str = ""
    target: str = ""
    method: str = "GET"

@dataclass
class ClientSend(Node):
    """send <form selector> to "<url>" / on.success ... / on.failure ...
    Serializes the form, POSTs it, then runs the success or failure branch.
    `result` (and result.field) is the parsed response inside the branches."""
    form_selector: str  = ""
    url:           str  = ""
    success:       list = field(default_factory=list)
    failure:       list = field(default_factory=list)

@dataclass
class ClientNotify(Node):
    """notify <"message" | result.field | datum> — transient toast.
    Creates an element with class mio-notify; CSS owns the look. Auto-dismisses."""
    source_kind: str = "literal"
    source:      str = ""

@dataclass
class ClientHold(Node):
    """hold <name> = <"literal" | datum | result.field> — store a page-level value.
    Reading the name anywhere recalls it. Capture/recall only; computation lives
    in Mohio (server), not in MioScript."""
    name:        str = ""
    source_kind: str = "literal"
    source:      str = ""

@dataclass
class ClientPrevent(Node):
    """prevent default — stop the browser's default action (e.g. form submit)."""
    pass

@dataclass
class ClientAfter(Node):
    """after <duration> ... after: done — run the body after a delay (setTimeout)."""
    ms:   int  = 0
    body: list = field(default_factory=list)

@dataclass
class ClientNav(Node):
    """go to "/url" | go back | reload — browser navigation."""
    op:  str = ""              # goto | back | reload
    url: str = ""

@dataclass
class ClientState(Node):
    """mark / unmark / toggle <selector> as <state> — apply a symbolic UI state
    (a class CSS reads). op is add | remove | toggle."""
    selector: str = ""
    state:    str = ""
    op:       str = "add"

@dataclass
class ClientValidate(Node):
    """validate as <type> — apply a built-in type's rule to the listened element
    and mark it valid/invalid."""
    vtype: str = ""

@dataclass
class ClientAppend(Node):
    """append <the value | "literal"> to "<selector>" — append text to an element."""
    source_kind: str = "literal"
    source:      str = ""
    target:      str = ""

@dataclass
class ClientSetHtml(Node):
    """set the html of "<selector>" to <value>. Literal markup is allowed as HTML;
    runtime/event data is forced to text so it can never become markup."""
    source_kind: str = "literal"
    source:      str = ""
    selector:    str = ""


@dataclass
class ShapeField(Node):
    """id  as  text  required  default uuid()"""
    name:       str  = ""
    type_name:  Optional[str] = None
    is_list:    bool = False       # NEW v3.8 -- "as list text"
    dotted:     Optional[str] = None  # NEW v3.8 -- "status.allowed"
    modifiers:  list = field(default_factory=list)   # list[ShapeFieldModifier]

@dataclass
class ShapeFieldModifier(Node):
    """never store | format "###" | default value | required | optional | unique | ..."""
    modifier_type: str = ""   # never_store | never_log | format | label | range |
                              # threshold | required | optional | unique | default |
                              # allowed | retain
    value: Any = None


# -- Pattern ----------------------------------------------------

@dataclass
class PatternDecl(Node):           # NEW v3.8
    """pattern Email ... pattern: done"""
    name: str  = ""
    body: list = field(default_factory=list)


# -- miomap -----------------------------------------------------

@dataclass
class MiomapDecl(Node):
    """miomap ACHToCanonical ... miomap: done"""
    name:          str  = ""
    from_shape:    Optional[str] = None
    to_shape:      Optional[str] = None
    fields:        list = field(default_factory=list)   # list[MiomapField]
    conflict_rules: list = field(default_factory=list)

@dataclass
class MiomapField(Node):
    """account_number -> account  divide.by 100"""
    source:     str  = ""
    target:     str  = ""
    direction:  str  = "->"         # "->" | "to" | "<->"
    transforms: list = field(default_factory=list)


# -- mioconnect -------------------------------------------------

@dataclass
class MioconnectDecl(Node):
    """mioconnect Stripe as StripeUS ... mioconnect: done"""
    name:       str  = ""
    alias:      Optional[str] = None    # NEW v3.8 -- as StripeUS
    source:     Any  = None             # from env.X shorthand
    address:    Any  = None
    auth_type:  str  = ""               # "bearer" | "header" | "basic" | "key"
    auth_value: Any  = None             # token / api-key / basic username
    auth_value2: Any = None             # basic password (auth basic user pass)
    auth_header_name: str = ""          # custom header name (auth header "X-API-Key")
    timeout:    Any  = None
    operations: list = field(default_factory=list)   # list[MioconnectOperation]

@dataclass
class MioconnectOperation(Node):
    """operation charge path "/charges" sends sh.X returns sh.Y"""
    name:         str  = ""
    path:         str  = ""
    sends_shape:  Optional[str] = None
    returns_shape: Optional[str] = None
    method:       str  = "POST"

@dataclass
class MioconnectCall(Node):
    """Stripe.charge with charge_payload as charge_result"""
    connector: str = ""
    operation: str = ""
    payload:   Any = None      # value node
    result:    str = ""        # `as NAME` binding (optional)


# -- miosearch --------------------------------------------------

@dataclass
class MiosearchDecl(Node):          # NEW v3.8
    """miosearch.index members ... miosearch.index: done"""
    variant:     str  = ""          # "index" | "vector" | "with"
    name:        str  = ""
    source:      Any  = None
    fields:      list = field(default_factory=list)
    searchable:  list = field(default_factory=list)
    filterable:  list = field(default_factory=list)
    sortable:    list = field(default_factory=list)
    embed_field: Optional[str] = None
    dimensions:  Any  = None


# -- miovalidate ------------------------------------------------

@dataclass
class MiovalidateDecl(Node):        # NEW v3.8
    """miovalidate subscriber_rules ... miovalidate: done"""
    name:  str  = ""
    rules: list = field(default_factory=list)   # list[MiovalidateRule]

@dataclass
class MiovalidateRule(Node):
    """check name as text length 2 to 100"""
    field_name: str  = ""
    type_name:  str  = ""
    modifiers:  list = field(default_factory=list)


# -- miopdf -----------------------------------------------------

@dataclass
class MiopdfDecl(Node):             # NEW v3.8
    """miopdf.with pdfshift ... miopdf: done"""
    provider: str  = ""
    key:      Any  = None
    timeout:  Any  = None
    address:  Any  = None


# -- Task -------------------------------------------------------

@dataclass
class TaskDecl(Node):
    """task greet / name as text required / returns text / ..."""
    name:        str  = ""
    params:      list = field(default_factory=list)     # list[TaskParam]
    return_type: Optional[str] = None
    body:        list = field(default_factory=list)

@dataclass
class TaskParam(Node):
    """name as text required | name as sh.Transaction required"""
    name:         str  = ""
    type_name:    str  = ""
    is_required:  bool = False
    is_optional:  bool = False
    default:      Any  = None


# -- Journey / Saga / Page --------------------------------------



@dataclass
class ApplangBlock(Node):
    """
    applang my_map
        context: checkout
        map: applang_map
        canonical: cancel_order
        ai.decide ...
    done
    
    Self-building multilingual interaction corpus.
    3-level cache lookup: exact(input+context) -> partial(context_category) -> global(input)
    Cache miss: invoke ai.decide, persist result, return canonical.
    """
    name:      Optional[str] = None
    context:   Optional[str] = None       # context tag for Level 1/2 lookup
    map_table: str = "applang_map"        # db table name
    canonical: Optional[str] = None       # canonical symbol namespace
    ai_block:  Optional[Any] = None       # the ai.decide block for resolution
    body:      list = field(default_factory=list)

@dataclass
class DebugDecl(Node):
    """debug on | off | minimal | verbose"""
    mode: str = "on"   # "on" | "off" | "minimal" | "verbose"

@dataclass
class DebugLogStmt(Node):
    """debug.log variable.field"""
    target: str = ""   # variable name or dotted path as string

@dataclass
class DebugCheckpoint(Node):
    """debug.checkpoint "label" ... done"""
    label: str = ""
    logs: list = field(default_factory=list)  # list of DebugLogStmt

@dataclass
class JourneyDecl(Node):
    """journey AppName ... journey: done"""
    name: Optional[str] = None
    body: list = field(default_factory=list)

@dataclass
class JourneyMeta(Node):
    """Inert journey config/metadata (public/private/flow path lists). Carried so it
    is never a raw Tree, but the interpreter ignores it. public/private/flow access
    control is deferred to A8 (needs named grammar terminals)."""
    kind: str = ""
    value: object = None

@dataclass
class SagaDecl(Node):               # NEW v3.8 (was stub)
    """saga fulfill_order ... saga: done"""
    name:  str  = ""
    steps: list = field(default_factory=list)   # list[StepBlock]

@dataclass
class StepBlock(Node):              # v3.8.2
    """step reserve_inventory ... step: done"""
    name:     str  = ""
    body:     list = field(default_factory=list)
    undo:     list = field(default_factory=list)   # compensate body (undo is alias)
    best_effort: bool = False
    handlers: list = field(default_factory=list)

@dataclass
class PageDecl(Node):               # NEW v3.8
    """page Dashboard at /dashboard ... page: done"""
    name: Optional[str] = None
    path: Optional[str] = None
    body: list = field(default_factory=list)

@dataclass
class TimespanDecl(Node):
    """timespan last_quarter ... timespan: done"""
    name: str  = ""
    body: list = field(default_factory=list)

@dataclass
class TimespanRef(Node):
    """A `timespan NAME` clause inside a find -- range-filters the result by the named,
    previously-declared timespan's [start, end) window. Defaults to filtering the `created_at`
    column (2026-08-01 ruling); an explicit-field form (`timespan NAME on <field>`) is deferred."""
    name: str = ""

@dataclass
class TimespanAnchor(Node):
    anchor_type:   str = ""
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
    exclude_type: str = ""
    value:        Any = None


# -- Closer -----------------------------------------------------

@dataclass
class Closer(Node):
    """blockname: done | done [as NAME]"""
    block_name: Optional[str] = None   # None = bare done
    as_name:    Optional[str] = None   # name to bind block result to


# ==============================================================
# LISTEN / ROUTING BLOCKS
# ==============================================================

@dataclass
class ListenBlock(Node):
    """listen for ... listen: done"""
    listeners: list = field(default_factory=list)

@dataclass
class NewBlock(Node):
    """new sh.Transaction [at /path] ... new: done"""
    shape: str  = ""
    path:  Optional[str] = None
    body:  list = field(default_factory=list)

@dataclass
class RequestInboundBlock(Node):
    """request for sh.InvoiceDownload [at /path] ... request: done"""
    shape: str  = ""
    path:  Optional[str] = None
    body:  list = field(default_factory=list)

@dataclass
class ChangeBlock(Node):
    """change to sh.Task ... change: done"""
    shape: str  = ""
    body:  list = field(default_factory=list)

@dataclass
class ConnectionBlock(Node):
    """connection at /chat ... connection: done"""
    path: str  = ""
    body: list = field(default_factory=list)

@dataclass
class WhileActiveBlock(Node):
    """while.active ... while.active: done"""
    body: list = field(default_factory=list)

@dataclass
class LoopBlock(Node):
    """loop [name] ... loop: done -- open-ended loop, break with 'stop' (or 'stop name')."""
    name: Optional[str] = None
    body: list = field(default_factory=list)

@dataclass
class PurposeBlock(Node):
    """purpose "X" ... purpose: done -- assert a use-purpose. A [pii] field referenced at
    a use/egress point inside must be collected for X, else fail loud (GDPR purpose limit)."""
    purpose: Optional[str] = None
    body: list = field(default_factory=list)

@dataclass
class OnOpen(Node):
    action: Any = None

@dataclass
class OnClose(Node):
    action: Any = None

@dataclass
class FromConnectorBlock(Node):     # NEW v3.8
    """from Stripe / when payment.succeeded ... / otherwise ..."""
    connector: str  = ""
    when_clauses: list = field(default_factory=list)   # list[FromConnectorWhen]
    otherwise: list = field(default_factory=list)

@dataclass
class FromConnectorWhen(Node):      # NEW v3.8
    event: str  = ""
    body:  list = field(default_factory=list)


# ==============================================================
# FLOW CONTROL
# ==============================================================

# IfBlock node REMOVED (A6): `if` as a block opener is retired (No-If canon).
# `if` is trailing-inline only; multi-branch logic is `check / when / otherwise`.

@dataclass
class OrIfClause(Node):
    condition: Any  = None
    body:      list = field(default_factory=list)

@dataclass
class OtherwiseClause(Node):
    body: list = field(default_factory=list)

@dataclass
class CheckBlock(Node):
    """check status / when "active" -> ... / otherwise ..."""
    value:        Any  = None
    when_clauses: list = field(default_factory=list)   # list[CheckWhen]
    otherwise:    Optional[OtherwiseClause] = None
    as_name:      Optional[str] = None   # check: done as NAME
    as_name:      Optional[str] = None   # check: done as NAME

@dataclass
class CheckWhen(Node):
    value:     Any  = None
    condition: str  = "when"   # "when" | "above" | "below" | "contains" | "is_in" | "not"
    body:      list = field(default_factory=list)

@dataclass
class EachBlock(Node):
    """each user in users"""
    item:       str  = ""
    collection: Any  = None
    body:       list = field(default_factory=list)
    as_name:    Optional[str] = None   # each: done as NAME
    as_name:    Optional[str] = None   # each: done as NAME

@dataclass
class RepeatBlock(Node):
    """repeat 3 times"""
    count: Any  = None
    body:  list = field(default_factory=list)

@dataclass
class WhileBlock(Node):
    """while queue.size > 0"""
    condition: Any  = None
    body:      list = field(default_factory=list)
    as_name:   Optional[str] = None   # while: done as NAME
    as_name:   Optional[str] = None   # while: done as NAME

@dataclass
class SectionBlock(Node):
    """section /admin"""
    path: str  = ""
    body: list = field(default_factory=list)

@dataclass
class TrailingQualifier(Node):      # NEW v3.8
    """if condition -- trailing qualifier on action statements"""
    condition: Any = None


# ==============================================================
# MioQL -- DATA OPERATIONS
# ==============================================================

@dataclass
class RetrieveBlock(Node):
    """retrieve member from db.members match id to ..."""
    name:      str  = ""
    alias:     Optional[str] = None   # NEW v3.8 -- as username
    modifier:  Optional[str] = None   # one | first | last | all | every
    source:    Any  = None
    locked:    bool = False           # NEW v3.8 -- retrieve member from db locked
    body:      list = field(default_factory=list)
    handlers:  list = field(default_factory=list)

@dataclass
class FindBlock(Node):
    """find recent in db.transactions where ..."""
    name:     str  = ""
    group_by: Optional[str] = None   # NEW v3.8 -- find summary by merchant
    time_bucket: Optional[str] = None  # hour|day|week|month|quarter|year -- `by day`
    source:   Any  = None
    body:     list = field(default_factory=list)
    handlers: list = field(default_factory=list)
    random_n: Optional[int] = None   # find ... random.N -- return N random matches

@dataclass
class GrabBlock(Node):              # NEW v3.8
    """grab config from cache.settings match key to "app_mode" """
    name:    str  = ""
    source:  Any  = None
    match:   Any  = None
    handlers: list = field(default_factory=list)

@dataclass
class GetBlock(Node):               # NEW v3.8
    """get config from cache.settings match key to "app_config" """
    name:    str  = ""
    source:  Any  = None
    match:   Any  = None
    handlers: list = field(default_factory=list)

@dataclass
class PullBlock(Node):              # NEW v3.8
    """pull up to 100 [random] from db.transactions where status is pending"""
    limit:   Any  = None            # None = no limit (pull from queue)
    source:  Any  = None
    random:  bool = False           # pull up to N random from — sample, not in-order
    body:    list = field(default_factory=list)
    handlers: list = field(default_factory=list)
    as_name: Any  = None            # pull: done as NAME — bind the pulled list

@dataclass
class CompareBlock(Node):           # NEW v3.8
    """compare group_a to group_b"""
    name_a:  str  = ""
    name_b:  str  = ""
    body:    list = field(default_factory=list)
    handlers: list = field(default_factory=list)

@dataclass
class SummarizeBlock(Node):         # NEW v3.8
    """summarize / amount.sum / count.count / ..."""
    fields: list = field(default_factory=list)   # list[AggField]

@dataclass
class AggField(Node):               # NEW v3.8
    name:     str = ""
    function: str = ""   # sum | count | average | max | min | running_sum | etc.
    arg:      Any = None

@dataclass
class CalculateBlock(Node):         # NEW v3.8
    fields: list = field(default_factory=list)

@dataclass
class JoinBlock(Node):              # NEW v3.8
    """with / with.required / with.all"""
    variant: str  = "with"          # "with" | "with.required" | "with.all"
    name:    str  = ""
    source:  Any  = None
    body:    list = field(default_factory=list)

@dataclass
class SaveBlock(Node):
    """save to db.cleared_transactions / id value / ...  [as NAME]"""
    target:  Any  = None
    fields:  list = field(default_factory=list)   # list[FieldValue]
    handlers: list = field(default_factory=list)
    alias:   Optional[str] = None                 # save ... as NAME -> bind new record
    dedupe_fields: list = field(default_factory=list)  # save ... unless <a>, <b> exists -> skip dup
                                                       # (composite: all named columns together
                                                       #  identify one logical row)

@dataclass
class SaveOrUpdateBlock(Node):      # NEW v3.8
    """save or update db.members match email to ... / name value"""
    source:  Any  = None
    match:   Any  = None
    fields:  list = field(default_factory=list)
    handlers: list = field(default_factory=list)

@dataclass
class SaveAllBlock(Node):           # NEW v3.8
    """save all to db.members from batch_data"""
    target:  Any  = None
    source:  Any  = None
    handlers: list = field(default_factory=list)

@dataclass
class UpdateBlock(Node):
    """update db.members match id to ... / status "active" """
    source:  Any  = None
    body:    list = field(default_factory=list)
    handlers: list = field(default_factory=list)

@dataclass
class RemoveBlock(Node):
    """remove from db.sessions match user_id to user.id"""
    source:    Any  = None
    condition: Any  = None
    match:     Any  = None   # MatchClause -- alias for condition
    handlers:  list = field(default_factory=list)

@dataclass
class RemoveAllBlock(Node):         # NEW v3.8
    """remove.all from db.temp_imports  (optional on.success / on.failure)"""
    source:   Any  = None
    handlers: list = field(default_factory=list)

@dataclass
class CheckMioqlBlock(Node):         # NEW v3.8
    """check exists / check count / check unique"""
    variant: str  = ""              # "exists" | "count" | "unique"
    name:    str  = ""
    source:  Any  = None
    condition: Any = None
    handlers:  list = field(default_factory=list)

@dataclass
class FieldValue(Node):
    """field_name  value"""
    name:  str = ""
    value: Any = None

@dataclass
class MatchClause(Node):
    """match id to value  |  match.unique email to value"""
    modifier: str = ""    # "" | "unique" | "any" | "none" | "all"
    field:    str = ""
    value:    Any = None

@dataclass
class WhereClause(Node):
    """where status is active | where amount is above 10000"""
    field:     str = ""
    condition: str = ""   # is | above | below | between | is_in | contains | etc.
    value:     Any = None
    value2:    Any = None   # for between

@dataclass
class AndClause(Node):
    """and created_at is.in last 24 hours"""
    field:     str = ""
    condition: str = ""
    value:     Any = None
    value2:    Any = None   # for between

@dataclass
class OrderClause(Node):
    """order.up by name | order.down by created_at"""
    field:     str = ""
    direction: str = "up"    # "up" | "down"

@dataclass
class LimitClause(Node):
    """up to 25"""
    count:  Any = None
    source: Any = None

@dataclass
class SkipClause(Node):             # NEW v3.8
    """skip 40"""
    count: Any = None

@dataclass
class PaginateClause(Node):         # NEW v3.8
    """paginate by 25"""
    count: Any = None

@dataclass
class CursorClause(Node):           # NEW v3.8
    """cursor from request.cursor"""
    source: Any = None

@dataclass
class CacheClause(Node):
    """cache for 10 minutes"""
    duration: Any = None

@dataclass
class ReturnClause(Node):           # NEW v3.8
    """return id, name as display_name, amount.sum as total"""
    fields: list = field(default_factory=list)

@dataclass
class ExportClause(Node):           # NEW v3.8
    """export as.csv to "exports/report.csv" """
    format: str = ""
    target: Any = None

@dataclass
class InjectClause(Node):           # NEW v3.8
    """inject into report"""
    target: str = ""


# ==============================================================
# RESULT HANDLERS
# ==============================================================

@dataclass
class OnFailure(Node):
    inline: Any  = None
    body:   list = field(default_factory=list)

@dataclass
class OnSuccess(Node):
    inline: Any  = None
    body:   list = field(default_factory=list)

@dataclass
class OnError(Node):
    body: list = field(default_factory=list)


# ==============================================================
# AI PRIMITIVES
# ==============================================================

@dataclass
class AiDecideBlock(Node):
    """ai.decide isFraudulent returns boolean ... ai.decide: done"""
    name:        str  = ""
    return_type: str  = ""
    body:        list = field(default_factory=list)
    args:        list = field(default_factory=list)  # compat -- no parens in v3.8
    goal:        str  = ""   # focuses the vote -- replaces generic decision description
    persona:     str  = ""   # shapes explanation field only (not result/confidence)
    context:     str  = ""   # situational info appended to user prompt
    temperature: Any  = None # 0.0-2.0 creativity
    model:       str  = ""   # override model for this block

@dataclass
class AiCompareBlock(Node):
    """ai.compare <name> ... ai.compare: done

    Relational judgment: which of two or more inputs is better. Binds a record with
    { winner, margin, explanation } to <name>. Shares ai_decide_body, so it takes the
    same goal/persona/context/temperature/model options as ai.decide.
    """
    name:        str  = ""
    return_type: str  = "text"
    body:        list = field(default_factory=list)
    goal:        str  = ""
    persona:     str  = ""
    context:     str  = ""
    temperature: Any  = None
    model:       str  = ""


@dataclass
class AiRespondBlock(Node):
    """ai.respond <name> ... ai.respond: done

    Interaction response: reacts to something that happened (support replies, chat,
    game narration). Binds the generated response text to <name>. Shares ai_decide_body,
    so it takes the same options as ai.decide.
    """
    name:        str  = ""
    return_type: str  = "text"
    body:        list = field(default_factory=list)
    goal:        str  = ""
    persona:     str  = ""
    context:     str  = ""
    temperature: Any  = None
    model:       str  = ""


@dataclass
class AiDecideInvoke(Node):
    """ai.decide <name> -- invoke a previously-declared ai.decide block by name,
    binding its result to a variable named <name> (declare-once / invoke-many)."""
    name: str = ""

@dataclass
class ConfidenceCheck(Node):
    """confidence above 0.85"""
    operator:  str = "above"
    threshold: Any = None

@dataclass
class UsingChain(Node):
    """using fraud_chain"""
    chain_name: str = ""

@dataclass
class WeighClause(Node):
    """weigh transaction.amount, member.history, ..."""
    inputs: list = field(default_factory=list)   # list[DottedName]

@dataclass
class NotConfidentBlock(Node):
    """not confident ... (mandatory fallback)"""
    body: list = field(default_factory=list)

@dataclass
class RankOption(Node):
    """A weighted candidate in an ai.rank block: `option "x" if <cond> weight N`
    or the no-match fallback `default "y" weight N`."""
    value:      Any = None
    condition:  Any = None     # a Condition node, or None
    weight:     Any = None     # value_expr, or None
    is_default: bool = False

@dataclass
class AiRankBlock(Node):
    """ai.rank <name> for <subject> -- weighted multi-option ranking. Picks the
    best candidate and binds the winner to <name> with a confidence score."""
    name:          Optional[str] = None
    subject:       Any = None
    return_type:   str = "text"
    options:       list = field(default_factory=list)
    confidence:    Any = None
    not_confident: Optional["NotConfidentBlock"] = None
    audit:         Optional["AiAuditStmt"] = None
    explain:       Optional["AiExplainBlock"] = None

@dataclass
class AiAuditStmt(Node):
    """ai.audit to fraud_audit_log"""
    log_name: str = ""

@dataclass
class AiExplainBlock(Node):
    """ai.explain fraud_check as explanation / audience "..." / format "..." """
    decision_name: Optional[str] = None
    alias:         Optional[str] = None
    audience:      Optional[str] = None
    goal:          str  = ""
    persona:       str  = ""
    context:       str  = ""
    temperature:   Any  = None
    model:         str  = ""
    format:        Optional[str] = None

@dataclass
class AiConnectBlock(Node):
    """ai.connect fraud_chain ... ai.connect: done"""
    names:     list = field(default_factory=list)   # group name(s)
    providers: list = field(default_factory=list)   # [{provider, model}] in fallback order
    handlers:  list = field(default_factory=list)   # on.failure handler(s)

@dataclass
class AiConnectProvider(Node):
    provider:          str = ""
    quality_threshold: Any = None
    body:              list = field(default_factory=list)

@dataclass
class AiCreateStmt(Node):
    """ai.create poster returns image as banner / goal / style / size / ..."""
    create_type: str  = ""   # image | audio | logic | text | data | video
    name:        str  = ""   # asset/decision name
    alias:       str  = ""   # `as NAME` -- variable the result is stored in
    return_type: str  = ""   # same as create_type for block form
    goal:        str  = ""   # what to create -- the prompt
    persona:     str  = ""   # voice/style -- shapes generated output
    context:     str  = ""   # situational info -- appended to prompt
    temperature: Any  = None # creativity control
    model:       str  = ""   # model override for this block
    style:       str  = ""   # image/audio/video style descriptor
    negative:    str  = ""   # what NOT to generate
    size:        str  = ""   # image dimensions e.g. "1024x1024"
    voice:       str  = ""   # audio voice descriptor
    pace:        Any  = None # audio speed 0.5-2.0
    duration:    Any  = None # video/audio length in seconds
    source:      str  = ""   # knowledge base ref
    attrs:       dict = field(default_factory=dict)  # `with k v` extras + template
    body:        list = field(default_factory=list)

@dataclass
class AiOverrideStmt(Node):
    """ai.override decision isFraudulent / by reviewer.id / isFraudulent false / reason "..." / to log"""
    name:           str = ""    # decision name being overridden
    value:          Any = None  # corrected value
    by_attribution: Any = None  # E008 -- who made the correction
    reason:         str = ""    # E010 -- why the correction was made
    log_target:     str = ""    # optional -- defaults to original ai.audit log
@dataclass
class RunBlock(Node):               # v3.8.2 -- run is canonical, call retired
    """run fulfillOrder / order order / customer customer / run: done"""
    task_name: str  = ""
    args:      list = field(default_factory=list)   # list[FieldValue]
    inline_arg: Any = None          # run sendWelcome with member
    alias:     str  = ""            # `call greet with "x" as greeting` -> bind result

CallBlock = RunBlock  # backward compat alias -- call is retired but interpreter still works

@dataclass
class ApplyBlock(Node):             # NEW v3.8
    """apply miogscreen.remove_background as clean_image / ..."""
    pack_method: Any  = None        # DottedName
    alias:       Optional[str] = None
    body:        list = field(default_factory=list)

@dataclass
class ApplyCollectionBlock(Node):   # NEW v3.8
    """apply miogscreen to every portrait in portrait_file"""
    pack_name:  str  = ""
    noun:       str  = ""
    collection: Any  = None
    body:       list = field(default_factory=list)

@dataclass
class ModifyBlock(Node):            # NEW v3.8
    """modify every portrait in portrait_file / ... / modify: done"""
    variant:    str  = "every"      # "every" | "all"
    noun:       Optional[str] = None   # required for "every"
    collection: Any  = None
    condition:  Any  = None
    body:       list = field(default_factory=list)

@dataclass
class CopyBlock(Node):              # NEW v3.8
    """copy source to destination / rename output.png"""
    source:      Any  = None
    destination: Any  = None
    rename:      Any  = None
    allow_overwrite: bool = False

@dataclass
class CreateBlock(Node):
    """create invoice as sh.Invoice / ... / create: done  (was 'make', retired)"""
    name:       str  = ""
    shape:      Optional[str] = None
    from_source: Any = None         # create Invoice from order
    body:       list = field(default_factory=list)

@dataclass
class RequestOutboundBlock(Node):   # NEW v3.8
    """request ocr_result from GoogleVision.ocr / with ... / on.failure ..."""
    result_name: str  = ""
    connector:   str  = ""
    operation:   str  = ""
    body:        list = field(default_factory=list)
    handlers:    list = field(default_factory=list)

@dataclass
class RerunStmt(Node):              # NEW v3.8
    """rerun sendAlert with event  |  rerun.3 task with arg"""
    variant:   str  = ""       # "" | "n" | "after" | "max" | "until"
    task_name: str  = ""
    count:     Any  = None     # for rerun.N and rerun.max
    delay:     Any  = None     # for rerun.after
    condition: Any  = None     # for rerun.until
    arg:       Any  = None
    on_exceeded: list = field(default_factory=list)


# ==============================================================
# COMPLIANCE ACTIONS
# ==============================================================

@dataclass
class CmPurgeBlock(Node):
    """cm.purge member.id / reason "GDPR Article 17" / includes "..." """
    target:   Any  = None
    reason:   Optional[str] = None
    includes: list = field(default_factory=list)
    preserve: list = field(default_factory=list)
    source:   Any  = None   # from-form: the table to delete matched rows from
    matches:  list = field(default_factory=list)  # from-form: MatchClause list

@dataclass
class CmRetainStmt(Node):
    value:    Any = None
    duration: Any = None

@dataclass
class CmReportStmt(Node):
    report_type: str = ""
    target:      Any = None

@dataclass
class CmExpireStmt(Node):
    value:    Any = None
    duration: Any = None

@dataclass
class CmLockStmt(Node):
    target: Any = None

@dataclass
class CmNotifyStmt(Node):
    event: str = ""
    body:  list = field(default_factory=list)


# ==============================================================
# ERROR HANDLING
# ==============================================================

@dataclass
class TryBlock(Node):
    body:          list = field(default_factory=list)
    catch:         Optional["CatchClause"] = None
    on_failure:    Optional["OnFailure"] = None
    on_success:    Optional["OnSuccess"] = None
    always:        Optional["AlwaysClause"] = None
    retry_times:   Optional[int]   = None    # `up to N times`
    per_timeout:   Optional[float] = None    # `within N <unit>` (per attempt)
    total_timeout: Optional[float] = None    # `within N <unit> total`
    backoff:       Optional[float] = None    # `waiting N <unit> between`

@dataclass
class CatchClause(Node):
    catch_type: Optional[str] = None   # "timeout" | "any" | named
    alias:      Optional[str] = None
    body:       list = field(default_factory=list)

@dataclass
class AlwaysClause(Node):
    body: list = field(default_factory=list)

@dataclass
class TransactionBlock(Node):
    body: list = field(default_factory=list)


# ==============================================================
# AUTH
# ==============================================================

@dataclass
class SignBlock(Node):              # NEW v3.8
    """sign url for cloud_storage / expires in 30 minutes / named download_url"""
    sign_type:  str  = "url"        # "url" | "upload url"
    for_target: Any  = None
    expires:    Any  = None
    named:      Optional[str] = None
    handlers:   list = field(default_factory=list)

@dataclass
class VerifyTokenStmt(Node):
    """verify token from request.header "Authorization" """
    source:  Any  = None
    header:  Optional[str] = None
    scope:   Optional[str] = None
    handlers: list = field(default_factory=list)

@dataclass
class ValidateStmt(Node):           # NEW v3.8
    """validate using subscriber_rules | validate rules against form.data"""
    variant:    str  = "using"      # "using" | "against"
    rules_name: str  = ""
    source:     Any  = None
    handlers:   list = field(default_factory=list)


# ==============================================================
# ACTION STATEMENTS
# ==============================================================

@dataclass
class GiveBackStmt(Node):
    """give back 200 "OK"  |  give back value  |  give back 201 call task with arg"""
    status:    Optional[Any] = None
    value:     Any  = None
    modifier:  Any  = None
    qualifier: Optional["TrailingQualifier"] = None   # NEW v3.8
    trusted:   bool = False    # `trusted` opts out of the XSS-safe {{ }} auto-escape (raw HTML/JSON)

@dataclass
class GiveStmt(Node):
    """give <value> as download ["<filename>"] -- hand a value over as a file."""
    value:     Any = None
    modifier:  Any = None            # the word after `as`; only 'download' is built
    filename:  Any = None            # explicit saved name, or None to infer from a path
    qualifier: Optional["TrailingQualifier"] = None


@dataclass
class JumpToStmt(Node):
    destination: Any = None
    qualifier:   Optional["TrailingQualifier"] = None

@dataclass
class HaltStmt(Node):
    qualifier: Optional["TrailingQualifier"] = None   # NEW v3.8

@dataclass
class StopStmt(Node):
    target: Optional[str] = None   # 'stop name' breaks the loop with that name
    condition: Any = None          # 'stop when <condition>' -- break only if true

@dataclass
class SkipStmt(Node):
    condition: Any = None          # 'skip when <condition>' -- skip this pass only if true

@dataclass
class ShowStmt(Node):
    value:    Any = None
    modifier: Any = None

@dataclass
class RaiseStmt(Node):
    error_name: Optional[str] = None
    message:    Any = None

@dataclass
class SendStmt(Node):
    value:  Any = None
    target: Any = None

@dataclass
class BroadcastStmt(Node):
    room:            Any = None
    value:           Any = None
    except_session:  Optional[Any] = None

@dataclass
class StreamStmt(Node):
    value:  Any = None
    target: Any = None

@dataclass
class NotifyStmt(Node):
    target:  Any  = None
    channel: Optional[str] = None
    body:    list = field(default_factory=list)

@dataclass
class ServiceCallStmt(Node):
    """miolog.alert "msg" | miomail.send to user.email"""
    service: str  = ""
    method:  str  = ""
    args:    Any  = None
    params:  list = field(default_factory=list)

@dataclass
class RunAsyncBlock(Node):
    """run async generateReport as report_job"""
    value: Any  = None
    alias: Optional[str] = None

@dataclass
class WaitForStmt(Node):
    """wait for task_a, task_b, task_c"""
    names: list = field(default_factory=list)


# ==============================================================
# ASSIGNMENT
# ==============================================================

@dataclass
class Assignment(Node):
    """name [as type] [=] value [default fallback]"""
    name:      str  = ""
    type_name: Optional[str] = None
    value:     Any  = None
    default:   Any  = None


@dataclass
class ThenChain(Node):
    """head then step then step ...  — sequential result-threading pipeline.
    Each step runs with `it` bound to the running result; transforming steps
    rebind `it`, side-effect steps pass it through."""
    steps: list = field(default_factory=list)


# ==============================================================
# CONDITIONS
# ==============================================================

@dataclass
class Condition(Node):
    left:  Any = None
    op:    str = ""
    right: Any = None

@dataclass
class NotCondition(Node):
    condition: Any = None

@dataclass
class AndCondition(Node):
    left:  Any = None
    right: Any = None

@dataclass
class OrCondition(Node):
    left:  Any = None
    right: Any = None

@dataclass
class UnlessGuard(Node):
    """A statement guarded by `unless <condition>`: run `stmt` unless the
    condition evaluates true."""
    stmt:      Any = None
    condition: Any = None


@dataclass
class IfGuard(Node):
    """A statement guarded by `if <condition>`: run `stmt` if the condition is
    true. The positive counterpart of UnlessGuard."""
    stmt:      Any = None
    condition: Any = None

@dataclass
class DotStateCheck(Node):
    """is.empty | is.in | is.not"""
    value:  Any = None
    prefix: str = ""   # "is" | "not"
    state:  str = ""


# ==============================================================
# VALUE EXPRESSIONS
# ==============================================================

@dataclass
class Literal(Node):
    value:        Any = None
    literal_type: str = ""   # string | number | bool | null

@dataclass
class DottedName(Node):
    """user.email | transaction.amount"""
    parts: list = field(default_factory=list)

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
class MioaiRef(Node):
    """mioai.embed"""
    method: str = ""

@dataclass
class NowCall(Node):
    """now()"""
    pass

@dataclass
class UuidCall(Node):               # NEW v3.8
    """uuid()"""
    pass

@dataclass
class MathExpr(Node):
    """(subtotal + tax)  |  (amount > 1000)  |  (result = expected)"""
    left:   Any = None
    op:     str = ""
    right:  Any = None

@dataclass
class TemplateString(Node):
    """{{ user.name }} | "Hello {{ name }}" """
    template: str = ""

@dataclass
class ListLiteral(Node):
    items: list = field(default_factory=list)

@dataclass
class ColorLit(Node):               # NEW v3.8
    """#00FF00"""
    value: str = ""

@dataclass
class PercentLit(Node):             # NEW v3.8
    """5%"""
    value: str = ""

@dataclass
class DimensionLit(Node):           # NEW v3.8
    """120px | 72dpi | 8x10"""
    value: str = ""

@dataclass
class TypeCastExpr(Node):           # NEW v3.8
    """(today - member.created_at) as.days  |  amount as.decimal"""
    value:     Any = None
    cast_type: str = ""   # days | hours | decimal | int | string | uc | lc | etc.
    places:    Any = None  # decimal places for as.decimal.N (None = full precision)

@dataclass
class RoundExpr(Node):              # NEW v3.8
    """(price * qty) round.up  |  round.to 2"""
    value:     Any = None
    direction: str = ""   # "up" | "down" | "to"
    places:    Any = None

@dataclass
class StringOpExpr(Node):
    """
    String operation expression -- returns a value.
    truncate.to 35  |  mask.all except last 4
    cmd after "take "  |  cmd before " "  |  cmd left 1  |  cmd right 5
    """
    operation:   str = ""   # truncate.to / mask.all / after / before / left / right
    operand:     Any = None # source value (the string being operated on)
    arg:         Any = None # argument (delimiter string or character count)
    direction:   str = ""   # for mask.all: "last" or "first" (which end to keep)
    modifier:    str = ""   # "last" | "first" | "words"
    default_val: Any = None # fallback when substring not found (after/before)


# ==============================================================
# TIME / DATE EXPRESSIONS
# ==============================================================

@dataclass
class TimeExpr(Node):
    base:      str = ""   # "now()" | "today" | "yesterday" | "last_month" | etc.
    offset_op: Optional[str] = None
    offset:    Optional["DurationExpr"] = None

@dataclass
class TimePeriod(Node):
    """A time PERIOD (an interval), used by `is.in <period>`. Either calendar-bound
    (`calendar` = 'today'|'this_week'|'last_quarter'|...) or rolling (`rolling` = a
    DurationExpr for `last N <unit>`). The interpreter resolves it to a half-open
    [start, end) range via _period_range / _rolling_range."""
    calendar: Optional[str] = None
    rolling:  Optional["DurationExpr"] = None

@dataclass
class DatetimeExpr(Node):
    date:     str           = ""
    time:     Optional[str] = None
    timezone: Optional[str] = None

@dataclass
class DurationExpr(Node):
    count: Any = None
    unit:  str = ""

@dataclass
class SinceExpr(Node):
    anchor: Any = None

@dataclass
class TimePeriodExpr(Node):         # NEW v3.8
    """last 30 days | this month | last_week"""
    variant: str = ""   # "last_N" | "this" | "constant"
    count:   Any = None
    unit:    str = ""
    constant: str = ""  # last_month | this_year | etc.


# ==============================================================
# BACKWARD COMPATIBILITY ALIASES
# Old transformer (mohio_transformer_ast.py) uses these names.
# ==============================================================

RequestBlock    = RequestInboundBlock   # renamed in v3.8
SagaBlock       = SagaDecl             # renamed in v3.8
OnRollbackClause = StepBlock           # old saga rollback pattern
MapLiteral      = ListLiteral          # was separate, now unified
@dataclass
class RandomValue(Node):
    """random.uuid / random.token length 32 / random.number between N and N"""
    kind:    str  = ""      # uuid / color / token / hex / number / select / count
    length:  int  = 0       # for token/hex
    min_val: Any  = None    # for number between N and N
    max_val: Any  = None    # for number between N and N
    count:   int  = 0       # for random.N count modifier
    cast:    str  = ""      # as integer / as decimal.2 etc
    alias:   str  = ""      # give back random as [name]
    source:  Any  = None    # for `random from <list>` — the list expr to pick from

@dataclass
class ConcatExpr(Node):
    """
    String concatenation -- "Hello, " & member.name & "!"
    & is always string join. + is always math. Never mixed.
    """
    terms: list = field(default_factory=list)

@dataclass
class DynamicFieldValue(Node):
    """
    set field puzzle.flag_set to "true"
    Dynamic field assignment -- field name comes from a runtime value.
    Generates a runtime warning in mio check (cannot validate at compile time).
    The field_name is evaluated at runtime to determine which column to set.
    """
    field_name: Any = None   # value_expr -- evaluated to get the column name
    value:      Any = None   # value_expr -- the value to set

@dataclass
class MiohttpStmt(Node):
    """
    miohttp.get "https://api.example.com/users"
        header "Authorization" "Bearer {{ token }}"
        as users
    miohttp.get: done

    miohttp.post "https://api.example.com/orders"
        header "Content-Type" "application/json"
        body   order
        as     result
    miohttp.post: done

    Result bound to variable via "as NAME".
    Response shape: { status, body, headers, ok }
    """
    method:  str  = "get"    # get / post / put / delete / patch
    url:     Any  = None     # value_expr
    headers: list = field(default_factory=list)   # [(name, value), ...]
    body:    Any  = None
    auth:    Any  = None
    timeout: int  = 30
    alias:   str  = ""       # "as result" -- binds response to this name

@dataclass
class MiofileStmt(Node):
    """
    miofile.read   "report.txt" as content
    miofile.write  "report.txt" content
    miofile.delete "report.txt"
    miofile.exists "report.txt"
    miofile.move   "a.txt" to "b.txt"
    miofile.copy   "a.txt" to "b.txt"
    miofile.list   "uploads" as entries

    All paths resolve inside a sandbox root (env MIOFILE_ROOT, else ./mio_files).
    A path that escapes the root fails loud. read/list bind via "as NAME".
    """
    op:      str = "read"   # read/write/delete/exists/move/copy/list
    path:    Any = None     # value_expr
    content: Any = None     # value_expr (write)
    dest:    Any = None     # value_expr (move/copy)
    alias:   str = ""       # "as NAME" (read/list)

@dataclass
class MiofileDecl(Node):
    """
    Storage declaration. Local/temp zones are the free engine; cloud zones and the
    managed lifecycle policies (expires, clean) are the commercial layer: parsed and
    validated in open core, executed only in the Mohio commercial runtime.

    miofile
        local "uploads" as docs
            accept pdf, png
            max size 10mb
        cloud receipts          // commercial: needs the runtime / a license
            bucket env.S3_BUCKET
            expires 30 days     // commercial: managed lifecycle
    miofile: done

    zones: list of {kind, name, path, policies:[{policy, parts}]}
    """
    zones: list = field(default_factory=list)

@dataclass
class MiomailStmt(Node):
    """
    miomail.send
        to      "user@example.com"
        from    "noreply@myapp.com" as "MyApp"
        subject "Welcome!"
        body    "Hello {{ member.name }}"
        attach  report.url
        cc      "manager@myapp.com"
        bcc     "archive@myapp.com"
    miomail.send: done

    Providers: SMTP (env.SMTP_*), SendGrid (env.SENDGRID_KEY),
               Brevo (env.BREVO_KEY), mock (dev mode -- prints to console)
    """
    action:   str  = "send"   # send / queue / template
    to:       Any  = None
    from_:    Any  = None
    from_name:Any  = None
    subject:  Any  = None
    body:     Any  = None
    template: Any  = None
    attach:   list = field(default_factory=list)
    cc:       list = field(default_factory=list)
    bcc:      list = field(default_factory=list)
    reply_to: Any  = None

@dataclass
class SqlBlock(Node):
    """
    Raw SQL escape hatch -- sql / SELECT ... / sql: done
    Template expressions {{ variable }} are interpolated before execution.
    Result returned as list of dicts (rows) or row count for writes.
    """
    sql:   str = ""    # raw SQL text with {{ }} placeholders
    alias: str = ""    # optional: as result_name

@dataclass
class ShowBlock(Node):
    """
    Raw HTML render block -- show / <html> / show: done
    Standard HTML/CSS/JS allowed inline (forgiveness, like SqlBlock).
    Template expressions {{ variable }} are interpolated before output.
    """
    html: str = ""     # raw HTML text with {{ }} placeholders
    escape: bool = False  # render block escapes interpolated {{ }} values; show block does not

@dataclass
class HashBlock(Node):
    """hash form.password as hashed using bcrypt"""
    value:     Any  = None
    alias:     Optional[str] = None
    algorithm: str  = "sha256"
    body:      list = field(default_factory=list)

@dataclass
class CheckAgainstStmt(Node):
    """check form.password against member.hashed"""
    value:  Any  = None
    stored: Any  = None
    body:   list = field(default_factory=list)

@dataclass
class EncodeStmt(Node):
    """encode value as base64"""
    value:  Any = None
    format: str = "base64"
    alias:  str = ""

@dataclass
class DecodeStmt(Node):
    """decode value from base64"""
    value:  Any = None
    format: str = "base64"
    alias:  str = ""

@dataclass
class ParseStmt(Node):
    """parse "2026-03-31" as date"""
    value:     Any = None
    type_name: str = "text"
    alias:     str = ""

@dataclass
class MathFuncStmt(Node):
    """absolute / minimum of / maximum of / average of / sum of / percentage X of Y"""
    func:   str = "absolute"
    value:  Any = None
    value2: Any = None   # second operand for `percentage X of Y`
    alias:  str = ""

@dataclass
class AppConfigBlock(Node):
    """app config / name "..." / timezone "..." / done"""
    body: list = field(default_factory=list)

@dataclass
class MioScheduleDecl(Node):
    """mioschedule daily_reconciliation / every 1 days / run taskName"""
    name: str  = ""
    body: list = field(default_factory=list)


@dataclass
class RunSchedule(Node):
    """run mioschedule.NAME now|immediately — fire a registered schedule now."""
    name: str = ""

FuncCall        = NowCall              # legacy -- now has dedicated terminals

# -- v3.8.2 new nodes ----------------------------------------------------------

@dataclass
class AiResolveBlock(Node):
    """ai.resolve name / cache X / learned db.Y / live ai.decide Z / ai.resolve: done"""
    name:        str  = ""
    cache_ref:   Any  = None
    learned_ref: Any  = None
    live_block:  Any  = None
    goal:        str  = ""
    persona:     str  = ""
    context:     str  = ""
    temperature: Any  = None
    model:       str  = ""

@dataclass
class AiAgentBlock(Node):
    """ai.agent Name / name / goal / persona / context / temperature / tools / limits"""
    name:        str  = ""   # code identifier
    display_name:str  = ""   # human-readable name (name keyword)
    goal:        str  = ""   # what the agent is trying to accomplish
    persona:     str  = ""   # character the agent adopts
    context:     str  = ""   # situational info
    temperature: Any  = None # creativity control
    model:       str  = ""   # model override
    tools:       list = field(default_factory=list)
    limits:      Any  = None # LimitsBlock
    confidence:  Any  = None # ConfidenceCheck
    body:        list = field(default_factory=list)

@dataclass
class LimitsBlock(Node):
    """limits / max steps N / max tokens N / cost ceiling N / limits: done"""
    max_steps:    int   = 0
    max_tokens:   int   = 0
    max_calls:    int   = 0
    cost_ceiling: float = 0.0
    timeout:      Any   = None

@dataclass
class ToolsBlock(Node):
    """Carrier for an ai.agent tools grant: connector operations (and/or ai
    builtins) the agent is permitted to call. No tools block means no tools."""
    grants: list = field(default_factory=list)

@dataclass
class LanguagesBlock(Node):
    """languages / current EN / supported PT / deploy EN / map ... / languages: done"""
    current:   str  = ""
    supported: list = field(default_factory=list)
    deploy:    str  = ""
    planned:   list = field(default_factory=list)
    maps:      list = field(default_factory=list)

@dataclass
class EnterpriseBlock(Node):
    """enterprise / key env.X / tier professional / enterprise: done"""
    key_ref: str = ""   # env.MOHIO_ENTERPRISE_KEY
    tier:    str = ""   # professional / regulated / unlimited




@dataclass
class MatchBlock(Node):
    """
    match
        room    to current_room
        verb    to command_verb
    match: done
    All conditions must be true (AND).
    """
    pairs: list = field(default_factory=list)  # list of (field, value) tuples

@dataclass
class MatchAnyBlock(Node):
    """
    match any
        label to "bug"
        label to "feature"
    match any: done
    At least one condition must be true (OR).
    """
    pairs: list = field(default_factory=list)

@dataclass
class NoMatchBlock(Node):
    """
    no.match
        status to "Done"
        status to "Cancelled"
    no.match: done
    None of these conditions may be true (NOT).
    """
    pairs: list = field(default_factory=list)

@dataclass
class MatchPair(Node):
    """field to value -- one condition in a match block"""
    field: str = ""
    value: Any = None

@dataclass
class ViewCallStmt(Node):
    """
    view "rates_page"
        price  price
        title  "Stone Ridge"

    Standalone view call statement -- renders template and
    makes result available for the current response.
    """
    template_name: str  = ""
    params:        list = field(default_factory=list)

@dataclass
class ViewRender(Node):
    """
    give back 200 view "rates_page"
        cabin       cabin
        price       249.99
    give back: done

    Renders a named view template with supplied variables.
    Returns HTML string. Content-type: text/html unless overridden.
    """
    template_name: str  = ""
    params:        list = field(default_factory=list)  # list of (key, value) tuples

@dataclass
class RespondAsStmt(Node):
    """respond as "text/html" -- sets content-type for current response"""
    content_type: str = "text/html"

@dataclass
class TitleDecl(Node):
    """title "Rates" -- page title for the render view shell"""
    text: str = ""

@dataclass
class DescribeDecl(Node):
    """describe "..." -- meta description for the render view shell"""
    text: str = ""

@dataclass
class ViewDecl(Node):
    """view header / title as text required / display ... / view: done"""
    name:   str  = ""
    params: list = field(default_factory=list)
    body:   list = field(default_factory=list)

@dataclass
class TemplateDecl(Node):
    """template welcome_email / guest_name as text / ... / template: done"""
    name:   str  = ""
    params: list = field(default_factory=list)
    body:   list = field(default_factory=list)

@dataclass
class MiotestDecl(Node):
    """miotest suite_name / it blocks / miotest: done"""
    name: str  = ""
    body: list = field(default_factory=list)

@dataclass
class ItBlock(Node):
    """it "description" mode unit / ... / it: done"""
    description: str  = ""
    modes:       list = field(default_factory=list)
    body:        list = field(default_factory=list)

@dataclass
class MioCookieSet(Node):
    """miocookie.set "name" / value "v" / expires in 30 days / miocookie: done

    Block-form options are captured onto these fields by the transformer (they were formerly
    dropped). None means "not specified" -- the interpreter/server apply the safe default
    (http_only on, same_site Lax, secure by the server's scheme default)."""
    name:            str  = ""
    inline_value:    Any  = None   # inline form: miocookie.set "name" to value
    value:           Any  = None   # block-form `value` clause
    secure:          Any  = None   # True/False explicit; None -> server scheme default
    http_only:       Any  = None   # True explicit; None -> default on
    same_site:       Any  = None   # "strict"/"lax"/"none"; None -> default Lax
    expires_seconds: Any  = None   # expires in N units, resolved to seconds
    domain:          Any  = None
    path:            Any  = None
    body:            list = field(default_factory=list)

@dataclass
class MioCookieGet(Node):
    """miocookie.get "name" / default "fallback" """
    name:    str = ""
    default: Any = None

@dataclass
class MioCookieDelete(Node):
    """miocookie.delete "name" """
    name: str = ""

@dataclass
class MioCookieExists(Node):
    """miocookie.exists "name" """
    name: str = ""

@dataclass
class MioLogStmt(Node):
    """miolog.info/warn/error/alert/metric message"""
    level: str = "info"
    value: Any = None

@dataclass
class MioCacheStmt(Node):
    """miocache.get/set/delete/flush/exists key value"""
    op:     str  = "get"
    key:    str  = ""
    values: list = field(default_factory=list)
    alias:  str  = ""   # miocache.get "k" as NAME -> bind result to NAME

@dataclass
class NotBuiltService(Node):
    """A dedicated mio* service statement/decl that parses but is not executable in
    this build. Instead of the transformer silently returning None (which dropped the
    statement from the AST and let it no-op), these rules now emit this node so the
    interpreter fails loud at the point of use, consistently and auditably.
    tier: 'plain' (not built yet, tracked) | 'commercial' (licensed, open compiler
    refuses)."""
    service: str = ""
    method:  str = ""
    tier:    str = "plain"
    line:    int = 0

@dataclass
class ReplaceBlock(Node):
    """replace in text_var / "old" with "new" / replace: done"""
    target: str  = ""
    entries: list = field(default_factory=list)  # list of (old, new) tuples
    alias:  str  = ""   # inline `replace "old" with "new" in var as NAME` -> result to NAME

@dataclass
class ExtractStmt(Node):
    """extract from member.email using pattern.Email as local_part"""
    source: Any = None
    pattern: str = ""
    alias:   str = ""

@dataclass
class PrependStmt(Node):
    """prepend "TXN-" to reference_number -- works on strings and lists"""
    value:  Any = None
    target: str = ""

@dataclass
class AppendStmt(Node):
    """append ".pdf" to filename -- works on strings and lists.
    strict_list=True marks the node as coming from the `add` verb, which is LISTS ONLY:
    add to a non-list fails loud (use append/prepend for strings)."""
    value:  Any = None
    target: str = ""
    strict_list: bool = False

@dataclass 
class IgnoreStmt(Node):
    """ignore ../journey.mho / except db / ignore: done"""
    target:    str  = ""
    exceptions: list = field(default_factory=list)

# Additional backward compat aliases for old transformer
AiExplainStmt   = AiExplainBlock        # renamed Block in v3.8
ListenBlock     = ListenBlock           # same name -- no alias needed  
MioconnectDecl  = MioconnectDecl        # same
# NOTE: `SinceExpr` (defined above) is its OWN class, deliberately NOT merged into TimeExpr.
# `since <anchor>` is a RANGE ("from a point until now") per the timespan spec, semantically
# distinct from a point-in-time TimeExpr. A prior `SinceExpr = TimeExpr` alias made
# `since last_month` crash: time_anchor builds SinceExpr(anchor=...) and TimeExpr has no
# `anchor` field. The alias is removed; the distinct class stands.


@dataclass
class MapAliasEntry:
    left: str = ""
    right: str = ""
    arrow: str = "->"
    modifiers: list = field(default_factory=list)

@dataclass
class MapDecl:
    name: str = ""
    entries: list = field(default_factory=list)
    source: object = None
    through: str = ""
    alias: str = ""
