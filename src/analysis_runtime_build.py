"""Publisher-only staging of pinned isolated numerical runtimes; never ships user data."""
from __future__ import annotations
import email
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

from installer_build import ROOT,APP_FILES,fetch,unpack,sha256,validate_wheels

MACHO={b'\xfe\xed\xfa\xce',b'\xce\xfa\xed\xfe',b'\xfe\xed\xfa\xcf',b'\xcf\xfa\xed\xfe',b'\xca\xfe\xba\xbe',b'\xbe\xba\xfe\xca'}


def lock():return json.loads((ROOT/'installer/analysis-runtime.lock.json').read_text(encoding='utf-8'))


def wheels(target,cache):
    records=lock()['targets'][target]
    files=[fetch(r['url'],cache/r['filename'],r['sha256']) for r in records]
    if target=='windows-x64':validate_wheels(files,'3.14')
    else:
        from packaging.tags import cpython_tags,compatible_tags,mac_platforms
        from packaging.markers import default_environment
        from packaging.requirements import Requirement
        from packaging.utils import canonicalize_name,parse_wheel_filename
        from packaging.version import Version
        arch='arm64' if target=='mac-arm64' else 'x86_64'
        platforms=list(mac_platforms((12,0),arch));supported=set(cpython_tags((3,14),abis=['cp314'],platforms=platforms))|set(compatible_tags((3,14),interpreter='cp314',platforms=platforms))
        installed={};metadata=[]
        for p in files:
            if not supported.intersection(parse_wheel_filename(p.name)[3]):raise ValueError('Incompatible Mac wheel: '+p.name)
            with zipfile.ZipFile(p) as z:m=email.message_from_bytes(z.read(next(n for n in z.namelist() if n.endswith('.dist-info/METADATA'))))
            installed[canonicalize_name(m['Name'])]=Version(m['Version']);metadata.append(m)
        env={**default_environment(),'sys_platform':'darwin','platform_system':'Darwin','os_name':'posix','platform_machine':arch,'python_version':'3.14','python_full_version':'3.14.10','extra':''}
        for m in metadata:
            for raw in m.get_all('Requires-Dist',[]):
                requirement=Requirement(raw)
                if requirement.marker and not requirement.marker.evaluate(env):continue
                v=installed.get(canonicalize_name(requirement.name))
                if v is None or v not in requirement.specifier:raise ValueError('Missing/incompatible Mac dependency: '+raw)
    return files


def sources(payload):
    (payload/'app').mkdir(parents=True,exist_ok=True)
    for name in APP_FILES:shutil.copy2(ROOT/'src'/name,payload/'app'/name)
    (payload/'config').mkdir(exist_ok=True)
    for name in ('exploration.json','exploration-stability.json'):shutil.copy2(ROOT/'config'/name,payload/'config'/name)


def write_manifest(payload,architectures,dependencies,python_input):
    binaries=[]
    for p in sorted(payload.rglob('*')):
        if not p.is_file() or p.is_symlink():continue
        if p.suffix in ('.dll','.pyd','.so','.dylib','.exe') or p.name in ('Python','python3.14'):
            binaries.append(p.relative_to(payload).as_posix())
    value={'formatVersion':1,'python':[3,14,10],'architectures':architectures,
        'packages':{r['name']:r['version'] for r in dependencies},'dependencies':dependencies,'pythonInput':python_input,
        'binaries':binaries,'sourceHashes':{p.relative_to(payload).as_posix():sha256(p) for folder in ('app','config') for p in sorted((payload/folder).iterdir()) if p.is_file() and p.suffix in ('.py','.json')}}
    value['identity']=hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    (payload/'analysis-runtime.json').write_text(json.dumps(value,sort_keys=True),encoding='utf-8')
    return value


def windows_manifest(payload,desktop_lock):
    sources(payload)
    return write_manifest(payload,['amd64','x86_64'],desktop_lock['wheels'],desktop_lock['python'])


def stage_mac(payload):
    metadata=lock()
    base=Path(os.environ.get('MINDTRAIL_MAC_PYTHON_ROOT',ROOT/'build/mac-python-expanded/Python_Framework.pkg/Payload/Versions/3.14'))
    if not (base/'bin/python3.14').is_file():raise ValueError('Prepare the universal publisher runtime with installer/prepare_mac_runtime.py first')
    version=subprocess.run([str(base/'bin/python3.14'),'-I','-S','-c','import platform;print(platform.python_version())'],check=True,capture_output=True,text=True).stdout.strip()
    if version!=metadata['pythonVersion']:raise ValueError('Mac numerical Python version differs from the lock')
    for binary in [base/'bin/python3.14',base/'Python',*(base/'lib/python3.14/lib-dynload').glob('*.so')]:
        archs=subprocess.run(['lipo','-archs',str(binary)],check=True,capture_output=True,text=True).stdout.split()
        if not {'arm64','x86_64'}<=set(archs):raise ValueError('Mac worker Python must support both architectures')
    if payload.exists():shutil.rmtree(payload)
    payload.mkdir(parents=True)
    shutil.copytree(base,payload/'runtime',symlinks=True,ignore=shutil.ignore_patterns('__pycache__','site-packages','test','tests','idlelib','tkinter','ensurepip'))
    # This worker has no Tcl/Tk UI. Omit those optional framework bundles and extension.
    for name in ('Tcl.framework','Tk.framework'):
        p=payload/'runtime/Frameworks'/name
        if p.exists():shutil.rmtree(p)
    for p in (payload/'runtime/lib/python3.14/lib-dynload').glob('_tkinter*'):p.unlink()
    sources(payload);all_dependencies=[]
    for arch in ('arm64','x86_64'):
        target='mac-'+arch;vendor=payload/'vendor'/arch;vendor.mkdir(parents=True)
        for file in wheels(target,ROOT/'build/installer-downloads'):unpack(file,vendor)
        for p in vendor.rglob('*'):
            if p.is_file() and p.suffix in ('.so','.dylib'):
                archs=subprocess.run(['lipo','-archs',str(p)],check=True,capture_output=True,text=True).stdout.split()
                if arch not in archs:raise ValueError('Mac numerical wheel CPU differs from target')
        all_dependencies+=metadata['targets'][target]
    value=write_manifest(payload,['arm64','x86_64'],all_dependencies,metadata['macPython'])
    # Run native numerical code before copying into the app; no network/profile/API/keychain.
    from analysis_runtime import environment
    env=environment();env['AWEAR_BUNDLED_RUNTIME']='1'
    subprocess.run([str(payload/'runtime/bin/python3.14'),'-I','-S','-B',str(payload/'app/analysis_bootstrap.py'),'--probe',str(payload/'probe.json')],env=env,check=True,timeout=30)
    shutil.move(str(payload/'probe.json'),ROOT/'build/mac-analysis-probe.json')
    return value


def sign_mac_tree(application):
    identity=os.environ.get('AWEAR_MAC_SIGN_IDENTITY') or '-'
    flags=['--options','runtime','--timestamp'] if identity!='-' else []
    runtime=application/'Contents/Resources/analysis-runtime'
    framework=runtime/'runtime'/'Python'
    binaries=[]
    for p in sorted(runtime.rglob('*')):
        if p.is_file() and not p.is_symlink():
            with p.open('rb') as f:magic=f.read(4)
            if magic in MACHO:
                binaries.append(p)
                if p!=framework:subprocess.run(['codesign','--force',*flags,'--sign',identity,str(p)],check=True)
    for p in sorted(runtime.rglob('*.app'),key=lambda p:len(p.parts),reverse=True):
        subprocess.run(['codesign','--force',*flags,'--sign',identity,str(p)],check=True)
    # This relocated framework executable seals the entire runtime directory.
    # Signing it before its children leaves a stale resource seal. The outer
    # app treats Resources as data, so --deep alone does not detect that defect.
    subprocess.run(['codesign','--force',*flags,'--sign',identity,str(framework)],check=True)
    for p in binaries:
        subprocess.run(['codesign','--verify','--strict','--all-architectures',str(p)],check=True)
    subprocess.run(['codesign','--force',*flags,'--sign',identity,str(application)],check=True)
    subprocess.run(['codesign','--verify','--deep','--strict',str(application)],check=True)
