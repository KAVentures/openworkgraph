(() => {
  'use strict';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt=s=>{s=Math.max(0,Number(s)||0);const h=Math.floor(s/3600),m=Math.floor((s%3600)/60);return h?`${h}h ${m}m`:`${m}m`;};
  let policy=null, history=null, access=null, memory=null, outcomes=null, brief=null;

  async function call(url,options={}){
    await window.__owgAuthReady;
    const r=await fetch(url,{cache:'no-store',...options});
    const d=await r.json().catch(()=>({}));
    if(!r.ok)throw new Error(d.detail||'History request failed');
    return d;
  }
  function parseChoice(value){
    if(value==='ephemeral')return {mode:'ephemeral',days:null};
    if(value==='forever')return {mode:'forever',days:null};
    const days=Number(String(value).replace('days:',''))||90;return {mode:'days',days};
  }
  function choiceValue(v){return v?.mode==='days'?`days:${v.days}`:(v?.mode||'ephemeral');}
  const options=selected=>['ephemeral','days:30','days:90','days:365','forever'].map(v=>`<option value="${v}" ${selected===v?'selected':''}>${v==='ephemeral'?'Don’t keep after session':v==='forever'?'Keep until I delete':v==='days:30'?'30 days':v==='days:90'?'90 days':'1 year'}</option>`).join('');

  function ensureUI(){
    const tabs=document.querySelector('.tabs'); const main=document.querySelector('main');
    if(!tabs||!main||document.querySelector('#tab-history'))return;
    const tab=document.createElement('button');tab.type='button';tab.role='tab';tab.id='tab-history';tab.dataset.tab='history';tab.setAttribute('aria-controls','panel-history');tab.setAttribute('aria-selected','false');tab.textContent='History';
    const exportTab=document.querySelector('#tab-export');tabs.insertBefore(tab,exportTab||null);
    const panel=document.createElement('section');panel.className='tabpanel';panel.role='tabpanel';panel.id='panel-history';panel.dataset.panel='history';panel.setAttribute('aria-labelledby','tab-history');panel.hidden=true;
    panel.innerHTML=`
      <div class="card"><h2>History & retention</h2><div class="muted">Capture, keeping history, and letting an AI read saved history are separate choices. Saved history stays on this computer unless you explicitly export or share it.</div><div id="historyPolicyMount" style="margin-top:14px"></div></div>
      <div class="card"><h2>Run memory</h2><div class="muted">A small, content-free summary of each run: steps, outcome, commands used, test results, file and line counts, tokens. No titles, paths, prompts or content. It is kept even after retention deletes the session, so OpenWorkGraph can still learn repeated workflows. Deleting a session or date range yourself deletes its summary too. It never leaves this computer.</div><div id="runMemoryMount" style="margin-top:14px"></div></div>
      <div class="card"><h2>Did agent work hold up?</h2><div class="muted">Off by default. When on, pull requests your agents open are checked through your own <code>gh</code> login (read-only) for merged/closed and CI status, so runs are judged by what happened to the work, not only by how the agent ended. Only the pull request link is kept, on this computer, until it is resolved or 30 days pass. Turning this off deletes every stored link.</div><div id="outcomeMount" style="margin-top:14px"></div></div>
      <div class="card"><h2>Brief agents at session start</h2><div class="muted">Off by default. When on, a new agent session starts with a short, content-free brief from your past runs in the same project: how tests usually end, pull request outcomes, commands used, typical size, tokens. No titles, paths, prompts or content, and it is labeled as observational, not instructions. Every brief sent is counted here.</div><div id="briefMount" style="margin-top:14px"></div></div>
      <div class="card"><h2>AI access to saved history</h2><div class="muted">Current-session AI access does not automatically include older saved work.</div><div id="historyAiMount" style="margin-top:14px"></div></div>
      <div class="card"><div style="display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap"><div><h2>Saved sessions</h2><div class="muted">A human session is one recording run. Long idle breaks stay inside it as activity blocks. Agent sessions use observed execution boundaries.</div></div><button class="secondary" id="historyExportAll">Export retained history JSON</button></div><div id="historySessions" style="margin-top:12px"></div></div>`;
    const exportPanel=document.querySelector('#panel-export');main.insertBefore(panel,exportPanel||null);
    tab.addEventListener('click',()=>{ if(typeof window.activateTab==='function')window.activateTab('history'); else {document.querySelectorAll('.tabpanel').forEach(x=>x.hidden=x!==panel);panel.hidden=false;} refresh(); });
    panel.querySelector('#historyExportAll').addEventListener('click',()=>exportRange());
  }

  function onboarding(){
    if(policy?.onboarding_complete)return;
    const overview=document.querySelector('#panel-overview');if(!overview||document.querySelector('#historyOnboarding'))return;
    const box=document.createElement('div');box.id='historyOnboarding';box.className='card';box.style.border='2px solid #3159a5';
    box.innerHTML=`<h2>How long should OpenWorkGraph remember your work?</h2><div class="muted">If you don’t keep history, live context still works, but OpenWorkGraph cannot answer “last month” questions, compare before/after periods, or build findings across days. You can keep human and agent history separately and change this later. Either way, a content-free summary of each run is kept for 90 days (run memory, no content), which you can turn off under History.</div><div class="modal-actions"><button id="keep90" class="green">Keep 90 days</button><button id="keepNone" class="secondary">Don’t keep after sessions</button><button id="openHistory" class="ghost">Choose separately</button></div>${policy?.upgrade_preserved_existing_history?'<div class="note" style="margin-top:10px">Existing history was preserved during this upgrade. Nothing was deleted automatically.</div>':''}`;
    overview.insertBefore(box,overview.firstChild);
    box.querySelector('#keep90').onclick=()=>quickPolicy('days',90);
    box.querySelector('#keepNone').onclick=()=>quickPolicy('ephemeral',null);
    box.querySelector('#openHistory').onclick=()=>document.querySelector('#tab-history')?.click();
  }

  async function quickPolicy(mode,days){
    await call('/v1/history-policy',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({human_mode:mode,human_days:days,agent_mode:mode,agent_days:days,onboarding_complete:true})});
    document.querySelector('#historyOnboarding')?.remove();await refresh();
  }

  function renderPolicy(){
    const host=document.querySelector('#historyPolicyMount');if(!host||!policy)return;
    const h=choiceValue(policy.human_retention),a=choiceValue(policy.agent_retention);
    host.innerHTML=`<div class="row"><div><label class="label" for="humanRetention">Human workflow history</label><select id="humanRetention" style="width:100%;min-height:42px;border:1px solid #cfd3cb;border-radius:9px;padding:8px">${options(h)}</select></div><div><label class="label" for="agentRetention">Agent run history</label><select id="agentRetention" style="width:100%;min-height:42px;border:1px solid #cfd3cb;border-radius:9px;padding:8px">${options(a)}</select></div></div><div class="note" style="margin-top:12px">“Don’t keep” uses temporary local working storage while the session is active, then deletes it. If OpenWorkGraph crashes, stale ephemeral sessions are deleted on the next start.</div><div class="modal-actions"><button id="saveHistoryPolicy" class="green">Save retention choices</button></div>`;
    host.querySelector('#saveHistoryPolicy').onclick=async()=>{const hc=parseChoice(host.querySelector('#humanRetention').value),ac=parseChoice(host.querySelector('#agentRetention').value);await call('/v1/history-policy',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({human_mode:hc.mode,human_days:hc.days,agent_mode:ac.mode,agent_days:ac.days,onboarding_complete:true})});if(typeof window.toast==='function')window.toast('History retention updated.');await refresh();};
  }

  function renderAccess(){
    const host=document.querySelector('#historyAiMount');if(!host||!access)return;const mode=access.mode||'off';
    host.innerHTML=`<div class="filter-row"><select id="historyAccessMode" style="min-height:42px;border:1px solid #cfd3cb;border-radius:9px;padding:8px"><option value="off" ${mode==='off'?'selected':''}>Off</option><option value="selected_range" ${mode==='selected_range'?'selected':''}>Selected dates</option><option value="all_saved" ${mode==='all_saved'?'selected':''}>All saved history</option></select><input type="date" id="historySince" value="${esc((access.since||'').slice(0,10))}" style="min-height:42px;border:1px solid #cfd3cb;border-radius:9px;padding:8px"><input type="date" id="historyUntil" value="${esc((access.until||'').slice(0,10))}" style="min-height:42px;border:1px solid #cfd3cb;border-radius:9px;padding:8px"><select id="historyExpiry" style="min-height:42px;border:1px solid #cfd3cb;border-radius:9px;padding:8px"><option value="60">1 hour</option><option value="480">8 hours</option><option value="1440">Today / 24h</option></select><button id="saveHistoryAccess" class="${mode==='off'?'secondary':'green'}">Apply</button></div><div class="muted">${mode==='off'?'Saved-history AI access is OFF.':`Access expires ${esc(access.expires_at||'when revoked')}.`} Turning normal AI access off also prevents all MCP reads.</div>`;
    const sync=()=>{const selected=host.querySelector('#historyAccessMode').value==='selected_range';host.querySelector('#historySince').disabled=!selected;host.querySelector('#historyUntil').disabled=!selected;};host.querySelector('#historyAccessMode').onchange=sync;sync();
    host.querySelector('#saveHistoryAccess').onclick=async()=>{const m=host.querySelector('#historyAccessMode').value;let since=null,until=null;if(m==='selected_range'){const a=host.querySelector('#historySince').value,b=host.querySelector('#historyUntil').value;if(!a||!b){if(typeof window.toast==='function')window.toast('Choose both history dates.');return;}since=new Date(a+'T00:00:00Z').toISOString();until=new Date(b+'T00:00:00Z').toISOString();}await call('/v1/history/ai-access',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode:m,since,until,expires_minutes:Number(host.querySelector('#historyExpiry').value)})});if(typeof window.toast==='function')window.toast('Saved-history AI access updated.');await refresh();};
  }

  function memoryLine(r){const w=r.work_summary||{},bits=[];if(w.tests)bits.push(`tests ${w.tests.ended}`);if(w.git)bits.push(Object.keys(w.git).map(k=>'git '+k).join(', '));if(w.files&&w.files.edited)bits.push(`${w.files.edited} file${w.files.edited===1?'':'s'} edited`);if(w.total_tokens)bits.push(`${w.total_tokens} tokens`);const d=r.delivery_outcome;if(d&&d.prs)bits.push(`PR ${d.merged?'merged':d.closed_unmerged?'closed unmerged':d.open?'open':'unknown'}${d.ci&&d.ci!=='none'?`, CI ${d.ci}`:''}`);return bits.join(' · ');}
  function renderMemory(){
    const host=document.querySelector('#runMemoryMount');if(!host||!memory)return;const p=memory.policy||{enabled:true,days:90},rows=memory.runs||[];
    const dayOpts=[30,90,180,365].map(d=>`<option value="${d}" ${Number(p.days)===d?'selected':''}>${d} days</option>`).join('');
    host.innerHTML=`<div class="filter-row"><label style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="runMemoryOn" ${p.enabled?'checked':''}> Keep run memory</label><select id="runMemoryDays" style="min-height:42px;border:1px solid #cfd3cb;border-radius:9px;padding:8px">${dayOpts}</select><button id="saveRunMemory" class="secondary">Save</button><button id="forgetRunMemory" class="ghost">Delete all run memory</button></div><div class="muted">${p.enabled?`${Number(memory.total||0)} runs remembered.`:'Run memory is off; nothing is kept after retention deletes a session.'}</div>${rows.length?`<div class="table-wrap" style="margin-top:10px"><table><thead><tr><th>Run</th><th>When</th><th title="The run’s own end status, e.g. the agent finished its turn. Test results are under What it did.">Agent reported</th><th>What it did</th><th></th></tr></thead><tbody>${rows.slice(0,20).map((r,i)=>`<tr><td><strong>${esc(r.actor_kind==='agent'?((r.agent||{}).name||'Agent'):'Human')}</strong><div class="muted">${esc(r.family_key||'')}</div></td><td>${esc(String(r.started_at||'').replace('T',' ').slice(0,16))}</td><td>${esc(r.outcome_status||'unknown')}</td><td>${esc(memoryLine(r))}</td><td><button class="ghost" data-forget="${i}">Delete</button></td></tr>`).join('')}</tbody></table></div>`:''}`;
    host.querySelector('#saveRunMemory').onclick=async()=>{const on=host.querySelector('#runMemoryOn').checked;if(!on&&!confirm('Turn off run memory? All remembered runs are deleted.'))return;await call('/v1/run-memory/policy',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:on,days:Number(host.querySelector('#runMemoryDays').value)})});if(typeof window.toast==='function')window.toast('Run memory updated.');await refresh();};
    host.querySelector('#forgetRunMemory').onclick=async()=>{if(!confirm('Delete all run memory from this computer?'))return;await call('/v1/run-memory/forget',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({all:true})});await refresh();};
    host.querySelectorAll('[data-forget]').forEach(b=>b.onclick=async()=>{const r=rows[Number(b.dataset.forget)];await call('/v1/run-memory/forget',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({execution_id:r.execution_id})});await refresh();});
  }

  function renderOutcomes(){
    const host=document.querySelector('#outcomeMount');if(!host||!outcomes)return;const o=outcomes;
    const gh=!o.gh_found?'The GitHub CLI (gh) was not found on this computer.':o.enabled&&o.gh_logged_in===false?'gh is installed but not logged in (run gh auth login).':'';
    host.innerHTML=`<div class="filter-row"><label style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="outcomeOn" ${o.enabled?'checked':''}> Track pull request outcomes</label><button id="saveOutcome" class="secondary">Save</button>${o.enabled?'<button id="checkOutcome" class="ghost">Check now</button>':''}</div><div class="muted">${o.enabled?`${Number(o.watching||0)} pull requests being watched · ${Number(o.resolved||0)} resolved${o.last_poll_at?` · last check ${esc(String(o.last_poll_at).replace('T',' ').slice(0,16))}`:''}${o.last_error?` · last problem: ${esc(o.last_error)}`:''}`:'Off: OpenWorkGraph never contacts GitHub.'}${gh?` <strong>${esc(gh)}</strong>`:''}</div>`;
    host.querySelector('#saveOutcome').onclick=async()=>{const on=host.querySelector('#outcomeOn').checked;if(!on&&o.enabled&&!confirm('Turn off outcome tracking? Stored pull request links are deleted.'))return;await call('/v1/outcome-tracking',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:on})});if(typeof window.toast==='function')window.toast('Outcome tracking updated.');await refresh();};
    const now=host.querySelector('#checkOutcome');if(now)now.onclick=async()=>{await call('/v1/outcome-tracking/check-now',{method:'POST'});await refresh();};
  }

  function renderBrief(){
    const host=document.querySelector('#briefMount');if(!host||!brief)return;const fw=(brief.frameworks||{})['claude-code']||{};
    host.innerHTML=`<div class="filter-row"><label style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="briefClaude" ${fw.enabled?'checked':''}> Claude Code</label><button id="saveBrief" class="secondary">Save</button><button id="previewBrief" class="ghost">Preview brief</button></div><div class="muted">${fw.enabled?`On${fw.hook_installed?'':' (hook missing: save again)'} · applies ${esc(brief.applies||'in new sessions')} · ${Number(brief.briefs_delivered||0)} brief${Number(brief.briefs_delivered||0)===1?'':'s'} sent${brief.last_delivered_at?`, last ${esc(String(brief.last_delivered_at).replace('T',' ').slice(0,16))}`:''}`:'Off: agents receive nothing from OpenWorkGraph at session start.'}</div><pre id="briefPreview" hidden style="white-space:pre-wrap;margin-top:10px;padding:10px;border:1px solid #cfd3cb;border-radius:9px;font-size:12px"></pre>`;
    host.querySelector('#saveBrief').onclick=async()=>{try{await call('/v1/agent-brief',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({framework:'claude-code',enabled:host.querySelector('#briefClaude').checked})});if(typeof window.toast==='function')window.toast('Session briefs updated. New Claude Code sessions pick this up.');}catch(e){if(typeof window.toast==='function')window.toast(e.message);}await refresh();};
    host.querySelector('#previewBrief').onclick=async()=>{const p=await call('/v1/agent-brief/preview?framework=claude-code');const pre=host.querySelector('#briefPreview');pre.hidden=false;pre.textContent=p.text||'No brief yet: there are no observed Claude Code runs with work detail in the past 30 days.';};
  }

  function sessionLabel(s){if(s.kind==='agent'){const a=s.agent||{};return `${a.framework||a.provider||a.name||'Agent'} · ${s.observation_level||'partial observation'}`;}return `Human recording · ${fmt(s.engaged_seconds)}`;}
  function renderSessions(){
    const host=document.querySelector('#historySessions');if(!host||!history)return;const rows=history.sessions||[];
    if(!rows.length){host.innerHTML='<div class="muted">No retained sessions yet.</div>';return;}
    host.innerHTML=`<div class="table-wrap"><table><thead><tr><th>Session</th><th>When</th><th>Evidence</th><th>Retention</th><th></th></tr></thead><tbody>${rows.map((s,i)=>`<tr><td><strong>${esc(sessionLabel(s))}</strong>${s.surfaces?.length?`<div class="muted">${esc(s.surfaces.slice(0,5).join(' · '))}</div>`:''}</td><td>${esc(String(s.started_at||'').replace('T',' ').slice(0,16))}<div class="muted">to ${esc(String(s.ended_at||'').replace('T',' ').slice(0,16))}</div></td><td>${Number(s.event_count||0)} events${s.activity_blocks?.length?` · ${s.activity_blocks.length} blocks`:''}</td><td>${esc(s.retention_mode||'')} ${s.expires_at?`<div class="muted">expires ${esc(String(s.expires_at).slice(0,10))}</div>`:''}</td><td><div style="display:flex;gap:6px"><button class="secondary" data-export="${i}">Export range</button><button class="ghost" data-delete="${i}">Delete</button></div></td></tr>`).join('')}</tbody></table></div>`;
    host.querySelectorAll('[data-delete]').forEach(b=>b.onclick=async()=>{const s=rows[Number(b.dataset.delete)];if(!confirm('Delete this retained session from this computer? This cannot recall evidence already synchronized to an organization Gateway.'))return;await call('/v1/history/delete-session',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({history_session_id:s.history_session_id})});if(typeof window.toast==='function')window.toast('Session deleted.');await refresh();});
    host.querySelectorAll('[data-export]').forEach(b=>b.onclick=()=>{const s=rows[Number(b.dataset.export)];exportRange(s.started_at,s.ended_at);});
  }

  async function exportRange(since=null,until=null){
    const q=new URLSearchParams();if(since)q.set('since',since);if(until)q.set('until',until);q.set('include_raw','false');
    const r=await fetch('/v1/history/export-json?'+q.toString(),{cache:'no-store'});if(!r.ok){if(typeof window.toast==='function')window.toast('History export failed.');return;}const blob=await r.blob(),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='openworkgraph-history.json';document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),5000);
  }

  async function refresh(){
    ensureUI();
    try{[policy,history,access,memory,outcomes,brief]=await Promise.all([call('/v1/history-policy'),call('/v1/history?limit=200'),call('/v1/history/ai-access'),call('/v1/run-memory?limit=20').catch(()=>null),call('/v1/outcome-tracking').catch(()=>null),call('/v1/agent-brief').catch(()=>null)]);access=access.access||{mode:'off'};renderPolicy();renderAccess();renderMemory();renderOutcomes();renderBrief();renderSessions();onboarding();}catch(e){const host=document.querySelector('#historySessions');if(host)host.innerHTML=`<div class="off">${esc(e.message)}</div>`;}
  }
  window.refreshHistory=refresh;
  document.addEventListener('DOMContentLoaded',()=>{ensureUI();refresh();});
})();
