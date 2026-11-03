# Confirmed activities across days and recorded sessions

Open **Recording quality → Compare confirmed activities**, or **Feature explorer →
Compare confirmed activities**, at `/compare.html`. Click **Analyze activities**. The
optional checked feature-explorer refresh preserves both outputs in the new completed
snapshot when scikit-learn is available. Activity comparisons themselves need only the
existing source NumPy environment. Existing quality-only and exploration-only jobs retain
their contracts; omitted optional branches show an explicit disabled state.

The default policy is **Confirmed only**, with origins shown separately. Choose a specific
manual/schedule/model/other retained origin or personal activity. Uncertain and needs-review
annotations have separate opt-in views; they never enter the confirmed summaries silently.
Stable participant-owned activity IDs and canonical definition names are used, including
archived definitions that still own historical labels. No universal activity list is added.

## Population and context

`activity_comparisons.py` runs in the isolated numerical worker and owns the full-population
facts. It uses **every computed quality window**, not the bounded day-balanced clustering
sample. Source record counts, extraction exclusions/remainders/failures, full quality
counts and annotation-assignment counts remain distinct. Invalid timestamps stay explicitly
unplaced. No waveform, label, definition, key or profile setting is written by analysis.

Contexts may be displayed together only when all canonical acquisition fields except an
explicit `session_id` match: participant, device, source, ear, sample rate, channel, clock
and units. Original acquisition group IDs and actual session IDs stay on separate strata
and rows. No feature distributions are pooled across those exact session contexts or
across activity origins/review states. Different devices/ears/sources stay in separate
selector entries. Cloud recordings retain **right-ear / RIGHT_TEMP / 256 Hz** provenance.

Current cloud extraction has no record-linked session IDs. These windows contribute to
daily support with session **not recorded**, never a fabricated session inferred from a
gap, date, folder or API session filename. Known-session summaries are emitted only for
explicit metadata. The page states known session support and unknown-session window counts.
A date is a descriptive grouping unit, not automatically an independent experiment.

## Interval eligibility and coverage

Annotation boundaries are swept over the analysis range. Explicit activity-unknown decisions
win over labels; overlapping labels are ambiguous. Neither population receives activity
features. Only one finished canonical activity may wholly contain a window. A window crossing
a label/decision boundary or local midnight is excluded from activity feature assignments
in full. Its recorded duration remains visible; features are never interpolated or split.
Open/invalid/unresolved-definition labels are counted as excluded annotation support.

Coverage uses the union of dated computed, failed, extraction-excluded and remainder source
intervals for the **exact acquisition context**. Duplicate records cannot inflate elapsed
coverage. Entered label spans are clipped to the selected analysis range and the observed
extent of that exact context, then split by actual local calendar boundaries (including
23/25-hour DST dates). Missing duration is within that denominator; it does not establish
when the device was worn. Per row:

- entered labeled duration = recorded union + missing duration;
- recorded union = fully labeled usable-window duration + fully labeled flagged-window
  duration + other recorded duration.

Other recorded duration includes extraction failures/remainders, label/day boundary windows,
and any source portions without an eligible complete feature window. Timing declarations
(exact/approximate/unknown) are counted from the owning labels for each day and session
stratum. Exact EEG timestamps do not turn unknown activity boundaries into exact timing.
Session-specific labeled denominators are not additive; usable minutes count source-window
duration, not deduplicated device-wearing time across simultaneous contexts.

All computed windows reconcile into assigned-usable, assigned-flagged, unlabeled,
activity-unknown, ambiguous and boundary categories. This accounting covers **all review
policies**; the selected policy's usable support is reported separately. A screening flag is
not a diagnosis, and an unflagged window is not proof of artifact-free EEG.

## Descriptive views and examples

The daily chart shows observed medians and within-day Q1–Q3 for AC RMS, log power 1–40 Hz
and alpha share, with declared source/fraction units. Quartiles use NumPy's explicit
[`method="linear"`](https://numpy.org/doc/stable/reference/generated/numpy.percentile.html)
convention. Each dot/whisker retains activity, origin, review state and exact session scope;
no line fills missing dates. Whiskers are distributions of windows, **not confidence
intervals**. Counts are not treated as independent trials. There are no p-values, activity
recognition probabilities, causal effects, mobile scores or preferred clustering method.

The API/browser page daily rows (25 by default, maximum 100), reporting shown/total and
support for the full filtered population separately. The chart describes the current page,
not every date invisibly. Explicit-session summaries state first N of M (maximum 25).
The selector uses canonical group counts to prioritize contexts with computed data.

Each visible activity/origin/review/session stratum offers at most three exact examples:

- an ordinary unflagged example selected by stable window-hash order, independent of feature
  ranking; it is not claimed to be a statistically representative random sample;
- an observed unflagged window closest to the stratum's RMS median (window ID breaks ties);
- a separately identified quality-excluded example when present.

Uncertain examples appear only under the separate uncertain policy. The existing feature
explorer remains available for unnamed model groups and feature-based representatives.
Examples link to their current Episode review interval; they never auto-confirm labels.

## Identity, bounds and lifecycle

`activityComparisons` is an optional strict boolean on `/api/analysis/start`. Worker output:

- `activities-index.json`: source/analysis/annotation/method/timezone identity, full coverage,
  contexts, counts and canonical context payload hashes (maximum 2 MB).
- `activities-{compatibility hash}.json`: exact contexts, strata, daily/session facts and
  source-digested examples (maximum 8 MB per compatible context).

Comparison identity includes quality identity, participant-scoped definition/assignment/
unknown/history revision, timezone and the owning implementation source bytes. Identical
pinned inputs reproduce the same facts and hashes. Runtime/memory remain job evidence.
Oversized output fails the optional job rather than publishing clipped data as complete.

`activity_access.py` is stdlib-only. `GET /api/activity-comparisons` reads a current scoped
manifest; a `group` query reads a content-hashed context and pages by `review`, `origin`,
`activity`, `offset` and `limit`. Wrong participant/group/content/identity and stale snapshots
are withheld. The server never imports NumPy to serve comparisons.
`GET /api/activity-comparisons/waveform` accepts only a current example window and comparison
identity. It delegates to W3's exact SourceRecord-digest verification, never nearby samples
or a browser-supplied path/time. Raw offset, units, flags and exclusive bounds remain visible.
Desktop authorization and existing host/local-start guards apply.

Publication validates the optional manifest/payloads before updating the current pointer.
Cancellation/failure preserves the last complete pointer and removes partial comparison
sidecars. Retention follows existing per-participant/one-extra rules. New raw data or
annotation/code changes mark results stale. The browser clears rows/examples while loading
a different policy and rejects late responses. Packaged apps include the page/stdlib reader
and honest unavailability; numerical dependency bundling and public releases remain P5.

## Evidence review and delivery boundary

The readiness panel reports limited per-activity days, missing common dates, missing sessions,
model-origin labels and imprecise timing. These are descriptive support checks, not proof
that an inferential model is identified. Impedance/contact conditions remain unmeasured.

W5 software delivery addresses P3-A4 and prepares the Phase 3 evidence review. P3-A5 and the
full phase gate remain open for independently interpreted examples, useful questions,
method/default-or-no-clustering choice and the justified scope of Phase 4. Software checks,
synthetic activity separation and a high silhouette score cannot pass that gate. No owner
acceptance or biological interpretation is fabricated.

Acceptance uses hand coverage/quantile oracles, >2,000-window full-population support,
participant/review/origin/acquisition/session isolation, overlap/unknown/boundary/DST cases,
no-label/no-usable states, deterministic bytes, real worker/HTTP/current-source/corruption/
publication/retention tests, browser keyboard/narrow layouts, a read-only private archive
pilot and native Windows package checks. Private data/evidence stays outside source control.
