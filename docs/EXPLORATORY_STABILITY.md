# Stability, recurrence and recording-condition diagnostics

P3-W2 extends the source-only frozen-snapshot workflow. It emits a local JSON report;
dashboard views follow in P3-W3 and numerical installer bundling remains Phase 5.
No default algorithm, activity interpretation, label changes or API credentials are involved.

```bash
python -m pip install -r config/requirements/analysis.txt
python src/exploratory_stability.py /absolute/private/quality-report.json \
  --output /absolute/private/new-stability-report.json
```

Input is the same canonical completed quality report used by
[P3-W1](EXPLORATORY_MODELS.md). `--sampling-config` selects its bounded sampling settings;
`--config` selects `config/exploration-stability.json`. Existing output files are refused.
All input/quality/acquisition validation, exclusions, sampling omissions and identities
are reused from W1. This is a frozen derived snapshot, not a live-source freshness claim.

## Frozen sensitivity protocol

The same selected intervals are evaluated in two views: five features with log power,
and four spectral ratios with power removed. No refit silently resamples the input.

| Method | Candidate settings in each feature view |
| --- | --- |
| HDBSCAN | Sizes 15, 30, 60 × minimum samples 5, 10 × single cluster allowed/forbidden |
| Gaussian mixture | 1–6 diagonal components × seed 42, 43; 3 starts, regularization 1e-4, 200 iterations |
| K-means | 1–6 groups × seed 42, 43; 10 starts, 300 iterations |

Seeds start at the W1 configured seed; the second is its successor modulo 2^32. The W1
maximum-component setting may narrow the count range. There are at most 36 candidates per
view. Each exports its exact parameters, status, convergence, label vector (aligned to
`selectedIntervals`), group sizes, unassigned fraction and BIC/inertia where applicable.
Infeasible counts, small or constant populations are explicit skipped statuses. Nonconverged
fits are retained for diagnosis but excluded from agreement calculations and transfer.

Parameter contrasts use fixed reference settings, **not a winning model**: HDBSCAN 30/10
with one cluster allowed, or two-component/group GMM/K-means at the first seed. Contrasts
against one-group candidates remain visible but have no partition-stability score.
With/without-power comparisons align the same candidate settings and source windows.
A large change is a feature-dependence cue; it cannot establish bad contact or a brain state.

## Temporal and day refits

Seven fixed anchors per feature view receive population perturbations: HDBSCAN 30/10/single
allowed, and GMM/K-means with 2, 4, 6 components/groups at the first seed (when configured).
Every fit learns its median/IQR scaler on its supplied training subset only.

- Remove UTC-aligned 30-minute blocks whose block index modulo four is 0, then 1. These are
  two deterministic perturbations, not exhaustive resampling or independent experiments.
- Leave out at most three New York dates, evenly spaced through the selected dates. The
  report states available/evaluated/omitted dates; three evaluated dates cannot imply all
  dates were tested.
- A window touching an excluded block/day is excluded in full, including windows crossing
  midnight or a block edge. Nothing is split or moved. An exclusion removing no selected
  windows has an explicit no-op status and cannot contribute a successful stability score.

Refit agreement compares the full fit with the refit **on their shared training windows**.
[Adjusted Rand index](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.adjusted_rand_score.html)
is invariant to cluster numbering. Here it is computed only on jointly assigned windows,
with at least 20 of those windows and at least two observed groups in each partition.
Otherwise the value is null with a reason. All-noise and single-group agreement cannot
manufacture perfect stability. Joint support, separate unassigned counts and assigned-set
Jaccard are always reported. There are no random-window confidence intervals or p-values.

## Cross-day evidence and transfer

Each unnamed pooled group reports counts and fractions by selected day, sampled duration,
days present, days with at least five windows, and maximum day concentration. Counts are
conditional on the W1 day-balanced sample, not full recording duration or independent
replication. Per-day unassigned counts expose density coverage differences.

Leave-day-out GMM reports training/excluded-day mean log density and the fraction below
the training fifth percentile. K-means reports excluded-day distances and the fraction
beyond the training 95th-percentile distance. All scaling and these reference cutoffs use
training rows only, following the
[training-only preprocessing principle](https://scikit-learn.org/stable/common_pitfalls.html#data-leakage).
HDBSCAN gets an explicit unsupported out-of-sample status; no invented centroid predictor
is substituted.

These are distribution-transfer diagnostics, **not activity accuracy or independent
confirmation**. The dates have already been inspected. Quality screening and sample selection
were frozen using the full snapshot, so this is not a wholly training-fold-discovered
pipeline. Log densities/BIC across different feature dimensions are not comparable scores.

## Recording-condition limits

- Exact participant/source/device/placement/session/channel/rate/clock/unit contexts stay
  separate. Current cloud data retains **right-ear** provenance.
- Session is reported as unknown when absent; an explicit session is constant within its
  exact acquisition group. Neither case identifies session effects within that group.
  Dates or time blocks are not relabeled as sessions.
- Contact impedance is unmeasured. RMS, log power, maximum step, trend fraction and mains-like
  fraction are descriptive proxies, not verified contact/quality measurements.
- Reports include proxy medians per group, descriptive between-group variance fractions,
  and day adjusted mutual information when both assigned groups and days vary. These are
  exploratory associations; shared-feature separation is circular evidence for biology.

## Reproducibility, bounds and acceptance

Identity includes full quality content/source identity, sampling/stability configs, exact
source bytes for the four owning modules, numerical environment and deterministic output.
Fits run with one numerical thread. Identical pinned inputs reproduce exact bytes; platforms
may need floating tolerances. Timing and OS peak memory are separate experiment metadata.

At default settings each acquisition group has at most 2,000 selected windows and 142 planned
fits (72 full fits plus 70 anchor refits); skipped/no-op fits remain explicit. W1's limits of
16 acquisition groups and one million input windows still apply. The private frozen-archive
pilot declares 180 seconds for two complete runs and 1.5 GiB peak memory before execution.

Synthetic acceptance covers known recurring groups, day-confounded groups, power-only groups,
training-only scalers/transfer, block/day boundary exclusion, no-op exclusion, noise/single
partitions, source preservation and repeatability. Completing these software checks does
not select a default method. P3's final evidence review and later views/retrieval/activity
comparisons remain open; absence of useful stable clusters is a valid result.
