'use strict';
const $=id=>document.getElementById(id),T=MindTrailTime;
let participant='',offset=0,historyOffset=0,items=[],selected=null,generation=0,viewGeneration=0,busy=false,refreshGeneration=0,lookupGeneration=0;
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const stamp=ms=>T.stamp(ms),iso=ms=>new Date(ms).toISOString();
function notice(message){$('notice').textContent=message;}
async function api(path,body){const r=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Awear-Local':'1'},body:JSON.stringify(body)});const value=await r.json();if(!r.ok)throw new Error(value.error||'Request failed');return value;}
function close(){generation++;viewGeneration++;selected=null;$('editor').hidden=true;}
function buttons(disabled){busy=disabled;for(const id of ['save','split','refresh','previous','next','history-previous','history-next'])$(id).disabled=disabled;}
async function load(){
  const token=++refreshGeneration;
  try {
    const status=await api('/api/status');if(token!==refreshGeneration)return;
    if(participant&&participant!==status.participantId){close();offset=historyOffset=0;}
    participant=status.participantId;$('participant').textContent=status.demo?'Synthetic demo':participant||'No participant selected';
    const [q,h,v]=await Promise.all([api('/api/review/queue?'+new URLSearchParams({offset,cues:$('cues').checked?'1':'0'})),api('/api/review/history?offset='+historyOffset),api('/api/activities?limit=100')]);
    if(token!==refreshGeneration||q.participantId!==participant||h.participantId!==participant)return;
    items=q.items;
    $('queue-count').textContent=`Showing ${q.items.length} of ${q.total} eligible review items (offset ${q.offset}). ${q.ordinaryTotal} ordinary; ${q.candidateTotal} optional candidates. ${q.unknownTotal} unknown decisions; ${q.rejectedTotal} rejections. ${q.invalidTimestampRecords} invalid-timestamp records excluded. Candidate snapshot: ${q.candidateStatus}. Items can overlap and are not independent observations.`;
    $('queue').innerHTML=q.items.length?q.items.map((item,i)=>`<article class="review-item">${item.labelId?`<span class="interval-number">Saved interval #${item.labelId}</span>`:''}<strong>${esc(stamp(item.startMs))} → ${esc(stamp(item.endMsExclusive))}</strong><p>${esc(item.reason)}${item.annotation?' · '+esc(item.annotation.label)+' · '+esc(item.annotation.source)+' · '+esc(item.annotation.reviewState):''}</p><small>${(item.recordedMs/1000).toLocaleString()} recorded seconds · ${item.coveragePct.toFixed(1)}% interval coverage${item.kind==='candidate'?' · PELT · search resolution ≤'+item.maxResolutionMs/1000+'s':''}</small><div class="review-actions"><button class="button subtle" data-open="${i}">Review / edit${item.labelId?' / split':''}</button>${item.labelId&&$('cues').checked?`<button class="button subtle" data-action="confirm" data-index="${i}">Confirm existing annotation</button>`:''}<button class="button subtle" data-action="unknown" data-index="${i}">Activity unknown${item.labelId?' (replace annotation)':''}</button>${item.labelId&&!item.rejectable?'':`<button class="button subtle" data-action="rejected" data-index="${i}">${item.labelId?'Reject suggestion':'Skip this review item'}</button>`}</div></article>`).join(''):'<p>No eligible items in this page. Confirmed activities and explicitly unknown spans are retained separately. Use Previous if the queue shrank.</p>';
    $('previous').disabled=busy||offset===0;$('next').disabled=busy||!q.hasMore;
    $('activities').replaceChildren(...v.activities.filter(a=>!a.archived).map(a=>new Option(a.name,a.name)));
    $('history-count').textContent=`Showing ${h.operations.length} of ${h.total} operations (offset ${h.offset}).`;
    $('history').innerHTML=h.operations.map(op=>{const before=JSON.parse(op.before_json),after=JSON.parse(op.after_json);const rows=[...Object.values(after.labels),...Object.values(after.decisions)].filter(Boolean);const fallback=[...Object.values(before.labels),...Object.values(before.decisions)].filter(Boolean);return `<article class="review-item"><strong>Change #${op.id} · ${esc(op.action)}${op.undo_of?' of #'+op.undo_of:''}</strong><p>${(rows.length?rows:fallback).map(row=>esc(row.label||row.kind)+(row.label?' <a href="/review.html?labelId='+row.id+'">Open current interval #'+row.id+'</a>':'')+' · '+esc(stamp(row.start_ms))+' → '+esc(row.end_ms===null?'active':stamp(row.end_ms))).join('<br>')}</p><small>${esc(new Date(op.changed_ms).toLocaleString())}</small><div class="review-actions"><button class="button subtle" data-undo="${op.id}" ${op.undone?'disabled':''}>${op.undone?'Already undone':op.action==='undo'?'Reverse this undo':'Undo operation'}</button></div></article>`;}).join('')||'<p>No operations yet.</p>';
    $('history-previous').disabled=busy||historyOffset===0;$('history-next').disabled=busy||!h.hasMore;
  }catch(e){notice(e.message);}
}
async function waveform(task,token){
  $('preview').replaceChildren();$('preview-note').textContent='Loading the first ten seconds…';
  try {
    const value=await api('/api/eeg/window?'+new URLSearchParams({start:iso(task.startMs),seconds:10}));
    if(viewGeneration!==token||value.participantId!==participant)return;
    const records=value.records.map(r=>{const mean=r.samples.reduce((a,b)=>a+b,0)/Math.max(1,r.samples.length);return {...r,centered:r.samples.map(x=>x-mean)};});
    const max=Math.max(1,...records.flatMap(r=>r.centered.map(Math.abs)));
    const lines=records.map(r=>{const base=Date.parse(r.timestamp)-task.startMs;return `<polyline fill="none" stroke="#278366" stroke-width="1" points="${r.centered.filter((_,i)=>i%4===0).map((v,i)=>`${(base+i*4000/r.centered.length)/10000*800},${75-v/max*65}`).join(' ')}"/>`;}).join('');
    $('preview').innerHTML=lines;$('preview-note').textContent=`First ten seconds of the original queue selection only · ${records.length} source seconds present · centered per second · ±${max.toFixed(2)} device units. Gaps remain blank; this is not the entire period.`;
  }catch(e){if(viewGeneration===token)$('preview-note').textContent=e.message;}
}
async function openTask(task){
  close();const token=generation;selected={...task};$('editor').hidden=false;$('review-form').reset();$('uncertainty').hidden=true;
  $('editor-title').textContent=task.labelId?'Saved interval #'+task.labelId:'Review selected period';
  $('selection').textContent=stamp(task.startMs)+' → '+stamp(task.endMsExclusive);
  $('selection-context').textContent=task.labelId?`Editing interval #${task.labelId}. ${$('cues').checked?'Existing annotation is shown.':'Blind entry: existing name and note are hidden.'}`:'Enter your own activity, or mark it unknown from the queue.';
  for(const [edge,ms] of [['start',task.startMs],['end',task.endMsExclusive],['cut',Math.floor((task.startMs+task.endMsExclusive)/2)]]){$(edge).value=T.input(ms);$(edge+'-offset').value=T.offset(ms);}
  $('split-controls').hidden=!task.labelId;$('eeg').href='/eeg.html?start='+encodeURIComponent(iso(task.startMs));
  waveform(task,viewGeneration);
  if(task.labelId&&$('cues').checked){
    try {const label=task.savedLabel||await api('/api/review/label?id='+task.labelId);if(token!==generation||label.participantId!==participant)return;
      selected.labelVersion=label.labelVersion;$('name').value=label.label;$('note').value=label.note;$('review-state').value=label.reviewState;
    }catch(e){notice(e.message);return;}
  }
  $('editor').scrollIntoView({block:'start'});$('name').focus({preventScroll:true});
}
async function openSavedInterval(value){
  if(busy)return;
  const id=Number(value);
  if(!Number.isSafeInteger(id)||id<1){notice('Enter a positive saved interval number.');return;}
  close();const token=generation,request=++lookupGeneration,person=participant;
  $('open-interval').disabled=true;
  try {
    const label=await api('/api/review/label?id='+id);
    if(request!==lookupGeneration||token!==generation||person!==participant||label.participantId!==person)return;
    if(!label.end)throw new Error('Stop this active period in the Activity log first; splitting needs a finished interval.');
    $('cues').checked=true;await load();
    if(request!==lookupGeneration||token!==generation||person!==participant)return;
    $('interval-number').value=String(id);
    await openTask({labelId:id,labelVersion:label.labelVersion,startMs:Date.parse(label.start),endMsExclusive:Date.parse(label.end),savedLabel:label});
    notice('Opened saved interval #'+id+'. Confirmed intervals are available here even when absent from the pending queue.');
  }catch(e){if(request===lookupGeneration&&token===generation)notice(e.message);}
  finally{if(request===lookupGeneration)$('open-interval').disabled=false;}
}
$('interval-lookup').addEventListener('submit',async event=>{event.preventDefault();await openSavedInterval($('interval-number').value);});
async function resolve(edge){const result=await api('/api/local-time?value='+encodeURIComponent(T.wallValue($(edge).value)));return T.choose(result.choices,$(edge+'-offset').value).ms;}
$('review-form').addEventListener('submit',async event=>{
  event.preventDefault();if(!selected||busy)return;
  const task={...selected},token=generation,person=participant,name=$('name').value.trim(),note=$('note').value;
  if(!name){notice('Enter your own activity name.');$('name').focus();return;}
  buttons(true);
  try {
    const start=await resolve('start'),end=await resolve('end');if(token!==generation||person!==participant)return;
    const evidence={reviewState:$('review-state').value},precision=$('precision').value;
    if(precision!=='keep'){evidence.timingPrecision=precision;evidence.startUncertaintyMs=precision==='exact'?0:precision==='approximate'&&$('start-uncertainty').value!==''?Math.round(Number($('start-uncertainty').value)*1000):null;evidence.endUncertaintyMs=precision==='exact'?0:precision==='approximate'&&$('end-uncertainty').value!==''?Math.round(Number($('end-uncertainty').value)*1000):null;}
    await api(task.labelId?'/api/labels/'+task.labelId+'/edit':'/api/labels',{participantId:person,label:name,note,preserveNote:!!task.labelId&&!$('cues').checked&&!note,start:iso(start),end:iso(end),labelVersion:task.labelVersion,reviewKey:task.key,cues:$('cues').checked,evidence});
    close();notice('Activity saved. Its operation can be undone from history.');await load();
  }catch(e){notice(e.message);}finally{buttons(false);await load();}
});
$('split').addEventListener('click',async()=>{
  if(!selected?.labelId||busy)return;const task={...selected},token=generation,person=participant;buttons(true);
  try {const cut=await resolve('cut');if(token!==generation||person!==participant)return;
    const result=await api('/api/review/split',{participantId:person,labelId:task.labelId,labelVersion:task.labelVersion,cut:iso(cut)});
    close();notice(`Split saved: left #${result.leftId}, right #${result.rightId}. Review/edit either via its label link; undo is in history.`);
  }catch(e){notice(e.message);}finally{buttons(false);await load();}
});
$('queue').addEventListener('click',async event=>{
  const open=event.target.closest('[data-open]');if(open){if(busy)return;await openTask(items[Number(open.dataset.open)]);return;}
  const button=event.target.closest('[data-action]');if(!button||busy)return;
  const task=items[Number(button.dataset.index)];buttons(true);
  try {await api('/api/review/action',{participantId:participant,key:task.key,action:button.dataset.action,cues:$('cues').checked});close();notice('Decision saved. Undo is available in history.');}
  catch(e){notice(e.message);}finally{buttons(false);await load();}
});
$('history').addEventListener('click',async event=>{const button=event.target.closest('[data-undo]');if(!button||busy)return;buttons(true);try{await api('/api/review/undo',{participantId:participant,operationId:Number(button.dataset.undo)});close();notice('Operation reversed; unrelated intervals were preserved.');}catch(e){notice(e.message);}finally{buttons(false);await load();}});
$('precision').addEventListener('change',()=>{$('uncertainty').hidden=$('precision').value!=='approximate';});
for(const edge of ['start','end','cut'])$(edge).addEventListener('input',()=>{generation++;$(edge+'-offset').value='';if(edge!=='cut')$('precision').value='unknown';});
for(const element of $('review-form').querySelectorAll('input,select'))element.addEventListener('input',()=>{generation++;});
$('cancel').addEventListener('click',close);$('refresh').addEventListener('click',()=>{close();load();});
$('cues').addEventListener('change',()=>{close();offset=0;load();});
$('previous').addEventListener('click',()=>{offset=Math.max(0,offset-25);load();});$('next').addEventListener('click',()=>{offset+=25;load();});
$('history-previous').addEventListener('click',()=>{historyOffset=Math.max(0,historyOffset-25);load();});$('history-next').addEventListener('click',()=>{historyOffset+=25;load();});
async function init(){await load();const value=new URLSearchParams(location.search).get('labelId');if(value!==null)await openSavedInterval(value);}
init();
