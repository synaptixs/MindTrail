import csv
import io
import json
import tempfile
import threading
import urllib.request
import unittest
from pathlib import Path

import app


class MobileScoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.original_path = app.DB_PATH
        self.original_page_size = app.MOBILE_SCORE_PAGE_SIZE
        app.DB_PATH = Path(self.directory.name) / "test.sqlite3"
        app.MOBILE_SCORE_PAGE_SIZE = 2
        app.setup_db()
        self.participant = "P-SCORES"
        self.base = app.utc_ms("2026-05-01T00:00:00Z")
        with app.db() as connection:
            connection.executemany("""INSERT INTO readings
                (participant_id,timestamp_ms,device_id,waveform_json,sample_count,mean,rms,peak_to_peak)
                VALUES (?,?,?,?,?,?,?,?)""", [
                (self.participant, self.base+offset, "DEVICE-1", "[1]", 256, 1, 1, 0)
                for offset in (0, 2000)])
        app.add_label(self.participant, "Reading", "", self.base, self.base+1000)
        app.add_label(self.participant, "Walking", "", self.base+1000, self.base+3000)

    def tearDown(self):
        app.DB_PATH = self.original_path
        app.MOBILE_SCORE_PAGE_SIZE = self.original_page_size
        self.directory.cleanup()

    def test_observation_alignment_keeps_gaps_and_label_boundaries(self):
        app.save_mobile_score(self.participant, self.base+500, 0, "alert", 5)
        app.save_mobile_score(self.participant, self.base+1000, 100, "calm")
        app.save_mobile_score(self.participant, self.base+2000, 52, "engaged")
        rows = app.mobile_score_rows(self.participant)
        self.assertEqual(rows[0]["eeg"]["recordStart"], app.iso(self.base+2000))
        self.assertIsNone(rows[1]["eeg"])
        self.assertEqual(rows[1]["activity"], "Walking")
        self.assertEqual(rows[2]["eeg"]["offsetMs"], 500)
        self.assertEqual(rows[2]["activity"], "Reading")
        self.assertEqual(rows[2]["uncertaintySeconds"], 5)
        self.assertEqual(rows[2]["source"], "mobile_manual")
        self.assertEqual(len(app.eeg_window(self.participant, self.base, 10)["mobileScores"]), 3)
        # State is independently transcribed, not inferred from score thresholds.
        app.save_mobile_score(self.participant, self.base+4000, 5, "calm")
        self.assertEqual(app.mobile_score_rows(self.participant)[0]["state"], "calm")

    def test_paging_filtering_and_exports_include_all_observations(self):
        for offset in (0, 1000, 2000):
            app.save_mobile_score(self.participant, self.base+offset, 20, "relaxed", note="=1+1")
        page = app.mobile_score_page(self.participant)
        self.assertEqual(page["total"], 3)
        self.assertEqual(len(page["observations"]), 2)
        older = app.mobile_score_page(self.participant, before_ms=app.utc_ms(page["nextBefore"]))
        self.assertEqual(len(older["observations"]), 1)
        self.assertIsNone(older["nextBefore"])
        filtered = app.mobile_score_rows(self.participant, self.base+1000, self.base+2000)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["observedAt"], app.iso(self.base+1000))
        exported = json.loads(app.mobile_score_export(self.participant, "json"))
        self.assertEqual(exported["total"], 3)
        self.assertEqual(exported["observations"][0]["note"], "=1+1")
        csv_rows = list(csv.DictReader(io.StringIO(app.mobile_score_export(self.participant, "csv").decode())))
        self.assertEqual(len(csv_rows), 3)
        self.assertEqual(csv_rows[0]["note"], "'=1+1")
        self.assertEqual(csv_rows[0]["eeg_device_id"], "DEVICE-1")
        self.assertEqual(csv_rows[1]["eeg_record_start_utc"], "")

    def test_edit_delete_and_participant_isolation(self):
        identifier = app.save_mobile_score(self.participant, self.base+500, 32, "engaged")
        original = app.mobile_score_rows(self.participant)[0]
        app.save_mobile_score(self.participant, self.base+1500, 80, "calm", 10, "1.2", "edited", identifier)
        changed = app.mobile_score_rows(self.participant)[0]
        self.assertEqual(changed["createdAt"], original["createdAt"])
        self.assertEqual(changed["appVersion"], "1.2")
        self.assertEqual(changed["activity"], "Walking")
        self.assertIsNone(changed["eeg"])
        with self.assertRaisesRegex(ValueError, "not found"):
            app.save_mobile_score("P-OTHER", self.base, 60, "relaxed", score_id=identifier)
        with self.assertRaisesRegex(ValueError, "not found"):
            app.delete_mobile_score("P-OTHER", identifier)
        self.assertEqual(app.mobile_score_rows("P-OTHER"), [])
        with self.assertRaisesRegex(ValueError, "already exists"):
            app.save_mobile_score(self.participant, self.base+1500, 42, "engaged")
        app.setup_db()  # Schema setup is safe for an existing observation archive.
        self.assertEqual(len(app.mobile_score_rows(self.participant)), 1)
        app.delete_mobile_score(self.participant, identifier)
        self.assertEqual(app.mobile_score_page(self.participant)["total"], 0)

    def test_http_checkin_save_and_context_without_a_cloud_key(self):
        with app.db() as connection:
            app.put_setting(connection, "participant_id", self.participant)
        server = app.AppServer(("127.0.0.1", 0), app.Collector("", demo=True))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/api/mobile-scores"
            body = {"observedAt": app.iso(self.base + 1500), "score": 52,
                    "state": "engaged", "uncertaintySeconds": 0}
            request = urllib.request.Request(url, data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json", "X-Awear-Local": "1"})
            with urllib.request.urlopen(request, timeout=5) as response:
                self.assertEqual(response.status, 201)
            with urllib.request.urlopen(url, timeout=5) as response:
                page = json.load(response)
            row = page["observations"][0]
            self.assertEqual(row["observedAt"], body["observedAt"])
            self.assertIsNone(row["eeg"])
            self.assertEqual(row["context"]["coveredSeconds"], 2)
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

    def test_context_retains_gaps_and_updates_when_delayed_records_arrive(self):
        app.save_mobile_score(self.participant, self.base + 1500, 50, "relaxed")
        first = app.mobile_score_page(self.participant)["observations"][0]
        self.assertIsNone(first["eeg"])
        self.assertEqual(first["context"]["recordCount"], 2)
        self.assertEqual(first["context"]["coveredSeconds"], 2)
        self.assertEqual(first["context"]["coveragePercent"], 3.3)
        with app.db() as connection:
            connection.execute("""INSERT INTO readings
                (participant_id,timestamp_ms,waveform_json,sample_count,mean,rms,peak_to_peak)
                VALUES (?,?,?,?,?,?,?)""", (self.participant, self.base + 1000, "[1]", 256, 1, 1, 0))
        refreshed = app.mobile_score_page(self.participant)["observations"][0]
        self.assertIsNotNone(refreshed["eeg"])
        self.assertEqual(refreshed["context"]["coveredSeconds"], 3)
        self.assertEqual(refreshed["observedAt"], first["observedAt"])
        exported = json.loads(app.mobile_score_export(self.participant, "json"))
        self.assertNotIn("context", exported["observations"][0])

    def test_context_clips_boundaries_and_does_not_double_count_overlap(self):
        app.save_mobile_score(self.participant, self.base + 30_000, 50, "relaxed")
        with app.db() as connection:
            for participant, offset in [(self.participant, -1000), (self.participant, 500),
                                         (self.participant, 59_500), (self.participant, 60_000),
                                         ("P-OTHER", 4000)]:
                connection.execute("""INSERT INTO readings
                    (participant_id,timestamp_ms,waveform_json,sample_count,mean,rms,peak_to_peak)
                    VALUES (?,?,?,?,?,?,?)""", (participant, self.base + offset, "[1]", 256, 1, 1, 0))
        context = app.mobile_score_page(self.participant)["observations"][0]["context"]
        self.assertEqual(context["start"], app.iso(self.base))
        self.assertEqual(context["end"], app.iso(self.base + 60_000))
        self.assertEqual(context["recordCount"], 4)
        self.assertEqual(context["coveredSeconds"], 3)
        self.assertEqual(context["coveragePercent"], 5)

    def test_validation_rejects_invalid_values(self):
        for value in (-1, 101, float("nan"), True, "50"):
            with self.subTest(score=value), self.assertRaises(ValueError):
                app.save_mobile_score(self.participant, self.base, value, "calm")
        with self.assertRaises(ValueError):
            app.save_mobile_score(self.participant, self.base, 50, "unknown")
        with self.assertRaises(ValueError):
            app.save_mobile_score(self.participant, self.base, 50, "calm", 1.5)
        with self.assertRaises(ValueError):
            app.save_mobile_score(self.participant, 9_999_999_999_999, 50, "calm")
        with self.assertRaises(ValueError):
            app.mobile_score_rows(self.participant, self.base+1000, self.base)


if __name__ == "__main__":
    unittest.main()
