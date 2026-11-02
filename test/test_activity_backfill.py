import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import app
import backfill_activity_labels as backfill


class ActivityBackfillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original = app.DB_PATH
        app.DB_PATH = Path(self.temp.name)/"test.sqlite3"
        app.setup_db()
        self.participant = "P-TEST"

    def tearDown(self):
        app.DB_PATH = self.original
        self.temp.cleanup()

    def records(self, *times):
        with app.db() as connection:
            connection.executemany("""INSERT INTO readings
                (participant_id,timestamp_ms,waveform_json,sample_count,mean,rms,peak_to_peak)
                VALUES (?,?,?,2,0,1,2)""",
                [(self.participant, app.utc_ms(value), "[-1,1]") for value in times])

    def suggestion(self, start, end):
        with app.db() as connection:
            return connection.execute("""INSERT INTO labels
                (participant_id,label,note,start_ms,end_ms,source,reviewed,batch_id)
                VALUES (?, 'Work (Misc)','Suggested',?,?,'schedule',0,'test-batch')""",
                (self.participant,start,end)).lastrowid

    def test_work_hours_weekdays_existing_labels_and_idempotence(self):
        self.records("2026-10-19T11:59:59Z", "2026-10-19T12:00:00Z", "2026-10-19T12:10:00Z",
                     "2026-10-19T12:20:00Z", "2026-10-19T19:59:59Z", "2026-10-19T20:00:00Z",
                     "2026-10-17T12:00:00Z")
        start = app.utc_ms("2026-10-19T12:10:00Z")
        label_id = app.add_label(self.participant,"Reading","Original",start,start+5*60_000)
        with app.db() as connection:
            original = dict(connection.execute("SELECT * FROM labels WHERE id=?",(label_id,)).fetchone())
            plan = backfill.plan(connection,self.participant)
        self.assertEqual(plan["eligibleRecords"],4)
        self.assertEqual(plan["newlyCoveredRecords"],3)
        self.assertEqual(plan["newLabels"],3)
        result = backfill.apply(self.participant,True,Path(self.temp.name)/"audit")
        self.assertEqual(result["status"],"applied")
        with app.db() as connection:
            self.assertEqual(dict(connection.execute("SELECT * FROM labels WHERE id=?",(label_id,)).fetchone()),original)
            self.assertEqual(backfill.plan(connection,self.participant)["newLabels"],0)
            self.assertEqual(backfill.plan(connection,self.participant,False)["newLabels"],1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM readings").fetchone()[0],7)
        self.assertEqual(json.loads(Path(result["beforeSnapshot"]).read_text()),[original])

    def test_timezone_work_hours_across_daylight_saving(self):
        # Friday is EST; the following Monday is EDT.
        self.records("2026-03-06T12:59:59Z", "2026-03-06T13:00:00Z",
                     "2026-03-09T11:59:59Z", "2026-03-09T12:00:00Z")
        with app.db() as connection:
            plan=backfill.plan(connection,self.participant)
        self.assertEqual(plan["eligibleRecords"],2)
        self.assertEqual([row["start"] for row in plan["intervals"]],
                         ["2026-03-06T13:00:00.000Z","2026-03-09T12:00:00.000Z"])

    def test_five_minute_break_splits_only_pending_suggestions(self):
        start=app.utc_ms("2026-10-19T12:00:00Z")
        self.suggestion(start,start+30*60_000)
        app.add_label(self.participant,"Relaxing","Five-minute break",start+10*60_000,start+15*60_000)
        with app.db() as connection:
            rows=connection.execute("SELECT label,start_ms,end_ms,source,reviewed FROM labels ORDER BY start_ms").fetchall()
        self.assertEqual([tuple(row) for row in rows],[
            ("Work (Misc)",start,start+10*60_000,"schedule",0),
            ("Relaxing",start+10*60_000,start+15*60_000,"manual",1),
            ("Work (Misc)",start+15*60_000,start+30*60_000,"schedule",0)])
        with self.assertRaisesRegex(ValueError,"overlaps"):
            app.add_label(self.participant,"Other","",start,start+30*60_000)
        with app.db() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM labels").fetchone()[0],3)

    def test_review_shorter_interval_preserves_other_suggestions_and_protects_it(self):
        start=app.utc_ms("2026-10-19T12:00:00Z")
        label_id=self.suggestion(start,start+30*60_000)
        result=app.add_label(self.participant,"Relaxing","Reviewed",start+5*60_000,start+10*60_000,label_id)
        self.assertEqual(result,label_id)
        reviewed=app.label_review(self.participant,"all")["labels"]
        self.assertEqual(len(reviewed),3)
        chosen=next(row for row in reviewed if row["id"]==label_id)
        self.assertTrue(chosen["reviewed"])
        self.assertEqual(app.label_review(self.participant,"suggested")["total"],2)
        with self.assertRaisesRegex(ValueError,"overlaps"):
            app.add_label(self.participant,"New","",start,start+30*60_000)
        with self.assertRaisesRegex(ValueError,"not found"):
            app.add_label("P-OTHER","Wrong participant","",start,start+1000,label_id)

    def test_legacy_annotations_migrate_as_protected(self):
        legacy=Path(self.temp.name)/"legacy.sqlite3"
        with closing(sqlite3.connect(legacy)) as connection:
            with connection:
                connection.execute("""CREATE TABLE labels (id INTEGER PRIMARY KEY,participant_id TEXT,
                    label TEXT,note TEXT,start_ms INTEGER,end_ms INTEGER)""")
                connection.execute("INSERT INTO labels VALUES (1,'P-OLD','Reading','Keep note',1000,2000)")
        app.DB_PATH=legacy
        app.setup_db()
        with app.db() as connection:
            row=connection.execute("SELECT * FROM labels").fetchone()
        self.assertEqual((row["label"],row["note"],row["source"],row["reviewed"]),
                         ("Reading","Keep note","manual",1))


if __name__ == "__main__":
    unittest.main()
