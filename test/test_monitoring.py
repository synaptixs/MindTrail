"""Immutable-history, independent aligned-change oracles and real lifecycle/HTTP checks."""
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict
import hashlib
import ast
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error

import app
from analysis_manager import AnalysisManager
from analysis_provenance import AcquisitionContext,fingerprint,canonical_json
import analysis_history as history
import monitoring_model as model
import monitoring_access as access
from test import test_recurrence as fixtures


def numeric(revision='a',*,value=10,day='2026-10-20',session=None):
    acq=asdict(AcquisitionContext('P','D','awear-cloud','right',256,'RIGHT_TEMP','cloud-timestamp',session_id=session));key=fingerprint(acq)
    n={'version':1,'participantId':'P','analysisIdentity':revision*64,'sourceRevision':revision*64,'analysisContext':{'pinned':'test-method'},
       'observedExtent':{'startMs':1000,'endMsExclusive':5000},'requestedRange':{'startMs':None,'endMsExclusive':None},'timezone':'UTC','demo':True,
       'coverage':{'sourceRecords':4,'computedWindows':1,'unflaggedWindows':1,'flaggedWindows':0,'excludedRecords':0,'boundaryRemainderRecordSeconds':0,'featureFailureWindows':0},
       'families':[{'compatibilityId':fingerprint({k:v for k,v in acq.items() if k!='session_id'}),'contexts':[{'acquisitionGroupId':key,'acquisition':acq}],
       'conditions':[{'acquisitionGroupId':key,'date':day,'sessionId':session,'startMs':1000,'endMsExclusive':5000,'unflaggedWindows':1,'flaggedWindows':0,'recordedUnionMs':4000,'missingMs':0,'qualityMs':{'computed_unflagged':4000},'proxyQuartiles':[[value-1,value,value+1],[1,2,3],[.1,.2,.3]]}],
       'proxies':[{'label':'AC RMS','units':'device units'},{'label':'Log power','units':'log10 units squared'},{'label':'Alpha share','units':'fraction'}]}]}
    n['reportIdentity']=fingerprint(n);return n


def seal(n):
    n.pop('reportIdentity',None);n['reportIdentity']=fingerprint(n);return n


def stored(n):return {'header':dict(history.metadata(n),version=n['reportIdentity']),'numerical':n}


class ModelTests(unittest.TestCase):
    def test_hand_net_counts_matched_medians_added_removed_and_missing(self):
        a=numeric();b=numeric('b',value=14)
        extra=deepcopy(b['families'][0]['conditions'][0]);extra['date']='2026-10-21';b['families'][0]['conditions'].append(extra);b['coverage'].update(sourceRecords=8,computedWindows=2,unflaggedWindows=2);seal(b)
        v=model.compare(stored(a),stored(b),limit=1)
        self.assertEqual(v['rowTotals'],{'added':1,'removed':0,'revised':1,'unchanged':0});self.assertEqual(v['total'],2);self.assertEqual(v['shown'],1);self.assertTrue(v['hasMore'])
        self.assertEqual(v['rows'][0]['features'][0]['medianDelta'],4)
        self.assertEqual(next(r['netDelta'] for r in v['coverage'] if r['metric']=='sourceRecords'),4)
        self.assertEqual(model.compare(stored(a),stored(b),offset=1)['rows'][0]['features'],[])
        reverse=model.compare(stored(b),stored(a));self.assertEqual(reverse['rowTotals']['removed'],1);self.assertEqual(reverse['rows'][0]['features'][0]['medianDelta'],-4)
        b['families'][0]['conditions'][0]['proxyQuartiles']=None;seal(b)
        self.assertEqual(model.compare(stored(a),stored(b))['rows'][0]['features'],[])
        self.assertEqual(model.compare(stored(a),stored(a))['rowTotals']['unchanged'],1)
    def test_context_session_and_method_range_isolation(self):
        a=numeric(session=None);b=numeric('b',session='known-new')
        v=model.compare(stored(a),stored(b));self.assertEqual(v['rowTotals']['added'],1);self.assertEqual(v['rowTotals']['removed'],1);self.assertTrue(all(not r['features'] for r in v['rows']))
        self.assertEqual(v['recordedSessionIds']['added'],['known-new'])
        for field,value in [('analysisContext',None),('analysisContext',{'new':'method'}),('timezone','America/New_York'),('requestedRange',{'startMs':0,'endMsExclusive':None})]:
            b=numeric('b');b[field]=value;seal(b);v=model.compare(stored(a),stored(b));self.assertEqual(v['status'],'incompatible');self.assertEqual(v['coverage'],[]);self.assertEqual(v['rows'],[])
        self.assertEqual(model.compare(None,stored(a))['status'],'baseline_missing')
        for offset,limit in [(-1,20),(True,20),(0,101)]:
            with self.assertRaises(ValueError):model.compare(stored(a),stored(a),offset=offset,limit=limit)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).resolve();self.db=self.root/'raw.sqlite3';self.db.write_bytes(b'raw archive untouched')
        self.manager=AnalysisManager(self.root,self.db);self.addCleanup(self.manager.close)
    def test_no_eager_creation_immutable_dedup_scope_caps_and_payload_validation(self):
        self.assertEqual(history.read(self.manager,'P')['total'],0);self.assertFalse(history.location(self.manager).exists())
        a=numeric();identifier=history.capture(self.manager,'P',a);p=history.location(self.manager);before=p.read_bytes()
        self.assertEqual(history.capture(self.manager,'P',a),identifier);self.assertEqual(p.read_bytes(),before)
        with patch.object(history,'MAX_VERSIONS',1):
            with self.assertRaisesRegex(history.HistoryError,'limit'):history.capture(self.manager,'P',numeric('b'))
        with patch.object(history,'MAX_BYTES',1):
            with self.assertRaisesRegex(history.HistoryError,'128MB'):history.capture(self.manager,'P',numeric('b'))
        self.assertEqual(history.read(self.manager,'P')['total'],1)
        with self.assertRaises(ValueError):history.read(self.manager,'OTHER',selected=identifier)
        with closing(sqlite3.connect(p)) as c:
            with self.assertRaises(sqlite3.IntegrityError):c.execute('DELETE FROM versions')
            with self.assertRaises(sqlite3.IntegrityError):c.execute("UPDATE versions SET payload='{}'")
        self.assertEqual(self.db.read_bytes(),b'raw archive untouched')
        history.capture(self.manager,'P',numeric('b'));result=history.read(self.manager,'P',limit=1,selected=numeric('b')['reportIdentity']);self.assertEqual((result['total'],result['shown'],result['omitted']),(2,1,1));self.assertEqual(result['baseline']['numerical'],a)
        self.assertIsNone(history.read(self.manager,'P',active='f'*64)['selected'])
        with closing(sqlite3.connect(p)) as c,c:
            c.execute('DROP TRIGGER versions_no_update');c.execute("UPDATE versions SET payload='{}' WHERE version=?",(identifier,))
        with self.assertRaises(ValueError):history.read(self.manager,'P',selected=identifier)
    def test_mac_frozen_reader_source_asset_is_explicit_and_portable(self):
        root=Path(__file__).parents[1]
        tree=ast.parse((root/'awear-desktop.spec').read_text())
        expression=next(n.value for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='monitoring_sources' for t in n.targets))
        assets=eval(compile(ast.Expression(expression),'asset-spec','eval'),{'root':root,'str':str,'__builtins__':{}})
        self.assertEqual(assets,[(str(root/'src/monitoring_model.py'),'.')])
        target=self.root/'monitoring_model.py';target.write_bytes(Path(assets[0][0]).read_bytes())
        a=numeric();expected=model.compare(stored(a),stored(a))
        with patch.object(model,'__file__',str(target)):
            self.assertEqual(model.compare(stored(a),stored(a)),expected)

    def test_corrupt_store_errors_and_stdlib_without_numpy(self):
        path=history.location(self.manager);path.parent.mkdir(parents=True);path.write_bytes(b'not SQLite')
        with self.assertRaises(history.HistoryError):history.read(self.manager,'P')
        with self.assertRaises(history.HistoryError):history.capture(self.manager,'P',numeric())
        code="import analysis_history,monitoring_access,monitoring_model,sys; assert not any(n in sys.modules for n in ('numpy','sklearn','analysis_quality'))"
        r=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).parents[1]/'src',capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr)
    def test_concurrent_capture_deduplicates_without_overwriting(self):
        snapshot=numeric()
        with ThreadPoolExecutor(max_workers=2) as pool:
            values=list(pool.map(lambda _:history.capture(self.manager,'P',snapshot),range(2)))
        self.assertEqual(values,[snapshot['reportIdentity']]*2)
        self.assertEqual(history.read(self.manager,'P')['total'],1)
        self.assertEqual(history.read(self.manager,'P',selected=values[0])['selected']['numerical'],snapshot)


@unittest.skipUnless(importlib.util.find_spec('numpy'),'Source numerical runtime')
class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.helper=fixtures.WorkflowTests('test_real_pipeline_http_sealed_source_and_lifecycle');self.helper.setUp();self.addCleanup(self.helper.doCleanups);self.manager=self.helper.manager;self.helper.analyze()
        self.first=access.page(self.manager,'P')['selected']['version'];self.before=hashlib.sha256(app.DB_PATH.read_bytes()).hexdigest()
    def edit(self):
        with app.db() as c:c.execute('UPDATE readings SET waveform_json=? WHERE timestamp_ms=?',('[0]',fixtures.BASE))
    def test_worker_versions_stale_edit_delete_context_and_prune(self):
        v=access.page(self.manager,'P');self.assertEqual(v['liveStatus'],'current');self.assertEqual(v['history']['total'],1);self.assertEqual(v['comparison']['status'],'baseline_missing')
        self.helper.analyze();self.assertEqual(access.page(self.manager,'P')['history']['total'],1)
        self.edit();self.assertEqual(access.page(self.manager,'P')['liveStatus'],'stale');self.helper.analyze();v=access.page(self.manager,'P');self.assertEqual(v['history']['total'],2);self.assertEqual(v['comparison']['status'],'descriptive')
        self.assertEqual(next(r['netDelta'] for r in v['comparison']['coverage'] if r['metric']=='sourceRecords'),0);self.assertGreater(v['comparison']['rowTotals']['revised'],0)
        for stamp in [fixtures.BASE+1000,fixtures.BASE+2000]:
            with app.db() as c:c.execute('DELETE FROM readings WHERE timestamp_ms=?',(stamp,))
            self.helper.analyze()
        self.assertEqual(access.page(self.manager,'P')['history']['total'],4);self.assertLessEqual(len(list((self.manager.root/'jobs').glob('*/report.json'))),2)
        self.assertEqual(access.payload(self.manager,'P',self.first)['coverage']['sourceRecords'],36)
        after=hashlib.sha256(app.DB_PATH.read_bytes()).hexdigest();access.page(self.manager,'P',{'version':self.first});self.assertEqual(hashlib.sha256(app.DB_PATH.read_bytes()).hexdigest(),after)
        with patch.object(self.manager,'available',return_value=False):self.assertFalse(access.page(self.manager,'P')['available']);self.assertEqual(access.page(self.manager,'P')['history']['total'],4)
    def test_cancel_failure_capture_limit_and_pointer_failure_preserve_previous(self):
        pointer=self.manager.root/(self.manager.scope('P')+'.json');before=pointer.read_bytes();self.manager.start('P',{});self.manager.cancel()
        import time
        deadline=time.monotonic()+25
        while self.manager.running() and time.monotonic()<deadline:time.sleep(.03)
        self.assertEqual(pointer.read_bytes(),before);self.assertEqual(history.read(self.manager,'P')['total'],1)
        self.edit()
        with patch.object(history,'MAX_VERSIONS',1):self.helper.analyze(failed=True)
        self.assertEqual(pointer.read_bytes(),before);self.assertEqual(history.read(self.manager,'P')['total'],1)
        from analysis_manager import write_json
        def fail(path,value):
            if path==pointer:raise OSError('simulated pointer replacement failure')
            return write_json(path,value)
        with patch('analysis_manager.write_json',side_effect=fail):self.helper.analyze(failed=True)
        self.assertEqual(pointer.read_bytes(),before);self.assertEqual(history.read(self.manager,'P')['total'],2)
        v=access.page(self.manager,'P');self.assertEqual(v['selected']['version'],self.first);self.assertEqual(v['liveStatus'],'stale')
        self.helper.analyze();self.assertEqual(history.read(self.manager,'P')['total'],2);self.assertEqual(access.page(self.manager,'P')['liveStatus'],'current')
    def test_http_snapshot_export_scope_and_race(self):
        server=app.AppServer(('127.0.0.1',0),app.Collector(''));server.analysis.close();server.analysis=self.manager;t=threading.Thread(target=server.serve_forever,daemon=True);t.start();self.addCleanup(lambda:(server.shutdown(),server.server_close(),t.join()));base=f'http://127.0.0.1:{server.server_port}'
        def get(path):
            with urllib.request.urlopen(base+path) as response:return response.read()
        for name in ('monitor.html','monitor.js','monitor.css'):self.assertGreater(len(get('/'+name)),100)
        self.assertEqual(json.loads(get('/api/monitoring'))['history']['total'],1)
        expected=access.payload(self.manager,'P',self.first);self.assertEqual(get('/api/export/monitoring-version.json?version='+self.first),canonical_json(expected).encode())
        for suffix in ('?version='+'f'*64,'?version=bad','?rowOffset=-1','?limit=51'):
            with self.assertRaises(urllib.error.HTTPError) as error:get('/api/monitoring'+suffix)
            self.assertEqual(error.exception.code,400);error.exception.close()
        with patch.object(self.manager,'signature',side_effect=['first','first','changed','changed']):self.assertEqual(access.page(self.manager,'P')['liveStatus'],'refresh_required')
        self.assertEqual(hashlib.sha256(app.DB_PATH.read_bytes()).hexdigest(),self.before)
