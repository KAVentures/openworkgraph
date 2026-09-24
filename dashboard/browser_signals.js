(() => {
  function ensureCard(){
    const panel=document.querySelector('#panel-organization');
    if(!panel) return null;
    let card=document.querySelector('#browserSignalSettingsCard');
    if(card) return card;
    card=document.createElement('section');
    card.className='card';
    card.id='browserSignalSettingsCard';
    card.innerHTML=`<h2>What is captured</h2>
      <div class="muted">Derived metrics reuse existing evidence. Only the two browser metadata signals below add new recorded fields.</div>
      <div style="display:grid;gap:10px;margin-top:12px">
        <label class="note" style="display:flex;gap:10px;align-items:flex-start;cursor:pointer"><input id="signalPerformanceTiming" type="checkbox" style="margin-top:4px"><span><strong>Browser performance timing</strong><br><span class="muted">Rounded top-frame navigation timing only. No resource URLs or page contents.</span></span></label>
        <label class="note" style="display:flex;gap:10px;align-items:flex-start;cursor:pointer"><input id="signalFileUploadCategory" type="checkbox" style="margin-top:4px"><span><strong>File upload category</strong> <span class="badge">Off by default</span><br><span class="muted">Coarse MIME category only. No filename, path, exact size, hash, or file contents.</span></span></label>
      </div>
      <div class="muted" style="margin-top:10px">Fragmentation, rapid-click candidates, auth-flow timing, manual transfers, daily rhythm, AI-tool usage and navigation candidates are derived from evidence OWG already records; they do not add sensors.</div>
      <div class="muted" style="margin-top:7px">Never added by these signals: typed text, clipboard contents, ordinary key identities, microphone state, file names/paths/sizes/contents.</div>
      <div id="browserSignalStatus" class="muted" style="margin-top:8px"></div>`;
    panel.prepend(card);
    for(const id of ['signalPerformanceTiming','signalFileUploadCategory']) card.querySelector('#'+id)?.addEventListener('change',save);
    return card;
  }
  async function load(){
    ensureCard();
    try{
      await window.__owgAuthReady;
      const r=await fetch('/v1/browser-signal-settings',{cache:'no-store'});
      if(!r.ok) return;
      const d=await r.json();
      const s=d.settings||{};
      const p=document.querySelector('#signalPerformanceTiming'); if(p) p.checked=!!s.performance_timing;
      const f=document.querySelector('#signalFileUploadCategory'); if(f) f.checked=!!s.file_upload_category;
    }catch(_){}
  }
  async function save(){
    const status=document.querySelector('#browserSignalStatus');
    try{
      await window.__owgAuthReady;
      const payload={performance_timing:!!document.querySelector('#signalPerformanceTiming')?.checked,file_upload_category:!!document.querySelector('#signalFileUploadCategory')?.checked};
      const r=await fetch('/v1/browser-signal-settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      if(!r.ok) throw new Error('Could not save signal settings');
      if(status) status.textContent='Saved locally. The paired browser sensor will pick this up automatically.';
      if(typeof window.showToast==='function') window.showToast('Browser signal settings saved locally.');
    }catch(e){if(status) status.textContent=e.message||'Could not save settings.';}
  }
  function install(){ensureCard();load();document.querySelector('#tab-organization')?.addEventListener('click',()=>setTimeout(load,0));}
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',install); else install();
})();
