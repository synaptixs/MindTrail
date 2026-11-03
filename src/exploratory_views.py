"""Bounded numerical view facts for the source-workspace explorer."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

import numpy as np
from threadpoolctl import threadpool_limits

import exploratory_models as base
import exploratory_stability as stability
from analysis_provenance import fingerprint

FEATURES = [('log10Power1to40Hz', 'Log power 1–40 Hz', 'log10 source units²'),
            ('delta', 'Delta share', 'fraction'), ('theta', 'Theta share', 'fraction'),
            ('alpha', 'Alpha share', 'fraction'), ('beta', 'Beta share', 'fraction'),
            ('high', '30–40 Hz share', 'fraction'), ('acRms', 'AC RMS', 'source units'),
            ('maxStep', 'Largest sample step', 'source units'),
            ('linearTrendFraction', 'Linear trend fraction', 'fraction'),
            ('mainsLikePowerFraction', 'Mains-like fraction', 'fraction')]


def pca_projection(raw):
    raw = np.asarray(raw, dtype=float)
    if raw.ndim != 2 or not np.isfinite(raw).all():raise ValueError('PCA requires a finite feature matrix')
    n, d = raw.shape
    if n < 2:
        return {'status': 'insufficient_windows', 'coordinates': [[0., 0.]]*n, 'rank': 0}
    median = np.median(raw, axis=0)
    scale = np.maximum(np.percentile(raw, 75, axis=0)-np.percentile(raw, 25, axis=0), 1e-6)
    normalized = (raw-median)/scale
    center = normalized.mean(axis=0)
    centered = normalized-center
    with threadpool_limits(limits=1):
        _, singular, components = np.linalg.svd(centered, full_matrices=False)
    rank = int(np.sum(singular > max(n, d)*np.finfo(float).eps*singular[0])) if singular[0] else 0
    for row in components:
        if row[np.argmax(np.abs(row))] < 0:row *= -1
    axes = components[:2]
    with threadpool_limits(limits=1):coordinates = centered @ axes.T
    total = float(np.sum(singular**2))
    ratios = (singular[:2]**2/total).tolist() if total else [0., 0.]
    tied = any(abs(singular[i]**2-singular[i+1]**2) <= max(total, 1e-30)*1e-8
               for i in range(min(2, len(singular)-1)) if singular[i] > 0)
    return {'status': 'projected' if rank else 'constant_features', 'rank': rank,
            'coordinates': coordinates.tolist() if rank else [[0., 0.]]*n,
            'components': axes.tolist(), 'explainedVarianceRatio': ratios,
            'scaling': {'median': median.tolist(), 'iqrScale': scale.tolist(), 'meanAfterScaling': center.tolist()},
            'axisAmbiguity': tied, 'signConvention': 'largest absolute loading positive; first feature breaks ties'}


def feature_values(window):
    f = window['features']
    return [f['log10Power1to40Hz'], *f['bandShares'], f['acRms'], f['maxStep'],
            f['linearTrendFraction'], f['mainsLikePowerFraction']]


def distributions(values):
    result = []
    for index, (key, label, units) in enumerate(FEATURES):
        column = values[:, index]
        low, high = (0., 1.) if units == 'fraction' else (float(column.min()), float(column.max()))
        if low == high:low, high = low-.5, high+.5
        counts, edges = np.histogram(column, bins=20, range=(low, high))
        result.append({'key': key, 'label': label, 'units': units, 'windows': len(column),
                       'quartiles': np.percentile(column, [25, 50, 75]).tolist(),
                       'edges': edges.tolist(), 'counts': counts.tolist()})
    return result


def representative(indexes, normalized, windows, values, name):
    # Closest observed window to this subset's feature centroid; no synthetic waveform.
    distance = np.sum((normalized[indexes]-normalized[indexes].mean(axis=0))**2, axis=1)
    index = int(indexes[int(np.argmin(distance))])
    return {'name': name, 'windowId': windows[index]['windowId'], 'windows': len(indexes),
            'profileQuartiles': np.percentile(values[indexes], [25, 50, 75], axis=0).T.tolist()}


def build_views(report, *, check=lambda: None, progress=lambda stage, done, total: None):
    sampling = json.loads(base.DEFAULT_CONFIG.read_text(encoding='utf-8'))
    config = json.loads(stability.DEFAULT_CONFIG.read_text(encoding='utf-8'))
    analysis = stability.analyze_stability(report, sampling, config, check=check, progress=progress)
    identity = fingerprint({'version': 1, 'stabilityIdentity': analysis['stabilityIdentity'],
                            'viewSource': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    windows_by_id = {w['windowId']: w for w in report['windows']}
    manifest = {'version': 1, 'analysisIdentity': report['analysisIdentity'], 'explorationIdentity': identity,
                'participantId': analysis['snapshot']['participantId'], 'snapshot': analysis['snapshot'],
                'samplingConfiguration': sampling, 'stabilityConfiguration': config,
                'demo': False, 'groups': [], 'limitations': analysis['limitations']}
    payloads = {}
    for group in analysis['groups']:
        check()
        windows = [windows_by_id[w['windowId']] for w in group['selectedIntervals']]
        entry = {k: group[k] for k in ('acquisitionGroupId', 'acquisition', 'computedWindows',
            'qualityExcludedWindows', 'eligibleWindows', 'spacingOmittedWindows', 'budgetOmittedWindows',
            'selectedWindows', 'selectedDays', 'recordingConditions', 'excludedDayScope')}
        entry['files'] = {}
        values = np.asarray([feature_values(w) for w in windows], dtype=float)
        raw = np.asarray([base.feature_vector(w) for w in windows], dtype=float)
        for view, data in group['views'].items():
            check()
            matrix = raw if view == 'with_power' else raw[:, 1:] if len(raw) else raw
            projection = pca_projection(matrix) if len(raw) else {'status': 'insufficient_windows', 'coordinates': [], 'rank': 0}
            coordinates = projection.pop('coordinates')
            projection['features'] = data['features']
            normalized = ((matrix-np.median(matrix, axis=0))/np.maximum(np.percentile(matrix, 75, axis=0)-np.percentile(matrix, 25, axis=0), 1e-6)) if len(raw) else matrix
            points = [{**{k: w[k] for k in ('windowId', 'startMs', 'endMsExclusive', 'sourceRecordDigests')},
                       'coordinates': coordinates[i], 'featureValues': values[i].tolist()} for i, w in enumerate(windows)]
            candidates = []
            for candidate in data['candidates']:
                check()
                chosen = dict(candidate)
                chosen['representatives'] = []
                chosen['representativeGroupTotal'] = candidate.get('assignment', {}).get('groupCount', 0)
                if candidate['status'] == 'fitted':
                    labels = np.asarray(candidate['labels'])
                    groups = sorted(candidate['assignment']['groups'], key=lambda g: (-g['windows'], g['groupNumber']))
                    for row in groups[:12]:
                        rep = representative(np.flatnonzero(labels == row['groupNumber']), normalized, windows, values, f"Group {row['groupNumber']+1}")
                        chosen['representatives'].append(dict(rep, groupNumber=row['groupNumber']))
                    if np.any(labels < 0):
                        rep = representative(np.flatnonzero(labels < 0), normalized, windows, values, 'Algorithm unassigned')
                        chosen['unassignedRepresentative'] = rep
                chosen['refits'] = [r for r in data['refits'] if r['candidateId'] == candidate['candidateId']]
                chosen['powerAblation'] = next(a for a in group['powerAblation'] if a['candidateId'] == candidate['candidateId'])
                candidates.append(chosen)
            typical = representative(np.arange(len(windows)), normalized, windows, values, 'Typical selected feature window') if windows else None
            payload = {'version': 1, 'analysisIdentity': report['analysisIdentity'], 'explorationIdentity': identity,
                'participantId': manifest['participantId'], 'acquisitionGroupId': group['acquisitionGroupId'],
                'acquisition': group['acquisition'], 'view': view, 'pca': projection, 'points': points,
                'distributions': distributions(values) if len(values) else [], 'typical': typical,
                'candidates': candidates, 'representativeLimit': 12, 'defaultCandidateId': None}
            if len(base.canonical_json(payload).encode('utf-8')) > 8_000_000:raise ValueError('Explorer view payload exceeds bound')
            entry['files'][view] = base.content_digest(payload)
            payloads[(group['acquisitionGroupId'], view)] = payload
        manifest['groups'].append(entry)
    return manifest, payloads
