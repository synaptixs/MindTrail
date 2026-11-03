#!/usr/bin/env python3
"""Export the documented AWEAR API to local JSON and CSV snapshots."""

from __future__ import annotations

import argparse
import csv
import getpass
import json
import re
import shutil
import tempfile
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from app import data_root, Collector, ROOT, iso, read_api_key, safe_csv, utc_ms
from archive_paths import ArchivePaths

RAW_WINDOW_MS = 60 * 60 * 1000
SESSIONS_WINDOW_MS = 30 * 24 * 60 * 60 * 1000
RAW_LIMIT = 5000
SESSIONS_LIMIT = 500


def filename_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
        raise ValueError("AWEAR returned an invalid participant ID")
    return value


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def csv_value(value: object) -> str | int | float:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, str):
        return safe_csv(value)
    return value


def write_rows(path: Path, rows: list[dict], preferred: tuple[str, ...]) -> None:
    fields = list(preferred)
    fields.extend(sorted({key for row in rows for key in row} - set(fields)))
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(fields)
        for row in rows:
            writer.writerow([csv_value(row.get(key)) for key in fields])


def write_pair(directory: Path, name: str, value: object, rows: list[dict], fields: tuple[str, ...]) -> None:
    write_json(directory / f"{name}.json", value)
    write_rows(directory / f"{name}.csv", rows, fields)


def split_fetch(collector: Collector, path: str, start_ms: int, end_ms: int,
                array_key: str, limit: int, min_window_ms: int) -> list[dict]:
    """Fetch a complete bounded window without depending on an API cursor."""
    if array_key == "data":
        collector._pace_raw_request()
    result = collector.request(path, {"start": iso(start_ms), "end": iso(end_ms),
                                      "limit": limit, **({"sort": "asc"} if array_key == "data" else {})})
    rows = result.get(array_key)
    if not isinstance(rows, list):
        raise RuntimeError(f"AWEAR response is missing {array_key}")
    pagination = result.get("pagination") or {}
    if pagination.get("hasMore") or len(rows) >= limit:
        if end_ms - start_ms <= min_window_ms:
            raise RuntimeError(f"AWEAR returned an incomplete {array_key} window")
        midpoint = start_ms + (end_ms - start_ms) // 2
        left = split_fetch(collector, path, start_ms, midpoint, array_key, limit, min_window_ms)
        right = split_fetch(collector, path, midpoint, end_ms, array_key, limit, min_window_ms)
        if array_key == "data":
            # An API range can include its end. Give the midpoint to the right
            # range without dropping distinct records at the same timestamp.
            left = [row for row in left if utc_ms(row["timestamp"]) < midpoint]
            right = [row for row in right if utc_ms(row["timestamp"]) >= midpoint]
        return left + right
    return rows


def windows(start_ms: int, end_ms: int, size_ms: int, overlap_ms: int):
    current = start_ms
    while current < end_ms:
        following = min(current + size_ms, end_ms)
        yield max(start_ms, current - overlap_ms), following
        current = following


def export_raw(collector: Collector, directory: Path, participant: str,
               start_ms: int, end_ms: int) -> int:
    path = "/members/" + urllib.parse.quote(participant, safe="") + "/data"
    count = 0
    with (directory / "raw.json").open("w", encoding="utf-8") as json_out, \
            (directory / "raw.csv").open("w", encoding="utf-8", newline="") as csv_out:
        writer = csv.writer(csv_out)
        writer.writerow(["timestamp", "participantId", "channel", "samplingRate"]
                        + [f"sample_{i:03d}" for i in range(256)])
        json_out.write("[\n")
        current = start_ms
        while current < end_ms:
            upper = min(current + RAW_WINDOW_MS, end_ms)
            rows = split_fetch(collector, path, max(start_ms, current - 1000),
                               upper, "data", RAW_LIMIT, 1000)
            for row in sorted(rows, key=lambda item: item["timestamp"]):
                timestamp = utc_ms(row["timestamp"])
                if not current <= timestamp < upper:
                    continue
                waveform = row.get("waveform")
                if not isinstance(waveform, list) or len(waveform) != 256:
                    raise RuntimeError("AWEAR returned a waveform that is not 256 samples")
                if count:
                    json_out.write(",\n")
                json.dump(row, json_out, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                writer.writerow([csv_value(row.get(field)) for field in
                                 ("timestamp", "participantId", "channel", "samplingRate")]
                                + [csv_value(sample) for sample in waveform])
                count += 1
            current = upper
        json_out.write("\n]\n")
    return count


def export_sessions(collector: Collector, directory: Path, participant: str,
                    start_ms: int, end_ms: int) -> int:
    path = "/members/" + urllib.parse.quote(participant, safe="") + "/sessions"
    seen: set[str] = set()
    sessions: list[dict] = []
    for lower, upper in windows(start_ms, end_ms, SESSIONS_WINDOW_MS, 1000):
        for row in split_fetch(collector, path, lower, upper, "sessions", SESSIONS_LIMIT, 1000):
            key = json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            if key not in seen:
                seen.add(key)
                sessions.append(row)
    sessions.sort(key=lambda row: str(row.get("startTime") or row.get("start") or ""))
    write_pair(directory, "sessions", sessions, sessions,
               ("date", "startTime", "endTime", "sampleCount", "deviceId",
                "start", "end", "durationSec", "records"))
    return len(sessions)


def flatten(value: object, prefix: str = "") -> dict:
    if not isinstance(value, dict):
        return {prefix: value}
    result = {}
    for key, child in value.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(child, dict):
            result.update(flatten(child, name))
        else:
            result[name] = child
    return result


def run_export(collector: Collector, data_dir: Path, requested_participant: str | None = None,
               requested_start: int | None = None, requested_end: int | None = None) -> Path:
    end_ms = requested_end if requested_end is not None else int(time.time() * 1000)
    if end_ms > int(time.time() * 1000) + 60_000:
        raise ValueError("End must not be in the future")
    members_response = collector.request("/members")
    members = members_response.get("members")
    if not isinstance(members, list):
        raise RuntimeError("AWEAR response is missing members")
    ids = [filename_id(str(member["participantId"])) for member in members]
    if requested_participant and requested_participant not in ids:
        raise ValueError("Participant ID is not accessible with this API key")
    ids = [requested_participant] if requested_participant else ids
    if not ids:
        raise RuntimeError("AWEAR returned no accessible participants")

    data_dir.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".awear-export-", dir=data_dir))
    try:
        write_pair(staging, "members", members_response, members,
                   ("participantId", "status", "joinedAt", "accessState", "deviceId", "hasSlot"))
        manifest = {"exportedAt": iso(int(time.time() * 1000)),
                    "source": "https://awearlabs.com/api/v1",
                    "note": "Documented research API only; consumer app scores and tags are not exposed.",
                    "participants": []}
        compare_start = None
        for participant in ids:
            directory = staging / participant
            directory.mkdir()
            encoded = urllib.parse.quote(participant, safe="")
            stats = collector.request(f"/members/{encoded}/stats")
            # The live API currently calls these firstRecord/lastRecord, while
            # the published example uses firstSeen/lastSeen.
            first_record = stats.get("firstRecord") or stats.get("firstSeen")
            if requested_start is None and not first_record and (
                stats.get("totalSamples", 0) or stats.get("totalRecords", 0)
            ):
                raise RuntimeError("AWEAR reports data but no first-record timestamp; pass --start explicitly")
            start_ms = (requested_start if requested_start is not None else
                        utc_ms(first_record) if first_record else end_ms)
            if start_ms > end_ms:
                raise ValueError("Start must be before end")
            compare_start = min(compare_start, start_ms) if compare_start is not None else start_ms
            write_pair(directory, "stats", stats, [flatten(stats)],
                       ("participantId", "totalRecords", "totalSamples", "totalHours",
                        "activeDays", "firstRecord", "lastRecord", "firstSeen", "lastSeen"))
            session_count = export_sessions(collector, directory, participant, start_ms, end_ms)
            raw_count = export_raw(collector, directory, participant, start_ms, end_ms)
            if requested_start is None and first_record and start_ms < end_ms and (
                stats.get("totalSamples", 0) or stats.get("totalRecords", 0)
            ) and raw_count == 0:
                raise RuntimeError("AWEAR stats report recordings, but the raw export is empty")
            reported = stats.get("totalSamples", stats.get("totalRecords"))
            last_record = stats.get("lastRecord") or stats.get("lastSeen")
            if (requested_start is None and isinstance(reported, int) and reported > raw_count
                    and last_record and utc_ms(last_record) < end_ms):
                raise RuntimeError(f"AWEAR stats report {reported} records, but the raw export has "
                                   f"only {raw_count}; no snapshot was published")
            manifest["participants"].append({"participantId": participant, "start": iso(start_ms),
                                               "end": iso(end_ms), "rawRecords": raw_count,
                                               "sessions": session_count})

        if 2 <= len(ids) <= 10 and compare_start < end_ms:
            comparison = collector.request("/compare", {"participants": ",".join(ids),
                                                        "start": iso(compare_start), "end": iso(end_ms)})
            write_json(staging / "compare.json", comparison)
            write_rows(staging / "compare_stats.csv", comparison.get("stats", []),
                       ("participantId", "totalRecords", "activeDays"))
            daily = [{"participantId": entry.get("participantId"), **day}
                     for entry in comparison.get("timeline", []) for day in entry.get("days", [])]
            write_rows(staging / "compare_timeline.csv", daily, ("participantId", "date", "records"))
        else:
            manifest["compare"] = "Skipped: requires 2–10 participants in a shared window"

        usage = collector.request("/usage")
        usage_rows = [{"keyId": usage.get("keyId"), "bucket": name, **flatten(value)}
                      for name, value in usage.get("usage", {}).items()]
        write_pair(staging, "usage", usage, usage_rows,
                   ("keyId", "bucket", "used", "limit", "remaining", "resetsAt"))
        write_json(staging / "manifest.json", manifest)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = data_dir / f"export-{stamp}"
        suffix = 2
        while destination.exists():
            destination = data_dir / f"export-{stamp}-{suffix}"
            suffix += 1
        staging.rename(destination)
        return destination
    except BaseException:
        shutil.rmtree(staging)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Export documented AWEAR API data as JSON and CSV")
    parser.add_argument("--key-prompt", action="store_true", help="Prompt for the key without echoing it")
    parser.add_argument("--participant", help="Export only one accessible participant ID")
    parser.add_argument("--start", help="ISO 8601 start; defaults to each participant's firstSeen")
    parser.add_argument("--end", help="ISO 8601 end; defaults to now")
    parser.add_argument("--data-dir", type=Path, default=ArchivePaths(data_root()).cloud,
                        help="Snapshot parent directory; defaults to data/raw/cloud/ in the selected archive")
    args, extras = parser.parse_known_args()
    if extras:
        parser.exit(2, "Unexpected argument. Enter the API key only after the hidden --key-prompt.\n")
    key = getpass.getpass("AWEAR API key: ") if args.key_prompt else read_api_key()
    if not key:
        parser.exit(2, "Set AWEAR_API_KEY in .env or run with --key-prompt.\n")
    try:
        result = run_export(Collector(key), args.data_dir, args.participant,
                            utc_ms(args.start) if args.start else None,
                            utc_ms(args.end) if args.end else None)
    except (RuntimeError, ValueError, OSError) as error:
        parser.exit(1, f"Export failed: {error}\n")
    print(f"AWEAR export saved to {result}")


if __name__ == "__main__":
    main()
