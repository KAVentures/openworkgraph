(() => {
  'use strict';

  const PROVIDERS = [
    {key:'chatgpt', name:'ChatGPT', hosts:['chatgpt.com','chat.openai.com']},
    {key:'claude', name:'Claude', hosts:['claude.ai']},
    {key:'microsoft_copilot', name:'Microsoft Copilot', hosts:['copilot.microsoft.com','copilot.microsoft365.com','m365.cloud.microsoft']},
    {key:'lovable', name:'Lovable', hosts:['lovable.dev']},
    {key:'gemini', name:'Gemini', hosts:['gemini.google.com']},
  ];
  const STOP_RE=/^(stop|cancel)( generating| response| task| run)?$/i;
  const SEND_RE=/^(send|submit|ask|run|build|generate)( prompt| message| request)?$/i;
  const APPROVE_RE=/^(allow|approve|confirm|continue|accept)$/i;

  function providerForHost(host){
    const h=String(host||'').toLowerCase().replace(/^www\./,'');
    return PROVIDERS.find(p=>p.hosts.some(x=>h===x||h.endsWith('.'+x)))||null;
  }
  function accessibleName(el){
    if(!el)return '';
    return String(el.getAttribute?.('aria-label')||el.getAttribute?.('title')||el.textContent||'').replace(/\s+/g,' ').trim().slice(0,80);
  }
  function buttonLike(el){return !!el?.closest?.('button,[role="button"],input[type="submit"]');}
  function hasBusyState(doc){
    if(!doc)return false;
    if(doc.querySelector('[aria-busy="true"]'))return true;
    return [...doc.querySelectorAll('button,[role="button"]')].some(el=>STOP_RE.test(accessibleName(el)));
  }
  function hasVisibleErrorState(doc){return !!doc?.querySelector?.('[role="alert"][aria-live], [role="alert"]');}
  function hasApprovalState(doc){
    if(!doc)return false;
    for(const root of doc.querySelectorAll('dialog,[role="dialog"]')){
      if([...root.querySelectorAll('button,[role="button"]')].some(el=>APPROVE_RE.test(accessibleName(el))))return true;
    }
    return false;
  }
  function isSendControl(el){const control=buttonLike(el);return !!control&&SEND_RE.test(accessibleName(control));}
  function isStopControl(el){const control=buttonLike(el);return !!control&&STOP_RE.test(accessibleName(control));}
  function isApprovalControl(el){const control=buttonLike(el);return !!control&&APPROVE_RE.test(accessibleName(control))&&!!control.closest('dialog,[role="dialog"]');}
  function safeRunId(){
    try{return 'web-'+crypto.randomUUID().replaceAll('-','');}catch(_){return 'web-'+Date.now().toString(36)+Math.random().toString(36).slice(2,12);}
  }
  function structuralPayload(provider, action, runId, source){
    return {
      type:'workflow_observer_event',
      payload:{
        observed_at:new Date().toISOString(),
        browser_session_id:'',
        action,
        page:{origin:location.origin,hostname:location.hostname,pathname:'/',title:provider.name},
        target:{role:'agent-lifecycle',label:provider.name},
        metadata:{agent_provider:provider.key,agent_run_id:runId,state_source:String(source||'semantic_state').slice(0,40)}
      }
    };
  }
  function sendLifecycle(provider,action,runId,source){
    // No DOM text or user/model content is included in this message.
    const message=structuralPayload(provider,action,runId,source);
    try{
      const runtime=(typeof browser!=='undefined'&&browser.runtime)?browser.runtime:(typeof chrome!=='undefined'&&chrome.runtime?chrome.runtime:null);
      runtime?.sendMessage(message).catch?.(()=>{});
    }catch(_){}
  }

  function start(doc=typeof document!=='undefined'?document:null){
    if(!doc||typeof location==='undefined')return;
    const provider=providerForHost(location.hostname);if(!provider)return;
    let runId='',wasBusy=false,errorSent=false,approvalSent=false,finishTimer=null;
    const begin=source=>{if(runId)return;runId=safeRunId();wasBusy=false;errorSent=false;approvalSent=false;sendLifecycle(provider,'agent_run_started',runId,source);};
    const reset=()=>{runId='';wasBusy=false;errorSent=false;approvalSent=false;if(finishTimer){clearTimeout(finishTimer);finishTimer=null;}};
    const finish=(action='agent_run_finished',source='busy_cleared')=>{if(!runId)return;sendLifecycle(provider,action,runId,source);reset();};
    const sample=()=>{
      const busy=hasBusyState(doc);
      if(busy&&!runId)begin('busy_state');
      if(runId){
        if(busy)wasBusy=true;
        if(hasVisibleErrorState(doc)&&!errorSent){sendLifecycle(provider,'agent_error',runId,'aria_alert');errorSent=true;}
        if(hasApprovalState(doc)&&!approvalSent){sendLifecycle(provider,'agent_approval_requested',runId,'semantic_dialog');approvalSent=true;}
        if(wasBusy&&!busy&&!finishTimer){finishTimer=setTimeout(()=>{finishTimer=null;if(runId&&!hasBusyState(doc))finish();},900);}
        if(busy&&finishTimer){clearTimeout(finishTimer);finishTimer=null;}
      }
    };
    doc.addEventListener('click',ev=>{
      if(isSendControl(ev.target)){begin('send_control');setTimeout(sample,0);return;}
      if(runId&&isStopControl(ev.target)){finish('agent_run_cancelled','stop_control');return;}
      if(runId&&isApprovalControl(ev.target)){sendLifecycle(provider,'agent_approval_received',runId,'approval_control');approvalSent=true;}
    },true);
    doc.addEventListener('submit',()=>{begin('form_submit');setTimeout(sample,0);},true);
    const observer=new MutationObserver(()=>sample());
    const root=doc.documentElement||doc;observer.observe(root,{subtree:true,childList:true,attributes:true,attributeFilter:['aria-busy','aria-live','role','open','disabled']});
    setInterval(()=>{if(!doc.hidden)sample();},1500);
    sample();
  }

  globalThis.__OWG_AGENT_SURFACE_ADAPTERS_FOR_TESTS__={PROVIDERS,providerForHost,accessibleName,hasBusyState,hasVisibleErrorState,hasApprovalState,isSendControl,isStopControl,isApprovalControl,structuralPayload};
  if(typeof document!=='undefined'&&typeof MutationObserver!=='undefined'){
    if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',()=>start(),{once:true});else start();
  }
})();
