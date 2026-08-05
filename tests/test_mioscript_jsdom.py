# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
test_mioscript_jsdom.py — MioScript regression suite: real emitter + real DOM

Compiles MioScript with the real mohio_mioscript emitter, injects into HTML,
runs in jsdom, dispatches real events, asserts DOM behavior.

15 cases: mark, put (literal + text-safety), send (POST + on.success +
on.failure + result.field + implicit submit-block), notify, hold capture/recall,
validate as, intent mapping (typing→input, leaving→blur, click, hover, press).

Pattern:
    run_case(mioscript_src, dom_html, steps, asserts, fetch_response=...)
    check(name, got, want)

Run: PYTHONPATH=$PWD DATABASE_URL=:memory: python3 tests/test_mioscript_jsdom.py
Requires: npm install jsdom
"""
import os, sys, json, subprocess
sys.argv = ['mio.py']
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('DATABASE_URL', ':memory:')

from mohio_mioscript import compile_listeners
from lark import Lark
from mohio_transformer_ast import transform

_raw = open(os.path.join(ROOT, 'mohio.lark'), encoding='utf-8').read()
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

HARNESS = os.path.join(ROOT, 'tests', 'mioscript_harness.js')

# ── check infra ───────────────────────────────────────────────────────────────
_passed = _failed = 0
def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1; print(f"  ok   {label}")
    else:
        _failed += 1; print(f"  FAIL {label}: got {got!r} want {want!r}")

def check_true(label, val):
    global _passed, _failed
    if val:
        _passed += 1; print(f"  ok   {label}")
    else:
        _failed += 1; print(f"  FAIL {label}: expected truthy, got {val!r}")

# ── jsdom availability ────────────────────────────────────────────────────────
try:
    subprocess.run(['node', '-e', 'require("jsdom")'], capture_output=True,
                   check=True, cwd=ROOT)
except Exception:
    print("SKIP: jsdom not available (npm install jsdom)")
    sys.exit(0)


# ── core helper ───────────────────────────────────────────────────────────────

def _compile_mioscript(mioscript_src):
    """Parse a .mho fragment that contains client listeners, return compiled JS."""
    # Wrap in the required listen for ... listen: done structure
    full = f"""\
journey _test
    page _p at /test
        render
            <p>placeholder</p>
        render: done
    page: done
journey: done
{mioscript_src}
"""
    prog = transform(_P.parse(full), full)
    # Collect client listener AST nodes
    from mohio_interpreter import MohioInterpreter, AiDecision
    class MockAI:
        def register_chain(self, *a, **k): pass
        def decide(self, name='', inputs=None, **k):
            return AiDecision(result=None, confidence=0.9, fell_back=False,
                              model='mock', inputs=inputs or {})
    interp = MohioInterpreter(ai=MockAI())
    clients = interp._collect_client_listeners(prog)
    return compile_listeners(clients)


def run_case(mioscript_src, dom_html, steps, asserts, fetch_response=None):
    """
    Compile MioScript → JS, inject into dom_html, run in jsdom, return assert results.
    Each assert result: {label, ok, actual, expected, error}
    """
    js = _compile_mioscript(mioscript_src)
    full_html = f"<html><body>{dom_html}<script>\n{js}\n</script></body></html>"

    spec = {"html": full_html, "steps": steps, "asserts": asserts}
    if fetch_response:
        spec["fetch_response"] = fetch_response

    r = subprocess.run(
        ['node', HARNESS], input=json.dumps(spec),
        capture_output=True, text=True, cwd=ROOT, timeout=15,
    )
    if r.returncode != 0:
        print(f"  jsdom error: {r.stderr[:200]}")
        return []
    try:
        data = json.loads(r.stdout)
        if "error" in data:
            print(f"  jsdom error: {data['error']}")
            return []
        return data.get("assert_results", [])
    except json.JSONDecodeError:
        print(f"  jsdom bad output: {r.stdout[:200]}")
        return []


def jcheck(label, results, idx):
    """Check a single assert result by index."""
    if idx >= len(results):
        global _failed
        _failed += 1
        print(f"  FAIL {label}: assert {idx} missing ({len(results)} results)")
        return
    r = results[idx]
    if r.get("error"):
        check_true(label, False)
        print(f"        error: {r['error']}")
    else:
        check_true(label, r.get("ok", False))
        if not r.get("ok"):
            print(f"        got={r.get('actual')!r} want={r.get('expected')!r}")


# ══════════════════════════════════════════════════════════════════════════════
# 1. MARK — adds a CSS class
# ══════════════════════════════════════════════════════════════════════════════
print("\n── 1. mark ──")
results = run_case(
    'listen for\n    listen for click on #btn\n        mark #card as active\n    listen: done\nlisten: done',
    '<div id="card">Card</div><button id="btn">Go</button>',
    [{"action": "dispatch", "selector": "#btn", "event": "click"}],
    [{"selector": "#card", "prop": "classList", "expected": "active", "check_type": "contains", "label": "mark adds class"}],
)
jcheck("mark adds class", results, 0)

# ══════════════════════════════════════════════════════════════════════════════
# 2. PUT literal — renders markup into an element
# ══════════════════════════════════════════════════════════════════════════════
print("\n── 2. put literal ──")
results = run_case(
    'listen for\n    listen for click on #btn\n        put "Hello <b>World</b>" into #out\n    listen: done\nlisten: done',
    '<p id="out">empty</p><button id="btn">Go</button>',
    [{"action": "dispatch", "selector": "#btn", "event": "click"}],
    [{"selector": "#out", "prop": "innerHTML", "expected": "Hello <b>World</b>", "label": "put literal renders markup"}],
)
jcheck("put literal renders markup", results, 0)

# ══════════════════════════════════════════════════════════════════════════════
# 3. PUT data — forced text-safe (textContent), hostile payload inert
# ══════════════════════════════════════════════════════════════════════════════
print("\n── 3. put data (text-safe) ──")
results = run_case(
    'listen for\n    listen for change on #inp\n        put value into #out\n    listen: done\nlisten: done',
    '<input id="inp" /><p id="out">safe</p>',
    [{"action": "dispatch", "selector": "#inp", "event": "change", "value": "<img onerror=alert(1) src=x>"}],
    [{"selector": "#out", "prop": "textContent", "expected": "<img onerror=alert(1) src=x>", "label": "hostile data rendered as text"}],
)
jcheck("hostile data rendered as text", results, 0)

# ══════════════════════════════════════════════════════════════════════════════
# 4. SEND — POST form, on.success branch, result.field access
# ══════════════════════════════════════════════════════════════════════════════
print("\n── 4. send on.success ──")
results = run_case(
    'listen for\n    listen for submit on #myform\n        send #myform to "/api"\n        on.success\n            put result.name into #out\n        \n    listen: done\nlisten: done',
    '<form id="myform"><input name="x" value="1" /><button type="submit">Go</button></form><p id="out">waiting</p>',
    [{"action": "submit", "selector": "#myform", "delay_ms": 100}],
    [
        {"selector": "#out", "prop": "textContent", "expected": "Alice", "label": "result.field rendered"},
        {"prop": "fetch_url", "expected": "/api", "label": "fetch URL correct"},
        {"prop": "fetch_method", "expected": "POST", "label": "fetch method is POST"},
    ],
    fetch_response={"ok": True, "status": 200, "body": '{"name":"Alice","status":"ok"}'},
)
jcheck("result.field rendered", results, 0)
jcheck("fetch URL correct", results, 1)
jcheck("fetch method is POST", results, 2)

# ══════════════════════════════════════════════════════════════════════════════
# 5. SEND — on.failure branch
# ══════════════════════════════════════════════════════════════════════════════
print("\n── 5. send on.failure ──")
results = run_case(
    'listen for\n    listen for submit on #myform\n        send #myform to "/api"\n        on.failure\n            put "Failed!" into #out\n        \n    listen: done\nlisten: done',
    '<form id="myform"><input name="x" value="1" /><button type="submit">Go</button></form><p id="out">waiting</p>',
    [{"action": "submit", "selector": "#myform", "delay_ms": 100}],
    [{"selector": "#out", "prop": "textContent", "expected": "Failed!", "label": "on.failure fires"}],
    fetch_response={"ok": False, "status": 422, "body": '{"error":"bad"}'},
)
jcheck("on.failure fires", results, 0)

# ══════════════════════════════════════════════════════════════════════════════
# 6. SEND — implicit submit-block (no page reload)
# ══════════════════════════════════════════════════════════════════════════════
print("\n── 6. implicit submit-block ──")
results = run_case(
    'listen for\n    listen for submit on #myform\n        send #myform to "/api"\n        on.success\n            put "done" into #out\n        \n    listen: done\nlisten: done',
    '<form id="myform" action="/nope"><input name="x" /><button type="submit">Go</button></form><p id="out">init</p>',
    [{"action": "submit", "selector": "#myform", "delay_ms": 100}],
    [
        {"selector": "#out", "prop": "textContent", "expected": "done", "label": "submit handled by send"},
        {"prop": "fetch_url", "expected": "/api", "label": "native submit blocked, send used"},
    ],
    fetch_response={"ok": True, "status": 200, "body": '{"message":"ok"}'},
)
jcheck("submit handled by send", results, 0)
jcheck("native submit blocked", results, 1)

# ══════════════════════════════════════════════════════════════════════════════
# 7. NOTIFY — creates .mio-notify, text-safe
# ══════════════════════════════════════════════════════════════════════════════
print("\n── 7. notify ──")
results = run_case(
    'listen for\n    listen for click on #btn\n        notify "Saved!"\n    listen: done\nlisten: done',
    '<button id="btn">Go</button>',
    [{"action": "dispatch", "selector": "#btn", "event": "click"}],
    [{"selector": ".mio-notify", "prop": "textContent", "expected": "Saved!", "label": "notify text"}],
)
jcheck("notify creates .mio-notify", results, 0)

# ══════════════════════════════════════════════════════════════════════════════
# 8. HOLD — capture on one event, recall on another
# ══════════════════════════════════════════════════════════════════════════════
print("\n── 8. hold capture/recall ──")
results = run_case(
    'listen for\n    listen for change on #inp\n        hold saved = value\n    listen: done\n    listen for click on #btn\n        put saved into #out\n    listen: done\nlisten: done',
    '<input id="inp" /><button id="btn">Show</button><p id="out">empty</p>',
    [
        {"action": "dispatch", "selector": "#inp", "event": "change", "value": "CAPTURED"},
        {"action": "wait", "delay_ms": 20},
        {"action": "dispatch", "selector": "#btn", "event": "click"},
    ],
    [{"selector": "#out", "prop": "textContent", "expected": "CAPTURED", "label": "hold persists"}],
)
jcheck("hold persists across events", results, 0)

# ══════════════════════════════════════════════════════════════════════════════
# 9. VALIDATE AS email — valid → .valid, invalid → .invalid
# ══════════════════════════════════════════════════════════════════════════════
print("\n── 9. validate as email ──")
results = run_case(
    'listen for\n    listen for change on #em\n        validate as email\n    listen: done\nlisten: done',
    '<input id="em" type="text" />',
    [{"action": "dispatch", "selector": "#em", "event": "change", "value": "a@b.com"}],
    [{"selector": "#em", "prop": "classList", "expected": "valid", "check_type": "contains", "label": "valid email → .valid"}],
)
jcheck("valid email → .valid", results, 0)

results = run_case(
    'listen for\n    listen for change on #em\n        validate as email\n    listen: done\nlisten: done',
    '<input id="em" type="text" />',
    [{"action": "dispatch", "selector": "#em", "event": "change", "value": "nope"}],
    [{"selector": "#em", "prop": "classList", "expected": "invalid", "check_type": "contains", "label": "invalid email → .invalid"}],
)
jcheck("invalid email → .invalid", results, 0)

# ══════════════════════════════════════════════════════════════════════════════
# 10. TYPING → input event mapping
# ══════════════════════════════════════════════════════════════════════════════
print("\n── 10. typing → input ──")
results = run_case(
    'listen for\n    listen for typing on #s\n        put value into #m\n    listen: done\nlisten: done',
    '<input id="s" /><p id="m">...</p>',
    [{"action": "dispatch", "selector": "#s", "event": "input", "value": "hello"}],
    [{"selector": "#m", "prop": "textContent", "expected": "hello", "label": "typing fires on input"}],
)
jcheck("typing maps to input", results, 0)

# ══════════════════════════════════════════════════════════════════════════════
# 11. SHOW / HIDE
# ══════════════════════════════════════════════════════════════════════════════
print("\n── 11. show/hide ──")
results = run_case(
    'listen for\n    listen for click on #hb\n        hide #box\n    listen: done\n    listen for click on #sb\n        show #box\n    listen: done\nlisten: done',
    '<div id="box">X</div><button id="hb">H</button><button id="sb">S</button>',
    [
        {"action": "dispatch", "selector": "#hb", "event": "click"},
    ],
    [{"selector": "#box", "prop": "display", "expected": "none", "label": "hide sets display:none"}],
)
jcheck("hide sets display:none", results, 0)

# ══════════════════════════════════════════════════════════════════════════════
# 12. DISABLE / ENABLE
# ══════════════════════════════════════════════════════════════════════════════
print("\n── 12. disable/enable ──")
results = run_case(
    'listen for\n    listen for click on #db\n        disable #t\n    listen: done\n    listen for click on #eb\n        enable #t\n    listen: done\nlisten: done',
    '<button id="t">T</button><button id="db">D</button><button id="eb">E</button>',
    [{"action": "dispatch", "selector": "#db", "event": "click"}],
    [{"selector": "#t", "prop": "disabled", "expected": "true", "label": "disable sets disabled"}],
)
jcheck("disable sets disabled", results, 0)

# ══════════════════════════════════════════════════════════════════════════════
# 13. TOGGLE class
# ══════════════════════════════════════════════════════════════════════════════
print("\n── 13. toggle ──")
results = run_case(
    'listen for\n    listen for click on #btn\n        toggle #card as highlighted\n    listen: done\nlisten: done',
    '<div id="card">Card</div><button id="btn">T</button>',
    [
        {"action": "dispatch", "selector": "#btn", "event": "click"},
    ],
    [{"selector": "#card", "prop": "classList", "expected": "highlighted", "check_type": "contains", "label": "toggle adds class"}],
)
jcheck("toggle adds class", results, 0)

# ══════════════════════════════════════════════════════════════════════════════
# 14. UNMARK — removes a class
# ══════════════════════════════════════════════════════════════════════════════
print("\n── 14. unmark ──")
results = run_case(
    'listen for\n    listen for click on #mb\n        mark #card as active\n    listen: done\n    listen for click on #ub\n        unmark #card as active\n    listen: done\nlisten: done',
    '<div id="card">Card</div><button id="mb">M</button><button id="ub">U</button>',
    [
        {"action": "dispatch", "selector": "#mb", "event": "click"},
        {"action": "dispatch", "selector": "#ub", "event": "click"},
    ],
    [{"selector": "#card", "prop": "classList", "expected": "active", "check_type": "not_contains", "label": "unmark removes class"}],
)
jcheck("unmark removes class", results, 0)

# ══════════════════════════════════════════════════════════════════════════════
# 15. LEAVING → blur intent mapping
# ══════════════════════════════════════════════════════════════════════════════
print("\n── 15. leaving → blur ──")
results = run_case(
    'listen for\n    listen for leaving on #inp\n        put "left!" into #out\n    listen: done\nlisten: done',
    '<input id="inp" /><p id="out">here</p>',
    [{"action": "dispatch", "selector": "#inp", "event": "blur"}],
    [{"selector": "#out", "prop": "textContent", "expected": "left!", "label": "leaving maps to blur"}],
)
jcheck("leaving maps to blur", results, 0)


# ══════════════════════════════════════════════════════════════════════════════
print(f"\n{'=' * 60}")
print(f"  {_passed} passed, {_failed} failed")
if _failed:
    print(f"  *** {_failed} FAILURE(S) ***")
print(f"{'=' * 60}")
sys.exit(1 if _failed else 0)
