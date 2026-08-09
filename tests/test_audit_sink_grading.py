# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Sink grading: a store never grades itself, and it fails closed.

THE WORST CASE THIS CLOSES
Silent non-durability: audit writes appear to succeed and land nowhere. It is the worst failure in
the audit system precisely because it is the only one that is invisible while it is happening --
the guarantee reads as true for exactly as long as nobody checks.

It was reachable two ways, both of which assumed adequacy:

    getattr(sink, '_mohio_durable', True)          # unset -> assume it persists
    getattr(sink, '_mohio_grade', 'durable')       # unset -> assume it meets the grade

So a sink nobody had classified satisfied the requirement by default, and an in-memory store
accepted compliance records and lost them without a word.

Now the sink is CLASSIFIED by inspection rather than asked. Grades above `durable` cannot be
reached by inspection at all -- append-only is a property of role grants and WORM of storage
configuration, neither visible from a connection object -- so those must be asserted by a provider
that verified them, through a channel separate from anything a sink could set for itself.

And structural incapacity is treated as absence: a bound sink that can never hold a record is not
a transient failure a retry could recover, so under an activated framework it refuses rather than
proceeding.
"""
import os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DATABASE_URL', ':memory:')

from mohio_interpreter import MohioInterpreter, DbRuntime, Context
from mohio_audit_grades import classify_sink, satisfies

_p = _f = 0
def check(label, cond, detail=""):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond and detail:
        print(f"          {detail}")
    _p += bool(cond); _f += (not cond)


# ── classification is by inspection, and fails closed ─────────────────────────────────
_g_mem, _d_mem, _why_mem = classify_sink(DbRuntime(':memory:'))
check("an in-memory store grades `none`, not `durable`", _g_mem == 'none', f"got {_g_mem}")
check("an in-memory store is not durable", _d_mem is False)
check("the reason says why", 'memory' in _why_mem.lower(), _why_mem)

_g_disk, _d_disk, _ = classify_sink(DbRuntime(tempfile.mktemp(suffix='.db')))
check("a sqlite file on disk grades `durable`", _g_disk == 'durable', f"got {_g_disk}")
check("a sqlite file on disk is durable", _d_disk is True)

_g_none, _d_none, _ = classify_sink(None)
check("no sink grades `none`", _g_none == 'none' and _d_none is False)


class _Unknown:
    pass


_g_unk, _d_unk, _why_unk = classify_sink(_Unknown())
check("an unrecognisable binding grades `none` (fails closed)",
      _g_unk == 'none' and _d_unk is False, f"{_g_unk} / {_why_unk}")


# ── a store cannot promote itself ─────────────────────────────────────────────────────
class _SelfPromoting(DbRuntime):
    def __init__(self):
        super().__init__(':memory:')
        self._mohio_grade = 'worm'          # what a store might claim about itself
        self._mohio_durable = True


_g_lie, _d_lie, _ = classify_sink(_SelfPromoting())
check("a store claiming `worm` about itself is not believed", _g_lie != 'worm', f"got {_g_lie}")
check("a store claiming durability about an in-memory backend is not believed",
      _d_lie is False)


# ── grades above durable come only through the verified channel ───────────────────────
class _ProviderVerified(DbRuntime):
    def __init__(self):
        super().__init__(tempfile.mktemp(suffix='.db'))
        self._mohio_grade_verified = 'append_only'      # a provider that checked the grants


_g_v, _d_v, _ = classify_sink(_ProviderVerified())
check("a provider-verified grade is honoured", _g_v == 'append_only', f"got {_g_v}")
check("the verified grade satisfies a framework requiring it",
      satisfies(_g_v, 'append_only'))


# ── structural incapacity refuses under a framework ───────────────────────────────────
def _write(sink, frameworks):
    it = MohioInterpreter(); it._db = sink
    class _C(Context):
        def get_connection(self, _x): return sink
    ctx = _C(); ctx._sector_compliance = frameworks
    try:
        it._audit_event('sg_audit_log', {'event': 'e', 'agent': 'a'}, ctx)
        return 'proceeded'
    except Exception as e:
        return 'refused' if 'durable' in str(e).lower() else f'other: {str(e)[:40]}'


check("with NO framework an ephemeral sink still proceeds (nothing is claimed)",
      _write(DbRuntime(':memory:'), None) == 'proceeded')
check("under a framework an EPHEMERAL sink is refused, not written to",
      _write(DbRuntime(':memory:'), ['hipaa']) == 'refused')
check("under a framework NO sink is refused",
      _write(None, ['hipaa']) == 'refused')
check("under a framework a DURABLE sink proceeds (below-grade is a degraded incident, "
      "not a lost record)",
      _write(DbRuntime(tempfile.mktemp(suffix='.db')), ['hipaa']) == 'proceeded')

# ── the fail-open defaults are gone from the source ───────────────────────────────────
_src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         'mohio_interpreter.py'), encoding='utf-8').read()
check("no code assumes an unclassified sink is durable",
      "_mohio_durable', True" not in _src)
check("no code assumes an unclassified sink meets the grade",
      "_mohio_grade', 'durable'" not in _src)

print(f"\nRESULTS: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
