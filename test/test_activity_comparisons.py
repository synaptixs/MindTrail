"""Hand coverage/quantile oracles and full-population, scope, lifecycle/API acceptance."""
from contextlib import closing
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime,timedelta
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

import app
import activity_access as access
from analysis_manager import AnalysisManager,read_json,write_json,remove_quality_payloads
from analysis_provenance import AcquisitionContext,fingerprint
AVAILABLE=importlib.util.find_spec('numpy')
if AVAILABLE:
    import numpy as np
    import activity_comparisons as compare

ZONE=ZoneInfo('America/New_York')
BASE=int(datetime(2026,10,20,8,tzinfo=ZONE).timestamp()*1000)


def context(device='D',session=None,person='P'):
    return asdict(AcquisitionContext(person,device,'awear-cloud','right',256,'RIGHT_TEMP','cloud-timestamp',session))


def window(i,start,value=1,flags=(),acquisition=None):
    acq=acquisition or context()
    return {'windowId':fingerprint(['window',i,acq]),'startMs':start,'endMsExclusive':start+4000,
            'sourceRecordDigests':['a'*64]*4,'acquisition':acq,'acquisitionGroupId':fingerprint(acq),
            'features':{'acRms':value,'log10Power1to40Hz':value/10,'bandShares':[.1,.2,.3,.35,.05]},'qualityFlags':list(flags)}


def report(windows,excluded=(),remainders=()):
    spans=[*windows,*excluded,*remainders];first=min((r['startMs'] for r in spans if r['startMs'] is not None),default=None);last=max((r['endMsExclusive']-1000 for r in spans if r['endMsExclusive'] is not None),default=None)
    return {'windows':windows,'failedWindows':[],'excludedSpans':list(excluded),'remainderSpans':list(remainders),
            'analysisIdentity':'f'*64,'sourceInventory':{'acquisition':context(),'firstTimestampMs':first,'lastTimestampMs':last,'range':{'startMs':None,'endMsExclusive':None}},
            'coverage':{'sourceRecords':len(windows)*4+sum(s['recordCount'] for s in [*excluded,*remainders]),'computedWindows':len(windows),
                        'unflaggedWindows':sum(not w['qualityFlags'] for w in windows),'flaggedWindows':sum(bool(w['qualityFlags']) for w in windows),
                        'excludedRecords':sum(s['recordCount'] for s in excluded),'boundaryRemainderRecordSeconds':sum(s['recordCount'] for s in remainders),'featureFailureRecordSeconds':0}}


@unittest.skipUnless(AVAILABLE,'Optional NumPy analysis environment')
class ActivityComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).resolve()
        old=app.DATA_ROOT,app.DB_PATH;self.addCleanup(lambda:setattr(app,'DATA_ROOT',old[0]));self.addCleanup(lambda:setattr(app,'DB_PATH',old[1]))
        app.configure_storage(self.root);app.setup_db()
        with app.db() as c:app.put_setting(c,'participant_id','P')
    def build(self,r):return compare.build(r,app.DB_PATH,'America/New_York')
    def label(self,name,start,end,person='P',**kwargs):return app.add_label(person,name,'',start,end,**kwargs)

    def test_independent_union_coverage_and_quartile_oracle_preserves_source(self):
        self.label('Read',BASE,BASE+24000)
        ws=[window(i,BASE+a,v,('step',) if a==16000 else ()) for i,(a,v) in enumerate([(0,1),(4000,3),(12000,5),(16000,100),(20000,7),(28000,9)])]
        excluded=[{'startMs':BASE+9000,'endMsExclusive':BASE+10000,'recordCount':2,'reason':'duplicate_timestamp','acquisition':context()}]
        remainders=[{'startMs':BASE+8000,'endMsExclusive':BASE+9000,'recordCount':1,'acquisition':context()}]
        r=report(ws,excluded,remainders);before=deepcopy(r);raw=app.DB_PATH.read_bytes()
        index,payloads=self.build(r);payload=next(iter(payloads.values()));row=payload['days'][0]
        self.assertEqual({k:row[k] for k in ('labeledMs','recordedMs','usableMs','flaggedMs','otherRecordedMs','missingMs')},
                         {'labeledMs':24000,'recordedMs':22000,'usableMs':16000,'flaggedMs':4000,'otherRecordedMs':2000,'missingMs':2000})
        self.assertEqual(row['quartiles'][0],[2.5,4.,5.5]);self.assertEqual(row['usableWindows'],4)
        self.assertEqual(sum(payload['windowAccounting'].values()),6);self.assertEqual(payload['windowAccounting']['unlabeledWindows'],1)
        self.assertEqual(payload['strata'][0]['sessionId'],None);self.assertEqual(index['groups'][0]['knownSessions'],0)
        self.assertEqual((index,payloads),self.build(r));self.assertEqual(r,before);self.assertEqual(app.DB_PATH.read_bytes(),raw)

    def test_uses_full_population_not_exploration_sample(self):
        count=2010;self.label('Read',BASE,BASE+count*4000)
        _,payloads=self.build(report([window(i,BASE+i*4000,float(i%11)) for i in range(count)]))
        p=next(iter(payloads.values()));self.assertEqual(p['strata'][0]['usableWindows'],count);self.assertEqual(p['days'][0]['usableMs'],count*4000)
        ordinary=p['strata'][0]['examples'][0]['windowId'];r=report([window(i,BASE+i*4000,float(100-i%13)) for i in range(count)])
        self.assertEqual(next(iter(self.build(r)[1].values()))['strata'][0]['examples'][0]['windowId'],ordinary)

    def test_review_origin_personal_identity_and_other_participant_isolation(self):
        self.label('Read',BASE,BASE+4000);self.label('Read',BASE+4000,BASE+8000,evidence={'reviewState':'uncertain'})
        self.label('Read',BASE+8000,BASE+12000,origin='model');self.label('Different person',BASE,BASE+12000,person='B')
        _,payloads=self.build(report([window(i,BASE+i*4000,i+1) for i in range(3)]));strata=next(iter(payloads.values()))['strata']
        self.assertEqual({(s['source'],s['reviewState'],s['usableWindows']) for s in strata},{('manual','confirmed',1),('manual','uncertain',1),('model','confirmed',1)})
        self.assertEqual(len({s['activityId'] for s in strata}),1);self.assertEqual({s['activityName'] for s in strata},{'Read'})

    def test_overlap_unknown_and_partial_label_windows_not_assigned(self):
        self.label('Read',BASE+1000,BASE+15000)
        with app.db() as c:
            act=c.execute("SELECT activity_id FROM labels WHERE participant_id='P'").fetchone()[0]
            c.execute("INSERT INTO labels(participant_id,label,note,start_ms,end_ms,activity_id,source,reviewed) VALUES('P','Read','',?,?,?,'manual',1)",(BASE+8000,BASE+12000,act))
            c.execute("INSERT INTO episode_decisions(participant_id,task_key,kind,start_ms,end_ms,generation,note) VALUES('P','x','unknown',?,?,'g','')",(BASE+4000,BASE+8000))
        _,payloads=self.build(report([window(i,BASE+i*4000) for i in range(4)]));p=next(iter(payloads.values()))
        self.assertEqual(p['windowAccounting'],{'ambiguousWindows':1,'boundaryWindows':2,'unknownWindows':1})
        self.assertEqual(sum(s['usableWindows'] for s in p['strata']),0)

    def test_exact_session_strata_and_incompatible_device_and_placement(self):
        self.label('Read',BASE,BASE+12000)
        left=asdict(AcquisitionContext('P','D','bluetooth','left',256,'LEFT','host-arrival','left-session'))
        ws=[window(0,BASE,acquisition=context(session='S1')),window(1,BASE+4000,acquisition=context(session='S2')),
            window(2,BASE+8000,acquisition=context()),window(3,BASE,acquisition=context(device='other')),window(4,BASE,acquisition=left)]
        index,payloads=self.build(report(ws));self.assertEqual(len(payloads),3)
        selected=next(g for g in index['groups'] if g['acquisition']['device_id']=='D' and g['acquisition']['placement']=='right')
        self.assertEqual((selected['knownSessions'],selected['unknownSessionContexts']),(2,1));p=payloads[selected['compatibilityId']]
        self.assertEqual({s['sessionId'] for s in p['strata']},{'S1','S2',None});self.assertTrue(all(s['usableWindows']==1 for s in p['strata']))

    def test_dst_midnight_clipping_and_no_feature_interpolation(self):
        a=int(datetime(2026,11,1,tzinfo=ZONE).timestamp()*1000);b=int(datetime(2026,11,2,tzinfo=ZONE).timestamp()*1000)
        self.assertEqual(list(compare.day_parts(a,b,ZONE)),[('2026-11-01',a,b)]);self.assertEqual(b-a,25*3600000)
        self.label('Read',a,a+25*3600000+8000)
        ws=[window(0,a+2*3600000),window(1,a+3*3600000),window(2,b-2000),window(3,b+4000)]
        _,payloads=self.build(report(ws));p=next(iter(payloads.values()))
        self.assertEqual(p['windowAccounting']['boundaryWindows'],1);self.assertEqual(sum(r['usableWindows'] for r in p['days']),3)
        self.assertEqual([r['date'] for r in p['days']],['2026-11-01','2026-11-02'])
        self.assertEqual(sum(r['recordedMs'] for r in p['days']),16000)
        r=report(ws);r['sourceInventory']['range']={'startMs':a+2*3600000+1000,'endMsExclusive':b+4000}
        # Range-filtered extraction never supplies windows outside the requested range.
        r['windows']=[ws[1],ws[2]]
        _,payloads=self.build(r);self.assertEqual(sum(s['usableWindows'] for s in next(iter(payloads.values()))['strata']),1)

    def test_no_labels_no_records_and_no_usable_features_are_explicit(self):
        _,payloads=self.build(report([window(0,BASE)]));p=next(iter(payloads.values()));self.assertEqual(p['strata'],[]);self.assertEqual(p['windowAccounting'],{'unlabeledWindows':1})
        self.assertEqual(self.build(report([]))[1],{})
        self.label('Read',BASE,BASE+4000)
        _,payloads=self.build(report([window(0,BASE,flags=['flatline'])]));p=next(iter(payloads.values()));self.assertIsNone(p['days'][0]['quartiles']);self.assertEqual(p['strata'][0]['examples'][0]['kind'],'quality-excluded example')

    def source(self):
        with app.db() as c:
            records=[]
            for i in range(64):
                for second in range(4):
                    wave=np.sin(2*np.pi*9*(np.arange(256)+second*256)/256)*(1+i*.001)
                    records.append(('P',BASE+i*60000+second*1000,'D',json.dumps(wave.tolist()),256,0,1,2))
            c.executemany('INSERT INTO readings VALUES(?,?,?,?,?,?,?,?)',records)
        self.label('Read',BASE,BASE+600000);self.label('Code',BASE+600000,BASE+1200000)
    def manager(self):
        m=AnalysisManager(self.root,app.DB_PATH);self.addCleanup(m.close);return m
    def wait(self,m):
        deadline=time.monotonic()+40
        while m.running() and time.monotonic()<deadline:time.sleep(.04)
        self.assertFalse(m.running());self.assertEqual(m.state('P')['job']['state'],'completed',m.state('P'))

    def test_real_worker_paging_waveform_content_scope_stale_and_label_readiness(self):
        self.source();m=self.manager();raw=app.DB_PATH.read_bytes();m.start('P',{'activityComparisons':True});self.wait(m)
        index=access.read(m,'P');self.assertEqual(index['status'],'current');group=index['groups'][0]['compatibilityId']
        p=access.page(m,'P',{'group':group,'limit':'1'});self.assertEqual(p['total'],2);self.assertEqual(p['shown'],1);self.assertTrue(p['hasMore'])
        self.assertEqual(p['support']['knownSessions'],0);self.assertEqual(p['support']['usableDays'],1)
        self.assertEqual(p['support']['unknownSessionWindows'],p['support']['usableWindows'])
        self.assertTrue(any('Session effects' in reason for reason in p['readiness']['reasons']))
        ex=p['strata'][0]['examples'][0];self.assertTrue(access.waveform(m,'P',group,ex['windowId'],index['comparisonIdentity'])['sourceVerified'])
        for window_id,identity in [('a'*64,index['comparisonIdentity']),(ex['windowId'],'old')]:
            with self.assertRaises(ValueError):access.waveform(m,'P',group,window_id,identity)
        self.assertEqual(access.read(m,'B')['status'],'unavailable');self.assertEqual(app.DB_PATH.read_bytes(),raw)
        for params in [{'review':'invalid'},{'origin':'invalid'},{'limit':101},{'offset':-1}]:
            with self.assertRaises(ValueError):access.page(m,'P',{'group':group,**params})
        directory=m.directory(m.state('P')['job']['id']);path=directory/f'activities-{group}.json';before=path.read_bytes();value=json.loads(before);value['participantId']='B';write_json(path,value)
        self.assertEqual(access.read(m,'P',group)['status'],'unavailable');path.write_bytes(before)
        with app.db() as c:c.execute("UPDATE labels SET timing_precision='approximate',start_uncertainty_ms=1000 WHERE participant_id='P'")
        self.assertEqual(access.read(m,'P')['status'],'stale')
        m.start('P',{'activityComparisons':True});self.wait(m);self.assertEqual(access.read(m,'P')['status'],'current')
        self.assertGreater(m.state('P')['job']['reusedWindows'],0)

    def test_optional_settings_publication_failure_and_retention(self):
        self.source();m=self.manager()
        with self.assertRaises(ValueError):m.settings({'activityComparisons':1})
        m.start('P',{'activityComparisons':True});self.wait(m);old=access.read(m,'P')['comparisonIdentity']
        identifier='c'*32;directory=m.directory(identifier);directory.mkdir();m.job={'id':identifier,'participant':'P','scope':m.scope('P'),'signature':m.signature(),**m.settings({'activityComparisons':True})}
        write_json(directory/'status.json',{'state':'completed','analysisIdentity':'broken'});write_json(directory/'summary.json',{'analysisIdentity':'broken'});write_json(directory/'report.json',{})
        write_json(directory/'activities-index.json',{'version':1,'participantId':'P','analysisIdentity':'broken','groups':None})
        write_json(m.root/'active.json',{});m.process=SimpleNamespace(poll=lambda:0);m._watch_loop()
        self.assertEqual(m.job['state'],'failed');self.assertEqual(access.read(m,'P')['comparisonIdentity'],old);self.assertFalse((directory/'activities-index.json').exists())
        write_json(directory/'activities-index.json',{});write_json(directory/('activities-'+'a'*64+'.json'),{});write_json(directory/'keep.json',{})
        remove_quality_payloads(directory);self.assertTrue((directory/'keep.json').exists());self.assertEqual(list(directory.glob('activities-*.json')),[])

    def test_real_http_assets_local_start_and_current_scoped_examples(self):
        self.source();server=app.AppServer(('127.0.0.1',0),app.Collector('',demo=True));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(thread.join,5);self.addCleanup(server.shutdown);base=f'http://127.0.0.1:{server.server_port}'
        for name,marker in [('compare.html',b'id="daily-chart"'),('compare.js',b'/api/activity-comparisons'),('compare.css',b'coverage-strip')]:
            with urllib.request.urlopen(base+'/'+name) as response:self.assertIn(marker,response.read())
        request=urllib.request.Request(base+'/api/analysis/start',data=b'{"activityComparisons":true}',headers={'Content-Type':'application/json'})
        with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(request)
        self.assertEqual(error.exception.code,403);error.exception.close();request.add_header('X-Awear-Local','1')
        with urllib.request.urlopen(request) as response:self.assertEqual(response.status,202)
        self.wait(server.analysis)
        with urllib.request.urlopen(base+'/api/activity-comparisons') as response:index=json.load(response)
        group=index['groups'][0]['compatibilityId']
        with urllib.request.urlopen(base+'/api/activity-comparisons?group='+group) as response:p=json.load(response)
        self.assertEqual(p['policy']['review'],'confirmed');self.assertGreater(p['support']['usableWindows'],0)
        ex=p['strata'][0]['examples'][0]
        with urllib.request.urlopen(base+f'/api/activity-comparisons/waveform?group={group}&window={ex["windowId"]}&identity={index["comparisonIdentity"]}') as response:self.assertEqual(len(json.load(response)['samples']),1024)
        with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(base+'/api/activity-comparisons?group=../x')
        self.assertEqual(error.exception.code,400);error.exception.close()


if __name__=='__main__':unittest.main()
