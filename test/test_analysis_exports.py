"""Independent ZIP accounting, complete scoped history, HTTP and preservation checks."""
from contextlib import closing
import csv
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import zipfile

import app
import analysis_exports as exports
import analysis_history as history
from analysis_manager import AnalysisManager
from analysis_provenance import canonical_json, fingerprint
import discovery_report_access as reports
from test import test_monitoring as fixtures
from test import test_recurrence as pipeline


def inspect_archive(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert z.testzip() is None
        manifest = json.loads(z.read('manifest.json'))
        assert manifest['bundleIdentity'] == fingerprint({k:v for k,v in manifest.items() if k != 'bundleIdentity'})
        assert set(z.namelist()) == {m['path'] for m in manifest['members']} | {'manifest.json'}
        for m in manifest['members']:
            content = z.read(m['path'])
            assert len(content) == m['bytes']
            assert hashlib.sha256(content).hexdigest() == m['sha256']
            assert z.getinfo(m['path']).date_time == (1980,1,1,0,0,0)
        return manifest, {name:z.read(name) for name in z.namelist()}


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.manager=AnalysisManager(self.root,self.root/'raw.sqlite3');self.addCleanup(self.manager.close)
    def bundle(self, participant='P'):
        with exports.history_bundle(self.manager,participant) as f:return f.read()
    def test_complete_history_beyond_page_cap_scoped_deterministic_and_preserved(self):
        expected=[]
        for i in range(53):
            n=fixtures.numeric();n['sourceRevision']=f'{i:064x}';fixtures.seal(n)
            expected.append(history.capture(self.manager,'P',n))
        n=fixtures.numeric();n['participantId']='OTHER';fixtures.seal(n);history.capture(self.manager,'OTHER',n)
        path=history.location(self.manager);before=path.read_bytes()
        a=self.bundle();self.assertEqual(a,self.bundle());self.assertEqual(path.read_bytes(),before)
        m,files=inspect_archive(a);index=json.loads(files['history.json'])
        self.assertEqual((m['versions'],index['shown'],index['omitted']),(53,53,0))
        self.assertEqual([r['version'] for r in index['versions']],expected)
        for identifier in expected:self.assertEqual(json.loads(files['versions/'+identifier+'.json'])['participantId'],'P')
        self.assertEqual(inspect_archive(self.bundle('EMPTY'))[0]['versions'],0)
        with patch.object(exports,'MAX_BUNDLE',100):
            with self.assertRaisesRegex(ValueError,'exceeds'):self.bundle()
        self.assertEqual(path.read_bytes(),before)
    def test_empty_corrupt_missing_scope_and_no_numerical_imports(self):
        self.assertEqual(inspect_archive(self.bundle())[0]['versions'],0)
        self.assertFalse(history.location(self.manager).exists())
        with self.assertRaises(ValueError):self.bundle(None)
        history.capture(self.manager,'P',fixtures.numeric());path=history.location(self.manager)
        with closing(sqlite3.connect(path)) as c,c:
            c.execute('DROP TRIGGER versions_no_update');c.execute("UPDATE versions SET payload='{}'")
        before=path.read_bytes()
        with self.assertRaises(ValueError):self.bundle()
        self.assertEqual(path.read_bytes(),before)
        code="import analysis_exports,analysis_history,sys; assert not any(x in sys.modules for x in ('numpy','sklearn','analysis_quality'))"
        r=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).parents[1]/'src',capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stderr)
    def test_csv_scalar_columns_nested_provenance_and_formula_safety(self):
        row={'features':{'acRms':-1.25,'bandShares':[.2,.8]},'acquisition':{'device_id':'=danger'},'startMs':1,'qualityFlags':[]}
        flat=exports.flat_row(row);columns=sorted(flat)
        data=b''.join(exports.table_chunks([flat],columns,app.safe_csv)).decode()
        actual=next(csv.DictReader(io.StringIO(data)))
        self.assertEqual(actual['features.acRms'],'-1.25')
        self.assertEqual(actual['acquisition.device_id'],"'=danger")
        self.assertEqual(json.loads(actual['features.bandShares']),[.2,.8])
        self.assertEqual(exports.quality_digest(row),hashlib.sha256(canonical_json(row).encode()).hexdigest())
        with self.assertRaisesRegex(ValueError,'cancel'):
            exports.quality_digest(row,lambda:(_ for _ in ()).throw(ValueError('cancel')))


@unittest.skipUnless(importlib.util.find_spec('numpy'),'Source numerical runtime')
class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.helper=pipeline.WorkflowTests('test_real_pipeline_http_sealed_source_and_lifecycle');self.helper.setUp();self.addCleanup(self.helper.doCleanups)
        self.manager=self.helper.manager;self.helper.analyze()
    def bundle(self, identity=None, **kwargs):
        identity=identity or reports.page(self.manager,'P')['snapshotIdentity']
        with exports.analysis_bundle(self.manager,'P',app.safe_csv,identity=identity,**kwargs) as f:return f.read()
    def test_full_windows_manifest_json_csv_offline_html_and_preservation(self):
        before=app.DB_PATH.read_bytes();a=self.bundle();self.assertEqual(a,self.bundle())
        self.assertEqual(app.DB_PATH.read_bytes(),before);m,files=inspect_archive(a)
        raw=json.loads(self.manager.report_path('P').read_text());report=json.loads(files['quality-report.json'])
        self.assertEqual(report,raw);self.assertEqual((m['windowRows'],m['omittedComputedWindows']),(9,0))
        csvrows=list(csv.DictReader(io.StringIO(files['feature-windows.csv'].decode())))
        self.assertEqual(len(csvrows),9)
        for actual,expected in zip(csvrows,raw['windows']):
            self.assertEqual(actual['windowId'],expected['windowId'])
            self.assertEqual(float(actual['features.acRms']),expected['features']['acRms'])
            self.assertEqual(actual['acquisition.placement'],'right')
            self.assertEqual(json.loads(actual['sourceRecordDigests']),expected['sourceRecordDigests'])
        self.assertEqual(m['coverage'],raw['coverage']);self.assertEqual(m['featureUnits'],raw['featureUnits'])
        self.assertIn(b'<svg',files['discovery-report.html']);self.assertNotIn(b'<script',files['discovery-report.html'])
        self.assertEqual(json.loads(files['activity-audit.json'])['assignments'],[])
        self.assertEqual(json.loads(files['window-context-audit.json'])['history'],[])
        self.assertTrue(any('clustering' in x for x in m['omissions']))
    def test_stale_identity_tampered_full_report_limits_and_changed_evidence_fail(self):
        identity=reports.page(self.manager,'P')['snapshotIdentity']
        with self.assertRaises(ValueError):self.bundle('f'*64)
        with self.assertRaises(ValueError):self.bundle(identity,lens='bad')
        path=self.manager.report_path('P');before=path.read_bytes();broken=json.loads(before)
        broken['windows'][0]['features']['acRms']+=99;path.write_text(json.dumps(broken))
        with self.assertRaisesRegex(ValueError,'sealed'):self.bundle(identity)
        path.write_bytes(before)
        with patch.object(exports,'MAX_INPUT',10):
            with self.assertRaises(ValueError):self.bundle(identity)
        with patch.object(exports,'MAX_BUNDLE',100):
            with self.assertRaises(ValueError):self.bundle(identity)
        with patch.object(reports,'recheck',side_effect=ValueError('changed')):
            with self.assertRaisesRegex(ValueError,'changed'):self.bundle(identity)
        with app.db() as c:c.execute('DELETE FROM readings WHERE timestamp_ms=?',(pipeline.BASE,))
        with self.assertRaises(ValueError):self.bundle(identity)
        with exports.history_bundle(self.manager,'P') as f:self.assertEqual(inspect_archive(f.read())[0]['versions'],1)
    def test_http_routes_current_wrong_scope_and_unavailable_runtime_history(self):
        server=app.AppServer(('127.0.0.1',0),app.Collector(''));server.analysis.close();server.analysis=self.manager
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(lambda:(server.shutdown(),server.server_close(),thread.join()))
        base=f'http://127.0.0.1:{server.server_port}'
        def get(path):
            with urllib.request.urlopen(base+path) as response:
                self.assertEqual(response.headers['Cache-Control'],'no-store');return response.read()
        identity=reports.page(self.manager,'P')['snapshotIdentity'];path='/api/export/analysis-bundle.zip?identity='+identity
        self.assertEqual(get(path),self.bundle(identity))
        with patch.object(self.manager,'available',return_value=False):self.assertEqual(inspect_archive(get('/api/export/monitoring-history.zip'))[0]['versions'],1)
        with patch.object(exports.tempfile,'TemporaryFile',side_effect=OSError('private path not disclosed')):
            with self.assertRaises(urllib.error.HTTPError) as error:get('/api/export/monitoring-history.zip')
            self.assertEqual(error.exception.code,400);self.assertNotIn(b'private path',error.exception.read());error.exception.close()
        for path in ('/api/export/analysis-bundle.zip','/api/export/analysis-bundle.zip?identity='+'f'*64):
            with self.assertRaises(urllib.error.HTTPError) as error:get(path)
            self.assertEqual(error.exception.code,400);error.exception.close()
        with app.db() as c:app.put_setting(c,'participant_id','OTHER')
        self.assertEqual(inspect_archive(get('/api/export/monitoring-history.zip'))[0]['versions'],0)
        with self.assertRaises(urllib.error.HTTPError) as error:get('/api/export/analysis-bundle.zip?identity='+identity)
        self.assertEqual(error.exception.code,400);error.exception.close()

    def test_nonempty_activity_and_undone_context_audits_keep_full_provenance(self):
        import discovery_review_access as review
        import discovery_context as context
        # Assignment is deliberately outside the numerical dates; the audit is participant-wide.
        label_id=app.add_label('P','=My activity','Personal note',1000,2000,
            evidence={'reviewState':'uncertain','timingPrecision':'approximate','startUncertaintyMs':500,'endUncertaintyMs':500})
        self.helper.analyze()
        snapshot=review.read(self.manager,'P');row=review.page(self.manager,'P')['items'][0]
        body={'participantId':'P','reviewIdentity':snapshot['reviewIdentity'],'windowId':row['windowId'],
            'revision':0,'kind':'partial','facts':'=My fact','hypothesis':'Hypothesis stays separate'}
        context.mutate(self.manager,'P',body)
        context.mutate(self.manager,'P',{'participantId':'P','windowId':row['windowId'],'revision':1},True)
        before=app.DB_PATH.read_bytes();context_before=context.location(self.manager).read_bytes()
        m,files=inspect_archive(self.bundle())
        audit=json.loads(files['activity-audit.json']);self.assertEqual(len(audit['assignments']),1)
        assignment=audit['assignments'][0];self.assertEqual(assignment['id'],label_id)
        self.assertEqual(assignment['review_state'],'uncertain');self.assertEqual(assignment['start_uncertainty_ms'],500)
        self.assertGreater(len(audit['assignmentHistory']),0)
        csvrow=next(csv.DictReader(io.StringIO(files['activity-assignments.csv'].decode())))
        self.assertEqual(csvrow['label'],"'=My activity");self.assertEqual(csvrow['start_ms'],'1000')
        contextual=json.loads(files['window-context-audit.json'])
        self.assertIsNone(contextual['current'][0]['record']);self.assertEqual(len(contextual['history']),2)
        self.assertEqual(contextual['history'][0]['after']['facts'],'=My fact')
        self.assertEqual(app.DB_PATH.read_bytes(),before);self.assertEqual(context.location(self.manager).read_bytes(),context_before)
