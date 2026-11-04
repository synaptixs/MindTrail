"""One escaped, dependency-free discovery presentation for live and standalone use."""
from __future__ import annotations
from datetime import datetime
from html import escape
import hashlib
from pathlib import Path
from zoneinfo import ZoneInfo
from analysis_provenance import fingerprint
from discovery_decisions import CHOICES

CSS=Path(__file__).with_name('static')/'report.css'
LIMITS={'families':20,'chartRows':100,'tableRows':200,'changeDays':25}


def presentation_identity():return fingerprint({'renderer':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'css':hashlib.sha256(CSS.read_bytes()).hexdigest()})
def e(value):return escape(str(value),quote=True)
def fact_label(value):
    import re
    return re.sub(r'(?<=[a-z])(?=[A-Z])',' ',value).replace('_',' ').capitalize()
def num(value):return format(value,'.5g') if isinstance(value,(int,float)) else str(value)
def stamp(value,zone):return 'not available' if value is None else datetime.fromtimestamp(value/1000,ZoneInfo(zone)).isoformat(timespec='milliseconds')
def p(value):return '<p>'+e(value)+'</p>'
def table(headers,rows):return '<div class="report-table" tabindex="0"><table><thead><tr>'+''.join('<th>'+e(h)+'</th>' for h in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+e(v)+'</td>' for v in row)+'</tr>' for row in rows)+'</tbody></table></div>'


def chart(group,feature):
    rows=[r for r in group['conditions'][:LIMITS['chartRows']] if r['proxyQuartiles'] is not None]
    meta=group['proxies'][feature];label=meta['label']+' ('+meta['units']+')'
    start='<svg viewBox="0 0 760 230" role="img" aria-label="'+e(label)+'"><title>'+e(label+'; Q1/median/Q3 by day/exact context')+'</title>'
    if not rows:return start+'<text x="60" y="80">No unflagged feature support</text></svg>'
    values=[v for r in rows for v in r['proxyQuartiles'][feature]];lo,hi=min(values),max(values)
    if 'fraction' in meta['units']:lo,hi=0,1
    elif hi-lo<.1:middle=(lo+hi)/2;lo,hi=middle-.05,middle+.05
    y=lambda v:185-(v-lo)/(hi-lo)*145
    result=[start]
    for f in (0,.5,1):
        v=lo+(hi-lo)*f;yp=y(v);result.append(f'<line x1="75" x2="745" y1="{yp}" y2="{yp}" class="report-grid"/><text x="0" y="{yp+4}">{e(num(v))}</text>')
    for i,row in enumerate(rows):
        x=75+(i+.5)/len(rows)*670;q=row['proxyQuartiles'][feature]
        result.append(f'<line x1="{x}" x2="{x}" y1="{y(q[0])}" y2="{y(q[2])}" class="report-whisker"/><circle cx="{x}" cy="{y(q[1])}" r="3" class="report-dot"><title>{e(row["date"]+"; session "+str(row["sessionId"])+"; median "+num(q[1]))}</title></circle>')
    result.extend(['<text x="75" y="218">'+e(rows[0]['date'])+'</text>','<text x="745" y="218" text-anchor="end">'+e(rows[-1]['date'])+'</text></svg>'])
    return ''.join(result)


def sections(value,*,live=False):
    n=value['numerical'];human=value['human'];c=human['context'];zone=n['timezone'];parts=[]
    def section(title,body):parts.append('<section class="report-section"><h2>'+e(title)+'</h2>'+body+'</section>')
    intro=p('Synthetic demonstration — no personal EEG' if n['demo'] else 'Personal cloud EEG discovery snapshot')
    intro+=p('Observed '+stamp(n['observedExtent']['startMs'],zone)+' → '+stamp(n['observedExtent']['endMsExclusive'],zone)+' (end exclusive); '+zone)
    intro+=p('Readiness: '+n['readiness'].replace('_',' ')+'. Discovery evidence is not confirmation, activity recognition or an inferential result.')
    intro+=table(['Population fact','Count'],[(fact_label(k),v) for k,v in n['coverage'].items() if isinstance(v,(int,float))])
    intro+=p(n['population'])+p('Record-seconds and context-specific elapsed times are not total device-wearing duration. Incompatible acquisition families remain separate.')
    section('What supports exploration?',intro)
    selection=n['selection'];body=p(f"{selection['offeredWindows']} distinct review windows offered of {selection['eligibleWindows']} eligible; {selection['unofferedWindows']} not offered.")
    body+=table(['Stream','Selected','Pool','Omitted'],[(k,v['selected'],v['pool'],v['omitted']) for k,v in selection['streams'].items()])
    body+=p(selection['policy']['selection'])+p(selection['policy']['meaning'])
    section('What was selected for review?',body)
    body=p(f"Showing {min(len(n['families']),LIMITS['families'])} of {n['familyCount']} families; {n['dayContextRows']} day/exact-context rows exist in the full numerical JSON.")
    for group in n['families'][:LIMITS['families']]:
        a=group['acquisition'];rows=group['conditions'];body+='<details open><summary>'+e(str(a['device_id'])+' · '+a['source']+' · '+a['placement']+' ear · '+a['channel']+' · '+str(a['sample_rate'])+' Hz')+'</summary>'
        body+=p(str(a['units'])+'; '+str(len(group['contexts']))+' exact contexts; explicit session IDs retained, unknown sessions remain unknown. Contact unmeasured.')
        body+=p(f"Charts: first {min(len(rows),LIMITS['chartRows'])} of {len(rows)} rows (rows without unflagged features have no dot). Equally spaced day/context positions, not elapsed-time spacing. Dots are medians; whiskers Q1–Q3, not confidence intervals. No lines bridge days or gaps.")
        for feature in range(3):body+='<h3>'+e(group['proxies'][feature]['label'])+'</h3>'+chart(group,feature)
        shown=rows[:LIMITS['tableRows']];body+=p(f"Table: first {len(shown)} of {len(rows)} rows; {len(rows)-len(shown)} omitted here.")
        body+=table(['Date / session','Unflagged / flagged','Recorded / missing seconds','AC RMS Q1 / median / Q3','Screening states (seconds)'],[(r['date']+' / '+str(r['sessionId'] if r['sessionId'] is not None else 'not recorded'),f"{r['unflaggedWindows']} / {r['flaggedWindows']}",f"{num(r['recordedUnionMs']/1000)} / {num(r['missingMs']/1000)}",' / '.join(map(num,r['proxyQuartiles'][0])) if r['proxyQuartiles'] else 'No support','; '.join(k.replace('_',' ')+': '+num(v/1000) for k,v in r['qualityMs'].items())) for r in shown]);body+='</details>'
    section('Daily variation and recording conditions',body)
    total=n['changeTotals'];body=table(['Change screening fact','Total'],[(fact_label(k),v) for k,v in total.items()])
    body+=p(f"Showing first {min(len(n['changes']),LIMITS['changeDays'])} of {len(n['changes'])} context/day screening rows, with at most 3 ranked flank examples per row. These heuristic cues are not statistical tests, task transitions or causes.")
    for row in n['changes'][:LIMITS['changeDays']]:
        body+='<details><summary>'+e(row['date']+' · '+row['acquisitionGroupId'][:12]+'… · '+row['status'].replace('_',' '))+'</summary>'
        body+=p(f"{row['evaluatedBoundaries']} boundaries evaluated; {row['retained']} retained; {row['shown']} examples shown, {row['omitted']} retained examples omitted.")
        for cue in row['items']:
            body+=p(stamp(cue['atMs'],zone)+' · heuristic score '+num(cue['score']))
            body+=table(['Feature','Before Q1 / median / Q3','After Q1 / median / Q3'],[(label,' / '.join(map(num,cue['beforeQuartiles'][i])),' / '.join(map(num,cue['afterQuartiles'][i]))) for i,label in enumerate(['AC RMS','Log power 1–40 Hz','Alpha share'])])
        body+='</details>'
    section('Changes worth inspecting',body)
    body=p(str(n['probeCount'])+' ordinary reference windows were probed, using the W4 selection. This is not an all-pairs recurrence search or episode matching.')
    for probe in n['probes']:
        ref=probe['withPower']['reference'];body+='<details><summary>'+e(stamp(ref['startMs'],zone)+' · observed reference '+probe['reference'][:12]+'…')+'</summary>'
        sensitivity=probe['viewSensitivity'];body+=p('View sensitivity: '+sensitivity['status'].replace('_',' ')+f"; {sensitivity['changedNearestWindow']} nearest window IDs changed among {sensitivity['commonShownDates']} common shown dates; {sensitivity['uncomparedShownDates']} shown dates not compared. This is not a refit stability test.")
        for name,key in [('Ratios + power','withPower'),('Ratios only','spectralOnly')]:
            q=probe[key];counts=q['counts'];body+='<h3>'+e(name)+'</h3>'+p(q['status'].replace('_',' ')+'. '+q['supportNote'])
            body+=p(f"Population {counts['eligiblePopulation']}; same-date excluded {counts['sameDayExcluded']}; guard excluded {counts['temporalGuardExcluded']}; other-date windows {counts['eligibleOtherWindows']}. Showing {counts['shown']} of {counts['otherDates']} date minima; {counts['omittedDates']} omitted.")
            if q.get('distanceQuartiles'):body+=p('All eligible other-window distance Q1 / median / Q3: '+' / '.join(map(num,q['distanceQuartiles'])))
            body+=p('Metric: '+q['metric']+'. Scaler '+q['scaler']['identity']+'; fit windows '+str(q['scaler']['fitWindows'])+'. '+q['scaler']['scope']+'. Different view distances are not comparable magnitudes.')
            body+=table(['Other date','Source start','Distance','Session'],[(r['date'],stamp(r['startMs'],zone),num(r['distance']),r['acquisition']['session_id'] if r['acquisition']['session_id'] is not None else 'not recorded') for r in q['items']])
            for item in q['items']:
                body+='<details><summary>'+e(item['date']+' contributions / source '+item['windowId'][:12]+'…')+'</summary>'
                body+=p('Source window '+item['windowId']+'; '+stamp(item['startMs'],zone)+' → '+stamp(item['endMsExclusive'],zone))
                body+=table(['Feature','Reference','Candidate','IQR scale','Scaled difference','Squared contribution'],[(d['feature'],num(d['reference']),num(d['candidate']),num(d['iqrScale']),num(d['scaledDelta']),num(d['squaredContribution'])) for d in item['differences']]);body+='</details>'
        if live:body+='<a href="/recurrence.html?exactGroup='+e(ref['acquisitionGroupId'])+'&amp;window='+e(ref['windowId'])+'">Inspect this reference and exact source in Across days →</a>'
        body+='</details>'
    section('Cross-day candidates and feature sensitivity',body)
    body=p(f"Optional window observations: {c['active']} active, {c['tombstones']} undone records, {c['historyRows']} history revisions. {c['sameFeatureSnapshot']} saved under this exact feature/source snapshot; {c['earlierSnapshot']} under other snapshots. Other-snapshot observations are preserved, not automatically revalidated or declared wrong.")
    body+=p('Activity interval annotations remain separate; their provenance is available in daily lanes. They are not treated as verified tasks by this summary.')
    body+=table(['Observation kind','Active records'],list(c['kindTotals'].items()))+p(f"Showing first {c['shown']} of {c['active']} active observations by stored key; {c['omitted']} omitted. These counts are not population review coverage.")
    for item in c['items']:
        r=item['record'];body+='<details><summary>'+e(stamp(r['window']['startMs'],zone)+' · '+r['kind']+' · '+item['relation'].replace('_',' '))+'</summary>'
        body+=p('Personal activities: '+(', '.join(a['name'] for a in r['activities']) or 'none named'))+p('Recollection: '+r['facts'])+p('Possible explanation (not a finding): '+r['hypothesis'])
        body+=p('Review '+r['reviewState']+'; timing '+r['timingPrecision']+'; uncertainty before/after ms '+str(r['startUncertaintyMs'])+' / '+str(r['endUncertaintyMs']))+'</details>'
    section('Human context and what remains unknown',body)
    body=p(str(human['questions']['total'])+' saved personal questions; all shown. They are authored aims, not validated findings.')
    for q in human['questions']['questions']:body+='<details><summary>'+e(q['title']+' · '+q['stage'])+'</summary>'+p(q['notes'])+p('Saved feature snapshot: '+q['analysisIdentity'])+'</details>'
    body+='<h3>Alternative explanations to consider</h3><ul>'+''.join('<li>'+e(x)+'</li>' for x in n['alternativeExplanations'])+'</ul>'
    section('Questions and alternative explanations',body)
    d=value['decision'];body=p('Direction status: '+value['decisionRelation'].replace('_',' ')+'. A direction records intent; it does not activate inference or reserve confirmation data.')
    if d['record']:
        r=d['record'];body+=p(CHOICES[r['choice']])+p(r['notes'])+p('Chosen question: '+(r['question']['title'] if r['question'] else 'none'))
    body+=p(f"Decision history: showing {d['shown']} of {d['total']} revisions; {d['total']-d['shown']} omitted in this snapshot.")
    body+=table(['Revision','Direction','Recorded time'],[(r['revision'],CHOICES[r['choice']],r['changedAt']) for r in d['history']])
    body+='<h3>Delivered</h3><ul>'+''.join('<li>'+e(x)+'</li>' for x in n['delivered'])+'</ul><h3>Deferred</h3><ul>'+''.join('<li>'+e(x)+'</li>' for x in n['deferred'])+'</ul>'
    section('Direction and delivered/deferred branches',body)
    audit=table(['Identity','Value'],[(k,n[k]) for k in ('analysisIdentity','sourceRevision','discoveryIdentity','dailyIdentity','recurrenceIdentity','reviewIdentity','reportIdentity')]+[(k,value[k]) for k in ('evidenceIdentity','snapshotIdentity','compositionMethodIdentity','presentationIdentity')])
    audit+=p('Point-to-point feature evidence only. No raw EEG samples embedded. Source bounds, identities and units remain available for later verification. The numerical JSON retains all day/context rows; HTML chart/table limits are stated above.')
    section('Provenance and scope','<details><summary>Source identities and export scope</summary>'+audit+'</details>')
    return '<div class="report-document">'+''.join(parts)+'</div>'


def document(value):
    body='<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>MindTrail discovery report</title><style>'+CSS.read_text(encoding='utf-8')+'</style></head><body><main class="report-offline"><h1>MindTrail Lab · Discovery report</h1>'+sections(value)+'</main></body></html>'
    encoded=body.encode('utf-8')
    if len(encoded)>32_000_000:raise ValueError('HTML report exceeds32MB; narrow analysis range')
    return encoded
