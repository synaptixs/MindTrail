"""Synthetic process, cache invalidation and coverage contracts."""
from contextlib import closing
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from analysis_manager import AnalysisManager, read_json
from analysis_view import coverage_summary

HAS_NUMPY=importlib.util.find_spec('numpy') is not None
if HAS_NUMPY:
    import numpy as np
    import analysis_quality as quality
    from analysis_cache import FeatureCache


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.database=self.root/'archive.sqlite3'
        self.manager=AnalysisManager(self.root,self.database,demo=True);self.addCleanup(self.manager.close)
        with closing(sqlite3.connect(self.database)) as c:
            c.execute('CREATE TABLE readings(participant_id TEXT,timestamp_ms INTEGER,device_id TEXT,sample_count INTEGER,waveform_json TEXT)')
            c.commit()

    def add(self,start,count):
        wave=json.dumps([float(np.sin(2*np.pi*10*i/256)) for i in range(256)])
        with closing(sqlite3.connect(self.database)) as c:
            c.executemany('INSERT INTO readings VALUES(?,?,?,?,?)',[('P',start+i*1000,'D',256,wave) for i in range(count)]);c.commit()

    def wait(self):
        deadline=time.monotonic()+20
        while self.manager.running() and time.monotonic()<deadline:time.sleep(.03)
        self.assertFalse(self.manager.running(),'worker did not finish')
        return self.manager.state('P')

    def test_stdlib_manager_unavailable_in_bundle_and_no_eager_files(self):
        self.assertFalse(self.manager.root.exists())
        with patch.dict(os.environ,{'AWEAR_BUNDLED_RUNTIME':'1'}):
            self.assertFalse(self.manager.available())
            with self.assertRaisesRegex(ValueError,'unavailable'):self.manager.start('P',{})
        self.assertFalse(self.manager.root.exists())

    def test_settings_and_path_validation(self):
        for value in ({'startMs':True},{'startMs':10,'endMs':9},{'normalize':'yes'},{'timezone':'../bad'}):
            with self.assertRaises(ValueError):self.manager.settings(value)
        with self.assertRaises(ValueError):self.manager.directory('../escape')
        with self.assertRaises(ValueError):self.manager.report_path('P')

    @unittest.skipUnless(HAS_NUMPY,'optional NumPy')
    def test_real_worker_reuses_features_stale_and_participant_isolation(self):
        self.add(0,40);before=hashlib.sha256(self.database.read_bytes()).hexdigest()
        self.manager.start('P',{});first=self.wait()
        self.assertEqual(first['job']['state'],'completed',first)
        self.assertGreater(first['job']['peakMemoryBytes'],0)
        self.assertFalse(first['stale']);self.assertTrue(first['summary']['demo'])
        self.assertEqual(first['summary']['coverage']['computedWindows'],10)
        path=self.manager.report_path('P');cold_bytes=path.read_bytes();cold=json.loads(cold_bytes)
        self.assertEqual(hashlib.sha256(self.database.read_bytes()).hexdigest(),before)
        self.assertIsNone(self.manager.state('Other')['summary'])
        self.manager.start('P',{});warm=self.wait()
        self.assertEqual(warm['job']['reusedWindows'],10)
        self.assertEqual(cold,json.loads(self.manager.report_path('P').read_text(encoding='utf-8')))
        self.assertEqual(cold_bytes,self.manager.report_path('P').read_bytes())
        self.add(40000,4);self.assertTrue(self.manager.state('P')['stale'])
        self.manager.start('P',{});updated=self.wait()
        self.assertEqual(updated['job']['reusedWindows'],10)
        self.assertEqual(updated['job']['computedFeatures'],1)
        self.assertNotEqual(cold['analysisIdentity'],updated['summary']['analysisIdentity'])

    @unittest.skipUnless(HAS_NUMPY,'optional NumPy')
    def test_cancel_and_failure_preserve_previous_result_and_retry(self):
        self.add(0,4);self.manager.start('P',{});first=self.wait();identity=first['summary']['analysisIdentity']
        self.manager.start('P',{});self.manager.cancel();canceled=self.wait()
        self.assertEqual(canceled['job']['state'],'canceled')
        self.assertEqual(canceled['summary']['analysisIdentity'],identity)
        with closing(sqlite3.connect(self.database)) as c:c.execute('ALTER TABLE readings RENAME TO broken');c.commit()
        self.manager.start('P',{});failed=self.wait()
        self.assertEqual(failed['job']['state'],'failed')
        self.assertEqual(failed['summary']['analysisIdentity'],identity)
        with closing(sqlite3.connect(self.database)) as c:c.execute('ALTER TABLE broken RENAME TO readings');c.commit()
        self.manager.start('P',{});self.assertEqual(self.wait()['job']['state'],'completed')

    @unittest.skipUnless(HAS_NUMPY,'optional NumPy')
    def test_source_changed_during_job_is_conservatively_stale(self):
        self.add(0,4)
        signature=self.manager.signature()
        with patch.object(self.manager,'signature',return_value=signature+'changed'):
            self.manager.start('P',{});state=self.wait();self.assertFalse(state['stale'])
        self.assertTrue(self.manager.state('P')['stale'])

    @unittest.skipUnless(HAS_NUMPY,'optional NumPy')
    def test_interrupted_recovery_does_not_adopt_partial_output(self):
        self.add(0,4);self.manager.start('P',{});self.wait()
        # Simulate crash after request persistence, before current-pointer adoption.
        request=json.loads((self.manager.directory(self.manager.job['id'])/'request.json').read_text(encoding='utf-8'))
        from analysis_manager import write_json
        write_json(self.manager.root/'active.json',request)
        os.utime(self.manager.directory(request['id'])/'heartbeat',(time.time()-30,time.time()-30))
        recovered=AnalysisManager(self.root,self.database);self.addCleanup(recovered.close)
        state=recovered.state('P')
        self.assertEqual(state['job']['state'],'interrupted')
        self.assertIsNotNone(state['summary'])
        self.assertTrue((self.manager.directory(request['id'])/'cancel').exists())

    @unittest.skipUnless(HAS_NUMPY,'optional NumPy')
    def test_another_manager_cannot_steal_active_lease(self):
        self.add(0,200)
        self.manager.start('P',{})
        other=AnalysisManager(self.root,self.database);self.addCleanup(other.close)
        with self.assertRaisesRegex(ValueError,'already running'):other.start('P',{})
        self.assertFalse((self.manager.directory(self.manager.job['id'])/'cancel').exists())
        self.assertEqual(self.wait()['job']['state'],'completed')

    @unittest.skipUnless(HAS_NUMPY,'optional NumPy')
    def test_shutdown_cancels_owned_worker_and_report_retention_is_bounded(self):
        self.add(0,4)
        for _ in range(4):self.manager.start('P',{});self.wait()
        self.assertLessEqual(len(list((self.manager.root/'jobs').glob('*/report.json'))),2)
        self.assertEqual(len(list((self.manager.root/'jobs').glob('*/summary.json'))),4)
        self.manager.start('P',{});self.manager.close()
        self.assertFalse(self.manager.running())
        self.assertEqual(self.manager.state('P')['job']['state'],'canceled')
        self.assertIsNotNone(self.manager.state('P')['summary'])

    @unittest.skipUnless(HAS_NUMPY,'optional NumPy')
    def test_feature_cache_edit_delete_insert_and_corrupt_payload(self):
        self.add(0,40)
        cache_path=self.root/'cache.sqlite3'
        with FeatureCache(cache_path) as cache:
            cold=quality.analyze_archive(self.database,'P',feature_cache=cache)
        with FeatureCache(cache_path) as cache:
            warm=quality.analyze_archive(self.database,'P',feature_cache=cache)
            self.assertEqual(cold,warm);self.assertEqual(cache.hits,10)
        with closing(sqlite3.connect(self.database)) as c:
            c.execute('DELETE FROM readings WHERE timestamp_ms=39000');c.execute("UPDATE readings SET waveform_json=? WHERE timestamp_ms=35000",(json.dumps([0]*256),));c.commit()
        with FeatureCache(cache_path) as cache:
            cached=quality.analyze_archive(self.database,'P',feature_cache=cache)
            self.assertGreater(cache.hits,0);self.assertGreater(cache.misses,0)
        self.assertEqual(cached,quality.analyze_archive(self.database,'P'))
        with closing(sqlite3.connect(cache_path)) as c:
            payload='{"bad":1}';c.execute('UPDATE features_v1 SET payload=?,checksum=?',(payload,hashlib.sha256(payload.encode()).hexdigest()));c.commit()
        with FeatureCache(cache_path) as cache:
            self.assertEqual(cached,quality.analyze_archive(self.database,'P',feature_cache=cache));self.assertEqual(cache.hits,0)
        normalized=quality.provenance.AnalysisSpec(normalization='within-acquisition-group-robust',quality_policy_version=quality.QualityPolicy().version)
        with FeatureCache(cache_path) as cache:
            n=quality.analyze_archive(self.database,'P',spec=normalized,feature_cache=cache)
            self.assertEqual(cache.hits,0)
        self.assertEqual(n,quality.analyze_archive(self.database,'P',spec=normalized))
        implementation=quality.implementation_identity()
        with patch.object(quality,'implementation_identity',return_value={**implementation,'qualitySource':'changed-code'}):
            with FeatureCache(cache_path) as cache:
                quality.analyze_archive(self.database,'P',feature_cache=cache)
                self.assertEqual(cache.hits,0)


    @unittest.skipUnless(HAS_NUMPY,'optional NumPy')
    def test_cache_never_reuses_population_quality_flags(self):
        self.add(0,32)
        with FeatureCache(self.root/'c.sqlite3') as cache:first=quality.analyze_archive(self.database,'P',feature_cache=cache)
        self.add(32000,32)
        with FeatureCache(self.root/'c.sqlite3') as cache:
            second=quality.analyze_archive(self.database,'P',feature_cache=cache);self.assertEqual(cache.hits,8)
        self.assertEqual(second,quality.analyze_archive(self.database,'P'))
        self.assertNotEqual(first['groups'][0]['referenceWindowRevision'],second['groups'][0]['referenceWindowRevision'])


class SummaryTests(unittest.TestCase):
    def report(self,windows,first=0,last=9999):
        return dict(sourceInventory={'firstTimestampMs':first,'lastTimestampMs':last},windows=windows,
            analysisIdentity='I',coverage={},groups=[],spec={},limitations=[],scope='cloud',excludedSpans=[])

    def test_interval_union_not_record_count_and_flagged_precedence(self):
        r=self.report([{'startMs':0,'endMsExclusive':4000,'qualityFlags':[]},
            {'startMs':2000,'endMsExclusive':6000,'qualityFlags':['cue']},
            {'startMs':5000,'endMsExclusive':9000,'qualityFlags':[]}])
        s=coverage_summary(r,timezone='America/New_York',demo=False)
        self.assertEqual(s['computedUnionMs'],9000);self.assertEqual(s['flaggedUnionMs'],4000)
        self.assertEqual(len(s['bins']),1)

    def test_large_and_empty_timelines_are_bounded(self):
        s=coverage_summary(self.report([],last=1000*86400*365*40),timezone='UTC',demo=False)
        self.assertLessEqual(len(s['bins']),1500)
        self.assertEqual(s['bins'][-1]['endMsExclusive'],1000*86400*365*40+1000)
        s=coverage_summary(self.report([],first=None,last=None),timezone='UTC',demo=False)
        self.assertEqual(s['bins'],[])
