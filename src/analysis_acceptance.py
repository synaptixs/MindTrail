"""Read-only source-archive acceptance runner; all experiments use a new private copy.

No API keys, uploads, sync, Bluetooth access or model calls. Private evidence is
never a distribution artifact. Resource budgets are fixed before measurements.
"""
from __future__ import annotations
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import html
import http.client
import json
import math
from pathlib import Path
import platform
import sqlite3
import sys
import threading
import time

from analysis_manager import AnalysisManager, write_json


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def source_files(database: Path) -> dict:
    return {path.name: digest_file(path) for path in (database, Path(str(database) + '-wal')) if path.is_file()}


def label_digest(database: Path) -> str:
    digest = hashlib.sha256()
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as connection:
        columns = [row[1] for row in connection.execute('PRAGMA table_info(labels)')]
        if not columns:
            return digest.hexdigest()
        for row in connection.execute('SELECT * FROM labels ORDER BY id'):
            data = json.dumps(list(row), ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()
            digest.update(len(data).to_bytes(8, 'big'))
            digest.update(data)
    return digest.hexdigest()


def check_coverage(summary: dict, expected_records: int) -> None:
    c = summary['coverage']
    if c['sourceRecords'] != expected_records:
        raise ValueError('Source count differs from independent SQL count')
    if c['sourceRecords'] != sum(c[k] for k in ('excludedRecords', 'computedRecordSeconds',
                                              'featureFailureRecordSeconds', 'boundaryRemainderRecordSeconds')):
        raise ValueError('Disjoint record accounting failed')
    if c['computedWindows'] != c['flaggedWindows'] + c['unflaggedWindows']:
        raise ValueError('Window accounting failed')
    if c['excludedRecords'] != sum(c['excludedByPrimaryReason'].values()):
        raise ValueError('Exclusion reason counts do not reconcile')
    if c['computedRecordSeconds'] != 4 * c['computedWindows']:
        raise ValueError('Computed record-seconds differ from window geometry')
    if len(summary['bins']) > 1500:
        raise ValueError('Timeline bound exceeded')
    for item in summary['bins']:
        if not 0 <= item['flaggedUnionMs'] <= item['computedUnionMs'] <= item['endMsExclusive'] - item['startMs']:
            raise ValueError('Interval union accounting failed')
    for key in ('computedUnionMs', 'flaggedUnionMs'):
        if summary[key] != sum(b[key] for b in summary['bins']):
            raise ValueError('Summary duration differs from timeline union')
    bins = summary['bins']
    if bins:
        inventory = summary['sourceInventory']
        if bins[0]['startMs'] != inventory['firstTimestampMs'] or bins[-1]['endMsExclusive'] != inventory['lastTimestampMs'] + 1000:
            raise ValueError('Timeline does not cover full source extent')
        if any(a['endMsExclusive'] != b['startMs'] for a, b in zip(bins, bins[1:])):
            raise ValueError('Timeline bins contain a hole or overlap')


def check_windows(report_path: Path, database: Path, participant: str) -> int | None:
    """Independently check every emitted interval against persisted timestamp/device rows."""
    report = json.loads(report_path.read_text())
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as connection:
        columns = {row[1] for row in connection.execute('PRAGMA table_info(readings)')}
        device = 'device_id' if 'device_id' in columns else 'NULL'
        rows = {}
        for timestamp, device_id, count in connection.execute(
                f'SELECT timestamp_ms,{device},sample_count FROM readings WHERE participant_id=?', (participant,)):
            rows.setdefault(timestamp, []).append((device_id, count))
    last_end = None
    for window in report['windows']:
        start, end = window['startMs'], window['endMsExclusive']
        context = window['acquisition']
        if end - start != 4000 or (last_end is not None and start < last_end):
            raise ValueError('Window geometry or overlap failed')
        if context['participant_id'] != participant or context['source'] != 'awear-cloud' or context['placement'] != 'right':
            raise ValueError('Acquisition provenance failed')
        for timestamp in range(start, end, 1000):
            if rows.get(timestamp) != [(context['device_id'], 256)]:
                raise ValueError('Window crosses missing, ambiguous or incompatible source records')
        if len(window['sourceRecordDigests']) != 4 or window['sampleCount'] != 1024:
            raise ValueError('Window source trace is incomplete')
        if window['qualityAnalysisIdentity'] != report['analysisIdentity']:
            raise ValueError('Window/report version mismatch')
        last_end = end
    if len(report['windows']) != report['coverage']['computedWindows']:
        raise ValueError('Window payload is incomplete')
    return report['windows'][0]['startMs'] if report['windows'] else None


def percentile95(values: list[float]) -> float:
    if not values:
        raise ValueError('No navigation samples collected')
    return sorted(values)[max(0, math.ceil(.95 * len(values)) - 1)]


def navigation(port: int) -> float:
    started = time.perf_counter()
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    try:
        connection.request('GET', '/quality.html')
        response = connection.getresponse()
        body = response.read()
        if response.status != 200 or b'id="timeline"' not in body:
            raise ValueError('Dashboard navigation failed during analysis')
    finally:
        connection.close()
    return time.perf_counter() - started


def exercise_job(manager: AnalysisManager, participant: str, port: int, budget: dict,
                 cancel_stage: str | None = None) -> dict:
    started = time.perf_counter()
    manager.start(participant, {'normalize': True, 'timezone': 'America/New_York'})
    samples = []
    canceled_at = None
    while manager.running():
        samples.append(navigation(port))
        state = manager.state(participant)
        job = state['job']
        if cancel_stage and canceled_at is None and job['stage'] == cancel_stage and (
                cancel_stage != 'extracting' or job.get('done', 0) > 0):
            canceled_at = time.perf_counter()
            manager.cancel()
        if time.perf_counter() - started > budget['jobTimeoutSeconds']:
            manager.cancel()
            raise ValueError('Job timeout: partial evidence retained; no acceptance pass')
        time.sleep(.05 if cancel_stage else .2)
    state = manager.state(participant)
    elapsed = time.perf_counter() - started
    if state['job']['state'] != ('canceled' if cancel_stage else 'completed'):
        raise ValueError(f"Unexpected job outcome: {state['job']}")
    if cancel_stage and canceled_at is None:
        raise ValueError('Requested cancellation stage was not observed; cancellation check is inconclusive')
    return {'seconds': elapsed, 'peakMemoryBytes': state['job'].get('peakMemoryBytes'),
            'navigationSamples': len(samples), 'navigationP95Seconds': percentile95(samples),
            'navigationMaxSeconds': max(samples), 'reusedWindows': state['job'].get('reusedWindows'),
            'computedFeatures': state['job'].get('computedFeatures'),
            'cancelSeconds': time.perf_counter() - canceled_at if canceled_at else None,
            'state': state['job']['state'], 'summary': state['summary']}


def coverage_html(summaries: list[dict], destination: Path) -> None:
    parts = ['<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">',
             '<title>Private Phase 1 coverage review</title><style>body{font:16px system-ui;max-width:1100px;margin:40px auto;padding:20px;color:#17352e}table{border-collapse:collapse;width:100%}td,th{padding:8px;text-align:left;border-bottom:1px solid #dde6e0}.timeline{table-layout:fixed}.timeline td:first-child{width:48%;overflow-wrap:anywhere}.timeline td:last-child{width:52%}.bar{display:flex;height:18px;background:#e6ece8}.bar i{display:block}small{color:#52665d}</style>',
             '<h1>Phase 1 coverage review</h1><p>Cloud EEG only · right ear · RIGHT_TEMP · 256 Hz. Bluetooth files are not analyzed. Screening cues are not verified artifacts or clinical states.</p>']
    for index, summary in enumerate(summaries, 1):
        c = summary['coverage']
        parts.append(f'<h2>Participant {index}</h2><p>Analysis identity: <small>{html.escape(summary["analysisIdentity"])}</small></p><table>')
        for key in ('sourceRecords', 'computedWindows', 'flaggedWindows', 'excludedRecords', 'boundaryRemainderRecordSeconds', 'featureFailureRecordSeconds'):
            parts.append(f'<tr><th>{html.escape(key)}</th><td>{c[key]:,}</td></tr>')
        parts.append('</table><h3>Complete observed extent</h3><p>UTC periods; proportions within each period. Gray includes gaps, exclusions and incomplete windows. Green is computed/unflagged; amber is computed/flagged. No external assets.</p><table class="timeline">')
        for item in summary['bins']:
            duration = item['endMsExclusive'] - item['startMs']
            green = 100 * (item['computedUnionMs'] - item['flaggedUnionMs']) / duration
            amber = 100 * item['flaggedUnionMs'] / duration
            start = datetime.fromtimestamp(item['startMs'] / 1000, timezone.utc).isoformat()
            end = datetime.fromtimestamp(item['endMsExclusive'] / 1000, timezone.utc).isoformat()
            parts.append(f'<tr><td><small>{start} – {end}</small></td><td><div class="bar" role="img" aria-label="{(item['computedUnionMs']-item['flaggedUnionMs'])/1000:g} seconds unflagged, {item['flaggedUnionMs']/1000:g} flagged, {(duration-item['computedUnionMs'])/1000:g} not analyzed"><i style="width:{green:.6f}%;background:#288568"></i><i style="width:{amber:.6f}%;background:#b6803d"></i></div></td></tr>')
        parts.append('</table>')
    parts.append('</html>')
    destination.write_text('\n'.join(parts), encoding='utf-8')


def changed_snapshot(snapshot: Path, target: Path, participant: str, *, change_start_ms: int = 0) -> int:
    """Controlled edits on a disposable second copy, never the reference or input."""
    target.parent.mkdir(mode=0o700)
    with closing(sqlite3.connect(snapshot.as_uri() + '?mode=ro', uri=True)) as source, closing(sqlite3.connect(target)) as copy:
        source.backup(copy)
    with closing(sqlite3.connect(target)) as connection:
        connection.row_factory = sqlite3.Row
        first = connection.execute('SELECT * FROM readings WHERE participant_id=? AND timestamp_ms>=? ORDER BY timestamp_ms LIMIT 2', (participant, change_start_ms)).fetchall()
        last = connection.execute('SELECT * FROM readings WHERE participant_id=? ORDER BY timestamp_ms DESC LIMIT 1', (participant,)).fetchone()
        if len(first) < 2:
            raise ValueError('Reference too small for edit/delete/arrival acceptance')
        # A valid flat record is deliberate; quality changes must be recalculated.
        connection.execute('UPDATE readings SET waveform_json=? WHERE participant_id=? AND timestamp_ms=?',
                           (json.dumps([0] * 256), participant, first[0]['timestamp_ms']))
        connection.execute('DELETE FROM readings WHERE participant_id=? AND timestamp_ms=?', (participant, first[1]['timestamp_ms']))
        columns = list(last.keys())
        placeholders = ','.join('?' for _ in columns)
        for index in range(1, 5):
            values = dict(last)
            values['timestamp_ms'] = last['timestamp_ms'] + index * 1000
            connection.execute(f"INSERT INTO readings({','.join(columns)}) VALUES({placeholders})", [values[key] for key in columns])
        connection.commit()
        return connection.execute('SELECT COUNT(*) FROM readings WHERE participant_id=?', (participant,)).fetchone()[0]


def validate_budget(budget: dict) -> None:
    if not isinstance(budget, dict):
        raise ValueError('Invalid acceptance budget')
    required = ('referenceRecordLimit', 'coldSeconds', 'cachedSeconds', 'workerPeakBytes',
                'navigationP95Seconds', 'navigationMaxSeconds', 'cancelSeconds', 'jobTimeoutSeconds')
    if type(budget.get('version')) is not int or budget['version'] != 1 or any(type(budget.get(k)) not in (int, float) or not math.isfinite(budget[k]) or budget[k] <= 0 for k in required):
        raise ValueError('Invalid acceptance budget')
    if budget['jobTimeoutSeconds'] < max(budget['coldSeconds'], budget['cachedSeconds']):
        raise ValueError('Job timeout must allow the declared runtime budget')


def run_acceptance(database: Path, output: Path, budget: dict) -> dict:
    validate_budget(budget)
    import app  # Optional numerical imports stay in the isolated worker.
    database = database.expanduser().resolve()
    output = output.expanduser().resolve()
    if not database.is_file() or output.exists():
        raise ValueError('Choose an existing database and a new, private output folder')
    if not AnalysisManager(output, database).available():
        raise ValueError('This source Python environment needs the analysis dependencies')
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as connection:
        counts = [row[0] for row in connection.execute('SELECT COUNT(*) FROM readings GROUP BY participant_id')]
        if not counts or max(counts) > budget['referenceRecordLimit']:
            raise ValueError('Reference outside declared nonempty record envelope; revise protocol before running')
    output.mkdir(parents=True, mode=0o700)
    from importlib.metadata import version
    implementation_files = {path.name: digest_file(path) for path in sorted(Path(__file__).parent.glob('analysis_*.py'))}
    write_json(output / 'protocol.json', {'budget': budget, 'database': str(database), 'platform': platform.platform(),
                                        'python': sys.version, 'numpy': version('numpy'),
                                        'implementationFiles': implementation_files,
                                        'scope': 'all cloud participants; raw input preserved'})
    before = source_files(database)
    labels_before = label_digest(database)
    profile = output / 'reference'
    profile.mkdir(mode=0o700)
    snapshot = profile / 'awear_data.sqlite3'
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as source, closing(sqlite3.connect(snapshot)) as copy:
        source.backup(copy)
    with closing(sqlite3.connect(snapshot)) as connection:
        participants = list(connection.execute('SELECT participant_id,COUNT(*) FROM readings GROUP BY participant_id ORDER BY participant_id'))
        if not participants:
            raise ValueError('Reference has no cloud records; not a performance reference')
    original_storage = app.DATA_ROOT, app.DB_PATH
    app.configure_storage(profile)
    # No schema setup, credentials or sync: serve existing static navigation only.
    server = app.AppServer(('127.0.0.1', 0), app.Collector(''))
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .05}, daemon=True)
    thread.start()
    reference_hash = digest_file(snapshot)
    reference_labels = label_digest(snapshot)
    result = {'platform': platform.platform(), 'python': sys.version, 'budget': budget, 'participants': [], 'passed': False}
    summaries = []
    try:
        for index, (participant, count) in enumerate(participants, 1):
            row = {'participantIndex': index, 'sourceRecords': count}
            for name in ('cold', 'cached'):
                measured = exercise_job(server.analysis, participant, server.server_port, budget)
                check_coverage(measured['summary'], count)
                report = server.analysis.report_path(participant)
                report_hash = digest_file(report)
                if name == 'cold':
                    first_hash = report_hash
                    change_start = check_windows(report, snapshot, participant)
                    if change_start is None:
                        raise ValueError("Reference has no computed window for the mutation check")
                    summaries.append(measured['summary'])
                elif report_hash != first_hash:
                    raise ValueError('Cold/cached serialized reports differ')
                measured.pop('summary')
                row[name] = {**measured, 'reportSha256': report_hash, 'reportBytes': report.stat().st_size}
                write_json(output / 'progress.json', {'participantIndex': index, 'stage': name, 'measurement': row[name]})
            if digest_file(snapshot) != reference_hash or label_digest(snapshot) != reference_labels:
                raise ValueError('Analysis changed the frozen source or labels')
            previous = server.analysis.state(participant)['summary']['analysisIdentity']
            for stage in ('extracting', 'saving'):
                measured = exercise_job(server.analysis, participant, server.server_port, budget, cancel_stage=stage)
                if measured['summary']['analysisIdentity'] != previous:
                    raise ValueError('Canceled analysis replaced complete results')
                measured.pop('summary')
                row['cancel_' + stage] = measured
            changed = output / ('changed-' + str(index)) / 'awear_data.sqlite3'
            changed_count = changed_snapshot(snapshot, changed, participant, change_start_ms=change_start)
            hashes = []
            for name, manager in (
                    ('changedCached', AnalysisManager(profile, changed)),
                    ('changedFresh', AnalysisManager(output / ('fresh-' + str(index)), changed))):
                try:
                    measured = exercise_job(manager, participant, server.server_port, budget)
                    check_coverage(measured['summary'], changed_count)
                    report = manager.report_path(participant)
                    check_windows(report, changed, participant)
                    hashes.append(digest_file(report))
                    measured.pop('summary')
                    row[name] = {**measured, 'sourceRecords': changed_count, 'reportSha256': hashes[-1]}
                finally:
                    manager.close()
            if hashes[0] != hashes[1]:
                raise ValueError('Changed-source cached analysis differs from a fresh full run')
            result['participants'].append(row)
            write_json(output / 'progress.json', result)
        coverage_html(summaries, output / 'coverage.html')
        result['rawSourceBytesUnchanged'] = before == source_files(database)
        result['sourceLabelsUnchanged'] = labels_before == label_digest(database)
        result['frozenSourceBytesUnchanged'] = reference_hash == digest_file(snapshot)
        result['frozenLabelsUnchanged'] = reference_labels == label_digest(snapshot)
        failures = []
        for row in result['participants']:
            if row['sourceRecords'] > budget['referenceRecordLimit']:
                failures.append('Reference exceeds declared record envelope; budget requires explicit revision')
            for name in ('cold', 'cached', 'changedCached', 'changedFresh'):
                m = row[name]
                if m['seconds'] > budget[('cached' if name in ('cached', 'changedCached') else 'cold') + 'Seconds']: failures.append(name + ' runtime')
                if not m['peakMemoryBytes'] or m['peakMemoryBytes'] > budget['workerPeakBytes']: failures.append(name + ' worker memory')
                if m['navigationP95Seconds'] > budget['navigationP95Seconds'] or m['navigationMaxSeconds'] > budget['navigationMaxSeconds']: failures.append(name + ' navigation')
            for name in ('cancel_extracting', 'cancel_saving'):
                if row[name]['cancelSeconds'] > budget['cancelSeconds']: failures.append(name + ' latency')
        for key in ('rawSourceBytesUnchanged', 'sourceLabelsUnchanged', 'frozenSourceBytesUnchanged', 'frozenLabelsUnchanged'):
            if not result[key]: failures.append(key)
        result['failures'] = failures
        result['passed'] = not failures
    except Exception as error:
        result['error'] = str(error)
        raise
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        app.DATA_ROOT, app.DB_PATH = original_storage
        write_json(output / 'acceptance.json', result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path)
    parser.add_argument('--output', type=Path, required=True, help='New private evidence directory; never committed')
    parser.add_argument('--budget', type=Path, default=Path(__file__).resolve().parent.parent / 'config' / 'analysis-acceptance.json')
    args = parser.parse_args()
    try:
        result = run_acceptance(args.database, args.output, json.loads(args.budget.read_text()))
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, f'Acceptance stopped: {error}\n')
    print(json.dumps({'passed': result['passed'], 'failures': result['failures']}))
    if not result['passed']: parser.exit(1)


if __name__ == '__main__':
    main()
