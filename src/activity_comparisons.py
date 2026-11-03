"""Worker-owned full-population descriptive activity facts. No archive writes."""
from __future__ import annotations
from bisect import bisect_right
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, time, timedelta, timezone as utc_timezone
import hashlib
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo
import numpy as np
from analysis_provenance import fingerprint

VERSION=1
FEATURES=[{'key':'acRms','label':'AC RMS','units':'source units'},
          {'key':'log10Power1to40Hz','label':'Log power 1–40 Hz','units':'log10 source units²'},
          {'key':'alphaShare','label':'Alpha share','units':'fraction of 1–40 Hz power'}]


def annotations(database, participant):
    with closing(sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True)) as c:
        c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'activities','labels'}<=tables:return [],[],{'status':'no_personal_activity_schema','revision':fingerprint([])}
        definitions=[dict(r) for r in c.execute('SELECT id,name,archived,version FROM activities WHERE participant_id=? ORDER BY id',(participant,))]
        names={r['id']:r['name'] for r in definitions}
        labels=[dict(r) for r in c.execute('SELECT id,activity_id,start_ms,end_ms,source,review_state,timing_precision,start_uncertainty_ms,end_uncertainty_ms FROM labels WHERE participant_id=? ORDER BY start_ms,id',(participant,))]
        unknown=[dict(r) for r in c.execute("SELECT id,start_ms,end_ms FROM episode_decisions WHERE participant_id=? AND kind='unknown' ORDER BY id",(participant,))] if 'episode_decisions' in tables else []
        revision=c.execute('SELECT COALESCE(MAX(event_id),0) FROM activity_label_history WHERE participant_id=?',(participant,)).fetchone()[0] if 'activity_label_history' in tables else 0
        meta={'status':'current','revision':fingerprint({'definitions':definitions,'labels':labels,'unknown':unknown,'historyRevision':revision}),
              'openOrInvalidLabels':sum(r['end_ms'] is None or r['end_ms']<=r['start_ms'] or r['activity_id'] not in names for r in labels)}
        return [{**r,'activityName':names[r['activity_id']]} for r in labels if r['end_ms'] is not None and r['end_ms']>r['start_ms'] and r['activity_id'] in names],unknown,meta


def union(intervals):
    result=[]
    for a,b in sorted(intervals):
        if b<=a:continue
        if result and a<=result[-1][1]:result[-1]=(result[-1][0],max(b,result[-1][1]))
        else:result.append((a,b))
    return result


def intersection_ms(intervals,a,b):
    return sum(max(0,min(b,y)-max(a,x)) for x,y in intervals if x<b and y>a)


def segments(labels,unknown,start,end):
    events=defaultdict(list)
    for kind,rows in [('label',labels),('unknown',unknown)]:
        for row in rows:
            a,b=max(start,row['start_ms']),min(end,row['end_ms'])
            if a<b:events[a].append((kind,row['id'],row));events[b].append((kind,row['id'],None))
    points=sorted({start,end,*events});active={};unsure={};result=[]
    for a,b in zip(points,points[1:]):
        for kind,key,row in events[a]:
            target=active if kind=='label' else unsure
            if row is None:target.pop(key,None)
            else:target[key]=row
        kind='unknown' if unsure else 'ambiguous' if len(active)>1 else 'labeled' if active else 'unlabeled'
        result.append({'start':a,'end':b,'kind':kind,'label':next(iter(active.values())) if kind=='labeled' else None})
    return result


def day_parts(a,b,zone):
    while a<b:
        date=datetime.fromtimestamp(a/1000,zone).date()
        end=int(datetime.combine(date+timedelta(days=1),time(),zone).timestamp()*1000)
        stop=min(b,end)
        yield date.isoformat(),a,stop
        a=stop


def values(window):
    f=window['features'];return [f['acRms'],f['log10Power1to40Hz'],f['bandShares'][2]]


def quartiles(windows):
    if not windows:return None
    return np.percentile(np.asarray([values(w) for w in windows],dtype=float),[25,50,75],axis=0,method="linear").T.tolist()


def example(window,kind,label):
    return {**{k:window[k] for k in ('windowId','startMs','endMsExclusive','sourceRecordDigests','acquisitionGroupId','acquisition')},
            'kind':kind,'labelId':label['id'],'activityId':label['activity_id'],'activityName':label['activityName'],
            'source':label['source'],'reviewState':label['review_state'],'timingPrecision':label['timing_precision'],
            'qualityFlags':window['qualityFlags'],'featureValues':values(window)}


def build(report,database,zone_name,*,check=lambda:None,progress=lambda *args:None):
    participant=report['sourceInventory']['acquisition']['participant_id'];zone=ZoneInfo(zone_name)
    labels,unknown,annotation=annotations(database,participant)
    inventory=report['sourceInventory'];first,last=inventory['firstTimestampMs'],inventory['lastTimestampMs']
    start=max(first or 0,inventory['range']['startMs'] or 0)
    end=min((last+1000) if last is not None else start,inventory['range']['endMsExclusive'] or 2**63-1)
    lanes=segments(labels,unknown,start,end) if start<end else []
    lane_starts=[s['start'] for s in lanes]
    contexts={};records=defaultdict(list)
    def include(acquisition,a,b):
        if a is None or b is None:return
        if acquisition['participant_id']!=participant:raise ValueError('Comparison acquisition scope mismatch')
        group=fingerprint(acquisition);contexts[group]=acquisition
        records[group].append((max(start,a),min(end,b)))
    for window in report['windows']:include(window['acquisition'],window['startMs'],window['endMsExclusive'])
    for item in report['failedWindows']:
        w=item['provenance'];include(w['acquisition'],w['startMs'],w['endMsExclusive'])
    for item in [*report['excludedSpans'],*report['remainderSpans']]:include(item['acquisition'],item['startMs'],item['endMsExclusive'])
    rows={};strata={};account=defaultdict(Counter)
    def key(group,label):return (group,label['activity_id'],label['source'],label['review_state'])
    def stratum(group,label):
        k=key(group,label)
        if k not in strata:strata[k]={'acquisitionGroupId':group,'activityId':label['activity_id'],'activityName':label['activityName'],
            'source':label['source'],'reviewState':label['review_state'],'labels':{},'windows':[],'flagged':[]}
        return strata[k]
    for group,spans in sorted(records.items()):
        check();spans=union(spans);records[group]=spans
        if not spans:continue
        for lane in lanes:
            if lane['label'] is None:continue
            a,b=max(spans[0][0],lane['start']),min(spans[-1][1],lane['end'])
            label=lane['label'];s=stratum(group,label)
            for day,lo,hi in day_parts(a,b,zone):
                k=(*key(group,label),day)
                row=rows.setdefault(k,{'date':day,'labeledMs':0,'recordedMs':0,'labels':{},'windows':[],'flagged':[]})
                row['labeledMs']+=hi-lo;row['recordedMs']+=intersection_ms(spans,lo,hi)
                row['labels'][label['id']]=label;s['labels'][label['id']]=label
    for i,w in enumerate(report['windows']):
        if i%1024==0:check();progress('activity comparisons',i,len(report['windows']))
        group=w['acquisitionGroupId'];a,b=w['startMs'],w['endMsExclusive'];counts=account[group]
        pos=bisect_right(lane_starts,a)-1
        lane=lanes[pos] if 0<=pos<len(lanes) else None
        if lane is None or b>lane['end']:counts['boundaryWindows']+=1;continue
        if lane['kind']!='labeled':counts[lane['kind']+'Windows']+=1;continue
        parts=list(day_parts(a,b,zone))
        if len(parts)!=1:counts['boundaryWindows']+=1;continue
        label=lane['label'];k=(*key(group,label),parts[0][0])
        row=rows[k];s=stratum(group,label)
        flag=bool(w['qualityFlags']);field='flagged' if flag else 'windows'
        row[field].append(w);s[field].append((w,label));counts['assignedFlaggedWindows' if flag else 'assignedUsableWindows']+=1
    payloads={};groups=defaultdict(list)
    for group,acquisition in sorted(contexts.items()):
        compatible={k:v for k,v in acquisition.items() if k!='session_id'}
        groups[fingerprint(compatible)].append((group,acquisition,compatible))
    identity=fingerprint({'version':VERSION,'analysis':report['analysisIdentity'],'annotations':annotation['revision'],
                          'timezone':zone_name,'implementation':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    manifest={'version':VERSION,'participantId':participant,'analysisIdentity':report['analysisIdentity'],'comparisonIdentity':identity,
              'timezone':zone_name,'annotation':annotation,'range':{'startMs':start,'endMsExclusive':end},
              'sourceCoverage':report['coverage'],'unplacedExcludedRecords':sum(s['recordCount'] for s in report['excludedSpans'] if s['startMs'] is None),
              'features':FEATURES,'groups':[],'defaultMethod':'No clustering default enabled; independent interpretation and Phase 3 evidence review remain open.'}
    for compatible,members in sorted(groups.items()):
        check();group_ids={m[0] for m in members};out=[];daily=[]
        for k,s in sorted(strata.items()):
            if k[0] not in group_ids:continue
            if not s['labels']:continue
            sid=fingerprint(list(k));assigned=s['windows'];flags=s['flagged'];ws=[w for w,_ in assigned]
            item={name:s[name] for name in ('acquisitionGroupId','activityId','activityName','source','reviewState')}
            item.update(stratumId=sid,sessionId=contexts[k[0]].get('session_id'),labelCount=len(s['labels']),
                timingCounts=dict(sorted(Counter(r['timing_precision'] for r in s['labels'].values()).items())),
                usableWindows=len(ws),usableMs=sum(w['endMsExclusive']-w['startMs'] for w in ws),
                flaggedWindows=len(flags),quartiles=quartiles(ws),examples=[])
            if assigned:
                ordinary=min(assigned,key=lambda pair:pair[0]['windowId']);item['examples'].append(example(ordinary[0],'ordinary hash-selected',ordinary[1]))
                median=float(np.median([w['features']['acRms'] for w in ws]));typical=min(assigned,key=lambda pair:(abs(pair[0]['features']['acRms']-median),pair[0]['windowId']))
                item['examples'].append(example(typical[0],'RMS median example',typical[1]))
            if flags:
                excluded=min(flags,key=lambda pair:pair[0]['windowId']);item['examples'].append(example(excluded[0],'quality-excluded example',excluded[1]))
            dates=[]
            for row_key,row in sorted(rows.items()):
                if row_key[:4]!=k:continue
                usable=sum(w['endMsExclusive']-w['startMs'] for w in row['windows']);flagged=sum(w['endMsExclusive']-w['startMs'] for w in row['flagged'])
                if usable:dates.append(row['date'])
                daily.append({'stratumId':sid,'date':row['date'],'labelCount':len(row['labels']),
                    'timingCounts':dict(sorted(Counter(label['timing_precision'] for label in row['labels'].values()).items())),'labeledMs':row['labeledMs'],'recordedMs':row['recordedMs'],
                    'missingMs':row['labeledMs']-row['recordedMs'],'usableMs':usable,'flaggedMs':flagged,
                    'otherRecordedMs':row['recordedMs']-usable-flagged,'usableWindows':len(row['windows']),
                    'flaggedWindows':len(row['flagged']),'quartiles':quartiles(row['windows'])})
            item.update(usableDays=len(set(dates)),dates=sorted(set(dates)))
            out.append(item)
        totals=Counter()
        for gid in group_ids:totals.update(account[gid])
        payload={'version':VERSION,'participantId':participant,'analysisIdentity':report['analysisIdentity'],'comparisonIdentity':identity,
                 'compatibilityId':compatible,'acquisition':members[0][2],'contexts':[{'acquisitionGroupId':g,'acquisition':a} for g,a,_ in members],
                 'strata':out,'days':sorted(daily,key=lambda r:(r['date'],r['stratumId'])),'windowAccounting':dict(sorted(totals.items()))}
        payloads[compatible]=payload
        manifest['groups'].append({'compatibilityId':compatible,'acquisition':members[0][2],'exactContexts':len(members),
            'knownSessions':len({a['session_id'] for _,a,_ in members if a.get('session_id')}),
            'unknownSessionContexts':sum(not a.get('session_id') for _,a,_ in members),
            'computedWindows':sum(totals.values()),'strata':len(out),'dailyRows':len(daily),'payloadHash':fingerprint(payload)})
    progress('activity comparisons',len(report['windows']),len(report['windows']))
    return manifest,payloads
