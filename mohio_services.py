# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE.md and LICENSE-SCOPE.md.
"""The Mohio service namespace -- the ONE canonical list.

WHY THIS FILE EXISTS
--------------------
This list used to live in four places that had quietly drifted apart:

  1. mohio.lark          NAME exclusion      (30 roots)
  2. mohio.lark          MIO_SERVICE_ROOT    (30 roots)
  3. mohio_transformer   MOHIO_RESERVED_EXACT(28 mio* roots)
  4. mohio_reachability  _NOT_WIRED_SERVICES (7 roots)

They disagreed in BOTH directions. #3 reserved three names that were not services
anywhere (mioenv, miolearn, miosms) -- so a user could not name a variable `miosms`
for a service the grammar had never heard of. And #3 was MISSING five roots that are
real (mioprint, miopublish, miopush, mioresponse, miovalidate) -- so those names were
reserved by the grammar with a generic message instead of a directional one.

That is the "patched only what bit us" disease. A name got added wherever the bug
surfaced, and nowhere else. The cure is one list plus a test that fails loud when the
grammar and this file disagree (tests/test_service_name_reservation.py).

RULE: add a service root HERE. The guard test will tell you the grammar is stale.

NOT the whole `mio` prefix. User names may legitimately start with `mio` -- the gate
has a langmap pack called `miogscreen`. Only the real roots are reserved.
"""

# Services that are BUILT or DECLARED in the grammar today.
SERVICE_ROOTS_ACTIVE = frozenset({
    "mioai", "mioapp", "mioauth", "miocache", "miochain", "mioconnect",
    "miocookie", "miodata", "miofile", "miograph", "miohttp", "mioimage",
    "mioknow", "miolog", "miomail", "miomap", "miopdf", "mioprint",
    "miopublish", "miopush", "mioresponse", "mioschedule", "miosearch",
    "miosecurity", "miostream", "miosys", "miotest", "miotranslate",
    "miovalidate", "miovault",
})

# PLANNED namespace. No grammar terminals yet, but the name is claimed so that nobody
# builds an app on a variable called `miosms` and has it break the day the service
# lands. Reserving them makes `miosms.send` fail loud BY NAME rather than dying as a
# bare "Syntax error".
SERVICE_ROOTS_PLANNED = frozenset({
    "mioenv", "miolearn", "miosms",
})

# The canonical namespace. Everything downstream imports THIS.
SERVICE_ROOTS = SERVICE_ROOTS_ACTIVE | SERVICE_ROOTS_PLANNED


def alternation(roots=None) -> str:
    """The roots as a regex alternation, LONGEST FIRST.

    Longest-first matters: without it `mio` would shadow `mioconnect`, and
    `miopush` would shadow `miopublish` on a shared prefix. Sorting by length
    descending makes the alternation greedy in the way we need.
    """
    return "|".join(sorted(roots or SERVICE_ROOTS, key=lambda s: (-len(s), s)))
