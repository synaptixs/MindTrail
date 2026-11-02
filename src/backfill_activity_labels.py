#!/usr/bin/env python3
"""Suggest work labels from a user-provided schedule, never from EEG features.

Dry run by default. Applying writes only labels, with a before snapshot and an
audit report. Existing activities are subtracted, including existing suggestions.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import app

LABEL = "Work (Misc)"
NOTE = ("Suggested from your general 08:00–16:00 work schedule (America/New_York). "
        "Not confirmed for this interval. Short relaxing breaks may be included; review and refine.")


def plan(connection, participant: str, weekdays_only: bool = True) -> dict:
    zone = ZoneInfo("America/New_York")
    timestamps = [row[0] for row in connection.execute(
        "SELECT timestamp_ms FROM readings WHERE participant_id=? ORDER BY timestamp_ms", (participant,))]
    existing = [dict(row) for row in connection.execute(
        "SELECT * FROM labels WHERE participant_id=? ORDER BY start_ms,id", (participant,))]
    slots = defaultdict(list)
    eligible = 0
    for timestamp in timestamps:
        local = datetime.fromtimestamp(timestamp/1000, zone)
        if not 8 <= local.hour < 16 or (weekdays_only and local.weekday() >= 5):
            continue
        eligible += 1
        slot = timestamp // app.ACTIVITY_INTERVAL_MS * app.ACTIVITY_INTERVAL_MS
        slots[slot].append(timestamp)
    intervals = []
    for slot, recorded in sorted(slots.items()):
        pieces = [(slot, slot+app.ACTIVITY_INTERVAL_MS)]
        for label in existing:
            lower, upper = label["start_ms"], label["end_ms"] or 2**63-1
            remaining = []
            for start, end in pieces:
                if upper <= start or lower >= end:
                    remaining.append((start, end))
                else:
                    if start < lower:
                        remaining.append((start, lower))
                    if upper < end:
                        remaining.append((upper, end))
            pieces = remaining
        for start, end in pieces:
            left, right = bisect_left(recorded, start), bisect_left(recorded, end)
            if left == right:
                continue
            # Bound suggestions by actual recorded endpoints; retain small gaps
            # inside a half hour as inferred schedule time, not observed activity.
            start, end = recorded[left], min(end, recorded[right-1]+1000)
            intervals.append({"start": app.iso(start), "end": app.iso(end), "records": right-left,
                              "localDate": datetime.fromtimestamp(start/1000, zone).date().isoformat()})
    identity = json.dumps({"participant": participant, "weekdaysOnly": weekdays_only,
                           "intervals": intervals}, sort_keys=True).encode()
    return {"participantId": participant, "label": LABEL, "note": NOTE,
            "source": "schedule", "reviewed": False,
            "policy": {"timezone": "America/New_York", "startHour": 8, "endHour": 16,
                       "weekdaysOnly": weekdays_only, "maximumIntervalMinutes": 30,
                       "breaks": "No break timestamps supplied; no relaxing intervals invented"},
            "recordedRecords": len(timestamps), "eligibleRecords": eligible,
            "newlyCoveredRecords": sum(row["records"] for row in intervals),
            "alreadyLabeledEligibleRecords": eligible-sum(row["records"] for row in intervals),
            "outsideScheduleRecords": len(timestamps)-eligible,
            "recordedFirst": app.iso(timestamps[0]) if timestamps else None,
            "recordedLast": app.iso(timestamps[-1]) if timestamps else None,
            "newLabels": len(intervals), "intervals": intervals,
            "existingLabels": existing,
            "batchId": "work-schedule-"+hashlib.sha256(identity).hexdigest()[:16]}


def apply(participant: str, weekdays_only: bool, report_root: Path) -> dict:
    app.setup_db()
    with app.db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        result = plan(connection, participant, weekdays_only)
        if not result["intervals"]:
            return {**result, "status": "no changes", "createdIds": []}
        folder = report_root / result["batchId"]
        folder.mkdir(parents=True, exist_ok=True)
        before_path = folder / "labels-before.json"
        if before_path.exists():
            raise ValueError("This audit batch already exists; review its report before retrying")
        before_path.write_text(json.dumps(result["existingLabels"], indent=2)+"\n")
        created = []
        for interval in result["intervals"]:
            cursor = connection.execute("""INSERT INTO labels
                (participant_id,label,note,start_ms,end_ms,source,reviewed,batch_id)
                VALUES (?,?,?,?,?,'schedule',0,?)""",
                (participant, LABEL, NOTE, app.utc_ms(interval["start"]), app.utc_ms(interval["end"]), result["batchId"]))
            created.append(cursor.lastrowid)
        for original in result["existingLabels"]:
            found = dict(connection.execute("SELECT * FROM labels WHERE id=?", (original["id"],)).fetchone())
            assert found == original, "An existing label changed"
        overlap = connection.execute("""SELECT 1 FROM labels a JOIN labels b
            ON a.participant_id=b.participant_id AND a.id<b.id
            AND a.start_ms<COALESCE(b.end_ms,9223372036854775807)
            AND b.start_ms<COALESCE(a.end_ms,9223372036854775807)
            WHERE a.participant_id=? LIMIT 1""", (participant,)).fetchone()
        if overlap:
            raise ValueError("Overlapping activities detected; backfill rolled back")
        result.update(status="applied", createdIds=created, beforeSnapshot=str(before_path))
        # A prepared report plus batch_id identifies the transaction if writing
        # the final applied report is interrupted after commit.
        (folder/"report.json").write_text(json.dumps({**result, "status": "prepared"}, indent=2)+"\n")
    (folder/"report.json").write_text(json.dumps(result, indent=2)+"\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--participant")
    parser.add_argument("--all-days", action="store_true", help="Also apply the guideline on weekends")
    parser.add_argument("--apply", action="store_true", help="Save suggestions; default only previews")
    args = parser.parse_args()
    with app.db() as connection:
        participant = args.participant or app.setting(connection, "participant_id")
        if not participant:
            parser.error("Select a participant first")
        preview = plan(connection, participant, not args.all_days)
    result = apply(participant, not args.all_days, app.data_root()/"data/activity-backfill") if args.apply else preview
    print(json.dumps({key:value for key,value in result.items() if key not in ("intervals", "existingLabels", "createdIds")}, indent=2))


if __name__ == "__main__":
    main()
