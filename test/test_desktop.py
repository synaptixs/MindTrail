"""Launcher tests use temporary archives, fake credentials and no Bluetooth radio."""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import app
from credential_store import CredentialStore
from desktop_runtime import DesktopController, InstanceLock, default_data_directory


class ProfileCompatibilityTests(unittest.TestCase):
    def test_renamed_app_reuses_legacy_profiles_but_creates_new_named_profiles(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            cases = [('darwin', home / 'Library' / 'Application Support', 'MindTrail Lab', 'AWEAR Personal Lab'),
                     ('win32', home / 'Local', 'MindTrail Lab', 'AWEAR Personal Lab'),
                     ('linux', home / 'share', 'mindtrail-lab', 'awear-personal-lab')]
            for system, parent, current_name, old_name in cases:
                with self.subTest(platform=system), patch('desktop_runtime.sys.platform', system), patch('desktop_runtime.Path.home', return_value=home), patch.dict(os.environ, {'LOCALAPPDATA':str(home / 'Local'), 'XDG_DATA_HOME':str(home / 'share')}, clear=True):
                    current, legacy = parent / current_name, parent / old_name
                    self.assertEqual(default_data_directory(), current)
                    legacy.mkdir(parents=True)
                    self.assertEqual(default_data_directory(), legacy)
                    current.mkdir()
                    self.assertEqual(default_data_directory(), current)
                    with patch.dict(os.environ, {'AWEAR_DATA_DIR':str(home / 'custom')}):
                        self.assertEqual(default_data_directory(), home / 'custom')

    def test_existing_vault_identity_survives_display_name_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            digest = hashlib.sha256(os.path.normcase(str(directory.resolve())).encode()).hexdigest()[:16]
            store = CredentialStore(directory, disabled=True)
            self.assertEqual(store.service, 'AWEAR Personal Lab ' + digest)


class MemoryStore:
    available = True
    key = ""
    def get(self): return self.key
    def save(self, key): self.key = key
    def delete(self): self.key = ""


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.logging = patch.object(app.Handler, "log_message")
        self.logging.start()
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.original_storage = app.DATA_ROOT, app.DB_PATH
        app.configure_storage(self.directory)
        app.setup_db()
        self.store = MemoryStore()
        self.collector = app.Collector("")
        self.controller = DesktopController(self.directory, self.collector, self.store)
        self.server = app.AppServer(("127.0.0.1", 0), self.collector, desktop=self.controller)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.cookie = ""

    def tearDown(self):
        self.collector.stop_event.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        app.DATA_ROOT, app.DB_PATH = self.original_storage
        self.temporary.cleanup()
        self.logging.stop()

    def request(self, path, body=None, *, method=None, extra_headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        headers = {"Cookie": self.cookie, "X-Awear-Local": "1"}
        if isinstance(body, dict):
            body = json.dumps(body)
            headers["Content-Type"] = "application/json"
        headers.update(extra_headers or {})
        connection.request(method or ("POST" if body is not None else "GET"), path, body, headers)
        response = connection.getresponse()
        result = response.read()
        status = response.status
        cookie = response.getheader("Set-Cookie")
        location = response.getheader("Location")
        connection.close()
        return status, result, cookie, location

    def launch(self):
        status, _, cookie, location = self.request("/launch?token=" + self.controller.secret)
        self.assertEqual(status, 303)
        self.cookie = cookie.split(";")[0]
        return location

    def test_binding_does_not_wait_for_reverse_dns(self):
        with patch("socket.getfqdn", side_effect=AssertionError("No DNS needed for a local listener")):
            server = app.AppServer(("127.0.0.1", 0), app.Collector(""))
            self.assertEqual(server.server_name, "localhost")
            self.assertGreater(server.server_port, 0)
            server.server_close()

    def test_access_needs_launcher_cookie_and_same_origin(self):
        self.assertEqual(self.request("/api/status")[0], 403)
        self.assertEqual(self.request("/api/desktop/bluetooth", {})[0], 403)
        self.assertEqual(self.request("/launch?token=wrong")[0], 403)
        self.assertEqual(self.launch(), "/welcome.html")
        self.assertEqual(self.request("/api/desktop/state")[0], 200)
        self.assertEqual(self.request("/api/desktop/bluetooth", {}, extra_headers={"Origin": "https://example.com"})[0], 403)
        self.assertEqual(self.request("/api/labels", {"label": "unconfigured"})[0], 400)

    def test_bluetooth_only_setup_and_restart_need_no_key_or_scan(self):
        self.launch()
        with patch.object(self.server.bluetooth, "scan", side_effect=AssertionError("No scan during setup")):
            self.assertEqual(self.request("/api/desktop/bluetooth", {})[0], 200)
        self.assertTrue(self.controller.ready())
        self.assertFalse(self.collector.enabled)
        first = json.loads(self.request("/api/participants")[1])["members"][0]["participantId"]
        self.assertTrue(first.startswith("LOCAL-"))
        self.request("/api/desktop/bluetooth", {})
        self.assertEqual(json.loads(self.request("/api/participants")[1])["members"][0]["participantId"], first)
        restored = DesktopController(self.directory, app.Collector(""), self.store)
        self.assertTrue(restored.ready())
        self.assertFalse(restored.collector.enabled)

    def test_key_validation_session_storage_and_source_selection(self):
        self.launch()
        members = [{"participantId": "P-EXAMPLE"}]
        key = "test-only-not-a-real-key"
        with patch.object(app.Collector, "participants", return_value=members):
            status, response, _, _ = self.request("/api/desktop/api-key", {"key": key, "remember": False})
        self.assertEqual(status, 200)
        self.assertNotIn(key.encode(), response)
        self.assertEqual(self.store.key, "")
        self.assertFalse(self.controller.ready())
        self.assertFalse(self.collector.enabled)
        with patch.object(self.collector, "start_sync", return_value=True) as sync:
            self.assertEqual(self.request("/api/select", {"participantId": "wrong", "syncStart": "2026-01-01T00:00:00Z"})[0], 400)
            self.assertEqual(self.request("/api/select", {"participantId": "P-EXAMPLE", "syncStart": "2026-01-01T00:00:00Z"})[0], 200)
            sync.assert_called_once()
        self.assertTrue(self.controller.ready())
        self.assertTrue(self.collector.enabled)
        for path in ("/api/status", "/api/desktop/state"):
            self.assertNotIn(key.encode(), self.request(path)[1])
        with app.db() as connection:
            self.assertNotIn(key, str(list(connection.execute("SELECT * FROM settings"))))
        self.assertFalse((self.directory / ".env").exists())
        restarted = DesktopController(self.directory, app.Collector(""), self.store)
        self.assertFalse(restarted.ready(), "Session key must not survive restart")

    def test_one_click_connect_selects_source_and_syncs_full_reported_history(self):
        self.launch()
        with patch.object(app.Collector, "participants", return_value=[{"participantId": "P-EXAMPLE"}]), patch.object(app.Collector, "request", return_value={"firstRecord": "2025-01-02T03:04:05Z"}), patch.object(self.collector, "start_sync", return_value=True) as sync:
            status, body, _, _ = self.request("/api/desktop/connect", {"key": "test-key"})
        self.assertEqual(status, 200)
        self.assertTrue(self.controller.ready())
        self.assertEqual(self.store.key, "test-key")
        self.assertEqual(json.loads(body)["syncStart"], "2025-01-02T03:04:05.000Z")
        self.assertNotIn(b"test-key", body)
        sync.assert_called_once()
        with app.db() as connection:
            self.assertEqual(app.setting(connection, "desktop_setup"), "1")
            self.assertEqual(app.setting(connection, "participant_id"), "P-EXAMPLE")

    def test_one_click_reconnect_retains_source_checkpoint_and_labels(self):
        self.launch()
        start = str(app.utc_ms("2025-01-02T03:04:05Z"))
        with app.db() as connection:
            for key, value in {"participant_id":"P-B", "sync_start_ms":start, "sync_checkpoint_ms":"1770000000000"}.items():
                app.put_setting(connection, key, value)
            connection.execute("INSERT INTO labels(participant_id,label,note,start_ms,end_ms) VALUES ('P-B','Reading','',1000,2000)")
        with patch.object(app.Collector, "participants", return_value=[{"participantId": "P-A"}, {"participantId": "P-B"}]), patch.object(app.Collector, "request", return_value={"firstSeen": "2025-01-02T03:04:05Z"}), patch.object(self.collector, "start_sync", return_value=True):
            response = self.request("/api/desktop/connect", {"key": "test-key"})
        self.assertEqual(response[0], 200)
        self.assertEqual(json.loads(response[1])["participantId"], "P-B")
        with app.db() as connection:
            self.assertEqual(app.setting(connection, "sync_checkpoint_ms"), "1770000000000")
            self.assertEqual(connection.execute("SELECT label FROM labels").fetchone()[0], "Reading")

    def test_one_click_vault_failure_uses_session_key_with_visible_notice(self):
        self.launch()
        with patch.object(app.Collector, "participants", return_value=[{"participantId": "P-EXAMPLE"}]), patch.object(app.Collector, "request", return_value={"firstRecord":"2025-01-02T03:04:05Z"}), patch.object(self.store, "save", side_effect=ValueError("Vault denied")), patch.object(self.collector, "start_sync", return_value=True):
            status, response, _, _ = self.request("/api/desktop/connect", {"key":"test-key"})
        self.assertEqual(status, 200)
        self.assertTrue(self.controller.ready())
        self.assertIn("session", json.loads(response)["notice"])
        self.assertIn(b"session", self.request("/api/status")[1])
        with app.db() as connection:
            self.assertEqual(app.setting(connection, "desktop_remember_key"), "0")

    def test_one_click_bad_key_leaves_previous_configuration_untouched(self):
        self.launch()
        with app.db() as connection:
            app.put_setting(connection, "participant_id", "P-ORIGINAL")
        with patch.object(app.Collector, "participants", side_effect=RuntimeError("private-response")):
            status, response, _, _ = self.request("/api/desktop/connect", {"key":"test-key"})
        self.assertEqual(status, 400)
        self.assertNotIn(b"private-response", response)
        self.assertEqual(self.collector.key, "")
        with app.db() as connection:
            self.assertEqual(app.setting(connection, "participant_id"), "P-ORIGINAL")

    def test_one_click_missing_stats_reports_seven_day_fallback(self):
        self.launch()
        with patch.object(app.Collector, "participants", return_value=[{"participantId": "P-EXAMPLE"}]), patch.object(app.Collector, "request", side_effect=RuntimeError("Unavailable")), patch.object(self.collector, "start_sync", return_value=True):
            status, response, _, _ = self.request("/api/desktop/connect", {"key":"test-key", "remember":False})
        self.assertEqual(status, 200)
        self.assertIn("seven days", json.loads(response)["notice"])

    def test_failed_verification_or_vault_write_does_not_activate_key(self):
        self.launch()
        with patch.object(app.Collector, "participants", side_effect=RuntimeError("secret-not-for-browser")):
            status, body, _, _ = self.request("/api/desktop/api-key", {"key": "test-key", "remember": False})
        self.assertEqual(status, 400)
        self.assertNotIn(b"secret-not-for-browser", body)
        self.assertEqual(self.collector.key, "")
        with patch.object(app.Collector, "participants", return_value=[{"participantId": "P-EXAMPLE"}]), patch.object(self.store, "save", side_effect=ValueError("Vault locked")):
            self.assertEqual(self.request("/api/desktop/api-key", {"key": "test-key", "remember": True})[0], 400)
        self.assertEqual(self.collector.key, "")

    def test_remember_and_forget_key_preserve_archive(self):
        self.launch()
        with patch.object(app.Collector, "participants", return_value=[{"participantId": "P-EXAMPLE"}]):
            self.assertEqual(self.request("/api/desktop/api-key", {"key": "test-key", "remember": True})[0], 200)
        restarted = DesktopController(self.directory, app.Collector(""), self.store)
        self.assertEqual(restarted.collector.key, "test-key")
        with app.db() as connection:
            connection.execute("INSERT INTO labels(participant_id,label,note,start_ms,end_ms) VALUES ('P-EXAMPLE','Reading','',1000,2000)")
        self.assertEqual(self.request("/api/desktop/forget-key", {})[0], 200)
        self.assertEqual(self.store.key, "")
        self.assertEqual(self.collector.key, "")
        with app.db() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM labels").fetchone()[0], 1)

    def test_restore_copies_labels_without_modifying_source_and_rejects_second_restore(self):
        source = self.directory / "source.sqlite3"
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(app.DB_PATH)) as origin, closing(sqlite3.connect(source)) as copy:
            origin.backup(copy)
        with closing(sqlite3.connect(source)) as copy:
            with copy:
                copy.execute("INSERT INTO labels(participant_id,label,note,start_ms,end_ms) VALUES ('P-EXAMPLE','Reading','original',1000,2000)")
        payload = source.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        self.launch()
        self.assertEqual(self.request("/api/desktop/import", payload)[0], 200)
        with app.db() as connection:
            self.assertEqual(connection.execute("SELECT label,note FROM labels").fetchone()[:], ("Reading", "original"))
        self.assertFalse(self.controller.ready())
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest)
        self.assertEqual(self.request("/api/desktop/import", payload)[0], 400)
        self.assertFalse(list(self.directory.glob("restore-*")))

    def test_restore_legacy_profile_upgrades_without_changing_original_assignments(self):
        import sqlite3
        from contextlib import closing
        source = self.directory / "legacy.sqlite3"
        with closing(sqlite3.connect(app.DB_PATH)) as origin, closing(sqlite3.connect(source)) as copy:
            origin.backup(copy)
            copy.execute("ALTER TABLE readings DROP COLUMN device_id")
            for name in ("source", "reviewed", "batch_id"):
                copy.execute("ALTER TABLE labels DROP COLUMN " + name)
            copy.execute("INSERT INTO labels(participant_id,label,note,start_ms,end_ms) VALUES('P','Personal activity','Original note',1000,3000)")
            copy.execute("INSERT INTO readings(participant_id,timestamp_ms,waveform_json,sample_count,mean,rms,peak_to_peak) VALUES('P',1000,?,256,0,0,0)",(json.dumps([0]*256),))
            copy.commit()
        original = source.read_bytes()
        self.launch()
        self.assertEqual(self.request("/api/desktop/import", original)[0],200)
        with app.db() as connection:
            self.assertEqual(tuple(connection.execute("SELECT participant_id,label,note,start_ms,end_ms FROM labels").fetchone()),('P','Personal activity','Original note',1000,3000))
            self.assertEqual(tuple(connection.execute("SELECT waveform_json,device_id FROM readings").fetchone()),(json.dumps([0]*256),None))
        self.assertEqual(source.read_bytes(),original)

    def test_restore_empty_profile_remains_empty_and_usable(self):
        self.launch()
        import sqlite3
        from contextlib import closing
        source = self.directory / "empty.sqlite3"
        with closing(sqlite3.connect(app.DB_PATH)) as origin, closing(sqlite3.connect(source)) as copy:
            origin.backup(copy)
        self.assertEqual(self.request("/api/desktop/import", source.read_bytes())[0],200)
        with app.db() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM readings").fetchone()[0],0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM labels").fetchone()[0],0)
        self.assertFalse(self.controller.ready())

    def test_restore_invalid_file_leaves_empty_database_usable(self):
        self.launch()
        self.assertEqual(self.request("/api/desktop/import", b"not-sqlite" * 20)[0], 400)
        self.assertEqual(self.request("/api/desktop/bluetooth", {})[0], 200)

    def test_restore_is_blocked_by_captures_in_either_archive_layout(self):
        self.launch()
        for root in self.server.bluetooth.paths.bluetooth_roots:
            with self.subTest(layout=str(root)):
                session = root / "session-20260501T000000Z-abcd1234"
                session.mkdir(parents=True)
                status, body, _, _ = self.request("/api/desktop/import", b"not-sqlite" * 20)
                self.assertEqual(status, 400)
                self.assertIn(b"empty workspace", body)
                session.rmdir()

    def test_quality_api_auth_setup_and_responsiveness(self):
        self.assertEqual(self.request("/api/analysis/state")[0], 403)
        self.assertEqual(self.request("/api/analysis/start", {})[0], 403)
        self.launch()
        self.assertEqual(self.request("/api/analysis/start", {})[0], 400)
        self.request("/api/desktop/bluetooth", {})
        self.assertEqual(self.request("/quality.html")[0], 200)
        self.assertEqual(self.request("/quality.js")[0], 200)
        self.assertEqual(self.request("/quality.css")[0], 200)
        self.assertEqual(self.request("/api/analysis/start", {}, extra_headers={"Origin":"https://example.com"})[0], 403)
        self.assertEqual(self.request("/api/analysis/start", {}, extra_headers={"X-Awear-Local":""})[0], 403)
        with patch.dict(os.environ, {"AWEAR_BUNDLED_RUNTIME":"1"}):
            status,body,_,_=self.request("/api/analysis/state")
            self.assertEqual(status,200)
            self.assertFalse(json.loads(body)["available"])
            self.assertEqual(self.request("/api/analysis/start", {})[0], 400)
        with patch.object(self.server.analysis,"running",return_value=True):
            self.assertEqual(self.request("/api/desktop/bluetooth", {})[0],400)
            self.assertEqual(self.request("/api/status")[0],200)
            self.assertEqual(self.request("/eeg.html")[0],200)

    @unittest.skipUnless(__import__('importlib.util').util.find_spec('numpy'), 'optional NumPy')
    def test_quality_http_worker_and_report_download(self):
        self.launch()
        self.request("/api/desktop/bluetooth", {})
        self.assertEqual(self.request("/api/analysis/start", {})[0],202)
        import time
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            state=json.loads(self.request("/api/analysis/state")[1])
            if not self.server.analysis.running():break
            self.assertEqual(self.request("/eeg.html")[0],200)
            time.sleep(.03)
        self.assertEqual(state["job"]["state"],"completed",state)
        status,body,_,_=self.request("/api/analysis/report")
        self.assertEqual(status,200)
        self.assertEqual(json.loads(body)["coverage"]["sourceRecords"],0)
        self.assertEqual(self.request("/api/analysis/cancel", {})[0],200)

    def test_setup_is_blocked_during_capture(self):
        self.launch()
        with patch.object(self.server.bluetooth, "running", return_value=True):
            self.assertEqual(self.request("/api/desktop/bluetooth", {})[0], 400)
        self.assertFalse(self.controller.ready())


class LifecycleTests(unittest.TestCase):
    def test_lock_allows_one_owner_and_recovers_after_process_exit(self):
        with tempfile.TemporaryDirectory() as temporary:
            lock = InstanceLock(Path(temporary))
            self.assertTrue(lock.acquire())
            lock.publish({"instanceId": "test", "url": "http://127.0.0.1:1/"})
            contender = InstanceLock(Path(temporary))
            self.assertFalse(contender.acquire())
            self.assertEqual(contender.read()["instanceId"], "test")
            lock.close()
            self.assertTrue(contender.acquire())
            self.assertEqual(contender.read(), {})
            contender.close()
            process = subprocess.run([sys.executable, "-c", "from desktop_runtime import InstanceLock; from pathlib import Path; import sys,os; lock=InstanceLock(Path(sys.argv[1])); assert lock.acquire(); os._exit(0)", temporary], check=True, env={**os.environ, "PYTHONPATH": str(app.SOURCE_ROOT)})
            self.assertEqual(process.returncode, 0)
            self.assertTrue(lock.acquire())
            lock.close()

    def test_vault_can_be_disabled_without_accessing_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = CredentialStore(Path(temporary), disabled=True)
            self.assertFalse(store.available)
            self.assertEqual(store.get(), "")
            with self.assertRaises(ValueError):
                store.save("test-key")

    def test_worker_uses_profile_data_and_cross_platform_stop_file(self):
        from bluetooth_manager import BluetoothManager
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = BluetoothManager(root / "assets", data_directory=root / "profile", worker_command=[sys.executable, "fake-worker"])
            self.assertTrue(manager.available())
            manager.devices = [{"id": "test-device"}]
            with patch("bluetooth_manager.subprocess.Popen") as process:
                process.return_value.poll.return_value = None
                manager.start("test-device", 60, "LOCAL-test", "right")
                command = process.call_args.args[0]
                self.assertEqual(command[:2], [sys.executable, "fake-worker"])
                self.assertIn("--stop-file", command)
                self.assertNotIn("AWEAR_API_KEY", process.call_args.kwargs["env"])
                manager.stop()
                self.assertTrue((manager.directory / manager.session / ".stop-requested").exists())
                process.return_value.send_signal.assert_not_called()


if __name__ == "__main__":
    unittest.main()
