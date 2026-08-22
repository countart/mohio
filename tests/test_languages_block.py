#!/usr/bin/env python3
# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""LANGUAGES -- comprehensive feature test + built-state map.

Ground truth: languages-declaration-2026-08-07.md, journey-newest-decisions-2026-08-07.md.
Zork uses journey, NOT languages -- languages' built state was genuinely open going into
this file, so every aspect below is proven by RUNNING, not assumed. Real in-repo sample
packs: KLINGON (mohio_data/maps/en-klingon.langmap, 61 entries, all `<->`) and EMOJI
(mohio_data/maps/en-emoji.langmap, 32 entries, all `<->`). Neither is a full langmap --
words not mapped fall back to English by design (that is the feature, not a bug); tests
below only assert on words actually present in each pack. Spanish/Portuguese packs live
only in the private mohio-internal/maps folder (~90% complete) and are deliberately never
used here.

Each aspect is labeled WORKS / PARSES-BUT-BROKEN / NOT-WIRED based on what running it
actually showed, not what the design doc says should happen. See PRODUCTION-BUILD-PLAN.md
/ CLAUDE-CODE-BACKLOG.md for the full built-state table and the Batch 8 disposition ruling
this file's results feed into.
"""
import os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import mohio_data
os.chdir(ROOT)
os.environ.setdefault('DATABASE_URL', ':memory:')

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter, MohioRuntimeError
from mohio_ast import LanguagesBlock
from mohio_langmap import LangmapLoader

_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

PASS = FAIL = 0
def check(label, cond, tag="", detail=""):
    global PASS, FAIL
    mark = 'PASS' if cond else 'FAIL'
    print(f"  {mark}  [{tag}]  {label}" + (f"  -- {detail}" if (not cond and detail) else ""))
    PASS += bool(cond); FAIL += (not cond)


def run_and_capture(src):
    """Real .mho source through the full pipeline (parse -> transform -> run), spying
    on the languages config actually stored on ctx. Real path, not a direct call.

    A journey's languages_block executes during run_declarations (journey scope is
    established there), not only during it.run() -- both calls must be inside the
    same try/except, confirmed live: an enforcement failure raised only from
    run_declarations previously escaped an except that wrapped it.run() alone."""
    it = MohioInterpreter()
    t = transform(P.parse(src), src)
    captured = {}
    orig = MohioInterpreter._exec_LanguagesBlock
    def spy(self, node, ctx):
        r = orig(self, node, ctx)
        captured['cfg'] = getattr(ctx, '_languages', None)
        return r
    MohioInterpreter._exec_LanguagesBlock = spy
    try:
        it.run_declarations(t)
        it.run(t)
        return captured.get('cfg'), None
    except MohioRuntimeError as e:
        return captured.get('cfg'), e
    finally:
        MohioInterpreter._exec_LanguagesBlock = orig


KL_PATH = mohio_data.MAPS_DIR / "en-klingon.langmap"
EM_PATH = mohio_data.MAPS_DIR / "en-emoji.langmap"


# ═══════════════════════════════════════════════════════════════════════════
# STEP 1 -- inventory (asserted here so drift in the packs/grammar is caught)
# ═══════════════════════════════════════════════════════════════════════════
print("=== STEP 1: inventory ===")
check("en-klingon.langmap exists", KL_PATH.exists(), "inventory")
check("en-emoji.langmap exists", EM_PATH.exists(), "inventory")
_kl = LangmapLoader(str(KL_PATH))
_em = LangmapLoader(str(EM_PATH))
check("klingon pack is entirely bidirectional (61 entries, forward==backward count)",
      len(_kl.forward) == len(_kl.backward) == 61, "inventory",
      f"forward={len(_kl.forward)} backward={len(_kl.backward)}")
check("emoji pack is entirely bidirectional (32 entries, forward==backward count)",
      len(_em.forward) == len(_em.backward) == 32, "inventory",
      f"forward={len(_em.forward)} backward={len(_em.backward)}")
print()


# ═══════════════════════════════════════════════════════════════════════════
# STEP 2a -- BLOCK KEYWORDS
# ═══════════════════════════════════════════════════════════════════════════
print("=== BLOCK KEYWORDS ===")

cfg, err = run_and_capture(
    'journey T\n    languages\n        current EN\n    languages: done\n'
    '    show "ok"\njourney: done\n')
check("current -- WORKS", cfg == {'current': 'EN', 'supported': [], 'deploy': '', 'planned': [], 'maps': []},
      "current", str(cfg))

cfg, err = run_and_capture(
    'journey T\n    languages\n        current EN\n        supported klingon, emoji\n'
    '    languages: done\n    show "ok"\njourney: done\n')
check("supported (comma list) -- WORKS", cfg is not None and cfg['supported'] == ['klingon', 'emoji'],
      "supported", str(cfg))

cfg, err = run_and_capture(
    'journey T\n    languages\n        current klingon\n        deploy EN\n'
    '    languages: done\n    show "ok"\njourney: done\n')
check("deploy -- WORKS (one-way target, path-verified)", cfg is not None and cfg['deploy'] == 'EN',
      "deploy", str(cfg))

cfg, err = run_and_capture(
    'journey T\n    languages\n        current EN\n        planned nosuchlanguage\n'
    '    languages: done\n    show "ok"\njourney: done\n')
check("planned -- WORKS (informational, no pack required, no error)",
      err is None and cfg is not None and cfg['planned'] == ['nosuchlanguage'], "planned", str(err or cfg))

cfg, err = run_and_capture(
    'journey T\n    languages\n        primary EN\n    languages: done\n'
    '    show "ok"\njourney: done\n')
check("primary (alias for current) -- WORKS", cfg is not None and cfg['current'] == 'EN',
      "primary", str(cfg))
print()


# ═══════════════════════════════════════════════════════════════════════════
# STEP 2b -- SUB-BLOCKS (map / custom) -- parse+transform level
# ═══════════════════════════════════════════════════════════════════════════
print("=== SUB-BLOCKS ===")

def parse_transform(src):
    t = transform(P.parse(src), src)
    j = t.statements[0]
    return next(b for b in j.body if isinstance(b, LanguagesBlock))

lb = parse_transform(
    'journey T\n    languages\n        current EN\n        map\n'
    '            EN -> klingon using maps/en-klingon.langmap\n        map: done\n'
    '    languages: done\njourney: done\n')
check("map block (registry-shaped entry) -- PARSES-BUT-BROKEN "
      "(parses+transforms; content is a raw, untransformed Lark tree -- not consumed by "
      "the pack-enforcement check below except via best-effort language-code extraction)",
      len(lb.maps) == 1, "map_block", str(lb.maps))

lb2 = parse_transform(
    'journey T\n    languages\n        current klingon\n        deploy EN\n        custom\n'
    '            klingon -> EN using maps/en-klingon.langmap\n        custom: done\n'
    '    languages: done\njourney: done\n')
check("custom block (local path via `using`) -- PARSES-BUT-BROKEN (same shape as map block)",
      len(lb2.maps) == 1, "custom_block", str(lb2.maps))
print()


# ═══════════════════════════════════════════════════════════════════════════
# STEP 2c -- DIRECTIONALS
# ═══════════════════════════════════════════════════════════════════════════
print("=== DIRECTIONALS ===")
print("  (declaration-level -> / <-> already covered by the SUB-BLOCKS checks above; the "
      "chain form is checked next. This section's remaining checks are the PACK-FILE-"
      "internal directional forms -- a different grammar rule, map_alias_entry.)")

lb_chain = parse_transform(
    'journey T\n    languages\n        current EN\n        map\n'
    '            EN <-> PT <-> ES\n        map: done\n'
    '    languages: done\njourney: done\n')
check("<-> chain (EN <-> PT <-> ES) -- WORKS at the grammar/declaration level "
      "(parses+resolves to a 3-way chain entry; PT/ES packs do not exist in this repo, "
      "not evaluated for translation)",
      len(lb_chain.maps) == 1, "chain", str(lb_chain.maps))

# Pack-FILE-internal directionals -- the real LangmapLoader, real packs.
one_way = {k: v for k, v in _kl.forward.items() if k not in _kl.backward.values()}
def _real_entry_lines(path):
    """Non-comment, non-blank lines only -- the pack files' own header prose uses '->'
    in explanatory English sentences (e.g. "// -> means one direction"), which must not
    be mistaken for an actual one-way entry."""
    return [l for l in open(path, encoding='utf-8')
            if l.strip() and not l.strip().startswith('//')]

check("-> one-way in a REAL pack: klingon/emoji have NONE (both packs are 100% <->)",
      all('<->' in l for l in _real_entry_lines(KL_PATH) if '->' in l)
      and all('<->' in l for l in _real_entry_lines(EM_PATH) if '->' in l),
      "one-way-real-pack", "confirmed: every real entry line containing an arrow uses '<->'")

check("<-> bidirectional in a REAL pack: round-trips to identity (klingon)",
      _kl.translate(_kl.translate("sector", direction='forward'), direction='backward') == "sector",
      "bidir-real-pack")

# `->` one-way and `<-` back-arrow: NEITHER real pack uses these, so the mechanism itself
# is tested with a small synthetic fixture -- clearly not a real Klingon/Emoji vocabulary
# claim, testing LangmapLoader's actual (real, shipped) parsing code.
_fixture = 'alpha -> beta\ngamma <-> delta\nepsilon <- zeta\n'
with tempfile.NamedTemporaryFile(mode='w', suffix='.langmap', delete=False, encoding='utf-8') as f:
    f.write(_fixture)
    _fx_path = f.name
try:
    fx = LangmapLoader(_fx_path)
    check("-> one-way: forward translates, no reverse claim -- WORKS",
          fx.forward.get('alpha') == 'beta' and 'beta' not in fx.backward, "one-way-mechanism",
          f"forward={fx.forward} backward={fx.backward}")
    check("<-> bidirectional: both directions populated -- WORKS",
          fx.forward.get('gamma') == 'delta' and fx.backward.get('delta') == 'gamma',
          "bidir-mechanism")
    check("<- back-arrow (`epsilon <- zeta` means the reverse of `zeta -> epsilon`) -- WIRED: "
          "normalized on load to a one-way forward entry ('zeta' -> 'epsilon'), matching "
          "how it'd load if written the forward way. Forward-only, same as a plain '->' entry "
          "(no backward population -- a back-arrow entry is not bidirectional)",
          fx.forward.get('zeta') == 'epsilon' and 'epsilon' not in fx.backward
          and 'epsilon' not in fx.forward and 'zeta' not in fx.backward, "back-arrow",
          f"forward={fx.forward} backward={fx.backward}")
finally:
    os.unlink(_fx_path)

# Map modifiers -- WIRED: parsed as a distinct trailing flag, stored on the entry, and
# applied during source matching (ignore.case/match.case control case-sensitivity,
# keep.whitespace requires exact whitespace on a multi-word key).
_mod_fixture = 'theta <-> iota ignore.case\nkappa -> lambda match.case\n'
with tempfile.NamedTemporaryFile(mode='w', suffix='.langmap', delete=False, encoding='utf-8') as f:
    f.write(_mod_fixture)
    _mod_path = f.name
try:
    modfx = LangmapLoader(_mod_path)
    check("map modifiers -- WIRED: the modifier keyword is parsed OFF into a separate flag, "
          "not absorbed into the target value",
          modfx.forward.get('theta') == 'iota' and modfx.forward.get('kappa') == 'lambda',
          "modifiers-value",
          f"forward={modfx.forward} (expected clean values, no modifier text)")
    check("map modifiers -- WIRED: the flag is recorded on the entry",
          modfx.forward_modifiers.get('theta') == 'ignore.case'
          and modfx.forward_modifiers.get('kappa') == 'match.case', "modifiers-stored",
          f"forward_modifiers={modfx.forward_modifiers}")
    check("ignore.case -- WIRED: a case-mismatched source word still matches",
          modfx.translate("THETA Theta theta", direction='forward') == "iota iota iota",
          "modifiers-ignore-case")
    check("match.case -- WIRED: a case-mismatched source word does NOT match",
          modfx.translate("KAPPA kappa Kappa", direction='forward') == "KAPPA lambda Kappa",
          "modifiers-match-case")
finally:
    os.unlink(_mod_path)
print()


# ═══════════════════════════════════════════════════════════════════════════
# STEP 2d -- CLOSER
# ═══════════════════════════════════════════════════════════════════════════
print("=== CLOSER ===")
cfg, err = run_and_capture(
    'journey T\n    languages\n        current EN\n    languages: done\n'
    '    show "ok"\njourney: done\n')
check("named `languages: done` -- WORKS (Ronnie's ruling)", cfg is not None, "closer-named", str(cfg))

cfg, err = run_and_capture(
    'journey T\n    languages\n        current EN\n    done\n'
    '    show "ok"\njourney: done\n')
check("bare `done` -- WORKS (still supported)", cfg is not None, "closer-bare", str(cfg))
print()


# ═══════════════════════════════════════════════════════════════════════════
# STEP 2e -- THE SIMPLE MAP FLOW (core -- real Klingon/Emoji packs, real words only)
# ═══════════════════════════════════════════════════════════════════════════
print("=== SIMPLE MAP FLOW ===")

# Words actually in en-klingon.langmap: sector, financial, hold, check, when, above,
# otherwise, show, true, false. Unmapped words (amount, 100) fall back to English by
# design -- that is correct behavior for an incomplete pack, not a bug.
kl_src = ('sector: financial\n'
          'hold amount 100\n'
          'check amount\n'
          '    when amount above 50\n'
          '        show true\n'
          '    otherwise\n'
          '        show false\n'
          'check: done\n')
kl_out = _kl.translate(kl_src, direction='forward')
check("Klingon: words map per the pack's real entries",
      'yos' in kl_out and 'huch' in kl_out and 'pol' in kl_out and "ngu'" in kl_out
      and 'law' in kl_out and "'ang" in kl_out and "hija'" in kl_out,
      "klingon-translate", kl_out)
check("Klingon: unmapped words (amount, 100) fall back to English unchanged",
      'amount' in kl_out and '100' in kl_out, "klingon-fallback", kl_out)
kl_back = _kl.translate(kl_out, direction='backward')
check("Klingon: round-trip returns the identical original (deploy language clean & same)",
      kl_back == kl_src, "klingon-roundtrip",
      f"src={kl_src!r} back={kl_back!r}")

# Words actually in en-emoji.langmap: sector, financial, connect, hold, check, when,
# otherwise, show, true, false.
em_src = ('sector: financial\n'
          'connect db as sqlite from env.DATABASE_URL\n'
          'hold amount 100\n'
          'check amount\n'
          '    when amount is true\n'
          '        show true\n'
          '    otherwise\n'
          '        show false\n'
          'check: done\n')
em_out = _em.translate(em_src, direction='forward')
check("Emoji: words map per the pack's real entries",
      '🏷' in em_out and '💰' in em_out and '🔌' in em_out and '📌' in em_out
      and '🔎' in em_out and '⏰' in em_out and '👁' in em_out and '👍' in em_out,
      "emoji-translate", em_out)
em_back = _em.translate(em_out, direction='backward')
check("Emoji: BYTE-EXACT round-trip (the charset stress test -- exact bytes, not 'looks right')",
      em_back.encode('utf-8') == em_src.encode('utf-8'), "emoji-roundtrip-bytes",
      f"src_bytes={len(em_src.encode('utf-8'))} back_bytes={len(em_back.encode('utf-8'))}")
check("Emoji: final deploy-language text is clean and identical to the original",
      em_back == em_src, "emoji-clean-identical")
print()


# ═══════════════════════════════════════════════════════════════════════════
# STEP 2f -- COMPILER ENFORCEMENT
# ═══════════════════════════════════════════════════════════════════════════
print("=== COMPILER ENFORCEMENT ===")

cfg, err = run_and_capture(
    'journey T\n    languages\n        current EN\n        supported nosuchlanguageatall\n'
    '    languages: done\n    show "ok"\njourney: done\n')
check("supported with NO pack -> BUILD ERROR, named language + fallback -- WORKS "
      "(fixed this session; was silently accepted before)",
      err is not None and 'nosuchlanguageatall' in str(err).lower()
      and 'checked the registry and local packs' in str(err).lower()
      and 'render in' in str(err).lower(), "enforce-supported", str(err))

cfg, err = run_and_capture(
    'journey T\n    languages\n        current klingon\n        deploy nosuchlanguageatall\n'
    '    languages: done\n    show "ok"\njourney: done\n')
check("deploy with NO pack -> BUILD ERROR -- WORKS (fixed this session)",
      err is not None and 'nosuchlanguageatall' in str(err).lower(), "enforce-deploy", str(err))

cfg, err = run_and_capture(
    'journey T\n    languages\n        current EN\n        planned nosuchlanguageatall\n'
    '    languages: done\n    show "ok"\njourney: done\n')
check("planned with NO pack -> NO error -- WORKS (informational only, per spec)",
      err is None, "enforce-planned", str(err))

cfg, err = run_and_capture(
    'journey T\n    languages\n        current EN\n        supported klingon\n'
    '    languages: done\n    show "ok"\njourney: done\n')
check("registry-by-default: `supported klingon` resolves via the local maps/ dir -- WORKS "
      "(no network registry client exists in this codebase -- registry.mohio.io is "
      "documented intent, not built; 'registry lookup' is this local lookup today)",
      err is None, "registry-default", str(err))

cfg, err = run_and_capture(
    'journey T\n    languages\n        current EN\n        supported customlang\n'
    '        custom\n            customlang -> EN using maps/en-klingon.langmap\n        custom: done\n'
    '    languages: done\n    show "ok"\njourney: done\n')
check("custom-by-path: an explicit custom: entry counts as coverage without a "
      "locally-named pack -- WORKS", err is None, "custom-by-path", str(err))

print()
print("=== NOT covered: mio check-time static enforcement ===")
print("  This session's fix fires at RUN time (_exec_LanguagesBlock), not as a mio check-time")
print("  static scan. A file can still `mio check` clean and only fail when actually run/served.")
print("  Logged as a follow-on wiring task, not silently claimed as full compile-time coverage.")

print(f"\nRESULTS: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
