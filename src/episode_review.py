"""Participant-scoped episode decisions and reversible annotation operations. Stdlib only."""
from __future__ import annotations
from contextlib import closing
import hashlib
from bisect import bisect_right
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid
from archive_paths import ArchivePaths

VERSION = 1
TRIGGERS = {f'mt_episode_operations_no_{op.lower()}':f"BEFORE {op} ON episode_operations BEGIN SELECT RAISE(ABORT,'Episode operations are append only'); END" for op in ('UPDATE','DELETE')}


def validate(connection):
    if [r[0] for r in connection.execute('SELECT version FROM episode_schema')]!=[VERSION]:raise ValueError('Unsupported episode schema version')
    required={'episode_operations':{'id','participant_id','action','before_json','after_json','versions_json','undo_of','changed_ms','context_json'},
              'episode_decisions':{'id','participant_id','task_key','kind','start_ms','end_ms','generation','note'},
              'episode_schema':{'version','backup_name'}}
    for table,columns in required.items():
        if not columns <= {r[1] for r in connection.execute('PRAGMA table_info('+table+')')}:raise ValueError('Incomplete episode schema')
    objects={r[0]:r[1] for r in connection.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND name LIKE 'mt_episode_%'")}
    normalize=lambda v:' '.join(v.split()).rstrip(';')
    if set(objects)!=set(TRIGGERS) or any(normalize(objects[k])!=normalize('CREATE TRIGGER '+k+' '+v) for k,v in TRIGGERS.items()):raise ValueError('Unsupported episode triggers')


def migrate(connection,database,profile):
    tables={r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'episode_schema' in tables:validate(connection);return
    if tables & {'episode_operations','episode_decisions'}:raise ValueError('Unversioned episode tables; preserve this archive before upgrading')
    connection.commit()
    backup=None
    if any(connection.execute('SELECT 1 FROM '+table+' LIMIT 1').fetchone() for table in ('readings','labels','activities','mobile_scores')):
        directory=ArchivePaths(profile).activity_migrations/'episodes';directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        backup='before-episodes-v1-'+uuid.uuid4().hex+'.sqlite3';target=directory/backup
        with closing(sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True)) as source,closing(sqlite3.connect(target)) as destination:
            os.chmod(target,0o600);source.backup(destination)
    connection.execute('BEGIN IMMEDIATE')
    if connection.execute("SELECT 1 FROM sqlite_master WHERE name='episode_schema'").fetchone():validate(connection);return
    connection.execute('''CREATE TABLE episode_operations(id INTEGER PRIMARY KEY AUTOINCREMENT,participant_id TEXT NOT NULL,
        action TEXT NOT NULL,before_json TEXT NOT NULL,after_json TEXT NOT NULL,versions_json TEXT NOT NULL,
        undo_of INTEGER UNIQUE,changed_ms INTEGER NOT NULL,context_json TEXT NOT NULL)''')
    connection.execute('CREATE INDEX episode_operations_scope ON episode_operations(participant_id,id)')
    connection.execute('''CREATE TABLE episode_decisions(id INTEGER PRIMARY KEY AUTOINCREMENT,participant_id TEXT NOT NULL,
        task_key TEXT NOT NULL,kind TEXT NOT NULL CHECK(kind IN ('unknown','rejected')),start_ms INTEGER NOT NULL,
        end_ms INTEGER NOT NULL CHECK(end_ms>start_ms),generation TEXT NOT NULL,note TEXT NOT NULL DEFAULT '',
        UNIQUE(participant_id,task_key))''')
    connection.execute('CREATE INDEX episode_decisions_scope ON episode_decisions(participant_id,start_ms,end_ms)')
    connection.execute('CREATE TABLE episode_schema(version INTEGER PRIMARY KEY,backup_name TEXT)')
    connection.execute('INSERT INTO episode_schema VALUES(?,?)',(VERSION,backup))
    for name,sql in TRIGGERS.items():connection.execute('CREATE TRIGGER '+name+' '+sql)


def snapshot(connection,participant):
    return {kind:{str(r['id']):dict(r) for r in connection.execute('SELECT * FROM '+table+' WHERE participant_id=? ORDER BY id',(participant,))}
            for kind,table in (('labels','labels'),('decisions','episode_decisions'))}


def label_version(connection,participant,label_id):
    return connection.execute('SELECT COALESCE(MAX(event_id),0) FROM activity_label_history WHERE participant_id=? AND label_id=?',(participant,label_id)).fetchone()[0]


def record(connection,participant,action,before,undo_of=None,context=None):
    after=snapshot(connection,participant)
    touched={kind:sorted(set(before[kind])|set(after[kind]),key=int) for kind in before}
    touched={kind:[key for key in keys if before[kind].get(key)!=after[kind].get(key)] for kind,keys in touched.items()}
    if not any(touched.values()):return None
    versions={key:label_version(connection,participant,int(key)) for key in touched['labels']}
    clipped=lambda value:{kind:{key:value[kind].get(key) for key in keys} for kind,keys in touched.items()}
    encode=lambda value:json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
    cursor=connection.execute('INSERT INTO episode_operations(participant_id,action,before_json,after_json,versions_json,undo_of,changed_ms,context_json) VALUES(?,?,?,?,?,?,?,?)',
        (participant,action,encode(clipped(before)),encode(clipped(after)),encode(versions),undo_of,int(time.time()*1000),encode(context or {})))
    return cursor.lastrowid


def require_label(connection,participant,label_id,expected):
    if type(label_id) is not int or label_id<1:raise ValueError('Invalid label ID')
    row=connection.execute('SELECT * FROM labels WHERE participant_id=? AND id=?',(participant,label_id)).fetchone()
    if row is None:raise ValueError('Label not found')
    if type(expected) is not int or expected!=label_version(connection,participant,label_id):raise ValueError('This label changed. Refresh before editing.')
    return row


def protect_unknown(connection,participant,start,end):
    if connection.execute("SELECT 1 FROM episode_decisions WHERE participant_id=? AND kind='unknown' AND start_ms<COALESCE(?,9223372036854775807) AND end_ms>? LIMIT 1",(participant,end,start)).fetchone():
        raise ValueError('This period is marked activity unknown. Undo that decision before assigning an activity.')


def split(connection,participant,label_id,cut,expected):
    row=require_label(connection,participant,label_id,expected)
    if type(cut) is not int or row['end_ms'] is None or not row['start_ms']<cut<row['end_ms']:raise ValueError('Choose a split strictly inside a finished interval')
    before=snapshot(connection,participant)
    connection.execute("UPDATE labels SET end_ms=?,timing_precision='unknown',start_uncertainty_ms=NULL,end_uncertainty_ms=NULL WHERE id=?",(cut,label_id))
    right=dict(row);right.pop('id');right.update(start_ms=cut,timing_precision='unknown',start_uncertainty_ms=None,end_uncertainty_ms=None)
    columns=list(right)
    cursor=connection.execute('INSERT INTO labels('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',[right[k] for k in columns])
    operation=record(connection,participant,'split',before)
    return {'leftId':label_id,'rightId':cursor.lastrowid,'operationId':operation}


def decide(connection,participant,task,kind,note=''):
    if kind not in ('unknown','rejected') or not isinstance(note,str) or len(note)>500:raise ValueError('Choose unknown or reject; note maximum 500 characters')
    start,end=task['startMs'],task['endMsExclusive']
    before=snapshot(connection,participant)
    if kind=='unknown':
        if task.get('labelId'):
            row=require_label(connection,participant,task['labelId'],task['labelVersion'])
            if row['review_state']=='confirmed':raise ValueError('A confirmed activity must be edited explicitly')
            connection.execute('DELETE FROM labels WHERE id=?',(row['id'],))
        protect_unknown(connection,participant,start,end)
        if connection.execute('SELECT 1 FROM labels WHERE participant_id=? AND start_ms<? AND COALESCE(end_ms,9223372036854775807)>?',(participant,end,start)).fetchone():raise ValueError('Edit or reject the existing label before marking this period unknown')
    elif task.get('labelId'):
        row=require_label(connection,participant,task['labelId'],task['labelVersion'])
        if row['source'] not in ('schedule','model') or row['review_state']!='needs_review':raise ValueError('Only pending schedule/model suggestions can be rejected; edit a human label instead')
        connection.execute('DELETE FROM labels WHERE id=?',(row['id'],))
    connection.execute('INSERT INTO episode_decisions(participant_id,task_key,kind,start_ms,end_ms,generation,note) VALUES(?,?,?,?,?,?,?)',
        (participant,task['key'],kind,start,end,uuid.uuid4().hex,note))
    return {'operationId':record(connection,participant,kind,before,context=task)}


def undo(connection,participant,operation_id):
    if type(operation_id) is not int or operation_id<1:raise ValueError('Invalid operation ID')
    operation=connection.execute('SELECT * FROM episode_operations WHERE participant_id=? AND id=?',(participant,operation_id)).fetchone()
    if operation is None:raise ValueError('Operation not found')
    if connection.execute('SELECT 1 FROM episode_operations WHERE undo_of=?',(operation_id,)).fetchone():raise ValueError('This operation was already undone')
    expected=json.loads(operation['after_json']);restore=json.loads(operation['before_json']);versions=json.loads(operation['versions_json'])
    current=snapshot(connection,participant)
    if set(expected)!={'labels','decisions'} or set(restore)!=set(expected):raise ValueError('Invalid operation snapshot')
    for kind,table in (('labels','labels'),('decisions','episode_decisions')):
        columns={r[1] for r in connection.execute('PRAGMA table_info('+table+')')}
        if not isinstance(expected[kind],dict) or not isinstance(restore[kind],dict) or set(expected[kind])!=set(restore[kind]):raise ValueError('Invalid operation identities')
        for rows in (expected[kind],restore[kind]):
            for key,row in rows.items():
                if not key.isdecimal() or int(key)<1:raise ValueError('Invalid operation identity')
                if row is None:continue
                if not isinstance(row,dict) or set(row)!=columns or row['participant_id']!=participant or type(row['id']) is not int or row['id']!=int(key):raise ValueError('Invalid operation scope')
                if type(row['start_ms']) is not int or (row['end_ms'] is not None and (type(row['end_ms']) is not int or row['end_ms']<=row['start_ms'])):raise ValueError('Invalid operation bounds')
    if set(versions)!=set(expected['labels']) or any(type(v) is not int or v<0 for v in versions.values()):raise ValueError('Invalid operation versions')
    for kind,rows in expected.items():
        for key,row in rows.items():
            if current[kind].get(key)!=row:raise ValueError('A touched interval changed. Undo is blocked; review its history.')
    for key,value in versions.items():
        if label_version(connection,participant,int(key))!=value:raise ValueError('A touched label has later history. Undo is blocked.')
    # Restoring cannot silently overwrite a newly assigned label or unknown interval.
    restored_spans=[r for rows in restore.values() for r in rows.values() if r is not None and (r.get('kind') in (None,'unknown'))]
    untouched=[r for kind,rows in current.items() for key,r in rows.items() if key not in expected[kind] and (kind=='labels' or r['kind']=='unknown')]
    for a in restored_spans:
        for b in untouched:
            if a['start_ms']<(b['end_ms'] if b['end_ms'] is not None else 2**63-1) and b['start_ms']<(a['end_ms'] if a['end_ms'] is not None else 2**63-1):raise ValueError('Restoration overlaps a later or unrelated protected interval. Edit it explicitly first.')
    for kind,table in (('labels','labels'),('decisions','episode_decisions')):
        for key in expected[kind]:connection.execute('DELETE FROM '+table+' WHERE id=? AND participant_id=?',(int(key),participant))
        for row in restore[kind].values():
            if row is None:continue
            row=dict(row)
            if kind=='decisions':row['generation']=uuid.uuid4().hex
            columns=list(row)
            connection.execute('INSERT INTO '+table+'('+','.join('"'+c.replace('"','""')+'"' for c in columns)+') VALUES('+','.join('?' for _ in columns)+')',[row[k] for k in columns])
    return {'operationId':record(connection,participant,'undo',current,operation_id)}


def history(connection,participant,offset=0,limit=25):
    if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=100:raise ValueError('Invalid history page')
    total=connection.execute('SELECT COUNT(*) FROM episode_operations WHERE participant_id=?',(participant,)).fetchone()[0]
    rows=connection.execute('''SELECT o.*,EXISTS(SELECT 1 FROM episode_operations u WHERE u.undo_of=o.id) AS undone
        FROM episode_operations o WHERE participant_id=? ORDER BY id DESC LIMIT ? OFFSET ?''',(participant,limit,offset)).fetchall()
    return {'participantId':participant,'total':total,'offset':offset,'operations':[dict(r) for r in rows],'hasMore':offset+len(rows)<total}


def unknown_spans(connection,participant,start,end):
    return [{'id':r['id'],'startMs':r['start_ms'],'endMsExclusive':r['end_ms'],'state':'activity_unknown'}
        for r in connection.execute("SELECT id,start_ms,end_ms FROM episode_decisions WHERE participant_id=? AND kind='unknown' AND start_ms<? AND end_ms>? ORDER BY start_ms,id",(participant,end,start))]


def key(participant,kind,start,end,identity=''):
    return hashlib.sha256(json.dumps([participant,kind,start,end,identity],separators=(',',':')).encode()).hexdigest()


def queue(connection,participant,*,offset=0,limit=25,candidates=(),task_key=None):
    """Whole-scope counts; ordinary intervals plus explicit optional candidate cues."""
    if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=100:raise ValueError('Invalid queue page')
    labels=[dict(r) for r in connection.execute('SELECT * FROM labels WHERE participant_id=? ORDER BY start_ms,id',(participant,))]
    decisions=[dict(r) for r in connection.execute('SELECT * FROM episode_decisions WHERE participant_id=? ORDER BY start_ms,id',(participant,))]
    rejected={r['task_key'] for r in decisions if r['kind']=='rejected'}
    protected=labels+[r for r in decisions if r['kind']=='unknown']
    items=[]
    for row in labels:
        if row['review_state']=='confirmed' or row['end_ms'] is None:continue
        version=label_version(connection,participant,row['id'])
        items.append({'key':key(participant,'label',row['start_ms'],row['end_ms'],str(version)), 'kind':'label','startMs':row['start_ms'],'endMsExclusive':row['end_ms'],
            'labelId':row['id'],'labelVersion':version,'rejectable':row['source'] in ('schedule','model') and row['review_state']=='needs_review','reason':'Existing interval needs review' if row['review_state']=='needs_review' else 'Activity marked unsure'})
    # Exact one-second coverage union: do not equate row counts with covered milliseconds.
    recorded=[]
    for r in connection.execute("SELECT timestamp_ms FROM readings WHERE participant_id=? AND typeof(timestamp_ms)='integer' AND timestamp_ms BETWEEN 0 AND 253402300798000 ORDER BY timestamp_ms",(participant,)):
        a,b=r[0],r[0]+1000
        if recorded and a<=recorded[-1][1]:recorded[-1][1]=max(b,recorded[-1][1])
        else:recorded.append([a,b])
    ordinary=[]
    for a,b in recorded:
        free=[(a,b)]
        for row in protected:
            lower,upper=row['start_ms'],row['end_ms'] if row['end_ms'] is not None else 2**63-1
            free=[span for x,y in free for span in ((x,min(y,lower)),(max(x,upper),y)) if span[0]<span[1]]
        for x,y in free:
            while x<y:
                end=min(y,(x//1800000+1)*1800000)
                ordinary.append({'key':key(participant,'ordinary',x,end),'kind':'ordinary','startMs':x,'endMsExclusive':end,'reason':'Ordinary coverage sample (stable shuffled order; no model selection)'})
                x=end
    ordinary.sort(key=lambda item:item['key'])
    suggested=[]
    for candidate in candidates:
        a,b=candidate['startMs'],candidate['endMsExclusive']
        if any(a<(r['end_ms'] if r['end_ms'] is not None else 2**63-1) and r['start_ms']<b for r in protected):continue
        suggested.append({**candidate,'key':key(participant,'candidate',a,b,candidate['candidateIdentity']),'kind':'candidate','reason':'Optional provisional mean-change period; not an activity'})
    # Every page should mix ordinary coverage with pending annotations and optional cues.
    pending=items;items=[]
    for index in range(max(len(pending),len(ordinary),len(suggested))):
        if index<len(pending):items.append(pending[index])
        if index<len(ordinary):items.append(ordinary[index])
        if index<len(suggested):items.append(suggested[index])
    items=[item for item in items if item['key'] not in rejected]
    starts=[x for x,y in recorded];cumulative=[0]
    for x,y in recorded:cumulative.append(cumulative[-1]+y-x)
    def elapsed(at):
        index=bisect_right(starts,at)-1
        return 0 if index<0 else cumulative[index]+min(at-recorded[index][0],recorded[index][1]-recorded[index][0])
    for item in items:
        a,b=item['startMs'],item['endMsExclusive']
        item['recordedMs']=elapsed(b)-elapsed(a)
        item['coveragePct']=100*item['recordedMs']/(b-a)
    page=[item for item in items if item['key']==task_key] if task_key else items[offset:offset+limit]
    invalid=connection.execute("SELECT COUNT(*) FROM readings WHERE participant_id=? AND (typeof(timestamp_ms)!='integer' OR timestamp_ms NOT BETWEEN 0 AND 253402300798000)",(participant,)).fetchone()[0]
    return {'invalidTimestampRecords':invalid,'participantId':participant,'total':len(items),'offset':offset,'items':page,'hasMore':offset+len(page)<len(items),
        'ordinaryTotal':sum(i['kind']=='ordinary' for i in items),'candidateTotal':sum(i['kind']=='candidate' for i in items),
        'unknownTotal':sum(r['kind']=='unknown' for r in decisions),'rejectedTotal':sum(r['kind']=='rejected' for r in decisions)}
