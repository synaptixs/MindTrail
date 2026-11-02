"""Prepare an isolated universal Python build environment; no system installation."""
from __future__ import annotations
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from installer_build import fetch


def main() -> None:
    if sys.platform != "darwin":
        raise SystemExit("Run this builder preparation on macOS.")
    package = fetch("https://www.python.org/ftp/python/3.14.10/python-3.14.10-macos11.pkg",
        ROOT / "build" / "installer-downloads" / "python-3.14.10-macos11.pkg",
        "286c7018aa2730f6747ca433dccdc2b31be45f078a9ea0ffa44d5f06368fd2d7")
    expanded = ROOT / "build" / "mac-python-expanded"
    if not expanded.exists():
        subprocess.run(["pkgutil", "--expand-full", str(package), str(expanded)], check=True)
    base = expanded / "Python_Framework.pkg" / "Payload" / "Versions" / "3.14"
    prefix = "/Library/Frameworks/Python.framework/Versions/3.14/"
    binaries = {base / "bin" / "python3.14", base / "Resources" / "Python.app" / "Contents" / "MacOS" / "Python"}
    binaries.update(p for p in base.rglob("*") if p.is_file() and not p.is_symlink() and p.suffix in {".so", ".dylib"})
    for path in sorted(binaries):
        result = subprocess.run(["otool", "-L", str(path)], capture_output=True, text=True, check=True)
        dependencies = {line.strip().split(" (")[0] for line in result.stdout.splitlines() if line.startswith("\t" + prefix)}
        changes = []
        for dependency in sorted(dependencies):
            changes += ["-change", dependency, "@loader_path/" + os.path.relpath(base / dependency[len(prefix):], path.parent)]
        if changes:
            subprocess.run(["install_name_tool", *changes, str(path)], check=True)
            subprocess.run(["codesign", "--force", "--sign", "-", str(path)], check=True)
    environment = ROOT / ".venv-desktop-universal"
    if not (environment / "bin" / "python").exists():
        subprocess.run([str(base / "bin" / "python3.14"), "-m", "venv", str(environment)], check=True)
    subprocess.run([str(environment / "bin" / "python"), "-m", "pip", "install", "-r", str(ROOT / "requirements-build.txt")], check=True)
    print("Universal builder prepared. Set AWEAR_MAC_ARCH=universal2 when building.")


if __name__ == "__main__":
    main()
