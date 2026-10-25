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
from mohio_egress import gated_print as print  # noqa: A001,E402 -- every print through the egress gate

from dataclasses import fields, is_dataclass

from mohio_ast import GiveBackStmt, HaltStmt, CheckBlock
from mohio_transformer import CompileWarning, CompileError

_HARD_RETURN = (GiveBackStmt, HaltStmt)

# EVERY CONSTRUCT THAT ENDS ITS BLOCK THE SAME WAY (RULED by Ron 2026-10-10: a line after `give back`
# refuses at check, and so does a line after each of these). Each is an executor that raises a
# control-flow exception and never returns to the next statement: give back / give (_GiveBack),
# halt (_Halt), jump to (_Jump), stop (_Stop), skip (_Skip), raise (_Raise).
# What each one ends, for the message.
_ENDS = {'GiveBackStmt': ('give back', 'the handler'), 'GiveStmt': ('give', 'the handler'),
         'HaltStmt': ('halt', 'the program'), 'JumpToStmt': ('jump to', 'the handler'),
         'StopStmt': ('stop', 'the loop'), 'SkipStmt': ('skip', 'this pass of the loop'),
         'RaiseStmt': ('raise', 'the block')}

# A DECLARATION after a top-level `give back` is not dead: declarations load at startup, before any
# request runs (a convention page's `give back` followed by the shapes and tasks it uses).
_LOADED_AT_START = ('ShapeDecl', 'TaskDecl', 'ConnectDecl', 'HoldDecl', 'LockDecl', 'SectorDecl',
                    'ComplianceDecl', 'SecurityDecl', 'IncludeDecl', 'MioconnectDecl', 'MiosearchDecl',
                    'MiovalidateDecl', 'PatternDecl', 'TimespanDecl', 'RateLimitDecl', 'LoadPackDecl',
                    'MioScheduleDecl', 'MiofileDecl', 'AiRankBlock', 'AiConnectBlock', 'AiDecideBlock',
                    'AiCompareBlock', 'AiRespondBlock', 'MapDecl', 'MiomapDecl', 'FrameworkDecl')


def _is_unconditional_return(node):
    """True only for a block-ending statement that always ends it: no trailing if/unless qualifier,
    and no `when <condition>` (`stop when ...` / `skip when ...` end the loop only when true)."""
    return (type(node).__name__ in _ENDS
            and getattr(node, "qualifier", None) is None
            and getattr(node, "condition", None) is None)


# A routed unit is ASSEMBLED, not executed in sequence, so a hard return earlier in the
# same list does not make it dead: the router reaches it directly when a request arrives.
# This became reachable-in-practice when `page` was removed -- a convention home page is a
# top-level `give back`, and the section routes that follow it serve normally (verified by
# real HTTP against a file with both). Warning on them would flag correct code as dead.
# Batch 16: the STANDALONE handlers (`new sh.X`, `request for sh.X`, `connection`, `change to`,
# a `from` connector) are routed units too -- they used to sit inside a `listen for` ListenBlock,
# so this list only named the container; now each is a top-level statement in its own right, and a
# convention home page's `give back` followed by loose section routes must NOT flag those routes as
# dead. (Regression caught by tools/blast_radius.py against examples/particularllc-skeleton.mho.)
_ROUTED_UNITS = ('ListenBlock', 'JourneyDecl', 'NewBlock', 'RequestInboundBlock',
                 'ConnectionBlock', 'ChangeBlock', 'FromConnectorBlock')


def _is_routed_unit(node):
    return type(node).__name__ in _ROUTED_UNITS


def scan_unreachable(program):
    """
    A statement after a block-ending statement in the same statement list REFUSES at check
    (RULED by Ron 2026-10-10; it was a warning). Reported at the first line that never runs, one
    per list. Routed units and, at the top level, declarations are not dead (see above).
    """
    warnings = []
    seen = set()  # guard against shared nodes / accidental cycles

    def _scan_list(seq):
        for i in range(len(seq) - 1):
            stmt = seq[i]
            if _is_unconditional_return(stmt):
                dead = next((s for s in seq[i + 1:] if not _is_routed_unit(s)
                             and type(s).__name__ not in _LOADED_AT_START
                             and type(s).__name__ != 'Closer'), None)
                if dead is None:
                    break
                verb, ends = _ENDS[type(stmt).__name__]
                ret_line = getattr(stmt, "line", 0)
                dead_line = getattr(dead, "line", 0) or ret_line
                warnings.append(CompileError(
                    f"Line {dead_line}: This line never runs because {verb} ends {ends}. "
                    f"Move it above.",
                    dead_line,
                    f"`{verb}` on line {ret_line} ends {ends}, so nothing after it in the same "
                    f"block can run. Move this line above it, or make the `{verb}` conditional "
                    f"with a trailing `if`/`unless`.",
                ))
                break  # one per list is enough

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


# Child grammar rules that a WIRED parent executor CONSUMES from its own body rather than dispatching
# them as statements (T1-SCAN-CHILD-NODE-FALSE-POSITIVE). Each has no executor of its own BY DESIGN,
# so scan_unwired must not flag it "not executable" -- doing so told an application lane a working
# feature was unbuilt. This is the SWEEP list: a rule belongs here only after confirming, by running
# the parent's tests, that the parent CONSUMES it (as opposed to an unwired construct a parent merely
# holds, e.g. a `mioschedule` timing body, which must still surface because nothing runs it).
_CONSUMED_CHILD_RULES = frozenset({
    'ai_create_body',        # `ai.create name text prompt "..."` -- read by _exec_AiCreateStmt (22 tests)
    'ai_create_full_body',   # its wrapper (ai_create_full_body* holds one ai_create_body)
    # The named ai_create_body alternatives (2026-10-07). `create_realistic` and `create_prompt` are
    # read by the transformer; the other five are refused BY NAME, at check by
    # scan_ai_create_unbuilt_clauses and at run by _exec_AiCreateStmt, so the generic "no executor"
    # line must not speak over that specific refusal.
    'create_realistic', 'create_prompt', 'create_count', 'create_from_db', 'create_to_db',
    'create_to_path', 'create_as_shape',
    'service_param',         # `NAME value` on a service call -- read by the service's executor (sec.non_critical reason ...)
})


def scan_unwired(program):
    """Walk the AST and return a list[CompileError] for constructs that parsed
    and validated but transformed to a raw Tree -- i.e. they have no transformer
    and therefore no executor, so at run they fail with 'no executor' or, inside a parent
    that never reads them, are silently ignored (an inline `tools Stripe` on an agent was).

    A REFUSAL AT CHECK, NOT A WARNING (RULED by Ron, 2026-10-06, day 2: every run-time refusal
    also refuses at check where the information exists). It was a warning, for scaffolding around
    a planned feature, and so `copy`, `rerun`, `apply` and `apply ... to every` checked clean and
    then failed when run. The program shows the construct, so the check refuses it.
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
            # Condition subtrees (`wc_*` where-conditions, `cond_*` when-conditions) are intentionally
            # left as raw Trees and evaluated in place, never dispatched as statements. The same is
            # true of the sub-clauses a WIRED parent CONSUMES from its own body: a wired `ai.create`
            # reads its `ai_create_body` (`_exec_AiCreateStmt`, 22 tests) rather than dispatching it,
            # so the child has no executor of its own BY DESIGN and is not unwired. Flagging it told an
            # application lane a working feature was unbuilt (T1-SCAN-CHILD-NODE-FALSE-POSITIVE).
            #
            # This is the CLASS: a child rule EXCLUDED here is one a wired parent handles. It is NOT a
            # blanket "skip everything under a transformed node" -- an UNWIRED construct that a parent
            # merely holds without an executor (a `mioschedule` timing body `every day at 09:00`, a
            # raw statement in a block) must still surface, because nothing runs it. So the exclusion
            # names the handled children rather than trusting position, which could not tell a handled
            # child from an unhandled one.
            if (node.data.startswith('wc_') or node.data.startswith('cond_')
                    or node.data in _CONSUMED_CHILD_RULES):
                return
            line = 0
            meta = getattr(node, "meta", None)
            if meta is not None and not getattr(meta, "empty", True):
                line = getattr(meta, "line", 0) or 0
            key = (node.data, line)
            if key not in seen_reports and node.data == 'ai_agent_body':
                # An inline agent clause with no alias: `tools X` on one line, `name "..."`, or
                # `memory X`. At run each was dropped without a word, so `tools Stripe` granted
                # nothing; name the clause and teach the form that works.
                seen_reports.add(key)
                kid = node.children[0] if node.children else None
                if isinstance(kid, Tree):
                    tools = [str(t) for t in kid.scan_values(lambda v: True)] or ['Stripe']
                    msg = (f"`tools {', '.join(tools)}` on one line is not built in this release "
                           f"(T1-AGENT-INLINE-CLAUSES), so it would grant the agent nothing. Tools "
                           f"are granted with a block.")
                    hint = ("Write the grant as a block, one connector or operation per line:\n"
                            "        tools\n"
                            + ''.join(f"            {t}\n" for t in tools)
                            + "        tools: done")
                elif type(kid).__name__ == 'AiAuditStmt':
                    # Named for what it is. It was reported as `memory`, a clause the line did not
                    # contain, because every non-text clause fell into that branch (2026-10-09).
                    _dest = kid.log_name    # the grammar requires the name (AI_AUDIT TO NAME)
                    msg = (f"`ai.audit to {_dest}` on an ai.agent is not built in this release "
                           f"(T1-AGENT-INLINE-CLAUSES). It would record nothing at run, so it is "
                           f"refused here.")
                    hint = ("Remove the line. An agent records its own refusals and limit stops in "
                            "`<agent name>_limits_log` without one.")
                else:
                    clause = 'name' if getattr(kid, 'type', '') == 'STRING' else 'memory'
                    msg = (f"`{clause}` on an ai.agent is not built in this release "
                           f"(T1-AGENT-INLINE-CLAUSES). It would be ignored at run, so it is "
                           f"refused here.")
                    hint = ("Remove the line. The agent's own name is the one after `ai.agent`."
                            if clause == 'name' else
                            "Remove the line; pass what the agent needs with `weigh` or a `tools` grant.")
                warnings.append(CompileError(msg, line, hint, "not_executable"))
            if key not in seen_reports:
                seen_reports.add(key)
                warnings.append(CompileError(
                    f"`{node.data}` parsed and validated, but is not executable in "
                    f"this build -- it has no interpreter wiring, so at run it would "
                    f"fail with 'no executor' or be ignored.",
                    line,
                    "This construct is recognized by the grammar but not wired in the interpreter, "
                    "so it is refused here rather than at run. Remove it, or use a wired form until "
                    "it is built.",
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
#
# ONLY LIVE VERBS (Q178, 2026-10-05). A suggestion is a promise that the suggested word works.
# The list used to carry RETIRED verbs (make, route, consider, emit), so `mkae x 5` was told
# "did you mean `make`?" and following the advice hit a second refusal; and words with no
# statement at all (fetch, sanitize, forward, transform, purge, flush, receive), so the advice
# could not run. Neither kind may be suggested.
_TYPO_VERBS = {
    "show", "save", "find", "hold", "check", "create", "give", "render",
    "grab", "update", "remove", "upsert", "retrieve", "connect", "listen",
    "validate", "redirect", "include", "raise",
    "repeat", "while", "otherwise", "cache", "encode",
    "transaction", "retain",
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
                # SORTED, so a name one letter off two verbs gets the same suggestion every run
                # (a set's order varies between processes).
                for kw in sorted(_TYPO_VERBS):
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


# Common browser events, for a did-you-mean on a MISSPELLED event name in
# `listen for <event> on <sel>`. Any name is accepted (custom events dispatched by other
# code are legitimate, and the raw pass-through is deliberate), so this is WARNING-only and
# fires only when the name is exactly one edit from a known event -- high-signal, exactly
# like scan_typos does for verbs.
_KNOWN_DOM_EVENTS = {
    'click', 'dblclick', 'mousedown', 'mouseup', 'mousemove', 'mouseover', 'mouseout',
    'mouseenter', 'mouseleave', 'contextmenu', 'wheel',
    'keydown', 'keyup', 'keypress',
    'submit', 'change', 'input', 'focus', 'blur', 'select', 'reset',
    'load', 'unload', 'scroll', 'resize', 'error',
    'touchstart', 'touchend', 'touchmove', 'touchcancel',
    'drag', 'dragstart', 'dragend', 'dragover', 'dragenter', 'dragleave', 'drop',
    'copy', 'cut', 'paste', 'play', 'pause', 'ended',
}


def scan_event_name_typos(program):
    """`listen for <event> on <sel>` with an event name one edit from a known browser event.

    `listen for clik on #btn` parses and mounts a listener for a `clik` event that no browser
    ever fires: it compiles, runs, and the handler silently never triggers. Any event name is
    accepted on purpose (a custom event dispatched by other code is legitimate), so this WARNS
    rather than refuses, and only when the name is exactly one edit from a real event -- the
    same high-signal rule scan_typos uses for verbs. An exact match passes untouched, and a
    genuine custom event nowhere near a known one is not flagged. Returns list[CompileWarning].
    """
    warnings = []
    seen = set()

    def visit(node):
        if node is None or id(node) in seen:
            return
        seen.add(id(node))
        if type(node).__name__ == 'ClientListener':
            ev = getattr(node, 'event', '') or ''
            low = ev.lower()
            if low and low not in _KNOWN_DOM_EVENTS:
                for known in _KNOWN_DOM_EVENTS:
                    if _edit_distance_one(low, known):
                        warnings.append(CompileWarning(
                            f"`{ev}` is one letter off from the browser event `{known}` "
                            f"-- this mounts a listener for `{ev}`, which no browser fires, "
                            f"so it compiles and runs but the handler never triggers.",
                            getattr(node, "line", 0) or 0,
                            f"Did you mean `{known}`? If `{ev}` is a real custom event you "
                            f"dispatch yourself, rename it away from `{known}` to silence this.",
                            "possible_event_typo",
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


# The analytic functions a `calculate` block declares but does NOT wire. The runtime refuses each in
# _apply_calculate's `else` branch (a 500 at run) -- this announces the SAME set at CHECK, so a
# program that would 500 is refused five seconds in rather than after a deploy (Ron ruled 2026-09-27,
# same shape as `ai.create story`). Kept beside the built set the interpreter handles so the two lists
# cannot drift: every name in _CALCULATE_UNBUILT_DIFF must NOT be one the runtime builds, and every
# name in _CALCULATE_BUILT must be handled by _apply_calculate. (T1-CALCULATE-DIFFERENCE-NOT-BUILT)
_CALCULATE_UNBUILT_DIFF = ('difference', 'p_value', 'cohens_d', 'percentage_of')
_CALCULATE_BUILT = ('running_sum', 'moving_average', 'rank', 'std_deviation', 'variance', 'percentile')


def scan_calculate_unbuilt_functions(program):
    """A `calculate` analytic difference function that is declared but not built refuses at CHECK.

    `difference` / `p_value` / `cohens_d` / `percentage_of` parse and check clean, then 500 at run in
    `_apply_calculate`'s else branch -- the check-clean-then-500 shape this project is closing. The
    refusal itself is correct (there is no implementation), so moving the news to check stops nothing
    that runs today; it only stops a program that CHECKS CLEAN from being deployed to fail later.
    """
    errors = []
    seen = set()

    def visit(node):
        if node is None or id(node) in seen:
            return
        seen.add(id(node))
        if type(node).__name__ == 'CalculateBlock':
            for f in (getattr(node, 'fields', None) or []):
                fn = (getattr(f, 'function', '') or '').lower()
                if fn in _CALCULATE_UNBUILT_DIFF:
                    line = getattr(f, 'line', 0) or getattr(node, 'line', 0) or 0
                    errors.append(CompileError(
                        f"`calculate` cannot compute `{fn}` yet -- `difference` / `p_value` / "
                        f"`cohens_d` / `percentage_of` are declared but NOT built "
                        f"(T1-CALCULATE-DIFFERENCE-NOT-BUILT); at run they 500, so this refuses at "
                        f"check instead of after a deploy. Supported now in calculate: "
                        f"%s." % ', '.join(_CALCULATE_BUILT),
                        line=line))
        if isinstance(node, (list, tuple)):
            for it in node:
                visit(it)
            return
        if is_dataclass(node):
            for fld in fields(node):
                visit(getattr(node, fld.name, None))

    visit(program)
    return errors


# THE EXECUTOR-BRANCH DEFERRAL CLASS (Ron ruled 2026-09-27). Each construct below is declared in the
# grammar, checks clean today, and 500s at run inside an executor BRANCH (an if/elif/else within an
# executor or helper) -- invisible to the pure-deferral-executor gate. The refusal is right (there is
# no implementation), so announcing it at check stops nothing that runs; it only stops a program that
# CHECKS CLEAN from being deployed to fail later. Detection is FROM THE SOURCE ALONE, so it holds for
# every deployment and input (Ron's test). Constructs whose failing condition is only knowable at run
# (a provider's audio capability, the connected sql backend, a bare name's runtime binding) are NOT
# here -- they carry a DEFER-RUNTIME-ONLY annotation at their raise instead.
# One shared tag keeps the fail-loud backlog gate satisfied: T1-EXECUTOR-BRANCH-DEFERRALS.
_SUMMARIZE_BASIC = ('sum', 'count', 'average', 'max', 'min')
_LIST_AGG_UNBUILT = ('max', 'min', 'sum', 'average', 'total')
_UNBUILT_CASTS = ('csv', 'pdf', 'html')
_EQ_CONDITIONS = ('is', '', 'equals', 'eq')   # equality remove conditions that ARE built


def _collect_list_vars(program):
    """Names bound to a list literal (`as list`, `create list`) -- statically known to be a list.
    A write or aggregate targeting one of these bare names is the held-list case the runtime refuses.
    A db write uses `db.table` (a dotted name), never a bare list name, so this cannot false-refuse a
    real table write."""
    names = set()
    seen = set()

    def visit(n):
        if n is None or id(n) in seen:
            return
        seen.add(id(n))
        if type(n).__name__ == 'Assignment' and getattr(n, 'name', '') \
           and type(getattr(n, 'value', None)).__name__ == 'ListLiteral':
            names.add(n.name)
        if isinstance(n, (list, tuple)):
            for it in n:
                visit(it)
            return
        if is_dataclass(n):
            for fld in fields(n):
                visit(getattr(n, fld.name, None))

    visit(program)
    return names


def _bare_list_target(node_val, list_vars):
    """True if node_val names a known list variable. A write target arrives either as a plain string
    name (e.g. SaveOrUpdateBlock.source), a single-part DottedName, or a DbRef (db.table, never a
    list). A db write is always `db.table` -- a bare list name here is the held-list case."""
    if isinstance(node_val, str):
        return node_val in list_vars
    if type(node_val).__name__ == 'DottedName':
        parts = getattr(node_val, 'parts', None) or []
        return len(parts) == 1 and parts[0] in list_vars
    return False


def joined_csv_export_refusal(node):
    """The refusal for `export as.csv` on a joined find that has two `with` lists side by side at one level,
    else None. One wording, read by the check and by the run.

    A joined record flattens into CSV rows one per child, the parent's columns repeated, and a childless
    parent is one row with its child columns empty (RULED by Ron 2026-10-23, T1-JOIN-EXPORT-CSV-FLATTEN);
    a chain (`with` inside `with`) flattens one row per deepest child the same way. Two sibling lists have
    no single child per row: pairing them is a cross product nobody ruled, so that refuses, named. After a
    `summarize` the rows are flat and nothing is refused."""
    body = getattr(node, 'body', None) or []
    exp = next((c for c in body if type(c).__name__ == 'ExportClause'), None)
    if exp is None or str(getattr(exp, 'format', '') or '').lower() != 'csv' \
            or any(type(c).__name__ == 'SummarizeBlock' for c in body):
        return None

    def siblings(level):
        joins = [j for j in (level or []) if type(j).__name__ == 'JoinBlock']
        if len(joins) > 1:
            return joins
        return next((s for s in (siblings(getattr(j, 'body', None)) for j in joins) if s), None)

    pair = siblings(body)
    if not pair:
        return None
    kids = ' and '.join(f"`{getattr(j, 'name', '')}`" for j in pair)
    return (f"`export as.csv` writes one row per child, and this joined find has the lists {kids} side by "
            f"side, so a row has no single child: pairing them would be a cross product, which is not "
            f"decided (T1-JOIN-EXPORT-CSV-SIBLINGS).")


def scan_executor_branch_deferrals(program):
    """Announce at check the executor-branch deferrals that check clean and 500 at run."""
    errors = []
    seen = set()
    list_vars = _collect_list_vars(program)
    TAG = "T1-EXECUTOR-BRANCH-DEFERRALS"

    def err(msg, node):
        errors.append(CompileError(msg, line=getattr(node, 'line', 0) or 0))

    def _computed_names(body):
        out = set()
        for b in (body or []):
            if type(b).__name__ in ('CalculateBlock', 'SummarizeBlock'):
                for f in (getattr(b, 'fields', None) or []):
                    if getattr(f, 'name', ''):
                        out.add(f.name)
        return out

    def _body_kinds(body):
        return {type(b).__name__ for b in (body or [])}

    def visit(node):
        if node is None or id(node) in seen:
            return
        seen.add(id(node))
        k = type(node).__name__

        # summarize with an analytic function (only sum/count/average/max/min are built there)
        if k == 'SummarizeBlock':
            for f in (getattr(node, 'fields', None) or []):
                fn = (getattr(f, 'function', '') or '').lower()
                if fn and fn not in _SUMMARIZE_BASIC:
                    err(f"(T1-EXECUTOR-BRANCH-DEFERRALS) summarize cannot use `{fn}` -- summarize does {', '.join(_SUMMARIZE_BASIC)}; "
                        f"`{fn}` is an analytic function that belongs in a `calculate` block, and used "
                        f"here it 500s at run, so this refuses at check.", f)

        # export as.pdf / as.xlsx -- need a document library, not wired
        if k == 'ExportClause':
            fmt = (getattr(node, 'format', '') or '').lower()
            if fmt in ('pdf', 'xlsx'):
                err(f"(T1-EXECUTOR-BRANCH-DEFERRALS) `export as.{fmt}` is declared but not built -- pdf and xlsx need a document "
                    f"library that is not wired, so it 500s at run. Use `export as.csv` or "
                    f"`export as.json` today.", node)

        # cursor pagination -- not wired
        if k == 'CursorClause':
            err(f"(T1-EXECUTOR-BRANCH-DEFERRALS) cursor pagination (`cursor from ...`) is declared but not wired, so it 500s at run. "
                f"Use offset pagination (`paginate by N`) today.", node)

        # a query block: paginate + match-any/no-match, and order-by-a-computed-column
        if k in ('FindBlock', 'RetrieveBlock', 'GrabBlock'):
            body = getattr(node, 'body', None) or []
            kinds = _body_kinds(body)
            if 'PaginateClause' in kinds and ('MatchAnyBlock' in kinds or 'NoMatchBlock' in kinds):
                err(f"(T1-EXECUTOR-BRANCH-DEFERRALS) offset pagination (`paginate by N`) combined with `match any` / `no.match` is "
                    f"declared but not wired (the page counts would not reflect the OR/NOT filter), so "
                    f"it 500s at run. Refusing at check.", node)
            computed = _computed_names(body)
            for b in body:
                if type(b).__name__ == 'OrderClause' and getattr(b, 'field', '') in computed:
                    err(f"(T1-EXECUTOR-BRANCH-DEFERRALS) order_by_computed_column: ordering by `{b.field}` "
                        f"is not yet built -- `{b.field}` is a value this block computes, and ordering "
                        f"happens in the database before any computed value exists, so it 500s at run. "
                        f"Order by a real column instead.", b)

            # Native join (`with`) sub-forms not yet built. Each is a clause-type decision on the
            # find body -- source-decidable -- so it is refused here at check, mirroring the
            # run-time refusal in _exec_find_joined exactly (same conditions). `find` only.
            if k == 'FindBlock':
                _joins = [b for b in body if type(b).__name__ == 'JoinBlock']
                if _joins:
                    # JOINS UNIT 6 (ruled by Ron, 2026-10-03): more than one top-level `with` is
                    # now SIBLINGS -- each its own independent chain, each its own pushed-down
                    # query, all under one consistent read (`_run_join_siblings`). The ONE
                    # combination still refused is with.all alongside a sibling: its own
                    # matched-then-unmatched paged shape has not been designed for that case.
                    if len(_joins) > 1 and any(getattr(j, 'variant', '') == 'with.all'
                                               for j in _joins):
                        err("(T1-NATIVE-JOIN-NESTING-AND-UNIT6) `with.all` alongside a sibling "
                            "`with` is not built yet -- with.all's own matched-then-unmatched "
                            "paged shape has not been designed for the sibling case, so it 500s "
                            "at run. Use `with` / `with.required` for every sibling for now.",
                            node)
                    # with.all (full outer) WITH a bound now runs (JOINS UNIT 6, ruled by Ron
                    # 2026-10-03: "the with.all paging rule"), matched records first then
                    # unmatched, each page naming page.count/page.unmatched.count/
                    # page.unmatched.start/page.kind. Two narrower combinations stay refused,
                    # named rather than silently dropped: a per-parent `up to` together with the
                    # outer bound (the window-function cap and the matched/unmatched split have
                    # not been reconciled), and an outer `match` filter together with the outer
                    # bound (an orphan has no parent row for the filter to apply to).
                    if any(getattr(j, 'variant', '') == 'with.all' for j in _joins):
                        _inner_lim = any(type(b).__name__ == 'LimitClause'
                                         for j in _joins for b in (getattr(j, 'body', None) or []))
                        _outer_bound = bool(kinds & {'LimitClause', 'PaginateClause', 'SkipClause'})
                        _outer_filter = bool(kinds & {'MatchClause', 'MatchBlock'})
                        if _outer_bound and _inner_lim:
                            err("(T1-NATIVE-JOIN-NESTING-AND-UNIT6) `with.all` combined with an "
                                "outer `up to` / page / skip AND a per-parent `up to` together is "
                                "not built yet (the per-parent window cap and the matched/"
                                "unmatched page split have not been reconciled), so it 500s at "
                                "run. Drop one of the two bounds.", node)
                        if _outer_bound and _outer_filter:
                            err("(T1-NATIVE-JOIN-NESTING-AND-UNIT6) `with.all` combined with an "
                                "outer `up to` / page / skip AND an outer `match` filter together "
                                "is not built yet (an orphan has no parent row for the filter to "
                                "apply to), so it 500s at run. Drop the outer `match` or the "
                                "outer bound.", node)
                    if 'CursorClause' in kinds:
                        err("(T1-EXECUTOR-BRANCH-DEFERRALS) cursor pagination combined with a `native join` "
                            "is not built yet (cursor pagination is not wired on any find), so it 500s at "
                            "run. Use `up to N` with `paginate by` for offset pagination.", node)
                    # JOINS UNIT 4: WhereClause/AndClause on the OUTER query is now built (the
                    # chain executor, _exec_find_joined_chain) -- removed from this refused set.
                    # Everything else here (OR/NOT groups, calculate, a computed return) is still
                    # genuinely not built for a joined find. A `return` of plain fields IS built
                    # (BS-010, RULED by Ron 2026-10-21): it trims the outer row and leaves each
                    # `with` child nested. `summarize` and `export` consume the joined result
                    # (T1-JOIN-SUMMARIZE-EXPORT); only a CSV export of nested records refuses.
                    _csv = joined_csv_export_refusal(node)
                    if _csv is not None:
                        err(_csv + " Export it as.json, which keeps each list with its record, or export "
                                   "each list from a find of its own.", node)
                    _refused = {'MatchAnyBlock', 'NoMatchBlock', 'TimespanRef', 'SinceClause',
                                'CalculateBlock', 'ReturnClause'}
                    _ret = next((b for b in body if type(b).__name__ == 'ReturnClause'), None)
                    if _ret is not None and getattr(_ret, 'fields', None) and all(
                            f.get('kind') == 'field' and f.get('field') for f in _ret.fields):
                        _refused = _refused - {'ReturnClause'}
                    if kinds & _refused:
                        err("(T1-EXECUTOR-BRANCH-DEFERRALS) a `native join` supports `match` and `where`/`and` "
                            "filters on the outer query; OR/NOT groups, calculate/summarize, a computed "
                            "return and export are not built for a joined find yet, and 500 at run. Use "
                            "`match`/`where` for now.", node)
                    # EVERY DEPTH (2026-10-12). This walked only a top-level `with`'s own body, so a
                    # mistake two levels down was caught at run, not check. Each `with` at any depth
                    # gets the same two checks: a nested `with.all` refuses, and its body keeps to
                    # the built vocabulary. Siblings inside a `with`, at any depth, are built
                    # (`_run_join_tree`, RULED by Ron 2026-10-12).
                    # `with.all` WHERE SIBLINGS LIVE, mirrored from the run (`_run_join_tree`): once a
                    # find has siblings inside a `with` at any depth, a top-level `with.all` refuses at
                    # run, so it refuses here too rather than check clean and 500.
                    def _has_sibs(_j):
                        _kids = [b for b in (getattr(_j, 'body', None) or [])
                                 if type(b).__name__ == 'JoinBlock']
                        return len(_kids) > 1 or any(_has_sibs(_k) for _k in _kids)
                    if any(_has_sibs(_j) for _j in _joins):
                        for _j in _joins:
                            if getattr(_j, 'variant', '') == 'with.all':
                                err("(T1-NATIVE-JOIN-NESTING-AND-UNIT6) `with.all` beside siblings, "
                                    "or with siblings inside it, is not built yet -- its "
                                    "matched-then-unmatched shape has not been designed for "
                                    "siblings. Use `with` / `with.required` for this join, or give "
                                    "`with.all` its own find.", _j)
                    _all_joins, _stack = [], list(_joins)
                    while _stack:
                        _jj = _stack.pop(0)
                        _all_joins.append(_jj)
                        _stack.extend(b for b in (getattr(_jj, 'body', None) or [])
                                      if type(b).__name__ == 'JoinBlock')
                    for j in _all_joins:
                        _nested = [b for b in (getattr(j, 'body', None) or [])
                                  if type(b).__name__ == 'JoinBlock']
                        # NESTING (T1-NATIVE-JOIN-NESTING-AND-UNIT6): the grammar parses a `with`
                        # inside a `with` (no depth cap), but executing one is not built yet --
                        # refused here by name rather than falling into the generic "only match/
                        # up to" message below, which was written before nesting could parse at
                        # all and does not say WHICH mistake this is.
                        for nj in _nested:
                            if getattr(nj, 'variant', '') == 'with.all':
                                # `with.all` nested below the top level is REFUSED, not "not built
                                # yet": a full outer join's unmatched-parent bucket has no defined
                                # meaning nested inside another join's result, so this is a
                                # permanent rule, not a later unit.
                                err("(T1-NATIVE-JOIN-NESTING-AND-UNIT6) `with.all` is refused "
                                    "anywhere but the top level of a `find` -- nested below "
                                    "another `with`, its unmatched-parent bucket has no defined "
                                    "meaning. Use `with` / `with.required` for a nested join, or "
                                    "move `with.all` to the top level.", nj)
                            # JOINS UNIT 4: a `with`/`with.required` nested inside another `with`
                            # -- a CHAIN, as deep as the program writes -- is now built
                            # (_exec_find_joined_chain). No refusal here any more for that case.
                        # JOINS UNIT 4: `where`/`and`/`order.` inside a `with`'s own body, and nested
                        # `with`/`with.required` blocks (one or several), are built, at every depth.
                        if any(type(b).__name__ not in
                               ('MatchClause', 'LimitClause', 'JoinBlock', 'WhereClause',
                                'AndClause', 'OrderClause')
                               for b in (getattr(j, 'body', None) or [])):
                            err("(T1-EXECUTOR-BRANCH-DEFERRALS) inside a `native join`, only `match`, "
                                "`where`/`and`, `order.`, a per-parent `up to N`, and nested `with` "
                                "blocks are built; anything else 500s at run. Keep the `with` body to "
                                "those forms.", j)

        # pull with a filter -- filtering a pull is not built
        if k == 'PullBlock':
            body = getattr(node, 'body', None) or []
            if _body_kinds(body) & {'MatchClause', 'WhereClause', 'AndClause', 'OrClause',
                                    'MatchAnyBlock', 'NoMatchBlock'}:
                err(f"(T1-EXECUTOR-BRANCH-DEFERRALS) filtering a `pull` with `match` / `where` is declared but not built -- the runtime "
                    f"fetches the rows unfiltered, so this 500s at run rather than hand back the wrong "
                    f"rows. Use `find` with the filter today.", node)

        # flow: / serves: journey metadata -- declared, not interpreted/enforced
        if k == 'JourneyMeta':
            kind = getattr(node, 'kind', '')
            if kind == 'flow':
                err(f"(T1-EXECUTOR-BRANCH-DEFERRALS) `flow:` is declared but not yet interpreted -- it has no runtime behaviour in "
                    f"this build and 500s at run. Remove it for now.", node)
            elif kind == 'serves' and getattr(node, 'value', '') == 'multiple tenants':
                err(f"(T1-EXECUTOR-BRANCH-DEFERRALS) `serves: multiple tenants` is declared but not yet enforced -- it needs a "
                    f"request-scoped tenant identity the language does not have yet, and 500s at run. "
                    f"Remove it for now.", node)

        # debug minimal -- the light-set filter is not built
        if k == 'DebugDecl' and (getattr(node, 'mode', '') or '').lower() == 'minimal':
            err(f"(T1-EXECUTOR-BRANCH-DEFERRALS) `debug minimal` is declared but its light-set filter is not built, so it 500s at run. "
                f"Use `debug on` or `debug verbose` today.", node)

        # unimplemented casts as.csv / as.pdf / as.html
        if k == 'TypeCastExpr' and (getattr(node, 'cast_type', '') or '').lower() in _UNBUILT_CASTS:
            ct = node.cast_type.lower()
            err(f"(T1-EXECUTOR-BRANCH-DEFERRALS) `as.{ct}` is declared but not implemented, so it 500s at run. Serialize in "
                f"application logic for now.", node)

        # miovalidate: unique / scheme rules are not enforceable
        if k == 'MiovalidateDecl':
            for rule in (getattr(node, 'rules', None) or []):
                for mod in (getattr(rule, 'modifiers', None) or []):
                    mk = mod.get('kind') if isinstance(mod, dict) else None
                    if mk in ('unique', 'scheme'):
                        err(f"(T1-EXECUTOR-BRANCH-DEFERRALS) the `{mk}` validation rule on `{getattr(rule, 'field_name', '?')}` is not "
                            f"enforceable in this build, so it 500s at run. Remove it for now.",
                            rule)

        # remove with a non-equality condition (only exact match is built)
        if k == 'RemoveBlock':
            for w in (getattr(node, 'body', None) or []) + list(_as_list(getattr(node, 'condition', None))):
                if type(w).__name__ == 'WhereClause':
                    cond = (getattr(w, 'condition', '') or '').lower()
                    if cond and cond not in _EQ_CONDITIONS:
                        err(f"(T1-EXECUTOR-BRANCH-DEFERRALS) `remove` supports only an exact-match condition, not `{cond}`, so a "
                            f"range/contains delete 500s at run. Use `where <field> is <value>` "
                            f"(exact match) today.", w)
            if _bare_list_target(getattr(node, 'source', None), list_vars):
                err(f"(T1-EXECUTOR-BRANCH-DEFERRALS) `remove` over a held list is declared but not built, so it 500s at run. Mutating "
                    f"a held list is tracked work; connect a real database to remove rows today ", node)

        # the list-write family over a held list (db writes use db.table, never a bare list name)
        if k == 'SaveBlock' and _bare_list_target(getattr(node, 'target', None), list_vars):
            err(f"(T1-EXECUTOR-BRANCH-DEFERRALS) `save` over a held list is declared but not built, so it 500s at run. Connect a real "
                f"database and `save to db.<table>` today.", node)
        if k == 'SaveAllBlock' and (_bare_list_target(getattr(node, 'target', None), list_vars)):
            err(f"(T1-EXECUTOR-BRANCH-DEFERRALS) `save all` over a held list is declared but not built, so it 500s at run. Connect a "
                f"real database and `save all to db.<table>` today.", node)
        if k == 'SaveOrUpdateBlock' and _bare_list_target(getattr(node, 'source', None), list_vars):
            err(f"(T1-EXECUTOR-BRANCH-DEFERRALS) `save or update` over a held list is declared but not built, so it 500s at run. "
                f"Connect a real database today.", node)
        if k == 'UpdateBlock' and _bare_list_target(getattr(node, 'source', None), list_vars):
            err(f"(T1-EXECUTOR-BRANCH-DEFERRALS) `update` over a held list is declared but not built, so it 500s at run. Connect a "
                f"real database to update rows today.", node)
        if k == 'ModifyBlock' and _bare_list_target(getattr(node, 'collection', None), list_vars):
            err(f"(T1-EXECUTOR-BRANCH-DEFERRALS) `modify` over a held list of plain values is declared but not built, so it 500s at "
                f"run. `modify` over a list of records already works.", node)

        # .max / .min / .sum / .average / .total on a held list (only .first/.last/.count/.pos are built)
        if k == 'DottedName':
            parts = getattr(node, 'parts', None) or []
            if len(parts) == 2 and parts[0] in list_vars and str(parts[1]).lower() in _LIST_AGG_UNBUILT:
                err(f"(T1-EXECUTOR-BRANCH-DEFERRALS) `{parts[0]}.{parts[1]}` is not a built list operation -- first, last, count and "
                    f"position.N are built; aggregates (max/min/sum/average/total) are not, so it 500s "
                    f"at run.", node)
            # RESULT-WORDS RENAME (T1-RESULT-WORDS-REGISTERED, 2026-10-03): `<result>.page.<field>`
            # is a CLOSED, known vocabulary (PAGE_FIELDS in mohio_interpreter.py), unlike
            # `request.*` which is whatever the program/payload names. `page` at parts[1] (not
            # parts[0]) is this namespace specifically -- parts[0] == 'page' is the unrelated
            # page.header/page.footer layout namespace and is never touched here. Mirrors the
            # runtime refusal in Context.get_dotted exactly (same message, same function), so a
            # retired or misspelled page field is caught here, at check, not on the first request.
            if len(parts) >= 3 and str(parts[1]) == 'page':
                from mohio_runtime_vocab import PAGE_FIELDS, _page_field_error
                sub_key = str(parts[2])
                if sub_key not in PAGE_FIELDS:
                    err(f"(T1-RESULT-WORDS-REGISTERED) {_page_field_error(sub_key, str(parts[0]))}",
                        node)

        # applang is NOT handled here: it transforms to a raw Tree (no dataclass node), so it never
        # reaches this walk. It is already flagged at check by scan_unwired ("recognized but not
        # executable"), a warning rather than a silent pass, and 500s at run (annotated there).

        if isinstance(node, (list, tuple)):
            for it in node:
                visit(it)
            return
        if is_dataclass(node):
            for fld in fields(node):
                visit(getattr(node, fld.name, None))

    visit(program)

    # nested saga: a saga that runs inside another saga -- directly, or via a task the outer saga
    # calls. The runtime guard is a runtime `_in_saga` flag, but the NESTING is static: a SagaDecl
    # inside another SagaDecl, or a SagaDecl whose call graph reaches a task that opens a saga.
    def _contains(n, kind, s):
        if n is None or id(n) in s:
            return False
        s.add(id(n))
        if type(n).__name__ == kind:
            return True
        if isinstance(n, (list, tuple)):
            return any(_contains(it, kind, s) for it in n)
        if is_dataclass(n):
            return any(_contains(getattr(n, f.name, None), kind, s) for f in fields(n))
        return False

    tasks_with_saga = {getattr(st, 'name', '')
                       for st in (getattr(program, 'statements', None) or [])
                       if type(st).__name__ == 'TaskDecl'
                       and _contains(getattr(st, 'body', None), 'SagaDecl', set())}

    def _calls_saga_task(n, s):
        if n is None or id(n) in s:
            return False
        s.add(id(n))
        if type(n).__name__ in ('CallBlock', 'RunBlock') \
                and getattr(n, 'task_name', '') in tasks_with_saga:
            return True
        if isinstance(n, (list, tuple)):
            return any(_calls_saga_task(it, s) for it in n)
        if is_dataclass(n):
            return any(_calls_saga_task(getattr(n, f.name, None), s) for f in fields(n))
        return False

    saga_seen = set()

    def _saga_walk(n, inside):
        if n is None or id(n) in saga_seen:
            return
        saga_seen.add(id(n))
        is_saga = type(n).__name__ == 'SagaDecl'
        if is_saga:
            if inside:
                err("(T1-EXECUTOR-BRANCH-DEFERRALS) nested sagas are not supported -- this saga runs inside another saga, which "
                    "500s at run. Flatten the steps or extract the inner work into non-saga tasks ", n)
            elif _calls_saga_task(getattr(n, 'body', None), set()) \
                    or _calls_saga_task(getattr(n, 'steps', None), set()):
                err("(T1-EXECUTOR-BRANCH-DEFERRALS) nested sagas are not supported -- this saga calls a task that opens a saga, which "
                    "500s at run. Flatten the steps or extract the inner work into non-saga tasks ", n)
        child_inside = inside or is_saga
        if isinstance(n, (list, tuple)):
            for it in n:
                _saga_walk(it, child_inside)
            return
        if is_dataclass(n):
            for f in fields(n):
                _saga_walk(getattr(n, f.name, None), child_inside)

    _saga_walk(program, False)
    return errors


def scan_mongo_join_unbuilt(program):
    """JOINS UNIT 5 (ruled by Ron, 2026-10-03): three Mongo join forms are refused at CHECK,
    each the defence-in-depth copy of a runtime refusal already in MongoRuntime -- `with.all`
    (join_select: a $lookup only produces the LEFT-join shape; the orphan-children half needs
    a second aggregation and a $unionWith, a later unit), a nested `with` chain, and more than
    one top-level `with` (siblings) -- both refused by join_select_chain, since
    _run_join_chain_query/_run_join_siblings call it regardless of engine and the SQL base
    class's own chain builder has no cursor on a document store. Query connection resolution
    today only wires the connection named `db` (scan_query_connection_name's own refusal for
    any other name), so finding `db`'s declared driver is finding the one connection this scan
    needs to know about -- not a general multi-connection resolver.
    """
    errors = []
    declared = {}   # connection name -> driver string, lowercased

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
            driver = str(getattr(node, 'driver', None)
                        or getattr(node, 'connect_type', '') or '').lower()
            if n:
                declared[n] = driver
        for f in fields(node):
            collect(getattr(node, f.name, None))

    collect(program)
    if not any(d in ('mongodb', 'mongo') for d in declared.values()):
        return errors   # nothing to check -- no Mongo connection declared at all

    def walk(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                walk(c)
            return
        if not is_dataclass(node):
            return
        if type(node).__name__ in ('FindBlock', 'RetrieveBlock'):
            src = getattr(node, 'source', None)
            parts = getattr(src, 'parts', None) or []
            conn_name = str(parts[0]) if parts else 'db'
            driver = declared.get(conn_name, declared.get('db', ''))
            if driver in ('mongodb', 'mongo'):
                _joins = [b for b in (getattr(node, 'body', None) or [])
                         if type(b).__name__ == 'JoinBlock']
                for b in _joins:
                    if getattr(b, 'variant', '') == 'with.all':
                        errors.append(CompileError(
                            "(T1-JOINS-UNIT-5-MONGO) `with.all` is not built on the Mongo "
                            "backend -- a $lookup only produces the LEFT-join shape (every "
                            "parent, matched or not); the orphan-children half needs a second "
                            "aggregation and a $unionWith, which is a later unit, so it 500s at "
                            "run. Use `with` / `with.required` on Mongo for now.",
                            line=getattr(b, 'line', 0) or getattr(node, 'line', 0) or 0))
                    _nested = [c for c in (getattr(b, 'body', None) or [])
                              if type(c).__name__ == 'JoinBlock']
                    if _nested:
                        errors.append(CompileError(
                            "(T1-JOINS-UNIT-5-MONGO) a nested `with` chain is not built on "
                            "the Mongo backend -- JOINS UNIT 5 built the single-level "
                            "$lookup join only, so it 500s at run. Use a single-level `with` "
                            "on Mongo for now, or move the chain to a SQL backend.",
                            line=getattr(b, 'line', 0) or getattr(node, 'line', 0) or 0))
                if len(_joins) > 1:
                    errors.append(CompileError(
                        "(T1-JOINS-UNIT-5-MONGO) more than one top-level `with` (siblings) "
                        "is not built on the Mongo backend -- JOINS UNIT 5 built the "
                        "single-level $lookup join only, so it 500s at run. Use one `with` "
                        "per find on Mongo for now, or move the siblings to a SQL backend.",
                        line=getattr(node, 'line', 0) or 0))
                # A FILTER OR ORDER around the join takes the chain path, which Mongo has not
                # built (the runtime's _join_uses_unit4_features): an outer `where`/`and`, or a
                # `where`/`and`/order inside the `with`. Measured 2026-10-06: it checked clean
                # and 500'd at run with a message about chains the program did not have.
                _filtered = [b for b in (getattr(node, 'body', None) or [])
                             if type(b).__name__ in ('WhereClause', 'AndClause')]
                _inner = [c for b in _joins for c in (getattr(b, 'body', None) or [])
                          if type(c).__name__ in ('WhereClause', 'AndClause', 'OrderClause')]
                if _joins and (_filtered or _inner):
                    errors.append(CompileError(
                        "(T1-JOINS-UNIT-5-MONGO) a `with` join filtered by `where`/`and` "
                        + ("on the outer find" if _filtered else "or ordered inside the `with`")
                        + " is not built on the Mongo backend -- JOINS UNIT 5 built the plain "
                        "single-level $lookup join, matched by `match`, so it 500s at run. "
                        "Use `match` to filter the outer find on Mongo for now, or run the join "
                        "on a SQL backend.",
                        line=getattr((_filtered or _inner)[0], 'line', 0)
                        or getattr(node, 'line', 0) or 0))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    walk(program)
    return errors


def _as_list(x):
    if x is None:
        return []
    return x if isinstance(x, (list, tuple)) else [x]


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
    # T1-DB-TYPED-STORAGE (RULED by Ron, 2026-10-05): `zone as timezone` -- a field whose value
    # is a zone name, validated on save against mohio_timezones.is_valid_timezone_name.
    'timezone',
    # CURRENCY TYPES. Each formats and rounds to its own places, and most are built on dec.2 --
    # but not all: `jpy` and `krw` have no minor unit and round to whole units, and the dinar
    # group below rounds to THREE places, which is why this is a list of currencies rather than
    # an alias for one decimal shape.
    'usd', 'eur', 'gbp', 'jpy', 'chf', 'cad', 'aud', 'cny', 'hkd', 'sgd', 'inr', 'nzd', 'sek', 'nok', 'mxn', 'brl', 'zar', 'krw',
    # THREE-DECIMAL DINARS (ISO 4217 minor unit 3): Bahrain, Kuwait, Oman, Jordan, Tunisia, Iraq,
    # Libya. Divided into 1000, so places=3 in _CURRENCIES keeps the third digit that a two-place
    # currency would silently drop.
    'bhd', 'kwd', 'omr', 'jod', 'tnd', 'iqd', 'lyd',
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


def scan_list_type_word_declared(program):
    """`prices as list eur`, where `eur` is a value the program declares, refuses at check.

    One word after `as list` is the element TYPE of an empty list (`errors as list text`). `eur` is
    also a currency type, so the line made an empty list of EUR amounts, while the coder meant a list
    holding the value `eur`: the list came out empty, with no error. A word that is a declared value
    and also reads as a type cannot say which it means."""
    declared = {}
    for n in _all_nodes(program):
        if type(n).__name__ == 'Assignment' and getattr(n, 'name', None) and getattr(n, 'value', None) is not None:
            declared.setdefault(str(n.name), getattr(n, 'line', 0) or 0)
        elif type(n).__name__ == 'Assignment' and getattr(n, 'name', None) and getattr(n, 'type_name', None) \
                and not str(n.type_name).startswith('list'):
            declared.setdefault(str(n.name), getattr(n, 'line', 0) or 0)
    errors = []
    for n in _all_nodes(program):
        if type(n).__name__ != 'Assignment' or getattr(n, 'value', None) is not None:
            continue
        tn = str(getattr(n, 'type_name', '') or '')
        if not tn.startswith('list '):
            continue
        word = tn[len('list '):].strip().replace('__USERVAR__', '')
        if word in declared and word != str(n.name):
            line = getattr(n, 'line', 0) or 0
            errors.append(CompileError(
                f"Line {line}: `{n.name} as list {word}` reads `{word}` as the type of an empty list, "
                f"and `{word}` is also a value this program declares (line {declared[word]}). The list "
                f"would start empty, not holding `{word}`.",
                line=line,
                hint=(f"For a list holding the value, write it as a block:\n        create list {n.name}\n"
                      f"            {word}\n        create: done\n    For an empty list of a type, write "
                      f"the type (`{n.name} as list text`, or a currency in capitals, `as list EUR`).")))
    return errors


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


# ── Batch 35 Part Two: the silent vocabulary ──────────────────────────────────
# Orphan terminals no rule consumes. Written at a statement position, each silently became a
# variable (`read "x"` parsed as a variable named `read`), so a word the language advertises did
# nothing with no error -- the parse-as-variable class the Batch 33 census confirmed. Ron ruled
# these REFUSE, each pointing at what to use, or saying "not built yet" where that is the truth --
# a different thing to hear than "you made a mistake". Detection is by AST NODE, so a shape FIELD
# named `read` in a save block (a FieldValue, never an Assignment) is untouched: only the
# variable-declaration use is refused.
_ORPHAN_VAR_REFUSALS = {
    'read':    ("`read` is not a Mohio verb; written on its own it silently becomes a variable named `read`",
                "To read data use `retrieve` for one record or `find` for many:\n"
                "        retrieve book from db.books\n            match id to 1\n        retrieve: done"),
    'screen':  ("`screen` is declared but NOT built yet -- it arrives with the mobile surface, so today it does nothing",
                "Remove it for now, or track it for the mobile release."),
    'layout':  ("`layout` is declared but NOT built yet -- it arrives with the mobile surface, so today it does nothing",
                "Remove it for now, or track it for the mobile release."),
    'decision':("`decision` is NOT built yet -- it is planned, not retired, so today it does nothing",
                "For an AI decision today use `ai.decide`:\n"
                "        ai.decide is_spam returns boolean\n            confidence above 0.85\n"
                "            weigh message.body\n        ai.decide: done"),
    'quality': ("`quality` is NOT built yet -- it is planned, not retired, so today it does nothing",
                "Remove it for now, or track it for a future release."),
    # VERBOSE_KW / MINIMAL_KW were stray duplicate terminals; the debug family is built and fires
    # through its own two-word form (Batch 35 Part Two). A bare `verbose` / `minimal` silently
    # became a variable, so it is refused pointing at the working form.
    'verbose': ("`verbose` on its own is not a Mohio word; it silently becomes a variable",
                "Turn on verbose debug output with the debug family's two-word form:\n        debug verbose"),
    'minimal': ("`minimal` on its own is not a Mohio word; it silently becomes a variable",
                "Turn on minimal debug output with the debug family's two-word form:\n        debug minimal"),
    # THE REST OF THE PARSE-AS-VARIABLE SET the reference names (Ron, 2026-10-13, with app config):
    # each is a real word in its own position, and written alone it became a variable, ran, and did
    # nothing. Each points at the position where the word works (every form below run 2026-10-13).
    'percentage': ("`percentage` on its own is not a statement; written alone it silently becomes a variable "
                   "named `percentage`",
                   "Work out a percentage with the math form:\n        percentage 20 of 50 as share"),
    'purpose': ("`purpose` opens a block around the statements it governs; written alone it silently becomes a "
                "variable named `purpose`",
                "Wrap the statements it governs:\n        purpose \"billing\"\n            show \"...\"\n"
                "        purpose: done"),
    'chars':   ("`chars` is the unit of a truncation, not a statement; written alone it silently becomes a "
                "variable named `chars`",
                "Truncate text to a number of characters:\n        show body truncate.to 40 chars"),
    'trusted': ("`trusted` follows a `give back`, sending that one response without the automatic escaping; "
                "written alone it silently becomes a variable named `trusted`",
                "Mark the one response that is meant as raw HTML:\n        give back [200] page_html trusted"),
}
_ORPHAN_DOTTED_REFUSALS = {
    ('on', 'change'): ("`on.change` is not a Mohio form; the change listener needs no event-modifier word",
                       "Listen for a change with a selector:\n        listen for change on #field"),
    ('modify', 'by'): ("`modify.by` is not built -- `by` has no use case yet (`as` names a result, `in` places it)",
                       "Change records with `modify` and an `apply` block:\n"
                       "        modify every r in db.rows\n            apply r\n        modify: done"),
    ('not', 'empty'): ("`not.empty` (dotted) is not the built form; written alone it silently becomes a call",
                       "Write emptiness as words in a condition:\n        when x is not empty"),
    ('not', 'found'): ("`not.found` is not built and tests nothing; written alone it silently becomes a call",
                       "Handle a missing record with `on.failure`, or `when empty` after a find:\n"
                       "        find rows in db.t\n            on.failure\n"
                       "                give back [404] \"not found\"\n        find: done"),
}
APP_CONFIG_FIX = ("Remove the block. A deployment setting today is read where it is used, with `env.NAME` (give "
                  "it a default when it may be unset), until application-level settings arrive after the tag.")


def app_config_refusal(line, word):
    """The one teaching message for every form of `app config` / `config` (T1-APP-CONFIG-SILENT-SETTINGS):
    shared by this scan, the transform's own block refusal and the closer and parser-error paths."""
    return ((f"Line {line}: " if line else "")
            + f"`{word}` declares application-level settings, and those are not built yet "
              f"(T1-APP-CONFIG-SILENT-SETTINGS, after the tag), so nothing would read them; it used to "
              f"become a variable with nothing said")


_DEVICE_PERMISSIONS = {
    'camera':    'the camera',
    'location':  'device location',
    'biometric': 'biometric authentication',
    'push':      'push notifications',
}

def scan_orphan_vocabulary(program):
    """Refuse the parse-as-variable orphan words (Batch 35 Part Two), loud and teaching."""
    errors = []
    def _err(node, what, fix):
        errors.append(CompileError(what + ".", line=getattr(node, 'line', 0) or 0, hint=fix))
    def walk(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                walk(c)
            return
        if not is_dataclass(node):
            return
        t = type(node).__name__
        if t == 'Assignment':
            nm = getattr(node, 'name', None)
            # `app config` / `config` read as a variable are refused earlier, in the transform
            # (`_refuse_app_config_assignment`), so they never reach this scan.
            if nm in _ORPHAN_VAR_REFUSALS:
                _err(node, *_ORPHAN_VAR_REFUSALS[nm])
            elif nm == 'require':
                val = getattr(node, 'value', None)
                dv = val
                if is_dataclass(val):
                    if type(val).__name__ == 'DottedName':
                        _parts = getattr(val, 'parts', None) or []
                        dv = _parts[0] if len(_parts) == 1 else None
                    else:
                        dv = getattr(val, 'value', None)
                if isinstance(dv, str) and dv in _DEVICE_PERMISSIONS:
                    _err(node,
                         f"`require {dv}` is declared but NOT built yet -- {_DEVICE_PERMISSIONS[dv]} "
                         f"arrives with the mobile surface, so it is a coming feature, not a mistake",
                         f"Track it for the mobile release. For server-side access control today, "
                         f"gate the handler with `require role \"...\"` and assign roles with `grant role`.")
        elif t == 'ServiceCallStmt':
            key = (getattr(node, 'service', None), getattr(node, 'method', None))
            if key in _ORPHAN_DOTTED_REFUSALS:
                _err(node, *_ORPHAN_DOTTED_REFUSALS[key])
        for f in fields(node):
            walk(getattr(node, f.name, None))
    walk(program)
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
            # an alias (`mioconnect Payments as Pay`) is a second declared name for the
            # same connector; the runtime governs it by the declared name
            for n in (getattr(node, 'name', ''), getattr(node, 'alias', '')):
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
    # Native join (with / with.required). `find` executes it now; forms not yet built (with.all,
    # per-parent `up to`, a join combined with outer pagination, a join on a Mongo source) refuse
    # at run with a pointer, not at check, so check no longer defers `join` on find.
    'JoinBlock',
)
_RETRIEVE_CONSUMED = (
    'MatchClause', 'MatchBlock', 'MatchAnyBlock', 'NoMatchBlock',
    # JOINS UNIT 6 (ruled by Ron, 2026-10-03): a native join now runs on `retrieve`, through
    # the same chain executor `find` uses -- only the result binding differs (retrieve's own
    # modifier, not always a list). Outer vocabulary is still `match` only, matching
    # retrieve's own pre-existing restriction; a `with` block's INNER where/and/order/up to
    # is a separate grammar scope (cur_join.body), unaffected by this line.
    'JoinBlock',
)
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
            line = getattr(b, 'line', 0) or getattr(node, 'line', 0) or 0
            if kind == 'FindBlock':
                # Unbuilt query clause on `find` -- a tracked deferral (T1-RETRIEVE-CLAUSE-UNBUILT):
                # find applies where/up to/order/export; any other clause refuses, never drops.
                errors.append(CompileError(
                    "find: `%s` is declared but not yet built on `find` -- find would return "
                    "the rows without applying `%s`, so it refuses rather than hand back a "
                    "result that looks filtered and is not. To proceed: remove `%s` for now, "
                    "or use a built clause (`where`, `up to`, `order`, `export`)."
                    % (word, word, word), line=line))
            else:
                # retrieve applies only `match`. `find` is the collection verb that applies
                # where/up to/order/export, so the fix is almost always the rename -- name it.
                errors.append(CompileError(
                    "retrieve: `%s` is declared but not yet built on `retrieve` -- retrieve "
                    "applies only `match`, so it would fetch the rows and hand them back without "
                    "applying `%s`, and a filter or export silently dropped from a read is wrong "
                    "data. `find` is the collection verb that applies these clauses: rename "
                    "`retrieve` to `find`. For a single-row export, use `find` with a `match` "
                    "and `up to 1`."
                    % (word, word), line=line))

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


def scan_unknown_db_driver(program):
    """A `connect db as <engine>` naming an engine Mohio does not know is refused at CHECK, with the
    SAME message the runtime factory gives at run, so a typo like `as oracle` is caught before deploy
    rather than only when the connection is first opened. The engine list comes from
    mohio_db_drivers -- the one source the runtime factory also reads -- so adding an engine updates
    both the check and the run at once.
    """
    from mohio_db_drivers import is_supported_driver, unknown_driver_message
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
        if type(node).__name__ == 'ConnectDecl':
            driver = getattr(node, 'driver', None) or getattr(node, 'connect_type', '') or ''
            if not is_supported_driver(driver):
                errors.append(CompileError(unknown_driver_message(driver),
                                           line=getattr(node, 'line', 0) or 0))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    walk(program)
    return errors


# Oracle folds an unquoted identifier to UPPER case, and before 12.2 capped it at 30 characters. We
# use the modern 128-character limit (12.2 / 19c / 23ai, what current deployments run). RULING-5 KNOB
# for Ron: if pre-12.2 (Oracle 12.1) support is required, lower this to 30 -- it is the one value in
# this scan that is a policy choice rather than a fact.
_ORACLE_MAX_IDENTIFIER = 128


def scan_oracle_identifier_safety(program):
    """When a program targets Oracle, refuse at CHECK the identifiers Oracle would reject at run.

    Mohio writes UNQUOTED identifiers on Oracle (ruling 5: so a bank DBA or a reporting tool can query
    Mohio tables without quoted names). Oracle folds an unquoted identifier to upper case, so two field
    names in one shape-governed table that differ only by case collide into a single column (ORA-00957
    at run), and an identifier over the length limit is rejected. Both are caught here, before deploy,
    instead of surfacing as a 500 on first write. Fires only when the program declares an Oracle
    connection -- every other engine quotes identifiers and keeps case, so the same names are fine.
    """
    errors = []
    targets_oracle = [False]

    def _find_oracle(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                _find_oracle(c)
            return
        if not is_dataclass(node):
            return
        if type(node).__name__ == 'ConnectDecl':
            d = (getattr(node, 'driver', None) or getattr(node, 'connect_type', '') or '').lower()
            if d == 'oracle':
                targets_oracle[0] = True
        for f in fields(node):
            _find_oracle(getattr(node, f.name, None))

    _find_oracle(program)
    if not targets_oracle[0]:
        return []

    def _check(table_name, field_names, line):
        if table_name and len(table_name) > _ORACLE_MAX_IDENTIFIER:
            errors.append(CompileError(
                "table name '%s' is %d characters, over Oracle's %d-character identifier limit, so "
                "Oracle rejects it at run. Shorten the table name."
                % (table_name, len(table_name), _ORACLE_MAX_IDENTIFIER), line=line))
        seen = {}
        for fn in field_names:
            if fn and len(fn) > _ORACLE_MAX_IDENTIFIER:
                errors.append(CompileError(
                    "field '%s' is %d characters, over Oracle's %d-character identifier limit, so "
                    "Oracle rejects it at run. Shorten the field name."
                    % (fn, len(fn), _ORACLE_MAX_IDENTIFIER), line=line))
            key = (fn or '').lower()
            if key in seen and seen[key] != fn:
                errors.append(CompileError(
                    "fields '%s' and '%s' differ only in case, which Oracle cannot tell apart: it "
                    "folds unquoted names to upper case, so both become one column and the write "
                    "fails at run. Rename one so the two differ by more than case."
                    % (seen[key], fn), line=line))
            elif key not in seen:
                seen[key] = fn

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
            line = getattr(node, 'line', 0) or 0
            for tname, tbl in (getattr(node, 'tables', None) or {}).items():
                fnames = [str(getattr(_f, 'name', '')) for _f in (getattr(tbl, 'fields', None) or [])
                          if getattr(_f, 'name', None)]
                _check(str(tname), fnames, line)
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
            if svc == 'mask':
                # The bare `mask.all` / `mask.last` shorthand is not a form (A9, decision B): it parses
                # as its own call, so the value it follows is never masked. It refused at run while
                # checking clean; the same words now refuse here.
                errors.append(CompileError(
                    f"mask.{mth} is not a Mohio form, so the value before it is never masked. To mask "
                    f"sensitive data write `value mask.all except last N` or `value mask.all except "
                    f"first N`: it shows only the last or first N characters and masks the rest.",
                    line=_line))
            elif svc == 'as':
                # Every real cast parses as a cast, so an `as.<name>` that reaches the generic call is
                # a cast Mohio does not have -- a mistake, not an unbuilt feature. It also reads the
                # NEXT line as its argument, so the line after it silently stops running.
                errors.append(CompileError(
                    f"as.{mth} is not a cast Mohio has, so the value is never converted and the line "
                    f"after it is read as part of this one. To fix it, use one of the casts that "
                    f"exist: as.int, as.decimal, as.text, as.boolean, as.json, as.uc, as.lc, as.title, "
                    f"as.sentence, as.absolute, or a duration such as as.days.", line=_line))
            elif svc == 'cm':
                # THE cm NAMESPACE IS CLOSED: every built compliance action has its own node
                # (CmRetainStmt, CmPurgeBlock, ...), so a cm.* that reaches the generic call is
                # one that is not built -- decidable here, unlike an arbitrary dotted name.
                errors.append(CompileError(f"(T1-CM-CATEGORY-RECORD) cm.{mth} is not built in this release, so it would do nothing. The compliance actions that run are cm.retain, cm.expire, cm.lock, cm.purge and cm.notify; cm.report runs on the commercial runtime. Use one of those, or remove this line until cm.{mth} is built.", line=_line))
            elif svc == 'miocache':
                # The working cache calls are their own statements; a miocache.* that reaches the
                # generic call is always refused at run (a known method written in a form it does
                # not take, as `miocache.set "k" to "v"`, or a misspelt one), so it refuses here.
                errors.append(CompileError(
                    f"miocache.{mth} is not written in a form the cache takes, so it would fail at "
                    f"run. Write it as one of its forms: `miocache.get \"key\" as name`, "
                    f"`miocache.set \"key\" value`, `miocache.delete \"key\"`, `miocache.flush`, "
                    f"`miocache.exists \"key\"`.", line=_line))
            elif svc == 'round':
                from mohio_ast import ROUNDING_FORMS
                if mth in ('to', 'up', 'down'):
                    errors.append(CompileError(
                        f"`round.{mth}` follows the value it rounds; written first on a line it "
                        f"rounds nothing. Write it after the value, as in `price round.{mth} 2`. "
                        f"{ROUNDING_FORMS}", line=_line))
                else:
                    errors.append(CompileError(
                        f"round.{mth} is not a rounding form Mohio has, and on its own line it "
                        f"rounds nothing. Use round.to, round.up or round.down after the value. "
                        f"{ROUNDING_FORMS}", line=_line))
            elif svc == 'mioai':
                from mohio_ast import mioai_refusal
                errors.append(CompileError(mioai_refusal(mth), line=_line))
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


# The fields through which a node holds a VALUE (not a list of statements). A call reached through
# one of these is asked for its value. Kept explicit rather than inferred: a field missing here
# only leaves that case to the run-time refusal, while guessing too wide would refuse a valid
# procedure call written as a statement.
_CALL_VALUE_FIELDS = ('value', 'left', 'right', 'parts', 'expr', 'operand', 'inline_arg')


def scan_call_value_without_returns(program):
    """`call T ... as y`, or a call used as a value, needs task T to declare what it gives back.

    F2 (RULED by Ron, 2026-10-06): this was refused only at run, and the refusal taught the
    retired header form. The information is all in the program, so it refuses here, and teaches
    the `returns <type>` body line that the current grammar reads.
    """
    errors = []
    stmts = getattr(program, 'statements', None) or []
    returns = {}            # task name -> its declared return type, or None for a procedure
    params = {}

    def collect(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                collect(c)
            return
        if not is_dataclass(node):
            return
        if type(node).__name__ == 'TaskDecl' and getattr(node, 'name', ''):
            returns[str(node.name)] = getattr(node, 'return_type', None)
            params[str(node.name)] = [p for p in (getattr(node, 'params', None) or [])]
        for f in fields(node):
            collect(getattr(node, f.name, None))

    def _example(tn):
        ps = params.get(tn) or []
        take = ("    take " + ", ".join(
            str(p.name) + (f" as {p.type_name}" if getattr(p, 'type_name', None) else "")
            for p in ps) + "\n") if ps else ""
        return (f"    task {tn}\n{take}        returns <type>\n        give back <value>\n"
                f"    task: done").replace("\n    take", "\n        take")

    def walk(node, in_value):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                walk(c, in_value)
            return
        if not is_dataclass(node):
            return
        if type(node).__name__ == 'RunBlock':
            tn = str(getattr(node, 'task_name', '') or '')
            alias = str(getattr(node, 'alias', '') or '')
            if tn in returns and not returns[tn] and (alias or in_value):
                asked = (f"`call {tn} ... as {alias}`" if alias
                         else f"`call {tn}` used as a value")
                errors.append(CompileError(
                    f"{asked} asks task `{tn}` for a value, but `{tn}` does not say what it "
                    f"gives back, so there is nothing to hand to the caller.",
                    line=getattr(node, 'line', 0) or 0,
                    hint=(f"Add a `returns` line to the task, naming the type it gives back:\n"
                          f"{_example(tn)}\n"
                          f"Without `returns`, a task is a procedure: call it on its own line, "
                          f"and its `give back` is the response.")))
        for f in fields(node):
            walk(getattr(node, f.name, None), in_value or f.name in _CALL_VALUE_FIELDS)

    for s in stmts:
        collect(s)
    for s in stmts:
        walk(s, False)
    return errors


def scan_connect_default_under_sector(program):
    """`connect ... default "app.db"` in a program that declares a sector refuses at check, the
    same refusal the runtime gives at startup (RULED by Ron, 2026-10-06): a regulated app names its
    database. The sector line may come after the connect, which is why this is asked of the whole
    program and not only at the moment the connect runs."""
    stmts = getattr(program, 'statements', None) or []
    if not any(type(s).__name__ in ('SectorDecl', 'ComplianceDecl') for s in stmts):
        return []
    errors = []
    for s in stmts:
        if type(s).__name__ == 'ConnectDecl' and getattr(s, 'default', None):
            src = getattr(s, 'source', None)
            # The variable the connect names; ConnectDecl always carries one from `from env.X`.
            var = src.key if getattr(src, 'key', None) else 'its address variable'
            errors.append(CompileError(
                f"connect {s.name}: this program declares a sector, so the default "
                f"\"{s.default}\" would never be used; a regulated app names its database.",
                getattr(s, 'line', 0) or 0,
                f"Remove `default \"{s.default}\"` and set {var} to the database this app is "
                f"approved to use.",
                'connect_default_regulated'))
    return errors


_SUBJECTLESS_PREDICATES = {
    ('not', 'empty'): 'not.empty', ('is', 'empty'): 'is.empty', ('not', 'found'): 'not.found',
    ('is', 'in'): 'is.in', ('empty',): 'is.empty',
}
# `when found` is NOT here: in `check exists found in db.users` the capture is named `found`, so
# `when found` reads that variable and is correct. `when empty` is refused only in a find or a
# retrieve: in a check, a check exists and a check unique it is the built test of the block's own
# subject (measured 2026-10-06), and refusing it there would be a false refusal.


def scan_subjectless_when(program):
    """A `when` whose test has no subject refuses at check, naming the subject it meant
    (T1-SPLIT-OPERATOR-FAMILIES sweep, 2026-10-06).

    `when not.empty`, `when is.empty`, `when not.found`, `when empty` and `when found` parsed as a
    VARIABLE (`not`, `is`, `empty`, `found`) and checked clean, then every one failed at run with
    "variable 'not' is not declared", a message about a variable nobody wrote. A condition names its
    subject first, then the test, the rule the parse error already teaches for `when is even`; this
    is that rule for the forms that happen to parse."""
    errors = []

    def subject_of(node):
        kind = type(node).__name__
        if kind == 'CheckBlock':
            v = getattr(node, 'value', None)
            if type(v).__name__ == 'DottedName':
                return '.'.join(str(p) for p in v.parts)
            return None
        for attr in ('alias', 'name'):
            v = getattr(node, attr, None)
            if isinstance(v, str) and v:
                return v
        return None

    def visit(node, subject, in_check=False):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                visit(c, subject, in_check)
            return
        if not is_dataclass(node):
            return
        kind = type(node).__name__
        if kind in ('CheckBlock', 'RetrieveBlock', 'FindBlock'):
            subject = subject_of(node) or subject
        # The nearest block that carries `when` clauses decides whether a bare `when empty` is built:
        # it is in a check, a check exists and a check unique; it fails at run in a find or retrieve.
        if getattr(node, 'when_clauses', None) is not None or getattr(node, 'handlers', None) is not None:
            in_check = kind not in ('RetrieveBlock', 'FindBlock')
            # ONE LINE READ AS TWO CONDITIONS. `when x is.in colors` and `when not x is.in colors`
            # parsed as `when x` (or `when not x`) with NOTHING in its body, followed by a second,
            # subject-less clause `is.in colors` on the same line, so the first answered true and
            # ran nothing, silently (measured 2026-10-06: `when x is.in colors` printed the check's
            # value and neither branch). An empty `when` with another clause on its own line is
            # never what was written.
            clauses = [c for c in (getattr(node, 'when_clauses', None) or
                                   getattr(node, 'handlers', None) or [])
                       if type(c).__name__ == 'CheckWhen']
            for a, b in zip(clauses, clauses[1:]):
                if (not (getattr(a, 'body', None) or []) and getattr(a, 'line', 0)
                        and getattr(a, 'line', 0) == getattr(b, 'line', -1)):
                    tail = str(getattr(b, 'condition', '') or '').replace('_', '.')
                    errors.append(CompileError(
                        f"this line was read as two conditions: a `when` that does nothing, then a "
                        f"separate `{tail}` test, so the line does not test what it says.",
                        a.line,
                        "Write one test per `when`, subject first (`when x is.empty`). `is.in` "
                        "tests a time period in a where clause, not a list: to ask whether a value "
                        "is in a list, find it (`find hit in colors / where value is x / find: done`)"
                        " and check `when hit not.empty`.",
                        'condition_split'))
        if kind == 'CheckWhen' and getattr(node, 'condition', None) == 'when':
            v = getattr(node, 'value', None)
            if type(v).__name__ == 'DottedName':
                key = tuple(str(p) for p in v.parts)
                pred = _SUBJECTLESS_PREDICATES.get(key)
                if key == ('empty',) and in_check:
                    pred = None
                if pred:
                    # The subject the message names: the block's own, or an example word
                    # when the block has none to offer.
                    who = subject if subject else 'member'
                    errors.append(CompileError(
                        f"`when {'.'.join(key)}` puts the test before its subject, so it has "
                        f"nothing to test and would fail at run.",
                        getattr(node, 'line', 0) or getattr(v, 'line', 0) or 0,
                        f"Name the subject first, then the test: `when {who} {pred}`.",
                        'subjectless_condition'))
        for f in fields(node):
            visit(getattr(node, f.name, None), subject, in_check)

    visit(program, None)
    return errors


_TOKEN_UNSAFE_ON_GET = ('SaveBlock', 'UpdateBlock', 'SaveOrUpdateBlock', 'SaveAllBlock',
                        'RemoveBlock', 'ModifyBlock', 'SqlBlock', 'CmPurgeBlock', 'GrantRoleDecl',
                        'MiomailStmt', 'MioconnectCall', 'MiohttpStmt', 'TokenCreateBlock',
                        'TokenRevokeBlock')
_TOKEN_UNSAFE_WORDS = {'SaveBlock': 'save', 'UpdateBlock': 'update', 'SaveOrUpdateBlock': 'save.or.update',
                       'SaveAllBlock': 'save.all', 'RemoveBlock': 'remove', 'ModifyBlock': 'modify',
                       'SqlBlock': 'sql', 'CmPurgeBlock': 'cm.purge', 'GrantRoleDecl': 'grant role',
                       'MiomailStmt': 'miomail', 'MioconnectCall': 'a connector call',
                       'MiohttpStmt': 'miohttp', 'TokenCreateBlock': 'mioauth.token.create',
                       'TokenRevokeBlock': 'mioauth.token.revoke'}


def scan_token_blocks(program):
    """One-time links (Q774, RULED by Ron 2026-10-06) refuse at check what would make them unsafe.

    - `mioauth.token.create` needs `as NAME`, `for`, `scope`, `expires in` and `one time`. A link
      with no expiry or that can be used twice is the bug this feature exists to close, so neither
      is a default: the program says both.
    - `mioauth.token.revoke` takes `for` and `scope` only.
    - `verify token from X` needs a `scope`, with at most one `*`, at the end: a link checked with
      no scope accepts a link minted for anything.
    - inside a GET handler (`request for`), a verify only checks the link, and its `on.success`
      may not write, grant a role or send anything: link scanners open links on their own, so the
      action belongs behind a POST the person confirms.
    - `named NAME` as a capture is retired: `as` is the capture word for the whole family.
    """
    errors = []

    def err(msg, line, hint):
        errors.append(CompileError(msg, line or 0, hint, 'token_form'))

    def contains_unsafe(node):
        found = []

        def walk(n):
            if n is None or isinstance(n, (str, int, float, bool)):
                return
            if isinstance(n, (list, tuple)):
                for c in n:
                    walk(c)
                return
            if not is_dataclass(n):
                return
            if type(n).__name__ in _TOKEN_UNSAFE_ON_GET:
                found.append(n)
            for f in fields(n):
                walk(getattr(n, f.name, None))
        walk(node)
        return found

    def visit(node, in_get):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                visit(c, in_get)
            return
        if not is_dataclass(node):
            return
        kind = type(node).__name__
        line = getattr(node, 'line', 0) or 0
        if kind == 'TokenCreateBlock':
            # The name the messages below use for the block: its capture, its retired `named`, or
            # the example name when it has neither (that case is refused just below).
            name = node.as_name if node.as_name else (node.named if node.named else 'approve_token')
            if getattr(node, 'named', None) is not None:
                err(f"`named {node.named}` is retired as a capture word. `as` names what a block "
                    f"gives you.", line,
                    f"Write `mioauth.token.create as {node.named}` and remove the `named` line.")
            elif not getattr(node, 'as_name', None):
                err("mioauth.token.create gives you the link's token, and nothing names it, so it "
                    "could never be sent.", line,
                    "Name it on the first line: `mioauth.token.create as approve_token`.")
            for clause, present, example in (
                    ('for', node.subject is not None, 'for member.id'),
                    ('scope', node.scope is not None, 'scope "approve:payment-1"'),
                    ('expires in', node.expires is not None, 'expires in 1 hour')):
                if not present:
                    err(f"mioauth.token.create `{name}` has no `{clause}`. A link says whom it acts "
                        f"for, what it is for and when it expires, so it cannot quietly outlive its "
                        f"purpose.", line, f"Add the line `{example}` inside the block.")
            # ONE TIME IS THE DEFAULT (ruled 2026-10-06); a link used more than once says so.
            if node.one_time and node.uses is not None:
                err(f"mioauth.token.create `{name}` says both `one time` and `up to {node.uses} "
                    f"times`, so it cannot say how often the link works.", line,
                    "Keep one: leave both out for a link that works once, or write `up to 3 times`.")
            elif node.uses is not None and node.uses < 1:
                err(f"mioauth.token.create `{name}`: `up to N times` needs a whole number, 1 or "
                    f"more.", line, "Write `up to 3 times`, or leave it out for a link that works once.")
            if getattr(node, 'token', None) is not None:
                err("`token` belongs to mioauth.token.revoke, which revokes a presented token; "
                    "creating one issues a new token.", line, "Remove the `token` line.")
        elif kind == 'TokenRevokeBlock':
            # Two ways to say which links (ruled 2026-10-06): `for` and `scope` together, every
            # live matching token; or `token X`, the one token presented.
            _by_token = getattr(node, 'token', None) is not None
            if (getattr(node, 'named', None) is not None or node.expires is not None
                    or node.one_time or node.uses is not None):
                err("mioauth.token.revoke takes `for` and `scope`, or `token`, and nothing else.",
                    line, 'Write `for member.id` and `scope "approve:*"`, or `token link.token`, '
                          'and remove the other lines.')
            if _by_token and (node.subject is not None or node.scope is not None):
                err("mioauth.token.revoke names a `token` and also `for`/`scope`; it revokes by one "
                    "or the other.", line,
                    "Keep `token link.token` to revoke that one link, or `for` and `scope` for "
                    "every matching link.")
            elif not _by_token:
                for clause, present, example in (('for', node.subject is not None, 'for member.id'),
                                                 ('scope', node.scope is not None, 'scope "approve:*"')):
                    if not present:
                        err(f"mioauth.token.revoke has no `{clause}`, so it cannot say which links "
                            f"to revoke.", line,
                            f"Add the line `{example}`, or revoke one link with `token link.token`.")
        elif kind == 'VerifyTokenStmt' and getattr(node, 'header', None) is not None:
            # The BEARER form reads a token from a request header and would need signature checks
            # that are not built; the one-time link form, `verify token from X`, is.
            err("verify token from a request header (a bearer token) is declared in the grammar "
                "but not built in this release (T1-VERIFY-BEARER-TOKEN). It refuses at run rather "
                "than pass authentication it cannot check.", line,
                "Gate the handler with `require role \"<role>\"` today, or verify a one-time link: "
                "`verify token from request.t as grant`.")
        elif kind == 'VerifyTokenStmt':
            sc = getattr(node, 'scope', None)
            if not sc:
                err("verify token has no `scope`, so it would accept a link minted for anything.",
                    line, 'Add the line `scope "approve:payment-*"` naming what this link is for.')
            elif '*' in sc[:-1]:
                err(f'verify token scope "{sc}" has a `*` before its end. A scope matches '
                    f'exactly, or by its start with one `*` at the end.', line,
                    'Write it as `scope "approve:payment-*"`.')
            if in_get:
                for h in getattr(node, 'handlers', None) or []:
                    if type(h).__name__ != 'OnSuccess':
                        continue
                    for bad in contains_unsafe(getattr(h, 'body', None)):
                        _word = _TOKEN_UNSAFE_WORDS.get(type(bad).__name__, type(bad).__name__)
                        _at = getattr(bad, 'line', 0) or getattr(node, 'line', 0)
                        err(f"this verify is in a GET handler, and its on.success does "
                            f"`{_word}` (line {_at}). "
                            f"Link scanners and mail previews open links on their own, so an "
                            f"action a link takes must wait for the person to confirm.",
                            line,
                            "In the GET, show what the link will do with a form that posts the "
                            "token back; verify it again in a `listen for sh.X` handler and act there, "
                            "where it is redeemed once.")
                        break
        elif kind == 'SignBlock' and getattr(node, 'named_word', False):
            err(f"`named {node.named}` is retired as a capture word. `as` names what a block gives "
                f"you.", line,
                f"Write `sign {node.sign_type} for <target> as {node.named}` and remove the `named` "
                f"line.")
        child_get = in_get or kind == 'RequestInboundBlock'
        if kind == 'NewBlock':
            child_get = False
        for f in fields(node):
            visit(getattr(node, f.name, None), child_get)

    visit(program, False)
    return errors


def scan_raw_sql_on_document_store(program):
    """Raw `sql` in a program whose every connection is a document store refuses at check.

    Day 2 item d (RULED by Ron, 2026-10-06: move run-time refusals to check where the information
    exists). A `sql` block runs on the program's connection; when every connection the program
    declares is MongoDB there is no SQL engine to run it, which the runtime refuses. That is known
    from the source. With an adaptive connect (no `as`) or any SQL engine declared, it is still a
    run-time question, and is left there."""
    conns, sqls = [], []

    def walk(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                walk(c)
            return
        if not is_dataclass(node):
            return
        k = type(node).__name__
        if k == 'ConnectDecl':
            conns.append(str(getattr(node, 'driver', '') or '').strip().lower())
        elif k == 'SqlBlock':
            sqls.append(node)
        for f in fields(node):
            walk(getattr(node, f.name, None))

    walk(getattr(program, 'statements', None) or [])
    if not sqls or not conns or not all(c in ('mongodb', 'mongo') for c in conns):
        return []
    return [CompileError(
        "This `sql` block has no SQL database to run on: every connection in this program is "
        "MongoDB, a document store, so the raw SQL would be refused when it runs.",
        line=getattr(n, 'line', 0) or 0,
        hint=("Use find, retrieve, save and update, which run on MongoDB, or connect a SQL "
              "database for this query: `connect reports as postgres from env.REPORTS_URL`."))
        for n in sqls]


def scan_native_join_on_declared_list(program):
    """A native join whose source is a list the program itself declares refuses at check.

    Day 2 item d. A join is pushed to a database, so a find over a held list cannot run one, and
    the runtime refuses it. Whether a bare name is a list is a run-time binding in general, but a
    name this program declares as a list (`items as list ...`, `create list items`, a held list)
    is known here; that case refuses at check. Any other bare name is left to the runtime."""
    lists = set()

    def collect(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                collect(c)
            return
        if not is_dataclass(node):
            return
        k = type(node).__name__
        if ((k == 'Assignment' and str(getattr(node, 'type_name', '') or '') == 'list')
                or (k == 'HoldDecl' and getattr(node, 'is_list', False))):
            if getattr(node, 'name', ''):
                lists.add(str(node.name))
        for f in fields(node):
            collect(getattr(node, f.name, None))

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
        if type(node).__name__ in ('FindBlock', 'RetrieveBlock'):
            src = getattr(node, 'source', None)
            parts = getattr(src, 'parts', None) or []
            if (len(parts) == 1 and str(parts[0]) in lists
                    and any(type(b).__name__ == 'JoinBlock' for b in (getattr(node, 'body', None) or []))):
                errors.append(CompileError(
                    f"a `native join` needs a database source; this find reads the list "
                    f"`{parts[0]}`, which this program declares as a list.",
                    line=getattr(node, 'line', 0) or 0,
                    hint="Query a connected table (`find x in db.table`), or drop the join."))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    stmts = getattr(program, 'statements', None) or []
    collect(stmts)
    if lists:
        walk(stmts)
    return errors


def scan_take_after_returns(program):
    """A `take` line after `returns` reads as a value named `take`, not a parameter: refuse it.

    F1 (RULED by Ron, 2026-10-06). The task form is `task NAME / take ... / returns <type> /
    body / task: done`: parameters first, then what the task gives back. Written the other way
    round (`returns` on the task line, or before the take lines), the take lines fall into the
    body as an assignment to a variable called `take`, the task has no parameters, and the call
    is refused at run for a parameter it does not have. Said here, at the take line."""
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
        if type(node).__name__ == 'TaskDecl':
            for st in (getattr(node, 'body', None) or []):
                # A take only falls into the body AFTER a returns (before one, it is a parameter),
                # so a task that reaches here always has its return type; one without is skipped.
                if (type(st).__name__ == 'Assignment' and str(getattr(st, 'name', '')) == 'take'
                        and getattr(node, 'return_type', None)):
                    tn = str(node.name)
                    rt = str(node.return_type)
                    errors.append(CompileError(
                        f"This `take` comes after `returns {rt}` in task `{tn}`, so it reads as a "
                        f"value named take, not a parameter, and the task takes nothing.",
                        line=getattr(st, 'line', 0) or 0,
                        hint=(f"Write the take lines first, then returns:\n"
                              f"    task {tn}\n        take <param> as <type>\n"
                              f"        returns {rt}\n        give back <value>\n    task: done")))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    walk(getattr(program, 'statements', None) or [])
    return errors


def scan_held_value_restated(program):
    """Re-holding a held value, or restating a held or locked one, refuses at CHECK.

    F3 (RULED by Ron, 2026-10-06): these were refused only at run, though the program shows them.
    The same rule as the runtime, read conservatively so a valid program is never refused:
      - a name that is released, cleared, forgotten or renamed ANYWHERE is left to the runtime,
        because whether the release ran first depends on the path taken (a lock is permanent, so
        a locked name has no such exemption);
      - straight-line code is read in order, and a hold made inside a branch stays in that branch;
      - a handler runs after every top-level declaration has loaded, so a handler restating a
        top-level held or locked value is refused wherever it is written;
      - a task body is not read (a parameter of the same name is the task's own), and a loop
        variable of the same name shadows the held value inside its loop.
    """
    from mohio_ast import TOP_HANDLER_TYPES
    errors = []
    stmts = getattr(program, 'statements', None) or []
    freed = set()

    def collect(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                collect(c)
            return
        if not is_dataclass(node):
            return
        k = type(node).__name__
        if k == 'ReleaseStmt' or (k == 'VarStateStmt' and getattr(node, 'op', '') != 'replace'):
            if getattr(node, 'name', ''):
                freed.add(str(node.name))
        for f in fields(node):
            collect(getattr(node, f.name, None))

    def _refuse(node, kind, name, held_line, verb):
        line = getattr(node, 'line', 0) or 0
        if kind == 'lock':
            errors.append(CompileError(
                f"`{name}` is locked (line {held_line}) and cannot be changed: a lock is permanent.",
                line=line,
                hint=(f"Use a different name for the new value, or, if `{name}` is meant to "
                      f"change, declare it as a plain value (`{name} <value>`) instead of "
                      f"`lock`.")))
        elif verb == 'hold':
            errors.append(CompileError(
                f"`{name}` is already held (line {held_line}) and cannot be held again.",
                line=line,
                hint=(f"To give it a new value, write `release {name}` first and then "
                      f"`{name} <new value>`; or write `replace {name} with <new value>` to "
                      f"change it while keeping the hold.")))
        else:
            errors.append(CompileError(
                f"`{name}` is held (line {held_line}) and cannot be changed until it is released.",
                line=line,
                hint=(f"Write `release {name}` first and then `{name} <new value>`; or write "
                      f"`replace {name} with <new value>` to change it while keeping the hold.")))

    def _restates(node, held):
        k = type(node).__name__
        name = str(getattr(node, 'name', '') or '')
        if k in ('Assignment', 'HoldDecl', 'LockDecl') and name in held:
            kind, held_line = held[name]
            if kind == 'lock' or name not in freed:
                _refuse(node, kind, name, held_line, 'hold' if k == 'HoldDecl' else 'assign')
                return True
        return False

    def block(items, held, skip_handlers):
        for node in (items or []):
            if not is_dataclass(node):
                continue
            k = type(node).__name__
            if k == 'TaskDecl' or (skip_handlers and isinstance(node, TOP_HANDLER_TYPES)):
                continue
            if not _restates(node, held):
                name = str(getattr(node, 'name', '') or '')
                if k == 'HoldDecl' and name:
                    held[name] = ('hold', getattr(node, 'line', 0) or 0)
                elif k == 'LockDecl' and name:
                    held[name] = ('lock', getattr(node, 'line', 0) or 0)
            inner = dict(held)
            for _shadow in ('item', 'var', 'var_name', 'item_name'):
                inner.pop(str(getattr(node, _shadow, '') or ''), None)
            for f in fields(node):
                v = getattr(node, f.name, None)
                if isinstance(v, list) and any(is_dataclass(x) for x in v):
                    block(v, dict(inner), skip_handlers)

    for s in stmts:
        collect(s)
    top = {}
    block(stmts, top, skip_handlers=True)
    for s in stmts:
        if isinstance(s, TOP_HANDLER_TYPES):
            block([s], dict(top), skip_handlers=False)
    return errors


# The words that OPEN a block. None of them is a variable name.
#
# `new` was not among the reserved words, so this:
#
#     new Signup at "/signup"          <-- wrong: needs `sh.Signup`; `new sh.Signup at "/signup"` stands on its own
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
    'change', 'create', 'try', 'loop', 'repeat', 'each', 'while', 'sql',
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
    # `page` is FREED as an ordinary variable and field name (Ron, 2026-09-25), the same way `style`
    # was: `page.header` / `page.footer` are the dotted namespace now, so the bare word is the
    # developer's to use. It is NOT in this set any more -- `page 5` and `page as int` are ordinary
    # declarations, and `page "bump"` was always a legal field. The RETIRED page BLOCK is a separate
    # thing and is untouched: the routing form `page /home` still fails to parse and gets its
    # migration message from mio.py's _RETIRED_FORMS (`^page\s+/`), which the bare word never matches.
    # `check` IS HERE NOW (T1-MISPARSE-AS-ASSIGNMENT, 2026-10-17). It was left out because
    # `check confidence above 0.85`, retired inside ai.decide, was thought to parse as a variable
    # named `check`; it does not (the transform names that retired form by its shape), so only
    # `check 5` reaches here, and written that way the check never ran.
    #
    # THE REST OF THE BLOCK VERBS (T1-MISPARSE-AS-ASSIGNMENT, RULED by Ron 2026-10-17), from the live
    # grammar: the leading word of every `block_stmt` alternative where a bare `VERB value` parses as
    # an assignment (run through `mio check`). `find`, `retrieve`, `update`, `grab`, `call` and `run`
    # are refused earlier, by the transformer, with their own messages; they are listed here too so
    # this set is the whole class and a transformer change cannot quietly drop one.
    'check', 'find', 'retrieve', 'update', 'grab', 'call', 'run', 'apply', 'compare', 'connection',
    'copy', 'hash', 'ignore', 'invoke', 'it', 'limits', 'modify', 'recall', 'remember',
    'section', 'sign', 'upsert', 'from',
}

# THE ONE-LINE ACTION VERBS (T1-MISPARSE-AS-ASSIGNMENT, RULED by Ron 2026-10-17): the leading word of
# every `action_stmt` alternative, and `connect`, where a bare `VERB value` parses as an assignment.
# Same refusal as a block opener, on the same path: the verb's own line is shown instead of a block.
# Leaders already refused another way are not here: the NAME token reserves the mio* service roots,
# `render` and the type words; `show`, `raise`, `give` and `absolute` take a bare value as their own
# statement; `as` and `on` lead only a dotted form (as.title, on.idle) and are connectives, not verbs.
# A retired word (`make`, `delete`) is an ordinary name except in its retired shape: RETIRED_OPENERS.
# `percentage` and `purpose` are refused by scan_orphan_vocabulary with their own messages, so they are
# not here (one refusal per line). `notify` is EXCLUDED (RULED 2026-10-17): a server-side `notify` is declared,
# not built, so there is no verb for a top-level `notify "hi"` to shadow and it stays a plain name (as
# test_check_time_silence asserts); the client `notify` statement inside a listener is its own statement.
ONE_LINE_VERBS = {
    'add', 'append', 'prepend', 'average', 'maximum', 'minimum', 'sum', 'broadcast',
    'clear', 'forget', 'rename', 'replace', 'release', 'convert', 'decode', 'encode', 'end', 'extract',
    'grant', 'halt', 'stop', 'skip', 'inject', 'jump', 'redirect', 'load', 'parse',
    'require', 'rerun', 'send', 'stream', 'validate', 'verify', 'wait', 'connect',
}

_OPENER_HINT = {
    'new':    'listen for sh.<Shape> at /path ... listen: done (`new` is not a handler)',
    'shape':  'shape <Name> ... shape: done',
    'listen': 'listen for ... listen: done',
    'save':   'save to db.<table> ... save: done',
    'find':   'find <name> in db.<table> ... find: done',
    'check':  'check <value> ... when <condition> ... otherwise ... check: done',
    'try':    'try ... try: done',
    'sql':    'sql ... sql: done',
    'flow':   'flow <map>[.<chain>]           (drives a declared map)',
    'walk':   'walk <map>[.<chain>] ... walk: done',
    'journey':'journey <Name> ... journey: done',
    'page':   'the page block is retired -- a file serves itself, and several addresses are named by writing each handler on its own: `request for sh.X at /path`',
    # T1-MISPARSE-AS-ASSIGNMENT (2026-10-17): every verb the scan refuses names its real form. Each is
    # the language reference's verified syntax, or the grammar rule itself where it has none.
    'remove':     'remove from db.<table> ... match <field> to <value> ... remove: done',
    'get':        'get <name> from <source> ... get: done',
    'pull':       'pull up to <N> from db.<table> ... pull: done',
    'change':     'change to sh.<Shape> ... change: done (a stored-record change handler, declared, not built in this release); to change a value, restate it: <name> <value>',
    'create':     'create list <name> ... <item> ... create: done',
    'loop':       'loop ... stop ... loop: done',
    'repeat':     'repeat <N> times ... repeat: done',
    'each':       'repeat each <item> in <collection> ... repeat: done',
    'while':      'while <condition> ... while: done',
    'request':    'request for sh.<Shape> at /path ... request: done',
    'transaction':'transaction ... <writes> ... transaction: done',
    'retrieve':   'retrieve <name> from db.<table> ... match <field> to <value> ... retrieve: done',
    'update':     'update db.<table> ... match id to <value> ... <field> <value> ... update: done',
    'grab':       'grab <name> from db.<table> ... match <field> to <value> ... grab: done',
    'call':       'call <task> with <value>, or call <task> as <result> ... <input> <value> ... call: done',
    'run':        'run mioschedule.<name> now',
    'apply':      'apply runs inside a modify block: modify every <item> in <collection> ... apply ... modify: done',
    'compare':    'compare <a> to <b> ... compare: done',
    'connection': 'connection at /path ... on.open ... connection: done',
    'copy':       'copy <source> to <destination> ... copy: done',
    'hash':       'hash ... <field> ... hash: done',
    'ignore':     'ignore <target> ... ignore: done',
    'invoke':     'invoke <name> ... invoke: done',
    'it':         'it "what this proves" ... expect ... it: done (inside mio test)',
    'limits':     'limits ... max steps <N> ... limits: done',
    'modify':     'modify every <item> in <collection> ... apply ... modify: done',
    'recall':     'recall from <name> ... recall: done',
    'remember':   'remember in <name> ... remember: done',
    'section':    'section <name> ... section: done',
    'sign':       'sign url for <value> as <name> ... sign: done',
    'upsert':     'upsert db.<table> match <key> to <value>',
    'from':       'from <connector> ... when <event> ... from: done (a webhook handler, declared, not built in this release: T1-FROM-CONNECTOR-EXECUTOR)',
    'add':        'add <value> to <list>',
    'append':     'append <value> to <name>',
    'prepend':    'prepend <value> to <name>',
    'average':    'average of <value> as <name>',
    'maximum':    'maximum of <value> as <name>',
    'minimum':    'minimum of <value> as <name>',
    'sum':        'sum of <value> as <name>',
    'broadcast':  'broadcast to room <value> (declared, not built in this release)',
    'clear':      'clear <name> (empties a held value)',
    'forget':     'forget <name> (removes a held value)',
    'rename':     'rename <name> to <new name> (renames a held value)',
    'replace':    'replace "<old>" with "<new>" in <name>',
    'release':    'release <name> (unfreezes a held value)',
    'convert':    'convert <name> using <map>',
    'decode':     'decode <value> from <format>',
    'encode':     'encode <value> as <format>',
    'end':        'end session',
    'extract':    'extract from <source> using <rule> as <name>',
    'grant':      'grant role "<name>"',
    'halt':       'halt (on its own, with no value)',
    'stop':       'stop (on its own, or stop when <condition>)',
    'skip':       'skip (on its own, or skip when <condition>)',
    'inject':     'inject <name> into <name>',
    'jump':       'jump to <path>',
    'redirect':   'jump to <path> (redirect is not built; jump to sends the visitor elsewhere)',
    'load':       'load pack <name>',
    'parse':      'parse <value> as <type> as <name>',
    'require':    'require role "<name>"',
    'rerun':      'rerun <name> with <value> (declared, not built in this release)',
    'send':       'send #<form> to "/path"',
    'stream':     'stream <value> to <name>',
    'validate':   'validate <name> against <rule>',
    'verify':     'verify signature using env.<SECRET> (a clause inside a handler)',
    'wait':       'wait for <name> (declared, not built in this release)',
    'connect':    'connect db as postgres from env.DATABASE_URL (the engine is sqlite, postgres, mysql, mongodb or oracle; the source is always env.X or secret.X, never a literal string)',
}

# Retired block words. Each is an ordinary name (a field, a column, a variable, a word after a dot)
# and refuses only where it reads as its retired statement; see scan_block_opener_as_variable.
RETIRED_OPENERS = {
    # word: (what replaced it, the replacing form with {t} for the name that was written)
    'make':     ('`create` builds an object from pieces',
                 'create {t}\n            name "value"\n        create: done'),
    'consider': ('`check` decides',
                 'check {t}\n            when {t} is more than 0\n                show "more"\n'
                 '        check: done'),
    'delete':   ('`remove` removes',
                 'remove {t}'),
    'route':    ('a handler names its own address',
                 'request for sh.<Shape> at /path   (listen for sh.<Shape> at /path for a write)'),
}
_RETIRED_EXAMPLE_VALUE = {'make': '"Volvo"', 'consider': '5', 'delete': '"draft"', 'route': '"north"'}


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
            # `route "/a" to "/b"` (RULED by Ron 2026-10-09): the retired route form split into two
            # variables on one line, `route` holding "/a" and `to` holding "/b", and ran with
            # nothing routed.
            for here, after in zip(node, node[1:]):
                if (type(here).__name__ == 'Assignment' and type(after).__name__ == 'Assignment'
                        and getattr(here, 'name', '') == 'route' and getattr(after, 'name', '') == 'to'
                        and getattr(here, 'line', 0) == getattr(after, 'line', 0)):
                    errors.append(CompileError(
                        "`route ... to ...` is the retired route form, and here it became two "
                        "variables, `route` and `to`, so nothing is routed.\n"
                        "    A handler names its own address, on its own line:\n"
                        "        request for sh.<Shape> at /a\n"
                        "            jump to \"/b\"\n"
                        "        request: done\n"
                        "    (`listen for sh.<Shape> at /a` for a write.)",
                        line=getattr(here, 'line', 0) or 0))
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
            elif name in RETIRED_OPENERS:
                # A RETIRED WORD IS AN ORDINARY NAME except where it reads as its retired
                # statement (RULED by Ron 2026-10-09, from the Dispatch app's `make` field). The
                # statement is `<word> <name>` (`make Report`, `delete it`), so only a value that is
                # one bare name refuses here; `make "Volvo"`, `consider 5` and `make car.make` are a
                # variable with that name. Before this, `delete it` declared a variable called
                # delete and removed nothing, with no error.
                value = getattr(node, 'value', None)
                if type(value).__name__ == 'DottedName' and len(getattr(value, 'parts', None) or []) == 1:
                    line = getattr(node, 'line', 0) or 0
                    target = value.parts[0]
                    what, form = RETIRED_OPENERS[name]
                    errors.append(CompileError(
                        f"`{name} {target}` reads as the retired `{name}` statement, so it is refused.\n"
                        f"    `{name}` is retired -- {what}:\n"
                        f"        {form.format(t=target)}\n"
                        f"    A variable called `{name}` is fine when it holds a value, for example:  "
                        f"{name} {_RETIRED_EXAMPLE_VALUE[name]}",
                        line=line))
            elif name in BLOCK_OPENERS:
                line = getattr(node, 'line', 0) or 0
                hint = _OPENER_HINT.get(name)
                msg = (f"Line {line}: `{name}` opens a block. It is not a variable name.\n"
                       f"    This line declared a VARIABLE called `{name}`, which means the "
                       f"`{name}` block you wrote does not exist in the program at all -- "
                       f"and nothing would have told you.")
                if hint:
                    msg += f"\n    The form is:  {hint}"
                errors.append(CompileError(msg, line=line))
            elif name in ONE_LINE_VERBS:
                line = getattr(node, 'line', 0) or 0
                msg = (f"Line {line}: `{name}` is an action verb. It is not a variable name.\n"
                       f"    This line declared a VARIABLE called `{name}`, so the `{name}` you "
                       f"wrote never happens -- and nothing would have told you.\n"
                       f"    The form is:  {_OPENER_HINT[name]}\n"
                       f"    To keep a value, give it a name that says what it holds.")
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


def _declares_audit_store(program):
    """True when the program connects `db`, the connection the audit trail is written to (the
    runtime reads `get_connection('db')` for its sink). A store bound by a deployment's audit-sink
    provider is not visible here; open core writes the trail to the program's own `db`."""
    stack = [program]
    while stack:
        n = stack.pop()
        if n is None or isinstance(n, (str, int, float, bool)):
            continue
        if isinstance(n, (list, tuple)):
            stack.extend(n)
            continue
        if not is_dataclass(n):
            continue
        if type(n).__name__ == 'ConnectDecl' and str(getattr(n, 'name', '') or '') == 'db':
            return True
        for f in fields(n):
            stack.append(getattr(n, f.name, None))
    return False


def _sector_frameworks(name):
    """(frameworks, profile_found, licensed) for a sector name.

    A sector is not a framework. The sector's PROFILE declares which frameworks are active, and
    those are what carry an audit grade -- so resolve the profile first rather than looking up the
    sector name in the framework table and reporting every sector as "unknown".
    T1-SILENT-SWEEP-BATCH6-10 (2026-08-15): this once called `load_sector_profile(path)`, which
    expects a SECTOR NAME, so frameworks stayed permanently [] and no grade rule ever fired."""
    frameworks, profile_found, paid = [], False, False
    try:
        from mohio_sector_loader import (find_sector_profile, load_sector_profile,
                                         sector_requires_license)
        paid = sector_requires_license(name)
        if find_sector_profile(name):
            profile_found = True
            frameworks = list(getattr(load_sector_profile(name), 'compliance', []) or [])
    except Exception:  # SILENT-OK: an unreadable profile reads as not found, which the sector check refuses
        profile_found = False
    return frameworks, profile_found, paid


_AUDIT_STORE_FIX = ("    Connect the database the trail is written to, once at the top of the file:\n"
                    "        connect db from env.DATABASE_URL")


def scan_audit_store_reachable(program):
    """An audit record with nowhere to go is refused at check (RULED by Ron 2026-10-09).

    Measured before: `ai.audit to d_audit_log` in an ai.decide with no `connect` checked clean, ran,
    answered 200 and wrote nothing anywhere. And a sector whose frameworks require an audit grade,
    with no store, only warned at check while every audited request then failed with
    `audit.no_durable_store`. Both are refused here, at check and therefore at serve.

    Covers every place an `ai.audit to` line records (ai.decide, ai.rank, ai.create and the bare
    statement), and every `sector:` or `compliance:` line whose frameworks require a grade. An
    `ai.audit` on an ai.agent is refused separately as not built, so it is not named twice."""
    if _declares_audit_store(program):
        return []
    if not getattr(program, 'served_as_checked', True):
        # A journey or an underscore partial checked on its own: the page that serves it supplies
        # the store, and that page is checked with this code prepended (T1-CHECK-ALL-SPINE-NO-STORE).
        # Measured: `mio check --all` refused tests/zork/journey.mho and _cheats.mho while the
        # assembled tests/zork/index.mho, which connects, checked clean.
        return []
    from mohio_audit_grades import required_grade
    errors = []
    _WHAT = {'AiDecideBlock': 'this decision', 'AiDecide': 'this decision',
             'AiRankBlock': 'this ranking', 'AiCreateStmt': 'what this creates'}

    def walk(node, parent):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                walk(c, parent)
            return
        if not is_dataclass(node):
            return
        k = type(node).__name__
        line = getattr(node, 'line', 0) or 0
        if k == 'AiAuditStmt' and parent != 'AiAgentBlock':
            dest = getattr(node, 'log_name', '') or ''
            what = _WHAT.get(parent, 'that this point was reached')
            errors.append(CompileError(
                f"Line {line}: `ai.audit to {dest}` records {what}, and nothing in this program "
                f"connects a store to hold the record, so it would run and the record would be "
                f"written nowhere.\n" + _AUDIT_STORE_FIX, line=line))
        elif k in ('SectorDecl', 'ComplianceDecl'):
            if k == 'SectorDecl':
                label = str(getattr(node, 'name', '') or getattr(node, 'sector', '') or '')
                frameworks = _sector_frameworks(label)[0] if label else []
                # levels are held joined internally; show them the way they are written
                said = f"sector `{', '.join(label.split('.'))}` ({', '.join(frameworks)})"
            else:
                label = str(getattr(node, 'framework', '') or '')
                frameworks = [label] if label else []
                said = f"`compliance: {label}`"
            grade = required_grade(frameworks)[0] if frameworks else 'none'
            if grade != 'none':
                errors.append(CompileError(
                    f"Line {line}: {said} requires {grade}-grade audit storage, and nothing in this "
                    f"program connects a store to hold its records, so every audited action (a "
                    f"sign-in, a role check, a governed decision) would be refused at run with "
                    f"audit.no_durable_store.\n" + _AUDIT_STORE_FIX, line=line))
        for f in fields(node):
            walk(getattr(node, f.name, None), k if k.startswith('Ai') else parent)

    walk(getattr(program, 'statements', program), None)
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
        # A field typed as a shape, or a list of one (`home as sh.Address`, `items as list sh.Order`).
        # These were never checked, so `items as list sh.order` checked clean with only `Order`
        # declared (RULED by Ron 2026-10-10: a shape is referenced by its name exactly as declared).
        if cls == 'ShapeField':
            for attr in ('type_name', 'element_type'):
                val = getattr(node, attr, None)
                if isinstance(val, str) and val.startswith('sh.'):
                    refs.append((val, getattr(node, 'line', 0)))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    walk(program)

    errors = []
    for name, line in refs:
        bare = name.split('.')[-1]
        if bare and bare not in declared:
            hint = (f"Declare it with `shape {bare} ... shape: done`.")
            same = [d for d in declared if d.replace('_', '').lower() == bare.replace('_', '').lower()]
            if same:
                hint = (f"The shape is declared as `{same[0]}`, and a shape is referenced by its name "
                        f"exactly as declared: write `sh.{same[0]}`.")
            elif declared:
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
    from mohio_runtime_vocab import DANGEROUS_UPLOAD_EXT as banned
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
                                    line=getattr(node, 'line', 0) or 0,
                                    code="miofile_dangerous_accept"))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    walk(getattr(program, 'statements', None) or [])
    return errors


def _blocked_upload_exts():
    """The one blocklist, the runtime's own, so it can never drift into two."""
    from mohio_runtime_vocab import DANGEROUS_UPLOAD_EXT
    return DANGEROUS_UPLOAD_EXT


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
                    "To proceed: `give <value> as download` to hand a file over to save, "
                    "`give <value> as image` to display it inline, or `give back <value>` "
                    "to answer the request.",
                    line=line))
            elif mod not in ('download', 'image'):
                errors.append(CompileError(
                    f"`give ... as {mod}` is not a destination `give` knows. It hands a "
                    f"value to the requester as a file: `as download` to save, `as image` to "
                    f"display inline. To proceed: use one of those, or the service that owns "
                    f"the job you mean -- miomail to send mail, miohttp to call a server, "
                    f"`give back ... as json` to answer with data.",
                    line=line))
            elif mod == 'download' and getattr(node, 'filename', None) is None and \
                    not isinstance(getattr(node, 'value', None), Literal):
                # Only `as download` needs a name at check: it takes the saved filename from a path
                # written in place, so a runtime value with no name has nothing to save under. `as
                # image` does NOT need one -- the executor sniffs the image type from the bytes, and
                # refuses loud at run only when a nameless value is not a recognizable image.
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


def _program_names(program):
    """Every name the program uses anywhere: declared, assigned, read, a parameter, a shape field
    (a request's bare field), a query or loop result. For telling a typo from a real name."""
    from mohio_ast import DottedName, ReleaseStmt
    names = set()
    stack = [getattr(program, 'statements', None) or []]
    while stack:
        n = stack.pop()
        if n is None or isinstance(n, (int, float, bool)):
            continue
        if isinstance(n, str):
            continue
        if isinstance(n, (list, tuple)):
            stack.extend(n)
            continue
        if not is_dataclass(n):
            continue
        if isinstance(n, DottedName) and n.parts:
            names.add(str(n.parts[0]))
        for f in fields(n):
            v = getattr(n, f.name, None)
            if isinstance(v, str) and re.fullmatch(r'[A-Za-z_]\w*', v) and not isinstance(n, ReleaseStmt):
                names.add(v)
            elif v is not None and not isinstance(v, str):
                stack.append(v)
    return names


def scan_release_never_held(program):
    """`release` of a name the program uses but never holds does nothing: a no-op at run, so check
    says so (RULED by Ron 2026-10-10). A name that exists nowhere refuses instead, as a typo
    (scan_state_op_requires_held)."""
    from mohio_ast import HoldDecl, ReleaseStmt, Assignment
    held, out, rels = set(), [], []
    for n in _all_nodes(getattr(program, 'statements', None) or []):
        if isinstance(n, HoldDecl) and getattr(n, 'name', ''):
            held.add(n.name)
        elif isinstance(n, Assignment) and getattr(n, 'type_name', None) and getattr(n, 'name', ''):
            held.add(n.name)          # a type contract is something release removes
        elif isinstance(n, ReleaseStmt) and n.variant == 'release' and n.name:
            rels.append(n)
    known = _program_names(program)
    for n in rels:
        if n.name not in held and n.name in known:
            ln = getattr(n, 'line', 0) or 0
            out.append(CompileWarning(
                f"Line {ln}: release does nothing here because {n.name} is never held; hold it "
                f"first if you meant to protect it.",
                ln,
                f"Write `hold {n.name} <value>` where the value is set, or remove this release.",
                code="release_never_held",
            ))
    return out


def scan_state_op_requires_held(program):
    """clear/forget/rename/replace/release on a FLUID variable check clean today and refuse
    only at RUN (mohio_interpreter.py's `_require_held` / `_exec_ReplaceBlock`'s in-place guard
    / `_exec_ReleaseStmt`) -- a clean check followed by a runtime refusal is the silent-until-run
    shape this project treats as highest severity (Ron's ruling, 2026-10-04: these five
    operators were designed from the start to act only on a held variable).

    CONSERVATIVE BY DESIGN, GLOBAL rather than scope-precise: a name is treated as "ever
    held" if ANY `hold NAME ...` appears ANYWHERE in the program, not only in the same
    handler/scope as the operator using it. This can MISS a genuine same-named-but-different-
    variable case (a `hold x` in one handler does not actually protect a different fluid `x`
    in another), but it can never FALSE-POSITIVE on legitimate code -- the standing cost this
    project weighs far higher than an occasional missed case the runtime check still catches.

    `release` has a WIDER exemption than the other four, matching `_exec_ReleaseStmt` exactly:
    it releases a HELD binding or a TYPE CONTRACT, so a name is exempt from this refusal if it
    is ever held OR ever typed anywhere (`x as int`, the empty-typed-declaration form --
    `mohio_transformer_ast.py`'s `empty_typed_decl` emits an `Assignment` with `type_name` set).
    Checked 2026-10-04 (Ron's own instruction): `release` on a plain fluid, untyped variable
    checked clean and only refused at run, unlike the other four operators -- the ONE gap this
    scan left when it was first written, on the mistaken assumption that release's own runtime
    message was refusal enough. VERIFIED by running: `x 5 / release x` checked clean, then
    raised "release: 'x' has no protection to release" only at `mio run`.

    The ONE exception, matching the runtime exactly: `replace "old" with "new" in X as NAME`
    (an explicit capture) reads `X` and writes only to `NAME` -- a pure expression, never a
    variable-state operation, so it is not scanned regardless of `X`'s held status.

    `release.now`/`release.lock` are NOT scanned here -- both are RETIRED outright and refused
    by the separate `scan_release_now_lock_retired`, so only the plain `release` variant reaches
    this check.
    """
    from dataclasses import fields, is_dataclass
    from mohio_ast import HoldDecl, VarStateStmt, ReplaceBlock, ReleaseStmt, Assignment
    errors = []
    held_names = set()
    typed_names = set()

    def collect_held(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for item in node:
                collect_held(item)
            return
        if not is_dataclass(node):
            return
        if isinstance(node, HoldDecl) and getattr(node, 'name', ''):
            held_names.add(node.name)
        if isinstance(node, Assignment) and getattr(node, 'type_name', None) \
                and getattr(node, 'name', ''):
            typed_names.add(node.name)
        for f in fields(node):
            collect_held(getattr(node, f.name, None))

    collect_held(getattr(program, 'statements', None) or [])

    _op_word = {'clear': 'clear', 'forget': 'forget', 'rename': 'rename', 'replace': 'replace'}

    def walk(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for item in node:
                walk(item)
            return
        if not is_dataclass(node):
            return
        if isinstance(node, VarStateStmt) and node.op in _op_word and node.name \
                and node.name not in held_names:
            errors.append(CompileError(
                f"{node.op}: '{node.name}' is a fluid variable, not held -- {node.op} only "
                f"applies to a held variable. Use plain assignment instead: "
                f"`{node.name} <value>`, or hold it first: `hold {node.name} <value>`.",
                getattr(node, 'line', 0) or 0,
                f"Hold it first (`hold {node.name} <value>`), or drop `{node.op}` and use "
                f"plain assignment.",
            ))
            return
        if isinstance(node, ReplaceBlock) and not getattr(node, 'alias', '') \
                and node.target and node.target not in held_names:
            errors.append(CompileError(
                f"replace: '{node.target}' is a fluid variable, not held -- replace only "
                f"applies in place to a held variable. Use `... as NAME` to capture the "
                f"result instead, or hold it first: `hold {node.target} <value>`.",
                getattr(node, 'line', 0) or 0,
                f"Add `as NAME` to capture the result instead, or hold `{node.target}` "
                f"first (`hold {node.target} <value>`).",
            ))
            return
        # `release` of a name the program never holds is a no-op at run and WARNS at check
        # (scan_release_never_held); only a name that exists NOWHERE in the program refuses here,
        # because that is a typo (RULED by Ron 2026-10-10).
        if isinstance(node, ReleaseStmt) and node.variant == 'release' and node.name \
                and node.name not in held_names and node.name not in typed_names \
                and node.name not in _program_names(program):
            ln = getattr(node, 'line', 0) or 0
            errors.append(CompileError(
                f"Line {ln}: release: nothing in this program is called '{node.name}', so this "
                f"release names a value that does not exist. Check the spelling.",
                ln,
                f"Write the name as it is held, for example `release <name>` after "
                f"`hold <name> <value>`.",
            ))
            return
        for f in fields(node):
            walk(getattr(node, f.name, None))

    walk(getattr(program, 'statements', None) or [])
    return errors


def scan_release_now_lock_retired(program):
    """`release.now`/`release.lock` check clean today and refuse only at RUN
    (mohio_interpreter.py's `_exec_ReleaseStmt`) -- a clean check followed by a runtime
    refusal is the silent-until-run shape this project treats as highest severity. Both are
    RETIRED outright (Ron's ruling, 2026-10-04): a one-step release-and-reassign reads like a
    second `set`, the drift the five variable-state operators exist to prevent.

    Only the `= value` surface form (`release.now NAME = VALUE`, `release.lock NAME = VALUE`)
    parses to a `ReleaseStmt` with this variant at all -- the grammar's RELEASE_NOW/RELEASE_LOCK
    alternatives both require a trailing `value_expr`. A bare NAME with no value and no `=` does
    not match this rule and parses as a generic `service.method` call instead (the words split
    back into service `release`, method `now`); that untyped bare form was never the thing RULED
    retired above, so it is intentionally not scanned here.
    """
    from dataclasses import fields, is_dataclass
    from mohio_ast import ReleaseStmt
    errors = []
    _RETIRED = {
        'release.now': 'release.now',
        'release.lock': 'release.lock',
    }

    def walk(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for item in node:
                walk(item)
            return
        if not is_dataclass(node):
            return
        if isinstance(node, ReleaseStmt) and node.variant in _RETIRED:
            errors.append(CompileError(
                f"`{node.variant}` is retired -- a one-step release-and-reassign reads like "
                f"a second `set`, the same drift the five variable-state operators exist to "
                f"prevent.",
                getattr(node, 'line', 0) or 0,
                f"Write it as two plain statements instead: `release {node.name}` then "
                f"`{node.name} <value>`.",
            ))
            return
        # The bare form with no value (`release.lock x`) parses as a service call instead, so it
        # checked clean and stopped at run as "not wired yet, tracked for a future release",
        # which says a retired word is coming (RULED by Ron 2026-10-09: it refuses like the rest).
        if (type(node).__name__ == 'ServiceCallStmt' and getattr(node, 'service', '') == 'release'
                and getattr(node, 'method', '') in ('now', 'lock')):
            variant = 'release.' + node.method
            args = getattr(node, 'args', None)
            parts = getattr(args, 'parts', None)
            held = '.'.join(parts) if parts else '<name>'     # the teaching placeholder, shown as such
            errors.append(CompileError(
                f"`{variant}` is retired -- a one-step release-and-reassign reads like "
                f"a second `set`, the same drift the five variable-state operators exist to "
                f"prevent.",
                getattr(node, 'line', 0) or 0,
                f"Write it as two plain statements instead: `release {held}` then "
                f"`{held} <value>`.",
            ))
            return
        for f in fields(node):
            walk(getattr(node, f.name, None))

    walk(getattr(program, 'statements', None) or [])
    return errors


_AGENT_RESULT_FIELD_NAMES = ('cost', 'steps', 'tokens', 'error', 'explanation')


def scan_old_agent_result_underscore_form(program):
    """`_agent_<name>_cost` and its four siblings (`_steps`, `_tokens`, `_error`,
    `_explanation`) are the OLD internal spelling an `ai.agent` block used to leak as a plain
    variable, before the registered dotted result word (`<name>.cost` etc., read through
    `Context.get_dotted`'s `AGENT_RESULT_FIELDS` intercept) replaced it (Ron's ruling,
    2026-10-03). The interpreter no longer produces the old name at all, so reading it bare
    now falls through to the ordinary undeclared-variable refusal -- loud, but generic. This
    scanner gives the SPECIFIC, teaching refusal at check time instead, naming the dotted
    replacement, the same DEFER-CHECK-MIRRORED shape every other retirement in this file uses.

    CONSERVATIVE BY DESIGN, GLOBAL rather than scope-precise, matching
    `scan_state_op_requires_held`: an agent name counts as "declared" if ANY `ai.agent NAME`
    appears ANYWHERE in the program. Only a bare reference whose underscored shape names an
    ACTUALLY-DECLARED agent is flagged -- an unrelated variable that happens to be named
    `_agent_something_cost` by coincidence, with no `ai.agent something` anywhere, is left
    alone (it was never this leaking form to begin with).
    """
    import re
    from dataclasses import fields, is_dataclass
    from mohio_ast import AiAgentBlock, DottedName
    errors = []
    agent_names = set()

    def collect_agents(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for item in node:
                collect_agents(item)
            return
        if not is_dataclass(node):
            return
        if isinstance(node, AiAgentBlock) and getattr(node, 'name', ''):
            agent_names.add(node.name)
        for f in fields(node):
            collect_agents(getattr(node, f.name, None))

    collect_agents(getattr(program, 'statements', None) or [])
    if not agent_names:
        return errors

    _pattern = re.compile(
        r'^_agent_(' + '|'.join(re.escape(n) for n in agent_names) + r')_('
        + '|'.join(_AGENT_RESULT_FIELD_NAMES) + r')$')

    def walk(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for item in node:
                walk(item)
            return
        if not is_dataclass(node):
            return
        if isinstance(node, DottedName) and len(node.parts) == 1:
            m = _pattern.match(str(node.parts[0]))
            if m:
                agent_name, field_name = m.group(1), m.group(2)
                errors.append(CompileError(
                    f"'_agent_{agent_name}_{field_name}' is the retired internal spelling "
                    f"for an ai.agent result field -- use `{agent_name}.{field_name}` "
                    f"instead.",
                    getattr(node, 'line', 0) or 0,
                    f"Write `{agent_name}.{field_name}` in place of "
                    f"`_agent_{agent_name}_{field_name}`.",
                ))
                return
        for f in fields(node):
            walk(getattr(node, f.name, None))

    walk(getattr(program, 'statements', None) or [])
    return errors


def scan_bare_now_uuid_as_value(program):
    """`now`/`uuid` used bare, alone, as a value (`x now`, `show uuid`) check clean today and
    refuse only at RUN, as `bare_intrinsic_as_value` (mohio_interpreter.py) -- a clean check
    followed by a runtime 500 is the silent-until-run shape this project treats as highest
    severity (overnight ORDER item 2e, 2026-10-04).

    THE NARROW SCAN, not a general one: confirmed by direct AST inspection before writing this
    -- `(now - 2 days)`, `until now`, and `where created_at is after now` ALL parse to a
    dedicated `TimeExpr` node (`base='now()'`), never a bare `DottedName`. The grammar has no
    rule that produces `DottedName(parts=['now'])`/`(['uuid'])` for any of the documented safe
    temporal/math contexts -- only a genuinely bare reference (an Assignment's value, a ShowStmt,
    or any other value-position leaf) ever reaches this AST shape. So flagging EVERY
    `DottedName(['now'])`/`(['uuid'])` anywhere in the program is safe: there is no legitimate
    construct this scanner could mistake for the unsafe one, the same confidence
    `scan_bare_random_intrinsic` (right below) already has for its own bare-intrinsic shape.
    """
    from dataclasses import fields, is_dataclass
    from mohio_ast import DottedName
    errors = []
    _produces = {'now': 'the current moment', 'uuid': 'a new id'}
    _rep = {
        'now': " Bare `now` names the current moment only in a temporal or math context, "
              "like `(now - 2 days)`.",
        'uuid': " There is no bare form: an id has no temporal or math context to represent, "
               "so `uuid` alone is never a value.",
    }

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
            if len(parts) == 1 and str(parts[0]) in _produces:
                w = str(parts[0])
                errors.append(CompileError(
                    f"`{w}` on its own is not a value. Write `{w}()` to produce "
                    f"{_produces[w]}.{_rep[w]}",
                    getattr(node, 'line', 0) or 0,
                    f"`{w}()` creates the value (for example `created_at {w}()` or "
                    f"`default {w}()`).",
                ))
            return
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
                    f"Write each handler on its own: "
                    f"`listen for sh.<Shape> at /your/path` for a write or "
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
            # the block's own name is the DECLARATION, not a read -- walk children, skip the name
            for f in fields(node):
                walk(getattr(node, f.name, None))
            return
        if kind == 'AiDecideInvoke':
            nm = str(getattr(node, 'name', '') or '')
            if nm:
                invoked.add(nm)
            return
        # Any OTHER node carrying the decision's name is a READ of its result (`check NAME`,
        # `show NAME`, `& NAME`, ...). Detected broadly -- a plain `name`/`parts` attribute, the
        # same way the sibling warning scan does -- because a check/show subject is not always a
        # VarRef/DottedName node, which is why the narrow earlier form of this scan missed it.
        nm = getattr(node, 'name', None)
        if isinstance(nm, str) and nm:
            read.setdefault(nm, node)
        for _attr in ('live_name', 'chain_name', 'decision', 'decision_name'):
            _v = getattr(node, _attr, None)
            if isinstance(_v, str) and _v:
                read.setdefault(_v, node)
        _parts = getattr(node, 'parts', None)
        if _parts:
            read.setdefault(str(_parts[0]), node)
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

    def walk(node, pos='top', inside=frozenset()):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                walk(c, pos, inside)
            return
        if not is_dataclass(node):
            return
        # INSIDE `listen for sh.X` / `request for sh.X`, `X.field` IS the request data (RULED by Ron
        # 2026-10-09: the validated data is read by the shape's name exactly as declared). Outside
        # that handler a bare shape name still says nothing about what it reads, and refuses.
        if type(node).__name__ in ('NewBlock', 'RequestInboundBlock') and getattr(node, 'shape', None):
            inside = inside | {str(node.shape)}
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
                elif len(parts) >= 2 and parts[0] not in ('db', 'sh') and parts[0] not in bound \
                        and parts[0] not in inside:
                    head = parts[0]
                    if head in tables:
                        errors.append(CompileError(
                            f"`{head}.{parts[1]}` does not say what it is reading. `{head}` is "
                            f"a table a shape declares, and table content is always reached "
                            f"through `db.` -- write `db.{head}.{parts[1]}`. The prefix is what "
                            f"tells a reader this is data.",
                            line=ln))
                    elif head in shapes:
                        # `X.field` is the data a request for sh.X brought (RULED by Ron
                        # 2026-10-10), and that exists only inside its handler. This used to say
                        # "write sh.X", which is the shape itself, never a value.
                        errors.append(CompileError(
                            f"Line {ln}: `{head}.{parts[1]}` reads the data a request for "
                            f"sh.{head} brings, and that exists only inside a handler for it. "
                            f"Move the read into that handler (`listen for sh.{head} at /<path>` ... "
                            f"`{head}.{parts[1]}`), or, if `{parts[1]}` is stored data, reach it "
                            f"through `db.<table>.{parts[1]}`.",
                            line=ln))
        for f in fields(node):
            walk(getattr(node, f.name, None), f.name, inside)

    # A TOP-LEVEL DECLARATION A HANDLER INVOKES (a task it calls, an ai.decide it names) runs in
    # that handler's scope, so `X.field` there reads the request for sh.X too.
    invoked = handler_invoked_shapes(program)
    for st in (getattr(program, 'statements', None) or []):
        walk(st, inside=invoked.get(str(getattr(st, 'name', '') or ''), frozenset()))
    return errors


def spelling_key(name):
    """A name with its spelling choices removed: `staff_status`, `staffStatus` and `StaffStatus`
    give one key. Check's did-you-mean and mio fmt's rewrite match a shape name by it."""
    return str(name).replace("_", "").lower()


def handler_invoked_shapes(program):
    """{declaration name: the shapes of every handler that invokes it}, for the top-level tasks and
    AI blocks a `listen for sh.X` / `request for sh.X` handler names in its body. Such a declaration
    runs in the handler's scope (a task's body runs in a child of its caller's), so `X.field` in it
    reads that handler's request data. A name no handler mentions maps to nothing."""
    from mohio_ast import NewBlock, RequestInboundBlock
    stmts = getattr(program, 'statements', None) or []
    names = {str(getattr(st, 'name', '') or '') for st in stmts
             if not isinstance(st, (NewBlock, RequestInboundBlock)) and type(st).__name__ != 'ShapeDecl'}
    names.discard('')
    out = {}
    for h in _all_nodes(stmts):
        if not isinstance(h, (NewBlock, RequestInboundBlock)) or not getattr(h, 'shape', None):
            continue
        for n in _all_nodes(getattr(h, 'body', None) or []):
            for f in fields(n):
                v = getattr(n, f.name, None)
                if isinstance(v, str) and v in names:
                    out.setdefault(v, set()).add(str(h.shape))
    return {k: frozenset(v) for k, v in out.items()}


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
                              "erased." % (fname, "[%s]" % ", ".join(own))),
                        code="regulated_field_needs_type"))
                elif fname.lower() in sector_fields:
                    sec, tags = sector_fields[fname.lower()]
                    errors.append(CompileError(
                        "`%s` is classified %s by the `%s` sector and has no declared type. "
                        "Regulated data is never inferred."
                        % (fname, ", ".join("[%s]" % t for t in tags), sec),
                        line=line,
                        hint=("Say what it is, for example `%s as text`. The sector regulates this "
                              "field name wherever it appears, so the shape has to state the type "
                              "even though the classification came from the profile." % fname),
                        code="regulated_field_needs_type"))
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
                                 ", ".join("[%s]" % t for t in missing))),
                        code="shape_may_not_loosen_sector"))
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



_VALID_INTERP_NAME = re.compile(r'^[A-Za-z_]\w*(?:\.\w+)*$')
_INTERP_DIRECTIVE_KW = ('guard', 'form', 'field', 'include')
_RENDER_INCLUDE_RE = re.compile(r'include\s*"([^"]*)"\s*$')


def _iter_render_includes(html, base_dir, anchor_base_line, seen, top_anchor=None):
    """Yield (content, include_path, anchor_line) for every file a render body `{{ include "x" }}`s,
    recursively, following nested includes so a check-time content scan sees the SAME text the
    runtime renders. Without this, an authoring error inside an included presentation file (an
    inline `{{ each }}`, an unresolvable `{{ }}`) checks clean and then 500s at run -- FAC-27's
    include hole, and it was shared by every check-time render-content scan, because none of them
    descended into the included file.

    The path is resolved cwd-relative, EXACTLY as the interpreter's `_render_include_directive`
    does (`_include_base_dir` is never set, so both fall back to the working directory): a file the
    runtime cannot find, the scan cannot find either, and the runtime reports `include_not_found`
    itself, so a missing or unreadable include is skipped here rather than raising a second, weaker
    error at check. `anchor_line` is the .mho line of the TOP-LEVEL include that pulled the chain
    in, so an error in a nested file is still reported at a line the developer can open. A cycle is
    not recursed into (the runtime fails it loud separately)."""
    import os as _os
    for m in re.finditer(r'(\\?)\{\{([^}]+)\}\}', html or ''):
        if m.group(1):
            continue  # a backslash-escaped pair renders literally
        im = _RENDER_INCLUDE_RE.match(m.group(2).strip())
        if not im:
            continue
        this_anchor = top_anchor if top_anchor is not None \
            else (anchor_base_line + html[:m.start()].count('\n'))
        abspath = _os.path.abspath(_os.path.join(base_dir, im.group(1)))
        if abspath in seen:
            continue  # a cycle -- the runtime fails loud on it; the scan simply stops descending
        try:
            with open(abspath, encoding='utf-8-sig') as fh:
                content = fh.read()
        except OSError:
            continue  # unresolvable at check; runtime reports include_not_found
        yield (content, im.group(1), this_anchor)
        yield from _iter_render_includes(content, base_dir, 1, seen | {abspath},
                                         top_anchor=this_anchor)


def scan_render_interpolation_unresolvable(program):
    """A `{{ ... }}` in a render/output body whose contents can NEVER resolve -- caught at check
    instead of 500ing at runtime.

    `{{ }}` inserts a value. At runtime the interpolator reads the contents as a directive
    (`form`/`field`/`guard`/`include`), an `each` view loop, or a dotted variable name; anything
    else it splits on the first dot and looks up as a variable, which cannot exist, so the request
    dies with `unknown_variable`. `mio check` used to pass every one of these -- the second "check
    clean, runtime 500" of the class the uninvoked-ai.decide error closed. This catches the
    GUARANTEED failures (arithmetic, a quoted string, a call, `{{ include "x" }}` written as bare
    text somewhere it cannot mean the directive), while leaving a bare or dotted NAME to runtime,
    because that name may legitimately be bound by a query at run time.
    """
    errors = []

    # The retired `_agent_<name>_<field>` spelling (scan_old_agent_result_underscore_form's own
    # AST-level check) reaches a bare NAME here too -- `{{ _agent_helper_cost }}` is syntactically
    # just a variable name, so it fell straight through to "may be bound at runtime" below with
    # no teaching refusal, and the runtime's own generic `unknown_variable` never names the
    # retired form or its replacement. Same posture: an agent name counts as declared if ANY
    # `ai.agent NAME` appears anywhere in the program.
    _agent_names = set()

    def _collect_agent_names(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for item in node:
                _collect_agent_names(item)
            return
        if not is_dataclass(node):
            return
        if type(node).__name__ == 'AiAgentBlock' and getattr(node, 'name', ''):
            _agent_names.add(node.name)
        for f in fields(node):
            _collect_agent_names(getattr(node, f.name, None))

    _collect_agent_names(getattr(program, 'statements', None) or [])
    _retired_agent_pattern = re.compile(
        r'^_agent_(' + '|'.join(re.escape(n) for n in _agent_names) + r')_('
        + '|'.join(_AGENT_RESULT_FIELD_NAMES) + r')$') if _agent_names else None

    def check_html(html, base_line, origin=None, anchor_line=None):
        if not html or '{{' not in html:
            return

        def _line_and_prefix(pos):
            # For an included file report at the .mho include line the developer can open, naming
            # the file and the line INSIDE it; for a direct body, the ordinary source line.
            if origin is not None:
                inner = html[:pos].count('\n') + 1
                return (anchor_line or None, f'In the included file "{origin}" (line {inner}): ')
            return (((base_line or 0) + html[:pos].count('\n')) or None, '')

        for m in re.finditer(r'(\\?)\{\{([^}]+)\}\}', html):
            if m.group(1):
                continue  # a backslash-escaped pair renders literally; nothing is evaluated
            expr = m.group(2).strip()
            if not expr:
                continue  # an empty {{ }} is left to runtime's own handling
            first = expr.split()[0]
            if first in _INTERP_DIRECTIVE_KW:
                continue  # a directive; it validates itself (and fails loud) at runtime
            if first == 'each' or expr.startswith('each:'):
                # A view loop is LINE-ANCHORED: `{{ each NAME in SOURCE }}` / `{{ each: done }}` each
                # ALONE on its line, with the row markup on the lines between. An INLINE `{{ each ... }}`
                # (markup on the same line) is NOT a view loop -- `_RENDER_EACH_OPEN` never matches it,
                # so the runtime reads the whole marker as a variable and 500s with `unknown_variable`
                # (FAC-27). Refuse it here, at the source line, pointing at the multiline form. The
                # runtime's OWN opener/closer regexes are the test, so check and run cannot drift on
                # what counts as a view loop.
                from mohio_runtime_vocab import _RENDER_EACH_OPEN as _EACH_OPEN, \
                    _RENDER_EACH_CLOSE as _EACH_CLOSE
                _ls = html.rfind('\n', 0, m.start()) + 1
                _le = html.find('\n', m.end())
                _whole = html[_ls:(_le if _le != -1 else len(html))]
                if _EACH_OPEN.match(_whole) or _EACH_CLOSE.match(_whole):
                    continue  # a real, line-anchored view loop, handled by the each machinery
                line, _pfx = _line_and_prefix(m.start())
                errors.append(CompileError(
                    _pfx +
                    f"{{{{ {expr} }}}} is not a valid view loop. A view loop stands alone on its line as "
                    f"`{{{{ each NAME in SOURCE }}}}`, closed by `{{{{ each: done }}}}` on its own line, with "
                    f"the row markup on the lines between. Written another way -- inline with markup, or "
                    f"missing the `in` -- it checks clean and then fails at run with `unknown_variable`, "
                    f"because the whole marker is read as a variable name.",
                    line=line or None,
                    hint=("Put the loop on its own lines, row markup between:\n"
                          "    {{ each row in rows }}\n"
                          "        <li>{{ row.name }}</li>\n"
                          "    {{ each: done }}")))
                continue
            if _retired_agent_pattern is not None:
                _rm = _retired_agent_pattern.match(expr)
                if _rm:
                    _an, _fn = _rm.group(1), _rm.group(2)
                    line, _pfx = _line_and_prefix(m.start())
                    errors.append(CompileError(
                        _pfx +
                        f"'_agent_{_an}_{_fn}' is the retired internal spelling for an "
                        f"ai.agent result field -- use `{_an}.{_fn}` instead.",
                        line=line or None,
                        hint=f"Write {{{{ {_an}.{_fn} }}}} in place of {{{{ _agent_{_an}_{_fn} }}}}."))
                    continue
            if _VALID_INTERP_NAME.match(expr):
                continue  # a variable name; may be bound at runtime
            line, _pfx = _line_and_prefix(m.start())
            errors.append(CompileError(
                _pfx +
                f"{{{{ {expr} }}}} is not something Mohio can insert. Double braces insert a "
                f"value: a variable name, or a `form` / `field` / `guard` / `include` directive. "
                f"This is none of those, so it checks clean and then fails at run time with "
                f"`unknown_variable`.",
                line=line or None,
                hint=("Put a variable name inside the braces, or the right directive. A literal "
                      "value belongs outside the braces, as ordinary text.")))

    def walk(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                walk(c)
            return
        if not is_dataclass(node):
            return
        if type(node).__name__ == 'ShowBlock':
            import os as _os
            _html = getattr(node, 'html', '') or ''
            _base = getattr(node, 'line', 0) or 0
            check_html(_html, _base)
            for _content, _origin, _anchor in _iter_render_includes(_html, _os.getcwd(), _base, set()):
                check_html(_content, 0, origin=_origin, anchor_line=_anchor)
        for f in fields(node):
            walk(getattr(node, f.name, None))

    for st in (getattr(program, 'statements', None) or []):
        walk(st)
    return errors


def scan_render_include_bare(program):
    """A bare `include "file"` line inside a render block is emitted as literal text, not run.

    Inside render the body is HTML text by default, and the one escape into evaluation is `{{ }}`.
    So `include "x"` on its own line renders the characters `include "x"`, which is almost never
    what was meant. A WARNING, not an error: a page teaching Mohio may show that line on purpose,
    and a render body is free text.
    """
    warnings = []
    bare = re.compile(r'^include\s+"[^"]*"\s*$')

    def check_lines(html, base_line, origin=None, anchor_line=None):
        for i, raw in enumerate(html.split('\n')):
            s = raw.strip()
            if bare.match(s):
                if origin is not None:
                    warnings.append(CompileWarning(
                        f'In the included file "{origin}" (line {i + 1}): `{s}` is emitted as '
                        f"literal text, not run. To load and insert the file here, write it as an "
                        f"evaluation: {{{{ {s} }}}}.",
                        line=anchor_line or None, code="include_line_emitted_as_text"))
                else:
                    warnings.append(CompileWarning(
                        f"`{s}` inside a render block is emitted as literal text, not run. To load "
                        f"and insert the file here, write it as an evaluation: {{{{ {s} }}}}.",
                        line=(base_line + i) or None, code="include_line_emitted_as_text"))

    def walk(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                walk(c)
            return
        if not is_dataclass(node):
            return
        if type(node).__name__ == 'ShowBlock':
            import os as _os
            html = getattr(node, 'html', '') or ''
            base = getattr(node, 'line', 0) or 0
            check_lines(html, base)
            for _content, _origin, _anchor in _iter_render_includes(html, _os.getcwd(), base, set()):
                check_lines(_content, 0, origin=_origin, anchor_line=_anchor)
        for f in fields(node):
            walk(getattr(node, f.name, None))

    for st in (getattr(program, 'statements', None) or []):
        walk(st)
    return warnings


def scan_duplicate_routes(program):
    """Two handlers that answer the SAME method and path is a mistake: a method and path together
    name exactly one route, so the second can never be reached, silently. Refuse it at check and name
    both handlers by line (Batch 16). Standalone handlers at the top level AND loose inside a journey
    register here.

    A PATHLESS HANDLER BINDS TO ITS PAGE'S OWN LOCATION (ruled Mar 30 and Apr 3, locked Jul 18; the
    route sweep, RULED by Ron 2026-10-08): it is optional on a page and REQUIRED in a journey. So two
    pathless handlers of one method on a page are a duplicate wherever the page is served, and a
    pathless one beside `at /` of the same method collides wherever the page is served at `/` (always
    for a file served on its own), so both refuse. A pathless handler in a journey has no page of its
    own to bind to, and refuses, naming the line.
    """
    errors = []
    seen = {}
    PAGE = '<the page>'

    def _norm(p):
        if not p:
            return None
        p = str(p).split('?', 1)[0]
        return p[:-1] if len(p) > 1 and p.endswith('/') else p

    def _method(h):
        n = type(h).__name__
        if n == 'NewBlock':
            return 'POST'
        if n == 'RequestInboundBlock':
            return 'GET'
        return None   # connection / change / from-connector are not method+path HTTP routes

    def _opener(h):
        verb = 'listen for' if type(h).__name__ == 'NewBlock' else 'request for'
        return f"{verb} sh.{getattr(h, 'shape', '?')}"

    def _register(handlers, journey=None):
        for h in handlers:
            m = _method(h)
            if m is None:
                continue
            line = getattr(h, 'line', 0) or 0
            path = _norm(getattr(h, 'path', None))
            if path is None and journey is not None:
                errors.append(CompileError(
                    f"Line {line}: `{_opener(h)}` inside journey `{journey}` has no `at /path`. A "
                    f"handler on a page answers at that page's own address, but a journey is not a "
                    f"page, so this handler would have no address of its own. Say where it answers, "
                    f"for example `{_opener(h)} at /orders`.",
                    line=line))
                continue
            key = (m, path if path is not None else PAGE)
            for other in ([(m, '/')] if key[1] == PAGE else [(m, PAGE)] if key[1] == '/' else []):
                if other in seen:
                    first = seen[other]
                    errors.append(CompileError(
                        f"Two handlers answer {m} at this page's own address: one at line {first} and "
                        f"one at line {line}. A handler with no `at /path` answers at its page's "
                        f"address, which is `/` when this file is served on its own, the same as `at "
                        f"/`, so the second could never be reached. Give one of them its own path, "
                        f"for example `{_opener(h)} at /other`.",
                        line=line))
                    break
            if key in seen:
                where = (f"{m} {path}" if path is not None else
                         f"{m} at this page's own address (neither names an `at /path`)")
                errors.append(CompileError(
                    f"Two handlers answer {where}: one at line {seen[key]} and one at line {line}. A "
                    f"method and path together name exactly one route, so the second can never be "
                    f"reached. Give one a different path, or merge them.",
                    line=line))
                continue
            seen[key] = line

    _HANDLER_NAMES = ('NewBlock', 'RequestInboundBlock', 'ConnectionBlock', 'ChangeBlock',
                      'FromConnectorBlock')
    stmts = getattr(program, 'statements', []) or []
    _register([s for s in stmts if type(s).__name__ in _HANDLER_NAMES])
    for s in stmts:
        if type(s).__name__ == 'JourneyDecl':
            _register([b for b in (getattr(s, 'body', []) or [])
                       if type(b).__name__ in _HANDLER_NAMES], journey=getattr(s, 'name', '?'))
    return errors


# ── inbound webhook verification (Batch 17) ──────────────────────────────────────────────────────
# Scoped to `new sh.X` (POST) handlers, the surface Ron's `new sh.Payment at /webhooks/stripe`
# example names and where the runtime verification is wired (interpreter `_exec_new_listener`).
# `request for` (GET) reads are already covered by scan_sector_route_unauthenticated; `change to` and
# `from <connector>` are follow-on surfaces (their runtime wiring extends next). Keyed to sector
# PRESENCE, like scan_financial_multi_write_no_transaction is keyed to a financial name: profile rules
# cannot yet carry a "webhook must be verified" rule (T1-PROFILE-RULES), so a declared sector is
# treated as regulated here. When profiles can carry structural rules, read it from the profile.
# Defined before ERROR_SCANS/WARNING_SCANS so both tuples can name these functions.
_INBOUND_PAYLOAD_HANDLERS = ('NewBlock',)


def _webhook_active_sector(program):
    found = [None]

    def walk(node):
        if found[0] is not None or node is None:
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                walk(c)
            return
        if not is_dataclass(node):
            return
        if type(node).__name__ == 'SectorDecl':
            found[0] = (str(getattr(node, 'name', '') or getattr(node, 'sector', '') or '').strip()
                        or None)
            return
        for f in fields(node):
            walk(getattr(node, f.name, None))

    walk(getattr(program, 'statements', None) or [])
    return found[0]


_BROWSER_FACING_NODES = ('ShowBlock', 'JumpToStmt', 'ForwardStmt', 'RedirectDecl')


def _webhook_is_browser_facing(node):
    """True if a handler RENDERS a page (ShowBlock, from a `render` block) or REDIRECTS (JumpToStmt) --
    a form a person submits, guarded by the form guard (CSRF/honeypot), NOT a service-to-service
    webhook. A browser cannot HMAC-sign, so `verify signature` is the wrong control for it, and the
    webhook rules below do not apply. A webhook acks with a status/JSON and neither renders nor
    redirects, so it is not caught here. This is how the rules tell a webhook from a browser form
    without a per-handler declaration: by what the handler DOES with the request."""
    found = [False]

    def walk(n):
        if found[0] or n is None:
            return
        if isinstance(n, (list, tuple)):
            for c in n:
                walk(c)
            return
        if not is_dataclass(n):
            return
        if type(n).__name__ in _BROWSER_FACING_NODES:
            found[0] = True
            return
        for f in fields(n):
            walk(getattr(n, f.name, None))

    walk(getattr(node, 'body', None) or [])
    return found[0]


def _webhook_inbound_handlers(program):
    """Every WEBHOOK-shaped inbound payload handler, each as (node, has_require_role, verify_or_None).
    Browser-facing handlers (they render or redirect -- a person's form, CSRF-guarded) are excluded,
    because a signature is the wrong control for them; see _webhook_is_browser_facing."""
    out = []

    def contains_role(node):
        if node is None:
            return False
        if isinstance(node, (list, tuple)):
            return any(contains_role(c) for c in node)
        if not is_dataclass(node):
            return False
        if type(node).__name__ == 'RequireRoleDecl':
            return True
        return any(contains_role(getattr(node, f.name, None)) for f in fields(node))

    def find_verify(node):
        for st in (getattr(node, 'body', None) or []):
            if is_dataclass(st) and type(st).__name__ == 'VerifySignatureDecl':
                return st
        return None

    def walk(node):
        if node is None:
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                walk(c)
            return
        if not is_dataclass(node):
            return
        if type(node).__name__ in _INBOUND_PAYLOAD_HANDLERS and not _webhook_is_browser_facing(node):
            out.append((node, contains_role(node), find_verify(node)))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    walk(getattr(program, 'statements', None) or [])
    return out


def _handler_is_auth_boundary(node):
    """Is this handler an AUTHENTICATION BOUNDARY -- does it establish OR end a session?

    A login is where identity is CREATED, so it cannot be asked to prove an identity that does not
    exist yet. A logout is the mirror: it DESTROYS whatever session exists -- anonymous, expired or
    signed-in alike -- so requiring a role to reach it is the wrong prerequisite (you would be locked
    out of logging out). Both are the boundary, and both are detected by BEHAVIOUR, not by a path
    name or an allow-list:
      - `grant role` (GrantRoleDecl) writes a server-verified role onto the session root -- a login.
      - `end session` (EndSessionStmt) invalidates the session -- a logout.
    These are the only session-lifecycle statements this build has; if a session-start or session-end
    verb is added later, recognise it here so the exemption stays behaviour-based. A handler that does
    NEITHER (an open `/demo-login` that grants no role, a bare `/ingest`) is not a boundary and still
    warns."""
    if node is None:
        return False
    if isinstance(node, (list, tuple)):
        return any(_handler_is_auth_boundary(c) for c in node)
    if not is_dataclass(node):
        return False
    if type(node).__name__ in ('GrantRoleDecl', 'EndSessionStmt'):
        return True
    return any(_handler_is_auth_boundary(getattr(node, f.name, None)) for f in fields(node))


def _webhook_where(node):
    w = getattr(node, 'path', None) or getattr(node, 'shape', None)
    return w if w else 'a handler'   # display label for a message only


# ── secret rotation (item 7) ─────────────────────────────────────────────────────────────────────
# Rotation frequency and date live in ENVIRONMENT variables, derived from the secret's name: for a
# secret at env.X, env.X_ROTATED is the date it was last rotated (ISO, e.g. 2026-09-01) and
# env.X_ROTATION is the interval (e.g. "90 days"). Enforced ONLY under a sector, at CHECK and at
# STARTUP -- never mid-request. Outside a sector nothing is required. This is the durable-record and
# refusal half; the reminder half (approaching expiry) belongs to the platform control plane, NOT to
# compiler nudges (Ron 2026-09-22), and is deliberately not built.
def _parse_rotation_interval(text):
    """`90 days` / `12 weeks` / `6 months` -> a timedelta, or None if unparseable."""
    import re as _re
    from datetime import timedelta
    m = _re.match(r'^\s*(\d+)\s*([a-zA-Z]+)\s*$', str(text or ''))
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2).lower().rstrip('s')
    days = {'day': 1, 'week': 7, 'month': 30, 'year': 365}.get(unit)
    if unit in ('hour', 'minute', 'second'):
        return timedelta(**{unit + 's': n})
    if days is None:
        return None
    return timedelta(days=n * days)


def _rotation_status(secret_env):
    """Read env.{X}_ROTATED and env.{X}_ROTATION and classify. Returns (status, detail) where status
    is one of 'ok' | 'missing_date' | 'missing_interval' | 'bad_date' | 'bad_interval' | 'expired'.
    Reads os.environ, so it reflects the environment `mio check`/startup runs in -- which is the
    point: the refusal fires wherever the secret is actually configured (or is not)."""
    import os as _os
    from datetime import datetime, timezone
    rotated = _os.environ.get(secret_env + '_ROTATED')
    interval = _os.environ.get(secret_env + '_ROTATION')
    if not rotated:
        return ('missing_date', secret_env + '_ROTATED')
    if not interval:
        return ('missing_interval', secret_env + '_ROTATION')
    try:
        dt = datetime.fromisoformat(rotated.strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return ('bad_date', rotated)
    delta = _parse_rotation_interval(interval)
    if delta is None:
        return ('bad_interval', interval)
    if datetime.now(timezone.utc) > dt + delta:
        return ('expired', f"rotated {rotated}, interval {interval}")
    return ('ok', f"rotated {rotated}, interval {interval}")


def _rotation_error_message(where, secret_env, status, detail):
    if status == 'missing_date':
        return (f"`{where}` uses the secret env.{secret_env} under a regulated sector, so its rotation "
                f"date must be recorded, and env.{secret_env}_ROTATED is not set. Set it to the date "
                f"the secret was last rotated:\n        {secret_env}_ROTATED=2026-09-01")
    if status == 'missing_interval':
        return (f"`{where}` uses the secret env.{secret_env} under a regulated sector, so its rotation "
                f"interval must be recorded, and env.{secret_env}_ROTATION is not set. Set how often "
                f"it must rotate:\n        {secret_env}_ROTATION=90 days")
    if status == 'bad_date':
        return (f"env.{secret_env}_ROTATED is not a date the compiler can read ({detail!r}). Use an "
                f"ISO date:\n        {secret_env}_ROTATED=2026-09-01")
    if status == 'bad_interval':
        return (f"env.{secret_env}_ROTATION is not an interval the compiler can read ({detail!r}). "
                f"Use a number and a unit:\n        {secret_env}_ROTATION=90 days")
    if status == 'expired':
        return (f"`{where}` uses the secret env.{secret_env}, which is PAST its rotation interval "
                f"({detail}). Under a regulated sector an app cannot start on an expired secret. "
                f"Rotate the secret, then update {secret_env}_ROTATED to the new date.")
    return ""


def _webhook_secrets_under_sector(program):
    """Distinct (secret_env, where) for every verify clause on an inbound handler."""
    seen = {}
    for node, _has_role, vdecl in _webhook_inbound_handlers(program):
        if vdecl is not None and getattr(vdecl, 'secret_env', None):
            seen.setdefault(vdecl.secret_env, (vdecl, _webhook_where(node)))
    return seen


def webhook_rotation_problems(program):
    """The rotation problems for a program, as (vdecl, where, secret_env, status, detail) tuples, one
    per verify-clause secret under a REAL regulated sector (not `demo_*`) whose rotation record is
    missing, unreadable or expired. The shared core of the CHECK scan and the STARTUP gate: both read
    this, so the compile-time refusal and the boot-time refusal can never diverge. Named without the
    `scan_` prefix on purpose -- the CLI calls THIS, never a scanner by hand (a structural invariant).

    Scope: rotation is an operational deployment policy (a real secret, rotated on a real schedule,
    its dates in the real environment), not a code pattern to teach. A demo sector demonstrates the
    `verify signature` PATTERN without demanding real rotation dates at check time, which would make
    every demo unrunnable. Item 5 (verify required) still fires on a demo sector, so the pattern is
    taught; only the operational rotation burden is scoped to real regulated sectors."""
    sector = _webhook_active_sector(program)
    if not sector or str(sector).lower().startswith('demo'):
        return []
    problems = []
    for secret_env, (vdecl, where) in _webhook_secrets_under_sector(program).items():
        status, detail = _rotation_status(secret_env)
        if status != 'ok':
            problems.append((vdecl, where, secret_env, status, detail))
    return problems


def scan_webhook_secret_rotation(program):
    """Under a regulated sector, a `verify signature` secret must carry a rotation date and interval
    (env.X_ROTATED / env.X_ROTATION), and must not be past that interval. Missing or expired is an
    ERROR at check. Startup enforces the same expiry via `webhook_rotation_problems`, so an app can
    never run on an expired secret under a real regulated sector."""
    return [CompileError(_rotation_error_message(where, secret_env, status, detail),
                         line=getattr(vdecl, 'line', 0) or 0, code="webhook_secret_rotation")
            for (vdecl, where, secret_env, status, detail) in webhook_rotation_problems(program)]


def scan_webhook_unverified_error(program):
    """Under a declared (regulated) sector, a POST handler that accepts a payload with NEITHER
    `require role` NOR `verify signature` is an ERROR: anyone who can reach the address can send a
    payload it will act on, and nothing proves who sent it. This is distinct from
    scan_sector_route_unauthenticated, which warns about unauthenticated data READS -- `require role`
    is a session control, not the control a webhook needs, so a webhook satisfies THIS rule with a
    signature check, not a role."""
    sector = _webhook_active_sector(program)
    if not sector:
        return []
    errors = []
    for node, has_role, vdecl in _webhook_inbound_handlers(program):
        if not has_role and vdecl is None and not _handler_is_auth_boundary(node):
            errors.append(CompileError(
                f"`{_webhook_where(node)}` accepts an inbound request in the `{sector}` sector with "
                f"nothing proving who sent it. Anyone who can reach the address can POST a payload it "
                f"will act on. Add a signature check so only the real sender is accepted:\n"
                f"        verify signature using env.YOUR_WEBHOOK_SECRET\n"
                f"(Or `require role \"...\"` if this is a signed-in user's endpoint, not a webhook.)",
                line=getattr(node, 'line', 0) or 0, code="sector_webhook_unverified"))
    return errors


def scan_webhook_unverified_warning(program):
    """Outside any sector, the same situation is a WARNING: a developer is still building and an open
    endpoint may be intentional for now. Under a sector it is an error (the scan above)."""
    if _webhook_active_sector(program):
        return []
    warnings = []
    for node, has_role, vdecl in _webhook_inbound_handlers(program):
        if not has_role and vdecl is None and not _handler_is_auth_boundary(node):
            warnings.append(CompileWarning(
                f"`{_webhook_where(node)}` accepts an inbound request with nothing proving who sent "
                f"it. If it is a webhook, add `verify signature using env.YOUR_WEBHOOK_SECRET`; if it "
                f"is a signed-in user's endpoint, `require role \"...\"`. Under a regulated sector "
                f"this becomes an error.",
                line=getattr(node, 'line', 0) or 0, code="webhook_unverified"))
    return warnings


def scan_webhook_freshness_absent(program):
    """Under a sector, a handler that verifies a signature but declares no freshness window still accepts
    a delivery signed long ago, the first time it arrives. A WARNING, not an
    error: a provider may send no timestamp, so a window cannot always be required. Freshness only
    narrows the window. An exact resend is already refused by its recorded identity (the hash of the
    signed bytes, kept in security_audit_log, T1-WEBHOOK-REPLAY-PROTECTION); using the sender's own
    event id as the identity is T1-WEBHOOK-IDEMPOTENCY, a follow-on."""
    sector = _webhook_active_sector(program)
    if not sector:
        return []
    warnings = []
    for node, has_role, vdecl in _webhook_inbound_handlers(program):
        if vdecl is not None and getattr(vdecl, 'within_seconds', None) is None:
            warnings.append(CompileWarning(
                f"`{_webhook_where(node)}` verifies the signature but sets no freshness window. An exact "
                f"resend is already answered as processed (its identity is kept in security_audit_log), "
                f"but nothing refuses a delivery signed long ago. Add a bound matched to how quickly "
                f"the sender delivers:\n"
                f"        verify signature using env.YOUR_WEBHOOK_SECRET within 5 minutes\n"
                f"(Using the sender's own event id as the identity is a follow-on, "
                f"T1-WEBHOOK-IDEMPOTENCY.)",
                line=getattr(vdecl, 'line', 0) or getattr(node, 'line', 0) or 0,
                code="webhook_freshness_absent"))
    return warnings


def scan_loose_unique_dynamic_target(program):
    """A loose `unique` field whose shape is saved to a RUNTIME-CHOSEN table refuses at check.

    Plain-shape `unique` is enforced by resolving the write target and indexing that table (see
    `resolve_loose_unique`, defined later in this module and resolved at call time). When the target
    is chosen at runtime there is no table to index, so the constraint cannot be honoured -- and a
    `unique` that silently enforces nothing is the exact deception this whole line of work closes.
    Refuse, naming the field and the two ways to fix it.
    """
    _enf, undecidable, _dec = resolve_loose_unique(program)
    errors = []
    for sname, col, line in undecidable:
        errors.append(CompileError(
            f"`unique` on `{col}` in shape `{sname}` cannot be enforced: this shape is saved to a "
            f"table chosen at runtime, so there is no table to put the uniqueness index on. Bind the "
            f"field to a table with `<name> as table` in the shape, or remove `unique`.",
            line=line))
    return errors


_CONNECTION_OP_ATTR = {
    # read verbs -- the connection.table is in `.source`. `get` is DELIBERATELY excluded: it reads a
    # generic dotted source (`cache.settings`, `store.config`) which is a namespace, not a declared
    # database connection, so requiring a `connect` for it would be a false refusal.
    'FindBlock': 'source', 'RetrieveBlock': 'source', 'GrabBlock': 'source',
    'PullBlock': 'source',
    # write verbs -- `.source` for the in-place ones, `.target` for save, `.collection` for modify.
    # SaveAllBlock.source is the collection saved FROM (not a connection), so only its `.target` is
    # read; ConnectDecl.source is the connection STRING, so ConnectDecl is not in this map at all.
    'UpdateBlock': 'source', 'RemoveBlock': 'source', 'RemoveAllBlock': 'source',
    'SaveOrUpdateBlock': 'source', 'SaveBlock': 'target', 'SaveAllBlock': 'target',
    'ModifyBlock': 'collection',
}


def scan_undeclared_connection(program):
    """A data operation against a connection that was never declared is refused HERE, not at run.

    `find rows in other.things` / `save to other.things` / `retrieve x from other.things` all name a
    connection `other`. If nothing declared it (no `connect other as ...`), the runtime cannot resolve
    it and fails mid-request with a database error three layers from the typo -- the exact
    check-clean-then-fail this closes, and the same class `new sh.X` already refuses for an undeclared
    shape. It teaches the fix, and it catches a stranger's first copied example before they run it.

    PRECISE, to never turn a working program into a false refusal: a `HEAD.tail` DOTTED source counts
    when HEAD is not a declared connection, and the DEFAULT connection `db.tail` (a DbRef) counts when
    no `connect db` opened it. `db` is NOT pre-declared: the runtime opens no default connection, so
    `db.things` with no `connect db` fails at run with `no_db_connection` (verified: even `DATABASE_URL`
    set does not auto-open it), which is the same check-clean-then-fail this closes for a named
    connection, and the case a first program hits. A bare one-part name (a held list, a variable) is
    not a connection reference and is untouched; a DECLARED but not-yet-wired non-default name is
    scan_query_connection_name's job, not this one, so this skips it and the two never double-report.
    """
    if not getattr(program, 'served_as_checked', True):
        # A journey or an underscore partial checked on its own: the connection comes from the page
        # that serves it, which is checked with this code included (T1-CHECK-ALL-SPINE-NO-STORE).
        return []
    declared = set()          # `db` is declared ONLY when a `connect db` opens it (collected below)
    errors = []

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
        attr = _CONNECTION_OP_ATTR.get(type(node).__name__)
        if attr is not None:
            src = getattr(node, attr, None)
            parts = getattr(src, 'parts', None)
            # A DottedName with >= 2 parts is `connection.table`; a DbRef (default `db`) has no
            # `.parts`; a one-part name is a held list / variable. Only the first two are connection
            # references.
            if parts and len(parts) >= 2:
                head = str(parts[0])
                if head not in declared:
                    errors.append(CompileError(
                        f"`{head}` is not a declared connection, so `{head}.{parts[1]}` cannot be "
                        f"resolved and this would fail at run with a database error. Declare the "
                        f"connection first, for example `connect {head} as sqlite from "
                        f"env.DATABASE_URL` (or postgres / mysql / mongodb); or use the default "
                        f"connection, `db.{parts[1]}`, with `connect db as ...`.",
                        line=getattr(node, 'line', 0) or 0))
            elif type(src).__name__ == 'DbRef' and 'db' not in declared:
                # THE DEFAULT CONNECTION, same class as the named case above. `db.tail` names the
                # default connection `db`; if no `connect db` opened it, the runtime has no default to
                # reach and fails at run with `no_db_connection`, storing or returning nothing. This is
                # the case a first program hits (nobody's first line declares a connection), so it
                # teaches the same fix the named case does.
                _tbl = getattr(src, 'table', '') or ''
                errors.append(CompileError(
                    f"the default connection `db` is used here (`db.{_tbl}`) but never opened, so this "
                    f"would fail at run with no_db_connection and store or return nothing. Open it "
                    f"first, for example `connect db as sqlite from env.DATABASE_URL default \"app.db\"` (or postgres / "
                    f"mysql / mongodb).",
                    line=getattr(node, 'line', 0) or 0))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    for st in (getattr(program, 'statements', None) or []):
        collect(st)
    for st in (getattr(program, 'statements', None) or []):
        walk(st)
    return errors


_AI_PROVIDER_DISPLAY = "anthropic, openai, gemini, groq, kimi, qwen, deepseek, perplexity, grok"


def scan_ai_connect_valid(program):
    """ai.connect must declare something, and name only known providers with explicit models.

    - Defect 1: a block with no address and no order registers nothing (a silent no-op) -- refuse.
    - Defect 2: an order names a provider that is neither built-in nor coder-declared -- refuse.
    - HOLD: a provider named with no model -- refuse (Mohio keeps no per-provider default model).
    """
    errors = []
    try:
        from mohio_runtime_vocab import builtin_provider_names
        known = builtin_provider_names()
    except Exception:
        known = set(_AI_PROVIDER_DISPLAY.replace(",", " ").split())

    def walk(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                walk(c)
            return
        if not is_dataclass(node):
            return
        if type(node).__name__ == 'AiConnectBlock':
            line = getattr(node, 'line', 0) or 0
            declared = getattr(node, 'declared', None) or []
            providers = getattr(node, 'providers', None) or []
            declared_names = {str(d.get('name', '')).strip() for d in declared if d.get('name')}
            names = getattr(node, 'names', None) or []
            if not declared and not providers:
                nm = names[0] if names else 'answer'
                errors.append(CompileError(
                    f"`ai.connect {names[0] if names else ''}` declares a connection with nothing in "
                    f"it, so it registers no provider and no AI call is ever made through it.\n"
                    f"Give it either an address (one provider) or an order (a fallback chain):\n"
                    f"    ai.connect {nm}\n        address \"https://api.groq.com/openai/v1\"\n"
                    f"        key env.GROQ_API_KEY\n    ai.connect: done", line=line))
            for p in providers:
                prov = str(p.get('provider', '') or '').strip()
                if not prov:
                    continue
                if prov not in known and prov not in declared_names:
                    errors.append(CompileError(
                        f"`{prov}` is not a recognized AI provider, so the chain cannot resolve it "
                        f"and would fail at run.\nKnown providers: {_AI_PROVIDER_DISPLAY}.\n"
                        f"For any other provider, declare it with its own address and key, then name "
                        f"it in the order:\n    ai.connect answer\n        address "
                        f"\"https://your-host/v1\" key env.YOUR_API_KEY as {prov}\n        order\n"
                        f"            {prov} model \"your-model\"\n        order: done\n"
                        f"    ai.connect: done", line=line))
                elif not p.get('model'):
                    errors.append(CompileError(
                        f"provider `{prov}` is named with no model. Mohio does not keep a per-provider "
                        f"default model, because models change too often to pin one in the compiler.\n"
                        f"Name the exact model string the provider documents:\n    order\n"
                        f"        {prov} model \"gpt-4o\"\n    order: done", line=line))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    for st in (getattr(program, 'statements', None) or []):
        walk(st)
    return errors


def resolve_declared_col_types(program):
    """{table: {field: declared_type_lowercased}}, built STATICALLY from `shape ... as table`
    declarations -- the check-time twin of the interpreter's own registration
    (`MohioInterpreter._exec_ShapeDecl` builds the identical dict at run, same `.lower()`), so
    `text.long` misuse (see scan_text_long_query_misuse) can be refused at `mio check`, before
    any table is ever connected to.
    """
    from mohio_ast import ShapeDecl as _SD

    def _walk(n, out):
        if n is None or isinstance(n, (str, int, float, bool)):
            return
        if isinstance(n, (list, tuple)):
            for c in n:
                _walk(c, out)
            return
        if not is_dataclass(n):
            return
        if isinstance(n, _SD):
            out.append(n)
        for f in fields(n):
            _walk(getattr(n, f.name, None), out)

    shapes = []
    _walk(getattr(program, 'statements', None) or [], shapes)

    col_types = {}
    for sd in shapes:
        for tname, tbl in (getattr(sd, 'tables', None) or {}).items():
            types = col_types.setdefault(str(tname), {})
            for f in (getattr(tbl, 'fields', None) or []):
                fname = getattr(f, 'name', None)
                tn = getattr(f, 'type_name', None)
                if fname and tn:
                    types[str(fname)] = str(tn).lower()
    return col_types


def scan_text_long_unique(program):
    """`unique` on a `text.long` field refuses at check -- as ruled: an unbounded-length column
    cannot carry a database uniqueness index the way a bounded `text` column can (every engine's
    own unique-index mechanism needs a comparable, bounded key). Checked directly on the field
    declaration, independent of table resolution: the combination is invalid regardless of
    whether the governing table is otherwise decidable.
    """
    from mohio_ast import ShapeDecl as _SD, ShapeField as _SF

    def _walk(n, out):
        if n is None or isinstance(n, (str, int, float, bool)):
            return
        if isinstance(n, (list, tuple)):
            for c in n:
                _walk(c, out)
            return
        if not is_dataclass(n):
            return
        if isinstance(n, _SD):
            out.append(n)
        for f in fields(n):
            _walk(getattr(n, f.name, None), out)

    shapes = []
    _walk(getattr(program, 'statements', None) or [], shapes)

    errors = []
    for sd in shapes:
        all_fields = list(getattr(sd, 'fields', None) or [])
        for tbl in (getattr(sd, 'tables', None) or {}).values():
            all_fields += list(getattr(tbl, 'fields', None) or [])
        for f in all_fields:
            if not isinstance(f, _SF):
                continue
            if str(getattr(f, 'type_name', '') or '').lower() != 'text.long':
                continue
            if any(str(getattr(m, 'modifier_type', '') or '') == 'unique'
                   for m in (getattr(f, 'modifiers', None) or [])):
                errors.append(CompileError(
                    f"`{f.name}` is `text.long` and `unique`: an unlimited-length field cannot "
                    f"carry a uniqueness index. Drop `unique`, or change the field to bounded "
                    f"`text` if duplicates genuinely must be refused.",
                    line=getattr(f, 'line', 0) or 0))
    return errors


def scan_text_long_query_misuse(program):
    """A `text.long` field refuses at check when used for an exact match or as a sort key --
    as ruled. Neither operation is something an unlimited-length column can do the way a bounded
    `text` column does: an exact-match comparison on a CLOB/LONGTEXT value is not indexable the
    same way, and Mohio's own sort is a human-collation comparison with no SQL equivalent for an
    unbounded value (the same reason PUSHDOWN UNIT 5 never pushes a text order key at all).

    STATIC AND CONSERVATIVE, same posture as resolve_join_fields: only a `find`/`retrieve` whose
    source is a LITERAL `db.<table>` reference is checked. A dynamically chosen source is
    undecidable here and is left for the runtime to deal with it as it fetches.
    """
    from mohio_ast import (FindBlock as _FB, RetrieveBlock as _RB, WhereClause as _WC,
                           AndClause as _AC, OrderClause as _OC, MatchClause as _MC,
                           DbRef as _DbRef, DottedName as _DN)
    from mohio_runtime_vocab import split_table_ref_parts

    col_types = resolve_declared_col_types(program)

    def _walk(n, out):
        if n is None or isinstance(n, (str, int, float, bool)):
            return
        if isinstance(n, (list, tuple)):
            for c in n:
                _walk(c, out)
            return
        if not is_dataclass(n):
            return
        if isinstance(n, (_FB, _RB)):
            out.append(n)
        for f in fields(n):
            _walk(getattr(n, f.name, None), out)

    blocks = []
    _walk(getattr(program, 'statements', None) or [], blocks)

    errors = []
    for blk in blocks:
        src = getattr(blk, 'source', None)
        if isinstance(src, _DbRef):
            raw = src.table
        elif isinstance(src, _DN):
            raw = '.'.join(str(p) for p in (src.parts or []))
        else:
            continue  # a dynamic/computed source -- undecidable, not checked here
        table, column = split_table_ref_parts(raw)
        if not table or column:
            continue
        types = col_types.get(str(table), {})
        for clause in (getattr(blk, 'body', None) or []):
            if isinstance(clause, (_WC, _AC)):
                fname = str(getattr(clause, 'field', '') or '').split('.')[-1]
                if types.get(fname) == 'text.long' and getattr(clause, 'condition', '') in ('is', '==', ''):
                    errors.append(CompileError(
                        f"`{fname}` is `text.long`: an exact-match comparison (`is`) on an "
                        f"unlimited-length field is refused. Use a `contains`/`starts.with` "
                        f"text op instead, or change the field to bounded `text` if exact "
                        f"matching is genuinely needed.",
                        line=getattr(clause, 'line', 0) or 0))
            elif isinstance(clause, _MC):
                fname = str(getattr(clause, 'field', '') or '').split('.')[-1]
                if types.get(fname) == 'text.long':
                    errors.append(CompileError(
                        f"`{fname}` is `text.long`: `match` is always an exact-match comparison, "
                        f"refused on an unlimited-length field. Use a `where ... contains` text op "
                        f"instead, or change the field to bounded `text` if exact matching is "
                        f"genuinely needed.",
                        line=getattr(clause, 'line', 0) or 0))
            elif isinstance(clause, _OC):
                fname = str(getattr(clause, 'field', '') or '').split('.')[-1]
                if types.get(fname) == 'text.long':
                    errors.append(CompileError(
                        f"`{fname}` is `text.long`: it cannot be used as a sort key. Mohio's sort "
                        f"is a human-collation comparison with no SQL equivalent for an unlimited-"
                        f"length value (the same reason order-by never pushes to the database for "
                        f"a text column at all). Sort by a bounded field instead.",
                        line=getattr(clause, 'line', 0) or 0))
    return errors



# ── T1-MIOCHECK-UNDECLARED-NAME: a dotted read whose root is declared nowhere ─────────────────
# RULED by Ron, 2026-10-05. `user_intent intent.intent` in a handler where nothing called `intent`
# exists parsed, checked clean, and failed only when the line ran ("not declared"). The runtime
# already refuses it; this says so at check, where the information exists.
#
# CONSERVATIVE BY CONSTRUCTION, so it cannot refuse working code: a root counts as declared if
# ANY statement anywhere in the program introduces that name (an assignment, hold, lock, create,
# a query's result, a loop item, a task and its parameters, a shape field, an `as NAME` capture,
# a handler's shape variable), or the runtime binds it itself. It is not scope-precise: a name
# declared in one handler and read in another passes. A missed refusal still fails at run.
_RUNTIME_BOUND_ROOTS = frozenset({
    "request", "session", "failure", "error", "errors", "it", "value", "random",
    "flow_trace", "walk_report", "comparison", "canonical", "endpoint", "stage", "request_id",
    "flowed", "walked", "page",
})
_DECL_FIELDS = ("name", "alias", "as_name", "item", "result", "result_name", "target", "var", "var_name",
                "item_name", "capture", "into", "shape_var")


def scan_undeclared_dotted_roots(program):
    from mohio_ast import DottedName, Literal, TOP_HANDLER_TYPES
    declared = set()
    reads = []
    seen = set()
    old_names = {}           # the retired lowercase-first name -> the shape it meant
    bare_reads = []          # one-part reads, reported only when they are a retired name

    def visit(node, line=0, in_handler=False, invoked_by=False, scope=frozenset()):
        if node is None or isinstance(node, (str, int, float, bool)) or id(node) in seen:
            return
        if isinstance(node, (list, tuple)):
            for x in node:
                visit(x, line, in_handler, invoked_by, scope)
            return
        if not is_dataclass(node):
            return
        seen.add(id(node))
        line = getattr(node, "line", 0) or line          # the nearest statement's line
        in_handler = in_handler or isinstance(node, TOP_HANDLER_TYPES)
        # The shapes whose request data a read here can reach: the handler's own, or for a top-level
        # declaration, the shapes of the handlers that invoke it.
        if isinstance(node, TOP_HANDLER_TYPES) and isinstance(getattr(node, 'shape', None), str):
            scope = frozenset([node.shape])
        if isinstance(node, DottedName):
            parts = [str(p) for p in (node.parts or [])]
            # ONLY INSIDE A HANDLER (narrowed as ruled: zero new refusals on any doc example or
            # gate snippet). A top-level line in an excerpt often reads a name its surrounding
            # program would declare; inside a handler, what is in scope is the program plus the
            # request, which is where `user_intent intent.intent` was written.
            # A top-level task or AI block a handler invokes runs in that handler's scope, so it
            # is read the same way (RULED by Ron 2026-10-10: the transformed name refuses there too).
            if len(parts) >= 2 and (in_handler or invoked_by):
                reads.append((parts[0], ".".join(parts), line, bool(invoked_by) and not in_handler, scope))
            elif len(parts) == 1 and (in_handler or invoked_by):
                # A WHOLE-VALUE READ (`give back [200] pay`), kept only to name a retired name below:
                # any other undeclared bare word is another scan's business.
                bare_reads.append((parts[0], parts[0], line, bool(invoked_by) and not in_handler, scope))
            return
        if isinstance(node, Literal):
            return
        for fname in _DECL_FIELDS:
            v = getattr(node, fname, None)
            if isinstance(v, str) and v:
                declared.add(v)
                declared.add(v.split(".")[0])
        shp = getattr(node, "shape", None)
        if isinstance(shp, str) and shp:
            declared.add(shp)                          # a handler's request data, by its declared name
            if shp[0].lower() + shp[1:] != shp:
                old_names[shp[0].lower() + shp[1:]] = shp
        for f in fields(node):
            visit(getattr(node, f.name, None), line, in_handler, invoked_by, scope)

    _invoked = handler_invoked_shapes(program)
    _stmts = getattr(program, "statements", program)
    for _st in (_stmts if isinstance(_stmts, (list, tuple)) else [_stmts]):
        _by = _invoked.get(str(getattr(_st, "name", "") or "")) or frozenset()
        visit(_st, invoked_by=bool(_by), scope=_by)
    errors = []
    reported = set()
    # A whole-value read is kept when it is a retired name, or another spelling of a shape whose
    # request this line can read (`give back [200] staff_status` in sh.StaffStatus's handler): that
    # checked clean and answered 500 undeclared_variable at run.
    reads = reads + [r for r in bare_reads if r[0] in old_names
                     or any(s != r[0] and spelling_key(s) == spelling_key(r[0]) for s in r[4])]
    for root, full, line, outside_handler, scope in reads:
        if root in declared or root in _RUNTIME_BOUND_ROOTS or root.startswith("mio"):
            continue
        if (root, line) in reported:
            continue
        reported.add((root, line))
        if root == "sh":
            continue         # `sh.X.field`: scan_shape_ref_field says what it is
        if full == "no.symbol":
            continue         # a misplaced field modifier: scan_two_statements_one_line names it
        if root in old_names:
            # THE RETIRED NAME (RULED by Ron 2026-10-10, his wording): the request data is read by
            # the shape's name exactly as declared. `staffStatus` for sh.StaffStatus was a
            # transformation of a name, written nowhere in the program.
            shp = old_names[root]
            errors.append(CompileError(
                (f"Line {line}: " if line else "")
                + f"`{shp}` and `{root}`: are these two different variables or a typo? Make sure "
                  f"they match, or choose a different variable name.",
                line,
                f"A request for sh.{shp} is read by the shape's name exactly as declared: "
                f"`{shp}{full[len(root):]}`. "
                + ("This line is in a declaration a handler calls, so change it by hand."
                   if outside_handler else "`mio fmt --write` rewrites every one of these for you."),
                "undeclared_name",
            ))
            continue
        hint = (f"Declare `{root}` first (`{root} <value>`, or name a query's result `{root}`), or "
                f"check the spelling. Inside a handler for sh.<Shape>, the request's own fields are "
                f"read by the shape's name exactly as declared, `<Shape>.<field>`.")
        # A REQUEST NAME SPELLED ANOTHER WAY (found 2026-10-09 in the Dispatch app): a request for
        # sh.StaffStatus is read as `staffStatus`, and `staff_status` said only that nothing was
        # called that. Name the spelling that works.
        # Only a shape whose request data THIS read can reach: a name in another handler's request
        # is not readable here, so naming it would send the coder to a line that refuses too.
        near = sorted(s for s in scope if spelling_key(s) == spelling_key(root))
        rest = full[len(root):]
        if len(near) == 1:
            hint = (f"Did you mean `{near[0]}{rest}`? A request for sh.{near[0]} is read by the "
                    f"shape's name exactly as declared, `{near[0]}`.")
        elif near:
            hint = (f"Did you mean one of " + ", ".join(f"`{s}{rest}`" for s in near) + "? Each is "
                    f"a request this line can read, by the shape's name exactly as declared.")
        errors.append(CompileError(
            (f"Line {line}: " if line else "") +
            (f"`{full}` reads from `{root}`, but nothing in this program is called `{root}`, so "
             f"this line fails when it runs." if full != root else
             f"nothing in this program is called `{root}`, so this line fails when it runs."),
            line,
            hint,
            "undeclared_name",
        ))
    return errors


def scan_shape_name_collision(program):
    """In a `listen for sh.X` / `request for sh.X` handler, `X` is the request data (RULED by Ron
    2026-10-09). A variable, task or map also called `X` would give that one name two meanings in the
    handler, so it refuses at check, naming both."""
    from mohio_ast import NewBlock, RequestInboundBlock
    errors = []
    stmts = getattr(program, 'statements', None) or []

    def nodes(root):
        stack = [root]
        while stack:
            n = stack.pop()
            if n is None or isinstance(n, (str, int, float, bool)):
                continue
            if isinstance(n, (list, tuple)):
                stack.extend(n)
                continue
            if not is_dataclass(n):
                continue
            yield n
            for f in fields(n):
                stack.append(getattr(n, f.name, None))

    program_wide = {}
    for n in nodes(stmts):
        k = type(n).__name__
        if k == 'TaskDecl' and getattr(n, 'name', None):
            program_wide[str(n.name)] = ('a task', getattr(n, 'line', 0) or 0)
        elif k in ('MiomapDecl', 'MapDecl') and getattr(n, 'name', None):
            program_wide[str(n.name)] = ('a map', getattr(n, 'line', 0) or 0)
    for h in nodes(stmts):
        if not isinstance(h, (NewBlock, RequestInboundBlock)) or not getattr(h, 'shape', None):
            continue
        shape = str(h.shape)
        hline = getattr(h, 'line', 0) or 0
        clash = None
        if shape in program_wide:
            clash = program_wide[shape]
        else:
            for n in nodes(getattr(h, 'body', None) or []):
                k = type(n).__name__
                named = [getattr(n, 'name', None)] if k in ('Assignment', 'HoldDecl', 'LockDecl', 'RetrieveBlock',
                                                             'FindBlock', 'CreateBlock') else []
                named.append(getattr(n, 'alias', None) if k in ('RetrieveBlock', 'FindBlock', 'RunBlock') else None)
                if shape in [str(x) for x in named if x]:
                    clash = ('a variable', getattr(n, 'line', 0) or 0)
                    break
        if clash:
            what, line = clash
            errors.append(CompileError(
                f"Line {line}: {what} here is called `{shape}`, and in the handler on line {hline} `{shape}` "
                f"is the request data for sh.{shape}, so one name would mean two things.\n"
                f"    Give the {what.split()[-1]} its own name, for example `{shape}_record`, and read the "
                f"request as `{shape}.<field>`.", line=line))
    return errors


def _all_nodes(root):
    """Every dataclass node under `root`, depth first."""
    stack = [root]
    while stack:
        n = stack.pop()
        if n is None or isinstance(n, (str, int, float, bool)):
            continue
        if isinstance(n, (list, tuple)):
            stack.extend(n)
            continue
        if not is_dataclass(n):
            continue
        yield n
        for f in fields(n):
            stack.append(getattr(n, f.name, None))


_STATEMENT_LISTS = ('body', 'statements', 'else_body', 'otherwise_body', 'then_body')


def scan_two_statements_one_line(program):
    """One action per line: two statements read from ONE line refuse (found 2026-10-10).

    `give back [200] Totals.qty 5` checked clean. Nothing joins `5` to the value, so the parser read
    the line as a status-only `give back [200]` and then a second statement, `Totals.qty 5`, and the
    request answered an empty 200 while the line looked like it answered the quantity. (A declared
    name is marked by the pretokenizer and that spelling fails the parse instead; an undeclared head
    reaches this.) Measured before landing: no two sibling statements share a line anywhere in the
    corpus, Zork included."""
    from mohio_ast import DottedName
    errors = []

    def first_words(n):
        k = type(n).__name__
        if k == 'GiveBackStmt':
            return 'give back' + (f" [{n.status}]" if getattr(n, 'status', None) else '')
        if k == 'ServiceCallStmt':
            return f"{n.service}.{n.method} ..."
        return k

    def look(stmts):
        stmts = [s for s in stmts if is_dataclass(s)]
        for a, b in zip(stmts, stmts[1:]):
            la, lb = getattr(a, 'line', 0) or 0, getattr(b, 'line', 0) or 0
            if not la or la != lb:
                continue
            arg = getattr(b, 'args', None)
            if isinstance(arg, DottedName) and [str(x) for x in (arg.parts or [])] == ['no', 'symbol']:
                errors.append(CompileError(
                    f"Line {la}: no.symbol is a field modifier, and here it follows a value. Move it "
                    f"to the money field itself, in its shape:  amount as USD no.symbol", line=la))
                continue
            errors.append(CompileError(
                f"Line {la}: this line was read as two statements, `{first_words(a)}` and then "
                f"`{first_words(b)}`, because nothing joins the second part to the first.\n"
                f"    Mohio reads one action per line. Put each on its own line, or, if it is one "
                f"value, group it:  give back [200] (<value>)", line=la))

    # BLOCK BODIES ONLY. The top level is assembled from several files (a journey's statements are
    # put in front of the page's, an include's beside them), each carrying its own file's line
    # numbers, so two top-level statements can share a number without sharing a line. Measured:
    # a journey's `connect` on its line 1 beside the page's line-1 `shape`.
    for n in _all_nodes(getattr(program, 'statements', None) or []):
        for f in fields(n):
            v = getattr(n, f.name, None)
            if f.name in _STATEMENT_LISTS and isinstance(v, list):
                look(v)
    return errors


def scan_paginate_without_page_size(program):
    """`paginate by` with no `up to` refuses (T1-PAGINATE-WITHOUT-PAGE-SIZE, RULED by Ron 2026-10-23).
    `paginate by N` names the PAGE; `up to N` is the page's size, and without one the page number was
    worked out and dropped, so the query returned every row and `found.page.more` then refused at run.
    The pioneer manual's `paginate by 25` alone read as 25 per page and returned the whole table."""
    errors = []
    for n in _all_nodes(getattr(program, 'statements', None) or []):
        if type(n).__name__ not in ('FindBlock', 'RetrieveBlock', 'GrabBlock'):
            continue
        body = getattr(n, 'body', None) or []
        page = next((b for b in body if type(b).__name__ == 'PaginateClause'), None)
        if page is None or any(type(b).__name__ == 'LimitClause' for b in body):
            continue
        ln = getattr(page, 'line', 0) or getattr(n, 'line', 0) or 0
        errors.append(CompileError(
            f"Line {ln}: `paginate by` picks which page to return, and this query never says how big a "
            f"page is, so it would return every row. Add `up to` with the page size:\n"
            f"        up to 20\n        paginate by request.page default 1",
            line=ln, code="paginate_without_page_size"))
    return errors


def scan_connect_name_twice(program):
    """Two connections of one name that point in different places refuse (T1-CONNECT-DB-LAST-WINS, RULED
    by Ron 2026-10-23 for 6.0.1). `connect db ... from env.DB1` then `connect db ... from env.DB2` sent
    every write to the second database without a word. Compared within one statement list (the top
    level, which includes a folder journey's own lines, or one block body), so a connection chosen in
    one branch and another in a different branch is not this. The same connection written twice, as a
    page repeating its journey's line word for word, names one database and is left alone."""
    errors = []

    def where(n):
        ln = getattr(n, 'line', 0) or 0
        f = getattr(ln, 'file', None)
        return f"line {ln}" + (f" of {str(f).replace(chr(92), '/').rsplit('/', 1)[-1]}" if f else "")

    def what(n):
        return (str(getattr(n, 'driver', '') or '').lower(), repr(getattr(n, 'source', None)),
                str(getattr(n, 'access', '') or '').lower())

    def look(stmts):
        first = {}
        for n in stmts:
            if type(n).__name__ != 'ConnectDecl':
                continue
            name = str(getattr(n, 'name', '') or '')
            if name not in first:
                first[name] = n
                continue
            a = first[name]
            if what(a) == what(n):
                continue
            ln = getattr(n, 'line', 0) or 0
            errors.append(CompileError(
                f"Line {ln}: `{name}` is connected twice, on {where(a)} and {where(n)}, to different places. A "
                f"name holds one connection, so every read and write through `{name}` would go to the second "
                f"one and the first would be ignored without a word. Keep one, or give the second its own "
                f"name:\n        connect {name} ...\n        connect reports ...",
                line=ln, code="connect_name_twice"))

    stmts = getattr(program, 'statements', None) or []
    look(stmts)
    for n in _all_nodes(stmts):
        for f in fields(n):
            v = getattr(n, f.name, None)
            if f.name in _STATEMENT_LISTS and isinstance(v, list):
                look(v)
    return errors


def scan_shape_name_start(program):
    """A shape name begins with a letter (RULED by Ron 2026-10-10). `shape _Hidden` declared a shape
    no `sh.` reference could reach. A leading `-` never parses; the syntax-error explainer says the
    same thing for it (shape_name_start_message)."""
    from mohio_ast import shape_name_start_message
    errors = []
    for n in _all_nodes(getattr(program, 'statements', None) or []):
        if type(n).__name__ == 'ShapeDecl':
            nm = str(getattr(n, 'name', '') or '')
            if nm[:1] in ('_', '-'):
                ln = getattr(n, 'line', 0) or 0
                errors.append(CompileError(shape_name_start_message(ln, nm), line=ln))
    return errors


def scan_shape_ref_field(program):
    """`sh.Order` is the shape itself, so `sh.Order.qty` reads nothing (RULED by Ron 2026-10-10). The
    value arrives in a handler and is read by the shape's name: `Order.qty`."""
    errors = []
    seen = set()
    for n in _all_nodes(getattr(program, 'statements', None) or []):
        if type(n).__name__ != 'DottedName':
            continue
        parts = [str(x) for x in (getattr(n, 'parts', None) or [])]
        if len(parts) >= 3 and parts[0] == 'sh':
            ln = getattr(n, 'line', 0) or 0
            if (ln, parts[1]) in seen:
                continue
            seen.add((ln, parts[1]))
            errors.append(CompileError(
                f"Line {ln}: sh.{parts[1]} is the shape itself; read the value as "
                f"{parts[1]}.{'.'.join(parts[2:])}.\n"
                f"    Inside `listen for sh.{parts[1]}` or `request for sh.{parts[1]}`, the data that "
                f"arrived is `{parts[1]}`, and `sh.{parts[1]}` is the blueprint it was checked against.",
                line=ln))
    return errors


def scan_shape_field_assign(program):
    """Inside a handler for sh.X, `X.field <value>` assigns to the request data, and the value is held
    to the type the shape declares (RULED by Ron 2026-10-10). A literal of the wrong type, or a field
    the shape does not declare, refuses here; a computed value is checked when it runs."""
    from mohio_ast import NewBlock, RequestInboundBlock, Literal
    from mohio_runtime_vocab import value_matches_type
    stmts = getattr(program, 'statements', None) or []
    shapes = {}
    for n in _all_nodes(stmts):
        if type(n).__name__ == 'ShapeDecl' and getattr(n, 'name', None):
            shapes[str(n.name)] = {str(f.name): (getattr(f, 'type_name', None) or 'text')  # SILENT-OK: an untyped field is text, as the runtime reads it
                                   for f in (getattr(n, 'fields', None) or []) if getattr(f, 'name', None)}
    errors = []
    for h in _all_nodes(stmts):
        if not isinstance(h, (NewBlock, RequestInboundBlock)) or str(getattr(h, 'shape', '')) not in shapes:
            continue
        shape = str(h.shape)
        declared = shapes[shape]
        for n in _all_nodes(getattr(h, 'body', None) or []):
            if type(n).__name__ != 'ServiceCallStmt' or str(getattr(n, 'service', '')) != shape:
                continue
            ln = getattr(n, 'line', 0) or 0
            fld = str(getattr(n, 'method', '') or '')
            if fld not in declared:
                have = ", ".join(sorted(declared)) or "no fields yet"  # SILENT-OK: message text for a shape with no fields
                errors.append(CompileError(
                    f"Line {ln}: `{shape}.{fld}` is not a field of sh.{shape}, so there is nothing to "
                    f"assign it to. Use one of the fields sh.{shape} declares: {have}.", line=ln))
                continue
            arg = getattr(n, 'args', None)
            ftype = declared[fld]
            if isinstance(arg, Literal) and not value_matches_type(arg.value, ftype):
                what = ('text' if isinstance(arg.value, str) else 'a boolean' if isinstance(arg.value, bool)
                        else 'a number')
                errors.append(CompileError(
                    f"Line {ln}: `{shape}.{fld}` is declared as {ftype} in sh.{shape}, and "
                    f"{arg.value!r} is {what}. The shape's type is a contract on the field.\n"
                    f"    Assign a {ftype} value, for example `{shape}.{fld} <a {ftype}>`, or cast it "
                    f"with `(... as.{str(ftype).split('.')[0]})`.", line=ln))
    return errors


def scan_plus_on_text(program):
    """`+` with text on either side refuses at check, teaching `&` (RULED by Ron). `+` adds
    numbers; Mohio joins text with `&`. The run already refused it ("Cannot do math on text"), but
    only when the line ran, so a branch nobody tried shipped a crash. A `+` chain with a string
    literal in it is certainly text, so it refuses here; a chain of names alone is left to the run,
    which knows the values. `mio fmt --write` rewrites a `+` that sits beside a string literal."""
    from mohio_ast import MathExpr, Literal
    errors = []

    def is_plus(n):
        return isinstance(n, MathExpr) and str(getattr(n, 'op', '')).strip() == '+'

    def terms(n):
        if is_plus(n):
            return terms(n.left) + terms(n.right)
        return [n]

    def shown(n):
        if isinstance(n, Literal):
            return '"%s"' % n.value if isinstance(n.value, str) else str(n.value)
        parts = getattr(n, 'parts', None)
        if parts:
            return '.'.join(str(p) for p in parts).replace('__USERVAR__', '')
        return '...'

    seen = set()
    for n in _all_nodes(getattr(program, 'statements', None) or []):
        if not is_plus(n) or id(n) in seen:
            continue
        ts = terms(n)
        # The whole chain is one expression: an inner `+` of it is not reported again.
        stack = [n]
        while stack:
            m = stack.pop()
            if is_plus(m):
                seen.add(id(m))
                stack += [m.left, m.right]
        if not any(isinstance(t, Literal) and isinstance(t.value, str) for t in ts):
            continue
        ln = getattr(n, 'line', 0) or 0
        joined = " & ".join(shown(t) for t in ts)
        errors.append(CompileError(
            f"Line {ln}: `+` adds numbers, and this joins text. Mohio joins text with `&`.\n"
            f"    Write it as `({joined})` (a number beside `&` joins as its text). "
            f"`mio fmt --write` rewrites this line for you.", line=ln))
    return errors


def _classified_field_names(program):
    """{field name: tag} for every shape field tagged [phi], [pci] or [pii]. Tags apply by field
    name across the program (the runtime resolver's own rule), so a name is enough to know a value
    is protected wherever that name is written."""
    out = {}

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
                    for part in [x.strip().lower() for x in str(getattr(m, 'value', '') or '').split(',')]:
                        if part in ('phi', 'pci', 'pii') and getattr(fld, 'name', None):
                            out.setdefault(str(fld.name), part)
        for f in fields(node):
            walk(getattr(node, f.name, None))

    walk(program)
    return out


_RAW_SQL_WRITE_WORDS = ('insert', 'update', 'delete', 'merge', 'replace', 'upsert')


# A raw statement that writes or reshapes a table, and the table it names: UPDATE [ONLY] t,
# DELETE [alias] FROM t, INSERT [OR x] INTO t, REPLACE INTO t, MERGE INTO t, DROP TABLE [IF EXISTS] t,
# ALTER TABLE [ONLY] t, TRUNCATE [TABLE] t. A name may be quoted (`"t"`, `[t]`, a backticked t) or
# schema-qualified; the last part is the table. An alias after the name changes nothing here.
_RAW_SQL_TARGET = re.compile(
    r'(?is)\b(update(?:\s+only)?|delete\s+(?:[\w"`\[\]]+\s+)?from|insert\s+(?:or\s+\w+\s+)?into|'
    r'replace\s+into|merge\s+into|drop\s+table(?:\s+if\s+exists)?|alter\s+table(?:\s+only)?|'
    r'truncate(?:\s+table)?)\s+((?:[`"\[]?[A-Za-z_][\w$]*[`"\]]?\s*\.\s*)*[`"\[]?[A-Za-z_][\w$]*[`"\]]?)')


def raw_sql_audit_targets(sql_text):
    """[(verb, table)] for every statement in `sql_text` that writes or reshapes an AUDIT RELATION.
    Comments are removed first, so a commented-out statement is not refused and a comment cannot
    hide one. Shared by the check scan and the runtime refusal (audit append-only, 2026-10-13); the
    in-database refusal is the backstop for any form this cannot read."""
    from mohio_audit_grades import is_audit_table
    text = re.sub(r'/\*.*?\*/', ' ', str(sql_text or ''), flags=re.S)
    text = re.sub(r'--[^\n]*', ' ', text)
    hits = []
    for m in _RAW_SQL_TARGET.finditer(text):
        name = re.split(r'\s*\.\s*', m.group(2))[-1].strip('`"[]')
        if is_audit_table(name):
            verb = ' '.join(m.group(1).split()).upper()
            hits.append(('DELETE FROM' if verb.startswith('DELETE') else verb, name))
    return hits


def scan_raw_sql_writes_audit_relation(program):
    """A raw `sql` statement that writes or reshapes an audit relation refuses at check (audit
    append-only, Ron 2026-10-13). The runtime refuses the same statement in _exec_SqlBlock."""
    from mohio_audit_grades import audit_append_only_refusal, AUDIT_APPEND_ONLY_FIX
    errors = []
    for n in _all_nodes(program):
        if type(n).__name__ != 'SqlBlock':
            continue
        line = getattr(n, 'line', 0) or 0
        for verb, table in raw_sql_audit_targets(getattr(n, 'sql', '')):
            errors.append(CompileError(
                f"Line {line}: " + audit_append_only_refusal(table, 'sql ' + verb),
                line=line, hint=AUDIT_APPEND_ONLY_FIX))
    return errors


_AUDIT_VERB_NODES = {'SaveBlock': 'save', 'SaveAllBlock': 'save.all', 'UpdateBlock': 'update',
                     'SaveOrUpdateBlock': 'save or update', 'ModifyBlock': 'modify',
                     'RemoveBlock': 'remove', 'RemoveAllBlock': 'remove.all', 'CmPurgeBlock': 'cm.purge',
                     'AiResolveBlock': 'ai.resolve learned'}


def scan_write_verb_on_audit_relation(program):
    """A data verb whose target is written in the source refuses at check when it names an audit
    relation (audit append-only, Ron 2026-10-13): `remove.all from db.data_audit_log` emptied the
    trail and verify reported 0 entries with the chain intact. The runtime guard refuses the same
    write when the target is only known at run."""
    from mohio_audit_grades import audit_append_only_refusal, AUDIT_APPEND_ONLY_FIX
    errors = []
    for n in _all_nodes(program):
        verb = _AUDIT_VERB_NODES.get(type(n).__name__)
        if not verb:
            continue
        for attr in ('target', 'source', 'collection', 'learned_ref'):
            ref = getattr(n, attr, None)
            if ref is None:
                continue
            if isinstance(ref, str):                     # save or update carries `db.t` as text
                parts = ref.split('.')
            else:
                parts = [str(p) for p in (getattr(ref, 'parts', None) or [])]
            table = getattr(ref, 'table', None) or (parts[-1] if len(parts) >= 2 and parts[0] == 'db'
                                                     else None)
            msg = audit_append_only_refusal(str(table), verb) if table else None
            if msg:
                line = getattr(n, 'line', 0) or 0
                errors.append(CompileError(f"Line {line}: " + msg, line=line,
                                           hint=AUDIT_APPEND_ONLY_FIX))
                break
    return errors


def _raw_sql_classified_hit(sql_text, tagged):
    """(statement, field, tag) for the first statement of `sql_text` that WRITES and names a
    classified field as a word, or None. Shared by the check scan and the runtime refusal."""
    for stmt in [x.strip() for x in str(sql_text or '').split(';') if x.strip()]:
        first = stmt.split(None, 1)[0].lower()
        if first not in _RAW_SQL_WRITE_WORDS:
            continue
        for name in sorted(tagged):
            if re.search(r'(?<![A-Za-z0-9_])' + re.escape(name) + r'(?![A-Za-z0-9_])', stmt, re.I):
                return stmt, name, tagged[name]
    return None


def _raw_sql_classified_message(line, stmt, name, tag):
    m = re.search(r'(?i)\b(?:into|update)\s+["`]?([A-Za-z_][A-Za-z0-9_]*)', stmt)
    table = m.group(1) if m else '<table>'
    return (f"Line {line}: this raw `sql` block writes `{name}`, a field tagged [{tag}]. Raw SQL "
            f"goes to the database exactly as written, so Mohio cannot encrypt the value or give it "
            f"a blind index: it would sit in plain text beside the rows `save` sealed. Write it with "
            f"the data verb instead, which seals it:\n"
            f"    save to db.{table}\n"
            f"        {name} <value>\n"
            f"    save: done")


def scan_raw_sql_writes_classified_field(program):
    """A raw `sql` write naming a [phi]/[pci]/[pii] field refuses (classification sweep,
    2026-10-06): measured, `INSERT INTO t (ssn) VALUES ('...')` stored the value in plain text,
    silently, beside the encrypted rows `save` wrote. The runtime refuses the same statement in
    _exec_SqlBlock, for SQL this scan cannot see (an interpolated statement)."""
    tagged = _classified_field_names(program)
    if not tagged:
        return []
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
        if type(node).__name__ == 'SqlBlock':
            hit = _raw_sql_classified_hit(getattr(node, 'sql', ''), tagged)
            if hit:
                line = getattr(node, 'line', 0) or 0
                errors.append(CompileError(_raw_sql_classified_message(line, *hit), line=line))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    walk(program)
    return errors


_TEMPLATE_REF = re.compile(r"\{\{\s*([A-Za-z_][\w.]*)\s*\}\}")


def _protected_value_tracker(program, tagged):
    """Follow protected data through a program, for the scans that refuse it leaving or landing
    where it should not: every node, and `prot(expr)` answering (what, class) when `expr` carries
    a [phi]/[pci]/[pii] value, a value derived from one, or a record holding one, else None. Shared
    by scan_protected_data_egress and scan_protected_into_untagged_field so both see the same
    derivations (a name assigned from protected data carries it, to a fixed point)."""
    nodes = []

    def collect(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                collect(c)
            return
        if not is_dataclass(node):
            return
        nodes.append(node)
        for f in fields(node):
            collect(getattr(node, f.name, None))

    collect(program)
    kind = lambda n: type(n).__name__

    # Tables a shape gives a tagged field, and the record names bound from them.
    tagged_tables = set()
    for n in nodes:
        if kind(n) == 'ShapeDecl':
            for tname, t in (getattr(n, 'tables', None) or {}).items():
                for fld in (getattr(t, 'fields', None) or []):
                    if str(getattr(fld, 'name', '')) in tagged:
                        tagged_tables.add(str(tname))
    records = {}
    for n in nodes:
        if kind(n) in ('FindBlock', 'RetrieveBlock', 'GrabBlock'):
            src = getattr(n, 'source', None)
            # A source is a DbRef, or a `db.<table>` DottedName (the check pipeline's reading).
            _parts = [str(x) for x in (getattr(src, 'parts', None) or [])]
            _tbl = (str(getattr(src, 'table', '')) if kind(src) == 'DbRef'
                    else _parts[1] if len(_parts) == 2 and _parts[0] == 'db' else '')
            if _tbl and _tbl in tagged_tables:
                for nm in (getattr(n, 'name', None), getattr(n, 'alias', None)):
                    if nm:
                        records[str(nm)] = f"a record from db.{_tbl}"
        if kind(n) == 'DottedName':
            parts = [str(p) for p in (getattr(n, 'parts', None) or [])]
            if len(parts) >= 2 and parts[-1] in tagged:
                records.setdefault(parts[0], f"`{'.'.join(parts)}`")

    derived = {}
    # A TASK'S PARAMETER IS FOLLOWED ONLY INSIDE THAT TASK (see scan_secret_egress): keyed by
    # (task, name), and read only by an expression in that task's own body.
    param_derived, in_task = {}, {}

    def _mark_task(t):
        stack = [getattr(t, 'body', None)]
        while stack:
            m = stack.pop()
            if isinstance(m, (list, tuple)):
                stack.extend(m)
                continue
            if not is_dataclass(m):
                continue
            in_task[id(m)] = str(t.name)
            for f in fields(m):
                stack.append(getattr(m, f.name, None))

    for _t in nodes:                       # outer tasks first, so a nested task's nodes end up its own
        if kind(_t) == 'TaskDecl' and getattr(_t, 'name', None):
            _mark_task(_t)

    def prot(expr, depth=0):
        """(what, class) when `expr` carries protected data, else None."""
        if expr is None or depth > 30:
            return None
        if isinstance(expr, str):
            for ref in _TEMPLATE_REF.findall(expr):
                parts = ref.split('.')
                if len(parts) >= 2 and parts[-1] in tagged:
                    return (f"`{{{{ {ref} }}}}`", tagged[parts[-1]])
                if parts[0] in derived:
                    return (f"`{{{{ {ref} }}}}`", derived[parts[0]][1])
            return None
        if isinstance(expr, (list, tuple)):
            for c in expr:
                h = prot(c, depth + 1)
                if h:
                    return h
            return None
        if not is_dataclass(expr):
            return None
        k = kind(expr)
        if k == 'StrippedExpr':
            return None
        if k == 'StringOpExpr' and str(getattr(expr, 'operation', '')) == 'mask.all':
            return None
        if k == 'TypeCastExpr' and str(getattr(expr, 'cast_type', '')) in ('json', 'csv', 'pdf', 'html'):
            return None
        if k == 'DottedName':
            parts = [str(p) for p in (getattr(expr, 'parts', None) or [])]
            if getattr(expr, 'stripped', False):
                return None
            if len(parts) >= 2 and parts[-1] in tagged:
                return (f"`{'.'.join(parts)}`", tagged[parts[-1]])
            if len(parts) == 1 and parts[0] in derived:
                return derived[parts[0]]
            if len(parts) == 1 and (in_task.get(id(expr)), parts[0]) in param_derived:
                return param_derived[(in_task.get(id(expr)), parts[0])]
            if len(parts) == 1 and parts[0] in records:
                return (f"`{parts[0]}`, {records[parts[0]]}", 'record')
            return None
        for f in fields(expr):
            h = prot(getattr(expr, f.name, None), depth + 1)
            if h:
                return h
        return None

    tasks = {str(n.name): n for n in nodes if kind(n) == 'TaskDecl' and getattr(n, 'name', None)}

    def _task_gives(t):
        """What a task's own status-less `give back` returns, when it is protected (a nested task is
        its own)."""
        stack = list(getattr(t, 'body', None) or [])
        while stack:
            m = stack.pop()
            if isinstance(m, (list, tuple)):
                stack.extend(m)
                continue
            if not is_dataclass(m) or kind(m) == 'TaskDecl':
                continue
            if kind(m) == 'GiveBackStmt' and getattr(m, 'status', None) is None:
                h = prot(getattr(m, 'value', None))
                if h:
                    return h
            for f in fields(m):
                stack.append(getattr(m, f.name, None))
        return None

    # Derived values: a name assigned from protected data carries it (to a fixed point).
    for _ in range(8):
        grew = False
        for n in nodes:
            if kind(n) in ('Assignment', 'HoldDecl', 'CreateBlock') and getattr(n, 'name', None) \
                    and str(n.name) not in derived:
                # A record built with `create NAME as sh.X` carries what its fields carry (HC-060:
                # a shaped connector payload holding a [phi] field passed check and sent it).
                h = prot(getattr(n, 'body', None) if kind(n) == 'CreateBlock' else getattr(n, 'value', None))
                if h:
                    derived[str(n.name)] = (f"`{n.name}` (from {h[0]})", h[1])
                    grew = True
            elif kind(n) == 'RunBlock':
                # THROUGH THE TASK BOUNDARY (RULED by Ron 2026-10-09, battery sweep 1): protected data
                # passed into a task travels in its parameter, and protected data a task gives back
                # travels out in the call's `as` name.
                t = tasks.get(str(getattr(n, 'task_name', '') or ''))
                if t is None:
                    continue
                params = [str(getattr(pp, 'name', '')) for pp in (getattr(t, 'params', None) or [])]
                sent = [(str(getattr(a, 'name', '')), getattr(a, 'value', None))
                        for a in (getattr(n, 'args', None) or [])]
                if getattr(n, 'inline_arg', None) is not None and params:
                    sent.append((params[0], n.inline_arg))
                for pname, val in sent:
                    if pname in params and (str(t.name), pname) not in param_derived:
                        h = prot(val)
                        if h:
                            param_derived[(str(t.name), pname)] = (
                                f"`{pname}` (from {h[0]}, passed into task `{t.name}`)", h[1])
                            grew = True
                alias = str(getattr(n, 'alias', '') or '')
                if alias and alias not in derived:
                    h = _task_gives(t)
                    if h:
                        derived[alias] = (f"`{alias}` (from {h[0]}, given back by task `{t.name}`)", h[1])
                        grew = True
        if not grew:
            break
    return nodes, prot, records, derived, tagged_tables


def _table_tagged_fields(program):
    """{table: {field: tag}} for every `as table` shape field tagged [phi], [pci] or [pii], by the
    TABLE it belongs to (a write lands in one table's column, so its own tag is what counts), and
    {table: {field: declared type}} beside it."""
    out, types = {}, {}

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
            for tname, t in (getattr(node, 'tables', None) or {}).items():
                tags = out.setdefault(str(tname), {})
                for fld in (getattr(t, 'fields', None) or []):
                    if getattr(fld, 'type_name', None):
                        types.setdefault(str(tname), {})[str(fld.name)] = str(fld.type_name)
                    for m in (getattr(fld, 'modifiers', None) or []):
                        if str(getattr(m, 'modifier_type', '') or '') != 'tag':
                            continue
                        for part in [x.strip().lower() for x in str(getattr(m, 'value', '') or '').split(',')]:
                            if part in ('phi', 'pci', 'pii'):
                                tags.setdefault(str(fld.name), part)
        for f in fields(node):
            walk(getattr(node, f.name, None))

    walk(program)
    return out, types


def scan_protected_into_untagged_field(program):
    """A [phi], [pci] or [pii] value, or a value derived from one, written into a field that carries
    no tag refuses at check (HC-039, RULED by Ron 2026-10-08). It was sealed on the way in, then a
    program that knows the destination only as ordinary text read it back as plain text, unmasked,
    with no protected access recorded: the classification did not survive the stored value. Tag the
    destination field, or mask the value first. The run-time half (_encrypt_fields_for_write)
    refuses what this cannot see."""
    tagged = _classified_field_names(program)
    if not tagged:
        return []
    nodes, prot, _records, _derived, _tt = _protected_value_tracker(program, tagged)
    by_table, types = _table_tagged_fields(program)
    errors = []
    for n in nodes:
        k = type(n).__name__
        if k not in ('SaveBlock', 'SaveOrUpdateBlock', 'UpdateBlock'):
            continue
        dest = getattr(n, 'target', None) if k == 'SaveBlock' else getattr(n, 'source', None)
        _parts = [str(x) for x in (getattr(dest, 'parts', None) or [])]
        table = (str(getattr(dest, 'table', '')) if type(dest).__name__ == 'DbRef'
                 else _parts[1] if len(_parts) == 2 and _parts[0] == 'db' else '')
        if not table:
            continue
        fvs = (getattr(n, 'fields', None) or []) if k != 'UpdateBlock' else \
            [b for b in (getattr(n, 'body', None) or []) if type(b).__name__ == 'FieldValue']
        for fv in fvs:
            name = str(getattr(fv, 'name', '') or '')
            # A destination no `as table` shape describes takes the program's bare-name tags, the
            # runtime resolver's own rule for a loose shape; one that a shape describes, its own.
            if not name or name in (by_table[table] if table in by_table else tagged):
                continue
            hit = prot(getattr(fv, 'value', None))
            if not hit or hit[1] == 'record':
                continue
            _src = hit[0][1:-1] if (hit[0].startswith('`') and hit[0].endswith('`')
                                    and ' ' not in hit[0]) else '<value>'
            errors.append(CompileError(
                f"{hit[0]} is [{hit[1]}] data, and `{name}` in db.{table} carries no tag, so it would "
                f"be stored where a later read sees ordinary text: unmasked, with no protected access "
                f"recorded.\n"
                f"    To fix it, tag the field in its shape (`{name} as {types.get(table, {}).get(name, 'text')} [{hit[1]}]`), or mask the "
                f"value first, for example `{name} {_src} mask.all except last 4`.",
                line=getattr(fv, 'line', 0) or getattr(n, 'line', 0) or 0))
    return errors


def scan_protected_data_egress(program):
    """PROTECTED DATA LEAVING THE APP (RULED by Ron): a [phi]/[pci]/[pii] value, a value derived from
    one, or a record holding one, reaching an AI call (weigh, goal, persona, context, an ai.create
    source) or an outside call (miohttp, a connector operation, miomail's subject and body) refuses
    at check. `mask.all` and `stripped` are the ways through; an AI call routed `using` a chain whose
    every provider is at a loopback address is exempt. The runtime backstop
    (_guard_protected_egress) refuses what this cannot see, such as a record whose table no shape
    describes."""
    tagged = _classified_field_names(program)
    if not tagged:
        return []
    nodes, prot, records, derived, tagged_tables = _protected_value_tracker(program, tagged)
    kind = lambda n: type(n).__name__

    # Chains whose every provider is at a loopback address: the one AI exemption.
    def _loopback(addr):
        # The same rule as the runtime's _is_loopback_address: localhost, *.localhost, a loopback IP.
        import ipaddress
        from urllib.parse import urlsplit
        s = str(addr or '')
        host = (urlsplit(s if '://' in s else 'http://' + s).hostname or '').lower()
        if host == 'localhost' or host.endswith('.localhost'):
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False
    local_chains = set()
    for n in nodes:
        if kind(n) == 'AiConnectBlock':
            # A chain is local when EVERY provider it orders is one this block declares at a
            # literal loopback address (an env-held address is not knowable at check).
            rows = {str(d.get('name', '')): d for d in (getattr(n, 'declared', None) or [])
                    if isinstance(d, dict)}
            provs = [p for p in (getattr(n, 'providers', None) or []) if isinstance(p, dict)]
            if provs and all(rows.get(str(p.get('provider', ''))) is not None
                             and rows[str(p.get('provider'))].get('address_type') == 'string'
                             and _loopback(rows[str(p.get('provider'))].get('address')) for p in provs):
                local_chains.update(str(x) for x in (getattr(n, 'names', None) or []))

    errors = []

    def refuse(node, what, hit, ai):
        line = getattr(node, 'line', 0) or 0
        where, cls = hit
        data = ('a record holding protected fields' if cls == 'record'
                else f"[{cls}] data")
        fix = ("send the record without its protected fields with `stripped` "
               "(`weigh patient stripped`)" if cls == 'record'
               else "mask the value first (`patient.ssn mask.all except last 4`), or leave it out")
        errors.append(CompileError(
            f"{what} would send {data} ({where}) "
            f"{'to an AI model' if ai else 'outside the app'}. Protected data does not leave the "
            f"app{' (a local model at a loopback address, through ai.connect, is the one AI exception)' if ai else ''}.\n"
            f"    To fix it, {fix}.", line=line))

    for n in nodes:
        k = kind(n)
        if k in ('AiDecideBlock', 'AiCompareBlock', 'AiRespondBlock', 'AiAgentBlock', 'AiCreateStmt'):
            using = next((str(getattr(b, 'chain_name', '')) for b in (getattr(n, 'body', None) or [])
                          if kind(b) == 'UsingChain'), None)
            if using and using in local_chains:
                continue
            what = f"{k.replace('Ai', 'ai.').replace('Block', '').replace('Stmt', '').lower()} {getattr(n, 'name', '') or ''}".strip()
            for b in (getattr(n, 'body', None) or []):
                if kind(b) == 'WeighClause':
                    for inp in (getattr(b, 'inputs', None) or []):
                        h = prot(inp)
                        if h:
                            refuse(n, f"`weigh {'.'.join(str(p) for p in inp.parts)}` in {what}", h, True)
            for attr in ('goal', 'persona', 'context', 'style'):
                h = prot(getattr(n, attr, None))
                if h:
                    refuse(n, f"the {attr} of {what}", h, True)
            if k == 'AiCreateStmt' and getattr(n, 'source', None):
                src = str(n.source)
                if src in records or src in derived:
                    h = derived.get(src) or (f"`{src}`, {records[src]}", 'record')
                    if getattr(n, 'realistic', None) is None:
                        errors.append(CompileError(
                            f"{what} builds from `{src}`, which "
                            f"holds protected data ({h[0]}). Content made from it must be fully "
                            f"synthetic.\n    To fix it, add `realistic 0.0`: it generates from the "
                            f"fields of `{src}`, never their values.", line=getattr(n, 'line', 0) or 0))
        elif k == 'MiohttpStmt':
            what = f"miohttp.{str(getattr(n, 'method', '') or '').lower()}"
            for attr in ('url', 'body'):
                h = prot(getattr(n, attr, None))
                if h:
                    refuse(n, f"the {attr} of {what}", h, False)
            for _hn, hv in (getattr(n, 'headers', None) or []):
                h = prot(hv)
                if h:
                    refuse(n, f"a header of {what}", h, False)
        elif k == 'MioconnectCall':
            h = prot(getattr(n, 'payload', None))
            if h:
                refuse(n, f"the payload of {n.connector}.{n.operation}", h, False)
        elif k == 'MiomailStmt':
            for attr in ('subject', 'body', 'attach'):
                h = prot(getattr(n, attr, None))
                if h:
                    refuse(n, f"the {attr} of miomail.send", h, False)
            # The addresses are the destination: a [pii] address is how mail is delivered and the
            # purpose rule governs it; anything else protected there is data sent (HC-066).
            for attr in ('to', 'from_', 'reply_to', 'cc', 'bcc'):
                h = prot(getattr(n, attr, None))
                if h and h[1] not in ('pii',):
                    refuse(n, f"the {attr.rstrip('_')} address of miomail.send", h, False)
        elif k == 'MiofileStmt':
            # A name the program creates is written to the disk (HC-066); a lookup is not a way out.
            for attr in (('path', 'dest') if str(getattr(n, 'op', '')) == 'write' else ('dest',)):
                h = prot(getattr(n, attr, None))
                if h:
                    refuse(n, f"the file name of miofile.{n.op}", h, False)
        if k == 'MiohttpStmt':
            h = prot(getattr(n, 'auth', None))
            if h:
                refuse(n, f"the auth of miohttp.{str(getattr(n, 'method', '') or '').lower()}", h, False)
        if k == 'GrantRoleDecl':
            # The session keeps `for person` and hands it back as session.person on every request; a
            # [pii] identifier is the ordinary case, a [phi] or [pci] value is not.
            h = prot(getattr(n, 'person', None))
            if h and h[1] in ('phi', 'pci'):
                line = getattr(n, 'line', 0) or 0
                errors.append(CompileError(
                    f"Line {line}: `grant role ... for person` is given {h[0]}, which is [{h[1]}] data, "
                    f"and the session keeps it and hands it back as session.person on every request.\n"
                    f"    To fix it, identify the person by an id or an email (`for person user.id`).",
                    line=line))
    return errors


_AI_CREATE_UNBUILT = {'create_count': 'count', 'create_from_db': 'from db.<table>',
                      'create_to_db': 'to db.<table>', 'create_to_path': 'to <path>',
                      'create_as_shape': 'as sh.<Shape>'}


# Every lookup the grammar has that can carry `on.failure` (enumerated from mohio.lark, H 6.0.1;
# tests/test_battery_not_found_in_failure_runtime.py fails when a new one is not classified).
# `check exists` / `check unique` refuse any on.success/on.failure at transform, so of
# CheckMioqlBlock only `check count` reaches this rule.
_QUERY_BLOCKS = ('RetrieveBlock', 'FindBlock', 'GrabBlock', 'PullBlock', 'GetBlock', 'CheckMioqlBlock')


def _refusal_status(stmts):
    """The first 4xx a `give back` in these statements answers with, or None (nested blocks too)."""
    for s in (stmts if isinstance(stmts, (list, tuple)) else [stmts]):
        if s is None or not is_dataclass(s):
            continue
        if type(s).__name__ == 'GiveBackStmt':
            st = getattr(s, 'status', None)
            if isinstance(st, int) and 400 <= st < 500:
                return st, getattr(s, 'line', 0) or 0
            continue
        for f in fields(s):
            v = getattr(s, f.name, None)
            if isinstance(v, list) or is_dataclass(v):
                hit = _refusal_status(v)
                if hit:
                    return hit
    return None


def not_found_refusal_in_failure(node):
    """A lookup that refuses in `on.failure` and has nowhere to handle finding nothing (HC-015,
    HC-017, HC-018, HC-020, HC-021, HC-022). Returns (status, line, failure_line, name) or None.

    Since FORK-1 (ruled 2026-08-19, first released in 4.9.0) `on.failure` is the ERROR path: it runs
    when the query itself breaks, never when it finds no row. Code written as "refuse in on.failure
    when the row is missing" therefore never refuses: the result is empty and the handler carries
    on, into the protected read, the write, the commit. Under a sector that is an authorization that
    fails OPEN. A client-error status (4xx) in `on.failure` is the mark of that intent, since a broken
    query is not the caller's fault; with no `when` on the block to handle the empty result, the
    refusal can never run. Shared by the check (scan_not_found_refusal_in_failure) and the runtime."""
    from mohio_ast import OnFailure, CheckWhen
    if type(node).__name__ not in _QUERY_BLOCKS:
        return None
    handlers = getattr(node, 'handlers', None) or []
    if any(isinstance(h, CheckWhen) for h in handlers):
        return None
    for h in handlers:
        if isinstance(h, OnFailure):
            hit = _refusal_status([getattr(h, 'inline', None)] + list(getattr(h, 'body', None) or []))
            if hit:
                # `pull` keeps its result name in as_name, and may have none
                return (hit[0], hit[1], h.line,
                        getattr(node, 'name', None) or getattr(node, 'as_name', None) or '<name>')  # SILENT-OK: placeholder in the refusal's teaching text only; the program refuses either way and the text asks for a name

    return None


def not_found_refusal_message(node, hit):
    status, gb_line, fail_line, name = hit
    kind = type(node).__name__
    from mohio_ast import OnSuccess
    goes_on = ("its `on.success` runs" if any(isinstance(h, OnSuccess) for h in (node.handlers or []))
               else "the handler carries on past this block")
    if kind == 'CheckMioqlBlock':
        # a count answers a number: no matching row is a 0, branched on with `when empty`
        return (f"this `on.failure` gives back [{status}] (line {gb_line}), but `on.failure` runs only "
                f"when the lookup itself breaks, such as a lost connection or a missing table. Matching "
                f"no row is not a failure: `{name}` comes back 0 and {goes_on}, so the refusal never "
                f"happens and everything after it runs.\n"
                f"    Refuse on a zero count instead, inside the block:\n"
                f"        check count as {name} in ...\n"
                f"            when empty\n"
                f"                give back [{status}] ...\n"
                f"        check: done\n"
                f"    Keep `on.failure` for a broken lookup (for example `give back [503]`).")
    verb = {'RetrieveBlock': 'retrieve' + ('.' + node.modifier if getattr(node, 'modifier', None) else ''),
            'FindBlock': 'find', 'GrabBlock': 'grab', 'PullBlock': 'pull', 'GetBlock': 'get'}[kind]
    return (f"this `on.failure` gives back [{status}] (line {gb_line}), but `on.failure` runs only when "
            f"the lookup itself breaks, such as a lost connection or a missing table. Finding no row is "
            f"not a failure: `{name}` comes back empty and {goes_on}, so the refusal never happens "
            f"and everything after it runs.\n"
            f"    Refuse on the empty result instead, inside the block:\n"
            f"        {verb} {name} ...\n"
            f"            when {name} is empty\n"
            f"                give back [{status}] ...\n"
            f"        {verb}: done\n"
            f"    Keep `on.failure` for a broken lookup (for example `give back [503]`).")


def scan_not_found_refusal_in_failure(program):
    """Refuse at check a lookup whose not-found refusal sits in `on.failure` (see
    not_found_refusal_in_failure). Mirrored at run by the query executors."""
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
        hit = not_found_refusal_in_failure(node)
        if hit:
            errors.append(CompileError(not_found_refusal_message(node, hit), line=hit[2] or
                                       getattr(node, 'line', 0) or 0))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    walk(program)
    return errors


def scan_success_on_possibly_empty_lookup(program):
    """Warn on a single-record lookup whose `on.success` has nowhere to handle finding nothing
    (MHC-B011). `on.success` runs whenever the lookup did not break, which includes finding no row,
    so a body written as "the row was found" runs with an empty result: in the health app, an
    unauthorized clinician's lookup found nothing and its on.success still served the record and
    wrote an access event. A warning, not a refusal: an on.success that only logs that the lookup ran
    is correct, and only the author can tell which one this is."""
    from mohio_ast import OnSuccess, CheckWhen
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
        kind = type(node).__name__
        single = (kind == 'GrabBlock' or (kind == 'RetrieveBlock'
                                          and getattr(node, 'modifier', None) in (None, 'one', 'first', 'last')))
        hs = getattr(node, 'handlers', None) or []
        if (single and any(isinstance(h, OnSuccess) for h in hs)
                and not any(isinstance(h, CheckWhen) for h in hs)
                and not not_found_refusal_in_failure(node)):
            ok = next(h for h in hs if isinstance(h, OnSuccess))
            name = node.name
            warnings.append(CompileWarning(
                f"`on.success` here runs whenever the lookup did not break, including when it finds no "
                f"row, so `{name}` may be empty inside it. If this branch means \"found\", it also runs "
                f"for a lookup that matched nothing.",
                getattr(ok, 'line', 0) or getattr(node, 'line', 0) or 0,
                f"Handle the empty result in the block, before anything that assumes a row:\n"
                f"    when {name} is empty\n"
                f"        give back [404] ...\n"
                f"    otherwise\n"
                f"        ...the found path...\n"
                f"Keep `on.success` only for work that is right whether or not a row came back.", code="success_on_possibly_empty_lookup"))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    walk(program)
    return warnings


def scan_ai_create_unbuilt_clauses(program):
    """ai.create clauses the grammar accepts and nothing executes (T1-AI-CREATE-BODY-CLAUSES):
    `count`, `from db.X`, `to db.X`, `to <path>`, `as sh.X`, `weigh`, and any `realistic` but 0.0.
    They parsed and did nothing. Mirrors the refusal at the top of _exec_AiCreateStmt."""
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
        if type(node).__name__ == 'AiCreateStmt':
            line = getattr(node, 'line', 0) or 0
            words = [_AI_CREATE_UNBUILT[str(getattr(b, 'data', ''))] for b in (node.body or [])
                     if str(getattr(b, 'data', '')) in _AI_CREATE_UNBUILT]
            words += ['weigh' for b in (node.body or []) if type(b).__name__ == 'WeighClause']
            # `using NAME` here was read as a free-form hint and folded into the prompt, so the
            # request went to the default provider while the block named another (2026-10-09).
            words += ['using' for k in (getattr(node, 'attrs', None) or {}) if str(k) == 'using']
            if words:
                errors.append(CompileError(
                    f"ai.create {node.name}: {', '.join('`' + w + '`' for w in words)} is "
                    f"declared in the grammar but not built in this release, so it would do nothing "
                    f"(T1-AI-CREATE-BODY-CLAUSES).\n    To fix it, remove the clause; name what to "
                    f"build from with `from <value>`.", line=line))
            r = getattr(node, 'realistic', None)
            if r is not None and float(r) != 0.0:
                errors.append(CompileError(
                    f"ai.create {node.name}: `realistic {r}` is not built; only "
                    f"`realistic 0.0` (fully synthetic, from the source's fields and never its values) "
                    f"is (T1-AI-CREATE-BODY-CLAUSES).\n    To fix it, write `realistic 0.0`, or leave "
                    f"`realistic` out.", line=line))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    walk(program)
    return errors


def _join_value_text(value):
    """The match value as the program wrote it, for quoting it back."""
    if type(value).__name__ == 'Literal':
        v = getattr(value, 'value', None)
        return f'"{v}"' if getattr(value, 'literal_type', '') == 'string' else str(v)
    parts = getattr(value, 'parts', None)
    if parts:
        return '.'.join(str(x) for x in parts)
    return str(value)


def _join_source_text(source):
    """The `with` source as the program wrote it (`db.orders`), never the node's internal form."""
    parts = getattr(source, 'parts', None)
    if parts:
        return '.'.join(str(x) for x in parts)
    table = getattr(source, 'table', None)
    if table:
        return f"db.{table}"   # a DbRef is the `db.<table>` reference by definition
    return 'db.<table>'


def join_match_message(line, field, value, joined, source, found, variant='with'):
    """The refusal for a `match` inside a `with` that does not connect the joined record to the
    record it hangs from. Shared by the check scan and the runtime, so both say the same thing."""
    written = f"match {field} to {_join_value_text(value)}"
    where = f"Line {line}: " if line else ""
    src = _join_source_text(source)
    if type(value).__name__ == 'Literal':
        return (f"{where}`{written}` compares the joined `{joined}` record's `{field}` with a fixed "
                f"value. In a `{variant}` join every match pair connects a field of `{joined}` to a "
                f"field of `{found}`, the record it belongs to. A fixed value is a filter, and it "
                f"goes in a `where` inside the `{variant}`:\n"
                f"    {variant} {joined} from {src}\n"
                f"        match {field} to {found}.<the {found} field it belongs to>\n"
                f"        where {field} is {_join_value_text(value)}\n"
                f"    {variant}: done\n"
                f"(join_match_fixed_value)")
    return (f"{where}`{written}` connects the joined `{joined}` record to `{_join_value_text(value)}`, "
            f"but `{joined}` belongs to `{found}`. Every match pair in a `{variant}` connects a field "
            f"of `{joined}` to a field of `{found}`:\n"
            f"        match {field} to {found}.<field>\n"
            f"(join_match_wrong_record)")


def scan_join_match_must_connect(program):
    """A `match` inside a `with` must connect the joined record to the record it hangs from (the
    find's record, or the enclosing `with` for a nested one). A fixed value there (`match person to
    "alice"`) checked clean and refused only at run, with an unreadable message; it now refuses
    here, naming both records and showing the `where` the value belongs in."""
    errors = []

    def joins_of(body):
        return [b for b in (body or []) if type(b).__name__ == 'JoinBlock']

    def check_join(j, parent_name, line_fallback):
        for b in (getattr(j, 'body', None) or []):
            if type(b).__name__ != 'MatchClause':
                continue
            val = getattr(b, 'value', None)
            parts = getattr(val, 'parts', None) or []
            ok = type(val).__name__ == 'DottedName' and len(parts) >= 2 \
                and str(parts[0]) == str(parent_name)
            if not ok:
                line = getattr(b, 'line', 0) or getattr(j, 'line', 0) or line_fallback
                # A JoinBlock always carries its variant; an empty one is the plain `with`.
                _variant = getattr(j, 'variant', None)
                errors.append(CompileError(
                    join_match_message(line, getattr(b, 'field', '?'), val, getattr(j, 'name', '?'),
                                       getattr(j, 'source', None), parent_name,
                                       _variant if _variant else 'with'),
                    line=line))
        for nj in joins_of(getattr(j, 'body', None)):
            check_join(nj, getattr(j, 'name', ''), line_fallback)

    def walk(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                walk(c)
            return
        if not is_dataclass(node):
            return
        if type(node).__name__ in ('FindBlock', 'RetrieveBlock'):
            found = getattr(node, 'alias', None) or getattr(node, 'name', None) or ''
            for j in joins_of(getattr(node, 'body', None)):
                check_join(j, found, getattr(node, 'line', 0) or 0)
        for f in fields(node):
            walk(getattr(node, f.name, None))

    walk(program)
    return errors


def scan_secret_egress(program):
    """A SECRET IS USED, NEVER SHOWN (HC-061 to HC-069, RULED by Ron 2026-10-08). `secret.X`, or any
    value made from it, reaching a response, a render, `show`, a log, a cookie, a redirect, an
    outside call's address, header or body, a connector payload, mail, an AI call, a file, the cache
    or a stored field refuses at check. Its declared use is the credential of an outside call: the
    `auth` of a miohttp call, an `Authorization` header, and a connector's own `auth` lines, which
    this scan does not read. Masking does not make a secret safe to show, so `mask.all` is no way
    through. The runtime backstop (_refuse_secret) refuses what this cannot see, such as a secret
    passed into a task."""
    nodes = []

    def collect(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                collect(c)
            return
        if not is_dataclass(node):
            return
        nodes.append(node)
        for f in fields(node):
            collect(getattr(node, f.name, None))

    collect(program)
    if not any(type(n).__name__ == 'SecretRef' for n in nodes):
        return []
    kind = lambda n: type(n).__name__
    derived = {}
    # A TASK'S PARAMETER IS FOLLOWED ONLY INSIDE THAT TASK. A secret passed into `keep`'s `v` says
    # nothing about another task's `v`, so a parameter is keyed by (task, name) and read only by an
    # expression that sits in that task's own body (`in_task` maps every node to its innermost task).
    param_derived, in_task = {}, {}

    def _mark_task(t):
        stack = [getattr(t, 'body', None)]
        while stack:
            m = stack.pop()
            if isinstance(m, (list, tuple)):
                stack.extend(m)
                continue
            if not is_dataclass(m):
                continue
            in_task[id(m)] = str(t.name)
            for f in fields(m):
                stack.append(getattr(m, f.name, None))

    for _t in nodes:                       # outer tasks first, so a nested task's nodes end up its own
        if kind(_t) == 'TaskDecl' and getattr(_t, 'name', None):
            _mark_task(_t)

    def sec(expr, depth=0):
        """The `secret.X` that `expr` carries, as written, else None."""
        if expr is None or depth > 30:
            return None
        if isinstance(expr, str):
            for ref in _TEMPLATE_REF.findall(expr):
                if ref.split('.')[0] in derived:
                    return derived[ref.split('.')[0]]
                if ref.startswith('secret.'):
                    return f"`{ref}`"
            return None
        if isinstance(expr, (list, tuple)):
            for c in expr:
                h = sec(c, depth + 1)
                if h:
                    return h
            return None
        if not is_dataclass(expr):
            return None
        if kind(expr) == 'SecretRef':
            return f"`secret.{expr.key}`"
        if kind(expr) == 'DottedName':
            parts = [str(p) for p in (getattr(expr, 'parts', None) or [])]
            if parts and parts[0] in derived:
                return derived[parts[0]]
            if parts and (in_task.get(id(expr)), parts[0]) in param_derived:
                return param_derived[(in_task.get(id(expr)), parts[0])]
            return None
        for f in fields(expr):
            h = sec(getattr(expr, f.name, None), depth + 1)
            if h:
                return h
        return None

    tasks = {str(n.name): n for n in nodes if kind(n) == 'TaskDecl' and getattr(n, 'name', None)}

    def _task_gives(t):
        """The secret a task's own status-less `give back` returns, if any (a nested task is its own)."""
        stack = list(getattr(t, 'body', None) or [])
        while stack:
            m = stack.pop()
            if isinstance(m, (list, tuple)):
                stack.extend(m)
                continue
            if not is_dataclass(m) or kind(m) == 'TaskDecl':
                continue
            if kind(m) == 'GiveBackStmt' and getattr(m, 'status', None) is None:
                h = sec(getattr(m, 'value', None))
                if h:
                    return h
            for f in fields(m):
                stack.append(getattr(m, f.name, None))
        return None

    for _ in range(8):
        grew = False
        for n in nodes:
            if kind(n) in ('Assignment', 'HoldDecl', 'CreateBlock') and getattr(n, 'name', None) \
                    and str(n.name) not in derived:
                h = sec(getattr(n, 'body', None) if kind(n) == 'CreateBlock' else getattr(n, 'value', None))
                if h:
                    derived[str(n.name)] = h if h.startswith('`secret.') else h
                    grew = True
            elif kind(n) == 'RunBlock':
                # THROUGH THE TASK BOUNDARY (RULED by Ron 2026-10-09, battery sweep 1): a secret passed
                # into a task travels in its parameter, and one a task gives back travels out in the
                # call's `as` name, the same way the grant-role check follows a client-sent role.
                t = tasks.get(str(getattr(n, 'task_name', '') or ''))
                if t is None:
                    continue
                params = [str(getattr(pp, 'name', '')) for pp in (getattr(t, 'params', None) or [])]
                sent = [(str(getattr(a, 'name', '')), getattr(a, 'value', None))
                        for a in (getattr(n, 'args', None) or [])]
                if getattr(n, 'inline_arg', None) is not None and params:
                    sent.append((params[0], n.inline_arg))
                for pname, val in sent:
                    if pname in params and (str(t.name), pname) not in param_derived:
                        h = sec(val)
                        if h:
                            param_derived[(str(t.name), pname)] = f"{h} (passed into task `{t.name}`)"
                            grew = True
                alias = str(getattr(n, 'alias', '') or '')
                if alias and alias not in derived:
                    h = _task_gives(t)
                    if h:
                        derived[alias] = f"{h} (given back by task `{t.name}`)"
                        grew = True
        if not grew:
            break

    # A status-less `give back` in a task that declares `returns` is the task's return value, not a
    # response: a task may build and return a credential for its caller to use.
    task_returns = set()

    def _mark_returns(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                _mark_returns(c)
            return
        if not is_dataclass(node):
            return
        if kind(node) == 'TaskDecl':
            return          # a nested task is judged by its own declaration
        if kind(node) == 'GiveBackStmt' and getattr(node, 'status', None) is None:
            task_returns.add(id(node))
        for f in fields(node):
            _mark_returns(getattr(node, f.name, None))

    for n in nodes:
        if kind(n) == 'TaskDecl' and (getattr(n, 'return_type', None) or getattr(n, 'returns', None)):
            _mark_returns(getattr(n, 'body', None))

    errors = []

    def refuse(node, where, hit, stored=False):
        line = getattr(node, 'line', 0) or 0
        # The fix names the secret alone; `hit` may carry the way it travelled ("passed into task").
        _m = re.search(r'secret\.[A-Za-z_][A-Za-z0-9_]*', hit or '')
        _key = _m.group(0) if _m else 'secret.NAME'
        errors.append(CompileError(
            f"Line {line}: {hit}, or a value made from it, would {'be stored in' if stored else 'reach'} "
            f"{where}. A secret is used only where it is declared for, a connector's credential or "
            f"the `auth` of an outside call, and is never shown, logged, sent elsewhere or stored, "
            f"masked or not.\n"
            f"    To fix it, pass it only as a credential (`auth {_key}` on the call, or "
            f"`auth bearer {_key}` in the connector's block); to show that it is set, "
            f"check that it exists rather than showing it.", line=line))

    for n in nodes:
        k = kind(n)
        if k == 'ShowStmt':
            h = sec(n.value)
            if h:
                refuse(n, 'the output of `show`', h)
        elif k == 'GiveBackStmt' and id(n) not in task_returns:
            h = sec(n.value)
            if h:
                refuse(n, 'the response', h)
        elif k == 'ShowBlock':
            h = sec(getattr(n, 'html', None))
            if h:
                refuse(n, 'a rendered page', h)
        elif k == 'MioLogStmt':
            h = sec(n.value)
            if h:
                refuse(n, f"a log (`miolog.{n.level}`)", h)
        elif k == 'DebugLogStmt':
            _t = str(getattr(n, 'target', '') or '').split('.')[0]
            if _t in derived or _t == 'secret':
                refuse(n, 'the debug log', derived.get(_t) or f"`{n.target}`")
        elif k == 'MioCookieSet':
            h = sec(getattr(n, 'inline_value', None)) or sec(getattr(n, 'value', None))
            if h:
                refuse(n, f'the cookie "{n.name}"', h)
        elif k == 'JumpToStmt':
            h = sec(n.destination)
            if h:
                refuse(n, 'a redirect address', h)
        elif k == 'MiohttpStmt':
            what = f"miohttp.{str(n.method or '').lower()}"
            for attr in ('url', 'body'):
                h = sec(getattr(n, attr, None))
                if h:
                    refuse(n, f"the {'address' if attr == 'url' else attr} of {what}", h)
            for hn, hv in (getattr(n, 'headers', None) or []):
                if str(hn).lower() == 'authorization':
                    continue        # the declared use
                h = sec(hv)
                if h:
                    refuse(n, f'the "{hn}" header of {what}', h)
        elif k == 'MioconnectCall':
            h = sec(getattr(n, 'payload', None))
            if h:
                refuse(n, f"the payload of {n.connector}.{n.operation}", h)
        elif k == 'MiomailStmt':
            for attr in ('to', 'from_', 'subject', 'body', 'attach', 'cc', 'bcc', 'reply_to'):
                h = sec(getattr(n, attr, None))
                if h:
                    refuse(n, f"the {attr.rstrip('_')} of miomail", h)
        elif k in ('AiDecideBlock', 'AiCompareBlock', 'AiRespondBlock', 'AiAgentBlock', 'AiCreateStmt'):
            for b in (getattr(n, 'body', None) or []):
                if kind(b) == 'WeighClause':
                    for inp in (getattr(b, 'inputs', None) or []):
                        h = sec(inp)
                        if h:
                            refuse(n, f"an AI call (`weigh` in {getattr(n, 'name', '') or 'it'})", h)
            for attr in ('goal', 'persona', 'context', 'style'):
                h = sec(getattr(n, attr, None))
                if h:
                    refuse(n, f"an AI call (its {attr})", h)
        elif k == 'MiofileStmt':
            for attr in ('path', 'content', 'dest'):
                h = sec(getattr(n, attr, None))
                if h:
                    refuse(n, f"a file (miofile.{n.op}, its {attr})", h, stored=(attr == 'content'))
        elif k == 'GrantRoleDecl':
            h = sec(getattr(n, 'person', None)) or sec(getattr(n, 'value', None))
            if h:
                refuse(n, 'the session (as session.person or a role)', h, stored=True)
        elif k == 'MioCacheStmt' and str(getattr(n, 'op', '')) == 'set':
            h = sec(getattr(n, 'values', None))
            if h:
                refuse(n, f'the cache entry "{n.key}"', h, stored=True)
        elif k in ('SaveBlock', 'SaveOrUpdateBlock', 'UpdateBlock', 'ModifyBlock'):
            fvs = (getattr(n, 'fields', None) or []) if k in ('SaveBlock', 'SaveOrUpdateBlock') else \
                [b for b in (getattr(n, 'body', None) or []) if kind(b) == 'FieldValue']
            for fv in fvs:
                h = sec(getattr(fv, 'value', None))
                if h:
                    refuse(fv if getattr(fv, 'line', 0) else n, f"`{fv.name}`, a stored field", h,
                           stored=True)
    return errors


def scan_save_from_record_undeclared(program):
    """A record saved whole from INPUT into a table no shape declares refuses at check (the relay-
    identifier blocker, RULED by Ron 2026-10-08: the schema never grows from input). `save [all] to
    db.t from X` writes whatever keys X carries; when X is the request or what an outside call
    returned, those keys are the caller's, and with no `t as table` shape nothing says which belong.
    The runtime refuses any key the program declares nowhere (_exec_SaveAllBlock); this catches the
    input case before it runs."""
    kind = lambda n: type(n).__name__
    nodes = []

    def collect(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                collect(c)
            return
        if not is_dataclass(node):
            return
        nodes.append(node)
        for f in fields(node):
            collect(getattr(node, f.name, None))

    collect(program)
    shaped = set()
    for n in nodes:
        if kind(n) == 'ShapeDecl':
            shaped.update(str(t) for t in (getattr(n, 'tables', None) or {}))
    inputs = {'request'}
    for n in nodes:
        if kind(n) == 'MiohttpStmt' and getattr(n, 'alias', None):
            inputs.add(str(n.alias))
    for _ in range(6):
        grew = False
        for n in nodes:
            if kind(n) in ('Assignment', 'HoldDecl') and getattr(n, 'name', None) \
                    and str(n.name) not in inputs:
                v = getattr(n, 'value', None)
                parts = [str(p) for p in (getattr(v, 'parts', None) or [])]
                if parts and parts[0] in inputs:
                    inputs.add(str(n.name))
                    grew = True
        if not grew:
            break
    errors = []
    for n in nodes:
        if kind(n) != 'SaveAllBlock':
            continue
        tgt = getattr(n, 'target', None)
        tparts = [str(p) for p in (getattr(tgt, 'parts', None) or [])]
        table = (str(getattr(tgt, 'table', '')) if kind(tgt) == 'DbRef'
                 else tparts[1] if len(tparts) >= 2 and tparts[0] == 'db' else '')
        if not table or table in shaped:
            continue
        src = getattr(n, 'source', None)
        sparts = [str(p) for p in (getattr(src, 'parts', None) or [])]
        if not sparts or sparts[0] not in inputs:
            continue
        line = getattr(n, 'line', 0) or 0
        errors.append(CompileError(
            f"Line {line}: this saves `{'.'.join(sparts)}` whole into `db.{table}`, so its columns "
            f"would be whatever keys the caller sent, and no shape declares `db.{table}` to say which "
            f"belong. A table's columns come from the program, never from the data written to it.\n"
            f"    To fix it, declare the table in a shape (`{table} as table`, one line per field), or "
            f"save each field by name (`save to db.{table}` with a line per field).", line=line))
    return errors


_PASSWORD_NAME = re.compile(r'(^|_)(password|passwd|passphrase|passcode|pwd)(_|$)|password', re.I)


def _dataclass_nodes(root):
    stack = [root]
    while stack:
        n = stack.pop()
        if n is None or isinstance(n, (str, int, float, bool)):
            continue
        if isinstance(n, (list, tuple)):
            stack.extend(n)
            continue
        if not is_dataclass(n):
            continue
        yield n
        for f in fields(n):
            stack.append(getattr(n, f.name, None))


def _tasks_and_operations(program):
    """The program's tasks by name, and each connector's operations with their HTTP method."""
    tasks, ops = {}, {}
    for n in _dataclass_nodes(program):
        k = type(n).__name__
        if k == 'TaskDecl' and getattr(n, 'name', None):
            tasks[str(n.name)] = n
        elif k == 'MioconnectDecl':
            ops[str(n.name)] = {str(getattr(o, 'name', '')): str(getattr(o, 'method', 'POST')).upper()
                                for o in (getattr(n, 'operations', None) or [])}
    return tasks, ops


def _outbound_effect(n, ops):
    """What the node sends to another service, as words a developer would recognise, or None when it
    only reads. One definition, shared by the transaction rule and the idempotent-route rule."""
    k = type(n).__name__
    if k == 'MiohttpStmt' and str(getattr(n, 'method', 'get')).lower() in ('post', 'put', 'patch', 'delete'):
        return f"miohttp.{str(n.method).lower()}"
    if k == 'MioconnectCall':
        _m = ops.get(str(n.connector), {}).get(str(n.operation), 'POST')
        if _m != 'GET':
            return f"{n.connector}.{n.operation}"
    if k == 'MiomailStmt' and str(getattr(n, 'action', 'send')) in ('send', 'queue'):
        return f"miomail.{n.action}"
    return None


def _first_effect(body, tasks, effect, via=None, seen=frozenset()):
    """The first node under `body` that `effect` names, followed through `run`/`call` into the tasks
    it reaches: (what, line, via), where via is (task, line it is called on) or None."""
    for n in _dataclass_nodes(body):
        what = effect(n)
        if what:
            return what, getattr(n, 'line', 0) or 0, via
        if type(n).__name__ == 'RunBlock':
            t = tasks.get(str(getattr(n, 'task_name', '')))
            if t is not None and t.name not in seen:
                hit = _first_effect(t.body, tasks, effect, via or (t.name, getattr(n, 'line', 0) or 0),
                                    seen | {t.name})
                if hit:
                    return hit
    return None


def scan_outbound_call_in_transaction(program):
    """A call that changes something on another service refuses inside a `transaction` (RULED by Ron
    2026-10-08, pre-tag). A rollback puts the database back; it cannot take back a payment, an email
    or a message that already went out, so a transaction that fails after the call leaves the two
    disagreeing, and one that succeeds after a retried call may have sent it twice. Followed through
    `run`/`call` into the tasks it reaches. Read-only calls (a `miohttp.get`, a connector GET
    operation, an AI call) are not refused."""
    errors = []
    tasks, ops = _tasks_and_operations(program)
    for n in _dataclass_nodes(program):
        if type(n).__name__ != 'TransactionBlock':
            continue
        hit = _first_effect(n.body, tasks, lambda x: _outbound_effect(x, ops))
        if not hit:
            continue
        what, line, via = hit
        tline = getattr(n, 'line', 0) or 0
        where = (f"`{what}` on line {line}, in task `{via[0]}` called on line {via[1]}, sends"
                 if via else f"`{what}` on line {line} sends")
        at = via[1] if via else line
        errors.append(CompileError(
            f"Line {at}: {where} to another service from inside the `transaction` that opens on line "
            f"{tline}. If the transaction rolls back, the database is put back but the call has "
            f"already happened, and a payment, an email or a message cannot be taken back.\n"
            f"    Do it in two steps: save the work as pending and let the transaction commit, then make "
            f"the call, then record what it answered:\n"
            f"        transaction\n            save to db.orders\n                status \"pending\"\n"
            f"            save: done\n        transaction: done\n        {what} ... as result\n"
            f"        update db.orders\n            status result.status\n        update: done\n"
            f"    (A read-only call, a `miohttp.get` or an AI call, may stay inside.)",
            line=at))
    return errors


def scan_retry_after_send(program):
    """A connector `retry` over an operation that changes something says what it will and will not
    retry (RULED by Ron 2026-10-08, pre-tag). Until the idempotency design lands, a retry runs only
    when the request provably never left (a refused connection, a host name that did not resolve). It
    never runs after a timeout, a reset or a partial response, because the service may already have
    the request and a second attempt could charge or send twice. A notice, not an error: the
    behaviour is correct, and the developer should know a declared retry will not fire on a timeout."""
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
        if type(node).__name__ == 'MioconnectDecl' and getattr(node, 'retry', None):
            changing = [op for op in (getattr(node, 'operations', None) or [])
                        if str(getattr(op, 'method', 'POST')).upper() != 'GET']
            if changing:
                line = getattr(node, 'line', 0) or 0
                ops = ', '.join(f"`{getattr(o, 'name', '?')}` ({str(getattr(o, 'method', 'POST')).upper()})"
                                for o in changing)
                warnings.append(CompileWarning(
                    f"Line {line}: `retry {node.retry} times` on connector `{node.name}` covers "
                    f"{ops}, which change something on the other side. Each is retried only when the "
                    f"request never left (a refused connection, a host name that did not resolve); "
                    f"after a timeout or a reset it is not retried, because the service may already "
                    f"have it and a second attempt could deliver it twice.\n"
                    f"    Make the operation safe to repeat on the service's side, or handle the "
                    f"timeout in `on.failure` (look the result up before trying again).",
                    line=line, code="connector_retry_changes"))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    walk(program)
    return warnings


def scan_password_field_without_format(program):
    """A shape field named like a password with no `format "password"` (the sensitive-values sweep,
    RULED by Ron 2026-10-08). A form that fails validation is re-rendered filled in with what was
    submitted, and a submitted password carries no tag, so only `format "password"` keeps it from
    being written back into the page. A warning, since the name is a guess about intent."""
    warnings = []

    def fields_of(shape):
        out = list(getattr(shape, 'fields', None) or [])
        for _t in (getattr(shape, 'tables', None) or {}).values():
            out += list(getattr(_t, 'fields', None) or [])
        return out

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
            for fld in fields_of(node):
                name = str(getattr(fld, 'name', '') or '')
                if not name or not _PASSWORD_NAME.search(name):
                    continue
                fmts = [str(getattr(m, 'value', '') or '').strip().strip('"').lower()
                        for m in (getattr(fld, 'modifiers', None) or [])
                        if str(getattr(m, 'modifier_type', '')) == 'format']
                if 'password' in fmts:
                    continue
                line = getattr(fld, 'line', 0) or getattr(node, 'line', 0) or 0
                warnings.append(CompileWarning(
                    f"Line {line}: `{name}` reads as a password, but it has no `format \"password\"`, "
                    f"so a form that fails validation is sent back with the submitted password "
                    f"filled in.\n"
                    f"    To fix it, declare the format:\n"
                    f"        {name} as text format \"password\"", line=line, code="password_no_format"))
            return
        for f in fields(node):
            walk(getattr(node, f.name, None))

    walk(program)
    return warnings


def scan_join_key_protection_mismatch(program):
    """A join `match` pairing a [phi]/[pci]/[pii] field with an untagged one refuses at check
    (HC-104/105). A tagged field is stored encrypted and joins through its blind index; the untagged
    side holds the plain value, so the database has nothing to compare and the join matches nothing.
    Seen here when both tables are declared `as table`; the run-time half (_bidx_join_on) refuses the
    rest."""
    by_table, _types = _table_tagged_fields(program)
    if not any(by_table.values()):
        return []
    errors = []

    def table_of(src):
        parts = [str(x) for x in (getattr(src, 'parts', None) or [])]
        if type(src).__name__ == 'DbRef':
            return str(getattr(src, 'table', '') or '')
        if len(parts) == 2 and parts[0] == 'db':
            return parts[1]
        if isinstance(src, str) and src.startswith('db.') and src.count('.') == 1:
            return src[3:]
        return ''

    def check_join(j, parent_name, parent_table):
        jt = table_of(getattr(j, 'source', None))
        for b in (getattr(j, 'body', None) or []):
            if type(b).__name__ != 'MatchClause' or not jt or not parent_table:
                continue
            if jt not in by_table or parent_table not in by_table:
                continue    # a table no shape declares: its tags are unknown here
            val = getattr(b, 'value', None)
            parts = [str(x) for x in (getattr(val, 'parts', None) or [])]
            if len(parts) < 2 or parts[0] != str(parent_name):
                continue    # scan_join_match_must_connect reports this one
            inf, outf = str(getattr(b, 'field', '')).split('.')[-1], parts[-1]
            it, ot = by_table[jt].get(inf), by_table[parent_table].get(outf)
            if bool(it) == bool(ot):
                continue
            enc, plain = ((f'{jt}.{inf}', f'{parent_table}.{outf}') if it
                          else (f'{parent_table}.{outf}', f'{jt}.{inf}'))
            tag = it or ot
            line = getattr(b, 'line', 0) or getattr(j, 'line', 0) or 0
            errors.append(CompileError(
                f"Line {line}: this join matches `{inf}` to `{outf}`, but `{enc}` is [{tag}] and "
                f"stored encrypted while `{plain}` is not, so the database cannot compare them and "
                f"the join would match nothing.\n"
                f"    To fix it, tag `{plain.split('.')[-1]}` in `{plain.split('.')[0]}` as [{tag}] "
                f"too, or join on a key that neither table encrypts.",
                line=line))
        for nj in (b for b in (getattr(j, 'body', None) or []) if type(b).__name__ == 'JoinBlock'):
            check_join(nj, getattr(j, 'name', ''), jt)

    def walk(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                walk(c)
            return
        if not is_dataclass(node):
            return
        if type(node).__name__ in ('FindBlock', 'RetrieveBlock'):
            found = getattr(node, 'alias', None) or getattr(node, 'name', None) or ''
            ptable = table_of(getattr(node, 'source', None))
            for j in (b for b in (getattr(node, 'body', None) or []) if type(b).__name__ == 'JoinBlock'):
                check_join(j, found, ptable)
        for f in fields(node):
            walk(getattr(node, f.name, None))

    walk(program)
    return errors


_COLUMN_TEACH = (
    "Line {line}: {what} {sentence}, {risk}. Authority the server grants must be decided by the server.\n"
    "    Set it as a literal when the record is created, and change it only in a handler that requires "
    "a role, for example\n"
    "        save to db.{tbl}\n"
    "            {colname} \"member\"\n"
    "        save: done\n"
    "    and in the handler that changes it:\n"
    "        require role \"admin\"")


def scan_grant_role_client_source(program):
    """A role granted from a value the client sent refuses at check (RULED by Ron 2026-10-08, an
    ERROR everywhere, with or without a sector; extended by the M6-SEC-012 sweep to follow the value).

    `grant role` writes a VERIFIED server-side role. If the role comes from the request, the caller
    chooses their own role, which re-opens the exact forgery `grant role` exists to close. This used to
    be a warning that matched only `request.X` or the shape variable written directly in the grant, so
    `call assign / r request.role` and then `grant role r` inside the task granted it with no word at
    all. The value is now followed from where it came from: `request`, the handler's shape variable,
    and the bare field names bound from the request, through assignments and holds, expressions and
    transforms, and into every task a call passes it to, through as many tasks as it travels and back
    out through what a task gives back. Any grant whose role derives from one of them refuses.
    A role the server decides (a literal, or a field of a record looked up from the database) passes.
    The runtime backstop, for any path this misses, is in `_exec_GrantRoleDecl`.
    """
    from mohio_ast import DottedName, NewBlock, RequestInboundBlock
    errors = []
    stmts = getattr(program, 'statements', None) or []

    def nodes(root):
        stack = [root]
        while stack:
            n = stack.pop()
            if n is None or isinstance(n, (str, int, float, bool)):
                continue
            if isinstance(n, (list, tuple)):
                stack.extend(n)
                continue
            if not is_dataclass(n):
                continue
            yield n
            for f in fields(n):
                stack.append(getattr(n, f.name, None))

    tasks = {str(n.name): n for n in nodes(stmts) if type(n).__name__ == 'TaskDecl' and getattr(n, 'name', None)}

    def roots(expr):
        """Every name an expression reads, as the first part of each name it mentions -- including a
        name read inside text through `{{ }}` (RULED by Ron 2026-10-09: `hold r "{{ login.role }}"`
        carries the request's value as surely as `hold r login.role` does)."""
        out = set()
        for n in nodes(expr):
            if isinstance(n, DottedName) and getattr(n, 'parts', None):
                out.add(str(n.parts[0]).replace('__USERVAR__', ''))
            elif type(n).__name__ == 'Literal' and isinstance(getattr(n, 'value', None), str):
                for ref in _TEMPLATE_REF.findall(n.value):
                    out.add(ref.split('.')[0].strip())
        return out

    # A COLUMN A CALLER CAN WRITE (RULED by Ron 2026-10-09, item 4: the Oct 8 authority ruling
    # extended through storage). Measured on the battery's REPRO-01: `/signup` saved `role
    # profile.role` with no role required, `/login` looked the record up and granted `m.role`, and
    # `/admin` let the caller in. The role went into the database as the caller's choice and came
    # back out reading like a server-found value. A column is client-writable when any handler that
    # requires no role writes it from a request-origin value; a grant or `for person` read from one
    # refuses. Whole-program only: a write from ANOTHER program is outside this one's view.
    # key: (table, a column name) -- the column name is '*' when a whole record was saved
    # value: (where it is written, line)
    writable = {}

    def table_of(ref):
        """The `db.<table>` a write or a lookup names, in each form the transformer hands it over:
        a DottedName (`save to db.t`), a DbRef (`retrieve.one x from db.t`), or text (`save or update
        db.t`). Missing one form misses every write or lookup written that way."""
        if getattr(ref, 'table', None):
            return str(ref.table)
        if isinstance(ref, str):
            parts = ref.split('.')
        else:
            parts = [str(p) for p in (getattr(ref, 'parts', None) or [])]
        return parts[1] if len(parts) >= 2 and parts[0] == 'db' else None

    def column_origin(expr, recs):
        """The writable column an expression reads through a looked-up record, as a sentence, or None."""
        for n in nodes(expr):
            if isinstance(n, DottedName) and len(getattr(n, 'parts', None) or []) >= 2:
                alias = str(n.parts[0]).replace('__USERVAR__', '')
                t = recs.get(alias)
                col = str(n.parts[-1])
                hit = writable.get((t, col)) or writable.get((t, '*')) if t else None
                if hit:
                    return (f"`{alias}.{col}`, read from `db.{t}`, a column {hit[0]} (line {hit[1]}) "
                            f"writes from what the caller sent with no role required", t, col)
        return None

    _COLUMN = 'COLUMN::'     # marks a taint that came out of a client-writable column, not the request

    def _col_taint(col):
        """The taint a value read from a client-writable column carries: the marker, then its table and
        column, then the sentence, so every path it travels can still name all three."""
        return _COLUMN + col[1] + '::' + col[2] + '::' + col[0]

    def _col_from(origin):
        t, c, sentence = origin[len(_COLUMN):].split('::', 2)
        return sentence, t, c

    def _column_grant_error(n, which, col):
        sentence, t, c = col
        line = getattr(n, 'line', 0) or 0
        what = ("`grant role` here grants" if which == 'role' else "`for person` here signs in as")
        risk = ("so a caller could store a role and then be granted it" if which == 'role' else
                "so a caller could store a name and then be signed in as it")
        return CompileError(_COLUMN_TEACH.format(line=line, what=what, sentence=sentence, risk=risk,
                                                 tbl=t, colname=c), line=line)

    def flow(body, tainted, origin, seen, mode=None):
        """Spread `tainted` (name -> where it came from) through `body`; report every grant it reaches.
        Returns the origin of what the body gives back, when that is client-derived, or None.
        `mode` carries the pass: {'collect': True, 'gated': bool, 'where': str} records the columns
        an ungated handler writes from the request; the report pass reads them back through lookups."""
        mode = mode or {}
        collect = mode.get('collect', False)
        tainted = dict(tainted)
        given = None
        recs = {}                                # a looked-up record's name -> its table
        for _ in range(4):                       # a value can be assigned before or after it is used
            grew = False
            for n in nodes(body):
                k = type(n).__name__
                if k in ('RetrieveBlock', 'FindBlock'):
                    _t = table_of(getattr(n, 'source', None))
                    _a = getattr(n, 'alias', None) or getattr(n, 'name', None)
                    if _t and _a and str(_a) not in recs:
                        recs[str(_a)] = _t
                        grew = True          # the walk may have passed a read of it already
                if collect and not mode.get('gated'):
                    if k in ('SaveBlock', 'SaveOrUpdateBlock', 'UpdateBlock', 'SaveAllBlock'):
                        _t = table_of(getattr(n, 'target', None) if k in ('SaveBlock', 'SaveAllBlock')
                                      else getattr(n, 'source', None))
                        if _t:
                            if k == 'SaveAllBlock' and roots(getattr(n, 'source', None)) & set(tainted):
                                writable.setdefault((_t, '*'), (mode.get('where'), getattr(n, 'line', 0) or 0))
                            for fv in (getattr(n, 'fields', None) or getattr(n, 'body', None) or []):
                                if type(fv).__name__ == 'FieldValue' \
                                        and roots(getattr(fv, 'value', None)) & set(tainted):
                                    writable.setdefault((_t, str(fv.name).split('.')[-1]),
                                                        (mode.get('where'), getattr(fv, 'line', 0)
                                                         or getattr(n, 'line', 0) or 0))
                if k in ('Assignment', 'HoldDecl', 'LockDecl', 'CreateBlock') and getattr(n, 'name', None) \
                        and str(n.name) not in tainted:
                    # A record built with `create` from the request carries it too (found 2026-10-09:
                    # `create acct / name login.who` then `for person acct.name` passed check).
                    _src = getattr(n, 'body', None) if k == 'CreateBlock' else getattr(n, 'value', None)
                    hit = roots(_src) & set(tainted)
                    if hit:
                        tainted[str(n.name)] = tainted[sorted(hit)[0]]
                        grew = True
                    elif not collect:
                        _col = column_origin(_src, recs)
                        if _col:
                            tainted[str(n.name)] = _col_taint(_col)
                            grew = True
                elif k == 'RunBlock':
                    t = tasks.get(str(getattr(n, 'task_name', '')))
                    if t is None or t.name in seen:
                        continue
                    params = [str(getattr(pp, 'name', '')) for pp in (getattr(t, 'params', None) or [])]
                    into = {}
                    for arg in (getattr(n, 'args', None) or []):
                        hit = roots(getattr(arg, 'value', None)) & set(tainted)
                        if hit and str(getattr(arg, 'name', '')) in params:
                            into[str(arg.name)] = tainted[sorted(hit)[0]] + f" -> task `{t.name}`"
                        elif not collect and str(getattr(arg, 'name', '')) in params:
                            _col = column_origin(getattr(arg, 'value', None), recs)
                            if _col:
                                into[str(arg.name)] = _col_taint(_col) + f" -> task `{t.name}`"
                    inline = getattr(n, 'inline_arg', None)
                    if inline is not None and params:
                        hit = roots(inline) & set(tainted)
                        if hit:
                            into[params[0]] = tainted[sorted(hit)[0]] + f" -> task `{t.name}`"
                    if into:
                        back = flow(t.body, into, origin, seen | {t.name}, mode)
                        alias = getattr(n, 'alias', None)
                        if back and alias and str(alias) not in tainted:
                            tainted[str(alias)] = back
                            grew = True
            if not grew:
                break
        if collect:
            return None
        for n in nodes(body):
            k = type(n).__name__
            if k == 'GrantRoleDecl':
                for _which, _expr in (('person', getattr(n, 'person', None)), ('role', getattr(n, 'value', None))):
                    _col = column_origin(_expr, recs) if _expr is not None else None
                    if _col and not (roots(_expr) & set(tainted)):
                        errors.append(_column_grant_error(n, _which, _col))
            if k == 'GrantRoleDecl' and getattr(n, 'person', None) is not None:
                # THE PERSON TOO (RULED by Ron, M6-SEC-012, 2026-10-09): `for person` sets
                # session.person, which every later request and every audit record trusts, so a
                # person the caller sent is the same forgery as a role the caller sent.
                phit = roots(n.person) & set(tainted)
                if phit and tainted[sorted(phit)[0]].startswith(_COLUMN):
                    _h = sorted(phit)[0]
                    errors.append(_column_grant_error(n, 'person', _col_from(tainted[_h])))
                elif phit:
                    line = getattr(n, 'line', 0) or 0
                    errors.append(CompileError(
                        f"Line {line}: `for person` here takes the person from what the caller sent "
                        f"({tainted[sorted(phit)[0]]}), so the caller would choose who they are signed "
                        f"in as, and every later request and audit record would carry that name.\n"
                        f"    Assert a person the server found: look the account up from the database, "
                        f"check the password against it, and grant from the record, for example\n"
                        f"        retrieve.one account from db.staff\n"
                        f"            match email to login.email\n"
                        f"        retrieve.one: done\n"
                        f"        grant role \"user\" for person account.id",
                        line=line))
            if k == 'GrantRoleDecl':
                hit = roots(getattr(n, 'value', None)) & set(tainted)
                if hit and tainted[sorted(hit)[0]].startswith(_COLUMN):
                    _h = sorted(hit)[0]
                    errors.append(_column_grant_error(n, 'role', _col_from(tainted[_h])))
                elif hit:
                    line = getattr(n, 'line', 0) or 0
                    errors.append(CompileError(
                        f"Line {line}: `grant role` here takes its role from what the caller sent "
                        f"({tainted[sorted(hit)[0]]}), so the caller would pick their own role, the "
                        f"forgery `grant role` exists to close.\n"
                        f"    Grant a role the server decides: a literal (`grant role \"member\"`), or a "
                        f"field of a record you looked up (`retrieve user from db.users`, then "
                        f"`grant role user.role`).",
                        line=line))
            elif k in ('GiveBackStmt', 'GiveStmt') and given is None:
                hit = roots(getattr(n, 'value', None)) & set(tainted)
                if hit:
                    given = tainted[sorted(hit)[0]]
        return given

    handlers = []
    # Every shape by name, found once: looking it up by walking the whole program again for each
    # handler made this scan grow with handlers times program size (a third of a warm check on a
    # 146-handler app). The first declaration of a name wins, as the walk-per-handler lookup did.
    shapes = {}
    for d in nodes(stmts):
        if type(d).__name__ == 'ShapeDecl':
            shapes.setdefault(str(getattr(d, 'name', '')), d)
    for n in nodes(stmts):
        if not isinstance(n, (NewBlock, RequestInboundBlock)) or not getattr(n, 'shape', ''):
            continue
        sv = n.shape
        start = {'request': '`request`', sv: f'`{sv}`, the request'}
        shape = shapes.get(str(n.shape))
        for fld in (getattr(shape, 'fields', None) or []):
            fname = str(getattr(fld, 'name', '') or '')
            if fname and fname not in start:
                start[fname] = f'`{fname}`, a field the request sent'
        handlers.append((n, start))
    # Pass 1 collects the columns an ungated handler writes from the request; pass 2 reports.
    for n, start in handlers:
        _gated = any(type(d).__name__ == 'RequireRoleDecl' and getattr(d, 'roles', None)
                     for d in nodes(getattr(n, 'body', None) or []))
        _where = f"`{n.path}`" if getattr(n, 'path', None) else f"the `sh.{n.shape}` handler"
        flow(getattr(n, 'body', None) or [], start, n, frozenset(),
             {'collect': True, 'gated': _gated, 'where': _where})
    for n, start in handlers:
        flow(getattr(n, 'body', None) or [], start, n, frozenset())
    # one report per grant line, however many handlers reach it
    _seen, _out = set(), []
    for e in errors:
        if getattr(e, 'line', None) not in _seen:
            _seen.add(getattr(e, 'line', None))
            _out.append(e)
    return _out



def scan_session_principal_writes(program):
    """Only `grant role` sets who is signed in (RULED by Ron 2026-10-09, M6-SEC-012).

    `session.person "CEO"` checked clean and stopped only at run (read-only); it is refused here, and
    so is `session.agent`, which only an `ai.agent` sets. (Text holding `{{ }}` in a grant is judged by
    where the names inside it come from, in scan_grant_role_client_source.)"""
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
        k = type(node).__name__
        line = getattr(node, 'line', 0) or 0
        if k == 'ServiceCallStmt' and str(getattr(node, 'service', '')) == 'session' \
                and str(getattr(node, 'method', '')) in ('person', 'agent'):
            m = node.method
            errors.append(CompileError(
                f"Line {line}: `session.{m}` is read-only; this line would assign it. "
                + ("Who is signed in is set only where they sign in, by `grant role`, so no other line "
                   "can change it:\n        grant role \"user\" for person account.id"
                   if m == 'person' else
                   "It is set only while an `ai.agent` acts, never by a program line."),
                line=line))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    walk(program)
    return errors


def scan_ai_reads_upload(program):
    """An uploaded file handed to an AI primitive refuses at check (RULED by Ron 2026-10-09).

    The AI primitives read text. An upload field holds the stored file's NAME once the request is
    bound, so `weigh doc.photo` sent the model a file name and the model answered as if it had seen
    the receipt: it checked clean and answered plausibly. Every AI block is checked for an upload
    field named through the handler's shape variable (`doc.photo`), `request.photo` or the bare
    field (`photo`), as a weighed input, its context, or an `ai.create ... from` source.
    """
    from mohio_ast import DottedName, NewBlock, RequestInboundBlock
    UPLOAD = ('file', 'image', 'audio', 'video', 'pdf')
    AI_BLOCKS = ('AiDecideBlock', 'AiCompareBlock', 'AiRespondBlock', 'AiCreateStmt', 'AiAgentBlock',
                 'AiExplainBlock', 'AiResolveBlock', 'AiRankBlock')
    errors = []
    stmts = getattr(program, 'statements', None) or []

    def nodes(root):
        stack = [root]
        while stack:
            n = stack.pop()
            if n is None or isinstance(n, (str, int, float, bool)):
                continue
            if isinstance(n, (list, tuple)):
                stack.extend(n)
                continue
            if not is_dataclass(n):
                continue
            yield n
            for f in fields(n):
                stack.append(getattr(n, f.name, None))

    shapes = {str(d.name): d for d in nodes(stmts) if type(d).__name__ == 'ShapeDecl' and getattr(d, 'name', '')}

    def named(value):
        """The dotted spelling of a reference, or None when it is not a plain reference."""
        if isinstance(value, DottedName) and getattr(value, 'parts', None):
            return '.'.join(str(p).replace('__USERVAR__', '') for p in value.parts)
        if isinstance(value, str) and value.strip() and ' ' not in value.strip():
            return value.strip()
        return None

    for h in nodes(stmts):
        if not isinstance(h, (NewBlock, RequestInboundBlock)) or not getattr(h, 'shape', ''):
            continue
        shape = shapes.get(str(h.shape))
        uploads = {str(f.name) for f in (getattr(shape, 'fields', None) or [])
                   if str(getattr(f, 'type_name', '') or '').lower() in UPLOAD}
        if not uploads:
            continue
        sv = h.shape
        refs = {f'{sv}.{u}': u for u in uploads}
        refs.update({f'request.{u}': u for u in uploads})
        refs.update({u: u for u in uploads})
        for ai in nodes(getattr(h, 'body', None) or []):
            kind = type(ai).__name__
            if kind not in AI_BLOCKS:
                continue
            used = []
            for w in nodes(getattr(ai, 'body', None) or []):
                if type(w).__name__ == 'WeighClause':
                    used += [named(i) for i in (getattr(w, 'inputs', None) or [])]
            used += [named(getattr(ai, 'context', None)), named(getattr(ai, 'source', None))]
            hit = next((u for u in used if u in refs), None)
            if hit is None:
                continue
            verb = 'ai.' + kind[2:].replace('Block', '').replace('Stmt', '').lower()
            errors.append(CompileError(
                f"Line {getattr(ai, 'line', 0) or 0}: `{verb}` is given `{hit}`, an uploaded file. AI "
                f"primitives read text; reading a document or image is mioai.extract, which is "
                f"designed but not built yet. Once the "
                f"request is bound, `{hit}` holds the stored file's name, so the model would answer "
                f"about the name and not the file.\n    To fix it, weigh text instead, such as a "
                f"field the person typed, and leave the file out of the AI call.",
                line=getattr(ai, 'line', 0) or 0))
    return errors


def scan_mongo_tagged_data(program):
    """Tagged data on MongoDB refuses at check while Mohio keeps no audit trail there (ruling 2,
    Ron 2026-10-09, T1-MONGO-AUDIT-TRAIL). Only a connect that NAMES MongoDB is known here; a
    connect whose engine comes from its address is refused at startup with the same message."""
    from mohio_ast import mongo_tagged_refusal, mongo_ai_audit_refusal
    tagged = _classified_field_names(program)
    audit_logs, connects = set(), []

    def walk(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                walk(c)
            return
        if not is_dataclass(node):
            return
        if type(node).__name__ == 'ConnectDecl' and str(getattr(node, 'driver', '') or '').lower() in ('mongodb', 'mongo'):
            connects.append(getattr(node, 'line', 0) or 0)
        if type(node).__name__ == 'AiAuditStmt' and getattr(node, 'log_name', None):
            audit_logs.add(str(node.log_name))
        if type(node).__name__ == 'SectorDecl':
            label = str(getattr(node, 'sector', '') or '')
            if label:
                graded.append((f"sector `{', '.join(label.split('.'))}`", _sector_frameworks(label)[0]))
        if type(node).__name__ == 'ComplianceDecl' and getattr(node, 'framework', None):
            graded.append((f"`compliance: {node.framework}`", [str(node.framework)]))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    graded = []
    walk(program)
    from mohio_audit_grades import required_grade
    from mohio_ast import mongo_sector_audit_refusal
    errors = []
    for line in connects:
        if tagged:
            errors.append(CompileError(mongo_tagged_refusal(list(tagged), line), line=line))
        if audit_logs:
            errors.append(CompileError(mongo_ai_audit_refusal(list(audit_logs), line), line=line))
        for label, fw in graded:
            grade = required_grade(fw)[0] if fw else 'none'
            if grade != 'none':
                errors.append(CompileError(mongo_sector_audit_refusal(fw, grade, line, label), line=line))
    return errors


def scan_when_is_as_field(program):
    """`when actor.is` inside a check reads `is` as a FIELD of actor, so it is neither a comparison
    nor a value; it checked clean and stopped at run on "'is' is not a field" (RULED by Ron
    2026-10-09, from the Dispatch app). Refused here, naming the enclosing check's subject."""
    from mohio_ast import when_is_field_message
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
        if type(node).__name__ == 'CheckBlock':
            subj = getattr(node, 'value', None)
            subject = ('.'.join(subj.parts) if type(subj).__name__ == 'DottedName' else None)
            for w in (getattr(node, 'when_clauses', None) or []):
                v = getattr(w, 'value', None)
                parts = list(getattr(v, 'parts', None) or []) if type(v).__name__ == 'DottedName' else []
                if len(parts) > 1 and 'is' in parts[1:]:
                    owner = '.'.join(parts[:parts.index('is', 1)])
                    line = getattr(w, 'line', 0) or 0
                    errors.append(CompileError(when_is_field_message(line, owner, subject), line=line))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    walk(program)
    return errors


def scan_using_undeclared_chain(program):
    """`using <chain>` names a chain some `ai.connect` in the program declares (RULED by Ron
    2026-10-09). With none, the call has no provider to reach: the real runtime refuses it when the
    call runs, which is a request failing in production for a name that could be read off the page.
    Refused here, naming the chain and the chains that are declared; the runtime refusal stays as
    the backstop."""
    declared, uses = [], []

    def walk(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                walk(c)
            return
        if not is_dataclass(node):
            return
        if type(node).__name__ == 'AiConnectBlock':
            declared.extend(str(n) for n in (getattr(node, 'names', None) or []))
        body = getattr(node, 'body', None)
        if isinstance(body, list):
            for b in body:
                if type(b).__name__ == 'UsingChain' and getattr(b, 'chain_name', None):
                    uses.append((node, b))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    walk(program)
    errors = []
    for owner, using in uses:
        chain = str(using.chain_name)
        if chain in declared:
            continue
        line = getattr(using, 'line', 0) or getattr(owner, 'line', 0) or 0
        names = sorted(set(declared))
        if names:
            known = (f"    Chains declared here: {', '.join(f'`{d}`' for d in names)}.\n"
                     f"    To fix it, name one of those, or declare the chain:\n")
        else:
            known = "    No chain is declared in this program. To fix it, declare one:\n"
        errors.append(CompileError(
            f"Line {line}: `using {chain}` names an AI chain, but no `ai.connect {chain}` is declared "
            f"in this program, so the call has no provider to reach and would fail when it runs.\n"
            + known +
            f"        ai.connect {chain}\n"
            f"            order\n"
            f"                <provider> model \"<model>\"\n"
            f"            order: done\n"
            f"        ai.connect: done",
            line=line))
    return errors


def scan_sign_in_outside_password_match(program):
    """Sign-in happens inside the branch where the password matched (RULED by Ron 2026-10-09).

    A `grant role` (which starts the signed-in session) or a `jump to` (the sign-in redirect) that
    follows a `check ... against` but sits outside its `on.success` runs whether or not the password
    matched. The Dispatch shape that showed it closed the check block one line early, so the grant
    after it signed in anyone who knew a staff email. It refuses here, naming both lines: one that
    FOLLOWS the check in the same block, and one inside the check's `on.failure` or loose in its body.
    """
    errors = []
    SIGN_IN = ('GrantRoleDecl', 'JumpToStmt')

    def nodes(root):
        stack = [root]
        while stack:
            n = stack.pop()
            if n is None or isinstance(n, (str, int, float, bool)):
                continue
            if isinstance(n, (list, tuple)):
                stack.extend(n)
                continue
            if not is_dataclass(n):
                continue
            yield n
            for f in fields(n):
                stack.append(getattr(n, f.name, None))

    def what(n):
        return '`grant role`' if type(n).__name__ == 'GrantRoleDecl' else '`jump to`'

    def flag(check, later):
        cl = getattr(check, 'line', 0) or 0
        ll = getattr(later, 'line', 0) or 0
        errors.append(CompileError(
            f"Line {ll}: {what(later)} signs someone in after the password check on line {cl}, but "
            f"outside the branch where the password matched, so it runs whether or not it did. "
            f"Sign-in must happen inside the match.\n    To fix it, move it under `on.success` of the "
            f"`check ... against` on line {cl}, for example:\n"
            f"        check password against account.password_hash\n"
            f"            on.success\n"
            f"                grant role \"staff\" for person account.id\n"
            f"        check: done",
            line=ll))

    def lists_in(root):
        for n in nodes(root):
            for f in fields(n):
                v = getattr(n, f.name, None)
                if isinstance(v, list) and v:
                    yield v

    seen = set()
    for stmts in [getattr(program, 'statements', None) or []] + list(lists_in(program)):
        for i, st in enumerate(stmts):
            if type(st).__name__ != 'CheckAgainstStmt' or id(st) in seen:
                continue
            seen.add(id(st))
            for later in stmts[i + 1:]:
                for n in nodes(later):
                    if type(n).__name__ in SIGN_IN:
                        flag(st, n)
            for part in (getattr(st, 'body', None) or []):
                if type(part).__name__ == 'OnSuccess':
                    continue
                for n in nodes(part):
                    if type(n).__name__ in SIGN_IN:
                        flag(st, n)
    return errors


def scan_handler_reads_file_level_name(program):
    """A handler that reads a name set only by a plain statement at file top level (the Volarix login
    finding, 2026-10-12; T1-MIOCHECK-UNDECLARED-NAME on a security path). A served handler runs on
    its own, on the file's DECLARATIONS (shapes, tasks, connections, holds and locks, loaded before
    any request); a plain top-level value (`greeting "hi"`) or a top-level capture
    (`hash env.X as decoy_hash using bcrypt`) is a statement `mio run` runs and a server does not, so
    the read answered 500 `undeclared_variable` on every request while check passed.

    Narrow on purpose: only names a top-level Assignment or HashBlock sets, read inside a handler that
    never sets the name itself. A hold, a lock, a task or a shape is never a candidate."""
    from mohio_ast import DottedName
    HANDLERS = ('NewBlock', 'RequestInboundBlock', 'ChangeBlock', 'ConnectionBlock')
    top_only = {}
    for st in (getattr(program, 'statements', None) or []):
        k = type(st).__name__
        if k == 'Assignment' and getattr(st, 'name', None):
            top_only.setdefault(str(st.name), getattr(st, 'line', 0) or 0)
        elif k == 'HashBlock' and getattr(st, 'alias', None):
            top_only.setdefault(str(st.alias), getattr(st, 'line', 0) or 0)
    if not top_only:
        return []
    errors = []

    def nodes(n):
        if n is None or isinstance(n, (str, int, float, bool)):
            return
        if isinstance(n, (list, tuple)):
            for c in n:
                yield from nodes(c)
            return
        if not is_dataclass(n):
            return
        yield n
        for f in fields(n):
            yield from nodes(getattr(n, f.name, None))

    for h in nodes(getattr(program, 'statements', None) or []):
        if type(h).__name__ not in HANDLERS:
            continue
        inner = list(nodes(getattr(h, 'body', None) or []))
        assigned = set()
        for n in inner:
            for attr in ('name', 'alias', 'var', 'variable', 'item'):
                v = getattr(n, attr, None)
                if isinstance(v, str):
                    assigned.add(v)
        seen = set()
        for n in inner:
            if not isinstance(n, DottedName):
                continue
            parts = getattr(n, 'parts', None) or []
            name = str(parts[0]) if parts else ''
            if name in top_only and name not in assigned:
                if name in seen:
                    continue
                seen.add(name)
                errors.append(CompileError(
                    f"this handler reads `{name}`, which is set only by a statement at the top of the "
                    f"file (line {top_only[name]}). A served handler runs on its own, with the file's "
                    f"declarations loaded (shapes, tasks, connections, holds and locks) and not its "
                    f"statements, so this read would answer 500 on every request.",
                    getattr(n, 'line', 0) or getattr(h, 'line', 0) or 0,
                    hint=(f"Hold it at the top of the file so every request can read it "
                          f"(`hold {name} <value>`), or set it inside the handler.")))
    return errors


def scan_give_back_as_word(program):
    """`give back <value> as <word>`: the word must be a response format (json, xml, text, html).
    Any other word was dropped and the value went out as JSON with nothing said; a declared shape's
    name there read like a promise the response is checked against the shape, which is declared but
    not built (T1-WRITE-PATH-ENFORCEMENT-ALL-CONTEXTS). Mirrors `_refuse_give_back_as_word`."""
    shapes = set()
    gbs = []

    def walk(n):
        if n is None or isinstance(n, (str, int, float, bool)):
            return
        if isinstance(n, (list, tuple)):
            for c in n:
                walk(c)
            return
        if not is_dataclass(n):
            return
        k = type(n).__name__
        if k == 'ShapeDecl' and getattr(n, 'name', None):
            shapes.add(str(n.name))
        elif k == 'GiveBackStmt' and isinstance(getattr(n, 'modifier', None), str):
            gbs.append(n)
        for f in fields(n):
            walk(getattr(n, f.name, None))
    walk(getattr(program, 'statements', None) or [])
    errors = []
    for gb in gbs:
        word = str(gb.modifier)
        if word.lower() in ('json', 'xml', 'text', 'html'):
            continue
        if word in shapes:
            errors.append(CompileError(
                f"(T1-WRITE-PATH-ENFORCEMENT-ALL-CONTEXTS) `give back ... as {word}` names the shape "
                f"`{word}`, and checking a response against its shape is declared but not built in "
                f"this release: the response would go out unchecked, with any field outside its "
                f"shape's rules.", getattr(gb, 'line', 0) or 0,
                hint=f"Give the value back without `as {word}`, or check its fields before giving it back."))
        else:
            errors.append(CompileError(
                f"`as {word}` is not a response format, so this would be given back as JSON with "
                f"nothing said. Mohio gives back as json, xml, text or html.",
                getattr(gb, 'line', 0) or 0,
                hint=f"Write `as json`, `as xml`, `as text` or `as html`, or drop `as {word}`."))
    return errors


def scan_required_certified_sector(program):
    """A deployment that requires certified sectors runs no program that declares none (Ron, 2026-10-13,
    from the sector feasibility review, P16). MOHIO_REQUIRE_CERTIFIED_SECTORS was consulted only for a
    sector the program declared, so a program that simply left out `sector:` ran with no sector rules in
    a deployment that requires them. Judged on the assembled program, so a `sector:` in the journey counts."""
    from mohio_sector_loader import _required_certified_set, REQUIRE_CERTIFIED_ENV
    required = _required_certified_set()
    if not required:
        return []
    if any(type(n).__name__ == 'SectorDecl' for n in _all_nodes(program)):
        return []
    named = sorted(r for r in required if r not in ('*', 'all'))
    which = ("every sector" if not named else ", ".join(named))
    first = named[0] if named else "<sector>"
    return [CompileError(
        f"Line 1: {REQUIRE_CERTIFIED_ENV} is set ({which}), so this deployment requires a certified sector "
        f"profile, and this program declares no `sector:` line, so no sector rules would apply to it at all",
        line=1,
        hint=(f"Declare the sector this deployment requires, at the top of the program (or its journey):\n"
              f"        sector: {first}\n"
              f"    or remove {REQUIRE_CERTIFIED_ENV} where the deployment is not regulated."))]


def _is_zero(v):
    try:
        return float(v) == 0
    except (TypeError, ValueError):
        return False


def _numeric_type(type_name):
    tn = str(type_name or '').lower()
    return (tn in ('int', 'integer', 'dec', 'decimal', 'number')
            or tn.startswith('dec.') or tn.startswith('decimal.'))


_TEXT_CONDS = {'contains': 'contains', 'starts': 'starts with', 'starts_with': 'starts with',
               'ends': 'ends with', 'ends_with': 'ends with', 'minlen': 'is at least N characters',
               'maxlen': 'is at most N characters'}


# A declared type whose values are never an amount (value kinds, money right-operand class, RULED by
# Ron 2026-10-16): each is refused against money, like text and another currency already are.
_OTHER_KIND_OF_TYPE = {'boolean': 'boolean', 'bool': 'boolean', 'date': 'date', 'datetime': 'datetime',
                       'time': 'datetime', 'uuid': 'uuid', 'list': 'list', 'text': 'text',
                       'text.long': 'text', 'email': 'text'}
# How each kind is named in a refusal.
_KIND_WORDS = {'boolean': 'a true-or-false', 'empty': 'empty text', 'null': 'no value',
               'date': 'a date', 'datetime': 'a date and time', 'uuid': 'a uuid', 'list': 'a list',
               'text': 'text'}


def scan_value_kinds_known(program):
    """Values of different kinds never compare silently, refused AT CHECK where the kinds are
    known (RULED by Ron, 2026-10-13; run-time refusal in `_refuse_kind_clash` covers the rest).

    Known here means written in the program: a name declared `as <CURRENCY>`, a name only ever
    given quoted text, a quoted literal, and a field a table's shape declares as a currency (read
    through a lookup's result, or named in a query's `match` / `where`). An amount compared with
    text, or with an amount in another currency, is refused; an amount against a plain number,
    and anything whose kind depends on a value at run, are left to run time."""
    from lark import Tree, Token
    from mohio_ast import DottedName as DottedName_
    from mohio_runtime_vocab import CURRENCIES      # the table the runtime reads, not the interpreter
    currencies = {str(c).upper() for c in CURRENCIES}
    nodes = list(_all_nodes(program))

    kinds = {}                                     # name -> set of kinds it is ever given
    for n in nodes:
        if type(n).__name__ != 'Assignment' or not getattr(n, 'name', None):
            continue
        tn = str(getattr(n, 'type_name', '') or '').upper()
        v = getattr(n, 'value', None)
        if tn in currencies:
            k = ('money', tn)
        elif _OTHER_KIND_OF_TYPE.get(tn.lower()):
            k = (_OTHER_KIND_OF_TYPE[tn.lower()], '')
        elif tn:
            k = ('other', tn)
        elif type(v).__name__ == 'Literal' and getattr(v, 'literal_type', '') == 'bool':
            k = ('boolean', '')
        elif type(v).__name__ == 'Literal' and getattr(v, 'literal_type', '') == 'string':
            k = ('text', '')
        elif type(v).__name__ == 'Literal' and getattr(v, 'literal_type', '') == 'number':
            k = ('number', '0' if _is_zero(getattr(v, 'value', None)) else '')
        else:
            k = ('unknown', '')
        kinds.setdefault(str(n.name), set()).add(k)
    # A DECLARED TYPE WINS over the values later given to the name: `d as datetime` then
    # `d "2020-01-01T00:00:00"` is a date (the assignment is checked against the type), not text.
    for _nm, _ks in list(kinds.items()):
        _decl = {k for k in _ks if k[0] in ('money',) or k[0] in _KIND_WORDS and k[0] != 'text'
                 and k[0] != 'boolean'}
        _decl = {k for k in _decl if k[0] in ('datetime', 'date', 'uuid', 'list')}
        if len(_decl) == 1:
            kinds[_nm] = _decl

    table_money = {}                               # table -> {field: CODE}
    table_other = {}                               # table -> {field: kind}, kinds never an amount
    for n in nodes:
        if type(n).__name__ != 'ShapeDecl':
            continue
        for tname, t in (getattr(n, 'tables', None) or {}).items():
            for f in (getattr(t, 'fields', None) or []):
                ft = str(getattr(f, 'type_name', '') or getattr(f, 'field_type', '') or '').upper()
                if ft in currencies and getattr(f, 'name', None):
                    table_money.setdefault(str(tname), {})[str(f.name)] = ft
                elif _OTHER_KIND_OF_TYPE.get(ft.lower()) and getattr(f, 'name', None):
                    table_other.setdefault(str(tname), {})[str(f.name)] = _OTHER_KIND_OF_TYPE[ft.lower()]
    shape_money = {}                               # shape -> {field: CODE}, read as `Pay.amount`
    for n in nodes:
        if type(n).__name__ != 'ShapeDecl':
            continue
        for f in (getattr(n, 'fields', None) or []):
            ft = str(getattr(f, 'type_name', '') or '').upper()
            if ft in currencies and getattr(f, 'name', None):
                shape_money.setdefault(str(getattr(n, 'name', '')), {})[str(f.name)] = ft
    # A NUMBER FROM ANY DECLARED SOURCE (RULED by Ron, 2026-10-14, value kinds 2e): a column, a
    # request-shape field, a typed take. Volarix compared `challenge.amount` (a column declared as a
    # number) with an EUR amount and it passed check.
    table_number, shape_number = {}, {}
    for n in nodes:
        if type(n).__name__ != 'ShapeDecl':
            continue
        for tname, t in (getattr(n, 'tables', None) or {}).items():
            for f in (getattr(t, 'fields', None) or []):
                ft = str(getattr(f, 'type_name', '') or getattr(f, 'field_type', '') or '')
                if _numeric_type(ft) and getattr(f, 'name', None):
                    table_number.setdefault(str(tname), set()).add(str(f.name))
        for f in (getattr(n, 'fields', None) or []):
            ft = str(getattr(f, 'type_name', '') or '')
            if _numeric_type(ft) and getattr(f, 'name', None):
                shape_number.setdefault(str(getattr(n, 'name', '')), set()).add(str(f.name))

    def _shape_field_kind(shape, field):
        shape = str(shape or '')
        shape = shape[3:] if shape.startswith('sh.') else shape
        code = shape_money.get(shape, {}).get(field)
        if code:
            return ('money', code)
        return ('number', '') if field in shape_number.get(shape, set()) else None

    # kinds that depend on WHERE a name is read: `request.amount` inside a handler for a shape, and a
    # typed take inside its task (`take p as sh.Pay` makes `p.amount` that shape's field)
    node_kind = {}
    for n in nodes:
        t = type(n).__name__
        if t in ('NewBlock', 'RequestInboundBlock') and getattr(n, 'shape', None):
            for d in _all_nodes(n):
                parts = [str(x) for x in (getattr(d, 'parts', None) or [])] \
                    if type(d).__name__ == 'DottedName' else []
                if len(parts) == 2 and parts[0] == 'request':
                    k = _shape_field_kind(n.shape, parts[1])
                    if k:
                        node_kind[id(d)] = k
        elif t == 'TaskDecl':
            params = {}
            for prm in (getattr(n, 'params', None) or []):
                ptn = str(getattr(prm, 'type_name', '') or '')
                if not getattr(prm, 'name', None) or not ptn:
                    continue
                if ptn.upper() in currencies:
                    params[str(prm.name)] = ('money', ptn.upper())
                elif _numeric_type(ptn):
                    params[str(prm.name)] = ('number', '')
                elif ptn.startswith('sh.') or ptn in shape_money or ptn in shape_number:
                    params[str(prm.name)] = ('shape', ptn)
            if not params:
                continue
            for d in _all_nodes(n):
                parts = [str(x) for x in (getattr(d, 'parts', None) or [])] \
                    if type(d).__name__ == 'DottedName' else []
                if len(parts) == 1 and parts[0] in params and params[parts[0]][0] != 'shape':
                    node_kind[id(d)] = params[parts[0]]
                elif len(parts) == 2 and parts[0] in params and params[parts[0]][0] == 'shape':
                    k = _shape_field_kind(params[parts[0]][1], parts[1])
                    if k:
                        node_kind[id(d)] = k

    from mohio_runtime_vocab import LIST_RESULT_PROPERTIES
    lookups = {}
    list_lookups = set()           # result names holding a LIST: `.count` there is the built-in
    for n in nodes:
        if type(n).__name__ not in ('RetrieveBlock', 'FindBlock', 'GrabBlock'):
            continue
        src = getattr(n, 'source', None)
        parts = [str(p) for p in (getattr(src, 'parts', None) or [])] if src is not None else []
        table = str(getattr(src, 'table', None) or (parts[-1] if parts else ''))
        is_list = (type(n).__name__ == 'FindBlock'
                   or str(getattr(n, 'modifier', '') or '') in ('all', 'every'))
        for nm in (getattr(n, 'name', None), getattr(n, 'alias', None)):
            if nm:
                lookups[str(nm)] = table
                if is_list:
                    list_lookups.add(str(nm))

    def kind_of(node):
        """('money', CODE), ('text', ''), or None when the kind is not known at check."""
        if isinstance(node, Token):
            if node.type == 'STRING':
                return ('text', '') if str(node).strip('"').strip() else ('empty', '')
            if node.type in ('TRUE', 'FALSE'):
                return ('boolean', '')
            if node.type in ('NULL_KW', 'NONE_KW'):
                return ('null', '')
            return None
        t = type(node).__name__
        if t == 'Literal':
            lt = getattr(node, 'literal_type', '')
            if lt == 'number':
                return ('number', '0' if _is_zero(getattr(node, 'value', None)) else '')
            if lt == 'bool':
                return ('boolean', '')
            if lt == 'null':
                return ('null', '')
            if lt == 'string':
                # A WRITTEN empty "" is a value of its own here, refused against an amount; asking
                # whether an amount has no value is `is empty`, a different construct (RULED 2026-10-16).
                return ('text', '') if str(getattr(node, 'value', '') or '').strip() else ('empty', '')
            return None
        if t == 'TimeExpr':
            return ('datetime', '')
        if t == 'DottedName' and id(node) in node_kind:
            return node_kind[id(node)]
        if t == 'DottedName':
            parts = [str(p) for p in (getattr(node, 'parts', None) or [])]
            if len(parts) == 1:
                ks = kinds.get(parts[0], set())
                money = {k for k in ks if k[0] == 'money'}
                if len(money) == 1 and all(k[0] in ('money', 'number') for k in ks):
                    return next(iter(money))
                if ks and all(k[0] == 'text' for k in ks):
                    return ('text', '')
                if ks and all(k[0] == 'number' or (k[0] == 'other' and _numeric_type(k[1]))
                              for k in ks):
                    return ('number', '0' if all(k == ('number', '0') for k in ks) else '')
                cats = {k[0] for k in ks}
                if len(cats) == 1 and next(iter(cats)) in _KIND_WORDS:
                    return (next(iter(cats)), '')
            elif (len(parts) == 2 and parts[0] in list_lookups
                  and parts[1] in LIST_RESULT_PROPERTIES):
                # A list result's own property, never the column of the same name (EE-HC-02):
                # `items.count` is not money even when the table has a money `count` column.
                return None
            elif len(parts) == 2 and parts[0] in lookups:
                code = table_money.get(lookups[parts[0]], {}).get(parts[1])
                if code:
                    return ('money', code)
                if parts[1] in table_number.get(lookups[parts[0]], set()):
                    return ('number', '')
                ok = table_other.get(lookups[parts[0]], {}).get(parts[1])
                if ok:
                    return (ok, '')
            elif len(parts) == 2 and (parts[0] in shape_money or parts[0] in shape_number):
                # a request-shape field declared as a currency or a number (`Pay.amount`, shape Pay)
                return _shape_field_kind(parts[0], parts[1])
        return None

    def _quoted(node):
        return (isinstance(node, Token) and node.type == 'STRING') or \
            (type(node).__name__ == 'Literal' and getattr(node, 'literal_type', '') == 'string')

    def label(node):
        if isinstance(node, Token):
            return str(node)
        if type(node).__name__ == 'Literal':
            if getattr(node, 'literal_type', '') == 'number':
                return str(node.value)
            if getattr(node, 'literal_type', '') == 'bool':
                return 'true' if node.value else 'false'
            if getattr(node, 'literal_type', '') == 'null':
                return 'null'
            return f'"{node.value}"'
        if type(node).__name__ == 'TimeExpr':
            return '`' + str(getattr(node, 'base', 'now()')) + '`'
        return '`' + '.'.join(str(p) for p in node.parts) + '`'

    errors = []
    # a when clause's check subject, for the subject-less `when contains "x"` form
    check_subject = {}
    for n in nodes:
        if type(n).__name__ == 'CheckBlock':
            for w in (getattr(n, 'when_clauses', None) or []):
                check_subject[id(w)] = getattr(n, 'value', None)

    def refuse_bare_number(a, b, ka, kb, line, verb):
        # MONEY AGAINST A BARE NUMBER (RULED by Ron, 2026-10-13, correcting the earlier allow):
        # zero has no currency and is allowed; any other bare number is "1000 what?", because the
        # coder may mean another currency (Volarix submit-payment.mho compared a bare number with
        # EUR where BHD was meant).
        money, num = (a, b) if ka[0] == 'money' else (b, a)
        code = ka[1] if ka[0] == 'money' else kb[1]
        if (kb if ka[0] == 'money' else ka)[1] == '0':
            return
        if type(num).__name__ == 'DottedName' and kind_of(num) and kind_of(num)[1] != '0' \
                and not (len(num.parts) == 1 and kinds.get(str(num.parts[0]))):
            # a column, a request field or a take declared as a number (RULED 2026-10-14)
            _fld = str(num.parts[-1])
            errors.append(CompileError(
                f"Line {line}: {label(money)} is an amount in {code} and {label(num)} is declared as a "
                f"number, {verb} it. A number has no currency; declare it with its currency.",
                line=line,
                hint=(f"Declare the field as {code} where it is described, for example `{_fld} as {code}`, "
                      f"so it carries its currency. Zero needs no currency.")))
            return
        errors.append(CompileError(
            f"Line {line}: {label(money)} is an amount in {code} and {label(num)} is a bare number "
            f"{verb} it. {label(num)} what? A number with no currency could mean any currency.",
            line=line,
            hint=(f"Write it with its currency, for example declare `limit as {code}` then "
                  f"`limit {label(num).strip('`')}`, and use `limit`. Zero needs no currency.")))

    def refuse_text_on_amount(node, op, code, line):
        # TEXT OPERATIONS ON AN AMOUNT REFUSE (RULED by Ron, 2026-10-14), at check where the amount
        # is known; the run-time half is `_refuse_text_on_amount`.
        name = label(node).strip('`')
        errors.append(CompileError(
            f"Line {line}: `{name}` is an amount in {code}, and `{op}` works on text. An amount "
            f"isn't text; join it with & first.",
            line=line,
            hint=(f"Make the text first, then use it: `label (\"\" & {name})`, then `label {op}`. The "
                  f"text is the amount as it shows, with its currency.")))

    def judge(a, b, line):
        ka, kb = kind_of(a), kind_of(b)
        if not ka or not kb:
            return
        if ka[0] == 'money' and kb[0] == 'money' and ka[1] != kb[1]:
            errors.append(CompileError(
                f"Line {line}: {label(a)} is an amount in {ka[1]} and {label(b)} is an amount in "
                f"{kb[1]}. Amounts in different currencies have no fixed rate between them, so "
                f"they cannot be compared.",
                line=line,
                hint=(f"Compare amounts in the same currency, for example {label(a)} with another "
                      f"{ka[1]} amount. Mohio does not convert between currencies yet.")))
            return
        if {ka[0], kb[0]} == {'money', 'number'}:
            refuse_bare_number(a, b, ka, kb, line, "compared with")
            return
        if 'money' in (ka[0], kb[0]) and (kb if ka[0] == 'money' else ka)[0] in _KIND_WORDS \
                and (kb if ka[0] == 'money' else ka)[0] != 'text':
            # AN AMOUNT AGAINST A VALUE OF ANOTHER KIND (RULED by Ron, 2026-10-16, value kinds money
            # class, VALUE-KINDS-MATRIX-R0): a true-or-false, a written "" or null, a date, a uuid,
            # a list. `when amount is more than true` coerced the boolean into a number; `is ""`
            # and `is true` said no. Asking whether an amount has no value is `is empty`.
            money, other = (a, b) if ka[0] == 'money' else (b, a)
            code, okind = (ka[1], kb[0]) if ka[0] == 'money' else (kb[1], ka[0])
            name = label(money).strip('`')
            errors.append(CompileError(
                f"Line {line}: {label(money)} is an amount in {code} and {label(other)} is "
                f"{_KIND_WORDS[okind]}, so the comparison has no answer, and at run it would refuse. Use a value of the same kind.",
                line=line,
                hint=(f"To ask whether the amount has no value, write `when {name} is empty`."
                      if okind in ('empty', 'null') else
                      f"Compare it with an amount in {code}, for example `when {name} is more than "
                      f"limit` with `limit as {code}`.")))
            return
        if {ka[0], kb[0]} == {'number', 'text'}:
            # A NUMBER AGAINST QUOTED TEXT IN SOURCE (RULED by Ron, 2026-10-13; proposal case 14):
            # the coder wrote the number as text. A value that arrives as text (a form, a row, env,
            # JSON) still converts at its boundary; only a quoted literal is refused here.
            num, text = (a, b) if ka[0] == 'number' else (b, a)
            if _quoted(text):
                raw = str(text).strip('"') if isinstance(text, Token) else str(text.value)
                fix = raw.strip() if re.fullmatch(r'[+-]?(\d+(\.\d*)?|\.\d+)', raw.strip()) else '5'
                errors.append(CompileError(
                    f"Line {line}: {label(num)} is a number and {label(text)} is quoted text, so "
                    f"this compares a number with text.",
                    line=line,
                    hint=(f"Write the number without quotes, for example {fix} instead of "
                          f"{label(text)}.")))
            return
        if {ka[0], kb[0]} == {'money', 'text'}:
            money, text = (a, b) if ka[0] == 'money' else (b, a)
            code = ka[1] if ka[0] == 'money' else kb[1]
            errors.append(CompileError(
                f"Line {line}: {label(money)} is an amount in {code} and {label(text)} is text. "
                f"Text is not an amount, so the comparison has no answer, and at run it would "
                f"refuse.",
                line=line,
                hint=(f"Compare it with an amount, for example a number with no quotes "
                      f"(`125000.00`), or declare the text's value as {code} so it converts.")))
            return
        cross = _cross_kind_refusal(a, b, ka, kb)
        if cross:
            errors.append(CompileError(
                f"Line {line}: {label(a)} is {_kind_phrase(ka[0])} and {label(b)} is "
                f"{_kind_phrase(kb[0])}, so the comparison has no answer, and at run it would refuse. Use a value of the same kind.",
                line=line, hint=cross))

    def _cross_kind_refusal(a, b, ka, kb):
        """THE GENERAL RULE at check (RULED 2026-10-14, built across the whole axis 2026-10-16): two
        written values of different kinds refuse; returns the fix hint, or None when allowed. Money
        and the number-against-text rule are judged above; what stays allowed here is the same kind,
        a date against a date and time, and a uuid against text that is a canonical uuid."""
        x, y = ka[0], kb[0]
        if x == y or {x, y} & {'money', 'unknown'} or {x, y} == {'number', 'text'}:
            return None
        if {x, y} <= {'date', 'datetime'}:
            return None
        if {x, y} == {'uuid', 'text'}:
            txt = b if y == 'text' else a
            if not _quoted(txt):
                return None                        # text known only at run: the run decides
            import uuid as _uuid
            raw = str(txt).strip('"') if isinstance(txt, Token) else str(getattr(txt, 'value', ''))
            try:
                if str(_uuid.UUID(raw)) == raw:
                    return None
            except ValueError:  # SILENT-OK: not a uuid, so the pair refuses
                pass
        if 'null' in (x, y) or 'empty' in (x, y):
            return "To ask whether a value has no value, write `is empty`."
        return f"Compare it with {_kind_phrase(x)}; values of different kinds never compare."

    def _reads_as_date(node):
        import datetime as _dt
        raw = str(node).strip('"') if isinstance(node, Token) else str(getattr(node, 'value', ''))
        try:
            _dt.datetime.fromisoformat(raw.strip().replace('Z', '+00:00'))
            return True
        except ValueError:
            return False

    def _kind_phrase(k):
        return {'number': 'a number', 'boolean': 'a true-or-false', 'date': 'a date',
                'datetime': 'a date and time', 'uuid': 'a uuid', 'list': 'a list', 'text': 'text',
                'empty': 'empty text', 'null': 'no value'}.get(k, k)

    compare_ops = {'is', '==', '=', 'is not', '!=', '>', '<', '>=', '<=', 'above', 'below'}
    pair_trees = {'wc_is', 'wc_not_is', 'wc_above', 'wc_below', 'wc_not_above', 'wc_not_below',
                  'wc_between'}
    text_trees = {'cond_contains': 'contains', 'cond_starts': 'starts with', 'cond_ends': 'ends with',
                  'cond_minlen': 'is at least N characters', 'cond_maxlen': 'is at most N characters',
                  'wc_contains': 'contains', 'wc_starts': 'starts with', 'wc_starts_with': 'starts with',
                  'wc_ends': 'ends with', 'wc_ends_with': 'ends with',
                  'wc_minlen': 'is at least N characters', 'wc_maxlen': 'is at most N characters'}

    def judge_tree(tree, subject=None):
        for st in tree.iter_subtrees():
            if st.data in text_trees:
                # a text predicate on a known amount (its subject is its first value, or the check's
                # own subject for the subject-less `when contains "x"` form)
                _vals = [c for c in st.children if not isinstance(c, Token)]
                _subj = _vals[0] if _vals else subject
                _k = kind_of(_subj) if _subj is not None else None
                if _k and _k[0] == 'money':
                    refuse_text_on_amount(_subj, text_trees[st.data], _k[1],
                                          getattr(_subj, 'line', 0) or getattr(st.meta, 'line', 0)
                                          if hasattr(st, 'meta') else 0)
                continue
            if st.data not in pair_trees:
                continue
            vals = [c for c in st.children if not isinstance(c, Token)]
            strs = [c for c in st.children if isinstance(c, Token) and c.type == 'STRING']
            subj = vals[0] if vals else None
            line = getattr(subj, 'line', 0) or 0
            for other in vals[1:] + strs:
                if subj is not None:
                    judge(subj, other, line)

    for n in nodes:
        t = type(n).__name__
        # a `when x is ...` clause is a parse tree the node walk does not enter
        for f in fields(n):
            v = getattr(n, f.name, None)
            if isinstance(v, Tree):
                judge_tree(v, check_subject.get(id(n)))
        if t in ('SaveBlock', 'UpdateBlock'):
            # MALFORMED TEXT REFUSES: quoted text that is not a number, written to a field the
            # table's shape declares as a currency, is known wrong before anything runs.
            tgt = getattr(n, 'target', None) or getattr(n, 'source', None)
            _tp = [str(p) for p in (getattr(tgt, 'parts', None) or [])]
            money = table_money.get(str(getattr(tgt, 'table', None) or (_tp[-1] if _tp else '')), {})
            for fv in (getattr(n, 'fields', None) or getattr(n, 'body', None) or []):
                if type(fv).__name__ != 'FieldValue' or str(getattr(fv, 'name', '')) not in money:
                    continue
                lit = getattr(fv, 'value', None)
                if type(lit).__name__ != 'Literal' or getattr(lit, 'literal_type', '') != 'string':
                    continue
                txt = str(getattr(lit, 'value', '') or '')
                # a plain number only: no exponent ("1e5"), separators, NaN or Infinity (RULED 2026-10-13)
                if txt.strip() and not re.fullmatch(r'[+-]?(\d+(\.\d*)?|\.\d+)', txt.strip()):
                    line = getattr(fv, 'line', 0) or getattr(n, 'line', 0) or 0
                    errors.append(CompileError(
                        f"Line {line}: `{fv.name}` holds an amount in {money[str(fv.name)]}, and "
                        f"\"{txt}\" is not a number, so it cannot be stored there.",
                        line=line,
                        hint=(f"Write it as a plain number, for example `{fv.name} 125000.00`: "
                              f"digits and one decimal point, with no thousands separators or "
                              f"currency signs.")))
        if t == 'Condition' and getattr(n, 'op', '') in compare_ops:
            judge(n.left, n.right, getattr(n, 'line', 0) or 0)
        elif t == 'StringOpExpr' and kind_of(getattr(n, 'operand', None)) \
                and kind_of(n.operand)[0] == 'money':
            refuse_text_on_amount(n.operand, str(getattr(n, 'operation', '') or 'this'), kind_of(n.operand)[1],  # SILENT-SHAPE-OK: the operation's name in the message
                                  getattr(n, 'line', 0) or 0)
        elif t == 'Condition' and str(getattr(n, 'op', '')) in _TEXT_CONDS \
                and kind_of(getattr(n, 'left', None)) and kind_of(n.left)[0] == 'money':
            refuse_text_on_amount(n.left, _TEXT_CONDS[str(n.op)], kind_of(n.left)[1], getattr(n, 'line', 0) or 0)
        elif t == 'DottedName' and len(getattr(n, 'parts', []) or []) == 2 \
                and str(n.parts[1]) in ('length', 'size', 'count', 'uc', 'lc') \
                and kind_of(DottedName_(parts=[n.parts[0]])) \
                and kind_of(DottedName_(parts=[n.parts[0]]))[0] == 'money':
            refuse_text_on_amount(DottedName_(parts=[n.parts[0]]), "." + str(n.parts[1]),
                                  kind_of(DottedName_(parts=[n.parts[0]]))[1], getattr(n, 'line', 0) or 0)
        elif t == 'MathExpr' and getattr(n, 'op', '') in ('+', '-', '*', '/', '%'):
            # ARITHMETIC ON AMOUNTS (RULED by Ron, 2026-10-14), where both kinds are written: the
            # run-time rule in `_refuse_money_math`, refused here before anything runs.
            ka, kb = kind_of(n.left), kind_of(n.right)
            line = getattr(n, 'line', 0) or 0
            if not (ka and kb):
                pass
            elif ka[0] == 'money' and kb[0] == 'money' and ka[1] != kb[1]:
                errors.append(CompileError(
                    f"Line {line}: {label(n.left)} is an amount in {ka[1]} and {label(n.right)} is an "
                    f"amount in {kb[1]}. There is no fixed rate between them, so `{n.op}` has no "
                    f"answer.",
                    line=line,
                    hint=(f"Use amounts in one currency, for example two {ka[1]} amounts. Mohio "
                          f"does not convert between currencies yet.")))
            elif n.op in ('+', '-') and {ka[0], kb[0]} == {'money', 'number'}:
                refuse_bare_number(n.left, n.right, ka, kb, line,
                                   "added to" if n.op == '+' else "taken from")
            elif n.op == '*' and ka[0] == 'money' and kb[0] == 'money':
                errors.append(CompileError(
                    f"Line {line}: {label(n.left)} times {label(n.right)} multiplies an amount by "
                    f"an amount, and an amount times an amount is not an amount of anything.",
                    line=line,
                    hint=(f"Multiply an amount by a plain number, for example "
                          f"`({label(n.left).strip('`')} * 2)`.")))
            elif n.op in ('/', '%') and ka[0] == 'number' and kb[0] == 'money':
                errors.append(CompileError(
                    f"Line {line}: {label(n.left)} {n.op} {label(n.right)} divides a plain number "
                    f"by an amount in {kb[1]}, which has no meaning.",
                    line=line,
                    hint=(f"Divide the amount by the number instead, for example "
                          f"`({label(n.right).strip('`')} / {label(n.left).strip('`')})`, or "
                          f"divide two {kb[1]} amounts to get a ratio.")))
        elif t == 'CheckBlock':
            for w in (getattr(n, 'when_clauses', None) or []):
                wv = getattr(w, 'value', None)
                if type(wv).__name__ in ('Literal', 'DottedName') and \
                        getattr(w, 'condition', '') in ('when', 'above', 'below'):
                    judge(getattr(n, 'value', None), wv, getattr(w, 'line', 0) or 0)
        if t in ('RetrieveBlock', 'FindBlock', 'GrabBlock', 'UpdateBlock', 'RemoveBlock'):
            src = getattr(n, 'source', None)
            parts = [str(p) for p in (getattr(src, 'parts', None) or [])] if src is not None else []
            table = str(getattr(src, 'table', None) or (parts[-1] if parts else ''))
            money = table_money.get(table) or {}
            colkind = dict(table_other.get(table, {}))
            colkind.update({f: 'number' for f in table_number.get(table, set())})
            if not money and not colkind:
                continue
            clauses = list(getattr(n, 'body', None) or [])
            m = getattr(n, 'match', None)
            clauses += (m if isinstance(m, list) else [m]) if m is not None else []
            c = getattr(n, 'condition', None)
            clauses += (c if isinstance(c, list) else [c]) if c is not None else []
            for cl in clauses:
                ct = type(cl).__name__
                pairs = []
                _tcond = str(getattr(cl, 'condition', '') or '')
                if ct in ('WhereClause', 'AndClause') and _tcond in _TEXT_CONDS \
                        and money.get(str(getattr(cl, 'field', '') or '').split('.')[-1]):
                    # TEXT OPERATIONS ON AN AMOUNT REFUSE (RULED by Ron, 2026-10-14)
                    _col = str(cl.field).split('.')[-1]
                    _line = getattr(cl, 'line', 0) or getattr(n, 'line', 0) or 0
                    errors.append(CompileError(
                        f"Line {_line}: `{_col}` in db.{table} is declared {money[_col]}, and "
                        f"`{_TEXT_CONDS[_tcond]}` works on text. An amount isn't text, so a query "
                        f"cannot search its characters.",
                        line=_line,
                        hint=(f"Compare it as an amount, for example `where {_col} is above 100.00` "
                              f"or `where {_col} is between 10.00 and 20.00`.")))
                    continue
                if ct in ('MatchClause', 'WhereClause', 'AndClause'):
                    pairs = [(getattr(cl, 'field', ''), getattr(cl, 'value', None))]
                    if getattr(cl, 'value2', None) is not None:
                        pairs.append((cl.field, cl.value2))
                elif ct in ('MatchBlock', 'MatchAnyBlock', 'NoMatchBlock'):
                    pairs = list(getattr(cl, 'pairs', None) or [])
                for field, value in pairs:
                    col = str(field or '').split('.')[-1]
                    code = money.get(col)
                    kv = kind_of(value) if value is not None else None
                    if not code and kv and colkind.get(col):
                        # every other column kind, the same rule as a comparison (RULED 2026-10-14)
                        _ck = (colkind[col], '')
                        _hint = None
                        if {_ck[0], kv[0]} == {'number', 'text'}:
                            if _quoted(value):
                                _hint = (f"Write the number without quotes, for example "
                                         f"`match {col} to 5`.")
                        elif _ck[0] in ('date', 'datetime') and kv[0] == 'text' and _quoted(value)                                 and _reads_as_date(value):
                            _hint = None   # a date column meets quoted date text (datetime_bounds)
                        else:
                            _hint = _cross_kind_refusal(DottedName_(parts=[col]), value, _ck, kv)
                        if _hint:
                            line = getattr(cl, 'line', 0) or getattr(n, 'line', 0) or 0
                            errors.append(CompileError(
                                f"Line {line}: `{col}` in db.{table} holds {_kind_phrase(_ck[0])}, and "
                                f"{label(value)} is {_kind_phrase(kv[0])}, so the comparison has no "
                                f"answer, and at run the query would refuse. Use a value of the same kind.",
                                line=line, hint=_hint))
                        continue
                    if not code or not kv:
                        continue
                    line = getattr(cl, 'line', 0) or getattr(n, 'line', 0) or 0
                    if kv[0] == 'text':
                        errors.append(CompileError(
                            f"Line {line}: `{col}` in db.{table} is declared {code}, and "
                            f"{label(value)} is text. Text is not an amount, so the comparison has "
                            f"no answer, and at run the query would refuse.",
                            line=line,
                            hint=(f"Compare it with an amount, for example `match {col} to "
                                  f"125000.00` with no quotes.")))
                    elif kv[0] == 'number' and kv[1] != '0':
                        # money column against a bare non-zero number (RULED 2026-10-13)
                        errors.append(CompileError(
                            f"Line {line}: `{col}` in db.{table} is declared {code}, and "
                            f"{label(value)} is a bare number. {label(value)} what? A number with no "
                            f"currency could mean any currency.",
                            line=line,
                            hint=(f"Write it with its currency, for example declare `limit as {code}` "
                                  f"then `limit {label(value).strip('`')}`, and match `{col}` to "
                                  f"`limit`. Zero needs no currency.")))
                    elif kv[0] in _KIND_WORDS:
                        # an amount column against a value of another kind (RULED 2026-10-16)
                        errors.append(CompileError(
                            f"Line {line}: `{col}` in db.{table} is declared {code}, and "
                            f"{label(value)} is {_KIND_WORDS[kv[0]]}, so the comparison has no "
                            f"answer, and at run the query would refuse. Use a value of the same kind.",
                            line=line,
                            hint=(f"To find rows with no {col}, write `where {col} is empty`."
                                  if kv[0] in ('empty', 'null') else
                                  f"Compare `{col}` with a {code} amount, for example `limit as "
                                  f"{code}` then `match {col} to limit`.")))
                    elif kv[0] == 'money' and kv[1] != code:
                        errors.append(CompileError(
                            f"Line {line}: `{col}` in db.{table} is declared {code}, and "
                            f"{label(value)} is an amount in {kv[1]}. Amounts in different "
                            f"currencies cannot be compared.",
                            line=line,
                            hint=(f"Compare `{col}` with a {code} amount, for example `match "
                                  f"{col} to 125000.00`. Mohio does not convert between "
                                  f"currencies yet.")))
    return errors


_CONDITION_QUERIES = ('FindBlock', 'RetrieveBlock', 'GrabBlock', 'UpdateBlock', 'RemoveBlock',
                      'SaveOrUpdateBlock', 'CheckMioqlBlock', 'ModifyBlock',
                      'JoinBlock')     # a `with` level is judged with its own table
_CONDITION_CLAUSES = ('WhereClause', 'AndClause', 'MatchClause')
_CONDITION_PAIR_BLOCKS = ('MatchBlock', 'MatchAnyBlock', 'NoMatchBlock')


def resolve_condition_words(program, rewrite=False):
    """A WORD IN A CONDITION (RULED by Ron, 2026-10-15). In a where / and / match value, an in-list,
    and a match block, on every query verb:
      - a dotted name is a value (unchanged);
      - a bare word in the field's shape allowed list is the literal word;
      - otherwise a declared name is its value;
      - a word that is neither keeps today's behaviour;
      - a bare word that is both declared and allowed refuses here, at check.
    `{{ name }}`, `"{{ name }}"` and `\\{{ name }}` are untouched (they already work).

    Returns the check errors. With `rewrite`, the interpreter has this pass put the ruling into the
    program it is about to run: an allowed word becomes its literal, and a declared word in a list
    becomes a reference to the value (a list's bare words were always the words themselves, so
    `where n a, b` compared the column with "a" and "b" and matched nothing)."""
    from mohio_ast import Literal, DottedName
    nodes = list(_all_nodes(program))
    declared = set()
    for n in nodes:
        t = type(n).__name__
        if t == 'Assignment' and getattr(n, 'name', None):
            declared.add(str(n.name))
        elif t == 'TaskParam' and getattr(n, 'name', None):
            declared.add(str(n.name))
        elif t == 'EachBlock' and getattr(n, 'item', None):
            declared.add(str(n.item))
        elif t in _CONDITION_QUERIES and isinstance(getattr(n, 'name', None), str) and n.name:
            declared.add(str(n.name))
    allowed = {}                                  # table -> {field: set(words)}
    for n in nodes:
        if type(n).__name__ != 'ShapeDecl':
            continue
        for tname, tdef in (getattr(n, 'tables', None) or {}).items():
            for f in (getattr(tdef, 'fields', None) or []):
                for m in (getattr(f, 'modifiers', None) or []):
                    if getattr(m, 'modifier_type', '') == 'allowed' and getattr(f, 'name', None):
                        words = m.value if isinstance(m.value, (list, tuple)) else [m.value]
                        allowed.setdefault(str(tname), {})[str(f.name)] = {
                            str(w).strip('"') for w in words}

    def table_of(q):
        src = getattr(q, 'source', None) or getattr(q, 'target', None) or getattr(q, 'collection', None)
        if isinstance(src, str):
            parts = src.split('.')
        else:
            parts = [str(p) for p in (getattr(src, 'parts', None) or [])]
        if len(parts) >= 2 and parts[0] == 'db':
            return parts[1]
        return str(getattr(src, 'table', '') or '') or None

    errors = []

    def judge(word, field, words, line, in_list=False):
        if word in words and word in declared:
            # in a list too: `{{ x }}` and a dotted name are list items (T1-INLIST-VALUE-FORMS)
            hint = (f"`{{{{{word}}}}}` compares `{field}` with the value `{word}` holds; `\"{word}\"` "
                    f"compares it with the word itself.")
            errors.append(CompileError(
                f"Line {line}: {word} is both a declared value and one of {field}'s allowed words. "
                f"Write {{{{{word}}}}} for the value or \"{word}\" for the word.",
                line=line, hint=hint))
            return 'both'
        if word in words:
            return 'word'
        if word in declared:
            return 'value'
        return 'neither'

    def resolve(value, field, words, line):
        """The node to evaluate for one condition value."""
        if type(value).__name__ == 'DottedName' and len(getattr(value, 'parts', []) or []) == 1:
            verdict = judge(str(value.parts[0]), field, words, line)
            if verdict == 'word':
                return Literal(value=str(value.parts[0]), literal_type='string', line=line)
            return value
        if type(value).__name__ == 'ListLiteral':
            items = []
            for it in (getattr(value, 'items', None) or []):
                if getattr(it, 'bare_word', False):
                    verdict = judge(str(it.value), field, words, getattr(it, "line", 0) or line, in_list=True)
                    if verdict == 'value':
                        it = DottedName(parts=[str(it.value)], line=getattr(it, 'line', 0) or line)
                items.append(it)
            if rewrite:
                value.items = items
            return value
        return value

    def walk(node, table, top):
        for f in fields(node):
            v = getattr(node, f.name, None)
            for child in (v if isinstance(v, list) else [v]):
                if child is None or not hasattr(child, '__dataclass_fields__'):
                    continue
                ct = type(child).__name__
                if ct in _CONDITION_QUERIES and child is not top:
                    continue                      # a nested query is judged with its own table
                if ct in _CONDITION_CLAUSES:
                    col = str(getattr(child, 'field', '') or '').split('.')[-1]
                    words = (allowed.get(table) or {}).get(col, set())
                    line = getattr(child, 'line', 0) or getattr(top, 'line', 0) or 0
                    for attr in ('value', 'value2'):
                        val = getattr(child, attr, None)
                        if val is not None:
                            res = resolve(val, col, words, line)
                            if rewrite:
                                setattr(child, attr, res)
                elif ct == 'Condition' and type(top).__name__ == 'ModifyBlock' \
                        and type(getattr(child, 'left', None)).__name__ == 'DottedName':
                    # `modify every r in db.t where status is pending`: the filter is a condition on
                    # a field of the table, not a where clause, and follows the same rule.
                    col = str(child.left.parts[-1])
                    words = (allowed.get(table) or {}).get(col, set())
                    line = getattr(child, 'line', 0) or getattr(top, 'line', 0) or 0
                    if getattr(child, 'right', None) is not None:
                        res = resolve(child.right, col, words, line)
                        if rewrite:
                            child.right = res
                elif ct in _CONDITION_PAIR_BLOCKS:
                    line = getattr(child, 'line', 0) or getattr(top, 'line', 0) or 0
                    pairs = []
                    for fld, val in (getattr(child, 'pairs', None) or []):
                        col = str(fld or '').split('.')[-1]
                        words = (allowed.get(table) or {}).get(col, set())
                        pairs.append((fld, resolve(val, col, words, line) if val is not None else val))
                    if rewrite:
                        child.pairs = pairs
                walk(child, table, top)

    for n in nodes:
        if type(n).__name__ in _CONDITION_QUERIES:
            walk(n, table_of(n), n)

    # A `check` on a field of a record a lookup read (`retrieve t from db.tasks` then `check t.status`
    # / `when pending`, or `when t.status is pending`): the record's table gives the allowed list.
    records = {}
    for n in nodes:
        if type(n).__name__ in ('RetrieveBlock', 'GrabBlock') and isinstance(getattr(n, 'name', None), str):
            tbl = table_of(n)
            if tbl:
                records[str(n.name)] = tbl

    def record_field(node):
        parts = [str(p) for p in (getattr(node, 'parts', None) or [])] \
            if type(node).__name__ == 'DottedName' else []
        if len(parts) == 2 and parts[0] in records:
            return records[parts[0]], parts[1]
        return None

    try:
        from lark import Tree as _Tree
    except ImportError:  # SILENT-OK: no parse trees without lark, so there is nothing to walk here
        _Tree = ()
    for n in nodes:
        if type(n).__name__ != 'CheckBlock':
            continue
        subj = record_field(getattr(n, 'value', None))
        for w in (getattr(n, 'when_clauses', None) or []):
            line = getattr(w, 'line', 0) or getattr(n, 'line', 0) or 0
            wv = getattr(w, 'value', None)
            if subj and getattr(w, 'condition', '') in ('when', 'not', 'is_in') and wv is not None \
                    and not isinstance(wv, _Tree):
                res = resolve(wv, subj[1], (allowed.get(subj[0]) or {}).get(subj[1], set()), line)
                if rewrite:
                    w.value = res
            if isinstance(wv, _Tree):
                for st in wv.iter_subtrees():
                    if st.data not in ('wc_is', 'wc_not_is', 'wc_in_list', 'wc_not_in_list'):
                        continue
                    kids = [c for c in st.children if not hasattr(c, 'type')]
                    rf = record_field(kids[0]) if kids else None
                    if not rf or len(kids) < 2:
                        continue
                    res = resolve(kids[1], rf[1], (allowed.get(rf[0]) or {}).get(rf[1], set()), line)
                    if rewrite:
                        st.children[st.children.index(kids[1])] = res
    # a node shared by more than one walk reports once
    seen, unique = set(), []
    for e in errors:
        key = str(e)
        if key not in seen:
            seen.add(key)
            unique.append(e)
    return unique


def scan_condition_words(program):
    """Check side of `resolve_condition_words`: a bare word both declared and allowed refuses."""
    return resolve_condition_words(program, rewrite=False)


def _canonical_uuid(text):
    """The canonical text of a uuid (lowercase, hyphenated), or None when `text` is not one."""
    import uuid as _uuid
    try:
        return str(_uuid.UUID(str(text)))
    except (ValueError, TypeError, AttributeError):  # SILENT-OK: None means not a uuid, which the caller refuses
        return None


def scan_other_kinds_known(program):
    """VALUES OF DIFFERENT KINDS NEVER COMPARE SILENTLY, beyond money (RULED by Ron, 2026-10-14,
    value kinds 2f, the silent cases; dates wait for the dates design). Where both kinds are written:
    a true-or-false against quoted "true" / "false" refuses (`write true`), and against any other text
    or a number; a uuid against text needs canonical uuid text (lowercase, hyphenated), parsed here;
    a uuid against a number or a true-or-false refuses; a column declared as text against a number,
    and a number against a true-or-false, refuse. Each used to answer: `when b is "true"` said no, a
    boolean column matched to "true" found nothing, `b == 1` said yes, and a uuid column matched to
    malformed text found nothing."""
    from lark import Token
    nodes = list(_all_nodes(program))

    def norm(tn):
        tn = str(tn or '').lower()
        if tn in ('bool', 'boolean'):
            return 'bool'
        if tn == 'uuid':
            return 'uuid'
        if tn in ('text', 'text.long'):
            return 'text'
        if _numeric_type(tn):
            return 'number'
        return None

    var_kind = {}
    for n in nodes:
        if type(n).__name__ != 'Assignment' or not getattr(n, 'name', None):
            continue
        k = norm(getattr(n, 'type_name', None))
        v = getattr(n, 'value', None)
        if not k and type(v).__name__ == 'Literal':
            lt = getattr(v, 'literal_type', '')
            k = 'bool' if lt in ('bool', 'boolean') else ('text' if lt == 'string' else None)
            if k == 'text' and str(getattr(v, 'value', '')) == '':
                k = None             # an empty value has no kind: the empty rule decides it
        if k:
            var_kind.setdefault(str(n.name), set()).add(k)
        elif v is not None:
            var_kind.setdefault(str(n.name), set()).add('other')
    table_kind = {}
    for n in nodes:
        if type(n).__name__ != 'ShapeDecl':
            continue
        for tname, t in (getattr(n, 'tables', None) or {}).items():
            for f in (getattr(t, 'fields', None) or []):
                k = norm(getattr(f, 'type_name', '') or getattr(f, 'field_type', ''))
                if k and getattr(f, 'name', None):
                    table_kind.setdefault(str(tname), {})[str(f.name)] = k
    lookups = {}
    for n in nodes:
        if type(n).__name__ in ('RetrieveBlock', 'FindBlock', 'GrabBlock'):
            src = getattr(n, 'source', None)
            parts = [str(p) for p in (getattr(src, 'parts', None) or [])] if src is not None else []
            tbl = str(getattr(src, 'table', None) or (parts[-1] if parts else ''))
            for nm in (getattr(n, 'name', None), getattr(n, 'alias', None)):
                if nm:
                    lookups[str(nm)] = tbl

    def kind_of(node):
        """(kind, literal-or-None): kind is bool / uuid / text / number; the literal is the written value."""
        if isinstance(node, Token):
            _sv = str(node).strip('"')
            return ('text', _sv) if node.type == 'STRING' and _sv != '' else (None, None)
        t = type(node).__name__
        if t == '_ColumnRef':
            return (node.kind, None)
        if t == 'Literal':
            lt = getattr(node, 'literal_type', '')
            if lt in ('bool', 'boolean'):
                return ('bool', node.value)
            if lt == 'number':
                return ('number', node.value)
            if lt == 'string':
                # AN EMPTY VALUE HAS NO KIND. "" is the empty value, and an empty value matches no
                # comparison (it falls to otherwise, with a warning): judging it as text refused
                # `amount ""` against a number, which that ruling answers.
                return ('text', str(node.value)) if str(node.value) != '' else (None, None)
            return (None, None)
        if t == 'DottedName':
            parts = [str(p) for p in (getattr(node, 'parts', None) or [])]
            if len(parts) == 1:
                ks = var_kind.get(parts[0], set())
                if len(ks) == 1 and 'other' not in ks:
                    return (next(iter(ks)), None)
            elif len(parts) == 2 and parts[0] in lookups:
                return (table_kind.get(lookups[parts[0]], {}).get(parts[1]), None)
        return (None, None)

    errors = []

    def name_of(node):
        if type(node).__name__ in ('DottedName', '_ColumnRef'):
            return '`' + '.'.join(str(p) for p in node.parts) + '`'
        lit = kind_of(node)[1]
        if isinstance(lit, bool):
            return 'true' if lit else 'false'        # as Mohio writes it, never True
        return '"%s"' % lit if kind_of(node)[0] == 'text' else str(lit)

    def judge(a, b, line):
        (ka, la), (kb, lb) = kind_of(a), kind_of(b)
        if not ka or not kb or ka == kb:
            if ka == kb == 'uuid':
                return
            if not (ka == 'uuid' and kb == 'text' or ka == 'text' and kb == 'uuid'):
                return
        pair = {ka, kb}
        # make `a` the side with the declared kind the message is about
        if ka in ('text', 'number') and kb in ('bool', 'uuid'):
            a, b, ka, kb, la, lb = b, a, kb, ka, lb, la
        if ka == 'bool':
            if kb == 'text' and lb is not None and str(lb).strip().lower() in ('true', 'false'):
                word = str(lb).strip().lower()
                errors.append(CompileError(
                    f"Line {line}: {name_of(a)} is a true-or-false, and \"{lb}\" is quoted text, so the "
                    f"comparison has no answer. Write {word} without quotes.",
                    line=line, hint=f"Write `{word}`, not `\"{lb}\"`: a true-or-false is never text."))
            else:
                errors.append(CompileError(
                    f"Line {line}: {name_of(a)} is a true-or-false, and {name_of(b)} is "
                    f"{'a number' if kb == 'number' else 'text' if kb == 'text' else 'a ' + kb}, so the "
                    f"comparison has no answer.",
                    line=line, hint="Compare a true-or-false with `true` or `false`."))
            return
        if ka == 'uuid':
            if kb == 'text':
                if lb is None:
                    return            # text whose value arrives at run: checked then
                canon = _canonical_uuid(lb)
                if canon is None:
                    errors.append(CompileError(
                        f"Line {line}: {name_of(a)} holds a uuid, and \"{lb}\" is not a uuid, so the "
                        f"comparison can never match.",
                        line=line, hint="Compare it with a uuid, written like 6f1c2b9e-3d4a-4b5c-8d7e-9f0a1b2c3d4e."))
                elif canon != str(lb):
                    errors.append(CompileError(
                        f"Line {line}: {name_of(a)} holds a uuid, and \"{lb}\" is a uuid not written in its "
                        f"canonical form, so it would not match the stored text.",
                        line=line, hint=f"Write it in lower case: \"{canon}\"."))
                return
            errors.append(CompileError(
                f"Line {line}: {name_of(a)} holds a uuid, and {name_of(b)} is "
                f"{'a number' if kb == 'number' else 'a true-or-false'}, so the comparison has no answer.",
                line=line, hint="Compare a uuid with a uuid, written as quoted text."))
            return
        if pair == {'text', 'number'}:
            txt, num = (a, b) if ka == 'text' else (b, a)
            if type(txt).__name__ in ('DottedName', '_ColumnRef') and kind_of(num)[1] is not None:
                errors.append(CompileError(
                    f"Line {line}: {name_of(txt)} holds text, and {name_of(num)} is a number, so the "
                    f"comparison has no answer.",
                    line=line, hint=f"Quote it to compare text: \"{kind_of(num)[1]}\"."))
            elif type(num).__name__ in ('DottedName', '_ColumnRef') and kind_of(txt)[1] is not None and \
                    type(txt).__name__ not in ('DottedName', '_ColumnRef'):
                errors.append(CompileError(
                    f"Line {line}: {name_of(num)} holds a number, and {name_of(txt)} is quoted text, so "
                    f"this compares a number with text.",
                    line=line, hint=f"Write the number without quotes: {kind_of(txt)[1]}."))

    compare_ops = {'is', '==', '=', 'is not', '!=', '>', '<', '>=', '<=', 'above', 'below'}
    pair_trees = {'wc_is', 'wc_not_is', 'wc_above', 'wc_below'}
    for n in nodes:
        t = type(n).__name__
        for f in fields(n):
            v = getattr(n, f.name, None)
            if hasattr(v, 'iter_subtrees'):
                for st in v.iter_subtrees():
                    if st.data in pair_trees:
                        vals = [c for c in st.children if not isinstance(c, Token)]
                        strs = [c for c in st.children if isinstance(c, Token) and c.type == 'STRING']
                        if vals:
                            for other in vals[1:] + strs:
                                judge(vals[0], other, getattr(vals[0], 'line', 0) or 0)
        if t == 'Condition' and getattr(n, 'op', '') in compare_ops:
            judge(n.left, n.right, getattr(n, 'line', 0) or 0)
        elif t == 'CheckBlock':
            for w in (getattr(n, 'when_clauses', None) or []):
                wv = getattr(w, 'value', None)
                if type(wv).__name__ in ('Literal', 'DottedName') and getattr(w, 'condition', '') == 'when':
                    judge(getattr(n, 'value', None), wv, getattr(w, 'line', 0) or 0)
        if t in ('RetrieveBlock', 'FindBlock', 'GrabBlock', 'UpdateBlock', 'RemoveBlock', 'SaveOrUpdateBlock'):
            src = getattr(n, 'source', None)
            if isinstance(src, str):
                parts = src.split('.')
            else:
                parts = [str(p) for p in (getattr(src, 'parts', None) or [])] if src is not None else []
            kinds = table_kind.get(str(getattr(src, 'table', None) or (parts[-1] if parts else '')))
            if not kinds:
                continue
            clauses = list(getattr(n, 'body', None) or [])
            m = getattr(n, 'match', None)
            clauses += (m if isinstance(m, list) else [m]) if m is not None else []
            for cl in clauses:
                ct = type(cl).__name__
                if ct in ('WhereClause', 'AndClause') and str(getattr(cl, 'condition', '')) not in (
                        'is', 'is_not', '==', '!=', 'above', 'below'):
                    continue
                pairs = []
                if ct in ('MatchClause', 'WhereClause', 'AndClause'):
                    pairs = [(getattr(cl, 'field', ''), getattr(cl, 'value', None))]
                elif ct in ('MatchBlock', 'MatchAnyBlock', 'NoMatchBlock'):
                    pairs = list(getattr(cl, 'pairs', None) or [])
                for field, value in pairs:
                    col = str(field or '').split('.')[-1]
                    if col not in kinds or value is None:
                        continue
                    judge(_ColumnRef(col, kinds[col]), value,
                          getattr(cl, 'line', 0) or getattr(n, 'line', 0) or 0)
    return errors


class _ColumnRef:
    """A query column in `scan_other_kinds_known`: named and kinded like a lookup field."""
    def __init__(self, name, kind):
        self.parts = [name]
        self.kind = kind


def table_shape_field_message(table, shape, declared, extra, verb):
    """The refusal for a write that carries a field the table's shape does not declare (Q13), one
    wording for `mio check` and the run (T1-Q13-SHAPED-TABLE-EXTRA-FIELD)."""
    lead = {'update': f"Can't update db.{table}", 'upsert': f"Can't upsert into db.{table}",
            'flow': f"Can't flow into db.{table}"}.get(
        verb, f"Can't save to db.{table}")
    first = extra[0]
    more = (" (and " + ", ".join(f'"{e}"' for e in extra[1:]) + ")") if len(extra) > 1 else ""
    return (f"{lead}: it has no field \"{first}\"{more}.\n"
            f"shape {shape} declares db.{table} with {', '.join(sorted(declared))}, "
            f"and a write carries only what the shape declares, so nothing was written.\n"
            f"Fix one of: add \"{first}\" to db.{table} in shape {shape}; or leave it out of this {verb}.")


def scan_write_field_outside_table_shape(program):
    """A write that names a field its table's shape does not declare refuses at check (Q13, the
    mass-assignment class, folded into the tag by Ron 2026-10-16). `save`, `save or update`, `upsert`
    and `update` name their fields in the program, so the mistake is known before anything runs; the
    run refuses the same way where the fields arrive with a record (`save all`)."""
    kind = lambda n: type(n).__name__
    tables, owner = {}, {}
    for n in _all_nodes(program):
        if kind(n) == 'ShapeDecl':
            for tname, t in (getattr(n, 'tables', None) or {}).items():
                tables[str(tname)] = {str(getattr(f, 'name', '')) for f in (getattr(t, 'fields', None) or [])}
                owner[str(tname)] = str(getattr(n, 'name', '') or '')
    if not tables:
        return []

    def table_of(ref):
        if ref is None:
            return ''
        if kind(ref) == 'DbRef':
            return str(getattr(ref, 'table', '') or '')
        parts = [str(p) for p in (getattr(ref, 'parts', None) or [])] or str(ref).split('.')
        return parts[1] if len(parts) >= 2 and parts[0] == 'db' else ''

    errors = []
    for n in _all_nodes(program):
        k = kind(n)
        if k == 'MapPipeline':
            # The stages a hop writes to, by its direction: forward writes stage i+1, reverse writes
            # stage i, bidirectional writes both (`a -> b <-> c <- d`).
            stages = list(getattr(n, 'stages', None) or [])
            hops = list(getattr(n, 'hops', None) or [])
            written = set()
            for i, h in enumerate(hops):
                if h in ('forward', 'bidirectional') and i + 1 < len(stages):
                    written.add(i + 1)
                if h in ('reverse', 'bidirectional'):
                    written.add(i)
            for st in [stages[i] for i in sorted(written) if kind(stages[i]) == 'MapStage']:
                if str(st.qualifier) != 'db' or str(st.path).count('.') == 0:
                    continue
                table, fld = str(st.path).split('.', 1)
                declared = tables.get(table)
                if declared is not None and fld not in declared and fld != 'id':
                    line = getattr(st, 'line', 0) or getattr(n, 'line', 0) or 0
                    errors.append(CompileError(
                        f"Line {line}: " + table_shape_field_message(table, owner.get(table, ''), declared,
                                                                    [fld], 'flow'), line=line))
            continue
        if k == 'SaveBlock':
            table, items, verb = table_of(getattr(n, 'target', None)), getattr(n, 'fields', None), 'save'
        elif k == 'SaveOrUpdateBlock':
            table, items, verb = table_of(getattr(n, 'source', None)), getattr(n, 'fields', None), 'save or update'
        elif k == 'UpdateBlock':
            table, items, verb = table_of(getattr(n, 'source', None)), getattr(n, 'body', None), 'update'
        else:
            continue
        declared = tables.get(table)
        if declared is None:
            continue
        fvs = [f for f in (items or []) if kind(f) == 'FieldValue']
        extra = [str(f.name) for f in fvs if str(f.name) not in declared and str(f.name) != 'id']
        if extra:
            line = next((getattr(f, 'line', 0) for f in fvs if str(f.name) == extra[0]), 0) \
                or getattr(n, 'line', 0) or 0
            errors.append(CompileError(
                f"Line {line}: " + table_shape_field_message(table, owner.get(table, ''), declared, extra, verb),
                line=line))
    return errors


def scan_upload_zone_ambiguous(program):
    """More than one cloud storage area in a program that takes uploads refuses at check
    (UPLOAD-ZONE-AMBIGUOUS, 6.0.1). There is no way yet to say which area an upload belongs in
    (`T1-UPLOAD-AREA-CHOICE`), so the runtime refused every upload with a 500 mid-request, after the
    app was serving; now the program does not start. One cloud area, or none, is unaffected, and so
    is a program with several areas and no upload field."""
    clouds, uploads = [], []
    UPLOAD_TYPES = ('file', 'image', 'audio', 'video', 'pdf')

    def walk(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                walk(c)
            return
        if not is_dataclass(node):
            return
        k = type(node).__name__
        if k == 'MiofileDecl':
            for z in (getattr(node, 'zones', None) or []):
                if isinstance(z, dict) and z.get('kind') == 'cloud':
                    nm = str(z.get('name') or '')
                    if nm not in [c[0] for c in clouds]:
                        clouds.append((nm, getattr(node, 'line', 0) or 0))
        elif k == 'ShapeDecl':
            for fld in (node.every_field() if hasattr(node, 'every_field')
                        else (getattr(node, 'fields', None) or [])):
                if str(getattr(fld, 'type_name', '') or '').lower() in UPLOAD_TYPES:
                    uploads.append((str(getattr(fld, 'name', '') or ''), getattr(fld, 'line', 0) or 0))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    walk(getattr(program, 'statements', None) or [])
    if len(clouds) < 2 or not uploads:
        return []
    names = ", ".join(c[0] for c in clouds)
    fld, fline = uploads[0]
    return [CompileError(
        f"This program declares {len(clouds)} cloud storage areas ({names}) and takes uploads "
        f"(`{fld}`, line {fline}), and choosing which area an upload belongs in "
        f"(`T1-UPLOAD-AREA-CHOICE`) is not built yet, so every upload would refuse while the app is serving.",
        line=clouds[1][1],
        hint=(f"Keep one cloud area for uploads and make the others local, or move them to a "
              f"program that takes no uploads. Picking one for you would put a file in whichever "
              f"bucket happened to be declared first."))]


# ONE PATH, TWO CLASSES (RULED by Ron 2026-10-22: teach, do not refuse the general coder). `public: /x`
# and `authorize: /x` in one journey resolve by the locked model (T1-PAGE-CLASSIFICATION-MODEL) at serve
# time, unchanged here: public: overrides every class, hidden: is applied before authorize:, authorize:
# gates where private: (inert) does nothing. So one of the two lines does nothing, and when public: wins
# the sign-in gate the coder wrote is silently not applied. A WARNING without a sector; under a sector
# (any declared one: the runtime treats every declared sector as regulated, its profile or the built-in
# defaults in force) a REFUSAL. A classification cannot be guarded by a `when` inside a journey (a
# syntax error, measured 2026-10-22), so every repeat in one journey is unconditional; if a guarded
# classification is ever built, it is exempt here. Only the SAME path counts (normalized as the serve
# layer does): a narrower path under a broader one is the designed carve-out, not a contradiction.
_CLASS_PRECEDENCE = ('public', 'hidden', 'authorize', 'private')   # who wins at serve time, first wins


def _class_conflict_text(path, winner, loser):
    if winner == 'public' and loser == 'authorize':
        return (f"`public:` overrides every other class when the page is served, so the sign-in gate "
                f"written with `authorize:` is never applied: anyone can open `{path}` without signing in.")
    if winner == 'public' and loser == 'hidden':
        return (f"`public:` serves the page, so the `hidden:` on the same path never hides it: `{path}` is "
                f"served to anyone.")
    if winner == 'public':
        return (f"`public:` overrides every other class when the page is served, so `private:` has no "
                f"effect on `{path}`.")
    if winner == 'hidden' and loser == 'authorize':
        return ("`hidden:` wins: the page is never served (404), so the `authorize:` sign-in gate is never "
                "reached.")
    if winner == 'hidden':
        return "`hidden:` wins: the page is never served (404), so `private:` has no effect."
    return ("`authorize:` wins and asks for a sign-in; `private:` has no runtime effect, so it adds "
            "nothing.")


def _same_path_two_classes(program):
    """[(journey name, path, (kind, line), (kind, line))], one per path declared under two classes in
    one journey: the first two different classes it was declared under, in source order."""
    found = []
    for n in _all_nodes(program):
        if type(n).__name__ != 'JourneyDecl':
            continue
        first = {}                                  # normalized path -> (kind, line) where it first appeared
        reported = set()
        for m in (getattr(n, 'body', None) or []):
            kind = getattr(m, 'kind', '')
            if type(m).__name__ != 'JourneyMeta' or kind not in _CLASS_PRECEDENCE:
                continue
            for written in (getattr(m, 'value', None) or []):
                p = _class_norm_path(str(written))
                if p not in first:
                    first[p] = (kind, getattr(m, 'line', 0) or 0)
                elif first[p][0] != kind and p not in reported:
                    reported.add(p)
                    found.append((getattr(n, 'name', '') or 'App',  # SILENT-SHAPE-OK: the word the message calls an unnamed journey
                                  p, first[p], (kind, getattr(m, 'line', 0) or 0)))
    return found


def _declared_sector(program):
    for n in _all_nodes(program):
        if type(n).__name__ == 'SectorDecl':
            name = getattr(n, 'name', None) or getattr(n, 'sector', None)
            if name:
                return str(name)
    return None


def _same_path_two_classes_findings(program, under_sector):
    """The findings, as a warning without a sector or as a refusal under one. The runtime asks
    get_sector_profile(...) is not None (_exec_SectorDecl) to decide a sector's rules are in force;
    so does this, so the two can never disagree about which programs are regulated."""
    sector = _declared_sector(program)
    if sector is not None:
        from mohio_sector_loader import get_sector_profile
        if get_sector_profile(sector) is None:
            sector = None
    if (sector is not None) != under_sector:
        return []
    out = []
    for jname, path, (k1, l1), (k2, l2) in _same_path_two_classes(program):
        winner = min((k1, k2), key=_CLASS_PRECEDENCE.index)
        loser = k2 if winner == k1 else k1
        keep = 'authorize' if 'authorize' in (k1, k2) else ('hidden' if 'hidden' in (k1, k2) else k1)
        msg = (f"Line {l2}: journey `{jname}` has `{path}` declared under two classes: `{k1}:` on line {l1} "
               f"and `{k2}:` on line {l2}. {_class_conflict_text(path, winner, loser)}"
               + (f" Under sector `{sector}` a page classified two ways is refused."
                  if under_sector else " (A warning while you build; an error under a regulated sector.)"))
        hint = (f"Keep only the line you mean, for example `{keep}: {path}`."
                + (f" To gate `{path}` but open one page beneath it, keep the gate on the folder and add "
                   f"`public:` on the narrower path: `{keep}: {path}`, then `public: {path}/open`."
                   if 'public' in (k1, k2) else ""))
        code = "classification_same_path"
        out.append(CompileError(msg, line=l2, hint=hint, code=code) if under_sector
                   else CompileWarning(msg, line=l2, hint=hint, code=code))
    return out


def scan_same_path_two_classes_warning(program):
    return _same_path_two_classes_findings(program, under_sector=False)


def scan_same_path_two_classes_refused(program):
    return _same_path_two_classes_findings(program, under_sector=True)


_KEEP_FOR_NO_FIXED_LENGTH = {'month': 'a month', 'months': 'a month', 'year': 'a year', 'years': 'a year'}


def _regulated_sector_as_written(nodes):
    """The regulated sector the program declares, its levels shown as the coder writes them
    (`financial, banking`; the parsed form joins them with dots), or None."""
    from mohio_transformer import REGULATED_SECTORS
    for n in nodes:
        if type(n).__name__ != 'SectorDecl':
            continue
        levels = [x.strip() for x in str(getattr(n, 'sector', '') or '').replace(',', '.').split('.') if x.strip()]
        if levels and levels[0].lower() in REGULATED_SECTORS:
            return ', '.join(levels)
    return None


def scan_route_idempotency(program):
    """`idempotent by request.header "X" (keep for N)?` (T1-ROUTE-IDEMPOTENCY, RULED by Ron 2026-10-22).

    It describes a whole `listen for` route, so it stands once at the top of one. It reads its key
    from a request header, with the one `request.header "X"` form. It keeps answers in the database,
    in the request transaction, so it needs a connection that can run one (MongoDB cannot yet).
    `keep for` is optional, 24 hours by default, a fixed length when written, and required under a
    regulated sector. The run-time copies of the first refusals sit in _idempotency_gate."""
    errors = []
    nodes = list(_all_nodes(program))
    decls = [n for n in nodes if type(n).__name__ == 'IdempotentDecl']
    if not decls:
        return errors
    top, under_write, under_read = {}, set(), set()
    for n in nodes:
        kind = type(n).__name__
        if kind not in ('NewBlock', 'RequestInboundBlock'):
            continue
        body = getattr(n, 'body', None) or []
        for d in (x for x in _all_nodes(body) if type(x).__name__ == 'IdempotentDecl'):
            (under_write if kind == 'NewBlock' else under_read).add(id(d))
        if kind == 'NewBlock':
            mine = [b for b in body if type(b).__name__ == 'IdempotentDecl']
            for d in mine:
                top[id(d)] = (n, mine)
    drivers = [str(getattr(n, 'driver', None) or getattr(n, 'connect_type', '') or '').lower()
               for n in nodes if type(n).__name__ == 'ConnectDecl']
    regulated = _regulated_sector_as_written(nodes)
    fix = 'idempotent by request.header "Idempotency-Key"'
    for d in decls:
        line = getattr(d, 'line', 0) or 0

        if id(d) in under_read:
            errors.append(CompileError(f"Line {line}: " "`idempotent by` is for a route that changes something, a `listen for`. A `request for` "
                "route only reads, and a read is already safe to repeat.", line=line, hint="Remove the line, or move the work that changes something to a `listen for` route.", code="idempotent_route"))
            continue
        if id(d) not in top:
            errors.append(CompileError(f"Line {line}: " "`idempotent by` describes a whole `listen for` route, so it stands at the top of the "
                "route's body, not inside a branch or anywhere else.", line=line, hint=f"Move it to the first lines of the `listen for` it guards:\n    listen for sh.Pay at /pay\n"
                f"        {fix}\n        ...\n    listen: done", code="idempotent_route"))
            continue
        _route, mine = top[id(d)]
        if mine[0] is not d:
            first = getattr(mine[0], 'line', 0) or 0
            errors.append(CompileError(f"Line {line}: " f"this route already says `idempotent by` at line {first}. A route keeps its answers "
                f"under one key.", line=line, hint="Remove one of the two lines.", code="idempotent_route"))
            continue
        src = getattr(d, 'source', None)
        parts = [str(p).replace('__USERVAR__', '') for p in (getattr(src, 'parts', None) or [])]
        if parts != ['request', 'header'] or not getattr(d, 'header', ''):
            shown = '.'.join(parts) or 'this source'  # SILENT-SHAPE-OK: the words a refusal names a source by
            errors.append(CompileError(f"Line {line}: " f"`idempotent by` reads its key from a request header, and `{shown}` is not one. A key the "
                f"caller sends in the body would make the body the thing that names the request.", line=line, hint=f"Read the header the caller sends:\n    {fix}", code="idempotent_route"))
            continue
        if not drivers:
            errors.append(CompileError(f"Line {line}: " "`idempotent by` keeps each answer in the database, and this program connects none, so "
                "a repeated request would find nothing to answer from.", line=line, hint="Connect one, for example:\n    connect db as sqlite from env.DATABASE_URL", code="idempotent_route"))
        elif all(dr in ('mongo', 'mongodb') for dr in drivers):
            # DEFER: T1-MONGO-PROGRAM-TRANSACTION. The key and the work commit in one request
            # transaction, which MongoDB does not run in this release.
            errors.append(CompileError(f"Line {line}: " "`idempotent by` keeps the key and the work in one transaction, and a MongoDB request "
                "transaction is declared but not built in this release (T1-MONGO-PROGRAM-TRANSACTION).", line=line, hint="Run this route on Postgres, MySQL, Oracle or SQLite until the MongoDB transaction lands.", code="idempotent_route"))
        unit = str(getattr(d, 'keep_unit', '') or '')
        written = str(getattr(d, 'keep_written', '') or '')
        if unit:
            count = getattr(d, 'keep_count', None)
            if unit in _KEEP_FOR_NO_FIXED_LENGTH:
                errors.append(CompileError(f"Line {line}: " f"`keep for {written}`: {_KEEP_FOR_NO_FIXED_LENGTH[unit]} has no fixed length, so when "
                    f"the key frees would depend on the calendar.", line=line, hint="Write it in days or weeks, for example `keep for 30 days`.", code="idempotent_route"))
            elif getattr(d, 'keep_seconds', None) is None:
                errors.append(CompileError(f"Line {line}: " f"`keep for {written}` is shorter than a second, too short to answer a retry from.", line=line, hint="Write seconds or longer, for example `keep for 24 hours`.", code="idempotent_route"))
            elif not isinstance(count, (int, float)) or count <= 0:
                errors.append(CompileError(f"Line {line}: " f"`keep for {written}` keeps nothing, so every repeat would do the work again.", line=line, hint="Give it a length, for example `keep for 24 hours`.", code="idempotent_route"))
        elif regulated is not None:
            errors.append(CompileError(f"Line {line}: " f"under `sector: {regulated}` an idempotent route says how long it keeps each answer, "
                f"because how long a record is kept is a thing an audit asks.", line=line, hint=f"Add `keep for` to the line:\n    {fix} keep for 24 hours", code="idempotent_route"))
    return errors


def _routes_without_idempotent_line(program):
    """Each `listen for` that changes something (a write, or a call that changes something on another
    service) and has no `idempotent by` line (T1-IDEMPOTENT-ROUTE-REQUIRED, RULED by Ron 2026-10-23).
    Followed through `run`/`call` into the tasks the route reaches."""
    nodes = list(_dataclass_nodes(program))
    tasks, ops = _tasks_and_operations(program)

    def changes(n):
        k = type(n).__name__
        if k in _WRITE_NODES:
            return k
        return _outbound_effect(n, ops)

    out = []
    for n in nodes:
        if type(n).__name__ != 'NewBlock':
            continue
        body = getattr(n, 'body', None) or []
        if any(type(x).__name__ == 'IdempotentDecl' for x in _dataclass_nodes(body)):
            continue                    # a misplaced line is scan_route_idempotency's to refuse
        if not _first_effect(body, tasks, changes):
            continue                    # a route that changes nothing is already safe to repeat
        out.append(n)
    return out


def scan_write_route_no_idempotent(program):
    """A `listen for` that changes something with no `idempotent by` line is named at check, because a
    request sent twice does its work twice."""
    warnings = []
    for n in _routes_without_idempotent_line(program):
        shape = str(getattr(n, 'shape', '') or '').split('.')[-1]
        path = str(getattr(n, 'path', '') or '')
        line = getattr(n, 'line', 0) or 0
        warnings.append(CompileWarning(
            f"Line {line}: the `listen for` route at `{path or 'sh.' + shape}` changes something and has no "
            f"`idempotent by` line, so a request sent twice (a double click, a network retry) does its work "
            f"twice.",
            line,
            f"Name the header that carries the caller's key, at the top of the route:\n"
            f"    listen for sh.{shape}{' at ' + path if path else ''}\n"
            f"        idempotent by request.header \"Idempotency-Key\"\n        ...\n    listen: done",
            code="write_route_no_idempotent"))
    return warnings


ERROR_SCANS = (
    scan_route_idempotency,
    scan_connect_name_twice,
    scan_paginate_without_page_size,
    scan_same_path_two_classes_refused,
    scan_upload_zone_ambiguous,
    scan_write_verb_on_audit_relation,
    scan_other_kinds_known,
    scan_list_type_word_declared,
    scan_condition_words,
    scan_raw_sql_writes_audit_relation,
    scan_value_kinds_known,
    scan_required_certified_sector,
    scan_give_back_as_word,
    scan_handler_reads_file_level_name,
    scan_unwired,
    scan_unreachable,
    scan_sign_in_outside_password_match,
    scan_session_principal_writes,
    scan_using_undeclared_chain,
    scan_when_is_as_field,
    scan_mongo_tagged_data,
    scan_ai_reads_upload,
    scan_join_match_must_connect,
    scan_join_key_protection_mismatch,
    scan_secret_egress,
    scan_save_from_record_undeclared,
    scan_write_field_outside_table_shape,
    scan_outbound_call_in_transaction,
    scan_grant_role_client_source,
    scan_raw_sql_writes_classified_field,
    scan_protected_data_egress,
    scan_protected_into_untagged_field,
    scan_ai_create_unbuilt_clauses,
    scan_not_found_refusal_in_failure,
    scan_token_blocks,
    scan_subjectless_when,
    scan_connect_default_under_sector,
    scan_undeclared_dotted_roots,
    scan_shape_name_collision,
    scan_shape_name_start,
    scan_two_statements_one_line,
    scan_shape_ref_field,
    scan_shape_field_assign,
    scan_plus_on_text,
    scan_ai_connect_valid,
    scan_duplicate_routes,
    scan_render_interpolation_unresolvable,
    scan_table_as_value,
    scan_reference_rule,
    scan_loose_unique_dynamic_target,
    scan_undeclared_connection,
    scan_table_name_collision,
    scan_listener_with_no_handler,
    scan_multi_database_reference,
    scan_query_connection_name,
    scan_connection_access_mode,
    scan_give_destination,
    scan_orphan_it,
    scan_orphan_vocabulary,
    scan_unknown_types,
    scan_undeclared_connectors,
    scan_agent_tool_grants,
    scan_task_param_undeclared_shape,
    scan_undeclared_tasks,
    scan_call_value_without_returns,
    scan_held_value_restated,
    scan_take_after_returns,
    scan_raw_sql_on_document_store,
    scan_native_join_on_declared_list,
    scan_not_built_services,
    scan_unknown_db_driver,
    scan_oracle_identifier_safety,
    scan_calculate_unbuilt_functions,
    scan_executor_branch_deferrals,
    scan_text_long_unique,
    scan_text_long_query_misuse,
    scan_mongo_join_unbuilt,
    scan_deferred_constructs,
    scan_undeclared_maps,
    scan_regulated_field_needs_type,
    scan_shape_may_not_loosen_sector,
    scan_route_path_param_form,
    scan_otherwise_placement,
    scan_block_opener_as_variable,
    scan_give_back_no_value,
    scan_audit_destinations,
    scan_audit_store_reachable,
    scan_undeclared_shapes,
    scan_miofile_dangerous_accept,
    scan_upload_accept_groups,
    scan_bare_random_intrinsic,
    scan_mixed_connector_chain,
    scan_ai_decide_declared_but_never_invoked,
    scan_webhook_unverified_error,
    scan_webhook_secret_rotation,
    scan_bare_now_uuid_as_value,
    scan_state_op_requires_held,
    scan_release_now_lock_retired,
    scan_old_agent_result_underscore_form,
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
            frameworks, profile_found, _paid = _sector_frameworks(name)
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
                    from mohio_sector_loader import certified_not_installed_message as _cnim
                    warnings.append(CompileWarning(
                        _cnim(name),
                        line=getattr(node, 'line', 0),
                        hint=("This is a commercial boundary, not a missing feature. Provide the "
                              "certified profile on the search path (~/.mohio/sectors) or run the "
                              "licensed runtime. To exercise sector rules without one, write your "
                              "own community profile as sectors/sector-<name>.mho and declare "
                              "`sector: <name>`."),
                        code="sector_certified_not_installed"))
                # A non-licensed sector with no profile (absent or misspelled) WARNS AND RUNS (OR-0,
                # RULED by Ron 2026-10-12, reversing 2cfaa31): the warning, with the nearest installed
                # profile, is given once by mohio_transformer's sector check. No second warning here.
            elif frameworks:
                grade, unknown = required_grade(frameworks)
                # With no store connected this is not a warning but a refusal
                # (scan_audit_store_reachable), so it is not said twice.
                if grade in ('append_only', 'worm') and _declares_audit_store(program):
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
                              "deployment binds a sink at this grade before going live."), code="sector_audit_grade_required"))
                if unknown:
                    warnings.append(CompileWarning(
                        f"sector `{name}` names framework(s) with no known audit grade: "
                        f"{', '.join(unknown)}.",
                        line=getattr(node, 'line', 0),
                        hint=("An unrecognised framework is treated as requiring nothing, which "
                              "is a silent downgrade. Add it to the framework table or correct "
                              "the spelling."), code="sector_audit_grade_unknown"))
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


def _class_norm_path(p):
    """The serve layer's path normalization (MohioInterpreter._norm_path): drop a query string and a
    trailing slash. Kept identical; test_battery_route_classification_checked asserts they agree."""
    if not p:
        return p
    p = str(p).split('?', 1)[0]
    return p[:-1] if len(p) > 1 and p.endswith('/') else p


def _class_path_matches(entry, norm_req_path):
    """The serve layer's classification match (MohioInterpreter._journey_path_matches): an entry
    covers itself and every path under it at a segment boundary, so `authorize: /admin` covers
    /admin/users."""
    e = _class_norm_path(entry)
    if not e:
        return False
    return norm_req_path == e or norm_req_path.startswith(e + '/')


def _route_classification(program, path):
    """What the locked page-classification model (mohio.lark journey_body, T1-PAGE-CLASSIFICATION-MODEL)
    says about a route path, decided exactly as the serve layer decides it (_exec_JourneyDecl):
    'public' when an explicit `public:` covers it (it overrides the others), 'hidden' when `hidden:`
    covers it or a segment starts with `_` (not served), 'authorize' when `authorize:` covers it (login
    required), else None (public by default, or `private:`, which is served with no gate)."""
    if not path:
        return None
    metas = []

    def collect(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                collect(c)
            return
        if not is_dataclass(node):
            return
        if type(node).__name__ == 'JourneyMeta':
            metas.append(node)
            return
        for f in fields(node):
            collect(getattr(node, f.name, None))

    collect(getattr(program, 'statements', None) or [])
    req = _class_norm_path(path)

    def covered(kind):
        return any(_class_path_matches(p, req) for m in metas if m.kind == kind for p in (m.value or []))

    if covered('public'):
        return 'public'
    if covered('hidden') or any(s.startswith('_') for s in req.strip('/').split('/') if s):
        return 'hidden'
    if covered('authorize'):
        return 'authorize'
    return None


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

    def token_gated(node):
        """A `verify token` that names a `scope` is an authority check, the same as `require role`:
        the request is refused unless it presents a live token for exactly that purpose. It counts
        only when it actually gates: with no `on.failure` (a failed verify refuses by itself) or an
        `on.failure` that answers with a `give back`. One whose `on.failure` carries on would let the
        request through with no authority, so that one still draws the warning."""
        if node is None:
            return False
        if isinstance(node, list):
            return any(token_gated(i) for i in node)
        if not is_dataclass(node):
            return False
        if type(node).__name__ == 'VerifyTokenStmt' and getattr(node, 'scope', None):
            fails = [h for h in (getattr(node, 'handlers', None) or [])
                     if type(h).__name__ == 'OnFailure']
            return all(any(type(b).__name__ == 'GiveBackStmt'
                           for b in (getattr(h, 'body', None) or [])) for h in fails)
        return any(token_gated(getattr(node, f.name, None)) for f in fields(node))

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
            # THE JOURNEY'S PAGE CLASSIFICATION DECIDES FIRST (checker 3b, Summit): the serve layer
            # already refuses an `authorize:` path without a session (403), never serves a `hidden:`
            # one (404), and an explicit `public:` is a declared decision. Only a route public by
            # default (or `private:`, served with no gate) is the read nobody decided.
            if (reads_data(node) and not contains(node, RequireRoleDecl) and not token_gated(node)
                    and _route_classification(program, getattr(node, 'path', None)) is None):
                where = getattr(node, 'path', None) or getattr(node, 'name', '') or 'a route'
                _p = where if str(where).startswith('/') else '/path'
                warnings.append(CompileError(
                    f"`{where}` reads data in the `{sector}` sector with nothing checking "
                    f"who is asking. Anyone who can reach the address gets the data. "
                    f"If that is intended -- a price list, a status page -- declare it in the "
                    f"journey with `public: {_p}` so the next reader knows it was a decision. "
                    f"Otherwise gate it: `authorize: {_p}` in the journey, or `require role ...` "
                    f"in the handler.",
                    line=getattr(node, 'line', 0) or 0,
                    code="sector_route_unauthenticated"))
                return
        for f in fields(node):
            walk(getattr(node, f.name, None))

    walk(getattr(program, 'statements', None) or [])
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


def scan_transaction_onfailure_success(program):
    """Warn when an `on.failure` inside a `transaction` answers SUCCESS (RULED by Ron 2026-10-22).

    T0-4 / FORK-8 (ruled): a transaction is atomic regardless of a caught failure inside it, so a
    failed write rolls back the whole block. An `on.failure` that answers an error (`give back
    [503]`), or answers nothing, is fail-closed and correct, and this used to warn on it too: on every
    such guard, which is every one the healthcare app writes (44 sites, all non-2xx). KEYED ON THE
    RISK NOW: it fires only when the `on.failure` gives back a success (a 2xx, a bare give-back, a
    3xx, or a `jump to`, the success outcomes of HC-127), a receipt for writes the transaction did not
    keep. The run already refuses to send it (`_exec_TransactionBlock`), so this says so before the
    request fails. A warning everywhere; no refusal is minted (Ron).
    """
    from dataclasses import fields, is_dataclass
    from mohio_ast import TransactionBlock, OnFailure, GiveBackStmt, JumpToStmt
    warnings = []

    def success_answer(body):
        """The first give-back or jump in an on.failure body that answers success, else None."""
        stack = [body]
        while stack:
            n = stack.pop()
            if isinstance(n, (list, tuple)):
                stack.extend(reversed(n))
                continue
            if not is_dataclass(n):
                continue
            if isinstance(n, JumpToStmt):
                return n, "jump to"
            if isinstance(n, GiveBackStmt):
                st = getattr(n, 'status', None)
                if st is None or (isinstance(st, int) and not isinstance(st, bool) and st < 400):
                    return n, "give back" + ("" if st is None else " [%d]" % st)
            for f in fields(n):
                stack.append(getattr(n, f.name, None))
        return None

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
            hit = success_answer([getattr(node, 'inline', None), getattr(node, 'body', None)])
            if hit is not None:
                gb, said = hit
                line = getattr(gb, 'line', 0) or getattr(node, 'line', 0) or 0
                warnings.append(CompileWarning(
                    f"Line {line}: this `on.failure` answers success (`{said}`) inside a transaction, "
                    f"after a write in it failed. The transaction rolls every write in it back, so the "
                    f"success would be a receipt for work that was not kept; the run refuses to send it "
                    f"and the request fails instead.",
                    line,
                    "Answer the failure, for example `give back [409] \"not saved\"` or a 5xx, or put "
                    "the success after `transaction: done`, where it runs only when the writes were kept.",
                    'transaction_onfailure_success',
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
        'threshold': 'nothing reads it',
    }

    # NOT DECORATIVE, BUT NARROWER THAN IT LOOKS. Both of these enforce on a table a shape
    # GOVERNS via `<name> as table`, and neither reaches a plain `shape` whose fields name no
    # table, because a bare write's target is a table name with no shape attached. That binding
    # is a language decision (additive-first: binding by name convention would change every
    # pre-Phase-2 program), so the scope is stated rather than quietly assumed either way.
    #   * `default` (left the decorative set 2026-09-14) fills a missing field.
    #   * `unique` (left the decorative set 2026-09-18) becomes a real database unique index, so a
    #     duplicate is refused on every write verb -- on a governed table.
    # `unique` LEFT THIS SET (2026-09-29): plain-shape `unique` is now enforced by resolving the
    # write target and indexing that table (resolve_loose_unique), a runtime-chosen target refuses
    # (scan_loose_unique_dynamic_target), and a never-written one warns once
    # (scan_shape_unique_governs_no_table). So `unique` is no longer "partially enforced with a
    # caveat" -- it is enforced, refused, or inert-and-named, and the per-field caveat that fired on
    # every correct declaration (twenty-five explanations of a working feature) is gone.
    # `default` LEFT THIS SET (RULED by Ron 2026-10-22): its note fired on every defaulted field, a
    # scope explanation on correct code, so it regrew with every program that used a default (40 in
    # the healthcare app). The scope belongs in the manual; the check now speaks only where the scope
    # bites, a save that leaves the defaulted field out of a table no shape governs
    # (scan_default_not_on_ungoverned_save, A3 / PUB600-HC-01).
    PARTIAL = {}

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
                            line=getattr(fld, 'line', 0) or getattr(node, 'line', 0) or 0, code="modifier_not_enforced"))
                    elif mt in PARTIAL:
                        warnings.append(CompileWarning(
                            f"`{mt}` on field `{getattr(fld, 'name', '?')}` is "
                            f"{PARTIAL[mt]}.",
                            line=getattr(fld, 'line', 0) or getattr(node, 'line', 0) or 0, code="modifier_partial"))
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
    from mohio_classification import RESERVED_LABELS
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
                                line=getattr(fld, 'line', 0) or getattr(node, 'line', 0) or 0, code="classifier_is_sec_level"))
                            continue
                        if part in RECOGNIZED:
                            continue
                        # A RESERVED LABEL NAMES ITS OWN JOB (`[important]` v1, RULED 2026-10-22).
                        # The generic text called it an unknown classifier kept as a searchable
                        # label, which reads like a protection word nobody built.
                        if part in RESERVED_LABELS:
                            warnings.append(CompileWarning(
                                f"[{part}] on field `{getattr(fld, 'name', '?')}` is the journey-override "
                                f"marker, not a field label: it trails a journey.mho line, as in "
                                f"`timezone \"Asia/Tokyo\" [important]`, to make the journey's value win "
                                f"over a page's own. On a field it does nothing and gives the field no "
                                f"protection. Remove it here; to protect the field, use [pii], [phi] or "
                                f"[pci].",
                                line=getattr(fld, 'line', 0) or getattr(node, 'line', 0) or 0, code="classifier_is_override_marker"))
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
                            line=getattr(fld, 'line', 0) or getattr(node, 'line', 0) or 0, code="classifier_unrecognized"))
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

    KEPT, NOT REMOVED (batch 7). Batch 6 read this as a dead warning because it stays silent for a
    decision that is READ but never invoked. That silence is DELIBERATE delegation, not a gap: the
    wired error scan `scan_ai_decide_declared_but_never_invoked` catches the read case as a hard
    error (a read of a stale value), and this warning covers the case that error cannot -- a
    decision declared, never invoked, and whose result is never read: governance theatre that
    harms nothing at runtime but reads as governed. The two are complementary with no overlap and
    no gap, and `test_battery_ai_decide_never_invoked.py` pins exactly this. Removing it deletes
    real, tested coverage; whether the pair should instead be one construct is Ron's ruling.

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

    # A journey's declarations are used by the pages under it; their names arrive on the program
    # (mio._apply_journey) so a decision invoked from another file is not reported unused.
    spine = getattr(program, 'spine_names', None) or set()
    # On a page, only a decision a JOURNEY declares is used by the other pages; one the page declares
    # itself is judged on the page. A journey's own check has no such set: every name is the journey's.
    from_journey = getattr(program, 'journey_declared', None)
    warnings = []
    for name, line in sorted(declared.items()):
        if name in invoked or name in referenced:
            continue
        if name in spine and (from_journey is None or name in from_journey):
            continue
        warnings.append(CompileWarning(
            f"`ai.decide {name}` is declared but never invoked, so it will not run: no "
            f"confidence gate, no weighed inputs, no fallback and NO AUDIT ENTRY. The decision "
            f"reads as governed in the source and does nothing at runtime.",
            line=line,
            hint=(f"Invoke it where the decision belongs by putting its name on a line "
                  f"(`ai.decide {name}`), or remove the block if it is not needed yet."), code="ai_decide_never_invoked"))
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

    # D1-02: if the program declares a CLOUD upload area, uploads go to that bucket (durable object
    # store), not to local disk -- so the local-disk durability warning is FALSE and must not fire.
    # `_upload_store` routes every upload to a declared cloud area when one exists, so one cloud area
    # is enough to make the local-disk caveat wrong for every upload field. (The separate caveat that
    # a cloud object's reachability is the bucket's own policy is D1-04, not this warning's job.)
    def _declares_cloud_zone(root):
        for st in (getattr(root, 'statements', None) or []):
            if type(st).__name__ == 'MiofileDecl':
                for z in (getattr(st, 'zones', None) or []):
                    if isinstance(z, dict) and z.get('kind') == 'cloud':
                        return True
        return False
    if _declares_cloud_zone(program):
        return warnings

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
                        f"disk (MOHIO_UPLOAD_DIR, default ~/.mohio/uploads, outside the project "
                        f"folder) while the database stores only the path. On a host whose disk "
                        f"resets on a restart or a redeploy, the row survives and the FILE does "
                        f"not. Point MOHIO_UPLOAD_DIR at a durable volume, or declare cloud "
                        f"storage with `miofile` so uploads go there.",
                        line=getattr(fld, 'line', 0) or getattr(node, 'line', 0) or 0, code="upload_local_disk"))
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
                code="check_without_branches",
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
                    line=getattr(node, 'line', 0) or 0, code="task_answer_discarded"))
        for f in fields(node):
            walk(getattr(node, f.name, None))

    for st in stmts:
        collect(st)
    for st in stmts:
        walk(st)
    return warnings


_WRITE_NODES = {'SaveBlock', 'SaveOrUpdateBlock', 'SaveAllBlock', 'UpdateBlock',
                'RemoveBlock', 'RemoveAllBlock', 'ModifyBlock'}


_HANDLER_WRITE_NODES = ('SaveBlock', 'UpdateBlock', 'SaveOrUpdateBlock', 'SaveAllBlock',
                        'RemoveBlock', 'ModifyBlock')
_BRANCHING_NODES = ('CheckBlock', 'UnlessBlock', 'CheckWhen', 'OtherwiseClause')


def scan_refuse_after_unwrapped_write(program):
    """A handler that can refuse AFTER a write that no transaction covers is warned at check.

    RULED by Ron, 2026-10-06 (day 2, item 3). A handler with ONE write runs it alone (a single
    write is atomic by itself, and the runtime does not wrap it, on purpose); a handler with its
    own `transaction` manages its own boundary. In either, a write outside a transaction commits
    the moment it runs, so a later `give back [4xx/5xx]` or `require role` answers with a refusal
    while the row it would have refused is already saved. A handler with two or more writes and no
    transaction of its own is wrapped by the runtime and rolls back on a refusal, so it is not
    warned. Read conservatively, so the warning is never noise:
      - a refusal inside the write's OWN on.failure is the write failing, not a later refusal;
      - a refusal in a different branch of the same `check` as the write cannot follow it.
    Whether per-handler atomicity should change is recorded as a post-tag design question.
    """
    from mohio_ast import TOP_HANDLER_TYPES
    warnings = []

    def profile(node):
        n, own = [0], [False]

        def w(x):
            if x is None or isinstance(x, (str, int, float, bool)):
                return
            if isinstance(x, (list, tuple)):
                for c in x:
                    w(c)
                return
            if not is_dataclass(x):
                return
            k = type(x).__name__
            if k in _HANDLER_WRITE_NODES:
                n[0] += 1
            if k in ('TransactionBlock', 'SagaDecl'):
                own[0] = True
            for f in fields(x):
                w(getattr(x, f.name, None))
        w(getattr(node, 'body', None))
        return n[0], own[0]

    def _status(gb):
        st = getattr(gb, 'status', None)
        st = getattr(st, 'value', st)
        if isinstance(st, bool):
            return None
        if isinstance(st, int):
            return st
        if isinstance(st, str) and st.strip().isdigit():
            return int(st.strip())
        return None     # a computed status is not known at check, so it is not read as a refusal

    for handler in (getattr(program, 'statements', None) or []):
        if not isinstance(handler, TOP_HANDLER_TYPES):
            continue
        count, own = profile(handler)
        if count == 0 or (count >= 2 and not own):
            continue            # nothing written, or the runtime wraps it atomically
        writes, refusals = [], []

        def walk(node, chain, in_txn, in_own_failure_of):
            if node is None or isinstance(node, (str, int, float, bool)):
                return
            if isinstance(node, (list, tuple)):
                for c in node:
                    walk(c, chain, in_txn, in_own_failure_of)
                return
            if not is_dataclass(node):
                return
            k = type(node).__name__
            here = chain + [node]
            if k in _HANDLER_WRITE_NODES and not in_txn:
                writes.append((node, here))
            if k == 'GiveBackStmt' and (_status(node) or 0) >= 400 and in_own_failure_of is None:
                refusals.append((node, here))
            if k == 'RequireRoleDecl' and in_own_failure_of is None:
                refusals.append((node, here))
            for f in fields(node):
                v = getattr(node, f.name, None)
                inner_fail = in_own_failure_of
                if k in _HANDLER_WRITE_NODES and f.name in ('handlers', 'on_failure'):
                    inner_fail = node
                walk(v, here, in_txn or k == 'TransactionBlock', inner_fail)

        walk(getattr(handler, 'body', None), [handler], False, None)

        def exclusive(a_chain, b_chain):
            for i in range(min(len(a_chain), len(b_chain))):
                if a_chain[i] is not b_chain[i]:
                    return i > 0 and type(a_chain[i - 1]).__name__ in _BRANCHING_NODES
            return False

        for wnode, wchain in writes:
            wline = getattr(wnode, 'line', 0) or 0
            later = [(r, rc) for r, rc in refusals
                     if (getattr(r, 'line', 0) or 0) > wline and not exclusive(wchain, rc)]
            if not later:
                continue
            r, _ = later[0]
            rline = getattr(r, 'line', 0) or 0
            what = (f"`give back [{_status(r)}]`" if type(r).__name__ == 'GiveBackStmt'
                    else "`require role`")
            warnings.append(CompileWarning(
                f"This handler writes at line {wline} and can still refuse at line {rline} "
                f"({what}). The write is not inside a transaction, so the refusal answers while the "
                f"row is already saved.",
                line=wline,
                hint=(f"Check first, then write: move the test that refuses (line {rline}) above "
                      f"the write. Or wrap the write and the test in one transaction, so the "
                      f"refusal undoes the write:\n"
                      f"    transaction\n        ...\n    transaction: done"), code="refuse_after_unwrapped_write"))
            break
    return warnings


def scan_financial_multi_write_no_transaction(program):
    """Under a financial sector, several database writes in one body that are not inside a
    `transaction` are flagged with a question: do they belong together?

    Overnight notes section 17: sequential writes representing ONE business effect (a debit, a
    ledger record, a payment status, an execution record, a decision event) committed independently
    outside a `transaction`, and an injected failure left a real partial state; wrapping the five in
    one `transaction ... transaction: done` fixed it. Ron ruled WARN, not error, and that THE
    PROFILE DECIDES WHAT IS REQUIRED.

    LIMITATION, stated where it is enforced: a sector profile cannot carry a structural rule like
    this today. `SectorProfile` carries compliance grades, retain/expire rules, connector
    operation-governance rules and ai.decide confidence floors -- none of them a "writes in a
    handler must share a transaction" rule. So this is keyed to a financial sector by NAME here,
    not read from the profile. When profiles can carry structural rules, move the trigger there so
    any profile can turn it on.

    A WARNING worded as a QUESTION, because the false positive is real (Batch 7): a handler may write
    two UNRELATED tables, which is legitimate. No active financial sector means no warning and no
    cost to an ordinary developer.
    """
    financial = [False]

    def find_sector(node):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                find_sector(c)
            return
        if not is_dataclass(node):
            return
        if type(node).__name__ == 'SectorDecl':
            nm = str(getattr(node, 'name', '') or getattr(node, 'sector', '') or '').lower()
            if 'financial' in nm:
                financial[0] = True
        for f in fields(node):
            find_sector(getattr(node, f.name, None))

    find_sector(getattr(program, 'statements', None) or [])
    if not financial[0]:
        return []

    warnings = []
    seen_lines = set()

    def check_body(lst, in_txn=False):
        # A body is a list of statements. Its direct writes are flagged only when no `transaction`
        # encloses them at ANY depth: a `check` branch, a loop or a `try` inside a transaction runs
        # inside that transaction. Only the transaction's own direct children used to count as
        # covered, so two writes in a `when` inside a transaction were warned as unwrapped.
        writes = [c for c in lst if is_dataclass(c) and type(c).__name__ in _WRITE_NODES]
        if len(writes) >= 2 and not in_txn:
            line = getattr(writes[0], 'line', 0) or 0
            if line not in seen_lines:
                seen_lines.add(line)
                warnings.append(CompileWarning(
                    f"Under a financial sector, {len(writes)} database writes here are not inside a "
                    f"`transaction`. If they are one business effect, a failure partway through "
                    f"leaves partial state. Do these writes belong together? If so, wrap them in "
                    f"`transaction ... transaction: done` so they commit or roll back as one. If "
                    f"they are unrelated, this is fine as written.",
                    line=line, code="financial_writes_outside_transaction"))
        for c in lst:
            walk_node(c, in_txn)

    def walk_node(node, in_txn=False):
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                walk_node(c, in_txn)
            return
        if not is_dataclass(node):
            return
        inside = in_txn or type(node).__name__ == 'TransactionBlock'
        for f in fields(node):
            v = getattr(node, f.name, None)
            if isinstance(v, list):
                check_body(v, inside)    # every body below a transaction is covered by it
            else:
                walk_node(v, inside)

    check_body(getattr(program, 'statements', None) or [])
    return warnings


def _static_target_name(target):
    """A save/upsert destination resolved to a bare table name without a runtime context, or None
    when the destination is a runtime expression (an undecidable target). Mirrors the interpreter's
    `_static_table_name`, kept here so the checker and the runtime resolve a target the same way."""
    if target is None:
        return None
    if isinstance(target, str):
        # An `upsert db.plans` / `save or update db.plans` destination arrives as the reference
        # text, and the runtime resolves a text source as a literal table reference
        # (`_resolve_source_parts`). Returning None here made every upsert look runtime-chosen,
        # so a bound shape's defaults and a plain-shape `unique` silently never reached it.
        from mohio_runtime_vocab import split_table_ref_parts
        table, _col = split_table_ref_parts(target)
        return str(table) if table else None
    if type(target).__name__ == 'DbRef':
        return getattr(target, 'table', None)
    parts = getattr(target, 'parts', None)
    if parts:
        return str(parts[-1])
    return None


def resolve_loose_unique(program):
    """Resolve where every LOOSE `unique` field's shape is written, so plain-shape `unique` can land
    a real database index -- the plain-shape counterpart of `<name> as table` unique. This is the
    SINGLE source of truth for both the runtime (which registers the index) and the checker (which
    refuses an undecidable target), so the two can never drift.

    Returns (enforceable, undecidable, decorative):
      * enforceable: {table: set(cols)}     -- a unique index belongs on each; `unique` is enforced.
      * undecidable: [(shape, field, line)] -- the shape is written to a table chosen at RUNTIME, so
        there is no table to index; refuse (fail loud, never a silent no-op).
      * decorative:  [(shape, field, line)] -- the shape is never written in a handler body, so the
        `unique` is inert; warn once (not the enforced case, and not silent).

    Precedence, safety first: a field written to ANY dynamic target is undecidable even if also
    written to a static one, because uniqueness cannot be guaranteed across a write path with no
    index. A field written only to static targets is enforceable on each. A field whose shape is
    never written in a handler body is decorative. Scope is a handler body's own save/upsert blocks
    (the reported and common path); a write reached only through a called task is not traced here and
    stays decorative -- warned, never silently treated as enforced.
    """
    HANDLERS = ('NewBlock', 'RequestInboundBlock', 'ChangeBlock')
    WRITES = ('SaveBlock', 'SaveOrUpdateBlock', 'SaveAllBlock')

    # Collect ShapeDecls and handler nodes ANYWHERE in the tree, not only at top level: a shape or a
    # loose handler declared inside a `journey` body is just as real as a top-level one, and scanning
    # only `program.statements` would silently miss it (the pre-rewrite scanner walked recursively).
    def _collect(kinds):
        found = []

        def w(n):
            if n is None or isinstance(n, (str, int, float, bool)):
                return
            if isinstance(n, (list, tuple)):
                for c in n:
                    w(c)
                return
            if not is_dataclass(n):
                return
            if type(n).__name__ in kinds:
                found.append(n)
            for f in fields(n):
                w(getattr(n, f.name, None))
        w(getattr(program, 'statements', None) or [])
        return found

    # shape name -> {loose unique field name: line}
    loose_unique = {}
    for st in _collect(('ShapeDecl',)):
        for fld in (getattr(st, 'fields', None) or []):   # LOOSE fields only, never table-owned
            if any(getattr(m, 'modifier_type', '') == 'unique'
                   for m in (getattr(fld, 'modifiers', None) or [])):
                loose_unique.setdefault(str(getattr(st, 'name', '') or ''), {})[
                    str(getattr(fld, 'name', '') or '')] = (
                        getattr(fld, 'line', 0) or getattr(st, 'line', 0) or 0)
    if not loose_unique:
        return {}, [], []

    def _writes_in(body):
        out = []

        def w(n):
            if n is None or isinstance(n, (str, int, float, bool)):
                return
            if isinstance(n, (list, tuple)):
                for c in n:
                    w(c)
                return
            if not is_dataclass(n):
                return
            if type(n).__name__ in WRITES:
                out.append(n)
            for f in fields(n):
                w(getattr(n, f.name, None))
        w(body)
        return out

    enforceable = {}          # table -> set(cols)
    seen_static = set()       # (shape, col)
    seen_dynamic = {}         # (shape, col) -> line
    for st in _collect(HANDLERS):
        sname = str(getattr(st, 'shape', '') or '')
        cols = loose_unique.get(sname)
        if not cols:
            continue
        for block in _writes_in(getattr(st, 'body', None)):
            written = {str(getattr(fv, 'name', '') or '')
                       for fv in (getattr(block, 'fields', None) or [])}
            hit = written & set(cols.keys())
            if not hit:
                continue
            tname = _static_target_name(getattr(block, 'target', None)
                                        or getattr(block, 'source', None))
            for col in hit:
                if tname:
                    enforceable.setdefault(tname, set()).add(col)
                    seen_static.add((sname, col))
                else:
                    seen_dynamic[(sname, col)] = cols[col]

    undecidable, decorative = [], []
    for sname, cols in loose_unique.items():
        for col, line in cols.items():
            key = (sname, col)
            if key in seen_dynamic:                       # any dynamic write -> refuse
                undecidable.append((sname, col, seen_dynamic[key]))
            elif key not in seen_static:                  # never written in a handler body
                decorative.append((sname, col, line))
    return enforceable, undecidable, decorative


def resolve_join_fields(program):
    """Every field a native join (`with`/`with.required`/`with.all`) actually matches on, so an
    index can be created ALONGSIDE the table rather than discovered as a missing one later --
    "join-field indexes, created with a table, listed for an existing one" (Ron's operations
    list). The SAME source of truth a build pass can use for both halves: the runtime reads
    this to CREATE the index when a table is first made, and (not built here, see
    BUILD-LANE-DEFERRALS.md) a connect-time advisory would read it to LIST a missing one on a
    table that already existed.

    Returns {table: {field, ...}} -- the CHILD (inner) table's own field named in each
    `match <field> to <outer>.<col>` clause, the column the join filters children by for every
    parent row, mirroring exactly what `_exec_find_joined` itself resolves at run (`mc.field`
    for the match field, `_resolve_source_parts(join.source, ...)` for the table).

    STATIC AND CONSERVATIVE, same posture as `resolve_loose_unique`: only a join whose source is
    a LITERAL table reference is resolved, by routing through the SAME `_split_table_ref` the
    runtime itself uses (`MohioInterpreter._resolve_source_parts`) rather than re-deriving the
    `db.` stripping rule here and risking a second place that can disagree with it. Verified by
    running THROUGH THE REAL `mio.py` PARSE PATH, not a hand-built parser instance: a locally
    built `Lark(...)` test parser resolved `with purchases from db.jo`'s source as a `DbRef`,
    but `mio.py`'s own cached/configured parser resolved the SAME source text as a
    `DottedName(parts=['db','jo'])` -- the two ambiguity-resolution paths genuinely disagree on
    which AST shape to emit, so both are handled here. A computed source (`with ... from call
    <task>`) is refused by the grammar itself before this pass ever runs, so "dynamic source,
    not indexed" is enforced one layer up from here.
    """
    from mohio_ast import JoinBlock as _JB, MatchClause as _MC, DbRef as _DbRef, DottedName as _DN
    from mohio_runtime_vocab import split_table_ref_parts

    def _walk(n, out):
        if n is None or isinstance(n, (str, int, float, bool)):
            return
        if isinstance(n, (list, tuple)):
            for c in n:
                _walk(c, out)
            return
        if not is_dataclass(n):
            return
        if isinstance(n, _JB):
            out.append(n)
        for f in fields(n):
            _walk(getattr(n, f.name, None), out)

    joins = []
    _walk(getattr(program, 'statements', None) or [], joins)

    fields_by_table = {}
    for jb in joins:
        src = getattr(jb, 'source', None)
        if isinstance(src, _DbRef):
            raw = src.table
        elif isinstance(src, _DN):
            raw = '.'.join(str(p) for p in (src.parts or []))
        else:
            continue  # a dynamic/computed join source -- undecidable, not indexed here
        table, column = split_table_ref_parts(raw)
        if not table or column:
            # A qualified column reference, or a resolution _split_table_ref itself could not
            # make sense of, is not a bare table source for a join -- not indexed here either.
            continue
        for mc in (getattr(jb, 'body', None) or []):
            if isinstance(mc, _MC) and getattr(mc, 'field', ''):
                fields_by_table.setdefault(str(table), set()).add(str(mc.field))
    return fields_by_table


def scan_shape_unique_governs_no_table(program):
    """`unique` on a loose shape field that is NEVER WRITTEN is inert, and says so -- once.

    Plain-shape `unique` is now enforced: `resolve_loose_unique` finds the table a shape is saved to
    and a real database index lands there, so a loose `unique` on a shape a handler writes is a
    working constraint and earns no warning (it used to warn on every one -- twenty-five explanations
    of a working feature, which trains a developer to scroll past warnings). A runtime-chosen target
    is a REFUSAL, not a warning (see `scan_loose_unique_dynamic_target`).

    What remains a warning is the genuinely decorative case: a loose `unique` on a shape that is never
    written in any handler body, so there is no write for the constraint to attach to. That is inert
    rather than wrong (the shape may describe an API response or a non-table structure), so it warns
    rather than refuses -- honest, and rare, not the per-field noise it replaced.
    """
    _enf, _undecidable, decorative = resolve_loose_unique(program)
    warnings = []
    for sname, col, line in decorative:
        warnings.append(CompileWarning(
            f"`unique` on `{col}` in shape `{sname}` is inert: this shape is never written to a "
            f"table, so there is nothing for the uniqueness constraint to attach to. Remove `unique` "
            f"if the shape only describes data; or save the shape to a table (a handler that writes "
            f"it, or a `<name> as table` block) to make the constraint real.",
            line=line, code="unique_governs_no_table"))
    return warnings


_CM_MARKER_NODES = ('CmPurgeBlock', 'CmRetainStmt', 'CmReportStmt',
                    'CmExpireStmt', 'CmLockStmt', 'CmNotifyStmt')
_WRITE_NODES = ('SaveBlock', 'UpdateBlock', 'SaveOrUpdateBlock', 'SaveAllBlock', 'RemoveBlock')
# NARROW compliance-shape signals, deliberately conservative (Ron 2026-09-24: a false positive on an
# ordinary app is worse than a missed one). A table NAME that reads like an audit/evidence store, and
# a shape FIELD named unambiguously for an audit record. LEFT OUT on purpose, and reported: `actor`,
# `reason`, and a bare `by` (ordinary apps use these), and "a record that describes another write"
# (no static signal for it that does not fire on ordinary joins/foreign keys).
_COMPLIANCE_TABLE_WORDS = ('audit', 'evidence', 'compliance')
_AUDIT_FIELD_NAMES = frozenset({'approved_by', 'performed_by', 'changed_by', 'audited_by'})


def _write_target_table_name(node):
    """The bare table name a write node targets, lowercased, or None. A target is a DbRef (`.table`),
    a DottedName (`.parts`, e.g. ['db', 'audit_events']), or a plain string like 'db.audit_events'."""
    for attr in ('target', 'source'):
        t = getattr(node, attr, None)
        if t is None:
            continue
        tbl = getattr(t, 'table', None)
        if tbl:
            return str(tbl).strip().lower()
        parts = getattr(t, 'parts', None)
        if parts:
            return str(parts[-1]).strip().lower()
        return str(t).split('.')[-1].strip().lower()
    return None


def scan_compliance_shape_no_marker(program):
    """One warning when a program keeps its own audit/evidence records but carries NO compliance
    marker, so Mohio makes no compliance claim for it (Ron 2026-09-24). Compliance is opt-in by ONE
    marker: a sensitive field tag, a sector, or a .cm verb. With none, an audit table the program
    keeps itself is ordinary data the program can also alter -- and the developer should be told,
    once, rather than assume a guarantee that is not there.

    ONE WARNING PER PROGRAM. The signals are narrow on purpose (see the module constants above)."""
    from mohio_classification import RECOGNIZED_CLASSIFIERS as RECOGNIZED
    marker_tags = set(RECOGNIZED) - {'public'}   # `[public]` declassifies; it is not an opt-in
    has_marker = [False]
    shaped = [None]          # (line, reason) of the FIRST compliance-shape signal seen

    def walk(node):
        if node is None:
            return
        if isinstance(node, (list, tuple)):
            for c in node:
                walk(c)
            return
        if not is_dataclass(node):
            return
        tn = type(node).__name__
        if tn == 'SectorDecl' or tn in _CM_MARKER_NODES:
            has_marker[0] = True
        if tn == 'ShapeDecl':
            _flds = (node.every_field() if hasattr(node, 'every_field')
                     else (getattr(node, 'fields', None) or []))
            for fld in _flds:
                for m in (getattr(fld, 'modifiers', None) or []):
                    if str(getattr(m, 'modifier_type', '') or '') == 'tag':
                        raw = str(getattr(m, 'value', '') or '')
                        for part in [p.strip().lower() for p in raw.split(',') if p.strip()]:
                            if part in marker_tags:
                                has_marker[0] = True
                fname = str(getattr(fld, 'name', '') or '').lower()
                if fname in _AUDIT_FIELD_NAMES and shaped[0] is None:
                    shaped[0] = (getattr(node, 'line', 0) or 0,
                                 f"the shape `{getattr(node, 'name', '')}` declares an audit-record "
                                 f"field `{fname}`")
        if tn in _WRITE_NODES:
            tbl = _write_target_table_name(node)
            if tbl and any(w in tbl for w in _COMPLIANCE_TABLE_WORDS) and shaped[0] is None:
                shaped[0] = (getattr(node, 'line', 0) or 0,
                             f"a write to `{tbl}`, a table named like an audit or evidence store")
        for f in fields(node):
            walk(getattr(node, f.name, None))

    walk(program)
    if has_marker[0] or shaped[0] is None:
        return []
    line, why = shaped[0]
    return [CompileWarning(
        f"Line {line}: this program looks compliance-shaped ({why}), but it carries no compliance "
        f"marker, so Mohio makes NO compliance claim here: these writes are ordinary, with no audit "
        f"chain, no owe-and-recover, and no tamper-evidence, and an audit table you keep yourself is "
        f"data you can also alter. Compliance is opt-in by ONE marker -- add any one and the minimum "
        f"applies from that point:\n"
        f"    a field tag    card as text [pci]\n"
        f"    a sector       sector: financial\n"
        f"    a .cm marker   cm.retain db.events for 7 years",
        line=line, code="compliance_shape_no_marker")]


def scan_comparison_can_meet_empty(program):
    """A comparison that can meet an empty value warns at check (OR-5, RULED by Ron 2026-10-12).

    An empty value matches no comparison at run (outside a regulated sector the chain falls to
    `otherwise` with a warning; under one it refuses). So `when Pay.amount is above 100` on a request
    field that is not `required` can take no branch at all, and the line reads as if it always
    decides. Flagged, as the ruling names them: a range comparison (above, below, at least, at most,
    between, and the `(a > b)` forms) whose subject can be empty -- a shape field (a request field
    included) that is neither `required` nor given a `default`, or a field of a lookup result (`retrieve`,
    `find`, `grab`) that the table's shape does not declare `required` or give a `default` -- with no
    `when <field> is empty` guard in the same check."""
    from lark import Tree, Token

    def _can_be_empty(f):
        mods = {getattr(m, 'modifier_type', '') for m in (getattr(f, 'modifiers', None) or [])}
        return 'required' not in mods and 'default' not in mods

    optional = {}                                  # shape name -> {fields that can be empty}
    table_sure = {}                                # table name -> {fields required or defaulted}
    for n in _all_nodes(program):
        if type(n).__name__ != 'ShapeDecl':
            continue
        flds = list(getattr(n, 'fields', None) or [])
        for tname, t in (getattr(n, 'tables', None) or {}).items():
            tf = list(getattr(t, 'fields', None) or [])
            flds.extend(tf)
            table_sure.setdefault(str(tname), set()).update(
                str(f.name) for f in tf if getattr(f, 'name', None) and not _can_be_empty(f))
        optional[str(getattr(n, 'name', ''))] = {str(f.name) for f in flds
                                                 if getattr(f, 'name', None) and _can_be_empty(f)}
    from mohio_runtime_vocab import LIST_RESULT_PROPERTIES
    lookups = {}                                   # a lookup's result name -> the table it read
    list_lookups = set()                           # the result names that hold a LIST
    for n in _all_nodes(program):
        if type(n).__name__ not in ('RetrieveBlock', 'FindBlock', 'GrabBlock'):
            continue
        src = getattr(n, 'source', None)
        parts = [str(p) for p in (getattr(src, 'parts', None) or [])] if src is not None else []
        table = str(getattr(src, 'table', None) or (parts[-1] if parts else ''))
        # A find, and a retrieve.all / retrieve.every, hand back a list; a plain retrieve, a
        # retrieve.one/first/last and a grab hand back one record, whose `.count` is a real field.
        is_list = (type(n).__name__ == 'FindBlock'
                   or str(getattr(n, 'modifier', '') or '') in ('all', 'every'))
        for nm in (getattr(n, 'name', None), getattr(n, 'alias', None)):
            if nm:
                lookups[str(nm)] = table
                if is_list:
                    list_lookups.add(str(nm))

    def dotted(v):
        if type(v).__name__ == 'DottedName':
            return '.'.join(str(p) for p in (getattr(v, 'parts', None) or []))
        return None

    ranges = {'wc_above', 'wc_below', 'wc_not_above', 'wc_not_below', 'wc_between'}
    empties = {'wc_empty', 'wc_is_empty'}
    warnings = []
    for n in _all_nodes(program):
        if type(n).__name__ != 'CheckBlock':
            continue
        guarded, compared = set(), []
        for w in (getattr(n, 'when_clauses', None) or []):
            v = getattr(w, 'value', None)
            if isinstance(v, Tree):
                subj = next((c for c in v.children if not isinstance(c, Token)), None)
                name = dotted(subj)
                if v.data in empties and name:
                    guarded.add(name)
                elif v.data in ranges and name:
                    compared.append((name, getattr(w, 'line', 0) or getattr(subj, 'line', 0) or 0))
            elif type(v).__name__ == 'Condition' and getattr(v, 'op', '') in ('>', '<', '>=', '<='):
                name = dotted(getattr(v, 'left', None))
                if name:
                    compared.append((name, getattr(v, 'line', 0) or getattr(w, 'line', 0) or 0))
            elif getattr(w, 'condition', '') in ('above', 'below'):
                name = dotted(getattr(n, 'value', None))
                if name:
                    compared.append((name, getattr(w, 'line', 0) or 0))
        for name, line in compared:
            parts = name.split('.')
            if len(parts) != 2 or name in guarded:
                continue
            # A PROPERTY THE RUNTIME PUTS ON A LIST RESULT IS NOT A TABLE FIELD (EE-HC-02):
            # `items.count` is a number on every result, so "not declared required for the table"
            # was a false reason, and it fired on every `when (items.count > 0)` in an app.
            if parts[0] in list_lookups and parts[1] in LIST_RESULT_PROPERTIES:
                continue
            if parts[1] in optional.get(parts[0], ()):
                why = f"the field is neither `required` nor given a `default` in shape `{parts[0]}`"
            elif parts[0] in lookups and parts[1] not in table_sure.get(lookups[parts[0]], ()):
                why = (f"`{parts[0]}` is a lookup result, and `{parts[1]}` is not declared `required` "
                       f"or given a `default` for `{lookups[parts[0]]}`")
            else:
                continue
            warnings.append(CompileWarning(
                f"Line {line}: `{name}` can be empty ({why}), and an empty value matches no "
                f"comparison: this `when` answers no and the check goes on to `otherwise`. Guard it "
                f"first, `when {name} is empty` with its own answer, or give `{parts[1]}` a default "
                f"(`{parts[1]} as ... default ...`) or declare it `required`. (A warning here; under a "
                f"regulated sector an empty value in a comparison refuses at run.)",
                line=line, code="comparison_can_meet_empty"))
    return warnings


def scan_journey_without_timezone(program):
    """A journey that declares no timezone earns a check-time warning (RULED by Ron, 2026-10-15).
    Its zone is the app zone every calendar word reads (today, this_month, is.in) and every shown
    datetime is displayed in; with none declared that zone is UTC (or MOHIO_APP_TIMEZONE), which is
    rarely the day the app's people live in."""
    warnings = []
    for n in _all_nodes(program):
        if type(n).__name__ != 'JourneyDecl':
            continue
        if any(type(m).__name__ == 'JourneyMeta' and getattr(m, 'kind', '') == 'timezone'
               for m in (getattr(n, 'body', None) or [])):
            continue
        line = getattr(n, 'line', 0) or 0
        jname = getattr(n, 'name', '') or 'App'  # SILENT-SHAPE-OK: the word the message calls an unnamed journey
        warnings.append(CompileWarning(
            f"Line {line}: journey `{jname}` declares no timezone, so its days "
            f"(today, this_month, is.in) and every shown date and time are UTC, unless MOHIO_APP_TIMEZONE "
            f"says otherwise.",
            line=line,
            hint=("Declare the zone the app's days are in, inside the journey, for example:\n"
                  "        timezone \"Asia/Bahrain\"\n    Use timezone \"UTC\" if UTC is what you mean."),
            code="journey_no_timezone"))
    return warnings


def scan_page_class_outranked(program):
    """A page class a folder journey outranks (RULED by Ron 2026-10-22). The comparison is made where the
    cascade assembles the page with its journeys (mio.py `_apply_journey`), on the one shared class
    order, and recorded on the program; this hands each finding to the check as a warning. A page below
    an `[important]` floor never reaches here: the cascade refuses it outright."""
    return [CompileWarning(msg, line=line, code="page_class_outranked")
            for line, msg in (getattr(program, 'cascade_warnings', None) or [])]


def _save_table_name(target):
    """`db.events` -> 'events' for a save's target, whichever form it arrived in; '' when not a table."""
    parts = getattr(target, 'parts', None)
    if parts:
        parts = [str(p).replace('__USERVAR__', '') for p in parts]
        return parts[-1] if len(parts) >= 2 and parts[0] == 'db' else ''
    text = str(target or '')
    return text.split('.', 1)[1] if text.startswith('db.') else ''


def scan_default_not_on_ungoverned_save(program):
    """A shape default that a save leaves out of a table no shape governs (RULED by Ron 2026-10-22, the
    real mis-use site of A3 / PUB600-HC-01). A default is applied when a request is bound to the shape and
    on a write to a table a shape governs with `as table`, and never on a save to any other table (A3's
    runtime rule, `8e4b37d`). So a save inside a handler bound to `sh.X` that writes that request to such a
    table, and leaves out a field X gives a default, writes no value for it, whatever the default says.
    This replaces the note that fired on EVERY defaulted field, a scope explanation on correct code. A save
    that writes the field (`flow flow`, the bound value carries the default), a save to a governed table,
    and a save that writes none of the request's fields (an unrelated record) earn nothing."""
    nodes = list(_all_nodes(program))
    shapes = {str(getattr(n, 'name', '') or ''): n for n in nodes if type(n).__name__ == 'ShapeDecl'}
    governed = {str(t) for n in shapes.values() for t in (getattr(n, 'tables', None) or {})}
    warnings = []
    for h in nodes:
        if type(h).__name__ not in ('NewBlock', 'RequestInboundBlock'):
            continue
        sname = str(getattr(h, 'shape', '') or '')
        sname = sname[3:] if sname.startswith('sh.') else sname
        sd = shapes.get(sname)
        if sd is None:
            continue
        own = list(getattr(sd, 'fields', None) or [])
        names = {str(getattr(f, 'name', '') or '') for f in own}
        defaulted = [str(getattr(f, 'name', '') or '') for f in own
                     if any(str(getattr(m, 'modifier_type', '') or '') == 'default'
                            for m in (getattr(f, 'modifiers', None) or []))]
        if not defaulted:
            continue
        for s in _all_nodes(getattr(h, 'body', None) or []):
            if type(s).__name__ != 'SaveBlock':
                continue
            table = _save_table_name(getattr(s, 'target', None))
            if not table or table in governed:
                continue
            written = {str(getattr(fv, 'name', '') or '') for fv in (getattr(s, 'fields', None) or [])}
            if not (written & names):
                continue
            for d in defaulted:
                if d in written:
                    continue
                line = getattr(s, 'line', 0) or getattr(h, 'line', 0) or 0
                warnings.append(CompileWarning(
                    f"Line {line}: this save writes a `{sname}` request to db.{table}, a table no shape "
                    f"governs, and leaves out `{d}`, which `{sname}` gives a default. A default is applied "
                    f"when the request is bound and on a write to a table a shape governs with `as table`, "
                    f"never on this save, so db.{table} gets no `{d}`.",
                    line,
                    f"If db.{table} should hold it, write it on the save: `{d} {d}` (the bound value "
                    f"carries the default). If the table has no place for it, the save is right as it is.",
                    "default_not_on_ungoverned_save"))
    return warnings


WARNING_SCANS = (
    scan_default_not_on_ungoverned_save,
    scan_write_route_no_idempotent,
    scan_journey_without_timezone,
    scan_page_class_outranked,
    scan_same_path_two_classes_warning,
    scan_success_on_possibly_empty_lookup,
    scan_comparison_can_meet_empty,
    scan_release_never_held,
    scan_retry_after_send,
    scan_password_field_without_format,
    scan_compliance_shape_no_marker,
    scan_returning_call_without_capture,
    scan_check_with_no_branches,
    scan_shape_unique_governs_no_table,
    scan_financial_multi_write_no_transaction,
    scan_refuse_after_unwrapped_write,
    scan_upload_storage_durability,
    scan_ai_decide_never_invoked,
    scan_render_include_bare,
    scan_unrecognized_field_classifier,
    scan_decorative_shape_modifiers,
    scan_sector_route_unauthenticated,
    scan_typos,
    scan_event_name_typos,
    scan_audit_grade_requirement,
    scan_miofile_zone_coverage,
    scan_cookie_samesite_none_insecure,
    scan_transaction_onfailure_success,
    scan_webhook_unverified_warning,
    scan_webhook_freshness_absent,
)


def run_scans(program):
    """Run every canonical scanner. Returns (errors, warnings).

    A scanner must never take down the check: it reports, or it says nothing. But it must
    also never be SKIPPED because a caller forgot it existed -- which is exactly what a
    hand-copied list guarantees will happen eventually.
    """
    import sys as _sys
    from mohio_sector_loader import SectorVerificationError, SectorUnenforceableRuleError
    _DELIBERATE_REFUSAL = (SectorVerificationError, SectorUnenforceableRuleError)
    errors, warnings = [], []
    # Every scanner that touches a sector profile hits the SAME refusal independently (each one
    # loads the profile itself); without this, one bad profile reported as many identical errors
    # as scanners happened to read it, which reads as several problems instead of one.
    _refusals_seen = set()
    for scan in ERROR_SCANS:
        try:
            errors.extend(scan(program))
        except _DELIBERATE_REFUSAL as e:
            # A scanner that reads a sector profile can surface a DELIBERATE refusal (a
            # certified profile that fails verification, or a profile naming a rule nothing
            # enforces) -- the program's own content being refused on purpose, not a scanner
            # bug. Report it as the plain refusal it is, not as "report this bug" -- once, not
            # once per scanner that happens to read the same profile.
            if str(e) not in _refusals_seen:
                _refusals_seen.add(str(e))
                errors.append(CompileError(str(e), line=0))
        except Exception as e:
            # FAILS LOUD (ruled by Ron, 2026-10-03; was T1-SILENT-SWEEP-BATCH6-10's "still never
            # takes down the check, just print a warning" -- that still let a program report
            # CLEAN with a scanner's entire contribution silently missing, which is the same
            # silent-wrongness one layer further down). The other scanners still run (this does
            # not raise out of the loop), but the program itself now reports failed, never clean,
            # when a scanner could not finish.
            _name = getattr(scan, '__name__', str(scan))
            # The LINE here is the SCANNER's own definition (mohio_reachability.py), never a
            # line in the program being checked -- a scanner-level crash is not tied to one
            # program line, so pointing at the scanner's own source is the genuinely useful
            # line to report, not a placeholder.
            _scan_line = getattr(getattr(scan, '__code__', None), 'co_firstlineno', 0)
            print(f"  [mio check] scanner {_name!r} raised {type(e).__name__}: {e} -- its "
                  f"findings for this run are incomplete.", file=_sys.stderr)
            errors.append(CompileError(
                f"the {_name!r} scanner (mohio_reachability.py line {_scan_line}) crashed "
                f"({type(e).__name__}: {e}) and did not finish -- this is a compiler bug, not "
                f"a mistake in your program. Its findings are missing, so this reports as "
                f"failed rather than an unknown amount of checking passing as clean.\n"
                f"    Report this bug, naming {_name!r}.", line=_scan_line))
    # ONE KIND REFUSAL PER LINE. Two scans judge kinds (scan_value_kinds_known holds the general rule;
    # scan_other_kinds_known the 2026-10-14 true-or-false / uuid / text pairs), so a pair both see was
    # reported twice. The first refusal for a line stands; a second kind refusal on it adds nothing.
    _kind_lines, _kept = set(), []
    for _e in errors:
        _m = str(getattr(_e, 'message', _e))
        if 'so the comparison has no answer' in _m:
            _key = getattr(_e, 'line', None)
            if _key in _kind_lines:
                continue
            _kind_lines.add(_key)
        _kept.append(_e)
    errors[:] = _kept
    for scan in WARNING_SCANS:
        try:
            warnings.extend(scan(program))
        except _DELIBERATE_REFUSAL as e:
            # Same reasoning as the ERROR_SCANS branch above, reported as an error even though
            # this is nominally a warning-only scanner: a sector refusal is never advisory.
            if str(e) not in _refusals_seen:
                _refusals_seen.add(str(e))
                errors.append(CompileError(str(e), line=0))
        except Exception as e:
            # Same rule for a WARNING-only scanner: it cannot silently drop its contribution
            # either, but a missing warning is advisory, not a reason to fail the build -- so
            # this still reports the crash as a warning, never as an error.
            _name = getattr(scan, '__name__', str(scan))
            # Same reasoning as the ERROR_SCANS branch above: the LINE is the scanner's own
            # definition, the genuinely useful pointer for a compiler-bug report.
            _scan_line = getattr(getattr(scan, '__code__', None), 'co_firstlineno', 0)
            print(f"  [mio check] scanner {_name!r} raised {type(e).__name__}: {e} -- its "
                  f"findings for this run are incomplete.", file=_sys.stderr)
            warnings.append(CompileWarning(
                f"the {_name!r} scanner (mohio_reachability.py line {_scan_line}) crashed "
                f"({type(e).__name__}: {e}) and did not finish -- this is a compiler bug; its "
                f"findings for this run are missing.\n"
                f"    Report this bug, naming {_name!r}.", line=_scan_line, code="scanner_crashed"))
    return errors, warnings
