'use strict';
const similarityNumber=value=>Number(value).toLocaleString(undefined,{maximumSignificantDigits:5});
let similaritySequence=0,similarityResult=null,similarityDraft=null,similarityBusy=false;
function similarityClear(){similaritySequence++;similarityResult=null;similarityDraft=null;$('similarity-results').replaceChildren();$('similarity-form').hidden=true;$('similarity-confirm').checked=false;$('similarity-reference').replaceChildren(new Option('Choose a confirmed reference',''));$('similarity-status').textContent='Choose a confirmed reference to start.';$('similarity-scope').textContent='';}
function similarityBody(reference){return {participantId:payload.participantId,group:payload.acquisitionGroupId,view:payload.view,identity:currentKey,reference,spacingMinutes:Number($('similarity-spacing').value)};}
async function similarityReferences(){
  if(!payload)return;const sequence=++similaritySequence;
  try{const result=await request('/api/similarity/search',similarityBody());if(sequence!==similaritySequence)return;
    $('similarity-reference').replaceChildren(new Option('Choose a confirmed reference',''),...result.references.map(r=>new Option(`${r.label.label} · ${fmt.format(r.startMs)} · timing ${r.label.timing_precision}`,r.windowId)));
    $('similarity-status').textContent=result.references.length?`${num(result.references.length)} selected windows inside confirmed, finished activities. Choose one or use Find similar in the window table.`:'No selected windows inside a confirmed, finished activity. Confirm an activity in Episode review, then refresh analysis. No activity is inferred automatically.';
  }catch(e){if(sequence===similaritySequence)$('similarity-status').textContent=e.message;}
}
async function similaritySearch(reference){
  if(!payload||similarityBusy)return;
  if(!reference){$('similarity-status').textContent='Choose a confirmed reference first.';return;}
  const sequence=++similaritySequence;similarityResult=null;similarityDraft=null;$('similarity-form').hidden=true;$('similarity-results').replaceChildren();$('similarity-saved').textContent='';$('similarity-status').textContent='Finding similar full-feature windows…';
  try{const result=await request('/api/similarity/search',similarityBody(reference));if(sequence!==similaritySequence)return;similarityResult=result;
    $('similarity-reference').value=reference;const c=result.counts,r=result.reference;
    $('similarity-status').textContent=`Top ${c.shown} of ${c.spacedCandidates} spaced candidates · ${c.eligible} eligible windows · ${c.spacingSuppressed} suppressed by spacing · ${c.guardExcluded} reference/nearby excluded · ${c.selected} selected. ${c.limitOmitted} further spaced candidates not shown.`;
    const a=result.acquisition;$('similarity-scope').textContent=`Reference: ${r.label.label} · ${fmt.format(r.startMs)} → ${fmt.format(r.endMsExclusive)} (end exclusive) · origin ${r.label.source}, ${r.label.review_state}, timing ${r.label.timing_precision}. ${a.source} · ${a.device_id} · ${a.placement} ear · ${a.channel} · ${a.sample_rate} Hz · session ${a.session_id||'not recorded'}. ${result.metric}. Sampling and quality exclusions are listed above. Changing features changes the distance ranking.`;
    for(const [i,item] of result.items.entries()){
      const card=document.createElement('article');card.append(text('h3',`${i+1}. ${fmt.format(item.startMs)} → ${fmt.format(item.endMsExclusive)}`));
      card.append(text('p',`Distance ${similarityNumber(item.distance)} · ${item.quality} · ${item.annotation.reason}.`));
      for(const label of item.annotation.labels)card.append(text('p',`${label.label} · ${label.source} · ${label.review_state} · timing ${label.timing_precision}`));
      if(!item.annotation.labels.length&&!item.annotation.unknown.length)card.append(text('p','No existing activity label.'));
      const details=document.createElement('details');details.append(text('summary','Why this distance? Compare all features'));const scroll=document.createElement('div');scroll.className='table-scroll';const table=document.createElement('table'),header=document.createElement('tr');for(const name of ['Feature','Reference','Match','Difference','IQR scale','Scaled difference','Share of squared distance'])header.append(text('th',name));const head=document.createElement('thead');head.append(header);table.append(head);const tbody=document.createElement('tbody');
      for(const d of item.differences){const tr=document.createElement('tr');for(const value of [d.feature,similarityNumber(d.reference),similarityNumber(d.candidate),similarityNumber(d.delta),similarityNumber(d.iqrScale),similarityNumber(d.scaledDelta),`${num(d.distanceShare*100)}%`])tr.append(text('td',value));tbody.append(tr);}table.append(tbody);scroll.append(table);details.append(scroll);card.append(details);
      const wave=waveformButton(item.windowId);wave.addEventListener('click',()=>showWaveform(item.windowId));card.append(wave);
      const review=text('button','Review activity suggestion');review.className='button subtle';review.type='button';review.disabled=!item.annotation.canAssign;review.addEventListener('click',()=>{similarityDraft=item;$('similarity-label').value=result.reference.label.label;$('similarity-confirm').checked=false;$('similarity-target').textContent=`${fmt.format(item.startMs)} → ${fmt.format(item.endMsExclusive)} (end exclusive). Suggested from ${result.reference.label.label}; verify what you actually performed.`;$('similarity-form').hidden=false;$('similarity-label').focus();});card.append(review);$('similarity-results').append(card);
    }
  }catch(e){if(sequence===similaritySequence)$('similarity-status').textContent=e.message;}
  $('similarity-section').scrollIntoView({behavior:'smooth',block:'start'});
}
$('similarity-search').addEventListener('click',()=>similaritySearch($('similarity-reference').value));
for(const id of ['similarity-reference','similarity-spacing'])$(id).addEventListener('change',()=>{similaritySequence++;similarityResult=null;similarityDraft=null;$('similarity-form').hidden=true;$('similarity-results').replaceChildren();$('similarity-scope').textContent='';$('similarity-status').textContent='Selection changed. Click Find similar periods to search.';});
$('windows').addEventListener('click',e=>{const reference=e.target.closest('[data-reference]')?.dataset.reference;if(reference)similaritySearch(reference);});
$('similarity-dismiss').addEventListener('click',()=>{similarityDraft=null;$('similarity-form').hidden=true;});
$('similarity-form').addEventListener('submit',async e=>{
  e.preventDefault();if(similarityBusy||!similarityResult||!similarityDraft||!payload)return;
  if(!$('similarity-confirm').checked){$('similarity-saved').textContent='Review and confirm this activity first.';return;}
  const sequence=similaritySequence;similarityBusy=true;$('similarity-save').disabled=true;
  try{const body={...similarityBody(similarityResult.reference.windowId),target:similarityDraft.windowId,proposalId:similarityDraft.proposalId,reviewed:true,label:$('similarity-label').value};const result=await request('/api/similarity/accept',body);$('similarity-saved').textContent=result.message;if(sequence===similaritySequence)similarityClear();await refresh();}
  catch(error){$('similarity-saved').textContent=error.message;}
  finally{similarityBusy=false;$('similarity-save').disabled=false;}
});
window.addEventListener('exploration-cleared',similarityClear);window.addEventListener('exploration-ready',similarityReferences);
