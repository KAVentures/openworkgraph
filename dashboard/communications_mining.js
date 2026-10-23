/* Communications mining: analyse an explicitly selected email/ticket export on this computer; keep only aggregates. */
(() => {
  'use strict';
  const FORMAT='openworkgraph.communications-taxonomy.v1';
  const el=(tag,text,cls)=>{const node=document.createElement(tag);if(text!==undefined&&text!==null)node.textContent=String(text);if(cls)node.className=cls;return node;};
  const input=(id,type,placeholder)=>{const node=document.createElement('input');node.id=id;node.type=type||'text';if(placeholder)node.placeholder=placeholder;return node;};
  const field=(labelText,control)=>{const wrap=el('div');const label=el('label',labelText);label.htmlFor=control.id;wrap.append(label,control);return wrap;};
  const setOptions=(node,options)=>node.replaceChildren(...options.map(([value,label])=>{const o=el('option',label);o.value=value;return o;}));
  const select=(id,options)=>{const node=document.createElement('select');node.id=id;setOptions(node,options);return node;};
  const button=(text,cls)=>{const node=el('button',text,cls);node.type='button';return node;};
  const show=(node,visible)=>{node.hidden=!visible;node.style.display=visible?'':'none';};
  const pct=v=>v===null||v===undefined?'—':(Number(v)*100).toFixed(1)+'%';
  const duration=s=>{if(s===null||s===undefined)return '—';s=Number(s);return s<7200?Math.round(s/60)+' min':s<172800?(s/3600).toFixed(1)+' h':(s/86400).toFixed(1)+' d';};
  function table(headers,rows){
    const wrap=el('div',null,'table-wrap');const t=el('table');const head=el('thead');const hr=el('tr');
    headers.forEach(h=>hr.appendChild(el('th',h)));head.appendChild(hr);
    const body=el('tbody');rows.forEach(r=>{const tr=el('tr');r.forEach(v=>tr.appendChild(el('td',v)));body.appendChild(tr);});
    t.append(head,body);wrap.appendChild(t);return wrap;
  }

  // Pure helpers, exercised by tests.
  function detectFormat(name){const n=String(name||'').toLowerCase();return n.endsWith('.eml')?'eml':(n.endsWith('.csv')||n.endsWith('.tsv'))?'csv':'mbox';}
  // "Intent name: term, term" per line.
  function parseTaxonomy(text){
    const intents=[];
    for(const line of String(text||'').split(/\r?\n/)){
      if(!line.trim())continue;
      const at=line.indexOf(':');
      if(at<1)throw new Error('Write each intent as "Name: term, term".');
      const name=line.slice(0,at).trim(),terms=line.slice(at+1).split(',').map(t=>t.trim()).filter(Boolean);
      if(!terms.length)throw new Error('Intent "'+name+'" needs at least one term.');
      intents.push({name,terms});
    }
    if(!intents.length)throw new Error('Enter at least one intent.');
    return {format:FORMAT,intents};
  }
  const formatTaxonomy=t=>(t.intents||[]).map(i=>i.name+': '+i.terms.join(', ')).join('\n');
  function csvHeader(text,delimiter){
    const line=String(text||'').split(/\r?\n/,1)[0].replace(/^﻿/,'');const out=[];let cell='',quoted=false;
    for(let i=0;i<line.length;i++){const c=line[i];
      if(quoted){if(c==='"'&&line[i+1]==='"'){cell+='"';i++;}else if(c==='"')quoted=false;else cell+=c;}
      else if(c==='"')quoted=true;else if(c===delimiter){out.push(cell.trim());cell='';}else cell+=c;}
    out.push(cell.trim());return out.filter(Boolean).slice(0,200);
  }

  async function api(path,init){
    await window.__owgAuthReady;
    const response=await fetch(path,{cache:'no-store',...(init||{})});
    if(!response.ok){let detail='';try{detail=(await response.json()).detail;}catch(_){}throw new Error(typeof detail==='string'&&detail?detail:'Communications request failed ('+response.status+').');}
    return response.json();
  }

  function build(){
    const panel=document.getElementById('panel-evidence');
    if(!panel||document.getElementById('owgCommsCard'))return;
    const card=el('div',null,'card');card.id='owgCommsCard';
    card.appendChild(el('h2','Communications'));
    card.appendChild(el('div','Analyse an email (mbox or .eml) or ticket (CSV) export you choose. It is read on this computer, sensitive identifiers are redacted, and only counts are kept: intents, entity types, volumes and response times. No message text or entity value is stored or returned. Intents are keyword matches from the taxonomy below, not verified reasons for contact.','muted'));
    const file=input('owgCommsFile','file');file.accept='.mbox,.eml,.csv,.tsv,.txt';
    const format=select('owgCommsFormat',[['mbox','Mailbox (mbox)'],['eml','Single email (.eml)'],['csv','Ticket export (CSV)']]);
    const domains=input('owgCommsDomains','text','example.com');
    const row=el('div',null,'row');row.style.alignItems='end';
    const domainField=field('Your own email domains (marks replies)',domains);
    row.append(field('File',file),field('Format',format),domainField);
    const mapping=el('div');mapping.id='owgCommsMapping';
    const delimiter=select('owgCommsDelimiter',[[',','Comma'],[';','Semicolon'],['t','Tab'],['|','Pipe']]);
    const textCols=el('fieldset');textCols.id='owgCommsTextColumns';textCols.appendChild(el('legend','Text columns'));
    const cols={time:select('owgCommsTime',[]),thread:select('owgCommsThread',[]),direction:select('owgCommsDirection',[]),channel:select('owgCommsChannel',[])};
    const mapRow=el('div',null,'row');mapRow.style.alignItems='end';
    mapRow.append(field('Delimiter',delimiter),field('Time (with timezone)',cols.time),field('Thread or ticket ID',cols.thread),field('Direction (inbound/outbound)',cols.direction),field('Channel',cols.channel));
    mapping.append(textCols,mapRow);
    const consent=input('owgCommsConsent','checkbox');const consentLabel=el('label');consentLabel.style.cssText='display:flex;gap:6px;align-items:center;margin-top:8px';
    consentLabel.append(consent,document.createTextNode(' Analyse this export on this computer (nothing is uploaded or kept)'));
    const save=input('owgCommsSave','checkbox');const saveLabel=el('label');saveLabel.style.cssText='display:flex;gap:6px;align-items:center';
    saveLabel.append(save,document.createTextNode(' Also store an intent-only event log (thread, intent, time, direction) on this computer'));
    const run=button('Analyse communications');run.id='owgCommsRun';run.disabled=true;
    const status=el('div',null,'muted');status.id='owgCommsStatus';status.setAttribute('role','status');status.setAttribute('aria-live','polite');
    const results=el('div');results.id='owgCommsResults';
    const taxonomyBox=el('details');const summary=el('summary','Intent taxonomy');taxonomyBox.appendChild(summary);
    const taxonomy=document.createElement('textarea');taxonomy.id='owgCommsTaxonomy';taxonomy.rows=8;taxonomy.style.width='100%';
    const saveTaxonomy=button('Save taxonomy','secondary');saveTaxonomy.id='owgCommsTaxonomySave';
    const resetTaxonomy=button('Restore default','secondary');resetTaxonomy.id='owgCommsTaxonomyReset';
    const taxStatus=el('div',null,'muted');taxStatus.id='owgCommsTaxonomyStatus';taxStatus.setAttribute('role','status');
    const taxRow=el('div',null,'row');taxRow.append(saveTaxonomy,resetTaxonomy);
    taxonomyBox.append(el('div','One intent per line: "Name: term, term". A message gets the intent whose terms match best; ties are shown as ambiguous.','muted'),field('Intents and terms',taxonomy),taxRow,taxStatus);
    card.append(row,mapping,consentLabel,saveLabel,run,status,results,taxonomyBox);
    const anchor=document.getElementById('owgEventLogCard')||document.getElementById('owgProcessCSV');
    if(anchor)anchor.insertAdjacentElement('afterend',card);else panel.appendChild(card);

    const refresh=()=>{run.disabled=!(file.files&&file.files[0]&&consent.checked);show(mapping,format.value==='csv');show(domainField,format.value!=='csv');};
    async function readColumns(){
      const chosen=file.files&&file.files[0];if(!chosen||format.value!=='csv')return;
      const columns=csvHeader(await chosen.slice(0,65536).text(),delimiter.value==='t'?'\t':delimiter.value);
      textCols.replaceChildren(el('legend','Text columns'));
      columns.forEach((c,i)=>{const box=input('owgCommsText'+i,'checkbox');box.value=c;box.checked=/subject|body|message|text|description|comment/i.test(c);
        const l=el('label');l.style.cssText='display:inline-flex;gap:4px;margin-right:12px';l.append(box,document.createTextNode(' '+c));textCols.appendChild(l);});
      const options=[['','(none)'],...columns.map(c=>[c,c])];
      for(const [key,words] of [['time',/time|date|created/i],['thread',/thread|ticket|case|conversation/i],['direction',/direction|from_type|author_type|sender_type/i],['channel',/channel|source|via/i]]){
        setOptions(cols[key],options);cols[key].value=columns.find(c=>words.test(c))||'';}
    }
    file.onchange=()=>{const chosen=file.files&&file.files[0];if(chosen){format.value=detectFormat(chosen.name);if(chosen.name.toLowerCase().endsWith('.tsv'))delimiter.value='t';}refresh();readColumns().catch(()=>{status.textContent='Could not read the column names.';});};
    format.onchange=()=>{refresh();readColumns().catch(()=>{});};delimiter.onchange=()=>readColumns().catch(()=>{});consent.onchange=refresh;
    run.onclick=async()=>{
      const chosen=file.files&&file.files[0];if(!chosen||!consent.checked)return;
      const q=new URLSearchParams({format:format.value});
      if(format.value==='csv'){
        const picked=[...textCols.querySelectorAll('input:checked')].map(b=>b.value);
        if(!picked.length){status.textContent='Choose at least one text column.';return;}
        q.set('text_columns',picked.join(','));q.set('delimiter',delimiter.value);
        for(const key of ['time','thread','direction','channel'])if(cols[key].value)q.set(key+'_column',cols[key].value);
      }else if(domains.value.trim())q.set('own_domains',domains.value);
      const headers={'Content-Type':'application/octet-stream','X-OWG-Local-Processing-Confirmed':'true'};
      if(save.checked){q.set('save_event_log','true');q.set('name',chosen.name.replace(/\.[^.]+$/,'').slice(0,100)+' intents');headers['X-OWG-Local-Storage-Confirmed']='true';}
      run.disabled=true;status.textContent='Analysing '+chosen.name+'…';results.replaceChildren();
      try{render(await api('/v1/communications/analyze?'+q,{method:'POST',body:chosen,headers}));status.textContent='';consent.checked=false;save.checked=false;}
      catch(error){status.textContent=error.message;}finally{refresh();}
    };
    const loadTaxonomy=async()=>{try{const t=await api('/v1/communications/taxonomy');taxonomy.value=formatTaxonomy(t.taxonomy);taxStatus.textContent=t.is_default?'Default taxonomy.':'Your saved taxonomy.';}catch(error){taxStatus.textContent=error.message;}};
    saveTaxonomy.onclick=async()=>{try{const t=await api('/v1/communications/taxonomy',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(parseTaxonomy(taxonomy.value))});taxonomy.value=formatTaxonomy(t.taxonomy);taxStatus.textContent='Taxonomy saved on this computer.';}catch(error){taxStatus.textContent=error.message;}};
    resetTaxonomy.onclick=async()=>{try{const t=await api('/v1/communications/taxonomy/reset',{method:'POST'});taxonomy.value=formatTaxonomy(t.taxonomy);taxStatus.textContent='Default taxonomy restored.';}catch(error){taxStatus.textContent=error.message;}};
    refresh();loadTaxonomy();

    function render(r){
      const parts=[el('h3','Intents'),el('div',Number(r.messages).toLocaleString()+' messages; '+Number(r.classified_messages).toLocaleString()+' classified (outbound replies are counted, not classified).','muted'),
        table(['Intent','Messages','Share'],r.intents.map(i=>[i.intent,Number(i.messages).toLocaleString(),pct(i.share)]))];
      if(r.ambiguous_between.length)parts.push(el('div','Ambiguous messages matched: '+r.ambiguous_between.map(a=>a.intent+' ('+a.messages+')').join(', ')+'.','muted'));
      parts.push(el('h3','Entities mentioned'),table(['Entity type','Messages','Mentions'],r.entities.map(e=>[e.type.replace(/_/g,' '),Number(e.messages).toLocaleString(),Number(e.mentions).toLocaleString()])),
        el('div','Only entity types are counted; values are never kept or shown.','muted'));
      const t=r.threads;
      parts.push(el('h3','Threads'),table(['Threads','Median messages','Threads with repeat contact','Median first response'],[[Number(t.threads).toLocaleString(),t.median_messages_per_thread===null?'—':t.median_messages_per_thread,Number(t.threads_with_repeat_inbound).toLocaleString(),t.first_response_seconds?duration(t.first_response_seconds.median)+' ('+t.first_response_seconds.count+' threads)':'—']]));
      if(r.weekly_volume.length)parts.push(el('h3','Weekly volume'),table(['Week','Messages','Most common intent'],r.weekly_volume.slice(-26).map(w=>{const top=Object.entries(w.by_intent).filter(([k])=>k!=='outbound reply').sort((a,b)=>b[1]-a[1])[0];return [w.week,Number(w.messages).toLocaleString(),top?top[0]+' ('+top[1]+')':'—'];})));
      if(r.unclassified_themes.length)parts.push(el('h3','Frequent phrases in unclassified messages'),el('div','Phrases in at least 5 messages that matched no intent; consider adding them to the taxonomy.','muted'),
        table(['Phrase','Messages'],r.unclassified_themes.map(x=>[x.terms,x.messages])));
      const doms=Object.entries(r.sender_domains);
      if(doms.length)parts.push(el('h3','Sender domains'),table(['Domain','Messages'],doms.map(([d,n])=>[d,n])),el('div','Domains with fewer than 5 messages are not listed ('+r.sender_domains_below_threshold+' messages).','muted'));
      if(r.event_log)parts.push(el('div','Saved the intent event log "'+r.event_log.name+'" with '+Number(r.event_log.events).toLocaleString()+' events. Open it under Large event logs to analyse it like any process.','note'));
      results.replaceChildren(...parts);
      if(r.event_log&&typeof window.owgEventLogRefresh==='function')window.owgEventLogRefresh(r.event_log.log_id);
    }
  }

  window.__owgCommsModel={detectFormat,parseTaxonomy,formatTaxonomy,csvHeader};
  if(typeof document!=='undefined'&&document.addEventListener)document.addEventListener('DOMContentLoaded',build);
})();
