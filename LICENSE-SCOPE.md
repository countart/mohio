# LICENSE-SCOPE.md

Release: 4.9.0
Source-control tag: v4.9.0
First public distribution date: 2026-08-22
License: Business Source License 1.1
Change Date: 2030-08-22 (four years from first public distribution)
Change License: AGPL-3.0-or-later — match LICENSE

---

## Files Included in the Licensed Work

Only files listed in this section are part of the Mohio Public Core Licensed Work for this release.

### Compiler, parser, and language core
- `mohio_data/mohio.lark`
- `mohio_data/__init__.py` — resolves the installed location of the grammar, sector, and langmap files this package ships (mechanism, not content; added by the packaging relocation, commit `4d39d83`/`07d984c`).
- `mohio_pretokenizer.py`
- `mohio_transformer.py`
- `mohio_transformer_ast.py`
- `mohio_ast.py`
- `mohio_enforce.py`
- `mohio_symbol_table.py`
- `mohio_schema.py`
- `mohio_reachability.py`
- `mohio_version.py`

### Runtime and interpreter
- `mohio_interpreter.py`
- `mohio_services.py`
- `mohio_server.py`
- `mohio_html_sanitize.py`
- `mio_utils.py`

### Command-line tooling
- `mio.py`
- `mohio_fmt.py`
- `mohio_test_grammar.py`

### AI primitives (public surface)
- `mohio_ai.py` — public core. `ai.decide`/`ai.audit`/`ai.agent`/`ai.compare`/`ai.respond`/`ai.connect` are language primitives, not managed convenience services. Model-routing/cost-optimization *services* remain excluded under `mioai.*` (commercial); this file governs only the primitive mechanism.

### Compliance mechanism (public surface)
- `mohio_audit_grades.py` — public core. `required_grade()` and `classify_sink()` are the compiler-enforced "declare frameworks, determine the required audit grade, fail closed" mechanism, not managed fulfillment. DC-08 and DC-09's Normative Control Tests depend on this module; excluding it would make licensee-runnable compliance tests unrunnable.

### Client-side language
- `mohio_mioscript.py`

### Langmap engine (mechanism, not content)
- `mohio_langmap.py`
- `mohio_layer3.py`

### Sector profile loader (mechanism, not profiles)
- `mohio_sector_loader.py`

Rationale: consistent with the open-core model, the *loader* is mechanism and is public; *certified/official profiles* are commercial and excluded below.

### Editor tooling
- `mohio-vscode/` — VS Code extension (syntax highlighting, snippets, language config). Public, free, adoption tooling. Explicitly separate from the `tools/`/`scripts/` exclusion below; do not treat as internal build tooling.

### Dependency and packaging manifests
- `requirements.txt`
- `pyproject.toml`

### Public examples
- `examples/contact.mho`
- `examples/emoji_hello.mho`
- `examples/klingon_hello.mho`
- `examples/particularllc-skeleton.mho`

`examples/asseta/` is excluded from this release. Not public core, not yet in a shippable state. May be added to a future release's Files Included once ready.

### Demonstration langmaps (novelty/teaching)
- `mohio_data/maps/en-emoji.langmap`
- `mohio_data/maps/en-klingon.langmap`

The Spanish, Portuguese, and Hindi language packs are excluded from this release. Confirmed paid commercial offerings; Spanish/Portuguese may run a limited-time free promotion, Hindi is paid as of this release. These are not part of Mohio Public Core and do not ship with the runtime. (They also do not currently reside in this repository.)

### Demonstration sector profiles (teaching the mechanism)
- `mohio_data/sectors/sector-demo-low.sector`
- `mohio_data/sectors/sector-demo-high.sector`
- `mohio_data/sectors/sector-demo-regulated.sector`

Demo profiles, not certified ones. Included: they teach the mechanism without exposing certified content. Certified/official profiles remain excluded.

### Control tests
- `tests/` — the test suite, including all tests designated as Normative Control Tests in this file. Excludes `seed_zork.json` (any path): third-party game content used only as a test fixture, moved to `_private/` and not distributed.

**Rationale, and this one matters:** the LICENSE defines a Compliance-Reduced Build by reference to Normative Control Tests. If a licensee cannot run those tests, they cannot determine whether their modified build complies. Withholding the tests would make the central restriction unverifiable by the party bound by it.

### Documentation
- `Docs/` — licensed under BSL, same as code, **for the files actually distributed in this
  repository.** One license to manage rather than splitting code and docs across two license
  regimes. This does NOT reach the private guides carved out below, and does NOT reach
  `Docs/archive/` — see "Private docs excluded from the `Docs/` statement" and "`Docs/archive/`
  excluded from the `Docs/` statement," both below.
- `start-here/` — licensed under BSL, same as code.
- `README.md`, `NOTICE`, `LICENSE`, `LICENSE-SCOPE.md`, `VERSIONS.md`, `TRADEMARKS.md`, `RESERVED-COMMERCIAL-OFFERINGS.md`

#### `Docs/archive/` excluded from the `Docs/` statement

`Docs/archive/` is internal development history — retired build logs and investigation records
(`BUILD-LOG.md`, `BUILD-LOG-PROD.md`, `RECONCILIATION-2026-08-07.md` and similar) kept for our own
reference, not authored as user-facing documentation and not part of the distributed Licensed Work.
Unlike the private docs below, these files ARE git-tracked and DO ship in a clone or archive built
from this repository — the exclusion here is a scope decision, not a distribution mechanism, and is
stated explicitly for that reason rather than left to be inferred from `.gitignore`. Any release
archive built from the `Docs/` statement should exclude `Docs/archive/` accordingly.

#### Private docs excluded from the `Docs/` statement

We license what we ship, not what we keep private. A file that is gitignored / private-repo-only
never reaches a clone, a tag, or the wheel — the `Docs/` statement above cannot cover it no matter
how it reads, so this section makes the exclusion explicit instead of leaving it implied by an
untracked-file lookup nobody would think to do. If a file under `Docs/` is not in this
repository's git history, it is not part of the Licensed Work, regardless of the blanket statement
above. Applies to the following classes, all currently enforced by `.gitignore`:

- **Private sector guides.** `Docs/sector-financial.md`, `Docs/sector-healthcare.md`. The
  certified/official sector guides, distinct from the public demo profiles below.
- **Private langmap working docs.** `Docs/*langmap*` — currently
  `Docs/feature-langmap-layer2-resolver-2026-06-28.md`, `Docs/fix-langmap-direction-2026-06-26.md`,
  `Docs/langmap-chat-hindi-grammar-prompt-2026-06-29.md`. Internal langmap design/debugging notes,
  distinct from the public demo langmaps below.
- **Private services.** Any documentation of hosted platform services, managed integrations, or
  service backends — matching the existing "Files Excluded" categories below — is excluded from
  `Docs/` on the same basis, whether or not a specific file exists in this repository today.
- **Patent-status docs.** `Docs/patent-*` — currently `Docs/patent-implementation-status.md`.

**What DOES ship, named explicitly so the line is not implied:** the demonstration langmaps
(`mohio_data/maps/en-emoji.langmap`, `mohio_data/maps/en-klingon.langmap`) and demonstration
sector profiles (`mohio_data/sectors/sector-demo-low.sector`,
`mohio_data/sectors/sector-demo-high.sector`, `mohio_data/sectors/sector-demo-regulated.sector`),
listed in full above under "Demonstration langmaps" and "Demonstration sector profiles." Those are
tracked, public, and Licensed Work. Everything in this section is not.

---

## Redistributable Runtime Components

Only the following may be redistributed in object-code form as Runtime Components incorporated into, and reasonably necessary to operate, an Application permitted by the LICENSE.

- `mohio_data/mohio.lark`
- `mohio_data/__init__.py`
- `mohio_pretokenizer.py`
- `mohio_transformer.py`
- `mohio_transformer_ast.py`
- `mohio_ast.py`
- `mohio_enforce.py`
- `mohio_symbol_table.py`
- `mohio_schema.py`
- `mohio_reachability.py`
- `mohio_interpreter.py`
- `mohio_services.py`
- `mohio_server.py`
- `mohio_html_sanitize.py`
- `mohio_langmap.py`
- `mohio_layer3.py`
- `mohio_sector_loader.py`
- `mohio_mioscript.py`
- `mohio_version.py`
- `mio_utils.py`
- `mohio_audit_grades.py`
- `mohio_ai.py`

Not redistributable as Runtime Components: `mio.py` (CLI), `mohio_fmt.py`, `mohio_test_grammar.py`, `tests/`, `tools/`, `mohio-vscode/`, docs, examples. These are development tooling, editor tooling, or non-runtime materials.

---

## Files Excluded From the Licensed Work

The following are not included unless expressly listed above:

- enterprise runtime modules
- hosted platform services and service backends
- official paid langmaps and langmap marketplaces
- official sector profiles, certified profiles, compliance profiles, custom profiles, and profile-generation systems
- advanced audit, compliance, certification, entitlement, governance, cost-optimization, inference-control, or agent-control systems
- connectors, managed integrations, commercial services, signing keys, credentials, deployment infrastructure, and private repositories
- MOHIO trademarks, logos, certification marks, compatibility marks, and brand assets

### Specifically excluded from this repository
- `tools/` — internal build and lint tooling (`build_langref.py`, `langref_meta.json`, `silent_noop_lint.py`, `langmap_coverage.py`). Excluded.
- `scripts/` — internal automation. Excluded.
- `bucket/`, `playground/`, `dirtest/`, `cookbook/` — scratch and work-in-progress. Excluded.
- `seed_postgres.py`, `walkthrough_test.py` — internal utilities. Excluded.
- `examples/asseta/` — not shippable yet. Excluded, see above.
- the local design spine and any pre-filing IP documents (gitignored, never distributed)
- `.claude/` session configuration
- private sector guides, private langmap working docs, private patent-status docs, and any private
  service documentation under `Docs/` — gitignored, never distributed. Full detail and current
  file list under "Private docs excluded from the `Docs/` statement," above.

---

## Designated Controls and Normative Control Tests

### Designated Controls for this release

- `DC-01` — **never_store enforcement.** Fields declared never-store are not persisted.
- `DC-02` — **encryption at rest.** Fields requiring encryption are sealed on every write path.
- `DC-03` — **key-provider seam integrity.** Key material is obtained through the provider seam, not inlined.
- `DC-04` — **audit chain integrity.** Audit entries are hash-chained; forging or deleting an entry breaks verification.
- `DC-05` — **audit anchoring.** Chain truncation or repointing is detectable against external anchors.
- `DC-06` — **two-role audit isolation.** A tenant connection cannot write, alter, or scrub the authoritative audit trail.
- `DC-07` — **audit records names, never values.** Audit events record field names and context, never the sensitive values.
- `DC-08` — **lawful-erasure recording (tombstone).** `cm.purge` fails loud on a failed delete, is atomic across clauses, and writes a tombstone only when an erasure actually occurred.
- `DC-09` — **erasure verifier honesty.** The verifier reports UNVERIFIABLE on a read error and never returns a verdict from a failed read.
- `DC-10` — **PHI access auditing.** Access to protected health information is audited.
- `DC-11` — **PII purpose limitation.** Purpose limitation is enforced on personal data.
- `DC-12` — **filter integrity.** An unrecognized query filter fails loud and never matches all rows.
- `DC-13` — **data-change auditing.** Every data-change verb writes its audit and fails loud if the audit write fails.

### Normative Control Tests for this release

| Control | Test |
|---|---|
| DC-01 | `tests/test_pii_purpose.py`, `tests/test_encryption_all_writes.py` |
| DC-02 | `tests/test_encryption.py`, `tests/test_encryption_all_writes.py` |
| DC-03 | `tests/test_key_provider_seam.py` |
| DC-04 | `tests/test_audit_hash_chain.py`, `tests/test_audit_fail_loud.py`, `tests/test_canonical_audit_schema.py` |
| DC-05 | `tests/test_audit_anchor_verify.py` |
| DC-06 | `tests/test_audit_two_role_isolation.py`, `tests/test_audit_sink_seam.py` |
| DC-07 | `tests/test_audit_names_not_values.py`, `tests/test_ai_decide_audit_names.py` |
| DC-08 | `tests/test_cm_purge_failloud.py`, `tests/test_tombstone_reroute.py`, `tests/test_tombstone_rowref.py` |
| DC-09 | `tests/test_tombstone_dberror.py`, `tests/test_tombstone_verifier.py` |
| DC-10 | `tests/test_phi_audit_access.py` |
| DC-11 | `tests/test_pii_purpose.py` |
| DC-12 | `tests/test_filter_failloud.py` |
| DC-13 | `tests/test_modify_audit.py`, `tests/test_audit_preseal_gate.py` |

Additional supporting suites, not designated normative: `tests/test_audit_locks.py`, `tests/test_audit_sink_grading.py`, `tests/test_audit_table_contract.py`, `tests/test_audit_table_schema.py`, `tests/test_audit_chain_postgres.py`, `tests/test_not_found.py`, `tests/test_dead_store_warning.py`.

**Environment-dependence, resolved and verified live (2026-08-05), not asserted from the doc's own description:**

DC-02 (encryption at rest) conformance requires the `cryptography` package installed; a build without it cannot claim this control regardless of test outcome. `pyproject.toml`'s base dependencies were updated to require `cryptography` unconditionally, matching `requirements.txt`, so the standard packaged-install path can claim DC-02 by default rather than only via CI's `requirements.txt` path.

DC-05's normative test (`test_audit_anchor_verify.py`) has a known Windows-specific teardown artifact (an open SQLite handle blocks `os.remove()` on Windows, `WinError 32`) that does not affect its pass/fail signal. Verified by reading the control flow: every cleanup call runs strictly after that section's assertions are already tallied, and the exit code reads only the assertion tally. The artifact is cosmetic to teardown, not to the verdict.

Both controls remain normative as designated.

---

## Notes

This file controls license scope for the applicable release. Do not publish a release without confirming this file matches the files actually distributed.

Verification step before publishing: list the files in the release artifact and diff that list against the "Files Included" section above. A file present in the artifact but absent here is unlicensed; a file listed here but absent from the artifact is a broken reference.
