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
                       ClientAppend, ClientState, ClientValidate, ClientNotify, ClientAlert,
                       ClientConfirm, ClientCopy, ClientHold, ClientIdle, ClientInject,
                       ClientEvery, ClientStopInterval, ClientShare, ClientAssign,
                       ClientRelease, ClientClearVar, ClientForgetVar, ClientRename,
                       ClientReplaceVar, ClientReplaceIn)

# share VALUE on DESTINATION: destination -> share-page URL template, maintained as data
# (mohio_data/share_destinations.json), never hardcoded here. An unknown destination refuses
# at compile time (see the ClientShare branch in _emit_stmt), so it is checked, not guessed.
import mohio_data as _mohio_data
# A key starting with `_` is metadata (the license line), never a destination.
_SHARE_DESTINATIONS = {k: v for k, v in json.loads(
    _mohio_data.SHARE_DESTINATIONS_PATH.read_text(encoding='utf-8')).items() if not k.startswith('_')}

# Implicit event-data reads. The listened element is the subject (event.target).
_DATUM = {
    'value': 'event.target.value',
    'key':   'event.key',
    'x':     'event.clientX',
    'y':     'event.clientY',
}

# Page-level client values -- every declared name (fluid, via `name value`, or held, via
# `hold`) reads from here. _HELD_VARS is the subset currently eligible for the five state
# operators (release/clear/forget/rename/replace): a name only enters it through `hold`,
# matching the server's hold-only ruling (2026-10-04).
_DECLARED_VARS = set()
_HELD_VARS = set()

def _name_expr(name):
    """Resolve a bare name read in a client value: a declared page var (fluid or held), the
    response var inside a send branch, an event datum, else fail loud."""
    if name in _DECLARED_VARS:
        return f"_moState[{json.dumps(name)}]"
    if name == 'result':
        return "result"
    if name in _DATUM:
        return _DATUM[name]
    # T1-SILENT-SWEEP-BATCH7 (2026-08-15): an unrecognized name used to silently fall back to
    # 'event.target.value' -- wrong JS shipped to real browsers with no error at compile or
    # runtime, for e.g. a typo'd event datum or a `hold`-declared var referenced before its
    # own compile_listeners() pass has run (compile_listeners always collects every declared
    # var across all listeners FIRST, so a genuinely declared var is always in _DECLARED_VARS
    # by the time this runs -- this only fires on a name that is neither declared nor a known
    # event datum nor `result`).
    raise ValueError(
        f"'the {name}' is not a recognized client value. Expected one of: "
        f"{', '.join(sorted(_DATUM.keys()))}, 'result' (inside a send branch), or a "
        f"page value declared with plain assignment (`{name} <value>`) or 'hold'.")

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
      else { t.innerHTML = html; _moRebind(t); }
    });
  }
  function _moInject(sel, html){
    document.querySelectorAll(sel).forEach(function(t){ t.innerHTML = html; _moRebind(t); });
  }
  // THE SWIRL: Mohio's default in-flight indicator for a `send` (Batch 30). Inline SVG served from
  // this bundle, never fetched from another host -- a page reaching out for a spinner has an external
  // dependency exactly when the network is already in doubt, and a bank's network may block it. It
  // appears only once a request has run past the threshold (a spinner that flashes for 80ms reads as
  // a glitch), and it carries its own class `mio-swirl` so a stylesheet can restyle it, replace it,
  // or switch it off with `.mio-swirl{display:none}`. Every send clears it, on success AND on failure.
  var _MO_SWIRL_THRESHOLD_MS = 200;
  function _moSwirlStyle(){
    if (document.getElementById('mio-swirl-style')) return;
    var st = document.createElement('style'); st.id = 'mio-swirl-style';
    st.textContent = '.mio-swirl{position:fixed;inset:0;display:flex;align-items:center;' +
      'justify-content:center;background:rgba(255,255,255,0.4);z-index:9999;color:#333}';
    (document.head || document.documentElement).appendChild(st);
  }
  function _moSwirlStart(){
    var h = { timer: null, el: null };
    h.timer = setTimeout(function(){
      _moSwirlStyle();
      var d = document.createElement('div');
      d.className = 'mio-swirl'; d.setAttribute('role','status'); d.setAttribute('aria-label','Loading');
      d.innerHTML = '<svg width="40" height="40" viewBox="0 0 40 40" aria-hidden="true">' +
        '<circle cx="20" cy="20" r="16" fill="none" stroke="currentColor" stroke-width="4" ' +
        'stroke-dasharray="80" stroke-dashoffset="60">' +
        '<animateTransform attributeName="transform" type="rotate" from="0 20 20" to="360 20 20" ' +
        'dur="0.8s" repeatCount="indefinite"/></circle></svg>';
      document.body.appendChild(d); h.el = d;
    }, _MO_SWIRL_THRESHOLD_MS);
    return h;
  }
  function _moSwirlStop(h){
    if (!h) return;
    if (h.timer) { clearTimeout(h.timer); h.timer = null; }
    if (h.el && h.el.parentNode) { h.el.parentNode.removeChild(h.el); h.el = null; }
  }
  function _moToggle(sel, attr, a, b){
    document.querySelectorAll(sel).forEach(function(t){
      var cur = t.getAttribute(attr);
      t.setAttribute(attr, cur === a ? b : a);
    });
  }
  // POLLING DEFAULTS (RULED by Ron, 2026-10-06; no new syntax). A `request ... into` inside an
  // `every` is a poll, and a poll behaves: it waits while the page is hidden or offline and picks
  // up again on the next tick after; it never starts while its last request is still running; it
  // replaces the target only when the answer changed; and the target becomes a polite live region,
  // so a screen reader announces the change instead of the page changing silently.
  var _moPollBusy = {}, _moPollLast = {};
  function _moPollInto(url, sel, ev){
    if (document.hidden || navigator.onLine === false) return;
    var key = url + " " + sel;
    if (_moPollBusy[key]) return;
    _moPollBusy[key] = true;
    var done = function(){ _moPollBusy[key] = false; };
    fetch(_moReqUrl(url, ev)).then(function(r){
      var _frag = (r.headers.get('X-Mohio-Rendered') === 'fragment');
      return r.text().then(function(t){
        if (_moPollLast[key] === t) return;
        _moPollLast[key] = t;
        document.querySelectorAll(sel).forEach(function(el){
          if (!el.hasAttribute('aria-live')) el.setAttribute('aria-live', 'polite');
        });
        if (_frag) { _moInject(sel, t); } else { _moPut(sel, t); }
      });
    }).then(done, done);
  }
  // MARKUP PLACED LATER GETS THE SAME LISTENERS (day 2, item c). A listener binds to the
  // elements that exist when the page loads, so a button that arrived afterwards -- placed by
  // `request ... into` or `send` with a render fragment, or by `inject` -- had no listener and its
  // click did nothing. Every binding is remembered, and whatever markup is placed is bound to each
  // listener whose selector it matches. Binding an element that already has the listener is safe:
  // addEventListener ignores the same function registered twice on one element, so it runs once.
  var _moBindings = [];
  function _moBind(sel, ev, fn){
    _moBindings.push({ sel: sel, ev: ev, fn: fn });
    document.querySelectorAll(sel).forEach(function(el){ el.addEventListener(ev, fn); });
  }
  function _moRebind(root){
    _moBindings.forEach(function(b){
      var els = Array.prototype.slice.call(root.querySelectorAll(b.sel));
      if (root.matches && root.matches(b.sel)) els.push(root);
      els.forEach(function(el){ el.addEventListener(b.ev, b.fn); });
    });
  }
  // ONE FAILURE SHAPE (FAC-26). Every send failure -- an HTTP error OR a network exception --
  // reaches on.failure as the same `result` object, so a handler branches on one field on every
  // path. An HTTP error used to arrive as the parsed body (result.message maybe), a network
  // exception as { error }, so neither field was reliable and a network failure showed nothing.
  // The KIND matches the server's failure.kind vocabulary from Batch 38 (conflict is conflict on
  // both sides), and a network exception -- which has no status at all -- carries kind 'network'
  // and an explicit null status. Where the kind cannot be determined it is an explicit 'unknown',
  // never blank. Status->kind inverts the server's kind->status table; a status with more than one
  // server kind collapses to its representative (400 -> validation_error), and 500 is left as
  // 'unknown' because the server maps both db_error and a generic runtime error onto it.
  var _MO_STATUS_KIND = { 400: 'validation_error', 403: 'authorization_error', 404: 'not_found',
    409: 'conflict', 422: 'validation_error', 502: 'provider_error', 503: 'connection_error',
    504: 'timeout' };
  var _MO_KINDS = { validation_error:1, authorization_error:1, not_found:1, conflict:1,
    provider_error:1, connection_error:1, timeout:1, coercion_error:1, math_error:1,
    string_error:1, division_by_zero:1, db_error:1, network:1, unknown:1 };
  function _moKind(status, json){
    // Prefer the kind the server named in its own error body, so it matches exactly; else map the
    // status; else it is not determinable.
    if (json && typeof json === 'object' && json.code && _MO_KINDS[json.code]) return json.code;
    return _MO_STATUS_KIND[status] || 'unknown';
  }
  function _moFailure(status, json, text, netErr){
    // Additive: start from the parsed body so an existing handler reading result.message on an HTTP
    // failure is unchanged, then layer the normalized fields on top. Empty representations are
    // documented: status null when there was no response, message/error "" when none, json {}.
    var body = (json && typeof json === 'object') ? json : {};
    var result = {}; for (var k in body) { if (body.hasOwnProperty(k)) result[k] = body[k]; }
    result.ok = false;
    result.status = (status == null ? null : status);
    result.message = (body.message != null ? String(body.message)
                       : (netErr == null && typeof text === 'string' ? text : ''));
    result.error = (netErr != null ? String(netErr)
                     : (body.error != null ? String(body.error)
                        : (body.code != null ? String(body.code) : '')));
    result.json = body;
    result.kind = (netErr != null ? 'network' : _moKind(status, json));
    return result;
  }
  function _moStampRow(data, event){
    // ROW IDENTITY: if this send fired from a click (or any event) inside a rendered row, attach the
    // row's identity -- its record key, or its position flagged positional -- under the loop variable
    // name the render stamped. The client never has the record's fields, only this identity. Attaches
    // automatically, the way the event's own data (x, y) rides along.
    if (!event || !event.target || !event.target.closest) return;
    var row = event.target.closest('[data-mio-row]');
    if (!row) return;
    var field = row.getAttribute('data-mio-row-field');
    if (!field) return;
    // CLASH: the form already carries a field with the loop variable's name. Refuse loud rather than
    // silently override either value -- the row identity and the form field would otherwise collide.
    if (data.has && data.has(field)) {
      throw new Error("row identity clash: the form already has a field named '" + field
        + "' (the loop variable). Rename the form field or the loop variable; "
        + "Mohio will not override either value.");
    }
    data.append(field, row.getAttribute('data-mio-row') || '');
    if (row.getAttribute('data-mio-row-positional') === '1') {
      data.append('__mio_positional__' + field, '1');
    }
  }
  function _moReqUrl(url, event){
    // ROW IDENTITY for a client `request` (a GET): the identity rides as a query parameter, since a
    // GET has no body. Same clash rule -- refuse loud if the url already names the loop variable.
    if (!event || !event.target || !event.target.closest) return url;
    var row = event.target.closest('[data-mio-row]');
    if (!row) return url;
    var field = row.getAttribute('data-mio-row-field');
    if (!field) return url;
    if (new RegExp('[?&]' + field.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '=').test(url)) {
      throw new Error("row identity clash: the request url already has a '" + field
        + "' parameter (the loop variable). Rename the parameter or the loop variable; "
        + "Mohio will not override either value.");
    }
    var sep = (url.indexOf('?') === -1) ? '?' : '&';
    var out = url + sep + field + '=' + encodeURIComponent(row.getAttribute('data-mio-row') || '');
    if (row.getAttribute('data-mio-row-positional') === '1') {
      out += '&__mio_positional__' + field + '=1';
    }
    return out;
  }
  function _moSend(formSel, url, event){
    var f = document.querySelector(formSel);
    var data = f ? new FormData(f) : new FormData();
    _moStampRow(data, event);
    var _sw = _moSwirlStart();
    // Accept: application/json so a failed shape-validation comes back as the structured field
    // errors, the same failure body the JSON path returns, rather than a re-rendered HTML page a
    // fetch cannot use. The server keeps the HTML re-render for a plain browser <form> submit.
    return fetch(url, { method: 'POST', body: data, headers: { 'Accept': 'application/json' } }).then(function(r){
      return r.text().then(function(t){
        _moSwirlStop(_sw);   // cleared on success
        var json = null; try { json = JSON.parse(t); } catch(e) {}
        var result = (json !== null) ? json : t;   // SUCCESS shape is unchanged: the parsed body or text
        // The fragment marker the server sets on a `render fragment` response. `inject` trusts a
        // string as markup ONLY when this is true; anything else stays text or is refused.
        return { ok: r.ok, result: result, status: r.status, json: json, text: t,
                 fragment: (r.headers.get('X-Mohio-Rendered') === 'fragment') };
      });
    }).catch(function(e){ _moSwirlStop(_sw); throw e; });   // and cleared on failure, never left spinning
  }
  function _moNotify(text){
    var n = document.createElement('div');
    n.className = 'mio-notify';
    n.textContent = text;
    document.body.appendChild(n);
    setTimeout(function(){ if (n.parentNode) n.parentNode.removeChild(n); }, 3000);
  }
  function _moVal(sel){ var e = document.querySelector(sel); return e ? (e.value !== undefined ? e.value : e.textContent) : ""; }
  function _moCopy(text){
    if (!navigator.clipboard || !navigator.clipboard.writeText) {
      if (typeof window.alert === 'function') window.alert('Copy is not available in this browser.');
      return;
    }
    navigator.clipboard.writeText(text).catch(function(e){
      if (typeof window.alert === 'function') window.alert('Could not copy: ' + ((e && e.message) || e));
    });
  }
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


def _source_expr(source_kind, source):
    """The client_value vocabulary (literal / result / the) as a JS expression."""
    if source_kind == 'result':
        return source
    if source_kind == 'the':
        return _name_expr(source)
    return json.dumps(source)


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


# Depth of `every` bodies being emitted: a request emitted inside one is a poll.
_POLLING = [0]


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
    if isinstance(s, ClientInject):
        # Place a Mohio-rendered fragment (`result`) as markup, but ONLY when the response carried the
        # server's fragment marker. `result` and `_resp` exist inside a send branch; the guard also
        # makes an inject written outside one refuse gracefully rather than throw. A response that is
        # not a marked fragment is refused loudly and the region is left as it was -- data assembled
        # anywhere but a Mohio render never becomes markup.
        return (f"if (typeof _resp !== 'undefined' && _resp.fragment) "
                f"{{ _moInject({json.dumps(s.target)}, result); }} "
                f"else {{ if (window.console) console.error("
                f"'inject refused: the response is not a Mohio-rendered fragment'); }}")
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
        if s.op == 'play':
            return (f"(function(){{ var _e = document.querySelector({json.dumps(s.selector)}); "
                    f"if (!_e || !_e.play) return; var _p = _e.play(); "
                    f"if (_p && _p.catch) _p.catch(function(){{}}); }})();")
        if s.op == 'pause':
            return (f"(function(){{ var _e = document.querySelector({json.dumps(s.selector)}); "
                    f"if (_e && _e.pause) _e.pause(); }})();")
        if s.op == 'stopmedia':
            return (f"(function(){{ var _e = document.querySelector({json.dumps(s.selector)}); "
                    f"if (!_e) return; if (_e.pause) _e.pause(); _e.currentTime = 0; }})();")
        if s.op == 'reset':
            return (f"(function(){{ var _e = document.querySelector({json.dumps(s.selector)}); "
                    f"if (_e && _e.reset) _e.reset(); }})();")
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
    if isinstance(s, ClientRequest) and _POLLING[0]:
        # Inside an `every`: the poll helper carries the four polling defaults (see the prelude).
        return (f"_moPollInto({json.dumps(s.url)}, {json.dumps(s.target)}, "
                f"(typeof event !== 'undefined' ? event : null));")
    if isinstance(s, ClientRequest):
        # request (a browser GET) honours the SAME fragment marker as send (Batch 30): a response the
        # Mohio server marked `X-Mohio-Rendered: fragment` -- server-rendered markup that has been
        # through render's escaping and classification -- is placed as MARKUP; anything else is placed
        # as TEXT exactly as before. Reads are the common case, so a GET endpoint returning
        # `render fragment` could not be placed as markup without this, forcing a POST or a handwritten
        # fetch. The security rule is unchanged, not relaxed: server-rendered markup carrying the marker
        # is the only markup, the marker is set on and only on a render fragment handler, and an
        # unmarked response (ordinary text, or anything a non-render endpoint returns) stays text.
        return (f"fetch(_moReqUrl({json.dumps(s.url)}, (typeof event !== 'undefined' ? event : null)))"
                f".then(function(r){{ "
                f"var _frag = (r.headers.get('X-Mohio-Rendered') === 'fragment'); "
                f"return r.text().then(function(t){{ "
                f"if (_frag) {{ _moInject({json.dumps(s.target)}, t); }} "
                f"else {{ _moPut({json.dumps(s.target)}, t); }} }}); }});")
    if isinstance(s, ClientSend):
        succ = "\n      ".join(js for js in (_emit_stmt(x) for x in s.success) if js)
        fail = "\n      ".join(js for js in (_emit_stmt(x) for x in s.failure) if js)
        # SUCCESS keeps the raw parsed body as `result` (unchanged). BOTH failure paths -- an HTTP
        # error and a network exception -- build the ONE normalized failure shape via _moFailure, so
        # on.failure sees the same object with a reliable kind/status/message/error on every path.
        return (f"_moSend({json.dumps(s.form_selector)}, {json.dumps(s.url)}, "
                f"(typeof event !== 'undefined' ? event : null))"
                f".then(function(_resp){{\n      "
                f"if (_resp.ok) {{ var result = _resp.result;\n      {succ} }}"
                f" else {{ var result = _moFailure(_resp.status, _resp.json, _resp.text, null);\n      {fail} }} }})"
                f".catch(function(_err){{ var result = _moFailure(null, null, null, (_err && _err.message) || _err);\n      "
                f"{fail} }});")
    if isinstance(s, ClientState):
        method = {'add': 'add', 'remove': 'remove', 'toggle': 'toggle'}.get(s.op, 'add')
        return (f"document.querySelectorAll({json.dumps(s.selector)})"
                f".forEach(function(t){{ t.classList.{method}({json.dumps(s.state)}); }});")
    if isinstance(s, ClientValidate):
        if s.vtype == 'strict':
            # Whole-form validation for a submit listener (event.target IS the form), using
            # the browser's own constraint-validation API rather than reinventing a per-type
            # check: whatever `required`/`type=`/`pattern`/`min`/`max` the fields already
            # carry is what checkValidity() enforces. On failure, auto-focus the first
            # invalid field (the "validate auto-focus on failure" behavior) and stop the
            # listener right there -- `return` skips every statement written after this one,
            # the same gate shape `confirm` uses, so a chained `send` never fires on bad input.
            return (f"if (!event.target.checkValidity()) {{ "
                    f"var _inv = event.target.querySelector(':invalid'); "
                    f"if (_inv) {{ if (_inv.focus) _inv.focus(); "
                    f"if (_inv.reportValidity) _inv.reportValidity(); }} "
                    f"return; }}")
        return f"_moValidate(event.target, {json.dumps(s.vtype)});"
    if isinstance(s, ClientHold):
        # FROZEN until `release` (Ron ruling, 2026-10-04): a second `hold` for the same name,
        # with nothing having released it, refuses loud rather than quietly overwriting --
        # `_moHeld` is the freeze flag, kept separate from `_moState` (the value) so `release`
        # can drop the freeze while keeping the value, the same split the server's own
        # held/typed state keeps.
        expr = _source_expr(s.source_kind, s.source)
        name = json.dumps(s.name)
        return (f"if (_moHeld[{name}]) {{ throw new Error(\"'{s.name}' is already held -- "
                f"release it first.\"); }} "
                f"_moState[{name}] = {expr}; _moHeld[{name}] = true;")
    if isinstance(s, ClientAssign):
        # The ordinary, always-reassignable client value (what `hold` meant before this
        # ruling) -- no freeze, no guard.
        expr = _source_expr(s.source_kind, s.source)
        return f"_moState[{json.dumps(s.name)}] = {expr};"
    if isinstance(s, ClientRelease):
        name = json.dumps(s.name)
        return (f"if (!_moHeld[{name}]) {{ throw new Error(\"'{s.name}' has no hold to "
                f"release -- it is not currently held.\"); }} delete _moHeld[{name}];")
    if isinstance(s, ClientClearVar):
        # Mirrors the server's own `clear`: empties the value AND drops the hold (clear is not
        # a way to stay held with an empty value -- a cleared name is fluid again).
        name = json.dumps(s.name)
        return (f"if (!_moHeld[{name}]) {{ throw new Error(\"'{s.name}' is a fluid client "
                f"value, not held -- clear only applies to a held value. Hold it first: "
                f"hold {s.name} <value>.\"); }} "
                f"_moState[{name}] = ''; delete _moHeld[{name}];")
    if isinstance(s, ClientForgetVar):
        name = json.dumps(s.name)
        return (f"if (!_moHeld[{name}]) {{ throw new Error(\"'{s.name}' is a fluid client "
                f"value, not held -- forget only applies to a held value. Hold it first: "
                f"hold {s.name} <value>.\"); }} "
                f"delete _moState[{name}]; delete _moHeld[{name}];")
    if isinstance(s, ClientRename):
        name, target = json.dumps(s.name), json.dumps(s.target)
        return (f"if (!_moHeld[{name}]) {{ throw new Error(\"'{s.name}' is a fluid client "
                f"value, not held -- rename only applies to a held value. Hold it first: "
                f"hold {s.name} <value>.\"); }} "
                f"if (_moState.hasOwnProperty({target}) || _moHeld[{target}]) {{ throw new "
                f"Error(\"'{s.target}' already exists -- rename never overwrites.\"); }} "
                f"_moState[{target}] = _moState[{name}]; _moHeld[{target}] = true; "
                f"delete _moState[{name}]; delete _moHeld[{name}];")
    if isinstance(s, ClientReplaceVar):
        # Whole-value replace, in place, on a HELD client value -- the client mirror of the
        # server's `replace NAME with VALUE`. Stays held, same as the server (replace swaps
        # the value only; it does not drop the hold the way clear does).
        name = json.dumps(s.name)
        expr = _source_expr(s.source_kind, s.source)
        return (f"if (!_moHeld[{name}]) {{ throw new Error(\"'{s.name}' is a fluid client "
                f"value, not held -- replace only applies in place to a held value. Hold it "
                f"first: hold {s.name} <value>.\"); }} _moState[{name}] = {expr};")
    if isinstance(s, ClientReplaceIn):
        # "old" with VALUE in TARGET (as ALIAS)? -- split/join replaces EVERY occurrence
        # (matches Python's str.replace, which the server side uses) without the regex
        # special-character escaping a JS RegExp replace would need.
        new_expr = _source_expr(s.source_kind, s.source)
        target_expr = _name_expr(s.target)
        subst = (f"String({target_expr}).split({json.dumps(s.old)}).join(String({new_expr}))")
        if s.alias:
            # A PURE EXPRESSION: reads the target, writes only the new alias, never touches
            # the target -- works on a fluid OR held source alike (the one exception Ron's
            # ruling carries over from the server: "replacing text inside a value... keeps
            # working" is not a variable-state operation).
            return f"_moState[{json.dumps(s.alias)}] = {subst};"
        name = json.dumps(s.target)
        return (f"if (!_moHeld[{name}]) {{ throw new Error(\"'{s.target}' is a fluid client "
                f"value, not held -- replace only applies in place to a held value. Hold it "
                f"first: hold {s.target} <value>.\"); }} _moState[{name}] = {subst};")
    if isinstance(s, ClientNotify):
        if s.source_kind == 'result':
            expr = s.source
        elif s.source_kind == 'the':
            expr = _name_expr(s.source)
        else:
            expr = json.dumps(s.source)
        return f"_moNotify({expr});"
    if isinstance(s, ClientAlert):
        if s.source_kind == 'result':
            expr = s.source
        elif s.source_kind == 'the':
            expr = _name_expr(s.source)
        else:
            expr = json.dumps(s.source)
        # A real window.alert(), not a console fallback -- the whole point of `alert` over
        # `notify` is that it blocks until the person dismisses it, which only the native
        # dialog does. If `window.alert` is genuinely unavailable (a non-browser host), fail
        # loud rather than quietly logging instead -- a silently-skipped alert is the exact
        # shape this project refuses to ship.
        return (f"if (typeof window.alert !== 'function') {{ throw new Error("
                f"'this browser has no window.alert to call'); }} window.alert({expr});")
    if isinstance(s, ClientConfirm):
        if s.source_kind == 'result':
            expr = s.source
        elif s.source_kind == 'the':
            expr = _name_expr(s.source)
        else:
            expr = json.dumps(s.source)
        # A GATE, not a question answered and forgotten: on Cancel (false), the handler
        # stops right here -- every statement written after this line in the listener never
        # runs. `return` exits the enclosing event-handler function, which is exactly the
        # scope one `listen for` body owns, so a confirm before a `send` genuinely blocks it.
        return (f"if (typeof window.confirm !== 'function') {{ throw new Error("
                f"'confirm is not available in this environment'); }} "
                f"if (!window.confirm({expr})) {{ return; }}")
    if isinstance(s, ClientShare):
        if s.source_kind == 'page':
            # Bare `share` -- the current page's own URL (ruled by Ron, 2026-10-03).
            expr = "window.location.href"
        elif s.source_kind == 'elem':
            # `share #element` -- that element's LINK: its own href if it is a link, else the
            # first link found inside it, else fall back to the current page rather than
            # throwing (a share action should never crash the page over a missing link).
            _sel = json.dumps(s.source)
            expr = (f"(function(){{var e=document.querySelector({_sel}); if(!e){{throw new "
                    f"Error('no element matches {_sel}');}} var a=e.href?e:e.querySelector("
                    f"'a'); return (a&&a.href)||window.location.href;}})()")
        elif s.source_kind == 'result':
            expr = s.source
        elif s.source_kind == 'the':
            expr = _name_expr(s.source)
        else:
            expr = json.dumps(s.source)
        if s.destination:
            tmpl = _SHARE_DESTINATIONS.get(s.destination)
            if tmpl is None:
                raise ValueError(
                    f"'{s.destination}' is not a recognized share destination. Known "
                    f"destinations: {', '.join(sorted(_SHARE_DESTINATIONS))}. Add a new one "
                    f"to mohio_data/share_destinations.json rather than guessing a URL.")
            before, after = tmpl.split('{url}', 1)
            return (f"window.open({json.dumps(before)} + encodeURIComponent({expr}) + "
                    f"{json.dumps(after)}, '_blank');")
        # Bare form: the device's native share sheet. A failed or cancelled share does NOT
        # alert, unlike `copy` -- declining to share, or the API being unsupported on this
        # device, is a normal outcome here, not an error worth interrupting the page for.
        return (f"if (navigator.share) {{ navigator.share({{ url: {expr} }}).catch(function(){{}}); }}")
    if isinstance(s, ClientCopy):
        if s.source_kind == 'elem':
            expr = f"_moVal({json.dumps(s.source)})"
        elif s.source_kind == 'result':
            expr = s.source
        elif s.source_kind == 'the':
            expr = _name_expr(s.source)
        else:
            expr = json.dumps(s.source)
        return f"_moCopy({expr});"
    if isinstance(s, ClientAppend):
        expr = (_name_expr(s.source)
                if s.source_kind == 'the' else json.dumps(s.source))
        return (f"document.querySelectorAll({json.dumps(s.target)}).forEach(function(t){{ "
                f"if (t instanceof HTMLInputElement || t instanceof HTMLTextAreaElement || t instanceof HTMLSelectElement) {{ t.value += {expr}; }} "
                f"else {{ t.textContent += {expr}; }} }});")
    if isinstance(s, ClientAfter):
        body = " ".join(js for js in (_emit_stmt(x) for x in s.body) if js)
        return f"setTimeout(function(){{ {body} }}, {int(s.ms)});"
    if isinstance(s, ClientEvery):
        _POLLING[0] += 1
        try:
            body = " ".join(js for js in (_emit_stmt(x) for x in s.body) if js)
        finally:
            _POLLING[0] -= 1
        return (f"_moIntervals[{json.dumps(s.name)}] = "
                f"setInterval(function(){{ {body} }}, {int(s.ms)});")
    if isinstance(s, ClientStopInterval):
        return (f"clearInterval(_moIntervals[{json.dumps(s.name)}]); "
                f"delete _moIntervals[{json.dumps(s.name)}];")
    if isinstance(s, ClientNav):
        if s.op == 'goto':
            return f"window.location.assign({json.dumps(s.url)});"
        if s.op == 'back':
            return "window.history.back();"
        if s.op == 'reload':
            return "window.location.reload();"
    # NOT AN EMPTY STRING. Every branch above returns JavaScript, and the fallthrough
    # returned "" -- so a MioScript statement this emitter does not know about compiled to
    # NOTHING and the page shipped without it. That is the silent-no-op shape this project
    # treats as its worst: the check passes, the page loads, the button does not work, and
    # there is no message anywhere saying which line was dropped. A statement the grammar
    # accepts and the emitter cannot write is a gap in the emitter, and it says so.
    raise ValueError(
        "unknown MioScript statement %r -- the browser side has no way to write this, so "
        "the page would load with the statement silently missing. This is a gap in the "
        "MioScript emitter, not a mistake in the program." % (getattr(s, 'op', s),))


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


# What counts as a person still being there. A pointer moving, a key pressed, the page
# scrolled, something clicked, a finger on a touch screen. The set is fixed rather than
# configurable because "is anyone there" is one question, and a coder who had to list the
# events would be back to writing the boilerplate this construct exists to remove.
_IDLE_EVENTS = ('mousemove', 'keydown', 'scroll', 'click', 'touchstart')


def _emit_idle(idle):
    """on.idle: one timer, every activity event, the whole document.

    ONE timer shared across all of the events is the whole point. A timer per event would
    fire the body as soon as any SINGLE kind of activity stopped, so a person reading
    without touching the mouse would be logged out mid-sentence. The timer is reset by any
    of them, so the body runs only when all of them have been quiet together.

    Bound to `document` rather than to a selector so it sees activity anywhere on the page,
    and captured (`true`) so it still sees events that a handler further in stops from
    bubbling. `passive` promises nothing here calls preventDefault, which lets the browser
    keep scrolling smoothly while it watches.
    """
    body = "\n        ".join(js for js in (_emit_stmt(s) for s in idle.body) if js)
    events = ", ".join(json.dumps(e) for e in _IDLE_EVENTS)
    return ("  (function(){\n"
            "    var _t;\n"
            "    function _reset(){ clearTimeout(_t); _t = setTimeout(function(){\n"
            f"        {body}\n"
            f"    }}, {int(idle.ms)}); }}\n"
            f"    [{events}].forEach(function(e){{\n"
            "      document.addEventListener(e, _reset, {passive: true, capture: true});\n"
            "    });\n"
            "    _reset();\n"
            "  })();")


_EVERY_NAMES = set()


def compile_listeners(listeners):
    """Compile a list of ClientListener and ClientIdle nodes into a single JS bundle string.
    Returns '' when there are no client listeners (nothing to inject)."""
    listeners = [L for L in (listeners or []) if isinstance(L, (ClientListener, ClientIdle))]
    if not listeners:
        return ""
    _DECLARED_VARS.clear()
    _HELD_VARS.clear()
    _EVERY_NAMES.clear()
    stop_refs = []
    held_refs = []
    errors = []
    for L in listeners:
        _collect_vars(L.body, stop_refs, held_refs, errors)
    # A `stop <name>` that names no `every ... as <name>` on this page would clearInterval
    # on nothing and look like a working cancel that never fires -- the exact silent-wrong
    # shape this project refuses to ship. Caught at compile time, before any browser sees it.
    for ref_name, ref_line in stop_refs:
        if ref_name not in _EVERY_NAMES:
            errors.append(
                f"Line {ref_line}: `stop {ref_name}` does not match any `every ... as "
                f"{ref_name}` on this page, so there is no timer to cancel. Name the "
                f"interval you meant:\n        every 30 seconds as {ref_name}\n            "
                f"...\n        every: done")
    # release/clear/forget/rename/replace act ONLY on a held client value (Ron ruling,
    # 2026-10-04, the client mirror of the server's hold-only rule) -- checked here against
    # every `hold` collected across the WHOLE page (a second pass, same reason `stop_refs` is
    # checked after the loop above: a `hold` declared in a later listener still counts).
    for op, ref_name, ref_line in held_refs:
        if ref_name not in _HELD_VARS:
            errors.append(
                f"Line {ref_line}: `{op} {ref_name}` -- '{ref_name}' is a fluid client "
                f"value, not held, so {op} does not apply to it.\n"
                f"    Use plain assignment instead: `{ref_name} <value>`.\n"
                f"    Or hold it first if you want {op} to apply: `hold {ref_name} <value>`.")
    if errors:
        raise ValueError("\n\n".join(errors))
    parts = [_PRELUDE] + [_emit_idle(L) if isinstance(L, ClientIdle) else _emit_listener(L)
                          for L in listeners]
    state = "  var _moState = {};\n" if _DECLARED_VARS else ""
    held = "  var _moHeld = {};\n" if _HELD_VARS else ""
    intervals = "  var _moIntervals = {};\n" if _EVERY_NAMES else ""
    return "(function(){\n" + state + held + intervals + "\n".join(parts) + "\n})();"


def _collect_vars(stmts, stop_refs=None, held_refs=None, errors=None):
    stop_refs = [] if stop_refs is None else stop_refs
    held_refs = [] if held_refs is None else held_refs
    errors = [] if errors is None else errors
    for s in stmts or []:
        if isinstance(s, ClientHold):
            _DECLARED_VARS.add(s.name)
            _HELD_VARS.add(s.name)
        elif isinstance(s, ClientAssign):
            _DECLARED_VARS.add(s.name)
        elif isinstance(s, ClientRelease):
            held_refs.append(('release', s.name, getattr(s, 'line', 0)))
        elif isinstance(s, ClientClearVar):
            held_refs.append(('clear', s.name, getattr(s, 'line', 0)))
        elif isinstance(s, ClientForgetVar):
            held_refs.append(('forget', s.name, getattr(s, 'line', 0)))
        elif isinstance(s, ClientRename):
            held_refs.append(('rename', s.name, getattr(s, 'line', 0)))
            # Carries the hold to the new name, the same as the server's rename -- a later
            # operator (in program order OR not, this check runs after every listener is
            # collected) referencing the renamed-to name finds it already held.
            _DECLARED_VARS.add(s.target)
            _HELD_VARS.add(s.target)
        elif isinstance(s, ClientReplaceVar):
            held_refs.append(('replace', s.name, getattr(s, 'line', 0)))
        elif isinstance(s, ClientReplaceIn):
            if s.alias:
                # A pure expression (reads s.target, writes only s.alias) -- not a state
                # operation, so no held_refs check; the new alias is an ordinary fluid value.
                _DECLARED_VARS.add(s.alias)
            else:
                held_refs.append(('replace', s.target, getattr(s, 'line', 0)))
        elif isinstance(s, ClientEvery):
            # Two `every` blocks sharing one name would silently share one timer slot --
            # the second declaration's setInterval call overwrites the first's id in
            # _moIntervals, so a `stop` on that name cancels only the SECOND timer and the
            # first keeps running, uncancelable. Refused at compile time, not discovered by
            # a developer watching a "stopped" poll keep firing.
            if s.name in _EVERY_NAMES:
                errors.append(
                    f"Line {getattr(s, 'line', 0)}: `every ... as {s.name}` is declared "
                    f"more than once on this page. Each named interval needs its own name, "
                    f"or a later `stop {s.name}` would not know which timer it is cancelling.")
            _EVERY_NAMES.add(s.name)
            _collect_vars(s.body, stop_refs, held_refs, errors)
        elif isinstance(s, ClientStopInterval):
            stop_refs.append((s.name, getattr(s, 'line', 0)))
        elif isinstance(s, ClientAfter):
            _collect_vars(s.body, stop_refs, held_refs, errors)
        elif isinstance(s, ClientSend):
            _collect_vars(s.success, stop_refs, held_refs, errors)
            _collect_vars(s.failure, stop_refs, held_refs, errors)
        elif isinstance(s, ClientCheck):
            for _cond, body in s.branches:
                _collect_vars(body, stop_refs, held_refs, errors)
            _collect_vars(s.otherwise, stop_refs, held_refs, errors)


def collect_client_listeners(program):
    """Walk the program for MioScript nodes that compile to browser code.

    ClientIdle is collected here alongside ClientListener because it compiles through the
    same bundle: a node the compiler can emit but the walk never finds would be a block
    the coder wrote and the browser never received, with nothing said about it.
    """
    from mohio_ast import ClientListener, ClientIdle
    out, seen = [], set()
    def walk(node):
        if id(node) in seen:
            return
        seen.add(id(node))
        if isinstance(node, (ClientListener, ClientIdle)):
            out.append(node)
        for v in (vars(node).values() if hasattr(node, '__dict__') else []):
            for it in (v if isinstance(v, list) else [v]):
                if hasattr(it, '__dict__'):
                    walk(it)
    walk(program)
    return out
