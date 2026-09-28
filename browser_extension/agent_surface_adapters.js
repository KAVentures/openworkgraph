(() => {
  'use strict';

  const PROVIDERS = [
    {key:'chatgpt', name:'ChatGPT', hosts:['chatgpt.com','chat.openai.com']},
    {key:'claude', name:'Claude', hosts:['claude.ai']},
    {key:'microsoft_copilot', name:'Microsoft Copilot', hosts:['copilot.microsoft.com','copilot.microsoft365.com','m365.cloud.microsoft']},
    {key:'lovable', name:'Lovable', hosts:['lovable.dev']},
    {key:'gemini', name:'Gemini', hosts:['gemini.google.com']},
  ];
  // Structural signals come first and work in any UI language: aria-busy,
  // streaming markers, stable data-testid tokens and submit semantics. English
  // labels remain only a fallback. No prompt/response text is read.
  const BUSY_SELECTOR='[aria-busy="true"],[data-is-streaming="true"],button[data-testid*="stop" i]';
  const SEND_TESTID=/(^|[-_])(send|submit)([-_]|$)/i;
  const STOP_TESTID=/(^|[-_])(stop|cancel)([-_]|$)/i;
  const COMPOSER_TESTID=/(composer|prompt|chat[-_]?input|message[-_]?input)/i;
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
  function controlOf(el){return el?.closest?.('button,[role="button"],input[type="submit"]')||null;}
  function buttonLike(el){return !!controlOf(el);}
  function testId(el){return String(el?.getAttribute?.('data-testid')||'');}
  function usable(el){
    if(!el)return false;
    return el.hidden!==true&&String(el.getAttribute?.('aria-hidden')||'').toLowerCase()!=='true'&&el.disabled!==true;
  }
  function hasBusyState(doc){
    if(!doc)return false;
    if([...doc.querySelectorAll(BUSY_SELECTOR)].some(usable))return true;
    return [...doc.querySelectorAll('button,[role="button"]')].some(el=>usable(el)&&STOP_RE.test(accessibleName(el)));
  }
  function isComposer(el){
    if(!el||typeof el.closest!=='function')return false;
    const tag=String(el.tagName||'').toLowerCase();
    return tag==='textarea'||el.isContentEditable===true||String(el.getAttribute?.('contenteditable')||'')==='true'
      ||String(el.getAttribute?.('role')||'')==='textbox';
  }
  function formHasComposer(form){
    if(!form||typeof form.querySelectorAll!=='function')return false;
    return [...form.querySelectorAll('textarea,[contenteditable="true"],[role="textbox"]')].some(isComposer);
  }
  function structurallyComposer(el){
    if(!isComposer(el))return false;
    const form=el.closest?.('form');
    if(form&&formHasComposer(form))return true;
    const marked=el.closest?.('[data-testid]');
    return !!marked&&COMPOSER_TESTID.test(testId(marked));
  }
  // Enter is considered a send only in a structurally identified message
  // composer. This avoids treating unrelated textareas/editors on agent sites as
  // new runs. Busy-state detection remains the fallback when a site has no such
  // structural marker.
  function isComposerSend(ev){
    return ev?.key==='Enter'&&!ev.shiftKey&&!ev.altKey&&!ev.ctrlKey&&!ev.metaKey&&!ev.isComposing&&structurallyComposer(ev.target);
  }
  function hasVisibleErrorState(doc){return !!doc?.querySelector?.('[role="alert"][aria-live], [role="alert"]');}
  function hasApprovalState(doc){
    if(!doc)return false;
    for(const root of doc.querySelectorAll('dialog,[role="dialog"]')){
      if([...root.querySelectorAll('button,[role="button"]')].some(el=>usable(el)&&APPROVE_RE.test(accessibleName(el))))return true;
    }
    return false;
  }
  function isSendControl(el){
    const control=controlOf(el);if(!control||!usable(control))return false;
    if(SEND_TESTID.test(testId(control)))return true;
    const type=String(control.getAttribute?.('type')||'').toLowerCase();
    if(type==='submit'&&control.form&&formHasComposer(control.form))return true;
    return SEND_RE.test(accessibleName(control));
  }
  function isStopControl(el){
    const control=controlOf(el);if(!control||!usable(control))return false;
    if(STOP_TESTID.test(testId(control)))return true;
    return STOP_RE.test(accessibleName(control));
  }
  function isApprovalControl(el){const control=controlOf(el);return !!control&&usable(control)&&APPROVE_RE.test(accessibleName(control))&&!!control.closest('dialog,[role="dialog"]');}
  function safeRunId(){
    try{return 'web-'+crypto.randomUUID().replaceAll('-','');}catch(_){return 'web-'+Date.now().toString(36)+Math.random().toString(36).slice(2,12);}
  }
  function structuralPayload(provider, action, runId, source){
    return {
      type:'workflow_observer_event',
      observed_at:new Date().toISOString(),
      action,
      // background.js derives the real safe page URL from sender.tab. Deliberately
      // do not send conversation paths, titles, prompts, responses or alert text.
      page:{url:location.origin+'/',title:provider.name},
      target:{role:'agent-lifecycle',label:provider.name},
      metadata:{agent_provider:provider.key,agent_run_id:runId,state_source:String(source||'semantic_state').slice(0,40),top_frame:true}
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
        if(hasVisibleErrorState(doc)&&!errorSent){sendLifecycle(provider,'agent_error',runId,'aria_alert_present');errorSent=true;}
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
    doc.addEventListener('submit',ev=>{if(formHasComposer(ev.target)){begin('form_submit');setTimeout(sample,0);}},true);
    doc.addEventListener('keydown',ev=>{if(isComposerSend(ev)){begin('composer_enter');setTimeout(sample,0);}},true);
    const observer=new MutationObserver(()=>sample());
    const root=doc.documentElement||doc;observer.observe(root,{subtree:true,childList:true,attributes:true,attributeFilter:['aria-busy','aria-live','role','open','disabled','hidden','aria-hidden','data-is-streaming','data-testid']});
    setInterval(()=>{if(!doc.hidden)sample();},1500);
    sample();
  }

  globalThis.__OWG_AGENT_SURFACE_ADAPTERS_FOR_TESTS__={PROVIDERS,providerForHost,accessibleName,hasBusyState,hasVisibleErrorState,hasApprovalState,isSendControl,isStopControl,isApprovalControl,isComposerSend,structuralPayload};
  if(typeof document!=='undefined'&&typeof MutationObserver!=='undefined'){
    if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',()=>start(),{once:true});else start();
  }
})();
