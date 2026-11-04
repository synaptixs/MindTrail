"""Stdlib-only verified composition of numerical discovery and separate human evidence."""
from __future__ import annotations
from contextlib import closing
from collections import Counter
import hashlib
from pathlib import Path
import sqlite3
import json
from analysis_provenance import canonical_json,fingerprint
import discovery_context as context
import discovery_questions as questions

MAX_BYTES=8_000_000


def validate(value,participant,identity):
    if value.get('version')!=1 or value.get('participantId')!=participant or value.get('analysisIdentity')!=identity or value.get('reportIdentity')!=fingerprint({k:v for k,v in value.items() if k!='reportIdentity'}):raise ValueError('Invalid discovery summary')
    return value


def validate_files(directory,participant,identity):
    from analysis_manager import read_json
    value=validate(read_json(directory/'discovery-report.json',limit=MAX_BYTES),participant,identity)
    for filename,key in [('discovery.json','discoveryIdentity'),('discovery-daily-index.json','dailyIdentity'),('recurrence-index.json','recurrenceIdentity'),('discovery-review.json','reviewIdentity')]:
        if read_json(directory/filename)[key]!=value[key]:raise ValueError('Report dependency identity differs')
    return value


def human_signature(manager):
    result=[]
    for base in (context.location(manager),questions.location(manager)):
        if base.is_symlink():raise ValueError('Invalid discovery evidence location')
        for p in (base,Path(str(base)+'-wal')):
            try:
                st=p.stat();result.append((p.name,None if p.name.endswith('-wal') and st.st_size==0 else (st.st_size,st.st_mtime_ns,st.st_ctime_ns,st.st_ino)))
            except FileNotFoundError:result.append((p.name,None))
    return result


def human_snapshot(manager,participant,numerical):
    value={'context':{'totalRecords':0,'active':0,'tombstones':0,'sameFeatureSnapshot':0,'earlierSnapshot':0,'kindTotals':{},'historyRows':0,'shown':0,'omitted':0,'items':[]},'questions':questions.page(manager,participant)}
    path=context.location(manager);h=hashlib.sha256();counts=Counter();cvalue=value['context']
    if path.exists():
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as c:
            c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
            if context.validate(c):
                scope=manager.scope(participant);cvalue['historyRows']=c.execute('SELECT COUNT(*) FROM history WHERE scope=?',(scope,)).fetchone()[0]
                for key,revision,encoded in c.execute('SELECT key,revision,record FROM current WHERE scope=? ORDER BY key',(scope,)):
                    record=json.loads(encoded) if encoded else None;h.update(canonical_json([key,revision,record]).encode()+b'\n');cvalue['totalRecords']+=1
                    if record is None:cvalue['tombstones']+=1;continue
                    if record['window']['acquisition']['participant_id']!=participant or record['kind'] not in context.KINDS:raise ValueError('Human context scope/schema differs')
                    cvalue['active']+=1;counts[record['kind']]+=1
                    same=all(record['provenance'][k]==numerical[k] for k in ('analysisIdentity','sourceRevision'));relation='same_feature_snapshot' if same else 'earlier_snapshot'
                    cvalue['sameFeatureSnapshot' if same else 'earlierSnapshot']+=1
                    if len(cvalue['items'])<50:cvalue['items'].append({'windowId':key,'revision':revision,'record':record,'relation':relation})
    cvalue.update(kindTotals=dict(sorted(counts.items())),shown=len(cvalue['items']));cvalue['omitted']=cvalue['active']-cvalue['shown']
    value['contextContentIdentity']=h.hexdigest();value['humanIdentity']=fingerprint(value)
    return value


def guard_current(manager,guard):
    return guard['source']==manager.signature() and guard['human']==human_signature(manager)


def capture(manager,participant):
    from analysis_manager import read_json
    result={'status':'unavailable','participantId':participant}
    if not participant:return dict(result,status='no_participant')
    with manager.lock:
        try:
            pointer=read_json(manager.root/(manager.scope(participant)+'.json'));signature=pointer['signature']
            if signature!=manager.signature():return dict(result,status='stale')
            directory=manager.directory(pointer['id']);identity=read_json(directory/'summary.json')['analysisIdentity']
            numerical=validate_files(directory,participant,identity)
        except (OSError,ValueError,KeyError,TypeError):return result
        before=human_signature(manager)
        try:human=human_snapshot(manager,participant,numerical)
        except (OSError,sqlite3.Error):raise ValueError('Human evidence is unavailable; recordings unchanged') from None
        guard={'source':signature,'human':before}
        if not guard_current(manager,guard):return dict(result,status='stale')
        method=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        value={'status':'current','participantId':participant,'numerical':numerical,'human':human,'compositionMethodIdentity':method,'_guard':guard}
        value['evidenceIdentity']=fingerprint({'report':numerical['reportIdentity'],'human':human['humanIdentity'],'composition':method})
        return value


def page(manager,participant,expected=None,*,internal=False):
    value=capture(manager,participant)
    if value['status']!='current':return value
    from discovery_decisions import read,signature
    from discovery_report_render import presentation_identity
    before=signature(manager);decision=read(manager,participant)
    if before!=signature(manager) or not guard_current(manager,value['_guard']):return {'status':'stale','participantId':participant}
    value['decision']=decision;value['decisionRelation']='not_recorded' if decision['record'] is None else 'current_evidence' if decision['record']['evidenceIdentity']==value['evidenceIdentity'] else 'earlier_evidence'
    value['presentationIdentity']=presentation_identity()
    value['snapshotIdentity']=fingerprint({k:v for k,v in value.items() if k not in ('_guard','status')})
    if expected is not None and expected!=value['snapshotIdentity']:raise ValueError('Report changed. Refresh before exporting this snapshot.')
    result={k:v for k,v in value.items() if k!='_guard'}
    if len(canonical_json(result).encode())>16_000_000:raise ValueError('Composed report exceeds16MB; narrow analysis range')
    if internal:result['_guard']=dict(value['_guard'],decision=before,presentation=value['presentationIdentity'])
    return result


def recheck(manager,value):
    from discovery_decisions import signature
    from discovery_report_render import presentation_identity
    guard=value['_guard']
    if not guard_current(manager,guard) or guard['decision']!=signature(manager) or guard['presentation']!=presentation_identity():raise ValueError('Report changed while preparing the response. Refresh and try again.')
