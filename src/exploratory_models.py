"""Source-only, read-only comparison of unnamed signal groups on a frozen quality report.

No activity labels, API credentials, database writes or out-of-sample predictions.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict
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
from sklearn.mixture import GaussianMixture
from threadpoolctl import threadpool_info, threadpool_limits

from analysis_provenance import AcquisitionContext, AnalysisSpec, canonical_json, fingerprint
from analysis_quality import QualityPolicy, valid_features

VERSION = 'exploration-v1'
from spectral_features import FEATURES, feature_vector as canonical_feature_vector
DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / 'config' / 'exploration.json'


def configuration(value: dict) -> dict:
    keys = {'version', 'seed', 'maximumInputWindows', 'maximumGroups',
            'maximumSelectedPerGroup', 'minimumSelectedPerGroup', 'minimumStartSpacingMs',
            'timezone', 'maximumComponents', 'densityMinimumSizes', 'densityMinimumSamples'}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError('Exploration configuration has missing or unknown fields')
    for key in keys - {'timezone', 'densityMinimumSizes'}:
        if type(value[key]) is not int or value[key] < (0 if key == 'seed' else 1):
            raise ValueError(f'Invalid exploration setting: {key}')
    if (value['version'] != 1 or value['seed'] > 2**32-1
            or not 20 <= value['maximumSelectedPerGroup'] <= 2000
            or not 2 <= value['minimumSelectedPerGroup'] <= value['maximumSelectedPerGroup']
            or value['maximumInputWindows'] > 1000000 or value['maximumGroups'] > 16
            or value['minimumStartSpacingMs'] < 60000 or value['maximumComponents'] > 6):
        raise ValueError('Exploration configuration exceeds the bounded protocol')
    sizes = value['densityMinimumSizes']
    if (not isinstance(sizes, list) or not 1 <= len(sizes) <= 3
            or any(type(v) is not int or not 2 <= v <= 2000 for v in sizes)
            or sorted(set(sizes)) != sizes):
        raise ValueError('Choose up to three ordered distinct density minimum sizes')
    if value['timezone'] != 'America/New_York':
        raise ValueError('This protocol uses America/New_York day strata')
    return json.loads(canonical_json(value))


def feature_vector(window: dict) -> list:
    return canonical_feature_vector(window)


def content_digest(value: dict) -> str:
    """Canonical JSON digest without allocating another full Unicode report string."""
    digest = hashlib.sha256()
    encoder = json.JSONEncoder(sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    for chunk in encoder.iterencode(value):
        digest.update(chunk.encode('utf-8'))
    return digest.hexdigest()


def prepare_snapshot(report: dict, config: dict) -> tuple[dict, list]:
    """Validate the complete input before fitting; expose every omitted population."""
    config = configuration(config)
    if report.get('reportVersion') != 1 or report.get('methodVersion') != 'quality-v1':
        raise ValueError('A version-1 canonical quality report is required')
    spec = AnalysisSpec(**report['spec'])
    policy = QualityPolicy(**report['qualityPolicy'])
    if spec.quality_policy_version != policy.version:
        raise ValueError('Quality policy and specification differ')
    windows = report['windows']
    if not isinstance(windows, list) or len(windows) > config['maximumInputWindows']:
        raise ValueError('Input window budget exceeded')
    participant = report['sourceInventory']['acquisition']['participant_id']
    if not isinstance(participant, str) or not participant:
        raise ValueError('An explicitly scoped participant is required')
    groups, seen, contexts = defaultdict(list), set(), {}
    last_end = {}
    flagged = Counter()
    for window in sorted(windows, key=lambda w: (w['startMs'], w['acquisitionGroupId'])):
        context = AcquisitionContext(**window['acquisition'])
        group = window['acquisitionGroupId']
        if (context.participant_id != participant or context.device_id is None
                or group != context.group_id or window['windowId'] in seen
                or window['qualityAnalysisIdentity'] != report['analysisIdentity']):
            raise ValueError('Window scope, identity or acquisition provenance is inconsistent')
        start, end = window['startMs'], window['endMsExclusive']
        if (type(start) is not int or type(end) is not int or start < 0 or end <= start
                or end-start != spec.window_seconds*1000
                or window['sampleCount'] != spec.sample_rate*spec.window_seconds
                or window['spec'] != report['spec'] or context.sample_rate != spec.sample_rate
                or start < last_end.get(group, -1)):
            raise ValueError('Windows must be valid non-overlapping elapsed intervals')
        last_end[group] = end
        seen.add(window['windowId'])
        contexts[group] = asdict(context)
        f, flags = window['features'], window['qualityFlags']
        if (not valid_features(f) or any(v < 0 or v > 1 for v in f['bandShares'])
                or not isinstance(flags, list) or any(not isinstance(v, str) for v in flags)):
            raise ValueError('Invalid feature or screening payload')
        if flags:
            flagged[group] += 1
        else:
            groups[group].append(window)
    if len(contexts) > config['maximumGroups']:
        raise ValueError('Acquisition group budget exceeded; select a narrower quality snapshot')
    coverage = report['coverage']
    if (coverage['computedWindows'] != len(windows)
            or coverage['flaggedWindows'] != sum(flagged.values())
            or coverage['unflaggedWindows'] != len(windows)-sum(flagged.values())
            or coverage['computedRecordSeconds'] != len(windows)*spec.window_seconds
            or coverage['sourceRecords'] != report['sourceInventory']['recordCount']
            or coverage['sourceRecords'] != sum(coverage[k] for k in ('excludedRecords',
                'computedRecordSeconds', 'featureFailureRecordSeconds', 'boundaryRemainderRecordSeconds'))):
        raise ValueError('Quality coverage does not reconcile')
    zone = ZoneInfo(config['timezone'])
    prepared = []
    for group in sorted(contexts):
        eligible = groups[group]
        spaced, prior = [], None
        for window in eligible:
            if prior is None or window['startMs']-prior >= config['minimumStartSpacingMs']:
                spaced.append(window)
                prior = window['startMs']
        days = defaultdict(list)
        for w in spaced:
            day = datetime.fromtimestamp(w['startMs']/1000, timezone.utc).astimezone(zone).date().isoformat()
            days[day].append(w)
        # Allocate one slot per day in rotation, then spread each day's slots over its span.
        quotas = dict.fromkeys(sorted(days), 0)
        remaining = min(len(spaced), config['maximumSelectedPerGroup'])
        while remaining:
            for day in quotas:
                if remaining and quotas[day] < len(days[day]):
                    quotas[day] += 1
                    remaining -= 1
        selected = []
        for day, n in quotas.items():
            indexes = np.linspace(0, len(days[day])-1, n, dtype=int) if n else []
            selected.extend(days[day][int(i)] for i in indexes)
        selected.sort(key=lambda w: w['startMs'])
        prepared.append({'acquisitionGroupId': group, 'acquisition': contexts[group],
                         'computedWindows': len(eligible)+flagged[group],
                         'qualityExcludedWindows': flagged[group], 'eligibleWindows': len(eligible),
                         'spacingOmittedWindows': len(eligible)-len(spaced),
                         'budgetOmittedWindows': len(spaced)-len(selected),
                         'selectedWindows': len(selected), 'selectedDays': quotas, 'windows': selected})
    snapshot = {'qualityAnalysisIdentity': report['analysisIdentity'],
                'qualityContentRevision': content_digest(report), 'sourceRevision': report['sourceInventory']['sourceRevision'],
                'participantId': participant, 'qualityPolicy': report['qualityPolicy'],
                'qualitySpec': report['spec'], 'sourceCoverage': coverage,
                'computedWindows': len(windows), 'qualityExcludedWindows': sum(flagged.values()),
                'eligibleWindows': len(windows)-sum(flagged.values())}
    return snapshot, prepared


def summarize_assignment(labels, windows) -> dict:
    counts = Counter(int(x) for x in labels)
    clusters = []
    for label in sorted(k for k in counts if k >= 0):
        chosen = [w for w, x in zip(windows, labels) if x == label]
        clusters.append({'groupNumber': label, 'windows': len(chosen),
                         'durationMs': sum(w['endMsExclusive']-w['startMs'] for w in chosen)})
    return {'clusters': clusters, 'groupCount': len(clusters),
            'algorithmUnassignedWindows': sum(n for k, n in counts.items() if k < 0),
            'algorithmUnassignedFraction': sum(n for k, n in counts.items() if k < 0)/len(windows),
            'assignments': [{'windowId': w['windowId'], 'groupNumber': int(x) if x >= 0 else None}
                            for w, x in zip(windows, labels)]}


def fit_group(group: dict, config: dict) -> dict:
    windows = group['windows']
    result = {k: v for k, v in group.items() if k != 'windows'}
    result['selectedIntervals'] = [{k: w[k] for k in ('windowId', 'startMs', 'endMsExclusive')}
                                   for w in windows]
    result['candidates'] = []
    if len(windows) < config['minimumSelectedPerGroup']:
        result['status'] = 'insufficient_selected_windows'
        return result
    raw = np.asarray([feature_vector(w) for w in windows], dtype=float)
    median = np.median(raw, axis=0)
    scale = np.maximum(np.percentile(raw, 75, axis=0)-np.percentile(raw, 25, axis=0), 1e-6)
    x = (raw-median)/scale
    result['scaling'] = {'median': median.tolist(), 'iqrScale': scale.tolist(), 'floor': 1e-6,
                         'referenceWindows': len(windows), 'scope': 'selected snapshot; not held-out evaluation'}
    if np.max(np.ptp(x, axis=0)) <= 1e-10:
        result['status'] = 'no_feature_variation'
        return result
    result['status'] = 'compared_no_default_selected'
    models = []
    for size in config['densityMinimumSizes']:
        if size <= len(x) and config['densityMinimumSamples'] <= len(x):
            params = {'min_cluster_size': size, 'min_samples': config['densityMinimumSamples'],
                      'allow_single_cluster': True, 'n_jobs': 1}
            models.append(('hdbscan', params, HDBSCAN(**params)))
    for k in range(1, min(config['maximumComponents'], len(np.unique(x, axis=0)))+1):
        params = {'n_components': k, 'covariance_type': 'diag', 'random_state': config['seed'],
                  'n_init': 3, 'reg_covar': 1e-4, 'max_iter': 200}
        models.append(('gmm', params, GaussianMixture(**params)))
        params = {'n_clusters': k, 'random_state': config['seed'], 'n_init': 10, 'max_iter': 300}
        models.append(('kmeans', params, KMeans(**params)))
    for method, params, model in models:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always', ConvergenceWarning)
            labels = model.fit_predict(x)
        candidate = {'method': method, 'parameters': params,
                     'convergenceWarning': any(issubclass(w.category, ConvergenceWarning) for w in caught),
                     **summarize_assignment(labels, windows)}
        if method == 'gmm':
            candidate.update(bic=float(model.bic(x)), converged=bool(model.converged_))
        elif method == 'kmeans':
            candidate['inertia'] = float(model.inertia_)
        result['candidates'].append(candidate)
    return result


def compare_models(report: dict, config: dict) -> dict:
    config = configuration(config)
    snapshot, groups = prepare_snapshot(report, config)
    with threadpool_limits(limits=1):
        environment = {'python': platform.python_version(), 'numpy': np.__version__,
                       'scipy': scipy.__version__, 'sklearn': sklearn.__version__,
                       'threadpools': [{k: p.get(k) for k in ('internal_api', 'version', 'architecture', 'num_threads')}
                                       for p in threadpool_info()]}
        implementation = {'spectralOwnerSha256': hashlib.sha256(Path(__file__).with_name('spectral_features.py').read_bytes()).hexdigest(),
                          'moduleSha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                          'provenanceSha256': hashlib.sha256(Path(__file__).with_name('analysis_provenance.py').read_bytes()).hexdigest(),
                          'qualitySha256': hashlib.sha256(Path(__file__).with_name('analysis_quality.py').read_bytes()).hexdigest(),
                          'environment': environment}
        result = {'version': 1, 'methodVersion': VERSION, 'snapshot': snapshot, 'configuration': config,
                  'features': FEATURES, 'bandSharePseudocount': 1e-6, 'implementation': implementation,
                  'groups': [fit_group(g, config) for g in groups],
                  'limitations': ['Unnamed exploratory signal groups are not activities or mental states',
                      'Only selected intervals are fitted; omitted windows are not assigned',
                      'Spacing reduces adjacency but does not establish independent observations',
                      'Day balancing changes the sampled population relative to duration weighting',
                      'BIC and inertia are in-sample diagnostics, not independent evidence',
                      'No default method selected; sensitivity, recurrence and confounds remain P3-W2',
                      'Unflagged does not establish artifact-free EEG; no clinical interpretation']}
    result['comparisonIdentity'] = fingerprint(result)
    canonical_json(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('quality_report', type=Path)
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--output', type=Path, required=True, help='New private JSON file; never overwritten')
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise ValueError('Output already exists')
        report = compare_models(json.loads(args.quality_report.read_text(encoding='utf-8')),
                                json.loads(args.config.read_text(encoding='utf-8')))
        with args.output.open('x', encoding='utf-8') as output:
            output.write(canonical_json(report)+'\n')
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        parser.exit(1, f'Comparison failed: {error}\n')


if __name__ == '__main__':
    main()
