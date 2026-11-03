"""Participant-owned activity vocabulary, additive migration and immutable history.

activities.name owns the current name. labels.label remains its compatibility
projection; recorded_label and the audit ledger retain names as they were saved.
Raw recordings and credential/profile identities are never modified here.
"""
from __future__ import annotations
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

from archive_paths import ArchivePaths

VERSION = 2
NAMESPACE = uuid.UUID('d48680ee-7319-4d93-88ae-f5f41104f88a')
NOW = "CAST(strftime('%s','now') AS INTEGER)*1000"
LABEL_FIELDS = 'label_id,participant_id,activity_id,label,recorded_label,note,start_ms,end_ms,source,reviewed,batch_id'


def _snapshot(prefix: str) -> str:
    return ','.join(prefix + '.' + key for key in ('id','participant_id','activity_id','label','recorded_label','note','start_ms','end_ms','source','reviewed','batch_id'))


TRIGGERS = {
    'mt_activity_scope_insert': """BEFORE INSERT ON labels WHEN NEW.activity_id IS NOT NULL AND NOT EXISTS
        (SELECT 1 FROM activities WHERE id=NEW.activity_id AND participant_id=NEW.participant_id)
        BEGIN SELECT RAISE(ABORT,'Activity belongs to another participant'); END""",
    'mt_activity_scope_update': """BEFORE UPDATE OF activity_id,participant_id ON labels WHEN NEW.activity_id IS NOT NULL AND NOT EXISTS
        (SELECT 1 FROM activities WHERE id=NEW.activity_id AND participant_id=NEW.participant_id)
        BEGIN SELECT RAISE(ABORT,'Activity belongs to another participant'); END""",
    'mt_activity_legacy_insert': f"""AFTER INSERT ON labels WHEN NEW.activity_id IS NULL BEGIN
        INSERT OR IGNORE INTO activities(id,participant_id,name,created_ms,updated_ms,origin)
        VALUES(lower(hex(randomblob(16))),NEW.participant_id,NEW.label,{NOW},{NOW},'legacy-writer');
        UPDATE labels SET activity_id=(SELECT id FROM activities WHERE participant_id=NEW.participant_id AND name=NEW.label),
            recorded_label=COALESCE(NEW.recorded_label,NEW.label) WHERE id=NEW.id; END""",
    'mt_activity_legacy_update': f"""AFTER UPDATE OF label ON labels WHEN NEW.activity_id=OLD.activity_id AND
        NEW.label!=(SELECT name FROM activities WHERE id=NEW.activity_id) BEGIN
        INSERT OR IGNORE INTO activities(id,participant_id,name,created_ms,updated_ms,origin)
        VALUES(lower(hex(randomblob(16))),NEW.participant_id,NEW.label,{NOW},{NOW},'legacy-writer');
        UPDATE labels SET activity_id=(SELECT id FROM activities WHERE participant_id=NEW.participant_id AND name=NEW.label),
            recorded_label=NEW.label WHERE id=NEW.id; END""",
    'mt_activity_projection_insert': """AFTER INSERT ON labels WHEN NEW.activity_id IS NOT NULL AND
        (NEW.label!=(SELECT name FROM activities WHERE id=NEW.activity_id) OR NEW.recorded_label IS NULL) BEGIN
        UPDATE labels SET label=(SELECT name FROM activities WHERE id=NEW.activity_id),
            recorded_label=COALESCE(NEW.recorded_label,(SELECT name FROM activities WHERE id=NEW.activity_id)) WHERE id=NEW.id; END""",
    'mt_activity_projection_update': """AFTER UPDATE OF activity_id ON labels WHEN NEW.activity_id IS NOT NULL AND
        (NEW.label!=(SELECT name FROM activities WHERE id=NEW.activity_id) OR NEW.recorded_label IS NULL OR
         (OLD.activity_id IS NOT NULL AND NEW.activity_id!=OLD.activity_id AND NEW.recorded_label=OLD.recorded_label)) BEGIN
        UPDATE labels SET label=(SELECT name FROM activities WHERE id=NEW.activity_id),
            recorded_label=(SELECT name FROM activities WHERE id=NEW.activity_id) WHERE id=NEW.id; END""",
    'mt_label_identity': """BEFORE UPDATE ON labels WHEN NEW.id!=OLD.id OR NEW.participant_id!=OLD.participant_id
        BEGIN SELECT RAISE(ABORT,'Label identity is immutable'); END""",
    'mt_label_keep_activity': """BEFORE UPDATE OF activity_id ON labels WHEN OLD.activity_id IS NOT NULL AND NEW.activity_id IS NULL
        BEGIN SELECT RAISE(ABORT,'Assign a personal activity instead of clearing its identity'); END""",
    'mt_activity_identity': """BEFORE UPDATE ON activities WHEN NEW.id!=OLD.id OR NEW.participant_id!=OLD.participant_id
        BEGIN SELECT RAISE(ABORT,'Activity identity is immutable'); END""",
    'mt_activity_version': """BEFORE UPDATE ON activities WHEN NEW.version!=OLD.version+1
        BEGIN SELECT RAISE(ABORT,'Activity version must advance'); END""",
    'mt_activity_name_projection': """AFTER UPDATE OF name ON activities WHEN NEW.name!=OLD.name BEGIN
        UPDATE labels SET label=NEW.name WHERE activity_id=NEW.id AND participant_id=NEW.participant_id; END""",
    'mt_activity_created': """AFTER INSERT ON activities BEGIN
        INSERT INTO activity_history(activity_id,participant_id,version,name,category,context,favorite,archived,changed_ms,origin)
        VALUES(NEW.id,NEW.participant_id,NEW.version,NEW.name,NEW.category,NEW.context,NEW.favorite,NEW.archived,NEW.updated_ms,NEW.origin); END""",
    'mt_activity_updated': """AFTER UPDATE ON activities BEGIN
        INSERT INTO activity_history(activity_id,participant_id,version,name,category,context,favorite,archived,changed_ms,origin)
        VALUES(NEW.id,NEW.participant_id,NEW.version,NEW.name,NEW.category,NEW.context,NEW.favorite,NEW.archived,NEW.updated_ms,'edited'); END""",
    'mt_activity_no_delete': """BEFORE DELETE ON activities BEGIN SELECT RAISE(ABORT,'Archive activities instead of deleting them'); END""",
    'mt_label_created': f"""AFTER INSERT ON labels WHEN NEW.activity_id IS NOT NULL BEGIN
        INSERT INTO activity_label_history({LABEL_FIELDS},action,changed_ms) VALUES({_snapshot('NEW')},'created',{NOW}); END""",
    'mt_label_before_update': f"""BEFORE UPDATE ON labels WHEN OLD.activity_id IS NOT NULL BEGIN
        INSERT INTO activity_label_history({LABEL_FIELDS},action,changed_ms) VALUES({_snapshot('OLD')},'before-update',{NOW}); END""",
    'mt_label_updated': f"""AFTER UPDATE ON labels WHEN NEW.activity_id IS NOT NULL BEGIN
        INSERT INTO activity_label_history({LABEL_FIELDS},action,changed_ms)
        VALUES({_snapshot('NEW')},CASE WHEN OLD.activity_id IS NULL THEN 'created' ELSE 'updated' END,{NOW}); END""",
    'mt_label_deleted': f"""BEFORE DELETE ON labels WHEN OLD.activity_id IS NOT NULL BEGIN
        INSERT INTO activity_label_history({LABEL_FIELDS},action,changed_ms) VALUES({_snapshot('OLD')},'deleted',{NOW}); END""",
}
for _table in ('activity_history', 'activity_label_history'):
    for _operation in ('UPDATE', 'DELETE'):
        TRIGGERS[f'mt_{_table}_no_{_operation.lower()}'] = f"BEFORE {_operation} ON {_table} BEGIN SELECT RAISE(ABORT,'Activity history is append only'); END"


# Freeze the shipped v1 definitions: restore validates them before upgrading.
V1_TRIGGERS = dict(TRIGGERS)
V1_LABEL_FIELDS = LABEL_FIELDS
EVIDENCE_COLUMNS = {
    'review_state': "TEXT CHECK(review_state IN ('confirmed','needs_review','uncertain'))",
    'timing_precision': "TEXT NOT NULL DEFAULT 'unknown' CHECK(timing_precision IN ('unknown','exact','approximate'))",
    'start_uncertainty_ms': "INTEGER CHECK(start_uncertainty_ms IS NULL OR (typeof(start_uncertainty_ms)='integer' AND start_uncertainty_ms BETWEEN 0 AND 86400000))",
    'end_uncertainty_ms': "INTEGER CHECK(end_uncertainty_ms IS NULL OR (typeof(end_uncertainty_ms)='integer' AND end_uncertainty_ms BETWEEN 0 AND 86400000))",
}
LABEL_FIELDS += ',' + ','.join(EVIDENCE_COLUMNS)


def evidence_snapshot(prefix):
    return _snapshot(prefix) + ',' + ','.join(prefix+'.'+key for key in EVIDENCE_COLUMNS)


# History snapshots retain timing/review independently from the original source.
for name, event, when, action in (
    ('mt_label_created','AFTER INSERT','NEW.activity_id IS NOT NULL',"'created'"),
    ('mt_label_before_update','BEFORE UPDATE','OLD.activity_id IS NOT NULL',"'before-update'"),
    ('mt_label_updated','AFTER UPDATE','NEW.activity_id IS NOT NULL',"CASE WHEN OLD.activity_id IS NULL THEN 'created' ELSE 'updated' END"),
    ('mt_label_deleted','BEFORE DELETE','OLD.activity_id IS NOT NULL',"'deleted'")):
    prefix='OLD' if event.startswith('BEFORE') else 'NEW'
    TRIGGERS[name]=f"{event} ON labels WHEN {when} BEGIN INSERT INTO activity_label_history({LABEL_FIELDS},action,changed_ms) VALUES({evidence_snapshot(prefix)},{action},{NOW}); END"
TRIGGERS.update({
    'mt_review_insert': """AFTER INSERT ON labels WHEN NEW.review_state IS NULL OR NEW.reviewed!=(NEW.review_state='confirmed') BEGIN
        UPDATE labels SET review_state=COALESCE(NEW.review_state,CASE WHEN NEW.reviewed THEN 'confirmed' ELSE 'needs_review' END),
            reviewed=CASE WHEN COALESCE(NEW.review_state,CASE WHEN NEW.reviewed THEN 'confirmed' ELSE 'needs_review' END)='confirmed' THEN 1 ELSE 0 END WHERE id=NEW.id; END""",
    'mt_review_legacy_update': """AFTER UPDATE OF reviewed ON labels WHEN NEW.reviewed!=OLD.reviewed AND NEW.review_state IS OLD.review_state AND NEW.reviewed!=(NEW.review_state='confirmed') BEGIN
        UPDATE labels SET review_state=CASE WHEN NEW.reviewed THEN 'confirmed' ELSE 'needs_review' END WHERE id=NEW.id; END""",
    'mt_review_state_update': """AFTER UPDATE OF review_state ON labels WHEN NEW.review_state IS NULL OR NEW.reviewed!=(NEW.review_state='confirmed') BEGIN
        UPDATE labels SET review_state=COALESCE(NEW.review_state,CASE WHEN NEW.reviewed THEN 'confirmed' ELSE 'needs_review' END),
            reviewed=CASE WHEN COALESCE(NEW.review_state,CASE WHEN NEW.reviewed THEN 'confirmed' ELSE 'needs_review' END)='confirmed' THEN 1 ELSE 0 END WHERE id=NEW.id; END""",
    'mt_label_origin': """BEFORE UPDATE OF source ON labels WHEN NEW.source!=OLD.source BEGIN SELECT RAISE(ABORT,'Original label source is immutable'); END""",
})
for operation in ('INSERT','UPDATE'):
    TRIGGERS['mt_timing_'+operation.lower()]=f"""BEFORE {operation} ON labels WHEN
        (NEW.end_ms IS NULL AND NEW.end_uncertainty_ms IS NOT NULL) OR
        (NEW.timing_precision='unknown' AND (NEW.start_uncertainty_ms IS NOT NULL OR NEW.end_uncertainty_ms IS NOT NULL)) OR
        (NEW.timing_precision='exact' AND (NEW.start_uncertainty_ms IS NOT 0 OR (NEW.end_ms IS NOT NULL AND NEW.end_uncertainty_ms IS NOT 0))) OR
        (NEW.timing_precision='approximate' AND NOT(COALESCE(NEW.start_uncertainty_ms,0)>0 OR COALESCE(NEW.end_uncertainty_ms,0)>0))
        BEGIN SELECT RAISE(ABORT,'Timing precision and uncertainty do not agree'); END"""


def trigger_sql(name: str) -> str:
    return 'CREATE TRIGGER ' + name + ' ' + TRIGGERS[name]


def validate_archive_objects(connection: sqlite3.Connection) -> None:
    """Only this version's exact owned triggers are allowed through archive restore."""
    objects = connection.execute("SELECT type,name,sql FROM sqlite_master WHERE type IN ('view','trigger')").fetchall()
    tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'activity_schema' not in tables:
        if objects: raise ValueError('This archive contains unsupported triggers or views')
        return
    versions = connection.execute('SELECT version FROM activity_schema').fetchall()
    if len(versions) != 1 or versions[0][0] not in (1,VERSION):
        raise ValueError('This archive needs a different activity schema version')
    supported = V1_TRIGGERS if versions[0][0] == 1 else TRIGGERS
    normalize = lambda value: ' '.join(value.split()).rstrip(';')
    if {row[1] for row in objects} != set(supported) or any(
            row[0] != 'trigger' or normalize(row[2]) != normalize('CREATE TRIGGER '+row[1]+' '+supported[row[1]]) for row in objects):
        raise ValueError('Activity archive triggers differ from the supported schema')
    required = {'activities': {'id','participant_id','name','category','context','favorite','archived','version','created_ms','updated_ms','origin'},
                'activity_history': {'activity_id','participant_id','version','name','category','context','favorite','archived','changed_ms','origin'},
                'activity_label_history': set((V1_LABEL_FIELDS if versions[0][0]==1 else LABEL_FIELDS).split(',')) | {'event_id','action','changed_ms'},
                'labels': {'activity_id','recorded_label'}}
    if versions[0][0] == VERSION: required['labels'].update(EVIDENCE_COLUMNS)
    for table, fields in required.items():
        if not fields <= {row[1] for row in connection.execute(f'PRAGMA table_info({table})')}:
            raise ValueError('Activity archive schema is incomplete')


def migrate(connection: sqlite3.Connection, database: Path, profile: Path) -> None:
    tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'activity_schema' in tables:
        validate_archive_objects(connection)
        if connection.execute('SELECT version FROM activity_schema').fetchone()[0] == 1:
            _upgrade_v1(connection,database,profile)
        return
    if tables & {'activities','activity_history','activity_label_history'}:
        raise ValueError('Unversioned activity tables already exist; preserve this archive before upgrading')
    connection.commit()  # Finish the pre-existing additive base-schema upgrade.
    backup_name = _backup(connection,database,profile)
    connection.execute('BEGIN IMMEDIATE')
    # Another launcher may have completed migration while this one made a backup.
    if connection.execute("SELECT 1 FROM sqlite_master WHERE name='activity_schema'").fetchone():
        validate_archive_objects(connection)
        if connection.execute('SELECT version FROM activity_schema').fetchone()[0] == 1:
            _upgrade_v1(connection,database,profile)
        return
    connection.execute('''CREATE TABLE activities(id TEXT PRIMARY KEY,participant_id TEXT NOT NULL,name TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT '',context TEXT NOT NULL DEFAULT '',favorite INTEGER NOT NULL DEFAULT 0 CHECK(favorite IN(0,1)),
        archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),version INTEGER NOT NULL DEFAULT 1,
        created_ms INTEGER NOT NULL,updated_ms INTEGER NOT NULL,origin TEXT NOT NULL,
        UNIQUE(participant_id,name),UNIQUE(participant_id,id))''')
    connection.execute('''CREATE TABLE activity_history(activity_id TEXT NOT NULL,participant_id TEXT NOT NULL,version INTEGER NOT NULL,
        name TEXT NOT NULL,category TEXT NOT NULL,context TEXT NOT NULL,favorite INTEGER NOT NULL,archived INTEGER NOT NULL,
        changed_ms INTEGER NOT NULL,origin TEXT NOT NULL,PRIMARY KEY(activity_id,version))''')
    connection.execute('''CREATE TABLE activity_label_history(event_id INTEGER PRIMARY KEY AUTOINCREMENT,label_id INTEGER NOT NULL,
        participant_id TEXT NOT NULL,activity_id TEXT,label TEXT NOT NULL,recorded_label TEXT,note TEXT NOT NULL,
        start_ms INTEGER NOT NULL,end_ms INTEGER,source TEXT NOT NULL,reviewed INTEGER NOT NULL,batch_id TEXT,
        action TEXT NOT NULL,changed_ms INTEGER NOT NULL)''')
    connection.execute('CREATE INDEX activity_label_history_scope ON activity_label_history(participant_id,activity_id,event_id)')
    connection.execute('ALTER TABLE labels ADD COLUMN activity_id TEXT')
    connection.execute('ALTER TABLE labels ADD COLUMN recorded_label TEXT')
    connection.execute('CREATE INDEX labels_activity ON labels(participant_id,activity_id)')
    _add_evidence_columns(connection)
    now = int(time.time()*1000)
    for participant, name in connection.execute('SELECT DISTINCT participant_id,label FROM labels ORDER BY participant_id,label').fetchall():
        activity_id = uuid.uuid5(NAMESPACE,json.dumps([participant,name],ensure_ascii=False,separators=(',',':'))).hex
        connection.execute('INSERT INTO activities(id,participant_id,name,created_ms,updated_ms,origin) VALUES(?,?,?,?,?,?)',
                           (activity_id,participant,name,now,now,'migration'))
        connection.execute('INSERT INTO activity_history VALUES(?,?,?,?,?,?,?,?,?,?)',(activity_id,participant,1,name,'','',0,0,now,'migration'))
        connection.execute('UPDATE labels SET activity_id=?,recorded_label=label WHERE participant_id=? AND label=?',(activity_id,participant,name))
    connection.execute(f"INSERT INTO activity_label_history({LABEL_FIELDS},action,changed_ms) SELECT id,participant_id,activity_id,label,recorded_label,note,start_ms,end_ms,source,reviewed,batch_id,review_state,timing_precision,start_uncertainty_ms,end_uncertainty_ms,'migrated',? FROM labels",(now,))
    for name in TRIGGERS: connection.execute(trigger_sql(name))
    connection.execute('CREATE TABLE activity_schema(version INTEGER PRIMARY KEY,applied_ms INTEGER NOT NULL,backup_name TEXT)')
    connection.execute('INSERT INTO activity_schema VALUES(?,?,?)',(VERSION,now,backup_name))


def _backup(connection,database,profile):
    connection.commit()
    if not any(connection.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone() for table in ('readings','labels','mobile_scores','activities') if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone()):
        return None
    directory=ArchivePaths(profile).activity_migrations
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    name=f'before-activities-v{VERSION}-'+uuid.uuid4().hex+'.sqlite3'
    path=directory/name
    with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True)) as source, closing(sqlite3.connect(path)) as target:
        os.chmod(path,0o600);source.backup(target)
    return name


def _add_evidence_columns(connection):
    for table in ('labels','activity_label_history'):
        for name,definition in EVIDENCE_COLUMNS.items():
            connection.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
        connection.execute(f"UPDATE {table} SET review_state=CASE WHEN reviewed THEN 'confirmed' ELSE 'needs_review' END")


def _upgrade_v1(connection,database,profile):
    backup_name=_backup(connection,database,profile)
    connection.execute('BEGIN IMMEDIATE')
    validate_archive_objects(connection)
    if connection.execute('SELECT version FROM activity_schema').fetchone()[0] == VERSION:return
    for name in V1_TRIGGERS:connection.execute('DROP TRIGGER '+name)
    _add_evidence_columns(connection)
    for name in TRIGGERS:connection.execute(trigger_sql(name))
    connection.execute('UPDATE activity_schema SET version=?,applied_ms=?,backup_name=?',(VERSION,int(time.time()*1000),backup_name))


def label_evidence(current,body,start_ms,end_ms):
    """Validate human evidence; precision is declared, never inferred from EEG or a clock."""
    if body is not None and (not isinstance(body,dict) or set(body)-{'reviewState','timingPrecision','startUncertaintyMs','endUncertaintyMs'}):
        raise ValueError('Invalid label evidence fields')
    # Old callers explicitly saving an interval retain their confirmation behavior.
    review=(body or {}).get('reviewState','confirmed')
    if review not in ('confirmed','needs_review','uncertain'):raise ValueError('Choose a valid review state')
    unchanged=current is not None and current['start_ms']==start_ms and current['end_ms']==end_ms
    base={'timingPrecision':current['timing_precision'],'startUncertaintyMs':current['start_uncertainty_ms'],'endUncertaintyMs':current['end_uncertainty_ms']} if unchanged else {'timingPrecision':'unknown','startUncertaintyMs':None,'endUncertaintyMs':None}
    base.update(body or {})
    precision=base['timingPrecision']
    if precision not in ('unknown','exact','approximate'):raise ValueError('Choose a valid timing precision')
    start,end=base['startUncertaintyMs'],base['endUncertaintyMs']
    for value in (start,end):
        if value is not None and (type(value) is not int or not 0<=value<=86400000):raise ValueError('Timing uncertainty must be whole milliseconds from 0 to 24 hours, or unknown')
    if precision=='unknown':
        if start is not None or end is not None:raise ValueError('Unknown timing must leave uncertainty unknown')
    elif precision=='exact':
        if start != 0 or (end_ms is not None and end != 0):raise ValueError('Exact timing requires zero uncertainty at each known boundary')
    elif not ((start or 0)>0 or (end or 0)>0):raise ValueError('Approximate timing needs a positive uncertainty at one or both boundaries')
    if end_ms is None and end is not None:raise ValueError('An active interval has no end uncertainty yet')
    return review,precision,start,end


def _text(value, limit: int, *, required=False) -> str:
    if not isinstance(value,str) or len(value.strip()) > limit or (required and not value.strip()):
        raise ValueError(f'Enter text of {"1–" if required else "up to "}{limit} characters')
    if any(ord(c)<32 for c in value):raise ValueError('Activity text must not contain control characters')
    return value.strip()


def create(connection: sqlite3.Connection, participant: str, body: dict) -> dict:
    if not participant:raise ValueError('Select a participant first')
    name = _text(body.get('name'),80,required=True)
    category = _text(body.get('category',''),80)
    context = _text(body.get('context',''),500)
    favorite = body.get('favorite',False)
    if type(favorite) is not bool:raise ValueError('Invalid favorite setting')
    activity_id, now = uuid.uuid4().hex, int(time.time()*1000)
    try:
        connection.execute('INSERT INTO activities(id,participant_id,name,category,context,favorite,created_ms,updated_ms,origin) VALUES(?,?,?,?,?,?,?,?,?)',
                           (activity_id,participant,name,category,context,int(favorite),now,now,'personal'))
    except sqlite3.IntegrityError as error:
        raise ValueError('An activity with that exact name already exists, possibly archived. Nothing was merged.') from error
    return get(connection,participant,activity_id)


def get(connection,participant,activity_id) -> dict:
    row=connection.execute('SELECT * FROM activities WHERE id=? AND participant_id=?',(activity_id,participant)).fetchone()
    if row is None:raise ValueError('Activity not found for this participant')
    return dict(row)


def resolve(connection,participant,name,activity_id=None,*,allow_archived=None) -> dict:
    if activity_id:
        activity=get(connection,participant,activity_id)
    else:
        row=connection.execute('SELECT * FROM activities WHERE participant_id=? AND name=?',(participant,name)).fetchone()
        activity=dict(row) if row else create(connection,participant,{'name':name})
    if activity['archived'] and activity['id'] != allow_archived:
        raise ValueError('This activity is archived. Restore it or choose another activity.')
    return activity


def update(connection,participant,activity_id,body) -> dict:
    current=get(connection,participant,activity_id)
    version=body.get('version')
    if type(version) is not int or version!=current['version']:
        raise ValueError('This activity changed. Refresh and open Edit again before saving.')
    name=_text(body.get('name',current['name']),80,required=True)
    category=_text(body.get('category',current['category']),80)
    context=_text(body.get('context',current['context']),500)
    favorite,archived=body.get('favorite',bool(current['favorite'])),body.get('archived',bool(current['archived']))
    if type(favorite) is not bool or type(archived) is not bool:raise ValueError('Invalid activity setting')
    try:
        connection.execute('UPDATE activities SET name=?,category=?,context=?,favorite=?,archived=?,version=version+1,updated_ms=? WHERE id=? AND participant_id=?',
                           (name,category,context,int(favorite),int(archived),int(time.time()*1000),activity_id,participant))
    except sqlite3.IntegrityError as error:
        raise ValueError('Another activity already uses that exact name. Nothing was merged.') from error
    return get(connection,participant,activity_id)


def revision(connection,participant) -> str | None:
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='activity_schema'").fetchone():
        return None
    row=connection.execute("""SELECT COUNT(*),COALESCE(SUM(version),0),
        (SELECT COALESCE(MAX(event_id),0) FROM activity_label_history WHERE participant_id=?)
        FROM activities WHERE participant_id=?""",(participant,participant)).fetchone()
    return ':'.join(str(value) for value in row)


def page(connection,participant,*,offset=0,limit=50,include_archived=True) -> dict:
    if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=100:
        raise ValueError('Choose a valid activity page (1–100 rows)')
    where='a.participant_id=?'+('' if include_archived else ' AND a.archived=0')
    total=connection.execute('SELECT COUNT(*) FROM activities a WHERE '+where,(participant,)).fetchone()[0]
    rows=connection.execute('''SELECT a.*,(SELECT COUNT(*) FROM labels l WHERE l.activity_id=a.id AND l.participant_id=a.participant_id) AS period_count,
        (SELECT MAX(changed_ms) FROM activity_label_history h WHERE h.activity_id=a.id AND h.participant_id=a.participant_id) AS last_used_ms
        FROM activities a WHERE '''+where+' ORDER BY a.favorite DESC,last_used_ms DESC,a.name COLLATE NOCASE,a.id LIMIT ? OFFSET ?',
        (participant,limit,offset)).fetchall()
    return {'participantId':participant,'revision':revision(connection,participant),'activities':[dict(row) for row in rows],'total':total,'offset':offset,'hasMore':offset+len(rows)<total}


def history(connection,participant,activity_id,*,offset=0,limit=50):
    get(connection,participant,activity_id)
    if offset<0 or not 1<=limit<=100:raise ValueError('Invalid history page')
    total=connection.execute('SELECT COUNT(*) FROM activity_history WHERE participant_id=? AND activity_id=?',(participant,activity_id)).fetchone()[0]
    rows=connection.execute('SELECT * FROM activity_history WHERE participant_id=? AND activity_id=? ORDER BY version DESC LIMIT ? OFFSET ?',
                            (participant,activity_id,limit,offset)).fetchall()
    return {'participantId':participant,'history':[dict(row) for row in rows],'total':total,'offset':offset,'hasMore':offset+len(rows)<total}


def export_json(connection,participant):
    """Stream one consistent complete export, retaining all names and assignment history."""
    yield ('{"formatVersion":2,"participantId":'+json.dumps(participant)+',').encode()
    sections=(('activities','SELECT * FROM activities WHERE participant_id=? ORDER BY id'),
              ('activityHistory','SELECT * FROM activity_history WHERE participant_id=? ORDER BY activity_id,version'),
              ('assignments','SELECT * FROM labels WHERE participant_id=? ORDER BY id'),
              ('assignmentHistory','SELECT * FROM activity_label_history WHERE participant_id=? ORDER BY event_id'))
    for index,(name,query) in enumerate(sections):
        if index:yield b','
        yield (json.dumps(name)+':[').encode()
        for row_index,row in enumerate(connection.execute(query,(participant,))):
            if row_index:yield b','
            yield json.dumps(dict(row),ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf-8')
        yield b']'
    yield b'}'
