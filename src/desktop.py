#!/usr/bin/env python3
"""Open the local dashboard, or focus the instance already serving this profile."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit


def worker_command(root: Path) -> list[str] | None:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--ble-worker"]
    python = root / ".venv-bluetooth" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if python.is_file():
        return [str(python), str(Path(__file__).resolve().with_name("bluetooth_collector.py"))]
    if importlib.util.find_spec("bleak"):
        return [sys.executable, str(Path(__file__).resolve().with_name("bluetooth_collector.py"))]
    return None


def existing_url(lock) -> str:
    # A second click can arrive while the first process is still starting.
    for _ in range(50):
        metadata = lock.read()
        url = metadata.get("url", "")
        try:
            parsed = urlsplit(url)
            if parsed.scheme == "http" and parsed.hostname == "127.0.0.1" and parsed.port:
                with urllib.request.urlopen(f"http://127.0.0.1:{parsed.port}/api/desktop/info", timeout=.25) as response:
                    info = json.load(response)
                if info.get("instanceId") == metadata.get("instanceId"):
                    return url
        except (ValueError, OSError):
            pass
        time.sleep(.1)
    raise ValueError("MindTrail Lab is already starting or closing. Wait a moment and open it again.")


def main() -> None:
    # A bundled BLE worker must not launch another dashboard or acquire its lock.
    if "--ble-worker" in sys.argv:
        sys.argv.remove("--ble-worker")
        for name in ("stdout", "stderr"):
            if getattr(sys, name) is None:
                setattr(sys, name, open(os.devnull, "w"))
        import bluetooth_collector
        bluetooth_collector.main()
        return

    if "--self-check" in sys.argv:
        # Package smoke check: imports only; no keychain reads, scan, or connection.
        import ssl
        import certifi
        ssl.create_default_context(cafile=certifi.where())
        from zoneinfo import ZoneInfo
        from bluetooth_collector import bluetooth_library
        from credential_store import CredentialStore
        bluetooth_library()
        ZoneInfo("America/New_York")
        report = {"bluetoothImports": True, "timezoneData": True, "httpsCertificates": True,
                  "nativeCredentialBackend": CredentialStore(Path.cwd()).available}
        Path(sys.argv[sys.argv.index("--self-check") + 1]).write_text(json.dumps(report))
        return

    from credential_store import CredentialStore
    from desktop_runtime import DesktopController, InstanceLock, default_data_directory
    import app

    parser = argparse.ArgumentParser(description="MindTrail Lab desktop launcher")
    parser.add_argument("--data-dir", type=Path, default=default_data_directory())
    parser.add_argument("--port", type=int, default=0, help="Default: choose an available local port")
    parser.add_argument("--no-browser", action="store_true", help="Start without opening the browser")
    parser.add_argument("--stop-running", action="store_true", help="Stop the current profile before an installer update")
    parser.add_argument("--no-key-store", action="store_true", help="Use session-only credentials")
    args = parser.parse_args()
    directory = args.data_dir.expanduser().resolve()
    lock = InstanceLock(directory)
    if not lock.acquire():
        url = existing_url(lock)
        if args.stop_running:
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
            opener.open(url, timeout=5).close()
            request = urllib.request.Request(url.split("/launch", 1)[0] + "/api/desktop/quit",
                data=b"{}", headers={"X-Awear-Local": "1", "Content-Type": "application/json"})
            opener.open(request, timeout=5).close()
            for _ in range(150):
                if lock.acquire():
                    lock.close()
                    return
                time.sleep(.1)
            raise ValueError("MindTrail Lab is still closing. Quit it from Settings, then run the installer again.")
        if not args.no_browser:
            webbrowser.open(url)
        return
    if args.stop_running:
        lock.close()
        return
    server = None
    collector = None
    log = None
    original_streams = (sys.stdout, sys.stderr)
    try:
        # Windowless builds have no stdout; keep diagnostics in the private profile.
        log = open(directory / "desktop.log", "a", encoding="utf-8")
        os.chmod(directory / "desktop.log", 0o600)
        sys.stdout = sys.stderr = log
        app.configure_storage(directory)
        app.setup_db()
        collector = app.Collector("")
        controller = DesktopController(directory, collector, CredentialStore(directory, args.no_key_store))
        server = app.AppServer(("127.0.0.1", args.port), collector, desktop=controller,
                               worker_command=worker_command(app.ROOT))
        url = f"http://127.0.0.1:{server.server_port}/launch?token={controller.secret}"
        lock.publish({"url": url, "instanceId": controller.instance_id})
        threading.Thread(target=collector.loop, daemon=True).start()
        if collector.enabled:
            collector.start_sync()
        if not args.no_browser:
            # HTTP listener is already bound; browser requests queue until serve_forever.
            webbrowser.open(url)
        print(f"MindTrail Lab is running on local port {server.server_port}.", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if collector:
            collector.stop_event.set()
        if server:
            server.server_close()
        lock.close()
        if log:
            sys.stdout, sys.stderr = original_streams
            log.close()


def run() -> None:
    try:
        main()
    except Exception as error:
        # Never surface API responses or credential values in launch dialogs.
        message = "MindTrail Lab could not start. Close any existing MindTrail Lab window and try again. "
        if isinstance(error, ValueError):
            message += str(error)
        else:
            message += "Check that your data folder is writable and has free space."
        if sys.stderr is not None:
            print(message, file=sys.stderr)
        try:
            if sys.platform == "win32":
                import ctypes
                ctypes.windll.user32.MessageBoxW(None, message, "MindTrail Lab", 0x10)
                raise SystemExit(1)
            if sys.platform == "darwin":
                from AppKit import NSAlert, NSApplication
                NSApplication.sharedApplication()
                alert = NSAlert.alloc().init()
                alert.setMessageText_("MindTrail Lab could not start")
                alert.setInformativeText_(message)
                alert.addButtonWithTitle_("OK")
                alert.runModal()
        except Exception:
            pass
        raise SystemExit(1)


if __name__ == "__main__":
    run()
