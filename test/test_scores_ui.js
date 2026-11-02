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
