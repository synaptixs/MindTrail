"""Independent distance/count oracles and transaction/HTTP adoption regressions."""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

import app
import episode_review
import similar_periods as similar


def fixture():
    def point(i, start, vector):
        return {'windowId':hashlib.sha256(str(i).encode()).hexdigest(),'startMs':start,'endMsExclusive':start+4000,
                'coordinates':[0.,0.],'modelFeatures':vector}
    points=[point(0,0,[0.,0.,0.,0.,0.])]
    points += [point(i,i*400_000,[float(i),float(i*2),0.,0.,0.]) for i in range(1,21)]
    return {'participantId':'A','explorationIdentity':'f'*64,'acquisitionGroupId':'a'*64,'acquisition':{'source':'awear-cloud','placement':'right'},
            'view':'with_power','status':'current','sourceSignature':'source','points':points,
            'pca':{'features':['power','delta/beta','theta/beta','alpha/beta','high/beta'],'scaling':{'iqrScale':[1.,2.,1.,1.,1.]}}}


def reference():
    return {'id':1,'label':'Reading','activity_id':'activity','participant_id':'A','start_ms':0,'end_ms':10_000,
            'review_state':'confirmed','source':'manual','timing_precision':'unknown','version':1}


class DistanceTests(unittest.TestCase):
    def test_hand_distance_explanations_and_zero_distance(self):
        distance,changes=similar.distance([0,0,0],[3,8,0],['a','b','c'],[1,2,10])
        self.assertEqual(distance,5);self.assertEqual([d['scaledDelta'] for d in changes],[3,4,0])
        self.assertEqual([d['distanceShare'] for d in changes],[.36,.64,0])
        self.assertEqual(changes[1]['delta'],8)
        zero,details=similar.distance([2,2],[2,2],['a','b'],[1,1]);self.assertEqual(zero,0)
        self.assertEqual(sum(d['distanceShare'] for d in details),0)
        for vector,scales in [([math.nan], [1]),([1],[0]),([], [1])]:
            with self.assertRaises(ValueError):similar.distance([1],vector,['a'],scales)

    def test_full_features_not_pca_top_n_and_input_preservation(self):
        data=fixture();before=deepcopy(data);labels=[reference()]
        data['points'][1]['modelFeatures']=[0,0,5,0,0]
        data['points'][2]['modelFeatures']=[3,8,0,0,0] # same Euclidean distance, same PCA: stable time tie
        result=similar.retrieve(data,labels,[],data['points'][0]['windowId'])
        self.assertEqual(result,similar.retrieve(data,labels,[],data['points'][0]['windowId']))
        self.assertEqual([p['windowId'] for p in result['items'][:2]],[data['points'][3]['windowId'],data['points'][1]['windowId']])
        self.assertAlmostEqual(result['items'][0]['distance'],math.sqrt(18))
        self.assertEqual(result['counts'],{'selected':21,'guardExcluded':1,'eligible':20,'spacingSuppressed':0,'spacedCandidates':20,'shown':10,'limitOmitted':10})
        self.assertEqual(labels,[reference()]);self.assertEqual(before['pca'],data['pca'])
        self.assertTrue(all(p['annotation']['canAssign'] for p in result['items']))

    def test_half_open_guard_gap_boundary_and_counts(self):
        data=fixture();data['points']=data['points'][:5]
        for point,start in zip(data['points'],[0,69_999,70_000,134_000,198_000]):point.update(startMs=start,endMsExclusive=start+4000)
        result=similar.retrieve(data,[reference()],[],data['points'][0]['windowId'],1)
        self.assertEqual(result['counts']['guardExcluded'],2) # 70s is the exact guard end
        self.assertEqual(result['counts']['spacedCandidates'],3) # exactly 60s interval gaps allowed
        data['points'][3].update(startMs=133_999,endMsExclusive=137_999)
        result=similar.retrieve(data,[reference()],[],data['points'][0]['windowId'],1)
        self.assertEqual(result['counts']['spacingSuppressed'],1)
        c=result['counts'];self.assertEqual(c['selected'],c['guardExcluded']+c['eligible']);self.assertEqual(c['eligible'],c['spacingSuppressed']+c['spacedCandidates'])
        for invalid in [True,0,2,'5']:
            with self.assertRaises(ValueError):similar.retrieve(data,[reference()],[],None,invalid)

    def test_reference_requires_single_closed_confirmed_containing_activity(self):
        data=fixture();window=data['points'][0]['windowId']
        self.assertEqual(similar.retrieve(data,[],[])['status'],'no_confirmed_reference')
        for change in [{'review_state':'needs_review'},{'end_ms':None},{'start_ms':1},{'end_ms':3999}]:
            label={**reference(),**change}
            with self.assertRaises(ValueError):similar.retrieve(data,[label],[],window)
        with self.assertRaises(ValueError):similar.retrieve(data,[reference(),reference()],[],window)
        with self.assertRaises(ValueError):similar.retrieve(data,[reference()],[{'start_ms':0,'end_ms':4000}],window)
        self.assertEqual(similar.retrieve(data,[reference()],[],window)['reference']['label']['timing_precision'],'unknown')

    def test_annotations_protected_unknown_pending_and_fingerprint_versions(self):
        data=fixture();window=data['points'][0]['windowId'];target=data['points'][1]
        label={**reference(),'id':2,'start_ms':target['startMs'],'end_ms':target['endMsExclusive'],'source':'model','review_state':'needs_review'}
        result=similar.retrieve(data,[reference(),label],[],window)
        item=result['items'][0];self.assertTrue(item['annotation']['canAssign'])
        old=item['proposalId'];label['version']+=1
        self.assertNotEqual(old,similar.retrieve(data,[reference(),label],[],window)['items'][0]['proposalId'])
        for source,state in [('manual','needs_review'),('schedule','confirmed'),('model','uncertain')]:
            label.update(source=source,review_state=state)
            self.assertFalse(similar.retrieve(data,[reference(),label],[],window)['items'][0]['annotation']['canAssign'])
        unknown=[{'start_ms':target['startMs'],'end_ms':target['endMsExclusive']}]
        self.assertFalse(similar.retrieve(data,[reference()],unknown,window)['items'][0]['annotation']['canAssign'])
        body={'reviewed':True,'target':item['windowId'],'proposalId':old}
        with self.assertRaises(ValueError):similar.proposal(similar.retrieve(data,[reference(),label],[],window),body)


class AdoptionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).resolve()
        old=app.DATA_ROOT,app.DB_PATH;self.addCleanup(lambda:setattr(app,'DATA_ROOT',old[0]));self.addCleanup(lambda:setattr(app,'DB_PATH',old[1]))
        app.configure_storage(self.root);app.setup_db()
        with app.db() as c:app.put_setting(c,'participant_id','A')
        self.label=app.add_label('A','Reading','Keep me',0,10_000)
        self.data=fixture();self.manager=SimpleNamespace(database=app.DB_PATH,signature=lambda:'source')
        self.viewpatch=patch('similar_periods.read_view',return_value=self.data);self.viewpatch.start();self.addCleanup(self.viewpatch.stop)
        self.body={'participantId':'A','group':self.data['acquisitionGroupId'],'view':'with_power','identity':self.data['explorationIdentity'],'reference':self.data['points'][0]['windowId']}

    def search(self):return similar.search(self.manager,'A',self.body)
    def test_scope_snapshot_source_and_aba_rejection(self):
        first=self.search();self.assertEqual(first['reference']['label']['id'],self.label)
        for key,value in [('participantId','B'),('identity','old')]:
            with self.assertRaises(ValueError):similar.search(self.manager,'A',{**self.body,key:value})
        with patch.object(self.manager,'signature',return_value='changed'):
            with self.assertRaises(ValueError):self.search()
        item=first['items'][0];body={**self.body,'target':item['windowId'],'proposalId':item['proposalId'],'reviewed':True}
        app.add_label('A','Reading','edited',0,10_000,self.label)
        app.add_label('A','Reading','Keep me',0,10_000,self.label)
        with self.assertRaises(ValueError):similar.proposal(self.search(),body)

    def test_transaction_validation_rolls_back_and_model_origin_split_undo(self):
        with app.db() as c:
            c.execute("INSERT INTO labels(participant_id,label,note,start_ms,end_ms,source,reviewed) VALUES('A','Work','',390000,420000,'schedule',0)")
        result=self.search();item=result['items'][0];body={'target':item['windowId'],'proposalId':item['proposalId'],'reviewed':True}
        before=None
        with app.db() as c:before=episode_review.snapshot(c,'A')
        def context(c):return {'kind':'similar-period-review','match':similar.proposal(similar.search(self.manager,'A',self.body,c),body)}
        saved=app.add_label('A','Reading','',item['startMs'],item['endMsExclusive'],activity_id=result['reference']['label']['activity_id'],expected_participant='A',origin='model',context_provider=context)
        with app.db() as c:
            row=c.execute('SELECT * FROM labels WHERE id=?',(saved,)).fetchone();self.assertEqual(row['source'],'model');self.assertEqual(row['review_state'],'confirmed');self.assertEqual(row['timing_precision'],'unknown')
            self.assertEqual([(r[0],r[1]) for r in c.execute("SELECT start_ms,end_ms FROM labels WHERE source='schedule' ORDER BY start_ms")],[(390000,400000),(404000,420000)])
            op=c.execute('SELECT MAX(id) FROM episode_operations').fetchone()[0];episode_review.undo(c,'A',op)
            self.assertEqual(episode_review.snapshot(c,'A'),before)
        def invalid(c):raise ValueError('Stale proposal')
        with self.assertRaises(ValueError):app.add_label('A','New activity','',800000,804000,context_provider=invalid)
        with app.db() as c:self.assertFalse(c.execute("SELECT 1 FROM activities WHERE name='New activity'").fetchone())

    def test_http_guards_review_adoption_conflict_and_undo(self):
        server=app.AppServer(('127.0.0.1',0),app.Collector('',demo=False));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(thread.join,5);self.addCleanup(server.shutdown)
        base=f'http://127.0.0.1:{server.server_port}'
        with patch.object(server.analysis,'signature',return_value='source'):
            def post(path,body,headers=None):
                req=urllib.request.Request(base+path,data=json.dumps(body).encode(),headers=headers or {'X-Awear-Local':'1','Content-Type':'application/json'})
                with urllib.request.urlopen(req) as response:return json.load(response)
            with self.assertRaises(urllib.error.HTTPError) as error:post('/api/similarity/search',self.body,{'Content-Type':'application/json'})
            self.assertEqual(error.exception.code,403);error.exception.close()
            with self.assertRaises(urllib.error.HTTPError) as error:post('/api/similarity/search',self.body,{'X-Awear-Local':'1','Origin':'http://remote.test'})
            self.assertEqual(error.exception.code,403);error.exception.close()
            result=post('/api/similarity/search',self.body);item=result['items'][0]
            body={**self.body,'target':item['windowId'],'proposalId':item['proposalId'],'label':'Reading'}
            with self.assertRaises(urllib.error.HTTPError) as error:post('/api/similarity/accept',body)
            self.assertEqual(error.exception.code,400);error.exception.close()
            with self.assertRaises(urllib.error.HTTPError) as error:post('/api/similarity/accept',{**body,'reviewed':True,'target':'forged'})
            error.exception.close()
            accepted=post('/api/similarity/accept',{**body,'reviewed':True})
            with app.db() as c:
                row=c.execute('SELECT * FROM labels WHERE id=?',(accepted['id'],)).fetchone();self.assertEqual(row['source'],'model');self.assertEqual(row['start_ms'],item['startMs']);self.assertEqual(row['end_ms'],item['endMsExclusive'])
                ledger=c.execute('SELECT id,context_json FROM episode_operations ORDER BY id DESC LIMIT 1').fetchone();context=json.loads(ledger['context_json']);self.assertEqual(context['reference']['label']['id'],self.label);self.assertEqual(context['match']['proposalId'],item['proposalId'])
            with self.assertRaises(urllib.error.HTTPError) as error:post('/api/similarity/accept',{**body,'reviewed':True})
            error.exception.close()
            post('/api/review/undo',{'participantId':'A','operationId':ledger['id']})
            with app.db() as c:self.assertIsNone(c.execute('SELECT 1 FROM labels WHERE id=?',(accepted['id'],)).fetchone())
            with urllib.request.urlopen(base+'/similarity.js') as r:self.assertIn(b'/api/similarity/accept',r.read())


if __name__=='__main__':unittest.main()
