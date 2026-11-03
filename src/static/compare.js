'use strict';
const $=id=>document.getElementById(id),NS='http://www.w3.org/2000/svg';
const colors=['#258267','#5585ba','#bc8846','#9771ae','#bd7275','#5c9594','#8c9444','#797e99'];
let index=null,data=null,state=null,offset=0,sequence=0,waveSequence=0,busy=false;
const number=v=>v===null||v===undefined?'—':Number(v).toLocaleString(undefined,{maximumSignificantDigits:5});
const duration=ms=>`${number(ms/60000)} min`;
const stamp=ms=>new Intl.DateTimeFormat(undefined,{timeZone:index?.timezone||'America/New_York',dateStyle:'medium',timeStyle:'medium'}).format(ms);
function text(tag,value){const e=document.createElement(tag);e.textContent=value;return e;}
function svg(tag,attrs={},value){const e=document.createElementNS(NS,tag);for(const [k,v] of Object.entries(attrs))e.setAttribute(k,String(v));if(value!==undefined)e.textContent=value;return e;}
async function request(url,body){const response=await fetch(url,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Awear-Local':'1'},body:JSON.stringify(body)});const value=await response.json();if(!response.ok)throw Error(value.error||'Request failed');return value;}
function clearWave(){waveSequence++;$('waveform').replaceChildren();$('waveform-note').textContent='Select an example to verify its exact raw samples.';}
function clear(message){sequence++;data=null;$('report').hidden=true;$('freshness').textContent=message;clearWave();}
async function refresh(){
 try{const [s,snapshot]=await Promise.all([request('/api/analysis/state'),request('/api/activity-comparisons')]);state=s;
  if(s.participantId!==snapshot.participantId){index=null;clear('Participant changed. Refresh to inspect its comparisons.');return;}
  $('participant').textContent=s.demo?'Synthetic demo':s.participantId||'No participant selected';
  $('source').textContent=s.demo?'Synthetic test data · cloud-format right-ear provenance; not personal EEG measurements.':'Cloud EEG · behind the right ear · RIGHT_TEMP · 256 Hz. Bluetooth files are not included.';
  const running=s.job&&['running','canceling'].includes(s.job.state);$('run').disabled=!!running||!s.available||!s.participantId;$('cancel').hidden=!running||!!s.job.external;
  $('include-exploration').disabled=!s.explorationAvailable;if(!s.explorationAvailable)$('include-exploration').checked=false;
  $('status').textContent=running?`${s.job.stage}: ${s.job.total?`${number(s.job.done)} of ${number(s.job.total)}`:'working…'}`:s.job&&['failed','interrupted','canceled'].includes(s.job.state)?s.job.error||`Analysis ${s.job.state}`:'Ready to review saved comparisons.';
  $('availability').textContent=s.available?'Full-population comparisons run locally in the background. Raw recordings and annotations are read only.':'Numerical analysis is unavailable in this packaged build. Runtime bundling remains Phase 5; use the source analysis environment.';
  if(snapshot.status!=='current'){index=null;clear(snapshot.status==='stale'?'Source, annotations or analysis code changed. Rerun analysis; stale comparisons are withheld.':snapshot.status==='disabled'?'The latest analysis omitted activity comparisons. Click Analyze activities.':'No completed comparisons for this participant. Click Analyze activities.');return;}
  const changed=index?.comparisonIdentity!==snapshot.comparisonIdentity;index=snapshot;$('freshness').textContent=`Current completed snapshot · ${index.timezone} · all computed windows, not the clustering sample.`;
  if(changed||!data){const prior=$('group').value;$('group').replaceChildren(...index.groups.map(g=>new Option(`${g.acquisition.device_id||'Unknown device'} · ${g.acquisition.placement} ear · ${g.knownSessions} known sessions`,g.compatibilityId)));if(index.groups.some(g=>g.compatibilityId===prior))$('group').value=prior;offset=0;$('feature').replaceChildren(...index.features.map((f,i)=>new Option(`${f.label} · ${f.units}`,String(i))));if(index.groups.length)await load();else clear('No dated source contexts are available for activity comparison.');}
 }catch(e){$('status').textContent=e.message;clear('Could not verify the current comparison. Refresh to retry.');}
}
async function load(){
 const token=++sequence;clearWave();data=null;
 try{const params=new URLSearchParams({group:$('group').value,review:$('review').value,origin:$('origin').value,activity:$('activity').value,offset:String(offset)});const result=await request('/api/activity-comparisons?'+params);
  if(token!==sequence)return;if(result.status!=='current'||result.comparisonIdentity!==index?.comparisonIdentity)throw Error('Comparison changed. Refresh before interpreting it.');data=result;
  const previous=$('activity').value;$('activity').replaceChildren(new Option('All activities',''),...result.activities.map(a=>new Option(a.name,a.id)));if(result.activities.some(a=>a.id===previous))$('activity').value=previous;
  const a=result.acquisition;$('context').textContent=`${a.source} · ${a.device_id||'Unknown device'} · ${a.placement} ear · ${a.channel} · ${a.sample_rate} Hz · ${a.timestamp_basis} · ${a.units}. ${result.contexts.length} exact acquisition/session contexts remain separate.`;
  const c=index.sourceCoverage;$('accounting').textContent=`Full source: ${number(c.sourceRecords)} records; ${number(c.computedWindows)} computed windows = ${number(c.unflaggedWindows)} unflagged + ${number(c.flaggedWindows)} flagged. ${number(c.excludedRecords)} extraction-excluded records, ${number(c.boundaryRemainderRecordSeconds)} remainder seconds, ${number(c.featureFailureRecordSeconds)} feature-failure seconds. ${number(index.unplacedExcludedRecords)} excluded records have no placeable time. This context's assignment accounting: ${Object.entries(result.windowAccounting).map(([k,v])=>`${k}: ${number(v)}`).join(' · ')||'no computed windows'}.`;
  $('support').replaceChildren(...[['Activities with usable data',result.support.activitiesWithUsableWindows],['Dates with usable data',result.support.usableDays],['Known sessions with usable data',result.support.knownSessions],['Usable minutes',number(result.support.usableMs/60000)]].map(([label,value])=>{const card=document.createElement('article');card.append(text('strong',value),text('span',label));return card;}));
  $('readiness').replaceChildren(...result.readiness.reasons.map(reason=>text('li',reason)));$('method-decision').textContent=index.defaultMethod;
  $('report').hidden=false;draw();examples();
 }catch(e){if(token===sequence){$('status').textContent=e.message;clear('Comparison could not be verified. Refresh to retry.');}}
}
function name(s){return `${s.activityName} · ${s.source} · ${s.reviewState} · session ${s.sessionId??'not recorded'}`;}
function triple(q){return q?q.map(number).join(' / '):'No usable feature values';}
function draw(){
 if(!data)return;const feature=Number($('feature').value),meta=index.features[feature],strata=new Map(data.strata.map(s=>[s.stratumId,s]));$('days').replaceChildren();
 for(const row of data.rows){const s=strata.get(row.stratumId),tr=document.createElement('tr'),first=document.createElement('td');first.append(text('strong',`${row.date} · ${s.activityName}`),text('small',`${s.source} · ${s.reviewState} · session ${s.sessionId??'not recorded'}`),text('small',`Timing: ${Object.entries(s.timingCounts).map(([k,v])=>`${v} ${k}`).join(', ')} labels in this stratum`));tr.append(first,text('td',`${duration(row.recordedMs)} / ${duration(row.labeledMs)}`),text('td',`${number(row.usableWindows)} usable / ${number(row.flaggedWindows)} flagged`));
  const cell=document.createElement('td'),strip=document.createElement('div');strip.className='coverage-strip';for(const [key,color] of [['usableMs','#258267'],['flaggedMs','#bd9655'],['otherRecordedMs','#b1bdb7'],['missingMs','#eef3f0']]){const part=document.createElement('span');part.style.width=`${row.labeledMs?row[key]/row.labeledMs*100:0}%`;part.style.background=color;part.title=`${key}: ${duration(row[key])}`;strip.append(part);}cell.append(strip,text('small',`${duration(row.usableMs)} usable · ${duration(row.missingMs)} missing`));tr.append(cell,text('td',triple(row.quartiles?.[feature])));$('days').append(tr);
 }
 $('row-count').textContent=`${data.total?offset+1:0}–${offset+data.shown} of ${data.total} daily/activity/origin/session rows under this policy. ${data.support.usableWindows} usable windows across ${data.support.usableDays} dates; these are not independent trials.`;$('previous').disabled=offset===0;$('next').disabled=!data.hasMore;
 $('sessions').replaceChildren();$('session-note').textContent=`Showing ${data.sessionRowsShown} of ${data.sessionRowsTotal} explicit-session activity/origin summaries. ${number(data.support.unknownSessionWindows)} usable windows have no record-linked session ID; dates and gaps are not substituted for sessions.`;
 for(const s of data.sessions){const row=document.createElement('tr');row.append(text('td',name(s)),text('td',`${s.usableDays} dates / ${s.usableWindows} windows / ${duration(s.usableMs)}`),text('td',triple(s.quartiles?.[feature])));$('sessions').append(row);}
 chart(feature,strata);$('chart-note').textContent=`${meta.label} · ${meta.units} · plotted rows are the current ${data.shown} of ${data.total} daily rows. Select a personal activity or page through dates to inspect further support.`;
}
function chart(feature,strata){
 const target=$('daily-chart');target.replaceChildren();$('legend').replaceChildren();const rows=data.rows.filter(r=>r.quartiles),dates=[...new Set(data.rows.map(r=>r.date))].sort(),ids=[...new Set(rows.map(r=>r.stratumId))].sort();
 if(!rows.length){target.append(svg('text',{x:80,y:180},'No usable windows under this policy and page'));return;}
 const qs=rows.flatMap(r=>r.quartiles[feature]),low=feature===1?Math.min(...qs):0,high=Math.max(...qs),range=high-low||1;
 const x=date=>85+(dates.indexOf(date)+.5)/Math.max(1,dates.length)*950,y=value=>325-(value-low)/range*275;
 target.append(svg('line',{x1:75,y1:325,x2:1060,y2:325,stroke:'#9bb4a5'}));
 for(let i=0;i<=4;i++){const value=low+range*i/4,py=y(value);target.append(svg('line',{x1:75,y1:py,x2:1060,y2:py,stroke:'#dce8df'}),svg('text',{x:8,y:py+4},number(value)));}
 const step=Math.max(1,Math.ceil(dates.length/7));dates.forEach((date,i)=>{if(i%step===0||i===dates.length-1)target.append(svg('text',{x:x(date)-40,y:355},date));});
 for(const [i,id] of ids.entries()){const color=colors[i%colors.length],label=text('span',name(strata.get(id))),swatch=document.createElement('i');swatch.style.background=color;label.prepend(swatch);$('legend').append(label);}
 for(const row of rows){const i=ids.indexOf(row.stratumId),px=x(row.date)+(i-(ids.length-1)/2)*Math.min(4,20/Math.max(1,ids.length)),q=row.quartiles[feature],color=colors[i%colors.length],line=svg('line',{x1:px,x2:px,y1:y(q[0]),y2:y(q[2]),stroke:color,'stroke-width':2}),dot=svg('circle',{cx:px,cy:y(q[1]),r:4,fill:color});dot.append(svg('title',{},`${row.date} · ${name(strata.get(row.stratumId))} · median ${number(q[1])} · ${duration(row.usableMs)} usable`));target.append(line,dot);}
}
function examples(){
 $('examples').replaceChildren();$('example-note').textContent=`Examples for ${data.strata.length} strata visible on this page. Ordinary examples use a stable hash order independent of feature ranking; RMS-median and quality-excluded examples are separately identified. Choose Uncertain only above to review uncertain annotations separately.`;
 for(const s of data.strata){const article=document.createElement('article');article.append(text('strong',name(s)));for(const e of s.examples){const button=text('button',`${e.kind} · ${stamp(e.startMs)}`);button.type='button';button.className='button subtle';button.addEventListener('click',()=>wave(e));article.append(button);const link=text('a',`Review activity #${e.labelId} →`);link.href=`/review.html?labelId=${e.labelId}`;article.append(link);}$('examples').append(article);}
}
async function wave(example){
 if(!data)return;const token=++waveSequence,identity=data.comparisonIdentity,group=data.compatibilityId;$('waveform').replaceChildren();$('waveform-note').textContent='Verifying the exact source waveform…';
 try{const w=await request('/api/activity-comparisons/waveform?'+new URLSearchParams({group,window:example.windowId,identity}));if(token!==waveSequence||identity!==data?.comparisonIdentity)return;const span=w.maximum-w.minimum||1;const points=w.samples.map((v,i)=>`${65+i/(w.samples.length-1)*1000},${215-(v-w.minimum)/span*185}`).join(' ');$('waveform').replaceChildren(svg('polyline',{points,fill:'none',stroke:'#258267','stroke-width':1.2}),svg('text',{x:5,y:25},number(w.maximum)),svg('text',{x:5,y:220},number(w.minimum)),svg('text',{x:65,y:245},'0 s'),svg('text',{x:1000,y:245},`${number((w.endMsExclusive-w.startMs)/1000)} s`));$('waveform-note').textContent=`${example.kind} · ${example.activityName} · ${example.source} · ${example.reviewState} · timing ${example.timingPrecision}. Source verified: ${stamp(w.startMs)} → ${stamp(w.endMsExclusive)} (end exclusive), ${w.samples.length} raw samples, ${w.acquisition.placement} ear, ${w.sampleRate} Hz, ${w.acquisition.units}. Flags: ${example.qualityFlags.join(', ')||'none; not proof of artifact-free EEG'}.`;}
 catch(e){if(token===waveSequence)$('waveform-note').textContent=e.message;}
}
for(const id of ['group','review','origin','activity'])$(id).addEventListener('change',()=>{offset=0;load();});$('feature').addEventListener('change',draw);$('previous').addEventListener('click',()=>{offset=Math.max(0,offset-25);load();});$('next').addEventListener('click',()=>{offset+=25;load();});
$('refresh').addEventListener('click',refresh);$('run').addEventListener('click',async()=>{if(busy)return;busy=true;try{await request('/api/analysis/start',{activityComparisons:true,exploration:$('include-exploration').checked,timezone:'America/New_York'});await refresh();}catch(e){$('status').textContent=e.message;}finally{busy=false;}});$('cancel').addEventListener('click',async()=>{try{await request('/api/analysis/cancel',{});await refresh();}catch(e){$('status').textContent=e.message;}});
refresh();setInterval(refresh,3000);
