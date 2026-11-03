# Build on each target OS. Only application assets are allowlisted as data.
import sys
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = Path(SPECPATH)
static = [(str(path), 'static') for path in sorted((root / 'src' / 'static').iterdir())
          if path.is_file() and path.suffix in {'.html', '.css', '.js'}]
hidden = ['tzdata', 'zoneinfo']
if sys.platform == 'darwin':
    hidden += collect_submodules('bleak.backends.corebluetooth')
    hidden += ['keyring.backends.macOS']
elif sys.platform == 'win32':
    hidden += collect_submodules('bleak.backends.winrt')
    hidden += collect_submodules('winrt')
    hidden += ['keyring.backends.Windows']

a = Analysis([str(root / 'src' / 'desktop.py')], pathex=[str(root / "src")],
             binaries=[], datas=static + collect_data_files('tzdata') + collect_data_files('certifi'), hiddenimports=hidden,
             hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=['numpy', 'sklearn', 'matplotlib', 'pytest', 'IPython', 'notebook', 'tkinter'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='MindTrail Lab',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False, argv_emulation=False,
          icon=str(root / 'build' / 'icons' / ('awear.icns' if sys.platform == 'darwin' else 'awear.ico')),
          codesign_identity=os.environ.get('AWEAR_MAC_SIGN_IDENTITY'),
          target_arch=os.environ.get('AWEAR_MAC_ARCH'))
collection = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='MindTrail Lab')
if sys.platform == 'darwin':
    application = BUNDLE(collection, name='MindTrail Lab.app',
        bundle_identifier='org.awearpersonallab.desktop', version='0.2.5',
        icon=str(root / 'build' / 'icons' / 'awear.icns'),
        info_plist={'CFBundleShortVersionString': '0.2.5', 'LSUIElement': True,
            'NSBluetoothAlwaysUsageDescription': 'Record EEG from the AWEAR device you select.',
            'NSBluetoothPeripheralUsageDescription': 'Record EEG from the AWEAR device you select.',
            'NSHighResolutionCapable': True, 'LSMinimumSystemVersion':'10.15'})
