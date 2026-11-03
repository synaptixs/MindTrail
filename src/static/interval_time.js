/* Pure epoch geometry and explicit New York formatting, shared with Node tests. */
(function(root){
  'use strict';
  const zone='America/New_York';
  function input(ms){
    const parts=new Intl.DateTimeFormat('en-CA',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',fractionalSecondDigits:3,hourCycle:'h23'}).formatToParts(ms);
    const p=Object.fromEntries(parts.map(v=>[v.type,v.value]));return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}:${p.second}.${p.fractionalSecond}`;
  }
  function offset(ms){return new Intl.DateTimeFormat('en',{timeZone:zone,timeZoneName:'longOffset'}).formatToParts(ms).find(p=>p.type==='timeZoneName').value.replace('GMT','')||'+00:00';}
  function stamp(ms){return `${input(ms).replace('T',' ')} UTC${offset(ms)}`;}
  function move(bounds,current,edge,value,snap=true){
    const range={...(current||bounds)},minimum=Math.min(1,bounds.end-bounds.start),v=snap?Math.floor(value/1000)*1000:Math.round(value);
    range[edge]=edge==='start'?Math.max(bounds.start,Math.min(v,range.end-minimum)):Math.min(bounds.end,Math.max(v,range.start+minimum));return range;
  }
  function choose(choices,offset){
    if(choices.length===1)return choices[0];
    const found=choices.find(c=>c.offset===offset);if(!found)throw new Error('Choose EDT or EST for this repeated New York time.');return found;
  }
  const api={input,offset,stamp,move,choose,zone};
  if(typeof module==='object'&&module.exports)module.exports=api;else root.MindTrailTime=api;
})(typeof globalThis!=='undefined'?globalThis:this);
