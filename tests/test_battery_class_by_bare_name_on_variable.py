# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""A one-word name is a VARIABLE, never a field, so it is classified by the class its value carries,
never by another table's field of the same spelling (R71, T1-CLASS-BY-BARE-NAME-ON-VARIABLE).

THE BUG, from the bank lane (R71 public receipt): `message "Payment scheduled"` saved to an untagged
`result_message` refused with protected_into_untagged_field, because an unrelated table declares its
own `message [pii]`. The card route answered 500 with no money moved. Measured on 2abcc27e, the same
literal with that table tagging `message` [phi] or [pci] was also SHOWN as `****eipt`: by `show`, by a
`{{ }}`, by `give back`, by render, and after a task, a list or a join carried it. A one-word read
went through the field-read classifier, which asks every table's field of that name. `mio check`
already followed the value (a one-word read takes the class of the assignment that reaches it).

Rule 0, the invariant: a value is protected because of where it came from, not because of the name
of the variable that holds it. Enumerated from the code, the one-word classification sites:
  the dotted-read classifier in _eval (class, [hide], never store / never log, purposes)  1
  the give-back and as.json name mask (_egress_field_name)                                2
  the render {{ }} name mask                                                              1
  the ai.decide audit label for a weighed input                                           1
The ONE exception kept: a request field bound as a bare variable IS a field of the request. Nine
binding sites (enumerated by the structural case below) mark it, and its read keeps the field rule,
by the handler's own shape when it names one.

Run: python tools/run_tests.py --only class_by_bare_name_on_variable
"""
import ast
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CAN_FAIL = [
    {"file": "mohio_interpreter.py",
     "find": "                    if _rf is None:\n                        return val\n",
     "replace": "                    if _rf is None:\n                        _rf = (_fld, None)  # MUTATED: a variable is classified by its name\n",
     "note": "a one-word variable is classified by another table's same-named field"},
    {"file": "mohio_interpreter.py",
     "find": "    value._request_field = (str(name), str(shape) if shape else None)\n",
     "replace": "    pass  # MUTATED: a request field binding is not marked\n",
     "note": "a bare request field loses its field class, so a [phi] request value saves untagged"},
    {"file": "mohio_interpreter.py",
     "find": "        return str(parts[-1]) if parts and len(parts) > 1 else None\n",
     "replace": "        return str(parts[-1]) if parts else None  # MUTATED: a variable masked by name\n",
     "note": "give back masks a public variable named like a tagged field"},
    {"file": "mohio_interpreter.py",
     "find": "            elif (_masked_names and len(parts) > 1 and str(parts[-1]) in _masked_names\n",
     "replace": "            elif (_masked_names and parts and str(parts[-1]) in _masked_names  # MUTATED\n",
     "note": "render masks a public variable named like a tagged field"},
    {"file": "mohio_interpreter.py",
     "find": "            if '.' not in str(k):\n                return by_value\n",
     "replace": "            if '.' not in str(k):\n                return self._classify_audit_field(k, ctx)  # MUTATED: labelled by name\n",
     "note": "the decision audit labels a weighed public variable [phi]"},
]

_p = _f = 0
_fail = []


def check(label, cond, detail=""):
    global _p, _f
    print("  [" + ("PASS" if cond else "FAIL") + "] " + label)
    if not cond and detail:
        print("        " + str(detail)[-700:])
    if not cond:
        _fail.append(label)
    _p += bool(cond)
    _f += (not cond)


TMP = tempfile.mkdtemp(prefix="mohio_bare_name_var_")
ENV = {k: v for k, v in os.environ.items()
       if not k.startswith(("MOHIO_STORE_", "AWS_")) and k != "PYTHONDONTWRITEBYTECODE"}
ENV.update(PYTHONPATH=ROOT, MOHIO_ENCRYPTION_KEY="testkey", MOHIO_AI="mock", PYTHONUTF8="1")
_n = [0]


def run(src, db=None):
    _n[0] += 1
    path = os.path.join(TMP, "p%d.mho" % _n[0])
    db = db or os.path.join(TMP, "p%d.db" % _n[0])
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(src)
    r = subprocess.run([sys.executable, os.path.join(ROOT, "mio.py"), "run", path], cwd=TMP,
                       env=dict(ENV, DATABASE_URL=db), capture_output=True, text=True, timeout=300)
    return r.returncode, (r.stdout or "") + (r.stderr or ""), db


def rows(db, sql):
    if not os.path.exists(db):
        return None
    con = sqlite3.connect(db)
    try:
        return con.execute(sql).fetchall()
    except sqlite3.OperationalError:
        return None
    finally:
        con.close()


PUBLIC = "public receipt"


def header(tag):
    return """connect db as sqlite from env.DATABASE_URL
shape PublicStore
    pub_receipts as table
        record_key as text required unique
        result_message as text required
shape: done
shape PrivateStore
    priv_rows as table
        record_key as text required unique
        message as text required [%s]
shape: done
""" % tag


SAVE = """save to db.pub_receipts
    record_key "k1"
    result_message %s
save: done
show "SAVED"
"""

CASES = [
    ("a public variable saved to an untagged field",
     'message "public receipt"\n' + SAVE % "message", "public receipt"),
    ("a public variable joined into a sentence, then saved",
     'message "public receipt"\nline ("ref 7: " & message)\nshow line\n' + SAVE % "line",
     "ref 7: public receipt"),
    ("a public variable added to a list, then its first item saved",
     'message "public receipt"\nitems as list text\nadd message to items\nshow items\n'
     + SAVE % "items.first", "public receipt"),
    ("a public variable passed to a task's `message` and given back",
     'task receipt\n    take message as text\n    returns text\n    give back ("ok " & message)\n'
     'task: done\nmessage "public receipt"\ncall receipt with message as out\nshow out\n' + SAVE % "out",
     "ok public receipt"),
]

for tag in ("pii", "phi", "pci"):
    print("\nanother table tags `message` [%s]: a public variable named `message` stays public" % tag)
    print("=" * 78)
    for label, body, stored in CASES:
        rc, out, db = run(header(tag) + body)
        got = rows(db, "SELECT result_message FROM pub_receipts")
        check("%s: runs, and the row holds the public text" % label,
              rc == 0 and "SAVED" in out and got == [(stored,)], (rc, got, out[-500:]))
        check("%s: nothing is masked on the way out" % label, "****" not in out, out[-400:])
    rc, out, _ = run(header(tag) + 'message "public receipt"\nshow message\nshow "braces {{ message }}"\n')
    lines = [l.strip() for l in out.splitlines()]
    check("show, and a `{{ }}`: the public text, unmasked",
          rc == 0 and PUBLIC in lines and "braces " + PUBLIC in lines, out[-400:])
    for gb in ("give back message", "give back message as json"):
        rc, out, _ = run(header(tag) + 'message "public receipt"\n' + gb + "\n")
        check("`%s`: the public text, unmasked" % gb, rc == 0 and PUBLIC in out and "****" not in out,
              out[-400:])

print("\nthe adversarial half: a protected value is still protected, whatever its variable is called")
print("=" * 78)
for tag in ("pii", "phi", "pci"):
    seed = header(tag) + """save to db.priv_rows
    record_key "s1"
    message "SENTINEL-PRIVATE-%s"
save: done
retrieve.one sensitive from db.priv_rows
    match record_key to "s1"
retrieve.one: done
message sensitive.message
""" % tag.upper()
    rc, out, db = run(seed + SAVE % "message")
    got = rows(db, "SELECT count(*) FROM pub_receipts")
    check("[%s] copied into a variable named `message`, then saved untagged: refused, no row" % tag,
          rc != 0 and "SAVED" not in out and got in (None, [(0,)]), (rc, got, out[-500:]))
    rc, out, db = run(seed + 'receiptText ("ref: " & message)\n' + SAVE % "receiptText")
    got = rows(db, "SELECT count(*) FROM pub_receipts")
    check("[%s] joined into a differently named variable, then saved untagged: refused, no row" % tag,
          rc != 0 and "SAVED" not in out and got in (None, [(0,)]), (rc, got, out[-500:]))
    if tag in ("phi", "pci"):
        rc, out, _ = run(seed + "show message\n")
        check("[%s] shown through the variable: masked" % tag,
              "****" in out and "SENTINEL-PRIVATE" not in out, out[-400:])

print("\ntwo processes: a reader with no private shape still refuses a protected value at run time")
print("=" * 78)
shared = os.path.join(TMP, "two-process.db")
rc, out, _ = run(header("pii") + """save to db.priv_rows
    record_key "s2"
    message "SENTINEL-RUNTIME-PRIVATE"
save: done
show "SEEDED"
""", db=shared)
check("process 1 seeds one protected row", rc == 0 and "SEEDED" in out, out[-400:])
rc, out, _ = run("""connect db as sqlite from env.DATABASE_URL
shape PublicStore
    pub_receipts as table
        record_key as text required unique
        result_message as text required
shape: done
retrieve.one sensitive from db.priv_rows
    match record_key to "s2"
retrieve.one: done
message sensitive.message
""" + SAVE % "message", db=shared)
check("process 2, no private shape, copies it into `message` and saves it untagged: refused at run",
      rc != 0 and "protected_into_untagged_field" in out and "SAVED" not in out
      and rows(shared, "SELECT count(*) FROM pub_receipts") in (None, [(0,)]), out[-500:])

print("\na decision weighing a public variable named `message`")
print("=" * 78)
rc, out, db = run(header("phi") + """message "public receipt"
ai.decide needs_review returns boolean
    confidence above 0.5
    weigh message
    ai.audit to review_audit_log
    not confident
        give back pending "Sent to a human"
    on.failure
        give back [503] "unavailable"
ai.decide: done
ai.decide needs_review
show "DECIDED"
""")
_detail = [json.loads(r[0]) for r in (rows(db, "SELECT detail FROM review_audit_log") or [])]
_inputs = [json.loads(d.get("inputs") or "{}") for d in _detail]
check("it runs (the public value may be weighed), and the audit records it as unclassified",
      rc == 0 and "DECIDED" in out and _inputs == [{"message": "-"}], (rc, _inputs, out[-400:]))

print("\nserved over real HTTP: handlers, render, and bare request fields")
print("=" * 78)
APP = """connect db as sqlite from env.DATABASE_URL
shape PublicStore
    pub_receipts as table
        record_key as text required unique
        result_message as text required
shape: done
shape PrivateStore
    priv_rows as table
        record_key as text required unique
        message as text required [phi]
        note as text [phi]
shape: done
shape Pub
    note as text
shape: done
shape Patient
    ssn as text [phi]
shape: done
shape Late
    ssn as text [phi] default "000-00-0042"
    label as text
shape: done
shape Lookup
    ssn as text [phi]
shape: done

listen for sh.Pub at /pub
    message "public receipt"
    give back [200] message
listen: done

listen for sh.Pub at /pubrender
    message "public receipt"
    render
        <p>{{ message }}</p>
    render: done
listen: done

listen for sh.Pub at /pubsave
    message "public receipt"
    save to db.pub_receipts
        record_key "served-public"
        result_message message
    save: done
    give back [201] "saved"
listen: done

listen for sh.Pub at /ownplain
    save to db.pub_receipts
        record_key "served-note"
        result_message note
    save: done
    give back [200] note
listen: done

listen for sh.Patient at /ssn
    give back [200] ssn
listen: done

listen for sh.Patient at /ssnsave
    save to db.pub_receipts
        record_key "served-ssn"
        result_message ssn
    save: done
    give back [201] "MUST NOT SAVE"
listen: done

listen for sh.Late at /latesave
    save to db.pub_receipts
        record_key "served-late"
        result_message ssn
    save: done
    give back [201] "MUST NOT SAVE"
listen: done

request for sh.Lookup at /lookup
    give back [200] ssn
request: done
"""
SRV = os.path.join(TMP, "served")
os.makedirs(SRV)
with open(os.path.join(SRV, "app.mho"), "w", encoding="utf-8", newline="\n") as fh:
    fh.write(APP)
_sock = socket.socket()
_sock.bind(("127.0.0.1", 0))
PORT = _sock.getsockname()[1]
_sock.close()
SDB = os.path.join(SRV, "app.db")
_log = open(os.path.join(SRV, "serve.log"), "w", encoding="utf-8")   # drained to a file, never a PIPE
proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "mio.py"), "serve", os.path.join(SRV, "app.mho"),
                         "--port", str(PORT), "--host", "127.0.0.1"], cwd=SRV,
                        env=dict(ENV, DATABASE_URL=SDB), stdout=_log, stderr=subprocess.STDOUT)


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (PORT, path), data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


try:
    up = False
    for _ in range(240):
        time.sleep(0.5)
        if proc.poll() is not None:
            break
        if "Listening" in open(os.path.join(SRV, "serve.log"), encoding="utf-8", errors="replace").read():
            up = True
            break
    check("the app serves", up, open(os.path.join(SRV, "serve.log"), encoding="utf-8", errors="replace").read()[-600:])
    if up:
        SSN = "123-45-6789"
        st, b = call("POST", "/pub", {"note": "n"})
        check("give back of a public variable: 200 and the public text", st == 200 and PUBLIC in b and "****" not in b, (st, b))
        st, b = call("POST", "/pubrender", {"note": "n"})
        check("render {{ }} of a public variable: the public text", st == 200 and "<p>%s</p>" % PUBLIC in b, (st, b[-300:]))
        st, b = call("POST", "/pubsave", {"note": "n"})
        check("save of a public variable: 201 and the row stored",
              st == 201 and rows(SDB, "SELECT result_message FROM pub_receipts WHERE record_key = 'served-public'")
              == [(PUBLIC,)], (st, b))
        st, b = call("POST", "/ownplain", {"note": "plain note"})
        check("a request field its own shape declares plain, named like another table's [phi] field: "
              "saved and shown", st == 200 and "plain note" in b and "****" not in b
              and rows(SDB, "SELECT result_message FROM pub_receipts WHERE record_key = 'served-note'")
              == [("plain note",)], (st, b))
        st, b = call("POST", "/ssn", {"ssn": SSN})
        check("a bare [phi] request field given back: masked", st == 200 and "****6789" in b and SSN not in b, (st, b))
        st, b = call("POST", "/ssnsave", {"ssn": SSN})
        check("a bare [phi] request field saved untagged: refused, no row",
              st == 500 and rows(SDB, "SELECT count(*) FROM pub_receipts WHERE record_key = 'served-ssn'")
              == [(0,)], (st, b))
        st, b = call("POST", "/latesave", {"label": "x"})
        check("a defaulted [phi] request field saved untagged: refused, no row",
              st == 500 and rows(SDB, "SELECT count(*) FROM pub_receipts WHERE record_key = 'served-late'")
              == [(0,)], (st, b))
        st, b = call("GET", "/lookup?ssn=" + SSN)
        check("a bare [phi] field of a GET handler given back: masked", st == 200 and SSN not in b and "6789" in b, (st, b))
        _logtext = open(os.path.join(SRV, "serve.log"), encoding="utf-8", errors="replace").read()
        check("both refusals are protected_into_untagged_field",
              _logtext.count("protected_into_untagged_field") >= 2, _logtext[-800:])
finally:
    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"] if os.name == "nt" else ["kill", str(proc.pid)],
                   capture_output=True)
    try:
        proc.wait(timeout=30)
    except Exception:
        pass
    _log.close()

print("\nstructural: every bare binding keyed by a loop variable is a marked request field, or listed")
print("=" * 78)
# A request field bound bare WITHOUT the mark is read as a plain variable, so its field class is lost:
# the leak direction. Every ctx binding whose name is a loop variable is either marked, or named here
# with the reason its value carries its own class. A new one fails until it is one or the other.
NOT_A_REQUEST_FIELD = {
    ("_modify_statements", "k"): "a stored row's fields: a protected value comes back decrypted, "
                                 "carrying its own class",
    ("_render_mail_template", "slot"): "a mail template slot, given a value the program computed",
}
_src = open(os.path.join(ROOT, "mohio_interpreter.py"), encoding="utf-8").read()
_tree = ast.parse(_src)
_marked, _unmarked = [], []


class _Scan(ast.NodeVisitor):
    def __init__(self):
        self.fn, self.loops = [], []

    def visit_FunctionDef(self, n):
        self.fn.append(n.name)
        self.generic_visit(n)
        self.fn.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_For(self, n):
        # The loop variable, and any name the loop body assigns (`_fn = _p['name']`), since a
        # binding keyed by either is one binding per item.
        names = {t.id for t in ast.walk(n.target) if isinstance(t, ast.Name)}
        for s in n.body:
            for a in ast.walk(s):
                if isinstance(a, ast.Assign):
                    names |= {t.id for tg in a.targets for t in ast.walk(tg) if isinstance(t, ast.Name)}
        self.loops.append(names)
        self.generic_visit(n)
        self.loops.pop()

    def visit_Call(self, n):
        f = n.func
        if (isinstance(f, ast.Attribute) and f.attr == "set" and len(n.args) >= 2
                and isinstance(n.args[0], ast.Name) and any(n.args[0].id in s for s in self.loops)):
            v = n.args[1]
            key = (self.fn[-1] if self.fn else "<module>", n.args[0].id)
            if isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id == "_mark_request_field":
                _marked.append((n.lineno,) + key)
            elif key not in NOT_A_REQUEST_FIELD:
                _unmarked.append((n.lineno,) + key)
        self.generic_visit(n)


_Scan().visit(_tree)
check("no bare binding keyed by a loop variable is unmarked and unlisted", not _unmarked, _unmarked)
_fns = {m[1] for m in _marked}
check("the scan sees the request-binding sites (top-level run, POST and GET handlers, page, session, "
      "shape coercion)",
      {"_run_inner", "_exec_new_listener", "_exec_request_listener", "_serve_page",
       "_run_with_session_inner", "_validate_and_coerce_shape"} <= _fns and len(_marked) >= 9,
      sorted(_marked))

shutil.rmtree(TMP, ignore_errors=True)
print("\n" + "=" * 78)
print("RESULTS: %d passed, %d failed" % (_p, _f))
for x in _fail:
    print("  FAILED: " + x)
sys.exit(1 if _f else 0)
