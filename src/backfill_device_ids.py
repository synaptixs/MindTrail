#!/usr/bin/env python3
"""Fill missing archived device IDs from matching AWEAR raw records."""

from __future__ import annotations

import argparse
import getpass

import app


def backfill(collector: app.Collector) -> tuple[int, int, int]:
    """Return missing before, filled, and missing after for the selected participant.

    Match both timestamp and waveform. Ambiguous API device IDs stay unresolved.
    """
    app.setup_db()
    with app.db() as connection:
        participant = app.setting(connection, "participant_id")
        if not participant:
            raise ValueError("Select a participant in the dashboard first")
        before = connection.execute("""SELECT COUNT(*) FROM readings
            WHERE participant_id=? AND device_id IS NULL""", (participant,)).fetchone()[0]
        buckets = [row[0] for row in connection.execute("""SELECT DISTINCT
            (timestamp_ms / ?) * ? AS bucket FROM readings
            WHERE participant_id=? AND device_id IS NULL ORDER BY bucket""",
            (app.RAW_WINDOW_MS, app.RAW_WINDOW_MS, participant))]
    filled = 0
    for start_ms in buckets:
        rows = collector._fetch_complete_window(participant, start_ms, start_ms + app.RAW_WINDOW_MS)
        candidates: dict[tuple[int, str], set[str]] = {}
        for row in rows:
            device_id = row[2]
            if isinstance(device_id, str) and device_id:
                candidates.setdefault((row[1], row[3]), set()).add(device_id)
        unique = [(next(iter(ids)), participant, timestamp_ms, waveform_json)
                  for (timestamp_ms, waveform_json), ids in candidates.items() if len(ids) == 1]
        with app.db() as connection:
            before_changes = connection.total_changes
            connection.executemany("""UPDATE readings SET device_id=?
                WHERE participant_id=? AND timestamp_ms=? AND waveform_json=? AND device_id IS NULL""",
                unique)
            filled += connection.total_changes - before_changes
    with app.db() as connection:
        remaining = connection.execute("""SELECT COUNT(*) FROM readings
            WHERE participant_id=? AND device_id IS NULL""", (participant,)).fetchone()[0]
    return before, filled, remaining


class SafeParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        if message.startswith("unrecognized arguments:"):
            self.exit(2, "error: unexpected argument. Run `python3 backfill_device_ids.py --key-prompt` "
                         "with nothing after it; enter the key at the hidden prompt.\n")
        super().error(message)


def main() -> None:
    parser = SafeParser(description="Fill missing local device IDs using your AWEAR API key")
    parser.add_argument("--key-prompt", action="store_true", help="Read API key without echoing or saving it")
    args = parser.parse_args()
    key = getpass.getpass("AWEAR API key: ") if args.key_prompt else app.read_api_key()
    if not key:
        parser.exit(1, "API key is required. Add AWEAR_API_KEY to .env or run with --key-prompt.\n")
    try:
        before, filled, remaining = backfill(app.Collector(key))
    except (ValueError, RuntimeError) as error:
        parser.exit(1, f"Backfill failed: {error}\n")
    print(f"Missing before: {before}. Filled from matching AWEAR records: {filled}. Still missing: {remaining}.")


if __name__ == "__main__":
    main()
