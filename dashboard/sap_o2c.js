/* SAP order-to-cash source: configure, run one confirmed incremental read, see checkpoint and last run. */
(() => {
  'use strict';
  const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined&&text!==null)n.textContent=String(text);if(cls)n.className=cls;return n;};
  const input=(id,type,value)=>{const n=document.createElement('input');n.id=id;n.type=type||'text';if(value!==undefined)n.value=value;return n;};
  const field=(text,control)=>{const w=el('div');const l=el('label',text);l.htmlFor=control.id;w.append(l,control);return w;};
  const button=(text,cls)=>{const n=el('button',text,cls);n.type='button';return n;};
  async function api(path,init){
    await window.__owgAuthReady;
    const r=await fetch(path,{cache:'no-store',...(init||{})});
    if(!r.ok){let d='';try{const j=await r.json();d=typeof j.detail==='string'?j.detail:(j.detail&&j.detail.message)||'';}catch(_){}throw new Error(d||'SAP source request failed ('+r.status+').');}
    return r.json();
  }
  function build(){
    const panel=document.getElementById('panel-evidence');
    if(!panel||document.getElementById('owgSapCard'))return;
    const card=el('div',null,'card');card.id='owgSapCard';
    card.append(el('h2','SAP order to cash'),el('div','Read sales orders (with items), outbound deliveries and billing documents from SAP S/4HANA standard OData APIs into one local object-centric event log. Read-only and off until you configure it; each read needs your confirmation. Document numbers and user names are replaced by keyed pseudonyms, customer fields are never read, and amounts are left out unless you include them. The credential stays in an environment variable of the OWG process and is never stored.','muted'));
    const root=input('owgSapRoot','url');root.placeholder='https://my-host.s4hana.ondemand.com/sap/opu/odata/sap';root.style.width='100%';
    const auth=document.createElement('select');auth.id='owgSapAuth';[['bearer','Bearer token'],['basic','Basic (user:password)']].forEach(([v,t])=>{const o=el('option',t);o.value=v;auth.appendChild(o);});
    const env=input('owgSapEnv','text','OWG_SAP_TOKEN');const overlap=input('owgSapOverlap','number','24');overlap.min='0';overlap.max='168';
    const initial=input('owgSapInitial','number','30');initial.min='1';initial.max='366';
    const amounts=input('owgSapAmounts','checkbox');const amountsLabel=el('label');amountsLabel.style.cssText='display:flex;gap:6px;align-items:center';amountsLabel.append(amounts,document.createTextNode(' Include net amounts and currencies'));
    const save=button('Save SAP source','secondary');save.id='owgSapSave';
    const consent=input('owgSapConsent','checkbox');const consentLabel=el('label');consentLabel.style.cssText='display:flex;gap:6px;align-items:center;margin-top:8px';
    consentLabel.append(consent,document.createTextNode(' Read changed order-to-cash documents from SAP now and add them to the local event log'));
    const run=button('Read from SAP');run.id='owgSapRun';run.disabled=true;
    const disconnect=button('Disconnect','secondary');disconnect.id='owgSapDisconnect';
    const status=el('div',null,'muted');status.id='owgSapStatus';status.setAttribute('role','status');status.setAttribute('aria-live','polite');
    const details=el('div');details.id='owgSapDetails';
    const row=el('div',null,'row');row.style.alignItems='end';row.append(field('Credential type',auth),field('Credential environment variable',env),field('Overlap for late changes (hours)',overlap),field('First read covers (days)',initial));
    card.append(field('OData service root',root),row,amountsLabel,save,consentLabel,run,disconnect,status,details);
    const anchor=document.getElementById('owgEventLogCard');if(anchor)anchor.insertAdjacentElement('afterend',card);else panel.appendChild(card);
    const refresh=configured=>{run.disabled=!(configured&&consent.checked);disconnect.disabled=!configured;};
    let configured=false;
    function render(s){
      configured=s.configured;refresh(configured);
      if(!s.configured){details.replaceChildren(el('div','Not configured.','muted'));return;}
      root.value=s.service_root;auth.value=s.auth_type;env.value=s.credential_env;overlap.value=String(s.overlap_hours);initial.value=String(s.initial_days);amounts.checked=s.include_amounts;
      const r=s.last_run;
      const rows=[['Checkpoint',s.checkpoint||'none yet'],['Event log',s.log_id||'created on the first read']];
      if(r)rows.push(['Last read',r.started_at+' · '+r.outcome+(r.outcome==='failed'?' · '+r.error+' (checkpoint unchanged)':'')],
        ['Window',r.window_since+' → '+r.window_until],
        ...(r.outcome==='succeeded'?[['Documents',Object.entries(r.documents).map(([k,v])=>k.replace(/_/g,' ')+' '+v).join(', ')],
          ['Events',r.events_added+' added, '+r.duplicates_skipped+' already in the log'],['Retries',String(r.retries)],
          ['Schema changes',r.schema_changed_services.length?r.schema_changed_services.join(', '):'none']]:[]));
      const t=el('table');const b=el('tbody');rows.forEach(([k,v])=>{const tr=el('tr');tr.append(el('th',k),el('td',v));b.appendChild(tr);});t.appendChild(b);
      details.replaceChildren(t);
    }
    const load=async()=>{try{render(await api('/v1/sources/sap-o2c'));}catch(e){status.textContent=e.message;}};
    consent.onchange=()=>refresh(configured);
    save.onclick=async()=>{try{render(await api('/v1/sources/sap-o2c',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({service_root:root.value.trim(),auth_type:auth.value,credential_env:env.value.trim(),overlap_hours:Number(overlap.value),initial_days:Number(initial.value),include_amounts:amounts.checked})}));status.textContent='SAP source saved. Nothing has been read yet.';}catch(e){status.textContent=e.message;}};
    run.onclick=async()=>{run.disabled=true;status.textContent='Reading from SAP…';
      try{const r=await api('/v1/sources/sap-o2c/run',{method:'POST',headers:{'X-OWG-Source-Read-Confirmed':'true'}});status.textContent='Read finished: '+r.events_added+' new events.';consent.checked=false;await load();if(typeof window.owgEventLogRefresh==='function')window.owgEventLogRefresh(r.log_id);}
      catch(e){status.textContent=e.message;consent.checked=false;await load();}};
    disconnect.onclick=async()=>{if(!window.confirm('Disconnect SAP? The configuration and checkpoint are removed; the event log stays until you delete it.'))return;
      try{await api('/v1/sources/sap-o2c/disconnect',{method:'POST'});status.textContent='Disconnected.';await load();}catch(e){status.textContent=e.message;}};
    load();
  }
  if(typeof document!=='undefined'&&document.addEventListener)document.addEventListener('DOMContentLoaded',build);
})();
