"""Read-only exploratory robustness, day recurrence and recording-condition diagnostics."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import warnings
from zoneinfo import ZoneInfo

import numpy as np
import scipy
import sklearn
from sklearn.cluster import HDBSCAN, KMeans
from sklearn.exceptions import ConvergenceWarning
from sklearn.metrics import adjusted_mutual_info_score, adjusted_rand_score
from sklearn.mixture import GaussianMixture
from threadpoolctl import threadpool_info, threadpool_limits

import exploratory_models as base
from analysis_provenance import canonical_json, fingerprint

VERSION = 'exploration-stability-v1'
DEFAULT_CONFIG = base.DEFAULT_CONFIG.with_name('exploration-stability.json')
PROXIES = ('log10Power1to40Hz', 'acRms', 'maxStep', 'linearTrendFraction', 'mainsLikePowerFraction')


def configuration(value):
    bounds = {'version': (1, 1), 'temporalBlockMinutes': (5, 120),
              'maximumExcludedDays': (1, 3), 'minimumSharedWindows': (20, 2000),
              'recurrenceMinimumWindowsPerDay': (2, 100)}
    if not isinstance(value, dict) or set(value) != set(bounds):
        raise ValueError('Stability configuration has missing or unknown fields')
    for key, (low, high) in bounds.items():
        if type(value[key]) is not int or not low <= value[key] <= high:
            raise ValueError(f'Invalid stability setting: {key}')
    return dict(value)


def candidate_grid(sampling):
    candidates = []
    for size in (15, 30, 60):
        for minimum in (5, 10):
            for single in (False, True):
                candidates.append({'method': 'hdbscan', 'parameters': {
                    'min_cluster_size': size, 'min_samples': minimum,
                    'allow_single_cluster': single, 'n_jobs': 1}})
    for k in range(1, sampling['maximumComponents']+1):
        for seed in (sampling['seed'], (sampling['seed']+1) % 2**32):
            candidates.append({'method': 'gmm', 'parameters': {'n_components': k,
                'covariance_type': 'diag', 'random_state': seed, 'n_init': 3,
                'reg_covar': 1e-4, 'max_iter': 200}})
            candidates.append({'method': 'kmeans', 'parameters': {'n_clusters': k,
                'random_state': seed, 'n_init': 10, 'max_iter': 300}})
    return [dict(c, candidateId=fingerprint(c)) for c in candidates]


def is_anchor(candidate, seed):
    p = candidate['parameters']
    if candidate['method'] == 'hdbscan':
        return p['min_cluster_size'] == 30 and p['min_samples'] == 10 and p['allow_single_cluster']
    return p['random_state'] == seed and p.get('n_components', p.get('n_clusters')) in (2, 4, 6)


def assignment_summary(labels):
    counts = Counter(int(v) for v in labels)
    n = len(labels)
    return {'groups': [{'groupNumber': k, 'windows': v} for k, v in sorted(counts.items()) if k >= 0],
            'groupCount': sum(k >= 0 for k in counts), 'windows': n,
            'unassignedWindows': sum(v for k, v in counts.items() if k < 0),
            'unassignedFraction': sum(v for k, v in counts.items() if k < 0)/n if n else None}


def partition_agreement(left, right, minimum):
    """Noise is coverage, never an extra cluster that can manufacture high ARI."""
    left, right = np.asarray(left), np.asarray(right)
    if left.shape != right.shape or left.ndim != 1:
        raise ValueError('Agreement requires aligned window assignments')
    both = (left >= 0) & (right >= 0)
    union = (left >= 0) | (right >= 0)
    n = int(both.sum())
    result = {'sharedWindows': len(left), 'jointAssignedWindows': n,
              'leftUnassignedWindows': int((left < 0).sum()),
              'rightUnassignedWindows': int((right < 0).sum()),
              'assignedSetJaccard': float(n/union.sum()) if union.any() else None,
              'adjustedRandIndex': None}
    if n < minimum:
        result['status'] = 'insufficient_joint_assigned_support'
    elif min(len(np.unique(left[both])), len(np.unique(right[both]))) < 2:
        result['status'] = 'fewer_than_two_joint_partitions'
    else:
        result.update(status='descriptive_refit_agreement', adjustedRandIndex=float(adjusted_rand_score(left[both], right[both])))
    return result


def fit_candidate(raw, candidate, minimum):
    """Fit preprocessing only on supplied training rows; keep skipped fits explicit."""
    if len(raw) < minimum:
        return {'status': 'insufficient_training_windows', 'trainingWindows': len(raw)}, None, None
    median = np.median(raw, axis=0)
    scale = np.maximum(np.percentile(raw, 75, axis=0)-np.percentile(raw, 25, axis=0), 1e-6)
    x = (raw-median)/scale
    metadata = {'trainingWindows': len(raw), 'scaling': {'median': median.tolist(), 'iqrScale': scale.tolist(), 'floor': 1e-6}}
    if np.max(np.ptp(x, axis=0)) <= 1e-10:
        return dict(metadata, status='no_feature_variation'), None, None
    method, params = candidate['method'], candidate['parameters']
    required = max(params['min_cluster_size'], params['min_samples']) if method == 'hdbscan' else params.get('n_components', params.get('n_clusters'))
    if required > len(x) or (method != 'hdbscan' and required > len(np.unique(x, axis=0))):
        return dict(metadata, status='insufficient_distinct_or_training_windows'), None, None
    model = {'hdbscan': HDBSCAN, 'gmm': GaussianMixture, 'kmeans': KMeans}[method](**params)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always', ConvergenceWarning)
        labels = model.fit_predict(x)
    converged = bool(model.converged_) if method == 'gmm' else not any(issubclass(w.category, ConvergenceWarning) for w in caught)
    result = dict(metadata, status='fitted' if converged else 'nonconverged', converged=converged,
                  assignment=assignment_summary(labels))
    if method == 'gmm':result['bic'] = float(model.bic(x))
    if method == 'kmeans':result['inertia'] = float(model.inertia_)
    return result, labels, model


def perturbations(windows, days, config):
    starts = np.asarray([w['startMs'] for w in windows], dtype=np.int64)
    ends = np.asarray([w['endMsExclusive']-1 for w in windows], dtype=np.int64)
    block_ms = config['temporalBlockMinutes']*60000
    blocks, end_blocks = starts // block_ms, ends // block_ms
    items = []
    for fold in (0, 1):
        keep = np.flatnonzero((blocks % 4 != fold) & (end_blocks % 4 != fold))
        items.append({'id': f'block-drop-{fold}', 'kind': 'utc_block_drop', 'droppedModulo4': fold,
                      'trainingIndices': keep.tolist(), 'excludedWindows': len(windows)-len(keep)})
    available = sorted(set(days))
    chosen = [available[int(i)] for i in np.linspace(0, len(available)-1,
              min(config['maximumExcludedDays'], len(available)), dtype=int)] if available else []
    for day in chosen:
        zone = ZoneInfo('America/New_York')
        end_days = [datetime.fromtimestamp(int(ms)/1000, timezone.utc).astimezone(zone).date().isoformat() for ms in ends]
        keep = [i for i, value in enumerate(days) if value != day and end_days[i] != day]
        items.append({'id': f'day-drop-{day}', 'kind': 'excluded_day', 'excludedDay': day,
                      'trainingIndices': keep, 'excludedWindows': len(windows)-len(keep)})
    return items, {'availableDays': len(available), 'evaluatedDays': chosen,
                   'omittedDays': [d for d in available if d not in chosen]}


def transfer_diagnostics(raw_train, raw_day, fitted, model, method):
    if model is None or fitted['status'] != 'fitted':return {'status': 'training_fit_unavailable'}
    if method == 'hdbscan':return {'status': 'unsupported_density_out_of_sample_prediction'}
    if not len(raw_day):return {'status': 'no_excluded_day_windows'}
    scale = fitted['scaling']
    train = (raw_train-np.asarray(scale['median']))/np.asarray(scale['iqrScale'])
    test = (raw_day-np.asarray(scale['median']))/np.asarray(scale['iqrScale'])
    if method == 'gmm':
        reference, observed = model.score_samples(train), model.score_samples(test)
        cutoff = float(np.percentile(reference, 5))
        return {'status': 'exploratory_density_transfer', 'trainingWindows': len(train), 'excludedDayWindows': len(test),
                'trainingMeanLogDensity': float(np.mean(reference)), 'excludedDayMeanLogDensity': float(np.mean(observed)),
                'trainingFifthPercentileLogDensity': cutoff, 'excludedDayBelowReferenceFraction': float(np.mean(observed < cutoff))}
    reference, observed = model.transform(train).min(axis=1), model.transform(test).min(axis=1)
    cutoff = float(np.percentile(reference, 95))
    return {'status': 'exploratory_distance_transfer', 'trainingWindows': len(train), 'excludedDayWindows': len(test),
            'training95thPercentileDistance': cutoff, 'excludedDayMedianDistance': float(np.median(observed)),
            'excludedDayBeyondReferenceFraction': float(np.mean(observed > cutoff))}


def condition_summary(labels, windows, days, config):
    labels = np.asarray(labels)
    assigned = labels >= 0
    all_days = sorted(set(days))
    day_array = np.asarray(days)
    day_counts = Counter(days)
    rows = []
    for number in sorted(set(int(v) for v in labels if v >= 0)):
        indexes = np.flatnonzero(labels == number)
        counts = Counter(days[i] for i in indexes)
        rows.append({'groupNumber': number, 'windows': len(indexes),
            'sampledDurationMs': sum(windows[i]['endMsExclusive']-windows[i]['startMs'] for i in indexes),
            'daysPresent': len(counts), 'daysAtMinimumSupport': sum(v >= config['recurrenceMinimumWindowsPerDay'] for v in counts.values()),
            'maximumDayFraction': max(counts.values())/len(indexes),
            'byDay': [{'date': d, 'windows': counts[d], 'selectedWindowsThatDay': day_counts[d],
                       'fractionOfDaySample': counts[d]/day_counts[d]} for d in all_days],
            'proxyMedians': {key: float(np.median([windows[i]['features'][key] for i in indexes])) for key in PROXIES}})
    ami = None
    if len(set(day_array[assigned])) >= 2 and len(set(labels[assigned])) >= 2:
        ami = float(adjusted_mutual_info_score(day_array[assigned], labels[assigned]))
    association = {}
    for key in PROXIES:
        values = np.array([w['features'][key] for w in windows])[assigned]
        total = float(np.sum((values-values.mean())**2)) if len(values) else 0.
        between = sum(int((labels[assigned] == n).sum())*(float(values[labels[assigned] == n].mean())-float(values.mean()))**2
                      for n in np.unique(labels[assigned])) if total else 0.
        association[key] = min(1., max(0., between/total)) if total and len(rows) >= 2 else None
    return {'recurrenceBasis': 'pooled selected-snapshot groups; not independent replication',
            'minimumWindowsPerDay': config['recurrenceMinimumWindowsPerDay'], 'groups': rows,
            'dayAssociationAdjustedMutualInformation': ami,
            'dayAssociationStatus': 'descriptive' if ami is not None else 'insufficient_day_or_group_variation',
            'proxyBetweenGroupVarianceFraction': association,
            'byDayCoverage': [{'date': d, 'selectedWindows': day_counts[d],
                              'unassignedWindows': int(np.sum((day_array == d) & ~assigned))} for d in all_days]}


def evaluate_group(group, sampling, config, grid, check=lambda: None):
    windows = group['windows']
    zone = ZoneInfo(sampling['timezone'])
    days = [datetime.fromtimestamp(w['startMs']/1000, timezone.utc).astimezone(zone).date().isoformat() for w in windows]
    result = {k: v for k, v in group.items() if k != 'windows'}
    result.update(selectedIntervals=[{k: w[k] for k in ('windowId', 'startMs', 'endMsExclusive')} for w in windows],
                  views={}, powerAblation=[], recordingConditions={
                      'sessionStatus': 'unknown_not_recorded' if group['acquisition']['session_id'] is None else 'constant_within_acquisition_group_not_identifiable',
                      'contactStatus': 'unmeasured; feature proxies do not establish contact impedance or causation',
                      'sourceDevicePlacement': 'stratified_by_exact_acquisition_context'})
    changes, scope = perturbations(windows, days, config)
    result.update(perturbations=changes, excludedDayScope=scope)
    raw_all = np.array([base.feature_vector(w) for w in windows])
    anchors = [c for c in grid if is_anchor(c, sampling['seed'])]
    result['maximumPlannedFits'] = 2*(len(grid)+len(anchors)*len(changes))
    if result['maximumPlannedFits'] > 142:raise ValueError('Fit budget exceeds protocol')
    for view in ('with_power', 'spectral_only'):
        raw = raw_all if view == 'with_power' else raw_all[:, 1:] if len(raw_all) else raw_all
        candidates, full_labels = [], {}
        for candidate in grid:
            check()
            fitted, labels, _ = fit_candidate(raw, candidate, sampling['minimumSelectedPerGroup'])
            entry = dict(candidate, **fitted)
            if labels is not None:
                if fitted['status'] == 'fitted':full_labels[candidate['candidateId']] = labels
                entry.update(labels=labels.tolist(), conditions=condition_summary(labels, windows, days, config))
            candidates.append(entry)
        sensitivity, refits = [], []
        for method in ('hdbscan', 'gmm', 'kmeans'):
            reference = next((c for c in anchors if c['method'] == method and
                             (method == 'hdbscan' or c['parameters'].get('n_components', c['parameters'].get('n_clusters')) == 2)), None)
            if reference and reference['candidateId'] in full_labels:
                for candidate in grid:
                    cid = candidate['candidateId']
                    if candidate['method'] == method and cid in full_labels and cid != reference['candidateId']:
                        sensitivity.append({'referenceCandidateId': reference['candidateId'], 'candidateId': cid,
                            **partition_agreement(full_labels[reference['candidateId']], full_labels[cid], config['minimumSharedWindows'])})
        for change in changes:
            keep = np.asarray(change['trainingIndices'], dtype=int)
            excluded = np.setdiff1d(np.arange(len(windows)), keep)
            for candidate in anchors:
                check()
                cid = candidate['candidateId']
                if not len(excluded):
                    refits.append({'perturbationId': change['id'], 'candidateId': cid,
                                   'status': 'no_windows_excluded'})
                    continue
                fitted, labels, model = fit_candidate(raw[keep], candidate, sampling['minimumSelectedPerGroup'])
                entry = {'perturbationId': change['id'], 'candidateId': cid, **fitted}
                if labels is not None and cid in full_labels and fitted['status'] == 'fitted':
                    entry['agreement'] = partition_agreement(full_labels[cid][keep], labels, config['minimumSharedWindows'])
                if change['kind'] == 'excluded_day':
                    entry['transfer'] = transfer_diagnostics(raw[keep], raw[excluded], fitted, model, candidate['method'])
                refits.append(entry)
        result['views'][view] = {'features': list(base.FEATURES if view == 'with_power' else base.FEATURES[1:]),
                                  'candidates': candidates, 'parameterSensitivity': sensitivity, 'refits': refits}
    by_view = [{c['candidateId']: c for c in result['views'][v]['candidates']} for v in ('with_power', 'spectral_only')]
    for candidate in grid:
        cid = candidate['candidateId'];left, right = (v[cid] for v in by_view)
        contrast = {'candidateId': cid, 'withPowerStatus': left['status'], 'spectralOnlyStatus': right['status']}
        if left['status'] == right['status'] == 'fitted':
            contrast['agreement'] = partition_agreement(left['labels'], right['labels'], config['minimumSharedWindows'])
        result['powerAblation'].append(contrast)
    result['completedFits'] = sum(c['status'] in ('fitted', 'nonconverged') for v in result['views'].values() for c in v['candidates']+v['refits'])
    result['status'] = 'diagnostics_complete_no_default_selected' if result['completedFits'] else 'no_fittable_candidates'
    return result


def analyze_stability(report, sampling_config, stability_config, *, check=lambda: None, progress=lambda stage, done, total: None):
    sampling = base.configuration(sampling_config)
    config = configuration(stability_config)
    check()
    snapshot, groups = base.prepare_snapshot(report, sampling)
    grid = candidate_grid(sampling)
    with threadpool_limits(limits=1):
        environment = {'python': platform.python_version(), 'numpy': np.__version__, 'scipy': scipy.__version__,
            'sklearn': sklearn.__version__, 'threadpools': [{k: p.get(k) for k in ('internal_api', 'version', 'architecture', 'num_threads')} for p in threadpool_info()]}
        evaluated = []
        for index, group in enumerate(groups):
            progress('exploring acquisition groups', index, len(groups))
            evaluated.append(evaluate_group(group, sampling, config, grid, check))
        check()
        progress('exploring acquisition groups', len(groups), len(groups))
        result = {'version': 1, 'methodVersion': VERSION, 'snapshot': snapshot,
            'samplingConfiguration': sampling, 'stabilityConfiguration': config, 'candidateGrid': grid,
            'implementation': {'sources': {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                for name in ('exploratory_stability.py', 'exploratory_models.py', 'analysis_provenance.py', 'analysis_quality.py')}, 'environment': environment},
            'groups': evaluated,
            'defaultMethod': None, 'limitations': [
                'Exploratory signal groups are not activities, moods or clinical states',
                'Only selected windows are fitted; original exclusions and sampling omissions remain in scope',
                'Refit ARI uses shared training windows, not independent activity accuracy; degenerate partitions have no ARI',
                'Pooled cross-day occupancy is not independently replicated group discovery',
                'Excluded-day transfer uses training-only preprocessing but is exploratory on previously inspected dates',
                'Quality screening and sample selection were frozen on the full snapshot, not independent training-fold discovery',
                'Fixed folds, candidate counts and excluded dates are a bounded sensitivity sample, not exhaustive stability',
                'Feature ablation and proxy associations cannot identify contact quality or biological causes',
                'No significance tests, independent-window uncertainty intervals or default method selected']}
    result['stabilityIdentity'] = base.content_digest(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('quality_report', type=Path)
    parser.add_argument('--sampling-config', type=Path, default=base.DEFAULT_CONFIG)
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.output.exists():raise ValueError('Output already exists')
        result = analyze_stability(json.loads(args.quality_report.read_text(encoding='utf-8')),
                                   json.loads(args.sampling_config.read_text(encoding='utf-8')),
                                   json.loads(args.config.read_text(encoding='utf-8')))
        text = canonical_json(result)+'\n'
        with args.output.open('x', encoding='utf-8') as out:out.write(text)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        parser.exit(1, f'Stability analysis failed: {error}\n')


if __name__ == '__main__':main()
