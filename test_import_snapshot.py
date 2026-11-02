import json
import tempfile
import unittest
from pathlib import Path

import app
import import_snapshot


class SnapshotImportTests(unittest.TestCase):
    def test_merge_is_idempotent_and_preserves_labels(self):
        with tempfile.TemporaryDirectory() as temporary:
            original = app.DB_PATH
            app.DB_PATH = Path(temporary) / "dashboard.sqlite3"
            try:
                app.setup_db()
                snapshot = Path(temporary) / "snapshot"
                folder = snapshot / "P-TEST01"
                folder.mkdir(parents=True)
                (snapshot / "manifest.json").write_text(json.dumps({"participants": [
                    {"participantId": "P-TEST01", "rawRecords": 2}]}))
                start = app.utc_ms("2026-05-01T00:00:00Z")
                records = [{"timestamp": app.iso(start + i*1000), "device_id": "AWEAR-TEST",
                            "waveform": [i+1] * 256}
                           for i in range(2)]
                (folder / "raw.json").write_text("[\n" + ",\n".join(json.dumps(x) for x in records) + "\n]\n")
                app.add_label("P-TEST01", "Reading", "", start, start + 30*60_000)
                self.assertEqual(import_snapshot.import_snapshot(snapshot), (2, 2))
                with app.db() as connection:
                    connection.execute("UPDATE readings SET device_id=NULL WHERE timestamp_ms=?", (start,))
                self.assertEqual(import_snapshot.import_snapshot(snapshot), (2, 0))
                with app.db() as connection:
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM readings").fetchone()[0], 2)
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM readings WHERE device_id='AWEAR-TEST'").fetchone()[0], 2)
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM labels").fetchone()[0], 1)
            finally:
                app.DB_PATH = original


if __name__ == "__main__":
    unittest.main()
