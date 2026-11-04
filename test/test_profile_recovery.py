"""Portable complete-profile recovery, corruption/hostile input and atomic rollback."""
from contextlib import closing
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

import app
from analysis_manager import AnalysisManager
import profile_identity as identity
import profile_recovery as recovery
from test import test_desktop as desktop_tests


class RecoveryTests(unittest.TestCase):
    setUp = desktop_tests.DesktopTests.setUp
    tearDown = desktop_tests.DesktopTests.tearDown
    request = desktop_tests.DesktopTests.request
    launch = desktop_tests.DesktopTests.launch

    def source(self):
        source = self.directory/'source'
        source.mkdir()
        with closing(sqlite3.connect(app.DB_PATH)) as c, closing(sqlite3.connect(source/recovery.CORE)) as target:
            c.backup(target)
        with closing(sqlite3.connect(source/recovery.CORE)) as c,c:
            c.execute("INSERT INTO settings VALUES('participant_id','SYNTHETIC') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
            c.execute("INSERT OR REPLACE INTO settings VALUES('desktop_remember_key','1')")
            c.execute("INSERT OR REPLACE INTO settings VALUES('desktop_mode','api')")
            c.execute("INSERT INTO readings VALUES('SYNTHETIC',1000,'fixture','[1,2]',2,1.5,1.6,1)")
        for name,content in [('raw/bluetooth/session-a/records.jsonl',b'{"fixture":true}\n'),('bluetooth/session-legacy/raw.bin',b'\x00\x01'),('raw/cloud/export-a/data.csv',b'fixture\n'),('processed/patterns/history.json',b'{}')]:
            p=source/'data'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(content)
        manager=AnalysisManager(source,source/recovery.CORE)
        for name,module in recovery.STORES.items():
            p=source/'data/processed/discovery'/name;p.parent.mkdir(parents=True,exist_ok=True)
            with closing(sqlite3.connect(p)) as c,c:
                __import__(module).initialize(c)
                if name=='questions.sqlite3':
                    c.execute('INSERT INTO questions VALUES(?,?,?,?,?,?,?)',(manager.scope('NO-READINGS'),'a'*32,1,'Question without records','context','discovery','old'))
                    c.execute('INSERT INTO history VALUES(?,?,?,?,?)',(manager.scope('NO-READINGS'),'a'*32,1,'{}','fixture'))
                elif name=='context.sqlite3':
                    c.execute('INSERT INTO current VALUES(?,?,?,?)',(manager.scope('SYNTHETIC'),'context',1,'{}'))
                    c.execute('INSERT INTO history VALUES(?,?,?,?,?,?,?,?)',(manager.scope('SYNTHETIC'),'context',1,None,'{}','save',None,'fixture'))
                elif name=='decisions.sqlite3':
                    c.execute('INSERT INTO decision VALUES(?,?,?)',(manager.scope('SYNTHETIC'),1,'{}'))
                    c.execute('INSERT INTO decision_history VALUES(?,?,?)',(manager.scope('SYNTHETIC'),1,'{}'))
        (source/'.env').write_text('AWEAR_API_KEY=DO-NOT-EXPORT')
        quality=source/'data/processed/quality/jobs/private';quality.mkdir(parents=True);(quality/'partial.json').write_text('{}')
        return source

    def bundle(self,source):
        with recovery.backup(source,source/recovery.CORE) as (path,manifest):
            return path.read_bytes(),manifest

    def test_complete_rehomed_repeated_restore_preserves_immutable_scopes_and_files(self):
        source=self.source();before=recovery.digest(source/recovery.CORE)
        body,manifest=self.bundle(source)
        self.assertEqual(body,self.bundle(source)[0])
        self.assertEqual(before,recovery.digest(source/recovery.CORE))
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            self.assertFalse(any('quality/' in n or '.env' in n for n in z.namelist()))
            self.assertEqual(set(z.namelist()),{e['path'] for e in manifest['files']}|{'manifest.json'})
        self.launch()
        status,data,*_=self.request('/api/desktop/restore',body)
        self.assertEqual(status,200,data)
        restored=AnalysisManager(self.directory,app.DB_PATH)
        old=AnalysisManager(source,source/recovery.CORE)
        self.assertEqual(old.scope('NO-READINGS'),restored.scope('NO-READINGS'))
        import discovery_questions
        self.assertEqual(discovery_questions.page(restored,'NO-READINGS')['total'],1)
        self.assertFalse(self.controller.ready());self.assertFalse(self.collector.enabled)
        self.assertEqual(self.request('/api/desktop/offline',{})[0],200)
        self.assertTrue(self.controller.ready());self.assertEqual(self.controller.mode(),'offline')
        self.assertEqual(self.request('/api/participants')[0],200)
        for e in manifest['files']:
            if e['path'] not in (recovery.CORE,identity.NAME):
                self.assertEqual(e['sha256'],recovery.digest(self.directory/e['path']))
        self.assertEqual(self.request('/api/desktop/restore',body)[0],400)
        status,second,*_=self.request('/api/desktop/backup');self.assertEqual(status,200)
        with tempfile.TemporaryDirectory() as temp:
            other=Path(temp)
            with closing(sqlite3.connect(app.DB_PATH)) as c,closing(sqlite3.connect(other/recovery.CORE)) as d:c.backup(d)
            with closing(sqlite3.connect(other/recovery.CORE)) as c,c:
                c.execute('DELETE FROM readings');c.execute("UPDATE settings SET value='' WHERE key='desktop_setup'")
            recovery.restore(other,other/recovery.CORE,io.BytesIO(second),lambda:None)
            self.assertEqual(old.scope('NO-READINGS'),AnalysisManager(other,other/recovery.CORE).scope('NO-READINGS'))

    def test_checksum_schema_traversal_and_duplicate_fail_before_mutation(self):
        body,_=self.bundle(self.source());before=recovery.digest(app.DB_PATH)
        self.launch()
        def mutate(kind):
            with zipfile.ZipFile(io.BytesIO(body)) as z:
                entries={n:z.read(n) for n in z.namelist()}
            if kind=='checksum':entries['data/raw/cloud/export-a/data.csv']=b'corrupt'
            if kind=='traversal':entries['../outside']=b'no'
            if kind=='schema':
                with tempfile.TemporaryDirectory() as t:
                    p=Path(t)/'bad';p.write_bytes(entries[recovery.CORE])
                    with closing(sqlite3.connect(p)) as c,c:c.execute('CREATE VIEW hostile AS SELECT 1')
                    entries[recovery.CORE]=p.read_bytes()
                m=json.loads(entries['manifest.json'])
                for e in m['files']:
                    if e['path']==recovery.CORE:e.update(bytes=len(entries[recovery.CORE]),sha256=hashlib.sha256(entries[recovery.CORE]).hexdigest())
                m['totalBytes']=sum(e['bytes'] for e in m['files']);entries['manifest.json']=json.dumps(m).encode()
            out=io.BytesIO()
            with zipfile.ZipFile(out,'w') as z:
                for n,b in entries.items():z.writestr(n,b)
                if kind=='duplicate':
                    import warnings
                    with warnings.catch_warnings():warnings.simplefilter('ignore');z.writestr(recovery.CORE,entries[recovery.CORE])
            return out.getvalue()
        for kind in ('checksum','traversal','schema','duplicate'):
            with self.subTest(kind=kind):
                status,data,*_=self.request('/api/desktop/restore',mutate(kind));self.assertEqual(status,400,data)
                self.assertEqual(before,recovery.digest(app.DB_PATH));self.assertFalse((self.directory/'data').exists())

    def test_migration_failure_rolls_back_core_and_generated_data(self):
        body,_=self.bundle(self.source());before=recovery.digest(app.DB_PATH)
        p=self.directory/'data/processed/quality/sentinel';p.parent.mkdir(parents=True);p.write_text('old')
        with self.assertRaisesRegex(RuntimeError,'migration'):
            recovery.restore(self.directory,app.DB_PATH,io.BytesIO(body),lambda:(_ for _ in ()).throw(RuntimeError('migration')))
        self.assertEqual(before,recovery.digest(app.DB_PATH));self.assertEqual(p.read_text(),'old')
        self.assertFalse((self.directory/identity.NAME).exists())

    def test_auth_busy_separate_history_and_external_writer_guards(self):
        self.assertEqual(self.request('/api/desktop/backup')[0],403)
        self.launch()
        self.collector.running=True
        self.assertEqual(self.request('/api/desktop/backup')[0],400)
        self.collector.running=False
        with patch.object(recovery,'backup',side_effect=OSError('storage')):
            status,message,*_=self.request('/api/desktop/backup');self.assertEqual(status,400);self.assertIn(b'Backup could not finish',message)
        source=self.source();body,_=self.bundle(source)
        with patch.object(recovery,'restore',side_effect=OSError('storage')):
            status,message,*_=self.request('/api/desktop/restore',body);self.assertEqual(status,400);self.assertIn(b'Keep your original backup',message)
        p=self.directory/'data/processed/discovery/questions.sqlite3';p.parent.mkdir(parents=True)
        with closing(sqlite3.connect(p)) as c:__import__('discovery_questions').initialize(c)
        self.assertEqual(self.request('/api/desktop/restore',body)[0],400)
        self.assertEqual(self.request('/api/desktop/import',(source/recovery.CORE).read_bytes())[0],400)
        source_file=source/'data/raw/cloud/export-a/data.csv'
        original=recovery.snapshot
        def change(*args):
            original(*args);source_file.write_text('writer changed')
        with patch.object(recovery,'snapshot',side_effect=change), self.assertRaisesRegex(ValueError,'changed during backup'):
            with recovery.backup(source,source/recovery.CORE):pass

    def test_partial_upload_compression_and_promotion_failure_preserve_destination(self):
        body,_=self.bundle(self.source());before=recovery.digest(app.DB_PATH)
        for candidate in (body[:-100],b'not a zip'):
            with self.assertRaises(ValueError):
                recovery.restore(self.directory,app.DB_PATH,io.BytesIO(candidate),lambda:None)
            self.assertEqual(before,recovery.digest(app.DB_PATH))
        out=io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(body)) as source,zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED) as z:
            for name in source.namelist():z.writestr(name,source.read(name))
        with self.assertRaisesRegex(ValueError,'Unsupported'):
            recovery.restore(self.directory,app.DB_PATH,io.BytesIO(out.getvalue()),lambda:None)
        original=Path.replace
        def fail(source,destination):
            if source.name=='data' and source.parent.name=='stage':raise OSError('simulated storage failure')
            return original(source,destination)
        with patch.object(Path,'replace',fail),self.assertRaisesRegex(OSError,'storage failure'):
            recovery.restore(self.directory,app.DB_PATH,io.BytesIO(body),lambda:None)
        self.assertEqual(before,recovery.digest(app.DB_PATH));self.assertFalse((self.directory/identity.NAME).exists())

    def test_sqlite_wal_contents_and_destination_change_are_not_lost(self):
        source=self.source()
        with closing(sqlite3.connect(source/recovery.CORE)) as c:
            c.execute('PRAGMA journal_mode=WAL')
            c.execute("INSERT INTO readings VALUES('SYNTHETIC',2000,'fixture','[1,2]',2,1.5,1.6,1)");c.commit()
            body,_=self.bundle(source)
        with zipfile.ZipFile(io.BytesIO(body)) as z,tempfile.TemporaryDirectory() as t:
            p=Path(t)/'snapshot';p.write_bytes(z.read(recovery.CORE))
            with closing(sqlite3.connect(p)) as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM readings').fetchone()[0],2)
        extract=recovery.extract
        def writer(*args):
            result=extract(*args)
            with app.db() as c:app.put_setting(c,'external_writer','changed')
            return result
        with patch.object(recovery,'extract',side_effect=writer),self.assertRaisesRegex(ValueError,'Destination changed'):
            recovery.restore(self.directory,app.DB_PATH,io.BytesIO(body),lambda:None)
        with app.db() as c:self.assertEqual(app.setting(c,'external_writer'),'changed')
        self.assertFalse((self.directory/identity.NAME).exists())
        for names in [('data/A/file','data/a/other'),('data/a','data/A')]:
            with self.assertRaisesRegex(ValueError,'collide'):recovery.portable_paths(names)

    def test_limits_symlinks_origin_and_gate_concurrency(self):
        source=self.source()
        with patch.object(recovery,'MAX_BYTES',1),self.assertRaisesRegex(ValueError,'20 GB'):
            with recovery.backup(source,source/recovery.CORE):pass
        (source/'data/link').symlink_to(self.directory)
        with self.assertRaisesRegex(ValueError,'symbolic link'):
            with recovery.backup(source,source/recovery.CORE):pass
        (source/'data/link').unlink()
        (source/identity.NAME).write_text('{"version":2}')
        with self.assertRaisesRegex(ValueError,'origin'):
            AnalysisManager(source,source/recovery.CORE).scope('x')
        gate=recovery.WorkspaceGate()
        with gate.lease():
            with gate.lease():self.assertEqual(gate.readers,2)
            with self.assertRaisesRegex(ValueError,'still active'):
                with gate.lease(exclusive=True,timeout=.01):pass
        with gate.lease(exclusive=True):
            with self.assertRaisesRegex(ValueError,'in progress'):
                with gate.lease():pass
        with gate.lease():pass
