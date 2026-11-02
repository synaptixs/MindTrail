import csv
import json
import tempfile
import unittest
from pathlib import Path

import export_data
from app import iso, utc_ms


class FakeCollector:
    def __init__(self, first_ms):
        self.first_ms = first_ms
        self.raw_calls = 0

    def _pace_raw_request(self):
        pass

    def request(self, path, params=None):
        if path == "/members":
            return {"members": [{"participantId": "P-TEST01", "status": "active"}]}
        if path.endswith("/stats"):
            return {"participantId": "P-TEST01", "firstRecord": iso(self.first_ms),
                    "lastRecord": iso(self.first_ms + 1000), "totalSamples": 2}
        if path.endswith("/sessions"):
            return {"sessions": [
                {"date": "2026-05-01", "startTime": iso(self.first_ms),
                 "endTime": iso(self.first_ms + 1000), "sampleCount": 1},
                {"date": "2026-05-01", "startTime": iso(self.first_ms + 1000),
                 "endTime": iso(self.first_ms + 2000), "sampleCount": 1},
            ], "pagination": {"count": 2}}
        if path.endswith("/data"):
            self.raw_calls += 1
            lower, upper = utc_ms(params["start"]), utc_ms(params["end"])
            records = [{"timestamp": iso(at), "participantId": "P-TEST01", "channel": "RIGHT_TEMP",
                        "samplingRate": 256, "waveform": [value] * 256}
                       for at, value in ((self.first_ms, 1), (self.first_ms + 1000, 2))
                       if lower <= at <= upper]
            return {"data": records, "pagination": {"hasMore": False}}
        if path == "/usage":
            return {"keyId": "test", "usage": {"raw_data": {"used": self.raw_calls, "limit": 60}}}
        raise AssertionError(path)


class ExportTests(unittest.TestCase):
    def test_snapshot_has_matching_json_csv_and_manifest(self):
        first_ms = utc_ms("2026-05-01T00:00:00Z")
        with tempfile.TemporaryDirectory() as temporary:
            target = export_data.run_export(FakeCollector(first_ms), Path(temporary) / "data",
                                            requested_end=first_ms + 3600_000)
            self.assertTrue(target.is_dir())
            raw = json.loads((target / "P-TEST01" / "raw.json").read_text())
            with (target / "P-TEST01" / "raw.csv").open(newline="") as source:
                rows = list(csv.DictReader(source))
            self.assertEqual(len(raw), len(rows))
            self.assertEqual(len(raw), 2)
            self.assertEqual(rows[1]["sample_255"], "2")
            self.assertEqual(json.loads((target / "manifest.json").read_text())
                             ["participants"][0]["rawRecords"], 2)
            self.assertEqual(json.loads((target / "manifest.json").read_text())
                             ["participants"][0]["sessions"], 2)
            for name in ("members", "usage"):
                self.assertTrue((target / f"{name}.json").exists())
                self.assertTrue((target / f"{name}.csv").exists())
            for name in ("stats", "sessions"):
                self.assertTrue((target / "P-TEST01" / f"{name}.json").exists())
                self.assertTrue((target / "P-TEST01" / f"{name}.csv").exists())

    def test_incomplete_raw_window_does_not_publish_snapshot(self):
        first_ms = utc_ms("2026-05-01T00:00:00Z")

        class BrokenCollector(FakeCollector):
            def request(self, path, params=None):
                if path.endswith("/data"):
                    return {"data": [], "pagination": {"hasMore": True}}
                return super().request(path, params)

        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary) / "data"
            with self.assertRaisesRegex(RuntimeError, "incomplete data window"):
                export_data.run_export(BrokenCollector(first_ms), data_dir,
                                       requested_end=first_ms + 1000)
            self.assertEqual(list(data_dir.iterdir()), [])

    def test_distinct_readings_at_same_timestamp_are_preserved(self):
        first_ms = utc_ms("2026-05-01T00:00:00Z")

        class DuplicateTimeCollector(FakeCollector):
            def request(self, path, params=None):
                if path.endswith("/data"):
                    return {"data": [
                        {"timestamp": iso(first_ms), "participantId": "P-TEST01",
                         "channel": "RIGHT_TEMP", "samplingRate": 256, "waveform": [1] * 256},
                        {"timestamp": iso(first_ms), "participantId": "P-TEST01",
                         "channel": "RIGHT_TEMP", "samplingRate": 256, "waveform": [2] * 256},
                    ], "pagination": {"hasMore": False}}
                return super().request(path, params)

        with tempfile.TemporaryDirectory() as temporary:
            target = export_data.run_export(DuplicateTimeCollector(first_ms), Path(temporary),
                                            requested_end=first_ms + 1000)
            raw = json.loads((target / "P-TEST01" / "raw.json").read_text())
            self.assertEqual(len(raw), 2)
            self.assertNotEqual(raw[0]["waveform"], raw[1]["waveform"])

    def test_data_without_start_timestamp_fails_instead_of_exporting_empty_files(self):
        first_ms = utc_ms("2026-05-01T00:00:00Z")

        class MissingTimestampCollector(FakeCollector):
            def request(self, path, params=None):
                if path.endswith("/stats"):
                    return {"participantId": "P-TEST01", "totalSamples": 512}
                return super().request(path, params)

        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary) / "data"
            with self.assertRaisesRegex(RuntimeError, "no first-record timestamp"):
                export_data.run_export(MissingTimestampCollector(first_ms), data_dir,
                                       requested_end=first_ms + 3600_000)
            self.assertEqual(list(data_dir.iterdir()), [])

    def test_reported_recordings_cannot_produce_an_empty_raw_export(self):
        first_ms = utc_ms("2026-05-01T00:00:00Z")

        class EmptyRawCollector(FakeCollector):
            def request(self, path, params=None):
                if path.endswith("/data"):
                    return {"data": [], "pagination": {"hasMore": False}}
                return super().request(path, params)

        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary) / "data"
            with self.assertRaisesRegex(RuntimeError, "raw export is empty"):
                export_data.run_export(EmptyRawCollector(first_ms), data_dir,
                                       requested_end=first_ms + 3600_000)
            self.assertEqual(list(data_dir.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
