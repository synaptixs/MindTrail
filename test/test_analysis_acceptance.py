"""Independent acceptance-oracle and resource-measurement checks, synthetic only."""
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from analysis_acceptance import (changed_snapshot, check_coverage, check_windows, coverage_html,
                                 digest_file, label_digest, percentile95, run_acceptance, validate_budget)
from analysis_manager import read_json
from analysis_resources import peak_memory_bytes
from analysis_worker import AnalysisCancelled, atomic_json, run

HAS_NUMPY = importlib.util.find_spec('numpy') is not None


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.database = self.root / 'source.sqlite3'
        with closing(sqlite3.connect(self.database)) as connection:
            connection.executescript('''CREATE TABLE readings(participant_id TEXT,timestamp_ms INTEGER,
                device_id TEXT,sample_count INTEGER,waveform_json TEXT);
                CREATE TABLE labels(id INTEGER PRIMARY KEY,label TEXT);
                INSERT INTO labels VALUES(1,'Reading <notes>');''')
            connection.executemany('INSERT INTO readings VALUES(?,?,?,?,?)',
                [('P',i*1000,'D',256,json.dumps([i]*256)) for i in range(8)])
            connection.commit()

    def test_changed_snapshot_preserves_reference_and_labels(self):
        before = digest_file(self.database)
        labels = label_digest(self.database)
        target = self.root / 'changes' / 'awear_data.sqlite3'
        self.assertEqual(changed_snapshot(self.database,target,'P'),11)
        self.assertEqual(digest_file(self.database),before)
        self.assertEqual(label_digest(target),labels)
        with closing(sqlite3.connect(target)) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM readings WHERE timestamp_ms=1000').fetchone()[0],0)
            self.assertEqual(json.loads(connection.execute('SELECT waveform_json FROM readings WHERE timestamp_ms=0').fetchone()[0]),[0]*256)
            self.assertEqual(connection.execute('SELECT MAX(timestamp_ms) FROM readings').fetchone()[0],11000)

    def test_changed_snapshot_can_target_an_analyzed_interval(self):
        target=self.root/'target-window'/'awear_data.sqlite3'
        self.assertEqual(changed_snapshot(self.database,target,'P',change_start_ms=4000),11)
        with closing(sqlite3.connect(target)) as connection:
            self.assertEqual(json.loads(connection.execute('SELECT waveform_json FROM readings WHERE timestamp_ms=4000').fetchone()[0]),[0]*256)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM readings WHERE timestamp_ms=5000').fetchone()[0],0)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM readings WHERE timestamp_ms=1000').fetchone()[0],1)

    def test_budget_preflight_rejects_uninterpretable_protocol(self):
        path=Path(__file__).resolve().parent.parent/'config'/'analysis-acceptance.json'
        budget=json.loads(path.read_text(encoding='utf-8'))
        validate_budget(budget)
        for patch in ({'coldSeconds':False},{'workerPeakBytes':float('nan')},{'jobTimeoutSeconds':1},{'version':2}):
            with self.assertRaises(ValueError):validate_budget({**budget,**patch})
        # An existing output is rejected before any analysis or source mutation.
        with self.assertRaises(ValueError):run_acceptance(self.database,self.root,budget)

    def test_real_isolated_stdlib_environment_has_safe_unavailable_state(self):
        source=Path(__file__).resolve().parent.parent/'src'
        script="""import sys,json
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from analysis_manager import AnalysisManager
import app
manager=AnalysisManager(Path(sys.argv[2]),Path(sys.argv[2])/'absent.sqlite3')
assert manager.available() is False
state=manager.state('P')
assert state['available'] is False and state['summary'] is None
assert not manager.root.exists()
print(json.dumps({'stdlibImports':True,'unavailable':True}))
"""
        result=subprocess.run([sys.executable,'-S','-c',script,str(source),str(self.root)],capture_output=True,text=True,check=True)
        self.assertEqual(json.loads(result.stdout),{'stdlibImports':True,'unavailable':True})

    def test_os_peak_memory_is_positive_and_monotonic(self):
        before=peak_memory_bytes()
        allocation=bytearray(16*1024*1024)
        allocation[-1]=1
        after=peak_memory_bytes()
        self.assertGreater(before,0)
        self.assertGreaterEqual(after,before)
        self.assertGreaterEqual(after,len(allocation))

    def test_atomic_json_is_deterministic_and_cancel_does_not_publish(self):
        path=self.root/'result.json'
        atomic_json(path,{'z':1,'a':{'q':2,'b':3}})
        original=path.read_bytes()
        atomic_json(path,{'a':{'b':3,'q':2},'z':1})
        self.assertEqual(path.read_bytes(),original)
        def cancel():raise AnalysisCancelled()
        with self.assertRaises(AnalysisCancelled):atomic_json(path,{'large':list(range(10000))},cancel)
        self.assertEqual(path.read_bytes(),original)

    def test_unicode_job_metadata_uses_utf8_on_every_platform(self):
        path=self.root/'unicode.json'
        value={'participant':'Person α','channel':'30–40 Hz','note':'Reading 🧠'}
        atomic_json(path,value)
        self.assertEqual(read_json(path),value)
        self.assertEqual(json.loads(path.read_bytes()),value)

    def test_navigation_percentile_uses_all_samples_and_rejects_empty(self):
        self.assertEqual(percentile95([100]+[1]*19),1)
        self.assertEqual(percentile95([100]+[1]*18),100)
        with self.assertRaises(ValueError):percentile95([])

    @unittest.skipUnless(HAS_NUMPY,'optional NumPy')
    def test_worker_direct_success_cancel_and_failure_have_honest_status(self):
        def job(name,database):
            directory=self.root/'quality'/'jobs'/name
            directory.mkdir(parents=True)
            (directory/'heartbeat').touch()
            (directory/'request.json').write_text(json.dumps({'database':str(database),'participant':'P',
                'normalize':False,'startMs':None,'endMs':None,'timezone':'UTC','demo':True}))
            return directory
        completed=job('completed',self.database)
        self.assertEqual(run(completed),0)
        status=json.loads((completed/'status.json').read_text(encoding='utf-8'))
        self.assertEqual(status['state'],'completed')
        self.assertGreater(status['peakMemoryBytes'],0)
        self.assertEqual(status['computedFeatures'],2)
        canceled=job('canceled',self.database);(canceled/'cancel').touch()
        self.assertEqual(run(canceled),2)
        self.assertEqual(json.loads((canceled/'status.json').read_text(encoding='utf-8'))['state'],'canceled')
        self.assertFalse((canceled/'report.json').exists())
        failed=job('failed',self.root/'missing.sqlite3')
        self.assertEqual(run(failed),1)
        self.assertEqual(json.loads((failed/'status.json').read_text(encoding='utf-8'))['state'],'failed')
        self.assertFalse((failed/'report.json').exists())

    @unittest.skipUnless(HAS_NUMPY,'optional NumPy')
    def test_independent_full_window_oracle_rejects_missing_source(self):
        from analysis_quality import analyze_archive
        from analysis_view import coverage_summary
        report=analyze_archive(self.database,'P')
        summary=coverage_summary(report,timezone='UTC',demo=True)
        check_coverage(summary,8)
        path=self.root/'report.json';atomic_json(path,report)
        check_windows(path,self.database,'P')
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute('DELETE FROM readings WHERE timestamp_ms=2000');connection.commit()
        with self.assertRaisesRegex(ValueError,'crosses'):check_windows(path,self.database,'P')
        with self.assertRaisesRegex(ValueError,'SQL'):check_coverage(summary,7)
        bad=json.loads(json.dumps(summary));bad['flaggedUnionMs']+=1
        with self.assertRaisesRegex(ValueError,'union'):check_coverage(bad,8)
        summary['bins'][0]['computedUnionMs']=100000
        with self.assertRaisesRegex(ValueError,'union'):check_coverage(summary,8)

    @unittest.skipUnless(HAS_NUMPY,'optional NumPy')
    def test_private_coverage_export_is_self_contained_and_complete(self):
        from analysis_quality import analyze_archive
        from analysis_view import coverage_summary
        summary=coverage_summary(analyze_archive(self.database,'P'),timezone='UTC',demo=True)
        path=self.root/'coverage.html';coverage_html([summary],path)
        page=path.read_text(encoding='utf-8')
        self.assertNotIn('src="http',page)
        self.assertNotIn('<script',page)
        self.assertIn('Cloud EEG only',page)
        self.assertIn('right ear',page)
        self.assertIn(summary['analysisIdentity'],page)
        self.assertEqual(page.count('class="bar"'),len(summary['bins']))
