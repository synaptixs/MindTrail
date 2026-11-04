"""Isolated entry point: explicit source/vendor paths, no profile setup or credentials."""
from __future__ import annotations
import json
import os
from pathlib import Path
import platform
import sys


def main():
    sys.dont_write_bytecode=True
    source=Path(__file__).resolve().parent
    if os.environ.get('AWEAR_BUNDLED_RUNTIME')=='1':
        vendor=source.parent/'vendor'
        native=vendor/platform.machine().lower()
        sys.path.insert(0,str(native if native.is_dir() else vendor))
    sys.path.insert(0,str(source))
    # Also cap threads for direct developer probes, before any numerical imports.
    import analysis_runtime as runtime
    os.environ.update({name:'1' for name in runtime.THREAD_ENV})
    os.environ.pop('AWEAR_API_KEY',None)
    if runtime.bundled():
        from importlib.metadata import version
        expected=runtime.versions()
        if list(sys.version_info[:3])!=expected['python'] or any(version(name)!=expected['packages'][name] for name in runtime.PACKAGES):
            raise ValueError('Bundled numerical dependencies differ from the pinned runtime')
    if len(sys.argv)==3 and sys.argv[1]=='--probe':
        import numpy as np
        from sklearn.cluster import KMeans
        from analysis_quality import power_spectrum
        x=np.arange(1024)/256
        spectrum=power_spectrum(np.sin(2*np.pi*10*x),256)
        assert np.isfinite(spectrum['density']).all()
        assert spectrum['frequencies'][np.argmax(spectrum['density'])]==10
        assert KMeans(n_clusters=2,n_init=1,random_state=0).fit_predict([[0],[1],[9],[10]]).shape==(4,)
        result={'runtime':runtime.versions(),'machine':platform.machine().lower(),'policy':runtime.policy(),'numericalProbe':True}
        Path(sys.argv[2]).write_text(json.dumps(result,sort_keys=True),encoding='utf-8')
        return 0
    if len(sys.argv)!=2:raise ValueError('Choose an analysis job directory')
    from analysis_worker import run
    return run(Path(sys.argv[1]))


if __name__=='__main__':raise SystemExit(main())
