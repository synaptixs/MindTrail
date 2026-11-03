"""Stdlib-only scoped recurrence retrieval, condition pages and source inspection."""
from __future__ import annotations
import math
import re
from analysis_provenance import canonical_json, fingerprint
from similar_periods import distance

HEX=re.compile(r'[0-9a-f]{64}')
MAX_PAYLOAD=96_000_000
VIEWS=('with_power','spectral_only')


def validate_index(value,participant,identity):
    if (value.get('version')!=1 or value.get('participantId')!=participant or value.get('analysisIdentity')!=identity
            or value.get('recurrenceIdentity')!=fingerprint({k:v for k,v in value.items() if k!='recurrenceIdentity'})):
        raise ValueError('Invalid recurrence identity')
    return value


def read(manager,participant,group=None):
    from analysis_manager import read_json
    result={'status':'unavailable','participantId':participant}
    if not participant:return dict(result,status='no_participant')
    if group is not None and (not isinstance(group,str) or not HEX.fullmatch(group)):raise ValueError('Choose a valid recurrence family')
    with manager.lock:
        try:
            pointer=read_json(manager.root/(manager.scope(participant)+'.json'));signature=pointer['signature']
            if signature!=manager.signature():return dict(result,status='stale')
            directory=manager.directory(pointer['id']);identity=read_json(directory/'summary.json')['analysisIdentity']
            index=validate_index(read_json(directory/'recurrence-index.json'),participant,identity)
            if group is None:value=index
            else:
                entry=next((g for g in index['groups'] if g['compatibilityId']==group),None)
                if entry is None:raise ValueError('Family is outside current recurrence')
                value=read_json(directory/f'recurrence-{group}.json',limit=MAX_PAYLOAD)
                if (fingerprint(value)!=entry['payloadHash'] or value['version']!=1 or value['participantId']!=participant
                        or value['analysisIdentity']!=identity or value['compatibilityId']!=group
                        or value['acquisition']!=entry['acquisition'] or value['contexts']!=entry['contexts']
                        or value['scaler']['identity']!=entry['scalerIdentity']):raise ValueError('Invalid recurrence content')
            if signature!=manager.signature():return dict(result,status='stale')
            return dict(value,status='current',recurrenceIdentity=index['recurrenceIdentity'],dailyIdentity=index['dailyIdentity'],sourceSignature=signature)
        except (OSError,ValueError,KeyError,TypeError):return result


def integer(params,name,default,minimum,maximum):
    value=params.get(name,default)
    try:
        if isinstance(value,bool) or str(int(value))!=str(value) or not minimum<=int(value)<=maximum:raise ValueError()
        return int(value)
    except (ValueError,TypeError):raise ValueError('Invalid condition page') from None


def family_page(payload,params):
    offset=integer(params,'offset',0,0,1_000_000);limit=integer(params,'limit',25,1,100)
    rows=payload['conditions'];shown=rows[offset:offset+limit]
    return {k:v for k,v in payload.items() if k not in ('rows','conditions')}|{
        'conditions':shown,'offset':offset,'limit':limit,'shown':len(shown),'total':len(rows),
        'hasMore':offset+len(shown)<len(rows),'contact':'unmeasured',
        'knownSessions':len({c['acquisition']['session_id'] for c in payload['contexts'] if c['acquisition']['session_id'] is not None}),
        'unknownSessionContexts':sum(c['acquisition']['session_id'] is None for c in payload['contexts']),
        'conditionMeaning':'Descriptive day/exact-session strata. Shared feature associations are not independent evidence of contact or biology.'}


def retrieve(payload,reference,view):
    if view not in VIEWS:raise ValueError('Choose with power or spectral ratios only')
    if not isinstance(reference,str) or not HEX.fullmatch(reference):raise ValueError('Choose a valid observed reference window')
    ref=next((r for r in payload['rows'] if r['windowId']==reference),None)
    if ref is None:raise ValueError('Reference is outside eligible unflagged windows in this family')
    start=0 if view=='with_power' else 1
    features=payload['features'][start:];scales=payload['scaler']['iqrScale'][start:]
    refvalues=ref['vector'][start:];ranked=[];same=guarded=0
    for candidate in payload['rows']:
        if candidate['date']==ref['date']:same+=1;continue
        if candidate['startMs']<ref['endMsExclusive']+payload['policy']['guardMs'] and ref['startMs']-payload['policy']['guardMs']<candidate['endMsExclusive']:
            guarded+=1;continue
        score=math.sqrt(math.fsum(((b-a)/s)**2 for a,b,s in zip(refvalues,candidate['vector'][start:],scales)))
        ranked.append((score,candidate))
    contexts={c['acquisitionGroupId']:c['acquisition'] for c in payload['contexts']}
    result={'status':'ranked_candidates','view':view,'reference':dict(ref,acquisition=contexts[ref['acquisitionGroupId']]),
        'metric':payload['policy']['metric'],'features':features,'scaler':payload['scaler'],
        'counts':{'eligiblePopulation':len(payload['rows']),'sameDayExcluded':same,'temporalGuardExcluded':guarded,
            'eligibleOtherWindows':len(ranked),'otherDates':len({r['date'] for _,r in ranked}),'shown':0,'omittedDates':0},
        'items':[],'matchEvidence':'not_established','supportNote':'Dates are not independent replication; no activity labels or similarity probabilities are inferred.'}
    if not ranked:return dict(result,status='no_other_day_support')
    if not any(r>payload['policy']['varyingRangeFloor'] for r in payload['scaler']['ranges'][start:]):
        return dict(result,status='uninformative_features',supportNote='This view has no feature variation above the declared numerical floor. Zero distances cannot establish recurrence.')
    by_date={}
    for score,row in ranked:
        key=(score,row['startMs'],row['windowId'])
        if row['date'] not in by_date or key<by_date[row['date']][0]:by_date[row['date']]=(key,row)
    ordered=sorted(by_date.values(),key=lambda pair:pair[0]);items=[]
    for _,row in ordered[:payload['policy']['resultDateLimit']]:
        total,differences=distance(refvalues,row['vector'][start:],features,scales)
        items.append(dict(row,distance=total,differences=differences,acquisition=contexts[row['acquisitionGroupId']]))
    values=sorted(v for v,_ in ranked)
    def q(f):
        pos=(len(values)-1)*f;lo=int(pos);hi=min(lo+1,len(values)-1)
        return values[lo]+(values[hi]-values[lo])*(pos-lo)
    result.update(items=items,distanceQuartiles=[q(.25),q(.5),q(.75)])
    result['counts'].update(shown=len(items),omittedDates=len(ordered)-len(items))
    if len(ordered)==1:result['supportNote']='Only one other date is available. These are exploratory candidates, not replication or task repeatability.'
    return result


def page(manager,participant,params):
    payload=read(manager,participant,params.get('group'))
    if payload['status']!='current' or params.get('group') is None:return payload
    identity=params.get('identity')
    if identity is not None and identity!=payload['recurrenceIdentity']:raise ValueError('Recurrence changed. Refresh before searching.')
    result=family_page(payload,params)
    if params.get('reference') is not None:
        if identity is None:raise ValueError('Current recurrence identity is required')
        result['query']=retrieve(payload,params['reference'],params.get('view','with_power'))
    if payload['sourceSignature']!=manager.signature():return {'status':'stale','participantId':participant}
    if len(canonical_json(result).encode('utf-8'))>2_000_000:raise ValueError('Recurrence response exceeds output bound')
    return result


def waveform(manager,participant,params):
    payload=read(manager,participant,params.get('group'))
    if payload['status']!='current' or 'rows' not in payload or payload['recurrenceIdentity']!=params.get('identity'):
        raise ValueError('Recurrence is stale or unavailable')
    row=next((r for r in payload['rows'] if r['windowId']==params.get('window')),None)
    if row is None:raise ValueError('Window is outside the eligible recurrence population')
    from discovery_daily_access import waveform as exact_daily_waveform
    value=exact_daily_waveform(manager,participant,{'group':row['acquisitionGroupId'],'date':row['date'],
        'window':row['windowId'],'identity':payload['dailyIdentity']})
    if payload['sourceSignature']!=manager.signature():raise ValueError('Source changed. Refresh analysis.')
    return value


def validate_files(directory,participant,identity):
    from analysis_manager import read_json
    index=validate_index(read_json(directory/'recurrence-index.json'),participant,identity)
    for g in index['groups']:
        key=g['compatibilityId']
        if not isinstance(key,str) or not HEX.fullmatch(key):raise ValueError('Invalid recurrence family')
        value=read_json(directory/f'recurrence-{key}.json',limit=MAX_PAYLOAD)
        if (fingerprint(value)!=g['payloadHash'] or value['version']!=1 or value['participantId']!=participant
                or value['analysisIdentity']!=identity or value['compatibilityId']!=key
                or value['acquisition']!=g['acquisition'] or value['contexts']!=g['contexts']):raise ValueError('Incomplete recurrence payload')
