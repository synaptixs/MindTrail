"""Explainable, bounded full-feature retrieval. Stdlib only; never assigns labels."""
from __future__ import annotations
from contextlib import closing
import math
import sqlite3

from analysis_provenance import fingerprint
import episode_review
from exploration_access import read_view

VERSION = 1
GUARD_MS = 60_000
LIMIT = 10


def overlaps(start, end, lower, upper):
    return start < upper and lower < end


def annotations(connection, participant):
    labels = []
    for row in connection.execute('SELECT * FROM labels WHERE participant_id=? ORDER BY start_ms,id', (participant,)):
        label = dict(row)
        label['version'] = episode_review.label_version(connection, participant, label['id'])
        labels.append(label)
    unknown = [dict(r) for r in connection.execute("SELECT * FROM episode_decisions WHERE participant_id=? AND kind='unknown' ORDER BY id", (participant,))]
    return labels, unknown


def overlay(point, labels, unknown):
    start, end = point['startMs'], point['endMsExclusive']
    matches = [r for r in labels if overlaps(start, end, r['start_ms'], r['end_ms'] if r['end_ms'] is not None else 2**63-1)]
    decisions = [r for r in unknown if overlaps(start, end, r['start_ms'], r['end_ms'])]
    blocked = bool(decisions) or any(r['source'] not in ('model', 'schedule') or r['review_state'] != 'needs_review' for r in matches)
    return {'labels': matches, 'unknown': decisions, 'canAssign': not blocked,
            'reason': 'Activity unknown is protected' if decisions else 'Existing activity is protected' if blocked else 'Pending suggestions will be split outside this window' if matches else 'Unlabeled'}


def distance(reference, candidate, features, scales):
    if not (len(reference) == len(candidate) == len(features) == len(scales)) or not features:
        raise ValueError('Missing canonical retrieval features; run analysis again')
    if not all(type(v) in (int, float) and math.isfinite(v) for v in [*reference, *candidate, *scales]) or any(s <= 0 for s in scales):
        raise ValueError('Invalid canonical retrieval features; run analysis again')
    changes = [(b-a)/s for a, b, s in zip(reference, candidate, scales)]
    total = math.fsum(v*v for v in changes)
    return math.sqrt(total), [{'feature': name, 'reference': a, 'candidate': b, 'delta': b-a,
            'iqrScale': s, 'scaledDelta': d, 'squaredContribution': d*d,
            'distanceShare': d*d/total if total else 0.}
            for name, a, b, s, d in zip(features, reference, candidate, scales, changes)]


def retrieve(payload, labels, unknown, reference=None, spacing_minutes=5):
    if type(spacing_minutes) is not int or spacing_minutes not in (1, 5, 15):raise ValueError('Choose result spacing of 1, 5 or 15 minutes')
    points = payload['points']
    references = []
    for point in points:
        current = overlay(point, labels, unknown)
        matches = current['labels']
        if (not current['unknown'] and len(matches) == 1 and matches[0]['review_state'] == 'confirmed'
                and matches[0]['end_ms'] is not None and matches[0]['start_ms'] <= point['startMs']
                and point['endMsExclusive'] <= matches[0]['end_ms']):
            references.append({'windowId': point['windowId'], 'startMs': point['startMs'],
                               'endMsExclusive': point['endMsExclusive'], 'label': matches[0]})
    result = {'version': VERSION, 'participantId': payload['participantId'], 'explorationIdentity': payload['explorationIdentity'],
              'acquisitionGroupId': payload['acquisitionGroupId'], 'acquisition': payload['acquisition'], 'view': payload['view'],
              'references': references, 'spacingMinutes': spacing_minutes, 'guardMs': GUARD_MS,
              'metric': 'Euclidean distance / selected-snapshot IQR; full feature space',
              'status': 'choose_reference' if references else 'no_confirmed_reference', 'items': []}
    if reference is None:return result
    chosen = next((r for r in references if r['windowId'] == reference), None)
    if chosen is None:raise ValueError('Choose a selected window fully inside one confirmed, finished activity. Review its activity first.')
    point = next(p for p in points if p['windowId'] == reference)
    scales = payload['pca'].get('scaling', {}).get('iqrScale', [])
    features = payload['pca']['features']
    lower, upper = chosen['label']['start_ms']-GUARD_MS, chosen['label']['end_ms']+GUARD_MS
    ranked = []
    guarded = 0
    for candidate in points:
        if overlaps(candidate['startMs'], candidate['endMsExclusive'], lower, upper):guarded += 1;continue
        value, differences = distance(point.get('modelFeatures', []), candidate.get('modelFeatures', []), features, scales)
        ranked.append({'windowId': candidate['windowId'], 'startMs': candidate['startMs'], 'endMsExclusive': candidate['endMsExclusive'],
                       'distance': value, 'differences': differences, 'annotation': overlay(candidate, labels, unknown),
                       'quality': 'No screening flags in selected snapshot'})
    ranked.sort(key=lambda p: (p['distance'], p['startMs'], p['windowId']))
    spaced = []
    for candidate in ranked:
        if all(not overlaps(candidate['startMs'], candidate['endMsExclusive'], p['startMs']-spacing_minutes*60_000, p['endMsExclusive']+spacing_minutes*60_000) for p in spaced):
            spaced.append(candidate)
    query = {'version': VERSION, 'identity': payload['explorationIdentity'], 'participant': payload['participantId'],
             'group': payload['acquisitionGroupId'], 'view': payload['view'], 'reference': chosen, 'spacingMinutes': spacing_minutes}
    items = spaced[:LIMIT]
    for item in items:item['proposalId'] = fingerprint({'query': query, 'target': item})
    result.update(status='ready', reference=chosen, guardStartMs=lower, guardEndMsExclusive=upper, items=items,
                  counts={'selected': len(points), 'guardExcluded': guarded, 'eligible': len(ranked),
                          'spacingSuppressed': len(ranked)-len(spaced), 'spacedCandidates': len(spaced),
                          'shown': len(items), 'limitOmitted': max(0, len(spaced)-LIMIT)})
    return result


def search(manager, participant, body, connection=None):
    if not participant or body.get('participantId') != participant:raise ValueError('Participant changed. Refresh before searching.')
    payload = read_view(manager, participant, body.get('group'), body.get('view', 'with_power'))
    if payload['status'] != 'current' or 'points' not in payload:raise ValueError('Exploration is unavailable or stale; run analysis again')
    if body.get('identity') != payload['explorationIdentity']:raise ValueError('Exploration changed. Refresh before searching.')
    def read(current):
        labels, unknown = annotations(current, participant)
        return retrieve(payload, labels, unknown, body.get('reference'), body.get('spacingMinutes', 5))
    if connection is None:
        with closing(sqlite3.connect(manager.database.as_uri()+'?mode=ro', uri=True)) as current:
            current.row_factory = sqlite3.Row
            current.execute('PRAGMA query_only=ON');current.execute('BEGIN')
            result = read(current)
    else:result = read(connection)
    if payload['sourceSignature'] != manager.signature():raise ValueError('Saved source changed; refresh analysis')
    return result


def proposal(result, body):
    if body.get('reviewed') is not True:raise ValueError('Confirm that you reviewed this activity before saving')
    item = next((p for p in result['items'] if p['windowId'] == body.get('target') and p['proposalId'] == body.get('proposalId')), None)
    if item is None:raise ValueError('This suggestion changed. Search again before saving.')
    if not item['annotation']['canAssign']:raise ValueError(item['annotation']['reason']+'. Edit or undo it in Episode review.')
    return item
