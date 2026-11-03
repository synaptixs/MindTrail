"""Independent daily lane, time resolution and quality projection contracts."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import app
import daily_review as review
from analysis_manager import AnalysisManager,write_json,remove_quality_payloads


class DailyReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.old=app.DATA_ROOT,app.DB_PATH
        self.addCleanup(self.restore);app.configure_storage(self.root);app.setup_db()
    def restore(self):app.DATA_ROOT,app.DB_PATH=self.old

    def test_wall_time_missing_and_repeated_hour_and_millisecond_roundtrip(self):
        with self.assertRaisesRegex(ValueError,'does not exist'):review.wall_time('2026-03-08T02:30:00.123')
        choices=review.wall_time('2026-11-01T01:30:00.123')['choices']
        self.assertEqual([c['offset'] for c in choices],['-04:00','-05:00'])
        self.assertEqual([c['utc'] for c in choices],['2026-11-01T05:30:00.123Z','2026-11-01T06:30:00.123Z'])
        self.assertEqual(choices[1]['ms']-choices[0]['ms'],3600000)
        self.assertEqual(len(review.wall_time('2026-03-08T03:00:00')['choices']),1)
        self.assertEqual(review.wall_time('1969-12-31T19:00:00')['choices'][0]['ms'],0)
        for bad in ('2026-11-01','2026-02-30T01:00:00','2026-11-01T01:30:00Z','9999-12-31T23:00:00'):
            with self.assertRaises(ValueError):review.wall_time(bad)
        base=app.utc_ms('2026-11-01T06:30:00Z')
        for stamp in (base,review.epoch('9998-10-31T12:00:00Z')):
            for ms in range(stamp,stamp+1000):self.assertEqual(app.utc_ms(app.iso(ms)),ms)

    def test_lane_partition_clips_gaps_and_never_changes_saved_labels(self):
        start=app.utc_ms('2026-10-30T23:00:00Z')
        with app.db() as c:
            c.executemany("INSERT INTO readings(participant_id,timestamp_ms,device_id,waveform_json,sample_count,mean,rms,peak_to_peak) VALUES('A',?,'D','[-1,1]',2,0,1,2)",[(start,),(start+1000,),(start+5000,)])
            c.execute("INSERT INTO readings(participant_id,timestamp_ms,waveform_json,sample_count,mean,rms,peak_to_peak) VALUES('B',?,'[-1,1]',2,0,99,2)",(start+2000,))
        label_id=app.add_label('A','Reading','Retain',start+123,start+4567)
        with app.db() as c:before=dict(c.execute('SELECT * FROM labels WHERE id=?',(label_id,)).fetchone())
        with app.db() as c:c.execute("INSERT INTO labels(participant_id,label,start_ms,end_ms) VALUES('A','Legacy overlap',?,?)",(start+1000,start+2000))
        view=app.daily_signal('A','2026-10-30',start+500,start+5500)
        runs=view['lanes']['recording']
        self.assertEqual([(r['startMs']-start,r['endMsExclusive']-start,r['state']) for r in runs],[(500,2000,'recorded'),(2000,5000,'missing'),(5000,5500,'recorded')])
        self.assertEqual(view['lanes']['activityTrackCount'],2)
        self.assertEqual(view['lanes']['recordedMs'],2000);self.assertEqual(view['coveragePct'],40)
        self.assertEqual(view['lanes']['activities'][0]['startMs'],start+500)
        self.assertEqual(view['lanes']['activities'][0]['start'],app.iso(start+123))
        with app.db() as c:self.assertEqual(dict(c.execute('SELECT * FROM labels WHERE id=?',(label_id,)).fetchone()),before)

    def test_fractional_source_second_is_split_at_calendar_midnight(self):
        first=app.utc_ms('2026-11-01T03:59:59.500Z')
        with app.db() as c:
            c.executemany("INSERT INTO readings(participant_id,timestamp_ms,device_id,waveform_json,sample_count,mean,rms,peak_to_peak) VALUES('A',?,'D','[-1,1]',2,0,1,2)",[(first,),(first+1000,)])
            days=app.recorded_days(c,'A',{'2026-10-31','2026-11-01'})
        by_date={day['date']:day for day in days}
        self.assertEqual(by_date['2026-10-31']['end'],'2026-11-01T04:00:00.000Z')
        self.assertEqual(by_date['2026-10-31']['lastRecord'],app.iso(first))
        self.assertEqual(by_date['2026-11-01']['start'],'2026-11-01T04:00:00.000Z')
        self.assertEqual(sum(day['lanes']['recordedMs'] for day in days),2000)

    def test_quality_union_uses_exact_flags_and_splits_calendar_midnight(self):
        start=app.utc_ms('2026-11-01T03:59:58Z')
        report={'windows':[{'startMs':start,'endMsExclusive':start+4000,'qualityFlags':[]},
                           {'startMs':start+1000,'endMsExclusive':start+3000,'qualityFlags':['flatline']},
                           {'startMs':start+2000,'endMsExclusive':start+4000,'qualityFlags':['relative_large_step']}]}
        days=review.quality_days(report)
        self.assertEqual(list(days),['2026-10-31','2026-11-01'])
        runs=[r for values in days.values() for r in values]
        self.assertEqual(sum(r['endMsExclusive']-r['startMs'] for r in runs),4000)
        self.assertEqual(sum(r['endMsExclusive']-r['startMs'] for r in runs if r['state']=='flagged'),3000)
        self.assertTrue(any(r['flags']==['flatline','relative_large_step'] for r in runs))
        self.assertEqual(days,review.quality_days(report))

    def test_quality_reader_is_scoped_stale_safe_and_fills_unanalyzed_time(self):
        manager=AnalysisManager(self.root,app.DB_PATH);self.addCleanup(manager.close)
        folder=manager.directory('a'*32);folder.mkdir(parents=True)
        signature=manager.signature()
        write_json(manager.root/(manager.scope('A')+'.json'),{'id':'a'*32,'signature':signature,'settings':{}})
        write_json(folder/'summary.json',{'analysisIdentity':'identity'})
        write_json(folder/'quality-index.json',{'version':1,'participantId':'A','analysisIdentity':'identity','dates':['2026-11-01']})
        write_json(folder/'quality-2026-11-01.json',{'version':1,'participantId':'A','analysisIdentity':'identity','runs':[{'startMs':2000,'endMsExclusive':4000,'state':'flagged','flags':['flatline']}]})
        result=manager.daily_quality('A','2026-11-01',1000,5000)
        self.assertEqual(result['status'],'current')
        self.assertEqual([r['state'] for r in result['runs']],['not_analyzed','flagged','not_analyzed'])
        self.assertEqual(manager.daily_quality('B','2026-11-01',1000,5000)['status'],'unavailable')
        with patch.object(manager,'signature',return_value='changed'):
            stale=manager.daily_quality('A','2026-11-01',1000,5000);self.assertEqual(stale['status'],'stale');self.assertEqual(len(stale['runs']),1)
        payload=json.loads((folder/'quality-2026-11-01.json').read_text());payload['participantId']='B';write_json(folder/'quality-2026-11-01.json',payload)
        self.assertEqual(manager.daily_quality('A','2026-11-01',1000,5000)['status'],'unavailable')

    def test_empty_reader_wal_is_not_a_source_change_but_frames_are(self):
        database=self.root/'signature.sqlite3';database.write_bytes(b'synthetic database signature fixture')
        manager=AnalysisManager(self.root,database);self.addCleanup(manager.close)
        before=manager.signature();wal=Path(str(database)+'-wal')
        wal.write_bytes(b'');self.assertEqual(manager.signature(),before)
        wal.write_bytes(b'nonempty frames');self.assertNotEqual(manager.signature(),before)
        wal.unlink();database.write_bytes(b'changed database');self.assertNotEqual(manager.signature(),before)

    def test_generated_quality_cleanup_keeps_unrelated_files(self):
        directory=self.root/'generated';directory.mkdir()
        for name in ('quality-index.json','quality-2026-11-01.json','quality-2026-11-01.tmp','quality-user-export.json','raw.json'):(directory/name).write_text('{}')
        remove_quality_payloads(directory)
        self.assertEqual({p.name for p in directory.iterdir()},{'quality-user-export.json','raw.json'})

    def test_pure_browser_clock_geometry_contracts(self):
        result=subprocess.run(['node','--test',str(Path(__file__).parent/'daily_time.test.cjs')],capture_output=True,text=True,env={**os.environ,'TZ':'Asia/Tokyo'})
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
