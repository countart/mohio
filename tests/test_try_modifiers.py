# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""try super-verb: retry / on.failure / on.success / always execution + modifiers.

`try` is now a real closer-block: required `try: done`, `on.failure` / `on.success`
(catch retired), `always`, and front modifiers that attach by natural word order:
  - `up to N times`            -> retry
  - `within N <unit>`          -> per-attempt timeout budget
  - `within N <unit> total`    -> total timeout budget
  - `waiting N <unit> between`  -> backoff between retries
Modifiers are order-independent. After the final retry fails, that last failure
flows to on.failure; earlier attempts retry silently.

The retry loop is verified by RUNNING `_exec_TryBlock` against a probe node that
raises a controlled number of times (parse-OK != runtime-OK).
"""
import os, sys
from pathlib import Path
from dataclasses import dataclass
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_URL", ":memory:")

from lark import Lark
from mohio_ast import Node, TryBlock, OnFailure, OnSuccess, AlwaysClause
from mohio_interpreter import MohioInterpreter, Context, MohioValue, _Raise
from mohio_transformer_ast import transform

_passed = _failed = 0
def check(label, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1; print(f"  [PASS] {label}")
    else:
        _failed += 1; print(f"  [FAIL] {label}  {detail}")


@dataclass
class _Probe(Node):
    tag: str = ""
    fail_until: int = 0   # raise _Raise on the first N executions of this tag


def _interp_with_probe(counters):
    interp = MohioInterpreter()
    def probe_exec(node, ctx):
        counters[node.tag] = counters.get(node.tag, 0) + 1
        if counters[node.tag] <= node.fail_until:
            raise _Raise(error_name="boom", message=f"{node.tag} #{counters[node.tag]}")
        return MohioValue("ok")
    if getattr(interp, "_plugin_registry", None) is None:
        interp._plugin_registry = {}
    interp._plugin_registry["_Probe"] = probe_exec
    return interp


def test_retry_succeeds_on_second_attempt():
    print("\n=== up to 3 times: fails once then succeeds; on.success + always run ===")
    counters = {}
    interp = _interp_with_probe(counters)
    tb = TryBlock(body=[_Probe(tag="body", fail_until=1)],
                  on_success=OnSuccess(body=[_Probe(tag="success")]),
                  on_failure=OnFailure(body=[_Probe(tag="failure")]),
                  always=AlwaysClause(body=[_Probe(tag="always")]),
                  retry_times=3)
    interp._exec_TryBlock(tb, Context())
    check("body ran twice (1 fail + 1 success)", counters.get("body") == 2, counters)
    check("on.success ran once", counters.get("success") == 1, counters)
    check("on.failure did NOT run", "failure" not in counters, counters)
    check("always ran once", counters.get("always") == 1, counters)


def test_retries_exhaust_then_on_failure():
    print("\n=== up to 2 times: always fails; on.failure runs once, always runs ===")
    counters = {}
    interp = _interp_with_probe(counters)
    tb = TryBlock(body=[_Probe(tag="body", fail_until=99)],
                  on_failure=OnFailure(body=[_Probe(tag="failure")]),
                  always=AlwaysClause(body=[_Probe(tag="always")]),
                  retry_times=2)
    interp._exec_TryBlock(tb, Context())
    check("body attempted twice (retry honored)", counters.get("body") == 2, counters)
    check("on.failure ran exactly once", counters.get("failure") == 1, counters)
    check("always ran once", counters.get("always") == 1, counters)


def test_no_handler_reraises_but_always_runs():
    print("\n=== no on.failure: failure re-raises loud, always still runs ===")
    counters = {}
    interp = _interp_with_probe(counters)
    tb = TryBlock(body=[_Probe(tag="body", fail_until=99)],
                  always=AlwaysClause(body=[_Probe(tag="always")]),
                  retry_times=1)
    raised = False
    try:
        interp._exec_TryBlock(tb, Context())
    except _Raise:
        raised = True
    check("unhandled failure re-raises", raised, counters)
    check("always ran despite re-raise", counters.get("always") == 1, counters)


def _parser():
    raw = Path("mohio.lark").read_text(encoding="utf-8")
    g = "\n".join(l for l in raw.splitlines() if not l.strip().startswith("//"))
    return Lark(g, parser="earley", ambiguity="resolve", propagate_positions=True)
_P = _parser()
def _tryblock(src):
    prog = transform(_P.parse(src), src)
    return next(s for s in prog.statements if isinstance(s, TryBlock))


def test_modifiers_parse():
    print("\n=== modifiers parse, convert units, order-independent ===")
    t = _tryblock('try up to 3 times within 5 seconds\n    give back 200 "ok"\ntry: done\n')
    check("retry=3", t.retry_times == 3)
    check("per=5s", t.per_timeout == 5.0)
    t = _tryblock('try within 5 seconds up to 3 times\n    give back 200 "ok"\ntry: done\n')
    check("order-independent: retry=3", t.retry_times == 3)
    check("order-independent: per=5s", t.per_timeout == 5.0)
    t = _tryblock('try within 2 hours total\n    give back 200 "ok"\ntry: done\n')
    check("total=7200s (hours converted)", t.total_timeout == 7200.0)
    t = _tryblock('try waiting 1 minute between\n    give back 200 "ok"\ntry: done\n')
    check("backoff=60s (minute converted)", t.backoff == 60.0)


def test_bare_try_and_always_forms_parse():
    print("\n=== bare try / try+always / try+on.failure+on.success+always all parse ===")
    for label, src in [
        ("bare try", 'try\n    give back 200 "ok"\ntry: done\n'),
        ("try+always", 'try\n    give back 200 "ok"\nalways\n    miolog.info "x"\ntry: done\n'),
        ("try+on.failure+on.success+always",
         'try\n    give back 200 "ok"\non.failure\n    give back 500 "e"\n'
         'on.success\n    miolog.info "ok"\nalways\n    miolog.info "done"\ntry: done\n'),
    ]:
        try:
            _tryblock(src); ok = True; detail = ""
        except Exception as e:
            ok = False; detail = str(e).splitlines()[0][:70]
        check(label, ok, detail)


def test_quietly_reserved():
    print("\n=== 'quietly' kept free (reserved, not implemented) ===")
    raw = Path("mohio.lark").read_text(encoding="utf-8")
    check("'quietly' is not tokenized anywhere in the grammar", '"quietly"' not in raw)


if __name__ == "__main__":
    test_retry_succeeds_on_second_attempt()
    test_retries_exhaust_then_on_failure()
    test_no_handler_reraises_but_always_runs()
    test_modifiers_parse()
    test_bare_try_and_always_forms_parse()
    test_quietly_reserved()
    print(f"\nRESULTS: {_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
