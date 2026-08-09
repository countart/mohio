# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Non-ASCII langmap path: the ASCII gate must run AFTER Layer-1 translation, not before.

Locked pipeline (langmap reference S2): Layer 3 -> Layer 1 -> parser; the parser only ever
sees canonical English. Gating the untranslated source rejected every non-Latin pack
(Devanagari/Hindi, emoji, Cyrillic) before Layer 1 could translate it. Guard that regression.
"""
import subprocess, sys, os, tempfile
env = dict(os.environ, PYTHONPATH=os.getcwd(), DATABASE_URL=':memory:')
_p = _f = 0
def check(label, cond):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    _p += bool(cond); _f += (not cond)

def run(verb, path):
    return subprocess.run([sys.executable, 'mio.py', verb, path], env=env,
                          capture_output=True, text=True, timeout=180)

# A non-ASCII (emoji) pack translates to canonical English and runs.
if os.path.exists('examples/emoji_hello.mho'):
    r = run('check', 'examples/emoji_hello.mho')
    check("non-ASCII langmap file passes check", r.returncode == 0)
    r = run('run', 'examples/emoji_hello.mho')
    check("non-ASCII langmap file runs (Layer-1 translated)", 'Welcome to Mohio' in r.stdout)

# The gate still fires for genuine non-ASCII in a keyword slot with NO langmap.
fd, bad = tempfile.mkstemp(suffix='.mho'); os.write(fd, 'sh\u00f6w "x"\n'.encode()); os.close(fd)
check("gate still rejects non-ASCII keyword (no langmap)", run('check', bad).returncode == 1)
os.unlink(bad)

# Non-ASCII stays legal in comments and string literals.
fd, ok = tempfile.mkstemp(suffix='.mho')
os.write(fd, '// caf\u00e9\nshow "h\u00e9llo w\u00f6rld"\n'.encode()); os.close(fd)
check("non-ASCII still allowed in strings/comments", run('check', ok).returncode == 0)
os.unlink(ok)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
