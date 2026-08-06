# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
test_miofile.py — miofile local operations: thorough black-box test

Isolated sandbox per run: MIOFILE_ROOT=/tmp/miofile_test_<pid>
Covers: happy path, fail-loud, content/types, interplay, regression.

Run: PYTHONPATH=$PWD DATABASE_URL=:memory: python3 tests/test_miofile.py
"""
import os, sys, shutil, tempfile
sys.argv = ['mio.py']
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter, AiDecision, MohioRuntimeError
from mohio_server import MohioServer, create_app
from starlette.testclient import TestClient

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

def check_in(label, haystack, needle):
    global _passed, _failed
    if needle in str(haystack):
        _passed += 1; print(f"  ok   {label}")
    else:
        _failed += 1; print(f"  FAIL {label}: {needle!r} not in output")

_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)

class MockAI:
    def register_chain(self, *a, **k): pass
    def decide(self, name='', inputs=None, **k):
        return AiDecision(result=None, confidence=0.9, fell_back=False,
                          model='mock', inputs=inputs or {})

# ── sandbox ───────────────────────────────────────────────────────────────────
SANDBOX = f'/tmp/miofile_test_{os.getpid()}'

def fresh_sandbox():
    if os.path.exists(SANDBOX):
        shutil.rmtree(SANDBOX)
    os.makedirs(SANDBOX, exist_ok=True)
    os.environ['MIOFILE_ROOT'] = SANDBOX

def run(src, request=None):
    """Parse and run a .mho snippet. Returns the interpreter result."""
    prog = transform(_P.parse(src), src)
    it = MohioInterpreter(ai=MockAI())
    return it.run(prog, request)

def run_expect_error(label, src):
    """Run and assert it raises. Returns the error message."""
    try:
        run(src)
        check_true(f"{label} raises", False)
        return ""
    except Exception as e:
        check_true(f"{label} raises", True)
        return str(e)

def file_at(relpath):
    return os.path.join(SANDBOX, relpath)

def file_exists(relpath):
    return os.path.exists(file_at(relpath))

def file_content(relpath):
    with open(file_at(relpath), 'r', encoding='utf-8') as f:
        return f.read()


# ══════════════════════════════════════════════════════════════════════════════
# HAPPY PATH
# ══════════════════════════════════════════════════════════════════════════════
print("\n── Happy path ──")
fresh_sandbox()

# write then read
run('miofile.write "hello.txt" "hello world"')
check_true("write creates file", file_exists("hello.txt"))
check("write content correct", file_content("hello.txt"), "hello world")

run('miofile.read "hello.txt" as content\nshow content')
# The show is for the interpreter's internal state; read binding is tested via interplay below

# exists
run('miofile.exists "hello.txt"')
check_true("exists true after write", file_exists("hello.txt"))

# write creates parent folders
run('miofile.write "a/b/c.txt" "nested"')
check_true("nested write creates parents", file_exists("a/b/c.txt"))
check("nested content correct", file_content("a/b/c.txt"), "nested")

# copy leaves source
run('miofile.copy "hello.txt" to "hello_copy.txt"')
check_true("copy: source still exists", file_exists("hello.txt"))
check_true("copy: dest exists", file_exists("hello_copy.txt"))
check("copy: dest content matches", file_content("hello_copy.txt"), "hello world")

# move removes source
run('miofile.write "moveme.txt" "moving"')
run('miofile.move "moveme.txt" to "moved.txt"')
check_true("move: source gone", not file_exists("moveme.txt"))
check_true("move: dest exists", file_exists("moved.txt"))
check("move: dest content", file_content("moved.txt"), "moving")

# list returns sorted names
run('miofile.write "listdir/b.txt" "b"')
run('miofile.write "listdir/a.txt" "a"')
run('miofile.write "listdir/c.txt" "c"')
# list is tested via interplay (binding via as)

# overwrite replaces content
run('miofile.write "hello.txt" "replaced"')
check("overwrite replaces", file_content("hello.txt"), "replaced")

# delete
run('miofile.delete "hello_copy.txt"')
check_true("delete removes file", not file_exists("hello_copy.txt"))

# exists false after delete
check_true("exists false after delete", not file_exists("hello_copy.txt"))


# ══════════════════════════════════════════════════════════════════════════════
# FAIL-LOUD
# ══════════════════════════════════════════════════════════════════════════════
print("\n── Fail-loud ──")
fresh_sandbox()

# read missing file
msg = run_expect_error("read missing", 'miofile.read "nope.txt" as x')
check_in("read missing message", msg, "no file")

# delete missing file
run_expect_error("delete missing", 'miofile.delete "nope.txt"')

# move missing source
run_expect_error("move missing src", 'miofile.move "nope.txt" to "dest.txt"')

# copy missing source
run_expect_error("copy missing src", 'miofile.copy "nope.txt" to "dest.txt"')

# list a file (not a folder)
run('miofile.write "afile.txt" "x"')
run_expect_error("list non-folder", 'miofile.list "afile.txt" as items')


# ── Path safety ───────────────────────────────────────────────────────────────
print("\n── Path safety ──")
fresh_sandbox()

for label, path in [
    ("parent escape",     "../../etc/passwd"),
    ("absolute unix",     "/etc/passwd"),
    ("absolute tmp",      "/tmp/x"),
    ("mid-path escape",   "notes/../../x"),
]:
    msg = run_expect_error(f"write blocked: {label}",
        f'miofile.write "{path}" "pwned"')
    msg2 = run_expect_error(f"read blocked: {label}",
        f'miofile.read "{path}" as x')

# Windows-style escape
msg = run_expect_error("write blocked: win escape",
    'miofile.write "..\\\\..\\\\x" "pwned"')

# Legitimate nested path IS allowed
run('miofile.write "deep/nested/file.txt" "legit"')
check_true("nested path allowed", file_exists("deep/nested/file.txt"))


# ══════════════════════════════════════════════════════════════════════════════
# CONTENT / TYPES
# ══════════════════════════════════════════════════════════════════════════════
print("\n── Content / types ──")
fresh_sandbox()

# write a number → stored as text form
run('miofile.write "num.txt" 42')
check("number stored as text", file_content("num.txt"), "42")

# write a boolean → stored as text form
run('miofile.write "bool.txt" true')
content = file_content("bool.txt")
check_true("boolean stored as text", content in ("true", "True"))


# ══════════════════════════════════════════════════════════════════════════════
# INTERPLAY — miofile inside handlers and tasks
# ══════════════════════════════════════════════════════════════════════════════
print("\n── Interplay: handler ──")
fresh_sandbox()

# Write then read inside a served request
src = '''\
listen for
    new sh.FileOp at /write
        miofile.write "served.txt" "from handler"
        give back created "written"
    new: done
    request for sh.FileOp at /read
        miofile.read "served.txt" as content
        give back ok content
    request: done
listen: done
'''
prog = transform(_P.parse(src), src)
interp = MohioInterpreter(ai=MockAI())
server = MohioServer(prog, interp)
app = create_app(server)
c = TestClient(app, raise_server_exceptions=False)

r = c.post("/write", json={"_shape": "FileOp"})
check("handler write status", r.status_code, 201)

r = c.get("/read")
import re
def unwrap(r):
    try:
        data = r.json()
    except Exception:
        return r.text.strip()
    if isinstance(data, list):
        return str(data)
    msg = data.get("message", data.get("body", ""))
    if isinstance(msg, str):
        m = re.match(r"MohioValue\('(.+?)',", msg)
        if m: return m.group(1)
    return str(msg)

check("handler read content", unwrap(r), "from handler")

# list binding via as
run('miofile.write "ltest/x.txt" "x"')
run('miofile.write "ltest/y.txt" "y"')
src2 = '''\
listen for
    request for sh.Q at /list
        miofile.list "ltest" as files
        give back ok files
    request: done
listen: done
'''
prog2 = transform(_P.parse(src2), src2)
interp2 = MohioInterpreter(ai=MockAI())
server2 = MohioServer(prog2, interp2)
app2 = create_app(server2)
c2 = TestClient(app2, raise_server_exceptions=False)
r = c2.get("/list")
val = unwrap(r)
check_in("list returns file names", val, "x.txt")
check_in("list returns both files", val, "y.txt")


# ══════════════════════════════════════════════════════════════════════════════
# REGRESSION — unwired services still fail loud
# ══════════════════════════════════════════════════════════════════════════════
print("\n── Regression: unwired services ──")
fresh_sandbox()

for svc in ["miopdf.merge", "mioimage.resize"]:
    try:
        src = f'{svc} "test"'
        prog = transform(_P.parse(src), src)
        it = MohioInterpreter(ai=MockAI())
        it.run(prog, None)
        check_true(f"{svc} still fails loud", False)
    except Exception as e:
        check_in(f"{svc} error message", str(e), "not yet executable")

# miofile must NOT be in the stub set anymore
try:
    run('miofile.write "regression.txt" "works"')
    check_true("miofile is no longer stubbed", file_exists("regression.txt"))
except Exception as e:
    if "not yet executable" in str(e):
        check_true("miofile NOT stubbed", False)
    else:
        check_true(f"miofile error (not stub): {e}", False)


# ══════════════════════════════════════════════════════════════════════════════
# Cleanup
shutil.rmtree(SANDBOX, ignore_errors=True)
os.environ.pop('MIOFILE_ROOT', None)

print(f"\n{'=' * 60}")
print(f"  {_passed} passed, {_failed} failed")
if _failed:
    print(f"  *** {_failed} FAILURE(S) ***")
print(f"{'=' * 60}")
sys.exit(1 if _failed else 0)
