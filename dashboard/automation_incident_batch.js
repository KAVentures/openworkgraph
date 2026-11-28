/* Explicit visible selection, exact preview and atomic acknowledgement only. */
const MAX=50;
export function mount(host,{incidents,api,refresh,deploymentName}){
  const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text);return n;};
  const button=text=>{const n=el('button',text);n.type='button';n.className='secondary';return n;};
  const labelled=(text,node)=>{const n=el('label');n.append(node,document.createTextNode(' '+text));return n;};
  const eligible=incidents.filter(i=>i.status==='open');
  if(!eligible.length)return;
  const panel=el('details');panel.id='owgMonitorBatchAck';
  panel.append(el('summary','Acknowledge selected open incidents together'),
    el('p','Select up to 50 visible open incidents. Review the exact set and optional shared note. If any selected record changes, the entire acknowledgement is refused. This does not resolve an incident, verify underlying reports, or rerun anything.'));
  const boxes=eligible.map(i=>{
    const checkbox=el('input');checkbox.type='checkbox';
    const name=deploymentName(i.deployment_id)||i.deployment_id;
    const desc=name+' · '+i.kind+' · '+i.fingerprint+' · '+i.occurrences+' · '+i.incident_id;
    const label=labelled(desc,checkbox);label.style.display='block';
    return {i,checkbox,label};
  });
  const list=el('fieldset');list.append(el('legend','Visible open incidents'),...boxes.map(x=>x.label));
  const select=button('Select up to 50 visible open incidents'),clear=button('Clear selection');
  const note=el('input');note.type='text';note.maxLength=500;note.setAttribute('aria-label','Shared acknowledgement note');
  const prepare=button('Review exact acknowledgement'),apply=button('Acknowledge reviewed incidents');
  prepare.id='owgMonitorBatchPreview';apply.id='owgMonitorBatchApply';
  const confirm=el('input');confirm.type='checkbox';confirm.id='owgMonitorBatchConfirm';
  const output=el('div'),status=el('p');status.setAttribute('role','status');status.id='owgMonitorBatchStatus';
  let generation=0,review=null,busy=false;
  const chosen=()=>boxes.filter(x=>x.checkbox.checked);
  const buttons=()=>{const n=chosen().length;prepare.disabled=busy||!n||n>MAX;apply.disabled=busy||!review||!confirm.checked;
    confirm.disabled=busy||!review;select.disabled=clear.disabled=note.disabled=busy;
    boxes.forEach(x=>{x.checkbox.disabled=busy;});};
  function invalidate(){generation++;review=null;confirm.checked=false;output.replaceChildren();status.textContent=chosen().length+' selected (maximum 50).';buttons();}
  boxes.forEach(x=>{x.checkbox.onchange=invalidate;});note.oninput=invalidate;
  select.onclick=()=>{boxes.forEach((x,i)=>{x.checkbox.checked=i<MAX;});invalidate();};
  clear.onclick=()=>{boxes.forEach(x=>{x.checkbox.checked=false;});invalidate();};
  confirm.onchange=buttons;
  const post=(path,payload)=>api('/v1/automation-monitoring/incidents/batch/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  function payload(){return {selection:chosen().map(({i})=>({incident_id:i.incident_id,expected_last_seen:i.last_seen,expected_review_revision:i.review_revision??0,expected_occurrences:i.occurrences,expected_membership_sha256:i.run_membership_sha256??null})),note:note.value};}
  prepare.onclick=async()=>{
    if(prepare.disabled)return;
    const p=payload(),v=++generation;busy=true;review=null;confirm.checked=false;output.replaceChildren();status.textContent='Reviewing exact selected records…';buttons();
    try{
      const data=await post('preview',p);
      if(v!==generation||!host.isConnected)return;
      if(data.action!=='acknowledge_open_incidents'||data.selected_count!==p.selection.length||data.resolution_claimed!==false||data.execution_authorized!==false||!/^[a-f0-9]{64}$/.test(data.preview_sha256))throw Error('Unexpected incident review response.');
      const reviewed=el('ul');
      for(const row of data.incidents)reviewed.append(el('li',(deploymentName(row.deployment_id)||row.deployment_id)+' · '+row.kind+' · '+row.fingerprint+' · '+row.incident_id+' · '+row.occurrences+' '+row.count_basis+' · last '+row.last_seen+' · revision '+row.review_revision));
      output.replaceChildren(el('p','Exact acknowledgement: '+data.selected_count+' incident records.'),reviewed,el('p','Shared note: '+(data.note||'(none)')),el('p','Preview digest: '+data.preview_sha256));
      review={...p,preview_sha256:data.preview_sha256};status.textContent='Review the listed records, then explicitly confirm acknowledgement.';
    }catch(error){if(v===generation&&host.isConnected)status.textContent=error.message;}
    finally{if(v===generation&&host.isConnected){busy=false;buttons();}}
  };
  apply.onclick=async()=>{
    if(apply.disabled)return;
    const p={...review,confirm:true},v=++generation;busy=true;buttons();
    status.textContent='Acknowledging reviewed records atomically…';
    try{
      const data=await post('acknowledge',p);
      if(v!==generation||!host.isConnected)return;
      if(data.acknowledged_count!==p.selection.length)throw Error('Unexpected acknowledgement receipt; refresh before retrying.');
      review=null;confirm.checked=false;output.replaceChildren();
      status.textContent='Acknowledged '+data.acknowledged_count+' incidents. No incident was resolved.';
      // Refresh the existing incident list from storage. If it fails, do
      // not offer a replay of a committed decision.
      await refresh();
    }catch(error){if(v===generation&&host.isConnected){review=null;confirm.checked=false;output.replaceChildren();status.textContent=error.message+' Refresh incidents before preparing another review.';}}
    finally{if(v===generation&&host.isConnected){busy=false;buttons();}}
  };
  panel.append(list,select,clear,labelled('Shared note (optional)',note),prepare,output,labelled('I reviewed this exact selection and acknowledge receipt of these incidents, not their resolution.',confirm),apply,status);
  host.replaceChildren(panel);invalidate();
}
