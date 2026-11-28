/* Explicit paste import and read-only human inspection of structurally reviewed proposals. */
const MAX=1024*1024, HEX=/^[a-f0-9]{64}$/;
const bytes=s=>new TextEncoder().encode(s).length;
function require(ok,msg){if(!ok)throw Error(msg);}
export function pastedProposal(raw){
  require(typeof raw==='string'&&bytes(raw)<=MAX,'Paste at most 1 MiB of proposal JSON.');
  // Accept one exact Markdown JSON fence, but do not rewrite the JSON interior.
  const match=raw.match(/^\s*```(?:json)?[ \t]*\r?\n([\s\S]*?)\r?\n```[ \t]*\s*$/i);
  const text=match?match[1]:raw;
  const p=JSON.parse(text);
  require(p&&p.format==='openworkgraph.ai-implementation-proposal.v1'&&HEX.test(p.packet_sha256),'Expected an integrated AI implementation proposal. Use compatibility review for earlier formats.');
  return text;
}
export function inspectProposal(basis,packet){
  require(basis&&basis.draft&&typeof basis.draft.proposal==='string'&&bytes(basis.draft.proposal)<=8*MAX&&HEX.test(basis.pin)&&HEX.test(basis.packetPin),'Review the exact proposal first.');
  const p=JSON.parse(basis.draft.proposal);
  require(p.format==='openworkgraph.ai-implementation-proposal.v1'&&p.packet_sha256===basis.packetPin,'Reviewed proposal packet mismatch.');
  const refs=new Set();
  for(const entry of [p.outcome,...p.steps,...p.exceptions,...p.execution_options])for(const ref of entry.evidence_refs)refs.add(ref);
  let evidence=null, gaps=null;
  if(packet?.packet_sha256===basis.packetPin){
    const q=JSON.parse(packet.packet_json);
    require(q.format==='openworkgraph.ai-implementation-evidence.v1'&&q.packet_sha256===basis.packetPin&&Array.isArray(q.bundles)&&Array.isArray(q.coverage_gaps),'Unexpected prepared evidence.');
    const all=new Map();
    for(const bundle of q.bundles)for(const run of bundle.runs)for(const event of run.events){
      require(!all.has(event.ref)&&all.size<4000,'Unexpected evidence membership.');all.set(event.ref,event.untrusted_observed_data);
    }
    require(all.size===q.event_count&&[...refs].every(ref=>all.has(ref)),'Prepared evidence differs from reviewed citations.');
    evidence=new Map([...refs].map(ref=>[ref,all.get(ref)]));gaps=q.coverage_gaps;
  }
  return {proposal:p,refs:[...refs],evidence,gaps};
}
export function mountProposalInspector(host,{getState,onSelect,download}){
  const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
  const button=text=>{const n=el('button',text);n.type='button';return n;};
  const field=(text,control)=>{const n=el('label',text+' ');n.append(control);return n;};
  const paste=el('textarea');paste.rows=6;paste.maxLength=MAX;
  const select=button('Use pasted proposal for review'),clear=button('Clear pasted text'),status=el('p');status.setAttribute('role','status');status.setAttribute('aria-live','polite');
  const body=el('div'),save=button('Download exact reviewed proposal');let lastBasis,lastPacket;
  function refresh(){
    const s=getState();paste.disabled=s.busy;select.disabled=clear.disabled=s.busy||!paste.value.trim();save.disabled=s.busy||!s.reviewed;
    if(lastBasis===s.reviewed&&lastPacket===s.packet)return;
    lastBasis=s.reviewed;lastPacket=s.packet;body.replaceChildren();
    if(!s.reviewed){body.append(el('p','Review an exact proposal to inspect all of its claims and citations here.'));return;}
    try{
      const x=inspectProposal(s.reviewed,s.packet),p=x.proposal;
      body.append(el('h4',p.title),el('p','Author: '+p.author_model+' · structural review only; all claims remain unverified.'));
      const claim=c=>{const section=el('div');section.append(el('p',c.basis+' — '+c.text),el('p','Citations: '+(c.evidence_refs.join(', ')||'none')));return section;};
      body.append(el('h5','Outcome'),claim(p.outcome));
      for(const [name,rows] of [['Steps',p.steps],['Exceptions',p.exceptions]]){body.append(el('h5',name));if(!rows.length)body.append(el('p','None supplied.'));for(const c of rows)body.append(claim(c));}
      for(const [name,rows] of [['Unresolved questions',p.questions],['Approval requirements',p.approval_requirements]]){body.append(el('h5',name));const ul=el('ul');for(const text of rows)ul.append(el('li',text));body.append(rows.length?ul:el('p','None supplied; not proof that none are needed.'));}
      body.append(el('h5','Execution options'));for(const o of p.execution_options){body.append(el('p',o.environment+' — '+o.description),el('p','Required tools: '+(o.required_tools.join(', ')||'none supplied')),el('p','Evaluation: '+o.evaluation),el('p','Citations: '+(o.evidence_refs.join(', ')||'none')));}
      body.append(el('h5','Observed evidence drilldown'));
      if(!x.evidence){body.append(el('p','Prepare the matching original evidence packet to inspect event payloads. Structural review already checked reference membership.'));return;}
      body.append(el('p',x.gaps.length+' evidence coverage gaps; citations do not establish claim correctness.'));
      const chooser=el('select'),initial=el('option','Choose a cited event');initial.value='';chooser.append(initial);
      for(const ref of x.refs){const opt=el('option',ref);opt.value=ref;chooser.append(opt);}
      const detail=el('pre');detail.style.whiteSpace='pre-wrap';detail.style.overflowWrap='anywhere';
      chooser.onchange=()=>{detail.textContent=chooser.value?JSON.stringify(x.evidence.get(chooser.value),null,2):'';};
      body.append(field('Cited event',chooser),detail);
      if(x.gaps.length){const gaps=el('details');gaps.append(el('summary','Inspect coverage gaps'),el('pre',JSON.stringify(x.gaps,null,2)));body.append(gaps);}
    }catch(e){body.append(el('p','Proposal inspection unavailable: '+e.message));}
  }
  paste.oninput=refresh;clear.onclick=()=>{if(!clear.disabled){paste.value='';status.textContent='';refresh();}};
  select.onclick=()=>{if(select.disabled)return;try{const raw=pastedProposal(paste.value);onSelect(raw);paste.value='';status.textContent='Pasted proposal selected. Enter human-reviewed details and run structural review.';}catch(_){status.textContent='Paste a valid integrated proposal JSON of at most 1 MiB (one optional JSON code fence). The current selection was not replaced.';}refresh();};
  save.onclick=()=>{const s=getState();if(!save.disabled&&s.reviewed)download('owg-exact-reviewed-proposal.json','application/json',s.reviewed.draft.proposal);};
  const details=el('details');details.id='owgProposalInspector';details.append(el('summary','Paste or inspect an AI proposal'),el('p','Paste a returned integrated proposal to replace the current proposal selection. Pasting never creates an idea or runs an AI.'),field('Paste proposal JSON',paste),select,clear,status,el('p','After structural review, inspect every claim and its cited evidence before choosing the next step.'),body,save);host.append(details);refresh();return {refresh};
}
