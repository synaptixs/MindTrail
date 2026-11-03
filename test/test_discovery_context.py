"""Independent selective-selection, human context and real source/HTTP preservation checks."""
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
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
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error

import app
from analysis_manager import AnalysisManager,remove_quality_payloads
from analysis_provenance import fingerprint
import discovery_context as context
import discovery_review_access as access
from test import test_recurrence as fixtures


@unittest.skipUnless(importlib.util.find_spec('numpy'),'Source numerical environment')
class SelectionTests(unittest.TestCase):
    def test_independent_diversity_interleave_counts_blind_and_repeat(self):
        import discovery_review_model as model
        import discovery_daily,recurrence_model
        with tempfile.TemporaryDirectory() as folder:
            db=Path(folder)/'db.sqlite3'
            with closing(sqlite3.connect(db)) as c:c.execute('CREATE TABLE unrelated(id)');c.commit()
            rows=[fixtures.window(i,d,power=i%7+d) for d in range(3) for i in range(710)]
            source=fixtures.report(rows);daily,payloads=discovery_daily.build(source,db,'P','America/New_York','d'*64)
            index,families=recurrence_model.build(source,daily,payloads,'P');value=model.build(index,families,payloads)
            self.assertEqual(value,model.build(index,families,payloads));self.assertEqual(value['eligibleWindows'],2130)
            self.assertLessEqual(len(value['items']),24);self.assertEqual(len({r['windowId'] for r in value['items']}),len(value['items']))
            self.assertEqual(value['unofferedWindows']+value['offeredWindows'],2130)
            self.assertEqual(value['streams']['ordinary'],{'pool':2130,'selected':8,'omitted':2122})
            actual=model.diverse([dict(r,date=str(r['startMs']//86400000)) for r in rows],8)
            strata={}
            for r in rows:strata.setdefault((r['acquisitionGroupId'],str(r['startMs']//86400000)),[]).append(r['windowId'])
            expected=[sorted(strata[key])[depth] for depth in range(3) for key in sorted(strata)][:8]
            self.assertEqual([r['windowId'] for r in actual],expected)
            self.assertIn('ordinary',value['items'][0]['streams'])
            with patch.object(access,'read',return_value=dict(value,status='current')):
                blind=access.page(None,'P');visible=access.page(None,'P',True)
            self.assertEqual([r['windowId'] for r in blind['items']],[r['windowId'] for r in model.diverse([r for p in families.values() for r in p['rows']],8)]);self.assertEqual(blind['shown'],8);self.assertEqual(visible['shown'],value['offeredWindows'])
            self.assertTrue(all(r['streams']==['ordinary'] and all(reason['kind']=='ordinary' for reason in r['reasons']) for r in blind['items']))
            self.assertLessEqual(len(value['recurrenceQueries']),4)
    def test_change_source_context_guard_empty_cancel_and_payload_bound(self):
        import discovery_review_model as model
        import discovery_daily,recurrence_model
        with tempfile.TemporaryDirectory() as folder:
            db=Path(folder)/'db.sqlite3'
            with closing(sqlite3.connect(db)) as c:c.execute('CREATE TABLE unrelated(id)');c.commit()
            source=fixtures.report([fixtures.window(0),fixtures.window(1,flags=['flatline'])]);daily,payloads=discovery_daily.build(source,db,'P','America/New_York','d'*64);index,families=recurrence_model.build(source,daily,payloads,'P')
            row=next(iter(payloads.values()));row['changes']['retained']=[{'beforeExample':source['windows'][0]['windowId'],'afterExample':source['windows'][0]['windowId'],'atMs':fixtures.BASE,'score':4,'featureIndex':0}]
            v=model.build(index,families,payloads);self.assertEqual(v['streams']['change']['pool'],1);self.assertEqual(v['offeredWindows'],1);self.assertEqual(v['eligibleWindows'],1)
            row['date']='wrong'
            with self.assertRaisesRegex(ValueError,'context'):model.build(index,families,payloads)
            self.assertEqual(model.build(dict(index,groups=[]),{},{} )['offeredWindows'],0)
            def stop():raise RuntimeError('cancel')
            with self.assertRaisesRegex(RuntimeError,'cancel'):model.build(index,families,payloads,stop)


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).resolve()
        old=app.DATA_ROOT,app.DB_PATH;self.addCleanup(lambda:setattr(app,'DATA_ROOT',old[0]));self.addCleanup(lambda:setattr(app,'DB_PATH',old[1]))
        app.configure_storage(self.root);app.setup_db()
        with app.db() as c:
            app.put_setting(c,'participant_id','P')
            self.activity=app.personal_activities.create(c,'P',{'name':'My reading'})
            self.other=app.personal_activities.create(c,'OTHER',{'name':'My reading'})
        self.manager=AnalysisManager(self.root,app.DB_PATH);self.addCleanup(self.manager.close)
        self.sourcehash=hashlib.sha256(app.DB_PATH.read_bytes()).hexdigest()
        self.row=fixtures.window(0);self.row['date']='2026-10-20';self.row.update(streams=['ordinary'],reasons=[{'kind':'ordinary'}])
        self.snapshot={k:'a'*64 for k in ('reviewIdentity','analysisIdentity','dailyIdentity','recurrenceIdentity','sourceRevision')};self.snapshot['sourceSignature']=self.manager.signature()
        self.mock=patch.object(access,'require',return_value=(self.snapshot,self.row));self.mock.start();self.addCleanup(self.mock.stop)
    def body(self,**kwargs):
        return dict({'participantId':'P','reviewIdentity':'a'*64,'windowId':self.row['windowId'],'revision':0,'kind':'partial','facts':'Reading a document','hypothesis':'Possibly a posture change','activityIds':[],'evidence':{'reviewState':'uncertain','timingPrecision':'approximate','startUncertaintyMs':None,'endUncertaintyMs':5000}},**kwargs)
    def save(self,**kwargs):return context.mutate(self.manager,'P',self.body(**kwargs))
    def test_personal_identity_precision_provenance_edit_undo_export(self):
        first=self.save(kind='known',activityIds=[self.activity['id']]);r=first['record'];self.assertEqual(r['activities'],[{'id':self.activity['id'],'name':'My reading'}]);self.assertEqual(r['startUncertaintyMs'],None);self.assertEqual(r['endUncertaintyMs'],5000)
        self.assertEqual(r['provenance']['source'],'manual_observation');self.assertEqual(r['window']['acquisition']['placement'],'right')
        with self.assertRaisesRegex(ValueError,'Reveal'):self.save(revision=1)
        second=self.save(revision=1,revealed=True,facts='Edited factual context');self.assertEqual(second['revision'],2)
        restored=context.mutate(self.manager,'P',{'participantId':'P','windowId':self.row['windowId'],'revision':2},True)
        self.assertEqual(restored['record'],first['record'])
        with self.assertRaisesRegex(ValueError,'changed'):context.mutate(self.manager,'P',{'participantId':'P','windowId':self.row['windowId'],'revision':2},True)
        json_export=json.loads(b''.join(context.export(self.manager,'P')));self.assertEqual(len(json_export['history']),3);self.assertEqual(json_export['current'][0]['record'],first['record'])
        page=context.entry(self.manager,'P',self.row['windowId'],limit=1);self.assertEqual((page['shown'],page['total'],page['hasMore']),(1,3,True))
        self.assertEqual(context.entry(self.manager,'OTHER',self.row['windowId'])['revision'],0)
        self.assertEqual(hashlib.sha256(app.DB_PATH.read_bytes()).hexdigest(),self.sourcehash)
    def test_distinct_choices_invalid_ids_and_timing(self):
        for kwargs in [dict(kind='known'),dict(kind='known',activityIds=[self.other['id']]),dict(kind='unknown',activityIds=[self.activity['id']]),dict(kind='mixed',facts=''),dict(kind='partial',facts=''),dict(evidence={'timingPrecision':'approximate'}),dict(participantId='OTHER')]:
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):self.save(**kwargs)
        for kind in ('known','partial','mixed','unknown','skip'):
            result=self.save(kind=kind,activityIds=[self.activity['id']] if kind=='known' else [],revision=0 if kind=='known' else revision,revealed=True);revision=result['revision']
            self.assertEqual(result['record']['kind'],kind)
        self.assertEqual(context.entry(self.manager,'P',self.row['windowId'])['total'],5)
    def test_concurrency_aba_tombstone_and_formula_safe_csv(self):
        def attempt():
            try:return self.save(facts='=UNTRUSTED()')['revision']
            except ValueError:return 'conflict'
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:attempt(),range(2)))
        self.assertCountEqual(results,[1,'conflict'])
        exported=list(csv.DictReader(io.StringIO(b''.join(context.export(self.manager,'P','csv',app.safe_csv)).decode())));self.assertTrue(exported[0]['facts'].startswith("'="))
        self.save(revision=1,revealed=True,facts='B');self.save(revision=2,revealed=True,facts='=UNTRUSTED()')
        with self.assertRaises(ValueError):context.mutate(self.manager,'P',{'participantId':'P','windowId':self.row['windowId'],'revision':1},True)
        # Undoing a first save leaves a versioned tombstone and auditable history.
        another='f'*64;self.row['windowId']=another;self.save(windowId=another);context.mutate(self.manager,'P',{'participantId':'P','windowId':another,'revision':1},True)
        self.assertIsNone(context.entry(self.manager,'P',another)['record']);self.assertEqual(context.listing(self.manager,'P')['total'],2)
        self.assertEqual(len(json.loads(b''.join(context.export(self.manager,'P')))['history']),5)
        self.assertEqual(hashlib.sha256(app.DB_PATH.read_bytes()).hexdigest(),self.sourcehash)
    def test_selected_source_stale_and_append_only(self):
        self.snapshot['sourceSignature']='changed'
        with self.assertRaisesRegex(ValueError,'Source changed'):self.save()
        self.assertEqual(context.listing(self.manager,'P')['total'],0)
        self.snapshot['sourceSignature']=self.manager.signature();self.save()
        with closing(sqlite3.connect(context.location(self.manager))) as c:
            with self.assertRaises(sqlite3.IntegrityError):c.execute('DELETE FROM history')
        with app.db() as c:app.put_setting(c,'participant_id','OTHER')
        with self.assertRaisesRegex(ValueError,'participant'):context.mutate(self.manager,'P',{'participantId':'P','windowId':self.row['windowId'],'revision':1},True)
    def test_corrupt_store_rejected_before_export_bytes(self):
        path=context.location(self.manager);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'not a SQLite database')
        with self.assertRaises(ValueError):next(context.export(self.manager,'P'))
        with self.assertRaises(ValueError):context.entry(self.manager,'P',self.row['windowId'])
        self.assertEqual(hashlib.sha256(app.DB_PATH.read_bytes()).hexdigest(),self.sourcehash)

    def test_stdlib_import_and_empty_export(self):
        result=subprocess.run([sys.executable,'-c',"import discovery_context,discovery_review_access,sys; assert 'numpy' not in sys.modules"],cwd=Path(__file__).parents[1]/'src',capture_output=True,text=True);self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(b''.join(context.export(self.manager,'P')))['history'],[])


@unittest.skipUnless(importlib.util.find_spec('numpy'),'Source numerical environment')
class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.helper=fixtures.WorkflowTests('test_real_pipeline_http_sealed_source_and_lifecycle');self.helper.setUp();self.addCleanup(self.helper.doCleanups)
        self.manager=self.helper.manager
    def test_real_worker_http_no_labels_context_preserved_and_publication(self):
        self.helper.analyze();value=access.read(self.manager,'P');self.assertEqual(value['status'],'current');self.assertGreater(value['offeredWindows'],0)
        row=access.page(self.manager,'P')['items'][0];body={'participantId':'P','reviewIdentity':value['reviewIdentity'],'windowId':row['windowId'],'revision':0,'kind':'unknown','facts':'','hypothesis':''}
        wave=access.waveform(self.manager,'P',body);self.assertTrue(wave['sourceVerified'])
        rawhash=hashlib.sha256(app.DB_PATH.read_bytes()).hexdigest();context.mutate(self.manager,'P',body);self.assertEqual(access.read(self.manager,'P')['status'],'current')
        server=app.AppServer(('127.0.0.1',0),app.Collector(''));server.analysis.close();server.analysis=self.manager;thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();self.addCleanup(lambda:(server.shutdown(),server.server_close(),thread.join()));url=f'http://127.0.0.1:{server.server_port}'
        def get(path):
            with urllib.request.urlopen(url+path) as response:return json.load(response)
        for asset in ('context.html','context.js','context.css'):
            with urllib.request.urlopen(url+'/'+asset) as response:self.assertEqual(response.status,200)
        self.assertEqual(get('/api/discovery/review')['shown'],min(8,value['eligibleWindows']))
        self.assertIsNone(get('/api/discovery/context?windowId='+row['windowId'])['record'])
        self.assertEqual(get('/api/discovery/context?reveal=1&windowId='+row['windowId'])['record']['kind'],'unknown')
        self.assertEqual(len(get('/api/export/discovery-context.json')['history']),1)
        mutation=dict(body,revision=1,revealed=True,kind='skip')
        raw=json.dumps(mutation).encode()
        request=urllib.request.Request(url+'/api/discovery/context/save',data=raw,headers={'Content-Type':'application/json'})
        with self.assertRaises(urllib.error.HTTPError) as denied:urllib.request.urlopen(request)
        self.assertEqual(denied.exception.code,403)
        request.add_header('X-Awear-Local','1')
        with urllib.request.urlopen(request) as response:self.assertEqual(json.load(response)['record']['kind'],'skip')
        undo=urllib.request.Request(url+'/api/discovery/context/undo',data=json.dumps({'participantId':'P','windowId':row['windowId'],'revision':2}).encode(),headers={'Content-Type':'application/json','X-Awear-Local':'1'})
        with urllib.request.urlopen(undo) as response:self.assertEqual(json.load(response)['record']['kind'],'unknown')
        self.assertEqual(len(get('/api/export/discovery-context.json')['history']),3)

        with self.assertRaises(ValueError):access.require(self.manager,'P',dict(body,reviewIdentity='f'*64))
        directory=self.manager.report_path('P').parent;path=directory/'discovery-review.json';sealed=path.read_bytes();pointer=(self.manager.root/(self.manager.scope('P')+'.json')).read_bytes()
        path.write_bytes(sealed+b'x');self.assertEqual(access.read(self.manager,'P')['status'],'unavailable');path.write_bytes(sealed)
        with patch.object(access,'validate',side_effect=ValueError('simulated invalid review')):self.helper.analyze(failed=True)
        self.assertEqual((self.manager.root/(self.manager.scope('P')+'.json')).read_bytes(),pointer);self.assertFalse((self.manager.directory(self.manager.job['id'])/'discovery-review.json').exists())
        self.helper.analyze();self.assertEqual(sealed,(self.manager.report_path('P').parent/'discovery-review.json').read_bytes())
        self.assertEqual(hashlib.sha256(app.DB_PATH.read_bytes()).hexdigest(),rawhash)
        with app.db() as c:c.execute('UPDATE readings SET waveform_json=? WHERE timestamp_ms=?',('[0]',fixtures.BASE))
        self.assertEqual(access.read(self.manager,'P')['status'],'stale');self.assertEqual(context.entry(self.manager,'P',row['windowId'])['revision'],3)
        with self.assertRaises(ValueError):context.mutate(self.manager,'P',dict(body,revision=1,revealed=True))
        remove_quality_payloads(directory);self.assertFalse((directory/'discovery-review.json').exists());self.assertTrue(context.location(self.manager).exists())
