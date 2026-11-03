"""Canonical daily discovery facts and range summaries; no eager numerical imports."""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime, time, timedelta
import hashlib
from pathlib import Path
from zoneinfo import ZoneInfo
from analysis_provenance import canonical_json, fingerprint
from daily_review import partition

VERSION = 1
MAX_DAY_BYTES = 16_000_000
FEATURES = [
    {'key':'acRms','label':'AC RMS','units':'source units'},
    {'key':'log10Power1to40Hz','label':'Log power 1–40 Hz','units':'log10 source units squared'},
    {'key':'alphaShare','label':'Alpha share','units':'fraction of 1–40 Hz power'}]
POLICY = {'flankWindows':15,'windowMs':4000,'threshold':3.,'suppressionMs':120000,
          'rmsRelativeFloor':.05,'logPowerFloor':.05,'alphaShareFloor':.02,'bucketMs':60000,
          'quartiles':'linear; whole unflagged windows; no daily normalization',
          'meaning':'Exploratory feature-change cues, not statistical tests or activity boundaries'}


def quantiles(rows):
    if not rows: return None
    result=[]
    for column in zip(*(r['featureValues'] for r in rows)):
        values=sorted(column);q=[]
        for fraction in (.25,.5,.75):
            position=(len(values)-1)*fraction;lo=int(position);hi=min(lo+1,len(values)-1)
            q.append(values[lo]+(values[hi]-values[lo])*(position-lo))
        result.append(q)
    return result


def day_bounds(date,zone):
    value=datetime.strptime(date,'%Y-%m-%d').date()
    return (int(datetime.combine(value,time(),zone).timestamp()*1000),
            int(datetime.combine(value+timedelta(days=1),time(),zone).timestamp()*1000))


def parts(start,end,zone):
    while start<end:
        date=datetime.fromtimestamp(start/1000,zone).date().isoformat()
        stop=min(end,day_bounds(date,zone)[1])
        if stop<=start: raise ValueError('Invalid daily calendar boundary')
        yield date,start,stop
        start=stop


def continuous(rows):
    runs=[]
    for row in sorted(rows,key=lambda w:(w['startMs'],w['windowId'])):
        if row['qualityFlags']: continue
        if not runs or runs[-1][-1]['endMsExclusive']!=row['startMs']: runs.append([])
        runs[-1].append(row)
    return runs


def change_cues(rows,check=lambda:None):
    candidates=[];evaluated=0
    for run in continuous(rows):
        n=POLICY['flankWindows']
        for i in range(n,len(run)-n+1):
            if evaluated%256==0: check()
            before,after=run[i-n:i],run[i:i+n]
            if any(w['endMsExclusive']-w['startMs']!=POLICY['windowMs'] for w in [*before,*after]):continue
            evaluated+=1;bq,aq=quantiles(before),quantiles(after)
            floors=[POLICY['rmsRelativeFloor']*max(abs(bq[0][1]),abs(aq[0][1]),1),POLICY['logPowerFloor'],POLICY['alphaShareFloor']]
            scales=[max(b[2]-b[0],a[2]-a[0],floor) for b,a,floor in zip(bq,aq,floors)]
            scores=[abs(a[1]-b[1])/scale for a,b,scale in zip(aq,bq,scales)]
            feature=max(range(len(scores)),key=lambda j:(scores[j],-j))
            if scores[feature]<POLICY['threshold']:continue
            def observed(flank,median):return min(flank,key=lambda w:(abs(w['featureValues'][feature]-median),w['windowId']))['windowId']
            candidates.append({'atMs':after[0]['startMs'],'startMs':before[0]['startMs'],
                'endMsExclusive':after[-1]['endMsExclusive'],'featureIndex':feature,'scores':scores,
                'score':scores[feature],'beforeQuartiles':bq,'afterQuartiles':aq,'scales':scales,
                'beforeExample':observed(before,bq[feature][1]),'afterExample':observed(after,aq[feature][1])})
    kept=[]
    for candidate in sorted(candidates,key=lambda c:(-c['score'],c['atMs'],c['afterExample'])):
        if all(abs(candidate['atMs']-other['atMs'])>=POLICY['suppressionMs'] for other in kept):kept.append(candidate)
    return {'evaluatedBoundaries':evaluated,'aboveThreshold':len(candidates),'suppressed':len(candidates)-len(kept),
        'retained':kept,'status':'insufficient_continuous_support' if not evaluated else 'changes_found' if kept else 'no_changes_above_threshold'}


def quality_union(spans,check=lambda:None):
    events=defaultdict(list)
    for start,end,state in spans:
        events[start].append((state,1));events[end].append((state,-1))
    counts=defaultdict(int);result=[];previous=None
    priority=['excluded','feature_failure','remainder','computed_flagged','computed_unflagged']
    for i,point in enumerate(sorted(events)):
        if i%1024==0:check()
        state=next((s for s in priority if counts[s]),None)
        if previous is not None and point>previous and state:
            if result and result[-1]['endMsExclusive']==previous and result[-1]['state']==state:result[-1]['endMsExclusive']=point
            else:result.append({'startMs':previous,'endMsExclusive':point,'state':state})
        for name,delta in events[point]:counts[name]+=delta
        previous=point
    return result


def build(report,database,participant,zone_name,discovery_identity,*,check=lambda:None):
    # Annotation ownership stays in its existing worker module; HTTP summaries below
    # never import that numerical module or query source/annotation data again.
    from activity_comparisons import annotations,segments
    labels,unknown,annotation=annotations(database,participant)
    zone=ZoneInfo(zone_name);days={};contexts={};cross_day=0
    def include(acq,start,end,state):
        if start is None or end is None:return []
        if acq['participant_id']!=participant:raise ValueError('Daily acquisition participant mismatch')
        group=fingerprint(acq);contexts[group]=acq
        if len(contexts)>1000:raise ValueError('Daily discovery exceeds context bound')
        keys=[]
        for date,a,b in parts(start,end,zone):
            key=(group,date);keys.append(key)
            day=days.setdefault(key,{'spans':[],'windows':[],'boundaryWindows':0})
            day['spans'].append((a,b,state))
        if len(days)>20000:raise ValueError('Daily discovery exceeds day bound')
        return keys
    for i,w in enumerate(report['windows']):
        if i%256==0:check()
        keys=include(w['acquisition'],w['startMs'],w['endMsExclusive'],'computed_flagged' if w['qualityFlags'] else 'computed_unflagged')
        if len(keys)!=1:
            cross_day+=1
            for key in keys:days[key]['boundaryWindows']+=1
        else:
            days[keys[0]]['windows'].append({k:w[k] for k in ('windowId','startMs','endMsExclusive','sourceRecordDigests','qualityFlags')}|
                {'featureValues':[w['features']['acRms'],w['features']['log10Power1to40Hz'],w['features']['bandShares'][2]]})
    for failure in report['failedWindows']:
        w=failure['provenance'];include(w['acquisition'],w['startMs'],w['endMsExclusive'],'feature_failure')
    for field,state in [('excludedSpans','excluded'),('remainderSpans','remainder')]:
        for span in report[field]:include(span['acquisition'],span['startMs'],span['endMsExclusive'],state)
    index={'version':VERSION,'participantId':participant,'analysisIdentity':report['analysisIdentity'],
        'discoveryIdentity':discovery_identity,'sourceRevision':report['sourceInventory']['sourceRevision'],
        'methodIdentity':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'timezone':zone_name,
        'features':FEATURES,'policy':POLICY,'annotation':annotation,'contexts':[],
        'sourceCoverage':report['coverage'],'unplacedExcludedRecords':sum(s['recordCount'] for s in report['excludedSpans'] if s['startMs'] is None),
        'windowAccounting':{'computed':len(report['windows']),'fullyContained':sum(len(d['windows']) for d in days.values()),'crossDay':cross_day}}
    payloads={}
    for group in sorted(contexts):
        entry={'acquisitionGroupId':group,'acquisition':contexts[group],'days':[]}
        for (gid,date),raw in sorted(days.items()):
            if gid!=group:continue
            check();runs=quality_union(raw['spans'],check);start=runs[0]['startMs'];end=runs[-1]['endMsExclusive']
            windows=sorted(raw['windows'],key=lambda w:(w['startMs'],w['windowId']));usable=[w for w in windows if not w['qualityFlags']]
            lo,hi=day_bounds(date,zone)
            human=[{'startMs':s['start'],'endMsExclusive':s['end'],'state':s['kind'],
                'label':s['label']} for s in segments(labels,unknown,start,end)]
            payload={'version':VERSION,'participantId':participant,'analysisIdentity':report['analysisIdentity'],
                'sourceRevision':index['sourceRevision'],'discoveryIdentity':discovery_identity,
                'acquisitionGroupId':group,'acquisition':contexts[group],'date':date,'timezone':zone_name,
                'calendarStartMs':lo,'calendarEndMsExclusive':hi,'startMs':start,'endMsExclusive':end,
                'quality':runs,'humanContext':human,'windows':windows,'dayQuartiles':quantiles(usable),
                'dayBoundaryWindowsTouching':raw['boundaryWindows'],'changes':change_cues(windows,check)}
            if len(canonical_json(payload).encode('utf-8'))>MAX_DAY_BYTES:raise ValueError('Daily discovery exceeds payload bound')
            payloads[(group,date)]=payload
            entry['days'].append({'date':date,'startMs':start,'endMsExclusive':end,'payloadHash':fingerprint(payload)})
        index['contexts'].append(entry)
    index['dailyIdentity']=fingerprint(index)
    if len(canonical_json(index).encode('utf-8'))>2_000_000:raise ValueError('Daily discovery exceeds manifest bound')
    check();return index,payloads


def selection(payload,start=None,end=None):
    start=payload['startMs'] if start is None else start;end=payload['endMsExclusive'] if end is None else end
    if (type(start) is not int or type(end) is not int or not payload['calendarStartMs']<=start<end<=payload['calendarEndMsExclusive']):
        raise ValueError('Choose an explicit range within the selected calendar day')
    touching=[w for w in payload['windows'] if w['startMs']<end and w['endMsExclusive']>start]
    whole=[w for w in touching if start<=w['startMs'] and w['endMsExclusive']<=end]
    usable=[w for w in whole if not w['qualityFlags']];flagged=[w for w in whole if w['qualityFlags']]
    points=[]
    for run_index,run in enumerate(continuous(whole)):
        buckets=defaultdict(list)
        for row in run:buckets[row['startMs']//POLICY['bucketMs']].append(row)
        for _,rows in sorted(buckets.items()):
            q=quantiles(rows);example=min(rows,key=lambda w:(abs(w['featureValues'][0]-q[0][1]),w['windowId']))
            points.append({'startMs':rows[0]['startMs'],'endMsExclusive':rows[-1]['endMsExclusive'],
                'count':len(rows),'quartiles':q,'run':run_index,'exampleId':example['windowId']})
    quality=partition(payload['quality'],start,end,'missing')
    human=[]
    for item in payload['humanContext']:
        a,b=max(start,item['startMs']),min(end,item['endMsExclusive'])
        if a<b:human.append(dict(item,startMs=a,endMsExclusive=b))
    cues=[c for c in payload['changes']['retained'] if c['startMs']>=start and c['endMsExclusive']<=end]
    cues.sort(key=lambda c:(-c['score'],c['atMs'],c['afterExample']))
    def example(rows):return min(rows,key=lambda w:w['windowId'])['windowId'] if rows else None
    counts=defaultdict(int)
    for run in quality:counts[run['state']]+=run['endMsExclusive']-run['startMs']
    result={k:v for k,v in payload.items() if k not in ('windows','quality','humanContext','changes')}|{
        'viewStartMs':start,'viewEndMsExclusive':end,'points':points,'quality':quality,'humanContext':human,
        'selectionQuartiles':quantiles(usable),'support':{'wholeWindows':len(whole),'unflaggedWindows':len(usable),
            'flaggedWindows':len(flagged),'selectionBoundaryWindows':len(touching)-len(whole),'elapsedMsByState':dict(counts)},
        'examples':{'ordinary':example(usable),'flagged':example(flagged)},
        'changes':{'status':payload['changes']['status'],'evaluatedBoundaries':payload['changes']['evaluatedBoundaries'],
            'aboveThreshold':payload['changes']['aboveThreshold'],'suppressed':payload['changes']['suppressed'],
            'dayRetained':len(payload['changes']['retained']),'rangeRetained':len(cues),'shown':min(20,len(cues)),
            'items':cues[:20],'statusScope':'Change screening status is for the full observed day; rangeRetained requires both full flanks inside the selection.'}}
    if len(canonical_json(result).encode('utf-8'))>MAX_DAY_BYTES:raise ValueError('Daily selection exceeds output bound')
    return result
