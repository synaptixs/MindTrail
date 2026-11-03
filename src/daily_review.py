"""Canonical elapsed-time lanes and New York wall-clock resolution. Stdlib only."""
from __future__ import annotations
from collections import Counter
from datetime import datetime, timedelta, timezone
import re
from zoneinfo import ZoneInfo

ZONE = 'America/New_York'
LOCAL = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?')


def epoch(value):
    dt=datetime.fromisoformat(value.replace('Z','+00:00'))
    if dt.tzinfo is None:raise ValueError('Use an explicit UTC offset')
    delta=dt.astimezone(timezone.utc)-datetime(1970,1,1,tzinfo=timezone.utc)
    micros=(delta.days*86400+delta.seconds)*1000000+delta.microseconds
    return micros//1000 if micros>=0 else -(-micros//1000)


def wall_time(value):
    """Return both real instants for a repeated hour; never normalize a missing hour."""
    if not isinstance(value,str) or not LOCAL.fullmatch(value):
        raise ValueError('Enter a New York date and time with seconds (optional milliseconds)')
    zone=ZoneInfo(ZONE)
    wall=datetime.fromisoformat(value)
    if not 1969<=wall.year<=9998:raise ValueError('Choose a year from 1969 through 9998')
    choices={}
    for fold in (0,1):
        local=wall.replace(tzinfo=zone,fold=fold)
        utc=local.astimezone(timezone.utc)
        if utc.astimezone(zone).replace(tzinfo=None)!=wall:continue
        stamp=epoch(utc.isoformat())
        offset=local.strftime('%z');offset=offset[:3]+':'+offset[3:]
        choices[stamp]={'ms':stamp,'utc':utc.isoformat(timespec='milliseconds').replace('+00:00','Z'),
                        'offset':offset,'label':local.strftime('%Z')+' (UTC'+offset+')'}
    if not choices:raise ValueError('This New York time does not exist during the daylight-saving change. Choose another time.')
    return {'timezone':ZONE,'choices':[choices[key] for key in sorted(choices)]}


def partition(runs,start,end,default):
    """Clip ordered disjoint facts and explicitly fill every uncovered interval."""
    result=[];cursor=start
    for run in runs:
        a,b=max(start,run['startMs']),min(end,run['endMsExclusive'])
        if b<=a:continue
        if a<cursor:raise ValueError('Timeline intervals overlap or are unordered')
        if a>cursor:result.append({'startMs':cursor,'endMsExclusive':a,'state':default})
        result.append({**run,'startMs':a,'endMsExclusive':b});cursor=b
    if cursor<end:result.append({'startMs':cursor,'endMsExclusive':end,'state':default})
    return result


def lanes(preview,labels,start,end):
    union=[]
    for run in preview['coverage']:
        a,b=epoch(run['start']),epoch(run['end'])
        if union and a<=union[-1]['endMsExclusive']:union[-1]['endMsExclusive']=max(b,union[-1]['endMsExclusive'])
        else:union.append({'startMs':a,'endMsExclusive':b,'state':'recorded'})
    activity=[]
    for label in labels:
        a,b=max(start,epoch(label['start'])),min(end,epoch(label['end']) if label['end'] else end)
        if a<b:activity.append({**label,'startMs':a,'endMsExclusive':b})
    activity.sort(key=lambda r:(r['startMs'],r['endMsExclusive'],r['id']))
    tracks=[]
    for run in activity:
        track=next((i for i,last in enumerate(tracks) if last<=run['startMs']),len(tracks))
        if track==len(tracks):tracks.append(run['endMsExclusive'])
        else:tracks[track]=run['endMsExclusive']
        run['track']=track
    return {'recording':partition(union,start,end,'missing'),'activities':activity,'activityTrackCount':len(tracks),
            'recordedMs':sum(max(0,min(end,r['endMsExclusive'])-max(start,r['startMs'])) for r in union)}


def quality_days(report,check=lambda:None):
    """Exact union of canonical quality windows, split at New York calendar boundaries."""
    zone=ZoneInfo(ZONE)
    events={}
    for index,window in enumerate(report['windows']):
        if index%2048==0:check()
        for point,delta in ((window['startMs'],1),(window['endMsExclusive'],-1)):
            entry=events.setdefault(point,[0,Counter()]);entry[0]+=delta
            for flag in window['qualityFlags']:entry[1][flag]+=delta
    days={};active=0;flags=Counter();previous=None
    for index,(point,(delta,changes)) in enumerate(sorted(events.items())):
        if index%2048==0:check()
        if previous is not None and active and point>previous:
            a=previous;names=sorted(flag for flag,count in flags.items() if count>0)
            while a<point:
                local=datetime.fromtimestamp(a/1000,zone)
                midnight=datetime.combine(local.date()+timedelta(days=1),datetime.min.time(),zone)
                b=min(point,int(midnight.timestamp()*1000));key=local.date().isoformat()
                runs=days.setdefault(key,[]);state='flagged' if names else 'computed_unflagged'
                if runs and runs[-1]['endMsExclusive']==a and runs[-1]['flags']==names:
                    runs[-1]['endMsExclusive']=b
                else:runs.append({'startMs':a,'endMsExclusive':b,'state':state,'flags':names})
                a=b
        active+=delta;flags.update(changes);previous=point
    return days
