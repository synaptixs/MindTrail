"""Versioned input/window contracts for the Phase 1 analysis foundation.

This module inspects existing cloud archives read-only. It does not calculate
features, diagnose artifacts, run clustering or modify the source database.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import sys
from typing import Callable

CONTRACT_VERSION = 1
METHOD_VERSION = "provenance-v1"


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def fingerprint(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AnalysisSpec:
    sample_rate: int = 256
    window_seconds: int = 4
    normalization: str = "none"
    quality_policy_version: str = "not-implemented"

    def __post_init__(self) -> None:
        if type(self.sample_rate) is not int or self.sample_rate <= 0:
            raise ValueError("Sample rate must be a positive integer")
        if type(self.window_seconds) is not int or not 1 <= self.window_seconds <= 60:
            raise ValueError("Window duration must be an integer from 1 to 60 seconds")
        if self.normalization not in ("none", "within-acquisition-group-robust"):
            raise ValueError("Unsupported normalization policy")
        if not isinstance(self.quality_policy_version, str) or not self.quality_policy_version:
            raise ValueError("A quality policy version is required")

    @property
    def digest(self) -> str:
        return fingerprint(asdict(self))


@dataclass(frozen=True)
class AcquisitionContext:
    participant_id: str
    device_id: str | None
    source: str
    placement: str
    sample_rate: int
    channel: str | None
    timestamp_basis: str
    session_id: str | None = None
    units: str = "uncalibrated device units"

    def __post_init__(self) -> None:
        if not isinstance(self.participant_id, str) or not self.participant_id.strip():
            raise ValueError("Participant identity is required")
        if self.device_id is not None and (not isinstance(self.device_id, str) or not self.device_id.strip()):
            raise ValueError("Device identity must be a nonempty string or unknown")
        if self.source not in ("awear-cloud", "bluetooth") or self.placement not in ("right", "left", "unknown"):
            raise ValueError("Explicit source and placement are required")
        if (type(self.sample_rate) is not int or self.sample_rate <= 0
                or not isinstance(self.units, str) or not self.units.strip()):
            raise ValueError("Sample rate and units are required")
        if self.channel is not None and (not isinstance(self.channel, str) or not self.channel.strip()):
            raise ValueError("Channel must be a nonempty string or explicitly unknown")
        if self.source == "awear-cloud" and (self.placement, self.channel, self.sample_rate, self.timestamp_basis) != ("right", "RIGHT_TEMP", 256, "cloud-timestamp"):
            raise ValueError("The current cloud archive contract is RIGHT_TEMP, right ear, 256 Hz")
        if self.source == "bluetooth" and (not isinstance(self.session_id, str) or not self.session_id):
            raise ValueError("Bluetooth windows require an explicit session identity")
        if self.timestamp_basis not in ("cloud-timestamp", "device-timestamp", "host-arrival"):
            raise ValueError("Timestamp basis must be explicit")

    @property
    def group_id(self) -> str:
        return fingerprint(asdict(self))


@dataclass(frozen=True)
class SourceRecord:
    context: AcquisitionContext
    timestamp_ms: int
    waveform_json: str
    sample_count: int

    @property
    def digest(self) -> str:
        return fingerprint([asdict(self.context), self.timestamp_ms, self.sample_count, self.waveform_json])

    def validate(self) -> None:
        if type(self.timestamp_ms) is not int or self.timestamp_ms < 0:
            raise ValueError("Record time must be a nonnegative UTC epoch millisecond integer")
        if type(self.sample_count) is not int or self.sample_count != self.context.sample_rate:
            raise ValueError("A source record must contain exactly one second of samples")
        if not isinstance(self.waveform_json, str):
            raise ValueError("Source waveform must be stored as JSON text")
        try:
            samples = json.loads(self.waveform_json)
            valid = (isinstance(samples, list) and len(samples) == self.sample_count
                     and all(type(value) in (int, float) and math.isfinite(value) for value in samples))
        except (ValueError, TypeError, OverflowError):
            valid = False
        if not valid:
            raise ValueError("Source samples must be finite numeric values with the declared shape")


def implementation_fingerprint() -> str:
    """Dirty source edits invalidate identities; no commit-only cache assumption."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def analysis_identity(source_revision: str, spec: AnalysisSpec, implementation: str) -> str:
    return fingerprint({"contractVersion": CONTRACT_VERSION, "methodVersion": METHOD_VERSION,
                        "sourceRevision": source_revision, "spec": asdict(spec),
                        "implementation": implementation,
                        "python": list(sys.version_info[:3])})


def make_window_provenance(records: list[SourceRecord], spec: AnalysisSpec,
                           implementation: str | None = None) -> dict:
    """Reject incompatible or discontinuous records before any feature computation."""
    if len(records) != spec.window_seconds:
        raise ValueError("Window must contain the configured number of one-second records")
    first = records[0]
    if first.context.device_id is None:
        raise ValueError("Device identity is unknown; do not assume recordings are compatible")
    if first.context.sample_rate != spec.sample_rate:
        raise ValueError("Acquisition and analysis sample rates differ")
    for index, record in enumerate(records):
        record.validate()
        if record.context != first.context:
            raise ValueError("A window cannot cross an acquisition group")
        if record.timestamp_ms != first.timestamp_ms + index * 1000:
            raise ValueError("A window cannot cross a gap, duplicate or out-of-order record")
    revision = fingerprint([record.digest for record in records])
    return {"contractVersion": CONTRACT_VERSION, "methodVersion": METHOD_VERSION,
            "windowId": analysis_identity(revision, spec, implementation or implementation_fingerprint()),
            "sourceRevision": revision, "sourceRecordDigests": [record.digest for record in records],
            "acquisitionGroupId": first.context.group_id, "acquisition": asdict(first.context),
            "startMs": first.timestamp_ms, "endMsExclusive": first.timestamp_ms + spec.window_seconds * 1000,
            "sampleCount": spec.sample_rate * spec.window_seconds, "spec": asdict(spec)}


def _sqlite_value(value: object) -> object:
    # Corrupt metadata must still affect the input revision, not disappear from it.
    if isinstance(value, bytes):
        return {"blobSha256": hashlib.sha256(value).hexdigest(), "length": len(value)}
    if isinstance(value, float) and not math.isfinite(value):
        return {"nonfiniteFloat": repr(value)}
    return value


def inspect_archive(database: Path, participant: str | None = None,
                    spec: AnalysisSpec | None = None, *,
                    on_record: Callable[[AcquisitionContext, tuple], None] | None = None,
                    start_ms: int | None = None, end_ms: int | None = None,
                    on_start: Callable[[int], None] | None = None) -> dict:
    """Stream a consistent cloud-source snapshot without setup/migrations or key access.

    Every selected record, including invalid waveform text, affects the revision.
    Labels and redundant mean/RMS columns do not: later features must derive from samples.
    No claim about waveform eligibility or signal quality is made by this inventory.
    """
    spec = spec or AnalysisSpec()
    if any(value is not None and (type(value) is not int or value < 0) for value in (start_ms, end_ms)):
        raise ValueError("Range boundaries must be nonnegative UTC epoch milliseconds")
    if start_ms is not None and end_ms is not None and start_ms >= end_ms:
        raise ValueError("Range start must precede end")
    database = database.expanduser().resolve()
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        columns = {row[1] for row in connection.execute("PRAGMA table_info(readings)")}
        required = {"participant_id", "timestamp_ms", "waveform_json", "sample_count"}
        if not required <= columns:
            raise ValueError("Archive lacks the cloud readings schema; it was not modified")
        if participant is None:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            selected = connection.execute("SELECT value FROM settings WHERE key='participant_id'").fetchone() if "settings" in tables else None
            participant = selected[0] if selected else None
        if not isinstance(participant, str) or not participant:
            raise ValueError("Choose a participant explicitly with --participant")
        context = AcquisitionContext(participant, None, "awear-cloud", "right", 256, "RIGHT_TEMP", "cloud-timestamp")
        digest = hashlib.sha256()
        def include(value: object) -> None:
            encoded = canonical_json(value).encode("utf-8")
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
        include({"contractVersion": CONTRACT_VERSION, "source": asdict(context)})
        conditions, arguments = ["participant_id=?"], [participant]
        for boundary, operator in ((start_ms, ">="), (end_ms, "<")):
            if boundary is not None:
                conditions.append(f"timestamp_ms {operator} ?")
                arguments.append(boundary)
        if start_ms is not None or end_ms is not None:
            include({"startMs": start_ms, "endMsExclusive": end_ms})
        device_column = "device_id" if "device_id" in columns else "NULL"
        query = f"""SELECT timestamp_ms,{device_column},sample_count,waveform_json
            FROM readings WHERE {' AND '.join(conditions)}
            ORDER BY timestamp_ms,{device_column},sample_count,waveform_json"""
        if on_start is not None:
            on_start(connection.execute(f"SELECT COUNT(*) FROM readings WHERE {' AND '.join(conditions)}",arguments).fetchone()[0])
        count = invalid_times = 0
        first = last = None
        devices = Counter()
        for row in connection.execute(query, arguments):
            include([_sqlite_value(value) for value in row])
            count += 1
            if type(row[0]) is int and row[0] >= 0:
                first = row[0] if first is None else min(first, row[0])
                last = row[0] if last is None else max(last, row[0])
            else:
                invalid_times += 1
            device = row[1] if isinstance(row[1], str) and row[1] else None
            devices[device] += 1
            if on_record is not None:
                on_record(context, row)
        revision = digest.hexdigest()
    implementation = implementation_fingerprint()
    return {"contractVersion": CONTRACT_VERSION, "methodVersion": METHOD_VERSION,
            "scope": "cloud readings inventory only; no waveform quality/feature analysis",
            "notInspected": ["bluetooth captures", "activity labels", "mobile observations"],
            "sourceDatabase": database.name, "acquisition": asdict(context),
            "sourceRevision": revision, "analysisIdentity": analysis_identity(revision, spec, implementation),
            "implementationFingerprint": implementation, "python": list(sys.version_info[:3]),
            "spec": asdict(spec), "recordCount": count, "firstTimestampMs": first, "lastTimestampMs": last,
            "range": {"startMs": start_ms, "endMsExclusive": end_ms},
            "invalidTimestampRecords": invalid_times,
            "deviceCounts": [{"deviceId": device, "records": devices[device]}
                             for device in sorted(devices, key=lambda value: (value is not None, value or ""))]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path, help="Existing archive SQLite file; opened read-only")
    parser.add_argument("--participant", help="Defaults to the archive's selected participant")
    parser.add_argument("--output", type=Path, help="Optional new JSON file; existing files are never overwritten")
    args = parser.parse_args()
    try:
        report = inspect_archive(args.database, args.participant)
        text = json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        if args.output:
            with args.output.open("x", encoding="utf-8") as output:
                output.write(text)
        else:
            print(text, end="")
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, f"Inventory failed: {error}\n")


if __name__ == "__main__":
    main()
