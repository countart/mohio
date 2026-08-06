# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
test_seed_schema_and_dotted.py

Covers the autonomous round:
  1. Seed schema reconcile: an existing narrow table is widened from the union of all
     seed rows (a __schema_template__ row can carry the wide schema), and the sentinel
     is not saved as data.
  2. Sector recognition via the loader (single source): a loaded profile is known and
     carries its declared tier; an unknown base is not known.
  3. Dotted-verb fail-loud: a MioQL data verb used as a dotted service (save.do,
     find.by) fails loud; real services and blocks are unaffected.

Run: PYTHONPATH=$PWD DATABASE_URL=:memory: python3 tests/test_seed_schema_and_dotted.py
 or: PYTHONPATH=$PWD python3 -m pytest tests/test_seed_schema_and_dotted.py -q
"""
import os, sys
os.environ.setdefault("DATABASE_URL", ":memory:")

from lark import Lark
from mohio_transformer_ast import transform, MohioCompileError
from mohio_interpreter import MohioInterpreter, DbRuntime
from mohio_sector_loader import is_known_sector_base, sector_tier
import mohio_data

_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = "\n".join(l for l in _raw.splitlines() if not l.strip().startswith("//"))
_P = Lark(_g, parser="earley", ambiguity="resolve", propagate_positions=True)


# ── 1. seed schema reconcile ─────────────────────────────────────────────────

def test_narrow_table_widens_on_reseed_and_sentinel_filtered():
    interp = MohioInterpreter(); interp._db = DbRuntime(":memory:")
    # existing narrow table (the live-DB state that used to 500 on the first write)
    interp._db.ensure_table("saved_games", ["session_id", "current_room"])
    seed = {"saved_games": [
        {"session_id": "game1", "current_room": "kitchen"},
        {"session_id": "__schema_template__", "current_room": "", "score": "0",
         "window_open": "false", "cyclops_fled": "false"},  # wide template, not first
    ]}
    interp.seed_db(seed)
    cols = {r[1] for r in interp._db.conn.execute('PRAGMA table_info("saved_games")')}
    for need in ("window_open", "cyclops_fled", "score"):
        assert need in cols, f"column {need} was not reconciled: {sorted(cols)}"
    # the wide write must not crash
    interp._db.save("saved_games", {"session_id": "game2", "current_room": "attic",
                                    "window_open": "true", "score": "10"})
    # the sentinel must not be a selectable saved game
    sids = [dict(r)["session_id"] for r in
            interp._db.conn.execute('SELECT session_id FROM "saved_games"')]
    assert "__schema_template__" not in sids, sids
    assert "game1" in sids and "game2" in sids, sids


# ── 2. sector recognition via the loader ─────────────────────────────────────

def test_loader_is_single_source_for_sector_recognition():
    assert is_known_sector_base("demo_low") is True     # has a profile file
    assert is_known_sector_base("financial") is True    # paid/enterprise base
    assert is_known_sector_base("madeupthing") is False # neither
    assert sector_tier("demo_low") == "community"       # declared tier
    assert sector_tier("madeupthing") is None           # no profile


# ── 3. dotted-verb fail-loud ─────────────────────────────────────────────────

FAKE = ["save.do to db.users\n", "find.by x\n", "retrieve.one x\n",
        "update.set x\n", "create.new x\n"]
REAL = ['miohttp.get "https://x.com/y"\n', "miomail.send\n", 'miolog.info "hi"\n',
        'miocache.set "k" to "v"\n']


def test_data_verb_as_dotted_service_fails_loud():
    for src in FAKE:
        try:
            transform(_P.parse(src), src)
        except MohioCompileError as e:
            assert "not a valid form" in str(e) or "block verb" in str(e), str(e)
        else:
            raise AssertionError(f"fake dotted verb did not fail loud: {src!r}")


def test_real_services_still_compile():
    for src in REAL:
        transform(_P.parse(src), src)   # must not raise


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"  PASS {name}")
            except Exception as e:
                failed += 1; print(f"  FAIL {name}: {e}")
    print(f"\n{'ALL PASS' if not failed else str(failed) + ' FAILED'}")
    sys.exit(1 if failed else 0)
