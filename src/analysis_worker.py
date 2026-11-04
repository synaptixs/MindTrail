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
    # Windows readers can briefly hold the destination without delete sharing.
    # Keep the prior complete file until replacement succeeds; never retry other I/O errors.
    for attempt in range(21):
        if check:check()
        try:
            temporary.replace(path)
            break
        except PermissionError as error:
            if sys.platform!='win32' or getattr(error,'winerror',None) not in (5,32,33) or attempt==20:raise
            time.sleep(.025)


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
            from discovery_contracts import build as discovery_build
            discovery=discovery_build(report,request['participant'],request['timezone'],demo=request['demo'],check=check)
            atomic_json(directory/'discovery.json',discovery,check)
            from discovery_daily import build as daily_discovery
            progress('daily discovery',0,1)
            daily_index,daily_payloads=daily_discovery(report,Path(request['database']),request['participant'],request['timezone'],discovery['discoveryIdentity'],check=check)
            for (group,date),payload in daily_payloads.items():
                atomic_json(directory/f'discovery-day-{group}-{date}.json',payload,check)
            atomic_json(directory/'discovery-daily-index.json',daily_index,check)
            from recurrence_model import build as recurrence_build
            progress('cross-day recurrence',0,1)
            recurrence_index,recurrence_payloads=recurrence_build(report,daily_index,daily_payloads,request['participant'],check)
            for group,payload in recurrence_payloads.items():
                atomic_json(directory/f'recurrence-{group}.json',payload,check)
            atomic_json(directory/'recurrence-index.json',recurrence_index,check)
            from discovery_review_model import build as review_build
            progress('selective review',0,1)
            review=review_build(recurrence_index,recurrence_payloads,daily_payloads,check)
            atomic_json(directory/'discovery-review.json',review,check)
            from discovery_report_model import build as report_build,analysis_context
            progress('discovery report',0,1)
            discovery_summary=report_build(discovery,daily_index,daily_payloads,recurrence_index,recurrence_payloads,review,check,analysis_context=analysis_context(report))
            atomic_json(directory/'discovery-report.json',discovery_summary,check)
            del daily_payloads,recurrence_payloads
            days=quality_days(report,check)
            for date,runs in sorted(days.items()):
                atomic_json(directory/('quality-'+date+'.json'),{'version':1,'analysisIdentity':report['analysisIdentity'],'participantId':request['participant'],'runs':runs},check)
                check()
            atomic_json(directory/'quality-index.json',{'version':1,'analysisIdentity':report['analysisIdentity'],'participantId':request['participant'],'dates':sorted(days)},check)
            if request.get('candidateBoundaries'):
                from candidate_boundaries import daily_candidates
                progress('candidate boundaries',0,1)
                candidates=daily_candidates(report,request['participant'],request['candidateBoundaries'],check)
                for date,value in sorted(candidates.items()):
                    atomic_json(directory/('boundaries-'+date+'.json'),value,check)
                atomic_json(directory/'boundaries-index.json',{'version':1,'participantId':request['participant'],
                    'analysisIdentity':report['analysisIdentity'],'dates':sorted(candidates)},check)
            if request.get('exploration'):
                from exploratory_views import build_views
                index,payloads=build_views(report,check=check,progress=progress)
                index['demo']=request['demo']
                for (group,view),payload in payloads.items():
                    check()
                    atomic_json(directory/f'exploration-{group}-{view}.json',payload,check)
                atomic_json(directory/'exploration-index.json',index,check)
            if request.get('activityComparisons'):
                from activity_comparisons import build
                from analysis_provenance import canonical_json
                index,payloads=build(report,Path(request['database']),request['timezone'],check=check,progress=progress)
                index['demo']=request['demo']
                if len(canonical_json(index).encode('utf-8'))>2_000_000:raise ValueError('Comparison manifest exceeds its output bound')
                for group,payload in payloads.items():
                    check()
                    if len(canonical_json(payload).encode('utf-8'))>8_000_000:raise ValueError('Comparison context exceeds its output bound')
                    atomic_json(directory/f'activities-{group}.json',payload,check)
                atomic_json(directory/'activities-index.json',index,check)
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
