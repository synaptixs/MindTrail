# Interpretable feature explorer

Open **Recording quality → Open feature explorer**, or `/explore.html`. In the source
workspace, click **Analyze and explore**. The existing isolated worker extracts quality
features, runs the bounded [W2 comparison](EXPLORATORY_STABILITY.md), and saves view sidecars.
The quality page also offers an optional exploration checkbox. Existing quality-only jobs
remain compatible. Packaged apps include the page and an explicit unavailable state;
bundling the numerical runtime remains Phase 5.

The page starts **Ungrouped**. Choose an exact acquisition context and either spectral
ratios with log power or spectral ratios alone. Model settings are an explicit subsequent
choice. No cluster is named as an activity or enabled as a preferred method.

## PCA and features

`exploratory_views.py` owns projection, histogram, quartile and representative facts.
It reuses W1's validated acquisition/sample facts and W2's candidate assignments rather
than extracting another population in the browser. At most 2,000 windows per context/view
are shown. Counts reconcile quality exclusions, spacing omissions, budget omissions and
selection; extraction coverage remains separate. All context metadata, including **right
ear** for cloud recordings, is retained.

Each feature view uses its selected-snapshot median/IQR scaler (floor 1e-6), followed by
mean centering and full thin [NumPy SVD](https://numpy.org/doc/stable/reference/generated/numpy.linalg.svd.html).
The first two right singular vectors are the PCA axes; variance shares use squared singular
values divided by total squared singular values. This matches the usual
[PCA components/variance convention](https://scikit-learn.org/stable/modules/generated/sklearn.decomposition.PCA.html).
The largest absolute loading is positive, with feature order breaking ties. No random
layout, jitter or UMAP is used. Near-equal eigenvalues warn that axes are not uniquely
interpretable; constant/insufficient populations show an explicit unavailable projection.
Rank-one data is not turned into artificial two-dimensional separation.

The page exposes axes, explained variance, weights and scaler parameters. Distances describe
these features, not mental-state similarity or biological validity. Feature histograms use
the complete selected sample within the context, with 20 bounded bins and exact counts.
Raw power/share/RMS/step/trend/mains features retain their declared source units; hardware
microvolt calibration is not implied. Group quartiles are displayed beside observed examples.

## Observed examples and raw source

Representatives are actual selected windows closest to a subset's normalized feature
centroid. Ties choose the first chronologically ordered window. Ungrouped mode offers one
typical example. A model offers the top 12 of M groups by selected-window count, plus a
separate algorithm-unassigned example when available. The page states the limit. Every
selected window remains inspectable through a keyboard-accessible table, 25 rows per page,
or by selecting a plotted point. Colors can repeat; numbers identify unnamed groups.

`exploration_access.py` owns stdlib-only reads and exact waveform validation. The waveform
endpoint requires the displayed exploration identity, a current participant-scoped sidecar,
and a selected window ID. A read-only SQLite transaction fetches only that exact interval.
Record count, one-second cadence, device, participant, declared sample count and every
canonical `SourceRecord.digest` must match. Missing, edited, duplicate or incompatible data
is rejected; a nearby record is never substituted. Signature checks before/after reading
also withhold results when the source changes during navigation. Raw samples, source units,
offset/mean, sampling rate and exclusive time bounds are shown; the plot applies no filter
or centering. The EEG link points to the same source start time.

## Jobs, scope and payloads

`exploration` is an optional boolean in the existing start request. NumPy/scikit-learn
availability is checked before starting; the web server imports no numerical modules.
The numerical worker receives no API key, writes no raw/annotation data and checks cancellation
between fits/groups and while saving. Failure or cancellation preserves the previous complete
pointer; incomplete sidecars are removed. Job publication requires the optional manifest and
all context/view payloads. Existing leases, source-change guards and shutdown behavior apply.

Sidecars live beside their owning completed quality report under `ArchivePaths.quality`:

- `exploration-index.json`: participant/source identity, exclusions, contexts and payload hashes.
- `exploration-{context hash}-{with_power|spectral_only}.json`: at most 8 MB per view, with
  projection/feature facts, selected intervals, record digests, candidate diagnostics and examples.

The browser never loads the full raw quality report. Each view's content hash, participant,
context, view and analysis identity must match the manifest. Neither the API nor the browser
chooses filesystem paths. Existing desktop/local-request guards protect start/cancel actions.
Stale results are withheld; source/config/code and numerical-library versions participate
in freshness checks. Optional outputs follow existing per-participant/one-extra retention.

Deterministic identity includes the full frozen source/quality content, configurations,
algorithm code/environment, view implementation and candidate output. Timings and OS peak
memory remain job evidence outside deterministic facts. Cold and cached runs on pinned input
must match exactly. The predeclared full-archive budget is 180 seconds per cold/cached run,
1.5 GiB peak memory, and 8 MB per group/view payload.

## Acceptance and limits

Tests independently check covariance eigenvalues, score projection and axis orthogonality,
constant/rank-one/tied cases, histogram totals, observed group membership, repeated hashes,
read-only source preservation and exact-waveform rejection. Real process/HTTP tests cover
scope, local POST guards, corrupt/stale snapshots, cached reuse and retention; browser checks
cover selection, pagination, waveform verification, keyboard access and narrow layouts.
Native Windows validation checks installed assets and honest numerical unavailability.

Pooled recurrence and shared-training refit ARI remain exploratory; they are not independent
replication or activity accuracy. Session/contact limitations and power-ablation sensitivity
remain visible. [Similar-period retrieval](SIMILAR_PERIODS.md) is available in W4; confirmed-activity comparisons remain W5. This
increment does not select a default method or complete the Phase 3 evidence-review gate.
