# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""The session-store provider seam itself (2026-08-05 durable-session-store build),
mirroring the audit-sink / key-provider seam pattern exactly.

Proves the SEAM, not any particular backend:
  (a) with nothing registered and no MOHIO_SESSION_STORE set, MohioServer builds the
      in-memory default -- unchanged from before this seam existed.
  (b) register_session_store_provider() actually takes effect: MohioServer picks up
      whatever the registered callable returns, not the default.
  (c) an explicitly registered provider outranks MOHIO_SESSION_STORE=postgres (same
      explicit-instruction-outranks-env-default precedent as the 2026-08-04 AI
      model-resolution ruling) -- proven without a real Postgres connection, since the
      provider should win before DATABASE_URL is even consulted.
  (d) unregister_session_store_provider() restores the in-memory default.

Run: `python tests/test_session_store_provider_seam.py`.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mohio_data
os.environ.setdefault('DATABASE_URL', ':memory:')

from lark import Lark
from mohio_transformer_ast import transform
from mohio_interpreter import MohioInterpreter, _InMemorySessionStore
from mohio_server import MohioServer

_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_g = '\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//'))
_P = Lark(_g, parser='earley', ambiguity='resolve', propagate_positions=True)
_SRC = 'show "hi"\n'
_PROG = transform(_P.parse(_SRC), _SRC)

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail: print(f"          {detail}")
    _p += bool(cond); _f += (not cond)

def make_server():
    return MohioServer(_PROG, MohioInterpreter())

# ── (a) default: nothing registered, no env var -> in-memory ───────────────────────
os.environ.pop('MOHIO_SESSION_STORE', None)
MohioInterpreter.unregister_session_store_provider()
srv_default = make_server()
check("(a) default store is the in-memory implementation",
      isinstance(srv_default._session_store, _InMemorySessionStore),
      type(srv_default._session_store))
check("(a) default store starts empty, matching today's self.sessions = {} behavior",
      srv_default._session_store.count() == 0, srv_default._session_store.count())

# ── (b) a registered provider actually takes effect ─────────────────────────────────
class _MarkerStore(_InMemorySessionStore):
    """A distinguishable subclass -- proves MohioServer picked up THIS object, not
    coincidentally an equivalent-looking default."""
    pass

_registered = {'called': 0}
def _provider():
    _registered['called'] += 1
    return _MarkerStore()

MohioInterpreter.register_session_store_provider(_provider)
srv_provided = make_server()
check("(b) a registered provider's store is used instead of the default",
      isinstance(srv_provided._session_store, _MarkerStore),
      type(srv_provided._session_store))
check("(b) the provider callable was actually invoked exactly once",
      _registered['called'] == 1, _registered['called'])

# ── (c) explicit provider outranks MOHIO_SESSION_STORE=postgres ────────────────────
# No real Postgres reachable here -- if the provider did NOT win, _build_session_store
# would try to construct _PostgresSessionStore and this would raise/hang, not quietly
# fall through. The provider winning is what keeps this test fast and connection-free.
os.environ['MOHIO_SESSION_STORE'] = 'postgres'
try:
    srv_precedence = make_server()
    check("(c) a registered provider wins over MOHIO_SESSION_STORE=postgres",
          isinstance(srv_precedence._session_store, _MarkerStore),
          type(srv_precedence._session_store))
except Exception as e:
    check("(c) a registered provider wins over MOHIO_SESSION_STORE=postgres", False,
          f"raised instead of using the provider: {type(e).__name__}: {e}")
finally:
    os.environ.pop('MOHIO_SESSION_STORE', None)

# ── (d) unregistering restores the in-memory default ───────────────────────────────
MohioInterpreter.unregister_session_store_provider()
srv_restored = make_server()
check("(d) unregister_session_store_provider restores the in-memory default",
      isinstance(srv_restored._session_store, _InMemorySessionStore) and
      not isinstance(srv_restored._session_store, _MarkerStore),
      type(srv_restored._session_store))

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
