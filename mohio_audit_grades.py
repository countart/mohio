# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Framework-driven audit grade requirements.

The audit guarantee is NOT keyed on whether a sector is paid/licensed. It is keyed on which
COMPLIANCE FRAMEWORKS a sector profile activates. Frameworks are modular: a profile declares
`compliance: [hipaa, pci-dss, ...]`, and each framework independently demands a minimum audit
grade. The compiler must enforce the HIGHEST grade any activated framework requires.

This is what makes compliance configurable for enterprise: a client composes the frameworks they
are subject to, and the required audit posture falls out of that composition automatically. A
community-tier profile that activates HIPAA gets HIPAA's audit grade; a licensed profile that
activates nothing audit-bearing gets none. Tier and audit grade are independent.

## The grades (ascending strength)

    NONE              no audit-persistence requirement
    DURABLE           records must survive restart/sleep (not ephemeral SQLite on a sleeping box)
    APPEND_ONLY       DURABLE + records cannot be updated or deleted in place (tamper-evident)
    WORM              APPEND_ONLY + the STORAGE PROVIDER itself refuses rewrite and deletion
                      until retention ends. Named for what the storage does, not for a promise
                      about outcomes: `tamper_proof` claimed prevention the compiler cannot
                      deliver, since the guarantee belongs to the provider, not to us.

A sink satisfies a requirement if the sink's grade is >= the required grade.

## How enforcement uses this

- At check/deploy: if the highest required grade has no sink that meets it, REFUSE (structural
  absence, caught before the app serves traffic -- halts nothing in production).
- At runtime, a transient write failure does NOT abort the operation. It is retried, mirrored to a
  redundant durable sink, and alerted. Abort is reserved for the true case where NO durable
  substrate can hold the record at all. (Recovery/redundancy behavior lives in the interpreter and
  the design-chat WAL/circuit-breaker spec, not here -- this module only answers "what grade is
  required".)

## Extending

Add a framework by adding one line to FRAMEWORK_AUDIT_GRADE. Names are matched case-insensitively
and with common separators normalized (hipaa, HIPAA, hip-aa -> "hipaa"). Unknown frameworks map to
NONE and are reported (an unknown framework must not silently imply "no requirement" without a
trace) -- see required_grade().
"""

# --- the canonical audit schema ---------------------------------------------------------
# ONE schema for every audit table, replacing the old Group-A (fraud/phi_audit_log:
# decision_name/inputs/result/confidence/model/fell_back/ts) vs Group-B (data/operation/
# <agent>_limits_log: audit_id/ts/event/agent/detail) split. Both are subsumed here so all audit
# tables share one namespace and one shape, which is what lets a single append-only grant and a
# single hash-chain design cover every audit table.
#
# A given writer fills the columns relevant to it and leaves the rest null. Creating every audit
# table with the full column list means adding the chain/binding columns later is NOT a migration
# (they are already present, nullable) -- important for an append-only/immutable log where an
# ALTER is painful and breaks the chain.
CANONICAL_AUDIT_COLUMNS = [
    # identity + chain (chain cols reserved, filled by the GATED sealed-pipeline work)
    'audit_id', 'prev_hash', 'entry_hash',
    # common event columns (was Group B)
    'ts', 'event', 'agent', 'detail',
    # decision columns (was Group A; inputs is names+classification only, never values)
    'decision_name', 'inputs', 'result', 'confidence', 'model', 'fell_back',
    # record-level binding (reserved; filled only by the detailed /mioaudit paygate tier)
    'input_binding',
    # context
    'sector', 'session_id', 'member_id',
]


def canonical_audit_columns():
    """The single column list every audit table is created with. One source of truth so the
    ensure_table calls, the writers, and the platform pre-seed cannot drift from each other."""
    return list(CANONICAL_AUDIT_COLUMNS)


# --- audit-table identification (for schema-qualification + fail-closed governance) ------
# The compiler names LOGICAL audit tables; physical placement (e.g. a Postgres `audit` schema
# with append-only grants) is the sink/platform's job -- the compiler does not hardcode Postgres
# schema syntax, because that would break SQLite / MySQL / Mongo. What the compiler DOES own is
# knowing which tables are audit tables, so:
#   - the sink can place them in the governed `audit` namespace and grant append-only, and
#   - the compiler can refuse (under an active framework) to create an audit table outside the
#     governed set (the fail-closed slot).
#
# Identification is by name convention, matching every audit writer in the interpreter:
#   - the four static names below,
#   - any `<name>_limits_log` (per-agent governance),
#   - any `*_audit_log` (data/operation/profile-custom).
STATIC_AUDIT_TABLES = frozenset({
    'fraud_audit_log', 'phi_audit_log', 'data_audit_log', 'operation_audit_log',
    # These two are written by the compiler but match neither suffix family, so they have to be
    # named explicitly. `compliance_audit` is the ai.decide record -- the primary compliance log
    # -- and `audit_incident_log` records the moment an audit sink was too weak for the required
    # grade. Both were previously invisible to is_audit_table, which meant a platform deriving
    # append-only grants from this predicate would not have covered them.
    'compliance_audit', 'audit_incident_log',
})


def is_audit_table(name):
    """True if `name` is an audit table by Mohio's naming convention. Covers the static names
    and both dynamic families (`<agent>_limits_log`, profile-custom `*_audit_log`).

    This predicate is a CONTRACT, not a convenience: the platform derives its append-only role
    grants from it. Any table the compiler writes audit records to must satisfy this, or those
    records land outside the grants and the append-only guarantee quietly does not apply to them.
    `compliance_audit` and `audit_incident_log` were exactly that case -- real audit logs whose
    names matched neither the static set nor either suffix family. test_audit_table_contract
    holds every writer against this predicate so the gap cannot reopen.
    """
    n = str(name)
    return (n in STATIC_AUDIT_TABLES
            or n.endswith('_audit_log')
            or n.endswith('_limits_log'))



# Ascending order matters: index in this tuple IS the strength.
GRADES = ("none", "durable", "append_only", "worm")


def _rank(grade: str) -> int:
    g = (grade or "none").strip().lower()
    return GRADES.index(g) if g in GRADES else 0


def stronger(a: str, b: str) -> str:
    """Return the stronger of two grades."""
    return a if _rank(a) >= _rank(b) else b


def satisfies(sink_grade: str, required_grade: str) -> bool:
    """True if a sink of `sink_grade` meets `required_grade`."""
    return _rank(sink_grade) >= _rank(required_grade)


# Mappings whose grade is a considered DEFAULT rather than a ratified legal reading. They are
# named here rather than hedged in a comment, because a comment is invisible at runtime and a
# caller relying on the grade deserves to know which readings are settled and which are not.
#
# Nothing about this module is legal advice; it is research-grade organisation. The difference
# between these entries and the rest is only that the rest are uncontroversial, not that they are
# ratified.
UNRATIFIED_MAPPINGS = frozenset({"bsa", "aml", "glba"})


def framework_grade_overrides():
    """Deployment-supplied framework grades, e.g. MOHIO_FRAMEWORK_GRADES="glba=append_only,bsa=worm".

    The table is a configurable default, not an assertion of law. A compliance officer who reads
    a requirement differently -- or a regulator who settles one -- must be able to say so without
    editing the compiler.
    """
    raw = _os.environ.get("MOHIO_FRAMEWORK_GRADES", "").strip()
    out = {}
    if not raw:
        return out
    import sys as _sys
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        # T1-SILENT-SWEEP-BATCH6-10 (2026-08-15): a malformed entry (no "=", or an
        # unrecognized grade word -- typically a typo) used to be dropped with no trace at
        # all. An operator setting this var is deliberately trying to STRENGTHEN a
        # framework's required grade; a typo that silently no-ops that attempt is the
        # opposite of what they asked for, and nothing told them. Warn (not raise --
        # this is a startup/deployment-time config value, and the unset/empty case above
        # is the common path and must stay silent).
        if "=" not in part:
            print(f"  [audit] MOHIO_FRAMEWORK_GRADES entry {part!r} has no '=' -- "
                  f"expected 'framework=grade' (e.g. 'glba=append_only'). Ignored.",
                  file=_sys.stderr)
            continue
        name, grade = part.split("=", 1)
        name, grade = _normalize(name), grade.strip().lower()
        if not name:
            print(f"  [audit] MOHIO_FRAMEWORK_GRADES entry {part!r} has an empty framework "
                  f"name. Ignored.", file=_sys.stderr)
            continue
        if grade not in GRADES:
            print(f"  [audit] MOHIO_FRAMEWORK_GRADES entry {part!r}: {grade!r} is not a "
                  f"recognized grade (expected one of {GRADES}). Ignored.", file=_sys.stderr)
            continue
        out[name] = grade
    return out


def _normalize(name: str) -> str:
    return "".join(ch for ch in str(name).strip().lower() if ch.isalnum())


# --- the mapping ------------------------------------------------------------------------
# Framework (normalized) -> minimum audit grade it requires.
# Grounded in the persistence/audit obligations these regimes impose. This is research-grade
# organization, not legal advice; the design/compliance chat owns final ratification, and a
# framework's grade can be tightened here in one line without touching enforcement.
FRAMEWORK_AUDIT_GRADE = {
    # Healthcare: HIPAA Security Rule requires audit controls and retention of access logs.
    "hipaa":     "append_only",
    "hitech":    "append_only",
    # Payments: PCI-DSS requires audit trails that cannot be altered.
    "pcidss":    "append_only",
    "pci":       "append_only",
    # Financial reporting / recordkeeping: SOX, SEC 17a-4 explicitly require WORM (non-rewritable,
    # non-erasable) storage for certain records.
    "sox":       "worm",
    "sec17a4":   "worm",
    "finra":     "worm",
    # Banking / AML: Bank Secrecy Act & anti-money-laundering require retention + immutability of
    # transaction records. Grade is a DEFAULT, not a ratified reading -- see UNRATIFIED below.
    "bsa":       "worm",
    "aml":       "worm",
    # Financial privacy: Gramm-Leach-Bliley requires durable records of access to financial data.
    # Whether append-only is required is unsettled here -- see UNRATIFIED below.
    "glba":      "durable",
    # Privacy regimes: durable records of processing/access; not inherently WORM.
    "gdpr":      "durable",
    "ccpa":      "durable",
    "cpra":      "durable",
    # Government / defense baselines that require durable, protected audit records.
    "fedramp":   "append_only",
    "cmmc":      "append_only",
    "nist80053": "append_only",
    # Trust-services / SaaS + financial common requirement: audit-logging control.
    "soc2":      "durable",
}


def required_grade(frameworks, *, return_unratified=False):
    """The highest audit grade any activated framework requires.

    frameworks: iterable of framework name strings (as declared in profile.compliance).
    Returns (grade, unknown_names): the required grade, and a list of any framework names not in
    the table (so callers can surface them -- an unrecognized framework must not silently be
    treated as "no requirement" with no trace).
    """
    grade = "none"
    unknown = []
    unratified = []
    _over = framework_grade_overrides()
    for f in (frameworks or []):
        key = _normalize(f)
        if key in _over:
            grade = stronger(grade, _over[key])      # deployment ruling wins outright
        elif key in FRAMEWORK_AUDIT_GRADE:
            grade = stronger(grade, FRAMEWORK_AUDIT_GRADE[key])
            if key in UNRATIFIED_MAPPINGS:
                unratified.append(key)
        elif key:
            unknown.append(str(f))
    if return_unratified:
        return grade, unknown, unratified
    return grade, unknown


# ── enforcement: audit relations accept rows ONLY through the chaining path ───────────
# An ordinary data write into an audit table produces a row with no prev_hash and no
# entry_hash. Verification skips such rows, so they are audit records that are invisible to
# the audit. A test can only guess at this by reading source; the storage layer can refuse it.
import os as _os
import threading as _threading

_CHAINED_WRITE = _threading.local()


class chained_write:
    """Marks the one sanctioned path that may write an audit relation."""

    def __enter__(self):
        _CHAINED_WRITE.active = True
        return self

    def __exit__(self, *exc):
        _CHAINED_WRITE.active = False
        return False


def assert_write_allowed(table):
    """Raise if an audit relation is being written outside the chaining path."""
    if not is_audit_table(table):
        return
    if getattr(_CHAINED_WRITE, 'active', False):
        return
    raise PermissionError(
        f"'{table}' is an audit relation and can only be written through the audit chaining "
        f"path. A direct write would produce a record with no chain links -- an audit row that "
        f"verification cannot see, which is worse than no row at all.")


# ── sink classification: a store never grades itself ─────────────────────────────────
# The grade of an audit sink is decided by INSPECTION, not by an attribute the sink sets. A store
# asserting its own grade is the same shape as a program asserting its own compliance: the claim
# and the thing being claimed about have the same author.
#
# Fail CLOSED. An unrecognised binding resolves to "none", never to "durable". The previous
# defaults assumed adequacy -- `getattr(sink, '_mohio_grade', 'durable')` -- which meant a sink
# nobody had classified was treated as meeting the requirement. That is the silent
# non-durability failure: writes appear to succeed, the guarantee reads as true, and nothing is
# detectable at the time.

EPHEMERAL_REASON = "in-memory store: nothing written survives the process"


def classify_sink(sink):
    """Return (grade, durable, reason) for an audit sink, by inspection.

    Grades above `durable` cannot be reached by inspection alone -- append-only and WORM are
    properties of role grants and storage configuration, not of a connection object. Those are
    asserted by a provider that verified them, and the assertion is recorded separately
    (`_mohio_grade_verified`) from anything a sink could set for itself.
    """
    if sink is None:
        return "none", False, "no audit sink is bound"

    # A provider may certify a higher grade, but only through the verified channel, and only for
    # grades that exist. Anything else is ignored rather than trusted.
    verified = getattr(sink, "_mohio_grade_verified", None)
    if verified in ("durable", "append_only", "worm"):
        # The ONLY channel by which a grade may be asserted rather than observed. It exists
        # because the properties that matter above inspection -- that a volume persists, that
        # role grants are append-only, that object storage is in compliance mode -- are not
        # visible from a connection object. Whoever sets this is stating they verified it, and
        # that is a different act from a store describing itself.
        return verified, True, f"provider-verified {verified} storage"

    conn = getattr(sink, "conn", None)
    if conn is None:
        return "none", False, "sink exposes no connection to inspect"

    # SQLite: the file path tells us whether anything survives the process.
    try:
        rows = list(conn.execute("PRAGMA database_list"))
        if rows:
            path = tuple(rows[0])[2]
            if not path:
                return "none", False, EPHEMERAL_REASON
            return "durable", True, f"sqlite file on disk: {path}"
    except Exception:
        pass

    # Networked engines persist by construction. They are `durable` and no higher: append-only
    # requires grants this cannot see, and WORM requires storage this cannot see.
    name = type(sink).__name__.lower()
    if "postgres" in name:
        return "durable", True, "postgres server"
    if "mysql" in name or "maria" in name:
        return "durable", True, "mysql/mariadb server"

    return "none", False, f"unrecognised sink type {type(sink).__name__}: cannot be graded"
