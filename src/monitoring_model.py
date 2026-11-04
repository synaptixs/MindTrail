"""Descriptive aligned version differences; never session inference or statistical alerts."""
from __future__ import annotations
import hashlib
from pathlib import Path
from analysis_provenance import fingerprint


def compare(before,after,*,offset=0,limit=20):
    if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=100:raise ValueError('Invalid comparison page')
    result={'status':'baseline_missing' if before is None else 'selected_missing' if after is None else 'descriptive',
        'contexts':{},'rows':[],'shown':0,'total':0,'omitted':0,'offset':offset,'hasMore':False,'rowTotals':{},'coverage':[],
        'methodIdentity':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'limitations':['Net counts do not identify newly arrived records or prove append-only collection.',
            'Revised day/context summaries can reflect edited/deleted records, extended coverage or refitted relative screening.',
            'Median changes describe different observed populations, not matched-window effects, significance or biological drift.',
            'Sessions are compared only by explicit recorded IDs; gaps never imply sessions. Unflagged is not established artifact-free.']}
    if before is None or after is None:return result
    a,b=before['header'],after['header']
    if not a['comparisonKey'] or a['comparisonKey']!=b['comparisonKey']:
        return dict(result,status='incompatible',reason='Analysis method/runtime, quality policy, normalization, timezone or requested range differ or are unknown. No deltas computed.')
    old,new=before['numerical'],after['numerical']
    if old['participantId']!=new['participantId']:raise ValueError('Monitoring participant scope differs')
    result['sourceRelation']='same_source_revision' if old['sourceRevision']==new['sourceRevision'] else 'different_source_revision'
    result['coverage']=[{'metric':key,'before':a['coverage'][key],'after':b['coverage'][key],'netDelta':b['coverage'][key]-a['coverage'][key]} for key in a['coverage']]
    contexts={}
    for value in (old,new):
        for family in value['families']:
            for context in family['contexts']:contexts[context['acquisitionGroupId']]=context['acquisition']
    def rows(value):
        data={}
        for family in value['families']:
            for row in family['conditions']:
                key=(row['acquisitionGroupId'],row['date'])
                if key in data:raise ValueError('Duplicate monitoring day/context row')
                data[key]={'family':family['compatibilityId'],'condition':row,'features':family['proxies'][:3]}
        return data
    left,right=rows(old),rows(new);changes=[];totals={k:0 for k in ('added','removed','revised','unchanged')}
    for key in sorted(set(left)|set(right)):
        x,y=left.get(key),right.get(key);kind='added' if x is None else 'removed' if y is None else 'unchanged' if x==y else 'revised';totals[kind]+=1
        delta=[]
        if x and y and x['condition']['proxyQuartiles'] is not None and y['condition']['proxyQuartiles'] is not None:
            for i,feature in enumerate(y['features']):
                u,v=x['condition']['proxyQuartiles'][i][1],y['condition']['proxyQuartiles'][i][1]
                delta.append({'feature':feature['label'],'units':feature['units'],'beforeMedian':u,'afterMedian':v,'medianDelta':v-u})
        changes.append({'date':key[1],'acquisitionGroupId':key[0],'kind':kind,'before':x,'after':y,'features':delta})
    changes.sort(key=lambda r:({'revised':0,'added':1,'removed':2,'unchanged':3}[r['kind']],r['date'],r['acquisitionGroupId']))
    result.update(rowTotals=totals,total=len(changes),rows=changes[offset:offset+limit])
    result['contexts']={r['acquisitionGroupId']:contexts[r['acquisitionGroupId']] for r in result['rows']}
    result['shown']=len(result['rows']);result['omitted']=result['total']-result['shown'];result['hasMore']=offset+result['shown']<result['total']
    def sessions(value):return {c['acquisition']['session_id'] for g in value['families'] for c in g['contexts'] if c['acquisition'].get('session_id') is not None}
    old_sessions,new_sessions=sessions(old),sessions(new);added,removed=sorted(new_sessions-old_sessions),sorted(old_sessions-new_sessions)
    result['recordedSessionIds']={'added':added[:50],'addedTotal':len(added),'addedOmitted':max(0,len(added)-50),'removed':removed[:50],'removedTotal':len(removed),'removedOmitted':max(0,len(removed)-50),'meaning':'Explicit recorded identifiers only; no inferred sessions or independent repetitions'}
    result['comparisonIdentity']=fingerprint({'before':a['version'],'after':b['version'],'facts':result});return result
