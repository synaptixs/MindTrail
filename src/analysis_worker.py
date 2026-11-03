"""Isolated source-workspace analysis worker; receives no API credentials."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys
import time
import traceback


class AnalysisCancelled(Exception):
    pass


def atomic_json(path: Path, value: dict, check=None) -> None:
    temporary=path.with_suffix('.tmp')
    with temporary.open('w',encoding='utf-8') as output:
        os.chmod(temporary,0o600)
        encoder=json.JSONEncoder(ensure_ascii=False,allow_nan=False,sort_keys=True,separators=(',',':'))
        buffer=[]
        for index,chunk in enumerate(encoder.iterencode(value)):
            buffer.append(chunk)
            if index%4096==0:
                if check:check()
                output.write(''.join(buffer));buffer=[]
        output.write(''.join(buffer))
    temporary.replace(path)


def run(directory: Path) -> int:
    last_update=0
    def check():
        if (directory/'cancel').exists() or time.time()-(directory/'heartbeat').stat().st_mtime>15:
            raise AnalysisCancelled()
    def progress(stage,done,total):
        nonlocal last_update
        check()
        if done==total or time.monotonic()-last_update>.25:
            atomic_json(directory/'status.json',{'state':'running','stage':stage,'done':done,'total':total})
            last_update=time.monotonic()
    try:
        from analysis_resources import peak_memory_bytes
        from analysis_cache import FeatureCache
        from analysis_quality import analyze_archive, QualityPolicy
        from analysis_provenance import AnalysisSpec
        from analysis_view import coverage_summary
        from daily_review import quality_days
        request=json.loads((directory/'request.json').read_text(encoding='utf-8'))
        check()
        spec=AnalysisSpec(normalization='within-acquisition-group-robust' if request['normalize'] else 'none',
                          quality_policy_version=QualityPolicy().version)
        with FeatureCache(directory.parent.parent/'cache.sqlite3') as cache:
            report=analyze_archive(Path(request['database']),request['participant'],spec=spec,
                start_ms=request['startMs'],end_ms=request['endMs'],feature_cache=cache,check_cancel=check,progress=progress)
            check()
            summary=coverage_summary(report,timezone=request['timezone'],demo=request['demo'])
            progress('saving',0,1)
            atomic_json(directory/'report.json',report,check)
            check()
            atomic_json(directory/'summary.json',summary)
            days=quality_days(report,check)
            for date,runs in sorted(days.items()):
                atomic_json(directory/('quality-'+date+'.json'),{'version':1,'analysisIdentity':report['analysisIdentity'],'participantId':request['participant'],'runs':runs},check)
                check()
            atomic_json(directory/'quality-index.json',{'version':1,'analysisIdentity':report['analysisIdentity'],'participantId':request['participant'],'dates':sorted(days)},check)
            check()
            atomic_json(directory/'status.json',{'state':'completed','stage':'completed','done':1,'total':1,
                'reusedWindows':cache.hits,'computedFeatures':cache.misses,'analysisIdentity':report['analysisIdentity'],
                'peakMemoryBytes':peak_memory_bytes()})
        return 0
    except AnalysisCancelled:
        (directory/'report.tmp').unlink(missing_ok=True)
        (directory/'report.json').unlink(missing_ok=True)
        atomic_json(directory/'status.json',{'state':'canceled','stage':'canceled'})
        return 2
    except Exception:
        (directory/'report.tmp').unlink(missing_ok=True)
        (directory/'report.json').unlink(missing_ok=True)
        with (directory/'error.log').open('w',encoding='utf-8') as output:
            os.chmod(directory/'error.log',0o600)
            traceback.print_exc(file=output)
        atomic_json(directory/'status.json',{'state':'failed','stage':'failed','error':'Analysis failed. Retry or inspect the local analysis log.'})
        return 1


if __name__=='__main__':
    raise SystemExit(run(Path(sys.argv[1])))
