const $ = id => document.getElementById(id);
const state = {rows:[], nextBefore:null, total:0, editing:null, loading:false, saving:false, captureTime:null, loadedOlder:false};
const colors = {alert:'#bd8050',engaged:'#af9852',relaxed:'#568eab',calm:'#3f9774'};
function esc(value) { return String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function localInput(value) {
  const date = new Date(value);
  return new Date(date.getTime()-date.getTimezoneOffset()*60000).toISOString().slice(0,23);
}
function stamp(value) { return new Date(value).toLocaleString([], {year:'numeric',month:'short',day:'numeric',hour:'numeric',minute:'2-digit',second:'2-digit'}); }
function notice(message, error=false) {
  $('notice').textContent = message;
  $('notice').hidden = !message;
  $('notice').classList.toggle('error',error);
}
async function api(path, body) {
  const options = body === undefined ? {cache:'no-store'} : {method:'POST',headers:{'Content-Type':'application/json','X-Awear-Local':'1'},body:JSON.stringify(body)};
  const response = await fetch(path,options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `Request failed (${response.status})`);
  return result;
}
function updateTimePreview() {
  const live = $('capture-now').checked;
  $('observed-at').disabled = live;
  if (live) {
    $('timestamp-preview').textContent = state.captureTime ? `Marked ${stamp(state.captureTime)}. This time stays fixed while you type and save. Uncheck the option above to enter a past reading.` : 'Click Mark time now when you read your phone, or start entering the score to mark the time. Saving will preserve that moment.';
    return;
  }
  const date = new Date($('observed-at').value);
  $('timestamp-preview').textContent = Number.isNaN(date.getTime()) ? '' :
    `Time entry uses ${Intl.DateTimeFormat().resolvedOptions().timeZone}. Saved as ${date.toISOString()} (UTC).`;
}
function clearEditor() {
  const version = $('app-version').value;
  $('score-form').reset();
  $('app-version').value = version;
  $('observed-at').value = localInput(new Date());
  state.editing = null;
  state.captureTime = null;
  $('editor-title').textContent = 'Record a phone check-in';
  $('save-score').textContent = 'Save observation';
  $('cancel-edit').hidden = true;
  updateTimePreview();
}
function rangeQuery() {
  const query = new URLSearchParams();
  for (const [id,key] of [['range-start','start'],['range-end','end']]) {
    if ($(id).value) query.set(key,new Date($(id).value).toISOString());
  }
  return query;
}
function renderChart() {
  const rows = [...state.rows].reverse();
  const first = rows.length ? Date.parse(rows[0].observedAt) : Date.now()-86400000;
  const last = rows.length ? Date.parse(rows[rows.length-1].observedAt) : Date.now();
  const padding = first === last ? 1800000 : Math.max(1000,(last-first)*.05);
  const selectedStart = Date.parse($('range-start').value), selectedEnd = Date.parse($('range-end').value);
  const start = Number.isFinite(selectedStart) ? selectedStart : first-padding;
  const end = Number.isFinite(selectedEnd) ? selectedEnd : Math.max(start+1000,last+padding);
  const span = Math.max(1000,end-start), left=52, right=870, top=30, bottom=205;
  const x = value => left+(Date.parse(value)-start)/span*(right-left);
  const y = value => bottom-value/100*(bottom-top);
  const grid = [0,25,50,75,100].map(value => `<path d="M${left},${y(value)} H${right}" stroke="#dce7df"/><text x="${left-12}" y="${y(value)+4}" text-anchor="end" fill="#597466" font-size="12">${value}</text>`).join('');
  const points = rows.map((row,index) => {
    const px=x(row.observedAt), py=y(row.score);
    const title = `${row.score} · ${row.state} · ${stamp(row.observedAt)}${row.activity ? ' · '+row.activity : ''}`;
    const half = Math.min(right-left,row.uncertaintySeconds*1000/span*(right-left));
    const url = '/eeg.html?'+new URLSearchParams({start:row.context ? row.context.start : new Date(Date.parse(row.observedAt)-30000).toISOString(),seconds:'60'});
    const label = rows.length <= 12 || index === rows.length-1 ? `<text x="${px}" y="${py-13}" text-anchor="middle" fill="${colors[row.state]}" font-weight="700" font-size="13">${row.score}</text>` : '';
    return `<a href="${esc(url)}" aria-label="${esc(title+'; inspect nearby EEG')}"><title>${esc(title)}</title>${half ? `<path d="M${Math.max(left,px-half)},${py} H${Math.min(right,px+half)}" stroke="${colors[row.state]}" opacity=".5"/>` : ''}<circle cx="${px}" cy="${py}" r="7" fill="${colors[row.state]}" stroke="white" stroke-width="2"/>${label}</a>`;
  }).join('');
  const empty = rows.length ? '' : `<text x="460" y="104" text-anchor="middle" fill="#355547" font-size="17" font-weight="700">No phone scores recorded in this range</text><text x="460" y="132" text-anchor="middle" fill="#758d80" font-size="13">Record a reading below to start this chart.</text>`;
  $('score-chart').innerHTML = `<svg viewBox="0 0 900 260" role="img" aria-label="${state.rows.length} manually recorded phone score points; score axis zero to one hundred"><text x="${left}" y="16" fill="#597466" font-size="11">SCORE (0–100)</text>${grid}${points}${empty}<text x="${left}" y="236" fill="#597466" font-size="11">${esc(stamp(start))}</text><text x="${right}" y="236" text-anchor="end" fill="#597466" font-size="11">${esc(stamp(end))}</text><text x="460" y="255" text-anchor="middle" fill="#758d80" font-size="10">OBSERVED TIME · ${esc(Intl.DateTimeFormat().resolvedOptions().timeZone)}</text></svg>`;
  const latest = state.rows[0];
  $('latest-mobile-score').textContent = latest ? `${latest.score} / 100 · ${latest.state}` : 'No readings yet';
}

function render() {
  $('score-count').textContent = `${state.rows.length} of ${state.total} observations shown`;
  $('chart-description').textContent = `Showing ${state.rows.length} of ${state.total} observations in this range. Points are manual readings; values between them are unknown. Horizontal bars show entered time uncertainty.`;
  $('load-older').hidden = !state.nextBefore;
  const query = rangeQuery();
  for (const format of ['csv','json']) $('scores-'+format).href = '/api/export/mobile-scores.'+format+(query.size ? '?'+query : '');
  $('score-rows').innerHTML = state.rows.map(row => {
    const context = row.context;
    const eegUrl = '/eeg.html?'+new URLSearchParams({start:context ? context.start : new Date(Date.parse(row.observedAt)-30000).toISOString(), seconds:'60'});
    const coverage = context ? `<strong>${context.coveredSeconds} of 60 sec recorded</strong><meter min="0" max="100" value="${context.coveragePercent}" aria-label="EEG context coverage">${context.coveragePercent}%</meter><small>${esc(stamp(context.start))} – ${esc(stamp(context.end))}</small>` : '';
    return `<tr><td><strong>${esc(stamp(row.observedAt))}</strong><small>±${row.uncertaintySeconds} sec · entered ${esc(stamp(row.createdAt))}</small></td><td><strong>${row.score}</strong></td><td><span class="score-state">${esc(row.state)}</span></td><td>${row.activity ? esc(row.activity) : 'Unlabeled'}</td><td>${coverage}${row.eeg ? `At marked time: recorded<small>${esc(row.eeg.deviceId || 'Device ID unavailable')} · ${row.eeg.sampleCount} samples</small>` : '<span class="score-error">No EEG at the exact marked time in the archive</span>'}<small><a href="${esc(eegUrl)}">Inspect nearby EEG ↗</a></small></td><td class="score-note">${esc(row.note || '—')}<small>${row.appVersion ? 'App '+esc(row.appVersion) : 'App version unspecified'}</small></td><td><button type="button" data-edit="${row.id}">Edit</button><button type="button" data-delete="${row.id}">Delete</button></td></tr>`;
  }).join('') || '<tr><td colspan="7" class="empty-state">No observations yet. Record the score and state shown in your mobile app.</td></tr>';
  renderChart();
}
async function load(append=false, refresh=false) {
  if (state.loading) return;
  state.loading = true;
  try {
    const query = rangeQuery();
    if (append && state.nextBefore) query.set('before',state.nextBefore);
    const result = await api('/api/mobile-scores?'+query);
    const target = refresh ? state.rows.length : 0;
    const refreshed = [...result.observations];
    let cursor = result.nextBefore;
    while (refresh && cursor && refreshed.length < target) {
      const olderQuery = rangeQuery(); olderQuery.set('before',cursor);
      const older = await api('/api/mobile-scores?'+olderQuery);
      refreshed.push(...older.observations);
      if (older.nextBefore === cursor) break;
      cursor = older.nextBefore;
    }
    state.rows = append ? [...state.rows,...result.observations] : refreshed;
    if (append) state.loadedOlder = true;
    else if (!refresh) state.loadedOlder = false;
    state.total = result.total;
    state.nextBefore = refresh ? cursor : result.nextBefore;
    $('participant').textContent = result.participantId;
    render();
  } catch(error) { notice(error.message,true); }
  finally { state.loading = false; }
}
$('observed-at').addEventListener('input',updateTimePreview);
$('capture-now').addEventListener('change',updateTimePreview);
function markTime() {
  state.captureTime = new Date().toISOString();
  $('capture-now').checked = true;
  $('observed-at').value = localInput(state.captureTime);
  updateTimePreview();
}
$('use-now').addEventListener('click',markTime);
$('score').addEventListener('input',() => { if ($('capture-now').checked && !state.captureTime) markTime(); });
$('cancel-edit').addEventListener('click',clearEditor);
$('score-form').addEventListener('submit',async event => {
  event.preventDefault();
  if (state.saving) return;
  state.saving = true; $('save-score').disabled = true;
  try {
    if ($('capture-now').checked && !state.captureTime) throw new Error('Mark the observation time before saving.');
    const path = state.editing === null ? '/api/mobile-scores' : `/api/mobile-scores/${state.editing}/edit`;
    await api(path,{observedAt:$('capture-now').checked ? state.captureTime : new Date($('observed-at').value).toISOString(),score:Number($('score').value),
      state:$('mobile-state').value,uncertaintySeconds:Number($('uncertainty').value),
      appVersion:$('app-version').value,note:$('score-note').value});
    clearEditor();
    notice('Check-in saved at the marked time. Nearby EEG context updates as recordings arrive.');
    await load();
  } catch(error) { notice(error.message,true); }
  finally { state.saving = false; $('save-score').disabled = false; }
});
$('score-rows').addEventListener('click',async event => {
  const edit = event.target.closest('[data-edit]'), remove=event.target.closest('[data-delete]');
  if (edit) {
    const row = state.rows.find(row => row.id === Number(edit.dataset.edit));
    state.editing = row.id;
    state.captureTime = null;
    $('capture-now').checked = false;
    $('observed-at').value = localInput(row.observedAt); $('score').value = row.score;
    $('mobile-state').value=row.state; $('uncertainty').value=row.uncertaintySeconds;
    $('app-version').value=row.appVersion; $('score-note').value=row.note;
    $('editor-title').textContent='Edit mobile observation'; $('save-score').textContent='Save changes';
    $('cancel-edit').hidden=false; updateTimePreview();
    $('score-form').scrollIntoView({behavior:'smooth',block:'center'});
  }
  if (remove && confirm('Delete this manually entered observation?')) {
    try {
      const id = Number(remove.dataset.delete);
      await api(`/api/mobile-scores/${id}/delete`,{});
      if (state.editing === id) clearEditor();
      await load(); notice('Observation deleted.');
    } catch(error) { notice(error.message,true); }
  }
});
$('range-form').addEventListener('submit',event => { event.preventDefault(); load(); });
$('clear-range').addEventListener('click',() => { $('range-form').reset(); load(); });
$('load-older').addEventListener('click',() => load(true));
clearEditor();
const initialTime = new URLSearchParams(location.search).get('observedAt');
if (initialTime && Number.isFinite(new Date(initialTime).getTime())) {
  $('capture-now').checked = false; $('observed-at').value = localInput(initialTime); updateTimePreview();
}
load();
api('/api/status').then(status => { $('demo-badge').hidden=!status.demo; }).catch(()=>{});

$('refresh-context').addEventListener('click',() => load(false,true));
setInterval(() => { if (!state.loadedOlder && !state.editing && !state.saving && !document.hidden) load(); },10000);
