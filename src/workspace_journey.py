"""Small, read-only onboarding facts; scientific readiness stays with discovery_contracts."""
from __future__ import annotations


def snapshot(connection, participant, analysis, discovery, *, mode="api"):
    row = connection.execute('SELECT COUNT(*),MIN(timestamp_ms),MAX(timestamp_ms) FROM readings WHERE participant_id=?',
                             (participant,)).fetchone() if participant else (0, None, None)
    recordings = {'count': row[0], 'firstMs': row[1], 'lastMs': row[2]}
    summary = analysis.get('summary') or {}
    current = bool(participant and analysis.get('participantId') == participant
                   and discovery.get('participantId') == participant
                   and discovery.get('status') == 'current' and not analysis.get('stale')
                   and summary.get('analysisIdentity')
                   and summary['analysisIdentity'] == discovery.get('analysisIdentity'))
    job = (analysis.get('job') or {}) if analysis.get('participantId') == participant else {}
    busy = job.get('state') in ('running', 'canceling')
    ready = current and discovery.get('readiness') == 'ready_for_exploration'
    # A record count is not a signal-quality threshold. Only canonical discovery
    # can enable the insight choices, including after malformed/gapped inputs.
    state = ('connect' if not participant else 'waiting' if not row[0] else
             'preparing' if busy else 'unavailable' if not analysis.get('available') else
             'ready' if ready else 'review' if current else
             'retry' if job.get('state') in ('failed', 'interrupted', 'canceled') else 'prepare')
    return {'participantId': participant, 'recordings': recordings, 'stage': state, 'mode': mode, 'demo': bool(analysis.get('demo')),
            'canPrepare': bool(participant and row[0] and analysis.get('available') and not busy),
            'analysisIdentity': discovery.get('analysisIdentity') if current else None,
            'analysisStage': job.get('stage') if busy else None,
            'analysisError': job.get('error') if state == 'retry' else None,
            'canExplore': state == 'ready',
            'canCompareDays': bool(state == 'ready' and any(len(g.get('dates', [])) > 1 for g in discovery.get('contexts', []))),
            'labelsRequired': False}
