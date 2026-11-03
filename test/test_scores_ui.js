// Behavioral regression tests for browser-side observation timing, with no dependencies.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const code = fs.readFileSync(path.join(root, 'src/static/scores.js'), 'utf8');
const html = fs.readFileSync(path.join(root, 'src/static/scores.html'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));
async function screen(search = '') {
  let now = Date.parse('2026-05-02T12:00:00Z');
  class ClockDate extends Date {
    constructor(...args) { super(...(args.length ? args : [now])); }
    static now() { return now; }
  }
  const elements = {}, posts = [], requests = [], intervals = [];
  for (const [,id] of html.matchAll(/id="([^"]+)"/g)) elements[id] = {
    value:'',checked:id==='capture-now',hidden:false,disabled:false,handlers:{},
    addEventListener(name, fn) { this.handlers[name] = fn; },
    classList:{toggle() {}}, scrollIntoView() {},
  };
  elements.uncertainty.value = '0';
  elements['score-form'].reset = () => {
    for (const id of ['score','mobile-state','app-version','score-note']) elements[id].value='';
    elements['capture-now'].checked=true; elements.uncertainty.value='0';
  };
  const context=vm.createContext({Date:ClockDate,URLSearchParams,Intl,console,
    document:{getElementById:id=>elements[id],hidden:false}, location:{search},
    confirm:()=>true,setInterval:fn=>intervals.push(fn),
    fetch:async (url, options) => {
      requests.push(url);
      if (options.method==='POST') posts.push({url,body:JSON.parse(options.body)});
      return {ok:true,json:async()=>url==='/api/status' ? {demo:false} :
        {participantId:'P-TEST',total:0,observations:[],nextBefore:null}};
    }});
  vm.runInContext(code,context); await flush();
  return {elements,posts,requests,intervals,advance:ms=>now+=ms,
    async input(value) {elements.score.value=value;elements.score.handlers.input();await flush();},
    async save() {elements['mobile-state'].value='calm';await elements['score-form'].handlers.submit({preventDefault(){}});await flush();},
    run:source=>vm.runInContext(source,context)};
}
test('typing marks the observation time; saving later does not move it',async()=>{
  const s=await screen();await s.input('52');const marked=s.run('state.captureTime');
  s.advance(15000);await s.save();assert.equal(s.posts[0].body.observedAt,marked);
  assert.equal(s.posts[0].body.score,52);
});
test('an explicit time mark survives later typing and coverage refresh',async()=>{
  const s=await screen();s.elements['use-now'].handlers.click();const marked=s.run('state.captureTime');
  s.advance(10000);await s.input('37');s.intervals[0]();await flush();
  assert.equal(s.run('state.captureTime'),marked);assert.equal(s.elements.score.value,'37');
  await s.save();assert.equal(s.posts[0].body.observedAt,marked);
});
test('a past observation prefilled from EEG retains its time when saved',async()=>{
  const time='2026-05-01T10:11:12.500Z';const s=await screen('?observedAt='+encodeURIComponent(time));
  await s.input('44');s.advance(60000);await s.save();
  assert.equal(s.posts[0].body.observedAt,time);
});
test('a missing marked time is reported and never silently replaced with Save time',async()=>{
  const s=await screen();s.elements.score.value='50';await s.save();
  assert.equal(s.posts.length,0);assert.match(s.elements.notice.textContent,/Mark the observation time/);
});
test('automatic refresh does not discard loaded older pages',async()=>{
  const s=await screen();s.run('state.loadedOlder = true');const before=s.requests.length;
  s.intervals[0]();await flush();assert.equal(s.requests.length,before);
});

test('switching from an unsaved draft to editing does not reuse the draft timestamp',async()=>{
  const s=await screen();await s.input('45');const draftTime=s.run('state.captureTime');
  s.run(`state.rows=[{id:7,observedAt:'2026-05-01T00:00:00Z',score:32,state:'engaged',uncertaintySeconds:0,appVersion:'',note:''}]`);
  await s.elements['score-rows'].handlers.click({target:{closest:selector=>selector==='[data-edit]' ? {dataset:{edit:'7'}} : null}});
  s.elements['capture-now'].checked=true;s.advance(15000);await s.input('60');await s.save();
  assert.notEqual(s.posts[0].body.observedAt,draftTime);
  assert.equal(s.posts[0].url,'/api/mobile-scores/7/edit');
});
test('empty chart retains score axes and explains missing phone readings without inventing points',async()=>{
  const s=await screen();const chart=s.elements['score-chart'].innerHTML;
  assert.match(chart,/SCORE \(0–100\)/);
  assert.match(chart,/No phone scores recorded/);
  assert.match(chart,/Record a reading below/);
  assert.doesNotMatch(chart,/<circle/);
  assert.equal(s.elements['latest-mobile-score'].textContent,'No readings yet');
  assert.ok(html.indexOf('id="mobile-chart-title"')<html.indexOf('id="score-form"'));
});
test('a single recorded score is visible inside the plot with a value and EEG link',async()=>{
  const s=await screen();s.run(`state.rows=[{observedAt:'2026-05-01T12:00:00Z',score:62,state:'relaxed',uncertaintySeconds:0}];renderChart()`);
  const chart=s.elements['score-chart'].innerHTML;
  const x=Number(chart.match(/<circle cx="([^"]+)"/)[1]);
  assert.ok(x>100&&x<800,'A lone point needs horizontal space on both sides');
  assert.match(chart,/>62<\/text>/);
  assert.match(chart,/href="\/eeg.html\?/);
  assert.match(chart,/seconds=60/);
  assert.equal(s.elements['latest-mobile-score'].textContent,'62 / 100 · relaxed');
});
test('score bounds and recorded states render without interpolating missing observations',async()=>{
  const s=await screen();s.run(`state.rows=[{observedAt:'2026-05-01T12:01:00Z',score:100,state:'calm',uncertaintySeconds:5},{observedAt:'2026-05-01T12:00:00Z',score:0,state:'alert',uncertaintySeconds:0}];renderChart()`);
  const chart=s.elements['score-chart'].innerHTML;
  assert.equal([...chart.matchAll(/<circle /g)].length,2);
  assert.doesNotMatch(chart,/NaN|<polyline/);
  assert.match(chart,/>100<\/text>/);assert.match(chart,/>0<\/text>/);
  assert.match(chart,/fill="#3f9774"/);assert.match(chart,/fill="#bd8050"/);
});
