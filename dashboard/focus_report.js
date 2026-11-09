/* Focus report: how recorded time was split between apps. Local view only. */
(function(){
  const el=(tag,text,cls)=>{const node=document.createElement(tag);if(text!==undefined&&text!==null)node.textContent=String(text);if(cls)node.className=cls;return node;};
  const field=(labelText,control)=>{const wrap=el('div');const label=el('label',labelText);label.htmlFor=control.id;wrap.append(label,control);return wrap;};
  const dur=s=>{if(s===null||s===undefined)return '—';const h=Math.floor(s/3600),m=Math.round((s%3600)/60);return h?h+' h '+m+' min':m+' min';};
  const daysAgo=n=>{const d=new Date();d.setUTCDate(d.getUTCDate()-n);return d.toISOString().slice(0,10);};
  async function api(path){
    await window.__owgAuthReady;
    const response=await fetch(path,{cache:'no-store'});
    if(!response.ok){let detail='';try{detail=(await response.json()).detail;}catch(_){}throw new Error(typeof detail==='string'&&detail?detail:'Request failed ('+response.status+').');}
    return response.json();
  }
  function build(){
    const panel=document.getElementById('panel-export');
    if(!panel||document.getElementById('owgFocusReport'))return;
    const card=el('div',null,'card');card.id='owgFocusReport';
    card.append(el('h2','Focus and switching'),el('div','How recorded time on this computer was split between apps: uninterrupted blocks in one app and how often you switched. Short detours (under 30 seconds) are folded into the block around them. Switching is not a measure of attention or productivity.','muted'));
    const start=document.createElement('input');start.type='date';start.id='owgFocusStart';start.value=daysAgo(6);
    const end=document.createElement('input');end.type='date';end.id='owgFocusEnd';end.value=daysAgo(0);
    const zone=document.createElement('input');zone.type='text';zone.id='owgFocusZone';zone.maxLength=64;
    try{zone.value=Intl.DateTimeFormat().resolvedOptions().timeZone||'UTC';}catch(_){zone.value='UTC';}
    const show=el('button','Show focus report','secondary');show.type='button';show.id='owgFocusShow';
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('From',start),field('To',end),field('Time zone',zone),show);
    const status=el('div',null,'muted');status.id='owgFocusStatus';status.setAttribute('role','status');status.setAttribute('aria-live','polite');
    const out=el('div');out.id='owgFocusResult';
    card.append(row,status,out);panel.appendChild(card);
    show.onclick=async()=>{
      show.disabled=true;status.textContent='';
      try{
        const r=await api('/v1/focus-report?'+new URLSearchParams({start:start.value,end:end.value,tz:zone.value.trim()||'UTC'}));
        const wrap=el('div',null,'table-wrap'),t=el('table'),head=el('tr');t.id='owgFocusTable';
        ['Day','Recorded','Focus blocks','Switches','Per active hour','Median block','Longest block'].forEach(h=>{const th=el('th',h);th.scope='col';head.appendChild(th);});
        const thead=el('thead');thead.appendChild(head);const body=el('tbody');
        r.days.forEach(d=>{const tr=el('tr');const th=el('th',d.date);th.scope='row';tr.appendChild(th);
          [dur(d.recorded_seconds),d.focus_blocks,d.app_switches,d.switches_per_active_hour===null?'—':d.switches_per_active_hour,dur(d.median_block_seconds),dur(d.longest_block_seconds)].forEach(v=>tr.appendChild(el('td',String(v))));body.appendChild(tr);});
        t.append(thead,body);wrap.appendChild(t);
        const pairs=el('ul');pairs.id='owgFocusPairs';
        r.top_switch_pairs.slice(0,5).forEach(p=>pairs.appendChild(el('li',p.apps.join(' ⇄ ')+': '+p.switches+' switches')));
        const longest=el('ol');longest.id='owgFocusLongest';
        r.longest_blocks.slice(0,5).forEach(b=>longest.appendChild(el('li',b.app+' · '+b.start.replace('T',' ')+' · '+dur(b.seconds)+(b.short_detours?' ('+b.short_detours+' short detours)':''))));
        out.replaceChildren(el('div',r.focus_blocks+' focus blocks, '+r.app_switches+' switches; median block '+dur(r.median_block_seconds)+', '+r.blocks_over_25_minutes+' blocks of 25 minutes or more.'),wrap,
          el('h3','Switched between most'),r.top_switch_pairs.length?pairs:el('div','No switches recorded.','muted'),
          el('h3','Longest blocks'),r.longest_blocks.length?longest:el('div','No recorded time in this period.','muted'),el('p',r.interpretation,'muted'));
      }catch(error){status.textContent=error.message;}
      finally{show.disabled=false;}
    };
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',build);else build();
})();
