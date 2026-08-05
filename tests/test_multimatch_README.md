# Multi-Match Test

Verifies that `retrieve` and `update` honor ALL `match` clauses, not just the first.

## The bug this catches

Before the fix, `_exec_RetrieveBlock` (and the update path) did:
```python
match = next((b for b in node.body if isinstance(b, MatchClause)), None)
```
— grabbing only the FIRST match clause. A second `match` was silently ignored,
so `match command + match room` only filtered on `command`, returning the first
row regardless of room.

## Test sequence

Run each command in order and check the result:

| Step | Command | Expected (PASS) | Wrong (FAIL — first-match-only bug) |
|------|---------|-----------------|--------------------------------------|
| 1 | `seed` | "Seeded two rows..." | — |
| 2 | `retrieve` | "You are in the cellar." | "You are in the kitchen." (returns first row) |
| 3 | `update` | "Updated look+cellar..." | — |
| 4 | `verify_kitchen` | "kitchen use_count = 0" | "kitchen use_count = 99" (updated wrong row) |
| 5 | `verify_cellar` | "cellar use_count = 99" | "cellar use_count = 0" (update missed) |

## Pass criteria

- Step 2 returns the **cellar** response (second match clause honored)
- Step 4 shows kitchen **still 0** (update didn't hit the wrong row)
- Step 5 shows cellar **= 99** (update hit the right row)

If all three hold, multi-match works on both `retrieve` and `update`.

## What each step exercises

- **seed** — multi-field `save` (4 fields), `remove ... where`
- **retrieve** — two-field `match` on retrieve
- **update** — two-field `match` on update (the line 2099 code path)
- **verify_*** — confirms the update was surgical, not broad

## Run

```
mio serve test_multimatch.mho --port 8080 --ai
# POST / with {"command":"seed"}, then "retrieve", "update", etc.
```
