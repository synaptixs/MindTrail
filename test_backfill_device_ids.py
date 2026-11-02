import json
import tempfile
import unittest
from pathlib import Path

import app
import backfill_device_ids


class BackfillDeviceIdsTests(unittest.TestCase):
    def test_only_matching_unambiguous_record_is_updated(self):
        with tempfile.TemporaryDirectory() as temporary:
            original = app.DB_PATH
            app.DB_PATH = Path(temporary) / "test.sqlite3"
            try:
                app.setup_db()
                participant = "P-TEST01"
                first = app.utc_ms("2026-05-01T00:00:00Z")
                waveform = json.dumps([1, 2], separators=(",", ":"))
                with app.db() as connection:
                    app.put_setting(connection, "participant_id", participant)
                    connection.executemany("""INSERT INTO readings
                        (participant_id,timestamp_ms,waveform_json,sample_count,mean,rms,peak_to_peak)
                        VALUES (?,?,?,?,?,?,?)""", [
                        (participant, first+i*1000, waveform, 2, 1.5, 1.58, 1) for i in range(3)
                    ])

                class FakeCollector:
                    def _fetch_complete_window(self, chosen, start_ms, end_ms):
                        assert chosen == participant
                        return [
                            (participant, first, "AWEAR-TEST", waveform, 2, 1.5, 1.58, 1),
                            (participant, first+1000, "AWEAR-TEST", "[9,9]", 2, 9, 9, 0),
                            (participant, first+2000, "AWEAR-TEST", waveform, 2, 1.5, 1.58, 1),
                            (participant, first+2000, "AWEAR-OTHER", waveform, 2, 1.5, 1.58, 1),
                        ]

                self.assertEqual(backfill_device_ids.backfill(FakeCollector()), (3, 1, 2))
                with app.db() as connection:
                    ids = [row[0] for row in connection.execute(
                        "SELECT device_id FROM readings ORDER BY timestamp_ms")]
                self.assertEqual(ids, ["AWEAR-TEST", None, None])
            finally:
                app.DB_PATH = original


if __name__ == "__main__":
    unittest.main()
