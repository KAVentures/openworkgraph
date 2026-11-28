/* Portable, explicit local checkpoint. Never restores an approval or model request. */
const FORMAT='openworkgraph.ai-implementation-checkpoint.v1';
const LIMIT=6*1024*1024;
const BUDGETS=[131072,524288,2097152,8388608];
const hex=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const bytes=v=>new TextEncoder().encode(v).length;
function require(ok,message){if(!ok)throw Error(message);}
function keys(v,fields){return v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).sort().join('|')===fields.slice().sort().join('|');}
export function parseCheckpoint(raw){
  require(typeof raw==='string'&&bytes(raw)<=LIMIT,'Checkpoint exceeds 6 MiB; use original files instead.');
  const c=JSON.parse(raw);
  require(keys(c,['format','sources','max_bytes','packet_sha256','draft'])&&c.format===FORMAT&&hex(c.packet_sha256)&&BUDGETS.includes(c.max_bytes),'Invalid checkpoint format or packet pin.');
  require(Array.isArray(c.sources)&&c.sources.length>=1&&c.sources.length<=8&&c.sources.every(s=>typeof s==='string'&&s.length>0),'Invalid checkpoint sources.');
  const d=c.draft;
  require(d===null||(keys(d,['proposal','title','description','preview_sha256'])&&typeof d.proposal==='string'&&d.proposal.length>0&&typeof d.title==='string'&&d.title.trim()&&d.title.length<=200&&typeof d.description==='string'&&d.description.length<=2000&&hex(d.preview_sha256)),'Invalid checkpoint draft.');
  return c;
}
export function makeCheckpoint(sources,max_bytes,packet_sha256,draft){
  const raw=JSON.stringify({format:FORMAT,sources,max_bytes,packet_sha256,draft});parseCheckpoint(raw);return raw;
}
export async function verifyCheckpoint(raw,post){
  const c=parseCheckpoint(raw);
  // Check encoded request size, not merely the sum of unescaped file sizes.
  const request={sources:c.sources,max_bytes:c.max_bytes};
  require(bytes(JSON.stringify(request))<=8*1024*1024,'Checkpoint request exceeds server budget.');
  const packet=await post('/v1/ai-opportunity-handoff/prepare-packet',request);
  require(packet?.format==='openworkgraph.ai-implementation-packet-preview.v1'&&packet.packet_sha256===c.packet_sha256&&packet.max_packet_bytes===c.max_bytes&&packet.source_count===c.sources.length&&packet.execution_authorized===false&&packet.external_model_invoked===false&&packet.external_sharing_authorized===false,'Original evidence packet changed; cannot resume.');
  if(c.draft){
    const {proposal,title,description,preview_sha256}=c.draft;
    const req={sources:c.sources,proposal,title,description};
    require(bytes(JSON.stringify(req))<=8*1024*1024,'Checkpoint draft request exceeds server budget.');
    const review=await post('/v1/ai-opportunity-handoff/preview',req);
    require(review?.format==='openworkgraph.ai-opportunity-handoff.v1'&&review.preview_sha256===preview_sha256&&review.lineage?.packet_sha256===c.packet_sha256&&review.execution_authorized===false&&review.state_on_create==='idea'&&review.assessment_on_create===null,'Original draft review changed; cannot resume.');
  }
  return {checkpoint:c,packet};
}
export function mountImplementationCheckpoint(host,{getState,post,lock,download,onRestore}){
  const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
  const button=text=>{const n=el('button',text);n.type='button';return n;};
  const field=(label,n)=>{const l=el('label',label+' ');l.append(n);return l;};
  const save=button('Download local workflow checkpoint'),load=button('Restore checkpoint inputs');
  const file=el('input');file.type='file';file.accept='.json,application/json';
  const confirm=el('input');confirm.type='checkbox';
  const status=el('p','Prepare an evidence packet to save a checkpoint.');status.setAttribute('role','status');status.setAttribute('aria-live','polite');
  function refresh(){const s=getState();save.disabled=s.busy||!s.packet;file.disabled=confirm.disabled=s.busy;load.disabled=s.busy||!file.files?.[0]||!confirm.checked;}
  file.addEventListener('change',()=>{confirm.checked=false;refresh();});confirm.addEventListener('change',refresh);
  save.onclick=async()=>{
    const s=getState();if(save.disabled||!s.packet)return;lock(true);
    try{
      const sources=await Promise.all(s.files.map(f=>f.text()));
      const draft=s.reviewed?{proposal:s.reviewed.draft.proposal,title:s.reviewed.draft.title,description:s.reviewed.draft.description,preview_sha256:s.reviewed.pin}:null;
      const raw=makeCheckpoint(sources,s.packet.max_packet_bytes,s.packet.packet_sha256,draft);
      // Verify that the locally assembled download is resumable before offering it.
      await verifyCheckpoint(raw,post);download('owg-workflow-checkpoint.json','application/json',raw);
      status.textContent=draft?'Checkpoint saved with the reviewed draft inputs. No confirmation is saved.':'Checkpoint saved with original evidence. No proposal or confirmation is saved.';
    }catch(e){status.textContent=e.message;}finally{lock(false);refresh();}
  };
  load.onclick=async()=>{
    const f=file.files?.[0];if(load.disabled||!f)return;confirm.checked=false;lock(true);
    try{
      require(f.size>0&&f.size<=LIMIT,'Select a checkpoint of at most 6 MiB.');
      const raw=new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(await f.arrayBuffer());
      const verified=await verifyCheckpoint(raw,post);onRestore(verified);
      file.value='';status.textContent='Checkpoint restored. Review the exact proposal again before creating an idea or preparing a skill.';
    }catch(e){status.textContent=e.message;}finally{lock(false);refresh();}
  };
  const details=el('details');details.id='owgImplementationCheckpoint';details.append(el('summary','Pause or resume this workflow discovery'),el('p','A checkpoint contains the original selected evidence and, if already structurally reviewed, the exact proposal and entered idea details. It may contain sensitive company data. Keep it locally under your retention policy; do not attach it to an external AI. No credentials, model settings, approval, or execution state are saved. Restoring replaces the current evidence and draft selection only.'),save,field('Local workflow checkpoint JSON',file),field('Replace the current evidence and draft inputs with this checkpoint',confirm),load,status);host.append(details);refresh();return {refresh};
}
