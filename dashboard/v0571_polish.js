(() => {
  const KNOWN = [
    [/mail\.google\.com|\bgmail\b/i, 'Gmail'],
    [/docs\.google\.com\/spreadsheets|google sheets|\bsheets\b/i, 'Google Sheets'],
    [/docs\.google\.com\/document|google docs/i, 'Google Docs'],
    [/salesforce/i, 'Salesforce'],
    [/chatgpt|chat\.openai\.com/i, 'ChatGPT'],
    [/claude/i, 'Claude'],
    [/github/i, 'GitHub'],
    [/slack/i, 'Slack'],
    [/outlook|office\.com.*mail/i, 'Outlook'],
    [/microsoft teams|teams\.microsoft/i, 'Microsoft Teams'],
    [/notion/i, 'Notion'],
    [/figma/i, 'Figma'],
    [/linear/i, 'Linear'],
    [/jira/i, 'Jira'],
    [/confluence/i, 'Confluence'],
    [/cursor/i, 'Cursor'],
  ];
  const PALETTE = [
    '#2f6b4f','#8a4b2a','#315a8a','#7a4f9a','#9a6b1f','#3f7a7a',
    '#8b3f5e','#566b2f','#5e5aa0','#a14a3b','#3f6f8f','#6a5a3c',
  ];
  const colorByKey = new Map();
  const keyByColor = new Map();

  function canonicalSurfaceName(value) {
    let raw=String(value||'Unknown')
      .replace(/^Google Chrome\s*[·|\-]\s*/i,'')
      .replace(/^Chrome\s*[·|\-]\s*/i,'')
      .replace(/^Microsoft Edge\s*[·|\-]\s*/i,'')
      .trim() || 'Unknown';
    for (const [matcher,name] of KNOWN) if (matcher.test(raw)) return name;
    return raw;
  }

  function surfaceKey(value) {
    return canonicalSurfaceName(value).toLocaleLowerCase('en-US');
  }

  function hash(value) {
    let h=2166136261;
    for (const c of String(value)) { h^=c.charCodeAt(0); h=Math.imul(h,16777619); }
    return h>>>0;
  }

  function unifiedSurfaceColor(value) {
    const key=surfaceKey(value);
    if (colorByKey.has(key)) return colorByKey.get(key);
    let index=hash(key)%PALETTE.length;
    for (let i=0;i<PALETTE.length;i++) {
      const candidate=PALETTE[(index+i)%PALETTE.length];
      const owner=keyByColor.get(candidate);
      if (!owner || owner===key) {
        colorByKey.set(key,candidate); keyByColor.set(candidate,key); return candidate;
      }
    }
    const hue=hash(key)%360;
    const fallback=`hsl(${hue} 42% 38%)`;
    colorByKey.set(key,fallback);
    return fallback;
  }

  window.canonicalSurfaceName=canonicalSurfaceName;
  window.surfaceName=canonicalSurfaceName;
  window.surfaceKey=surfaceKey;
  window.surfaceColor=unifiedSurfaceColor;

  function to24Hour(text) {
    const value=String(text||'').trim();
    const match=value.match(/^(\d{1,2}):(\d{2})\s*([AP]M)$/i);
    if (!match) return value.replace(/^0(?=\d:)/,'');
    let hour=Number(match[1])%12;
    if (match[3].toUpperCase()==='PM') hour+=12;
    return `${String(hour).padStart(2,'0')}:${match[2]}`;
  }

  function polishTimeline() {
    const host=document.querySelector('#timelineMount');
    if (!host) return;
    const tickSpans=[...host.querySelectorAll('span.muted')].filter(span=>/\b[AP]M\b/i.test(span.textContent||''));
    tickSpans.forEach((span,index)=>{
      span.textContent=to24Hour(span.textContent);
      span.style.whiteSpace='nowrap';
      span.style.fontVariantNumeric='tabular-nums';
      if (index===0) { span.style.left='0'; span.style.right='auto'; span.style.transform='none'; }
      else if (index===tickSpans.length-1) { span.style.left='auto'; span.style.right='0'; span.style.transform='none'; }
    });
  }

  function removeDeveloperNotes() {
    document.querySelectorAll('#evidenceTableView .evidence-meta .muted').forEach(node=>{
      if (/cursor paging is added/i.test(node.textContent||'')) node.remove();
    });
  }

  function findSharingCard() {
    return [...document.querySelectorAll('#panel-organization .card')].find(card=>
      /what would be shared/i.test(card.querySelector('h2')?.textContent||'')
    );
  }

  function yn(value) { return value ? 'Yes' : 'No'; }

  function renderPolicy(payload) {
    const card=findSharingCard();
    if (!card) return;
    const effective=payload.effective_policy;
    const local=payload.local_policy||{};
    if (!payload.policy_current && payload.connected) {
      card.innerHTML=`<h2>What would be shared?</h2><div class="note"><strong>Organization policy is temporarily unavailable.</strong><div class="muted" style="margin-top:5px">OpenWorkGraph sync fails closed in this state; it does not upload using a broader assumed policy.</div></div>${policyTable(local,'Local policy floor')}`;
      return;
    }
    card.innerHTML=`<h2>What would be shared?</h2>${policyTable(effective||local,payload.connected?'Effective local + organization policy':'Local policy — no organization connected')}<div class="muted" style="margin-top:10px">Typed text, clipboard contents, ordinary key identities, password values, and screenshot bytes are never shared by the Gateway path.</div>`;
  }

  function policyTable(policy,title) {
    const allowed=policy.deny_all_event_types ? 'None' : (policy.allowed_event_types||[]).length ? (policy.allowed_event_types||[]).join(', ') : 'All privacy-hardened event types';
    const stripped=(policy.strip_metadata_keys||[]).length ? (policy.strip_metadata_keys||[]).join(', ') : 'None beyond baseline privacy hardening';
    return `<div class="muted" style="margin:4px 0 9px">${escapeHtml(title)}</div><div class="table-wrap"><table style="min-width:0"><tbody><tr><th>Window titles</th><td>${yn(policy.share_window_titles)}</td></tr><tr><th>Safe metadata</th><td>${yn(policy.share_metadata)}</td></tr><tr><th>Locally excluded events</th><td>${yn(policy.share_excluded)}</td></tr><tr><th>Allowed event types</th><td>${escapeHtml(allowed)}</td></tr><tr><th>Additional stripped metadata</th><td>${escapeHtml(stripped)}</td></tr></tbody></table></div>`;
  }

  function escapeHtml(value) {
    if (typeof window.esc==='function') return window.esc(String(value??''));
    const node=document.createElement('div'); node.textContent=String(value??''); return node.innerHTML;
  }

  let policyLoading=false;
  async function refreshSharingPolicy() {
    if (policyLoading) return;
    const active=document.querySelector('[role="tab"][aria-selected="true"]')?.dataset.tab;
    if (active!=='organization') return;
    policyLoading=true;
    try {
      await window.__owgAuthReady;
      const response=await fetch('/v1/sharing-policy',{cache:'no-store'});
      if (!response.ok) throw new Error('policy unavailable');
      renderPolicy(await response.json());
    } catch (_) {
      const card=findSharingCard();
      if (card) card.innerHTML='<h2>What would be shared?</h2><div class="muted">Could not read the effective sharing policy.</div>';
    } finally { policyLoading=false; }
  }
  window.refreshSharingPolicy=refreshSharingPolicy;

  function restyleVisibleSurfaces() {
    document.querySelectorAll('.surface-dot').forEach(dot=>{
      const text=(dot.parentElement?.textContent||'').replace(/^[·\s]+/,'').trim();
      if (text) dot.style.setProperty('--surface',unifiedSurfaceColor(text));
    });
  }

  function polish() {
    removeDeveloperNotes();
    polishTimeline();
    restyleVisibleSurfaces();
    const version=document.querySelector('#versionLabel');
    if (version && /v0\.56\.1|v0\.57\.0/.test(version.textContent||'')) version.textContent='v0.57.1';
  }

  const observer=new MutationObserver(()=>polish());
  function install() {
    polish();
    observer.observe(document.body,{childList:true,subtree:true});
    document.querySelector('#tab-organization')?.addEventListener('click',()=>setTimeout(refreshSharingPolicy,0));
    refreshSharingPolicy();
    setInterval(()=>{ if (!document.hidden) { polish(); refreshSharingPolicy(); } },5000);
    if (window.__owgLastSummary && typeof window.renderActive==='function') window.renderActive(window.__owgLastSummary);
    if (typeof window.refreshOverviewDerived==='function') window.refreshOverviewDerived();
  }

  if (document.readyState==='loading') document.addEventListener('DOMContentLoaded',install); else install();
})();
