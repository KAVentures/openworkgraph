(() => {
  let profiles={};
  let applying=false;

  function ensureCard(){
    const panel=document.querySelector('#panel-organization');
    if(!panel) return null;
    let card=document.querySelector('#browserSignalSettingsCard');
    if(card) return card;
    card=document.createElement('section');
    card.className='card';
    card.id='browserSignalSettingsCard';
    card.innerHTML=`<h2>Capture & privacy</h2>
      <div class="muted">Choose how much browser business context OpenWorkGraph may keep locally. Privacy-first keeps business-object references off and now masks known SaaS object IDs in stored browser paths.</div>
      <div id="browserSensorVersionStatus" style="margin-top:10px"></div>
      <div class="note" style="margin-top:12px"><strong>What is captured</strong><br><span class="muted">Only the bounded browser signals enabled below. Richer business-object context stays opt-in.</span></div>
      <div style="display:grid;gap:10px;margin-top:12px">
        <label class="note"><strong>Privacy profile</strong><br>
          <select id="browserPrivacyProfile" style="margin-top:6px;max-width:320px">
            <option value="privacy_first">Privacy-first</option>
            <option value="context">Context</option>
            <option value="rich_enterprise">Rich enterprise</option>
            <option value="custom">Custom</option>
          </select>
          <div id="browserPrivacyProfileHelp" class="muted" style="margin-top:5px"></div>
        </label>
        <label class="note" style="display:flex;gap:10px;align-items:flex-start;cursor:pointer"><input id="signalBusinessObjectReferences" type="checkbox" style="margin-top:4px"><span><strong>Business-object references</strong> <span class="badge">Off in Privacy-first</span><br><span class="muted">Recognize allowlisted work objects such as a GitHub PR, Google document, Jira issue or Salesforce record. Full URLs, queries, fragments and page contents are not stored.</span></span></label>
        <label class="note" style="display:flex;gap:10px;align-items:flex-start;cursor:pointer"><input id="signalResourceReferenceLocators" type="checkbox" style="margin-top:4px"><span><strong>Connector-resolvable object locators</strong> <span class="badge">Rich enterprise only by preset</span><br><span class="muted">Keep the minimal validated provider-specific record/thread/document locator so an authorized AI connector can fetch it. Context mode keeps only an installation-keyed OWG correlation token instead.</span></span></label>
        <label class="note" style="display:flex;gap:10px;align-items:flex-start;cursor:pointer"><input id="signalPerformanceTiming" type="checkbox" style="margin-top:4px"><span><strong>Browser performance timing</strong><br><span class="muted">Rounded top-frame navigation timing only. No resource URLs or page contents.</span></span></label>
        <label class="note" style="display:flex;gap:10px;align-items:flex-start;cursor:pointer"><input id="signalFileUploadCategory" type="checkbox" style="margin-top:4px"><span><strong>File upload category</strong> <span class="badge">Off by default</span><br><span class="muted">Coarse MIME category only. No filename, path, exact size, hash, or file contents.</span></span></label>
      </div>
      <div class="note" style="margin-top:10px"><strong>Never added by these signals</strong><br><span class="muted">Typed text, clipboard contents, passwords, file names/paths/contents, raw URL query values and page contents remain outside these profiles.</span></div>
      <div id="browserSignalStatus" class="muted" style="margin-top:8px"></div>`;
    panel.prepend(card);
    card.querySelector('#browserPrivacyProfile')?.addEventListener('change',saveProfile);
    for(const id of ['signalPerformanceTiming','signalFileUploadCategory','signalBusinessObjectReferences','signalResourceReferenceLocators']) card.querySelector('#'+id)?.addEventListener('change',saveCustom);
    return card;
  }

  function profileHelp(name){
    if(name==='privacy_first') return 'Business-object references stay off. Known Docs/Drive/GitHub/Salesforce/Jira/Linear object-ID positions are also masked in stored browser paths.';
    if(name==='context') return 'Links repeated work on the same object with an installation-keyed local token, without retaining the provider object ID.';
    if(name==='rich_enterprise') return 'For explicitly trusted/customer-controlled environments: keeps minimal validated provider locators for connector resolution.';
    return 'Custom combination of the controls below.';
  }

  function render(d){
    const s=d?.settings||{};
    profiles=d?.profiles||profiles||{};
    const p=document.querySelector('#signalPerformanceTiming'); if(p) p.checked=!!s.performance_timing;
    const f=document.querySelector('#signalFileUploadCategory'); if(f) f.checked=!!s.file_upload_category;
    const r=document.querySelector('#signalBusinessObjectReferences'); if(r) r.checked=!!s.business_object_references;
    const l=document.querySelector('#signalResourceReferenceLocators'); if(l){l.checked=!!s.resource_reference_locators;l.disabled=!s.business_object_references;}
    const profile=document.querySelector('#browserPrivacyProfile'); if(profile) profile.value=d?.profile||'custom';
    const help=document.querySelector('#browserPrivacyProfileHelp'); if(help) help.textContent=profileHelp(d?.profile||'custom');
  }

  async function renderBrowserVersionNotice(){
    const el=document.querySelector('#browserSensorVersionStatus');
    if(!el) return;
    try{
      const r=await fetch('/v1/summary?scope=current',{cache:'no-store'});
      if(!r.ok) return;
      const d=await r.json();
      const sensor=d?.browser_sensor;
      if(!sensor){el.innerHTML='';return;}
      if(sensor.version_ok===false){
        const current=String(sensor.sensor_version||'older version');
        const expected=String(sensor.expected_sensor_version||d?.expected_browser_sensor_version||'the current version');
        el.className='note';
        el.innerHTML=`<strong>Browser sensor update available</strong><br><span class="muted">Open your browser's extensions page and reload the unpacked OpenWorkGraph extension. Running: ${current}. Expected: ${expected}. Capture continues, but new browser-context features stay unavailable until the sensor is reloaded.</span>`;
      }else{
        el.className='';
        el.innerHTML='';
      }
    }catch(_){}
  }

  async function load(){
    ensureCard();
    try{
      await window.__owgAuthReady;
      const r=await fetch('/v1/browser-signal-settings',{cache:'no-store'});
      if(!r.ok) return;
      render(await r.json());
      await renderBrowserVersionNotice();
    }catch(_){}
  }

  async function post(payload){
    const status=document.querySelector('#browserSignalStatus');
    try{
      await window.__owgAuthReady;
      const r=await fetch('/v1/browser-signal-settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      if(!r.ok) throw new Error('Could not save capture settings');
      const d=await r.json();
      render(d);
      if(status) status.textContent='Saved locally. The paired browser sensor will pick this up automatically.';
      if(typeof window.showToast==='function') window.showToast('Capture & privacy settings saved locally.');
    }catch(e){if(status) status.textContent=e.message||'Could not save settings.';}
  }

  async function saveProfile(){
    if(applying) return;
    const profile=document.querySelector('#browserPrivacyProfile')?.value||'privacy_first';
    if(profile==='custom') return;
    applying=true;
    try{await post({profile});}finally{applying=false;}
  }

  async function saveCustom(){
    if(applying) return;
    const refs=!!document.querySelector('#signalBusinessObjectReferences')?.checked;
    applying=true;
    try{
      await post({
        performance_timing:!!document.querySelector('#signalPerformanceTiming')?.checked,
        file_upload_category:!!document.querySelector('#signalFileUploadCategory')?.checked,
        business_object_references:refs,
        resource_reference_locators:refs&&!!document.querySelector('#signalResourceReferenceLocators')?.checked,
      });
    }finally{applying=false;}
  }

  function install(){ensureCard();load();document.querySelector('#tab-organization')?.addEventListener('click',()=>setTimeout(load,0));}
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',install); else install();
})();
