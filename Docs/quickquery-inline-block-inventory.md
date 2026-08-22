# Inline vs Block form inventory — every query/data verb

**Compiled 2026-08-11**, quoted from `mohio_data/mohio.lark` as it stood that day. Corpus usage
measured across all 35 real `.mho` files (`cookbook/`, `examples/`, `tests/`, `bucket/`, `dirtest/`,
root demo/verify files — excluding `drafts/` and the throwaway `tmp*.mho` scratch files). This is
the source inventory for `T1-QUICKQUERY-PATTERN` in `PRODUCTION-BUILD-PLAN.md` — read that entry
for the ruling context; this file is the raw, verified data underneath it.

## Per-verb detail

### `retrieve` (bare) — **HAS BOTH**
```
retrieve_block:  RETRIEVE NAME (AS NAME)? LOCKED? FROM source_ref retrieve_body* result_handlers closer   (:1492)
retrieve_inline: RETRIEVE NAME (AS NAME)? FROM source_ref WHERE where_is_pair ("," where_is_pair)*         (:1495)
```
**Redundancy flag:** `retrieve_inline` is NOT strictly one-condition — it allows
`("," where_is_pair)*`, an arbitrary-length comma-joined AND-chain on one line. This genuinely
overlaps `retrieve_block`'s own match-clause capability; it does not earn a clean "quick
single-condition" niche the way `grab_inline` does.
**Corpus:** block form — 128 real sites. Inline form — **0 real sites**, defined but dead.

### `retrieve.all` / `retrieve.one` (RETRIEVE_MOD) — **BLOCK ONLY**
```
RETRIEVE_MOD.3: /retrieve\.\w+/                                                                             (:1497)
retrieve_mod_block: RETRIEVE_MOD NAME (AS NAME)? FROM source_ref retrieve_body* result_handlers closer      (:1499)
```
No inline counterpart exists for the dot-modified forms at all — they don't inherit `retrieve_inline`.
**Corpus:** 52 real sites (50 `.all`, 2 `.one`).

### `find` — **BLOCK ONLY**
```
find_block: FIND NAME (BY groupby_expr)? IN source_ref find_body* closer                                    (:1526)
```
No `find_inline` exists anywhere in the grammar.
**Corpus:** 12 real sites.

### `grab` — **HAS BOTH — the model case**
```
grab_block:  GRAB NAME FROM source_ref match_clause? result_handlers closer                                  (:1569)
grab_inline: GRAB NAME FROM source_ref WHERE dotted_name IS value_expr                                        (:1570)
```
**No redundancy risk:** `grab_inline` is genuinely, strictly ONE condition — exactly one
`dotted_name IS value_expr`, no comma, no list, no AND. This is the disciplined shape the two-tier
pattern should be modeled on.
**Corpus:** **0 real sites for either form.** `grab` is entirely unused in the corpus despite being
the cleanest-designed two-tier verb in the grammar.

### `check exists` — **BLOCK ONLY, with a duplicate-spelling wrinkle**
```
check_mioql_block (CHECK_EXISTS alt): CHECK_EXISTS NAME IN source_ref match_clause result_handlers closer    (:1671)
check_exists_bare_block: CHECK NAME IN source_ref (match_clause | where_clause) result_handlers closer        (:1679)
```
Two separate grammar rules express "exists," not an inline/block pair — `check_exists_bare_block`
is a second SPELLING of the same concept (bare `check`, disambiguated by a required match/where),
not a lighter-weight inline form. Both still always require `result_handlers closer`; neither has
a true one-liner.
**Corpus:** 0 real sites in standalone `.mho` files. Exercised via embedded source strings in
`tests/test_check_count_where_filter.py`/`test_check_mioql.py`/`test_cross_dialect_sweep.py`/
`test_mongo_backend.py` (proves the feature works, not that any real program uses it).

### `check count` — **BLOCK ONLY**
```
check_mioql_block (CHECK_COUNT alt): CHECK_COUNT (AS NAME)? IN source_ref (where_clause | match_clause)? result_handlers closer   (:1672)
```
Takes at most one clause (`where_clause` or `match_clause`, either of which can itself be a
multi-field AND — confirmed via `tests/test_check_count_where_filter.py`'s
`where grp is "a" / where name is "one"` stacked case). No inline one-liner.
**Corpus:** 0 real sites in `.mho` files; exercised via the same 4 test files as `check exists`.

### `check unique` — **BLOCK-SHAPED BUT RIGID — the extensibility gap**
```
check_mioql_block (CHECK_UNIQUE alt): CHECK_UNIQUE IN source_ref MATCH_MOD NAME TO value_expr result_handlers closer   (:1673)
```
Grammatically classified as a "block" (requires `result_handlers closer`) but its condition slot is
exactly as rigid as an inline form — one `NAME TO value_expr`, period. Unlike
`retrieve`/`find`/`update`/`remove`/`save or update` (all of which route through `match_clause` and
can therefore express AND via stacked pairs or OR via a `match any` sub-block), `check unique` has
no path to a composite check (e.g. "unique on `(email, tenant_id)` together") — it structurally
cannot grow past one field. This is the clearest real case of "has only a single form, needs a real
block form for extensibility" — feeds `T1-CHECK-UNIQUE-REDESIGN` directly.
**Corpus:** 0 real sites in `.mho` files; exercised in the same 4 test files; also taught in
`Docs/mioql-user-guide.md`.

### `save` — **BLOCK ONLY**
```
save_block: SAVE TO source_ref (AS NAME)? (UNLESS name_list _EXISTS)? save_field* result_handlers closer     (:1624)
```
No inline form. **Corpus:** 71 real sites.

### `save or update` / `upsert` — **BLOCK ONLY, two spellings**
```
save_or_update_block: SAVE OR UPDATE source_ref match_clause save_field* result_handlers closer
                     | UPSERT source_ref match_clause save_field* result_handlers closer                     (:1637-1638)
```
`match_clause` is MANDATORY here (not optional), and always needs `result_handlers closer`. No
inline form for either spelling.
**Corpus:** `upsert` (concise) — 2 real sites. `save or update` (verbose) — **0 real sites**,
defined but dead.

### `save all` — **BLOCK ONLY, and the inline/block distinction doesn't really apply**
```
save_all_block: SAVE ALL? TO source_ref FROM value_expr result_handlers closer                                (:1642)
```
No WHERE/match filter concept at all — it bulk-inserts an entire collection, it doesn't select
rows. The inline-vs-block question is not applicable to this verb the way it is to the filtering
verbs.
**Corpus:** 0 real sites in `.mho` files.

### `update` — **BLOCK ONLY**
```
update_block: UPDATE source_ref update_body* result_handlers closer                                           (:1646)
update_body: match_clause | save_field                                                                        (:1648)
```
No inline form. **Corpus:** 62 real sites.

### `remove` — **BLOCK ONLY** (plus the separate `remove.all` dotted/spaced pair, unrelated to this filtering question)
```
remove_block: REMOVE FROM source_ref remove_condition result_handlers closer                                  (:1653)
remove_condition: match_clause | where_clause | (where_clause AND where_clause)                                (:1655-1657)
```
No inline form. (`remove_all_block`/`remove_all_spaced` at :1661/:1667 are the dotted-vs-spaced
pair from the `T1-SPACED-MISPARSE-GUARDS` work — a spelling question, not an inline/block question;
`remove.all` takes no condition at all.)
**Corpus:** `remove` — 5 real sites. `remove.all` — 9 real sites.

### `modify` — **BLOCK ONLY**
```
modify_block: MODIFY EVERY NAME IN value_expr (WHERE condition)? modify_body* closer                          (:1715)
            | MODIFY ALL IN value_expr modify_body* closer
```
No inline form.
**Corpus:** 2 real sites (both Zork's `modify all in db.items / modify.as backup_items` auto-save
use; the WHERE-filtered `modify every X in Y where ...` shape is exercised in
`tests/test_modify_where.py`'s embedded source, not in a standalone `.mho` file).

## Summary table

| Verb | Block form (rule) | Inline form (rule) | Both/One/N-A | Corpus usage |
|---|---|---|---|---|
| `retrieve` | `retrieve_block` :1492 | `retrieve_inline` :1495 | **BOTH** — but inline overlaps block (not strictly 1-condition) | block: 128; inline: **0** |
| `retrieve.all`/`.one` | `retrieve_mod_block` :1499 | — | **ONE** (block only) | 52 |
| `find` | `find_block` :1526 | — | **ONE** (block only) | 12 |
| `grab` | `grab_block` :1569 | `grab_inline` :1570 | **BOTH** — clean, strictly 1-condition (the model) | both: **0** |
| `check exists` | `check_mioql_block`/`check_exists_bare_block` :1671/:1679 | — | **ONE** (block only, 2 spellings) | 0 in `.mho`; test-only |
| `check count` | `check_mioql_block` :1672 | — | **ONE** (block only) | 0 in `.mho`; test-only |
| `check unique` | `check_mioql_block` :1673 | — | **ONE**, but rigid — no extensibility path | 0 in `.mho`; test-only + docs |
| `save` | `save_block` :1624 | — | **ONE** (block only) | 71 |
| `save or update`/`upsert` | `save_or_update_block` :1637-1638 | — | **ONE** (block only, 2 spellings) | `upsert`: 2; `save or update`: 0 |
| `save all` | `save_all_block` :1642 | — | **N/A** — no filter concept, bulk insert | 0 |
| `update` | `update_block` :1646 | — | **ONE** (block only) | 62 |
| `remove` | `remove_block` :1653 | — | **ONE** (block only) | 5 |
| `modify` | `modify_block` :1715 | — | **ONE** (block only) | 2 |

## Flags for the design decision

- **Already two-tier:** only `grab` (unused) and `retrieve` (block heavily used, inline dead and
  structurally not disciplined to one condition).
- **Block-only, candidates to GET an inline quick form:** `find`, `save`, `update`, `remove`,
  `modify` — the five real-corpus-heavy verbs (12/71/62/5/2 sites respectively) that have no quick
  single-condition path today.
- **Single-form-only, candidate to GET a block form for extensibility:** `check unique` — its
  condition slot (`MATCH_MOD NAME TO value_expr`) is already inline-shaped but wrapped in block
  ceremony (`result_handlers closer`) with no room to grow to a composite key.
- **Redundancy risk already realized once:** `retrieve_inline` — it exists, but violates the
  "strictly one condition" discipline (comma-joined N pairs) and has zero real usage, suggesting
  either the discipline needs enforcing or the form should be reconsidered before it's used as the
  template for others.
- **Not applicable:** `save all` (bulk insert, no filter), `remove.all`/`save or update` vs
  `upsert` (spelling pairs, a different axis from inline/block).
