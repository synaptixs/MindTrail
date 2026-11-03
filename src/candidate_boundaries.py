"""Opt-in exploratory mean-change proposals. Imported only by the numerical worker."""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

VERSION = 'mean-change-v1'
MAX_BINS = 1200
FEATURES = ('log10Power1to40Hz', 'thetaShare', 'alphaShare', 'betaShare')
SENSITIVITY = {'low': 3.0, 'medium': 1.5, 'high': .5}


def settings(value):
    if value is None:return None
    if not isinstance(value, dict) or set(value) != {'minimumSeconds', 'sensitivity'}:
        raise ValueError('Choose boundary minimum duration and sensitivity')
    if type(value['minimumSeconds']) is not int or not 12 <= value['minimumSeconds'] <= 1800:
        raise ValueError('Boundary minimum duration must be 12–1800 seconds')
    if not isinstance(value['sensitivity'],str) or value['sensitivity'] not in SENSITIVITY:raise ValueError('Choose low, medium or high sensitivity')
    return dict(value)


class Cost:
    def __init__(self, values, weights, edges):
        import numpy as np
        self.edges = np.asarray(edges, dtype=np.int64)
        values = np.asarray(values, dtype=float)
        weights = np.asarray(weights, dtype=float)
        self.mass = np.r_[0., np.cumsum(weights)]
        self.sums = np.vstack([np.zeros(values.shape[1]), np.cumsum(values*weights[:, None], axis=0)])
        self.squares = np.r_[0., np.cumsum(np.sum(values*values, axis=1)*weights)]
        self.n = len(values)

    def at(self, starts, end):
        import numpy as np
        starts = np.asarray(starts, dtype=int)
        mass = self.mass[end]-self.mass[starts]
        return np.maximum(0., self.squares[end]-self.squares[starts]-np.sum((self.sums[end]-self.sums[starts])**2, axis=-1)/mass)


def segment(cost, minimum_ms, penalty, method='pelt', check=lambda: None):
    """Exact PELT on the supplied bin grid; pruning deferred for minimum duration."""
    import numpy as np
    n = cost.n
    if cost.edges[-1]-cost.edges[0] < minimum_ms:return []
    if method == 'baseline':
        stack, ends = [(0, n)], []
        while stack:
            check()
            a, b = stack.pop()
            candidates = np.arange(a+1, b)
            candidates = candidates[(cost.edges[candidates]-cost.edges[a] >= minimum_ms) & (cost.edges[b]-cost.edges[candidates] >= minimum_ms)]
            if len(candidates):
                split_cost = cost.at(a, candidates)+cost.at(candidates, b)
                pos = int(np.argmin(split_cost)); cut = int(candidates[pos])
                if cost.at(a, b)-split_cost[pos] > penalty+1e-9:
                    stack.extend([(cut, b), (a, cut)]);continue
            ends.append(b)
        return sorted(ends)
    if method != 'pelt':raise ValueError('Unknown boundary method')
    score = np.full(n+1, np.inf);score[0] = -penalty
    previous = np.full(n+1, -1, dtype=int)
    count = np.full(n+1, n+1, dtype=int);count[0] = 0
    active, expiry, next_start = [], {}, 0
    for end in range(1, n+1):
        if end % 32 == 0:check()
        while next_start < end and cost.edges[end]-cost.edges[next_start] >= minimum_ms:
            if np.isfinite(score[next_start]):active.append(next_start)
            next_start += 1
        active = [s for s in active if cost.edges[end] < expiry.get(s, math.inf)]
        if not active:continue
        candidates = np.asarray(active)
        raw = score[candidates]+cost.at(candidates, end)
        best = min(range(len(candidates)), key=lambda i: (float(raw[i]), int(count[candidates[i]]), int(candidates[i])))
        prior = int(candidates[best]);score[end] = raw[best]+penalty
        previous[end] = prior;count[end] = count[prior]+1
        # A replacement at end is legal only once its own minimum duration elapsed.
        for s in candidates[raw > score[end]+1e-9]:
            expiry.setdefault(int(s), int(cost.edges[end])+minimum_ms)
    if previous[n] < 0:return []
    ends, end = [], n
    while end:
        ends.append(end);end = int(previous[end])
    return list(reversed(ends))


def daily_candidates(report, participant, config, check=lambda: None):
    import numpy as np
    config = settings(config)
    identity_input = {'version': VERSION, 'source': report['analysisIdentity'], 'settings': config,
                      'implementation': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    identity = hashlib.sha256(json.dumps(identity_input, sort_keys=True).encode()).hexdigest()
    days, runs, current = {}, [], []
    zone = ZoneInfo('America/New_York')
    def date(ms):return datetime.fromtimestamp(ms/1000, timezone.utc).astimezone(zone).date().isoformat()
    for i, w in enumerate(sorted(report['windows'], key=lambda w: (w['startMs'], w['acquisitionGroupId']))):
        if i % 1024 == 0:check()
        d = date(w['startMs'])
        day = days.setdefault(d, {'version':1, 'methodVersion':VERSION, 'participantId':participant,
            'analysisIdentity':report['analysisIdentity'], 'candidateIdentity':identity, 'settings':config,
            'date':d, 'eligibleWindows':0, 'excludedWindows':0, 'tooShortWindows':0, 'runs':[]})
        if w['qualityFlags'] or date(w['endMsExclusive']-1) != d:
            day['excludedWindows'] += 1
            if current:runs.append(current);current=[]
            continue
        day['eligibleWindows'] += 1
        if current and (current[-1]['endMsExclusive'] != w['startMs'] or current[-1]['acquisitionGroupId'] != w['acquisitionGroupId'] or date(current[-1]['startMs']) != d):
            runs.append(current);current=[]
        current.append(w)
    if current:runs.append(current)
    for run in runs:
        check()
        raw = np.asarray([[w['features']['log10Power1to40Hz'], *w['features']['bandShares'][1:4]] for w in run])
        median = np.median(raw, axis=0)
        scale = np.maximum(np.percentile(raw, 75, axis=0)-np.percentile(raw, 25, axis=0), [.1,.02,.02,.02])
        normalized = np.clip((raw-median)/scale, -10, 10)
        stride = max(1, math.ceil(len(run)/MAX_BINS))
        starts = list(range(0, len(run), stride))
        weights = [min(stride, len(run)-s) for s in starts]
        values = [normalized[s:s+weight].mean(axis=0) for s, weight in zip(starts, weights)]
        edges = [run[s]['startMs'] for s in starts]+[run[-1]['endMsExclusive']]
        cost = Cost(values, weights, edges)
        penalty = len(FEATURES)*math.log(max(2, len(run)))*SENSITIVITY[config['sensitivity']]
        item = {'startMs':edges[0], 'endMsExclusive':edges[-1], 'acquisitionGroupId':run[0]['acquisitionGroupId'],
                'acquisition':run[0]['acquisition'], 'windows':len(run), 'bins':len(starts),
                'maxResolutionMs':max(np.diff(edges)).item(), 'penalty':penalty,
                'scaling':{'features':list(FEATURES), 'median':median.tolist(), 'scale':scale.tolist(), 'clip':10}, 'methods':{}}
        for method in ('baseline', 'pelt'):
            ends = segment(cost, config['minimumSeconds']*1000, penalty, method, check)
            segments, a = [], 0
            for b in ends:
                segments.append({'startMs':edges[a], 'endMsExclusive':edges[b],
                    'meanFeatures':raw[starts[a]:starts[b] if b < len(starts) else len(raw)].mean(axis=0).tolist()})
                a = b
            item['methods'][method] = {'segments':segments, 'boundaryCount':max(0,len(ends)-1),
                'objective':sum(float(cost.at(a,b)) for a,b in zip([0]+ends[:-1],ends))+penalty*max(0,len(ends)-1) if ends else None}
        if edges[-1]-edges[0] < config['minimumSeconds']*1000:
            days[date(edges[0])]['tooShortWindows'] += len(run)
        days[date(edges[0])]['runs'].append(item)
    return days
