# Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC.
# Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md.
"""RETIRED IN PLACE (2026-07-18) -- kept for the lesson, no longer executes.

WHAT THIS GUARDED
mohio_server's seed endpoint used to choose a table's primary key like this:

    pk = pk_map.get(table, cols[0])          # <-- the FIRST COLUMN

`exits` is (room_id, direction, dest). A room has MANY exits. So `room_id` became a
PRIMARY KEY, and `INSERT ... ON CONFLICT (room_id) DO UPDATE` overwrote each previous
exit for that room. 229 of 342 exits were destroyed on every seed. Nothing reported a
problem, because ON CONFLICT DO UPDATE *succeeds*. The DB was clean, the app code was
correct, the seed file was correct. The TABLE was lying.

THE LESSON (carry this forward)
Uniqueness cannot be inferred. It is a claim about the world, and no amount of looking at
rows establishes it: room_id repeats, and is supposed to. Either a human states the key
for a table, or that table has NO key. Never guess. A fallback that picks "the first
column" is not a default, it is a silent data-destroyer.

WHY IT NO LONGER RUNS
The seed machinery left the general server on 2026-07-18. Seeding is an app / control-plane
concern, not a hosting-runtime one: the shared runtime that every tenant executes must carry
no app's schema, seed data, or key map. There is consequently no generic seeder in this repo,
and this test has no subject here.

WHOEVER BUILDS SEEDING NEXT -- control-plane DB management, or an app-side seeder -- inherits
the rule above, and should re-create a guard like this one against whatever key map they
introduce: replay the real key decisions against the real seed data and assert, table by
table, that every row sent is a row that lands.

Related: the Zork demo's reseed path (POST /mio/seed?secret=...&reset=true, driven from
mio_admin.html) went away with the endpoint. Zork reseeds via its SQL script until the
control-plane DB management page exists.
"""
import sys

print("  [SKIP] test_seed_schema_safety -- retired in place.")
print("         Seed machinery moved out of the general server; no subject in this repo.")
print("         The uniqueness lesson is preserved in this file's docstring.")
print("\nRESULTS: 0 passed, 0 failed (retired)")
sys.exit(0)
