"""Stdlib-only, current scoped explorer reads and exact raw-source verification."""
from __future__ import annotations
from contextlib import closing
import math
import re
import sqlite3

from analysis_provenance import AcquisitionContext, SourceRecord, fingerprint

HEX = re.compile(r'^[0-9a-f]{64}$')
VIEWS = ('with_power', 'spectral_only')


def read_view(manager, participant, group=None, view='with_power'):
    from analysis_manager import read_json
    result = {'status': 'unavailable', 'participantId': participant}
    if not participant:return result
    if group is not None and (not isinstance(group, str) or not HEX.fullmatch(group)):
        raise ValueError('Choose a valid acquisition group')
    if view not in VIEWS:raise ValueError('Choose a feature view')
    with manager.lock:
        try:
            pointer = read_json(manager.root/(manager.scope(participant)+'.json'))
            if pointer['signature'] != manager.signature():return dict(result, status='stale')
            if not pointer['settings'].get('exploration'):return dict(result, status='disabled')
            directory = manager.directory(pointer['id'])
            index = read_json(directory/'exploration-index.json')
            if index['version'] != 1 or index['participantId'] != participant:raise ValueError('Invalid exploration scope')
            if read_json(directory/'summary.json')['analysisIdentity'] != index['analysisIdentity']:raise ValueError('Invalid source identity')
            if group is None:value = index
            else:
                selected = next((g for g in index['groups'] if g['acquisitionGroupId'] == group), None)
                if selected is None:raise ValueError('Acquisition group not in this snapshot')
                path = directory/f'exploration-{group}-{view}.json'
                value = read_json(path, limit=8_000_000)
                if fingerprint(value) != selected['files'][view]:raise ValueError('Invalid view content')
                if (value['version'] != 1 or value['participantId'] != participant or value['view'] != view
                        or value['acquisitionGroupId'] != group or value['acquisition'] != selected['acquisition']
                        or value['analysisIdentity'] != index['analysisIdentity'] or value['explorationIdentity'] != index['explorationIdentity']):
                    raise ValueError('Invalid explorer identity')
            if pointer['signature'] != manager.signature():return dict(result, status='stale')
            return dict(value, status='current', sourceSignature=pointer['signature'])
        except (OSError, ValueError, KeyError, TypeError):return result


def verified_waveform(database, participant, acquisition, point):
    context = AcquisitionContext(**acquisition)
    if context.participant_id != participant or context.source != 'awear-cloud':raise ValueError('Unsupported waveform source')
    start, end = point['startMs'], point['endMsExclusive']
    if type(start) is not int or type(end) is not int or not 0 < end-start <= 60000:raise ValueError('Invalid source interval')
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro', uri=True)) as connection:
        connection.execute('PRAGMA query_only=ON');connection.execute('BEGIN')
        rows = connection.execute('SELECT timestamp_ms,device_id,sample_count,waveform_json FROM readings WHERE participant_id=? AND timestamp_ms>=? AND timestamp_ms<? ORDER BY timestamp_ms', (participant, start, end)).fetchall()
        if len(rows) != len(point['sourceRecordDigests']) or len(rows)*1000 != end-start:
            raise ValueError('Exact recorded waveform is missing or ambiguous; run analysis again')
        samples = []
        for index, (timestamp, device, count, waveform) in enumerate(rows):
            record = SourceRecord(context, timestamp, waveform, count)
            record.validate()
            if timestamp != start+index*1000 or device != context.device_id or record.digest != point['sourceRecordDigests'][index]:
                raise ValueError('Source waveform changed; run analysis again')
            import json
            samples.extend(json.loads(waveform))
    return {'windowId': point['windowId'], 'startMs': start, 'endMsExclusive': end,
            'acquisition': acquisition, 'sampleRate': context.sample_rate, 'samples': samples,
            'mean': math.fsum(samples)/len(samples), 'minimum': min(samples), 'maximum': max(samples),
            'sourceVerified': True}


def waveform(manager, participant, group, view, window, identity):
    if not isinstance(window, str) or not HEX.fullmatch(window):raise ValueError('Choose a valid recorded window')
    payload = read_view(manager, participant, group, view)
    if payload['status'] != 'current':raise ValueError('Exploration is unavailable or stale; run analysis again')
    if identity != payload['explorationIdentity']:raise ValueError('Exploration changed; refresh before inspecting a waveform')
    point = next((p for p in payload['points'] if p['windowId'] == window), None)
    if point is None:raise ValueError('Window is outside the selected snapshot')
    signature = payload['sourceSignature']
    if signature != manager.signature():raise ValueError('Saved source changed; refresh analysis')
    try:result = verified_waveform(manager.database, participant, payload['acquisition'], point)
    except sqlite3.Error:raise ValueError('Exact source waveform is unavailable; run analysis again') from None
    if signature != manager.signature():raise ValueError('Saved source changed; refresh analysis')
    return result
