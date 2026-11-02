#!/usr/bin/env python3
"""Merge raw records from an AWEAR API snapshot into the local dashboard archive."""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
from pathlib import Path

import app


def import_snapshot(snapshot: Path) -> tuple[int, int]:
    manifest_path = snapshot / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    total = 0
    inserted = 0
    def merge_rows(connection, rows):
        before = connection.total_changes
        connection.executemany("""INSERT OR IGNORE INTO readings
            (participant_id,timestamp_ms,device_id,waveform_json,sample_count,mean,rms,peak_to_peak)
            VALUES (?,?,?,?,?,?,?,?)""", rows)
        added = connection.total_changes - before
        connection.executemany("""UPDATE readings SET device_id=?
            WHERE participant_id=? AND timestamp_ms=? AND device_id IS NULL""",
            [(row[2], row[0], row[1]) for row in rows if row[2] is not None])
        return added

    with app.db() as connection:
        for participant in manifest["participants"]:
            participant_id = str(participant["participantId"])
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", participant_id):
                raise ValueError("Snapshot contains an invalid participant ID")
            raw_path = snapshot / participant_id / "raw.json"
            rows = []
            participant_count = 0
            with raw_path.open(encoding="utf-8") as source:
                for line in source:
                    line = line.strip()
                    if line in ("[", "]", ""):
                        continue
                    record = json.loads(line.removesuffix(","))
                    waveform = record.get("waveform")
                    if (record.get("participantId", participant_id) != participant_id
                            or record.get("channel", "RIGHT_TEMP") != "RIGHT_TEMP"
                            or record.get("samplingRate", 256) != 256
                            or not isinstance(waveform, list) or len(waveform) != 256
                            or any(isinstance(x, bool) or not isinstance(x, (int, float))
                                   or not math.isfinite(x) for x in waveform)):
                        raise ValueError("Snapshot contains an unexpected raw EEG record")
                    mean = statistics.fmean(waveform)
                    rms = math.sqrt(statistics.fmean(x*x for x in waveform))
                    rows.append((participant_id, app.utc_ms(record["timestamp"]), record.get("device_id"),
                                 json.dumps(waveform, separators=(",", ":")), 256,
                                 mean, rms, max(waveform)-min(waveform)))
                    participant_count += 1
                    if len(rows) >= 1000:
                        inserted += merge_rows(connection, rows)
                        rows.clear()
            if rows:
                inserted += merge_rows(connection, rows)
            if participant_count != participant["rawRecords"]:
                raise ValueError("Snapshot raw record count does not match its manifest")
            total += participant_count
    return total, inserted


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge a saved API snapshot into the local dashboard")
    parser.add_argument("snapshot", type=Path, nargs="?", help="Snapshot directory; defaults to newest in data/")
    args = parser.parse_args()
    if args.snapshot:
        snapshot = args.snapshot
    else:
        snapshots = sorted((app.data_root() / "data").glob("export-*"))
        if not snapshots:
            parser.exit(1, "No AWEAR export snapshot found in data/.\n")
        snapshot = snapshots[-1]
    try:
        app.setup_db()
        total, inserted = import_snapshot(snapshot)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        parser.exit(1, f"Import failed: {error}\n")
    print(f"Merged {inserted} new records from {total} snapshot records into the dashboard archive.")


if __name__ == "__main__":
    main()
