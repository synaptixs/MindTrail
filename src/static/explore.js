'use strict';
const $=id=>document.getElementById(id), ns='http://www.w3.org/2000/svg';
const palette=['#258267','#5585ba','#bc8846','#9771ae','#bd7275','#5c9594','#8c9444','#797e99'];
let index=null,payload=null,viewSequence=0,waveSequence=0,page=0,loading=false,currentKey=null;
const fmt=new Intl.DateTimeFormat(undefined,{timeZone:'America/New_York',year:'numeric',month:'short',day:'numeric',hour:'numeric',minute:'2-digit',second:'2-digit',timeZoneName:'short'});
const num=v=>v===null||v===undefined?'—':Number(v).toLocaleString(undefined,{maximumFractionDigits:3});
function text(tag,value){const e=document.createElement(tag);e.textContent=value;return e;}
function svg(tag,attrs={},value){const e=document.createElementNS(ns,tag);for(const [k,v] of Object.entries(attrs))e.setAttribute(k,String(v));if(value!==undefined)e.textContent=value;return e;}
async function request(url,body){const r=await fetch(url,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Awear-Local':'1'},body:JSON.stringify(body)});const v=await r.json();if(!r.ok)throw new Error(v.error||'Request failed');return v;}
function candidate(){return payload?.candidates.find(c=>c.candidateId===$('candidate').value)||null;}
function describe(c){const p=c.parameters;return c.method==='hdbscan'?`HDBSCAN · minimum size ${p.min_cluster_size} · samples ${p.min_samples} · single ${p.allow_single_cluster?'allowed':'forbidden'}`:`${c.method==='gmm'?'GMM':'K-means'} · ${p.n_components||p.n_clusters} groups · seed ${p.random_state}`;}
function clearWave(){waveSequence++;$('waveform').replaceChildren();$('waveform-note').textContent='Select an observed window to inspect its recorded samples.';$('eeg-link').hidden=true;}
function hideReport(message){viewSequence++;payload=null;currentKey=null;$('report').hidden=true;$('freshness').textContent=message;clearWave();}
async function refresh(){
  try{
    const [state,snapshot]=await Promise.all([request('/api/analysis/state'),request('/api/exploration')]);
    if(snapshot.participantId!==state.participantId){index=null;hideReport('Selected source changed. Refresh to inspect its exploration.');return;}
    $('participant').textContent=state.demo?'Synthetic demo':state.participantId||'No participant selected';
    $('source').textContent=state.demo?'Synthetic demo · Cloud-format right-ear metadata; not personal EEG measurements.':'Cloud recordings · behind the right ear · RIGHT_TEMP · 256 Hz. Bluetooth captures are not included.';
    const job=state.job,busy=job&&['running','canceling'].includes(job.state);
    $('run').disabled=!!busy||!state.explorationAvailable||!state.participantId;$('cancel').hidden=!busy||!!job.external;
    $('availability').textContent=state.explorationAvailable?'Analysis runs locally in the background. Samples and activities are preserved.':'Feature exploration is unavailable in this build. The source-workspace analysis environment is required; installer support is planned separately.';
    $('status').textContent=busy?`${job.stage}: ${job.total?`${num(job.done)} of ${num(job.total)} steps`:'working…'}`:job&&['failed','canceled','interrupted'].includes(job.state)?job.error||`Analysis ${job.state}. Retry when ready.`:'Ready to inspect saved exploration.';
    if(snapshot.status!=='current'){index=null;hideReport(snapshot.status==='stale'?'Saved source or analysis settings changed. Run analysis to refresh; old projections and waveforms are withheld.':snapshot.status==='disabled'?'The latest analysis did not include exploration. Click Analyze and explore to create it.':'No completed exploration for this source. Click Analyze and explore to begin.');return;}
    const old=index?.explorationIdentity;index=snapshot;$('freshness').textContent='Current completed snapshot · dates shown in America/New_York.';
    if(old!==index.explorationIdentity||!payload){
      const previous=$('group').value;$('group').replaceChildren(...index.groups.map(g=>new Option(`${g.acquisition.device_id} · ${g.acquisition.placement} ear · ${num(g.selectedWindows)} selected`,g.acquisitionGroupId)));
      if(index.groups.some(g=>g.acquisitionGroupId===previous))$('group').value=previous;
      if(index.groups.length)await loadView();else hideReport('No computed acquisition groups in this snapshot.');
    }
  }catch(e){$('status').textContent=e.message;hideReport('Could not verify the current exploration. Refresh to retry.');}
}
async function loadView(){
  const sequence=++viewSequence;const group=$('group').value,view=$('view').value;
  payload=null;$('report').hidden=true;clearWave();
  try{
    const data=await request(`/api/exploration?group=${encodeURIComponent(group)}&view=${view}`);
    if(sequence!==viewSequence)return;
    if(data.status!=='current'||data.explorationIdentity!==index?.explorationIdentity)throw new Error('Exploration changed; refresh to inspect the current snapshot.');
    payload=data;currentKey=data.explorationIdentity;page=0;
    const g=index.groups.find(g=>g.acquisitionGroupId===group),a=g.acquisition;
    $('context').textContent=`${a.source} · ${a.device_id} · ${a.placement} ear · ${a.channel} · ${a.sample_rate} Hz · ${a.units} · ${num(index.groups.length)} acquisition groups kept separate.`;
    $('accounting').textContent=`${num(g.computedWindows)} computed = ${num(g.qualityExcludedWindows)} quality-excluded + ${num(g.spacingOmittedWindows)} spacing-omitted + ${num(g.budgetOmittedWindows)} budget-omitted + ${num(g.selectedWindows)} selected. Source contains ${num(index.snapshot.sourceCoverage.sourceRecords)} recorded seconds, including extraction exclusions and remainders.`;
    $('conditions').textContent=`Session: ${g.recordingConditions.sessionStatus.replaceAll('_',' ')}. Contact impedance is unmeasured. Excluded-day refits evaluated ${g.excludedDayScope.evaluatedDays.length} of ${g.excludedDayScope.availableDays} selected dates; pooled recurrence is not independent replication.`;
    $('candidate').replaceChildren(new Option('Ungrouped — no model selected',''),...data.candidates.map(c=>{const o=new Option(`${describe(c)}${c.status==='fitted'?'':` · ${c.status}`}`,c.candidateId);o.disabled=c.status!=='fitted';return o;}));
    $('feature').replaceChildren(...data.distributions.map((d,i)=>new Option(d.label,String(i))));
    $('report').hidden=false;draw();drawHistogram();
  }catch(e){if(sequence===viewSequence){$('status').textContent=e.message;payload=null;$('report').hidden=true;}}
}
function drawPCA(){
  const target=$('pca');target.replaceChildren();const p=payload.pca,c=candidate();
  $('pca-note').textContent=p.status==='projected'?`PC1 ${num(p.explainedVarianceRatio[0]*100)}%, PC2 ${num(p.explainedVarianceRatio[1]*100)}% of scaled feature variance · rank ${p.rank}. ${p.axisAmbiguity?'Near-equal axes: orientation is not uniquely interpretable.':''}`:`PCA unavailable: ${p.status.replaceAll('_',' ')}. No artificial separation is added.`;
  $('loadings').replaceChildren();
  for(let i=0;i<p.features.length;i++){const row=document.createElement('tr');for(const value of [p.features[i],num(p.components?.[0]?.[i]),num(p.components?.[1]?.[i]),num(p.scaling?.median[i]),num(p.scaling?.iqrScale[i])])row.append(text('td',value));$('loadings').append(row);}
  if(p.status!=='projected'){target.append(svg('text',{x:40,y:200},'Insufficient feature variation for a PCA view'));return;}
  const xs=payload.points.map(p=>p.coordinates[0]),ys=payload.points.map(p=>p.coordinates[1]);
  const minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys);
  const x=v=>60+(v-minX)/(maxX-minX||1)*610,y=v=>360-(v-minY)/(maxY-minY||1)*325;
  target.append(svg('line',{x1:60,y1:360,x2:670,y2:360,stroke:'#a6bdb2'}),svg('line',{x1:60,y1:35,x2:60,y2:360,stroke:'#a6bdb2'}),svg('text',{x:340,y:405},'PC1'),svg('text',{x:10,y:24},'PC2'));
  for(const [value,px,py] of [[minX,60,380],[maxX,620,380],[maxY,5,40],[minY,5,355]])target.append(svg('text',{x:px,y:py},num(value)));
  payload.points.forEach((point,i)=>{const label=c?c.labels[i]:null;const circle=svg('circle',{cx:x(point.coordinates[0]),cy:y(point.coordinates[1]),r:3.5,fill:label===null?'#278767':label<0?'#99a6a0':palette[label%palette.length],'fill-opacity':.65,'data-window':point.windowId});circle.append(svg('title',{},`${fmt.format(point.startMs)} · ${label===null?'Ungrouped':label<0?'Algorithm unassigned':`Group ${label+1}`}`));target.append(circle);});
}
function drawHistogram(){
  $('histogram').replaceChildren();const d=payload?.distributions[Number($('feature').value)];if(!d){$('feature-detail').textContent='No selected feature values.';return;}
  const max=Math.max(...d.counts,1),target=$('histogram');
  d.counts.forEach((count,i)=>{const bar=svg('rect',{x:50+i*24,y:240-count/max*200,width:22,height:count/max*200,fill:'#409373'});bar.append(svg('title',{},`${num(d.edges[i])} to ${num(d.edges[i+1])}: ${num(count)} windows`));target.append(bar);});
  target.append(svg('text',{x:50,y:270},num(d.edges[0])),svg('text',{x:460,y:270},num(d.edges.at(-1))),svg('text',{x:15,y:30},`${num(max)} windows`));
  $('feature-detail').textContent=`${d.label} · ${d.units} · ${num(d.windows)} selected windows · Q1 ${num(d.quartiles[0])}, median ${num(d.quartiles[1])}, Q3 ${num(d.quartiles[2])}.`;
}
function waveformButton(id){const b=text('button','View waveform');b.type='button';b.className='button subtle';b.dataset.window=id;return b;}
function draw(){
  if(!payload)return;page=0;clearWave();const c=candidate();
  $('candidate-detail').textContent=c?`${describe(c)} · ${c.assignment.groupCount} unnamed groups · ${num(c.assignment.unassignedWindows)} of ${num(c.assignment.windows)} algorithm-unassigned. Colors may repeat; group numbers identify groups.`:'No clustering selected. PCA and distributions describe the selected signal features.';
  const aris=(c?.refits||[]).map(r=>r.agreement?.adjustedRandIndex).filter(v=>v!==null&&v!==undefined);
  const ablation=c?.powerAblation?.agreement?.adjustedRandIndex;
  $('robustness').textContent=c?`Refit ARI: ${aris.length?`${num(Math.min(...aris))}–${num(Math.max(...aris))} across ${aris.length} comparable refits`:'no supported nondegenerate comparison'}. Power-ablation ARI: ${num(ablation)}. Shared-training-window agreement is not activity accuracy; groups and dates are exploratory.`:'';
  drawPCA();drawRepresentatives();drawTable();
}
function drawRepresentatives(){
  const c=candidate();$('representatives').replaceChildren();
  const feature=Number($('feature').value),distribution=payload.distributions[feature];
  $('profile-title').textContent=distribution?`${distribution.label} median (Q1–Q3) · ${distribution.units}`:'Feature median (Q1–Q3)';
  const reps=c?[...c.representatives,...(c.unassignedRepresentative?[c.unassignedRepresentative]:[])]:payload.typical?[payload.typical]:[];
  $('representative-note').textContent=c?`Top ${c.representatives.length} of ${c.representativeGroupTotal} groups by selected-window count${c.unassignedRepresentative?', plus an unassigned example':''}. All selected windows remain available in the table.`:payload.typical?'One typical example from all selected windows; no group meaning is assigned.':'No selected unflagged windows; no representative is available.';
  for(const rep of reps){const point=payload.points.find(p=>p.windowId===rep.windowId),row=document.createElement('tr'),q=rep.profileQuartiles[feature];row.append(text('td',rep.name),text('td',num(rep.windows)),text('td',q?`${num(q[1])} (${num(q[0])}–${num(q[2])})`:'—'),text('td',fmt.format(point.startMs)));const cell=document.createElement('td');cell.append(waveformButton(rep.windowId));row.append(cell);$('representatives').append(row);}
}
function drawTable(){
  const c=candidate(),start=page*25,end=Math.min(start+25,payload.points.length);$('windows').replaceChildren();
  payload.points.slice(start,end).forEach((p,i)=>{const label=c?.labels[start+i],row=document.createElement('tr');for(const value of [fmt.format(p.startMs),`${num((p.endMsExclusive-p.startMs)/1000)} s`,label===undefined?'Ungrouped':label<0?'Unassigned':`Group ${label+1}`,num(p.featureValues[6])])row.append(text('td',value));const cell=document.createElement('td');cell.append(waveformButton(p.windowId));row.append(cell);$('windows').append(row);});
  $('window-count').textContent=`${payload.points.length?start+1:0}–${end} of ${num(payload.points.length)} selected windows shown · America/New_York.`;$('previous').disabled=page===0;$('next').disabled=end>=payload.points.length;
}
async function showWaveform(window){
  if(!payload)return;const sequence=++waveSequence,key=currentKey,group=payload.acquisitionGroupId,view=payload.view;
  $('waveform').replaceChildren();$('eeg-link').hidden=true;$('waveform-note').textContent='Verifying exact recorded samples…';
  try{
    const wave=await request(`/api/exploration/waveform?group=${group}&view=${view}&window=${window}&identity=${encodeURIComponent(key)}`);
    if(sequence!==waveSequence||key!==currentKey)return;
    const target=$('waveform'),span=wave.maximum-wave.minimum||1;
    const points=wave.samples.map((v,i)=>`${65+i/(wave.samples.length-1)*1010},${225-(v-wave.minimum)/span*195}`).join(' ');
    target.replaceChildren(svg('polyline',{points,fill:'none',stroke:'#268567','stroke-width':1.3}));
    target.append(svg('text',{x:8,y:25},num(wave.maximum)),svg('text',{x:8,y:230},num(wave.minimum)),svg('text',{x:65,y:265},'0 s'),svg('text',{x:1010,y:265},`${num((wave.samples.length-1)/wave.sampleRate)} s`));
    $('waveform-note').textContent=`Source verified · ${fmt.format(wave.startMs)} → ${fmt.format(wave.endMsExclusive)} (end exclusive) · ${wave.samples.length} raw samples at ${wave.sampleRate} Hz · ${wave.acquisition.placement} ear · ${wave.acquisition.units}. Raw offset retained; mean ${num(wave.mean)}.`;
    $('eeg-link').href=`/eeg.html?start=${encodeURIComponent(new Date(wave.startMs).toISOString())}`;$('eeg-link').hidden=false;
    $('waveform-section').scrollIntoView({behavior:'smooth',block:'start'});
  }catch(e){if(sequence===waveSequence)$('waveform-note').textContent=e.message;}
}
$('run').addEventListener('click',async()=>{if(loading)return;loading=true;$('run').disabled=true;try{await request('/api/analysis/start',{exploration:true,timezone:'America/New_York'});await refresh();}catch(e){$('status').textContent=e.message;}finally{loading=false;}});
$('cancel').addEventListener('click',async()=>{try{await request('/api/analysis/cancel',{});await refresh();}catch(e){$('status').textContent=e.message;}});
$('refresh').addEventListener('click',refresh);$('group').addEventListener('change',loadView);$('view').addEventListener('change',loadView);$('candidate').addEventListener('change',draw);$('feature').addEventListener('change',()=>{drawHistogram();drawRepresentatives();});
$('previous').addEventListener('click',()=>{page--;drawTable();});$('next').addEventListener('click',()=>{page++;drawTable();});
for(const id of ['pca','windows','representatives'])$(id).addEventListener('click',e=>{const id=e.target.closest('[data-window]')?.dataset.window;if(id)showWaveform(id);});
refresh();setInterval(refresh,3000);
