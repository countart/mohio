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
VERSION = "4.9.0"

# The LANGUAGE spec this build implements. A different thing, deliberately: the toolchain
# ships faster than the language changes.
LANGUAGE_VERSION = "v3.8 -- Phase 1"


def _resolve_build_sha():
    """The exact COMMIT this build was made from. A different job from VERSION, and the reason
    this exists (2026-08-20): VERSION had not moved in six commits, so `/ping` reported the same
    "4.8.2" for production and for a local checkout six commits ahead. Asking "which build is
    running" got an answer that could not distinguish them -- the same class of failure this
    module's own docstring was written about, one level down. VERSION answers "which release";
    BUILD_SHA answers "which build", and a deploy question needs the second one.

    Resolution order, most authoritative first:
      1. MOHIO_BUILD_SHA -- stamped by the deploy pipeline. The only one that survives into a
         container built without a .git directory, which is the case that matters in production.
      2. _build_sha.txt beside this file -- written at package time for the same reason.
      3. `git rev-parse --short HEAD` -- a developer running from a checkout.
      4. "unknown" -- said plainly. Never fabricated, never silently blank: a caller can tell the
         difference between "this build is X" and "this build did not record which commit it is,"
         and the second is actionable (the pipeline is not stamping it).
    """
    import os
    sha = (os.environ.get('MOHIO_BUILD_SHA') or '').strip()
    if sha:
        return sha
    try:
        from pathlib import Path as _Path
        _stamp = _Path(__file__).with_name('_build_sha.txt')
        if _stamp.is_file():
            sha = _stamp.read_text(encoding='utf-8').strip()
            if sha:
                return sha
    except Exception:
        pass
    try:
        import subprocess as _sp
        from pathlib import Path as _Path
        out = _sp.run(['git', 'rev-parse', '--short', 'HEAD'],
                      cwd=str(_Path(__file__).parent), capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        # No git, no checkout, or git refused. Not an error: this build simply cannot say.
        pass
    return "unknown"


# Resolved ONCE at import. A server answers /ping thousands of times; shelling out to git per
# request would be absurd, and the commit cannot change under a running process anyway.
BUILD_SHA = _resolve_build_sha()
