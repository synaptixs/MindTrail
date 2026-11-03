"""Bounded presentation facts derived from canonical quality-window intervals."""
from __future__ import annotations
import math


def coverage_summary(report: dict, *, timezone: str, demo: bool) -> dict:
    inventory = report['sourceInventory']
    start, last = inventory.get('firstTimestampMs'), inventory.get('lastTimestampMs')
    # Inventory uses valid timestamp bounds; records with invalid times stay unplaced.
    bins = []
    if start is not None and last is not None:
        end = last + 1000
        width = max(900000, math.ceil((end-start)/1500/900000)*900000)
        bins = [{'startMs':a,'endMsExclusive':min(a+width,end),'computedUnionMs':0,'flaggedUnionMs':0}
                for a in range(start,end,width)]
        # Sweep all intervals, with flagged intervals taking precedence in overlaps.
        events = {}
        for window in report['windows']:
            a,b = window['startMs'],window['endMsExclusive']
            flagged = bool(window['qualityFlags'])
            for point,delta in ((a,1),(b,-1)):
                v=events.setdefault(point,[0,0]);v[0]+=delta;v[1]+=delta*flagged
        active=flagged=0
        previous=None
        for point,(delta,flag_delta) in sorted(events.items()):
            if previous is not None and active and point>previous:
                a,b=max(start,previous),min(end,point)
                while a<b:
                    index=min(len(bins)-1,(a-start)//width)
                    stop=min(b,bins[index]['endMsExclusive'])
                    bins[index]['computedUnionMs']+=stop-a
                    if flagged:bins[index]['flaggedUnionMs']+=stop-a
                    a=stop
            active+=delta;flagged+=flag_delta;previous=point
    return {'analysisIdentity':report['analysisIdentity'],'sourceInventory':inventory,
            'coverage':report['coverage'],'groups':report['groups'][:200],'groupCount':len(report['groups']),
            'relativeScreeningGroupCount':sum(g['relativeScreeningAvailable'] for g in report['groups']),
            'spec':report['spec'],
            'limitations':report['limitations'],'scope':report['scope'],
            'timezone':timezone,'demo':demo,'bins':bins,
            'computedUnionMs':sum(b['computedUnionMs'] for b in bins),
            'flaggedUnionMs':sum(b['flaggedUnionMs'] for b in bins),
            'unplacedExcludedRecords':sum(s['recordCount'] for s in report['excludedSpans'] if s['startMs'] is None)}
