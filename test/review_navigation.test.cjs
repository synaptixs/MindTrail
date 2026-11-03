const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const T=require('../src/static/interval_time.js');
const source=fs.readFileSync(path.join(__dirname,'../src/static/review.js'),'utf8');
async function page({search='',labelParticipant='P'}={}){
  const elements=new Map(),calls=[];
  function element(id){if(!elements.has(id))elements.set(id,{value:'',textContent:'',innerHTML:'',checked:false,hidden:id==='editor',disabled:false,events:{},addEventListener(type,fn){this.events[type]=fn;},replaceChildren(){},querySelectorAll(){return[];},reset(){},focus(){},scrollIntoView(){}});return elements.get(id);}
  const pending={labelId:9,labelVersion:1,key:'pending',kind:'label',startMs:Date.parse('2026-10-14T14:00:00Z'),endMsExclusive:Date.parse('2026-10-14T14:30:00Z'),recordedMs:1800000,coveragePct:100,reason:'Existing interval needs review',rejectable:true};
  const label={id:10,participantId:labelParticipant,label:'Personal reading',note:'Original note',start:'2026-10-14T15:00:00Z',end:'2026-10-14T15:30:00Z',labelVersion:7,reviewState:'confirmed'};
  const fetch=async(url,options={})=>{
    calls.push({url,method:options.method||'GET'});
    let value;
    if(url==='/api/status')value={participantId:'P'};
    else if(url.startsWith('/api/review/queue?'))value={participantId:'P',items:[pending],total:1,offset:0,ordinaryTotal:0,candidateTotal:0,unknownTotal:0,rejectedTotal:0,invalidTimestampRecords:0,candidateStatus:'hidden',hasMore:false};
    else if(url.startsWith('/api/review/history?'))value={participantId:'P',operations:[],total:0,offset:0,hasMore:false};
    else if(url.startsWith('/api/activities?'))value={activities:[]};
    else if(url==='/api/review/label?id=10')value=label;
    else if(url.startsWith('/api/eeg/window?'))value={participantId:'P',records:[]};
    else throw new Error('Unexpected request '+url);
    return {ok:true,json:async()=>value};
  };
  const sandbox={document:{getElementById:element},MindTrailTime:T,fetch,location:{search},URLSearchParams,Option:class{},Date,Intl};
  await vm.runInNewContext(source,sandbox);
  return {element,calls,label};
}
test('blind queue identifies the saved interval without showing its activity name',async()=>{
  const p=await page();assert.match(p.element('queue').innerHTML,/Saved interval #9/);
  assert.doesNotMatch(p.element('queue').innerHTML,/Personal reading/);
  assert.equal(p.element('cues').checked,false);
});
test('open by number finds a confirmed interval absent from the pending queue, read-only',async()=>{
  const p=await page();p.element('interval-number').value='10';
  await p.element('interval-lookup').events.submit({preventDefault(){}});
  assert.equal(p.element('editor').hidden,false);assert.equal(p.element('editor-title').textContent,'Saved interval #10');
  assert.equal(p.element('name').value,p.label.label);assert.equal(p.element('note').value,p.label.note);
  assert.equal(p.element('start').value,T.input(Date.parse(p.label.start)));
  assert.equal(p.element('end').value,T.input(Date.parse(p.label.end)));
  assert.equal(p.element('interval-number').value,'10');assert.equal(p.element('cues').checked,true);
  assert.ok(p.calls.some(c=>c.url==='/api/review/label?id=10'));assert.ok(p.calls.every(c=>c.method==='GET'));
});
test('deep link and number lookup open the same saved interval',async()=>{
  const p=await page({search:'?labelId=10'});assert.equal(p.element('editor-title').textContent,'Saved interval #10');
  assert.equal(p.element('name').value,p.label.label);assert.ok(p.calls.every(c=>c.method==='GET'));
});
test('invalid and cross-participant lookups never populate the editor',async()=>{
  const p=await page();const before=p.calls.length;p.element('interval-number').value='1.5';
  await p.element('interval-lookup').events.submit({preventDefault(){}});
  assert.equal(p.calls.length,before);assert.equal(p.element('editor').hidden,true);
  const other=await page({labelParticipant:'OTHER'});other.element('interval-number').value='10';
  await other.element('interval-lookup').events.submit({preventDefault(){}});
  assert.equal(other.element('editor').hidden,true);assert.equal(other.element('name').value,'');
});
