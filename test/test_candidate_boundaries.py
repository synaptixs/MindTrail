"""Independent small-series oracle and provisional scope/lifecycle checks."""
import importlib.util
import itertools
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HAS_NUMPY=importlib.util.find_spec('numpy') is not None
if HAS_NUMPY:import numpy as np
from candidate_boundaries import Cost, daily_candidates, segment, settings
from analysis_manager import AnalysisManager, remove_quality_payloads


def oracle(values, weights, edges, minimum, penalty):
    best = float('inf')
    for mask in itertools.product((False, True), repeat=len(values)-1):
        ends = [i+1 for i, flag in enumerate(mask) if flag]+[len(values)]
        begin, total = 0, penalty*(len(ends)-1)
        for end in ends:
            if edges[end]-edges[begin] < minimum:break
            subset = values[begin:end];w = weights[begin:end]
            mean = np.average(subset, weights=w, axis=0)
            total += float(np.sum((subset-mean)**2*w[:,None]))
            begin = end
        else:best = min(best,total)
    return best


class BoundaryTests(unittest.TestCase):
    @unittest.skipUnless(HAS_NUMPY,'optional NumPy')
    def test_pelt_matches_exhaustive_oracle_with_minimum_duration_and_weights(self):
        rng = np.random.default_rng(193)
        for n in range(2,10):
            for minimum in (1,3,7):
                for penalty in (.1,2,10):
                    values = rng.normal(size=(n,2));weights = rng.integers(1,4,size=n)
                    edges = np.r_[0,np.cumsum(rng.integers(1,5,size=n))]
                    cost = Cost(values,weights,edges)
                    ends = segment(cost,minimum,penalty)
                    expected = oracle(values,weights,edges,minimum,penalty)
                    if np.isinf(expected):self.assertEqual(ends,[]);continue
                    actual = sum(float(cost.at(a,b)) for a,b in zip([0]+ends[:-1],ends))+penalty*(len(ends)-1)
                    self.assertAlmostEqual(actual,expected,places=8)
                    baseline = segment(cost,minimum,penalty,'baseline')
                    baseline_cost = sum(float(cost.at(a,b)) for a,b in zip([0]+baseline[:-1],baseline))+penalty*(len(baseline)-1)
                    self.assertLessEqual(actual,baseline_cost+1e-8)

    @unittest.skipUnless(HAS_NUMPY,'optional NumPy')
    def test_constant_and_known_shift_without_forced_splits(self):
        for method in ('baseline','pelt'):
            self.assertEqual(segment(Cost(np.zeros((40,2)),np.ones(40),np.arange(41)),5,2,method),[40])
            values=np.r_[np.zeros((20,2)),np.ones((20,2))*3]
            self.assertEqual(segment(Cost(values,np.ones(40),np.arange(41)),5,2,method),[20,40])
            self.assertEqual(segment(Cost(values,np.ones(40),np.arange(41)),41,2,method),[])

    def report(self):
        def window(i,group='G',flag=False):
            return {'startMs':i*4000,'endMsExclusive':(i+1)*4000,'acquisitionGroupId':group,
                'acquisition':{'placement':'right','participant_id':'P','device_id':group},
                'qualityFlags':['flatline'] if flag else [],
                'features':{'log10Power1to40Hz':1 if i<20 else 2,'bandShares':[.1,.2,.5,.2]}}
        return {'analysisIdentity':'source','windows':[window(i) for i in range(40)]}

    @unittest.skipUnless(HAS_NUMPY,'optional NumPy')
    def test_gap_flags_acquisition_resolution_and_deterministic_proposals(self):
        report=self.report();report['windows'][10]['qualityFlags']=['flatline']
        for w in report['windows'][30:]:w['acquisitionGroupId']='other'
        report['windows'].pop(15)
        before=json.dumps(report,sort_keys=True)
        config={'minimumSeconds':12,'sensitivity':'medium'}
        result=daily_candidates(report,'P',config)
        self.assertEqual(result,daily_candidates(report,'P',config))
        self.assertEqual(json.dumps(report,sort_keys=True),before)
        day=result['1969-12-31'];self.assertEqual(day['eligibleWindows'],38);self.assertEqual(day['excludedWindows'],1)
        self.assertEqual([(r['startMs'],r['endMsExclusive']) for r in day['runs']],[(0,40000),(44000,60000),(64000,120000),(120000,160000)])
        self.assertNotEqual(day['candidateIdentity'],daily_candidates(report,'P',dict(config,sensitivity='high'))['1969-12-31']['candidateIdentity'])
        with patch('candidate_boundaries.MAX_BINS',8):
            binned=daily_candidates(self.report(),'P',config)['1969-12-31']['runs'][0]
        self.assertEqual(binned['bins'],8);self.assertEqual(binned['maxResolutionMs'],20000)
        for method in binned['methods'].values():
            self.assertTrue(all(s['endMsExclusive']-s['startMs']>=12000 for s in method['segments']))

    @unittest.skipUnless(HAS_NUMPY,'optional NumPy')
    def test_day_boundary_and_cancellation(self):
        report=self.report()
        # Both occurrences of NY's fall-back hour belong to one real-time day.
        from datetime import datetime
        start=int(datetime.fromisoformat('2026-11-01T01:59:20-04:00').timestamp()*1000)
        for w in report['windows']:
            w['startMs']+=start;w['endMsExclusive']+=start
        result=daily_candidates(report,'P',{'minimumSeconds':12,'sensitivity':'low'})
        self.assertEqual(list(result),['2026-11-01'])
        def cancel():raise RuntimeError('stop')
        with self.assertRaisesRegex(RuntimeError,'stop'):daily_candidates(report,'P',{'minimumSeconds':12,'sensitivity':'low'},cancel)

    def test_invalid_settings_and_stdlib_safe_import(self):
        for value in ({},{'minimumSeconds':True,'sensitivity':'low'},{'minimumSeconds':60,'sensitivity':[]},{'minimumSeconds':11,'sensitivity':'low'}):
            with self.assertRaises(ValueError):settings(value)
        self.assertIsNone(AnalysisManager.settings({})['candidateBoundaries'])

    def test_cleanup_removes_only_derived_sidecars(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name in ('boundaries-index.json','boundaries-2026-11-01.json','boundaries-2026-11-02.tmp','quality-index.json','notes.json','boundaries-secret.json'):(root/name).write_text('{}')
            remove_quality_payloads(root)
            self.assertEqual(sorted(p.name for p in root.iterdir()),['boundaries-secret.json','notes.json'])
