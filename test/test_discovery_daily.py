"""Independent daily feature/count/change oracles and real current-source inspection."""
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
from analysis_manager import AnalysisManager,remove_quality_payloads
from analysis_provenance import AcquisitionContext,fingerprint
import discovery_daily as daily
import discovery_daily_access as access

ZONE=ZoneInfo('America/New_York')
BASE=int(datetime(2026,10,20,17,0,tzinfo=ZONE).timestamp()*1000)


def acq(device='D',person='P'):
    return asdict(AcquisitionContext(person,device,'awear-cloud','right',256,'RIGHT_TEMP','cloud-timestamp'))


def row(i,start=None,value=1,flags=(),device='D',person='P'):
    return {'windowId':fingerprint([i,device,person]),'startMs':BASE+i*4000 if start is None else start,
        'endMsExclusive':(BASE+i*4000 if start is None else start)+4000,'featureValues':[value,value/10,value/100],
        'qualityFlags':list(flags),'sourceRecordDigests':['a'*64]*4,'acquisition':acq(device,person)}


def report(rows,excluded=()):
    windows=[{**w,'acquisitionGroupId':fingerprint(w['acquisition']),
        'features':{'acRms':w['featureValues'][0],'log10Power1to40Hz':w['featureValues'][1],
        'bandShares':[0,0,w['featureValues'][2],0,0]}} for w in rows]
    return {'analysisIdentity':'b'*64,'sourceInventory':{'sourceRevision':'c'*64},'windows':windows,
        'failedWindows':[],'excludedSpans':list(excluded),'remainderSpans':[],
        'coverage':{'computedWindows':len(windows)}}


class ModelTests(unittest.TestCase):
    def test_hand_quartiles_and_before_after_step_oracles(self):
        for actual,expected in zip(daily.quantiles([row(i,value=v) for i,v in enumerate([1,3,5,7])]),[[2.5,4,5.5],[.25,.4,.55],[.025,.04,.055]]):
            for a,b in zip(actual,expected):self.assertAlmostEqual(a,b,places=12)
        step=[row(i,value=1 if i<30 else 4) for i in range(60)]
        changes=daily.change_cues(step)
        self.assertEqual(changes['status'],'changes_found');self.assertEqual(changes['evaluatedBoundaries'],31)
        self.assertEqual(len(changes['retained']),1);cue=changes['retained'][0]
        self.assertEqual(cue['beforeQuartiles'][0],[1,1,1]);self.assertEqual(cue['afterQuartiles'][0],[4,4,4])
        self.assertGreaterEqual(cue['score'],3)
        self.assertEqual(daily.change_cues([row(i) for i in range(60)])['status'],'no_changes_above_threshold')

    def test_gap_flag_and_short_runs_never_make_a_change(self):
        for rows in [[row(i) for i in range(14)],
                     [row(i,value=1 if i<15 else 10,start=BASE+i*4000+(1000 if i>=15 else 0)) for i in range(30)],
                     [row(i,flags=['cue'] if i==15 else (),value=1 if i<15 else 10) for i in range(30)],
                     [row(i,flags=['flatline']) for i in range(40)]]:
            c=daily.change_cues(rows);self.assertEqual(c['retained'],[]);self.assertEqual(c['status'],'insufficient_continuous_support')

    def test_dst_calendar_uses_real_elapsed_hours(self):
        for date,hours in [('2026-03-08',23),('2026-11-01',25)]:
            a,b=daily.day_bounds(date,ZONE);self.assertEqual(b-a,hours*3600000)
            self.assertEqual(list(daily.parts(b-2000,b+2000,ZONE))[0][1:],(b-2000,b))

    def test_range_summary_is_full_windows_not_average_of_bin_medians(self):
        rows=[row(i,value=v) for i,v in enumerate([1,3,5,7])]
        p={'windows':rows,'quality':[{'startMs':BASE,'endMsExclusive':BASE+16000,'state':'computed_unflagged'}],
            'humanContext':[],'startMs':BASE,'endMsExclusive':BASE+16000,'calendarStartMs':BASE-1000,'calendarEndMsExclusive':BASE+86400000,
            'changes':{'retained':[],'status':'insufficient_continuous_support','evaluatedBoundaries':0,'aboveThreshold':0,'suppressed':0}}
        v=daily.selection(p,BASE+1000,BASE+15000)
        self.assertEqual(v['support']['wholeWindows'],2);self.assertEqual(v['support']['selectionBoundaryWindows'],2)
        self.assertEqual(v['selectionQuartiles'][0],[3.5,4,4.5]);self.assertEqual(sum(v['support']['elapsedMsByState'].values()),14000)
        self.assertEqual(v['points'][0]['count'],2)
        with self.assertRaises(ValueError):daily.selection(p,BASE-2000,BASE+8000)

    def test_series_splits_at_missing_or_flagged_windows_and_singletons_remain(self):
        rows=[row(0),row(1,flags=['cue']),row(2),row(3,start=BASE+20000)]
        p={'windows':rows,'quality':[],'humanContext':[],'startMs':BASE,'endMsExclusive':BASE+24000,
            'calendarStartMs':BASE,'calendarEndMsExclusive':BASE+86400000,
            'changes':{'retained':[],'status':'insufficient_continuous_support','evaluatedBoundaries':0,'aboveThreshold':0,'suppressed':0}}
        result=daily.selection(p)
        self.assertEqual([p['count'] for p in result['points']],[1,1,1]);self.assertEqual(len({p['run'] for p in result['points']}),3)
        self.assertEqual(result['support']['flaggedWindows'],1)


@unittest.skipUnless(importlib.util.find_spec('numpy'),'Optional source analysis environment')
class BuildTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).resolve()
        self.db=self.root/'source.sqlite3'
        with closing(sqlite3.connect(self.db)) as c:c.execute('CREATE TABLE unrelated(id INTEGER)');c.commit()
    def build(self,r):return daily.build(r,self.db,'P','America/New_York','d'*64)
    def test_whole_population_unlabeled_and_acquisition_isolation(self):
        rows=[row(i,value=i%11) for i in range(2010)]+[row(3000,device='OTHER')]
        r=report(rows);before=deepcopy(r);dbhash=hashlib.sha256(self.db.read_bytes()).hexdigest()
        index,payloads=self.build(r);self.assertEqual(len(index['contexts']),2)
        self.assertEqual(sum(len(p['windows']) for p in payloads.values()),2011)
        self.assertEqual(index['windowAccounting'],{'computed':2011,'fullyContained':2011,'crossDay':0})
        self.assertEqual((index,payloads),self.build(r));self.assertEqual(r,before)
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(),dbhash)
        self.assertTrue(all(s['state']=='unlabeled' for p in payloads.values() for s in p['humanContext']))
    def test_after_hours_exclusions_and_midnight_window_not_split_into_features(self):
        end=daily.day_bounds('2026-10-20',ZONE)[1]
        excluded=[{'acquisition':acq(),'startMs':BASE-10000,'endMsExclusive':BASE-8000,'recordCount':2}]
        index,payloads=self.build(report([row(0),row(1,start=end-2000)],excluded))
        self.assertEqual(index['windowAccounting']['crossDay'],1)
        first=payloads[(fingerprint(acq()),'2026-10-20')]
        self.assertEqual(first['startMs'],BASE-10000);self.assertEqual(first['endMsExclusive'],end)
        self.assertEqual(len(first['windows']),1);self.assertEqual(first['dayBoundaryWindowsTouching'],1)
        self.assertEqual(first['quality'][0]['state'],'excluded')
        second=payloads[(fingerprint(acq()),'2026-10-21')];self.assertEqual(second['dayQuartiles'],None)
        self.assertEqual(second['endMsExclusive'],end+2000)
    def test_empty_bounds_participant_and_cancel(self):
        index,payload=self.build(report([]));self.assertEqual(index['contexts'],[]);self.assertEqual(payload,{})
        with self.assertRaises(ValueError):self.build(report([row(0,person='other')]))
        with patch.object(daily,'MAX_DAY_BYTES',1):
            with self.assertRaisesRegex(ValueError,'bound'):self.build(report([row(0)]))
        def cancel():raise RuntimeError('cancel')
        with self.assertRaisesRegex(RuntimeError,'cancel'):daily.build(report([row(0)]),self.db,'P','America/New_York','d'*64,check=cancel)


@unittest.skipUnless(importlib.util.find_spec('numpy'),'Optional source analysis environment')
class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).resolve()
        old=app.DATA_ROOT,app.DB_PATH;self.addCleanup(lambda:setattr(app,'DATA_ROOT',old[0]));self.addCleanup(lambda:setattr(app,'DB_PATH',old[1]))
        app.configure_storage(self.root);app.setup_db()
        with app.db() as c:app.put_setting(c,'participant_id','P')
        self.manager=AnalysisManager(self.root,app.DB_PATH);self.addCleanup(self.manager.close)
    def add(self,count=240):
        waves=[json.dumps([amp*math.sin(2*math.pi*10*i/256) for i in range(256)]) for amp in [1,3]]
        with app.db() as c:
            c.executemany('INSERT INTO readings VALUES(?,?,?,?,?,?,?,?)',[('P',BASE+i*1000,'D',waves[i>=120],256,0,1,2) for i in range(count)])
    def analyze(self,failed=False):
        self.manager.start('P',{});deadline=time.monotonic()+25
        while self.manager.running() and time.monotonic()<deadline:time.sleep(.03)
        result=self.manager.state('P');self.assertEqual(result['job']['state'],'failed' if failed else 'completed',result)
    def current(self):
        index=access.read(self.manager,'P');g=index['contexts'][0];date=g['days'][0]['date']
        return index,g['acquisitionGroupId'],date
    def test_real_worker_scope_hash_exact_waveform_and_preservation(self):
        self.add();before=hashlib.sha256(app.DB_PATH.read_bytes()).hexdigest();self.analyze()
        index,g,date=self.current();first=access.read(self.manager,'P',g,date);path=self.manager.report_path('P').parent;raw=(path/f'discovery-day-{g}-{date}.json').read_bytes()
        view=access.page(self.manager,'P',{'group':g,'date':date})
        self.assertEqual(view['support']['unflaggedWindows'],60);self.assertTrue(view['changes']['items'])
        exact=access.waveform(self.manager,'P',{'group':g,'date':date,'window':view['examples']['ordinary'],'identity':index['dailyIdentity']})
        self.assertTrue(exact['sourceVerified']);self.assertEqual(len(exact['samples']),1024)
        self.assertEqual(access.read(self.manager,'other')['status'],'unavailable')
        self.analyze();newpath=self.manager.report_path('P').parent
        self.assertEqual(raw,(newpath/f'discovery-day-{g}-{date}.json').read_bytes())
        self.assertEqual(hashlib.sha256(app.DB_PATH.read_bytes()).hexdigest(),before)
        payload=newpath/f'discovery-day-{g}-{date}.json';broken=json.loads(payload.read_text());broken['acquisition']['device_id']='other';payload.write_text(json.dumps(broken))
        self.assertEqual(access.read(self.manager,'P',g,date)['status'],'unavailable')
        with self.assertRaises(ValueError):access.validate_files(newpath,'P',index['analysisIdentity'])
        payload.write_bytes(raw)
        with self.assertRaises(ValueError):access.waveform(self.manager,'P',{'group':g,'date':date,'window':'f'*64,'identity':index['dailyIdentity']})
        with app.db() as c:c.execute('UPDATE readings SET waveform_json=? WHERE timestamp_ms=?',('[0]' ,BASE))
        self.assertEqual(access.read(self.manager,'P')['status'],'stale')
        with self.assertRaises(ValueError):access.waveform(self.manager,'P',{'group':g,'date':date,'window':view['examples']['ordinary'],'identity':index['dailyIdentity']})
    def test_publication_failure_preserves_previous_complete_daily_report(self):
        self.add();self.analyze();pointer=(self.manager.root/(self.manager.scope('P')+'.json')).read_bytes();old=self.manager.report_path('P').parent
        with patch.object(access,'validate_files',side_effect=ValueError('simulated invalid daily sidecar')):self.analyze(failed=True)
        self.assertEqual((self.manager.root/(self.manager.scope('P')+'.json')).read_bytes(),pointer)
        self.assertEqual(access.read(self.manager,'P')['status'],'current')
        failed=self.manager.directory(self.manager.job['id']);self.assertFalse(list(failed.glob('discovery-day-*')))
        self.assertTrue((old/'discovery-daily-index.json').is_file())
        remove_quality_payloads(old);self.assertFalse(list(old.glob('discovery-day-*')))
    def test_human_context_retains_review_timing_without_filtering_features(self):
        self.add();self.analyze();index,g,date=self.current();original=access.read(self.manager,'P',g,date)
        label=app.add_label('P','Personal activity','Synthetic only',BASE+30000,BASE+210000)
        with app.db() as c:c.execute("UPDATE labels SET review_state='uncertain',timing_precision='approximate',start_uncertainty_ms=5000,end_uncertainty_ms=5000 WHERE id=?",(label,))
        self.analyze();newindex,g,date=self.current();updated=access.read(self.manager,'P',g,date)
        self.assertEqual(updated['windows'],original['windows']);self.assertEqual(updated['dayQuartiles'],original['dayQuartiles'])
        self.assertNotEqual(newindex['dailyIdentity'],index['dailyIdentity'])
        labels=[s['label'] for s in updated['humanContext'] if s['label']]
        self.assertEqual(len(labels),1);self.assertEqual(labels[0]['review_state'],'uncertain')
        self.assertEqual(labels[0]['timing_precision'],'approximate');self.assertEqual(labels[0]['start_uncertainty_ms'],5000)

    def test_http_current_selection_scope_and_assets(self):
        self.add();self.analyze();index,g,date=self.current()
        server=app.AppServer(('127.0.0.1',0),app.Collector(''));server.analysis.close();server.analysis=self.manager
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def cleanup():server.shutdown();server.server_close();thread.join()
        self.addCleanup(cleanup);url=f'http://127.0.0.1:{server.server_port}'
        with urllib.request.urlopen(url+'/discovery_daily.js') as r:self.assertEqual(r.status,200)
        path=f'/api/discovery/daily?group={g}&date={date}&startMs={BASE+2000}&endMs={BASE+58000}'
        with urllib.request.urlopen(url+path) as r:v=json.load(r)
        self.assertEqual(v['support']['wholeWindows'],13);self.assertEqual(v['support']['selectionBoundaryWindows'],2)
        for params in [{'group':'../bad','date':date},{'group':g,'date':date,'startMs':'True'},{'group':g,'date':date,'endMs':BASE-1}]:
            with self.assertRaises(ValueError):access.page(self.manager,'P',params)
        with app.db() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM labels').fetchone()[0],0)
