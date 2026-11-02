const $ = (id) => document.getElementById(id);
const state = { latest: true, result: null, loading: false };
const FS = 256;
const FFT_SIZE = 4 * FS;
const FREQ_STEP = FS / FFT_SIZE;

function localInput(date) {
  const shifted = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return shifted.toISOString().slice(0, 19);
}
function stamp(value) {
  return new Date(value).toLocaleString([], {month:'short',day:'numeric',hour:'numeric',minute:'2-digit',second:'2-digit'});
}
function notice(message) {
  $('notice').textContent = message;
  $('notice').hidden = !message;
}
function prepareCanvas(id) {
  const canvas = $(id);
  const ratio = window.devicePixelRatio || 1;
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  const ctx = canvas.getContext('2d');
  ctx.scale(ratio, ratio);
  ctx.fillStyle = '#fbfefb';
  ctx.fillRect(0, 0, width, height);
  return {ctx, width, height};
}
function emptyCanvas(id, message) {
  const {ctx, width, height} = prepareCanvas(id);
  ctx.fillStyle = '#82978a';
  ctx.font = '12px system-ui';
  ctx.textAlign = 'center';
  ctx.fillText(message, width / 2, height / 2);
}
function runs(records) {
  const result = [];
  let run = [];
  let prior = null;
  for (const record of records) {
    const time = Date.parse(record.timestamp);
    const valid = record.samples.length === FS && record.samples.every(Number.isFinite);
    if (!valid) { if (run.length) result.push(run); run = []; prior = null; continue; }
    if (prior && (time - prior.time !== 1000 || record.deviceId !== prior.deviceId)) {
      result.push(run);
      run = [];
    }
    run.push(record);
    prior = {time, deviceId: record.deviceId};
  }
  if (run.length) result.push(run);
  return result;
}
function fftPower(samples) {
  const n = FFT_SIZE;
  const real = new Float64Array(n);
  const imag = new Float64Array(n);
  const mean = samples.reduce((total, value) => total + value, 0) / n;
  let windowSumSq = 0;
  for (let i = 0; i < n; i++) {
    const hann = 0.5 - 0.5 * Math.cos(2 * Math.PI * i / (n - 1));
    real[i] = (samples[i] - mean) * hann;
    windowSumSq += hann * hann;
  }
  for (let i = 1, j = 0; i < n; i++) {
    let bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) {
      [real[i], real[j]] = [real[j], real[i]];
      [imag[i], imag[j]] = [imag[j], imag[i]];
    }
  }
  for (let size = 2; size <= n; size <<= 1) {
    const half = size >> 1;
    const angle = -2 * Math.PI / size;
    for (let start = 0; start < n; start += size) {
      for (let k = 0; k < half; k++) {
        const c = Math.cos(angle * k), s = Math.sin(angle * k);
        const even = start + k, odd = even + half;
        const tr = c * real[odd] - s * imag[odd];
        const ti = s * real[odd] + c * imag[odd];
        real[odd] = real[even] - tr;
        imag[odd] = imag[even] - ti;
        real[even] += tr;
        imag[even] += ti;
      }
    }
  }
  const power = new Float64Array(161); // 0–40 Hz, 0.25-Hz bins
  for (let bin = 0; bin < power.length; bin++) {
    power[bin] = 2 * (real[bin] ** 2 + imag[bin] ** 2) / (FS * windowSumSq);
  }
  return power;
}
function spectra(records) {
  const frames = [];
  for (const run of runs(records)) {
    for (let offset = 0; offset + 4 <= run.length; offset += 2) {
      const samples = [];
      for (let second = 0; second < 4; second++) samples.push(...run[offset + second].samples);
      frames.push({center: Date.parse(run[offset].timestamp) + 2000, power: fftPower(samples)});
    }
  }
  return frames;
}
function drawWaveform(result) {
  const {ctx, width, height} = prepareCanvas('waveform');
  const start = Date.parse(result.start), end = Date.parse(result.end);
  const left = 10, right = width - 10, top = 12, bottom = height - 12;
  const x = (time) => left + (time - start) / (end - start) * (right - left);
  for (const label of result.labels) {
    const from = Math.max(start, Date.parse(label.start));
    const to = Math.min(end, label.end ? Date.parse(label.end) : end);
    if (to <= from) continue;
    ctx.fillStyle = '#e5f2e0';
    ctx.fillRect(x(from), top, x(to) - x(from), bottom - top);
  }
  for (let t = Math.ceil(start / 5000) * 5000; t < end; t += 5000) {
    ctx.strokeStyle = '#e7eee9'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x(t), top); ctx.lineTo(x(t), bottom); ctx.stroke();
  }
  const segments = runs(result.records);
  const centered = segments.flatMap(run => run.flatMap(record => {
    const mean = record.samples.reduce((sum, value) => sum + value, 0) / FS;
    return record.samples.map(value => value - mean);
  }));
  if (!centered.length) {
    ctx.fillStyle = '#82978a'; ctx.font = '12px system-ui'; ctx.textAlign = 'center';
    ctx.fillText('No valid EEG records in this window.', width / 2, height / 2 - 12);
  }
  const scale = Math.max(1, ...centered.map(Math.abs)) * 1.08;
  const y = (value) => height / 2 - value / scale * (bottom - top) / 2;
  ctx.strokeStyle = '#b8d0bf'; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(left, height / 2); ctx.lineTo(right, height / 2); ctx.stroke();
  ctx.strokeStyle = '#228868'; ctx.lineWidth = 1.1;
  for (const run of segments) {
    for (const record of run) {
      ctx.beginPath();
      const mean = record.samples.reduce((sum, value) => sum + value, 0) / FS;
      const time = Date.parse(record.timestamp);
      record.samples.forEach((value, index) => {
        const px = x(time + index * 1000 / FS), py = y(value - mean);
        if (index === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
      });
      ctx.stroke();
    }
  }
  ctx.fillStyle = '#678675'; ctx.font = '10px system-ui'; ctx.textAlign = 'left';
  ctx.fillText(`±${scale.toFixed(1)} device units`, left + 5, top + 12);
  for (const observation of result.mobileScores || []) {
    const time = Date.parse(observation.observedAt);
    const uncertainty = observation.uncertaintySeconds * 1000;
    if (uncertainty) {
      ctx.fillStyle = '#af79ba20';
      const from = x(Math.max(start,time-uncertainty)), to = x(Math.min(end,time+uncertainty));
      ctx.fillRect(from,top,to-from,bottom-top);
    }
    ctx.strokeStyle = '#a36bb5'; ctx.lineWidth = 1.5; ctx.setLineDash([4,3]);
    ctx.beginPath(); ctx.moveTo(x(time),top); ctx.lineTo(x(time),bottom); ctx.stroke();
    ctx.setLineDash([]);
  }
}
function drawSpectrogram(result, frames) {
  const {ctx, width, height} = prepareCanvas('spectrogram');
  if (!frames.length) return emptyCanvas('spectrogram', 'At least 4 continuous seconds are needed.');
  const start = Date.parse(result.start), end = Date.parse(result.end);
  const left = 34, right = width - 10, top = 12, bottom = height - 18;
  const db = frames.flatMap(frame => Array.from(frame.power.slice(4, 161), value => 10 * Math.log10(Math.max(value, 1e-12))));
  const sorted = db.sort((a, b) => a - b);
  const low = sorted[Math.floor(sorted.length * .05)];
  const high = Math.max(low + 1, sorted[Math.floor(sorted.length * .95)]);
  const x = (time) => left + (time - start) / (end - start) * (right - left);
  const cellH = (bottom - top) / 157;
  for (const frame of frames) {
    const from = Math.max(start, frame.center - 1000), to = Math.min(end, frame.center + 1000);
    for (let bin = 4; bin <= 160; bin++) {
      const value = 10 * Math.log10(Math.max(frame.power[bin], 1e-12));
      const level = Math.max(0, Math.min(1, (value - low) / (high - low)));
      const hue = 205 - level * 175;
      ctx.fillStyle = `hsl(${hue} 76% ${94 - level * 49}%)`;
      ctx.fillRect(x(from), bottom - (bin - 3) * cellH, Math.max(1, x(to) - x(from)), cellH + .3);
    }
  }
  ctx.strokeStyle = '#d4e2d8'; ctx.fillStyle = '#718a7a'; ctx.font = '10px system-ui'; ctx.textAlign = 'right';
  for (const freq of [10, 20, 30, 40]) {
    const py = bottom - (freq * 4 - 3.5) * cellH;
    ctx.beginPath(); ctx.moveTo(left, py); ctx.lineTo(right, py); ctx.stroke();
    ctx.fillText(String(freq), left - 5, py + 3);
  }
  $('spectrogram-scale').textContent = 'Color scaled within this window';
}
function drawSpectrum(frames) {
  const {ctx, width, height} = prepareCanvas('spectrum');
  if (!frames.length) return emptyCanvas('spectrum', 'At least 4 continuous seconds are needed.');
  const average = new Float64Array(161);
  for (const frame of frames) for (let bin = 4; bin <= 160; bin++) average[bin] += frame.power[bin] / frames.length;
  const left = 36, right = width - 12, top = 12, bottom = height - 22;
  const peak = Math.max(...average.slice(4));
  const db = Array.from(average, value => 10 * Math.log10(Math.max(value / peak, 1e-12)));
  const floor = Math.min(-20, Math.max(-70, Math.min(...db.slice(4))));
  const x = (freq) => left + (freq - 1) / 39 * (right - left);
  const y = (value) => top + (0 - Math.max(floor, value)) / -floor * (bottom - top);
  for (const freq of [1, 4, 8, 13, 30, 40]) {
    ctx.strokeStyle = '#e4eee6'; ctx.beginPath(); ctx.moveTo(x(freq), top); ctx.lineTo(x(freq), bottom); ctx.stroke();
    ctx.fillStyle = '#738b7b'; ctx.font = '10px system-ui'; ctx.textAlign = 'center'; ctx.fillText(String(freq), x(freq), height - 6);
  }
  ctx.strokeStyle = '#278d69'; ctx.lineWidth = 2; ctx.beginPath();
  for (let bin = 4; bin <= 160; bin++) {
    const px = x(bin * FREQ_STEP), py = y(db[bin]);
    if (bin === 4) ctx.moveTo(px, py); else ctx.lineTo(px, py);
  }
  ctx.stroke();
  ctx.fillStyle = '#678675'; ctx.font = '10px system-ui'; ctx.textAlign = 'left';
  ctx.fillText('dB relative to peak', left + 4, top + 10);
  const bands = [['Delta',1,4],['Theta',4,8],['Alpha',8,13],['Beta',13,30],['30–40 Hz',30,40.25]];
  const total = average.slice(4,161).reduce((sum,value)=>sum+value,0);
  $('bands').innerHTML = bands.map(([name, low, high]) => {
    let sum = 0;
    for (let bin = Math.ceil(low / FREQ_STEP); bin < high / FREQ_STEP && bin <= 160; bin++) sum += average[bin];
    const share = total ? 100 * sum / total : 0;
    return `<div class="eeg-band"><span>${name}</span><strong>${share.toFixed(1)}%</strong><small>${low}–${high === 40.25 ? '40' : high} Hz</small></div>`;
  }).join('');
}
function render(result) {
  state.result = result;
  const records = result.records;
  $('start').value = localInput(new Date(result.start));
  $('window-time').textContent = `${stamp(result.start)} – ${stamp(result.end)}`;
  $('axis-start').textContent = stamp(result.start);
  $('axis-end').textContent = stamp(result.end);
  $('spectrogram-start').textContent = stamp(result.start);
  $('spectrogram-end').textContent = stamp(result.end);
  const seconds = (Date.parse(result.end) - Date.parse(result.start)) / 1000;
  $('coverage').textContent = `${records.length} one-second records in ${seconds} seconds`;
  const devices = [...new Set(records.map(record => record.deviceId).filter(Boolean))];
  $('device').textContent = devices.length === 1 ? `Device ${devices[0]}` : devices.length ? `${devices.length} devices` : 'Device ID unavailable';
  $('activity-labels').replaceChildren();
  if (result.labels.length) {
    for (const label of result.labels) {
      const badge = document.createElement('span');
      badge.textContent = `${label.label} · ${stamp(label.start)}${label.end ? ` – ${stamp(label.end)}` : ' – ongoing'}`;
      $('activity-labels').append(badge);
    }
  } else $('activity-labels').textContent = 'No activity label overlaps this window.';
  $('log-mobile-score').href = '/scores.html?' + new URLSearchParams({observedAt:result.start});
  $('mobile-score-list').replaceChildren();
  for (const observation of result.mobileScores || []) {
    const badge = document.createElement('span');
    badge.textContent = `${observation.score} · ${observation.state} · ${stamp(observation.observedAt)} · ±${observation.uncertaintySeconds} sec`;
    badge.title = observation.note;
    $('mobile-score-list').append(badge);
  }
  if (!(result.mobileScores || []).length) $('mobile-score-list').textContent = 'No mobile observation at a timestamp inside this window.';
  const frames = spectra(records);
  $('window-count').textContent = `${frames.length} valid 4-second windows`;
  $('spectrogram-scale').textContent = frames.length ? 'Color scaled within this window' : 'No continuous window';
  $('bands').replaceChildren();
  drawWaveform(result);
  drawSpectrogram(result, frames);
  drawSpectrum(frames);
}
async function load() {
  if (state.loading) return;
  state.loading = true;
  try {
    const query = new URLSearchParams({seconds: $('seconds').value});
    if (!state.latest) query.set('start', new Date($('start').value).toISOString());
    const response = await fetch('/api/eeg/window?' + query, {cache:'no-store'});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
    $('participant').textContent = result.participantId;
    notice('');
    render(result);
  } catch (error) { notice(error.message); }
  finally { state.loading = false; }
}
$('window-form').addEventListener('submit', event => { event.preventDefault(); state.latest = false; load(); });
$('seconds').addEventListener('change', load);
$('latest').addEventListener('click', () => { state.latest = true; load(); });
for (const [id, direction] of [['previous',-1], ['next',1]]) {
  $(id).addEventListener('click', () => {
    if (!state.result) return;
    const seconds = Number($('seconds').value);
    $('start').value = localInput(new Date(Date.parse(state.result.start) + direction * seconds * 1000));
    state.latest = false;
    load();
  });
}
window.addEventListener('resize', () => { if (state.result) render(state.result); });
const initial = new URLSearchParams(location.search).get('start');
if (initial) { const parsed = new Date(initial); if (!Number.isNaN(parsed.getTime())) { $('start').value = localInput(parsed); state.latest = false; } }
load();
setInterval(() => { if (state.latest) load(); }, 10000);
