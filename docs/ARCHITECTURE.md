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
| Personal activity migration backups | `data/processed/activity-migrations/*` | Retain pre-migration databases |
| Database, labels, settings, key and locks | Existing profile root | No move or identity change |

Cloud snapshots are searched newest-name first across both layouts. For duplicate names,
the new layout takes precedence. Pattern writers always use the new path; readers may
fall back to a legacy report. Bluetooth file downloads retain session/file validation in
both layouts and prefer a new-layout duplicate session.

No archive files are automatically moved or deleted. SQLite annotation schemas use explicit
backed-up migrations described below. Raw captures and human annotations
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
They feed the source-workspace analysis UI; numerical runtime bundling remains pending.
`src/analysis_quality.py` owns the shared spectral primitive and source-only
quality/extraction core; see [quality analysis](ANALYSIS_QUALITY.md). The existing
clustering feature wrapper reuses its primitive while preserving old outputs.

Windows copies an explicit application/static allowlist, including `archive_paths.py`.
Mac packaging follows imports and an explicit static-data selection. Neither packages
source configuration, tests, local archives, or keys. Native upgrade tests use isolated
synthetic profiles and must preserve both legacy and new capture/report paths.


The source dashboard's `analysis_manager.py` is a stdlib process supervisor.
`analysis_worker.py` alone imports numerical extraction and content-addressed
`analysis_cache.py`; `analysis_view.py` derives bounded elapsed-time coverage facts
from the report. HTTP/desktop setup changes consult the manager before changing
sources. Jobs and current pointers live under `ArchivePaths.quality`; raw storage
and existing installer profile/credential identities remain unchanged. See
[background analysis contracts](ANALYSIS_QUALITY.md#source-workspace-background-analysis)
for cancellation, freshness, retention, and packaged-runtime limitations.

The source-only `analysis_acceptance.py` runner checks a frozen private archive against
`config/analysis-acceptance.json`. `analysis_resources.py` owns OS peak-memory measurement,
which belongs to job metadata rather than deterministic feature reports. See
[Phase 1 acceptance](PHASE1_ACCEPTANCE.md) for scope, preservation and resource ceilings.

`personal_activities.py` owns participant vocabulary, stable activity IDs and immutable
assignment/name history. It maintains legacy label names through a compatibility projection
and backs up nonempty archives before its additive migration. See
[personal activities](PERSONAL_ACTIVITIES.md) for identity, exports and restore contracts.

`daily_review.py` owns elapsed-time lane facts, quality-window projection and New York
wall-clock resolution. Daily charts render those facts using the bundled `interval_time.js`
geometry/formatting helpers. See [daily review](DAILY_REVIEW.md) for clipping, snapshot
freshness, precise editing, DST choices and quality-sidecar lifecycle.

`episode_review.py` owns the separately versioned review decisions and reversible operation
ledger. Activity restore validation delegates the episode trigger contract to that module.
See [episode review](EPISODE_REVIEW.md) and [Phase 2 acceptance](PHASE2_ACCEPTANCE.md).

`exploratory_models.py` owns the source-only frozen quality-snapshot comparison. It does not
write the archive or replace the legacy Patterns API/UI. See
[exploratory models](EXPLORATORY_MODELS.md) for sampling, acquisition isolation and candidate
method contracts. Packaged numerical runtime remains Phase 5 work.

`exploratory_stability.py` reuses that module's canonical snapshot, sampling and feature
primitives for bounded parameter, feature-ablation and temporal/day-refit diagnostics. See
[stability contracts](EXPLORATORY_STABILITY.md); it does not replace the existing Patterns
API/UI or infer session/contact measurements from recording paths.

`exploratory_views.py` owns bounded PCA/feature/representative sidecars generated by the
optional exploration branch of the quality worker. `exploration_access.py` reads only scoped
current sidecars and verifies exact source-record digests before returning raw waveforms.
The `/explore.html` page renders these facts. See [explorer contracts](EXPLORATORY_VIEWS.md).

`activity_comparisons.py` owns optional full-quality-population personal activity/day/session
facts in the isolated worker. `activity_access.py` serves bounded, scoped, content-verified
facts and exact observed examples without numerical imports. `/compare.html` renders those
facts; unknown cloud session IDs remain unknown. See [activity comparisons](ACTIVITY_COMPARISONS.md)
for coverage, provenance filters, lifecycle and the still-open Phase 3 evidence gate.

`discovery_review_model.py` derives the bounded ordinary/change/recurrence snapshot in the
worker; `discovery_review_access.py` serves verified current facts without numerical imports.
`discovery_context.py` owns revisioned human observations in the separate discovery context
database, reusing vocabulary/timing validators without altering activity assignments. See
[selective review](DISCOVERY_READINESS.md#selective-review-and-optional-context-p4-w4).

`discovery_report_model.py` seals the existing discovery owners' numerical facts in the
worker. `discovery_report_access.py` composes verified human snapshots without numerical
imports; `discovery_decisions.py` owns the separate scoped direction register.
`discovery_report_render.py` is the single escaped live/offline renderer, with self-contained
`report.css` for downloaded HTML. Renderer bytes and human revisions enter export identity;
they are not silently inferred again by the browser. See the
[report contract](DISCOVERY_READINESS.md#discovery-report-and-next-direction-p4-w5).

`discovery_insights.py` derives question-linked longitudinal summaries from the verified
numerical report and exact scoped question snapshot, without numerical imports or human-store
writes. View selection and model identity enter the final report snapshot; rendering and
export reuse the same facts. See the [question-view contract](DISCOVERY_READINESS.md#question-linked-longitudinal-views-p5-w1).

`analysis_history.py` owns immutable compact numerical captures under the existing discovery
path. The manager captures validated worker output before current-pointer replacement;
this is a numerical-capture history, not a transactionally coupled adoption ledger.
`monitoring_model.py` owns compatible, exact-context version differences and complete row
accounting; `monitoring_access.py` separates historical facts from live lifecycle freshness.
`monitor.html` renders those facts and offers browser-local opt-in refresh. See
[monitoring/history contracts](DISCOVERY_READINESS.md#recording-changes-and-retained-versions-p5-w2).

`analysis_exports.py` owns bounded deterministic audit ZIP composition and full-quality
content digests. It reuses the existing report renderer, annotation exporters and canonical
quality facts. `analysis_history.export_all` owns complete scoped numerical-history reads
in one transaction. Neither is an archive importer or a numerical runtime; see
[auditable downloads](DISCOVERY_READINESS.md#auditable-analysis-and-retained-history-downloads-p5-w3).

P5-W4 source builds add an isolated packaged analysis runtime. `analysis_runtime.py` owns
runtime selection/manifest/availability and limits; `analysis_bootstrap.py` owns the worker
entry point. `analysis_runtime_build.py` stages pinned publisher artifacts and source/config
assets. Mac keeps a stdlib GUI and a separate universal CPython with per-CPU vendor trees;
Windows extends its existing embedded interpreter. `installer/analysis-runtime.lock.json`
pins numerical downloads; the existing Windows lock retains all embedded dependencies.
Native acceptance is shared by Mac and Windows in `installer/smoke_analysis.py`. See
[packaged analysis contracts](ANALYSIS_QUALITY.md#packaged-numerical-runtime-p5-w4-source-builds).

## Complete workspace recovery (P5-W5 source builds)

`profile_recovery.py` owns credential-free recovery ZIPs: the core SQLite snapshot,
all persisted files under `ArchivePaths.data` (including both legacy layouts), and
questions/context/direction/monitoring stores. Only regenerable quality jobs/cache
are omitted from that data tree. The manifest lists every included file, size and
SHA256, plus explicit exclusions. Recovery is bounded to 20 GiB, 100,000 files and
5 GiB per database; unsupported paths/schemas stop the operation rather than
silently producing a partial backup. This ZIP contains private recordings and
human notes; the user must store and share it accordingly.

SQLite backup incorporates committed WAL frames. HTTP requests share concurrent
read leases; authenticated backup/restore takes an exclusive, bounded lease and
checks sync/Bluetooth/analysis idleness. Ordinary navigation remains concurrent.
Inventory/stat guards detect changes by external writers during snapshot or
validation. Stop other programs that write the workspace before recovery.

Restore accepts only a new, empty profile before setup. It validates the entire
inventory, checksums, portable paths and owner-defined SQLite schemas before
promoting owned files, with rollback if migration fails. It never restores keys,
root configuration, OS vault entries, locks or logs. A legacy SQLite import remains
a database-only compatibility route and cannot replace existing discovery stores.
Settings → Advanced settings provides backup, restore, and an explicit offline
archive mode. The normal first run remains API key → Connect.

`profile_identity.py` preserves the original scope hash seed across profile moves.
The marker's seed is opaque hash input, never an accessed path; existing profiles
without a marker keep their original scopes. Immutable question/context/direction
and numerical histories remain accessible after restoration without rewriting
records. Historical captures remain historical; full current analyses and caches
must be regenerated. This is not an automatic cloud backup or a release gate pass.


## Guided workspace and progressive navigation

`workspace_journey.snapshot` owns the read-only `/api/workspace` facts: selected-source
record count/extent, preparation state and supported next actions. It reuses
`AnalysisManager.state` and `discovery_contracts.read`; only matching participant and
analysis identities with a current, nonstale discovery report can enable exploration.
Raw counts never establish valid feature support. A second observed date in a canonical
context exposes the cross-day tool, whose own evidence/readiness rules still apply;
it does not establish independent repetitions or successful recurrence.

`journey.js` renders these facts on Overview and `/journey.html`. One explicit action
starts the existing local analysis route, preserving saved options and using the
existing worker/resource/cache contracts. Page polling is read-only and does not start
jobs. There is no new always-on analysis loop, activity inference or automatic labeling.
Bluetooth-only and offline empty workspaces receive separate instructions.

Every dashboard page keeps three primary links and an expandable More tools group.
Overview renders daily recordings first, with optional activity and signal/export panels.
`navigation.js` reveals collapsed ancestors for existing hash links, daily selections and
inline edits, retaining precise interval URLs and operation behavior. The legacy patterns
reader and its stored reports remain available; only its empty-state instructions change.
The first-run key guide remains on the welcome screen but expands at the user's choice.
The shipped v0.2.7 artifacts are unchanged; distributing this UI requires a new release.
