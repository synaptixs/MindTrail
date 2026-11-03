# Personal activities and assignment history

Each participant owns a vocabulary of activities. Names, favorites, categories and context
are personal; there is no default activity list and no assumed work schedule. Existing
explicit backfill and demo commands remain opt-in.

## Identity and names

`personal_activities.py` owns the model. `activities.id` is a stable identity scoped to
its participant. `activities.name` owns the current name; `labels.activity_id` links a
recorded period to that definition. SQL triggers maintain `labels.label` as a compatibility
projection so existing charts and analysis readers see the current name.

Renaming an activity keeps assignment IDs, notes, timestamps, source/review status and
batch identity. It updates all associated displayed names. Reassigning one period changes
its activity identity instead. Names match exactly within a participant: no case folding,
semantic grouping or automatic merging. Duplicate names, including archived names, are
rejected. Writes require the current definition version to prevent stale edits.

`labels.recorded_label` preserves the name saved at assignment. For migrated periods it
preserves the name present at migration; earlier unrecorded edits cannot be reconstructed.
`activity_history` records definition versions and `activity_label_history` records period
mutations, including deletion and the before/after state. These ledgers are append-only.
SQL compatibility writers can generate multiple snapshots for a single logical change;
these are snapshots, not a count of user actions. History begins with migration, not with
the original device collection date.

Archiving hides an activity from new assignment choices but retains its periods and history.
Existing periods can still be edited without reassignment. Restore reuses the same identity.
Definitions cannot be deleted. Episode undo, splitting and candidate-boundary review are subsequent Phase 2 work.

## Migration and restore

`app.setup_db` applies schema version 2 after the existing additive base-schema upgrades.
For nonempty archives it first creates a SQLite backup in
`ArchivePaths.activity_migrations` (`data/processed/activity-migrations/`). Backup files
are private to the local user. Raw recordings, credential namespaces and profile locations
are unchanged. Distinct exact `(participant_id, label)` pairs receive deterministic UUID5
identities; all original label columns are preserved. New definitions receive UUID4 IDs.
The schema marker makes repeated startup idempotent. Version 1 archives are validated against the
exact shipped definitions before a backed-up, transactional v2 upgrade. Existing IDs and
all original history fields survive; timing defaults to unknown and review state maps only
from the existing reviewed flag. The migration transaction rolls back
on failure and retains its backup for recovery.

Desktop restore accepts legacy archives without views/triggers or version 1 or 2 archives with
only the exact supported activity trigger definitions for their version and required columns. It refuses
unknown schema versions or modified triggers. Future trigger changes require an explicit
versioned migration. Restoring into a vocabulary-only workspace is blocked, as it already
contains personal data. Use a new empty workspace for recovery.

## UI and API contracts

The Activity log contains a collapsible **Your activities** manager. It supports custom
names, category/context, favorites, rename, archive/restore and paged version history.
Active loaded definitions populate the label-name suggestions; users can type a name
without creating it beforehand. Pages state the shown and total counts and provide Load
more. Switching participant clears forms and vocabulary. The new UI sends the expected
participant ID on writes and uses optimistic definition versions; stale writes are rejected.

| Endpoint | Result |
| --- | --- |
| `GET /api/activities` | Selected participant; offset, limit (1–100, default 50), includeArchived (0/1); total and hasMore |
| `POST /api/activities` | Create a personal definition |
| `POST /api/activities/<id>/edit` | Edit using the current integer version |
| `GET /api/activities/<id>/history` | Selected participant's paged definition versions |
| `GET /api/export/activities.json` | Complete snapshot: definitions, versions, current assignments and assignment history |
| `GET /api/export/activity-assignments.csv` | Current assignments with stable IDs, recorded name, source and review status |

Existing label APIs keep their fields and add `activityId` and `recordedLabel`. Existing
`/api/export/labels.csv` retains its exact four columns. The new JSON export streams all
rows in one SQLite read snapshot and is not clipped by UI paging. JSON format version 2 adds the evidence fields to current assignments and history;
extended assignment CSV appends them, while the legacy four-column CSV is unchanged.
All endpoints retain
the desktop authentication, setup and CSRF gates. Exports contain the selected participant
only. The JSON is a portable audit export, not a substitute for SQLite archive restore.

Status `activitiesRevision` is a scoped UI refresh token derived from definition versions
and assignment-history events. It is not a permanent analysis-cache fingerprint. Analysis
continues to use its existing source/content provenance contracts.

## Verification

Synthetic tests cover participant isolation, rename and original names, archive/restore,
collision/stale edits, compatibility SQL writers, complete exports, pagination, backup,
transaction rollback/retry and restore trigger validation. HTTP tests exercise the same
contracts behind desktop authentication. Native Windows validation checks fresh startup,
installation, upgrade and uninstall while retaining activity identity/settings/history.
Private archive migration validation stays outside the public repository.

## Label evidence: source, review and timing

These are separate facts on each period, not properties of an activity definition or EEG
quality metrics:

| Field | Contract |
| --- | --- |
| `source` | Original origin, e.g. manual, schedule or model. Human review does not change it; in-place source changes are rejected. Existing source strings are retained. |
| `review_state` | confirmed, needs_review or uncertain (unsure of the activity). `reviewed` remains the compatibility projection: 1 only for confirmed. |
| `timing_precision` | unknown, exact (known entered boundaries), or approximate. This is a person's declaration, not verified physiological alignment. |
| `start_uncertainty_ms`, `end_uncertainty_ms` | Nonnegative whole milliseconds, maximum 24 hours; null means unknown, not zero. A running activity has no end uncertainty. |

Unknown timing has null uncertainties. Exact timing requires zero at each known boundary.
Approximate timing requires at least one positive uncertainty; the other boundary may
remain unknown. These symmetric ± ranges describe entered estimates, not statistical
confidence intervals. A person's label confidence and timing precision are independent.

The label editor explicitly selects a review state and timing declaration. Editing a
suggestion initially keeps **Needs review**; saving a note alone does not confirm it in
this UI. Its original source and note remain visible. New manually entered labels default
to **Confirmed by me**, with timing unknown. Changing a typed boundary resets the editor's
old timing declaration. Legacy API callers omitting evidence retain their prior explicit-save
confirmation behavior; unchanged boundaries retain timing, changed boundaries reset it.
The live Stop action records its click time as the end; it preserves unknown or approximate
start timing and closes a known-boundary interval with zero declared end uncertainty.

`POST /api/labels` and `/api/labels/<id>/edit` accept an optional `evidence` object:

```json
{"reviewState":"confirmed","timingPrecision":"approximate","startUncertaintyMs":30000,"endUncertaintyMs":60000}
```

Public labels add `reviewState`, `timingPrecision`, `startUncertaintyMs` and
`endUncertaintyMs`. Full JSON history and extended CSV use the storage field names above.
`/api/labels?scope=` accepts all, suggested, needs_review, uncertain or confirmed, retaining
its shown/total bound. Suggested selects needs-review schedule/model origins only.
Uncertain human decisions remain protected from suggestion replacement. Automatic splitting
of pending schedule/model suggestions preserves origin, batch, activity ID and review status;
new fragment boundaries have unknown timing. No model classifier is added by this package.

The activity comparison separates source/review groups, so unreviewed and uncertain periods
are not pooled with confirmed periods of the same activity. Daily shading tooltips and
period labels expose the review, origin and timing facts. A label review does not alter
signal-quality flags, sampling gaps or feature extraction, and confirming a label does not
validate EEG quality. The later inference phase must choose an explicit eligible-label policy.

The synthetic v1 SQL fixture was generated with the shipped main `da99ee4` implementation.
Tests independently restore that fixture and verify the v2 migration, failure rollback,
review/origin independence, precision validation, authenticated APIs and export round trips.
