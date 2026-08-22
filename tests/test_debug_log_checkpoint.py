# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Regression guard for FIX-B8-4/FIX-B8-5 (T1-SILENT-SWEEP-BATCH8).

`debug.log` and `debug.checkpoint` had a full, working executor
(_exec_DebugLogStmt/_exec_DebugCheckpoint) that was permanently unreachable: no transformer
method built DebugLogStmt/DebugCheckpoint, so both statements fell through to the generic
"No executor for ..." path -- silently discarded before `mio check` or `mio run` ever saw an
error. This runs the real `.mho` source through the full pipeline (parse -> transform -> run)
and asserts on the actual debug log file written to disk, not a direct interpreter call --
the real-path standard, since a unit-level call to _exec_DebugLogStmt directly would have
passed even while the statement was completely unreachable from real source.
"""
import os, sys, tempfile, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter

_raw = mohio_data.GRAMMAR_PATH.read_text()
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  PASS  {name}")
    else:    FAIL += 1; print(f"  FAIL  {name}  {detail}")


def run_and_read_log(src):
    """Run real .mho source in a scratch cwd (debug writes to ./mohiolog) and return the
    written journey.log content, or None if nothing was written."""
    workdir = tempfile.mkdtemp(prefix='mohio_debug_test_')
    prev_cwd = os.getcwd()
    try:
        os.chdir(workdir)
        it = MohioInterpreter()
        t = transform(P.parse(src), src)
        it.run_declarations(t)
        it.run(t)
        log_root = os.path.join(workdir, 'mohiolog', 'unknown')
        if not os.path.isdir(log_root):
            return None
        runs = sorted(d for d in os.listdir(log_root) if d != 'latest')
        if not runs:
            return None
        log_path = os.path.join(log_root, runs[-1], 'journey.log')
        return open(log_path, encoding='utf-8').read() if os.path.exists(log_path) else None
    finally:
        os.chdir(prev_cwd)
        shutil.rmtree(workdir, ignore_errors=True)


# 1. debug.log actually reaches its executor and writes the target's value
log = run_and_read_log('debug on\nhold score 42\ndebug.log score\nshow "ok"\n')
check("debug.log reaches its executor (log file written)", log is not None)
check("debug.log logs the variable name and value", log is not None and 'score' in log and '42' in log,
      detail=f"got: {log!r}")

# 2. debug.checkpoint reaches its executor, with a nested debug.log line
log = run_and_read_log(
    'debug on\nhold score 7\n'
    'debug.checkpoint "start"\n    debug.log score\ndebug: done\n'
    'show "ok"\n')
check("debug.checkpoint reaches its executor (log file written)", log is not None)
check("debug.checkpoint logs its label", log is not None and 'Checkpoint: start' in log,
      detail=f"got: {log!r}")
check("debug.checkpoint's nested debug.log still logs the variable", log is not None and 'score' in log,
      detail=f"got: {log!r}")

# 3. debug off -- no log file at all (legitimate no-op, must stay that way)
log = run_and_read_log('debug off\nhold score 1\ndebug.log score\nshow "ok"\n')
check("debug off: no log file written (legitimate)", log is None)

print(f"\nRESULTS: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
