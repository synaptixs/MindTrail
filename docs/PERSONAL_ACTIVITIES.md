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
Definitions cannot be deleted. Episode undo, splitting, precision/provenance controls and
candidate-boundary review are subsequent Phase 2 work.

## Migration and restore

`app.setup_db` applies schema version 1 after the existing additive base-schema upgrades.
For nonempty archives it first creates a SQLite backup in
`ArchivePaths.activity_migrations` (`data/processed/activity-migrations/`). Backup files
are private to the local user. Raw recordings, credential namespaces and profile locations
are unchanged. Distinct exact `(participant_id, label)` pairs receive deterministic UUID5
identities; all original label columns are preserved. New definitions receive UUID4 IDs.
The schema marker makes repeated startup idempotent. The migration transaction rolls back
on failure and retains its backup for recovery.

Desktop restore accepts legacy archives without views/triggers or version 1 archives with
only the exact supported activity trigger definitions and required columns. It refuses
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
rows in one SQLite read snapshot and is not clipped by UI paging. All endpoints retain
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
