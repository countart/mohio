# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
mohio_enforce.py
THE SINGLE SOURCE OF TRUTH FOR WHAT THE COMPILER ENFORCES.

Mohio enforces rules in three layers. They are NOT interchangeable -- each one needs data the
others do not have:

    Layer 1  validate()   mohio_transformer.py       raw parse tree: tokens, positions, text
    Layer 2  transform()  mohio_transformer_ast.py   building the AST: shape of each construct
    Layer 3  scan_*()     mohio_reachability.py      the whole program: what is declared where

You cannot collapse them (an "undeclared task" rule needs every task in the file; a "retired
keyword" rule needs the raw token). But there must be exactly ONE DOOR into them, or they
drift apart -- which is precisely what happened:

    `mio check` ran all three.  The GATE ran only validate().

So the gate -- the thing we treat as sacred -- was blind to 25 transformer guards and 7
scanners. A rule retired in one layer stayed alive in another, and nobody could see it. That
is not a bug in any layer. It is a bug in having three front doors.

Everything that checks Mohio calls `enforce()`. Nothing calls a layer directly.
"""

from __future__ import annotations



# ============================================================================
# RULE CHANGES REQUIRE RONNIE'S APPROVAL. NO EXCEPTIONS.
#
# Enforcement rules and grammar are the language. A wrong rule does not fail
# loud - it BECOMES the truth and everything drifts to match it.
#
# Before you add, change, or retire a rule you must CITE a source: Ronnie's
# explicit ruling, a design decision found via conversation_search, or a working
# .mho in the repo that you RAN. If you cannot cite one, you are writing from
# memory. Stop, say "unverified", and ask Ronnie.
#
# Go through the door: mohio_enforce.enforce(). See TESTING.md and DRIFT.md.
# ============================================================================

def enforce(tree, source: str, filename: str = "", *, build_ast: bool = True, scan: bool = True):
    """Run every enforcement layer, in order, and return everything they found.

    Returns (ctx, program):
        ctx      -- the validation context; `ctx.errors` and `ctx.warnings` hold everything
                    found by ALL layers (a transformer guard is recorded as an error here,
                    not raised, so one bad construct does not hide the rest of the file).
        program  -- the AST, or None if it could not be built.

    Callers decide what to do with errors. This function decides WHAT THE RULES ARE.

    build_ast=False  -- stop after Layer 1 (parse-tree validation only). No AST, no scans.
    scan=False       -- run Layers 1 and 2 (validate + build AST) but NOT the whole-program
                        scanners. This is for callers that must ASSEMBLE the program first
                        (resolve includes, merge a journey spine) so the Layer-3 scanners see
                        every declaration across files. Those callers MUST finish with
                        `enforce_scans(ctx, assembled_program)` -- they still go through this
                        door for Layer 3, they just do it after assembly. They never call the
                        scanners directly.
    """
    from mohio_transformer import validate

    # ── Layer 1: parse-tree validation ────────────────────────────────────
    # Needs raw tokens (e.g. a retired `set` keyword is a TOKEN, not an AST node).
    ctx = validate(tree, source=source, filename=filename)

    program = None
    if not build_ast:
        return ctx, program

    # ── Layer 2: AST construction ─────────────────────────────────────────
    # Guards that can only be seen while assembling a construct (a leading `if`, an unclosed
    # `task`, a closer that names a result). These RAISE, so record and stop the AST here.
    try:
        from mohio_transformer_ast import transform, MohioCompileError
        program = transform(tree, source)
    except Exception as e:
        _record(ctx, e)
        return ctx, None

    if not scan:
        # Caller will assemble (includes/journey) then call enforce_scans(ctx, program).
        return ctx, program

    # ── Layer 3: whole-program scanners ───────────────────────────────────
    enforce_scans(ctx, program)
    return ctx, program


def enforce_scans(ctx, program):
    """Layer 3 -- the whole-program scanners -- as a separate entry so callers that must
    assemble the program first (includes, journey spine) still go through THIS door for the
    scanners rather than importing run_scans themselves.

    Rules that need the entire file: is that type declared? that task? that connector?

    ONE canonical list, defined next to the scanners in mohio_reachability. This function
    used to keep its own copy, and `mio.py check` kept ANOTHER -- so a rule added here
    silently did not exist there. A list that does not name a thing does not fail; it
    silently does nothing.
    """
    if program is None:
        return ctx
    from mohio_reachability import run_scans
    scan_errors, scan_warnings = run_scans(program)
    ctx.errors.extend(scan_errors)
    ctx.warnings.extend(scan_warnings)
    return ctx


def _record(ctx, exc):
    """Fold a raised transformer guard into the same error list as everything else, so callers
    see ONE list and never have to know which layer spoke."""
    from mohio_reachability import CompileError
    msg = getattr(exc, "message", None) or str(exc)
    line = getattr(exc, "line", 0) or 0
    ctx.errors.append(CompileError(msg.strip(), line=line))


def has_errors(ctx) -> bool:
    return bool(getattr(ctx, "errors", None))
