"""Desktop profile lifecycle and first-run routes. No hardware calls during setup."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import sys
import tempfile
import threading
import time
import uuid
from contextlib import closing
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

import app


APP_NAME = "MindTrail Lab"
LEGACY_PROFILE_NAME = "AWEAR Personal Lab"


def default_data_directory() -> Path:
    if os.environ.get("AWEAR_DATA_DIR"):
        return Path(os.environ["AWEAR_DATA_DIR"]).expanduser()
    if sys.platform == "darwin":
        parent = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        parent = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    else:
        parent = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
        current, legacy = parent / "mindtrail-lab", parent / "awear-personal-lab"
        return legacy if not current.exists() and legacy.is_dir() else current
    current, legacy = parent / APP_NAME, parent / LEGACY_PROFILE_NAME
    # Keep the path stable for existing archives and path-bound vault entries.
    return legacy if not current.exists() and legacy.is_dir() else current


class InstanceLock:
    """The OS releases the lock after a crash; stale metadata never owns the lock."""
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = directory / "desktop.lock"
        self.file = None

    def acquire(self) -> bool:
        self.file = self.path.open("a+b")
        os.chmod(self.path, 0o600)
        self.file.seek(0, 2)
        if self.file.tell() == 0:
            self.file.write(b"\0")
            self.file.flush()
        self.file.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            self.file = None
            return False
        self.file.seek(1)
        self.file.truncate()
        self.file.flush()
        return True

    def publish(self, metadata: dict) -> None:
        self.file.seek(1)
        self.file.truncate()
        self.file.write(json.dumps(metadata).encode())
        self.file.flush()

    def read(self) -> dict:
        try:
            with self.path.open("rb") as source:
                source.seek(1)  # Do not read the byte locked by msvcrt.
                return json.load(source)
        except (OSError, ValueError):
            return {}

    def close(self) -> None:
        if self.file:
            if sys.platform == "win32":
                import msvcrt
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            self.file.close()
            self.file = None


class DesktopController:
    def __init__(self, directory: Path, collector: app.Collector, store):
        self.directory = directory
        self.collector = collector
        self.store = store
        self.secret = secrets.token_urlsafe(32)
        self.instance_id = uuid.uuid4().hex
        self.cookie_name = "awear_" + hashlib.sha256(str(directory).encode()).hexdigest()[:12]
        self.members: list[dict] = []
        self.operation = threading.Lock()
        self.startup_error = ""
        with app.db() as connection:
            remember = app.setting(connection, "desktop_remember_key") == "1"
        if remember:
            try:
                self.collector.key = self.store.get()
            except ValueError as error:
                self.startup_error = str(error)
        self.collector.enabled = self.ready() and self.mode() == "api"

    def mode(self) -> str:
        with app.db() as connection:
            return app.setting(connection, "desktop_mode")

    def ready(self) -> bool:
        with app.db() as connection:
            configured = app.setting(connection, "desktop_setup") == "1"
            mode = app.setting(connection, "desktop_mode")
        return configured and (mode == "bluetooth" or (mode == "api" and bool(self.collector.key)))

    def authorized(self, handler) -> bool:
        cookie = SimpleCookie()
        try:
            cookie.load(handler.headers.get("Cookie", ""))
            value = cookie.get(self.cookie_name)
            return value is not None and hmac.compare_digest(value.value, self.secret)
        except (ValueError, TypeError):
            return False

    @staticmethod
    def redirect(handler, destination: str, cookie: str | None = None) -> None:
        handler.send_response(303)
        handler.send_header("Location", destination)
        handler.send_header("Content-Length", "0")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Referrer-Policy", "no-referrer")
        if cookie:
            handler.send_header("Set-Cookie", cookie)
        handler.end_headers()

    def get(self, handler, route) -> bool:
        if route.path == "/api/desktop/info":
            handler._json(200, {"app": APP_NAME, "instanceId": self.instance_id})
            return True
        if route.path == "/launch":
            token = parse_qs(route.query).get("token", [""])[0]
            if not hmac.compare_digest(token, self.secret):
                handler._json(403, {"error": "Open the desktop launcher to access this workspace."})
            else:
                self.redirect(handler, "/" if self.ready() else "/welcome.html",
                              f"{self.cookie_name}={self.secret}; HttpOnly; SameSite=Strict; Path=/")
            return True
        if route.path.startswith("/api/") and not self.authorized(handler):
            handler._json(403, {"error": "Open the desktop launcher to access this workspace."})
            return True
        if route.path == "/" and not self.ready():
            self.redirect(handler, "/welcome.html")
            return True
        if route.path == "/api/desktop/state":
            with app.db() as connection:
                participant = app.setting(connection, "participant_id")
                remembered = app.setting(connection, "desktop_key_saved", app.setting(connection, "desktop_remember_key")) == "1"
            handler._json(200, {"ready": self.ready(), "mode": self.mode(),
                "keyConfigured": bool(self.collector.key), "secureStorageAvailable": self.store.available,
                "remembered": remembered, "participantId": participant,
                "members": self.members, "dataDirectory": str(self.directory),
                "bluetoothAvailable": handler.server.bluetooth.status()["available"],
                "startupError": self.startup_error})
            return True
        if route.path == "/api/participants" and self.mode() == "bluetooth":
            with app.db() as connection:
                member = {"participantId": app.setting(connection, "participant_id"), "status": "local Bluetooth profile"}
            handler._json(200, {"members": [member]})
            return True
        return False

    def idle(self, server) -> None:
        # Also prevents the polling thread starting between checking and disabling.
        with self.collector.lock:
            if self.collector.running or server.bluetooth.running() or server.bluetooth.scanning or (getattr(server, "analysis", None) and server.analysis.running()):
                raise ValueError("Finish the current analysis, sync or Bluetooth operation before changing setup.")
            self.collector.enabled = False

    def connect(self, handler, body: dict) -> None:
        """Complete personal-account setup in one request, preserving existing data."""
        key = body.get("key", self.collector.key)
        if not isinstance(key, str) or not 1 <= len(key) <= 4096 or any(c.isspace() for c in key):
            raise ValueError("Enter your AWEAR API key without spaces.")
        remember = body.get("remember", True)
        if not isinstance(remember, bool):
            raise ValueError("Invalid secure-storage preference.")
        candidate = app.Collector(key)
        try:
            members = candidate.participants()
        except Exception as error:
            raise ValueError("Could not connect to AWEAR. Check your key and internet connection, then try again.") from error
        members = [m for m in members if isinstance(m, dict) and isinstance(m.get("participantId"), str) and m["participantId"]]
        ids = sorted({m["participantId"] for m in members})
        if not ids:
            raise ValueError("This API key has no accessible recordings account. Check your AWEAR account or key permissions.")
        with app.db() as connection:
            previous = app.setting(connection, "participant_id")
            previous_start = app.setting(connection, "sync_start_ms")
        # A personal key normally has one participant. Research keys retain the
        # current source, otherwise use a stable default; Settings offers others.
        participant = previous if previous in ids else ids[0]
        now = int(time.time() * 1000)
        notice = ""
        try:
            stats = candidate.request("/members/" + quote(participant, safe="") + "/stats")
            first = stats.get("firstRecord") or stats.get("firstSeen")
            start_ms = app.utc_ms(first) if first else now - 7 * 86400_000
            if not 0 < start_ms < now:
                raise ValueError("Invalid first-record timestamp")
            if not first and (stats.get("totalRecords") or stats.get("totalSamples")):
                notice = "AWEAR did not supply the first recording date. Sync starts seven days ago; change it in Settings to include older data."
        except Exception:
            start_ms = now - 7 * 86400_000
            notice = "Could not read the earliest recording date. Sync starts seven days ago; change it in Settings to include older data."
        if participant == previous and previous_start:
            start_ms = min(start_ms, int(previous_start))
        saved = False
        if remember and self.store.available:
            try:
                self.store.save(key)
                saved = True
            except ValueError:
                notice += " Your key could not be saved securely. It will work for this session; enter it again after quitting."
        elif remember:
            notice += " Secure key storage is unavailable. Your key will work for this session only."
        if len(ids) > 1:
            notice += f" Connected to {participant}; this key has {len(ids)} participants. Change the source in Settings if needed."
        with app.db() as connection:
            same = participant == previous and str(start_ms) == previous_start
            app.put_setting(connection, "participant_id", participant)
            app.put_setting(connection, "sync_start_ms", str(start_ms))
            if not same:
                app.put_setting(connection, "sync_checkpoint_ms", str(start_ms))
            app.put_setting(connection, "desktop_remember_key", "1" if saved else "0")
            if saved:
                app.put_setting(connection, "desktop_key_saved", "1")
            app.put_setting(connection, "desktop_mode", "api")
            app.put_setting(connection, "desktop_setup", "1")
            app.put_setting(connection, "desktop_notice", notice.strip())
        self.collector.key = key
        self.members = members
        self.startup_error = ""
        self.collector.enabled = True
        self.collector.start_sync()
        handler._json(200, {"ok": True, "participantId": participant, "syncStart": app.iso(start_ms), "notice": notice.strip()})

    def post(self, handler, route: str, body: dict) -> bool:
        if route == "/api/desktop/quit":
            handler._json(200, {"ok": True})
            threading.Thread(target=handler.server.shutdown, daemon=True).start()
            return True
        if route not in {"/api/desktop/api-key", "/api/desktop/bluetooth", "/api/desktop/forget-key", "/api/desktop/connect", "/api/select"}:
            return False
        if not self.operation.acquire(blocking=False):
            raise ValueError("Another setup operation is in progress. Try again shortly.")
        previous_enabled = self.collector.enabled
        try:
            self.idle(handler.server)
            if route == "/api/desktop/connect":
                self.connect(handler, body)
            elif route == "/api/desktop/api-key":
                key = body.get("key", "")
                remember = body.get("remember", False)
                if not isinstance(key, str) or not 1 <= len(key) <= 4096 or any(c.isspace() for c in key):
                    raise ValueError("Enter a valid API key without spaces.")
                if not isinstance(remember, bool):
                    raise ValueError("Choose whether to remember your key.")
                try:
                    members = app.Collector(key).participants()
                except Exception as error:
                    raise ValueError("Could not verify the API key with AWEAR. Check the key and your internet connection, then try again.") from error
                members = [m for m in members if isinstance(m, dict) and m.get("participantId")]
                if not members:
                    raise ValueError("AWEAR returned no participants for this key.")
                if remember:
                    self.store.save(key)
                self.collector.key = key
                self.members = members
                self.startup_error = ""
                with app.db() as connection:
                    app.put_setting(connection, "desktop_remember_key", "1" if remember else "0")
                    if remember:
                        app.put_setting(connection, "desktop_key_saved", "1")
                    app.put_setting(connection, "desktop_mode", "api")
                    app.put_setting(connection, "desktop_setup", "0")
                handler._json(200, {"members": members})
            elif route == "/api/select":
                participant = str(body.get("participantId", ""))
                if not self.collector.key or self.mode() != "api":
                    raise ValueError("Connect your API key in desktop setup first.")
                members = self.members or self.collector.participants()
                if participant not in {m.get("participantId") for m in members}:
                    raise ValueError("Choose a participant returned by your API key.")
                start_ms = app.utc_ms(body["syncStart"])
                if not 0 <= start_ms < int(time.time() * 1000):
                    raise ValueError("Sync start must be in the past.")
                with app.db() as connection:
                    # Keep an existing checkpoint when reconnecting the same source/start.
                    same = app.setting(connection, "participant_id") == participant and app.setting(connection, "sync_start_ms") == str(start_ms)
                    app.put_setting(connection, "participant_id", participant)
                    app.put_setting(connection, "sync_start_ms", str(start_ms))
                    if not same:
                        app.put_setting(connection, "sync_checkpoint_ms", str(start_ms))
                    app.put_setting(connection, "desktop_setup", "1")
                self.collector.enabled = True
                self.collector.start_sync()
                handler._json(200, {"ok": True})
            elif route == "/api/desktop/bluetooth":
                with app.db() as connection:
                    participant = app.setting(connection, "desktop_local_participant") or "LOCAL-" + uuid.uuid4().hex[:12]
                    app.put_setting(connection, "desktop_local_participant", participant)
                    app.put_setting(connection, "participant_id", participant)
                    app.put_setting(connection, "desktop_mode", "bluetooth")
                    app.put_setting(connection, "desktop_setup", "1")
                handler._json(200, {"ok": True})
            else:
                self.store.delete()
                self.collector.key = ""
                with app.db() as connection:
                    app.put_setting(connection, "desktop_remember_key", "0")
                    app.put_setting(connection, "desktop_key_saved", "0")
                handler._json(200, {"ok": True})
            return True
        except Exception:
            self.collector.enabled = previous_enabled
            raise
        finally:
            self.operation.release()

    def import_archive(self, handler, length: int) -> None:
        if not self.operation.acquire(blocking=False):
            raise ValueError("Another setup operation is in progress.")
        temporary = None
        previous_enabled = self.collector.enabled
        try:
            self.idle(handler.server)
            if not 16 <= length <= 5 * 1024**3:
                raise ValueError("Choose a SQLite archive under 5 GB.")
            with app.db() as connection:
                occupied = any(connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() for table in ("readings", "labels", "mobile_scores", "activities"))
            captures = any(any(root.glob("session-*")) for root in handler.server.bluetooth.paths.bluetooth_roots)
            if self.ready() or occupied or captures:
                raise ValueError("Restore is available only in a new, empty workspace before setup.")
            with tempfile.NamedTemporaryFile(dir=self.directory, prefix="restore-", suffix=".sqlite3", delete=False) as output:
                temporary = Path(output.name)
                remaining = length
                while remaining:
                    block = handler.rfile.read(min(1024 * 1024, remaining))
                    if not block:
                        raise ValueError("Upload was interrupted. Please select the archive again.")
                    output.write(block)
                    remaining -= len(block)
            try:
                with closing(sqlite3.connect(temporary.as_uri() + "?mode=ro&immutable=1", uri=True)) as source:
                    source.execute("PRAGMA trusted_schema=OFF")
                    if source.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise ValueError("Archive integrity check failed.")
                    app.personal_activities.validate_archive_objects(source)
                    for table, columns in {"settings": {"key", "value"}, "readings": {"participant_id", "timestamp_ms", "waveform_json", "sample_count", "mean", "rms", "peak_to_peak"}, "labels": {"id", "participant_id", "label", "note", "start_ms", "end_ms"}}.items():
                        actual = {row[1] for row in source.execute(f"PRAGMA table_info({table})")}
                        if not columns <= actual:
                            raise ValueError("Choose a MindTrail Lab SQLite archive.")
                    # Backup is transactional and safe with the destination's WAL readers.
                    with closing(sqlite3.connect(app.DB_PATH, timeout=15)) as destination:
                        source.backup(destination)
                app.setup_db()
                with app.db() as connection:
                    for name in ("desktop_setup", "desktop_mode", "desktop_remember_key", "desktop_key_saved"):
                        app.put_setting(connection, name, "")
                self.members = []
                self.collector.key = ""
                handler._json(200, {"ok": True})
            except sqlite3.Error as error:
                raise ValueError("Could not restore this file. Choose a valid AWEAR SQLite archive.") from error
        except Exception:
            self.collector.enabled = previous_enabled
            raise
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
            self.operation.release()
