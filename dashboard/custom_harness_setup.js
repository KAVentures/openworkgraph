(() => {
  let cache=null;
  const esc=value=>{const node=document.createElement('div');node.textContent=String(value??'');return node.innerHTML;};

  function installStyle(){
    if(document.querySelector('#owg-custom-harness-style'))return;
    const style=document.createElement('style');style.id='owg-custom-harness-style';
    style.textContent=`
      .harness-card{border:1px solid var(--line);border-radius:14px;padding:15px;background:#f8faf8;display:flex;flex-direction:column;min-height:205px}
      .harness-card h3{font-size:15px;margin:0 0 5px}.harness-card p{font-size:12.5px;color:var(--muted);line-height:1.45;margin:0 0 12px}.harness-card .actions{margin-top:auto}
      .harness-badges{display:flex;gap:6px;flex-wrap:wrap;margin:4px 0 10px}.harness-badge{font-size:10.5px;border-radius:999px;padding:3px 7px;background:#eef3ee;color:#385143;font-weight:700}
      .harness-methods{display:flex;gap:6px;flex-wrap:wrap;margin:10px 0}.harness-methods button{min-height:32px;padding:5px 9px;font-size:12px}.harness-methods button.active{background:#2f7d55;color:#fff}
      .harness-code{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f4f0;border:1px solid var(--line);border-radius:9px;padding:10px;font:12px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;margin:7px 0}
      .harness-two-way{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:12px 0}.harness-two-way>div{border:1px solid var(--line);border-radius:10px;padding:10px;font-size:12px}.harness-two-way strong{display:block;margin-bottom:3px}
      @media(max-width:620px){.harness-two-way{grid-template-columns:1fr}}
    `;
    document.head.appendChild(style);
  }

  async function load(){
    if(cache)return cache;
    await window.__owgAuthReady;
    const response=await fetch('/v1/custom-harness-setup',{cache:'no-store'});
    if(!response.ok)throw new Error('custom harness setup unavailable');
    cache=await response.json();return cache;
  }

  function injectCard(){
    const section=document.querySelector('#agent-observation-setup');
    if(!section||document.querySelector('#customHarnessCard'))return;
    const grid=section.querySelector('.setup-grid');if(!grid)return;
    const card=document.createElement('div');card.id='customHarnessCard';card.className='harness-card';
    card.innerHTML=`<div class="setup-tag">Any framework</div><h3>Your own agent / harness</h3><div class="harness-badges"><span class="harness-badge">OpenClaw / Hermes-style</span><span class="harness-badge">Internal agents</span><span class="harness-badge">Any MCP client</span></div><p>Connect an arbitrary harness in either or both directions: send its structural execution to OpenWorkGraph and optionally let it read the OWG context you authorize.</p><div class="actions"><button type="button" id="customHarnessSetupButton">Connect your agent</button></div>`;
    grid.appendChild(card);
    card.querySelector('#customHarnessSetupButton').onclick=openSetup;
  }

  function code(text,id){return `<div class="harness-code" id="${esc(id)}">${esc(text||'')}</div><button class="secondary" type="button" data-harness-copy="${esc(id)}">Copy</button>`;}
  function privacy(payload){
    const p=payload.privacy||{};
    return `<div class="setup-privacy"><div>${p.prompt_content?'⚠':'✓'} No prompt content</div><div>${p.model_response_content?'⚠':'✓'} No response content</div><div>${p.tool_arguments?'⚠':'✓'} No tool arguments</div><div>${p.tool_results?'⚠':'✓'} No tool results</div><div>${p.reasoning?'⚠':'✓'} No reasoning</div><div>${p.exception_text?'⚠':'✓'} No exception text</div></div>`;
  }

  function methodBody(payload,method){
    const write=payload.write||{};
    if(method==='python'){
      const x=write.python||{};
      return `<p><strong>Python SDK.</strong> A dependency-free helper with context managers for runs, models and tools. It is fail-open: OWG going down never stops the agent.</p><h3>Install the tiny SDK</h3>${code(x.install,'harnessPyInstall')}<h3 style="margin-top:14px">Give it the local write-only credential</h3>${code(x.environment,'harnessPyEnv')}<h3 style="margin-top:14px">Instrument the harness</h3>${code(x.example,'harnessPyExample')}`;
    }
    if(method==='typescript'){
      const x=write.typescript||{};
      return `<p><strong>TypeScript / Node.</strong> Dependency-free ESM with TypeScript declarations. Node 18+.</p><h3>Get the SDK files</h3>${code(x.download,'harnessTsDownload')}<h3 style="margin-top:14px">Give it the local write-only credential</h3>${code(x.environment,'harnessTsEnv')}<h3 style="margin-top:14px">Instrument the harness</h3>${code(x.example,'harnessTsExample')}`;
    }
    if(method==='otel'){
      const x=write.otel||{};
      const token=(write.raw_http||{}).authorization||'';
      const env=`export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="${x.endpoint||''}"\nexport OTEL_EXPORTER_OTLP_TRACES_PROTOCOL="http/json"\nexport OTEL_EXPORTER_OTLP_TRACES_HEADERS="Authorization=${token}"`;
      return `<p><strong>OpenTelemetry.</strong> If the harness already emits portable GenAI spans, this is the least invasive option. OWG accepts OTLP/HTTP JSON and ignores unknown spans rather than guessing.</p>${code(env,'harnessOtelEnv')}`;
    }
    const x=write.raw_http||{};
    const curl=`curl -X POST ${x.endpoint||''} \\\n  -H 'Content-Type: application/json' \\\n  -H 'Authorization: ${x.authorization||''}' \\\n  --data '${String(x.example||'').replaceAll("'","'\\''")}'`;
    return `<p><strong>Raw HTTP.</strong> For any language/runtime: POST the canonical structural envelope directly. The credential is write-only.</p>${code(curl,'harnessRawHttp')}<div class="note">The server validates the complete batch and rejects content-bearing fields such as prompts, responses, messages, reasoning, tool arguments and tool results.</div>`;
  }

  async function openSetup(){
    try{
      const payload=await load();
      const body=`<p>Use either direction independently. Observing an agent does <strong>not</strong> let it read your work history, and giving it MCP context does <strong>not</strong> automatically record its execution.</p><div class="harness-two-way"><div><strong>Agent → OpenWorkGraph</strong>Structural run/model/tool/handoff/approval/error telemetry through a write-only credential.</div><div><strong>OpenWorkGraph → Agent</strong>Optional MCP context, still controlled by OWG's AI-access switch and saved-history lease.</div></div>${privacy(payload)}<h3>1. Observe this harness</h3><div class="harness-methods" id="harnessMethods"><button type="button" data-method="python" class="active">Python</button><button type="button" data-method="typescript">TypeScript / Node</button><button type="button" data-method="otel">OpenTelemetry</button><button type="button" data-method="raw">Raw HTTP</button></div><div id="harnessMethodBody">${methodBody(payload,'python')}</div><h3 style="margin-top:18px">2. Optional: let the harness read OWG context via MCP</h3><p class="muted">Paste this standard stdio MCP server entry into any MCP-capable harness. Context access remains OFF unless you enable OWG's AI-access master switch; historical reads additionally require your saved-history lease.</p>${code(JSON.stringify(payload.read?.config||{},null,2),'harnessMcpConfig')}<div class="note">The telemetry token above cannot read anything. MCP is a separate connection and permission path.</div>`;
      window.openModal?.('Connect your agent','Custom harness · two-way connection',body);
      bind(payload);
    }catch(_){window.openModal?.('Custom harness setup unavailable','Connect','<p>Could not generate local setup material. Confirm OpenWorkGraph is running and reload the dashboard.</p>');}
  }

  function bind(payload){
    document.querySelectorAll('[data-harness-copy]').forEach(button=>button.onclick=()=>copy(button.dataset.harnessCopy||'',button));
    document.querySelectorAll('#harnessMethods [data-method]').forEach(button=>button.onclick=()=>{
      document.querySelectorAll('#harnessMethods [data-method]').forEach(x=>x.classList.toggle('active',x===button));
      const target=document.querySelector('#harnessMethodBody');if(target)target.innerHTML=methodBody(payload,button.dataset.method||'python');
      document.querySelectorAll('[data-harness-copy]').forEach(copyButton=>copyButton.onclick=()=>copy(copyButton.dataset.harnessCopy||'',copyButton));
    });
  }

  async function copy(id,button){
    const text=document.querySelector(`#${CSS.escape(id)}`)?.textContent||'';
    try{await navigator.clipboard.writeText(text);const before=button.textContent;button.textContent='Copied';setTimeout(()=>button.textContent=before,1100);}catch(_){window.prompt('Copy this:',text);}
  }

  function install(){installStyle();injectCard();setTimeout(injectCard,150);setTimeout(injectCard,800);}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install);else install();
})();
