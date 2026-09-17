# LICENSE-SCOPE.md

Release: 5.1.0
Source-control tag: v5.1.0
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
- `mohio_framework.py`: the `framework:` declaration registry and resolution point (T1-FRAMEWORK-FOUNDATION). Scaffolds an app's target structure, orthogonal to `sector:`, which enforces rules.
- `mohio_classification.py`: the qualified-identity classification resolver. One place that answers what a field is classified as, replacing five separate bare-field-name sets with a single resolver that DC-01/DC-02/DC-10/DC-11 depend on.
- `mohio_decisions.py`: the deliberate-decision registry. Records why a behaviour that looks like a defect is not one, in a form a verification pass can read, so a settled decision is not re-filed as a bug.

### Datasource / metasource (mechanism)
- `mohio_metasource.py`: the metasource contract, a normalized index of what a data source contains.
- `mohio_metasource_postgres.py`: the Postgres/Supabase source adapter.
- `mohio_metasource_mongo.py`: the MongoDB source adapter.
- `mohio_metasource_mysql.py`: the MySQL/MariaDB source adapter.
- `mohio_metasource_sqlite.py`: the SQLite source adapter.
- `mohio_metasource_shared.py`: the shared-artifact coordination policy (one metasource description, many instances, coordinated regeneration).
- `mohio_metasource_store.py`: the portable object-store client (S3-compatible) and singleton lock the shared artifact is coordinated through.

Rationale: consistent with the open-core loader-is-public pattern above, these seven files are the
generic, engine-agnostic MECHANISM (adapters against a schema any Postgres/Mongo/MySQL/SQLite
already has, and a portable object-store client that runs against any S3-compatible endpoint, not
a vendor-specific one). None of them is a managed or hosted service; a licensee runs them against
their own database and their own bucket.

### Write planning (mechanism)
- `mohio_write_intent.py`: the normalized form of a write. Eight node types spelled the same
  destination and predicate four different ways depending on which word the author typed; this
  is the one shape they are all read through.
- `mohio_write_effects.py`: the dependency analysis that answers whether a write inside a loop
  is independent across iterations, which is what any batching decision must rest on.
- `mohio_capability.py`: what each datasource CAN do, as a profile rather than a branch, so a
  plan asks whether a source returns generated ids from a batch rather than which engine it is.

Rationale: the same open-core reasoning as the metasource block above. These are engine-agnostic
mechanism, run by a licensee against their own data, not a managed or hosted service. All three
are in the package's module list and therefore ship in any install; licensing them here is what
keeps the artifact and this file in agreement.

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
- `MANIFEST.in` -- what the source distribution carries. Added this release after the sdist was
  found to be shipping the entire internal test corpus, 418 of its 481 entries.

### Public examples
- `examples/contact.mho`
- `examples/emoji_hello.mho`
- `examples/klingon_hello.mho`
- `examples/particularllc-skeleton.mho`

- `cookbook/` -- the recipe collection, as a FOLDER: nine runnable `.mho` programs and the
  `index.html` that shows each one with its real output. Scoped as a folder rather than file by
  file because it holds one kind of thing and nothing else: every file in it is a recipe written
  to be read and run by a licensee, verified by scanning the folder before naming it here.

`examples/asseta/` is excluded from this release. Not public core, not yet in a shippable state. May be added to a future release's Files Included once ready.

### Demonstration langmaps (novelty/teaching)
- `mohio_data/maps/en-emoji.langmap`
- `mohio_data/maps/en-klingon.langmap`

The Spanish, Portuguese, and Hindi language packs are excluded from this release. Confirmed paid commercial offerings; Spanish/Portuguese may run a limited-time free promotion, Hindi is paid as of this release. These are not part of Mohio Public Core and do not ship with the runtime. (They also do not currently reside in this repository.)

### Demonstration sector profiles (teaching the mechanism)
- `mohio_data/sectors/sector-demo-low.sector`
- `mohio_data/sectors/sector-demo-high.sector`
- `mohio_data/sectors/sector-demo-regulated.sector`
- `mohio_data/sectors/sector-demo-financial.sector`
- `mohio_data/sectors/sector-demo-soc2.sector`

The last two were already being packaged and were not named here, which is the one thing this
file exists to prevent: both are in the source distribution and the wheel, because the package
data takes the whole sectors folder, so they were shipping unlicensed. Both say in their own
opening lines that they are SAMPLE, non-certified and public, and both are named `demo_` for the
same reason the other three are: `financial` is a reserved paid-tier name whose real profile is
commercial and never ships, and no profile here certifies anything. Naming them is the correct
fix rather than dropping them from the package, since they teach the same mechanism.

Demo profiles, not certified ones. Included: they teach the mechanism without exposing certified content. Certified/official profiles remain excluded.

### Control tests

The licensed tests are the COMPLIANCE-VERIFICATION set, not the development suite. Two groups,
41 files, each named individually below rather than covered by a folder.

**Normative Control Tests** (32 files). The set the LICENSE points at. The Designated
Controls table later in this file is what DESIGNATES them; this is the inventory, named here so
that a filter reading Files Included alone finds every one of them. The control each serves is
in brackets.

- `tests/test_ai_decide_audit_names.py` [DC-07]
- `tests/test_audit_anchor_verify.py` [DC-05]
- `tests/test_audit_fail_loud.py` [DC-04]
- `tests/test_audit_hash_chain.py` [DC-04]
- `tests/test_audit_names_not_values.py` [DC-07]
- `tests/test_audit_preseal_gate.py` [DC-13]
- `tests/test_audit_sink_seam.py` [DC-06]
- `tests/test_audit_two_role_isolation.py` [DC-06]
- `tests/test_battery_audit_separate_connection.py` [DC-06]
- `tests/test_battery_authorized_upload_retrieval.py` [DC-16]
- `tests/test_battery_bulk_save_and_batch_audit.py` [DC-13]
- `tests/test_battery_cloud_upload_zone.py` [DC-16]
- `tests/test_battery_cloud_zone_double_registration.py` [DC-16]
- `tests/test_battery_file_protection_audit.py` [DC-16]
- `tests/test_battery_marker_keyed_decryption.py` [DC-14]
- `tests/test_battery_prevention_audited.py` [DC-15]
- `tests/test_battery_refusal_leaves_a_trace.py` [DC-15]
- `tests/test_battery_value_bound_classification.py` [DC-14]
- `tests/test_battery_value_bound_write.py` [DC-14]
- `tests/test_canonical_audit_schema.py` [DC-04]
- `tests/test_cm_purge_failloud.py` [DC-08]
- `tests/test_encryption.py` [DC-02]
- `tests/test_encryption_all_writes.py` [DC-01, DC-02]
- `tests/test_filter_failloud.py` [DC-12]
- `tests/test_key_provider_seam.py` [DC-03]
- `tests/test_modify_audit.py` [DC-13]
- `tests/test_phi_audit_access.py` [DC-10]
- `tests/test_pii_purpose.py` [DC-01, DC-11]
- `tests/test_tombstone_dberror.py` [DC-09]
- `tests/test_tombstone_reroute.py` [DC-08]
- `tests/test_tombstone_rowref.py` [DC-08]
- `tests/test_tombstone_verifier.py` [DC-09]

**Supporting suites** (9 files), licensed and shipped but NOT designated normative. They cover
the same controls from the side; a licensee determines compliance from the normative set above.

- `tests/test_battery_signed_upload_url.py`
- `tests/test_battery_engine_dialects_and_purge_truth.py`
- `tests/test_audit_locks.py`
- `tests/test_audit_sink_grading.py`
- `tests/test_audit_table_contract.py`
- `tests/test_audit_table_schema.py`
- `tests/test_audit_chain_postgres.py`
- `tests/test_not_found.py`
- `tests/test_dead_store_warning.py`

**Rationale, and this one matters:** the LICENSE defines a Compliance-Reduced Build by reference to
Normative Control Tests. If a licensee cannot run those tests, they cannot determine whether their
modified build complies. Withholding them would make the central restriction unverifiable by the
party bound by it. That argument reaches exactly as far as the tests a licensee needs in order to
answer the compliance question, and no further.

**THE REST OF `tests/` IS METHODOLOGY, NOT LICENSED WORK, AND IS NOT SHIPPED.** The other
~420 files are how this compiler is developed: adversarial batteries, interview sweeps, internal
probes, performance harnesses, the demo programs several of them drive (including the fraud
demo), and the Zork fixtures. They tell a reader how we work rather than whether a build complies,
and none of them is referenced by the LICENSE. They remain in the source repository; they are
simply not part of the Licensed Work and do not appear in the public artifact.

An earlier release scoped `tests/` as a whole folder. That was the same mistake this file
corrected for `Docs/`: a folder scope is only safe when the folder holds one kind of thing, and
this one holds two. Naming the compliance set means a new internal battery cannot become Licensed
Work by being written in the same directory.

Verified before this list was written: all 41 files exist, none imports another test module, and
none reads any other file under `tests/`, so each runs standalone against a tree carrying only the
licensed set. `seed_zork.json` was previously called out as an exclusion here and no longer needs
its own carve-out, since nothing under `tests/` is covered unless it is named.

### Documentation
- `Docs/manual/` -- the manual, as a FOLDER. Every chapter, and nothing else.
- `Docs/guides/` -- the user-facing guides, tutorials and references, as a FOLDER.

  **Why these are named and `Docs/` is not.** An earlier release licensed `Docs/` as a whole and
  carved out the parts that were not user-facing. That reading only holds while everything under
  `Docs/` is written for a licensee, and it is not: `Docs/design/`, `Docs/build-diary/` and a
  number of loose build reports live there too. A blanket plus carve-outs licenses whatever
  nobody remembered to carve out, and the carve-out list is what goes stale first.
  Naming the folders inverts it: what is not named is not licensed, so a new internal document
  cannot become Licensed Work by being written in the wrong directory. Both folders were created
  for this purpose and each file in them was checked individually before the folder was named.
- `start-here/` — licensed under BSL, same as code.
- `README.md`, `NOTICE`, `LICENSE`, `LICENSE-SCOPE.md`, `VERSIONS.md`, `TRADEMARKS.md`, `RESERVED-COMMERCIAL-OFFERINGS.md`

#### `Docs/archive/` and everything else under `Docs/`

`Docs/archive/` is internal development history — retired build logs and investigation records
(`BUILD-LOG.md`, `BUILD-LOG-PROD.md`, `RECONCILIATION-2026-08-07.md` and similar) kept for our own
reference, not authored as user-facing documentation and not part of the distributed Licensed Work.
Unlike the private docs below, these files ARE git-tracked and DO ship in a clone or archive built
from this repository — the exclusion here is a scope decision, not a distribution mechanism, and is
stated explicitly for that reason rather than left to be inferred from `.gitignore`.

The same now goes for every path under `Docs/` other than `Docs/manual/` and `Docs/guides/`:
`Docs/design/` and its `_private/` subfolder, `Docs/build-diary/`, and the loose investigation
reports at the top of `Docs/` are internal working material, none of it is Licensed Work, and
none of it needs its own carve-out any more, because it is excluded by not being named.

#### Private docs, excluded on a second and independent ground

We license what we ship, not what we keep private. A file that is gitignored or
private-repo-only never reaches a clone, a tag, or the wheel, so it could not be Licensed Work
however this file reads. The named-folder scope above already excludes everything below, since
none of it sits in `Docs/manual/` or `Docs/guides/`. This section is kept anyway, because an
exclusion that rests on two independent grounds survives a later edit to either one. Applies
to the following classes, all currently enforced by `.gitignore`:

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
- `mohio_framework.py`
- `mohio_classification.py`
- `mohio_decisions.py`
- `mohio_metasource.py`
- `mohio_metasource_postgres.py`
- `mohio_metasource_mongo.py`
- `mohio_metasource_mysql.py`
- `mohio_metasource_sqlite.py`
- `mohio_metasource_shared.py`
- `mohio_metasource_store.py`

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
- `DC-14`: **value-bound classification.** A classified value keeps its protection through a copy, masking follows the value on every display/egress path, and a copied `[phi]`/`[pci]`/`[pii]` value is sealed at the write, not only in the column that declares it.
- `DC-15`: **prevention-audit.** A control that refuses or reverses an action leaves an auditable trace, not only the controls that permit one. Covers purpose-limitation refusal, saga compensation, and a `cm.purge` refusal under a `cm.lock` legal hold.
- `DC-16`: **upload protection.** An uploaded file classified `[phi]`/`[pci]` is encrypted at rest, retrieval is gated by the same server-verified access control as any other handler, and every store/read/delete/move/copy is audited with whether the content was protected.

### Normative Control Tests for this release

| Control | Test |
|---|---|
| DC-01 | `tests/test_pii_purpose.py`, `tests/test_encryption_all_writes.py` |
| DC-02 | `tests/test_encryption.py`, `tests/test_encryption_all_writes.py` |
| DC-03 | `tests/test_key_provider_seam.py` |
| DC-04 | `tests/test_audit_hash_chain.py`, `tests/test_audit_fail_loud.py`, `tests/test_canonical_audit_schema.py` |
| DC-05 | `tests/test_audit_anchor_verify.py` |
| DC-06 | `tests/test_audit_two_role_isolation.py`, `tests/test_audit_sink_seam.py`, `tests/test_battery_audit_separate_connection.py` |
| DC-07 | `tests/test_audit_names_not_values.py`, `tests/test_ai_decide_audit_names.py` |
| DC-08 | `tests/test_cm_purge_failloud.py`, `tests/test_tombstone_reroute.py`, `tests/test_tombstone_rowref.py` |
| DC-09 | `tests/test_tombstone_dberror.py`, `tests/test_tombstone_verifier.py` |
| DC-10 | `tests/test_phi_audit_access.py` |
| DC-11 | `tests/test_pii_purpose.py` |
| DC-12 | `tests/test_filter_failloud.py` |
| DC-13 | `tests/test_modify_audit.py`, `tests/test_audit_preseal_gate.py`, `tests/test_battery_bulk_save_and_batch_audit.py` |
| DC-14 | `tests/test_battery_value_bound_classification.py`, `tests/test_battery_value_bound_write.py`, `tests/test_battery_marker_keyed_decryption.py` |
| DC-15 | `tests/test_battery_prevention_audited.py`, `tests/test_battery_refusal_leaves_a_trace.py` |
| DC-16 | `tests/test_battery_file_protection_audit.py`, `tests/test_battery_authorized_upload_retrieval.py`, `tests/test_battery_cloud_upload_zone.py`, `tests/test_battery_cloud_zone_double_registration.py` |

Additional supporting suites, not designated normative: `tests/test_battery_signed_upload_url.py` (see the DC-16 boundary note below), `tests/test_battery_engine_dialects_and_purge_truth.py` (raw sql across engines, and the honesty of a deletion warning; the erasure control itself is covered by DC-08's own tests), `tests/test_audit_locks.py`, `tests/test_audit_sink_grading.py`, `tests/test_audit_table_contract.py`, `tests/test_audit_table_schema.py`, `tests/test_audit_chain_postgres.py`, `tests/test_not_found.py`, `tests/test_dead_store_warning.py`.

**Environment-dependence, resolved and verified live (2026-08-05), not asserted from the doc's own description:**

DC-02 (encryption at rest) conformance requires the `cryptography` package installed; a build without it cannot claim this control regardless of test outcome. `pyproject.toml`'s base dependencies were updated to require `cryptography` unconditionally, matching `requirements.txt`, so the standard packaged-install path can claim DC-02 by default rather than only via CI's `requirements.txt` path.

DC-05's normative test (`test_audit_anchor_verify.py`) has a known Windows-specific teardown artifact (an open SQLite handle blocks `os.remove()` on Windows, `WinError 32`) that does not affect its pass/fail signal. Verified by reading the control flow: every cleanup call runs strictly after that section's assertions are already tallied, and the exit code reads only the assertion tally. The artifact is cosmetic to teardown, not to the verdict.

DC-16's normative test `test_battery_cloud_upload_zone.py` includes a real bucket round trip that
runs only when `MOHIO_STORE_ENDPOINT`, `MOHIO_STORE_BUCKET`, `MOHIO_STORE_REGION`,
`AWS_ACCESS_KEY_ID`, and `AWS_SECRET_ACCESS_KEY` are set, and says so loudly rather than passing
silently when they are absent. The control's dispatch, configuration precedence, and every
non-network assertion (23 of them) run and pass without those credentials; the live bucket write
is the one part of this release's testing that was not independently exercised without a
configured store present. Confirmed live this pass: 43/43, 25/25, and 23/23 across the three DC-16
batteries, with the cloud round trip reporting NOT RUN rather than a false pass.

**DC-16 AND THE SIGNED UPLOAD URL ADDED THIS RELEASE -- A STATED BOUNDARY, NOT A CLAIM.**

`sign upload url for` issues a temporary signed URL and the browser uploads straight to the
configured bucket. The bytes never reach the application, and that is the point of the feature:
a large upload stops being the application's memory, timeout and bandwidth problem.

It is therefore NOT within DC-16 as that control is written, and its test is deliberately not
listed above. DC-16 says an uploaded file classified `[phi]`/`[pci]` is encrypted at rest, that
retrieval is gated by the same server-verified access control as any other handler, and that
every store/read/delete/move/copy is audited. On the signed-URL path, verified by reading the
issuing code rather than assumed:

- The content never passes through Mohio, so Mohio does not seal it. A classified value written
  this way is not encrypted at rest BY THIS RUNTIME.
- The transfer does not pass the handler, so the handler's access check is not what gates it.
  The signature is: it authorises one key, one method, and one expiry window, and the runtime
  refuses to issue one for a local area precisely because a directory has no signature to give.
- Issuing a URL is not itself audited.

What the signature does enforce is proven and narrow, and `tests/test_battery_signed_upload_url.py`
is listed here as supporting rather than normative for that reason: the upload form authorises a
WRITE and the download form a READ, each is refused for the other verb against a real bucket, and
the lifetime is inside the signed material so moving the deadline invalidates it.

A deployment that must keep DC-16 over a file should upload it THROUGH the handler, which is the
path DC-16's own tests cover and which is unchanged.

**The decision, so this does not read as an open question.** Governance of uploaded content in
Mohio is privacy tags plus sector profiles. That is where a classification is declared and where
the rules attached to it are enforced, and it is the mechanism a deployment configures to say what
may be uploaded and what must happen to it. Base Mohio provides the transport: the upload and the
download, including the signed form added here. Carrying tag and sector governance onto the signed
path as well is exploration for after this tag, not a gap this release left open. It is recorded
in the backlog as work, not as an unresolved question.

All controls remain normative as designated.

---

## Notes

This file controls license scope for the applicable release. Do not publish a release without confirming this file matches the files actually distributed.

Verification step before publishing: list the files in the release artifact and diff that list against the "Files Included" section above. A file present in the artifact but absent here is unlicensed; a file listed here but absent from the artifact is a broken reference.
