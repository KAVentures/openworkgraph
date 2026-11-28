/* Explicit saved process context, separate from the original evidence packet. */
export function mountKnowledgeContext(host,{getState,post,lock,download}){
  const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
  const button=text=>{const n=el('button',text);n.type='button';return n;};
  const reload=button('Load reviewed process catalog'),next=button('Next catalog page'),clear=button('Clear selected processes'),prepare=button('Prepare selected knowledge companion'),save=button('Download reviewed knowledge companion');
  const rows=el('div'),selection=el('p'),status=el('p','Prepare original evidence before selecting reviewed knowledge.');status.setAttribute('role','status');status.setAttribute('aria-live','polite');
  const hex=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
  const validRef=r=>r&&typeof r.workflow_id==='string'&&/^[a-zA-Z0-9_-]{1,80}$/.test(r.workflow_id)&&Number.isSafeInteger(r.revision)&&r.revision>=1&&hex(r.content_sha256);
  const ref=r=>({workflow_id:r.workflow_id,revision:r.revision,content_sha256:r.content_sha256});
  let page=[],cursor=null,selected=new Map(),result=null,epoch=0,lastPin=null;
  function invalidate(){epoch++;result=null;status.textContent='Original evidence or selection changed; prepare the knowledge companion again.';refresh();}
  function refresh(){
    const s=getState(),pin=s.packet?.packet_sha256??null;
    if(pin!==lastPin){epoch++;lastPin=pin;result=null;}
    reload.disabled=s.busy||!pin;next.disabled=reload.disabled||cursor===null;clear.disabled=s.busy||!selected.size;prepare.disabled=s.busy||!pin||!selected.size;save.disabled=s.busy||!pin||!result;
    selection.textContent=selected.size+' of 5 processes selected: '+[...selected.values()].map(r=>r.title+' (revision '+r.revision+')').join('; ');
    rows.replaceChildren();for(const r of page){const cb=el('input');cb.type='checkbox';cb.checked=selected.has(r.workflow_id);cb.disabled=s.busy||(!cb.checked&&selected.size>=5);cb.onchange=()=>{if(cb.checked)selected.set(r.workflow_id,r);else selected.delete(r.workflow_id);invalidate();};const label=el('label',r.title+' · revision '+r.revision+' · '+r.workflow_id+' ');label.style.display='block';label.append(cb);rows.append(label);}
  }
  async function load(after){
    if(getState().busy||!getState().packet)return;const ticket=++epoch;result=null;lock(true);
    try{const r=await post('/v1/ai-opportunity-handoff/knowledge-list',{after});if(ticket!==epoch)return;
      if(r.format!=='openworkgraph.ai-implementation-knowledge-list.v1'||!Array.isArray(r.processes)||r.processes.length>20||r.processes.some(x=>!validRef(x)||typeof x.title!=='string')||new Set(r.processes.map(x=>x.workflow_id)).size!==r.processes.length||typeof r.has_more!=='boolean'||(r.has_more?(typeof r.next_after!=='string'||r.next_after<=after):r.next_after!==null))throw Error('Unexpected catalog page.');
      page=r.processes;cursor=r.has_more?r.next_after:null;
      // A newer catalog revision must be selected again explicitly.
      for(const row of page){const old=selected.get(row.workflow_id);if(old&&JSON.stringify(ref(old))!==JSON.stringify(ref(row)))selected.delete(row.workflow_id);}
      status.textContent=page.length+' saved process designs on this page. Select relevant current revisions explicitly.';
    }catch(e){if(ticket===epoch)status.textContent=e.message;}finally{lock(false);refresh();}
  }
  reload.onclick=()=>load('');next.onclick=()=>{if(!next.disabled)load(cursor);};clear.onclick=()=>{if(!clear.disabled){selected.clear();invalidate();}};
  prepare.onclick=async()=>{
    const s=getState();if(prepare.disabled||!s.packet)return;const refs=[...selected.values()].map(ref),ticket=++epoch;result=null;lock(true);
    try{const r=await post('/v1/ai-opportunity-handoff/knowledge-context',{sources:await Promise.all(s.files.map(f=>f.text())),packet_sha256:s.packet.packet_sha256,max_bytes:s.packet.max_packet_bytes,references:refs});if(ticket!==epoch)return;
      const q=JSON.parse(r.context_json);if(r.format!=='openworkgraph.ai-implementation-knowledge-context-result.v1'||!hex(r.context_sha256)||r.packet_sha256!==s.packet.packet_sha256||r.execution_authorized!==false||r.external_sharing_authorized!==false||q.format!=='openworkgraph.ai-implementation-knowledge-context.v1'||q.context_sha256!==r.context_sha256||q.packet_sha256!==r.packet_sha256||JSON.stringify(q.references.map(ref))!==JSON.stringify(refs)||!Array.isArray(q.reviewed_knowledge)||q.reviewed_knowledge.length!==refs.length||q.reviewed_knowledge.some((x,i)=>JSON.stringify(ref(x))!==JSON.stringify(refs[i]))||['observed_evidence','organization_policy','execution_authorized','external_sharing_authorized','model_invoked','current_applicability_verified','atomic_catalog_snapshot_verified'].some(k=>q[k]!==false))throw Error('Unexpected knowledge companion lineage or claims.');
      result=r;status.textContent='Knowledge companion prepared. Inspect the downloaded file locally; share only with an authorized AI alongside original evidence. SHA-256: '+r.context_sha256+'.';
    }catch(e){if(ticket===epoch)status.textContent=e.message;}finally{lock(false);refresh();}
  };
  save.onclick=()=>{if(!save.disabled)download('owg-reviewed-knowledge-companion.json','application/json',result.context_json);};
  const details=el('details');details.id='owgImplementationKnowledgeContext';details.append(el('summary','Reuse saved reviewed process knowledge'),el('p','Select up to five saved process designs. This is separate prior context, not observed evidence, verified current policy or execution authority. No automatic external sharing or AI request.'),reload,next,rows,selection,clear,prepare,status,save);host.append(details);refresh();return {refresh,invalidate};
}
