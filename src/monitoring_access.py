"""Scoped immutable-history access alongside separate live lifecycle freshness."""
from __future__ import annotations
from analysis_provenance import fingerprint,canonical_json
import analysis_history as history
from monitoring_model import compare


def page(manager,participant,params=None):
    from analysis_manager import read_json
    params=params or {}
    with manager.lock:
        state=manager.state(participant);signature=manager.signature();pointer={}
        if participant:
            try:pointer=read_json(manager.root/(manager.scope(participant)+'.json'))
            except (OSError,ValueError):pass
        live='no_participant' if not participant else 'recomputing' if state['job'] and state['job']['state'] in ('running','canceling') else 'stale' if state['stale'] else 'current' if state['summary'] else 'unavailable'
        value=history.read(manager,participant,offset=int(params.get('offset','0')),limit=int(params.get('limit','20')),selected=params.get('version') or None,baseline=params.get('baseline') or None,active=pointer.get('monitoringVersion'))
        comparison=compare(value['baseline'],value['selected'],offset=int(params.get('rowOffset','0')),limit=int(params.get('rowLimit','20')))
        result={'participantId':participant,'liveStatus':live,'available':state['available'],'job':state['job'],'settings':state['settings'],
            'activeVersion':pointer.get('monitoringVersion'),'history':{k:v for k,v in value.items() if k not in ('selected','baseline')},
            'selected':value['selected']['header'] if value['selected'] else None,'baseline':value['baseline']['header'] if value['baseline'] else None,
            'comparison':comparison,'meaning':'Retained validated numerical captures, not a job/adoption ledger. Historical captures do not prove prior activation; live status is separate.',
            'retention':'Up to 200 distinct versions per source/participant and 128 MB profile payload; no silent history deletion. Unchanged snapshots deduplicate.',
            'historyStart':'History begins with captures made by this version; old job summaries are not backfilled.'}
        again=manager.state(participant)
        status_key=lambda s:(s['stale'],s['job']['state'] if s['job'] else None,s['summary']['analysisIdentity'] if s['summary'] else None,s['settings'])
        if signature!=manager.signature() or status_key(state)!=status_key(again):return {'participantId':participant,'liveStatus':'refresh_required','history':{'versions':[],'total':0},'comparison':{'status':'refresh_required'}}
        result['viewIdentity']=fingerprint(result)
        if len(canonical_json(result).encode('utf-8'))>16_000_000:raise ValueError('Monitoring response exceeds16MB; choose smaller history/row pages')
        return result


def payload(manager,participant,identifier):
    result=history.read(manager,participant,selected=identifier)
    if result['selected'] is None:raise ValueError('Choose a retained numerical version')
    return result['selected']['numerical']
