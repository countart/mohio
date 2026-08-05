#!/usr/bin/env python3
# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""
Lock tests for response-serialization hardening in mohio_server.py.

Reproduces the live "read leaflet -> Connection error" class and proves the
fixes hold:
  1. A multi-line, unicode (em-dash) string body serializes to valid JSON that
     a client's response.json() can parse (the leaflet description).
  2. A dict body carrying a non-JSON-native value (datetime, bytes) serializes
     instead of raising (default=str) — no 500/Connection-error.
  3. A None give-back becomes {"message": ""} — never an empty body the client
     can't parse.
  4. The exact leaflet description from the seed round-trips byte-for-byte.

These test the module-level helpers (importable without starlette) that the
SafeJSONResponse and dispatch paths delegate to.
"""
import os, sys, json, datetime
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from mohio_server import _safe_json_bytes, _response_payload

PASS = FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  PASS  {name}")
    else:    FAIL += 1; print(f"  FAIL  {name}")


def client_parse(body):
    """Emulate a browser doing `await response.json()` on the serialized body."""
    return json.loads(_safe_json_bytes(_response_payload(body)))


# 1. multi-line + em-dash string body (the leaflet shape)
leaflet = "WELCOME TO ZORK!\n\nlow cunning. No computer should be without one!\n\n(Built with MOHIO \u2014 mohio.io)"
parsed = client_parse(leaflet)
check("multi-line + em-dash string round-trips for the client",
      parsed["message"] == leaflet)

# 2. dict body with non-serializable nested values must NOT crash
try:
    parsed = client_parse({"message": "ok", "ts": datetime.datetime(2026, 6, 13, 22, 0, 0),
                           "blob": b"\x00\x01"})
    check("dict with datetime + bytes serializes (default=str, no crash)",
          parsed["message"] == "ok" and "2026" in parsed["ts"])
except Exception as e:
    check(f"dict with datetime + bytes serializes (raised {type(e).__name__})", False)

# 3. None give-back -> {"message": ""}, never an empty body
parsed = client_parse(None)
check("None body -> non-blank marker with _empty flag (never blank)",
      parsed.get("message", "").strip() != "" and parsed.get("_empty") is True)

# 3b. empty-string body and dict-with-blank-message also get the marker
check("empty-string body -> non-blank marker",
      client_parse("").get("_empty") is True
      and client_parse("")["message"].strip() != "")
check("dict with blank message -> marker, other keys preserved",
      (lambda p: p.get("_empty") is True and p["message"].strip() != "" and p.get("room") == "cellar")
      (json.loads(_safe_json_bytes(_response_payload({"message": "", "room": "cellar"})).decode())))
check("dict with real content passes through untouched",
      json.loads(_safe_json_bytes(_response_payload({"message": "you see a leaflet"})).decode())
      == {"message": "you see a leaflet"})

# 4. the EXACT seed leaflet description round-trips byte-for-byte
seed_path = next((p for p in (
    os.path.join(ROOT, "tests", "seed_zork.json"),
    "tests/seed_zork.json") if os.path.exists(p)), None)
if seed_path:
    seed = json.load(open(seed_path, encoding='utf-8'))
    desc = next((i["description"] for i in seed.get("items", []) if i.get("id") == "leaflet"), None)
    if desc is not None:
        check("exact seed leaflet description round-trips byte-for-byte",
              client_parse(desc)["message"] == desc)
    else:
        check("seed leaflet description present", False)
else:
    print("  SKIP  seed file not found (leaflet byte-for-byte check)")

# 5. no raw newline survives in the serialized bytes (would break a hand-built client)
raw = _safe_json_bytes(_response_payload(leaflet))
check("serialized bytes contain no raw newline (all escaped as \\n)",
      b"\n" not in raw.replace(b"\\n", b""))

print(f"\nRESULTS: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
