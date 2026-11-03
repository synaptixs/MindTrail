# Feature extraction and quality screening

This P1-W3 source command reads one participant's cloud archive in a consistent,
read-only SQLite transaction. It emits non-overlapping feature windows, quality
screening cues, and reconciled coverage. It does not change raw samples or labels,
access keys, infer activities, run clustering, or start a desktop background job.

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
not a window ID alone, to identify those derived results. No cache is implemented yet.

Background jobs, cancellation, incremental feature reuse, dashboard coverage views,
full-archive performance budgets and complete Phase 1 acceptance remain pending.
