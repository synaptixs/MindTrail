"""Report accounting, evidence transactions, standalone exports and source preservation."""
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
from html.parser import HTMLParser
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
from analysis_manager import remove_quality_payloads
from analysis_provenance import canonical_json, fingerprint
import discovery_report_access as access
import discovery_report_render as render
import discovery_decisions as decisions
import discovery_context as context
import discovery_questions as questions
from test import test_recurrence as fixtures


@unittest.skipUnless(importlib.util.find_spec('numpy'),'Source numerical environment')
class ModelTests(unittest.TestCase):
    def build(self,rows):
        import discovery_contracts,discovery_daily,recurrence_model,discovery_review_model,discovery_report_model
        source=fixtures.report(rows)
        source['coverage'].update(flaggedWindows=sum(bool(r['qualityFlags']) for r in rows),unflaggedWindows=sum(not r['qualityFlags'] for r in rows))
        source['sourceInventory'].update(acquisition=fixtures.window(0)['acquisition'],range={},recordCount=len(rows)*4,
            firstTimestampMs=min((r['startMs'] for r in rows),default=None),lastTimestampMs=max((r['endMsExclusive']-1000 for r in rows),default=None))
        with tempfile.TemporaryDirectory() as folder:
            db=Path(folder)/'db.sqlite3'
            with closing(sqlite3.connect(db)) as c:c.execute('CREATE TABLE unrelated(id)');c.commit()
            discovery=discovery_contracts.build(source,'P','America/New_York')
            daily,payloads=discovery_daily.build(source,db,'P','America/New_York','d'*64)
            index,families=recurrence_model.build(source,daily,payloads,'P')
            review=discovery_review_model.build(index,families,payloads)
            args=(discovery,daily,payloads,index,families,review)
            return discovery_report_model.build(*args),args

    def test_counts_quartiles_context_isolation_reuse_and_determinism(self):
        rows=[fixtures.window(i,d,power=i+d,shares=[.1,.1,.2+.01*i,.4-.01*i,.2],session='A' if i<2 else 'B') for d in range(3) for i in range(4)]
        rows+=[fixtures.window(4,flags=['flatline']),fixtures.window(0,device='OTHER',power=3)]
        value,args=self.build(rows);import discovery_report_model as model
        self.assertEqual(value,model.build(*args));self.assertEqual(value['familyCount'],2)
        self.assertEqual(value['coverage']['computedWindows'],14);self.assertEqual(value['coverage']['flaggedWindows'],1)
        self.assertEqual(value['dayContextRows'],8)
        g=next(g for g in value['families'] if g['acquisition']['device_id']=='D')
        row=next(r for r in g['conditions'] if r['date']=='2026-10-20' and r['sessionId']=='A')
        self.assertEqual(row['proxyQuartiles'][0],[10.25,10.5,10.75])
        self.assertEqual(value['selection']['eligibleWindows'],13)
        self.assertLessEqual(value['probeCount'],4)
        for probe in value['probes']:
            family=args[4][probe['family']]
            for key,view in [('withPower','with_power'),('spectralOnly','spectral_only')]:
                self.assertEqual(probe[key],fixtures.access.retrieve(family,probe['reference'],view))
            a={r['date']:r['windowId'] for r in probe['withPower']['items']};b={r['date']:r['windowId'] for r in probe['spectralOnly']['items']}
            common=set(a)&set(b)
            self.assertEqual(probe['viewSensitivity']['changedNearestWindow'],sum(a[d]!=b[d] for d in common))
        for key in value['changeTotals']:self.assertEqual(value['changeTotals'][key],sum(r[key] for r in value['changes']))
        with self.assertRaisesRegex(ValueError,'scope'):model.build(dict(args[0],participantId='OTHER'),*args[1:])
        def stop():raise RuntimeError('cancel')
        with self.assertRaisesRegex(RuntimeError,'cancel'):model.build(*args,check=stop)
        with patch.object(model,'MAX_BYTES',1):
            with self.assertRaisesRegex(ValueError,'exceeds'):model.build(*args)

    def test_empty_constant_and_known_change(self):
        empty,_=self.build([]);self.assertEqual(empty['readiness'],'no_recordings');self.assertEqual(empty['families'],[])
        constant,_=self.build([fixtures.window(0),fixtures.window(0,1)])
        self.assertTrue(all(p['viewSensitivity']['status']=='not_comparable' for p in constant['probes']))
        changed,_=self.build([fixtures.window(i,power=0 if i<30 else 5) for i in range(60)])
        self.assertGreater(changed['changeTotals']['retained'],0)
        self.assertTrue(all(len(r['items'])<=3 and r['shown']+r['omitted']==r['retained'] for r in changed['changes']))


@unittest.skipUnless(importlib.util.find_spec('numpy'),'Source numerical environment')
class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.helper=fixtures.WorkflowTests('test_real_pipeline_http_sealed_source_and_lifecycle');self.helper.setUp();self.addCleanup(self.helper.doCleanups)
        self.manager=self.helper.manager;self.helper.analyze()
        self.rawhash=hashlib.sha256(app.DB_PATH.read_bytes()).hexdigest()
    def report(self):return access.page(self.manager,'P')
    def body(self,**kwargs):
        v=self.report();return dict({'participantId':'P','evidenceIdentity':v['evidenceIdentity'],'revision':v['decision']['revision'],'choice':'continue_discovery','notes':''},**kwargs)
    def note(self):
        import discovery_review_access
        v=discovery_review_access.read(self.manager,'P');r=v['items'][0]
        return context.mutate(self.manager,'P',{'participantId':'P','reviewIdentity':v['reviewIdentity'],'windowId':r['windowId'],'revision':0,'kind':'unknown','facts':'<script>alert(1)</script>','hypothesis':'<img src=x onerror=alert(1)>'})
    def test_exact_export_escaping_bounds_and_stdlib(self):
        self.note();v=self.report();n=v['numerical'];self.assertEqual(v['human']['context']['active'],1)
        self.assertEqual(v['human']['context']['sameFeatureSnapshot'],1)
        raw=canonical_json(v);html=render.document(v)
        self.assertEqual(html,render.document(self.report()));self.assertEqual(raw,canonical_json(self.report()))
        self.assertIn(b'&lt;script&gt;alert(1)&lt;/script&gt;',html);self.assertNotIn(b'<script>',html)
        class Offline(HTMLParser):
            def handle_starttag(self,tag,attrs):
                if tag in ('script','link','img','iframe'):raise AssertionError(tag)
                for key,val in attrs:
                    if key in ('src','href'):raise AssertionError((key,val))
        Offline().feed(html.decode());self.assertNotIn(b'url(',html);self.assertNotIn(b'@import',html)
        for key in ('reportIdentity','analysisIdentity'):self.assertIn(n[key].encode(),html)
        clipped=deepcopy(v);g=clipped['numerical']['families'][0];g['conditions']=g['conditions']*100
        clipped['numerical']['families']=[g]*21;clipped['numerical']['familyCount']=21
        text=render.document(clipped).decode();self.assertIn('Showing 20 of 21 families',text);self.assertIn('Table: first 200 of 300 rows; 100 omitted',text)
        code="import discovery_report_access,discovery_report_render,discovery_decisions,sys; assert not any(n in sys.modules for n in ('numpy','sklearn','discovery_report_model'))"
        r=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).parents[1]/'src',capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(hashlib.sha256(app.DB_PATH.read_bytes()).hexdigest(),self.rawhash)
    def test_choices_cas_questions_history_and_source_guards(self):
        v=self.report();self.assertEqual(v['decisionRelation'],'not_recorded')
        q=questions.save(self.manager,'P',{'participantId':'P','analysisIdentity':v['numerical']['analysisIdentity'],'discoveryIdentity':v['numerical']['discoveryIdentity'],'title':'My personal question','notes':'','stage':'candidate'})
        self.assertNotEqual(self.report()['evidenceIdentity'],v['evidenceIdentity'])
        with self.assertRaisesRegex(ValueError,'Evidence changed'):decisions.save(self.manager,'P',dict(participantId='P',evidenceIdentity=v['evidenceIdentity'],revision=0,choice='collect_context'))
        with self.assertRaisesRegex(ValueError,'belonging'):decisions.save(self.manager,'P',self.body(questionId='foreign'))
        body=self.body(questionId=q['id']);record=decisions.save(self.manager,'P',body)
        self.assertEqual(record['question']['title'],q['title']);self.assertEqual(record['inference'],'not_implemented');self.assertEqual(record['confirmation'],'not_reserved')
        self.assertEqual(self.report()['decisionRelation'],'current_evidence');self.assertEqual(self.report()['evidenceIdentity'],body['evidenceIdentity'])
        with self.assertRaisesRegex(ValueError,'Direction changed'):decisions.save(self.manager,'P',body)
        for i in range(26):decisions.save(self.manager,'P',self.body(choice=list(decisions.CHOICES)[i%4],notes=str(i)))
        d=decisions.read(self.manager,'P');self.assertEqual(d['total'],27);self.assertEqual(d['shown'],25);self.assertTrue(d['hasMore'])
        self.assertEqual(decisions.read(self.manager,'P',25)['shown'],2);self.assertEqual(decisions.read(self.manager,'OTHER')['total'],0)
        with closing(sqlite3.connect(decisions.location(self.manager))) as c:
            with self.assertRaises(sqlite3.IntegrityError):c.execute('DELETE FROM decision_history')
        self.note();self.assertEqual(self.report()['decisionRelation'],'earlier_evidence')
        b=self.body();old=decisions.read(self.manager,'P')['revision']
        with patch.object(access,'guard_current',return_value=False):
            with self.assertRaises(ValueError):decisions.save(self.manager,'P',b)
        self.assertEqual(decisions.read(self.manager,'P')['revision'],old)
        self.assertEqual(hashlib.sha256(app.DB_PATH.read_bytes()).hexdigest(),self.rawhash)
    def test_sealed_lifecycle_staleness_and_post_render_guard(self):
        v=access.page(self.manager,'P',internal=True);directory=self.manager.report_path('P').parent;path=directory/'discovery-report.json';sealed=path.read_bytes()
        self.assertEqual(access.page(self.manager,None)['status'],'no_participant');self.assertEqual(access.page(self.manager,'OTHER')['status'],'unavailable')
        with self.assertRaisesRegex(ValueError,'changed'):access.page(self.manager,'P','f'*64)
        self.note()
        with self.assertRaisesRegex(ValueError,'changed'):access.recheck(self.manager,v)
        path.write_bytes(sealed+b'x');self.assertEqual(self.report()['status'],'unavailable');path.write_bytes(sealed)
        pointer=(self.manager.root/(self.manager.scope('P')+'.json')).read_bytes()
        with patch.object(access,'validate_files',side_effect=ValueError('invalid report')):self.helper.analyze(failed=True)
        self.assertEqual((self.manager.root/(self.manager.scope('P')+'.json')).read_bytes(),pointer)
        self.assertFalse((self.manager.directory(self.manager.job['id'])/'discovery-report.json').exists())
        self.helper.analyze();self.assertEqual((self.manager.report_path('P').parent/'discovery-report.json').read_bytes(),sealed)
        decisions.save(self.manager,'P',self.body())
        with app.db() as c:c.execute('UPDATE readings SET waveform_json=? WHERE timestamp_ms=?',('[0]',fixtures.BASE))
        self.assertEqual(self.report()['status'],'stale');self.assertEqual(decisions.read(self.manager,'P')['revision'],1)
        remove_quality_payloads(directory);self.assertFalse(path.exists());self.assertTrue(decisions.location(self.manager).exists())
    def test_concurrent_cas_and_late_evidence_rollback(self):
        body=self.body()
        def save():
            try:return decisions.save(self.manager,'P',body)
            except ValueError:return None
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:save(),range(2)))
        self.assertEqual(sum(r is not None for r in results),1)
        self.assertEqual(decisions.read(self.manager,'P')['total'],1)
        body=self.body()
        # Initial capture and pre-write check succeed; final pre-commit check fails.
        with patch.object(access,'guard_current',side_effect=[True,True,False]):
            with self.assertRaisesRegex(ValueError,'before commit'):decisions.save(self.manager,'P',body)
        self.assertEqual(decisions.read(self.manager,'P')['total'],1)
        self.assertEqual(decisions.read(self.manager,'P')['revision'],1)
    def test_human_all_counts_content_identity_and_old_snapshot(self):
        r=self.note()["record"];p=context.location(self.manager);scope=self.manager.scope('P')
        with closing(sqlite3.connect(p)) as c,c:
            for i in range(60):
                record=deepcopy(r);record['provenance']['sourceRevision']='f'*64
                c.execute('INSERT INTO current VALUES(?,?,?,?)',(scope,f'{i:064x}',1,canonical_json(record)))
        v=self.report();h=v['human']['context'];self.assertEqual((h['active'],h['shown'],h['omitted'],h['earlierSnapshot']),(61,50,11,60))
        with closing(sqlite3.connect(p)) as c,c:
            record['facts']='A changed omitted record';c.execute('UPDATE current SET record=? WHERE key=?',(canonical_json(record),f'{59:064x}'))
        self.assertNotEqual(self.report()['evidenceIdentity'],v['evidenceIdentity'])
    def test_http_assets_exact_exports_mutation_gate_and_race(self):
        server=app.AppServer(('127.0.0.1',0),app.Collector(''));server.analysis.close();server.analysis=self.manager
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();self.addCleanup(lambda:(server.shutdown(),server.server_close(),thread.join()))
        url=f'http://127.0.0.1:{server.server_port}'
        def get(path):
            with urllib.request.urlopen(url+path) as r:return r.read()
        for name in ('report.html','report.js','report.css'):self.assertGreater(len(get('/'+name)),100)
        v=json.loads(get('/api/discovery/report'));view=json.loads(get('/api/discovery/report/view'))
        self.assertNotIn('_guard',v);self.assertEqual(v['snapshotIdentity'],view['snapshotIdentity'])
        self.assertEqual(json.loads(get('/api/export/discovery-report.json?identity='+view['snapshotIdentity'])),v)
        self.assertEqual(get('/api/export/discovery-report.html?identity='+view['snapshotIdentity']),render.document(v))
        request=urllib.request.Request(url+'/api/discovery/decision',data=json.dumps(self.body()).encode(),headers={'Content-Type':'application/json'})
        with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(request)
        self.assertEqual(error.exception.code,403);request.add_header('X-Awear-Local','1')
        with urllib.request.urlopen(request) as response:self.assertEqual(json.load(response)['revision'],1)
        with self.assertRaises(urllib.error.HTTPError):get('/api/export/discovery-report.html?identity='+view['snapshotIdentity'])
        original=render.document
        def change(value):
            result=original(value);self.note();return result
        with patch.object(render,'document',side_effect=change):
            with self.assertRaises(urllib.error.HTTPError) as error:get('/api/export/discovery-report.html')
        self.assertEqual(error.exception.code,400)
