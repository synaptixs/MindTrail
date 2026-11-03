/* Daily discovery owns presentation only; all statistics and cue facts come from the model. */
'use strict';
(()=>{
 const el=id=>document.getElementById(id),NS='http://www.w3.org/2000/svg';
 let index=null,data=null,seq=0,indexSeq=0,waveSeq=0,loadKey=null,drag=null,pins=new Map(),currentPerson=null,lastStart=0,autoBlocked=false;
 const n=v=>Number(v).toLocaleString(undefined,{maximumFractionDigits:3});
 const stamp=ms=>new Intl.DateTimeFormat(undefined,{timeZone:index?.timezone||'UTC',dateStyle:'medium',timeStyle:'long'}).format(ms);
 function text(tag,value){const e=document.createElement(tag);e.textContent=value;return e;}
 function svg(tag,attributes={},label){const e=document.createElementNS(NS,tag);for(const[k,v]of Object.entries(attributes))e.setAttribute(k,v);if(label!==undefined)e.textContent=label;return e;}
 async function request(path,body){const r=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Awear-Local':'1'},body:JSON.stringify(body)});const v=await r.json();if(!r.ok)throw new Error(v.error||'Request failed');return v;}
 function selected(){return index?.contexts.find(g=>g.acquisitionGroupId===el('daily-group').value);}
 function day(){return selected()?.days.find(d=>d.date===el('daily-date').value);}
 function pinKey(){return `${currentPerson}:${el('daily-group').value}:${el('daily-date').value}`;}
 function range(){return pins.get(pinKey())||day();}
 function hide(message){el('daily-recur').hidden=true;seq++;indexSeq++;waveSeq++;index=null;data=null;loadKey=null;drag=null;el('daily-discovery').hidden=true;el('daily-status').textContent=message;el('daily-wave').replaceChildren();}
 function options(target,items){const previous=target.value;target.replaceChildren(...items.map(([value,label])=>new Option(label,value)));if(items.some(([value])=>value===previous))target.value=previous;}
 function days(){options(el('daily-date'),selected()?.days.map(d=>[d.date,d.date])||[]);}
 async function sync({value,state}){
  if(typeof value.participantId==='string'&&value.participantId!==currentPerson){currentPerson=value.participantId;pins.clear();loadKey=null;}
  const busy=state.job&&['running','canceling'].includes(state.job.state);
  if(state.job&&['failed','canceled','interrupted'].includes(state.job.state))autoBlocked=true;
  if(value.status==='stale'&&el('daily-auto').checked&&!autoBlocked&&!busy&&state.available&&state.summary&&Date.now()-lastStart>60000){lastStart=Date.now();try{await request('/api/analysis/start',state.settings||{timezone:'America/New_York'});}catch(e){autoBlocked=true;el('daily-status').textContent=e.message;}}
  if(value.status!=='current'){hide('Daily features are unavailable until current analysis completes.');return;}
  if(drag)return;
  const token=++indexSeq;
  try{const next=await request('/api/discovery/daily');if(token!==indexSeq)return;
   if(next.status!=='current'||next.participantId!==value.participantId||next.analysisIdentity!==value.analysisIdentity){hide('Run analysis to create current daily features.');return;}
   const changed=next.dailyIdentity!==index?.dailyIdentity;index=next;el('daily-discovery').hidden=false;
   if(changed){options(el('daily-group'),index.contexts.map(g=>[g.acquisitionGroupId,`${g.acquisition.device_id??'unknown device'} · ${g.acquisition.placement} ear · ${g.acquisition.source} · session ${g.acquisition.session_id??'unknown'}`]));days();loadKey=null;}
   if(!day()){data=null;el('daily-body').hidden=true;el('daily-status').textContent='No timestamped source spans. Unplaced exclusions remain in source accounting.';el('daily-features').replaceChildren();return;}
   await load();
  }catch(e){if(token===indexSeq)hide(e.message);}
 }
 async function load(){
  if(!index||!day())return;
  const r=range(),group=el('daily-group').value,date=el('daily-date').value,identity=index.dailyIdentity;
  const key=`${identity}:${group}:${date}:${r.startMs}:${r.endMsExclusive}`;
  if(key===loadKey)return;loadKey=key;const token=++seq;waveSeq++;el('daily-wave').replaceChildren();el('daily-wave-note').textContent='';el('daily-recur').hidden=true;
  try{const v=await request('/api/discovery/daily?'+new URLSearchParams({group,date,startMs:r.startMs,endMs:r.endMsExclusive}));
   if(token!==seq||identity!==index?.dailyIdentity)return;
   if(v.status!=='current'||v.dailyIdentity!==identity){hide('Daily features changed; refresh analysis.');return;}
   data=v;el('daily-body').hidden=false;draw();render();
  }catch(e){if(token===seq){data=null;loadKey=null;el('daily-body').hidden=true;el('daily-features').replaceChildren();el('daily-status').textContent=e.message;}}
 }
 function render(){
  const a=data.acquisition,which=selected().days.findIndex(d=>d.date===data.date)+1;
  el('daily-status').textContent=`Day ${which} of ${selected().days.length} observed dates in this exact context. All eligible windows are used; no labels are required.`;
  el('daily-provenance').textContent=`${a.source} · ${a.placement} ear · ${a.channel??'unknown channel'} · ${a.sample_rate} Hz · ${a.units} · session ${a.session_id??'not recorded'}. Contact unmeasured.`;
  el('daily-range-note').textContent=`${stamp(data.viewStartMs)} → ${stamp(data.viewEndMsExclusive)} (end exclusive). ${pins.has(pinKey())?'Pinned range stays fixed on refresh.':'Full observed day follows new recordings.'} Calendar day: ${(data.calendarEndMsExclusive-data.calendarStartMs)/3600000} elapsed hours.`;
  if(!['daily-start','daily-end'].includes(document.activeElement?.id)){el('daily-start').value=new Date(data.viewStartMs).toISOString();el('daily-end').value=new Date(data.viewEndMsExclusive).toISOString();}
  const s=data.support;el('daily-support').textContent=`Selection: ${s.unflaggedWindows} unflagged + ${s.flaggedWindows} flagged whole windows; ${s.selectionBoundaryWindows} touching windows excluded at selection edges; ${data.dayBoundaryWindowsTouching} day-edge windows touch this day and are not split into features. Missing: ${n((s.elapsedMsByState.missing||0)/1000)} seconds in this view. Points group by 60-second window-start buckets and continuous runs. Flags and gaps break lines.`;
  const human=data.humanContext.filter(h=>h.state!=='unlabeled');el('daily-human-note').textContent=human.length?`Time-based context (first ${Math.min(20,human.length)} of ${human.length} spans): `+human.slice(0,20).map(h=>h.label?`${h.label.activityName} · ${h.label.source} · ${h.label.review_state} · timing ${h.label.timing_precision}`:h.state).join('; '):'Activity context: unassigned. No activity is inferred from these curves.';
  el('daily-distributions').replaceChildren();const triple=q=>q?q.map(n).join(' / '):'No eligible windows';
  for(const [i,f]of index.features.entries()){const row=text('tr','');row.append(text('th',`${f.label} (${f.units})`),text('td',triple(data.dayQuartiles?.[i])),text('td',triple(data.selectionQuartiles?.[i])));el('daily-distributions').append(row);}
  const c=data.changes;el('daily-change-note').textContent=`Full-day screening: ${c.status.replaceAll('_',' ')} · ${c.evaluatedBoundaries} eligible boundaries · ${c.aboveThreshold} above threshold · ${c.suppressed} nearby candidates suppressed. Showing ${c.shown} of ${c.rangeRetained} retained cues whose complete before/after flanks fit this selection (${c.dayRetained} retained in the day).`;
  el('daily-changes').replaceChildren();for(const cue of c.items){const f=index.features[cue.featureIndex],article=text('article','');article.className='change-example';article.append(text('strong',`${stamp(cue.atMs)} · ${f.label}`),text('p',`Before median ${n(cue.beforeQuartiles[cue.featureIndex][1])} → after ${n(cue.afterQuartiles[cue.featureIndex][1])} ${f.units}. Shift/spread score ${n(cue.score)}; denominator ${n(cue.scales[cue.featureIndex])}. Cause unknown.`));const actions=text('div','');actions.className='discovery-actions';for(const [label,id]of [['Inspect before',cue.beforeExample],['Inspect after',cue.afterExample]]){const b=text('button',label);b.type='button';b.className='button subtle';b.addEventListener('click',()=>wave(id));actions.append(b);}article.append(actions);el('daily-changes').append(article);}
  el('daily-ordinary').disabled=!data.examples.ordinary;el('daily-flagged').disabled=!data.examples.flagged;
  options(el('daily-point'),data.points.map(p=>[p.exampleId,`${stamp(p.startMs)} · ${p.count} windows · AC RMS median ${n(p.quartiles[0][1])}`]));el('daily-inspect-bin').disabled=!data.points.length;
 }
 function draw(){
  if(!data)return;const chart=el('daily-features'),w=Math.max(240,chart.clientWidth),L=64,R=w-20,pw=R-L,start=data.viewStartMs,end=data.viewEndMsExclusive;
  chart.setAttribute('viewBox',`0 0 ${w} 452`);chart.replaceChildren(svg('title',{},'Daily window-feature summaries and source lanes; gaps stay missing'));
  const x=t=>L+(t-start)/(end-start)*pw;
  for(const [i,f]of index.features.entries()){
   const top=30+i*102,bottom=top+74;chart.append(svg('text',{x:L+14,y:top-10},`${({acRms:'AC RMS · source u',log10Power1to40Hz:'Log power · log10 u²',alphaShare:'Alpha share · 0–1'})[f.key]}`));
   const all=data.points.flatMap(p=>p.quartiles[i]);let low=all.length?Math.min(...all):0,high=all.length?Math.max(...all):1;if(f.key==='alphaShare'){low=0;high=1;}else{const minimum=f.key==='acRms'?.1*Math.max(Math.abs(low),Math.abs(high),1):.1;if(high-low<minimum){const middle=(high+low)/2;low=middle-minimum/2;high=middle+minimum/2;}if(f.key==='acRms')low=Math.max(0,low);}
   const y=v=>bottom-(v-low)/(high-low)*74;
   for(const fraction of[0,.5,1]){const v=low+(high-low)*fraction;chart.append(svg('line',{x1:L,x2:R,y1:y(v),y2:y(v),stroke:'#dce6df'}),svg('text',{x:0,y:y(v)+4},n(v)));}
   let previous=null;for(const p of data.points){const px=x((p.startMs+p.endMsExclusive)/2),py=y(p.quartiles[i][1]);if(previous&&previous.run===p.run&&previous.endMsExclusive===p.startMs)chart.append(svg('line',{x1:x((previous.startMs+previous.endMsExclusive)/2),y1:y(previous.quartiles[i][1]),x2:px,y2:py,stroke:'#278366','stroke-width':2}));const dot=svg('circle',{cx:px,cy:py,r:3,fill:'#278366'});dot.append(svg('title',{},`${stamp(p.startMs)} → ${stamp(p.endMsExclusive)}; median ${n(p.quartiles[i][1])}; ${p.count} whole windows`));chart.append(dot);previous=p;}
   for(const cue of data.changes.items)if(cue.featureIndex===i)chart.append(svg('line',{x1:x(cue.atMs),x2:x(cue.atMs),y1:top,y2:bottom,stroke:'#a77532','stroke-dasharray':'3 3'}));
   if(!data.points.length)chart.append(svg('text',{x:L+5,y:top+40},'No eligible feature windows'));
  }
  const colors={missing:'#e2e8e4',recorded:'#278366',computed_unflagged:'#278366',computed_flagged:'#b38036',excluded:'#a44d46',feature_failure:'#a44d46',remainder:'#b7c5ba',labeled:'#b8d8b6',unlabeled:'#e2e8e4',unknown:'#9fabbf',ambiguous:'#c3a3b5'};
  const recording=data.quality.map(r=>({...r,state:r.state==='missing'?'missing':'recorded'}));
  for(const [i,[name,runs]]of [['Recorded',recording],['Quality',data.quality],['Context',data.humanContext]].entries()){
   const y=340+i*24;chart.append(svg('text',{x:0,y:y+12},name));chart.append(svg('rect',{x:L,y,width:pw,height:16,fill:colors.missing}));
   for(const r of runs){const rect=svg('rect',{x:x(r.startMs),y,width:Math.max(0,x(r.endMsExclusive)-x(r.startMs)),height:16,fill:r.state==='labeled'?({confirmed:'#80b58a',uncertain:'#d1c08d',needs_review:'#e1e6cc'}[r.label?.review_state]||colors.labeled):colors[r.state]||colors.unlabeled});rect.append(svg('title',{},`${r.label?`${r.label.activityName} · ${r.label.source} · ${r.label.review_state} · timing ${r.label.timing_precision}`:r.state} · ${stamp(r.startMs)} → ${stamp(r.endMsExclusive)}`));chart.append(rect);}
  }
  for(const fraction of(w<500?[0,1]:[0,.5,1])){const t=start+(end-start)*fraction,label=new Intl.DateTimeFormat(undefined,{timeZone:index.timezone,hour:'numeric',minute:'2-digit',second:'2-digit'}).format(t);chart.append(svg('text',{x:L+fraction*pw,y:435,'text-anchor':fraction===0?'start':fraction===1?'end':'middle'},label));}
  const chosen=drag||{startMs:start,endMsExclusive:end};if(drag)chart.append(svg('rect',{x:x(chosen.startMs),y:22,width:x(chosen.endMsExclusive)-x(chosen.startMs),height:389,fill:'#278366',opacity:.1,'pointer-events':'none'}));
  for(const[edge,id]of[['startMs','daily-left'],['endMsExclusive','daily-right']]){const button=el(id);button.style.left=x(chosen[edge])+'px';button.setAttribute('aria-valuemin',start);button.setAttribute('aria-valuemax',end);button.setAttribute('aria-valuenow',chosen[edge]);button.setAttribute('aria-valuetext',stamp(chosen[edge]));}
 }
 function apply(start,end){if(!data||end-start<4000||start<data.calendarStartMs||end>data.calendarEndMsExclusive){el('daily-status').textContent='Choose at least four seconds within this calendar day.';return;}pins.set(pinKey(),{startMs:start,endMsExclusive:end});drag=null;loadKey=null;load();}
 for(const[id,edge]of[['daily-left','startMs'],['daily-right','endMsExclusive']]){
  const button=el(id);button.addEventListener('pointerdown',e=>{if(!data)return;drag={startMs:data.viewStartMs,endMsExclusive:data.viewEndMsExclusive,edge};button.setPointerCapture(e.pointerId);e.preventDefault();});
  button.addEventListener('pointermove',e=>{if(!drag||!button.hasPointerCapture(e.pointerId))return;const rect=el('daily-features').getBoundingClientRect(),fraction=Math.max(0,Math.min(1,(e.clientX-rect.left-64)/(rect.width-84))),ms=Math.round(data.viewStartMs+fraction*(data.viewEndMsExclusive-data.viewStartMs));drag[edge]=edge==='startMs'?Math.min(ms,drag.endMsExclusive-4000):Math.max(ms,drag.startMs+4000);draw();});
  button.addEventListener('pointerup',e=>{if(!drag||!button.hasPointerCapture(e.pointerId))return;button.releasePointerCapture(e.pointerId);const r=drag;apply(r.startMs,r.endMsExclusive);});
  button.addEventListener('pointercancel',()=>{drag=null;draw();});
  button.addEventListener('keydown',e=>{if(!data)return;if(e.key==='Escape'){drag=null;draw();return;}if(e.key==='Enter'&&drag){e.preventDefault();apply(drag.startMs,drag.endMsExclusive);return;}if(!['ArrowLeft','ArrowRight'].includes(e.key))return;e.preventDefault();drag=drag||{startMs:data.viewStartMs,endMsExclusive:data.viewEndMsExclusive,edge};const step=e.shiftKey?1000:Math.max(1000,Math.round((data.viewEndMsExclusive-data.viewStartMs)/100));drag[edge]+=e.key==='ArrowLeft'?-step:step;drag.startMs=Math.max(data.viewStartMs,Math.min(drag.startMs,drag.endMsExclusive-4000));drag.endMsExclusive=Math.min(data.viewEndMsExclusive,Math.max(drag.endMsExclusive,drag.startMs+4000));draw();});
 }
 function parse(value){if(!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?(?:Z|[+-]\d{2}:\d{2})$/.test(value))throw new Error('Enter a full ISO timestamp including seconds and an explicit UTC offset.');const ms=Date.parse(value),wall=Date.parse(value.slice(0,19)+'Z');if(!Number.isFinite(ms)||!Number.isFinite(wall)||new Date(wall).toISOString().slice(0,19)!==value.slice(0,19))throw new Error('Invalid timestamp');return ms;}
 el('daily-apply').addEventListener('click',()=>{try{apply(parse(el('daily-start').value),parse(el('daily-end').value));}catch(e){el('daily-status').textContent=e.message;}});
 el('daily-reset').addEventListener('click',()=>{pins.delete(pinKey());drag=null;loadKey=null;load();});
 for(const id of['daily-group','daily-date'])el(id).addEventListener('change',()=>{if(id==='daily-group')days();data=null;drag=null;loadKey=null;load();});
 el('daily-auto').addEventListener('change',()=>{autoBlocked=false;});el('run').addEventListener('click',()=>{autoBlocked=false;lastStart=Date.now();});
 el('daily-ordinary').addEventListener('click',()=>wave(data?.examples.ordinary));el('daily-flagged').addEventListener('click',()=>wave(data?.examples.flagged));el('daily-inspect-bin').addEventListener('click',()=>wave(el('daily-point').value));
 async function wave(id){if(!id||!data)return;const token=++waveSeq,identity=index.dailyIdentity;el('daily-wave').replaceChildren();el('daily-wave-note').textContent='Verifying exact source samples…';el('daily-recur').hidden=true;
  try{const result=await request('/api/discovery/daily/waveform?'+new URLSearchParams({group:data.acquisitionGroupId,date:data.date,window:id,identity}));if(token!==waveSeq||identity!==index?.dailyIdentity)return;
   const target=el('daily-wave'),w=Math.max(240,target.clientWidth);target.setAttribute('viewBox',`0 0 ${w} 220`);const span=result.maximum-result.minimum||1;target.replaceChildren(svg('polyline',{points:result.samples.map((v,i)=>`${55+i/(result.samples.length-1)*(w-70)},${180-(v-result.minimum)/span*150}`).join(' '),fill:'none',stroke:'#278366','stroke-width':1}),svg('text',{x:0,y:25},n(result.maximum)),svg('text',{x:0,y:185},n(result.minimum)),svg('text',{x:55,y:210},'0 s'),svg('text',{x:w-15,y:210,'text-anchor':'end'},`${(result.endMsExclusive-result.startMs)/1000} s`));
   el('daily-recur').hidden=!!result.qualityFlags.length;el('daily-recur').href='/recurrence.html?'+new URLSearchParams({exactGroup:data.acquisitionGroupId,window:id});
   el('daily-wave-note').textContent=`Source verified · ${stamp(result.startMs)} → ${stamp(result.endMsExclusive)} · ${result.samples.length} samples at ${result.sampleRate} Hz · ${result.acquisition.units}. Raw, unfiltered; mean/offset ${n(result.mean)}. Flags: ${result.qualityFlags.join(', ')||'none; not proof of clean EEG'}.`;
  }catch(e){if(token===waveSeq)el('daily-wave-note').textContent=e.message;}
 }
 new ResizeObserver(()=>draw()).observe(el('daily-features'));
 window.addEventListener('mindtrail-discovery',event=>sync(event.detail));
})();
