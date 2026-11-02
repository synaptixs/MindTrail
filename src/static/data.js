const $ = id => document.getElementById(id);
const state = {tab:'raw', nextBefore:null, shown:0, total:0, loading:false, snapshot:null};
const rawColumns = ['timestamp','device_id','activity',...Array.from({length:256},(_,i)=>`sample_${String(i).padStart(3,'0')}`)];
const sessionColumns = ['date','deviceId','sampleCount','startTime','endTime'];
const statColumns = ['participantId','totalSamples','totalHours','firstRecord','lastRecord','devices'];

function localTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}
function localInput(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const shifted = new Date(date.getTime() - date.getTimezoneOffset()*60000);
  return shifted.toISOString().slice(0,16);
}
function cell(value) {
  if (value === null || value === undefined || value === '') return '—';
  return typeof value === 'object' ? JSON.stringify(value) : String(value);
}
function setNotice(message) { $('notice').textContent=message; $('notice').hidden=!message; }
async function api(path) {
  const response=await fetch(path,{cache:'no-store'});
  const body=await response.json();
  if (!response.ok) throw new Error(body.error||`Request failed (${response.status})`);
  return body;
}
function setColumns(columns, sticky=false) {
  const head=$('data-table').tHead||$('data-table').createTHead();
  head.replaceChildren();
  const row=head.insertRow();
  columns.forEach((name,index)=>{
    const th=document.createElement('th');
    th.textContent=name;
    th.scope='col';
    if (sticky && index===0) th.className='time-col';
    if (sticky && index===1) th.className='device-col';
    if (sticky && index===2) th.className='activity-col';
    row.append(th);
  });
  $('data-table').tBodies[0].replaceChildren();
  $('table-wrap').scrollLeft=0;
}
function addRows(rows, columns, sticky=false) {
  const tbody=$('data-table').tBodies[0];
  const fragment=document.createDocumentFragment();
  rows.forEach(record=>{
    const tr=document.createElement('tr');
    columns.forEach((name,index)=>{
      const td=tr.insertCell();
      const value=record[name];
      td.textContent=cell(value);
      if (sticky && index===0) td.className='time-col';
      if (sticky && index===1) td.className='device-col';
      if (sticky && index===2) td.className='activity-col';
      if (typeof value==='object' && value!==null) {td.classList.add('json-cell');td.title=JSON.stringify(value);}
    });
    fragment.append(tr);
  });
  tbody.append(fragment);
  if (!rows.length && !tbody.rows.length) {
    const tr=tbody.insertRow();
    const td=tr.insertCell();
    td.colSpan=columns.length;
    td.className='empty-cell';
    td.textContent='No records in this table.';
  }
}
function rawQuery() {
  const query=new URLSearchParams();
  const start=$('start').value, end=$('end').value;
  if (start) query.set('start',new Date(start).toISOString());
  if (end) query.set('end',new Date(end).toISOString());
  if (state.nextBefore) query.set('before',state.nextBefore);
  return query.toString();
}
async function loadRaw(reset=false) {
  if (state.loading) return;
  state.loading=true;
  $('load-more').disabled=true;
  try {
    if (reset) {state.nextBefore=null;state.shown=0;setColumns(rawColumns,true);}
    const query=rawQuery();
    const result=await api('/api/table/raw'+(query?'?'+query:''));
    const rows=result.records.map(row=>{
      const flat={timestamp:row.timestamp,device_id:row.device_id,activity:row.activity};
      row.waveform.forEach((value,i)=>{flat[rawColumns[i+3]]=value;});
      return flat;
    });
    addRows(rows,rawColumns,true);
    state.shown+=rows.length;
    state.total=result.total;
    state.nextBefore=result.nextBefore;
    $('row-count').textContent=`${state.total.toLocaleString()} records`;
    $('page-info').textContent=`Showing ${state.shown.toLocaleString()} of ${state.total.toLocaleString()} records · newest first`;
    $('load-more').hidden=!state.nextBefore;
    setNotice('');
  } catch(error) {setNotice(error.message);}
  finally {state.loading=false;$('load-more').disabled=false;}
}
async function loadSnapshot() {
  if (!state.snapshot) state.snapshot=await api('/api/table/snapshot');
  const data=state.snapshot;
  const stamp=data.exportedAt?`Saved API export · ${localTime(data.exportedAt)}`:'No saved API export found. Run export_data.py to populate this table.';
  $('table-source').textContent=stamp;
  if (state.tab==='sessions') {
    setColumns(sessionColumns);
    const rows=Array.isArray(data.sessions)?data.sessions:[];
    addRows(rows,sessionColumns);
    $('row-count').textContent=`${rows.length.toLocaleString()} sessions`;
    $('page-info').textContent=`Showing all ${rows.length.toLocaleString()} saved sessions`;
  } else {
    setColumns(statColumns);
    const rows=data.stats?[data.stats]:[];
    addRows(rows,statColumns);
    $('row-count').textContent=rows.length?'1 stats record':'0 stats records';
    $('page-info').textContent=rows.length?'Showing saved AWEAR account statistics':'No saved statistics';
  }
}
async function selectTab(tab) {
  state.tab=tab;
  document.querySelectorAll('[data-tab]').forEach(button=>{
    const selected=button.dataset.tab===tab;
    button.classList.toggle('selected',selected);
    button.setAttribute('aria-selected',String(selected));
  });
  $('range-form').hidden=tab!=='raw';
  $('load-more').hidden=true;
  if (tab==='raw') {
    $('table-eyebrow').textContent='LOCAL ARCHIVE';
    $('table-title').textContent='Raw EEG recordings';
    $('table-source').textContent='Waveforms stored in the local SQLite archive. New synced records appear here.';
    $('table-help').textContent='Activity comes from your saved time-period labels. Scroll sideways for all 256 waveform samples; values are shown exactly as saved. A device ID may be blank for older archived rows.';
    await loadRaw(true);
  } else {
    $('table-eyebrow').textContent='SAVED API EXPORT';
    $('table-title').textContent=tab==='sessions'?'Recording sessions':'Account statistics';
    $('table-help').textContent=tab==='sessions'?'Session fields and UTC timestamps as returned by AWEAR.':'Statistics fields as returned by AWEAR. The devices column contains the saved JSON value.';
    try {await loadSnapshot();setNotice('');} catch(error) {setNotice(error.message);}
  }
}

document.querySelectorAll('[data-tab]').forEach(button=>button.addEventListener('click',()=>selectTab(button.dataset.tab)));
$('range-form').addEventListener('submit',event=>{
  event.preventDefault();
  if ($('start').value && $('end').value && $('start').value >= $('end').value) return setNotice('Start must be earlier than end.');
  loadRaw(true);
});
$('clear-range').addEventListener('click',()=>{$('start').value='';$('end').value='';loadRaw(true);});
$('load-more').addEventListener('click',()=>loadRaw());
const initial=new URLSearchParams(location.search);
if (initial.get('start')) $('start').value=localInput(initial.get('start'));
if (initial.get('end')) $('end').value=localInput(initial.get('end'));
api('/api/status').then(status=>{
  $('participant').textContent=status.participantId||'No participant selected';
  if (status.demo) $('participant').textContent+=' · sample data';
}).catch(error=>setNotice(error.message));
selectTab('raw');
