# Episode review, decisions and undo

`episode_review.py` owns the participant-scoped review queue and operation ledger.
The stdlib review page is available from **Episode review** and **Split / history**
beside an existing interval. Numerical candidate detection is optional and remains
source-workspace-only; manual review, splitting and undo work in packaged runtimes.

## A person's labels, with optional cues

Saved label periods display **Saved interval #N** independently of hidden annotation names.
The number identifies an interval assignment, not its activity definition or a history change.
Use **Find a saved interval** to open a confirmed period that has left the pending queue;
opening by number explicitly reveals its current activity and note. The editor repeats the
interval number; history actions use **Change #N** to distinguish their separate identities.

The default queue hides existing annotation names and does not request model proposals.
It interleaves pending/unsure annotations with ordinary unassigned recording coverage.
Ordinary intervals use a stable SHA-256 shuffle, independent of signal features; they
are not a probability sample suitable for statistical inference. Each page states its
shown/total counts, offset, selection reasons, source coverage and excluded invalid
timestamps. Items can overlap and must not be counted as independent observations.
The complete queue is built from all eligible stored timestamps, not a recent-days cap.

Ordinary periods are continuous recorded spans minus existing labels/unknown decisions,
cut at UTC half-hour boundaries. Exact coverage is the union of declared one-second
records, including fractional starts; a record count is not substituted for duration.
Invalid timestamps are counted and excluded. Sample validity and artifact screening
remain separate quality facts. Active labels are edited in the existing Activity log;
queue review/splitting requires a finished period.

**Show existing annotations and optional PELT proposals** explicitly reveals names and
adds current scoped candidates alongside ordinary coverage. Missing/disabled/stale
candidate snapshots are reported; a lack of current proposals is not a finding of no
signal changes. Candidate periods overlapping existing labels or confirmed unknown spans
are withheld from the queue. No model-derived activity names are supplied. The current conservative freshness signature
also marks numerical snapshots stale after annotation-only saves; rerun analysis to continue
proposal review. Manual/blind queue review remains available immediately.

The selected-period preview displays only the first ten seconds of the original queue
item, centered per source second, with gaps blank. Changing typed bounds does not silently
claim a new waveform preview. A link opens the full EEG page, which may show annotations.
The editor accepts an individual's own activity name without a predefined vocabulary.
New York time controls resolve EDT/EST and milliseconds through the existing canonical
resolver. Browser-normalized whole minutes receive explicit zero seconds before resolution.

## Decisions and provenance

- **Save my activity** explicitly creates/edits an assignment. Review state and timing
  remain independent. A blind edit with a blank note retains the existing note. Unchanged
  boundaries can retain timing; changed boundaries reset it unless the person declares it.
- **Confirm existing annotation** is offered only with names revealed; it preserves source
  and timing. Pending schedule/model origins do not become manual origins after review.
- **Reject suggestion** removes only a pending schedule/model label and records a separate
  rejection. Skipping an ordinary/proposed item records a rejection of that item, not an
  activity, quality flag or unknown decision. Changed task identities can appear again.
- **Activity unknown** is an explicit decision, independent of vocabulary and uncertain
  guesses about a named activity. On a pending/unsure label it replaces that annotation
  reversibly; it cannot replace a confirmed label through the queue. Existing overlaps are
  protected. Unknown spans are distinct in the activity comparison and daily lane and
  suppress unassigned queue coverage. Undo the decision before assigning over it.

A review save validates the queue key again inside its write transaction. Candidate
provenance is retained in the operation's `context_json`: task key, method, candidate
identity, original bounds and search resolution. This records suggested timing, not a
model-generated activity. Ordinary/suggestion review reasons also survive in context.

## Reversible operations

All new human label create/edit/stop/delete APIs journal their logical operation, including
pending suggestion fragments affected by the save. Split, confirm, reject, unknown and
undo use the same ledger. Compatibility SQL writers retain the original assignment-history
triggers but do not invent a logical operation; older history remains in the audit export.

Splitting requires the current assignment-history version and a cut strictly inside a
finished interval. The left ID is retained; the right gets a new ID. Activity identity,
source, batch, note and review state are copied. Both periods get unknown timing because
no precision was declared for the new cut; original timing remains in immutable history.
Use the current-ID links in history to edit either resulting period independently.

Undo verifies the touched rows and their post-operation assignment-history versions.
An ABA edit (changed and then changed back) still blocks an old undo. Restoration cannot
overwrite another participant, a later edit or an unrelated overlapping label/unknown
span. It restores only touched intervals, keeping unrelated work. Deleted IDs can be
restored; append-only assignment history records the restoration. Vocabulary definitions
created while labeling are retained, even if their assignment is undone.

Undo itself appends a new operation and can be reversed once. Each operation can have only
one direct undo. Unknown/rejection rows receive a fresh generation when restored, so an
older operation cannot reuse a coincidentally identical decision. Stale/cross-scope/invalid
snapshots are refused before any restoration; failed transactions roll back completely.

## Schema, backups and exports

The activity schema remains version 2. A separate `episode_schema` version 1 adds:

- `episode_operations`: append-only before/after snapshots of touched label/decision rows,
  version guards, source context, an optional unique `undo_of`, and human operation time.
- `episode_decisions`: unknown/rejected interval decisions with stable task keys and
  generations. Their mutations are recorded by the operation ledger.

Nonempty archives get a consistent SQLite backup under
`ArchivePaths.activity_migrations / episodes / before-episodes-v1-*.sqlite3` before the
transactional additive migration. Raw recordings, old labels, activity IDs and all prior
history are unchanged. Startup is idempotent; failure retains the backup and rolls back.
Restore validates the exact owned activity and episode triggers, required columns and
schema versions. Unknown/custom triggers remain unsupported.

`/api/export/activities.json` format version **3** preserves all earlier sections and adds
complete participant-scoped `episodeDecisions` and `episodeOperations`. Current assignment
CSV and the legacy four-column label CSV retain their contracts. The operation ledger is
an audit export; a full SQLite archive is the restore format.

| Endpoint | Scope and behavior |
| --- | --- |
| `GET /api/review/queue` | `cues=0/1`, offset, limit 1–100; full totals and selection reasons |
| `GET /api/review/label?id=` | Current scoped assignment and optimistic history version |
| `GET /api/review/history` | Paged operation snapshots and direct-undo state |
| `POST /api/review/action` | Explicit confirm, rejected or unknown for a current task key |
| `POST /api/review/split` | Participant, label ID/version and timezone-qualified cut |
| `POST /api/review/undo` | Participant and operation ID; conflicts return an explanation |

All endpoints retain launcher authentication, setup and same-origin/CSRF gates. Mutation
requests require the selected participant. Deterministic queue derivation uses no random
calls, clocks, LLMs or uploads; UUIDs/timestamps belong only to explicit human mutations.
