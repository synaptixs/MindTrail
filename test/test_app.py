import json
import math
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.original_path = app.DB_PATH
        app.DB_PATH = Path(self.directory.name) / "test.sqlite3"
        app.setup_db()

    def tearDown(self):
        app.DB_PATH = self.original_path
        self.directory.cleanup()

    def test_local_env_key_and_shell_precedence(self):
        path = Path(self.directory.name) / ".env"
        with patch.dict(os.environ, {"AWEAR_API_KEY": ""}):
            self.assertEqual(app.read_api_key(path), "")
            path.write_text("# Local configuration\nOTHER=value\nAWEAR_API_KEY=example-file-key\n",
                            encoding="utf-8")
            self.assertEqual(app.read_api_key(path), "example-file-key")
            path.write_text('AWEAR_API_KEY="example-quoted-key"\n', encoding="utf-8")
            self.assertEqual(app.read_api_key(path), "example-quoted-key")
        with patch.dict(os.environ, {"AWEAR_API_KEY": "example-shell-key"}):
            self.assertEqual(app.read_api_key(path), "example-shell-key")

    def test_labels_are_intervals_and_cannot_overlap(self):
        first = app.add_label("P-TEST01", "Reading", "", 1_000, 2_000)
        self.assertGreater(first, 0)
        second = app.add_label("P-TEST01", "Walking", "", 2_000, 3_000)
        self.assertGreater(second, first)
        with self.assertRaisesRegex(ValueError, "overlaps"):
            app.add_label("P-TEST01", "Work", "", 1_500, 2_500)

    def test_sync_splits_full_windows_without_a_cursor(self):
        start_ms = int(time.time() * 1000) - 30 * 60 * 1000
        first_ms = start_ms + 60_000
        second_ms = start_ms + 20 * 60_000
        with app.db() as connection:
            app.put_setting(connection, "participant_id", "P-TEST01")
            app.put_setting(connection, "sync_start_ms", str(start_ms))

        collector = app.Collector("fake-key")
        calls = []
        waveform = [1.0] * 256

        def fake_request(path, params):
            calls.append(params.copy())
            self.assertNotIn("cursor", params)
            lower, upper = app.utc_ms(params["start"]), app.utc_ms(params["end"])
            if upper - lower > 20 * 60_000:
                return {"data": [{"timestamp": app.iso(first_ms), "waveform": waveform}],
                        "pagination": {"hasMore": True, "nextCursor": "broken"}}
            return {"data": [{"timestamp": app.iso(timestamp), "waveform": waveform}
                             for timestamp in (first_ms, second_ms) if lower <= timestamp <= upper],
                    "pagination": {"hasMore": False}}

        collector.request = fake_request
        collector._pace_raw_request = lambda: None
        self.assertEqual(collector.sync(), 2)
        self.assertEqual(collector.sync(), 0)
        self.assertGreaterEqual(len(calls), 4)
        with app.db() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM readings").fetchone()[0], 2)
            self.assertGreater(int(app.setting(connection, "sync_checkpoint_ms")), start_ms)

    def test_incomplete_one_second_window_stops_without_checkpoint(self):
        collector = app.Collector("fake-key")
        collector._pace_raw_request = lambda: None
        collector.request = lambda path, params: {"data": [], "pagination": {"hasMore": True}}
        with self.assertRaisesRegex(RuntimeError, "cannot be downloaded completely"):
            collector._fetch_complete_window("P-TEST01", 1_000, 2_000)
        with app.db() as connection:
            self.assertEqual(app.setting(connection, "sync_checkpoint_ms"), "")

    def test_summary_uses_half_open_label_interval(self):
        participant = "P-TEST01"
        base = app.utc_ms("2026-05-01T00:00:00Z")
        with app.db() as connection:
            connection.executemany("""INSERT INTO readings
                (participant_id,timestamp_ms,waveform_json,sample_count,mean,rms,peak_to_peak)
                VALUES (?,?,?,?,?,?,?)""", [
                (participant, base, json.dumps([1]*256), 256, 1, 1, 0),
                (participant, base+1000, json.dumps([2]*256), 256, 2, 2, 0),
            ])
        app.add_label(participant, "Reading", "", base, base+1000)
        result = app.summary(participant, base, base+2000)
        self.assertEqual(result["stats"]["records"], 2)
        self.assertEqual(result["activities"][0]["records"], 1)
        self.assertEqual(result["activities"][0]["rms"], 1)
        self.assertEqual(result["stats"]["coveragePct"], 100.0)

    def test_summary_zoom_resolves_seconds_and_preserves_window_boundaries(self):
        participant = "P-ZOOM"
        base = app.utc_ms("2026-05-01T00:00:00Z")
        with app.db() as connection:
            connection.executemany("""INSERT INTO readings
                (participant_id,timestamp_ms,waveform_json,sample_count,mean,rms,peak_to_peak)
                VALUES (?,?,?,?,?,?,?)""", [
                (participant, base+offset, json.dumps([-value,value]), 2, 0, value, 2*value)
                for offset,value in ((10000,1),(11000,3),(60000,5),(70000,7))
            ])
        zoom = app.summary(participant, base+10000, base+70000)
        self.assertEqual(zoom["window"]["bucketMs"], 1000)
        self.assertEqual(zoom["stats"]["records"], 3)
        self.assertEqual([point["acRms"] for point in zoom["series"]], [1,3,5])
        week = app.summary(participant, base, base+7*24*60*60*1000)
        self.assertEqual(week["window"]["bucketMs"], 42*60*1000)
        self.assertLessEqual(len(week["series"]), 241)
        self.assertEqual(sum(point["records"] for point in week["series"]), 4)
        self.assertAlmostEqual(week["stats"]["acRms"], math.sqrt(21))

    def test_all_graphs_include_before_and_after_work_and_unassigned_time(self):
        participant = "P-ALL-HOURS"
        start = app.utc_ms("2026-10-28T04:00:00Z")
        end = app.utc_ms("2026-10-29T04:00:00Z")
        stamps = [app.utc_ms(value) for value in ("2026-10-28T11:30:00Z",  # 07:30 Eastern
                  "2026-10-28T12:00:00Z", "2026-10-28T19:59:59Z",  # 08:00, 15:59:59
                  "2026-10-28T20:00:00Z", "2026-10-28T22:01:43Z")]  # 16:00, 18:01:43
        with app.db() as connection:
            connection.executemany("""INSERT INTO readings
                (participant_id,timestamp_ms,waveform_json,sample_count,mean,rms,peak_to_peak)
                VALUES (?,?, '[-1,1]',2,0,?,2)""",
                [(participant, stamp, value) for stamp,value in zip(stamps, (1,2,4,8,16))])
            connection.execute("""INSERT INTO labels
                (participant_id,label,start_ms,end_ms,source,reviewed)
                VALUES (?,'Work (Misc)',?,?,'schedule',0)""", (participant, stamps[1], stamps[3]))
            # Another participant's label must not absorb this participant's unassigned time.
            connection.execute("""INSERT INTO labels (participant_id,label,start_ms,end_ms)
                VALUES ('P-OTHER','Other',?,?)""", (start,end))
        result = app.summary(participant,start,end)
        self.assertEqual(result["stats"]["records"],5)
        self.assertEqual(sum(point["records"] for point in result["series"]),5)
        self.assertEqual(result["stats"]["first"],app.iso(stamps[0]))
        self.assertEqual(result["stats"]["last"],app.iso(stamps[-1]))
        unassigned = next(row for row in result["activities"] if row["unassigned"])
        self.assertEqual(unassigned["label"],"No activity assigned")
        self.assertEqual(unassigned["records"],3)
        self.assertAlmostEqual(unassigned["acRms"],math.sqrt((1+64+256)/3))
        self.assertEqual(sum(row["records"] for row in result["activities"]),5)
        periods = app.recorded_intervals(participant,end,now_ms=end)["intervals"]
        self.assertEqual(sum(period["records"] for period in periods),5)
        self.assertEqual(sum(point["records"] for period in periods
                             for segment in period["preview"]["segments"] for point in segment),5)
        self.assertEqual(periods[0]["labels"],[])
        self.assertEqual(periods[-1]["labels"],[])
        # A narrowed evening window still has a comparison even when no label overlaps it.
        evening = app.summary(participant,stamps[3],end)
        self.assertEqual(len(evening["activities"]),1)
        self.assertEqual(evening["activities"][0]["records"],2)
        self.assertTrue(evening["activities"][0]["unassigned"])
        with app.db() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM labels WHERE participant_id=?",(participant,)).fetchone()[0],1)

    def test_centered_amplitude_excludes_large_baseline(self):
        participant = "P-TEST01"
        base = app.utc_ms("2026-05-01T00:00:00Z")
        waveform = [800 + 10 * math.sin(2 * math.pi * 10 * i / 256) for i in range(256)]
        mean = sum(waveform) / len(waveform)
        rms = math.sqrt(sum(value * value for value in waveform) / len(waveform))
        with app.db() as connection:
            connection.execute("""INSERT INTO readings
                (participant_id,timestamp_ms,waveform_json,sample_count,mean,rms,peak_to_peak)
                VALUES (?,?,?,?,?,?,?)""",
                (participant, base, json.dumps(waveform), 256, mean, rms,
                 max(waveform) - min(waveform)))
        result = app.summary(participant, base, base + 1000)
        expected = 10 / math.sqrt(2)
        self.assertAlmostEqual(result["stats"]["acRms"], expected, places=6)
        self.assertAlmostEqual(result["series"][0]["acRms"], expected, places=6)
        self.assertGreater(result["stats"]["meanRms"], 800)

    def test_eeg_window_preserves_gaps_and_activity_alignment(self):
        participant = "P-TEST01"
        base = app.utc_ms("2026-05-01T00:00:00Z")
        with app.db() as connection:
            connection.executemany("""INSERT INTO readings
                (participant_id,timestamp_ms,device_id,waveform_json,sample_count,mean,rms,peak_to_peak)
                VALUES (?,?,?,?,?,?,?,?)""", [
                (participant, base, "DEVICE-1", "[1,2]", 2, 1.5, 1.58, 1),
                (participant, base + 2000, "DEVICE-1", "[3,4]", 2, 3.5, 3.54, 1),
            ])
        app.add_label(participant, "Reading", "", base + 1000, base + 3000)
        result = app.eeg_window(participant, base, 10)
        self.assertEqual([row["timestamp"] for row in result["records"]],
                         [app.iso(base), app.iso(base + 2000)])
        self.assertEqual(result["records"][1]["samples"], [3, 4])
        self.assertEqual(result["labels"][0]["label"], "Reading")
        self.assertEqual(result["samplingRate"], 256)
        with self.assertRaisesRegex(ValueError, "10, 30, or 60"):
            app.eeg_window(participant, base, 3600)

    def test_pattern_report_checks_source_and_reads_current_labels(self):
        participant = "P-TEST01"
        base = app.utc_ms("2026-05-01T00:00:00Z")
        with app.db() as connection:
            connection.execute("""INSERT INTO readings
                (participant_id,timestamp_ms,waveform_json,sample_count,mean,rms,peak_to_peak)
                VALUES (?,?,?,?,?,?,?)""", (participant, base, "[1]", 1, 1, 1, 0))
        original_root = app.ROOT
        app.ROOT = Path(self.directory.name)
        try:
            path = app.pattern_report_path(participant)
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"participantId": participant,
                "sourceDatabase": app.DB_PATH.name, "source": {"records": 1,
                "first": app.iso(base), "last": app.iso(base)}, "windows": []}), encoding="utf-8")
            self.assertTrue(app.pattern_report(participant)["sourceCurrent"])
            app.add_label(participant, "Reading", "", base, base + 1000)
            self.assertEqual(app.pattern_report(participant)["labels"][0]["label"], "Reading")
            with app.db() as connection:
                connection.execute("""INSERT INTO readings
                    (participant_id,timestamp_ms,waveform_json,sample_count,mean,rms,peak_to_peak)
                    VALUES (?,?,?,?,?,?,?)""", (participant, base+1000, "[1]", 1, 1, 1, 0))
            self.assertFalse(app.pattern_report(participant)["sourceCurrent"])
            self.assertFalse(app.pattern_report("P-OTHER")["available"])
        finally:
            app.ROOT = original_root

    def test_recorded_half_hour_pages_and_label_overlap(self):
        participant = "P-TEST01"
        slot = app.ACTIVITY_INTERVAL_MS
        base = app.utc_ms("2026-05-01T00:00:00Z")
        timestamps = (base + 1000, base + slot - 1000, base + slot,
                      base + 2*slot + 1000)
        with app.db() as connection:
            connection.executemany("""INSERT INTO readings
                (participant_id,timestamp_ms,waveform_json,sample_count,mean,rms,peak_to_peak)
                VALUES (?,?,?,?,?,?,?)""", [
                (participant, timestamp, "[1]", 1, 1, 1, 0) for timestamp in timestamps
            ])
        label_id = app.add_label(participant, "Reading", "Original note", base + slot + 1234, base + 2*slot + 2345)
        first = app.recorded_intervals(participant, base + 3*slot, limit=2,
                                       now_ms=base + 3*slot)
        self.assertEqual([x["records"] for x in first["intervals"]], [1, 1])
        self.assertEqual(first["intervals"][0]["labels"][0]["id"], label_id)
        label = first["intervals"][1]["labels"][0]
        self.assertEqual(label, {"id": label_id, "label": "Reading", "note": "Original note",
                                "start": app.iso(base + slot + 1234), "end": app.iso(base + 2*slot + 2345),
                                "source": "manual", "reviewed": True,
                                "activityId": label["activityId"], "recordedLabel": "Reading"})
        self.assertTrue(label["activityId"])
        # Both overlapping rows edit the same complete activity, not a clipped half-hour copy.
        self.assertEqual(first["intervals"][0]["labels"][0], label)
        app.add_label(participant, "Reading updated", label["note"], app.utc_ms(label["start"]),
                      app.utc_ms(label["end"]), label["id"])
        updated = app.recorded_intervals(participant, base + 3*slot, limit=2, now_ms=base + 3*slot)
        self.assertEqual(updated["intervals"][0]["labels"][0]["label"], "Reading updated")
        self.assertEqual(first["nextBefore"], app.iso(base + slot))
        older = app.recorded_intervals(participant, app.utc_ms(first["nextBefore"]),
                                       limit=2, now_ms=base + 3*slot)
        self.assertEqual(len(older["intervals"]), 1)
        self.assertEqual(older["intervals"][0]["records"], 2)
        self.assertIsNone(older["nextBefore"])

    def test_interval_preview_centered_power_gaps_devices_and_boundaries(self):
        participant = "P-PREVIEW"
        base = app.utc_ms("2026-10-30T12:00:00Z")
        slot = app.ACTIVITY_INTERVAL_MS
        with app.db() as connection:
            connection.executemany("""INSERT INTO readings
                (participant_id,timestamp_ms,device_id,waveform_json,sample_count,mean,rms,peak_to_peak)
                VALUES (?,?,?,?,2,?,?,2)""", [
                (participant, base + offset, device, "[-1,1]", mean, rms)
                for offset, device, mean, rms in (
                    (0, "A", 100, math.sqrt(10000 + 9)),
                    (1000, "A", 100, math.sqrt(10000 + 49)),
                    (3000, "A", 0, 2),  # A missing second splits even one ten-second group.
                    (4000, "B", 0, 4),  # A different device also starts a new segment.
                    (9000, "B", 0, 6), (10000, "B", 0, 8),
                    (slot - 1000, "B", 0, 0), (slot, "B", 0, 12))
            ])
            connection.execute("""INSERT INTO readings
                (participant_id,timestamp_ms,waveform_json,sample_count,mean,rms,peak_to_peak)
                VALUES ('P-OTHER',?,'[99]',1,0,99,0)""", (base + 2000,))
        result = app.recorded_intervals(participant, base + slot, now_ms=base + slot)
        self.assertEqual(len(result["intervals"]), 1)
        preview = result["intervals"][0]["preview"]
        self.assertEqual(preview["bucketMs"], 10_000)
        self.assertEqual([len(segment) for segment in preview["segments"]], [1, 1, 1, 2, 1])
        first = preview["segments"][0][0]
        self.assertAlmostEqual(first["acRms"], math.sqrt(29))
        self.assertEqual(first["records"], 2)
        self.assertEqual(first["start"], app.iso(base))
        self.assertEqual(first["end"], app.iso(base + 2000))
        self.assertEqual(preview["coverage"][0], {"start": app.iso(base), "end": app.iso(base + 2000)})
        self.assertEqual(preview["coverage"][-1]["end"], app.iso(base + slot))
        self.assertEqual(preview["segments"][-1][0]["acRms"], 0)
        self.assertEqual(sum(point["records"] for segment in preview["segments"] for point in segment), 7)
        clipped = app.recorded_intervals(participant, base + 10_000, now_ms=base + 10_000)["intervals"][0]
        self.assertFalse(clipped["complete"])
        self.assertEqual(clipped["preview"]["coverage"][-1]["end"], app.iso(base + 10_000))
        newest = app.recorded_intervals(participant, base + 2*slot, limit=1)["intervals"][0]
        self.assertEqual(newest["preview"]["segments"][0][0]["acRms"], 12)
        self.assertEqual(newest["preview"]["coverage"], [{"start": app.iso(base + slot), "end": app.iso(base + slot + 1000)}])

    def test_daily_chart_spans_entire_day_across_pages_and_extends_with_new_data(self):
        participant = "P-DAILY"
        before = app.utc_ms("2026-10-31T04:00:00Z")
        first = app.utc_ms("2026-10-30T11:52:31Z")
        last = app.utc_ms("2026-10-30T19:42:33Z")
        def insert(stamps):
            with app.db() as connection:
                connection.executemany("""INSERT INTO readings
                    (participant_id,timestamp_ms,waveform_json,sample_count,mean,rms,peak_to_peak)
                    VALUES (?,?,'[-2,2]',2,0,2,4)""", [(participant, stamp) for stamp in stamps])
        insert([first,first+1000,last])
        label_id = app.add_label(participant,"Reading","Preserve",first,first+2000)
        page = app.recorded_intervals(participant,before,limit=1)
        self.assertEqual(len(page["intervals"]),1)
        self.assertEqual(page["intervals"][0]["records"],1)
        day = page["days"][0]
        self.assertEqual(day["date"],"2026-10-30")
        self.assertEqual(day["timezone"],"America/New_York")
        self.assertEqual(day["start"],app.iso(first))
        self.assertEqual(day["end"],app.iso(last+1000))
        self.assertEqual(day["records"],3)
        self.assertEqual(day["labels"][0]["id"],label_id)
        older = app.recorded_intervals(participant,app.utc_ms(page["nextBefore"]),limit=1)
        self.assertEqual(older["days"],[day])  # No truncated day at a half-hour page boundary.
        earlier, later = first-60_000,app.utc_ms("2026-10-30T22:05:06Z")
        insert([earlier,later])
        refreshed = app.recorded_intervals(participant,before,limit=1)["days"][0]
        self.assertEqual(refreshed["start"],app.iso(earlier))
        self.assertEqual(refreshed["end"],app.iso(later+1000))
        self.assertEqual(refreshed["lastRecord"],app.iso(later))
        self.assertEqual(refreshed["records"],5)
        self.assertEqual(sum(p["records"] for segment in refreshed["preview"]["segments"] for p in segment),5)
        self.assertEqual(refreshed["preview"]["coverage"][-1]["end"],refreshed["end"])
        self.assertEqual(refreshed["labels"],day["labels"])

    def test_daily_chart_calendar_boundaries_follow_new_york_daylight_saving(self):
        for name,first,last,next_day,hours in (
            ("spring","2026-03-08T05:00:00Z","2026-03-09T03:59:59Z","2026-03-09T04:00:00Z",23),
            ("fall","2026-11-01T04:00:00Z","2026-11-02T04:59:59Z","2026-11-02T05:00:00Z",25)):
            with self.subTest(name=name):
                participant = "P-DST-"+name
                stamps = [app.utc_ms(value) for value in (first,last,next_day)]
                with app.db() as connection:
                    connection.executemany("""INSERT INTO readings
                        (participant_id,timestamp_ms,waveform_json,sample_count,mean,rms,peak_to_peak)
                        VALUES (?,?,'[1]',1,0,1,0)""",[(participant,stamp) for stamp in stamps])
                days = app.recorded_intervals(participant,stamps[-1]+1000)["days"]
                self.assertEqual(len(days),2)
                self.assertEqual(days[0]["records"],1)
                self.assertEqual(days[1]["records"],2)
                self.assertEqual(app.utc_ms(days[1]["end"])-app.utc_ms(days[1]["start"]),hours*3600_000)
                self.assertEqual(days[1]["end"],app.iso(stamps[-1]))

    def test_daily_chart_does_not_break_a_contiguous_signal_at_half_hours(self):
        first = app.utc_ms("2026-10-30T12:29:59Z")
        with app.db() as connection:
            connection.executemany("""INSERT INTO readings
                (participant_id,timestamp_ms,device_id,waveform_json,sample_count,mean,rms,peak_to_peak)
                VALUES ('P-CONTIG',?,'A','[1]',1,0,1,0)""",[(first+i*1000,) for i in range(3)])
        day = app.recorded_intervals("P-CONTIG",first+10_000)["days"][0]
        self.assertEqual(len(day["preview"]["segments"]),1)
        self.assertEqual(day["preview"]["coverage"],[{"start":app.iso(first),"end":app.iso(first+3000)}])

    def test_daily_zoom_reaggregates_exact_selected_seconds_and_retains_empty_gaps(self):
        first = app.utc_ms("2026-10-30T12:00:00Z")
        participant = "P-DAILY-ZOOM"
        with app.db() as connection:
            connection.executemany("""INSERT INTO readings
                (participant_id,timestamp_ms,device_id,waveform_json,sample_count,mean,rms,peak_to_peak)
                VALUES (?,?,'A',?,2,100,?,?)""", [
                (participant,first+i*1000,json.dumps([100-i-1,100+i+1]),math.sqrt(10000+(i+1)**2),2*(i+1))
                for i in range(10)])
            connection.execute("""INSERT INTO readings
                (participant_id,timestamp_ms,waveform_json,sample_count,mean,rms,peak_to_peak)
                VALUES ('P-OTHER',?,'[-99,99]',2,0,99,198)""",(first+3000,))
        label_id = app.add_label(participant,"Reading","Original",first+1000,first+4000)
        full = app.recorded_intervals(participant,first+60_000)["days"][0]
        self.assertEqual(full["preview"]["bucketMs"],10_000)
        zoom = app.daily_signal(participant,"2026-10-30",first+3000,first+5000)
        self.assertEqual(zoom["start"],app.iso(first+3000))
        self.assertEqual(zoom["end"],app.iso(first+5000))
        self.assertEqual(zoom["records"],2)
        self.assertEqual(zoom["preview"]["bucketMs"],1000)
        self.assertEqual([round(point["acRms"],6) for point in zoom["preview"]["segments"][0]],[4,5])
        self.assertEqual(zoom["labels"][0]["id"],label_id)
        empty = app.daily_signal(participant,"2026-10-30",first+20_000,first+25_000)
        self.assertEqual(empty["records"],0)
        self.assertEqual(empty["preview"]["segments"],[])
        self.assertEqual(empty["preview"]["coverage"],[])
        self.assertEqual(empty["start"],app.iso(first+20_000))
        self.assertEqual(empty["end"],app.iso(first+25_000))
        last_second = app.daily_signal(participant,"2026-10-30",first+9000,first+10_000)
        self.assertEqual(last_second["records"],1)
        self.assertAlmostEqual(last_second["preview"]["segments"][0][0]["acRms"],10)
        with app.db() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM labels WHERE participant_id=?",(participant,)).fetchone()[0],1)

    def test_daily_zoom_rejects_cross_day_and_invalid_ranges(self):
        lower,upper=app.recording_day_bounds("2026-11-01")
        self.assertEqual(upper-lower,25*3600_000)
        for start,end in ((lower-1000,upper),(lower,upper+1000),(lower,lower),(upper,lower)):
            with self.subTest(start=start,end=end),self.assertRaises(ValueError):
                app.daily_signal("P-TEST","2026-11-01",start,end)
        for date in ("all","2026-02-30","2026-10-30T00:00:00Z"):
            with self.subTest(date=date),self.assertRaises(ValueError):
                app.recording_day_bounds(date)

    def test_csv_formula_prefix(self):
        self.assertEqual(app.safe_csv("=1+1"), "'=1+1")
        self.assertEqual(app.safe_csv("Reading"), "Reading")

    def test_raw_table_keeps_samples_and_pages_with_time_filter(self):
        participant = "P-TEST01"
        base = app.utc_ms("2026-05-01T00:00:00Z")
        with app.db() as connection:
            connection.executemany("""INSERT INTO readings
                (participant_id,timestamp_ms,device_id,waveform_json,sample_count,mean,rms,peak_to_peak)
                VALUES (?,?,?,?,?,?,?,?)""", [
                (participant, base+i*1000, "AWEAR-TEST", json.dumps([i, i+0.125]), 2,
                 i+0.0625, i, 0.125) for i in range(3)
            ])
        app.add_label(participant, "Reading", "", base+1000, base+2000)
        first = app.raw_table(participant, start_ms=base+1000, end_ms=base+3000, limit=1)
        self.assertEqual(first["total"], 2)
        self.assertEqual(first["records"][0]["waveform"], [2, 2.125])
        self.assertEqual(first["records"][0]["device_id"], "AWEAR-TEST")
        self.assertIsNone(first["records"][0]["activity"])
        second = app.raw_table(participant, before_ms=app.utc_ms(first["nextBefore"]),
                               start_ms=base+1000, end_ms=base+3000, limit=1)
        self.assertEqual(second["records"][0]["waveform"], [1, 1.125])
        self.assertEqual(second["records"][0]["activity"], "Reading")
        self.assertIsNone(second["nextBefore"])

    def test_old_archive_gains_device_id_column(self):
        with app.db() as connection:
            connection.execute("ALTER TABLE readings RENAME TO legacy_readings")
            connection.execute("""CREATE TABLE readings (
                participant_id TEXT NOT NULL,timestamp_ms INTEGER NOT NULL,
                waveform_json TEXT NOT NULL,sample_count INTEGER NOT NULL,
                mean REAL NOT NULL,rms REAL NOT NULL,peak_to_peak REAL NOT NULL,
                PRIMARY KEY(participant_id,timestamp_ms))""")
            connection.execute("""INSERT INTO readings VALUES
                ('P-TEST01',1000,'[1]',1,1,1,0)""")
            connection.execute("DROP TABLE legacy_readings")
        app.setup_db()
        with app.db() as connection:
            self.assertIn("device_id", {row[1] for row in connection.execute("PRAGMA table_info(readings)")})
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM readings").fetchone()[0], 1)

    def test_saved_snapshot_tables_selects_latest_complete_export(self):
        original = app.ROOT
        try:
            app.ROOT = Path(self.directory.name)
            older = app.ROOT / "data" / "export-20260501T000000Z"
            folder = older / "P-TEST01"
            folder.mkdir(parents=True)
            (older / "manifest.json").write_text(json.dumps({"exportedAt": "2026-05-01T00:00:00Z",
                "participants": [{"participantId": "P-TEST01"}]}))
            (folder / "sessions.json").write_text(json.dumps([{"date": "2026-05-01", "sampleCount": 10}]))
            (folder / "stats.json").write_text(json.dumps({"totalSamples": 10}))
            (app.ROOT / "data" / "export-20260502T000000Z").mkdir()
            result = app.saved_snapshot_tables("P-TEST01")
            self.assertEqual(result["stats"]["totalSamples"], 10)
            self.assertEqual(len(result["sessions"]), 1)
        finally:
            app.ROOT = original

    def test_accidental_positional_key_is_not_echoed(self):
        example = "example-secret-123"
        result = subprocess.run(
            [sys.executable, str(app.SOURCE_ROOT / "app.py"), "--key-prompt", example],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(example, result.stderr)
        self.assertIn("nothing after it", result.stderr)


if __name__ == "__main__":
    unittest.main()
