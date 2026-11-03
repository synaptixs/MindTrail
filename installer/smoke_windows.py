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
            with opener.open(url.split("/launch")[0] + "/scores.html", timeout=1) as response:
                archive = response.read().decode()
            assert 'id="legacy-scores-archive"' in archive and '/api/export/mobile-scores.csv' in archive
            with opener.open(url.split("/launch")[0] + "/eeg.html", timeout=1) as response:
                eeg = response.read().decode()
            assert 'log-mobile-score' not in eeg and 'mobile-score-list' not in eeg
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
        command = [str(executable), str(entry), "--data-dir", str(profile), "--no-browser", "--no-key-store"]
        process = subprocess.Popen(command, env=environment)
        try:
            wait_for_workspace(process, profile)
            subprocess.run(command, check=True, timeout=10, env=environment)
            if installed:
                with closing(sqlite3.connect(profile / 'awear_data.sqlite3')) as db:
                    with db:
                        db.execute("INSERT INTO labels(participant_id,label,start_ms,end_ms) VALUES('qa-only','Windows upgrade QA',1000,2000)")
                        db.execute("INSERT INTO mobile_scores(participant_id,observed_ms,score,state,created_ms,updated_ms) VALUES('qa-only',1000,42,'calm',1000,1000)")
                subprocess.run(f'"{args.installer.resolve()}" /S /D={root}', check=True, env=environment, timeout=120)
                assert process.wait(timeout=10) == 0
                with closing(sqlite3.connect(profile / 'awear_data.sqlite3')) as db:
                    assert db.execute("SELECT label FROM labels WHERE participant_id='qa-only'").fetchone() == ('Windows upgrade QA',)
                    assert db.execute("SELECT score FROM mobile_scores WHERE participant_id='qa-only'").fetchone() == (42,)
                process = subprocess.Popen(command, env=environment)
                wait_for_workspace(process, profile)
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
            checks['uninstallPreservesArchive'] = True
            checks['legacyMobileScoresPreserved'] = True
        result = {**checks, "singleInstance": True, "shutdown": True, "setupPageAndGuide": True, "mobileScoresRetired": True,
                  "installerExercised": installed, "platform": platform.platform(),
                  "signaturesVerified": args.require_signed,
                  "installerSha256": hashlib.sha256(args.installer.read_bytes()).hexdigest() if installed else None}
        if args.report:
            args.report.write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result))


if __name__ == "__main__":
    main()
