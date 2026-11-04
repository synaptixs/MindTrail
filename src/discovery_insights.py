"""Deterministic, stdlib question views of existing discovery facts; no text interpretation."""
from __future__ import annotations
import hashlib
from pathlib import Path
from analysis_provenance import fingerprint

LENSES={'recurrence_conditions':'Spectral recurrence and recording conditions',
        'daily_variation':'Daily feature variation', 'open_review':'Open question / manual evidence review'}
STARTER='Which spectral patterns recur across days, and how sensitive are those matches to amplitude and recording-quality cues?'


def method_identity():return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def source_row(row):
    return {k:row[k] for k in ('date','acquisitionGroupId','sessionId','startMs','endMsExclusive','unflaggedWindows','flaggedWindows')}


def feature_range(rows,index):
    supported=[r for r in rows if r['unflaggedWindows'] and r['proxyQuartiles'] is not None]
    if not supported:return None
    values=[r['proxyQuartiles'][index][1] for r in supported];low,high=min(values),max(values)
    def point(value):
        matches=[r for r in supported if r['proxyQuartiles'][index][1]==value]
        return {'value':value,'firstSourceRow':source_row(matches[0]),'tiedRows':len(matches)}
    return {'minimum':point(low),'maximum':point(high),'rows':len(supported),
            'meaning':'Range of day/exact-context medians, not pooled windows, effect size or uncertainty'}


def build(numerical,human,question=None,lens='recurrence_conditions'):
    if human['questions']['participantId']!=numerical['participantId']:raise ValueError('Question evidence scope differs')
    if not isinstance(lens,str) or lens not in LENSES:raise ValueError('Choose a supported evidence view')
    if question not in (None,'') and (not isinstance(question,str) or len(question)!=32 or any(c not in '0123456789abcdef' for c in question)):raise ValueError('Invalid report question identity')
    selected=next((q for q in human['questions']['questions'] if q['id']==question),None)
    if question and selected is None:raise ValueError('Question is unavailable for this participant. Choose a current question or the starter prompt.')
    prompt=({'origin':'personal_question','record':selected,'analysisRelation':'same_analysis' if selected['analysisIdentity']==numerical['analysisIdentity'] else 'earlier_analysis'} if selected else
        {'origin':'starter_prompt','record':{'id':None,'title':STARTER,'revision':None,'stage':'discovery_prompt','notes':''},'analysisRelation':'not_applicable'})
    families=[]
    for group in numerical['families']:
        rows=sorted(group['conditions'],key=lambda r:(r['date'],r['acquisitionGroupId']))
        supported=[r for r in rows if r['unflaggedWindows'] and r['proxyQuartiles'] is not None]
        dates=sorted({r['date'] for r in supported});known={r['sessionId'] for r in rows if r['sessionId'] is not None}
        probes=[]
        for probe in numerical['probes']:
            if probe['family']!=group['compatibilityId']:continue
            probes.append({'reference':probe['reference'],'startMs':probe['withPower']['reference']['startMs'],
                'viewSensitivity':probe['viewSensitivity'],
                'views':{key:{k:probe[key].get(k) for k in ('status','counts','supportNote','distanceQuartiles')} for key in ('withPower','spectralOnly')}})
        informative=any(p['viewSensitivity']['status']=='descriptive_comparison' and p['viewSensitivity']['commonShownDates'] for p in probes)
        status=('no_unflagged_support' if not dates else 'manual_review' if lens=='open_review' else
                'exploratory_evidence' if lens=='daily_variation' else 'no_cross_day_support' if len(dates)<2 else
                'exploratory_evidence' if informative else 'no_informative_sampled_probe')
        families.append({'family':group['compatibilityId'],'acquisition':group['acquisition'],'status':status,
            'dayContextRows':len(rows),'featureRows':len(supported),'featureDates':dates,'featureDateCount':len(dates),
            'knownSessions':len(known),'unknownSessionRows':sum(r['sessionId'] is None for r in rows),
            'computedWindows':group['accounting']['computedContained'],'unflaggedWindows':group['accounting']['eligible'],
            'flaggedWindows':group['accounting']['flaggedExcluded'],'dayBoundaryWindowsTouching':group['accounting']['dayBoundaryWindowsTouching'],
            'recordedContextMs':sum(r['recordedUnionMs'] for r in rows),'missingContextMs':sum(r['missingMs'] for r in rows),
            'features':[{'label':meta['label'],'units':meta['units'],'medianRange':feature_range(rows,i)} for i,meta in enumerate(group['proxies'][:3])],
            'probes':probes,'probeCount':len(probes),'supportMeaning':'Dates and sessions describe observed support, not independent repetitions; durations sum exact contexts and can overlap across contexts.'})
    if numerical['readiness']=='no_recordings':status='no_recordings'
    elif not numerical['coverage']['unflaggedWindows']:status='no_unflagged_support'
    elif not any(g['featureDateCount'] for g in families):status='no_whole_day_support'
    elif lens=='open_review':status='manual_review'
    elif any(g['status']=='exploratory_evidence' for g in families):status='exploratory_evidence'
    elif all(g['featureDateCount']<2 for g in families):status='no_cross_day_support'
    else:status='no_informative_sampled_probe'
    value={'version':1,'question':prompt,'selection':{'question':question or '', 'lens':lens},'lensLabel':LENSES[lens],
        'status':status,'answerStatus':'unconfirmed; selected evidence requires human interpretation',
        'linkage':'Viewer-selected question and evidence view; question text is not interpreted and relevance is not established automatically.',
        'source':{k:numerical[k] for k in ('analysisIdentity','sourceRevision','reportIdentity','observedExtent','requestedRange','timezone','demo')},
        'coverage':numerical['coverage'],'windowAccounting':numerical['windowAccounting'],'families':families,'familyCount':len(families),
        'changes':numerical['changeTotals'],'missingContext':numerical['missingContext'],
        'inference':'not_implemented','confirmation':'not_reserved',
        'limitations':['A nearest candidate is not a confirmed recurring pattern or activity.',
            'At most four ordinary references are probed; each view shows at most ten other-date minima. No all-pairs search or recurrence prevalence is estimated.',
            'Feature-view sensitivity is descriptive only among shown dates common to both views; no refit stability or independent confirmation.',
            'Quality flags are screening cues; unflagged data is not established artifact-free EEG.',
            'Unknown sessions, unmeasured contact and uneven coverage limit interpretation. No causal, biological or mental-state attribution.'],
        'methodIdentity':method_identity()}
    value['insightsIdentity']=fingerprint(value);return value
