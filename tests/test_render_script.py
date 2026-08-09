# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""`render` is markup. Script must declare itself: `render scripts`.

A <script> tag inside a render block passed `mio check` with zero errors and zero warnings.
So "no arbitrary JS, by design" was a documentation claim, not a compiler rule -- in a
language whose entire pitch is that the compiler enforces the rules rather than a style guide
asking nicely. The container now NAMES the content, so the escape hatch is explicit,
greppable, and enforced.
"""
import subprocess, sys, os, tempfile
env = dict(os.environ, PYTHONPATH=os.getcwd(), DATABASE_URL=':memory:')
_p = _f = 0
H = ('shape Home\n    title as text\nshape: done\n\n'
     'listen for\n    request for sh.Home at /\n')
TAIL = '    request: done\nlisten: done\n'
def check(label, body, want):
    global _p, _f
    fd, path = tempfile.mkstemp(suffix='.mho'); os.write(fd, (H + body + TAIL).encode()); os.close(fd)
    r = subprocess.run([sys.executable, 'mio.py', 'check', path], env=env,
                       capture_output=True, text=True, timeout=200)
    os.unlink(path)
    ok = (r.returncode == want)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label} (exit {r.returncode}, want {want})")
    _p += ok; _f += (not ok)

check("plain render, markup only, passes",
      '        render\n            <h1>Hi</h1>\n        render: done\n', 0)
check("<script> in render fails loud",
      '        render\n            <script>x=1;</script>\n        render: done\n', 1)
check("<script> in `render html` fails loud",
      '        render html\n            <script>x=1;</script>\n        render: done\n', 1)
check("inline onclick= counts as script",
      '        render\n            <button onclick="go()">Go</button>\n        render: done\n', 1)
check("`render scripts` is the declared hatch",
      '        render scripts\n            <script>x=1;</script>\n        render: done\n', 0)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
