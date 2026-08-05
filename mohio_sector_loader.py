# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
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
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set
from pathlib import Path


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

    # Operation-governance rules (official/certified profiles). Each entry:
    #   {pattern, verdict ('forbidden'|'review'|'allowed'), reason, audit_log}
    # The enforcement mechanism is open-core; these RULES are a paid profile artifact,
    # so a public profile carries none and the open compiler governs no operations.
    operation_rules:    List[dict] = field(default_factory=list)

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

    def get_operation_verdict(self, connector: str, operation: str,
                              touched_classes=None):
        """Return (verdict, reason, audit_log) for a connector operation.

        verdict is 'forbidden' | 'review' | 'allowed'. The first matching rule
        wins, so a profile lists specific rules before broad ones. When no rule
        matches, the operation is allowed -- an operation is only governed if the
        active (paid) profile says so.

        Two rule forms:
          - name/pattern rules match against 'Connector.operation' with a wildcard
            allowed on either side: 'Stripe.refund' (exact), '*.refund' (any
            connector's refund), 'Stripe.*' (every Stripe operation).
          - data-class rules (`any operation touching [pci]`) match when the call's
            payload touches a field carrying that classification. `touched_classes`
            is the set of classifications the payload touches, computed at the call
            site; when it is None or empty, data-class rules simply do not match.
        """
        touched = {str(c).lower() for c in (touched_classes or [])}
        for rule in self.operation_rules:
            dc = rule.get('data_class')
            if dc:
                matched = dc.lower() in touched
            else:
                matched = _op_pattern_matches(rule.get('pattern', ''), connector, operation)
            if matched:
                return (rule.get('verdict', 'allowed'),
                        rule.get('reason', ''),
                        rule.get('audit_log', ''))
        return ('allowed', '', '')

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


def _op_pattern_matches(pattern: str, connector: str, operation: str) -> bool:
    """Match 'Connector.operation' against a rule pattern, case-insensitive, with a
    wildcard '*' allowed on the connector side, the operation side, or both.
    A bare pattern with no dot is treated as an operation wildcard ('*.pattern')."""
    if not pattern:
        return False
    if '.' in pattern:
        pconn, pop = pattern.split('.', 1)
    else:
        pconn, pop = '*', pattern
    pconn = pconn.strip().lower()
    pop = pop.strip().lower()
    conn_ok = (pconn == '*' or pconn == (connector or '').lower())
    op_ok = (pop == '*' or pop == (operation or '').lower())
    return conn_ok and op_ok


def _parse_sector_profile_text(text: str, source_path: str = "") -> SectorProfile:
    """
    Parse a .sector file text into a SectorProfile.
    
    Uses regex-based extraction -- not the full Earley parser.
    Fast, resilient, handles the subset of Mohio used in sector profiles.
    """
    profile = SectorProfile()
    
    # Remove comments
    clean = re.sub(r'//[^\n]*', '', text)
    
    # Extract profile name. Canonical form is the public `sector: name` line
    # (same line the grammar uses to activate a sector in a .mho app; the file
    # extension disambiguates definition from activation). Dotted names allowed
    # (`sector: education.us.nc`). The block closer `sector: done` is skipped.
    # Legacy `sector profile "name"` is tolerated as a fallback during migration.
    for mm in re.finditer(r'(?m)^\s*sector:\s*([A-Za-z][\w.]*)', clean):
        if mm.group(1) != 'done':
            profile.name = mm.group(1)
            break
    if not profile.name:
        m = re.search(r'sector\s+profile\s+"([^"]+)"', clean)
        if m:
            profile.name = m.group(1)

    # Extract meta block fields
    meta_m = re.search(r'meta\s*\n(.*?)meta:\s*done', clean, re.DOTALL)
    if meta_m:
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
        if km and not getattr(profile, key):
            setattr(profile, key, km.group(1))

    # Extract compliance declarations
    # Only match lines that START with compliance: (not in strings/notes)
    for m in re.finditer(r'^\s*compliance:\s*(\w+)', clean, re.MULTILINE):
        val = m.group(1)
        # Skip Mohio keywords that aren't compliance framework names
        if val.lower() not in ('done', 'true', 'false', 'null', 'none'):
            profile.compliance.append(val)

    # Extract retain rules
    for m in re.finditer(
        r'retain\s+all\s+\[([^\]]+)\]\s+for\s+(\d+)\s+(\w+)', clean
    ):
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
        for m in re.finditer(
            r'minimum\s+confidence\s+([\d.]+)\s+for\s+([\w_]+)',
            ai_text
        ):
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

    # Extract operation-governance rules block (official/certified profiles only).
    # Mechanism is open-core; the actual rules ship in the paid profile artifact.
    #   operation rules for sector "financial"
    #       any operation "Stripe.refund"
    #           forbidden
    #           reason "..."
    #       any operation "*.charge"
    #           requires human review
    #           reason "..."
    #           ai.audit to operation_audit_log
    #   operation: done
    op_block = re.search(
        r'operation\s+rules\s+for\s+sector\s+"[^"]+"\s*\n(.*?)operation:\s*done',
        clean, re.DOTALL
    )
    if op_block:
        op_text = op_block.group(1)
        # Split into per-operation chunks at each `any operation ...`.
        chunks = re.split(r'(?m)^\s*any\s+operation\s+', op_text)
        for chunk in chunks[1:]:
            # Two rule forms:
            #   any operation "Stripe.refund"   -> name/pattern rule
            #   any operation touching [pci]     -> data-class rule (v2)
            pm = re.match(r'\s*"([^"]+)"', chunk)
            tm = re.match(r'\s*touching\s+\[([A-Za-z_]+)\]', chunk)
            if pm:
                pattern, data_class = pm.group(1), None
            elif tm:
                pattern, data_class = None, tm.group(1).lower()
            else:
                continue
            if re.search(r'\bforbidden\b', chunk):
                verdict = 'forbidden'
            elif re.search(r'requires\s+human\s+review', chunk):
                verdict = 'review'
            else:
                verdict = 'allowed'
            rm = re.search(r'reason\s+"([^"]+)"', chunk)
            am = re.search(r'ai\.audit\s+to\s+([\w_]+)', chunk)
            profile.operation_rules.append({
                'pattern':    pattern,
                'data_class': data_class,
                'verdict':    verdict,
                'reason':     rm.group(1) if rm else '',
                'audit_log':  am.group(1) if am else '',
            })

    # Extract pci rules block -- additional never_store and enforcement
    pci_block = re.search(
        r'pci\s+rules\s*\n(.*?)pci:\s*done', clean, re.DOTALL
    )
    if pci_block:
        pci_text = pci_block.group(1)
        # "card_number must tokenize before storage" -> effectively never store raw
        for m in re.finditer(r'(\w+)\s+(?:never\s+store|never\s+logged)', pci_text):
            profile.never_store_fields.add(m.group(1).lower())
        # Check field types for never store
        for m in re.finditer(r'(\w+)\s+must\s+tokenize', pci_text):
            profile.never_store_fields.add(m.group(1).lower())

    # Extract profile notes
    notes_block = re.search(
        r'profile\s+notes\s*\n(.*?)profile:\s*done', clean, re.DOTALL
    )
    if notes_block:
        for m in re.finditer(r'"([^"]+)"', notes_block.group(1)):
            profile.notes.append(m.group(1))

    return profile


# ── Profile discovery ─────────────────────────────────────────────────────────

def _sector_filename(sector_name: str) -> str:
    """Convert sector name to expected filename."""
    # education.us.nc -> sector-education-us-nc.sector
    # financial -> sector-financial.sector
    slug = sector_name.replace('.', '-').replace('_', '-')
    return f"sector-{slug}.sector"


def find_sector_profile(
    sector_name: str,
    search_paths: Optional[List[str]] = None
) -> Optional[str]:
    """
    Find a .sector file for the given sector name.
    Returns the path if found, None otherwise.
    """
    if search_paths is None:
        # Default search order
        search_paths = [
            "./sectors",
            os.path.expanduser("~/.mohio/sectors"),
            os.path.join(os.path.dirname(__file__), "sectors"),
        ]

    # Try exact name first, then base sector (education.us.nc -> education)
    names_to_try = [sector_name]
    parts = sector_name.split('.')
    for i in range(len(parts) - 1, 0, -1):
        names_to_try.append('.'.join(parts[:i]))

    for search_path in search_paths:
        if not os.path.isdir(search_path):
            continue
        for name in names_to_try:
            fname = _sector_filename(name)
            full_path = os.path.join(search_path, fname)
            if os.path.exists(full_path):
                return full_path

    return None


def load_sector_profile(
    sector_name: str,
    search_paths: Optional[List[str]] = None
) -> Optional[SectorProfile]:
    """
    Find and parse a sector profile for the given sector name.
    Returns SectorProfile if found, None otherwise.
    """
    path = find_sector_profile(sector_name, search_paths)
    if not path:
        return None
    
    try:
        text = open(path, encoding='utf-8').read()
        profile = _parse_sector_profile_text(text, path)
        return profile
    except Exception as e:
        # Non-fatal -- warn but continue without profile enforcement
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
    """The tier ('official'/'certified'/'community'/'local') of the loaded profile,
    or None when no profile file is present. Single source for the transformer's
    official/community guidance warning."""
    profile = load_sector_profile(sector_name, search_paths)
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
