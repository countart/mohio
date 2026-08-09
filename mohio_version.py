# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""The version. ONE source.

`mio serve` bannered v0.3.8 while /mio/health reported "version": "0.4.4" -- two
hardcoded strings in one binary, in two files, already disagreeing. A version that
disagrees with itself is worse than no version: it makes a deploy unprovable. You cannot
answer "what is running in production" from an artifact that gives two answers.

Everything imports from here. Nothing hardcodes a version string anywhere else, and
tests/test_structural_invariants.py fails loud if anything starts to.
"""

# The compiler/CLI release.
VERSION = "4.8.2"

# The LANGUAGE spec this build implements. A different thing, deliberately: the toolchain
# ships faster than the language changes.
LANGUAGE_VERSION = "v3.8 -- Phase 1"
