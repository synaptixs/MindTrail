"""Bounded, deterministic audit bundles. No extraction, migrations or archive writes."""
from __future__ import annotations

from contextlib import closing, contextmanager
import csv
import hashlib
import io
import json
import sqlite3
import tempfile
import zipfile

from analysis_provenance import canonical_json, fingerprint

MAX_INPUT = 256_000_000
MAX_BUNDLE = 768_000_000


class Bundle:
    def __init__(self, output):
        self.zip = zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED)
        self.members = []
        self.bytes = 0

    def add(self, name, chunks, *, rows=None):
        info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
        info.create_system = 3
        info.external_attr = 0o100600 << 16
        size = 0
        digest = hashlib.sha256()
        with self.zip.open(info, 'w', force_zip64=True) as target:
            for chunk in chunks:
                if not isinstance(chunk, bytes):raise ValueError('Invalid export chunk')
                self.bytes += len(chunk)
                if self.bytes > MAX_BUNDLE:raise ValueError('Export exceeds 768 MB; narrow the analysis range. No files changed.')
                target.write(chunk)
                size += len(chunk)
                digest.update(chunk)
        item = {'path': name, 'bytes': size, 'sha256': digest.hexdigest()}
        if rows is not None:item['rows'] = rows
        self.members.append(item)

    def finish(self, manifest):
        manifest = dict(manifest, members=list(self.members), uncompressedBytes=self.bytes)
        manifest['bundleIdentity'] = fingerprint(manifest)
        self.add('manifest.json', json_chunks(manifest))
        self.zip.close()
        return manifest


def json_chunks(value):
    encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':'))
    pending = []
    for i, chunk in enumerate(encoder.iterencode(value)):
        pending.append(chunk)
        if i % 4096 == 0:
            yield ''.join(pending).encode('utf-8')
            pending = []
    if pending:yield ''.join(pending).encode('utf-8')


def quality_digest(value, check=lambda:None):
    digest=hashlib.sha256()
    for chunk in json_chunks(value):
        check()
        digest.update(chunk)
    return digest.hexdigest()


def table_chunks(rows, fields, safe):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream, lineterminator='\n')
    writer.writerow(fields)
    yield stream.getvalue().encode('utf-8')
    for row in rows:
        stream.seek(0)
        stream.truncate(0)
        values = []
        for field in fields:
            value = row.get(field)
            if isinstance(value, (dict, list)):value = canonical_json(value)
            values.append(safe(value) if isinstance(value, str) else '' if value is None else value)
        writer.writerow(values)
        yield stream.getvalue().encode('utf-8')


def flat_row(row, prefix=''):
    result = {}
    for key, value in sorted(row.items()):
        name = prefix + key
        if isinstance(value, dict):result.update(flat_row(value, name + '.'))
        else:result[name] = value
    return result


def file_signature(path):
    if path.is_symlink():raise ValueError('Invalid export source location')
    try:
        s = path.stat()
        return (s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_ino)
    except FileNotFoundError:return None


@contextmanager
def history_bundle(manager, participant):
    """Complete scoped portable numerical history; no claim of a profile restore format."""
    import analysis_history as history
    if not participant:raise ValueError('Select a participant first')
    with tempfile.TemporaryFile() as output:
        bundle = Bundle(output)
        try:
            headers = []
            # The owning history exporter validates all rows in one read transaction.
            for header, numerical in history.export_all(manager, participant):
                bundle.add('versions/' + header['version'] + '.json', json_chunks(numerical))
                headers.append(header)
            bundle.add('history.json', json_chunks({'formatVersion': 1, 'participantId': participant,
                'versions': headers, 'total': len(headers), 'shown': len(headers), 'omitted': 0}))
            bundle.finish({'formatVersion': 1, 'kind': 'retained_numerical_history', 'participantId': participant,
                'versions': len(headers), 'omittedVersions': 0,
                'scope': 'Complete retained captures for this source/participant, including never-activated captures; not an adoption ledger.',
                'omissions': ['Raw EEG', 'Feature-window rows pruned with jobs', 'Human annotations/questions/directions',
                    'Credentials', 'Other sources/participants', 'Automatic restore/import (not implemented)'],
                'capacity': {'versionsPerScope': history.MAX_VERSIONS, 'profilePayloadBytes': history.MAX_BYTES},
                'retention': 'Export does not clear history or raise its capacity. Keep the profile separately for recovery.'})
            output.seek(0)
            yield output
        finally:bundle.zip.close()


@contextmanager
def analysis_bundle(manager, participant, safe, *, identity, question=None, lens='recurrence_conditions'):
    """One source-matched current report, full windows and annotation audit; all-or-error."""
    import discovery_report_access as access
    from discovery_report_render import document
    from analysis_manager import read_json
    import discovery_context
    import personal_activities
    if not participant:raise ValueError('Select a participant first')
    if not identity:raise ValueError('Refresh the report before downloading its audit bundle')
    with tempfile.TemporaryFile() as output:
        bundle = Bundle(output)
        try:
            with manager.lock:
                value = access.page(manager, participant, identity, internal=True, question=question, lens=lens)
                if value['status'] != 'current':raise ValueError('A current discovery report is required before export')
                path = manager.report_path(participant)
            signature = file_signature(path)
            report = read_json(path, limit=MAX_INPUT)
            numerical = value['numerical']
            if not numerical.get('qualityReportDigest'):raise ValueError('Run analysis again to seal the full feature export')
            if quality_digest(report) != numerical['qualityReportDigest']:raise ValueError('Feature report content differs from its sealed snapshot')
            if (report['analysisIdentity'] != numerical['analysisIdentity'] or
                report['sourceInventory']['sourceRevision'] != numerical['sourceRevision']):
                raise ValueError('Feature report and discovery snapshot differ')
            windows = report['windows']
            if len(windows) != numerical['coverage']['computedWindows']:raise ValueError('Feature-window accounting differs')
            if any(w['acquisition']['participant_id'] != participant or w['qualityAnalysisIdentity'] != report['analysisIdentity'] for w in windows):
                raise ValueError('Feature-window scope differs')
            public = {k: v for k, v in value.items() if k != '_guard'}
            bundle.add('discovery-report.json', json_chunks(public))
            bundle.add('discovery-report.html', [document(value)])
            bundle.add('quality-report.json', json_chunks(report), rows=len(windows))
            # Canonical nested windows remain in JSON. CSV flattens map keys, preserving
            # arrays as JSON cells and units in the manifest, with no filename inference.
            flattened = (flat_row(w) for w in windows)
            fields = sorted({k for w in windows for k in flat_row(w)})
            if not fields:fields = ['windowId', 'startMs', 'endMsExclusive', 'acquisition', 'features', 'qualityFlags']
            bundle.add('feature-windows.csv', table_chunks(flattened, fields, safe), rows=len(windows))
            with closing(sqlite3.connect(manager.database.as_uri() + '?mode=ro', uri=True)) as c:
                c.row_factory = sqlite3.Row
                c.execute('PRAGMA query_only=ON')
                c.execute('BEGIN')
                bundle.add('activity-audit.json', personal_activities.export_json(c, participant))
                rows = c.execute('SELECT * FROM labels WHERE participant_id=? ORDER BY id', (participant,))
                columns = [r[0] for r in rows.description]
                count = c.execute('SELECT COUNT(*) FROM labels WHERE participant_id=?', (participant,)).fetchone()[0]
                bundle.add('activity-assignments.csv', table_chunks((dict(r) for r in rows), columns, safe), rows=count)
            bundle.add('window-context-audit.json', discovery_context.export(manager, participant))
            bundle.add('window-context.csv', discovery_context.export(manager, participant, 'csv', safe))
            bundle.finish({'formatVersion': 1, 'kind': 'current_analysis_audit', 'participantId': participant,
                'snapshotIdentity': value['snapshotIdentity'], 'analysisIdentity': numerical['analysisIdentity'],
                'sourceRevision': numerical['sourceRevision'], 'reportIdentity': numerical['reportIdentity'],
                'analysisContext': numerical.get('analysisContext'), 'timezone': numerical['timezone'],
                'requestedRange': numerical['requestedRange'], 'coverage': report['coverage'],
                'featureUnits': report['featureUnits'], 'bandNames': report['bandNames'], 'bandEdgesHz': report['bandEdgesHz'],
                'windowRows': len(windows), 'omittedComputedWindows': 0,
                'annotationScope': 'All current participant assignments and complete activity/context audit histories at export; not clipped to the numerical range. No automatic label-to-window attribution.',
                'omissions': ['Raw EEG samples', 'Bluetooth-file ingestion', 'Optional clustering/PCA sidecars (exported quality groups are not clusters)',
                    'Complete question/direction histories (report has its stated snapshot bounds)',
                    'Retained numerical history (use separate history ZIP)', 'Credentials', 'Automatic restore/import (not implemented)'],
                'meaning': 'Descriptive exploratory features and screening only. Right-ear cloud acquisition is retained in canonical window contexts. No activity, mental-state, biological or inferential conclusion.'})
            access.recheck(manager, value)
            if signature != file_signature(path):raise ValueError('Feature report changed while exporting; refresh and retry')
            del report, windows, flattened, public, value
            output.seek(0)
            yield output
        finally:bundle.zip.close()
