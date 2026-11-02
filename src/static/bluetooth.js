const $ = id => document.getElementById(id);
const state = {status:null, busy:false, loading:false, participant:'', actionError:''};
function esc(value) { return String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function stamp(value) { return value ? new Date(value).toLocaleString() : 'Unknown'; }
function notice(message,error=false) { $('notice').textContent=message; $('notice').hidden=!message; $('notice').classList.toggle('error',error); }
async function api(path,body) {
  const response=await fetch(path,body===undefined ? {cache:'no-store'} : {method:'POST',headers:{'Content-Type':'application/json','X-Awear-Local':'1'},body:JSON.stringify(body)});
  const result=await response.json();
  if (!response.ok) throw new Error(result.error || `Request failed (${response.status})`);
  return result;
}
function controls() {
  const status=state.status || {};
  const active=status.running || status.scanning || state.busy;
  $('scan').disabled=!status.available || active;
  $('device').disabled=!!active;
  $('duration').disabled=!!active;
  $('placement').disabled=!!active;
  $('connect').disabled=!status.available || active || !$('device').value || !state.participant;
  $('stop').disabled=!status.running || status.stopRequested || state.busy;
}
function renderWaveform(latest) {
  if (!latest) { $('waveform').innerHTML='<div class="empty-state">Waiting for a valid EEG block from the selected device.</div>'; return; }
  const values=latest.samples, mean=values.reduce((a,b)=>a+b,0)/values.length;
  const scale=Math.max(1,...values.map(value=>Math.abs(value-mean)))*1.1;
  const points=values.map((value,index)=>`${(index/(values.length-1)*900).toFixed(2)},${(80-(value-mean)/scale*72).toFixed(2)}`).join(' ');
  $('waveform').innerHTML=`<svg viewBox="0 0 900 160" role="img" aria-label="256 raw samples centered for display"><path d="M0 80 H900" stroke="#d8e7dc"/><polyline points="${points}" fill="none" stroke="#398b67" stroke-width="1.5"/></svg>`;
}
function render() {
  const status=state.status, current=status.current || {}, latest=current.latest;
  $('connection-state').textContent=status.scanning ? 'Scanning…' : status.stopRequested && status.running ? 'Stopping…' : (current.state || 'Idle').replaceAll('_',' ');
  const selected=status.running && current.deviceId ? current.deviceId : $('device').value;
  const signature=JSON.stringify(status.devices);
  if ($('device').dataset.signature!==signature) {
    $('device').innerHTML='<option value="">Choose your device</option>'+status.devices.map(device=>`<option value="${esc(device.id)}">${esc(device.name)} · ${device.rssi} dBm · ${esc(device.id)}</option>`).join('');
    $('device').dataset.signature=signature;
    if (status.devices.some(device=>device.id===selected)) $('device').value=selected;
  }
  if (status.running && current.placement) $('placement').value=current.placement;
  if (status.devices.length) $('scan-summary').textContent=`${status.devices.length} AWEAR candidate${status.devices.length===1?'':'s'} found. Select the exact device you want to record.`;
  else if (status.error) $('scan-summary').textContent='Scan did not complete. Check the message above, then scan again.';
  $('blocks').textContent=(current.eegBlocks || 0).toLocaleString();
  $('samples').textContent=(current.samples || 0).toLocaleString();
  $('missing').textContent=(current.missingBlocks || 0).toLocaleString();
  $('warnings').textContent=(current.decodeErrors || 0).toLocaleString();
  $('stream-source').textContent=latest ? `${latest.header.replay ? 'Saved-memory replay' : 'Live'} · ${latest.header.device_id}` : 'Waiting for data';
  $('device-time').textContent='Device clock: '+stamp(latest?.deviceTime);
  $('arrival-time').textContent='Computer arrival: '+stamp(latest?.receivedAt);
  const info=current.deviceInfo;
  $('capture-details').textContent=status.sessionId ? `${current.liveBlocks || 0} live / ${current.replayedBlocks || 0} replayed blocks · ${current.notifications || 0} notifications · ${current.duplicateCounters || 0} duplicate counters · ${current.queueOverflows || 0} queue overflows${info?.firmware ? ' · firmware '+info.firmware : ''} · placement: ${current.placement || 'unknown'} · ${status.sessionId}` : 'No recording started.';
  renderWaveform(latest);
  $('session-count').textContent=`${status.sessions.length} of ${status.totalSessions} sessions shown`;
  const names={'eeg.csv':'EEG CSV','eeg.jsonl':'EEG JSONL','raw-notifications.jsonl':'Raw packets','events.jsonl':'Events','session.json':'Session details','collector.log':'Collector log'};
  $('sessions').innerHTML=status.sessions.map(session=>{
    const active=status.running && session.id===status.sessionId;
    const links=active ? '<span>Recording in progress — stop to download.</span>' : session.files.map(file=>`<a href="/api/bluetooth/download?${esc(new URLSearchParams({session:session.id,file}))}">${names[file]}</a>`).join('');
    return `<div class="ble-session"><strong>${esc(session.id)}</strong><small>${esc(session.state)} · ${session.eegBlocks.toLocaleString()} EEG blocks · ${esc(stamp(session.startedAt))}${session.deviceId ? ' · '+esc(session.deviceId) : ''}</small><div class="ble-downloads">${links}</div></div>`;
  }).join('') || '<div class="empty-state">No local Bluetooth recordings yet.</div>';
  const error=!status.available ? 'Bluetooth support is not installed. Follow the Direct Bluetooth setup in README.' : status.error || state.actionError || current.error || '';
  notice(error || (current.savedMemoryDeletionReported ? 'The device reported clearing its saved buffer after replay. Received packets are retained in this local session; replayed placement is unknown.' : ''),!!error);
  controls();
}
async function refresh() {
  if (state.loading) return;
  state.loading=true;
  try { state.status=await api('/api/bluetooth/status'); render(); }
  catch(error) { notice(error.message,true); }
  finally { state.loading=false; }
}
async function action(path,body) {
  if (state.busy) return;
  state.busy=true; state.actionError=''; controls(); notice('');
  try {
    const result=await api(path,body);
    if (path.endsWith('/scan') && !result.devices.length) {
      $('scan-summary').textContent='No AWEAR candidates found. Check device power, distance, mobile connection, and Bluetooth permission.';
    }
  } catch(error) { state.actionError=error.message; notice(error.message,true); }
  finally { state.busy=false; await refresh(); controls(); }
}
$('scan').addEventListener('click',()=>action('/api/bluetooth/scan',{}));
$('connect').addEventListener('click',()=>action('/api/bluetooth/start',{deviceId:$('device').value,seconds:Number($('duration').value),placement:$('placement').value}));
$('stop').addEventListener('click',()=>action('/api/bluetooth/stop',{}));
$('device').addEventListener('change',controls);
api('/api/status').then(status=>{ state.participant=status.participantId; $('participant').textContent=status.participantId || 'Select a participant in Overview'; controls(); }).catch(error=>notice(error.message,true));
refresh();
setInterval(refresh,2000);
