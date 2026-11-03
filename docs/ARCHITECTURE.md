# Source and archive ownership

The Git checkout is the canonical source. A selected profile is the canonical archive;
it can live outside the checkout. Code updates must not create a competing copy of the
person's archive or change its credential namespace.

## Source

- `src/`: application modules, analysis commands, dashboard assets and existing build entry points.
- `test/`: tests grouped by owning component; mutable fixtures use temporary directories.
- `test/fixtures/`: synthetic, shareable fixtures only.
- `config/requirements/`: dependency definitions; root requirements files forward to them.
- `config/`: configuration templates, plus an ignored local development profile selection.
- `scripts/`: source-profile launcher and platform build commands.
- `installer/`: runtime locks, packaging templates, signing and native validation.

Existing `src/*.py` module names and `test/test_*.py` discovery paths stay compatible.
This cleanup does not split the application into new packages or implement the analysis
roadmap. Introduce narrower owning modules as those features are developed.

## Archive

`src/archive_paths.py` owns new output locations and legacy read fallbacks. Consumers
must use this helper instead of independently guessing directory names.

| Content | New output | Existing files |
| --- | --- | --- |
| Cloud snapshots | `data/raw/cloud/export-*` | Read `data/export-*` in place |
| Bluetooth captures | `data/raw/bluetooth/session-*` | Read/download `data/bluetooth/session-*` in place |
| Pattern reports | `data/processed/patterns/*` | Read `data/patterns/*` when no new report exists |
| Activity backfill reports/backups | `data/processed/activity-backfill/*` | Leave historical output unchanged |
| Database, labels, settings, key and locks | Existing profile root | No move or identity change |

Cloud snapshots are searched newest-name first across both layouts. For duplicate names,
the new layout takes precedence. Pattern writers always use the new path; readers may
fall back to a legacy report. Bluetooth file downloads retain session/file validation in
both layouts and prefer a new-layout duplicate session.

No archive files are automatically migrated or deleted. Raw captures and human annotations
are not disposable caches. The current SQLite database contains recordings and annotations;
physically splitting it requires a separately tested migration.

## Source-profile selection

`scripts/run-local.py` resolves `--profile`, then `AWEAR_DATA_DIR`, then the ignored
`config/development.local.json`. It sets the existing environment variable before executing
the selected source command. Without a selection, command defaults remain unchanged.

Only `data_directory` is accepted in local configuration. Keys stay in the selected profile's
existing `.env` or credential store. Packaged apps do not invoke the source launcher and
retain their existing OS/legacy-profile discovery behavior.

## Distribution

Phase 1 source-provenance contracts and the read-only inventory command live in
`src/analysis_provenance.py`; see [analysis provenance](ANALYSIS_PROVENANCE.md).
They are not yet integrated into the desktop analysis UI or runtime bundle.
`src/analysis_quality.py` owns the shared spectral primitive and source-only
quality/extraction core; see [quality analysis](ANALYSIS_QUALITY.md). The existing
clustering feature wrapper reuses its primitive while preserving old outputs.

Windows copies an explicit application/static allowlist, including `archive_paths.py`.
Mac packaging follows imports and an explicit static-data selection. Neither packages
source configuration, tests, local archives, or keys. Native upgrade tests use isolated
synthetic profiles and must preserve both legacy and new capture/report paths.
