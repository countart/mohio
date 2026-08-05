# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""Layer 3 -- structural (word-order) adjustment.  SKELETON / POC.

WHAT THIS IS
------------
The translation pipeline is three layers:

    Layer 1 (words)  ->  Layer 3 (structure)  ->  Layer 2 (jobs/connectors)

Layer 3 runs BEFORE Layer 2 on purpose. Layer 2 decides a connector's job from
its POSITION relative to the verb (`on.click` = listener, `click.on` = actor --
one word, one job, different context). For that to be correct, the tokens must
already be in canonical order when Layer 2 reads them. So Layer 3's job is to
take a sentence written in another language's natural word order and permute it
into canonical Mohio order, and only then does Layer 2 read position to assign
the dot-job. That is why a front/back modifier can never be flipped by
reordering: its job is resolved AFTER reordering, from canonical position.

THE PROFILE SWITCH
------------------
A pack declares its word order in its header:

    word_order: SVO     # default -- English/Spanish/Portuguese. Layer 3 = no-op.
    word_order: SOV     # Hindi etc. Reorder declared constructs to canonical.

European packs set SVO (or omit it) and Layer 3 does nothing -- the "button"
is off. A pack like Hindi sets SOV and ships per-construct role orders, and the
button is on. This is the push-of-a-button switch: one header line flips Layer 3
from no-op to active for that pack, data-driven exactly like Layer 2.

BOUNDED-NATURAL, NOT FREE ORDER (v1)
------------------------------------
v1 does NOT parse arbitrary natural language. Each construct declares its role
order per profile; the engine permutes the matched roles back to canonical and
fails loud if the input does not match a declared order. This is a permutation
over a finite, declared construct set -- not NLP. Free word order is a later v2.

STATUS
------
NOT wired into the live preprocess pipeline. Standalone so it can be examined
and evolved with zero risk to the compiler gate. Wiring happens once the design
chat locks the [grammar] section format and the langmap chat authors real
per-language role data. The demo below uses a single real construct
(`retrieve <obj> from <source>`) to prove the mechanism end to end.
"""
from __future__ import annotations


class Layer3Error(Exception):
    """Raised when input roles do not match a construct's declared roles.

    Fail loud: a structural mismatch is a bug report, never a silent best-effort.
    """


# ---------------------------------------------------------------------------
# CANONICAL construct registry -- engine-owned, language-independent.
# This is the fixed Mohio role order for each construct. A pack never changes
# it; a pack only declares its OWN source order, and the engine permutes from
# source order to this canonical order.
# (Seeded with the data constructs; grows as Layer 3 covers more.)
# ---------------------------------------------------------------------------
CANONICAL_CONSTRUCTS = {
    "retrieve": ["VERB", "OBJECT", "SOURCE"],   # retrieve name from db.users
    "save":     ["VERB", "OBJECT", "TARGET"],   # save name to db.users
    "find":     ["VERB", "OBJECT", "SOURCE"],   # find user in db.users
    # Added 2026-06-29 (langmap chat request, Hindi SOV authoring):
    "listen":       ["VERB", "OBJECT", "LOCATION"],  # listen for sh.Order at /orders
    "new":          ["VERB", "OBJECT", "LOCATION"],  # new sh.Order at /orders
    "check":        ["VERB", "OBJECT"],              # check status
    "give back":    ["VERB", "OBJECT"],              # give back 200 "ok"
    "require role": ["VERB", "OBJECT"],              # require role "clinician"
}


def parse_grammar_section(text: str) -> dict:
    """Parse a langmap `[grammar]` section into a structural profile.

    Format (the locked v1 shape -- design ratifies, langmap authors):

        [grammar]
        word_order = SOV
        retrieve = OBJECT SOURCE VERB     # this language's source order
        save     = OBJECT TARGET VERB
        find     = OBJECT SOURCE VERB

    `word_order` is advisory/self-documenting; the actual reordering is driven
    by the per-construct source orders. A pack with `word_order = SVO` and no
    per-construct lines is a no-op (European packs). Lines outside the section,
    blanks, and `#`/`//` comments are ignored.
    """
    profile = {"word_order": "SVO", "constructs": {}}
    in_section = False
    for raw in text.splitlines():
        line = raw.split("//")[0].split("#")[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            in_section = (line[1:-1].strip().lower() == "grammar")
            continue
        if not in_section or "=" not in line:
            continue
        key, val = (p.strip() for p in line.split("=", 1))
        if key.lower() == "word_order":
            profile["word_order"] = val.upper()
        else:
            profile["constructs"][key.lower()] = val.split()
    return profile


def reorder_construct(construct_name: str,
                      tagged_segments: list[tuple[str, str]],
                      profile: dict) -> str:
    """Permute one construct's segments from source order to canonical order.

    tagged_segments arrive in SOURCE order (as the pack's word order produces
    them). Canonical order comes from CANONICAL_CONSTRUCTS. Fails loud if the
    role set does not match. SVO packs (no per-construct override) are a no-op.
    """
    canon = CANONICAL_CONSTRUCTS.get(construct_name)
    if canon is None:
        raise Layer3Error(f"unknown construct '{construct_name}' -- not in canonical registry")

    source_order = profile.get("constructs", {}).get(construct_name)
    if source_order is None or profile.get("word_order", "SVO") == "SVO":
        # No structural override for this construct: already canonical.
        return " ".join(surface for _role, surface in tagged_segments)

    roles_present = [role for role, _ in tagged_segments]
    if sorted(roles_present) != sorted(canon):
        raise Layer3Error(
            f"construct '{construct_name}': roles {roles_present} "
            f"do not match canonical roles {canon}. Layer 3 will not guess."
        )
    by_role = dict(tagged_segments)
    return " ".join(by_role[role] for role in canon)


def apply_layer3(construct_name: str,
                 tagged_segments: list[tuple[str, str]],
                 profile: dict) -> str:
    """Convenience wrapper -- reorder one construct under a parsed profile."""
    return reorder_construct(construct_name, tagged_segments, profile)


# ---------------------------------------------------------------------------
# Demo -- run:  PYTHONPATH=$PWD python3 mohio_layer3.py
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")

    # A pack's [grammar] section. European packs ship SVO (or nothing) -> no-op.
    # An SOV pack declares its per-construct source order. This is the locked v1
    # format the langmap chat will author Hindi into.
    SOV_PACK = """
    [grammar]
    word_order = SOV
    retrieve = OBJECT SOURCE VERB      // Hindi: object, source, verb-last
    """
    SVO_PACK = "[grammar]\nword_order = SVO\n"

    sov_profile = parse_grammar_section(SOV_PACK)
    svo_profile = parse_grammar_section(SVO_PACK)
    print("parsed SOV profile :", sov_profile)

    # SVO (European): segments already canonical -> no-op.
    svo_in = [("VERB", "retrieve"), ("OBJECT", "name"), ("SOURCE", "from db.users")]
    out_svo = reorder_construct("retrieve", svo_in, svo_profile)

    # SOV (Hindi-like): verb last. Layer 3 permutes back to canonical.
    sov_in = [("OBJECT", "name"), ("SOURCE", "from db.users"), ("VERB", "retrieve")]
    out_sov = reorder_construct("retrieve", sov_in, sov_profile)

    print("SVO input order    :", [r for r, _ in svo_in], "-> ", out_svo, "(no-op)")
    print("SOV input order    :", [r for r, _ in sov_in], "->", out_sov, "(reordered)")
    print("converge           :", out_svo == out_sov)

    # Prove AST-identity: both orders produce the SAME canonical string, so the
    # parser yields the SAME AST. Two word orders, one canonical program.
    try:
        from lark import Lark
        from mohio_transformer_ast import transform
        raw = open("mohio.lark", encoding="utf-8").read()
        g = "\n".join(l for l in raw.splitlines() if not l.strip().startswith("//"))
        P = Lark(g, parser="earley", ambiguity="resolve", propagate_positions=True)
        a = repr(transform(P.parse(out_svo + "\n"), out_svo))
        b = repr(transform(P.parse(out_sov + "\n"), out_sov))
        print("AST identical      :", a == b)
    except Exception as e:
        print("AST check skipped  :", str(e).splitlines()[-1][:50])

    # Fail-loud: an SOV input missing a role is a bug report, not a guess.
    try:
        reorder_construct("retrieve", [("OBJECT", "name"), ("VERB", "retrieve")], sov_profile)
        print("fail-loud          : MISSED (should have raised)")
    except Layer3Error as e:
        print("fail-loud          :", str(e)[:58], "...")
