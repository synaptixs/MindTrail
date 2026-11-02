"""Standard-library bridge between the dashboard and the optional BLE process."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

SESSION_NAME = re.compile(r"session-\d{8}T\d{6}Z-[0-9a-f]{8}")
DOWNLOAD_FILES = {"raw-notifications.jsonl", "eeg.jsonl", "eeg.csv", "events.jsonl", "session.json", "collector.log"}


class BluetoothManager:
    def __init__(self, root: Path, data_directory: Path | None = None, worker_command: list[str] | None = None):
        self.root = root
        self.directory = (data_directory or root) / "data" / "bluetooth"
        self.python = root / ".venv-bluetooth" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        self.worker_command = worker_command
        self.lock = threading.Lock()
        self.process: subprocess.Popen | None = None
        self.session: str | None = None
        self.devices: list[dict] = []
        self.scanning = False
        self.stop_requested = False
        self.last_error = ""

    def available(self) -> bool:
        return bool(self.worker_command) or self.python.is_file()

    def command(self) -> list[str]:
        return self.worker_command or [str(self.python), str(Path(__file__).resolve().with_name("bluetooth_collector.py"))]

    @staticmethod
    def process_options() -> dict:
        return {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}

    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def require_environment(self) -> None:
        if not self.available():
            raise ValueError("Set up the optional Bluetooth environment first; see README → Direct Bluetooth")

    def scan(self) -> dict:
        self.require_environment()
        with self.lock:
            if self.running() or self.scanning:
                raise ValueError("Wait for the current Bluetooth operation to finish")
            self.scanning = True
            self.last_error = ""
        try:
            environment = os.environ.copy()
            environment.pop("AWEAR_API_KEY", None)
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.TemporaryDirectory(prefix="scan-", dir=self.directory) as temporary:
                result_file = Path(temporary) / "result.json"
                result = subprocess.run(self.command() + ["scan", "--result-file", str(result_file)],
                                        capture_output=True, text=True, timeout=45, env=environment,
                                        **self.process_options())
                payload = result_file.read_text(encoding="utf-8") if result_file.is_file() else result.stdout
            try:
                response = json.loads(payload)
            except ValueError as error:
                raise ValueError("Bluetooth discovery did not complete. Check Bluetooth permissions and the collector environment.") from error
            if result.returncode or response.get("error"):
                raise ValueError(response.get("error") or "Bluetooth discovery failed")
            self.devices = response["devices"]
            return response
        except (OSError, subprocess.TimeoutExpired, ValueError) as error:
            self.devices = []
            self.last_error = str(error)
            raise ValueError(str(error)) from error
        finally:
            self.scanning = False

    def start(self, device_id: str, seconds: int, participant: str, placement: str = "unknown") -> dict:
        self.require_environment()
        if isinstance(seconds, bool) or not isinstance(seconds, int) or not 1 <= seconds <= 3600:
            raise ValueError("Choose a recording length from 1 to 3600 seconds")
        if not participant:
            raise ValueError("Select a participant in Overview before recording")
        if placement not in ("right", "left", "unknown"):
            raise ValueError("Choose right, left, or unknown placement")
        with self.lock:
            if self.running() or self.scanning:
                raise ValueError("A Bluetooth operation is already running")
            if device_id not in {device["id"] for device in self.devices}:
                raise ValueError("Scan and select a discovered AWEAR device first")
            self.session = "session-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
            folder = self.directory / self.session
            folder.mkdir(parents=True, mode=0o700)
            environment = os.environ.copy()
            environment.pop("AWEAR_API_KEY", None)
            try:
                with os.fdopen(os.open(folder / "collector.log", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as log:
                    self.process = subprocess.Popen(self.command() + ["record", "--device", device_id, "--seconds", str(seconds), "--output", str(folder),
                        "--participant", participant, "--placement", placement, "--stop-file", str(folder / ".stop-requested")],
                        stdout=log, stderr=subprocess.STDOUT, env=environment, **self.process_options())
            except OSError as error:
                self.last_error = str(error)
                raise ValueError("Could not start the Bluetooth collector") from error
            self.stop_requested = False
            self.last_error = ""
        return {"sessionId": self.session}

    def stop(self) -> dict:
        with self.lock:
            if self.running():
                self.stop_requested = True
                # A file works for a windowless Windows worker as well as macOS.
                (self.directory / self.session / ".stop-requested").touch(mode=0o600)
        return {"stopping": self.running()}

    def close(self) -> None:
        self.stop()
        if self.process is not None:
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                # Preserve session files even if the Bluetooth stack fails to close.
                self.process.terminate()

    @staticmethod
    def read_json(path: Path) -> dict:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def status(self) -> dict:
        folders = sorted([folder for folder in self.directory.glob("session-*")
                          if folder.is_dir() and SESSION_NAME.fullmatch(folder.name)], reverse=True)
        sessions = []
        for folder in folders[:20]:
            summary = self.read_json(folder / "session.json") or self.read_json(folder / "status.json")
            sessions.append({"id": folder.name, "startedAt": summary.get("startedAt"),
                             "state": summary.get("state", "starting"), "eegBlocks": summary.get("eegBlocks", 0),
                             "deviceId": summary.get("deviceId"),
                             "files": sorted(name for name in DOWNLOAD_FILES if (folder / name).is_file())})
        current = self.read_json(self.directory / self.session / "status.json") if self.session else {}
        if self.session and not current:
            current = {"state": "starting" if self.running() else "error"}
        if current and not self.running() and current.get("state") not in {"completed", "stopped", "error"}:
            current.update(state="error", error="Collector exited before finalizing its status; partial files retained")
        return {"available": self.available(), "running": self.running(), "scanning": self.scanning,
                "stopRequested": self.stop_requested, "devices": self.devices, "error": self.last_error,
                "sessionId": self.session, "current": current, "sessions": sessions, "totalSessions": len(folders)}

    def download_path(self, session: str, name: str) -> Path:
        if not SESSION_NAME.fullmatch(session) or name not in DOWNLOAD_FILES:
            raise ValueError("Choose a valid Bluetooth session file")
        if self.running() and session == self.session:
            raise ValueError("Stop the Bluetooth recording before downloading its session files")
        folder = (self.directory / session).resolve()
        path = (folder / name).resolve()
        if folder.parent != self.directory.resolve() or path.parent != folder or not path.is_file():
            raise ValueError("Bluetooth session file not found")
        return path
