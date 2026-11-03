"""Standard-library lifecycle for isolated quality jobs and last-complete results.

Numerical modules are imported only by the separate source-workspace worker.
"""
from __future__ import annotations
import hashlib
import importlib.util
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from archive_paths import ArchivePaths


JOB_ID=re.compile(r'^[0-9a-f]{32}$')
SOURCE_FILES=('analysis_quality.py','analysis_provenance.py','analysis_worker.py','analysis_view.py','analysis_cache.py','analysis_resources.py','daily_review.py')


def write_json(path: Path, value: dict) -> None:
    temporary=path.with_suffix('.tmp')
    with temporary.open('w',encoding='utf-8') as output:
        os.chmod(temporary,0o600)
        json.dump(value,output,allow_nan=False,separators=(',',':'))
    temporary.replace(path)


def read_json(path: Path, limit=2_000_000) -> dict:
    if path.is_symlink() or path.stat().st_size>limit:raise ValueError('Invalid analysis metadata')
    result=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(result,dict):raise ValueError('Invalid analysis metadata')
    return result


def remove_quality_payloads(directory):
    for path in directory.glob('quality-*'):
        if re.fullmatch(r'quality-(?:index|\d{4}-\d{2}-\d{2})\.(?:json|tmp)',path.name) and not path.is_symlink():path.unlink()


class AnalysisManager:
    def __init__(self, profile: Path, database: Path, *, demo=False):
        self.root=ArchivePaths(Path(profile).resolve()).quality
        self.database=Path(database).resolve()
        self.demo=bool(demo)
        self.lock=threading.RLock()
        self.process=None
        self.job=None
        self.closed=False
        self.monitor=None

    def available(self) -> bool:
        return not (getattr(sys,'frozen',False) or os.environ.get('AWEAR_BUNDLED_RUNTIME')=='1') and (Path(__file__).parent/'analysis_worker.py').is_file() and importlib.util.find_spec('numpy') is not None

    def running(self) -> bool:
        with self.lock:
            self._recover()
            return self.process is not None or bool(self.job and self.job.get("external") and self.job["state"]=="running")

    def signature(self) -> str:
        try:numpy_version=version('numpy')
        except PackageNotFoundError:numpy_version=None
        parts=[('python',list(sys.version_info[:3])),('numpy',numpy_version)]
        for path in (self.database,Path(str(self.database)+'-wal')):
            try:
                s=path.stat()
                # Reader-only WAL connections can create/remove an empty file. It holds no frames.
                if path.name.endswith('-wal') and s.st_size==0:parts.append((str(path),None))
                else:parts.append((str(path),s.st_size,s.st_mtime_ns,s.st_ctime_ns,s.st_ino))
            except FileNotFoundError:parts.append((str(path),None))
        for name in SOURCE_FILES:
            path=Path(__file__).parent/name
            parts.append((name,hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None))
        return hashlib.sha256(json.dumps(parts).encode()).hexdigest()

    def scope(self,participant):
        return hashlib.sha256((str(self.database)+'\0'+participant).encode()).hexdigest()

    def directory(self,job_id):
        if not isinstance(job_id,str) or not JOB_ID.fullmatch(job_id):raise ValueError('Invalid analysis job')
        path=self.root/'jobs'/job_id
        if path.resolve()!=path.absolute():raise ValueError('Invalid analysis location')
        return path

    @staticmethod
    def settings(body):
        start,end=body.get('startMs'),body.get('endMs')
        for value in (start,end):
            if value is not None and (type(value) is not int or not 0<=value<=253402300798000):raise ValueError('Invalid analysis range')
        if start is not None and end is not None and start>=end:raise ValueError('Start must precede end')
        normalize=body.get('normalize',False)
        if type(normalize) is not bool:raise ValueError('Invalid normalization setting')
        timezone=body.get('timezone','America/New_York')
        if not isinstance(timezone,str):raise ValueError('Invalid timezone')
        try:ZoneInfo(timezone)
        except (ZoneInfoNotFoundError,ValueError):raise ValueError('Choose a valid timezone') from None
        return {'startMs':start,'endMs':end,'normalize':normalize,'timezone':timezone}

    def _recover(self):
        active=self.root/'active.json'
        if self.process is not None:return
        if not active.exists():
            if self.job and self.job.get('external'):self.job=None
            return
        try:
            request=read_json(active)
            directory=self.directory(request['id'])
            if time.time()-(directory/'heartbeat').stat().st_mtime<15:
                self.job={**request,'state':'running','stage':'running in another launcher','external':True}
                return
            self.job={**request,'state':'interrupted','stage':'interrupted',
                      'error':'The previous analysis was interrupted. Run analysis again.'}
            # No saved PID is trusted or killed. An orphan loses its heartbeat.
            (directory/'cancel').touch(mode=0o600)
            active.unlink()
        except (OSError,ValueError,KeyError):
            raise ValueError('Analysis metadata is unreadable. Inspect the local analysis folder.') from None

    def start(self,participant,body):
        config=self.settings(body)
        if not participant:raise ValueError('Select a participant first')
        with self.lock:
            if self.closed:raise ValueError('Workspace is closing')
            if not self.available():raise ValueError('Analysis is unavailable in this build')
            if self.process:raise ValueError('An analysis is already running')
            self._recover()
            if (self.root/'active.json').exists():raise ValueError('An analysis is already running in this workspace')
            job_id=uuid.uuid4().hex
            self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
            directory=self.directory(job_id)
            directory.parent.mkdir(exist_ok=True,mode=0o700)
            directory.mkdir(mode=0o700)
            request={'id':job_id,'scope':self.scope(participant),'participant':participant,
                'database':str(self.database),'demo':self.demo,'signature':self.signature(),**config}
            write_json(directory/'request.json',request)
            (directory/'heartbeat').touch(mode=0o600)
            self.job={**request,'state':'running','stage':'starting','done':0,'total':0}
            write_json(directory/'status.json',{'state':'running','stage':'starting','done':0,'total':0})
            try:
                with (self.root/'active.json').open('x',encoding='utf-8') as active:
                    os.chmod(self.root/'active.json',0o600)
                    json.dump(request,active,allow_nan=False)
            except FileExistsError:
                self.job=None
                raise ValueError('An analysis is already running in this workspace') from None
            env=dict(os.environ);env.pop('AWEAR_API_KEY',None)
            try:
                self.process=subprocess.Popen([sys.executable,str(Path(__file__).parent/'analysis_worker.py'),str(directory)],
                    cwd=Path(__file__).parent,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            except OSError:
                self.job.update(state='failed',stage='failed',error='Could not start analysis')
                (self.root/'active.json').unlink(missing_ok=True)
                raise ValueError('Could not start analysis') from None
            self.monitor=threading.Thread(target=self._watch,name='quality-analysis',daemon=True)
            self.monitor.start()
            return self._public_job(participant)

    def _watch(self):
        try:
            self._watch_loop()
        except (OSError,ValueError,KeyError):
            # A metadata/storage failure must not leave a live, unmonitored worker.
            with self.lock:
                process=self.process
                if process and process.poll() is None:
                    process.terminate()
                    try:process.wait(timeout=3)
                    except subprocess.TimeoutExpired:process.kill();process.wait()
                self.process=None
                self.job.update(state='failed',stage='failed',error='Analysis storage failed. Check free disk space and retry.')
                try:(self.root/'active.json').unlink(missing_ok=True)
                except OSError:pass

    def _watch_loop(self):
        while True:
            with self.lock:
                if self.process is None:return
                directory=self.directory(self.job['id'])
                (directory/'heartbeat').touch(mode=0o600)
                code=self.process.poll()
                try:status=read_json(directory/'status.json')
                except (OSError,ValueError):status={}
                self.job.update(status)
                if code is None:
                    if (directory/'cancel').exists():self.job.update(state='canceling',stage='canceling')
                    elif status.get('state')=='completed':self.job.update(state='running',stage='finishing')
                if code is not None:
                    if (directory/'cancel').exists():
                        self.job.update(state='canceled',stage='canceled')
                        (directory/'report.json').unlink(missing_ok=True)
                    elif code==0 and status.get('state')=='completed':
                        try:
                            summary=read_json(directory/'summary.json')
                            report=directory/'report.json'
                            if report.is_symlink() or not report.is_file() or summary['analysisIdentity']!=status['analysisIdentity']:raise ValueError('Incomplete report')
                            pointer={'id':self.job['id'],'signature':self.job['signature'],
                                     'settings':{k:self.job[k] for k in ('startMs','endMs','normalize','timezone')}}
                            write_json(self.root/(self.job['scope']+'.json'),pointer)
                            try:self._prune()
                            except OSError:self.job['warning']='Older generated payloads could not be pruned.'
                        except (OSError,ValueError,KeyError):self.job.update(state='failed',stage='failed',error='Could not publish complete analysis')
                    else:self.job.update(state='failed',stage='failed',error=status.get('error','Analysis stopped unexpectedly. Retry analysis.'))
                    if self.job['state']!='completed':
                        try:remove_quality_payloads(directory)
                        except OSError:self.job['warning']='Incomplete generated quality lanes could not be removed.'
                    try:
                        write_json(directory/'status.json',{k:v for k,v in self.job.items() if k not in ('database','participant','signature')})
                        (self.root/'active.json').unlink(missing_ok=True)
                    except OSError:self.job['warning']='Analysis finished, but final job metadata could not be saved.'
                    self.process=None
                    return
            time.sleep(.25)

    def _prune(self):
        # Keep all current participants' payloads and one extra most recent payload.
        keep=set()
        for pointer in self.root.glob('*.json'):
            if re.fullmatch(r'[0-9a-f]{64}\.json',pointer.name):
                try:keep.add(read_json(pointer)['id'])
                except (OSError,ValueError,KeyError):pass
        reports=sorted((self.root/'jobs').glob('*/report.json'),key=lambda p:p.stat().st_mtime_ns,reverse=True)
        extra=1
        for report in reports:
            if report.parent.name in keep:continue
            if extra:extra-=1;continue
            if JOB_ID.fullmatch(report.parent.name) and not report.is_symlink():
                try:self.directory(report.parent.name)
                except ValueError:continue
                report.unlink()
                remove_quality_payloads(report.parent)

    def cancel(self):
        with self.lock:
            if self.process:
                (self.directory(self.job['id'])/'cancel').touch(mode=0o600)
                self.job.update(state='canceling',stage='canceling')
            return {'cancelRequested':self.process is not None}

    def _public_job(self,participant):
        if not self.job or self.job.get('scope')!=self.scope(participant):return None
        return {k:self.job[k] for k in ('id','state','stage','done','total','error','reusedWindows','computedFeatures','external','warning','peakMemoryBytes') if k in self.job}

    def state(self,participant):
        with self.lock:
            self._recover()
            result={'available':self.available(),'participantId':participant,'demo':self.demo,'job':self._public_job(participant),'summary':None,'stale':False,'settings':None}
            if not participant:return result
            try:
                pointer=read_json(self.root/(self.scope(participant)+'.json'))
                result.update(summary=read_json(self.directory(pointer['id'])/'summary.json'),
                              settings=pointer['settings'],stale=pointer['signature']!=self.signature())
            except (OSError,ValueError,KeyError):pass
            return result

    def report_path(self,participant):
        with self.lock:
            try:
                pointer=read_json(self.root/(self.scope(participant)+'.json'))
                path=self.directory(pointer['id'])/'report.json'
                if path.is_symlink() or not path.is_file():raise ValueError('Missing report')
                return path
            except (OSError,ValueError,KeyError):raise ValueError('No completed report for this source') from None

    def daily_quality(self,participant,date,start,end):
        from daily_review import partition
        result={'participantId':participant,'date':date,'startMs':start,'endMsExclusive':end,
                'status':'unavailable','analysisIdentity':None,'runs':partition([],start,end,'not_analyzed')}
        if not participant:return result
        with self.lock:
            try:
                pointer=read_json(self.root/(self.scope(participant)+'.json'))
                if pointer['signature']!=self.signature():return {**result,'status':'stale'}
                directory=self.directory(pointer['id'])
                index=read_json(directory/'quality-index.json')
                if index['version']!=1 or index['participantId']!=participant:raise ValueError('Invalid quality scope')
                if read_json(directory/'summary.json')['analysisIdentity']!=index['analysisIdentity']:raise ValueError('Invalid completed quality identity')
                result['analysisIdentity']=index['analysisIdentity']
                runs=[]
                if date in index['dates']:
                    value=read_json(directory/('quality-'+date+'.json'),limit=8_000_000)
                    if value['version']!=1 or value['participantId']!=participant or value['analysisIdentity']!=index['analysisIdentity']:raise ValueError('Invalid quality identity')
                    for run in value['runs']:
                        if any(type(run[k]) is not int for k in ('startMs','endMsExclusive')) or run['startMs']>=run['endMsExclusive']:raise ValueError('Invalid quality range')
                        if run['state'] not in ('flagged','computed_unflagged') or not isinstance(run['flags'],list) or any(not isinstance(f,str) for f in run['flags']):raise ValueError('Invalid quality flags')
                        if bool(run['flags'])!=(run['state']=='flagged'):raise ValueError('Invalid quality state')
                    runs=value['runs']
                if pointer['signature']!=self.signature():return {**result,'status':'stale'}
                return {**result,'status':'current','runs':partition(runs,start,end,'not_analyzed')}
            except (OSError,ValueError,KeyError,TypeError):return result

    def close(self):
        with self.lock:
            self.closed=True
            self.cancel()
            process=self.process
        if process:
            try:process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:process.wait(timeout=3)
                except subprocess.TimeoutExpired:process.kill();process.wait()
        if self.monitor:self.monitor.join(timeout=5)
