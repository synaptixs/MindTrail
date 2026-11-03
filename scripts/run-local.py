#!/usr/bin/env python3
"""Run canonical source against an explicit, existing local archive.

Precedence: --profile, AWEAR_DATA_DIR, config/development.local.json, then the
selected command's existing defaults. Configuration is source-only, never bundled.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parent.parent
COMMANDS = {"desktop": "desktop.py", "app": "app.py", "export": "export_data.py",
            "patterns": "analyze_patterns.py", "import": "import_snapshot.py",
            "activity-backfill": "backfill_activity_labels.py",
            "device-backfill": "backfill_device_ids.py"}


def resolve_profile(explicit: Path | None, environment: dict, config: Path) -> Path | None:
    value = explicit or environment.get("AWEAR_DATA_DIR")
    if not value and config.is_file():
        settings = json.loads(config.read_text(encoding="utf-8"))
        if not isinstance(settings, dict) or set(settings) - {"data_directory"}:
            raise ValueError("Local configuration supports only data_directory; keep API keys in the archive or key store")
        value = settings.get("data_directory")
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError("data_directory must be a nonempty path or null")
    if value is None or value == "":
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("The archive path must be absolute (or start with ~)")
    return path.resolve()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, help="Existing archive root; overrides environment and local config")
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        profile = resolve_profile(args.profile, os.environ, ROOT / "config" / "development.local.json")
    except (OSError, ValueError) as error:
        parser.error(str(error))
    if profile is not None:
        os.environ["AWEAR_DATA_DIR"] = str(profile)
    source = ROOT / "src"
    entry = source / COMMANDS[args.command]
    sys.path.insert(0, str(source))
    sys.argv = [str(entry), *args.arguments]
    runpy.run_path(str(entry), run_name="__main__")


if __name__ == "__main__":
    main()
