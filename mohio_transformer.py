# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
mohio_transformer.py
Mohio Language -- Parse Tree Validator + Compile-Time Enforcement
Version: 3.8.0 | May 2026 | Particular LLC
"""

from __future__ import annotations


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
#   This file is validate()
#   LAYER 1 of 3 -- the RAW PARSE TREE.
#
#   Rules that need tokens, positions, or raw text (a retired keyword is a TOKEN, not a node).
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

from dataclasses import dataclass, field
from typing import Optional
from lark import Token, Tree
import re, sys


# -- Agent-readable error codes and hint lookup ------------------------------
# -- Reserved word list ------------------------------------------------
from mohio_services import SERVICE_ROOTS

# The mio* half of this set used to be typed out by hand here, and it had drifted from
# the grammar in BOTH directions: it reserved three names that were not services at all
# (so `miosms` was refused as a variable name for a service the grammar had never heard
# of) and it was missing five that were (mioprint, miopublish, miopush, mioresponse,
# miovalidate). It is now DERIVED from the one canonical list in mohio_services.py, and
# tests/test_service_name_reservation.py fails loud if the grammar and that list drift.
MOHIO_RESERVED_EXACT = {
    # Verb/outcome modifier namespaces
    "on", "do", "while",
    # AI primitive namespace
    "ai",
    # Shape type namespace
    "sh",
    # Environment namespace
    "env",
} | set(SERVICE_ROOTS)  # built-in mio namespaces -- exact match only (mio_something is fine)

MOHIO_RESERVED_WHAT = {
    "on":          "verb modifier namespace (on.failure, on.success, on.resolve)",
    "do":          "verb modifier namespace (do.once, do.after, do.unless)",
    "while":       "state modifier namespace (while.active)",
    "ai":          "AI primitive namespace (ai.decide, ai.audit, ai.explain)",
    "sh":          "shape type namespace (sh.ShapeName)",
    "env":         "environment namespace (env.VARIABLE_NAME)",
    "miocookie":   "built-in cookie service (miocookie.set, miocookie.get)",
    "miocache":    "built-in cache service (miocache.set, miocache.get)",
    "miolog":      "built-in logging service (miolog.info, miolog.warn)",
    "miohttp":     "built-in HTTP service (miohttp.get, miohttp.post)",
    "miomail":     "built-in email service (miomail.send)",
    "miofile":     "built-in file service (miofile.read, miofile.write)",
    "mioauth":     "built-in auth service (mioauth.login, mioauth.jwt)",
    "miopdf":      "built-in PDF service (miopdf.from, miopdf.merge)",
    "miotest":     "built-in test service (miotest.unit, miotest.expect)",
    "miosearch":   "built-in search service (miosearch.index, miosearch.query)",
    "mioimage":    "built-in image service (mioimage.resize, mioimage.crop)",
    "mioai":       "built-in AI generation service (mioai.generate, mioai.embed)",
    "miosms":      "built-in SMS service (miosms.send)",
    "miosys":      "built-in system service (miosys.run)",
    "miostream":   "built-in streaming service (miostream.open)",
    "miodata":     "built-in data service (miodata.xml, miodata.csv)",
    "mioenv":      "built-in environment service (mioenv.check)",
    "miograph":    "built-in graph service (miograph.query)",
    "miovault":    "built-in secrets service (miovault.get)",
    "mioschedule": "built-in scheduler (mioschedule.at, mioschedule.every)",
    "mioknow":     "built-in AI memory service (mioknow.recall)",
    "miochain":    "built-in blockchain service (miochain.tx)",
    "mioapp":      "built-in app declaration (mioapp)",
    "miolearn":    "built-in training data capture (miolearn)",
    "miotranslate":"built-in translation service (miotranslate)",
}

HINT_TABLE = {
    "RESERVED_WORD_VARIABLE":    "Use a different name -- this word is reserved for a Mohio built-in. See mohio.io/docs/reserved-words",
    "HARDCODED_CREDENTIAL":       "Replace with env.VARIABLE_NAME. Never hardcode credentials.",
    "MISSING_AGENT_LIMITS":       "Add: limits / max steps 10 / max cost 0.50 / limits: done",
    "SECTOR_VIOLATION":           "Raise confidence to sector floor or add sec.non_critical.",
    "SECURITY_DEBT_UNDOCUMENTED": "Add reason and expires to security: off declaration.",
    "SCHEMA_FIELD_UNKNOWN":       "Check field name against shape. Run mio schema generate.",
    "SCHEMA_TABLE_UNKNOWN":       "Add shape declaration for this table or check spelling.",
    "MIOMAP_FIELD_UNKNOWN":       "Check field exists in source or target shape.",
    "MISSING_FALLBACK":           "Add not confident block inside ai.decide.",
    "MISSING_AUDIT":              "Add ai.audit to [log_name] before not confident block.",
    "AUDIT_ORDER":                "Move ai.audit above the not confident block.",
    "SECTOR_SECURITY_FLOOR":      "Raise security level. sector: financial requires strict.",
    "VISIBILITY_CONFLICT":        "Move authenticated routes into a private section.",
    "TAINT_FLOW":                 "Pass request.body through a shape before using in queries.",
    "MISSING_AGENT_LIMITS":       "Add limits block with max steps and/or max cost.",
    "HARDCODED_SIGNING_KEY":      "Never hardcode a private key. Use miovault for signing. "
                                  "Private keys must never enter application memory.",
    "MISSING_SIGN_VIA":           "Add 'sign via miovault' to miochain.tx.send. "
                                  "All transaction signing must route through miovault.",
    "HARDCODED_CONTRACT_ADDR":    "Use env.CONTRACT_ADDRESS instead of a hardcoded address. "
                                  "Contract addresses belong in environment variables.",
}

@dataclass
class CompileError:
    message: str
    line:    int
    hint:    str = ""
    code:    str = ""   # structured error code e.g. HARDCODED_CREDENTIAL
    def __str__(self):
        parts = [f"\nLine {self.line} -- {self.message}"]
        if self.hint:
            parts.append(self.hint)
        return "\n".join(parts)
    def to_dict(self):
        import re as _re
        code = self.code or (_re.match(r"([A-Z][A-Z_]{2,}):", self.message) or [None,"ERROR"])[1] if self.message else "ERROR"
        if hasattr(code, 'group'): code = code.group(1)
        return {
            "code":    code,
            "line":    self.line,
            "message": self.message,
            "hint":    self.hint or HINT_TABLE.get(code, ""),
        }

@dataclass
class CompileWarning:
    message: str
    line:    int
    hint:    str = ""
    code:    str = ""
    def __str__(self):
        parts = [f"\nWarning -- Line {self.line} -- {self.message}"]
        if self.hint:
            parts.append(self.hint)
        return "\n".join(parts)
    def to_dict(self):
        import re as _re
        code = self.code or (_re.match(r"([A-Z][A-Z_]{2,}):", self.message) or [None,"WARNING"])[1] if self.message else "WARNING"
        if hasattr(code, 'group'): code = code.group(1)
        return {
            "code":    code,
            "line":    self.line,
            "message": self.message,
            "hint":    self.hint or HINT_TABLE.get(code, ""),
        }

class MohioCompileError(Exception):
    def __init__(self, errors):
        self.errors = errors
        msgs = "\n".join(str(e) for e in errors)
        super().__init__(f"\n{'='*55}\nBuild failed -- {len(errors)} error(s):\n{'='*55}\n{msgs}\n")


@dataclass
class CompileContext:
    sector: Optional[str] = None
    sector_base: str = ""
    sector_full: str = ""
    sector_profile: Optional[object] = None   # loaded SectorProfile
    compliance: list = field(default_factory=list)
    block_stack: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def error(self, msg, line, hint=""):
        self.errors.append(CompileError(msg, line, hint))

    def warn(self, msg, line, hint=""):
        self.warnings.append(CompileWarning(msg, line, hint))

    def push_block(self, name, line):
        self.block_stack.append((name, line))

    def pop_block(self):
        return self.block_stack.pop() if self.block_stack else ("", 0)

    @property
    def financial(self):
        return self.sector == "financial" or self.sector_base == "financial"

    @property
    def healthcare(self):
        return self.sector == "healthcare" or self.sector_base == "healthcare"

    @property
    def education(self):
        return self.sector == "education" or self.sector_base == "education"

    @property
    def crypto(self):
        return self.sector == "crypto" or self.sector_base == "crypto"


def get_line(node):
    if isinstance(node, Token):
        return node.line or 0
    if hasattr(node, "meta") and hasattr(node.meta, "line"):
        return node.meta.line or 0
    if hasattr(node, "children"):
        for child in node.children:
            ln = get_line(child)
            if ln:
                return ln
    return 0

def first_token(tree, *types):
    if isinstance(tree, Token):
        return tree if (not types or tree.type in types) else None
    if hasattr(tree, "children"):
        for child in tree.children:
            r = first_token(child, *types)
            if r:
                return r
    return None

def find_subtree(tree, name):
    if isinstance(tree, Tree):
        if tree.data == name:
            return tree
        for child in tree.children:
            r = find_subtree(child, name)
            if r:
                return r
    return None

def find_all_subtrees(tree, name):
    results = []
    if isinstance(tree, Tree):
        if tree.data == name:
            results.append(tree)
        for child in tree.children:
            results.extend(find_all_subtrees(child, name))
    return results

def tree_to_str(tree):
    if isinstance(tree, Token):
        return str(tree)
    if isinstance(tree, Tree):
        return " ".join(tree_to_str(c) for c in tree.children)
    return str(tree)

def extract_closer_name(closer_tree):
    """
    Extract the block name from a closer tree node.
    Returns (block_name, as_name) where as_name may be None.
    
    Handles:
        blockname: done          -> ("blockname", None)
        blockname: done as X     -> ("blockname", "X")
        done                     -> ("", None)
        done as X                -> ("", "X")
    
    Note: "as" is a grammar literal so it does NOT appear as a Token.
    Any NAME token that is not DOTTED_CLOSER and not "done"/"as" is the as_name.
    """
    if not closer_tree or not hasattr(closer_tree, "children"):
        return "", None
    block_name = ""
    as_name    = None
    for child in closer_tree.children:
        if isinstance(child, Token):
            if child.type == "DOTTED_CLOSER":
                block_name = str(child)
            elif child.type == "DONE":
                pass  # skip "done" token
            elif child.type == "NAME":
                val = str(child).lower()
                if val not in ("done", "as"):
                    # Any NAME token is the as_name binding
                    as_name = str(child)
    return block_name, as_name

def validate_closer(ctx, closer_tree, expected, open_line):
    if not closer_tree:
        return
    found, as_name = extract_closer_name(closer_tree)
    if found == "":
        return
    if found != expected:
        ctx.error(
            f"Closer mismatch -- '{found}: done' closes the wrong block.",
            get_line(closer_tree),
            hint=f"The '{expected}' block (line {open_line}) requires '{expected}: done'. Found '{found}: done'."
        )


PCI_NEVER_STORE = {"card_cvv", "cvv", "card_verification"}
PCI_NEVER_LOG   = {"card_cvv", "cvv", "card_number", "card_verification"}
PHI_FIELDS = {"mrn","npi","diagnosis","dob","prescription","medication","lab_result",
              "vital_sign","clinical_note","treatment_plan","patient_name","ssn"}


def noncritical_status(block_text):
    """Single source of truth for the sector-floor exemption rule, shared by the
    validator (compile gate) and the security report (audit) so they cannot drift.

    Returns (present, has_reason):
      present    -- the block contains `sec.non_critical`
      has_reason -- it carries `reason "..."` (REQUIRED for a valid exemption;
                    every override must be justified, and the reason is logged).

    sec.non_critical exempts a single NON-regulatory ai.decide from the sector
    confidence floor. It can never lower the security level or disable a mandatory
    baseline -- the sector minimum is immutable.
    """
    import re as _re
    present = 'sec.non_critical' in block_text
    has_reason = bool(_re.search(r'sec\.non_critical\s+reason\s+["\']', block_text))
    return present, has_reason


class MohioValidator:
    def __init__(self, tree, source="", symbol_table=None):
        self.tree = tree
        self.source = source
        self.ctx = CompileContext()
        self.lines = source.splitlines() if source else []
        self.symbol_table = symbol_table  # Pre-pass symbol table for disambiguation

    def validate(self):
        self._scan_source()
        self._walk(self.tree)
        self._check_unclosed()
        return self.ctx

    def _scan_source(self):
        for i, raw in enumerate(self.lines, 1):
            s = raw.strip()
            if s.startswith("#"):
                self.ctx.error("Mohio comments use '//', not '#' or '##'.", i,
                    hint="Convert this line to a // comment. '#' is reserved and is not a comment marker.")
                continue
            if s.startswith("//"):
                continue

            # Cost controller (compile-time, best effort): a plain 'while' with no visible exit
            # (stop / give back / halt) is likely an infinite loop. while.active is intentionally
            # persistent, so it is excluded. Runtime guards still apply either way.
            _wm = re.match(r'(\s*)while\b(?!\s*:)', raw)
            if _wm and not s.startswith('while.active'):
                _wind = len(_wm.group(1)); _has_exit = False
                for _j in range(i, len(self.lines)):
                    _bl = self.lines[_j]
                    if _bl.strip() == '':
                        continue
                    _bind = len(_bl) - len(_bl.lstrip())
                    if _bind <= _wind:
                        break
                    if re.search(r'\b(stop|halt|exit)\b', _bl) or 'give back' in _bl:
                        _has_exit = True; break
                if not _has_exit:
                    self.ctx.warn("This 'while' loop has no visible exit (stop / give back / halt) and may run forever.", i,
                        hint="Ensure the condition becomes false or add a 'stop'. A runtime guard will also stop it, but catching it here is better.")
            # Wrong data-verb connector. Each data verb pairs with exactly one
            # connector word: find/in, and retrieve/grab/get/remove/from. Using
            # the wrong one makes the parser mis-derive and report a confusing
            # closer mismatch elsewhere -- so we name the real mistake here.
            _cw = s.split()
            if _cw and _cw[0] in ('find', 'retrieve', 'grab', 'get', 'remove'):
                _verb = _cw[0]
                _want = 'in' if _verb == 'find' else 'from'
                _conn = next((w for w in _cw[1:]
                              if w in ('in', 'from', 'to', 'into')), None)
                if _conn and _conn != _want:
                    _other = ("'from' is for retrieve / grab / get / remove"
                              if _want == 'in' else "'in' is for find")
                    self.ctx.error(
                        f"'{_verb}' connects with '{_want}', not '{_conn}'.", i,
                        hint=f"Use '{_verb} ... {_want} <source>'. {_other}.")
            # Hardcoded secrets
            for secret in re.findall(r'"([A-Za-z0-9_\-]{20,})"', raw):
                if "env." not in raw and "secret." not in raw:
                    self.ctx.warn("Possible hardcoded credential.", i,
                        hint=f"Value '{secret[:8]}...' looks like a key. Use env.X or secret.X.")
            # Hardcoded CONNECTION source. A connect source must be env.X or secret.X,
            # never a literal string. A literal here hardcodes a credential (e.g. a
            # postgres URL with user:pass) -- non-suppressible by principle -- AND the
            # grammar only accepts ENV_REF/SECRET_REF, so the string form silently
            # mis-parses into junk assignments and the connection never even opens.
            # The plain-secret scan above misses connection URLs (the :// : @ / chars
            # fall outside its alphanumeric class), so catch the connect form here.
            if re.match(r'\s*connect\b', raw) and not s.startswith('connect:'):
                _ctext = raw
                if not re.search(r'\bfrom\b', raw):        # block form: `from` is on a later line
                    for _j in range(i, min(i + 12, len(self.lines))):
                        _bl = self.lines[_j]
                        if _bl.strip().startswith('connect:'):
                            break
                        _ctext += ' ' + _bl
                if re.search(r'''\bfrom\s+["']''', _ctext):
                    self.ctx.errors.append(CompileError(
                        "Hardcoded connection source. A connect source must be env.X "
                        "or secret.X, never a literal string -- a literal hardcodes a "
                        "credential, and the string form silently fails to open the "
                        "connection (HARDCODED_CREDENTIAL).",
                        i, code="HARDCODED_CREDENTIAL",
                        hint="Use `from env.DATABASE_URL` or `from secret.DB_URL`."))
            # ── Space-form cast not yet supported — FAIL LOUD ─────────────
            # Canon (for now) is the dotted modifier: as.int / as.number /
            # as.decimal / as.boolean / as.string. The space form ("value as int")
            # has no grammar rule and silently mis-parses (the cast vanishes,
            # numeric data stays a string, math then crashes). It is reserved
            # for the Rust rewrite, which will accept both. Until then we catch
            # it here so it screams instead of running wrong.
            # Distinguish from the LEGIT `NAME as type` declaration form (shape
            # field, assignment target, hold/lock/task-param): those have a single
            # leading identifier before `as`. Cast position has more (a value,
            # a default clause, or a close paren) before `as`.
            if not s.startswith(('parse ', 'check ', 'miovalidate', 'task ', 'take ')):
                _cm = re.search(r'\bas\s+(int|integer|number|num|decimal|dec|boolean|bool|string|text)\b', s)
                if _cm:
                    _prefix = s[:_cm.start()].strip()
                    _is_decl = re.fullmatch(r'(set\s+|hold\s+|lock\s+)?[A-Za-z_][\w.]*', _prefix) is not None
                    if not _is_decl:
                        _ct = _cm.group(1)
                        _dotted = {'int':'as.int','integer':'as.int','number':'as.number','num':'as.number',
                                   'decimal':'as.decimal','dec':'as.decimal','boolean':'as.boolean','bool':'as.boolean',
                                   'string':'as.string','text':'as.string'}[_ct]
                        self.ctx.error(
                            f"Space-form cast 'as {_ct}' is not supported yet -- use the dotted modifier '{_dotted}'.",
                            i,
                            hint=(f"Write '{_dotted}', e.g. (value default \"0\") {_dotted}. "
                                  f"The space form is reserved for the Rust rewrite; until then the dotted "
                                  f"modifier is canonical and the space form silently mis-parses."))
            # ── give back render / give back show — the page misparse — FAIL LOUD ──
            # `give back` returns DATA; `render`/`show` are page-output blocks. Written
            # together (`give back render ... render: done`) they silently decompose into
            # a junk `give = back` assignment plus a standalone block -- both individually
            # valid, so nothing complains. The trailing block alone IS the page form.
            _gb = re.match(r'give\s+back\s+(?:\d{3}\s+)?(render|show)\b', s)
            if _gb:
                _kw = _gb.group(1)
                self.ctx.error(
                    f"`give back {_kw}` is not valid. `give back` returns data; `{_kw}` is the "
                    f"page-output block. Together they silently mis-parse into a junk assignment "
                    f"plus a standalone {_kw} block.",
                    i,
                    hint=(f"To serve a page, end the endpoint with a `{_kw}` block and no "
                          f"`give back` -- the {_kw} block IS the response. To return data, use "
                          f"`give back <value>` (optionally `give back <status> <value>`)."))
            # ## comments retired
            if re.match(r'\s*##', raw):
                self.ctx.warn("'##' comments are retired.", i,
                    hint="Use '//' for single-line comments or '/* */' for block comments.")
            # or if
            if re.search(r'\bor\s+if\b', s):
                self.ctx.warn("'or if' is retired.", i,
                    hint="Use 'check / when / otherwise'. mio fmt converts automatically.")
            # `check confidence above N` is CANONICAL. Locked Apr 3 (LDD v2.0), never
            # overturned. A warning used to sit here telling developers it was retired and
            # to drop the `check` prefix -- written by a compiler chat off a stale marker,
            # never a design decision. The advice was backwards, and a gate test then
            # asserted the warning was correct, cementing it. Nothing here is retired.
            # { } outside templates
            if re.search(r'\{[^{]', s) and not re.search(r'\{\{', s):
                self.ctx.warn("Curly braces '{ }' are retired.", i,
                    hint="Use named fields or 'as list sh.[Shape]' for nested structures.")
            # Phase 3 reserved -- only flag as KEYWORDS, not inside string literals
            # Strip string literals before checking to avoid false positives
            # e.g. "You remember the leaflet" should never trigger this
            _stripped = re.sub(r'"[^"]*"|\'[^\']*\'', '', s)
            for word in ("invoke", "recall", "mioagent"):
                if re.search(rf'\b{re.escape(word)}\b', _stripped):
                    self.ctx.error(f"\'{word}\' is Phase 3 reserved -- not yet available.", i,
                        hint=f"Remove this. {word}/mioagent support arrives in Phase 3.")
            # Note: "remember" removed from reserved list -- it appears in string
            # literals legitimately (e.g. game dialogue) and mioknow.remember
            # uses the mioknow. prefix so bare "remember" is safe
            # 'call' is the canonical task-invocation verb (linked to 'task').
            # 'run' for tasks is rejected by the AST transformer (run is async/schedule only).
            # define reserved
            if re.match(r'\s*define\s+\w', raw):
                self.ctx.error("'define' is reserved -- not valid in this version.", i,
                    hint="Use 'shape', 'task', 'miovalidate', or 'load pack'.")
            # take back reserved
            if re.search(r'\btake\s+back\b', s):
                self.ctx.error("'take back' is reserved. Use 'give back'.", i)
            # set retired
            if re.match(r'\s*set\s+\w', raw):
                # RETIRED, not warned. A warning is silent acceptance with extra steps: the
                # code still runs, so the dead keyword survives in docs and comes back as canon.
                self.ctx.error("`set` is retired.", i,
                    hint="Write the declaration directly: `name <value>` (the `=` is optional "
                         "sugar). Use `hold` to freeze until released, or `lock` for a "
                         "permanent constant.")
            # route retired
            if re.match(r'\s*route\s+(GET|POST|PUT|DELETE|PATCH)\b', raw, re.I):
                self.ctx.warn("'route' is retired. Use 'listen for'.", i)
            # emit retired
            if re.match(r'\s*emit\b', s):
                self.ctx.warn("'emit' is retired and reserved.", i)
            # A type label on a hold/assignment (space form) is parsed but dropped --
            # it does not coerce. Casting is the expression form: hold x = value as.int
            if re.match(r'\s*(?:hold|set)\s+[\w.]+\s+as\s+\w', raw) and ' as.' not in raw:
                self.ctx.warn("A type label on a hold/assignment does not convert the value.", i,
                    hint="To coerce, cast in the value: hold x = value as.int. A bare 'as <type>' here is ignored.")
            # <-> bidirectional arrow -- reserved. Valid ONLY inside a languages
            # map block (or an inline langmap using 'using'). Anywhere else
            # (notably miomap field mapping) it is a compile error.
            if re.search(r'<->', s):
                prior = '\n'.join(self.lines[max(0, i-8):i])
                # Note: match the WORD 'languages', not a loose 'map' (which would
                # falsely match 'miomap' and suppress the error).
                in_languages = bool(re.search(r'\blanguages\b', prior)) or bool(re.search(r'\busing\s+', s))
                if not in_languages:
                    self.ctx.error(
                        "'<->' (bidirectional arrow) is reserved and not supported here.",
                        i,
                        hint="Use '->' for one-directional miomap field mapping. "
                             "'<->' is reserved for languages map blocks only.")
            # modify every in [collection] without noun -- compile error
            if re.search(r'\bmodify\s+every\s+in\b', s):
                self.ctx.error(
                    "'modify every in [collection]' requires a noun between 'every' and 'in'.",
                    i,
                    hint="Fix: 'modify every portrait in portrait_file' -- not 'modify every in portrait_file'."
                )

    def _walk(self, node):
        if isinstance(node, Token):
            return
        if not isinstance(node, Tree):
            return
        self._check_trailing_guard_body(node)
        handler = getattr(self, f"_v_{node.data}", None)
        if handler:
            handler(node)
        else:
            for c in node.children:
                self._walk(c)

    def _check_trailing_guard_body(self, node):
        """A trailing `if`/`unless` guard never opens a block. If the next statement
        in the same block is indented FURTHER than the guarded statement, it looks
        guarded by the condition but actually runs unconditionally -- a silent trap
        (verified: with the condition false, the guarded statement is suppressed but
        the indented line below it still runs). The AST flattens the indented line
        into a sibling, so we catch it here on the parse tree, where indentation
        (meta.column) is still visible.

        A trailing guard is an IF_KW / UNLESS_KW token sitting as a DIRECT child of a
        `statement` node (the leading-if block form parses as `retired_if_block`, a
        different node, and is reported separately -- this never double-fires on it).
        """
        stmts = [c for c in node.children
                 if isinstance(c, Tree) and c.data == "statement"]
        for i in range(len(stmts) - 1):
            cur, nxt = stmts[i], stmts[i + 1]
            guard = next((c for c in cur.children
                          if isinstance(c, Token) and c.type in ("IF_KW", "UNLESS")),
                         None)
            if guard is None:
                continue
            cur_col = getattr(cur.meta, "column", None) if not getattr(cur.meta, "empty", True) else None
            nxt_col = getattr(nxt.meta, "column", None) if not getattr(nxt.meta, "empty", True) else None
            if cur_col is None or nxt_col is None:
                continue
            if nxt_col > cur_col:
                nxt_line = getattr(nxt.meta, "line", 0) if not getattr(nxt.meta, "empty", True) else 0
                self.ctx.errors.append(CompileError(
                    f"A trailing `{guard}` guard does not open a block. The line "
                    f"indented beneath it runs unconditionally -- it only LOOKS "
                    f"guarded by the condition.",
                    nxt_line or 0,
                    hint=("Give that line its own trailing guard on its own line, or "
                          "use check/when to guard a block: `check x / when ... / "
                          "... / check: done`.")))

    def _block(self, tree, name, suppress_closer_error=False):
        line = get_line(tree)
        self.ctx.push_block(name, line)
        closer = None
        for c in tree.children:
            if isinstance(c, Tree) and c.data == "closer":
                closer = c
            else:
                self._walk(c)
        if not suppress_closer_error:
            validate_closer(self.ctx, closer, name, line)
        self.ctx.pop_block()

    def _v_cm_action_stmt(self, tree):
        # cm.retain / cm.report / cm.notify are compliance ACTIONS (data
        # retention, regulatory filing, breach notification). They are declared
        # but NOT executed in this build. Warn loudly so no one ships a program
        # that assumes reports are filed or data is retained when nothing runs --
        # same safety reasoning as `verify token`.
        head = next((c for c in tree.children if isinstance(c, Token)), None)
        what = str(head).strip() if head else "cm.*"
        self.ctx.warn(
            f"'{what}' is a compliance action that is declared but not yet "
            f"executed in this build -- retention / reporting / notification "
            f"will NOT actually run.",
            get_line(tree),
            hint="Do not rely on this for compliance yet; perform the action "
                 "explicitly until cm.* enforcement ships.")
        for c in tree.children:
            self._walk(c)

    def _v_notify_stmt(self, tree):
        self.ctx.warn(
            "'notify' is declared but not yet executed in this build -- no "
            "notification is actually sent.",
            get_line(tree),
            hint="Send notifications explicitly (miomail.send / miolog.alert) "
                 "until 'notify' is wired.")
        for c in tree.children:
            self._walk(c)

    def _v_require_role_decl(self, tree):
        line = get_line(tree)
        # Check role_val children -- warn if bare NAME (no quotes)
        for child in find_all_subtrees(tree, 'role_val'):
            tok = first_token(child, 'NAME')
            str_tok = first_token(child, 'STRING')
            if tok and not str_tok:
                self.ctx.warn(f"Role value '{tok}' should be in quotes.", line,
                    hint=f'Use require role "{tok}" -- all role values are strings and go in quotes.')
        for c in tree.children:
            self._walk(c)

    # Declarations
    def _v_sector_decl(self, tree):
        # Read full dotted sector name e.g. "education.us.nc.k12.high-school"
        segments = []
        def collect_segs(node):
            if hasattr(node, 'type') and node.type == 'SECTOR_SEG':
                segments.append(str(node).lower())
            elif hasattr(node, 'children'):
                for child in node.children:
                    collect_segs(child)
        collect_segs(tree)
        if segments:
            full_name = ".".join(segments)
            self.ctx.sector      = segments[0]   # base for .financial/.healthcare checks
            self.ctx.sector_base = segments[0]
            self.ctx.sector_full = full_name
            self._check_sector_trust(full_name, getattr(tree, 'meta', None))
        for c in tree.children:
            self._walk(c)

        # Load sector profile if available
        try:
            from mohio_sector_loader import get_sector_profile
            profile = get_sector_profile(self.ctx.sector_full or self.ctx.sector_base)
            self.ctx.sector_profile = profile
        except ImportError:
            self.ctx.sector_profile = None

    def _check_sector_trust(self, sector_name, meta):
        line = getattr(meta, 'line', 0) if meta else 0
        # Certified-sector paygate -- financial/healthcare require an enterprise
        # license in an enforcing deployment. Fail loud at compile, never silent.
        try:
            from mohio_sector_loader import sector_requires_license, sector_entitled
            if sector_requires_license(sector_name) and not sector_entitled(sector_name):
                self.ctx.error(
                    f"sector: {sector_name} is a certified compliance sector and requires an enterprise license.",
                    line,
                    hint="Set MOHIO_LICENSE for an enterprise deployment, or use a free sector such as 'ecommerce'. Contact hello@mohio.io."
                )
                return
        except ImportError:
            pass
        # Sector truth lives in the loader, not here. The profile's declared tier
        # drives the guidance warning, and the loader decides whether a base is known,
        # so a loaded profile (a demo or a licensed profile) is never misflagged.
        from mohio_sector_loader import sector_tier, is_known_sector_base, PAID_SECTORS
        base = sector_name.split(".")[0]
        tier = sector_tier(sector_name)   # from the loaded profile; None if no profile
        if tier == "official":
            pass  # official profile -- no warning
        elif tier == "certified":
            self.ctx.warnings.append(CompileWarning(
                f"sector: {sector_name} is Mohio-certified but not actively maintained. "
                f"Verify it reflects current regulations before deployment.",
                line,
                hint="Run 'mio check --sector-verify' to check profile currency."
            ))
        elif tier is None and base.lower() in PAID_SECTORS:
            # A licensed/official sector whose profile isn't bundled with the open
            # compiler. The interpreter reports the licensed-profile note separately.
            pass
        else:
            self.ctx.warnings.append(CompileWarning(
                f"sector: {sector_name} is a community or unverified profile. "
                f"Review carefully before production use.",
                line,
                hint="Use an official Mohio sector profile for production compliance."
            ))
        if not is_known_sector_base(sector_name):
            self.ctx.warnings.append(CompileWarning(
                f"Unrecognized sector base '{base}'. If custom, ensure it extends a known base.",
                line,
                hint="Use a paid/enterprise base or provide a matching .sector profile."
            ))

    def resolve_dotted(self, left: str, right: str) -> str:
        """Use symbol table to resolve what kind of dotted expression this is."""
        if self.symbol_table:
            return self.symbol_table.resolve_dotted(left, right)
        # Fallback without symbol table -- use reserved set
        if left.lower() in MOHIO_RESERVED_EXACT:
            return 'builtin'
        return 'unknown'

    def _check_reserved_name(self, name, context, line):
        """Check if a name is a reserved Mohio word. Fatal error if so."""
        name_lower = name.strip().lower()
        if name_lower in MOHIO_RESERVED_EXACT:
            what = MOHIO_RESERVED_WHAT.get(name_lower, "a Mohio built-in namespace")
            # Suggest an alternative
            suggestions = {
                "on": "on_result, outcome, status",
                "do": "do_action, action, run_task", 
                "while": "while_active, is_active, running",
                "ai": "ai_score, ai_result, model_output",
                "sh": "shape_data, shape_ref, record",
                "env": "env_config, config, settings",
            }
            suggestion = suggestions.get(name_lower, f"{name_lower}_value or {name_lower}_data")
            self.ctx.errors.append(CompileError(
                f"'{name}' is reserved -- it is the Mohio {what}. "
                f"Use '{suggestion}' or similar instead.",
                line,
                hint=f"Reserved words cannot be used as {context} names. "
                     f"See mohio.io/docs/reserved-words"
            ))
            return True
        return False


    def _v_compliance_decl(self, tree):
        t = first_token(tree, "NAME")
        if t:
            self.ctx.compliance.append(str(t).upper())
        for c in tree.children:
            self._walk(c)

    # Data blocks
    def _v_retrieve_block(self, t): self._block(t, "retrieve")
    def _v_retrieve_mod_block(self, t):
        tok = first_token(t, "RETRIEVE_MOD")
        self._block(t, str(tok) if tok else "retrieve")
    def _v_find_block(self, t): self._block(t, "find")
    def _v_grab_block(self, t): self._block(t, "grab")
    def _v_get_block(self, t): self._block(t, "get")
    def _v_pull_block(self, t): self._block(t, "pull")
    def _v_compare_block(self, t): self._block(t, "compare")
    def _v_summarize_block(self, t): self._block(t, "summarize")
    def _v_calculate_block(self, t): self._block(t, "calculate")
    def _v_join_block(self, t): self._block(t, "with")

    def _v_save_block(self, tree):
        line = get_line(tree)
        if self.ctx.financial:
            for fv in find_all_subtrees(tree, "save_field"):
                tok = first_token(fv, "NAME")
                if tok and str(tok).lower() in PCI_NEVER_STORE:
                    self.ctx.error(f"PCI violation -- '{tok}' cannot be stored (sector: financial).",
                        get_line(fv) or line,
                        hint="card_cvv must never be stored -- PCI-DSS v4. Tokenize immediately.")
        # Warn on dynamic field names -- cannot validate at compile time
        for fv in find_all_subtrees(tree, "save_field"):
            text = tree_to_str(fv)
            if "set field" in text.lower():
                self.ctx.warn(
                    "Dynamic field assignment -- cannot validate field name at compile time.",
                    get_line(fv) or line,
                    hint="'set field' resolves the column name at runtime. "
                         "Ensure the value is a valid column name before executing."
                )
        self._block(tree, "save")

    def _v_save_or_update_block(self, t):
        """save or update / upsert -- both are valid block names for the closer."""
        line = get_line(t)
        self.ctx.push_block("save", line)
        closer = None
        for c in t.children:
            if isinstance(c, Tree) and c.data == "closer":
                closer = c
            else:
                self._walk(c)
        # Accept either "save" or "upsert" as the closer name
        if closer:
            found, _as = extract_closer_name(closer)
            if found not in ("", "save", "upsert", "save or update"):
                self.ctx.error(
                    f"Closer mismatch -- '{found}: done' closes the wrong block.",
                    get_line(closer),
                    hint=f"Use 'upsert: done' or 'save: done' to close this block."
                )
        self.ctx.pop_block()
    def _v_save_all_block(self, t): self._block(t, "save")
    def _v_update_block(self, t): self._block(t, "update")
    def _v_remove_block(self, t): self._block(t, "remove")
    def _v_remove_all_block(self, t): self._block(t, "remove.all")
    def _v_check_mioql_block(self, t): self._block(t, "check")

    def _v_create_block(self, tree):
        line = get_line(tree)
        if find_subtree(tree, "find_block"):
            self.ctx.error("'find' inside 'create' is not valid.", line,
                hint="Run find first in its own block, then use the result in create.")
        self._block(tree, "create")

    def _v_make_retired_block(self, tree):
        # 'make' is retired -> 'create'. Surface a clean check error (the AST
        # transform also fails loud at run time).
        self.ctx.error("'make' is retired -- use 'create'.", get_line(tree),
            hint="Rename 'make' to 'create' (and 'make: done' to 'create: done'). "
                 "create builds from pieces; ai.create builds from nothing via AI.")

    def _v_apply_block(self, t): self._block(t, "apply")
    def _v_apply_collection_block(self, t): self._block(t, "apply")

    def _v_modify_block(self, tree):
        line = get_line(tree)
        if re.search(r'\bmodify\s+every\s+in\b', tree_to_str(tree)):
            self.ctx.error("'modify every in' requires a noun between 'every' and 'in'.", line,
                hint="Fix: 'modify every portrait in portrait_file'.")
        self._block(tree, "modify")

    def _v_copy_block(self, t): self._block(t, "copy")
    def _v_call_block(self, tree):
        # call is the canonical task-invocation verb (linked to task).
        self._block(tree, "call")

    def _v_run_block(self, t): self._block(t, "run")

    def _v_request_outbound_block(self, t): self._block(t, "request")
    def _v_request_inbound_block(self, t): self._block(t, "request")

    # AI blocks
    def _v_ai_decide_block(self, tree):
        line = get_line(tree)
        tok = first_token(tree, "NAME")
        name = str(tok) if tok else "unknown"
        text = tree_to_str(tree)

        # Sector confidence floor check
        if self.ctx.sector_profile and self.ctx.sector_profile.confidence_floors:
            profile = self.ctx.sector_profile
            floor = profile.get_confidence_floor(name)
            if floor > 0:
                # Extract declared confidence from the block
                conf_match = re.search(r'(?:confidence\s+)?above\s+([\d.]+)', text)
                if conf_match:
                    declared = float(conf_match.group(1))
                    if declared < floor:
                        # sec.non_critical (WITH a reason) is the explicit, audited
                        # exemption for a non-regulatory decision. Check raw source --
                        # the same basis the security report uses -- so the rule is
                        # identical in both places (see noncritical_status).
                        block_src = ("\n".join(self.lines[max(0, line-1): line + 15])
                                     if self.lines else text)
                        present, has_reason = noncritical_status(block_src)
                        if present and has_reason:
                            pass   # justified exemption -- floor suppressed; logged as an audit notice
                        elif present:
                            self.ctx.errors.append(CompileError(
                                f"ai.decide '{name}' uses sec.non_critical without a reason.",
                                line,
                                code="SEC_NONCRITICAL_NO_REASON",
                                hint='Every override must be justified and is logged: '
                                     'sec.non_critical reason "why this decision is non-regulatory".'))
                        else:
                            self.ctx.errors.append(CompileError(
                                f"ai.decide '{name}' confidence {declared} is below the "
                                f"{self.ctx.sector_base} sector floor of {floor}.",
                                line,
                                code="SECTOR_CONFIDENCE_FLOOR",
                                hint=f"Raise confidence to at least {floor} for "
                                     f"{self.ctx.sector_base} sector compliance, or, if this "
                                     f'decision is non-regulatory, add sec.non_critical reason "...". '
                                     f"See: mohio.io/docs/sectors/{self.ctx.sector_base}"
                            ))
        has_nc = "not confident" in text.lower()
        has_audit = "ai.audit" in text
        audit_pos = text.find("ai.audit")
        nc_pos = text.lower().find("not confident")
        audit_first = audit_pos != -1 and nc_pos != -1 and audit_pos < nc_pos

        if not has_nc:
            self.ctx.error(f"ai.decide '{name}' is missing a 'not confident' block.", line,
                hint=f"Every ai.decide must define what happens when confidence falls below threshold.\nAdd 'not confident' inside 'ai.decide {name}'.")
        if has_audit and has_nc and not audit_first:
            self.ctx.error(f"ai.decide '{name}' -- ai.audit must appear before 'not confident'.", line,
                hint="Move ai.audit above the 'not confident' block.")
        if not has_audit:
            self.ctx.warn(f"ai.decide '{name}' has no 'ai.audit' declaration.", line,
                hint="Add 'ai.audit to [log_name]' so this AI decision produces an immutable record.\n"
                     "Recommended for every decision; sector profiles (e.g. financial, healthcare) require it.")
        self._block(tree, "ai.decide")

    def _v_ai_override_stmt(self, tree):
        line = get_line(tree)
        text = tree_to_str(tree)
        # E008 -- missing 'by' attribution
        if not re.search(r'\bby\b', text):
            self.ctx.error("ai.override E008: missing 'by' attribution.", line,
                hint="Add 'by [reviewer_id]' -- who made this correction must be recorded.")
        # E009 -- missing decision correction value
        if not re.search(r'\breason\b', text):
            pass  # reason is E010, checked below
        # E009 -- missing decision correction value
        # Must have a line that is the decision name followed by a value
        # e.g. "isFraudulent false" or "approved true"
        lines_in_block = text.splitlines()
        has_decision_value = any(
            re.match(r'\s+\w+\s+(true|false|\d+|"[^"]*")', l)
            for l in lines_in_block
            if 'by' not in l.lower() and 'reason' not in l.lower() and 'to' not in l.lower()
        )
        if not has_decision_value:
            self.ctx.error("ai.override E009: missing decision correction value.", line,
                hint="Add the decision name and new value, e.g.: isFraudulent false")

        # E010 -- missing reason
        if not re.search(r'\breason\b', text):
            self.ctx.error("ai.override E010: missing 'reason' declaration.", line,
                hint="Add: reason \"Your explanation here\" -- E010 requires a reason string.")
        for c in tree.children:
            self._walk(c)

    def _v_ai_connect_block(self, t): self._block(t, "ai.connect")
    def _v_ai_explain_block(self, tree):
        for c in tree.children:
            self._walk(c)

    def _v_ai_resolve_block(self, tree):
        line = get_line(tree)
        text = tree_to_str(tree)
        has_cache = 'cache' in text
        has_learned = 'learned' in text
        has_live = 'live' in text
        if not (has_cache and has_learned and has_live):
            missing = [t for t, h in [('cache', has_cache), ('learned', has_learned), ('live', has_live)] if not h]
            self.ctx.error(
                f"ai.resolve is missing: {', '.join(missing)}.",
                line,
                hint="ai.resolve requires all three tiers: cache, learned, and live."
            )
        self._block(tree, "ai.resolve")

    def _v_ai_agent_block(self, tree):
        line = get_line(tree)
        text = tree_to_str(tree)
        # goal or name satisfies the "what is this agent doing" requirement
        # tree_to_str drops grammar literals like "goal" -- check raw source instead
        raw_src = getattr(tree.meta, 'orig_text', '') if hasattr(tree, 'meta') else ''
        # Fallback: check if any string value exists in the block (goal produces a string)
        # The goal/name keywords are grammar literals lost in tree_to_str
        # but the goal VALUE (a string) is preserved
        has_goal = ('goal' in text or 'name' in text or
                    bool(raw_src and 'goal' in raw_src) or
                    text.count('"') >= 2)  # goal requires a string value
        # limits satisfied by: limits block OR direct max steps/cost/time
        has_limits = ('limits' in text or 'max steps' in text
                      or 'max cost' in text or 'max time' in text)
        has_nc = 'not confident' in text.lower()
        if not has_goal:
            self.ctx.error("ai.agent is missing a 'goal' declaration.", line,
                hint="Add: goal \"Description of what this agent does\"")
        if not has_limits:
            self.ctx.error("ai.agent is missing a limits declaration.", line,
                hint="Add 'max steps N' to prevent infinite loops and runaway token spend.")
        if not has_nc:
            self.ctx.warn("ai.agent has no 'not confident' block.", line,
                hint="Add 'not confident' -- what to do when the agent cannot complete with sufficient confidence.")
        self._block(tree, "ai.agent")

    def _v_ai_create_stmt(self, tree):
        line = get_line(tree)
        if self.ctx.healthcare:
            text = tree_to_str(tree).lower()
            if "data" in text and any(f in text for f in PHI_FIELDS):
                if not ("realistic" in text and "0.0" in text):
                    self.ctx.error("ai.create data from PHI source requires 'realistic 0.0'.", line,
                        hint="Add 'realistic 0.0' to prevent generated data passing as real patient records.")
        for c in tree.children:
            self._walk(c)

    def _v_ai_create_block(self, tree):
        # A block-form ai.create makes a live AI generation call. Warn (not error) when
        # it generates from a source with no on.failure fallback, so a model outage is
        # handled gracefully instead of crashing the request.
        import re
        line = get_line(tree)
        text = tree_to_str(tree)
        if re.search(r'\bfrom\b', text) and 'on.failure' not in text:
            self.ctx.warn(
                "ai.create generates from a source via a live AI call but has no "
                "'on.failure' fallback.", line,
                hint="Add an 'on.failure' block inside ai.create so a model outage is "
                     "handled gracefully.")
        for c in tree.children:
            self._walk(c)

    # Structure blocks
    def _v_shape_decl(self, t):
        line = get_line(t)
        tok = first_token(t, "NAME")
        if tok:
            self._check_reserved_name(str(tok), "shape", line)
        # Check shape fields against sector profile forbidden/never_store rules
        if self.ctx.sector_profile:
            profile = self.ctx.sector_profile
            # Fields arrive wrapped: shape_decl -> shape_body -> shape_field. The previous
            # version looked for a child whose rule name contained 'field' and found none,
            # because the direct children are `shape_body` -- so a never-store field passed the
            # compiler untouched while the profile correctly declared it forbidden. The rule
            # existed, the profile loaded, and nothing enforced either.
            def _shape_fields(node):
                for child in getattr(node, 'children', []):
                    data = str(getattr(child, 'data', ''))
                    if 'shape_field' in data:
                        yield child
                    elif 'shape_body' in data:
                        for inner in _shape_fields(child):
                            yield inner

            for field_node in _shape_fields(t):
                field_tok = first_token(field_node, 'NAME')
                if not field_tok:
                    continue
                fname = str(field_tok).lower()
                if profile.is_never_store(fname):
                    self.ctx.errors.append(CompileError(
                        f"Field '{fname}' is classified as never-store "
                        f"in {self.ctx.sector_base} sector profile. "
                        f"Use a tokenized identifier instead.",
                        get_line(field_node),
                        code="SECTOR_NEVER_STORE",
                        hint=f"Replace '{fname}' with a token reference. "
                             f"Raw {fname} must never be stored."
                    ))
        self._block(t, "shape")
    def _v_pattern_decl(self, t): self._block(t, "pattern")
    def _v_miomap_decl(self, t): self._block(t, "miomap")
    def _v_mioconnect_decl(self, t): self._block(t, "mioconnect")
    def _v_mioconnect_operation(self, t): self._block(t, "operation")
    def _v_miosearch_decl(self, t): self._block(t, "miosearch")
    def _v_miopdf_decl(self, t): self._block(t, "miopdf")
    def _v_mioimage_decl(self, t): self._block(t, "mioimage")
    def _v_miovalidate_decl(self, t): self._block(t, "miovalidate")

    def _v_task_decl(self, tree):
        line = get_line(tree)
        tok = first_token(tree, "NAME")
        task_name = str(tok) if tok else "task"
        self._check_reserved_name(task_name, "task", line)
        closer = find_subtree(tree, "closer")
        suppress = False
        if closer:
            found, _as = extract_closer_name(closer)
            # If developer used taskName: done instead of task: done -- warn, not error
            if found and found != "task" and found == task_name:
                self.ctx.warn(f"Task closer '{task_name}: done' should be 'task: done'.",
                    get_line(closer) or line,
                    hint="The canonical task closer is 'task: done'. mio fmt converts automatically.")
                suppress = True  # Don't also fire a closer mismatch error
        self._block(tree, "task", suppress_closer_error=suppress)


    def _v_hold_decl(self, tree):
        line = get_line(tree)
        tok = first_token(tree, "NAME")
        if tok:
            self._check_reserved_name(str(tok), "variable", line)
        for c in tree.children:
            self._walk(c)

    def _v_assignment(self, tree):
        line = get_line(tree)
        tok = first_token(tree, "NAME")
        if tok:
            self._check_reserved_name(str(tok), "variable", line)
        for c in tree.children:
            self._walk(c)

    def _v_connect_decl(self, tree):
        line = get_line(tree)
        # connect NAME as ... -- alias is the NAME token
        toks = [t for t in tree.children 
                if hasattr(t, 'type') and t.type == 'NAME']
        if toks:
            self._check_reserved_name(str(toks[0]), "connection", line)
        for c in tree.children:
            self._walk(c)

    def _v_journey_decl(self, t):
        line = get_line(t)
        tok = first_token(t, "NAME")
        if tok:
            self._check_reserved_name(str(tok), "journey", line)
        self._block(t, "journey")
    def _v_saga_decl(self, t): self._block(t, "saga")
    def _v_step_block(self, t): self._block(t, "step")
    def _v_page_decl(self, t): self._block(t, "page")
    def _v_timespan_decl(self, t): self._block(t, "timespan")
    def _v_listen_block(self, t): self._block(t, "listen")
    def _v_new_block(self, t): self._block(t, "new")
    def _v_check_block(self, t): self._block(t, "check")

    def _v_each_block(self, tree):
        line = get_line(tree)
        if find_subtree(tree, "ai_connect_block"):
            self.ctx.error("'ai.connect' must not appear inside an 'each' loop.", line,
                hint="Resolve ai.chain before the each loop via on.resolve.")
        self._block(tree, "each")

    def _v_repeat_block(self, t): self._block(t, "repeat")
    def _v_while_block(self, t): self._block(t, "while")
    def _v_while_active_block(self, t): self._block(t, "while.active")
    def _v_transaction_block(self, t): self._block(t, "transaction")
    def _v_section_block(self, t): self._block(t, "section")
    def _v_sign_block(self, t): self._block(t, "sign")
    def _v_from_connector_block(self, tree):
        for c in tree.children:
            self._walk(c)
    def _v_try_block(self, tree):
        for c in tree.children:
            self._walk(c)


    def _v_languages_block(self, tree):
        line = get_line(tree)
        text = tree_to_str(tree)
        # Check for primary instead of current -- deprecation warning
        if 'primary' in text and 'current' not in text:
            self.ctx.warn("'primary' in languages block should be 'current'.", line,
                hint="The canonical keyword is 'current' -- mio fmt converts 'primary' automatically.")
        for c in tree.children:
            self._walk(c)

    def _v_enterprise_block(self, tree):
        line = get_line(tree)
        text = tree_to_str(tree)
        if 'env.' not in text:
            self.ctx.error("enterprise 'key' must use env.VARIABLE -- never hardcoded.", line,
                hint="Add: key env.MOHIO_ENTERPRISE_KEY")
        for c in tree.children:
            self._walk(c)

    def _v_miotest_decl(self, tree):
        # miotest blocks are valid -- pass through for test runner
        for c in tree.children:
            self._walk(c)

    def _v_it_block(self, tree):
        for c in tree.children:
            self._walk(c)

    def _v_view_decl(self, tree):
        self._block(tree, "view")

    def _v_template_decl(self, tree):
        self._block(tree, "template")

    def _v_ai_resolve_block_outer(self, t): self._block(t, "ai.resolve")
    def _v_ai_agent_block_outer(self, t): self._block(t, "ai.agent")
    def _v_tools_block(self, t): self._block(t, "tools")
    def _v_limits_block(self, t): self._block(t, "limits")
    def _v_run_block(self, t): self._block(t, "run")
    def _v_ignore_stmt(self, tree):
        line = get_line(tree)
        self.ctx.warn("ignore suppresses inherited declarations.", line,
            hint="Suppression logged to build report. Verify this is intentional.")
        for c in tree.children:
            self._walk(c)

    # Phase 3 reserved blocks
    def _v_invoke_block(self, t):
        self.ctx.error("'invoke' is Phase 3 reserved.", get_line(t),
            hint="Remove this block. mioagent arrives in Phase 3.")
    def _v_recall_block(self, t):
        self.ctx.error("'recall' is Phase 3 reserved.", get_line(t),
            hint="Remove this block. mioagent arrives in Phase 3.")
    def _v_remember_block(self, t):
        self.ctx.error("'remember' is Phase 3 reserved.", get_line(t),
            hint="Remove this block. mioagent arrives in Phase 3.")

    # Compliance
    def _v_cm_purge_block(self, tree):
        line = get_line(tree)
        # cm_purge_body children -- first body must be a reason (STRING)
        # The grammar: cm_purge_body: "reason" STRING | "includes" ... | "preserve" ...
        # Lark drops the string "reason" literal -- check if ANY cm_purge_body child
        # contains a STRING token that looks like a reason (not includes/preserve)
        bodies = [c for c in tree.children if isinstance(c, Tree) and c.data == "cm_purge_body"]
        # A `reason <value_expr>` line parses to a cm_purge_reason tree (the
        # source-first reason enhancement). Its presence alone satisfies the
        # reason requirement; main's `reason STRING` -> cm_purge_body still works too.
        has_reason = any(isinstance(c, Tree) and c.data == "cm_purge_reason"
                         for c in tree.children)
        for body in bodies:
            if has_reason:
                break
            # First body with a bare STRING (not inside string_list) = reason
            str_tok = first_token(body, "STRING")
            if str_tok and not find_subtree(body, "string_list"):
                has_reason = True
                break
        if not has_reason:
            self.ctx.error("'cm.purge' requires a 'reason' declaration.", line,
                hint='Data deletion must include an audit reason.\nAdd: reason "GDPR Article 17"')
        # Declared but not yet executed -- same safety reasoning as cm.retain /
        # cm.report / cm.notify, but cm.purge additionally fails loud at runtime
        # because silently skipping a deletion is worse than silently skipping a log.
        self.ctx.warn(
            "'cm.purge' is declared but not yet executed in this build -- NO data "
            "is actually deleted (it will fail loud at runtime).",
            line,
            hint="Do not rely on this for right-to-be-forgotten / erasure yet; "
                 "perform deletion explicitly until cm.purge enforcement ships.")
        for c in tree.children:
            self._walk(c)

    # Service calls
    def _v_service_call_stmt(self, tree):
        if self.ctx.financial:
            line = get_line(tree)
            text = tree_to_str(tree).lower()
            if "miolog" in text:
                for field in PCI_NEVER_LOG:
                    if field in text:
                        self.ctx.error(f"PCI violation -- '{field}' must not appear in logs.", line,
                            hint="PCI-DSS prohibits logging card numbers/CVV. Log only masked/tokenized references.")
        for c in tree.children:
            self._walk(c)

    # Assignment
    def _v_assignment(self, tree):
        # Bare `call NAME` (no closer, no `with`) is ambiguous and degrades to an
        # assignment whose target is the word "call" -- silently no-opping the
        # intended task call. `call` is reserved; fail loud with the real fix.
        name_tok = next((c for c in tree.children
                         if isinstance(c, Token) and c.type == 'NAME'), None)
        if name_tok is not None and str(name_tok).strip().lower() == 'call':
            self.ctx.error(
                "Bare 'call NAME' is not a valid task call -- it parses as an "
                "assignment to a variable named 'call' and does nothing.",
                get_line(tree),
                hint="Use 'call NAME / call: done' (no arguments) or "
                     "'call NAME with VALUE'.")
            return
        tok = first_token(tree, "SET")
        if tok:
            self.ctx.error("`set` is retired.", tok.line or get_line(tree),
                hint="Write the declaration directly: `name <value>` (the `=` is optional "
                     "sugar). Use `hold` to freeze until released, or `lock` for a permanent "
                     "constant.")
        for c in tree.children:
            self._walk(c)

    def _check_unclosed(self):
        for name, open_line in self.ctx.block_stack:
            self.ctx.error(f"'{name}' block was never closed.", open_line,
                           hint=f"Add '{name}: done' to close this block.")

    # -- miocookie validator methods -------------------------------------------
    # miocookie forms are expression-level and block-level nodes.
    # _v_miocookie_set uses _block() to validate the opener/closer pair.
    # The get/delete/exists forms are expression nodes -- no block validation.

    def _v_miocookie_set(self, t):
        """miocookie.set block -- validate opener/closer pair."""
        self._block(t, "miocookie.set")

    def _v_miocookie_get(self, t):
        """miocookie.get -- expression node, no block validation needed."""
        pass

    def _v_miocookie_delete(self, t):
        """miocookie.delete -- expression node, no block validation needed."""
        pass

    def _v_miocookie_exists(self, t):
        """miocookie.exists -- expression node, no block validation needed."""
        pass

    # -- string_op_expr validator ----------------------------------------------
    # after/before/default -- expression node, no block validation needed.
    # The interpreter handles execution; the validator only validates structure.

    def _v_string_op_expr(self, t):
        """after/before/default string operation -- expression, no block needed."""
        pass




def validate(tree, source="", filename="", symbol_table=None):
    v = MohioValidator(tree, source=source, symbol_table=symbol_table)
    ctx = v.validate()
    # Claim 11: Compiler warns if ai.decide blocks exist with no AI compliance tests
    # Check if this is a test file -- if not, note untested ai.decide blocks
    if filename and not filename.endswith('.test.mho'):
        _check_ai_test_coverage(tree, ctx, filename)
    _check_dead_stores(tree, ctx, filename)
    return ctx

def _check_ai_test_coverage(tree, ctx, filename):
    """Claim 11: Track ai.decide blocks and warn if no .test.mho file covers them."""
    import os
    from lark import Tree, Token
    ai_decides = []
    def _find_ai_decides(node):
        if isinstance(node, Tree):
            if node.data == 'ai_decide_block':
                name_tok = next((c for c in node.children 
                                 if isinstance(c, Token) and c.type == 'NAME'), None)
                if name_tok:
                    ai_decides.append(str(name_tok))
            for c in node.children:
                _find_ai_decides(c)
    _find_ai_decides(tree)
    if not ai_decides:
        return
    # Look for a corresponding .test.mho file
    test_file = filename.replace('.mho', '.test.mho')
    ai_test_file = os.path.join(os.path.dirname(filename), 'ai.test.mho')
    has_tests = os.path.exists(test_file) or os.path.exists(ai_test_file)
    if not has_tests:
        names = ", ".join(f"'{n}'" for n in ai_decides)
        ctx.warn(
            f"ai.decide block(s) {names} have no AI compliance tests.",
            0,
            hint=f"Create {os.path.basename(test_file)} or ai.test.mho with compliance tests.\n"
                 f"Run 'mio test --generate ai_compliance' to generate a starter test suite."
        )


# Leading-word that a newcomer reaches for out of another language, appended to the dead-store
# hint TEXT only. Membership here NEVER decides whether the warning fires -- the general
# assigned-but-never-read mechanism does that. This is purely "did you mean ...".
_DEAD_STORE_HINTS = {
    'print':   "Did you mean `show`? Mohio displays a value with `show`.",
    'echo':    "Did you mean `show`? Mohio displays a value with `show`.",
    'puts':    "Did you mean `show`? Mohio displays a value with `show`.",
    'console': "Did you mean `show`? There is no `console.log` in Mohio.",
    'log':     "Did you mean `show`? Mohio displays a value with `show`.",
    'printf':  "Did you mean `show`? Mohio displays a value with `show`.",
    'if':      "A decision is `check` / `when` / `otherwise` (or a trailing `unless`).",
    'def':     "Define reusable work with `task NAME ... task: done`.",
    'function':"Define reusable work with `task NAME ... task: done`.",
    'func':    "Define reusable work with `task NAME ... task: done`.",
    'fn':      "Define reusable work with `task NAME ... task: done`.",
    'var':     "Declare a value with `name value` -- there is no `var`.",
    'let':     "Declare a value with `name value` -- there is no `let`.",
    'return':  "Return a value from a task with `give back`.",
}

# Real Mohio verbs. A lowercase verb never reaches the dead-store check (it parses as a statement,
# not an assignment); only a WRONG-CASE form (`Show`, `Check`) lands here as a bare assignment. So
# these entries only ever fire on a case mistake, and the hint says exactly that.
_DEAD_STORE_VERBS = {
    'show', 'check', 'save', 'find', 'remove', 'repeat', 'task', 'create', 'connect',
    'retrieve', 'listen', 'replace', 'update', 'modify', 'hold', 'lock', 'loop', 'while',
    'call', 'encode', 'verify', 'require', 'summarize', 'calculate', 'compare',
}


def _check_dead_stores(tree, ctx, filename=""):
    """Assigned-but-never-read (dead store).

    `print "hello world"` checks clean and runs silently: with no `print` keyword, the line is
    absorbed as a bare `NAME value` assignment (print = "hello world") and nothing is shown. Same
    shape for `Show "hi"`, `echo`, `return x`, etc. -- any leading word a newcomer brings from
    another language becomes a variable that is set and never read.

    This is the GENERAL detector for that whole class: any top-level `name value` assignment whose
    name is never read anywhere in the program earns a check-time WARNING. `_DEAD_STORE_HINTS` only
    enriches the hint text when the name is a known foreign keyword; it never gates the warning.

    ONE narrow exception is a hard ERROR (A4): a leading word that is a case-variant of a real
    Mohio verb (`Show`, `SHOW`, `Save`) -- not a variable at all, just a mis-cased keyword. A
    foreign keyword (`print`) and any other unknown name stay warnings.

    Scope: TOP-LEVEL statements only (a dead store nested inside a task/block body is ordinary
    unused-local noise, not the newcomer trap). Reads are collected GLOBALLY, so a top-level value
    read anywhere -- including deep inside a task -- counts as used.

    CROSS-FILE SUPPRESSION (accepted trade). The read-scan sees ONLY this single file's Lark tree.
    A value declared here and read in an `include` target, or in the auto-applied `journey.mho`
    spine, looks unread and would warn FALSELY -- and a false warning is worse than a missed one.
    So the WARNING is suppressed for any file that has an `include` or a co-located spine. The A4
    ERROR (a mis-cased verb) is NOT suppressed -- it is read-independent and never a cross-file
    false positive. REAL COST, stated plainly: zork's `index.mho` has BOTH an include and a spine,
    so dead-store warnings are effectively OFF for the largest app in the repo. That is the
    accepted trade; the correct fix is to collect reads at the AST layer AFTER include/spine
    assembly (not at this single-file tree layer), tracked as a known limitation with a revisit
    trigger in the backlog.
    """
    from lark import Tree, Token
    from mohio_pretokenizer import unmark_dotted  # lazy: avoids a module-load import cycle

    # ---- reads, collected globally (every NAME used as a value, every dotted first-part) ----
    target_ids = set()   # id() of NAME tokens that are assignment TARGETS (writes, not reads)
    reads = set()

    def _assign_target(node):
        for c in node.children:
            if isinstance(c, Token) and c.type == 'NAME':
                return c
        return None

    def _mark_targets(node):
        if isinstance(node, Tree):
            if node.data == 'assignment':
                t = _assign_target(node)
                if t is not None:
                    target_ids.add(id(t))
            for c in node.children:
                _mark_targets(c)
    _mark_targets(tree)

    def _collect_reads(node):
        if isinstance(node, Tree):
            for c in node.children:
                _collect_reads(c)
        elif isinstance(node, Token):
            if node.type == 'NAME' and id(node) not in target_ids:
                reads.add(str(node))
            elif node.type == 'USERVAR_DOTTED':
                reads.add(unmark_dotted(str(node))[0])
    _collect_reads(tree)

    # ---- writes, TOP-LEVEL statements only ----
    # top-level statements are the direct children of `start`; do not descend into block bodies.
    top = tree
    if isinstance(top, Tree) and top.data != 'start':
        found = next((c for c in top.iter_subtrees() if isinstance(c, Tree) and c.data == 'start'), None)
        if found is not None:
            top = found

    def _is_block(node):
        # a block introduces its own scope and always closes -- with a `closer` node, or (a few
        # edge blocks) a bare DONE token. Detect the close structurally rather than by rule name,
        # so `task_decl`, `*_block`, and the DONE-closed edge blocks are all treated as scopes.
        return isinstance(node, Tree) and any(
            (isinstance(c, Tree) and c.data == 'closer') or
            (isinstance(c, Token) and c.type == 'DONE')
            for c in node.children)

    warned = set()
    def _top_level_assignments(node):
        # collect assignment nodes that sit at the top level (not inside a block body)
        if not isinstance(node, Tree):
            return
        if node.data == 'assignment':
            t = _assign_target(node)
            if t is not None:
                yield str(t), getattr(t, 'line', 0)
            return
        for c in node.children:
            # never descend into a block body -- a block introduces its own scope, where an
            # unused local is ordinary noise, not the newcomer leading-word trap.
            if isinstance(c, Tree) and not _is_block(c):
                yield from _top_level_assignments(c)

    # Cross-file suppression (see the docstring): this scan sees only THIS file, so a value read
    # in an include target or the co-located journey.mho spine looks unread. Suppress the WARNING
    # (never the A4 error) for such files. NARROW -- only when this file actually has an include or
    # a spine, never globally.
    import os as _os
    has_include = any(isinstance(n, Tree) and getattr(n, 'data', None) == 'include_decl'
                      for n in tree.iter_subtrees())
    has_spine = False
    if filename:
        _base = _os.path.basename(filename)
        _dir = _os.path.dirname(_os.path.abspath(filename))
        if _base != 'journey.mho' and _os.path.isfile(_os.path.join(_dir, 'journey.mho')):
            has_spine = True
    suppress_warnings = has_include or has_spine

    for name, line in _top_level_assignments(top):
        if name in warned:
            continue
        low = name.lower()
        # A4 (tier 1): a case-variant of a real Mohio verb (`Show`, `SHOW`, `Save`, `Check`) is
        # NOT a variable. The line reads aloud as an action and is not one -- a Walk-By failure --
        # and no legitimate variable is named `Show` when `show` is a verb (keywords are
        # case-sensitive; the corpus has zero capitalized bare variables). So this is a hard
        # ERROR, not the dead-store warning, and it fires whether or not the name is read (a read
        # `Show` is still a mistaken verb, not a legitimate variable). NARROW: only case-variants
        # of actual verbs escalate -- a capitalized name that is not a verb (a shape) is untouched.
        if low in _DEAD_STORE_VERBS and name != low:
            warned.add(name)
            ctx.error(f"`{name}` is not a Mohio word. Mohio keywords are lowercase.",
                      line, hint=f"Did you mean `{low}`?")
            continue
        # tiers 2-4 are the assigned-but-never-read detector. A name that IS read anywhere is a
        # legitimate variable (tier 4) -- no diagnostic. A foreign keyword (tier 2) or a plausible
        # unused variable (tier 3) stays a WARNING; never escalate those.
        if name in reads:
            continue
        if suppress_warnings:
            continue   # cross-file: a value read in an include/spine looks unread from here
        warned.add(name)
        if low in _DEAD_STORE_HINTS:
            hint = _DEAD_STORE_HINTS[low]
            if name != low:
                hint = "Mohio keywords are lowercase. " + hint
        else:
            # a plausible real variable, not a foreign keyword -- so this is an honest
            # unused-declaration notice, not a did-you-mean. Do not assume display intent.
            hint = "Declared but never read. Remove it, or read the value somewhere if it is needed."
        ctx.warn(f"`{name}` is set but never used.", line, hint=hint)


def validate_and_raise(tree, source="", filename=""):
    ctx = validate(tree, source=source, filename=filename)
    for w in ctx.warnings:
        print(str(w), file=sys.stderr)
    if ctx.errors:
        raise MohioCompileError(ctx.errors)
    return ctx
