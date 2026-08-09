// Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
// Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
/**
 * mioscript_harness.js — run compiled MioScript in jsdom, drive events, report DOM state.
 *
 * Usage: echo '{"html":"...","steps":[...],"asserts":[...]}' | node mioscript_harness.js
 *
 * Input JSON:
 *   html:           full HTML with <script> containing the MioScript bundle
 *   steps:          [{action, selector, event, value, delay_ms}]
 *   asserts:        [{selector, prop, expected, check_type, label}]
 *   fetch_response: {ok, status, body}  (mock for send tests)
 *
 * Output JSON:
 *   assert_results: [{label, ok, actual, expected, error}]
 */
const { JSDOM } = require("jsdom");

let input = "";
process.stdin.setEncoding("utf-8");
process.stdin.on("data", (c) => (input += c));
process.stdin.on("end", async () => {
  try {
    const spec = JSON.parse(input);
    const fr = spec.fetch_response || { ok: true, status: 200, body: '{"message":"ok"}' };

    const dom = new JSDOM(spec.html || "<html><body></body></html>", {
      runScripts: "dangerously",
      resources: "usable",
      pretendToBeVisual: true,
      beforeParse(window) {
        window.fetch = (url, opts) => {
          window.__lastFetchUrl = url;
          window.__lastFetchOpts = opts;
          const bodyStr = typeof fr.body === "string" ? fr.body : JSON.stringify(fr.body);
          return Promise.resolve({
            ok: fr.ok !== false,
            status: fr.status || 200,
            text: () => Promise.resolve(bodyStr),
            json: () => Promise.resolve(JSON.parse(bodyStr)),
          });
        };
        window.FormData = class FormData {
          constructor(form) {
            this._d = {};
            if (form) form.querySelectorAll("input,textarea,select").forEach(
              (el) => { if (el.name) this._d[el.name] = el.value; });
          }
          get(k) { return this._d[k]; }
          set(k, v) { this._d[k] = v; }
          entries() { return Object.entries(this._d); }
          [Symbol.iterator]() { return Object.entries(this._d)[Symbol.iterator](); }
        };
      },
    });

    const doc = dom.window.document;
    await new Promise((r) => setTimeout(r, 50));

    for (const s of spec.steps || []) {
      if (s.action === "set") {
        const el = doc.querySelector(s.selector);
        if (el) el.value = s.value;
      } else if (s.action === "dispatch") {
        const el = doc.querySelector(s.selector);
        if (!el) continue;
        if (s.value !== undefined) el.value = s.value;
        el.dispatchEvent(new dom.window.Event(s.event, { bubbles: true, cancelable: true }));
        await new Promise((r) => setTimeout(r, s.delay_ms || 10));
      } else if (s.action === "submit") {
        const el = doc.querySelector(s.selector);
        if (el) {
          el.dispatchEvent(new dom.window.Event("submit", { bubbles: true, cancelable: true }));
          await new Promise((r) => setTimeout(r, s.delay_ms || 50));
        }
      } else if (s.action === "wait") {
        await new Promise((r) => setTimeout(r, s.delay_ms || 100));
      }
    }

    const results = [];
    for (const a of spec.asserts || []) {
      const entry = { label: a.label || "", ok: false, actual: null, expected: a.expected, error: null };
      try {
        if (a.prop === "fetch_url") {
          entry.actual = dom.window.__lastFetchUrl || null;
        } else if (a.prop === "fetch_method") {
          entry.actual = (dom.window.__lastFetchOpts || {}).method || null;
        } else if (a.prop === "count") {
          entry.actual = doc.querySelectorAll(a.selector).length;
        } else {
          const el = doc.querySelector(a.selector);
          if (!el) { entry.error = "not found: " + a.selector; results.push(entry); continue; }
          if (a.prop === "textContent") entry.actual = el.textContent;
          else if (a.prop === "innerHTML") entry.actual = el.innerHTML;
          else if (a.prop === "value") entry.actual = el.value;
          else if (a.prop === "classList") entry.actual = Array.from(el.classList).join(" ");
          else if (a.prop === "disabled") entry.actual = String(el.disabled);
          else if (a.prop === "hidden") entry.actual = String(el.hidden);
          else if (a.prop === "display") entry.actual = el.style.display;
          else if (a.prop === "exists") entry.actual = "true";
          else entry.actual = el.getAttribute(a.prop);
        }
        const act = String(entry.actual), exp = String(a.expected);
        if (a.check_type === "contains") entry.ok = act.includes(exp);
        else if (a.check_type === "not_contains") entry.ok = !act.includes(exp);
        else if (a.check_type === "truthy") entry.ok = !!entry.actual;
        else entry.ok = act === exp;
      } catch (e) { entry.error = e.message; }
      results.push(entry);
    }

    process.stdout.write(JSON.stringify({ assert_results: results }));
    dom.window.close();
  } catch (e) {
    process.stdout.write(JSON.stringify({ error: e.message }));
    process.exit(1);
  }
});
