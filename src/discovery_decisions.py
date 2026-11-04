"""Explicit owner direction, separate from data, inference and confirmation reservation."""
from __future__ import annotations
from contextlib import closing
from datetime import datetime,timezone
import os
import sqlite3
import json
from analysis_provenance import canonical_json

CHOICES={'continue_discovery':'Keep exploring without mandatory labels','collect_context':'Collect more data or optional context',
 'plan_matched_recording':'Plan matched recordings later','insufficient_evidence':'Insufficient evidence for a next question'}


def location(manager):
    p=manager.discovery_root/'decisions.sqlite3'
    if p.is_symlink():raise ValueError('Invalid decision storage location')
    return p


def signature(manager):
    from pathlib import Path
    p=location(manager);values=[]
    for f in (p,Path(str(p)+'-wal')):
        try:
            s=f.stat();values.append(None if f.name.endswith('-wal') and s.st_size==0 else (s.st_size,s.st_mtime_ns,s.st_ctime_ns,s.st_ino))
        except FileNotFoundError:values.append(None)
    return values


def validate(c):
    version=c.execute('PRAGMA user_version').fetchone()[0]
    tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not version and not tables:return False
    if version!=1 or not {'decision','decision_history'}<=tables:raise ValueError('Unsupported decision schema')
    return True


def initialize(c):
    if validate(c):return
    c.execute('CREATE TABLE decision(scope TEXT PRIMARY KEY,revision INTEGER,record TEXT)')
    c.execute('CREATE TABLE decision_history(scope TEXT,revision INTEGER,record TEXT,PRIMARY KEY(scope,revision))')
    for op in ('UPDATE','DELETE'):c.execute(f"CREATE TRIGGER decision_history_no_{op.lower()} BEFORE {op} ON decision_history BEGIN SELECT RAISE(ABORT,'Decision history is append only'); END")
    c.execute('PRAGMA user_version=1')


def read(manager,participant,offset=0,limit=25):
    if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=100:raise ValueError('Invalid decision history page')
    value={'participantId':participant,'revision':0,'record':None,'history':[],'total':0,'shown':0,'offset':offset,'hasMore':False}
    p=location(manager)
    if not participant or not p.exists():return value
    try:
        with closing(sqlite3.connect(p.as_uri()+'?mode=ro',uri=True)) as c:
            c.execute('BEGIN')
            if not validate(c):return value
            scope=manager.scope(participant);row=c.execute('SELECT revision,record FROM decision WHERE scope=?',(scope,)).fetchone()
            if row:value.update(revision=row[0],record=json.loads(row[1]))
            value['total']=c.execute('SELECT COUNT(*) FROM decision_history WHERE scope=?',(scope,)).fetchone()[0]
            value['history']=[json.loads(r[0]) for r in c.execute('SELECT record FROM decision_history WHERE scope=? ORDER BY revision DESC LIMIT ? OFFSET ?',(scope,limit,offset))]
            value['shown']=len(value['history']);value['hasMore']=offset+value['shown']<value['total'];return value
    except (OSError,sqlite3.Error):raise ValueError('Decision history unavailable; recordings unchanged') from None


def save(manager,participant,body):
    if not participant or body.get('participantId')!=participant:raise ValueError('Participant changed; refresh before recording direction')
    revision=body.get('revision')
    if type(revision) is not int or revision<0 or body.get('choice') not in CHOICES:raise ValueError('Choose a supported discovery direction and current revision')
    notes=body.get('notes','')
    if not isinstance(notes,str) or len(notes)>2000:raise ValueError('Decision notes maximum2000 characters')
    from discovery_report_access import capture,guard_current
    with manager.lock:
        evidence=capture(manager,participant)
        if evidence['status']!='current' or body.get('evidenceIdentity')!=evidence['evidenceIdentity']:raise ValueError('Evidence changed. Refresh the report before saving direction.')
        question=None
        if body.get('questionId'):
            question=next((q for q in evidence['human']['questions']['questions'] if q['id']==body['questionId']),None)
            if question is None:raise ValueError('Choose a question belonging to this report participant')
        record={'revision':revision+1,'choice':body['choice'],'notes':notes,'question':question,'evidenceIdentity':evidence['evidenceIdentity'],
            'reportIdentity':evidence['numerical']['reportIdentity'],'analysisIdentity':evidence['numerical']['analysisIdentity'],
            'sourceRevision':evidence['numerical']['sourceRevision'],'humanIdentity':evidence['human']['humanIdentity'],
            'changedAt':datetime.now(timezone.utc).isoformat(),'inference':'not_implemented','confirmation':'not_reserved'}
        p=location(manager);p.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        try:
            with closing(sqlite3.connect(p)) as c,c:
                os.chmod(p,0o600);c.execute('BEGIN IMMEDIATE');initialize(c);scope=manager.scope(participant)
                old=c.execute('SELECT revision FROM decision WHERE scope=?',(scope,)).fetchone()
                if (old[0] if old else 0)!=revision:raise ValueError('Direction changed. Refresh before editing.')
                with closing(sqlite3.connect(manager.database.as_uri()+'?mode=ro',uri=True)) as archive:
                    archive.execute('BEGIN');selected=archive.execute("SELECT value FROM settings WHERE key='participant_id'").fetchone()
                    if not selected or selected[0]!=participant:raise ValueError('Selected participant changed')
                    if not guard_current(manager,evidence['_guard']):raise ValueError('Evidence changed before saving direction')
                    encoded=canonical_json(record)
                    c.execute('INSERT INTO decision VALUES(?,?,?) ON CONFLICT(scope) DO UPDATE SET revision=excluded.revision,record=excluded.record',(scope,revision+1,encoded))
                    c.execute('INSERT INTO decision_history VALUES(?,?,?)',(scope,revision+1,encoded))
                    if not guard_current(manager,evidence['_guard']):raise ValueError('Evidence changed before commit; direction not saved')
            return record
        except (OSError,sqlite3.Error):raise ValueError('Direction storage unavailable; recordings unchanged') from None
