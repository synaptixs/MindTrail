# Analysis provenance foundation

This Phase 1 module defines acquisition/window identities and provides a read-only
cloud-source inventory. [Quality/feature extraction](ANALYSIS_QUALITY.md) now shares
the source snapshot through an optional record callback. Incremental feature caching,
background jobs and the coverage dashboard remain subsequent increments.
No existing clustering behavior, raw records or personal labels are changed here.

## Inspect an archive

```bash
python src/analysis_provenance.py /absolute/profile/awear_data.sqlite3 \
  --output /absolute/new-inventory.json
```

Use `--participant ID` to inspect a different participant. Otherwise the archive's
selected participant is used. An explicitly selected empty participant produces a
zero-record inventory; no default participant is guessed when none is selected.
The output path must not exist. Omit `--output` to print the JSON locally.

The inspector does not import the app, run schema setup, read a key, start syncing,
or access Bluetooth hardware. SQLite opens in read-only/query-only mode within a
single transaction. It supports older `readings` tables without `device_id` by
reporting unknown device identity, without migrating them.

## Source and processing identities

- `sourceRevision` hashes every selected persisted timestamp, device ID, declared
  sample count and waveform text in stable order, including invalid waveform text.
  Insertions, deletions and edits affect it even when aggregate counts do not change.
- Labels, cached mean/RMS values, and other participants are excluded. Future features
  must be computed from samples rather than trust the database's redundant statistics.
  Label-dependent comparisons need a separate label revision in their result identity;
  this raw-input identity alone must never be used to cache activity comparisons.
- `analysisIdentity` additionally includes the processing specification, contract/method
  versions, exact module source fingerprint and Python version. Dirty implementation
  edits invalidate the identity without depending on a clean Git commit.
- Absolute paths and database filenames are not part of the identity. The filename
  remains a display provenance field; moving an otherwise identical archive does not
  change the analysis identity.

This is a revision contract, not an incremental cache implementation. Future feature
implementations and numerical backends must extend the implementation/environment
fingerprint before their results are cached. The current source-file fingerprint is
for source execution; this inspector is not yet a bundled desktop feature.

## Acquisition and window contracts

Acquisition groups include participant, source, device, ear placement, sample rate,
channel, timestamp basis, session and units. Bluetooth requires an explicit session
identity; `device-timestamp` and `host-arrival` are distinct groups, without implying
verified sample-clock alignment. Current cloud provenance is RIGHT_TEMP, behind the
right ear, 256 Hz, based on the existing cloud-readings import contract.

A four-second window must contain four valid one-second records in exact UTC order,
with identical acquisition context and known device identity. Gaps, duplicate or
out-of-order times, source/participant/device/session/placement changes and invalid
waveforms are rejected. Local daylight-saving transitions do not change UTC continuity.
Start is inclusive; end is exclusive. Each window retains its source-record digests.

Optional programmatic range bounds select record starts in a half-open UTC range
and participate in its source revision. The optional `on_record` callback runs
inside the same read transaction as inventory hashing; the default inventory behavior
is unchanged. A failing callback aborts the inspection rather than publish partial counts.

The cloud inventory is not a waveform-validity or eligibility report: `recordCount`
includes invalid waveforms. It explicitly lists uninspected Bluetooth captures,
labels and mobile observations. Unknown device counts do not establish that records
come from one device. The separate quality/extraction report reconciles exclusions
and complete windows; no full Phase 1 acceptance gate is claimed complete yet.
