# VERSIONS.md

| Version | First Public Distribution Date | Change Date | Change License | Source-Control Tag | Notes |
|---|---:|---:|---|---|---|
| 4.8 | 2026-08-05 | 2030-08-05 | AGPL-3.0-or-later | v4.8 | First public BSL 1.1 release. File scope for this version is fixed by the `LICENSE-SCOPE.md` committed at the tag above. |
| 4.8.1 | 2026-08-05 | 2030-08-05 | AGPL-3.0-or-later | v4.8.1 | Patch release. Fixes a packaging gap where the installed wheel shipped with no grammar file at all (`mohio.lark`, the sector profiles, and the langmaps relocated into `mohio_data/` so they are actually included); `connect db as sqlite from env.X` now consults its declared source instead of silently falling back to the constructor default; `miosearch`'s check-time severity corrected from WARNING to ERROR to match its real runtime crash. Also includes the durable session-store provider seam (in-memory and Postgres-backed session storage, survives a process restart, preserves role and hold-scoped state), which landed on `main` between `v4.8` and this tag and lives inside `mohio_interpreter.py`, already part of the Licensed Work. File scope for this version is fixed by the `LICENSE-SCOPE.md` committed at the tag above. |

Each version of the Licensed Work is licensed separately. The Change Date may vary for each version. A version not listed in this table has not been publicly released under this License and carries no Change Date obligation.
