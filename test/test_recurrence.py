"""Independent full-population recurrence oracles and current-source HTTP contracts."""
from contextlib import closing
from copy import deepcopy
from dataclasses import asdict
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.request
from zoneinfo import ZoneInfo

import app
from analysis_manager import AnalysisManager,remove_quality_payloads
from analysis_provenance import AcquisitionContext,fingerprint
import discovery_daily as daily
import recurrence_access as access

BASE=daily.day_bounds('2026-10-20',ZoneInfo('America/New_York'))[0]+3600000


def window(i,day=0,power=0,shares=None,flags=(),session=None,device='D',person='P',start=None):
    acq=asdict(AcquisitionContext(person,device,'awear-cloud','right',256,'RIGHT_TEMP','cloud-timestamp',session_id=session))
    a=BASE+day*86400000+i*4000 if start is None else start
    return {'windowId':fingerprint([i,day,session,device,person,a]),'startMs':a,'endMsExclusive':a+4000,
        'acquisition':acq,'acquisitionGroupId':fingerprint(acq),'qualityFlags':list(flags),'sourceRecordDigests':['a'*64]*4,
        'features':{'acRms':power+10,'log10Power1to40Hz':power,'bandShares':shares or [.1,.2,.3,.25,.15],
            'maxStep':2,'linearTrendFraction':.01,'mainsLikePowerFraction':.02}}


def report(rows):
    return {'analysisIdentity':'b'*64,'sourceInventory':{'sourceRevision':'c'*64},'windows':rows,
        'failedWindows':[],'excludedSpans':[],'remainderSpans':[],'coverage':{'computedWindows':len(rows)}}


@unittest.skipUnless(importlib.util.find_spec('numpy'),'Optional source analysis environment')
class ModelTests(unittest.TestCase):
    def setUp(self):
        import recurrence_model
        self.model=recurrence_model
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.db=Path(self.temp.name)/'source.sqlite3'
        with closing(sqlite3.connect(self.db)) as c:c.execute('CREATE TABLE unrelated(id INTEGER)');c.commit()
    def build(self,rows):
        r=report(rows);index,payloads=daily.build(r,self.db,'P','America/New_York','d'*64)
        return self.model.build(r,index,payloads,'P')
    def payload(self,rows):return next(iter(self.build(rows)[1].values()))
    def test_shared_vector_matches_preexisting_formula_exactly(self):
        import numpy as np
        from spectral_features import feature_vector
        from exploratory_models import feature_vector as p3
        for shares in ([0,0,0,0,0],[.1,.2,.3,.25,.15],[1e-9,.8,.1,.09,.01]):
            w=window(0,power=2,shares=shares);s=np.asarray(shares)
            expected=[2,*np.log10((s[[0,1,2,4]]+1e-6)/(s[3]+1e-6)).tolist()]
            self.assertEqual(feature_vector(w),expected);self.assertEqual(p3(w),expected)
    def test_hand_scaler_distances_date_minima_and_contributions(self):
        rows=[window(0,power=0),window(1,power=2),window(0,1,power=1),window(1,1,power=3),window(0,2,power=4)]
        p=self.payload(rows);self.assertEqual(p['scaler']['median'][0],2);self.assertEqual(p['scaler']['iqrScale'][0],2)
        q=access.retrieve(p,rows[0]['windowId'],'with_power')
        self.assertEqual(q['counts'],{'eligiblePopulation':5,'sameDayExcluded':2,'temporalGuardExcluded':0,'eligibleOtherWindows':3,'otherDates':2,'shown':2,'omittedDates':0})
        self.assertEqual([r['windowId'] for r in q['items']],[rows[2]['windowId'],rows[4]['windowId']])
        self.assertEqual([r['distance'] for r in q['items']],[.5,2]);self.assertEqual(q['distanceQuartiles'],[1,1.5,1.75])
        for r in q['items']:self.assertAlmostEqual(sum(d['squaredContribution'] for d in r['differences']),r['distance']**2)
        self.assertEqual(access.retrieve(p,rows[0]['windowId'],'spectral_only')['status'],'uninformative_features')
    def test_zero_label_full_population_determinism_and_no_writes(self):
        rows=[window(i,power=i%11) for i in range(2100)]+[window(0,1,power=2)]
        before=deepcopy(rows);raw=self.db.read_bytes();a=self.build(rows);b=self.build(rows)
        self.assertEqual(a,b);self.assertEqual(rows,before);self.assertEqual(self.db.read_bytes(),raw)
        p=next(iter(a[1].values()));self.assertEqual(p['scaler']['fitWindows'],2101)
        self.assertEqual(access.retrieve(p,rows[-1]['windowId'],'with_power')['counts']['eligibleOtherWindows'],2100)
    def test_context_and_session_isolation_flags_missing_and_boundary(self):
        midnight=daily.day_bounds('2026-10-20',ZoneInfo('America/New_York'))[1]
        rows=[window(0),window(2,flags=['flatline']),window(3,session='S1'),window(4,session='S2'),window(5,device='OTHER'),window(6,start=midnight-2000)]
        index,payloads=self.build(rows);self.assertEqual(len(payloads),2)
        p=next(p for p in payloads.values() if p['acquisition']['device_id']=='D')
        self.assertEqual(p['accounting']['eligible'],3);self.assertEqual(p['accounting']['flaggedExcluded'],1)
        self.assertEqual(index['windowAccounting']['crossDay'],1)
        unknown=next(c for c in p['conditions'] if c['sessionId'] is None and c['date']=='2026-10-20')
        self.assertEqual(unknown['unflaggedWindows'],1);self.assertEqual(unknown['flaggedWindows'],1)
        self.assertEqual(unknown['recordedUnionMs'],10000);self.assertGreater(unknown['missingMs'],0)
        v=access.family_page(p,{'limit':'1'});self.assertEqual(v['knownSessions'],2);self.assertEqual(v['unknownSessionContexts'],1)
        self.assertEqual(v['shown'],1);self.assertTrue(v['hasMore'])
        with self.assertRaises(ValueError):self.build([window(0,person='OTHER')])
    def test_midnight_guard_empty_constant_and_one_day(self):
        self.assertEqual(self.build([])[0]['groups'],[])
        midnight=daily.day_bounds('2026-10-20',ZoneInfo('America/New_York'))[1]
        rows=[window(0,start=midnight-4000),window(1,start=midnight+4000,power=1),window(2,start=midnight+70000,power=2)]
        q=access.retrieve(self.payload(rows),rows[0]['windowId'],'with_power')
        self.assertEqual(q['counts']['temporalGuardExcluded'],1);self.assertEqual(q['counts']['eligibleOtherWindows'],1)
        self.assertEqual(q['items'][0]['windowId'],rows[2]['windowId']);self.assertIn('Only one other date',q['supportNote'])
        for rows,status in [([window(0)],'no_other_day_support'),([window(0),window(0,1)],'uninformative_features')]:
            q=access.retrieve(self.payload(rows),rows[0]['windowId'],'with_power');self.assertEqual(q['status'],status);self.assertEqual(q['items'],[])
    def test_shape_recurrence_and_day_confounded_amplitude(self):
        a=[.1,.1,.5,.2,.1];b=[.4,.2,.1,.2,.1]
        rows=[window(0,shares=a),window(1,shares=b),window(0,1,power=4,shares=a),window(1,1,power=0,shares=b)]
        p=self.payload(rows);shape=access.retrieve(p,rows[0]['windowId'],'spectral_only')
        self.assertEqual(shape['items'][0]['windowId'],rows[2]['windowId']);self.assertEqual(shape['items'][0]['distance'],0)
        self.assertGreater(access.retrieve(p,rows[0]['windowId'],'with_power')['items'][0]['distance'],0)
    def test_ties_top_ten_bounds_and_invalid_inputs(self):
        rows=[window(0)]+[window(i,day,power=1) for day in range(1,13) for i in range(2)]
        p=self.payload(rows);q=access.retrieve(p,rows[0]['windowId'],'with_power')
        self.assertEqual(q['counts']['shown'],10);self.assertEqual(q['counts']['omittedDates'],2)
        self.assertTrue(all(r['windowId']==rows[2*i+1]['windowId'] for i,r in enumerate(q['items'])))
        for ref,view in [('f'*64,'with_power'),(rows[0]['windowId'],'bad'),('../bad','with_power')]:
            with self.assertRaises(ValueError):access.retrieve(p,ref,view)
        for params in [{'offset':True},{'limit':'101'},{'offset':'-1'}]:
            with self.assertRaises(ValueError):access.family_page(p,params)
        with patch.object(self.model,'MAX_PAYLOAD',1):
            with self.assertRaisesRegex(ValueError,'bound'):self.build(rows)
        with self.assertRaisesRegex(ValueError,'source differs'):self.model.build(report([]),{'analysisIdentity':'a','participantId':'P'},{},'P')


@unittest.skipUnless(importlib.util.find_spec('numpy'),'Optional source analysis environment')
class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).resolve()
        old=app.DATA_ROOT,app.DB_PATH;self.addCleanup(lambda:setattr(app,'DATA_ROOT',old[0]));self.addCleanup(lambda:setattr(app,'DB_PATH',old[1]))
        app.configure_storage(self.root);app.setup_db()
        with app.db() as c:
            app.put_setting(c,'participant_id','P')
            c.executemany('INSERT INTO readings VALUES(?,?,?,?,?,?,?,?)',[
                ('P',BASE+d*86400000+i*1000,'D',json.dumps([(d+1)*math.sin(2*math.pi*(8+d)*j/256) for j in range(256)]),256,0,1,2)
                for d in range(3) for i in range(12)])
        self.manager=AnalysisManager(self.root,app.DB_PATH);self.addCleanup(self.manager.close)
    def analyze(self,failed=False):
        self.manager.start('P',{});deadline=time.monotonic()+25
        while self.manager.running() and time.monotonic()<deadline:time.sleep(.03)
        state=self.manager.state('P');self.assertEqual(state['job']['state'],'failed' if failed else 'completed',state)
    def test_real_pipeline_http_sealed_source_and_lifecycle(self):
        before=hashlib.sha256(app.DB_PATH.read_bytes()).hexdigest();self.analyze();idx=access.read(self.manager,'P');g=idx['groups'][0]['compatibilityId']
        p=access.read(self.manager,'P',g);reference=p['rows'][0]['windowId'];params={'group':g,'identity':idx['recurrenceIdentity'],'reference':reference}
        self.assertEqual(access.page(self.manager,'P',params)['query']['counts']['otherDates'],2)
        wave=access.waveform(self.manager,'P',dict(params,window=reference));self.assertTrue(wave['sourceVerified']);self.assertEqual(len(wave['samples']),1024)
        self.assertEqual(access.read(self.manager,None)['status'],'no_participant');self.assertEqual(access.read(self.manager,'other')['status'],'unavailable')
        server=app.AppServer(('127.0.0.1',0),app.Collector(''));server.analysis.close();server.analysis=self.manager
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();self.addCleanup(lambda:(server.shutdown(),server.server_close(),thread.join()))
        url=f'http://127.0.0.1:{server.server_port}'
        for asset in ['recurrence.html','recurrence.js','recurrence.css']:
            with urllib.request.urlopen(url+'/'+asset) as r:self.assertEqual(r.status,200)
        with urllib.request.urlopen(url+'/api/discovery/recurrence?'+urllib.parse.urlencode(params)) as r:self.assertEqual(json.load(r)['query']['counts']['otherDates'],2)
        with self.assertRaises(ValueError):access.page(self.manager,'P',dict(params,identity='f'*64))
        with self.assertRaises(ValueError):access.waveform(self.manager,'P',dict(params,window='f'*64))
        directory=self.manager.report_path('P').parent;path=directory/f'recurrence-{g}.json';raw=path.read_bytes()
        self.analyze();self.assertEqual(raw,(self.manager.report_path('P').parent/path.name).read_bytes())
        directory=self.manager.report_path('P').parent;path=directory/path.name;path.write_bytes(raw+b'x')
        self.assertEqual(access.read(self.manager,'P',g)['status'],'unavailable')
        with self.assertRaises(ValueError):access.validate_files(directory,'P',idx['analysisIdentity'])
        path.write_bytes(raw)
        manifest=directory/'recurrence-index.json';sealed=manifest.read_bytes();broken=json.loads(sealed)
        broken['groups'][0]['scalerIdentity']='f'*64
        broken['recurrenceIdentity']=fingerprint({k:v for k,v in broken.items() if k!='recurrenceIdentity'})
        manifest.write_text(json.dumps(broken))
        with self.assertRaises(ValueError):access.validate_files(directory,'P',idx['analysisIdentity'])
        manifest.write_bytes(sealed);pointer=(self.manager.root/(self.manager.scope('P')+'.json')).read_bytes()
        with patch.object(access,'validate_files',side_effect=ValueError('simulated invalid recurrence')):self.analyze(failed=True)
        self.assertEqual((self.manager.root/(self.manager.scope('P')+'.json')).read_bytes(),pointer)
        self.assertFalse(list(self.manager.directory(self.manager.job['id']).glob('recurrence-*')))
        self.assertEqual(hashlib.sha256(app.DB_PATH.read_bytes()).hexdigest(),before)
        with app.db() as c:c.execute('UPDATE readings SET waveform_json=? WHERE timestamp_ms=?',('[0]',BASE))
        self.assertEqual(access.read(self.manager,'P')['status'],'stale')
        with self.assertRaises(ValueError):access.waveform(self.manager,'P',dict(params,window=reference))
        remove_quality_payloads(directory);self.assertFalse(list(directory.glob('recurrence-*')))
    def test_http_reader_imports_without_numerical_modules(self):
        code="import sys; import recurrence_access; assert not any(n in sys.modules for n in ('numpy','sklearn','recurrence_model','spectral_features'))"
        result=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[1]/'src',capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
