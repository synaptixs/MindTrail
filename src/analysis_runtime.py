"""Stdlib-only worker selection and bounded runtime policy for source and installers."""
from __future__ import annotations
import hashlib
import importlib.util
from importlib.metadata import PackageNotFoundError, version, distribution
import json
import os
from pathlib import Path
import platform
import sys

MAX_SECONDS=240
MAX_PEAK_BYTES=1_610_612_736
PACKAGES=('numpy','scipy','scikit-learn','joblib','threadpoolctl','narwhals','cloudpickle')
THREAD_ENV=('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS','BLIS_NUM_THREADS')


def bundled():return bool(getattr(sys,'frozen',False) or os.environ.get('AWEAR_BUNDLED_RUNTIME')=='1')


def root():
    if getattr(sys,'frozen',False):return Path(sys.executable).parent.parent/'Resources'/'analysis-runtime'
    return Path(__file__).resolve().parent.parent


def manifest():
    if not bundled():return None
    path=root()/'analysis-runtime.json'
    if path.is_symlink() or not path.is_file() or path.stat().st_size>2_000_000:raise ValueError('Bundled analysis manifest unavailable')
    value=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value,dict) or not isinstance(value.get('packages'),dict) or not isinstance(value.get('architectures'),list) or not isinstance(value.get('binaries'),list):raise ValueError('Invalid bundled analysis manifest')
    identity=value.get('identity');facts={k:v for k,v in value.items() if k!='identity'}
    if value.get('formatVersion')!=1 or hashlib.sha256(json.dumps(facts,sort_keys=True,separators=(',',':')).encode()).hexdigest()!=identity:raise ValueError('Invalid bundled analysis manifest')
    if platform.machine().lower() not in value['architectures']:raise ValueError('Unsupported numerical runtime architecture')
    if any(not value['packages'].get(n) for n in PACKAGES):raise ValueError('Incomplete numerical runtime')
    for name in value['binaries']:
        path=Path(name)
        if path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name:raise ValueError('Invalid runtime member')
        p=root()/path
        if p.is_symlink() or not p.is_file():raise ValueError('Numerical runtime binary unavailable')
    return value


def source_root():return root()/'app' if bundled() else Path(__file__).resolve().parent


def executable():
    if not bundled():return Path(sys.executable)
    return root()/('runtime/python.exe' if sys.platform=='win32' else 'runtime/bin/python3.14')


def available(exploration=False):
    try:
        if bundled():
            manifest()
            return executable().is_file() and (source_root()/'analysis_bootstrap.py').is_file()
        return (source_root()/'analysis_bootstrap.py').is_file() and importlib.util.find_spec('numpy') is not None and (not exploration or importlib.util.find_spec('sklearn') is not None)
    except (OSError,ValueError,KeyError,TypeError):return False


def versions():
    if bundled():
        try:
            value=manifest()
            return {'python':value['python'],'packages':value['packages']}
        except (OSError,ValueError,KeyError,TypeError):return {'python':None,'packages':{}}
    result={}
    for name in PACKAGES:
        try:result[name]=version(name)
        except PackageNotFoundError:result[name]=None
    return {'python':list(sys.version_info[:3]),'packages':result}


def stamp():
    """Pinned payload plus cheap native-binary changes; source bytes are checked by manager."""
    if not bundled():return None
    try:
        value=manifest();stats=[]
        for name in value['binaries']:
            s=(root()/name).stat();stats.append((name,s.st_size,s.st_mtime_ns,s.st_ctime_ns,s.st_ino))
        return [value['identity'],stats]
    except (OSError,ValueError,KeyError,TypeError):return 'unavailable'


def command(directory):
    if not available():raise ValueError('Analysis is unavailable in this build')
    return [str(executable()),'-I',*(['-S'] if bundled() else []),'-B',str(source_root()/'analysis_bootstrap.py'),str(directory)]


def environment():
    env=dict(os.environ);env.pop('AWEAR_API_KEY',None)
    # -I also ignores Python injection variables; remove them for child-process hygiene.
    for key in ('PYTHONPATH','PYTHONHOME'):env.pop(key,None)
    env.update({name:'1' for name in THREAD_ENV})
    if bundled():env['AWEAR_BUNDLED_RUNTIME']='1'
    return env


def policy():return {'wallSeconds':MAX_SECONDS,'peakBytes':MAX_PEAK_BYTES,'nativeThreads':1}


def numerical_identity():
    """Worker-only owning-distribution membership, including linked BLAS wheel libraries."""
    dist=distribution('numpy')
    native={}
    for member in sorted(dist.files or [],key=str):
        if str(member).endswith(('.so','.dylib','.dll','.pyd')):
            native[str(member)]=hashlib.sha256(dist.locate_file(member).read_bytes()).hexdigest()
    if not native:raise ValueError('NumPy native implementation files are unavailable')
    return {'machine':platform.machine().lower(),'nativeLibraries':native,
        'threadEnvironment':{name:os.environ.get(name) for name in THREAD_ENV}}
