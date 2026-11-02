const $ = id => document.getElementById(id);
const state = {rows:[], nextBefore:null, total:0, editing:null, loading:false, saving:false};
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
    $('timestamp-preview').textContent = `Live reading: the observation time is captured when you press Save. Computer timezone: ${Intl.DateTimeFormat().resolvedOptions().timeZone}; stored as UTC. Uncheck “Timestamp at Save” to enter a past time.`;
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
  $('editor-title').textContent = 'Record a mobile score';
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
  if (!state.rows.length) { $('score-chart').innerHTML = '<div class="empty-state">No mobile observations in this range.</div>'; return; }
  const rows = [...state.rows].reverse();
  const first = Date.parse(rows[0].observedAt), last = Date.parse(rows[rows.length-1].observedAt);
  const span = Math.max(1000,last-first), left=42, right=880, top=15, bottom=137;
  const x = value => left+(Date.parse(value)-first)/span*(right-left);
  const y = value => bottom-value/100*(bottom-top);
  const grid = [0,25,50,75,100].map(value => `<path d="M${left},${y(value)} H${right}" stroke="#e2ebe4"/><text x="${left-9}" y="${y(value)+3}" text-anchor="end" fill="#8ba092" font-size="10">${value}</text>`).join('');
  const points = rows.map(row => {
    const radius = 4, px=x(row.observedAt), py=y(row.score);
    const title = `${row.score} · ${row.state} · ${stamp(row.observedAt)}${row.activity ? ' · '+row.activity : ''}`;
    const half = Math.min(right-left,row.uncertaintySeconds*1000/span*(right-left));
    return `<g><title>${esc(title)}</title>${half ? `<path d="M${Math.max(left,px-half)},${py} H${Math.min(right,px+half)}" stroke="${colors[row.state]}" opacity=".5"/>` : ''}<circle cx="${px}" cy="${py}" r="${radius}" fill="${colors[row.state]}"/></g>`;
  }).join('');
  $('score-chart').innerHTML = `<svg viewBox="0 0 900 170" role="img" aria-label="${state.rows.length} manually recorded score points">${grid}${points}<text x="${left}" y="163" fill="#8ba092" font-size="10">${esc(stamp(rows[0].observedAt))}</text><text x="${right}" y="163" text-anchor="end" fill="#8ba092" font-size="10">${esc(stamp(rows[rows.length-1].observedAt))}</text></svg>`;
}
function render() {
  $('score-count').textContent = `${state.rows.length} of ${state.total} observations shown`;
  $('chart-description').textContent = `Showing ${state.rows.length} of ${state.total} observations in this range. Points are manual readings; values between them are unknown. Horizontal bars show entered time uncertainty.`;
  $('load-older').hidden = !state.nextBefore;
  const query = rangeQuery();
  for (const format of ['csv','json']) $('scores-'+format).href = '/api/export/mobile-scores.'+format+(query.size ? '?'+query : '');
  $('score-rows').innerHTML = state.rows.map(row => {
    const eegUrl = '/eeg.html?'+new URLSearchParams({start:new Date(Date.parse(row.observedAt)-10000).toISOString()});
    return `<tr><td><strong>${esc(stamp(row.observedAt))}</strong><small>±${row.uncertaintySeconds} sec · entered ${esc(stamp(row.createdAt))}</small></td><td><strong>${row.score}</strong></td><td><span class="score-state">${esc(row.state)}</span></td><td>${row.activity ? esc(row.activity) : 'Unlabeled'}</td><td>${row.eeg ? `Recorded<small>${esc(row.eeg.deviceId || 'Device ID unavailable')} · ${row.eeg.sampleCount} samples</small>` : '<span class="score-error">No EEG at this time</span>'}<small><a href="${esc(eegUrl)}">View aligned window ↗</a></small></td><td class="score-note">${esc(row.note || '—')}<small>${row.appVersion ? 'App '+esc(row.appVersion) : 'App version unspecified'}</small></td><td><button type="button" data-edit="${row.id}">Edit</button><button type="button" data-delete="${row.id}">Delete</button></td></tr>`;
  }).join('') || '<tr><td colspan="7" class="empty-state">No observations yet. Record the score and state shown in your mobile app.</td></tr>';
  renderChart();
}
async function load(append=false) {
  if (state.loading) return;
  state.loading = true;
  try {
    const query = rangeQuery();
    if (append && state.nextBefore) query.set('before',state.nextBefore);
    const result = await api('/api/mobile-scores?'+query);
    state.rows = append ? [...state.rows,...result.observations] : result.observations;
    state.total = result.total;
    state.nextBefore = result.nextBefore;
    $('participant').textContent = result.participantId;
    render();
  } catch(error) { notice(error.message,true); }
  finally { state.loading = false; }
}
$('observed-at').addEventListener('input',updateTimePreview);
$('capture-now').addEventListener('change',updateTimePreview);
$('use-now').addEventListener('click',() => { $('capture-now').checked=true; $('observed-at').value=localInput(new Date()); updateTimePreview(); });
$('cancel-edit').addEventListener('click',clearEditor);
$('score-form').addEventListener('submit',async event => {
  event.preventDefault();
  if (state.saving) return;
  state.saving = true; $('save-score').disabled = true;
  try {
    const path = state.editing === null ? '/api/mobile-scores' : `/api/mobile-scores/${state.editing}/edit`;
    await api(path,{observedAt:$('capture-now').checked ? new Date().toISOString() : new Date($('observed-at').value).toISOString(),score:Number($('score').value),
      state:$('mobile-state').value,uncertaintySeconds:Number($('uncertainty').value),
      appVersion:$('app-version').value,note:$('score-note').value});
    clearEditor();
    notice('Observation saved. EEG coverage and activity context reflect the current archive.');
    await load();
  } catch(error) { notice(error.message,true); }
  finally { state.saving = false; $('save-score').disabled = false; }
});
$('score-rows').addEventListener('click',async event => {
  const edit = event.target.closest('[data-edit]'), remove=event.target.closest('[data-delete]');
  if (edit) {
    const row = state.rows.find(row => row.id === Number(edit.dataset.edit));
    state.editing = row.id;
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
