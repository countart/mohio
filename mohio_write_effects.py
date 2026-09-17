# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Phase 2 -- can iteration N run without iteration N-1?

WHAT THIS ANSWERS. Phase 1 normalized every write into a WriteIntent and left six dependency
questions reading UNKNOWN, because no def-use or dataflow analysis exists anywhere in this
compiler. This is that analysis, and the question it exists to settle is the one Phase 4 will act
on: is a write inside a loop provably independent across iterations, or does it depend on what
the iteration before it did?

IT DECIDES NOTHING AND CHANGES NOTHING. Phase 2 records a verdict on the IR. Every program still
executes exactly as it does today, one row at a time. Phase 4 is what reads the verdict.

THE DIRECTION OF ERROR IS THE WHOLE DESIGN. Saying DEPENDENT about an independent loop costs a
missed optimization -- the program runs the way it runs today, which is correct and slower than
it needed to be. Saying INDEPENDENT about a dependent loop lets a later phase batch writes that
had to happen in order, and that produces WRONG DATA with no error. Those two mistakes are not
comparable, so the analysis is built to make only the first one:

  - every question starts UNKNOWN, and UNKNOWN counts as DEPENDENT;
  - a question turns INDEPENDENT only when something positively proves it;
  - a node type this analysis does not recognise makes the answer UNKNOWN, never safe. The
    vocabulary below is a list of node types known to be harmless, and a list is a thing this
    project knows regrows one member at a time -- so an unrecognised node forbids rather than
    being assumed benign;
  - the overall verdict is INDEPENDENT only when ALL six questions are, and the ordering,
    failure and transaction contracts from the IR allow it too.

WHAT IT DOES NOT ATTEMPT. No heroic proofs, per the roadmap. There is no SSA, no CFG, and no
alias analysis here. What it has is the structure of the tree: which names a loop body reads,
which it writes, which relations it touches, and whether anything in it has an effect beyond the
write. That is enough to prove the common case safe and to refuse everything else.
"""
from __future__ import annotations

from dataclasses import dataclass

from mohio_write_intent import (FailureContract, OrderingContract, TransactionScope,
                                WriteIntent)


# EVERY GRANT OF INDEPENDENT BELOW IS AN EXPLICIT POSITIVE CONDITION, never a
# fall-through. INDEPENDENT is the answer that permits batching, so granting it by
# falling off the end of an if-chain means any branch added later, or any case
# nobody thought of, lands on `safe` by default. The fall-through is UNKNOWN, which
# forbids.
INDEPENDENT = "independent"
DEPENDENT = "dependent"
UNKNOWN = "unknown"


# ── the vocabulary of harmlessness ──────────────────────────────────────────────────────
#
# A node type is listed here only when its presence in a loop body cannot make one iteration
# depend on another. EVERYTHING NOT LISTED FORBIDS, which is the opposite of how such a list
# usually decays: the danger is not that this list is incomplete, it is that somebody adds a node
# type to it without checking. An incomplete list costs a missed optimization; a wrong entry
# costs wrong data.
_PURE_VALUE_NODES = frozenset({
    "Literal", "DottedName", "EnvRef", "SecretRef", "DbRef", "ShRef",
    "MathExpr", "ConcatExpr", "TemplateString", "TypeCastExpr", "RoundExpr",
    "Condition", "NotCondition", "AndCondition", "OrCondition",
    "FieldValue", "MatchClause", "WhereClause", "AndClause", "Closer",
    "ListLiteral", "PercentLit", "ColorLit", "DimensionLit",
    # CONTROL STRUCTURE, which carries no effect of its own. What is INSIDE a handler or a
    # branch is walked separately and judged on its own terms, so recognising the wrapper does
    # not let anything through: the `show` inside an on.failure is still found and still forbids.
    # The write's own handler is separately reported by the IR's failure contract, which is the
    # thing that actually matters about it.
    "OnFailure", "OnSuccess", "OnError", "OtherwiseClause", "CheckBlock", "CheckWhen",
    "OrIfClause", "UnlessGuard", "IfGuard", "ThenChain", "TrailingQualifier",
    # TryBlock IS DELIBERATELY NOT HERE. It changes what failure means for everything inside it
    # and this analysis has no ruling about that, so it forbids. A missed optimization is the
    # cost; wrong data is the alternative.
})

# The write verbs themselves, which are the thing being analysed rather than an effect beside it.
_WRITE_NODES = frozenset({
    "SaveBlock", "SaveOrUpdateBlock", "SaveAllBlock", "UpdateBlock",
    "RemoveBlock", "RemoveAllBlock", "ModifyBlock", "FlowStmt", "SqlBlock",
})

# Read verbs: they do not mutate, but reading the relation being written is its own question.
_READ_NODES = frozenset({
    "FindBlock", "RetrieveBlock", "GrabBlock", "GetBlock", "PullBlock",
    "CheckMioqlBlock", "SummarizeBlock", "CompareBlock", "CalculateBlock", "JoinBlock",
})

# NONDETERMINISTIC: the value differs per evaluation, so how many times it is evaluated and in
# what order is observable. Batching changes both.
_NONDETERMINISTIC_NODES = frozenset({
    "NowCall", "UuidCall", "RandomValue", "TimeExpr", "DatetimeExpr", "SinceExpr",
})

# AN EFFECT BEYOND THE WRITE. Anything that leaves the process, talks to a person, or can be
# observed in its own right. Batching reorders or coalesces the writes around these.
_SIDE_EFFECT_NODES = frozenset({
    "ShowStmt", "ShowBlock", "SendStmt", "BroadcastStmt", "StreamStmt", "NotifyStmt",
    "MiohttpStmt", "MiofileStmt", "MiomailStmt", "MioLogStmt", "MioCacheStmt",
    "ServiceCallStmt", "MioconnectCall", "RunBlock", "RunAsyncBlock", "WaitForStmt",
    "AiDecideBlock", "AiDecideInvoke", "AiCreateStmt", "AiRespondBlock", "AiRankBlock",
    "AiCompareBlock", "AiExplainBlock", "AiAuditStmt", "AiResolveBlock", "AiAgentBlock",
    "GiveBackStmt", "GiveStmt", "HaltStmt", "StopStmt", "SkipStmt", "JumpToStmt",
    "RaiseStmt", "MioCookieSet", "MioCookieDelete", "ViewCallStmt", "RespondAsStmt",
    "CmPurgeBlock", "CmRetainStmt", "CmReportStmt", "CmExpireStmt", "CmLockStmt",
    "CmNotifyStmt", "SignBlock", "EncodeStmt", "DecodeStmt", "ParseStmt",
})


@dataclass
class EffectSummary:
    """What a loop body does, read off the tree."""
    names_read: frozenset = frozenset()
    names_written: frozenset = frozenset()
    relations_read: frozenset = frozenset()
    relations_written: tuple = ()
    side_effects: tuple = ()
    nondeterministic: tuple = ()
    unrecognised: tuple = ()          # node types this analysis has no ruling about


def _iter_nodes(node, seen=None):
    """Every node under this one, once."""
    if seen is None:
        seen = set()
    if node is None:
        return
    if isinstance(node, (list, tuple)):
        for n in node:
            yield from _iter_nodes(n, seen)
        return
    if not hasattr(node, "__dict__"):
        return
    if id(node) in seen:
        return
    seen.add(id(node))
    yield node
    for value in list(vars(node).values()):
        if isinstance(value, (list, tuple)) or hasattr(value, "__dict__"):
            yield from _iter_nodes(value, seen)


def _dotted_root(node):
    """The base name a dotted reference starts from: `row.v` reads `row`."""
    parts = getattr(node, "parts", None)
    if parts:
        return str(parts[0])
    name = getattr(node, "name", None)
    return str(name) if name else None


def _relation_of(node):
    from mohio_write_intent import _name_of
    for attr in ("target", "source", "collection"):
        value = getattr(node, attr, None)
        got = _name_of(value)
        if got:
            return got
    return None


def summarize_body(body):
    """Read a loop body and report what it does. Never guesses: an unrecognised node is recorded.

    `names_written` covers a plain assignment and any verb that binds a result with `as NAME`,
    because both make a name defined in one iteration visible to the next.
    """
    reads, writes, rel_read, rel_write = set(), set(), set(), []
    effects, nondet, unknown_nodes = [], [], []

    for node in _iter_nodes(body):
        kind = type(node).__name__

        if kind == "Assignment":
            target = getattr(node, "name", None)
            if target:
                writes.add(str(target))
            continue
        if kind in ("HoldDecl", "LockDecl", "CreateBlock"):
            target = getattr(node, "name", None)
            if target:
                writes.add(str(target))
            continue

        alias = getattr(node, "alias", None)
        if alias:
            writes.add(str(alias))
        # a read verb binds its result under its own `name`
        if kind in _READ_NODES:
            bound = getattr(node, "name", None)
            if bound:
                writes.add(str(bound))
            got = _relation_of(node)
            if got:
                rel_read.add(got)
            continue

        if kind in _WRITE_NODES:
            got = _relation_of(node)
            if got:
                rel_write.append(got)
            continue

        if kind in _NONDETERMINISTIC_NODES:
            nondet.append(kind)
            continue
        if kind in _SIDE_EFFECT_NODES:
            effects.append(kind)
            continue

        if kind == "DottedName":
            root = _dotted_root(node)
            if root:
                reads.add(root)
            continue

        if kind in _PURE_VALUE_NODES:
            continue

        # NOT RECOGNISED. Not assumed harmless -- recorded, and its presence forbids.
        unknown_nodes.append(kind)

    return EffectSummary(frozenset(reads), frozenset(writes), frozenset(rel_read),
                         tuple(rel_write), tuple(sorted(set(effects))),
                         tuple(sorted(set(nondet))), tuple(sorted(set(unknown_nodes))))


def analyze_loop_write(intent: WriteIntent, loop_node, loop_item=None):
    """Answer the six questions for one write inside one loop, and write them onto the IR.

    Returns (verdict, reasons). The verdict is also recorded on `intent.dependencies`.
    """
    body = getattr(loop_node, "body", None) or []
    summary = summarize_body(body)
    dep = intent.dependencies
    reasons = []

    # THE STARTING ANSWER IS THE FORBIDDING ONE, set here rather than left to the
    # dataclass default, so the rule is visible at the top of the function that applies
    # it. Every grant below is a positive condition; a case that matches no branch leaves
    # UNKNOWN standing, and UNKNOWN forbids. There is deliberately no trailing else: a
    # fall-through that assigns anything is how `safe` becomes the default by accident.
    for _q in ("loop_carried", "result_consumed_by_next",
               "intervening_read_of_target", "write_after_write",
               "external_side_effect_between", "nondeterministic_inputs"):
        setattr(dep, _q, UNKNOWN)

    # THE LOOP VARIABLE IS NOT A CARRIED DEPENDENCY. `repeat each row in rows` rebinds `row` from
    # the collection every iteration; it is the loop's input, not a value one iteration leaves
    # for the next.
    carried_names = set(summary.names_written)
    if loop_item:
        carried_names.discard(str(loop_item))

    # 1. does the body read something the body also writes?
    overlap = carried_names & summary.names_read
    if summary.unrecognised:
        dep.loop_carried = UNKNOWN
        reasons.append("the body contains %s, which this analysis has no ruling about"
                       % ", ".join(summary.unrecognised[:3]))
    elif overlap:
        dep.loop_carried = DEPENDENT
        reasons.append("the body reads %s, which the body also writes, so one iteration can see "
                       "what the one before it left" % ", ".join(sorted(overlap)[:3]))
    elif not overlap:
        # GRANTED EXPLICITLY: nothing the body writes is read back by it, and every node in the
        # body was recognised. Both halves are required.
        dep.loop_carried = INDEPENDENT

    # 2. does a later iteration consume this write's result?
    alias = intent.result_bound_to
    if summary.unrecognised:
        dep.result_consumed_by_next = UNKNOWN
    elif alias and alias in summary.names_read:
        dep.result_consumed_by_next = DEPENDENT
        reasons.append("the write binds `%s` and the body reads it, so each row needs the id the "
                       "row before it generated" % alias)
    elif not alias or alias not in summary.names_read:
        dep.result_consumed_by_next = INDEPENDENT

    # 3. does the body read the relation it writes?
    target = intent.target.relation
    if summary.unrecognised:
        dep.intervening_read_of_target = UNKNOWN
    elif target and target in summary.relations_read:
        dep.intervening_read_of_target = DEPENDENT
        reasons.append("the body reads `%s`, the same relation it writes, so a later iteration "
                       "can observe an earlier one's row" % target)
    elif not target or target not in summary.relations_read:
        dep.intervening_read_of_target = INDEPENDENT

    # 4. more than one write to the same relation in one iteration?
    if summary.unrecognised:
        dep.write_after_write = UNKNOWN
    elif target and list(summary.relations_written).count(target) > 1:
        dep.write_after_write = DEPENDENT
        reasons.append("the body writes `%s` more than once, so the order of those writes is "
                       "part of what the loop means" % target)
    elif not target or list(summary.relations_written).count(target) <= 1:
        dep.write_after_write = INDEPENDENT

    # 5. anything with an effect of its own beside the write?
    if summary.side_effects:
        dep.external_side_effect_between = DEPENDENT
        reasons.append("the body also does %s, which batching would reorder around the writes"
                       % ", ".join(summary.side_effects[:3]))
    elif summary.unrecognised:
        dep.external_side_effect_between = UNKNOWN
    elif not summary.side_effects and not summary.unrecognised:
        dep.external_side_effect_between = INDEPENDENT

    # 6. a value that differs per evaluation?
    if summary.nondeterministic:
        dep.nondeterministic_inputs = DEPENDENT
        reasons.append("the body uses %s, whose value depends on when and how often it is "
                       "evaluated" % ", ".join(summary.nondeterministic[:3]))
    elif summary.unrecognised:
        dep.nondeterministic_inputs = UNKNOWN
    elif not summary.nondeterministic and not summary.unrecognised:
        dep.nondeterministic_inputs = INDEPENDENT

    # ── the contracts Phase 1 already settled, which bear on the same decision ───────────
    if intent.failure == FailureContract.PER_ROW_OBSERVABLE:
        reasons.append("the write carries its own on.failure, so which row failed is observable "
                       "and any plan that cannot say so is illegal")
    if intent.ordering == OrderingContract.EXECUTION_ORDER_OBSERVABLE:
        reasons.append("execution order is observable for this verb")
    if intent.transaction == TransactionScope.UNKNOWN:
        reasons.append("the transaction scope is unknown")

    answers = (dep.loop_carried, dep.result_consumed_by_next, dep.intervening_read_of_target,
               dep.write_after_write, dep.external_side_effect_between,
               dep.nondeterministic_inputs)

    blocking_contract = (intent.failure == FailureContract.PER_ROW_OBSERVABLE
                         or intent.ordering == OrderingContract.EXECUTION_ORDER_OBSERVABLE
                         or intent.transaction == TransactionScope.UNKNOWN)

    # THE SAFE ANSWER FIRST, then earned. Written this way round on purpose: forbidding is the
    # default and permitting is granted, so nothing a later edit adds can fall through into
    # `safe to batch`.
    verdict = DEPENDENT
    if all(a == INDEPENDENT for a in answers) and not blocking_contract and intent.normalized:
        verdict = INDEPENDENT
    if verdict == DEPENDENT:
        if not intent.normalized:
            reasons.append("the write itself is not normalized, so there is nothing to reason "
                           "about")
        if not reasons:
            reasons.append("at least one question could not be answered, and an unanswered "
                           "question forbids")

    dep.verdict = verdict
    dep.reasons = tuple(reasons)
    return verdict, tuple(reasons)


def analyze_program(program, classifier=None):
    """Every write in a program, with its loop context resolved and its verdict recorded.

    A write NOT inside a loop gets no verdict: the question Phase 2 asks is about iterations, and
    a write that runs once has none. It is left UNKNOWN, which forbids, because a plan that wants
    to do something clever with a single write has to justify that on its own terms.
    """
    from mohio_write_intent import lower_program, lower_write, classify_program

    if classifier is None:
        classifier = classify_program(program)

    loops = []      # (loop_node, item, txn_scope)
    out = []

    def walk(node, txn, loop):
        if node is None:
            return
        if isinstance(node, (list, tuple)):
            for n in node:
                walk(n, txn, loop)
            return
        kind = type(node).__name__
        if kind == "TransactionBlock":
            walk(getattr(node, "body", None), TransactionScope.ENCLOSING_BLOCK, loop)
            return
        if kind in ("EachBlock", "RepeatBlock", "WhileBlock", "LoopBlock"):
            item = getattr(node, "item", None)
            walk(getattr(node, "body", None), txn, (node, item))
            return
        if kind in _WRITE_NODES:
            intent = lower_write(node, classifier, False, txn)
            if intent is None:
                return
            intent.in_loop = loop is not None
            if loop is not None:
                loop_node, item = loop
                analyze_loop_write(intent, loop_node, item)
            if not intent.in_loop:
                # A WRITE THAT RUNS ONCE HAS NO ITERATION-TO-ITERATION QUESTION, so there is
                # nothing here to prove safe. It stays UNKNOWN, which forbids: a later phase
                # wanting to do something with a single write has to justify that on its own
                # terms rather than inherit a verdict this analysis never made.
                intent.dependencies.verdict = UNKNOWN
                intent.dependencies.reasons = (
                    "this write is not inside a loop, so there is no iteration-to-iteration "
                    "question to answer",)
            out.append(intent)
            return
        for attr in ("body", "statements", "when_clauses", "otherwise", "handlers",
                     "listeners", "body_stmts"):
            walk(getattr(node, attr, None), txn, loop)

    walk(getattr(program, "statements", None) or getattr(program, "body", None),
         TransactionScope.PER_ROW, None)
    return out
