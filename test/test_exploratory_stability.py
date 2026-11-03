"""Independent known populations and guardrails for exploratory robustness."""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

AVAILABLE = importlib.util.find_spec('sklearn') and importlib.util.find_spec('numpy')
if AVAILABLE:
    import numpy as np
    from threadpoolctl import threadpool_limits
    import exploratory_stability as stability
    from analysis_provenance import AcquisitionContext, AnalysisSpec
    from analysis_quality import QualityPolicy


@unittest.skipUnless(AVAILABLE, 'Optional numerical environment is not installed')
class StabilityTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(stability.DEFAULT_CONFIG.read_text())
        self.sampling = json.loads(stability.base.DEFAULT_CONFIG.read_text())
        self.sampling['maximumComponents'] = 2

    def report(self, days=3, each=40):
        rng = np.random.default_rng(907)
        context = AcquisitionContext('P', 'device', 'awear-cloud', 'right', 256, 'RIGHT_TEMP', 'cloud-timestamp')
        spec = asdict(AnalysisSpec(quality_policy_version=QualityPolicy().version))
        windows = []
        for day in range(days):
            for slot in range(each):
                center = -1 if slot % 2 == 0 else 1
                ratios = center+rng.normal(0, .025, 4)
                shares = 10**np.r_[ratios[:3], 0, ratios[3]]
                shares /= shares.sum()
                power = float(center+rng.normal(0, .025))
                start = 1793700000000+day*86400000+slot*60000
                features = {'acRms': float(10**(power/2)), 'peakToPeak': 4., 'maxStep': .1,
                    'linearSlopePerSecond': 0., 'linearTrendFraction': .01,
                    'mainsLikePowerFraction': .01, 'power1to40Hz': float(10**power),
                    'log10Power1to40Hz': power, 'bandPowers': (shares*10**power).tolist(), 'bandShares': shares.tolist()}
                windows.append({'windowId': f'{day}-{slot}', 'startMs': start, 'endMsExclusive': start+4000,
                    'acquisition': asdict(context), 'acquisitionGroupId': context.group_id, 'spec': spec,
                    'sampleCount': 1024, 'qualityAnalysisIdentity': 'quality', 'features': features, 'qualityFlags': []})
        n = len(windows)
        return {'reportVersion': 1, 'methodVersion': 'quality-v1', 'analysisIdentity': 'quality',
            'sourceInventory': {'acquisition': asdict(context), 'sourceRevision': 'source', 'recordCount': n*4},
            'spec': spec, 'qualityPolicy': asdict(QualityPolicy()), 'windows': windows,
            'coverage': {'computedWindows': n, 'flaggedWindows': 0, 'unflaggedWindows': n,
                'sourceRecords': n*4, 'computedRecordSeconds': n*4, 'excludedRecords': 0,
                'featureFailureRecordSeconds': 0, 'boundaryRemainderRecordSeconds': 0}}

    def test_noise_and_degenerate_partitions_cannot_manufacture_stability(self):
        agree = stability.partition_agreement
        for a, b, reason in [([-1]*40, [-1]*40, 'insufficient_joint_assigned_support'),
                              ([0]*40, [0]*40, 'fewer_than_two_joint_partitions'),
                              ([0]*20+[1]*20, [-1]*30+[0]*5+[1]*5, 'insufficient_joint_assigned_support')]:
            result = agree(a, b, 20)
            self.assertIsNone(result['adjustedRandIndex'])
            self.assertEqual(result['status'], reason)
        labels = [0]*20+[1]*20
        result = agree(labels, [7]*20+[3]*20, 20)
        self.assertEqual(result['adjustedRandIndex'], 1)
        self.assertEqual(result['assignedSetJaccard'], 1)
        with self.assertRaises(ValueError):agree(labels, [1], 20)

    def test_block_exclusions_and_bounded_day_scope_are_explicit(self):
        windows = self.report(days=5)['windows']
        days = [f'day-{i//40}' for i in range(len(windows))]
        changes, scope = stability.perturbations(windows, days, self.config)
        self.assertEqual(scope['availableDays'], 5)
        self.assertEqual(scope['evaluatedDays'], ['day-0', 'day-2', 'day-4'])
        self.assertEqual(scope['omittedDays'], ['day-1', 'day-3'])
        for change in changes:
            keep = set(change['trainingIndices'])
            self.assertEqual(len(keep)+change['excludedWindows'], len(windows))
            for i, w in enumerate(windows):
                excluded = (((w['startMs']//1800000) % 4 == change['droppedModulo4'] or
                              ((w['endMsExclusive']-1)//1800000) % 4 == change['droppedModulo4'])
                            if change['kind'] == 'utc_block_drop' else days[i] == change['excludedDay'])
                self.assertEqual(i not in keep, excluded)

    def test_crossing_windows_cannot_leak_into_excluded_blocks_or_days(self):
        midnight = int(datetime.fromisoformat('2026-11-03T00:00:00-05:00').timestamp()*1000)
        windows = [{'startMs': midnight-2000, 'endMsExclusive': midnight+2000},
                   {'startMs': midnight+60000, 'endMsExclusive': midnight+64000}]
        changes, _ = stability.perturbations(windows, ['2026-11-02', '2026-11-03'], self.config)
        new_day = next(p for p in changes if p.get('excludedDay') == '2026-11-03')
        self.assertEqual(new_day['trainingIndices'], [])
        for change in changes[:2]:
            fold = change['droppedModulo4']
            if (midnight//1800000) % 4 == fold:
                self.assertNotIn(0, change['trainingIndices'])

    def test_day_only_confound_and_recurring_population_are_distinguished(self):
        windows = self.report()['windows'];days = [f'day-{i//40}' for i in range(120)]
        day_labels = [i//40 for i in range(120)]
        confined = stability.condition_summary(day_labels, windows, days, self.config)
        self.assertAlmostEqual(confined['dayAssociationAdjustedMutualInformation'], 1)
        self.assertTrue(all(g['daysAtMinimumSupport'] == 1 for g in confined['groups']))
        recurring = stability.condition_summary([i % 2 for i in range(120)], windows, days, self.config)
        self.assertLess(abs(recurring['dayAssociationAdjustedMutualInformation']), .05)
        self.assertTrue(all(g['daysAtMinimumSupport'] == 3 for g in recurring['groups']))
        self.assertEqual(sum(g['windows'] for g in recurring['groups']), 120)

    def test_training_only_scaling_and_novel_day_transfer(self):
        candidate = {'method': 'kmeans', 'parameters': {'n_clusters': 2, 'random_state': 42, 'n_init': 10}}
        raw = np.r_[np.zeros((20, 2)), np.ones((20, 2))]
        with threadpool_limits(limits=1):
            fitted, labels, model = stability.fit_candidate(raw, candidate, 20)
            self.assertEqual(fitted['scaling']['median'], [.5, .5])
            self.assertEqual(fitted['scaling']['iqrScale'], [1., 1.])
            result = stability.transfer_diagnostics(raw, np.full((20, 2), 100.), fitted, model, 'kmeans')
        self.assertEqual(result['excludedDayBeyondReferenceFraction'], 1)
        self.assertEqual(stability.transfer_diagnostics(raw, raw, fitted, model, 'hdbscan')['status'],
                         'unsupported_density_out_of_sample_prediction')

    def test_power_only_groups_disappear_in_spectral_ablation(self):
        candidate = {'method': 'gmm', 'parameters': {'n_components': 2, 'random_state': 42}}
        raw = np.c_[np.r_[np.full(30, -1.), np.ones(30)], np.zeros((60, 4))]
        with threadpool_limits(limits=1):
            full, _, _ = stability.fit_candidate(raw, candidate, 20)
            spectral, labels, _ = stability.fit_candidate(raw[:, 1:], candidate, 20)
        self.assertEqual(full['assignment']['groupCount'], 2)
        self.assertEqual(spectral['status'], 'no_feature_variation')
        self.assertIsNone(labels)

    def test_recurring_known_groups_reproduce_with_refit_support_and_identity(self):
        report = self.report();before = deepcopy(report)
        result = stability.analyze_stability(report, self.sampling, self.config)
        self.assertEqual(result, stability.analyze_stability(report, self.sampling, self.config))
        self.assertEqual(before, report)
        self.assertIsNone(result['defaultMethod'])
        group = result['groups'][0]
        self.assertLessEqual(group['maximumPlannedFits'], 142)
        self.assertEqual(group['recordingConditions']['sessionStatus'], 'unknown_not_recorded')
        candidates = group['views']['with_power']['candidates']
        target = next(c for c in candidates if c['method'] == 'gmm' and c['parameters']['n_components'] == 2 and c['parameters']['random_state'] == 42)
        self.assertTrue(all(g['daysAtMinimumSupport'] == 3 for g in target['conditions']['groups']))
        refits = [r for r in group['views']['with_power']['refits'] if r['candidateId'] == target['candidateId']]
        comparable = [r['agreement']['adjustedRandIndex'] for r in refits if r.get('agreement', {}).get('adjustedRandIndex') is not None]
        self.assertTrue(comparable)
        self.assertTrue(all(value > .95 for value in comparable))
        changed = dict(self.config, temporalBlockMinutes=60)
        self.assertNotEqual(result['stabilityIdentity'], stability.analyze_stability(report, self.sampling, changed)['stabilityIdentity'])

    def test_empty_flagged_and_small_inputs_have_no_forced_groups(self):
        for report in (self.report(days=0), self.report(each=1)):
            result = stability.analyze_stability(report, self.sampling, self.config)
            self.assertTrue(all(g['completedFits'] == 0 for g in result['groups']))
        report = self.report(each=4)
        for w in report['windows']:w['qualityFlags'] = ['flatline']
        report['coverage'].update(flaggedWindows=12, unflaggedWindows=0)
        result = stability.analyze_stability(report, self.sampling, self.config)
        self.assertEqual(result['snapshot']['qualityExcludedWindows'], 12)
        self.assertEqual(result['groups'][0]['selectedWindows'], 0)

    def test_noop_block_is_not_reported_as_successful_stability(self):
        report = self.report(days=1, each=20)
        result = stability.analyze_stability(report, self.sampling, self.config)
        group = result['groups'][0]
        noops = {p['id'] for p in group['perturbations'] if p['excludedWindows'] == 0}
        self.assertTrue(noops)
        for row in group['views']['with_power']['refits']:
            if row['perturbationId'] in noops:
                self.assertEqual(row['status'], 'no_windows_excluded')
                self.assertNotIn('agreement', row)

    def test_bad_scope_and_configuration_fail_before_fitting(self):
        report = self.report();report['windows'][0]['acquisition']['participant_id'] = 'other'
        with self.assertRaises(ValueError):stability.analyze_stability(report, self.sampling, self.config)
        for config in (dict(self.config, maximumExcludedDays=4), dict(self.config, minimumSharedWindows=1),
                       dict(self.config, version=True), dict(self.config, surprise=1)):
            with self.assertRaises(ValueError):stability.configuration(config)

    def test_nonconverged_fit_is_not_used_for_transfer(self):
        raw = np.random.default_rng(991).normal(size=(80, 4))
        candidate = {'method': 'gmm', 'parameters': {'n_components': 2, 'random_state': 42, 'max_iter': 1}}
        with threadpool_limits(limits=1):
            fit, _, model = stability.fit_candidate(raw, candidate, 20)
        self.assertEqual(fit['status'], 'nonconverged')
        self.assertEqual(stability.transfer_diagnostics(raw, raw, fit, model, 'gmm')['status'], 'training_fit_unavailable')

    def test_distinct_devices_stay_in_separate_diagnostic_groups(self):
        report = self.report(days=1, each=4)
        context = AcquisitionContext('P', 'second-device', 'awear-cloud', 'right', 256, 'RIGHT_TEMP', 'cloud-timestamp')
        for w in report['windows'][2:]:
            w['acquisition'] = asdict(context);w['acquisitionGroupId'] = context.group_id
        result = stability.analyze_stability(report, self.sampling, self.config)
        self.assertEqual(len(result['groups']), 2)
        self.assertTrue(all(g['selectedWindows'] == 2 for g in result['groups']))
        self.assertEqual({g['acquisition']['device_id'] for g in result['groups']}, {'device', 'second-device'})

    def test_cli_roundtrip_and_existing_output_preserve_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory);source = root/'source.json';target = root/'result.json'
            source.write_text(json.dumps(self.report(days=0)), encoding='utf-8');before = source.read_bytes()
            cmd = [sys.executable, stability.__file__, str(source), '--output', str(target)]
            run = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(run.returncode, 0, run.stderr)
            output = target.read_bytes()
            run = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
            self.assertNotEqual(run.returncode, 0)
            self.assertEqual(target.read_bytes(), output)
            self.assertEqual(source.read_bytes(), before)


if __name__ == '__main__':unittest.main()
