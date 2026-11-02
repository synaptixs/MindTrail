const $ = (id) => document.getElementById(id);
const state = { rangeHours: 168, mode: 'live', selected: '', refreshing: false, showSetup: false,
  editingLabel: null, editingInputs: null, pendingLabelRange: null,
  dayRanges: new Map(), dayViews: new Map(), dayDragging: null, dayRequest: 0,
  context: null, focus: null, rangeVersion: 0, refreshQueued: false, summary: null, view: null, dragging: null,
  intervals: [], days: [], expandedDays: new Set(), intervalSignature: '', nextBefore: null, intervalPages: 1, intervalLoading: false, intervalsFetchedAt: 0 };

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function localInput(date) {
  const shifted = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return shifted.toISOString().slice(0, 16);
}
function localStamp(iso) {
  return iso ? new Date(iso).toLocaleString([], {month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}) : '—';
}
function age(iso) {
  if (!iso) return 'Waiting for data';
  const seconds = Math.max(0, Math.floor((Date.now() - Date.parse(iso)) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds/60)}m ago`;
  return `${Math.floor(seconds/3600)}h ago`;
}
function duration(seconds) {
  if (!seconds) return '0 sec';
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return hours ? `${hours}h ${minutes}m` : minutes ? `${minutes}m` : `${seconds}s`;
}
function notice(message, kind = '') {
  const el = $('notice');
  el.textContent = message;
  el.className = `notice ${kind}`;
  el.hidden = !message;
}
async function api(path, options = {}) {
  const response = await fetch(path, {cache:'no-store', ...options});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
  return result;
}
async function post(path, body = {}) {
  return api(path, {method:'POST', headers:{'Content-Type':'application/json','X-Awear-Local':'1'}, body:JSON.stringify(body)});
}
function windowRange() {
  if (!state.focus) {
    const end = Math.floor(Date.now()/1000)*1000;
    state.context = {start:end-state.rangeHours*3600000, end};
  }
  const range=state.focus || state.context;
  return {start:new Date(range.start).toISOString(), end:new Date(range.end).toISOString()};
}

function preciseStamp(ms) {
  return new Date(ms).toLocaleString([], {year:'numeric',month:'short',day:'numeric',hour:'numeric',minute:'2-digit',second:'2-digit'});
}
function preciseInput(ms) {
  const date=new Date(ms);
  return new Date(ms-date.getTimezoneOffset()*60000).toISOString().slice(0,19);
}
function renderFocus(force=false) {
  if (!state.context) return;
  const bounds=state.context, range=state.focus || bounds;
  for (const edge of ['start','end']) {
    const input=$(`focus-${edge}-time`), handle=$(`focus-${edge}-handle`);
    if (force || document.activeElement!==input) input.value=preciseInput(range[edge]);
    input.min=preciseInput(bounds.start+(edge==='end'?60000:0));
    input.max=preciseInput(bounds.end-(edge==='start'?60000:0));
    handle.setAttribute('aria-valuemin',String(((edge==='start'?bounds.start:range.start+60000)-bounds.start)/1000));
    handle.setAttribute('aria-valuemax',String(((edge==='start'?range.end-60000:bounds.end)-bounds.start)/1000));
    handle.setAttribute('aria-valuenow',String((range[edge]-bounds.start)/1000));
    handle.setAttribute('aria-valuetext',preciseStamp(range[edge]));
  }
  const timezone=Intl.DateTimeFormat().resolvedOptions().timeZone;
  $('focus-context').textContent=`${timezone} · drag a vertical handle on the graph, then release to zoom · minimum 1 minute.`;
  $('chart-start').textContent='Start '+preciseStamp(range.start);
  $('chart-end').textContent='End '+preciseStamp(range.end);
  $('focus-duration').textContent=state.focus ? `${duration(Math.floor((range.end-range.start)/1000))} selected · time range pinned` : `Full ${state.rangeHours===168?'seven-day':state.rangeHours+'-hour'} window · follows latest time`;
  $('reset-focus').disabled=!state.focus;
  $('reset-focus').textContent=state.rangeHours===168?'Show full 7 days':`Show full ${state.rangeHours} hours`;
  $('focus-eeg').href='/eeg.html?'+new URLSearchParams({start:new Date(range.start).toISOString()});
  renderBrush();
}
function brushGeometry() {
  const actual=$('trend-chart').clientWidth||920;
  const width=Math.max(280,actual);
  return {width,left:42,right:width-14,span:width-56,pointerSpan:(width-56)*actual/width};
}
function renderBrush() {
  if (!state.context) return;
  const g=brushGeometry(), range=state.focus || state.context;
  const domain=state.dragging?.domain || state.view || state.context;
  const x=time=>g.left+Math.max(0,Math.min(1,(time-domain.start)/(domain.end-domain.start)))*g.span;
  const start=x(range.start), end=x(range.end);
  $('range-brush').setAttribute('viewBox',`0 0 ${g.width} 230`);
  $('focus-start-handle').setAttribute('transform',`translate(${start} 0)`);
  $('focus-end-handle').setAttribute('transform',`translate(${end} 0)`);
  for (const [id,left,width] of [['brush-before',g.left,start-g.left],['brush-selected',start,end-start],['brush-after',end,g.right-end]]) {
    $(id).setAttribute('x',left);$(id).setAttribute('width',Math.max(0,width));
  }
}
let focusTimer;
function selectFocus(edge, value, defer=false) {
  if (!state.context || !Number.isFinite(value)) return;
  const bounds=state.context, range={...(state.focus || bounds)};
  const rounded=Math.floor(value/1000)*1000;
  range[edge]=edge==='start' ? Math.max(bounds.start,Math.min(rounded,range.end-60000)) : Math.min(bounds.end,Math.max(rounded,range.start+60000));
  state.focus=range;
  state.rangeVersion++;
  renderFocus(true);
  $('focus-loading').hidden=false;
  $('focus-loading').textContent=defer?'Release to load detailed view…':'Updating graph…';
  $('trend-chart').setAttribute('aria-busy','true');
  clearTimeout(focusTimer);
  if (!defer) focusTimer=setTimeout(refresh,180);
}
for (const edge of ['start','end']) {
  $(`focus-${edge}-time`).addEventListener('change',event=>{
    const value=new Date(event.target.value).getTime();
    if (!Number.isFinite(value)) {renderFocus();return;}
    selectFocus(edge,value);
  });
  const handle=$(`focus-${edge}-handle`);
  handle.addEventListener('pointerdown',event=>{
    if (!state.context || (event.pointerType==='mouse' && event.button!==0)) return;
    event.preventDefault();handle.focus();
    const range=state.focus || state.context;
    state.dragging={edge,pointerId:event.pointerId,x:event.clientX,time:range[edge],span:brushGeometry().pointerSpan,domain:{...(state.view || state.context)}};
    clearTimeout(focusTimer);
    handle.setPointerCapture(event.pointerId);
    handle.classList.add('dragging');
  });
  handle.addEventListener('pointermove',event=>{
    const drag=state.dragging;
    if (!drag || drag.edge!==edge || drag.pointerId!==event.pointerId) return;
    const delta=(event.clientX-drag.x)/drag.span*(drag.domain.end-drag.domain.start);
    selectFocus(edge,drag.time+delta,true);
  });
  const finish=event=>{
    const drag=state.dragging;
    if (!drag || drag.edge!==edge || drag.pointerId!==event.pointerId) return;
    state.dragging=null;handle.classList.remove('dragging');
    if (handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId);
    $('focus-loading').textContent='Updating graph…';
    refresh();
  };
  handle.addEventListener('pointerup',finish);
  handle.addEventListener('pointercancel',finish);
  handle.addEventListener('lostpointercapture',finish);
  handle.addEventListener('keydown',event=>{
    if (!state.context) return;
    const range=state.focus || state.context, step=event.shiftKey?60000:1000;
    const moves={ArrowLeft:-step,ArrowRight:step,ArrowDown:-step,ArrowUp:step,PageDown:-3600000,PageUp:3600000};
    let value;
    if (event.key in moves) value=range[edge]+moves[event.key];
    else if (event.key==='Home') value=edge==='start'?state.context.start:range.start+60000;
    else if (event.key==='End') value=edge==='start'?range.end-60000:state.context.end;
    else return;
    event.preventDefault();selectFocus(edge,value);
  });
}
$('reset-focus').addEventListener('click',()=>{
  clearTimeout(focusTimer);
  state.focus=null;
  state.rangeVersion++;
  windowRange();renderFocus();
  $('focus-loading').hidden=false;
  $('trend-chart').setAttribute('aria-busy','true');
  refresh();
});

async function loadParticipants() {
  const select = $('participant-select');
  try {
    const result = await api('/api/participants');
    select.innerHTML = '<option value="">Choose a participant ID</option>' + result.members.map(m =>
      `<option value="${escapeHtml(m.participantId)}">${escapeHtml(m.participantId)} · ${escapeHtml(m.accessState || m.status || 'available')}</option>`).join('');
    if (result.members.length === 1) select.value = result.members[0].participantId;
  } catch (error) {
    select.innerHTML = `<option value="">${escapeHtml(error.message)}</option>`;
  }
}

function chartSvg(series, labels, start, end, bucketMs) {
  if (!series.length) return '<div class="chart-empty">No recordings in this time window yet. Sync or choose another range.</div>';
  const W=Math.max(280,$('trend-chart').clientWidth||920),H=230,L=42,R=14,T=12,B=26;
  const span = Date.parse(end)-Date.parse(start);
  const maxY = Math.max(1, ...series.map(p=>p.acRms||0))*1.2;
  const x = (t) => L + Math.max(0,Math.min(1,(t-Date.parse(start))/span))*(W-L-R);
  const y = (v) => T+(1-v/maxY)*(H-T-B);
  const lines=[]; let segment=[]; let last=null;
  for (const point of series) {
    const t = Date.parse(point.time);
    if (last !== null && t-last > bucketMs*1.5 && segment.length) {lines.push(segment.join(' ')); segment=[];}
    segment.push(`${x(t).toFixed(1)},${y(point.acRms||0).toFixed(1)}`); last=t;
  }
  if (segment.length) lines.push(segment.join(' '));
  const shade = labels.map((label, i) => {
    const left=x(Math.max(Date.parse(label.start),Date.parse(start)));
    const right=x(Math.min(Date.parse(label.end||end),Date.parse(end)));
    if (right<=left) return '';
    return `<rect x="${left.toFixed(1)}" y="${T}" width="${(right-left).toFixed(1)}" height="${H-T-B}" fill="${i%2 ? '#cde4f0':'#def1d6'}" opacity=".55"><title>${label.source==='schedule'&&!label.reviewed?'Suggested: ':''}${escapeHtml(label.label)}</title></rect>`;
  }).join('');
  const guides=[0,.25,.5,.75,1].map(v=>`<g><line x1="${L}" x2="${W-R}" y1="${y(maxY*v)}" y2="${y(maxY*v)}" stroke="#e9f0e9"/><text x="${L-9}" y="${y(maxY*v)+3}" text-anchor="end" fill="#9baaa1" font-size="9">${(maxY*v).toFixed(0)}</text></g>`).join('');
  const paths=lines.map(p=>`<polyline points="${p}" fill="none" stroke="#268d6c" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>`).join('');
  const points=series.map(point=>{
    const t=Date.parse(point.time), isolated=!series.some(other=>other!==point&&Math.abs(Date.parse(other.time)-t)<=bucketMs*1.5);
    return `<circle class="trend-point${isolated?' isolated':''}" cx="${x(t).toFixed(1)}" cy="${y(point.acRms||0).toFixed(1)}" r="${isolated?3:6}"><title>${escapeHtml(preciseStamp(Math.max(t,Date.parse(start))))} → ${escapeHtml(preciseStamp(Math.min(t+bucketMs,Date.parse(end))))} · AC RMS ${(point.acRms||0).toFixed(1)} · ${point.records} records</title></circle>`;
  }).join('');
  const ticks=[0,.25,.5,.75,1].map((fraction,index)=>{
    const t=Date.parse(start)+fraction*span;
    const stamp=new Date(t).toLocaleString([],span>36*3600000?{month:'short',day:'numeric'}:{hour:'numeric',minute:'2-digit'});
    return `<text x="${x(t)}" y="${H-6}" text-anchor="${index===0?'start':index===4?'end':'middle'}" fill="#859b90" font-size="9">${escapeHtml(stamp)}</text>`;
  }).join('');
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true"><defs><linearGradient id="chart-fade" x1="0" x2="0" y1="0" y2="1"><stop stop-color="#f2faf4"/><stop offset="1" stop-color="#fff"/></linearGradient></defs><rect x="${L}" y="${T}" width="${W-L-R}" height="${H-T-B}" fill="url(#chart-fade)"/>${guides}${shade}${paths}${points}${ticks}<line x1="${L}" y1="${H-B}" x2="${W-R}" y2="${H-B}" stroke="#dfe9e2"/></svg>`;
}

function waveformSvg(waveform) {
  if (!waveform) return '<div class="chart-empty">Your latest waveform will appear here.</div>';
  const values=waveform.samples;
  const W=680,H=110,P=8;
  const mean=values.reduce((sum,v)=>sum+v,0)/values.length;
  const max=Math.max(1,...values.map(v=>Math.abs(v-mean)))*1.1;
  const points=values.map((v,i)=>`${(P+i/(values.length-1)*(W-2*P)).toFixed(1)},${(H/2-(v-mean)/max*(H/2-P)).toFixed(1)}`).join(' ');
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true"><line x1="0" x2="${W}" y1="${H/2}" y2="${H/2}" stroke="#deebe2" stroke-dasharray="4 5"/><polyline points="${points}" fill="none" stroke="#378e73" stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg>`;
}

function renderActiveLabel(labels) {
  const active=labels.find(l=>!l.end);
  $('active-activity').hidden=!active;
  if (active) $('active-activity').innerHTML=`<strong>● ${escapeHtml(active.label)} is active <button type="button" data-edit="${active.id}" aria-label="Edit ${escapeHtml(active.label)} activity">Edit</button></strong>Started ${escapeHtml(localStamp(active.start))}<br><button type="button" data-stop="${active.id}">Stop activity →</button>`;
}
function daySelection(day) {
  return state.dayRanges.get(day.date)||{start:Date.parse(day.start),end:Date.parse(day.end)};
}
function daySelectionStatus(day,range) {
  const overlaps=day.labels.filter(label=>Date.parse(label.start)<range.end&&(!label.end||Date.parse(label.end)>range.start));
  const protectedLabels=overlaps.filter(label=>label.source!=='schedule'||label.reviewed);
  return {blocked:protectedLabels.length>0,message:protectedLabels.length?
    `Overlaps confirmed activity: ${[...new Set(protectedLabels.map(label=>label.label))].join(', ')}. Narrow the selection or edit the existing label.`:
    overlaps.length?'Saving this activity will retain the suggested work periods around it.':'Assign an activity to this exact time range.'};
}
function dayStamp(day,value) {
  return new Date(value).toLocaleString([],{timeZone:day.timezone,month:'short',day:'numeric',hour:'numeric',minute:'2-digit',second:'2-digit'});
}
function dayBrush(day,plot,width) {
  const range=daySelection(day),start=Date.parse(plot.start),end=Date.parse(plot.end);
  const x=value=>4+Math.max(0,Math.min(1,(value-start)/(end-start)))*(width-8);
  const left=x(range.start),right=x(range.end),minimum=Math.min(1000,Date.parse(day.end)-Date.parse(day.start));
  const handles=['start','end'].map(edge=>`<g class="brush-handle day-handle" data-day="${day.date}" data-day-handle="${edge}" transform="translate(${edge==='start'?left:right} 0)" role="slider" tabindex="0" aria-label="${day.date} ${edge} time handle" aria-orientation="horizontal" aria-valuemin="${(edge==='start'?Date.parse(day.start):range.start+minimum)/1000}" aria-valuemax="${(edge==='start'?range.end-minimum:Date.parse(day.end))/1000}" aria-valuenow="${range[edge]/1000}" aria-valuetext="${escapeHtml(dayStamp(day,range[edge]))}"><title>Drag ${edge} time, then release to zoom</title><rect class="brush-hit" x="-18" y="5" width="36" height="151"/><line class="brush-line" x1="0" x2="0" y1="8" y2="154"/><rect class="brush-grip" x="-7" y="57" width="14" height="29" rx="4"/><path class="brush-grip-mark" d="M-2 65v13 M2 65v13"/></g>`).join('');
  return `<svg class="day-range-brush" preserveAspectRatio="none" viewBox="0 0 ${width} 190" aria-label="Select a period on ${day.date}"><rect class="brush-dim day-before" x="4" y="8" width="${left-4}" height="146"/><rect class="brush-selected day-selected" x="${left}" y="8" width="${Math.max(0,right-left)}" height="146"/><rect class="brush-dim day-after" x="${right}" y="8" width="${width-4-right}" height="146"/>${handles}</svg>`;
}
function dailyPreview(period) {
  const pinned=state.dayRanges.has(period.date),view=state.dayViews.get(period.date);
  const plot=pinned&&view?.data?view.data:period;
  const preview=plot.preview,range=daySelection(period),selection=daySelectionStatus(period,range);
  if (!preview) return '<div class="preview-unavailable">Restart this dashboard server to enable amplitude previews.</div>';
  const start=Date.parse(plot.start), end=Date.parse(plot.end), W=Math.max(280,$('intervals-list').clientWidth-20), L=4, R=W-4;
  const points=preview.segments.flat();
  const max=points.reduce((max,point)=>Math.max(max,point.acRms),1)*1.1;
  const x=stamp=>L+(Math.max(start,Math.min(end,Date.parse(stamp)))-start)/(end-start)*(R-L);
  const y=value=>130-value/max*120;
  const shade=plot.labels.map(label=>{
    const left=x(label.start), right=x(label.end||plot.end);
    return `<rect x="${left}" y="8" width="${Math.max(0,right-left)}" height="122" fill="#e4f2dd" opacity="${label.source==='schedule'&&!label.reviewed?'.45':'.8'}"><title>${escapeHtml(label.label)}${label.source==='schedule'&&!label.reviewed?' (suggested)':''}</title></rect>`;
  }).join('');
  const lines=preview.segments.map(segment=>`<polyline points="${segment.map(point=>`${(x(point.start)+x(point.end))/2},${y(point.acRms)}`).join(' ')}" fill="none" stroke="#278366" stroke-width="1.7" vector-effect="non-scaling-stroke"/>`).join('');
  const coverage=preview.coverage.map(run=>`<rect x="${x(run.start)}" y="146" width="${x(run.end)-x(run.start)}" height="8" fill="#398d70"/>`).join('');
  const hits=points.map(point=>{
    const left=x(point.start), right=x(point.end), middle=(left+right)/2;
    const stamp=value=>new Date(value).toLocaleString([],{timeZone:period.timezone,month:'short',day:'numeric',hour:'numeric',minute:'2-digit',second:'2-digit'});
    const title=`${stamp(point.start)} → ${stamp(point.end)} · AC RMS ${point.acRms.toFixed(2)} device units · ${point.records} sec recorded · Open EEG`;
    const href='/eeg.html?'+new URLSearchParams({start:point.start});
    return `<a href="${escapeHtml(href)}" tabindex="-1" class="preview-point" aria-label="${escapeHtml(title)}"><title>${escapeHtml(title)}</title><rect x="${left}" y="5" width="${Math.max(.1,right-left)}" height="151" fill="transparent"/><circle cx="${middle}" cy="${y(point.acRms)}" r="1.8" fill="#278366"/></a>`;
  }).join('');
  const clock=(stamp,seconds=false)=>new Date(stamp).toLocaleTimeString([],{timeZone:period.timezone,hour:'numeric',minute:'2-digit',...(seconds?{second:'2-digit'}:{})});
  const fractions=W<400?[0,1]:W<600?[0,.5,1]:[0,.25,.5,.75,1];
  const ticks=fractions.map((fraction,index)=>`<text x="${L+fraction*(R-L)}" y="178" text-anchor="${index===0?'start':index===fractions.length-1?'end':'middle'}" font-size="11" fill="#7a9184">${escapeHtml(clock(start+fraction*(end-start),fraction===0||fraction===1))}</text>`).join('');
  const empty=points.length?'':`<text x="${W/2}" y="72" text-anchor="middle" font-size="12" fill="#789080">No EEG records in this selection</text>`;
  const status=view?.loading?'Loading detailed view…':view?.error?view.error:pinned?'Zoomed view · range pinned':'Full day · follows new recordings';
  return `<div class="interval-preview daily-preview" data-day="${period.date}" data-start="${period.start}" data-end="${period.end}" data-view-start="${plot.start}" data-view-end="${plot.end}" data-selected-start="${new Date(range.start).toISOString()}" data-selected-end="${new Date(range.end).toISOString()}"><div class="preview-heading"><span>AC RMS · drag either edge to zoom</span><span>0–${max.toFixed(1)} device units · auto scale</span></div><div class="daily-stage"><svg class="day-signal" viewBox="0 0 ${W} 190" preserveAspectRatio="none" role="group" aria-label="Daily amplitude, ${escapeHtml(clock(plot.start,true))} to ${escapeHtml(clock(plot.end,true))}; ${plot.records} recorded seconds. Hover a point for details, click to open EEG."><rect x="${L}" y="8" width="${R-L}" height="122" fill="#f7faf8"/>${shade}<line x1="${L}" y1="130" x2="${R}" y2="130" stroke="#e2ebe5"/>${lines}${empty}<rect x="${L}" y="146" width="${R-L}" height="8" fill="#e9eeeb"/>${coverage}${hits}${ticks}</svg>${dayBrush(period,plot,W)}</div><div class="preview-key"><span><i></i>Recorded</span><span><i class="missing"></i>Missing</span><span>${preview.bucketMs/1000}s averages · activity shading · ${escapeHtml(period.timezone)}</span></div><div class="day-selection"><strong class="day-selection-times">${escapeHtml(dayStamp(period,range.start))} → ${escapeHtml(dayStamp(period,range.end))}</strong><span class="day-view-status">${escapeHtml(status)}</span></div><div class="day-selection-actions"><button class="button subtle" type="button" data-day-reset="${period.date}" ${pinned?'':'disabled'}>Show entire day</button><button class="button primary" type="button" data-day-assign="${period.date}" ${selection.blocked?'disabled':''}>Assign activity to selection</button></div><p class="day-selection-note">${escapeHtml(selection.message)}</p></div>`;
}
function renderIntervals() {
  if (state.dayDragging) return;
  const list=$('intervals-list'),scroll=list.scrollTop,active=document.activeElement;
  const focusDay=active?.dataset?.day,focusEdge=active?.dataset?.dayHandle;
  const intervals=state.intervals;
  $('interval-count').textContent=`${state.days.length} day${state.days.length===1?'':'s'} · ${intervals.length} half-hour rows loaded${state.nextBefore?' · older available':''}`;
  $('load-older').hidden=!state.nextBefore;
  if (!intervals.length) {
    $('intervals-list').innerHTML='<div class="empty-state">No recorded periods yet. Sync your device data to see them here.</div>';
    return;
  }
  let lastDay='';
  $('intervals-list').innerHTML=intervals.map(period=>{
    const start=new Date(period.start), end=new Date(period.end);
    const key=period.day||period.start.slice(0,10);
    const day=start.toLocaleDateString([], {timeZone:'America/New_York',weekday:'long',month:'short',day:'numeric',year:'numeric'});
    const recordingDay=state.days.find(day=>day.date===key);
    const loaded=intervals.filter(item=>(item.day||item.start.slice(0,10))===key).length;
    const dayHeader=key===lastDay?'':`${lastDay?'</details>':''}<div class="recorded-day-heading"><h3 class="interval-day">${escapeHtml(day)}</h3><span>${recordingDay?`${Number(recordingDay.records).toLocaleString()} sec recorded · entire recorded day`:'Daily graph needs a dashboard server restart'}</span></div>${recordingDay?dailyPreview(recordingDay):''}<details class="daily-intervals" data-day="${key}" ${state.expandedDays.has(key)?'open':''}><summary>${loaded} half-hour activity period${loaded===1?'':'s'} loaded · expand to edit</summary>`;
    lastDay=key;
    const time=(date)=>date.toLocaleTimeString([], {hour:'numeric',minute:'2-digit'});
    const labels=[...new Set(period.labels.map(item=>item.label))];
    const activityLabels=period.labels.map(label=>`<span class="interval-label"><span>${escapeHtml(label.label)}</span><button type="button" class="edit-activity" data-edit="${label.id}" aria-label="Edit ${escapeHtml(label.label)} activity">Edit</button>${label.source==='schedule'&&!label.reviewed?'<span class="label-suggested">Suggested</span>':''}</span>`).join('');
    const status=labels.length?`<div class="interval-labels">${activityLabels}</div>`:
      `<small>${period.complete?'Ready to label':'In progress'}</small>`;
    const suggestionsOnly=period.labels.length&&period.labels.every(l=>l.source==='schedule'&&!l.reviewed);
    const canPick=period.complete&&(!labels.length||suggestionsOnly);
    const rawUrl='/data.html?'+new URLSearchParams({start:period.start,end:period.end});
    const eegUrl='/eeg.html?'+new URLSearchParams({start:period.firstRecord});
    return `${dayHeader}<div class="interval-row" role="listitem"><div class="interval-time"><strong>${escapeHtml(time(start))}–${escapeHtml(time(end))}</strong><small>${escapeHtml(day)}</small></div><div class="interval-records"><strong>${Number(period.records).toLocaleString()} sec recorded</strong><small>${Number(period.coveragePct).toFixed(1)}% of 30 minutes</small>${status}<a class="interval-raw-link" href="${escapeHtml(eegUrl)}">View EEG ↗</a> · <a class="interval-raw-link" href="${escapeHtml(rawUrl)}">View raw records ↗</a></div><div class="interval-side">${canPick||!labels.length?`<button class="button ${canPick?'subtle':'muted'}" type="button" data-pick-start="${escapeHtml(period.start)}" data-pick-end="${escapeHtml(period.end)}" ${canPick?'':'disabled'}>${canPick?(suggestionsOnly?'Add a break / activity':'Assign activity'):'In progress'}</button>`:''}</div></div>`;
  }).join('')+'</details>';
  list.scrollTop=scroll;
  if (focusEdge) list.querySelector(`[data-day="${focusDay}"][data-day-handle="${focusEdge}"]`)?.focus({preventScroll:true});
}

function updateDayBrush(date) {
  const day=state.days.find(day=>day.date===date),el=$('intervals-list').querySelector(`.daily-preview[data-day="${date}"]`);
  if (!day||!el) return;
  const range=daySelection(day),status=daySelectionStatus(day,range),svg=el.querySelector('.day-range-brush');
  const width=svg.viewBox.baseVal.width,start=Date.parse(el.dataset.viewStart),end=Date.parse(el.dataset.viewEnd);
  const x=value=>4+Math.max(0,Math.min(1,(value-start)/(end-start)))*(width-8);
  const left=x(range.start),right=x(range.end),minimum=Math.min(1000,Date.parse(day.end)-Date.parse(day.start));
  for (const edge of ['start','end']) {
    const handle=el.querySelector(`[data-day-handle="${edge}"]`);
    handle.setAttribute('transform',`translate(${edge==='start'?left:right} 0)`);
    handle.setAttribute('aria-valuenow',String(range[edge]/1000));
    handle.setAttribute('aria-valuemin',String((edge==='start'?Date.parse(day.start):range.start+minimum)/1000));
    handle.setAttribute('aria-valuemax',String((edge==='start'?range.end-minimum:Date.parse(day.end))/1000));
    handle.setAttribute('aria-valuetext',dayStamp(day,range[edge]));
  }
  for(const [name,x,width] of [['before',4,left-4],['selected',left,right-left],['after',right,svg.viewBox.baseVal.width-4-right]]) {
    const rect=el.querySelector(`.day-${name}`);rect.setAttribute('x',x);rect.setAttribute('width',Math.max(0,width));
  }
  el.dataset.selectedStart=new Date(range.start).toISOString();el.dataset.selectedEnd=new Date(range.end).toISOString();
  el.querySelector('.day-selection-times').textContent=`${dayStamp(day,range.start)} → ${dayStamp(day,range.end)}`;
  el.querySelector('.day-view-status').textContent=state.dayDragging?'Release to zoom…':'Selection changed · updating view…';
  el.querySelector('[data-day-reset]').disabled=false;
  el.querySelector('[data-day-assign]').disabled=status.blocked;
  el.querySelector('.day-selection-note').textContent=status.message;
}
function selectDayEdge(date,edge,value) {
  const day=state.days.find(day=>day.date===date);if(!day||!Number.isFinite(value))return;
  const lower=Date.parse(day.start),upper=Date.parse(day.end),minimum=Math.min(1000,upper-lower),range={...daySelection(day)};
  const snapped=Math.floor(value/1000)*1000;
  range[edge]=edge==='start'?Math.max(lower,Math.min(snapped,range.end-minimum)):Math.min(upper,Math.max(snapped,range.start+minimum));
  state.dayRanges.set(date,range);updateDayBrush(date);
}
async function loadDayView(date) {
  const day=state.days.find(day=>day.date===date),range=state.dayRanges.get(date);if(!day||!range)return;
  const participant=state.selected,request=++state.dayRequest,source=JSON.stringify(day);
  state.dayViews.set(date,{data:state.dayViews.get(date)?.data,source,loading:true,request});
  renderIntervals();
  try {
    const data=await api('/api/daily-signal?'+new URLSearchParams({date,start:new Date(range.start).toISOString(),end:new Date(range.end).toISOString()}));
    const current=state.dayRanges.get(date);
    if(participant!==state.selected||state.dayViews.get(date)?.request!==request||!current||current.start!==range.start||current.end!==range.end)return;
    state.dayViews.set(date,{data,source,request,loading:false});renderIntervals();
  } catch(error) {
    if(participant!==state.selected||state.dayViews.get(date)?.request!==request)return;
    state.dayViews.set(date,{...state.dayViews.get(date),loading:false,error:'Unable to load detail. Adjust a handle to retry.'});
    renderIntervals();notice(error.message,'error');
  }
}
const dayKeyTimers=new Map();
$('intervals-list').addEventListener('pointerdown',event=>{
  const handle=event.target.closest('[data-day-handle]');
  if(!handle||(event.pointerType==='mouse'&&event.button!==0))return;
  const date=handle.dataset.day,day=state.days.find(day=>day.date===date),el=handle.closest('.daily-preview');if(!day)return;
  event.preventDefault();handle.focus({preventScroll:true});
  clearTimeout(dayKeyTimers.get(date));
  state.dayDragging={date,edge:handle.dataset.dayHandle,pointerId:event.pointerId,x:event.clientX,
    time:daySelection(day)[handle.dataset.dayHandle],start:Date.parse(el.dataset.viewStart),end:Date.parse(el.dataset.viewEnd),
    span:el.querySelector('.daily-stage').getBoundingClientRect().width*(el.querySelector('.day-range-brush').viewBox.baseVal.width-8)/el.querySelector('.day-range-brush').viewBox.baseVal.width};
  handle.setPointerCapture(event.pointerId);handle.classList.add('dragging');
});
$('intervals-list').addEventListener('pointermove',event=>{
  const drag=state.dayDragging;if(!drag||event.pointerId!==drag.pointerId)return;
  selectDayEdge(drag.date,drag.edge,drag.time+(event.clientX-drag.x)/drag.span*(drag.end-drag.start));
});
function finishDayDrag(event) {
  const drag=state.dayDragging;if(!drag||event.pointerId!==drag.pointerId)return;
  state.dayDragging=null;
  const handle=event.target.closest('[data-day-handle]');
  if(handle){handle.classList.remove('dragging');if(handle.hasPointerCapture(event.pointerId))handle.releasePointerCapture(event.pointerId);}
  if(state.dayRanges.has(drag.date))loadDayView(drag.date);else renderIntervals();
  if(state.refreshQueued)refresh();
}
for(const event of ['pointerup','pointercancel','lostpointercapture'])$('intervals-list').addEventListener(event,finishDayDrag);
$('intervals-list').addEventListener('keydown',event=>{
  const handle=event.target.closest('[data-day-handle]');if(!handle)return;
  const date=handle.dataset.day,edge=handle.dataset.dayHandle,day=state.days.find(day=>day.date===date);if(!day)return;
  const range=daySelection(day),minimum=Math.min(1000,Date.parse(day.end)-Date.parse(day.start)),step=event.shiftKey?60000:1000;
  const moves={ArrowLeft:-step,ArrowRight:step,ArrowDown:-step,ArrowUp:step,PageDown:-3600000,PageUp:3600000};
  let value;if(event.key in moves)value=range[edge]+moves[event.key];
  else if(event.key==='Home')value=edge==='start'?Date.parse(day.start):range.start+minimum;
  else if(event.key==='End')value=edge==='start'?range.end-minimum:Date.parse(day.end);
  else return;
  event.preventDefault();selectDayEdge(date,edge,value);clearTimeout(dayKeyTimers.get(date));
  dayKeyTimers.set(date,setTimeout(()=>loadDayView(date),180));
});

async function loadIntervals() {
  if (state.intervalLoading || !state.selected) return;
  state.intervalLoading=true;
  try {
    const all=[];
    const days=new Map();
    const participant=state.selected;
    let before=null;
    let pages=0;
    for (let i=0;i<state.intervalPages;i++) {
      const result=await api('/api/recorded-intervals'+(before?'?before='+encodeURIComponent(before):''));
      all.push(...result.intervals);
      for (const day of result.days||[]) days.set(day.date,day);
      before=result.nextBefore;
      pages++;
      if (!before) break;
    }
    if (participant!==state.selected) return;
    state.intervals=all;
    state.days=[...days.values()];
    state.nextBefore=before;
    state.intervalPages=pages;
    state.intervalsFetchedAt=Date.now();
    const signature=JSON.stringify({intervals:all,days:state.days,nextBefore:before});
    if (signature!==state.intervalSignature) {state.intervalSignature=signature;renderIntervals();}
    for(const day of state.days) {
      const view=state.dayViews.get(day.date);
      if(state.dayRanges.has(day.date)&&view&&!view.loading&&view.source!==JSON.stringify(day))loadDayView(day.date);
    }
    $('recorded-refreshed').textContent=`Checked ${new Date(state.intervalsFetchedAt).toLocaleTimeString([],{timeZone:'America/New_York',hour:'numeric',minute:'2-digit',second:'2-digit'})} · refreshes every 10 seconds`;
  } catch(error) {notice(error.message,'error');}
  finally {state.intervalLoading=false;}
}

function renderSummary(result) {
  if (!result.stats) return;
  state.summary=result;
  state.view={start:Date.parse(result.window.start),end:Date.parse(result.window.end)};
  const s=result.stats;
  $('recorded-time').textContent=duration(s.records);
  $('coverage').textContent=`${Math.min(100,s.coveragePct).toFixed(1)}%`;
  $('mean-rms').textContent=s.acRms==null?'—':s.acRms.toFixed(1);
  $('latest-record').textContent=s.allTimeLast ? new Date(s.allTimeLast).toLocaleTimeString([], {hour:'numeric',minute:'2-digit'}) : '—';
  $('latest-age').textContent=s.allTimeLast ? `${age(s.allTimeLast)} · ${s.allTimeRecords.toLocaleString()} total records` : 'Waiting for data';
  $('trend-chart').innerHTML=chartSvg(result.series,result.labels,result.window.start,result.window.end,result.window.bucketMs);
  $('chart-start').textContent=localStamp(result.window.start);
  $('chart-end').textContent=localStamp(result.window.end);
  $('trend-chart').setAttribute('aria-label',`EEG amplitude from ${preciseStamp(Date.parse(result.window.start))} to ${preciseStamp(Date.parse(result.window.end))}; ${result.stats.records.toLocaleString()} records`);
  $('trend-chart').setAttribute('aria-busy','false');
  $('focus-loading').hidden=true;
  renderFocus();
  $('waveform-chart').innerHTML=waveformSvg(result.waveform);
  $('waveform-time').textContent=result.waveform ? localStamp(result.waveform.time) : 'No data yet';
  renderActiveLabel(result.labels);
  const max=Math.max(1,...result.activities.map(a=>a.acRms||0));
  $('activity-comparison').innerHTML=result.activities.length ? result.activities.map(a=>`<div class="comparison-row${a.unassigned?' unassigned':''}"><div>${escapeHtml(a.label)}<small>${duration(a.records)}${a.unassigned?' · unlabeled':''}</small></div><progress value="${Math.max(2,100*a.acRms/max).toFixed(1)}" max="100"></progress><div class="value">${a.acRms.toFixed(1)}</div></div>`).join('') : '<div class="empty-state">No recordings in the selected time window.</div>';
}

async function refresh() {
  if (state.dragging || state.dayDragging || state.refreshing) {state.refreshQueued=true;return;}
  state.refreshing=true;
  let rangeVersion=state.rangeVersion;
  try {
    const status=await api('/api/status');
    $('desktop-settings').hidden=!status.desktop;
    const bluetoothMode=status.connectionMode==='bluetooth';
    const pill=$('connection-pill');
    pill.classList.toggle('offline',!status.keyConfigured&&!status.demo&&!bluetoothMode);
    pill.innerHTML=`<span class="pill-dot"></span> ${bluetoothMode?'Bluetooth profile':status.demo?'Sample mode':!status.keyConfigured?'Key needed':status.syncing?'Syncing':'Connected'}`;
    $('demo-badge').hidden=!status.demo;
    if (state.selected!==status.participantId) {
      state.intervalPages=1;
      state.intervals=[];
      state.days=[];
      state.expandedDays.clear();
      state.dayRanges.clear();state.dayViews.clear();state.pendingLabelRange=null;
      for(const timer of dayKeyTimers.values())clearTimeout(timer);dayKeyTimers.clear();
      state.intervalSignature='';
      state.intervalsFetchedAt=0;
      state.focus=null;
      state.context=null;
      state.view=null;
      state.rangeVersion++;
    }
    state.selected=status.participantId;
    $('setup').hidden=!!status.participantId&&!state.showSetup;
    $('key-help').hidden=!!status.keyConfigured||status.demo;
    $('connect-button').disabled=!status.keyConfigured&&!status.demo;
    $('sync-button').disabled=bluetoothMode||!status.participantId||status.syncing;
    if (status.lastError) notice(status.lastError,'error');
    else if (status.desktopNotice) notice(status.desktopNotice);
    else if ($('notice').classList.contains('error')) notice('');
    if (status.participantId) {
      const range=windowRange();
      rangeVersion=state.rangeVersion;
      const query=new URLSearchParams(range);
      const result=await api('/api/summary?'+query);
      if (rangeVersion===state.rangeVersion && !state.dragging) {
        renderSummary(result);
        $('raw-export').href='/api/export/raw.jsonl?'+query;
      }
      await loadIntervals();
    }
  } catch (error) {
    notice(error.message,'error');
    $('focus-loading').hidden=true;
    $('trend-chart').setAttribute('aria-busy','false');
  }
  finally {
    state.refreshing=false;
    if (state.refreshQueued || rangeVersion!==state.rangeVersion) {
      state.refreshQueued=false;
      queueMicrotask(refresh);
    }
  }
}

document.querySelectorAll('[data-range]').forEach(button=>button.addEventListener('click',()=>{
  state.rangeHours=Number(button.dataset.range);
  state.view=null;
  state.focus=null;
  state.rangeVersion++;
  clearTimeout(focusTimer);
  windowRange();renderFocus();
  $('focus-loading').hidden=false;
  $('trend-chart').setAttribute('aria-busy','true');
  document.querySelectorAll('[data-range]').forEach(b=>{b.classList.toggle('selected',b===button);b.setAttribute('aria-pressed',String(b===button));});
  refresh();
}));
$('sync-button').addEventListener('click',async()=>{
  try {const result=await post('/api/sync'); notice(result.started?'Sync started. New records will appear shortly.':'A sync is already running.','success'); refresh();}
  catch(error){notice(error.message,'error');}
});
$('source-button').addEventListener('click',()=>{if(!$('desktop-settings').hidden){location.assign('/welcome.html');return;}
  state.showSetup=!state.showSetup;
  $('setup').hidden=!state.showSetup&&!!state.selected;
  if(state.showSetup){loadParticipants();$('setup').scrollIntoView({behavior:'smooth',block:'nearest'});}
});
$('setup-form').addEventListener('submit',async(event)=>{
  event.preventDefault();
  try {
    await post('/api/select',{participantId:$('participant-select').value,syncStart:new Date($('sync-start').value).toISOString()});
    state.showSetup=false;
    notice('Participant connected. Initial sync has started.','success');
    refresh();
  } catch(error){notice(error.message,'error');}
});
function mode(value){
  state.mode=value;
  $('interval-fields').hidden=value==='live';
  $('label-submit').innerHTML=state.editingLabel?'Save changes <span>→</span>':value==='live'?'Start activity <span>→</span>':'Save interval <span>→</span>';
  $('label-start').required=value==='past';$('label-end').required=value==='past';
  for(const [id,selected] of [['live-tab',value==='live'],['past-tab',value==='past']]){
    $(id).classList.toggle('selected',selected);$(id).setAttribute('aria-selected',String(selected));
  }
}
$('live-tab').addEventListener('click',()=>{cancelLabelEdit();mode('live');});
$('past-tab').addEventListener('click',()=>mode('past'));
function cancelLabelEdit() {
  state.editingLabel=null;state.editingInputs=null;state.pendingLabelRange=null;$('cancel-label-edit').hidden=true;$('delete-label-edit').hidden=true;$('label-form').reset();mode('live');
}
$('cancel-label-edit').addEventListener('click',cancelLabelEdit);
$('delete-label-edit').addEventListener('click',async()=>{
  const label=state.editingLabel;if(!label)return;
  if(!confirm('Delete this activity label? The EEG recording will remain.'))return;
  try {await post(`/api/labels/${label.id}/delete`);cancelLabelEdit();notice('Label deleted.','success');await loadIntervals();refresh();}
  catch(error){notice(error.message,'error');}
});
$('label-form').addEventListener('submit',async(event)=>{
  event.preventDefault();
  if (!state.selected) return notice('Connect a participant first.','error');
  try {
    const original=state.editingLabel;
    const source=original||state.pendingLabelRange,inputs=original?state.editingInputs:state.pendingLabelRange?.inputs;
    const stamp=(edge)=>source&&source[edge]&&$(`label-${edge}`).value===inputs?.[edge]?source[edge]:new Date($(`label-${edge}`).value).toISOString();
    const start=state.mode==='live'?new Date().toISOString():stamp('start');
    const end=state.mode==='live'||(original&&!original.end&&!$('label-end').value)?null:stamp('end');
    await post(original?`/api/labels/${original.id}/edit`:'/api/labels',{label:$('label-name').value,note:$('label-note').value,start,end});
    const message=original?'Activity updated. Suggestions around it were retained.':state.mode==='live'?'Activity started. Stop it when you finish.':'Interval saved. Overlapping suggestions were refined.';
    cancelLabelEdit();
    notice(message,'success');
    await loadIntervals();
    refresh();
  } catch(error){notice(error.message,'error');}
});
$('active-activity').addEventListener('click',async(event)=>{
  const edit=event.target.closest('[data-edit]');
  if(edit) {const label=state.summary?.labels.find(label=>label.id===Number(edit.dataset.edit));if(label)editActivity(label);return;}
  const button=event.target.closest('[data-stop]');if(!button)return;
  try {await post(`/api/labels/${button.dataset.stop}/stop`,{end:new Date().toISOString()});notice('Activity stopped.','success');await loadIntervals();refresh();}
  catch(error){notice(error.message,'error');}
});
function editActivity(label) {
  state.editingLabel=label;mode('past');$('cancel-label-edit').hidden=false;$('delete-label-edit').hidden=false;
  $('label-name').value=label.label;$('label-note').value=label.source==='schedule'&&!label.reviewed?'':label.note;
  $('label-start').value=preciseInput(Date.parse(label.start));
  $('label-end').value=label.end?preciseInput(Date.parse(label.end)):'';
  $('label-end').required=!!label.end;
  state.editingInputs={start:$('label-start').value,end:$('label-end').value};
  $('activity').scrollIntoView({behavior:'smooth',block:'start'});
  $('label-name').focus({preventScroll:true});
  notice(label.source==='schedule'&&!label.reviewed?'Review this suggestion, then save your changes. Shortening it keeps the surrounding work suggestions.':'Edit the activity and save your changes.','success');
}
$('load-older').addEventListener('click',async()=>{
  if (state.intervalLoading || !state.nextBefore) return;
  state.intervalPages++;
  await loadIntervals();
});
$('intervals-list').addEventListener('toggle',(event)=>{
  const details=event.target;
  if (!details.matches('.daily-intervals')) return;
  if (details.open) state.expandedDays.add(details.dataset.day);
  else state.expandedDays.delete(details.dataset.day);
},true);
$('intervals-list').addEventListener('click',(event)=>{
  const reset=event.target.closest('[data-day-reset]');
  if(reset) {
    const date=reset.dataset.dayReset;clearTimeout(dayKeyTimers.get(date));state.dayRanges.delete(date);state.dayViews.delete(date);renderIntervals();return;
  }
  const assign=event.target.closest('[data-day-assign]');
  if(assign) {
    const day=state.days.find(day=>day.date===assign.dataset.dayAssign);if(!day)return;
    const range=daySelection(day);if(daySelectionStatus(day,range).blocked)return;
    cancelLabelEdit();mode('past');
    $('label-start').value=preciseInput(range.start);$('label-end').value=preciseInput(range.end);
    state.pendingLabelRange={start:new Date(range.start).toISOString(),end:new Date(range.end).toISOString(),inputs:{start:$('label-start').value,end:$('label-end').value}};
    $('activity').scrollIntoView({behavior:'smooth',block:'start'});$('label-name').focus({preventScroll:true});
    notice(`Selected ${dayStamp(day,range.start)} → ${dayStamp(day,range.end)}. Enter an activity name and save the interval.`,'success');return;
  }
  const edit=event.target.closest('[data-edit]');
  if(edit) {
    const label=state.intervals.flatMap(period=>period.labels).find(label=>label.id===Number(edit.dataset.edit));
    if(label) editActivity(label);
    return;
  }
  const button=event.target.closest('[data-pick-start]');
  if (!button || button.disabled) return;
  cancelLabelEdit();mode('past');
  $('label-start').value=localInput(new Date(button.dataset.pickStart));
  $('label-end').value=localInput(new Date(button.dataset.pickEnd));
  $('activity').scrollIntoView({behavior:'smooth',block:'start'});
  $('label-name').focus({preventScroll:true});
  notice('Selected a 30-minute period. Enter the activity name and save it.','success');
});
$('sync-start').value=localInput(new Date(Date.now()-24*3600000));
$('label-start').value=localInput(new Date(Date.now()-60*60000));
$('label-end').value=localInput(new Date());
const labelQuery = new URLSearchParams(location.search);
if (labelQuery.has('labelStart') && labelQuery.has('labelEnd')) {
  const start = new Date(labelQuery.get('labelStart'));
  const end = new Date(labelQuery.get('labelEnd'));
  if (Number.isFinite(start.getTime()) && Number.isFinite(end.getTime()) && end > start) {
    mode('past');
    $('label-start').value = localInput(start);
    $('label-end').value = localInput(end);
  }
}
windowRange();renderFocus();
new ResizeObserver(()=>{
  const result=state.summary;
  if (result) $('trend-chart').innerHTML=chartSvg(result.series,result.labels,result.window.start,result.window.end,result.window.bucketMs);
  renderBrush();
  if (state.intervals.length) renderIntervals();
}).observe($('trend-chart'));
loadParticipants();refresh();setInterval(refresh,10000);
