# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""mio harvest -- emits the current word inventory from mohio.lark.

ONE job: read the authoritative grammar (not documents) and emit every reserved
word with terminal, category, def_line, example rule, and status. Feeds the
langmap tooling. Uniform schema across all entries; retired status is detected
from *_retired* rule names (e.g. MAKE -> make_retired_block). Verified by
RUNNING harvest and checking the output.
"""
import os, sys, json, tempfile
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import mio

_passed = _failed = 0


def check(label, got, expected):
    global _passed, _failed
    if got == expected:
        _passed += 1
        print(f"  [PASS] {label}: {got!r}")
    else:
        _failed += 1
        print(f"  [FAIL] {label}: got {got!r}, expected {expected!r}")


def harvest():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        mio.cmd_harvest(SimpleNamespace(output=path, stdout=False))
        return json.loads(Path(path).read_text(encoding="utf-8"))
    finally:
        os.unlink(path)


def test_harvest():
    print("\n=== mio harvest: current word inventory from the grammar ===")
    d = harvest()
    check("emits a substantial list", len(d) > 100, True)
    check("schema uniform across all entries",
          len(set(tuple(sorted(e.keys())) for e in d)) == 1, True)
    check("expected keys", sorted(d[0].keys()),
          ['alias', 'category', 'def_line', 'example_rule',
           'invariant_prefix', 'map_label', 'note', 'status', 'terminal', 'word'])
    by_word = {e['word']: e for e in d}
    check("create present + canonical",
          by_word.get('create', {}).get('status'), 'canonical')
    check("make present + retired (via make_retired_block)",
          by_word.get('make', {}).get('status'), 'retired')
    check("grab present + canonical (distinct verb, not an alias)",
          by_word.get('grab', {}).get('status'), 'canonical')
    check("get present + canonical",
          by_word.get('get', {}).get('status'), 'canonical')
    check("write freed (no longer reserved)", 'write' in by_word, False)
    check("ai.create carries invariant_prefix 'ai.'",
          by_word.get('ai.create', {}).get('invariant_prefix'), 'ai.')
    check("def_line is an int", isinstance(by_word['create']['def_line'], int), True)
    check("editorial fields blank for langmap pass",
          (by_word['create']['alias'], by_word['create']['map_label'],
           by_word['create']['note']), ('', '', ''))


if __name__ == "__main__":
    test_harvest()
    print(f"\nRESULTS: {_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
