# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""give back is XSS-safe by default; `trusted` opts out (2026-07-31, security sweep S8.2).

give back HTML-escapes interpolated {{ }} VALUES so untrusted data reflected into an HTML response
cannot inject markup. Authored markup is untouched (only the interpolated value is escaped). The
explicit `trusted` modifier opts out for intentional raw HTML / pre-built markup -- declared, never
inferred (no content sniffing). Mirrors the `render` view block, which already escaped.

Uses the real CLI. Run: `python tests/test_giveback_xss.py`.
"""
import os, subprocess, sys, tempfile

REPO = os.getcwd(); MIO = os.path.join(REPO, "mio.py")
ENV = dict(os.environ, PYTHONPATH=REPO, DATABASE_URL=":memory:", PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
_p = _f = 0
def rec(label, ok, detail=""):
    global _p, _f
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{('' if ok else '  -- ' + detail)}")
    _p += ok; _f += (not ok)
def run(src, cmd="run"):
    fd, path = tempfile.mkstemp(suffix=".mho"); os.write(fd, src.encode("utf-8")); os.close(fd)
    try:
        r = subprocess.run([sys.executable, MIO, cmd, path], cwd=REPO, env=ENV, capture_output=True, text=True, timeout=60)
        return r.returncode, (r.stdout + r.stderr)
    finally:
        os.unlink(path)

# 1. default give back ESCAPES an injected <script> (the XSS attack is neutralized)
c, out = run('evil "<script>alert(1)</script>"\ngive back 200 "<p>{{ evil }}</p>"\n')
rec("default give back escapes injected {{ }} (no raw <script>)",
    "&lt;script&gt;" in out and "<script>alert(1)" not in out, out[-200:])

# 2. `trusted` opts out -> raw markup preserved (for intentional HTML)
c, out = run('markup "<b>bold</b>"\ngive back 200 "<p>{{ markup }}</p>" trusted\n')
rec("`trusted` opts out to raw markup", "<b>bold</b>" in out and "&lt;b&gt;" not in out, out[-200:])

# 3. authored markup is untouched by escaping (only the VALUE is escaped)
c, out = run('name "Bo"\ngive back 200 "<h1>Hello {{ name }}</h1>"\n')
rec("authored <h1> markup preserved; value escaped", "<h1>Hello Bo</h1>" in out, out[-200:])

# 4. plain-text {{ }} data unchanged (numbers/names have no HTML chars -> no double-escaping regressions)
c, out = run('score 42\ngive back 200 "Your score is {{ score }}."\n')
rec("plain-text {{ }} unchanged (corpus give-backs keep working)", "Your score is 42." in out, out[-200:])

# 5. the render view block still escapes (no regression on the path that was already safe)
c, out = run('evil "<img src=x onerror=alert(1)>"\nrender\n    <p>{{ evil }}</p>\nrender: done\n')
rec("render view block still escapes", "onerror=alert" not in out or "&lt;img" in out, out[-200:])

# 6. URL-scheme allowlist in href context: `javascript:` is stripped (HTML-escaping alone would NOT
#    stop it -- the colon and text survive escaping and the browser still runs the script).
c, out = run('u "javascript:alert(1)"\ngive back 200 "<a href=\\"{{ u }}\\">go</a>"\n')
rec("javascript: in href is stripped (href=\"\")",
    'href=""' in out and "javascript:alert" not in out, out[-200:])

# 7. data: URL in href is likewise stripped (data:text/html;... is an XSS vector)
c, out = run('u "data:text/html,<script>alert(1)</script>"\ngive back 200 "<a href=\\"{{ u }}\\">x</a>"\n')
rec("data: in href is stripped", 'href=""' in out and "data:text/html" not in out, out[-200:])

# 8. obfuscated `java\tscript:` (control char inside the scheme) is still caught (browser-style normalize)
c, out = run('u "java\tscript:alert(1)"\ngive back 200 "<a href=\\"{{ u }}\\">x</a>"\n')
rec("obfuscated java\\tscript: is stripped", 'href=""' in out and "script:alert" not in out, out[-200:])

# 9. a legitimate https:// link still works (allowlist does not break normal URLs)
c, out = run('u "https://example.com/page?q=1"\ngive back 200 "<a href=\\"{{ u }}\\">x</a>"\n')
rec("https:// href preserved", 'href="https://example.com/page?q=1"' in out, out[-200:])

# 10. mailto: still works, and a scheme-less relative URL passes (no scheme = not rejected)
c, out = run('u "mailto:a@b.com"\ngive back 200 "<a href=\\"{{ u }}\\">x</a>"\n')
rec("mailto: href preserved", 'href="mailto:a@b.com"' in out, out[-200:])
c, out = run('u "/dashboard/home"\ngive back 200 "<a href=\\"{{ u }}\\">x</a>"\n')
rec("relative /path href preserved", 'href="/dashboard/home"' in out, out[-200:])

# 11. text context is unaffected by the URL allowlist -- a javascript: string in body text is escaped,
#     not stripped (it is not a live vector there, and stripping would corrupt legitimate content).
c, out = run('u "javascript:alert(1)"\ngive back 200 "<p>see {{ u }}</p>"\n')
rec("javascript: in TEXT context escaped, not stripped",
    "javascript:alert(1)" in out and "<p>see javascript:alert(1)</p>" in out, out[-200:])

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
