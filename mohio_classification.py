# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""The classification resolver: one place that answers what a field is classified as.

P7. Classification used to be five sets of BARE FIELD NAMES -- `_encrypted_fields`,
`_pci_fields`, `_phi_fields`, `_pii_fields`, `_never_store_fields` -- and a bare name is not an
identity. That is Q533, and it was reproduced through `mio run` against a real SQLite file read
back raw before any of this was written:

    shape Store
        customers as table
            email as text [pii]
        vendors as table
            email as text          <- no tag at all
    shape: done

    customers  ENCRYPTED  enc:v1:A804HhxesVncgcU5bBVAWhyaBtcdxFEFNgVVV
    vendors    ENCRYPTED  enc:v1:0CTmWjGCAmfPUBKyu6MJzZmZw1tNOMB5zdfix

`vendors.email` borrowed a classification nobody gave it, because the set held the string
`email` and nothing else. The false positive is the visible half; the false NEGATIVE is the same
mechanism pointing the other way, and it is the one that leaks.

THE RESOLUTION RULE, and every part of it exists to make this additive rather than merely
correct:

  1. A field declared under `<name> as table` registers QUALIFIED, as (table, field), and NOT
     bare. This is what kills Q533: `vendors.email` has no qualified entry of its own, and
     `customers.email`'s entry cannot be reached by a different table's name.
  2. A field declared loose, with no table, registers BARE, exactly as before. Every existing
     program is this case -- zero `.mho` files in the repo use `as table`, measured -- so level 1
     resolves identically to how it always has, by construction rather than by hope. That is
     what protects Zork.
  3. A lookup that KNOWS its table answers from the qualified entries for that table, then from
     the bare entries.
  4. A lookup that does NOT know its table falls back to the UNION over every table. This is
     the one that has to be argued rather than assumed: the union is exactly today's behaviour,
     so a call site that cannot say which table it is writing to is no worse than it was, and
     never fails open. A resolver that answered "unclassified" there would turn a missing
     argument into a field that silently stopped being encrypted, which is the failure this
     whole module exists to prevent, arriving through the front door.

So the guarantee is: knowing the table can only ever make the answer MORE precise, never less
protective. The write path knows its table, which is where Q533 actually dies.

WHAT CARRIES ENFORCEMENT TODAY is three classifiers, and the list is here rather than spread
across the interpreter and the check-time scanner so the two cannot disagree about what a
classifier is.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Optional, Set, Tuple

# The three that do something. Each has real behaviour behind it: all three encrypt at rest,
# pci additionally masks to last 4 on output, phi carries audit-on-access, pii carries its own
# audit coverage.
ENFORCED_CLASSIFIERS = ('phi', 'pii', 'pci')

# Recognized but carrying no enforcement yet. They are not warned about, because a warning
# saying "this does nothing" would be read as "do not use this", and whether they become tags or
# a `sec.classify` level is an open catalog question rather than a mistake the developer made.
# `public` is here because a shipped sector profile already classifies a field with it
# (mohio_data/sectors/sector-demo-regulated.sector: `region is [public]`).
RESERVED_CLASSIFIERS = ('confidential', 'classified', 'public')

# THE TWO THAT ARE NOT TAGS AT ALL. `confidential` and `classified` are `sec.classify`
# LEVEL names, and a shape field tag never invokes sec.classify, so one written in brackets
# on a field reads like protection and applies none. They stay RECOGNIZED, because the word
# is real and saying otherwise would be wrong; what is wrong is the position, so the check
# gives them their own message pointing at the construct that does what they were reaching
# for. `public` is not here: it is a genuine classifier and a shipped sector profile uses it.
SEC_CLASSIFY_LEVEL_WORDS = frozenset(('confidential', 'classified'))

# `identifier` is deliberately ABSENT. The grammar comment at mohio.lark's TAG_REF still lists
# it as an example, and nothing anywhere reads it, so it is dropped rather than left looking
# like one of the real ones.
RECOGNIZED_CLASSIFIERS = frozenset(ENFORCED_CLASSIFIERS + RESERVED_CLASSIFIERS)

# The non-classifier kinds that ride the same identity. They are not tags a developer writes in
# brackets; they are the other per-field rules that were also keyed by bare name and so had the
# same borrowing bug.
OTHER_KINDS = ('encrypted', 'never_store')

ALL_KINDS = tuple(ENFORCED_CLASSIFIERS) + OTHER_KINDS


class ClassificationResolver:
    """What a field is classified as, keyed by identity rather than by name."""

    def __init__(self):
        # (table, field) -> {kind}
        self._qualified: Dict[Tuple[str, str], Set[str]] = {}
        # field -> {kind}, for a field declared with no table
        self._bare: Dict[str, Set[str]] = {}
        # field -> {kind}, the union across every table that qualifies it. Maintained here
        # rather than computed per lookup because the lookup is on the write path.
        self._union: Dict[str, Set[str]] = {}

    # ── registration ─────────────────────────────────────────────────────────────────────────
    def register(self, field: str, kind: str, table: Optional[str] = None) -> None:
        field = str(field)
        kind = str(kind)
        if table:
            self._qualified.setdefault((str(table), field), set()).add(kind)
            self._union.setdefault(field, set()).add(kind)
        else:
            self._bare.setdefault(field, set()).add(kind)

    # ── lookup ───────────────────────────────────────────────────────────────────────────────
    def kinds(self, field: str, table: Optional[str] = None) -> FrozenSet[str]:
        """Every kind attached to this field, as precisely as the caller allowed."""
        field = str(field)
        found: Set[str] = set()
        if table:
            found |= self._qualified.get((str(table), field), set())
        else:
            # No table: the union, which is what the bare-name sets always answered. See rule 4.
            found |= self._union.get(field, set())
        found |= self._bare.get(field, set())
        return frozenset(found)

    def has(self, field: str, kind: str, table: Optional[str] = None) -> bool:
        return kind in self.kinds(field, table)

    def any_field_has(self, kind: str) -> bool:
        """Is this kind registered anywhere at all? The cheap guard before a per-field scan."""
        for kinds in self._qualified.values():
            if kind in kinds:
                return True
        for kinds in self._bare.values():
            if kind in kinds:
                return True
        return False

    def fields_with(self, kind: str, table: Optional[str] = None):
        """Every field name carrying `kind`, for a message that has to list them."""
        out = set()
        for (tbl, field), kinds in self._qualified.items():
            if kind in kinds and (table is None or tbl == str(table)):
                out.add(field)
        for field, kinds in self._bare.items():
            if kind in kinds:
                out.add(field)
        return sorted(out)

    # ── a view of what is registered, for diagnostics and for tests ──────────────────────────
    def identities(self):
        """Every (table, field) and (None, field) this resolver knows, with its kinds."""
        rows = [((tbl, fld), sorted(kinds)) for (tbl, fld), kinds in self._qualified.items()]
        rows += [((None, fld), sorted(kinds)) for fld, kinds in self._bare.items()]
        return sorted(rows, key=lambda r: (r[0][0] or "", r[0][1]))
