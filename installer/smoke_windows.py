"""Native Windows package smoke test. No real credentials or Bluetooth access."""
from __future__ import annotations
import argparse
import json
import os
import hashlib
import platform
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from contextlib import closing
from pathlib import Path


def wait_for_workspace(process, profile):
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError("Packaged app exited before starting")
        try:
            with (profile / "desktop.lock").open("rb") as source:
                source.seek(1)
                url = json.load(source)["url"]
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
            opener.open(url, timeout=1).close()
            with opener.open(url.split("/launch")[0] + "/api/desktop/state", timeout=1) as response:
                state = json.load(response)
            assert not state["ready"] and not state["keyConfigured"]
            with opener.open(url.split("/launch")[0] + "/welcome.html", timeout=1) as response:
                page = response.read().decode()
            assert 'id="api-key-guide"' in page and 'MindTrail Lab' in page
            with opener.open(url.split("/launch")[0] + "/index.html", timeout=1) as response:
                overview = response.read().decode()
            assert 'href="/scores.html"' not in overview, "Mobile score navigation must be removed"
            assert 'id="recorded-periods"' in overview
            assert 'id="personal-activity-manager"' in overview
            assert 'id="label-review"' in overview and 'id="label-precision"' in overview
            assert '/interval_time.js' in overview and 'id="label-start-offset"' in overview
            with opener.open(url.split("/launch")[0] + "/interval_time.js", timeout=1) as response:
                assert 'MindTrailTime' in response.read().decode('utf-8')
            with opener.open(url.split("/launch")[0] + "/api/local-time?value=2026-11-01T01:30:00.123", timeout=1) as response:
                choices=json.load(response)['choices']
            assert [c['offset'] for c in choices]==['-04:00','-05:00']

            with opener.open(url.split("/launch")[0] + "/api/activities", timeout=1) as response:
                vocabulary = json.load(response)
            assert vocabulary['activities'] == [] and vocabulary['total'] == 0
            with opener.open(url.split("/launch")[0] + "/scores.html", timeout=1) as response:
                archive = response.read().decode()
            assert 'id="legacy-scores-archive"' in archive and '/api/export/mobile-scores.csv' in archive
            with opener.open(url.split("/launch")[0] + "/eeg.html", timeout=1) as response:
                eeg = response.read().decode()
            assert 'log-mobile-score' not in eeg and 'mobile-score-list' not in eeg
            with opener.open(url.split("/launch")[0] + "/quality.html", timeout=1) as response:
                quality = response.read().decode()
            assert 'id="timeline"' in quality and '/quality.js' in quality
            with opener.open(url.split("/launch")[0] + "/api/analysis/state", timeout=1) as response:
                analysis = json.load(response)
            assert analysis['available'] is False, "Numerical analysis must not start in the stdlib bundle"
            assert analysis['explorationAvailable'] is False, "Exploration must have an honest unavailable state"
            for name,marker in [('review.html','id="review-form"'),('review.js','/api/review/undo'),('explore.html','id="pca"'),('explore.js','/api/exploration')]:
                with opener.open(url.split("/launch")[0]+'/'+name,timeout=1) as response:
                    assert marker in response.read().decode('utf-8')
            return
        except (OSError, ValueError, KeyError):
            time.sleep(.1)
    raise RuntimeError("Packaged app did not become ready")


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("Run this verification on Windows.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", type=Path)
    parser.add_argument("--installer", type=Path)
    parser.add_argument("--require-signed", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if bool(args.payload) == bool(args.installer):
        parser.error("Choose --payload or --installer")
    if args.require_signed and not args.installer:
        parser.error("--require-signed requires --installer")
    signer = shutil.which("signtool")
    if args.require_signed:
        if not signer:
            raise ValueError("Install the Windows SDK signing tools before release verification.")
        subprocess.run([signer, 'verify', '/pa', '/all', '/tw', str(args.installer.resolve())], check=True)
    if args.installer:
        import winreg
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall\AWEARPersonalLab", 0, winreg.KEY_READ | view):
                    raise ValueError("Use a clean Windows test account or disposable runner; MindTrail Lab is already installed for this user.")
            except FileNotFoundError:
                pass
    with tempfile.TemporaryDirectory(prefix="awear-package-test-") as temporary:
        folder = Path(temporary)
        profile = folder / "profile"
        # Installer/upgrade/uninstaller stop commands must also use this profile,
        # never the tester's real archive or credential-store namespace.
        environment = {**os.environ, "AWEAR_DATA_DIR": str(profile)}
        installed = args.installer is not None
        root = folder / "installed" if installed else args.payload.resolve()
        if installed:
            # NSIS consumes /D through the end of the raw command line, including
            # spaces; do not quote that switch or send it through a shell.
            subprocess.run(f'"{args.installer.resolve()}" /S /D={root}', check=True, env=environment, timeout=120)
            if args.require_signed:
                subprocess.run([signer, 'verify', '/pa', '/all', '/tw', str(root / 'Uninstall.exe')], check=True)
        executable = root / "runtime" / "python.exe"
        entry = root / "app" / "windows_entry.pyw"
        report = folder / "self-check.json"
        subprocess.run([str(executable), str(entry), "--self-check", str(report)], check=True, timeout=30, env=environment)
        checks = json.loads(report.read_text())
        assert all(checks.values()), checks
        # Exercise only a synthetic credential under this disposable profile's
        # unique vault namespace. Never load the real default profile or key.
        vault_check = """from pathlib import Path
import sys
from credential_store import CredentialStore
store = CredentialStore(Path(sys.argv[1]))
assert store.available
try:
    store.save('synthetic-windows-package-test')
    assert CredentialStore(Path(sys.argv[1])).get() == 'synthetic-windows-package-test'
finally:
    store.delete()
assert CredentialStore(Path(sys.argv[1])).get() == ''
"""
        subprocess.run([str(executable), '-c', vault_check, str(profile)], check=True, env=environment, timeout=30)
        checks['nativeVaultSaveReadDelete'] = True
        # Keep old/new captures and reports in place through a real upgrade.
        archive_files = {}
        for prefix, stamp in [('data/bluetooth', '20260501'), ('data/raw/bluetooth', '20260502')]:
            session = profile / prefix / f'session-{stamp}T000000Z-abcd1234'
            archive_files[session / 'session.json'] = '{"state":"completed","eegBlocks":1}'
            archive_files[session / 'eeg.csv'] = 'synthetic capture\n'
        for prefix, stamp in [('data', '20260501'), ('data/raw/cloud', '20260502')]:
            archive_files[profile / prefix / f'export-{stamp}T000000Z' / 'manifest.json'] = '{"participants":[]}'
        archive_files[profile / 'data/patterns/legacy.json'] = '{"synthetic":"legacy"}'
        archive_files[profile / 'data/processed/patterns/current.json'] = '{"synthetic":"current"}'
        for path, contents in archive_files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents, encoding='utf-8')
        archive_check = """from pathlib import Path
import sys
from archive_paths import ArchivePaths
from bluetooth_manager import BluetoothManager
profile = Path(sys.argv[1])
paths = ArchivePaths(profile)
assert len(paths.snapshots()) == 2
assert paths.pattern_report('legacy.json') == profile / 'data/patterns/legacy.json'
assert paths.pattern_report('current.json') == profile / 'data/processed/patterns/current.json'
manager = BluetoothManager(profile)
assert manager.status()['totalSessions'] == 2
for session in manager.status()['sessions']:
    assert manager.download_path(session['id'], 'eeg.csv').read_text() == 'synthetic capture\\n'
"""
        episode_check="""from pathlib import Path
import sys
import app,episode_review
app.configure_storage(Path(sys.argv[1]));app.setup_db()
with app.db() as c:
    existing=c.execute("SELECT id FROM labels WHERE participant_id='episode-qa'").fetchone()
if existing is None:
    label=app.add_label('episode-qa','Native review','Synthetic only',1000,10000)
    with app.db() as c:
        c.execute('BEGIN IMMEDIATE')
        result=episode_review.split(c,'episode-qa',label,5123,episode_review.label_version(c,'episode-qa',label))
    with app.db() as c:
        c.execute('BEGIN IMMEDIATE');episode_review.undo(c,'episode-qa',result['operationId'])
        c.execute("INSERT INTO readings VALUES('episode-qa',20000,'D','[-1,1]',2,0,1,2)")
    with app.db() as c:
        c.execute('BEGIN IMMEDIATE')
        task=episode_review.queue(c,'episode-qa')['items'][0]
        episode_review.decide(c,'episode-qa',task,'unknown')
with app.db() as c:
    assert tuple(c.execute("SELECT start_ms,end_ms,timing_precision FROM labels WHERE participant_id='episode-qa'").fetchone())==(1000,10000,'unknown')
    assert c.execute("SELECT COUNT(*) FROM episode_operations WHERE participant_id='episode-qa'").fetchone()[0]==4
    assert c.execute("SELECT kind FROM episode_decisions WHERE participant_id='episode-qa'").fetchone()[0]=='unknown'
"""
        subprocess.run([str(executable), '-c', episode_check, str(profile)],check=True,env=environment,timeout=30)
        checks['episodeSplitUndoAndUnknown']=True
        subprocess.run([str(executable), '-c', archive_check, str(profile)], check=True, env=environment, timeout=30)
        command = [str(executable), str(entry), "--data-dir", str(profile), "--no-browser", "--no-key-store"]
        process = subprocess.Popen(command, env=environment)
        try:
            wait_for_workspace(process, profile)
            subprocess.run(command, check=True, timeout=10, env=environment)
            if installed:
                with closing(sqlite3.connect(profile / 'awear_data.sqlite3')) as db:
                    with db:
                        db.execute("INSERT INTO labels(participant_id,label,start_ms,end_ms) VALUES('qa-only','Windows upgrade QA',1000,2000)")
                        activity_id = db.execute("SELECT activity_id FROM labels WHERE participant_id='qa-only'").fetchone()[0]
                        assert activity_id
                        db.execute("UPDATE labels SET review_state='uncertain',timing_precision='approximate',start_uncertainty_ms=5000 WHERE participant_id='qa-only'")
                        db.execute("UPDATE activities SET favorite=1,archived=1,version=version+1,updated_ms=2000 WHERE id=?",(activity_id,))
                        db.execute("INSERT INTO mobile_scores(participant_id,observed_ms,score,state,created_ms,updated_ms) VALUES('qa-only',1000,42,'calm',1000,1000)")
                subprocess.run(f'"{args.installer.resolve()}" /S /D={root}', check=True, env=environment, timeout=120)
                assert process.wait(timeout=10) == 0
                with closing(sqlite3.connect(profile / 'awear_data.sqlite3')) as db:
                    assert db.execute("SELECT label FROM labels WHERE participant_id='qa-only'").fetchone() == ('Windows upgrade QA',)
                    assert db.execute("SELECT score FROM mobile_scores WHERE participant_id='qa-only'").fetchone() == (42,)
                    assert db.execute("SELECT activity_id,recorded_label FROM labels WHERE participant_id='qa-only'").fetchone() == (activity_id,'Windows upgrade QA')
                    assert db.execute("SELECT favorite,archived FROM activities WHERE id=?",(activity_id,)).fetchone() == (1,1)
                    assert db.execute("SELECT COUNT(*) FROM activity_history WHERE activity_id=?",(activity_id,)).fetchone() == (2,)
                    assert db.execute("SELECT source,review_state,timing_precision,start_uncertainty_ms FROM labels WHERE participant_id='qa-only'").fetchone() == ('manual','uncertain','approximate',5000)
                process = subprocess.Popen(command, env=environment)
                wait_for_workspace(process, profile)
                subprocess.run([str(executable), '-c', archive_check, str(profile)], check=True, env=environment, timeout=30)
                checks['upgradePreservesActivityLabel'] = True
            subprocess.run([str(executable), str(entry), "--data-dir", str(profile), "--stop-running"], check=True, timeout=25, env=environment)
            assert process.wait(timeout=10) == 0
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
        if installed:
            subprocess.run([str(root / "Uninstall.exe"), "/S"], check=True, timeout=30, env=environment)
            # NSIS runs its uninstaller from a temporary copy; the first process
            # can return before that copy has finished removing installed files.
            for _ in range(100):
                if not root.exists():
                    break
                time.sleep(.1)
            else:
                raise RuntimeError("Uninstaller did not finish removing the temporary installation")
            with closing(sqlite3.connect(profile / 'awear_data.sqlite3')) as db:
                assert db.execute("SELECT label FROM labels WHERE participant_id='qa-only'").fetchone() == ('Windows upgrade QA',)
                assert db.execute("SELECT score FROM mobile_scores WHERE participant_id='qa-only'").fetchone() == (42,)
                assert db.execute("SELECT activity_id,recorded_label FROM labels WHERE participant_id='qa-only'").fetchone() == (activity_id,'Windows upgrade QA')
                assert db.execute("SELECT favorite,archived FROM activities WHERE id=?",(activity_id,)).fetchone() == (1,1)
                assert db.execute("SELECT COUNT(*) FROM activity_history WHERE activity_id=?",(activity_id,)).fetchone() == (2,)
                assert db.execute("SELECT source,review_state,timing_precision,start_uncertainty_ms FROM labels WHERE participant_id='qa-only'").fetchone() == ('manual','uncertain','approximate',5000)
            with closing(sqlite3.connect(profile / 'awear_data.sqlite3')) as db:
                assert db.execute("SELECT COUNT(*) FROM episode_operations WHERE participant_id='episode-qa'").fetchone()==(4,)
                assert db.execute("SELECT kind FROM episode_decisions WHERE participant_id='episode-qa'").fetchone()==('unknown',)
            checks['episodeLedgerAndUnknownPreserved']=True
            checks['personalActivityIdentityAndHistoryPreserved'] = True
            checks['labelEvidencePreserved'] = True
            checks['uninstallPreservesArchive'] = True
            checks['legacyMobileScoresPreserved'] = True
        assert all(path.read_text(encoding='utf-8') == contents for path, contents in archive_files.items())
        checks['archiveLayoutsPreserved'] = True
        checks['archivePathCompatibility'] = True
        result = {**checks, "singleInstance": True, "shutdown": True, "setupPageAndGuide": True, "mobileScoresRetired": True, "qualityPageAndUnavailableState": True, "personalActivityPageAndEmptyVocabulary": True, "dailyClockAndTimezoneChoices": True,
                  "installerExercised": installed, "platform": platform.platform(),
                  "signaturesVerified": args.require_signed,
                  "installerSha256": hashlib.sha256(args.installer.read_bytes()).hexdigest() if installed else None}
        if args.report:
            args.report.write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result))


if __name__ == "__main__":
    main()
