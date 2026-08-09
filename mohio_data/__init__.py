# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Canonical location of Mohio's shipped resource files: the grammar, the built-in
sector profiles, and the language maps. Everything here is resolved relative to this
package's own installed location, so it works identically whether mohio is running
from a repo checkout or from a pip-installed wheel -- there is exactly one copy of
each resource file, this package is where it lives.
"""
from pathlib import Path

_ROOT = Path(__file__).resolve().parent

GRAMMAR_PATH = _ROOT / "mohio.lark"
SECTORS_DIR = _ROOT / "sectors"
MAPS_DIR = _ROOT / "maps"
