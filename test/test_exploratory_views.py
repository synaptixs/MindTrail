"""PCA oracle, exact-source verification and real explorer lifecycle/HTTP checks."""
from contextlib import closing
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import urllib.error
import urllib.request

from analysis_manager import AnalysisManager, read_json, remove_quality_payloads
import exploration_access as access

AVAILABLE = importlib.util.find_spec('numpy') and importlib.util.find_spec('sklearn')
if AVAILABLE:
    import numpy as np
    import exploratory_views as views
    from analysis_quality import analyze_archive


@unittest.skipUnless(AVAILABLE, 'Optional numerical environment')
class ExplorerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.database=self.root/'source.sqlite3'
        with closing(sqlite3.connect(self.database)) as c:
            c.execute('CREATE TABLE readings(participant_id TEXT,timestamp_ms INTEGER,device_id TEXT,sample_count INTEGER,waveform_json TEXT)')
            records=[]
            for i in range(64):
                hz=9 if i%2 else 19
                for second in range(4):
                    wave=(1+i*.01)*np.sin(2*np.pi*hz*(np.arange(256)+second*256)/256)+.02*np.sin(np.arange(256))
                    records.append(('P',1793700000000+i*60000+second*1000,'D',256,json.dumps(wave.tolist())))
            c.executemany('INSERT INTO readings VALUES(?,?,?,?,?)',records);c.commit()

    def manager(self):
        manager=AnalysisManager(self.root,self.database,demo=True);self.addCleanup(manager.close);return manager

    def wait(self,manager):
        deadline=time.monotonic()+45
        while manager.running() and time.monotonic()<deadline:time.sleep(.04)
        self.assertFalse(manager.running());state=manager.state('P')
        self.assertEqual(state['job']['state'],'completed',state)
        return state

    def test_pca_matches_covariance_eigenvalues_reconstruction_and_signs(self):
        raw=np.random.default_rng(920).normal(size=(80,5)) @ np.diag([1,2,3,4,5])
        p=views.pca_projection(raw);scale=p['scaling']
        centered=(raw-scale['median'])/scale['iqrScale']-scale['meanAfterScaling']
        eigen=np.linalg.eigvalsh(np.cov(centered,rowvar=False))[::-1]
        np.testing.assert_allclose(p['explainedVarianceRatio'],eigen[:2]/eigen.sum(),rtol=1e-12)
        axes=np.asarray(p['components']);scores=np.asarray(p['coordinates'])
        np.testing.assert_allclose(axes@axes.T,np.eye(2),atol=1e-12)
        np.testing.assert_allclose(scores,centered@axes.T,atol=1e-12)
        for axis in axes:self.assertGreaterEqual(axis[np.argmax(abs(axis))],0)
        self.assertEqual(p,views.pca_projection(raw))
        self.assertEqual(views.pca_projection(np.ones((8,5)))['status'],'constant_features')
        rank_one=views.pca_projection(np.arange(8)[:,None]*np.ones((1,5)))
        self.assertEqual(rank_one['rank'],1)
        self.assertEqual(rank_one['explainedVarianceRatio'][1],0)
        self.assertTrue(all(point[1]==0 for point in rank_one['coordinates']))
        self.assertEqual(len(rank_one['components']),1)
        self.assertTrue(views.pca_projection(np.r_[np.eye(4),-np.eye(4)])['axisAmbiguity'])
        with self.assertRaises(ValueError):views.pca_projection(np.full((8,5),np.nan))

    def test_view_counts_representatives_hashes_and_input_preservation(self):
        report=analyze_archive(self.database,'P');before=deepcopy(report)
        index,payloads=views.build_views(report)
        self.assertEqual(report,before)
        again,other=views.build_views(report)
        self.assertEqual((index,payloads),(again,other))
        for (group,view),payload in payloads.items():
            self.assertIsNone(payload['defaultCandidateId'])
            self.assertEqual(index['groups'][0]['files'][view],views.base.content_digest(payload))
            self.assertEqual(len(payload['points']),64)
            self.assertTrue(all(sum(d['counts'])==64 for d in payload['distributions']))
            ids={p['windowId'] for p in payload['points']}
            for c in payload['candidates']:
                self.assertLessEqual(len(c['representatives']),12)
                for rep in c['representatives']:
                    self.assertIn(rep['windowId'],ids)
                    point=next(i for i,p in enumerate(payload['points']) if p['windowId']==rep['windowId'])
                    self.assertEqual(c['labels'][point],rep['groupNumber'])
        calls=0
        def cancel():
            nonlocal calls
            calls+=1
            if calls==10:raise RuntimeError('cancel probe')
        with self.assertRaisesRegex(RuntimeError,'cancel probe'):views.build_views(report,check=cancel)

    def test_exact_waveform_rejects_changes_gaps_duplicates_and_devices(self):
        report=analyze_archive(self.database,'P');window=report['windows'][0];before=self.database.read_bytes()
        result=access.verified_waveform(self.database,'P',window['acquisition'],window)
        self.assertEqual(len(result['samples']),1024);self.assertTrue(result['sourceVerified'])
        self.assertEqual(self.database.read_bytes(),before)
        mutations=[('UPDATE readings SET waveform_json=? WHERE timestamp_ms=?',('[0]',window['startMs'])),
                   ('DELETE FROM readings WHERE timestamp_ms=?',(window['startMs'],)),
                   ('UPDATE readings SET device_id=? WHERE timestamp_ms=?',('other',window['startMs'])),
                   ('INSERT INTO readings SELECT * FROM readings WHERE timestamp_ms=?',(window['startMs'],))]
        for sql,args in mutations:
            with closing(sqlite3.connect(self.database)) as c:
                c.execute('BEGIN');c.execute(sql,args);c.commit()
            with self.assertRaises(ValueError):access.verified_waveform(self.database,'P',window['acquisition'],window)
            self.database.write_bytes(before)
        with self.assertRaises(ValueError):access.verified_waveform(self.database,'other',window['acquisition'],window)

    def test_real_worker_freshness_corruption_waveform_scope_and_retention(self):
        manager=self.manager();before=hashlib.sha256(self.database.read_bytes()).hexdigest()
        manager.start('P',{'exploration':True});self.wait(manager)
        index=access.read_view(manager,'P');self.assertEqual(index['status'],'current')
        group=index['groups'][0]['acquisitionGroupId'];payload=access.read_view(manager,'P',group)
        self.assertEqual(payload['status'],'current')
        window=payload['points'][0]['windowId']
        wave=access.waveform(manager,'P',group,'with_power',window,payload['explorationIdentity'])
        self.assertTrue(wave['sourceVerified']);self.assertEqual(access.read_view(manager,'other')['status'],'unavailable')
        with self.assertRaises(ValueError):access.waveform(manager,'P',group,'with_power',window,'old')
        with self.assertRaises(ValueError):access.read_view(manager,'P','../bad')
        path=manager.report_path('P').parent/f'exploration-{group}-with_power.json';saved=path.read_bytes()
        altered=json.loads(saved);altered['participantId']='other';path.write_text(json.dumps(altered),encoding='utf-8')
        self.assertEqual(access.read_view(manager,'P',group)['status'],'unavailable');path.write_bytes(saved)
        self.assertEqual(hashlib.sha256(self.database.read_bytes()).hexdigest(),before)
        manager.start('P',{'exploration':True});state=self.wait(manager)
        self.assertEqual(state['job']['reusedWindows'],64)
        self.assertEqual(saved,(manager.report_path('P').parent/path.name).read_bytes())
        for _ in range(2):manager.start('P',{});self.wait(manager)
        self.assertEqual(access.read_view(manager,'P')['status'],'disabled')
        self.assertLessEqual(len(list((manager.root/'jobs').glob('*/exploration-index.json'))),1)
        manager.start('P',{'exploration':True});self.wait(manager)
        with closing(sqlite3.connect(self.database)) as c:c.execute('DELETE FROM readings WHERE timestamp_ms=(SELECT MIN(timestamp_ms) FROM readings)');c.commit()
        self.assertEqual(access.read_view(manager,'P')['status'],'stale')
        with self.assertRaises(ValueError):access.waveform(manager,'P',group,'with_power',window,payload['explorationIdentity'])

    def test_settings_stdlib_import_cleanup_and_packaged_assets(self):
        with self.assertRaises(ValueError):AnalysisManager.settings({'exploration':'yes'})
        manager=self.manager()
        with patch.dict('os.environ',{'AWEAR_BUNDLED_RUNTIME':'1'}):
            self.assertFalse(manager.state('P')['explorationAvailable'])
            with self.assertRaises(ValueError):manager.start('P',{'exploration':True})
        run=subprocess.run([sys.executable,'-S','-c','import sys;sys.path.insert(0,sys.argv[1]);import exploration_access',str(Path(access.__file__).parent)],capture_output=True,text=True)
        self.assertEqual(run.returncode,0,run.stderr)
        import installer_build
        for name in ('explore.html','explore.js','explore.css'):self.assertIn(name,installer_build.STATIC_FILES)
        for name in ('analysis_provenance.py','exploration_access.py'):self.assertIn(name,installer_build.APP_FILES)
        for name in ('exploration-index.json','exploration-'+'a'*64+'-with_power.json','notes.json'):(self.root/name).write_text('{}')
        remove_quality_payloads(self.root)
        self.assertTrue((self.root/'notes.json').exists());self.assertFalse((self.root/'exploration-index.json').exists())

    def test_corrupt_completion_manifest_cannot_publish_or_leave_partial_views(self):
        from analysis_manager import write_json
        manager=self.manager();manager.start('P',{'exploration':True});self.wait(manager)
        previous=access.read_view(manager,'P')['explorationIdentity']
        identifier='a'*32;directory=manager.directory(identifier);directory.mkdir()
        manager.job={'id':identifier,'participant':'P','scope':manager.scope('P'),'signature':manager.signature(),**manager.settings({'exploration':True})}
        write_json(directory/'status.json',{'state':'completed','analysisIdentity':'broken'})
        write_json(directory/'summary.json',{'analysisIdentity':'broken'})
        write_json(directory/'report.json',{})
        write_json(directory/'exploration-index.json',{'analysisIdentity':'broken','participantId':'P','groups':None})
        write_json(manager.root/'active.json',{})
        manager.process=SimpleNamespace(poll=lambda:0)
        manager._watch_loop()
        self.assertEqual(manager.job['state'],'failed')
        self.assertFalse((directory/'exploration-index.json').exists())
        self.assertEqual(access.read_view(manager,'P')['explorationIdentity'],previous)

    def test_http_page_post_guards_current_view_and_exact_waveform(self):
        import app
        old,old_root=app.DB_PATH,app.DATA_ROOT;app.DB_PATH=self.root/'http.sqlite3';app.DATA_ROOT=self.root
        try:
            app.setup_db()
            with app.db() as c:
                app.put_setting(c,'participant_id','P')
                with closing(sqlite3.connect(self.database)) as source:
                    rows=source.execute('SELECT participant_id,timestamp_ms,device_id,sample_count,waveform_json FROM readings').fetchall()
                c.executemany('INSERT INTO readings(participant_id,timestamp_ms,device_id,sample_count,waveform_json,mean,rms,peak_to_peak) VALUES(?,?,?,?,?,0,1,2)',rows)
            server=app.AppServer(('127.0.0.1',0),app.Collector('',demo=True));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                base=f'http://127.0.0.1:{server.server_port}'
                with urllib.request.urlopen(base+'/explore.html') as r:self.assertIn(b'Principal component view',r.read())
                req=urllib.request.Request(base+'/api/analysis/start',data=json.dumps({'exploration':True}).encode(),headers={'Content-Type':'application/json'})
                with self.assertRaises(urllib.error.HTTPError) as failure:urllib.request.urlopen(req)
                self.assertEqual(failure.exception.code,403)
                failure.exception.close()
                req.add_header('X-Awear-Local','1')
                with urllib.request.urlopen(req) as r:self.assertEqual(r.status,202)
                self.wait(server.analysis)
                with urllib.request.urlopen(base+'/api/exploration') as r:index=json.load(r)
                group=index['groups'][0]['acquisitionGroupId']
                with urllib.request.urlopen(base+f'/api/exploration?group={group}&view=with_power') as r:payload=json.load(r)
                window=payload['points'][0]['windowId']
                with urllib.request.urlopen(base+f'/api/exploration/waveform?group={group}&window={window}&identity={index["explorationIdentity"]}') as r:self.assertTrue(json.load(r)['sourceVerified'])
                with self.assertRaises(urllib.error.HTTPError) as failure:urllib.request.urlopen(base+'/api/exploration?group=../x')
                failure.exception.close()
            finally:server.shutdown();thread.join(timeout=5);server.server_close()
        finally:app.DB_PATH=old;app.DATA_ROOT=old_root


if __name__=='__main__':unittest.main()
