"""Canonical discovery-summary facts; reuses existing estimands, never an inference engine."""
from __future__ import annotations
import hashlib
from pathlib import Path
from analysis_provenance import fingerprint,canonical_json
from recurrence_access import retrieve

MAX_BYTES=8_000_000
POLICY={'changeExamplesPerDay':3,'recurrenceReferences':'Same at most4 W4 ordinary references; not all possible pairs',
 'viewComparison':'Window-ID changes only among shown date minima common to both views; no independent stability test',
 'inference':'not_implemented','confirmation':'not_reserved'}
ALTERNATIVES=[
 'Recording conditions can coincide with feature changes. Contact/impedance is unmeasured; sessions are unknown unless explicitly recorded.',
 'Missing coverage and screening exclusions can change the observed distribution. Unflagged windows are not established artifact-free EEG.',
 'Feature ratios, power and full-snapshot IQR scaling affect nearest ranks; a nearest rank does not establish meaningful recurrence.',
 'Neighboring windows, dates and selected examples are not independent experiments or probability samples.',
 'Human context is optional recollection, not verified task alignment. Missing or mixed context does not justify activity recognition.',
 'No inferential estimator, holdout reservation, prediction, causal claim or mental-state score is implemented by this report.']
BRANCHES={'continue_discovery':'Keep exploring without mandatory activity labels',
 'collect_context':'Collect more data or optional context',
 'plan_matched_recording':'Plan matched recordings later; protocol still required',
 'insufficient_evidence':'Record insufficient evidence for a next question'}


def analysis_context(report):
    import sys,platform
    names=('analysis_worker.py','discovery_contracts.py','discovery_daily.py','recurrence_model.py','spectral_features.py','discovery_review_model.py','discovery_report_model.py')
    return {'spec':report['spec'],'qualityPolicy':report['qualityPolicy'],'implementation':report['implementation'],
        'qualityMethodVersion':report['methodVersion'],'python':list(sys.version_info[:3]),
        'platform':platform.system(),'machine':platform.machine(),
        'numericalSources':{name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in names}}


def build(discovery,daily_index,daily,family_index,families,review,check=lambda:None,*,analysis_context=None):
    participant=discovery['participantId'];identity=discovery['analysisIdentity']
    if any(v['participantId']!=participant or v['analysisIdentity']!=identity for v in (daily_index,family_index,review)):raise ValueError('Report source scope differs')
    if family_index['dailyIdentity']!=daily_index['dailyIdentity'] or review['recurrenceIdentity']!=family_index['recurrenceIdentity']:raise ValueError('Report dependency differs')
    groups=[]
    for key,payload in sorted(families.items()):
        check()
        if payload['participantId']!=participant or payload['analysisIdentity']!=identity:raise ValueError('Report family differs')
        groups.append({k:payload[k] for k in ('compatibilityId','acquisition','contexts','conditions','accounting','scaler','proxies')})
    changes=[]
    for (group,date),payload in sorted(daily.items()):
        check();c=payload['changes'];items=c['retained'][:POLICY['changeExamplesPerDay']]
        changes.append({'acquisitionGroupId':group,'date':date,'status':c['status'],'evaluatedBoundaries':c['evaluatedBoundaries'],
            'aboveThreshold':c['aboveThreshold'],'suppressed':c['suppressed'],'retained':len(c['retained']),
            'shown':len(items),'omitted':len(c['retained'])-len(items),'items':items})
    probes=[]
    for reference in review['recurrenceQueries']:
        check();payload=families[reference['family']]
        power=retrieve(payload,reference['reference'],'with_power');shape=retrieve(payload,reference['reference'],'spectral_only')
        comparable=power['status']=='ranked_candidates' and shape['status']=='ranked_candidates'
        a={r['date']:r['windowId'] for r in power['items']};b={r['date']:r['windowId'] for r in shape['items']};common=sorted(set(a)&set(b)) if comparable else []
        probes.append({'family':reference['family'],'reference':reference['reference'],'withPower':power,'spectralOnly':shape,
            'viewSensitivity':{'status':'descriptive_comparison' if comparable else 'not_comparable','commonShownDates':len(common),
                'changedNearestWindow':sum(a[d]!=b[d] for d in common),'uncomparedShownDates':len(set(a)|set(b))-len(common)}})
    value={'version':1,'participantId':participant,'analysisIdentity':identity,'sourceRevision':discovery['sourceRevision'],'analysisContext':analysis_context,
        'discoveryIdentity':discovery['discoveryIdentity'],'dailyIdentity':daily_index['dailyIdentity'],
        'recurrenceIdentity':family_index['recurrenceIdentity'],'reviewIdentity':review['reviewIdentity'],
        'readiness':discovery['readiness'],'demo':discovery['demo'],'timezone':discovery['timezone'],
        'observedExtent':discovery['observedExtent'],'requestedRange':discovery['requestedRange'],'coverage':discovery['coverage'],
        'population':discovery['population'],'missingContext':discovery['missingContext'],'limitations':discovery['limitations'],
        'contexts':discovery['contexts'],'families':groups,'familyCount':len(groups),
        'dayContextRows':sum(len(g['conditions']) for g in groups),'windowAccounting':daily_index['windowAccounting'],
        'changes':changes,'changeTotals':{k:sum(r[k] for r in changes) for k in ('evaluatedBoundaries','aboveThreshold','suppressed','retained','shown','omitted')},
        'selection':{k:review[k] for k in ('eligibleWindows','offeredWindows','unofferedWindows','streams','policy')},
        'probes':probes,'probeCount':len(probes),'policy':POLICY,'alternativeExplanations':ALTERNATIVES,'branches':BRANCHES,
        'delivered':['Label-free readiness','Daily features and heuristic changes','Point-to-point cross-day retrieval','Optional human context','Discovery summary and direction register'],
        'deferred':['Inferential estimator','Prediction and activity validation','Enforced confirmation holdout','Whole-episode sequence matching','Contact measurement','Bluetooth-file ingestion','Packaged numerical runtime/release'],
        'methodIdentity':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    value['reportIdentity']=fingerprint(value)
    if len(canonical_json(value).encode())>MAX_BYTES:raise ValueError('Discovery report exceeds8MB; narrow analysis range')
    check();return value
