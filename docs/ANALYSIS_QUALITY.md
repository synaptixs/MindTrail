# Feature extraction and quality screening

This P1-W3 source command reads one participant's cloud archive in a consistent,
read-only SQLite transaction. It emits non-overlapping feature windows, quality
screening cues, and reconciled coverage. It does not change raw samples or labels,
access keys, infer activities, or run clustering. The source dashboard also runs this extractor as an isolated background job.

```bash
python -m pip install -r config/requirements/analysis.txt
python src/analysis_quality.py /absolute/profile/awear_data.sqlite3 \
  --output /absolute/new-quality-report.json
```

Choose another participant with `--participant ID`. Optional `--start` and `--end`
take timezone-qualified ISO 8601 values. They select record starts in a half-open
range; a final record extending past `--end` is excluded rather than clipped.
The output path must be new. Keep private results in the profile's
`data/processed/` or another private location, not in source control.

The CLI currently reads cloud data only. Its acquisition-aware core can accept
explicit source records, including separately scoped Bluetooth contexts, but no
Bluetooth-file adapter is supplied yet. The report does not claim complete
Bluetooth coverage. Source-only NumPy analysis is not yet a bundled desktop feature.

## Windows and acquisition boundaries

Four consecutive one-second records form a default window. Source provenance
retains participant, device, source, ear placement, session, channel, sample rate,
timestamp basis and units. Unknown device identity, invalid metadata/samples,
cadence gaps and acquisition changes prevent a window from bridging those records.

The cloud schema promises one record per participant/time. When an older or
malformed archive has duplicates, every record at that timestamp is excluded;
the extractor never silently chooses a device or a waveform. UTC continuity is
independent of daylight-saving changes in the local clock.

Unlike the existing pattern-labeling view, the new extractor does not break at
arbitrary half-hour slots: actual acquisition continuity determines windows.
The older clustering command retains its existing half-hour behavior and outputs.

## Spectral method and units

Linear detrending and a symmetric Hann taper precede the real FFT. One-sided
density uses squared FFT magnitude divided by sample rate times summed squared
taper values; non-DC/non-Nyquist bins are doubled. Band power integrates this
density using bin width. This follows the documented
[periodogram density convention](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.periodogram.html).
Tests compare against an independent SciPy periodogram for even/odd sample counts.

Bands are 1–4, 4–8, 8–13, 13–30, and 30–40 Hz. The shared last upper guard is
40.01 Hz for compatibility with old FFT-bin selection; supported integer-duration
windows include the 40 Hz bin but not bins above it. Shares use total 1–40 Hz power
as denominator. Zero-power signals have zero shares rather than fabricated fractions.

RMS, peak-to-peak and adjacent-sample differences retain source units. Spectral
power uses source units squared; density uses source units squared/Hz. Hardware
voltage calibration is unavailable, so no microvolt conversion is implied.
Log power is log10 relative to one source unit squared, with an explicit 1e-12 floor.

`power_spectrum` owns the shared spectral primitive. The old pattern feature wrapper
continues using its original unnormalized FFT sums; synthetic baseline vectors
verify backward compatibility. The new quality features use normalized density.

## Screening policy

The full policy and version are included in every report. Defaults are review cues:

| Flag | Rule |
| --- | --- |
| `flatline` | Raw peak-to-peak at most 1e-6 source units |
| `mains_like_power` | Power near 50/60 Hz exceeds 25% of 1–100 Hz power, limited by Nyquist |
| `linear_trend_dominant` | Fitted linear-trend variance exceeds 80% of centered raw variance |
| `relative_high_amplitude` | RMS exceeds acquisition-group median plus 8 robust MAD scales |
| `relative_large_step` | Largest adjacent-sample difference exceeds its group-relative cutoff |

Relative screening needs at least eight computed windows by default. Otherwise
the report marks relative screening unavailable; absolute/fraction cues still apply.
A numerical floor prevents a zero MAD from creating undefined cutoffs. These choices
are configurable programmatically through `QualityPolicy`, not clinically validated
thresholds. Flags can overlap and do not remove the original feature values.
Unflagged windows are not proof of artifact-free EEG. Clipping is not screened
without known acquisition limits.

`--normalize` adds robust deviations for RMS and log power. References are fitted
only to unflagged windows within the exact acquisition group, with counts and
median/scale values exposed. If no reference exists, normalized values are null.
These are exploratory snapshot references, not training-fold preprocessing; future
prediction must fit its references on training data only.

## Coverage and traceability

The following disjoint record accounting must hold:

```text
source records
  = excluded records
  + computed window record-seconds
  + numeric-failure window record-seconds
  + incomplete/boundary remainder record-seconds
```

Each input record represents one declared second. Exclusion counts are records,
not unique elapsed seconds: two duplicates occupy the same timestamp. Exclusions
have one primary reason per record. `excludedSpans`, `remainderSpans` and failed
window provenance retain interval/reason/acquisition context; an invalid timestamp
has null interval bounds rather than an invented time.

Computed windows partition into flagged and unflagged windows. Flag occurrences
may exceed flagged-window totals because reasons overlap. Missing-time estimates
are separate: `unrecordedSpanMilliseconds` measures gaps between observed record
starts, not wearing time, and does not extrapolate before/after the observed span.

## Identity and current limits

Input revision and feature extraction consume the same snapshot through the
provenance inspector's optional record callback. New arrivals cannot mix an old
inventory with new feature windows. Bounded input ranges affect the source revision.

Report identity includes the source revision, specification, policy parameters,
both implementation-file fingerprints, NumPy version and Python version. It is
deterministic within the pinned numerical environment. This also accounts for
uncommitted code changes instead of trusting only a Git commit ID.

A window ID identifies its local input/configuration. Quality flags and normalization
also depend on the group's reference population: use the report's analysis identity,
not a window ID alone, to identify those derived results. Raw numeric features can be cached by window ID. Quality flags and normalization references are always recalculated for the complete selected snapshot.

See [Phase 1 acceptance](PHASE1_ACCEPTANCE.md) for full-archive validation and resource budgets. Bluetooth-file ingestion remains separate work.


## Source-workspace background analysis

Open **Recording quality** in the source dashboard and click **Run analysis**.
The worker runs separately from the HTTP server. Stage progress counts records
while extracting and acquisition groups while screening; it is not an estimated
whole-job percentage. Cancel stops publication and preserves the last complete
report. Closing the server cancels its own worker. After a crash, its heartbeat
expires in 15 seconds and the next launcher offers a retry; no stored PID is killed.
A fresh lease prevents another launcher from taking over an active job.

Outputs use `ArchivePaths.quality`: `data/processed/quality/`. Each private job
folder contains a request, progress, complete summary/report or safe failure
status, and a local diagnostic log on failure. The worker does not read `.env`,
receive the API key environment variable, synchronize, or access Bluetooth.
Requests are scoped to the server's selected database and participant; clients
cannot choose filesystem paths. Desktop cookie, host, origin and local-request
header checks apply to the new API routes. Setup changes wait for active analysis.

`analysis_cache.py` stores raw feature dictionaries in a content-addressed SQLite
cache. Checksums, finite-number/schema validation and implementation/configuration
hashes reject incompatible entries. Every run scans the read-only source snapshot,
so edited/deleted historical rows are detected as well as new arrivals. A cadence
change can shift subsequent windows and legitimately invalidate many entries.
Cache hits and runtime progress are job metadata, outside deterministic reports.
A warm run must produce exactly the same canonical report as a cold run.

A successful process exit and complete report/summary are required before the
manager atomically changes the current pointer. Failed/canceled jobs never replace
it. Database/WAL stat signatures, Python/NumPy versions and relevant source-file bytes conservatively
mark freshness; they are captured before the job starts. Changes during a job
leave its completed snapshot marked stale. Changes unrelated to EEG may also mark
it stale; a rerun verifies the exact revision and reuses valid features. Report
identity is the authoritative result identity, not the cheap freshness signal.
The dashboard exposes full-history analysis; optional bounded ranges are available
through the start API and CLI. Report settings are returned with status. Automatic
updates are opt-in, at most once a minute; failures/cancellation require an explicit
retry or re-enabling updates. This work does not download new records itself.

The coverage view combines computed intervals as an elapsed-time union, with
flagged intervals taking precedence where they overlap. It does not turn record
counts into wearing duration. Time with no computed interval includes exclusions,
incomplete windows, failures and gaps. Invalid timestamp exclusions remain unplaced.
At most 1,500 time bins cover the entire observed extent, with wider bins for long
archives. The sections show proportions within each bin, not precise gap positions.
The summary retains up to 200 acquisition-group details with full group totals;
full JSON retains all windows, groups and accounting. Time geometry uses epoch
milliseconds; local labels include timezone offsets across daylight-saving changes.

The manager retains each participant/database's current full report plus one extra
recent payload, and all small job summaries/status records. Only generated
`jobs/<validated-id>/report.json` payloads are pruned after success; downloaded/user
exports and raw data are untouched. Long-term cache/metadata cleanup remains longitudinal-release work;
[Phase 1 acceptance](PHASE1_ACCEPTANCE.md) bounds the tested reference resource envelope.

This increment enables numerical jobs in the **source workspace** with NumPy.
Packaged apps include the page and stdlib lifecycle API but show analysis as
unavailable. Bundling/validating the numerical runtime remains release work;
these changes do not claim new analysis functionality in current public installers.

Worker-generated JSON uses canonical key ordering, so cold and cached reports are
byte-identical in the pinned numerical environment. OS peak memory is exposed as
`peakMemoryBytes` in completed job status, outside the deterministic report.
