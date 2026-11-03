# Explainable similar-period retrieval

Open **Feature explorer → Find periods like a confirmed activity** after **Analyze and
explore**. Choose a selected four-second window contained by one confirmed, finished
activity, or use **Find similar** beside a selected window. The reference menu shows
personal activity names, source times and timing precision. No reference is created or
confirmed automatically. With no eligible confirmed reference, the page explains how to
review an activity first. An open activity, partial containment, overlapping annotations
or an explicit unknown decision cannot serve as a reference.

## Distance and population

The worker exports the canonical `exploratory_models.feature_vector` as `modelFeatures`
for each selected window. `similar_periods.py` consumes these facts, never the two PCA
coordinates or a renderer's reconstruction of the data. This is bounded exact
[nearest-neighbor retrieval](https://scikit-learn.org/stable/modules/neighbors.html),
implemented with the standard library; no new fit or dependency runs in the HTTP handler.

For each full feature dimension, `scaledDelta = (match - reference) / IQR scale`.
Distance is `sqrt(sum(scaledDelta²))`. The scaler is the selected snapshot/view's IQR
(floor 1e-6), already owned and exported by the numerical worker. Centering cancels in
pairwise differences. Spectral ratios + log power uses five dimensions; spectral-only
uses four. A missing/invalid vector or scaler requires reanalysis. A constant-feature
population can legitimately produce zero distances; zero squared-distance shares are
reported as zero. Ties sort by UTC start then window ID. No probability, classification
confidence, threshold-based activity recognition, mental-state or biological claim follows.

Candidates are the same bounded unflagged, day-balanced sample as the feature explorer,
within the exact participant/device/source/placement/channel/rate/session/units context.
The page retains cloud **right-ear** provenance, acquisition and session limitations,
quality accounting, sampling omissions and feature-view choice. Quality-screened does
not mean artifact-free. Distances across feature views are not interchangeable.

The entire reference activity, plus 60 seconds before/after it, is excluded. Half-open
intervals touching the guard end are eligible. Ranked results are greedily thinned to
leave at least the chosen 1/5/15-minute interval gap (default five minutes) between matches.
All candidates are processed before clipping to ten. Counts reconcile:

- selected = reference/guard excluded + eligible;
- eligible = spacing suppressed + spaced candidates;
- spaced candidates = shown + limit omitted.

“Top N of M” refers to this explicitly thinned population, not every recorded second.
Spacing reduces repetitive examples; same-day matches are not independent experiments.
The nearest result is returned even when all matches are distant: distance is a ranking,
not proof that an activity recurred. Each result shows exact exclusive bounds, existing
labels/origin/review/timing, unknown/protection state, screening status and every feature's
reference/match value, difference, scale, scaled difference and squared-distance share.
The existing exact-digest waveform endpoint inspects the same selected source window.

## Explicit human adoption and undo

**Review activity suggestion** opens a form for that exact four-second window. The
reference activity name is editable, and confirmation is unchecked. A person must review
what they actually performed and explicitly save. This does not extrapolate an activity
across minutes, gaps or adjacent matches. Use Episode review to edit a longer interval.
Activity timing defaults to unknown: precise EEG timestamps do not establish precise
activity boundaries.

`POST /api/similarity/search` reads a current scoped, content-verified view and a read-only
SQLite annotation snapshot. It returns reference readiness or ranked matches. No mutation
occurs. `POST /api/similarity/accept` requires the selected participant, exploration identity,
reference window, spacing, target window, exact proposal fingerprint and `reviewed: true`.
Client-supplied dates, distances and activity IDs cannot select an arbitrary interval.
Both endpoints use existing host/origin/local-header/desktop guards.

Acceptance recomputes the proposal inside `add_label`'s `BEGIN IMMEDIATE` transaction,
before any activity resolution or mutation. Its identity includes reference label/history
version, acquisition/view/configuration, target values and current annotation/unknown state.
ABA edits/undo, changed names, reference removal or altered target annotation invalidate it.
The existing selected-participant, unknown and protected-overlap checks remain in force.
A pending schedule/model suggestion may be split around the reviewed window; confirmed,
uncertain or manual labels and explicit unknown decisions cannot be overwritten here.
There is no bulk action.

Adopting the reference activity preserves `source=model` with `review_state=confirmed`;
entering a different personal activity records `source=manual`. The append-only episode
journal retains retrieval version, source identity/context, reference/version, spacing,
guard, metric and full feature explanation. Existing history/undo restores affected
suggestion fragments and preserves unrelated annotations. Existing labels/API callers
retain their default manual origin. No schema migration is needed.

**Current freshness limitation:** all label writes conservatively invalidate numerical
analysis through the existing database signature. After saving, the page retains a success
notice and directs the person to reanalyze before another search. It does not silently
advance a source signature or reuse an unverifiable snapshot. View/context changes and
staleness clear results/drafts; generation guards discard late responses.

## Delivery boundary

This W4 increment implements P3-A3's retrieval and explicit-review software scope. W5
confirmed-activity comparison and the Phase 3 evidence-review/default-method decision remain
open. Numerical generation is still source-workspace only until Phase 5 dependency bundling;
Mac/Windows packages include the UI and stdlib readers with honest unavailability.

Acceptance covers independent hand-calculated distances, full-space/PCA counterexamples,
stable ties, interval guards/counts, reference readiness, stale/source/participant/ABA
rejection, HTTP guards, human confirmation, model/manual provenance, pending-suggestion
split/undo and protected unknown/activities. W3's worker/content/source tests remain active.
Browser checks use a clearly synthetic isolated profile; private pilot measurements and
personal source data remain outside the public repository.
