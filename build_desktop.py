"""Build a platform-native, self-contained desktop app from an isolated environment."""
from __future__ import annotations
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from pathlib import Path


def main() -> None:
    if sys.platform not in {"darwin", "win32"}:
        raise SystemExit("Build on macOS for Mac, or on Windows for Windows.")
    root = Path(__file__).resolve().parent
    from installer_build import make_icons
    icons = root / "build" / "icons"
    make_icons(icons)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(root / "awear-desktop.spec")], cwd=root, check=True,
                   env={**os.environ, "PYINSTALLER_CONFIG_DIR": str(root / "build" / "pyinstaller-cache")})
    metadata = {"platform": platform.platform(), "architecture": platform.machine(),
                "python": platform.python_version(),
                "packages": {package: importlib.metadata.version(package) for package in ("pyinstaller", "bleak", "keyring", "tzdata", "certifi")}}
    (root / "dist" / "build-info.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print("Build ready in dist. Test on a clean machine before distribution.")


if __name__ == "__main__":
    main()
