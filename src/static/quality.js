'use strict';
const $=id=>document.getElementById(id);
let latest=null, busy=false, lastStart=0, autoBlocked=false, renderedKey=null, initialized=false;
const number=n=>Number(n).toLocaleString();
async function request(path,body){
  const response=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Awear-Local':'1'},body:JSON.stringify(body)});
  const value=await response.json();if(!response.ok)throw new Error(value.error||'Request failed');return value;
}
function text(tag,value){const e=document.createElement(tag);e.textContent=value;return e;}
function table(id,values){$(id).replaceChildren();for(const [name,count] of Object.entries(values)){const row=document.createElement('tr');row.append(text('td',name.replaceAll('_',' ')),text('td',number(count)));$(id).append(row);}if(!Object.keys(values).length){const row=document.createElement('tr');row.append(text('td','None in this snapshot'));$(id).append(row);}}
function draw(){
  if(!latest?.summary)return;
  const s=latest.summary,day=$('day').value;
  const fmt=new Intl.DateTimeFormat(undefined,{timeZone:s.timezone,month:'short',day:'numeric',hour:'numeric',minute:'2-digit',timeZoneName:'short'});
  const key=ms=>new Intl.DateTimeFormat('en-CA',{timeZone:s.timezone,year:'numeric',month:'2-digit',day:'2-digit'}).format(ms);
  const bins=s.bins.filter(b=>day==='all'||key(b.startMs)===day);
  $('timeline').replaceChildren();
  for(const b of bins){
    const duration=b.endMsExclusive-b.startMs,unflagged=b.computedUnionMs-b.flaggedUnionMs;
    const row=document.createElement('div');row.className='coverage-row';
    row.append(text('small',`${fmt.format(b.startMs)} – ${fmt.format(b.endMsExclusive)}`));
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.classList.add('coverage-bar');svg.setAttribute('viewBox','0 0 1000 22');svg.setAttribute('preserveAspectRatio','none');svg.setAttribute('role','img');
    const description=`${number(unflagged/1000)} seconds computed unflagged, ${number(b.flaggedUnionMs/1000)} flagged, ${number((duration-b.computedUnionMs)/1000)} not analyzed`;
    svg.setAttribute('aria-label',description);const title=document.createElementNS(svg.namespaceURI,'title');title.textContent=description;svg.append(title);
    let x=0;for(const [amount,color] of [[unflagged,'#278c6b'],[b.flaggedUnionMs,'#b77c35'],[duration-b.computedUnionMs,'#e6ece8']]){const rect=document.createElementNS(svg.namespaceURI,'rect');rect.setAttribute('x',String(x));rect.setAttribute('width',String(1000*amount/duration));rect.setAttribute('height','22');rect.setAttribute('fill',color);svg.append(rect);x+=1000*amount/duration;}
    row.append(svg);$('timeline').append(row);
  }
  if(!bins.length)$('timeline').append(text('p','No timestamped records in this snapshot.'));
  $('time-total').textContent=`${number(bins.length)} of ${number(s.bins.length)} periods shown · ${s.timezone}. Bar sections show proportions, not the position of gaps within a period.`;
}
function render(state){
  $('participant').textContent=state.demo?'Synthetic demo':(state.participantId||'No participant selected');
  $('source').textContent=state.demo?'Synthetic demo data · Cloud-format recordings · right-ear metadata. These are not personal EEG measurements.':'Cloud recordings · behind the right ear · RIGHT_TEMP · 256 Hz. Bluetooth captures are not included in this analysis.';
  if(!initialized){if(state.settings){$('normalize').checked=state.settings.normalize;$('exploration').checked=!!state.settings.exploration;const b=state.settings.candidateBoundaries;$('boundaries').checked=!!b;if(b){$('boundary-minimum').value=b.minimumSeconds;$('boundary-sensitivity').value=b.sensitivity;}}initialized=true;}
  latest=state;const job=state.job;busy=!!job&&['running','canceling'].includes(job.state);
  $('start').disabled=busy||!state.available||!state.participantId;$('cancel').hidden=!busy||job?.external;$('cancel').disabled=job?.state==='canceling';
  $('progress').hidden=!busy;if(busy&&job.total>0){$('progress').max=job.total;$('progress').value=job.done;}else $('progress').removeAttribute('value');
  let message=state.available?'Ready to analyze saved recordings.':'Analysis is unavailable in this build.';
  if(job){if(busy)message=`${job.stage}: ${job.total?`${number(job.done)} of ${number(job.total)} ${job.stage==='extracting'?'records':job.stage==='screening'?'acquisition groups':'steps'}`:'starting…'}`;
    else if(job.state==='completed')message=`Analysis completed. ${number(job.reusedWindows)} feature windows reused; ${number(job.computedFeatures)} calculated.`;
    else {autoBlocked=true;message=job.error||`Analysis ${job.state}. Run analysis to retry. Previous complete results are preserved.`;}}
  $('status').textContent=message+(job?.warning?` ${job.warning}`:'');
  const s=state.summary;$('report').hidden=!s;$('download').hidden=!s;
  $('freshness').textContent=s?(state.stale?'Saved recordings or analysis code changed. Showing the previous complete snapshot. Run analysis to update.':'Showing the last complete snapshot.'):'No complete analysis yet.';
  if(!s){renderedKey=null;return;}
  const key=`${s.analysisIdentity}:${s.timezone}:${s.demo}`;
  if(renderedKey===key)return;
  renderedKey=key;
  if(s.demo)$('source').textContent='Synthetic demo data · Cloud-format recordings · right-ear metadata. These are not personal EEG measurements.';
  else $('source').textContent='Cloud recordings · behind the right ear · RIGHT_TEMP · 256 Hz. Bluetooth captures are not included in this analysis.';
  const c=s.coverage;$('metrics').replaceChildren();
  for(const [label,note,count] of [['Source records','Each declares one second',c.sourceRecords],['Computed windows','Four continuous seconds each',c.computedWindows],['Flagged windows','Screening cues, not diagnoses',c.flaggedWindows],['Excluded records','Invalid or ambiguous input',c.excludedRecords],['Incomplete record-seconds','Acquisition or cadence boundaries',c.boundaryRemainderRecordSeconds],['Failed record-seconds','Numeric feature failures',c.featureFailureRecordSeconds],['Computed interval seconds','Union, without double counting',s.computedUnionMs/1000],['Flagged interval seconds','Union of flagged intervals',s.flaggedUnionMs/1000]]){const e=document.createElement('article');e.className='card';e.append(text('span',label),text('strong',number(count)),text('small',note));$('metrics').append(e);}
  table('flags',c.flagOccurrences);table('exclusions',c.excludedByPrimaryReason);
  $('unplaced').textContent=`${number(s.unplacedExcludedRecords)} excluded records have no valid timestamp and cannot be placed on this timeline.`;
  $('references').textContent=`${s.relativeScreeningGroupCount} of ${s.groupCount} acquisition groups have enough windows for relative screening. ${s.spec.normalization==='none'?'Robust deviations are off.':'Robust deviations use unflagged windows in each acquisition group.'}`;
  const selected=$('day').value;const keys=[...new Set(s.bins.map(b=>new Intl.DateTimeFormat('en-CA',{timeZone:s.timezone,year:'numeric',month:'2-digit',day:'2-digit'}).format(b.startMs)))];
  $('day').replaceChildren(new Option('All days','all'),...keys.map(k=>new Option(k,k)));$('day').value=keys.includes(selected)?selected:'all';draw();
}
async function refresh(){try{const state=await request('/api/analysis/state');render(state);if($('auto').checked&&!autoBlocked&&state.available&&state.stale&&!busy&&Date.now()-lastStart>60000)await start();}catch(e){$('status').textContent=e.message;}}
async function start(){autoBlocked=false;lastStart=Date.now();$('start').disabled=true;try{await request('/api/analysis/start',{candidateBoundaries:$('boundaries').checked?{minimumSeconds:Number($('boundary-minimum').value),sensitivity:$('boundary-sensitivity').value}:null,normalize:$('normalize').checked,exploration:$('exploration').checked,timezone:Intl.DateTimeFormat().resolvedOptions().timeZone});await refresh();}catch(e){autoBlocked=true;$('status').textContent=e.message;$('start').disabled=false;}}
$('start').addEventListener('click',start);$('cancel').addEventListener('click',async()=>{autoBlocked=true;$('auto').checked=false;try{await request('/api/analysis/cancel',{});await refresh();}catch(e){$('status').textContent=e.message;}});$('day').addEventListener('change',draw);
$('auto').addEventListener('change',()=>{autoBlocked=false;});
refresh();setInterval(refresh,2000);
