# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""WriteIntent -- the first normal form for a Mohio write.

WHY THIS EXISTS. Phase 0 of the write-planner work went looking for the internal representation
of a write and found there is not one. Eight node types call the destination `target` on two of
them and `source` on four; the predicate is `condition`, `match`, `where`, or folded into `body`,
depending on which word the author typed. There is no shared base class, no common accessor, and
insert / update / delete / upsert are normalized nowhere in the tree. The nearest thing is a
hand-maintained list of class-name strings inside one compile-time scan, which is already missing
two of the verbs it should name -- this project's own hand-listed-family failure, in miniature.

A planner cannot analyse eight divergent shapes. So WriteIntent is not a wrapper over an existing
normal form: it IS the first one.

WHAT PHASE 1 IS, AND IS NOT. This module builds the representation and lowers the verbs into it.
It does not plan, rank, optimize, or execute. Nothing here changes what any program does, and
that is the acceptance bar rather than a caveat: the IR is the shape Phase 4 will analyse, and it
is worth nothing if producing it moves a single result.

THE PRINCIPLES, which are not this module's to negotiate:

  LEGALITY FIRST, COST SECOND.  Nothing here ranks anything.
  UNKNOWN = NOT SAFE.           Every contract below defaults to the answer that forbids
                                optimization. A field this phase cannot determine is not left
                                empty or guessed at -- it is UNKNOWN, and UNKNOWN forbids.
  SCALAR IS THE UNIVERSAL SAFE FALLBACK.  A verb that cannot be faithfully represented is marked
                                NOT NORMALIZED and keeps its current path. It is never
                                approximated, because an approximate IR is worse than none: it
                                reads as knowledge.
  COMPLIANCE PARTICIPATES.      The compliance contract is a field of the IR, populated at
                                lowering, not a question asked later during execution.

THE CONTRACTS are the roadmap's, kept in its order and its names, so the two documents can be
read against each other without translation: operation, target, data, result, ordering, failure,
transaction, dependency, compliance.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


# ── the vocabularies ────────────────────────────────────────────────────────────────────
#
# Each is a small closed set with an UNKNOWN member, and UNKNOWN is never a hole to be filled in
# later by whoever needs an answer -- it is the answer, and it means "no transformation may rely
# on this".

class Operation:
    """What the write MEANS, independent of which word the author typed.

    Four verbs in the language lower to INSERT, and the difference between them is a predicate,
    not an operation. That collapse is the whole point of the normal form.
    """
    INSERT = "insert"
    UPDATE = "update"
    DELETE = "delete"
    UPSERT = "upsert"
    OPAQUE = "opaque"          # raw sql: the operation is inside text this compiler did not write


class RowSource:
    """Where the rows come from, which is the shape the batching question is asked about."""
    SINGLE_LITERAL = "single_literal"      # fields written out in the block
    COLLECTION = "collection"              # save all ... from <a collection>
    QUERY_RESULT = "query_result"          # modify every x in db.t -- rows the runtime read back
    PREDICATE_ONLY = "predicate_only"      # update / remove: a predicate, no row values
    WHOLE_TABLE = "whole_table"            # remove all
    UNKNOWN = "unknown"


class ResultNeed:
    """What the program observably needs back.

    PRODUCED IS NOT CONSUMED, and nothing in the tree distinguishes them today: `db.save`
    returns an id on every backend and every caller materializes it whether or not the program
    reads it. Deciding that needs the consumption analysis Phase 2 builds, so this phase records
    what is STRUCTURALLY visible -- an alias binds the result, therefore it is consumed -- and
    UNKNOWN everywhere else.
    """
    NONE = "none"
    AFFECTED_COUNT = "affected_count"
    GENERATED_ID = "generated_id"
    RETURNED_ROW = "returned_row"
    PER_ROW_STATUS = "per_row_status"
    UNKNOWN = "unknown"


class OrderingContract:
    IRRELEVANT = "irrelevant"
    INPUT_ORDER_OBSERVABLE = "input_order_observable"
    EXECUTION_ORDER_OBSERVABLE = "execution_order_observable"
    UNKNOWN = "unknown"


class FailureContract:
    """WHOLE_OPERATION means a failure anywhere may fail the lot. PER_ROW_OBSERVABLE means the
    program can tell WHICH row failed, which removes every plan that cannot attribute a failure
    to one row. It is statically visible: a handler is a field on the node.
    """
    WHOLE_OPERATION = "whole_operation"
    PER_ROW_OBSERVABLE = "per_row_observable"
    UNKNOWN = "unknown"


class TransactionScope:
    """Outside a `transaction` block there is NO transaction: every write commits itself, so the
    atomicity scope of the default Mohio write is one row and the default physical shape is
    insert-commit-insert-commit. Inside one, the scope is the block."""
    PER_ROW = "per_row"
    ENCLOSING_BLOCK = "enclosing_block"
    UNKNOWN = "unknown"


class Regulated:
    """Is this write regulated?

    THREE CONDITIONS make a write audited at runtime, and only two of them are statically
    knowable: a tagged field being written, and an active sector. The third is "this table is
    already known to hold sensitive data", which lives in per-process in-memory state, so two
    processes writing the same table can disagree about it.

    A plan whose legality depends on whether a write is regulated cannot rest on that. So the
    answer here is YES when either static condition holds, and UNKNOWN otherwise -- never NO on
    the strength of state that differs between processes. UNKNOWN forbids, which is the same
    protection YES gives, arrived at honestly.
    """
    YES = "yes"
    UNKNOWN = "unknown"


class Durability:
    """No durability knob is set on any engine anywhere in this tree -- no synchronous_commit, no
    innodb_flush_log_at_trx_commit, no journal_mode, no WriteConcern. The honest Phase 1 answer is
    that the slot exists and reads ENGINE_DEFAULT, recorded so the gap is not rediscovered later
    as a surprise."""
    ENGINE_DEFAULT = "engine_default"


@dataclass
class ComplianceContract:
    """What compliance needs the planner to know, from the compiler's EXISTING model.

    Deliberately not a redesign of the compliance model: it reads the same inputs the runtime
    gate reads -- a `[phi]`/`[pci]`/`[pii]` tag on a shape field, a `sector:` line -- and reports
    them where a plan can see them before it is chosen, rather than discovering them mid-write.
    """
    regulated: str = Regulated.UNKNOWN
    audit_required: str = Regulated.UNKNOWN
    tagged_fields: tuple = ()
    sector_active: bool = False
    per_row_evidence_required: bool = True     # the ruling: per-line records, never a count
    atomic_coupling_required: bool = True      # data and its evidence commit together
    durability: str = Durability.ENGINE_DEFAULT
    why: str = ""


@dataclass
class DependencyContract:
    """The questions Phase 2 answers. Every one of them starts UNKNOWN, and that is not a
    placeholder -- no def-use or dataflow analysis exists anywhere in this compiler, so UNKNOWN
    is the true answer today and the conservative rule is the only implementable one."""
    loop_carried: str = "unknown"
    result_consumed_by_next: str = "unknown"
    intervening_read_of_target: str = "unknown"
    write_after_write: str = "unknown"
    external_side_effect_between: str = "unknown"
    nondeterministic_inputs: str = "unknown"
    # PHASE 2 WRITES THESE. The six above are the questions; these two are the answer and the
    # reasoning behind it.  is independent only when every question is, and only when
    # the ordering, failure and transaction contracts allow it too -- one unanswered question
    # is enough to forbid.
    verdict: str = "unknown"
    reasons: tuple = ()


@dataclass
class WriteTarget:
    """ONE name for the destination, which is the split this normal form exists to end.

    `target` on two node types and `source` on four, for the same concept, is how a planner ends
    up with four accessors for one question. Here it is `relation`, everywhere, for every verb.
    """
    datasource: Optional[str] = None        # the connection name, `db` today at every call site
    relation: Optional[str] = None          # table / collection
    identity: tuple = ()                    # the columns that identify a row, when declared
    resolved: bool = False                  # did the lowering actually resolve the name


@dataclass
class WriteIntent:
    """One write, normalized. Produced by `lower_write`, consumed by nothing in Phase 1."""
    operation: str = Operation.OPAQUE
    target: WriteTarget = field(default_factory=WriteTarget)

    row_source: str = RowSource.UNKNOWN
    field_names: tuple = ()                 # names only -- a value never enters the IR
    predicate_fields: tuple = ()            # what the write is matched on
    cardinality: Optional[int] = None       # known row count, when it is known

    result: str = ResultNeed.UNKNOWN
    result_bound_to: Optional[str] = None   # `save ... as NAME` -- structurally consumed
    ordering: str = OrderingContract.UNKNOWN
    failure: str = FailureContract.UNKNOWN
    transaction: str = TransactionScope.UNKNOWN

    dependencies: DependencyContract = field(default_factory=DependencyContract)
    compliance: ComplianceContract = field(default_factory=ComplianceContract)

    # PROVENANCE, so a plan can always be traced back to the line that asked for it.
    verb: str = ""
    node_type: str = ""
    line: int = 0

    # NOT NORMALIZED is a first-class answer, not a failure. A verb whose meaning this phase
    # cannot represent faithfully keeps its current path and says so, because an IR that guesses
    # is worse than no IR: it reads as knowledge.
    # SET BY PHASE 2. A write inside a loop is the one the batching question is about; a
    # write that runs once has no iteration-to-iteration question at all.
    in_loop: bool = False

    normalized: bool = True
    not_normalized_reason: str = ""

    def describe(self):
        """One line, for reading a lowering in a test or at the command line.

        NO PLACEHOLDER STANDS IN FOR AN ANSWER. An unresolved destination and a write with no
        statically visible field names are different facts, and a `?` or a `-` would report them
        as the same shrug. Each says what it is.
        """
        if self.target.relation is not None:
            where = self.target.relation
        else:
            where = "<unresolved>"
        if self.field_names:
            fields = ",".join(self.field_names)[:26]
        else:
            fields = "<none static>"
        if not self.normalized:
            return "%-16s %-10s NOT NORMALIZED (%s)" % (self.verb, where,
                                                        self.not_normalized_reason)
        return ("%-16s %-9s %-10s rows=%-15s fields=%-28s result=%-15s txn=%-15s "
                "regulated=%s" % (self.verb, self.operation, where, self.row_source,
                                  fields, self.result, self.transaction,
                                  self.compliance.regulated))


# ── lowering ────────────────────────────────────────────────────────────────────────────
#
# Read from the node, never inferred. Each verb needs its fields extracted differently, because
# each node named them differently -- which is the whole reason this module exists.

def _name_of(obj):
    """The relation a destination expression names, as text, or None.

    A destination reaches here as a DbRef, a dotted name, or a bare string, depending on the
    verb, so this asks each shape in turn rather than assuming one.
    """
    if obj is None:
        return None
    if isinstance(obj, str):
        return obj.split(".", 1)[-1] if obj.startswith("db.") else obj
    for attr in ("table", "name", "collection"):
        value = getattr(obj, attr, None)
        if isinstance(value, str) and value:
            return value
    parts = getattr(obj, "parts", None)
    if parts:
        flat = [str(p) for p in parts]
        return flat[-1] if len(flat) > 1 else flat[0]
    return None


def _datasource_of(obj):
    """Which connection the write goes to. Every call site in the tree says `db` today, and this
    reads it rather than hardcoding it, so the day a second one exists the IR already carries it."""
    if isinstance(obj, str) and "." in obj:
        return obj.split(".", 1)[0]
    for attr in ("source", "qualifier", "connection"):
        value = getattr(obj, attr, None)
        if isinstance(value, str) and value:
            return value
    parts = getattr(obj, "parts", None)
    if parts and len(parts) > 1:
        return str(parts[0])
    return "db"


def _field_names(fields):
    """Field NAMES only. A written value never enters the IR, for the same reason the audit trail
    refuses to hold one: an internal representation that carries the data is a second unguarded
    copy of it, and this one would be readable by every later phase."""
    out = []
    for f in fields or []:
        n = getattr(f, "name", None)
        if n:
            out.append(str(n))
        elif isinstance(f, str):
            out.append(f)
    return tuple(out)


def _handler_kinds(handlers):
    return tuple(type(h).__name__ for h in (handlers or []))


def _failure_contract(handlers):
    """A write carrying its own `on.failure` makes failure observable AT THAT WRITE.

    Inside a loop this is the roadmap's per-row-failure-observable case and it removes every plan
    that cannot say which row failed. Statically visible, because the handler is a field on the
    node.
    """
    kinds = _handler_kinds(handlers)
    if any("OnFailure" in k or "OnError" in k for k in kinds):
        return FailureContract.PER_ROW_OBSERVABLE
    if kinds:
        return FailureContract.WHOLE_OPERATION
    return FailureContract.WHOLE_OPERATION


def _compliance(field_names, classifier=None, sector_active=False, table=None):
    """The compliance contract, from the compiler's existing model.

    Reads the same two static inputs the runtime gate reads. The third gate condition -- a table
    already known to hold sensitive data -- is per-process in-memory state and is deliberately
    NOT consulted: a plan cannot rest on something two processes can disagree about. Where the
    static answer is not YES, this says UNKNOWN rather than NO, and UNKNOWN forbids.
    """
    tagged = ()
    if classifier is not None and field_names:
        try:
            encrypted = set(classifier.fields_with("encrypted", table)
                            if table else classifier.fields_with("encrypted"))
        except TypeError:
            encrypted = set(classifier.fields_with("encrypted"))
        tagged = tuple(n for n in field_names if n in encrypted)
    regulated = Regulated.YES if (tagged or sector_active) else Regulated.UNKNOWN
    if tagged:
        why = "writes tagged field(s): " + ", ".join(tagged)
    elif sector_active:
        why = "a compliance sector is active"
    else:
        why = ("no tagged field and no active sector are visible here; the third runtime "
               "condition is per-process state a plan may not rest on, so this is UNKNOWN "
               "rather than NO")
    return ComplianceContract(regulated=regulated, audit_required=regulated,
                              tagged_fields=tagged, sector_active=bool(sector_active), why=why)


def _base(node, verb, txn_scope):
    return {
        "verb": verb,
        "node_type": type(node).__name__,
        "line": getattr(node, "line", 0) or 0,
        "transaction": txn_scope,
    }


def lower_write(node, classifier=None, sector_active=False,
                transaction=TransactionScope.PER_ROW):
    """Lower ONE write node into a WriteIntent. Returns None for a node that is not a write.

    `transaction` is passed in rather than discovered, because transaction membership is a
    property of where the node SITS, which only the walker above it knows.
    """
    kind = type(node).__name__

    if kind == "SaveBlock":
        names = _field_names(getattr(node, "fields", None))
        dedupe = tuple(str(d) for d in (getattr(node, "dedupe_fields", None) or []))
        alias = getattr(node, "alias", None)
        return WriteIntent(
            operation=Operation.UPSERT if dedupe else Operation.INSERT,
            target=WriteTarget(datasource=_datasource_of(getattr(node, "target", None)),
                               relation=_name_of(getattr(node, "target", None)),
                               identity=dedupe,
                               resolved=_name_of(getattr(node, "target", None)) is not None),
            row_source=RowSource.SINGLE_LITERAL,
            field_names=names,
            predicate_fields=dedupe,
            cardinality=1,
            # AN ALIAS IS THE ONE STRUCTURALLY VISIBLE CONSUMPTION. Without one the id is still
            # produced, and whether the program reads it needs Phase 2, so it stays UNKNOWN.
            result=ResultNeed.GENERATED_ID if alias else ResultNeed.UNKNOWN,
            result_bound_to=alias,
            ordering=OrderingContract.IRRELEVANT,
            failure=_failure_contract(getattr(node, "handlers", None)),
            compliance=_compliance(names, classifier, sector_active,
                                   _name_of(getattr(node, "target", None))),
            **_base(node, "save unless-exists" if dedupe else "save", transaction))

    if kind == "SaveOrUpdateBlock":
        names = _field_names(getattr(node, "fields", None))
        match = getattr(node, "match", None)
        keys = tuple(str(getattr(m, "field", m)) for m in
                     (match if isinstance(match, list) else [match] if match else []))
        return WriteIntent(
            operation=Operation.UPSERT,
            target=WriteTarget(datasource=_datasource_of(getattr(node, "source", None)),
                               relation=_name_of(getattr(node, "source", None)),
                               identity=keys,
                               resolved=_name_of(getattr(node, "source", None)) is not None),
            row_source=RowSource.SINGLE_LITERAL,
            field_names=names,
            predicate_fields=keys,
            cardinality=1,
            result=ResultNeed.UNKNOWN,
            ordering=OrderingContract.IRRELEVANT,
            failure=_failure_contract(getattr(node, "handlers", None)),
            compliance=_compliance(names, classifier, sector_active,
                                   _name_of(getattr(node, "source", None))),
            **_base(node, "save or update", transaction))

    if kind == "SaveAllBlock":
        # THE ONE VERB WHERE THE AUTHOR HAS ALREADY STATED THE BATCH. `target` is the
        # destination and `source` is the collection -- the one node that uses both names, and
        # in opposite senses to the verbs around it.
        return WriteIntent(
            operation=Operation.INSERT,
            target=WriteTarget(datasource=_datasource_of(getattr(node, "target", None)),
                               relation=_name_of(getattr(node, "target", None)),
                               resolved=_name_of(getattr(node, "target", None)) is not None),
            row_source=RowSource.COLLECTION,
            field_names=(),          # the rows are a runtime collection; names are not static
            result=ResultNeed.UNKNOWN,
            ordering=OrderingContract.INPUT_ORDER_OBSERVABLE,
            failure=_failure_contract(getattr(node, "handlers", None)),
            compliance=_compliance((), classifier, sector_active,
                                   _name_of(getattr(node, "target", None))),
            **_base(node, "save all", transaction))

    if kind == "UpdateBlock":
        body = getattr(node, "body", None) or []
        names = _field_names([b for b in body if getattr(b, "name", None)])
        match_names = tuple(str(getattr(b, "field", "")) for b in body
                            if type(b).__name__ == "MatchClause")
        return WriteIntent(
            operation=Operation.UPDATE,
            target=WriteTarget(datasource=_datasource_of(getattr(node, "source", None)),
                               relation=_name_of(getattr(node, "source", None)),
                               resolved=_name_of(getattr(node, "source", None)) is not None),
            row_source=RowSource.PREDICATE_ONLY,
            field_names=names,
            predicate_fields=tuple(n for n in match_names if n),
            result=ResultNeed.AFFECTED_COUNT,
            ordering=OrderingContract.IRRELEVANT,
            failure=_failure_contract(getattr(node, "handlers", None)),
            compliance=_compliance(names, classifier, sector_active,
                                   _name_of(getattr(node, "source", None))),
            **_base(node, "update", transaction))

    if kind == "RemoveBlock":
        cond = getattr(node, "condition", None) or getattr(node, "match", None)
        conds = cond if isinstance(cond, list) else ([cond] if cond else [])
        keys = tuple(str(getattr(c, "field", "")).split(".")[-1] for c in conds)
        return WriteIntent(
            operation=Operation.DELETE,
            target=WriteTarget(datasource=_datasource_of(getattr(node, "source", None)),
                               relation=_name_of(getattr(node, "source", None)),
                               resolved=_name_of(getattr(node, "source", None)) is not None),
            row_source=RowSource.PREDICATE_ONLY,
            predicate_fields=tuple(k for k in keys if k),
            result=ResultNeed.AFFECTED_COUNT,
            ordering=OrderingContract.IRRELEVANT,
            failure=_failure_contract(getattr(node, "handlers", None)),
            compliance=_compliance((), classifier, sector_active,
                                   _name_of(getattr(node, "source", None))),
            **_base(node, "remove", transaction))

    if kind == "RemoveAllBlock":
        return WriteIntent(
            operation=Operation.DELETE,
            target=WriteTarget(datasource=_datasource_of(getattr(node, "source", None)),
                               relation=_name_of(getattr(node, "source", None)),
                               resolved=_name_of(getattr(node, "source", None)) is not None),
            row_source=RowSource.WHOLE_TABLE,
            result=ResultNeed.AFFECTED_COUNT,
            ordering=OrderingContract.IRRELEVANT,
            failure=_failure_contract(getattr(node, "handlers", None)),
            compliance=_compliance((), classifier, sector_active,
                                   _name_of(getattr(node, "source", None))),
            **_base(node, "remove all", transaction))

    if kind == "ModifyBlock":
        body = getattr(node, "body", None) or []
        names = _field_names(body)
        collection = getattr(node, "collection", None)
        relation = _name_of(collection)
        return WriteIntent(
            operation=Operation.UPDATE,
            target=WriteTarget(datasource=_datasource_of(collection), relation=relation,
                               resolved=relation is not None),
            # ROWS THE RUNTIME READ BACK, not rows written out in the block. `modify` reads,
            # filters in the interpreter, and writes each survivor by its own identity, so its
            # row source is a query result and its cardinality is not known until it runs.
            row_source=RowSource.QUERY_RESULT,
            field_names=names,
            result=ResultNeed.AFFECTED_COUNT,
            ordering=OrderingContract.EXECUTION_ORDER_OBSERVABLE,
            failure=FailureContract.WHOLE_OPERATION,     # modify carries no handlers of its own
            compliance=_compliance(names, classifier, sector_active, relation),
            **_base(node, "modify", transaction))

    if kind == "FlowStmt":
        # A FLOW HOP LANDING IN A TABLE IS A WRITE, and it is the one that reached no audit at
        # all until recently. What it writes is decided by the MAP, at runtime, so the relation
        # and the fields are not statically visible from this node.
        return WriteIntent(
            operation=Operation.INSERT,
            target=WriteTarget(datasource="db", resolved=False),
            row_source=RowSource.UNKNOWN,
            result=ResultNeed.UNKNOWN,
            ordering=OrderingContract.EXECUTION_ORDER_OBSERVABLE,
            failure=_failure_contract(getattr(node, "handlers", None)),
            compliance=ComplianceContract(
                why="the destination is decided by the map at runtime, so nothing here can say "
                    "whether it is regulated"),
            normalized=False,
            not_normalized_reason="a flow hop's destination and fields are chosen by the map at "
                                  "runtime, so neither is visible at this node",
            **_base(node, "flow hop", transaction))

    if kind == "SqlBlock":
        # RAW SQL IS TEXT THIS COMPILER DID NOT WRITE. Reading an operation out of it would mean
        # parsing SQL, and a planner acting on a guess about somebody else's statement is exactly
        # the failure the not-normalized answer exists to prevent.
        return WriteIntent(
            operation=Operation.OPAQUE,
            target=WriteTarget(resolved=False),
            row_source=RowSource.UNKNOWN,
            result=ResultNeed.UNKNOWN,
            ordering=OrderingContract.UNKNOWN,
            failure=FailureContract.UNKNOWN,
            compliance=ComplianceContract(
                why="raw sql is opaque to this compiler; whether it touches regulated data "
                    "cannot be read from the text"),
            normalized=False,
            not_normalized_reason="raw sql is opaque text; its operation and target would have "
                                  "to be guessed by parsing somebody else's statement",
            **_base(node, "sql", transaction))

    return None


# ── walking a program ───────────────────────────────────────────────────────────────────

_WRITE_NODES = ("SaveBlock", "SaveOrUpdateBlock", "SaveAllBlock", "UpdateBlock",
                "RemoveBlock", "RemoveAllBlock", "ModifyBlock", "FlowStmt", "SqlBlock")


def lower_program(program, classifier=None):
    """Every write in a program, in source order, each as a WriteIntent.

    Transaction membership comes from the walk rather than the node, because it is a property of
    where a write SITS. Sector activation is picked up the same way: a `sector:` line makes every
    write after it regulated, and the walk is what knows the order.
    """
    out = []
    state = {"sector": False}

    def walk(node, txn):
        if node is None:
            return
        if isinstance(node, (list, tuple)):
            for n in node:
                walk(n, txn)
            return
        kind = type(node).__name__
        if kind == "SectorDecl" or (kind.endswith("Sector") and getattr(node, "name", None)):
            state["sector"] = True
        if kind == "TransactionBlock":
            walk(getattr(node, "body", None), TransactionScope.ENCLOSING_BLOCK)
            return
        if kind in _WRITE_NODES:
            intent = lower_write(node, classifier, state["sector"], txn)
            if intent is not None:
                out.append(intent)
            # a write node's own children hold no further writes worth a second visit
            return
        for attr in ("body", "statements", "when_clauses", "otherwise", "handlers",
                     "listeners", "body_stmts"):
            walk(getattr(node, attr, None), txn)

    walk(getattr(program, "statements", None) or getattr(program, "body", None),
         TransactionScope.PER_ROW)
    return out


# ── the compile-time classifier ─────────────────────────────────────────────────────────

# The tags that make a field regulated. Read from the shape declaration, which is where the
# author wrote them, rather than from the runtime registry the audit gate consults -- because a
# plan has to know before it is chosen, not discover during execution.
REGULATED_TAGS = ("phi", "pci", "pii", "financial", "encrypted")


class StaticClassification:
    """Which field names a program declares regulated, read from its shape declarations.

    Phase 0 recorded the absence of this as WP-B2. It is deliberately the SMALLEST thing that
    lets compliance participate: a set of names, resolved per table where the shape says so and
    by bare name otherwise, which is the same two-step the runtime resolver already uses.

    WHAT IT DOES NOT DO, on purpose: it does not consult the runtime `_tagged_tables` registry.
    That is per-process in-memory state, so two processes writing the same table can disagree
    about whether a write is regulated, and a plan may not rest on it. A write this cannot show
    to be regulated is UNKNOWN, never NO.
    """

    def __init__(self, tagged_names=(), by_table=None):
        self._names = set(tagged_names)
        self._by_table = dict(by_table or {})

    def fields_with(self, kind, table=None):
        if kind != "encrypted":
            return set()
        if table and table in self._by_table:
            return set(self._by_table[table]) | set(self._names)
        return set(self._names)

    def __bool__(self):
        return bool(self._names or self._by_table)


def classify_program(program):
    """Build a StaticClassification from a program's shape declarations."""
    names = set()
    for node in _walk_all(program):
        if type(node).__name__ != "ShapeDecl":
            continue
        for f in getattr(node, "fields", None) or []:
            fname = getattr(f, "name", None)
            if not fname:
                continue
            for mod in getattr(f, "modifiers", None) or []:
                if getattr(mod, "modifier_type", None) == "tag" and \
                        str(getattr(mod, "value", "")).lower() in REGULATED_TAGS:
                    names.add(str(fname))
    return StaticClassification(names)


def program_activates_sector(program):
    """Does the program activate a compliance sector? A sector makes every data write audited."""
    for node in _walk_all(program):
        kind = type(node).__name__
        if kind in ("SectorDecl", "SectorDeclaration", "SectorStmt"):
            return True
        if kind == "Program":
            continue
    return False


def _walk_all(node, _seen=None):
    """Every node in a program, once."""
    if _seen is None:
        _seen = set()
    if node is None:
        return
    if isinstance(node, (list, tuple)):
        for n in node:
            yield from _walk_all(n, _seen)
        return
    if not hasattr(node, "__dict__"):
        return
    if id(node) in _seen:
        return
    _seen.add(id(node))
    yield node
    for value in list(vars(node).values()):
        if isinstance(value, (list, tuple)) or hasattr(value, "__dict__"):
            yield from _walk_all(value, _seen)
