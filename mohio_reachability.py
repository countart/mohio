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

import re

from dataclasses import fields, is_dataclass

from mohio_ast import GiveBackStmt, HaltStmt, CheckBlock
from mohio_transformer import CompileWarning, CompileError

_HARD_RETURN = (GiveBackStmt, HaltStmt)


def _is_unconditional_return(node):
    """True only for a `give back` / `halt` with no trailing if/unless qualifier."""
    return (isinstance(node, _HARD_RETURN)
            and getattr(node, "qualifier", None) is None)


# A routed unit is ASSEMBLED, not executed in sequence, so a hard return earlier in the
# same list does not make it dead: the router reaches it directly when a request arrives.
# This became reachable-in-practice when `page` was removed -- a convention home page is a
# top-level `give back`, and the section routes that follow it serve normally (verified by
# real HTTP against a file with both). Warning on them would flag correct code as dead.
_ROUTED_UNITS = ('ListenBlock', 'JourneyDecl')


def _is_routed_unit(node):
    return type(node).__name__ in _ROUTED_UNITS


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
                dead = next((s for s in seq[i + 1:] if not _is_routed_unit(s)), None)
                if dead is None:
                    break
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
    # CURRENCY TYPES. Each formats and rounds to its own places, and most are built on dec.2 --
    # but not all: `jpy` and `krw` have no minor unit and round to whole units, which is why
    # this is a list of currencies rather than an alias for one decimal shape.
    'usd', 'eur', 'gbp', 'jpy', 'chf', 'cad', 'aud', 'cny', 'hkd', 'sgd', 'inr', 'nzd', 'sek', 'nok', 'mxn', 'brl', 'zar', 'krw',
    # `as table` (Phase 2, recovered shape model). `as` describes what a thing IS, and that
    # covers "this is a table" exactly as it covers "this is text" -- it was never only a
    # naming word. A table is a NATURE here, not a scalar type: it opens a field scope rather
    # than describing a value.
    'table',
    # PHASE 3. `number` is a NATURE, not a precision. `47` and `4.567` are both just number,
    # and a shape says what a thing IS, never how many places it prints to. Refinement is a
    # reformatting operation at the POINT OF USE -- `(total as.dec.2)` -- which already works
    # and already reuses the name rather than creating a second one.
    #
    # UN-RETIRED, deliberately and on a ruling. `number` used to fail loud pointing at
    # `int`/`dec`, which reads as a correction and is really a category error: it answered
    # "what nature is this" with "choose a storage precision". `int` and `dec` stay valid --
    # they are natures too, the corpus is full of them, and removing them would not be additive.
    'number',
}
# Retired: say so by name instead of a generic "unknown type".
_RETIRED_TYPES = {
    'num':    'number (a nature -- 47 and 4.567 are both number), or int / dec if you '
              'genuinely mean whole-only or fractional',
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

def scan_agent_tool_grants(program):
    """An `ai.agent` tools grant must name a connector, and an operation, that exists.

    THE GRANT LIST IS THE SECURITY BOUNDARY. It is the whole statement of what an autonomous
    agent is allowed to reach, so a grant list that accepts names nobody declared is not a grant
    list, it is a list. `tools / Nope.refund / tools: done` used to pass `mio check` with no
    errors at all.

    The refusal already existed, at agent SETUP, inside `_agent_tool_schemas`. That is the right
    place to keep it and it stays there: it is the last line before a tool is handed to a model,
    and a runtime that trusts a check is a runtime that can be reached another way. What was
    missing is that the same mistake was invisible until the code ran, which for an agent means
    until whatever schedule or request first wakes it.

    The two are the same rule read at two times, deliberately: this one so a typo is a compile
    error, that one so it is never merely a compile error.

    SCOPED TO THE BLOCK FORM, which is the form that carries a real grant. `tools <name>` written
    inline on one line lands in the body as an unwired tree and is already reported by the
    not-built scan, so validating it here would produce a second, more confusing message about a
    construct that does not run at all yet.
    """
    errors = []
    stmts = getattr(program, 'statements', None) or []
    connectors = {}

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
            name = str(getattr(node, 'name', '') or '')
            if name:
                connectors[name] = [str(getattr(op, 'name', '') or '')
                                    for op in (getattr(node, 'operations', None) or [])]
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
        if type(node).__name__ == 'AiAgentBlock':
            agent = str(getattr(node, 'name', '') or '')
            line = getattr(node, 'line', 0) or 0
            # Named rather than written as `join(...) or "(none)"`: an empty join is an
            # empty STRING, and the reader of the message needs to be told that the
            # program declares no connectors at all, which is different from a list that
            # happened to render blank.
            known = ", ".join(sorted(connectors)) if connectors else "(none declared)"
            for grant in (getattr(node, 'tools', None) or []):
                grant = str(grant)
                # An `mioai.` grant is a built-in, not a connector operation, and the runtime
                # skips it for the same reason.
                if grant.startswith('mioai.'):
                    continue
                conn_name = grant.split('.', 1)[0] if '.' in grant else grant
                if conn_name not in connectors:
                    errors.append(CompileError(
                        f"ai.agent `{agent}` grants the tool `{grant}`, and no connector named "
                        f"`{conn_name}` is declared. Declared connectors: {known}.",
                        line=line,
                        hint=(f"Declare it with `mioconnect {conn_name} ... mioconnect: done` "
                              f"before granting it, or correct the name. A grant list is what "
                              f"the agent is allowed to reach, so a name in it that reaches "
                              f"nothing is either a typo or a permission nobody wrote.")))
                    continue
                if '.' in grant:
                    op_name = grant.split('.', 1)[1]
                    if op_name not in connectors[conn_name]:
                        _ops = connectors[conn_name]
                        ops = ", ".join(_ops) if _ops else "(none)"
                        errors.append(CompileError(
                            f"ai.agent `{agent}` grants `{grant}`, and connector `{conn_name}` "
                            f"has no operation `{op_name}`. Its operations: {ops}.",
                            line=line,
                            hint=(f"Name one of that connector's operations, or grant the "
                                  f"connector bare (`{conn_name}`) to allow all of them.")))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    for s in stmts:
        collect(s)
    for s in stmts:
        walk(s)
    return errors


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


# ── RECORDED AND ANNOUNCED ARE DIFFERENT PROPERTIES ────────────────────────────────────────
#
# `test_unbuilt_failloud_backlog.py` enforces that every deferral is RECORDED in the backlog. It
# never asked whether any of them is VISIBLE at CHECK TIME, and they were not: `rate limit 5 per
# second` checked clean, with `--security` clean and `--json` reporting zero errors, and then
# refused at RUN. `rate limit` is a SECURITY control, and `--json` is the documented machine
# surface for CI, so a pipeline could certify a program that will not start.
#
# WHAT THIS IS, PRECISELY. A construct whose executor is NOTHING BUT a deferral raise cannot do
# anything at run except refuse. Whether it will refuse is therefore a WHOLLY STATIC fact: the
# node is in the tree, so the answer is known before the program runs. Announcing it at check is
# not a prediction, it is reading what is already there.
#
# DERIVED, NOT INVENTED. Every kind below was read out of `mohio_interpreter.py` by parsing it:
# an `_exec_<Kind>` whose entire body is one `raise` carrying a not-built sentence. The gate in
# `tests/test_unbuilt_failloud_backlog.py` re-derives that set on every run and fails if it does
# not match this dict exactly, so a deferral added later cannot be silent at check -- and one
# that gets BUILT cannot be left announced as unbuilt either.
#
# THE LIST LIVES HERE RATHER THAN IN THE INTERPRETER because `mio check` does not import the
# interpreter and should not start: it is 22,000 lines, and paying that on every check to read
# sixteen names is the wrong trade. The gate is what keeps the two honest, which is the same
# arrangement every other baseline in this repo uses.
#
# THE WORD, NOT THE WHOLE SENTENCE. Copying each runtime message here would be two copies of the
# same text drifting apart. The check message says which construct and which KIND of unavailable
# it is; the run message keeps its own fuller explanation of what would have gone wrong.
DEFERRED_CONSTRUCTS = {
    'AiOverrideStmt':     ('ai.override',      'unbuilt'),
    'BroadcastStmt':      ('broadcast',        'unbuilt'),
    'ChangeBlock':        ('change to sh.X',   'unbuilt'),
    'CmNotifyStmt':       ('cm.notify',        'commercial'),
    'CmReportStmt':       ('cm.report',        'commercial'),
    'EnterpriseBlock':    ('enterprise',       'unbuilt'),
    'FromConnectorBlock': ('from <connector>', 'unbuilt'),
    'MiomapDecl':         ('miomap',           'unbuilt'),
    'MiopdfDecl':         ('miopdf',           'unbuilt'),
    'MiotestDecl':        ('miotest',          'unbuilt'),
    'NotifyStmt':         ('notify',           'unbuilt'),
    'PatternDecl':        ('pattern',          'unbuilt'),
    'RateLimitDecl':      ('rate limit',       'unbuilt'),
    'SendStmt':           ('send',             'unbuilt'),
    'StreamStmt':         ('stream',           'unbuilt'),
    'VerifyTokenStmt':    ('verify token',     'unbuilt'),
}

# THE CLAUSE DEFERRALS, which are a different shape and needed naming separately. `find` and
# `retrieve` each declare what their executor CONSUMES, and refuse anything else rather than
# return rows that look like they honoured a clause they dropped. So `cache for 5 minutes` on a
# find is a deferral too, and it was silent at check for the same reason the node-level ones
# were.
#
# MIRRORED EXACTLY, AND THE EXCLUSIONS MATTER MORE THAN THE LIST. Paginate, Skip, Cursor and Sql
# are handled further down the interpreter's own method, so they are NOT dropped clauses; a check
# that flagged them would turn four working forms into false refusals, which is this project's
# recorded way of getting a consumed-list wrong (an earlier allowlist missed three clauses and
# disabled calculate, summarize and return). The gate compares both lists against the
# interpreter's own literals for exactly this reason.
# A CONSTRUCT WITH NO EXECUTOR AT ALL, which is a different shape from the sixteen above and
# was invisible to the rule that finds them. Those are executors whose whole body is a deferral
# raise; these have no `_exec_` method in the interpreter, so there is nothing to read a sentence
# out of. They reach the runtime's generic no-executor fallback instead, which says "this
# construct is not executable in this build" and names the AST class rather than anything the
# developer wrote.
#
# `MapDecl` COVERS TWO OF MAP'S THREE FORMS, measured 2026-09-15: the value-to-value entry form
# (`map Names / "a" -> "b" / map: done`) and the action form (`map raw through Names as tidy`).
# Both parse, both check clean, and both stop at run. The third form, the one with `route` and
# `data` sections, is a different node and works on every engine.
#
# THE GATE CHECKS THE OTHER DIRECTION HERE. For the deferral registry it re-derives the set from
# the interpreter and demands an exact match; for this one it asserts that each kind named really
# has no `_exec_` method, so an entry cannot outlive the construct being built.
NO_EXECUTOR_CONSTRUCTS = {
    'MapDecl': (
        "a `map` written as value-to-value entries, and `map <value> through <name> as <alias>`",
        "Use the section form, which is built and runs on every engine:\n"
        "        map Paydata\n"
        "            data\n"
        "                order.total -> db.ledger.amount\n"
        "        map: done",
    ),
}

_FIND_CONSUMED = (
    'WhereClause', 'AndClause', 'TimespanRef', 'MatchClause', 'MatchBlock',
    'MatchAnyBlock', 'NoMatchBlock', 'LimitClause', 'OrderClause', 'ExportClause',
    'CalculateBlock', 'SummarizeBlock', 'ReturnClause',
)
_RETRIEVE_CONSUMED = ('MatchClause', 'MatchBlock', 'MatchAnyBlock', 'NoMatchBlock')
_QUERY_LATER = ('Paginate', 'Skip', 'Cursor', 'Sql')

# The spoken word for a clause, so check names `cache` where the developer wrote `cache for`,
# rather than `CacheClause`. Same table the interpreter's own refusal reads from.
_CLAUSE_WORDS = {
    'WhereClause': 'where', 'AndClause': 'and', 'OrClause': 'or',
    'LimitClause': 'up to', 'OrderClause': 'order', 'SkipClause': 'skip',
    'PaginateClause': 'paginate by', 'CacheClause': 'cache', 'ExportClause': 'export',
    'ReturnClause': 'return', 'JoinBlock': 'join', 'SummarizeBlock': 'summarize',
    'CalculateBlock': 'calculate', 'InjectClause': 'inject', 'SinceClause': 'since',
    'TimespanRef': 'timespan',
}


def _deferred_construct_error(kind, line):
    """The check-time sentence for a construct whose executor can only refuse.

    THREE KINDS OF UNAVAILABLE, kept apart because they are different situations for the person
    reading them: a thing that is not built yet is something to wait for, a commercial capability
    is something to buy, and a mistake is something to fix. Collapsing them into one message is
    how a licensed product reads as a missing feature.
    """
    # THE NO-EXECUTOR CASE GETS ITS OWN SENTENCE, because the useful thing to say about it is
    # not the same. A deferral has nothing to offer instead; here one FORM of the construct is
    # missing while another does the job, so the message names the form that works rather than
    # telling someone to go and find one.
    if kind in NO_EXECUTOR_CONSTRUCTS:
        what, instead = NO_EXECUTOR_CONSTRUCTS[kind]
        return CompileError(
            "%s is declared in the grammar but is not built in this release: nothing runs it, so "
            "it stops at the first line that reaches it." % what,
            line=line, hint=instead)
    word, tier = DEFERRED_CONSTRUCTS[kind]
    if tier == 'commercial':
        return CompileError(
            "%s is a planned commercial capability and does not run on the open compiler. "
            "It refuses at run rather than appear to work, so this names it now. To proceed: "
            "remove %s, or run it under a Mohio commercial license." % (word, word), line=line)
    return CompileError(
        "%s is declared in the grammar but not built in this release. It refuses at run rather "
        "than silently do nothing, so this names it before you deploy. To proceed: remove %s "
        "for now, or use a built alternative." % (word, word), line=line)


def scan_deferred_constructs(program):
    """Everything the runtime can only refuse, said at check time instead of at run.

    THE PROMISE THIS COMPLETES. The compiler already REFUSES programs that are WRONG -- an
    ungated AI decision, an unhashed password, a never-store violation -- and it does that at
    check, before anything deploys. What it did not do was warn about programs that are INERT:
    ones that parse, check clean, and then refuse the moment they run. Both halves belong to
    "check catches it before you deploy", and only the first half was there.
    """
    errors = []
    seen = set()

    def flag(kind, line):
        if (kind, line) in seen:
            return
        seen.add((kind, line))
        errors.append(_deferred_construct_error(kind, line))

    def query_clauses(node, kind):
        consumed = _FIND_CONSUMED if kind == 'FindBlock' else _RETRIEVE_CONSUMED
        for b in (getattr(node, 'body', None) or []):
            bk = type(b).__name__
            if bk in consumed or any(k in bk for k in _QUERY_LATER):
                continue
            word = _CLAUSE_WORDS.get(bk, bk)
            verb = 'find' if kind == 'FindBlock' else 'retrieve'
            line = getattr(b, 'line', 0) or getattr(node, 'line', 0) or 0
            errors.append(CompileError(
                "%s: `%s` is declared but not yet built on `%s` -- the runtime would "
                "return rows without applying it, so it refuses instead. To proceed: "
                "remove the clause, or use a form that applies it."
                % (verb, word, verb), line=line))

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
        if kind in DEFERRED_CONSTRUCTS or kind in NO_EXECUTOR_CONSTRUCTS:
            flag(kind, getattr(node, 'line', 0) or 0)
        elif kind in ('FindBlock', 'RetrieveBlock'):
            query_clauses(node, kind)
        for f in fields(node):
            walk(getattr(node, f.name, None))

    walk(program)
    return errors


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
    # THE MAP-DRIVING FAMILY, added 2026-09-15 after a sweep for the same shape. `walk 5` and
    # `flow 5` silently declared a variable and printed it, while `save 5` had always been
    # refused: same class, three words short. It matters more for these two than for most,
    # because the thing a reader is most likely to write next to them is a map name, and a
    # construct word that quietly becomes a variable takes the statement with it.
    #
    # `walk m.stage` itself was never the problem in this tree -- WALK is a priority terminal, so
    # the construct wins wherever a map path follows it. What was absorbable is the bare word in
    # an assignment position, which is the half a lexer priority cannot decide.
    'flow', 'walk', 'journey',
    # `page` IS RETIRED, and this is the only place that can say so safely. A line scan
    # cannot tell `page 5` from a save block's `page "bump"` field, and refusing the
    # second would break a real program over a normal column name. This scan works on
    # the tree, where an assignment and a field are different things.
    'page',
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
    'flow':   'flow <map>[.<chain>]           (drives a declared map)',
    'walk':   'walk <map>[.<chain>] ... walk: done',
    'journey':'journey <Name> ... journey: done',
    'page':   'the page block is retired -- a file serves itself, and several addresses are named with `listen for ... request for sh.X at /path`',
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
            # `expect` IS ONLY A WORD INSIDE A TEST CASE. The grammar reaches `expect_stmt`
            # from `it_body` and nowhere else, so one indent short of a case the line resolves
            # into assignments instead: `expect total is 5` becomes a variable `expect` holding
            # `total`, plus a variable `is` holding 5, with no error. The assertion is gone and
            # nothing said so. Separate message from the block openers below, because the fix is
            # not "write the block form", it is "put this in a case".
            if name == 'expect':
                errors.append(CompileError(
                    "`expect` only means something inside a test case, and this line is not in "
                    "one.\n"
                    "    Written here it declares a VARIABLE called `expect`, so the assertion "
                    "you wrote is not in the program at all and nothing would have checked it.\n"
                    "    The form is:  it \"what this proves\"\n"
                    "                      expect <name> is <value>\n"
                    "                  it: done\n"
                    "    Run the cases with:  mio test <file>",
                    line=getattr(node, 'line', 0) or 0))
            elif name in BLOCK_OPENERS:
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
            # EVERY field, loose or table-owned -- an upload field declared under a
            # `<name> as table` scope is still an upload field. See ShapeDecl.every_field.
            for fld in (node.every_field() if hasattr(node, 'every_field')
                        else (getattr(node, 'fields', None) or [])):
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



def scan_query_connection_name(program):
    """A query against a connection named anything but the default is refused HERE, not at
    runtime with a message that used to be false.

    Every query call site resolves the hardcoded default `'db'`, so
    `connect primary as sqlite ...` opened a real connection and `find a in primary.orders`
    answered "needs a database connection, but none is open" -- while one was. The runtime
    message is correct now, but this is statically visible: the connect name and the query
    source are both in the source text, so the standing rule puts the refusal at check time,
    where a developer decides whether to deploy.

    Only fires when the name IS a declared connection. An unknown prefix is somebody else's
    error (an undeclared connector, a shape ref, a held list) and is not claimed here.
    """
    errors = []
    declared = {}

    def collect(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                collect(c)
            return
        if not is_dataclass(node):
            return
        if type(node).__name__ == 'ConnectDecl':
            n = str(getattr(node, 'name', '') or '')
            if n:
                declared[n] = node
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
        src = getattr(node, 'source', None)
        parts = getattr(src, 'parts', None)
        if parts and len(parts) >= 2:
            head = str(parts[0])
            if head in declared and head != 'db':
                errors.append(CompileError(
                    f"This queries `{head}.{parts[1]}`, but the query layer does not resolve "
                    f"a connection name other than the default yet. `{head}` IS declared and "
                    f"really does open a connection -- so this would fail when it ran, not "
                    f"here. Today, name the connection you query `db` "
                    f"(`connect db as ...`) and refer to it as `db.{parts[1]}`. Naming "
                    f"connections is a real declared form that is not wired through the "
                    f"query layer yet; this is not a rule that connections must be called "
                    f"`db`.",
                    line=getattr(node, 'line', 0) or 0))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    for st in (getattr(program, 'statements', None) or []):
        collect(st)
    for st in (getattr(program, 'statements', None) or []):
        walk(st)
    return errors



def scan_connection_access_mode(program):
    """`readonly` refuses writes, `writeonly` refuses reads -- at COMPILE time.

    DESIGNED AND LOCKED 2026-06-08 as compile-time enforcement, and never wired: the grammar
    parsed `conn_access` and `ConnectDecl` had no field to put it in, so every access mode ever
    written was discarded at the transformer. Measured 2026-09-02: a `save` to a `readonly`
    connection wrote the row, printed nothing, and `mio check` reported no errors. A declared
    constraint that enforces nothing is the decorative-constraint class, and on a connection it
    is the kind a reviewer would reasonably take for a real guarantee.

    Enforced statically, which is where the design put it and where it is reachable today with
    a single connection: the mode and the verb are both visible in the source.
    """
    errors = []
    WRITE_VERBS = {
        'SaveBlock': 'save', 'SaveAllBlock': 'save all', 'UpdateBlock': 'update',
        'RemoveBlock': 'remove', 'RemoveAllBlock': 'remove all',
        'SaveOrUpdateBlock': 'save or update',
    }
    READ_VERBS = {
        'FindBlock': 'find', 'RetrieveBlock': 'retrieve', 'GrabBlock': 'grab',
        'PullBlock': 'pull', 'GetBlock': 'get',
    }
    modes = {}
    all_conns = []

    def collect(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                collect(c)
            return
        if not is_dataclass(node):
            return
        if type(node).__name__ == 'ConnectDecl':
            nm = str(getattr(node, 'name', '') or '')
            all_conns.append(nm)
            acc = str(getattr(node, 'access', '') or '').lower()
            if acc in ('readonly', 'writeonly'):
                modes[nm] = acc
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
        kind = type(node).__name__
        verb = WRITE_VERBS.get(kind) or READ_VERBS.get(kind)
        if verb:
            # A read names its source as `db.table` (parts), but a WRITE's target is a DbRef
            # that carries only the table -- the connection prefix is dropped in the AST,
            # because every call site resolves the single default connection anyway (the same
            # unfinished wiring E1 reports). So an unqualified verb belongs to the one declared
            # connection whenever there is exactly one, which is the case this is reachable in
            # today and the case the design was written for.
            src = getattr(node, 'source', None) or getattr(node, 'target', None)
            parts = getattr(src, 'parts', None) or []
            conn = str(parts[0]) if parts else ""
            if not conn and len(set(all_conns)) == 1:
                conn = all_conns[0]
            mode = modes.get(conn)
            if mode == 'readonly' and kind in WRITE_VERBS:
                errors.append(CompileError(
                    f"`{verb}` writes to `{conn}`, which is declared `readonly`. A readonly "
                    f"connection refuses save, update, upsert and remove -- that is what the "
                    f"word is for. Either drop `readonly` from the connect line, or do this "
                    f"write against a connection that allows it.",
                    line=getattr(node, 'line', 0) or 0))
            elif mode == 'writeonly' and kind in READ_VERBS:
                errors.append(CompileError(
                    f"`{verb}` reads from `{conn}`, which is declared `writeonly`. A writeonly "
                    f"connection refuses find, retrieve, grab, pull and get. Either drop "
                    f"`writeonly` from the connect line, or read from a connection that "
                    f"allows it.",
                    line=getattr(node, 'line', 0) or 0))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    for st in (getattr(program, 'statements', None) or []):
        collect(st)
    for st in (getattr(program, 'statements', None) or []):
        walk(st)
    return errors



def scan_multi_database_reference(program):
    """`db.<database>.<table>` is not resolved yet, and it must say so HERE, not at runtime.

    MEASURED 2026-09-03. The DB_REF terminal already accepts arbitrary dotted depth
    (a dotted terminal with no depth limit), so `db.sales.people` PARSES today, `mio check` reports
    NO ERRORS, and the program then dies at runtime with:

        db_error: no such table: sales

    The developer never wrote a table called `sales`. They wrote a database and a table, the
    compiler kept the string, something downstream split it, and the error named a thing that
    does not appear in their source. A confidently wrong message is worse than a blunt one,
    and `mio check` saying nothing at all is what lets it reach runtime.

    Multiple databases on one connection is a REAL part of the recovered design -- the registry
    is already by name (`set_connection(name, c)`, `get_connection(name='db')`) and the MVP
    flattened it to the hardcoded default. This refusal is the honest placeholder until the
    declaration form is ruled: it does not invent a form, it stops the compiler from pretending
    the reference means something it does not.

    ZERO corpus files use a three-segment reference (measured before landing), so nothing that
    works today stops working -- `db.<table>` is untouched and is the compatibility subset.
    """
    errors = []

    def walk(node, pos='top'):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                walk(c, pos)
            return
        if not is_dataclass(node):
            return
        # TWO NODE SHAPES, and finding that out is the reason this is verified through the
        # real CLI rather than a direct transform() call. Building the program in-process gave
        # a `DbRef(table='warehouse.orders')`; the actual `mio run` / `mio check` path gives a
        # `DottedName(parts=['db','warehouse','orders'])` for the SAME source line. A scanner
        # written against the in-process shape alone runs, sees nothing, and reports clean --
        # which is what it did until this was probed on the real path.
        _kind = type(node).__name__
        _multi = None
        if _kind == 'DbRef':
            tbl = str(getattr(node, 'table', '') or '')
            if '.' in tbl and '*' not in tbl:
                _multi = tbl
        elif _kind == 'DottedName':
            _parts = [str(x) for x in (getattr(node, 'parts', None) or [])]
            if len(_parts) >= 3 and _parts[0] == 'db' and '*' not in _parts:
                # PHASE 2 ITEM 2 RECONCILIATION. This scanner refused EVERY `db.a.b`, and the
                # ruled design assigns that exact shape to the canonical field reference:
                # `db.<table>.<field>` is how table content is reached, and the multi-database
                # form is `db.<dbname>.<table>.<field>` -- one segment DEEPER. So for a while
                # the one canonical spelling was the only one refused, while bare `Name.field`
                # and `table.field` both checked clean. Depth and POSITION separate them, and
                # both were established by running each verb rather than reasoned about:
                #   * `source` / `target` name a TABLE (find/retrieve/grab/pull/update/remove/
                #     save/save.all -- every one measured 2026-09-04). Three segments there is
                #     still database-plus-table and still unresolved, so it still refuses here.
                #   * anywhere else is a REFERENCE. Three segments there is `db.table.field`,
                #     which is canonical and belongs to scan_reference_rule, not to this one.
                #     Four or more is the multi-database form and is still unresolved.
                if pos in ('source', 'target') or len(_parts) >= 4:
                    _multi = '.'.join(_parts[1:])
        if _multi:
                tbl = _multi
                head = tbl.split('.', 1)[0]
                # THE ADVICE IS THE LAST SEGMENT, not everything after the first. It used to be
                # `rest`, which is right at three segments and WRONG at four or more: for
                # `db.sales.public.customers` it said "use `db.public.customers`", and that is
                # refused by this very scanner with the identical message. A refusal whose
                # suggested fix reproduces the refusal sends the reader in a circle, which is
                # worse than a refusal with no advice at all, because they trust it once first.
                table_only = tbl.rsplit('.', 1)[-1]
                errors.append(CompileError(
                    f"`db.{tbl}` names a database and a table, and the query layer does not "
                    f"resolve a database name yet -- it resolves the single default "
                    f"connection. Left alone this reaches the database as a table called "
                    f"`{head}`, which is not something you wrote, and the error you would get "
                    f"says `no such table: {head}`. Use `db.{table_only}` against the "
                    f"connection you declared. Naming several databases on one connection is a "
                    f"real declared part of the design that is not wired through the query "
                    f"layer yet; this is not a rule that there can only ever be one.",
                    line=getattr(node, 'line', 0) or 0))
        for f in fields(node):
            walk(getattr(node, f.name, None), f.name)

    for st in (getattr(program, 'statements', None) or []):
        walk(st)
    return errors


def _declared_shape_tables(program, duplicates=None):
    """Every table a shape declares, and the fields that belong to it. {table: (shape, [field])}

    Built from the ShapeTable hierarchy Phase 2 item 1 put in the AST, which is the only place
    that keeps `db.users.email` and `db.orders.email` apart.

    `duplicates`, when a list is passed in, collects every SECOND declaration of a table name
    rather than letting it merge into the first. Merging is what this did in its first version,
    and it is the collision bug wearing a different coat: two shapes declaring `users` would have
    quietly pooled their fields, so `db.users.email` resolved against a table neither shape
    actually described. scan_table_name_collision is the caller that passes the list.
    """
    out = {}

    def collect(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                collect(c)
            return
        if not is_dataclass(node):
            return
        if type(node).__name__ == 'ShapeDecl':
            owner = str(getattr(node, 'name', '') or '')
            for tname, tbl in (getattr(node, 'tables', None) or {}).items():
                tname = str(tname)
                flds = [str(getattr(f, 'name', '')) for f in (getattr(tbl, 'fields', None) or [])]
                if tname in out:
                    if duplicates is not None:
                        duplicates.append((tname, out[tname][0], owner,
                                           getattr(tbl, 'line', 0) or 0))
                    continue
                out[tname] = (owner, flds)
        for f in fields(node):
            collect(getattr(node, f.name, None))

    collect(program)
    return out


def scan_listener_with_no_handler(program):
    """A `listen for` whose body holds no handler at all mounts NOTHING, silently. Item 7.

    THE ENUMERATION, every arrangement run through `mio check` AND a real HTTP request:

        handler directly inside the container              MOUNTS      (the control)
        bare `give back`, no handler                       404, clean
        empty body                                         404, clean
        a `task` block only                                404, clean
        an `ai.decide` block plus a `give back`             404, clean
        handler wrapped in `check` / `try` / `repeat`       PARSE ERROR -- not a member

    That last row is the useful negative: `new sh.P at /x` inside a `try` does not parse at all
    ("No terminal matches '/'"), so a wrapped handler already fails loud and is not part of this
    class. The class is one rule, not four bugs: `_exec_ListenBlock` filters its candidates to
    NewBlock and RequestInboundBlock, and the grammar's `listener_body` also accepts `statement`
    -- which is every statement in the language. So a listener whose body is only statements has
    nothing to dispatch and answers nothing.

    SCOPED so a webhook listener is untouched. `connection_block`, `change_block` and
    `from_connector_block` (`from Stripe / when payment.succeeded`) are legitimate listener
    bodies that are not HTTP route handlers, so a listener holding one of those is left alone.
    Only a body that is ORDINARY STATEMENTS ALONE is refused.

    Zero .mho files in the tree contain a handler-less `listen for` (measured before landing).
    """
    HANDLERS = ('NewBlock', 'RequestInboundBlock')
    # Listener bodies that are legitimately NOT http route handlers. A `listen for` wrapping
    # MioScript client listeners is the plainest case: `listen for / listen for change on #inp`
    # compiles to BROWSER code and never mounts a server route, by design -- refusing it broke
    # three assertions in test_mioscript_unknown_value_failloud, which is what caught it.
    OTHER_LISTENERS = ('ConnectionBlock', 'ChangeBlock', 'FromConnectorBlock',
                       'ClientListener', 'ClientListenChange', 'ListenBlock')
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
        if type(node).__name__ == 'ListenBlock':
            _ls = getattr(node, 'listeners', None) or []
            kinds = [type(l).__name__ for l in _ls]
            # An untransformed Lark `Tree` in the listener list is a body this scanner cannot
            # classify -- `from Stripe / when payment.succeeded` arrives that way. Silence is
            # the only safe answer there: the gate's own `listen_from_connector` case is a
            # legitimate webhook listener with no HTTP handler in it, and refusing it would be
            # a false refusal on a working form.
            _unknown = any(not is_dataclass(l) for l in _ls)
            if not _unknown and not any(k in HANDLERS for k in kinds)                     and not any(k in OTHER_LISTENERS for k in kinds):
                what = ("is empty" if not kinds
                        else "holds only " + ", ".join(sorted(set(kinds))))
                errors.append(CompileError(
                    f"This `listen for` block {what}, so it registers no route and answers "
                    f"nothing -- a request to any path in it comes back with no route matching. "
                    f"`listen for` groups HANDLERS: put the work inside "
                    f"`new sh.<Shape> at /your/path` for a write or "
                    f"`request for sh.<Shape> at /your/path` for a read, and the path goes on "
                    f"the handler, unquoted.",
                    line=getattr(node, 'line', 0) or 0))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    for st in (getattr(program, 'statements', None) or []):
        walk(st)
    return errors


def scan_ai_decide_declared_but_never_invoked(program):
    """A declared `ai.decide` name that is READ but never INVOKED. Item 6, and it is a
    diagnostic gap rather than the binding bug it was reported as.

    MEASURED before building. The invoke form binds correctly:

        ai.decide risk returns boolean ... ai.decide: done
        ai.decide risk          <- the invocation
        show risk               -> True

    The DECLARATION deliberately does not run. That is the ruled
    T1-EVAL-AI-DECIDE-DECLARE-VS-INVOKE behaviour: Zork declares a block once at module scope
    with template vars that only exist later inside the handler that re-invokes it, so running
    the body at declaration time interpolated those to None and, on a live provider, spent a
    real paid AI call before the genuine invocation overwrote the result.

    So the name is unbound for a reason. What was missing is anyone SAYING so: declare, then
    read the name with no invocation between, and `mio check` reports clean and the program dies
    at runtime with `unknown variable 'risk'` -- a message about a variable, for a mistake about
    a block that was never run. The developer's actual error is one line from the declaration
    and nothing pointed at it.

    NARROW ON PURPOSE. It fires only when there is NO invocation of that name ANYWHERE in the
    program. Declaring at the top and invoking deep inside a handler is the documented pattern
    and must stay silent, so "invoked somewhere" is the whole test -- not "invoked before this
    line", which would refuse the pattern the split exists to support.
    """
    declared, invoked, read = {}, set(), {}

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
        if kind == 'AiDecideBlock':
            nm = str(getattr(node, 'name', '') or '')
            if nm:
                declared[nm] = node
        elif kind == 'AiDecideInvoke':
            nm = str(getattr(node, 'name', '') or '')
            if nm:
                invoked.add(nm)
        elif kind == 'DottedName':
            parts = [str(x) for x in (getattr(node, 'parts', None) or [])]
            if parts:
                read.setdefault(parts[0], node)
        elif kind == 'VarRef':
            nm = str(getattr(node, 'name', '') or '')
            if nm:
                read.setdefault(nm, node)
        for f in fields(node):
            walk(getattr(node, f.name, None))

    for st in (getattr(program, 'statements', None) or []):
        walk(st)

    errors = []
    for nm, decl in declared.items():
        if nm in invoked or nm not in read:
            continue
        errors.append(CompileError(
            f"`{nm}` is read, but the `ai.decide {nm}` block that declares it is never run. "
            f"Declaring an ai.decide block DEFINES it; it does not decide anything until it is "
            f"invoked. Add `ai.decide {nm}` where the decision should happen, then read `{nm}` "
            f"after it. Left as written this checks clean and then fails at run time saying "
            f"`{nm}` is an unknown variable, which describes the wrong problem.",
            line=getattr(read[nm], 'line', 0) or getattr(decl, 'line', 0) or 0))
    return errors


def scan_table_name_collision(program):
    """One table name, one owner. Two shapes declaring `users` refuse LOUDLY.

    PHASE 2 ITEM 4 of the recovered shape model. A table name is the whole address of a field:
    `db.users.email` says which table by name and nothing else. So if two shapes both declare
    `users`, that reference has two possible meanings and nothing in the source says which one
    wins. Every available outcome is bad -- last-wins silently discards a described table,
    first-wins silently discards the other, and merging (which the first version of the table
    collector actually did) invents a table neither shape describes and resolves references
    against it. There is no correct silent behaviour available, which is what makes this a
    refusal rather than a warning.

    WHERE A TABLE NAME CAN BE INTRODUCED, enumerated before wiring this rather than after, the
    same way the where-clause allowlist had to be:

      1. `<name> as table` inside a shape. The only declaration form that EXISTS today.
      2. Twice inside ONE shape -- already refused in the transformer (_split_shape_tables),
         where both names are in hand at construction time. Covered, and not re-checked here.
      3. Across two shapes in one program (including a shape pulled in by an include or a
         journey, because Layer 3 runs on the ASSEMBLED program). That is this scanner.
      4. A CONNECTION naming tables. NOT REACHABLE: `connect` accepts one name, one driver and
         one source, with no list and no `as table` binding anywhere in the grammar, so there
         is no slot a table name could be introduced from. The multi-database declaration form
         is a live fork awaiting a ruling, and the cross-connection half of this rule is not
         buildable ahead of it. Named here so the gap is a stated boundary rather than a silent
         one -- this scanner covers shapes, and only shapes.
    """
    dupes = []
    _declared_shape_tables(program, duplicates=dupes)
    errors = []
    for tname, first_owner, second_owner, line in dupes:
        a = f"`{first_owner}`" if first_owner else "an earlier shape"
        b = f"`{second_owner}`" if second_owner else "a later shape"
        errors.append(CompileError(
            f"Two shapes declare the table `{tname}`: {a} and {b}. A table name is the whole "
            f"address of a field -- `db.{tname}.<field>` names the table and nothing else -- so "
            f"two owners leave that reference with two meanings and no way to choose. Give one "
            f"of them a different table name, or describe `{tname}` in a single shape.",
            line=line))
    return errors


def _declared_shape_names(program):
    names = set()

    def collect(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                collect(c)
            return
        if not is_dataclass(node):
            return
        if type(node).__name__ == 'ShapeDecl':
            n = str(getattr(node, 'name', '') or '')
            if n:
                names.add(n)
        for f in fields(node):
            collect(getattr(node, f.name, None))

    collect(program)
    return names


def scan_reference_rule(program):
    """The PREFIX declares the kind, always. A bare `Name.field` does not say what it is reading.

    PHASE 2 ITEM 2 of the recovered shape model, and it is a rule about READABILITY before it is
    a rule about resolution: `db.users.email` says on its face that this is data, in a table, and
    which field. `users.email` says none of that, and the reader has to go find a declaration
    somewhere else in the file to learn whether `users` is a table, a shape, or a variable
    somebody happened to name `users`.

    MEASURED BEFORE BUILDING, 2026-09-04, and the state was exactly backwards -- the one
    canonical spelling was the only one refused:

        db.users.email       REFUSED (by the multi-database scanner, wrongly)
        sh.Person.email      checked clean
        Person.email         checked clean, then died at runtime: "variable 'Person' is not declared"
        users.email          checked clean, then died at runtime: "variable 'users' is not declared"

    The two bare forms are not silent -- they fail loud at runtime -- but they fail with a
    message that describes a variable nobody wrote, at run time rather than at check time, when
    the whole mistake is visible in the source text. That is the standing rule's case for moving
    a refusal to check time.

    WHAT IT REFUSES, deliberately narrow:
      * `db.<table>.<field>` where a shape declares `<table>` but not `<field>` -- and the
        message names the fields that table really has.
      * `db.<table>.<field>` where NO shape declares `<table>` at all.
      * a bare `<Head>.<field>` whose head is a declared SHAPE name or a declared TABLE name.

    WHAT IT LEAVES ALONE, which is the part that stops it becoming a false-refusal machine:
      * anything whose head is a BOUND VARIABLE. `retrieve customer from db.customers` binds
        `customer`, and `customer.email` is the ordinary, correct way to read that row. The
        variable always wins over the declaration -- it is a real value, and a shape name that
        is also a variable name is the author's business, not this scanner's.
      * `db.<table>` (two segments), `db.*`, `sh.<Name>`, and every reference whose head is not
        a declaration this program makes. An unknown prefix belongs to somebody else's error.

    ZERO corpus files across all 123 .mho contain a bare `<shape-or-table>.<field>` reference
    (measured over-inclusively before landing, so that number is an upper bound), which is why
    this can refuse rather than warn.
    """
    errors = []
    tables = _declared_shape_tables(program)
    shapes = _declared_shape_names(program)
    if not tables and not shapes:
        return errors

    # A name that is BOUND to a value is a variable, and a variable wins. Collected first and
    # from the whole program, because a refusal that fires on a legitimate `customer.email` is
    # the same false statement pointing the other way, and harder to catch: it looks like care.
    bound = set()

    def collect_bound(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                collect_bound(c)
            return
        if not is_dataclass(node):
            return
        kind = type(node).__name__
        if kind != 'ShapeDecl':
            for attr in ('name', 'as_name', 'alias', 'var', 'result_name'):
                v = getattr(node, attr, None)
                if isinstance(v, str) and v:
                    bound.add(v)
        for f in fields(node):
            collect_bound(getattr(node, f.name, None))

    collect_bound(program)

    def walk(node, pos='top'):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                walk(c, pos)
            return
        if not is_dataclass(node):
            return
        if type(node).__name__ == 'DottedName':
            parts = [str(x) for x in (getattr(node, 'parts', None) or [])]
            ln = getattr(node, 'line', 0) or 0
            if '*' not in parts and pos not in ('source', 'target'):
                if len(parts) == 3 and parts[0] == 'db':
                    tbl, fld = parts[1], parts[2]
                    if tbl not in tables:
                        errors.append(CompileError(
                            f"`db.{tbl}.{fld}` reads the field `{fld}` from a table `{tbl}`, "
                            f"and no shape declares a table called `{tbl}`. Declare it with "
                            f"`{tbl} as table` inside a shape and put `{fld}` under it. If "
                            f"`{tbl}` was meant as a DATABASE name, that form is "
                            f"`db.{tbl}.<table>.<field>` and it is not wired through the query "
                            f"layer yet.",
                            line=ln))
                    elif fld not in tables[tbl][1]:
                        # An empty scope is a REAL, legal state (`users as table` with nothing
                        # under it yet), not a lookup that failed, so the two cases are branched
                        # rather than collapsed into an `or` default. The silent-shape ratchet
                        # caught the `or` version of this line on its first run and it was right
                        # to: a reader cannot tell a rendered empty list from a fallback that
                        # fired because something upstream returned nothing.
                        cols = tables[tbl][1]
                        real = ", ".join(cols) if cols else "no fields yet"
                        owner = tables[tbl][0]
                        where = f"`{owner}`" if owner else "an unnamed shape"
                        errors.append(CompileError(
                            f"`db.{tbl}.{fld}` reads a field `{fld}` that the table `{tbl}` "
                            f"does not declare. {where} declares `{tbl}` with: {real}.",
                            line=ln))
                elif len(parts) >= 2 and parts[0] not in ('db', 'sh') and parts[0] not in bound:
                    head = parts[0]
                    if head in tables:
                        errors.append(CompileError(
                            f"`{head}.{parts[1]}` does not say what it is reading. `{head}` is "
                            f"a table a shape declares, and table content is always reached "
                            f"through `db.` -- write `db.{head}.{parts[1]}`. The prefix is what "
                            f"tells a reader this is data.",
                            line=ln))
                    elif head in shapes:
                        errors.append(CompileError(
                            f"`{head}.{parts[1]}` does not say what it is reading. `{head}` is "
                            f"a shape, and a shape is reached through `sh.` -- write "
                            f"`sh.{head}`. If `{parts[1]}` is data in a table, reach it through "
                            f"`db.<table>.{parts[1]}` instead: data is always reached through "
                            f"`db.`, even when a shape governs it.",
                            line=ln))
        for f in fields(node):
            walk(getattr(node, f.name, None), f.name)

    for st in (getattr(program, 'statements', None) or []):
        walk(st)
    return errors


def scan_task_param_undeclared_shape(program):
    """A task parameter typed as a shape that does not exist is refused, like the listener path.

    THE SILENT FAILURE. `take request as sh.NoSuchShape` reported no errors, RAN, and gave back
    an empty value. The contract the developer declared was simply absent, and nothing said so
    anywhere. Found while measuring what migrating a shape into a table scope would cost, since
    that migration renames the shape and every reference to it then names nothing.

    IT WAS A WARNING FIRST, and the record of why is worth keeping. Making it a refusal broke
    three things on the first attempt: two grammar-gate fixtures that declared a task taking
    `sh.Transaction` with no shape, because they were testing the grammar of a parameter and
    nothing else, and `tests/invoice_saga.mho:84`, which took `sh.Customer` with no
    `shape Customer` declared anywhere. Additive-first says a change that cannot be made
    additively stops as a fork rather than breaking the corpus, so it stopped and the finding
    was reported.

    All three are now FIXED rather than exempted. invoice_saga really did need that shape: it
    reads `customer.email` to send the order confirmation, so the parameter was carrying no
    contract at all. The two fixtures declare the shape their parameter names, which leaves them
    testing exactly the grammar they were written for. Nothing is special-cased, and the rule
    reads the same on every path a shape can be referenced from.
    """
    declared = set()
    refs = []

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
        if cls == 'TaskParam':
            # `type_name` also holds ordinary types (`text`, `int`), so only the `sh.` form is
            # a shape reference.
            tn = str(getattr(node, 'type_name', '') or '')
            if tn.startswith('sh.'):
                refs.append((tn.split('.')[-1], getattr(node, 'line', 0)))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    walk(program)
    errors = []
    for name, line in refs:
        if name and name not in declared:
            known = ", ".join(sorted(declared)[:6]) if declared else "(none declared)"
            errors.append(CompileError(
                f"`sh.{name}` is used as a task parameter type but no shape named `{name}` is "
                f"declared, so this parameter would carry NO contract: none of the fields, "
                f"types or validation a shape gives it would be applied. "
                f"Declared shapes: {known}.",
                line=line,
                hint=(f"Declare it with `shape {name} ... shape: done`, or name a shape that "
                      f"exists. This is the rule the request path already enforces: a reference "
                      f"to a shape that is not there is a contract that is not there.")))
    return errors


# ── a table where a value belongs ─────────────────────────────────────────
# `x db.members` used to run and die with `variable 'db' is not declared`, which was false: db
# was declared one line above, as a CONNECTION. The resolver looked in the variable scope, did
# not find it there, and reported its own search rather than the question asked. Both halves of
# the real mistake are visible at check time, so it is refused here.

# The positions where a VALUE is unambiguously being read. Short on purpose: see the module
# note above on why this is a deny-list and not an allow-list of table targets.
_TABLE_VALUE_SLOTS = {'Assignment': 'value', 'ShowStmt': 'value'}


def scan_table_as_value(program):
    """A table reference in a value position is a category error, not a missing variable."""
    errors = []
    stmts = getattr(program, 'statements', None) or []
    connections = set()

    def collect(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                collect(c)
            return
        if not is_dataclass(node):
            return
        if type(node).__name__ == 'ConnectDecl':
            n = getattr(node, 'name', '')
            if n:
                connections.add(str(n))
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
        slot = _TABLE_VALUE_SLOTS.get(type(node).__name__)
        if slot is not None:
            val = getattr(node, slot, None)
            parts = [str(p) for p in (getattr(val, 'parts', None) or [])]
            # EXACTLY TWO. `db.members` is a table; `db.users.email` is a FIELD and a
            # perfectly good value, and refusing it on depth alone is the bug
            # test_battery_reference_rule was written to stop. Deeper forms are left to the
            # scanners that already own them.
            if (len(parts) == 2 and parts[0] in connections
                    and type(val).__name__ in ('DottedName', 'DbRef')):
                ref = '.'.join(parts)
                errors.append(CompileError(
                    f"`{ref}` is a table, not a value -- `{parts[0]}` is a connection, so "
                    f"there is no value of `{ref}` to read here.",
                    line=getattr(val, 'line', 0) or getattr(node, 'line', 0) or 0,
                    hint=(f"A table is a place you read from or write to. Read rows with "
                          f"`find rows in {ref}` or `retrieve one from {ref}`; write with "
                          f"`save to {ref}`. A table cannot be assigned or shown directly.")))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    for s in stmts:
        collect(s)
    for s in stmts:
        walk(s)
    return errors


def scan_undeclared_maps(program):
    """A `flow`, `walk`, `check flow` or `grab stage` must name a map that is declared.

    WHOLLY STATIC, WHICH IS WHY IT BELONGS HERE. A map is declared in the same file that drives
    it -- `map paydata ... map: done` -- so whether the name exists is knowable before anything
    runs. It was not being asked: `flow m.stage` checked clean and then refused at run, on a
    program with no database in it, which is the same check-says-nothing-run-refuses split this
    pass exists to close.

    ALL FOUR CONSUMERS, not the two that were reported. `flow` and `walk` share MAP_STAGE_PATH in
    the grammar and were the two named, but `check flow` and `grab stage` take a map name by the
    same route and had the same silence. A fix applied to the two that were noticed would have
    left the family half-done, which is how this class regrows one member at a time.
    """
    MAP_USERS = {'FlowStmt': 'flow', 'WalkStmt': 'walk',
                 'CheckFlowStmt': 'check flow', 'GrabStageStmt': 'grab stage'}
    errors = []
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
        # BOTH DECLARATION FORMS, and finding that out cost a false refusal in testing. `map
        # paydata / data / ... / map: done` is a MapSectionsDecl, not a MapDecl, so a scan that
        # knew only the second told a program with its map declared six lines above that no such
        # map existed. A `MapDecl` in its ACTION form (`map source through name`) carries no
        # `name` and correctly declares nothing.
        if type(node).__name__ in ('MapDecl', 'MapSectionsDecl'):
            n = str(getattr(node, 'name', '') or '')
            if n:
                declared.add(n)
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
        kind = type(node).__name__
        if kind in MAP_USERS:
            name = str(getattr(node, 'map_name', '') or '')
            # A NAME THE TRANSFORMER COULD NOT READ is not a missing map. Reporting an empty name
            # as undeclared would turn a parse-shape problem into a confident wrong diagnosis.
            if name and name not in declared:
                verb = MAP_USERS[kind]
                errors.append(CompileError(
                    "%s: no map named `%s` is declared." % (verb, name),
                    line=getattr(node, 'line', 0) or 0,
                    hint="Declare it first: `map %s ... map: done`. This is a declaration "
                         "problem, not a database one -- %s reads a map, not a table."
                         % (name, verb)))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    stmts = getattr(program, 'statements', None) or []
    for s in stmts:
        collect(s)
    for s in stmts:
        walk(s)
    return errors


def scan_regulated_field_needs_type(program):
    """Regulated data says its own type. Nothing guesses it.

    THE RULING, and the reason the two halves differ. An UNREGULATED field with no declared type
    is inferred from what is written: quotes make text, a bare number makes a number, a bare name
    reads a variable. That is safe because a wrong inference does not stay quiet -- it fails at the
    point of USE, loudly, with the cast named:

        a "5" / (a + 1)   ->  Cannot do math on text (+) ... cast it first: `5` as.number
        n "x" into `n as int`  ->  shape_violation: n: N must be a number

    So the coder learns at the line that is wrong and adds the type. Infer, then fail loud at use.

    REGULATED DATA GETS NONE OF THAT, because the cost of being wrong is not a stack trace. A
    guessed type on a classified field decides how the value is stored, compared, masked and
    erased, and a wrong guess there is a compliance fault that looks like nothing until somebody
    audits it. There is no fail-loud-at-use to fall back on, because the harm has already been
    done by the time anyone reads the column. So a regulated field with no type is REFUSED before
    the program is built: fail closed, not inferred.

    WHAT MAKES A FIELD REGULATED, both routes, because a rule that knew only one would leave the
    other silently inferring:
      * it carries a descriptor of its own -- `ssn [phi]`, `card [phi, pci]`
      * the DECLARED SECTOR names it -- a profile saying `member_id is [pii]` regulates every
        `member_id` in the program, which is the whole point of a sector being a baseline.
    """
    errors = []

    # The sector's own field names, when a sector is declared. Loaded once: a program declares a
    # sector at most a handful of times, and a profile read per field would be the same file
    # opened once per line.
    sector_fields = {}

    def collect_sector(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                collect_sector(c)
            return
        if not is_dataclass(node):
            return
        if type(node).__name__ == 'SectorDecl':
            name = str(getattr(node, 'name', '') or getattr(node, 'sector', '') or '')
            if name:
                # NOT WRAPPED IN A TRY. A profile that simply does not exist returns None on
                # its own, so the only thing an except here could catch is a real failure to
                # read one -- and answering that with "no profile, carry on" would silently
                # switch this rule off for the program it was most needed on. Letting it
                # propagate reaches the Layer 3 guard, which already says out loud that the
                # whole-program scanners did not finish and that enforcement is incomplete.
                from mohio_sector_loader import load_sector_profile
                prof = load_sector_profile(name)
                for fname, ft in ((prof.field_types if prof else None) or {}).items():
                    tags = [t for t in (getattr(ft, 'classifications', None) or [])]
                    if tags:
                        sector_fields[str(fname).lower()] = (name, tags)
        for f in fields(node):
            collect_sector(getattr(node, f.name, None))

    def walk(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                walk(c)
            return
        if not is_dataclass(node):
            return
        if type(node).__name__ == 'ShapeDecl':
            decl = (node.every_field() if hasattr(node, 'every_field')
                    else (getattr(node, 'fields', None) or []))
            for fld in decl:
                if getattr(fld, 'type_name', None):
                    continue
                fname = str(getattr(fld, 'name', '') or '')
                own = [str(getattr(m, 'value', '') or '')
                       for m in (getattr(fld, 'modifiers', None) or [])
                       if str(getattr(m, 'modifier_type', '') or '') == 'tag'
                       and getattr(m, 'value', None)]
                line = getattr(fld, 'line', 0) or getattr(node, 'line', 0) or 0
                if own:
                    errors.append(CompileError(
                        "`%s` is classified %s and has no declared type. Regulated data is never "
                        "inferred." % (fname, ", ".join("[%s]" % t for t in own)),
                        line=line,
                        hint=("Say what it is, for example `%s as text %s`. An unclassified field "
                              "may be left to inference, because a wrong guess there fails loud "
                              "the first time the value is used. A classified one has no such "
                              "second chance: the type decides how it is stored, matched and "
                              "erased." % (fname, "[%s]" % ", ".join(own)))))
                elif fname.lower() in sector_fields:
                    sec, tags = sector_fields[fname.lower()]
                    errors.append(CompileError(
                        "`%s` is classified %s by the `%s` sector and has no declared type. "
                        "Regulated data is never inferred."
                        % (fname, ", ".join("[%s]" % t for t in tags), sec),
                        line=line,
                        hint=("Say what it is, for example `%s as text`. The sector regulates this "
                              "field name wherever it appears, so the shape has to state the type "
                              "even though the classification came from the profile." % fname)))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    stmts = getattr(program, 'statements', None) or []
    for st in stmts:
        collect_sector(st)
    for st in stmts:
        walk(st)
    return errors



def scan_shape_may_not_loosen_sector(program):
    """A sector is the floor. A shape may raise it and may not lower it.

    THE RULING. The sector provides the BASELINE. A shape may change it only by TIGHTENING, or
    where the sector allows the change. Tightening is free; loosening is refused. So the floor can
    be raised and never lowered.

    TIGHTEN AND LOOSEN ARE SET CONTAINMENT HERE, and that is deliberate rather than convenient.
    The three enforced classifiers are not a ladder: all three encrypt at rest, `pci` additionally
    masks to the last four on output, `phi` carries audit-on-access. They are different
    protections, not degrees of one, so there is no ordering to rank them by and inventing one
    would be a design decision dressed as an implementation detail. What can be said without
    inventing anything is whether the shape keeps everything the sector asked for:

        sector [pii]   shape [pii, phi]   ADDS a protection        tighter   allowed
        sector [pii]   shape [pii]        says the same thing      equal     allowed
        sector [phi, pci] shape [pci]     DROPS a protection       looser    REFUSED

    A SHAPE THAT SAYS NOTHING IS NOT LOOSENING. Leaving the tags off entirely keeps the sector's
    classification, which applies to the field name wherever it appears, so nothing is claimed and
    nothing is refused. This fires only when a shape writes its own tags on a field the sector
    already governs, because that is the case where the source states a protection set and would
    otherwise be stating a smaller one than the program actually enforces.

    WHICH IS THE REAL DAMAGE, and worth naming because the runtime is already safe here. The
    sector's tags register regardless, so a narrowed shape does not actually strip protection at
    runtime. What it does is make the SOURCE lie: the field reads as `[pci]` while `[phi]` is also
    being enforced, and a reader auditing the shape draws a conclusion the program does not
    support. Readable code is auditable code, so a shape that misdescribes its own protection is
    refused rather than quietly corrected.

    THE PER-FIELD PERMISSION MARKER, by which a sector could declare a field loosenable, belongs
    to the control vocabulary that is still to be designed. Until it exists the default is the
    safe one: not overridable, floor enforced.
    """
    errors = []
    sector_fields = {}

    def collect_sector(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                collect_sector(c)
            return
        if not is_dataclass(node):
            return
        if type(node).__name__ == 'SectorDecl':
            name = str(getattr(node, 'name', '') or getattr(node, 'sector', '') or '')
            if name:
                # NOT WRAPPED IN A TRY. A profile that simply does not exist returns None on
                # its own, so the only thing an except here could catch is a real failure to
                # read one -- and answering that with "no profile, carry on" would silently
                # switch this rule off for the program it was most needed on. Letting it
                # propagate reaches the Layer 3 guard, which already says out loud that the
                # whole-program scanners did not finish and that enforcement is incomplete.
                from mohio_sector_loader import load_sector_profile
                prof = load_sector_profile(name)
                for fname, ft in ((prof.field_types if prof else None) or {}).items():
                    tags = {str(t).strip().lower()
                            for t in (getattr(ft, 'classifications', None) or []) if str(t).strip()}
                    if tags:
                        sector_fields[str(fname).lower()] = (name, tags)
        for f in fields(node):
            collect_sector(getattr(node, f.name, None))

    def walk(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                walk(c)
            return
        if not is_dataclass(node):
            return
        if type(node).__name__ == 'ShapeDecl':
            decl = (node.every_field() if hasattr(node, 'every_field')
                    else (getattr(node, 'fields', None) or []))
            for fld in decl:
                fname = str(getattr(fld, 'name', '') or '')
                if fname.lower() not in sector_fields:
                    continue
                own = {str(getattr(m, 'value', '') or '').strip().lower()
                       for m in (getattr(fld, 'modifiers', None) or [])
                       if str(getattr(m, 'modifier_type', '') or '') == 'tag'
                       and getattr(m, 'value', None)}
                if not own:
                    continue          # says nothing, so claims nothing: the floor simply holds
                sec, need = sector_fields[fname.lower()]
                missing = sorted(need - own)
                if missing:
                    errors.append(CompileError(
                        "`%s` drops %s, which the `%s` sector requires. A shape may raise the "
                        "sector's baseline and may not lower it."
                        % (fname, ", ".join("[%s]" % t for t in missing), sec),
                        line=getattr(fld, 'line', 0) or getattr(node, 'line', 0) or 0,
                        hint=("Write every classification the sector gives this field, and add "
                              "any further one you want: `%s ... [%s]`. Leaving the tags off "
                              "entirely is also fine and keeps the sector's own set. The narrowed "
                              "list is refused because the program would still enforce %s while "
                              "the shape read as though it did not."
                              % (fname, ", ".join(sorted(own | need)),
                                 ", ".join("[%s]" % t for t in missing)))))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    stmts = getattr(program, 'statements', None) or []
    for st in stmts:
        collect_sector(st)
    for st in stmts:
        walk(st)
    return errors



_CANONICAL_PATH_PARAM = re.compile(r'^\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}$')


def scan_route_path_param_form(program):
    """A route parameter is `{{name}}`, and anything else shaped like one is refused here.

    WHY THIS IS AN ERROR AND NOT A STYLE NOTE. A route path is matched segment by segment, so a
    segment the matcher does not recognize as a parameter is treated as literal text. `at
    /users/:id` then answers only a request that literally spells `:id`, which no client sends, so
    every real request 404s and nothing anywhere says the route was never going to work. That is
    the silent class, reached by writing a spelling borrowed from another framework.

    `{{name}}` IS NOT A NEW INVENTION FOR PATHS. Doubled braces are how Mohio says the value of a
    variable everywhere else, so a route parameter is the same idea in the same spelling, and the
    spacing inside is stripped in a path exactly as it is in a string.

    WHAT GETS REFUSED, and each is refused for its own reason rather than as a list:
      * `:id`    another framework's spelling, not Mohio
      * `{id}`   single braces, which this language does not use anywhere
      * `*`      a wildcard, which is not built and would silently match nothing
      * `{{a.b}}` doubled braces around something that is not a plain name, which the matcher
                  cannot bind and would leave as literal text
    """
    errors = []

    def flag(path, seg, line, what, fix):
        errors.append(CompileError(
            "`%s` in the route `%s` is not a Mohio route parameter. %s" % (seg, path, what),
            line=line,
            hint=("Write it as `{{%s}}`. A route parameter uses the same doubled braces as any "
                  "other value in Mohio, and the spacing inside does not matter. Without the "
                  "canonical form the segment is matched as literal text, so the route answers "
                  "nothing and every request to it returns 404." % fix)))

    def walk(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                walk(c)
            return
        if not is_dataclass(node):
            return
        if type(node).__name__ in ('NewBlock', 'RequestInboundBlock'):
            path = getattr(node, 'path', None)
            if path:
                line = getattr(node, 'line', 0) or 0
                for seg in str(path).split('/'):
                    seg = seg.strip()
                    if not seg or _CANONICAL_PATH_PARAM.match(seg):
                        continue
                    if seg.startswith(':'):
                        # The name to suggest is whatever followed the colon. A bare `:` has
                        # nothing to carry over, so the suggestion says `name` -- written out
                        # rather than as a fallback, because a reader of this line should not
                        # have to work out which of two things the message will say.
                        _suggest = seg[1:]
                        if not _suggest:
                            _suggest = "name"
                        flag(path, seg, line,
                             "A leading colon is another framework's spelling.", _suggest)
                    elif seg == '*':
                        flag(path, seg, line,
                             "A `wildcard` segment is not built and would match nothing.", "name")
                    elif '{' in seg or '}' in seg:
                        inner = seg.strip('{}').strip()
                        nice = inner if re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', inner) else "name"
                        flag(path, seg, line,
                             "Only the doubled-brace form around a plain name is a parameter.",
                             nice)
        for f in fields(node):
            walk(getattr(node, f.name, None))

    for st in (getattr(program, 'statements', None) or []):
        walk(st)
    return errors



ERROR_SCANS = (
    scan_table_as_value,
    scan_reference_rule,
    scan_table_name_collision,
    scan_listener_with_no_handler,
    scan_multi_database_reference,
    scan_query_connection_name,
    scan_connection_access_mode,
    scan_give_destination,
    scan_orphan_it,
    scan_unknown_types,
    scan_undeclared_connectors,
    scan_agent_tool_grants,
    scan_task_param_undeclared_shape,
    scan_undeclared_tasks,
    scan_not_built_services,
    scan_deferred_constructs,
    scan_undeclared_maps,
    scan_regulated_field_needs_type,
    scan_shape_may_not_loosen_sector,
    scan_route_path_param_form,
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
            frameworks, profile_found, _paid = [], False, False
            try:
                from mohio_sector_loader import (find_sector_profile, load_sector_profile,
                                                 sector_requires_license)
                _paid = sector_requires_license(name)
                path = find_sector_profile(name)
                if path:
                    profile_found = True
                    # T1-SILENT-SWEEP-BATCH6-10 (2026-08-15): was `load_sector_profile(path)` --
                    # load_sector_profile expects a SECTOR NAME (it re-resolves to a path
                    # internally via find_sector_profile), not a path. Passed a path, its
                    # internal re-resolution could never match any real filename, so it always
                    # returned None -- frameworks stayed permanently [] and the WORM/append-only
                    # audit-grade warning below never fired for any real sector with a real
                    # profile.
                    frameworks = list(getattr(load_sector_profile(name), 'compliance', []) or [])
            except Exception:
                profile_found = False
            if not profile_found:
                # A LICENSED PRODUCT AND A MISSING FILE ARE NOT THE SAME SITUATION, and check
                # said the same sentence for both. `mio run` already distinguishes them: for a
                # paid sector it says the profile is licensed and not bundled with the open
                # compiler. Check said "no profile for it was found ... a sector with no profile
                # enforces nothing", which reads as something unbuilt.
                #
                # WHY THAT SPECIFIC WORDING COST SOMETHING. Check is what a prospect runs first
                # and what CI runs every time. So somebody evaluating the fraud demo ran
                # `mio check --security`, read that no profile was found, and could reasonably
                # conclude financial-sector support does not exist -- at the exact moment they
                # were deciding whether to keep looking. It is a product they could buy, and the
                # boundary was invisible in the one place it most needed to be visible.
                if _paid:
                    warnings.append(CompileWarning(
                        f"sector `{name}` is a licensed sector profile and is not bundled with "
                        f"the open compiler, so nothing is enforced here.",
                        line=getattr(node, 'line', 0),
                        hint=("This is a commercial boundary, not a missing feature. Provide the "
                              "profile on the search path (~/.mohio/sectors) or run the licensed "
                              "runtime. To exercise the enforcement mechanism without a licensed "
                              "profile, declare a demo sector: `demo_financial` or "
                              "`demo_regulated` carry field classifications, never-store fields "
                              "and confidence floors.")))
                else:
                    warnings.append(CompileWarning(
                        f"sector `{name}` is declared but no profile for it was found, so no "
                        f"compliance framework is active and no audit grade is required.",
                        line=getattr(node, 'line', 0),
                        hint=("A sector with no profile enforces nothing. Add the profile, or "
                              "drop the declaration so the program does not read as governed "
                              "when it is not.")))
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
        # T1-SILENT-SWEEP-BATCH11 (2026-08-15): this tuple used to carry two dead strings --
        # 'RequestBlock' is a Python-level alias for RequestInboundBlock (mohio_ast.py:1583,
        # `RequestBlock = RequestInboundBlock`), so type(node).__name__ could never equal it
        # (a class's __name__ is fixed at definition, unaffected by an alias assigned to it
        # elsewhere); 'ListenerDecl' never existed as a class anywhere in this codebase. Both
        # were removed as dead weight, and at the time this check's real coverage was left as
        # just PageDecl/NewBlock -- `request for sh.X` routes were silently never covered by
        # this security scanner at all, the same disease the sweep this tuple came from was
        # named for. FIXED (2026-08-15, attended): 'RequestInboundBlock' is the real class
        # name (confirmed via `class RequestInboundBlock(Node)`, mohio_ast.py). Reachability
        # rule: a `request for sh.X [at /path]` listener reaches its body through
        # _exec_ListenBlock's GET/REQUEST-gated dispatch (mohio_interpreter.py, `_method_ok`)
        # -- the identical listener-dispatch mechanism `new sh.X` uses for POST, just gated to
        # GET/REQUEST instead of POST/NEW/PUT. This scanner does no deeper reachability
        # analysis for PageDecl/NewBlock either (pure type-match anywhere in the tree, no
        # check that a listener is inside a reachable `listen for`), so RequestInboundBlock is
        # covered the same way, not a bespoke stricter rule.
        # `PageDecl` dropped 2026-08-25 -- the page block is removed, so no tree can hold one.
        if type(node).__name__ in ('NewBlock', 'RequestInboundBlock'):
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



def scan_decorative_shape_modifiers(program):
    """A shape field modifier that enforces NOTHING must say so, not look like a guarantee.

    Q42. Measured 2026-09-03 by running a violating payload through the form boundary, which is
    the one path where a shape is actually bound to data:

        ENFORCED      required, allowed, min, max, range, pattern, matches, format
        ENFORCED ELSEWHERE (correctly, at another layer)
                      never store (write guard), never log, purpose (use-time scope)
        NOT CONSTRAINTS (rendering or messaging, nothing to enforce)
                      optional, multiline, multiple, label, error
        DECORATIVE    default, unique, threshold  <- zero consumers anywhere

    `modifier_type == 'default' | 'unique' | 'threshold'` returns ZERO matches across the whole
    interpreter. They parse, they check clean, and they do nothing at all. In a language whose
    claim is that the compiler enforces what you declare, a constraint that enforces nothing is
    worse than an absent one: a reviewer reads `unique` and believes it.

    WARNING, NOT REFUSAL, and the reason is measured rather than preferred: all three are
    already used in the corpus (`default` in 6 files, `unique` and `threshold` in 1 each). A
    hard refusal would break working files for a property that was never delivered, which is
    punishing the author for the compiler's gap. The warning removes the DECEPTION, which is the
    harm; delivering the enforcement is the separate build each backlog entry names.
    """
    warnings = []
    # `default` LEFT THIS SET on 2026-09-14: it now fills a missing field at the boundary
    # that accepts a request into `new sh.X`, which is the one path where a shape is bound to
    # data. Calling it unenforced would be this scanner's own dishonesty pointed the other way.
    DECORATIVE = {
        'unique':    'no uniqueness check runs, on the form boundary or at write',
        'threshold': 'nothing reads it',
    }

    # NOT DECORATIVE, BUT NARROWER THAN IT LOOKS. `default` is applied where a request is bound
    # to a shape and NOT on a direct write, because a write's target is a table name with no
    # shape attached to it. That binding is a language decision rather than a missing function,
    # so the scope is stated rather than quietly assumed either way.
    PARTIAL = {
        'default': ('applied when a request is bound to this shape, where a missing field is '
                    'filled in. It is NOT applied on a direct `save`/`update`, because a write '
                    'names a table and a table carries no shape'),
    }

    def walk(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                walk(c)
            return
        if not is_dataclass(node):
            return
        if type(node).__name__ == 'ShapeDecl':
            # EVERY field, loose or table-owned. A decorative modifier is just as decorative
            # inside a table scope, and staying silent there would be the exact dishonesty
            # this scanner exists to prevent. See ShapeDecl.every_field.
            for fld in (node.every_field() if hasattr(node, 'every_field')
                        else (getattr(node, 'fields', None) or [])):
                for m in (getattr(fld, 'modifiers', None) or []):
                    mt = str(getattr(m, 'modifier_type', '') or '')
                    if mt in DECORATIVE:
                        warnings.append(CompileWarning(
                            f"`{mt}` on field `{getattr(fld, 'name', '?')}` is declared but NOT "
                            f"ENFORCED in this build: {DECORATIVE[mt]}. It reads like a "
                            f"guarantee and is not one. Nothing about this program is refused "
                            f"for it -- this says so out loud rather than letting the "
                            f"declaration look like protection it does not provide.",
                            line=getattr(fld, 'line', 0) or getattr(node, 'line', 0) or 0))
                    elif mt in PARTIAL:
                        warnings.append(CompileWarning(
                            f"`{mt}` on field `{getattr(fld, 'name', '?')}` is "
                            f"{PARTIAL[mt]}.",
                            line=getattr(fld, 'line', 0) or getattr(node, 'line', 0) or 0))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    for st in (getattr(program, 'statements', None) or []):
        walk(st)
    return warnings


def scan_unrecognized_field_classifier(program):
    """A bracketed label on a shape field that is not a recognized classifier says so.

    THE LEAK, measured through `mio run` against a real SQLite file and read back raw:

        secret as text [pii]            enc:v1:3Z19rtY1OBB...   encrypted at rest
        secret as text [ssn]            123-45-6789             PLAINTEXT ON DISK

    A social security number tagged `[ssn]` is stored in the clear, `mio check` reports no
    errors, and the developer who wrote the most obviously careful thing on the line gets
    nothing for it.

    A WARNING, NOT A REFUSAL, and that is the design rather than caution. OPEN LABELS ARE
    DESIGNED BEHAVIOUR: an unreserved `[label]` is a searchable label that does nothing yet,
    and closing the set was considered and rejected on 2026-09-06. So the harm to remove is not
    that `[ssn]` is allowed, it is that `[ssn]` LOOKS like protection. The warning removes the
    deception and leaves the label.

    THE RESERVED SET WAS CONFIRMED AGAINST THE SECTOR PROFILES rather than assumed, and the
    confirmation changed it. `mohio_data/sectors/sector-demo-regulated.sector` classifies a
    field `region is [public]`, which was not in the proposed set, so warning on it would have
    fired on a classifier a shipped profile already uses. That profile is also the clearest
    statement of the principle this warning teaches: it writes `ssn is [phi, pii]`, naming the
    FIELD ssn and CLASSIFYING it as phi and pii. A classifier names a regulated class; it does
    not name the kind of value.

    SCOPED TO SHAPE FIELDS, deliberately and narrowly. Brackets do other jobs elsewhere -- a
    give-back status `[404]`, and labels reserved for later scaffolding -- and none of those
    are touched, because in those positions a bracket is not claiming protection.
    """
    # THE ONE LIST, imported rather than repeated. The check-time warning and the runtime
    # resolver have to agree about what a classifier is, and two copies of a set are two
    # answers waiting to diverge. `identifier` is gone from it: nothing anywhere reads it, so
    # leaving it recognized made it look like one of the real ones.
    from mohio_classification import RECOGNIZED_CLASSIFIERS as RECOGNIZED
    from mohio_classification import SEC_CLASSIFY_LEVEL_WORDS as LEVEL_WORDS
    # What a developer most plausibly meant, for the ones worth naming outright. Anything not
    # listed still warns; this only sharpens the suggestion where the intent is unmistakable.
    MEANT = {
        'ssn': 'pii', 'social_security': 'pii', 'email': 'pii', 'phone': 'pii',
        'address': 'pii', 'dob': 'pii', 'birthdate': 'pii', 'name': 'pii',
        'payment_card': 'pci', 'card': 'pci', 'card_number': 'pci', 'credit_card': 'pci',
        'cvv': 'pci', 'pan': 'pci',
        'medical': 'phi', 'health': 'phi', 'diagnosis': 'phi', 'patient': 'phi',
        'secret': 'confidential', 'private': 'confidential',
    }
    warnings = []

    def walk(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                walk(c)
            return
        if not is_dataclass(node):
            return
        if type(node).__name__ == 'ShapeDecl':
            for fld in (node.every_field() if hasattr(node, 'every_field')
                        else (getattr(node, 'fields', None) or [])):
                for m in (getattr(fld, 'modifiers', None) or []):
                    if str(getattr(m, 'modifier_type', '') or '') != 'tag':
                        continue
                    # Split on comma even though a FIELD tag cannot carry one: TAG_REF is
                    # `"[" NAME "]"`, and `[phi, pii]` on a field does not parse (measured).
                    # The comma form is real in sector profiles and on the shape-level zone
                    # tag, which the interpreter does split, so the split stays rather than
                    # this being the one reader that would mis-handle a list if it arrives.
                    raw = str(getattr(m, 'value', '') or '')
                    for part in [p.strip().lower() for p in raw.split(',') if p.strip()]:
                        # THE LEVEL WORDS GET THEIR OWN MESSAGE, and they keep their place in
                        # the recognized set. `confidential` and `classified` are `sec.classify`
                        # LEVEL names (`sec_classify_body: NAME sec_classify_rule+`, where the
                        # rules are encrypt.both / strip.on.output / log.access). A shape field
                        # tag never invokes sec.classify, so writing one in brackets on a field
                        # produced no warning AND no protection: the same looks-like-protection
                        # shape this scanner exists to close, one layer down. Saying "not a
                        # recognized classifier" would be wrong, because the word is real; what
                        # is wrong is the position. So it is pointed at the construct that does
                        # what it was reaching for, the way [ssn] is pointed at [pii].
                        if part in LEVEL_WORDS:
                            warnings.append(CompileWarning(
                                f"[{part}] on field `{getattr(fld, 'name', '?')}` names a "
                                f"`sec.classify` LEVEL, not a field tag, so nothing here reads "
                                f"it and the field gets no protection from it. To classify this "
                                f"field at that level, declare it in a `sec.classify` block "
                                f"(`sec.classify` / `{part} strip.on.output` / `sec.classify: "
                                f"done`). To protect the field itself, use a classifier that "
                                f"carries enforcement: [pii], [phi] or [pci].",
                                line=getattr(fld, 'line', 0) or getattr(node, 'line', 0) or 0))
                            continue
                        if part in RECOGNIZED:
                            continue
                        suggestion = MEANT.get(part)
                        did_you_mean = (f" Did you mean [{suggestion}]?" if suggestion else "")
                        warnings.append(CompileWarning(
                            f"[{part}] on field `{getattr(fld, 'name', '?')}` is not a "
                            f"recognized classifier and does nothing.{did_you_mean} It is kept "
                            f"as a searchable label, so nothing is refused, but it applies no "
                            f"protection: a field tagged only this way is stored in the clear. "
                            f"The classifiers that carry protection are: "
                            f"{', '.join('[' + c + ']' for c in sorted(RECOGNIZED))}.",
                            line=getattr(fld, 'line', 0) or getattr(node, 'line', 0) or 0))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    for st in (getattr(program, 'statements', None) or []):
        walk(st)
    return warnings


def scan_ai_decide_never_invoked(program):
    """A declared `ai.decide` that nothing ever invokes will not run, and says so.

    ai.decide REGISTERS AND WAITS. Declaring one produces no gate, no weigh, no fallback, no
    audit entry and no diagnostic until its name appears on a line to invoke it, and `mio check`
    passed either way. So a program could carry a governed-looking decision in its source,
    answer 201 APPROVED, and leave an empty audit behind, with nothing anywhere saying the
    decision never ran.

    NARROW ON PURPOSE, AND THE SCOPE WAS MEASURED RATHER THAN ASSUMED. The obvious generalisation
    is "warn on anything declared and never used", and a sweep of the class found that shape does
    NOT extend past ai.decide: an unused task, view, shape, miovalidate or pattern is ordinary
    dead code. Warning on those is noise, and noise is how a warning gets switched off. What
    makes ai.decide different is that it LOOKS like it is doing the work by being declared: the
    confidence floor, the weighed inputs and the audit destination are all written down, and none
    of them happens.

    A WARNING, NOT A REFUSAL. A block declared ahead of the code that will invoke it is a
    legitimate thing to write, so this must not refuse. What it must not do is stay silent.
    """
    declared = {}      # name -> line
    invoked = set()

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
        name = str(getattr(node, 'name', '') or '')
        if cls == 'AiDecideBlock' and name:
            declared.setdefault(name, getattr(node, 'line', 0) or 0)
        elif cls == 'AiDecideInvoke' and name:
            invoked.add(name)
        for f in fields(node):
            walk(getattr(node, f.name, None))

    def collect_refs(node, out):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                collect_refs(c, out)
            return
        if not is_dataclass(node):
            return
        cls = type(node).__name__
        if cls != 'AiDecideBlock':
            nm = getattr(node, 'name', None)
            if isinstance(nm, str) and nm:
                out.add(nm)
            for attr in ('live_name', 'chain_name', 'decision', 'decision_name'):
                v = getattr(node, attr, None)
                if isinstance(v, str) and v:
                    out.add(v)
            parts = getattr(node, 'parts', None)
            if parts:
                out.add(str(parts[0]))
        for f in fields(node):
            collect_refs(getattr(node, f.name, None), out)

    stmts = getattr(program, 'statements', None) or []
    walk(stmts)
    referenced = set()
    collect_refs(stmts, referenced)

    warnings = []
    for name, line in sorted(declared.items()):
        if name in invoked or name in referenced:
            continue
        warnings.append(CompileWarning(
            f"`ai.decide {name}` is declared but never invoked, so it will not run: no "
            f"confidence gate, no weighed inputs, no fallback and NO AUDIT ENTRY. The decision "
            f"reads as governed in the source and does nothing at runtime.",
            line=line,
            hint=(f"Invoke it where the decision belongs by putting its name on a line "
                  f"(`ai.decide {name}`), or remove the block if it is not needed yet.")))
    return warnings


def scan_upload_storage_durability(program):
    """A shape that accepts an upload says where those files actually go.

    Uploads are written to local disk (`MOHIO_UPLOAD_DIR`, default `./uploads`) and the database
    stores a PATH into that directory. The row survives a restart; the file does not, unless the
    directory is durable AND outside the tree a deploy replaces. The default is relative to the
    working directory, so on most hosts it sits exactly inside that tree.

    The project already warns like this about the things that do not survive a restart: an
    in-memory SQLite connection says "everything written is lost when the app stops", and a
    SQLite file says "on a host that resets its disk, it does not survive". Uploads had no such
    line, so the one kind of data that leaves a working row pointing at a missing file was the
    one kind nobody was told about.

    A WARNING, and only a warning. Where uploads should be stored for production is a design
    question with its own document; this is the sentence that stops the silence in the meantime.
    """
    UPLOAD_TYPES = ('file', 'image', 'audio', 'video', 'pdf')
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
        if type(node).__name__ == 'ShapeDecl':
            for fld in (node.every_field() if hasattr(node, 'every_field')
                        else (getattr(node, 'fields', None) or [])):
                tn = str(getattr(fld, 'type_name', '') or '').lower()
                # Named rather than written as `... or '?'`: a field with no name is not a
                # field, and the placeholder is only so the message has something to print if
                # one ever arrives that way.
                _raw_name = getattr(fld, 'name', None)
                nm = str(_raw_name) if _raw_name else '(unnamed field)'
                if tn in UPLOAD_TYPES and nm not in seen:
                    seen.add(nm)
                    warnings.append(CompileWarning(
                        f"`{nm}` accepts an upload, and uploaded files are written to local "
                        f"disk (MOHIO_UPLOAD_DIR, default ./uploads) while the database stores "
                        f"only the path. The row survives a restart and the FILE does not, "
                        f"unless that directory is durable and outside the tree a deploy "
                        f"replaces. The default is relative to the working directory, which on "
                        f"most hosts is inside it.",
                        line=getattr(fld, 'line', 0) or getattr(node, 'line', 0) or 0))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    for st in (getattr(program, 'statements', None) or []):
        walk(st)
    return warnings


def scan_check_with_no_branches(program):
    """A `check` that has neither a `when` nor an `otherwise` does nothing at all.

    WARNS, DELIBERATELY, RATHER THAN REFUSING. It is hard to construct a reason to write one,
    but not impossible, and the neighbouring ruling went the other way for a reason worth
    keeping straight: an empty `listen for` is REFUSED because it is the construct that mounts
    routes, so an empty one is a server answering nothing and there is no version of that which
    is on purpose. A `check` with no branches only evaluates a subject and stops, which is
    pointless rather than broken, so it is named and allowed through. An empty `journey` is left
    entirely alone: one per folder, not required, and empty is a legitimate state for it.

    Silence was the one option not on the table. The subject still gets evaluated, so the block
    looks like it is doing something, and a `when` deleted by accident leaves exactly this shape.
    """
    warnings = []
    seen = set()

    def visit(node):
        if node is None or not is_dataclass(node) or id(node) in seen:
            return
        seen.add(id(node))
        if isinstance(node, CheckBlock) and not node.when_clauses and node.otherwise is None:
            warnings.append(CompileWarning(
                "this `check` has no `when` and no `otherwise`, so it does nothing.",
                getattr(node, "line", 0),
                "Add a branch (`when <value>` / `otherwise`), or remove the block. The subject "
                "is still read, so the block looks active while having no effect.",
            ))
        for f in fields(node):
            val = getattr(node, f.name, None)
            for item in (val if isinstance(val, list) else [val]):
                if is_dataclass(item):
                    visit(item)

    for st in (getattr(program, 'statements', None) or []):
        visit(st)
    return warnings


def scan_returning_call_without_capture(program):
    """A call to a task that declares a return type, with nothing capturing the result.

    The compiler already refuses the opposite mistake -- asking a task for a value it does not
    produce -- so this is the missing half of a pair rather than a new idea.
    """
    warnings = []
    stmts = getattr(program, 'statements', None) or []
    returns = {}

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
            nm = str(getattr(node, 'name', '') or '')
            rt = getattr(node, 'return_type', None)
            if nm and rt:
                returns[nm] = str(rt)
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
        if type(node).__name__ in ('RunBlock', 'CallBlock'):
            name = str(getattr(node, 'task_name', '') or '')
            alias = str(getattr(node, 'alias', '') or '')
            if name in returns and not alias:
                warnings.append(CompileWarning(
                    f"`{name}` returns {returns[name]}, and this call does not keep the "
                    f"answer.\n"
                    f"    The task runs and its result is dropped, so anything after this line "
                    f"decides on its own rather than on what `{name}` worked out.\n"
                    f"    Did you mean:  call {name} as result   -- then read `result`?\n"
                    f"    (A `give back` inside a task sets the task's return value; it does "
                    f"not answer for the handler around it, so an uncaptured answer is simply "
                    f"gone.)",
                    line=getattr(node, 'line', 0) or 0))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    for st in stmts:
        collect(st)
    for st in stmts:
        walk(st)
    return warnings


WARNING_SCANS = (
    scan_returning_call_without_capture,
    scan_check_with_no_branches,
    scan_upload_storage_durability,
    scan_ai_decide_never_invoked,
    scan_unrecognized_field_classifier,
    scan_decorative_shape_modifiers,
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
    import sys as _sys
    errors, warnings = [], []
    for scan in ERROR_SCANS:
        try:
            errors.extend(scan(program))
        except Exception as e:
            # T1-SILENT-SWEEP-BATCH6-10 (2026-08-15): used to `pass` here -- a scanner that
            # raises on some particular program silently dropped its ENTIRE contribution to
            # `mio check`, with `mio check` still reporting a clean pass for that program.
            # Still never takes down the check itself (the other scanners still run, the
            # program is not refused) -- just no longer invisible when it happens.
            print(f"  [mio check] internal warning: scanner {getattr(scan, '__name__', scan)!r} "
                  f"raised {type(e).__name__}: {e} -- its findings for this run are incomplete.",
                  file=_sys.stderr)
    for scan in WARNING_SCANS:
        try:
            warnings.extend(scan(program))
        except Exception as e:
            print(f"  [mio check] internal warning: scanner {getattr(scan, '__name__', scan)!r} "
                  f"raised {type(e).__name__}: {e} -- its findings for this run are incomplete.",
                  file=_sys.stderr)
    return errors, warnings
