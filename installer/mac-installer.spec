import os
from pathlib import Path
root = Path(SPECPATH).parent
identity = os.environ.get('AWEAR_MAC_SIGN_IDENTITY')
a = Analysis([str(root / 'installer' / 'mac_install.py')], pathex=[str(root)],
    binaries=[], datas=[(str(root / 'build' / 'installer-payload' / 'awear-app.zip'), 'payload')],
    hiddenimports=['AppKit'], excludes=['tkinter','numpy','sklearn','matplotlib','pytest','IPython'],
    hookspath=[], hooksconfig={}, runtime_hooks=[], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Install MindTrail Lab', console=False,
    debug=False, strip=False, upx=False, argv_emulation=False, codesign_identity=identity,
    target_arch=os.environ.get('AWEAR_MAC_ARCH'),
    icon=str(root / 'build' / 'icons' / 'awear.icns'))
collection = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='Install MindTrail Lab')
application = BUNDLE(collection, name='Install MindTrail Lab.app', version='0.2.7',
    icon=str(root / 'build' / 'icons' / 'awear.icns'), bundle_identifier='org.awearpersonallab.install',
    info_plist={'CFBundleShortVersionString':'0.2.7','LSUIElement':True,'NSHighResolutionCapable':True,'LSMinimumSystemVersion':'10.15'})
