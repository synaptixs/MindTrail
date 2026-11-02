"""One-action per-user Mac installation; no administrator password or choices."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

NAME = "MindTrail Lab.app"
LEGACY_NAME = "AWEAR Personal Lab.app"


def install(payload: Path, applications: Path, *, launch: bool = True) -> Path:
    applications = applications.expanduser().resolve()
    applications.mkdir(parents=True, exist_ok=True)
    target = applications / NAME
    previous = target if target.exists() else applications / LEGACY_NAME
    backup = applications / (".awear-previous-" + uuid.uuid4().hex)
    with tempfile.TemporaryDirectory(prefix=".awear-install-", dir=applications) as temporary:
        staged = Path(temporary) / NAME
        subprocess.run(["/usr/bin/ditto", "-x", "-k", str(payload), temporary], check=True)
        executable = staged / "Contents" / "MacOS" / "MindTrail Lab"
        if not executable.is_file():
            raise ValueError("The installer payload is incomplete. Download the installer again.")
        subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(staged)], check=True, capture_output=True)
        if previous.exists():
            old_name = "MindTrail Lab" if previous == target else "AWEAR Personal Lab"
            old = previous / "Contents" / "MacOS" / old_name
            if not old.is_file():
                raise ValueError("Another app uses this installation name. Move it before installing MindTrail Lab.")
        # A first installation can share a profile with a copy launched from
        # Downloads or a development build. Stop that session before opening
        # the installed app, even when no previous app lives in Applications.
        subprocess.run([str(executable), "--stop-running"], check=True, timeout=25, capture_output=True)
        if previous.exists():
            previous.rename(backup)
        try:
            staged.rename(target)
            if launch:
                subprocess.run(["/usr/bin/open", "-a", str(target)], check=True)
        except Exception:
            if target.exists():
                shutil.rmtree(target)
            if backup.exists():
                backup.rename(previous)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-dir", type=Path, default=Path.home() / "Applications", help=argparse.SUPPRESS)
    parser.add_argument("--no-launch", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--report", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    payload = Path(__file__).resolve().parent / "payload" / "awear-app.zip"
    try:
        if os.geteuid() == 0:
            raise ValueError("Open Install MindTrail Lab as your normal Mac user.")
        target = install(payload, args.target_dir, launch=not args.no_launch)
        if args.report:
            args.report.write_text(json.dumps({"ok":True, "installed":str(target)}))
    except Exception as error:
        if args.report:
            args.report.write_text(json.dumps({"ok":False,"error":type(error).__name__}))
            raise SystemExit(1)
        # AppKit is native and avoids a terminal or a separate Tk installation.
        from AppKit import NSAlert, NSApplication
        NSApplication.sharedApplication()
        alert = NSAlert.alloc().init()
        alert.setMessageText_("MindTrail Lab could not be installed")
        alert.setInformativeText_(str(error) if isinstance(error, ValueError) else "Quit MindTrail Lab and try again. Check that your Applications folder is writable and has free space.")
        alert.addButtonWithTitle_("OK")
        alert.runModal()
        raise SystemExit(1)


if __name__ == "__main__":
    main()
