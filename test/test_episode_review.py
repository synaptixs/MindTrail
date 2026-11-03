"""Independent episode mutation, coverage, provenance and restore acceptance fixtures."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import app
import episode_review as review
import personal_activities
from archive_paths import ArchivePaths


class EpisodeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.old=app.DATA_ROOT,app.DB_PATH
        self.addCleanup(self.restore);app.configure_storage(self.root);app.setup_db()
    def restore(self):app.DATA_ROOT,app.DB_PATH=self.old
    def labels(self,person='A'):
        with app.db() as c:return [dict(r) for r in c.execute('SELECT * FROM labels WHERE participant_id=? ORDER BY id',(person,))]
    def version(self,label_id,person='A'):
        with app.db() as c:return review.label_version(c,person,label_id)
    def mutate(self,function,*args,person='A',**kwargs):
        with app.db() as c:
            c.execute('BEGIN IMMEDIATE');return function(c,person,*args,**kwargs)
    def queue(self,person='A',**kwargs):
        with app.db() as c:return review.queue(c,person,**kwargs)
    def records(self,stamps,person='A'):
        with app.db() as c:c.executemany("INSERT INTO readings VALUES(?,?,'D','[-1,1]',2,0,1,2)",[(person,t) for t in stamps])
    def last(self):
        with app.db() as c:return c.execute('SELECT MAX(id) FROM episode_operations').fetchone()[0]

    def test_split_undo_and_reverse_undo_retain_identity_origin_history_and_other_participant(self):
        label=app.add_label('A','Reading','Original note',1000,10000,evidence={'timingPrecision':'exact','startUncertaintyMs':0,'endUncertaintyMs':0})
        app.add_label('B','Reading','Other person',1000,10000)
        before=self.labels();other=self.labels('B')
        result=self.mutate(review.split,label,5123,self.version(label))
        split=self.labels();self.assertEqual([r['id'] for r in split],[label,result['rightId']])
        self.assertEqual([(r['start_ms'],r['end_ms']) for r in split],[(1000,5123),(5123,10000)])
        for r in split:
            for field in ('source','batch_id','activity_id','recorded_label','review_state','note'):self.assertEqual(r[field],before[0][field])
            self.assertEqual(r['timing_precision'],'unknown')
        undone=self.mutate(review.undo,result['operationId'])
        self.assertEqual(self.labels(),before);self.assertEqual(self.labels('B'),other)
        self.mutate(review.undo,undone['operationId']);self.assertEqual(self.labels(),split)
        with self.assertRaisesRegex(ValueError,'already undone'):self.mutate(review.undo,result['operationId'])
        with app.db() as c:
            self.assertGreater(c.execute('SELECT COUNT(*) FROM activity_label_history WHERE label_id=?',(label,)).fetchone()[0],3)
            with self.assertRaises(sqlite3.IntegrityError):c.execute('DELETE FROM episode_operations')

    def test_stale_split_and_aba_undo_cannot_overwrite_later_work(self):
        label=app.add_label('A','Read','old',0,10000);version=self.version(label)
        app.add_label('A','Read','new',0,10000,label)
        edit_op=self.last()
        with self.assertRaisesRegex(ValueError,'changed'):self.mutate(review.split,label,5000,version)
        app.add_label('A','Read','other',0,10000,label);app.add_label('A','Read','new',0,10000,label)
        with self.assertRaisesRegex(ValueError,'later history'):self.mutate(review.undo,edit_op)
        self.assertEqual(self.labels()[0]['note'],'new')
        with self.assertRaisesRegex(ValueError,'not found'):self.mutate(review.undo,edit_op,person='B')

    def test_edit_undo_restores_all_refined_suggestions_but_keeps_unrelated_labels(self):
        with app.db() as c:c.execute("INSERT INTO labels(participant_id,label,note,start_ms,end_ms,source,reviewed,batch_id) VALUES('A','Work','Suggested',0,10000,'schedule',0,'batch')")
        before=self.labels();app.add_label('A','Break','Human',4000,6000);operation=self.last()
        self.assertEqual(len(self.labels()),3)
        later=app.add_label('A','Unrelated','Keep',20000,30000)
        self.mutate(review.undo,operation)
        rows=self.labels();self.assertEqual(rows[:1],before);self.assertEqual(rows[1]['id'],later)
        self.assertEqual(rows[0]['source'],'schedule');self.assertEqual(rows[0]['review_state'],'needs_review')

    def test_conflicting_restoration_is_rejected_without_partial_changes(self):
        label=app.add_label('A','Read','',1000,10000)
        app.add_label('A','Read','moved',11000,20000,label);edit=self.last()
        app.add_label('A','New activity','',1000,10000)
        before=self.labels()
        with self.assertRaisesRegex(ValueError,'overlaps'):self.mutate(review.undo,edit)
        self.assertEqual(self.labels(),before)

    def test_unknown_is_distinct_from_unassigned_and_vocabulary_and_reversible(self):
        self.records(range(0,10000,1000));app.add_label('A','Known','',8000,10000)
        task=self.queue()['items'][0]
        result=self.mutate(review.decide,task,'unknown')
        with app.db() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM activities WHERE participant_id='A'").fetchone()[0],1)
        self.assertEqual(self.queue()['unknownTotal'],1);self.assertEqual(self.queue()['ordinaryTotal'],0)
        comparison=app.summary('A',0,10000)['activities']
        unknown=[r for r in comparison if r['unknown']]
        self.assertEqual(unknown[0]['records'],8);self.assertFalse(unknown[0]['unassigned'])
        view=app.daily_signal('A','1969-12-31',0,10000)
        self.assertEqual([(r['startMs'],r['endMsExclusive'],r['state']) for r in view['lanes']['reviewDecisions']],[(0,8000,'activity_unknown')])
        with self.assertRaisesRegex(ValueError,'marked activity unknown'):app.add_label('A','Invented','',1000,2000)
        self.mutate(review.undo,result['operationId'])
        self.assertEqual(self.queue()['unknownTotal'],0);self.assertEqual(self.queue()['ordinaryTotal'],1)

    def test_unknown_can_replace_unreviewed_label_and_restore_original_source(self):
        self.records(range(0,10000,1000))
        with app.db() as c:c.execute("INSERT INTO labels(participant_id,label,start_ms,end_ms,source,reviewed) VALUES('A','Work',0,10000,'schedule',0)")
        before=self.labels();task=self.queue()['items'][0]
        result=self.mutate(review.decide,task,'unknown');self.assertEqual(self.labels(),[])
        self.mutate(review.undo,result['operationId']);self.assertEqual(self.labels(),before)

    def test_reject_is_a_decision_not_unknown_and_preserves_original_on_undo(self):
        with app.db() as c:c.execute("INSERT INTO labels(participant_id,label,start_ms,end_ms,source,reviewed) VALUES('A','Work',0,10000,'model',0)")
        before=self.labels();task=self.queue()['items'][0]
        result=self.mutate(review.decide,task,'rejected');self.assertEqual(self.labels(),[])
        q=self.queue();self.assertEqual(q['unknownTotal'],0);self.assertEqual(q['rejectedTotal'],1)
        self.mutate(review.undo,result['operationId']);self.assertEqual(self.labels(),before)
        with self.assertRaisesRegex(ValueError,'changed'):self.mutate(review.decide,task,'rejected')

    def test_queue_counts_exact_fractional_coverage_and_is_model_independent_by_default(self):
        self.records([0,500,1000,4000,5000,1800000]);self.records([7000],person='B')
        app.add_label('A','Protected','',600,1100)
        first=self.queue();self.assertEqual(first,self.queue())
        self.assertEqual(first['candidateTotal'],0)
        # Source interval union = [0,2000), [4000,6000), [1800000,1801000), minus protected [600,1100).
        self.assertEqual(sum(i['recordedMs'] for i in first['items']),4500)
        self.assertTrue(all(i['recordedMs']==i['endMsExclusive']-i['startMs'] for i in first['items']))
        self.assertTrue(all('annotation' not in i and 'candidateIdentity' not in i for i in first['items']))
        page=self.queue(limit=1);self.assertEqual(page['total'],first['total']);self.assertEqual(len(page['items']),1);self.assertTrue(page['hasMore'])
        self.assertEqual(self.queue(person='B')['total'],1)

    def test_candidate_context_is_retained_without_inventing_an_activity(self):
        self.records(range(0,10000,1000))
        proposals=[{'startMs':0,'endMsExclusive':10000,'candidateIdentity':'snapshot','method':'pelt','maxResolutionMs':4000}]
        task=next(i for i in self.queue(candidates=proposals)['items'] if i['kind']=='candidate')
        app.add_label('A','My individual activity','',0,10000,review_key=task['key'],candidate_provider=lambda participant:proposals)
        with app.db() as c:
            op=c.execute('SELECT context_json FROM episode_operations ORDER BY id DESC LIMIT 1').fetchone()[0]
            context=json.loads(op);self.assertEqual(context['candidateIdentity'],'snapshot');self.assertEqual(context['key'],task['key'])
        self.assertEqual(self.labels()[0]['source'],'manual')
        with self.assertRaisesRegex(ValueError,'review item changed'):app.add_label('A','Stale','',20000,30000,review_key=task['key'],candidate_provider=lambda person:proposals)

    def test_unsupported_or_cross_participant_journal_cannot_be_restored(self):
        label=app.add_label('A','Read','',0,1000)
        with app.db() as c:
            before={'labels':{str(label):None},'decisions':{}};after={'labels':{str(label):dict(c.execute('SELECT * FROM labels').fetchone())},'decisions':{}}
            after['labels'][str(label)]['participant_id']='B'
            op=c.execute("INSERT INTO episode_operations(participant_id,action,before_json,after_json,versions_json,changed_ms,context_json) VALUES('A','bad',?,?,?,?, '{}')",(json.dumps(before),json.dumps(after),json.dumps({str(label):self.version(label)}),0)).lastrowid
        with self.assertRaisesRegex(ValueError,'scope'):self.mutate(review.undo,op)
        self.assertEqual(self.labels()[0]['participant_id'],'A')

    def test_old_v2_archive_migrates_with_backup_and_no_source_or_history_changes(self):
        profile=self.root/'baseline';profile.mkdir()
        database=profile/'awear_data.sqlite3'
        with closing(sqlite3.connect(database)) as c:c.executescript((Path(__file__).parent/'fixtures'/'activities-v2.sql').read_text(encoding='utf-8'))
        def rows(table):
            with closing(sqlite3.connect(database)) as c:return c.execute('SELECT * FROM '+table).fetchall()
        original={t:rows(t) for t in ('readings','labels','activities','activity_label_history')}
        app.configure_storage(profile)
        with patch.dict(review.TRIGGERS,{'mt_episode_failure':'NOT VALID SQL'}):
            with self.assertRaises(sqlite3.OperationalError):app.setup_db()
        with closing(sqlite3.connect(database)) as c:self.assertFalse(c.execute("SELECT 1 FROM sqlite_master WHERE name='episode_schema'").fetchone())
        app.setup_db();app.setup_db()
        for table,value in original.items():self.assertEqual(rows(table),value)
        backups=list((ArchivePaths(profile).activity_migrations/'episodes').glob('*.sqlite3'));self.assertEqual(len(backups),2)
        with app.db() as c:
            personal_activities.validate_archive_objects(c)
            exported=json.loads(b''.join(personal_activities.export_json(c,'A')))
        self.assertEqual(exported['formatVersion'],3);self.assertEqual(exported['episodeDecisions'],[]);self.assertEqual(exported['episodeOperations'],[])
