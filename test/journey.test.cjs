const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../src/static/journey.js'),'utf8');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
async function page(stage='prepare'){
 const elements=new Map(),calls=[];let poll,fail=false;
 const value={stage,participantId:'P',recordings:{count:12},canPrepare:stage==='prepare',canExplore:stage==='ready'};
 const el=id=>{if(!elements.has(id))elements.set(id,{hidden:false,textContent:'',dataset:{},addEventListener(name,fn){this[name]=fn;}});return elements.get(id);};
 const settings={startMs:1000,endMs:9000,normalize:true,exploration:false,activityComparisons:true,timezone:'America/New_York'};
 const fetch=async(url,options)=>{calls.push({url,options});let result;
  if(url==='/api/workspace')result={...value};
  else if(url==='/api/analysis/state')result={participantId:value.analysisParticipant||'P',settings};
  else if(url==='/api/analysis/start'){if(fail)return{ok:false,json:async()=>({error:'Analysis already running'})};result={};value.stage='preparing';value.canPrepare=false;}
  else throw Error(url);return{ok:true,json:async()=>result};};
 vm.runInNewContext(source,{document:{getElementById:el},fetch,setInterval(fn){poll=fn;},Intl,Number});
 await tick();return{el,calls,value,settings,poll:()=>poll(),fail:()=>{fail=true;}};
}
test('no background start; one explicit preparation preserves settings and does not require labels',async()=>{
 const p=await page();assert.equal(p.el('journey-title').textContent,'Your recordings are here');
 assert.equal(p.calls.some(c=>c.url.endsWith('/start')),false);
 await p.el('journey-action').click();
 const posts=p.calls.filter(c=>c.url.endsWith('/start'));assert.equal(posts.length,1);
 assert.deepEqual(JSON.parse(posts[0].options.body),{...p.settings,participantId:'P'});
 assert.equal(p.el('journey-progress').hidden,false);
 await p.poll();assert.equal(p.calls.filter(c=>c.url.endsWith('/start')).length,1);
});
test('choices follow live readiness and disappear when the source loses readiness',async()=>{
 const p=await page('ready');assert.equal(p.el('journey-options').hidden,false);
 p.value.stage='waiting';p.value.canExplore=false;await p.poll();
 assert.equal(p.el('journey-options').hidden,true);assert.equal(p.el('journey-action').hidden,true);
});
test('post errors survive a successful status refresh; a changed source never starts',async()=>{
 const p=await page();p.fail();await p.el('journey-action').click();
 assert.equal(p.el('journey-error').hidden,false);assert.equal(p.el('journey-error-text').textContent,'Analysis already running');
 await p.poll();assert.equal(p.el('journey-error').hidden,false);
 const q=await page();q.value.analysisParticipant='OTHER';await q.el('journey-action').click();
 assert.equal(q.calls.some(c=>c.url.endsWith('/start')),false);assert.match(q.el('journey-error-text').textContent,/source changed/);
});
