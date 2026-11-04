"""Independent question linkage, longitudinal support and exact HTTP snapshot contracts."""
from copy import deepcopy
import hashlib
from html.parser import HTMLParser
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error

import app
from analysis_provenance import canonical_json
import discovery_insights as model
import discovery_report_access as access
import discovery_report_render as render
import discovery_questions as questions
from test import test_discovery_report as reports
from test import test_recurrence as fixtures


@unittest.skipUnless(importlib.util.find_spec('numpy'),'Source numerical environment')
class ModelTests(unittest.TestCase):
    def build(self,rows,question=None,lens='recurrence_conditions'):
        n,_=reports.ModelTests('test_empty_constant_and_known_change').build(rows)
        human={'questions':{'participantId':'P','questions':[]}}
        return model.build(n,human,question,lens),n,human
    def test_hand_counts_ranges_row_references_and_context_isolation(self):
        rows=[fixtures.window(i,d,power=i+d,session='A' if i<2 else 'B') for d in range(3) for i in range(4)]
        rows+=[fixtures.window(0,device='OTHER',power=10),fixtures.window(4,flags=['flatline'])]
        value,n,human=self.build(rows);self.assertEqual(value,model.build(n,human));self.assertEqual(value['familyCount'],2)
        g=next(g for g in value['families'] if g['acquisition']['device_id']=='D')
        self.assertEqual((g['computedWindows'],g['unflaggedWindows'],g['flaggedWindows'],g['featureDateCount'],g['knownSessions']),(13,12,1,3,2))
        r=g['features'][0]['medianRange'];self.assertEqual(r['minimum']['value'],10.5);self.assertEqual(r['maximum']['value'],14.5)
        self.assertEqual(r['minimum']['firstSourceRow']['sessionId'],'A');self.assertEqual(r['maximum']['firstSourceRow']['sessionId'],'B')
        self.assertEqual(r['minimum']['firstSourceRow']['unflaggedWindows'],2)
        original=next(x for x in n['families'] if x['compatibilityId']==g['family'])
        self.assertEqual(g['recordedContextMs'],sum(r['recordedUnionMs'] for r in original['conditions']))
        self.assertEqual(g['missingContextMs'],sum(r['missingMs'] for r in original['conditions']))
        self.assertEqual(g['dayContextRows'],len(original['conditions']))
        for probe in g['probes']:
            source=next(p for p in n['probes'] if p['reference']==probe['reference'])
            self.assertEqual(probe['viewSensitivity'],source['viewSensitivity'])
            for key in ('withPower','spectralOnly'):self.assertEqual(probe['views'][key]['counts'],source[key]['counts'])
        before=deepcopy(n);model.build(n,human,lens='open_review');self.assertEqual(n,before)
    def test_empty_flagged_one_day_constant_and_view_specific_status(self):
        self.assertEqual(self.build([])[0]['status'],'no_recordings')
        self.assertEqual(self.build([fixtures.window(0,flags=['flatline'])])[0]['status'],'no_unflagged_support')
        self.assertEqual(self.build([fixtures.window(0),fixtures.window(1,power=2)])[0]['status'],'no_cross_day_support')
        self.assertEqual(self.build([fixtures.window(0),fixtures.window(0,1)])[0]['status'],'no_informative_sampled_probe')
        self.assertEqual(self.build([fixtures.window(0)],lens='daily_variation')[0]['status'],'exploratory_evidence')
        self.assertEqual(self.build([fixtures.window(0)],lens='open_review')[0]['status'],'manual_review')
        v,n,h=self.build([fixtures.window(0),fixtures.window(0,1)])
        r=v['families'][0]['features'][0]['medianRange'];self.assertEqual(r['minimum']['tiedRows'],2);self.assertEqual(r['minimum'],r['maximum'])
        crossing=fixtures.daily.day_bounds('2026-10-20',fixtures.ZoneInfo('America/New_York'))[1]-2000
        self.assertEqual(self.build([fixtures.window(0,start=crossing)])[0]['status'],'no_whole_day_support')
        separate=self.build([fixtures.window(0,device='A'),fixtures.window(0,1,device='B')])[0]
        self.assertEqual(separate['status'],'no_cross_day_support')
    def test_personal_question_not_interpreted_and_invalid_scope_or_lens(self):
        _,n,h=self.build([fixtures.window(0)])
        q={'id':'a'*32,'title':'My own entirely different activity?','revision':3,'notes':'An aim, not a finding','stage':'candidate','analysisIdentity':'old'};h['questions']['questions']=[q]
        v=model.build(n,h,q['id'],'open_review');self.assertEqual(v['question']['record'],q);self.assertEqual(v['question']['analysisRelation'],'earlier_analysis')
        self.assertEqual(v['status'],'manual_review');self.assertEqual(v['confirmation'],'not_reserved');self.assertEqual(v['inference'],'not_implemented')
        q['analysisIdentity']=n['analysisIdentity'];self.assertEqual(model.build(n,h,q['id'])['question']['analysisRelation'],'same_analysis')
        for question,lens in [('bad','open_review'),('b'*32,'open_review'),(None,'bad'),([], 'open_review')]:
            with self.assertRaises(ValueError):model.build(n,h,question,lens)
        h['questions']['participantId']='OTHER'
        with self.assertRaisesRegex(ValueError,'scope'):model.build(n,h)


@unittest.skipUnless(importlib.util.find_spec('numpy'),'Source numerical environment')
class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.helper=fixtures.WorkflowTests('test_real_pipeline_http_sealed_source_and_lifecycle');self.helper.setUp();self.addCleanup(self.helper.doCleanups);self.helper.analyze();self.manager=self.helper.manager
        self.rawhash=hashlib.sha256(app.DB_PATH.read_bytes()).hexdigest()
    def question(self,title='<script>My question?</script>'):
        v=access.page(self.manager,'P');n=v['numerical']
        return questions.save(self.manager,'P',{'participantId':'P','analysisIdentity':n['analysisIdentity'],'discoveryIdentity':n['discoveryIdentity'],'title':title,'notes':'<img src=x onerror=alert(1)>','stage':'candidate'})
    def test_question_snapshot_selection_identity_escape_and_offline_targets(self):
        q=self.question();path=questions.location(self.manager);original=path.read_bytes();starter=access.page(self.manager,'P');v=access.page(self.manager,'P',question=q['id'],lens='open_review')
        self.assertEqual(v['evidenceIdentity'],starter['evidenceIdentity']);self.assertNotEqual(v['snapshotIdentity'],starter['snapshotIdentity'])
        self.assertEqual(v['investigation']['question']['record']['revision'],1)
        html=render.document(v);self.assertIn(b'&lt;script&gt;My question?&lt;/script&gt;',html);self.assertNotIn(b'<script>',html)
        self.assertEqual(html,render.document(access.page(self.manager,'P',question=q['id'],lens='open_review')))
        class Links(HTMLParser):
            def __init__(self):super().__init__();self.ids=set();self.links=[]
            def handle_starttag(self,tag,attrs):
                if tag in ('script','link','img','iframe'):raise AssertionError(tag)
                for k,val in attrs:
                    if k=='id':self.ids.add(val)
                    if k=='src':raise AssertionError(val)
                    if k=='href':self.links.append(val)
        parser=Links();parser.feed(render.document(starter).decode())
        self.assertTrue(parser.links);self.assertTrue(all(link.startswith('#') and link[1:] in parser.ids for link in parser.links))
        self.assertEqual(path.read_bytes(),original);self.assertEqual(hashlib.sha256(app.DB_PATH.read_bytes()).hexdigest(),self.rawhash)
        n=v['numerical'];questions.save(self.manager,'P',dict(q,participantId='P',analysisIdentity=n['analysisIdentity'],discoveryIdentity=n['discoveryIdentity'],title='Edited question',notes='',stage='discovery'))
        with self.assertRaisesRegex(ValueError,'changed'):access.page(self.manager,'P',v['snapshotIdentity'],question=q['id'],lens='open_review')
        self.assertEqual(access.page(self.manager,'P',question=q['id'])['investigation']['question']['record']['revision'],2)
    def test_http_exact_lens_exports_invalid_selections_and_post_render_guard(self):
        q=self.question();server=app.AppServer(('127.0.0.1',0),app.Collector(''));server.analysis.close();server.analysis=self.manager
        t=threading.Thread(target=server.serve_forever,daemon=True);t.start();self.addCleanup(lambda:(server.shutdown(),server.server_close(),t.join()))
        base=f'http://127.0.0.1:{server.server_port}'
        def get(path):
            with urllib.request.urlopen(base+path) as r:return r.read()
        for lens in model.LENSES:
            params='?question='+q['id']+'&lens='+lens;v=json.loads(get('/api/discovery/report'+params));view=json.loads(get('/api/discovery/report/view'+params));suffix=params+'&identity='+view['snapshotIdentity']
            self.assertEqual(v['snapshotIdentity'],view['snapshotIdentity']);self.assertEqual(v['investigation'],view['investigation'])
            self.assertEqual(get('/api/export/discovery-report.html'+suffix),render.document(v));self.assertEqual(get('/api/export/discovery-report.json'+suffix),canonical_json(v).encode())
            self.assertEqual(v['investigation']['selection']['lens'],lens)
        for params in ('?lens=bad','?question='+'f'*32,'?question=bad'):
            with self.assertRaises(urllib.error.HTTPError) as error:get('/api/discovery/report/view'+params)
            self.assertEqual(error.exception.code,400);error.exception.close()
        v=access.page(self.manager,'P',question=q['id'],lens='open_review')
        with self.assertRaises(urllib.error.HTTPError) as error:get('/api/export/discovery-report.html?identity='+v['snapshotIdentity'])
        self.assertEqual(error.exception.code,400);error.exception.close()
        original=render.sections
        def change(value,**kwargs):
            result=original(value,**kwargs);n=value['numerical'];questions.save(self.manager,'P',dict(q,participantId='P',analysisIdentity=n['analysisIdentity'],discoveryIdentity=n['discoveryIdentity'],title='Changed during render',notes='',stage='discovery'));return result
        with patch.object(render,'sections',side_effect=change):
            with self.assertRaises(urllib.error.HTTPError) as error:get('/api/discovery/report/view?question='+q['id'])
        self.assertEqual(error.exception.code,400);error.exception.close()
        self.assertEqual(hashlib.sha256(app.DB_PATH.read_bytes()).hexdigest(),self.rawhash)
    def test_stdlib_model_and_dirty_method_identity_guard(self):
        code="import discovery_insights,discovery_report_access,discovery_report_render,sys; assert not any(n in sys.modules for n in ('numpy','sklearn','spectral_features'))"
        r=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).parents[1]/'src',capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr)
        v=access.page(self.manager,'P',internal=True)
        with patch.object(render,'method_identity',return_value='changed'):
            with self.assertRaisesRegex(ValueError,'changed'):access.recheck(self.manager,v)
