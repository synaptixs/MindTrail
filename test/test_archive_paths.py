"""Archive continuity and source-profile selection across the structural cleanup."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import app
from archive_paths import ArchivePaths
from bluetooth_manager import BluetoothManager

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("run_local", ROOT / "scripts" / "run-local.py")
run_local = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_local)


class SourceProfileTests(unittest.TestCase):
    def test_profile_precedence_and_invalid_configuration(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            config = root / "development.local.json"
            config.write_text(json.dumps({"data_directory": str(root / "retained")}))
            self.assertEqual(run_local.resolve_profile(None, {}, config), root / "retained")
            self.assertEqual(run_local.resolve_profile(None, {"AWEAR_DATA_DIR": str(root / "env")}, config), root / "env")
            self.assertEqual(run_local.resolve_profile(root / "explicit", {"AWEAR_DATA_DIR": str(root / "env")}, config), root / "explicit")
            config.write_text('{"data_directory": null}')
            self.assertIsNone(run_local.resolve_profile(None, {}, config))
            config.write_text('{"data_directory":"relative/path"}')
            with self.assertRaisesRegex(ValueError, "absolute"):
                run_local.resolve_profile(None, {}, config)
            config.write_text('{"AWEAR_API_KEY":"synthetic"}')
            with self.assertRaisesRegex(ValueError, "only data_directory"):
                run_local.resolve_profile(None, {}, config)

    def test_source_entry_points_work_from_another_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            environment = {**os.environ, "AWEAR_DATA_DIR": temporary, "AWEAR_API_KEY": ""}
            for command in ("app", "desktop", "export", "import", "activity-backfill", "device-backfill"):
                result = subprocess.run([sys.executable, str(ROOT / "scripts" / "run-local.py"),
                    "--profile", temporary, command, "--help"], cwd=temporary,
                    env=environment, capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, (command, result.stderr))
            self.assertFalse((Path(temporary) / "awear_data.sqlite3").exists())


class ArchiveContinuityTests(unittest.TestCase):
    def test_snapshots_order_across_layouts_without_moving_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = ArchivePaths(Path(temporary))
            legacy = paths.data / "export-20260503T000000Z"
            current = paths.cloud / "export-20260502T000000Z"
            for folder in (legacy, current):
                folder.mkdir(parents=True)
            self.assertEqual(paths.snapshots(), [legacy, current])
            duplicate = paths.cloud / legacy.name
            duplicate.mkdir()
            self.assertEqual(paths.snapshots(), [duplicate, current])
            self.assertTrue(legacy.is_dir())

    def test_pattern_read_fallback_and_write_destination_are_distinct(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = ArchivePaths(Path(temporary))
            old = paths.data / "patterns" / "report.json"
            old.parent.mkdir(parents=True)
            old.write_text('legacy')
            self.assertEqual(paths.pattern_report("report.json"), old)
            new = paths.pattern_report("report.json", for_write=True)
            self.assertEqual(new, paths.patterns / "report.json")
            new.parent.mkdir(parents=True)
            new.write_text('current')
            self.assertEqual(paths.pattern_report("report.json"), new)
            self.assertEqual(old.read_text(), 'legacy')

    def test_dashboard_uses_newest_complete_snapshot_across_both_layouts(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(app, 'DATA_ROOT', Path(temporary)):
            paths = ArchivePaths(Path(temporary))
            for parent, stamp, value in [(paths.data, '20260501', 'legacy'), (paths.cloud, '20260502', 'current')]:
                folder = parent / f'export-{stamp}T000000Z'
                (folder / 'P-TEST').mkdir(parents=True)
                (folder / 'manifest.json').write_text(json.dumps({'exportedAt':value,'participants':[{'participantId':'P-TEST'}]}))
                (folder / 'P-TEST' / 'sessions.json').write_text('[]')
                (folder / 'P-TEST' / 'stats.json').write_text('{}')
            (paths.cloud / 'export-20260503T000000Z').mkdir()
            self.assertEqual(app.saved_snapshot_tables('P-TEST')['exportedAt'], 'current')

    def test_legacy_and_current_bluetooth_sessions_remain_downloadable(self):
        with tempfile.TemporaryDirectory() as temporary:
            profile = Path(temporary).resolve()
            manager = BluetoothManager(profile)
            legacy = profile / 'data' / 'bluetooth' / 'session-20260501T000000Z-1234abcd'
            current = manager.directory / 'session-20260502T000000Z-1234abcd'
            for folder in (legacy, current):
                folder.mkdir(parents=True)
                (folder / 'session.json').write_text('{"state":"completed","eegBlocks":3}')
                (folder / 'eeg.csv').write_text('synthetic capture')
            self.assertEqual(manager.status()['totalSessions'], 2)
            self.assertEqual(manager.download_path(legacy.name, 'eeg.csv'), legacy / 'eeg.csv')
            self.assertEqual(manager.download_path(current.name, 'eeg.csv'), current / 'eeg.csv')
            self.assertEqual(manager.directory, profile / 'data' / 'raw' / 'bluetooth')
            duplicate = manager.directory / legacy.name
            duplicate.mkdir()
            (duplicate / 'session.json').write_text('{"state":"completed","eegBlocks":5}')
            self.assertEqual(manager.status()['totalSessions'], 2)
            # A duplicate current session is authoritative even if a file is absent.
            with self.assertRaisesRegex(ValueError, 'not found'):
                manager.download_path(legacy.name, 'eeg.csv')


if __name__ == '__main__':
    unittest.main()
