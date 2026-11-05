/* Guided entry point. The server owns support/readiness; this surface renders it. */
'use strict';
(()=>{
 const byId=id=>document.getElementById(id);
 const card=byId('journey-card');
 if(!card)return;
 let current=null,loading=false,starting=false,actionError=null;
 const copy={
  connect:['Connect your recordings','Enter your AWEAR key to start bringing your recordings onto this computer.','Connect','/welcome.html'],
  waiting:['Waiting for recordings','Recordings will appear below as they arrive. Keep your AWEAR mobile app syncing. There is nothing else to set up.','',null],
  prepare:['Your recordings are here','When you’re ready, prepare a first look at your recording quality and daily changes. No activity labels or research question needed.','Explore my recordings',null],
  preparing:['Preparing your first look','You can keep viewing your recordings while the analysis runs on this computer.','',null],
  ready:['Your recordings are ready to explore','Start with how your signal changes during a day. Activity labels and questions can wait.','Explore my day','/discover.html#daily-discovery'],
  review:['Let’s check the recording first','The analysis could not find unflagged signal windows to explore yet. Check coverage and screening cues, or let more recordings arrive.','Review recording quality','/quality.html'],
  retry:['Preparation needs another try','Your saved recordings are safe. Retry preparation, or open recording quality for details.','Try again',null],
  unavailable:['Your recordings are available','This build cannot prepare insights. You can keep viewing and labeling recordings, or install the current MindTrail release with analysis included.','Get the current release','https://github.com/synaptixs/MindTrail/releases/tag/v0.2.8']
 };
 function render(value){
  if(current&&current.participantId!==value.participantId)actionError=null;
  current=value;
  let [title,note,label,href]=copy[value.stage]||copy.waiting;
  if(value.stage==='waiting'&&value.mode==='bluetooth'){title='Your Bluetooth workspace is ready';note='Record directly from your device in Bluetooth. These captures stay separate from cloud insights.';label='Open Bluetooth';href='/bluetooth.html';}
  if(value.stage==='waiting'&&value.mode==='offline'){title='No cloud recordings in this archive';note='This workspace is offline. You can view saved Bluetooth sessions under More tools, or connect an AWEAR key in Settings.';}
  byId('journey-title').textContent=title;byId('journey-note').textContent=note;
  const button=byId('journey-action'),link=byId('journey-link');
  button.hidden=!label||!!href;button.disabled=starting||!value.canPrepare;button.textContent=label;
  link.hidden=!label||!href;link.textContent=label;if(href)link.href=href;
  byId('journey-options').hidden=!value.canExplore;
  byId('journey-days').hidden=!value.canCompareDays;
  byId('journey-records').textContent=value.demo?'Synthetic demo recordings · not personal EEG':value.recordings.count?`${Number(value.recordings.count).toLocaleString()} saved one-second records · right-ear cloud EEG`:'Right-ear cloud recordings · saved on this computer';
  byId('journey-progress').hidden=value.stage!=='preparing';
  card.dataset.stage=value.stage;
 }
 async function request(url,body){
  const response=await fetch(url,{cache:'no-store',...(body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Awear-Local':'1'},body:JSON.stringify(body)})});
  const data=await response.json();if(!response.ok)throw new Error(data.error||'Could not load recordings. Please try again.');return data;
 }
 async function refresh(){
  if(loading||starting)return;loading=true;
  try{render(await request('/api/workspace'));byId('journey-error').hidden=!actionError;byId('journey-error-text').textContent=actionError||'We couldn’t check your workspace.';}
  catch(error){current=null;byId('journey-options').hidden=true;byId('journey-action').hidden=true;byId('journey-link').hidden=true;byId('journey-progress').hidden=true;byId('journey-title').textContent='Your workspace could not be checked';byId('journey-note').textContent='Your saved recordings are unchanged. Try again in a moment.';byId('journey-error').hidden=false;}
  finally{loading=false;}
 }
 byId('journey-action').addEventListener('click',async()=>{
  if(starting||!current?.canPrepare)return;
  starting=true;actionError=null;byId('journey-action').disabled=true;
  try{
   // Preserve any previously chosen range/options. Guided preparation does not
   // silently enable clustering, reset history, or keep a timer running unseen.
   const analysis=await request('/api/analysis/state');
   if(analysis.participantId!==current.participantId)throw new Error('The recording source changed. Refresh and try again.');
   await request('/api/analysis/start',{...(analysis.settings||{}),participantId:current.participantId,timezone:analysis.settings?.timezone||Intl.DateTimeFormat().resolvedOptions().timeZone});
  }catch(error){actionError=error.message;}
  finally{starting=false;await refresh();}
 });
 byId('journey-retry').addEventListener('click',()=>{actionError=null;refresh();});
 // Surface a live job without repeated starts; local refresh stops with the page.
 refresh();setInterval(refresh,3000);
})();
