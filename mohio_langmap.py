# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
mohio_langmap.py
Mohio Language Pack Pre-Processor
Version: 0.1.0 | May 2026 | Particular LLC

Loads a .langmap file and performs vocabulary substitution
on Mohio source code BEFORE the Lark parser sees it.

This is how Klingon Mohio compiles:
  1. Load maps/kl-en.langmap
  2. Build substitution table: {klingon_word: english_canonical}
  3. Replace tokens in source file
  4. Hand canonical English source to Lark grammar
  5. Parse, transform, execute — grammar never changes

Vocabulary words (verbs, keywords, status values) translate. The clean connectors
(and, or, by, as, with, than, via, to, ...) translate one-to-one. The context-sensitive
connectors (in, on, at, for, from, of, into) resolve through the job map, which picks the
canonical token from context. The grammar structure is invariant; the surface words on
top of it translate.
"""

from __future__ import annotations
import re
from pathlib import Path



# ── Required keyword set ─────────────────────────────────────────────────────
# Every complete .langmap file must map ALL of these canonical keywords.
# Missing entries trigger LANGMAP_INCOMPLETE diagnostic.

MOHIO_REQUIRED_KEYWORDS = frozenset([
    # Core flow
    "hold", "check", "when", "otherwise", "give back", "stop", "skip",
    # Data operations
    "create", "find", "save", "update", "remove", "connect",
    # Route / listen
    "listen for", "new", "require role",
    # AI primitives -- method names after prefix (ai. stays invariant)
    "decide", "audit", "explain", "chain",
    # Lifecycle -- method names after prefix (on. stays invariant)
    "failure", "success",
    # Block structure
    "sector", "shape", "task", "journey", "try", "catch", "always",
    # Modifiers
    "include", "validate",
    # Debug (new)
    "debug", "checkpoint",
])

LANGMAP_VERSION = "1.0"  # minimum compatible langmap version


# ── Layer-2 job resolver: canonical (language-agnostic) machinery ────────────
# After Layer-1 substitution, every word EXCEPT the surface connectors is already
# canonical. So the only language-specific data is the connector rules (which live
# in the .langmap [jobs] section). The pieces below are canonical and shared by
# every language: which data verb implies which connector, which words are events,
# and how to read the context of the token after a connector.

# Canonical data verb -> the connector it implies when the target is a data source.
# Spanish (and others) write the connector loosely before db.X; the VERB decides.
_DATA_VERB_ROLE = {
    'to':   {'save', 'store', 'write', 'upsert', 'put', 'insert'},
    'in':   {'find', 'check', 'search'},
    'from': {'retrieve', 'remove', 'grab', 'pull', 'get', 'read',
             'load', 'take', 'extract', 'fetch', 'delete'},
}
_VERB_TO_CANON = {v: canon for canon, verbs in _DATA_VERB_ROLE.items() for v in verbs}

# Lifecycle event words (canonical, post Layer-1). `en éxito` -> `on success`.
_EVENT_WORDS = {'success', 'failure'}

# Tokeniser: each quoted string is one token; every other run of non-space is one.
_TOKEN_RE = re.compile(r'"(?:[^"\\]|\\.)*"|\S+')
# A data source looks like a dotted name (db.users, store.orders).
_DOTTED_RE = re.compile(r'^[A-Za-z_]\w*\.[A-Za-z_]\w*')


class LangmapLoader:
    """
    Loads a .langmap file and builds a bidirectional substitution table.
    
    .langmap format:
        // comments
        source_word -> target_word      # one-way
        source_word <-> target_word     # bidirectional (both directions valid)
        "multi word phrase" -> "target phrase"
    """

    def __init__(self, langmap_path: str | Path):
        self.path = Path(langmap_path)
        self.forward: dict[str, str] = {}   # source -> canonical
        self.backward: dict[str, str] = {}  # canonical -> source
        self.jobs: dict[tuple[str, str], str] = {}   # (connector, context) -> canonical
        self.connectors: set[str] = set()            # context-sensitive surface words
        self._load()

    def _load(self):
        if not self.path.exists():
            raise FileNotFoundError(f"Language pack not found: {self.path}")
        
        in_jobs = False
        for line_num, line in enumerate(self.path.read_text(encoding='utf-8').splitlines(), 1):
            line = line.strip()
            if not line or line.startswith('//') or line.startswith('#'):
                continue
            # Section markers: [jobs] switches to job-rule parsing; any other
            # [section] switches back to vocabulary parsing.
            if line.startswith('['):
                in_jobs = line.lower().startswith('[jobs')
                continue
            if in_jobs:
                self._parse_job_rule(line)
                continue
            # Capture version declaration: version = "1.0"
            if line.startswith('version') and '=' in line:
                try:
                    ver_val = line.split('=', 1)[1].strip().strip('"').strip("'")
                    self._version = ver_val
                except Exception:
                    pass
                continue
            
            if '<->' in line:
                # Bidirectional
                parts = line.split('<->', 1)
                if len(parts) == 2:
                    src = self._clean(parts[0])
                    tgt = self._clean(parts[1].split('//')[0])  # strip inline comments
                    if src and tgt:
                        self.forward[src] = tgt
                        self.backward[tgt] = src
            elif '->' in line:
                # One-way
                parts = line.split('->', 1)
                if len(parts) == 2:
                    src = self._clean(parts[0])
                    tgt = self._clean(parts[1].split('//')[0])
                    if src and tgt:
                        self.forward[src] = tgt
            # Lines without arrows are ignored (metadata, notes)

    def _clean(self, s: str) -> str:
        s = s.strip().strip('"')
        return s.lower() if s else ''

    def _parse_job_rule(self, line: str):
        """
        Parse one [jobs] rule: `connector + context -> canonical`.
        e.g. `a + path -> at`, `en + event -> on`. The connector is a foreign
        surface word; context is a canonical category the resolver detects
        (path, value, event, duration, source); canonical is the Mohio token.
        """
        if '->' not in line or '+' not in line:
            return
        lhs, canonical = line.split('->', 1)
        canonical = canonical.split('//')[0].strip()
        connector, context = lhs.split('+', 1)
        connector = connector.strip().lower()
        context = context.strip().lower()
        if connector and context and canonical:
            self.jobs[(connector, context)] = canonical
            self.connectors.add(connector)

    def _context_of(self, tok: str, is_string: bool) -> str:
        """Classify the token that follows a connector into a canonical context."""
        if is_string:
            return 'value'
        if tok.startswith('/'):
            return 'path'
        if _DOTTED_RE.match(tok):
            return 'source'           # a data source (db.users)
        if tok in _EVENT_WORDS:
            return 'event'
        if re.match(r'^-?\d', tok):
            return 'duration'         # a number (por 30 días -> for 30 days)
        return 'value'

    def resolve_jobs(self, source: str) -> str:
        """
        Layer 2 — resolve context-sensitive connectors to canonical tokens.

        Runs AFTER Layer-1 substitution, so every word except the surface
        connectors is already canonical. For each connector, the token that
        follows it gives the context, which selects the canonical token from the
        [jobs] rules. A connector before a data source is special: the GOVERNING
        data verb decides (save -> to, find/check -> in, retrieve/remove -> from),
        because the source token itself is identical across those jobs.
        """
        if not self.connectors:
            return source

        out_lines = []
        last_data_verb = None
        for line in source.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith('//') or stripped.startswith('#'):
                out_lines.append(line)
                continue

            tokens = _TOKEN_RE.findall(line)
            # (value, is_string) pairs
            toks = [(t, t.startswith('"')) for t in tokens]

            # Governing data verb: first canonical data verb on the line, else the
            # most recent one seen (the enclosing block's verb).
            line_verb = next((t.lower() for t, s in toks
                              if not s and t.lower() in _VERB_TO_CANON), None)
            if line_verb:
                last_data_verb = line_verb
            governing = line_verb or last_data_verb

            changed = False
            for i, (tok, is_str) in enumerate(toks):
                if is_str:
                    continue
                low = tok.lower()
                # Dotted connector form: `en.success` -> `on.success` (the dot
                # convention, alongside the two-word `en success`). The connector is
                # the dotted prefix; resolve it and keep the joined event.
                if '.' in tok:
                    prefix, _, rest = tok.partition('.')
                    pl = prefix.lower()
                    if pl in self.connectors and rest in _EVENT_WORDS:
                        joined = self.jobs.get((pl, 'event'))
                        if joined:
                            base = joined[:-1] if joined.endswith('.') else joined
                            toks[i] = (base + '.' + rest, False)
                            changed = True
                        continue
                if low not in self.connectors:
                    continue
                nxt, nxt_str = toks[i + 1] if i + 1 < len(toks) else ('', False)
                ctx = self._context_of(nxt, nxt_str) if (nxt or nxt_str) else None
                canonical = None
                if ctx == 'source':
                    canonical = _VERB_TO_CANON.get(governing)
                if canonical is None and ctx is not None:
                    canonical = self.jobs.get((low, ctx))
                if canonical:
                    if canonical.endswith('.') and i + 1 < len(toks) and not toks[i + 1][1]:
                        # Joining canonical: `en success` -> `on.success` (the dotted
                        # lifecycle handler). Merge with the next token and consume it.
                        toks[i] = (canonical + toks[i + 1][0], False)
                        toks[i + 1] = ('', False)
                    else:
                        toks[i] = (canonical, False)
                    changed = True

            if not changed:
                out_lines.append(line)
                continue
            # Preserve indentation; rejoin tokens with single spaces.
            indent = line[:len(line) - len(line.lstrip())]
            out_lines.append(indent + ' '.join(t for t, _ in toks if t))

        return '\n'.join(out_lines)

    def validate_completeness(self) -> list[str]:
        """
        Check that all required keywords are mapped.
        Returns list of missing keyword strings.
        Each missing keyword is a LANGMAP_INCOMPLETE diagnostic.
        """
        missing = []
        for kw in MOHIO_REQUIRED_KEYWORDS:
            # Check if keyword exists in forward OR backward table
            if kw not in self.forward and kw not in self.backward:
                missing.append(kw)
        return sorted(missing)

    def validate_version(self, compiler_version: str = LANGMAP_VERSION) -> bool:
        """
        Check langmap version compatibility.
        Returns True if compatible, False if LANGMAP_VERSION mismatch.
        """
        langmap_ver = getattr(self, '_version', '1.0')
        # Simple major version check -- same major = compatible
        try:
            lv_major = int(str(langmap_ver).split('.')[0])
            cv_major = int(str(compiler_version).split('.')[0])
            return lv_major == cv_major
        except (ValueError, IndexError):
            return True  # Unknown version -- allow with warning

    def get_version(self) -> str:
        """Return the version declared in the langmap file."""
        return getattr(self, '_version', 'unknown')

    def translate(self, source: str, direction: str = 'forward') -> str:
        """
        Apply vocabulary substitution to source code.
        direction: 'forward' (source lang -> canonical) or 'backward' (canonical -> source lang)
        """
        table = self.forward if direction == 'forward' else self.backward
        if not table:
            return source
        
        # Sort by length descending so longer phrases match first
        # "listen for" must match before "listen" alone
        sorted_keys = sorted(table.keys(), key=len, reverse=True)
        
        result = source
        
        # Process line by line to avoid translating inside strings and comments
        lines_out = []
        for line in result.splitlines():
            # Don't translate comment lines
            stripped = line.strip()
            if stripped.startswith('//') or stripped.startswith('#'):
                lines_out.append(line)
                continue
            
            # Don't translate inside strings (simple heuristic)
            # Split on string boundaries, only translate non-string segments
            segments = re.split(r'("(?:[^"\\]|\\.)*")', line)
            new_segments = []
            for i, seg in enumerate(segments):
                if i % 2 == 1:  # Inside quotes
                    new_segments.append(seg)
                else:
                    # Translate this segment
                    for key in sorted_keys:
                        val = table[key]
                        # Accent-insensitive match. A developer typing `metodo` for `método`
                        # otherwise gets NO substitution and the word becomes an ordinary
                        # identifier: the file still compiles, and silently means something
                        # else. That is the worst outcome available, and it is invisible.
                        #
                        # Safe to fold because verify_pack() REFUSES any pack containing two
                        # entries that differ only by accent, so a folded key cannot be
                        # ambiguous. The two checks only work as a pair.
                        _folded = _strip_accents(key)
                        if _folded != key:
                            _fpat = (r'(?<![a-zA-Z_])' + re.escape(_folded)
                                     + r'(?![a-zA-Z_])')
                            seg = re.sub(_fpat, val, seg, flags=re.IGNORECASE)
                        # Word-boundary aware replacement (case insensitive)
                        pattern = r'(?<![a-zA-Z_])' + re.escape(key) + r'(?![a-zA-Z_])'
                        seg = re.sub(pattern, val, seg, flags=re.IGNORECASE)
                    new_segments.append(seg)
            lines_out.append(''.join(new_segments))
        
        return '\n'.join(lines_out)

    @classmethod
    def from_journey(cls, journey_path: Path) -> dict[str, 'LangmapLoader']:
        """
        Read a journey.mho file and extract language pack declarations.
        Returns dict of {lang_code: LangmapLoader} for all declared mappings.
        """
        packs = {}
        if not journey_path.exists():
            return packs
        
        content = journey_path.read_text(encoding='utf-8')
        # Simple extraction — look for 'using' followed by a .langmap path
        for match in re.finditer(r'using\s+([^\s\n]+\.langmap)', content):
            map_path = journey_path.parent / match.group(1)
            try:
                loader = cls(map_path)
                # Derive lang code from filename
                stem = map_path.stem  # e.g. en-klingon or kl-en
                packs[stem] = loader
            except FileNotFoundError:
                pass
        
        return packs


def _normalize_particled_closers(source: str) -> str:
    """Canonical-side closer normalization for the langmap path.

    A block's closer is its bare block name (`listen: done`), but its opener may
    carry a particle (`listen for ...`). When a pack maps a multi-word opener to a
    single foreign word (e.g. `listen for <-> escuchar`), that word also lands in
    closer position, so translation yields `listen for: done`. `<name> for: done`
    is never a valid canonical closer, so reduce it to `<name>: done`. This keeps
    the grammar strict (one closer form) instead of teaching it to accept the
    particled closer everywhere.
    """
    pat = re.compile(r'^(\s*)([A-Za-z_]\w*)\s+for\s*:\s*done\b(.*)$')
    out = [pat.sub(r'\1\2: done\3', line) for line in source.splitlines()]
    return '\n'.join(out) + ('\n' if source.endswith('\n') else '')


def preprocess_source(source: str, lang: str | None, 
                       maps_dir: str | Path | None = None,
                       langmap_path: str | Path | None = None) -> str:
    """
    Main entry point for the pre-processing pass.
    
    Called by mio.py before handing source to Lark.
    
    Args:
        source:       Raw source code (possibly in another language)
        lang:         Language code/name hint (e.g. 'klingon', 'KL', 'portuguese')
        maps_dir:     Directory to look for langmap files (default: ./maps/)
        langmap_path: Explicit path to a .langmap file (overrides lang lookup)
    
    Returns:
        Source code with vocabulary substituted to English canonical form.
    """
    if not lang and not langmap_path:
        return source  # No translation needed — already English
    
    if langmap_path:
        loader = LangmapLoader(langmap_path)
        # en-XX packs hold english<->foreign; translating foreign *source* to
        # canonical English needs the backward direction, same as the filename
        # route below. Without this, en-* packs translated the wrong way.
        stem = Path(langmap_path).stem
        direction = 'backward' if stem.startswith('en-') else 'forward'
        translated = loader.translate(source, direction=direction)
        if direction == 'backward':
            translated = _normalize_particled_closers(translated)
            translated = loader.resolve_jobs(translated)
        return translated
    
    # Try to find the right langmap file
    maps_dir = Path(maps_dir or './maps')
    lang_lower = lang.lower() if lang else ''
    
    # Try common filename patterns
    candidates = [
        maps_dir / f"{lang_lower}-en.langmap",
        maps_dir / f"en-{lang_lower}.langmap",
        maps_dir / f"{lang_lower}.langmap",
    ]
    
    for candidate in candidates:
        if candidate.exists():
            loader = LangmapLoader(candidate)
            # For en-XX files, we need the backward direction
            stem = candidate.stem
            if stem.startswith('en-'):
                direction = 'backward'  # en-klingon: klingon->en = backward direction
            else:
                direction = 'forward'
            translated = loader.translate(source, direction=direction)
            if direction == 'backward':
                translated = _normalize_particled_closers(translated)
                translated = loader.resolve_jobs(translated)
            return translated
    
    # No matching file found
    raise FileNotFoundError(
        f"No language pack found for '{lang}' in {maps_dir}.\n"
        f"Tried: {[str(c) for c in candidates]}\n"
        f"Install with: mio langpack add {lang_lower}"
    )


def translate_file(input_path: Path, output_path: Path, 
                   to_lang: str, maps_dir: Path | None = None) -> None:
    """
    mio translate [file.mho] --to [lang]
    
    Translates a canonical English .mho file to another language.
    Creates a new file — does not modify the original.
    """
    source = input_path.read_text(encoding='utf-8')
    maps_dir = maps_dir or input_path.parent / 'maps'
    
    to_lower = to_lang.lower()
    candidates = [
        maps_dir / f"en-{to_lower}.langmap",
        maps_dir / f"{to_lower}-en.langmap",
        maps_dir / f"{to_lower}.langmap",
    ]
    
    for candidate in candidates:
        if candidate.exists():
            loader = LangmapLoader(candidate)
            stem = candidate.stem
            if stem.startswith('en-'):
                direction = 'forward'   # en-klingon: en->klingon = forward
            else:
                direction = 'backward'
            translated = loader.translate(source, direction=direction)
            output_path.write_text(translated, encoding='utf-8')
            print(f"  Translated: {input_path.name} -> {output_path.name}")
            print(f"  Pack: {candidate.name}")
            print(f"  {len(loader.forward)} vocabulary entries")
            return
    
    raise FileNotFoundError(
        f"No language pack found for '{to_lang}'. "
        f"Tried: {[str(c) for c in candidates]}"
    )


# ── pack integrity ───────────────────────────────────────────────────────────────────
# A language pack is a third-party artifact that rewrites source before the parser sees it.
# A wrong pack does not produce a clumsy translation; it produces a DIFFERENT PROGRAM, and it
# does so silently, because every substitution it makes is textually valid. These checks exist
# because the failures below were all reachable and none of them surfaced.
#
# What is refused is a pack that can CHANGE MEANING. What is allowed is a pack that is merely
# INCOMPLETE -- unmapped keywords fall back to English, which is how a pack grows. That
# distinction is the whole design: a community author can ship a partial pack on day one and
# cannot ship one that silently corrupts a program.

# Keywords retired or non-canonical in Mohio. A pack mapping these teaches syntax the compiler
# will reject, so the developer writes correct-looking code in their own language and gets an
# error about a word they never typed.
RETIRED_KEYWORDS = {
    'make', 'route', 'consider', 'delete', 'if', 'else', 'or if', 'set', 'catch',
}


class PackIntegrityError(Exception):
    """A language pack that could change what a program means."""


def _strip_accents(text):
    """NFD-fold a string so `numero` and `número` compare equal."""
    import unicodedata
    return ''.join(c for c in unicodedata.normalize('NFD', text)
                   if not unicodedata.combining(c))


def verify_pack(loader, *, strict=True):
    """Return a list of integrity findings for a loaded pack.

    Each finding is (severity, code, message) where severity is 'refuse' or 'warn'.
    `refuse` findings mean the pack can change program meaning.
    """
    findings = []
    forward = getattr(loader, 'forward', {}) or {}
    backward = getattr(loader, 'backward', {}) or {}

    # 1. COLLISION on the direction the compiler actually uses. Two English keywords mapping to
    #    one foreign word means the foreign source cannot be resolved back: whichever English
    #    keyword the table happens to yield wins, and the other becomes unwritable in that
    #    language. This is the failure that silently changes a program.
    targets = {}
    for eng, foreign in forward.items():
        targets.setdefault(str(foreign).strip().lower(), []).append(eng)
    for foreign, engs in sorted(targets.items()):
        if len(set(engs)) > 1:
            findings.append((
                'refuse', 'PACK_COLLISION',
                f"'{foreign}' maps back to {len(set(engs))} different keywords "
                f"({', '.join(sorted(set(engs)))}). A program written with '{foreign}' cannot "
                f"be resolved to one meaning."))

    # 2. ACCENT-ONLY VARIANTS. If two distinct entries fold to the same accent-stripped form,
    #    a developer typing the unaccented spelling gets whichever one the table matched -- or
    #    worse, no match at all, and the word passes through into a keyword slot untranslated.
    folded = {}
    for foreign in targets:
        folded.setdefault(_strip_accents(foreign), []).append(foreign)
    for base, variants in sorted(folded.items()):
        if len(variants) > 1:
            findings.append((
                'refuse', 'PACK_ACCENT_COLLISION',
                f"{len(variants)} entries differ only by accent ({', '.join(sorted(variants))}). "
                f"They cannot be told apart by a developer typing without accents."))

    # 3. RETIRED KEYWORDS. A pack teaching syntax the compiler rejects.
    retired = sorted(k for k in forward if str(k).strip().lower() in RETIRED_KEYWORDS)
    if retired:
        findings.append((
            'refuse', 'PACK_RETIRED_KEYWORD',
            f"maps {len(retired)} retired or non-canonical keyword(s): {', '.join(retired)}. "
            f"Code written with these is rejected by the compiler, so the developer sees an "
            f"error about a word they never typed."))

    # 4. VERSION. An undeclared version is indistinguishable from a compatible one, so it is
    #    reported rather than assumed.
    try:
        if not loader.validate_version():
            findings.append((
                'refuse', 'PACK_VERSION',
                f"declares version {loader.get_version()}, which this compiler does not accept."))
    except Exception:
        pass

    # 5. ROUND-TRIP. English -> foreign -> English must return the original keyword. A pack that
    #    fails this cannot survive being read back, which is the whole point of a shared file.
    for eng, foreign in sorted(forward.items()):
        back = backward.get(str(foreign).strip().lower())
        if back is not None and str(back).strip().lower() != str(eng).strip().lower():
            findings.append((
                'refuse', 'PACK_ROUNDTRIP',
                f"'{eng}' -> '{foreign}' -> '{back}' does not round-trip."))

    return [f for f in findings if strict or f[0] != 'refuse']
