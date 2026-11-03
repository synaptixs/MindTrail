"""Independent source-revision and acquisition-boundary contracts; synthetic data only."""
from contextlib import closing
from dataclasses import replace
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import analysis_provenance as provenance


class WindowContractTests(unittest.TestCase):
    def setUp(self):
        self.context = provenance.AcquisitionContext('P-A', 'device-A', 'bluetooth', 'right',
            256, None, 'device-timestamp', session_id='session-A')
        self.records = [provenance.SourceRecord(self.context, 1000+i*1000,
            json.dumps([i]*256), 256) for i in range(4)]
        self.spec = provenance.AnalysisSpec()

    def test_contiguous_window_has_exact_interval_source_digests_and_sample_count(self):
        report = provenance.make_window_provenance(self.records, self.spec)
        self.assertEqual(report['startMs'], 1000)
        self.assertEqual(report['endMsExclusive'], 5000)
        self.assertEqual(report['sampleCount'], 1024)
        self.assertEqual(report['acquisition']['session_id'], 'session-A')
        self.assertEqual(len(report['sourceRecordDigests']), 4)
        self.assertEqual(report, provenance.make_window_provenance(self.records, self.spec))

    def test_no_window_crosses_each_acquisition_boundary(self):
        cases = [replace(self.context, participant_id='P-B'), replace(self.context, device_id='device-B'),
                 replace(self.context, placement='left'), replace(self.context, placement='unknown'),
                 replace(self.context, session_id='session-B'), replace(self.context, sample_rate=128),
                 replace(self.context, timestamp_basis='host-arrival'), replace(self.context, units='other units'),
                 provenance.AcquisitionContext('P-A','device-A','awear-cloud','right',256,'RIGHT_TEMP','cloud-timestamp')]
        for context in cases:
            with self.subTest(context=context):
                records = list(self.records)
                rate = context.sample_rate
                records[2] = replace(records[2], context=context, sample_count=rate, waveform_json=json.dumps([0]*rate))
                with self.assertRaisesRegex(ValueError, 'acquisition group'):
                    provenance.make_window_provenance(records, self.spec)

    def test_gaps_duplicates_and_reversed_records_are_rejected(self):
        for timestamp in [1000, 2500, 4000]:
            records = list(self.records)
            records[1] = replace(records[1], timestamp_ms=timestamp)
            with self.subTest(timestamp=timestamp), self.assertRaisesRegex(ValueError, 'gap, duplicate'):
                provenance.make_window_provenance(records, self.spec)
        with self.assertRaises(ValueError):
            provenance.make_window_provenance(list(reversed(self.records)), self.spec)

    def test_unknown_device_and_sample_rate_mismatch_cannot_silently_join(self):
        records = [replace(record, context=replace(self.context, device_id=None)) for record in self.records]
        with self.assertRaisesRegex(ValueError, 'identity is unknown'):
            provenance.make_window_provenance(records, self.spec)
        with self.assertRaisesRegex(ValueError, 'sample rates differ'):
            provenance.make_window_provenance(self.records, replace(self.spec, sample_rate=128))

    def test_invalid_waveforms_and_metadata_do_not_become_valid_windows(self):
        cases = [('true',256), ('{}',256), ('[NaN]',256), (json.dumps([True]*256),256),
                 (json.dumps([0]*255),256), (json.dumps([0]*256),255), ('broken',256)]
        for waveform, count in cases:
            records = list(self.records)
            records[0] = replace(records[0], waveform_json=waveform, sample_count=count)
            with self.subTest(waveform=waveform[:12]), self.assertRaises(ValueError):
                provenance.make_window_provenance(records, self.spec)
        records = list(self.records)
        records[0] = replace(records[0], timestamp_ms=True)
        with self.assertRaises(ValueError):
            provenance.make_window_provenance(records, self.spec)

    def test_utc_window_remains_contiguous_across_daylight_saving_fallback(self):
        start = int(datetime.fromisoformat('2026-11-01T05:59:58+00:00').timestamp()*1000)
        records = [replace(record, timestamp_ms=start+i*1000) for i,record in enumerate(self.records)]
        result = provenance.make_window_provenance(records, self.spec)
        self.assertEqual(result['endMsExclusive']-result['startMs'],4000)

    def test_settings_source_and_dirty_implementation_each_change_window_identity(self):
        original = provenance.make_window_provenance(self.records,self.spec,implementation='code-A')
        changed = list(self.records)
        changed[0] = replace(changed[0],waveform_json=json.dumps([1]*256))
        candidates = [provenance.make_window_provenance(changed,self.spec,implementation='code-A'),
                      provenance.make_window_provenance(self.records,replace(self.spec,normalization='within-acquisition-group-robust'),implementation='code-A'),
                      provenance.make_window_provenance(self.records,self.spec,implementation='code-B')]
        for candidate in candidates:self.assertNotEqual(candidate['windowId'],original['windowId'])

    def test_context_and_configuration_reject_ambiguous_input(self):
        for changes in [{'sample_rate':True},{'window_seconds':0},{'window_seconds':1.5},{'normalization':'guess'},{'quality_policy_version':''}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):replace(self.spec,**changes)
        with self.assertRaisesRegex(ValueError,'session identity'):replace(self.context,session_id=None)
        for changes in [{'participant_id':' '},{'device_id':' '},{'units':True},{'channel':''}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):replace(self.context,**changes)
        with self.assertRaisesRegex(ValueError,'cloud archive contract'):
            provenance.AcquisitionContext('P','D','awear-cloud','left',256,'RIGHT_TEMP','cloud-timestamp')


class SourceRevisionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.database = self.root / 'archive #1.sqlite3'
        with closing(sqlite3.connect(self.database)) as connection:
            connection.executescript('''CREATE TABLE readings(participant_id TEXT,timestamp_ms INTEGER,
                device_id TEXT,sample_count INTEGER,waveform_json TEXT,mean REAL);
                CREATE TABLE labels(label TEXT);
                CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT);''')
            connection.executemany('INSERT INTO readings VALUES(?,?,?,?,?,?)',
                [('P-A',1000+i*1000,'D-A',256,json.dumps([i]*256),float(i)) for i in range(4)])
            connection.execute("INSERT INTO settings VALUES('participant_id','P-A')")
            connection.execute("INSERT INTO labels VALUES('Personal activity')")
            connection.commit()

    def update(self, sql, args=()):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(sql,args)
            connection.commit()

    def report(self):return provenance.inspect_archive(self.database)

    def test_repeated_inventory_is_deterministic_and_preserves_source_bytes_and_labels(self):
        before = hashlib.sha256(self.database.read_bytes()).hexdigest()
        first = self.report()
        self.assertEqual(first,self.report())
        self.assertEqual(first['recordCount'],4)
        self.assertEqual(first['firstTimestampMs'],1000)
        self.assertEqual(first['lastTimestampMs'],4000)
        self.assertEqual(first['acquisition']['placement'],'right')
        self.assertIn('bluetooth captures',first['notInspected'])
        self.assertEqual(before,hashlib.sha256(self.database.read_bytes()).hexdigest())

    def test_changed_deleted_and_inserted_rows_invalidate_source_even_with_same_counts(self):
        revision = self.report()['sourceRevision']
        mutations = [("UPDATE readings SET waveform_json='invalid waveform text' WHERE timestamp_ms=1000",()),
                     ("UPDATE readings SET device_id='D-B' WHERE timestamp_ms=1000",()),
                     ("UPDATE readings SET sample_count=0 WHERE timestamp_ms=1000",()),
                     ("UPDATE readings SET timestamp_ms=5000 WHERE timestamp_ms=1000",()),
                     ("DELETE FROM readings WHERE timestamp_ms=2000",()),
                     ("INSERT INTO readings VALUES('P-A',6000,'D-A',256,'[]',0)",())]
        for sql,args in mutations:
            self.update(sql,args)
            new = self.report()['sourceRevision']
            self.assertNotEqual(new,revision)
            revision = new

    def test_annotations_redundant_statistics_and_other_participants_do_not_invalidate_samples(self):
        original = self.report()['sourceRevision']
        self.update("UPDATE labels SET label='Renamed personal activity'")
        self.update('UPDATE readings SET mean=999')
        self.update("INSERT INTO readings VALUES('P-B',1000,'other',256,'[]',0)")
        self.assertEqual(self.report()['sourceRevision'],original)

    def test_source_identity_does_not_depend_on_storage_location(self):
        destination = self.root/'renamed.sqlite3'
        shutil.copy2(self.database,destination)
        self.assertEqual(provenance.inspect_archive(destination)['analysisIdentity'],self.report()['analysisIdentity'])

    def test_legacy_archive_without_device_column_is_not_migrated(self):
        legacy = self.root/'legacy.sqlite3'
        with closing(sqlite3.connect(legacy)) as connection:
            connection.execute('CREATE TABLE readings(participant_id TEXT,timestamp_ms INTEGER,sample_count INTEGER,waveform_json TEXT)')
            connection.execute("INSERT INTO readings VALUES('P',1000,256,'invalid samples')")
            connection.commit()
        before = legacy.read_bytes()
        report = provenance.inspect_archive(legacy,'P')
        self.assertEqual(report['deviceCounts'],[{'deviceId':None,'records':1}])
        self.assertEqual(before,legacy.read_bytes())

    def test_missing_empty_and_unselected_archives_have_explicit_behavior(self):
        missing = self.root/'missing.sqlite3'
        with self.assertRaises(sqlite3.OperationalError):provenance.inspect_archive(missing,'P')
        self.assertFalse(missing.exists())
        self.assertEqual(provenance.inspect_archive(self.database,'P-EMPTY')['recordCount'],0)
        self.update('DELETE FROM settings')
        with self.assertRaisesRegex(ValueError,'participant explicitly'):self.report()

    def test_inventory_is_one_consistent_wal_snapshot_during_new_data_arrival(self):
        with closing(sqlite3.connect(self.database)) as connection:connection.execute('PRAGMA journal_mode=WAL')
        original_json = provenance.canonical_json
        inserted = False
        def encode(value):
            nonlocal inserted
            if not inserted:
                inserted = True
                self.update("INSERT INTO readings VALUES('P-A',5000,'D-A',256,'[]',0)")
            return original_json(value)
        with patch('analysis_provenance.canonical_json',side_effect=encode):
            self.assertEqual(self.report()['recordCount'],4)
        self.assertEqual(self.report()['recordCount'],5)

    def test_actual_module_bytes_invalidate_dirty_source_identity(self):
        source = self.root/'implementation.py'
        source.write_text('version = 1')
        with patch('analysis_provenance.__file__',str(source)):
            before = self.report()['analysisIdentity']
            source.write_text('version = 2')
            self.assertNotEqual(self.report()['analysisIdentity'],before)

    def test_cli_writes_new_report_and_refuses_to_overwrite_archive(self):
        command = [sys.executable,provenance.__file__,str(self.database),'--output']
        report = self.root/'inventory.json'
        result = subprocess.run(command+[str(report)],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(report.read_text())['recordCount'],4)
        before = self.database.read_bytes()
        result = subprocess.run(command+[str(self.database)],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,1)
        self.assertEqual(self.database.read_bytes(),before)


if __name__ == '__main__':unittest.main()
