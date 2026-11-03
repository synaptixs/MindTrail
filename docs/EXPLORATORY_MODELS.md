# Frozen exploratory model comparison

P3-W1 provides a source-only comparison command. It consumes a completed Phase 1 quality
report, preserves the archive and annotations, and does not replace the older Patterns
dashboard. Charts, sensitivity/recurrence, similar-period retrieval and confirmed-activity
comparisons are later P3 packages. Numerical installer bundling remains Phase 5.

```bash
python -m pip install -r config/requirements/analysis.txt
python src/exploratory_models.py /absolute/private/quality-report.json \
  --output /absolute/private/new-comparison.json
```

The input is `report.json` from a completed source-workspace quality job, or output from
`analysis_quality.py`. Outputs must be new files. Keep actual participant reports outside
the public source checkout. No credentials, network, app startup or database migration are
used. The report is a frozen derived snapshot: its recorded source revision does **not**
establish that a live database has stayed current. Re-extract quality after source changes.

## Frozen population and features

`config/exploration.json` declares the bounded protocol before fitting. The complete quality
report is content-hashed, including policy/specification/source identity. Duplicate IDs,
overlapping intervals within a group, mixed participants, mismatched acquisition or quality
identities, nonfinite features and unreconciled window counts are rejected before fitting.
This validates the supplied report contract, not the original waveform values independently.

All flagged windows are excluded, and the original extraction coverage is retained. Flags
are screening cues; unflagged does not prove clean EEG. Each exact acquisition group has its
own sample, scaler and fits: participant, source, device, placement, channel, sample rate,
session, timestamp basis and units cannot silently mix. Cloud placement remains **right ear**.
Bluetooth files are not ingested by the current cloud extractor.

Five features use canonical extracted values: log10 1–40 Hz power and four additive log10
band-share ratios (delta, theta, alpha, 30–40 Hz relative to beta). A declared `1e-6`
pseudocount handles zero shares. Log ratios avoid including five shares that sum to one as
five unconstrained coordinates. This feature choice is exploratory, not a validated state
representation. Median/IQR scaling, with a `1e-6` floor, is fitted separately on each selected
group snapshot; parameters are exported. No held-out accuracy is claimed.

## Sampling and honest bounds

First, retain chronologically greedy windows at least 60 seconds apart by start time. Then
allocate at most 2,000 slots per acquisition group across New York dates in round-robin order
and evenly spread each date's slots through its remaining windows. This is deterministic
day-balanced sampling, **not duration weighting or statistical independence**. Adjacent windows
are reduced, but serial dependence remains. Only selected windows receive assignments.

Every group reconciles:

```text
computed windows = quality excluded + spacing omitted + budget omitted + selected
```

The output includes all selected window IDs and exact intervals, day counts, context and
candidate assignments. Model-unassigned windows are separate from quality exclusions and
unsampled windows. Input is bounded to one million windows and 16 acquisition groups; larger
inputs fail with an explicit error. Override with `--config` only within enforced ceilings.

## Candidate methods

| Method | Frozen comparison | Meaning |
| --- | --- | --- |
| HDBSCAN | Minimum group sizes 15, 30, 60; minimum samples 10; one cluster allowed | Density groups and unassigned windows |
| Gaussian mixtures | 1–6 diagonal components; seed 42; 3 starts; 200 iterations | In-sample BIC and convergence flag |
| K-means | 1–6 groups; seed 42; 10 starts; 300 iterations | Inertia baseline; no automatic choice of k |

Scikit-learn's [HDBSCAN](https://scikit-learn.org/stable/modules/generated/sklearn.cluster.HDBSCAN.html)
supports a single cluster and labels noise separately. Its `min_samples` includes the point
itself. [GaussianMixture](https://scikit-learn.org/stable/modules/generated/sklearn.mixture.GaussianMixture.html)
supports the one-component candidate and exposes convergence/BIC diagnostics. These values
cannot establish activity accuracy or biological meaning. No method is enabled as a default
and no signal group is named as an activity. No membership-to-activity probability is emitted.

Small groups and constant feature populations produce explicit statuses without forcing
two groups. Component counts are also limited by distinct selected feature rows. Insufficient
support, all noise, one group or no useful grouping are valid outcomes.

## Reproducibility and acceptance boundary

Comparison identity hashes the full deterministic output, module/provenance/quality source
bytes, exact config, input content and numerical environment (Python, NumPy, SciPy,
scikit-learn and numerical threadpool details). Dirty code invalidates the identity.
Numerical libraries are limited to one thread while fitting. Repeatability is byte-exact
within a pinned environment; cross-platform floating-point differences require tolerances.
Wall time and OS peak memory belong to separate experiment metadata, outside this identity.

Synthetic checks cover clearly separated populations, constant/small/empty groups, density
noise accounting, acquisition/participant isolation, source preservation, deterministic
sampling and repeatability. The real frozen-archive pilot is bounded in advance to 120
seconds and 1.5 GiB peak memory. It is a software/resource check, not a method endorsement.
The [P3-W2 stability command](EXPLORATORY_STABILITY.md) assesses parameter sensitivity,
cross-day recurrence and session/contact confounds without choosing a default. The full
Phase 3 acceptance gate remains open.
