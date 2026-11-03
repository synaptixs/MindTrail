# Phase 1 acceptance: data and quality foundation

Phase 1 establishes local **cloud EEG** analysis in the source workspace. Its
acquisition-aware core is tested across participant, device, source, placement,
session, rate and timing boundaries. Bluetooth-file ingestion and numerical-runtime
bundling remain explicit later work; neither is implied by a cloud coverage result.
Cloud provenance remains behind the **right ear**, RIGHT_TEMP, 256 Hz. Screening
cues do not establish artifacts, activities, emotions or clinical states.

## Repeat the checks

Use the source Python environment with the optional analysis requirements:

```bash
python -m unittest discover
python src/analysis_acceptance.py /absolute/profile/awear_data.sqlite3 \
  --output /absolute/new-private-acceptance-folder
```

The output directory must be new. The runner opens the input read-only and backs
it up into a private reference profile. All intentional edits, deletions and new
arrivals happen on a second disposable copy. It starts no syncing, reads no API
key and uploads nothing. Keep its evidence outside the checkout: it contains
personal source metadata and complete derived-window reports.

The runner covers every participant represented in the cloud table. It checks
SQL counts, disjoint record accounting, every computed interval against source
timestamps/devices, whole-extent timeline bounds, and exact cold/cached JSON bytes.
It also compares cached analysis after source edits/deletion/arrival with a fresh
full run on that same changed snapshot. A failed oracle or unobserved cancellation
stage stops acceptance and retains partial evidence; it cannot silently pass.

`coverage.html` is a private, self-contained review report covering the entire
observed extent. `acceptance.json`, `protocol.json` and per-job status/JSON provide
measurement and provenance evidence. The HTML and report are not release artifacts.

## Performance envelope declared before measurement

[The versioned budget](../config/analysis-acceptance.json) applies per participant
to a reference of at most **300,000 cloud records**, with the default four-second
windows and robust within-acquisition deviations enabled:

| Check | Ceiling |
| --- | ---: |
| Cold or changed-source fresh run | 180 seconds |
| Repeated or changed-source cached run | 120 seconds |
| Worker peak resident/working-set memory | 1.5 GiB |
| Navigation p95 / maximum | 1 / 3 seconds |
| Cancellation through worker exit | 3 seconds |

The product does not truncate history at 300,000 records. This is the validated
performance envelope, not a source-data limit. Before expanding the release
reference beyond it, revise the protocol explicitly and measure again. The runner
rejects an out-of-envelope reference before starting numerical jobs.

Memory comes from the worker OS high-water measurement: `getrusage` peak RSS on
Mac/Linux and `GetProcessMemoryInfo` peak working set on Windows. Measurements are
job metadata, outside deterministic analysis identities and reports. They do not
include the browser, acceptance verifier or other processes. Runtime includes
worker start, extraction, screening, serialization and manager publication.
Navigation repeatedly loads the actual local quality page during the job.
This checks service responsiveness; it is not a browser frame-rate benchmark.

These ceilings are regression/release targets, not promises for every computer.
The full reference performance assessment is on Mac; Windows CI exercises source
correctness and the OS memory API, plus native installer compatibility. Full
packaged numerical analysis/performance on both platforms remains a Phase 5 gate.

## Acceptance evidence map

| Criterion | Independent checks and evidence |
| --- | --- |
| P1-A1 Acquisition boundaries and repeatability | Provenance boundary/DST fixtures; mixed source/placement/participant quality fixture; every full-reference window checked against persisted timestamps/devices; byte-identical cold/cached reports in a pinned environment. Numerical tests compare spectral density against SciPy with tolerances on both platforms. Unicode job metadata has an explicit UTF-8 round-trip regression. |
| P1-A2 Traceability and accounting | Source/window digests, intervals, acquisition context, specification, units and quality policy in full JSON; independent SQL and disjoint-record equations; flagged/unflagged partition; interval union separate from record counts; invalid timestamps remain unplaced; timeline covers the full observed extent. |
| P1-A3 Invalidation and incremental equality | Edit/delete/insert, settings, code identity, corruption and reference-population fixtures; full-reference changed-source cached/fresh report equality. Quality flags and normalization are never reused from the raw-feature cache. |
| P1-A4 Preservation and compatibility | Input and frozen database byte hashes, source/frozen label hashes, synthetic empty/current/legacy restore tests, native Windows upgrade/uninstall preservation. No analysis schema migration is introduced. Legacy unknown device identity remains excluded rather than guessed. |
| P1-A5 Background lifecycle and resource envelope | Real HTTP navigation throughout full jobs; extraction and saving cancellation preserve the prior complete pointer; failure/retry/interrupted/competing-lease tests; OS peak memory and cold/cached/changed-source timings against the predeclared budget; complete private coverage HTML/JSON. |

## Storage and remaining boundaries

The manager retains each participant/database's current full payload plus one
extra recent payload; canceled temporary report payloads are removed. Small job
metadata and reusable raw-feature cache entries remain, including entries useful
after an interrupted job. They are not raw samples or personal annotations.
Long-term cache/metadata maintenance and distribution disk sizing need to be
revisited with Phase 5 longitudinal refresh; Phase 1 does not claim unbounded
history or storage has been benchmarked.

The full-reference report does not account for Bluetooth recordings as analyzed
cloud records. Existing capture files are inventoried separately and preserved;
no additional placement or channel is inferred from their filenames. No new
mobile score, clustering method or activity inference is included in this gate.

## Reference assessment

Full-reference source validation passed on macOS 27.2 arm64, Python 3.14.6,
NumPy 2.5.4. Reference R1 contains 296,595 cloud records and 170 labels. All cloud
participants were included (one in this reference). Each of the 73,010 computed
windows passed the persisted-row interval/device check. Accounting reconciles:
292,040 computed record-seconds + 2,143 unknown-device exclusions + 2,412 boundary
remainder record-seconds = 296,595 source records. Numeric feature failures: zero.
The complete timeline has 783 bins; the private HTML was checked in the browser.

| Run | Seconds | Worker peak MiB | Features reused / calculated |
| --- | ---: | ---: | ---: |
| Cold | 47.67 | 548.69 | 0 / 73,010 |
| Repeated cached | 33.72 | 592.86 | 73,010 / 0 |
| Analyzed-window edit/delete plus arrivals, cached | 34.20 | 593.30 | 72,760 / 251 |
| Same changed snapshot, fresh | 48.39 | 549.16 | 0 / 73,011 |

Cold/cached serialized reports are byte-identical. The changed-snapshot cached
and fresh reports are also byte-identical. The first mutation scenario affected
early incomplete records; a supplemental scenario deliberately targeted the first
independently validated computed window and recalculated affected features.
Both scenarios and their original measurements are retained in private evidence.

Extraction cancellation took 0.29 seconds; saving cancellation took 0.50 seconds.
Neither replaced the prior complete result. Navigation p95 stayed below 1.3 ms;
maximum measured navigation latency across these runs stayed below 13 ms. Every
runtime, worker-memory, navigation and cancellation ceiling passed.

Original and frozen source byte hashes and label hashes were unchanged. Four
Bluetooth capture folders / 30 files were separately inventoried; all file hashes
were unchanged. They were not numerically analyzed. Existing personal labels and
API credentials were not used as model inputs or uploaded.

The cold/repeated-cache segments completed before the regression suite. Later
segments included ordinary concurrent regression-test/browser load; these results
assess resource ceilings, not a controlled algorithm-speedup comparison. Exact
runtime source hashes, settings and environment are retained privately alongside
the reports. No clinical validity or cross-platform timing equivalence is claimed.

Local source and packaging checks passed. The cross-platform gate is this
revision’s **Windows installer validation** check, which must be green in the
review PR before Phase 1 acceptance is recorded in the roadmap. The large-archive
timing measurements above remain Mac-only.
