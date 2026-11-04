"""Packaged selection, real budget failures, input identities and compatibility checks."""
from contextlib import closing
import hashlib
import importlib.util
import json
import os
from pathlib import Path,PurePosixPath
import platform
import sqlite3
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import analysis_runtime as runtime
from analysis_provenance import fingerprint
from test import test_recurrence as fixtures


class RuntimeTests(unittest.TestCase):
    def test_packaged_selection_scope_manifest_corruption_binary_stamp_and_sources(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'app';source.mkdir();(source/'analysis_bootstrap.py').write_text('fixture')
            binary=root/('runtime/python.exe' if sys.platform=='win32' else 'runtime/bin/python3.14');binary.parent.mkdir(parents=True);binary.write_bytes(b'fixture')
            relative=binary.relative_to(root).as_posix()
            value={'formatVersion':1,'python':[3,14,10],'architectures':[platform.machine().lower()],
                'packages':{name:'test' for name in runtime.PACKAGES},'binaries':[relative]}
            def save(v):
                v={k:x for k,x in v.items() if k!='identity'};v['identity']=hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':')).encode()).hexdigest();(root/'analysis-runtime.json').write_text(json.dumps(v),encoding='utf-8')
            with patch.object(runtime,'root',return_value=root),patch.object(runtime,'bundled',return_value=True):
                self.assertFalse(runtime.available());save(value);self.assertTrue(runtime.available(True));self.assertEqual(runtime.source_root(),source)
                self.assertEqual(runtime.command(root/'job')[:3],[str(binary),'-I','-S'])
                self.assertIn('-B',runtime.command(root/'job'))
                a=runtime.stamp();binary.write_bytes(b'changed');self.assertNotEqual(runtime.stamp(),a)
                save(dict(value,architectures=['foreign']));self.assertFalse(runtime.available())
                save(dict(value,binaries=['../outside']));self.assertFalse(runtime.available())
                save(dict(value,packages={}));self.assertFalse(runtime.available())
                (root/'analysis-runtime.json').write_text('[]');self.assertFalse(runtime.available())
                save(value);binary.unlink();self.assertFalse(runtime.available())
    def test_environment_isolation_and_stdlib_reader(self):
        with patch.dict(os.environ,{'AWEAR_API_KEY':'synthetic-only','PYTHONPATH':'/untrusted','PYTHONHOME':'/untrusted','OMP_NUM_THREADS':'88'}):
            env=runtime.environment();self.assertNotIn('AWEAR_API_KEY',env);self.assertNotIn('PYTHONPATH',env);self.assertNotIn('PYTHONHOME',env)
            self.assertTrue(all(env[name]=='1' for name in runtime.THREAD_ENV))
        code="import analysis_runtime,analysis_manager,sys; assert 'numpy' not in sys.modules and 'sklearn' not in sys.modules"
        r=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).parents[1]/'src',capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr)
    def test_owning_native_distribution_content_invalidates_identity_even_preserved_mtime(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'native.so';p.write_bytes(b'one');stat=p.stat()
            distribution=SimpleNamespace(files=[PurePosixPath('numpy/native.so')],locate_file=lambda _:p)
            with patch.object(runtime,'distribution',return_value=distribution):
                a=runtime.numerical_identity();self.assertEqual(a,runtime.numerical_identity())
                p.write_bytes(b'two');os.utime(p,ns=(stat.st_atime_ns,stat.st_mtime_ns));self.assertNotEqual(runtime.numerical_identity(),a)
    def test_mac_framework_seal_is_created_after_all_nested_code_and_verified(self):
        from analysis_runtime_build import sign_mac_tree
        with tempfile.TemporaryDirectory() as folder:
            application=Path(folder)/'Fixture.app';root=application/'Contents/Resources/analysis-runtime'
            framework=root/'runtime/Python'
            children=[root/'runtime/bin/python3.14',root/'runtime/lib/fixture.so',root/'runtime/Resources/Python.app/Contents/MacOS/Python',root/'vendor/arm64/fixture.so']
            for p in [framework,*children]:p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'\xcf\xfa\xed\xfe-fixture')
            seals={};verified=set();commands=[]
            def run(command,**kwargs):
                commands.append(command);target=Path(command[-1])
                if '--sign' in command:
                    self.assertIn('--timestamp',command);self.assertIn('runtime',command)
                    if target.is_file():target.write_bytes(target.read_bytes()+b'-signed')
                    elif target.name=='Python.app':
                        executable=target/'Contents/MacOS/Python';executable.write_bytes(executable.read_bytes()+b'-bundle')
                    if target==framework:seals['children']={p:p.read_bytes() for p in children}
                elif target==framework:
                    self.assertEqual(seals['children'],{p:p.read_bytes() for p in children})
                    verified.add(target)
                elif target.is_file():verified.add(target)
                return SimpleNamespace(returncode=0)
            with patch.dict(os.environ,{'AWEAR_MAC_SIGN_IDENTITY':'Synthetic Publisher'}),patch('analysis_runtime_build.subprocess.run',side_effect=run):sign_mac_tree(application)
            self.assertEqual(verified,set([framework,*children]))
            self.assertEqual(Path(commands[-1][-1]),application);self.assertIn('--verify',commands[-1])

    def test_locked_dependency_closure_and_explicit_packaging_members(self):
        from installer_build import APP_FILES,ROOT
        from analysis_runtime_build import lock
        data=lock();self.assertEqual(data['macMinimum'],'12.0')
        for target,rows in data['targets'].items():
            names={r['name'] for r in rows};self.assertTrue(set(runtime.PACKAGES)<=names)
            self.assertTrue(all(len(r['sha256'])==64 and r['url'].startswith('https://files.pythonhosted.org/') for r in rows))
        for name in ('analysis_runtime.py','analysis_bootstrap.py','analysis_worker.py','analysis_quality.py','exploratory_models.py','spectral_features.py'):
            self.assertIn(name,APP_FILES);self.assertTrue((ROOT/'src'/name).is_file())
        self.assertEqual(len(APP_FILES),len(set(APP_FILES)))


@unittest.skipUnless(importlib.util.find_spec('numpy'),'Source numerical runtime')
class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.helper=fixtures.WorkflowTests('test_real_pipeline_http_sealed_source_and_lifecycle');self.helper.setUp();self.addCleanup(self.helper.doCleanups);self.manager=self.helper.manager
    def test_supervisor_wall_budget_preserves_pointer_and_no_orphan_lease(self):
        self.helper.analyze();pointer=self.manager.root/(self.manager.scope('P')+'.json');before=pointer.read_bytes()
        with patch.object(runtime,'MAX_SECONDS',0):self.helper.analyze(failed=True)
        self.assertEqual(pointer.read_bytes(),before);self.assertIn('resource budget',self.manager.state('P')['job']['error'])
        self.assertFalse((self.manager.root/'active.json').exists());self.assertIsNone(self.manager.process)
    def test_worker_memory_failure_is_explicit_before_publication(self):
        import analysis_worker
        directory=self.helper.root/'budget-job';directory.mkdir();(directory/'heartbeat').touch();(directory/'request.json').write_text('{}')
        with patch('analysis_resources.peak_memory_bytes',return_value=runtime.MAX_PEAK_BYTES+1):
            self.assertEqual(analysis_worker.run(directory),3)
        status=json.loads((directory/'status.json').read_text(encoding='utf-8'));self.assertEqual(status['state'],'failed');self.assertIn('memory budget',status['error']);self.assertFalse((directory/'report.json').exists())
    def test_source_quality_cli_remains_compatible_and_records_native_implementation(self):
        output=self.helper.root/'cli.json';root=Path(__file__).parents[1]
        r=subprocess.run([sys.executable,str(root/'src/analysis_quality.py'),str(self.manager.database),'--participant','P','--output',str(output)],env=runtime.environment(),capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stderr);value=json.loads(output.read_text(encoding='utf-8'));self.assertEqual(len(value['windows']),9)
        self.assertTrue(value['implementation']['numericalRuntime']['nativeLibraries'])
