"""Installer integrity, target dependency resolution, and rollback contracts."""
import hashlib
import io
import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from installer_build import build_windows, fetch, unpack, validate_wheels, verify_mac_bundle_signature, notarize_mac_artifact
from installer.mac_install import install


class DependencyTests(unittest.TestCase):
    def wheel(self, root, name, version="1.0", tag="py3-none-any", dependencies=()):
        path = root / f"{name}-{version}-{tag}.whl"
        with zipfile.ZipFile(path, "w") as wheel:
            text = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
            text += "".join("Requires-Dist: " + dep + "\n" for dep in dependencies)
            wheel.writestr(f"{name}-{version}.dist-info/METADATA", text)
        return path

    def test_target_windows_markers_are_used_even_on_a_mac_builder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = self.wheel(root, "application", dependencies=('windows_only>=2; sys_platform == "win32"', 'mac_only; sys_platform == "darwin"'))
            with self.assertRaisesRegex(ValueError, "windows_only"):
                validate_wheels([app], "3.14")
            windows = self.wheel(root, "windows_only", "2.0")
            validate_wheels([app, windows], "3.14")
            incompatible = self.wheel(root, "windows_only", "1.0")
            with self.assertRaisesRegex(ValueError, "windows_only"):
                validate_wheels([app, incompatible], "3.14")

    def test_wrong_python_or_cpu_binary_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for tag in ("cp313-cp313-win_amd64", "cp314-cp314-win_arm64", "py2-none-any"):
                with self.assertRaisesRegex(ValueError, "Incompatible"):
                    validate_wheels([self.wheel(root, "binary", tag=tag)], "3.14")
            validate_wheels([self.wheel(root, "binary", tag="cp39-abi3-win_amd64")], "3.14")

    def test_checksum_failure_does_not_publish_a_download(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "runtime.zip"
            with patch("urllib.request.urlopen", return_value=io.BytesIO(b"incorrect content")):
                with self.assertRaisesRegex(ValueError, "Checksum"):
                    fetch("https://example.com/runtime.zip", target, hashlib.sha256(b"correct content").hexdigest())
            self.assertFalse(target.exists())
            self.assertFalse(target.with_suffix(".zip.download").exists())

    def test_zip_traversal_and_symlink_entries_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("../escape", "/absolute", "C:/escape", "folder\\escape", "package.data/scripts/install"):
                archive = root / "input.zip"
                with zipfile.ZipFile(archive, "w") as output:
                    info = zipfile.ZipInfo(name)
                    # Preserve the deliberately malformed name on Windows too.
                    info.filename = info.orig_filename = name
                    output.writestr(info, "bad")
                with self.assertRaises(ValueError):
                    unpack(archive, root / "output")
            with zipfile.ZipFile(root / "link.zip", "w") as output:
                info = zipfile.ZipInfo("link")
                info.create_system = 3
                info.external_attr = 0o120777 << 16
                output.writestr(info, "../../escape")
            with self.assertRaises(ValueError):
                unpack(root / "link.zip", root / "output")
            self.assertFalse((root / "output").exists())


class WindowsSigningTests(unittest.TestCase):
    def build(self, root, fail_at=None, signing_environment=None):
        calls = []
        def command(args, **kwargs):
            calls.append(args)
            step = 'compile' if args[0] == 'makensis' else args[1]
            if step == fail_at:
                raise subprocess.CalledProcessError(1, args)
            if step == 'compile':
                Path(next(arg.split('=', 1)[1] for arg in args if arg.startswith('-DOUTPUT='))).write_bytes(b'unsigned')
            elif step == 'sign':
                Path(args[-1]).write_bytes(b'signed and timestamped')
            return subprocess.CompletedProcess(args, 0)
        environment = {'AWEAR_WINDOWS_CERT_THUMBPRINT':'AA ' * 20, 'AWEAR_WINDOWS_SIGNTOOL':'',
            'AWEAR_WINDOWS_ARTIFACT_DLIB':'', 'AWEAR_WINDOWS_ARTIFACT_METADATA':''}
        environment.update(signing_environment or {})
        with patch('installer_build.ROOT', root), patch('installer_build.sys', SimpleNamespace(platform='win32')), patch.dict(os.environ, environment), patch('installer_build.shutil.which', side_effect=lambda name: {'makensis':'makensis','signtool':'C:/Windows SDK/signtool.exe'}.get(name)), patch('installer_build.stage_windows'), patch('installer_build.subprocess.run', side_effect=command):
            result = build_windows(SimpleNamespace(release=True))
        return result, calls

    def test_release_is_published_only_after_signing_and_timestamp_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output, calls = self.build(root)
            self.assertEqual(output.read_bytes(), b'signed and timestamped')
            self.assertTrue(any(arg.startswith('-DSIGNTOOL=') for arg in calls[0]))
            self.assertIn('-DSIGN_THUMBPRINT=' + 'AA' * 20, calls[0])
            self.assertIn('/tw', calls[-1])
            self.assertNotEqual(Path(calls[-1][-1]), output)
            self.assertEqual(list((root / 'dist').iterdir()), [output])

    def test_failed_compile_sign_or_verify_never_publishes_an_unsigned_release(self):
        for step in ('compile', 'sign', 'verify'):
            with self.subTest(step=step), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                with self.assertRaises(subprocess.CalledProcessError):
                    self.build(root, fail_at=step)
                self.assertEqual(list((root / 'dist').iterdir()), [])

    def test_a_mac_signtool_cannot_be_mistaken_for_the_windows_sdk(self):
        with patch('installer_build.sys', SimpleNamespace(platform='darwin')), patch.dict(os.environ, {'AWEAR_WINDOWS_CERT_THUMBPRINT':'A' * 40}), patch('installer_build.shutil.which', return_value='/opt/homebrew/bin/signtool'), patch('installer_build.stage_windows') as stage:
            with self.assertRaisesRegex(ValueError, 'requires Windows'):
                build_windows(SimpleNamespace(release=True))
            stage.assert_not_called()

    def artifact_environment(self, root, metadata=None):
        folder = (root / 'Signing files').resolve()
        folder.mkdir(exist_ok=True)
        dlib, config = folder / 'Azure.CodeSigning.Dlib.dll', folder / 'metadata.json'
        dlib.write_bytes(b'fixture, not an executable library')
        config.write_text(json.dumps(metadata if metadata is not None else {
            'Endpoint':'https://eus.codesigning.azure.net/',
            'CodeSigningAccountName':'mindtraillabtest',
            'CertificateProfileName':'mindtrail-lab-public'}), encoding='utf-8')
        return {'AWEAR_WINDOWS_CERT_THUMBPRINT':'', 'AWEAR_WINDOWS_SIGNTOOL':'',
            'AWEAR_WINDOWS_ARTIFACT_DLIB':str(dlib), 'AWEAR_WINDOWS_ARTIFACT_METADATA':str(config)}

    def test_artifact_signing_uses_same_identity_for_setup_and_uninstaller(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = self.artifact_environment(root)
            output, calls = self.build(root, signing_environment=environment)
            self.assertEqual(output.read_bytes(), b'signed and timestamped')
            for define, option, variable in (('SIGN_DLIB','/dlib','AWEAR_WINDOWS_ARTIFACT_DLIB'),
                    ('SIGN_METADATA','/dmdf','AWEAR_WINDOWS_ARTIFACT_METADATA')):
                self.assertIn('-D' + define + '=' + environment[variable], calls[0])
                self.assertEqual(calls[1][calls[1].index(option)+1], environment[variable])
            self.assertIn('http://timestamp.acs.microsoft.com', calls[1])
            self.assertNotIn('/sha1', calls[1])
            self.assertIn('/tw', calls[-1])
            self.assertNotEqual(Path(calls[-1][-1]), output)

    def test_artifact_signing_failure_does_not_publish_a_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = self.artifact_environment(root)
            with self.assertRaises(subprocess.CalledProcessError):
                self.build(root, fail_at='sign', signing_environment=environment)
            self.assertEqual(list((root / 'dist').iterdir()), [])

    def test_invalid_cloud_configuration_stops_before_staging(self):
        for endpoint in ('http://eus.codesigning.azure.net', 'https://codesigning.azure.net.evil.test',
                'https://user@eus.codesigning.azure.net', 'https://eus.codesigning.azure.net/path',
                'https://eus.codesigning.azure.net/?redirect=elsewhere', 'https://evil.test'):
            with self.subTest(endpoint=endpoint), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                environment = self.artifact_environment(root, {'Endpoint':endpoint,
                    'CodeSigningAccountName':'test', 'CertificateProfileName':'public'})
                with patch('installer_build.sys', SimpleNamespace(platform='win32')), patch.dict(os.environ, environment), patch('installer_build.shutil.which', return_value='signtool'), patch('installer_build.stage_windows') as stage:
                    with self.assertRaises(ValueError):
                        build_windows(SimpleNamespace(release=True))
                    stage.assert_not_called()
        for change in ({'AWEAR_WINDOWS_ARTIFACT_DLIB':''},
                {'AWEAR_WINDOWS_ARTIFACT_METADATA':'missing-file.json'},
                {'AWEAR_WINDOWS_CERT_THUMBPRINT':'A'*40}):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                environment = self.artifact_environment(Path(directory)) | change
                with patch('installer_build.sys', SimpleNamespace(platform='win32')), patch.dict(os.environ, environment), patch('installer_build.shutil.which', return_value='signtool'), patch('installer_build.stage_windows') as stage:
                    with self.assertRaises(ValueError):
                        build_windows(SimpleNamespace(release=True))
                    stage.assert_not_called()


class MacSigningTests(unittest.TestCase):
    def signature_result(self, text):
        return [subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 0, stdout="", stderr=text)]

    def test_signed_bundle_requires_developer_id_runtime_and_timestamp(self):
        metadata = "Authority=Developer ID Application: Example (TEAMID)\nCodeDirectory flags=0x10000(runtime)\nTimestamp=Oct 31, 2026\n"
        with patch("installer_build.subprocess.run", side_effect=self.signature_result(metadata)):
            self.assertEqual(verify_mac_bundle_signature(Path("application.app")), metadata)
        for incomplete in (metadata.replace("Authority=Developer ID Application: Example (TEAMID)\n", "Signature=adhoc\n"), metadata.replace("CodeDirectory flags=0x10000(runtime)\n", "Executable=/runtime/application\n"), metadata.replace("Timestamp=Oct 31, 2026", "Timestamp=none")):
            with self.subTest(metadata=incomplete), patch("installer_build.subprocess.run", side_effect=self.signature_result(incomplete)):
                with self.assertRaises(ValueError):
                    verify_mac_bundle_signature(Path("application.app"))

    def test_invalid_nested_signature_stops_before_reading_metadata(self):
        with patch("installer_build.subprocess.run", side_effect=subprocess.CalledProcessError(1, ["codesign"])) as command:
            with self.assertRaises(subprocess.CalledProcessError):
                verify_mac_bundle_signature(Path("application.app"))
            self.assertEqual(command.call_count, 1)


class MacNotarizationTests(unittest.TestCase):
    def test_selected_keychain_is_used_without_exposing_a_password(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "app.zip"
            result = subprocess.CompletedProcess([], 0, stdout=json.dumps({"status":"Accepted", "id":"test-submission"}), stderr="")
            with patch.dict(os.environ, {"AWEAR_NOTARY_PROFILE":"test-profile", "AWEAR_NOTARY_KEYCHAIN":str(Path(directory)/"login.keychain-db")}), patch("installer_build.subprocess.run", return_value=result) as run:
                receipt = notarize_mac_artifact(artifact)
            command = run.call_args.args[0]
            self.assertEqual(command[command.index("--keychain")+1], str(Path(directory)/"login.keychain-db"))
            self.assertEqual(command[command.index("--keychain-profile")+1], "test-profile")
            self.assertNotIn("--password", command)
            self.assertEqual(receipt["status"], "Accepted")

    def test_successful_process_exit_does_not_hide_an_apple_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "app.zip"
            result = subprocess.CompletedProcess([], 0, stdout=json.dumps({"status":"Invalid", "id":"rejected-submission"}), stderr="")
            with patch.dict(os.environ, {"AWEAR_NOTARY_PROFILE":"test-profile"}), patch("installer_build.subprocess.run", return_value=result):
                with self.assertRaisesRegex(ValueError, "Invalid"):
                    notarize_mac_artifact(artifact)
            receipt = json.loads(artifact.with_suffix(".zip.notary.json").read_text())
            self.assertEqual(receipt["id"], "rejected-submission")


class MacInstallTests(unittest.TestCase):
    def test_first_install_stops_an_external_session_before_launch_and_aborts_on_failure(self):
        for stop_fails in (False, True):
            with self.subTest(stop_fails=stop_fails), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                applications = root / 'Applications'
                calls = []
                def command(args, **kwargs):
                    calls.append(args)
                    if args[0] == '/usr/bin/ditto':
                        new = Path(args[-1]) / 'MindTrail Lab.app/Contents/MacOS/MindTrail Lab'
                        new.parent.mkdir(parents=True)
                        new.write_text('packaged app')
                    if stop_fails and '--stop-running' in args:
                        raise subprocess.CalledProcessError(1, args)
                    return subprocess.CompletedProcess(args, 0)
                with patch('installer.mac_install.subprocess.run', side_effect=command):
                    if stop_fails:
                        with self.assertRaises(subprocess.CalledProcessError):
                            install(root / 'payload.zip', applications)
                    else:
                        install(root / 'payload.zip', applications)
                stop_indexes = [i for i,args in enumerate(calls) if '--stop-running' in args]
                self.assertEqual(len(stop_indexes), 1)
                if stop_fails:
                    self.assertFalse((applications / 'MindTrail Lab.app').exists())
                    self.assertFalse(any(args[0] == '/usr/bin/open' for args in calls))
                else:
                    launch_index = next(i for i,args in enumerate(calls) if args[0] == '/usr/bin/open')
                    self.assertLess(stop_indexes[0], launch_index)
                    self.assertTrue((applications / 'MindTrail Lab.app').is_dir())

    def test_legacy_named_app_upgrade_stops_old_app_and_rolls_back_on_failure(self):
        for fail in (False, True):
            with self.subTest(launch_failure=fail), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                applications = root / 'Applications'
                legacy = applications / 'AWEAR Personal Lab.app'
                executable = legacy / 'Contents/MacOS/AWEAR Personal Lab'
                executable.parent.mkdir(parents=True)
                executable.write_text('original app')
                archive = root / 'profile/awear_data.sqlite3'
                archive.parent.mkdir()
                archive.write_bytes(b'recordings and labels')
                calls = []
                def command(args, **kwargs):
                    calls.append(args)
                    if args[0] == '/usr/bin/ditto':
                        new = Path(args[-1]) / 'MindTrail Lab.app/Contents/MacOS/MindTrail Lab'
                        new.parent.mkdir(parents=True)
                        new.write_text('renamed app')
                    if fail and args[0] == '/usr/bin/open':
                        raise subprocess.CalledProcessError(1, args)
                    return subprocess.CompletedProcess(args, 0)
                with patch('installer.mac_install.subprocess.run', side_effect=command):
                    if fail:
                        with self.assertRaises(subprocess.CalledProcessError):
                            install(root / 'payload.zip', applications)
                    else:
                        install(root / 'payload.zip', applications)
                stop_calls = [args for args in calls if '--stop-running' in args]
                self.assertEqual(len(stop_calls), 1)
                self.assertEqual(Path(stop_calls[0][0]).name, 'MindTrail Lab')
                self.assertEqual(archive.read_bytes(), b'recordings and labels')
                if fail:
                    self.assertEqual(executable.read_text(), 'original app')
                    self.assertFalse((applications / 'MindTrail Lab.app').exists())
                else:
                    self.assertFalse(legacy.exists())
                    self.assertEqual((applications / 'MindTrail Lab.app/Contents/MacOS/MindTrail Lab').read_text(), 'renamed app')

    def test_failed_upgrade_launch_rolls_back_the_app_and_preserves_user_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            applications = root / "Applications"
            executable = applications / "MindTrail Lab.app" / "Contents" / "MacOS" / "MindTrail Lab"
            executable.parent.mkdir(parents=True)
            executable.write_text("old working app")
            archive = root / "profile" / "awear_data.sqlite3"
            archive.parent.mkdir()
            archive.write_bytes(b"keep all recordings and labels")
            def command(args, **kwargs):
                if args[0] == "/usr/bin/ditto":
                    new = Path(args[-1]) / "MindTrail Lab.app" / "Contents" / "MacOS" / "MindTrail Lab"
                    new.parent.mkdir(parents=True)
                    new.write_text("new app")
                if args[0] == "/usr/bin/open":
                    raise subprocess.CalledProcessError(1, args)
                return subprocess.CompletedProcess(args, 0)
            with patch("installer.mac_install.subprocess.run", side_effect=command):
                with self.assertRaises(subprocess.CalledProcessError):
                    install(root / "payload.zip", applications)
            self.assertEqual(executable.read_text(), "old working app")
            self.assertEqual(archive.read_bytes(), b"keep all recordings and labels")
            self.assertEqual(list(applications.iterdir()), [applications / "MindTrail Lab.app"])

    def test_capture_must_stop_before_replacing_a_running_app(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            applications = root / "Applications"
            executable = applications / "MindTrail Lab.app" / "Contents" / "MacOS" / "MindTrail Lab"
            executable.parent.mkdir(parents=True)
            executable.write_text("original")
            def command(args, **kwargs):
                if args[0] == "/usr/bin/ditto":
                    new = Path(args[-1]) / "MindTrail Lab.app" / "Contents" / "MacOS" / "MindTrail Lab"
                    new.parent.mkdir(parents=True)
                    new.write_text("new")
                if "--stop-running" in args:
                    raise subprocess.CalledProcessError(1, args)
                return subprocess.CompletedProcess(args, 0)
            with patch("installer.mac_install.subprocess.run", side_effect=command):
                with self.assertRaises(subprocess.CalledProcessError):
                    install(root / "payload.zip", applications)
            self.assertEqual(executable.read_text(), "original")


class MobileScoreArchivePackagingTests(unittest.TestCase):
    def test_packaged_html_dependencies_are_present_in_the_offline_asset_manifest(self):
        from html.parser import HTMLParser
        from urllib.parse import urlsplit
        from installer_build import STATIC_FILES
        source=Path(__file__).resolve().parent.parent/'src'/'static'
        class Dependencies(HTMLParser):
            def __init__(self):super().__init__();self.paths=[]
            def handle_starttag(self,tag,attrs):
                attrs=dict(attrs)
                value=attrs.get('src') if tag=='script' else attrs.get('href') if tag=='link' and attrs.get('rel')=='stylesheet' else None
                if value and value.startswith('/'):self.paths.append(urlsplit(value).path.lstrip('/'))
        for name in STATIC_FILES:
            self.assertTrue((source/name).is_file(),name)
            if not name.endswith('.html'):continue
            parser=Dependencies();parser.feed((source/name).read_text(encoding='utf-8'))
            for dependency in parser.paths:
                with self.subTest(page=name,dependency=dependency):self.assertIn(dependency,STATIC_FILES)

    def test_package_check_rejects_mobile_navigation_and_keeps_archive_exports(self):
        from installer.smoke_windows import wait_for_workspace
        source = Path(__file__).resolve().parent.parent / "src" / "static"
        current = (source / "index.html").read_bytes()
        old = current + b'<a href="/scores.html">Mobile scores</a>'
        for body, accepted in [(current, True), (old, False)]:
            with self.subTest(accepted=accepted), tempfile.TemporaryDirectory() as directory:
                profile = Path(directory)
                (profile / "desktop.lock").write_bytes(b"\0" + json.dumps({"url":"http://127.0.0.1:1234/launch?test"}).encode())
                responses = iter([io.BytesIO(b""), io.BytesIO(b'{"ready":false,"keyConfigured":false}'),
                    io.BytesIO(b'<div id="api-key-guide">MindTrail Lab</div>'), io.BytesIO(body),io.BytesIO(b'MindTrailTime'),io.BytesIO(b'{"choices":[{"offset":"-04:00"},{"offset":"-05:00"}]}'),io.BytesIO(b'{"activities":[],"total":0}'),
                    io.BytesIO((source / "scores.html").read_bytes()), io.BytesIO((source / "eeg.html").read_bytes()),
                    io.BytesIO((source / "quality.html").read_bytes()), io.BytesIO(b'{"available":false,"explorationAvailable":false}'),io.BytesIO((source/'review.html').read_bytes()),io.BytesIO((source/'review.js').read_bytes()),io.BytesIO((source/'explore.html').read_bytes()),io.BytesIO((source/'explore.js').read_bytes()),io.BytesIO((source/'similarity.js').read_bytes()),io.BytesIO((source/'compare.html').read_bytes()),io.BytesIO((source/'compare.js').read_bytes()),io.BytesIO((source/'compare.css').read_bytes())])
                opener = SimpleNamespace(open=lambda *args, **kwargs: next(responses))
                with patch("installer.smoke_windows.urllib.request.build_opener", return_value=opener):
                    if accepted:
                        wait_for_workspace(SimpleNamespace(poll=lambda:None), profile)
                    else:
                        with self.assertRaisesRegex(AssertionError, "navigation must be removed"):
                            wait_for_workspace(SimpleNamespace(poll=lambda:None), profile)


if __name__ == "__main__":
    unittest.main()
