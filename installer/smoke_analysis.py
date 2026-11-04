"""Native numerical acceptance on a new synthetic profile; no real key or Bluetooth."""
from __future__ import annotations
from contextlib import closing
import hashlib
import io
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile


def exercise(command):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
    import app
    binary=Path(command[0]).resolve()
    payload=binary.parent.parent if len(command)>1 else binary.parent.parent/'Resources/analysis-runtime'
    def installed_files():
        return {p.relative_to(payload).as_posix():(p.stat().st_size,p.stat().st_mtime_ns) for p in payload.rglob('*') if p.is_file() and not p.is_symlink()}
    installed_before=installed_files()
    with tempfile.TemporaryDirectory(prefix='mindtrail-native-analysis-') as folder:
        profile=Path(folder).resolve();app.configure_storage(profile);app.setup_db()
        participant='SYNTHETIC-RUNTIME';base=1_767_268_800_000
        def record(stamp,i,day):
            frequency=10+(i//60)%3;amplitude=1+day*.1+((i//60)%4)*.05
            samples=[amplitude*math.sin(2*math.pi*frequency*j/256) for j in range(256)]
            return (participant,stamp,'SYNTHETIC-DEVICE',json.dumps(samples),256,0,1,2)
        with app.db() as c:
            app.put_setting(c,'participant_id',participant)
            # Offline archive mode passes normal setup guards without any key or scan.
            app.put_setting(c,'desktop_setup','1');app.put_setting(c,'desktop_mode','offline')
            c.executemany('INSERT INTO readings VALUES(?,?,?,?,?,?,?,?)',[record(base+day*86_400_000+i*1000,i,day) for day in range(3) for i in range(720)])
        original=hashlib.sha256(app.DB_PATH.read_bytes()).hexdigest()
        env=dict(os.environ);env.pop('AWEAR_API_KEY',None);env['AWEAR_DATA_DIR']=str(profile)
        process=subprocess.Popen([*command,'--data-dir',str(profile),'--no-browser','--no-key-store'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor());url=None
        try:
            deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                if process.poll() is not None:raise RuntimeError('Native analysis app exited before startup')
                try:
                    with (profile/'desktop.lock').open('rb') as f:f.seek(1);launch=json.load(f)['url']
                    opener.open(launch,timeout=2).close();url=launch.split('/launch')[0];break
                except (OSError,ValueError,KeyError):time.sleep(.1)
            if not url:raise RuntimeError('Native analysis app did not start')
            def get(path):
                with opener.open(url+path,timeout=30) as r:return r.read()
            def post(path,body):
                request=urllib.request.Request(url+path,data=json.dumps(body).encode(),headers={'Content-Type':'application/json','X-Awear-Local':'1'})
                with opener.open(request,timeout=10) as r:return json.load(r)
            def state():return json.loads(get('/api/analysis/state'))
            options={'timezone':'America/New_York','exploration':True,'activityComparisons':True,'candidateBoundaries':{'minimumSeconds':12,'sensitivity':'medium'}}
            def analyze():
                start=time.monotonic();post('/api/analysis/start',options);deadline=start+90
                while time.monotonic()<deadline:
                    current=state()
                    if current['job']['state'] not in ('running','canceling'):
                        assert current['job']['state']=='completed',current
                        return current,time.monotonic()-start
                    time.sleep(.1)
                raise RuntimeError('Native analysis acceptance timed out')
            assert state()['available'] and state()['explorationAvailable']
            first,cold=analyze();a=get('/api/analysis/report');value=json.loads(a)
            assert value['coverage']['sourceRecords']==2160 and len(value['windows'])==540
            assert all(w['acquisition']['placement']=='right' and w['acquisition']['sample_rate']==256 for w in value['windows'])
            assert abs(value['windows'][0]['features']['acRms']-1/math.sqrt(2))<1e-12
            assert value['implementation']['numericalRuntime']['nativeLibraries']
            second,warm=analyze();assert get('/api/analysis/report')==a
            assert second['job']['reusedWindows']==540
            report=json.loads(get('/api/discovery/report'));assert report['status']=='current'
            assert report['numerical']['analysisContext']['resourcePolicy']['nativeThreads']==1
            query=urllib.parse.urlencode({'identity':report['snapshotIdentity']})
            with zipfile.ZipFile(io.BytesIO(get('/api/export/analysis-bundle.zip?'+query))) as z:
                manifest=json.loads(z.read('manifest.json'));assert manifest['windowRows']==540
                for member in manifest['members']:assert hashlib.sha256(z.read(member['path'])).hexdigest()==member['sha256']
            monitoring=json.loads(get('/api/monitoring'));assert monitoring['history']['total']==1
            selected=monitoring['selected']['version'];old=get('/api/export/monitoring-version.json?version='+selected)
            assert hashlib.sha256(app.DB_PATH.read_bytes()).hexdigest()==original
            with app.db() as c:c.executemany('INSERT INTO readings VALUES(?,?,?,?,?,?,?,?)',[record(base+2*86_400_000+(720+i)*1000,720+i,2) for i in range(4)])
            assert state()['stale']
            post('/api/analysis/start',options);post('/api/analysis/cancel',{})
            deadline=time.monotonic()+10
            while state()['job']['state'] in ('running','canceling') and time.monotonic()<deadline:time.sleep(.05)
            assert state()['job']['state']=='canceled'
            assert get('/api/export/monitoring-version.json?version='+selected)==old
            third,arrival=analyze();assert third['summary']['coverage']['computedWindows']==541
            assert json.loads(get('/api/monitoring'))['history']['total']==2
            with zipfile.ZipFile(io.BytesIO(get('/api/export/monitoring-history.zip'))) as z:assert json.loads(z.read('manifest.json'))['versions']==2
            backup=get('/api/desktop/backup')
            with tempfile.TemporaryDirectory(prefix='mindtrail-restored-native-') as restored_folder:
                restored=Path(restored_folder)
                child=subprocess.Popen([*command,'--data-dir',str(restored),'--no-browser','--no-key-store'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor());restore_url=None
                try:
                    deadline=time.monotonic()+30
                    while time.monotonic()<deadline:
                        if child.poll() is not None:raise RuntimeError('Restored app exited before startup')
                        try:
                            with (restored/'desktop.lock').open('rb') as f:f.seek(1);launch=json.load(f)['url']
                            client.open(launch,timeout=2).close();restore_url=launch.split('/launch')[0];break
                        except (OSError,ValueError,KeyError):time.sleep(.1)
                    assert restore_url
                    def restored_get(path):
                        with client.open(restore_url+path,timeout=30) as response:return response.read()
                    def restored_post(path,data,raw=False):
                        request=urllib.request.Request(restore_url+path,data=data if raw else json.dumps(data).encode(),headers={'Content-Type':'application/octet-stream' if raw else 'application/json','X-Awear-Local':'1'})
                        with client.open(request,timeout=30) as response:return json.load(response)
                    restored_post('/api/desktop/restore',backup,True)
                    assert not json.loads(restored_get('/api/desktop/state'))['ready']
                    restored_post('/api/desktop/offline',{})
                    assert json.loads(restored_get('/api/desktop/state'))['mode']=='offline'
                    assert json.loads(restored_get('/api/monitoring'))['history']['total']==2
                    assert restored_get('/api/export/monitoring-version.json?version='+selected)==old
                    restored_post('/api/analysis/start',options);deadline=time.monotonic()+90
                    while time.monotonic()<deadline:
                        recovered=json.loads(restored_get('/api/analysis/state'))
                        if recovered['job']['state'] not in ('running','canceling'):break
                        time.sleep(.1)
                    assert recovered['job']['state']=='completed',recovered
                    assert recovered['summary']['coverage']['computedWindows']==541
                    assert not (restored/'.env').exists()
                finally:
                    if restore_url:
                        try:restored_post('/api/desktop/quit',{})
                        except OSError:pass
                    try:child.wait(timeout=10)
                    except subprocess.TimeoutExpired:child.kill();child.wait()
            result={'nativeNumericalAnalysis':True,'completeBackupRestoreOfflineAndReanalysis':True,'optionalExplorationAndComparisons':True,'knownRmsOracle':True,'coldCachedBytesEqual':True,
                'sourcePreserved':True,'staleArrivalAndCancelPreservedVersion':True,'auditAndHistoryExports':True,
                'coldSeconds':cold,'cachedSeconds':warm,'arrivalSeconds':arrival,'computedWindows':540,'reusedWindows':540,
                'peakMemoryBytes':max(s['job']['peakMemoryBytes'] for s in (first,second,third))}
            assert installed_files()==installed_before,'Analysis must not modify installed runtime files'
            result['installedRuntimePreserved']=True
            assert result['peakMemoryBytes']<=1_610_612_736
            return result
        finally:
            if url:
                try:
                    request=urllib.request.Request(url+'/api/desktop/quit',data=b'{}',headers={'Content-Type':'application/json','X-Awear-Local':'1'})
                    opener.open(request,timeout=3).close()
                except OSError:pass
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait()


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--executable',required=True);parser.add_argument('--entry');parser.add_argument('--report',type=Path,required=True);args=parser.parse_args()
    value=exercise([args.executable,*([args.entry] if args.entry else [])]);args.report.write_text(json.dumps(value,indent=2),encoding='utf-8');print(json.dumps(value))
