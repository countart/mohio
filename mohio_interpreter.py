# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
mohio_interpreter.py
Mohio Language — AST Interpreter
Version: 0.3.8 | May 2026 | Particular LLC

Walks the AST produced by mohio_transformer_ast.py and executes it.

Phase 1 scope (working):
  - Full execution of all four demo programs
  - Real SQLite database (test fixture, seed data)
  - Mocked AI provider (deterministic, configurable)
  - Real control flow: check/each/repeat/while
  - Real data ops: retrieve/find/save/update/remove/transaction
  - Task declarations and call verb
  - AI: ai.decide with confidence threshold, ai.audit, not confident
  - Saga/step execution (no auto-rollback — Phase 2)

Phase 1 stubs (parse + log, no execution):
  - apply / modify / copy / pull / get / grab / rerun
  - mioconnect request outbound
  - miovalidate
  - miosearch
  - sign / verify advanced forms
  - WebSocket / streaming / mioagent

Architecture:
  MohioValue    — runtime value wrapper
  Context       — scoped variable environment
  DbRuntime     — SQLite backend
  MockAiRuntime — deterministic AI backend
  AuditLog      — structured JSON-lines audit trail
  MohioInterpreter — one _exec_* method per AST node
"""

from __future__ import annotations
import os, sys, json, sqlite3, datetime, traceback, re, uuid, math, statistics
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP, InvalidOperation
from dataclasses import dataclass, field
from typing import Any, Optional
from pathlib import Path

_HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(_HERE))

from mohio_ast import *


# ══════════════════════════════════════════════════════════════
# SESSION LIFECYCLE — runtime-owned identity, rotation, expiry
# Mirrors mohio_server.py's own MOHIO_SESSION_COOKIE lookup exactly (same env var,
# same default) so a renamed cookie stays reserved under its new name too, rather
# than the interpreter silently protecting a name the server no longer uses.
# ══════════════════════════════════════════════════════════════

_SESSION_COOKIE_NAME = os.environ.get("MOHIO_SESSION_COOKIE", "mio_session")

# 2026-08-04 ruling: 30 min idle / 12 hours absolute, both ordinary, unsurprising
# web-session values, both env-overridable in seconds.
_SESSION_IDLE_TIMEOUT_SECONDS = int(os.environ.get("MOHIO_SESSION_IDLE_TIMEOUT", "1800"))
_SESSION_ABSOLUTE_TIMEOUT_SECONDS = int(os.environ.get("MOHIO_SESSION_ABSOLUTE_TIMEOUT", "43200"))

_DURATION_UNIT_SECONDS = {
    'second': 1, 'seconds': 1, 'sec': 1, 'secs': 1,
    'minute': 60, 'minutes': 60, 'min': 60, 'mins': 60,
    'hour': 3600, 'hours': 3600, 'hr': 3600, 'hrs': 3600,
    'day': 86400, 'days': 86400,
}

def _duration_rule_to_seconds(duration, unit):
    """N, 'unit' -> seconds, or None for an unrecognized unit (ignored, not fail-loud --
    the same tolerance the rest of a sector profile's parsing already has for an unknown
    line). Shared by every ExpireRule consumer so 'session_idle' and 'session_absolute'
    are read the same way a future 'token'/'cache' classification would be."""
    try:
        return int(duration) * _DURATION_UNIT_SECONDS[str(unit).strip().lower()]
    except (KeyError, TypeError, ValueError):
        return None


def _is_session_expired(created, accessed, now, idle_ceiling, absolute_ceiling):
    """Pure expiry check: (created_at, last_accessed, now, idle_ceiling, absolute_ceiling)
    -> bool. The one source of truth for what 'expired' means, shared by the lazy
    per-request check (MohioInterpreter._session_is_expired, below) and every session-store
    backend's own sweep_expired -- an in-memory sweep and a Postgres-backed sweep (which
    pushes this same comparison into a SQL WHERE clause) can never quietly disagree on the
    definition, only on how cheaply they can evaluate it."""
    if created is not None and (now - created) > absolute_ceiling:
        return True
    if accessed is not None and (now - accessed) > idle_ceiling:
        return True
    return False


# ══════════════════════════════════════════════════════════════
# STORAGE READ HELPERS — cast-on-read + Python-side ordering
# Auto-created columns are TEXT affinity, so numbers round-trip as strings.
# We restore type at the storage->interpreter boundary (cast-on-read) and order
# in Python (Path B) so behavior is numeric-correct and identical across SQL
# backends. SQL ORDER BY is no longer used for find. FOR NOW (small tables):
# when an order is requested, ordering + limit happen in memory; revisit and
# push back into per-DB SQL when tables get large.
# ══════════════════════════════════════════════════════════════

_READ_LEADING_ZERO = re.compile(r'^-?0\d+$')   # 001, 007 — zero-padded codes


def _coerce_read(v):
    """Restore a stored TEXT value to its natural type using the SAME rule as
    literal parsing (_number_or_code), so codes survive a round trip:
    '100' -> 100, '5.5' -> 5.5, leading-zero '001' stays '001', other text
    unchanged. This is the documented tradeoff: an all-digit string with no
    leading zero comes back as a number (override via a leading zero or a sector
    field-type declaration)."""
    if not isinstance(v, str):
        return v
    if _READ_LEADING_ZERO.match(v):
        return v                       # zero-padded code -> keep as string
    s = v.strip()
    if s == '':
        return v
    try:
        if '.' in s or 'e' in s or 'E' in s:
            return float(s)
        return int(s)
    except ValueError:
        return v


def _cast_row(row):
    if isinstance(row, dict):
        return {k: _coerce_read(val) for k, val in row.items()}
    return row


def _sort_rows(rows, field, direction):
    reverse = direction in ('desc', 'down')

    def key(r):
        v = r.get(field) if isinstance(r, dict) else None
        if v is None:
            return (2, 0.0, '')                # nulls grouped together
        if isinstance(v, bool):
            return (0, float(v), '')
        if isinstance(v, (int, float)):
            return (0, float(v), '')           # numbers sort numerically
        return (1, 0.0, str(v))                # text after numbers, alphabetical

    return sorted(rows, key=key, reverse=reverse)


def _finalize_rows(rows, order_by=None, order_dir='asc', limit=None, offset=0):
    """Cast every returned row, then order + offset + limit in Python.

    offset defaults to 0, so callers that don't paginate behave exactly as
    before. When an offset is requested (pagination), find_many skips its SQL
    LIMIT and lets us apply offset + limit here, after ordering, so the page is
    correct whether or not the find was ordered.
    """
    rows = [_cast_row(r) for r in rows]
    if order_by:
        rows = _sort_rows(rows, order_by, order_dir)
    if offset:
        rows = rows[int(offset):]
    if limit and (order_by or offset):
        rows = rows[:int(limit)]
    return rows


# ══════════════════════════════════════════════════════════════
# RUNTIME SIGNALS — control flow via exceptions
# ══════════════════════════════════════════════════════════════

class MohioRuntimeError(Exception):
    """
    Raised when a commercial feature is called without the commercial runtime.
    The open core interpreter raises this for miochain.* and other
    commercial-only executors.

    Carries an optional `line`: raisers that know the node set it; otherwise the CLI fills it from
    the interpreter's current-line tracker so every runtime error can point at a line.
    """
    def __init__(self, message="", line=None):
        super().__init__(message)
        self.line = line


# ── outbound-HTTP SSRF guard (S9, 2026-08-01) ─────────────────────────────────────────────
# `urllib.request.urlopen` follows 3xx redirects by DEFAULT. That turns an allowlisted public
# URL into an SSRF vector: the remote returns `302 Location: http://169.254.169.254/...` and
# urllib silently fetches the cloud-metadata endpoint. Confirmed by running (miohttp.get). The
# policy below is shared by every outbound site (miohttp, mioconnect):
#   * DEFAULT: do NOT follow redirects. A 3xx fails loud, naming the target.
#   * OPT-IN  (MOHIO_HTTP_FOLLOW_REDIRECTS=1): follow redirects, but re-vet EVERY hop -- a
#     chain public -> public -> internal is refused at the internal hop, not just the first.
import ipaddress as _ipaddress
import urllib.request as _urllib_request
import urllib.error as _urllib_error
from urllib.parse import urlsplit as _urlsplit


def _ssrf_internal_reason(url):
    """Return a human reason if `url`'s host is an internal/private/loopback/link-local target
    an outbound request must not reach, else None. Checks IP literals and obvious internal
    names. NOTE: a public hostname that DNS-resolves to a private IP (DNS rebinding) is NOT
    caught here -- tracked as a residual; the common redirect-to-169.254.169.254 vector is."""
    try:
        host = (_urlsplit(url).hostname or '').strip('[]')
    except Exception:
        return "unparseable URL"
    if not host:
        return "no host in URL"
    h = host.lower()
    if h == 'localhost' or h.endswith('.localhost') or h.endswith('.internal') or h.endswith('.local'):
        return f"internal hostname '{host}'"
    if h in ('metadata', 'metadata.google.internal'):
        return f"cloud-metadata host '{host}'"
    try:
        ip = _ipaddress.ip_address(host)
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            return f"private/loopback/link-local address {host}"
    except ValueError:
        pass  # a public hostname -- allowed (see DNS-rebinding note above)
    return None


class _VettedRedirect(_urllib_request.HTTPRedirectHandler):
    """Follow a redirect ONLY when its target is not internal; refuse an internal hop loudly.
    urllib calls redirect_request for EVERY hop, so this vets the whole chain, not just hop 1."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        reason = _ssrf_internal_reason(newurl)
        if reason:
            raise MohioRuntimeError(
                f"refused to follow a {code} redirect to '{newurl}' -- it targets an internal "
                f"address ({reason}). This is the SSRF vector redirect-following exists to stop.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _NoFollowRedirect(_urllib_request.HTTPRedirectHandler):
    """redirect_request -> None makes urllib raise the 3xx as an HTTPError rather than chase it,
    so `_http_open` can turn it into a loud refusal that names the redirect target."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_HTTP_OPENER_INSTALLED = False

def _ensure_http_opener():
    """Install Mohio's redirect policy as the process-default opener, ONCE. We route outbound
    requests through `urllib.request.urlopen` (so tests that mock it still intercept, and other
    urllib users share the policy); the installed default opener is what urlopen delegates to
    when it is NOT mocked. The env is a static deployment setting, so install-once is correct."""
    global _HTTP_OPENER_INSTALLED
    if _HTTP_OPENER_INSTALLED:
        return
    follow = os.environ.get('MOHIO_HTTP_FOLLOW_REDIRECTS') == '1'
    handler = _VettedRedirect() if follow else _NoFollowRedirect()
    _urllib_request.install_opener(_urllib_request.build_opener(handler))
    _HTTP_OPENER_INSTALLED = True

def _http_open(req, timeout, method, url):
    """Open `req` with Mohio's SSRF-aware redirect policy. Default: refuse to follow 3xx
    (fail loud, naming the target). Opt-in MOHIO_HTTP_FOLLOW_REDIRECTS=1: follow, vetting every
    hop (an internal hop raises from _VettedRedirect). Returns the response object, or raises
    MohioRuntimeError on a blocked redirect. Goes through urllib.request.urlopen so unit tests
    that patch it keep working."""
    # Vet the INITIAL target (hop 0), with the SAME classifier used for redirect hops. A raw
    # env.X-configured or {{ }}-interpolated address that points at cloud metadata / a private
    # service is an SSRF vector even with no redirect involved; without this, hop 0 was trusted
    # and only later redirects were checked. Deployments that legitimately call an internal
    # service or localhost declare MOHIO_HTTP_ALLOW_INTERNAL=1 (the redirect hops stay refused
    # regardless -- a remote-controlled redirect to internal is the classic attack).
    if os.environ.get('MOHIO_HTTP_ALLOW_INTERNAL') != '1':
        reason = _ssrf_internal_reason(url)
        if reason:
            raise MohioRuntimeError(
                f"{method} {url} targets an internal address ({reason}), which Mohio refuses to "
                f"request (SSRF guard): cloud metadata (169.254.169.254) and private/loopback "
                f"services must not be reachable from a request URL. If this deployment "
                f"legitimately calls an internal service, set MOHIO_HTTP_ALLOW_INTERNAL=1.")
    _ensure_http_opener()
    try:
        return _urllib_request.urlopen(req, timeout=timeout)
    except _urllib_error.HTTPError as e:
        # No-follow default: _NoFollowRedirect turns a 3xx into this HTTPError. Translate it into
        # a loud, actionable refusal that names the redirect target. (When following is opted in,
        # an internal hop raises MohioRuntimeError from the handler and never reaches here.)
        if 300 <= e.code < 400 and os.environ.get('MOHIO_HTTP_FOLLOW_REDIRECTS') != '1':
            loc = (e.headers.get('Location') if e.headers else None) or '<no Location header>'
            raise MohioRuntimeError(
                f"{method} {url} was answered with a {e.code} redirect to '{loc}', which Mohio "
                f"does NOT follow automatically: an allowlisted URL can be redirected to an "
                f"internal host (169.254.169.254 cloud metadata, a private service). Request the "
                f"final URL directly, or set MOHIO_HTTP_FOLLOW_REDIRECTS=1 to follow redirects "
                f"(each hop is still refused if it points at a private/internal address).")
        raise


_MOHIO_NULL_SENTINELS = {"", "none", "null", "n/a", "na", "nan", "nil", "-", "--"}


def _mohio_text(x):
    """User-facing text form of a python value. Booleans render lowercase
    (true/false — the web/JSON/JS convention, never Python's True/False),
    None renders as empty, everything else via str()."""
    if isinstance(x, bool):
        return 'true' if x else 'false'
    if x is None:
        return ''
    return str(x)


# ── include resolution ──────────────────────────────────────────────────────
# `include "lib/util.mho"` splices another file's top-level declarations into the
# current program. Resolution happens once at program load (cached by mtime), so a
# served program does not re-parse its includes on every request.
_INCLUDE_PARSER = None
_INCLUDE_CACHE = {}   # abspath -> (mtime, [statements])


def _get_include_parser():
    global _INCLUDE_PARSER
    if _INCLUDE_PARSER is None:
        from lark import Lark
        import mohio_data
        raw = mohio_data.GRAMMAR_PATH.read_text(encoding="utf-8")
        grammar = "\n".join(l for l in raw.splitlines() if not l.strip().startswith("//"))
        _INCLUDE_PARSER = Lark(grammar, parser="earley", ambiguity="resolve",
                               propagate_positions=True)
    return _INCLUDE_PARSER


def _load_include_statements(abspath):
    """Parse + transform an included .mho file into its statement list. Cached by
    modification time so repeated includes of the same file are cheap."""
    try:
        mtime = os.path.getmtime(abspath)
    except OSError:
        raise MohioRuntimeError(f"include cannot read file: {abspath}")
    cached = _INCLUDE_CACHE.get(abspath)
    if cached and cached[0] == mtime:
        return cached[1]
    from mohio_transformer_ast import transform
    with open(abspath, encoding="utf-8") as fh:
        src = fh.read()
    tree = _get_include_parser().parse(src)
    prog = transform(tree, src)
    stmts = list(getattr(prog, 'statements', []))
    _INCLUDE_CACHE[abspath] = (mtime, stmts)
    return stmts


def _mohio_is_empty(raw):
    if raw is None:
        return True
    if isinstance(raw, bool):
        return False
    if isinstance(raw, str):
        return raw.strip().lower() in _MOHIO_NULL_SENTINELS
    if isinstance(raw, (list, tuple, dict, set)):
        return len(raw) == 0
    return False


def _mohio_cast_label(node_value):
    try:
        from mohio_ast import DottedName as _DN
        if isinstance(node_value, _DN):
            return ".".join(str(p) for p in node_value.parts)
    except Exception:
        pass
    nm = getattr(node_value, 'name', None) or getattr(node_value, 'value', None)
    return str(nm) if nm is not None else None


def _mohio_coerce_number(raw, target, default, label):
    where = f"'{label}' " if label else ""
    if _mohio_is_empty(raw):
        d = 0 if default is None else default
        try:
            raw = float(d) if (isinstance(d, str) and '.' in d) else (int(d) if isinstance(d, str) else d)
        except (TypeError, ValueError):
            raw = 0
    if isinstance(raw, bool):
        raise MohioRuntimeError(f"{where}as.{target} got a true/false value, not a number.")
    if isinstance(raw, (int, float)):
        num = raw
    else:
        s = str(raw).strip()
        try:
            num = float(s) if ('.' in s or 'e' in s.lower()) else int(s)
        except (TypeError, ValueError):
            raise MohioRuntimeError(
                f'{where}expected a number, got "{raw}". '
                f"Fix the data, or guard it with 'is empty' or a 'default'.")
    if target == 'int':
        # as.int rounds to the nearest whole number (234.7656 -> 235). For explicit
        # direction use round.up / round.down; to keep the fraction use as.number.
        return int(round(num)), 'integer'
    if target == 'decimal':
        return float(num), 'decimal'
    return num, ('decimal' if isinstance(num, float) else 'number')


class _AgentLimitExceeded(Exception):
    """
    Native runtime exception for ai.agent boundary gate.
    Thrown by the interpreter's execution stack when any resource
    metric breaches a declared limits threshold.
    Claim 18: Deterministic Execution Boundaries for Non-Deterministic
    Reasoning Loops — the boundary gate is an intrinsic interpreter
    primitive, not application-level framework code.
    """
    def __init__(self, reason: str, metric: str, value, ceiling):
        self.reason  = reason
        self.metric  = metric   # 'steps' | 'tokens' | 'cost' | 'timeout'
        self.value   = value    # current metric value
        self.ceiling = ceiling  # threshold that was breached
        super().__init__(f"Agent limit exceeded: {metric} {value} > {ceiling}")


class _GiveBack(Exception):
    def __init__(self, status=None, value=None, fmt=None, download=None):
        self.status = status
        self.value  = value
        self.fmt    = fmt   # 'json'|'xml'|'text'|'html' from `give back ... as FORMAT`
        self.download = download  # saved filename from `give ... as download`

class _Halt(Exception):
    pass

class _Stop(Exception):
    def __init__(self, target=None):
        self.target = target
        super().__init__()

class _Skip(Exception):
    pass

class _Jump(Exception):
    def __init__(self, destination):
        self.destination = destination

class _Raise(Exception):
    def __init__(self, error_name=None, message=None, line=None, hint=""):
        self.error_name = error_name
        self.message    = message
        self.line       = line          # WHERE (optional; filled where the node is known)
        self.hint       = hint          # HOW to fix (falls back to _RUNTIME_HINT_TABLE)
    def __str__(self):
        return f"{self.error_name}: {self.message}" if self.error_name else str(self.message)
    def to_dict(self):
        return format_runtime_error(self)


# ── Runtime error quality standard ──────────────────────────────────────────
# Every runtime error must state WHERE it is, WHAT it is, and HOW to fix it, must
# carry an honest HTTP status, and must be logged with a trace id for traceability.
# No bare 500s with a raw Python message and no guidance.
_RUNTIME_HINT_TABLE = {
    'math_error':          "Both sides of a math operator must be numbers. Convert text with as.number / as.decimal first, or guard a missing value with 'is empty' or a default.",
    'string_error':        "Both sides of & must be text. Convert with as.string, or supply a default for a missing value.",
    'db_error':            "The database rejected the operation. Check the table and field names, and that the connection is configured.",
    'authorization_error': "The caller does not have the required role. Check 'require role' against the caller's roles.",
    'validation_error':    "The input failed validation. Check the required fields and their formats.",
    'coercion_error':      "A value could not be converted to the expected type. Fix the data, or guard it with 'is empty' or a default.",
    'timeout':             "The operation exceeded its time budget. Raise the limit, or check the upstream service.",
    'not_found':           "The requested item was not found. Check the identifier and that the record exists.",
    'division_by_zero':    "Guard the denominator before dividing.",
}
_RUNTIME_STATUS_TABLE = {
    'authorization_error': 403, 'not_found': 404, 'validation_error': 400,
    'coercion_error': 400, 'math_error': 400, 'string_error': 400,
    'division_by_zero': 400, 'timeout': 504, 'db_error': 500,
}


def _mohio_trace_id():
    import uuid
    return uuid.uuid4().hex[:12]


def format_runtime_error(err, trace_id=None):
    """Turn any runtime error into a clear, structured payload:
       code (WHAT) · message (WHAT) · hint (HOW) · line (WHERE) · honest status · trace id.
    Handles Mohio's own _Raise, MohioRuntimeError, and unexpected Python exceptions."""
    trace = trace_id or _mohio_trace_id()
    if isinstance(err, _Raise):
        code   = err.error_name or 'runtime_error'
        msg    = str(err.message if err.message is not None else err)
        hint   = err.hint or _RUNTIME_HINT_TABLE.get(code, "")
        line   = err.line
        status = _RUNTIME_STATUS_TABLE.get(code, 500)
    elif isinstance(err, MohioRuntimeError):
        msg = str(err); low = msg.lower()
        if 'not yet' in low:                      # converted stubs: declared but not built
            code, status = 'not_implemented', 501
            hint = "This construct is recognized but not executable in this build. Track it for a future release, or remove it for now to proceed."
        else:
            code, status, hint = 'runtime_error', 500, ""
        line = None
    else:
        code, status = type(err).__name__, 500
        msg  = str(err)
        hint = "An unexpected internal error occurred. This is a bug in the program or runtime; the trace id locates it in the logs."
        line = None
    return {"code": code, "message": msg, "hint": hint,
            "line": line, "status": status, "trace": trace}


def log_runtime_error(info, verbose=False):
    """Traceable structured error log. Always emitted so every runtime error is
    recorded with its trace id (the same id is returned to the caller)."""
    where = f" line {info['line']}" if info.get('line') else ""
    print(f"  [mohio.error] trace={info['trace']} code={info['code']}{where} :: {info['message']}")
    if info.get('hint'):
        print(f"    hint: {info['hint']}")
    return info


# ══════════════════════════════════════════════════════════════
# MOHIO RUNTIME VALUE
# ══════════════════════════════════════════════════════════════

class MohioValue:
    """
    Runtime value wrapper. All values in Mohio are MohioValue at runtime.
    Supports dotted attribute access: member.email -> member["email"]
    """

    def __init__(self, value: Any, mohio_type: str = "any"):
        self._value = value
        self._type  = mohio_type
        self.data_class = None   # e.g. 'pci' -> masked on display, full for use
        self._purposes = None    # [pii] collection purposes carried on the value (taint)
        self._purpose_fields = None  # which [pii] field name(s) this value came from
        self._currency = None    # currency code (USD/CAD/EUR/GBP) if this value is money; drives display
        self._pad_places = None  # dec.N.pad: render with exactly N decimal places (zero-filled)

    @property
    def value(self): return self._value

    @property
    def mohio_type(self): return self._type

    def get(self, key: str, default=None):
        """Attribute access — user.email, transaction.amount etc."""
        if isinstance(self._value, dict):
            v = self._value.get(key, default)
            return MohioValue(v) if not isinstance(v, MohioValue) else v
        if isinstance(self._value, list):
            if key in ('count', 'length', 'size'):
                return MohioValue(len(self._value), 'number')
            if key == 'first':
                return MohioValue(self._value[0] if self._value else None)
            if key == 'last':
                return MohioValue(self._value[-1] if self._value else None)
            if key == 'empty':
                return MohioValue(len(self._value) == 0, 'boolean')
        if isinstance(self._value, str):
            if key in ('length', 'size', 'count'):
                return MohioValue(len(self._value), 'number')
        if hasattr(self._value, key):
            val = getattr(self._value, key)
            return MohioValue(None) if callable(val) else MohioValue(val)
        return MohioValue(default)

    def to_python(self): return self._value

    def __repr__(self): return f"MohioValue({self._value!r}, {self._type!r})"
    def __bool__(self): return bool(self._value)
    def __eq__(self, o): return self._value == (o._value if isinstance(o, MohioValue) else o)
    def __lt__(self, o): v = o._value if isinstance(o, MohioValue) else o; return self._value < v
    def __gt__(self, o): v = o._value if isinstance(o, MohioValue) else o; return self._value > v
    def __le__(self, o): v = o._value if isinstance(o, MohioValue) else o; return self._value <= v
    def __ge__(self, o): v = o._value if isinstance(o, MohioValue) else o; return self._value >= v
    def __add__(self, o): v = o._value if isinstance(o, MohioValue) else o; return MohioValue(self._value + v)
    def __sub__(self, o): v = o._value if isinstance(o, MohioValue) else o; return MohioValue(self._value - v)
    def __mul__(self, o): v = o._value if isinstance(o, MohioValue) else o; return MohioValue(self._value * v)
    def __truediv__(self, o): v = o._value if isinstance(o, MohioValue) else o; return MohioValue(self._value / v)
    def __mod__(self, o): v = o._value if isinstance(o, MohioValue) else o; return MohioValue(self._value % v)


# ══════════════════════════════════════════════════════════════
# CONTEXT — Scoped runtime environment
# ══════════════════════════════════════════════════════════════

class Context:
    def __init__(self, parent: Optional[Context] = None):
        self._vars        = {}
        self._constants   = {}   # hold / lock — original binding
        self._locked      = set()  # names created by `lock` (permanent, no release)
        self._held        = set()  # names created by `hold` (frozen until release)
        self._typed       = {}   # name -> declared type (x as int); enforced on assignment
        self._shapes      = {}
        self._tasks       = {}
        self._connections = {}
        self._connectors  = {}   # mioconnect: name/alias -> external connector record
        self._sector      = None
        self._compliance  = []
        self._security_posture     = "standard"
        self._security_off_reason  = ""
        self._security_off_expires = ""
        self._roles       = []
        self._roles_verified = False   # roles from the client `_roles` payload are UNVERIFIED (S8.4)
        self._current_request = None
        self._parent      = parent
        self._audit_log   = []

    def child(self):
        ctx = Context(parent=self)
        return ctx

    def set(self, name, value, immutable=False):
        v = value if isinstance(value, MohioValue) else MohioValue(value)
        if immutable:
            self._constants[name] = v
        else:
            self._vars[name] = v

    def set_persistent(self, name, value):
        """Session-mode assignment.

        Updates the variable where it already lives, so reassigning a request
        field (e.g. `command`) inside a when/otherwise branch is visible to later
        reads in the SAME request. New variables are created on the per-session
        context, not the globally-shared base context (which would leak state
        across sessions). The session boundary is marked by `_session_root`
        (set in run_with_session); without it, falls back to the topmost root.
        """
        v = value if isinstance(value, MohioValue) else MohioValue(value)
        # Locate the per-session boundary; never write past it into shared base.
        session_root = None
        walk = self
        while walk is not None:
            if getattr(walk, '_session_root', False):
                session_root = walk
                break
            walk = walk._parent
        if session_root is None:
            session_root = self
            while session_root._parent is not None:
                session_root = session_root._parent
        # Update the nearest existing binding within the session scope.
        ctx = self
        while ctx is not None:
            if name in ctx._vars:
                ctx._vars[name] = v
                return
            if ctx is session_root:
                break
            ctx = ctx._parent
        # No existing binding in session scope: create on the per-session root.
        session_root._vars[name] = v

    def get(self, name) -> MohioValue:
        if name in self._vars:      return self._vars[name]
        if name in self._constants: return self._constants[name]
        if self._parent:            return self._parent.get(name)
        return MohioValue(None)

    def lock_name(self, name):
        """Mark a name as locked (created by `lock`): permanent, cannot change."""
        self._locked.add(name)

    def is_locked(self, name) -> bool:
        """True if `name` is a permanently-locked constant up the scope chain."""
        ctx = self
        while ctx is not None:
            if name in getattr(ctx, '_locked', ()):
                return True
            ctx = ctx._parent
        return False

    def hold_name(self, name):
        """Mark a name as held (created by `hold`): frozen until released."""
        self._held.add(name)

    def is_held(self, name) -> bool:
        """True if `name` is a held (frozen) value up the scope chain."""
        ctx = self
        while ctx is not None:
            if name in getattr(ctx, '_held', ()):
                return True
            ctx = ctx._parent
        return False

    def unhold_name(self, name) -> bool:
        """Release a hold wherever it lives in the chain. True if one was cleared."""
        ctx = self
        while ctx is not None:
            if name in getattr(ctx, '_held', ()):
                ctx._held.discard(name)
                return True
            ctx = ctx._parent
        return False

    def declare_type(self, name, type_name):
        """Put a type contract on a name (`x as int`): assignments must satisfy it."""
        self._typed[name] = str(type_name).lower()

    def typed_of(self, name):
        """The declared type of `name` up the scope chain, or None if it is bare."""
        ctx = self
        while ctx is not None:
            t = getattr(ctx, '_typed', {})
            if name in t:
                return t[name]
            ctx = ctx._parent
        return None

    def untype_name(self, name) -> bool:
        """Drop a name's type contract wherever it lives (used by `release`). True if cleared."""
        ctx = self
        while ctx is not None:
            if name in getattr(ctx, '_typed', {}):
                del ctx._typed[name]
                return True
            ctx = ctx._parent
        return False

    def delete_var(self, name) -> bool:
        """Remove a variable entirely wherever it lives in the chain -- value, hold/lock flag, and
        type contract all gone (used by `forget`). True if a binding was removed."""
        found = False
        ctx = self
        while ctx is not None:
            if name in ctx._vars:
                del ctx._vars[name]
                found = True
            getattr(ctx, '_held', set()).discard(name)
            getattr(ctx, '_locked', set()).discard(name)
            if name in getattr(ctx, '_typed', {}):
                del ctx._typed[name]
            if name in getattr(ctx, '_constants', {}):
                del ctx._constants[name]
            ctx = ctx._parent
        return found

    def exists(self, name) -> bool:
        """True if a variable/constant by this name is defined anywhere in the
        scope chain. Used to tell an unknown {{ var }} (typo) apart from a
        defined-but-null one, so output interpolation can fail loud on the
        former instead of silently rendering 'None'."""
        if name in self._vars or name in self._constants: return True
        if self._parent:                                  return self._parent.exists(name)
        return False

    def get_dotted(self, parts) -> MohioValue:
        """
        Resolve a dotted name chain with collection accessor support.

        Standard:    member.name, room.description
        Collection:  scene.items.first, scene.items.last
                     scene.items.count
                     scene.items.position.1
                     scene.items.position.2.name
                     scene.items.first.name
        """
        root = self.get(parts[0])

        i = 1
        while i < len(parts):
            p = str(parts[i])

            # Collection accessors — intercept before generic .get(p)
            root_py = root.to_python() if isinstance(root, MohioValue) else root

            # page.* metadata — stored separately in ctx as __page__varname
            # Intercept: varname.page.KEY → ctx.__page__varname[KEY]
            if p == 'page' and i + 1 < len(parts):
                # Find the root variable name by walking back through ctx
                # The page meta is stored as __page__<name>
                # We need to find which variable this list belongs to
                page_key_candidates = [
                    f"__page__{parts[0]}",  # most common: direct variable
                ]
                for pk in page_key_candidates:
                    page_mv = self.get(pk) if hasattr(self, 'get') else None
                    if page_mv is None:
                        break
                    page_py = page_mv.to_python() if isinstance(page_mv, MohioValue) else {}
                    if isinstance(page_py, dict):
                        sub_key = str(parts[i + 1])
                        val = page_py.get(sub_key)
                        return MohioValue(val, 'text' if val is not None else 'null')
                # If not found, fall through to normal processing

            if isinstance(root_py, list):
                if p == 'first':
                    root = MohioValue(root_py[0] if root_py else None,
                                     'shape' if root_py else 'null')
                    i += 1; continue
                if p == 'last':
                    root = MohioValue(root_py[-1] if root_py else None,
                                     'shape' if root_py else 'null')
                    i += 1; continue
                if p == 'count':
                    root = MohioValue(len(root_py), 'number')
                    i += 1; continue
                if p in ('position', 'pos') and i + 1 < len(parts):
                    # position.N / pos.N — 1-based always
                    try:
                        idx_n = int(str(parts[i + 1])) - 1  # convert 1-based to 0-based
                        root = MohioValue(root_py[idx_n] if 0 <= idx_n < len(root_py) else None,
                                         'shape' if root_py else 'null')
                        i += 2; continue
                    except (ValueError, IndexError):
                        root = MohioValue(None, 'null')
                        i += 2; continue

            # Standard field access
            if isinstance(root, MohioValue):
                root = root.get(p)
            else:
                root = MohioValue(None, 'null')
            i += 1

        return root if isinstance(root, MohioValue) else MohioValue(root, 'text')

    def set_shape(self, name, decl):   self._shapes[name] = decl
    def set_task(self, name, decl):    self._tasks[name] = decl
    def set_connection(self, name, c): self._connections[name] = c

    def get_shape(self, name):
        if name in self._shapes: return self._shapes[name]
        return self._parent.get_shape(name) if self._parent else None

    def get_task(self, name):
        if name in self._tasks: return self._tasks[name]
        return self._parent.get_task(name) if self._parent else None

    def get_connection(self, name='db'):
        if name in self._connections: return self._connections[name]
        return self._parent.get_connection(name) if self._parent else None

    def get_env(self, key):
        return MohioValue(os.environ.get(key), 'text')

    def has_role(self, role):
        if role in self._roles: return True
        return self._parent.has_role(role) if self._parent else False

    def has_any_roles(self):
        if self._roles: return True
        return self._parent.has_any_roles() if self._parent else False

    def roles_verified(self):
        if self._roles_verified: return True
        return self._parent.roles_verified() if self._parent else False

    def set_roles(self, roles, verified=False):
        self._roles = roles
        self._roles_verified = bool(verified)

    def add_audit(self, entry):
        entry['_ts'] = datetime.datetime.utcnow().isoformat()
        self._audit_log.append(entry)
        if self._parent: self._parent.add_audit(entry)


# ══════════════════════════════════════════════════════════════
# DATABASE RUNTIME — SQLite for Phase 1
# ══════════════════════════════════════════════════════════════

def _spec_to_sql(spec, ph, q):
    """Translate a condition spec into (where_sql, params).
    spec = list of (kind, [(field, value), ...]); kinds: 'and' | 'or' | 'not'.
    Top-level groups are ANDed. ph = placeholder ('?'/'%s'); q = identifier quote."""
    parts, params = [], []
    for kind, pairs in spec:
        if not pairs:
            continue
        if kind == 'and':
            for f, v in pairs:
                parts.append(f'{q}{f}{q} = {ph}'); params.append(v)
        elif kind == 'or':
            sub = ' OR '.join(f'{q}{f}{q} = {ph}' for f, _ in pairs)
            parts.append(f'({sub})'); params.extend(v for _, v in pairs)
        elif kind == 'not':
            sub = ' AND '.join(f'{q}{f}{q} <> {ph}' for f, _ in pairs)
            parts.append(f'({sub})'); params.extend(v for _, v in pairs)
    return (' AND '.join(parts) if parts else '1=1'), params


def _mongo_pair(f, v):
    """One filter pair; Mohio's string 'id' becomes Mongo's ObjectId '_id'."""
    if f == 'id':
        try:
            from bson import ObjectId
            return {'_id': ObjectId(v)}
        except Exception:
            return {'_id': v}
    return {f: v}


def _mongo_id_filter(d):
    """Translate 'id' -> '_id' (ObjectId) in a plain Mongo filter dict."""
    if not isinstance(d, dict) or 'id' not in d:
        return d or {}
    out = {k: v for k, v in d.items() if k != 'id'}
    out.update(_mongo_pair('id', d['id']))
    return out


def _spec_to_mongo(spec):
    ands = []
    for kind, pairs in spec:
        if not pairs:
            continue
        if kind == 'and':
            for f, v in pairs: ands.append(_mongo_pair(f, v))
        elif kind == 'or':
            ands.append({'$or': [_mongo_pair(f, v) for f, v in pairs]})
        elif kind == 'not':
            for f, v in pairs:
                pr = _mongo_pair(f, v); k2, v2 = next(iter(pr.items()))
                ands.append({k2: {'$ne': v2}})
    return {'$and': ands} if ands else {}


class DbRuntime:
    def __init__(self, db_path=':memory:'):
        # check_same_thread=False: the connection is created once and cached, but the
        # ASGI server runs sync request handlers in a worker threadpool, so a later
        # request reuses the connection from a different thread. Without this, the
        # second request to any data-driven app dies with "SQLite objects created in
        # a thread can only be used in that same thread." (Production uses
        # psycopg2/pymysql, separate code paths, unaffected.)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._in_transaction = False

    def ensure_table(self, table, columns, id_value=None):
        # Universal record id: every table gets a primary-key "id". When the app or a
        # seed supplies its own id, that value wins. An auto-increment INTEGER key rejects
        # a string id like "M001" with a datatype mismatch, so if a non-integer id is being
        # stored the id column is TEXT; integer or absent ids keep auto-increment.
        field_cols = [c for c in columns if c != 'id']
        id_is_text = False
        if isinstance(id_value, str):
            _s = id_value.strip()
            id_is_text = not (_s.isdigit() or (_s[:1] == '-' and _s[1:].isdigit()))
        parts = ['"id" TEXT PRIMARY KEY'] if id_is_text else ['"id" INTEGER PRIMARY KEY AUTOINCREMENT']
        parts += [f'"{c}" TEXT' for c in field_cols]
        # 3a: the program's `save ... unless a, b exists` declares this table's identity, so a
        # clean-database deploy CREATES the real composite constraint -- no manual migration.
        # Only columns this table actually has are constrained. NOTE: this applies at CREATE
        # time; a table that already exists without the constraint is not retrofitted here
        # (that is the held-back verify-at-connect work).
        _uniq = [c for c in (getattr(self, '_declared_unique', {}) or {}).get(table, [])
                 if c in field_cols]
        if len(_uniq) > 1:
            parts.append('UNIQUE(' + ', '.join(f'"{c}"' for c in _uniq) + ')')
        self.conn.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ({", ".join(parts)})')
        # Reconcile columns: widen an existing narrow table so a grown schema (e.g.
        # new seed/template columns) is applied instead of silently ignored by
        # CREATE TABLE IF NOT EXISTS.
        existing = {r[1] for r in self.conn.execute(f'PRAGMA table_info("{table}")')}
        for c in field_cols:
            if c not in existing:
                self.conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{c}" TEXT')
        self.conn.commit()

    def retrieve_one(self, table, match_field, match_value):
        try:
            cur = self.conn.execute(
                f'SELECT * FROM "{table}" WHERE "{match_field}" = ? LIMIT 1',
                (match_value,)
            )
            row = cur.fetchone()
            return _cast_row(dict(row)) if row else None
        except sqlite3.OperationalError:
            return None

    def find_many(self, table, where=None, limit=None,
                  order_by=None, order_dir='asc', offset=0):
        try:
            clauses, params = [], []
            for f, v in (where or {}).items():
                clauses.append(f'"{f}" = ?')
                params.append(v)
            sql = f'SELECT * FROM "{table}"'
            if clauses: sql += ' WHERE ' + ' AND '.join(clauses)
            # Ordering + ordered/offset limiting happen in Python (cast-on-read
            # first). Keep the SQL LIMIT only for the simple unordered, unpaged
            # case; when paginating we fetch the matching set and slice in
            # _finalize_rows so the offset is applied after ordering.
            if limit and not order_by and not offset: sql += f' LIMIT {limit}'
            cur = self.conn.execute(sql, params)
            raw = [dict(r) for r in cur.fetchall()]
            return _finalize_rows(raw, order_by, order_dir, limit, offset)
        except sqlite3.OperationalError as e:
            # A MISSING TABLE is an operational failure, NOT an empty result. Surface it so the
            # find routes to on.failure -- "table doesn't exist" and "found zero rows" look identical
            # at the result level but must never share a branch. Other operational errors keep the
            # prior lenient [] here.
            if 'no such table' in str(e).lower():
                raise
            return []

    def count(self, table, where=None):
        """COUNT(*) honoring equality filters — used for pagination totals."""
        try:
            clauses, params = [], []
            for f, v in (where or {}).items():
                clauses.append(f'"{f}" = ?'); params.append(v)
            sql = f'SELECT COUNT(*) FROM "{table}"'
            if clauses: sql += ' WHERE ' + ' AND '.join(clauses)
            cur = self.conn.execute(sql, params)
            row = cur.fetchone()
            return int(row[0]) if row else 0
        except sqlite3.OperationalError:
            return 0

    def save(self, table, fields):
        from mohio_audit_grades import assert_write_allowed as _awa
        _awa(table)          # audit relations only via the chaining path
        self.ensure_table(table, list(fields.keys()), id_value=fields.get('id'))
        cols = ', '.join(f'"{k}"' for k in fields)
        phs  = ', '.join('?' for _ in fields)
        cur  = self.conn.execute(
            f'INSERT INTO "{table}" ({cols}) VALUES ({phs})',
            list(fields.values())
        )
        if not self._in_transaction: self.conn.commit()
        return cur.lastrowid

    def update(self, table, updates, match_field, match_value):
        try:
            set_clause = ', '.join(f'"{k}" = ?' for k in updates)
            params = list(updates.values()) + [match_value]
            cur = self.conn.execute(
                f'UPDATE "{table}" SET {set_clause} WHERE "{match_field}" = ?',
                params
            )
            if not self._in_transaction: self.conn.commit()
            return cur.rowcount
        except sqlite3.OperationalError:
            return 0

    def retrieve_one_multi(self, table, conditions):
        try:
            if not conditions:
                return None
            where = ' AND '.join(f'"{f}" = ?' for f in conditions)
            cur = self.conn.execute(
                f'SELECT * FROM "{table}" WHERE {where} LIMIT 1',
                list(conditions.values()))
            row = cur.fetchone()
            return _cast_row(dict(row)) if row else None
        except sqlite3.OperationalError:
            return None

    def retrieve_one_spec(self, table, spec):
        try:
            where, params = _spec_to_sql(spec, '?', '"')
            cur = self.conn.execute(
                f'SELECT * FROM "{table}" WHERE {where} LIMIT 1', params)
            row = cur.fetchone()
            return _cast_row(dict(row)) if row else None
        except sqlite3.OperationalError:
            return None

    def retrieve_all_spec(self, table, spec):
        """Multi-row retrieve (retrieve.all/.every/.count/.first/.last). Empty
        spec means every row. Returns a list of cast row dicts (possibly empty)."""
        try:
            if spec:
                where, params = _spec_to_sql(spec, '?', '"')
                cur = self.conn.execute(
                    f'SELECT * FROM "{table}" WHERE {where}', params)
            else:
                cur = self.conn.execute(f'SELECT * FROM "{table}"')
            return [_cast_row(dict(r)) for r in cur.fetchall()]
        except sqlite3.OperationalError:
            return []

    def update_multi(self, table, updates, conditions):
        try:
            if not conditions:
                return 0
            set_clause = ', '.join(f'"{k}" = ?' for k in updates)
            where = ' AND '.join(f'"{f}" = ?' for f in conditions)
            params = list(updates.values()) + list(conditions.values())
            cur = self.conn.execute(
                f'UPDATE "{table}" SET {set_clause} WHERE {where}', params)
            if not self._in_transaction: self.conn.commit()
            return cur.rowcount
        except sqlite3.OperationalError:
            return 0

    def remove_all(self, table):
        cur = self.conn.execute(f'DELETE FROM "{table}"')
        if not getattr(self, '_in_transaction', False): self.conn.commit()
        return getattr(cur, 'rowcount', 0)

    def remove(self, table, match_field, match_value):
        try:
            cur = self.conn.execute(
                f'DELETE FROM "{table}" WHERE "{match_field}" = ?',
                (match_value,)
            )
            if not self._in_transaction: self.conn.commit()
            return cur.rowcount
        except sqlite3.OperationalError:
            return 0

    def remove_multi(self, table, conditions):
        try:
            if not conditions:
                return 0
            where = ' AND '.join(f'"{f}" = ?' for f in conditions)
            cur = self.conn.execute(
                f'DELETE FROM "{table}" WHERE {where}', list(conditions.values()))
            if not self._in_transaction: self.conn.commit()
            return cur.rowcount
        except sqlite3.OperationalError:
            return 0

    def save_if_not_exists(self, table, fields, key_cols):
        """Insert only if no row already matches key_cols -- `save ... unless a, b exists`.

        ONE atomic statement (INSERT ... SELECT ... WHERE NOT EXISTS), so it is not the
        SELECT-then-INSERT race the interpreter-level guard used to be. Deliberately NOT
        `ON CONFLICT`: that hard-requires a matching UNIQUE constraint, and Mohio's own
        auto-created tables never get one on a non-id column, so ON CONFLICT would error on
        exactly the tables Mohio makes. This form is correct with or without a constraint.
        Returns the new row id, or None when an existing row meant nothing was inserted."""
        from mohio_audit_grades import assert_write_allowed as _awa
        _awa(table)
        self.ensure_table(table, list(fields.keys()), id_value=fields.get('id'))
        cols = ', '.join(f'"{k}"' for k in fields)
        phs  = ', '.join('?' for _ in fields)
        where = ' AND '.join(f'"{c}" = ?' for c in key_cols)
        cur = self.conn.execute(
            f'INSERT INTO "{table}" ({cols}) SELECT {phs} '
            f'WHERE NOT EXISTS (SELECT 1 FROM "{table}" WHERE {where})',
            list(fields.values()) + [fields.get(c) for c in key_cols]
        )
        if not self._in_transaction: self.conn.commit()
        return cur.lastrowid if cur.rowcount else None

    def begin_transaction(self):    self._in_transaction = True
    def commit_transaction(self):   self.conn.commit();   self._in_transaction = False
    def rollback_transaction(self): self.conn.rollback(); self._in_transaction = False
    def close(self):                self.conn.close()


# ══════════════════════════════════════════════════════════════
# POSTGRES RUNTIME
# ══════════════════════════════════════════════════════════════

class PostgresRuntime:
    """
    Postgres backend — same interface as DbRuntime.
    Used when connect declares 'as postgres' and DATABASE_URL is set.
    Requires: psycopg2-binary
    """
    def __init__(self, url):
        try:
            import psycopg2
            import psycopg2.extras
            self.conn = psycopg2.connect(url)
            self.conn.autocommit = False
            self._cursor_factory = psycopg2.extras.RealDictCursor
            self._in_transaction = False
            # Test connection
            cur = self.conn.cursor()
            cur.execute("SELECT 1")
            cur.close()
            self.conn.commit()
        except ImportError:
            raise RuntimeError(
                "PostgreSQL driver not installed.\n"
                "Add to requirements.txt: psycopg2-binary>=2.9.0\n"
                "Or run: pip install psycopg2-binary"
            )
        except Exception as e:
            raise RuntimeError(f"PostgreSQL connection failed: {e}\n"
                               f"Check DATABASE_URL environment variable.")

    def _col_defs(self, columns, pk=None, id_value=None, table=None):
        """
        Generate column definitions with a universal auto-increment id.
        Every table gets "id" SERIAL PRIMARY KEY so record.id works and rows are
        addressable, matching the SQLite backend. A user-supplied "id" reuses this
        column. Upsert targets a UNIQUE/PK column via ON CONFLICT as before.

        When the program declared this table's identity (`save ... unless a, b exists`), the
        composite UNIQUE is emitted here so a clean-database deploy creates the real
        constraint -- this is what previously required a manual ALTER (3a, ruled 2026-08-04).
        """
        field_cols = [c for c in columns if c != 'id']
        _s = id_value.strip() if isinstance(id_value, str) else None
        _id_is_text = _s is not None and not (_s.isdigit() or (_s[:1] == '-' and _s[1:].isdigit()))
        parts = ['"id" TEXT PRIMARY KEY'] if _id_is_text else ['"id" SERIAL PRIMARY KEY']
        for c in field_cols:
            parts.append(f'"{c}" TEXT')
        _uniq = [c for c in (getattr(self, '_declared_unique', {}) or {}).get(table, [])
                 if c in field_cols]
        if len(_uniq) > 1:
            parts.append('UNIQUE(' + ', '.join(f'"{c}"' for c in _uniq) + ')')
        return ', '.join(parts)

    def ensure_table(self, table, columns, id_value=None):
        cur = self.conn.cursor()
        cols = self._col_defs(columns, id_value=id_value, table=table)
        try:
            cur.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ({cols})')
            # Reconcile columns: widen an existing table so a grown schema is applied
            # instead of silently skipped. ADD COLUMN IF NOT EXISTS is idempotent.
            for c in columns:
                if c == 'id':
                    continue
                cur.execute(f'ALTER TABLE "{table}" ADD COLUMN IF NOT EXISTS "{c}" TEXT')
            self.conn.commit()
        except Exception as e:
            # This used to be a bare `except Exception: rollback()` -- it swallowed EVERY
            # schema failure without a word. A table that could not be created, or a
            # column that could not be added, produced no error here; the program carried
            # on and died later on a raw Postgres string, or worse, dropped the write.
            # A schema the runtime cannot build is not a warning. Fail loud, name the fix.
            self.conn.rollback()
            raise RuntimeError(
                f"Could not build the schema for table '{table}'.\n"
                f"  columns: {', '.join(columns)}\n"
                f"  database said: {e}\n"
                f"If the table already exists with a different shape, its schema and this "
                f"program disagree. Mohio will widen a table, but it will not redefine a "
                f"key or a type that is already there."
            ) from e
        finally:
            cur.close()

    def retrieve_one(self, table, match_field, match_value):
        try:
            cur = self.conn.cursor(cursor_factory=self._cursor_factory)
            cur.execute(
                f'SELECT * FROM "{table}" WHERE "{match_field}" = %s LIMIT 1',
                (match_value,)
            )
            row = cur.fetchone()
            cur.close()
            return _cast_row(dict(row)) if row else None
        except Exception:
            self.conn.rollback()
            return None

    def find_many(self, table, where=None, limit=None,
                  order_by=None, order_dir='asc', offset=0):
        try:
            import psycopg2.extras
            cur = self.conn.cursor(cursor_factory=self._cursor_factory)
            clauses, params = [], []
            for f, v in (where or {}).items():
                clauses.append(f'"{f}" = %s')
                params.append(v)
            sql = f'SELECT * FROM "{table}"'
            if clauses: sql += ' WHERE ' + ' AND '.join(clauses)
            # Ordering + ordered/offset limiting happen in Python (cast-on-read
            # first); skip the SQL LIMIT when paginating so the offset is applied
            # after ordering in _finalize_rows.
            if limit and not order_by and not offset: sql += f' LIMIT {limit}'
            cur.execute(sql, params)
            rows = _finalize_rows([dict(r) for r in cur.fetchall()],
                                  order_by, order_dir, limit, offset)
            cur.close()
            return rows
        except Exception:
            self.conn.rollback()
            return []

    def count(self, table, where=None):
        """COUNT(*) honoring equality filters — used for pagination totals."""
        cur = self.conn.cursor()
        clauses, params = [], []
        for f, v in (where or {}).items():
            clauses.append(f'"{f}" = %s'); params.append(v)
        sql = f'SELECT COUNT(*) FROM "{table}"'
        if clauses:
            sql += ' WHERE ' + ' AND '.join(clauses)
        try:
            cur.execute(sql, params)
            n = cur.fetchone()[0]; cur.close()
            return int(n)
        except Exception as e:
            # Missing table → 0 (parity with sqlite). Anything else fails loud
            # rather than silently reporting 0 rows.
            self.conn.rollback(); cur.close()
            if getattr(e, 'pgcode', None) == '42P01' or 'does not exist' in str(e).lower():
                return 0
            raise

    def save(self, table, fields):
        from mohio_audit_grades import assert_write_allowed as _awa
        _awa(table)          # audit relations only via the chaining path
        self.ensure_table(table, list(fields.keys()), id_value=fields.get('id'))
        cols = ', '.join(f'"{k}"' for k in fields)
        phs  = ', '.join('%s' for _ in fields)
        cur  = self.conn.cursor()
        try:
            cur.execute(
                f'INSERT INTO "{table}" ({cols}) VALUES ({phs}) RETURNING id',
                list(fields.values())
            )
            result = cur.fetchone()
            if not self._in_transaction: self.conn.commit()
            cur.close()
            return result[0] if result else None
        except Exception:
            # Heal the connection so one failed insert can't leave the session in
            # aborted-transaction state (every later read would return empty).
            self.conn.rollback()
            try: cur.close()
            except Exception: pass
            raise   # re-raise so the executor routes to on.failure as before

    def update(self, table, updates, match_field, match_value):
        try:
            set_clause = ', '.join(f'"{k}" = %s' for k in updates)
            params = list(updates.values()) + [match_value]
            cur = self.conn.cursor()
            cur.execute(
                f'UPDATE "{table}" SET {set_clause} WHERE "{match_field}" = %s',
                params
            )
            count = cur.rowcount
            if not self._in_transaction: self.conn.commit()
            cur.close()
            return count
        except Exception:
            self.conn.rollback()
            return 0

    def retrieve_one_multi(self, table, conditions):
        try:
            if not conditions:
                return None
            cur = self.conn.cursor(cursor_factory=self._cursor_factory)
            where = ' AND '.join(f'"{f}" = %s' for f in conditions)
            cur.execute(f'SELECT * FROM "{table}" WHERE {where} LIMIT 1',
                        list(conditions.values()))
            row = cur.fetchone()
            cur.close()
            return _cast_row(dict(row)) if row else None
        except Exception:
            self.conn.rollback()   # heal aborted-transaction state; don't brick the session
            return None

    def retrieve_one_spec(self, table, spec):
        try:
            where, params = _spec_to_sql(spec, '%s', '"')
            cur = self.conn.cursor(cursor_factory=self._cursor_factory)
            cur.execute(f'SELECT * FROM "{table}" WHERE {where} LIMIT 1', params)
            row = cur.fetchone()
            cur.close()
            return _cast_row(dict(row)) if row else None
        except Exception:
            self.conn.rollback()   # heal aborted-transaction state; don't brick the session
            return None

    def retrieve_all_spec(self, table, spec):
        """Multi-row retrieve. Empty spec means every row."""
        try:
            cur = self.conn.cursor(cursor_factory=self._cursor_factory)
            if spec:
                where, params = _spec_to_sql(spec, '%s', '"')
                cur.execute(f'SELECT * FROM "{table}" WHERE {where}', params)
            else:
                cur.execute(f'SELECT * FROM "{table}"')
            rows = cur.fetchall()
            cur.close()
            return [_cast_row(dict(r)) for r in rows]
        except Exception:
            self.conn.rollback()
            return []

    def update_multi(self, table, updates, conditions):
        try:
            if not conditions:
                return 0
            set_clause = ', '.join(f'"{k}" = %s' for k in updates)
            where = ' AND '.join(f'"{f}" = %s' for f in conditions)
            params = list(updates.values()) + list(conditions.values())
            cur = self.conn.cursor()
            cur.execute(f'UPDATE "{table}" SET {set_clause} WHERE {where}', params)
            count = cur.rowcount
            if not self._in_transaction: self.conn.commit()
            cur.close()
            return count
        except Exception:
            self.conn.rollback()
            return 0

    def upsert(self, table, fields, match_field):
        """Postgres native upsert — INSERT ON CONFLICT DO UPDATE. `match_field` is a single
        column name or a list of columns (a composite unique constraint, e.g. UNIQUE(a, b))."""
        match_cols = [match_field] if isinstance(match_field, str) else list(match_field)
        self.ensure_table(table, list(fields.keys()), id_value=fields.get('id'))
        cols     = ', '.join(f'"{k}"' for k in fields)
        phs      = ', '.join('%s' for _ in fields)
        conflict = ', '.join(f'"{c}"' for c in match_cols)
        # DO UPDATE SET excludes EVERY conflict column, not just the first (a conflict column
        # cannot be reassigned from EXCLUDED). If nothing is left to set, DO NOTHING.
        set_cols = [k for k in fields if k not in match_cols]
        do = ('DO NOTHING' if not set_cols
              else 'DO UPDATE SET ' + ', '.join(f'"{k}" = EXCLUDED."{k}"' for k in set_cols))
        try:
            cur = self.conn.cursor()
            cur.execute(
                f'INSERT INTO "{table}" ({cols}) VALUES ({phs}) '
                f'ON CONFLICT({conflict}) {do}',
                list(fields.values())
            )
            if not self._in_transaction: self.conn.commit()
            cur.close()
            return 1
        except Exception as e:
            self.conn.rollback()
            raise

    def remove_all(self, table):
        cur = self.conn.cursor()
        cur.execute(f'DELETE FROM "{table}"')
        count = cur.rowcount
        if not getattr(self, '_in_transaction', False): self.conn.commit()
        return count

    def remove(self, table, match_field, match_value):
        try:
            cur = self.conn.cursor()
            cur.execute(
                f'DELETE FROM "{table}" WHERE "{match_field}" = %s',
                (match_value,)
            )
            count = cur.rowcount
            if not self._in_transaction: self.conn.commit()
            cur.close()
            return count
        except Exception:
            self.conn.rollback()
            return 0

    def remove_multi(self, table, conditions):
        try:
            if not conditions:
                return 0
            where = ' AND '.join(f'"{f}" = %s' for f in conditions)
            cur = self.conn.cursor()
            cur.execute(f'DELETE FROM "{table}" WHERE {where}', list(conditions.values()))
            count = cur.rowcount
            if not self._in_transaction: self.conn.commit()
            cur.close()
            return count
        except Exception:
            self.conn.rollback()
            return 0

    def save_if_not_exists(self, table, fields, key_cols):
        """Insert only if no row matches key_cols -- `save ... unless a, b exists`. One atomic
        statement, and deliberately NOT ON CONFLICT (which needs a matching UNIQUE constraint
        that Mohio's auto-created tables do not have on non-id columns). See the SQLite
        implementation for the full reasoning."""
        from mohio_audit_grades import assert_write_allowed as _awa
        _awa(table)
        self.ensure_table(table, list(fields.keys()), id_value=fields.get('id'))
        cols  = ', '.join(f'"{k}"' for k in fields)
        phs   = ', '.join('%s' for _ in fields)
        where = ' AND '.join(f'"{c}" = %s' for c in key_cols)
        cur = self.conn.cursor()
        try:
            cur.execute(
                f'INSERT INTO "{table}" ({cols}) SELECT {phs} '
                f'WHERE NOT EXISTS (SELECT 1 FROM "{table}" WHERE {where}) RETURNING id',
                list(fields.values()) + [fields.get(c) for c in key_cols]
            )
            row = cur.fetchone()
            if not self._in_transaction: self.conn.commit()
            cur.close()
            return row[0] if row else None
        except Exception:
            self.conn.rollback()
            try: cur.close()
            except Exception: pass
            raise

    def begin_transaction(self):
        self._in_transaction = True
        self.conn.autocommit = False

    def commit_transaction(self):
        self.conn.commit()
        self._in_transaction = False

    def rollback_transaction(self):
        self.conn.rollback()
        self._in_transaction = False

    def close(self):
        self.conn.close()


class MySQLRuntime:
    """
    MySQL/MariaDB backend — same interface as DbRuntime.
    MariaDB is fully compatible — declare as mysql or mariadb.
    Requires: pymysql
    connect db as mysql from env.MYSQL_URL
    connect db as mariadb from env.MYSQL_URL
    """
    def __init__(self, url):
        try:
            import pymysql
            import pymysql.cursors
            # Parse URL: mysql://user:pass@host:port/dbname
            import urllib.parse as _up
            p = _up.urlparse(url)
            self.conn = pymysql.connect(
                host=p.hostname or 'localhost',
                port=p.port or 3306,
                user=p.username,
                password=p.password,
                database=p.path.lstrip('/'),
                cursorclass=pymysql.cursors.DictCursor,
                autocommit=False,
            )
            self._in_transaction = False
        except ImportError:
            raise RuntimeError(
                "MySQL driver not installed.\n"
                "Add to requirements.txt: pymysql>=1.1.0\n"
                "Or run: pip install pymysql"
            )
        except Exception as e:
            raise RuntimeError(f"MySQL connection failed: {e}\n"
                               f"Check MYSQL_URL environment variable.")

    def ensure_table(self, table, columns, id_value=None):
        field_cols = [c for c in columns if c != 'id']
        _s = id_value.strip() if isinstance(id_value, str) else None
        _id_is_text = _s is not None and not (_s.isdigit() or (_s[:1] == '-' and _s[1:].isdigit()))
        _id_def = '`id` VARCHAR(255) PRIMARY KEY' if _id_is_text else '`id` BIGINT PRIMARY KEY AUTO_INCREMENT'
        _parts = [_id_def] + [f'`{c}` TEXT' for c in field_cols]
        # 3a: emit the composite UNIQUE the program declared via `unless a, b exists`.
        # MySQL cannot index a TEXT column without a prefix length, hence the (255).
        _uniq = [c for c in (getattr(self, '_declared_unique', {}) or {}).get(table, [])
                 if c in field_cols]
        if len(_uniq) > 1:
            _parts.append('UNIQUE(' + ', '.join(f'`{c}`(255)' for c in _uniq) + ')')
        cols = ', '.join(_parts)
        cur = self.conn.cursor()
        cur.execute(f'CREATE TABLE IF NOT EXISTS `{table}` ({cols})')
        # Reconcile columns: widen an existing narrow table. MySQL lacks a portable
        # ADD COLUMN IF NOT EXISTS, so diff against information_schema first.
        cur.execute("SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND table_name = %s", (table,))
        existing = {r[0] for r in cur.fetchall()}
        for c in columns:
            if c not in existing:
                cur.execute(f'ALTER TABLE `{table}` ADD COLUMN `{c}` TEXT')
        self.conn.commit()
        cur.close()

    def retrieve_one(self, table, match_field, match_value):
        try:
            cur = self.conn.cursor()
            cur.execute(
                f'SELECT * FROM `{table}` WHERE `{match_field}` = %s LIMIT 1',
                (match_value,)
            )
            row = cur.fetchone()
            cur.close()
            return _cast_row(dict(row)) if row else None
        except Exception:
            self.conn.rollback()
            return None

    def find_many(self, table, where=None, limit=None,
                  order_by=None, order_dir='asc', offset=0):
        try:
            cur = self.conn.cursor()
            clauses, params = [], []
            for f, v in (where or {}).items():
                clauses.append(f'`{f}` = %s')
                params.append(v)
            sql = f'SELECT * FROM `{table}`'
            if clauses: sql += ' WHERE ' + ' AND '.join(clauses)
            # Ordering + ordered/offset limiting happen in Python (cast-on-read
            # first); skip the SQL LIMIT when paginating so the offset is applied
            # after ordering in _finalize_rows.
            if limit and not order_by and not offset: sql += f' LIMIT {int(limit)}'
            cur.execute(sql, params)
            rows = _finalize_rows([dict(r) for r in cur.fetchall()],
                                  order_by, order_dir, limit, offset)
            cur.close()
            return rows
        except Exception:
            self.conn.rollback()
            return []

    def count(self, table, where=None):
        """COUNT(*) honoring equality filters — used for pagination totals."""
        cur = self.conn.cursor()
        clauses, params = [], []
        for f, v in (where or {}).items():
            clauses.append(f'`{f}` = %s'); params.append(v)
        sql = f'SELECT COUNT(*) AS cnt FROM `{table}`'
        if clauses:
            sql += ' WHERE ' + ' AND '.join(clauses)
        try:
            cur.execute(sql, params)
        except Exception as e:
            # Missing table → 0 (parity with sqlite OperationalError path).
            # Any other error fails loud rather than silently reporting 0 rows.
            self.conn.rollback(); cur.close()
            if getattr(e, 'args', [None])[0] == 1146 or "doesn't exist" in str(e).lower():
                return 0
            raise
        row = cur.fetchone(); cur.close()
        if not row:
            return 0
        # Connection uses DictCursor, so fetchone() is a dict; read by alias.
        n = row['cnt'] if isinstance(row, dict) else row[0]
        return int(n)

    def save(self, table, fields):
        from mohio_audit_grades import assert_write_allowed as _awa
        _awa(table)          # audit relations only via the chaining path
        self.ensure_table(table, list(fields.keys()), id_value=fields.get('id'))
        cols = ', '.join(f'`{k}`' for k in fields)
        phs  = ', '.join('%s' for _ in fields)
        cur  = self.conn.cursor()
        cur.execute(
            f'INSERT INTO `{table}` ({cols}) VALUES ({phs})',
            list(fields.values())
        )
        row_id = cur.lastrowid
        if not self._in_transaction: self.conn.commit()
        cur.close()
        return row_id

    def update(self, table, updates, match_field, match_value):
        try:
            set_clause = ', '.join(f'`{k}` = %s' for k in updates)
            params = list(updates.values()) + [match_value]
            cur = self.conn.cursor()
            cur.execute(
                f'UPDATE `{table}` SET {set_clause} WHERE `{match_field}` = %s',
                params
            )
            count = cur.rowcount
            if not self._in_transaction: self.conn.commit()
            cur.close()
            return count
        except Exception:
            self.conn.rollback()
            return 0

    def retrieve_one_multi(self, table, conditions):
        try:
            if not conditions:
                return None
            cur = self.conn.cursor()
            where = ' AND '.join(f'`{f}` = %s' for f in conditions)
            cur.execute(f'SELECT * FROM `{table}` WHERE {where} LIMIT 1',
                        list(conditions.values()))
            row = cur.fetchone()
            cur.close()
            return _cast_row(dict(row)) if row else None
        except Exception:
            return None

    def retrieve_one_spec(self, table, spec):
        try:
            where, params = _spec_to_sql(spec, '%s', '`')
            cur = self.conn.cursor()
            cur.execute(f'SELECT * FROM `{table}` WHERE {where} LIMIT 1', params)
            row = cur.fetchone()
            cur.close()
            return _cast_row(dict(row)) if row else None
        except Exception:
            return None

    def retrieve_all_spec(self, table, spec):
        """Multi-row retrieve. Empty spec means every row."""
        try:
            cur = self.conn.cursor()
            if spec:
                where, params = _spec_to_sql(spec, '%s', '`')
                cur.execute(f'SELECT * FROM `{table}` WHERE {where}', params)
            else:
                cur.execute(f'SELECT * FROM `{table}`')
            rows = cur.fetchall()
            cur.close()
            return [_cast_row(dict(r)) for r in rows]
        except Exception:
            return []

    def update_multi(self, table, updates, conditions):
        try:
            if not conditions:
                return 0
            set_clause = ', '.join(f'`{k}` = %s' for k in updates)
            where = ' AND '.join(f'`{f}` = %s' for f in conditions)
            params = list(updates.values()) + list(conditions.values())
            cur = self.conn.cursor()
            cur.execute(f'UPDATE `{table}` SET {set_clause} WHERE {where}', params)
            count = cur.rowcount
            if not self._in_transaction: self.conn.commit()
            cur.close()
            return count
        except Exception:
            self.conn.rollback()
            return 0

    def upsert(self, table, fields, match_field):
        """MySQL/MariaDB native upsert — INSERT ON DUPLICATE KEY UPDATE. `match_field` is a
        single column or a list; ON DUPLICATE KEY uses the table's unique keys, so the columns
        only need excluding from the SET (every conflict column, not just the first)."""
        match_cols = [match_field] if isinstance(match_field, str) else list(match_field)
        self.ensure_table(table, list(fields.keys()), id_value=fields.get('id'))
        cols   = ', '.join(f'`{k}`' for k in fields)
        phs    = ', '.join('%s' for _ in fields)
        set_cols = [k for k in fields if k not in match_cols]
        update = ', '.join(f'`{k}` = VALUES(`{k}`)' for k in set_cols) or '`id` = `id`'
        try:
            cur = self.conn.cursor()
            cur.execute(
                f'INSERT INTO `{table}` ({cols}) VALUES ({phs}) '                f'ON DUPLICATE KEY UPDATE {update}',
                list(fields.values())
            )
            if not self._in_transaction: self.conn.commit()
            cur.close()
            return 1
        except Exception:
            self.conn.rollback()
            return 0

    def remove_all(self, table):
        cur = self.conn.cursor()
        cur.execute(f'DELETE FROM `{table}`')
        count = cur.rowcount
        if not getattr(self, '_in_transaction', False): self.conn.commit()
        return count

    def remove(self, table, match_field, match_value):
        try:
            cur = self.conn.cursor()
            cur.execute(
                f'DELETE FROM `{table}` WHERE `{match_field}` = %s',
                (match_value,)
            )
            count = cur.rowcount
            if not self._in_transaction: self.conn.commit()
            cur.close()
            return count
        except Exception:
            self.conn.rollback()
            return 0

    def remove_multi(self, table, conditions):
        try:
            if not conditions:
                return 0
            where = ' AND '.join(f'`{f}` = %s' for f in conditions)
            cur = self.conn.cursor()
            cur.execute(f'DELETE FROM `{table}` WHERE {where}', list(conditions.values()))
            count = cur.rowcount
            if not self._in_transaction: self.conn.commit()
            cur.close()
            return count
        except Exception:
            self.conn.rollback()
            return 0

    def save_if_not_exists(self, table, fields, key_cols):
        """Insert only if no row matches key_cols -- `save ... unless a, b exists`. One atomic
        statement; deliberately NOT ON DUPLICATE KEY (which needs a matching unique key that
        Mohio's auto-created tables do not have on non-id columns). MySQL cannot SELECT from
        the table it inserts into without an alias, hence the derived-table wrapper."""
        from mohio_audit_grades import assert_write_allowed as _awa
        _awa(table)
        self.ensure_table(table, list(fields.keys()), id_value=fields.get('id'))
        cols  = ', '.join(f'`{k}`' for k in fields)
        phs   = ', '.join('%s' for _ in fields)
        where = ' AND '.join(f'`{c}` = %s' for c in key_cols)
        cur = self.conn.cursor()
        try:
            cur.execute(
                f'INSERT INTO `{table}` ({cols}) SELECT {phs} FROM DUAL '
                f'WHERE NOT EXISTS (SELECT 1 FROM (SELECT * FROM `{table}`) AS _chk '
                f'WHERE {where})',
                list(fields.values()) + [fields.get(c) for c in key_cols]
            )
            inserted = cur.rowcount
            row_id = cur.lastrowid
            if not self._in_transaction: self.conn.commit()
            cur.close()
            return row_id if inserted else None
        except Exception:
            self.conn.rollback()
            try: cur.close()
            except Exception: pass
            raise

    def begin_transaction(self):    self._in_transaction = True
    def commit_transaction(self):   self.conn.commit();   self._in_transaction = False
    def rollback_transaction(self): self.conn.rollback(); self._in_transaction = False
    def close(self):                self.conn.close()


# MariaDB is MySQL-compatible — same class, same driver
MariaDBRuntime = MySQLRuntime


class MongoRuntime:
    """
    MongoDB backend — same interface as DbRuntime.
    Collections = tables. Documents = rows. _id = primary key.
    Requires: pymongo
    connect db as mongodb from env.MONGO_URL
    """
    def __init__(self, url):
        try:
            import pymongo
            self._client = pymongo.MongoClient(url)
            # Extract database name from URL or use 'mohio' as default
            import urllib.parse as _up
            p = _up.urlparse(url)
            db_name = p.path.lstrip('/') or 'mohio'
            self._mongo_db = self._client[db_name]
            self._in_transaction = False
            # Test connection
            self._client.admin.command('ping')
        except ImportError:
            raise RuntimeError(
                "MongoDB driver not installed.\n"
                "Add to requirements.txt: pymongo>=4.0.0\n"
                "Or run: pip install pymongo"
            )
        except Exception as e:
            raise RuntimeError(f"MongoDB connection failed: {e}\n"
                               f"Check MONGO_URL environment variable.")

    def ensure_table(self, table, columns):
        # MongoDB creates collections automatically — no schema needed
        if table not in self._mongo_db.list_collection_names():
            self._mongo_db.create_collection(table)
        # 3a: a declared `unless a, b exists` key becomes a real compound unique index, so the
        # collection enforces the same identity the relational backends do. create_index is
        # idempotent, so this is safe to re-run.
        _uniq = (getattr(self, '_declared_unique', {}) or {}).get(table, [])
        if len(_uniq) > 1:
            try:
                self._mongo_db[table].create_index([(c, 1) for c in _uniq], unique=True)
            except Exception:
                pass   # an existing index (or existing duplicate data) must not break the write path

    def retrieve_one(self, table, match_field, match_value):
        try:
            col = self._mongo_db[table]
            doc = col.find_one(_mongo_id_filter({match_field: match_value}))
            if doc:
                doc['id'] = str(doc.pop('_id', ''))
            return doc
        except Exception:
            return None

    def find_many(self, table, where=None, limit=None,
                  order_by=None, order_dir='asc', offset=0):
        try:
            col = self._mongo_db[table]
            query = _mongo_id_filter(where or {})
            cursor = col.find(query)
            if order_by:
                import pymongo
                direction = pymongo.ASCENDING if order_dir == 'asc' else pymongo.DESCENDING
                cursor = cursor.sort(order_by, direction)
            if offset:
                cursor = cursor.skip(int(offset))
            if limit:
                cursor = cursor.limit(int(limit))
            rows = []
            for doc in cursor:
                doc['id'] = str(doc.pop('_id', ''))
                rows.append(doc)
            return rows
        except Exception:
            return []

    def count(self, table, where=None):
        try:
            return self._mongo_db[table].count_documents(_mongo_id_filter(where or {}))
        except Exception:
            return 0

    def save(self, table, fields):
        try:
            col = self._mongo_db[table]
            result = col.insert_one(fields)
            return str(result.inserted_id)
        except Exception:
            return None

    def update(self, table, updates, match_field, match_value):
        try:
            col = self._mongo_db[table]
            result = col.update_many(
                _mongo_id_filter({match_field: match_value}),
                {'$set': updates}
            )
            return result.modified_count
        except Exception:
            return 0

    def retrieve_one_multi(self, table, conditions):
        try:
            col = self._mongo_db[table]
            doc = col.find_one(_mongo_id_filter(dict(conditions)))
            if doc:
                doc['id'] = str(doc.pop('_id', ''))
            return doc
        except Exception:
            return None

    def retrieve_one_spec(self, table, spec):
        try:
            doc = self._mongo_db[table].find_one(_spec_to_mongo(spec))
            if doc:
                doc['id'] = str(doc.pop('_id', ''))
            return doc
        except Exception:
            return None

    def retrieve_all_spec(self, table, spec):
        """Multi-row retrieve. Empty spec means every document."""
        try:
            q = _spec_to_mongo(spec) if spec else {}
            out = []
            for doc in self._mongo_db[table].find(q):
                doc['id'] = str(doc.pop('_id', ''))
                out.append(doc)
            return out
        except Exception:
            return []

    def update_multi(self, table, updates, conditions):
        try:
            col = self._mongo_db[table]
            result = col.update_many(_mongo_id_filter(dict(conditions)), {'$set': updates})
            return result.modified_count
        except Exception:
            return 0

    def upsert(self, table, fields, match_field):
        """MongoDB native upsert — update_one with upsert=True. `match_field` is a single field
        or a list; the filter matches on ALL of them (a composite key)."""
        try:
            col = self._mongo_db[table]
            match_cols = [match_field] if isinstance(match_field, str) else list(match_field)
            match_filter = {c: fields.get(c) for c in match_cols}
            update_fields = {k: v for k, v in fields.items() if k not in match_cols}
            result = col.update_one(
                match_filter,
                {'$set': update_fields, '$setOnInsert': match_filter},
                upsert=True
            )
            return 1
        except Exception:
            return 0

    def remove_all(self, table):
        try:
            return self._mongo_db[table].delete_many({}).deleted_count
        except Exception:
            return 0

    def remove(self, table, match_field, match_value):
        try:
            col = self._mongo_db[table]
            result = col.delete_many(_mongo_id_filter({match_field: match_value}))
            return result.deleted_count
        except Exception:
            return 0

    def remove_multi(self, table, conditions):
        try:
            col = self._mongo_db[table]
            result = col.delete_many(_mongo_id_filter(dict(conditions)))
            return result.deleted_count
        except Exception:
            return 0

    def save_if_not_exists(self, table, fields, key_cols):
        """Insert only if no doc matches key_cols -- `save ... unless a, b exists`. update_one
        with upsert=True and $setOnInsert is Mongo's atomic insert-if-absent: an existing doc
        is left untouched (matched, nothing modified), an absent one is created."""
        try:
            col = self._mongo_db[table]
            key_filter = {c: fields.get(c) for c in key_cols}
            result = col.update_one(_mongo_id_filter(key_filter),
                                    {'$setOnInsert': dict(fields)}, upsert=True)
            return str(result.upserted_id) if result.upserted_id is not None else None
        except Exception:
            return None

    def begin_transaction(self):    pass   # MongoDB transactions need replica set
    def commit_transaction(self):   pass
    def rollback_transaction(self): pass
    def close(self):                self._client.close()


def _make_db_runtime(driver: str, db_path: str = ':memory:'):
    """
    Factory — returns the right DbRuntime based on declared driver type.
    driver: 'postgres' | 'postgresql' | 'mysql' | 'sqlite' | anything else → sqlite

    A NAMED backend with no connection string raises. It used to fall back to SQLite
    with a printed notice, as a local-dev convenience, and that convenience is the trap:
    the app boots, serves 200s, and writes to the wrong database. The default path is
    `:memory:`, so on a host that sleeps machines the data is not merely in the wrong
    place, it is gone at the next restart while the app still looks healthy. A program
    that says `as postgres` and silently gets SQLite is the same failure as asking for
    real AI and silently getting the mock.

    To run on SQLite, say so: `connect db as sqlite`. The declaration is the opt-in.
    """
    driver = (driver or 'sqlite').lower()

    _named = {
        'postgres': 'DATABASE_URL', 'postgresql': 'DATABASE_URL',
        'mysql': 'MYSQL_URL', 'mariadb': 'MYSQL_URL',
        'mongodb': 'MONGO_URL', 'mongo': 'MONGO_URL',
    }

    def _missing(env_names):
        raise RuntimeError(
            f"declared as {driver}, but {' and '.join(env_names)} is not set.\n"
            f"    Mohio will not quietly run a {driver} program on SQLite -- the app "
            f"would start, answer normally, and write everything to the wrong database. "
            f"The fallback also used an in-memory database, so the data would disappear "
            f"the next time the app restarted.\n"
            f"    To proceed: set {env_names[0]}, or declare the database you actually "
            f"want with `connect db as sqlite`.")

    if driver in ('postgres', 'postgresql'):
        url = (os.environ.get('DATABASE_URL') or '').strip()
        if url:
            return PostgresRuntime(url)
        _missing(['DATABASE_URL'])

    elif driver in ('mysql', 'mariadb'):
        url = ((os.environ.get('MYSQL_URL') or os.environ.get('DATABASE_URL') or '')
               .strip())
        if url:
            return MySQLRuntime(url)
        _missing(['MYSQL_URL', 'DATABASE_URL'])

    elif driver in ('mongodb', 'mongo'):
        url = ((os.environ.get('MONGO_URL') or os.environ.get('MONGODB_URL') or '')
               .strip())
        if url:
            return MongoRuntime(url)
        _missing(['MONGO_URL', 'MONGODB_URL'])

    elif driver not in ('sqlite', 'sqlite3', ''):
        # An unknown backend name is a typo, not a request for SQLite. Quietly
        # treating `as postgress` as SQLite is the same silent-wrong-database bug.
        raise RuntimeError(
            f"'{driver}' is not a database Mohio knows.\n"
            f"    To proceed: use sqlite, postgres, mysql or mongodb.")

    # SQLite: asked for by name, or no backend named at all.
    if str(db_path) == ':memory:':
        print("  [connect] SQLite in memory -- everything written is lost when the app "
              "stops. Fine for a test, never for real data.")
    else:
        print(f"  [connect] SQLite file {db_path} -- data lives in this one file. On a "
              f"host that resets its disk or sleeps the machine, it does not survive.")
    return DbRuntime(db_path)


# ══════════════════════════════════════════════════════════════
# AI RUNTIME
# ══════════════════════════════════════════════════════════════

@dataclass
class AiDecision:
    result:      Any
    confidence:  float
    model:       str
    inputs:      dict
    explanation: Optional[str] = None
    fell_back:   bool = False


@dataclass
class AgentTurn:
    """One turn of an agent's reasoning, deliberately tiny. The model either
    finishes with an answer (kind='text') or asks to call exactly one granted
    tool (kind='tool'). That is the whole contract the loop needs. The messy
    provider-specific shape (Anthropic content blocks, stop reasons) is
    translated into this by the runtime adapter, so the loop never sees it."""
    kind:       str                       # 'text' | 'tool'
    text:       str   = ""                # the answer, when kind == 'text'
    tool_name:  str   = ""                # which tool, when kind == 'tool'
    tool_input: dict  = field(default_factory=dict)
    tool_id:    str   = ""                # provider id, to match the result back
    tokens:     int   = 0                 # usage for the boundary gate
    cost:       float = 0.0


class MockAiRuntime:
    """Deterministic mock AI runtime for Phase 1 testing."""

    def __init__(self):
        self._overrides = {}
        self._chains = {}          # ai.connect provider chains, so the mock is a first-class adapter

    def set_response(self, name, result, confidence=0.95):
        self._overrides[name] = AiDecision(
            result=result, confidence=confidence,
            model='mock-v1', inputs={}
        )

    def register_chain(self, chain_name, providers):
        """ai.connect on the mock: register the chain so ai.connect actually does something in
        mock/no-key mode (classroom weeks 1-3) instead of silently no-opping."""
        from mohio_ai import ResolvedChain
        chain = ResolvedChain(chain_name, list(providers or []))
        self._chains[chain_name] = chain
        return chain

    def resolve_chain(self, chain_name):
        """Mock resolution: no live pinging; the first provider is the active one."""
        return self._chains.get(chain_name)

    def generate_text(self, goal="", persona="", context="", style="",
                      model=None, temperature=None, max_tokens=None):
        # Deterministic offline generation so program flow (binding, downstream
        # use) is exercisable without a live model. A real runtime replaces this.
        g = (goal or "").strip()
        return f"[mock ai.create text] {g}" if g else "[mock ai.create text]"

    def generate_image(self, goal="", style="", negative="",
                       size="1024x1024", model=None):
        return (f"[mock ai.create image {size}] {goal}".strip())

    def generate_video(self, goal="", style="", duration=None,
                       size=None, model=None):
        return (f"[mock ai.create video] {goal}".strip())

    def generate_audio(self, goal="", **kw):
        return (f"[mock ai.create audio] {goal}".strip())

    def decide(self, name, inputs, threshold=0.85,
               return_type='boolean', chain_name=None,
               system_prompt=None, persona=None, context=None,
               temperature=None, max_tokens_override=None,
               model_override=None):
        if name in self._overrides:
            d = self._overrides[name]
            d.inputs = inputs
            return d

        # Route by return_type and decision name to give sensible mock results
        name_lower = name.lower()

        # Boolean decisions — return True/False based on input signals
        if return_type == 'boolean':
            # Fraud/risk: flag large amounts
            for k, v in inputs.items():
                if 'amount' in k.lower():
                    raw = v.to_python() if isinstance(v, MohioValue) else v
                    try:
                        amount = float(raw) if raw else 0
                        is_flagged = amount > 50000
                        return AiDecision(
                            result=is_flagged,
                            confidence=0.92 if is_flagged else 0.88,
                            model='mock-v1', inputs=inputs
                        )
                    except (ValueError, TypeError):
                        pass
            # Default boolean — return True with high confidence
            return AiDecision(result=True, confidence=0.91,
                              model='mock-v1', inputs=inputs)

        # Text decisions — return a plausible string based on context
        if return_type == 'text':
            # Game/command interpretation — return the command as-is
            for k, v in inputs.items():
                if k.lower() in ('command', 'input', 'query', 'text', 'message'):
                    raw = v.to_python() if isinstance(v, MohioValue) else v
                    return AiDecision(
                        result=str(raw) if raw else "unknown",
                        confidence=0.88,
                        explanation=f"Mock: echoing {k} input",
                        model='mock-v1', inputs=inputs
                    )
            # Triage/classification
            if 'primary_concern' in inputs:
                raw = inputs.get('primary_concern')
                raw = raw.to_python() if isinstance(raw, MohioValue) else raw
                level = "immediate" if raw and "chest" in str(raw).lower() else "standard"
                return AiDecision(result=level, confidence=0.96,
                                  model='mock-v1', inputs=inputs)
            return AiDecision(result="mock_result", confidence=0.88,
                              model='mock-v1', inputs=inputs)

        # Number decisions
        if return_type in ('number', 'decimal', 'integer'):
            return AiDecision(result=42, confidence=0.85,
                              model='mock-v1', inputs=inputs)

        # Result/status decisions
        if return_type == 'result':
            return AiDecision(result="approved", confidence=0.89,
                              model='mock-v1', inputs=inputs)

        # Default: not confident — trigger fallback
        return AiDecision(result=None, confidence=0.5,
                          model='mock-v1', inputs=inputs, fell_back=True)

    def agent_turn(self, *, messages, tools=None, model=None,
                   temperature=None, max_tokens=None):
        """One scripted agent turn, for deterministic offline agent loops.

        Reads self._agent_script (a list), consuming one item per call:
          - a dict {'tool': '<name>', 'input': {...}} -> a tool request
          - anything else (usually a string) -> a final text answer
        When the script is empty or unset, the agent finishes with a default
        text answer. The same script always drives the same sequence of tool
        calls, so a test can prove the gate cuts the loop off at its ceiling
        without ever touching the network or a live model.
        """
        script = getattr(self, '_agent_script', None)
        if script:
            item = script.pop(0)
            if isinstance(item, dict) and 'tool' in item:
                return AgentTurn(kind='tool', tool_name=item['tool'],
                                 tool_input=item.get('input', {}) or {},
                                 tool_id=f"mock_{len(script)}", tokens=10, cost=0.0)
            return AgentTurn(kind='text', text=str(item), tokens=10, cost=0.0)
        return AgentTurn(kind='text', text="Done.", tokens=5, cost=0.0)

    def explain(self, decision, audience='developer', fmt='paragraph'):
        return (
            f"Decision: {decision.result} "
            f"(confidence {decision.confidence:.0%}, model {decision.model}). "
            f"Inputs: {list(decision.inputs.keys())}."
        )


# ══════════════════════════════════════════════════════════════
# AUDIT LOG
# ══════════════════════════════════════════════════════════════

class AuditLog:
    def __init__(self, name, output_path=None):
        self.name        = name
        self.entries     = []
        self.output_path = output_path

    def record(self, entry):
        entry['log'] = self.name
        entry['ts']  = datetime.datetime.utcnow().isoformat()
        self.entries.append(entry)
        if self.output_path:
            with open(self.output_path, 'a') as f:
                f.write(json.dumps(entry) + '\n')

    def __len__(self): return len(self.entries)


# ══════════════════════════════════════════════════════════════
# SESSION STORES — see MohioInterpreter.register_session_store_provider above for the seam
# ══════════════════════════════════════════════════════════════

# The durable fields of a session Context -- everything a `hold X in session` value or
# runtime bookkeeping actually needs to survive a restart. Deliberately NOT included:
# _connections, _connectors, _shapes, _tasks (all inherited from __base__, rebuilt from
# source every cold start, never per-session), _sessions_store / _parent / _current_request
# (runtime scaffolding, reattached on load, never stored), _audit_log (the real audit trail
# is the durable record; this in-context list is a per-request convenience buffer, not a
# second copy of truth).
_SESSION_STATE_FIELDS = ('_locked', '_held', '_typed', '_roles', '_roles_verified',
                          '_created_at', '_last_accessed', '_session_id')


def _mohio_value_to_record(v):
    """MohioValue -> a plain, JSON-safe dict carrying its FULL state, not just
    .to_python(). MohioValue carries classification/formatting metadata beyond the raw
    value -- data_class (e.g. [pci]/[phi] tagging), _purposes/_purpose_fields ([pii]
    collection-purpose taint), _currency, _pad_places. A naive to_python() dump would
    silently drop all of it on every restart-reload: a PCI-tagged held value would come
    back unclassified after a process restart, the exact silent-default shape this
    project treats as urgent everywhere else. Every field round-trips explicitly."""
    return {
        'value': v.to_python(), 'type': v.mohio_type,
        'data_class': v.data_class, 'purposes': v._purposes,
        'purpose_fields': v._purpose_fields, 'currency': v._currency,
        'pad_places': v._pad_places,
    }


def _record_to_mohio_value(rec):
    """Inverse of _mohio_value_to_record -- rebuild a MohioValue with every field
    restored, not just the raw value."""
    v = MohioValue(rec.get('value'), rec.get('type', 'any'))
    v.data_class      = rec.get('data_class')
    v._purposes       = rec.get('purposes')
    v._purpose_fields = rec.get('purpose_fields')
    v._currency       = rec.get('currency')
    v._pad_places     = rec.get('pad_places')
    return v


def _session_context_to_state(ctx):
    """Context -> a plain, JSON-serializable dict: the durable fields only (see
    _SESSION_STATE_FIELDS), with _vars/_constants walked through
    _mohio_value_to_record so classification metadata survives too. Both in-memory
    and Postgres stores use this same function -- the in-memory store simply never
    calls it (see below), so it is exercised only on the path that actually needs it,
    but it is the ONE place this conversion is written."""
    return {
        'vars':       {k: _mohio_value_to_record(v) for k, v in ctx._vars.items()},
        'constants':  {k: _mohio_value_to_record(v) for k, v in ctx._constants.items()},
        'locked':     sorted(ctx._locked),
        'held':       sorted(ctx._held),
        'typed':      dict(ctx._typed),
        'roles':      list(ctx._roles),
        'roles_verified': bool(ctx._roles_verified),
    }


def _apply_state_to_context(ctx, state):
    """Rehydrate a freshly-created child Context (ctx = base_ctx.child()) with a state
    dict produced by _session_context_to_state. Only the durable fields are touched;
    everything else on ctx (inherited shapes/tasks/connections via the parent chain,
    runtime scaffolding set by the caller) is untouched."""
    ctx._vars      = {k: _record_to_mohio_value(r) for k, r in state.get('vars', {}).items()}
    ctx._constants = {k: _record_to_mohio_value(r) for k, r in state.get('constants', {}).items()}
    ctx._locked    = set(state.get('locked', ()))
    ctx._held      = set(state.get('held', ()))
    ctx._typed     = dict(state.get('typed', {}))
    ctx._roles     = list(state.get('roles', ()))
    ctx._roles_verified = bool(state.get('roles_verified', False))
    return ctx


class _InMemorySessionStore:
    """Default session store: a plain in-process dict, exactly what MohioServer.sessions
    has always been. Does NOT survive a process restart -- this is the open-core default,
    unchanged in every observable way from before this seam existed.

    get/put operate on the LIVE Context object directly, never through
    _session_context_to_state -- zero serialization cost on the default path. Serialization
    is a Postgres-store concern only (see _PostgresSessionStore), never paid here.

    __invalidated__ is its own explicit set, not a magic dict key sharing space with real
    sessions (the old shape this replaces). __base__ is not part of this store at all --
    it lives on MohioInterpreter._base_ctx instead (see run_with_session)."""

    def __init__(self):
        self._sessions = {}
        self._invalidated = set()

    def get(self, session_id, base_ctx):
        return self._sessions.get(session_id)

    def put(self, session_id, context):
        self._sessions[session_id] = context

    def delete(self, session_id):
        self._sessions.pop(session_id, None)

    def sweep_expired(self, now, idle_ceiling, absolute_ceiling):
        stale = [sid for sid, ctx in self._sessions.items()
                 if _is_session_expired(getattr(ctx, '_created_at', None),
                                         getattr(ctx, '_last_accessed', None),
                                         now, idle_ceiling, absolute_ceiling)]
        for sid in stale:
            self._sessions.pop(sid, None)
        return stale

    def is_invalidated(self, session_id):
        return session_id in self._invalidated

    def mark_invalidated(self, session_id):
        self._invalidated.add(session_id)

    def count(self):
        """Live session count, for MohioServer.stats()."""
        return len(self._sessions)


class _PostgresSessionStore:
    """Postgres-backed session store, built into open core (MOHIO_SESSION_STORE=postgres),
    reusing the app's own DATABASE_URL -- no new secret, same precedent as every other
    Mohio-managed table (lazy auto-create, matching save to / db.upsert; no separate
    migration step).

    Two tables, deliberately separate: mohio_sessions (live session state, deleted on
    invalidation/expiry to free storage) and mohio_sessions_invalidated (the blocklist,
    kept independently durable -- ruled 2026-08-05: losing it on restart reopens the
    fixation risk rotation exists to close, so it is NOT deleted when a session is, and
    is never swept by expiry).

    A session's state is stored as a JSON blob built by _session_context_to_state /
    restored by _apply_state_to_context -- the full MohioValue state per variable
    (value, type, data_class, purposes, purpose_fields, currency, pad_places), not a
    raw .to_python() dump, so a PCI/PII-classified held value survives a restart still
    classified. created_at/last_accessed stay as real columns (not inside the JSON) so
    sweep_expired can push the comparison into a SQL WHERE clause instead of hydrating
    every row's full state just to check two timestamps.

    Known, disclosed boundary: values that are not natively JSON-representable (Decimal,
    datetime) round-trip through json.dumps(..., default=str) -- the same stringify-on-
    write convention already used for audit-log serialization elsewhere in this file, not
    a new one invented here. A Decimal held in session survives a restart as its string
    form, not as a live Decimal.
    """

    def __init__(self, database_url):
        import psycopg2
        self._psycopg2 = psycopg2
        self.conn = psycopg2.connect(database_url)
        self.conn.autocommit = False
        self._ensure_tables()

    def _ensure_tables(self):
        cur = self.conn.cursor()
        try:
            cur.execute(
                'CREATE TABLE IF NOT EXISTS mohio_sessions ('
                '  session_id TEXT PRIMARY KEY,'
                '  state TEXT NOT NULL,'
                '  created_at DOUBLE PRECISION NOT NULL,'
                '  last_accessed DOUBLE PRECISION NOT NULL'
                ')'
            )
            cur.execute(
                'CREATE TABLE IF NOT EXISTS mohio_sessions_invalidated ('
                '  session_id TEXT PRIMARY KEY,'
                '  invalidated_at DOUBLE PRECISION NOT NULL'
                ')'
            )
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            raise RuntimeError(
                f"Could not build the session-store schema (mohio_sessions / "
                f"mohio_sessions_invalidated).\n  database said: {e}"
            ) from e
        finally:
            cur.close()

    def get(self, session_id, base_ctx):
        import json as _json
        cur = self.conn.cursor()
        try:
            cur.execute(
                'SELECT state, created_at, last_accessed FROM mohio_sessions '
                'WHERE session_id = %s', (session_id,))
            row = cur.fetchone()
        finally:
            cur.close()
        if row is None:
            return None
        state_json, created_at, last_accessed = row
        ctx = base_ctx.child()
        _apply_state_to_context(ctx, _json.loads(state_json))
        ctx._created_at = created_at
        ctx._last_accessed = last_accessed
        ctx._session_id = session_id
        return ctx

    def put(self, session_id, context):
        import json as _json, time as _time
        state = _session_context_to_state(context)
        state_json = _json.dumps(state, default=str)
        created_at = getattr(context, '_created_at', None) or _time.time()
        last_accessed = getattr(context, '_last_accessed', None) or created_at
        cur = self.conn.cursor()
        try:
            cur.execute(
                'INSERT INTO mohio_sessions (session_id, state, created_at, last_accessed) '
                'VALUES (%s, %s, %s, %s) '
                'ON CONFLICT (session_id) DO UPDATE SET '
                '  state = EXCLUDED.state, last_accessed = EXCLUDED.last_accessed',
                (session_id, state_json, created_at, last_accessed))
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        finally:
            cur.close()

    def delete(self, session_id):
        cur = self.conn.cursor()
        try:
            cur.execute('DELETE FROM mohio_sessions WHERE session_id = %s', (session_id,))
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        finally:
            cur.close()

    def sweep_expired(self, now, idle_ceiling, absolute_ceiling):
        # Same comparison _is_session_expired makes, pushed into SQL: durable expiry
        # (now - created_at > absolute_ceiling) OR idle expiry (now - last_accessed >
        # idle_ceiling). DELETE ... RETURNING does the read and the removal atomically.
        cur = self.conn.cursor()
        try:
            cur.execute(
                'DELETE FROM mohio_sessions WHERE '
                '  (%s - created_at) > %s OR (%s - last_accessed) > %s '
                'RETURNING session_id',
                (now, absolute_ceiling, now, idle_ceiling))
            freed = [r[0] for r in cur.fetchall()]
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        finally:
            cur.close()
        return freed

    def is_invalidated(self, session_id):
        cur = self.conn.cursor()
        try:
            cur.execute('SELECT 1 FROM mohio_sessions_invalidated WHERE session_id = %s',
                       (session_id,))
            return cur.fetchone() is not None
        finally:
            cur.close()

    def mark_invalidated(self, session_id):
        import time as _time
        cur = self.conn.cursor()
        try:
            cur.execute(
                'INSERT INTO mohio_sessions_invalidated (session_id, invalidated_at) '
                'VALUES (%s, %s) ON CONFLICT (session_id) DO NOTHING',
                (session_id, _time.time()))
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        finally:
            cur.close()

    def count(self):
        """Live session count, for MohioServer.stats()."""
        cur = self.conn.cursor()
        try:
            cur.execute('SELECT COUNT(*) FROM mohio_sessions')
            return cur.fetchone()[0]
        finally:
            cur.close()


# ══════════════════════════════════════════════════════════════
# INTERPRETER
# ══════════════════════════════════════════════════════════════

class MohioInterpreter:
    """
    Tree-walking interpreter. One _exec_* method per AST node type.
    """

    # ── Plugin Registry ─────────────────────────────────────────────────────
    # Commercial runtime registers executors here at startup.
    # Key:   AST node class name (e.g. 'MiochainTxBlock')
    # Value: callable(node, ctx) -> MohioValue
    #
    # Open core has stub executors that raise MohioRuntimeError.
    # Commercial binary calls register_executor() to replace them.
    #
    # Pattern:
    #   interpreter.register_executor('MiochainTxBlock', _commercial_exec_tx)
    _plugin_registry: dict = {}

    def __init__(self, ai=None, db_path=':memory:', verbose=False):
        self.ai          = ai or MockAiRuntime()
        self._current_line = 0        # line of the innermost node being executed (for runtime errors)
        self.db_path     = db_path
        self.verbose     = verbose
        # __base__ (shared declarations: shapes/tasks/connections) lives HERE, on the
        # interpreter instance -- never in the pluggable session store. It is rebuilt from
        # source (_exec_declarations) on every cold start, exactly like before this seam
        # existed; a session-store provider is never consulted for it (2026-08-05 build,
        # satisfies the durable session-store brief's acceptance criterion #5 by
        # construction rather than by a special-case guard).
        self._base_ctx   = None
        self._audit_logs = {}
        self._db         = None
        self._db_target  = None   # (driver, resolved-url/path) of the active connection
        # Cost controller (open core): per-request AI-call ceiling + loop iteration guard.
        import os as _os
        self._ai_call_count   = 0
        self.shown            = []     # `show` output buffer — surfaced by `mio run`
        self._encrypted_fields = set() # field names marked sec.encrypt (per run)
        self._pci_fields       = set() # [pci] field names -> masked to last-4 on output
        self._phi_fields       = set() # [phi] field names -> audit-on-access (HIPAA)
        self._tagged_tables    = set() # tables ever written with a sensitive field -> writes stay trailed
        self._field_purposes   = {}    # [pii] field name -> set of collection purposes
        self._purpose_scope    = []    # stack of asserted purposes (purpose "x" ... purpose: done)
        self._schedules       = {}     # name -> {tasks, modifiers, raw} registered by mioschedule
        self._validation_rules = {}   # name -> list[MiovalidateRule], registered by miovalidate
        self._ai_call_limit   = int(_os.environ.get('MOHIO_MAX_AI_CALLS', '25'))
        self._loop_iter_limit   = int(_os.environ.get('MOHIO_MAX_LOOP_ITERATIONS', '100000'))
        self._run_seconds_limit = float(_os.environ.get('MOHIO_MAX_RUN_SECONDS', '30'))
        self._run_deadline      = None

    @classmethod
    def register_executor(cls, node_class_name: str, executor_fn):
        """
        Register a commercial executor for a given AST node class name.

        Called by the commercial runtime binary at startup to replace
        open core stub executors with full implementations.

        Args:
            node_class_name: Exact AST class name e.g. 'MiochainTxBlock'
            executor_fn:     callable(node, ctx) -> MohioValue

        Example (commercial binary):
            MohioInterpreter.register_executor(
                'MiochainTxBlock', _exec_commercial_tx
            )
        """
        cls._plugin_registry[node_class_name] = executor_fn

    @classmethod
    def unregister_executor(cls, node_class_name: str):
        """Remove a registered executor (for testing)."""
        cls._plugin_registry.pop(node_class_name, None)

    @classmethod
    def registered_executors(cls) -> list:
        """List all currently registered commercial executors."""
        return list(cls._plugin_registry.keys())

    # ── Public API ────────────────────────────────────────────

    def _charge_ai_call(self, where=''):
        """Cost controller: count AI calls and fail loud past the per-request ceiling."""
        self._ai_call_count += 1
        if self._ai_call_limit > 0 and self._ai_call_count > self._ai_call_limit:
            raise _Raise(error_name='ai_budget_exceeded',
                message=f"AI call budget exceeded: {self._ai_call_count} calls (limit {self._ai_call_limit}).",
                hint="A loop is likely calling AI repeatedly. Add an exit, or raise the ceiling with the "
                     "MOHIO_MAX_AI_CALLS environment variable. This guard prevents runaway AI spend.")

    def _check_deadline(self, node=None):
        """Cost controller: wall-clock guard -- the real protection against a heavy loop crashing the server."""
        if self._run_deadline is not None:
            import time as _time
            if _time.monotonic() > self._run_deadline:
                raise _Raise(error_name='run_timeout',
                    message=f"Execution exceeded the {self._run_seconds_limit:.0f}s time limit -- likely a runaway or infinite loop.",
                    line=getattr(node, 'line', None) if node else None,
                    hint="Ensure loops terminate, reduce the work, or raise MOHIO_MAX_RUN_SECONDS (0 = unlimited).")

    def _collect_client_listeners(self, program):
        """Walk the program for MioScript ClientListener nodes (top-level or nested)."""
        from mohio_ast import ClientListener
        out, seen = [], set()
        def walk(node):
            if id(node) in seen:
                return
            seen.add(id(node))
            if isinstance(node, ClientListener):
                out.append(node)
            for v in (vars(node).values() if hasattr(node, '__dict__') else []):
                for it in (v if isinstance(v, list) else [v]):
                    if hasattr(it, '__dict__'):
                        walk(it)
        walk(program)
        return out

    def _exec_ClientListener(self, node, ctx):
        """MioScript blocks run in the browser, not on the server. Collected and
        compiled at load; here they are a no-op."""
        return None

    def _inject_mioscript(self, html):
        """Insert the compiled MioScript bundle before </body> (or append it)."""
        js = getattr(self, '_mioscript_js', '')
        if not js:
            return html
        tag = f'<script>\n{js}\n</script>'
        if '</body>' in html:
            return html.replace('</body>', tag + '\n</body>', 1)
        return html + '\n' + tag

    def run(self, program, request=None):
        self._ai_call_count = 0   # reset per request
        self._saga_failed = False  # reset saga-failure flag per request
        self.shown = []           # reset show output per request
        import time as _time
        self._run_deadline = (_time.monotonic() + self._run_seconds_limit) if self._run_seconds_limit > 0 else None
        self._session_mode = False   # stateless — child contexts per request
        self._scope_prewired = False  # stateless: journeys self-wire their scope
        ctx = Context()
        if request:
            ctx._current_request = request
            # Auth rebuild Item 1 (2026-08-02): the client `_roles` payload is NO LONGER
            # trusted -- not even behind MOHIO_TRUST_PROXY_ROLES, whose wholesale-trust
            # bypass is removed entirely. Roles are established server-side by `grant role`
            # at login. The legitimate reverse-proxy case returns later behind an explicit
            # trusted-header declaration (brief Requirement 1), not this env var.
            clean = {k: v for k, v in request.items() if not k.startswith('_')}
            # Expose cookies under request.cookie.NAME
            # request.cookie.mio_session works as a dotted name lookup
            raw_cookies = request.get('__request_cookies__', {})
            clean['cookie'] = raw_cookies
            # Also store separately for miocookie.get
            ctx.set('__request_cookies__', MohioValue(raw_cookies, 'shape'))
            ctx.set('request', MohioValue(clean, 'shape'))
            for k, v in clean.items():
                ctx.set(k, MohioValue(v) if not isinstance(v, dict)
                        else MohioValue(v, 'shape'))
        try:
            self._expand_program_includes(program)
            # AFTER include expansion: a `unless a, b exists` in an included file declares the
            # table's key just as one in the main file does, and a conflict between the two is
            # exactly the cross-file case this must catch.
            self._collect_declared_unique_keys(program)
            self._register_ai_blocks(program)
            # MioScript: collect browser-event listeners once per program and
            # compile them to a JS bundle that gets injected into served pages.
            if getattr(self, '_mioscript_for', None) != id(program):
                _client_listeners = self._collect_client_listeners(program)
                if _client_listeners:
                    from mohio_mioscript import compile_listeners
                    self._mioscript_js = compile_listeners(_client_listeners)
                else:
                    self._mioscript_js = ""
                self._mioscript_for = id(program)
            # A stateless REQUEST against a program with routable listeners
            # (listen-for / journey / page) routes by path through the shared
            # router so journeys + listeners coexist and an unmatched path yields a
            # clean 404 (never a silently-wrong page, never a stray last-match).
            # Top-level non-listener statements run first (declarations + setup),
            # exactly as _exec_program would; journeys self-wire their own scope.
            if request is not None and self._program_has_listeners(program):
                from mohio_ast import ListenBlock, JourneyDecl, PageDecl
                _listener_types = (ListenBlock, JourneyDecl, PageDecl)
                for stmt in program.statements:
                    if not isinstance(stmt, _listener_types):
                        self._exec(stmt, ctx)
                return self._route_program(program, ctx)
            return self._exec_program(program, ctx)
        except _Halt:
            return {'status': 200, 'body': 'halted'}
        except _GiveBack as gb:
            self._debug_trace(ctx,
                f"give back resolved -> {self._format_response(gb)!r}")
            return self._format_response(gb)
        except _Raise as r:
            if r.error_name == 'authorization_error':
                return {'status': 403, 'body': str(r.message)}
            return {'status': 500, 'body': str(r)}
        except _Jump as j:
            # A real HTTP redirect: 303 See Other (correct for post-redirect-get) and a
            # redirect_to the server turns into a Location header. Was a 302 with the URL
            # stuck in the body and no Location, which did not actually redirect.
            return {'status': 303, 'body': '', 'redirect_to': str(j.destination)}

    def setup_test_db(self, seed_data=None, extra_tables=None):
        """Legacy: creates SQLite and seeds. Use run_declarations + seed_db for Postgres."""
        self._db = DbRuntime(self.db_path)
        from mohio_audit_grades import canonical_audit_columns as _cac
        self._db.ensure_table('fraud_audit_log', _cac())
        self._db.ensure_table('phi_audit_log', _cac())
        if seed_data:
            for table, rows in seed_data.items():
                if rows:
                    cols = list(dict.fromkeys(k for r in rows for k in r.keys()))
                    # Pass the seed's own id so ensure_table picks the right id-column type: a
                    # string id like "M001" needs a TEXT primary key, not INTEGER AUTOINCREMENT
                    # (which rejects the string on save with a datatype mismatch). Find the first
                    # row that actually carries an id (skip schema-template sentinels).
                    _seed_id = next((r.get('id') for r in rows
                                     if r.get('id') is not None
                                     and not any(str(v) == '__schema_template__' for v in r.values())),
                                    None)
                    self._db.ensure_table(table, cols, id_value=_seed_id)
                    for row in rows:
                        if any(str(v) == '__schema_template__' for v in row.values()):
                            continue  # schema-template sentinel: defines columns, not data
                        self._db.save(table, row)
        if extra_tables:
            for table, cols in extra_tables.items():
                self._db.ensure_table(table, cols)
        return self

    def _static_table_name(self, target):
        """Resolve a save target to a bare table name WITHOUT a runtime context.
        Used by the whole-program unique-key scan, which runs before anything executes."""
        if target is None:
            return None
        if type(target).__name__ == 'DbRef':
            return getattr(target, 'table', None)
        parts = getattr(target, 'parts', None)
        if parts:
            return str(parts[-1])
        return None

    def _collect_declared_unique_keys(self, program):
        """Derive each table's composite UNIQUE key from the `save ... unless a, b exists`
        declarations in the source, so a clean-database deploy CREATES the constraint the
        program already declares instead of needing a manual migration step.

        One source of truth by design (ruled 2026-08-04): the key is stated once, at the write
        site, and the schema follows from it -- there is no second declaration that can drift.
        Two sites declaring DIFFERENT keys for the same table is unresolvable, so it fails loud
        naming both sites and both keys rather than silently picking one."""
        from mohio_ast import SaveBlock
        import dataclasses as _dc
        declared = {}   # table -> (tuple(cols), line)

        def walk(node):
            if node is None:
                return
            if isinstance(node, list):
                for x in node:
                    walk(x)
                return
            if not _dc.is_dataclass(node):
                return
            if isinstance(node, SaveBlock):
                cols = list(getattr(node, 'dedupe_fields', None) or [])
                table = self._static_table_name(getattr(node, 'target', None))
                if cols and table:
                    key = tuple(cols)
                    line = getattr(node, 'line', 0) or 0
                    prev = declared.get(table)
                    if prev is None:
                        declared[table] = (key, line)
                    elif set(prev[0]) != set(key):
                        raise MohioRuntimeError(
                            f"Conflicting `unless ... exists` keys for table '{table}':\n"
                            f"    line {prev[1]}: {', '.join(prev[0])}\n"
                            f"    line {line}: {', '.join(key)}\n"
                            f"A table has ONE identity. Both sites must name the same columns, "
                            f"or they are describing two different tables.")
            for f in _dc.fields(node):
                walk(getattr(node, f.name, None))

        walk(getattr(program, 'statements', None) or [])
        self._declared_unique = {t: list(k) for t, (k, _ln) in declared.items()}
        if self._db is not None:
            self._db._declared_unique = self._declared_unique
        return self._declared_unique

    def run_declarations(self, program):
        """
        Run only the declaration statements in a program.
        This fires _exec_ConnectDecl which sets up the real database
        (Postgres/MySQL/MongoDB) from the connect declaration.
        Call this BEFORE seed_db so seed goes into the right database.
        """
        self._collect_declared_unique_keys(program)
        ctx = Context()
        self._exec_declarations(program, ctx)
        # OQ-025: refuse to boot an app that is contractually required to encrypt but cannot.
        self._encryption_startup_guard(program)
        # Ensure audit tables exist in whatever db was connected (canonical schema)
        if self._db:
            try:
                from mohio_audit_grades import canonical_audit_columns as _cac
                self._db.ensure_table('fraud_audit_log', _cac())
                self._db.ensure_table('phi_audit_log', _cac())
            except Exception:
                pass  # Tables may already exist
        return self

    def _encryption_startup_guard(self, program):
        """OQ-025 (2026-08-01): if the app is contractually required to encrypt at rest (any field
        is marked encrypted / [phi]/[pii]/[pci] / sec.encrypt), the encryption backend MUST load at
        STARTUP -- refuse to boot rather than silently run unable to seal required data. Same for
        bcrypt when the app hashes a password. The backends are imported LAZILY at the point of use,
        so without this guard a stripped/broken `cryptography` first surfaces mid-write, after the
        app already looked healthy and accepted data -- the single worst failure mode in a
        compliance product. The guard is CONDITIONAL: an app with no encrypted/password fields is
        never forced to have the backend."""
        if getattr(self, '_encrypted_fields', None):
            try:
                from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
            except Exception as e:
                _fields = ', '.join(sorted(self._encrypted_fields))
                raise MohioRuntimeError(
                    f"This app encrypts data at rest (field(s): {_fields}), but the encryption "
                    f"backend (cryptography) could not load: {e}. Install it "
                    f"(pip install cryptography) -- the app must never boot unable to seal "
                    f"required data. A silent can-not-encrypt looks healthy while it breaches.")
        if self._program_hashes_with_bcrypt(program):
            try:
                import bcrypt  # noqa: F401
            except Exception as e:
                raise MohioRuntimeError(
                    f"This app hashes a password with bcrypt, but the bcrypt package could not "
                    f"load: {e}. Install it (pip install bcrypt) -- the app must never boot unable "
                    f"to hash a password it will be asked to store.")

    @staticmethod
    def _program_hashes_with_bcrypt(program):
        """True if the program contains a `hash ... using bcrypt` (HashBlock, algorithm=bcrypt)."""
        from mohio_ast import HashBlock
        seen = [False]
        def walk(n):
            if seen[0] or n is None or isinstance(n, (str, int, float, bool)):
                return
            if isinstance(n, HashBlock) and str(getattr(n, 'algorithm', '')).lower() == 'bcrypt':
                seen[0] = True
                return
            if isinstance(n, (list, tuple)):
                for c in n:
                    walk(c)
                return
            for a in getattr(n, '__dict__', {}).values():
                walk(a)
        walk(getattr(program, 'statements', program))
        return seen[0]

    def seed_db(self, seed_data, extra_tables=None):
        """
        Seed data into self._db — whatever database was connected.
        Call after run_declarations so seed goes into the real db (Postgres etc).
        Falls back to creating SQLite if no db was connected yet.
        """
        if self._db is None:
            self._db = DbRuntime(self.db_path)
        if seed_data:
            for table, rows in seed_data.items():
                if rows:
                    try:
                        cols = list(dict.fromkeys(k for r in rows for k in r.keys()))
                        _sid = next((r.get('id') for r in rows if isinstance(r, dict) and r.get('id') is not None), None)
                        self._db.ensure_table(table, cols, id_value=_sid)
                        for row in rows:
                            if any(str(v) == '__schema_template__' for v in row.values()):
                                continue  # schema-template sentinel: columns, not data
                            self._db.save(table, row)
                    except Exception as e:
                        if self.verbose:
                            print(f"  [seed] Warning: {table}: {e}")
        if extra_tables:
            for table, cols in extra_tables.items():
                try:
                    self._db.ensure_table(table, cols)
                except Exception:
                    pass
        return self

    # ── Dispatch ──────────────────────────────────────────────

    def _exec_program(self, node, ctx):
        result = None
        self._expand_program_includes(node)
        for stmt in node.statements:
            r = self._exec(stmt, ctx)
            if r is not None:
                result = r
        return result

    def _exec(self, node, ctx):
        """
        Dispatch execution to the appropriate executor method.

        Lookup order:
        1. Plugin registry — commercial executors registered at startup
        2. Standard executor — _exec_ClassName on this instance
        3. Stub executor — raises MohioRuntimeError for commercial features
        4. Unknown node — FAIL LOUD (raises MohioRuntimeError). A statement-level
           construct with no executor would otherwise silently do nothing, so it is
           surfaced rather than hidden. (Deliberate Phase-2 no-ops have their own
           explicit pass handlers and never reach this fallback.)
        """
        if node is None: return None

        # Track the innermost executing node's line so a runtime error can point at it. Statement
        # and expression nodes carry `.line`; the deepest one set before a raise is the right spot.
        _ln = getattr(node, 'line', 0)
        if _ln: self._current_line = _ln

        node_type = type(node).__name__

        # ── 1. Plugin registry — commercial runtime executors ──────────────
        # Commercial binary registers executors here via register_executor()
        plugin = self._plugin_registry.get(node_type)
        if plugin is not None:
            if self.verbose:
                print(f"  [interp] [commercial] {node_type}")
            return plugin(node, ctx)

        # ── 2. Standard executor ───────────────────────────────────────────
        method = getattr(self, f'_exec_{node_type}', None)
        if method is not None:
            return method(node, ctx)

        # ── 3. Check if this is a known commercial feature (stub) ──────────
        commercial_stubs = {
            'MiochainTxBlock':       'miochain.tx requires Mohio Commercial Runtime',
            'MiochainWalletBlock':   'miochain.wallet requires Mohio Commercial Runtime',
            'MiochainExecuteBlock':  'miochain.execute requires Mohio Commercial Runtime',
            'MiochainContractDecl':  'miochain.contract requires Mohio Commercial Runtime',
            'MioledgerAuditBlock':   'on chain audit requires Mohio Commercial Runtime',
        }
        if node_type in commercial_stubs:
            raise MohioRuntimeError(
                f"{commercial_stubs[node_type]}. "
                f"See mohio.io/commercial"
            )

        # ── 4. Unknown node — FAIL LOUD ──────────────────────────────────
        # No plugin, no standard executor, not a known commercial stub. A node
        # reaching here is a statement-level construct with no executor, so it
        # would otherwise silently do nothing. Surface it instead of hiding it.
        # (Expressions are evaluated via _eval and never reach _exec; deliberate
        # Phase-2 no-ops like pattern/miomap have explicit pass handlers and do
        # not reach here.)
        # If this is a raw Lark Tree (transformer never built an AST node), report
        # the parse-rule name so the error names the actual construct, not 'Tree'.
        construct = node_type
        _rule = getattr(node, 'data', None)
        if _rule is not None:
            construct = str(_rule)
        # A5: `request outbound` is retired -- steer to miohttp instead of a generic
        # "no executor" message.
        if construct == 'request_outbound_block':
            raise MohioRuntimeError(
                "request outbound is retired -- use miohttp.get / miohttp.post / "
                "miohttp.put / miohttp.delete / miohttp.patch for outbound HTTP (or "
                "mioconnect for named services, which compiles to miohttp).")
        raise MohioRuntimeError(
            f"No executor for '{construct}' -- this construct is not executable "
            f"in this build (it parsed and validated, but nothing runs it). "
            f"If you expected it to work, it is not yet wired in the interpreter."
        )

    def _exec_block(self, stmts, ctx):
        # Block bodies (loop iterations, if/check branches) run in the SAME
        # scope they're nested in, so assignments inside them mutate the
        # enclosing variables (e.g. a loop accumulator). Tasks get their own
        # isolated scope explicitly in _exec_CallBlock, so they are unaffected.
        result = None
        for stmt in stmts:
            result = self._exec(stmt, ctx)
        return result

    def _stub(self, name, node, ctx):
        # Tier-0 class-closure: every construct that reached _stub used to silently
        # return None ("declared but does nothing"). That is the exact failure mode
        # the runtime must never have. Fail loud instead, with the verb named, so a
        # not-yet-built construct can never quietly succeed while doing nothing.
        raise MohioRuntimeError(
            f"{name} is declared but not yet executable in this build (it parsed "
            f"and validated, but would silently do nothing). Tracked for a future "
            f"release.")

    # ── Declarations ──────────────────────────────────────────

    def _exec_SectorDecl(self, node, ctx):
        ctx._sector = node.sector
        if self.verbose: print(f"  [sector] {node.sector}")
        # Load sector profile and attach to context for runtime enforcement
        import sys as _sys
        _line = getattr(node, "line", None)
        _where = f" (line {_line})" if _line else ""
        try:
            from mohio_sector_loader import get_sector_profile
            profile = get_sector_profile(node.sector)
            ctx._sector_profile = profile
            # Register field classifications for runtime access
            ctx._never_store_fields = profile.never_store_fields
            ctx._confidence_floors = profile.confidence_floors
            ctx._sector_compliance = profile.compliance
            _slug = node.sector.replace(".", "-")
            _empty = (not profile.compliance and not profile.field_types
                      and not profile.confidence_floors)
            _seen = globals().setdefault("_SECTOR_WARN_SEEN", set())
            _first = node.sector not in _seen
            _seen.add(node.sector)
            if _first and _empty:
                from mohio_sector_loader import sector_requires_license as _srl
                if _srl(node.sector):
                    print(f"  [mohio.sector] note{_where}: '{node.sector}' is a licensed sector "
                          f"profile and is not bundled with the open compiler. The sector is "
                          f"active but enforces nothing until its licensed profile is present.\n"
                          f"                 provide the profile on the search path "
                          f"(~/.mohio/sectors) or run the licensed runtime.",
                          file=_sys.stderr)
                else:
                    print(f"  [mohio.sector] WARNING{_where}: no profile found for sector "
                          f"'{node.sector}'. Sector rules will NOT be enforced.\n"
                          f"                 fix: check the sector name, or place a profile file on the "
                          f"search path (./sectors, ~/.mohio/sectors).\n"
                          f"                 '.sector' = certified profile; '.mho' = community/uncertified.",
                          file=_sys.stderr)
            elif _first and not profile.field_types:
                print(f"  [mohio.sector] note{_where}: using built-in baseline rules for "
                      f"'{node.sector}' (no profile file found; field-type classifications inactive).\n"
                      f"                 add sector-{_slug}.sector (certified) or sector-{_slug}.mho "
                      f"(community) on the search path for full enforcement.",
                      file=_sys.stderr)
            if self.verbose:
                print(f"  [sector] profile loaded: {len(profile.field_types)} field types, "
                      f"{len(profile.confidence_floors)} confidence floors, "
                      f"{len(profile.never_store_fields)} never-store fields")
        except ImportError:
            print(f"  [mohio.sector] WARNING{_where}: sector loader (mohio_sector_loader) is not "
                  f"available; sector rules will NOT be enforced.\n"
                  f"                 fix: ensure mohio_sector_loader.py is installed alongside the compiler.",
                  file=_sys.stderr)
            ctx._sector_profile = None
            ctx._never_store_fields = set()
            ctx._confidence_floors = {}
            ctx._sector_compliance = []

    def _connection_target(self, driver):
        """Resolve the effective connection target for a driver, so a repeated
        connect declaration can tell whether anything actually changed. Reads
        the same env vars _make_db_runtime uses."""
        d = (driver or 'sqlite').lower()
        if d in ('postgres', 'postgresql'):
            return ('postgres', os.environ.get('DATABASE_URL'))
        if d in ('mysql', 'mariadb'):
            return ('mysql', os.environ.get('MYSQL_URL') or os.environ.get('DATABASE_URL'))
        if d in ('mongodb', 'mongo'):
            return ('mongo', os.environ.get('MONGO_URL') or os.environ.get('MONGODB_URL'))
        return ('sqlite', self.db_path)

    def _exec_ConnectDecl(self, node, ctx):
        driver = getattr(node, 'driver', None) or getattr(node, 'connect_type', 'sqlite')
        target = self._connection_target(driver)
        prev   = getattr(self, '_db_target', None)
        # Idempotent connect. Re-creating the runtime here throws away an
        # already-established (and possibly seeded) connection -- which breaks
        # the run_declarations/setup_test_db + seed_db -> run flow and needlessly
        # re-opens connections on the CLI's double-connect. Reuse the existing
        # db when one is present and the resolved target is unchanged (prev is
        # None when the db was set up externally, e.g. seed_db -- honor it).
        reuse = self._db is not None and (prev is None or prev == target)
        if not reuse:
            try:
                db_path = self.db_path
                # sqlite used to ignore its own declared `from` source entirely --
                # postgres/mysql/mongo already read os.environ themselves inside
                # _make_db_runtime, but sqlite always used self.db_path (the interpreter
                # constructor's own default) regardless of what env var the program
                # named. Invisible through the CLI (mio.py resolves and passes db_path=
                # before construction), but a real gap for any direct-Python caller.
                # The declared source now wins when it resolves to something; an unset
                # or empty var falls through to self.db_path unchanged, so the CLI's own
                # persistent-default behavior (and a bare MohioInterpreter() with no
                # source at all) is unaffected.
                if driver in ('sqlite', 'sqlite3', '') and getattr(node, 'source', None) is not None:
                    _declared = self._eval(node.source, ctx)
                    _declared_path = (_declared.to_python()
                                      if isinstance(_declared, MohioValue) else _declared)
                    if _declared_path:
                        db_path = str(_declared_path)
                self._db = _make_db_runtime(driver, db_path)
                if self.verbose:
                    print(f"  [connect] {node.name} as {driver} ({type(self._db).__name__})")
            except RuntimeError as e:
                # A declared database must FAIL LOUD, whether its URL is set and the
                # connection fails (bad credentials, unreachable host, missing driver)
                # or the URL is not set at all. Silently running on SQLite would send
                # production data to the wrong database, and the old no-URL fallback
                # used an in-memory one, so the data vanished at the next restart while
                # the app kept answering normally.
                raise MohioRuntimeError(
                    f"Database connection failed for '{node.name}' (declared as {driver}):\n"
                    f"    {e}\n"
                    f"Mohio does not fall back to SQLite when a database is declared. "
                    f"Fix the connection, or declare `as sqlite` if that is what you want.")
        self._db_target = target
        # Hand the runtime the program's declared unique keys so ensure_table can CREATE the
        # constraint the source already declares (3a: schema derived from the write sites).
        self._db._declared_unique = getattr(self, '_declared_unique', {}) or {}
        ctx.set_connection(node.name, self._db)
        if self.verbose and hasattr(self._db, 'conn'):
            print(f"  [connect] {node.name} → {type(self._db).__name__} ready")

    def _exec_ShapeDecl(self, node, ctx):
        ctx.set_shape(node.name, node)
        zone = getattr(node, 'zone_tag', None)
        # Zone tag: `shape Intake [phi]` seals EVERY field in the shape.
        if zone:
            zone_names = [z.strip() for z in str(zone).split(',')]
            for f in getattr(node, 'fields', []) or []:
                self._encrypted_fields.add(f.name)
                if 'phi' in zone_names:
                    self._phi_fields.add(f.name)
        # Field-level: register any field marked `sec.encrypt` so save encrypts it
        # and find/retrieve decrypt it. Class tags ([phi]/[pii]/[pci]) also encrypt;
        # [pci] additionally masks to last-4 on output.
        for f in getattr(node, 'fields', []) or []:
            mods = getattr(f, 'modifiers', []) or []
            for m in mods:
                mt = getattr(m, 'modifier_type', None)
                if mt == 'encrypt':
                    self._encrypted_fields.add(f.name)
                elif mt == 'tag' and getattr(m, 'value', None) in ('phi', 'pii', 'pci'):
                    self._encrypted_fields.add(f.name)
                    if m.value == 'pci':
                        self._pci_fields.add(f.name)
                    if m.value == 'phi':
                        self._phi_fields.add(f.name)
                elif mt == 'purpose' and m.value:
                    self._field_purposes.setdefault(f.name, set()).update(
                        pp.strip() for pp in str(m.value).split(',') if pp.strip())

    # ---- field-level encryption (at rest) --------------------------------------
    _ENC_PREFIX = 'enc:v1:'

    # ── Key provider seam ───────────────────────────────────────────────────────
    # WHERE the 32-byte encryption key comes from is the ONLY thing that differs
    # across deployments. WHAT gets encrypted, the fail-loud, the AES-GCM, and the
    # tag wiring are identical everywhere and live below, untouched.
    #
    #   open core / self-host  -> default provider: SHA-256 of MOHIO_ENCRYPTION_KEY
    #   getmohio (managed)     -> platform registers an envelope-encryption provider
    #                             (per-tenant data key, wrapped by a master in KMS)
    #   commercial, self-host  -> customer registers their AWS KMS / Vault provider
    #
    # A provider is a zero-arg callable returning 32 raw bytes, or None if no key is
    # configured (which makes every tagged write fail loud rather than store plaintext).
    # This mirrors the register_executor plugin pattern above: open core ships a
    # default, the commercial binary swaps it in at startup. It does NOT weaken the
    # guarantee -- a provider that returns None still triggers the same refusal.
    _key_provider = None   # class-level; set by register_key_provider()

    @classmethod
    def register_key_provider(cls, provider_fn):
        """Register the key provider (callable() -> 32 bytes | None).

        Called by the managed platform or a self-hosting commercial customer at
        startup to source the key from a KMS/vault instead of the env var. The
        enforcement path never changes; only the origin of the key does.
        """
        cls._key_provider = staticmethod(provider_fn).__func__

    @classmethod
    def unregister_key_provider(cls):
        """Restore the default env-var provider (used by tests)."""
        cls._key_provider = None

    _audit_sink_provider = None   # class-level; set by register_audit_sink_provider()

    @classmethod
    def register_audit_sink_provider(cls, provider_fn):
        """Register the audit-sink provider (callable(ctx) -> list of sinks | None).

        Called by the managed platform (or a self-hosting commercial customer) at startup so audit
        events go to DEDICATED, GRADED, governed audit sinks instead of the tenant's application
        `db`. Each returned sink is a db-like object with `ensure_table`/`save`, and may carry
        `_mohio_durable` (bool) and `_mohio_grade` (str: durable|append_only|worm) so the
        grade check can see whether the required grade is met.

        This is where physical placement lives: a Postgres sink can put audit tables in a dedicated
        `audit` schema with append-only grants; a WORM object-store sink can provide worm.
        The compiler names LOGICAL audit tables and never hardcodes schema syntax, so SQLite /
        MySQL / Mongo are unaffected. When no provider is registered (open core), audit falls back
        to the app db, exactly as before.
        """
        cls._audit_sink_provider = staticmethod(provider_fn).__func__

    @classmethod
    def unregister_audit_sink_provider(cls):
        """Restore the default (app-db) audit sink (used by tests)."""
        cls._audit_sink_provider = None

    _alert_sink = None   # class-level; set by register_alert_sink()

    @classmethod
    def register_alert_sink(cls, sink_fn):
        """Register the alert sink (callable(degraded_event: dict) -> None).

        Called by the managed platform to route degraded-audit incidents to the client's on-call
        channel (PagerDuty, Opsgenie, Slack, email, webhook). The runtime calls
        `alert_sink(degraded_event)` whenever an audit write degrades (durable but below the
        required grade, or no sink accepted it).

        NEVER SILENT: the alert sink is only the OUTBOUND notification. Whether or not one is
        registered, the degraded incident is still recorded to the durable incident log
        (`audit_incident_log`), so the absence of an alert sink never suppresses the record -- it
        only means no one gets paged. The incident still shows on the dashboard and in the
        queryable incident log.
        """
        cls._alert_sink = staticmethod(sink_fn).__func__

    @classmethod
    def unregister_alert_sink(cls):
        """Remove the alert sink (used by tests)."""
        cls._alert_sink = None

    # ── Session-store seam ──────────────────────────────────────────────────
    # WHERE session data lives is pluggable, same shape as the audit-sink and key-provider
    # seams above. WHAT a session IS and how it is used -- creation, rotation, invalidation,
    # expiry, all landed 2026-08-04 -- never changes; only physical storage does.
    #
    #   open core / self-host, no MOHIO_SESSION_STORE  -> in-memory (today's behavior,
    #                                                       unchanged; does not survive a
    #                                                       process restart)
    #   MOHIO_SESSION_STORE=postgres                     -> built into open core (see
    #                                                       _PostgresSessionStore below),
    #                                                       reuses the app's own DATABASE_URL,
    #                                                       survives a restart
    #   commercial / custom backend (Redis, a client's own store)
    #                                                     -> register_session_store_provider()
    #
    # A provider is a zero-arg callable returning a store object exposing:
    #   get(session_id, base_ctx) -> Context | None
    #   put(session_id, context)
    #   delete(session_id)
    #   sweep_expired(now, idle_ceiling, absolute_ceiling) -> list[freed session_ids]
    #   is_invalidated(session_id) -> bool
    #   mark_invalidated(session_id)
    #   count() -> int   (live session count, MohioServer.stats() only)
    # None means "use the in-memory default." An explicitly registered provider always wins
    # over MOHIO_SESSION_STORE (same explicit-instruction-outranks-env-default precedent as
    # the AI model-resolution ruling, 2026-08-04).
    _session_store_provider = None   # class-level; set by register_session_store_provider()

    @classmethod
    def register_session_store_provider(cls, provider_fn):
        """Register the session-store provider (zero-arg callable() -> store object).

        Called by the managed platform, or a self-hosting commercial customer, at startup
        so sessions survive a process restart or run behind a load balancer across multiple
        instances, instead of living only in this process's memory. __base__ (shared
        declarations) and the __invalidated__ blocklist are NOT part of this seam -- __base__
        is rebuilt from source every cold start (see MohioInterpreter._base_ctx) and
        __invalidated__ durability is the store's own is_invalidated/mark_invalidated pair,
        not a session.
        """
        cls._session_store_provider = staticmethod(provider_fn).__func__

    @classmethod
    def unregister_session_store_provider(cls):
        """Restore the default (in-memory) session store (used by tests)."""
        cls._session_store_provider = None

    @staticmethod
    def _default_key_provider():
        """Open-core default: 32-byte key from env MOHIO_ENCRYPTION_KEY, or None.

        SHA-256 derivation is the single-tenant MVP. It is deliberately NOT used
        by the managed or commercial deployments, which register a KMS-backed
        provider so one leaked secret cannot decrypt every tenant.
        """
        import os as _os, hashlib
        raw = _os.environ.get('MOHIO_ENCRYPTION_KEY')
        if not raw:
            return None
        return hashlib.sha256(raw.encode('utf-8')).digest()

    def _encryption_key(self):
        """Resolve the 32-byte key from the registered provider, else the default.

        Every caller of this method is unchanged. Whether the key comes from an env
        var, an envelope-wrapped per-tenant key, or a customer KMS is decided solely
        by which provider (if any) was registered at startup. None here still means
        'no key' and still makes tagged writes fail loud.
        """
        provider = type(self)._key_provider or self._default_key_provider
        key = provider()
        if key is not None and len(key) != 32:
            raise _Raise(
                error_name='encryption.bad_key',
                message=(f"The configured key provider returned a {len(key)}-byte key; "
                         f"field encryption requires exactly 32 bytes (AES-256)."),
                hint="A KMS/vault provider must return a raw 32-byte key, not a "
                     "base64 string or a wrapped blob. Unwrap before returning.")
        return key

    def _encrypt_field(self, value, key):
        import os as _os, base64
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        if value is None:
            return None
        text = value if isinstance(value, str) else str(value)
        if text.startswith(self._ENC_PREFIX):     # already encrypted
            return text
        nonce = _os.urandom(12)
        ct = AESGCM(key).encrypt(nonce, text.encode('utf-8'), None)
        return self._ENC_PREFIX + base64.b64encode(nonce + ct).decode('ascii')

    def _decrypt_field(self, value, key):
        import base64
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        if not isinstance(value, str) or not value.startswith(self._ENC_PREFIX):
            return value                            # plaintext or already decrypted
        if key is None:
            return value                            # no key -> leave ciphertext
        blob = base64.b64decode(value[len(self._ENC_PREFIX):])
        nonce, ct = blob[:12], blob[12:]
        return AESGCM(key).decrypt(nonce, ct, None).decode('utf-8')

    def _encrypt_fields_for_write(self, fields, node=None):
        """Encrypt any registered field in a save/update dict. Fails loud if a field
        needs encryption but no key is configured -- never silently store plaintext."""
        if not self._encrypted_fields:
            return fields
        to_enc = [k for k in fields if k in self._encrypted_fields and fields[k] is not None]
        if not to_enc:
            return fields
        key = self._encryption_key()
        if key is None:
            raise _Raise(
                error_name='encryption.key_missing',
                message=(f"Field(s) {', '.join(to_enc)} are marked sec.encrypt but no "
                         f"encryption key is set -- refusing to store them as plaintext."),
                line=getattr(node, 'line', None),
                hint="Set MOHIO_ENCRYPTION_KEY in the environment (a strong secret).")
        return {k: (self._encrypt_field(v, key) if k in self._encrypted_fields else v)
                for k, v in fields.items()}

    def _decrypt_row(self, row):
        """Decrypt registered fields in a fetched row (dict)."""
        if not self._encrypted_fields or not isinstance(row, dict):
            return row
        key = self._encryption_key()
        if key is None:
            return row
        for k in list(row.keys()):
            if k in self._encrypted_fields:
                try:
                    row[k] = self._decrypt_field(row[k], key)
                except Exception:
                    pass   # leave as-is if it can't be decrypted
        return row

    def _mask_last4(self, s):
        s = '' if s is None else str(s)
        return s if len(s) <= 4 else '****' + s[-4:]

    def _apply_pci_mask(self, raw):
        if isinstance(raw, dict):
            out = {}
            for k, v in raw.items():
                if k in self._pci_fields and isinstance(v, str) and not v.startswith(self._ENC_PREFIX):
                    out[k] = self._mask_last4(v)
                else:
                    out[k] = self._apply_pci_mask(v)
            return out
        if isinstance(raw, list):
            return [self._apply_pci_mask(x) for x in raw]
        return raw

    def _display_value(self, mv):
        cur = getattr(mv, '_currency', None) if isinstance(mv, MohioValue) else None
        if cur and self._is_currency(cur):
            return self._format_currency(mv.to_python(), cur)
        _pad = getattr(mv, '_pad_places', None) if isinstance(mv, MohioValue) else None
        if _pad is not None:
            _pv = mv.to_python()
            if isinstance(_pv, (int, float)) and not isinstance(_pv, bool):
                return f"{float(_pv):.{_pad}f}"
        if not self._pci_fields:
            return mv.to_python() if hasattr(mv, 'to_python') else mv
        raw = mv.to_python() if hasattr(mv, 'to_python') else mv
        if getattr(mv, 'data_class', None) == 'pci' and isinstance(raw, str):
            return self._mask_last4(raw)
        return self._apply_pci_mask(raw)

    def _giveback_masked(self, value):
        if not self._pci_fields or value is None:
            return value
        masked = self._display_value(value)
        mt = value.mohio_type if isinstance(value, MohioValue) else 'any'
        return MohioValue(masked, mt)

    def _exec_TaskDecl(self, node, ctx):
        ctx.set_task(node.name, node)

    def _exec_HoldDecl(self, node, ctx):
        if ctx.is_locked(node.name):
            raise MohioRuntimeError(
                f"'{node.name}' is locked and cannot be re-held -- a lock is permanent.")
        if ctx.is_held(node.name):
            raise MohioRuntimeError(
                f"'{node.name}' is already held and cannot be re-held. "
                f"Use `release {node.name}` first, or `release.now {node.name} = ...`.")
        if getattr(node, 'is_list', False):
            # List form: hold name / "a" / "b" / hold: done
            # node.items are already raw python values (str/num/bool) from the
            # transformer. Store as a real list so `random from <name>`, pull,
            # and iteration work.
            held_val = MohioValue(list(node.items or []), 'list')
            ctx.set(node.name, held_val, immutable=True)
        elif node.is_block:
            # Block form — store body as a definition dict
            body_dict = {}
            for item in node.body:
                if isinstance(item, FieldValue):
                    self._require_defined(item.value, ctx, f"hold {node.name} field '{item.name}'")
                    body_dict[item.name] = self._eval_simple(item.value, ctx)
            held_val = MohioValue(body_dict, 'definition')
            ctx.set(node.name, held_val, immutable=True)
        else:
            # A3: `hold x nobody` bound x to None silently. Fail loud on a bare undefined
            # source -- UNLESS a `default` is declared, which is exactly the sanctioned way
            # to say "if the source is missing, use this" and must keep working.
            if getattr(node, 'default', None) is None:
                self._require_defined(node.value, ctx, f"hold {node.name}")
            val = self._eval(node.value, ctx)
            val_py = val.to_python() if isinstance(val, MohioValue) else val
            # value-level fallback: `hold x source default Y` -- if the source is
            # missing/null/empty, use the default instead.
            if (val_py is None or val_py == '') and getattr(node, 'default', None) is not None:
                val = self._eval(node.default, ctx)
            held_val = val
            ctx.set(node.name, held_val, immutable=True)
        ctx.hold_name(node.name)  # frozen until `release`
        if self.verbose: print(f"  [hold] {node.name}")
        return held_val

    def _exec_LockDecl(self, node, ctx):
        if node.value is None:
            # `lock x` (no value): seal an existing variable in place. Fail loud if it does not
            # exist -- a lock on a missing name is a bug, not a no-op.
            if not ctx.exists(node.name):
                raise MohioRuntimeError(
                    f"lock: '{node.name}' does not exist -- declare it first, or use "
                    f"`lock {node.name} = <value>` to declare and lock in one step.")
            ctx.lock_name(node.name)
            if self.verbose: print(f"  [lock] {node.name} (in place)")
            return
        val = self._eval(node.value, ctx)
        ctx.set(node.name, val, immutable=True)
        ctx.lock_name(node.name)
        if self.verbose: print(f"  [lock] {node.name}")

    def _exec_ComplianceDecl(self, node, ctx):
        ctx._compliance.append(node.framework)
        if self.verbose: print(f"  [compliance] {node.framework}")

    def _exec_SecurityDecl(self, node, ctx):
        # Posture declaration (check-time enforced; runtime records it).
        # Locked June 8: only `off`/`standard` rungs are built. `off` is the
        # documented security-debt escape hatch — mio check --security demands
        # reason + expires. Recording posture here is real work, not a no-op.
        ctx._security_posture = node.level
        if node.level == "off":
            ctx._security_off_reason  = node.reason
            ctx._security_off_expires = node.expires
        if self.verbose:
            extra = f" (reason={node.reason!r}, expires={node.expires!r})" if node.level == "off" else ""
            print(f"  [security] {node.level}{extra}")

    def _exec_RequireRoleDecl(self, node, ctx):
        # Auth rebuild Item 1 (2026-08-02): roles are now established SERVER-side by
        # `grant role` at login, stored on the session root (survives across requests
        # via the session store -- in-memory or Postgres-backed, see
        # register_session_store_provider) and marked verified. The client `_roles` payload is
        # no longer consulted anywhere -- the old forgeable path is gone. `require role`
        # simply reads the server-side session store.
        #
        # The verified-guard below stays as a structural invariant: `require role` never
        # trusts an UNVERIFIED role. Nothing sets unverified roles today (grant role sets
        # verified=True), so it never fires falsely; if a future path ever introduced an
        # unverified role, this fails loud rather than trusting it.
        if ctx.has_any_roles() and not ctx.roles_verified():
            raise _Raise(
                error_name='authorization_error',
                message=("require role: the caller's roles are NOT server-verified -- refusing "
                         "rather than trusting an unverified claim. Roles must be established "
                         "server-side at login with `grant role`."))
        for role in node.roles:
            if ctx.has_role(role): return
        # Self-diagnosing refusal: name the missing role AND the fix. Without the pointer, an
        # app that suddenly 403s after the auth rebuild (roles no longer read from the client)
        # looks like a mystery outage -- this is exactly the shape Zork hit in production.
        raise _Raise(
            error_name='authorization_error',
            message=(f"Role required: {' or '.join(node.roles)}. No server-verified role is "
                     f"present for this session -- roles are never read from the client request; "
                     f"establish one at login with `grant role`.")
        )

    def _exec_GrantRoleDecl(self, node, ctx):
        # Establish a SERVER-verified role at login. This is the ONLY thing that puts a
        # role where `require role` can see it -- the client `_roles` payload is never
        # trusted anymore. The role value is resolved at runtime (a literal "admin", or a
        # looked-up field like user.role), then written onto the SESSION ROOT so it
        # survives across requests via the session store, and marked verified.
        val = self._eval(node.value, ctx)
        if isinstance(val, MohioValue):
            val = val.to_python()
        if isinstance(val, (list, tuple)):
            new_roles = [str(r) for r in val if r is not None and str(r) != '']
        elif val is None or val == '':
            new_roles = []
        else:
            new_roles = [str(val)]
        if not new_roles:
            # Fail loud at the source: a login that grants an empty role would silently
            # leave the session with no authority, surfacing later as a confusing 403.
            raise _Raise(
                error_name='authorization_error',
                message=("grant role: the role value resolved to empty -- refusing to establish "
                         "an empty role. A login must grant a concrete role name."))
        # Locate the per-session boundary so the grant persists across requests; never
        # write past it into the shared base. Stateless `run` has no session root, so the
        # grant falls back to this request's context (single execution, no persistence).
        target = ctx
        walk = ctx
        while walk is not None:
            if getattr(walk, '_session_root', False):
                target = walk
                break
            walk = walk._parent

        # Session rotation, 2026-08-04 ruling: fire ONLY on an actual privilege-level
        # CHANGE, never on every call to this statement. Zork calls `grant role "player"`
        # on every single request purely to satisfy the `require role` check that
        # immediately follows it -- an idempotent re-assertion of an already-held role,
        # not a login event. Rotating on that literal reading of the brief would churn the
        # session identity every command and risk real data loss: a request racing the
        # rotation with the previous cookie would be treated as invalidated and silently
        # handed a fresh, empty session (found by checking Zork's actual usage before
        # building, not assumed from the brief's wording).
        _in_real_session = getattr(target, '_session_root', False) and hasattr(target, '_sessions_store')
        _is_real_change = sorted(new_roles) != sorted(getattr(target, '_roles', None) or [])
        if _in_real_session and _is_real_change:
            self._rotate_session(target)

        # REPLACE, not merge: a grant reflects the session's CURRENT roles. Merging would let a
        # re-login as a lower role on the same session silently keep a privilege from an earlier
        # login. Grant several roles at once with a list value (grant role <list>).
        target.set_roles(list(new_roles), verified=True)
        # Audit the privilege grant -- a role change is a security-relevant event, recorded the
        # same way as data mutations and governance refusals. _audit_event stamps + hash-chains
        # to the durable trail when an audit sink is configured, and no-ops safely (in-memory
        # only) when none is; it never breaks the grant.
        _evt = self._audit_event('security_audit_log', {
            'event': 'role_granted',
            'roles': list(new_roles),
        }, ctx)
        self._audit_logs.setdefault('security_audit_log', []).append(_evt)
        return None

    # Serializes session-store mutation across concurrent requests. mio serve threads at
    # least one route (the root GET) via asyncio.to_thread while the other two call
    # dispatch() directly in the event loop -- confirmed by reading the actual server
    # wiring, not assumed -- so a threaded and a non-threaded request CAN genuinely race
    # on the SAME session store. Mirrors _AUDIT_CHAIN_LOCK exactly (an RLock so a caller
    # already holding it, like _rotate_session, can call a sub-primitive that also
    # acquires it without deadlocking).
    _SESSIONS_LOCK = __import__('threading').RLock()

    def _rotate_session(self, session_ctx):
        """Mint a new session ID, re-key the SAME Context object under it, invalidate the
        old ID so it can never be resurrected, and update session.id on ctx so the REST of
        the current request sees the new identity too. Only ever called for a genuine
        privilege change (see _exec_GrantRoleDecl) -- a re-assertion of an already-held
        role never reaches here.

        Nothing is copied: re-keying the live object is what makes every session-scoped
        variable (any hold-in-session-mode value, anything else set on this ctx) carry
        forward intact, with no field-by-field copy logic that could silently drop one.
        Under the in-memory store this stays a pure re-key of the live Context object,
        byte-identical to before the session-store seam existed. A durable-store backend
        (e.g. Postgres) instead writes the state under new_id and removes old_id -- still
        "nothing copied" from the caller's perspective (the same live session_ctx keeps
        being mutated in place for the rest of this request either way).
        """
        import uuid as _uuid
        store = session_ctx._sessions_store
        old_id = session_ctx._session_id
        new_id = _uuid.uuid4().hex
        with MohioInterpreter._SESSIONS_LOCK:
            store.put(new_id, session_ctx)
            store.delete(old_id)
            store.mark_invalidated(old_id)
        session_ctx._session_id = new_id
        session_ctx.set('session', MohioValue({'id': new_id}, 'shape'))
        # A rotation is a security-relevant state change, same footing as role_granted --
        # but the session IDs themselves are credentials, so only the event and the
        # resulting roles are named, never the old or new ID (matching this project's own
        # audit discipline: names/classifications in the trail, never the sensitive value).
        _evt = self._audit_event('security_audit_log', {
            'event': 'session_rotated',
            'reason': 'role_change',
            'roles': list(getattr(session_ctx, '_roles', None) or []),
        }, session_ctx)
        self._audit_logs.setdefault('security_audit_log', []).append(_evt)

    def _invalidate_session(self, store, session_id):
        """Remove a session and permanently block its ID from being resurrected. The one
        shared primitive rotation and expiry both call -- built once, per the ruling,
        not duplicated per caller. Reentrant-safe to call while already holding
        _SESSIONS_LOCK (rotation does, via the put/delete/mark_invalidated above; expiry
        acquires it itself). mark_invalidated is ruled durable (2026-08-05): losing the
        blocklist on restart reopens the fixation risk rotation exists to close, so a
        Postgres-backed store never drops this row the way it drops the session's own."""
        with MohioInterpreter._SESSIONS_LOCK:
            store.delete(session_id)
            store.mark_invalidated(session_id)

    def _session_timeout_ceilings(self, ctx):
        """(idle_seconds, absolute_seconds) -- the runtime default (2026-08-04 ruling: 30
        min idle / 12 hours absolute, both env-overridable), TIGHTENED, never loosened, by
        an active sector's `expire all [session_idle/session_absolute] after N unit`
        declaration. Mirrors get_confidence_floor's own raise-only precedent exactly. The
        `expire all [...] after N unit` syntax and ExpireRule parsing already existed
        (mohio_sector_loader.py, sibling to the working `retain all [...] for N unit`) --
        parsed but never once consumed by the interpreter until now. One computation,
        shared by the lazy expiry check and the Max-Age emitted on the cookie, so they can
        never disagree with each other the way the two original hardcoded model defaults
        did (2026-08-04, same day, different bug)."""
        idle = _SESSION_IDLE_TIMEOUT_SECONDS
        absolute = _SESSION_ABSOLUTE_TIMEOUT_SECONDS
        profile = getattr(ctx, '_sector_profile', None)
        for rule in (getattr(profile, 'expire_rules', None) or []):
            secs = _duration_rule_to_seconds(rule.duration, rule.unit)
            if secs is None:
                continue
            if rule.classification == 'session_idle' and secs < idle:
                idle = secs
            elif rule.classification == 'session_absolute' and secs < absolute:
                absolute = secs
        return idle, absolute

    def _session_is_expired(self, session_ctx, now, idle_ceiling, absolute_ceiling):
        return _is_session_expired(getattr(session_ctx, '_created_at', None),
                                    getattr(session_ctx, '_last_accessed', None),
                                    now, idle_ceiling, absolute_ceiling)

    def _opportunistic_expiry_sweep(self, store, now, idle_ceiling, absolute_ceiling):
        """A bounded scan for OTHER stale sessions, piggybacked on an ordinary request
        instead of a dedicated background task. This project has hit exactly this shape of
        concurrency bug before with an independent background thread touching shared state
        (the audit-chain DDL race, the duplicate-column flake) -- folding the sweep into a
        request that already holds _SESSIONS_LOCK avoids introducing a new one. Runs every
        Nth request (MOHIO_SESSION_SWEEP_INTERVAL, default 20), not every one, so a busy
        app doesn't pay a full dict scan (in-memory) or a full table scan (Postgres, though
        there sweep_expired pushes the comparison into an indexed-eligible SQL WHERE clause
        rather than hydrating every row) on every single request. __base__ and
        __invalidated__ are no longer special dict keys living alongside real sessions --
        they are not part of the store at all (see _InMemorySessionStore /
        _PostgresSessionStore), so store.sweep_expired() can never sweep either by
        accident, no exclusion list needed here."""
        interval = int(os.environ.get("MOHIO_SESSION_SWEEP_INTERVAL", "20") or "20")
        self._session_sweep_counter = getattr(self, '_session_sweep_counter', 0) + 1
        if interval <= 0 or self._session_sweep_counter % interval != 0:
            return
        with MohioInterpreter._SESSIONS_LOCK:
            freed = store.sweep_expired(now, idle_ceiling, absolute_ceiling)
            for sid in freed:
                store.mark_invalidated(sid)

    def _session_cookie_opts(self, sid, idle_ceiling, absolute_ceiling, created_at, now):
        """Cookie options for the runtime-owned session cookie. Max-Age reflects whichever
        timeout is SHORTER, computed fresh every response, so the browser stops sending a
        cookie the server would reject anyway (2026-08-04 brief: a mismatch here is its own
        small bug class)."""
        absolute_remaining = max(0.0, absolute_ceiling - (now - (created_at if created_at is not None else now)))
        max_age = int(min(idle_ceiling, absolute_remaining))
        return {
            'value': str(sid),
            'http_only': True,
            'same_site': 'Lax',
            'expires': max_age,
            # 'secure' deliberately omitted -- the server's own scheme-based
            # _secure_default decides (Secure on https), matching _exec_MioCookieSet's
            # established default exactly rather than a second, possibly-diverging rule.
        }

    def _expand_program_includes(self, program):
        """Splice included files' top-level statements into the program, once.
        Cached on the program object so a served program expands a single time."""
        if getattr(program, '_includes_expanded', False):
            return
        base = getattr(self, '_include_base_dir', None) or os.getcwd()
        try:
            program.statements = self._expand_includes(
                list(getattr(program, 'statements', [])), base, set())
            program._includes_expanded = True
        except AttributeError:
            pass

    def _expand_includes(self, stmts, base_dir, seen):
        from mohio_ast import IncludeDecl
        out = []
        for stmt in stmts:
            if isinstance(stmt, IncludeDecl):
                rel = stmt.path
                abspath = os.path.abspath(os.path.join(base_dir, rel))
                if abspath in seen:
                    chain = " -> ".join(list(seen) + [abspath])
                    raise MohioRuntimeError(
                        f"include forms a cycle: {chain}. A file cannot include "
                        f"itself, directly or indirectly.")
                if not os.path.exists(abspath):
                    raise MohioRuntimeError(
                        f"include cannot find '{rel}' (looked in {base_dir}). The "
                        f"path is resolved relative to the including file.")
                inc = _load_include_statements(abspath)
                out.extend(self._expand_includes(
                    inc, os.path.dirname(abspath), seen | {abspath}))
            else:
                out.append(stmt)
        return out

    def _exec_IncludeDecl(self, node, ctx):
        # Includes are normally expanded at program load. If one reaches execution
        # (a path that bypasses _expand_program_includes), resolve and run it here
        # rather than silently skipping it.
        base = getattr(self, '_include_base_dir', None) or os.getcwd()
        abspath = os.path.abspath(os.path.join(base, node.path))
        if not os.path.exists(abspath):
            raise MohioRuntimeError(
                f"include cannot find '{node.path}' (looked in {base}).")
        for stmt in _load_include_statements(abspath):
            self._exec(stmt, ctx)
    def _exec_TimespanDecl(self, node, ctx):
        # Declarative: record a named time window so it can be referenced later
        # (e.g. by a query). It defines a window; it does not act on its own.
        if not hasattr(self, '_timespans') or self._timespans is None:
            self._timespans = {}
        self._timespans[node.name] = node
        if getattr(self, 'verbose', False):
            print(f"  [timespan] '{node.name}' declared")
        return None
    def _exec_ReleaseStmt(self, node, ctx):
        # release removes a binding PROTECTION from a name, keeping the value:
        #   held  -> unfrozen (a `hold` value becomes freely changeable)
        #   typed -> bare     (a type contract `x as int` is dropped; value kept, now malleable)
        # A `lock` is permanent and cannot be released. release.now unfreezes/re-types-off and
        # reassigns in one step. This is one lever with a consistent meaning across hold and type
        # -- part of the variable state machine (release / clear / forget / rename / replace).
        name = node.name
        if ctx.is_locked(name):
            raise MohioRuntimeError(
                f"release: '{name}' is locked, and a lock is permanent -- it cannot "
                f"be released. Use `hold` if you need releasable protection.")
        _was_held  = ctx.is_held(name)
        _was_typed = ctx.typed_of(name) is not None
        if not _was_held and not _was_typed:
            raise MohioRuntimeError(
                f"release: '{name}' has no protection to release -- it is neither held "
                f"nor under a type contract (a bare variable is already changeable).")
        if _was_held:
            ctx.unhold_name(name)
        if _was_typed:
            ctx.untype_name(name)          # drop the type contract, keep the value
        if getattr(node, 'variant', '') == 'release.now' and node.value is not None:
            val = self._eval(node.value, ctx)
            ctx.set(name, val, immutable=False)
        if self.verbose: print(f"  [release] {name}"
                               f"{' (unheld)' if _was_held else ''}"
                               f"{' (untyped)' if _was_typed else ''}")
        return None
    def _exec_VarStateStmt(self, node, ctx):
        # The variable state-change operators. Each fails loud rather than silently no-opping -- a
        # forget/clear/rename/replace on a name that isn't there is a bug, not a no-op.
        #   clear   -> empty the VALUE, keep the name + type contract (typed -> type-zero)
        #   forget  -> remove the name ENTIRELY (value + hold/lock + contract)
        #   rename  -> change the NAME, carry the value + hold/lock + contract to the new name
        #   replace -> swap the VALUE (must satisfy any type contract, like a normal assignment)
        name = node.name
        op = node.op
        # A `lock` is permanent: it cannot be cleared, renamed, replaced, or forgotten. All four
        # operators refuse a locked name consistently -- a lock you could clear or forget would not
        # be a lock. (release also refuses a lock, in _exec_ReleaseStmt.)
        if ctx.is_locked(name):
            raise MohioRuntimeError(
                f"{op}: '{name}' is locked, and a lock is permanent -- it cannot be "
                f"{'cleared' if op == 'clear' else 'renamed' if op == 'rename' else 'forgotten' if op == 'forget' else 'replaced'}. "
                f"Use `hold` instead of `lock` if you need a value you can later change.")
        if op == 'clear':
            if not ctx.exists(name):
                raise MohioRuntimeError(
                    f"clear: '{name}' does not exist -- nothing to clear.")
            tn = ctx.typed_of(name)
            if tn:
                _base = tn.split('.')[0]
                _zero = (0 if _base in ('int', 'integer')
                         else 0.0 if _base in ('dec', 'decimal')
                         else False if _base in ('boolean', 'bool')
                         else "")
                _kind = ('number' if _base in ('int', 'integer')
                         else 'decimal' if _base in ('dec', 'decimal')
                         else 'boolean' if _base in ('boolean', 'bool') else 'text')
                ctx.set(name, MohioValue(_zero, _kind))
            else:
                ctx.set(name, MohioValue(None, 'null'))
            if self.verbose: print(f"  [clear] {name}")
            return None
        if op == 'forget':
            if not ctx.delete_var(name):
                raise MohioRuntimeError(
                    f"forget: '{name}' does not exist -- nothing to forget.")
            if self.verbose: print(f"  [forget] {name}")
            return None
        if op == 'rename':
            target = node.target
            if not ctx.exists(name):
                raise MohioRuntimeError(
                    f"rename: '{name}' does not exist -- nothing to rename.")
            if ctx.exists(target):
                raise MohioRuntimeError(
                    f"rename: '{target}' already exists -- forget it first, or choose "
                    f"another name (rename never overwrites).")
            cur = ctx.get(name)
            was_held = ctx.is_held(name)
            was_locked = ctx.is_locked(name)
            tn = ctx.typed_of(name)
            ctx.delete_var(name)
            ctx.set(target, cur)
            if tn: ctx.declare_type(target, tn)
            if was_held: ctx.hold_name(target)
            if was_locked: ctx.lock_name(target)
            if self.verbose: print(f"  [rename] {name} -> {target}")
            return None
        if op == 'replace':
            if not ctx.exists(name):
                raise MohioRuntimeError(
                    f"replace: '{name}' does not exist -- declare it first "
                    f"(replace swaps the value of an existing variable).")
            new_val = self._eval(node.value, ctx)
            new_py = new_val.to_python() if isinstance(new_val, MohioValue) else new_val
            tn = ctx.typed_of(name)
            if tn and not self._value_matches_type(new_py, tn):
                raise MohioRuntimeError(
                    f"replace: '{name}' is declared as {tn}, but the replacement {new_py!r} "
                    f"does not satisfy that type. Cast it, or release the contract first.")
            ctx.set(name, new_val)
            if self.verbose: print(f"  [replace] {name}")
            return None
        raise MohioRuntimeError(f"unknown variable state operation: {op!r}")

    def _exec_LoadPackDecl(self, node, ctx):    raise MohioRuntimeError("load pack is declared but not yet executable in this build (the pack would silently not load). Tracked for a future release.")
    def _exec_PatternDecl(self, node, ctx):
        raise MohioRuntimeError(
            "pattern is declared but not yet executable in this build (it would silently "
            "match nothing). The declarative matcher (starts.with / digits / one of / ...) "
            "is tracked for implementation -- it will back validate.")
    def _exec_MiomapDecl(self, node, ctx):
        raise MohioRuntimeError(
            "miomap is declared but not yet executable in this build (it would silently "
            "map nothing). Shape-to-shape field mapping (from/to/fields with transforms) "
            "is tracked for implementation.")

    # ── mioschedule ───────────────────────────────────────────────────────
    def _schedule_tasks(self, node):
        """Extract the task name(s) a schedule runs from its body clauses.

        Body clauses arrive as lists (from mioschedule_body). A `run NAME`
        clause is a bare NAME token; a run_block / RunBlock names one or more
        tasks. Cadence clauses (DurationExpr, time/weekday) and modifiers are
        not tasks. (Precise cadence parsing is deferred until time_unit captures
        its unit — see the wiring audit; the on-demand trigger does not need it.)
        """
        from lark import Token
        tasks = []
        for clause in (node.body or []):
            items = clause if isinstance(clause, list) else [clause]
            for it in items:
                tname = type(it).__name__
                if isinstance(it, Token) and it.type == 'NAME':
                    tasks.append(str(it))
                elif tname in ('RunBlock', 'CallBlock'):
                    nm = getattr(it, 'task_name', None) or getattr(it, 'name', None)
                    if nm:
                        tasks.append(str(nm))
        return tasks

    def _exec_MioScheduleDecl(self, node, ctx):
        """Register a named schedule. Declaring does NOT auto-fire (stateless
        compute can't self-wake); it records the schedule so `run
        mioschedule.NAME now` can trigger it on demand and an external driver
        (cron / worker hitting `mio schedule run-due`) can fire it on cadence.
        """
        name = str(getattr(node, 'name', '') or '')
        if not name:
            return None
        self._schedules[name] = {
            'tasks':     self._schedule_tasks(node),
            'raw':       node.body,
            'last_fired': None,
        }
        if self.verbose:
            print(f"  [mioschedule] registered '{name}' "
                  f"-> tasks={self._schedules[name]['tasks']}")
        return None

    def _fire_schedule(self, name, ctx):
        """Run the task(s) a registered schedule points at, in order."""
        sched = self._schedules.get(name)
        if sched is None:
            raise MohioRuntimeError(
                f"run mioschedule.{name}: no schedule named '{name}' is "
                f"registered. Declare it with 'mioschedule {name} / ... / run "
                f"<task> / mioschedule: done' before triggering it.")
        last = None
        for tname in sched['tasks']:
            task = ctx.get_task(tname) if ctx else None
            if task is None:
                raise MohioRuntimeError(
                    f"mioschedule '{name}' refers to task '{tname}', which is "
                    f"not defined.")
            child = ctx.child()
            try:
                last = self._exec_block(task.body, child)
            except _GiveBack as gb:
                last = gb.value if hasattr(gb, 'value') else None
        import datetime as _dt
        sched['last_fired'] = _dt.datetime.utcnow().isoformat()
        return last

    def _exec_RunSchedule(self, node, ctx):
        """`run mioschedule.NAME now|immediately` — fire the schedule's task(s)
        immediately."""
        name = str(getattr(node, 'name', '') or '')
        return self._fire_schedule(name, ctx)

    def run_due_schedules(self, ctx=None):
        """Fire every registered schedule once. Intended to be called by an
        external driver (cron / worker / dev ticker) at the cadence the operator
        chooses. Returns the list of schedule names fired.

        NOTE: this fires ALL registered schedules each call (driver-paced).
        Per-schedule cadence ('every 5 minutes' vs 'every day') needs the
        time_unit unit-capture fix before run-due can honour individual
        intervals — tracked in the wiring audit. For now, set your driver's
        frequency to the cadence you want, or use one driver per cadence.
        """
        if ctx is None:
            ctx = Context()
        fired = []
        for name in list(self._schedules.keys()):
            try:
                self._fire_schedule(name, ctx)
                fired.append(name)
            except Exception as e:
                if self.verbose:
                    print(f"  [mioschedule] '{name}' failed: {e}")
        return fired
    def _exec_MioconnectDecl(self, node, ctx):
        """Register an external connector. mioconnect compiles to miohttp at call
        time: this stores the address, auth, and operations; env credentials are
        resolved when an operation actually fires (fail loud at the point of use)."""
        if not hasattr(self, '_connectors'):
            self._connectors = {}
        ops = {}
        for op in (node.operations or []):
            ops[op.name] = {
                'path':    op.path or "",
                'method':  (op.method or "POST").upper(),
                'sends':   op.sends_shape,
                'returns': op.returns_shape,
            }
        record = {
            'name':       node.name,
            'address':    node.address,      # value node, eval at call time
            'source':     node.source,       # `from env.X` shorthand credential
            'auth_type':  node.auth_type or "",
            'auth_value': node.auth_value,   # value node, eval at call time
            'auth_value2': node.auth_value2,         # basic password
            'auth_header_name': node.auth_header_name,  # custom header name
            'operations': ops,
            'timeout':    node.timeout,
        }
        self._connectors[node.name] = record
        if node.alias:
            self._connectors[node.alias] = record
        if self.verbose:
            extra = f" (alias {node.alias})" if node.alias else ""
            print(f"  [mioconnect] registered {node.name}{extra} — {len(ops)} operation(s)")

    def _exec_MioconnectCall(self, node, ctx):
        """Invoke a registered connector operation: Connector.op with payload as result.
        Compiles to an HTTP call (address + op path, op method, env-resolved auth,
        payload as JSON body); binds the response shape to `result`. Fails loud on an
        unknown connector/operation or a missing credential. Counts toward the agent
        boundary gate via the external-call counter."""
        import urllib.request, urllib.error, json as _json
        registry = getattr(self, '_connectors', {})
        conn = registry.get(node.connector)
        if conn is None:
            # `starts with` / `ends with` WORK as conditions (e.g. `when name starts with "A"`,
            # verified by running). But in THIS position they were read as a connector call, which
            # is a misparse. The dotted `starts.with` / `ends.with` is the canonical, unambiguous
            # form and parses correctly everywhere -- point at it (NOT a retirement: the space form
            # is not retired, it just misparsed here).
            _cond = {'starts': 'starts.with', 'ends': 'ends.with'}
            if node.connector in _cond:
                raise MohioRuntimeError(
                    f"`{node.connector} with` was read as a connector call here (a misparse). Use "
                    f"the dotted `{_cond[node.connector]}` -- the canonical, unambiguous form that "
                    f"parses correctly in every position (e.g. `when name {_cond[node.connector]} "
                    f"\"A\"`).")
            raise MohioRuntimeError(
                f"mioconnect: no connector named '{node.connector}'. "
                f"Declare it with `mioconnect {node.connector} ... mioconnect: done` before calling it.")
        op = conn['operations'].get(node.operation)
        if op is None:
            known = ", ".join(conn['operations'].keys()) or "(none)"
            raise MohioRuntimeError(
                f"mioconnect: connector '{node.connector}' has no operation '{node.operation}'. "
                f"Known operations: {known}.")

        # ── Sector-operation governance (the ceiling over any grant) ──────────
        # A certified/official sector profile may forbid or review-gate an
        # operation. This is checked before credential resolution, the boundary
        # gate, and the wire, so a forbidden call never runs, never resolves a
        # credential, and never counts against an agent's budget. It applies to
        # every connector call: a direct one fails loud with the refusal; an agent
        # tool call is caught by the agent loop and routed to its fallback. A grant
        # cannot widen past this ceiling -- the sector answers "is this permitted at
        # all," which no developer grant can override.
        _sector = getattr(ctx, '_sector_profile', None)
        if _sector is not None and getattr(_sector, 'operation_rules', None):
            # If any rule governs by data classification (`any operation touching
            # [pci]`), inspect which classifications this call's payload touches.
            # Only done when such a rule exists, so name-only profiles pay nothing.
            _touched = None
            if any(r.get('data_class') for r in _sector.operation_rules):
                _touched = set()
                try:
                    if node.payload is not None:
                        _pv = self._eval(node.payload, ctx)
                        _bp = _pv.to_python() if isinstance(_pv, MohioValue) else _pv
                        if isinstance(_bp, dict):
                            for _k in _bp.keys():
                                for _cls in _sector.get_field_classifications(str(_k)):
                                    _touched.add(str(_cls).lower())
                except Exception:
                    _touched = set()  # cannot inspect -> data-class rules just don't match
            verdict, reason, audit_log = _sector.get_operation_verdict(
                node.connector, node.operation, touched_classes=_touched)
            if verdict in ('forbidden', 'review'):
                ename = ('sector_forbids_operation' if verdict == 'forbidden'
                         else 'sector_requires_review')
                _log = audit_log or 'operation_audit_log'
                _evt = {
                    'event':     'OPERATION_REFUSED',
                    'verdict':   verdict,
                    'connector': node.connector,
                    'operation': node.operation,
                    'sector':    getattr(_sector, 'name', ''),
                    'reason':    reason,
                }
                if _touched:
                    # classification names only, never any field value
                    _evt['data_classes'] = sorted(_touched)
                _entry = self._audit_event(_log, _evt, ctx)
                self._audit_logs.setdefault(_log, []).append(_entry)
                phrase = ('forbidden' if verdict == 'forbidden'
                          else 'gated for human review')
                detail = reason or (
                    f"{node.connector}.{node.operation} is {phrase} under this profile.")
                raise _Raise(
                    error_name=ename,
                    message=(f"{node.connector}.{node.operation} is {phrase} under the "
                             f"'{getattr(_sector, 'name', '')}' sector profile. {detail}"),
                    line=getattr(node, 'line', None),
                    hint=("This is a sector ceiling and a grant cannot override it. "
                          "Remove the operation, or run under a profile that permits it."))

        # URL = address + operation path
        if conn.get('address') is None:
            raise MohioRuntimeError(
                f"mioconnect: connector '{node.connector}' has no address. "
                f"Add `address \"https://...\"` to its declaration.")
        addr_v = self._eval(conn['address'], ctx)
        base = str(addr_v.to_python() if isinstance(addr_v, MohioValue) else addr_v).rstrip("/")
        path = op.get('path') or ""
        url  = base + (path if path.startswith("/") or not path else "/" + path)
        method = (op.get('method') or "POST").upper()

        req_headers = {"User-Agent": "Mohio/4.0", "Accept": "application/json"}

        # auth — env-resolved at call time; fail loud if the credential is empty
        if conn.get('auth_value') is not None:
            av = self._eval(conn['auth_value'], ctx)
            cred = str(av.to_python() if isinstance(av, MohioValue) else av)
            if not cred or cred in ("None", ""):
                raise MohioRuntimeError(
                    f"mioconnect: connector '{node.connector}' credential resolved empty "
                    f"(check the env var in its `auth` line).")
            atype = (conn.get('auth_type') or "bearer").lower()
            if atype == "header":
                req_headers[conn.get('auth_header_name') or "Authorization"] = cred
            elif atype == "basic":
                import base64
                pw = ""
                if conn.get('auth_value2') is not None:
                    pv2 = self._eval(conn['auth_value2'], ctx)
                    pw = str(pv2.to_python() if isinstance(pv2, MohioValue) else pv2)
                token = base64.b64encode(f"{cred}:{pw}".encode()).decode()
                req_headers["Authorization"] = f"Basic {token}"
            else:  # bearer / key
                req_headers["Authorization"] = cred if cred.lower().startswith("bearer ") else f"Bearer {cred}"

        # payload -> JSON body (never on GET/HEAD: those carry no body)
        body_bytes = None
        if node.payload is not None and method not in ("GET", "HEAD"):
            pv = self._eval(node.payload, ctx)
            bp = pv.to_python() if isinstance(pv, MohioValue) else pv
            if isinstance(bp, (dict, list)):
                body_bytes = _json.dumps(bp).encode()
                req_headers.setdefault("Content-Type", "application/json")
            elif isinstance(bp, str):
                body_bytes = bp.encode()
                req_headers.setdefault("Content-Type", "text/plain")

        # boundary-gate accounting: every connector call is an external, side-effecting
        # operation. The counter is the hook the ai.agent gate reads to bound tool use.
        self._external_calls = getattr(self, '_external_calls', 0) + 1
        # When this fires inside an ai.agent, count it against that agent's external-call
        # ceiling. The frame is on the interpreter stack, so agent-authored code cannot
        # reset it. Over budget -> the deterministic boundary gate intercepts.
        _stack = getattr(self, '_agent_gate_stack', None)
        if _stack:
            _frame = _stack[-1]
            _frame['external_calls'] += 1
            if _frame['max_calls'] and _frame['external_calls'] > _frame['max_calls']:
                raise _AgentLimitExceeded(
                    "Maximum external calls reached",
                    metric="calls",
                    value=_frame['external_calls'],
                    ceiling=_frame['max_calls'],
                )

        if self.verbose:
            print(f"  [mioconnect] {node.connector}.{node.operation} -> {method} {url[:60]}")

        # ── chain of custody ──────────────────────────────────────────────────────
        # A boundary crossing is recorded as a FACT, the way a handoff is recorded in
        # evidence handling: data left here, at this time, bound for this destination.
        # Until now only REFUSED crossings were recorded, so a permitted one -- the
        # ordinary case -- left no trace, and a chain could look complete while data
        # had flowed out of it.
        #
        # The record is deliberately modest, and that is what makes it defensible:
        #   * WHAT MOHIO ATTESTS: it sent a request to a named destination at a time,
        #     and separately that a response to THAT request arrived with a status.
        #   * WHAT IT DOES NOT: what the destination did with the data, whether it
        #     stored or forwarded it, or that any particular record came back. Mohio
        #     holds an HTTP request/response pair, not custody of the data's fate.
        # Hence `boundary_send` and `boundary_response`, never "returned" -- a reviewer
        # reads "returned" as "the same record came back verified", which is a stronger
        # claim than this system can support anywhere.
        #
        # Values NEVER enter the record. Copying PII into an append-only trail to prove
        # PII was handled carefully creates a second, longer-lived copy of it. Field
        # CLASS names only, matching the refusal path above.
        _cc_log = 'data_audit_log'
        try:
            self._audit_scope_statement(_cc_log, ctx)
        except Exception:
            pass
        _cc_dest = base
        try:
            import datetime as _ccdt
            _cc_started = _ccdt.datetime.now(_ccdt.timezone.utc).isoformat()
        except Exception:
            _cc_started = ''
        _cc_evt = {
            'event':       'boundary_send',
            'meaning':     'data left this app for an outside destination',
            'connector':   node.connector,
            'operation':   node.operation,
            'destination': _cc_dest,
            'method':      method,
            'sent_at':     _cc_started,
            'sector':      getattr(locals().get('_sector'), 'name', '') or '',
        }
        # `_touched` and `_sector` only exist when a sector profile was consulted above.
        # A custody record must never depend on that, or the ordinary unregulated call --
        # the most common one -- would crash instead of being recorded.
        _cc_classes = locals().get('_touched') or None
        if _cc_classes:
            _cc_evt['data_classes'] = sorted(_cc_classes)   # names only, never values
        try:
            self._audit_logs.setdefault(_cc_log, []).append(
                self._audit_event(_cc_log, _cc_evt, ctx))
        except Exception:
            pass    # a custody record must never break the call it is recording

        def _cc_response(_status, _error=None):
            """Record that the destination answered. Not that anything 'came back'."""
            _evt = {
                'event':       'boundary_response',
                'meaning':     ('a response to this app\'s request arrived from the '
                                'destination; its contents are not attested here'),
                'connector':   node.connector,
                'operation':   node.operation,
                'destination': _cc_dest,
                'status':      _status,
                'sent_at':     _cc_started,
            }
            if _error:
                _evt['error'] = str(_error)[:200]
            try:
                self._audit_logs.setdefault(_cc_log, []).append(
                    self._audit_event(_cc_log, _evt, ctx))
            except Exception:
                pass

        try:
            req = urllib.request.Request(url, data=body_bytes, headers=req_headers, method=method)
            with _http_open(req, 30, method, url) as resp:   # SSRF: no auto-redirect (S9)
                status   = resp.status
                raw_body = resp.read().decode("utf-8", errors="replace")
                try:    parsed = _json.loads(raw_body)
                except: parsed = raw_body
                result = {"status": status, "ok": 200 <= status < 300,
                          "body": raw_body, "json": parsed, "headers": dict(resp.headers)}
                if self.verbose: print(f"  [mioconnect] → {status}")
                _cc_response(status)
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace") if e.fp else ""
            result = {"status": e.code, "ok": False, "body": raw, "json": None, "headers": {}, "error": str(e)}
            _cc_response(e.code, e)
        except MohioRuntimeError as e:
            # A refused redirect (SSRF guard) fails loud -- audit it, then propagate; never
            # swallow it into a benign-looking result the way the generic handler below would.
            _cc_response(0, e)
            raise
        except Exception as e:
            result = {"status": 0, "ok": False, "body": "", "json": None, "headers": {}, "error": str(e)}
            _cc_response(0, e)

        mv = MohioValue(result, "shape")
        if node.result:
            ctx.set(node.result, mv)
        return mv
    def _exec_MiosearchDecl(self, node, ctx):   raise MohioRuntimeError("miosearch is declared but not yet executable in this build (it would silently do nothing). Tracked for a future release.")
    def _exec_MiotestDecl(self, node, ctx):
        """`miotest "suite"` / `miotest.unit` / `miotest.unit.ai` -- grammar and transformer
        are wired (mohio.lark:1253-1258, mohio_transformer_ast.py:miotest_block), so this
        parses as a real MiotestDecl instead of the old orphan-rule bug (silently declaring a
        variable named miotest). The test-runner itself is not built yet; fail loud by name
        rather than falling through to the generic no-executor message, so this specific
        construct is tracked by tests/test_unbuilt_failloud_backlog.py."""
        raise MohioRuntimeError(
            "miotest is declared but not yet executable in this build (it would silently do "
            "nothing -- no test runner is wired). Tracked for a future release.")
    def _exec_MiovalidateDecl(self, node, ctx):
        """Register a named, reusable validation rule set. Applied later by
        `validate using <name>` or `validate <name> against <data>`."""
        self._validation_rules[node.name] = node.rules
        if self.verbose:
            print(f"  [miovalidate] registered '{node.name}' ({len(node.rules)} rules)")
        return None
    def _exec_MiopdfDecl(self, node, ctx):      raise MohioRuntimeError("miopdf is declared but not yet executable in this build (it would silently do nothing). Tracked for a future release.")
    def _exec_RateLimitDecl(self, node, ctx):   raise MohioRuntimeError("rate limit is not yet enforced in this build. It would silently allow all traffic, so Mohio refuses to run it rather than give false security.")
    def _exec_Closer(self, node, ctx):
        # A closer (e.g. `find: done`) is a pure structural terminator with no
        # runtime semantics. Some block transformers leave it in the body list,
        # so it legitimately flows through _exec_block -- a no-op here is correct,
        # not a silent trap (there is nothing to execute and nothing is lost).
        return None

    # ── Listen / Routing ──────────────────────────────────────

    def _exec_ListenBlock(self, node, ctx):
        req = ctx._current_request
        if not req:
            if self.verbose: print("  [listen] no request — skipping")
            return None

        shape_name = req.get('_shape')
        method     = req.get('_method', 'POST').upper()
        req_path   = req.get('_path')

        def _method_ok(listener):
            if isinstance(listener, NewBlock):
                return method in ('POST', 'NEW', 'PUT')
            if isinstance(listener, RequestInboundBlock):
                return method in ('GET', 'REQUEST')
            return False

        def _norm(p):
            if not p:
                return p
            p = str(p).split('?', 1)[0]            # ignore any query string
            return p[:-1] if len(p) > 1 and p.endswith('/') else p

        def _rerender_with_errors(new_listener):
            # A JSON / API request (not a browser form post) gets structured JSON
            # errors with a 422, naming each field that failed. The HTML re-render
            # below is for form posts, where keeping the typed values in the page
            # matters; an API client needs a parseable, field-named failure instead
            # of a page of markup.
            if not req.get('_form_post'):
                errs = dict(getattr(self, '_form_errors', {}) or {})
                self._form_errors = {}
                return {'status': 422, 'content_type': 'application/json',
                        'body': {'errors': errs}}
            # self._form_errors is set by _exec_new_listener. Re-run the GET view
            # at the same path/shape so the whole page comes back with values kept
            # and errors shown. Fall back to a form-only re-render when this block
            # has no GET view.
            try:
                shp, pth = new_listener.shape, new_listener.path
                view = next((l for l in node.listeners
                             if isinstance(l, RequestInboundBlock)
                             and (l.shape == shp
                                  or (pth and l.path and _norm(l.path) == _norm(pth)))),
                            None)
                if view is not None:
                    return self._exec_request_listener(view, ctx, req)
                ec = ctx.child()
                ec.set(shp[0].lower() + shp[1:], MohioValue(req, 'shape'))
                return {'status': 200, 'content_type': 'text/html',
                        'body': self._render_invalid_form(shp, ec)}
            finally:
                self._form_errors = {}

        def _dispatch(listener):
            if isinstance(listener, NewBlock):
                r = self._exec_new_listener(listener, ctx, req)
                if isinstance(r, dict) and r.get('_revalidate_failed'):
                    return _rerender_with_errors(listener)
                return r
            return self._exec_request_listener(listener, ctx, req)

        candidates = [l for l in node.listeners if _method_ok(l)]
        if not candidates:
            return None

        # 1. PATH is the route key. Match the listener whose `at /path` equals the
        #    request path (trailing slash / query string ignored). When a shape is
        #    also supplied, prefer the path+shape match.
        if req_path is not None:
            rp = _norm(req_path)
            path_hits = [l for l in candidates if l.path is not None and _norm(l.path) == rp]
            if path_hits:
                if shape_name:
                    both = [l for l in path_hits if l.shape == shape_name]
                    if both:
                        return _dispatch(both[0])
                return _dispatch(path_hits[0])

        # 2. SHAPE dispatch — only when path is NOT the routing key here: either the
        #    request pinned no path, or no candidate declares a path. When a path WAS
        #    pinned and path-keyed listeners exist, a non-matching path must not fall
        #    through to a shape match for the wrong route (that would serve a handler
        #    for a path the caller never asked for).
        if shape_name and (req_path is None or not any(l.path for l in candidates)):
            shape_hits = [l for l in candidates if l.shape == shape_name]
            if shape_hits:
                return _dispatch(shape_hits[0])

        # 3. Single-endpoint fallback: one route for this method, so use it even if
        #    the caller didn't pin a path/shape (keeps simple one-page apps working).
        #    But only when no path was pinned, or the lone route declares no path --
        #    a pinned path that doesn't match the lone route's path is a 404, not a
        #    fallback to the wrong page.
        if len(candidates) == 1 and (req_path is None or candidates[0].path is None):
            return _dispatch(candidates[0])

        # 4. No path/shape pinned and several routes exist, or a path was given that
        #    matches nothing -> no route. A clear 404 (never a silently-wrong page,
        #    and never a bare None that crashes the caller).
        if req_path is not None:
            return {'status': 404, '_no_route': True, 'body': f"No route matches {method} {req_path}"}
        return None

    def _exec_new_listener(self, node, ctx, req):
        # Session mode: write directly to session context so state persists.
        # Stateless mode: use a child so requests don't bleed into each other.
        if getattr(self, '_session_mode', False):
            exec_ctx = ctx   # write directly to session — persists between requests
        else:
            exec_ctx = ctx.child()

        shape_var = node.shape[0].lower() + node.shape[1:]
        exec_ctx.set(shape_var, MohioValue(req, 'shape'))
        for k, v in req.items():
            if not k.startswith('_'):
                exec_ctx.set(k, MohioValue(v))

        # Shape-gate validation: enforce the shape's own rules (required, email,
        # allowed) before the handler runs. On failure, re-render the form with the
        # submitted values kept and one small error per field -- no blank restart,
        # and the handler never runs on bad input. Shapes with no such rules pass
        # straight through, so behavior is unchanged unless a rule is declared.
        # Guard verify: only for real browser form posts (the server marks them
        # with _form_post). Honeypot first, then a signed, unexpired CSRF token.
        # API/JSON submits carry no guard and pass through untouched.
        if req.get('_form_post'):
            if req.get('_trap') not in (None, ''):
                return {'status': 400, 'body': 'Submission rejected.',
                        'content_type': 'text/plain'}
            if not self._verify_csrf(req.get('_csrf')):
                return {'status': 403,
                        'body': 'Invalid or expired form token. Please reload and try again.',
                        'content_type': 'text/plain'}

        shape = exec_ctx.get_shape(node.shape)
        if shape is not None and getattr(shape, 'fields', None):
            _inst = exec_ctx.get(shape_var)
            _data = _inst.to_python() if isinstance(_inst, MohioValue) else _inst
            if not isinstance(_data, dict):
                _data = {}
            errors = self._validate_against_shape(shape, lambda fn: _data.get(fn))
            # The shape DECLARES the type, so the boundary is where text becomes a number.
            # Without this, `price as decimal` still arrived as the text "10.50" and
            # `(price + tax)` blew up in the math instead of being 12.50. A value that
            # cannot convert is a validation error (422), not a 500 deep in an expression.
            for _f in (getattr(shape, 'fields', None) or []):
                _p = self._shape_field_props(_f)
                _fn, _ft = _p['name'], (_p.get('type') or '').lower()
                if _ft not in ('number', 'num', 'decimal', 'dec', 'integer', 'int'):
                    continue
                if _fn in errors or _fn not in _data:
                    continue
                _raw = _data[_fn]
                _raw = _raw.to_python() if isinstance(_raw, MohioValue) else _raw
                if isinstance(_raw, (int, float)) and not isinstance(_raw, bool):
                    continue                      # already a number
                _s = '' if _raw is None else str(_raw).strip()
                if _s == '' or _s.lower() in ('none', 'null'):
                    _num = 0                      # empty means zero, same as the cast
                else:
                    try:
                        _num = float(_s)
                    except ValueError:
                        _disp = _p['label'] or _fn.replace('_', ' ').title()
                        errors[_fn] = (_p.get('error')
                                       or f'{_disp} must be a number. Got "{_s}".')
                        continue
                    if _ft in ('integer', 'int'):
                        _num = int(round(_num))
                    elif _num == int(_num) and _ft in ('number', 'num'):
                        _num = int(_num)
                _data[_fn] = _num
                exec_ctx.set(_fn, MohioValue(_num))
                # `request` is bound separately from the shape var, so the coerced value
                # has to land there too or `request.price` still reads the raw text.
                _rq = exec_ctx.get('request') if exec_ctx.exists('request') else None
                _rqd = _rq.to_python() if isinstance(_rq, MohioValue) else _rq
                if isinstance(_rqd, dict):
                    _rqd[_fn] = _num
                    exec_ctx.set('request', MohioValue(_rqd, 'shape'))
            if not errors:
                exec_ctx.set(shape_var, MohioValue(_data, 'shape'))
            if errors:
                # Signal the dispatcher to re-render with errors. It re-runs the
                # GET view for this route when one exists (full page, chrome
                # intact), else falls back to a form-only re-render. Either way the
                # handler never runs and the typed values are kept.
                self._form_errors = errors
                return {'_revalidate_failed': True, 'shape': node.shape,
                        'path': node.path}
            # Validation passed: persist any uploaded files and rebind each field
            # to its stored path so the handler saves the path, not raw bytes.
            stored = self._store_uploads(shape, _data)
            for fn, path in stored.items():
                _data[fn] = path
                exec_ctx.set(fn, MohioValue(path))
            if stored:
                exec_ctx.set(shape_var, MohioValue(_data, 'shape'))

        try:
            result = self._exec_block(node.body, exec_ctx)
        except _GiveBack as gb:
            self._debug_trace(exec_ctx if 'exec_ctx' in dir() else ctx,
                f"give back resolved at route handler")
            return self._format_response(gb)
        return self._format_page_result(result, exec_ctx)

    def _bubble_pending_cookies(self, child, parent):
        """miocookie.set writes `__pending_cookies__` onto the ctx it runs in. A route handler
        runs in a CHILD ctx, so lift any pending cookies up to the parent (the session ctx) where
        `_attach_cookies` reads them -- otherwise Set-Cookie is silently dropped on this serving
        path while working on the `new sh.X` path (2026-08-01 fix; both paths now behave alike)."""
        pend = child.get('__pending_cookies__')
        pd = pend.to_python() if isinstance(pend, MohioValue) else pend
        if isinstance(pd, dict) and pd:
            parent.set('__pending_cookies__', MohioValue(dict(pd), 'shape'))

    def _exec_request_listener(self, node, ctx, req):
        child = ctx.child()
        shape_var = node.shape[0].lower() + node.shape[1:]
        child.set(shape_var, MohioValue(req, 'shape'))
        for k, v in req.items():
            if not k.startswith('_'):
                child.set(k, MohioValue(v))
        try:
            result = self._exec_block(node.body, child)
        except _GiveBack as gb:
            return self._format_response(gb)
        finally:
            # Runs on BOTH exits (the give-back return above and the normal return below) BEFORE
            # control leaves, so the pending cookies reach the session ctx that _attach_cookies
            # reads after _exec_listeners returns.
            self._bubble_pending_cookies(child, ctx)
        return self._format_page_result(result, child)

    def _format_page_result(self, result, ctx):
        """An endpoint whose body ends in a `render` block (with no explicit
        `give back`) serves that rendered page as its response. The render block
        sets _response_content_type='text/html'; when that marker is present we
        return a real {status, body} response with the HTML unwrapped to a plain
        string (a server must send markup, not a MohioValue repr). Endpoints that
        return some other value without giving back are passed through unchanged."""
        if self._ctx_attr(ctx, '_response_content_type') == 'text/html':
            html = result.to_python() if isinstance(result, MohioValue) else result
            return {'status': 200,
                    'body': '' if html is None else str(html),
                    'content_type': 'text/html'}
        return result

    def _exec_NewBlock(self, node, ctx):
        raise MohioRuntimeError(
            "a 'new sh.X at \"/path\"' route handler must live inside a 'listen for' "
            "block. Reached standalone, it would silently never receive requests.")
    def _exec_RequestInboundBlock(self, node, ctx):
        raise MohioRuntimeError(
            "a 'request for sh.X at \"/path\"' route handler must live inside a "
            "'listen for' block. Reached standalone, it would silently never receive "
            "requests.")
    def _exec_ConnectionBlock(self, node, ctx):
        raise MohioRuntimeError(
            "a 'connection' handler must live inside a 'listen for' block. Reached "
            "standalone, it would silently never handle a connection.")
    def _exec_WhileActiveBlock(self, node, ctx):
        raise MohioRuntimeError(
            "'while.active' is retired -- use 'loop' instead.\n"
            "  change:  while.active ... while: done\n"
            "  to:      loop ... loop: done   (break out with 'stop')")
    def _exec_FromConnectorBlock(self, node, ctx):   raise MohioRuntimeError("from <connector> is declared but not yet executable in this build (it would silently do nothing). Tracked for a future release.")
    def _exec_ChangeBlock(self, node, ctx):
        raise MohioRuntimeError(
            "'change to sh.X' is declared but not yet executable in this build (it would "
            "silently do nothing). Tracked for a future release.")

    # ── Flow Control ──────────────────────────────────────────

    # _exec_IfBlock REMOVED (A6): block-`if` is retired (No-If canon). No grammar
    # rule produces an IfBlock; `check / when / otherwise` is the canonical conditional.

    def _loose_eq(self, a, b):
        """Equality for `when` matching. Beginner-friendly: a numeric value matches
        its string form, so `when "0"` matches a numeric 0 (e.g. an INTEGER column).
        String-vs-string stays exact and booleans are never coerced into numbers,
        so only number-vs-numeric-string is loosened — no surprise matches."""
        if a == b:
            return True
        if isinstance(a, bool) or isinstance(b, bool):
            return False
        if isinstance(a, (int, float)) and isinstance(b, str):
            try: return float(a) == float(b)
            except ValueError: return False
        if isinstance(b, (int, float)) and isinstance(a, str):
            try: return float(b) == float(a)
            except ValueError: return False
        return False

    def _run_check_branch_body(self, body, ctx, as_name):
        """Run a check branch body, with optional give-back capture into as_name.
        Shared by both the matched `when` branch and the `otherwise` branch."""
        if as_name:
            try:
                result = self._exec_block(body, ctx)
            except _GiveBack as gb:
                self._debug_trace(ctx,
                    f"  [give back] {gb.value!r} -> bound to {as_name!r}")
                result = gb.value
            if result is not None:
                ctx.set(as_name, result if isinstance(result, MohioValue)
                        else MohioValue(result, 'text'))
            return result
        return self._exec_block(body, ctx)

    def _match_where_condition(self, tree, raw, ctx):
        """Evaluate a where_condition (wc_*) subtree used as a check/when clause.

        Evaluates the clause's OWN left-hand subject (not just the check value),
        then applies the real operator against the right-hand value. This is the
        fix for the bug where string `is`, `is above`, and `is below` matched
        regardless of value (the operator + RHS were dropped and the subtree
        resolved to the subject, which always equalled the check value).
        Unknown comparison forms fail loud rather than silently matching."""
        from lark import Token as _Tok
        data    = str(getattr(tree, 'data', ''))
        non_tok = [c for c in tree.children if not isinstance(c, _Tok)]
        toks    = [c for c in tree.children if isinstance(c, _Tok)]

        def _py(n):
            v = self._eval(n, ctx)
            return v.to_python() if isinstance(v, MohioValue) else v

        left = _py(non_tok[0]) if non_tok else raw

        def _rhs():
            # RHS may be a value node (non_tok[1]) or a STRING / NAME token.
            if len(non_tok) > 1:
                return _py(non_tok[1])
            strtok = next((t for t in toks if t.type == 'STRING'), None)
            if strtok is not None:
                return str(strtok).strip('"').strip("'")
            nametok = next((t for t in toks if t.type == 'NAME'), None)
            if nametok is not None:
                return str(nametok)
            return None

        def _num(x):
            try:
                return float(x if x not in (None, '') else 0)
            except (TypeError, ValueError):
                return None

        if data == 'wc_is':       return self._loose_eq(left, _rhs())
        if data == 'wc_not_is':   return not self._loose_eq(left, _rhs())
        if data == 'wc_is_name':  return self._loose_eq(left, _rhs())
        if data == 'wc_contains': return str(_rhs() or '') in str(left or '')
        if data in ('wc_starts', 'wc_starts_with'):
            return str(left or '').startswith(str(_rhs() or ''))
        if data in ('wc_ends', 'wc_ends_with'):
            return str(left or '').endswith(str(_rhs() or ''))
        if data in ('wc_above', 'wc_below', 'wc_not_above', 'wc_not_below'):
            l, r = _num(left), _num(_rhs())
            if l is None or r is None:
                return False
            if data == 'wc_above':     return l >  r
            if data == 'wc_below':     return l <  r
            if data == 'wc_not_above': return l <= r
            return l >= r                                  # wc_not_below
        if data in ('wc_empty', 'wc_not_empty', 'wc_is_empty'):
            empty  = left in (None, '', [], {}, ())
            is_not = (data == 'wc_not_empty') or any(t.type == 'NOT' for t in toks)
            return (not empty) if is_not else empty
        if data == 'wc_between':
            # `X is between LO and AND HI` -> subject plus two value_expr bounds.
            l  = _num(left)
            lo = _num(_py(non_tok[1])) if len(non_tok) > 2 else None
            hi = _num(_py(non_tok[2])) if len(non_tok) > 2 else None
            if l is None or lo is None or hi is None:
                return False
            return lo <= l <= hi
        raise MohioRuntimeError(
            f"check/when does not yet support the '{data}' comparison form "
            f"(it would otherwise match silently). Tracked for a future release.")

    def _exec_IfGuard(self, node, ctx):
        # `<statement> if <condition>` -- run the statement IF the condition is true.
        # The positive counterpart of `unless`. Same condition evaluator, so it supports
        # bare booleans, comparisons, and and/or/not compounds.
        try:
            run = bool(self._eval_condition(node.condition, ctx))
        except Exception:
            run = False
        if not run:
            return None
        return self._exec(node.stmt, ctx)

    def _exec_UnlessGuard(self, node, ctx):
        # `<statement> unless <condition>` -- run the statement UNLESS the
        # condition is true. Reuses the full condition evaluator, so `unless`
        # supports bare booleans, comparisons, and and/or/not compounds.
        try:
            skip = bool(self._eval_condition(node.condition, ctx))
        except Exception:
            skip = False
        if skip:
            return None
        return self._exec(node.stmt, ctx)

    def _exec_CheckBlock(self, node, ctx):
        """
        Execute a check block. If the block has as_name (check: done as X),
        give back inside when/otherwise sets the block result rather than
        propagating up. The result is bound to as_name in context.
        """
        value = self._eval(node.value, ctx)
        raw   = value.to_python() if isinstance(value, MohioValue) else value

        # Verbose trace: show what is being checked
        _check_name = str(getattr(node, 'value', 'value'))
        self._debug_trace(ctx, f"check {_check_name!r} = {raw!r}")

        for when in node.when_clauses:
            # --- operator forms: where_condition subtree (wc_*) or Condition node.
            # These evaluate the clause's own subject + operator + RHS correctly,
            # bypassing the bare-value equality path below (which Zork relies on).
            _wv = getattr(when, 'value', None)
            if type(_wv).__name__ == 'Tree' and str(getattr(_wv, 'data', '')).startswith('wc_'):
                if self._match_where_condition(_wv, raw, ctx):
                    self._debug_trace(ctx, f"  [branch fired] {_wv.data}")
                    return self._run_check_branch_body(
                        when.body, ctx, getattr(node, 'as_name', None))
                continue
            if _wv is not None and type(_wv).__name__ == 'Condition':
                if self._eval_condition(_wv, ctx):
                    self._debug_trace(ctx, "  [branch fired] condition")
                    return self._run_check_branch_body(
                        when.body, ctx, getattr(node, 'as_name', None))
                continue
            if type(_wv).__name__ in ('AndCondition', 'OrCondition', 'NotCondition'):
                if self._eval_check_compound(_wv, raw, ctx):
                    self._debug_trace(ctx, "  [branch fired] compound")
                    return self._run_check_branch_body(
                        when.body, ctx, getattr(node, 'as_name', None))
                continue
            condition = getattr(when, 'condition', 'when')
            # `when empty` / `when not empty`: an emptiness test on the CHECK SUBJECT (raw) -- this
            # is the not-found / zero-rows path (an empty `find` result is a CONDITION, not a value).
            # "empty" here is a keyword, not a variable, so evaluate _mohio_is_empty(subject) rather
            # than an equality against an undefined `empty` (which silently never matched an empty
            # list -- the exact silent no-op this fixes). A real variable named `empty` still wins.
            _ev = getattr(when, 'value', None)
            if condition == 'when' and type(_ev).__name__ == 'DottedName' \
                    and list(getattr(_ev, 'parts', [])) == ['empty'] and not ctx.exists('empty'):
                if _mohio_is_empty(raw):
                    self._debug_trace(ctx, "  [branch fired] when empty")
                    return self._run_check_branch_body(
                        when.body, ctx, getattr(node, 'as_name', None))
                continue
            when_val  = self._eval(when.value, ctx)
            when_raw  = when_val.to_python() if isinstance(when_val, MohioValue) else when_val
            # If when_raw is a single word with no spaces that matches a session
            # variable, resolve it (enables: check item.location / when current_room)
            # BUT only if it looks like a variable name (no spaces, not a command string)
            if (isinstance(when_raw, str) and when_raw and
                    ' ' not in when_raw and
                    not when_raw.startswith('"') and
                    '_' in when_raw or when_raw in ('current_room', 'lantern_lit',
                        'chest_unlocked', 'mailbox_open', 'has_lantern',
                        'score', 'moves')):
                # Only resolve if it's a known session state variable
                _session_vars = {'current_room', 'lantern_lit', 'chest_unlocked',
                                 'mailbox_open', 'has_lantern', 'has_key', 'chest_open'}
                if when_raw in _session_vars:
                    resolved = ctx.get(when_raw)
                    if resolved and resolved.to_python() is not None:
                        when_raw = resolved.to_python()

            matched = False
            if condition == 'when':
                # DotStateCheck SHORT FORM (OQ-003): `when order.shipped` / `when mfa.verified`.
                # A bare dotted-name whose value is a boolean is a STATE check -- "is this flag
                # true?" -- NOT an equality test against the check subject. `when mfa.verified is
                # true` is the verbose form (handled above via Condition); this is its sugar. We
                # only short-circuit when the when-clause is a dotted-name AND it resolved to a
                # real bool, so ordinary `when <value>` equality (Zork session patterns) is
                # untouched.
                _is_dotted = type(getattr(when, 'value', None)).__name__ == 'DottedName'
                if _is_dotted and isinstance(when_raw, bool):
                    matched = when_raw
                # Treat None and "" as equivalent for session init patterns
                elif when_raw == "" and raw is None:
                    matched = True
                else:
                    matched = self._loose_eq(raw, when_raw)
            elif condition == 'above':
                try: matched = float(raw or 0) > float(when_raw or 0)
                except: matched = False
            elif condition == 'below':
                try: matched = float(raw or 0) < float(when_raw or 0)
                except: matched = False
            elif condition == 'contains':
                matched = str(when_raw or '') in str(raw or '')
            elif condition == 'is_in':
                matched = raw in (when_raw if isinstance(when_raw, (list, tuple)) else [])
            elif condition == 'not':
                matched = (raw != when_raw)
            else:
                matched = (raw == when_raw)

            # Verbose trace: log each condition evaluation
            self._debug_trace(ctx,
                f"  [{condition}] {when_raw!r} -> "
                f"{'MATCHED' if matched else 'no match'}")

            if matched:
                self._debug_trace(ctx,
                    f"  [branch fired] {condition} {when_raw!r}")
                return self._run_check_branch_body(
                    when.body, ctx, getattr(node, 'as_name', None))

        if node.otherwise:
            if getattr(node, 'as_name', None):
                # Named block: catch give back and bind result
                try:
                    result = self._exec_block(node.otherwise.body, ctx)
                except _GiveBack as gb:
                    result = gb.value
                if result is not None:
                    ctx.set(node.as_name, result if isinstance(result, MohioValue)
                            else MohioValue(result, 'text'))
                return result
            else:
                # Unnamed block: let give back propagate naturally
                return self._exec_block(node.otherwise.body, ctx)
        return None

    def _exec_EachBlock(self, node, ctx):
        collection = self._eval(node.collection, ctx)
        items = collection.to_python() if isinstance(collection, MohioValue) else collection
        if not items: return None
        if isinstance(items, dict): items = list(items.values())
        result = None
        for item in items:
            self._check_deadline(node)
            # Bind the loop variable in the enclosing scope and run the body
            # there, so assignments inside the loop (e.g. an accumulator)
            # persist across iterations and out of the loop.
            ctx.set(node.item, item if isinstance(item, MohioValue) else MohioValue(item))
            try:
                result = self._exec_block(node.body, ctx)
            except _Stop as _s:
                if getattr(_s, 'target', None) is None: break
                raise
            except _Skip: continue
        return result

    def _exec_RepeatBlock(self, node, ctx):
        n = int(self._eval_simple(node.count, ctx) or 0)
        if self._loop_iter_limit > 0 and n > self._loop_iter_limit:
            raise _Raise(error_name='loop_limit_exceeded',
                message=f"repeat count {n} exceeds the iteration limit {self._loop_iter_limit}.",
                line=getattr(node, 'line', None),
                hint="Reduce the count, or raise MOHIO_MAX_LOOP_ITERATIONS.")
        result = None
        for _ in range(n):
            self._check_deadline(node)
            try:
                result = self._exec_block(node.body, ctx)
            except _Stop as _s:
                if getattr(_s, 'target', None) is None: break
                raise
            except _Skip: continue
        return result

    def _exec_LoopBlock(self, node, ctx):
        """loop [name] ... loop: done -- runs until a 'stop' inside it.
        'stop' (untargeted) breaks the innermost loop; 'stop name' breaks the
        loop with that name, passing through any loops in between. Capped by the
        iteration limit so a loop with no reachable 'stop' fails loud, not hangs."""
        cap = self._loop_iter_limit
        i = 0
        result = None
        while True:
            if cap > 0 and i >= cap:
                target = node.name or "name"
                raise _Raise(error_name='loop_limit_exceeded',
                    message=f"loop exceeded {cap} iterations -- it never reached a 'stop'.",
                    line=getattr(node, 'line', None),
                    hint=f"Add a 'stop' (or 'stop {target}') inside the loop, or raise MOHIO_MAX_LOOP_ITERATIONS.")
            self._check_deadline(node)
            try:
                result = self._exec_block(node.body, ctx)
            except _Stop as _s:
                if getattr(_s, 'target', None) is None or _s.target == node.name:
                    break
                raise   # targeted at an outer loop -- let it propagate
            except _Skip:
                pass
            i += 1
        return result

    def _exec_WhileBlock(self, node, ctx):
        result = None
        cap = self._loop_iter_limit
        i = 0
        while self._eval_condition(node.condition, ctx):
            if cap > 0 and i >= cap:
                raise _Raise(error_name='loop_limit_exceeded',
                    message=f"while loop exceeded {cap} iterations -- likely an infinite loop.",
                    line=getattr(node, 'line', None),
                    hint="Make the loop condition reach false, add a 'stop', or raise MOHIO_MAX_LOOP_ITERATIONS.")
            self._check_deadline(node)
            try:
                result = self._exec_block(node.body, ctx)
            except _Stop as _s:
                if getattr(_s, 'target', None) is None: break
                raise
            except _Skip: pass
            i += 1
        return result

    def _exec_OtherwiseClause(self, node, ctx):
        # `otherwise` is extracted by its parent block's transformer into a
        # `.otherwise` attribute and run from there. Reaching the interpreter as a
        # standalone statement means it was orphaned (parser/transformer leak) --
        # silently passing would drop the fallback branch. Fail loud.
        raise MohioRuntimeError(
            "internal: an 'otherwise' clause reached the interpreter standalone -- "
            "it should have been attached to its parent block. This is a Mohio "
            "compiler bug, not an error in your code. Please report it.")
    def _exec_OrIfClause(self, node, ctx):
        # 'or if' is RETIRED (No-If canon) and has no grammar rule, so this node
        # should never be built. If it ever is, fail loud rather than silently
        # dropping the branch.
        raise MohioRuntimeError(
            "internal: an 'or if' clause reached the interpreter. 'or if' is "
            "retired -- use a separate 'when' branch in a 'check' block. This is a "
            "Mohio compiler bug, not an error in your code. Please report it.")
    def _exec_SectionBlock(self, node, ctx):    return self._exec_block(node.body, ctx)
    def _exec_TrailingQualifier(self, node, ctx):
        # Trailing qualifiers (e.g. `cache for 5 minutes`, `as NAME`) are consumed
        # inline by the statement they attach to. Reaching the interpreter
        # standalone means the qualifier was dropped -- fail loud rather than
        # silently ignore it.
        raise MohioRuntimeError(
            "internal: a trailing qualifier reached the interpreter standalone -- "
            "it should have been consumed by the statement it modifies. This is a "
            "Mohio compiler bug, not an error in your code. Please report it.")

    # ── MioQL — Data Operations ────────────────────────────────

    def _exec_RetrieveBlock(self, node, ctx):
        db, _early = self._db_or_fail(ctx, 'retrieve', node)
        if db is None: return _early
        table = self._resolve_source(node.source, ctx)

        # Raw-SQL escape hatch, first-class inside the query block:
        #     retrieve results from db.members
        #         sql
        #             SELECT ...
        #         sql: done
        #     retrieve: done
        # The enclosing `retrieve` names the result, so the sql block needs no alias of its
        # own. Same result shape as the native MioQL form.
        from mohio_ast import SqlBlock as _SqlBlock
        _inner_sql = next((b for b in (node.body or []) if isinstance(b, _SqlBlock)), None)
        if _inner_sql is not None:
            _res = self._exec_SqlBlock(_inner_sql, ctx)
            _val = _res if isinstance(_res, MohioValue) else MohioValue(_res)
            if getattr(node, 'name', None):
                ctx.set(node.name, _val)
            ctx.set('it', _val)
            return _val

        from mohio_ast import MatchBlock, MatchAnyBlock, NoMatchBlock
        spec = []
        for b in node.body:
            if isinstance(b, MatchClause):
                spec.append(('and', [(b.field, self._eval_simple(b.value, ctx))]))
            elif isinstance(b, MatchBlock):
                spec.append(('and', [(f, self._eval_simple(v, ctx)) for f, v in b.pairs]))
            elif isinstance(b, MatchAnyBlock):
                spec.append(('or', [(f, self._eval_simple(v, ctx)) for f, v in b.pairs]))
            elif isinstance(b, NoMatchBlock):
                spec.append(('not', [(f, self._eval_simple(v, ctx)) for f, v in b.pairs]))

        # Top-level groups are ANDed: simple matches + AND-block = AND,
        # `match any` = OR within its group, `no.match` = NOT (none of these).
        mod  = (node.modifier or 'one').lower()
        name = node.alias if node.alias else node.name

        def _bind_and_succeed(value):
            ctx.set(name, value)
            if node.alias:
                ctx.set(node.name, value)          # bind both name forms
            self._handle_success(node.handlers, ctx)
            return value

        # ── multi-row: .all / .every -> collection (each-iterable), .count -> number ──
        # An empty spec is valid for these: it means "every row".
        if mod in ('all', 'every'):
            rows = db.retrieve_all_spec(table, spec)
            if self.verbose: print(f"  [retrieve.{mod}] {len(rows)} from {table}")
            return _bind_and_succeed(MohioValue(rows, 'list'))
        if mod == 'count':
            n = len(db.retrieve_all_spec(table, spec))
            if self.verbose: print(f"  [retrieve.count] {n} in {table}")
            return _bind_and_succeed(MohioValue(n, 'number'))
        if mod in ('first', 'last'):
            rows = db.retrieve_all_spec(table, spec)
            if not rows:
                return self._handle_failure(node.handlers, ctx, f"retrieve.{mod}: no record in {table}")
            row = rows[0] if mod == 'first' else rows[-1]
            if self.verbose: print(f"  [retrieve.{mod}] from {table}")
            return _bind_and_succeed(MohioValue(row, 'shape'))

        # ── single-row: .one (default) -- requires a match clause ──
        if not spec:
            return self._handle_failure(node.handlers, ctx, "retrieve: no match clause")
        row = db.retrieve_one_spec(table, spec)
        row = self._decrypt_row(row) if row else row
        if row is None:
            return self._handle_failure(node.handlers, ctx, f"retrieve: no record in {table}")
        if self.verbose: print(f"  [retrieve] {node.name} from {table}")
        self._audit_data_access('retrieve', table, row, ctx)
        return _bind_and_succeed(MohioValue(row, 'shape'))

    def _write_export(self, rows, spec, ctx):
        # Wire the `export as.FORMAT to PATH` egress. csv and json are wired;
        # pdf and xlsx fail loud until a document library is added.
        import csv as _csv, json as _json, os as _os
        fmt = getattr(spec, 'format', 'csv')
        target = self._eval_simple(spec.target, ctx)
        if hasattr(target, 'to_python'): target = target.to_python()
        target = str(target)
        _dir = _os.path.dirname(target)
        if _dir:
            _os.makedirs(_dir, exist_ok=True)
        data = [dict(r) for r in rows]
        if fmt == 'json':
            with open(target, 'w', encoding='utf-8') as f:
                _json.dump(data, f, indent=2, default=str)
        elif fmt == 'csv':
            keys = list(data[0].keys()) if data else []
            with open(target, 'w', newline='', encoding='utf-8') as f:
                w = _csv.DictWriter(f, fieldnames=keys)
                w.writeheader()
                for r in data:
                    w.writerow(r)
        else:
            raise MohioRuntimeError(
                f"export as.{fmt} is not wired yet -- use as.csv or as.json. "
                f"(pdf and xlsx need a document library.)")
        if getattr(self, 'verbose', False):
            print(f"  [export] {len(data)} rows -> {target} ({fmt})")

    def _exec_FindBlock(self, node, ctx):
        """
        Execute a find block with full pagination support.

        Option A pagination: find with page/cursor modifier returns
        a rich paginated result. Collection accessors (.first, .last,
        .count, .position.N, .page.*) all work on the result.

        Offset pagination:
            find orders in db.orders
                up to 20
                page request.page default 1
            find: done
            orders.page.current   // 1
            orders.page.total     // 47
            orders.page.has_more  // true
            orders.count          // 931

        Cursor pagination:
            find txns in db.transactions
                up to 50
                cursor from request.cursor
            find: done
            txns.page.has_more    // true
            txns.page.next_cursor // "abc123"
        """
        # Raw-SQL escape hatch, first-class inside the query block. The enclosing verb
        # names the result, so the sql block needs no alias of its own.
        from mohio_ast import SqlBlock as _SqlBlock
        _inner = next((b for b in (getattr(node, 'body', None) or [])
                       if isinstance(b, _SqlBlock)), None)
        if _inner is not None:
            _res = self._exec_SqlBlock(_inner, ctx)
            _val = _res if isinstance(_res, MohioValue) else MohioValue(_res)
            if getattr(node, 'name', None):
                ctx.set(node.name, _val)
            ctx.set('it', _val)
            return _val
        import math as _math

        db, _early = self._db_or_fail(ctx, 'find', node)
        if db is None: return _early
        table = self._resolve_source(node.source, ctx)

        from mohio_ast import MatchBlock, MatchAnyBlock, NoMatchBlock, TimespanRef
        where      = {}
        post_filters = []   # (field, condition, value, value2) for non-equality ops
        timespan_field = None  # set to the column a `timespan NAME` clause range-filters on
        or_groups  = []     # match any  -> list of [(field, value), ...] OR-groups (each ANDed)
        not_groups = []     # no.match   -> list of [(field, value), ...] NOT-groups (each ANDed)
        limit      = None
        order_by   = None
        order_dir  = 'asc'
        page_num   = None   # offset pagination
        cursor_val = None   # cursor pagination
        skip_offset = 0     # skip N — leading offset
        has_cursor = False  # cursor clause present (not yet wired -> fail loud)
        export_spec = None  # export as.csv/json/... to path

        for clause in node.body:
            if isinstance(clause, (WhereClause, AndClause)):
                field_name = clause.field.split('.')[-1]
                if clause.condition in ('is', '==', ''):
                    where[field_name] = self._eval_filter_value(clause.value, ctx)
                elif clause.condition == 'is_in' and type(clause.value).__name__ == 'TimePeriod':
                    # `is.in <time period>` -> a half-open [start, end) DATE-RANGE filter (Section 2,
                    # 2026-08-01). Resolve the period to concrete bounds now and carry them as a
                    # `time_in_range` post-filter (start in v, end in v2), applied per-row below.
                    _s, _e = self._timeperiod_range(clause.value)
                    post_filters.append((field_name, 'time_in_range', _s.isoformat(), _e.isoformat()))
                else:
                    v  = self._eval_filter_value(clause.value, ctx)
                    v2 = (self._eval_filter_value(clause.value2, ctx)
                          if getattr(clause, 'value2', None) is not None else None)
                    post_filters.append((field_name, clause.condition, v, v2))
            elif isinstance(clause, TimespanRef):
                # `timespan NAME` -> range-filter the result by the declared timespan's half-open
                # [start, end) window. Defaults to the `created_at` column (2026-08-01 ruling); the
                # explicit-field form (`timespan NAME on <field>`) is a deferred follow-on. The
                # created_at existence check (fail loud if the table lacks it) runs after fetch.
                _s, _e = self._resolve_declared_timespan(clause.name)
                post_filters.append(('created_at', 'time_in_range', _s, _e))
                timespan_field = 'created_at'
            elif isinstance(clause, MatchClause):
                # 'match field to value' is an equality filter, same as where ... is
                where[clause.field.split('.')[-1]] = self._eval_filter_value(clause.value, ctx)
            elif isinstance(clause, MatchBlock):
                # multi-field 'match' AND-block: every pair is an equality filter
                for f, v in clause.pairs:
                    where[f.split('.')[-1]] = self._eval_filter_value(v, ctx)
            elif isinstance(clause, MatchAnyBlock):
                # 'match any' OR-block: row must satisfy at least one pair in the group
                or_groups.append([(f.split('.')[-1], self._eval_filter_value(v, ctx))
                                  for f, v in clause.pairs])
            elif isinstance(clause, NoMatchBlock):
                # 'no.match' NOT-block: row must satisfy NONE of the pairs in the group
                not_groups.append([(f.split('.')[-1], self._eval_filter_value(v, ctx))
                                   for f, v in clause.pairs])
            elif isinstance(clause, LimitClause):
                limit = int(self._eval_simple(clause.count, ctx) or 0)
            elif isinstance(clause, OrderClause):
                order_by  = clause.field
                order_dir = 'asc' if clause.direction == 'up' else 'desc'
            elif isinstance(clause, ExportClause):
                export_spec = clause
            elif hasattr(clause, '__class__') and 'Paginate' in clause.__class__.__name__:
                # paginate by NUMBER — 1-based page number
                pv = getattr(clause, 'count', None)
                try:
                    page_num = max(1, int(pv if pv is not None else 1))
                except (TypeError, ValueError):
                    page_num = 1
            elif hasattr(clause, '__class__') and 'Skip' in clause.__class__.__name__:
                # skip NUMBER — leading offset
                try:
                    skip_offset = max(0, int(getattr(clause, 'count', 0) or 0))
                except (TypeError, ValueError):
                    skip_offset = 0
            elif hasattr(clause, '__class__') and 'Cursor' in clause.__class__.__name__:
                # cursor from request.cursor  (designed, not yet wired)
                has_cursor = True
                src = getattr(clause, 'source', None) or getattr(clause, 'value', None)
                cursor_val = self._eval_simple(src, ctx) if src else None

        # Cursor (keyset) pagination is designed but not yet wired in the db
        # layer. Fail loud the moment it's used, before the query try-block, so
        # the error reaches the developer rather than being swallowed.
        if has_cursor:
            raise _Raise(error_name='cursor_pagination_unavailable',
                message="cursor pagination ('cursor from ...') is not wired in this build yet.",
                line=getattr(node, 'line', None),
                hint=("Use offset pagination for now: 'up to N' together with "
                      "'paginate by PAGE'. Cursor (keyset) pagination is on the roadmap."))

        if (or_groups or not_groups) and page_num is not None:
            raise _Raise(error_name='paginate_with_or_not_unavailable',
                message=("offset pagination ('paginate by ...') combined with "
                         "'match any' or 'no.match' is not wired yet (page counts "
                         "would not reflect the OR/NOT filter)."),
                line=getattr(node, 'line', None),
                hint=("Use 'up to N' alone with match any / no.match, or switch the "
                      "filter to a 'match' (AND) block, which paginates correctly."))

        try:
            # ── Offset pagination ─────────────────────────────────
            if page_num is not None and limit:
                offset     = skip_offset + (page_num - 1) * limit
                rows       = db.find_many(table, where, limit=limit,
                                          order_by=order_by, order_dir=order_dir,
                                          offset=offset)
                rows = [self._decrypt_row(r) for r in rows]
                # COUNT query for total pages
                total_count = db.count(table, where) if hasattr(db, 'count') else len(rows)
                total_pages = _math.ceil(total_count / limit) if limit else 1

                page_meta = {
                    'current':    page_num,
                    'total':      total_pages,
                    'has_more':   page_num < total_pages,
                    'count':      total_count,
                    'per_page':   limit,
                    'next':       page_num + 1 if page_num < total_pages else None,
                    'prev':       page_num - 1 if page_num > 1 else None,
                    'next_cursor': None,
                }

            # ── Simple bounded find (with optional skip offset) ───
            else:
                # When match-any/no.match block filters are present, fetch the
                # equality-filtered set unbounded and defer limit/offset to memory
                # so the OR/NOT filter is applied BEFORE limiting (correct count).
                _blk = bool(or_groups or not_groups)
                rows = db.find_many(table, where,
                                    limit=(None if _blk else limit),
                                    order_by=order_by, order_dir=order_dir,
                                    offset=(0 if _blk else skip_offset))
                rows = [self._decrypt_row(r) for r in rows]
                page_meta = None

        except Exception as e:
            return self._handle_failure(node.handlers, ctx, str(e))

        # PHI audit-on-access: a read that returned [phi] data is logged (HIPAA).
        self._audit_data_access('find', table, rows, ctx)

        # Export egress: write the result set to a file if `export ... to` was given.
        if export_spec is not None:
            self._write_export(rows, export_spec, ctx)

        # Build rich result — collection accessors + page metadata
        # The MohioValue stores rows as list but page metadata is accessible
        # via get_dotted interception of .page.* keys
        # A `timespan NAME` filter defaults to the `created_at` column. If the table has no such
        # column, FAIL LOUD naming it -- never silently filter on nothing (the time_in_range
        # post-filter would otherwise exclude every row). Checked against a fetched row (the common
        # case); an empty table has nothing to filter, so an empty result is honest there.
        if timespan_field and rows and timespan_field not in rows[0]:
            raise MohioRuntimeError(
                f"a `timespan` filter defaults to the `{timespan_field}` column, but table "
                f"'{table}' has no `{timespan_field}` field. Add a `{timespan_field}` timestamp "
                f"column to that table (the explicit-field form `timespan NAME on <field>` is a "
                f"planned follow-on).")

        # ── apply non-equality where conditions (above/below/between/contains/...) ──
        if post_filters and rows:
            rows = [r for r in rows
                    if all(self._row_matches(r, f, c, v, v2)
                           for (f, c, v, v2) in post_filters)]

        # ── apply match-any (OR) and no.match (NOT) block filters in memory ──
        # Each match-any block is an OR-group; multiple blocks are ANDed together.
        # no.match excludes any row matching any pair in the group.
        if or_groups and rows:
            rows = [r for r in rows
                    if all(any(str(r.get(f)) == str(v) for f, v in grp)
                           for grp in or_groups)]
        if not_groups and rows:
            rows = [r for r in rows
                    if all(all(str(r.get(f)) != str(v) for f, v in grp)
                           for grp in not_groups)]
        # Block filters deferred limit/offset to here (after filtering) for a correct set.
        if or_groups or not_groups:
            if skip_offset:
                rows = rows[skip_offset:]
            if limit:
                rows = rows[:limit]

        result_data = rows  # keep as list for each/iteration

        # ── find ... random.N — return N random matches from the result set ──
        # Sampling happens after where/match filtering, so it's "N random of the
        # rows that matched". min(n, len): a short result returns fewer, never errors.
        if getattr(node, 'random_n', None) is not None and result_data:
            import random as _random
            result_data = _random.sample(list(result_data),
                                         min(node.random_n, len(result_data)))
            rows = result_data

        # ── calculate: analytic/window columns added per row (rows preserved) ──
        calc = next((c for c in node.body if isinstance(c, CalculateBlock)), None)
        if calc is not None:
            rows = self._apply_calculate(calc, rows)
            result_data = rows

        # ── summarize: grouped aggregation that collapses rows ──
        summ = next((c for c in node.body if isinstance(c, SummarizeBlock)), None)
        if summ is not None:
            rows = self._apply_summarize(summ, rows, getattr(node, 'group_by', None))
            result_data = rows

        # ── return clause: aggregates -> scalar aliases; plain fields -> projection ──
        ret = next((c for c in node.body if isinstance(c, ReturnClause)), None)
        if ret is not None and getattr(ret, 'fields', None):
            aggs  = [f for f in ret.fields if f.get('kind') == 'agg']
            projs = [f for f in ret.fields if f.get('kind') == 'field']
            for f in aggs:
                val = self._compute_aggregate(f['func'], f.get('field'), rows)
                ctx.set(f['alias'], MohioValue(val, 'number'))
            if projs:
                projected = []
                for r in rows:
                    if not isinstance(r, dict):
                        projected.append(r); continue
                    newr = {}
                    for f in projs:
                        key = f['field'].split('.')[-1]
                        newr[f['alias']] = r.get(key, r.get(f['field']))
                    projected.append(newr)
                rows = projected
                result_data = rows

        result = MohioValue(rows, 'list')

        # Attach page metadata alongside — accessible as node.name.page.*
        # Store in context with special key for get_dotted to find
        if page_meta is not None:
            ctx.set(f'__page__{node.name}', MohioValue(page_meta, 'shape'))

        ctx.set(node.name, result)

        if self.verbose:
            mode = (f"page {page_num}" if page_num else
                    f"cursor" if cursor_val else "bounded")
            print(f"  [find] {node.name} in {table} -> {len(rows)} rows [{mode}]")

        # DESIGN (Ronnie): on.failure means IT BROKE -- error, no connection, bad table. It does
        # NOT mean "no results". Conflating them made a dead database render to the user as
        # "You're all caught up!", which is the exact silent failure Mohio exists to kill.
        # An empty result is a CONDITION, and conditions belong to when / otherwise.
        # The query ran, so this is the success path; the conditional set runs after it.
        if node.handlers:
            self._handle_success(node.handlers, ctx)

        return result

    def _eval_filter_value(self, node, ctx):
        """Evaluate a where/and value. Quoted strings and numbers are literals;
        a bare or dotted word is a reference. An undefined bare word fails loud
        with a hint to quote it — so `where status is active` (no quotes, no such
        variable) teaches the fix rather than silently matching nothing."""
        from mohio_ast import DottedName as _DN
        if isinstance(node, _DN) and len(node.parts) == 1 and not ctx.exists(node.parts[0]):
            name = node.parts[0]
            raise _Raise(error_name='unknown_filter_value',
                message=f"where refers to '{name}', but no such value is defined.",
                line=getattr(node, 'line', None),
                hint=f'If you meant the literal text, quote it:  is "{name}"  — '
                     f'otherwise define {name} first.')
        return self._eval_simple(node, ctx)

    def _apply_summarize(self, summ, rows, group_by):
        """Grouped aggregation that collapses rows. With a group_by, returns one
        row per group (group key + aggregates); without, one summary row over all
        rows. summarize handles the basic reducers (sum/count/average/max/min) —
        analytic functions (running_sum, moving_average, rank, percentile, ...)
        belong to a calculate block and fail loud here rather than mislead."""
        BASIC = {'sum', 'count', 'average', 'max', 'min'}

        def compute(grp_rows):
            out = {}
            for f in summ.fields:
                func = f.function
                if func not in BASIC:
                    raise _Raise(error_name='summarize_unsupported_function',
                        message=f"'{func}' is an analytic function and can't be used in summarize.",
                        line=getattr(f, 'line', None),
                        hint="summarize does sum/count/average/max/min. Use a calculate "
                             "block for running_sum / moving_average / rank / percentile / etc.")
                src = f.arg.get('source') if isinstance(f.arg, dict) else f.arg
                out[f.name] = self._compute_aggregate(func, src, grp_rows)
            return out

        if group_by:
            groups = {}
            for r in rows:
                key = r.get(group_by) if isinstance(r, dict) else None
                groups.setdefault(key, []).append(r)
            result = []
            for key, grp in groups.items():
                row = {group_by: key}
                row.update(compute(grp))
                result.append(row)
            return result
        return [compute(rows)]   # single summary row over all matched rows

    @staticmethod
    def _percentile(nums, p):
        """Linear-interpolation percentile (same method as numpy default)."""
        if not nums:
            return None
        s = sorted(nums)
        if len(s) == 1:
            return s[0]
        k = (len(s) - 1) * (float(p) / 100.0)
        lo = math.floor(k)
        hi = math.ceil(k)
        if lo == hi:
            return s[int(k)]
        return s[lo] * (hi - k) + s[hi] * (k - lo)

    def _apply_calculate(self, calc, rows):
        """Analytic/window block: adds a computed column to every row and keeps
        all rows (the opposite of summarize, which collapses). running_sum and
        moving_average depend on row order, so order the find first. std_deviation,
        variance and percentile are set-level scalars broadcast onto each row."""
        out = [dict(r) if isinstance(r, dict) else {'value': r} for r in rows]

        def fval(r, src):
            v = r.get(src) if isinstance(r, dict) else None
            if v is None:
                return None
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        for f in calc.fields:
            func = f.function
            spec = f.arg if isinstance(f.arg, dict) else {}
            src  = spec.get('source')
            vals = [fval(r, src) for r in out]
            nums = [v for v in vals if v is not None]

            if func == 'running_sum':
                run = 0.0
                for i, r in enumerate(out):
                    if vals[i] is not None:
                        run += vals[i]
                    r[f.name] = int(run) if float(run).is_integer() else run

            elif func == 'moving_average':
                w = spec.get('window') or 1
                for i, r in enumerate(out):
                    win = [vals[j] for j in range(max(0, i - w + 1), i + 1)
                           if vals[j] is not None]
                    r[f.name] = (sum(win) / len(win)) if win else None

            elif func == 'rank':
                part = spec.get('partition')
                groups = {}
                for i, r in enumerate(out):
                    key = r.get(part) if (part and isinstance(r, dict)) else None
                    groups.setdefault(key, []).append(i)
                for idxs in groups.values():
                    ordered = sorted(idxs, key=lambda i: (vals[i] is None, -(vals[i] or 0)))
                    for rank, i in enumerate(ordered, start=1):
                        out[i][f.name] = rank

            elif func in ('std_deviation', 'variance'):
                if len(nums) >= 2:
                    val = (statistics.stdev(nums) if func == 'std_deviation'
                           else statistics.variance(nums))
                else:
                    val = 0
                for r in out:
                    r[f.name] = val

            elif func == 'percentile':
                val = self._percentile(nums, spec.get('p', 50))
                for r in out:
                    r[f.name] = val

            else:
                # difference / p_value / cohens_d / percentage_of — not yet wired
                raise _Raise(error_name='calculate_unsupported_function',
                    message=f"'{func}' is not yet implemented in calculate.",
                    line=getattr(f, 'line', None),
                    hint="Supported now: running_sum, moving_average, rank, "
                         "std_deviation, variance, percentile.")
        return out

    def _parse_datetime(self, v):
        """Parse a date/datetime value into a comparable datetime, or None if it cannot be parsed.

        Accepts ISO date ('2026-06-30'), ISO datetime ('2026-06-30T14:30:00'), a MohioValue
        wrapping either, or an existing date/datetime. Dates are widened to midnight so a date and
        a datetime compare on the same axis. Returns None on anything unparseable rather than
        raising -- the caller treats None as "not in range" (a row we cannot place is excluded).
        """
        import datetime as _dt
        if v is None:
            return None
        if isinstance(v, MohioValue):
            v = v.to_python()
        if isinstance(v, _dt.datetime):
            return v
        if isinstance(v, _dt.date):
            return _dt.datetime(v.year, v.month, v.day)
        s = str(v).strip().strip('"')
        if not s:
            return None
        try:
            d = _dt.datetime.fromisoformat(s)
            return d
        except ValueError:
            pass
        try:
            d = _dt.date.fromisoformat(s)
            return _dt.datetime(d.year, d.month, d.day)
        except ValueError:
            return None

    def _row_matches(self, row, field, cond, value, value2=None):
        """Apply a non-equality where condition to a single row. Unknown
        conditions return True (no filtering) — they were never applied before
        either, so this never silently drops rows that used to pass."""
        if not isinstance(row, dict):
            return True
        raw = row.get(field)
        def _num(x):
            return float(x)
        try:
            if cond == 'above':      return _num(raw) > _num(value)
            if cond == 'below':      return _num(raw) < _num(value)
            if cond == 'not_above':  return _num(raw) <= _num(value)
            if cond == 'not_below':  return _num(raw) >= _num(value)
            if cond == 'between':    return _num(value) <= _num(raw) <= _num(value2)
        except (TypeError, ValueError):
            # The datetime word family compares DATETIMES, not numbers -- fall through to the
            # datetime path below rather than failing here. (Every other numeric cond legitimately
            # returns False on bad nums.)
            if cond not in ('before', 'after', 'older', 'newer', 'since', 'from'):
                return False
        # ── datetime word family ──────────────────────────────────────────────────────────
        # before/after/older/newer/since/from all compare the row's field to a resolved point in
        # time (the anchor was already resolved to an ISO date/datetime string by
        # _eval_filter_value). These are DATETIME comparisons, never numeric -- so they live in
        # their own path, not the float `above`/`below` path above. A row whose field is missing or
        # unparseable is excluded (it cannot be shown to be in range).
        #
        # English-meaning inclusivity, so the word carries the meaning with no table to memorize:
        #   before / older  ->  strictly earlier   (<)   "before Tuesday" is not Tuesday
        #   after  / newer   ->  strictly later     (>)   "after Tuesday" is not Tuesday
        #   since  / from    ->  at-or-after        (>=)  "since/from Monday" includes Monday
        _DT_STRICT_LT = {'before', 'older'}
        _DT_STRICT_GT = {'after', 'newer'}
        _DT_INCLUSIVE_GE = {'since', 'from'}
        if cond in _DT_STRICT_LT or cond in _DT_STRICT_GT or cond in _DT_INCLUSIVE_GE:
            if raw is None:
                return False
            anchor_dt = self._parse_datetime(value)
            row_dt = self._parse_datetime(raw)
            if anchor_dt is None or row_dt is None:
                return False
            if cond in _DT_STRICT_LT:
                return row_dt < anchor_dt
            if cond in _DT_STRICT_GT:
                return row_dt > anchor_dt
            return row_dt >= anchor_dt          # since / from  (inclusive)
        # ── time-range membership: `is.in <period>` -> half-open [start, end) ──────────────
        # value = start ISO, value2 = end ISO (both from _timeperiod_range, tz-aware). A row is IN
        # the period iff start <= created_at < end (end EXCLUSIVE -- so `is.in today` and
        # `is.in yesterday` never both match the same instant). A row we cannot place is excluded.
        if cond == 'time_in_range':
            if raw is None:
                return False
            row_dt   = self._parse_datetime(raw)
            start_dt = self._parse_datetime(value)
            end_dt   = self._parse_datetime(value2)
            if row_dt is None or start_dt is None or end_dt is None:
                return False
            # Align awareness: a naive stored timestamp is read as being in the range's timezone.
            if row_dt.tzinfo is None and start_dt.tzinfo is not None:
                row_dt = row_dt.replace(tzinfo=start_dt.tzinfo)
            elif row_dt.tzinfo is not None and start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=row_dt.tzinfo)
                end_dt   = end_dt.replace(tzinfo=row_dt.tzinfo)
            return start_dt <= row_dt < end_dt
        sval = str(value).strip('"') if value is not None else ''
        if cond == 'contains':                    return raw is not None and sval in str(raw)
        if cond in ('starts', 'starts_with'):     return raw is not None and str(raw).startswith(sval)
        if cond in ('ends', 'ends_with'):         return raw is not None and str(raw).endswith(sval)
        if cond == 'is_not':                      return str(raw) != str(value)
        if cond == 'empty':                       return raw in (None, '', [])
        if cond == 'not_empty':                   return raw not in (None, '', [])
        if cond in ('is_in', 'in_list', 'in'):    return raw in (value if isinstance(value, (list, tuple, set)) else [value])
        if cond in ('not_in', 'not_in_list'):     return raw not in (value if isinstance(value, (list, tuple, set)) else [value])
        # UNKNOWN condition. Returning True here silently matched EVERY row -- a data-correctness AND
        # EXPOSURE bug: a filter meant to exclude rows would instead return all of them, leaking rows
        # the filter was supposed to keep out. A filter that cannot be applied must FAIL LOUD, never
        # silently pass everything.
        raise MohioRuntimeError(
            f"unknown filter condition '{cond}' on field '{field}' -- this `find`/`where` filter is "
            f"not recognized, so it cannot be applied and would otherwise match every row. Check "
            f"the condition word.")

    def _compute_aggregate(self, func, field, rows):
        """Compute count/sum/average/max/min over a list of row dicts.
        count ignores the field; the others coerce the named field to number
        and skip non-numeric/missing values. Whole results return as int."""
        if func == 'count':
            return len(rows)
        key  = field.split('.')[-1] if field else field
        vals = []
        for r in rows:
            v = r.get(key) if isinstance(r, dict) else None
            if v is None:
                continue
            try:
                vals.append(float(v))
            except (TypeError, ValueError):
                continue
        if not vals:
            return 0 if func == 'sum' else None
        if   func == 'sum':     out = sum(vals)
        elif func == 'average': out = sum(vals) / len(vals)
        elif func == 'max':     out = max(vals)
        elif func == 'min':     out = min(vals)
        else:                   return None
        return int(out) if float(out).is_integer() else out

    @staticmethod
    def _audit_rollback(sink):
        """Clear an aborted transaction so a deliberate probe failure does not poison the rest.

        Postgres aborts the entire transaction on any failed statement and refuses every
        subsequent command until rollback. Log discovery probes tables that are NOT audit tables
        on purpose -- each of those failures would otherwise leave the connection unusable, so
        the very act of looking for audit logs broke reading them.
        """
        try:
            if not getattr(sink.conn, 'autocommit', True):
                sink.conn.rollback()
        except Exception:
            pass

    @staticmethod
    def _audit_query(sink, sql):
        """Run a read-only audit query, portably across sqlite3 and psycopg2.

        `conn.execute(...)` is a sqlite3 convenience that psycopg2 does not have -- on Postgres it
        raises AttributeError, which the chain readers caught and turned into "log unreadable" or
        an empty result. The visible effect was severe and silent: verification reported nothing
        to check, log discovery returned an empty list, and the seed returned None so a restart
        began a second chain from genesis. Everything looked calm and none of it worked.

        `conn.cursor()` exists on both. Postgres additionally needs its dict-row cursor factory,
        or rows come back as plain tuples and every `row['column']` access fails.
        """
        conn = sink.conn
        if not hasattr(conn, 'cursor'):
            # A non-SQL backend (Mongo) reaches here only because something bound it as an audit
            # sink. Degrading to "log unreadable" would report a missing chain as a broken one,
            # which is the same silent-untruth the Postgres path produced. Say what is wrong.
            raise MohioRuntimeError(
                f"audit chain cannot be read from a {type(sink).__name__} sink: the audit chain "
                f"requires a SQL backend (sqlite, postgres, mysql). Bind a SQL audit sink, or "
                f"the trail cannot be verified at all.")
        factory = getattr(sink, '_cursor_factory', None)
        if factory is not None:
            try:
                cur = conn.cursor(cursor_factory=factory)
            except TypeError:
                cur = conn.cursor()
        else:
            cur = conn.cursor()
        cur.execute(sql)
        return cur

    @staticmethod
    def _audit_rows(sink, log_name):
        """Every chained record in a log, as plain dicts. No ORDER BY: the chain defines the
        order, not the storage. That is portability (Postgres has no rowid) and correctness at
        once -- a tamper-evidence mechanism that trusts the database's own row order is trusting
        the thing it is supposed to be checking."""
        from mohio_audit_grades import canonical_audit_columns as _cac
        cols = tuple(_cac())
        _sel = ', '.join(f'"{c}"' for c in cols)
        cur = MohioInterpreter._audit_query(sink, f'SELECT {_sel} FROM "{log_name}"')
        out = []
        for r in cur.fetchall():
            if hasattr(r, 'keys'):
                out.append({c: r[c] for c in cols})
            else:
                out.append(dict(zip(cols, r)))
        return [r for r in out if r.get('entry_hash')]

    def audit_logs(self, sink):
        """Every audit log present in a sink, by name.

        An audit table is identified by carrying the canonical chain columns, so this finds the
        logs without knowing any app's schema. Works on SQLite and Postgres: the control plane
        reads these from the tenant's own database, which is Postgres in production.
        """
        names = []
        candidates = []
        try:                                    # SQLite
            cur = self._audit_query(
                sink, "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            candidates = [r['name'] if hasattr(r, 'keys') else r[0] for r in cur.fetchall()]
        except Exception:
            self._audit_rollback(sink)          # sqlite_master does not exist on Postgres
            try:                                # Postgres / anything with information_schema
                cur = self._audit_query(
                    sink,
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema NOT IN ('pg_catalog','information_schema') "
                    "ORDER BY table_name")
                candidates = [r['table_name'] if hasattr(r, 'keys') else r[0]
                              for r in cur.fetchall()]
            except Exception:
                return []
        for t in candidates:
            if t.startswith('sqlite_') or t.startswith('pg_'):
                continue                        # engine bookkeeping tables, not app logs
            try:
                # Probe for the chain columns rather than reading a driver-specific catalog.
                # LIMIT 1 rather than WHERE 1=0: SQLite skips column resolution on a
                # never-true predicate, so a false negative table would have passed the probe.
                cur = self._audit_query(sink, f'SELECT "audit_id", "prev_hash", "entry_hash" '
                                                 f'FROM "{t}" LIMIT 1')
                cur.fetchall()
                names.append(t)
            except Exception:
                self._audit_rollback(sink)      # the probe was meant to fail; don't poison the tx
                continue
        return names

    def audit_chain_head(self, sink, log_name):
        """The current head of a log's hash chain, plus the length it covers.

        This is the value an anchoring scheme publishes, and it is the same value in every
        deployment. Reading it is open core: any runtime, hosted or self-hosted, on SQLite or
        Postgres, can compute its own head and verify its own chain.

        What is NOT universal is who HOLDS the published head, and that is the part that carries
        the guarantee. An anchor is only worth something to a third party if it is held by
        someone other than the party being audited: an operator who signs anchors over their own
        logs can rewrite the log and re-sign a matching anchor, so the anchor proves nothing. The
        checker must not be the checked.

        Deployments bind that differently:
          - hosted:      the control plane PULLS this head from the tenant's own database, signs
                         it with a key the tenant machine never holds, and stores it on its own
                         side. The tenant needs no new secret and cannot forge an anchor.
          - self-hosted: there is no control plane to pull, so the head must be pushed somewhere
                         the operator does not control (an independent anchoring service, or a
                         neutral timestamping authority) for the same guarantee to hold. An
                         operator anchoring to storage they own gets tamper-evidence for their own
                         benefit and nothing a regulator should accept.
          - neither:     valid, but then the runtime should say so rather than imply otherwise:
                         the chain is still tamper-evident for alteration, deletion, and forking,
                         and still cannot detect truncation of the tail.

        Publishing the head is what closes the one gap the chain cannot close by itself: removing
        records from the END of a log leaves a shorter but internally consistent chain, and only a
        previously published head disagrees with it.
        """
        result = self.verify_audit_chain(sink, log_name)
        head = self.AUDIT_GENESIS
        readable = True
        try:
            rows = self._audit_rows(sink, log_name)
            # The head is the record no other record points back to. Derived from the links, so
            # it needs no ordering column and behaves the same on SQLite and Postgres.
            referenced = {r['prev_hash'] for r in rows}
            tips = [r['entry_hash'] for r in rows if r['entry_hash'] not in referenced]
            if len(tips) == 1:
                head = tips[0]
            elif tips:
                head = sorted(tips)[0]      # forked chain; verify() reports the break
        except Exception as _e:
            # A head that could not be READ is not an empty log, and must never be published as
            # one. Returning the genesis sentinel here would hand an anchoring consumer a
            # perfectly well-formed "head" for a log it never managed to open -- anchoring the
            # absence of evidence as though it were evidence.
            readable = False
            result = dict(result)
            result['ok'] = False
            result['reason'] = (result.get('reason')
                                or f"chain head could not be read: {_e}")
        return {
            'log':      log_name,
            'readable': readable,
            'head':     None if not readable else head,
            'entries':  result.get('checked', 0),
            'intact':   result.get('ok', False),
            'reason':   result.get('reason'),
            'broken_at': result.get('broken_at'),
        }

    def verify_audit_chain_against_anchors(self, sink, log_name, anchors):
        """Check a log's chain against externally published heads (anchors).

        The chain alone proves recorded events were not rewritten, but it cannot prove the
        chain was not truncated, deleted wholesale, or replaced with a different
        internally-valid history: those leave a shorter or fresh chain that verifies clean on
        its own. Only a head published earlier to a party the operator does not control
        disagrees with such a chain. This compares the current chain against those heads.

        Each anchor is a dict with at least 'head' (the published entry_hash) and 'length' (the
        number of records that head covered when published) -- the two fields audit_chain_head
        already emits. Extra fields (sequence, received_at, signatures) are ignored here;
        verifying an anchor's own signature is the caller's job, upstream of this check.

        Returns:
          ok                : internal chain intact AND every anchor satisfied
          internal          : the verify_audit_chain result (integrity of the chain itself)
          anchors_checked   : how many anchors were compared
          anchors_satisfied : how many were found intact at their anchored position
          failures          : list of {length, head, kind, detail}. kind is 'TRUNCATION'
                              (chain now shorter than the anchored point -- includes wholesale
                              deletion, i.e. truncation to zero), 'REPOINT' (a different record
                              occupies the anchored point -- the database was replaced or an
                              earlier prefix rewritten), or 'MALFORMED' (anchor unusable). The
                              truncation/repoint labels are best-effort; the load-bearing
                              outputs are 'ok' and the failure list.
          reason            : one-line summary, or None
        """
        internal = self.verify_audit_chain(sink, log_name)
        try:
            rows = self._audit_rows(sink, log_name)
        except Exception as e:
            return {'ok': False, 'internal': internal,
                    'anchors_checked': len(anchors or []), 'anchors_satisfied': 0,
                    'failures': [], 'reason': f"audit log '{log_name}' could not be read: {e}"}

        # Walk from genesis into an ordered list of heads. Position i (1-based) is the head a
        # length-i anchor published. Derived from links only, so it is order-column-free and
        # identical on SQLite and Postgres. The seen-guard prevents a cycle from looping.
        by_prev = {}
        for r in rows:
            by_prev.setdefault(r['prev_hash'], []).append(r)
        order, cursor, seen = [], self.AUDIT_GENESIS, set()
        while cursor in by_prev and cursor not in seen:
            seen.add(cursor)
            row = by_prev[cursor][0]
            order.append(row['entry_hash'])
            cursor = row['entry_hash']
        cur_len = len(order)

        failures, satisfied = [], 0
        for a in (anchors or []):
            head, length = a.get('head'), a.get('length')
            if head is None or not isinstance(length, int) or length < 1:
                failures.append({'length': length, 'head': head, 'kind': 'MALFORMED',
                                 'detail': "anchor missing a usable 'head' or 'length'"})
            elif length > cur_len:
                failures.append({'length': length, 'head': head, 'kind': 'TRUNCATION',
                                 'detail': (f"chain is now {cur_len} record(s) long but a head was "
                                            f"anchored at length {length}; records that existed "
                                            f"when the anchor was published are gone")})
            elif order[length - 1] == head:
                satisfied += 1
            else:
                failures.append({'length': length, 'head': head, 'kind': 'REPOINT',
                                 'detail': (f"a different record occupies position {length}; the "
                                            f"anchored history was replaced or an earlier prefix "
                                            f"was rewritten")})

        anchors_checked = len(anchors or [])
        ok = bool(internal.get('ok', False)) and not failures
        if not internal.get('ok', False):
            reason = f"chain integrity failed: {internal.get('reason')}"
        elif failures:
            kinds = ", ".join(sorted({f['kind'] for f in failures}))
            reason = f"{len(failures)} of {anchors_checked} anchor(s) failed ({kinds})"
        else:
            reason = None
        return {'ok': ok, 'internal': internal, 'anchors_checked': anchors_checked,
                'anchors_satisfied': satisfied, 'failures': failures, 'reason': reason}

    def verify_audit_chain(self, sink, log_name):
        """Walk an audit log and report whether its hash chain is intact.

        Returns {ok, checked, broken_at, reason}. The walk FOLLOWS THE LINKS from genesis rather
        than reading rows in storage order, so it is identical on SQLite and Postgres and does not
        take the database's word for the sequence. It detects the three things a per-entry digest
        cannot:

          - ALTERED record   -> its recomputed entry_hash no longer matches what is stored
          - DELETED record   -> the walk stops early and records are left stranded
          - REORDERED/FORKED -> two records claim the same predecessor

        NOT detected, deliberately: truncation of the TAIL. Removing the most recent records
        leaves a shorter but internally consistent chain. Only an externally published head
        disagrees with that, which is what anchoring is for and is not claimed here.
        """
        try:
            rows = self._audit_rows(sink, log_name)
        except Exception as e:
            self._audit_rollback(sink)
            return {'ok': False, 'checked': 0, 'broken_at': None,
                    'reason': f"audit log '{log_name}' could not be read: {e}"}
        if not rows:
            return {'ok': True, 'checked': 0, 'broken_at': None, 'reason': None}

        by_prev = {}
        for r in rows:
            by_prev.setdefault(r['prev_hash'], []).append(r)
        for prev, group in by_prev.items():
            if len(group) > 1:
                return {'ok': False, 'checked': 0, 'broken_at': group[1]['audit_id'],
                        'reason': ("chain fork: two records claim the same predecessor -- a "
                                   "record was reordered, duplicated, or re-inserted")}

        checked = 0
        cursor = self.AUDIT_GENESIS
        while cursor in by_prev:
            row = by_prev[cursor][0]
            recomputed = self._audit_chain_hash(cursor, self._chain_payload(row))
            if recomputed != row['entry_hash']:
                return {'ok': False, 'checked': checked, 'broken_at': row['audit_id'],
                        'reason': ("content tampering: this record's contents no longer hash to "
                                   "its stored entry_hash")}
            checked += 1
            cursor = row['entry_hash']

        if checked != len(rows):
            return {'ok': False, 'checked': checked, 'broken_at': None,
                    'reason': (f"chain break: {len(rows) - checked} record(s) are not reachable "
                               f"by following the chain from its start -- a record was removed "
                               f"or its link was rewritten")}
        return {'ok': True, 'checked': checked, 'broken_at': None, 'reason': None}

    def _row_present(self, data_sink, table, field, value):
        """True if the row is present, False if GENUINELY absent (a real 0-count read). RAISES on a
        DB error -- a missing table/column or a locked/unreadable store is NOT 'the row is absent'.
        Silently reporting a failed read as 'absent' would let a real failure read as a confirmed
        lawful erasure -- the false-clean result the audit guarantee cannot afford. 'Could not
        determine' is UNVERIFIABLE, which the callers surface; it is never silently 'absent'."""
        try:
            cur = data_sink.conn.cursor()
            cur.execute(f'SELECT COUNT(*) FROM "{table}" WHERE "{field}" = ?', (value,))
            return cur.fetchone()[0] > 0
        except Exception:
            try: data_sink.conn.rollback()
            except Exception: pass
            raise

    def _tombstone_ref_present(self, data_sink, table, ref):
        """Is the row a tombstone row_ref points at STILL in the data store? Returns
        (present, verifiable). id -> direct lookup; salted-hash -> scan the field and hash each with
        the deployment salt (a match means the erased row is present again). Anything that prevents
        a definite answer -- no salt, a DB error, an unreadable store -- returns (None, False)
        UNVERIFIABLE, NEVER a silent 'absent' (which would falsely confirm the erasure)."""
        field, kind, rv = ref.get('field'), ref.get('kind'), ref.get('ref')
        if kind == 'id':
            try:
                return self._row_present(data_sink, table, 'id', rv), True
            except Exception:
                return None, False      # DB error -> UNVERIFIABLE, never a silent 'absent'
        salt = os.environ.get('MOHIO_AUDIT_SALT')
        if not salt:
            return None, False
        try:
            import hashlib, hmac
            cur = data_sink.conn.cursor()
            cur.execute(f'SELECT "{field}" FROM "{table}"')
            for (v,) in cur.fetchall():
                digest = hmac.new(salt.encode('utf-8'),
                                  f"{table}|{field}|{v}".encode('utf-8'),
                                  hashlib.sha256).hexdigest()
                if digest == rv:
                    return True, True
            return False, True
        except Exception:
            try: data_sink.conn.rollback()
            except Exception: pass
            return None, False          # DB error -> UNVERIFIABLE, never a silent 'absent'

    def verify_tombstones(self, audit_sink, data_sink, log_name='data_audit_log'):
        """(b) Every TOMBSTONE must name a row that is actually ABSENT from its table.

        A TOMBSTONE is the in-chain proof that a missing row was lawfully erased. If a tombstoned
        row is STILL present, the marker is false (or the row returned) and the trail no longer
        means what it says. This walks the TOMBSTONES -- never the data rows: unprompted hunting
        for unaccounted deletions is the enterprise INSERT-logging tier, deliberately NOT claimed
        here -- and confirms each row_ref points at an absent row. Chain integrity is checked first.

        Returns {ok, internal, tombstones, refs_checked, inconsistent[], unverifiable[], reason}.
        ok requires an intact chain, zero inconsistencies, and zero unverifiable refs -- we do not
        claim 'all tombstones verified' when some could not be checked."""
        import json as _json
        internal = self.verify_audit_chain(audit_sink, log_name)
        try:
            rows = self._audit_rows(audit_sink, log_name)
        except Exception as e:
            return {'ok': False, 'internal': internal, 'tombstones': 0, 'refs_checked': 0,
                    'inconsistent': [], 'unverifiable': [],
                    'reason': f"audit log '{log_name}' could not be read: {e}"}
        tombs = [r for r in rows if r.get('event') == 'TOMBSTONE']
        inconsistent, unverifiable, checked = [], [], 0
        for r in tombs:
            try:
                detail = _json.loads(r.get('detail') or '{}')
            except Exception:
                unverifiable.append({'audit_id': r.get('audit_id'),
                                     'reason': 'tombstone detail is not readable JSON'})
                continue
            table = detail.get('table')
            for ref in detail.get('row_refs', []):
                present, verifiable = self._tombstone_ref_present(data_sink, table, ref)
                if not verifiable:
                    unverifiable.append({'table': table, 'kind': ref.get('kind'),
                                         'ref': ref.get('ref'),
                                         'reason': 'salted-hash ref needs MOHIO_AUDIT_SALT to check absence'})
                    continue
                checked += 1
                if present:
                    inconsistent.append({'table': table, 'field': ref.get('field'),
                                         'kind': ref.get('kind'), 'ref': ref.get('ref'),
                                         'reason': 'a tombstoned row is still present'})
        ok = bool(internal.get('ok')) and not inconsistent and not unverifiable
        if not internal.get('ok'):
            reason = f"chain integrity failed: {internal.get('reason')}"
        elif inconsistent:
            reason = f"{len(inconsistent)} tombstoned row(s) still present"
        elif unverifiable:
            reason = f"{len(unverifiable)} tombstone ref(s) unverifiable (missing salt or unreadable detail)"
        else:
            reason = None
        return {'ok': ok, 'internal': internal, 'tombstones': len(tombs),
                'refs_checked': checked, 'inconsistent': inconsistent,
                'unverifiable': unverifiable, 'reason': reason}

    def adjudicate_erasure(self, audit_sink, data_sink, table, field, value,
                           log_name='data_audit_log'):
        """(b) Adjudicate ONE row ON REQUEST -- never by enumerating the data set:

          PRESENT       the row is still in the data store (no lawful erasure claimed)
          ERASED        the row is absent AND a matching tombstone proves lawful erasure
          MISSING       the row is absent with NO tombstone -- unaccounted (possible tampering)
          INCONSISTENT  the row is present BUT a tombstone claims it was erased
          UNVERIFIABLE  a non-id row cannot be referenced without the deployment salt

        MISSING cannot distinguish 'deleted without a tombstone' from 'never existed' -- that needs
        the enterprise creation-log tier. (b) reports exactly what the tombstones can prove."""
        import json as _json
        try:
            ref = self._tombstone_row_ref(table, field, value)
        except MohioRuntimeError as e:
            return {'verdict': 'UNVERIFIABLE', 'row_present': None, 'tombstone_found': None,
                    'row_ref': None, 'reason': str(e)}
        try:
            row_present = self._row_present(data_sink, table, field, value)
        except Exception as e:
            return {'verdict': 'UNVERIFIABLE', 'row_present': None, 'tombstone_found': None,
                    'row_ref': ref, 'reason': f'could not read the data store to adjudicate: {e}'}
        tombstone_found = False
        try:
            rows = self._audit_rows(audit_sink, log_name)
        except Exception as e:
            # A MISSING log table means no tombstone has ever been written -> genuinely no
            # tombstone (proceed). A REAL read error means we cannot tell whether a tombstone
            # exists -> UNVERIFIABLE, never a silent 'no tombstone' (which would falsely read as
            # MISSING/tampering).
            _es = str(e).lower()
            if 'no such table' in _es or 'does not exist' in _es:
                rows = []
            else:
                return {'verdict': 'UNVERIFIABLE', 'row_present': row_present,
                        'tombstone_found': None, 'row_ref': ref,
                        'reason': f'could not read the audit trail to adjudicate: {e}'}
        for r in rows:
            if r.get('event') != 'TOMBSTONE':
                continue
            try:
                detail = _json.loads(r.get('detail') or '{}')
            except Exception:
                continue
            if detail.get('table') != table:
                continue
            if any(rr.get('kind') == ref['kind'] and str(rr.get('ref')) == str(ref['ref'])
                   for rr in detail.get('row_refs', [])):
                tombstone_found = True
                break
        if row_present and tombstone_found:
            verdict, reason = 'INCONSISTENT', 'a tombstone claims erasure but the row is still present'
        elif row_present:
            verdict, reason = 'PRESENT', 'the row is still in the data store'
        elif tombstone_found:
            verdict, reason = 'ERASED', 'the row is absent and a matching tombstone proves lawful erasure'
        else:
            verdict, reason = 'MISSING', 'the row is absent with NO tombstone -- unaccounted (possible tampering)'
        return {'verdict': verdict, 'row_present': row_present, 'tombstone_found': tombstone_found,
                'row_ref': ref, 'reason': reason}

    def _audit_chain_seed(self, sink, log_name):
        """The head of an existing log, or None if empty. Read once per process per log so a
        restart continues the durable chain instead of silently beginning a second one."""
        try:
            rows = self._audit_rows(sink, log_name)
            if not rows:
                return None
            referenced = {r['prev_hash'] for r in rows}
            tips = [r['entry_hash'] for r in rows if r['entry_hash'] not in referenced]
            return tips[0] if len(tips) == 1 else (sorted(tips)[0] if tips else None)
        except Exception:
            return None

    # Serializes the chain head read-modify-write WITHIN a process. Across processes the
    # database enforces it -- see _ensure_chain_uniqueness.
    _AUDIT_CHAIN_LOCK = __import__('threading').RLock()

    # Columns that ARE the chain, so they cannot be part of what the chain covers.
    _CHAIN_LINK_COLUMNS = ('prev_hash', 'entry_hash')

    # The canonical encoding, carried INSIDE the hashed input so a future encoding change
    # produces visibly different hashes attributable to a known cause rather than silently
    # incompatible ones. Bump ONLY with a migration story: chains hashed under a different
    # version cannot be verified with this one, by design.
    AUDIT_ENCODING_VERSION = "mohio-audit-1"

    # WHY A CHAINED AUDIT RECORD IS RETAINED AGAINST AN ERASURE REQUEST
    #
    # Chaining makes the trail append-only in evidence: removing a record breaks every hash after
    # it. That collides with erasure rights, so the basis for retention has to be stated, and one
    # earlier justification was wrong and must not be repeated.
    #
    # WRONG: "the trail records field names, not values, so it holds no personal data to erase."
    # An entry naming a person and showing that an access occurred is individually identifiable
    # -- the fact of treatment is itself protected -- and under GDPR a record carrying an
    # identifier is personal data, pseudonymised or not. Names-not-values MINIMISES what is held
    # (and keeps the trail from becoming a second unguarded copy of the regulated data); it does
    # not put the trail outside the regulation.
    #
    # RIGHT: retention rests on the erasure exceptions -- processing necessary to comply with a
    # legal obligation, and processing necessary to establish, exercise, or defend legal claims.
    # Those are the grounds that survive scrutiny.
    #
    # OPEN, and a genuine gap: if a record must be erased anyway (a stricter state rule, or a
    # specific order), deleting it is indistinguishable from tampering -- verification reports a
    # break either way, which falsely accuses a lawful act. Crypto-shredding is the answer and the
    # chain already supports it (it hashes whatever is stored, so encrypting `detail` before the
    # save makes key destruction a valid redaction with verification intact).

    class AuditContentRefused(Exception):
        """An audit record was refused before the write because it carried a raw value.

        Deliberately NOT a plain write failure. A transient sink error can be tolerated by an
        app under no compliance framework -- the operation proceeds and the failure is surfaced.
        This cannot be tolerated at any tier, because it is not an infrastructure hiccup, it is
        code emitting protected data into a record that may be sealed beyond anyone's reach. The
        developer has to find out now, while the fix is still possible.
        """

    # Patterns for values that must never appear in an audit record. Deliberately narrow: a gate
    # that cries wolf gets disabled, and a disabled gate protects nothing. Each pattern is either
    # self-validating (Luhn) or structurally distinctive enough not to collide with the ids,
    # timestamps, and hex digests an audit record legitimately carries.
    _PRESEAL_PATTERNS = (
        ('email address',
         r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}'),
        ('US social security number',
         r'\b\d{3}-\d{2}-\d{4}\b'),
    )

    @staticmethod
    def _looks_like_card_number(text):
        """A 13-19 digit run that passes the Luhn checksum.

        Luhn is what makes this usable: roughly 90% of arbitrary digit runs fail it, so a
        timestamp, a row count, or an id does not trip the gate while a real card number does.
        """
        import re as _re
        for run in _re.findall(r'(?<!\d)(?:\d[ -]?){13,19}(?!\d)', text):
            digits = [int(c) for c in run if c.isdigit()]
            if not 13 <= len(digits) <= 19:
                continue
            total, parity = 0, len(digits) % 2
            for i, d in enumerate(digits):
                if i % 2 == parity:
                    d *= 2
                    if d > 9:
                        d -= 9
                total += d
            if total % 10 == 0:
                return True
        return False

    def _audit_preseal_check(self, log_name, row):
        """Refuse to write an audit record that carries a raw value where a name belongs.

        THE REASON THIS IS A HARD GATE AND NOT A CONVENTION
        An audit record can be sealed into storage that refuses deletion for the retention
        period. If a bug puts a raw PHI or PCI value into one, that value cannot be removed
        afterwards -- not by the tenant, not by the platform, not by a court order. It becomes a
        standing violation with no remediation path for years.

        Every other correctness failure in the audit system can be fixed and moved on from. This
        one cannot, which is why it is checked BEFORE the write rather than tested for after it,
        and why it fails the write rather than logging a warning.

        The rule it enforces: an audit trail records what happened -- field NAMES, ids, counts,
        classifications, timestamps -- and never the sensitive VALUES it exists to protect, so
        the trail can never become a second unguarded copy of the regulated data.
        """
        import re as _re, json as _json
        # Only the content fields. The chain columns are hex digests and the timestamp is an ISO
        # string; scanning them would produce false positives against data the record must carry.
        #
        # DIGEST COLUMNS ARE EXCLUDED STRUCTURALLY, by name, before anything is scanned. An audit
        # record legitimately carries hashes -- chain links, payload digests, ids -- and a hex
        # string will occasionally contain a digit run that satisfies Luhn purely by chance. That
        # matters more than the small rate suggests, because the refusal is deliberately
        # non-swallowable: a chance collision would fail a real operation over a record
        # containing nothing sensitive at all.
        #
        # By NAME rather than by shape, because shape alone cannot separate them: a truncated
        # 16-character digest and a 16-digit card number are the same length, so any rule wide
        # enough to skip the digest would also skip the card.
        _digest_keys = {'audit_id', 'prev_hash', 'entry_hash', 'payload_hash', 'hash',
                        'digest', 'checksum', 'signature', 'etag'}

        def _strip_digests(value):
            if isinstance(value, dict):
                return {k: _strip_digests(v) for k, v in value.items()
                        if not (k.lower() in _digest_keys or k.lower().endswith('_hash'))}
            if isinstance(value, list):
                return [_strip_digests(v) for v in value]
            return value

        _parts = []
        for _k in ('event', 'agent', 'detail'):
            _v = row.get(_k, '')
            if _k == 'detail' and isinstance(_v, str) and _v.strip().startswith('{'):
                try:
                    _v = _json.dumps(_strip_digests(_json.loads(_v)), default=str)
                except Exception:
                    pass                      # not JSON after all; scan it as written
            _parts.append(str(_v))
        haystack = ' '.join(_parts)
        if not haystack.strip():
            return
        # Belt and braces for a digest embedded somewhere the key name did not reveal. Safe at
        # 32+: no sensitive value is 32 contiguous hex characters -- a card is 13-19 decimal
        # digits, an SSN is 9 -- so this removes noise without creating a hiding place.
        haystack = _re.sub(r'\b[0-9a-fA-F]{32,}\b', ' ', haystack)
        for label, pattern in self._PRESEAL_PATTERNS:
            if _re.search(pattern, haystack):
                raise MohioInterpreter.AuditContentRefused(
                    f"audit write to '{log_name}' refused: the record appears to contain a raw "
                    f"{label}. An audit trail records field NAMES and classifications, never the "
                    f"values it exists to protect.\n"
                    f"This is refused before the write because an audit record can be sealed "
                    f"into storage that refuses deletion for years -- a value written here may "
                    f"be impossible to remove afterwards, by anyone.\n"
                    f"Record the field name and its classification instead "
                    f"(for example {{\"ssn\": \"[phi]\"}}), not the value.")
        if self._looks_like_card_number(haystack):
            raise MohioInterpreter.AuditContentRefused(
                f"audit write to '{log_name}' refused: the record appears to contain a raw "
                f"payment card number (a digit run passing the Luhn checksum). An audit trail "
                f"records field NAMES and classifications, never the values it exists to "
                f"protect.\n"
                f"This is refused before the write because an audit record can be sealed into "
                f"storage that refuses deletion for years.\n"
                f"Record the field name and its classification instead "
                f"(for example {{\"card\": \"[pci]\"}}), not the value.")

    def _audit_chained_save(self, sink, log_name, row):
        """Save an audit row with its chain links filled in, and return the row.

        Every audit writer goes through here. Chaining only one writer would mean the log that
        happened to be chained looked tamper-evident while the ones beside it were not -- and a
        claim of immutability is only as true as its weakest log.

        This is also the pre-seal gate: the last point at which an audit record can still be
        refused. After this it may be replicated and sealed, and a bad value in it becomes
        permanent.

        CONCURRENCY. Reading the head, hashing against it, writing, and advancing the head is one
        indivisible operation. Without that, two threads read the same head, both hash against
        it, and both write -- producing two records claiming the same predecessor. The chain
        forks, verification reports a break, and nothing tampered with anything. Measured: four
        threads writing twenty records each produced eighty rows and a forked chain every time.

        The lock is per process, which is the correct scope for the documented single-worker
        model. Across PROCESSES the same race exists and a lock cannot reach it -- that needs
        ordering the database enforces (a uniqueness constraint on prev_hash, so the losing
        writer is refused and retries against the new head). Multi-worker deployment must not
        happen before that lands; the runtime documents single-worker for exactly this reason.
        """
        self._audit_preseal_check(log_name, row)
        with MohioInterpreter._AUDIT_CHAIN_LOCK:
            return self._audit_chained_save_locked(sink, log_name, row)

    @staticmethod
    def _assert_audit_text_roundtrip(sink, log_name, row, stored):
        """The chain compares str(written) against str(read-back). Enforce that, do not assume it.

        Verification recomputes each record's hash from what the database returns. That only
        matches what was written while every audit column round-trips as an identical string,
        which holds today because audit columns are created TEXT on every backend -- and nothing
        checked it.

        The failure it prevents is the worst kind of false positive: a typed column, or a backend
        returning a native type, would make a legitimately written record recompute to a different
        hash and be reported as TAMPERED. The system would accuse itself over a schema change, and
        the accusation would be indistinguishable from a real one.

        Checked on the first write to each log per process -- enough to catch a schema that cannot
        round-trip, without paying for it on every record.
        """
        if not hasattr(MohioInterpreter, '_AUDIT_RT_CHECKED'):
            MohioInterpreter._AUDIT_RT_CHECKED = set()
        key = (id(sink), log_name)
        if key in MohioInterpreter._AUDIT_RT_CHECKED:
            return
        MohioInterpreter._AUDIT_RT_CHECKED.add(key)
        try:
            rows = MohioInterpreter._audit_rows(sink, log_name)
        except Exception:
            return                      # unreadable is a different failure, reported elsewhere
        match = next((r for r in rows if r.get('entry_hash') == stored.get('entry_hash')), None)
        if match is None:
            return
        drift = [c for c, v in stored.items()
                 if c not in MohioInterpreter._CHAIN_LINK_COLUMNS
                 and v is not None
                 and str(match.get(c)) != str(v)]
        if drift:
            raise MohioRuntimeError(
                f"audit log '{log_name}' cannot hold a verifiable chain: column(s) "
                f"{', '.join(drift)} do not round-trip as the same string.\n"
                f"Verification recomputes each record's hash from what the database returns, so a "
                f"column that changes on the way back would make correctly written records report "
                f"as TAMPERED. Audit columns must be TEXT.")

    @staticmethod
    def _ensure_audit_table(sink, log_name):
        """Create the audit log, tolerating a concurrent creator.

        `CREATE TABLE IF NOT EXISTS` is NOT atomic against a simultaneous create on Postgres:
        two sessions racing can still collide, and the loser's failure poisons its transaction so
        every subsequent write in that process is refused. The visible symptom is a busy log
        silently shedding the records of whichever process lost the create -- audit records lost
        to a race, which for an audit trail is a correctness failure and not a hiccup.

        So: attempt, and if it fails, clear the transaction and ask the database whether the table
        is there. If it is, the race was won by someone else and there is nothing wrong. Only a
        genuinely absent table is an error.
        """
        from mohio_audit_grades import canonical_audit_columns as _cac
        # Once per log per process. This used to run on EVERY write, so every record re-raced the
        # CREATE against every other process -- which is why a busy log kept shedding records long
        # after the table existed. The table does not stop existing between writes.
        # The marker lives ON the sink, not in a process-wide set keyed by id(sink).
        # id() is a MEMORY ADDRESS and Python reuses addresses after a garbage collection,
        # so a brand-new database whose sink happened to land where an earlier, unrelated
        # sink had been was treated as already prepared. It returned here without creating
        # anything, another path then made the table from one record's own keys, and the
        # result was an audit log with 8 columns instead of the canonical 18 -- a log that
        # accepted writes and could never be read back or verified.
        # It failed roughly one run in twenty, entirely dependent on whether the allocator
        # reused an address, which is why it read as a flaky test for weeks.
        # Tying the marker to the sink's own lifetime makes reuse impossible to confuse.
        _ready_logs = getattr(sink, '_mohio_audit_ready', None)
        if _ready_logs is None:
            _ready_logs = set()
            try:
                sink._mohio_audit_ready = _ready_logs
            except Exception:
                _ready_logs = None      # a sink that refuses attributes just re-creates
        if _ready_logs is not None and log_name in _ready_logs:
            return
        # Serialize create-and-reconcile. sink.ensure_table is CREATE-then-reconcile (PRAGMA read
        # of the columns, then ALTER ADD COLUMN for any missing). Two threads racing the SAME log
        # each read the column set before either adds, then both ADD the same column, and the loser
        # throws "duplicate column name: audit_id" -- an audit write lost to a schema-setup race,
        # which for an audit trail is a correctness failure (this was the ~1/9 flaky test). The
        # chain-head lock is already the in-process serialization point for a log; taking it here
        # makes the whole setup atomic per log, the same remedy as the chain-head race. The lock is
        # per process; the retry-on-collision loop below still covers the cross-PROCESS Postgres
        # race a lock cannot reach.
        with MohioInterpreter._AUDIT_CHAIN_LOCK:
            if _ready_logs is not None and log_name in _ready_logs:
                return                  # another thread finished the setup while we held for the lock
            # The window is the winner's CREATE not yet being COMMITTED: the loser rolls back, looks
            # for the table, and does not see it yet. Retrying past that window costs milliseconds;
            # not retrying costs audit records.
            for _attempt in range(12):
                try:
                    sink.ensure_table(log_name, _cac())
                    if _ready_logs is not None:
                        _ready_logs.add(log_name)
                    return
                except Exception:
                    MohioInterpreter._audit_rollback(sink)
                    try:
                        cur = MohioInterpreter._audit_query(
                            sink, f'SELECT "audit_id" FROM "{log_name}" LIMIT 1')
                        cur.fetchall()
                        if _ready_logs is not None:
                            _ready_logs.add(log_name)
                        return                  # someone else created it; that is fine
                    except Exception:
                        MohioInterpreter._audit_rollback(sink)
                        import time as _t, random as _r
                        _t.sleep(min(0.15, 0.02 * (_attempt + 1)) * (0.5 + _r.random()))
            sink.ensure_table(log_name, _cac())  # final attempt: let the real error surface

    @staticmethod
    def _ensure_chain_uniqueness(sink, log_name):
        """One record may claim a given predecessor. The DATABASE enforces it.

        The in-process lock serialises writers inside one process and cannot reach across
        processes: two workers read the same head, both hash against it, and both write, and the
        chain forks with nothing tampered. A unique index on `prev_hash` makes that physically
        impossible -- the second writer is refused by the engine, whichever process it is in.

        This is what makes multi-worker safe. Cheap, and idempotent, so it costs one statement per
        log per process.
        """
        if not hasattr(MohioInterpreter, '_AUDIT_UX_DONE'):
            MohioInterpreter._AUDIT_UX_DONE = set()
        key = (id(sink), log_name)
        if key in MohioInterpreter._AUDIT_UX_DONE:
            return
        MohioInterpreter._AUDIT_UX_DONE.add(key)
        idx = f"ux_{log_name}_prev_hash"[:60]
        try:
            cur = sink.conn.cursor()
            cur.execute(f'CREATE UNIQUE INDEX IF NOT EXISTS "{idx}" '
                        f'ON "{log_name}" ("prev_hash")')
            sink.conn.commit()
        except Exception:
            # An existing log may already contain a fork, in which case the index cannot be
            # created. That is worth knowing but must not stop the write: verification reports
            # the fork, which is the honest outcome.
            MohioInterpreter._audit_rollback(sink)

    def _audit_chained_save_locked(self, sink, log_name, row):
        """The chain advance itself. Only ever called holding _AUDIT_CHAIN_LOCK."""
        if not hasattr(self, '_audit_chain_head'):
            self._audit_chain_head = {}
        head_key = (id(sink), log_name)
        if head_key not in self._audit_chain_head:
            self._audit_chain_head[head_key] = (
                self._audit_chain_seed(sink, log_name) or self.AUDIT_GENESIS)
        self._ensure_chain_uniqueness(sink, log_name)
        # Retry against the head that actually won. The database refuses a second record claiming
        # the same predecessor, so a loser here has not failed -- it has been told the chain moved
        # underneath it, and the correct response is to re-read the head and link to that.
        # Contention is expected under multiple writers, and a lost audit record is a
        # correctness failure, not a performance one -- so retry generously with jittered
        # backoff rather than giving up early. Without the backoff, retries collide with each
        # other and a busy log sheds records it should have kept.
        import random as _rand, time as _time
        for _attempt in range(40):
            if _attempt:
                _time.sleep(min(0.05, 0.002 * _attempt) * (0.5 + _rand.random()))
            prev_hash = self._audit_chain_head[head_key]
            entry_hash = self._audit_chain_hash(prev_hash, self._chain_payload(row))
            row['prev_hash'] = prev_hash
            row['entry_hash'] = entry_hash
            try:
                from mohio_audit_grades import chained_write as _chained
                with _chained():
                    sink.save(log_name, row)
                break
            except Exception as _e:
                if 'unique' not in str(_e).lower() and 'duplicate' not in str(_e).lower():
                    raise
                self._audit_rollback(sink)
                fresh = self._audit_chain_seed(sink, log_name)
                if fresh is None or fresh == prev_hash:
                    raise
                self._audit_chain_head[head_key] = fresh
        else:
            raise MohioRuntimeError(
                f"audit write to '{log_name}' could not obtain a chain position after repeated "
                f"contention. Another writer is advancing the chain faster than this one can "
                f"link to it.")
        self._audit_chain_head[head_key] = entry_hash
        self._assert_audit_text_roundtrip(sink, log_name, row, row)
        return row

    @staticmethod
    def _chain_payload(row_like):
        """The exact dict a record's entry_hash is computed over.

        Built from the FULL canonical column set, not the handful of fields a particular writer
        happens to populate. Hashing only some columns leaves the rest unprotected: the ai.decide
        record carries `result`, `confidence`, `inputs`, and `sector`, and if those sit outside
        the hash then the decision itself can be rewritten without breaking the chain -- which is
        precisely the thing the chain exists to prevent.

        Writer and verifier both call this, so the two cannot drift. A missing column reads as
        None on both sides (absent at write, NULL at read), so the payload is identical either
        way.
        """
        from mohio_audit_grades import canonical_audit_columns as _cac
        cols = [c for c in _cac() if c not in MohioInterpreter._CHAIN_LINK_COLUMNS]
        get = row_like.get if hasattr(row_like, 'get') else (lambda k, d=None: row_like[k])
        out = {}
        for c in cols:
            try:
                v = get(c, None)
            except Exception:
                v = None
            out[c] = None if v is None else str(v)
        return out

    @staticmethod
    def _audit_chain_hash(prev_hash, payload):
        """entry_hash = H(encoding_version || prev_hash || canonical(payload)).

        Including the predecessor's hash is the point: it makes each record depend on every
        record before it, so altering or removing an earlier entry invalidates every hash that
        follows. A per-entry digest that does not include the predecessor (what `audit_id` is)
        proves a record's own integrity and nothing about the sequence -- delete a row and no
        arithmetic anywhere disagrees.

        The full 256-bit digest is returned. `audit_id` is truncated to 64 bits, which is fine
        for an identifier and NOT fine for anything load-bearing in a tamper-evidence claim, so
        the truncated value is never used as a chain link -- only as one of the hashed fields.
        """
        import hashlib as _hl, json as _json
        # Every value in `payload` is already a string by construction, so `default=str` is a
        # backstop and not the normal path. It stays as a guard: a non-string arriving here must
        # still hash rather than raise, because an audit write must not fail on a formatting edge.
        canonical = _json.dumps(payload, sort_keys=True, default=str, separators=(',', ':'),
                                ensure_ascii=False)
        blob = f"{MohioInterpreter.AUDIT_ENCODING_VERSION}\x1f{prev_hash}\x1f{canonical}"
        return _hl.sha256(blob.encode('utf-8')).hexdigest()

    # The genesis predecessor for an empty log. A fixed, recognisable value so a verifier can
    # tell "this is the first record" apart from "the predecessor is missing".
    AUDIT_GENESIS = "0" * 64

    def _audit_scope_statement(self, log_name, ctx):
        """Write, once per log, a record saying what this chain does and does not cover.

        A chain with no scope statement implies completeness it cannot have. Mohio records
        what passes through Mohio: requests this app served, and handoffs it made. It cannot
        record a read that never reached it -- a browser talking straight to a database, a
        second backend writing to the same tables -- because it was not there.

        That gap is the dangerous kind: the chain is internally perfect, every hash links,
        and data flowed around the side unseen. Stating the boundary turns an incomplete
        record into an honestly bounded one, which is a far stronger position in front of a
        reviewer than a trail that quietly claims more than it saw.

        This states scope. It does not detect an unseen path, and must never be read as
        evidence that none exists.
        """
        if log_name in getattr(self, '_audit_scoped', set()):
            return
        if not hasattr(self, '_audit_scoped'):
            self._audit_scoped = set()
        self._audit_scoped.add(log_name)
        _scope_entry = self._audit_event(log_name, {
            'event':   'chain_scope',
            'covers':  ('requests served by this Mohio app, and handoffs this app made to '
                        'outside destinations'),
            'excludes': ('any access that did not pass through this app -- a client reading '
                         'the database directly, or another service writing to it. Those are '
                         'not recorded here and their absence is not evidence they did not '
                         'happen'),
            'basis':   'boundary records are written for calls this app executed',
        }, ctx)
        # _audit_event stamps and returns the record; appending is the caller's job.
        self._audit_logs.setdefault(log_name, []).append(_scope_entry)

    def _audit_event(self, log_name, entry, ctx):
        """Stamp a governance audit entry and append it to the durable, HASH-CHAINED audit
        trail (the connected db) when one is present.

        Each record carries `entry_hash = H(prev_hash || content)` and the `prev_hash` of the
        record before it, so altering, deleting, or reordering any record invalidates every hash
        after it. `verify_audit_chain` walks a log and reports the break. The older `audit_id` is
        a per-entry digest and is retained for identity -- on its own it proves a record's own
        integrity and NOTHING about the sequence, which is why the chain exists.

        NOT claimed: tail truncation (removing the most recent records) leaves a shorter but
        internally consistent chain. Detecting that needs external anchoring, which is separate
        work and is deliberately not asserted here.

        Returns the enriched entry so the caller keeps it in memory too. This is what makes agent
        governance events -- tool refusals, budget cutoffs, sector refusals -- as logged and
        traceable as ai.decide, not merely held in memory for the length of one run."""
        import hashlib as _hl, json as _json, datetime as _dt
        entry = dict(entry)
        ts = _dt.datetime.utcnow().isoformat() + 'Z'
        entry['ts'] = ts
        entry['audit_id'] = _hl.sha256(
            f"{log_name}:{_json.dumps(entry, sort_keys=True, default=str)}:{ts}".encode()
        ).hexdigest()[:16]
        db = ctx.get_connection('db') if ctx is not None else None
        # WHERE the required audit grade comes from: the activated compliance FRAMEWORKS, not
        # the sector's price tier. A profile declares `compliance: [hipaa, pci-dss, ...]`; each
        # framework independently demands a minimum audit grade; the highest wins. This is what
        # makes compliance modular and enterprise-configurable -- the client composes the
        # frameworks they are subject to and the audit posture falls out automatically.
        req_grade, sinks = self._audit_requirement(ctx)

        wrote_durable = False
        wrote_at_grade = False   # did a sink MEETING the required grade accept the record?
        last_err = None
        from mohio_audit_grades import canonical_audit_columns as _cac, satisfies as _sat
        # Write to EVERY configured audit sink (redundancy). A single sink's transient failure is
        # covered if a redundant sink succeeds. Enterprise/commercial subscribers keep sinks keyed
        # to one another so a real outage on one does not lose the record or halt the operation.
        for sink in sinks:
            try:
                self._ensure_audit_table(sink, log_name)
                # ── hash chain ────────────────────────────────────────────────────────────
                # Link this record to its predecessor. The head is held per (sink, log) and
                # seeded from what is already durable, so a restart continues the existing
                # chain rather than silently starting a second one from genesis.
                _saved = self._audit_chained_save(sink, log_name, {
                    'audit_id': entry['audit_id'],
                    'ts':       ts,
                    'event':    str(entry.get('event', '')),
                    'agent':    str(entry.get('agent', '')),
                    'detail':   _json.dumps(entry, default=str),
                })
                entry['prev_hash']  = _saved['prev_hash']
                entry['entry_hash'] = _saved['entry_hash']
                # The sink is CLASSIFIED, not asked. Both of these used to default to "adequate"
                # when unset -- `_mohio_durable, True` and `_mohio_grade, 'durable'` -- so a sink
                # nobody had graded was treated as meeting the requirement. That is precisely the
                # silent non-durability failure: an in-memory store accepted compliance writes,
                # reported success, and lost them, with nothing detectable at the time.
                from mohio_audit_grades import classify_sink as _classify
                _sk_grade, _sk_durable, _sk_why = _classify(sink)
                wrote_durable = wrote_durable or _sk_durable
                # A sink's grade defaults to 'durable' when unstated (a plain db sink is durable
                # but not append-only/WORM). If it meets the required grade, the record landed
                # at grade and no degradation is needed.
                sink_grade = _sk_grade
                if _sat(sink_grade, req_grade):
                    wrote_at_grade = True
            except MohioInterpreter.AuditContentRefused:
                # NEVER swallowed. A refused record means code emitted a protected value into
                # something that may be sealed beyond reach; tolerating it here would hide the
                # one failure that cannot be remediated after the fact.
                raise
            except Exception as e:
                last_err = e

        if req_grade != 'none':
            import sys as _sys
            from mohio_audit_grades import classify_sink as _cls
            # STRUCTURALLY incapable is the same condition as absent, and must be treated the
            # same. A sink that is bound but can never be durable -- an in-memory store, an
            # unrecognisable binding -- is not a transient failure that a retry or a redundant
            # path can recover. It is a misconfiguration that will lose every record it is given
            # while reporting success, which is the silent non-durability case: the guarantee
            # reads as true for as long as nobody checks.
            _incapable = None
            if sinks:
                _graded = [_cls(s) for s in sinks]
                if not any(d for (_g, d, _r) in _graded):
                    _incapable = '; '.join(r for (_g, _d, r) in _graded)
            if not sinks or _incapable:
                # STRUCTURAL absence: a framework requires durable+ audit and NO sink is
                # configured at all, or none that can hold a record. This is the
                # encryption-key-parallel case and is meant to be caught at check/deploy, before
                # the app ever serves traffic -- so it halts nothing in production. If it reaches
                # here at runtime it is a genuine misconfiguration and must not be papered over.
                _detail = (f" The bound sink cannot hold records: {_incapable}."
                           if _incapable else "")
                raise _Raise(
                    error_name='audit.no_durable_store',
                    message=("A durable, append-only audit store is required by an activated "
                             "compliance framework, and none is connected. Logging to temporary "
                             "storage would satisfy the letter of the code and then lose the "
                             "records, which is worse than making no claim at all." + _detail),
                    line=None,
                    hint="Connect a durable audit store, or run this app on the Certified tier.")
            if not wrote_durable:
                # RUNTIME write failure with sinks present. We do NOT abort the audited
                # operation -- halting a live institution on a transient audit-store blip is the
                # wrong failure mode. The record is retried/mirrored across the redundant sinks
                # above; if every sink failed right now, we alert LOUDLY and let the operation
                # proceed, because the alternative (stopping a bank mid-transaction on a network
                # hiccup) is worse. Abort is reserved for the true catastrophe where no durable
                # substrate anywhere can hold the record -- which the platform's WAL/redundancy
                # layer is responsible for detecting; see the design-chat audit-recovery spec.
                #
                # DESIGN-CHAT SEAM: retry-with-backoff, local durable WAL buffer, circuit-breaker,
                # and the "no durable substrate at all -> abort" catastrophe detection plug in
                # here. This interim guarantees the failure is never silent and never halting.
                print(f"  [audit] ALERT: no audit sink accepted the record for {log_name} "
                      f"(required grade: {req_grade}). last error: {last_err}. The operation "
                      f"proceeded; the record must be reconciled from the redundant/WAL path. "
                      f"This is a compliance-affecting event -- page the on-call.",
                      file=_sys.stderr)
                entry['_audit_degraded'] = True
                self._raise_degraded_incident(
                    log_name, entry.get('audit_id'), req_grade, 'none',
                    f"no audit sink accepted the record: {last_err}", ctx)
            elif not wrote_at_grade:
                # DURABLY written, but only BELOW the required grade (e.g. landed on a `durable`
                # sink where the framework requires `append_only`/`worm`). The record is
                # NOT lost, so we do not abort or halt -- we mark it degraded and it is queued to
                # be reconciled UP to the required grade by the platform's reconciliation worker.
                # "Degraded" always means durably recorded, below grade, pending upgrade -- which
                # is exactly the lifecycle a degraded-event incident tracks.
                print(f"  [audit] DEGRADED: record for {log_name} landed below the required "
                      f"grade (required: {req_grade}). It is durable but must be reconciled up "
                      f"to grade. Tracking as a degraded incident.",
                      file=_sys.stderr)
                entry['_audit_degraded'] = True
                entry['_audit_required_grade'] = req_grade
                self._raise_degraded_incident(
                    log_name, entry.get('audit_id'), req_grade, 'durable',
                    "record landed on a sink below the required grade", ctx)
        elif not wrote_durable and last_err is not None:
            # No framework requires durable audit (community / no-compliance app). Still never
            # swallow the failure -- surface it, but the operation proceeds.
            import sys as _sys
            print(f"  [audit] WARNING: audit write failed for {log_name}: {last_err}. "
                  f"The audit record for this action was NOT persisted.",
                  file=_sys.stderr)
        return entry

    def _raise_degraded_incident(self, audit_table, orphaned_audit_id, required_grade,
                                 written_grade, reason, ctx):
        """Record a degraded-audit incident and emit it to the alert sink.

        Built to the design-chat degraded-event schema (SPEC-degraded-events §2). A degraded write
        (durable but below the required grade, or no sink accepted it) produces a durable,
        append-only incident record with a lifecycle (raised -> reconciling -> reconciled /
        unreconcilable). The platform's reconciliation worker advances the state and audits each
        transition; the compiler's job is to RAISE the incident honestly and never silently.

        Two outputs, and neither depends on the other:
          - the incident is written to the durable `audit_incident_log` (so it is queryable and
            survives even if no alert sink is bound), and
          - it is emitted to the registered alert sink (PagerDuty/Slack/webhook) if one is bound.
        The alert sink's absence never suppresses the incident record.
        """
        import uuid as _uuid, datetime as _dt, json as _json, sys as _sys
        frameworks = getattr(ctx, '_sector_compliance', None) if ctx is not None else None
        sector = getattr(ctx, '_sector', None) if ctx is not None else None
        session_id, member_id = self._audit_actor(ctx)
        incident = {
            'incident_id':             str(_uuid.uuid4()),
            'raised_ts':               _dt.datetime.utcnow().isoformat() + 'Z',
            'audit_table':             audit_table,
            'orphaned_audit_id':       orphaned_audit_id,
            'required_grade':          required_grade,
            'written_grade':           written_grade,
            'reason':                  str(reason),
            'sector':                  sector,
            'frameworks':              list(frameworks) if frameworks else [],
            'session_id':              session_id,
            'member_id':               member_id,
            'state':                   'raised',
            'reconciled_ts':           None,
            'reconciled_to_grade':     None,
            'reconciliation_audit_id': None,
        }
        # 1. Durable incident record -- best-effort to the app db incident log. Never raises: an
        #    incident-logging failure must not itself halt the operation (the primary alert has
        #    already fired to stderr). If the platform binds a graded audit sink, the incident log
        #    lands there too via the same naming convention (`*_audit_log` -> is_audit_table True).
        try:
            db = ctx.get_connection('db') if ctx is not None else None
            if db:
                from mohio_audit_grades import canonical_audit_columns as _cac
                db.ensure_table('audit_incident_log', _cac())
                self._audit_chained_save(db, 'audit_incident_log', {
                    'audit_id': incident['incident_id'][:16],
                    'ts':       incident['raised_ts'],
                    'event':    'AUDIT_DEGRADED',
                    'agent':    audit_table,
                    'detail':   _json.dumps(incident, default=str),
                })
        except Exception as _e:
            print(f"  [audit] note: could not persist degraded incident for {audit_table}: {_e}",
                  file=_sys.stderr)
        # 2. Emit to the alert sink if one is bound. Absence is not silence -- the incident is
        #    already recorded above and shown on the dashboard; the alert sink is only the page.
        _sink = type(self)._alert_sink
        if _sink is not None:
            try:
                _sink(dict(incident))
            except Exception as _e:
                print(f"  [audit] note: alert sink raised on degraded incident: {_e}",
                      file=_sys.stderr)
        return incident

    def _audit_requirement(self, ctx):
        """Return (required_grade, sinks).

        required_grade: the highest audit grade any activated compliance framework requires,
        derived from `ctx._sector_compliance` (the framework list) via the modular
        framework->grade mapping. NOT keyed on the sector's license tier.

        sinks: the list of audit sinks to write to. Today this is the connected `db` (one sink);
        the audit-sink seam and platform redundancy bind additional/dedicated durable sinks here.
        Each sink may carry `_mohio_durable` (bool) and `_mohio_grade` (str); absent = assume a
        plain durable db sink.

        DESIGN-CHAT SEAM: when the audit-sink seam lands, this method returns the bound graded
        sinks (primary + redundant + WAL) instead of the app db, and enforces that at least one
        sink meets `required_grade`. For now it returns the app db so behavior is unchanged for
        non-compliance apps and correct-direction for compliance apps.
        """
        try:
            from mohio_audit_grades import required_grade as _rg
            frameworks = getattr(ctx, '_sector_compliance', None) if ctx is not None else None
            grade, unknown = _rg(frameworks)
            if unknown:
                import sys as _sys
                print(f"  [audit] note: unrecognized compliance framework(s) {unknown}; "
                      f"their audit-grade requirement is unknown and not enforced. Add them to "
                      f"mohio_audit_grades.FRAMEWORK_AUDIT_GRADE.", file=_sys.stderr)
        except Exception:
            grade = 'none'
        # Sinks: a registered audit-sink provider (managed/commercial: dedicated graded, governed
        # sinks -- e.g. a Postgres `audit` schema with append-only grants, or WORM storage) takes
        # precedence. Open core falls back to the tenant's app db, unchanged. The provider is where
        # physical placement and grading live; the compiler stays backend-agnostic.
        sinks = None
        _provider = type(self)._audit_sink_provider
        if _provider is not None:
            try:
                provided = _provider(ctx)
                if provided:
                    sinks = list(provided)
            except Exception as _e:
                import sys as _sys
                print(f"  [audit] audit-sink provider failed ({_e}); falling back to app db.",
                      file=_sys.stderr)
        if sinks is None:
            db = ctx.get_connection('db') if ctx is not None else None
            sinks = [db] if db else []
        return grade, sinks

    def _audit_actor(self, ctx):
        """Best-effort actor for an audit entry: the session id and member id from
        the current session, when one is set. Returns ('', '') when unknown, so the
        trail still records that a change happened even if the actor wasn't named."""
        try:
            sess = ctx.get('session')
            sp = sess.to_python() if isinstance(sess, MohioValue) else sess
            if isinstance(sp, dict):
                return (str(sp.get('id') or sp.get('session_id') or ''),
                        str(sp.get('member_id') or sp.get('user_id') or ''))
        except Exception:
            pass
        return ('', '')

    def _exec_PurposeBlock(self, node, ctx):
        """purpose "X" ... purpose: done -- assert a use-purpose. Inside, a direct [pii]
        field reference at a use/egress point must be collected for X, or it fails loud."""
        self._purpose_scope.append(node.purpose)
        try:
            return self._exec_block(node.body, ctx)
        finally:
            self._purpose_scope.pop()

    def _walk_for_purpose_fields(self, v, out):
        if v is None:
            return
        if type(v).__name__ == 'DottedName':
            parts = getattr(v, 'parts', None) or []
            if parts and str(parts[-1]) in self._field_purposes:
                out.append(str(parts[-1]))
            return
        if isinstance(v, (list, tuple)):
            for x in v:
                self._walk_for_purpose_fields(x, out)
            return
        if hasattr(v, '__dict__'):
            for x in vars(v).values():
                self._walk_for_purpose_fields(x, out)

    def _check_purpose(self, node, ctx):
        """Direct-use enforcement: if inside a purpose scope, any [pii] field referenced
        directly here must have been collected for the asserted purpose, else fail loud."""
        if node is None or not self._purpose_scope or not self._field_purposes:
            return
        asserted = self._purpose_scope[-1]
        seen = []
        self._walk_for_purpose_fields(node, seen)
        for fld in seen:
            allowed = self._field_purposes.get(fld, set())
            if asserted not in allowed:
                allowed_str = "', '".join(sorted(allowed)) if allowed else '(no purpose)'
                raise MohioRuntimeError(
                    f"'{fld}' was collected for purpose '{allowed_str}'. Using it under "
                    f"purpose '{asserted}' violates purpose limitation (GDPR Art. 5(1)(b)). "
                    f"Use a field collected for '{asserted}', or record consent for it.")

    def _check_purpose_value(self, val, ctx):
        """Derived-use enforcement: a value copied or built from a [pii] field carries the
        field's purposes (taint); under a purpose scope it must satisfy the asserted purpose
        too, not only when the field is referenced directly."""
        if not self._purpose_scope:
            return
        purposes = getattr(val, '_purposes', None) if isinstance(val, MohioValue) else None
        if not purposes:
            return
        asserted = self._purpose_scope[-1]
        if asserted not in purposes:
            allowed_str = "', '".join(sorted(purposes))
            raise MohioRuntimeError(
                f"a value collected for purpose '{allowed_str}' is being used under purpose "
                f"'{asserted}', which violates purpose limitation (GDPR Art. 5(1)(b)). Use a "
                f"value collected for '{asserted}', or record consent for it.")
        for fld in sorted(getattr(val, '_purpose_fields', None) or {'(derived)'}):
            self._audit_purpose_use(fld, asserted, purposes, ctx)

    def _audit_purpose_use(self, field, asserted, allowed, ctx):
        """Log an ALLOWED [pii] use under a declared purpose to the same durable,
        hash-chained trail the reads and writes use. Compliance evidence: who used which
        field for what purpose, when. The field name (or '(derived)') + the asserted
        purpose + the field's permitted set. Never the value."""
        session_id, member_id = self._audit_actor(ctx)
        entry = {
            'event':            'PURPOSE_USE',
            'field':            field,
            'purpose':          asserted,
            'allowed_purposes': sorted(allowed),
            'session_id':       session_id,
            'member_id':        member_id,
        }
        enriched = self._audit_event('data_audit_log', entry, ctx)
        self._audit_logs.setdefault('data_audit_log', []).append(enriched)
        return enriched

    def _audit_data_access(self, operation, table, rows, ctx):
        """Audit-on-access: log a read that RETURNED a [phi] or [pci] field to the same
        durable, hash-chained trail the writes use. HIPAA requires logging every access to
        health data; PCI DSS requirement 10 requires logging every access to cardholder
        data. Field NAMES only, never values; the row count, the actor, and the time. The
        tag carries this on its own, sector or not; a sector adds the certified claim and
        org-wide breadth, it is not what turns the access log on. No tagged field in the
        result, no entry."""
        phi_set = self._phi_fields or set()
        pci_set = self._pci_fields or set()
        if not phi_set and not pci_set:
            return None
        row_list = rows if isinstance(rows, list) else ([rows] if rows else [])
        keys = {k for r in row_list if isinstance(r, dict) for k in r.keys()}
        phi_accessed = sorted(keys & phi_set)
        pci_accessed = sorted(keys & pci_set)
        if not phi_accessed and not pci_accessed:
            return None
        session_id, member_id = self._audit_actor(ctx)
        entry = {
            'event':      'DATA_ACCESS',
            'operation':  operation,
            'table':      table,
            'session_id': session_id,
            'member_id':  member_id,
            'count':      len(row_list),
        }
        if phi_accessed:
            entry['phi_fields'] = phi_accessed
        if pci_accessed:
            entry['pci_fields'] = pci_accessed
        enriched = self._audit_event('data_audit_log', entry, ctx)
        self._audit_logs.setdefault('data_audit_log', []).append(enriched)
        return enriched

    def _audit_data_change(self, operation, table, ctx, record_id=None,
                           match_fields=None, fields=None, count=None):
        """Under an active sector, record a data mutation in the durable audit
        trail: which operation, which table, which record or match, which fields
        were touched, by whom, and when. Only field NAMES and a surrogate record
        id are recorded -- never the written values, and never the lookup values a
        record was matched on -- so a compliance audit trail can never become a
        second, unguarded copy of the sensitive data it exists to protect. With no
        active sector there is no automatic data audit (the developer can still
        audit explicitly). This is what makes a sector profile's promise to log
        data writes true at runtime, not merely declared on paper."""
        fields_tagged = bool(fields and self._encrypted_fields
                             and {str(f) for f in fields} & self._encrypted_fields)
        table_tagged = table in self._tagged_tables
        if (getattr(ctx, '_sector_profile', None) is None
                and not fields_tagged and not table_tagged):
            return None
        if fields_tagged:
            self._tagged_tables.add(table)   # remember: this table holds sensitive data
        session_id, member_id = self._audit_actor(ctx)
        entry = {
            'event':      'DATA_CHANGE',
            'operation':  operation,
            'table':      table,
            'session_id': session_id,
            'member_id':  member_id,
        }
        if record_id is not None:
            entry['record_id'] = str(record_id)
        if match_fields:
            entry['match_fields'] = sorted(str(f) for f in match_fields)
        if fields:
            entry['fields'] = sorted(str(f) for f in fields)
        if count is not None:
            entry['count'] = count
        enriched = self._audit_event('data_audit_log', entry, ctx)
        self._audit_logs.setdefault('data_audit_log', []).append(enriched)
        return enriched

    def _shape_to_input_schema(self, shape_decl):
        """Build a JSON input schema from a shape's fields, so an agent tool gets
        a real input contract from the connector operation's `sends` shape. An
        unknown field type maps to string; a list field maps to an array. Only
        fields marked `required` are listed as required, to avoid over-constraining
        what the model may send."""
        type_map = {
            'text': 'string', 'string': 'string', 'uuid': 'string', 'id': 'string',
            'date': 'string', 'datetime': 'string', 'timestamp': 'string',
            'number': 'number', 'integer': 'number', 'int': 'number',
            'decimal': 'number', 'float': 'number', 'money': 'number', 'currency': 'number',
            'boolean': 'boolean', 'bool': 'boolean',
        }
        props, required = {}, []
        for f in (getattr(shape_decl, 'fields', None) or []):
            fname = getattr(f, 'name', None)
            if not fname:
                continue
            tn = (getattr(f, 'type_name', None) or 'text').lower()
            if getattr(f, 'is_list', False) or tn == 'list':
                props[fname] = {'type': 'array', 'items': {'type': type_map.get(tn, 'string')}}
            else:
                props[fname] = {'type': type_map.get(tn, 'string')}
            mods = [getattr(m, 'modifier_type', None) for m in (getattr(f, 'modifiers', None) or [])]
            if 'required' in mods:
                required.append(fname)
        schema = {'type': 'object', 'properties': props}
        if required:
            schema['required'] = required
        return schema

    def _agent_tool_schemas(self, node, ctx):
        """Turn an agent's tools grant (node.tools) into provider tool definitions.

        Each granted connector operation becomes one tool the model may request.
        A grant is validated here, at agent setup, and fails loud the same way a
        direct mioconnect call does: a grant to a connector or operation that does
        not exist is a programming error, caught before the agent ever runs.

        The tool's input schema is derived from the operation's `sends` shape when
        one is declared, so the model is told what arguments the operation takes.
        An operation with no `sends` shape gets a permissive object.

        Returns (schemas, routing): schemas is the list of provider tool defs;
        routing maps each tool name back to (connector, operation) so the loop
        knows which mioconnect call to run when the model asks for that tool.

        Bare-connector grants (no dot) expand to every operation on that connector
        -- the looser form, covering current and future operations. ai-builtin
        grants (mioai.*) are not connector tools and are skipped here.
        """
        registry = getattr(self, '_connectors', {})
        schemas, routing = [], {}
        for grant in (node.tools or []):
            if grant.startswith('mioai.'):
                continue  # ai-builtin tool grant; not a connector operation
            if '.' in grant:
                conn_name, op_name = grant.split('.', 1)
                conn = registry.get(conn_name)
                if conn is None:
                    raise MohioRuntimeError(
                        f"ai.agent '{node.name}': tools grant '{grant}' names connector "
                        f"'{conn_name}', which is not declared. Declare it with "
                        f"`mioconnect {conn_name} ... mioconnect: done` before granting it.")
                if op_name not in conn['operations']:
                    known = ", ".join(conn['operations'].keys()) or "(none)"
                    raise MohioRuntimeError(
                        f"ai.agent '{node.name}': tools grant '{grant}' names operation "
                        f"'{op_name}', which connector '{conn_name}' does not have. "
                        f"Known operations: {known}.")
                op_pairs = [(conn_name, op_name)]
            else:
                conn = registry.get(grant)
                if conn is None:
                    raise MohioRuntimeError(
                        f"ai.agent '{node.name}': tools grant '{grant}' names connector "
                        f"'{grant}', which is not declared. Declare it with "
                        f"`mioconnect {grant} ... mioconnect: done` before granting it.")
                op_pairs = [(grant, op) for op in conn['operations'].keys()]
            for conn_name, op_name in op_pairs:
                op = registry[conn_name]['operations'][op_name]
                input_schema = {"type": "object"}
                sends = op.get('sends')
                if sends:
                    shape_name = sends[3:] if str(sends).startswith('sh.') else str(sends)
                    shape_decl = ctx.get_shape(shape_name) if ctx else None
                    if shape_decl is not None:
                        input_schema = self._shape_to_input_schema(shape_decl)
                tool_name = f"{conn_name}_{op_name}"
                schemas.append({
                    "name":        tool_name,
                    "description": f"Call the '{op_name}' operation on the '{conn_name}' connector.",
                    "input_schema": input_schema,
                })
                routing[tool_name] = (conn_name, op_name)
        return schemas, routing

    def _exec_AiAgentBlock(self, node, ctx):
        """
        Execute an ai.agent block with Deterministic Runtime Boundary Gate.

        Claim 18: The boundary gate is managed natively by the interpreter's
        core evaluation architecture — not by application-level framework code.
        This makes an un-guarded infinite agent loop impossible to execute.

        The interpreter's execution stack tracks three resource metrics
        directly during each agent iteration pass:
          - iteration_count: depth counter incremented per reasoning step
          - token_count: accumulated token usage across all provider calls
          - elapsed_seconds: wall-clock time since agent loop started

        When any metric breaches its declared limits threshold, the interpreter
        raises _AgentLimitExceeded — a native exception that unwinds the call
        stack and forces execution to the deterministic recovery pathway
        (the not_confident block or on.failure handler).
        """
        import time as _time

        limits = node.limits
        if limits is None:
            # The limits block is transformed but parked in body inside an
            # ai_agent_body wrapper; node.limits was never set. Recover it so the
            # developer's ceilings actually apply (previously the gate silently ran
            # on defaults). Purely additive — does not alter body processing.
            from mohio_ast import LimitsBlock
            from lark import Tree as _Tree
            for _b in (node.body or []):
                _inner = _b.children[0] if isinstance(_b, _Tree) and getattr(_b, 'children', None) else _b
                if isinstance(_inner, LimitsBlock):
                    limits = _inner
                    break
        max_steps    = getattr(limits, 'max_steps',    0)   if limits else 10
        max_tokens   = getattr(limits, 'max_tokens',   0)   if limits else 0
        max_calls    = getattr(limits, 'max_calls',    0)   if limits else 0
        cost_ceiling = getattr(limits, 'cost_ceiling', 0.0) if limits else 0.0
        timeout_secs = None
        if limits and limits.timeout:
            tv = self._eval_simple(limits.timeout, ctx)
            try: timeout_secs = float(tv)
            except: timeout_secs = 30.0

        # ── Deterministic Runtime Boundary Gate ──────────────────────
        # These counters are managed on the interpreter's execution stack,
        # not passed as application variables. They cannot be overridden
        # by agent-generated code or adversarial prompt injection.
        iteration_count = 0
        token_count     = 0
        accumulated_cost = 0.0
        start_time      = _time.monotonic()

        goal    = node.goal    or ""
        context = node.context or ""
        from mohio_ai import DEFAULT_ANTHROPIC_MODEL
        model   = node.model   or DEFAULT_ANTHROPIC_MODEL

        # Build initial message
        messages = []
        if goal:
            messages.append({"role": "user", "content": goal})
        if context:
            messages[0]["content"] = messages[0]["content"] + "\n\nContext: " + str(context)

        result = None
        agent_error = None

        # Turn the agent's tools grant into provider tool definitions, once,
        # before the loop. A grant to a connector/operation that does not exist
        # fails loud here, before the agent runs a single step. An agent with no
        # tools grant gets an empty list -- it can reason, but it has no reach.
        tool_schemas, tool_routing = self._agent_tool_schemas(node, ctx)

        # External-call gate frame — lives on the interpreter's stack, never as an
        # application variable, so agent-generated code cannot reset or raise it.
        # _exec_MioconnectCall reads the top frame and counts every external call.
        if not hasattr(self, '_agent_gate_stack'):
            self._agent_gate_stack = []
        self._agent_gate_stack.append(
            {'name': node.name, 'external_calls': 0, 'max_calls': max_calls})

        try:
            while True:
                # ── Boundary Gate — check all metrics before each iteration ──
                iteration_count += 1

                # Iteration depth check
                if max_steps and iteration_count > max_steps:
                    raise _AgentLimitExceeded(
                        "Maximum iterations reached",
                        metric="steps",
                        value=iteration_count,
                        ceiling=max_steps,
                    )

                # Wall-clock timeout check
                if timeout_secs:
                    elapsed = _time.monotonic() - start_time
                    if elapsed > timeout_secs:
                        raise _AgentLimitExceeded(
                            "Execution timeout",
                            metric="timeout",
                            value=round(elapsed, 2),
                            ceiling=timeout_secs,
                        )

                # Token ceiling check
                if max_tokens and token_count >= max_tokens:
                    raise _AgentLimitExceeded(
                        "Token limit reached",
                        metric="tokens",
                        value=token_count,
                        ceiling=max_tokens,
                    )

                # Cost ceiling check
                if cost_ceiling and accumulated_cost >= cost_ceiling:
                    raise _AgentLimitExceeded(
                        "Cost ceiling reached",
                        metric="cost",
                        value=round(accumulated_cost, 4),
                        ceiling=cost_ceiling,
                    )

                # ── Provider call ─────────────────────────────────────────
                if self.verbose:
                    print(f"  [ai.agent] {node.name} step {iteration_count}")

                self._charge_ai_call(node.name)
                try:
                    if tool_schemas:
                        # ── Tool-enabled turn ──────────────────────────────
                        # The model either answers (done) or asks to call one
                        # granted tool. An ungranted tool is refused here: the
                        # runtime decides what the agent may touch, not the model.
                        from types import SimpleNamespace as _NS
                        turn = self.ai.agent_turn(
                            messages=messages, tools=tool_schemas, model=model,
                            temperature=float(self._eval_simple(node.temperature, ctx) or 0.7)
                                if node.temperature else 0.7,
                            max_tokens=(max_tokens or None),
                        )
                        token_count      += getattr(turn, 'tokens', 0) or 0
                        accumulated_cost += getattr(turn, 'cost',   0.0) or 0.0

                        if turn.kind == 'text':
                            result = turn.text
                            break  # the agent finished

                        # kind == 'tool': enforce the grant before anything runs.
                        if turn.tool_name not in tool_routing:
                            # Refused. The action does not happen. Audit it
                            # (compliance) and route to the fallback, the same
                            # shape as an over-budget call. error_name is the
                            # stable signal a caller can branch on.
                            self._audit_logs.setdefault(f"{node.name}_limits_log", []).append(
                                self._audit_event(f"{node.name}_limits_log", {
                                    "event":      "TOOL_NOT_GRANTED",
                                    "agent":      node.name,
                                    "tool":       turn.tool_name,
                                    "error_name": "tool_not_granted",
                                }, ctx))
                            agent_error = (f"tool_not_granted: agent '{node.name}' is not "
                                           f"granted tool '{turn.tool_name}'")
                            break

                        # Granted. Run the connector operation through the same
                        # mioconnect executor a direct call uses, so the boundary
                        # gate counts it against max_calls automatically (over
                        # budget -> _AgentLimitExceeded, handled below). The carrier
                        # is a minimal stand-in for a MioconnectCall node.
                        conn_name, op_name = tool_routing[turn.tool_name]
                        _call = _NS(connector=conn_name, operation=op_name,
                                    payload=MohioValue(turn.tool_input or {}, 'shape'),
                                    result=None)
                        tool_result = self._exec_MioconnectCall(_call, ctx)
                        tr = tool_result.to_python() if isinstance(tool_result, MohioValue) else tool_result
                        # Feed the result back in the canonical tool-use / tool-result
                        # shape the real Messages API requires for multi-turn tool use.
                        # The Mock ignores message content (it is scripted), so this is
                        # invisible to offline tests but essential for the live model.
                        import json as _json
                        _tid = turn.tool_id or f"call_{iteration_count}"
                        messages.append({"role": "assistant", "content": [
                            {"type": "tool_use", "id": _tid,
                             "name": turn.tool_name, "input": turn.tool_input or {}}]})
                        messages.append({"role": "user", "content": [
                            {"type": "tool_result", "tool_use_id": _tid,
                             "content": tr if isinstance(tr, str) else _json.dumps(tr)}]})
                        result = tr
                        continue

                    # 2026-08-04 ruling: this call never passed a model at all, so a
                    # declared `model "..."` on a no-tools ai.agent was silently ignored
                    # (the tool-enabled sibling path already passes it correctly to
                    # agent_turn(), see model= above). Only included when the developer
                    # actually declared one -- an absent node.model must fall through to
                    # decide()'s own chain/app-default resolution, not force the dated
                    # local default (`model` at the top of this method) in as a fake
                    # "explicit" override that would now outrank an active chain.
                    response = self.ai.decide(
                        name=node.name,
                        inputs={},
                        threshold=0.0,
                        return_type='text',
                        context=context,
                        temperature=float(self._eval_simple(node.temperature, ctx) or 0.7)
                            if node.temperature else 0.7,
                        **({'model_override': node.model} if node.model else {}),
                    )
                    # Accumulate token/cost telemetry from provider response
                    token_count      += getattr(response, 'tokens', 0) or 0
                    accumulated_cost += getattr(response, 'cost',   0.0) or 0.0
                    result = response.result

                    # ReAct-style loop: add response and continue
                    # The ONLY stop conditions are:
                    # 1. Boundary gate (max_steps, max_tokens, cost, timeout)
                    # 2. Model signals completion via result="DONE" or similar
                    # 3. Provider error
                    if str(result).strip().upper() in ('DONE', 'COMPLETE', 'FINISHED'):
                        break  # Model signaled completion
                    messages.append({"role": "assistant", "content": str(result)})
                    messages.append({"role": "user",      "content": "Continue."})

                except _AgentLimitExceeded:
                    raise  # let the boundary-gate handler audit + route to recovery
                except Exception as provider_err:
                    agent_error = str(provider_err)
                    break

        except _AgentLimitExceeded as limit_err:
            # ── Deterministic Failover Path ───────────────────────────────
            # Boundary gate fired. Log the intercept and route to recovery.
            agent_error = str(limit_err)
            if self.verbose:
                print(f"  [ai.agent] BOUNDARY GATE: {limit_err.metric} "
                      f"{limit_err.value} > {limit_err.ceiling}")
            # ai.audit the limit breach
            self._audit_logs.setdefault(f"{node.name}_limits_log", []).append(
                self._audit_event(f"{node.name}_limits_log", {
                    "event":   "AGENT_LIMIT_EXCEEDED",
                    "agent":   node.name,
                    "metric":  limit_err.metric,
                    "value":   limit_err.value,
                    "ceiling": limit_err.ceiling,
                }, ctx))
        finally:
            if getattr(self, '_agent_gate_stack', None):
                self._agent_gate_stack.pop()

        # ── Bind result and set context ───────────────────────────────────
        ctx.set(f"_agent_{node.name}_steps",  MohioValue(iteration_count, 'number'))
        ctx.set(f"_agent_{node.name}_tokens", MohioValue(token_count,     'number'))
        ctx.set(f"_agent_{node.name}_cost",   MohioValue(accumulated_cost,'number'))
        ctx.set(f"_agent_{node.name}_error",  MohioValue(agent_error or '', 'text'))

        if result is not None:
            ctx.set(node.name, MohioValue(result, 'text'))

        if agent_error:
            # Scan node.body for on.failure / not confident, mirroring exactly how
            # _exec_AiDecideBlock reads its own body (2026-08-04, Unit 2). This used to
            # call self._handle_failure(getattr(node, "handlers", None) or [], ...) --
            # AiAgentBlock has no `handlers` field at all, so that call ALWAYS ran with
            # an empty list. Neither on.failure nor not confident ever fired, for any
            # agent failure (a provider error OR a boundary-gate breach -- max steps,
            # timeout, cost -- both converge on this same agent_error path), and the
            # call silently resolved to None as if nothing had gone wrong.
            _of = next((b for b in node.body if isinstance(b, OnFailure)), None)
            if _of is not None:
                return self._exec_block(_of.body, ctx)
            _nc = next((b for b in node.body if isinstance(b, NotConfidentBlock)), None)
            if _nc is not None:
                return self._exec_block(_nc.body, ctx)
            # Neither declared: correctly hard-fail loud, matching ai.create's pattern
            # (no handler -> re-raise) rather than silently returning None.
            raise _Raise(error_name='ai_error',
                message=f"ai.agent '{node.name}' failed: {agent_error}",
                line=getattr(node, 'line', None),
                hint="Add an 'on.failure' or 'not confident' handler inside this "
                     "ai.agent, or check the AI provider/credentials.")

        if self.verbose:
            print(f"  [ai.agent] {node.name} complete: "
                  f"{iteration_count} steps, {token_count} tokens, "
                  f"${accumulated_cost:.4f}")

        return MohioValue(result, 'text') if result else MohioValue(None)

    def _exec_GrabBlock(self, node, ctx):
        """grab — same as retrieve but for cache/single records.

        on.success runs when a record is found; on.failure runs when nothing
        matches (the fetch-or-404 pattern). A miss without on.failure simply
        binds nothing — checking `if <name> is none` stays valid.
        """
        db, _early = self._db_or_fail(ctx, 'grab', node)
        if db is None: return _early
        table = self._resolve_source(node.source, ctx)
        # No match clause is NOT a failure -- a grab with nothing to match on simply binds
        # nothing, and `if <name> is none` stays the way you check. Missing CONNECTION is a
        # failure; missing MATCH is a result. Two different things, kept apart.
        if not node.match:
            ctx.set(node.name, MohioValue(None))
            return None
        # node.match is a single MatchClause, or a LIST for a composite match (multiple
        # comma-separated pairs). A composite match used to collapse to None entirely here
        # (isinstance check against a list of MatchClause never matched), so `grab`/`get`
        # silently bound nothing -- no error, indistinguishable from "record not found".
        match_clauses = node.match if isinstance(node.match, list) else [node.match]
        if len(match_clauses) == 1:
            mc = match_clauses[0]
            match_val = self._eval_simple(mc.value, ctx)
            row = db.retrieve_one(table, mc.field, match_val)
        else:
            conditions = {mc.field: self._eval_simple(mc.value, ctx) for mc in match_clauses}
            row = db.retrieve_one_multi(table, conditions)
        row = self._decrypt_row(row) if row else row
        self._audit_data_access('grab', table, row, ctx)
        result = MohioValue(row, 'shape') if row else MohioValue(None)
        ctx.set(node.name, result)
        if row:
            self._handle_success(node.handlers, ctx)
        elif any(isinstance(h, OnFailure) for h in node.handlers):
            return self._handle_failure(node.handlers, ctx, "record not found")
        return result

    def _exec_GetBlock(self, node, ctx):
        """get — assertive retrieval, same as grab at runtime."""
        return self._exec_GrabBlock(node, ctx)

    def _exec_PullBlock(self, node, ctx):
        """pull up to N [random] from <source> — bounded or random retrieval.

        Source may be a db table, a held list, or a find/retrieve result
        variable — all resolved the same way. `random` samples instead of
        taking in order. `up to N` is a ceiling: a short source returns fewer
        items, never an error. Result binds to the closer's `as` name.
        """
        import random as _random
        from mohio_ast import DbRef, DottedName
        n = int(self._eval_simple(node.limit, ctx)) if node.limit is not None else None
        is_random = getattr(node, 'random', False)

        is_db = isinstance(node.source, DbRef) or (
            isinstance(node.source, DottedName)
            and getattr(node.source, 'parts', None)
            and str(node.source.parts[0]) == 'db'
        )

        if is_db and not is_random:
            # Ordered DB pull — keep DB-side limit (bounded batch retrieval).
            # A missing connection used to yield an empty list, which is a LIE: it told
            # the program the table had no rows. "I could not look" is not "there is
            # nothing there". Fail loud.
            db, _early = self._db_or_fail(ctx, 'pull', node)
            if db is None: return _early
            table = self._resolve_source(node.source, ctx)
            rows  = db.find_many(table, limit=n)
            result = MohioValue(rows, 'list')
        else:
            # Random, or a non-db source (held list / find-result): resolve the
            # whole collection, then sample or slice in-process.
            seq = self._random_collection(node.source, ctx)
            if seq is None:
                raise _Raise(
                    error_name='pull_source_not_a_collection',
                    message="pull up to N from <X>: X must be a collection.",
                    line=getattr(node, 'line', 0),
                    hint="X must be a collection: a db table, a held list "
                         "(`hold name / items / hold: done`), or a find/retrieve result.",
                )
            if is_random:
                # min(n, len): `up to N` is a ceiling, so a short source is fine.
                chosen = _random.sample(list(seq), min(n, len(seq))) if n is not None else list(seq)
            else:
                chosen = list(seq)[:n] if n is not None else list(seq)
            result = MohioValue(chosen, 'list')

        count = len(result.to_python()) if hasattr(result, 'to_python') else 0
        if self.verbose:
            print(f"  [pull]{' random' if is_random else ''} {count} items")

        # Bind to the closer's `as` name, if present.
        if getattr(node, 'as_name', None):
            ctx.set(node.as_name, result)

        # Run on.success handler if one was declared.
        for h in (node.handlers or []):
            if isinstance(h, OnSuccess):
                self._exec_block(h.body, ctx)
                break

        return result

    def _require_defined(self, node, ctx, where):
        """A3 guard: a bare, single-part variable name used as a VALUE must be defined,
        or fail loud with the same unknown_variable rule `show` and interpolation already
        enforce. ONLY a lone undefined name is caught -- a field access (x.field), a
        literal, an expression, and a defined-but-null value all pass through untouched,
        so `ctx.get` returning None and the `when empty` / optional / default patterns
        that depend on it are left completely alone."""
        if type(node).__name__ == 'DottedName' and len(getattr(node, 'parts', [])) == 1:
            nm = node.parts[0]
            if not ctx.exists(nm):
                raise MohioRuntimeError(
                    f"{where} refers to an unknown variable '{nm}'. Declare it first "
                    f"(e.g. `hold {nm} <value>`), or check the spelling.")

    def _resolve_dynamic_field_name(self, field_name_node, ctx, verb):
        """Resolve a dynamic `name to value` field's COLUMN NAME at runtime, failing loud on
        anything that cannot be a column. The name decides which column gets written, so an
        unusable name is data corruption, not a warning: an empty/None name silently wrote a
        column literally named '' or 'None'. `_require_defined` upstream already catches a
        bare undefined variable; this catches a name that IS defined but resolves to nothing
        usable (empty text, null)."""
        raw = self._eval(field_name_node, ctx)
        raw = raw.to_python() if isinstance(raw, MohioValue) else raw
        name = '' if raw is None else str(raw).strip()
        if not name:
            raise MohioRuntimeError(
                f"{verb}: the dynamic field name resolved to empty, so there is no column to "
                f"write. A dynamic field name must resolve to a real column name "
                f"(e.g. `hold column \"amount\"` then `column to 42`).")
        return name

    def _exec_SaveBlock(self, node, ctx):
        db, _early = self._db_or_fail(ctx, 'save', node)
        if db is None: return _early
        table = self._resolve_source(node.target, ctx)

        from mohio_ast import DynamicFieldValue
        fields = {}
        for fv in node.fields:
            if isinstance(fv, FieldValue):
                # A3: an undefined bare name in a save field silently wrote None -- live
                # data corruption. Fail loud instead (a defined-but-null value still saves).
                self._require_defined(fv.value, ctx, f"save field '{fv.name}'")
                val = self._eval_simple(fv.value, ctx)
                if isinstance(val, datetime.datetime):
                    val = val.isoformat()
                fields[fv.name] = val
            elif isinstance(fv, DynamicFieldValue):
                # Dynamic `name to value`: the COLUMN NAME is resolved at runtime. save had
                # no branch for this at all (only FieldValue), so the field was silently
                # dropped -- the column simply never got written, with no error.
                self._require_defined(fv.field_name, ctx, "save dynamic field name")
                self._require_defined(fv.value, ctx, "save dynamic field value")
                fname = self._resolve_dynamic_field_name(fv.field_name, ctx, 'save')
                val = self._eval(fv.value, ctx)
                val = val.to_python() if isinstance(val, MohioValue) else val
                if isinstance(val, datetime.datetime):
                    val = val.isoformat()
                fields[fname] = val

        # dedupe guard: `save ... unless <a>, <b> exists` -- if a row already matches ALL the
        # named columns, skip the insert and return the existing record instead. The key may be
        # composite: the named columns together identify one logical row (Zork's per-session
        # flags are (session_id, flag_name), not either one alone).
        dedupe_fields = [f for f in (getattr(node, 'dedupe_fields', None) or []) if f in fields]
        _declared = getattr(node, 'dedupe_fields', None) or []
        if _declared and not dedupe_fields:
            # Every named column must actually be written by this save, or the key identifies
            # nothing and the guard would silently degrade to a plain insert.
            raise _Raise(error_name='save_dedupe_field_missing',
                message=(f"save ... unless {', '.join(_declared)} exists: none of those columns "
                         f"is set by this save, so there is nothing to match on."),
                line=getattr(node, 'line', None),
                hint="Name columns this save actually writes, e.g. "
                     "`save to db.flags unless session_id, flag_name exists`.")
        if dedupe_fields:
            conditions = {f: fields[f] for f in dedupe_fields}
            existing = (db.retrieve_one_multi(table, conditions)
                        if len(conditions) > 1
                        else db.retrieve_one(table, dedupe_fields[0], fields[dedupe_fields[0]]))
            if existing:
                dup = MohioValue(self._decrypt_row(existing) or existing, 'shape')
                if getattr(node, 'alias', None):
                    ctx.set(node.alias, dup)
                if self.verbose:
                    print(f"  [save] skipped duplicate on {conditions!r}")
                for h in node.handlers:
                    if isinstance(h, OnSuccess):
                        self._exec_block(h.body, ctx); break
                return dup
            # No existing row: insert ATOMICALLY (INSERT ... WHERE NOT EXISTS) rather than a
            # bare insert, so a concurrent writer between the check above and the write here
            # cannot produce a duplicate. The pre-check above stays because it is what returns
            # the EXISTING record (and runs on.success) when one is already there.
            try:
                fields = self._encrypt_fields_for_write(fields, node)
                row_id = db.save_if_not_exists(table, fields, dedupe_fields)
            except Exception as e:
                if any(isinstance(h, OnFailure) for h in node.handlers):
                    return self._handle_failure(node.handlers, ctx, str(e))
                raise _Raise(error_name='db_error', message=str(e))
            self._audit_data_change('save', table, ctx, record_id=row_id,
                                    fields=list(fields.keys()))
            result = MohioValue({'id': row_id, **fields}, 'shape')
            if getattr(node, 'alias', None):
                ctx.set(node.alias, result)
            if self.verbose: print(f"  [save] to {table} id={row_id} (unless-exists)")
            self._handle_success(node.handlers, ctx)
            return result

        try:
            fields = self._encrypt_fields_for_write(fields, node)
            row_id = db.save(table, fields)
        except Exception as e:
            if any(isinstance(h, OnFailure) for h in node.handlers):
                return self._handle_failure(node.handlers, ctx, str(e))
            raise _Raise(error_name='db_error', message=str(e))

        self._audit_data_change('save', table, ctx, record_id=row_id,
                                fields=list(fields.keys()))
        result = MohioValue({'id': row_id, **fields}, 'shape')
        if getattr(node, 'alias', None):
            ctx.set(node.alias, result)
        if self.verbose: print(f"  [save] to {table} id={row_id}")

        self._handle_success(node.handlers, ctx)

        return result

    def _exec_SaveOrUpdateBlock(self, node, ctx):
        """
        save or update / upsert — atomic upsert using native db support.
        All four runtimes have a native upsert method:
          SQLite   → INSERT OR REPLACE / ON CONFLICT DO UPDATE
          Postgres → INSERT ON CONFLICT DO UPDATE
          MySQL    → INSERT ON DUPLICATE KEY UPDATE
          MongoDB  → update_one with upsert=True
        Falls back to try-update-then-insert for any runtime without .upsert().
        """
        db, _early = self._db_or_fail(ctx, 'save or update', node)
        if db is None: return _early
        table = self._resolve_source(node.source, ctx)

        # Collect field values
        from mohio_ast import DynamicFieldValue
        fields = {}
        for fv in (node.fields or []):
            if isinstance(fv, FieldValue):
                self._require_defined(fv.value, ctx, f"save or update field '{fv.name}'")  # A3.1
                val = self._eval_simple(fv.value, ctx)
                fields[fv.name] = val.to_python() if isinstance(val, MohioValue) else val
            elif isinstance(fv, DynamicFieldValue):
                # The field NAME was unguarded: an undefined/empty name became the literal
                # column '' or 'None'. It decides WHICH column is written, so it gets the same
                # A3.1 treatment as the value.
                self._require_defined(fv.field_name, ctx, "save or update dynamic field name")
                self._require_defined(fv.value, ctx, "save or update field value")  # A3.1
                fn_str = self._resolve_dynamic_field_name(fv.field_name, ctx, 'save or update')
                v_val  = self._eval(fv.value, ctx)
                fields[fn_str] = v_val.to_python() if isinstance(v_val, MohioValue) else v_val

        # Resolve match clause(s). One or more MatchClause, AND-ed. Multiple match fields are a
        # COMPOSITE conflict target (a table with e.g. UNIQUE(session_id, id)); a single-column
        # upsert is the len==1 case, unchanged. Each match value is WRITTEN into the payload
        # (below), so an undefined bare name is silent-None corruption, not a mere filter (A3.1).
        match_clauses = ([] if not node.match
                         else (node.match if isinstance(node.match, list) else [node.match]))
        match_fields = []
        for mc in match_clauses:
            self._require_defined(mc.value, ctx, f"save or update match field '{mc.field}'")
            mval = self._eval_simple(mc.value, ctx)
            mv = mval.to_python() if isinstance(mval, MohioValue) else mval
            fields[mc.field] = mv          # every conflict column must be in the INSERT payload
            match_fields.append(mc.field)

        if self.verbose:
            print(f"  [upsert] {table} match {match_fields} → {len(fields)} fields")

        # Encrypt tagged fields BEFORE any of the three write branches below. Like update,
        # upsert skipped this entirely, so a [phi]/[pii]/[pci] field written via upsert went
        # in as plaintext. Encryption is idempotent (it no-ops on an already-`enc:v1:` value),
        # so the update-then-save fallback path re-calling it is harmless.
        fields = self._encrypt_fields_for_write(fields, node)

        # Native upsert if available -- pass the FULL list of conflict columns.
        if match_fields and hasattr(db, 'upsert'):
            db.upsert(table, fields, match_fields)
        elif match_fields:
            # Fallback (runtime without native upsert, e.g. SQLite): update matching on ALL
            # match fields, insert if nothing matched. update_multi builds the multi-field WHERE.
            update_fields = {k: v for k, v in fields.items() if k not in match_fields}
            conditions = {k: fields[k] for k in match_fields}
            if not update_fields:
                # PURE-EXISTENCE upsert: every field IS a match key, so there is nothing to
                # SET. `update_multi` would build `UPDATE t SET  WHERE ...` (empty SET), hit a
                # SQL syntax error, swallow it, return 0, and fall through to a plain INSERT --
                # which then violated the very constraint the upsert existed to respect. This
                # is Zork's flag-set shape exactly. Insert-if-absent is the correct semantic,
                # and it matches what the native Postgres path already does for this case
                # (ON CONFLICT ... DO NOTHING when no non-key column remains).
                db.save_if_not_exists(table, fields, match_fields)
            else:
                count = db.update_multi(table, update_fields, conditions)
                if not count:
                    db.save(table, fields)
        else:
            db.save(table, fields)

        self._audit_data_change('save_or_update', table, ctx,
                                match_fields=match_fields or None,
                                fields=list(fields.keys()))
        return MohioValue(fields, 'shape')

    def _exec_SaveAllBlock(self, node, ctx):
        # Persist every record in a collection. Was a silent _stub -- a persistence
        # verb that does not persist is a data-loss trap, so this now does the real
        # work and fails loud on bad input / no connection rather than no-opping.
        db    = ctx.get_connection('db')
        table = self._resolve_source(node.target, ctx)
        if not db:
            raise _Raise(error_name='db_error',
                         message="save all: no database connection is open")
        self._require_defined(node.source, ctx, "save all source")  # A3.1
        coll  = self._eval(node.source, ctx)
        items = coll.to_python() if isinstance(coll, MohioValue) else coll
        if items is None:
            items = []
        if isinstance(items, dict):
            items = [items]                      # a single record = a 1-item batch
        if not isinstance(items, (list, tuple)):
            raise _Raise(error_name='type_error',
                         message=f"save all expects a list of records to save, "
                                 f"got {type(items).__name__}")
        saved_ids = []
        try:
            for item in items:
                if isinstance(item, MohioValue):
                    item = item.to_python()
                if not isinstance(item, dict):
                    raise _Raise(error_name='type_error',
                                 message="save all: each item must be a record / shape")
                fields = {}
                for k, v in item.items():
                    if isinstance(v, datetime.datetime):
                        v = v.isoformat()
                    fields[k] = v
                # Encrypt tagged fields before persisting, same as singular save. Without
                # this, a batch write of [phi]/[pii]/[pci] records stored them in the clear.
                fields = self._encrypt_fields_for_write(fields, node)
                saved_ids.append(db.save(table, fields))
        except _Raise:
            raise
        except Exception as e:
            raise _Raise(error_name='db_error', message=str(e))
        self._audit_data_change('save_all', table, ctx, count=len(saved_ids))
        result = MohioValue({'count': len(saved_ids), 'ids': saved_ids}, 'shape')
        if self.verbose: print(f"  [save all] {len(saved_ids)} rows to {table}")
        for h in node.handlers:
            if isinstance(h, OnSuccess):
                self._exec_block(h.body, ctx); break
        return result

    def _exec_UpdateBlock(self, node, ctx):
        db, _early = self._db_or_fail(ctx, 'update', node)
        if db is None: return _early
        table = self._resolve_source(node.source, ctx)

        from mohio_ast import DynamicFieldValue
        matches = [b for b in node.body if isinstance(b, MatchClause)]
        updates = {}
        for b in node.body:
            if isinstance(b, FieldValue):
                fname = (b.name or '').strip()
                if not fname or fname.lower() == 'set':
                    raise _Raise(error_name='update_field_form',
                        message=(f"'{b.name or '(empty)'}' is not a valid update field. "
                                 "Update fields use the bare form, e.g. `balance 200`."),
                        line=getattr(node, 'line', None),
                        hint="Write `field value` directly. `set`, `to`, and `=` were "
                             "retired inside update blocks: write `balance 200`, not "
                             "`set balance to 200`.")
                self._require_defined(b.value, ctx, f"update field '{b.name}'")  # A3.1
                updates[b.name] = self._eval_simple(b.value, ctx)
            elif isinstance(b, DynamicFieldValue):
                # Evaluate field name at runtime. It decides WHICH column is written, so it is
                # guarded like the value: an undefined/empty name used to become the literal
                # column '' or 'None' instead of failing loud.
                self._require_defined(b.field_name, ctx, "update dynamic field name")
                field_name_str = self._resolve_dynamic_field_name(b.field_name, ctx, 'update')
                self._require_defined(b.value, ctx, "update field value")  # A3.1
                value_val = self._eval(b.value, ctx)
                value_py = value_val.to_python() if isinstance(value_val, MohioValue) else value_val
                updates[field_name_str] = value_py
                if self.verbose:
                    print(f"  [set field] dynamic: {field_name_str!r} = {value_py!r}")

        if matches and updates:
            conditions = {m.field: self._eval_simple(m.value, ctx) for m in matches}
            # Encrypt tagged fields BEFORE the write. `save` did this; update did not, so a
            # [phi]/[pii]/[pci] field first written via `update` went to the database as
            # plaintext and the fail-loud never fired (the encrypt path was never entered).
            # Same chokepoint, same fail-loud, now on this path too.
            updates = self._encrypt_fields_for_write(updates, node)
            try:
                count = db.update_multi(table, updates, conditions)
            except Exception as e:
                if any(isinstance(h, OnFailure) for h in node.handlers):
                    return self._handle_failure(node.handlers, ctx, str(e))
                raise _Raise(error_name='db_error', message=str(e))
            self._audit_data_change('update', table, ctx,
                                    match_fields=list(conditions.keys()),
                                    fields=list(updates.keys()), count=count)
            if self.verbose: print(f"  [update] {table} — {count} rows")

        self._handle_success(node.handlers, ctx)
        return None

    def _exec_RemoveBlock(self, node, ctx):
        db, _early = self._db_or_fail(ctx, 'remove', node)
        if db is None: return _early
        table = self._resolve_source(node.source, ctx)

        cond = node.condition
        if not cond:
            raise _Raise(error_name='remove_without_condition',
                message="remove needs a condition so it can't delete the whole table by accident.",
                line=getattr(node, 'line', None),
                hint="Add 'where <field> is <value>' or 'match <field> to <value>'. "
                     "To clear a table on purpose, use 'remove.all'.")

        if isinstance(cond, list):
            # A composite match (multiple comma-separated `match` pairs). Previously this
            # collapsed to node.condition = None (the isinstance check below never matched a
            # list), so a composite-match remove ALWAYS hit the "no condition" refusal above --
            # a confusing error claiming no condition was given when one was. MatchClause has no
            # comparison operator (always equality), so no operator check is needed here, unlike
            # the single-field path below.
            conditions = {str(mc.field).split('.')[-1]: self._eval_simple(mc.value, ctx)
                          for mc in cond}
            try:
                count = db.remove_multi(table, conditions)
            except Exception as e:
                if any(isinstance(h, OnFailure) for h in node.handlers):
                    return self._handle_failure(node.handlers, ctx, str(e))
                raise _Raise(error_name='db_error', message=str(e))
            self._audit_data_change('remove', table, ctx,
                                    match_fields=list(conditions.keys()), count=count)
            if self.verbose:
                print(f"  [remove] from {table} where {conditions!r} — {count} rows")
            self._handle_success(node.handlers, ctx)
            return None

        # remove deletes on equality only. A non-equality where (above/below/
        # contains/between) would delete the wrong rows, so fail loud instead.
        op = getattr(cond, 'condition', None)
        if op not in (None, 'is', '==', '', 'to'):
            raise _Raise(error_name='remove_condition_unsupported',
                message=f"remove only supports equality conditions, not '{op}'.",
                line=getattr(node, 'line', None),
                hint="Use 'where <field> is <value>' (exact match). "
                     "Range or contains deletes aren't supported yet.")

        field = str(cond.field).split('.')[-1]
        match_val = self._eval_simple(cond.value, ctx)
        try:
            count = db.remove(table, field, match_val)
        except Exception as e:
            if any(isinstance(h, OnFailure) for h in node.handlers):
                return self._handle_failure(node.handlers, ctx, str(e))
            raise _Raise(error_name='db_error', message=str(e))
        self._audit_data_change('remove', table, ctx,
                                match_fields=[field], count=count)
        if self.verbose:
            print(f"  [remove] from {table} where {field} = {match_val!r} — {count} rows")

        self._handle_success(node.handlers, ctx)
        return None

    def _exec_RemoveAllBlock(self, node, ctx):
        db    = ctx.get_connection('db')
        table = self._resolve_source(node.source, ctx)
        if not db:
            raise _Raise(error_name='remove_all_no_db',
                message="remove.all needs a database connection but none is open.",
                line=getattr(node, 'line', None),
                hint="Declare one first, e.g. 'connect db as postgres from env.DATABASE_URL'.")
        handlers = getattr(node, 'handlers', []) or []
        try:
            count = db.remove_all(table)
        except Exception as e:
            if any(isinstance(h, OnFailure) for h in handlers):
                return self._handle_failure(handlers, ctx, str(e))
            raise _Raise(error_name='db_error',
                message=f"clearing table '{table}' failed: {e}",
                line=getattr(node, 'line', None),
                hint="Check the table name and that it exists. remove.all truncates "
                     "every row, so the table must be present.")
        self._audit_data_change('remove_all', table, ctx, count=count)
        if self.verbose:
            print(f"  [remove.all] cleared {table} — {count} rows")
        for h in handlers:
            if isinstance(h, OnSuccess):
                self._exec_block(h.body, ctx)
                break
        return None

    def _exec_TransactionBlock(self, node, ctx):
        # `if db:` meant a transaction with no connection ran its body ANYWAY, with no
        # transaction around it. The one guarantee the block exists to make -- all of it
        # or none of it -- was silently not made. A transaction that cannot be atomic must
        # not pretend; it must refuse.
        db, _early = self._db_or_fail(ctx, 'transaction', node)
        if db is None: return _early
        db.begin_transaction()
        try:
            result = self._exec_block(node.body, ctx)
            db.commit_transaction()
            return result
        except Exception:
            db.rollback_transaction()
            raise

    def _exec_CheckMioqlBlock(self, node, ctx):
        # A1 MioQL: check exists -> boolean, check count -> integer, check unique ->
        # boolean (signup polarity: unique == value not already present). These are
        # non-retrieving -- they answer a question, never return rows. For the row,
        # use find / get.
        db    = ctx.get_connection('db')
        table = self._resolve_source(node.source, ctx)
        if not db or not table:
            raise MohioRuntimeError(
                "check exists/count/unique needs a database source -- e.g. "
                "`in db.<table>`.")
        where = {}
        cond = node.condition
        cond_type = type(cond).__name__
        if cond is not None and cond_type == 'MatchClause' and cond.field:
            where[cond.field] = (self._eval_simple(cond.value, ctx)
                                 if cond.value is not None else None)
        elif cond is not None and cond_type == 'WhereClause' and cond.field:
            # db.count() only understands equality filters (a dict of column=value); it has no
            # WHERE-builder for comparison operators. `is`/`==`/bare is equality and honored the
            # same as a match clause -- this used to check ONLY for MatchClause, so a `where`
            # condition parsed fine and was silently dropped, counting the WHOLE table instead
            # of the filtered rows (e.g. `check count as n / where grp is "a"` returned every
            # row, not just grp=a). A comparison condition (above/below/contains/...) fails
            # loud here rather than silently degrading to "no filter" -- count() cannot express
            # it, and a wrong count masquerading as a right one is worse than an error.
            if cond.condition in ('is', '==', ''):
                where[cond.field] = self._eval_filter_value(cond.value, ctx)
            else:
                raise MohioRuntimeError(
                    f"check count/exists: the where condition '{cond.condition}' is not "
                    f"supported here -- only equality (`where {cond.field} is ...`) can be "
                    f"counted. For a comparison filter, use `find`/`retrieve` and read "
                    f"`.count`.")
        cnt = db.count(table, where or None)
        handlers = node.handlers or []

        def _run_success():
            for h in handlers:
                if isinstance(h, OnSuccess):
                    self._exec_block(h.body, ctx)
                    return

        if node.variant == 'exists':
            found = cnt > 0
            if node.name:
                ctx.set(node.name, MohioValue(found, 'boolean'))
            if found:
                _run_success()
            elif any(isinstance(h, OnFailure) for h in handlers):
                return self._handle_failure(handlers, ctx, "no matching record exists")
            return MohioValue(found, 'boolean')

        if node.variant == 'unique':
            is_unique = (cnt == 0)   # signup: unique == not already present
            if node.name:
                ctx.set(node.name, MohioValue(is_unique, 'boolean'))
            if is_unique:
                _run_success()
            elif any(isinstance(h, OnFailure) for h in handlers):
                return self._handle_failure(handlers, ctx,
                                            "value already exists (not unique)")
            return MohioValue(is_unique, 'boolean')

        if node.variant == 'count':
            if not node.name:
                raise MohioRuntimeError(
                    "check count requires `as NAME` (e.g. `check count as total in "
                    "db.X match ...`). Without it the count has nowhere to bind -- "
                    "`result` is reserved and there is no magic default.")
            ctx.set(node.name, MohioValue(cnt, 'number'))
            _run_success()
            return MohioValue(cnt, 'number')

        raise MohioRuntimeError(f"unknown check variant {node.variant!r}")
    def _exec_CompareBlock(self, node, ctx):
        # Compare two named values. Returns a result exposing equality, both
        # sides, and (for numbers) the numeric difference. For test/diff use.
        def _resolve(name):
            v = ctx.get(name)
            return v.to_python() if isinstance(v, MohioValue) else v
        a = _resolve(node.name_a)
        b = _resolve(node.name_b)
        equal = (a == b)
        result = {'equal': equal, node.name_a: a, node.name_b: b}
        a_num = isinstance(a, (int, float)) and not isinstance(a, bool)
        b_num = isinstance(b, (int, float)) and not isinstance(b, bool)
        if a_num and b_num:
            result['difference'] = a - b
            result['absolute']   = abs(a - b)
            result['larger']     = max(a, b)
            result['smaller']    = min(a, b)
            # percent change from b to a, and the ratio a:b. Guard divide-by-zero.
            if b != 0:
                result['percentage'] = round((a - b) / b * 100, 2)
                result['ratio']      = round(a / b, 4)
            else:
                result['percentage'] = None
                result['ratio']      = None
        for stmt in (getattr(node, 'body', None) or []):
            self._exec(stmt, ctx)
        rv = MohioValue(result, 'comparison')
        # bind the result to `comparison` so it can be read after the block
        ctx.set('comparison', rv)
        if getattr(self, 'verbose', False):
            print(f"  [compare] {node.name_a} to {node.name_b}: equal={equal}")
        return rv
    def _exec_SummarizeBlock(self, node, ctx): return self._stub('summarize', node, ctx)
    def _exec_CalculateBlock(self, node, ctx): return self._stub('calculate', node, ctx)
    def _exec_JoinBlock(self, node, ctx):      return self._stub('join', node, ctx)

    # ── Saga / Step ───────────────────────────────────────────

    def _exec_SagaDecl(self, node, ctx):
        # Saga execution per the ratified design ruling
        # (Docs/saga-step-semantics-for-design-chat.md).
        # saga == named alias of `try in sequence`: steps run in order, sharing one
        # saga scope. On a non-best-effort step failure, completed non-best-effort
        # steps are compensated in reverse; best-effort steps live outside the
        # consistency guarantee; the saga resolves to exactly one terminal status.
        if getattr(self, '_in_saga', False):
            raise MohioRuntimeError(
                f"nested sagas are not supported in v1 (saga '{getattr(node, 'name', '?')}' "
                f"runs inside another saga, directly or via a called task). Flatten the "
                f"steps or extract the inner work into non-saga tasks.")
        self._in_saga = True
        try:
            saga_ctx = ctx.child()          # shared scope: a step's compensate sees
                                            # the bindings that step's body created
            completed = []                  # completed NON-best-effort steps (rollback set)
            outcomes = []                   # per-step outcomes -> result.steps
            status = 'COMMITTED'
            for step in node.steps:
                try:
                    self._exec_block(step.body, saga_ctx)
                except (_GiveBack, _Raise):
                    # Local reaction first: the failing step's own on.failure runs now,
                    # before the saga decides anything.
                    self._run_step_handlers(step, OnFailure, saga_ctx)
                    if step.best_effort:
                        # Outside the transaction: swallow, log, continue. Never compensated.
                        outcomes.append({'step': step.name, 'outcome': 'best_effort_failed'})
                        if self.verbose:
                            print(f"  [saga] best-effort step '{step.name}' failed -- continuing")
                        continue
                    # Non-best-effort failure -> roll back completed non-best-effort steps.
                    outcomes.append({'step': step.name, 'outcome': 'failed'})
                    if self.verbose:
                        print(f"  [saga] step '{step.name}' failed -- compensating")
                    failed_comp = self._run_compensation_chain(completed, saga_ctx, outcomes)
                    status = 'FAILED_COMPENSATION' if failed_comp else 'COMPENSATED'
                    break
                else:
                    # Forward action succeeded: local on.success now; register for rollback.
                    self._run_step_handlers(step, OnSuccess, saga_ctx)
                    outcomes.append({'step': step.name, 'outcome': 'completed'})
                    if not step.best_effort:
                        completed.append(step)
            # A saga that did not commit (compensated, or failed to compensate) leaves
            # this task in a failure state. Record it so a `give back` carrying a failure
            # status propagates through `call` instead of being swallowed to a value
            # (saga-compensation status propagation; plain-task give back stays value-only).
            if status in ('COMPENSATED', 'FAILED_COMPENSATION'):
                self._saga_failed = True
            saga_result = MohioValue({'status': status, 'steps': outcomes}, 'shape')
            # Bind the status object to the saga's name in the ENCLOSING scope, the
            # same way find/retrieve/grab bind their result. A caller then reads
            # `<saga_name>.status` (COMMITTED / COMPENSATED / FAILED_COMPENSATION) and
            # `<saga_name>.steps`. (Binding goes on ctx, not the saga's child scope.)
            if getattr(node, 'name', None):
                ctx.set(node.name, saga_result)
            return saga_result
        finally:
            self._in_saga = False

    def _run_step_handlers(self, step, handler_type, ctx):
        """Run a step's first on.success / on.failure handler (local, immediate)."""
        for h in getattr(step, 'handlers', []) or []:
            if isinstance(h, handler_type):
                self._exec_block(h.body, ctx)
                break

    def _run_compensation_chain(self, completed, ctx, outcomes):
        """Compensate completed non-best-effort steps in REVERSE order. A compensate
        that itself fails -- or a completed step with no compensate -- means
        consistency cannot be guaranteed (FAILED_COMPENSATION), but rollback
        CONTINUES to undo as much as possible. Returns True if compensation failed."""
        failed = False
        for step in reversed(completed):
            if not step.undo:
                failed = True
                outcomes.append({'step': step.name, 'outcome': 'no_compensation'})
                if self.verbose:
                    print(f"  [saga] '{step.name}' completed but has no compensate -- FAILED_COMPENSATION")
                continue
            try:
                self._exec_block(step.undo, ctx)
                outcomes.append({'step': step.name, 'outcome': 'compensated'})
            except Exception:
                # A compensate failing must not abort the rest of the rollback.
                failed = True
                outcomes.append({'step': step.name, 'outcome': 'compensation_failed'})
                if self.verbose:
                    print(f"  [saga] compensate for '{step.name}' FAILED -- continuing rollback")
        return failed

    def _exec_StepBlock(self, node, ctx):
        # A step only runs inside a saga; the saga drives step execution. Standalone
        # is a developer error, so fail loud rather than silently running the body.
        raise _Raise(
            'step_outside_saga',
            f"step '{getattr(node, 'name', '?')}' can only run inside a saga.",
            line=getattr(node, 'line', None))

    # ── Task / Call ───────────────────────────────────────────

    def _eval_CallBlock(self, node, ctx):
        """A call used as a VALUE (`total = call add with 2`). The task must declare a
        return type -- a procedure has no value to give."""
        task = ctx.get_task(node.task_name)
        if task is not None and not getattr(task, 'return_type', None):
            raise MohioRuntimeError(
                f"`call {node.task_name}` is used as a value, but task "
                f"'{node.task_name}' does not declare one. A task returns a value only "
                f"when it declares a return type: "
                f"`task {node.task_name} <param> as <type> returns <type>`.")
        return self._exec_CallBlock(node, ctx)

    def _exec_CallBlock(self, node, ctx):
        """call TaskName / arg_name arg_value / call: done"""
        task = ctx.get_task(node.task_name)
        if task is None:
            if self.verbose:
                print(f"  [call] task '{node.task_name}' not found")
            return None

        child = ctx.child()

        # `call NAME ... as RESULT` asks the task for a VALUE. Only a task that declares
        # `returns <type>` is a value-producing function; a task with no `returns` is a
        # procedure whose `give back` is a RESPONSE. Capturing from a procedure used to
        # bind None silently -- the worst failure mode we have. Fail loud with the fix.
        if getattr(node, 'alias', '') and not getattr(task, 'return_type', None):
            raise MohioRuntimeError(
                f"`call {node.task_name} ... as {node.alias}` asks for a value, but task "
                f"'{node.task_name}' does not declare one. "
                f"A task returns a value only when it declares a return type: "
                f"`task {node.task_name} <param> as <type> returns <type>`. "
                f"Without `returns`, a task is a procedure -- its `give back` is a response, "
                f"not a value for the caller."
            )

        # Param lookup for validation + typed coercion. Each of the four checks below
        # replaces a verified SILENT no-op (unknown/extra arg ignored, "cat" bound to an
        # int param, a missing required param bound to None).
        param_by_name = {p.name: p for p in (task.params or [])}

        def _bind_typed(pname, raw):
            """A NUMERIC-typed param routes its incoming value through the existing
            coercion (`_mohio_coerce_number`), which fails loud on a mismatch such as
            "cat" -> int. Untyped/text/shape params bind the value as-is. -- (b)"""
            p = param_by_name.get(pname)
            t = (getattr(p, 'type_name', None) or 'any').lower()
            _num = {'int': 'int', 'integer': 'int', 'number': 'number',
                    'num': 'number', 'decimal': 'decimal', 'dec': 'decimal'}
            if t in _num:
                raw_py = raw.to_python() if isinstance(raw, MohioValue) else raw
                num, kind = _mohio_coerce_number(raw_py, _num[t], None, pname)
                return num if isinstance(num, MohioValue) else MohioValue(num, kind)
            return raw

        # Named args block form
        if node.args:
            for fv in node.args:
                if isinstance(fv, FieldValue):
                    if fv.name not in param_by_name:                       # (c) unknown arg
                        raise MohioRuntimeError(
                            f"task '{node.task_name}' has no parameter '{fv.name}'. "
                            f"Its parameters are: {', '.join(param_by_name) or '(none)'}. "
                            f"Check the name, or declare '{fv.name}' with a `take` line in the task.")
                    self._require_defined(fv.value, ctx, f"argument '{fv.name}'")
                    val = self._eval(fv.value, ctx)
                    child.set(fv.name, _bind_typed(fv.name, val))          # (b) typed coercion
        # Inline with form: call TaskName with value
        elif node.inline_arg is not None:
            self._require_defined(node.inline_arg, ctx, f"argument to task '{node.task_name}'")
            val = self._eval(node.inline_arg, ctx)
            if task.params:
                pname = task.params[0].name
                child.set(pname, _bind_typed(pname, val))                  # (b)
            else:                                                          # (d) extra arg
                raise MohioRuntimeError(
                    f"task '{node.task_name}' takes no arguments, but a value was passed. "
                    f"Declare an input with a `take` line, or call it with no argument "
                    f"(`call {node.task_name} / call: done`).")

        # Set defaults for missing params
        for param in task.params:
            if param.name not in child._vars and param.default is not None:
                child.set(param.name, self._eval(param.default, ctx))

        # (a) missing required: a param with NO default that the call didn't supply.
        # (Runs after defaults so only genuinely-required params trip.)
        for param in (task.params or []):
            if param.default is None and param.name not in child._vars:
                raise MohioRuntimeError(
                    f"task '{node.task_name}' requires '{param.name}'. Pass it "
                    f"(`call {node.task_name} with <value>`, or "
                    f"`call {node.task_name} / {param.name} <value> / call: done`), "
                    f"or give '{param.name}' a default.")

        # Snapshot and clear the saga-failure flag for the duration of this task call,
        # so we can tell whether a saga *inside this task* failed.
        prev_saga_failed = getattr(self, '_saga_failed', False)
        self._saga_failed = False
        try:
            result = self._exec_block(task.body, child)
            return result
        except _GiveBack as gb:
            # `give back` does one job — give back to the caller — but the context
            # differs. A task declared with `returns <type>` is a value-producing
            # function: its `give back` is the RETURN VALUE the caller consumes.
            # A task with no `returns` is a procedure: its `give back` is a RESPONSE,
            # so it propagates (status intact) up to end the request rather than
            # being swallowed into a bare value at the call boundary.
            #
            # Exception (ruled): if a saga inside this task compensated or failed to
            # compensate AND the give back carries a failure status (>= 400), the
            # failure response propagates even from a returns-typed task. A plain
            # task with no failed saga stays value-only.
            saga_failed = getattr(self, '_saga_failed', False)
            status = gb.status or 200
            if getattr(task, 'return_type', None) and not (saga_failed and status >= 400):
                val = gb.value
                mv = val if isinstance(val, MohioValue) else MohioValue(val)
                if getattr(node, 'alias', ''):
                    ctx.set(node.alias, mv)   # `call ... as greeting` captures the return
                return mv
            raise
        finally:
            self._saga_failed = prev_saga_failed

    # ── Make ─────────────────────────────────────────────────

    def _exec_RunBlock(self, node, ctx):
        """
        run TaskName / arg value / run: done
        RunBlock = CallBlock (same AST node, alias).
        Route directly to _exec_CallBlock.
        """
        return self._exec_CallBlock(node, ctx)

    def _split_sql_statements(self, sql):
        """Split a raw SQL block into statements on ';', but NOT on a ';' that sits
        inside a string literal. Handles single and double quotes, including the
        SQL doubled-quote escape ('' and ""). Far safer than a plain split(';')."""
        stmts, cur = [], []
        in_s = in_d = False
        i, n = 0, len(sql)
        while i < n:
            ch = sql[i]
            if ch == "'" and not in_d:
                # doubled '' inside a single-quoted string is an escaped quote
                if in_s and i + 1 < n and sql[i + 1] == "'":
                    cur.append("''"); i += 2; continue
                in_s = not in_s
            elif ch == '"' and not in_s:
                if in_d and i + 1 < n and sql[i + 1] == '"':
                    cur.append('""'); i += 2; continue
                in_d = not in_d
            elif ch == ';' and not in_s and not in_d:
                stmts.append(''.join(cur)); cur = []; i += 1; continue
            cur.append(ch); i += 1
        if ''.join(cur).strip():
            stmts.append(''.join(cur))
        return [s.strip() for s in stmts if s.strip()]

    def _exec_SqlBlock(self, node, ctx):
        """
        sql
            SELECT * FROM rooms WHERE id = {{ current_room }}
        sql: done [as result_name]

        Raw SQL escape hatch with {{ }} template interpolation.
        Interpolates variables from ctx before execution.
        Returns list of dicts for SELECT, row count for INSERT/UPDATE/DELETE.
        """
        import re as _re

        # SECURITY: raw sql bypasses the compiler's compliance enforcement, so it is
        # not allowed in a certified sector (financial/healthcare) where baselines are
        # cemented -- a raw INSERT must not be able to slip a card_cvv or PHI past the
        # guardrails. Fail loud instead.
        _sec = getattr(ctx, '_sector', None)
        if _sec and str(_sec).split('.')[0].strip().lower() in ('financial', 'healthcare'):
            raise _Raise(
                error_name='sql.blocked_in_certified_sector',
                message=(f"Raw sql is not allowed in a certified sector ('{_sec}') -- "
                         f"it would bypass the sector's compliance enforcement."),
                line=getattr(node, 'line', None),
                hint=("Use the data verbs (save / find / update / modify / remove) so the "
                      "sector's rules are enforced. Raw sql is available outside certified "
                      "sectors."))

        # An empty list here is a LIE: it says the query ran and matched nothing. It never
        # ran. The only trace was a --verbose print nobody reads in production.
        db, _early = self._db_or_fail(ctx, 'sql', node)
        if db is None: return _early

        # Interpolate {{ variable }} from context
        sql_text = node.sql or ''
        params = []

        def interpolate(m):
            var_name = m.group(1).strip()
            # Resolve dotted names
            if '.' in var_name:
                parts = var_name.split('.')
                val = ctx.get_dotted(parts)
            else:
                val = ctx.get(var_name)
            raw = val.to_python() if isinstance(val, MohioValue) else val
            # Use parameterized query for safety
            params.append(raw if raw is not None else '')
            return '?'  # SQLite placeholder — postgres uses %s

        sql_interpolated = _re.sub(r'\{\{\s*([\w.]+)\s*\}\}', interpolate, sql_text)

        is_pg = type(db).__name__ == 'PostgresRuntime'

        # Split into statements so one block can run a whole script
        # (CREATE + INSERT + ...). Naive split on ';' -- a ';' inside a string
        # literal would mis-split (documented escape-hatch limitation).
        statements = self._split_sql_statements(sql_interpolated)
        if not statements:
            return MohioValue([], 'list')

        # No-conn backends (execute_raw path): run the whole thing as one.
        if not hasattr(db, 'conn'):
            rows = db.execute_raw(sql_interpolated, params) if hasattr(db, 'execute_raw') else []
            result = MohioValue(rows or [], 'list')
            if node.alias:
                ctx.set(node.alias, result)
            else:
                ctx.set('_sql_result', result)
            return result

        conn = db.conn
        param_idx = 0
        result = MohioValue(True, 'boolean')
        try:
            for stmt in statements:
                n = stmt.count('?')
                stmt_params = params[param_idx:param_idx + n]
                param_idx += n
                exec_sql = stmt.replace('?', '%s') if is_pg else stmt
                is_select = stmt.strip().upper().startswith(('SELECT', 'WITH'))
                if is_pg:
                    # psycopg2: run on a dict cursor. The connection has no
                    # .execute() or .row_factory (those are the sqlite/psycopg3 API).
                    cur = conn.cursor(cursor_factory=db._cursor_factory)
                    cur.execute(exec_sql, stmt_params)
                    if is_select:
                        rows = [dict(r) for r in cur.fetchall()]
                        result = MohioValue(rows, 'list')
                    else:
                        rc = cur.rowcount if (cur.rowcount is not None and cur.rowcount >= 0) else 0
                        new_id = None
                        try:
                            if cur.description:            # INSERT ... RETURNING id
                                first = cur.fetchone()
                                if first:
                                    new_id = list(dict(first).values())[0]
                        except Exception:
                            pass
                        result = MohioValue({'count': rc, 'id': new_id}, 'shape')
                    cur.close()
                elif is_select:
                    _saved_factory = conn.row_factory
                    conn.row_factory = lambda c, r: {
                        desc[0]: r[i] for i, desc in enumerate(c.description)
                    } if c.description else {}
                    try:
                        cur = conn.execute(exec_sql, stmt_params)
                        rows = cur.fetchall()
                    finally:
                        conn.row_factory = _saved_factory   # restore, never leave None
                    result = MohioValue(rows, 'list')
                else:
                    cur = conn.execute(exec_sql, stmt_params)
                    rc = cur.rowcount if (cur.rowcount is not None and cur.rowcount >= 0) else 0
                    result = MohioValue({'count': rc, 'id': cur.lastrowid}, 'shape')
            conn.commit()
        except Exception as e:
            # Fail loud: the developer's SQL is wrong -- never silently swallow it.
            try: conn.rollback()
            except Exception: pass
            raise _Raise(
                error_name='sql.error',
                message=f"Raw sql failed: {e}",
                line=getattr(node, 'line', None),
                hint="Check the SQL syntax and that referenced tables and columns exist.")

        # Bind to alias if declared (sql: done as result_name)
        if node.alias:
            ctx.set(node.alias, result)
        else:
            ctx.set('_sql_result', result)

        if self.verbose:
            print(f"  [sql] {len(statements)} statement(s) ok")

        return result

    def _exec_CreateBlock(self, node, ctx):
        result = {}
        if node.from_source:
            src = self._eval(node.from_source, ctx)
            raw = src.to_python() if isinstance(src, MohioValue) else src
            if isinstance(raw, dict): result.update(raw)

        for fv in node.body:
            if isinstance(fv, FieldValue):
                self._require_defined(fv.value, ctx, f"create field '{fv.name}'")  # A3.1
                result[fv.name] = self._eval_simple(fv.value, ctx)

        # Enforce the shape's declared scalar field types (same contract as standalone `x as int`):
        # a field declared `age as int` rejects a text value, fail-loud, consistent with variable
        # type enforcement. None/absent values are not type-checked here (required-ness is a
        # separate, form-level concern). Non-scalar field types (shape refs, lists) are not
        # scalar-checked. Only runs when the instance is bound to a known shape.
        shape_ref = getattr(node, 'shape', None)
        if shape_ref:
            shape_name = str(shape_ref).split('.')[-1]      # sh.P -> P
            shape_def = ctx.get_shape(shape_name) if hasattr(ctx, 'get_shape') else None
            if shape_def is not None:
                for field in (getattr(shape_def, 'fields', None) or []):
                    fname = getattr(field, 'name', None)
                    ftype = getattr(field, 'type_name', None) or getattr(field, 'field_type', None)
                    if fname in result and ftype:
                        fval = result[fname]
                        fpy = fval.to_python() if isinstance(fval, MohioValue) else fval
                        if not self._value_matches_type(fpy, ftype):
                            _what = ('text' if isinstance(fpy, str)
                                     else 'a decimal' if isinstance(fpy, float)
                                     else 'a boolean' if isinstance(fpy, bool)
                                     else type(fpy).__name__)
                            raise MohioRuntimeError(
                                f"{shape_name}.{fname} is declared as {ftype}, but {fpy!r} is "
                                f"{_what}. A shape field type is a contract -- assign a matching "
                                f"value or cast it (e.g. `(... as.{str(ftype).split('.')[0]})`).")
                        # dec.N field: truncate the value to N places (same precision contract as a
                        # standalone dec.N variable). .pad display is not yet built -- fail loud.
                        # Currency field: round half-up to the currency's places and tag the value.
                        if str(ftype).upper() in self._CURRENCIES and fpy is not None:
                            _cur = str(ftype).upper()
                            _cv = MohioValue(
                                self._round_places(fpy, self._CURRENCIES[_cur]['places']), 'decimal')
                            _cv._currency = _cur
                            result[fname] = _cv
                        else:
                            _fparts = str(ftype).split('.')
                            if _fparts[0] in ('dec', 'decimal') and len(_fparts) >= 2:
                                if _fparts[1].isdigit() and fpy is not None:
                                    _cv = MohioValue(
                                        self._truncate_places(fpy, int(_fparts[1])), 'decimal')
                                    if str(ftype).endswith('.pad'):
                                        _cv._pad_places = int(_fparts[1])
                                    result[fname] = _cv

        shape_val = MohioValue(result, 'shape')
        var_name  = node.name[0].lower() + node.name[1:] if node.name else 'result'
        ctx.set(var_name, shape_val)
        if node.name and node.name != var_name:
            ctx.set(node.name, shape_val)

        if self.verbose: print(f"  [make] {node.name} ({len(result)} fields)")
        return shape_val

    # ── New v3.8 verb stubs ───────────────────────────────────

    def _exec_ApplyBlock(self, node, ctx):           return self._stub('apply', node, ctx)
    def _exec_ApplyCollectionBlock(self, node, ctx): return self._stub('apply collection', node, ctx)
    def _exec_ModifyBlock(self, node, ctx):
        """Bulk update: modify every X in COLLECTION [where COND] / apply X / field value.
        For each row in the collection that matches the condition, apply the field
        changes. Works on a db table (persists via db.update) or an in-memory list.
        Returns the number of rows modified."""
        from mohio_ast import DbRef, DottedName
        rows = self._random_collection(node.collection, ctx)
        if rows is None:
            raise _Raise(error_name='modify_source_not_collection',
                message="modify needs a db table or a list to work on.",
                line=getattr(node, 'line', None),
                hint=("Use:  modify every X in db.<table> [where ...]  /  apply X  /  "
                      "field value  /  apply: done  /  modify: done"))
        is_db = isinstance(node.collection, DbRef) or (
            isinstance(node.collection, DottedName)
            and getattr(node.collection, 'parts', None)
            and str(node.collection.parts[0]) == 'db')
        # A modify aimed at a DB TABLE with no connection used to fall through to the
        # in-memory path and change nothing on disk, silently. If the target is a table,
        # the connection is not optional.
        if is_db:
            db, _early = self._db_or_fail(ctx, 'modify', node)
            if db is None: return _early
        else:
            db = None
        table = self._resolve_source(node.collection, ctx) if is_db else None
        count = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            child = ctx.child()
            if node.noun:
                child.set(node.noun, MohioValue(dict(row), 'record'))
            for k, v in row.items():
                child.set(k, v if isinstance(v, MohioValue) else MohioValue(v))
            # filter (variant 'all' modifies every row; 'every' respects the where)
            if node.condition is not None and not self._eval_condition(node.condition, child):
                continue
            changes = {}
            for fv in node.body:
                # A3.1: eval in `child` (the row scope), so a field referencing a row column
                # is defined; only a truly-undefined bare name fails loud.
                self._require_defined(fv.value, child, f"modify field '{fv.name}'")
                val = self._eval(fv.value, child)
                changes[fv.name] = val.to_python() if isinstance(val, MohioValue) else val
            if not changes:
                continue
            if is_db and db is not None:
                # No portable row id is exposed, so match the row on its ORIGINAL
                # field values. Correct for unique rows; identical duplicate rows
                # are updated together (documented edge case).
                match = {k: (v.to_python() if isinstance(v, MohioValue) else v)
                         for k, v in row.items()}
                if match:
                    db.update_multi(table, changes, match)
            else:
                row.update(changes)
            count += 1
        if is_db and table:
            # A data change that cannot be audited must NOT silently succeed -- same principle as
            # cm.purge. Audit like the fail-loud siblings (save/remove): NO try/except swallow, so an
            # audit-write failure raises rather than letting the modify pass unrecorded. (When no
            # active sector requires auditing, _audit_data_change is a clean no-op.)
            self._audit_data_change('modify', table, ctx, count=count,
                                    fields=[fv.name for fv in node.body])
        if self.verbose: print(f"  [modify] {count} row(s) in {table or 'list'}")
        return MohioValue(count, 'number')
    def _exec_CopyBlock(self, node, ctx):            return self._stub('copy', node, ctx)
    def _exec_RequestOutboundBlock(self, node, ctx):
        # A5: `request outbound` is RETIRED. It collided head-on with miohttp.* and
        # provided no distinct behavior, and it was silently no-opping via _stub.
        raise MohioRuntimeError(
            "request outbound is retired -- use miohttp.get / miohttp.post / "
            "miohttp.put / miohttp.delete / miohttp.patch for outbound HTTP (or "
            "mioconnect for named services, which compiles to miohttp). It would "
            "otherwise silently do nothing.")
    def _exec_RerunStmt(self, node, ctx):            return self._stub('rerun', node, ctx)
    def _exec_SignBlock(self, node, ctx):            return self._stub('sign', node, ctx)
    def _exec_ValidateStmt(self, node, ctx):
        """Apply a named rule set to data. Source is either an explicit
        `against <expr>` value or, for `using`, a named variable / the inbound
        `request`. Failures collect into `errors`; on.failure / on.success fire.
        If validation fails with no on.failure handler, it fails loud rather
        than letting unvalidated data pass on."""
        rules = self._validation_rules.get(node.rules_name)
        if rules is None:
            raise _Raise(error_name='unknown_validation_ruleset',
                message=f"validate refers to an unknown rule set '{node.rules_name}'.",
                line=getattr(node, 'line', None),
                hint=f"Declare it first:  miovalidate {node.rules_name} ... miovalidate: done")

        data   = self._resolve_validation_source(node, ctx, rules)
        errors = self._run_validation(rules, data, ctx, getattr(node, 'line', None))
        ctx.set('errors', MohioValue(errors, 'list'))

        if errors:
            ran = False
            for h in node.handlers:
                if isinstance(h, OnFailure):
                    ran = True
                    return self._exec_block(h.body, ctx)   # may raise _GiveBack — propagates
            if not ran:
                raise _Raise(error_name='validation_failed',
                    message=(f"validation failed ({len(errors)} error(s)); first: "
                             f"{errors[0]['message']}"),
                    line=getattr(node, 'line', None),
                    hint="Add an 'on.failure' handler to validate, or correct the input.")
            return None
        for h in node.handlers:
            if isinstance(h, OnSuccess):
                return self._exec_block(h.body, ctx)
        return None

    def _resolve_validation_source(self, node, ctx, rules=None):
        """Return the data dict to validate. against -> explicit expr;
        using with a name -> that variable; using bare -> inbound request, or,
        when there is no inbound request, the in-scope variables for the rule's
        fields (so `hold email "..." / validate using EmailRule` validates the
        local `email`), merged with the request (non-empty request values override)."""
        if getattr(node, 'variant', 'using') == 'against' and node.source is not None:
            val = self._eval(node.source, ctx)
        elif isinstance(node.source, str) and node.source:
            val = ctx.get(node.source)
        else:
            val = ctx.get('request')
            req = val.to_python() if isinstance(val, MohioValue) else val
            req = req if isinstance(req, dict) else {}
            # Merge: the rule's fields resolved from in-scope variables, then overlaid
            # by any non-empty request values. So `hold email "..." / validate using R`
            # validates the held email whether or not there is a request, and a served
            # form submission still overrides the held value.
            merged = {}
            if rules:
                for r in rules:
                    fname = getattr(r, 'field_name', None)
                    if fname and ctx.exists(fname):
                        fv = ctx.get(fname)
                        merged[fname] = fv.to_python() if isinstance(fv, MohioValue) else fv
            merged.update({k: v for k, v in req.items() if v not in (None, "")})
            return merged
        py = val.to_python() if isinstance(val, MohioValue) else val
        return py if isinstance(py, dict) else ({} if py is None else py)

    def _run_validation(self, rules, data, ctx, line=None):
        """Apply each rule to data; return a list of {field, message} errors."""
        errors = []
        if not isinstance(data, dict):
            # Source resolved to a non-record value — can't field-validate it.
            raise _Raise(error_name='validation_source_invalid',
                message="validate needs record-shaped data (named fields), but the source is not a record.",
                line=line,
                hint="Validate a request payload, a shape, or a key/value map.")
        for rule in rules:
            fname = rule.field_name
            mods  = rule.modifiers or []
            kinds = {m.get('kind') for m in mods if isinstance(m, dict)}
            # unique / scheme can't be honestly enforced yet — fail loud, never silently pass
            if 'unique' in kinds or 'scheme' in kinds:
                bad = 'unique' if 'unique' in kinds else 'scheme'
                raise _Raise(error_name='validation_rule_unsupported',
                    message=f"the '{bad}' rule on '{fname}' is not enforceable in this build.",
                    line=line,
                    hint=(f"Remove '{bad}' from the miovalidate rule for now — leaving it in would "
                          f"give false assurance that '{fname}' was checked."))
            present = fname in data and data[fname] not in (None, "")
            if not present:
                if 'optional' in kinds:
                    continue
                errors.append({'field': fname, 'message': f"{fname} is required."})
                continue
            value = data[fname]
            type_err = self._validate_type(fname, value, rule.type_name)
            if type_err:
                errors.append({'field': fname, 'message': type_err})
                continue
            for m in mods:
                k = m.get('kind')
                if k == 'length':
                    n = len(str(value))
                    if n < m['min'] or n > m['max']:
                        errors.append({'field': fname,
                            'message': f"{fname} must be {m['min']} to {m['max']} characters (got {n})."})
                elif k == 'between':
                    try:
                        num = float(value)
                        if num < m['min'] or num > m['max']:
                            errors.append({'field': fname,
                                'message': f"{fname} must be between {m['min']} and {m['max']} (got {value})."})
                    except (TypeError, ValueError):
                        errors.append({'field': fname,
                            'message': f"{fname} must be a number to check its range."})
        return errors

    def _validate_type(self, fname, value, type_name):
        """Return None if value matches the type, else a clear error message."""
        t = (type_name or 'any').strip().lower()
        s = str(value)
        if t in ('', 'any', 'text', 'string', 'json', 'list', 'map',
                 'base64', 'image', 'audio', 'video', 'pdf', 'datetime', 'time'):
            return None  # accepted as-is (not a simple-format check here)
        if t in ('integer', 'int'):
            try: int(s); return None
            except (TypeError, ValueError): return f"{fname} must be a whole number."
        if t in ('number', 'decimal', 'float'):
            try: float(s); return None
            except (TypeError, ValueError): return f"{fname} must be a number."
        if t in ('boolean', 'bool'):
            if s.lower() in ('true', 'false', '1', '0', 'yes', 'no'): return None
            return f"{fname} must be true or false."
        if t == 'email':
            return None if re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', s) else f"{fname} must be a valid email address."
        if t == 'url':
            return None if re.match(r'^https?://\S+$', s) else f"{fname} must be a valid URL (http:// or https://)."
        if t == 'uuid':
            return None if re.match(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$', s) else f"{fname} must be a valid UUID."
        if t == 'date':
            return None if re.match(r'^\d{4}-\d{2}-\d{2}$', s) else f"{fname} must be a date (YYYY-MM-DD)."
        return None  # unknown type name — don't block

    def _exec_ValidateStmt_legacy_stub(self, node, ctx):  return self._stub('validate', node, ctx)

    # ── AI Primitives ─────────────────────────────────────────

    def _exec_AiDecideInvoke(self, node, ctx):
        """ai.decide <name> -- run a previously-declared ai.decide block by name.
        Running it binds the result to a variable named <name> (see the result-bind
        in _exec_AiDecideBlock), which is what the following `check <name>` reads.
        This is the declare-once / invoke-many pattern."""
        block = getattr(self, '_ai_blocks', {}).get(node.name)
        if block is None:
            raise MohioRuntimeError(
                f"ai.decide {node.name}: no ai.decide block named '{node.name}' is "
                f"declared. Declare it with `ai.decide {node.name} returns <type> ... "
                f"ai.decide: done`, then invoke it with `ai.decide {node.name}`.")
        return self._exec_AiDecideBlock(block, ctx)

    def _exec_AiDecideBlock(self, node, ctx):
        # Register this decision by name so a later bare `ai.decide <name>`
        # invocation can re-run it (see _exec_ServiceCallStmt, service 'ai').
        if not hasattr(self, '_ai_blocks'):
            self._ai_blocks = {}
        self._ai_blocks[node.name] = node
        # 1. Collect weigh inputs
        weigh  = next((b for b in node.body if isinstance(b, WeighClause)), None)
        inputs = {}
        if weigh:
            for dotted in weigh.inputs:
                key = '.'.join(dotted.parts) if isinstance(dotted, DottedName) else str(dotted)
                val = ctx.get_dotted(dotted.parts) if isinstance(dotted, DottedName) else ctx.get(str(dotted))
                inputs[key] = val

        # Strong enforcement: under an active sector, ai.decide MUST audit (compliance feature).
        _sector_active = getattr(ctx, '_sector', None) not in (None, '', 'none') \
                         or bool(getattr(ctx, '_sector_compliance', None))
        if _sector_active and not any(isinstance(b, AiAuditStmt) for b in node.body):
            raise _Raise(error_name='audit_required',
                message=f"ai.decide '{node.name}' under an active sector must declare 'ai.audit to <log>'.",
                line=getattr(node, 'line', None),
                hint="Add 'ai.audit to <log_name>' inside this ai.decide. Audit is mandatory under a sector.")

        # 2. Get threshold
        conf   = next((b for b in node.body if isinstance(b, ConfidenceCheck)), None)
        threshold = 0.85
        if conf:
            t = self._eval(conf.threshold, ctx)
            threshold = float(t.to_python() if isinstance(t, MohioValue) else t)
        # Check hold constants too (FRAUD_THRESHOLD, etc.)
        for name in ('FRAUD_THRESHOLD', 'TRIAGE_THRESHOLD'):
            held = ctx.get(name)
            if held and held.to_python() is not None:
                threshold = float(held.to_python())
                break

        # Sector floor: a regulated decision cannot be gated below its profile minimum.
        _profile = getattr(ctx, '_sector_profile', None)
        if _profile is not None:
            try:
                _floor = _profile.get_confidence_floor(node.name)
            except Exception:
                _floor = 0.0
            if _floor and _floor > threshold:
                if self.verbose:
                    print(f"  [ai.decide] sector floor raised threshold {threshold} -> {_floor} for '{node.name}'")
                threshold = _floor

        # 3. Read goal/persona/context/temperature/model from AST node fields
        #    (extracted cleanly by transformer — no fallthrough to statement parsing)
        system_prompt = None
        context_str   = None
        temperature   = getattr(node, 'temperature', None)
        model_override = getattr(node, 'model', None) or None

        # goal is "what to decide" — replaces generic decision description
        goal = getattr(node, 'goal', '') or ''
        if goal:
            system_prompt = self._interpolate(goal, ctx)

        # persona is "how to explain" — shapes explanation field only
        # For ai.decide it appends to system_prompt with explanation-only scope
        persona = getattr(node, 'persona', '') or ''
        if persona and system_prompt:
            system_prompt = system_prompt  # persona handled in mohio_ai._build_system_prompt
        elif persona:
            system_prompt = self._interpolate(persona, ctx)

        # context is situational info — interpolated and added to user prompt
        context_raw = getattr(node, 'context', '') or ''
        if context_raw:
            context_str = self._interpolate(context_raw, ctx)

        # 4. Decide
        # ── ai.connect chain reference: `using <chain>` ──
        chain_name = None
        for _item in (getattr(node, 'body', None) or []):
            if type(_item).__name__ == 'UsingChain':
                chain_name = getattr(_item, 'chain_name', None) or None
                break
        if chain_name and hasattr(self.ai, 'resolve_chain'):
            self.ai.resolve_chain(chain_name)   # pay resolution once, before any loop

        decide_kwargs = dict(
            name=node.name,
            inputs=inputs,
            threshold=threshold,
            return_type=node.return_type,
        )
        if system_prompt:   decide_kwargs['system_prompt']   = system_prompt
        if persona:         decide_kwargs['persona']         = self._interpolate(persona, ctx)
        if context_str:     decide_kwargs['context']         = context_str
        if temperature:     decide_kwargs['temperature']     = float(temperature)
        if model_override:  decide_kwargs['model_override']  = model_override
        if chain_name:      decide_kwargs['chain_name']      = chain_name

        self._charge_ai_call(node.name)
        try:
            decision = self.ai.decide(**decide_kwargs)
        except (_GiveBack, _Raise):
            raise
        except Exception as _ai_err:
            # on.error is retired; the AI call breaking IS on.failure ("it broke").
            _of = next((b for b in node.body if isinstance(b, OnFailure)), None)
            if _of is not None:
                self._exec_block(_of.body, ctx)
            raise _Raise(error_name='ai_error',
                message=f"ai.decide '{node.name}' failed: {str(_ai_err)[:120]}",
                line=getattr(node, 'line', None),
                hint="Add an 'on.failure' handler that gives back a response, or check the AI provider/credentials.")
        if self.verbose:
            print(f"  [ai.decide] {node.name}: {decision.result} "
                  f"(conf={decision.confidence:.2f}, threshold={threshold})")

        # 4. Write audit FIRST (must appear before not confident in source)
        audit = next((b for b in node.body if isinstance(b, AiAuditStmt)), None)
        if audit:
            self._write_ai_audit(audit.log_name, node.name, decision, ctx)

        # 5. Handle not confident
        if decision.confidence < threshold:
            decision.fell_back = True
            nc = next((b for b in node.body if isinstance(b, NotConfidentBlock)), None)
            if nc:
                try:
                    self._exec_block(nc.body, ctx)
                except _GiveBack as gb:
                    # `give back` inside `not confident` is the decision's FALLBACK
                    # RESULT, not a return from the surrounding request handler. Its
                    # value becomes the decision result, bound below (step 6), so the
                    # call site (`check <name>`) runs and the caller decides the
                    # response. This is what keeps an invoked resolver from
                    # short-circuiting the handler with an empty body.
                    decision.result = gb.value

        # on.failure is NOT fired here: 'not confident' is the sole below-threshold handler in
        # ai.decide. on.failure/on.success remain available as general handlers elsewhere.

        # 6. Bind result
        # Register the full decision object (confidence, model, inputs, explanation)
        # by name so ai.explain can look it up later. ctx only gets the result value.
        if not hasattr(self, '_ai_decisions'):
            self._ai_decisions = {}
        self._ai_decisions[node.name] = decision
        result_val = MohioValue(decision.result, node.return_type)
        ctx.set(node.name, result_val)
        # Run any ai.explain clause carried in this decide block now that the
        # decision is registered, binding its explanation into the surrounding
        # scope (the natural "decide, then explain it" reading).
        for _b in node.body:
            if isinstance(_b, AiExplainBlock):
                self._exec_AiExplainBlock(_b, ctx)
        return result_val

    def _classify_audit_field(self, field_name, ctx=None):
        """Return the data-classification tag for an audit field NAME, never its value.

        Used by the ai.decide audit so the trail records what a decision was made ABOUT
        ([phi]/[pci]/[pii]/...) without ever storing the sensitive value. Checks the same
        classification sets the rest of the enforcement uses, plus the active sector profile's
        field classifications (ctx._sector_profile) when one is present. Unclassified fields
        record "-".
        """
        name = str(field_name)
        # Highest-sensitivity wins if a field is multiply tagged.
        if name in (self._phi_fields or set()):
            return "[phi]"
        if name in (self._pci_fields or set()):
            return "[pci]"
        if name in (getattr(self, '_field_purposes', {}) or {}):
            return "[pii]"
        if name in (self._encrypted_fields or set()):
            return "[encrypted]"
        # Sector profile classifications, when a profile is active.
        try:
            prof = getattr(ctx, '_sector_profile', None) if ctx is not None else None
            if prof is not None and hasattr(prof, 'get_field_classifications'):
                classes = prof.get_field_classifications(name)
                if classes:
                    return "[" + sorted(classes)[0] + "]"
        except Exception:
            pass
        return "-"

    def _write_ai_audit(self, log_name, decision_name, decision, ctx):
        """
        Write an immutable AI decision audit record.

        Captures: decision name, result, confidence, model, fell_back flag,
        weigh inputs, sector profile, session context, and ISO timestamp.

        Piped to: (1) in-memory AuditLog per session,
                  (2) database table named after log_name — persistent record.

        Patent Claim 4: sector metadata is bound to every audit record
        automatically. The developer cannot omit it — it is injected by
        the interpreter, not the application code.
        """
        import datetime as _dt, hashlib as _hl

        ts = _dt.datetime.utcnow().isoformat() + 'Z'

        # Sector profile — bound automatically from interpreter state
        sector = getattr(self, '_sector', None) or getattr(ctx, '_sector', None) or 'none'

        # Session context — member_id if available, session_id
        session_id = None
        member_id  = None
        try:
            sess = ctx.get('session')
            if sess and isinstance(sess.to_python() if isinstance(sess, MohioValue) else sess, dict):
                sp = sess.to_python() if isinstance(sess, MohioValue) else sess
                session_id = sp.get('id') or sp.get('session_id')
                member_id  = sp.get('member_id') or sp.get('user_id')
        except Exception:
            pass

        # Build the input audit record. DESIGN RULING (design chat, 2026-07-15): store field
        # NAMES + CLASSIFICATION, never raw values, and never per-field plain hashes. A plain
        # hash of a low-entropy identifier (SSN, DOB, ZIP, boolean, small enum) is reversible in
        # milliseconds and counts as storing the value -- it would reintroduce the leak while
        # looking safe. So the base tier records only what the decision was made ABOUT, not the
        # data itself:  {"ssn": "[phi]", "balance": "[pci]", "age": "[pii]", "region": "-"}.
        #
        # Record-level binding (proving WHICH record a decision ran on, for dispute/contest) is
        # the future "detailed" paygate tier via a keyed HMAC over the canonical snapshot; the
        # base ship reserves the nullable `input_binding` column for it (below) so adding it later
        # does not require migrating an append-only log or breaking the hash chain.
        inputs_dict = {k: self._classify_audit_field(k, ctx)
                       for k in decision.inputs.keys()}
        input_binding = None   # reserved; filled only by the detailed (mioaudit) tier

        entry = {
            'event':          'ai.decide',
            'decision':       decision_name,
            'result':         str(decision.result),
            'confidence':     decision.confidence,
            'reasoning':      getattr(decision, 'explanation', None),
            'model':          decision.model,
            'fell_back':      decision.fell_back,
            'inputs':         inputs_dict,
            'input_binding':  input_binding,
            'sector':         sector,           # ← sector profile bound automatically
            'session_id':     session_id,       # ← session context
            'member_id':      member_id,        # ← actor identity
            'ts':             ts,               # ← ISO timestamp
            'log_name':       log_name,         # ← audit destination
        }

        # Deterministic audit ID -- hash of decision + the (now value-free) input classification
        # + timestamp. Note: hashing inputs_dict here is SAFE because inputs_dict no longer
        # contains values, only names and classifications.
        audit_id = _hl.sha256(
            f"{decision_name}:{json.dumps(inputs_dict, sort_keys=True)}:{ts}".encode()
        ).hexdigest()[:16]
        entry['audit_id'] = audit_id

        log = self._audit_logs.setdefault(log_name, AuditLog(log_name))
        log.record(entry)

        # Write to database -- persistent, durable, HASH-CHAINED audit trail.
        # This goes through the same chaining helper as every other audit writer. It used to call
        # db.save directly, which meant the primary ai.decide record -- the one carrying the
        # decision, its confidence, and the sector it was made under -- was written OUTSIDE the
        # chain: verification skipped it entirely (no entry_hash), so it could be altered or
        # removed and nothing would disagree. A chain that covers three of four writers is not a
        # chain, it is a chain-shaped claim.
        db = ctx.get_connection('db')
        if db:
            try:
                from mohio_audit_grades import canonical_audit_columns as _cac
                db.ensure_table(log_name, _cac())
                self._audit_chained_save(db, log_name, {
                    'audit_id':       audit_id,
                    'ts':             ts,
                    'event':          'ai.decide',
                    'agent':          decision_name,
                    'decision_name':  decision_name,
                    'inputs':         json.dumps(inputs_dict),   # names + classification only
                    'input_binding':  input_binding,             # reserved; null at base tier
                    'result':         entry['result'],
                    'confidence':     str(decision.confidence),
                    'model':          decision.model,
                    'fell_back':      str(decision.fell_back),
                    'sector':         sector,
                    'session_id':     str(session_id or ''),
                    'member_id':      str(member_id or ''),
                })
            except MohioInterpreter.AuditContentRefused:
                raise
            except Exception as e:
                if self.verbose:
                    print(f"  [ai.audit] db write failed: {e}")

        if self.verbose:
            print(f"  [ai.audit] -> {log_name} [{audit_id}] sector:{sector}")

    def _exec_AiAuditStmt(self, node, ctx):
        log = self._audit_logs.setdefault(node.log_name, AuditLog(node.log_name))
        log.record({'event': 'manual_audit'})

    def _ai_shared_inputs(self, node, ctx):
        """Collect `weigh` inputs and the prompt options shared by the ai.* blocks that
        reuse ai_decide_body (compare, respond)."""
        inputs = {}
        weigh = next((b for b in node.body if isinstance(b, WeighClause)), None)
        if weigh:
            for dotted in weigh.inputs:
                key = '.'.join(dotted.parts) if hasattr(dotted, 'parts') else str(dotted)
                inputs[key] = self._eval(dotted, ctx)
        kwargs = {'inputs': inputs, 'return_type': node.return_type or 'text'}
        if getattr(node, 'goal', ''):
            kwargs['system_prompt'] = self._interpolate(node.goal, ctx)
        if getattr(node, 'persona', ''):
            kwargs['persona'] = self._interpolate(node.persona, ctx)
        if getattr(node, 'context', ''):
            kwargs['context'] = self._interpolate(node.context, ctx)
        if getattr(node, 'temperature', None) is not None:
            t = self._eval(node.temperature, ctx)
            kwargs['temperature'] = t.to_python() if isinstance(t, MohioValue) else t
        if getattr(node, 'model', ''):
            kwargs['model_override'] = node.model
        return kwargs

    def _exec_AiResolveBlock(self, node, ctx):
        """ai.resolve -- progressive three-tier resolution (Provisional 1, Claim 3).

        One token expands into a pipeline that is tried cheapest-first:

            Tier 1  cache    in-memory lookup            free, instant
            Tier 2  learned  prior decisions in a table  near-zero, fast
            Tier 3  live     a declared ai.decide        full token cost, slow

        A Tier-3 result is written BACK to Tier 2 and Tier 1, so the next identical payload
        is answered for free. That write-back is the point: cost falls as the corpus grows.

        The tiers are keyed by a hash of the weighed payload, not by the raw values -- the
        hash is what makes "identical request" decidable without storing the inputs
        themselves, which also keeps regulated field values out of the learned table.
        """
        import hashlib, json as _json, datetime as _dt

        # ── resolve the Tier-3 decision (declared elsewhere, invoked here by name) ──
        live = node.live_block
        live_name = getattr(live, 'name', None)
        block = getattr(self, '_ai_blocks', {}).get(live_name) if live_name else None
        if live is not None and block is None and type(live).__name__ == 'AiDecideInvoke':
            raise MohioRuntimeError(
                f"ai.resolve {node.name}: Tier 3 names `{live_name}`, but no ai.decide block "
                f"called '{live_name}' is declared. Declare it before the resolve "
                f"(`ai.decide {live_name} returns <type> ... ai.decide: done`), then reference "
                f"it with `live ai.decide {live_name}`.")
        if block is None and type(live).__name__ == 'AiDecideBlock':
            block = live

        # ── payload hash: the weighed inputs of the Tier-3 decision ──
        payload = {}
        if block is not None:
            weigh = next((b for b in getattr(block, 'body', [])
                          if isinstance(b, WeighClause)), None)
            if weigh:
                for dotted in weigh.inputs:
                    key = '.'.join(dotted.parts) if hasattr(dotted, 'parts') else str(dotted)
                    try:
                        v = self._eval(dotted, ctx)
                        payload[key] = v.to_python() if isinstance(v, MohioValue) else v
                    except Exception:
                        payload[key] = None
        digest = hashlib.sha256(
            _json.dumps({'decision': live_name, 'inputs': payload}, sort_keys=True,
                        default=str).encode()).hexdigest()

        def _bind(value, tier):
            self._debug_trace(ctx, f"ai.resolve {node.name}: served from tier {tier}")
            v = value if isinstance(value, MohioValue) else MohioValue(value, 'any')
            if node.name:
                ctx.set(node.name, v)
                ctx.set(f"{node.name}.tier", MohioValue(tier, 'text'))
            return v

        # ── Tier 1: cache ──────────────────────────────────────────────────────────
        if not hasattr(self, '_resolve_cache'):
            self._resolve_cache = {}
        cache_bucket = node.cache_ref or node.name or 'default'
        cache_key = (cache_bucket, digest)
        if node.cache_ref is not None and cache_key in self._resolve_cache:
            return _bind(self._resolve_cache[cache_key], 'cache')

        # ── Tier 2: learned database ───────────────────────────────────────────────
        table = getattr(node.learned_ref, 'table', None) or (
            str(node.learned_ref) if node.learned_ref is not None else None)
        if table and getattr(self, '_db', None) is not None:
            try:
                self._db.ensure_table(table, {
                    'payload_hash': 'TEXT', 'result': 'TEXT',
                    'confidence': 'REAL', 'resolved_at': 'TEXT',
                })
                cur = self._db.conn.execute(
                    f"SELECT result FROM {table} WHERE payload_hash = ? LIMIT 1", (digest,))
                row = cur.fetchone()
                if row is not None:
                    learned = row['result'] if hasattr(row, 'keys') else row[0]
                    try:
                        learned = _json.loads(learned)
                    except Exception:
                        pass
                    if node.cache_ref is not None:
                        self._resolve_cache[cache_key] = learned
                    return _bind(learned, 'learned')
            except Exception as e:
                # A learned-tier miss must never take down the resolution: fall through to
                # live. It is a cost optimisation, not a correctness gate.
                self._debug_trace(ctx, f"ai.resolve {node.name}: tier 2 unavailable ({e})")

        # ── Tier 3: live decision, then write back to tiers 2 and 1 ────────────────
        if block is None:
            raise MohioRuntimeError(
                f"ai.resolve {node.name}: nothing to resolve. Tiers 1 and 2 missed and no "
                f"`live ai.decide <name>` tier is declared, so there is no way to produce an "
                f"answer. Add a live tier.")
        decision = self._exec_AiDecideBlock(block, ctx)
        result = decision.to_python() if isinstance(decision, MohioValue) else decision
        conf = 0.0
        if isinstance(result, dict):
            conf = float(result.get('confidence', 0.0) or 0.0)
        stored = result.get('result') if isinstance(result, dict) else result

        if table and getattr(self, '_db', None) is not None:
            try:
                self._db.conn.execute(
                    f"INSERT INTO {table} (payload_hash, result, confidence, resolved_at) "
                    f"VALUES (?, ?, ?, ?)",
                    (digest, _json.dumps(stored, default=str), conf,
                     _dt.datetime.now().isoformat()))
                self._db.conn.commit()
            except Exception as e:
                self._debug_trace(ctx, f"ai.resolve {node.name}: tier 2 write-back failed ({e})")
        if node.cache_ref is not None:
            self._resolve_cache[cache_key] = stored

        return _bind(stored, 'live')

    def _exec_AiCompareBlock(self, node, ctx):
        """ai.compare -- relational judgment over the weighed inputs.

        Binds a record { winner, margin, explanation } to the block's name. `margin` is how
        decisively the winner won, taken from the model's confidence: a 0.5 confidence is a
        coin-flip (margin 0), a 1.0 is unanimous (margin 1). Reporting the margin rather than
        only the winner is the point of a comparison -- "A, barely" and "A, decisively" are
        different answers and the program deserves to see which it got.
        """
        kwargs = self._ai_shared_inputs(node, ctx)
        kwargs['name'] = node.name or 'compare'
        try:
            decision = self.ai.decide(**kwargs)
        except (_GiveBack, _Raise):
            raise
        except Exception as _ai_err:
            # Mirrors _exec_AiDecideBlock: a hard provider failure (AiProviderError or
            # any other exception decide() lets through) is a real failure, not a
            # comparison the model happened to be unsure about -- loud, not swallowed.
            _of = next((b for b in node.body if isinstance(b, OnFailure)), None)
            if _of is not None:
                self._exec_block(_of.body, ctx)
            raise _Raise(error_name='ai_error',
                message=f"ai.compare '{node.name}' failed: {str(_ai_err)[:120]}",
                line=getattr(node, 'line', None),
                hint="Add an 'on.failure' handler that gives back a response, or check the AI provider/credentials.")
        winner = decision.result
        winner = winner.to_python() if isinstance(winner, MohioValue) else winner
        conf = float(getattr(decision, 'confidence', 0.0) or 0.0)
        margin = max(0.0, min(1.0, (conf - 0.5) * 2))   # 0.5 -> 0 (tie), 1.0 -> 1 (decisive)
        record = MohioValue({
            'winner':      winner,
            'margin':      round(margin, 4),
            'explanation': getattr(decision, 'explanation', None) or "",
        }, 'shape')
        if node.name:
            ctx.set(node.name, record)
        return record

    def _exec_AiRespondBlock(self, node, ctx):
        """ai.respond -- an interaction response (support reply, chat turn, narration).

        Binds the generated text to the block's name. Unlike ai.decide this is not a gated
        decision, so it carries no confidence threshold: there is no correct/incorrect answer
        to gate on, and inventing a gate here would be enforcement theatre.
        """
        kwargs = self._ai_shared_inputs(node, ctx)
        kwargs['name'] = node.name or 'respond'
        try:
            decision = self.ai.decide(**kwargs)
        except (_GiveBack, _Raise):
            raise
        except Exception as _ai_err:
            # Mirrors _exec_AiDecideBlock: a hard provider failure must surface loud,
            # not come back as an empty-looking response text.
            _of = next((b for b in node.body if isinstance(b, OnFailure)), None)
            if _of is not None:
                self._exec_block(_of.body, ctx)
            raise _Raise(error_name='ai_error',
                message=f"ai.respond '{node.name}' failed: {str(_ai_err)[:120]}",
                line=getattr(node, 'line', None),
                hint="Add an 'on.failure' handler that gives back a response, or check the AI provider/credentials.")
        text = decision.result
        text = text.to_python() if isinstance(text, MohioValue) else text
        value = MohioValue("" if text is None else str(text), 'text')
        if node.name:
            ctx.set(node.name, value)
        return value

    def _exec_AiRankBlock(self, node, ctx):
        # Weighted multi-option ranking. Evaluate each option's condition; among
        # the candidates whose condition holds (or that have none), the highest
        # weight wins. `default` is the no-match fallback. Confidence is the
        # winner's share of the matched weight; below the floor, `not confident`
        # runs and its `give back` becomes the winner.
        def _num(v, fallback):
            try:
                x = self._eval(v, ctx) if v is not None else None
                x = x.to_python() if hasattr(x, 'to_python') else x
                return float(x)
            except Exception:
                return fallback

        matched = []          # (weight, MohioValue)
        default_val = None
        for opt in node.options:
            w = _num(opt.weight, 1.0) if opt.weight is not None else 1.0
            if opt.is_default:
                default_val = self._eval(opt.value, ctx)
                continue
            if opt.condition is None or self._eval_condition(opt.condition, ctx):
                matched.append((w, self._eval(opt.value, ctx)))

        if matched:
            matched.sort(key=lambda x: x[0], reverse=True)
            best_w, winner = matched[0]
            total = sum(w for w, _ in matched) or 1.0
            confidence = best_w / total
        elif default_val is not None:
            winner, confidence = default_val, 1.0
        else:
            winner, confidence = MohioValue(None, 'text'), 0.0

        threshold = _num(node.confidence, None) if node.confidence is not None else None
        fell_back = False
        if threshold is not None and confidence < threshold and node.not_confident is not None:
            fell_back = True
            try:
                self._exec_block(node.not_confident.body, ctx)
            except _GiveBack as gb:
                winner = gb.value

        winner_v = winner if hasattr(winner, 'to_python') else MohioValue(winner, 'text')
        if node.name:
            ctx.set(node.name, winner_v)

        # Register so ai.explain can explain this ranking, then run a nested explain.
        if not hasattr(self, '_ai_decisions'):
            self._ai_decisions = {}
        self._ai_decisions[node.name] = AiDecision(
            result=(winner_v.to_python() if hasattr(winner_v, 'to_python') else winner_v),
            confidence=confidence, fell_back=fell_back, model='rank', inputs={})
        if node.audit is not None:
            self._write_ai_audit(node.audit.log_name, node.name,
                                 self._ai_decisions[node.name], ctx)
        if node.explain is not None:
            self._exec_AiExplainBlock(node.explain, ctx)

        return winner_v

    def _exec_AiExplainBlock(self, node, ctx):
        # ai.explain generates a human-readable explanation of a named ai.decide
        # result (sector profiles REQUIRE it for ECOA adverse-action and HIPAA
        # clinician explanations). Look up the decision registered by ai.decide,
        # ask the AI runtime to explain it (falling back to a plain summary if the
        # runtime has no explain()), and bind the text to the `as` name.
        name = getattr(node, 'decision_name', None)
        decisions = getattr(self, '_ai_decisions', {})
        decision = decisions.get(name)
        if decision is None:
            raise MohioRuntimeError(
                f"ai.explain: no decision named '{name}' to explain. Run "
                f"'ai.decide {name} ...' before ai.explain so there is a decision "
                f"to explain.")
        audience = getattr(node, 'audience', None) or 'developer'
        ai = getattr(self, 'ai', None)
        if ai is not None and hasattr(ai, 'explain'):
            explanation = ai.explain(decision, audience=audience)
        else:
            # Fallback: a plain-language summary from the decision itself.
            conf = getattr(decision, 'confidence', None)
            conf_s = f", confidence {conf:.0%}" if isinstance(conf, (int, float)) else ""
            explanation = (f"Decision: {getattr(decision, 'result', None)}"
                           f"{conf_s}, model {getattr(decision, 'model', 'unknown')}.")
        explanation = str(explanation)
        alias = getattr(node, 'alias', None)
        if alias:
            ctx.set(alias, MohioValue(explanation, 'text'))
        return MohioValue(explanation, 'text')

    def _exec_AiConnectBlock(self, node, ctx):
        # Register each named provider group as a fallback chain on the AI runtime.
        ai = getattr(self, 'ai', None)
        # Was a silent no-op (return None) when no capable runtime was present -- an ai.connect
        # that registers nothing while reporting success. This is a config/capability failure of a
        # BUILT feature (not a deferral), so it fails loud rather than vanishing.
        if ai is None:
            raise MohioRuntimeError(
                "ai.connect needs an AI runtime, but none is configured. Set a provider key "
                "(e.g. ANTHROPIC_API_KEY) so the fallback chain can be registered.")
        if not hasattr(ai, 'register_chain'):
            raise MohioRuntimeError(
                "ai.connect: the active AI runtime does not support provider chains "
                "(no register_chain). This runtime cannot register an ai.connect fallback chain.")
        _vendor_default = {
            'anthropic': 'claude-sonnet-4-6',
            'openai':    'gpt-4o',
            'gemini':    'gemini-1.5-pro',
            'google':    'gemini-1.5-pro',
        }
        providers = []
        for p in (getattr(node, 'providers', None) or []):
            prov = str(p.get('provider', '') or '').strip().strip('"').strip("'")
            model = str(p.get('model', '') or '').strip().strip('"').strip("'")
            ident = model or _vendor_default.get(prov.lower(), prov)
            if ident:
                providers.append(ident)
        for name in (getattr(node, 'names', None) or []):
            ai.register_chain(name, providers)
            if self.verbose:
                print(f"  [ai.connect] registered chain '{name}' -> {providers}")
        return None
    def _exec_AiCreateStmt(self, node, ctx):
        # Generate content (text/image/video/audio) and bind it to the `as` alias.
        ai = getattr(self, 'ai', None)
        ct = (getattr(node, 'create_type', '') or '').lower()
        alias = getattr(node, 'alias', '') or getattr(node, 'name', '')

        def itp(v):
            return self._interpolate(v, ctx) if v else ""
        goal     = itp(getattr(node, 'goal', ''))
        style    = itp(getattr(node, 'style', ''))
        negative = itp(getattr(node, 'negative', ''))
        persona  = itp(getattr(node, 'persona', ''))
        context  = itp(getattr(node, 'context', ''))
        size     = itp(getattr(node, 'size', ''))
        model    = getattr(node, 'model', '') or None
        temp     = (float(node.temperature)
                    if getattr(node, 'temperature', None) is not None else None)
        dur      = getattr(node, 'duration', None)

        result = None
        try:
            if ct in ('text', 'logic', 'data', ''):
                if not (ai and hasattr(ai, 'generate_text')):
                    raise MohioRuntimeError(
                        "ai.create text requires an AI runtime with generate_text().")
                # From-source block form: `ai.create NAME from SOURCE` with free-form
                # hint lines. Fold the source object and the hints into the prompt.
                src_name = getattr(node, 'source', '') or ''
                attrs = getattr(node, 'attrs', {}) or {}
                if src_name or attrs:
                    try:
                        src_val = ctx.get(src_name) if src_name else None
                    except Exception:
                        src_val = None
                    if src_val is None and src_name:
                        src_val = src_name
                    if isinstance(src_val, MohioValue):
                        src_val = src_val.to_python()
                    hints = "; ".join(f"{k}={itp(str(v))}" for k, v in attrs.items() if v)
                    if not goal:
                        bits = [f"Generate a '{getattr(node,'name','output')}'"]
                        if src_val is not None:
                            bits.append(f"from this source: {src_val}")
                        if hints:
                            bits.append(f"with these hints: {hints}")
                        goal = ". ".join(bits) + "."
                    if src_val is not None and not context:
                        context = str(src_val)
                result = ai.generate_text(goal=goal, persona=persona, context=context,
                                          style=style, model=model, temperature=temp)
            elif ct == 'image':
                if not (ai and hasattr(ai, 'generate_image')):
                    raise MohioRuntimeError(
                        "ai.create image requires an AI runtime with generate_image().")
                result = ai.generate_image(goal=goal, style=style, negative=negative,
                                           size=size or "1024x1024", model=model)
            elif ct == 'video':
                if not (ai and hasattr(ai, 'generate_video')):
                    raise MohioRuntimeError(
                        "ai.create video requires an AI runtime with generate_video().")
                result = ai.generate_video(goal=goal, style=style,
                                           duration=dur, size=size or None, model=model)
            elif ct == 'audio':
                if ai and hasattr(ai, 'generate_audio'):
                    result = ai.generate_audio(goal=goal, voice=getattr(node, 'voice', ''),
                                               pace=getattr(node, 'pace', None), model=model)
                else:
                    raise MohioRuntimeError(
                        "ai.create audio is declared but not yet executable "
                        "(no generate_audio runtime).")
            else:
                raise MohioRuntimeError(
                    f"ai.create: unknown modality '{ct}'. Use text, image, video, or audio.")
        except (_GiveBack, MohioRuntimeError):
            raise
        except Exception as e:
            # Generation failed -> run the not-confident (or on.failure) fallback if present.
            fb = next((b for b in (getattr(node, 'body', None) or [])
                       if type(b).__name__ in ('NotConfidentBlock', 'OnFailureHandler', 'OnFailure')), None)
            if fb:
                if getattr(self, 'verbose', False):
                    print(f"  [ai.create] {ct} generation failed "
                          f"({type(e).__name__}: {e}); running fallback")
                self._exec_block(fb.body, ctx)
                return None
            raise
        if alias:
            ctx.set(alias, MohioValue(result, ct or 'text'))
        if getattr(self, 'verbose', False):
            print(f"  [ai.create] {ct} -> {alias} = {str(result)[:60]!r}")
        return result
    def _exec_AiOverrideStmt(self, node, ctx):
        # ai.override is meant to replace an AI decision with a human-supplied
        # value, attribute it, record a reason, and signal the audit/learning
        # pipeline. None of that is wired yet. A SILENT no-op here is dangerous:
        # the AI's original (possibly wrong) decision silently stands while the
        # code reads as though a human overrode it -- exactly the wrong failure
        # mode in a fraud / compliance setting. Fail loud until it is built.
        raise MohioRuntimeError(
            "ai.override is declared but not yet executable in this build. It "
            "would SILENTLY KEEP the AI's original decision while appearing to "
            "override it -- unsafe for fraud / compliance review. Do not rely on "
            "it to override a decision yet. Real override (value, attribution, "
            "reason, audit + learning-loop signal) is tracked for a future "
            "release.")

    # ── Error handling ────────────────────────────────────────

    def _exec_TryBlock(self, node, ctx):
        import time as _time
        attempts = node.retry_times if (node.retry_times and node.retry_times > 0) else 1
        # Timeout is enforced as an attempt-boundary budget: the interpreter is
        # synchronous and cannot hard-interrupt a running attempt, so the budget
        # is checked before each attempt. `total` takes precedence; a per-attempt
        # value is used as the overall budget when no `total` is given.
        budget   = node.total_timeout if node.total_timeout else node.per_timeout
        deadline = (_time.monotonic() + budget) if budget else None
        backoff  = node.backoff or 0
        result = None
        error  = None
        for i in range(attempts):
            if deadline is not None and _time.monotonic() > deadline:
                error = _Raise(error_name='timeout',
                               message='try time budget exceeded')
                break
            error = None
            try:
                result = self._exec_block(node.body, ctx)
                break  # success
            except _GiveBack:
                # give back returns from inside the try; always still runs.
                if node.always:
                    self._exec_block(node.always.body, ctx)
                raise
            except _Halt:
                if node.always:
                    self._exec_block(node.always.body, ctx)
                raise
            except _Raise as r:
                error = r
            except Exception as e:
                error = _Raise(error_name=type(e).__name__, message=str(e))
            # this attempt failed; wait before the next retry (if any and budget allows)
            if i < attempts - 1 and backoff:
                if deadline is None or (_time.monotonic() + backoff) <= deadline:
                    _time.sleep(backoff)

        if error:
            handled = False
            # catch is the canonical try error handler; on.failure is the
            # data-op style. Either runs the error path with `error` in scope.
            for handler in (node.catch, node.on_failure):
                if handler is not None:
                    child = ctx.child()
                    child.set('error', MohioValue({
                        'message': str(error.message),
                        'name': error.error_name,
                    }))
                    result = self._exec_block(handler.body, child)
                    handled = True
            if not handled:
                if node.always:
                    self._exec_block(node.always.body, ctx)
                raise error
        else:
            if node.on_success is not None:
                result = self._exec_block(node.on_success.body, ctx)

        if node.always:
            self._exec_block(node.always.body, ctx)
        return result

    def _exec_JourneyDecl(self, node, ctx):
        # Journey = the app's root scope + routing container.
        # See Docs/journey-page-design-2026-06-17.md.
        if getattr(node, 'name', None):
            ctx._journey_name = node.name

        # 1. Establish scope (declarations + setup) UNLESS it was pre-wired by
        #    _exec_declarations (the session path, and the stateless request path).
        #    Guarded so connects/holds/tasks never run twice.
        if not getattr(self, '_scope_prewired', False):
            self._exec_journey_scope(node, ctx)

        # 2. Route the current request: pages (GET) first, then nested listeners.
        req = getattr(ctx, '_current_request', None)
        if not req:
            # No request -- the journey was pure scope setup (e.g. run(request=None)).
            if self._debug_active(ctx):
                self._debug_write(ctx, '\nCompleted: success')
            return None

        from mohio_ast import PageDecl, ListenBlock
        pages         = [b for b in node.body if isinstance(b, PageDecl)]
        listen_blocks = [b for b in node.body if isinstance(b, ListenBlock)]

        served = self._serve_pages(pages, ctx, req)
        if served is not None:
            return served

        # Nested listen blocks (POST/create + GET request-for endpoints).
        # continue-on-404: a no-route from one block must not shadow a sibling.
        final_404 = None
        for lb in listen_blocks:
            r = self._exec(lb, ctx)
            if r is None:
                continue
            if isinstance(r, dict) and r.get('status') == 404:
                final_404 = r
                continue
            return r
        if final_404 is not None:
            return final_404

        # A path was given, this journey has routes, but none matched -> clean 404
        # (never a silently-wrong page, never a bare None that the caller mishandles).
        if req.get('_path') is not None and (pages or listen_blocks):
            return {'status': 404, '_no_route': True,
                    'body': f"No route matches {req.get('_method', 'GET')} {req.get('_path')}"}
        return None

    # ── journey / page support ────────────────────────────────
    def _exec_journey_scope(self, node, ctx):
        """Run a journey's scope-establishing body items once: declarations and
        setup definitions. Excludes routed units (pages, listen blocks), inert
        metadata, and ai.decide definitions -- those are registered by
        _register_ai_blocks and must NOT run as setup (running one fires the AI)."""
        from mohio_ast import PageDecl, ListenBlock, JourneyMeta, AiDecideBlock
        skip = (PageDecl, ListenBlock, JourneyMeta, AiDecideBlock)
        for b in node.body:
            if isinstance(b, skip):
                continue
            self._exec(b, ctx)

    def _norm_path(self, p):
        """Normalize a request/route path: drop query string and a trailing slash."""
        if not p:
            return p
        p = str(p).split('?', 1)[0]
        return p[:-1] if len(p) > 1 and p.endswith('/') else p

    def _serve_pages(self, pages, ctx, req):
        """Route a GET request to a matching page by path. Mirrors _exec_ListenBlock:
        exact path match, single-page fallback when no path is pinned, else None
        (no match -- the caller decides whether to 404). Returns a response dict or
        None. Pages are GET-served; other methods never match a page."""
        if not pages:
            return None
        method = (req.get('_method') or 'GET').upper()
        if method not in ('GET', 'REQUEST', 'HEAD'):
            return None
        req_path = req.get('_path')
        if req_path is not None:
            rp = self._norm_path(req_path)
            hits = [pg for pg in pages
                    if pg.path is not None and self._norm_path(pg.path) == rp]
            if hits:
                return self._serve_page(hits[0], ctx, req)
            return None
        # No path pinned: a single page is unambiguous; serve it.
        if len(pages) == 1:
            return self._serve_page(pages[0], ctx, req)
        return None

    def _serve_page(self, page, ctx, req):
        """Execute one page body in a child scope and format its response. A page
        whose body ends in a `render` block serves that HTML; a `give back` returns
        data/status. Inherits the journey scope via the context chain."""
        child = ctx.child()
        child._current_request = req
        # Expose request fields (and a `request` shape) the same way the request
        # listener does, so pages can read query params / posted fields.
        clean = {k: v for k, v in req.items() if not k.startswith('_')}
        child.set('request', MohioValue(clean, 'shape'))
        for k, v in clean.items():
            child.set(k, MohioValue(v) if not isinstance(v, dict)
                      else MohioValue(v, 'shape'))
        try:
            result = self._exec_block(page.body, child)
        except _GiveBack as gb:
            return self._format_response(gb)
        return self._format_page_result(result, child)

    def _program_has_listeners(self, program):
        from mohio_ast import ListenBlock, JourneyDecl, PageDecl
        return any(isinstance(s, (ListenBlock, JourneyDecl, PageDecl))
                   for s in getattr(program, 'statements', []))




    # ── MATCH BLOCKS (MioQL) ───────────────────────────────────
    # match     -- all conditions AND (extend WHERE clause)
    # match any -- any condition OR
    # no.match  -- none of these NOT

    def _resolve_match_pairs(self, pairs, ctx):
        """Resolve match pairs to (field, value) tuples."""
        resolved = []
        for pair in (pairs or []):
            field = pair.field if hasattr(pair, 'field') else str(pair)
            val_node = pair.value if hasattr(pair, 'value') else None
            if val_node is not None:
                try:
                    resolved_val = self._eval(val_node, ctx)
                    raw = (resolved_val.to_python()
                           if isinstance(resolved_val, MohioValue)
                           else resolved_val)
                    resolved.append((field, raw))
                except Exception:
                    resolved.append((field, str(val_node)))
        return resolved

    def _exec_MatchBlock(self, node, ctx):
        """Store match pairs for the enclosing retrieve/find block."""
        if not hasattr(ctx, '_match_conditions'):
            ctx._match_conditions = {'and': [], 'or': [], 'not': []}
        pairs = self._resolve_match_pairs(node.pairs, ctx)
        ctx._match_conditions['and'].extend(pairs)
        return None

    def _exec_MatchAnyBlock(self, node, ctx):
        """Store OR match pairs for the enclosing retrieve/find block."""
        if not hasattr(ctx, '_match_conditions'):
            ctx._match_conditions = {'and': [], 'or': [], 'not': []}
        pairs = self._resolve_match_pairs(node.pairs, ctx)
        ctx._match_conditions['or'].extend(pairs)
        return None

    def _exec_NoMatchBlock(self, node, ctx):
        """Store NOT match pairs for the enclosing retrieve/find block."""
        if not hasattr(ctx, '_match_conditions'):
            ctx._match_conditions = {'and': [], 'or': [], 'not': []}
        pairs = self._resolve_match_pairs(node.pairs, ctx)
        ctx._match_conditions['not'].extend(pairs)
        return None

    def _exec_MatchPair(self, node, ctx):
        """Individual match pair -- handled by parent block."""
        return None

    # ── VIEW TEMPLATE ENGINE ──────────────────────────────────
    # Renders named view templates to HTML.
    # Templates live in views/ directory relative to the .mho file.
    # Template format: plain HTML with {{variable}} placeholders.
    # Falls back to auto-generated HTML if no template file found.
    # Patent note: view rendering as language primitive -- P-SECTOR

    def _find_view_template(self, name, ctx):
        """
        Search for a view template file.
        Looks for: views/name.html, views/name.mho.html, name.html
        Returns file content as string or None if not found.
        """
        import os
        search_dirs = []
        # Try to find views/ relative to the source file
        source_file = getattr(ctx, '_source_file', None)
        if source_file:
            source_dir = os.path.dirname(os.path.abspath(str(source_file)))
            search_dirs.extend([
                os.path.join(source_dir, 'views'),
                os.path.join(source_dir, '..', 'views'),
                source_dir,
            ])
        # Also check cwd
        search_dirs.extend([
            os.path.join(os.getcwd(), 'views'),
            os.getcwd(),
        ])

        candidates = [
            f"{name}.html",
            f"{name}.mohio.html",
            f"{name.replace('_', '-')}.html",
        ]

        for directory in search_dirs:
            for candidate in candidates:
                path = os.path.join(directory, candidate)
                if os.path.exists(path):
                    return open(path, encoding='utf-8').read()
        return None

    def _render_template(self, template_str, variables):
        """
        Render a template string with {{variable}} substitution.
        Supports:
            {{name}}           -- simple variable
            {{name.field}}     -- dotted access
            {{each items}}...{{end}} -- simple loop (future)
        """
        import re

        def resolve(key, variables):
            parts = key.strip().split('.')
            val = variables.get(parts[0], '')
            for part in parts[1:]:
                if isinstance(val, dict):
                    val = val.get(part, '')
                elif hasattr(val, part):
                    val = getattr(val, part, '')
                else:
                    val = ''
            if val is None:
                return ''
            return str(val)

        result = re.sub(
            r'\{\{([^}]+)\}\}',
            lambda m: resolve(m.group(1), variables),
            template_str
        )
        return result

    def _auto_html(self, template_name, variables):
        """
        Auto-generate a simple HTML page when no template file exists.
        Enough for development and demos. Replace with a real template
        for production.
        """
        title = variables.get('title', template_name.replace('_', ' ').title())
        rows = []
        for key, val in variables.items():
            if key == 'title':
                continue
            if isinstance(val, (list, tuple)):
                items = ''.join(f'<li>{v}</li>' for v in val)
                rows.append(f'<dt>{key}</dt><dd><ul>{items}</ul></dd>')
            elif isinstance(val, dict):
                items = ''.join(
                    f'<dt>{k}</dt><dd>{v}</dd>'
                    for k, v in val.items())
                rows.append(f'<dt>{key}</dt><dd><dl>{items}</dl></dd>')
            else:
                rows.append(f'<dt>{key}</dt><dd>{val}</dd>')

        body = '\n'.join(rows)
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 800px;
            margin: 2rem auto; padding: 0 1rem; color: #222; }}
    h1   {{ color: #0D7377; }}
    dl   {{ display: grid; grid-template-columns: max-content 1fr;
            gap: 0.5rem 1rem; }}
    dt   {{ font-weight: bold; color: #555; }}
    dd   {{ margin: 0; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <dl>
    {body}
  </dl>
  <footer style="margin-top:2rem;color:#999;font-size:0.8rem;">
    Built with <a href="https://mohio.io" style="color:#0D7377;">Mohio</a>
  </footer>
</body>
</html>"""

    def _exec_ViewRender(self, node, ctx):
        """
        Execute: give back 200 view "rates_page" cabin cabin price 250

        1. Resolve all param values from context
        2. Look for a template file in views/
        3. Render template with variable substitution
        4. Set content-type to text/html
        5. Return rendered HTML as give back value
        """
        # Resolve parameters
        variables = {}
        for param in (node.params or []):
            if isinstance(param, (list, tuple)) and len(param) == 2:
                key, val_node = param
                if val_node is not None:
                    try:
                        resolved = self._eval(val_node, ctx)
                        raw = (resolved.to_python()
                               if isinstance(resolved, MohioValue)
                               else resolved)
                        variables[str(key)] = raw
                    except Exception:
                        variables[str(key)] = str(val_node)

        # Add all context variables as fallback
        if hasattr(ctx, '_vars'):
            for k, v in ctx._vars.items():
                if k not in variables and not k.startswith('_'):
                    raw = v.to_python() if isinstance(v, MohioValue) else v
                    variables[k] = raw

        # Set content type on context
        ctx._response_content_type = 'text/html'

        # Look for template file
        template_str = self._find_view_template(node.template_name, ctx)

        if template_str:
            html = self._render_template(template_str, variables)
        else:
            html = self._auto_html(node.template_name, variables)

        self._debug_trace(ctx,
            f"view '{node.template_name}' rendered "
            f"({len(html)} chars, {len(variables)} vars)")

        return MohioValue(html, 'html')


    def _exec_ViewCallStmt(self, node, ctx):
        """
        Execute: view "rates_page" price price title "Stone Ridge"
        Renders the template and stores result on ctx for the response.
        """
        variables = {}
        for param in (node.params or []):
            if isinstance(param, (list, tuple)) and len(param) == 2:
                key, val_node = param
                if val_node is not None:
                    try:
                        resolved = self._eval(val_node, ctx)
                        raw = (resolved.to_python()
                               if isinstance(resolved, MohioValue)
                               else resolved)
                        variables[str(key)] = raw
                    except Exception:
                        variables[str(key)] = str(val_node)

        ctx._response_content_type = 'text/html'
        template_str = self._find_view_template(node.template_name, ctx)
        if template_str:
            html = self._render_template(template_str, variables)
        else:
            html = self._auto_html(node.template_name, variables)

        ctx._view_output = html
        self._debug_trace(ctx,
            f"view '{node.template_name}' rendered ({len(html)} chars)")
        return MohioValue(html, 'html')

    def _exec_RespondAsStmt(self, node, ctx):
        """respond as "text/html" -- sets content-type for this response."""
        ct = node.content_type if hasattr(node, 'content_type') else 'text/html'
        self._default_content_type = ct   # document-level default; per-response `as X` overrides
        ctx._response_content_type = ct
        self._debug_trace(ctx, f"respond as: {ct}")
        return None

    def _exec_ViewDecl(self, node, ctx):
        """
        view rates_page
            cabin as shape required
            price as number optional
            display
                // HTML template inline
            display: done
        view: done

        Registers a named view template in the context.
        Templates can also live as files in views/
        """
        # Register the view in context for later use
        if not hasattr(ctx, '_views'):
            ctx._views = {}
        ctx._views[node.name] = node
        return None

    # ── APPLANG SYSTEM ────────────────────────────────────────────
    # Self-building multilingual interaction corpus.
    # Patent claim: P-APPLANG Claims 1, 8, 9, 10, 11, 14

    APPLANG_TABLE = "applang_map"
    APPLANG_COLUMNS = [
        "id", "input", "canonical", "context_id", "context_category",
        "lang_header", "app_version_hash", "hit_count", "created_at", "updated_at"
    ]

    def _ensure_applang_table(self):
        """Create applang_map table if it doesn't exist."""
        if not self._db:
            return False
        try:
            self._db.conn.execute("""
                CREATE TABLE IF NOT EXISTS applang_map (
                    id              TEXT PRIMARY KEY,
                    input           TEXT NOT NULL,
                    canonical       TEXT NOT NULL,
                    context_id      TEXT DEFAULT '',
                    context_category TEXT DEFAULT '',
                    lang_header     TEXT DEFAULT 'en',
                    app_version_hash TEXT DEFAULT '',
                    hit_count       INTEGER DEFAULT 1,
                    created_at      TEXT NOT NULL,
                    updated_at      TEXT NOT NULL
                )
            """)
            self._db.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_applang_exact "
                "ON applang_map (input, context_id)"
            )
            self._db.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_applang_global "
                "ON applang_map (input)"
            )
            self._db.conn.commit()
            return True
        except Exception as e:
            return False

    def _applang_lookup(self, user_input, context_id='', context_category=''):
        """
        3-level hierarchical cache lookup.
        Level 1: exact match on (input + context_id)
        Level 2: partial match on (input + context_category)
        Level 3: global match on (input) alone
        Returns (canonical, level) or (None, 0) on full miss.
        """
        if not self._db:
            return None, 0

        normalized = str(user_input).strip().lower()

        # Level 1: exact match -- input + context_id
        try:
            cur = self._db.conn.execute(
                "SELECT canonical, id FROM applang_map "
                "WHERE LOWER(input) = ? AND context_id = ? "
                "ORDER BY hit_count DESC LIMIT 1",
                (normalized, context_id)
            )
            row = cur.fetchone()
            if row:
                self._applang_increment(row['id'])
                return row['canonical'], 1
        except Exception:
            pass

        # Level 2: partial match -- input + context_category
        if context_category:
            try:
                cur = self._db.conn.execute(
                    "SELECT canonical, id FROM applang_map "
                    "WHERE LOWER(input) = ? AND context_category = ? "
                    "ORDER BY hit_count DESC LIMIT 1",
                    (normalized, context_category)
                )
                row = cur.fetchone()
                if row:
                    self._applang_increment(row['id'])
                    return row['canonical'], 2
            except Exception:
                pass

        # Level 3: global match -- input only
        try:
            cur = self._db.conn.execute(
                "SELECT canonical, id FROM applang_map "
                "WHERE LOWER(input) = ? "
                "ORDER BY hit_count DESC LIMIT 1",
                (normalized,)
            )
            row = cur.fetchone()
            if row:
                self._applang_increment(row['id'])
                return row['canonical'], 3
        except Exception:
            pass

        return None, 0

    def _applang_increment(self, entry_id):
        """Increment hit_count for a cached entry."""
        try:
            import datetime
            self._db.conn.execute(
                "UPDATE applang_map SET hit_count = hit_count + 1, "
                "updated_at = ? WHERE id = ?",
                (datetime.datetime.utcnow().isoformat(), entry_id)
            )
            self._db.conn.commit()
        except Exception:
            pass

    def _applang_persist(self, user_input, canonical, context_id='',
                         context_category='', lang_header='en',
                         app_version_hash=''):
        """Persist a resolved mapping to applang_map."""
        if not self._db:
            return
        try:
            import datetime, uuid
            now = datetime.datetime.utcnow().isoformat()
            self._db.conn.execute(
                "INSERT OR REPLACE INTO applang_map "
                "(id, input, canonical, context_id, context_category, "
                " lang_header, app_version_hash, hit_count, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (str(uuid.uuid4()), str(user_input).strip().lower(),
                 str(canonical), context_id, context_category,
                 lang_header, app_version_hash, now, now)
            )
            self._db.conn.commit()
        except Exception:
            pass

    def _exec_ApplangBlock(self, node, ctx):
        """
        Execute an applang block.

        1. Ensure applang_map table exists
        2. Get user input from context (request body or named variable)
        3. Run 3-level lookup cascade
        4. On hit: return canonical, zero AI cost
        5. On miss: invoke ai.decide block, persist result, return canonical

        Debug verbose traces every lookup level.
        """
        import datetime

        # Ensure table exists
        self._ensure_applang_table()

        # Get context info
        context_id = node.context or ''
        context_category = context_id.split('.')[0] if '.' in context_id else context_id
        map_table = node.map_table or 'applang_map'

        # Get user input -- look for 'input' or 'user_input' in context
        user_input = None
        for key in ('input', 'user_input', 'command', 'query', 'message'):
            val = ctx.get(key)
            if val is not None:
                raw = val.to_python() if isinstance(val, MohioValue) else val
                if raw:
                    user_input = str(raw)
                    break

        if not user_input:
            self._debug_trace(ctx, "applang: no input found in context")
            return None

        # Get language header from context
        lang = ctx.get('lang_header')
        lang_str = lang.to_python() if isinstance(lang, MohioValue) else (lang or 'en')

        # Get app version hash
        app_hash = getattr(ctx, '_app_version_hash', '')

        self._debug_trace(ctx,
            f"applang lookup: input={user_input!r} context={context_id!r}")

        # 3-level cache lookup
        canonical, level = self._applang_lookup(
            user_input, context_id, context_category)

        if canonical:
            level_names = {1: 'Level 1 exact', 2: 'Level 2 partial', 3: 'Level 3 global'}
            self._debug_trace(ctx,
                f"applang cache hit ({level_names.get(level, 'unknown')}): "
                f"{user_input!r} -> {canonical!r} (zero AI cost)")
            ctx.set('canonical', MohioValue(canonical, 'text'))
            return MohioValue(canonical, 'text')

        # Cache miss -- invoke ai.decide block
        self._debug_trace(ctx,
            f"applang cache miss: invoking ai.decide for {user_input!r}")

        canonical = None
        if node.ai_block:
            try:
                result = self._exec_node(node.ai_block, ctx)
                if result:
                    raw = result.to_python() if isinstance(result, MohioValue) else result
                    canonical = str(raw) if raw else None
            except _GiveBack as gb:
                val = gb.value
                raw = val.to_python() if isinstance(val, MohioValue) else val
                canonical = str(raw) if raw else None
            except Exception as e:
                self._debug_trace(ctx, f"applang ai.decide error: {e}")

        if canonical:
            # Persist the resolved mapping
            self._applang_persist(
                user_input, canonical, context_id, context_category,
                lang_str, app_hash)
            self._debug_trace(ctx,
                f"applang resolved and persisted: "
                f"{user_input!r} -> {canonical!r}")
            ctx.set('canonical', MohioValue(canonical, 'text'))
            return MohioValue(canonical, 'text')

        # No resolution -- fallback
        self._debug_trace(ctx, f"applang: no resolution for {user_input!r}")
        return None

    # ── RANDOM PRIMITIVE ──────────────────────────────────────────
    def _exec_RandomValue(self, node, ctx):
        """
        Execute a random.* expression.
        Supports: uuid, color, token, hex, number, count, select
        """
        import random as _random
        import uuid as _uuid

        kind = node.kind if hasattr(node, 'kind') else 'uuid'

        # unique.id -- a GENERATOR. Fresh and distinct on every read, by contract. Never cached,
        # never memoised. Two reads must give two values, including twice on one line.
        if kind == 'unique':
            return MohioValue(_uuid.uuid4().hex, 'text')

        if kind == 'uuid':
            return str(_uuid.uuid4())

        elif kind == 'color':
            r = _random.randint(0, 255)
            g = _random.randint(0, 255)
            b = _random.randint(0, 255)
            return f'#{r:02x}{g:02x}{b:02x}'

        elif kind == 'token':
            import secrets, string
            length = node.length if hasattr(node, 'length') and node.length else 32
            alphabet = string.ascii_letters + string.digits
            return ''.join(secrets.choice(alphabet) for _ in range(length))

        elif kind == 'hex':
            import secrets
            length = node.length if hasattr(node, 'length') and node.length else 64
            return secrets.token_hex(length // 2)

        elif kind == 'number':
            min_val = node.min_val if hasattr(node, 'min_val') and node.min_val is not None else 0
            max_val = node.max_val if hasattr(node, 'max_val') and node.max_val is not None else 100
            try:
                lo = float(str(min_val))
                hi = float(str(max_val))
                if lo == int(lo) and hi == int(hi):
                    return _random.randint(int(lo), int(hi))
                return _random.uniform(lo, hi)
            except (ValueError, TypeError):
                return _random.randint(0, 100)

        elif kind == 'count':
            # random.N -- returns a random integer 1..N
            count = node.count if hasattr(node, 'count') and node.count else 10
            return _random.randint(1, int(count))

        elif kind == 'select':
            # `random from <source>` — pick one element from a collection.
            # Source may be: a db table (DbRef) -> rows; a held list; or a
            # find/retrieve result variable.
            source = getattr(node, 'source', None)
            if source is None:
                return None
            seq = self._random_collection(source, ctx)
            if seq is None:
                raise _Raise(
                    error_name='random_source_not_a_list',
                    message="random from <X>: X must be a collection.",
                    line=getattr(node, 'line', 0),
                    hint="X must be a collection: build one with `hold name / items / hold: done`, "
                         "use a find/retrieve result, or a db table.",
                )
            return _random.choice(seq) if seq else None

        else:
            # Bare random -- return random float 0..1
            return _random.random()

    def _random_collection(self, source, ctx):
        """Resolve a random/pull <source> to a python list of items.

        Accepts three source kinds:
          1. db table  (DbRef / db.<name>)      -> all rows
          2. held list (hold name / items)      -> the list
          3. find/retrieve result variable      -> its list
        Returns a python list, or None if the source is not a collection.
        """
        from mohio_ast import DbRef, DottedName
        # 1. DB table source — fetch rows
        is_db = isinstance(source, DbRef) or (
            isinstance(source, DottedName)
            and getattr(source, 'parts', None)
            and str(source.parts[0]) == 'db'
        )
        if is_db:
            db = ctx.get_connection('db')
            if not db:
                return None
            table = self._resolve_source(source, ctx)
            rows = db.find_many(table, limit=None)
            return list(rows) if rows else []
        # 2/3. Anything that evaluates to a list (held list, find-result var)
        val = self._eval(source, ctx)
        seq = val.to_python() if hasattr(val, 'to_python') else val
        if isinstance(seq, (list, tuple)):
            return list(seq)
        return None

    # ── IGNORE STMT ───────────────────────────────────────────────
    def _exec_IgnoreStmt(self, node, ctx):
        """
        ignore ../journey.mho except db
        Runtime no-op -- this is a compiler-level directive.
        The interpreter acknowledges it silently.
        """
        return None

    # ── RUN ASYNC (Phase 2 stub) ──────────────────────────────────
    def _exec_RunAsyncBlock(self, node, ctx):
        """
        run async task(...)
        Phase 2 -- runs synchronously for now with a notice.
        """
        import warnings
        print("  ! run async: executing synchronously (async execution is Phase 2)")
        if hasattr(node, 'body') and node.body:
            return self._exec_block(node.body, ctx)
        return None

    # ── WAIT FOR (Phase 2 stub) ───────────────────────────────────
    def _exec_WaitForStmt(self, node, ctx):
        """
        wait for task(...)
        Phase 2 -- no-op for now.
        """
        print("  ! wait for: async coordination is Phase 2")
        return None

    # ── DEBUG SYSTEM ───────────────────────────────────────────────
    # Journey-scoped execution tracing.
    # Compiles to nothing when debug off -- zero runtime cost.
    # Writes plain-English logs to mohiolog/ at repo root.

    def _get_debug_mode(self, ctx):
        """Get active debug mode from context. Default off."""
        return getattr(ctx, '_debug_mode', 'off')

    def _debug_active(self, ctx):
        return self._get_debug_mode(ctx) not in ('off', None)

    def _debug_verbose(self, ctx):
        """True only in verbose mode -- for automatic interpreter tracing."""
        return self._get_debug_mode(ctx) == 'verbose'

    def _debug_trace(self, ctx, message):
        """Write an interpreter trace line -- verbose mode only."""
        if self._debug_verbose(ctx):
            self._debug_write(ctx, f'  [trace] {message}')

    def _ensure_mohiolog(self, journey_name, ctx):
        """Create mohiolog directory structure on first debug write."""
        import os, datetime
        root = os.path.join(os.getcwd(), 'mohiolog')
        os.makedirs(root, exist_ok=True)
        # Auto-create .gitignore
        gi = os.path.join(root, '.gitignore')
        if not os.path.exists(gi):
            open(gi, 'w').write('*\n')
        # Auto-create README
        rm = os.path.join(root, 'README.md')
        if not os.path.exists(rm):
            open(rm, 'w').write(
                '# mohiolog\n\n'
                'Journey execution traces generated by Mohio debug system.\n'
                'These files are excluded from git automatically.\n'
                'Each run creates a timestamped folder under the journey name.\n'
            )
        # Create journey folder
        safe_name = journey_name.replace(' ', '_').lower() if journey_name else 'unknown'
        ts = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        run_dir = os.path.join(root, safe_name, ts)
        os.makedirs(run_dir, exist_ok=True)
        # Update latest symlink
        latest = os.path.join(root, safe_name, 'latest')
        try:
            if os.path.islink(latest):
                os.remove(latest)
            os.symlink(run_dir, latest)
        except Exception:
            pass  # symlinks may not work on all platforms
        return run_dir

    def _debug_write(self, ctx, message, log_file='journey.log'):
        """Write a line to the active debug log."""
        import os
        log_dir = getattr(ctx, '_debug_log_dir', None)
        if not log_dir:
            return
        path = os.path.join(log_dir, log_file)
        with open(path, 'a', encoding='utf-8') as f:
            f.write(message + '\n')

    def _format_journey_name(self, name):
        """Convert snake_case or underscore names to kitchen English."""
        if not name:
            return 'unknown journey'
        return name.replace('_', ' ').replace('-', ' ')

    def _resolve_debug_target(self, target, ctx):
        """Resolve a dotted variable path to its value. Redact sensitive fields."""
        try:
            parts = target.split('.')
            val = ctx.get(parts[0])
            for part in parts[1:]:
                if hasattr(val, part):
                    val = getattr(val, part)
                elif isinstance(val, dict):
                    val = val.get(part, '[unknown]')
                else:
                    val = '[unknown]'
            # Sector redaction -- check if field is classified sensitive
            sector = getattr(ctx, '_sector', None)
            if sector:
                sensitive = getattr(ctx, '_sensitive_fields', set())
                field_name = parts[-1]
                if field_name in sensitive:
                    return f'[omitted -- {sector} sector field]'
            return repr(val)
        except Exception:
            return '[unavailable]'

    def _exec_DebugDecl(self, node, ctx):
        """Set debug mode on context. Initialize log directory if turning on."""
        mode = node.mode if hasattr(node, 'mode') else 'on'
        ctx._debug_mode = mode
        if mode not in ('off',):
            journey_name = getattr(ctx, '_journey_name', 'unknown')
            log_dir = self._ensure_mohiolog(journey_name, ctx)
            ctx._debug_log_dir = log_dir
            friendly = self._format_journey_name(journey_name)
            import datetime
            self._debug_write(ctx,
                f'Journey: {friendly}\n'
                f'Started: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n'
                f'Debug mode: {mode}\n'
            )
        return None

    def _exec_DebugLogStmt(self, node, ctx):
        """Write a single variable value to the debug log."""
        if not self._debug_active(ctx):
            return None
        target = node.target if hasattr(node, 'target') else ''
        value = self._resolve_debug_target(target, ctx)
        friendly_target = target.replace('_', ' ')
        self._debug_write(ctx, f'  {friendly_target} = {value}')
        return None

    def _exec_DebugCheckpoint(self, node, ctx):
        """Write a named checkpoint with variable snapshots."""
        if not self._debug_active(ctx):
            return None
        label = node.label if hasattr(node, 'label') else ''
        self._debug_write(ctx, f'\n  Checkpoint: {label}')
        for log_stmt in (node.logs or []):
            self._exec_DebugLogStmt(log_stmt, ctx)
        self._debug_write(ctx, '')
        return None



    def _exec_PageDecl(self, node, ctx):
        # A top-level bare `page N at /path` (no enclosing journey) is a GET route.
        # Serve it when the request path matches (or it is the only page); otherwise
        # return None so a sibling listener can match. Central 404 lives in the
        # program router (_route_program), so a non-matching page never shadows a
        # sibling and never silently serves the wrong page.
        req = getattr(ctx, '_current_request', None)
        if not req:
            return None
        return self._serve_pages([node], ctx, req)

    def _exec_SagaBlock(self, node, ctx):  # old name compat
        return self._exec_SagaDecl(node, ctx)

    # ── Action Statements ─────────────────────────────────────

    # File types that are never handed out, whatever the path says. Source and config
    # leak the app itself; a database leaks everything in it. The static server refuses
    # the same set, and `give` must not become the way around it.
    _DENY_GIVE_EXT = {
        '.mho', '.py', '.pyc', '.lark', '.env', '.ini', '.cfg', '.conf', '.toml',
        '.yaml', '.yml', '.db', '.sqlite', '.sqlite3', '.sql', '.pem', '.key',
        '.crt', '.p12', '.pfx', '.log', '.bak', '.swp', '.orig',
        '.cache', '.pkl', '.pickle',
    }

    def _resolve_governed_path(self, raw, roots, label):
        """Resolve `raw` inside one of `roots`, or fail loud.

        This is the containment rule the file area and the static server already use,
        in one place so `give` cannot become a softer version of it. A path is refused
        when it is absolute, when it climbs out with `..`, or when it resolves outside
        every root after symlinks are followed -- that last check is the one that
        matters, because a link inside the app can point anywhere.
        """
        import os as _os
        from pathlib import Path as _P
        text = str(raw).replace('\\', '/')
        parts = text.split('/')
        if text.startswith('/') or '..' in parts:
            raise MohioRuntimeError(
                f"{label}: the path '{raw}' is not allowed (it reaches outside the app).")
        if any(p.startswith('.') or p.startswith('_') for p in parts if p):
            raise MohioRuntimeError(
                f"{label}: '{raw}' is a private file. A name starting with a dot or an "
                f"underscore is never served.")
        # One list, not two. `give` and the static server both hand files to the
        # public, so a type denied by one and allowed by the other is a hole with a
        # different door on it -- which is exactly how `.cache` came to be served
        # while `.mho` was refused. The static list is the canonical one; this adds
        # nothing and subtracts nothing.
        _deny = set(self._DENY_GIVE_EXT)
        try:
            from mohio_server import _DENY_STATIC_EXT as _dse
            _deny |= set(_dse)
        except Exception:
            pass   # server not importable here; the local list still applies
        if _os.path.splitext(text.lower())[1] in _deny:
            raise MohioRuntimeError(
                f"{label}: '{raw}' is source, configuration or data, which is never "
                f"handed out. To proceed: give a document, image or export instead.")
        for root in roots:
            try:
                base = _P(root).resolve()
            except Exception:
                continue
            target = (base / text).resolve()
            try:
                inside = target.is_relative_to(base)
            except AttributeError:
                inside = _os.path.commonpath([str(target), str(base)]) == str(base)
            if inside and target.is_file():
                return target
        raise MohioRuntimeError(
            f"{label}: there is no file at '{raw}'. Paths are read from the app folder "
            f"and the file area, so a file outside those is not reachable.")

    def _exec_GiveStmt(self, node, ctx):
        """`give <value> as download ["<filename>"]` -- hand a file to the requester.

        A path written in place is read from a governed root and names itself from its
        tail. Anything else is the content itself and carries the filename the program
        gave it. Which of the two applies is settled at check time, so by here the
        combination is already known to be valid.
        """
        import os as _os
        from mohio_ast import Literal
        fmt = str(getattr(node, 'modifier', '') or '').lower()
        if fmt != 'download':
            raise MohioRuntimeError(
                "`give` hands a value over as a file, so it needs `as download`.")

        filename = getattr(node, 'filename', None)
        if filename is not None:
            # An ordinary Mohio string, so `{{ }}` renames in transit.
            filename = str(self._interpolate(filename, ctx))

        if isinstance(getattr(node, 'value', None), Literal):
            raw = self._eval(node.value, ctx)
            raw = raw.to_python() if isinstance(raw, MohioValue) else raw
            roots = [_os.getcwd(),
                     _os.environ.get("MIOFILE_ROOT",
                                     _os.path.join(_os.getcwd(), "mio_files"))]
            target = self._resolve_governed_path(raw, roots, "give")
            content = target.read_bytes()
            if filename is None:
                filename = _os.path.basename(str(raw).replace('\\', '/'))
        else:
            value = self._eval(node.value, ctx)
            content = value.to_python() if isinstance(value, MohioValue) else value

        raise _GiveBack(status=200, value=content, fmt=None, download=filename)

    def _exec_GiveBackStmt(self, node, ctx):
        self._check_purpose(getattr(node, 'value', None), ctx)
        # Check trailing qualifier
        if node.qualifier:
            if not self._eval_condition(node.qualifier.condition, ctx):
                return None  # condition false — don't give back

        # The `as FORMAT` cast (as json | as xml | as text | as html) rides on
        # node.modifier as a NAME token. Thread it through so the response builder
        # can set the right content-type instead of defaulting everything to JSON.
        fmt = str(node.modifier).lower() if node.modifier is not None else None

        # XSS-safe by default (2026-07-31): give back HTML-escapes interpolated {{ }} VALUES (authored
        # markup untouched), so untrusted data reflected into an HTML response cannot inject markup.
        # `trusted` opts out for intentional raw HTML / pre-built markup -- declared, never inferred
        # (no content sniffing). `as json` etc. carry structured values, not {{ }} templates.
        _escape = not getattr(node, 'trusted', False)

        # Handle old transformer's give_back_val tree
        # The old transformer stores value in a give_back_val Tree node.
        # node.status already has the HTTP status (e.g. 200).
        # node.value is a give_back_val Tree containing the actual value to return.
        try:
            from lark import Tree as _LarkTree
            if isinstance(node.value, _LarkTree) and node.value.data == 'give_back_val':
                children = [c for c in node.value.children if c is not None]
                if children:
                    # Evaluate the first (and usually only) child as the value
                    actual_value = self._eval(children[0], ctx)
                    self._check_purpose_value(actual_value, ctx)
                    actual_value = self._maybe_interpolate(actual_value, ctx, getattr(node, 'line', None), escape=_escape)
                    actual_value = self._giveback_masked(actual_value)
                    status = node.status if node.status is not None else 200
                    raise _GiveBack(status=status, value=actual_value, fmt=fmt)
        except ImportError:
            pass

        value  = self._eval(node.value, ctx) if node.value else None
        self._check_purpose_value(value, ctx)
        value  = self._maybe_interpolate(value, ctx, getattr(node, 'line', None), escape=_escape)
        value  = self._giveback_masked(value)
        status = None
        if node.status is not None:
            s = node.status
            if not isinstance(s, (int, float)):
                s = self._eval(s, ctx)
                s = s.to_python() if isinstance(s, MohioValue) else s
            status = int(s)
        # Verbose trace: show give back before it propagates
        _v = value.to_python() if isinstance(value, MohioValue) else value
        self._debug_trace(ctx, f"give back {status} {_v!r} -> propagating up")
        raise _GiveBack(status=status, value=value, fmt=fmt)

    def _exec_HaltStmt(self, node, ctx):
        if node.qualifier:
            if not self._eval_condition(node.qualifier.condition, ctx):
                return None
        raise _Halt()

    def _exec_StopStmt(self, node, ctx):
        cond = getattr(node, 'condition', None)
        if cond is not None and not self._eval_condition(cond, ctx):
            return None   # 'stop when ...' and the condition is false -- keep looping
        raise _Stop(target=getattr(node, 'target', None))
    def _exec_SkipStmt(self, node, ctx):
        cond = getattr(node, 'condition', None)
        if cond is not None and not self._eval_condition(cond, ctx):
            return None   # 'skip when ...' and the condition is false -- don't skip
        raise _Skip()

    def _exec_JumpToStmt(self, node, ctx):
        if node.qualifier:
            if not self._eval_condition(node.qualifier.condition, ctx):
                return None
        dest = node.destination
        if isinstance(dest, str):
            d = dest.strip()
            # a quoted string literal -> the bare URL / path
            if len(d) >= 2 and d[0] in ('"', "'") and d[-1] == d[0]:
                d = d[1:-1]
        else:
            # a variable / dotted name -> its value
            v = self._eval(dest, ctx)
            d = v.to_python() if hasattr(v, 'to_python') else str(v)
        raise _Jump(destination=str(d))

    def _exec_ShowStmt(self, node, ctx):
        self._check_purpose(node.value, ctx)
        # A bare `show missing` on an undefined single variable fails loud -- consistent with
        # interpolation ({{ missing }} already fails loud). Silently showing None for a typo is the
        # exact surprising-behavior the language avoids. Field access (x.field) and literals are
        # unaffected; this only guards a lone, undefined name.
        _v = node.value
        if type(_v).__name__ == 'DottedName' and len(getattr(_v, 'parts', [])) == 1:
            _nm = _v.parts[0]
            if not ctx.exists(_nm):
                raise MohioRuntimeError(
                    f"show refers to an unknown variable '{_nm}'. Declare it first "
                    f"(e.g. `hold {_nm} <value>`), or check the spelling.")
        val = self._eval(node.value, ctx)
        self._check_purpose_value(val, ctx)
        val = self._maybe_interpolate(val, ctx, getattr(node, 'line', None))
        display = self._display_value(val)
        self.shown.append(display)          # collect for `mio run` to surface
        if self.verbose: print(f"  [show] {display}")
        return val

    def _exec_TitleDecl(self, node, ctx):
        ctx._page_title = node.text
        return MohioValue(node.text, 'string')

    def _exec_DescribeDecl(self, node, ctx):
        ctx._page_describe = node.text
        return MohioValue(node.text, 'string')

    def _ctx_attr(self, ctx, attr):
        """Find an attribute set on ctx or any ancestor (nearest wins)."""
        c = ctx
        while c is not None:
            if getattr(c, attr, None) is not None:
                return getattr(c, attr)
            c = getattr(c, '_parent', None)
        return None

    def _wrap_html_shell(self, body, ctx):
        """Wrap a render body fragment in a full HTML5 document. The runtime owns
        the boilerplate (doctype, head, charset, viewport); the developer writes
        intent. title/describe (if declared) populate the head."""
        from html import escape as _e
        title = self._ctx_attr(ctx, '_page_title')
        desc = self._ctx_attr(ctx, '_page_describe')
        head = ['<meta charset="UTF-8">',
                '<meta name="viewport" content="width=device-width, initial-scale=1.0">']
        if title:
            head.append(f'<title>{_e(title)}</title>')
        if desc:
            head.append(f'<meta name="description" content="{_e(desc, quote=True)}">')
        head_html = "\n  ".join(head)
        return self._inject_mioscript(
            f'<!DOCTYPE html>\n<html lang="en">\n<head>\n  {head_html}\n</head>\n'
            f'<body>\n{body}\n</body>\n</html>')

    def _exec_ShowBlock(self, node, ctx):
        # Raw HTML render block: show / <html> / show: done. Interpolate {{ }} then
        # emit to the same output channel as `show <value>`. HTML is rendered as-is
        # (forgiveness principle -- the parser did not restrict the inner syntax).
        html = node.html or ""
        if "{{" in html:
            html = self._interpolate_output(html, ctx, getattr(node, 'line', None),
                                            escape=getattr(node, 'escape', False))
        # A `render` block (escape=True) owns the full page: wrap the body fragment
        # in an HTML5 shell unless the author already wrote a complete document.
        if getattr(node, 'escape', False):
            low = html.lstrip().lower()
            if not (low.startswith('<!doctype') or low.startswith('<html')):
                html = self._wrap_html_shell(html, ctx)
            else:
                html = self._inject_mioscript(html)
            ctx._response_content_type = 'text/html'
        self.shown.append(html)
        if self.verbose: print(f"  [show block] {len(html)} chars")
        return MohioValue(html, 'string')

    def _exec_RaiseStmt(self, node, ctx):
        msg = self._eval_simple(node.message, ctx) if node.message else None
        raise _Raise(error_name=node.error_name, message=msg, line=getattr(node, 'line', None))

    def _exec_SendStmt(self, node, ctx):    raise MohioRuntimeError("WebSocket send is declared but not yet executable in this build (the message would silently not be sent). Tracked for a future release.")
    def _exec_BroadcastStmt(self, node, ctx): raise MohioRuntimeError("broadcast is declared but not yet executable in this build (the message would silently not be sent). Tracked for a future release.")
    def _exec_StreamStmt(self, node, ctx):  raise MohioRuntimeError("stream is declared but not yet executable in this build (it would silently emit nothing). Tracked for a future release.")
    def _exec_NotifyStmt(self, node, ctx):  raise MohioRuntimeError("notify is declared but not yet executable in this build (the notification would silently not be sent). Tracked for a future release.")
    # Sectors that raise the default 8-place cap for a bare `as.dec`. A sector
    # profile can register a higher precision here; science is the first.
    _SECTOR_DECIMAL_CAPS = {"science": 15}

    # Currency MVP: USD/CAD/EUR/GBP. Each is a dec.2 precision contract plus a display format.
    # The stored value is a number (rounded half-up to 2 places); the format is applied only when
    # the value is rendered to a string. thousands/decimal are the separators; symbol_before puts
    # the symbol ahead of the number (all four, MVP).
    _CURRENCIES = {
        'USD': {'symbol': '$', 'places': 2, 'thousands': ',', 'decimal': '.', 'symbol_before': True},
        'CAD': {'symbol': '$', 'places': 2, 'thousands': ',', 'decimal': '.', 'symbol_before': True},
        'GBP': {'symbol': '£', 'places': 2, 'thousands': ',', 'decimal': '.', 'symbol_before': True},
        'EUR': {'symbol': '€', 'places': 2, 'thousands': '.', 'decimal': ',', 'symbol_before': True},
    }

    def _display_text(self, val):
        """Render a value to its display string. A currency-tagged value formats as money
        ($1,234.56); everything else uses the normal text conversion. This is the single place
        display formatting is decided, so every output path (interpolation, give back, show,
        concat) renders currency consistently."""
        cur = getattr(val, '_currency', None) if isinstance(val, MohioValue) else None
        py = val.to_python() if isinstance(val, MohioValue) else val
        if cur and self._is_currency(cur):
            return self._format_currency(py, cur)
        pad = getattr(val, '_pad_places', None) if isinstance(val, MohioValue) else None
        if pad is not None and isinstance(py, (int, float)) and not isinstance(py, bool):
            return f"{float(py):.{pad}f}"
        return _mohio_text(py)

    def _is_currency(self, type_name):
        return str(type_name).upper() in self._CURRENCIES if type_name else False

    def _format_currency(self, num, code):
        """Render a number as a currency string: symbol + thousands + locale decimal.
        1234.5 as USD -> '$1,234.50' ; 1234.5 as EUR -> '€1.234,50'. Negatives keep the sign
        before the symbol (-$5.00) for the MVP.
        """
        spec = self._CURRENCIES.get(str(code).upper())
        if spec is None:
            return str(num)
        places = spec['places']
        try:
            val = self._round_places(float(num), places)
        except (TypeError, ValueError):
            return str(num)
        neg = val < 0
        val = abs(val)
        whole = int(val)
        frac = int(round((val - whole) * (10 ** places)))
        # group the whole part with the thousands separator
        whole_str = f"{whole:,}".replace(',', spec['thousands'])
        frac_str = str(frac).rjust(places, '0') if places > 0 else ''
        body = whole_str + (spec['decimal'] + frac_str if places > 0 else '')
        sign = '-' if neg else ''
        if spec['symbol_before']:
            return f"{sign}{spec['symbol']}{body}"
        return f"{sign}{body}{spec['symbol']}"

    def _round_places(self, num, places):
        """Round a number to `places` decimal places, HALF-UP (money standard).

        Deterministic via Decimal ROUND_HALF_UP: 19.995 -> 20.00, 19.994 -> 19.99, 2.675 -> 2.68.
        This is the rounding used by CURRENCY types (where dec.N truncates). Returns a float. A
        value that cannot be represented as Decimal falls back to the input.
        """
        try:
            p = max(0, int(places))
        except (TypeError, ValueError):
            return num
        try:
            d = Decimal(str(num))
        except (InvalidOperation, ValueError):
            return num
        q = Decimal(1) if p == 0 else Decimal(1).scaleb(-p)
        return float(d.quantize(q, rounding=ROUND_HALF_UP))

    def _truncate_places(self, num, places):
        """Cap a number to AT MOST `places` decimal places by TRUNCATION (no rounding).

        Deterministic: uses Decimal quantize with ROUND_DOWN so 10.45676 -> 10.45 exactly, with no
        float-representation or banker's-rounding surprise. Values with fewer places than the cap
        are unchanged (10.45 at 5 places stays 10.45 -- truncation never pads; that is `.pad`'s
        job). Returns a float to stay compatible with the rest of the numeric path. `places` < 0 is
        treated as 0. A value that cannot be represented as Decimal falls back to the input.
        """
        try:
            p = max(0, int(places))
        except (TypeError, ValueError):
            return num
        try:
            d = Decimal(str(num))
        except (InvalidOperation, ValueError):
            return num
        if p == 0:
            q = Decimal(1)
        else:
            q = Decimal(1).scaleb(-p)          # 10^-p, e.g. p=2 -> 0.01
        truncated = d.quantize(q, rounding=ROUND_DOWN)
        return float(truncated)

    def _decimal_cap(self, ctx):
        """Max decimal places for a bare `as.dec` / `as.decimal` (no explicit
        `.N`). Defaults to 8 to keep precision sane; a sector can raise it
        (science needs more). An express `as.dec.N` bypasses this entirely."""
        sector = getattr(ctx, "_sector", None) or getattr(self, "_sector", None)
        if sector:
            base = str(sector).split(".")[0].lower()
            raised = self._SECTOR_DECIMAL_CAPS.get(base)
            if raised is not None:
                return raised
        return 8

    def _cm_describe(self, node):
        """A stable string name for a compliance target/field (declarative record)."""
        from mohio_ast import DottedName, Literal
        if node is None: return ""
        if type(node).__name__ == 'DurationExpr':
            return f"{getattr(node, 'count', '')} {getattr(node, 'unit', '')}".strip()
        if type(node).__name__ == 'DbRef':
            return f"db.{getattr(node, 'table', '')}"
        if isinstance(node, DottedName): return ".".join(str(p) for p in node.parts)
        if isinstance(node, Literal): return str(node.value)
        if hasattr(node, 'parts'): return ".".join(str(p) for p in node.parts)
        return str(getattr(node, 'value', node))

    def _compliance_audit(self, ctx, entry):
        """Record a compliance policy/action in the in-memory registry, the
        db.compliance_audit table (append, when a db is connected), and a miolog
        audit line. Declarative: records intent + trail; it does not enforce."""
        import datetime as _dt
        entry = dict(entry)
        entry.setdefault('at', _dt.datetime.utcnow().isoformat())
        if not hasattr(self, '_compliance') or self._compliance is None:
            self._compliance = []
        self._compliance.append(entry)
        db = ctx.get_connection('db') if hasattr(ctx, 'get_connection') else None
        if db is not None:
            try:
                row = {k: (v if isinstance(v, (int, float, str)) else str(v))
                       for k, v in entry.items()}
                self._audit_chained_save(db, 'compliance_audit', row)
            except MohioInterpreter.AuditContentRefused:
                raise                       # a refused record is never downgraded to a warning
            except Exception as _e:
                # `compliance_audit` is the PRIMARY compliance log. Swallowing a failed write
                # here left stdout as the only trace that a compliance record did not persist --
                # and stdout is not an audit trail. Surface it on stderr so a failure is visible
                # in logs that are actually collected, and record the reason.
                import sys as _sys
                print(f"  [audit] WARNING: compliance_audit write failed: {_e}. "
                      f"The compliance record for this action was NOT persisted.",
                      file=_sys.stderr)
        try:
            detail = ", ".join(f"{k}={v}" for k, v in entry.items() if k != 'at')
            print(f"  [miolog.audit] compliance {entry.get('action', '?')}: {detail}")
        except Exception:
            pass

    def _exec_CmRetainStmt(self, node, ctx):
        # Declarative: record a retention policy (keep this data for the period).
        self._compliance_audit(ctx, {
            'action': 'retain',
            'field': self._cm_describe(getattr(node, 'value', None)),
            'duration': self._cm_describe(getattr(node, 'duration', None))})
        return None

    def _exec_CmExpireStmt(self, node, ctx):
        # Declarative: record an expiry policy (delete this data after the period).
        self._compliance_audit(ctx, {
            'action': 'expire',
            'field': self._cm_describe(getattr(node, 'value', None)),
            'duration': self._cm_describe(getattr(node, 'duration', None))})
        return None

    def _exec_CmLockStmt(self, node, ctx):
        # Declarative: record a legal hold on this data.
        self._compliance_audit(ctx, {
            'action': 'lock',
            'target': self._cm_describe(getattr(node, 'target', None))})
        return None

    def _tombstone_row_ref(self, table, field, value):
        """A row reference safe to keep in a TOMBSTONE forever.

        The universal primary key `id` is a surrogate, not personal data, so it is kept in the
        clear and names exactly which row was erased. ANY other match value (email, ssn, phone) is
        recorded as a per-deployment SALTED hash and never in the clear -- copying an erased
        identifier into the trail would re-create the very data the erasure removed, and an
        UNSALTED hash of a low-cardinality field is trivially reversed by hashing the candidate
        space. The salt lives in the deployment (env MOHIO_AUDIT_SALT) and is NEVER written beside
        the hash, so the stored reference cannot be turned back into the value even by someone who
        holds the whole audit store. The verifier recomputes the hash from the same deployment salt
        when it adjudicates a row (D3)."""
        if str(field) == 'id':
            return {'field': 'id', 'kind': 'id', 'ref': str(value)}
        salt = os.environ.get('MOHIO_AUDIT_SALT')
        if not salt:
            raise MohioRuntimeError(
                "cm.purge matched on a non-id field, so its tombstone must record a "
                "NON-reversible reference to the erased row -- and that needs a per-deployment "
                "audit salt. Set MOHIO_AUDIT_SALT to a stable secret kept OUT of the audit store "
                "(without it, a hash of a low-cardinality field like email/ssn is reversible). "
                "Matching on the primary key `id` needs no salt.")
        import hashlib, hmac
        digest = hmac.new(salt.encode('utf-8'),
                          f"{table}|{field}|{value}".encode('utf-8'),
                          hashlib.sha256).hexdigest()
        return {'field': str(field), 'kind': 'hash', 'ref': digest}

    def _exec_CmPurgeBlock(self, node, ctx):
        # cm.purge is the right-to-be-forgotten verb; `reason` is required.
        # FREE TIER (a tool, not a done-for-you engine):
        #   `cm.purge from db.X / match id to Y`  -> deletes THOSE matched rows from
        #       that ONE table and audits it. The developer cascades across stores
        #       by writing one cm.purge per table. Lock-aware and match-required for
        #       safety. The commercial tier does the whole shape-driven cascade in
        #       one line -- that is the paid value, not given away here.
        #   `cm.purge member.id`  (value form) -> records the erasure request +
        #       audit only; the developer writes the deletion.
        reason_node = getattr(node, 'reason', None)
        reason = None
        if reason_node is not None:
            if isinstance(reason_node, str):
                reason = reason_node          # backward-compat (plain string)
            else:
                val = self._eval(reason_node, ctx)
                if isinstance(val, MohioValue):
                    val = val.to_python()
                reason = str(val) if val is not None else None
        if not reason or not str(reason).strip():
            raise MohioRuntimeError(
                "cm.purge requires a non-empty `reason` -- an erasure without a "
                "documented reason is not a defensible audit record. Add: "
                "reason \"...\" (a literal, a variable, or request.field all work).")
        source  = getattr(node, 'source', None)
        matches = getattr(node, 'matches', None) or []

        if source is None:
            # Value form: record intent + audit; the developer writes the deletion.
            self._compliance_audit(ctx, {
                'action': 'purge_requested',
                'target': self._cm_describe(getattr(node, 'target', None)),
                'reason': reason,
                'status': 'recorded (audit only; write the deletion, or use the '
                          'commercial runtime for the full cascade)'})
            return None

        # From form: bounded, explicit, lock-aware deletion of matched rows.
        table = self._cm_describe(source)
        table_name = table[3:] if table.startswith('db.') else table
        if not matches:
            raise MohioRuntimeError(
                "cm.purge from a table requires a `match` to scope which rows to "
                "erase -- a table-wide purge is not allowed. Add: match <field> to "
                "<value>.")
        locked = {str(e.get('target')) for e in getattr(self, '_compliance', [])
                  if e.get('action') == 'lock'}
        if table_name in locked or table in locked:
            raise MohioRuntimeError(
                f"cm.purge refused: '{table_name}' is under a cm.lock legal hold and "
                f"cannot be erased. Release the hold first.")
        db = ctx.get_connection('db') if hasattr(ctx, 'get_connection') else None
        if db is None:
            raise MohioRuntimeError(
                "cm.purge from a table needs a database connection "
                "(connect db as ... from env.DATABASE_URL).")
        # Resolve every match clause -- its value AND its safe tombstone reference -- BEFORE any
        # deletion. A non-id match with no per-deployment salt fails loud HERE, so we never erase a
        # row we could not then tombstone (no rows-gone-but-no-record state).
        resolved = []
        for m in matches:
            field = getattr(m, 'field', None)
            val = self._eval_simple(getattr(m, 'value', None), ctx)
            if isinstance(val, MohioValue):
                val = val.to_python()
            resolved.append((str(field), val, self._tombstone_row_ref(table_name, field, val)))

        # ATOMIC ERASURE. Right-to-be-forgotten is all-or-nothing. Batch EVERY clause's delete in a
        # transaction so that a failure on ANY clause rolls back ALL of them -- never a half-erasure
        # (an erased row with no tombstone, which the verifier would read as tampering: the same
        # false-evidence class as a false tombstone, through a different door). The per-remove
        # auto-commit is suppressed while we batch; we commit ONCE, at the end, only on full success.
        _prev_txn = getattr(db, '_in_transaction', False)
        try: db._in_transaction = True
        except Exception: pass
        erased = []                 # (field, row_ref) for clauses that ACTUALLY erased row(s)
        try:
            for field, val, ref in resolved:
                removed = db.remove(table_name, field, val)
                # db.remove returns the deleted row count. Only a delete that removed >= 1 row is an
                # actual erasure; a 0-row match is not (no delete -> no tombstone). A backend that
                # returns no count (None) is assumed to have erased.
                if removed is None or (isinstance(removed, int) and removed > 0):
                    erased.append((field, ref))
            db.conn.commit()        # commit ALL clause deletes atomically
        except Exception as e:
            try: db.conn.rollback() # ANY clause failed -> roll back EVERYTHING; nothing erased
            except Exception: pass
            raise MohioRuntimeError(
                f"cm.purge could not erase from '{table_name}': {e}. All matched deletes were "
                f"rolled back -- NOTHING was erased and NO tombstone was written. Right-to-be-"
                f"forgotten is atomic: it erases everything or nothing, never a half-erasure.")
        finally:
            try: db._in_transaction = _prev_txn
            except Exception: pass
        # TOMBSTONE: the authoritative, append-only marker of a LAWFUL erasure. Written LAST -- only
        # AFTER the deletes above have COMMITTED. If the deletes rolled back, the raise above skipped
        # this, so a tombstone can never outlive an erasure that did not commit. Written through
        # _audit_event -- the SAME isolated seam every governance event uses -- so when a dedicated
        # audit_writer sink is bound the tombstone lands THERE and never on the tenant db the erased
        # row lived in. That routing lets the verifier tell a lawful erasure (a missing row WITH a
        # matching tombstone) from tampering (WITHOUT one). `row_refs` name WHICH rows were erased
        # without holding the values: the PK id in the clear (a surrogate key), any other match
        # value only as a per-deployment salted hash. Written ONLY when at least one row was actually
        # erased -- NEVER an empty (row_refs=[]) tombstone claiming an erasure that did not happen.
        if erased:
            session_id, member_id = self._audit_actor(ctx)
            self._audit_event('data_audit_log', {
                'event':        'TOMBSTONE',
                'table':        table_name,
                'match_fields': sorted({f for f, _ in erased}),
                'row_refs':     [ref for _, ref in erased],
                'reason':       reason,
                'legal_basis':  'GDPR Art. 17(1)',
                'session_id':   session_id,
                'member_id':    member_id,
            }, ctx)
        return None

    def _exec_CmReportStmt(self, node, ctx):  raise MohioRuntimeError("cm.report is a planned commercial capability (managed regulatory filing, e.g. CTR/SAR). It does not run on the open runtime -- a report would not be filed. Use the commercial runtime for regulatory reporting.")

    def _exec_CmNotifyStmt(self, node, ctx):  raise MohioRuntimeError("cm.notify is a planned commercial capability (managed breach notification). It does not run on the open runtime. Use the commercial runtime for breach notification.")
    def _exec_VerifyTokenStmt(self, node, ctx):
        # `verify token` is a REAL auth check, not a declaration — so it must not
        # silently pass. Until signature/expiry/scope verification ships with
        # mioauth.jwt, fail loud so no one relies on it for auth and gets a
        # silent security hole.
        raise MohioRuntimeError(
            "verify token is declared but not yet executable in this build. "
            "It would SILENTLY PASS authentication, which is unsafe — do not rely "
            "on it for auth yet. Real token verification is tracked for the "
            "mioauth.jwt release.")

    def _exec_NotBuiltService(self, node, ctx):
        # A dedicated mio* service that parses but is not executable in this build.
        # Fail loud at the point of use (never silently no-op), with the fix named.
        call  = node.service + (f".{node.method}" if node.method else "")
        where = f" (line {node.line})" if getattr(node, 'line', 0) else ""
        if getattr(node, 'tier', 'plain') == 'commercial':
            raise MohioRuntimeError(
                f"{call} is a commercial-tier managed service and is not available in "
                f"the open compiler{where}. Left silent it would look like it ran while "
                f"doing nothing, which is exactly what Mohio must never do. Structural "
                f"enforcement is free; this managed capability is licensed. To proceed: "
                f"remove {call}, or run it under a Mohio commercial license.")
        _alt = ""
        if node.service == 'mioai':
            _alt = (" For AI, use ai.decide (reasoning) or ai.create (generate text, data, an image, "
                    "or video) -- both are wired.")
        raise MohioRuntimeError(
            f"{call} is declared in the grammar but not built in this release{where}. "
            f"Left silent it would no-op and hide the gap. To proceed: remove {call} "
            f"for now, or use a built alternative.{_alt} Tracked for a future release.")

    def _exec_ServiceCallStmt(self, node, ctx):
        service = node.service
        method  = node.method

        if service == 'ai' and method == 'create':
            # `ai.create text|image|video "<prompt>" [as NAME]` -- the inline
            # generation form parses as a service call. Build a synthetic
            # AiCreateStmt so it shares the one generation code path (text/image/
            # video + `as` binding + mock/real runtime).
            from types import SimpleNamespace
            from lark import Tree as _T, Token as _Tok
            TYPES = ('text', 'image', 'video', 'audio', 'logic', 'data')
            state = {'ctype': 'text', 'prompt': None, 'alias': ''}
            def _walk(n):
                if isinstance(n, _T):
                    kids = n.children
                    # `as NAME` service_param -> the result binding
                    if kids and isinstance(kids[0], _Tok) and str(kids[0]) == 'as':
                        aval = kids[1] if len(kids) > 1 else None
                        if aval is not None:
                            state['alias'] = (".".join(str(x) for x in aval.parts)
                                              if hasattr(aval, 'parts')
                                              else str(getattr(aval, 'value', aval)))
                        return
                    for c in kids:
                        _walk(c)
                elif isinstance(n, _Tok):
                    if n.type == 'NAME' and str(n) in TYPES:
                        state['ctype'] = str(n)
                else:
                    # Literal or DottedName -> the prompt
                    try:
                        v = self._eval(n, ctx)
                        state['prompt'] = (v.to_python() if isinstance(v, MohioValue) else v)
                    except Exception:
                        state['prompt'] = str(getattr(n, 'value',
                                              getattr(n, 'parts', n)))
            _walk(getattr(node, 'args', None))
            for p in (getattr(node, 'params', None) or []):
                _walk(p)
            syn = SimpleNamespace(
                create_type=state['ctype'], name=(state['alias'] or 'output'),
                alias=state['alias'], goal=(state['prompt'] or ''), source='',
                attrs={}, style='', negative='', persona='', context='', size='',
                model='', temperature=None, duration=None)
            return self._exec_AiCreateStmt(syn, ctx)

        if service == 'ai' and method == 'decide':
            # Bare invocation: `ai.decide <name>` re-runs a previously-defined
            # ai.decide block and binds its result to a variable named <name>
            # (so the next line, e.g. `check <name>`, can read it). This is the
            # define-at-top / invoke-deep pattern Zork uses for its self-healing
            # noun resolver. The block must be defined with
            # `ai.decide <name> returns <type> ... ai.decide: done`.
            a = node.args
            if isinstance(a, DottedName):
                name = '.'.join(a.parts)
            elif a is None:
                name = None
            else:
                ev = self._eval(a, ctx)
                name = ev.to_python() if isinstance(ev, MohioValue) else str(ev)
            block = getattr(self, '_ai_blocks', {}).get(name)
            if block is None:
                raise MohioRuntimeError(
                    f"ai.decide {name or '?'}: no ai.decide block named "
                    f"'{name}' is defined. Define it first with "
                    f"`ai.decide {name or 'NAME'} returns <type> ... ai.decide: done`, "
                    f"then invoke it with `ai.decide {name or 'NAME'}`.")
            return self._exec_AiDecideBlock(block, ctx)

        if service == 'miolog':
            msg    = self._eval(node.args, ctx) if node.args else MohioValue('')
            raw    = msg.to_python() if isinstance(msg, MohioValue) else msg
            level  = method.split('.')[-1] if '.' in method else method
            if self.verbose:
                print(f"  [miolog.{level}] {raw}")
            return MohioValue({'level': level, 'message': raw})

        if service == 'mask':
            # A9 (decision B): the mask.last / mask.first / mask.all shorthand is
            # DROPPED. The canonical, unambiguous masking form is
            # `value mask.all except last N` (reveal the last N, mask the rest).
            # The shorthand read backwards ("mask.last" sounds like "hide the last"),
            # so it is not a Mohio form -- steer to the clear one.
            raise MohioRuntimeError(
                f"mask.{method} is not a Mohio form. To mask sensitive data use "
                f"`value mask.all except last N` or `value mask.all except first N` "
                f"-- it reveals only the last/first N characters and masks the rest.")

        if service == 'miocache':
            # The WIRED cache forms (get/set/delete/flush/exists) are grammar terminals routed
            # to _exec_MioCacheStmt; a real miss there returns empty cleanly. Anything reaching
            # THIS generic handler is an unrecognized method -- a typo like `miocache.gett`. The
            # old code returned None here, so a typo'd cache call silently no-op'd and the caller
            # saw a phantom "miss" forever (same class as the miomail.queue silent no-op). Fail
            # loud: this is an INVALID call (a mistake to fix), not an unbuilt feature to wait on.
            raise MohioRuntimeError(
                f"miocache.{method} is not a cache method. The cache methods are "
                f"miocache.get, miocache.set, miocache.delete, miocache.flush, and "
                f"miocache.exists. Check the spelling -- a typo here used to silently "
                f"return nothing (a phantom cache miss) instead of telling you.")

        # Side-effecting / data services: a silent no-op here means the email is
        # never sent, the SMS never goes out, the AI never runs -- the exact "looks
        # fine, does nothing" failure. Fail loud instead, with a hint to the wired
        # path where one exists (e.g. the miomail block form actually sends).
        _service_hints = {
            'miomail':  ("miomail.{m} is not a wired form. Use miomail.send with inline fields, "
                         "which sends via your configured provider (SendGrid / Brevo / SMTP): "
                         "miomail.send to X subject Y body Z."),
            'miosms':   ("miosms.{m} is not yet executable (the SMS would silently NOT "
                         "be sent). No SMS provider is wired yet."),
            'miopdf':   "miopdf.{m} is declared but not yet executable (it would silently do nothing).",
            'miofile':  "miofile.{m} is declared but not yet executable (it would silently do nothing).",
            'mioimage': "mioimage.{m} is declared but not yet executable (it would silently do nothing).",
        }
        if service == 'ai' and method == 'chain':
            # ai.chain is RETIRED (ai.connect + order is canonical). Was caught by the generic
            # "no handler" catch-all; name the replacement directly.
            raise MohioRuntimeError(
                "ai.chain is retired. Use `ai.connect` for provider fallback chains "
                "(ai.connect NAME / order / <providers> / order: done). Did you mean `ai.connect`?")
        if service == 'mioai':
            # mioai.text/.image/.audio dotted generative is RETIRED (2026-08-01): `ai.create` is the
            # canonical wired path for generating text, data, images, audio, and video. A working
            # alternative exists, so name it directly rather than "not wired".
            raise MohioRuntimeError(
                f"mioai.{method} is retired. Use `ai.create` to generate text, data, an image, "
                f"audio, or video (e.g. `ai.create poster image`), or `ai.decide` for AI reasoning "
                f"-- both are wired. Did you mean `ai.create`?")
        if service == 'miohttp':
            # miohttp verbs beyond the wired set are RETIRED (2026-08-01). get/post/put/delete/patch
            # are all wired outbound-HTTP verbs; anything else is not a miohttp verb -- name the
            # working ones directly rather than "not wired".
            raise MohioRuntimeError(
                f"miohttp.{method} is not a wired HTTP verb; the extra dotted forms are retired. "
                f"Use one of the wired verbs: miohttp.get, miohttp.post, miohttp.put, "
                f"miohttp.delete, or miohttp.patch.")
        if service in _service_hints:
            raise MohioRuntimeError(
                _service_hints[service].format(m=method) + " Tracked for a future release.")

        # Unknown service.method: do not silently swallow it.
        raise MohioRuntimeError(
            f"{service}.{method} has no handler in this build (it would silently do "
            f"nothing). If this should work, it is not wired yet. Tracked for a "
            f"future release.")

    # ── Assignment ────────────────────────────────────────────

    def _exec_MiofileDecl(self, node, ctx):
        # Open core PARSES and VALIDATES storage declarations. Local/temp zones are the
        # free engine. Cloud zones and the managed lifecycle policies (expires, clean)
        # are the commercial layer: their executor lives in the Mohio commercial runtime,
        # so here we fail loud unless the deployment is licensed. Never silently accept a
        # paid declaration as if it were running.
        import os
        enforce = os.environ.get("MOHIO_ENFORCE_LICENSE")
        entitled = (not enforce) or bool(os.environ.get("MOHIO_OWNER") or os.environ.get("MOHIO_LICENSE"))
        for z in (node.zones or []):
            paid = [p.get("policy") for p in (z.get("policies") or [])
                    if p.get("policy") in ("expires", "clean")]
            is_cloud = (z.get("kind") == "cloud")
            if (is_cloud or paid) and not entitled:
                if is_cloud:
                    feat = "cloud storage"
                else:
                    feat = "managed file lifecycle (" + ", ".join(paid) + ")"
                raise MohioRuntimeError(
                    f"miofile {feat} is a commercial feature. It is declared and validated "
                    f"in open core, but it runs only in the Mohio commercial runtime. Use "
                    f"local or temp storage to stay in open core, or set MOHIO_LICENSE for a "
                    f"licensed deployment. Contact hello@mohio.io.")
        # Register the zones so file operations resolve through them. Without this the
        # declaration validated and then governed nothing: `accept jpg` on a zone was
        # accepted and ignored, which is the one thing Mohio must never do.
        specs = [self._miofile_zone_spec(z) for z in (node.zones or [])]
        self._miofile_zones = (getattr(self, '_miofile_zones', None) or []) + specs
        return None

    _MIOFILE_SIZE_UNITS = {'b': 1, 'kb': 1024, 'mb': 1024 ** 2, 'gb': 1024 ** 3}

    def _miofile_zone_spec(self, zone):
        """Normalize a declared zone to {name, kind, path, accept, maxsize}.

        `accept` and `max size` must mean exactly what they mean on a shape field, so
        this mirrors field_accept_mod / field_maxsize_mod: extensions lowercased with
        no leading dot, sizes in bytes, MB when no unit is given. One word, one job.
        """
        def _unquote(s):
            s = str(s if s is not None else '')
            return s[1:-1] if len(s) >= 2 and s[0] == s[-1] == '"' else s

        path = _unquote(zone.get('path')).replace('\\', '/').strip('/')
        accept, maxsize = None, None
        for pol in (zone.get('policies') or []):
            kind, parts = pol.get('policy'), (pol.get('parts') or [])
            if kind == 'accept':
                exts = []
                for c in parts:
                    items = c.children if hasattr(c, 'children') else [c]
                    for t in items:
                        exts.append(_unquote(t).strip().lstrip('.').lower())
                accept = [e for e in exts if e]
            elif kind == 'max':
                nums = [str(c) for c in parts if str(c).replace('.', '', 1).isdigit()]
                units = [str(c).strip().lower() for c in parts
                         if not str(c).replace('.', '', 1).isdigit()]
                if nums:
                    mult = self._MIOFILE_SIZE_UNITS.get(units[0] if units else 'mb',
                                                        1024 ** 2)
                    maxsize = int(float(nums[0]) * mult)
        return {'name': zone.get('name'), 'kind': zone.get('kind'),
                'path': path, 'accept': accept, 'maxsize': maxsize}

    def _exec_MiofileStmt(self, node, ctx):
        import os, shutil
        from pathlib import Path
        root = Path(os.environ.get("MIOFILE_ROOT",
                                   os.path.join(os.getcwd(), "mio_files"))).resolve()
        root.mkdir(parents=True, exist_ok=True)

        def _val(n):
            v = self._eval(n, ctx)
            return v.to_python() if isinstance(v, MohioValue) else v

        def _resolve(n):
            if n is None:
                raise MohioRuntimeError("miofile: a path is required here.")
            raw = str(_val(n))
            # Confine every operation to the file area: no absolute paths, no
            # parent-directory escapes. Fail loud, never reach outside the root.
            parts = raw.replace("\\", "/").split("/")
            if raw.startswith("/") or raw.startswith("\\") or ".." in parts:
                raise MohioRuntimeError(f"miofile: the path '{raw}' is not allowed (it escapes the file area).")
            target = (root / raw).resolve()
            if root != target and root not in target.parents:
                raise MohioRuntimeError(f"miofile: the path '{raw}' is not allowed (it escapes the file area).")
            return target, raw

        def _zone_for(raw):
            """The declared zone governing this path, or None. Longest path wins so a
            nested zone beats its parent."""
            best = None
            r = str(raw).replace('\\', '/').strip('/')
            for z in (getattr(self, '_miofile_zones', None) or []):
                zp = z.get('path') or ''
                if zp and (r == zp or r.startswith(zp + '/')):
                    if best is None or len(zp) > len(best.get('path') or ''):
                        best = z
            return best

        def _enforce_zone(raw, size_bytes):
            """Apply a matching zone's accept / max size. A path in no declared zone
            keeps the default file-area behaviour (the check-time scan warns about it),
            so declaring zones tightens without breaking programs that declare none."""
            z = _zone_for(raw)
            if z is None:
                return
            area = z.get('name') or z.get('path')
            tail = str(raw).replace('\\', '/').rsplit('/', 1)[-1].rsplit('.', 1)
            ext = tail[1].lower() if len(tail) == 2 else ''
            # Executable types are refused in a declared area even when `accept` names
            # them, exactly as they are on an upload field: the blocklist is checked
            # before the allowlist, so widening `accept` can never opt back in. A
            # declared area is the governed surface, so it carries the same rule.
            if ext in self._DANGEROUS_UPLOAD_EXT:
                raise MohioRuntimeError(
                    f"miofile: '{raw}' is an executable file type, which a declared area "
                    f"never accepts. Listing {ext} in `accept` does not change this -- the "
                    f"same rule applies to uploads. To proceed: use a non-executable type.")
            acc = z.get('accept')
            if acc and ext not in acc:
                raise MohioRuntimeError(
                    f"miofile: '{raw}' is not an accepted file type for the {area} area, "
                    f"which accepts {', '.join(acc)}. To proceed: use an accepted type, or "
                    f"add that type to `accept` on the area.")
            mx = z.get('maxsize')
            if mx is not None and size_bytes is not None and size_bytes > mx:
                raise MohioRuntimeError(
                    f"miofile: '{raw}' is {size_bytes} bytes, over the "
                    f"{mx / (1024 * 1024):g} MB limit on the {area} area. To proceed: use a "
                    f"smaller file, or raise `max size` on that area.")

        op = node.op
        if op == "read":
            target, raw = _resolve(node.path)
            if not target.is_file():
                raise MohioRuntimeError(f"miofile.read: there is no file at '{raw}'.")
            data = target.read_text(encoding="utf-8")
            if node.alias:
                ctx.set(node.alias, MohioValue(data, "text"))
            return MohioValue(data, "text")
        if op == "write":
            target, raw = _resolve(node.path)
            content = "" if node.content is None else str(_val(node.content))
            _enforce_zone(raw, len(content.encode("utf-8")))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return MohioValue(True, "boolean")
        if op == "delete":
            target, raw = _resolve(node.path)
            if not target.is_file():
                raise MohioRuntimeError(f"miofile.delete: there is no file at '{raw}'.")
            target.unlink()
            return MohioValue(True, "boolean")
        if op == "exists":
            target, raw = _resolve(node.path)
            return MohioValue(target.exists(), "boolean")
        if op in ("move", "copy"):
            src, sraw = _resolve(node.path)
            dst, draw = _resolve(node.dest)
            if not src.exists():
                raise MohioRuntimeError(f"miofile.{op}: there is no file at '{sraw}'.")
            _enforce_zone(draw, src.stat().st_size if src.is_file() else None)
            dst.parent.mkdir(parents=True, exist_ok=True)
            if op == "move":
                shutil.move(str(src), str(dst))
            else:
                shutil.copy2(str(src), str(dst))
            return MohioValue(True, "boolean")
        if op == "list":
            target, raw = _resolve(node.path)
            if not target.is_dir():
                raise MohioRuntimeError(f"miofile.list: there is no folder at '{raw}'.")
            entries = sorted(p.name for p in target.iterdir())
            if node.alias:
                ctx.set(node.alias, MohioValue(entries, "list"))
            return MohioValue(entries, "list")
        raise MohioRuntimeError(f"miofile.{op} is not a known file action.")

    def _exec_MiomailStmt(self, node, ctx):
        """Send email via configured provider — SendGrid, Brevo, SMTP, or mock."""
        # miomail.queue / miomail.template are commercial-tier (deferred delivery, managed templates).
        # They parse into this same statement; before A8-week (2026-07-31) they SILENTLY sent
        # immediately (queue) or dropped the template. Gate them fail-loud, same pattern as miochain's
        # "requires Mohio Commercial Runtime" -- never silently degrade to a plain send.
        _action = getattr(node, 'action', 'send')
        if _action in ('queue', 'template'):
            raise MohioRuntimeError(
                f"miomail.{_action} requires Mohio Commercial Runtime. For the open tier, send "
                f"immediately with miomail.send to X subject Y body Z.")
        # Egress checkpoint: PII cannot leave via email under a wrong purpose. Check the
        # recipient, subject, and body for direct [pii] references against the scope.
        for _tgt in (getattr(node, 'to', None), getattr(node, 'subject', None), getattr(node, 'body', None)):
            self._check_purpose(_tgt, ctx)
        import os, urllib.request, json as _json, smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        def ev(n):
            if n is None: return None
            v = self._eval(n, ctx)
            self._check_purpose_value(v, ctx)
            r = v.to_python() if isinstance(v, MohioValue) else v
            s = str(r) if r is not None else None
            if s and "{{" in s: s = self._interpolate(s, ctx)
            return s

        to_addr   = ev(node.to)
        from_addr = ev(node.from_) or os.environ.get("MIOMAIL_FROM", "noreply@mohio.io")
        from_name = ev(node.from_name) or os.environ.get("MIOMAIL_FROM_NAME", "")
        subject   = ev(node.subject) or "(no subject)"
        body_text = ev(node.body) or ""
        reply_to  = ev(node.reply_to)
        cc_list   = [ev(c) for c in (node.cc  or []) if c]
        bcc_list  = [ev(b) for b in (node.bcc or []) if b]

        if not to_addr:
            # Was a silent no-op (returned False): a bare `miomail.send` with no recipient exited 0
            # and sent nothing. Fail loud -- a send path that silently sends nothing is the exact
            # "looks fine, does nothing" trap. `to` is the one hard requirement (subject defaults,
            # and the corpus legitimately omits body); name the working form.
            raise MohioRuntimeError(
                "miomail.send requires a recipient (`to`). Use: "
                "miomail.send to \"a@b.com\" subject \"Hi\" body \"Yo\".")

        is_html = "<" in body_text and ">" in body_text

        if self.verbose:
            print(f"  [miomail] {node.action} → {to_addr} | {subject[:40]!r}")

        # SendGrid
        sgk = os.environ.get("SENDGRID_API_KEY", "")
        if sgk:
            try:
                payload = {"personalizations": [{"to": [{"email": to_addr}]}],
                           "from": {"email": from_addr, "name": from_name},
                           "subject": subject,
                           "content": [{"type": "text/html" if is_html else "text/plain", "value": body_text}]}
                if reply_to: payload["reply_to"] = {"email": reply_to}
                if cc_list:  payload["personalizations"][0]["cc"]  = [{"email": a} for a in cc_list if a]
                if bcc_list: payload["personalizations"][0]["bcc"] = [{"email": a} for a in bcc_list if a]
                req = urllib.request.Request("https://api.sendgrid.com/v3/mail/send",
                    data=_json.dumps(payload).encode(),
                    headers={"Authorization": f"Bearer {sgk}", "Content-Type": "application/json"},
                    method="POST")
                with urllib.request.urlopen(req, timeout=15) as r:
                    return MohioValue(r.status in (200, 202), "boolean")
            except Exception as e:
                if self.verbose: print(f"  [miomail] SendGrid error: {e}")
                return MohioValue(False, "boolean")

        # Brevo
        bvk = os.environ.get("BREVO_API_KEY", "")
        if bvk:
            try:
                payload = {"sender": {"email": from_addr, "name": from_name},
                           "to": [{"email": to_addr}], "subject": subject}
                payload["htmlContent" if is_html else "textContent"] = body_text
                if reply_to: payload["replyTo"] = {"email": reply_to}
                if cc_list:  payload["cc"]  = [{"email": a} for a in cc_list if a]
                if bcc_list: payload["bcc"] = [{"email": a} for a in bcc_list if a]
                req = urllib.request.Request("https://api.brevo.com/v3/smtp/email",
                    data=_json.dumps(payload).encode(),
                    headers={"api-key": bvk, "Content-Type": "application/json"},
                    method="POST")
                with urllib.request.urlopen(req, timeout=15) as r:
                    return MohioValue(r.status in (200, 201), "boolean")
            except Exception as e:
                if self.verbose: print(f"  [miomail] Brevo error: {e}")
                return MohioValue(False, "boolean")

        # SMTP
        smtp_host = os.environ.get("SMTP_HOST", "")
        if smtp_host:
            try:
                smtp_port = int(os.environ.get("SMTP_PORT", "587"))
                smtp_user = os.environ.get("SMTP_USER", from_addr)
                smtp_pass = os.environ.get("SMTP_PASS", "")
                # Port 465 is implicit SSL; 587/25 use STARTTLS. An explicit
                # SMTP_SSL flag overrides for hosts that differ.
                use_ssl = smtp_port == 465 or os.environ.get("SMTP_SSL", "").lower() in ("1", "true", "yes")
                msg = MIMEMultipart("alternative")
                msg["Subject"] = subject
                msg["From"]    = f"{from_name} <{from_addr}>" if from_name else from_addr
                msg["To"]      = to_addr
                if reply_to: msg["Reply-To"] = reply_to
                if cc_list:  msg["Cc"] = ", ".join(cc_list)
                msg.attach(MIMEText(body_text, "html" if is_html else "plain", "utf-8"))
                all_r = [to_addr] + cc_list + bcc_list
                srv = (smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=15) if use_ssl
                       else smtplib.SMTP(smtp_host, smtp_port, timeout=15))
                with srv:
                    if not use_ssl:
                        srv.ehlo(); srv.starttls(); srv.ehlo()
                    if smtp_user and smtp_pass: srv.login(smtp_user, smtp_pass)
                    srv.sendmail(from_addr, all_r, msg.as_string())
                return MohioValue(True, "boolean")
            except Exception as e:
                if self.verbose: print(f"  [miomail] SMTP error: {e}")
                return MohioValue(False, "boolean")

        # Mock — dev mode
        print(f"  [miomail] MOCK")
        print(f"    To:      {to_addr}")
        print(f"    From:    {from_name + ' <' + from_addr + '>' if from_name else from_addr}")
        print(f"    Subject: {subject}")
        if cc_list:  print(f"    CC:  {', '.join(cc_list)}")
        if bcc_list: print(f"    BCC: {', '.join(bcc_list)}")
        print(f"    Body:    {body_text[:100]}{'...' if len(body_text) > 100 else ''}")
        print(f"    Set SENDGRID_API_KEY, BREVO_API_KEY, or SMTP_HOST to send real email.")
        return MohioValue(True, "boolean")

    def _exec_MiohttpStmt(self, node, ctx):
        """Execute outbound HTTP — GET/POST/PUT/DELETE/PATCH. Response bound to alias."""
        import urllib.request, urllib.error, json as _json

        url_val = self._eval(node.url, ctx)
        url = str(url_val.to_python() if isinstance(url_val, MohioValue) else url_val)
        if "{{" in url: url = self._interpolate(url, ctx)

        method = node.method.upper()
        req_headers = {"User-Agent": "Mohio/4.0", "Accept": "application/json"}

        for hname, hval_node in (node.headers or []):
            if hval_node is not None:
                hv = self._eval(hval_node, ctx)
                hv_str = str(hv.to_python() if isinstance(hv, MohioValue) else hv)
                if "{{" in hv_str: hv_str = self._interpolate(hv_str, ctx)
                req_headers[hname] = hv_str

        if node.auth is not None:
            av = self._eval(node.auth, ctx)
            auth_str = str(av.to_python() if isinstance(av, MohioValue) else av)
            req_headers["Authorization"] = auth_str if auth_str.lower().startswith(("bearer ", "basic ")) else f"Bearer {auth_str}"

        body_bytes = None
        if node.body is not None:
            bv = self._eval(node.body, ctx)
            bp = bv.to_python() if isinstance(bv, MohioValue) else bv
            if isinstance(bp, (dict, list)):
                body_bytes = _json.dumps(bp).encode()
                req_headers.setdefault("Content-Type", "application/json")
            elif isinstance(bp, str):
                if "{{" in bp: bp = self._interpolate(bp, ctx)
                body_bytes = bp.encode()
                req_headers.setdefault("Content-Type", "text/plain")

        if self.verbose: print(f"  [miohttp] {method} {url[:60]}")

        try:
            req = urllib.request.Request(url, data=body_bytes, headers=req_headers, method=method)
            with _http_open(req, node.timeout, method, url) as resp:   # SSRF: no auto-redirect (S9)
                status   = resp.status
                raw_body = resp.read().decode("utf-8", errors="replace")
                try:   parsed = _json.loads(raw_body)
                except: parsed = raw_body
                result = {"status": status, "ok": 200 <= status < 300,
                          "body": raw_body, "json": parsed, "headers": dict(resp.headers)}
                if self.verbose: print(f"  [miohttp] → {status}")
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace") if e.fp else ""
            result = {"status": e.code, "ok": False, "body": raw, "json": None, "headers": {}, "error": str(e)}
        except MohioRuntimeError:
            raise   # a refused redirect (SSRF guard) fails loud -- never swallow into a result
        except Exception as e:
            result = {"status": 0, "ok": False, "body": "", "json": None, "headers": {}, "error": str(e)}

        mv = MohioValue(result, "shape")
        if node.alias: ctx.set(node.alias, mv)
        return mv

    def _exec_MioCookieSet(self, node, ctx):
        """
        miocookie.set "name" to value          -- inline form
        miocookie.set "name"                   -- block form
            value      session.id
            expires in 30 days
            http_only  true
            secure     true
        miocookie.set: done

        Writes to ctx.__pending_cookies__ which the server picks up
        and emits as Set-Cookie response headers.
        """
        name = node.name
        if not name:
            return None

        # 2026-08-04: mio_session (or whatever MOHIO_SESSION_COOKIE renames it to) is
        # runtime-owned, the same way `sh.` is reserved for shapes. The runtime now emits
        # this cookie itself on every session-bearing response (see run_with_session);
        # an app writing it directly could desynchronize it from the actual session
        # identity (rotation, expiry) the runtime tracks, silently and invisibly.
        if name == _SESSION_COOKIE_NAME:
            raise MohioRuntimeError(
                f"miocookie.set \"{name}\" is not allowed -- {name} is the runtime-owned "
                f"session cookie, the same way `sh.` is reserved for shapes. The runtime "
                f"sets it automatically for every session-bearing response; a session "
                f"establishes itself the moment the app reads `session.id`, or "
                f"`grant role` if the app also needs an authorized identity. Writing it "
                f"directly would let application code desynchronize it from the session "
                f"the runtime is actually tracking (rotation, expiry).")

        # Evaluate value
        val = None
        if node.inline_value is not None:
            v = self._eval(node.inline_value, ctx)
            val = v.to_python() if isinstance(v, MohioValue) else v
        elif node.value is not None:
            v = self._eval(node.value, ctx)
            val = v.to_python() if isinstance(v, MohioValue) else v

        if val is None:
            val = ''

        # Build cookie options. A node field of None means "not specified" -> apply the safe
        # default. The server reads these exact keys (value/http_only/same_site/secure/domain/
        # path/expires), so they must match.
        opts = {'value': str(val)}
        # http_only defaults ON (the grammar only lets a program turn it on, never off).
        opts['http_only'] = True if getattr(node, 'http_only', None) is None else bool(node.http_only)
        # same_site defaults Lax; normalize an explicit value to the canonical casing.
        _ss = getattr(node, 'same_site', None)
        opts['same_site'] = ({'strict': 'Strict', 'lax': 'Lax', 'none': 'None'}
                             .get(str(_ss).strip().lower(), str(_ss))) if _ss else 'Lax'
        # secure: only pin a value if the program set one explicitly; otherwise OMIT so the
        # server's scheme-based _secure_default decides (Secure on https). Writing False here
        # is what used to leave the session cookie non-Secure even on https.
        _secure = getattr(node, 'secure', None)
        if _secure is not None:
            opts['secure'] = bool(_secure)
        if getattr(node, 'domain', None):
            opts['domain'] = str(node.domain)
        if getattr(node, 'path', None):
            opts['path'] = str(node.path)
        # Expiry: the server reads opts['expires'] (seconds) and emits Max-Age. The old code
        # wrote opts['max_age'], which the server never read, so expiry was silently inert.
        _exp = getattr(node, 'expires_seconds', None)
        if _exp is not None:
            try:
                opts['expires'] = int(_exp)
            except Exception:
                pass

        # Store in pending cookies — server emits Set-Cookie headers
        pending = ctx.get('__pending_cookies__')
        pending_dict = (pending.to_python() if isinstance(pending, MohioValue)
                       else pending) if pending else {}
        if not isinstance(pending_dict, dict):
            pending_dict = {}
        pending_dict[name] = opts
        ctx.set('__pending_cookies__', MohioValue(pending_dict, 'shape'))

        if self.verbose:
            print(f"  [miocookie] set {name!r} = {str(val)[:30]!r}")

        return MohioValue(True, 'boolean')

    def _exec_MioCookieGet(self, node, ctx):
        """
        miocookie.get "name" default ""

        Reads from request cookies. Returns default if not found.
        Also accessible as: request.cookie.name
        """
        name = node.name

        # Get request cookies dict
        req = ctx.get('request')
        req_py = (req.to_python() if isinstance(req, MohioValue) else req) or {}
        cookies = req_py.get('__request_cookies__', {}) if isinstance(req_py, dict) else {}

        # Also check ctx directly (for test contexts)
        if not cookies:
            rc = ctx.get('__request_cookies__')
            if rc:
                cookies = rc.to_python() if isinstance(rc, MohioValue) else rc or {}

        val = cookies.get(name)

        # The dataclass field is `default` (mohio_ast.MioCookieGet), not `fallback`. Reading
        # node.fallback raised AttributeError the moment a cookie was missing and the default
        # was needed -- which is exactly the first-visit path.
        _fb = getattr(node, 'default', None)
        if val is None and _fb is not None:
            fb = self._eval(_fb, ctx)
            val = fb.to_python() if isinstance(fb, MohioValue) else fb

        if self.verbose:
            print(f"  [miocookie] get {name!r} = {str(val)[:30]!r}")

        return MohioValue(val, 'text')

    def _exec_MioCookieDelete(self, node, ctx):
        """
        miocookie.delete "name"
        Sets cookie with empty value and max_age=0 — browser deletes it.
        """
        name = node.name
        pending = ctx.get('__pending_cookies__')
        pending_dict = (pending.to_python() if isinstance(pending, MohioValue)
                       else pending) if pending else {}
        if not isinstance(pending_dict, dict):
            pending_dict = {}
        pending_dict[name] = {'value': '', 'max_age': 0}
        ctx.set('__pending_cookies__', MohioValue(pending_dict, 'shape'))

        if self.verbose:
            print(f"  [miocookie] delete {name!r}")
        return MohioValue(True, 'boolean')

    def _exec_MioCookieExists(self, node, ctx):
        """
        miocookie.exists "name"
        Returns true/false.
        """
        name = node.name
        req = ctx.get('request')
        req_py = (req.to_python() if isinstance(req, MohioValue) else req) or {}
        cookies = req_py.get('__request_cookies__', {}) if isinstance(req_py, dict) else {}

        # Same fallback _exec_MioCookieGet uses. Without it, exists() and get() disagree:
        # a `new sh.X` route coerces the request to the SHAPE, which strips
        # __request_cookies__ from the request dict, so the cookies only survive on ctx.
        # get() looked there; exists() did not, so exists() was ALWAYS false and every
        # session check took the `otherwise` branch.
        if not cookies:
            rc = ctx.get('__request_cookies__')
            if rc:
                cookies = rc.to_python() if isinstance(rc, MohioValue) else rc or {}

        exists = name in cookies and cookies[name] != ''

        if self.verbose:
            print(f"  [miocookie] exists {name!r} = {exists}")
        return MohioValue(exists, 'boolean')


    # ── MioLogStmt ─────────────────────────────────────────────────────
    # miolog.info / miolog.warn / miolog.error / miolog.alert
    # CRITICAL — was silently doing nothing. Now logs properly.
    def _exec_MioLogStmt(self, node, ctx):
        level = getattr(node, 'level', 'info')
        raw   = self._eval(node.value, ctx) if node.value else MohioValue('')
        msg   = raw.to_python() if isinstance(raw, MohioValue) else str(raw)
        prefix = {
            'info':  '[miolog.info]',
            'warn':  '[miolog.warn]  ⚠️',
            'error': '[miolog.error] ❌',
            'alert': '[miolog.alert] 🔔',
            'debug': '[miolog.debug]',
            'metric': '[miolog.metric]',
        }.get(str(level).lower(), f'[miolog.{level}]')
        # Always print warnings and errors regardless of verbose flag
        if str(level).lower() in ('warn', 'error', 'alert') or self.verbose:
            print(f"  {prefix} {msg}")
        ctx.set('_last_log', MohioValue({'level': level, 'message': msg}))
        return MohioValue({'level': level, 'message': msg})

    # ── MioCacheStmt ────────────────────────────────────────────────────
    # miocache.get/set/delete/flush/exists
    def _exec_MioCacheStmt(self, node, ctx):
        op  = getattr(node, 'op',  'get')
        key = getattr(node, 'key', '')
        # Resolve key if it's a variable reference
        key_resolved = str(self._eval(key, ctx).to_python() if key else '')
        # Simple in-memory cache stored on interpreter instance
        if not hasattr(self, '_cache'):
            self._cache = {}
        if op == 'set':
            vals = getattr(node, 'values', [])
            val  = self._eval(vals[0], ctx) if vals else MohioValue(None)
            self._cache[key_resolved] = val
            if self.verbose:
                print(f"  [miocache.set] {key_resolved}")
            return val
        elif op == 'get':
            if key_resolved in self._cache:
                result = self._cache[key_resolved]
            else:
                # cache miss: use `default value_expr` if provided, else None
                dvals = getattr(node, 'values', [])
                result = self._eval(dvals[0], ctx) if dvals else MohioValue(None)
            result = result if isinstance(result, MohioValue) else MohioValue(result)
            if getattr(node, 'alias', ''):
                ctx.set(node.alias, result)
            if self.verbose:
                print(f"  [miocache.get] {key_resolved} → {result.to_python() if isinstance(result, MohioValue) else result}")
            return result
        elif op == 'delete':
            self._cache.pop(key_resolved, None)
            if self.verbose:
                print(f"  [miocache.delete] {key_resolved}")
            return MohioValue(True)
        elif op == 'flush':
            self._cache.clear()
            if self.verbose:
                print(f"  [miocache.flush]")
            return MohioValue(True)
        elif op == 'exists':
            result = key_resolved in self._cache
            return MohioValue(result)
        return MohioValue(None)

    # ── MathFuncStmt ────────────────────────────────────────────────────
    # abs / floor / ceil / round / min / max
    def _exec_MathFuncStmt(self, node, ctx):
        import math
        func = str(getattr(node, 'func', 'absolute')).lower()

        def _num(x, where=func):
            if isinstance(x, MohioValue):
                x = x.to_python()
            if isinstance(x, bool):
                raise MohioRuntimeError(f"{where}: got true/false, not a number.")
            if isinstance(x, (int, float)):
                return x
            try:
                s = str(x).strip()
                return float(s) if ('.' in s or 'e' in s.lower()) else int(s)
            except (TypeError, ValueError):
                raise MohioRuntimeError(f'{where}: expected a number, got "{x}".')

        def _numlist(v):
            raw = v.to_python() if isinstance(v, MohioValue) else v
            if isinstance(raw, (list, tuple)):
                items = list(raw)
            elif isinstance(raw, dict):
                items = list(raw.values())
            else:
                items = [raw]
            return [_num(i) for i in items]

        val = self._eval(node.value, ctx) if node.value is not None else None

        if func == 'percentage':
            # percentage X of Y -> X as a percent of Y (X / Y * 100). Guarded.
            x = _num(val)
            y = _num(self._eval(node.value2, ctx)) if getattr(node, 'value2', None) is not None else 0
            result = None if y == 0 else round(x / y * 100, 2)
        elif func in ('minimum', 'maximum', 'average', 'sum'):
            nums = _numlist(val)
            if not nums:
                result = 0 if func == 'sum' else None
            elif func == 'minimum':
                result = min(nums)
            elif func == 'maximum':
                result = max(nums)
            elif func == 'sum':
                result = sum(nums)
            else:  # average
                result = round(sum(nums) / len(nums), 4)
        else:
            n = _num(val) if val is not None else 0
            result = {
                'absolute': abs(n),
                'floor':    math.floor(n),
                'ceil':     math.ceil(n),
                'round':    round(n),
                'sqrt':     (math.sqrt(n) if n >= 0 else None),
            }.get(func, n)

        mv = MohioValue(result)
        alias = getattr(node, 'alias', '')
        if alias:
            ctx.set(alias, mv)
        return mv

    # ── HashBlock ───────────────────────────────────────────────────────
    # hash form.password as hashed using bcrypt
    def _exec_HashBlock(self, node, ctx):
        import hashlib
        value = self._eval(node.value, ctx) if getattr(node, 'value', None) is not None else MohioValue('')
        raw = str(value.to_python() if isinstance(value, MohioValue) else value)
        algo = (getattr(node, 'algorithm', None) or 'sha256').lower()
        if algo == 'bcrypt':
            try:
                import bcrypt
            except ImportError:
                raise MohioRuntimeError(
                    "hash using bcrypt requires the 'bcrypt' package. Add 'bcrypt' to "
                    "requirements, or use 'using pbkdf2' (no extra dependency).")
            hashed = bcrypt.hashpw(raw.encode(), bcrypt.gensalt()).decode()
        elif algo in ('pbkdf2', 'pbkdf2_sha256'):
            import os, binascii
            iters = 200000
            salt = os.urandom(16)
            dk = hashlib.pbkdf2_hmac('sha256', raw.encode(), salt, iters)
            hashed = 'pbkdf2_sha256$%d$%s$%s' % (
                iters, binascii.hexlify(salt).decode(), binascii.hexlify(dk).decode())
        elif algo in ('sha256', 'sha512', 'sha384', 'sha224', 'sha1', 'md5'):
            hashed = getattr(hashlib, algo)(raw.encode()).hexdigest()
        else:
            raise MohioRuntimeError(
                "hash: unknown algorithm '%s'. Use bcrypt or pbkdf2 for passwords, "
                "or sha256/sha512 for checksums." % algo)
        result = MohioValue(hashed, 'text')
        alias = getattr(node, 'alias', None)
        if alias:
            ctx.set(alias, result)
        if self.verbose:
            print(f"  [hash] {algo} applied")
        return result

    # ── EncodeStmt ──────────────────────────────────────────────────────
    # encode value as base64
    def _exec_EncodeStmt(self, node, ctx):
        import base64
        val = self._eval(node.value, ctx) if node.value else MohioValue('')
        raw = str(val.to_python() if isinstance(val, MohioValue) else val)
        fmt = str(getattr(node, 'format', 'base64')).lower()
        if fmt == 'base64':
            result = base64.b64encode(raw.encode()).decode()
        elif fmt == 'url':
            import urllib.parse
            result = urllib.parse.quote(raw)
        elif fmt == 'hex':
            result = raw.encode().hex()
        else:
            result = raw
        mv = MohioValue(result)
        alias = getattr(node, 'alias', '')
        if alias:
            ctx.set(alias, mv)
        return mv

    # ── DecodeStmt ──────────────────────────────────────────────────────
    # decode value from base64
    def _exec_DecodeStmt(self, node, ctx):
        import base64
        val = self._eval(node.value, ctx) if node.value else MohioValue('')
        raw = str(val.to_python() if isinstance(val, MohioValue) else val)
        fmt = str(getattr(node, 'format', 'base64')).lower()
        try:
            if fmt == 'base64':
                result = base64.b64decode(raw.encode()).decode()
            elif fmt == 'url':
                import urllib.parse
                result = urllib.parse.unquote(raw)
            elif fmt == 'hex':
                result = bytes.fromhex(raw).decode()
            else:
                result = raw
        except Exception:
            result = raw
        mv = MohioValue(result)
        alias = getattr(node, 'alias', '')
        if alias:
            ctx.set(alias, mv)
        return mv

    # ── ParseStmt ───────────────────────────────────────────────────────
    # parse "2026-03-31" as date
    def _exec_ParseStmt(self, node, ctx):
        val = self._eval(node.value, ctx) if node.value else MohioValue('')
        raw = str(val.to_python() if isinstance(val, MohioValue) else val)
        type_name = str(getattr(node, 'type_name', 'text')).lower()
        try:
            if type_name in ('date', 'datetime'):
                from datetime import datetime
                parsed = datetime.fromisoformat(raw)
                result = MohioValue(parsed.isoformat(), 'datetime')
            elif type_name in ('number', 'decimal', 'integer', 'int'):
                result = MohioValue(float(raw) if '.' in raw else int(raw), 'number')
            elif type_name == 'boolean':
                result = MohioValue(raw.lower() in ('true', '1', 'yes'))
            elif type_name == 'json':
                import json
                result = MohioValue(json.loads(raw))
            else:
                result = MohioValue(raw)
        except Exception as e:
            result = MohioValue(raw)
            if self.verbose:
                print(f"  [parse] could not parse as {type_name}: {e}")
        alias = getattr(node, 'alias', '')
        if alias:
            ctx.set(alias, result)
        return result

    # ── ReplaceBlock ────────────────────────────────────────────────────
    # replace in text_var / "old" with "new" / replace: done
    def _exec_ReplaceBlock(self, node, ctx):
        target_name = getattr(node, 'target', '')
        target_val  = ctx.get(target_name)
        raw = str(target_val.to_python() if isinstance(target_val, MohioValue) else '') if target_val else ''
        for entry in getattr(node, 'entries', []):
            # entry is (old, new) tuple or similar
            if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                old_v = self._eval(entry[0], ctx) if entry[0] else MohioValue('')
                new_v = self._eval(entry[1], ctx) if entry[1] else MohioValue('')
                old_s = str(old_v.to_python() if isinstance(old_v, MohioValue) else old_v)
                new_s = str(new_v.to_python() if isinstance(new_v, MohioValue) else new_v)
                raw = raw.replace(old_s, new_s)
        result = MohioValue(raw)
        alias = getattr(node, 'alias', '')
        if alias:
            ctx.set(alias, result)          # `... as NAME`: capture result; source unchanged
        elif target_name:
            ctx.set(target_name, result)    # no alias: replace in place
        return result

    # ── PrependStmt ─────────────────────────────────────────────────────
    # prepend "TXN-" to reference_number
    def _exec_PrependStmt(self, node, ctx):
        val      = self._eval(node.value, ctx) if node.value else MohioValue('')
        target   = getattr(node, 'target', '')
        existing = ctx.get(target)
        ev = existing.to_python() if isinstance(existing, MohioValue) else existing
        # List target: prepend an ELEMENT to the front.
        if isinstance(ev, list):
            elem = val.to_python() if isinstance(val, MohioValue) else val
            result = MohioValue([elem] + list(ev), 'list')
            if target:
                ctx.set(target, result)
            return result
        # String target (or unset): concat, unchanged behavior.
        prefix = str(val.to_python() if isinstance(val, MohioValue) else val)
        if existing:
            result = MohioValue(prefix + str(ev))
        else:
            result = MohioValue(prefix)
        if target:
            ctx.set(target, result)
        return result

    # ── AppendStmt ──────────────────────────────────────────────────────
    # append ".pdf" to filename
    def _exec_AppendStmt(self, node, ctx):
        val      = self._eval(node.value, ctx) if node.value else MohioValue('')
        target   = getattr(node, 'target', '')
        existing = ctx.get(target)
        ev = existing.to_python() if isinstance(existing, MohioValue) else existing
        # `add` (strict_list) is LISTS ONLY. Refuse a non-list target loudly rather than silently
        # concatenating a string -- that silent surprise is the exact class of bug this unit kills.
        # `append`/`prepend` keep their dual string/list behavior.
        if getattr(node, 'strict_list', False) and not isinstance(ev, list):
            raise MohioRuntimeError(
                f"add works on lists -- '{target}' is not a list. Use append/prepend to build a "
                f"string, or declare '{target}' with `{target} as list <type>` first.")
        # List target: append an ELEMENT (one word, one job; result shape is
        # context-driven). Stringifying a list was the bug.
        if isinstance(ev, list):
            elem = val.to_python() if isinstance(val, MohioValue) else val
            result = MohioValue(list(ev) + [elem], 'list')
            if target:
                ctx.set(target, result)
            return result
        # String target (or unset -> new string): concat, unchanged behavior.
        suffix = str(val.to_python() if isinstance(val, MohioValue) else val)
        if existing:
            result = MohioValue(str(ev) + suffix)
        else:
            result = MohioValue(suffix)
        if target:
            ctx.set(target, result)
        return result

    # ── ExtractStmt ─────────────────────────────────────────────────────
    # extract from member.email using pattern.Email as local_part
    def _exec_ExtractStmt(self, node, ctx):
        import re as re_mod
        source  = self._eval(node.source, ctx) if node.source else MohioValue('')
        raw     = str(source.to_python() if isinstance(source, MohioValue) else source)
        pattern = str(getattr(node, 'pattern', ''))
        alias   = str(getattr(node, 'alias', ''))
        # Built-in patterns
        patterns = {
            'email':    r'[\w.+-]+@[\w-]+\.[\w.-]+',
            'url':      r'https?://[^\s]+',
            'phone':    r'[\+]?[\d\s\-\(\)]{10,}',
            'number':   r'[\d]+\.?[\d]*',
            'date':     r'\d{4}-\d{2}-\d{2}',
            'zip':      r'\d{5}(?:-\d{4})?',
        }
        pat = patterns.get(pattern.lower().replace('pattern.', ''), pattern)
        try:
            match = re_mod.search(pat, raw)
            result = MohioValue(match.group(0) if match else None)
        except Exception:
            result = MohioValue(None)
        if alias:
            ctx.set(alias, result)
        return result

    # ── CheckAgainstStmt ────────────────────────────────────────────────
    # check form.password against member.hashed
    def _exec_CheckAgainstStmt(self, node, ctx):
        import hashlib
        value  = self._eval(node.value,  ctx) if node.value  else MohioValue('')
        stored = self._eval(node.stored, ctx) if node.stored else MohioValue('')
        raw_value  = str(value.to_python()  if isinstance(value,  MohioValue) else value)
        raw_stored = str(stored.to_python() if isinstance(stored, MohioValue) else stored)
        # Verify against the stored hash, detecting the scheme from its format.
        match = False
        if raw_stored.startswith('$2'):                       # bcrypt
            try:
                import bcrypt
                match = bcrypt.checkpw(raw_value.encode(), raw_stored.encode())
            except Exception:
                match = False
        elif raw_stored.startswith('pbkdf2_sha256$'):         # pbkdf2 (our format)
            try:
                import binascii
                _, iters, salt_hex, dk_hex = raw_stored.split('$')
                dk = hashlib.pbkdf2_hmac('sha256', raw_value.encode(),
                                         binascii.unhexlify(salt_hex), int(iters))
                match = (binascii.hexlify(dk).decode() == dk_hex)
            except Exception:
                match = False
        elif len(raw_stored) == 64 and all(c in '0123456789abcdef' for c in raw_stored.lower()):
            match = (hashlib.sha256(raw_value.encode()).hexdigest() == raw_stored.lower())
        else:
            match = (raw_value == raw_stored)                 # plaintext (dev only)
        handlers = getattr(node, 'body', []) or []
        if match:
            for h in handlers:
                if isinstance(h, OnSuccess):
                    return self._exec_block(h.body, ctx)
        else:
            for h in handlers:
                if isinstance(h, OnFailure):
                    return self._exec_block(h.body, ctx)
        return MohioValue(match, 'boolean')


    def _value_matches_type(self, value_py, type_name):
        """True if a Python value satisfies a declared scalar type. Used to enforce `x as int`.

        Types recognized: int/integer, dec/decimal, text/string, boolean. A numeric-looking text
        value does NOT satisfy a number type -- the whole point of the contract is that "5" (text)
        is not 5 (a number); cast explicitly if that is intended. None (an unassigned typed var)
        always satisfies its type -- the empty state is valid. Unknown types (shape refs, etc.) are
        not scalar-enforced here and pass.
        """
        if value_py is None:
            return True
        t = str(type_name).lower().split('.')[0]      # dec.2 -> dec
        if str(type_name).upper() in self._CURRENCIES:
            # a currency value is a number (rounded to the currency's places on assignment)
            return isinstance(value_py, (int, float)) and not isinstance(value_py, bool)
        if t in ('int', 'integer'):
            return isinstance(value_py, int) and not isinstance(value_py, bool)
        if t in ('dec', 'decimal'):
            return isinstance(value_py, (int, float)) and not isinstance(value_py, bool)
        if t in ('text', 'string'):
            return isinstance(value_py, str)
        if t in ('boolean', 'bool'):
            return isinstance(value_py, bool)
        return True      # non-scalar / unknown type: not enforced here

    def _exec_Assignment(self, node, ctx):
        # (Reserved statement keywords mis-parsed as an assignment target -- connect,
        # verify, find, retrieve, update, grab -- are now caught at transform time so
        # `mio check` reports them with the correct form.)
        # Three tiers: a bare variable changes freely; a `hold` value is frozen
        # until `release`; a `lock` value is permanent and never changes.
        if ctx.is_locked(node.name):
            raise MohioRuntimeError(
                f"'{node.name}' is locked and cannot be changed -- a lock is permanent.")
        if ctx.is_held(node.name):
            raise MohioRuntimeError(
                f"'{node.name}' is held and cannot be changed until released. "
                f"Use `release {node.name}` first, or `release.now {node.name} = ...` "
                f"to release and reassign in one step.")
        # `NAME as list TYPE` with no value declares an empty, growable list.
        if node.value is None:
            tn = (getattr(node, 'type_name', '') or '').lower()
            if tn == 'list' or tn.startswith('list '):
                empty = MohioValue([], 'list')
                if getattr(self, '_session_mode', False):
                    ctx.set_persistent(node.name, empty)
                else:
                    ctx.set(node.name, empty)
                if self.verbose: print(f"  [assign] {node.name} = [] ({tn})")
                return empty
            # `x as int` (no value): declare an empty typed variable. Record the contract and
            # seed the type-zero/empty value (0 / 0.0 / "" / false) -- reading it before assignment
            # returns that empty value (Ronnie's call), not an error. The contract is enforced on
            # every later assignment.
            if tn:
                ctx.declare_type(node.name, tn)
                _base = tn.split('.')[0]
                if tn.upper() in self._CURRENCIES:
                    seed = MohioValue(0.0, 'decimal')
                    seed._currency = tn.upper()
                    if getattr(self, '_session_mode', False):
                        ctx.set_persistent(node.name, seed)
                    else:
                        ctx.set(node.name, seed)
                    if self.verbose: print(f"  [declare] {node.name} as {tn} = 0.00")
                    return seed
                _zero = (0 if _base in ('int', 'integer')
                         else 0.0 if _base in ('dec', 'decimal')
                         else False if _base in ('boolean', 'bool')
                         else "" if _base in ('text', 'string')
                         else None)
                _kind = ('number' if _base in ('int', 'integer')
                         else 'decimal' if _base in ('dec', 'decimal')
                         else 'boolean' if _base in ('boolean', 'bool')
                         else 'text')
                seed = MohioValue(_zero, _kind)
                if tn.endswith('.pad') and _base in ('dec', 'decimal'):
                    _pp = tn.split('.')
                    if len(_pp) >= 2 and _pp[1].isdigit():
                        seed._pad_places = int(_pp[1])
                if getattr(self, '_session_mode', False):
                    ctx.set_persistent(node.name, seed)
                else:
                    ctx.set(node.name, seed)
                if self.verbose: print(f"  [declare] {node.name} as {tn} = {_zero!r}")
                return seed
        value = self._eval(node.value, ctx)
        value_py = value.to_python() if isinstance(value, MohioValue) else value
        # value-level fallback: `x source default Y`
        if (value_py is None or value_py == '') and getattr(node, 'default', None) is not None:
            value = self._eval(node.default, ctx)
            value_py = value.to_python() if isinstance(value, MohioValue) else value
        # ── type contract (x as int) ─────────────────────────────────────────────────────
        # A declared type is a contract on the NAME: this assignment (and every later one) must
        # satisfy it, or fail loud. `as <type>` on this line declares/reasserts the contract; an
        # earlier `x as int` is remembered on the name and enforced even when this line is bare
        # (`x "cat"`). To change the type you must `release x` (drop the contract, keep the value)
        # or `forget x` (remove the name) -- a type is never silently redefined.
        _decl_type = (getattr(node, 'type_name', None) or '').lower() or None
        _contract  = _decl_type or ctx.typed_of(node.name)
        if _contract and not self._value_matches_type(value_py, _contract):
            _what = ('text' if isinstance(value_py, str)
                     else 'a decimal' if isinstance(value_py, float)
                     else 'a boolean' if isinstance(value_py, bool)
                     else type(value_py).__name__)
            raise MohioRuntimeError(
                f"'{node.name}' is declared as {_contract}, but {value_py!r} is {_what}. "
                f"A declared type is a contract -- assign a matching value, cast it "
                f"(e.g. `{node.name} (... as.{_contract.split('.')[0]})`), or drop the contract "
                f"with `release {node.name}` (keeps the value) / `forget {node.name}` (removes it).")
        if _decl_type:
            ctx.declare_type(node.name, _decl_type)
        # dec.N precision contract: truncate the assigned value to at most N places (the contract's
        # whole purpose). dec.N.pad keeps the same stored value -- padding is a display concern,
        # applied on output, not a change to the number. A bare dec / non-dec contract is untouched.
        if _contract and value_py is not None:
            if str(_contract).upper() in self._CURRENCIES:
                # currency: ROUND half-up to the currency's places (money standard, not truncation)
                # and tag the value with its currency so it renders formatted on output.
                _cur = str(_contract).upper()
                _places = self._CURRENCIES[_cur]['places']
                _rounded = self._round_places(value_py, _places)
                value = MohioValue(_rounded, 'decimal')
                value._currency = _cur
                value_py = _rounded
            else:
                _cparts = _contract.split('.')
                _has_pad = _contract.endswith('.pad')
                if _cparts[0] in ('dec', 'decimal') and len(_cparts) >= 2 and _cparts[1].isdigit():
                    _places = int(_cparts[1])
                    _truncated = self._truncate_places(value_py, _places)
                    value = MohioValue(_truncated, 'decimal')
                    if _has_pad:
                        value._pad_places = _places
                    value_py = _truncated
        if getattr(self, '_session_mode', False):
            # Session mode — persist to session root so state survives request
            ctx.set_persistent(node.name, value)
        else:
            ctx.set(node.name, value)
        if self.verbose: print(f"  [assign] {node.name} = {value}")
        return value

    def _exec_ThenChain(self, node, ctx):
        """Sequential result-threading pipeline. The head produces a value bound
        to `it`; each `then` step runs with `it` set to the running result and
        rebinds `it` to its own result. Side-effect steps (no/None result) pass
        `it` through. A failing step raises with WHERE (line), WHAT (reason +
        which step), and HOW (fix hint) so the chain is debuggable."""
        it_val = None
        total  = len(node.steps)
        for i, step in enumerate(node.steps):
            if i > 0:
                ctx.set('it', it_val if isinstance(it_val, MohioValue)
                              else MohioValue(it_val))
            step_line = getattr(step, 'line', None) or getattr(node, 'line', None)
            try:
                ntype = type(step).__name__
                if getattr(self, f'_exec_{ntype}', None) or ntype in self._plugin_registry:
                    result = self._exec(step, ctx)
                else:
                    result = self._eval(step, ctx)
            except _GiveBack:
                raise                       # give back is control flow, not a failure
            except Exception as e:
                orig_msg  = getattr(e, 'message', None) or str(e)
                orig_hint = getattr(e, 'hint', None)
                raise _Raise(
                    error_name='then_step_failed',
                    message=(f"step {i + 1} of {total} in the 'then' pipeline failed: "
                             f"{orig_msg}"),
                    line=getattr(e, 'line', None) or step_line,
                    hint=(orig_hint or
                          "Each then-step runs on `it` (the result of the previous "
                          "step). Check that the prior step produced what this step "
                          "expects, and that every name/value this step uses is defined."))
            # Passthrough rule: a side-effect step (None/null) leaves `it` unchanged.
            if result is not None:
                rv = result.to_python() if isinstance(result, MohioValue) else result
                if rv is not None:
                    it_val = result if isinstance(result, MohioValue) else MohioValue(result)
        final = it_val if isinstance(it_val, MohioValue) else MohioValue(it_val)
        ctx.set('it', final)
        # If the head bound a name (`result X then it...`), that name should hold
        # the FINAL chain value, not just the head step's value.
        head = node.steps[0] if getattr(node, 'steps', None) else None
        head_name = getattr(head, 'name', None)
        if head_name:
            ctx.set(head_name, final)
        return final

    # ── Value Evaluation ──────────────────────────────────────

    def _eval(self, node, ctx) -> MohioValue:
        if node is None:                     return MohioValue(None)
        if isinstance(node, MohioValue):     return node
        # A comparison used as a VALUE -- `hold flag (score > 100)` -- is a boolean, and must be
        # evaluated as one. Before math_cmp was handled it arrived here as a raw Tree and the
        # permissive fallback returned its first child, so the comparison became a number.
        if isinstance(node, Condition):
            return MohioValue(bool(self._eval_condition(node, ctx)), 'boolean')
        # `total = call add with 2` -- a call in a VALUE position. (CallBlock is an alias
        # of RunBlock; only call_value puts one in a value slot, so this is unambiguous.)
        if type(node).__name__ == 'RunBlock' and getattr(node, 'task_name', None):
            return self._eval_CallBlock(node, ctx)
        if isinstance(node, bool):           return MohioValue(node, 'boolean')
        if isinstance(node, (int, float)):   return MohioValue(node, 'number')
        if isinstance(node, str):            return MohioValue(node, 'text')

        # Handle raw Lark Tree nodes — old transformer passes these through
        # Extract the first evaluatable child
        try:
            from lark import Tree as _LarkTree, Token as _LarkToken
            if isinstance(node, _LarkTree):
                # FAIL LOUD (A2 flip, 2026-07-30). A raw Lark Tree reaching the value evaluator
                # means a grammar rule has no transformer method, so no AST node was ever built
                # for it. This fallback used to evaluate the Tree's first child and return that --
                # a QUIETLY WRONG value: `(5 > 2)` became 5, `s is.not "a"` became "is s truthy".
                # It parsed, the AST looked populated, nothing errored, and the answer was wrong.
                # A file-based re-measure across the FULL suite + every example (subprocess-safe)
                # recorded ZERO rules reaching here, so nothing legitimate depends on it. Anything
                # that ever does was already silently wrong; it now names the rule and fails loud.
                _rule = str(getattr(node, 'data', '?'))
                raise MohioRuntimeError(
                    f"internal: the construct '{_rule}' reached the value evaluator with no rule "
                    f"to compute its value, so any result would be a guess. This is a compiler "
                    f"gap (a grammar rule with no transformer method), not your code -- please "
                    f"report it with the line that triggered it.")
            if isinstance(node, _LarkToken):
                t = str(node)
                if node.type == 'NUMBER': return MohioValue(float(t) if '.' in t else int(t))
                if node.type == 'STRING': return MohioValue(t.strip('"'), 'text')
                if node.type == 'NAME':   return ctx.get(t)
                return MohioValue(t)
        except ImportError:
            pass

        if isinstance(node, RandomValue):
            return self._eval(self._exec_RandomValue(node, ctx), ctx)

        if isinstance(node, Literal):
            val = node.value
            # Decode escape sequences in string literals
            # Numbers, booleans, None don't need decoding
            if isinstance(val, str) and node.literal_type in ('', 'text', None):
                try:
                    # Standard Python escape decoding:
                    # \" → "  \n → newline  \t → tab  \\ → backslash
                    val = val.encode('raw_unicode_escape').decode('unicode_escape')
                except (UnicodeDecodeError, ValueError):
                    pass  # Leave as-is if decode fails
            return MohioValue(val, node.literal_type)

        if isinstance(node, DottedName):
            # `it` is the running result of a `then` pipeline / the value derived
            # right before. It only has meaning once something has produced a value.
            # If it was never bound (no antecedent: no chain ran, nothing derived),
            # fail loud instead of resolving to None. A chain, hold, task return, or
            # find all bind it, so `give back it` after a chain still works.
            if node.parts == ['it'] and not ctx.exists('it'):
                raise MohioRuntimeError(
                    "`it` has no value here -- it refers to the result of the step "
                    "right before it, and nothing has produced a value yet. Start a "
                    "`then` pipeline (a head value followed by `then ...`), or use a "
                    "named variable instead of `it`.")
            val = ctx.get_dotted(node.parts)

            # ai.decide result fields. The block binds its NAME to the bare result, so
            # `when riskCheck is ""` keeps working. But the JSON contract also returns
            # {result, confidence, explanation}, and those had nowhere to live: reading
            # `riskCheck.confidence` walked into a boolean and came back None. The score
            # -- the whole point of a confidence gate -- was unreachable.
            #
            # The decision object is already registered under the developer's name in
            # `_ai_decisions`. Nothing needed inventing; the resolver simply never asked.
            #
            # The name is JUST a name. `isFraudulent` is not special, not reserved, not a
            # function -- do not special-case it here or anywhere.
            if (val is None or (isinstance(val, MohioValue) and val.to_python() is None)) \
                    and len(node.parts) == 2:
                _decisions = getattr(self, '_ai_decisions', None) or {}
                _d = _decisions.get(str(node.parts[0]))
                if _d is not None:
                    _field = str(node.parts[1])
                    if _field == 'result':
                        return MohioValue(_d.result)
                    if _field == 'confidence':
                        return MohioValue(_d.confidence, 'number')
                    if _field == 'explanation':
                        return MohioValue(_d.explanation or '', 'string')

            if node.parts and isinstance(val, MohioValue):
                if self._pci_fields and node.parts[-1] in self._pci_fields:
                    val.data_class = 'pci'
                if self._field_purposes and node.parts[-1] in self._field_purposes:
                    # attach collection purposes so a copy/derived value stays purpose-checkable
                    val._purposes = set(self._field_purposes[node.parts[-1]])
                    val._purpose_fields = {node.parts[-1]}
            return val

        # miocookie in a VALUE position (`check miocookie.exists "s"`). The exec handlers
        # already return a MohioValue, so a value slot just delegates to them.
        if type(node).__name__ == 'MioCookieExists':
            return self._exec_MioCookieExists(node, ctx)
        if type(node).__name__ == 'MioCookieGet':
            return self._exec_MioCookieGet(node, ctx)

        if isinstance(node, EnvRef):
            return ctx.get_env(node.key)

        if isinstance(node, SecretRef):
            return MohioValue(os.environ.get(node.key, ''), 'secret')

        if isinstance(node, DbRef):
            return MohioValue(node.table, 'table_ref')

        if isinstance(node, ShRef):
            return MohioValue(ctx.get_shape(node.shape_name), 'shape_def')

        if isinstance(node, MioaiRef):
            return MohioValue(node.method, 'mioai_ref')

        if isinstance(node, NowCall):
            return MohioValue(datetime.datetime.utcnow().isoformat(), 'datetime')

        if isinstance(node, UuidCall):
            return MohioValue(str(uuid.uuid4()), 'uuid')

        if isinstance(node, SinceExpr):
            # `since <anchor>` is a RANGE from the anchor until now. The timespan spec has it
            # only inside retrieve (`retrieve ... since last_month`), and that consumption path
            # is not built yet -- retrieve does not read a since-range. Rather than crash with an
            # internal TypeError (the old SinceExpr=TimeExpr alias) or silently evaluate to
            # nothing, refuse clearly. Wiring the range into retrieve is a design-chat item.
            raise _Raise(
                error_name='time.since_unwired',
                message=("`since` is a declared time-range form but is not yet wired into a "
                         "query in this build."),
                hint="Use an explicit comparison for now, e.g. "
                     "`where created is after now() - 30 days`. Full `since` support is tracked "
                     "for a future release.")

        if isinstance(node, TimeExpr):
            return self._eval_time(node, ctx)

        if isinstance(node, DatetimeExpr):
            return MohioValue(node.date, 'datetime')

        if isinstance(node, DurationExpr):
            return MohioValue({'count': node.count, 'unit': node.unit}, 'duration')

        if isinstance(node, MathExpr):
            return self._eval_math(node, ctx)

        if isinstance(node, TypeCastExpr):
            val  = self._eval(node.value, ctx)
            raw  = val.to_python() if isinstance(val, MohioValue) else val
            cast = node.cast_type
            if cast in ('int', 'number', 'decimal'):
                label   = _mohio_cast_label(node.value)
                default = None
                _dn = getattr(node, 'default', None)
                if _dn is not None:
                    _dv = self._eval(_dn, ctx)
                    default = _dv.to_python() if isinstance(_dv, MohioValue) else _dv
                num, kind = _mohio_coerce_number(raw, cast, default, label)
                _pl = getattr(node, 'places', None)
                if cast == 'decimal':
                    # as.dec / as.decimal: an express `.N` CAPS to at most N places by
                    # TRUNCATION -- it does NOT round. Rounding is a separate, explicitly
                    # named job (round.up / round.down / round.to N). A bare form caps at the
                    # sector's decimal limit (default 8; science raises it). Express `.N`
                    # overrides the cap. Truncation is deterministic (no float-rounding /
                    # banker's-rounding surprise): 10.45676 as.dec.2 -> 10.45, always.
                    places = int(_pl) if _pl is not None else self._decimal_cap(ctx)
                    num = self._truncate_places(num, places)
                return MohioValue(num, kind)
            label = _mohio_cast_label(node.value)
            if cast == 'string':   return MohioValue(str(raw), 'text')
            if cast == 'boolean':  return MohioValue(bool(raw), 'boolean')
            if cast == 'uc':       return MohioValue(str(raw).upper(), 'text')
            if cast == 'lc':       return MohioValue(str(raw).lower(), 'text')
            if cast == 'title':    return MohioValue(str(raw).title(), 'text')
            if cast == 'sentence':
                s = str(raw); return MohioValue(s[:1].upper() + s[1:], 'text')
            if cast == 'absolute':
                num, _k = _mohio_coerce_number(raw, 'number', None, label)
                return MohioValue(abs(num), ('decimal' if isinstance(num, float) else 'number'))
            if cast in ('days', 'hours', 'minutes', 'seconds', 'weeks'):
                where = f" for '{label}'" if label else ""
                if not isinstance(raw, str):
                    raise _Raise('coercion_error',
                                 f"as.{cast} needs a date/time value{where}, got {type(raw).__name__}.",
                                 hint="Use as." + cast + " on a stored datetime value.")
                try:
                    dt = datetime.datetime.fromisoformat(raw)
                except ValueError:
                    raise _Raise('coercion_error',
                                 f"as.{cast}: \"{raw}\" is not a valid ISO datetime{where}.",
                                 hint="Provide an ISO-8601 datetime, e.g. 2026-01-31T12:00:00.")
                secs = (datetime.datetime.utcnow() - dt).total_seconds()
                div  = {'days':86400, 'hours':3600, 'minutes':60, 'seconds':1, 'weeks':604800}[cast]
                return MohioValue(int(secs / div), 'number')
            if cast == 'json':
                import json as _json
                return MohioValue(_json.dumps(raw, default=str), 'text')
            if cast in ('csv', 'pdf', 'html'):
                raise _Raise('not_implemented',
                             f"as.{cast} is not yet implemented in this build.",
                             hint="Serialize in application logic for now; tracked for a future release.")
            # Unknown cast -> fail loud (never a silent no-op)
            raise _Raise('coercion_error',
                         f"Unknown cast as.{cast}" + (f" on '{label}'" if label else "") + ".",
                         hint="Check the cast name against the supported list.")

        if isinstance(node, RoundExpr):
            val = self._eval(node.value, ctx)
            raw = val.to_python() if isinstance(val, MohioValue) else val
            label = _mohio_cast_label(node.value)
            num, _k = _mohio_coerce_number(raw, 'number', None, label)
            import math
            if node.direction == 'up':   return MohioValue(math.ceil(num), 'number')
            if node.direction == 'down': return MohioValue(math.floor(num), 'number')
            if node.direction == 'to':
                places = int(self._eval_simple(node.places, ctx) or 2)
                return MohioValue(round(num, places), 'number')
            raise _Raise('coercion_error', f"Unknown round direction '{node.direction}'.",
                         hint="Use round.up, round.down, or round.to N.")

        if isinstance(node, StringOpExpr):
            from mohio_ast import StringOpExpr as _SOE
            op = str(getattr(node, 'operation', '')).lower()
            operand = getattr(node, 'operand', None)
            val_py = ""
            if operand is not None:
                v = self._eval(operand, ctx)
                val_py = str(v.to_python() if isinstance(v, MohioValue) else v or "")
            arg = getattr(node, 'arg', None)

            def _eval_arg():
                if arg is None: return None
                a = self._eval(arg, ctx)
                return a.to_python() if isinstance(a, MohioValue) else a

            if op == 'after':
                delim = str(_eval_arg() or '')
                idx = val_py.find(delim)
                if idx >= 0:
                    return MohioValue(val_py[idx + len(delim):].strip(), 'text')
                # Not found — use default_val if provided, else return empty string
                dv = getattr(node, 'default_val', None)
                if dv is not None:
                    dv_eval = self._eval(dv, ctx)
                    return MohioValue(str(dv_eval.to_python() if isinstance(dv_eval, MohioValue) else dv_eval or ''), 'text')
                return MohioValue('', 'text')
            if op == 'before':
                delim = str(_eval_arg() or '')
                idx = val_py.find(delim)
                if idx >= 0:
                    return MohioValue(val_py[:idx].strip(), 'text')
                # Not found — use default_val if provided, else return empty string
                dv = getattr(node, 'default_val', None)
                if dv is not None:
                    dv_eval = self._eval(dv, ctx)
                    return MohioValue(str(dv_eval.to_python() if isinstance(dv_eval, MohioValue) else dv_eval or ''), 'text')
                return MohioValue('', 'text')
            if op == 'left':
                try: n = int(_eval_arg() or 0)
                except: n = 0
                return MohioValue(val_py[:n], 'text')
            if op == 'right':
                try: n = int(_eval_arg() or 0)
                except: n = 0
                return MohioValue(val_py[-n:] if n > 0 else val_py, 'text')
            if op == 'by':
                n = _eval_arg()
                try:
                    n = int(n)
                except (TypeError, ValueError):
                    raise _Raise(error_name='type_error',
                                 message=f"`by` repeats text a whole number of times; got {n!r}.")
                return MohioValue(val_py * n, 'text')
            if op == 'uppercase':    return MohioValue(val_py.upper(), 'text')
            if op == 'lowercase':    return MohioValue(val_py.lower(), 'text')
            if op == 'as.title':     return MohioValue(val_py.title(), 'text')
            if op == 'as.sentence':  return MohioValue(val_py.capitalize(), 'text')
            if op == 'trim':         return MohioValue(val_py.strip(), 'text')
            if op == 'trim.front':   return MohioValue(val_py.lstrip(), 'text')
            if op == 'trim.back':    return MohioValue(val_py.rstrip(), 'text')
            if op == 'remove.ws':    return MohioValue(''.join(val_py.split()), 'text')
            if op in ('pad.left', 'pad.right'):
                n = getattr(node, 'arg', 0) or 0
                fill = str(getattr(node, 'default_val', None) or ' ')[:1] or ' '
                padded = val_py.rjust(n, fill) if op == 'pad.left' else val_py.ljust(n, fill)
                return MohioValue(padded, 'text')
            if op == 'remove.special':
                import re as _re
                return MohioValue(_re.sub(r'[^a-zA-Z0-9\s]', '', val_py), 'text')
            if op == 'remove.html':
                import re as _re
                return MohioValue(_re.sub(r'<[^>]+>', '', val_py), 'text')
            if op == 'truncate.to':
                n = getattr(node, 'arg', None)
                try: n = int(n) if n is not None else 35
                except (TypeError, ValueError): n = 35
                unit = (getattr(node, 'direction', '') or 'characters').lower()
                if unit == 'words':
                    words = val_py.split()
                    return MohioValue(' '.join(words[:n]), 'text')
                return MohioValue(val_py[:n], 'text')
            if op == 'mask.all':
                keep = getattr(node, 'arg', None)
                direction = (getattr(node, 'direction', '') or 'last').lower()
                if keep is None:
                    raise _Raise('mask_missing_count',
                        "mask.all needs a keep count, e.g. mask.all except last 4.",
                        line=getattr(node, 'line', None),
                        hint="Write: card_number mask.all except last 4")
                try:
                    keep = int(keep)
                except (TypeError, ValueError):
                    keep = 0
                s = str(val_py)
                n = len(s)
                if keep <= 0:
                    return MohioValue('*' * n, 'text')
                if keep >= n:
                    # keep count covers the whole string -> nothing to mask
                    return MohioValue(s, 'text')
                if direction == 'first':
                    return MohioValue(s[:keep] + '*' * (n - keep), 'text')
                # default: keep the LAST `keep` characters
                return MohioValue('*' * (n - keep) + s[-keep:], 'text')
            return MohioValue(val_py, 'text')

        if hasattr(node, '__class__') and node.__class__.__name__ == 'ConcatExpr':
            from mohio_ast import ConcatExpr
            parts = []
            pci_taint = False
            purpose_taint = None
            purpose_fields = None
            for term in (node.terms or []):
                v = self._eval(term, ctx)
                py = v.to_python() if isinstance(v, MohioValue) else v
                # a currency-tagged value renders formatted ($1,234.56); a dec.N.pad value renders
                # zero-filled; everything else uses normal text. Raw numeric value stays for math.
                parts.append(self._display_text(v))
                if isinstance(v, MohioValue):
                    if getattr(v, 'data_class', None) == 'pci':
                        pci_taint = True
                    vp = getattr(v, '_purposes', None)
                    if vp:
                        purpose_taint = set(vp) if purpose_taint is None else (purpose_taint & set(vp))
                        pf = getattr(v, '_purpose_fields', None)
                        if pf:
                            purpose_fields = set(pf) if purpose_fields is None else (purpose_fields | set(pf))
            result = MohioValue("".join(parts), 'text')  # always returns string, never None
            if purpose_taint is not None:
                result._purposes = purpose_taint
                if purpose_fields:
                    result._purpose_fields = purpose_fields
            if pci_taint:
                # Taint (option B, 2026-07-07): a string built from a [pci] value carries the
                # class, so it is masked on ANY display -- show AND give back -- not only when the
                # field is referenced directly. A PAN cannot leak by being concatenated into a
                # shown or returned string. The value stays full at rest and for internal use; to
                # use the full number, pass the RAW value to a use channel, never a built string.
                # NOTE: revisitable later (a show-masks / give-back-is-a-use split), tracked in docs.
                result.data_class = 'pci'
            return result

        if isinstance(node, ColorLit):
            return MohioValue(node.value, 'color')

        if isinstance(node, PercentLit):
            return MohioValue(node.value, 'percent')

        if isinstance(node, DimensionLit):
            return MohioValue(node.value, 'dimension')

        if isinstance(node, TemplateString):
            return MohioValue(self._interpolate(node.template, ctx), 'text')

        if isinstance(node, ListLiteral):
            items = [self._eval_simple(i, ctx) for i in node.items]
            return MohioValue(items, 'list')

        if isinstance(node, TimePeriodExpr):
            return MohioValue(str(node), 'time_period')

        # Fallback — wrap as-is
        return MohioValue(node)

    def _eval_simple(self, node, ctx):
        v = self._eval(node, ctx)
        return v.to_python() if isinstance(v, MohioValue) else v

    def _eval_math(self, node, ctx):
        left  = self._eval(node.left, ctx)
        right = self._eval(node.right, ctx)
        op    = node.op
        # Unwrap MohioValue to Python primitives
        lv = left.to_python()  if isinstance(left,  MohioValue) else left
        rv = right.to_python() if isinstance(right, MohioValue) else right
        # Currency guard: you cannot mix two different currencies in one operation (USD + EUR has no
        # meaning without a conversion rate). Same-currency math is fine; currency + plain number is
        # fine (e.g. price + tax_amount). The result carries the currency so it keeps formatting.
        _lc = getattr(left, '_currency', None) if isinstance(left, MohioValue) else None
        _rc = getattr(right, '_currency', None) if isinstance(right, MohioValue) else None
        if _lc and _rc and _lc != _rc and op in ('+', '-', '*', '/', '%'):
            raise _Raise(
                error_name='currency_mismatch',
                message=(f"Cannot do math across currencies ({_lc} {op} {_rc}). Convert one to the "
                         f"other first -- there is no fixed rate between them."),
                line=getattr(node, 'line', None))
        _result_currency = _lc or _rc
        try:
            # Math is math. `+ - * / %` never operate on text: `&` is the join operator,
            # and a numeric-looking string must be cast explicitly (`as.number`, `as.int`).
            # This used to silently concatenate ("5" + 2 -> "52") and silently repeat
            # ("5" * 2 -> "55"), which is the worst failure we can ship: every value off a
            # request is text, so `(price + tax)` on form fields quietly produced "1020".
            if op in ('+', '-', '*', '/', '%') and (isinstance(lv, str) or isinstance(rv, str)):
                _txt = lv if isinstance(lv, str) else rv
                _hint = ("Use `&` to join text (`a & b`)."
                         if op == '+' else
                         "Math needs numbers on both sides.")
                raise _Raise(
                    error_name='math_error',
                    message=(f"Cannot do math on text ({op}). The value {_txt!r} is text, "
                             f"not a number. {_hint} If it holds a number, cast it first: "
                             f"`{_txt!r} as.number` (or `as.int`)."))
            if op in ('+', '-', '*', '/', '%'):
                _r = (left + right if op == '+' else
                      left - right if op == '-' else
                      left * right if op == '*' else
                      left / right if op == '/' else
                      left % right)
                if _result_currency and op in ('+', '-'):
                    # +/- keep the currency (money +/- money or +/- a number is still that money).
                    # * and / typically produce a plain number (rate, ratio), so they drop the tag.
                    _rv = _r.to_python() if isinstance(_r, MohioValue) else _r
                    _tagged = MohioValue(_rv, 'decimal')
                    _tagged._currency = _result_currency
                    return _tagged
                return _r
            if op == '>':  return MohioValue(left.to_python() > right.to_python(), 'boolean')
            if op == '<':  return MohioValue(left.to_python() < right.to_python(), 'boolean')
            if op == '>=': return MohioValue(left.to_python() >= right.to_python(), 'boolean')
            if op == '<=': return MohioValue(left.to_python() <= right.to_python(), 'boolean')
            if op in ('==', '='):
                return MohioValue(left.to_python() == right.to_python(), 'boolean')
            if op == '!=':
                return MohioValue(left.to_python() != right.to_python(), 'boolean')
        except ZeroDivisionError:
            raise _Raise(error_name='division_by_zero',
                         message="Cannot divide by zero.",
                         line=getattr(node, 'line', None))
        except TypeError:
            line = getattr(node, 'line', None)
            def _what(v):
                if v is None: return "a missing value"
                if isinstance(v, str):
                    return f'text "{v}"' if len(str(v)) <= 24 else "text"
                if isinstance(v, (int, float)): return "a number"
                return f"a {type(v).__name__} value"
            # Text context -> string_error (a text operand was involved)
            if isinstance(lv, str) or isinstance(rv, str):
                other = rv if isinstance(lv, str) else lv
                raise _Raise(error_name='string_error',
                             message=f"Cannot combine text with {_what(other)} using '{op}'.",
                             line=line,
                             hint="Use & to join text, convert with as.string, or supply a default for a missing value.")
            # Pure math context -> name the offending operand. A missing value here is
            # almost always a bug; Mohio does not silently treat it as 0.
            offenders = [(s, v) for s, v in (("left", lv), ("right", rv))
                         if not isinstance(v, (int, float))]
            side, badv = offenders[0] if offenders else ("left", lv)
            raise _Raise(error_name='math_error',
                         message=f"Cannot do math on {_what(badv)} ({side} side of '{op}').",
                         line=line,
                         hint="Convert it first with as.number, declare a default at the source, or guard it with 'is empty'.")
        except Exception as e:
            line = getattr(node, 'line', None)
            if op in ('+', '-', '*', '/', '%'):
                raise _Raise(error_name='math_error', message=str(e), line=line,
                             hint="Convert operands with as.number, or guard missing values.")
            raise _Raise(error_name='eval_error', message=str(e), line=line)
        return MohioValue(None)

    # ── time-range primitive (Section 1, 2026-08-01) ────────────────────────────────────
    # A calendar/rolling PERIOD resolves to a half-open interval [start, end) of timezone-aware
    # datetimes, anchored in the app's declared timezone. Used by `is.in <period>` (Section 2).
    # Rulings: week starts Monday; quarters are REAL calendar quarters (Q1=Jan-Mar ...); month/
    # quarter/year are calendar-bound; `last N <unit>` is a rolling window from now. An unknown
    # period NEVER resolves to a guess -- it fails loud.

    def _resolve_app_tz(self):
        """The app's declared timezone: app config `timezone "X"` -> env MOHIO_APP_TIMEZONE -> UTC.
        UTC always works; a named IANA zone needs the tzdata database (fails loud if absent)."""
        name = (getattr(self, '_app_tz_name', None)
                or os.environ.get('MOHIO_APP_TIMEZONE') or 'UTC').strip()
        if name.upper() in ('UTC', 'Z', ''):
            return datetime.timezone.utc
        try:
            import zoneinfo
            return zoneinfo.ZoneInfo(name)
        except Exception:
            raise MohioRuntimeError(
                f"app timezone '{name}' could not be loaded. UTC always works; a named IANA zone "
                f"(e.g. 'America/New_York') needs the tzdata database -- add `tzdata` to "
                f"requirements, or set the app timezone to UTC.")

    def _app_now(self):
        return datetime.datetime.now(self._resolve_app_tz())

    @staticmethod
    def _shift_months(dtv, n):
        """dtv is a first-of-month aware datetime; return first-of-(month+n), tz preserved."""
        m0 = dtv.year * 12 + (dtv.month - 1) + n
        return dtv.replace(year=m0 // 12, month=(m0 % 12) + 1, day=1)

    def _period_range(self, period, now=None):
        """(start, end) half-open [start, end) aware datetimes for a CALENDAR period name.
        When `now` is a tz-aware datetime its tzinfo is used (so callers can pin the zone);
        otherwise the app timezone. Raises MohioRuntimeError for an unrecognized period."""
        if now is not None and now.tzinfo is not None:
            tz = now.tzinfo
        else:
            tz = self._resolve_app_tz()
            now = datetime.datetime.now(tz)
        today = now.date()
        def ds(d):   # midnight (day start) in tz
            return datetime.datetime(d.year, d.month, d.day, tzinfo=tz)
        day = datetime.timedelta(days=1)
        if period == 'today':      s = ds(today);       return s, s + day
        if period == 'yesterday':  s = ds(today - day); return s, s + day
        if period == 'this_week':
            mon = today - datetime.timedelta(days=today.weekday())
            return ds(mon), ds(mon) + datetime.timedelta(days=7)
        if period == 'last_week':
            mon = today - datetime.timedelta(days=today.weekday())
            return ds(mon - datetime.timedelta(days=7)), ds(mon)
        if period == 'this_month':
            s = ds(today.replace(day=1));  return s, self._shift_months(s, 1)
        if period == 'last_month':
            s = ds(today.replace(day=1));  return self._shift_months(s, -1), s
        if period == 'this_quarter':
            qf = 3 * ((today.month - 1) // 3) + 1
            s = datetime.datetime(today.year, qf, 1, tzinfo=tz);  return s, self._shift_months(s, 3)
        if period == 'last_quarter':
            qf = 3 * ((today.month - 1) // 3) + 1
            tq = datetime.datetime(today.year, qf, 1, tzinfo=tz); return self._shift_months(tq, -3), tq
        if period == 'this_year':
            return (datetime.datetime(today.year, 1, 1, tzinfo=tz),
                    datetime.datetime(today.year + 1, 1, 1, tzinfo=tz))
        if period == 'last_year':
            return (datetime.datetime(today.year - 1, 1, 1, tzinfo=tz),
                    datetime.datetime(today.year, 1, 1, tzinfo=tz))
        raise MohioRuntimeError(
            f"'{period}' is not a recognized time period. Use today, yesterday, this/last week, "
            f"this/last month, this/last quarter, this/last year, or `last N <unit>`.")

    def _rolling_range(self, count, unit, now=None):
        """(start, end) for a ROLLING window `last N <unit>`: [now - N units, now)."""
        if now is None or now.tzinfo is None:
            now = self._app_now()
        delta = self._to_timedelta(int(count or 0), unit)
        return now - delta, now

    def _timeperiod_range(self, tp):
        """Resolve a TimePeriod AST node to a half-open (start, end) of aware datetimes:
        calendar periods via _period_range (Ruling 1), rolling windows via _rolling_range."""
        if getattr(tp, 'calendar', None):
            return self._period_range(tp.calendar)
        roll = getattr(tp, 'rolling', None)
        if roll is not None:
            return self._rolling_range(roll.count, roll.unit)
        raise MohioRuntimeError(
            "internal: a TimePeriod carries neither a calendar period nor a rolling window.")

    def _resolve_declared_timespan(self, name):
        """Resolve a declared `timespan NAME` to a half-open (start_iso, end_iso). `start` and
        `end`/`until` are the declared date anchors; the window is [start, end). (2026-08-01: the
        find-filter use of a declared timespan; the block was previously stored and never read.)"""
        ts = (getattr(self, '_timespans', None) or {}).get(name)
        if ts is None:
            raise MohioRuntimeError(
                f"timespan '{name}' is not declared. Declare it first with "
                f"`timespan {name} / start <date> / end <date> / timespan: done`, then reference "
                f"it in a find with `timespan {name}`.")
        start = end = None
        for b in (getattr(ts, 'body', None) or []):
            if type(b).__name__ == 'TimespanAnchor':
                de = getattr(b, 'datetime_expr', None)
                raw = getattr(de, 'date', None) if (de is not None and hasattr(de, 'date')) else de
                dt = self._parse_datetime(raw)
                if b.anchor_type == 'start':
                    start = dt
                elif b.anchor_type in ('end', 'until'):
                    end = dt
        if start is None or end is None:
            raise MohioRuntimeError(
                f"timespan '{name}' needs both a `start` and an `end` (or `until`) date to filter a "
                f"query (it has start={start is not None}, end={end is not None}).")
        if end <= start:
            raise MohioRuntimeError(
                f"timespan '{name}' has end ({end.date()}) on or before start ({start.date()}); the "
                f"window would be empty. It is a half-open [start, end) range.")
        return start.isoformat(), end.isoformat()

    def _eval_time(self, node, ctx):
        now = datetime.datetime.utcnow()
        today = datetime.date.today()
        base = node.base

        # Resolve the BASE to a date/datetime first, then apply any offset to it. Offsets used to
        # apply only to now(); the datetime word family (since/from/after/before/newer/older) needs
        # `last_month - 1 day` style modifiers on ANY anchor, so offset is applied uniformly here.
        if base in ('now()', 'now'):
            resolved = now
        elif base == 'today':
            resolved = today
        elif base == 'yesterday':
            resolved = today - datetime.timedelta(days=1)
        elif base == 'last_week':
            resolved = today - datetime.timedelta(days=7)
        elif base == 'last_month':
            # first day of last month (start of the period) -- "since last_month" means from the
            # start of last month onward, which is the useful reading.
            first_this = today.replace(day=1)
            resolved = (first_this - datetime.timedelta(days=1)).replace(day=1)
        elif base == 'last_quarter':
            # Ruling 1 (2026-08-01): the actual previous CALENDAR quarter's start, not 90 rolling
            # days -- consistent with `last_month` above (first day of last month).
            _q_first = 3 * ((today.month - 1) // 3) + 1
            _m = today.year * 12 + (_q_first - 1) - 3
            resolved = today.replace(year=_m // 12, month=(_m % 12) + 1, day=1)
        elif base == 'last_year':
            resolved = today.replace(year=today.year - 1, month=1, day=1)
        elif base == 'this_week':
            resolved = today - datetime.timedelta(days=today.weekday())
        elif base == 'this_month':
            resolved = today.replace(day=1)
        elif base == 'this_quarter':
            _q_first_month = 3 * ((today.month - 1) // 3) + 1
            resolved = today.replace(month=_q_first_month, day=1)
        elif base == 'this_year':
            resolved = today.replace(month=1, day=1)
        else:
            # Never silently resolve an unknown anchor to `now` -- that turned a typo or an
            # unhandled time word into "the current instant" with no error (2026-08-01 fail-loud).
            raise MohioRuntimeError(
                f"'{base}' is not a time anchor this build can resolve. Use now(), today, "
                f"yesterday, this/last week|month|quarter|year, or since <point>.",
                line=getattr(node, 'line', None))

        # Apply an offset (e.g. `last_month - 1 day`) to whatever the base resolved to.
        if node.offset and node.offset_op:
            dur = node.offset
            delta = self._to_timedelta(int(dur.count or 0), dur.unit)
            if isinstance(resolved, datetime.datetime):
                resolved = resolved - delta if node.offset_op == '-' else resolved + delta
            else:
                resolved = resolved - delta if node.offset_op == '-' else resolved + delta

        kind = 'datetime' if isinstance(resolved, datetime.datetime) else 'date'
        return MohioValue(resolved.isoformat(), kind)

    def _to_timedelta(self, count, unit):
        unit = str(unit).lower().rstrip('s')
        if unit in ('second', 'sec'): return datetime.timedelta(seconds=count)
        if unit in ('minute', 'min'): return datetime.timedelta(minutes=count)
        if unit in ('hour',):         return datetime.timedelta(hours=count)
        if unit in ('day',):          return datetime.timedelta(days=count)
        if unit in ('week',):         return datetime.timedelta(weeks=count)
        if unit in ('month',):        return datetime.timedelta(days=count * 30)
        if unit in ('year',):         return datetime.timedelta(days=count * 365)
        return datetime.timedelta()

    def _interpolate(self, template, ctx):
        def replace(m):
            expr  = m.group(1).strip()
            parts = expr.split('.')
            val   = ctx.get_dotted(parts)
            return str(val.to_python() if isinstance(val, MohioValue) else val)
        return re.sub(r'\{\{([^}]+)\}\}', replace, str(template))

    def _interpolate_output(self, template, ctx, line=None, escape=False):
        """Render {{ var }} for user-facing output (show / give back / render).

        Double braces are RESERVED for one job: insert a variable's value.
        An unknown variable (typically a typo) fails loud with WHERE/WHAT/HOW
        rather than silently rendering 'None'. A defined-but-null variable
        renders as empty string.

        When escape=True (the render view block), the interpolated VALUE is
        HTML-escaped (text/attribute context) while the developer's literal markup
        is left untouched -- auto-escape untrusted data, trust authored HTML. This
        is what closes XSS in the view layer."""
        from html import escape as _html_escape
        text = str(template)
        if "{{" not in text:
            return text
        def replace(m):
            expr  = m.group(1).strip()
            directive = self._render_form_directive(expr, ctx, line)
            if directive is not None:
                return directive  # trusted Mohio-generated markup, not escaped
            parts = expr.split('.')
            root  = parts[0]
            if not ctx.exists(root):
                raise _Raise(
                    error_name='unknown_variable',
                    message=f"{{{{ {expr} }}}} refers to an unknown variable '{root}'.",
                    line=line,
                    hint=(f"Define it before use (e.g. hold {root} = \"...\"), or check the "
                          f"spelling. Double braces {{ }} are reserved only for inserting a "
                          f"variable's value."))
            val = ctx.get_dotted(parts)
            out = self._display_text(val)
            if not escape:
                return out
            # URL-scheme allowlist in href/src attribute context (S8.2 XSS, 2026-08-01). HTML-escaping
            # alone does NOT stop `javascript:` / `data:` URLs. In a URL attribute, only http/https/
            # mailto (or a scheme-less relative URL) pass; any other scheme is stripped. Control/space
            # are removed first the way browsers do, so `java\tscript:` cannot slip through.
            if re.search(r'\b(?:href|src|action|formaction|poster|background|xlink:href)\s*=\s*["\']?[^"\'<>]*$',
                         text[:m.start()], re.I):
                _norm = re.sub(r'[\x00-\x20]+', '', out).lower()
                _sm = re.match(r'([a-z][a-z0-9+.\-]*):', _norm)
                if _sm and _sm.group(1) not in ('http', 'https', 'mailto'):
                    out = ''   # dangerous/unknown URL scheme -> stripped
            return _html_escape(out, quote=True)
        return re.sub(r'\{\{([^}]+)\}\}', replace, text)

    def _maybe_interpolate(self, val, ctx, line=None, escape=False):
        """Apply output interpolation if the evaluated value is a string that
        carries {{ }}. Leaves non-strings and plain strings untouched. When escape=True the
        interpolated VALUES are HTML-escaped (authored markup untouched) -- the XSS-safe default
        for give back."""
        py = val.to_python() if isinstance(val, MohioValue) else val
        if isinstance(py, str) and "{{" in py:
            interp = self._interpolate_output(py, ctx, line, escape=escape)
            if isinstance(val, MohioValue):
                return MohioValue(interp, getattr(val, 'type', 'string'))
            return interp
        return val

    # ── Form rendering directives ({{ form }}, {{ field }}, {{ guard }}) ──
    # These are output-interpolation directives, not new grammar. They read a
    # shape (whose fields now survive into the AST) and emit HTML. The structural
    # markup is trusted Mohio output; the data parts (labels, options, defaults)
    # are HTML-escaped, so authored data cannot inject markup (XSS closed here too).

    _FORM_INPUT_TYPE = {
        'text': 'text', 'email': 'email', 'number': 'number', 'integer': 'number',
        'decimal': 'number', 'date': 'date', 'time': 'time', 'datetime': 'datetime-local',
        'url': 'url',
    }

    # The nine accept groups (catalog ruling, 2026-07-22). A group keyword expands to
    # its extension list before the allowlist check, and a comma-separated list unions
    # them, so `accept images, pdf, csv` means the image types plus those two.
    # `all` is deliberately NOT a group -- it fails loud at check.
    _ACCEPT_GROUPS = {
        'images':        ('jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'tiff'),
        'documents':     ('pdf', 'docx', 'txt', 'rtf', 'odt'),
        'spreadsheets':  ('xlsx', 'csv', 'tsv', 'ods'),
        'presentations': ('pptx', 'odp'),
        'archives':      ('zip', 'tar', 'gz', '7z'),
        'audio':         ('mp3', 'wav', 'ogg', 'm4a', 'flac'),
        'video':         ('mp4', 'webm', 'mov', 'avi', 'mkv'),
    }
    _ACCEPT_GROUPS['media'] = (_ACCEPT_GROUPS['images'] + _ACCEPT_GROUPS['audio']
                               + _ACCEPT_GROUPS['video'])
    _ACCEPT_GROUPS['office'] = (_ACCEPT_GROUPS['documents']
                                + _ACCEPT_GROUPS['spreadsheets']
                                + _ACCEPT_GROUPS['presentations'])

    @classmethod
    def resolve_accept_list(cls, entries):
        """Expand group keywords to extensions, leaving explicit extensions as written.

        Order is preserved and duplicates dropped, so a refusal message reads back the
        way the author wrote it. An unknown word is left alone and treated as an
        extension, which is the long-standing behaviour.
        """
        out = []
        for raw in (entries or []):
            w = str(raw).strip().strip('"').lstrip('.').lower()
            if not w:
                continue
            for e in cls._ACCEPT_GROUPS.get(w, (w,)):
                if e not in out:
                    out.append(e)
        return out

    def _shape_field_props(self, field):
        props = {'name': field.name, 'type': (getattr(field, 'type_name', None) or 'text'),
                 'required': False, 'label': None, 'default': None, 'options': None,
                 'format': None, 'error': None, 'min': None, 'max': None, 'matches': None,
                 'multiline': False, 'multiple': False, 'range': None,
                 'accept': None, 'maxsize': None, 'pattern': None}
        props['is_upload'] = props['type'] in ('file', 'image', 'audio', 'video', 'pdf')
        for m in (getattr(field, 'modifiers', None) or []):
            mt = getattr(m, 'modifier_type', '')
            if mt == 'required':  props['required'] = True
            elif mt == 'multiline': props['multiline'] = True
            elif mt == 'multiple':  props['multiple'] = True
            elif mt == 'range':     props['range'] = m.value
            elif mt == 'accept':    props['accept'] = self.resolve_accept_list(m.value)
            elif mt == 'maxsize':   props['maxsize'] = m.value
            elif mt == 'pattern':   props['pattern'] = m.value
            elif mt == 'label':   props['label'] = m.value
            elif mt == 'default': props['default'] = m.value
            elif mt == 'allowed': props['options'] = m.value
            elif mt == 'format':  props['format'] = m.value
            elif mt == 'error':   props['error'] = m.value
            elif mt == 'min':     props['min'] = m.value
            elif mt == 'max':     props['max'] = m.value
            elif mt == 'matches': props['matches'] = m.value
            elif mt == 'minmax':
                try: props['min'], props['max'] = m.value
                except Exception: pass
        return props

    def _store_uploads(self, shape, data):
        """Write validated uploads to MOHIO_UPLOAD_DIR with a random, sanitized
        filename (the submitted name never touches the path, so a crafted name
        cannot escape the directory). Returns {field_name: stored_path}."""
        import os, re as _re, uuid
        out = {}
        updir = os.environ.get('MOHIO_UPLOAD_DIR', './uploads')
        for field in (getattr(shape, 'fields', None) or []):
            p = self._shape_field_props(field)
            if not p.get('is_upload'):
                continue
            fd = data.get(p['name']) if isinstance(data, dict) else None
            if not (isinstance(fd, dict) and fd.get('_mohio_file') and fd.get('filename')):
                continue
            content = fd.get('content')
            if content is None:
                continue
            ext = _re.sub(r'[^a-z0-9]', '', (fd.get('ext') or '').lower().lstrip('.'))[:10]
            # An uploaded page is the one upload that can attack whoever opens it
            # later, so it is cleaned before it lands and only the clean version is
            # written. The original is never stored. If the sanitizer is missing this
            # raises rather than falling back to writing the file as uploaded.
            from mohio_html_sanitize import SANITIZED_EXTENSIONS, sanitize_html
            if ext in SANITIZED_EXTENSIONS:
                raw = (content.decode('utf-8', errors='replace')
                       if isinstance(content, (bytes, bytearray)) else str(content))
                content = sanitize_html(raw).encode('utf-8')
            fname = uuid.uuid4().hex + (('.' + ext) if ext else '')
            os.makedirs(updir, exist_ok=True)
            full = os.path.join(updir, fname)
            with open(full, 'wb') as fh:
                fh.write(content if isinstance(content, (bytes, bytearray))
                         else str(content).encode('utf-8'))
            out[p['name']] = full
        return out

    _EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    # Types an upload field may never accept, checked BEFORE the field's own
    # `accept` list so widening `accept` can never opt back in. `accept` is
    # mandatory on every upload field, so the allowlist is the primary control
    # and this is the backstop under it: it stops someone writing `accept exe`.
    #
    # It is an extension check, so a renamed file defeats it. The controls that
    # do not depend on the name are the mandatory allowlist, the mandatory max
    # size, and storing uploads under a random UUID name so a submitted name
    # never reaches the path.
    #
    # pdf is deliberately ALLOWED -- it is a normal business upload. A PDF can
    # carry script, so the mitigation is serving it as an attachment rather than
    # inline, not refusing it.
    _DANGEROUS_UPLOAD_EXT = {
        # Windows executables and installers
        'exe', 'com', 'scr', 'msi', 'msp', 'dll', 'cpl', 'msc', 'appx', 'msix',
        'gadget', 'pif', 'lnk', 'scf', 'inf', 'chm', 'jnlp',
        # Windows shell and scripting
        'bat', 'cmd', 'ps1', 'ps2', 'psc1', 'psc2', 'vbs', 'vbe', 'js', 'jse',
        'wsf', 'wsh', 'sct', 'hta', 'reg',
        # Macro-enabled Office documents and add-ins. The plain formats
        # (docx, xlsx, pptx) cannot carry macros and stay allowed.
        'docm', 'dotm', 'xlsm', 'xltm', 'xlam', 'xlsb', 'pptm', 'potm', 'ppam',
        'ppsm', 'sldm', 'xll', 'wll',
        # Java
        'jar', 'class', 'war',
        # Server-side scripts -- an uploaded one becomes remote code execution
        # the moment anything serves the upload directory.
        'php', 'phtml', 'php3', 'php4', 'php5', 'phar', 'asp', 'aspx', 'jsp',
        'jspx', 'cgi', 'pl',
        # macOS, Linux, mobile executables and packages
        'app', 'dmg', 'pkg', 'command', 'deb', 'rpm', 'run', 'out', 'elf',
        'so', 'dylib', 'apk', 'ipa', 'sh', 'bash', 'zsh', 'csh', 'ksh',
        # Disk images
        'iso', 'img', 'vhd', 'vhdx',
        # Ruled permanently blocked (catalog, 2026-07-22).
        # svg carries script and becomes stored cross-site scripting the moment it is
        # served inline. doc/xls/ppt are the legacy Office formats and can carry
        # macros; the modern docx/xlsx/pptx cannot, and stay allowed.
        'svg', 'doc', 'xls', 'ppt',
        # py/rb are paid-tier only and are not available in the free compiler. An
        # uploaded script is remote code execution the moment anything runs it, so
        # the free tier refuses rather than warns.
        'py', 'rb',
    }

    def _validate_against_shape(self, shape, get_value):
        """Validate submitted values against a shape's field rules. Returns an
        ordered dict {field_name: message} with the first failure per field.
        Defaults cover required / email / allowed; a field's `error "..."`
        modifier overrides the default for any failure on that field."""
        errors = {}
        for field in (getattr(shape, 'fields', None) or []):
            p = self._shape_field_props(field)
            name = p['name']
            raw = get_value(name)
            val = raw.to_python() if isinstance(raw, MohioValue) else raw
            custom = p.get('error')
            disp = p['label'] or name.replace('_', ' ').title()

            # file upload: value is a descriptor dict from the multipart parse
            if p.get('is_upload'):
                fd = val if isinstance(val, dict) and val.get('_mohio_file') else None
                if not fd or not fd.get('filename'):
                    if p['required']:
                        errors[name] = custom or f"{disp} is required."
                    continue
                ext = (fd.get('ext') or '').lower().lstrip('.')
                if ext in self._DANGEROUS_UPLOAD_EXT:
                    errors[name] = custom or f"{ext} files are not allowed."
                    continue
                if p.get('accept') and ext not in [e.lower() for e in p['accept']]:
                    errors[name] = custom or f"{disp} must be one of: {', '.join(p['accept'])}."
                    continue
                mx = p.get('maxsize')
                if mx is not None and (fd.get('size') or 0) > mx:
                    errors[name] = custom or f"{disp} must be {mx / (1024 * 1024):g} MB or smaller."
                    continue
                continue

            # multi-value field (allowed + multiple): the value is a list
            if p.get('multiple'):
                items = (val if isinstance(val, list)
                         else ([] if val in (None, '') else [val]))
                items = [str(x).strip() for x in items if str(x).strip() != '']
                if p['required'] and not items:
                    errors[name] = custom or f"{disp} is required."
                    continue
                if p['options']:
                    allowed = [str(o) for o in p['options']]
                    if any(x not in allowed for x in items):
                        errors[name] = custom or f"Choose from: {', '.join(allowed)}."
                        continue
                continue

            sval = '' if val is None else str(val).strip()
            if p['required'] and sval == '':
                errors[name] = custom or f"{disp} is required."
                continue
            if sval == '':
                continue  # optional and empty: nothing else to check
            if (p.get('format') == 'email' or p['type'] == 'email') and not self._EMAIL_RE.match(sval):
                errors[name] = custom or "Enter a valid email address."
                continue
            if p['type'] in ('number', 'decimal', 'integer', 'int') or p.get('format') == 'number':
                try:
                    float(sval)
                except (ValueError, TypeError):
                    errors[name] = custom or f"{disp} must be a number."
                    continue
            if p['options'] and sval not in [str(o) for o in p['options']]:
                errors[name] = custom or f"Choose one of: {', '.join(str(o) for o in p['options'])}."
                continue
            mn, mx = p.get('min'), p.get('max')
            is_numeric = (p['type'] in ('number', 'decimal', 'integer', 'int')
                          or p.get('format') == 'number')
            def _bnd(x):
                try:
                    fx = float(x)
                    return f"{int(fx)}" if fx.is_integer() else f"{fx:g}"
                except (ValueError, TypeError):
                    return str(x)
            if is_numeric:
                # For numeric fields min/max are VALUE bounds, not string length.
                try:
                    numv = float(sval)
                except (ValueError, TypeError):
                    numv = None
                if numv is not None and mn is not None and numv < float(mn):
                    errors[name] = custom or f"{disp} must be at least {_bnd(mn)}."
                    continue
                if numv is not None and mx is not None and numv > float(mx):
                    errors[name] = custom or f"{disp} must be {_bnd(mx)} or less."
                    continue
            else:
                # For text fields min/max are string-length bounds.
                if mn is not None and len(sval) < mn:
                    errors[name] = custom or f"{disp} must be at least {mn} characters."
                    continue
                if mx is not None and len(sval) > mx:
                    errors[name] = custom or f"{disp} must be {mx} characters or fewer."
                    continue
            rng = p.get('range')
            if rng:
                try:
                    num = float(sval)
                except (ValueError, TypeError):
                    errors[name] = custom or f"{disp} must be a number."
                    continue
                try:
                    lo, hi = float(rng[0]), float(rng[1])
                    if num < lo or num > hi:
                        errors[name] = custom or f"{disp} must be between {rng[0]} and {rng[1]}."
                        continue
                except (ValueError, TypeError, IndexError):
                    pass
            pat = p.get('pattern')
            if pat:
                try:
                    ok = re.fullmatch(pat, sval) is not None
                except re.error:
                    raise _Raise(error_name='bad_pattern', line=None,
                        message=f"Field '{name}' has an invalid pattern: {pat}",
                        hint="Fix the regular expression in the shape's pattern rule.")
                if not ok:
                    errors[name] = custom or f"{disp} is not in the expected format."
                    continue
            other = p.get('matches')
            if other:
                oraw = get_value(other)
                oval = oraw.to_python() if isinstance(oraw, MohioValue) else oraw
                osval = '' if oval is None else str(oval).strip()
                if sval != osval:
                    other_disp = other.replace('_', ' ').title()
                    errors[name] = custom or f"{disp} must match {other_disp}."
                    continue
        return errors

    def _render_one_field(self, field, esc, value=None, error=None):
        p = self._shape_field_props(field)
        name = p['name']
        label = esc(p['label'] or name.replace('_', ' ').title())
        req = ' required' if p['required'] else ''
        cur = p['default'] if value is None else value   # submitted value wins (no blank restart)
        err_html = f'<small class="mohio-error">{esc(error)}</small>' if error else ''
        fmt = p.get('format')

        # file upload -> <input type="file"> with an accept hint
        if p.get('is_upload'):
            accept_attr = ''
            if p.get('accept'):
                accept_attr = ' accept="' + ','.join('.' + e for e in p['accept']) + '"'
            ctrl = (f'<input type="file" name="{esc(name)}" id="{esc(name)}"'
                    f'{accept_attr}{req}>')
            return (f'<div class="mohio-field"><label for="{esc(name)}">{label}</label>'
                    f'{ctrl}{err_html}</div>')

        # multi-line text -> textarea
        if p.get('multiline') and not p['options'] and p['type'] not in ('boolean', 'bool'):
            txt = esc(cur) if cur is not None and str(cur) != '' else ''
            ctrl = f'<textarea name="{esc(name)}" id="{esc(name)}"{req}>{txt}</textarea>'
            return (f'<div class="mohio-field"><label for="{esc(name)}">{label}</label>'
                    f'{ctrl}{err_html}</div>')

        if p['options']:
            # multi-value (allowed + multiple): checkbox group by default,
            # `format "select"` for a multi-select. Submitted values pre-check.
            if p.get('multiple'):
                chosen = (cur if isinstance(cur, list)
                          else ([] if cur in (None, '') else [cur]))
                chosen = {str(x) for x in chosen}
                if fmt == 'select':
                    opts = ''.join(
                        f'<option value="{esc(o)}"'
                        f'{" selected" if str(o) in chosen else ""}>{esc(o)}</option>'
                        for o in p['options'])
                    ctrl = f'<select name="{esc(name)}" id="{esc(name)}" multiple{req}>{opts}</select>'
                    return (f'<div class="mohio-field"><label for="{esc(name)}">{label}</label>'
                            f'{ctrl}{err_html}</div>')
                boxes = ''.join(
                    f'<label class="mohio-check">'
                    f'<input type="checkbox" name="{esc(name)}" value="{esc(o)}"'
                    f'{" checked" if str(o) in chosen else ""}> {esc(o)}</label>'
                    for o in p['options'])
                return (f'<div class="mohio-field">'
                        f'<span class="mohio-label">{label}</span>{boxes}{err_html}</div>')
            # a `format "radio"` allowed field renders as radio buttons
            if fmt == 'radio':
                radios = ''.join(
                    f'<label class="mohio-radio">'
                    f'<input type="radio" name="{esc(name)}" value="{esc(o)}"'
                    f'{" checked" if str(o) == str(cur) else ""}{req}> {esc(o)}</label>'
                    for o in p['options'])
                return (f'<div class="mohio-field">'
                        f'<span class="mohio-label">{label}</span>{radios}{err_html}</div>')
            opts = ''.join(
                f'<option value="{esc(o)}"'
                f'{" selected" if str(o) == str(cur) else ""}>{esc(o)}</option>'
                for o in p['options'])
            ctrl = f'<select name="{esc(name)}" id="{esc(name)}"{req}>{opts}</select>'
            return (f'<div class="mohio-field"><label for="{esc(name)}">{label}</label>'
                    f'{ctrl}{err_html}</div>')
        if p['type'] in ('boolean', 'bool'):
            chk = ' checked' if str(cur).lower() in ('true', 'on', '1', 'yes') else ''
            return (f'<div class="mohio-field mohio-check">'
                    f'<input type="checkbox" name="{esc(name)}" id="{esc(name)}"{chk}{req}>'
                    f'<label for="{esc(name)}">{label}</label>{err_html}</div>')
        it = self._FORM_INPUT_TYPE.get(p['type'], 'text')
        if fmt == 'email':
            it = 'email'
        elif fmt == 'password':
            it = 'password'
        elif fmt in ('tel', 'phone'):
            it = 'tel'
        elif fmt == 'url':
            it = 'url'
        if it == 'password':
            cur = None   # never echo a password back into a re-rendered field
        val = f' value="{esc(cur)}"' if cur is not None and str(cur) != '' else ''
        ctrl = f'<input type="{it}" name="{esc(name)}" id="{esc(name)}"{val}{req}>'
        return f'<div class="mohio-field"><label for="{esc(name)}">{label}</label>{ctrl}{err_html}</div>'

    def _csrf_secret_bytes(self):
        s = getattr(self, '_csrf_secret', None)
        if s is None:
            import os, secrets
            env = os.environ.get('MOHIO_SECRET')
            if env:
                s = env.encode('utf-8')
            else:
                s = secrets.token_bytes(32)
                if getattr(self, 'verbose', False):
                    print("  [guard] MOHIO_SECRET not set; using a per-process key "
                          "(form tokens reset on restart). Set MOHIO_SECRET in production.")
            self._csrf_secret = s
        return s

    def _issue_csrf(self):
        import hmac, hashlib, time
        ts = str(int(time.time()))
        sig = hmac.new(self._csrf_secret_bytes(), ts.encode('utf-8'),
                       hashlib.sha256).hexdigest()
        return f"{ts}.{sig}"

    _CSRF_MAX_AGE = 7200  # two hours

    def _verify_csrf(self, token):
        import hmac, hashlib, time
        if not token or '.' not in str(token):
            return False
        ts, sig = str(token).split('.', 1)
        if not ts.isdigit():
            return False
        expected = hmac.new(self._csrf_secret_bytes(), ts.encode('utf-8'),
                            hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return False
        return (int(time.time()) - int(ts)) <= self._CSRF_MAX_AGE

    def _render_guard(self):
        # CSRF token (signed, timestamped) plus a honeypot. The verify side runs
        # in the submit path for real browser form posts.
        token = self._issue_csrf()
        return (f'<input type="hidden" name="_csrf" value="{token}">'
                f'<input type="text" name="_trap" tabindex="-1" autocomplete="off" '
                f'aria-hidden="true" style="position:absolute;left:-9999px">')

    def _render_form_directive(self, expr, ctx, line=None):
        """Return rendered HTML for a {{ form }} / {{ field }} / {{ guard }}
        directive, or None if expr is an ordinary interpolation. On a re-render
        after a failed submit, inputs are pre-filled from the bound instance and
        the per-field error is shown (no blank restart)."""
        from html import escape as _esc
        esc = lambda s: _esc('' if s is None else str(s), quote=True)
        e = expr.strip()

        if e == 'guard':
            return self._render_guard()

        if e.startswith('form ') or e.startswith('field '):
            is_field = e.startswith('field ')
            ref = e[6:].strip() if is_field else e[5:].strip()
            body = ref[3:] if ref.startswith('sh.') else ref
            if is_field:
                if '.' not in body:
                    raise _Raise(error_name='bad_field_ref', line=line,
                        message=f"{{{{ {e} }}}} must name a shape and a field, "
                                f"like field sh.Signup.email.",
                        hint="Write field sh.<Shape>.<field>.")
                sname, fname = body.split('.', 1)
            else:
                sname, fname = body, None
            shape = ctx.get_shape(sname)
            if shape is None:
                raise _Raise(error_name='unknown_shape', line=line,
                    message=f"{{{{ {e} }}}} refers to a shape '{sname}' that is not defined.",
                    hint=f"Declare it with `shape {sname} ... shape: done` before rendering it.")
            fields = getattr(shape, 'fields', None) or []

            # Submitted values (for re-render) come from a bound instance of this
            # shape; errors come from the gate's per-field map. Both are absent on
            # a fresh render, so the form draws empty.
            shapevar = sname[0].lower() + sname[1:] if sname else sname
            inst = None
            try:
                iv = ctx.get(shapevar)
                inst = iv.to_python() if isinstance(iv, MohioValue) else iv
            except Exception:
                inst = None
            def fval(fn):
                if isinstance(inst, dict):
                    return inst.get(fn)
                return None
            errs = getattr(self, '_form_errors', None) or {}

            if is_field:
                field = next((f for f in fields if f.name == fname), None)
                if field is None:
                    raise _Raise(error_name='unknown_field', line=line,
                        message=f"{{{{ {e} }}}}: shape '{sname}' has no field '{fname}'.",
                        hint=f"Fields on {sname}: " + ", ".join(f.name for f in fields) + ".")
                return self._render_one_field(field, esc, value=fval(fname), error=errs.get(fname))
            body_html = ''.join(
                self._render_one_field(f, esc, value=fval(f.name), error=errs.get(f.name))
                for f in fields)
            guard = self._render_guard()
            has_upload = any(self._shape_field_props(f).get('is_upload') for f in fields)
            enc = ' enctype="multipart/form-data"' if has_upload else ''
            return (f'<form method="post" action=""{enc} class="mohio-form">'
                    f'{body_html}{guard}'
                    f'<button type="submit">Submit</button></form>')

        return None

    def _render_invalid_form(self, shape_name, ctx):
        """Re-render the form after a failed submit, values kept and errors shown,
        wrapped in a minimal page. No blank restart, no handler run."""
        form = self._render_form_directive(f'form sh.{shape_name}', ctx)
        return ('<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">'
                '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
                f'</head><body>{form}</body></html>')

    # ── Condition Evaluation ──────────────────────────────────

    def _eval_condition(self, node, ctx):
        if node is None: return False

        if isinstance(node, Condition):
            lv = self._eval_simple(node.left, ctx)
            rv = self._eval_simple(node.right, ctx)
            op = node.op
            # Silent-false trap: `X is foo` where `foo` is an unquoted word that is NOT a declared
            # variable compared X to None and silently returned false (the same shape as the
            # `is even` bug). Fail loud instead, pointing at the fix. Only a single bare name is
            # caught -- a field access (obj.field) may legitimately resolve to None.
            if op in ('is', '==', 'is not', '!='):
                _r = node.right
                if type(_r).__name__ == 'DottedName' and len(getattr(_r, 'parts', [])) == 1 \
                        and not ctx.exists(_r.parts[0]):
                    _nm = _r.parts[0]
                    raise MohioRuntimeError(
                        f"unknown name '{_nm}' in this condition -- nothing by that name is "
                        f"declared, so `is {_nm}` would silently be false. Quote it for the text "
                        f"(\"{_nm}\"), declare it, or use a predicate like `is even` / `is empty`.")
            if op in ('is', '=='):     return lv == rv
            if op in ('is not', '!='): return lv != rv
            # Parity: `n is even` / `n is odd`. A unary predicate on a WHOLE number. Anything that
            # is not a whole number (a fraction, or non-numeric) fails loud -- parity of a
            # non-integer is not a false answer, it is a bad question, and answering it silently
            # false is exactly the bug this fixes.
            if op in ('even', 'odd'):
                pv = lv.to_python() if isinstance(lv, MohioValue) else lv
                try:
                    f = float(pv)
                except (TypeError, ValueError):
                    raise MohioRuntimeError(
                        f"`is {op}` needs a whole number, but got {pv!r}.")
                if f != int(f):
                    raise MohioRuntimeError(
                        f"`is {op}` needs a whole number, but got {pv!r} (it has a fraction).")
                is_even = (int(f) % 2 == 0)
                return is_even if op == 'even' else not is_even
            # Empty is not the same question as truthy, and conflating them is how `not.empty n`
            # answered false for n = 0. Empty means absent or zero-length; 0 is neither.
            if op == 'is.empty':       return _mohio_is_empty(lv)
            if op == 'not.empty':      return not _mohio_is_empty(lv)
            # Numeric comparisons: coerce both sides when they look numeric so
            # text like "10" compares against the number 5 without a TypeError.
            def _n(x):
                if isinstance(x, bool):
                    return x
                try:
                    return float(x)
                except (TypeError, ValueError):
                    return x
            a, b = _n(lv), _n(rv)
            try:
                if op in ('>', 'above'):  return a > b
                if op in ('<', 'below'):  return a < b
                if op == '>=':            return a >= b
                if op == '<=':            return a <= b
            except TypeError:
                # A comparison between two incomparable values (e.g. non-numeric text
                # against a number) used to silently answer False -- same "answered a
                # question it did not understand" shape as the unknown-operator
                # fallthrough just below. Fail loud and name both sides instead of
                # guessing false.
                raise MohioRuntimeError(
                    f"`{op}` cannot compare {lv!r} and {rv!r} -- they are not comparable "
                    f"values, so this condition cannot be answered (it would otherwise "
                    f"be silently false).")
            if op == 'contains': return str(rv) in str(lv)
            if op == 'starts':   return str(lv or '').startswith(str(rv))
            if op == 'ends':     return str(lv or '').endswith(str(rv))
            # Operator dispatch fell through: `op` is a comparison the evaluator does not
            # implement. Returning False here silently answered a question it did not
            # understand -- e.g. `(x = y)` (single `=`, which the grammar accepts via
            # MATH_CMP_OP but this dispatcher never handled) was ALWAYS false regardless of
            # the values. Fail loud and name the operator instead of guessing false.
            raise MohioRuntimeError(
                f"`{op}` is not a comparison operator Mohio evaluates here, so this "
                f"condition cannot be answered (it would otherwise be silently false)."
                + (" Use `is` or `==` for equality." if op == '='
                   else " Supported: is, is not, above/below, more/less than, is even/odd, "
                        "is empty/not empty, contains, starts, ends."))

        if isinstance(node, NotCondition):
            return not self._eval_condition(node.condition, ctx)

        if isinstance(node, AndCondition):
            return (self._eval_condition(node.left, ctx) and
                    self._eval_condition(node.right, ctx))

        if isinstance(node, OrCondition):
            return (self._eval_condition(node.left, ctx) or
                    self._eval_condition(node.right, ctx))

        if isinstance(node, DotStateCheck):
            raw = self._eval_simple(node.value, ctx)
            if node.prefix == 'is':
                if node.state == 'empty':  return not bool(raw)
                if node.state == 'true':   return raw is True or raw == 'true'
                if node.state == 'false':  return raw is False or raw == 'false'
                return bool(raw)
            if node.prefix == 'not':
                if node.state == 'empty': return bool(raw)
                return not bool(raw)

        # Literal true/false in condition
        if isinstance(node, Literal):
            return bool(node.value)

        # Truthy eval
        val = self._eval(node, ctx)
        return bool(val)

    def _apply_check_op(self, raw, op, val):
        """Apply a subject-less check op (contains/starts/ends/minlen/maxlen) --
        the tuple leaf ('cond', (op, val)) built for the implicit-subject
        `when contains "x"` form -- against the check subject `raw`."""
        s = raw.to_python() if isinstance(raw, MohioValue) else raw
        s = "" if s is None else str(s)
        if op == 'contains': return str(val) in s
        if op == 'starts':   return s.startswith(str(val))
        if op == 'ends':     return s.endswith(str(val))
        try:
            if op == 'minlen': return len(s) >= int(val)
            if op == 'maxlen': return len(s) <= int(val)
        except (TypeError, ValueError):
            return False
        return False

    def _eval_check_compound(self, node, raw, ctx):
        """Evaluate a compound check/when clause (and/or/not) while threading the
        check subject `raw`. Subject-less leaves (contains/starts/ends, stored as
        tuples) apply to the checked value; explicit-subject leaves (Condition,
        DotStateCheck, wc_ trees) evaluate normally. Fixes `when A and B`/`A or B`."""
        t = type(node).__name__
        if t == 'AndCondition':
            return (self._eval_check_compound(node.left, raw, ctx) and
                    self._eval_check_compound(node.right, raw, ctx))
        if t == 'OrCondition':
            return (self._eval_check_compound(node.left, raw, ctx) or
                    self._eval_check_compound(node.right, raw, ctx))
        if t == 'NotCondition':
            return not self._eval_check_compound(node.condition, raw, ctx)
        if isinstance(node, tuple) and len(node) == 2 and node[0] == 'cond':
            op, val = node[1]
            return self._apply_check_op(raw, op, val)
        if t == 'Tree' and str(getattr(node, 'data', '')).startswith('wc_'):
            return self._match_where_condition(node, raw, ctx)
        if t in ('Condition', 'DotStateCheck'):
            return bool(self._eval_condition(node, ctx))
        try:
            v = self._eval(node, ctx)
            vv = v.to_python() if isinstance(v, MohioValue) else v
            return self._loose_eq(raw, vv)
        except Exception:
            return False

    # ── Helpers ───────────────────────────────────────────────

    def _resolve_source(self, source, ctx):
        if isinstance(source, DbRef):        table = source.table
        elif isinstance(source, DottedName): table = source.parts[-1] if source.parts else None
        elif isinstance(source, str):
            s = source.strip()               # strip "db." prefix if present -- table name only
            table = s[3:] if s.startswith('db.') else s
        elif source:
            table = str(source)
        else:
            table = None
        # An empty resolution means the table name was lost between parse and execution. The old
        # fallback invented the name "unknown" and wrote there -- the same silent-plausible-
        # default class as Workstream A, and it turned a resolution bug into silent data loss
        # (rows landing in an `unknown` table with no error on SQLite). Never do that.
        if not table or not str(table).strip():
            raise MohioRuntimeError(
                "could not resolve the table for this operation -- the source name was lost "
                "between parse and execution. Refusing to write to an invented table. "
                "This is a compiler bug; please report the operation that triggered it.")
        return table

    def _db_or_fail(self, ctx, verb, node):
        """The ONE door for "this verb needs a database".

        A missing connection is a FAILURE. It is never an empty result, and it is never
        nothing at all. Before this existed, 11 of the 15 database verbs degraded quietly
        when no connection was open:

            save / save.or.update / update  ->  `if not db: return None`   the write was
                                                DISCARDED and the program reported success
            pull                            ->  returned an empty list, i.e. it LIED and
                                                said the source had no rows
            grab                            ->  bound None
            sql                             ->  printed only under --verbose
            transaction                     ->  ran the body with NO transaction, silently
                                                non-atomic
            cm.purge                        ->  the right-to-be-forgotten verb SILENTLY
                                                DID NOT PURGE

        Meanwhile `remove`, `remove.all` and `save.all` raised. Same situation, opposite
        behaviour: that inconsistency is drift, not design.

        The design already settles it. In the two-stage verb block, `on.failure` means IT
        BROKE -- an error, NO CONNECTION, a timeout. So: route to on.failure if the block
        has one, otherwise fail loud. Never continue as if the work was done.

        Returns (db, early_result). If db is None the caller returns early_result at once.
        """
        db = ctx.get_connection('db')
        if db:
            return db, None
        handlers = getattr(node, 'handlers', None) or []
        if any(isinstance(h, OnFailure) for h in handlers):
            return None, self._handle_failure(handlers, ctx, "no database connection")
        raise _Raise(
            error_name='no_db_connection',
            message=f"`{verb}` needs a database connection, but none is open.",
            line=getattr(node, 'line', None),
            hint="Declare one first, e.g. 'connect db as sqlite from env.DATABASE_URL'. "
                 "Without it this would have done nothing at all, and said nothing.")

    def _handle_failure(self, handlers, ctx, reason):
        for h in handlers:
            if isinstance(h, OnFailure):
                if self.verbose: print(f"  [on.failure] {reason}")
                try:
                    return self._exec_block(h.body, ctx)
                except _GiveBack: raise
        # on.failure did not fire, so `otherwise` is the final fallback (design spec).
        return self._handle_otherwise(handlers, ctx)

    def _handle_success(self, handlers, ctx):
        """The NON-FAILURE path of a verb block. Two stages, in order.

        DESIGN (Ronnie):
          STATE      on.failure / on.success -- did it break? on.failure fires and EXITS the
                     block, so nothing here runs on the failure path.
          CONDITION  when / otherwise -- what came back? A post-result conditional set. You
                     cannot condition on something you do not have yet, so it runs last.

        `on.failure` is a gate, not a branch of the set, which is why `when` never needs to be
        wrapped in an `otherwise` to protect it: the failure path already left. One set per
        block, `otherwise` last.
        """
        # STAGE 1 -- state. It did not break.
        for h in handlers or []:
            if isinstance(h, OnSuccess):
                self._exec_block(h.body, ctx)
                break

        # STAGE 2 -- conditions on the result. Same engine `check` uses: the conditions are
        # self-contained (`when t is empty`), so there is no implicit subject.
        return self._run_conditional_set(handlers, ctx)

    def _run_conditional_set(self, handlers, ctx):
        """`when` ... `when` ... `otherwise` -- first match wins, otherwise is the fallback."""
        from mohio_ast import CheckWhen
        whens = [h for h in (handlers or []) if isinstance(h, CheckWhen)]
        for when in whens:
            _wv = getattr(when, 'value', None)
            fired = False
            if type(_wv).__name__ == 'Tree' and str(getattr(_wv, 'data', '')).startswith('wc_'):
                fired = self._match_where_condition(_wv, None, ctx)
            elif type(_wv).__name__ == 'Condition':
                fired = self._eval_condition(_wv, ctx)
            elif type(_wv).__name__ in ('AndCondition', 'OrCondition', 'NotCondition'):
                fired = self._eval_check_compound(_wv, None, ctx)
            else:
                val = self._eval(_wv, ctx) if _wv is not None else None
                fired = bool(val.to_python() if isinstance(val, MohioValue) else val)
            if fired:
                return self._exec_block(when.body, ctx)
        # No `when` matched -- `otherwise` is the fallback of the set.
        return self._handle_otherwise(handlers, ctx)

    def _handle_otherwise(self, handlers, ctx):
        from mohio_ast import OtherwiseClause
        for h in handlers or []:
            if isinstance(h, OtherwiseClause):
                if self.verbose: print("  [otherwise] no handler fired -- running fallback")
                return self._exec_block(h.body, ctx)
        return None

    def _format_response(self, gb):
        raw = gb.value
        raw = raw.to_python() if isinstance(raw, MohioValue) else raw
        resp = {'status': gb.status or 200, 'body': raw}
        # Map the `as FORMAT` cast to a content-type so the response builder serves
        # xml/text raw instead of defaulting to JSON. Bare give-backs stay unset and
        # keep their existing behavior (markup sniffed as HTML, else JSON).
        _ct = {'json': 'application/json', 'xml': 'application/xml',
               'text': 'text/plain', 'html': 'text/html'}.get(getattr(gb, 'fmt', None))
        if not _ct:
            _ct = getattr(self, '_default_content_type', None)  # `respond as ...` default
        if _ct:
            resp['content_type'] = _ct
        _dl = getattr(gb, 'download', None)
        if _dl:
            # The browser must save this instead of displaying it. Serving a file
            # inline runs it inside this site's own origin, which is how an uploaded
            # page or PDF gets to read the viewer's session.
            resp['download'] = _dl
            import mimetypes as _mt
            resp['content_type'] = (_mt.guess_type(_dl)[0]
                                    or 'application/octet-stream')
        return resp

    def run_with_session(self, program, request, session_id, store):
        self._ai_call_count = 0   # reset per request
        self._saga_failed = False  # reset saga-failure flag per request
        self.shown = []           # reset show output per request (no cross-request leak)
        import time as _time
        self._run_deadline = (_time.monotonic() + self._run_seconds_limit) if self._run_seconds_limit > 0 else None
        """
        Run with persistent session context.
        The session context child survives between requests —
        variables set in one request are available in the next.
        Used for stateful apps like Zork.

        `store` is a session-store object (in-memory by default; Postgres-backed when
        MOHIO_SESSION_STORE=postgres, or whatever a registered provider returns -- see
        MohioInterpreter.register_session_store_provider) exposing get/put/delete/
        sweep_expired/is_invalidated/mark_invalidated. This method still owns every piece
        of session MECHANICS (mint, invalidate-on-presentation, lazy expiry, the
        opportunistic sweep) exactly as before the store became pluggable; only WHERE the
        bytes live changed.
        """
        from mohio_interpreter import Context, MohioValue
        import uuid as _uuid
        now = _time.time()

        with MohioInterpreter._SESSIONS_LOCK:
            # __base__ (shared declarations: shapes/tasks/connects) lives on the
            # interpreter instance, never in the pluggable store -- rebuilt from source on
            # every cold start, exactly as before this seam existed (see __init__).
            if self._base_ctx is None:
                base_ctx = Context()
                # Run declarations only (shapes, tasks, holds, connects)
                self._exec_declarations(program, base_ctx)
                self._register_ai_blocks(program)
                self._base_ctx = base_ctx
            base_ctx = self._base_ctx

            idle_ceiling, absolute_ceiling = self._session_timeout_ceilings(base_ctx)

            # `session_id` must be settled (never falsy) BEFORE either the invalidation or
            # the store lookup below -- this used to run in the other order, so a caller
            # that reached here with an unset id (dispatch() always pre-mints, but this
            # method is also called directly in tests) would look up/create an entry keyed
            # by None first and only mint afterward, binding session_ctx to the WRONG
            # (bogus) entry. Fixed alongside adding the invalidation check, which has the
            # same ordering requirement.
            if not session_id:
                session_id = _uuid.uuid4().hex

            # A presented ID that was deliberately invalidated -- rotated away by a real
            # privilege change, or expired -- must never be silently resurrected. Treat it
            # exactly like no session was presented at all: mint a fresh, anonymous one.
            # This is what makes "the old ID stops working immediately" true even for the
            # attacker's own next request, not just eventually. Ruled durable (2026-08-05):
            # the blocklist survives a restart on a Postgres-backed store, same as the
            # session data it protects.
            if store.is_invalidated(session_id):
                session_id = _uuid.uuid4().hex

            # Lazy expiry: checked on ACCESS, not by a background sweep (see
            # _opportunistic_expiry_sweep for why). An existing session that has gone idle
            # too long, or lived past its absolute ceiling, is invalidated right here,
            # before it is ever handed back to the program as "the" session. One store
            # lookup covers both this check and the get-or-create below -- no double
            # round trip to a durable backend for the same session_id.
            session_ctx = store.get(session_id, base_ctx)
            if session_ctx is not None and self._session_is_expired(
                    session_ctx, now, idle_ceiling, absolute_ceiling):
                self._invalidate_session(store, session_id)
                session_id = _uuid.uuid4().hex
                session_ctx = None

            # Bounded scan for OTHER stale sessions, piggybacked on this request+lock
            # rather than a dedicated background task.
            self._opportunistic_expiry_sweep(store, now, idle_ceiling, absolute_ceiling)

            # Get or create session context
            _is_new = session_ctx is None
            if _is_new:
                session_ctx = base_ctx.child()
                session_ctx._created_at = now
                # Registered immediately, not only at the end of the request: a brand-new
                # session that hits an unhandled exception before _attach_cookies runs
                # still exists afterward, matching the in-memory dict's original semantics
                # (sessions[session_id] = ... registered the entry the instant it was
                # created, regardless of what happened for the rest of the request).
                store.put(session_id, session_ctx)

            # Mark the per-session boundary so session-mode assignments persist here
            # (per session, survives across requests) instead of the shared base.
            session_ctx._session_root = True
            # Runtime-owned identity bookkeeping (2026-08-04): lets _rotate_session find
            # its way back to this exact store/id pair from inside _exec_GrantRoleDecl,
            # and lets the lazy expiry check above find this session's own clock next time.
            session_ctx._session_id = session_id
            session_ctx._sessions_store = store
            session_ctx._last_accessed = now
            if not hasattr(session_ctx, '_created_at'):
                session_ctx._created_at = now

        # Flag session mode so _exec_new_listener writes to session ctx directly
        self._session_mode = True

        # Set request on session context
        if request:
            session_ctx._current_request = request
            # Auth rebuild Item 1 (2026-08-02): do NOT re-read roles from the client
            # `_roles` payload each request -- that was the forgeable path, now removed.
            # Roles established by `grant role` live on this session_ctx (the session root)
            # and PERSIST across requests precisely because nothing overwrites them here.
            clean = {k: v for k, v in request.items() if not k.startswith("_")}
            session_ctx.set("request", MohioValue(clean, "shape"))
            for k, v in clean.items():
                session_ctx.set(k, MohioValue(v))
            # `clean` drops every key starting with "_", and the request cookies live under
            # __request_cookies__ -- so they were stripped right here. miocookie.exists/get read
            # them off ctx, so on this path they saw NO cookies at all and every visit looked
            # like a first visit. Put them back.
            session_ctx.set('__request_cookies__',
                            MohioValue(request.get('__request_cookies__', {}) or {}, 'shape'))

        # Set session as a shape with id field -- enables session.id in .mho code.
        # session_id is already settled and non-empty by this point (see the lock block
        # above): dispatch() pre-mints, the invalidated-id check re-mints if needed, and
        # the fallback mint at the top of the lock block covers any direct caller that
        # didn't. `session.id` means "the id of the current session"; a mid-request
        # rotation (see _rotate_session) updates this same key again, later, on the
        # (re-keyed) session_ctx -- so a read of session.id after `grant role` sees the
        # new identity, not the one this line set.
        session_ctx.set("session", MohioValue({"id": session_id}, "shape"))

        def _attach_cookies(result):
            """miocookie.set writes __pending_cookies__ onto ctx for any OTHER app cookie;
            the SERVER reads it off the RESULT. mio_session itself is no longer written
            that way at all (2026-08-04: it is reserved, miocookie.set on it fails loud) --
            the runtime emits it here, unconditionally, on every response that reaches this
            point, reading whatever session.id ended up being after the listener ran (a
            rotation during the request changes it; this must reflect the FINAL identity,
            not the one the request started with).

            Also the one funnel every return path in this method passes through (success,
            halt, give-back, raise/authorization/jump, MohioRuntimeError) -- so it is where
            the request's final session state gets written back to the store. For the
            in-memory default this is a cheap, effectively-redundant dict write (session_ctx
            was already the live object); for a durable store it is what actually persists
            everything the request just did (hold-in-session values, role grants, the
            updated _last_accessed clock) before the response goes out."""
            pending = session_ctx.get('__pending_cookies__')
            pend = (pending.to_python() if isinstance(pending, MohioValue) else pending) if pending else {}
            if not isinstance(pend, dict):
                pend = {}
            _cur = session_ctx.get('session')
            _cur = (_cur.to_python() if isinstance(_cur, MohioValue) else _cur) or {}
            _final_sid = _cur.get('id') if isinstance(_cur, dict) else None
            pend[_SESSION_COOKIE_NAME] = self._session_cookie_opts(
                _final_sid or session_id, idle_ceiling, absolute_ceiling,
                getattr(session_ctx, '_created_at', now), now)
            if isinstance(result, dict):
                result['__pending_cookies__'] = pend
            store.put(_final_sid or session_id, session_ctx)
            return result

        # Run listener blocks only (not declarations again)
        try:
            return _attach_cookies(self._exec_listeners(program, session_ctx))
        except Exception as e:
            from mohio_interpreter import _GiveBack, _Halt, _Raise, _Jump
            if isinstance(e, _Halt):
                return _attach_cookies({"status": 200, "body": "halted"})
            if isinstance(e, _GiveBack):
                return _attach_cookies(self._format_response(e))
            if isinstance(e, _Raise):
                # Same reasoning as the MohioRuntimeError branch just below: a failed
                # request must not silently drop the session cookie either. Previously
                # inconsistent with that branch for no stated reason -- fixed alongside
                # this unit since a fresh session's very first request commonly fails a
                # require-role check before ever succeeding (an anonymous visitor hitting
                # a protected route), and that request's minted id still deserves to reach
                # the browser so the NEXT request (after a real login) can find it again.
                if e.error_name == "authorization_error":
                    return _attach_cookies({"status": 403, "body": str(e.message)})
                return _attach_cookies({"status": 500, "body": str(e)})
            if isinstance(e, _Jump):
                # Same session-cookie reasoning as the two branches above -- a redirect
                # (the shape a real login-then-redirect flow would use) must not silently
                # drop a same-request rotation's cookie either. NOT touched here: this
                # branch's missing `redirect_to` key and its 302 (vs. the plain run()
                # path's 303) are a separate, pre-existing inconsistency, out of scope for
                # this unit -- named, not fixed.
                return _attach_cookies({"status": 302, "body": str(e.destination)})
            # A MohioRuntimeError (e.g. re-holding an already-held value, a failed contract) is a
            # real, deliberate fail-loud from the program. It should surface as a clean 500 response
            # for this request -- not crash the server or take down the session -- mirroring how the
            # non-session serve path and _route_program handle runtime errors.
            if isinstance(e, MohioRuntimeError):
                return _attach_cookies({"status": 500, "body": str(e)})
            raise

    def _register_ai_blocks(self, program):
        """Store every ai.decide block by name so a bare `ai.decide <name>`
        invocation can run it. Top-level definitions are otherwise skipped
        (neither declaration nor listener), so without this they are
        unreachable by name -- which is the bug that made Zork's self-healing
        noun resolver silently no-op (and then fail loud)."""
        from mohio_ast import AiDecideBlock
        if not hasattr(self, '_ai_blocks'):
            self._ai_blocks = {}
        def walk(stmts):
            for s in stmts or []:
                if isinstance(s, AiDecideBlock):
                    self._ai_blocks[s.name] = s
                for attr in ('statements', 'body'):
                    kids = getattr(s, attr, None)
                    if isinstance(kids, list):
                        walk(kids)
                for L in (getattr(s, 'listeners', None) or []):
                    walk(getattr(L, 'body', None) or [])
        walk(getattr(program, 'statements', []))

    def _exec_declarations(self, program, ctx):
        """Run only declaration statements — shapes, tasks, holds, connects.
        Descends one level into a top-level journey so the journey's scope
        declarations register into the shared context (pages inherit them)."""
        from mohio_ast import (SectorDecl, ConnectDecl, ShapeDecl, TaskDecl,
                                HoldDecl, LockDecl, ComplianceDecl, SecurityDecl, IncludeDecl,
                                MioconnectDecl, MiosearchDecl, MiovalidateDecl, MiofileDecl,
                                PatternDecl, TimespanDecl, RateLimitDecl, LoadPackDecl,
                                MioScheduleDecl, JourneyDecl, AiRankBlock)
        # A top-level `ai.rank name ... ai.rank: done` is a declaration: it ranks a
        # fixed set of options (a top-level rank has no request in scope, so its
        # options are static) and binds the winner into app scope. Running it here
        # means it computes ONCE when the base context is built and every handler
        # inherits the bound name -- the same way `hold` is visible everywhere.
        # Without this it was neither declaration nor listener, so it never ran on
        # the serve path and `give back ok <name>` silently produced an empty body.
        # ai.decide / ai.compare are intentionally NOT here: they depend on request
        # inputs and must run per-request inside a handler.
        #
        # Note: plain top-level assignments and top-level check/when blocks are NOT
        # run here on purpose. The canonical way to introduce a setup value is
        # `hold name "value"` (a declaration, already handled). Bare runnable code at
        # the top belongs in `hold` or inside a handler -- the interpreter does not
        # run it at startup, keeping startup to declarations only.
        decl_types = (SectorDecl, ConnectDecl, ShapeDecl, TaskDecl, HoldDecl,
                      LockDecl, ComplianceDecl, SecurityDecl, IncludeDecl, MioconnectDecl,
                      MiosearchDecl, MiovalidateDecl, PatternDecl, TimespanDecl,
                      RateLimitDecl, LoadPackDecl, MioScheduleDecl, MiofileDecl,
                      AiRankBlock)
        for stmt in program.statements:
            if isinstance(stmt, decl_types):
                self._exec(stmt, ctx)
            elif isinstance(stmt, JourneyDecl):
                if getattr(stmt, 'name', None):
                    ctx._journey_name = stmt.name
                self._exec_journey_scope(stmt, ctx)
        # Scope is now wired into ctx -- journeys executed for a request must not
        # re-run their declarations (connect/hold/task would fire twice).
        self._scope_prewired = True

    def _route_program(self, program, ctx):
        """Resolve a request against every top-level routable listener — listen-for
        blocks, journeys, and bare pages. A no-route (404) from one listener does not
        shadow a match from a sibling (continue-on-404). If a path was given and
        nothing matched, return one clean 404 rather than a silent None."""
        from mohio_ast import ListenBlock, JourneyDecl, PageDecl
        listener_types = (ListenBlock, JourneyDecl, PageDecl)
        req = getattr(ctx, '_current_request', None)
        has_listeners = any(isinstance(s, listener_types) for s in program.statements)
        final_404 = None
        for stmt in program.statements:
            if not isinstance(stmt, listener_types):
                continue
            try:
                result = self._exec(stmt, ctx)
            except _GiveBack as gb:
                return self._format_response(gb)
            if result is None:
                continue
            if isinstance(result, dict) and result.get('status') == 404:
                final_404 = result
                continue
            return result
        if final_404 is not None:
            return final_404
        if has_listeners and req is not None and req.get('_path') is not None:
            return {'status': 404, '_no_route': True,
                    'body': f"No route matches {req.get('_method', 'GET')} {req.get('_path')}"}
        return None

    def _exec_listeners(self, program, ctx):
        """Run listener blocks for a stateful (session) request. Delegates to the
        shared program router so journeys, listen-for blocks, and bare pages all
        route by path and coexist without first-match shadowing."""
        return self._route_program(program, ctx)
