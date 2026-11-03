"""Personal vocabulary, migration and history contracts on synthetic profiles."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import app
import personal_activities as activities
import analysis_provenance
from archive_paths import ArchivePaths


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        self.storage=app.DATA_ROOT,app.DB_PATH
        self.addCleanup(self.restore_storage)
        app.configure_storage(self.root);app.setup_db()

    def restore_storage(self):app.DATA_ROOT,app.DB_PATH=self.storage

    def create(self,participant='A',name='Reading α',**values):
        with app.db() as connection:return activities.create(connection,participant,{'name':name,**values})

    def update(self,participant,activity,**values):
        with app.db() as connection:
            connection.execute('BEGIN IMMEDIATE')
            return activities.update(connection,participant,activity['id'],{'version':activity['version'],**values})

    def get(self,participant,activity_id):
        with app.db() as c:return activities.get(c,participant,activity_id)

    def label(self,label_id):
        with app.db() as c:return dict(c.execute('SELECT * FROM labels WHERE id=?',(label_id,)).fetchone())

    def export(self,participant):
        with app.db() as c:
            c.execute('BEGIN')
            return json.loads(b''.join(activities.export_json(c,participant)))

    def legacy(self):
        profile=self.root/'legacy';profile.mkdir();database=profile/'awear_data.sqlite3'
        with closing(sqlite3.connect(database)) as c:
            c.executescript('''CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                INSERT INTO settings VALUES('participant_id','A');
                CREATE TABLE readings(participant_id TEXT,timestamp_ms INTEGER,device_id TEXT,waveform_json TEXT,
                    sample_count INTEGER,mean REAL,rms REAL,peak_to_peak REAL,PRIMARY KEY(participant_id,timestamp_ms));
                CREATE TABLE labels(id INTEGER PRIMARY KEY AUTOINCREMENT,participant_id TEXT NOT NULL,label TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',start_ms INTEGER NOT NULL,end_ms INTEGER,source TEXT NOT NULL DEFAULT 'manual',
                    reviewed INTEGER NOT NULL DEFAULT 1,batch_id TEXT);
                CREATE TABLE mobile_scores(id INTEGER PRIMARY KEY);''')
            c.executemany('INSERT INTO labels(participant_id,label,note,start_ms,end_ms,source,reviewed,batch_id) VALUES(?,?,?,?,?,?,?,?)',
                [('A','Reading α','Keep note',1000,2000,'manual',1,None),
                 ('A','Reading α','Suggested',3000,4000,'schedule',0,'original-batch'),
                 ('B','Reading α','Other person',5000,6000,'manual',1,None)])
            c.executemany('INSERT INTO readings VALUES(?,?,?,?,?,?,?,?)',[('A',i*1000,'D',json.dumps([i]*256),256,0,1,2) for i in range(8)])
            c.commit()
        return profile,database

    def test_new_people_start_empty_and_create_while_labeling(self):
        with app.db() as c:
            self.assertEqual(activities.page(c,'A')['activities'],[])
            self.assertEqual(activities.page(c,'B')['activities'],[])
        label_id=app.add_label('A','My own activity','',1000,2000)
        row=self.label(label_id)
        self.assertTrue(row['activity_id'])
        self.assertEqual(row['recorded_label'],'My own activity')
        with app.db() as c:
            self.assertEqual(activities.page(c,'B')['total'],0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM labels WHERE source='schedule'").fetchone()[0],0)

    def test_same_name_is_independent_and_cross_participant_ids_are_rejected(self):
        a=self.create('A');b=self.create('B')
        self.assertNotEqual(a['id'],b['id'])
        self.update('A',a,favorite=True,category='Study',context='Own context')
        self.assertEqual(self.get('B',b['id'])['favorite'],0)
        with self.assertRaises(ValueError):self.get('B',a['id'])
        with self.assertRaises(ValueError):app.add_label('B','Reading α','',1000,2000,activity_id=a['id'])
        with app.db() as c:
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute('INSERT INTO labels(participant_id,label,start_ms,end_ms,activity_id) VALUES(?,?,?,?,?)',('B','Reading α',1000,2000,a['id']))
        self.assertEqual(self.export('B')['activities'],[b])

    def test_rename_preserves_identity_original_names_times_and_export_history(self):
        label_id=app.add_label('A','Reading α','Exact note',1234,5678)
        before=self.label(label_id);activity=self.get('A',before['activity_id'])
        renamed=self.update('A',activity,name='Ontology review 🧠',category='Work',context='Technical docs',favorite=True)
        after=self.label(label_id)
        self.assertEqual(after['activity_id'],before['activity_id'])
        self.assertEqual(after['label'],'Ontology review 🧠')
        self.assertEqual(after['recorded_label'],'Reading α')
        for key in ('id','participant_id','note','start_ms','end_ms','source','reviewed','batch_id'):
            self.assertEqual(after[key],before[key])
        export=self.export('A')
        self.assertEqual([h['name'] for h in export['activityHistory']],['Reading α','Ontology review 🧠'])
        self.assertEqual(export['assignments'][0]['activity_id'],renamed['id'])
        self.assertTrue(any(h['label']=='Reading α' for h in export['assignmentHistory']))
        self.assertTrue(any(h['label']=='Ontology review 🧠' for h in export['assignmentHistory']))
        self.assertEqual(app.summary('A',0,10000)['labels'][0]['label'],'Ontology review 🧠')

    def test_archive_keeps_history_blocks_new_assignments_and_allows_existing_edits(self):
        label_id=app.add_label('A','Read','',1000,2000)
        activity=self.get('A',self.label(label_id)['activity_id'])
        archived=self.update('A',activity,archived=True)
        with self.assertRaisesRegex(ValueError,'archived'):app.add_label('A','Read','',3000,4000,activity_id=archived['id'])
        app.add_label('A','Read','Edited note',1000,2100,label_id,activity_id=archived['id'])
        self.assertEqual(self.label(label_id)['note'],'Edited note')
        with app.db() as c:
            self.assertEqual(activities.page(c,'A',include_archived=False)['total'],0)
            self.assertEqual(activities.page(c,'A')['total'],1)
        restored=self.update('A',archived,archived=False)
        second=app.add_label('A','Read','',3000,4000,activity_id=restored['id'])
        self.assertEqual(self.label(second)['activity_id'],restored['id'])

    def test_collision_and_stale_version_never_merge_or_overwrite(self):
        a=self.create(name='First');b=self.create(name='Second')
        with self.assertRaises(ValueError):self.update('A',a,name=b['name'])
        changed=self.update('A',a,category='Personal')
        with self.assertRaisesRegex(ValueError,'changed'):self.update('A',a,name='Stale name')
        self.assertEqual(self.get('A',a['id']),changed)
        self.assertEqual(len(self.export('A')['activities']),2)

    def test_legacy_sql_writers_get_stable_ids_and_projection_stays_owned(self):
        with app.db() as c:
            label_id=c.execute("INSERT INTO labels(participant_id,label,start_ms,end_ms) VALUES('A','Legacy',1000,2000)").lastrowid
        first=self.label(label_id)
        self.assertTrue(first['activity_id'])
        self.assertEqual(first['recorded_label'],'Legacy')
        with app.db() as c:c.execute("UPDATE labels SET label='New interval activity' WHERE id=?",(label_id,))
        changed=self.label(label_id)
        self.assertNotEqual(changed['activity_id'],first['activity_id'])
        self.assertEqual(changed['recorded_label'],'New interval activity')
        other=self.create(name='Explicit ID')
        with app.db() as c:c.execute('UPDATE labels SET activity_id=? WHERE id=?',(other['id'],label_id))
        self.assertEqual(self.label(label_id)['label'],'Explicit ID')
        for query in ('UPDATE labels SET activity_id=NULL WHERE id=?',"UPDATE labels SET participant_id='B' WHERE id=?"):
            with app.db() as c:
                with self.assertRaises(sqlite3.IntegrityError):c.execute(query,(label_id,))

    def test_history_and_definition_deletion_are_protected(self):
        label_id=app.add_label('A','Personal','',1000,2000)
        with app.db() as c:c.execute('DELETE FROM labels WHERE id=?',(label_id,))
        self.assertEqual(self.export('A')['assignments'],[])
        self.assertEqual(self.export('A')['assignmentHistory'][-1]['action'],'deleted')
        for table in ('activities','activity_history','activity_label_history'):
            with app.db() as c:
                with self.assertRaises(sqlite3.IntegrityError):c.execute('DELETE FROM '+table)

    def test_pagination_favorites_and_complete_export_are_participant_scoped(self):
        for i in range(55):self.create(name=f'Own {i:02d}')
        favorite=self.create(name='Favorite',favorite=True)
        self.create('B','PRIVATE OTHER PERSON')
        with app.db() as c:
            first=activities.page(c,'A',limit=50);second=activities.page(c,'A',limit=50,offset=50)
        self.assertEqual(first['total'],56);self.assertTrue(first['hasMore']);self.assertEqual(len(second['activities']),6)
        self.assertEqual(first['activities'][0]['id'],favorite['id'])
        self.assertNotIn('PRIVATE OTHER PERSON',json.dumps(self.export('A')))
        current=favorite
        for i in range(55):current=self.update('A',current,context=f'Change {i}')
        with app.db() as c:
            history=activities.history(c,'A',favorite['id']);self.assertEqual(history['total'],56);self.assertTrue(history['hasMore'])
        self.assertEqual(len([r for r in self.export('A')['activityHistory'] if r['activity_id']==favorite['id']]),56)

    def test_migration_is_idempotent_backed_up_and_preserves_all_original_fields(self):
        profile,database=self.legacy()
        with closing(sqlite3.connect(database)) as c:
            c.row_factory=sqlite3.Row
            labels=[dict(r) for r in c.execute('SELECT * FROM labels ORDER BY id')]
            waveforms=list(c.execute('SELECT waveform_json FROM readings ORDER BY timestamp_ms'))
        revision=analysis_provenance.inspect_archive(database,'A')['sourceRevision']
        app.configure_storage(profile);app.setup_db()
        with app.db() as c:
            migrated=[dict(r) for r in c.execute('SELECT * FROM labels ORDER BY id')]
            self.assertEqual(activities.page(c,'A')['total'],1);self.assertEqual(activities.page(c,'B')['total'],1)
        for old,new in zip(labels,migrated):self.assertEqual(old,{k:new[k] for k in old})
        self.assertEqual(migrated[0]['activity_id'],migrated[1]['activity_id'])
        self.assertNotEqual(migrated[0]['activity_id'],migrated[2]['activity_id'])
        self.assertEqual(analysis_provenance.inspect_archive(database,'A')['sourceRevision'],revision)
        backups=list(ArchivePaths(profile).activity_migrations.glob('*.sqlite3'));self.assertEqual(len(backups),1)
        with closing(sqlite3.connect(backups[0])) as c:
            c.row_factory=sqlite3.Row
            self.assertEqual([dict(r) for r in c.execute('SELECT * FROM labels ORDER BY id')],labels)
            self.assertNotIn('activities',{r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")})
        app.setup_db()
        self.assertEqual(list(ArchivePaths(profile).activity_migrations.glob('*.sqlite3')),backups)
        self.assertEqual([r['activity_id'] for r in self.export('A')['assignments']],[migrated[0]['activity_id'],migrated[1]['activity_id']])

    def test_failed_migration_rolls_back_and_retry_preserves_original_backup(self):
        profile,database=self.legacy();app.configure_storage(profile)
        with patch.object(activities,'trigger_sql',side_effect=RuntimeError('Synthetic migration failure')):
            with self.assertRaises(RuntimeError):app.setup_db()
        backups=list(ArchivePaths(profile).activity_migrations.glob('*.sqlite3'));self.assertEqual(len(backups),1)
        with closing(sqlite3.connect(database)) as c:
            self.assertNotIn('activity_id',{r[1] for r in c.execute('PRAGMA table_info(labels)')})
            self.assertEqual(c.execute('SELECT COUNT(*) FROM labels').fetchone()[0],3)
        app.setup_db()
        self.assertTrue(backups[0].exists())
        self.assertEqual(len(self.export('A')['assignments']),2)

    def test_revision_follows_own_definition_and_assignment_changes_only(self):
        def revision(participant):
            with app.db() as c:return activities.revision(c,participant)
        initial=revision('A')
        other=self.create('B','Other')
        self.assertEqual(revision('A'),initial)
        own=self.create('A','Own')
        created=revision('A');self.assertNotEqual(created,initial)
        self.update('B',other,context='Independent')
        self.assertEqual(revision('A'),created)
        self.update('A',own,favorite=True)
        edited=revision('A');self.assertNotEqual(edited,created)
        label_id=app.add_label('A','Own','',1000,2000)
        assigned=revision('A');self.assertNotEqual(assigned,edited)
        with app.db() as c:c.execute('DELETE FROM labels WHERE id=?',(label_id,))
        self.assertNotEqual(revision('A'),assigned)
        with closing(sqlite3.connect(':memory:')) as c:
            self.assertIsNone(activities.revision(c,'A'))

    def test_v1_upgrade_preserves_rows_history_identity_and_is_backed_up(self):
        profile=self.root/'version-one';profile.mkdir();database=profile/'awear_data.sqlite3'
        with closing(sqlite3.connect(database)) as c:
            c.executescript((Path(__file__).parent/'fixtures'/'activities-v1.sql').read_text(encoding='utf-8'))
            before=[dict(zip([d[0] for d in c.execute('SELECT * FROM labels').description],r)) for r in c.execute('SELECT * FROM labels ORDER BY id')]
            names=list(c.execute('SELECT activity_id,version,name FROM activity_history ORDER BY activity_id,version'))
        app.configure_storage(profile);app.setup_db()
        with app.db() as c:
            self.assertEqual(c.execute('SELECT version FROM activity_schema').fetchone()[0],2)
            for old,new in zip(before,c.execute('SELECT * FROM labels ORDER BY id')):
                self.assertEqual(old,{k:new[k] for k in old})
                self.assertEqual(new['timing_precision'],'unknown')
                self.assertIsNone(new['start_uncertainty_ms'])
                self.assertEqual(new['review_state'],'confirmed' if old['reviewed'] else 'needs_review')
            self.assertEqual([tuple(r) for r in c.execute('SELECT activity_id,version,name FROM activity_history ORDER BY activity_id,version')],names)
        backups=list(ArchivePaths(profile).activity_migrations.glob('*.sqlite3'));self.assertEqual(len(backups),1)
        with closing(sqlite3.connect(backups[0])) as c:self.assertEqual(c.execute('SELECT version FROM activity_schema').fetchone()[0],1)
        app.setup_db();self.assertEqual(list(ArchivePaths(profile).activity_migrations.glob('*.sqlite3')),backups)

    def test_v1_arriving_during_preflight_is_upgraded_before_setup_returns(self):
        profile=self.root/'concurrent-one';app.configure_storage(profile)
        fixture=self.root/'concurrent-source.sqlite3'
        with closing(sqlite3.connect(fixture)) as c:c.executescript((Path(__file__).parent/'fixtures'/'activities-v1.sql').read_text(encoding='utf-8'))
        original_backup=activities._backup
        first=True
        def another_launcher(connection,database,root):
            nonlocal first
            if first:
                first=False;connection.commit()
                with closing(sqlite3.connect(fixture)) as source:source.backup(connection)
                return None
            return original_backup(connection,database,root)
        with patch.object(activities,'_backup',side_effect=another_launcher):app.setup_db()
        with app.db() as c:
            activities.validate_archive_objects(c)
            self.assertEqual(c.execute('SELECT version FROM activity_schema').fetchone()[0],2)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM labels WHERE timing_precision='unknown'").fetchone()[0],2)

    def test_v1_failed_upgrade_rolls_back_and_keeps_usable_v1_backup(self):
        profile=self.root/'failure-one';profile.mkdir();database=profile/'awear_data.sqlite3'
        with closing(sqlite3.connect(database)) as c:c.executescript((Path(__file__).parent/'fixtures'/'activities-v1.sql').read_text(encoding='utf-8'))
        app.configure_storage(profile)
        with patch.object(activities,'trigger_sql',side_effect=RuntimeError('Synthetic upgrade failure')):
            with self.assertRaises(RuntimeError):app.setup_db()
        with closing(sqlite3.connect(database)) as c:
            activities.validate_archive_objects(c)
            self.assertEqual(c.execute('SELECT version FROM activity_schema').fetchone()[0],1)
            self.assertNotIn('timing_precision',{r[1] for r in c.execute('PRAGMA table_info(labels)')})
        app.setup_db()

    def test_review_origin_timing_and_history_are_independent(self):
        with app.db() as c:
            label_id=c.execute("INSERT INTO labels(participant_id,label,start_ms,end_ms,source,reviewed,batch_id) VALUES('A','Suggested',1000,9000,'model',0,'model-run-1')").lastrowid
        row=self.label(label_id);self.assertEqual(row['review_state'],'needs_review')
        self.assertEqual(app.label_review('A','suggested')['total'],1)
        app.add_label('A','Suggested','Human review',1000,9000,label_id,evidence={'reviewState':'confirmed','timingPrecision':'approximate','startUncertaintyMs':5000,'endUncertaintyMs':None})
        row=self.label(label_id)
        self.assertEqual((row['source'],row['batch_id'],row['review_state'],row['reviewed']),('model','model-run-1','confirmed',1))
        self.assertEqual(row['timing_precision'],'approximate');self.assertEqual(row['start_uncertainty_ms'],5000)
        self.assertEqual(app.label_review('A','suggested')['total'],0)
        app.add_label('A','Suggested','Unsure',1000,9000,label_id,evidence={'reviewState':'uncertain'})
        row=self.label(label_id);self.assertEqual(row['reviewed'],0);self.assertEqual(row['start_uncertainty_ms'],5000)
        history=self.export('A')['assignmentHistory'];self.assertTrue(any(r['review_state']=='confirmed' and r['start_uncertainty_ms']==5000 for r in history))
        with app.db() as c:
            with self.assertRaises(sqlite3.IntegrityError):c.execute("UPDATE labels SET source='manual' WHERE id=?",(label_id,))
        with self.assertRaisesRegex(ValueError,'overlaps'):app.add_label('A','Other','',2000,4000)

    def test_timing_validation_boundary_edits_and_active_end(self):
        invalid=[{'timingPrecision':'approximate'}, {'timingPrecision':'exact'}, {'timingPrecision':'unknown','startUncertaintyMs':1},
                 {'timingPrecision':'approximate','startUncertaintyMs':True},{'reviewState':'invented'}, {'source':'model'},
                 {'timingPrecision':'approximate','startUncertaintyMs':86400001},{'timingPrecision':'approximate','startUncertaintyMs':1.5}]
        for body in invalid:
            with self.subTest(body=body),self.assertRaises(ValueError):app.add_label('A','New','',1000,2000,evidence=body)
        self.assertEqual(self.export('A')['activities'],[])
        label_id=app.add_label('A','Known','',1000,2000,evidence={'timingPrecision':'exact','startUncertaintyMs':0,'endUncertaintyMs':0})
        app.add_label('A','Known','Note only',1000,2000,label_id)
        self.assertEqual(self.label(label_id)['timing_precision'],'exact')
        app.add_label('A','Known','Boundary changed',1000,2500,label_id)
        self.assertEqual(self.label(label_id)['timing_precision'],'unknown')
        with self.assertRaises(ValueError):app.add_label('A','Live','',3000,None,evidence={'timingPrecision':'approximate','startUncertaintyMs':1000,'endUncertaintyMs':1000})
        app.add_label('A','Live','',3000,None,evidence={'timingPrecision':'exact','startUncertaintyMs':0})

    def test_legacy_review_projection_and_quality_stays_separate(self):
        label_id=app.add_label('A','Personal','',1000,2000,evidence={'reviewState':'uncertain'})
        with app.db() as c:
            c.execute('UPDATE labels SET reviewed=1 WHERE id=?',(label_id,))
        self.assertEqual(self.label(label_id)['review_state'],'confirmed')
        with app.db() as c:c.execute("UPDATE labels SET review_state='needs_review' WHERE id=?",(label_id,))
        self.assertEqual(self.label(label_id)['reviewed'],0)
        with app.db() as c:
            for query in ("UPDATE labels SET timing_precision='exact' WHERE id=?", "UPDATE labels SET start_uncertainty_ms=10 WHERE id=?"):
                with self.assertRaises(sqlite3.IntegrityError):c.execute(query,(label_id,))
        self.assertNotIn('quality',json.dumps(self.export('A')['assignments'][0]))

    def test_summary_separates_confirmed_and_unsure_periods_with_same_activity(self):
        app.add_label('A','Read','',1000,2000)
        app.add_label('A','Read','',2000,3000,evidence={'reviewState':'uncertain'})
        with app.db() as c:
            c.executemany("INSERT INTO readings(participant_id,timestamp_ms,waveform_json,sample_count,mean,rms,peak_to_peak) VALUES('A',?,'[-1,1]',2,0,1,2)",[(1000,),(2000,),(3000,)])
        groups=app.summary('A',0,4000)['activities']
        self.assertEqual(sum(r['records'] for r in groups),3)
        self.assertEqual({r['reviewState'] for r in groups if not r['unassigned']},{'confirmed','uncertain'})
        self.assertEqual(sum(r['records'] for r in groups if r['unassigned']),1)

    def test_model_suggestion_fragments_preserve_origin_batch_and_pending_state(self):
        with app.db() as c:
            c.execute("INSERT INTO labels(participant_id,label,start_ms,end_ms,source,reviewed,batch_id,timing_precision,start_uncertainty_ms,end_uncertainty_ms) VALUES('A','Proposed',1000,9000,'model',0,'run-one','approximate',1000,1000)")
        app.add_label('A','Human activity','',3000,5000)
        with app.db() as c:rows=c.execute("SELECT * FROM labels WHERE source='model' ORDER BY start_ms").fetchall()
        self.assertEqual([(r['start_ms'],r['end_ms']) for r in rows],[(1000,3000),(5000,9000)])
        self.assertTrue(all(r['review_state']=='needs_review' and r['batch_id']=='run-one' and r['timing_precision']=='unknown' for r in rows))
        self.assertEqual(rows[0]['activity_id'],rows[1]['activity_id'])

    def test_restore_schema_allows_only_exact_owned_triggers(self):
        with app.db() as c:
            activities.validate_archive_objects(c)
            c.execute('CREATE TRIGGER custom_trigger AFTER INSERT ON labels BEGIN SELECT 1; END')
            with self.assertRaises(ValueError):activities.validate_archive_objects(c)
