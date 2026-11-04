"""Guided readiness uses actual scoped analysis, never a raw-record threshold."""
import sqlite3
import unittest
from workspace_journey import snapshot


class WorkspaceJourneyTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE readings(participant_id TEXT,timestamp_ms INTEGER)')
        self.db.executemany('INSERT INTO readings VALUES (?,?)', [('P',1000),('P',9000),('OTHER',4000)])
        self.analysis = {'available':True,'participantId':'P','summary':None,'stale':False,'job':None}
        self.discovery = {'status':'unavailable','participantId':'P'}

    def state(self, participant='P'):
        return snapshot(self.db, participant, self.analysis, self.discovery)

    def test_no_source_waiting_and_inventory_are_scoped_read_only(self):
        before = list(self.db.iterdump())
        self.assertEqual(self.state('')['stage'], 'connect')
        self.assertEqual(self.state('NEW')['stage'], 'waiting')
        value = self.state()
        self.assertEqual(value['recordings'], {'count':2,'firstMs':1000,'lastMs':9000})
        self.assertEqual(value['stage'], 'prepare')
        self.assertFalse(value['canExplore'])  # two gapped records imply no readiness
        self.assertTrue(value['canPrepare'])
        self.assertEqual(before, list(self.db.iterdump()))

    def test_only_current_matching_canonical_readiness_exposes_choices(self):
        self.analysis['summary'] = {'analysisIdentity':'A'}
        self.discovery.update(status='current',analysisIdentity='A',readiness='ready_for_exploration')
        self.assertTrue(self.state()['canExplore'])
        self.assertFalse(self.state()['canCompareDays'])
        self.discovery['contexts'] = [{'dates':[{'date':'2026-11-01'},{'date':'2026-11-02'}]}]
        self.assertTrue(self.state()['canCompareDays'])
        for owner,key,value in [(self.analysis,'stale',True),(self.analysis,'participantId','OTHER'),
                                (self.discovery,'participantId','OTHER'),(self.discovery,'analysisIdentity','OLD'),
                                (self.discovery,'status','stale')]:
            original = owner.get(key);owner[key] = value
            self.assertFalse(self.state()['canExplore'], (key,value))
            owner[key] = original
        self.analysis['available'] = False
        self.assertFalse(self.state()['canExplore'])
        self.analysis['available'] = True
        self.discovery['readiness'] = 'no_unflagged_windows'
        self.assertEqual(self.state()['stage'], 'review')
        self.assertFalse(self.state()['canExplore'])

    def test_busy_failure_unavailable_and_new_arrivals(self):
        self.analysis['job'] = {'state':'running','stage':'extracting'}
        self.assertEqual(self.state()['stage'], 'preparing')
        self.assertFalse(self.state()['canPrepare'])
        for state in ['canceling','failed','interrupted','canceled']:
            self.analysis['job']['state'] = state
            self.assertEqual(self.state()['stage'], 'preparing' if state=='canceling' else 'retry')
        self.analysis['available'] = False
        self.assertEqual(self.state()['stage'], 'unavailable')
        self.analysis['available'] = True
        self.db.execute('INSERT INTO readings VALUES (?,?)',('NEW',23000))
        self.assertEqual(self.state('NEW')['stage'], 'prepare')
        self.assertFalse(self.state('NEW')['canExplore'])


class JourneyHttpTests(unittest.TestCase):
    def test_assets_and_workspace_are_served_through_the_real_local_router(self):
        import app
        import http.client
        import tempfile
        import threading
        from pathlib import Path
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary, patch.object(app.Handler, 'log_message'):
            original = app.DATA_ROOT, app.DB_PATH
            app.configure_storage(Path(temporary))
            app.setup_db()
            server = app.AppServer(('127.0.0.1',0),app.Collector(''))
            thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                for path, mime in [('/journey.html','text/html'),('/journey.js','text/javascript'),
                                   ('/navigation.js','text/javascript'),('/api/workspace','application/json')]:
                    c = http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
                    c.request('GET',path);r=c.getresponse();body=r.read()
                    self.assertEqual(r.status,200,(path,body))
                    self.assertIn(mime,r.getheader('Content-Type'));c.close()
                import json
                self.assertEqual(json.loads(body)['stage'],'connect')
                with app.db() as connection:
                    app.put_setting(connection,'participant_id','EMPTY')
                c = http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
                c.request('GET','/api/workspace');r=c.getresponse()
                self.assertEqual(json.loads(r.read())['stage'],'waiting');c.close()
                with patch.object(server.analysis,'start') as start:
                    c = http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
                    c.request('POST','/api/analysis/start',json.dumps({'participantId':'PREVIOUS'}),
                              {'Content-Type':'application/json','X-Awear-Local':'1'})
                    r=c.getresponse();self.assertEqual(r.status,400);r.read();c.close()
                    start.assert_not_called()
            finally:
                server.shutdown();server.server_close();thread.join()
                app.DATA_ROOT, app.DB_PATH = original
