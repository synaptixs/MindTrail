"""Credential-free complete workspace recovery, with bounded validation and rollback.

Only the core DB, portable scope identity and persisted data/ tree are owned here.
Quality jobs/cache are regenerable; credentials, locks and logs never travel.
"""
from contextlib import closing, contextmanager
import hashlib
import importlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat
import tempfile
import threading
import time
import zipfile

import profile_identity

MAX_BYTES = 20 * 1024**3
MAX_DATABASE = 5 * 1024**3
MAX_FILES = 100_000
CORE = 'awear_data.sqlite3'
OMISSIONS = ['API credentials and OS keychain', 'root configuration, locks and logs',
             'data/processed/quality (regenerable jobs and cache)', 'SQLite WAL/SHM (snapshotted into databases)']
STORES = {'questions.sqlite3': 'discovery_questions', 'context.sqlite3': 'discovery_context',
          'decisions.sqlite3': 'discovery_decisions', 'monitoring.sqlite3': 'analysis_history'}


class WorkspaceGate:
    """Concurrent ordinary requests; bounded exclusive recovery, never a serial GET lock."""
    def __init__(self):
        self.condition = threading.Condition()
        self.readers = 0
        self.exclusive = False

    @contextmanager
    def lease(self, exclusive=False, timeout=5):
        with self.condition:
            if self.exclusive:
                raise ValueError('Backup or restore is in progress. Try again shortly.')
            if exclusive:
                self.exclusive = True
                deadline = time.monotonic() + timeout
                while self.readers:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self.exclusive = False
                        self.condition.notify_all()
                        raise ValueError('Other workspace requests are still active. Try again shortly.')
                    self.condition.wait(remaining)
            else:
                self.readers += 1
        try:
            yield
        finally:
            with self.condition:
                if exclusive:
                    self.exclusive = False
                else:
                    self.readers -= 1
                self.condition.notify_all()


def allowed(name):
    p = PurePosixPath(name)
    if (not name or '\\' in name or ':' in name or '\0' in name or p.is_absolute()
            or p.as_posix() != name or any(s in ('', '.', '..') for s in name.split('/'))
            or any(s.endswith((' ', '.')) for s in p.parts)):
        return False
    if name in (CORE, profile_identity.NAME):
        return True
    if len(p.parts) < 2 or p.parts[0] != 'data' or tuple(s.casefold() for s in p.parts[:3]) == ('data', 'processed', 'quality'):
        return False
    # Cross-platform reserved names and credential/operational files cannot hide in data/.
    for part in p.parts:
        lower = part.lower()
        if (lower.startswith('.env') or lower.endswith(('.p8', '.p12', '.pfx', '.key', '-wal', '-shm'))
                or lower.split('.')[0] in {'con', 'prn', 'aux', 'nul', *(f'com{i}' for i in range(1,10)), *(f'lpt{i}' for i in range(1,10))}):
            return False
    return True



def portable_paths(names):
    prefixes = {}
    for name in names:
        parts = PurePosixPath(name).parts
        for end in range(1,len(parts)+1):
            prefix = '/'.join(parts[:end])
            key = prefix.casefold()
            if key in prefixes and prefixes[key] != prefix:
                raise ValueError('Workspace paths collide on Windows')
            prefixes[key] = prefix

def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def files(profile):
    """Inventory all persisted data, failing closed on paths we cannot preserve."""
    result = {CORE: profile / CORE}
    marker = profile / profile_identity.NAME
    if marker.exists() or marker.is_symlink():
        result[profile_identity.NAME] = marker
    root = profile / 'data'
    if root.is_symlink():
        raise ValueError('Workspace data cannot be a symbolic link')
    for parent, directories, names in os.walk(root):
        parent = Path(parent)
        for name in list(directories):
            p = parent / name
            if p.is_symlink():
                raise ValueError('Workspace contains a symbolic link; backup stopped')
            if p.relative_to(profile).as_posix() == 'data/processed/quality':
                directories.remove(name)
        for name in names:
            p = parent / name
            if p.is_symlink() or not p.is_file():
                raise ValueError('Workspace contains a non-regular file; backup stopped')
            if name.endswith(('-wal', '-shm')) and Path(str(p)[:-4]).is_file():
                continue  # Consistent SQLite backup folds WAL frames into the DB.
            relative = p.relative_to(profile).as_posix()
            if not allowed(relative):
                raise ValueError('Unsupported workspace file: ' + relative)
            result[relative] = p
    if len(result) >= MAX_FILES:
        raise ValueError('Workspace exceeds the 100,000-file backup limit')
    return result


def stamp(profile):
    result = {}
    for name, p in files(profile).items():
        for candidate in (p, Path(str(p)+'-wal')):
            if candidate.is_symlink():
                raise ValueError('Invalid workspace file')
            try:
                s = candidate.stat()
                result[str(candidate)] = (s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_ino) if s.st_size else None
            except FileNotFoundError:
                result[str(candidate)] = None
    return result


def validate_database(path, name):
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1', uri=True)) as c:
        c.execute('PRAGMA trusted_schema=OFF')
        if c.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise ValueError('Database integrity check failed: ' + name)
        if name == CORE:
            from personal_activities import validate_archive_objects
            validate_archive_objects(c)
            for table, columns in {'settings': {'key','value'}, 'readings': {'participant_id','timestamp_ms','waveform_json','sample_count','mean','rms','peak_to_peak'}, 'labels': {'id','participant_id','label','note','start_ms','end_ms'}}.items():
                if not columns <= {r[1] for r in c.execute(f'PRAGMA table_info({table})')}:
                    raise ValueError('Unsupported recording archive schema')
        elif name.startswith('data/processed/discovery/'):
            module = STORES.get(PurePosixPath(name).name)
            if module is None or len(PurePosixPath(name).parts) != 4:
                raise ValueError('Unsupported discovery store')
            with closing(sqlite3.connect(':memory:')) as expected:
                importlib.import_module(module).initialize(expected)
                query = "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
                if c.execute(query).fetchall() != expected.execute(query).fetchall() or c.execute('PRAGMA user_version').fetchone() != expected.execute('PRAGMA user_version').fetchone():
                    raise ValueError('Unsupported discovery schema: ' + name)


def sanitize(path):
    with closing(sqlite3.connect(path)) as c, c:
        for key in ('desktop_setup', 'desktop_mode', 'desktop_remember_key', 'desktop_key_saved'):
            c.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)', (key,''))


def snapshot(source, target):
    if source.stat().st_size > MAX_DATABASE:
        raise ValueError('Database exceeds the 5 GB recovery limit')
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro', uri=True)) as src, closing(sqlite3.connect(target)) as dst:
        src.backup(dst)
    if target.stat().st_size > MAX_DATABASE:
        raise ValueError('Database exceeds the 5 GB recovery limit')


@contextmanager
def backup(profile, database):
    profile = Path(profile).resolve()
    before = stamp(profile)
    with tempfile.TemporaryDirectory(prefix='mindtrail-backup-') as directory:
        stage = Path(directory)
        entries = []
        total = 0
        inventory = files(profile)
        portable_paths(inventory)
        if sum(p.stat().st_size for p in inventory.values()) > MAX_BYTES:
            raise ValueError('Workspace exceeds the 20 GB backup limit')
        for name, source in sorted(inventory.items()):
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_symlink() or not source.is_file():
                raise ValueError('Invalid workspace source')
            with source.open('rb') as f:
                sqlite = f.read(16) == b'SQLite format 3\0'
            if sqlite:
                snapshot(source, target)
                validate_database(target, name)
                if name == CORE:
                    sanitize(target)
            else:
                if name == CORE or name.startswith('data/processed/discovery/'):
                    raise ValueError('Invalid workspace database')
                shutil.copyfile(source, target)
            total += target.stat().st_size
            if total > MAX_BYTES:
                raise ValueError('Workspace exceeds the 20 GB backup limit')
        marker = stage / profile_identity.NAME
        marker.write_text(json.dumps(profile_identity.origin(profile, database), sort_keys=True), encoding='utf-8')
        for target in sorted(p for p in stage.rglob('*') if p.is_file()):
            entries.append({'path': target.relative_to(stage).as_posix(), 'bytes': target.stat().st_size, 'sha256': digest(target)})
        if stamp(profile) != before:
            raise ValueError('Workspace changed during backup. Stop other writers and try again.')
        if sum(e['bytes'] for e in entries) > MAX_BYTES:
            raise ValueError('Workspace exceeds the 20 GB backup limit')
        manifest = {'format':'mindtrail-workspace', 'version':1, 'files':entries,
                    'totalBytes':sum(e['bytes'] for e in entries), 'omissions':OMISSIONS}
        manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(',',':')).encode()
        if len(manifest_bytes) > 16_000_000:
            raise ValueError('Workspace recovery inventory exceeds 16 MB')
        archive = stage / 'workspace.zip'
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as z:
            for entry in entries:
                info = zipfile.ZipInfo(entry['path'], (1980,1,1,0,0,0))
                info.external_attr = 0o100600 << 16
                with z.open(info,'w',force_zip64=True) as out, (stage/entry['path']).open('rb') as src:
                    shutil.copyfileobj(src,out,1024*1024)
            info = zipfile.ZipInfo('manifest.json', (1980,1,1,0,0,0))
            info.external_attr = 0o100600 << 16
            z.writestr(info, manifest_bytes)
        yield archive, manifest


def empty(profile, database):
    if (profile / profile_identity.NAME).exists():
        return False
    with closing(sqlite3.connect(Path(database).as_uri()+'?mode=ro', uri=True)) as c:
        for table in ('readings','labels','mobile_scores','activities'):
            if c.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone():
                return False
    retained = [name for name in files(profile) if name not in (CORE,profile_identity.NAME)]
    captures = any(any(root.glob('session-*')) for root in (profile/'data/raw/bluetooth',profile/'data/bluetooth'))
    return not retained and not captures


def extract(archive, stage):
    try:
        with zipfile.ZipFile(archive) as z:
            infos = z.infolist()
            if len(infos) > MAX_FILES+2 or len({i.filename.casefold() for i in infos}) != len(infos):
                raise ValueError('Duplicate or excessive backup members')
            manifests = [i for i in infos if i.filename == 'manifest.json']
            if len(manifests) != 1 or manifests[0].file_size > 16_000_000:
                raise ValueError('Missing or oversized recovery manifest')
            for info in infos:
                if (info.flag_bits & 1 or info.compress_type != zipfile.ZIP_STORED or info.is_dir()
                        or stat.S_IFMT(info.external_attr >> 16) not in (0,stat.S_IFREG)):
                    raise ValueError('Unsupported recovery archive member')
                if info.filename != 'manifest.json' and not allowed(info.filename):
                    raise ValueError('Invalid recovery archive path')
            manifest = json.loads(z.read('manifest.json'))
            if not isinstance(manifest,dict) or manifest.get('format') != 'mindtrail-workspace' or manifest.get('version') != 1:
                raise ValueError('Unsupported recovery format')
            entries = manifest.get('files')
            if not isinstance(entries,list) or len(entries) > MAX_FILES:
                raise ValueError('Invalid recovery inventory')
            expected = {}
            total = 0
            for e in entries:
                if (not isinstance(e,dict) or set(e) != {'path','bytes','sha256'} or not isinstance(e['path'],str) or not allowed(e['path'])
                        or type(e['bytes']) is not int or e['bytes'] < 0 or not isinstance(e['sha256'],str) or len(e['sha256']) != 64
                        or e['path'] in expected):
                    raise ValueError('Invalid recovery inventory entry')
                expected[e['path']] = e
                total += e['bytes']
            if total > MAX_BYTES or total != manifest.get('totalBytes'):
                raise ValueError('Invalid or excessive recovery size')
            if set(expected) != {i.filename for i in infos if i.filename != 'manifest.json'} or not {CORE, profile_identity.NAME} <= set(expected):
                raise ValueError('Recovery inventory does not match its files')
            portable_paths(expected)
            for name,e in expected.items():
                if z.getinfo(name).file_size != e['bytes']:
                    raise ValueError('Recovery file size mismatch')
                target = stage / name
                target.parent.mkdir(parents=True,exist_ok=True)
                with z.open(name) as src, target.open('xb') as out:
                    shutil.copyfileobj(src,out,1024*1024)
                os.chmod(target,0o600)
                if digest(target) != e['sha256']:
                    raise ValueError('Recovery checksum mismatch')
                if name == CORE or name.startswith('data/processed/discovery/'):
                    if target.stat().st_size > MAX_DATABASE:
                        raise ValueError('Recovery database exceeds 5 GB')
                    validate_database(target,name)
            profile_identity.origin(stage, stage/CORE)
            sanitize(stage/CORE)
            return manifest
    except (zipfile.BadZipFile, sqlite3.Error, json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValueError('Invalid or damaged workspace backup') from error


def restore(profile, database, archive, migrate):
    """Exclusive caller owns idle app state. Never replaces a nonempty workspace."""
    profile = Path(profile).resolve()
    if not empty(profile,database):
        raise ValueError('Restore is available only in a new, empty workspace before setup.')
    before = stamp(profile)
    with tempfile.TemporaryDirectory(dir=profile, prefix='.recovery-') as temporary:
        root = Path(temporary)
        stage, rollback = root/'stage', root/'rollback'
        stage.mkdir(); rollback.mkdir()
        manifest = extract(archive,stage)
        if stamp(profile) != before or not empty(profile,database):
            raise ValueError('Destination changed during validation; workspace unchanged.')
        owned = [CORE,CORE+'-wal',CORE+'-shm','data',profile_identity.NAME]
        moved = []; installed = []
        try:
            for name in owned:
                old = profile/name
                if old.exists():
                    old.replace(rollback/name); moved.append(name)
            for name in (CORE,'data',profile_identity.NAME):
                source = stage/name
                if source.exists():
                    source.replace(profile/name); installed.append(name)
            migrate()
        except BaseException:
            for name in (CORE, CORE+'-wal', CORE+'-shm', 'data', profile_identity.NAME):
                p = profile/name
                if installed:
                    if p.is_dir():shutil.rmtree(p)
                    else:p.unlink(missing_ok=True)
            for name in moved:
                (rollback/name).replace(profile/name)
            raise
        return manifest
