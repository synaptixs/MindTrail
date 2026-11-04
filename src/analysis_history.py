"""Immutable, scoped numerical snapshot history, independent of generated job retention."""
from __future__ import annotations
from contextlib import closing
from datetime import datetime,timezone
import json
import os
import re
import sqlite3
from analysis_provenance import canonical_json,fingerprint

MAX_VERSIONS=200
MAX_BYTES=128_000_000
PAYLOAD_LIMIT=8_000_000


class HistoryError(ValueError):pass


def location(manager):
    path=manager.discovery_root/'monitoring.sqlite3'
    if path.is_symlink():raise HistoryError('Invalid monitoring history location')
    return path


def validate(connection):
    version=connection.execute('PRAGMA user_version').fetchone()[0]
    tables={r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not tables and not version:return False
    if version!=1 or 'versions' not in tables:raise HistoryError('Unsupported monitoring history schema; existing versions unchanged')
    return True


def initialize(connection):
    if validate(connection):return
    connection.execute('CREATE TABLE versions(sequence INTEGER PRIMARY KEY AUTOINCREMENT,scope TEXT NOT NULL,version TEXT NOT NULL,captured_at TEXT NOT NULL,metadata TEXT NOT NULL,payload TEXT NOT NULL,bytes INTEGER NOT NULL,UNIQUE(scope,version))')
    for op in ('UPDATE','DELETE'):connection.execute(f"CREATE TRIGGER versions_no_{op.lower()} BEFORE {op} ON versions BEGIN SELECT RAISE(ABORT,'Monitoring versions are immutable'); END")
    connection.execute('PRAGMA user_version=1')


def metadata(value):
    context=value.get('analysisContext')
    result={k:value[k] for k in ('reportIdentity','analysisIdentity','sourceRevision','observedExtent','timezone','requestedRange','demo')}
    result['coverage']={k:value['coverage'].get(k,0) for k in ('sourceRecords','computedWindows','unflaggedWindows','flaggedWindows','excludedRecords','boundaryRemainderRecordSeconds','featureFailureWindows')}
    result['comparisonKey']=fingerprint({'context':context,'timezone':value['timezone'],'range':value['requestedRange']}) if context else None
    result['metadataIdentity']=fingerprint(result);return result


def capture(manager,participant,value):
    from discovery_report_access import validate as validate_report
    validate_report(value,participant,value['analysisIdentity'])
    encoded=canonical_json(value);size=len(encoded.encode('utf-8'))
    if size>PAYLOAD_LIMIT:raise HistoryError('Monitoring snapshot exceeds8MB; previous versions retained')
    meta=canonical_json(metadata(value));path=location(manager);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    try:
        with closing(sqlite3.connect(path)) as c,c:
            os.chmod(path,0o600);c.execute('BEGIN IMMEDIATE');initialize(c);scope=manager.scope(participant)
            old=c.execute('SELECT payload FROM versions WHERE scope=? AND version=?',(scope,value['reportIdentity'])).fetchone()
            if old:
                if old[0]!=encoded:raise HistoryError('Monitoring identity collision; previous versions retained')
                return value['reportIdentity']
            if c.execute('SELECT COUNT(*) FROM versions WHERE scope=?',(scope,)).fetchone()[0]>=MAX_VERSIONS:raise HistoryError('Monitoring history limit reached (200 versions per source/participant); previous versions retained')
            if c.execute('SELECT COALESCE(SUM(bytes),0) FROM versions').fetchone()[0]+size>MAX_BYTES:raise HistoryError('Monitoring history limit reached (128MB per profile); previous versions retained')
            c.execute('INSERT INTO versions(scope,version,captured_at,metadata,payload,bytes) VALUES(?,?,?,?,?,?)',(scope,value['reportIdentity'],datetime.now(timezone.utc).isoformat(),meta,encoded,size))
        return value['reportIdentity']
    except (OSError,sqlite3.Error):raise HistoryError('Monitoring history could not be saved; previous versions retained. Check storage and retry.') from None


def header(row):
    sequence,identifier,captured,encoded=row;meta=json.loads(encoded)
    if meta.get('reportIdentity')!=identifier or meta.get('metadataIdentity')!=fingerprint({k:v for k,v in meta.items() if k!='metadataIdentity'}):raise HistoryError('Invalid monitoring metadata')
    return dict(meta,sequence=sequence,version=identifier,capturedAt=captured)


def read(manager,participant,*,offset=0,limit=20,selected=None,baseline=None,active=None):
    if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=50:raise ValueError('Invalid history page')
    for value in (selected,baseline,active):
        if value is not None and (not isinstance(value,str) or not re.fullmatch('[0-9a-f]{64}',value)):raise ValueError('Invalid monitoring version identity')
    result={'versions':[],'total':0,'shown':0,'omitted':0,'offset':offset,'hasMore':False,'selected':None,'baseline':None}
    path=location(manager)
    if not participant or not path.exists():
        if selected or baseline:raise ValueError('Version unavailable for this source/participant')
        return result
    try:
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as c:
            c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
            if not validate(c):
                if selected or baseline:raise ValueError('Version unavailable for this source/participant')
                return result
            scope=manager.scope(participant);result['total']=c.execute('SELECT COUNT(*) FROM versions WHERE scope=?',(scope,)).fetchone()[0]
            result['versions']=[header(r) for r in c.execute('SELECT sequence,version,captured_at,metadata FROM versions WHERE scope=? ORDER BY sequence DESC LIMIT ? OFFSET ?',(scope,limit,offset))]
            def load(identifier):
                row=c.execute('SELECT sequence,version,captured_at,metadata,payload,bytes FROM versions WHERE scope=? AND version=?',(scope,identifier)).fetchone()
                if row is None:raise ValueError('Version unavailable for this source/participant')
                if row[5]>PAYLOAD_LIMIT or len(row[4].encode('utf-8'))!=row[5]:raise HistoryError('Invalid monitoring payload size')
                value=json.loads(row[4]);from discovery_report_access import validate as validate_report
                validate_report(value,participant,header(row[:4])['analysisIdentity'])
                h=header(row[:4])
                if metadata(value)!={k:v for k,v in h.items() if k not in ('sequence','version','capturedAt')}:raise HistoryError('Monitoring payload and metadata differ')
                return {'header':h,'numerical':value}
            identifier=selected or active
            if identifier and (selected or c.execute('SELECT 1 FROM versions WHERE scope=? AND version=?',(scope,identifier)).fetchone()):result['selected']=load(identifier)
            if baseline:result['baseline']=load(baseline)
            elif result['selected']:
                prior=c.execute('SELECT version FROM versions WHERE scope=? AND sequence<? ORDER BY sequence DESC LIMIT 1',(scope,result['selected']['header']['sequence'])).fetchone()
                if prior:result['baseline']=load(prior[0])
            result['shown']=len(result['versions']);result['omitted']=result['total']-result['shown'];result['hasMore']=offset+result['shown']<result['total'];return result
    except (OSError,sqlite3.Error,json.JSONDecodeError,KeyError,TypeError):raise HistoryError('Monitoring history is unavailable; saved versions were not changed') from None
