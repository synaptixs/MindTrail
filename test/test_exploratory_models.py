"""Known signal populations, scope checks and deterministic offline comparison."""
from copy import deepcopy
from contextlib import closing
from dataclasses import asdict
import importlib.util
import json
import sqlite3
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

AVAILABLE = importlib.util.find_spec('sklearn') and importlib.util.find_spec('numpy')
if AVAILABLE:
    import numpy as np
    from sklearn.metrics import adjusted_rand_score
    import exploratory_models as models
    from analysis_provenance import AcquisitionContext, AnalysisSpec
    from analysis_quality import QualityPolicy


@unittest.skipUnless(AVAILABLE, 'Optional analysis environment is not installed')
class ExplorationTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(models.DEFAULT_CONFIG.read_text())

    def report(self, n=100, constant=False):
        rng = np.random.default_rng(55)
        context = AcquisitionContext('P', 'D', 'awear-cloud', 'right', 256, 'RIGHT_TEMP', 'cloud-timestamp')
        windows = []
        spec = asdict(AnalysisSpec(quality_policy_version=QualityPolicy().version))
        for i in range(n):
            center = -1 if i < n//2 else 1
            logits = np.r_[center+rng.normal(0, .03, 3), 0., center+rng.normal(0, .03)]
            shares = 10**logits
            shares /= shares.sum()
            f = {'acRms': 1., 'peakToPeak': 2., 'maxStep': .2, 'linearSlopePerSecond': 0.,
                 'linearTrendFraction': 0., 'mainsLikePowerFraction': 0., 'power1to40Hz': 1.,
                 'log10Power1to40Hz': 0. if constant else float(center+rng.normal(0, .03)),
                 'bandPowers': shares.tolist(), 'bandShares': shares.tolist()}
            if constant:
                f['bandShares'] = [.2]*5
            start = 1793700000000+i*60000
            windows.append({'windowId': str(i), 'qualityAnalysisIdentity': 'Q',
                            'acquisitionGroupId': context.group_id, 'acquisition': asdict(context),
                            'startMs': start, 'endMsExclusive': start+4000,
                            'sampleCount': 1024, 'spec': spec,
                            'features': f, 'qualityFlags': []})
        return {'reportVersion': 1, 'methodVersion': 'quality-v1', 'analysisIdentity': 'Q',
                'sourceInventory': {'acquisition': asdict(context), 'sourceRevision': 'S', 'recordCount': n*4},
                'spec': spec, 'qualityPolicy': asdict(QualityPolicy()),
                'coverage': {'computedWindows': n, 'flaggedWindows': 0, 'unflaggedWindows': n,
                             'sourceRecords': n*4, 'computedRecordSeconds': n*4, 'excludedRecords': 0,
                             'featureFailureRecordSeconds': 0, 'boundaryRemainderRecordSeconds': 0},
                'windows': windows}

    def test_known_separated_populations_and_one_component_baseline(self):
        r = models.compare_models(self.report(), self.config)
        group = r['groups'][0]
        candidates = group['candidates']
        gmm = next(c for c in candidates if c['method'] == 'gmm' and c['parameters']['n_components'] == 2)
        self.assertGreater(adjusted_rand_score([0]*50+[1]*50,
            [a['groupNumber'] for a in gmm['assignments']]), .95)
        self.assertTrue(any(c['method'] == 'gmm' and c['groupCount'] == 1 for c in candidates))
        self.assertTrue(any(c['method'] == 'hdbscan' for c in candidates))
        for c in candidates:
            self.assertEqual(sum(v['windows'] for v in c['clusters'])+c['algorithmUnassignedWindows'], 100)

    def test_constant_small_empty_and_flagged_are_valid_without_forced_groups(self):
        for report, status in [(self.report(constant=True), 'no_feature_variation'),
                               (self.report(3), 'insufficient_selected_windows')]:
            g = models.compare_models(report, self.config)['groups'][0]
            self.assertEqual(g['status'], status)
            self.assertEqual(g['candidates'], [])
        report = self.report()
        for w in report['windows']:w['qualityFlags'] = ['flatline']
        report['coverage'].update(flaggedWindows=100, unflaggedWindows=0)
        g = models.compare_models(report, self.config)['groups'][0]
        self.assertEqual(g['qualityExcludedWindows'], 100)
        self.assertEqual(g['selectedWindows'], 0)
        self.assertEqual(models.compare_models(self.report(0), self.config)['groups'], [])

    def test_repeatability_identity_and_input_preservation(self):
        report = self.report(30)
        self.assertEqual(models.content_digest(report), models.fingerprint(report))
        before = deepcopy(report)
        a = models.compare_models(report, self.config)
        self.assertEqual(a, models.compare_models(report, self.config))
        self.assertEqual(before, report)
        changed = deepcopy(report);changed['sourceInventory']['sourceRevision'] = 'new'
        self.assertNotEqual(a['comparisonIdentity'], models.compare_models(changed, self.config)['comparisonIdentity'])
        config = dict(self.config, seed=43)
        self.assertNotEqual(a['comparisonIdentity'], models.compare_models(report, config)['comparisonIdentity'])
        changed = deepcopy(report);changed['windows'][0]['features']['acRms'] = 2
        self.assertNotEqual(a['snapshot']['qualityContentRevision'], models.compare_models(changed, self.config)['snapshot']['qualityContentRevision'])

    def test_scope_duplicate_overlap_identity_and_coverage_rejected_before_fit(self):
        mutations = [lambda r: r['windows'][0]['acquisition'].update(participant_id='other'),
                     lambda r: r['windows'][0].update(qualityAnalysisIdentity='stale'),
                     lambda r: r['windows'][1].update(windowId='0'),
                     lambda r: r['windows'][1].update(startMs=r['windows'][0]['startMs']),
                     lambda r: r['coverage'].update(flaggedWindows=1),
                     lambda r: r['windows'][0]['features'].update(bandShares=[-1, 0, 0, 0, 2])]
        for mutate in mutations:
            r = self.report();mutate(r)
            with self.assertRaises(ValueError):models.prepare_snapshot(r, self.config)

    def test_acquisition_separation_and_disjoint_omission_counts(self):
        report = self.report(100)
        context = AcquisitionContext('P', 'second', 'awear-cloud', 'right', 256, 'RIGHT_TEMP', 'cloud-timestamp')
        for w in report['windows'][50:]:
            w.update(acquisition=asdict(context), acquisitionGroupId=context.group_id)
        report['windows'][0]['qualityFlags'] = ['mains_like_power']
        report['coverage'].update(flaggedWindows=1, unflaggedWindows=99)
        config = dict(self.config, maximumSelectedPerGroup=20, minimumStartSpacingMs=120000)
        _, groups = models.prepare_snapshot(report, config)
        self.assertEqual(len(groups), 2)
        for g in groups:
            self.assertEqual(g['computedWindows'], g['qualityExcludedWindows']+g['spacingOmittedWindows']+
                             g['budgetOmittedWindows']+g['selectedWindows'])
            starts = [w['startMs'] for w in g['windows']]
            self.assertTrue(all(b-a >= 120000 for a, b in zip(starts, starts[1:])))

    def test_cli_preserves_input_and_refuses_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory);source = root/'quality.json';target = root/'comparison.json'
            source.write_text(json.dumps(self.report(3)), encoding='utf-8')
            before = source.read_bytes()
            command = [sys.executable, str(Path(models.__file__)), str(source), '--output', str(target)]
            run = subprocess.run(command, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(run.returncode, 0, run.stderr)
            output = target.read_bytes()
            run = subprocess.run(command, capture_output=True, text=True, encoding='utf-8')
            self.assertNotEqual(run.returncode, 0)
            self.assertEqual(output, target.read_bytes())
            self.assertEqual(before, source.read_bytes())

    def test_budget_and_invalid_configuration_fail_before_fit(self):
        for config in [dict(self.config, maximumSelectedPerGroup=5000), dict(self.config, seed=True),
                       dict(self.config, densityMinimumSizes=[15, 15]), dict(self.config, surprise=1)]:
            with self.assertRaises(ValueError):models.configuration(config)
        with self.assertRaises(ValueError):
            models.prepare_snapshot(self.report(), dict(self.config, maximumInputWindows=10))

    def test_real_quality_contract_and_density_noise_accounting(self):
        from analysis_quality import analyze_archive
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory)/'source.sqlite3'
            with closing(sqlite3.connect(db)) as connection:
                connection.execute('CREATE TABLE readings(participant_id,timestamp_ms,device_id,sample_count,waveform_json)')
                wave = json.dumps(np.sin(2*np.pi*10*np.arange(256)/256).tolist())
                connection.executemany('INSERT INTO readings VALUES(?,?,?,?,?)',
                                       [('P', i*1000, 'D', 256, wave) for i in range(8)])
                connection.commit()
            before = db.read_bytes()
            report = analyze_archive(db, 'P')
            result = models.compare_models(report, self.config)
            self.assertEqual(result['snapshot']['participantId'], 'P')
            self.assertEqual(result['groups'][0]['acquisition']['placement'], 'right')
            self.assertEqual(before, db.read_bytes())
        summary = models.summarize_assignment(np.array([-1, -1]), self.report(2)['windows'])
        self.assertEqual(summary['algorithmUnassignedFraction'], 1)
        self.assertEqual(summary['clusters'], [])

    def test_sampling_balances_days_and_keeps_acquisition_context(self):
        report = self.report(100)
        for i, w in enumerate(report['windows']):
            if i >= 50:
                w['startMs'] += 86400000
                w['endMsExclusive'] += 86400000
        _, groups = models.prepare_snapshot(report, dict(self.config, maximumSelectedPerGroup=20))
        self.assertEqual(sorted(groups[0]['selectedDays'].values()), [10, 10])
        self.assertEqual(groups[0]['budgetOmittedWindows'], 80)


if __name__ == '__main__':unittest.main()
