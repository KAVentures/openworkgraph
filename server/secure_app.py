from __future__ import annotations

import os
import sys
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from .local_auth import (
    bearer_matches,
    browser_server_proof,
    consume_export_ticket,
    consume_pairing_code,
    dashboard_session_valid,
    ensure_browser_secret,
    exchange_dashboard_bootstrap,
    issue_export_ticket,
    local_security_note,
    new_pairing_code,
    verify_browser_authorization,
)
from .main import DASHBOARD, app

COLLECTOR_ROUTES = {("POST", "/v1/events"), ("POST", "/v1/heartbeat")}
BROWSER_ROUTES = {
    ("GET", "/v1/browser-context"),
    ("POST", "/v1/browser-events"),
    ("POST", "/v1/browser-heartbeat"),
}
BROWSER_PATHS = {path for _, path in BROWSER_ROUTES}
PUBLIC_PATHS = {"/health"}
EXTENSION_PREFIXES = ("chrome-extension://", "moz-extension://", "safari-web-extension://")
BLOCKED_DEV_PATHS = {"/openapi.json", "/redoc"}
ALLOWED_LOCAL_HOSTS = {"127.0.0.1", "localhost", "testserver", "::1"}


def _json_error(detail: str, status: int) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status)


def _extension_cors(response: Response, origin: str) -> Response:
    if origin.startswith(EXTENSION_PREFIXES):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "content-type, authorization"
        response.headers["Access-Control-Max-Age"] = "300"
    return response


def _request_host_allowed(request: Request) -> bool:
    raw = str(request.headers.get("host") or "").strip().lower()
    if not raw:
        return False
    if raw.startswith("[") and "]" in raw:
        hostname = raw[1:raw.index("]")]
    elif ":" in raw:
        hostname = raw.rsplit(":", 1)[0]
    else:
        hostname = raw
    return hostname in ALLOWED_LOCAL_HOSTS


def _dashboard_authenticated(request: Request) -> bool:
    raw = str(request.headers.get("authorization") or "")
    if not raw.lower().startswith("owg-session "):
        return False
    return dashboard_session_valid(raw[len("owg-session "):].strip())


def _api_authenticated(request: Request) -> bool:
    return bearer_matches(request.headers.get("authorization")) or _dashboard_authenticated(request)


def _bootstrap_script() -> str:
    """Authenticate the launched dashboard without exposing a host-scoped cookie."""
    return r"""
<script>
(() => {
  const nativeFetch = window.fetch.bind(window);
  const SESSION_KEY = 'owg_dashboard_session_v1';
  let dashboardSession = sessionStorage.getItem(SESSION_KEY) || '';
  const fragment = new URLSearchParams((location.hash || '').replace(/^#/, ''));
  const bootstrap = fragment.get('bootstrap') || '';
  if (bootstrap) history.replaceState(null, '', location.pathname + location.search);

  window.__owgAuthReady = (async () => {
    if (dashboardSession) return true;
    if (!bootstrap) return false;
    try {
      const r = await nativeFetch('/v1/dashboard-session', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({bootstrap}),
        cache: 'no-store'
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || !d.session) return false;
      dashboardSession = String(d.session);
      sessionStorage.setItem(SESSION_KEY, dashboardSession);
      return true;
    } catch (_) { return false; }
  })();

  window.fetch = async (input, init={}) => {
    const url = typeof input === 'string' ? input : String(input?.url || '');
    if (!url.startsWith('/v1/') || url === '/v1/dashboard-session') return nativeFetch(input, init);
    const authenticated = await window.__owgAuthReady;
    const headers = new Headers(init.headers || (typeof Request !== 'undefined' && input instanceof Request ? input.headers : undefined));
    if (authenticated && dashboardSession && !headers.has('Authorization')) {
      headers.set('Authorization', `OWG-Session ${dashboardSession}`);
    }
    const response = await nativeFetch(input, {...init, headers});
    if (response.status === 401 && dashboardSession) {
      dashboardSession = '';
      sessionStorage.removeItem(SESSION_KEY);
    }
    return response;
  };
})();
</script>
"""


def _connection_override_script() -> str:
    """Add stdio-first MCP setup, per-run AI access, audit visibility and safe exports."""
    return r"""
<script>
(() => {
  async function jsonCall(url, options={}){
    await window.__owgAuthReady;
    const r=await fetch(url,{cache:'no-store',...options});
    let d={}; try{d=await r.json();}catch(_){}
    if(!r.ok) throw new Error(d.detail||d.error||'OpenWorkGraph request failed');
    return d;
  }

  window.downloadExport = async function(fmt){
    try{
      const raw=rawEnabled();
      const d=await jsonCall('/v1/export-ticket',{
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({format:String(fmt||''),scope:'current',include_raw:raw})
      });
      const a=document.createElement('a');
      a.href=d.url;
      a.style.display='none';
      document.body.appendChild(a);
      a.click();
      a.remove();
    }catch(e){
      openModal('Export unavailable','Local security',`<p>${esc(e.message||'Could not authorize this export.')}</p><div class="note">If OpenWorkGraph restarted, reopen the dashboard from the launcher and try again.</div>`);
    }
  };

  async function connectionConfig(){return jsonCall('/v1/mcp-connection-config');}
  async function setAiAccess(enabled){return jsonCall('/v1/ai-access',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:!!enabled})});}
  async function httpMcp(action){return jsonCall('/v1/mcp-http',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action})});}
  async function ensureAiAccess(){const s=await jsonCall('/v1/ai-access');if(!s.enabled)await setAiAccess(true);}
  function stdioObject(c){return {command:c.command,args:c.args,env:c.env};}

  window.connectCursor = async function(){
    try{
      await ensureAiAccess();
      const c=await connectionConfig();
      const config=b64(JSON.stringify(stdioObject(c)));
      window.location.href=`cursor://anysphere.cursor-deeplink/mcp/install?name=OpenWorkGraph&config=${encodeURIComponent(config)}`;
      setTimeout(refreshAiPanel,800);
    }catch(e){openModal('Connection unavailable','Local security',`<p>${esc(e.message||'Could not create the local MCP connection.')}</p><div class="note">Reopen the dashboard from the OpenWorkGraph launcher and try again.</div>`);}
  };

  window.showBrowserPairingCode = async function(){
    try{
      const d=await jsonCall('/v1/browser-pairing-code',{method:'POST'});
      openModal('Pair / repair browser sensor','Local browser authentication',`<p>Normal installs pair automatically. Use this fallback only if the browser sensor says it is not paired.</p><ol class="steps"><li>Click the OpenWorkGraph browser extension icon.</li><li>Enter this 8-digit code within ${esc(d.expires_in_seconds||120)} seconds:</li></ol><div class="codebox" style="font-size:22px;letter-spacing:.14em;text-align:center">${esc(d.code)}</div><div class="note">The code can be used only once and locks after repeated wrong attempts. Browser evidence stays queued until the genuine OpenWorkGraph server proves its identity.</div>`);
    }catch(e){openModal('Pairing unavailable','Local security',`<p>${esc(e.message||'Could not create a pairing code.')}</p>`);}
  };

  async function claudeConfig(){
    const c=await connectionConfig();
    return JSON.stringify({mcpServers:{openworkgraph:stdioObject(c)}},null,2);
  }

  const originalOpenConnect=window.openConnect;
  window.openConnect=async function(kind){
    if(kind==='chooser'){
      openModal('Connect your AI','MCP',`<p><strong>Export</strong> is the universal path. For ongoing local access, OpenWorkGraph uses stdio for local MCP clients so no extra localhost MCP port is needed.</p><div class="modal-actions"><button onclick="closeModal();connectCursor()">Add to Cursor</button><button class="secondary" onclick="openConnect('claude')">Claude Desktop</button><button class="secondary" onclick="openConnect('chatgpt')">ChatGPT live</button><button class="secondary" onclick="openConnect('other')">Other MCP</button></div>`);return;
    }
    if(kind==='claude'){
      try{
        await ensureAiAccess();
        const cfg=await claudeConfig();
        openModal('Connect Claude Desktop','Local stdio MCP',`<p>OpenWorkGraph uses Claude's local stdio MCP path. No MCP network port or bearer token is required.</p><ol class="steps"><li>Open Claude Desktop → <strong>Settings → Developer / local MCP settings</strong>.</li><li>Merge the <code>openworkgraph</code> entry below into <code>mcpServers</code>.</li><li>Restart Claude Desktop and approve OpenWorkGraph if prompted.</li></ol><div class="codebox">${esc(cfg)}</div><div class="modal-actions"><button id="copyClaudeCfg">Copy Claude config</button><button class="secondary" onclick="openClaudeApp()">Open Claude Desktop</button><a class="btn ghost" target="_blank" rel="noreferrer" href="https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop">Official Claude guide</a></div><div class="note" style="margin-top:12px">AI access is enabled for this OpenWorkGraph run. Turn it off at any time from the dashboard; configured clients will then be denied until you re-enable it.</div>`);
        document.querySelector('#copyClaudeCfg').onclick=function(){copyText(cfg,this)};
        refreshAiPanel();
      }catch(e){openModal('Connection unavailable','Local security',`<p>${esc(e.message||'Could not create Claude configuration.')}</p>`);} return;
    }
    if(kind==='chatgpt'){
      try{
        await ensureAiAccess();
        const c=await httpMcp('start');
        if(!c.running)throw new Error(c.error||'Could not start the local HTTP MCP bridge.');
        openModal('Connect ChatGPT live','On-demand HTTP MCP',`<p>ChatGPT cannot directly reach a local stdio process, so OpenWorkGraph started an authenticated HTTP MCP endpoint <strong>only for this live connection</strong>. Use OpenAI's Secure MCP Tunnel/custom app flow; do not expose the port publicly.</p><h3>Endpoint</h3><div class="codebox">${esc(c.endpoint)}</div><h3>Authorization</h3><div class="codebox">Bearer ${esc(c.token)}</div><div class="modal-actions"><button id="copyChatEndpoint">Copy endpoint</button><button class="secondary" id="copyChatAuth">Copy authorization</button><a class="btn secondary" target="_blank" rel="noreferrer" href="https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt">Open ChatGPT setup guide</a></div><div class="note" style="margin-top:12px">This bearer is temporary and dies when the HTTP MCP bridge stops or OpenWorkGraph exits. Every tool call also checks the dashboard's AI access switch.</div>`);
        document.querySelector('#copyChatEndpoint').onclick=function(){copyText(c.endpoint,this)};
        document.querySelector('#copyChatAuth').onclick=function(){copyText(`Bearer ${c.token}`,this)};
        refreshAiPanel();
      }catch(e){openModal('Connection unavailable','Local security',`<p>${esc(e.message||'Could not start ChatGPT live access.')}</p>`);} return;
    }
    if(kind==='other'){
      try{
        await ensureAiAccess();
        const c=await connectionConfig();
        const cfg=JSON.stringify(stdioObject(c),null,2);
        openModal('Other MCP client','Local stdio preferred',`<p>For clients that can launch a local MCP process, use stdio. This avoids a standing MCP network listener.</p><div class="codebox">${esc(cfg)}</div><div class="modal-actions"><button id="copyOtherCfg">Copy stdio config</button><button class="secondary" id="startHttpBtn">Start HTTP MCP instead</button></div><div class="note">HTTP MCP is intended only for clients that cannot use stdio. Its endpoint and bearer are temporary and allocated only when you start it.</div>`);
        document.querySelector('#copyOtherCfg').onclick=function(){copyText(cfg,this)};
        document.querySelector('#startHttpBtn').onclick=async function(){const h=await httpMcp('start');const txt=JSON.stringify({url:h.endpoint,headers:{Authorization:`Bearer ${h.token}`}},null,2);copyText(txt,this);this.textContent='HTTP config copied';refreshAiPanel();};
        refreshAiPanel();
      }catch(e){openModal('Connection unavailable','Local security',`<p>${esc(e.message||'Could not create connection details.')}</p>`);} return;
    }
    return originalOpenConnect(kind);
  };

  function activityHtml(items){
    if(!items||!items.length)return '<span class="muted">No MCP reads this run.</span>';
    return items.slice(0,6).map(x=>{const t=(x.observed_at||'').replace('T',' ').slice(11,19);const range=(x.range_start||x.range_end)?` · ${esc((x.range_start||'').slice(11,16))}–${esc((x.range_end||'').slice(11,16))}`:'';return `<div style="padding:5px 0;border-bottom:1px solid #eceee8"><strong>${esc(t)}</strong> · ${esc(x.tool)} · ${Number(x.rows||0)} rows · ${Math.round(Number(x.bytes||0)/1024)} KB${range}${x.status==='denied'?' · <span class="off">denied</span>':''}</div>`}).join('');
  }

  async function refreshAiPanel(){
    try{
      const [access,activity,http]=await Promise.all([jsonCall('/v1/ai-access'),jsonCall('/v1/mcp-activity?limit=20'),jsonCall('/v1/mcp-http')]);
      const btn=document.querySelector('#aiAccessToggle');
      const state=document.querySelector('#aiAccessState');
      const log=document.querySelector('#mcpActivityLog');
      if(btn){btn.textContent=access.enabled?'Turn AI access off':'Enable AI access';btn.className=access.enabled?'secondary':'green';}
      if(state)state.innerHTML=access.enabled?'<span class="ok">ON for this run</span>':'<span class="off">OFF</span> — MCP calls are denied';
      if(log)log.innerHTML=activityHtml(activity.items||[]);
      const box=document.querySelector('.statusbox.mcp');
      if(box)box.innerHTML=`<span class="mcpdot"></span><strong>Local MCP:</strong> stdio available${http.running?` · HTTP <code>${esc(http.endpoint)}</code>`:' · HTTP off'}<div class="muted" style="margin-top:3px">Local clients use stdio. HTTP starts only on demand.</div>`;
      const stop=document.querySelector('#stopHttpMcp');if(stop)stop.style.display=http.running?'inline-flex':'none';
    }catch(_){}
  }
  window.refreshAiPanel=refreshAiPanel;

  document.addEventListener('DOMContentLoaded',()=>{
    const connect=document.querySelector('.connect-grid');
    if(connect && !document.querySelector('#aiAccessPanel')){
      const panel=document.createElement('div');
      panel.id='aiAccessPanel';panel.className='card';panel.style.marginTop='12px';
      panel.innerHTML=`<div style="display:flex;gap:14px;justify-content:space-between;align-items:flex-start;flex-wrap:wrap"><div><h2 style="margin-bottom:5px">AI access</h2><div id="aiAccessState" class="muted">Checking…</div><div class="muted" style="margin-top:4px">OFF by default on every launch. Each MCP tool call is checked live.</div></div><div style="display:flex;gap:8px"><button id="aiAccessToggle" class="green">Enable AI access</button><button id="stopHttpMcp" class="ghost" style="display:none">Stop HTTP MCP</button></div></div><div style="margin-top:13px"><strong style="font-size:12px">Recent AI activity</strong><div id="mcpActivityLog" class="muted" style="margin-top:5px">No MCP reads this run.</div></div>`;
      connect.parentNode.insertBefore(panel,connect.nextSibling);
      panel.querySelector('#aiAccessToggle').onclick=async()=>{const s=await jsonCall('/v1/ai-access');await setAiAccess(!s.enabled);refreshAiPanel();};
      panel.querySelector('#stopHttpMcp').onclick=async()=>{await httpMcp('stop');refreshAiPanel();};
    }
    const q=document.querySelector('.quickhelp');
    if(q && !document.querySelector('#browserPairButton')){
      const b=document.createElement('button');b.id='browserPairButton';b.className='ghost';b.textContent='Pair / repair browser sensor';b.onclick=window.showBrowserPairingCode;q.appendChild(b);
    }
    refreshAiPanel();setInterval(()=>{if(!document.hidden)refreshAiPanel()},5000);
  });
})();
</script>
"""


def _dashboard_html() -> str:
    html = DASHBOARD.read_text(encoding="utf-8")
    html = html.replace("</head>", _bootstrap_script() + "\n</head>")
    html = html.replace("</body>", _connection_override_script() + "\n</body>")
    return html


@app.middleware("http")
async def local_capability_guard(request: Request, call_next):
    method = request.method.upper()
    path = request.url.path
    origin = str(request.headers.get("origin") or "")

    # This check must run before any route handled directly by this middleware.
    if not _request_host_allowed(request):
        return _json_error("untrusted Host header", 400)

    # The shipped app does not need FastAPI's interactive schema/docs surface.
    if path in BLOCKED_DEV_PATHS or path.startswith("/docs"):
        return _json_error("not found", 404)

    if method == "GET" and path == "/":
        return HTMLResponse(_dashboard_html())
    if path in PUBLIC_PATHS:
        return await call_next(request)
    if method == "OPTIONS" and path in BROWSER_PATHS:
        return await call_next(request)
    if path in {"/v1/browser-challenge", "/v1/browser-pair"} and method == "OPTIONS":
        return _extension_cors(Response(status_code=204), origin)

    if path == "/v1/dashboard-session" and method == "POST":
        try:
            payload: dict[str, Any] = await request.json()
        except Exception:
            return _json_error("invalid dashboard bootstrap request", 400)
        session = exchange_dashboard_bootstrap(str(payload.get("bootstrap") or ""))
        if not session:
            return _json_error("invalid or already-used dashboard bootstrap", 401)
        return JSONResponse({"status": "ok", "session": session, "expires_in_seconds": 12 * 60 * 60})

    if path == "/v1/browser-challenge" and method == "POST":
        try:
            payload = await request.json(); nonce = str(payload.get("nonce") or "")
        except Exception:
            nonce = ""
        if not nonce or len(nonce) > 200:
            return _extension_cors(_json_error("invalid challenge", 400), origin)
        return _extension_cors(JSONResponse({"proof": browser_server_proof(nonce)}), origin)

    if path == "/v1/browser-pair" and method == "POST":
        if not origin.startswith(EXTENSION_PREFIXES):
            return _json_error("browser extension origin required", 403)
        try:
            payload = await request.json(); code = str(payload.get("code") or "")
        except Exception:
            code = ""
        if not consume_pairing_code(code):
            return _extension_cors(_json_error("invalid or expired pairing code", 401), origin)
        return _extension_cors(JSONResponse({"secret": ensure_browser_secret()}), origin)

    if path == "/v1/export-ticket" and method == "POST":
        if not _api_authenticated(request):
            return _json_error("authentication required", 401)
        try:
            payload = await request.json()
        except Exception:
            return _json_error("invalid export ticket request", 400)
        export_format = str(payload.get("format") or "").lower()
        scope = str(payload.get("scope") or "current").lower()
        include_raw = bool(payload.get("include_raw", True))
        if export_format not in {"json", "xlsx", "csvzip"} or scope not in {"current", "all"}:
            return _json_error("invalid export format or scope", 400)
        issued = issue_export_ticket(export_format, scope, include_raw)
        raw = "true" if include_raw else "false"
        ticket = str(issued["ticket"])
        return JSONResponse({
            **issued,
            "url": f"/v1/export/{export_format}?scope={scope}&include_raw={raw}&ticket={ticket}",
        })

    if method == "GET" and path.startswith("/v1/export/") and request.query_params.get("ticket"):
        export_format = path.rsplit("/", 1)[-1].lower()
        scope = str(request.query_params.get("scope") or "current").lower()
        include_raw = str(request.query_params.get("include_raw") or "true").lower() == "true"
        if not consume_export_ticket(
            str(request.query_params.get("ticket") or ""), export_format, scope, include_raw
        ):
            return _json_error("invalid or expired export ticket", 401)
        return await call_next(request)

    if path == "/v1/browser-pairing-code" and method == "POST":
        if not _api_authenticated(request):
            return _json_error("authentication required", 401)
        return JSONResponse(new_pairing_code())

    if path == "/v1/mcp-connection-config" and method == "GET":
        if not _api_authenticated(request):
            return _json_error("authentication required", 401)
        root = str(DASHBOARD.parent.parent)
        env = {
            "PYTHONPATH": root,
            "WORKFLOW_OBSERVER_API": "http://127.0.0.1:8787",
            "WORKFLOW_OBSERVER_AUTH_DIR": os.getenv("WORKFLOW_OBSERVER_AUTH_DIR", os.path.join(root, "data", "auth")),
        }
        return JSONResponse({
            "transport": "stdio",
            "command": sys.executable,
            "args": ["-m", "mcp_server.secure_stdio"],
            "env": env,
            "security_note": local_security_note(),
        })

    if (method, path) in BROWSER_ROUTES:
        body = await request.body()
        if not verify_browser_authorization(request.headers.get("authorization"), method=method, path=path, body=body):
            return _extension_cors(_json_error("paired browser authentication required", 401), origin)
        return await call_next(request)

    if (method, path) in COLLECTOR_ROUTES:
        if not bearer_matches(request.headers.get("authorization")):
            return _json_error("collector authentication required", 401)
        return await call_next(request)

    if path.startswith("/v1/"):
        if not _api_authenticated(request):
            return _json_error("authentication required", 401)
        return await call_next(request)

    return await call_next(request)


@app.get("/v1/workflow-trace")
def get_workflow_trace(
    since: str | None = None,
    until: str | None = None,
    cursor: str | None = None,
    limit: int = 100,
    scope: str = "current",
    query: str | None = None,
    app_name: str | None = None,
    session_id: str | None = None,
):
    from .mcp_trace import workflow_trace
    from .privacy_pipeline import redact_for_display
    return redact_for_display(workflow_trace(since=since, until=until, cursor=cursor, limit=limit, scope=scope, query=query, app_name=app_name, session_id=session_id))


@app.get("/v1/ai-access")
def get_ai_access():
    from .ai_access import ai_access_enabled
    return {"enabled": ai_access_enabled(), "resets_on_restart": True}


@app.post("/v1/ai-access")
async def update_ai_access(request: Request):
    from .ai_access import set_ai_access
    try:
        payload = await request.json()
    except Exception:
        return _json_error("invalid AI access request", 400)
    return {"enabled": set_ai_access(bool(payload.get("enabled"))), "resets_on_restart": True}


@app.get("/v1/mcp-activity")
def get_mcp_activity(limit: int = 50):
    from .ai_access import recent_mcp_activity
    return {"items": recent_mcp_activity(limit)}


@app.post("/v1/mcp-activity")
async def add_mcp_activity(request: Request):
    from .ai_access import record_mcp_activity
    try:
        payload = await request.json()
    except Exception:
        return _json_error("invalid MCP activity entry", 400)
    return record_mcp_activity(payload if isinstance(payload, dict) else {})


@app.get("/v1/mcp-http")
def get_mcp_http_status():
    from .mcp_http_control import status
    return status()


@app.post("/v1/mcp-http")
async def change_mcp_http(request: Request):
    from .mcp_http_control import start_http_mcp, stop_http_mcp
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    action = str(payload.get("action") or "status").lower()
    if action == "start":
        return start_http_mcp()
    if action == "stop":
        return stop_http_mcp()
    if action == "status":
        from .mcp_http_control import status
        return status()
    return _json_error("action must be start, stop, or status", 400)


@app.on_event("shutdown")
def stop_optional_http_mcp() -> None:
    try:
        from .mcp_http_control import stop_http_mcp
        stop_http_mcp()
    except Exception:
        pass


__all__ = ["app"]
