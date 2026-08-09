# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
mohio_reachability.py — compile-time unreachable-code detection for `mio check`.

Within a single sequential statement list, any statement that follows an
UNCONDITIONAL hard return (`give back` / `halt` with no trailing if/unless
qualifier) can never execute. This pass flags that as a compile warning.

False-positive-free by design:

  * Only an UNCONDITIONAL give back / halt counts. A trailing `if`/`unless`
    qualifier means execution may continue past it, so it is NOT a hard return.

  * The check is scoped to ONE statement list at a time. A `give back` that is
    the LAST statement of a when/otherwise branch body produces no warning, and
    any code AFTER that branch (which lives in the parent list, a different list)
    is correctly still considered reachable. Only sibling statements in the same
    list, sitting after the hard return, are flagged.

  * `give back` / `halt` only ever appear as direct elements of real statement
    sequences (handler bodies, block bodies, branch bodies). They are never
    direct elements of non-sequential lists like `handlers`, `fields`, `roles`,
    or hold `items` — so scanning every list field is safe.

This is intentionally conservative: it under-warns (e.g. it will not flag code
after a check-block whose every branch returns) rather than risk a false alarm,
because a noisy check erodes trust faster than a missed one.
"""


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
#   This file is scan_*()
#   LAYER 3 of 3 -- THE WHOLE PROGRAM.
#
#   Rules that need the entire file (is that task declared? that type? that connector?).
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

from dataclasses import fields, is_dataclass

from mohio_ast import GiveBackStmt, HaltStmt
from mohio_transformer import CompileWarning, CompileError

_HARD_RETURN = (GiveBackStmt, HaltStmt)


def _is_unconditional_return(node):
    """True only for a `give back` / `halt` with no trailing if/unless qualifier."""
    return (isinstance(node, _HARD_RETURN)
            and getattr(node, "qualifier", None) is None)


def scan_unreachable(program):
    """
    Walk the AST and return a list[CompileWarning] for statements that sit after
    an unconditional hard return in the same statement list. At most one warning
    per offending list (reported at the first dead statement).
    """
    warnings = []
    seen = set()  # guard against shared nodes / accidental cycles

    def _scan_list(seq):
        for i in range(len(seq) - 1):
            stmt = seq[i]
            if _is_unconditional_return(stmt):
                dead = seq[i + 1]
                verb = "give back" if isinstance(stmt, GiveBackStmt) else "halt"
                ret_line = getattr(stmt, "line", 0)
                dead_line = getattr(dead, "line", 0) or ret_line
                warnings.append(CompileWarning(
                    f"unreachable statement after `{verb}` "
                    f"(the `{verb}` on line {ret_line} is a hard return, so "
                    f"nothing after it in this block can run).",
                    dead_line,
                    f"Move this above the `{verb}`, or make the `{verb}` "
                    f"conditional with a trailing `if`/`unless`.",
                ))
                break  # one warning per list is enough

    def visit(node):
        if node is None or not is_dataclass(node) or id(node) in seen:
            return
        seen.add(id(node))
        for f in fields(node):
            val = getattr(node, f.name, None)
            if isinstance(val, list):
                _scan_list(val)
                for item in val:
                    visit(item)
            elif is_dataclass(val):
                visit(val)

    visit(program)
    return warnings


def scan_unwired(program):
    """Walk the AST and return a list[CompileWarning] for constructs that parsed
    and validated but transformed to a raw Tree -- i.e. they have no transformer
    and therefore no executor, so they FAIL LOUD at run with 'no executor'.

    These are designed-but-unwired features (e.g. mioconnect). Warning at check
    closes the check/run gap: instead of passing `mio check` clean and then dying
    when actually served, the gap surfaces at check time. Per design, an unwired
    construct is a WARNING (you may be scaffolding around a planned feature), not
    a hard error -- genuinely invalid input is caught earlier as a parse error.
    """
    from lark import Tree
    warnings = []
    seen_ids = set()
    seen_reports = set()

    def visit(node):
        if node is None or id(node) in seen_ids:
            return
        seen_ids.add(id(node))
        if isinstance(node, Tree):
            # Condition subtrees (`wc_*` where-conditions, `cond_*` when-conditions)
            # are intentionally left as raw Trees and evaluated in place by the
            # check / find / when / unless condition evaluator -- they are never
            # dispatched as statements, so the "no executor" premise does not apply.
            # A genuinely unsupported condition still fails loud at runtime in that
            # evaluator; warning here just cries wolf on every working comparison.
            if node.data.startswith('wc_') or node.data.startswith('cond_'):
                return
            line = 0
            meta = getattr(node, "meta", None)
            if meta is not None and not getattr(meta, "empty", True):
                line = getattr(meta, "line", 0) or 0
            key = (node.data, line)
            if key not in seen_reports:
                seen_reports.add(key)
                warnings.append(CompileWarning(
                    f"`{node.data}` parsed and validated, but is not executable in "
                    f"this build -- it has no interpreter wiring, so it would fail "
                    f"at run with 'no executor'.",
                    line,
                    "This construct is recognized by the grammar but not yet wired "
                    "in the interpreter. If you are scaffolding around a planned "
                    "feature this is expected; it will not run yet.",
                    "not_executable",
                ))
            return  # do not recurse into the raw tree's children
        if isinstance(node, (list, tuple)):
            for item in node:
                visit(item)
            return
        if is_dataclass(node):
            for f in fields(node):
                visit(getattr(node, f.name, None))

    visit(program)
    return warnings


def scan_orphan_it(program):
    """`it` only gets a value inside a `then` pipeline (the running result of the
    previous step). If a program has NO chain at all, any `it` reference is orphaned
    and will fail loud at run. Surface it at `mio check` with direction.

    Conservative and false-positive-free: it fires ONLY when there is no ThenChain
    anywhere in the program, so a valid `give back it` after a chain is never flagged.
    (The runtime guard is the complete catch; this is the early check-time signal.)"""
    from lark import Tree
    from dataclasses import is_dataclass, fields
    has_chain = [False]
    it_refs = []
    seen = set()

    def visit(node):
        if node is None or id(node) in seen:
            return
        seen.add(id(node))
        tn = type(node).__name__
        if tn == 'ThenChain':
            has_chain[0] = True
        if tn == 'DottedName' and getattr(node, 'parts', None) == ['it']:
            it_refs.append(node)
        if isinstance(node, Tree):
            for c in node.children:
                visit(c)
        elif isinstance(node, (list, tuple)):
            for item in node:
                visit(item)
        elif is_dataclass(node):
            for f in fields(node):
                visit(getattr(node, f.name, None))

    visit(program)
    warnings = []
    if not has_chain[0]:
        reported = set()
        for n in it_refs:
            line = getattr(n, 'line', 0) or 0
            if line in reported:
                continue
            reported.add(line)
            warnings.append(CompileError(
                "`it` has no value here -- it refers to the result of the step right "
                "before it, but this program has no `then` pipeline to produce one.",
                line,
                "Start a chain (a head value followed by `then ...`), or use a named "
                "variable instead of `it`.",
                "orphan_it",
            ))
    return warnings


# Action verbs worth typo-guarding. Length >= 4 only: at <= 3 chars, edit-distance-1
# collides with too many ordinary words (set/bet/let, get/jet/vet), which would turn
# a help into noise. Curated rather than auto-extracted from the grammar so the list
# stays high-signal.
_TYPO_VERBS = {
    "show", "save", "find", "hold", "check", "make", "create", "give", "render",
    "fetch", "grab", "update", "remove", "upsert", "retrieve", "connect", "listen",
    "route", "sanitize", "validate", "redirect", "forward", "include", "raise",
    "repeat", "while", "consider", "otherwise", "transform", "cache", "encode",
    "transaction", "retain", "purge", "flush", "receive", "emit",
}


def _edit_distance_one(a, b):
    """True iff `a` and `b` are exactly one edit (insert/delete/substitute) apart."""
    if a == b:
        return False
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:                                   # substitution or transposition
        diff = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
        if len(diff) == 1:
            return True                            # one substitution
        if len(diff) == 2 and diff[1] == diff[0] + 1:
            i, j = diff                            # one adjacent transposition
            return a[i] == b[j] and a[j] == b[i]
        return False
    if la > lb:                                    # make `a` the shorter
        a, b, la, lb = b, a, lb, la
    i = j = 0
    skipped = False                                # one deletion from `b` yields `a`
    while i < la and j < lb:
        if a[i] == b[j]:
            i += 1
            j += 1
        else:
            if skipped:
                return False
            skipped = True
            j += 1
    return True


def scan_typos(program):
    """Statement-leading assignment whose name is one edit from an action verb.

    `shoow "hello"` parses as a valid assignment to a variable named `shoow`: it
    compiles AND runs, silently doing nothing while the intended `show` never
    fires. Silent-wrong is worse than a crash, so we surface it -- but as a
    WARNING, not an error, because a real variable named near a verb is possible
    (rename to silence). Returns list[CompileWarning]; a caller may escalate these
    to errors under --strict.
    """
    from mohio_ast import Assignment
    warnings = []
    seen = set()

    def visit(node):
        if node is None or id(node) in seen:
            return
        seen.add(id(node))
        if isinstance(node, Assignment) and node.name:
            nm = node.name.lower()
            if len(nm) >= 4 and nm not in _TYPO_VERBS:
                for kw in _TYPO_VERBS:
                    if len(kw) >= 4 and _edit_distance_one(nm, kw):
                        warnings.append(CompileWarning(
                            f"`{node.name}` is one letter off from the verb `{kw}` "
                            f"-- this parsed as an assignment to a variable named "
                            f"`{node.name}`, so it compiles and runs but the `{kw}` "
                            f"never happens.",
                            getattr(node, "line", 0) or 0,
                            f"Did you mean `{kw}`? If you really want a variable "
                            f"named `{node.name}`, rename it to silence this.",
                            "possible_typo",
                        ))
                        break
        if isinstance(node, (list, tuple)):
            for item in node:
                visit(item)
            return
        if is_dataclass(node):
            for f in fields(node):
                visit(getattr(node, f.name, None))

    visit(program)
    return warnings


# ── unknown / retired type names ──────────────────────────────────────────
# THE DRIFT GENERATOR. `type_name` accepts a bare NAME (it has to -- shape types like
# `sh.Order` arrive that way), so ANY word in a type slot was silently accepted:
# `n as banana` checked clean, and so did every retired type (`num`, `number`) and every
# typo. Silent acceptance is how wrong syntax survives, gets copied into docs, and comes
# back next session. A type slot now takes a known type or a declared shape. Nothing else.

_KNOWN_TYPES = {
    'text', 'decimal', 'dec', 'integer', 'int', 'boolean', 'bool',
    'datetime', 'date', 'time', 'uuid', 'email', 'url', 'json', 'list', 'map',
    'any', 'void', 'base64', 'image', 'audio', 'video', 'pdf', 'file',
    'usd', 'cad', 'eur', 'gbp',          # currency types (each formats + rounds; built on dec.2)
}
# Retired: say so by name instead of a generic "unknown type".
_RETIRED_TYPES = {
    'number': 'int (or integer) for whole numbers, dec (or decimal) for fractions',
    'num':    'int (or integer) for whole numbers, dec (or decimal) for fractions',
}


def scan_unknown_types(program):
    """Every type slot must name a known type or a declared shape."""
    errors = []
    stmts = getattr(program, 'statements', None) or []
    shapes = {getattr(s, 'name', '') for s in stmts
              if type(s).__name__ == 'ShapeDecl'}

    def walk(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                walk(c)
            return
        if not is_dataclass(node):
            return
        t = getattr(node, 'type_name', None)
        if isinstance(t, str) and t:
            base = t.split()[-1] if ' ' in t else t          # `list text` -> text
            base = base.strip()
            low = base.lower()
            # dec.N / dec.N.pad / decimal.N[.pad] are precision annotations on the dec type.
            _root = low.split('.')[0]
            if _root in ('dec', 'decimal') and low != _root:
                low = _root                                   # dec.2 / dec.2.pad -> dec
            known = (low in _KNOWN_TYPES
                     or base in shapes
                     or base.startswith('sh.')
                     or low.startswith('list'))
            if not known:
                if low in _RETIRED_TYPES:
                    msg = (f"`{base}` is not a type. Use {_RETIRED_TYPES[low]}.")
                else:
                    msg = (f"`{base}` is not a known type and no shape named `{base}` is "
                           f"declared. Types: text, int/integer, dec/decimal, bool/boolean, "
                           f"date, datetime, email, url, uuid, json, list, file "
                           f"(or a shape you declared).")
                errors.append(CompileError(
                    msg,
                    line=getattr(node, 'line', 0) or 0,
                    hint="A type slot takes a known type or a declared shape."))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    for s in stmts:
        walk(s)
    return errors


# ── undeclared connectors ─────────────────────────────────────────────────
# The compiler knows every declared connector at check time, so a call to one that does not
# exist should be refused at CHECK, with the line -- not left to blow up at runtime. This
# also catches the confusing case where a stray `name as other` line parses as a connector
# call (mioconnect's `Connector.op with payload as result` shape) and only failed when run.

def scan_undeclared_connectors(program):
    """Every `Connector.op ...` call must name a declared `mioconnect`."""
    errors = []
    stmts = getattr(program, 'statements', None) or []
    declared = set()

    def collect(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                collect(c)
            return
        if not is_dataclass(node):
            return
        if type(node).__name__ == 'MioconnectDecl':
            n = getattr(node, 'name', '')
            if n:
                declared.add(str(n))
        for f in fields(node):
            collect(getattr(node, f.name, None))

    def walk(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                walk(c)
            return
        if not is_dataclass(node):
            return
        if type(node).__name__ == 'MioconnectCall':
            conn = str(getattr(node, 'connector', '') or '')
            if conn and conn not in declared:
                errors.append(CompileError(
                    f"No connector named `{conn}` is declared.",
                    line=getattr(node, 'line', 0) or 0,
                    hint=(f"Declare it first: `mioconnect {conn} ... mioconnect: done`. "
                          f"(If you did not mean a connector call, note that "
                          f"`{conn} as NAME` on its own line reads as one.)")))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    for s in stmts:
        collect(s)
    for s in stmts:
        walk(s)
    return errors


# ── undeclared tasks ──────────────────────────────────────────────────────
# `call nonexistentTask` silently did nothing and execution carried on. Every task is known
# at check time, so a call to one that does not exist is refused at CHECK, with the line.

# Services the INTERPRETER itself says are not wired. This is its own _service_hints list --
# copied from the compiler, not guessed. An allowlist does not work here: ServiceCallStmt is a
# generic dotted catch-all that also carries legitimate forms (session.id, remove.html), so
# guessing at "wired" wrongly condemns real code. The interpreter names what is NOT wired, and
# that is the fact to enforce.
_NOT_WIRED_SERVICES = {'mioai', 'miofile', 'miohttp', 'mioimage', 'miomail', 'miopdf', 'miosms'}


def scan_not_built_services(program):
    """A service that will blow up at RUN must blow up at CHECK.

    These already fail loud at runtime -- NotBuiltService and the ServiceCallStmt fallback both
    raise with a clear message. But they passed `mio check` with exit 0 and no warning, so you
    would see green, deploy, and find out in production. Check-time silence is still silence.
    """
    errors = []

    def walk(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                walk(c)
            return
        if not is_dataclass(node):
            return

        kind = type(node).__name__
        if kind == 'NotBuiltService':
            svc = getattr(node, 'service', '')
            mth = getattr(node, 'method', '')
            call = svc + (f".{mth}" if mth else "")
            line = getattr(node, 'line', 0) or 0
            if getattr(node, 'tier', 'plain') == 'commercial':
                errors.append(CompileError(
                    f"{call} is a commercial-tier managed service and is not available in the "
                    f"open compiler. To proceed: remove {call}, or run it under a Mohio "
                    f"commercial license.", line=line))
            else:
                errors.append(CompileError(
                    f"{call} is declared in the grammar but not built in this release. Left "
                    f"silent it would no-op and hide the gap. To proceed: remove {call} for "
                    f"now, or use a built alternative.", line=line))

        elif kind == 'ServiceCallStmt':
            svc = str(getattr(node, 'service', '') or '')
            mth = str(getattr(node, 'method', '') or '')
            # Every genuinely wired mio* service gets its OWN ast node (MioCookieSet,
            # MiohttpStmt, MiomailStmt...). Only miocache and miolog run through the generic
            # dotted catch-all. So a `mio*` that lands HERE is not wired, and the interpreter
            # will raise 'no handler in this build' at run. Say so at CHECK instead.
            # This deliberately does not touch non-mio dotted forms (session.id, remove.html):
            # guessing an allowlist for those wrongly condemns real code.
            _line = getattr(node, 'line', 0) or 0
            unwired_mio = (svc.startswith('mio') and svc not in {'miocache', 'miolog'})
            # RETIRED dotted forms (2026-08-01, Category-3): a working alternative exists, so the
            # message says "retired, use X" rather than "not wired" -- same treatment as run_block,
            # hold blocks, and ai.chain.
            if svc == 'mioai':
                errors.append(CompileError(
                    f"mioai.{mth} is retired. Use `ai.create` to generate text, data, an image, "
                    f"audio, or video (e.g. `ai.create poster image`), or `ai.decide` for AI "
                    f"reasoning -- both are wired.", line=_line))
            elif svc == 'miohttp':
                errors.append(CompileError(
                    f"miohttp.{mth} is not a wired HTTP verb; the extra dotted forms are retired. "
                    f"Use one of the wired verbs: miohttp.get, miohttp.post, miohttp.put, "
                    f"miohttp.delete, or miohttp.patch.", line=_line))
            elif svc in _NOT_WIRED_SERVICES or unwired_mio:
                errors.append(CompileError(
                    f"{svc}.{mth} is declared but not wired in this build -- it parses, but it "
                    f"would fail at run with 'no executor'. To proceed: remove it, or use a "
                    f"built alternative.", line=_line))

        for f in fields(node):
            walk(getattr(node, f.name, None))

    walk(getattr(program, 'statements', None) or [])
    return errors


def scan_otherwise_placement(program):
    """`otherwise` is the LAST condition in a set of conditionals. Once per set.

    DESIGN SPEC (Ronnie): otherwise is the same idea as `else` -- the final branch of ONE
    conditional statement. A block may contain several conditional sets, and they may nest; each
    set gets its own otherwise. But within a set it appears once, and it appears last.

    check_block already enforces this in the grammar (`check_when* otherwise_clause?`). The other
    twenty verb blocks share `result_handler*`, which happily accepted TWO otherwise clauses, or
    an otherwise sitting ahead of on.failure. Both checked clean and one of them would simply
    never run. Scanned here so the rule lives in one place for every block.
    """
    errors = []

    def walk(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                walk(c)
            return
        if not is_dataclass(node):
            return

        handlers = getattr(node, 'handlers', None)
        if isinstance(handlers, (list, tuple)) and handlers:
            idx = [i for i, h in enumerate(handlers)
                   if type(h).__name__ == 'OtherwiseClause']
            block = type(node).__name__.replace('Block', '').lower() or 'block'
            if len(idx) > 1:
                errors.append(CompileError(
                    f"`otherwise` appears {len(idx)} times in this {block}. It is the LAST "
                    f"condition of a conditional set, so there is only ever one. Use a second "
                    f"conditional set (or a nested block) if you need another.",
                    line=getattr(handlers[idx[1]], 'line', 0) or getattr(node, 'line', 0) or 0))
            elif len(idx) == 1 and idx[0] != len(handlers) - 1:
                after = type(handlers[idx[0] + 1]).__name__
                errors.append(CompileError(
                    f"`otherwise` must be LAST in this {block}. It is the final fallback, so "
                    f"nothing follows it -- but `{after}` does. Move `otherwise` to the end.",
                    line=getattr(handlers[idx[0]], 'line', 0) or getattr(node, 'line', 0) or 0))

        for f in fields(node):
            walk(getattr(node, f.name, None))

    walk(getattr(program, 'statements', None) or [])
    return errors


def scan_undeclared_tasks(program):
    """Every `call NAME` must name a declared `task`."""
    errors = []
    stmts = getattr(program, 'statements', None) or []
    declared = set()

    def collect(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                collect(c)
            return
        if not is_dataclass(node):
            return
        if type(node).__name__ == 'TaskDecl':
            n = getattr(node, 'name', '')
            if n:
                declared.add(str(n))
        for f in fields(node):
            collect(getattr(node, f.name, None))

    def walk(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                walk(c)
            return
        if not is_dataclass(node):
            return
        # CallBlock is an alias of RunBlock; a task call carries task_name.
        if type(node).__name__ == 'RunBlock':
            tn = str(getattr(node, 'task_name', '') or '')
            if tn and tn not in declared:
                errors.append(CompileError(
                    f"No task named `{tn}` is declared.",
                    line=getattr(node, 'line', 0) or 0,
                    hint=f"Declare it first: `task {tn} ... task: done`."))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    for s in stmts:
        collect(s)
    for s in stmts:
        walk(s)
    return errors


# The words that OPEN a block. None of them is a variable name.
#
# `new` was not among the reserved words, so this:
#
#     new Signup at "/signup"          <-- wrong: needs `sh.Signup`, and lives in `listen for`
#
# did not fail. `NAME` matched `new`, the statement became an ASSIGNMENT declaring a
# variable called `new`, the listener silently never existed, every request 404'd, and
# `mio check` reported "no errors". The developer -- or the AI writing the Mohio -- is told
# the program is fine, and finds out from a user.
#
# This is the same bug that has now been fixed five separate times, one keyword at a time,
# only ever after it bit: `if`, `unless`, `otherwise`, `when`, `miotest`, and the 33 service
# roots. Patching what bit us is how it kept coming back. There are 23 of these words. They
# are all here.
BLOCK_OPENERS = {
    'new', 'shape', 'saga', 'listen', 'save', 'remove', 'get', 'pull',
    'make', 'change', 'create', 'try', 'loop', 'repeat', 'each', 'while', 'sql',
    'render', 'sector', 'request', 'step', 'transaction',
    # NOT `check`. `check confidence above 0.85` is RETIRED syntax inside ai.decide, and
    # the compiler already warns about it BY NAME. Flagging it here would escalate that
    # warning to an error and say the wrong thing. A word can open a block in one place
    # and be a retired modifier in another; the retirement message is the better one.
}

_OPENER_HINT = {
    'new':    'new sh.<Shape> at /path      (inside a `listen for` block)',
    'shape':  'shape <Name> ... shape: done',
    'listen': 'listen for ... listen: done',
    'save':   'save to db.<table> ... save: done',
    'find':   'find <name> in db.<table> ... find: done',
    'check':  'check <name> in db.<table> ... check: done',
    'try':    'try ... try: done',
    'sql':    'sql ... sql: done',
    'make':   'create <Name> ... create: done   (make is retired -- use create)',
}


def scan_block_opener_as_variable(program):
    """A block-opening word used as a variable name means the BLOCK SILENTLY VANISHED.

    Earley resolves `new Signup at "/signup"` to `assignment: NAME value_expr` -- a
    variable named `new` -- because `new_block` needs an SH_REF and `Signup` is not one.
    Both parses exist; the wrong one wins; nothing complains. The block the developer
    wrote does not exist in the program, and no error is ever produced.

    So: if a declaration's NAME is a word that opens a block, the block form was meant and
    was not achieved. Refuse, and say what the real form is.
    """
    errors = []

    def walk(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                walk(c)
            return
        if not is_dataclass(node):
            return

        if type(node).__name__ in ('Assignment', 'HoldDecl', 'LockDecl'):
            name = str(getattr(node, 'name', '') or '')
            if name in BLOCK_OPENERS:
                line = getattr(node, 'line', 0) or 0
                hint = _OPENER_HINT.get(name)
                msg = (f"`{name}` opens a block. It is not a variable name.\n"
                       f"    This line declared a VARIABLE called `{name}`, which means the "
                       f"`{name}` block you wrote does not exist in the program at all -- "
                       f"and nothing would have told you.")
                if hint:
                    msg += f"\n    The form is:  {hint}"
                errors.append(CompileError(msg, line=line))

        for f in fields(node):
            walk(getattr(node, f.name, None))

    walk(getattr(program, 'statements', []) or [])
    return errors


# ── THE CANONICAL SCANNER LIST ────────────────────────────────────────────────
# There is ONE list of scanners, and it is here, next to the scanners.
#
# There used to be two. `mohio_enforce.enforce()` had one, and `mio.py check` kept its own
# parallel copy -- so a rule added to one SILENTLY DID NOT EXIST in the other. That is not
# hypothetical: `scan_block_opener_as_variable` was added to enforce(), and `mio check`
# went on reporting "no errors" on the exact program it was written to catch, because
# mio.py's private list had never heard of it.
#
# It is the same disease as every other bug this week: A LIST THAT DOES NOT NAME A THING
# DOES NOT FAIL -- IT SILENTLY DOES NOTHING. The scanners were the enforcement of that
# rule, and the enforcement itself had the bug.
#
# The two callers still differ, and they SHOULD: `mio check` also resolves includes and
# applies the journey spine, which a library call has no business doing. What must never
# differ again is WHICH RULES RUN. Add a scanner to a tuple below and both doors get it.
#
# ERROR   -> refuses. The program is wrong and will not work.
# WARNING -> reports. The program is suspect but may be intentional.
def scan_audit_destinations(program):
    """Every `ai.audit to <dest>` must name a governed audit destination.

    `is_audit_table` is the contract the platform derives its append-only role grants from: the
    static set, `*_audit_log`, and `*_limits_log`. A destination outside that convention is not
    covered by those grants, so audit records written there are ordinary rows a tenant can update
    or delete -- the append-only guarantee silently does not apply, and nothing reports it.

    Catching it at CHECK time is the point. Once an audit record has been written to an
    ungoverned table it is already unprotected, and at the Certified tier it may already be
    sealed into storage that refuses deletion for the retention period. This is the cheapest
    possible moment to say no.
    """
    from mohio_audit_grades import is_audit_table
    errors = []

    def walk(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                walk(c)
            return
        if not is_dataclass(node):
            return
        if type(node).__name__ == 'AiAuditStmt':
            dest = getattr(node, 'log_name', '') or ''
            if dest and not is_audit_table(dest):
                errors.append(CompileError(
                    f"`ai.audit to {dest}` names a destination outside the audit convention, "
                    f"so it would not be covered by the append-only protections that make an "
                    f"audit trail an audit trail.",
                    line=getattr(node, 'line', 0),
                    hint=(f"Name it `{dest}_audit_log`, or use one of the standard logs "
                          f"(fraud_audit_log, phi_audit_log, data_audit_log, "
                          f"operation_audit_log). An audit destination must end in "
                          f"`_audit_log` or `_limits_log`.")))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    walk(program)
    return errors


def scan_undeclared_shapes(program):
    """A reference to a shape that was never declared is refused.

    `request for sh.NonExistent at /x` compiled clean and ran. A shape reference is the contract
    for what a request carries -- fields, types, validation -- so a reference to a shape that does
    not exist is a request with NO contract, silently. Every field validation the developer
    thought they declared simply does not happen.

    Undeclared connectors and tasks already fail this way; shapes were the gap.

    Covers every node that can carry a shape name, not just the one that surfaced the bug:
    request/new/change blocks, create blocks, miomap from/to, mioconnect sends/returns, and bare
    `sh.X` references.
    """
    declared = set()
    refs = []          # (name, line)

    # (class name, attribute) pairs that hold a shape REFERENCE
    ref_fields = (
        ('RequestInboundBlock', 'shape'), ('NewBlock', 'shape'), ('ChangeBlock', 'shape'),
        ('CreateBlock', 'shape'), ('ShRef', 'shape_name'),
        ('MiomapDecl', 'from_shape'), ('MiomapDecl', 'to_shape'),
        ('MioconnectOperation', 'sends_shape'), ('MioconnectOperation', 'returns_shape'),
    )

    def walk(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                walk(c)
            return
        if not is_dataclass(node):
            return
        cls = type(node).__name__
        if cls == 'ShapeDecl':
            nm = str(getattr(node, 'name', '') or '')
            if nm:
                declared.add(nm)
        for ref_cls, attr in ref_fields:
            if cls == ref_cls:
                val = getattr(node, attr, None)
                if val:
                    refs.append((str(val), getattr(node, 'line', 0)))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    walk(program)

    errors = []
    for name, line in refs:
        bare = name.split('.')[-1]
        if bare and bare not in declared:
            hint = (f"Declare it with `shape {bare} ... shape: done`.")
            if declared:
                close = sorted(declared, key=lambda d: (d.lower() != bare.lower(), d))
                hint += f" Declared shapes: {', '.join(close[:6])}."
            errors.append(CompileError(
                f"`sh.{bare}` is used but no shape named `{bare}` is declared.",
                line=line,
                hint=hint + " A request referencing a shape that does not exist has no field "
                            "contract at all, so nothing it declares is validated."))
    return errors


def scan_give_back_no_value(program):
    """`give back` with no value silently becomes a variable assignment.

    `give back` is the return verb, but neither `give` nor `back` is a reserved
    word, so `give back` written with nothing after it has no value_expr to bind.
    Earley then falls to the only other reading available -- `assignment: NAME
    value_expr` -- a variable named `give` set to the bare name `back`. The return
    never happens: the handler falls through and the route answers with an empty
    body and no error. That is the exact silent-wrongness this language refuses.

    Detection is exact and cannot false-positive: an Assignment whose name is
    `give` and whose value is the bare name `back`. That pair is only ever the
    misparse of the two-word return verb with its value missing -- it is never an
    intended assignment, because `give back` is the return statement.

    Note: this catches the missing-value case only. `give 5` or `give back x`
    are left alone -- the first is `give` used deliberately as a variable, the
    second is a real GiveBackStmt.
    """
    errors = []

    def _is_bare_back(val):
        return (val is not None
                and type(val).__name__ == 'DottedName'
                and list(getattr(val, 'parts', []) or []) == ['back'])

    def walk(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                walk(c)
            return
        if not is_dataclass(node):
            return
        if type(node).__name__ == 'Assignment':
            if (str(getattr(node, 'name', '') or '') == 'give'
                    and _is_bare_back(getattr(node, 'value', None))):
                line = getattr(node, 'line', 0) or 0
                errors.append(CompileError(
                    "`give back` needs a value.\n"
                    "    Written with nothing after it, `give back` is not the return "
                    "verb -- it becomes a VARIABLE named `give` set to `back`, so the "
                    "response is never sent and the handler falls through with an empty "
                    "body.\n"
                    "    The form is:  give back 200 something   (a status, a value, or "
                    "both).",
                    line=line))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    walk(getattr(program, 'statements', []) or [])
    return errors


def scan_miofile_dangerous_accept(program):
    """Refuse, at check, a declared area whose `accept` names an executable type.

    An upload field already refuses executables ahead of its allowlist, so `accept exe`
    can never be honoured. Left to runtime it reads as a working permission that
    silently is not one, so it is an error where it is written, not where it runs.
    """
    from dataclasses import fields, is_dataclass
    from mohio_ast import MiofileDecl
    try:
        from mohio_interpreter import MohioInterpreter
        banned = MohioInterpreter._DANGEROUS_UPLOAD_EXT
    except Exception:
        return []
    errors = []

    def walk(node):
        if node is None:
            return
        if isinstance(node, list):
            for i in node:
                walk(i)
            return
        if not is_dataclass(node):
            return
        if isinstance(node, MiofileDecl):
            for z in (getattr(node, 'zones', None) or []):
                for pol in (z.get('policies') or []):
                    if pol.get('policy') != 'accept':
                        continue
                    for c in (pol.get('parts') or []):
                        items = c.children if hasattr(c, 'children') else [c]
                        for t in items:
                            e = str(t).strip().strip('"').lstrip('.').lower()
                            if e in banned:
                                area = z.get('name') or z.get('path') or 'this area'
                                errors.append(CompileError(
                                    f"`accept {e}` on the {area} area names an executable "
                                    f"file type, which a declared area never accepts -- an "
                                    f"upload field refuses it too. To proceed: remove {e} "
                                    f"from `accept`.",
                                    line=getattr(node, 'line', 0) or 0))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    walk(getattr(program, 'statements', None) or [])
    return errors


def _blocked_upload_exts():
    """The one blocklist, read from the interpreter so it can never drift into two."""
    try:
        from mohio_interpreter import MohioInterpreter
        return MohioInterpreter._DANGEROUS_UPLOAD_EXT
    except Exception:
        return frozenset()


def _blocked_reason(ext):
    """Why this type is refused, in the words the author needs to hear."""
    if ext == 'svg':
        return ("An SVG can carry script, so it becomes cross-site scripting the moment "
                "it is served inline.")
    if ext in ('doc', 'xls', 'ppt'):
        return ("The legacy Office formats can carry macros. The modern docx, xlsx and "
                "pptx cannot, and are accepted.")
    if ext in ('py', 'rb'):
        return ("Uploading a script is a remote code execution risk, so it belongs to "
                "the paid file service rather than the free tier.")
    return "It is executable or macro-carrying."


_ACCEPT_NOT_A_GROUP = {'all', 'any', 'everything', '*'}

# Near misses for the nine real groups. Left alone these would be read as literal
# extensions, so `accept image` would refuse every real image and accept a file named
# '.image' -- the exact silent-backwards failure groups were built to remove.
_ACCEPT_GROUP_TYPOS = {
    'image': 'images', 'photo': 'images', 'photos': 'images', 'picture': 'images',
    'pictures': 'images', 'document': 'documents', 'spreadsheet': 'spreadsheets',
    'presentation': 'presentations', 'presentions': 'presentations',
    'archive': 'archives', 'audios': 'audio', 'videos': 'video', 'movie': 'video',
    'movies': 'video', 'sound': 'audio', 'sounds': 'audio',
}


def scan_upload_accept_groups(program):
    """Refuse, at check, an `accept` entry that reads like a group but is not one.

    The nine real groups resolve to extension lists, so they are fine. `accept all`
    never resolves -- it would be read as a literal '.all' extension and refuse
    everything -- and a near miss like `accept image` would do the same, so both stop
    here rather than shipping a field that behaves backwards.
    """
    from dataclasses import fields, is_dataclass
    from mohio_ast import ShapeDecl
    errors = []
    upload_types = ('file', 'image', 'audio', 'video', 'pdf')

    def walk(node):
        if node is None:
            return
        if isinstance(node, list):
            for i in node:
                walk(i)
            return
        if not is_dataclass(node):
            return
        if isinstance(node, ShapeDecl):
            for fld in (getattr(node, 'fields', None) or []):
                if (getattr(fld, 'type_name', None) or '') not in upload_types:
                    continue
                for m in (getattr(fld, 'modifiers', None) or []):
                    if getattr(m, 'modifier_type', '') != 'accept':
                        continue
                    for e in (getattr(m, 'value', None) or []):
                        w = str(e).strip().strip('"').lstrip('.').lower()
                        line = getattr(fld, 'line', 0) or getattr(node, 'line', 0) or 0
                        if w in _ACCEPT_NOT_A_GROUP:
                            errors.append(CompileError(
                                f"`accept {w}` on upload field "
                                f"'{getattr(fld, 'name', '')}' is not a group and never "
                                f"will be -- accepting anything at all is refused on "
                                f"purpose. To proceed: name a group (images, documents, "
                                f"spreadsheets, presentations, archives, audio, video, "
                                f"media, office) or list the extensions.",
                                line=line))
                        elif w in _ACCEPT_GROUP_TYPOS:
                            right = _ACCEPT_GROUP_TYPOS[w]
                            errors.append(CompileError(
                                f"`accept {w}` on upload field "
                                f"'{getattr(fld, 'name', '')}' is not a group, so it "
                                f"would be read as a file extension and refuse every "
                                f"real one. To proceed: use `accept {right}`.",
                                line=line))
                        elif w in _blocked_upload_exts():
                            errors.append(CompileError(
                                f"`accept {w}` on upload field "
                                f"'{getattr(fld, 'name', '')}' names a file type that is "
                                f"never accepted, so the field could not honour it. "
                                f"{_blocked_reason(w)} To proceed: remove {w} from "
                                f"`accept`.",
                                line=line))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    walk(getattr(program, 'statements', None) or [])
    return errors


def scan_give_destination(program):
    """Refuse, at check, a `give` that cannot do what it says.

    Three cases, all caught before the app runs rather than at request time:
      - no destination at all. `give invoice` on its own is not an action.
      - a destination other than `as download`. Sending mail is miomail, calling a
        server is miohttp; `give` has one job, handing a value to the requester as a
        file, and inventing a second `as` target would make it a second `give back`.
      - `as download` with no filename on a value that has no name of its own. Only a
        literal path carries its name in it (the tail). A variable or a database field
        does not, and the transformer sees only the shape, not what the value will hold
        at request time -- so this is refused now instead of failing on the request.
    """
    from dataclasses import fields, is_dataclass
    from mohio_ast import GiveStmt, Literal
    errors = []

    def walk(node):
        if node is None:
            return
        if isinstance(node, list):
            for i in node:
                walk(i)
            return
        if not is_dataclass(node):
            return
        if isinstance(node, GiveStmt):
            line = getattr(node, 'line', 0) or 0
            mod = (str(getattr(node, 'modifier', None) or '')).lower()
            if not mod:
                errors.append(CompileError(
                    "`give` needs a destination -- on its own it does not do anything. "
                    "To proceed: `give <value> as download` to hand a file to whoever "
                    "asked for it, or `give back <value>` to answer the request.",
                    line=line))
            elif mod != 'download':
                errors.append(CompileError(
                    f"`give ... as {mod}` is not a destination `give` knows. It hands a "
                    f"value to the requester as a file, so `as download` is the one form. "
                    f"To proceed: use `as download`, or the service that owns the job you "
                    f"mean -- miomail to send mail, miohttp to call a server, `give back "
                    f"... as json` to answer with data.",
                    line=line))
            elif getattr(node, 'filename', None) is None and \
                    not isinstance(getattr(node, 'value', None), Literal):
                errors.append(CompileError(
                    "`give ... as download` needs a filename here. The name can only be "
                    "worked out from a path written in place, like "
                    "`give \"reports/q3.pdf\" as download`. This value could hold anything "
                    "at the time the page runs, so there is no name to take. "
                    "To proceed: name the file, e.g. `as download \"invoice.pdf\"`.",
                    line=line))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    walk(getattr(program, 'statements', None) or [])
    return errors


def scan_bare_random_intrinsic(program):
    """`random.token`/`random.hex`/`random.number` used bare (no `length N` / `between N and
    N`) fail to match their dedicated grammar rule and are silently re-parsed as a field read
    on an undefined variable named `random` -- see get_dotted's identical runtime guard
    (mohio_interpreter.py, T0-5) for the full mechanism. This is the check-time half: catch it
    before the program ever runs, not only when it happens to execute that line.
    """
    from dataclasses import fields, is_dataclass
    from mohio_ast import DottedName
    errors = []
    _needs_clause = {'token': 'length N', 'hex': 'length N', 'number': 'between N and N'}

    def walk(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for item in node:
                walk(item)
            return
        if not is_dataclass(node):
            return
        if isinstance(node, DottedName):
            parts = getattr(node, 'parts', None) or []
            if len(parts) == 2 and str(parts[0]) == 'random' and str(parts[1]) in _needs_clause:
                clause = _needs_clause[str(parts[1])]
                errors.append(CompileError(
                    f"random.{parts[1]} needs its required clause -- write `random.{parts[1]} "
                    f"{clause}`. Used bare, it does not match that form and silently reads as "
                    f"a field on an undefined variable named `random` instead.",
                    getattr(node, 'line', 0) or 0,
                    f"Add the clause: `random.{parts[1]} {clause}`.",
                ))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    walk(getattr(program, 'statements', None) or [])
    return errors


def scan_mixed_connector_chain(program):
    """CR (ruled): a mixed `and`/`or` chain in the general `condition` rule has no defined
    grouping in Mohio and must be a check-time error, not silently resolved.

    `condition: ... | NOT condition -> cond_not | condition AND condition -> cond_and |
    condition OR condition -> cond_or | value_expr -> cond_bool` (mohio_data/mohio.lark:2727-2730)
    is one self-recursive rule with no precedence declared between AND and OR, so a flat mixed
    chain like `a and b or c` has multiple valid derivations and Earley's ambiguity resolution
    picks ONE silently (confirmed by direct AST dump this session: `a AND (b OR c)`, the OPPOSITE
    of the C-family convention every language this reads like uses). Mohio has no
    developer-writable grouping for conditions (parens are math-only), so there is no way to WRITE
    the grouping you meant -- the fix is to split into a check/when block, not to add parens.

    This is the single general `condition` rule reached everywhere `AndCondition`/`OrCondition`
    nodes appear -- `if`/`unless`/`while` guards, `check ... when` guards, trailing `IF condition`
    qualifiers, `modify`'s `WHERE condition` (T0-1's fix site: confirmed the SAME `_eval_condition`
    evaluator), `rerun until`, `stop`/`skip ... when`, and more -- so walking the AST for the node
    SHAPE (rather than special-casing each statement type that can carry one) catches all of them
    uniformly, matching how `_eval_condition` evaluates all of them uniformly at runtime.

    MioQL's `where`/`match` clauses are a COMPLETELY SEPARATE grammar path (block form:
    `match`/`match any`/`no.match`, or repeated `where` lines) that never produces an
    AndCondition/OrCondition node at all (confirmed by reading the grammar and by this session's
    connector-reach survey) -- so a legitimate `find`/`retrieve` query is structurally unreachable
    by this scanner, not just untested.
    """
    from dataclasses import fields, is_dataclass
    from mohio_ast import AndCondition, OrCondition, NotCondition
    errors = []

    def connector_types(node, seen):
        """Every connector type ('and'/'or') anywhere in this condition subtree, transparent
        through NOT (a bare `not (a and b)` is not itself mixed -- only a real and+or chain is)."""
        if node is None or id(node) in seen:
            return set()
        seen.add(id(node))
        if isinstance(node, AndCondition):
            return {'and'} | connector_types(node.left, seen) | connector_types(node.right, seen)
        if isinstance(node, OrCondition):
            return {'or'} | connector_types(node.left, seen) | connector_types(node.right, seen)
        if isinstance(node, NotCondition):
            return connector_types(node.condition, seen)
        return set()

    def walk(node, seen):
        if node is None or id(node) in seen:
            return
        seen.add(id(node))
        if isinstance(node, (list, tuple)):
            for item in node:
                walk(item, seen)
            return
        if not is_dataclass(node):
            return
        if isinstance(node, (AndCondition, OrCondition)):
            # The first AndCondition/OrCondition reached walking top-down is always the ROOT of
            # its chain (a root can't be a descendant of another connector in one recursive
            # tree), so checking it here and NOT descending further avoids re-examining --
            # and re-reporting -- the same chain again from an inner node's own perspective.
            if len(connector_types(node, set())) > 1:
                errors.append(CompileError(
                    "a mixed and/or chain has no defined grouping in Mohio -- the block "
                    "structure is the logic, there is no operator precedence between `and` and "
                    "`or`. Use all-`and` or all-`or` in one chain, or split the logic into a "
                    "check/when block.",
                    getattr(node, 'line', 0) or 0,
                    "Rewrite as `a and b and c` / `a or b or c`, or use `check ... when a and b "
                    "/ when c / otherwise ...` to write the grouping you actually mean.",
                ))
            return
        for f in fields(node):
            walk(getattr(node, f.name, None), seen)

    walk(getattr(program, 'statements', None) or [], set())
    return errors


ERROR_SCANS = (
    scan_give_destination,
    scan_orphan_it,
    scan_unknown_types,
    scan_undeclared_connectors,
    scan_undeclared_tasks,
    scan_not_built_services,
    scan_otherwise_placement,
    scan_block_opener_as_variable,
    scan_give_back_no_value,
    scan_audit_destinations,
    scan_undeclared_shapes,
    scan_miofile_dangerous_accept,
    scan_upload_accept_groups,
    scan_bare_random_intrinsic,
    scan_mixed_connector_chain,
)

def scan_audit_grade_requirement(program):
    """Surface the audit-storage grade a program's declared sector demands.

    This is a WARNING, not an error, and the distinction is the whole point. The compiler cannot
    know what sink a deployment will bind -- that is the seam: the compiler enforces the required
    grade, the platform binds a sink that meets it. So refusing the build here would be a guess
    dressed as a guarantee.

    What it can honestly do is tell the developer, before they deploy, what their program will
    demand. A program declaring a sector whose frameworks require append-only or WORM storage
    will trip the degraded-audit path on every write if it lands on an ordinary durable sink, and
    finding that out at check time is considerably better than finding it out from an incident
    record in production.

    Nothing in the base runtime provides a WORM sink today -- that grade is satisfied by
    provider-side immutable storage, not by anything the compiler ships -- so a WORM-requiring
    sector is specifically worth naming.
    """
    try:
        from mohio_audit_grades import required_grade
    except Exception:
        return []
    warnings = []
    seen = set()

    def walk(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                walk(c)
            return
        if not is_dataclass(node):
            return
        if type(node).__name__ == 'SectorDecl':
            name = str(getattr(node, 'name', '') or getattr(node, 'sector', '') or '')
            if not name or name in seen:
                for f in fields(node):
                    walk(getattr(node, f.name, None))
                return
            seen.add(name)
            # A sector is not a framework. The sector's PROFILE declares which frameworks are
            # active, and those are what carry an audit grade -- so resolve the profile first
            # rather than looking up the sector name in the framework table and reporting every
            # sector as "unknown".
            frameworks, profile_found = [], False
            try:
                from mohio_sector_loader import find_sector_profile, load_sector_profile
                path = find_sector_profile(name)
                if path:
                    profile_found = True
                    frameworks = list(getattr(load_sector_profile(path), 'compliance', []) or [])
            except Exception:
                profile_found = False
            if not profile_found:
                warnings.append(CompileWarning(
                    f"sector `{name}` is declared but no profile for it was found, so no "
                    f"compliance framework is active and no audit grade is required.",
                    line=getattr(node, 'line', 0),
                    hint=("A sector with no profile enforces nothing. Add the profile, or drop "
                          "the declaration so the program does not read as governed when it is "
                          "not.")))
            elif frameworks:
                grade, unknown = required_grade(frameworks)
                if grade in ('append_only', 'worm'):
                    detail = ("provider-side immutable (WORM) storage, which no sink in the base "
                              "runtime provides" if grade == 'worm'
                              else "append-only audit storage")
                    warnings.append(CompileWarning(
                        f"sector `{name}` ({', '.join(frameworks)}) requires {grade}-grade "
                        f"audit storage: {detail}.",
                        line=getattr(node, 'line', 0),
                        hint=("The compiler enforces the grade; the deployment binds a sink that "
                              "meets it. On an ordinary durable sink every audit write for this "
                              "program will record a degraded-audit incident. Confirm the "
                              "deployment binds a sink at this grade before going live.")))
                if unknown:
                    warnings.append(CompileWarning(
                        f"sector `{name}` names framework(s) with no known audit grade: "
                        f"{', '.join(unknown)}.",
                        line=getattr(node, 'line', 0),
                        hint=("An unrecognised framework is treated as requiring nothing, which "
                              "is a silent downgrade. Add it to the framework table or correct "
                              "the spelling.")))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    walk(program)
    return warnings


def scan_miofile_zone_coverage(program):
    """Warn when a program declares miofile zones and then operates on a path that
    no zone covers.

    A declared zone carries `accept` / `max size`. An operation outside every zone
    keeps the default file-area behaviour, so it is not an error -- but it silently
    skips the policies the author thought they had set, which is worth saying out
    loud. Deliberately silent when NO zones are declared: warning on every file
    operation in every program that never used zones is noise, not a signal.
    """
    from dataclasses import fields, is_dataclass
    from mohio_ast import MiofileDecl, MiofileStmt
    zones, stmts = [], []

    def walk(node):
        if node is None:
            return
        if isinstance(node, list):
            for i in node:
                walk(i)
            return
        if not is_dataclass(node):
            return
        if isinstance(node, MiofileDecl):
            for z in (getattr(node, 'zones', None) or []):
                p = str(z.get('path') or '')
                p = p[1:-1] if len(p) >= 2 and p[0] == p[-1] == '"' else p
                p = p.replace('\\', '/').strip('/')
                if p:
                    zones.append((p, z.get('name') or p))
        elif isinstance(node, MiofileStmt):
            stmts.append(node)
        for f in fields(node):
            walk(getattr(node, f.name, None))

    walk(getattr(program, 'statements', None) or [])
    if not zones or not stmts:
        return []

    warnings = []
    for st in stmts:
        for attr in ('path', 'dest'):
            n = getattr(st, attr, None)
            raw = getattr(n, 'value', None)
            if not isinstance(raw, str):
                continue
            r = raw.replace('\\', '/').strip('/')
            if any(r == zp or r.startswith(zp + '/') for zp, _ in zones):
                continue
            warnings.append(CompileWarning(
                f"miofile.{getattr(st, 'op', '')} works on '{raw}', which is not inside "
                f"any declared area ({', '.join(sorted({n for _, n in zones}))}). It runs "
                f"in the default file area, so the `accept` and `max size` rules on your "
                f"declared areas do not apply to it.",
                getattr(st, 'line', 0) or 0,
                "Move the path inside a declared area, or declare an area that covers it.",
                "miofile_zone_uncovered",
            ))
    return warnings


def scan_sector_route_unauthenticated(program):
    """Warn when a route in a declared sector reads data with nothing checking the caller.

    The runtime records where data crossed a boundary. This is the same question asked one
    step earlier, before anything ships: a route that touches the database in a regulated
    sector, with no `require role` anywhere in it, is an unauthenticated path to governed
    data. Whoever owns the data path owns the compliance claim, and a path nobody has to
    authenticate to reach is not a path the program is governing.

    A WARNING, not an error. A public read in a regulated app is sometimes exactly right --
    a price list, a status page, an opening-hours endpoint. The point is that it should be
    a decision someone made, not one nobody noticed.
    """
    from dataclasses import fields, is_dataclass
    from mohio_ast import SectorDecl, RequireRoleDecl
    warnings = []

    sector = None

    def find_sector(node):
        nonlocal sector
        if node is None or sector is not None:
            return
        if isinstance(node, list):
            for i in node:
                find_sector(i)
            return
        if not is_dataclass(node):
            return
        if isinstance(node, SectorDecl):
            sector = getattr(node, 'name', None) or getattr(node, 'sector', None)
            return
        for f in fields(node):
            find_sector(getattr(node, f.name, None))

    find_sector(getattr(program, 'statements', None) or [])
    if not sector:
        return warnings

    def contains(node, want):
        if node is None:
            return False
        if isinstance(node, list):
            return any(contains(i, want) for i in node)
        if not is_dataclass(node):
            return False
        if isinstance(node, want):
            return True
        return any(contains(getattr(node, f.name, None), want) for f in fields(node))

    def reads_data(node):
        if node is None:
            return False
        if isinstance(node, list):
            return any(reads_data(i) for i in node)
        if not is_dataclass(node):
            return False
        if type(node).__name__ in ('RetrieveBlock', 'FindBlock', 'SaveBlock', 'RemoveBlock'):
            return True
        return any(reads_data(getattr(node, f.name, None)) for f in fields(node))

    def walk(node):
        if node is None:
            return
        if isinstance(node, list):
            for i in node:
                walk(i)
            return
        if not is_dataclass(node):
            return
        if type(node).__name__ in ('PageDecl', 'ListenerDecl', 'RequestBlock', 'NewBlock'):
            if reads_data(node) and not contains(node, RequireRoleDecl):
                where = getattr(node, 'path', None) or getattr(node, 'name', '') or 'a route'
                warnings.append(CompileError(
                    f"`{where}` reads data in the `{sector}` sector with nothing checking "
                    f"who is asking. Anyone who can reach the address gets the data. "
                    f"If that is intended -- a price list, a status page -- say so in a "
                    f"comment so the next reader knows it was a decision. Otherwise add "
                    f"`require role ...`.",
                    line=getattr(node, 'line', 0) or 0))
                return
        for f in fields(node):
            walk(getattr(node, f.name, None))

    walk(getattr(program, 'statements', None) or [])
    return warnings


def scan_grant_role_client_source(program):
    """Warn when `grant role` establishes a role from a client-controlled value.

    `grant role` writes a VERIFIED server-side role. If its value comes straight from the
    incoming request -- `request.X`, or a field of the listener's own request shape -- then the
    caller chooses their own role, which re-opens the exact forgery `grant role` exists to close.
    The safe pattern is to grant a role the SERVER decides: a literal, or a field of a record you
    looked up (`retrieve ... from db...`, then `grant role user.role`).

    A WARNING, not an error: a validated request field can be legitimate, but it should be a
    decision someone made, not one nobody noticed. Conservative by design -- it flags a value
    rooted at `request` or at the enclosing shape variable; a client field routed through an
    intermediate local is not traced (it under-warns rather than cry wolf on the safe db pattern).
    """
    from dataclasses import fields, is_dataclass
    from mohio_ast import GrantRoleDecl, NewBlock, DottedName
    warnings = []

    def value_root(v):
        # value_expr wraps a bare NAME as a single-part DottedName, so `request` and
        # `request.role` both arrive as DottedName; a literal role does not.
        if isinstance(v, DottedName) and getattr(v, 'parts', None):
            return str(v.parts[0])
        return None

    def walk(node, shapevars):
        if node is None:
            return
        if isinstance(node, list):
            for i in node:
                walk(i, shapevars)
            return
        if not is_dataclass(node):
            return
        if isinstance(node, NewBlock) and getattr(node, 'shape', ''):
            sv = node.shape[0].lower() + node.shape[1:]
            shapevars = shapevars | {sv}
        if isinstance(node, GrantRoleDecl):
            root = value_root(getattr(node, 'value', None))
            if root is not None and (root == 'request' or root in shapevars):
                warnings.append(CompileWarning(
                    f"`grant role` here takes its role from `{root}`, which comes straight from "
                    f"the request -- the caller would pick their own role, re-opening the forgery "
                    f"`grant role` exists to close.",
                    getattr(node, 'line', 0) or 0,
                    "Grant a role the server decides: a literal (grant role \"member\"), or a "
                    "field of a record you looked up (retrieve the user, then grant role user.role).",
                    "grant_role_client_source",
                ))
        for f in fields(node):
            walk(getattr(node, f.name, None), shapevars)

    walk(getattr(program, 'statements', None) or [], set())
    return warnings


def scan_cookie_samesite_none_insecure(program):
    """Warn when `same site "none"` is declared without `secure`.

    SameSite=None is a hard browser requirement, not a style preference: a cookie with
    SameSite=None and no Secure flag is silently rejected by the browser -- the cookie is never
    set, with nothing telling the developer why. A check-time WARNING turns that silent
    runtime no-set into a visible message before it ships.
    """
    from dataclasses import fields, is_dataclass
    from mohio_ast import MioCookieSet
    warnings = []

    def walk(node):
        if node is None:
            return
        if isinstance(node, list):
            for i in node:
                walk(i)
            return
        if not is_dataclass(node):
            return
        if isinstance(node, MioCookieSet):
            ss = getattr(node, 'same_site', None)
            # `secure` is either True (declared) or None (not declared) -- the grammar has no
            # way to write it False -- so "not True" means "not declared".
            if ss is not None and str(ss).strip().lower() == 'none' and getattr(node, 'secure', None) is not True:
                warnings.append(CompileWarning(
                    'same site "none" requires secure; browsers will not set a SameSite=None '
                    'cookie that is not Secure, so this cookie would never actually be set.',
                    getattr(node, 'line', 0) or 0,
                    'Add a `secure` line to this miocookie.set block, or use same site "lax" or "strict".',
                    'cookie_samesite_none_insecure',
                ))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    walk(getattr(program, 'statements', None) or [])
    return warnings


def scan_transaction_onfailure_futile(program):
    """Warn when `on.failure` appears on a write inside a `transaction` block.

    T0-4 / FORK-8 (ruled): a transaction is atomic regardless of a caught failure inside it.
    A write's own `on.failure` may still run -- the handler fires as written -- but it does NOT
    rescue the transaction: any failed write still rolls back the WHOLE block, completed writes
    included. Written this way, `on.failure` reads like local damage control, and it is not one;
    surfacing that at check time is considerably better than a developer discovering it from a
    partial-write incident.
    """
    from dataclasses import fields, is_dataclass
    from mohio_ast import TransactionBlock, OnFailure
    warnings = []

    def find_onfailure(node, seen):
        if node is None or id(node) in seen:
            return
        seen.add(id(node))
        if isinstance(node, (list, tuple)):
            for item in node:
                find_onfailure(item, seen)
            return
        if not is_dataclass(node):
            return
        if isinstance(node, OnFailure):
            warnings.append(CompileWarning(
                "on.failure inside a transaction cannot rescue it -- the handler may run, but "
                "the whole transaction still rolls back if any write inside it fails. This is "
                "intentional (a transaction is atomic): a caught failure here does not mean "
                "the transaction survived.",
                getattr(node, 'line', 0) or 0,
                "Move failure handling outside the transaction if you need to react to the "
                "rollback, or remove on.failure -- the transaction's own rollback already "
                "covers the failure.",
                'transaction_onfailure_futile',
            ))
        for f in fields(node):
            find_onfailure(getattr(node, f.name, None), seen)

    def find_transactions(node, seen):
        if node is None or id(node) in seen:
            return
        seen.add(id(node))
        if isinstance(node, (list, tuple)):
            for item in node:
                find_transactions(item, seen)
            return
        if not is_dataclass(node):
            return
        if isinstance(node, TransactionBlock):
            find_onfailure(node.body, set())
            return   # do not also scan for nested transactions inside this one
        for f in fields(node):
            find_transactions(getattr(node, f.name, None), seen)

    find_transactions(getattr(program, 'statements', None) or [], set())
    return warnings


WARNING_SCANS = (
    scan_sector_route_unauthenticated,
    scan_unreachable,
    scan_unwired,
    scan_typos,
    scan_audit_grade_requirement,
    scan_miofile_zone_coverage,
    scan_grant_role_client_source,
    scan_cookie_samesite_none_insecure,
    scan_transaction_onfailure_futile,
)


def run_scans(program):
    """Run every canonical scanner. Returns (errors, warnings).

    A scanner must never take down the check: it reports, or it says nothing. But it must
    also never be SKIPPED because a caller forgot it existed -- which is exactly what a
    hand-copied list guarantees will happen eventually.
    """
    errors, warnings = [], []
    for scan in ERROR_SCANS:
        try:
            errors.extend(scan(program))
        except Exception:
            pass
    for scan in WARNING_SCANS:
        try:
            warnings.extend(scan(program))
        except Exception:
            pass
    return errors, warnings
