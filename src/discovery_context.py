"""Human window-context observations, separate from EEG facts and activity assignments."""
from __future__ import annotations
from contextlib import closing,ExitStack
from datetime import datetime,timezone
import csv
import io
import json
import os
import re
import sqlite3
from analysis_provenance import canonical_json
from personal_activities import get as activity_get,label_evidence

KINDS=('known','partial','mixed','unknown','skip')
HEX=re.compile('[0-9a-f]{64}')


def location(manager):
    path=manager.discovery_root/'context.sqlite3'
    if path.is_symlink():raise ValueError('Invalid context storage location')
    return path


def validate(connection):
    if connection.execute('PRAGMA user_version').fetchone()[0]==0 and not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table'").fetchone():return False
    if connection.execute('PRAGMA user_version').fetchone()[0]!=1:raise ValueError('Unsupported context schema')
    for table,columns in {'current':{'scope','key','revision','record'},'history':{'scope','key','revision','before_json','after_json','action','undo_of','changed_at'}}.items():
        if not columns <= {r[1] for r in connection.execute('PRAGMA table_info('+table+')')}:raise ValueError('Incomplete context schema')
    return True


def initialize(connection):
    if connection.execute('PRAGMA user_version').fetchone()[0]:validate(connection);return
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table'").fetchone():raise ValueError('Unversioned context database')
    connection.execute('CREATE TABLE current(scope TEXT,key TEXT,revision INTEGER,record TEXT,PRIMARY KEY(scope,key))')
    connection.execute('CREATE TABLE history(scope TEXT,key TEXT,revision INTEGER,before_json TEXT,after_json TEXT,action TEXT,undo_of INTEGER,changed_at TEXT,PRIMARY KEY(scope,key,revision),UNIQUE(scope,key,undo_of))')
    for op in ('UPDATE','DELETE'):
        connection.execute(f"CREATE TRIGGER context_history_no_{op.lower()} BEFORE {op} ON history BEGIN SELECT RAISE(ABORT,'Context history is append only'); END")
    connection.execute('PRAGMA user_version=1')


def identifier(value):
    if not isinstance(value,str) or not HEX.fullmatch(value):raise ValueError('Invalid context window identity')
    return value


def integer(value):
    if type(value) is not int or value<0:raise ValueError('Invalid context revision')
    return value


def _entry(connection,scope,key):
    row=connection.execute('SELECT revision,record FROM current WHERE scope=? AND key=?',(scope,key)).fetchone()
    return {'windowId':key,'revision':row[0] if row else 0,'record':json.loads(row[1]) if row and row[1] else None}


def entry(manager,participant,key,offset=0,limit=25):
    key=identifier(key)
    if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=100:raise ValueError('Invalid context history page')
    value={'windowId':key,'revision':0,'record':None,'history':[],'total':0,'shown':0,'offset':offset}
    path=location(manager)
    if not participant or not path.exists():return value
    try:
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as c:
            c.execute('BEGIN')
            if not validate(c):return value
            scope=manager.scope(participant);value.update(_entry(c,scope,key))
            value['total']=c.execute('SELECT COUNT(*) FROM history WHERE scope=? AND key=?',(scope,key)).fetchone()[0]
            value['history']=[{'revision':r[0],'action':r[1],'undoOf':r[2],'changedAt':r[3],'before':json.loads(r[4]),'after':json.loads(r[5])} for r in c.execute('SELECT revision,action,undo_of,changed_at,before_json,after_json FROM history WHERE scope=? AND key=? ORDER BY revision DESC LIMIT ? OFFSET ?',(scope,key,limit,offset))]
        value['shown']=len(value['history']);value['hasMore']=offset+value['shown']<value['total']
        if len(canonical_json(value).encode())>2_000_000:raise ValueError('Context history response exceeds bound; reduce page size')
        return value
    except (sqlite3.Error,OSError):raise ValueError('Context storage unavailable; recordings unchanged') from None


def human_record(connection,participant,body,snapshot,row):
    kind=body.get('kind');ids=body.get('activityIds',[])
    if kind not in KINDS:raise ValueError('Choose known, partial, mixed, unknown or skip')
    if not isinstance(ids,list) or len(ids)>5 or any(not isinstance(i,str) or not re.fullmatch('[0-9a-f]{32}',i) for i in ids) or len(set(ids))!=len(ids):raise ValueError('Choose up to five distinct personal activities')
    if kind=='known' and len(ids)!=1:raise ValueError('Known activity needs one personal activity')
    if kind in ('unknown','skip') and ids:raise ValueError('Unknown and skip cannot assign an activity')
    for key in ('facts','hypothesis'):
        if not isinstance(body.get(key,''),str) or len(body.get(key,''))>2000:raise ValueError('Context text maximum 2000 characters per field')
    if kind=='partial' and not ids and not body.get('facts','').strip():raise ValueError('Add what you remember, or choose unknown or skip')
    if kind=='mixed' and len(ids)<2 and not body.get('facts','').strip():raise ValueError('Describe the mixture or choose at least two personal activities')
    activities=[]
    for activity_id in ids:
        value=activity_get(connection,participant,activity_id)
        if value['archived']:raise ValueError('Restore archived activities before using them in new context')
        activities.append({'id':value['id'],'name':value['name']})
    review,precision,start,end=label_evidence(None,body.get('evidence'),row['startMs'],row['endMsExclusive'])
    cues=body.get('cues',False)
    if type(cues) is not bool:raise ValueError('Invalid cue visibility')
    if not cues and 'ordinary' not in row['streams']:raise ValueError('Reveal targeted examples before reviewing them')
    provenance={k:snapshot[k] for k in ('analysisIdentity','sourceRevision','dailyIdentity','recurrenceIdentity','reviewIdentity')}
    provenance.update(source='manual_observation',cuesRevealed=cues,selectionReasons=row['reasons'] if cues else [r for r in row['reasons'] if r['kind']=='ordinary'])
    return {'kind':kind,'facts':body.get('facts',''),'hypothesis':body.get('hypothesis',''),'activities':activities,
        'reviewState':review,'timingPrecision':precision,'startUncertaintyMs':start,'endUncertaintyMs':end,
        'window':{k:row[k] for k in ('windowId','acquisitionGroupId','acquisition','date','startMs','endMsExclusive')},'provenance':provenance}


def _mutate(manager,participant,body,undo):
    if not participant or body.get('participantId')!=participant:raise ValueError('Participant changed; refresh before saving context')
    key=identifier(body.get('windowId'));expected=integer(body.get('revision'))
    with manager.lock:
        snapshot=row=None
        if not undo:
            from discovery_review_access import require
            snapshot,row=require(manager,participant,body)
        path=location(manager);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with closing(sqlite3.connect(path)) as c,c:
            os.chmod(path,0o600);c.execute('BEGIN IMMEDIATE');initialize(c);scope=manager.scope(participant);current=_entry(c,scope,key)
            if current['revision']!=expected:raise ValueError('Context changed. Reload before editing or undoing.')
            if not undo and current['record'] is not None and body.get('revealed') is not True:raise ValueError('Reveal existing context before replacing it')
            # Canonical vocabulary and selected participant are read in one archive snapshot.
            with closing(sqlite3.connect(manager.database.as_uri()+'?mode=ro',uri=True)) as archive:
                archive.row_factory=sqlite3.Row;archive.execute('PRAGMA query_only=ON');archive.execute('BEGIN')
                selected=archive.execute("SELECT value FROM settings WHERE key='participant_id'").fetchone()
                if not selected or selected[0]!=participant:raise ValueError('Selected participant changed; refresh')
                if undo:
                    prior=c.execute('SELECT before_json FROM history WHERE scope=? AND key=? AND revision=?',(scope,key,expected)).fetchone()
                    if prior is None:raise ValueError('No current change to undo')
                    record=json.loads(prior[0]);action='undo'
                else:record=human_record(archive,participant,body,snapshot,row);action='save'
                if snapshot and snapshot['sourceSignature']!=manager.signature():raise ValueError('Source changed before context was saved; refresh analysis')
                revision=expected+1;encoded=canonical_json(record) if record is not None else None
                c.execute('INSERT INTO current VALUES(?,?,?,?) ON CONFLICT(scope,key) DO UPDATE SET revision=excluded.revision,record=excluded.record',(scope,key,revision,encoded))
                c.execute('INSERT INTO history VALUES(?,?,?,?,?,?,?,?)',(scope,key,revision,canonical_json(current['record']),canonical_json(record),action,expected if undo else None,datetime.now(timezone.utc).isoformat()))
                if snapshot and snapshot['sourceSignature']!=manager.signature():raise ValueError('Source changed before commit; context was not saved')
        return {'windowId':key,'revision':revision,'record':record,'action':action}


def mutate(manager,participant,body,undo=False):
    try:return _mutate(manager,participant,body,undo)
    except (sqlite3.Error,OSError):raise ValueError('Context storage unavailable; recordings unchanged') from None


def _export(manager,participant,format='json',safe=None):
    """Stream a complete, prevalidated read transaction; JSON includes tombstones/history."""
    if not participant:raise ValueError('Choose a participant')
    if format not in ('json','csv'):raise ValueError('Choose JSON or CSV')
    if format=='csv' and not callable(safe):raise ValueError('CSV needs the canonical text sanitizer')
    path=location(manager);scope=manager.scope(participant)
    with ExitStack() as stack:
        c=None
        if path.exists():
            c=stack.enter_context(closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)))
            c.execute('BEGIN')
            if not validate(c):c=None
        if format=='json':yield (canonical_json({'formatVersion':1,'participantId':participant,'kind':'window_context_audit'})[:-1]+',"current":[').encode()
        else:
            stream=io.StringIO();writer=csv.writer(stream);writer.writerow(['window_id','revision','kind','facts','hypothesis','activities_json','review_state','timing_precision','start_uncertainty_ms','end_uncertainty_ms','window_json','provenance_json']);yield stream.getvalue().encode();stream.seek(0);stream.truncate(0)
        first=True
        for key,revision,encoded in c.execute('SELECT key,revision,record FROM current WHERE scope=? ORDER BY key',(scope,)) if c else ():
            record=json.loads(encoded) if encoded else None
            if format=='json':
                yield (('' if first else ',')+canonical_json({'windowId':key,'revision':revision,'record':record})).encode();first=False
            elif record is not None:
                writer.writerow([key,revision,record['kind'],safe(record['facts']),safe(record['hypothesis']),safe(canonical_json(record['activities'])),record['reviewState'],record['timingPrecision'],record['startUncertaintyMs'],record['endUncertaintyMs'],canonical_json(record['window']),canonical_json(record['provenance'])]);yield stream.getvalue().encode();stream.seek(0);stream.truncate(0)
        if format=='json':
            yield b'],"history":[';first=True
            for key,revision,before,after,action,undo_of,changed in c.execute('SELECT key,revision,before_json,after_json,action,undo_of,changed_at FROM history WHERE scope=? ORDER BY key,revision',(scope,)) if c else ():
                yield (('' if first else ',')+canonical_json({'windowId':key,'revision':revision,'before':json.loads(before),'after':json.loads(after),'action':action,'undoOf':undo_of,'changedAt':changed})).encode();first=False
            yield b']}'


def listing(manager,participant,offset=0,limit=25):
    if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=100:raise ValueError('Invalid context page')
    value={'participantId':participant,'items':[],'offset':offset,'total':0,'shown':0,'hasMore':False}
    path=location(manager)
    if not participant or not path.exists():return value
    try:
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as c:
            c.execute('BEGIN')
            if not validate(c):return value
            scope=manager.scope(participant)
            value['total']=c.execute('SELECT COUNT(*) FROM current WHERE scope=?',(scope,)).fetchone()[0]
            value['items']=[]
            for key,revision,encoded in c.execute('SELECT key,revision,record FROM current WHERE scope=? ORDER BY key LIMIT ? OFFSET ?',(scope,limit,offset)):
                prior=encoded
                if prior is None:
                    row=c.execute('SELECT CASE WHEN after_json=? THEN before_json ELSE after_json END FROM history WHERE scope=? AND key=? ORDER BY revision DESC LIMIT 1',('null',scope,key)).fetchone()
                    prior=row[0] if row else None
                record=json.loads(prior) if prior else None
                item={'windowId':key,'revision':revision,'hasContext':encoded is not None}
                if record is not None:item.update(startMs=record['window']['startMs'],endMsExclusive=record['window']['endMsExclusive'])
                value['items'].append(item)
        value['shown']=len(value['items']);value['hasMore']=offset+value['shown']<value['total'];return value
    except (sqlite3.Error,OSError):raise ValueError('Context storage unavailable') from None


def export(manager,participant,format='json',safe=None):
    try:yield from _export(manager,participant,format,safe)
    except (sqlite3.Error,OSError):raise ValueError('Context export unavailable; recordings unchanged') from None
