# Daily lanes and precise interval editing

Daily review uses **America/New_York** calendar days, from the first stored record to the
last record's declared end. The server owns interval facts in `daily_review.py`; the UI
renders them on a shared epoch-millisecond axis. An unpinned view extends when recordings
arrive. A pinned selection and saved human intervals stay fixed. New data is fetched on the
existing ten-second refresh; this page does not itself download device recordings.

## Three independent lanes

- **Recording:** union of declared one-second source intervals, with exact missing spans.
  Fractional selections and midnight boundaries clip displayed coverage, not stored source
  timestamps. Covered duration is measured separately from intersecting record counts. Device changes
  continue to break the amplitude line even where recorded-time intervals touch.
- **Quality:** exact unions of completed quality windows. Amber marks screening flags;
  green means computed without those flags, not proof of artifact-free EEG. Grey means
  not analyzed. Flagged intervals take precedence in overlapping windows; their flag names
  are retained. A missing/stale/invalid result is shown as unavailable or stale and is not
  applied to current recordings. Review labels do not supply quality facts.
- **Activities:** current participant-scoped assignments, with original source, review state
  and timing declarations retained. Overlapping legacy assignments use separate deterministic
  tracks so one label cannot hide another. Links on the lane open the existing label editor.

Amplitude remains aggregated from whole source seconds. Selecting a finer interval does
not recompute sample-level power or establish sample-level physiological alignment. Quality
flags belong to their analysis windows, not to individual samples. Human timing estimates
remain independent of source coverage and quality screening.

## Precise controls and daylight saving

Embedded handles still drag on the amplitude plot. Arrow keys move one second; Shift moves
one minute; Page Up/Down move one hour. Keyboard changes preserve an existing millisecond
fraction. Home/End use the relevant boundary. Handles cannot cross; the minimum interval is
one millisecond. Pointer selection snaps to seconds. Use **Enter precise start and end** for
millisecond values and **Apply time range** to pin the graph before assigning a label.

Daily controls and label datetime fields explicitly use New York time, regardless of the
computer's timezone. UTC offsets appear beside the selection. The server resolves wall time
with `zoneinfo` (looked up when needed, not during stdlib startup), returning both real instants when fall-back repeats an hour. The user must
choose EDT/EST independently for an ambiguous start or end. A spring-forward time that does
not exist is rejected rather than shifted silently. End is exclusive. Typed daily ranges
must lie within the day's recorded extent; the label editor can enter other historical
periods explicitly.

`GET /api/local-time?value=YYYY-MM-DDTHH:mm:ss.sss` returns the valid UTC choices. Saving uses
these timezone-qualified choices, never `new Date(unqualifiedInput)`. Existing assignments
reopen with the matching occurrence and exact milliseconds. Integer timestamp arithmetic
avoids float truncation in HTTP parsing and ISO output. A form changed/canceled or switched
to another participant during resolution cannot save the earlier pending submission.

Draft daily inputs, open controls and keyboard focus survive periodic chart refreshes.
Applying a range changes the zoom only; **Assign activity to selection** fills the label
editor, and its Save action records the annotation. Changing a boundary/occurrence clears
the previous timing precision declaration, as described in [personal activities](PERSONAL_ACTIVITIES.md).

## Quality artifacts and lifecycle

The source-workspace worker generates `quality-index.json` and
`quality-YYYY-MM-DD.json` next to each completed report. These version 1 sidecars derive from
canonical quality windows, carry participant and analysis identity, and split at New York
calendar midnights. They are deterministic; the full numerical report and its identity are
unchanged by this projection. Cancellation checks remain active while deriving/writing them.

`GET /api/daily-quality` accepts date and timezone-qualified start/end parameters. The
manager validates the selected participant, identity, ordered intervals, flags and file
locations, clips the runs and fills all uncovered time. It checks source freshness before
and after reading. Empty reader-created WAL files do not count as changes; nonempty
WAL frames and database changes still invalidate freshness. An older report without sidecars requires a new analysis run. Stale
results are withheld; the existing conservative freshness check can also become stale after
annotation-only database changes. The quality page offers an explicit rerun.

Sidecars are removed with pruned reports and after failed/canceled jobs. Only the validated
job's generated filenames are eligible; source recordings, annotations and exports are
untouched. Daily reads need only one bounded sidecar (8 MB maximum), not the full archive
report or NumPy. Packaged apps render lanes and edit times using stdlib code, but continue
to show numerical analysis unavailable unless a compatible completed result exists; bundling
a numerical runtime remains later release work. No new installer is published by this change.

## Validation

Tests cover disjoint recorded/gap accounting, fractional clipping, unchanged labels,
quality unions/overlapping flags, calendar splitting, stale/cross-participant results,
HTTP authentication and precise saves, DST missing/repeated times, and exact millisecond
round-trips. Node tests exercise pure geometry/formatting under a different host timezone.
Real worker tests compare canonical cold/cached report bytes and check sidecar retention.
Browser validation covers typing, keyboard selection, saving/reopening, data arrival and
pinned/unpinned refresh. Private full-archive evidence stays outside the public repository.
