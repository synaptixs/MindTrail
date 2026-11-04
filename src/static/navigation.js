'use strict';
(()=>{
 function revealHash(){
  const name=location.hash.slice(1);
  const target=document.getElementById(name);
  if(!target)return;
  for(let parent=target.parentElement;parent;parent=parent.parentElement){if(parent.tagName==='DETAILS')parent.open=true;}
  target.scrollIntoView({block:'start'});
 }
 window.addEventListener('hashchange',revealHash);
 if(location.hash)revealHash();
 // Existing precise-selection/edit actions still reveal the optional editor.
 document.addEventListener('click',event=>{
  if(event.target.closest('[data-day-assign],[data-edit],[data-pick-start]')){
   const editor=document.getElementById('activity-tools');if(editor)editor.open=true;
  }
 },true);
 if(new URLSearchParams(location.search).has('labelStart')){
  const editor=document.getElementById('activity-tools');if(editor)editor.open=true;
 }
})();
