# Discover recordings without activity labels

Open **Discover recordings** from Overview or Recording quality, or `/discover.html`.
Click **Analyze saved recordings**. Activity labels and clustering are not required.
P4-W1 provides readiness and a question register; daily feature-change and expanded
cross-day discovery views are later work packages. Existing quality/explorer links remain.

## Population and provenance contract

Every completed source quality job now writes `discovery.json` beside `summary.json`.
`discovery_contracts.build` derives it from **all canonical computed windows**, without
querying labels or selecting a clustering sample. The report retains the source revision,
analysis identity, owning implementation hash, requested range, timezone, demo flag and
first-to-last observed source extent (end exclusive). Collection after working hours is
included. An empty archive has null extent and no fabricated feature context.

Exact participant/device/source/ear/channel/rate/clock/units/session contexts stay separate.
Cloud acquisition remains **right ear / RIGHT_TEMP / 256 Hz**; Bluetooth files are excluded.
Each computed context reports flagged/unflagged counts, unflagged window-duration and dates
with touching windows. A midnight-crossing window may touch two dates but contributes once
to window counts/duration. Dates are not independent repetitions or inferred sessions.
Source exclusions, failed windows and remainder record-seconds remain separately visible
in full canonical accounting. Contexts without computed windows contribute to that source
accounting, not a fabricated feature-support group. Record-second accounting is not elapsed
union coverage or device-wearing duration; quality retains its existing union timeline.

Readiness is `no_recordings`, `no_unflagged_windows`, or `ready_for_exploration`.
The latter means screened feature support exists, not physiological validity, activity
recognition, independent repeatability or inference readiness. Labels remain optional;
activity context is explicitly uninspected. Missing sessions and unmeasured contact are
visible. There is no minimum-days heuristic, forced grouping or invented mental-state score.

The deterministic discovery identity hashes all facts, owning source bytes and timezone.
Output is bounded to 1,000 computed contexts, 20,000 touched dates per context and 2 MB;
exceeding a bound fails explicitly rather than publishing a clipped report. Cancellation
and validation run before publication. The lifecycle validates participant/analysis/content
identity before publishing the complete pointer, cleans discovery sidecars after failure
and prunes them with their owning quality job. Existing last-complete pointers survive a
failed/canceled run. Adding the owning module to the lifecycle source signature invalidates
old/dirty implementation outputs; no commit-only cache is trusted.

`GET /api/discovery` is stdlib-only and returns `current`, `stale`, `unavailable` or
`no_participant`. Current reads validate the participant and exact completed analysis and
self-content hash, with source signature checks before/after reading. Stale facts are
withheld. Earlier reports without discovery require a rerun; raw data is not migrated.
The web process never imports NumPy. Packaged builds retain honest numerical unavailability
until P5 bundles the analysis runtime.

## Optional question register

`GET/POST /api/discovery/questions` reads or saves participant/source-scoped human questions.
The owning path is `ArchivePaths.discovery / questions.sqlite3`, under
`data/processed/discovery/` in the profile, separate from raw EEG, labels and generated jobs.
Reading an absent store creates nothing. Question edits do not invalidate EEG analysis.
The store belongs to the user's profile and is preserved independently of job pruning.

A save requires the selected participant, current analysis and discovery identities, title (1–240
characters), notes (0–4,000) and `discovery` or `candidate` stage. IDs are stable; edits
require the previous integer revision. SQLite transactions compare-and-swap the revision
and append history, preventing stale edits or another participant's IDs from overwriting
questions. There is a limit of 100 questions per source/participant scope. IDs and history
timestamps belong to human operations, never deterministic analysis output. History is
retained locally; question-history export/undo UI is outside W1.

**Confirmation is deferred.** Requests for `reserved_confirmation` are rejected. Saving
a candidate neither reserves dates nor excludes them from exploratory analysis. Actual
holdout enforcement, prespecified inference and prediction remain conditional later work;
no reservation is advertised before the pipeline can honor it. Partial activity-context
entry belongs to P4-W4; questions do not assign, confirm or overwrite activity labels.

POST retains desktop authorization, selected-participant matching, local-header and
same-origin guards. The browser uses text nodes for user content, clears wrong-scope or
stale readiness facts, and refuses saves without a current report. Question-store failure
does not prevent rendering independently available discovery readiness.

## Validation and boundaries

Independent count/duration fixtures cover zero labels, empty/all-flagged support,
context isolation, touched dates, identity changes and corruption. Real worker/HTTP tests
cover deterministic sidecars, stale sources, scope, static assets, local guards and separate
question saves. Revision/history tests cover stale edit rejection and participant isolation.
Windows native smoke checks load the offline page/JS/CSS. The real archive pilot is a
read-only preservation/resource check, not scientific confirmation. No public installers
are released by W1; packaged numerical runtime remains P5.
