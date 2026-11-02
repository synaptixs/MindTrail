'use strict';
const $=id=>document.getElementById(id);
let workspace;
function feedback(message,error=false){$('feedback').textContent=message;$('feedback').classList.toggle('error',error);$('feedback').hidden=!message;}
function confirmAction(message){
  $('confirmation-message').textContent=message;
  return new Promise(resolve=>{
    const finish=result=>{$('confirmation').close();$('confirm-accept').onclick=null;$('confirm-cancel').onclick=null;$('confirmation').oncancel=null;resolve(result);};
    $('confirm-accept').onclick=()=>finish(true);
    $('confirm-cancel').onclick=()=>finish(false);
    $('confirmation').oncancel=event=>{event.preventDefault();finish(false);};
    $('confirmation').showModal();$('confirm-cancel').focus();
  });
}
async function request(path,body){
  const response=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Awear-Local':'1'},body:JSON.stringify(body)});
  const result=await response.json();if(!response.ok)throw new Error(result.error||'Request failed. Please try again.');return result;
}
async function busy(action){
  const controls=[...document.querySelectorAll('button,input,select')];const previous=controls.map(c=>c.disabled);
  controls.forEach(c=>c.disabled=true);feedback('');
  try{await action();}catch(error){feedback(error.message,true);}
  finally{controls.forEach((c,i)=>c.disabled=previous[i]);}
}
function localTime(date){const offset=date.getTimezoneOffset()*60000;return new Date(date-offset).toISOString().slice(0,16);}
function showMembers(members){
  $('participant').replaceChildren(...members.map(member=>{const option=document.createElement('option');option.value=member.participantId;option.textContent=member.participantId;return option;}));
  if(members.some(m=>m.participantId===workspace.participantId))$('participant').value=workspace.participantId;
  $('source-form').hidden=false;
}
async function load(){
  workspace=await request('/api/desktop/state');
  $('api-key-guide').open=!workspace.keyConfigured;
  $('use-current-key').hidden=!workspace.keyConfigured||workspace.mode!=='api';
  $('data-directory').textContent=workspace.dataDirectory;
  $('remember-key').disabled=!workspace.secureStorageAvailable;
  $('remember-key').checked=workspace.secureStorageAvailable;
  $('storage-help').textContent=workspace.secureStorageAvailable?'Your key stays on this computer and is saved securely for next time.':'Secure storage is unavailable. Your key will stay in memory for this session only.';
  $('current-workspace').hidden=!workspace.ready;
  $('page-title').textContent=workspace.ready?'Your workspace, your way.':'Your recordings, connected.';
  $('source-settings').hidden=!workspace.keyConfigured||workspace.mode!=='api';
  $('connection-summary').textContent=workspace.mode==='bluetooth'?'Direct Bluetooth · local recording profile':'API sync · '+workspace.participantId;
  $('open-workspace').href=workspace.mode==='bluetooth'?'/bluetooth.html':'/';
  $('forget-key').hidden=!workspace.remembered;
  $('restore-section').hidden=workspace.ready;
  $('bluetooth-help').hidden=workspace.bluetoothAvailable;
  if(workspace.startupError)feedback(workspace.startupError,true);
  if(workspace.members.length)showMembers(workspace.members);
}
$('sync-start').value=localTime(new Date(Date.now()-7*86400000));
async function connect(body){
  feedback('Connecting to AWEAR and finding your recordings…');
  await request('/api/desktop/connect',body);
  $('api-key').value='';location.assign('/');
}
$('key-form').addEventListener('submit',event=>{event.preventDefault();busy(()=>connect({key:$('api-key').value.trim(),remember:$('remember-key').checked}));});
$('use-current-key').addEventListener('click',()=>busy(()=>connect({remember:$('remember-key').checked})));
$('load-sources').addEventListener('click',()=>busy(async()=>{const result=await request('/api/participants');showMembers(result.members);const status=await request('/api/status');if(status.syncStart)$('sync-start').value=localTime(new Date(status.syncStart));}));
$('source-form').addEventListener('submit',event=>{event.preventDefault();busy(async()=>{
  await request('/api/select',{participantId:$('participant').value,syncStart:new Date($('sync-start').value).toISOString()});location.assign('/');
});});
$('bluetooth-setup').addEventListener('click',()=>busy(async()=>{await request('/api/desktop/bluetooth',{});location.assign('/bluetooth.html');}));
$('forget-key').addEventListener('click',async()=>{if(await confirmAction('Remove the saved API key from this computer? Your recordings and labels will stay.'))busy(async()=>{await request('/api/desktop/forget-key',{});await load();feedback('Saved key removed. Enter a key again when you want to sync.');});});
$('quit').addEventListener('click',async()=>{if(await confirmAction('Quit MindTrail Lab? Any active Bluetooth recording will stop and be saved.'))busy(async()=>{await request('/api/desktop/quit',{});$('connect-card').hidden=true;$('advanced').hidden=true;$('current-workspace').hidden=true;$('restore-section').hidden=true;feedback('MindTrail Lab has stopped. You can close this tab and open the app whenever you’re ready.');});});
$('restore').addEventListener('click',()=>busy(async()=>{
  const file=$('archive-file').files[0];if(!file)throw new Error('Choose your AWEAR SQLite archive first.');
  feedback('Restoring your archive. Keep this tab open…');
  const response=await fetch('/api/desktop/import',{method:'POST',headers:{'Content-Type':'application/octet-stream','X-Awear-Local':'1'},body:file});
  const result=await response.json();if(!response.ok)throw new Error(result.error||'Restore failed.');
  $('archive-file').value='';await load();feedback('Archive restored. Connect your API key or choose Bluetooth to continue.');
}));
load().catch(error=>feedback(error.message,true));
