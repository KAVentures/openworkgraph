/* Explicit local export from a reviewed OWG process proposal. */
(() => {
  'use strict';
  let exporting=false;

  function validate(packet) {
    return !!packet && typeof packet==='object' &&
      packet.format==='openworkgraph.proposed-process-design.v1' &&
      packet.status==='human_reviewed_draft_not_approved_policy' &&
      packet.user_reviewed_export===true &&
      packet.authorized_agent_actions===false &&
      packet.agent_execution_triggered===false &&
      packet.approved_business_policy===false &&
      packet.source_case_ids_included===false &&
      packet.uploaded_data_retained_by_owg===false;
  }
  async function prepare(packet) {
    if(!validate(packet))throw new Error('Review the non-executable process proposal first.');
    if(exporting)throw new Error('Starter export already in progress.');
    exporting=true;
    try {
      if(!await window.__owgAuthReady)
        throw new Error('Reopen the current authenticated local OWG dashboard.');
      const response=await fetch('/v1/process-design/starter',{
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify(packet),cache:'no-store'
      });
      if(!response.ok){
        const data=await response.json().catch(()=>({}));
        throw new Error(String(data.detail||('Starter export failed ('+response.status+')')));
      }
      const zip=await response.blob();
      if(zip.size>512*1024)throw new Error('Starter archive exceeds expected size.');
      const url=URL.createObjectURL(zip);
      try {
        const a=document.createElement('a');
        a.href=url;a.download='openworkgraph-agent-starter.zip';a.hidden=true;
        document.body.appendChild(a);a.click();a.remove();
      }finally{setTimeout(()=>URL.revokeObjectURL(url),3000);}
      return true;
    }finally{exporting=false;}
  }
  window.owgAgentStarter={prepare};
  window.__owgAgentStarterModel={validate};
})();