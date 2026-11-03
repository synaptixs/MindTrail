"""Deterministic, label-free readiness facts from the canonical quality population."""
from __future__ import annotations
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import hashlib
from analysis_provenance import canonical_json, fingerprint

VERSION = 1
MAX_BYTES = 2_000_000


def build(report, participant, zone, *, demo=False, check=lambda: None):
    inventory = report['sourceInventory']
    if inventory['acquisition']['participant_id'] != participant:
        raise ValueError('Discovery participant differs from source')
    tz = ZoneInfo(zone)
    groups = {}
    for i, window in enumerate(report['windows']):
        if i % 256 == 0: check()
        acquisition = window['acquisition']
        if acquisition['participant_id'] != participant:
            raise ValueError('Discovery window differs from source participant')
        key = window['acquisitionGroupId']
        if key != fingerprint(acquisition): raise ValueError('Invalid discovery acquisition identity')
        group = groups.setdefault(key, {'acquisitionGroupId': key, 'acquisition': acquisition,
            'computedWindows': 0, 'flaggedWindows': 0, 'unflaggedWindows': 0,
            'unflaggedMs': 0, 'firstWindowMs': window['startMs'],
            'lastWindowEndMsExclusive': window['endMsExclusive'], 'dates': Counter()})
        if len(groups) > 1000: raise ValueError('Discovery exceeds 1000 acquisition contexts')
        flagged = bool(window['qualityFlags'])
        group['computedWindows'] += 1
        group['flaggedWindows' if flagged else 'unflaggedWindows'] += 1
        if not flagged: group['unflaggedMs'] += window['endMsExclusive'] - window['startMs']
        group['firstWindowMs'] = min(group['firstWindowMs'], window['startMs'])
        group['lastWindowEndMsExclusive'] = max(group['lastWindowEndMsExclusive'], window['endMsExclusive'])
        # Dates are observed support, not independent repetitions; a midnight-crossing
        # window contributes to both touched dates, without duplicating window totals.
        a = datetime.fromtimestamp(window['startMs']/1000, timezone.utc).astimezone(tz).date()
        b = datetime.fromtimestamp((window['endMsExclusive']-1)/1000, timezone.utc).astimezone(tz).date()
        group['dates'][a.isoformat()] += 1
        if a != b: group['dates'][b.isoformat()] += 1
        if len(group['dates']) > 20000: raise ValueError('Discovery exceeds date bound')
    contexts = []
    for key in sorted(groups):
        group = groups[key]
        group['dates'] = [{'date': date, 'touchingWindows': count} for date, count in sorted(group['dates'].items())]
        group['readiness'] = 'ready_for_exploration' if group['unflaggedWindows'] else 'no_unflagged_windows'
        contexts.append(group)
    coverage = report['coverage']
    if sum(g['computedWindows'] for g in contexts) != coverage['computedWindows']:
        raise ValueError('Discovery population does not reconcile')
    if sum(g['flaggedWindows'] for g in contexts) != coverage['flaggedWindows']:
        raise ValueError('Discovery screening population does not reconcile')
    value = {'version': VERSION, 'participantId': participant, 'demo': bool(demo),
        'analysisIdentity': report['analysisIdentity'], 'sourceRevision': inventory['sourceRevision'],
        'methodIdentity': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'timezone': zone, 'requestedRange': inventory['range'],
        'observedExtent': {'startMs': inventory['firstTimestampMs'],
            'endMsExclusive': None if inventory['lastTimestampMs'] is None else inventory['lastTimestampMs']+1000},
        'coverage': coverage, 'contexts': contexts, 'contextCount': len(contexts),
        'population': 'All computed canonical quality windows; no activity-label or clustering filter',
        'readiness': 'no_recordings' if not inventory['recordCount'] else
            'ready_for_exploration' if coverage['unflaggedWindows'] else 'no_unflagged_windows',
        'labelsRequired': False, 'inference': {'status': 'not_assessed',
            'reason': 'Discovery support does not establish independent repeatability, activity effects or confirmation.'},
        'missingContext': {'contact': 'unmeasured',
            'unknownSessionWindows': sum(g['computedWindows'] for g in contexts if g['acquisition']['session_id'] is None),
            'activityContext': 'optional; not inspected or inferred by this report'},
        'limitations': ['Unflagged does not establish artifact-free EEG.',
            'Dates and adjacent windows are not independent experiments or inferred sessions.',
            'Contexts with no computed windows are represented in source exclusion accounting, not as feature support.',
            'Observed extent is not device-wearing duration; record-second counts are not elapsed union coverage.',
            'Discovery does not reserve confirmation data or identify mental states.']}
    value['discoveryIdentity'] = fingerprint(value)
    if len(canonical_json(value).encode('utf-8')) > MAX_BYTES:
        raise ValueError('Discovery report exceeds its output bound')
    check()
    return value


def validate(value, participant, analysis_identity):
    identity = value.get('discoveryIdentity')
    if (value.get('version') != VERSION or value.get('participantId') != participant
            or value.get('analysisIdentity') != analysis_identity
            or identity != fingerprint({k: v for k, v in value.items() if k != 'discoveryIdentity'})):
        raise ValueError('Invalid discovery report identity')
    return value


def read(manager, participant):
    from analysis_manager import read_json
    result = {'status': 'unavailable', 'participantId': participant, 'labelsRequired': False}
    if not participant: return dict(result, status='no_participant')
    with manager.lock:
        try:
            pointer = read_json(manager.root/(manager.scope(participant)+'.json'))
            if pointer['signature'] != manager.signature(): return dict(result, status='stale')
            directory = manager.directory(pointer['id'])
            summary = read_json(directory/'summary.json')
            value = validate(read_json(directory/'discovery.json', limit=MAX_BYTES), participant, summary['analysisIdentity'])
            if pointer['signature'] != manager.signature(): return dict(result, status='stale')
            return dict(value, status='current')
        except (OSError, ValueError, TypeError, KeyError): return result
