"""Synthetic signals and explicit accounting expectations, independent of UI layout."""
from contextlib import closing
from dataclasses import replace
from datetime import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import analysis_provenance as provenance

if importlib.util.find_spec('numpy'):
    import numpy as np
    import analysis_quality as quality
else:
    np = quality = None


@unittest.skipUnless(quality is not None,'Optional NumPy is not installed')
class QualityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.database = self.root/'archive.sqlite3'
        with closing(sqlite3.connect(self.database)) as connection:
            connection.executescript('''CREATE TABLE readings(participant_id TEXT,timestamp_ms INTEGER,
                device_id TEXT,sample_count INTEGER,waveform_json TEXT);
                CREATE TABLE labels(label TEXT); INSERT INTO labels VALUES('Personal activity');''')

    def add(self,start,count,*,device='D',hz=10,amplitude=1,participant='P'):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.executemany('INSERT INTO readings VALUES(?,?,?,?,?)',[(participant,start+i*1000,device,256,
                json.dumps((amplitude*np.sin(2*np.pi*hz*(np.arange(256)+i*256)/256)).tolist())) for i in range(count)])
            connection.commit()

    def update(self,sql,args=()):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(sql,args);connection.commit()

    def analyze(self,**kwargs):return quality.analyze_archive(self.database,'P',**kwargs)

    def assert_accounted(self,result):
        c=result['coverage']
        self.assertEqual(c['sourceRecords'],c['excludedRecords']+c['computedRecordSeconds']+c['featureFailureRecordSeconds']+c['boundaryRemainderRecordSeconds'])
        self.assertEqual(c['excludedRecords'],sum(c['excludedByPrimaryReason'].values()))
        self.assertEqual(c['computedWindows'],c['flaggedWindows']+c['unflaggedWindows'])
        self.assertEqual(c['sourceRecords'],result['sourceInventory']['recordCount'])
        self.assertEqual(c['excludedRecords'],sum(s['recordCount'] for s in result['excludedSpans']))
        self.assertEqual(c['boundaryRemainderRecordSeconds'],sum(s['recordCount'] for s in result['remainderSpans']))

    def test_known_tone_has_expected_power_share_units_and_provenance(self):
        self.add(0,4,amplitude=2)
        report=self.analyze();window=report['windows'][0];f=window['features']
        self.assertGreater(f['bandShares'][2],.99)
        self.assertAlmostEqual(sum(f['bandShares']),1,places=12)
        self.assertAlmostEqual(f['power1to40Hz'],2,delta=.01)
        self.assertAlmostEqual(f['acRms'],2**.5,places=12)
        self.assertEqual(window['sampleCount'],1024)
        self.assertEqual(window['qualityAnalysisIdentity'],report['analysisIdentity'])
        self.assertEqual(window['acquisition']['placement'],'right')
        self.assertEqual(report['featureUnits']['bandPowers'],'source units squared')
        self.assert_accounted(report)

    @unittest.skipUnless(importlib.util.find_spec('scipy'),'Independent SciPy reference unavailable')
    def test_even_and_odd_density_agree_with_independent_periodogram(self):
        from scipy.signal import periodogram
        for n in [1024,1025]:
            signal=2*np.sin(2*np.pi*10*np.arange(n)/256)+.02*np.arange(n)+5
            result=quality.power_spectrum(signal,256)
            f,density=periodogram(signal,fs=256,window=np.hanning(n),detrend='linear',scaling='density')
            np.testing.assert_allclose(result['frequencies'],f,rtol=0,atol=1e-12)
            np.testing.assert_allclose(result['density'],density,rtol=1e-8,atol=1e-11)

    @unittest.skipUnless(importlib.util.find_spec('sklearn'),'Optional clustering dependencies unavailable')
    def test_legacy_pattern_feature_vectors_are_preserved(self):
        import analyze_patterns as legacy
        fixture=Path(__file__).parent/'fixtures/legacy-spectral-vectors.json'
        for row in json.loads(fixture.read_text()):
            samples=np.sin(2*np.pi*row['frequencyHz']*np.arange(1024)/256)+.002*np.arange(1024)+7
            vector,shares=legacy.features(samples)
            np.testing.assert_allclose(vector,row['vector'],rtol=1e-12,atol=1e-12)
            np.testing.assert_allclose(shares,row['shares'],rtol=1e-12,atol=1e-12)

    @unittest.skipUnless(importlib.util.find_spec('sklearn'),'Optional clustering dependencies unavailable')
    def test_legacy_clustering_cli_runs_with_shared_spectral_primitive(self):
        rng=np.random.default_rng(7)
        rows=[]
        for second in range(160):
            hz=2 if second<80 else 10
            samples=(1+.005*(second//4))*np.sin(2*np.pi*hz*np.arange(256)/256)+rng.normal(0,.02,256)
            rows.append(('P',second*1000,'D',256,json.dumps(samples.tolist())))
        with closing(sqlite3.connect(self.database)) as connection:
            connection.executemany('INSERT INTO readings VALUES(?,?,?,?,?)',rows);connection.commit()
        profile=self.root/'profile';profile.mkdir()
        shutil.copy2(self.database,profile/'awear_data.sqlite3')
        entry=Path(quality.__file__).with_name('analyze_patterns.py')
        result=subprocess.run([sys.executable,str(entry),'--participant','P'],cwd=self.root,
            env={**os.environ,'AWEAR_DATA_DIR':str(profile),'AWEAR_API_KEY':''},capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        reports=list((profile/'data/processed/patterns').glob('*.json'))
        self.assertEqual(len(reports),1)
        report=json.loads(reports[0].read_text())
        self.assertEqual(len(report['windows']),40)
        self.assertEqual(report['method']['sampleRate'],256)

    def test_gap_and_device_boundaries_preserve_complete_counts(self):
        self.add(0,2,device='A');self.add(3000,4,device='B');self.add(10000,4,device='B')
        report=self.analyze();c=report['coverage']
        self.assertEqual(c['sourceRecords'],10)
        self.assertEqual(c['computedWindows'],2)
        self.assertEqual(c['boundaryRemainderRecordSeconds'],2)
        self.assertEqual(c['unrecordedSpanMilliseconds'],4000)
        self.assertEqual([w['startMs'] for w in report['windows']],[3000,10000])
        self.assertTrue(all(w['acquisition']['device_id']=='B' for w in report['windows']))
        self.assert_accounted(report)

    def test_duplicate_at_window_edge_excludes_both_and_never_selects_a_winner(self):
        self.add(0,8)
        self.update("INSERT INTO readings SELECT participant_id,timestamp_ms,'other-device',sample_count,waveform_json FROM readings WHERE timestamp_ms=3000")
        report=self.analyze();c=report['coverage']
        self.assertEqual(c['excludedByPrimaryReason'],{'duplicate_timestamp':2})
        self.assertEqual(c['boundaryRemainderRecordSeconds'],3)
        self.assertEqual([w['startMs'] for w in report['windows']],[4000])
        self.assertEqual(report['excludedSpans'][0]['recordCount'],2)
        self.assertEqual(report['excludedSpans'][0]['endMsExclusive']-report['excludedSpans'][0]['startMs'],1000)
        self.assert_accounted(report)

    def test_fractional_second_cadence_break_is_not_silently_resampled(self):
        self.add(0,2);self.add(2500,4)
        report=self.analyze()
        self.assertEqual([w['startMs'] for w in report['windows']],[2500])
        self.assertEqual(report['coverage']['unrecordedSpanMilliseconds'],500)
        self.assertEqual(report['coverage']['boundaryRemainderRecordSeconds'],2)
        self.assert_accounted(report)

    def test_unknown_device_invalid_count_waveform_and_timestamp_are_explicit(self):
        self.add(0,4,device=None)
        self.add(4000,4)
        self.update("UPDATE readings SET sample_count=255 WHERE timestamp_ms=4000")
        self.update("UPDATE readings SET waveform_json='[NaN]' WHERE timestamp_ms=5000")
        self.update("UPDATE readings SET timestamp_ms='unknown' WHERE timestamp_ms=6000")
        report=self.analyze()
        self.assertEqual(report['coverage']['excludedByPrimaryReason'],{'invalid_sample_count':1,'invalid_timestamp':1,'invalid_waveform':1,'unknown_device':4})
        self.assertEqual(report['coverage']['computedWindows'],0)
        self.assert_accounted(report)

    def test_generic_core_never_joins_participant_source_placement_or_session(self):
        context=provenance.AcquisitionContext('P','D','bluetooth','right',256,None,'device-timestamp',session_id='A')
        changes=[replace(context,participant_id='Q'),replace(context,placement='left'),replace(context,session_id='B'),
                 replace(context,timestamp_basis='host-arrival'),
                 provenance.AcquisitionContext('P','D','awear-cloud','right',256,'RIGHT_TEMP','cloud-timestamp')]
        waveform=json.dumps(np.sin(2*np.pi*10*np.arange(256)/256).tolist())
        for new in changes:
            engine=quality.QualityExtractor(provenance.AnalysisSpec(quality_policy_version=quality.QualityPolicy().version),quality.QualityPolicy(),'synthetic-code')
            for i in range(6):engine.feed(provenance.SourceRecord(context if i<2 else new,i*1000,waveform,256))
            report=engine.finish()
            self.assertEqual([w['startMs'] for w in report['windows']],[2000])
            self.assertEqual(report['windows'][0]['acquisitionGroupId'],new.group_id)
            self.assertEqual(report['coverage']['boundaryRemainderRecordSeconds'],2)

    def test_flags_overlap_without_double_counting_and_preserve_features(self):
        self.add(0,32)
        self.add(32000,4,amplitude=1000)
        self.add(36000,4,amplitude=0)
        self.add(40000,4,hz=60)
        report=self.analyze();c=report['coverage']
        by_start={w['startMs']:w for w in report['windows']}
        self.assertIn('relative_high_amplitude',by_start[32000]['qualityFlags'])
        self.assertIn('relative_large_step',by_start[32000]['qualityFlags'])
        self.assertIn('flatline',by_start[36000]['qualityFlags'])
        self.assertIn('mains_like_power',by_start[40000]['qualityFlags'])
        self.assertGreater(sum(c['flagOccurrences'].values()),c['flaggedWindows'])
        self.assertTrue(all('features' in w for w in report['windows']))
        self.assert_accounted(report)

    def test_linear_trend_is_a_named_screening_cue(self):
        values=np.arange(1024,dtype=float)
        result=quality.feature_summary(values,256)
        self.assertAlmostEqual(result['linearTrendFraction'],1,places=12)
        self.assertGreater(result['linearSlopePerSecond'],0)

    def test_relative_references_and_normalization_do_not_mix_devices(self):
        self.add(0,32,device='A',amplitude=1)
        self.add(32000,32,device='B',amplitude=100)
        spec=provenance.AnalysisSpec(normalization='within-acquisition-group-robust',quality_policy_version=quality.QualityPolicy().version)
        report=self.analyze(spec=spec)
        self.assertEqual(len(report['groups']),2)
        self.assertEqual(report['coverage']['flaggedWindows'],0)
        medians=sorted(g['normalization']['acRms']['median'] for g in report['groups'])
        self.assertGreater(medians[1]/medians[0],99)
        self.assertTrue(all(w['normalizedFeatures'] is not None for w in report['windows']))
        self.assert_accounted(report)

    def test_small_reference_group_does_not_claim_relative_screening(self):
        self.add(0,4)
        report=self.analyze()
        self.assertFalse(report['groups'][0]['relativeScreeningAvailable'])
        self.assertEqual(report['groups'][0]['relativeCutoffs'],{})

    def test_numeric_failure_is_counted_and_retains_source_provenance(self):
        self.add(0,4)
        self.update('UPDATE readings SET waveform_json=?',(json.dumps([1e308,-1e308]*128),))
        report=self.analyze()
        self.assertEqual(report['coverage']['featureFailureWindows'],1)
        self.assertEqual(report['coverage']['featureFailureRecordSeconds'],4)
        self.assertEqual(report['failedWindows'][0]['provenance']['sampleCount'],1024)
        self.assertEqual(report['windows'],[])
        self.assert_accounted(report)

    def test_requested_range_is_half_open_and_partial_tail_is_excluded(self):
        self.add(0,8)
        exact=self.analyze(start_ms=0,end_ms=4000)
        partial=self.analyze(start_ms=0,end_ms=3500)
        self.assertEqual(exact['coverage']['computedWindows'],1)
        self.assertEqual(partial['coverage']['excludedByPrimaryReason'],{'partial_range_record':1})
        self.assertEqual(partial['coverage']['computedWindows'],0)
        self.assertNotEqual(exact['analysisIdentity'],partial['analysisIdentity'])
        self.assert_accounted(partial)

    def test_raw_data_and_labels_remain_unchanged_and_report_is_repeatable(self):
        self.add(0,8);self.add(0,4,participant='OTHER')
        before=hashlib.sha256(self.database.read_bytes()).hexdigest()
        report=self.analyze()
        self.assertEqual(report,self.analyze())
        self.assertEqual(report['coverage']['sourceRecords'],8)
        self.assertEqual(before,hashlib.sha256(self.database.read_bytes()).hexdigest())

    def test_changed_input_settings_and_implementation_change_report_identity(self):
        self.add(0,8)
        first=self.analyze()
        self.update("UPDATE readings SET waveform_json='[]' WHERE timestamp_ms=0")
        second=self.analyze()
        self.assertNotEqual(first['analysisIdentity'],second['analysisIdentity'])
        third=self.analyze(policy=replace(quality.QualityPolicy(),relative_mad_multiplier=9))
        self.assertNotEqual(second['analysisIdentity'],third['analysisIdentity'])
        with patch('analysis_quality.implementation_identity',return_value={'qualitySource':'changed'}):
            self.assertNotEqual(second['analysisIdentity'],self.analyze()['analysisIdentity'])

    def test_concurrent_wal_arrival_does_not_mix_inventory_and_feature_snapshots(self):
        self.add(0,4)
        with closing(sqlite3.connect(self.database)) as connection:connection.execute('PRAGMA journal_mode=WAL')
        original=quality.feature_summary
        def extract(samples,rate):
            self.add(4000,4)
            return original(samples,rate)
        with patch('analysis_quality.feature_summary',side_effect=extract):report=self.analyze()
        self.assertEqual(report['sourceInventory']['recordCount'],4)
        self.assertEqual(report['coverage']['computedWindows'],1)
        self.assertEqual(self.analyze()['coverage']['computedWindows'],2)

    def test_utc_continuity_survives_daylight_saving_fallback(self):
        start=int(datetime.fromisoformat('2026-11-01T05:59:58+00:00').timestamp()*1000)
        self.add(start,4)
        report=self.analyze()
        self.assertEqual(report['coverage']['unrecordedSpanMilliseconds'],0)
        self.assertEqual(report['windows'][0]['endMsExclusive']-start,4000)

    def test_legacy_missing_device_metadata_is_excluded_without_migration(self):
        self.add(0,4)
        self.update('ALTER TABLE readings DROP COLUMN device_id')
        before=self.database.read_bytes()
        report=self.analyze()
        self.assertEqual(report['coverage']['excludedByPrimaryReason'],{'unknown_device':4})
        self.assertEqual(before,self.database.read_bytes())

    def test_cli_creates_report_but_never_overwrites_existing_input(self):
        self.add(0,4)
        output=self.root/'quality.json'
        command=[sys.executable,quality.__file__,str(self.database),'--participant','P','--output']
        result=subprocess.run(command+[str(output)],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(output.read_text())['coverage']['computedWindows'],1)
        before=self.database.read_bytes()
        result=subprocess.run(command+[str(self.database)],capture_output=True,text=True,timeout=20)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(before,self.database.read_bytes())

    def test_empty_selection_and_invalid_settings_are_explicit(self):
        report=self.analyze()
        self.assertEqual(report['windows'],[])
        self.assert_accounted(report)
        with self.assertRaises(ValueError):self.analyze(start_ms=4000,end_ms=3000)
        with self.assertRaises(ValueError):quality.QualityPolicy(relative_mad_multiplier=float('nan'))
        with self.assertRaises(ValueError):quality.QualityPolicy(minimum_reference_windows=True)
        with self.assertRaises(ValueError):quality.QualityPolicy(mains_like_fraction=2)
        with self.assertRaises(ValueError):quality.QualityPolicy(relative_mad_multiplier='8')
        with self.assertRaises(ValueError):self.analyze(spec=provenance.AnalysisSpec())


if __name__=='__main__':unittest.main()
