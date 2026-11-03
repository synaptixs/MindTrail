"""Stdlib-only scoped/paged comparison facts and exact example waveform access."""
from __future__ import annotations
import re
from analysis_provenance import fingerprint
from exploration_access import verified_waveform

HEX=re.compile(r'^[0-9a-f]{64}$')


def read(manager,participant,group=None):
    from analysis_manager import read_json
    result={'status':'unavailable','participantId':participant}
    if not participant:return result
    if group is not None and (not isinstance(group,str) or not HEX.fullmatch(group)):raise ValueError('Choose a valid recording context')
    with manager.lock:
        try:
            pointer=read_json(manager.root/(manager.scope(participant)+'.json'))
            if pointer['signature']!=manager.signature():return dict(result,status='stale')
            if not pointer['settings'].get('activityComparisons'):return dict(result,status='disabled')
            directory=manager.directory(pointer['id']);index=read_json(directory/'activities-index.json')
            if index['version']!=1 or index['participantId']!=participant or index['analysisIdentity']!=read_json(directory/'summary.json')['analysisIdentity']:raise ValueError('Invalid comparison identity')
            if group is None:value=index
            else:
                entry=next((g for g in index['groups'] if g['compatibilityId']==group),None)
                if entry is None:raise ValueError('Context is outside the current comparison')
                value=read_json(directory/f'activities-{group}.json',limit=8_000_000)
                if (fingerprint(value)!=entry['payloadHash'] or value['version']!=1 or value['participantId']!=participant
                    or value['compatibilityId']!=group or value['acquisition']!=entry['acquisition']
                    or value['comparisonIdentity']!=index['comparisonIdentity'] or value['analysisIdentity']!=index['analysisIdentity']):raise ValueError('Invalid comparison content')
            if pointer['signature']!=manager.signature():return dict(result,status='stale')
            return dict(value,status='current',sourceSignature=pointer['signature'])
        except (OSError,ValueError,TypeError,KeyError):return result


def page(manager,participant,params):
    group=params.get('group');data=read(manager,participant,group)
    if data['status']!='current' or group is None:return data
    state=params.get('review','confirmed');origin=params.get('origin','all');activity=params.get('activity','')
    if state not in ('confirmed','uncertain','needs_review'):raise ValueError('Choose a review policy')
    if origin not in ('all','manual','schedule','model','other'):raise ValueError('Choose an origin policy')
    try:offset=int(params.get('offset',0));limit=int(params.get('limit',25))
    except (ValueError,TypeError):raise ValueError('Invalid comparison page') from None
    if offset<0 or not 1<=limit<=100:raise ValueError('Invalid comparison page')
    selected=[s for s in data['strata'] if s['reviewState']==state
              and (origin=='all' or s['source']==origin or origin=='other' and s['source'] not in ('manual','schedule','model'))
              and (not activity or s['activityId']==activity)]
    ids={s['stratumId'] for s in selected};all_rows=[r for r in data['days'] if r['stratumId'] in ids]
    rows=all_rows[offset:offset+limit];visible={r['stratumId'] for r in rows}
    known=[s for s in selected if s['sessionId'] is not None]
    activities={s['activityId']:s['activityName'] for s in data['strata']}
    names={s['activityId'] for s in selected if s['usableWindows']}
    days=set(r['date'] for r in all_rows if r['usableWindows'])
    by_activity={a:set(r['date'] for r in all_rows if r['usableWindows'] and any(s['stratumId']==r['stratumId'] and s['activityId']==a for s in selected)) for a in names}
    reasons=[]
    if len(names)<2:reasons.append('Fewer than two activities have usable windows; no activity contrast is available.')
    if any(len(dates)<2 for dates in by_activity.values()):reasons.append('Some activities have usable windows on fewer than two dates. Windows do not substitute for independent days.')
    if len(names)>1 and not set.intersection(*by_activity.values()):reasons.append('No date has usable data for every selected activity; activity and day may be confounded.')
    if any(s['sessionId'] is None and s['usableWindows'] for s in selected):reasons.append('Record-linked session IDs are missing for some support. Session effects cannot be evaluated there.')
    if any(s['source']=='model' for s in selected):reasons.append('Model-origin labels are included explicitly; reviewed retrieval suggestions are not blind independent validation.')
    if any(s['timingCounts'].get('unknown',0) or s['timingCounts'].get('approximate',0) for s in selected):reasons.append('Activity timing is unknown or approximate for some labels; entered boundaries are not verified EEG alignment.')
    if not rows and not all_rows:reasons.append('No labeled periods under this policy. Choose another policy or review activities, then rerun analysis.')
    return {k:v for k,v in data.items() if k not in ('days','strata')}|{
        'policy':{'review':state,'origin':origin,'activity':activity},'activities':[{'id':key,'name':name} for key,name in sorted(activities.items(),key=lambda x:(x[1],x[0]))],
        'strata':[s for s in selected if s['stratumId'] in visible],'rows':rows,'offset':offset,'limit':limit,'total':len(all_rows),'shown':len(rows),'hasMore':offset+len(rows)<len(all_rows),
        'sessions':known[:25],'sessionRowsTotal':len(known),'sessionRowsShown':min(25,len(known)),
        'support':{'activitiesWithUsableWindows':len(names),'usableDays':len(days),'knownSessions':len({s['sessionId'] for s in selected if s['sessionId'] is not None and s['usableWindows']}),
                   'usableWindows':sum(s['usableWindows'] for s in selected),'usableMs':sum(s['usableMs'] for s in selected),'unknownSessionWindows':sum(s['usableWindows'] for s in selected if s['sessionId'] is None)},
        'readiness':{'status':'descriptive_only','reasons':reasons,'decision':'No statistical effect, activity classifier or default clustering method is approved by these comparisons.'}}


def waveform(manager,participant,group,window,identity):
    if not isinstance(window,str) or not HEX.fullmatch(window):raise ValueError('Choose a valid observed example')
    payload=read(manager,participant,group)
    if payload['status']!='current' or payload['comparisonIdentity']!=identity:raise ValueError('Comparison is stale or unavailable; refresh analysis')
    example=next((e for s in payload['strata'] for e in s['examples'] if e['windowId']==window),None)
    if example is None:raise ValueError('Example is outside the selected comparison')
    result=verified_waveform(manager.database,participant,example['acquisition'],example)
    if payload['sourceSignature']!=manager.signature():raise ValueError('Source changed; refresh analysis')
    return result
