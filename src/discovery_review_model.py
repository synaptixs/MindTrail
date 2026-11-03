"""Deterministic bounded review selection from canonical daily/recurrence facts."""
from __future__ import annotations
from collections import defaultdict
import hashlib
from pathlib import Path
from analysis_provenance import canonical_json,fingerprint
from recurrence_access import retrieve

POLICY={'perStream':8,'recurrenceReferences':4,'streams':['ordinary','change','recurrence'],
 'selection':'Hash order inside exact-context/date strata, round robin across sorted strata; streams interleaved, ordinary first',
 'meaning':'Selective windows, not a probability sample, complete review or matched episodes',
 'recurrenceView':'with_power','default':'ordinary only; no existing annotations or targeted reasons revealed'}


def diverse(rows,limit):
    strata=defaultdict(list)
    for row in sorted(rows,key=lambda r:r['windowId']):strata[(row['acquisitionGroupId'],row['date'])].append(row)
    selected=[];depth=0
    while len(selected)<limit:
        added=False
        for key in sorted(strata):
            if depth<len(strata[key]):selected.append(strata[key][depth]);added=True
            if len(selected)==limit:break
        if not added:break
        depth+=1
    return selected


def build(index,families,daily,check=lambda:None):
    rows={};family_for={}
    for group,payload in sorted(families.items()):
        if payload['analysisIdentity']!=index['analysisIdentity'] or payload['participantId']!=index['participantId']:raise ValueError('Review family scope differs')
        contexts={c['acquisitionGroupId']:c['acquisition'] for c in payload['contexts']}
        if any(c['participant_id']!=index['participantId'] for c in contexts.values()):raise ValueError('Review participant differs')
        for row in payload['rows']:
            if row['windowId'] in rows:raise ValueError('Duplicate review source window')
            rows[row['windowId']]={k:row[k] for k in ('windowId','acquisitionGroupId','date','startMs','endMsExclusive')}|{'acquisition':contexts[row['acquisitionGroupId']]}
            family_for[row['windowId']]=group
    reasons={};pools={'ordinary':list(rows.values()),'change':[],'recurrence':[]}
    for payload in daily.values():
        check()
        for cue in payload['changes']['retained']:
            for flank in ('before','after'):
                wid=cue[flank+'Example']
                if wid not in rows:continue
                if rows[wid]['date']!=payload['date'] or rows[wid]['acquisitionGroupId']!=payload['acquisitionGroupId']:raise ValueError('Review change context differs')
                reasons.setdefault((wid,'change'),[]).append({'kind':'change','flank':flank,'atMs':cue['atMs'],'score':cue['score'],'featureIndex':cue['featureIndex']})
    pools['change']=[rows[wid] for wid,kind in sorted(reasons) if kind=='change']
    references=diverse(pools['ordinary'],POLICY['recurrenceReferences']);queries=[]
    for ref in references:
        check();group=family_for[ref['windowId']];query=retrieve(families[group],ref['windowId'],POLICY['recurrenceView'])
        queries.append({'reference':ref['windowId'],'family':group,'status':query['status'],'counts':query['counts']})
        for result in query['items']:
            wid=result['windowId'];reasons.setdefault((wid,'recurrence'),[]).append({'kind':'recurrence','reference':ref['windowId'],'family':group,
                'scalerIdentity':families[group]['scaler']['identity'],'distance':result['distance'],'view':'with_power','counts':query['counts']})
    pools['recurrence']=[rows[wid] for wid,kind in sorted(reasons) if kind=='recurrence']
    selected={kind:diverse(pool,POLICY['perStream']) for kind,pool in pools.items()};ordered=[];by_id={}
    for i in range(POLICY['perStream']):
        for kind in POLICY['streams']:
            if i>=len(selected[kind]):continue
            row=selected[kind][i];wid=row['windowId']
            if wid not in by_id:
                by_id[wid]=dict(row,streams=[],reasons=[]);ordered.append(by_id[wid])
            if kind=='ordinary':by_id[wid]['ordinaryRank']=i
            by_id[wid]['streams'].append(kind)
            by_id[wid]['reasons'].extend([{'kind':'ordinary','selection':'Feature-independent window-ID hash within date/context strata'}] if kind=='ordinary' else reasons[(wid,kind)])
    value={'version':1,'participantId':index['participantId'],'analysisIdentity':index['analysisIdentity'],'sourceRevision':index['sourceRevision'],
        'dailyIdentity':index['dailyIdentity'],'recurrenceIdentity':index['recurrenceIdentity'],'timezone':index['timezone'],'policy':POLICY,
        'eligibleWindows':len(rows),'offeredWindows':len(ordered),'unofferedWindows':len(rows)-len(ordered),
        'streams':{kind:{'pool':len(pools[kind]),'selected':len(selected[kind]),'omitted':len(pools[kind])-len(selected[kind])} for kind in pools},
        'recurrenceQueries':queries,'items':ordered,'implementation':{name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in ('discovery_review_model.py','discovery_review_access.py')}}
    value['reviewIdentity']=fingerprint(value)
    if len(canonical_json(value).encode())>2_000_000:raise ValueError('Review snapshot exceeds bound')
    check();return value
