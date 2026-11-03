# Discover recordings without activity labels

Open **Discover recordings** from Overview or Recording quality, or `/discover.html`.
Click **Analyze saved recordings**. Activity labels and clustering are not required.
Readiness, a question register and daily feature/change views are available without labels.
Expanded cross-day recurrence follows in P4-W3. Existing quality/explorer links remain.

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

## Daily features and inspection (P4-W2)

The same analysis now creates a content-hashed `discovery-daily-index.json` and one
`discovery-day-{acquisition group}-{date}.json` per exact context/local calendar date.
`discovery_daily.py` owns daily extents, distributions, aggregation, change cues and range
summaries. Its HTTP-facing functions use only the standard library. Annotation reads are
performed inside the worker via the existing comparison owner; no activity labels are
required, and no labels or raw samples are written.

Every timed canonical computed/excluded/failed/remainder source span contributes to the
context's observed extent. Invalid-time exclusions remain separately unplaced. Calendar
boundaries use real timezone transitions (23/25-hour days). Midnight-crossing windows remain
in elapsed coverage but are not split into day-specific feature values. Each daily payload
stores all fully contained four-second windows, including flagged windows for accounting
and exact source inspection. Features use only wholly contained unflagged windows.

AC RMS, log power 1–40 Hz and alpha share retain source/log/fraction units. Q1/median/Q3 use
linear interpolation over **all eligible windows**, not a clustering sample or an average
of bin medians. Selected-range summaries apply the same policy and count edge-crossing
windows separately. No per-day normalization is applied. Plotted points group continuous
runs by 60-second **window-start** bucket; whole windows remain whole, so each point reports
its actual source interval and count. Lines never bridge gaps, flagged windows or contexts.
RMS/log axes auto scale with minimum display spans; alpha share uses 0–1 to avoid magnifying
roundoff into an apparent change. Quartiles describe distributions, not confidence intervals.

Change cues compare 15 preceding and 15 following contiguous unflagged four-second windows,
inside one context and calendar day. For each feature the score is the absolute median shift
divided by the maximum of the two flank IQRs and a floor. RMS floor is 0.05 times
`max(abs(before median), abs(after median), 1)`; log-power floor is 0.05; alpha-share floor
is 0.02. Any feature score >=3 is a candidate. Candidates rank by strongest score, earliest
time and example ID; candidates within 120 seconds of an already retained stronger cue are
suppressed. Equal scores prefer earlier times. This is a frozen exploratory heuristic,
not a calibrated statistical test, exact transition detector, activity boundary or cause.

The report distinguishes insufficient continuous support, no above-threshold changes and
retained changes. Counts expose all evaluated boundaries, above-threshold candidates,
suppression and retention. A selected range shows the first 20 of M ranked retained cues
whose complete before/after flanks fit inside it; full-day screening status is explicitly
identified as full-day status. Inspection examples are real source windows closest to the
winning flank median. An ordinary hash-selected window and flagged example are separate.
Hash selection is conditional on eligible support, not a statistically representative sample.

The chart starts at the first observed collection and ends at the last, including after
16:00. Its embedded vertical edge handles preview selection while dragging and zoom on
release. Keyboard arrows plus Enter and explicit-offset ISO timestamps provide alternatives;
Escape cancels. Pinned bounds remain fixed on refresh, even if source extent changes.
Reset follows the latest observed extent. Automatic analysis refresh, while enabled and the
page remains open, runs at most once a minute and retains the previous analysis options.
Stale curves are withheld. Same-time activity annotations retain origin/review/timing,
unknown and ambiguous states; the context lane does not verify placement/task alignment.

`GET /api/discovery/daily` returns the scoped index, or one context/date/range with summaries.
Manifest and payload hashes, participant, context, date and source signature are checked;
results are withheld on staleness/corruption. The waveform endpoint accepts only an observed
window in the current context/date and exact daily identity, then reuses canonical source
record-digest verification. No nearest or nearby waveform is substituted. Raw waveform
units, mean/offset, flags and exclusive bounds are shown without filtering.

Manifest <=2 MB, day and selected-response payload <=16 MB, at most 1,000 contexts and
20,000 context/day entries; oversized output fails rather than silently clips. Publication
validates every payload before changing the complete pointer; cancellation/failure retains
the previous complete result and cleans partial daily files. New data, code or annotation
changes invalidate stale facts. Human-authored questions remain separate and preserved.
No default clustering, inferential estimate, activity classifier or packaged numerical
runtime is added. Cross-day recurrence is described below; numerical packaging is P5.

## Cross-day recurrence and recording conditions (P4-W3)

Open **Across days** from the dashboard or daily discovery. Choose an observed reference
window and either spectral ratios plus power or spectral ratios alone. A daily inspected
unflagged window can be sent directly to this page. No activity labels or clustering run
are required. Up to three observed examples per supported date are offered, deduplicated
and counted against that date's full eligible population; these are not a representative
sample. Any eligible daily-inspected window remains addressable.

`recurrence_model.py` owns full-population facts; `recurrence_access.py` serves them using
only the standard library. `spectral_features.py` owns the unchanged P3 coordinate formula:
log10 1–40 Hz power plus log10 delta/theta/alpha/high-band shares relative to beta, with a
1e-6 pseudocount. Existing P3 model/stability implementation keys include this owner.

Only wholly contained unflagged canonical four-second windows enter retrieval. Flagged
and midnight-crossing counts remain visible in the manifest. Compatible families may differ
only in explicit session ID. Participant, device, source, ear, sample rate, channel, clock
basis and units remain isolated; unknown sessions never become sessions inferred from gaps.
Cloud metadata remains right ear / RIGHT_TEMP / 256 Hz. Bluetooth-file ingestion is excluded.

Each family stores its full eligible population, feature ranges, median and IQR scale
(floor 1e-6), population revision and scaler identity. This is one full-snapshot scaler,
including discovery dates, rather than per-day normalization or held-out preprocessing.
Euclidean distance uses all five coordinates, or the four ratios with the power coordinate
removed. A view with every coordinate range <=1e-6 returns **uninformative features**, never
artificial perfect matches. Distances between different views are not comparable magnitudes.

Queries exclude the reference date and any other-date interval within 60 seconds of the
reference. All remaining windows are searched. The closest window per other date is selected
by distance, start and ID; the top 10 of M date candidates are returned. Counts reconcile the
full population, exclusions, other-date windows, shown and omitted dates. Full eligible
other-window distance Q1/median/Q3 and per-feature scaled differences/squared contributions
make rankings inspectable. One other date is explicitly limited support. There is no match
threshold, similarity probability, sequence recurrence, activity meaning or independent
replication claim. Changing the reference or view clears previous results.

The condition chart and table retain complete day/exact-session strata, not inferred
wearing sessions. They show recorded union/missing time, screening-state elapsed facts,
unflagged/flagged window counts and raw AC RMS, log-power, alpha-share, maximum-step, trend
and mains-like proxy quartiles. Whiskers are Q1–Q3, not confidence intervals. Conditions are
paged (25, maximum 100), with shown/total counts; the chart describes the current page.
Contact/impedance is unmeasured. Shared-feature differences cannot independently diagnose
contact, physiology, activity or cause; conditions may coincide with spectral changes.

Both reference and candidate source samples are fetched through the W2 sealed context/date/
window identity and canonical source-record digest verifier. No duplicate source-digest
owner is introduced. Raw unfiltered waveforms show source units/offset and share the same
amplitude axis once both are loaded. Their different times do not imply temporal alignment.

`GET /api/discovery/recurrence` returns the current manifest or a family/condition page;
`group`, current `identity`, `reference` and `view` add a ranked query. The waveform endpoint
also requires current identity and exact eligible membership. Sealed manifest/payload,
participant/context/scaler and before/after source signatures guard reads. Worker publication
validates all recurrence sidecars before changing the complete pointer; failed/canceled/
pruned jobs clean their sidecars and preserve the previous completed result.

Manifest <=2 MB, family <=96 MB, response <=2 MB, input <=1 million computed windows and
<=1,000 families; exceeding a bound fails explicitly. Nothing silently samples the population.
No raw/label writes, automatic assignment, default clustering, inference engine, public
release or numerical installer runtime are introduced. Numerical packaging remains P5.

## Selective review and optional context (P4-W4)

Open **Optional context**. It works with zero activity labels: ordinary windows are offered
first, without signal-feature selection cues or existing annotations. Opt into change and
cross-day examples to inspect why those windows were selected. This is a fixed small
selection, not an exhaustive labeling queue or statistical sample. Daily discovery and the
existing Episode review remain available for other windows and full activity intervals.

`discovery_review_model.py` owns deterministic selection in the isolated worker. From the
whole eligible W3 population it hash-orders windows inside exact-context/calendar-date
strata, then visits sorted strata round-robin. It offers up to eight ordinary examples,
eight W2 retained-change flank examples, and eight recurrence examples. Recurrence searches
use at most four diverse ordinary references, the unchanged W3 power-sensitive full-family
search, temporal guard and per-date minima. Uninformative or unsupported searches add no
recurrence examples. Categories interleave with ordinary first; duplicate windows retain
all selection reasons. Ordinary-only order remains independent of targeted ordering.

The page reports each category's full pool, selected and omitted totals, deduplicated offered
windows, the full eligible population, unoffered windows and offered windows hidden by the
ordinary-only choice. Selection reasons are provenance, not task labels. Dates, overlapping
examples and neighboring windows are not independent experiments. History-record counts
are separate from population review coverage; nothing claims unoffered data was reviewed.

The sealed review snapshot includes exact acquisition/time/window identity, source revision,
policy and method hashes, daily/recurrence identities and each selection reason. It contains
no human context. The stdlib reader validates identity and before/after source signature.
Exact source inspection reuses W2 source-record digest verification. Manifest <=2 MB; at most
24 distinct windows. Publication validates before moving the complete pointer; failure,
cancellation and pruning remove derived review sidecars, never human context.

`discovery_context.py` owns optional **human observations about the exact four-second source
window**. These bounds are not reconstructed task boundaries or longer activity assignments.
The choices remain distinct:

- Known activity: one existing participant-owned activity ID.
- Partial context: whatever is remembered, with optional personal IDs.
- Mixed activities: multiple personal IDs or a factual description of the mixture.
- Don't remember / unknown: an explicit lack of recollection, not pattern rejection.
- Skip: no recollection decision or signal rejection is inferred.

Up to five personal IDs are validated by the existing vocabulary owner and retained with
names as recorded. No universal activity menu or automatic vocabulary is introduced.
Archived activities must be restored before new use. Existing labels, schedule suggestions,
review decisions and activity assignments are never changed or confirmed by context entry.
To assign a longer activity period, use the existing interval editor explicitly.

Factual recollection and speculative explanation occupy separate fields (2,000 characters
each), independent of selection reason. The unchanged `personal_activities.label_evidence`
validator enforces review state, declared unknown/exact/approximate timing, and each side's
uncertainty in milliseconds. No precision is inferred from EEG or source timestamps.
Existing context requires explicit reveal before replacement; ordinary review does not
silently expose it. A save also records whether targeted cues were revealed.

Human records live in `ArchivePaths.discovery/context.sqlite3`, separately from deterministic
facts and the recording database. Saves validate current participant/snapshot/window, read
personal vocabulary in an archive snapshot and check source signature before committing.
A context-only save does not make EEG analysis stale. The source archive is opened read-only.
Current records and tombstones carry monotonically increasing revisions; append-only full
before/after history records explicit human save/undo times. CAS rejects stale and ABA edits.
Undo only affects the current revision, journals the restoration, and can itself be undone.
No prior provenance or timing is rewritten. History remains readable without current numerical
analysis; editing requires current offered membership, while undo can correct older context.

The saved-record list is paged (25, maximum 100). History is paged per window (25, maximum
100, response <=2 MB), with shown/total counts. JSON audit export streams **all** scoped
current records, tombstones and history in one read transaction; CSV streams all active
current observations, using the existing formula-safe text sanitizer. The JSON export is
an audit artifact, not an implemented context importer. Back up `context.sqlite3` or its
JSON export separately: the legacy recording-only SQLite restore does not import this
separate store. Installer upgrades/uninstall preserve it; derived-report cleanup excludes it.

Endpoints: `GET /api/discovery/review` (`cues=0/1`), `/review/waveform`, and
`GET /api/discovery/context` (saved list, or exact `windowId`, explicit `reveal=1`, history
`offset`/`limit`); `POST /api/discovery/context/save` and `/undo`; complete exports at
`/api/export/discovery-context.json` and `.csv`. Existing launcher authentication, selected
participant, same-origin and local mutation-header gates apply. No raw or label bulk writes,
mental-state recognition, inferential engine or numerical installer runtime are introduced.
