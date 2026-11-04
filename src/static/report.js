'use strict';
(()=>{
 const el=id=>document.getElementById(id),url=new URL(location.href);
 let state=null,report=null,dirty=false,saving=false,sequence=0,historyOffset=0,questionsKey=null,viewDirty=false;
 let applied={question:url.searchParams.get('question')||'',lens:url.searchParams.get('lens')||'recurrence_conditions'};
 const labels={continue_discovery:'Keep exploring without mandatory labels',collect_context:'Collect more data or optional context',plan_matched_recording:'Plan matched recordings later',insufficient_evidence:'Insufficient evidence for a next question'};
 async function request(path,body){const r=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Awear-Local':'1'},body:JSON.stringify(body)});const v=await r.json();if(!r.ok)throw Error(v.error||'Request failed');return v;}
 function message(v){el('report-message').textContent=v;}
 function options(target,questions,selected,empty){target.replaceChildren(new Option(empty,''),...questions.map(q=>new Option(q.title,q.id)));if(selected&&!questions.some(q=>q.id===selected))target.add(new Option('Unavailable question — choose another',selected));target.value=selected;}
 function withhold(text){report=null;el('report-body').replaceChildren();el('report-fields').disabled=true;el('report-html').hidden=true;el('report-json').hidden=true;el('report-bundle').hidden=true;el('report-status').textContent=text;el('report-view-message').textContent='The selected question and evidence view are retained. No report or export is available until current evidence can be shown.';}
 async function history(reset=false){if(reset)historyOffset=0;const person=state?.participantId;try{const v=await request('/api/discovery/decision?offset='+historyOffset);if(person!==state?.participantId||v.participantId!==person)return;el('report-history-count').textContent=`Showing ${v.shown} of ${v.total} direction revisions (offset ${v.offset}). Saved choices retain their original evidence snapshot.`;el('report-history').replaceChildren();for(const r of v.history){const p=document.createElement('p');p.textContent=`Revision ${r.revision} · ${labels[r.choice]} · ${new Date(r.changedAt).toLocaleString()}\n${r.notes||'No reason entered'}${r.question?'\nQuestion: '+r.question.title:''}`;el('report-history').append(p);}el('report-more-history').hidden=!v.hasMore;}catch(e){message(e.message);}}
 async function refresh(force=false){
  if(saving)return;
  let draft=force&&dirty?{choice:el('report-choice').value,notes:el('report-notes').value,question:el('report-question').value}:null;
  const token=++sequence,selection={...applied};
  try{
   const [s,v,questions]=await Promise.all([request('/api/analysis/state'),request('/api/discovery/report/view?'+new URLSearchParams(selection)).catch(e=>({error:e.message})),request('/api/discovery/questions').catch(e=>({error:e.message}))]);
   if(token!==sequence)return;
   const changed=state?.participantId!==s.participantId;state=s;
   const busy=s.job&&['running','canceling'].includes(s.job.state);el('report-analyze').disabled=!s.available||!s.participantId||busy;
   if(changed){draft=null;report=null;dirty=false;viewDirty=false;questionsKey=null;el('report-notes').value='';el('report-choice').value='';el('report-body').replaceChildren();await history(true);}
   if(!questions.error&&questions.participantId===s.participantId){
    const key=JSON.stringify(questions.questions);
    if(key!==questionsKey){const chosen=viewDirty?el('report-view-question').value:selection.question;options(el('report-view-question'),questions.questions,chosen,'Starter investigation (not saved)');questionsKey=key;}
   }else{questionsKey=null;options(el('report-view-question'),[],selection.question,'Starter investigation (not saved)');}
   if(!viewDirty)el('report-lens').value=selection.lens;
   if(v.error){withhold(v.error);return;}
   if(v.participantId!==s.participantId||v.status!=='current'){
    withhold(busy?`Analysis ${s.job.stage}…`:v.status==='stale'?'Source changed. Analyze saved recordings to refresh the report. Saved directions remain in history.':s.available?'No current report. Analyze saved recordings; labels and a direction are optional.':'Numerical reports are unavailable in this build; saved directions remain readable.');return;
   }
   if(v.snapshotIdentity===report?.snapshotIdentity&&!force)return;
   if(dirty&&!force){el('report-body').hidden=true;el('report-fields').disabled=true;el('report-html').hidden=true;el('report-json').hidden=true;el('report-bundle').hidden=true;el('report-status').textContent='Evidence or direction changed. Your draft is preserved. Refresh the report to review the new snapshot before saving.';return;}
   report=v;dirty=!!draft;el('report-body').hidden=false;el('report-body').innerHTML=v.html;el('report-fields').disabled=false;
   el('report-choice').value=draft?draft.choice:v.decision.record?.choice||'';el('report-notes').value=draft?draft.notes:v.decision.record?.notes||'';
   options(el('report-question'),v.questions,draft?draft.question:v.decision.record?.question?.id||'','No question tied to direction');
   el('report-status').textContent='Current source and human-evidence snapshot. Direction: '+v.decisionRelation.replaceAll('_',' ')+'.';
   for(const kind of ['html','json']){el('report-'+kind).href='/api/export/discovery-report.'+kind+'?'+new URLSearchParams({...selection,identity:v.snapshotIdentity});el('report-'+kind).hidden=false;}
   el('report-bundle').href='/api/export/analysis-bundle.zip?'+new URLSearchParams({...selection,identity:v.snapshotIdentity});el('report-bundle').hidden=false;
   if(!viewDirty)el('report-view-message').textContent=draft?'Report refreshed. Your direction draft is preserved; review the evidence before saving.':'Displayed report and downloads use the applied question and evidence view. Viewing does not save or revalidate a question.';
   await history(true);
  }catch(e){withhold(e.message);}
 }
 el('report-view-form').addEventListener('input',()=>{viewDirty=true;el('report-view-message').textContent='Selection not applied yet. The displayed report and downloads still use the previous applied selection.';});
 el('report-view-form').addEventListener('submit',async event=>{event.preventDefault();if(saving)return;applied={question:el('report-view-question').value,lens:el('report-lens').value};viewDirty=false;window.history.replaceState(null,'','/report.html?'+new URLSearchParams(applied)+location.hash);await refresh(true);});
 el('report-decision-form').addEventListener('input',()=>{dirty=true;});
 el('report-decision-form').addEventListener('submit',async event=>{event.preventDefault();if(!report||saving)return;saving=true;el('report-save').disabled=true;try{await request('/api/discovery/decision',{participantId:state.participantId,evidenceIdentity:report.evidenceIdentity,revision:report.decision.revision,choice:el('report-choice').value,questionId:el('report-question').value||null,notes:el('report-notes').value});dirty=false;saving=false;await refresh(true);message('Direction recorded. No inference has run and no confirmation dates were reserved.');}catch(e){message(e.message);}finally{saving=false;el('report-save').disabled=false;}});
 el('report-refresh').addEventListener('click',()=>refresh(true));el('report-analyze').addEventListener('click',async()=>{try{el('report-analyze').disabled=true;await request('/api/analysis/start',state?.settings||{timezone:'America/New_York'});await refresh();}catch(e){message(e.message);}});
 el('report-more-history').addEventListener('click',()=>{historyOffset+=25;history();});refresh();setInterval(()=>refresh(),5000);
})();
