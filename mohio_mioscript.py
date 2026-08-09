# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""MioScript compiler: turn Mohio client-event blocks into browser JavaScript.

MioScript is not an embedded language. It is Mohio words (`listen for <event> on
"selector"`, `put ... into`, `toggle ...`) that compile to plain JS and run in the
browser. This module is pure: it takes ClientListener AST nodes and returns a JS
string. The interpreter collects the listeners and injects the result into served
pages; the server never executes these blocks itself.

Event data is read implicitly: the element being listened to is the subject, so
`the value` is that element's value, `the key` the key pressed, and so on.
"""
import json
from mohio_ast import (ClientListener, ClientPut, ClientToggle, ClientCheck,
                       ClientDomOp, ClientRequest, ClientSend, ClientAfter, ClientNav,
                       ClientAppend, ClientState, ClientValidate, ClientNotify, ClientHold)

# Implicit event-data reads. The listened element is the subject (event.target).
_DATUM = {
    'value': 'event.target.value',
    'key':   'event.key',
    'x':     'event.clientX',
    'y':     'event.clientY',
}

# Page-level client variables declared via `hold` (capture/recall only).
_DECLARED_VARS = set()

def _name_expr(name):
    """Resolve a bare name read in a client value: a held page var, the response
    var inside a send branch, an event datum, else the listened element's value."""
    if name in _DECLARED_VARS:
        return f"_moState[{json.dumps(name)}]"
    if name == 'result':
        return "result"
    return _DATUM.get(name, 'event.target.value')

# Human-intent event names mapped to DOM events. Unknown names pass through raw,
# so a raw DOM event like `listen for keydown on ...` still works (escape hatch).
# `change` is already a DOM event (committed/settled value change — text-on-blur,
# selects, checkboxes), so it needs no mapping; `typing` is the live (per-keystroke)
# counterpart that DOM calls `input`.
_EVENT_INTENTS = {
    'typing':   'input',
    'leaving':  'blur',
    'entering': 'focus',
    'hover':    'pointerover',
    'press':    'pointerdown',
    'release':  'pointerup',
}

# Emitted once per page. Small helpers keep the per-listener code readable.
_PRELUDE = r"""  function _moPut(sel, text){
    document.querySelectorAll(sel).forEach(function(t){
      if (t instanceof HTMLInputElement || t instanceof HTMLTextAreaElement || t instanceof HTMLSelectElement) { t.value = text; }
      else { t.textContent = text; }
    });
  }
  function _moPutHtml(sel, html){
    document.querySelectorAll(sel).forEach(function(t){
      if (t instanceof HTMLInputElement || t instanceof HTMLTextAreaElement || t instanceof HTMLSelectElement) { t.value = html; }
      else { t.innerHTML = html; }
    });
  }
  function _moToggle(sel, attr, a, b){
    document.querySelectorAll(sel).forEach(function(t){
      var cur = t.getAttribute(attr);
      t.setAttribute(attr, cur === a ? b : a);
    });
  }
  function _moBind(sel, ev, fn){
    document.querySelectorAll(sel).forEach(function(el){ el.addEventListener(ev, fn); });
  }
  function _moSend(formSel, url){
    var f = document.querySelector(formSel);
    var data = f ? new FormData(f) : new FormData();
    return fetch(url, { method: 'POST', body: data }).then(function(r){
      return r.text().then(function(t){
        var result; try { result = JSON.parse(t); } catch(e) { result = t; }
        return { ok: r.ok, result: result };
      });
    });
  }
  function _moNotify(text){
    var n = document.createElement('div');
    n.className = 'mio-notify';
    n.textContent = text;
    document.body.appendChild(n);
    setTimeout(function(){ if (n.parentNode) n.parentNode.removeChild(n); }, 3000);
  }
  function _moVal(sel){ var e = document.querySelector(sel); return e ? (e.value !== undefined ? e.value : e.textContent) : ""; }
  function _moValidEmail(v){ return /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v); }
  function _moValidUrl(v){ try { new URL(v); return true; } catch(e){ return false; } }
  function _moValidPhone(v){ return /^[+]?[\d\s().-]{7,}$/.test(v); }
  function _moValidNumber(v){ return v.trim() !== "" && !isNaN(parseFloat(v)); }
  function _moValidRequired(v){ return v.trim() !== ""; }
  function _moValidPassword(v){ return v.length >= 8; }
  function _moValidate(el, type){
    if (!el) return;
    var v = (el.value !== undefined ? el.value : el.textContent) || "";
    var ok;
    switch(type){
      case "email":    ok = _moValidEmail(v); break;
      case "url":      ok = _moValidUrl(v); break;
      case "phone":    ok = _moValidPhone(v); break;
      case "password": ok = v.length >= 8; break;
      case "required": ok = v.trim() !== ""; break;
      case "number":   ok = v.trim() !== "" && !isNaN(parseFloat(v)); break;
      default:         ok = true;
    }
    el.classList.toggle("valid", ok);
    el.classList.toggle("invalid", !ok);
  }
  function _moShow(sel){ document.querySelectorAll(sel).forEach(function(t){ t.style.display = ""; t.hidden = false; }); }
  function _moHide(sel){ document.querySelectorAll(sel).forEach(function(t){ t.style.display = "none"; }); }
  function _moEnable(sel){ document.querySelectorAll(sel).forEach(function(t){ t.disabled = false; }); }
  function _moDisable(sel){ document.querySelectorAll(sel).forEach(function(t){ t.disabled = true; }); }"""


def _emit_cond(cond):
    kind = cond[0]
    if kind == 'valid':
        fmt = cond[1] if len(cond) > 1 else 'required'
        fn = {'email': '_moValidEmail', 'url': '_moValidUrl',
              'phone': '_moValidPhone', 'number': '_moValidNumber',
              'required': '_moValidRequired', 'password': '_moValidPassword'}.get(fmt, '_moValidRequired')
        return f"{fn}(_v)"
    if kind == 'matches':
        return f"_v === _moVal({json.dumps(cond[1])})"
    if kind == 'empty':
        return '_v.trim() === ""'
    if kind == 'notempty':
        return '_v.trim() !== ""'
    if kind == 'contains':
        return f"_v.indexOf({json.dumps(cond[1])}) !== -1"
    if kind == 'starts':
        return f"_v.lastIndexOf({json.dumps(cond[1])}, 0) === 0"
    if kind == 'ends':
        return f"_v.slice(-{len(cond[1])}) === {json.dumps(cond[1])}" if cond[1] else "true"
    if kind == 'minlen':
        return f"_v.length >= {int(cond[1])}"
    if kind == 'maxlen':
        return f"_v.length <= {int(cond[1])}"
    if kind == 'checked':
        return "(event.target.checked === true)"
    if kind == 'morethan':
        return f"(parseFloat(_v) > {cond[1]})"
    if kind == 'lessthan':
        return f"(parseFloat(_v) < {cond[1]})"
    if kind == 'atleastnum':
        return f"(parseFloat(_v) >= {cond[1]})"
    if kind == 'atmostnum':
        return f"(parseFloat(_v) <= {cond[1]})"
    if kind == 'equals':
        return f"_v === {json.dumps(cond[1])}"
    if kind == 'all':
        return "(" + " && ".join(_emit_cond(c) for c in cond[1]) + ")"
    if kind == 'any':
        return "(" + " || ".join(_emit_cond(c) for c in cond[1]) + ")"
    return "false"


def _emit_check(node):
    subj = _name_expr(node.subject)
    lines = [f"var _v = {subj};"]
    first = True
    for cond, stmts in node.branches:
        kw = "if" if first else "else if"
        first = False
        body = " ".join(js for js in (_emit_stmt(s) for s in stmts) if js)
        lines.append(f"{kw} ({_emit_cond(cond)}) {{ {body} }}")
    if node.otherwise:
        body = " ".join(js for js in (_emit_stmt(s) for s in node.otherwise) if js)
        lines.append(f"else {{ {body} }}")
    inner = "\n        ".join(lines)
    return "(function(){\n        " + inner + "\n      })();"


def _emit_stmt(s):
    """One client statement -> JS, or '' if not yet supported."""
    if isinstance(s, ClientPut):
        if s.source_kind == 'result':
            # result.field — dotted response access inside a send branch (text-safe)
            return f"_moPut({json.dumps(s.target)}, {s.source});"
        if s.source_kind == 'the':
            return f"_moPut({json.dumps(s.target)}, {_name_expr(s.source)});"
        # author literal: markup renders for non-inputs; the data path stays text-safe
        return f"_moPutHtml({json.dumps(s.target)}, {json.dumps(s.source)});"
    if isinstance(s, ClientToggle):
        return (f"_moToggle({json.dumps(s.selector)}, {json.dumps(s.attr)}, "
                f"{json.dumps(s.state_a)}, {json.dumps(s.state_b)});")
    if isinstance(s, ClientCheck):
        return _emit_check(s)
    if isinstance(s, ClientDomOp):
        simple = {'show': '_moShow', 'hide': '_moHide',
                  'enable': '_moEnable', 'disable': '_moDisable'}
        if s.op in simple:
            return f"{simple[s.op]}({json.dumps(s.selector)});"
        if s.op == 'clear':
            return (f"document.querySelectorAll({json.dumps(s.selector)}).forEach(function(t){{ "
                    f"if (t instanceof HTMLInputElement || t instanceof HTMLTextAreaElement || t instanceof HTMLSelectElement) {{ t.value = ''; }} "
                    f"else {{ t.textContent = ''; }} }});")
        if s.op == 'focus':
            return (f"(function(){{ var _e = document.querySelector({json.dumps(s.selector)}); "
                    f"if (_e) _e.focus(); }})();")
        if s.op == 'scrollto':
            return (f"(function(){{ var _e = document.querySelector({json.dumps(s.selector)}); "
                    f"if (_e) _e.scrollIntoView(); }})();")
        if s.op == 'removeelem':
            return (f"document.querySelectorAll({json.dumps(s.selector)})"
                    f".forEach(function(t){{ t.remove(); }});")
        if s.op == 'selecttext':
            return (f"(function(){{ var _e = document.querySelector({json.dumps(s.selector)}); "
                    f"if (_e && _e.select) _e.select(); }})();")
        if s.op == 'togglevis':
            return (f"document.querySelectorAll({json.dumps(s.selector)}).forEach(function(t){{ "
                    f"t.style.display = (t.style.display === 'none') ? '' : 'none'; }});")
    if isinstance(s, ClientRequest):
        return (f"fetch({json.dumps(s.url)}).then(function(r){{ return r.text(); }})"
                f".then(function(t){{ _moPut({json.dumps(s.target)}, t); }});")
    if isinstance(s, ClientSend):
        succ = "\n      ".join(js for js in (_emit_stmt(x) for x in s.success) if js)
        fail = "\n      ".join(js for js in (_emit_stmt(x) for x in s.failure) if js)
        return (f"_moSend({json.dumps(s.form_selector)}, {json.dumps(s.url)})"
                f".then(function(_resp){{ var result = _resp.result;\n      "
                f"if (_resp.ok) {{ {succ} }} else {{ {fail} }} }})"
                f".catch(function(_err){{ var result = {{ error: String((_err && _err.message) || _err) }};\n      "
                f"{fail} }});")
    if isinstance(s, ClientState):
        method = {'add': 'add', 'remove': 'remove', 'toggle': 'toggle'}.get(s.op, 'add')
        return (f"document.querySelectorAll({json.dumps(s.selector)})"
                f".forEach(function(t){{ t.classList.{method}({json.dumps(s.state)}); }});")
    if isinstance(s, ClientValidate):
        return f"_moValidate(event.target, {json.dumps(s.vtype)});"
    if isinstance(s, ClientHold):
        if s.source_kind == 'result':
            expr = s.source
        elif s.source_kind == 'the':
            expr = _name_expr(s.source)
        else:
            expr = json.dumps(s.source)
        return f"_moState[{json.dumps(s.name)}] = {expr};"
    if isinstance(s, ClientNotify):
        if s.source_kind == 'result':
            expr = s.source
        elif s.source_kind == 'the':
            expr = _name_expr(s.source)
        else:
            expr = json.dumps(s.source)
        return f"_moNotify({expr});"
    if isinstance(s, ClientAppend):
        expr = (_name_expr(s.source)
                if s.source_kind == 'the' else json.dumps(s.source))
        return (f"document.querySelectorAll({json.dumps(s.target)}).forEach(function(t){{ "
                f"if (t instanceof HTMLInputElement || t instanceof HTMLTextAreaElement || t instanceof HTMLSelectElement) {{ t.value += {expr}; }} "
                f"else {{ t.textContent += {expr}; }} }});")
    if isinstance(s, ClientAfter):
        body = " ".join(js for js in (_emit_stmt(x) for x in s.body) if js)
        return f"setTimeout(function(){{ {body} }}, {int(s.ms)});"
    if isinstance(s, ClientNav):
        if s.op == 'goto':
            return f"window.location.assign({json.dumps(s.url)});"
        if s.op == 'back':
            return "window.history.back();"
        if s.op == 'reload':
            return "window.location.reload();"
    return ""


def _emit_listener(listener):
    lines = [js for js in (_emit_stmt(s) for s in listener.body) if js]
    # A submit handler takes over: cancel the browser's native submit/reload
    # automatically so the handler stays in control. Submit only — reset and
    # other events keep their native default (e.g. reset still clears fields).
    if listener.event == 'submit':
        lines = ["event.preventDefault();"] + lines
    body = "\n      ".join(lines)
    ev = _EVENT_INTENTS.get(listener.event, listener.event)
    if getattr(listener, 'debounce_ms', 0) and listener.debounce_ms > 0:
        # on.pause: reset the timer on every event; run once after the quiet gap.
        return (f"  _moBind({json.dumps(listener.selector)}, {json.dumps(ev)}, "
                f"(function(){{ var _t; return function(event){{ clearTimeout(_t); "
                f"_t = setTimeout(function(){{\n      {body}\n  }}, {int(listener.debounce_ms)}); }}; }})());")
    return (f"  _moBind({json.dumps(listener.selector)}, {json.dumps(ev)}, "
            f"function(event){{\n      {body}\n  }});")


def compile_listeners(listeners):
    """Compile a list of ClientListener nodes into a single JS bundle string.
    Returns '' when there are no client listeners (nothing to inject)."""
    listeners = [L for L in (listeners or []) if isinstance(L, ClientListener)]
    if not listeners:
        return ""
    _DECLARED_VARS.clear()
    for L in listeners:
        _collect_vars(L.body)
    parts = [_PRELUDE] + [_emit_listener(L) for L in listeners]
    state = "  var _moState = {};\n" if _DECLARED_VARS else ""
    return "(function(){\n" + state + "\n".join(parts) + "\n})();"


def _collect_vars(stmts):
    for s in stmts or []:
        if isinstance(s, ClientHold):
            _DECLARED_VARS.add(s.name)
        elif isinstance(s, ClientAfter):
            _collect_vars(s.body)
        elif isinstance(s, ClientSend):
            _collect_vars(s.success); _collect_vars(s.failure)
        elif isinstance(s, ClientCheck):
            for _cond, body in s.branches:
                _collect_vars(body)
            _collect_vars(s.otherwise)
