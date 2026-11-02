const $ = (id) => document.getElementById(id);
const state = {report: null, periods: [], selected: null, windowPage: 0, timelineSlots: []};
const SLOT_MS = 30 * 60 * 1000;
const WINDOW_PAGE_SIZE = 60;
const COLORS = {one:'#3a9573', two:'#6894c9', flagged:'#d69d57', empty:'#eff3f0'};

function esc(value) {
  return String(value).replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
}
function localDay(ms) {
  const date = new Date(ms);
  return [date.getFullYear(), String(date.getMonth()+1).padStart(2,'0'), String(date.getDate()).padStart(2,'0')].join('-');
}
function localTime(ms) {
  return new Date(ms).toLocaleString([], {month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});
}
function percent(part, total) { return total ? `${(100 * part / total).toFixed(1)}%` : '—'; }
function slotOf(start) { return Math.floor(Date.parse(start) / SLOT_MS) * SLOT_MS; }
function labelFor(slot) {
  return state.report.labels.filter(label => Date.parse(label.start) < slot + SLOT_MS &&
    (label.end === null || Date.parse(label.end) > slot)).map(label => label.label);
}
function summarizeWindows(report) {
  const map = new Map();
  for (const window of report.windows) {
    const slot = slotOf(window.start);
    if (!map.has(slot)) map.set(slot, {slot, windows:[], one:0, two:0, flagged:0, uncertain:0});
    const period = map.get(slot);
    period.windows.push(window);
    if (window.qualityFlags.length) period.flagged++;
    else {
      if (window.gmmPattern === 1) period.one++;
      if (window.gmmPattern === 2) period.two++;
      if (window.confidence < .7) period.uncertain++;
    }
  }
  return [...map.values()].sort((a,b) => a.slot - b.slot);
}
function renderOverview() {
  const report = state.report;
  $('participant').textContent = report.participantId;
  $('generated').textContent = `Analyzed ${localTime(Date.parse(report.generatedAt))}`;
  $('source-records').textContent = `${report.source.records.toLocaleString()} source records · ${report.windows.length.toLocaleString()} windows`;
  $('source-status').textContent = report.sourceCurrent ? 'Archive matches analysis' : `${report.currentRecords.toLocaleString()} records now · rerun analysis`;
  if (!report.sourceCurrent) {
    $('notice').textContent = 'New recordings have arrived since this report was generated. Rerun python analyze_patterns.py to include them.';
    $('notice').hidden = false;
  }
  $('window-total').textContent = report.windows.length.toLocaleString();
  $('flagged-total').textContent = `${report.quality.flaggedWindows.toLocaleString()} (${percent(report.quality.flaggedWindows, report.windows.length)})`;
  for (const profile of report.profiles) {
    $(`pattern-${profile.pattern === 1 ? 'one' : 'two'}`).textContent = percent(profile.windows, report.unflaggedWindows);
  }
  const reasonNames = {highAmplitude:'High amplitude',largeStep:'Large sample jump',flatline:'Flat signal'};
  $('quality-details').innerHTML = Object.entries(report.quality.reasons).map(([key, count]) =>
    `<div class="quality-row"><span>${reasonNames[key] || esc(key)}</span><strong>${count.toLocaleString()}</strong></div>`).join('') +
    `<p class="patterns-note">${report.extraction.discontinuities.toLocaleString()} gaps or device changes; ${(report.extraction.periodBoundaries ?? 0).toLocaleString()} half-hour boundaries; ${report.extraction.shortRunSeconds.toLocaleString()} seconds in runs too short to analyze; ${report.extraction.invalidRecords.toLocaleString()} invalid records.</p>`;
  const bands = ['1–4 Hz','4–8 Hz','8–13 Hz','13–30 Hz','30–40 Hz'];
  $('profiles').innerHTML = report.profiles.map(profile =>
    `<div class="profile"><strong class="pattern-name pattern-${profile.pattern}">Pattern ${profile.pattern}</strong><span>Median AC RMS ${profile.medianRms} device units · ${profile.windows.toLocaleString()} windows</span><div class="profile-bands">${bands.map((name, index) => `<div><small>${name}</small><b>${(100 * profile.medianBandShares[index]).toFixed(1)}%</b></div>`).join('')}</div></div>`).join('');
  $('comparison').innerHTML = report.comparison.map(row => `<tr><td>${row.patterns}</td><td>${row.gmmSilhouette ?? '—'}</td><td>${row.kmeansSilhouette ?? '—'}</td><td>${row.gmmBic.toLocaleString()}</td><td>${row.agreementAri}</td></tr>`).join('');
  const [two, three] = report.comparison;
  $('comparison-note').textContent = `In this archive, ${two.gmmBic < three.gmmBic ? 'two' : 'three'} groups have the lower GMM BIC; ${two.gmmSilhouette > three.gmmSilhouette ? 'two' : 'three'} groups have the higher GMM silhouette. Keep the groups unnamed until activity labels and waveform review support an interpretation.`;
  const days = [...new Set(state.periods.map(period => localDay(period.slot)))].reverse();
  $('day').innerHTML = '<option value="all">All days</option>' + days.map(day => `<option value="${day}">${day}</option>`).join('');
}
function visiblePeriods() {
  const day = $('day').value;
  return state.periods.filter(period => day === 'all' || localDay(period.slot) === day);
}
function renderTimeline(periods) {
  const canvas = $('timeline');
  if (!periods.length) return;
  const bySlot = new Map(periods.map(period => [period.slot, period]));
  const first = periods[0].slot, last = periods[periods.length-1].slot;
  state.timelineSlots = [];
  for (let slot = first; slot <= last; slot += SLOT_MS) state.timelineSlots.push(bySlot.get(slot) || null);
  const cssWidth = Math.max(720, state.timelineSlots.length * 13);
  const cssHeight = 110, ratio = window.devicePixelRatio || 1;
  canvas.style.width = `${cssWidth}px`;
  canvas.style.height = `${cssHeight}px`;
  canvas.width = Math.round(cssWidth * ratio);
  canvas.height = Math.round(cssHeight * ratio);
  const ctx = canvas.getContext('2d');
  ctx.scale(ratio, ratio);
  ctx.fillStyle = '#f7faf8'; ctx.fillRect(0,0,cssWidth,cssHeight);
  const width = cssWidth / state.timelineSlots.length;
  state.timelineSlots.forEach((period, index) => {
    const x = index * width + 1, barWidth = Math.max(1, width - 2);
    if (!period) { ctx.fillStyle = COLORS.empty; ctx.fillRect(x,8,barWidth,94); return; }
    const total = period.windows.length;
    let top = 8;
    for (const [key,color] of [['one',COLORS.one],['two',COLORS.two],['flagged',COLORS.flagged]]) {
      const height = 94 * period[key] / total;
      ctx.fillStyle = color; ctx.fillRect(x,top,barWidth,height);
      top += height;
    }
    if (state.selected === period.slot) { ctx.strokeStyle = '#183e37'; ctx.lineWidth = 2; ctx.strokeRect(x-1,7,barWidth+2,96); }
  });
  $('timeline-axis').textContent = `${localTime(first)} — ${localTime(last + SLOT_MS)}`;
}
function renderPeriods() {
  const periods = visiblePeriods();
  renderTimeline(periods);
  const shown = [...periods].reverse().slice(0,200);
  const totalRecorded = state.report.extraction.recordedPeriods ?? periods.length;
  $('period-count').textContent = shown.length === periods.length ?
    `${periods.length} of ${totalRecorded} recorded periods have complete windows` :
    `Latest ${shown.length} of ${periods.length} analyzable periods (${totalRecorded} recorded)`;
  $('periods').innerHTML = shown.map(period => {
    const labels = labelFor(period.slot);
    const count = period.windows.length;
    const eeg = '/eeg.html?' + new URLSearchParams({start:period.windows[0].start});
    const label = '/?' + new URLSearchParams({labelStart:new Date(period.slot).toISOString(),labelEnd:new Date(period.slot+SLOT_MS).toISOString()}) + '#activity';
    const canLabel = !labels.length && period.slot + SLOT_MS <= Date.now();
    return `<tr><td><strong>${esc(localTime(period.slot))}</strong><small>30-minute slot</small></td><td>${(count*4).toLocaleString()} sec</td><td>${percent(period.one,count)}</td><td>${percent(period.two,count)}</td><td>${percent(period.flagged,count)}</td><td>${labels.length ? esc(labels.join(', ')) : canLabel ? `<a href="${esc(label)}">Assign activity ↗</a>` : 'In progress'}</td><td><button type="button" data-slot="${period.slot}">Windows</button> · <a href="${esc(eeg)}">EEG ↗</a></td></tr>`;
  }).join('');
}
function renderWindows() {
  const period = state.periods.find(item => item.slot === state.selected);
  if (!period) { $('window-section').hidden = true; return; }
  $('window-section').hidden = false;
  $('window-description').textContent = `${localTime(period.slot)} · ${period.windows.length} windows · ${period.flagged} flagged · ${period.uncertain} low-confidence assignments`;
  const windows = period.windows;
  const pageCount = Math.ceil(windows.length / WINDOW_PAGE_SIZE);
  state.windowPage = Math.min(state.windowPage, pageCount - 1);
  const page = windows.slice(state.windowPage * WINDOW_PAGE_SIZE, (state.windowPage+1) * WINDOW_PAGE_SIZE);
  const flagNames = {highAmplitude:'High amplitude',largeStep:'Large step',flatline:'Flat signal'};
  $('windows').innerHTML = page.map(window => {
    const flags = window.qualityFlags.map(flag => flagNames[flag] || flag).join(', ');
    const eeg = '/eeg.html?' + new URLSearchParams({start:window.start});
    return `<tr><td>${esc(new Date(window.start).toLocaleTimeString())}</td><td>${flags ? `<span class="flag">${esc(flags)}</span>` : 'Unflagged'}</td><td>${window.gmmPattern ? `Pattern ${window.gmmPattern}` : '—'}</td><td>${window.kmeansPattern ? `Pattern ${window.kmeansPattern}` : '—'}</td><td>${window.confidence === null ? '—' : percent(window.confidence,1)}</td><td>${window.rms.toFixed(1)}</td><td><a href="${esc(eeg)}">View EEG ↗</a></td></tr>`;
  }).join('');
  $('window-page').textContent = `Page ${state.windowPage+1} of ${pageCount}`;
  $('older-windows').disabled = state.windowPage === 0;
  $('newer-windows').disabled = state.windowPage >= pageCount-1;
}
function selectPeriod(slot) {
  state.selected = slot;
  state.windowPage = 0;
  renderPeriods();
  renderWindows();
  $('window-section').scrollIntoView({behavior:'smooth',block:'start'});
}
$('day').addEventListener('change', () => { state.selected = null; $('window-section').hidden = true; renderPeriods(); });
$('periods').addEventListener('click', event => {
  const button = event.target.closest('[data-slot]');
  if (button) selectPeriod(Number(button.dataset.slot));
});
$('timeline').addEventListener('click', event => {
  const rectangle = $('timeline').getBoundingClientRect();
  const index = Math.floor((event.clientX - rectangle.left) / rectangle.width * state.timelineSlots.length);
  const period = state.timelineSlots[index];
  if (period) selectPeriod(period.slot);
});
$('close-windows').addEventListener('click', () => { state.selected = null; $('window-section').hidden = true; renderPeriods(); });
$('older-windows').addEventListener('click', () => { state.windowPage--; renderWindows(); });
$('newer-windows').addEventListener('click', () => { state.windowPage++; renderWindows(); });
async function load() {
  try {
    const response = await fetch('/api/patterns', {cache:'no-store'});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
    if (!result.available) {
      $('participant').textContent = result.participantId || 'No participant selected';
      $('empty').hidden = false;
      return;
    }
    state.report = result;
    state.periods = summarizeWindows(result);
    $('report').hidden = false;
    renderOverview();
    renderPeriods();
  } catch (error) {
    $('notice').textContent = error.message;
    $('notice').classList.add('error');
    $('notice').hidden = false;
  }
}
load();
