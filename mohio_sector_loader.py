# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""
mohio_sector_loader.py -- Sector profile loader for Mohio compiler

Finds, parses, and returns sector profiles as structured data.
The compiler and interpreter use this to enforce sector rules.

Search order for sector profiles:
1. ./sectors/  (local project override)
2. ~/.mohio/sectors/  (user-installed profiles)
3. <compiler_dir>/sectors/  (bundled community profiles)
4. api.mohio.io (certified profiles -- Phase 2)
"""

import os
from mohio_egress import gated_print as print  # noqa: A001,E402 -- every print through the egress gate
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set
from pathlib import Path

# Re-exported so callers can `from mohio_sector_loader import SectorVerificationError` and fail closed
# on a certified-profile verification failure.
from mohio_sector_sign import SectorVerificationError


class SectorUnenforceableRuleError(Exception):
    """A sector profile declares a `security requirements` rule (mfa, session timeout, failed-
    login lockout, password rules, encryption-at-rest/in-transit, API-key rotation -- every
    alternative the `security_rule` grammar production accepts) that this loader does not
    enforce at all. Ron's ruling, 2026-10-04: until enforcement exists, a profile naming such a
    rule REFUSES TO LOAD rather than parse it and silently drop it -- the exact shape that let
    six wrong passwords in a row pass with no lockout, under an active profile that CLAIMED one,
    with `mio check` reporting clean. Never accept a rule this loader will not enforce."""


class SectorUnreadableLineError(SectorUnenforceableRuleError):
    """A sector profile line the loader cannot read: an unknown block, a loose control line, an unclosed
    block, or the removed `operation rules for sector` block (Ron, 2026-10-13 and 2026-10-29). A subclass of the unenforceable-rule refusal, so every place that
    already refuses on that (check, run, the `.mho` and `.sector` loaders) refuses on this too, instead
    of the generic handler warning and running the program unenforced."""


@dataclass
class FieldType:
    """A classified field type from a sector profile."""
    name:           str
    classifications: List[str]  # [phi], [pci], [pii], [identifier], etc.
    never_store:    bool = False
    label:          str  = ""
    format_hint:    str  = ""
    range_min:      Optional[float] = None
    range_max:      Optional[float] = None
    threshold:      Optional[float] = None


@dataclass
class RetainRule:
    """Data retention rule from a sector profile."""
    classification: str   # phi, pci, pii, audit_log, etc.
    duration:       int   # number of time units
    unit:           str   # years, months, days, hours


@dataclass 
class ExpireRule:
    """Session/token expiry rule from a sector profile."""
    classification: str
    duration:       int
    unit:           str


@dataclass
class SectorProfile:
    """
    Parsed sector profile -- the runtime representation of a .sector file.
    Used by compiler for static analysis and interpreter for runtime enforcement.
    """
    # Identity
    name:               str = ""
    version:            str = ""
    tier:               str = "community"  # community/certified/official/local
    maintainer:         str = ""
    parent:             str = ""

    # Compliance frameworks activated
    compliance:         List[str] = field(default_factory=list)

    # Field type classifications
    field_types:        Dict[str, FieldType] = field(default_factory=dict)

    # Fields that must never be stored (compiler enforces)
    never_store_fields: Set[str] = field(default_factory=set)

    # Confidence floors by decision type
    # key: decision type keyword, value: minimum confidence (0.0-1.0)
    confidence_floors:  Dict[str, float] = field(default_factory=dict)

    # Default confidence floor when no specific match
    default_confidence_floor: float = 0.0

    # Data retention rules
    retain_rules:       List[RetainRule] = field(default_factory=list)
    expire_rules:       List[ExpireRule] = field(default_factory=list)

    # Notes / disclaimers
    notes:              List[str] = field(default_factory=list)

    def get_confidence_floor(self, decision_name: str = "") -> float:
        """
        Return the confidence floor for a given decision name.
        Checks specific overrides first, then default.
        
        Matching strategy:
        1. Exact match (approve_credit -> credit_approval? no)
        2. Key words in decision name (transaction in approve_transaction -> transaction_approval)
        3. Decision name words in key (approve in transaction_approval? no)
        4. Default floor
        """
        if decision_name:
            dn = decision_name.lower()
            best_floor = 0.0
            best_match_len = 0
            
            for key, floor in self.confidence_floors.items():
                kl = key.lower()
                # Strategy 1: exact substring match either direction
                if kl in dn or dn in kl:
                    if len(kl) > best_match_len:
                        best_match_len = len(kl)
                        best_floor = floor
                    continue
                
                # Strategy 2: word-level overlap
                key_words = set(kl.replace("_", " ").split())
                name_words = set(dn.replace("_", " ").split())
                overlap = key_words & name_words
                if overlap and len(overlap) > best_match_len:
                    best_match_len = len(overlap)
                    best_floor = floor
            
            if best_floor > 0:
                return best_floor
        
        return self.default_confidence_floor

    def is_never_store(self, field_name: str) -> bool:
        """Return True if this field must never be stored."""
        return field_name.lower() in self.never_store_fields

    def get_field_classifications(self, field_name: str) -> List[str]:
        """Return classification tags for a field name."""
        ft = self.field_types.get(field_name.lower())
        return ft.classifications if ft else []


# ── Profile parsing ───────────────────────────────────────────────────────────

_TIME_UNIT_HOURS = {
    "second": 1/3600, "seconds": 1/3600,
    "minute": 1/60,   "minutes": 1/60,
    "hour":   1,      "hours":   1,
    "day":    24,     "days":    24,
    "week":   168,    "weeks":   168,
    "month":  720,    "months":  720,
    "year":   8760,   "years":   8760,
}


def _parse_sector_profile_text(text: str, source_path: str = "") -> SectorProfile:
    """
    Parse a .sector file text into a SectorProfile.
    
    Uses regex-based extraction -- not the full Earley parser.
    Fast, resilient, handles the subset of Mohio used in sector profiles.
    """
    profile = SectorProfile()

    # Remove comments
    clean = re.sub(r'//[^\n]*', '', text)

    # EVERY PROFILE LINE IS READ, OR THE PROFILE REFUSES (Ron, 2026-10-13, from the sector feasibility
    # review). The extraction below is regex over the whole text, and anything it did not match was
    # dropped: an unclosed `security requirements` block, an `approval rules` block, an `mfa required`
    # line outside any block all loaded and did nothing. Each construct below marks the lines it reads;
    # a non-blank line nobody read refuses at the end, naming it. (Comments are blanked above, so a
    # comment line is never unread.)
    _lines = clean.splitlines()
    _content_lines = [i + 1 for i, ln in enumerate(_lines) if ln.strip()]
    _read_lines = set()
    _where = f"sector profile '{os.path.basename(source_path) if source_path else '(inline)'}', "

    def _line_of(pos):
        return clean.count('\n', 0, pos) + 1

    def _consume(start, end):
        _read_lines.update(range(_line_of(start), _line_of(max(start, end - 1)) + 1))
    
    # Extract profile name. Canonical form is the public `sector: name` line
    # (same line the grammar uses to activate a sector in a .mho app; the file
    # extension disambiguates definition from activation). Dotted names allowed
    # (`sector: education, us, nc`). The block closer `sector: done` is skipped.
    # Legacy `sector profile "name"` is tolerated as a fallback during migration.
    for mm in re.finditer(r'(?m)^\s*sector:\s*([A-Za-z][\w.]*)[^\n]*', clean):
        _consume(mm.start(), mm.end())
        if mm.group(1) != 'done' and not profile.name:
            profile.name = mm.group(1)
    if not profile.name:
        m = re.search(r'sector\s+profile\s+"([^"]+)"[^\n]*', clean)
        if m:
            profile.name = m.group(1)
            _consume(m.start(), m.end())

    # Extract meta block fields
    meta_m = re.search(r'meta\s*\n(.*?)meta:\s*done', clean, re.DOTALL)
    if meta_m:
        _consume(meta_m.start(), meta_m.end())
        meta_text = meta_m.group(1)
        for key, pat in [
            ('version',    r'version\s+"([^"]+)"'),
            ('tier',       r'tier\s+"([^"]+)"'),
            ('maintainer', r'maintainer\s+"([^"]+)"'),
            ('parent',     r'parent\s+"([^"]+)"'),
        ]:
            km = re.search(pat, meta_text)
            if km:
                setattr(profile, key, km.group(1))
    
    # Legacy individual meta fields
    for key, pat in [
        ('version',    r'^    version\s+"([^"]+)"'),
        ('maintainer', r'^    maintainer\s+"([^"]+)"'),
    ]:
        km = re.search(pat, clean, re.MULTILINE)
        if km:
            _consume(km.start(), km.end())
        if km and not getattr(profile, key):
            setattr(profile, key, km.group(1))

    # Extract compliance declarations
    # Only match lines that START with compliance: (not in strings/notes)
    for m in re.finditer(r'^\s*compliance:\s*(\w+)', clean, re.MULTILINE):
        _consume(m.start(), m.end())
        val = m.group(1)
        # Skip Mohio keywords that aren't compliance framework names
        if val.lower() not in ('done', 'true', 'false', 'null', 'none'):
            profile.compliance.append(val)

    # Extract retain rules
    for m in re.finditer(
        r'retain\s+all\s+\[([^\]]+)\]\s+for\s+(\d+)\s+(\w+)', clean
    ):
        _consume(m.start(), m.end())
        tags = [t.strip() for t in m.group(1).split(',')]
        for tag in tags:
            profile.retain_rules.append(RetainRule(
                classification=tag,
                duration=int(m.group(2)),
                unit=m.group(3)
            ))

    # Extract expire rules
    for m in re.finditer(
        r'expire\s+all\s+\[([^\]]+)\]\s+after\s+(\d+)\s+(\w+)', clean
    ):
        _consume(m.start(), m.end())
        tags = [t.strip() for t in m.group(1).split(',')]
        for tag in tags:
            profile.expire_rules.append(ExpireRule(
                classification=tag,
                duration=int(m.group(2)),
                unit=m.group(3)
            ))

    # Extract field types block
    ft_block = re.search(r'field\s+types\s*\n(.*?)field:\s*done', clean, re.DOTALL)
    if ft_block:
        # The field-types body keeps its own multi-line field parser, so its lines are read as one
        # block here (a line inside it that the field regex skips is not separately refused).
        _consume(ft_block.start(), ft_block.end())
        ft_text = ft_block.group(1)
        for m in re.finditer(
            # Terminate at the next FIELD definition, not at the next line. The previous
            # lookahead was `(?=\n\s*\w|\Z)`, which ended the capture at the first newline --
            # so a modifier written the natural indented way:
            #
            #     ssn is [phi, pii]
            #         never store
            #
            # was silently discarded, and `never store` only worked if it was crammed onto the
            # same line. A compliance control that reads as present and enforces nothing is the
            # worst shape a bug can take in this file.
            r'(\w+)\s+(?:is|as)\s+\[([^\]]+)\](.*?)(?=\n\s*\w+\s+(?:is|as)\s+\[|\Z)',
            ft_text, re.DOTALL
        ):
            fname = m.group(1).lower()
            tags = [t.strip() for t in m.group(2).split(',')]
            # A RESERVED LABEL IS A TAKEN NAME (`[important]` v1, RULED by Ron 2026-10-22). A profile
            # is the one place a label is given a meaning, so a profile classifying a field with one
            # refuses to load rather than redefine it.
            from mohio_classification import RESERVED_LABELS as _RESERVED
            _taken = next((t for t in tags if t.lower() in _RESERVED), None)
            if _taken is not None:
                raise SectorUnreadableLineError(
                    f"{_where}field `{fname}`: [{_taken}] is a reserved label (the journey-override "
                    f"marker that trails a journey.mho line), so a profile cannot classify a field with it. "
                    f"Classify the field with [phi], [pii], [pci] or [public].")
            modifiers = m.group(3)
            
            ft = FieldType(name=fname, classifications=tags)
            
            # Check never store
            if 'never store' in modifiers:
                ft.never_store = True
                profile.never_store_fields.add(fname)
            
            # Extract label
            lm = re.search(r'label\s+"([^"]+)"', modifiers)
            if lm:
                ft.label = lm.group(1)
            
            # Extract format
            fm = re.search(r'format\s+"([^"]+)"', modifiers)
            if fm:
                ft.format_hint = fm.group(1)

            # Extract range
            rm = re.search(r'range\s+(\d+)\s+(\d+)', modifiers)
            if rm:
                ft.range_min = float(rm.group(1))
                ft.range_max = float(rm.group(2))

            # Extract threshold
            tm = re.search(r'threshold\s+(\d+)', modifiers)
            if tm:
                ft.threshold = float(tm.group(1))
            
            profile.field_types[fname] = ft

    # Extract confidence floors from ai.decide rules block
    ai_block = re.search(
        r'ai\.decide\s+rules\s+for\s+sector\s+"[^"]+"\s*\n(.*?)ai\.decide:\s*done',
        clean, re.DOTALL
    )
    if not ai_block:
        ai_block = re.search(
            r'ai\.decide\s+requirements\s*\n(.*?)ai\.decide:\s*done',
            clean, re.DOTALL
        )
    
    if ai_block:
        ai_text = ai_block.group(1)
        # The header and the closer are read; each rule line inside is read only when it is a
        # `minimum confidence N for NAME` rule, so any other line in the block refuses at the end.
        _consume(ai_block.start(), ai_block.start(1))
        _consume(ai_block.end(1), ai_block.end())
        for m in re.finditer(
            r'minimum\s+confidence\s+([\d.]+)\s+for\s+([\w_]+)',
            ai_text
        ):
            _consume(ai_block.start(1) + m.start(), ai_block.start(1) + m.end())
            floor = float(m.group(1))
            decision_type = m.group(2)
            profile.confidence_floors[decision_type] = floor

        # Default (fallback) floor for a decision whose type can't be identified
        # is the sector BASELINE — the lowest declared floor — not the highest.
        # The strict per-type floors (e.g. SAR/closure 0.95) are enforced by the
        # sector profile's runtime ai.decide rules, which have decision context.
        # Using the max here wrongly rejected legitimate baseline decisions
        # (e.g. a fraud screen at 0.85).
        if profile.confidence_floors:
            profile.default_confidence_floor = min(profile.confidence_floors.values())

    # THE OPERATION-RULES BLOCK IS REMOVED (RULED by Ron 2026-10-29). `operation` lives only inside a
    # mioconnect connector; a profile does not govern calls by operation name. What a call carries is
    # governed by its data's tags at the egress gate, for every transport. A profile still carrying the
    # block refuses at load, naming the change, rather than loading with rules that no longer run.
    op_block = re.search(r'operation\s+rules\s+for\s+sector\b', clean)
    if op_block:
        raise SectorUnreadableLineError(
            f"{_where}line {_line_of(op_block.start())}: `operation rules for sector` was removed from "
            f"sector profiles, so these rules would no longer run. `operation` belongs to a mioconnect "
            f"connector only. To protect what a call sends, classify the field under `field types` "
            f"(`card_number is [pci]`): Mohio refuses classified data leaving by mail, HTTP, a connector "
            f"or AI unless it is masked. Remove the `operation rules ... operation: done` block.")

    # Extract pci rules block -- additional never_store and enforcement
    pci_block = re.search(
        r'pci\s+rules\s*\n(.*?)pci:\s*done', clean, re.DOTALL
    )
    if pci_block:
        pci_text = pci_block.group(1)
        _consume(pci_block.start(), pci_block.start(1))
        _consume(pci_block.end(1), pci_block.end())
        # "card_number must tokenize before storage" -> effectively never store raw
        for m in re.finditer(r'(\w+)\s+(?:never\s+store|never\s+logged)', pci_text):
            _consume(pci_block.start(1) + m.start(), pci_block.start(1) + m.end())
            profile.never_store_fields.add(m.group(1).lower())
        # Check field types for never store
        for m in re.finditer(r'(\w+)\s+must\s+tokenize', pci_text):
            _consume(pci_block.start(1) + m.start(), pci_block.start(1) + m.end())
            profile.never_store_fields.add(m.group(1).lower())

    # `security requirements ... security: done` -- the grammar's `security_rule` production
    # (mfa required for, session timeout maximum, failed login lockout after, password minimum
    # length, encrypted at rest/in transit, api keys rotate every, and a bare catchall) accepts
    # eight different rule shapes, and THIS LOADER ENFORCES NONE OF THEM: no field on
    # SectorProfile holds any of them, so every line here was being silently dropped -- a
    # profile could declare `mfa required for all staff` and `failed login lockout after 3
    # attempts`, check clean, and enforce neither (measured live: six wrong passwords in a row,
    # zero lockout). Ron's ruling: refuse to load rather than accept a rule nobody checks. Every
    # line in the block is named in the refusal, not just the ones matching a specific keyword,
    # because the grammar's own bare `NAME NAME+` catchall alternative means ANY line here
    # parses as *some* security_rule -- enumerating only the named keyword shapes would leave an
    # unrecognized line silently accepted, the exact false-negative this refusal exists to close.
    sec_block = re.search(r'security\s+requirements\s*\n(.*?)\bsecurity:\s*done', clean, re.DOTALL)
    if sec_block:
        # A closed block is read (an empty one is harmless and loads); one with rules refuses below.
        _consume(sec_block.start(), sec_block.end())
        _rule_lines = [ln.strip() for ln in sec_block.group(1).splitlines() if ln.strip()]
        if _rule_lines:
            _fname = os.path.basename(source_path) if source_path else "(inline)"
            raise SectorUnenforceableRuleError(
                f"sector profile '{_fname}' declares a `security requirements` rule this loader "
                f"does not enforce, so it refuses to load rather than silently accept a rule "
                f"nobody is checking:\n"
                + "\n".join(f"    {ln}" for ln in _rule_lines) + "\n"
                f"    Remove these lines until enforcement exists, or drop the `security "
                f"requirements` block entirely. Use `require role` for access control today; "
                f"MFA, lockout, session and password rules are not yet built.")

    # Extract profile notes
    notes_block = re.search(
        r'profile\s+notes\s*\n(.*?)profile:\s*done', clean, re.DOTALL
    )
    if notes_block:
        _consume(notes_block.start(), notes_block.end())
        for m in re.finditer(r'"([^"]+)"', notes_block.group(1)):
            profile.notes.append(m.group(1))

    _unread = [n for n in _content_lines if n not in _read_lines]
    if _unread:
        _n = _unread[0]
        raise SectorUnreadableLineError(
            f"{_where}line {_n}: `{_lines[_n - 1].strip()}` is not a line the sector loader reads, so it "
            f"would do nothing, and a profile line that cannot be read refuses rather than being dropped"
            + (f" ({len(_unread)} such lines in this profile)" if len(_unread) > 1 else "")
            + ". A profile holds: meta, `compliance:` lines, `retain all [...] for N units`, `expire all "
              "[...] after N units`, field types, ai.decide rules, pci rules and profile "
              "notes, each block closed by its own `...: done`.")
    return profile


# ── Profile discovery ─────────────────────────────────────────────────────────

def _sector_filename(sector_name: str, ext: str = ".sector") -> str:
    """Convert sector name to expected filename with the given extension."""
    # education.us.nc -> sector-education-us-nc(.sector|.mho)
    # financial -> sector-financial(.sector|.mho)
    slug = sector_name.replace('.', '-').replace('_', '-')
    return f"sector-{slug}{ext}"


_program_folder: Optional[str] = None


def set_program_folder(path: Optional[str]) -> None:
    """The folder of the program being checked, run or served. Its own `sectors/` folder is searched
    for profiles (T1-SECTOR-APP-FOLDER-PROFILE, RULED by Ron 2026-10-12), so an app can carry its
    profile beside it wherever it is run from."""
    global _program_folder
    _program_folder = os.path.abspath(path) if path else None


def _default_search_paths() -> List[str]:
    import mohio_data
    paths = ["./sectors", os.path.expanduser("~/.mohio/sectors"), str(mohio_data.SECTORS_DIR)]
    # THE PROGRAM'S OWN FOLDER, after the certified location and before ~/.mohio/sectors (RULED by Ron
    # 2026-10-12): `<program folder>/sectors`, whatever directory the command was started from.
    if _program_folder:
        paths.insert(0, os.path.join(_program_folder, "sectors"))
    # A deployment may configure a dedicated certified-profile location (the secure area a certified
    # `.sector` is served from). The FETCH itself is platform work and not built here; the loader only
    # needs the directory to exist on disk and be listed here so a fetched profile is found.
    extra = os.environ.get("MOHIO_CERTIFIED_SECTORS_DIR", "").strip()
    if extra:
        paths.insert(0, extra)
    return paths


def _find_typed(sector_name: str,
                search_paths: Optional[List[str]] = None,
                exact: bool = False):
    """Find a profile file for the sector, PREFERRING a `.sector` (certified) over a `.mho`
    (community) of the same name, wherever each is found. Returns (path, ext) or (None, None).

    RESOLUTION ORDER (explicit, Ron's no-silent-downgrade rule): a `.sector` always wins over a
    same-named `.mho`. So if a `.sector` exists anywhere on the search path it is authoritative and
    must verify or be refused; the loader NEVER falls back to a same-named `.mho` when the `.sector`
    is present. Only when no `.sector` exists is the `.mho` (community) used. Within one extension the
    earliest search directory wins, in the order: MOHIO_CERTIFIED_SECTORS_DIR (if set), ./sectors,
    ~/.mohio/sectors, the bundled mohio_data/sectors."""
    if search_paths is None:
        search_paths = _default_search_paths()
    names_to_try = [sector_name]
    parts = sector_name.split('.')
    if not exact:
        # `exact` (one level of a composed declaration) looks for this name only; the composition in
        # load_sector_profile walks the shorter names itself, so it never meets them twice.
        for i in range(len(parts) - 1, 0, -1):
            names_to_try.append('.'.join(parts[:i]))

    sector_hit = mho_hit = None
    for search_path in search_paths:
        if not os.path.isdir(search_path):
            continue
        for name in names_to_try:
            if sector_hit is None:
                p = os.path.join(search_path, _sector_filename(name, ".sector"))
                if os.path.exists(p):
                    sector_hit = p
            if mho_hit is None:
                p = os.path.join(search_path, _sector_filename(name, ".mho"))
                if os.path.exists(p):
                    mho_hit = p
    if sector_hit:
        return sector_hit, ".sector"
    if mho_hit:
        return mho_hit, ".mho"
    return None, None


def find_sector_profile(
    sector_name: str,
    search_paths: Optional[List[str]] = None
) -> Optional[str]:
    """The path of a profile file for this sector (either extension), or None. Existence only;
    `load_sector_profile` does the authenticity check."""
    return _find_typed(sector_name, search_paths)[0]


def available_sector_names(search_paths: Optional[List[str]] = None) -> List[str]:
    """Every sector NAME that has a profile file on the search path, for a did-you-mean suggestion.
    Reverses `_sector_filename`: `sector-demo-financial.mho` -> `demo-financial`."""
    import glob as _glob
    if search_paths is None:
        search_paths = _default_search_paths()
    names = set()
    for sp in search_paths:
        for ext in (".sector", ".mho"):
            # glob returns [] for a missing or unreadable directory, so there is nothing to swallow
            # and no directory listing to guard.
            for path in _glob.glob(os.path.join(sp, "sector-*" + ext)):
                fn = os.path.basename(path)
                names.add(fn[len("sector-"):-len(ext)])
    return sorted(names)


# WHAT ANY SECTOR DECLARATION TURNS ON, with or without rules found (RULED by Ron 2026-10-12). Each
# item is a runtime path that asks only "is a sector declared", verified by running; a profile's own
# rules (session expiry, confidence floors, field classes) come on top of these.
BUILTIN_COMPLIANCE_DEFAULTS = (
    "an over-precise money amount is refused, never rounded",
    "stored rows that break a declared `required` refuse at first touch",
    "a default database on a temporary disk refuses at connect",
    "every data write is recorded in the audit trail",
)


def builtin_defaults_text() -> str:
    return "; ".join(BUILTIN_COMPLIANCE_DEFAULTS)


def spelling_hint(sector_name: str, search_paths: Optional[List[str]] = None) -> str:
    """` Did you mean sector: <name>?` with the nearest profile actually found, or "" when none is
    close. Only the text a coder writes, never a second name for the same profile."""
    import difflib
    norm = sector_name.split(".")[0].replace("_", "-").lower()
    close = difflib.get_close_matches(norm, available_sector_names(search_paths), n=1)
    return f" Did you mean sector: {close[0].replace('-', '_')}?" if close else ""


def sector_not_found_message(sector_name: str, search_paths: Optional[List[str]] = None) -> str:
    """The warning for a sector name no profile matches (OR-0, RULED by Ron 2026-10-12, reversing
    2cfaa31's refusal): it warns and runs. Any declared sector turns on the built-in compliance
    defaults, so the warning says those apply and that no sector rules were found, with a spelling
    hint drawn only from profiles actually found. Shared by `mio check` and the runtime."""
    # FOUR FIXES, IN THIS ORDER (Ron, 2026-10-13): writing your own profile comes first, because it is
    # the one a pioneer can always do; the legend line shows at check as well as at run.
    return (f"sector `{sector_name}` is not found: no sector rules were found, and the built-in "
            f"compliance defaults apply ({builtin_defaults_text()}). Fix it one of four ways: "
            f"add your own rules as a community profile at sectors/{_sector_filename(sector_name, '.mho')} "
            f"beside the program; name a certified sector whose profile is installed; correct the "
            f"spelling; or remove the `sector:` line if you did not mean to declare one."
            f"{spelling_hint(sector_name, search_paths)}\n"
            f"'.sector' = certified profile; '.mho' = community/uncertified.")


def certified_not_installed_message(sector_name: str) -> str:
    """A licensed name with no certified .sector installed: still regulated (RULED 2026-10-12)."""
    return (f"sector {sector_name}: certified rules are not installed. Built-in compliance defaults "
            f"apply ({builtin_defaults_text()}); certified sector rules do not.")


REQUIRE_CERTIFIED_ENV = "MOHIO_REQUIRE_CERTIFIED_SECTORS"


def _required_certified_set() -> Set[str]:
    """The sectors a DEPLOYMENT requires to run on a certified profile, from
    MOHIO_REQUIRE_CERTIFIED_SECTORS: a comma / os.pathsep separated list of sector names, or `*`
    (or `all`) for every sector. A DEPLOYMENT setting, never program syntax, so the same source runs
    on a community profile in development and requires the certified one in production.

    BACKEND ENTITLEMENT PLUGS IN HERE: the backend will know which certified profiles a customer is
    owed and can populate this requirement, instead of (or alongside) the environment variable.
    """
    val = os.environ.get(REQUIRE_CERTIFIED_ENV, "")
    parts = re.split(r'[,;' + re.escape(os.pathsep) + r']', val)
    return {p.strip().lower() for p in parts if p.strip()}


def _sector_is_required(sector_name: str, required: Set[str]) -> bool:
    if not required:
        return False
    if '*' in required or 'all' in required:
        return True
    return sector_name.lower() in required or sector_name.split('.')[0].lower() in required


def load_sector_profile(
    sector_name: str,
    search_paths: Optional[List[str]] = None
) -> Optional[SectorProfile]:
    """Find, verify and parse the profile for a sector declaration. Returns a SectorProfile, or None if
    no level of it has a profile file.

    POSITION SETS THE HIERARCHY, BROADEST FIRST (RULED by Ron 2026-10-13). `sector: financial, banking,
    retail` is financial, narrowed by banking, narrowed by retail: the profile for `financial` applies,
    then the profile for `financial, banking` adds to it, then `financial, banking, retail`. A narrower
    level only adds rules (_compose_profiles), so reordering the levels names a different hierarchy and
    gives a different result. A level with no profile adds nothing and is recorded in `missing_levels`,
    which check and run both say. It used to load only the single file named for all the levels, so a
    broader level's profile stopped applying the moment a narrower file existed.
    """
    levels = [lv for lv in str(sector_name).split('.') if lv]
    if len(levels) < 2:
        return _load_level_profile(sector_name, search_paths)
    # The certified-required check reads the declaration as a whole, unchanged by composition.
    _load_level_profile(sector_name, search_paths, required_only=True)
    layers, missing = [], []
    for depth in range(1, len(levels) + 1):
        name = '.'.join(levels[:depth])
        prof = _load_level_profile(name, search_paths, exact=True, check_required=False)
        if prof is None:
            missing.append((levels[depth - 1], _sector_filename(name, "")))
        else:
            layers.append(prof)
    if not layers:
        return None
    if len(layers) == 1 and not missing:
        return layers[0]
    return _compose_profiles(sector_name, layers, missing)


def missing_level_message(sector_name: str, level: str, slug: str) -> str:
    """What check and run both say for a level of a declaration that has no profile."""
    shown = str(sector_name).replace('.', ', ')
    return (f"sector `{shown}`: level `{level}` has no profile (looked for `{slug}.sector` or `{slug}.mho`), "
            f"so it adds no rules and the broader levels still apply. Add `{slug}.mho` to the program's "
            f"sectors folder to give it rules, or remove `{level}` from the declaration.")


def _compose_profiles(sector_name: str, layers: List[SectorProfile], missing) -> SectorProfile:
    """One profile from a declaration's levels, broadest first, where a narrower level ONLY ADDS: lists
    are joined, a confidence floor keeps the highest, a field keeps every classification any level gives
    it and never-store if any level says so. The tier is the weakest of the levels: a chain is
    only as certified as its least certified level."""
    import copy as _copy
    merged = SectorProfile(name=sector_name)
    merged.version = layers[-1].version
    merged.maintainer = layers[-1].maintainer
    merged.tier = "community" if any(lp.tier == "community" for lp in layers) else layers[-1].tier
    for layer in layers:
        for fw in layer.compliance:
            if fw not in merged.compliance:
                merged.compliance.append(fw)
        for fname, ft in (layer.field_types or {}).items():
            have = merged.field_types.get(fname)
            if have is None:
                merged.field_types[fname] = _copy.deepcopy(ft)
                continue
            have.classifications = list(dict.fromkeys(list(have.classifications) + list(ft.classifications)))
            have.never_store = have.never_store or ft.never_store
            have.label = ft.label or have.label
            have.format_hint = ft.format_hint or have.format_hint
            if ft.range_min is not None:
                have.range_min = ft.range_min if have.range_min is None else max(have.range_min, ft.range_min)
            if ft.range_max is not None:
                have.range_max = ft.range_max if have.range_max is None else min(have.range_max, ft.range_max)
            if ft.threshold is not None:
                have.threshold = ft.threshold
        merged.never_store_fields |= set(layer.never_store_fields or ())
        for key, floor in (layer.confidence_floors or {}).items():
            merged.confidence_floors[key] = max(floor, merged.confidence_floors.get(key, 0.0))
        merged.default_confidence_floor = max(merged.default_confidence_floor, layer.default_confidence_floor)
        merged.retain_rules.extend(layer.retain_rules)
        merged.expire_rules.extend(layer.expire_rules)
        merged.notes.extend(layer.notes)
    merged.layers = layers
    merged.missing_levels = list(missing)
    return merged


def _load_level_profile(
    sector_name: str,
    search_paths: Optional[List[str]] = None,
    exact: bool = False,
    check_required: bool = True,
    required_only: bool = False,
) -> Optional[SectorProfile]:
    """Find, verify and parse ONE profile file. Returns a SectorProfile, or None if no file exists.

    Tier is decided by what the profile IS, never by what it says (Ron's ruling):
      - a `.sector` is CERTIFIED, and MUST carry a valid Ed25519 signature over its body, verified
        against the deployment's trusted set. It FAILS CLOSED otherwise -- an unsigned or
        wrongly-signed `.sector` raises SectorVerificationError, and the loader never downgrades to a
        same-named `.mho`.
      - a `.mho` is COMMUNITY and free, loaded with no signature check; its tier is community
        regardless of any `tier "..."` line it carries.

    A DEPLOYMENT may also require the certified tier for named sectors (MOHIO_REQUIRE_CERTIFIED_SECTORS).
    Batch 14 already refused a `.sector` that EXISTS but fails verification; this closes the other half
    of the downgrade: when a required sector's `.sector` is MISSING entirely (a fetch failed, or it was
    never deployed), the loader used to fall back to the community `.mho` with no signal, so a
    deployment that believed it ran certified rules silently ran community ones. Now a required sector
    with no verified `.sector` FAILS CLOSED. When nothing is required, falling back to the community
    `.mho` is unchanged -- the correct free and development path.
    """
    effective_paths = search_paths if search_paths is not None else _default_search_paths()
    path, ext = _find_typed(sector_name, search_paths, exact=exact)

    if check_required and _sector_is_required(sector_name, _required_certified_set()) and ext != ".sector":
        # Required to be certified, but the best we found is a community `.mho` (ext == ".mho") or
        # nothing (path is None). Either way no verified certified profile is present -- fail closed.
        where = ", ".join(effective_paths)
        have = "only a community `.mho` profile is present" if ext == ".mho" else "no profile file was found"
        raise SectorVerificationError(
            f"sector `{sector_name}` is required to run on a CERTIFIED profile in this deployment, but "
            f"no verified `.sector` was found ({have}).\n"
            f"    Looked in: {where}\n"
            f"    To supply it: place the Mohio-signed `{_sector_filename(sector_name.split('.')[0])}` "
            f"in the certified directory (MOHIO_CERTIFIED_SECTORS_DIR), and add Mohio's public key to "
            f"MOHIO_SECTOR_TRUSTED_KEYS.\n"
            f"    To run on the community profile in development, remove `{sector_name.split('.')[0]}` "
            f"from {REQUIRE_CERTIFIED_ENV}.")

    if required_only or not path:
        return None

    if ext == ".sector":
        import mohio_sector_sign as _sign
        data = open(path, "rb").read()
        sig_b64, body = _sign.split_signed(data)
        if sig_b64 is None:
            raise _sign.SectorVerificationError(
                f"sector profile '{os.path.basename(path)}' is a .sector (certified) file but carries "
                f"no Mohio signature. A certified profile must be signed by Mohio. Refused (fail closed). "
                f"A free community profile is a .mho file, not a .sector.")
        if not _sign.verify(body, sig_b64, _sign.trusted_public_keys()):
            raise _sign.SectorVerificationError(
                f"sector profile '{os.path.basename(path)}' does not verify against any trusted Mohio "
                f"key. Its signature is missing, altered, or from a key this deployment does not trust. "
                f"Refused (fail closed).")
        profile = _parse_sector_profile_text(body.decode("utf-8-sig"), path)
        # A SIGNED PROFILE IS THE PROFILE OF THE NAME IT IS LOADED FOR (Ron, 2026-10-13, from the sector
        # feasibility review, S3). The signature binds the file's body, not a name: a different signed
        # profile copied in under another sector's file name verified and loaded, with none of that
        # sector's rules. Its own `sector:` name must match the name it was found under. (Version and
        # chain binding stay in 6.5.)
        def _slug(n):
            return str(n or '').strip().lower().replace('.', '-').replace('_', '-')
        _stem = os.path.basename(path)[len("sector-"):-len(ext)] if os.path.basename(path).startswith(
            "sector-") else os.path.basename(path)[:-len(ext)]
        if _slug(profile.name) != _slug(_stem):
            raise _sign.SectorVerificationError(
                f"sector profile '{os.path.basename(path)}' is signed, but the profile inside it is "
                f"`{profile.name or '(unnamed)'}`, not `{_stem}`: a signed profile for one sector cannot "
                f"stand in for another, so it refuses to load (fail closed). Install the signed profile "
                f"made for `{_stem}`.")
        profile.tier = "certified"     # a verifying .sector IS certified, whatever the text says
        return profile

    # community .mho
    try:
        text = open(path, encoding='utf-8-sig').read()
        profile = _parse_sector_profile_text(text, path)
        profile.tier = "community"     # a .mho is community, whatever a `tier` line claims
        return profile
    except SectorUnenforceableRuleError:
        # MUST propagate as the hard refusal it is -- the generic handler below exists for a
        # genuinely malformed profile (warn and run unenforced), not for a well-formed profile
        # naming a rule this loader will not silently drop.
        raise
    except Exception as e:
        print(f"  [sector] Warning: could not load {path}: {e}")
        return None


# ── Built-in fallback profiles ─────────────────────────────────────────────────
# Deliberately empty. Regulated sectors (financial, healthcare, and the rest of the
# enterprise set) are the commercial tier: their profiles are private and are served
# blindly from a private folder / the licensed runtime image, never bundled in the
# open compiler. So the open compiler carries the ENFORCEMENT MECHANISM but no
# regulated rules -- `sector: financial` with no licensed profile present enforces
# nothing, which is the honest open-core line ("the governance is the product").
#
# Free demonstration of the mechanism uses non-regulated sample profiles instead
# (ecommerce, and the demo-low / demo-high sample floors), never a regulated
# one-word call. If a free basic-floor demo under a generic name is ever wanted, add
# a `.sector` sample file; do not reintroduce regulated names here.
BUILTIN_SECTOR_RULES: Dict[str, Dict] = {}


# ── Certified-sector licensing ──────────────────────────────────────────────
# The enterprise sectors are the commercial tier. In an enforcing deployment (the
# public playground / Railway set MOHIO_ENFORCE_LICENSE) they require a license.
# Local dev, CI, and the test suite run ungated so the language stays fully testable.
# MOHIO_OWNER is the "it's me" override. These names map to the private profiles;
# with no licensed profile present, the sector activates but enforces nothing.
PAID_SECTORS = {
    'financial', 'healthcare', 'government', 'education',
    'legal', 'insurance', 'logistics', 'science',
}


def sector_requires_license(sector_name: str) -> bool:
    """True if the sector's base is a certified/paid sector."""
    return sector_name.split('.')[0].lower() in PAID_SECTORS


def sector_entitled(sector_name: str) -> bool:
    """Whether the current environment may use a paid sector. Free sectors are
    always entitled. Paid sectors are ungated unless MOHIO_ENFORCE_LICENSE is
    set, in which case an owner override or a license token is required."""
    if not sector_requires_license(sector_name):
        return True
    if not os.environ.get('MOHIO_ENFORCE_LICENSE'):
        return True
    return bool(os.environ.get('MOHIO_OWNER') or os.environ.get('MOHIO_LICENSE'))


def is_known_sector_base(sector_name: str,
                         search_paths: Optional[List[str]] = None) -> bool:
    """Single source for 'does the compiler recognize this sector base'. True if the
    base is a paid/enterprise sector or a profile file exists for it. Replaces the
    transformer's hardcoded known-bases set so a loaded profile (a demo or a licensed
    profile) is never flagged 'unrecognized'."""
    base = sector_name.split('.')[0]
    if base.lower() in PAID_SECTORS:
        return True
    return find_sector_profile(base, search_paths) is not None


def sector_tier(sector_name: str,
                search_paths: Optional[List[str]] = None) -> Optional[str]:
    """The tier ('certified' for a verifying `.sector`, 'community' for a `.mho') of the loaded
    profile, or None when no profile file is present. Single source for the transformer's guidance
    warning. A `.sector` that fails to verify, or that declares a rule this loader cannot enforce,
    returns None here; the hard refusal is raised where the profile is loaded for the `sector:`
    declaration, so this guidance path never crashes."""
    try:
        profile = load_sector_profile(sector_name, search_paths)
    except (SectorVerificationError, SectorUnenforceableRuleError):
        # Guidance path only: a failing/refused profile reports no tier here. FAIL-CLOSED SAFE --
        # the hard refusal is raised where the profile is loaded for the `sector:` declaration, so
        # returning None here suppresses only the tier-guidance warning, never the refusal. Without
        # this, the NEW SectorUnenforceableRuleError escaped through this guidance-only path during
        # check-time validation and was reported as an internal compiler crash rather than the
        # deliberate refusal it is -- caught by running `mio run` for real, not by reading the code.
        return None
    return profile.tier if profile else None


def get_sector_profile(
    sector_name: str,
    search_paths: Optional[List[str]] = None
) -> SectorProfile:
    """
    Get a sector profile, falling back to built-in rules if no file found.
    Always returns a SectorProfile (may be empty if sector unknown).
    """
    # Try loading from file first
    profile = load_sector_profile(sector_name, search_paths)
    if profile:
        return profile

    # Fall back to built-in rules for known sectors
    base = sector_name.split('.')[0].lower()
    if base in BUILTIN_SECTOR_RULES:
        rules = BUILTIN_SECTOR_RULES[base]
        p = SectorProfile(name=base, tier="community")
        p.compliance = rules.get("compliance", [])
        p.never_store_fields = rules.get("never_store_fields", set())
        p.confidence_floors = rules.get("confidence_floors", {})
        if p.confidence_floors:
            # Baseline floor = the lowest declared floor. Strict per-type floors
            # (e.g. SAR/closure 0.95) are enforced by runtime sector rules with
            # decision context; an unclassifiable decision gets the baseline so
            # legitimate baseline decisions (e.g. a 0.85 fraud screen) aren't rejected.
            p.default_confidence_floor = min(p.confidence_floors.values())
        return p

    # Unknown sector -- return empty profile
    return SectorProfile(name=sector_name)
