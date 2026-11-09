/* Time allocation: where recorded foreground time went, per local day and app. Local view only. */
(function(){
  const el=(tag,text,cls)=>{const node=document.createElement(tag);if(text!==undefined&&text!==null)node.textContent=String(text);if(cls)node.className=cls;return node;};
  const field=(labelText,control)=>{const wrap=el('div');const label=el('label',labelText);label.htmlFor=control.id;wrap.append(label,control);return wrap;};
  const hours=s=>{const h=Math.floor(s/3600),m=Math.round((s%3600)/60);return h?h+' h '+m+' min':m+' min';};
  const today=()=>new Date().toISOString().slice(0,10);
  const daysAgo=n=>{const d=new Date();d.setUTCDate(d.getUTCDate()-n);return d.toISOString().slice(0,10);};
  async function api(path){
    await window.__owgAuthReady;
    const response=await fetch(path,{cache:'no-store'});
    if(!response.ok){let detail='';try{detail=(await response.json()).detail;}catch(_){}throw new Error(typeof detail==='string'&&detail?detail:'Request failed ('+response.status+').');}
    return response;
  }
  function build(){
    const panel=document.getElementById('panel-export');
    if(!panel||document.getElementById('owgTimeAllocation'))return;
    const card=el('div',null,'card');card.id='owgTimeAllocation';
    card.append(el('h2','Time allocation'),el('div','Where recorded foreground time went on this computer, by day and app, in your time zone. It is not effort, productivity or billable time; gaps are times nothing was recorded.','muted'));
    const start=document.createElement('input');start.type='date';start.id='owgTimeStart';start.value=daysAgo(6);
    const end=document.createElement('input');end.type='date';end.id='owgTimeEnd';end.value=today();
    const zone=document.createElement('input');zone.type='text';zone.id='owgTimeZone';zone.maxLength=64;
    try{zone.value=Intl.DateTimeFormat().resolvedOptions().timeZone||'UTC';}catch(_){zone.value='UTC';}
    const show=el('button','Show time allocation','secondary');show.type='button';show.id='owgTimeShow';
    const csv=el('button','Download CSV','secondary');csv.type='button';csv.id='owgTimeCsv';
    const row=el('div',null,'row');row.style.alignItems='end';
    row.append(field('From',start),field('To',end),field('Time zone',zone),show,csv);
    const status=el('div',null,'muted');status.id='owgTimeStatus';status.setAttribute('role','status');status.setAttribute('aria-live','polite');
    const out=el('div');out.id='owgTimeResult';
    card.append(row,status,out);
    panel.appendChild(card);
    const query=extra=>'/v1/time-allocation?'+new URLSearchParams({start:start.value,end:end.value,tz:zone.value.trim()||'UTC',...(extra||{})});
    show.onclick=async()=>{
      show.disabled=true;status.textContent='';
      try{
        const r=await (await api(query())).json();
        const apps=r.apps.slice(0,8).map(a=>a.app);
        const wrap=el('div',null,'table-wrap'),t=el('table'),head=el('tr');t.id='owgTimeTable';
        ['Day','Recorded',...apps].forEach(h=>{const th=el('th',h);th.scope='col';head.appendChild(th);});
        const thead=el('thead');thead.appendChild(head);const body=el('tbody');
        r.days.forEach(d=>{const tr=el('tr');const th=el('th',d.date);th.scope='row';tr.appendChild(th);
          tr.appendChild(el('td',d.recorded_seconds?hours(d.recorded_seconds):'—'));
          apps.forEach(a=>tr.appendChild(el('td',d.apps[a]?hours(d.apps[a]):'—')));body.appendChild(tr);});
        t.append(thead,body);wrap.appendChild(t);
        const totals=el('ul');totals.id='owgTimeApps';
        r.apps.slice(0,10).forEach(a=>totals.appendChild(el('li',a.app+': '+hours(a.recorded_seconds)+' ('+(a.share*100).toFixed(1)+'%)'+(a.engaged_seconds?', '+hours(a.engaged_seconds)+' engaged':''))));
        const peak=r.hours_of_day_seconds.map((s,h)=>[s,h]).sort((x,y)=>y[0]-x[0]).filter(x=>x[0]>0).slice(0,3).map(([s,h])=>String(h).padStart(2,'0')+':00 ('+hours(s)+')');
        out.replaceChildren(el('div',hours(r.recorded_seconds)+' recorded from '+r.start+' to '+r.end+' ('+r.time_zone+').'),wrap,el('h3','By app'),totals,
          el('div',peak.length?'Busiest hours: '+peak.join(', ')+'.':'No recorded time in this period.','muted'),el('p',r.interpretation,'muted'));
      }catch(error){status.textContent=error.message;}
      finally{show.disabled=false;}
    };
    csv.onclick=async()=>{
      try{
        const response=await api(query({format:'csv'}));
        const url=URL.createObjectURL(await response.blob());
        const a=document.createElement('a');a.href=url;a.download='time-allocation-'+start.value+'-to-'+end.value+'.csv';document.body.appendChild(a);a.click();a.remove();
        setTimeout(()=>URL.revokeObjectURL(url),1000);
      }catch(error){status.textContent=error.message;}
    };
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',build);else build();
})();
