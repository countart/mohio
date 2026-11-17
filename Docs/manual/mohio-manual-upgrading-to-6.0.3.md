<!-- Copyright 2026 Particular LLC. MOHIO(TM) is a trademark of Particular LLC. -->
<!-- Licensed under the Mohio Business Source License 1.1 (BSL). See LICENSE and LICENSE-SCOPE.md. -->
# Upgrading to 6.0.3

**Most 6.0.2 programs run unchanged on 6.0.3.** One read behaviour changed: the three single-row read verbs now
refuse a match that finds more than one row, instead of binding whichever row the engine returned first. Every
item below was established by running the same program on 6.0.2 and on 6.0.3.

## Behaviour that changed

1. **`retrieve`, `grab`, and `get` refuse a match that finds more than one row.**
   - Before: a non-unique match on a single-row read bound whichever row the engine happened to list first, with
     no error. One person with two memberships could read as approved, then as denied after an unrelated update,
     with nothing in between to say which row was read.
   - After: the read fails loud, naming the verb, the table, and the field it matched on. It is a DEVELOPER
     error, a program mistake and not a governed refusal, so it never reaches `on.failure` and is never a 403. A
     match that finds exactly one row still binds it, and a match that finds none still binds empty.
   - The message names the fix (the verb in it is whichever single-row verb you wrote, so `grab` and `get` refuse
     the same way, each naming itself):

     ```
     retrieve `player` found more than one row in db.players matching `team`. retrieve reads exactly one record, so Mohio will not pick one for you. Match a key so only one row can match (`match id to ...`, or a field declared `unique`). To ask whether any such row exists, use `check exists player in db.players` with the same match. To read all of them, use `find player in db.players`.
     ```

   - **The cardinality triad.** The change lines each read verb up with one cardinality question, and reaching for
     the right one is how you stay clear of the refusal:
     - **One entity, use `retrieve` (or its siblings `grab` / `get`).** The match identifies a single record, by
       `id` or another `unique` field.
     - **Whether any of a group exist, use `check exists`.** A yes-or-no gate that pulls no rows in, branching on
       `when empty` / `otherwise`.
     - **The group itself, use `find`.** Every matching row, as a collection you count, page, iterate, or join.

     ```mohio
     connect db from env.DATABASE_URL
     shape Player
         players as table
             team as text
             name as text
     shape: done

     // one entity: match a key that can only hit one row
     retrieve player from db.players
         match id to 1
     retrieve: done
     show player.name

     // whether any exist: a condition, no rows pulled
     check exists someone in db.players
         match team to "red"
         when empty
             show "no one on red"
         otherwise
             show "red has players"
     check: done

     // the group: a collection
     find roster in db.players
         match team to "red"
     find: done
     show roster.count
     ```

   - Enforced across all five engines: SQLite, Postgres, MySQL and Oracle read two rows and refuse on the second;
     MongoDB does the same by limiting to two rows and then counting. A `retrieve` may still carry a child list
     with `with` (one parent record and its related rows); the one-row rule is about the parent match, not the
     children.

## Worked example: the zork migration

The text adventure in `zork/` ran on the old behaviour and changed in two places when it moved to 6.0.3
(its migration commit `8cebe59`). Both sites matched a session, not a key, so both read one row by the
luck of the engine's order.

**Site 1, a world-clone existence check (`index.mho:64`).** The code read one row only to learn whether
any existed, then cloned a starter world when none did. That is a yes-or-no question, so it became a
`check exists`, which pulls no row in:

```mohio
retrieve mine from db.items
    match session_id to player_session
    when mine is empty
        // clone the starter world into this session
retrieve: done
```

```mohio
check exists mine in db.items
    match session_id to player_session
    when empty
        // clone the starter world into this session
check: done
```

**Site 2, a multi-row candidate read (`index.mho:312`).** The code matched every item in the player's
inventory or current room. That is a group, so it became a `find`:

```mohio
retrieve candidates from db.items
    match session_id to player_session
    match any
        location to "inventory"
        location to current_room
    match any: done
retrieve: done
```

```mohio
find candidates in db.items
    match session_id to player_session
    match any
        location to "inventory"
        location to current_room
    match any: done
find: done
```

Neither site changed what the program does; each names the cardinality it always meant. The headline the
migration proved: `mio check` reported zero errors on the old source at both sites, because whether a
match finds one row or several is a property of the data, not of the text. The refusal came only when the
code RAN against a session that had more than one row. A clean static gate is not proof of a finished
6.0.3 upgrade; only a real run on real data is.

## How to find them

This change refuses when the line RUNS, not at `mio check`, because whether a match finds one row or several is a
property of the data, not of the program text. A clean `mio check` does not clear it. Look instead for every
`retrieve`, `grab` and `get` whose `match` is not on a key (an `id`, or a field you declared `unique`): each one
is a single-row read that will refuse the first time its match finds two rows. Narrow the match to a key, or
switch verb, `check exists` for the yes-or-no and `find` for all of them.
