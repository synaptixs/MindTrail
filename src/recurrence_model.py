"""Worker-owned full-population recurrence coordinates and recording-condition facts."""
from __future__ import annotations
from collections import defaultdict
import hashlib
from pathlib import Path
import numpy as np
from analysis_provenance import canonical_json, fingerprint
from discovery_daily import quantiles
from spectral_features import FEATURES, feature_vector

VERSION=1
MAX_PAYLOAD=96_000_000
PROXIES=[{'key':'acRms','label':'AC RMS','units':'source units'},
 {'key':'log10Power1to40Hz','label':'Log power 1–40 Hz','units':'log10 source units squared'},
 {'key':'alphaShare','label':'Alpha share','units':'fraction of 1–40 Hz power'},
 {'key':'maxStep','label':'Maximum step','units':'source units'},
 {'key':'linearTrendFraction','label':'Trend fraction','units':'fraction'},
 {'key':'mainsLikePowerFraction','label':'Mains-like power fraction','units':'fraction'}]
POLICY={'scaleFloor':1e-6,'varyingRangeFloor':1e-6,'guardMs':60000,'resultDateLimit':10,
 'referenceExamplesPerDate':3,'population':'All whole-day unflagged canonical windows; no label filter or clustering sample',
 'metric':'Euclidean / full eligible-family snapshot IQR; full spectral feature space',
 'datePolicy':'Other dates only, with temporal guard; one minimum-distance candidate per date',
 'interpretation':'Ranked feature candidates only; no validated match threshold or independent replication',
 'contact':'unmeasured','sessionPolicy':'Explicit sessions retained; dates/gaps never inferred as sessions'}


def proxy_values(w):
    f=w['features']
    return [f['acRms'],f['log10Power1to40Hz'],f['bandShares'][2],f['maxStep'],f['linearTrendFraction'],f['mainsLikePowerFraction']]


def build(report,daily_index,daily_payloads,participant,check=lambda:None):
    if daily_index['analysisIdentity']!=report['analysisIdentity'] or daily_index['participantId']!=participant:
        raise ValueError('Recurrence daily source differs')
    if len(report['windows'])>1_000_000:raise ValueError('Recurrence exceeds input bound')
    originals={w['windowId']:w for w in report['windows']}
    if len(originals)!=len(report['windows']):raise ValueError('Recurrence has duplicate source windows')
    families={}
    for group in daily_index['contexts']:
        acq=group['acquisition']
        if acq['participant_id']!=participant:raise ValueError('Recurrence participant mismatch')
        compatible={k:v for k,v in acq.items() if k!='session_id'};key=fingerprint(compatible)
        family=families.setdefault(key,{'compatibilityId':key,'acquisition':compatible,'contexts':[],
            'rows':[],'conditions':[],'accounting':{'computedContained':0,'flaggedExcluded':0,'eligible':0,'dayBoundaryWindowsTouching':0}})
        if len(families)>1000:raise ValueError('Recurrence exceeds family bound')
        family['contexts'].append({'acquisitionGroupId':group['acquisitionGroupId'],'acquisition':acq})
        for day in group['days']:
            check();payload=daily_payloads[(group['acquisitionGroupId'],day['date'])]
            unflagged=[];flagged=[]
            for i,point in enumerate(payload['windows']):
                if i%512==0:check()
                w=originals[point['windowId']]
                if w['acquisitionGroupId']!=group['acquisitionGroupId']:raise ValueError('Recurrence exact context differs')
                values=proxy_values(w);family['accounting']['computedContained']+=1
                if w['qualityFlags']:
                    family['accounting']['flaggedExcluded']+=1;flagged.append({'featureValues':values});continue
                vector=feature_vector(w)
                if not np.isfinite(vector).all():raise ValueError('Recurrence has nonfinite coordinates')
                row={k:w[k] for k in ('windowId','startMs','endMsExclusive','acquisitionGroupId')}
                row.update(date=day['date'],vector=vector,proxies=values)
                family['rows'].append(row);family['accounting']['eligible']+=1;unflagged.append({'featureValues':values})
            elapsed=defaultdict(int)
            # W2 supplies exact disjoint quality runs. Its missing intervals are filled
            # inside the observed extent, never assumed wearing time.
            recorded=sum(r['endMsExclusive']-r['startMs'] for r in payload['quality'])
            for r in payload['quality']:elapsed[r['state']]+=r['endMsExclusive']-r['startMs']
            family['accounting']['dayBoundaryWindowsTouching']+=payload['dayBoundaryWindowsTouching']
            family['conditions'].append({'date':day['date'],'acquisitionGroupId':group['acquisitionGroupId'],
                'sessionId':acq['session_id'],'startMs':payload['startMs'],'endMsExclusive':payload['endMsExclusive'],
                'recordedUnionMs':recorded,'missingMs':payload['endMsExclusive']-payload['startMs']-recorded,
                'qualityMs':dict(elapsed),'unflaggedWindows':len(unflagged),'flaggedWindows':len(flagged),
                'proxyQuartiles':quantiles(unflagged),'flaggedProxyQuartiles':quantiles(flagged)})
    index={'version':VERSION,'participantId':participant,'analysisIdentity':report['analysisIdentity'],
        'sourceRevision':report['sourceInventory']['sourceRevision'],'dailyIdentity':daily_index['dailyIdentity'],
        'timezone':daily_index['timezone'],'policy':POLICY,'features':FEATURES,'proxies':PROXIES,
        'implementation':{name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in ('recurrence_model.py','recurrence_access.py','spectral_features.py','similar_periods.py')},
        'sourceCoverage':report['coverage'],'windowAccounting':daily_index['windowAccounting'],'groups':[]}
    outputs={}
    for key,p in sorted(families.items()):
        check();p['rows'].sort(key=lambda r:(r['startMs'],r['windowId']))
        p['conditions'].sort(key=lambda r:(r['date'],r['acquisitionGroupId']))
        rows=p['rows'];raw=np.asarray([r['vector'] for r in rows],dtype=float)
        if rows:
            q=np.percentile(raw,[25,50,75],axis=0,method='linear');ranges=np.ptp(raw,axis=0)
            median=q[1].tolist();iqr=(q[2]-q[0]).tolist();scales=np.maximum(q[2]-q[0],POLICY['scaleFloor']).tolist()
        else:median=[0.]*5;iqr=[0.]*5;scales=[POLICY['scaleFloor']]*5;ranges=np.zeros(5)
        scaler={'features':FEATURES,'median':median,'iqr':iqr,'iqrScale':scales,'ranges':ranges.tolist(),
            'fitWindows':len(rows),'populationRevision':fingerprint([r['windowId'] for r in rows]),
            'scope':'Full eligible family snapshot, including discovery dates; not held-out preprocessing'}
        scaler['identity']=fingerprint(scaler);p['scaler']=scaler;p['referenceDays']=[]
        by_date=defaultdict(list)
        for r in rows:by_date[r['date']].append(r)
        for date,day_rows in sorted(by_date.items()):
            rms=float(np.median([r['proxies'][0] for r in day_rows]));ordinary=min(day_rows,key=lambda r:r['windowId'])
            middle=min(day_rows,key=lambda r:(abs(r['proxies'][0]-rms),r['windowId']))
            examples=[];seen=set()
            for kind,r in [('ordinary hash-selected',ordinary),('RMS median example',middle),('earliest observed',day_rows[0])]:
                if r['windowId'] in seen:continue
                seen.add(r['windowId']);examples.append({k:r[k] for k in ('windowId','startMs','endMsExclusive','acquisitionGroupId')}|{'kind':kind})
            p['referenceDays'].append({'date':date,'eligibleWindows':len(day_rows),'examples':examples})
        p.update(version=VERSION,participantId=participant,analysisIdentity=index['analysisIdentity'],
            sourceRevision=index['sourceRevision'],timezone=index['timezone'],policy=POLICY,features=FEATURES,proxies=PROXIES)
        if len(canonical_json(p).encode('utf-8'))>MAX_PAYLOAD:raise ValueError('Recurrence family exceeds payload bound; narrow analysis range')
        outputs[key]=p
        index['groups'].append({k:p[k] for k in ('compatibilityId','acquisition','contexts','accounting','referenceDays')}|
            {'payloadHash':fingerprint(p),'scalerIdentity':scaler['identity'],'conditionRows':len(p['conditions'])})
    index['recurrenceIdentity']=fingerprint(index)
    if len(canonical_json(index).encode('utf-8'))>2_000_000:raise ValueError('Recurrence manifest exceeds output bound')
    check();return index,outputs
