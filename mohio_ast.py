# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
# mohio_ast.py
# Mohio AST Node Types -- v3.8
# Version: see mohio_version.VERSION | August 2026 | Particular LLC
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

class SourceLine(int):
    """A line number that also knows its FILE. Statements merged in from an included file carry
    their own file's line numbers (HC-137); as a plain int, a finding on one was printed against the
    including file's line of the same number. Being an int, it compares, sorts and serializes as
    the number it is, so every scan passes it on unchanged and the report reads `.file`."""

    def __new__(cls, value, file):
        obj = super().__new__(cls, value)
        obj.file = file
        return obj

    def __reduce__(self):
        return (SourceLine, (int(self), self.file))


def tag_source_file(nodes, path):
    """Give every node line under `nodes` the file it was written in, unless a deeper include
    already gave it its own."""
    from dataclasses import fields, is_dataclass
    stack, seen = [nodes], set()
    while stack:
        n = stack.pop()
        if isinstance(n, (list, tuple)):
            stack.extend(n)
            continue
        if not is_dataclass(n) or id(n) in seen:
            continue
        seen.add(id(n))
        ln = getattr(n, 'line', None)
        if isinstance(ln, int) and not isinstance(ln, (bool, SourceLine)) and ln > 0:
            try:
                n.line = SourceLine(ln, path)
            except (AttributeError, TypeError):  # SILENT-OK: a frozen node keeps its plain line
                pass
        for f in fields(n):
            v = getattr(n, f.name, None)
            if isinstance(v, (list, tuple)) or is_dataclass(v):
                stack.append(v)


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
class MapMount(Node):
    """route:  about.mho to "/about_us"  -- mount a file at a path."""
    source: str = ""          # the file: `about.mho`, or "blog/post.mho" when quoted
    path:   str = ""          # the URL it answers at, always a quoted string

@dataclass
class MapRedirect(Node):
    """route as redirect:  "/old" to "/new" as permanent."""
    source: str = ""
    target: str = ""
    kind:   str = ""          # "permanent" | "temporary"
    status: int = 0           # 301 | 302 -- what actually goes on the wire

@dataclass
class MapStage(Node):
    """One stage of a data pipeline: `<qualifier>.<path>` with optional formatting.

    `qualifier` declares the stage's KIND (`db`, `json`, `feed`, `legacy`, ...); `path` is the
    rest of the dotted chain, at whatever depth the real source has. Deliberately kept as text
    and NOT resolved: `map` is structure-aware, not content-aware -- checking that the path
    exists or that types line up is the paid `miomap` engine's job."""
    qualifier: str = ""
    path:      str = ""          # the dotted remainder, e.g. "dbs1.col2"
    fmt:       str = ""          # `as dec.4` / `as.usd` / `in.EUR`, carried opaquely

    @property
    def full(self) -> str:
        return f"{self.qualifier}.{self.path}" if self.qualifier else self.path

    @property
    def is_transform(self) -> bool:
        """A BARE stage names a shape the value passes THROUGH, not a place it comes from or
        goes to. No qualifier is the tell -- a transform is not a location."""
        return not self.qualifier


@dataclass
class MapPipeline(Node):
    """A CHAIN of stages joined by directional hops -- a flow graph, not a one-way list.

    `stages` holds N stages; `hops` holds the N-1 directions between them, so hop i connects
    stage i to stage i+1. Direction is PER HOP, which is what lets one chain flow both ways
    (`a -> b <-> c <- d`)."""
    stages: list = field(default_factory=list)   # MapStage
    hops:   list = field(default_factory=list)   # "forward" | "bidirectional" | "reverse"

@dataclass
class FlowStmt(Node):
    """flow paydata  |  flow paydata.stripe  -- DRIVE a data map (prototyped 2026-08-25).

    `map_name` is the map; `chain` is a chain selector (the source stage's qualifier) or "" for
    every chain in the map. `handlers` is the STATUS channel -- `on.success` / `on.failure`, the
    same two-stage outcome every other verb block uses, with on.failure naming the broken hop.
    There is deliberately no `flow as x`: the END value is `check flow`, and any other position
    is `grab` or `walk`."""
    map_name: str = ""
    chain:    str = ""
    handlers: list = field(default_factory=list)


@dataclass
class CheckFlowStmt(Node):
    """check flow paydata [as NAME] -- run it and report the ENDPOINT value.

    The endpoint is the stage after the last forward hop. Any OTHER position is `grab` or
    `walk`, so `flow` itself is never overloaded with positional access."""
    map_name: str = ""
    chain:    str = ""
    alias:    str = ""


@dataclass
class GrabStageStmt(Node):
    """grab first|last stage from <map>.<chain>  |  grab stage at N|"name" from ...

    `first` and `last` are the two SEMANTIC ends, found by the ARROWS rather than array
    position: the first stage is where data ENTERS, the last is where it comes to rest."""
    map_name:    str = ""
    chain:       str = ""
    which:       str = "first"       # first | last | at
    alias:       str = ""
    at_position: int = 0
    at_name:     str = ""


@dataclass
class WalkStmt(Node):
    """walk paydata / at each stage: report / walk: done -- INSPECT a data map.

    The opposite of `flow`: flow executes and gets out of the way, walk stops at every stage so
    the program can look at what is there and act on it. `actions` is what runs at each stage --
    'report', 'log', or a statement body with the stage bound."""
    map_name: str = ""
    chain:    str = ""
    actions:  list = field(default_factory=list)   # ('report'|'log'|'do', body)


@dataclass
class MapSectionsDecl(Node):
    """map [name] / route | route as redirect | data / map: done  (T1-MAP-EXTRACTION).

    The FREE, translatable mapping construct. Classifiers are explicit because an entry cannot
    identify itself: `user_name to full_name` is simultaneously a valid route and a valid data
    map. Sections are scoped -- `route` uses `to`, `data` uses arrows, and the grammar refuses
    the crossover rather than reinterpreting it."""
    name:      str  = ""
    mounts:    list = field(default_factory=list)   # MapMount
    redirects: list = field(default_factory=list)   # MapRedirect
    data:      list = field(default_factory=list)   # MapPipeline
    responses: list = field(default_factory=list)   # MapStatusResponse


@dataclass
class MapStatusResponse(Node):
    """`[404] /notfound` or `[404] "gone"` -- what an app answers for a status.

    `kind` is 'page' or 'message', decided by CONTENT rather than by a keyword: a path serves a
    page, a string is a body. That is the same disambiguation the rest of the language uses, so
    there is nothing extra to learn and nothing to declare twice.
    """
    status: int = 0
    kind:   str = ""      # 'page' | 'message'
    target: str = ""


@dataclass
class FrameworkDecl(Node):
    """framework: web-app  --  the app TARGET type (T1-FRAMEWORK-FOUNDATION).

    Orthogonal to SectorDecl: sector enforces RULES, framework scaffolds STRUCTURE. Carries the
    dotted value verbatim (`mobile.ios`, `api.rest`); `base` splits off the leading segment,
    which is what the serve layer dispatches on -- a sub-profile refines a framework, it never
    changes which framework you are in."""
    framework: str = ""

    @property
    def base(self) -> str:
        return (self.framework or "").split(".", 1)[0]

@dataclass
class ConnectDecl(Node):
    """connect db as postgres from env.DATABASE_URL"""
    name:   str = ""      # alias (db)
    driver: str = ""      # postgres, redis, etc.
    source: Any = None    # value_expr -- usually env ref
    handlers: list = field(default_factory=list)   # T1-RUN3: OnFailure/OnSuccess, additive
    # readonly / writeonly / readwrite. The grammar has parsed `conn_access` since it was
    # designed and locked (2026-06-08) and there was NO FIELD HERE to put it in, so every
    # access mode ever written was discarded at the transformer: a `save` to a `readonly`
    # connection wrote happily and `mio check` reported no errors (measured 2026-09-02).
    access: str = ""
    default: Optional[str] = None   # `default "app.db"`: the address when the variable is unset

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
    """require role "admin" or "screener"  |  require person"""
    roles: list = field(default_factory=list)   # list[str]
    person: bool = False   # `require person`: anyone signed in, no role named

@dataclass
class VerifySignatureDecl(Node):
    """verify signature using env.X [header "H"] [within N minutes]

    A webhook handler clause. At request time the interpreter runs it FIRST -- before shape
    validation and before any body statement -- computing HMAC-SHA256 over the RAW request bytes with
    the secret from env, comparing it constant-time against the signature header, and failing closed.
    `secret_env` is the ENV VAR NAME only (e.g. STRIPE_WEBHOOK_SECRET); the secret VALUE is never
    stored on the node, never logged and never audited. A string-literal secret is refused at
    transform time (the value must be `env.X`). `header` names a non-default signature header (default
    X-Signature). `within_seconds`, when set, adds a freshness bound: the signed material becomes
    `timestamp.rawbody`, the timestamp arrives in a header (default X-Timestamp), and a timestamp too
    far in the past OR the future is rejected. Freshness narrows the window; replay defence is the
    recorded identity of every accepted delivery (T1-WEBHOOK-REPLAY-PROTECTION), and the sender's own
    event id as that identity is T1-WEBHOOK-IDEMPOTENCY."""
    secret_env: str = ""          # the env var NAME, never the value
    header: str = None            # signature header name; None -> X-Signature default
    ts_header: str = None         # timestamp header name; None -> X-Timestamp default
    within_seconds: object = None # freshness tolerance in seconds; None -> no freshness check

@dataclass
class GrantRoleDecl(Node):
    """grant role "admin"  /  grant role user.role -- establish a SERVER-verified
    role in the current session. The value_expr resolves at runtime to the role
    name(s); the grant is stored on the session root and marked verified, and is
    the ONLY source `require role` trusts (client `_roles` is never consulted).

    `person` (T1-DURABLE-PRINCIPAL) is the OPTIONAL `for person <who>` value_expr: the human the
    application asserts at this auth boundary. Set once per session, read-only thereafter as
    session.person, survives rotation, stamped automatically on writes and audit records."""
    value:  object = None   # role value_expr node, resolved at runtime
    person: object = None   # `for person <who>` value_expr node, or None

@dataclass
class EndSessionStmt(Node):
    """end session -- logout. Ends the whole current session: roles dropped, session contents
    gone, and the session id invalidated via the shared primitive so the old cookie can never be
    resurrected. The inverse of GrantRoleDecl; carries no value (it ends the current session)."""
    pass

@dataclass
class EndPersonSessionsStmt(Node):
    """end every session for person <who> -- end every session that person holds, in every
    session store, each ending audited. An administrative act on someone else's sessions, so it is
    deliberately NOT an EndSessionStmt: the auth-boundary exemption a logout earns does not reach
    it, and a handler running it needs an authority check like any other."""
    person: object = None   # the value_expr naming the person

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
    """release var | release.now var value (retired) | release.lock var value (retired)"""
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
    # PHASE 2 of the recovered shape model. `users as table` opens a REAL nested field scope,
    # so `db.users.email` and `db.orders.email` are two different fields that happen to share a
    # name. A flat list with table labels IS the bug this exists to prevent.
    # ADDITIVE: a shape with no table line leaves this empty and keeps every field in `fields`,
    # which is byte-identical to the MVP behaviour every existing program relies on.
    tables:        dict = field(default_factory=dict)   # {table_name: ShapeTable}

    def every_field(self):
        """Every field this shape declares, loose or table-owned, in source order.

        THE HIERARCHY SPLIT FIELDS IN TWO AND THAT IS A SILENT DROP UNTIL EVERY CONSUMER KNOWS.
        Found by running, 2026-09-04, the same hour the hierarchy landed: a `never store` field
        declared inside a `users as table` scope was PERSISTED, while the identical field
        declared loose in the same shape refused the write. Every governance registration --
        `never store`, `[pii]`/`[phi]`/`[pci]`, `sec.encrypt`, `purpose` -- walked `fields`, and
        the hierarchy had quietly moved half of them out of it.

        So a shape has two readings and both are legitimate:
          * `fields` / `tables` -- the STRUCTURE. Which table owns what, which is the whole
            point of Phase 2 and the only thing keeping `db.users.email` apart from
            `db.orders.email`.
          * `every_field()` -- the GOVERNANCE view. A rule that attaches to a field attaches to
            it wherever it was declared, and a caller asking "which fields carry a tag" must
            never have to know about scopes to get a complete answer.

        Anything that answers a question about FIELDS uses this; anything that answers a
        question about TABLES uses `tables`.
        """
        out = list(self.fields or [])
        for tbl in (self.tables or {}).values():
            out.extend(getattr(tbl, 'fields', None) or [])
        return out


@dataclass
class ClientListener(Node):
    """MioScript: listen for <event> on "<selector>" ... listen: done.
    Mohio words that compile to JS and run in the browser."""
    event:       str  = ""         # input / click / blur / change / ...
    selector:    str  = ""         # CSS selector the listener binds to
    body:        list = field(default_factory=list)   # list of client statements
    debounce_ms: int  = 0          # on.pause <duration> — 0 means fire immediately


@dataclass
class ClientIdle(Node):
    """MioScript: on.idle <duration> ... on.idle: done.

    Whole-page inactivity, which is a different question from ClientListener's on.pause.
    on.pause waits for ONE selector's ONE event to stop arriving. This waits for the person
    to stop being there at all, so it watches the document for every sign of a person and
    keeps ONE timer between them: any of them restarts it. That is what makes it session
    timeout rather than a debounce.
    """
    ms:   int  = 0                 # the quiet period before the body runs
    body: list = field(default_factory=list)   # list of client statements


@dataclass
class ClientEvery(Node):
    """every <duration> as <name> ... every: done — named recurring poll (setInterval).
    The name is what a later `stop <name>` cancels; two `every` blocks with the same name
    on one page is a mistake (one would silently replace the other's running timer), so it
    is refused at compile time rather than left to collide at runtime."""
    ms:   int  = 0
    name: str  = ""
    body: list = field(default_factory=list)


@dataclass
class ClientStopInterval(Node):
    """stop <name> — cancels the `every <name>` interval it names (clearInterval). A name
    that was never declared by an `every` block refuses at compile time, the same as any
    other reference to an undeclared page construct."""
    name: str = ""


@dataclass
class ClientPut(Node):
    """put <the value | "literal"> into "<selector>" """
    source_kind: str = "literal"   # "literal" or "the"
    source:      str = ""          # the literal text, or the datum name (value/key)
    target:      str = ""          # CSS selector to write into

@dataclass
class ClientInject(Node):
    """inject result into "<selector>" -- place a Mohio-rendered FRAGMENT as markup.

    Only `result` (a `send` response) may be injected, and only when it carried the fragment marker
    the server set on `render fragment`. Anything else -- a value, a field, a browser-built string --
    is not markup and is refused at transform, never silently escaped. The runtime check of the marker
    is what makes this safe: data never becomes markup."""
    target: str = ""               # CSS selector to inject the fragment into

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
class ClientAlert(Node):
    """alert "message" — a real, blocking browser alert() dialog. Reuses the server-side
    `alert` keyword (already a terminal for miolog.alert); this is the client statement form.
    Unlike notify (a dismissable toast), alert halts the page until the person closes it, so
    it is for something that must be seen before anything else continues."""
    source_kind: str = "literal"
    source:      str = ""

@dataclass
class ClientCopy(Node):
    """copy #element or a built value — writes to the OS clipboard via navigator.clipboard.
    source_kind 'elem' reads the element's displayed value/text first; the other kinds are
    the same literal/result/the/marked vocabulary every other client value accepts. A denied
    or unsupported clipboard write alerts rather than failing silently."""
    source_kind: str = "literal"
    source:      str = ""

@dataclass
class ClientShare(Node):
    """Three forms (ruled by Ron, 2026-10-03), each composing with `on DESTINATION`:
    `share` with no value shares the CURRENT PAGE (source_kind='page', source unused --
    _emit_stmt reads window.location.href); `share #element` shares that ELEMENT'S OWN LINK
    (source_kind='elem', source is the selector -- its own .href, or a link found inside it,
    or the current page as a last resort); `share VALUE` shares a literal/built value
    (source_kind 'literal'/'result'/'the', the pre-existing form). Bare opens the device's
    native share sheet (navigator.share); `on DESTINATION` instead opens that site's own share
    page (a new tab/window), built from the URL template in
    mohio_data/share_destinations.json -- destination is "" for the bare-destination form; an
    unrecognized destination refuses at compile time, the same as an unrecognized client
    value. A failed or cancelled share sheet does not alert -- declining to share is a normal
    outcome, not an error to report."""
    source_kind:  str = "literal"
    source:       str = ""
    destination:  str = ""

@dataclass
class ClientConfirm(Node):
    """confirm "message" — a yes/no gate. On no (Cancel), the handler stops right there;
    every statement after it in this listener is skipped. On yes, execution falls through."""
    source_kind: str = "literal"
    source:      str = ""

@dataclass
class ClientHold(Node):
    """hold <name> = <"literal" | datum | result.field> — FREEZE a page-level value
    (Ron ruling, 2026-10-04 -- the client mirror of the server's hold). Reading the name
    anywhere recalls it; a second `hold` for the same name, or an assignment to it, refuses
    until `release`. For a value that should keep recapturing (the last keystroke, the last
    selection), use the bare `ClientAssign` form (`name value`) instead -- that is what `hold`
    meant before this ruling, and the one it is retired from."""
    name:        str = ""
    source_kind: str = "literal"
    source:      str = ""

@dataclass
class ClientAssign(Node):
    """<name> <value> — a FLUID page value, no freeze: plain read/recapture, the client's
    own mirror of the server's bare `name value`. Always reassignable, unlike ClientHold."""
    name:        str = ""
    source_kind: str = "literal"
    source:      str = ""

@dataclass
class ClientRelease(Node):
    """release <name> — unfreezes a client hold, so it can be held (or assigned) again.
    Refused at compile time if <name> was never held anywhere on the page."""
    name: str = ""

@dataclass
class ClientClearVar(Node):
    """clear <name> — empties a HELD client value in place; it stays held. A different rule
    from `clear #selector` (ClientDomOp op='clear'), which empties a DOM element and is
    unaffected by this. Refused at compile time if <name> was never held."""
    name: str = ""

@dataclass
class ClientForgetVar(Node):
    """forget <name> — removes a held client value entirely. Refused at compile time if
    <name> was never held."""
    name: str = ""

@dataclass
class ClientRename(Node):
    """rename <name> to <target> — renames a held client value. Refused at compile time if
    <name> was never held."""
    name:   str = ""
    target: str = ""

@dataclass
class ClientReplaceVar(Node):
    """replace <name> with <value> — whole-value replace of a HELD client value, in place;
    the client mirror of the server's VarStateStmt op='replace'. Refused at compile time if
    <name> was never held."""
    name:        str = ""
    source_kind: str = "literal"
    source:      str = ""

@dataclass
class ClientReplaceIn(Node):
    """replace "old" with <value> in <target> (as <alias>)? — string substitution, the client
    mirror of the server's ReplaceBlock. With `as <alias>` it is a pure expression (reads
    <target>, writes only <alias>, source untouched) and works on ANY client value, held or
    fluid. Without `as <alias>` it mutates <target> in place, and that form is refused at
    compile time unless <target> was held -- the same held-only split as the server side."""
    old:         str = ""
    source_kind: str = "literal"
    source:      str = ""
    target:      str = ""
    alias:       str = ""

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
    # The ITEM type of a `list X` field (`tags as list int` -> "int"); type_name stays "list".
    element_type: Optional[str] = None
    dotted:     Optional[str] = None  # NEW v3.8 -- "status.allowed"
    modifiers:  list = field(default_factory=list)   # list[ShapeFieldModifier]
    # DECLARED AS PART OF A COMMA GROUP (`a, b, c as text`). The refuse-two-fields-on-one-line
    # guard reads this: several fields on one line is exactly what that guard exists to catch
    # when they are separated by SPACES, and exactly what a comma group is for when they are
    # not. Without the flag the two are identical by the time the guard sees them.
    grouped:    bool = False

@dataclass
class ShapeTable(Node):
    """`users as table` -- a table a shape governs, and the fields that BELONG to it.

    Its own node rather than a flag on ShapeField, because the whole point of Phase 2 is that
    a table owns a field SCOPE. A boolean on a flat field would be the label-on-a-flat-list
    reading the design rules out by name.
    """
    name:    str  = ""
    fields:  list = field(default_factory=list)   # list[ShapeField], owned by THIS table


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
    # Every `auth` line, in order: (kind, header name, value, second value, line). kind is "bearer",
    # "key", "basic" or "header"; the header name is "Authorization" for all but "header". Each line
    # is its own header on the request; the same header twice refuses at transform (it used to be one
    # slot, so the last line won and a bearer beside an Idempotency-Key was dropped without a word).
    auth:       list = field(default_factory=list)
    # Every `header "Name" value` line, in order: (header name, value, line). A header that is not a
    # credential (T1-HEADER-SURFACE-UNIFY), sent on every operation; the same header twice, here or
    # beside an `auth` line, refuses at transform as the `auth` lines do.
    headers:    list = field(default_factory=list)
    timeout:    Any  = None
    operations: list = field(default_factory=list)   # list[MioconnectOperation]
    retry:      Optional[int] = None    # T1-RUN3: retry N times on a genuine connectivity
                                         # failure (never on an HTTP error response -- that's
                                         # a legitimate result, not an operational break)
    handlers:   list = field(default_factory=list)   # T1-RUN3: OnFailure/OnSuccess, additive

@dataclass
class MioconnectOperation(Node):
    """operation charge path "/charges" sends sh.X returns sh.Y"""
    name:         str  = ""
    path:         str  = ""
    sends_shape:  Optional[str] = None
    returns_shape: Optional[str] = None
    method:       str  = "POST"
    timeout:      Any  = None   # seconds; overrides the connector's own timeout for this operation
    outcome_from: Optional[str] = None   # the read operation that looks a call up by its key (reconciler)
    outcome_line: int  = 0

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
    """name as text | name as sh.Transaction | ssn, dob as text [phi]

    `modifiers` carries what trailed the take group, one fresh copy per name so a group of
    three is indistinguishable from three separate takes. Almost everything that can appear
    there is refused by the transformer (a task is not a shape); a class tag is kept, because
    classification belongs to the value and a task param is where a value arrives.
    """
    name:         str  = ""
    type_name:    str  = ""
    is_required:  bool = False
    is_optional:  bool = False
    default:      Any  = None
    modifiers:    list = field(default_factory=list)


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
    """Journey-level config/metadata. kind is one of 'public'/'private'/'hidden'/
    'authorize'/'flow' (value: a path list) or 'serves' (value: 'single tenant'/
    'multiple tenants'). All four of public/private/hidden/authorize are read and
    enforced by _exec_JourneyDecl (2026-08-27, the locked T1-PAGE-CLASSIFICATION-MODEL):
    public: default, served+listed; private: served but unlisted (no runtime gate --
    inert until a listing feature exists to consume it); hidden: not served, 404
    (also implied by any `_`-prefixed path segment); authorize: login required,
    reusing require role's server-verified-session mechanism. flow is captured but not
    yet interpreted -- no documented source of truth for its intended runtime
    behavior exists in this repo. serves is captured but not yet enforced -- real
    tenant isolation needs a request-scoped tenant-identity primitive that does
    not exist anywhere in the language today; both are open questions, not silent
    no-ops (see the journey_body comment in mohio.lark)."""
    kind: str = ""
    value: object = None
    # `[important]` trailed the line: the journey's value wins over a page's own for it (`timezone`;
    # a classification line is a floor for the pages under it, checked in mio.py's cascade).
    important: bool = False

@dataclass
class SagaDecl(Node):               # NEW v3.8 (was stub)
    """saga fulfill_order ... saga: done"""
    name:  str  = ""
    steps: list = field(default_factory=list)   # list[StepBlock]
    # SAGA-LEVEL handlers, `on.failure` for now. A step could already declare one; the saga as
    # a whole could not, so there was no way to say what happens when it does not commit.
    body:  list = field(default_factory=list)

@dataclass
class StepBlock(Node):              # v3.8.2
    """step reserve_inventory ... step: done"""
    name:     str  = ""
    body:     list = field(default_factory=list)
    undo:     list = field(default_factory=list)   # compensate body (undo is alias)
    best_effort: bool = False
    handlers: list = field(default_factory=list)

# `PageDecl` REMOVED 2026-08-25. The `page` block was doing the framework's routing job in
# disguise; serve-by-default does that job now, so the construct had nothing left to do.
# The WORD stays reserved in the grammar. NOT removed and not related: the page
# CLASSIFICATION model (public:/private:/hidden:/authorize:), which is access control.
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


# ── Handler registry (Batch 16) ───────────────────────────────
# The handler node types that STAND ALONE. Each of these is a complete web handler on its own line:
# it listens for something (a shape POST, a GET at a path, a websocket connection, a stored-record
# change, a connector webhook) and registers itself as one independent route. None of them depends on
# a containing block. A `listen for` wrapper around them is retired -- see the lock comment at
# mohio.lark's block_stmt rule and _build_route_registry in mohio_interpreter. `top_handlers` is the
# ONE place that answers "which top-level statements are routes", so the router, the has-routes checks
# and the framework scanner cannot drift apart on the answer.
TOP_HANDLER_TYPES = (NewBlock, RequestInboundBlock, ConnectionBlock, ChangeBlock, FromConnectorBlock)


def top_handlers(statements):
    """The standalone handler statements in a statement list, in source order. Used for top-level
    program routing and for handlers written loose inside a journey body."""
    return [s for s in (statements or []) if isinstance(s, TOP_HANDLER_TYPES)]


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
    take_n:     Optional[int] = None   # `take N` -- walk at most N items

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
    alias:   Optional[str] = None                 # update ... as NAME -> bind rows-changed count

@dataclass
class UpdateWhereEmpty(Node):
    """`where site_id is empty` inside an update: the row must have no value there (no value, or
    empty text). Part of the update's own statement, so an expected-empty compare-and-set stays one
    write (MIOQL-EMPTY-UPDATE-01)."""
    field: str = ""

@dataclass
class RemoveBlock(Node):
    """remove from db.sessions match user_id to user.id"""
    source:    Any  = None
    condition: Any  = None
    match:     Any  = None   # MatchClause -- alias for condition
    handlers:  list = field(default_factory=list)
    alias:     Optional[str] = None   # remove ... as NAME -> bind rows-removed count

@dataclass
class RemoveAllBlock(Node):         # NEW v3.8
    """remove.all from db.temp_imports  (optional on.success / on.failure)"""
    source:   Any  = None
    handlers: list = field(default_factory=list)
    alias:    Optional[str] = None   # remove.all ... as NAME -> bind rows-cleared count

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
    """paginate by 25  |  paginate by request.page default 1"""
    count: Any = None
    default: Any = None   # the value a MISSING page takes (ruled 2026-09-22)

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
    inputs: list = field(default_factory=list)   # list[DottedName | WeighLiteral]

@dataclass
class WeighLiteral(Node):
    """A literal weighed as context (`weigh message.body, "returns: 30 days"`, RULED 2026-08-30).
    The model reads `literal`; the decision record keeps only its source line and the
    classification `literal`, never its text."""
    literal: Any = None

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
    # Coder-declared providers carrying their own endpoint + key, overriding a built-in of the same
    # name. [{name, address, address_type, key, key_type}]; address_type/key_type in
    # {'string','env','secret'} so the interpreter resolves an env/secret ref at run time.
    declared:  list = field(default_factory=list)

@dataclass
class AiConnectProvider(Node):
    provider:          str = ""
    quality_threshold: Any = None
    body:              list = field(default_factory=list)

@dataclass
class AiCreateStmt(Node):
    """ai.create image as banner / goal / style / size / ...  (KIND first, NAME last)"""
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
    realistic:   Optional[float] = None   # `realistic N`; 0.0 = fully synthetic, from the source's fields only

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
class MioScheduleClause(Node):
    """One body line of a schedule, with the word that said what it is.

    `every 5 minutes`, `in 5 minutes` and `for 5 minutes` are a cadence, a delay and a
    self-terminating burst, and all three arrived as an indistinguishable DurationExpr
    because their keywords are filtered out of the parse tree. The kind is carried here so
    they stop being the same thing.
    """
    kind:   str = ""
    value:  Any = None
    extras: list = field(default_factory=list)


@dataclass
class SignBlock(Node):              # NEW v3.8
    """sign url for cloud_storage / expires in 30 minutes / named download_url"""
    sign_type:  str  = "url"        # "url" | "upload url"
    for_target: Any  = None
    expires:    Any  = None
    named:      Optional[str] = None
    handlers:   list = field(default_factory=list)
    named_word: bool = False        # captured with the retired `named NAME` (refused at check)

@dataclass
class VerifyTokenStmt(Node):
    """verify token from request.header "Authorization"  |  verify token from link.token as grant"""
    source:  Any  = None
    header:  Optional[str] = None
    scope:   Optional[str] = None
    handlers: list = field(default_factory=list)
    as_name: Optional[str] = None

@dataclass
class TokenCreateBlock(Node):
    """mioauth.token.create as NAME / for X / scope "..." / expires in N unit / one time"""
    as_name:  Optional[str] = None
    subject:  Any  = None
    scope:    Any  = None
    expires:  Any  = None
    one_time: bool = False
    named:    Optional[str] = None      # the retired `named NAME` capture, refused at check
    uses:     Optional[int] = None      # `up to N times`; None means one time, the default
    token:    Any  = None               # not a create clause; kept so check can refuse it

@dataclass
class TokenRevokeBlock(Node):
    """mioauth.token.revoke / for X / scope "approve:*" -- every unused matching token"""
    subject:  Any  = None
    scope:    Any  = None
    expires:  Any  = None               # not a revoke clause; kept so check can refuse it
    one_time: bool = False
    named:    Optional[str] = None
    uses:     Optional[int] = None
    token:    Any  = None               # `token X`: revoke the one presented token

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
    injects: list = field(default_factory=list)   # (source DottedName, slot name), as on miomail.send

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
class InTimezoneExpr(Node):
    """payment.created in.timezone "Asia/Bahrain"  |  payment.created in.timezone customer.zone
    T1-DB-TYPED-STORAGE (RULED by Ron, 2026-10-05): display conversion, translating a stored
    (UTC) datetime into another zone for display. `zone` is either a literal zone-name string
    (zone_literal set, zone_value None) or a value_expr to evaluate at run (zone_value set,
    zone_literal None) -- e.g. a dotted reference to a `timezone`-typed field."""
    value:        Any = None
    zone_literal: Optional[str] = None
    zone_value:   Any = None

@dataclass
class StrippedExpr(Node):
    """`<record> stripped` -- the record without its [phi]/[pci]/[pii] fields (RULED by Ron), the one
    way to send a record holding protected data to an AI call or an outside call."""
    operand: Any = None


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
    overwrite: bool = False # move/copy `allow overwrite`: replace an existing destination

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
    template_name: str  = ""                            # `template NAME`: a declared template (T1-TEMPLATE)
    injects:  list = field(default_factory=list)       # (source DottedName, slot name) per `inject X into Y`

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
    """hash form.password as hashed using bcrypt; hash account.iban as token using sha256 with key secret.K"""
    value:     Any  = None
    alias:     Optional[str] = None
    algorithm: str  = "sha256"
    body:      list = field(default_factory=list)
    key:       Any  = None          # `with key <value>`: the digest is an HMAC under it

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
    """limits / max steps N / max tokens N / cost ceiling N / limits: done

    Shared by `ai.agent` (reasoning ceilings) and `journey` (the per-IP request backstop),
    because both are the same sentence: numeric ceilings for this thing. Reusing the block was
    the point -- a second way to declare a ceiling would be a second thing to learn.
    """
    max_steps:    int   = 0
    max_tokens:   int   = 0
    max_calls:    int   = 0
    cost_ceiling: float = 0.0
    timeout:      Any   = None
    # `max requests N per second` -- 0 means "not declared", so the runtime falls through to
    # the env override and then the 2000/sec default rather than reading 0 as "allow nothing".
    max_requests_per_second: int = 0

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
class RespondAsStmt(Node):
    """respond as "text/html" -- sets content-type for current response"""
    content_type: str = "text/html"

@dataclass
class IdempotentDecl(Node):
    """once per request.header "Idempotency-Key" keep for 24 hours (T1-ROUTE-IDEMPOTENCY).
    `source` is the dotted name the key is read from and `header` the header it names ("" when the
    source names no header, which check refuses). `keep_seconds` is None when no `keep for` was
    written; `keep_written` is the duration as written, for messages. `sector` names the sector that
    made a route idempotent with no line written (the run builds that declaration), "" for a line."""
    source:       Any = None
    header:       str = ""
    keep_seconds: Any = None
    keep_written: str = ""
    keep_unit:    str = ""
    keep_count:   Any = None
    sector:       str = ""

@dataclass
class NotIdempotentDecl(Node):
    """not idempotent reason "<why>" -- opts a `listen for` out of the idempotency a named sector gives a
    route that moves value or calls out (BRIEF sector-idempotency-and-diagnostics, Part A). `reason` is
    None when no `reason` was written, which check refuses."""
    reason: Any = None

@dataclass
class TitleDecl(Node):
    """title "Rates" -- page title for the render view shell"""
    text: str = ""

@dataclass
class DescribeDecl(Node):
    """describe "..." -- meta description for the render view shell"""
    text: str = ""

@dataclass
class ImageDecl(Node):
    """image "q3-preview.png" -- the social-preview image, declared beside title/describe.
    The runtime writes title/describe/image into every og:/twitter: social preview tag the
    render shell emits (_wrap_html_shell), not just <title>/<meta name="description">."""
    text: str = ""

@dataclass
class ViewDecl(Node):
    """view header / title as text required / display ... / view: done"""
    name:   str  = ""
    params: list = field(default_factory=list)
    body:   list = field(default_factory=list)

@dataclass
class TemplateDecl(Node):
    """template welcome_email / subject "..." / from "..." as "Name" / html "path" / template: done

    A named email or document, declared once and sent by name (T1-TEMPLATE). `subject`, `body` and the
    html/text FILES are text with {{ }} slots, filled at send from the injected values only. `params`
    holds declared slots as (name, type, mode, default node); mode is required, optional or default."""
    name:      str  = ""
    params:    list = field(default_factory=list)
    body:      list = field(default_factory=list)
    subject:   Any  = None    # raw text with {{ }}, rendered at send
    text_body: Any  = None    # inline `body "..."`, raw text rendered at send
    html_path: Any  = None    # `html "path"`, read at send, relative to the program
    text_path: Any  = None    # `text "path"`, read at send, relative to the program
    from_:     Any  = None    # value node, evaluated at send in the sender's scope
    from_name: Any  = None
    reply_to:  Any  = None

@dataclass
class MiotestDecl(Node):
    """miotest suite_name / it blocks / miotest: done"""
    name: str  = ""
    body: list = field(default_factory=list)

@dataclass
class ExpectStmt(Node):
    """expect total is 5 / expect fallback was used / ...

    `kind` names the comparison the source actually wrote, which the grammar aliases make
    knowable (the underscore terminals that distinguish them are filtered out of the children).
    A form this build does not evaluate arrives as kind "unsupported" carrying its source text,
    so the runner can REFUSE it rather than counting it as a pass. An assertion nobody
    implemented must never look like an assertion that held.
    """
    kind:   str = ""          # is / is_not / above / below / contains /
                              # fallback_used / human_review / unsupported
    target: Any = None        # the dotted name under test, where the form has one
    value:  Any = None        # the expected value, where the form has one
    source: str = ""          # the written form, for the unsupported refusal

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
    # `miocache.set "k" v for 10 minutes` -- the grammar has always accepted this and the
    # transformer used to drop it, so a declared expiry was silently ignored and the value
    # lived forever (Q66). None means no expiry was declared, which is different from 0.
    ttl_seconds: float = None

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

# WHAT A `mioai.<method>` CALL IS, said by the check and the run refusal alike (RULED by Ron
# 2026-10-09). "Retired" is kept for the spellings that really were retired (the dotted generative
# forms, replaced by `ai.create`). `mioai.extract` is a locked design (2026-06-12) that is not built
# yet, so calling it retired told the reader a design was abandoned. Any other method is not a
# mioai operation at all.
MIOAI_RETIRED_METHODS = ('text', 'image', 'audio', 'video', 'data', 'generate')
MIOAI_DESIGNED_METHODS = {
    'extract': ("reads a document or image (a PDF, a scan, a photo) into text",
                "Until it lands, get the text out by other means and give `ai.*` the text: a field "
                "the person types, or `miofile.read` for a stored plain-text file."),
}


def mioai_refusal(method):
    """The message for a `mioai.<method>` call that this build cannot run."""
    if method in MIOAI_DESIGNED_METHODS:
        what, today = MIOAI_DESIGNED_METHODS[method]
        return (f"mioai.{method} is designed but not built yet: it {what}, and it arrives with "
                f"mioai. {today}")
    if method in MIOAI_RETIRED_METHODS:
        return (f"mioai.{method} is retired. Use `ai.create` to generate text, data, an image, "
                f"audio, or video (e.g. `ai.create poster image`), or `ai.decide` for AI reasoning "
                f"-- both are wired.")
    return (f"mioai.{method} is not a mioai operation. To generate something use `ai.create` "
            f"(e.g. `ai.create poster image`); for AI reasoning use `ai.decide`.")


# TAGGED DATA ON MONGODB (ruling 2, Ron 2026-10-09): until T1-MONGO-AUDIT-TRAIL is built Mohio keeps
# no audit trail on MongoDB, so an app holding [phi], [pii] or [pci] data there refuses: at check when
# the connect line names the engine, and at startup when only the address does. One message for both.
def mongo_tagged_refusal(fields, line=None):
    shown = ', '.join(f"`{f}`" for f in sorted(fields)[:6]) + (', ...' if len(fields) > 6 else '')
    return ((f"Line {line}: " if line else "")
            + f"this app keeps tagged data ({shown}) on MongoDB, and Mohio does not keep an audit "
            f"trail on MongoDB yet (T1-MONGO-AUDIT-TRAIL), so every record of who saw or changed "
            f"that data would be lost.\n"
            f"    Until it is built, keep this app's data on an engine that keeps the trail: SQLite, "
            f"Postgres, MySQL or Oracle, for example\n"
            f"        connect db as postgres from env.DATABASE_URL")


def sqlite_protected_deploy_refusal(fields, frameworks, marker):
    """Protected data on SQLite on a deploy (T1-SQLITE-PROTECTED-DATA-TIERING): the one-file engine is
    the development tier for tagged data and for a sector, so a deployed app refuses to boot."""
    if fields:
        what = ("tagged data (" + ', '.join(f"`{f}`" for f in fields[:6])
                + (', ...' if len(fields) > 6 else '') + ")")
    else:
        what = "data under a sector (" + ', '.join(str(f) for f in frameworks[:4]) + ")"
    return (f"this app keeps {what} on SQLite, and {marker} is set, which the platform sets on a "
            f"deployed app. SQLite is one file with one writer at a time, so it is the development "
            f"tier for protected data; a deployed app keeps it on a server database.\n"
            f"    Point the connection at Postgres, MySQL or Oracle, for example\n"
            f"        connect db as postgres from env.DATABASE_URL")


def sqlite_protected_dev_notice(fields, frameworks=()):
    """The local side of the same boundary: a local `mio serve` on SQLite with tagged data or a sector
    (inform locally, enforce at deploy)."""
    if fields:
        what = ("tagged data (" + ', '.join(f"`{f}`" for f in fields[:6])
                + (', ...' if len(fields) > 6 else '') + ")")
    else:
        what = "data under a sector (" + ', '.join(str(f) for f in list(frameworks)[:4]) + ")"
    return (f"  [connect] SQLite holds {what} here. That is fine on this machine while you "
            f"build; deploy it on Postgres, MySQL or Oracle, where a deployed app with protected "
            f"data on SQLite refuses to start.")


def mongo_ai_audit_refusal(logs, line=None):
    """The same boundary for an `ai.audit to` line: on MongoDB the record it asks for has nowhere to go
    (measured 2026-10-09: an audited ai.decide answered 200 and wrote nothing), and a governed AI step
    never completes without its record, so the app would refuse every one of them."""
    shown = ', '.join(f"`{n}`" for n in sorted(logs)[:6]) + (', ...' if len(logs) > 6 else '')
    return ((f"Line {line}: " if line else "")
            + f"this app records AI steps with `ai.audit to` ({shown}) on MongoDB, and Mohio does not "
            f"keep an audit trail on MongoDB yet (T1-MONGO-AUDIT-TRAIL), so every one of those "
            f"records would be lost and each governed step would be refused.\n"
            f"    Until it is built, keep this app's data on an engine that keeps the trail: SQLite, "
            f"Postgres, MySQL or Oracle, for example\n"
            f"        connect db as postgres from env.DATABASE_URL")


def mongo_sector_audit_refusal(frameworks, grade, line=None, label=None):
    """The same boundary for a sector or compliance profile that requires an audit grade (RULED by
    Ron 2026-10-09): every audited action needs the trail MongoDB does not keep yet."""
    said = label or "this app's sector"
    return ((f"Line {line}: " if line else "")
            + f"{said} ({', '.join(frameworks)}) requires {grade}-grade audit storage, and Mohio "
            f"does not keep an audit trail on MongoDB yet (T1-MONGO-AUDIT-TRAIL), so every audited "
            f"action (a sign-in, a role check, a governed decision) would be refused.\n"
            f"    Until it is built, keep this app's data on an engine that keeps the trail: SQLite, "
            f"Postgres, MySQL or Oracle, for example\n"
            f"        connect db as postgres from env.DATABASE_URL")


# `when actor.is "yes"` (RULED by Ron 2026-10-09, from the Dispatch app's line 747): `is` dotted
# onto a name reads as a FIELD called is, so the condition is neither a comparison nor a value. With
# a value after it the parse fails a line later; without one it checks clean and stops at run. Both
# paths say this, at the condition's own line.
def when_is_field_message(line, owner, subject=None, value=None):
    """The refusal for a `when <owner>.is ...` condition. `subject` is the enclosing check's subject
    when it is known, `value` the value written after `is` when there was one."""
    subject = subject or f"{owner}.<field>"
    value = value or '"<value>"'
    return (f"Line {line}: `when {owner}.is` reads `is` as a field of `{owner}`, but `is` is a "
            f"comparison word, not a field, so this condition is neither a comparison nor a value.\n"
            f"    Inside `check {subject}`, compare with the value alone:  when {value}\n"
            f"    or write the whole comparison:  when {subject} is {value}")


def shape_name_start_message(line, name):
    """The refusal for a shape declared with a name that does not begin with a letter (RULED by Ron
    2026-10-10). Said by `mio check` for a leading `_` and by the syntax-error explainer for a leading
    `-`, which never parses, so both read the same."""
    rest = name.lstrip('_-') or 'Order'  # SILENT-OK: an example name in the message when the declared one has no letters
    rest = rest[0].upper() + rest[1:]
    return (f"Line {line}: the shape name `{name}` begins with `{name[0]}`. A shape name begins with "
            f"a letter, so that `sh.<name>` can always reach it.\n"
            f"    Rename it, for example:  shape {rest}\n"
            f"    and refer to it as `sh.{rest}`, reading its data in a handler as `{rest}.<field>`.")


# The type words a value can be mistakenly followed by (`code 5 as text`), and the cast that converts
# to each. `as.integer` and `as.date` do not exist, so a type with no cast gets none offered: the
# message only ever suggests a form that runs.
_CAST_FOR_TYPE = {'text': 'as.text', 'text.long': 'as.text', 'int': 'as.int', 'integer': 'as.int',
                  'dec': 'as.dec', 'decimal': 'as.dec', 'boolean': 'as.boolean', 'bool': 'as.boolean'}
_TYPE_AFTER_VALUE_WORDS = frozenset(_CAST_FOR_TYPE) | {
    'date', 'datetime', 'time', 'uuid', 'json', 'string', 'number', 'num',
    'usd', 'eur', 'gbp', 'jpy', 'chf', 'cad', 'aud', 'cny', 'hkd', 'sgd', 'inr', 'nzd', 'sek', 'nok',
    'mxn', 'brl', 'zar', 'krw', 'bhd', 'kwd', 'omr', 'jod', 'tnd', 'iqd', 'lyd'}


def type_after_value_message(lines, line):
    """`code 5 as text` (RULED by Ron 2026-10-11): a type written after a value, on source line
    `line` of `lines`. Returns the teaching message, or None when the line is not that mistake.

    One message for every way the line fails: a reserved type word (`text`, `int`, `json`) stops
    the parse, and the syntax-error explainer asks here; an unreserved one (`date`, `USD`) parses
    as a field named `as`, and the save, call and top-level refusals ask here. The type's source
    depends on where the line sits: a save's column, a call's take line, or the value itself."""
    import re as _re
    if not (line and 0 < line <= len(lines)):
        return None
    m = _re.match(r'(\s*)([A-Za-z_]\w*)\s+(\S.*?)\s+as\s+([A-Za-z_][\w.]*)\s*(?://.*)?$',
                  lines[line - 1])
    if not m:
        return None
    indent, name, value, typ = len(m.group(1)), m.group(2), m.group(3), m.group(4)
    base = 'dec' if _re.fullmatch(r'(?:dec|decimal)\.\d+', typ.lower()) else typ.lower()
    if base not in _TYPE_AFTER_VALUE_WORDS or name.lower() in ('connect', 'take'):
        return None
    opener, opener_words = '', []
    for prev in reversed(lines[:line - 1]):
        s = prev.strip()
        if s and not s.startswith("//") and len(prev) - len(prev.lstrip()) < indent:
            opener_words = s.split()
            opener = opener_words[0].lower()
            break
    cast = _CAST_FOR_TYPE.get(base)
    if cast == 'as.dec' and base != typ.lower():
        cast = 'as.dec.' + typ.split('.', 1)[1]
    if opener in ('save', 'update', 'upsert', 'save.all'):
        article = 'an' if opener in ('update', 'upsert') else 'a'
        where = (f"In {article} {opener}, a field's type comes from its column, or from the value "
                 f"itself when the column is new, so there is nothing to declare on this line.")
    elif opener == 'call':
        # Quote the task's own take line when this file declares it; the type on the mistaken
        # line is not necessarily the one the task takes.
        take = None
        task = opener_words[1] if len(opener_words) > 1 else None
        in_task = False
        for src in lines:
            s = src.strip()
            if s.split()[:2] == ['task', task]:
                in_task = True
            elif in_task and s.startswith('task:'):
                break
            elif in_task and s.startswith('take ') and _re.search(r'\b%s\b' % _re.escape(name), s):
                take = s
                break
        where = (f"In a call, a value's type comes from the task's take line"
                 + (f" ({take})" if take else "") + ", so there is nothing to declare here.")
    else:
        where = (f"A value's type comes from the value itself. To declare an empty typed name, "
                 f"write `{name} as {typ}` on its own line.")
    msg = (f"`{name} {value} as {typ}` puts a type after the value. {where}\n"
           f"    Write the value alone:\n        {name} {value}")
    if cast:
        msg += f"\n    To convert the value, use the cast:\n        {name} {value} {cast}"
    return msg


# THE ACCEPTED ROUNDING FORMS, said by every refusal of a rounding form that is not one of them
# (RULED by Ron, 2026-10-06), so no unsupported spelling ends at a generic parse error or a
# no-handler message.
ROUNDING_FORMS = (
    "Rounding is written after the value: `price round.to 2` rounds to two places, ties away "
    "from zero; `price round.up 2` rounds away from zero and `price round.down 2` toward zero. "
    "`round.up` and `round.down` with no number round to a whole number. The number of places "
    "is a whole number, 0 or more.")
