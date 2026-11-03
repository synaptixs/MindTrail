"""Independent readiness accounting, zero-label worker/HTTP and question-store contracts."""
from contextlib import closing
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

import app
from analysis_manager import AnalysisManager, read_json, remove_quality_payloads
from analysis_provenance import AcquisitionContext, fingerprint
import discovery_contracts as discovery
import discovery_questions as questions


def acquisition(device='D',person='P',session=None):
    return asdict(AcquisitionContext(person,device,'awear-cloud','right',256,'RIGHT_TEMP','cloud-timestamp',session))


def source(windows):
    return {'sourceInventory':{'acquisition':acquisition(),'sourceRevision':'a'*64,'range':{'startMs':None,'endMsExclusive':None},
        'recordCount':len(windows)*4,'firstTimestampMs':min((w['startMs'] for w in windows),default=None),
        'lastTimestampMs':max((w['endMsExclusive']-1000 for w in windows),default=None)},
        'analysisIdentity':'b'*64,'windows':windows,'coverage':{'sourceRecords':len(windows)*4,
        'computedWindows':len(windows),'flaggedWindows':sum(bool(w['qualityFlags']) for w in windows),
        'unflaggedWindows':sum(not w['qualityFlags'] for w in windows)}}


def window(start,flags=(),device='D',person='P',session=None):
    acq=acquisition(device,person,session)
    return {'startMs':start,'endMsExclusive':start+4000,'qualityFlags':list(flags),
        'acquisition':acq,'acquisitionGroupId':fingerprint(acq)}


class ReadinessTests(unittest.TestCase):
    def test_zero_labels_full_population_separate_contexts_and_exact_repeat(self):
        report=source([window(0),window(4000,['flatline']),window(8000,device='other',session='S')])
        before=deepcopy(report);a=discovery.build(report,'P','UTC');b=discovery.build(report,'P','UTC')
        self.assertEqual(a,b);self.assertEqual(before,report)
        self.assertFalse(a['labelsRequired']);self.assertEqual(a['readiness'],'ready_for_exploration')
        self.assertEqual(a['contextCount'],2);self.assertEqual(sum(g['computedWindows'] for g in a['contexts']),3)
        self.assertEqual(sum(g['unflaggedMs'] for g in a['contexts']),8000)
        self.assertEqual(a['missingContext']['unknownSessionWindows'],2)
        self.assertEqual(a['observedExtent'],{'startMs':0,'endMsExclusive':12000})
        self.assertEqual(a['inference']['status'],'not_assessed');discovery.validate(a,'P','b'*64)
        a['contexts'][0]['computedWindows']=999
        with self.assertRaises(ValueError):discovery.validate(a,'P','b'*64)

    def test_empty_and_all_flagged_are_honest_no_forced_support(self):
        self.assertEqual(discovery.build(source([]),'P','UTC')['readiness'],'no_recordings')
        r=discovery.build(source([window(0,['flatline'])]),'P','UTC')
        self.assertEqual(r['readiness'],'no_unflagged_windows');self.assertEqual(r['contexts'][0]['unflaggedMs'],0)
        self.assertEqual(r['missingContext']['contact'],'unmeasured')

    def test_midnight_and_dst_do_not_invent_sessions_or_duplicate_total(self):
        base=int(datetime(2026,11,1,23,59,58,tzinfo=ZoneInfo('America/New_York')).timestamp()*1000)
        r=discovery.build(source([window(base)]),'P','America/New_York')
        self.assertEqual([d['date'] for d in r['contexts'][0]['dates']],['2026-11-01','2026-11-02'])
        self.assertEqual(r['contexts'][0]['computedWindows'],1)
        self.assertIsNone(r['contexts'][0]['acquisition']['session_id'])

    def test_participant_and_accounting_guards(self):
        r=source([window(0,person='other')])
        with self.assertRaises(ValueError):discovery.build(r,'P','UTC')
        r=source([window(0)]);r['coverage']['computedWindows']=8
        with self.assertRaises(ValueError):discovery.build(r,'P','UTC')
        r=source([window(0)])
        self.assertNotEqual(discovery.build(r,'P','UTC')['discoveryIdentity'],discovery.build(r,'P','America/New_York')['discoveryIdentity'])

    def test_bound_and_cancel_are_explicit(self):
        with patch.object(discovery,'MAX_BYTES',1):
            with self.assertRaisesRegex(ValueError,'bound'):discovery.build(source([]),'P','UTC')
        def stop():raise RuntimeError('cancel')
        with self.assertRaisesRegex(RuntimeError,'cancel'):discovery.build(source([]),'P','UTC',check=stop)


class QuestionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.manager=AnalysisManager(self.root,self.root/'raw.sqlite3');self.addCleanup(self.manager.close)
        self.current=patch.object(discovery,'read',return_value={'status':'current','analysisIdentity':'b'*64,'discoveryIdentity':'d'*64});self.current.start();self.addCleanup(self.current.stop)
    def body(self,**kw):return dict(participantId='P',analysisIdentity='b'*64,discoveryIdentity='d'*64,title='What changes?',notes='',stage='discovery',**kw)
    def test_read_has_no_side_effect_and_store_never_writes_raw(self):
        self.manager.database.write_bytes(b'raw snapshot')
        self.assertEqual(questions.page(self.manager,'P')['questions'],[]);self.assertFalse(questions.location(self.manager).exists())
        saved=questions.save(self.manager,'P',self.body());self.assertEqual(saved['revision'],1)
        self.assertEqual(self.manager.database.read_bytes(),b'raw snapshot')
        self.assertEqual(questions.page(self.manager,'other')['total'],0)
        self.assertEqual(questions.page(self.manager,'P')['total'],1)
    def test_compare_and_swap_and_history_prevent_lost_edits(self):
        first=questions.save(self.manager,'P',self.body());body=self.body(id=first['id'],revision=1);body['stage']='candidate'
        second=questions.save(self.manager,'P',body);self.assertEqual(second['revision'],2)
        with self.assertRaisesRegex(ValueError,'changed'):questions.save(self.manager,'P',body)
        with closing(sqlite3.connect(questions.location(self.manager))) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM history').fetchone()[0],2)
        body['participantId']='other';body['revision']=2
        with self.assertRaises(ValueError):questions.save(self.manager,'other',body)
    def test_corrupt_store_is_reported_without_touching_raw(self):
        path=questions.location(self.manager);path.parent.mkdir(parents=True);path.write_bytes(b'not sqlite')
        with self.assertRaisesRegex(ValueError,'storage is unavailable'):questions.page(self.manager,'P')

    def test_profile_path_not_database_location_owns_questions(self):
        alternate=AnalysisManager(self.root/'profile',self.root/'external'/'archive.sqlite3')
        self.addCleanup(alternate.close)
        self.assertEqual(questions.location(alternate),self.root/'profile'/'data'/'processed'/'discovery'/'questions.sqlite3')

    def test_scope_identity_validation_and_no_fake_confirmation(self):
        for changes in [{'participantId':'other'},{'stage':'reserved_confirmation'},{'analysisIdentity':'old'},{'discoveryIdentity':'old'}, {'revision':True},{'title':''},{'id':'bad'}]:
            body=self.body();body.update(changes)
            with self.assertRaises(ValueError):questions.save(self.manager,'P',body)
        self.assertFalse(questions.location(self.manager).exists())
        with patch.object(discovery,'read',return_value={'status':'stale'}):
            with self.assertRaises(ValueError):questions.save(self.manager,'P',self.body())


@unittest.skipUnless(importlib.util.find_spec('numpy'),'Optional source analysis runtime')
class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).resolve()
        old=app.DATA_ROOT,app.DB_PATH;self.addCleanup(lambda:setattr(app,'DATA_ROOT',old[0]));self.addCleanup(lambda:setattr(app,'DB_PATH',old[1]))
        app.configure_storage(self.root);app.setup_db()
        with app.db() as c:app.put_setting(c,'participant_id','P')
        self.manager=AnalysisManager(self.root,app.DB_PATH);self.addCleanup(self.manager.close)
    def add(self):
        wave=json.dumps([math.sin(2*math.pi*10*i/256) for i in range(256)])
        with app.db() as c:c.executemany('INSERT INTO readings(participant_id,timestamp_ms,device_id,sample_count,waveform_json,mean,rms,peak_to_peak) VALUES(?,?,?,?,?,?,?,?)',[('P',i*1000,'D',256,wave,0.,.707,2.) for i in range(12)])
    def analyze(self):
        self.manager.start('P',{});deadline=time.monotonic()+25
        while self.manager.running() and time.monotonic()<deadline:time.sleep(.03)
        state=self.manager.state('P');self.assertEqual(state['job']['state'],'completed',state)
    def test_actual_zero_label_worker_read_stale_corrupt_retention_and_empty(self):
        self.analyze();self.assertEqual(discovery.read(self.manager,'P')['readiness'],'no_recordings')
        self.add();before=hashlib.sha256(app.DB_PATH.read_bytes()).hexdigest();self.analyze()
        first=discovery.read(self.manager,'P');self.assertEqual(first['status'],'current');self.assertEqual(first['coverage']['computedWindows'],3)
        self.assertEqual(discovery.read(self.manager,'other')['status'],'unavailable')
        path=self.manager.report_path('P').parent/'discovery.json';payload=path.read_bytes();self.analyze()
        path=self.manager.report_path('P').parent/'discovery.json';self.assertEqual(path.read_bytes(),payload)
        changed=json.loads(payload);changed['participantId']='other';path.write_text(json.dumps(changed))
        self.assertEqual(discovery.read(self.manager,'P')['status'],'unavailable');path.write_bytes(payload)
        with app.db() as c:c.execute('UPDATE readings SET device_id=? WHERE timestamp_ms=0',('new-device',))
        self.assertEqual(discovery.read(self.manager,'P')['status'],'stale')
        self.assertNotEqual(hashlib.sha256(app.DB_PATH.read_bytes()).hexdigest(),before)
        remove_quality_payloads(path.parent);self.assertFalse(path.exists())
        with app.db() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM labels').fetchone()[0],0)
    def test_http_zero_label_readiness_and_question_local_guard(self):
        self.add();self.analyze()
        server=app.AppServer(('127.0.0.1',0),app.Collector(''));server.analysis.close();server.analysis=self.manager
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def cleanup():server.shutdown();server.server_close();thread.join()
        self.addCleanup(cleanup);url=f'http://127.0.0.1:{server.server_port}'
        def get(path):return urllib.request.urlopen(url+path)
        with get('/api/discovery') as r:data=json.load(r)
        self.assertEqual(data['readiness'],'ready_for_exploration')
        for name in ('discover.html','discover.js','discover.css'):
            with get('/'+name) as r:self.assertEqual(r.status,200)
        body={'participantId':'P','analysisIdentity':data['analysisIdentity'],'discoveryIdentity':data['discoveryIdentity'],'title':'Unlabeled discovery','notes':'','stage':'discovery'}
        request=urllib.request.Request(url+'/api/discovery/questions',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
        with self.assertRaises(urllib.error.HTTPError) as err:urllib.request.urlopen(request)
        self.assertEqual(err.exception.code,403);err.exception.close()
        request.add_header('X-Awear-Local','1')
        with urllib.request.urlopen(request) as r:self.assertEqual(json.load(r)['revision'],1)
        with get('/api/discovery/questions') as r:self.assertEqual(json.load(r)['total'],1)
        with app.db() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM labels').fetchone()[0],0)
