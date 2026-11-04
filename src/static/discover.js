'use strict';
const $=id=>document.getElementById(id);
let snapshot=null,participant=null,editing=null,sequence=0,pending=false,renderedIdentity=null,questionKey=null,analysisOptions=null;
const num=n=>Number(n).toLocaleString();
function node(tag,value){const e=document.createElement(tag);e.textContent=value;return e;}
async function request(path,body){const r=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Awear-Local':'1'},body:JSON.stringify(body)});const v=await r.json();if(!r.ok)throw new Error(v.error||'Request failed');return v;}
function reset(){editing=null;$('question').reset();$('question-action').textContent='Save question';}
function date(ms,zone){return ms===null?'Not recorded':new Intl.DateTimeFormat(undefined,{timeZone:zone,dateStyle:'medium',timeStyle:'long'}).format(ms);}
function render(value,state){
 analysisOptions=state.settings;
 window.dispatchEvent(new CustomEvent('mindtrail-discovery',{detail:{value,state}}));
 snapshot=value.status==='current'?value:null;
 $('source').textContent=state.demo?'Synthetic demo · no personal EEG':'Cloud EEG · right ear · RIGHT_TEMP · 256 Hz. Bluetooth files are excluded.';
 $('freshness').textContent=value.status==='current'?`Current snapshot · ${value.readiness.replaceAll('_',' ')} · labels are optional.`:({stale:'Recordings or analysis code changed. Refresh analysis before using discovery results.',no_participant:'Connect a source and select a participant to begin.',unavailable:'No complete discovery report. Run analysis; no activity labels are needed.'})[value.status]||'Discovery unavailable.';
 $('facts').hidden=!snapshot;$('question-save').disabled=!snapshot||pending;
 const busy=state.job&&['running','canceling'].includes(state.job.state);
 $('run').disabled=!state.available||!state.participantId||busy;
 $('analysis-status').textContent=busy?`Analysis ${state.job.stage}…`:state.job?.state==='failed'?state.job.error:state.available?'Analysis runs locally in the background.':'Analysis is unavailable in this build. Use a build with analysis included.';
 if(!snapshot){renderedIdentity=null;$('contexts').replaceChildren();$('coverage').replaceChildren();return;}
 if(renderedIdentity===value.discoveryIdentity)return;
 renderedIdentity=value.discoveryIdentity;
 const c=value.coverage;$('coverage').replaceChildren();
 for(const [label,count] of [['Source records',c.sourceRecords],['Computed windows',c.computedWindows],['Unflagged windows',c.unflaggedWindows],['Flagged windows',c.flaggedWindows],['Excluded records',c.excludedRecords],['Remainder record-seconds',c.boundaryRemainderRecordSeconds],['Failed record-seconds',c.featureFailureRecordSeconds]]){const row=node('tr','');row.append(node('th',label),node('td',num(count)));$('coverage').append(row);}
 $('extent').textContent=`Observed source extent: ${date(value.observedExtent.startMs,value.timezone)} → ${date(value.observedExtent.endMsExclusive,value.timezone)} (end exclusive). ${value.timezone}. Requested range: ${value.requestedRange.startMs===null?'all saved':date(value.requestedRange.startMs,value.timezone)} → ${value.requestedRange.endMsExclusive===null?'latest saved':date(value.requestedRange.endMsExclusive,value.timezone)}.`;
 $('identity').textContent=`Source revision ${value.sourceRevision} · analysis ${value.analysisIdentity} · discovery ${value.discoveryIdentity}`;
 $('contexts').replaceChildren();
 for(const group of value.contexts){const a=group.acquisition,article=node('article','');article.className='context';article.append(node('h3',`${a.source} · ${a.placement} ear · device ${a.device_id??'unknown'}`),node('p',`${a.channel??'Unknown channel'} · ${a.sample_rate} Hz · ${a.units} · clock ${a.timestamp_basis} · session ${a.session_id??'not recorded'}`),node('p',`${num(group.unflaggedWindows)} unflagged / ${num(group.computedWindows)} computed windows · ${num(group.unflaggedMs/1000)} unflagged window-seconds · ${num(group.dates.length)} observed dates`),node('p',`${group.readiness.replaceAll('_',' ')}. Dates and windows are not independent repetitions.`));$('contexts').append(article);}
 if(!value.contexts.length)$('contexts').append(node('p','No computed feature contexts. Inspect source exclusions and recording quality.'));
 $('context-count').textContent=`All ${value.contextCount} computed acquisition contexts shown separately. Contexts without computed windows contribute only to source/exclusion accounting.`;
 $('missing').textContent=`Contact: ${value.missingContext.contact}. ${num(value.missingContext.unknownSessionWindows)} computed windows have no record-linked session. Readiness does not use activity labels; time-based annotations are shown separately in the daily view. Inference: ${value.inference.status.replaceAll('_',' ')}. ${value.inference.reason}`;
}
async function refresh(){
 const token=++sequence;
 try{const [state,value,questions]=await Promise.all([request('/api/analysis/state'),request('/api/discovery'),request('/api/discovery/questions').catch(e=>({questions:[],total:0,error:e.message}))]);if(token!==sequence)return;
 if(state.participantId!==value.participantId||(!questions.error&&questions.participantId!==value.participantId)){window.dispatchEvent(new CustomEvent('mindtrail-discovery',{detail:{value:{status:'unavailable'},state:{}}}));snapshot=null;$('facts').hidden=true;$('question-save').disabled=true;$('questions').replaceChildren();questionKey=null;reset();return;}
 if(participant!==value.participantId){participant=value.participantId;questionKey=null;reset();}
 render(value,state);const nextQuestionKey=JSON.stringify([questions,!!snapshot]);if(nextQuestionKey===questionKey)return;questionKey=nextQuestionKey;$('questions').replaceChildren();
 for(const question of questions.questions){const article=node('article','');article.className='context';article.append(node('h3',question.title),node('p',`${question.stage} · revision ${question.revision} · saved against analysis ${question.analysisIdentity}`),node('p',question.notes));const button=node('button','Edit question');button.type='button';button.className='button subtle';button.disabled=!snapshot;button.addEventListener('click',()=>{editing=question;$('title').value=question.title;$('notes').value=question.notes;$('stage').value=question.stage;$('question-action').textContent='Save changes';$('title').focus();});article.append(button);const reportLink=node('a','Review evidence for this question →');reportLink.className='button subtle';reportLink.href='/report.html?'+new URLSearchParams({question:question.id,lens:'open_review'});article.append(reportLink);$('questions').append(article);}
 if(!questions.total)$('questions').append(node('p',questions.error||'No questions saved. You can explore first and add one when something interests you.'));
 }catch(e){window.dispatchEvent(new CustomEvent('mindtrail-discovery',{detail:{value:{status:'unavailable'},state:{}}}));snapshot=null;$('facts').hidden=true;$('question-save').disabled=true;$('freshness').textContent=e.message;}
}
$('question').addEventListener('submit',async event=>{event.preventDefault();if(!snapshot||pending)return;pending=true;$('question-save').disabled=true;
 const body={participantId:participant,analysisIdentity:snapshot.analysisIdentity,discoveryIdentity:snapshot.discoveryIdentity,title:$('title').value,notes:$('notes').value,stage:$('stage').value,...(editing?{id:editing.id,revision:editing.revision}:{})};
 try{await request('/api/discovery/questions',body);reset();$('save-status').textContent='Question saved separately from EEG and activity labels. Confirmation data is not reserved.';await refresh();}catch(e){$('save-status').textContent=e.message;}finally{pending=false;$('question-save').disabled=!snapshot;}});
$('reset-question').addEventListener('click',reset);$('refresh').addEventListener('click',refresh);
$('run').addEventListener('click',async()=>{try{$('run').disabled=true;await request('/api/analysis/start',{...(analysisOptions||{}),timezone:Intl.DateTimeFormat().resolvedOptions().timeZone});await refresh();}catch(e){$('analysis-status').textContent=e.message;}});
refresh();setInterval(refresh,3000);
