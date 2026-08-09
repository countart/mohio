# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""Guard: a service name is a RESERVED WORD, never a variable. (Backlog item 2)

History. `NAME` matched the service roots, so a service name could be captured as an
identifier and the real rule never got a chance:

    miotest "suite"        -> Assignment      (a VARIABLE named miotest)
    miopdf "y"             -> Assignment      (a VARIABLE named miopdf)

`miotest_block` existed in the grammar the whole time -- and was referenced by NOTHING.
An orphan rule. It could never win the ambiguity, so `miotest` quietly became a variable
and the block silently did nothing.

Two fixes, both guarded here:
  1. The 30 service roots are reserved in NAME, so they cannot be declared as variables.
  2. Reserving them must NOT break the dotted call form. `dotted_name_with_dot` filtered
     children on `c.type == 'NAME'`, so once the head was reserved it was SILENTLY DROPPED
     (`miosearch.index` -> parts=['index'], service name gone) and the not-built check had
     nothing to complain about. The head must survive.

And user names that merely START with `mio` stay legal -- `mio` is not a reserved prefix,
only the 30 real roots are. The gate has a langmap pack called `miogscreen`.
"""
import os, sys
os.environ['DATABASE_URL'] = ':memory:'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mohio_data

from lark import Lark
from mohio_transformer_ast import transform

_raw = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_P = Lark('\n'.join(l for l in _raw.splitlines() if not l.strip().startswith('//')),
          parser='earley', ambiguity='resolve', propagate_positions=True)

passed = failed = 0

def check(label, ok):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [ok]   {label}")
    else:
        failed += 1
        print(f"  [FAIL] {label}")

def stmts(src):
    prog = transform(_P.parse(src), src)
    return getattr(prog, 'statements', []) or []

def kinds(src):
    return [type(s).__name__ for s in stmts(src)]

def is_loud(src):
    try:
        stmts(src)
        return False
    except Exception:
        return True

print("service-name reservation (backlog item 2)")

# 1. miotest is a BLOCK, not a variable.
MT = 'miotest "suite"\n    show "hi"\nmiotest: done\n'
check("miotest parses as MiotestDecl, not Assignment", kinds(MT) == ['MiotestDecl'])
check("miotest keeps its suite name",  getattr(stmts(MT)[0], 'name', '') == 'suite')
check("miotest keeps its body",        len(getattr(stmts(MT)[0], 'body', [])) == 1)

MTU = 'miotest.unit "case"\n    show "hi"\nmiotest: done\n'
check("miotest.unit parses as MiotestDecl", kinds(MTU) == ['MiotestDecl'])

# 2. A bare service root can never be declared as a variable.
for svc in ('miotest', 'miopdf', 'miosearch', 'mioimage', 'miomap', 'miovault'):
    check(f"{svc} cannot be declared as a variable", is_loud(f'{svc} "x"\n'))

# 3. Reserving the root must not kill the dotted call. The head has to SURVIVE the
#    transformer, or the not-built check sees no service name at all.
for call, root in (('miosearch.index "d"', 'miosearch'),
                   ('mioai.generate "p"',  'mioai'),
                   ('miopdf.from "<h1>x</h1>"', 'miopdf')):
    st = stmts(call + '\n')[0]
    got = str(getattr(st, 'service', ''))
    check(f"{call} keeps its service head ({root})", got == root)

# 4. A wired service still routes to its own dedicated node, not the generic call.
check("miocookie.get still routes to MioCookieGet",
      kinds('miocookie.get "k"\n') == ['MioCookieGet'])

# 5. `mio` is NOT a reserved prefix. Only the roots are. User names survive.
check("miogscreen (user pack name) is still a legal identifier",
      not is_loud('load pack miogscreen\n'))

# 6. SINGLE SOURCE OF TRUTH.
# The service list used to live in four places that had drifted apart in BOTH
# directions -- three names reserved that were not services, five real services not
# reserved. Nobody noticed, because nothing compared them. This compares them. If the
# grammar and mohio_services.py ever disagree again, this fails LOUD and names the gap.
import re as _re
from mohio_services import SERVICE_ROOTS, SERVICE_ROOTS_PLANNED, SERVICE_ROOTS_ACTIVE
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_g = mohio_data.GRAMMAR_PATH.read_text(encoding='utf-8')
_name_ex = set(_re.search(r'NAME: /\(\?!__USERVAR__\)\(\?!\(\?:([^)]*)\)', _g).group(1).split('|'))
_svc_root = set(_re.search(r'MIO_SERVICE_ROOT\.1: /\(\?:([^)]*)\)', _g).group(1).split('|'))

check(f"grammar NAME-exclusion == canonical list  (drift: "
      f"{sorted(_name_ex ^ set(SERVICE_ROOTS)) or 'none'})",
      _name_ex == set(SERVICE_ROOTS))

check(f"grammar MIO_SERVICE_ROOT == canonical list  (drift: "
      f"{sorted(_svc_root ^ set(SERVICE_ROOTS)) or 'none'})",
      _svc_root == set(SERVICE_ROOTS))

from mohio_transformer import MOHIO_RESERVED_EXACT
check(f"transformer reserves every service root  (missing: "
      f"{sorted(set(SERVICE_ROOTS) - MOHIO_RESERVED_EXACT) or 'none'})",
      set(SERVICE_ROOTS) <= MOHIO_RESERVED_EXACT)

_phantom = {n for n in MOHIO_RESERVED_EXACT if n.startswith('mio')} - set(SERVICE_ROOTS)
check(f"transformer reserves no phantom mio* name  (phantoms: {sorted(_phantom) or 'none'})",
      not _phantom)

check("active and planned roots do not overlap",
      not (SERVICE_ROOTS_ACTIVE & SERVICE_ROOTS_PLANNED))

# 7. A PLANNED service is reserved, and says so instead of dying as a syntax error.
check("bare planned service (miosms) fails loud", is_loud('miosms "hi"\n'))
# Reserving a root removes it from NAME, so the dotted form can ONLY still parse if the
# root is also reachable as MIO_SERVICE_ROOT. That is the regression this guards: reserve
# the name and you can silently break every dotted call that uses it. It must still parse
# and keep its head -- the not-built ERROR is a separate door (test_service_failloud).
check("planned dotted op (miosms.send) still parses after reservation",
      not is_loud('miosms.send to "+15551234" body "Yo"\n'))
check("miosms.send keeps its service head",
      str(getattr(stmts('miosms.send to "+15551234" body "Yo"\n')[0], 'service', '')) == 'miosms')

print(f"\nRESULTS: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
