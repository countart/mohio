#!/usr/bin/env python3
"""
mio — Mohio Language CLI
Phase 1: run, check

Usage:
    mio run <file.mho> [--verbose] [--request <json>]
    mio check <file.mho>
    mio version
    mio help

Exit codes:
    0  success
    1  compile error (closer mismatch, missing not_confident, etc.)
    2  runtime error
    3  file not found or unreadable
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
import traceback
from pathlib import Path

# ── Path setup ───────────────────────────────────────────────────────────────
# Allow running as  python mio.py  from any directory.
# The compiler files (mohio.lark, mohio_ast.py, etc.) live alongside mio.py.
_HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(_HERE))

GRAMMAR_FILE = _HERE / "mohio.lark"

# ── Version ───────────────────────────────────────────────────────────────────
VERSION = "0.1.0"
LANGUAGE_VERSION = "Phase 1"

# ── Colour helpers ────────────────────────────────────────────────────────────
# Simple ANSI — degrades cleanly on terminals that don't support it.
_USE_COLOUR = sys.stdout.isatty() and os.name != "nt"  # off on Windows cmd

def _c(code: str, text: str) -> str:
    if not _USE_COLOUR:
        return text
    return f"\033[{code}m{text}\033[0m"

def red(t):    return _c("31", t)
def yellow(t): return _c("33", t)
def green(t):  return _c("32", t)
def bold(t):   return _c("1",  t)
def dim(t):    return _c("2",  t)
def cyan(t):   return _c("36", t)


# ── Grammar loader ────────────────────────────────────────────────────────────

def _load_grammar() -> str:
    if not GRAMMAR_FILE.exists():
        _die(
            f"Grammar file not found: {GRAMMAR_FILE}\n"
            f"Make sure mohio.lark is in the same directory as mio.py.",
            exit_code=3,
        )
    raw = GRAMMAR_FILE.read_text(encoding="utf-8")
    # Strip // line comments — valid in .mho source but not in .lark files
    lines = [l for l in raw.splitlines() if not l.strip().startswith("//")]
    return "\n".join(lines)


def _make_parser(grammar: str):
    from lark import Lark
    return Lark(grammar, parser="earley", ambiguity="resolve",
                propagate_positions=True)


# ── Error formatting ──────────────────────────────────────────────────────────
# All errors follow Mohio's error philosophy:
#   calm, specific, actionable — never just a code, never just a line number.

def _print_compile_error(e, source: str = "", filename: str = ""):
    """Format a MohioCloserError or MohioCompileError for the terminal."""
    header = bold(red("Compile error")) + (f"  {dim(filename)}" if filename else "")
    print(f"\n{header}\n")

    msg = str(e).strip()

    # Extract line context if we have source and a line number
    line_no = getattr(e, "line", 0) or getattr(e, "close_line", 0)
    if line_no and source:
        lines = source.splitlines()
        if 1 <= line_no <= len(lines):
            snippet = lines[line_no - 1]
            print(f"  {dim(str(line_no) + ' │')} {snippet}")
            print()

    # Print the message — already formatted by the error class
    for line in msg.splitlines():
        print(f"  {line}")
    print()


def _print_parse_error(e, source: str = "", filename: str = ""):
    """Format a Lark parse error."""
    from lark.exceptions import UnexpectedInput
    header = bold(red("Syntax error")) + (f"  {dim(filename)}" if filename else "")
    print(f"\n{header}\n")

    line = getattr(e, "line", None)
    col  = getattr(e, "column", None)

    if line and source:
        lines = source.splitlines()
        if 1 <= line <= len(lines):
            snippet = lines[line - 1]
            print(f"  {dim(str(line) + ' │')} {snippet}")
            if col:
                print(f"  {dim('  │')} {' ' * (col - 1)}{red('^')}")
            print()

    # Lark's message is usually helpful — include it
    msg = str(e).split("\n")[0][:120]
    print(f"  {msg}")
    print()


def _print_runtime_error(e, filename: str = ""):
    """Format a runtime _Raise signal or unexpected exception."""
    header = bold(red("Runtime error")) + (f"  {dim(filename)}" if filename else "")
    print(f"\n{header}\n")
    print(f"  {str(e)}")
    print()


def _die(message: str, exit_code: int = 1):
    print(f"\n{bold(red('Error'))}  {message}\n", file=sys.stderr)
    sys.exit(exit_code)


# ── Result formatting ─────────────────────────────────────────────────────────

def _print_result(result, verbose: bool = False):
    """Print the final give back value in a readable way."""
    if result is None:
        if verbose:
            print(dim("  (no response — program completed without give back)"))
        return

    if isinstance(result, dict):
        status = result.get("status", "")
        body   = result.get("body", "")

        # Colour the status code
        if isinstance(status, int):
            if status < 300:
                status_str = green(str(status))
            elif status < 400:
                status_str = yellow(str(status))
            else:
                status_str = red(str(status))
        else:
            status_str = str(status)

        print(f"\n  {bold('Response')}  {status_str}  {body}")
    else:
        print(f"\n  {bold('Result')}  {result}")
    print()


# ── mio run ───────────────────────────────────────────────────────────────────

def cmd_run(args):
    filename = args.file
    verbose  = args.verbose
    request  = None

    # Parse request — three ways to supply it, in priority order:
    #   1. --request-file <file.json>   reads from a file (works on all platforms)
    #   2. --request <json>             inline JSON (use single quotes on Windows PS)
    #   3. --param key=value            repeatable key=value pairs (works on Windows CMD)
    if args.request_file:
        rpath = Path(args.request_file)
        if not rpath.exists():
            _die(f"Request file not found: {args.request_file}", exit_code=3)
        try:
            request = json.loads(rpath.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            _die(f"Request file is not valid JSON: {e}")
    elif args.request:
        try:
            request = json.loads(args.request)
        except json.JSONDecodeError as e:
            _die(
                f"--request must be valid JSON: {e}\n\n"
                f"  On Windows CMD, use --request-file instead:\n"
                f"    mio run file.mho --request-file request.json\n\n"
                f"  Or use --param for individual fields:\n"
                f"    mio run file.mho --param _shape=Transaction --param amount=500\n\n"
                f"  On PowerShell, single-quote the JSON:\n"
                f"    mio run file.mho --request \'{{...}}\'",
            )
    elif args.param:
        request = {}
        for p in args.param:
            if "=" not in p:
                _die(f"--param must be key=value, got: {p!r}")
            k, _, v = p.partition("=")
            # Auto-coerce obvious types
            if v.lstrip("-").isdigit():
                v = int(v)
            elif v.replace(".", "", 1).lstrip("-").isdigit():
                v = float(v)
            elif v.lower() == "true":
                v = True
            elif v.lower() == "false":
                v = False
            elif v.startswith("[") or v.startswith("{"):
                try:
                    v = json.loads(v)
                except json.JSONDecodeError:
                    pass  # keep as string
            request[k] = v

    # Load source file
    path = Path(filename)
    if not path.exists():
        _die(f"File not found: {filename}", exit_code=3)
    if path.suffix not in (".mho", ""):
        print(yellow(f"  Warning: expected .mho file, got {path.suffix}"))

    source = path.read_text(encoding="utf-8")

    if verbose:
        print(dim(f"\n  Loading {filename} ({len(source.splitlines())} lines)"))

    # ── 1. Parse ─────────────────────────────────────────────────────────────
    try:
        grammar = _load_grammar()
        parser  = _make_parser(grammar)
        tree    = parser.parse(source)
    except Exception as e:
        from lark.exceptions import UnexpectedInput
        if isinstance(e, UnexpectedInput):
            _print_parse_error(e, source, filename)
            sys.exit(1)
        _die(f"Parse failed: {e}", exit_code=1)

    if verbose:
        from mio_utils import tree_depth
        print(dim(f"  Parsed — tree depth {tree_depth(tree)}"))

    # ── 2. Transform ─────────────────────────────────────────────────────────
    try:
        from mohio_transformer import transform, MohioCloserError, MohioCompileError
        from lark.exceptions import VisitError
        program = transform(tree, source)
    except VisitError as ve:
        real = ve.__context__
        from mohio_transformer import MohioCloserError, MohioCompileError
        if isinstance(real, (MohioCloserError, MohioCompileError)):
            _print_compile_error(real, source, filename)
            sys.exit(1)
        _die(f"Transform failed: {real}", exit_code=1)
    except Exception as e:
        from mohio_transformer import MohioCloserError, MohioCompileError
        if isinstance(e, (MohioCloserError, MohioCompileError)):
            _print_compile_error(e, source, filename)
            sys.exit(1)
        _die(f"Transform failed: {e}", exit_code=1)

    if verbose:
        print(dim(f"  Transformed — {len(program.statements)} top-level statements"))

    # ── 3. Execute ───────────────────────────────────────────────────────────
    try:
        from mohio_interpreter import MohioInterpreter, MockAiRuntime

        # Select AI runtime
        if args.ai or args.api_key:
            try:
                from mohio_ai import AnthropicAiRuntime
            except ImportError:
                _die(
                    "The Anthropic SDK is not installed.\n\n"
                    "  Run:  pip install anthropic\n\n"
                    "  Then retry:  mio run <file> --ai"
                )
            try:
                ai = AnthropicAiRuntime(
                    api_key=args.api_key,
                    verbose=verbose,
                )
                if verbose:
                    print(dim(f"  AI runtime: Anthropic API ({ai._model})"))
            except RuntimeError as e:
                _die(str(e))
        else:
            ai = MockAiRuntime()
            if verbose:
                print(dim("  AI runtime: mock (use --ai for real Anthropic API)"))

        interp = MohioInterpreter(ai=ai, verbose=verbose)

        # Load seed data if provided
        seed_data = None
        if args.seed:
            seed_path = Path(args.seed)
            if not seed_path.exists():
                _die(f"Seed file not found: {args.seed}", exit_code=3)
            try:
                seed_data = json.loads(seed_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                _die(f"Seed file is not valid JSON: {e}")
            if verbose:
                tables = list(seed_data.keys())
                rows = sum(len(v) for v in seed_data.values())
                print(dim(f"  Seed data: {rows} rows across {tables}"))

        interp.setup_test_db(seed_data=seed_data)
        result = interp.run(program, request=request)
    except Exception as e:
        _print_runtime_error(e, filename)
        if verbose:
            traceback.print_exc()
        sys.exit(2)

    _print_result(result, verbose)


# ── mio check ────────────────────────────────────────────────────────────────

def cmd_check(args):
    """Parse and transform only — no execution. Reports all errors and exits."""
    filename = args.file
    path = Path(filename)

    if not path.exists():
        _die(f"File not found: {filename}", exit_code=3)

    source = path.read_text(encoding="utf-8")
    errors = []

    # Parse
    try:
        grammar = _load_grammar()
        parser  = _make_parser(grammar)
        tree    = parser.parse(source)
    except Exception as e:
        from lark.exceptions import UnexpectedInput
        if isinstance(e, UnexpectedInput):
            _print_parse_error(e, source, filename)
            sys.exit(1)
        _die(f"Parse failed: {e}")

    # Transform
    try:
        from mohio_transformer import transform, MohioCloserError, MohioCompileError
        from lark.exceptions import VisitError
        program = transform(tree, source)
    except VisitError as ve:
        real = ve.__context__
        from mohio_transformer import MohioCloserError, MohioCompileError
        if isinstance(real, (MohioCloserError, MohioCompileError)):
            _print_compile_error(real, source, filename)
            sys.exit(1)
        _die(f"Transform failed: {real}")
    except Exception as e:
        from mohio_transformer import MohioCloserError, MohioCompileError
        if isinstance(e, (MohioCloserError, MohioCompileError)):
            _print_compile_error(e, source, filename)
            sys.exit(1)
        _die(f"Transform failed: {e}")

    # All clear
    lines = len(source.splitlines())
    stmts = len(program.statements)
    print(f"\n  {green('✓')}  {bold(filename)}  {dim(f'{lines} lines · {stmts} top-level statements · no errors')}\n")


# ── mio version ──────────────────────────────────────────────────────────────

def cmd_version(args):
    print(f"\n  {bold('mio')}  Mohio Language CLI")
    print(f"  CLI version:      {VERSION}")
    print(f"  Language:         {LANGUAGE_VERSION}")
    print(f"  Grammar:          {GRAMMAR_FILE}")
    print()


# ── mio help ─────────────────────────────────────────────────────────────────

HELP_TEXT = f"""
  {bold('mio')} — Mohio Language CLI

  {bold('USAGE')}

    mio run <file.mho>              Execute a Mohio program
    mio run <file.mho> --verbose    Execute with trace output
    mio run <file.mho> --request '{{...}}'   Execute with a JSON request payload

    mio check <file.mho>            Parse and validate — no execution

    mio version                     Print version information
    mio help                        Print this message

  {bold('EXAMPLES')}

    mio run fraud_demo.mho
    mio run fraud_demo.mho --verbose
    mio run fraud_demo.mho --ai                        (real Anthropic API)
    mio run fraud_demo.mho --ai --verbose              (API + trace)
    mio check fraud_demo.mho

  {bold('PASSING A REQUEST — BY PLATFORM')}

    {bold('Windows CMD')} (use --request-file or --param):
      mio run fraud_demo.mho --request-file request.json
      mio run fraud_demo.mho --param _shape=Transaction --param amount=500 --param _roles=screener

    {bold('Windows PowerShell')} (single-quote the JSON):
      mio run fraud_demo.mho --request '{{...}}'

    {bold('Mac / Linux')} (single-quote the JSON):
      mio run fraud_demo.mho --request '{{...}}'

  {bold('SAMPLE request.json')}

    {{
      "_shape":  "Transaction",
      "_method": "POST",
      "_roles":  ["screener"],
      "id":      "T1",
      "amount":  500,
      "member_id": "M001"
    }}

  {bold('EXIT CODES')}

    0   success
    1   compile error
    2   runtime error
    3   file not found

  {bold('PHASE 1 NOTES')}

    All flow control blocks (if, check, each, repeat, while) require
    explicit closers in Phase 1:

        if condition
            give back 200 "ok"
        if: done                ← required in Phase 1

    Phase 1.5 (indentation preprocessor) will lift this requirement.

    ai.audit must appear before not confident in ai.decide blocks.
    mio fmt (Phase 2) will enforce canonical style automatically.
"""

def cmd_help(args):
    print(HELP_TEXT)


# ── Argument parser ───────────────────────────────────────────────────────────

def build_parser():
    p = argparse.ArgumentParser(
        prog="mio",
        description="Mohio Language CLI",
        add_help=False,
    )
    sub = p.add_subparsers(dest="command")

    # run
    run_p = sub.add_parser("run", add_help=False)
    run_p.add_argument("file")
    run_p.add_argument("--verbose", "-v", action="store_true")
    run_p.add_argument("--request", "-r", default=None,
                       help="JSON request payload (simulates inbound POST/GET)")
    run_p.add_argument("--request-file", "-f", default=None,
                       dest="request_file",
                       help="Path to a JSON file containing the request payload")
    run_p.add_argument("--param", "-p", action="append", default=[],
                       metavar="key=value",
                       help="Request field as key=value (repeatable). "
                            "Use instead of --request on Windows CMD.")
    run_p.add_argument("--ai", action="store_true", default=False,
                       help="Use real Anthropic API for ai.decide blocks "
                            "(requires ANTHROPIC_API_KEY env var)")
    run_p.add_argument("--api-key", default=None,
                       dest="api_key",
                       help="Anthropic API key (overrides ANTHROPIC_API_KEY)")
    run_p.add_argument("--seed", default=None,
                       metavar="seed.json",
                       help="JSON file with database seed data "
                            "(tables: members, transactions, etc.)")

    # check
    chk_p = sub.add_parser("check", add_help=False)
    chk_p.add_argument("file")

    # version
    sub.add_parser("version", add_help=False)

    # help
    sub.add_parser("help", add_help=False)

    return p


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = build_parser()
    args   = parser.parse_args()

    if args.command == "run":
        cmd_run(args)
    elif args.command == "check":
        cmd_check(args)
    elif args.command == "version":
        cmd_version(args)
    elif args.command in ("help", None):
        cmd_help(args)
    else:
        cmd_help(args)


if __name__ == "__main__":
    main()
