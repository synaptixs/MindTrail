"""Build offline consumer installers. Build artifacts never include user data."""
from __future__ import annotations

import argparse
import email
import hashlib
import json
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.request
from urllib.parse import urlsplit
import zipfile
import zlib
from pathlib import Path, PurePosixPath

VERSION = "0.2.7"
ROOT = Path(__file__).resolve().parent.parent
APP_FILES = ("app.py", "desktop.py", "desktop_runtime.py", "credential_store.py", "bluetooth_manager.py", "bluetooth_collector.py", "ble_protocol.py", "archive_paths.py", "analysis_manager.py", "personal_activities.py", "daily_review.py", "candidate_boundaries.py", "episode_review.py", "analysis_provenance.py", "exploration_access.py", "similar_periods.py", "activity_access.py", "discovery_contracts.py", "discovery_questions.py", "discovery_daily.py", "discovery_daily_access.py", "recurrence_access.py")
STATIC_FILES = tuple(name + extension for name in ("app", "data", "eeg", "patterns", "bluetooth", "welcome", "quality", "review", "explore", "compare", "discover", "recurrence") for extension in (".js", ".css"))
STATIC_FILES = tuple(name for name in STATIC_FILES if name != "app.css") + ("interval_time.js", "similarity.js", "style.css", "index.html", "data.html", "eeg.html", "patterns.html", "scores.html", "bluetooth.html", "welcome.html", "quality.html", "review.html", "explore.html", "compare.html", "discover.html", "discovery_daily.js", "recurrence.html")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fetch(url: str, path: Path, expected: str) -> Path:
    if not path.is_file() or sha256(path) != expected:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".download")
        try:
            with urllib.request.urlopen(url, timeout=90) as response, temporary.open("wb") as output:
                shutil.copyfileobj(response, output)
            if sha256(temporary) != expected:
                raise ValueError(f"Checksum mismatch for {path.name}; build stopped")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return path


def unpack(archive: Path, destination: Path) -> None:
    """Reject traversal, symlinks and wheel install schemes we do not support."""
    with zipfile.ZipFile(archive) as source:
        for info in source.infolist():
            # ZipInfo normalizes backslashes on Windows; validate the original
            # archive spelling so the same malformed member fails on every host.
            raw_name = info.orig_filename
            name = PurePosixPath(raw_name)
            if name.is_absolute() or ".." in name.parts or "\\" in raw_name or ":" in raw_name or "\0" in raw_name or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Unsafe archive member: " + raw_name)
            if any(part.endswith(".data") for part in name.parts):
                raise ValueError("Wheel requires an unsupported installation scheme: " + archive.name)
        source.extractall(destination)


def validate_wheels(wheels: list[Path], python_version: str) -> None:
    """Evaluate dependency markers for Windows, never for the build host."""
    from packaging.markers import default_environment
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name, parse_wheel_filename
    from packaging.tags import cpython_tags, compatible_tags
    from packaging.version import Version
    environment = {**default_environment(), "sys_platform": "win32", "platform_system": "Windows",
        "os_name": "nt", "platform_machine": "AMD64", "python_version": python_version,
        "python_full_version": python_version + ".10", "implementation_name": "cpython",
        "platform_python_implementation": "CPython", "extra": ""}
    target_version = tuple(int(value) for value in python_version.split("."))
    abi = "cp" + python_version.replace(".", "")
    supported = set(cpython_tags(target_version, abis=[abi], platforms=["win_amd64"])) | set(compatible_tags(target_version, interpreter=abi, platforms=["win_amd64"]))
    installed, metadata = {}, []
    for wheel in wheels:
        _, _, _, tags = parse_wheel_filename(wheel.name)
        if not supported.intersection(tags):
            raise ValueError("Incompatible Windows wheel: " + wheel.name)
        with zipfile.ZipFile(wheel) as archive:
            contents = email.message_from_bytes(archive.read(next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))))
        installed[canonicalize_name(contents["Name"])] = Version(contents["Version"])
        metadata.append(contents)
    for package in metadata:
        for raw in package.get_all("Requires-Dist", []):
            requirement = Requirement(raw)
            if requirement.marker and not requirement.marker.evaluate(environment):
                continue
            version = installed.get(canonicalize_name(requirement.name))
            if version is None or version not in requirement.specifier:
                raise ValueError(f"Missing/incompatible Windows dependency: {package['Name']} requires {requirement}")


def make_icons(directory: Path) -> None:
    """Draw the app's simple A mark with no external fonts or rendering libraries."""
    directory.mkdir(parents=True, exist_ok=True)
    def pixels(size):
        result = []
        for y in range(size):
            row = []
            for x in range(size):
                px, py = (x + .5) / size, (y + .5) / size
                # Rounded dark square, white M and the mint dot.
                near_x, near_y = max(.19-px, 0, px-.81), max(.19-py, 0, py-.81)
                inside = .02 < px < .98 and .02 < py < .98 and near_x**2 + near_y**2 < .17**2
                color = (16, 43, 43, 255) if inside else (0, 0, 0, 0)
                if .22 < py < .69:
                    diagonal = .29 + .21 * (py-.22) / .23
                    if abs(px-.29) < .035 or abs(px-.71) < .035 or (py < .45 and (abs(px-diagonal) < .035 or abs(px-(1-diagonal)) < .035)):
                        color = (245, 255, 247, 255)
                if (px-.5)**2 + (py-.81)**2 < .037**2:
                    color = (167, 242, 184, 255)
                row.append(color)
            result.append(row)
        return result
    size = 256
    data = b"".join(b"\0" + bytes(c for color in row for c in color) for row in pixels(size))
    def chunk(kind, content):
        return struct.pack(">I", len(content)) + kind + content + struct.pack(">I", zlib.crc32(kind + content))
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(data)) + chunk(b"IEND", b"")
    (directory / "awear.png").write_bytes(png)
    icon_chunk = b"ic08" + struct.pack(">I", 8 + len(png)) + png
    (directory / "awear.icns").write_bytes(b"icns" + struct.pack(">I", 8 + len(icon_chunk)) + icon_chunk)
    images = []
    for size in (16, 32, 48, 256):
        rows = pixels(size)
        bitmap = b"".join(bytes(channel for red, green, blue, alpha in row for channel in (blue, green, red, alpha)) for row in reversed(rows))
        mask_stride = ((size+31)//32)*4
        mask = bytearray(mask_stride*size)
        for y, row in enumerate(reversed(rows)):
            for x, color in enumerate(row):
                if color[3] == 0:
                    mask[y*mask_stride+x//8] |= 0x80 >> (x%8)
        header = struct.pack("<IiiHHIIiiII", 40, size, size*2, 1, 32, 0, len(bitmap), 0, 0, 0, 0)
        images.append((size, header+bitmap+mask))
    offset = 6 + 16*len(images)
    entries, content = [], []
    for size, image in images:
        entries.append(struct.pack("<BBBBHHII", size%256, size%256, 0, 0, 1, 32, len(image), offset))
        content.append(image)
        offset += len(image)
    (directory / "awear.ico").write_bytes(struct.pack("<HHH", 0, 1, len(images))+b"".join(entries+content))


def stage_windows(cache: Path, payload: Path) -> dict:
    lock = json.loads((ROOT / "installer" / "windows-runtime.lock.json").read_text())
    runtime = fetch(lock["python"]["url"], cache / lock["python"]["filename"], lock["python"]["sha256"])
    wheels = [fetch(item["url"], cache / item["filename"], item["sha256"]) for item in lock["wheels"]]
    validate_wheels(wheels, lock["python"]["minorVersion"])
    if payload.exists():
        shutil.rmtree(payload)
    payload.mkdir(parents=True)
    unpack(runtime, payload / "runtime")
    for wheel in wheels:
        unpack(wheel, payload / "vendor")
    # ._pth isolates this interpreter from global Python and the user's PYTHONPATH.
    minor = lock["python"]["minorVersion"].replace(".", "")
    (payload / "runtime" / f"python{minor}._pth").write_text(f"python{minor}.zip\n.\n../app\n../vendor\n", encoding="utf-8")
    (payload / "app" / "static").mkdir(parents=True)
    for name in APP_FILES:
        shutil.copy2(ROOT / "src" / name, payload / "app" / name)
    shutil.copy2(ROOT / "installer" / "windows_entry.pyw", payload / "app" / "windows_entry.pyw")
    for name in STATIC_FILES:
        shutil.copy2(ROOT / "src" / "static" / name, payload / "app" / "static" / name)
    make_icons(ROOT / "build" / "icons")
    shutil.copy2(ROOT / "build" / "icons" / "awear.ico", payload / "awear.ico")
    manifest = {"version": VERSION, "platform": "windows-x64", "python": lock["python"],
                "dependencies": [{"name": w["name"], "version": w["version"], "sha256": w["sha256"]} for w in lock["wheels"]],
                "files": {p.relative_to(payload).as_posix(): sha256(p) for p in sorted(payload.rglob("*")) if p.is_file()}}
    (payload / "package-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    notices = "MindTrail Lab includes the following third-party software.\nLicenses are retained in the runtime and each package's .dist-info directory.\n\nPython " + lock["python"]["version"] + " — Python Software Foundation License\n"
    notices += "\n".join(f"{w['name']} {w['version']}" for w in lock["wheels"]) + "\n"
    (payload / "THIRD-PARTY-NOTICES.txt").write_text(notices)
    return manifest


def windows_signing_options() -> tuple[str, list[str], list[str]]:
    """Select one signing provider; never put credentials in command arguments."""
    if sys.platform != "win32":
        raise ValueError("Release signing requires Windows and the Windows SDK signtool.")
    signer = os.environ.get("AWEAR_WINDOWS_SIGNTOOL") or shutil.which("signtool")
    if not signer:
        raise ValueError("Release signing requires Windows SDK signtool.")
    thumbprint = re.sub(r"\s+", "", os.environ.get("AWEAR_WINDOWS_CERT_THUMBPRINT", ""))
    dlib = os.environ.get("AWEAR_WINDOWS_ARTIFACT_DLIB", "")
    metadata = os.environ.get("AWEAR_WINDOWS_ARTIFACT_METADATA", "")
    defines = [f"-DSIGNTOOL={signer}"]
    if dlib or metadata:
        if thumbprint or not (dlib and metadata):
            raise ValueError("Choose one provider: Artifact Signing requires both DLIB and METADATA, without a certificate thumbprint.")
        dlib_path, metadata_path = Path(dlib).resolve(), Path(metadata).resolve()
        if not dlib_path.is_file() or not metadata_path.is_file():
            raise ValueError("Artifact Signing DLIB and METADATA must be existing files.")
        settings = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(settings, dict) or any(not isinstance(settings.get(key), str) or not settings[key].strip()
                for key in ("Endpoint", "CodeSigningAccountName", "CertificateProfileName")):
            raise ValueError("Artifact Signing metadata requires Endpoint, CodeSigningAccountName and CertificateProfileName.")
        endpoint = urlsplit(settings["Endpoint"])
        if endpoint.scheme != "https" or not re.fullmatch(r"[a-z0-9]+\.codesigning\.azure\.net", endpoint.netloc) or endpoint.path not in ("", "/") or endpoint.query or endpoint.fragment:
            raise ValueError("Artifact Signing endpoint must be a regional HTTPS codesigning.azure.net endpoint.")
        defines += [f"-DSIGN_DLIB={dlib_path}", f"-DSIGN_METADATA={metadata_path}"]
        identity = ["/dlib", str(dlib_path), "/dmdf", str(metadata_path)]
        timestamp = "http://timestamp.acs.microsoft.com"
    elif re.fullmatch(r"[0-9a-fA-F]{40}", thumbprint):
        defines += [f"-DSIGN_THUMBPRINT={thumbprint}"]
        identity = ["/sha1", thumbprint]
        timestamp = "http://timestamp.digicert.com"
    else:
        raise ValueError("AWEAR_WINDOWS_CERT_THUMBPRINT must be a certificate's 40 hexadecimal digit SHA-1 thumbprint.")
    return signer, ["sign", *identity, "/fd", "SHA256", "/tr", timestamp, "/td", "SHA256"], defines


def build_windows(args) -> Path:
    signer, sign_args, signing = windows_signing_options() if args.release else (None, [], [])
    compiler = shutil.which("makensis")
    if not compiler:
        raise ValueError("Install NSIS on this build machine (Homebrew makensis or the official Windows NSIS installer).")
    payload = ROOT / "build" / "windows-payload"
    stage_windows(ROOT / "build" / "installer-downloads", payload)
    output = ROOT / "dist" / f"MindTrail-Lab-{VERSION}-Windows-x64-Setup{'' if args.release else '-unsigned'}.exe"
    output.parent.mkdir(exist_ok=True)
    # Publish only after the compiler, signature and timestamp checks succeed.
    # A failed release must never leave an unsigned file under a release name.
    with tempfile.TemporaryDirectory(prefix=".windows-build-", dir=output.parent) as temporary:
        pending = Path(temporary) / output.name
        subprocess.run([compiler, "-V2", f"-DPAYLOAD_FILES={payload / '*.*'}", f"-DICON={payload / 'awear.ico'}", f"-DOUTPUT={pending}", *signing, str(ROOT / "installer" / "windows.nsi")], check=True)
        if args.release:
            subprocess.run([signer, *sign_args, str(pending)], check=True)
            subprocess.run([signer, "verify", "/pa", "/all", "/tw", str(pending)], check=True)
        pending.replace(output)
    return output


def verify_mac_bundle_signature(application: Path) -> str:
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(application)], check=True)
    signature = subprocess.run(["codesign", "-dv", "--verbose=4", str(application)], capture_output=True, text=True, check=True)
    lines = signature.stderr.splitlines()
    if not any(line.startswith("Authority=Developer ID Application:") for line in lines):
        raise ValueError("The application must have a Developer ID Application signature before signed packaging.")
    runtime = re.search(r"\bflags=0x[0-9a-fA-F]+\([^)]*\bruntime\b", signature.stderr)
    timestamp = any(line.startswith("Timestamp=") and line.split("=", 1)[1].strip().lower() not in {"", "none"} for line in lines)
    if not runtime or not timestamp:
        raise ValueError("Signed applications require hardened runtime and a trusted timestamp.")
    return signature.stderr


def notarize_mac_artifact(artifact: Path) -> dict:
    command = ["xcrun", "notarytool", "submit", str(artifact), "--keychain-profile",
               os.environ["AWEAR_NOTARY_PROFILE"], "--wait", "--output-format", "json"]
    # notarytool otherwise uses the data-protection Keychain, which differs
    # from an explicitly selected login.keychain-db credential store.
    if os.environ.get("AWEAR_NOTARY_KEYCHAIN"):
        command += ["--keychain", os.path.expanduser(os.environ["AWEAR_NOTARY_KEYCHAIN"])]
    print(f"Submitting {artifact.name} to Apple for notarization...", flush=True)
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    try:
        response = json.loads(result.stdout)
    except ValueError as error:
        raise ValueError("Apple returned an unreadable notarization result; no release was approved.") from error
    receipt = {key: response[key] for key in ("id", "status", "message", "name", "createdDate") if key in response}
    artifact.with_suffix(artifact.suffix + ".notary.json").write_text(json.dumps(receipt, indent=2) + "\n")
    if response.get("status") != "Accepted":
        raise ValueError(f"Apple notarization was not accepted: {response.get('status', 'unknown')}; submission {response.get('id', 'unknown')}. Receipt retained beside the artifact.")
    print(f"Apple accepted {artifact.name}; submission {response.get('id')}", flush=True)
    return receipt


def build_mac(args) -> Path:
    if sys.platform != "darwin":
        raise ValueError("Build the Mac installer on macOS.")
    sign_only = getattr(args, "sign_only", False)
    signed = args.release or sign_only
    if signed and not os.environ.get("AWEAR_MAC_SIGN_IDENTITY"):
        raise ValueError("Signed packaging requires a Developer ID Application identity.")
    if args.release and not os.environ.get("AWEAR_NOTARY_PROFILE"):
        raise ValueError("Release requires Developer ID Application and a notarytool profile. Development packages remain unsigned.")
    application = ROOT / "dist" / "MindTrail Lab.app"
    if not args.skip_app_build:
        subprocess.run([sys.executable, str(ROOT / "src" / "build_desktop.py")], check=True)
    if not application.is_dir():
        raise ValueError("Build the Mac application first.")
    if signed:
        verify_mac_bundle_signature(application)
    payload_folder = ROOT / "build" / "installer-payload"
    payload_folder.mkdir(parents=True, exist_ok=True)
    payload_archive = payload_folder / "awear-app.zip"
    subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(application), str(payload_archive)], check=True)
    if args.release:
        notarize_mac_artifact(payload_archive)
        subprocess.run(["xcrun", "stapler", "staple", str(application)], check=True)
        subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(application), str(payload_archive)], check=True)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(ROOT / "installer" / "mac-installer.spec")],
        cwd=ROOT, check=True, env={**os.environ, "PYINSTALLER_CONFIG_DIR": str(ROOT / "build" / "pyinstaller-cache")})
    if signed:
        verify_mac_bundle_signature(ROOT / "dist" / "Install MindTrail Lab.app")
    disk_root = ROOT / "build" / "mac-installer-disk"
    if disk_root.exists():
        shutil.rmtree(disk_root)
    disk_root.mkdir()
    subprocess.run(["ditto", str(ROOT / "dist" / "Install MindTrail Lab.app"), str(disk_root / "Install MindTrail Lab.app")], check=True)
    suffix = "" if args.release else "-signed-unnotarized" if sign_only else "-unsigned"
    disk = ROOT / "dist" / f"MindTrail-Lab-{VERSION}-Mac-{os.environ.get('AWEAR_MAC_ARCH', platform.machine())}{suffix}.dmg"
    disk.unlink(missing_ok=True)
    subprocess.run(["hdiutil", "create", "-volname", "Install MindTrail Lab", "-srcfolder", str(disk_root), "-format", "UDZO", str(disk)], check=True)
    if signed:
        subprocess.run(["codesign", "--timestamp", "--sign", os.environ["AWEAR_MAC_SIGN_IDENTITY"], str(disk)], check=True)
        subprocess.run(["codesign", "--verify", "--strict", str(disk)], check=True)
    if args.release:
        notarize_mac_artifact(disk)
        subprocess.run(["xcrun", "stapler", "staple", str(disk)], check=True)
        subprocess.run(["xcrun", "stapler", "validate", str(disk)], check=True)
    (disk.with_suffix(".dmg.sha256")).write_text(sha256(disk) + "  " + disk.name + "\n")
    print(disk)
    return disk



def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("platform", choices=("mac", "windows", "all"))
    signing = parser.add_mutually_exclusive_group()
    signing.add_argument("--release", action="store_true", help="Require signing, and notarize the Mac installer")
    signing.add_argument("--sign-only", action="store_true", help="Sign the Mac app and installer; leave notarization pending")
    parser.add_argument("--skip-app-build", action="store_true", help="Use an already-built Mac app")
    args = parser.parse_args()
    if args.sign_only and args.platform != "mac":
        parser.error("--sign-only applies to the Mac installer; choose platform mac")
    outputs = []
    if args.platform in {"windows", "all"}:
        outputs.append(build_windows(args))
    if args.platform in {"mac", "all"}:
        outputs.append(build_mac(args))
    for output in outputs:
        (output.with_suffix(output.suffix + ".sha256")).write_text(sha256(output) + "  " + output.name + "\n")
        print(output)
    print("SIGNED RELEASE" if args.release else "SIGNED — Apple notarization pending" if args.sign_only else "DEVELOPMENT BUILD — signing/native validation may still be required")


if __name__ == "__main__":
    main()
