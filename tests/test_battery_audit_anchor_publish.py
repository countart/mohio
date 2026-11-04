# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Signed audit anchors: `mio audit publish` signs each log's chain head with the tenant's Ed25519
key, and `mio audit verify-anchors --verify-sig KEY` refuses an anchor whose signature does not hold
(MALFORMED-SIG) before its head is compared (design: audit anchor publishing, 2026-10-31).

Everything goes through the real `mio run` (to build a real audit chain) and the real `mio audit`
and `mio serve` commands, and asserts the answer each gives:
  - a published head at length L, the log truncated to L-k: verify exits 1 naming TRUNCATION at L;
  - the database swapped for another chain of the same length: REPOINT;
  - a middle record altered: the chain itself breaks;
  - a signature byte flipped, a signature removed, a signed field edited, the wrong key, an anchor
    filed under another log: each is MALFORMED-SIG and none passes;
  - the private key is in no anchor, no output and nothing the store receives, and the public key
    alone verifies (the store holds no secret);
  - the store's own success status is the only thing that says the anchors were delivered;
  - a broken chain is never signed;
  - `mio serve` with MOHIO_AUDIT_ANCHOR_INTERVAL publishes on its timer, and refuses to start when
    the timer could never publish;
  - without --verify-sig, verify-anchors prints what it always printed.

Run: PYTHONPATH=$PWD python tests/test_battery_audit_anchor_publish.py
"""
import base64
import http.server
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

CAN_FAIL = [
    {"file": "mio.py",
     "find": "        public_key.verify(signature, _anchor_signed_bytes(record))\n",
     "replace": "        pass  # MUTATED: the signature is never checked\n",
     "note": "a flipped or edited signed anchor passes"},
    {"file": "mio.py",
     "find": "                anchor_map[_log] = _kept\n",
     "replace": "                pass  # MUTATED: a rejected anchor still reaches the head comparison\n",
     "note": "a MALFORMED-SIG anchor is still counted as holding"},
    {"file": "mio.py",
     "find": "    if record.get(\"log\") != log:\n",
     "replace": "    if False:  # MUTATED: an anchor is not bound to the log it is filed under\n",
     "note": "an anchor signed for one log satisfies another"},
    {"file": "mio.py",
     "find": "    if record.get(\"key_id\") != key_id:\n",
     "replace": "    if False:  # MUTATED: the key id is not compared\n",
     "note": "an anchor from another key is not named as such"},
    {"file": "mio.py",
     "find": "    if not 200 <= status < 300:\n",
     "replace": "    if False:  # MUTATED: any answer from the store reads as delivered\n",
     "note": "a 503 from the store is reported as delivered"},
    {"file": "mio.py",
     "find": "        _start_anchor_timer()       # off unless MOHIO_AUDIT_ANCHOR_INTERVAL is set; refuses before listening\n",
     "replace": "        pass  # MUTATED: the directory server never starts the anchor timer\n",
     "note": "mio serve <dir> with MOHIO_AUDIT_ANCHOR_INTERVAL publishes nothing"},
]

_p = _f = 0
_fail = []


def check(label, cond, detail=""):
    global _p, _f
    print("  [" + ("PASS" if cond else "FAIL") + "] " + label)
    if not cond and detail:
        print("        " + str(detail)[:900])
    if not cond:
        _fail.append(label)
    _p += bool(cond)
    _f += (not cond)


tmp = tempfile.mkdtemp(prefix="mohio_anchorpub_")
BASE_ENV = {k: v for k, v in os.environ.items()
            if not k.startswith(("MOHIO_STORE_", "AWS_", "MOHIO_AUDIT_ANCHOR"))}
BASE_ENV.update(PYTHONPATH=ROOT, MOHIO_ENCRYPTION_KEY="testkey", NO_COLOR="1", PYTHONUTF8="1")

key = Ed25519PrivateKey.generate()
KEY_PEM = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
KEY_SEED = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                             serialization.NoEncryption())
PUB = os.path.join(tmp, "anchor.pub")
with open(PUB, "wb") as fh:
    fh.write(key.public_key().public_bytes(serialization.Encoding.PEM,
                                           serialization.PublicFormat.SubjectPublicKeyInfo))
other = Ed25519PrivateKey.generate()
OTHER_PUB = os.path.join(tmp, "other.pub")
with open(OTHER_PUB, "wb") as fh:
    fh.write(other.public_key().public_bytes(serialization.Encoding.PEM,
                                             serialization.PublicFormat.SubjectPublicKeyInfo))
# Every form the private key can be written in. None of them may appear anywhere but the env.
SECRETS = [KEY_PEM.strip(), KEY_PEM.split("-----")[2].strip(), KEY_SEED.hex(),
           base64.b64encode(KEY_SEED).decode()]


def mio(db, *args, env=None, cwd=None, timeout=600):
    e = dict(BASE_ENV, DATABASE_URL=db)
    e.update(env or {})
    r = subprocess.run([sys.executable, os.path.join(ROOT, "mio.py")] + list(args), cwd=cwd or tmp, env=e,
                       capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout or "", r.stderr or ""


PROG = os.path.join(tmp, "write.mho")
with open(PROG, "w", encoding="utf-8", newline="\n") as fh:
    fh.write('connect db from env.DATABASE_URL\nshape S\n    notes as table\n        body as text [pii]\n'
             'shape: done\nsave to db.notes\n    body "synthetic"\nsave: done\n')


def build(db, runs):
    for _ in range(runs):
        rc, out, err = mio(db, "run", PROG)
        if rc != 0:
            raise SystemExit(f"setup: mio run failed: {out[-400:]} {err[-400:]}")
    rc, out, _err = mio(db, "audit", "head", db)
    m = re.search(r"(data_audit_log)\s+head\s+(\w+)\s+entries\s+(\d+)", out)
    return m.group(1), m.group(2), int(m.group(3))


def chain_order(db, log):
    con = sqlite3.connect(db)
    by_prev = dict(con.execute(f"select prev_hash, entry_hash from {log}").fetchall())
    con.close()
    cur, order = "0" * 64, []
    while cur in by_prev:
        order.append(by_prev[cur])
        cur = by_prev[cur]
    return order


KEYED = {"MOHIO_AUDIT_ANCHOR_KEY": KEY_PEM}
DB = os.path.join(tmp, "app.db")
LOG, HEAD, L = build(DB, 5)

print("\n-- publish signs the head the chain reports, and the public key alone verifies it --")
rc, out, err = mio(DB, "audit", "publish", DB, env=KEYED)
check("publish exits 0 on an intact chain", rc == 0, out + err)
try:
    published = json.loads(out)
except ValueError:
    published = {}
rec = (published.get(LOG) or [{}])[0]
check("stdout is the anchors file shape {log: [anchor]}, nothing else", isinstance(published.get(LOG), list), out[:400])
check("the anchor records the head and length `mio audit head` reports",
      rec.get("head") == HEAD and rec.get("length") == L, (rec, HEAD, L))
check("the anchor is versioned (schema mohio.audit.anchor/1) and names its key and algorithm",
      rec.get("schema") == "mohio.audit.anchor/1" and rec.get("alg") == "Ed25519"
      and re.fullmatch(r"[0-9a-f]{16}", rec.get("key_id") or "") is not None, rec)
signed = json.dumps({k: rec.get(k) for k in ("schema", "log", "head", "length", "published_at", "key_id")},
                    sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
try:
    key.public_key().verify(base64.b64decode(rec.get("signature", "")), signed)
    sig_ok = True
except Exception:
    sig_ok = False
check("the signature verifies with the public key over the signed fields", sig_ok, rec)
blob = out + err + json.dumps(published)
check("the private key appears in no form in the anchor or the command's output",
      not any(s in blob for s in SECRETS))

ANCH = os.path.join(tmp, "anchors.json")
rc, out, err = mio(DB, "audit", "publish", DB, "--out", ANCH, env=KEYED)
check("--out writes the anchors file", rc == 0 and os.path.isfile(ANCH), out + err)
rc, out, err = mio(DB, "audit", "publish", DB, "--out", ANCH, env=KEYED)
held = json.load(open(ANCH, encoding="utf-8"))
check("a second publish ADDS to the file, keeping the anchor already there", len(held.get(LOG, [])) == 2, held)
rc, out, err = mio(DB, "audit", "verify-anchors", DB, "--anchors", ANCH, "--verify-sig", PUB)
check("verify --verify-sig with only the public key passes (no MOHIO_AUDIT_ANCHOR_KEY in its env)",
      rc == 0 and "2/2 anchors hold" in out, out + err)
check("...and says the signatures were checked, by key", re.search(r"signature.*key [0-9a-f]{16}", out) is not None, out)

print("\n-- truncation, replacement and tampering are caught through a signed anchor --")
TDB = os.path.join(tmp, "trunc.db")
shutil.copy(DB, TDB)
order = chain_order(TDB, LOG)
con = sqlite3.connect(TDB)
con.executemany(f"delete from {LOG} where entry_hash=?", [(h,) for h in order[L - 2:]])
con.commit()
con.close()
one = os.path.join(tmp, "one.json")
json.dump({LOG: [rec]}, open(one, "w"))
rc, out, err = mio(TDB, "audit", "verify-anchors", TDB, "--anchors", one, "--verify-sig", PUB)
check(f"truncated to L-2 = {L - 2}: exits 1", rc == 1, out + err)
check(f"...naming TRUNCATION at the anchored length {L}", f"TRUNCATION at length {L}" in out, out)
check("...and not as a signature problem (the signature is good)", "MALFORMED-SIG" not in out, out)

RDB = os.path.join(tmp, "replaced.db")
build(RDB, 5)
rc, out, err = mio(RDB, "audit", "verify-anchors", RDB, "--anchors", one, "--verify-sig", PUB)
check(f"a different chain of the same length {L}: exits 1 naming REPOINT",
      rc == 1 and f"REPOINT at length {L}" in out, out + err)

MDB = os.path.join(tmp, "tampered.db")
shutil.copy(DB, MDB)
con = sqlite3.connect(MDB)
mid = chain_order(MDB, LOG)[L // 2]
con.execute(f"update {LOG} set detail = detail || ' ' where entry_hash = ?", (mid,))
con.commit()
con.close()
rc, out, err = mio(MDB, "audit", "verify-anchors", MDB, "--anchors", one, "--verify-sig", PUB)
check("a middle record altered: exits 1, the chain itself fails", rc == 1 and "chain integrity failed" in out, out + err)

print("\n-- an anchor whose signature does not hold is MALFORMED-SIG, never a pass --")


def variant(name, mutate, log_key=LOG):
    r = json.loads(json.dumps(rec))
    mutate(r)
    path = os.path.join(tmp, name + ".json")
    json.dump({log_key: [r]}, open(path, "w"))
    return path


def flip(r):
    raw = bytearray(base64.b64decode(r["signature"]))
    raw[7] ^= 0x01
    r["signature"] = base64.b64encode(bytes(raw)).decode()


cases = [
    ("flipped", flip, "a signature with one byte flipped", "does not match its contents"),
    ("unsigned", lambda r: r.pop("signature"), "an anchor with no signature", "no signature"),
    ("relengthed", lambda r: r.update(length=L - 2), "a signed anchor whose length was edited", "does not match"),
    ("reheaded", lambda r: r.update(head="a" * 64), "a signed anchor whose head was edited", "does not match"),
    ("oldschema", lambda r: r.update(schema="mohio.audit.anchor/0"), "an unknown schema version", "schema"),
]
for name, mut, what, why in cases:
    rc, out, err = mio(DB, "audit", "verify-anchors", DB, "--anchors", variant(name, mut), "--verify-sig", PUB)
    check(f"{what}: exits 1 with MALFORMED-SIG ({why})", rc == 1 and "MALFORMED-SIG" in out and why in out, out + err)
    check(f"...and is not counted as holding", "anchors hold" not in out, out)
rc, out, err = mio(DB, "audit", "verify-anchors", DB, "--anchors", one, "--verify-sig", OTHER_PUB)
check("verified with a different public key: MALFORMED-SIG naming both key ids",
      rc == 1 and "MALFORMED-SIG" in out and rec["key_id"] in out, out + err)
# An anchor signed for one log, filed under another log of the same database.
two = os.path.join(tmp, "misfiled.json")
json.dump({LOG: [rec], "fraud_audit_log": [rec]}, open(two, "w"))
rc, out, err = mio(DB, "audit", "verify-anchors", DB, "--anchors", two, "--verify-sig", PUB)
check("an anchor signed for one log and filed under another: MALFORMED-SIG", rc == 1 and "signed for the log" in out, out + err)
# Without --verify-sig, the head comparison alone is what it always was: the flipped signature passes.
rc, out, err = mio(DB, "audit", "verify-anchors", DB, "--anchors", variant("flipped2", flip))
check("WITHOUT --verify-sig nothing checks signatures, and the disclaimer still says so",
      rc == 0 and "this check does not verify signatures" in out, out + err)

print("\n-- the store: delivery is recorded only from its own success, and receives no secret --")
received = []


class Store(http.server.BaseHTTPRequestHandler):
    status = 201

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        received.append((dict(self.headers), body.decode("utf-8", "replace")))
        self.send_response(Store.status)
        self.end_headers()

    def log_message(self, *a):
        pass


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Store)
threading.Thread(target=srv.serve_forever, daemon=True).start()
SINK = f"http://127.0.0.1:{srv.server_address[1]}/anchors"
env = dict(KEYED, MOHIO_AUDIT_ANCHOR_SINK=SINK, MOHIO_AUDIT_ANCHOR_SINK_TOKEN="tenant-token-1")
rc, out, err = mio(DB, "audit", "publish", DB, env=env)
check("with MOHIO_AUDIT_ANCHOR_SINK set, publish POSTs and exits 0 on the store's 201",
      rc == 0 and len(received) == 1 and "delivered 1 anchor(s)" in err, out + err)
hdrs, body = received[-1] if received else ({}, "")
sent = json.loads(body or "{}").get("anchors") or [{}]
check("the store received the same signed anchor that was printed", sent[0] == json.loads(out)[LOG][0], (sent, out))
check("the store received the tenant token as a bearer token", hdrs.get("Authorization") == "Bearer tenant-token-1", hdrs)
check("the store received no form of the private key, in the body or a header",
      not any(s in body or s in json.dumps(hdrs) for s in SECRETS))
Store.status = 503
rc, out, err = mio(DB, "audit", "publish", DB, env=env)
check("a store answering 503: exits 1 saying the anchors were NOT delivered",
      rc == 1 and "NOT delivered" in err and "503" in err, out + err)
check("...and claims no delivery", "delivered 1 anchor" not in err, err)
Store.status = 201
rc, out, err = mio(DB, "audit", "publish", DB, env=dict(KEYED, MOHIO_AUDIT_ANCHOR_SINK="http://anchors.example.com/in"))
check("a plain http:// store on another host is refused before anything is sent",
      rc == 3 and "https://" in err and len(received) == 2, out + err)
srv.shutdown()

print("\n-- what publish refuses --")
rc, out, err = mio(DB, "audit", "publish", DB)
check("no MOHIO_AUDIT_ANCHOR_KEY: exits 3 saying an unsigned anchor proves nothing, and how to make a key",
      rc == 3 and "MOHIO_AUDIT_ANCHOR_KEY is not set" in err and "openssl genpkey" in err, out + err)
rc, out, err = mio(DB, "audit", "publish", DB, env={"MOHIO_AUDIT_ANCHOR_KEY": "not-a-key-zzzz"})
check("a value that is not a key: exits 3 and does not repeat the value",
      rc == 3 and "not an Ed25519 private key" in err and "not-a-key-zzzz" not in out + err, out + err)
rc, out, err = mio(DB, "audit", "publish", DB, env={"MOHIO_AUDIT_ANCHOR_KEY": KEY_SEED.hex()})
check("the 32-byte seed as hex signs the same key id as the PEM",
      rc == 0 and json.loads(out)[LOG][0]["key_id"] == rec["key_id"], out + err)
rc, out, err = mio(MDB, "audit", "publish", MDB, env=KEYED)
check("a broken chain is not signed: exits 1 naming the log, and prints no anchor for it",
      rc == 1 and LOG in err and "broken chain must not be signed" in err and '"head"' not in out, out + err)
rc, out, err = mio(DB, "audit", "verify-anchors", DB, "--anchors", one, "--verify-sig", os.path.join(tmp, "nokey.pub"))
check("--verify-sig naming something that is not a public key: exits 3", rc == 3, out + err)
priv_file = os.path.join(tmp, "anchor.key")
open(priv_file, "w").write(KEY_PEM)
rc, out, err = mio(DB, "audit", "verify-anchors", DB, "--anchors", one, "--verify-sig", priv_file)
check("--verify-sig given the PRIVATE key: exits 3 saying only the public half is needed",
      rc == 3 and "PRIVATE key" in err, out + err)

print("\n-- mio serve publishes on its timer, and refuses a timer that could never publish --")
SDB = os.path.join(tmp, "serve.db")
build(SDB, 2)
page = os.path.join(tmp, "site")
os.makedirs(page)
open(os.path.join(page, "index.mho"), "w", encoding="utf-8", newline="\n").write('give back [200] "ok"\n')
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
OUT = os.path.join(tmp, "timer-anchors.json")
log_path = os.path.join(tmp, "serve.log")
senv = dict(BASE_ENV, DATABASE_URL=SDB, MOHIO_AUDIT_ANCHOR_KEY=KEY_PEM, MOHIO_AUDIT_ANCHOR_INTERVAL="0.02",
            MOHIO_AUDIT_ANCHOR_OUT=OUT)
with open(log_path, "w", encoding="utf-8") as lf:
    proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "mio.py"), "serve", page, "--port", str(port),
                             "--host", "127.0.0.1"], cwd=tmp, env=senv, stdout=lf, stderr=subprocess.STDOUT)
    try:
        deadline = time.time() + 240
        n = 0
        while time.time() < deadline:
            time.sleep(1)
            try:
                n = len(json.load(open(OUT, encoding="utf-8")).get(LOG, []))
            except (OSError, ValueError):
                n = 0
            if n >= 2 or proc.poll() is not None:
                break
    finally:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True) if os.name == "nt" \
            else proc.kill()
        proc.wait(timeout=60)
slog = open(log_path, encoding="utf-8", errors="replace").read()
check("the timer published at least twice while the server ran", n >= 2, slog[-1500:])
check("the server said the timer is on, naming the interval and the key id",
      "audit anchors: signed every 0.02 minute(s)" in slog and rec["key_id"] in slog, slog[-1500:])
rc, out, err = mio(SDB, "audit", "verify-anchors", SDB, "--anchors", OUT, "--verify-sig", PUB)
check("every anchor the timer kept verifies with the public key", rc == 0 and "anchors hold" in out, out + err)
check("the private key is not in the server's output", not any(s in slog for s in SECRETS))
for label, extra, want in [
        ("no key", {"MOHIO_AUDIT_ANCHOR_KEY": ""}, "MOHIO_AUDIT_ANCHOR_KEY is not set"),
        ("nowhere to keep them", {"MOHIO_AUDIT_ANCHOR_OUT": ""}, "nowhere to keep the anchors"),
        ("an interval that is not a number", {"MOHIO_AUDIT_ANCHOR_INTERVAL": "soon"}, "minutes between"),
        ("an in-memory database", {"DATABASE_URL": ":memory:"}, "in-memory database")]:
    e = dict(senv)
    e.update(extra)
    r = subprocess.run([sys.executable, os.path.join(ROOT, "mio.py"), "serve", page, "--port", str(port),
                        "--host", "127.0.0.1"], cwd=tmp, env=e, capture_output=True, text=True, timeout=300)
    text = (r.stdout or "") + (r.stderr or "")
    check(f"timer with {label}: the server refuses to start, saying why", r.returncode != 0 and want in text
          and "Listening on" not in text, text[-600:])

shutil.rmtree(tmp, ignore_errors=True)
print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
