/* Explicit local no-execution agent-plan preflight; user-selected JSON only. */
(() => {
  'use strict';
  const limit=64*1024;
  const el=(tag,value,cls)=>{
    const node=document.createElement(tag);
    if(value!==undefined&&value!==null)node.textContent=String(value);
    if(cls)node.className=cls;
    return node;
  };
  function install(){
    const panel=document.getElementById('panel-evidence');
    if(!panel||document.getElementById('owgAgentPreflight'))return;
    const card=el('section',null,'card');card.id='owgAgentPreflight';
    card.appendChild(el('h2','Agent plan preflight · no execution'));
    card.appendChild(el('p',
      'Test an externally drafted agent plan against a reviewed OWG starter specification and synthetic case traces. Import one JSON request, then explicitly run a deterministic structural check. This does not call an AI or a business system.','muted'));
    card.appendChild(el('p',
      'The request must contain format, workflow, candidate, and simulated_cases. See docs/AGENT_PLAN_PREFLIGHT.md for the contract. Synthetic success is never a verified business outcome.','muted'));
    const row=el('div');row.style.cssText='display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin:12px 0';
    const file=el('input');file.type='file';file.accept='.json,application/json';
    file.setAttribute('aria-label','Select agent plan preflight JSON');
    file.id='owgAgentPreflightFile';
    const run=el('button','Evaluate synthetic plan','secondary');
    run.type='button';run.id='owgAgentPreflightRun';run.disabled=true;
    const status=el('p','No scenario JSON selected. Nothing will run automatically.','muted');
    status.setAttribute('role','status');status.id='owgAgentPreflightStatus';
    const result=el('div');result.id='owgAgentPreflightReport';
    file.addEventListener('change',()=>{
      result.replaceChildren();
      run.disabled=!file.files?.length;
      status.textContent=file.files?.length?
        'File selected; click Evaluate synthetic plan to run only local structural checks.':
        'No scenario JSON selected.';
    });
    run.addEventListener('click',async()=>{
      const selected=file.files?.[0];if(!selected)return;
      if(selected.size>limit){status.textContent='Preflight JSON exceeds the 64 KiB limit.';return;}
      result.replaceChildren();run.disabled=true;
      status.textContent='Checking selected synthetic scenarios locally…';
      try{
        if(!await window.__owgAuthReady)throw new Error('Reopen the authenticated local OWG dashboard.');
        const response=await fetch('/v1/process-design/preflight',{
          method:'POST',headers:{'Content-Type':'application/json'},
          body:selected,cache:'no-store'
        });
        if(!response.ok){
          const data=await response.json().catch(()=>({}));
          throw new Error(String(data.detail||('Preflight request rejected ('+response.status+')')));
        }
        const report=await response.json();
        if(report.format!=='openworkgraph.agent-preflight-report.v1')
          throw new Error('Unexpected local preflight report.');
        result.appendChild(el('h3','Structural review'));
        result.appendChild(el('p',
          String(report.target_step_count||0)+' target steps · '+
          String(report.candidate_step_count||0)+' candidate steps · '+
          String(report.synthetic_case_count||0)+' synthetic scenarios'));
        const summary=el('p',report.structural_review_passed?
          'No structural blockers detected in the supplied synthetic evidence. This is NOT deployment clearance.':
          'Structural issues detected; inspect the summary before any sandbox integration.');
        result.appendChild(summary);
        const blockers=Array.isArray(report.plan_blockers)?report.plan_blockers:[];
        if(blockers.length){
          result.appendChild(el('h4','Plan blockers'));
          const list=el('ul');blockers.forEach(item=>list.appendChild(el('li',String(item))));
          result.appendChild(list);
        }
        const cases=Array.isArray(report.synthetic_cases)?report.synthetic_cases:[];
        if(cases.length){
          const list=el('ol');
          cases.forEach(item=>{
            const findings=Array.isArray(item.issues)?item.issues:[];
            list.appendChild(el('li',
              String(item.case||'Synthetic scenario')+': '+
              (findings.length?findings.join(', '):'No structural findings')+
              ' (real outcome unverified)'
            ));
          });
          result.appendChild(el('h4','Synthetic-case findings'));result.appendChild(list);
        }
        result.appendChild(el('p',String(report.report_warning||'Synthetic observations do not prove business results.'),'muted'));
        const exportReport=el('button','Download structural preflight report','secondary');
        exportReport.type='button';
        exportReport.addEventListener('click',()=>{
          const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)+'\n'],{type:'application/json'}));
          try{
            const link=el('a');link.href=url;link.download='openworkgraph-agent-preflight-report.json';
            document.body.appendChild(link);link.click();link.remove();
          }finally{setTimeout(()=>URL.revokeObjectURL(url),3000);}
        });
        result.appendChild(exportReport);
        status.textContent='Local deterministic preflight complete. No agent, tool, or business action was executed.';
      }catch(error){status.textContent='Preflight unavailable: '+String(error?.message||'unknown error');}
      finally{run.disabled=false;}
    });
    row.append(file,run);card.append(row,status,result);
    panel.appendChild(card);
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install);
  else install();
})();