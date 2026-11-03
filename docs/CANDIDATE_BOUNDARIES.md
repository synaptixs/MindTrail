# Provisional candidate periods (P2-W4)

Candidate periods are optional review aids in the **source workspace**. They do not
identify an activity, mood, artifact or clinical state. Their usefulness on personally
annotated examples remains an owner-review gate in P2-W5. Bundled numerical analysis
remains Phase 5 work; packaged apps continue to report it unavailable safely.

## Review flow

1. In **Recording quality**, expand **Optional candidate periods**, enable calculation,
   set a minimum period (12–1800 seconds) and low/medium/high sensitivity, then run analysis.
2. On any daily graph, expand **Optional candidate periods**. Compare PELT and the simple
   split baseline. Purple dashed lines mark internal proposed changes in the visible range.
3. **Inspect** pins a proposed interval. Use the existing handles or precise time controls
   to adjust it, then **Assign activity to selection** and explicitly save your own activity.

Inspecting a proposal performs no label write and does not split, move or confirm existing
labels. Conflicting saved activities retain their normal protection. Saved manual activity
and timing declarations remain the person's observations; the candidate JSON is separate.
All periods are ordered by time, initially first 25 of M with **Show 25 more**. Full JSON
contains both methods, all runs and periods; no omitted result is implied to be complete.
The review queue, rejection, undo and candidate-to-label evidence linkage remain W5 work.

## Reproducible method

`src/candidate_boundaries.py` implements `mean-change-v1`, without another dependency.
It consumes the canonical four-second quality windows, never labels or a work schedule.
Only unflagged computed windows enter the input. Unflagged does not establish artifact-free
EEG. Gaps, flagged windows, changed acquisition groups and New York calendar days break runs.
A window straddling midnight is excluded entirely rather than assigned invented subwindow
features. Fall-back hours retain their actual UTC instants. Failed/excluded records and
incomplete windows remain outside the computed-window counts; the quality report accounts
for them. Runs shorter than the minimum are counted separately and propose no period.

Each continuous run uses four features: log10 1–40 Hz power and theta, alpha, beta power
shares. Each is centered at its run median and divided by its IQR, floored at 0.1 for log
power and 0.02 for shares, then clipped to ±10. References are exploratory run-local
references, not training folds. Band shares are correlated; equal feature weighting and
these floors are explicit prototype choices, not a fitted or validated physiological model.

At most 1,200 sequential bins per run are searched. Bin size is `ceil(windows/1200)`;
the final bin can be shorter. Bins average the transformed features and carry their original
window counts as weights. Prefix sums yield weighted multivariate squared-error segment
costs. Within-bin variance is a common omitted constant, so reported objectives refer to
binned values. Original untransformed period means are supplied separately for inspection.
Actual UTC edge differences enforce the minimum duration, including the shorter final bin.
Search resolution is the maximum bin duration and is exposed on every proposed period.
PELT is exact **on this restricted bin grid**, not on every raw sample or possible timestamp.

Both methods use the same heuristic penalty:

`4 * log(max(2, run window count)) * multiplier`

Multipliers: low 3, medium 1.5, high 0.5. Higher sensitivity lowers the cost of extra
changes. These are not significance levels or calibrated confidence scores. No change
is a valid result. A constant run does not get forced into multiple groups.

The baseline greedily splits a segment at the largest SSE improvement only when it exceeds
the penalty. PELT minimizes total SSE plus the penalty per internal change. For SSE, the
pruning constant is zero; pruning is deferred until the replacement segment also meets the
minimum duration. Strict pruning with a numerical tolerance preserves tied candidates.
No random seeds, clocks, LLM calls or external uploads participate in derived output.
Worst-case PELT remains quadratic; no unconditional linear-runtime claim is made.

Method reference: [Killick, Fearnhead & Eckley (2012)](https://arxiv.org/abs/1101.1438).
The independent exhaustive small-series oracle tests the constrained objective, including
unequal weights and irregular durations; it does not reuse the production cost helper.

## Ownership, identity and lifecycle

Optional `candidateBoundaries: {minimumSeconds, sensitivity}` is part of the validated
analysis request and completed pointer settings; null/off preserves the old default.
The worker writes `boundaries-YYYY-MM-DD.json` and `boundaries-index.json` before publishing
completed status, alongside the unchanged canonical quality report and lane sidecars.
Candidate identity hashes the quality identity, exact settings, method version and algorithm
source bytes. Sidecars carry participant, acquisition, source identity, scaling references,
counts, resolution and both methods. The worker signature includes algorithm source bytes.

`GET /api/daily-boundaries?date=YYYY-MM-DD` uses the selected participant and existing desktop
HTTP security gates. It validates bounded day payloads (8 MB), source/scope/settings and
segment geometry. Missing, disabled, stale or invalid snapshots return an explicit status
with no periods. Source changes are checked before and after reads. A completed prior
snapshot is retained through failed/canceled jobs; incomplete and pruned sidecars follow
the existing report retention lifecycle. New records invalidate proposals; they never
adjust a pinned chart or saved human interval.

## Acceptance boundary

Software checks establish deterministic output, constrained optimization, scope separation,
accounting, cancellation and preservation. They do not establish that a signal change is a
meaningful activity boundary. Contact, motion and acquisition differences can still drive
changes after screening. P2 remains open for the owner's labeling/undo/queue walkthrough
and annotated-example usefulness review; detection can remain disabled if it adds little value.
