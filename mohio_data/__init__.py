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
# The built-in AI provider routing table: name -> {api_format, base_url, env_key}. Shipped as a
# data file (not hardcoded in Python) so it can be refreshed on a tag without a code change, and so
# the model registry treadmill stays out of the compiler -- it carries endpoints and env-key names,
# never model strings. Read with json.loads(PROVIDERS_PATH.read_text()).
PROVIDERS_PATH = _ROOT / "providers.json"
# MioScript `share VALUE on DESTINATION`: destination name -> that site's share-page URL
# template, with {url} as the placeholder for the (URL-encoded) shared value. Data, not
# hardcoded in the emitter, so a new destination is a one-line addition here, and an unknown
# one is checked against this file rather than a list buried in Python.
SHARE_DESTINATIONS_PATH = _ROOT / "share_destinations.json"
# The diagnostic-code registry: one entry per stable warning or error code (type, class, whether it
# may be suppressed, message, fix). The getmohio link and the suppressible codes derive from it.
DIAGNOSTICS_PATH = _ROOT / "diagnostics.json"
