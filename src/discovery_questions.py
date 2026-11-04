"""Human-authored discovery questions, separate from deterministic EEG and annotations."""
from __future__ import annotations
from contextlib import closing
from datetime import datetime, timezone
import json
import os
import sqlite3
import uuid


def location(manager):
    return manager.discovery_root / 'questions.sqlite3'


def _page(manager, participant):
    if not participant: return {'participantId': participant, 'questions': [], 'total': 0}
    path = location(manager)
    if path.is_symlink(): raise ValueError('Invalid question storage location')
    if not path.exists(): return {'participantId': participant, 'questions': [], 'total': 0}
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as connection:
        rows = connection.execute('SELECT id,revision,title,notes,stage,analysis_identity FROM questions WHERE scope=? ORDER BY id', (manager.scope(participant),)).fetchall()
    return {'participantId': participant, 'questions': [dict(zip(('id','revision','title','notes','stage','analysisIdentity'), r)) for r in rows], 'total': len(rows),
        'confirmation': 'not_reserved; enforced holdout workflow is deferred'}


def initialize(connection):
    connection.execute('CREATE TABLE IF NOT EXISTS questions(scope TEXT,id TEXT,revision INTEGER,title TEXT,notes TEXT,stage TEXT,analysis_identity TEXT,PRIMARY KEY(scope,id))')
    connection.execute('CREATE TABLE IF NOT EXISTS history(scope TEXT,id TEXT,revision INTEGER,record_json TEXT,created_at TEXT,PRIMARY KEY(scope,id,revision))')


def _save(manager, participant, body):
    if not participant or body.get('participantId') != participant:
        raise ValueError('Participant changed. Refresh before saving this question.')
    if body.get('stage') not in ('discovery', 'candidate'):
        raise ValueError('Choose discovery or candidate; confirmation reservation is not implemented.')
    for field, maximum in (('title', 240), ('notes', 4000)):
        if not isinstance(body.get(field), str) or len(body[field]) > maximum:
            raise ValueError('Invalid question '+field)
    if not body['title'].strip(): raise ValueError('Enter a question')
    from discovery_contracts import read
    current = read(manager, participant)
    if (current['status'] != 'current' or body.get('analysisIdentity') != current['analysisIdentity']
            or body.get('discoveryIdentity') != current['discoveryIdentity']):
        raise ValueError('Discovery changed or is unavailable. Refresh before saving.')
    identifier = body.get('id')
    revision = body.get('revision', 0)
    if type(revision) is not int or revision < 0: raise ValueError('Invalid question revision')
    if identifier is not None and (not isinstance(identifier, str) or len(identifier) != 32
            or any(c not in '0123456789abcdef' for c in identifier)):
        raise ValueError('Invalid question identity')
    if identifier is None and revision != 0: raise ValueError('Invalid new question revision')
    path = location(manager)
    if path.is_symlink(): raise ValueError('Invalid question storage location')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with closing(sqlite3.connect(path)) as connection, connection:
        os.chmod(path,0o600)
        connection.execute('BEGIN IMMEDIATE')
        initialize(connection)
        scope = manager.scope(participant)
        if identifier is None:
            if connection.execute('SELECT COUNT(*) FROM questions WHERE scope=?', (scope,)).fetchone()[0] >= 100:
                raise ValueError('Question limit reached (100); edit an existing question.')
            identifier = uuid.uuid4().hex
            connection.execute('INSERT INTO questions VALUES(?,?,?,?,?,?,?)',
                (scope, identifier, 1, body['title'].strip(), body['notes'], body['stage'], current['analysisIdentity']))
        else:
            changed = connection.execute('UPDATE questions SET revision=revision+1,title=?,notes=?,stage=?,analysis_identity=? WHERE scope=? AND id=? AND revision=?',
                (body['title'].strip(),body['notes'],body['stage'],current['analysisIdentity'],scope,identifier,revision))
            if changed.rowcount != 1: raise ValueError('Question changed. Refresh before editing.')
        record = {'id':identifier, 'revision':revision+1, 'title':body['title'].strip(),
            'notes':body['notes'], 'stage':body['stage'], 'analysisIdentity':current['analysisIdentity'],'discoveryIdentity':current['discoveryIdentity']}
        connection.execute('INSERT INTO history VALUES(?,?,?,?,?)',
            (scope, identifier, revision+1, json.dumps(record,ensure_ascii=False), datetime.now(timezone.utc).isoformat()))
    return record


def page(manager, participant):
    try: return _page(manager, participant)
    except (sqlite3.Error, OSError):
        raise ValueError('Question storage is unavailable. Your recordings are unchanged.') from None


def save(manager, participant, body):
    try: return _save(manager, participant, body)
    except (sqlite3.Error, OSError):
        raise ValueError('Question storage is unavailable. Your recordings are unchanged.') from None
